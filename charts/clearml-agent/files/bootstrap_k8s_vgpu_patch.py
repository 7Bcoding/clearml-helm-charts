"""Install K8sIntegration._resolve_template patch for per-task vGPU (open-source agent)."""
from __future__ import print_function

import os
import sys

_HOOK_DIR = os.environ.get("CLEARML_VGPU_HOOK_DIR", "/root/vgpu")
if _HOOK_DIR not in sys.path:
    sys.path.insert(0, _HOOK_DIR)


def _install_patch():
    try:
        from clearml_agent.glue.k8s import K8sIntegration
    except ImportError:
        return

    if getattr(K8sIntegration, "_clearml_vgpu_hook_installed", False):
        return

    import vgpu_template_module

    original = K8sIntegration._resolve_template

    def _resolve_template_with_vgpu(self, task_session, task_data, queue, task_id):
        template = original(self, task_session, task_data, queue, task_id)
        if not template:
            return template
        try:
            return vgpu_template_module.patch_template_for_task(
                template,
                task_data,
                task_id=task_id,
                queue_name=queue,
            )
        except Exception as ex:
            print("[vgpu-hook] failed to patch template for task %s: %s" % (task_id, ex))
            return template

    K8sIntegration._resolve_template = _resolve_template_with_vgpu
    K8sIntegration._clearml_vgpu_hook_installed = True
    print("[vgpu-hook] K8sIntegration._resolve_template patched (dir=%s)" % _HOOK_DIR)


_install_patch()
