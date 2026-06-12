#!/usr/bin/env python3
"""Start k8s-glue in-process after installing the vGPU template hook.

Python 3 ignores PYTHONSTARTUP when running scripts, so the hook must load
in the same interpreter that executes k8s_glue_example.py.
"""
from __future__ import print_function

import os
import runpy
import sys

_HOOK_DIR = os.environ.get("CLEARML_VGPU_HOOK_DIR", "/root/vgpu")
if _HOOK_DIR not in sys.path:
    sys.path.insert(0, _HOOK_DIR)

_BOOTSTRAP = os.path.join(_HOOK_DIR, "bootstrap_k8s_vgpu_patch.py")
with open(_BOOTSTRAP) as bootstrap_file:
    exec(compile(bootstrap_file.read(), _BOOTSTRAP, "exec"), {"__name__": "__main__"})

queue = os.environ.get("K8S_GLUE_QUEUE", "k8s_glue")
extra_args = os.environ.get("K8S_GLUE_EXTRA_ARGS", "").split()
argv = ["k8s_glue_example.py", "--queue", queue] + extra_args

max_pods = os.environ.get("K8S_GLUE_MAX_PODS", "").strip()
if max_pods:
    argv.extend(["--max-pods", max_pods])

glue_script = os.environ.get("CLEARML_K8S_GLUE_SCRIPT", "/root/k8s_glue_example.py")
if not os.path.isfile(glue_script):
    raise SystemExit("[vgpu-hook] k8s glue script not found: %s" % glue_script)

sys.argv = argv
os.chdir(os.path.dirname(glue_script) or "/root")
print("[vgpu-hook] starting k8s glue:", " ".join(argv))
runpy.run_path(glue_script, run_name="__main__")
