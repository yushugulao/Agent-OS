#!/usr/bin/env python3
"""加载仓库唯一的资源感知 worker 策略。"""

from __future__ import annotations

import runpy
from pathlib import Path


MAX_BUILD_JOBS = 24


def adaptive_build_jobs(repository_root: Path) -> int:
    """从 ``scripts/resource-jobs.py`` 返回有界构建预算。"""

    root = repository_root.resolve(strict=True)
    candidates = (
        root / "scripts" / "resource-jobs.py",
        root.parent / "scripts" / "resource-jobs.py",
    )
    policies = [
        path for path in candidates if path.is_file() and not path.is_symlink()
    ]
    if len(policies) != 1:
        raise ValueError("resource job policy is unavailable or link-backed")
    policy = policies[0]
    namespace = runpy.run_path(str(policy))
    choose = namespace.get("choose_jobs")
    available_memory = namespace.get("available_memory")
    if not callable(choose) or not callable(available_memory):
        raise ValueError("resource job policy does not expose its build interface")
    jobs = choose("build", memory=available_memory())
    if type(jobs) is not int or not 1 <= jobs <= MAX_BUILD_JOBS:
        raise ValueError("resource job policy returned an invalid build budget")
    return jobs
