"""Per-task Volcano vGPU overrides for ClearML k8s glue (open-source).

Users declare resources in the SDK before execute_remotely():

    task.connect({"vgpu_number": 1, "vgpu_memory": 2, "vgpu_cores": 30}, name="VGPU")

``vgpu_memory`` is passed verbatim to ``volcano.sh/vgpu-memory``. Its physical meaning
depends on volcano-vgpu-device-plugin ``--gpu-memory-factor``:

    factor=1024  ->  SDK value 2 means 2 GiB (recommended on large GPUs)
    factor=1     ->  SDK value 2048 means 2048 MiB

Set agent env ``CLEARML_VGPU_MEMORY_FACTOR`` to match the device plugin (Helm value
``agentk8sglue.vgpuHook.gpuMemoryFactor``).
"""
from __future__ import print_function

import copy
import os

DEFAULT_VGPU_SECTION = "VGPU"
DEFAULT_MEMORY_FACTOR = int(os.environ.get("CLEARML_VGPU_MEMORY_FACTOR", "1024") or "1024")

VGPU_LIMIT_KEYS = {
    "vgpu_number": "volcano.sh/vgpu-number",
    "vgpu_memory": "volcano.sh/vgpu-memory",
    "vgpu_cores": "volcano.sh/vgpu-cores",
}


def vgpu_section_name():
    return os.environ.get("CLEARML_VGPU_SECTION", DEFAULT_VGPU_SECTION)


def _task_hyperparams(task_data):
    if isinstance(task_data, dict):
        hyperparams = task_data.get("hyperparams")
        if hyperparams:
            return hyperparams
        configuration = task_data.get("configuration") or {}
        if isinstance(configuration, dict) and configuration:
            return configuration
        return {}
    hyperparams = getattr(task_data, "hyperparams", None)
    if hyperparams:
        return hyperparams
    configuration = getattr(task_data, "configuration", None)
    if configuration:
        return configuration
    return {}


def _param_value(section, name):
    if not isinstance(section, dict):
        return None
    entry = section.get(name)
    if entry is None:
        return None
    if isinstance(entry, dict):
        value = entry.get("value")
        if value is None and len(entry) == 1:
            value = next(iter(entry.values()))
    else:
        value = entry
    if value is None or value == "":
        return None
    return value


def _section_with_vgpu_keys(hyperparams, preferred_section):
    if not isinstance(hyperparams, dict):
        return None, preferred_section
    section = hyperparams.get(preferred_section)
    if isinstance(section, dict) and any(_param_value(section, key) is not None for key in VGPU_LIMIT_KEYS):
        return section, preferred_section
    for name, candidate in hyperparams.items():
        if not isinstance(candidate, dict):
            continue
        if any(_param_value(candidate, key) is not None for key in VGPU_LIMIT_KEYS):
            return candidate, name
    return None, preferred_section


def _warn_legacy_mib_value(param_key, value, factor):
    if param_key != "vgpu_memory" or factor <= 1:
        return
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return
    if numeric > 64:
        print(
            "[vgpu-hook] WARNING: vgpu_memory=%s looks like legacy MiB; "
            "this cluster uses gpu-memory-factor=%s (use GiB integers, e.g. 2 for 2GB)"
            % (numeric, factor)
        )


def extract_vgpu_params(task_data, section_name=None):
    section_name = section_name or vgpu_section_name()
    hyperparams = _task_hyperparams(task_data)
    section, resolved_section = _section_with_vgpu_keys(hyperparams, section_name)
    if not section:
        return {}

    if resolved_section != section_name:
        print("[vgpu-hook] using hyperparam section %r (configured=%r)" % (resolved_section, section_name))

    factor = DEFAULT_MEMORY_FACTOR
    params = {}
    for key in VGPU_LIMIT_KEYS:
        value = _param_value(section, key)
        if value is not None:
            _warn_legacy_mib_value(key, value, factor)
            try:
                numeric = int(float(value))
                if numeric < 1:
                    print("[vgpu-hook] ignore non-positive %s/%s=%r" % (resolved_section, key, value))
                    continue
                if key == "vgpu_cores" and not (0 < numeric <= 100):
                    print("[vgpu-hook] ignore out-of-range %s/%s=%r" % (resolved_section, key, value))
                    continue
                params[key] = numeric
            except (TypeError, ValueError):
                print("[vgpu-hook] ignore invalid %s/%s=%r" % (resolved_section, key, value))
    return params


