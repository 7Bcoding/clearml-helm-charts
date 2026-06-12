"""Install k8s-glue hooks for per-task Volcano vGPU (open-source agent).

Loaded by run_k8s_glue_with_vgpu_hook.py in the same Python process as k8s-glue.
(Python 3 PYTHONSTARTUP is interactive-only and does not run for scripts.)
"""
from __future__ import print_function

import os
import sys

_HOOK_DIR = os.environ.get("CLEARML_VGPU_HOOK_DIR", "/root/vgpu")
if _HOOK_DIR not in sys.path:
    sys.path.insert(0, _HOOK_DIR)


def _install_patch():
    try:
        from clearml_agent.glue import k8s as k8s_module
        from clearml_agent.glue.k8s import K8sIntegration
    except ImportError as exc:
        print("[vgpu-hook] import clearml_agent failed, patch skipped: %s" % exc)
        return

    if getattr(K8sIntegration, "_clearml_vgpu_hook_installed", False):
        return

    import vgpu_template_module

    if not hasattr(K8sIntegration, "_kubectl_apply_original"):
        K8sIntegration._kubectl_apply_original = K8sIntegration._kubectl_apply

    def _kubectl_apply_with_vgpu(self, *args, **kwargs):
        task_data = kwargs.get("task_data")
        task_id = kwargs.get("task_id")
        if task_data is None and len(args) >= 15:
            task_id = args[8]
            task_data = args[14]

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
            if params and isinstance(document, dict):
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
