"""Install k8s-glue hooks for per-task Volcano vGPU (open-source agent).

Loaded by run_k8s_glue_with_vgpu_hook.py in the same Python process as k8s-glue.
(Python 3 PYTHONSTARTUP is interactive-only and does not run for scripts.)

This monkeypatches the open-source agent's private ``K8sIntegration._kubectl_apply``
because that agent does not yet expose the official
``CLEARML_K8S_GLUE_TEMPLATE_MODULE`` hook. The patch is defensive: if the agent
internals change shape, it logs a clear WARNING and leaves the agent untouched so
the failure is visible instead of silently dropping vGPU limits.
"""
from __future__ import print_function

import inspect
import os
import sys

_HOOK_DIR = os.environ.get("CLEARML_VGPU_HOOK_DIR", "/root/vgpu")
if _HOOK_DIR not in sys.path:
    sys.path.insert(0, _HOOK_DIR)

# Config-only kinds the k8s glue may serialize alongside the task Pod; never
# inject vGPU container limits into these. Anything else (Pod, or a template
# with no explicit kind) is still handled by apply_vgpu_params.
_NON_POD_KINDS = frozenset((
    "ConfigMap",
    "Secret",
    "Service",
    "ServiceAccount",
    "PersistentVolumeClaim",
    "Namespace",
    "Role",
    "RoleBinding",
))


def _resolve_task_args(bound_args):
    """Pull task_id / task_data from the bound _kubectl_apply call by name.

    Using the inspected signature avoids brittle positional indexes: the agent
    calls _kubectl_apply with keyword arguments, and the parameter order has
    changed across versions.
    """
    arguments = bound_args.arguments
    return arguments.get("task_id"), arguments.get("task_data")


def _install_patch():
    try:
        from clearml_agent.glue import k8s as k8s_module
        from clearml_agent.glue.k8s import K8sIntegration
    except ImportError as exc:
        print("[vgpu-hook] WARNING: import clearml_agent failed, vGPU limits NOT applied: %s" % exc)
        return

    if getattr(K8sIntegration, "_clearml_vgpu_hook_installed", False):
        return

    original = getattr(K8sIntegration, "_kubectl_apply", None)
    if not callable(original):
        print(
            "[vgpu-hook] WARNING: K8sIntegration._kubectl_apply not found; "
            "agent internals changed, vGPU limits NOT applied"
        )
        return

    if not hasattr(k8s_module, "yaml") or not hasattr(k8s_module.yaml, "dump"):
        print(
            "[vgpu-hook] WARNING: k8s glue no longer serializes via yaml.dump; "
            "agent internals changed, vGPU limits NOT applied"
        )
        return

    try:
        signature = inspect.signature(original)
    except (TypeError, ValueError) as exc:
        print("[vgpu-hook] WARNING: cannot inspect _kubectl_apply signature: %s" % exc)
        signature = None

    import vgpu_template_module

    if not hasattr(K8sIntegration, "_kubectl_apply_original"):
        K8sIntegration._kubectl_apply_original = original

    def _kubectl_apply_with_vgpu(self, *args, **kwargs):
        task_id = kwargs.get("task_id")
        task_data = kwargs.get("task_data")
        if (task_id is None or task_data is None) and signature is not None:
            try:
                bound = signature.bind(self, *args, **kwargs)
                bound.apply_defaults()
                bound_task_id, bound_task_data = _resolve_task_args(bound)
                task_id = task_id if task_id is not None else bound_task_id
                task_data = task_data if task_data is not None else bound_task_data
            except TypeError as exc:
                print("[vgpu-hook] WARNING: failed to bind _kubectl_apply args: %s" % exc)

        params = vgpu_template_module.resolve_vgpu_params(
            task_data,
            task_id=task_id,
            session=getattr(self, "_session", None),
        )
        if not params:
            vgpu_template_module.log_missing_vgpu_params(task_data, task_id=task_id)
        else:
            print(
                "[vgpu-hook] task=%s will override %s"
                % (task_id, " ".join("%s=%s" % (k, v) for k, v in sorted(params.items())))
            )

        orig_dump = k8s_module.yaml.dump

        def dump_with_vgpu(document, stream, **dump_kwargs):
            # Only the task Pod carries the container we patch. If _kubectl_apply
            # ever serializes a config-only object (ConfigMap/Secret/...) in the
            # same call, skip it explicitly so we never mutate an unintended
            # document. Pods (kind="Pod") and templates without an explicit kind
            # still go through apply_vgpu_params, which self-protects when no
            # container path is present.
            if (
                params
                and isinstance(document, dict)
                and document.get("kind") not in _NON_POD_KINDS
            ):
                document = vgpu_template_module.apply_vgpu_params(document, params)
            return orig_dump(document, stream, **dump_kwargs)

        k8s_module.yaml.dump = dump_with_vgpu
        try:
            return K8sIntegration._kubectl_apply_original(self, *args, **kwargs)
        finally:
            k8s_module.yaml.dump = orig_dump

    K8sIntegration._kubectl_apply = _kubectl_apply_with_vgpu
    K8sIntegration._clearml_vgpu_hook_installed = True
    print("[vgpu-hook] K8sIntegration._kubectl_apply patched (dir=%s)" % _HOOK_DIR)


_install_patch()
