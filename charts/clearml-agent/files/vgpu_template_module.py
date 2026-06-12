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
        return task_data.get("hyperparams") or {}
    return getattr(task_data, "hyperparams", None) or {}


def _param_value(section, name):
    if not isinstance(section, dict):
        return None
    entry = section.get(name)
    if entry is None:
        return None
    if isinstance(entry, dict):
        value = entry.get("value")
    else:
        value = entry
    if value is None or value == "":
        return None
    return value


def _warn_legacy_mib_value(param_key, value, factor):
    if param_key != "vgpu_memory" or factor <= 1:
        return
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return
    # Values like 2048/6144 on factor=1024 clusters are almost always old MiB configs.
    if numeric > 64:
        print(
            "[vgpu-hook] WARNING: vgpu_memory=%s looks like legacy MiB; "
            "this cluster uses gpu-memory-factor=%s (use GiB integers, e.g. 2 for 2GB)"
            % (numeric, factor)
        )


def extract_vgpu_params(task_data, section_name=None):
    section_name = section_name or vgpu_section_name()
    hyperparams = _task_hyperparams(task_data)
    section = hyperparams.get(section_name)
    if not section:
        return {}

    factor = DEFAULT_MEMORY_FACTOR
    params = {}
    for key in VGPU_LIMIT_KEYS:
        value = _param_value(section, key)
        if value is not None:
            _warn_legacy_mib_value(key, value, factor)
            try:
                numeric = int(float(value))
                if numeric < 1:
                    print("[vgpu-hook] ignore non-positive %s/%s=%r" % (section_name, key, value))
                    continue
                if key == "vgpu_cores" and not (0 < numeric <= 100):
                    print("[vgpu-hook] ignore out-of-range %s/%s=%r" % (section_name, key, value))
                    continue
                params[key] = numeric
            except (TypeError, ValueError):
                print("[vgpu-hook] ignore invalid %s/%s=%r" % (section_name, key, value))
    return params


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

    limits = containers[0].setdefault("resources", {}).setdefault("limits", {})
    for param_key, k8s_key in VGPU_LIMIT_KEYS.items():
        if param_key in params:
            limits[k8s_key] = str(params[param_key])

    metadata = template.setdefault("metadata", {})
    annotations = metadata.setdefault("annotations", {})
    annotations.setdefault("volcano.sh/vgpu-mode", "hami-core")
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
    """Used by the open-source PYTHONSTARTUP monkey-patch."""
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