def fetch_task_data(session, task_id):
    if not session or not task_id:
        return {}
    try:
        data = session.get(service="tasks", action="get_by_id", task=task_id)
        if isinstance(data, dict):
            return data.get("task") or data
    except Exception as exc:
        print("[vgpu-hook] get_by_id failed task=%s: %s" % (task_id, exc))
    try:
        data = session.get(service="tasks", action="get_all", version="2.13", id=[task_id])
        tasks = (data or {}).get("tasks") or []
        if tasks:
            return tasks[0]
    except Exception as exc:
        print("[vgpu-hook] get_all failed task=%s: %s" % (task_id, exc))
    return {}


def resolve_vgpu_params(task_data, task_id=None, session=None):
    params = extract_vgpu_params(task_data)
    if params:
        return params
    if session and task_id:
        refreshed = fetch_task_data(session, task_id)
        if refreshed:
            params = extract_vgpu_params(refreshed)
            if params:
                print("[vgpu-hook] task=%s loaded VGPU params from API refresh" % task_id)
                return params
    return {}


def log_missing_vgpu_params(task_data, task_id=None):
    hyperparams = _task_hyperparams(task_data)
    section_name = vgpu_section_name()
    print(
        "[vgpu-hook] task=%s no VGPU override; section=%r hyperparam_sections=%s"
        % (task_id, section_name, sorted(hyperparams.keys()) if hyperparams else "[]")
    )
    section = hyperparams.get(section_name) if isinstance(hyperparams, dict) else None
    if section:
        print("[vgpu-hook] task=%s section keys: %s" % (task_id, sorted(section.keys())))


def _container_paths(template):
    spec = template.get("spec") or {}
    if spec.get("template"):
        return spec["template"].setdefault("spec", {}).setdefault("containers", [])
    return spec.setdefault("containers", [])


def apply_vgpu_params(template, params):
    if not params:
        return template

    template = copy.deepcopy(template)
    containers = _container_paths(template)
    if not containers:
        print("[vgpu-hook] template has no containers, skip vGPU override")
        return template

    resources = containers[0].setdefault("resources", {})
    limits = resources.setdefault("limits", {})
    requests = resources.setdefault("requests", {})
    applied = {}
    for param_key, k8s_key in VGPU_LIMIT_KEYS.items():
        if param_key in params:
            value = str(params[param_key])
            limits[k8s_key] = value
            requests[k8s_key] = value
            applied[k8s_key] = value

    metadata = template.setdefault("metadata", {})
    annotations = metadata.setdefault("annotations", {})
    annotations.setdefault("volcano.sh/vgpu-mode", "hami-core")
    print("[vgpu-hook] applied pod resources: %s" % applied)
    return template


def update_template(
    queue,
    task_data,
    task_dict,
    providers_info,
    template,
    task_config,
    worker,
    queue_name,
    *args,
    **kwargs
):
    """Enterprise-compatible entry point (CLEARML_K8S_GLUE_TEMPLATE_MODULE)."""
    data = task_dict if isinstance(task_dict, dict) else task_data
    task_id = data.get("id") if isinstance(data, dict) else getattr(data, "id", None)
    params = extract_vgpu_params(data)
    if params:
        print(
            "[vgpu-hook] task=%s queue=%s factor=%s override %s"
            % (
                task_id,
                queue_name,
                DEFAULT_MEMORY_FACTOR,
                " ".join("%s=%s" % (k, v) for k, v in sorted(params.items())),
            )
        )
    template = apply_vgpu_params(template, params)
    return {"template": template}


def patch_template_for_task(template, task_data, task_id=None, queue_name=None):
    params = extract_vgpu_params(task_data)
    if params:
        print(
            "[vgpu-hook] task=%s queue=%s factor=%s override %s"
            % (
                task_id,
                queue_name,
                DEFAULT_MEMORY_FACTOR,
                " ".join("%s=%s" % (k, v) for k, v in sorted(params.items())),
            )
        )
    return apply_vgpu_params(template, params)
