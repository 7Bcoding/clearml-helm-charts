#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SDK 侧按任务指定 Volcano vGPU (gpu-memory-factor=1024, 单位 GiB).

前提: agentk8sglue.vgpuHook.enabled=true

    python test_vgpu_per_task.py --memory 2 --cores 30
    python test_vgpu_per_task.py --memory 6 --cores 50
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from clearml import Task

Task.add_requirements(str(Path(__file__).resolve().parent / "requirements-remote.txt"))

GPU_MEMORY_FACTOR = 1024


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--queue", default="volcano-queue")
    p.add_argument("--vgpu-number", type=int, default=1, help="volcano.sh/vgpu-number")
    p.add_argument(
        "--memory",
        type=int,
        default=2,
        help="volcano.sh/vgpu-memory in GiB (device-plugin gpu-memory-factor=1024)",
    )
    p.add_argument("--cores", type=int, default=30, help="volcano.sh/vgpu-cores, 0-100")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    task = Task.init(
        project_name="volcano-vgpu",
        task_name="per-task-vgpu-test",
        task_type=Task.TaskTypes.testing,
    )

    task.connect(
        {
            "vgpu_number": args.vgpu_number,
            "vgpu_memory": args.memory,
            "vgpu_cores": args.cores,
            "gpu_memory_factor": GPU_MEMORY_FACTOR,
        },
        name="VGPU",
    )

    task.execute_remotely(queue_name=args.queue, exit_process=True)

    logger = task.get_logger()
    expected_mib = args.memory * GPU_MEMORY_FACTOR
    logger.report_text(
        "requested VGPU: number=%s memory_gib=%s cores=%s factor=%s"
        % (args.vgpu_number, args.memory, args.cores, GPU_MEMORY_FACTOR)
    )

    print("=" * 60)
    print("nvidia-smi")
    print("=" * 60)
    r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, check=False)
    print(r.stdout or r.stderr)

    q = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        mem_mb = float(q.stdout.strip().split()[0])
        logger.report_scalar("gpu", "memory_total_mb", mem_mb, iteration=0)
        print("memory.total = %.0f MiB (expect ~ %s MiB = %s GiB)" % (mem_mb, expected_mib, args.memory))
        if abs(mem_mb - expected_mib) > expected_mib * 0.15:
            print("WARNING: memory limit differs from request; check gpu-memory-factor / VGPU section")
    except Exception as exc:
        print("parse failed:", exc)
        sys.exit(1)

    print("DONE")


if __name__ == "__main__":
    main()
