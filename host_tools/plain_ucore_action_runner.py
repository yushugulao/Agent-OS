#!/usr/bin/env python3
"""根据 Host 动作记录准备并可选运行 plain uCore 工作。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Iterable

if __package__:
    from .evidence_delivery_contract import (
        DeliveryContractError,
        controlled_git_environment,
        tracked_worktree_identity,
    )
    from .plain_ucore_fs_extract import extract_state_files
    from .research_state_manifest import (
        GUEST_STATE_RECEIPT_SCHEMA,
        guest_state_inventory_sha256,
        load_manifest,
    )
    from .resource_job_budget import adaptive_build_jobs
    from .safe_host_paths import (
        absolute_lexical_path as _absolute_lexical_path,
        create_private_directory,
        ensure_safe_directory,
        path_is_link as _is_link_component,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
        require_private_directory,
    )
else:
    from evidence_delivery_contract import (
        DeliveryContractError,
        controlled_git_environment,
        tracked_worktree_identity,
    )
    from plain_ucore_fs_extract import extract_state_files
    from research_state_manifest import (
        GUEST_STATE_RECEIPT_SCHEMA,
        guest_state_inventory_sha256,
        load_manifest,
    )
    from resource_job_budget import adaptive_build_jobs
    from safe_host_paths import (
        absolute_lexical_path as _absolute_lexical_path,
        create_private_directory,
        ensure_safe_directory,
        path_is_link as _is_link_component,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
        require_private_directory,
    )

UCORE_FS_BLOCK_SIZE = 1024
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SEEDED_ACTION_PHASES = ("clean", "build", "guest")
SEEDED_ACTION_PHASE_TIMEOUT_MARGIN_SECONDS = 30
SEEDED_ACTION_OBSERVER_CLEANUP_ALLOWANCE_SECONDS = 10
SEEDED_ACTION_MAX_TIMEOUT_SECONDS = 3600
WSL_COMMAND_ID_ENV = "AGENTOS_UCORE_RUN_ID"
WSL_COMMAND_ID_RE = re.compile(r"\bAGENTOS_UCORE_RUN_ID=([0-9a-f]{32})\b")
WSL_TIMEOUT_KILL_AFTER_SECONDS = 5
# GNU timeout 必须在 Host 侧截止时间前完成 KILL 升级，以便新的 WSL 客户端
# 仍有足够时间独立扫描 /proc。
WSL_HOST_DEADLINE_MARGIN_SECONDS = 10
WSL_QUIESCENCE_VERIFY_TIMEOUT_SECONDS = 8
WSL_CLEANUP_UNVERIFIED_EXIT_CODE = 125
CONTROLLED_SHELL_PATH = (
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
CONTROLLED_SHELL_VARIABLES = (
    "HOME",
    "LANG",
    "LC_ALL",
    "MAKE_TOOL",
    "PATH",
    "QEMU",
    "SHELL",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TOOLPREFIX",
    "TZ",
)
SOURCE_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
# Native MSYS2 Git can take well over five seconds to enumerate a large dirty
# worktree on a Windows volume. This remains a fail-closed per-command bound,
# but leaves enough room to capture the exact tracked diff before a Guest run.
SOURCE_IDENTITY_GIT_TIMEOUT_SECONDS = 300
GUEST_FAILURE_RULES = (
    (
        "kernel_panic",
        re.compile(
            r"^\[PANIC (?:(?:-?\d+--?\d+\]\s+\S+:\d+:)|"
            r"(?:-?\d+\]\[\S+:\d+\]:))\s+.+$",
            re.IGNORECASE,
        ),
    ),
    (
        "kernel_fault",
        re.compile(
            r"^\[ERROR -?\d+--?\d+\](?:"
            r"unknown syscall\s+-?\d+"
            r"|-?\d+ in application, bad addr = .+?, bad instruction = .+?, "
            r"core dumped\."
            r"|IllegalInstruction in application, core dumped\."
            r"|unknown trap:.+"
            r"|invalid trap from kernel:.+"
            r")$",
            re.IGNORECASE,
        ),
    ),
    (
        "guest_check_failed",
        re.compile(r"^[A-Za-z0-9_.-]+:\s+check failed(?:\s|:|$).*$", re.IGNORECASE),
    ),
    (
        "orchestrator_failed",
        re.compile(
            r"^rp_(?:seed_|agentos_)?orch:\s+(?:child_failed|failed)(?:\s|:|$).*$",
            re.IGNORECASE,
        ),
    ),
)
DEFAULT_FAILURE_PATTERN = "|".join(f"(?:{rule.pattern})" for _, rule in GUEST_FAILURE_RULES)
HOST_STATE_FILES = frozenset(
    load_manifest(Path(__file__).resolve().parents[1]).host_state_files
)


class RepoRunBusy(RuntimeError):
    """已有破坏性 plain-uCore 运行正在使用该仓库。"""


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """通过目标目录中的 ``O_EXCL`` 临时文件发布字节。"""

    target = _absolute_lexical_path(path)
    parent = reject_link_components(target.parent)
    if not parent.is_dir():
        raise ValueError(f"Atomic write parent is unavailable: {parent}")
    try:
        target_info = target.lstat()
    except FileNotFoundError:
        target_info = None
    if target_info is not None and (
        _is_link_component(target, target_info.st_mode)
        or not stat.S_ISREG(target_info.st_mode)
    ):
        raise ValueError(f"Atomic write destination is unsafe: {target}")

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        else:
            os.chmod(temporary, mode)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # 发生竞争的符号链接只作为目录项替换，绝不跟随。
        os.replace(temporary, target)
        if os.name != "nt" and hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary.is_file() and not _is_link_component(temporary):
            temporary.unlink()


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def archive_runtime_artifacts(repo_dir: Path, run_dir: Path) -> dict[str, object]:
    artifact_dir = create_private_directory(run_dir / "artifacts")
    sources = {
        "kernel": repo_dir / "build" / "kernel",
        "image_input": repo_dir / "nfs" / "fs.img",
        "image_final": repo_dir / "nfs" / "fs-copy.img",
    }
    archived: dict[str, object] = {}
    for name, source in sources.items():
        try:
            source = require_regular_file(source, nonempty=True)
        except (OSError, ValueError) as error:
            raise ValueError(f"runtime artifact is unavailable or link-backed: {source}")
        destination = artifact_dir / name
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".tmp", dir=str(artifact_dir)
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        digest = hashlib.sha256()
        size = 0
        with destination.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        if size <= 0:
            raise ValueError(f"runtime artifact is empty: {source}")
        archived[name] = {
            "path": f"artifacts/{name}",
            "bytes": size,
            "sha256": digest.hexdigest(),
        }
    return archived


def capture_source_identity(repo_dir: Path) -> dict[str, object]:
    """捕获 HEAD、已跟踪文件洁净状态及精确状态摘要。"""

    repo_dir = require_safe_directory(_absolute_lexical_path(repo_dir)).resolve(strict=True)
    git_name = shutil.which("git")
    if git_name is None:
        raise ValueError("source identity probe requires Git")
    git = require_regular_file(Path(git_name)).resolve(strict=True)
    common_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "strict",
        "timeout": SOURCE_IDENTITY_GIT_TIMEOUT_SECONDS,
        "check": False,
        "env": controlled_git_environment(),
    }
    try:
        top_level = subprocess.run(
            [str(git), "-c", "core.fsmonitor=false", "-c",
             "core.untrackedCache=false", "-C", str(repo_dir), "rev-parse",
             "--show-toplevel"],
            **common_kwargs,
        )
        if top_level.returncode != 0:
            diagnostic = (top_level.stderr or "").strip()
            raise ValueError(
                f"source repository root is unavailable: {diagnostic}"
            )
        source_root = _normalize_git_toplevel(repo_dir, top_level.stdout)
        head = subprocess.run(
            [str(git), "-c", "core.fsmonitor=false", "-c",
             "core.untrackedCache=false", "-C", str(source_root), "rev-parse",
             "--verify", "HEAD^{commit}"],
            **common_kwargs,
        )
        status = subprocess.run(
            [
                str(git),
                "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                "-C",
                str(source_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=no",
                "--ignore-submodules=none",
            ],
            **common_kwargs,
        )
        tracked_diff = subprocess.run(
            [
                str(git),
                "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                "-C",
                str(source_root),
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
            ],
            **common_kwargs,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ValueError(f"source identity probe failed: {error}") from error
    commit = head.stdout.strip()
    if head.returncode != 0 or SOURCE_COMMIT_RE.fullmatch(commit) is None:
        diagnostic = (head.stderr or "").strip()
        raise ValueError(f"source HEAD is unavailable: {diagnostic or 'invalid commit'}")
    if status.returncode != 0:
        diagnostic = (status.stderr or "").strip()
        raise ValueError(f"tracked source status is unavailable: {diagnostic}")
    if tracked_diff.returncode != 0:
        diagnostic = (tracked_diff.stderr or "").strip()
        raise ValueError(f"tracked source diff is unavailable: {diagnostic}")
    try:
        tracked_clean, exact_tracked_digest = tracked_worktree_identity(
            str(git), source_root
        )
    except DeliveryContractError as error:
        raise ValueError(f"tracked source identity is unsafe: {error}") from error
    tracked_fingerprint = hashlib.sha256(
        (
            status.stdout
            + "\0"
            + tracked_diff.stdout
            + "\0"
            + exact_tracked_digest
        ).encode("utf-8")
    ).hexdigest()
    return {
        "source_commit": commit,
        "source_tree_clean": status.stdout == "" and tracked_clean,
        "source_tracked_sha256": tracked_fingerprint,
    }


def _normalize_git_toplevel(repo_dir: Path, reported: str) -> Path:
    """在当前 Host 命名空间返回 Git 无链接工作树根目录。"""

    lines = reported.splitlines()
    if len(lines) != 1 or not lines[0] or "\0" in lines[0]:
        raise ValueError("Git repository root is malformed")
    value = lines[0]
    candidate = Path(value)
    if not candidate.is_absolute() and PureWindowsPath(value).is_absolute():
        converted = subprocess.run(
            ["cygpath", "-a", "-u", value],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5,
            check=False,
            env=controlled_git_environment(),
        )
        converted_lines = converted.stdout.splitlines()
        if (
            converted.returncode != 0
            or len(converted_lines) != 1
            or not converted_lines[0]
        ):
            raise ValueError("Git repository root cannot be converted")
        candidate = Path(converted_lines[0])
    if not candidate.is_absolute():
        raise ValueError("Git repository root is not absolute")
    source_root = require_safe_directory(
        _absolute_lexical_path(candidate)
    ).resolve(strict=True)
    try:
        repo_dir.relative_to(source_root)
    except ValueError as error:
        raise ValueError("source directory is outside its Git worktree") from error
    return source_root


def verify_source_identity(
    repo_dir: Path, expected: dict[str, object]
) -> dict[str, object]:
    """持有仓库锁重新探测，并拒绝运行中源码变更。"""

    current = capture_source_identity(repo_dir)
    if current != expected:
        raise ValueError("source identity changed while the runtime artifacts were built")
    return current


def _normalize_git_common_dir(repo_dir: Path, reported: str) -> Path:
    """将 Git common-dir 结果转换到当前 Host 命名空间。"""

    if not reported or "\0" in reported or "\r" in reported or "\n" in reported:
        raise ValueError("Git common directory is malformed")
    candidate = Path(reported)
    if candidate.is_absolute():
        return candidate
    if PureWindowsPath(reported).is_absolute():
    # Windows Git 创建的工作树保存绝对 Windows gitdir。原生 MSYS2 Git 会保留
    # 该拼写，即使 Python 在 POSIX 命名空间运行。必须将其作为数据经 cygpath
    # 转换，绝不能把盘符路径拼接到工作树下。
        converted = subprocess.run(
            ["cygpath", "-u", reported],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5,
            check=False,
        )
        lines = converted.stdout.splitlines()
        if converted.returncode != 0 or len(lines) != 1 or not lines[0]:
            raise ValueError("Windows Git common directory cannot be converted")
        candidate = Path(lines[0])
        if not candidate.is_absolute():
            raise ValueError("converted Git common directory is not absolute")
        return candidate
    return repo_dir / candidate


def _run_lock_path(repo_dir: Path) -> Path:
    repo_dir = require_safe_directory(_absolute_lexical_path(repo_dir)).resolve(strict=True)
    lock_dir: Path | None = None
    try:
        git_name = shutil.which("git")
        if git_name is None:
            raise ValueError("repository lock requires Git")
        git = require_regular_file(Path(git_name)).resolve(strict=True)
        result = subprocess.run(
            [str(git), "-C", str(repo_dir), "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5,
            check=False,
            env=controlled_git_environment(),
        )
        lines = result.stdout.splitlines()
        if result.returncode == 0 and len(lines) == 1 and lines[0]:
            candidate = _normalize_git_common_dir(repo_dir, lines[0])
            candidate = require_safe_directory(candidate).resolve(strict=True)
            lock_dir = candidate
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        pass
    if lock_dir is None:
        marker = repo_dir / ".git"
        try:
            lock_dir = require_safe_directory(marker).resolve(strict=True)
        except (OSError, ValueError):
            lock_dir = None
    if lock_dir is None:
        identity = hashlib.sha256(str(repo_dir).encode("utf-8")).hexdigest()[:16]
        lock_dir = Path(tempfile.gettempdir()) / "agentos-repo-locks" / identity
    return lock_dir / f".{repo_dir.name}-run.lock"


def _try_lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_repo_run_lock(repo_dir: Path):
    """按失败关闭，避免并发清理、构建与运行阶段冲突。"""

    lock_path = _run_lock_path(repo_dir)
    ensure_safe_directory(lock_path.parent)
    if _is_link_component(lock_path):
        raise ValueError(f"repository run lock is link-backed: {lock_path}")
    with lock_path.open("a+b") as handle:
        try:
            _try_lock_file(handle)
        except OSError as error:
            raise RepoRunBusy(
                f"plain uCore repository is already running: {repo_dir.resolve()}"
            ) from error
        try:
            yield
        finally:
            _unlock_file(handle)


def normalize_guest_log_line(line: str) -> str:
    return ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")


def classify_guest_failure(line: str) -> str:
    normalized = normalize_guest_log_line(line)
    for reason, rule in GUEST_FAILURE_RULES:
        if rule.fullmatch(normalized):
            return reason
    return ""


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            records.append(data)
    return records


def action_kind(path: str) -> str:
    if path.endswith("/research/studio-launch"):
        return "studio_launch"
    if path.endswith("/research/library-source"):
        return "library_source"
    if path.endswith("/research/inspect-workspace"):
        return "workspace_inspect"
    if path.endswith("/research/import-workspace"):
        return "workspace_import"
    if path.endswith("/research/import-and-run"):
        return "workspace_import_run"
    if path.endswith("/research/literature-search"):
        return "literature_search"
    if path.endswith("/research/evidence-review"):
        return "evidence_review"
    if path.endswith("/research/evidence-protocol"):
        return "evidence_protocol"
    if path.endswith("/research/llm-relay-request"):
        return "llm_relay_request"
    if path.endswith("/research/llm-relay-response"):
        return "llm_relay_response"
    if path.endswith("/research/llm-relay-fallback"):
        return "llm_relay_fallback"
    if path.endswith("/research/artifact-input"):
        return "artifact_input"
    if path.endswith("/research/artifact-derive"):
        return "artifact_derive"
    if path.endswith("/research/artifact-log"):
        return "artifact_log"
    if path.endswith("/research/artifact-chart"):
        return "artifact_chart"
    if path.endswith("/research/artifact-package"):
        return "artifact_package"
    if path.endswith("/research/run"):
        return "research_run"
    if path.endswith("/research/rerun"):
        return "research_rerun"
    if path.endswith("/research/review"):
        return "human_review"
    if path.endswith("/research/delivery"):
        return "delivery"
    if path.endswith("/research/revision-task"):
        return "revision_task"
    if path.endswith("/research/run-revision") or path.endswith("/research/run-revision-task"):
        return "revision_run"
    if path.endswith("/research/export"):
        return "research_export"
    if path.endswith("/research/template"):
        return "template"
    if path.endswith("/research/dataset"):
        return "dataset"
    if path.endswith("/research/dataset-preview"):
        return "dataset_preview"
    if path.endswith("/research/dataset-visualization"):
        return "dataset_visualization"
    if path.endswith("/research/dataset-card"):
        return "dataset_card"
    if path.endswith("/research/dataset-answer"):
        return "dataset_answer"
    if path.endswith("/research/dataset-run"):
        return "dataset_run"
    if path.endswith("/research/dataset-run-comparison"):
        return "dataset_run_comparison"
    if path.endswith("/research/dataset-portfolio"):
        return "dataset_portfolio"
    if path.endswith("/research/workbench"):
        return "workbench"
    if path.endswith("/research/workbench-advance"):
        return "workbench_advance"
    if path.endswith("/research/workbench-auto-advance"):
        return "workbench_auto_advance"
    if path.endswith("/research/workbench-task"):
        return "workbench_task"
    if path.endswith("/research/workbench-note"):
        return "workbench_note"
    if path.endswith("/research/workbench-notes"):
        return "workbench_notes"
    if path.endswith("/research/workbench-handoff-package"):
        return "workbench_handoff_package"
    if path.endswith("/research/workbench-readiness"):
        return "workbench_readiness"
    if path.endswith("/research/workbench-answer"):
        return "workbench_answer"
    if path.endswith("/research/workbench-answer-audit"):
        return "workbench_answer_audit"
    if path.endswith("/research/workbench-evidence-search"):
        return "workbench_evidence_search"
    if path.endswith("/research/workbench-brief"):
        return "workbench_brief"
    if path.endswith("/research/workbench-evidence-dossier"):
        return "workbench_evidence_dossier"
    if path.endswith("/research/workbench-evidence-graph"):
        return "workbench_evidence_graph"
    if path.endswith("/research/workbench-citations"):
        return "workbench_citations"
    if path.endswith("/research/workbench-manuscript"):
        return "workbench_manuscript"
    if path.endswith("/research/workbench-manuscript-audit"):
        return "workbench_manuscript_audit"
    if path.endswith("/research/workbench-manuscript-revision-plan"):
        return "workbench_manuscript_revision_plan"
    if path.endswith("/research/workbench-manuscript-revision-task"):
        return "workbench_manuscript_revision_task"
    if path.endswith("/research/workbench-task-board"):
        return "workbench_task_board"
    if path.endswith("/research/workbench-task-board-row"):
        return "workbench_task_board_row"
    if path.endswith("/research/workbench-plan-queue-row"):
        return "workbench_plan_queue_row"
    if path.endswith("/research/workbench-plan-queue-execute"):
        return "workbench_plan_queue_execute"
    if path.endswith("/research/workbench-runbook"):
        return "workbench_runbook"
    if path.endswith("/research/workbench-timeline"):
        return "workbench_timeline"
    if path.endswith("/research/workbench-file-manifest"):
        return "workbench_file_manifest"
    if path.endswith("/research/workbench-file-verify"):
        return "workbench_file_verify"
    if path.endswith("/research/workbench-complete"):
        return "workbench_complete"
    if path.endswith("/research/workbench-quality-gate"):
        return "workbench_quality_gate"
    if path.endswith("/research/workbench-quality-repair-plan"):
        return "workbench_quality_repair_plan"
    if path.endswith("/research/workbench-quality-repair-execute"):
        return "workbench_quality_repair_execute"
    if path.endswith("/research/workbench-action-item"):
        return "workbench_action_item"
    if path.endswith("/research/workbench-delivery-dashboard"):
        return "workbench_delivery_dashboard"
    if path.endswith("/research/workbench-delivery-execute-next"):
        return "workbench_delivery_execute_next"
    if path.endswith("/research/operations-report"):
        return "operations_report"
    if path.endswith("/research/operations-advance-next"):
        return "operations_advance_next"
    if path.endswith("/research/operations-execute-next-plan"):
        return "operations_execute_next_plan"
    if path.endswith("/research/project-scaffold"):
        return "project_scaffold"
    if path.endswith("/research/project-launch"):
        return "project_launch"
    if path.endswith("/research/project-action-execute"):
        return "project_action_execute"
    if path.endswith("/research/sample-workbench"):
        return "sample_workbench"
    if path.endswith("/research/study-protocol"):
        return "study_protocol"
    if path.endswith("/research/run-study-protocol"):
        return "study_protocol_run"
    if path.endswith("/research/study-protocol-compliance"):
        return "study_protocol_compliance"
    if path.endswith("/research/study-protocol-bundle"):
        return "study_protocol_bundle"
    if path.endswith("/research/study-protocol-launch"):
        return "study_protocol_launch"
    if path.endswith("/research/study-protocol-launch-rerun"):
        return "study_protocol_launch_rerun"
    if path.endswith("/research/study-protocol-launch-comparison"):
        return "study_protocol_launch_comparison"
    if path.endswith("/research/study-protocol-reproduction-package"):
        return "study_protocol_reproduction_package"
    if path.endswith("/research/study-protocol-reproduction-package-review"):
        return "study_protocol_reproduction_package_review"
    if path.endswith("/research/study-protocol-reproduction-package-action-plan"):
        return "study_protocol_reproduction_package_action_plan"
    if path.endswith("/research/study-protocol-reproduction-package-action-execute"):
        return "study_protocol_reproduction_package_action_execute"
    if path.endswith("/research/source-portfolio"):
        return "source_portfolio"
    if path.endswith("/research/project-space"):
        return "project_space"
    if path.endswith("/research/project-space-note"):
        return "project_space_note"
    if path.endswith("/research/project-space-action-item"):
        return "project_space_action_item"
    if path.endswith("/research/project-space-review"):
        return "project_space_review"
    if path.endswith("/research/project-space-answer"):
        return "project_space_answer"
    if path.endswith("/research/project-space-repair-execute"):
        return "project_space_repair_execute"
    if path.endswith("/research/project-space-task-board-row"):
        return "project_space_task_board_row"
    if path.endswith("/research/project-handoff-audit"):
        return "project_handoff_audit"
    if path.endswith("/research/project-release-gate"):
        return "project_release_gate"
    if path.endswith("/research/project-snapshot"):
        return "project_snapshot"
    if path.endswith("/research/project-snapshot-comparison"):
        return "project_snapshot_comparison"
    if path.endswith("/research/project-reproducibility-audit"):
        return "project_reproducibility_audit"
    if path.endswith("/research/project-provenance-graph"):
        return "project_provenance_graph"
    if path.endswith("/research/project-delivery"):
        return "project_delivery"
    if path.endswith("/research/package-intake"):
        return "package_intake"
    if path.endswith("/research-search/save"):
        return "research_search_save"
    if path.endswith("/research-search/export"):
        return "research_search_export"
    if path.endswith("/research-search/note"):
        return "research_search_note"
    if path.endswith("/research-search/action-item"):
        return "research_search_action_item"
    if path.endswith("/research/export-workbench"):
        return "workbench_export"
    if path.endswith("/research/export-notebook"):
        return "notebook_export"
    if path.endswith("/research/export-bundle"):
        return "bundle_export"
    if path.endswith("/agentcompare/run"):
        return "agentcompare"
    if path.endswith("/host-workflow/run"):
        return "host_workflow"
    if path.endswith("/host-workflow/export"):
        return "host_workflow_export"
    if path.endswith("/host-workflow/stage-attempt"):
        return "host_workflow_stage"
    if path.endswith("/host-workflow/cache-decision"):
        return "host_workflow_cache"
    if path.endswith("/host-workflow/retry-decision"):
        return "host_workflow_retry"
    if path.endswith("/host-workflow/artifact-manifest"):
        return "host_workflow_artifact"
    if path.endswith("/host-workflow/report-export"):
        return "host_workflow_report"
    if path.endswith("/workflow-portability/import"):
        return "workflow_portability_import"
    if path.endswith("/workflow-portability/plan"):
        return "workflow_portability_plan"
    if path.endswith("/workflow-portability/bind"):
        return "workflow_portability_bind"
    if path.endswith("/workflow-portability/rehearse"):
        return "workflow_portability_rehearse"
    if path.endswith("/workflow-portability/review"):
        return "workflow_portability_review"
    if path.endswith("/workflow-portability/package"):
        return "workflow_portability_package"
    if path.endswith("/workflow-portability/run"):
        return "workflow_portability"
    return "generic"


def clean_copy_state(src: Path, dst: Path) -> None:
    try:
        src = require_safe_directory(src)
    except (OSError, ValueError) as error:
        raise ValueError("Guest state source is missing or unsafe")
    dst = reject_link_components(dst)
    if dst.exists():
        try:
            require_safe_directory(dst)
        except (OSError, ValueError) as error:
            raise ValueError("Guest state destination is unsafe")
        shutil.rmtree(dst)
    dst = ensure_safe_directory(dst)
    for item in sorted(src.iterdir()):
        if not item.name.startswith("rp_"):
            continue
        try:
            item = require_regular_file(item)
        except (OSError, ValueError) as error:
            raise ValueError(f"Guest state source is unsafe: {item.name}")
        shutil.copy2(item, dst / item.name)


def replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def line_value(value: object) -> str:
    text = str(value)
    return text.replace("\n", " ").replace(";", ",").strip()


def action_line(record: dict[str, object]) -> str:
    sequence = line_value(record.get("sequence", ""))
    path = line_value(record.get("path", ""))
    status = line_value(record.get("status", "accepted"))
    kind = action_kind(path)
    payload = record.get("payload", {})
    fields = [f"action={sequence}", f"path={path}", f"kind={kind}", f"status={status}"]
    if isinstance(payload, dict):
        for key in sorted(payload):
            fields.append(f"{line_value(key)}={line_value(payload[key])}")
    return ";".join(fields)


def action_plan_line(record: dict[str, object]) -> str:
    path = str(record.get("path", ""))
    kind = action_kind(path)
    sequence = line_value(record.get("sequence", ""))
    if kind == "research_run":
        return f"plan={sequence};kind=research_run;prepare=rp_input;execute=rp_orch;collect=rp_web_bundle;status=ready"
    if kind == "research_rerun":
        return f"plan={sequence};kind=research_rerun;prepare=rp_input;execute=rp_orch;collect=rp_runner;status=ready"
    if kind == "studio_launch":
        return f"plan={sequence};kind=studio_launch;prepare=rp_input;execute=rp_orch;collect=rp_studio;status=ready"
    if kind == "agentcompare":
        return f"plan={sequence};kind=agentcompare;prepare=rp_agentcmp;execute=rp_orch;collect=rp_compare_plain;status=ready"
    if kind in {
        "host_workflow",
        "host_workflow_stage",
        "host_workflow_cache",
        "host_workflow_retry",
        "host_workflow_artifact",
        "host_workflow_report",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_stage_dag;execute=rp_orch;collect=rp_artifact_manifest;status=ready"
    if kind in {
        "artifact_input",
        "artifact_derive",
        "artifact_log",
        "artifact_chart",
        "artifact_package",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_artifact;execute=rp_artifact_ops;collect=rp_artifact_manifest;status=ready"
    if kind in {
        "workflow_portability",
        "workflow_portability_import",
        "workflow_portability_plan",
        "workflow_portability_bind",
        "workflow_portability_rehearse",
        "workflow_portability_review",
        "workflow_portability_package",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_wfio;execute=rp_orch;collect=rp_compare_plain;status=ready"
    if kind in {
        "project_scaffold",
        "project_launch",
        "project_action_execute",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_usableproj;execute=rp_orch;collect=rp_usablelaunch;status=ready"
    if kind in {
        "dataset_preview",
        "dataset_visualization",
        "dataset_card",
        "dataset_answer",
        "dataset_run",
        "dataset_run_comparison",
        "dataset_portfolio",
        "source_portfolio",
        "sample_workbench",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_usable;execute=rp_orch;collect=rp_usableds;status=ready"
    if kind.startswith("study_protocol"):
        return f"plan={sequence};kind={kind};prepare=rp_studyproto;execute=rp_orch;collect=rp_usablepack;status=ready"
    return f"plan={sequence};kind={kind};prepare=rp_host_action_queue;execute=rp_orch;collect=rp_web_bundle;status=ready"


def write_text(path: Path, text: str) -> None:
    atomic_write_text(path, text)


def write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_fs_aligned_seed(path: Path, seed_text: str) -> None:
    data = seed_text.encode("utf-8")
    remainder = len(data) % UCORE_FS_BLOCK_SIZE
    if remainder:
        data += b"\0" * (UCORE_FS_BLOCK_SIZE - remainder)
    atomic_write_bytes(path, data)


def pad_file_for_ucore_fs(path: Path) -> None:
    path = require_regular_file(path)
    data = path.read_bytes()
    remainder = len(data) % UCORE_FS_BLOCK_SIZE
    if remainder:
        atomic_write_bytes(path, data + b"\0" * (UCORE_FS_BLOCK_SIZE - remainder))


def pad_state_files_for_ucore_fs(state_dir: Path) -> None:
    state_dir = require_safe_directory(state_dir)
    for item in sorted(state_dir.iterdir()):
        if item.name.startswith("rp_"):
            require_regular_file(item)
            pad_file_for_ucore_fs(item)


def prepare_action_state(actions: list[dict[str, object]], state_dir: Path, run_dir: Path) -> dict[str, object]:
    # Host 动作运行名是确定的，因此占用目录必须失败，不能复用动作获准前预置的路径。
    run_dir = create_private_directory(run_dir)
    next_state = run_dir / "state-next"
    clean_copy_state(state_dir, next_state)

    queue_lines = [action_line(action) for action in actions]
    plan_lines = [action_plan_line(action) for action in actions]
    kinds = [action_kind(str(action.get("path", ""))) for action in actions]
    accepted = sum(1 for action in actions if action.get("status", "accepted") == "accepted")

    write_text(next_state / "rp_host_action_queue", "\n".join(queue_lines + ["status=ready"]) + "\n")
    write_text(next_state / "rp_host_action_plan", "\n".join(plan_lines + ["status=ready"]) + "\n")
    write_text(next_state / "rp_host_action_inbox", "\n".join(queue_lines) + ("\n" if queue_lines else ""))
    write_json(run_dir / "actions.json", actions)

    summary = {
        "actions": len(actions),
        "accepted": accepted,
        "kinds": sorted(set(kinds)),
        "state_dir": str(state_dir),
        "next_state_dir": str(next_state),
        "status": "ready",
    }
    write_json(run_dir / "runner-summary.json", summary)
    return summary


def windows_path_to_wsl(path: Path) -> str:
    text = str(reject_link_components(path).resolve(strict=False))
    if len(text) >= 3 and text[1:3] == ":\\":
        drive = text[0].lower()
        rest = text[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return text.replace("\\", "/")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def make_var_arg(name: str, value: str) -> str:
    return f"{name}={shell_quote(value)}"


def toolprefix_arg() -> str:
    return make_var_arg("TOOLPREFIX", os.environ.get("TOOLPREFIX", "riscv64-linux-gnu-"))


def bash_path(path: Path, base: Path | None = None) -> str:
    resolved = reject_link_components(path).resolve(strict=False)
    if base is not None:
        try:
            rel = resolved.relative_to(
                reject_link_components(base).resolve(strict=False)
            )
            return "./" + rel.as_posix()
        except ValueError:
            pass
    return windows_path_to_wsl(resolved)


def wsl_command_identity(command: list[str]) -> str:
    """返回 ``make_wsl_command`` 嵌入的不可猜测身份。"""

    matches: list[str] = []
    for argument in command:
        matches.extend(WSL_COMMAND_ID_RE.findall(argument))
    return matches[0] if len(matches) == 1 else ""


def _wsl_quiescence_script(command_identity: str) -> str:
    needle = f"{WSL_COMMAND_ID_ENV}={command_identity}"
    return f"""set -u
needle={shell_quote(needle)}
process_has_token() {{
    local entry
    while IFS= read -r -d '' entry; do
        if [ "$entry" = "$needle" ]; then
            return 0
        fi
    done < "$1"
    return 1
}}
token_pids() {{
    local env_file pid
    for env_file in /proc/[0-9]*/environ; do
        [ -r "$env_file" ] || continue
        if process_has_token "$env_file" 2>/dev/null; then
            pid=${{env_file#/proc/}}
            pid=${{pid%/environ}}
            case "$pid" in (*[!0-9]*|'') continue;; esac
            printf '%s\\n' "$pid"
        fi
    done
}}
count_pids() {{
    set -- $1
    printf '%s' "$#"
}}
signal_tagged() {{
    local sig pid env_file
    sig="$1"
    for pid in $(token_pids); do
        env_file="/proc/$pid/environ"
        if [ -r "$env_file" ] && process_has_token "$env_file" 2>/dev/null; then
            kill "-$sig" "$pid" 2>/dev/null || true
        fi
    done
}}
initial="$(token_pids)"
initial_count="$(count_pids "$initial")"
if [ -n "$initial" ]; then
    signal_tagged TERM
    for _attempt in 1 2 3 4 5 6 7 8 9 10; do
        [ -z "$(token_pids)" ] && break
        sleep 0.1
    done
fi
if [ -n "$(token_pids)" ]; then
    signal_tagged KILL
    for _attempt in 1 2 3 4 5 6 7 8 9 10; do
        [ -z "$(token_pids)" ] && break
        sleep 0.1
    done
fi
remaining="$(token_pids)"
quiet_scans=0
# Require two consecutive quiet scans so a just-forked tagged child cannot
# escape a single /proc glob expansion.
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    current="$(token_pids)"
    if [ -z "$current" ]; then
        quiet_scans=$((quiet_scans + 1))
        if [ "$quiet_scans" -ge 2 ]; then
            remaining=""
            break
        fi
    else
        quiet_scans=0
        remaining="$current"
        signal_tagged KILL
    fi
    sleep 0.1
done
remaining_count="$(count_pids "$remaining")"
printf 'AGENTOS_WSL_CLEANUP initial=%s remaining=%s\\n' \
    "$initial_count" "$remaining_count"
[ "$remaining_count" -eq 0 ]
"""


def _controlled_shell_environment() -> dict[str, str]:
    """构建 seeded 场景 shell 唯一可见的环境。"""

    posix_temporary = os.environ.get("AGENTOS_WSL_TMPDIR", "/tmp")
    native_temporary = (
        os.environ.get("TEMP", os.environ.get("TMP", posix_temporary))
        if sys.platform == "cygwin"
        else posix_temporary
    )
    native_system_drive = (
        os.environ.get("SYSTEMDRIVE", "") if sys.platform == "cygwin" else "/"
    )
    environment = {
        "HOME": os.environ.get("AGENTOS_WSL_HOME", "/tmp"),
        "LANG": os.environ.get("AGENTOS_WSL_LANG", "C"),
        "LC_ALL": os.environ.get("AGENTOS_WSL_LC_ALL", "C"),
        "MAKE_TOOL": os.environ.get("AGENTOS_WSL_MAKE", "make"),
        "PATH": os.environ.get("AGENTOS_WSL_PATH", CONTROLLED_SHELL_PATH),
        "QEMU": os.environ.get("QEMU", "qemu-system-riscv64"),
        "SHELL": os.environ.get("AGENTOS_WSL_BASH", "bash"),
        "SYSTEMDRIVE": os.environ.get(
            "AGENTOS_WINDOWS_SYSTEM_DRIVE", native_system_drive
        ),
        "TEMP": native_temporary,
        "TMP": native_temporary,
        "TMPDIR": posix_temporary,
        "TOOLPREFIX": os.environ.get("TOOLPREFIX", "riscv64-linux-gnu-"),
        "TZ": os.environ.get("AGENTOS_WSL_TZ", "UTC"),
    }
    if tuple(environment) != CONTROLLED_SHELL_VARIABLES:
        raise ValueError("controlled shell environment order changed")
    for name, value in environment.items():
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError(f"controlled shell environment is invalid: {name}")
    if not environment["HOME"].startswith("/"):
        raise ValueError("controlled shell HOME must be an absolute POSIX path")
    if not environment["TMPDIR"].startswith("/"):
        raise ValueError("controlled shell TMPDIR must be an absolute POSIX path")
    if not environment["TEMP"] or not environment["TMP"]:
        raise ValueError("controlled shell native temporary directory is unavailable")
    if sys.platform == "cygwin" and not (
        environment["TEMP"].startswith("/")
        or PureWindowsPath(environment["TEMP"]).is_absolute()
    ):
        raise ValueError("MSYS2 TEMP must be absolute in one active namespace")
    if environment["TEMP"] != environment["TMP"]:
        raise ValueError("controlled shell TEMP and TMP must identify one directory")
    if sys.platform == "cygwin":
        if re.fullmatch(r"[A-Z]:", environment["SYSTEMDRIVE"]) is None:
            raise ValueError("MSYS2 SYSTEMDRIVE must be a canonical drive identity")
    elif environment["SYSTEMDRIVE"] != "/":
        raise ValueError("POSIX controlled shell SYSTEMDRIVE must use the neutral identity")
    if not environment["PATH"] or any(
        not component.startswith("/")
        for component in environment["PATH"].split(":")
    ):
        raise ValueError("controlled shell PATH must contain absolute POSIX paths")
    return environment


def _controlled_shell_command(script: str) -> list[str]:
    environment = _controlled_shell_environment()
    env_executable = os.environ.get("AGENTOS_WSL_ENV", "env")
    bash_executable = environment["SHELL"]
    if any(character in env_executable for character in "\x00\r\n"):
        raise ValueError("controlled shell env executable is invalid")
    return [
        env_executable,
        "-i",
        *(f"{name}={environment[name]}" for name in CONTROLLED_SHELL_VARIABLES),
        bash_executable,
        "--noprofile",
        "--norc",
        "-c",
        script,
    ]


def _wsl_verification_command(command: list[str], script: str) -> list[str]:
    if command and command[0].lower().endswith("wsl.exe"):
        try:
            separator = command.index("--")
        except ValueError as error:
            raise ValueError("tagged WSL command has no argument separator") from error
        prefix = command[: separator + 1]
        controlled = command[separator + 1 :]
    else:
        prefix = []
        controlled = command
    if not controlled or controlled != _controlled_shell_command(controlled[-1]):
        raise ValueError("tagged WSL command is not a controlled non-login shell")
    return [*prefix, *_controlled_shell_command(script)]


def verify_wsl_command_quiesced(command: list[str]) -> dict[str, object]:
    """回收并独立证明该命令的 WSL 后代已全部退出。"""

    identity = wsl_command_identity(command)
    if not identity:
        malformed_tag = any(
            f"{WSL_COMMAND_ID_ENV}=" in argument for argument in command
        )
        if malformed_tag:
            return {
                "applicable": True,
                "verified": False,
                "initial_survivors": None,
                "remaining_survivors": None,
                "error": "malformed or ambiguous WSL command identity",
            }
        return {
            "applicable": False,
            "verified": True,
            "initial_survivors": 0,
            "remaining_survivors": 0,
            "error": "",
        }
    try:
        verification_command = _wsl_verification_command(
            command, _wsl_quiescence_script(identity)
        )
        result = subprocess.run(
            verification_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WSL_QUIESCENCE_VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {
            "applicable": True,
            "verified": False,
            "initial_survivors": None,
            "remaining_survivors": None,
            "error": f"{type(error).__name__}: {error}",
        }
    receipt = re.search(
        r"^AGENTOS_WSL_CLEANUP initial=(\d+) remaining=(\d+)$",
        result.stdout or "",
        re.MULTILINE,
    )
    initial = int(receipt.group(1)) if receipt else None
    remaining = int(receipt.group(2)) if receipt else None
    verified = result.returncode == 0 and remaining == 0
    diagnostic = ""
    if not verified:
        diagnostic = (result.stderr or result.stdout or "invalid cleanup receipt").strip()
    return {
        "applicable": True,
        "verified": verified,
        "initial_survivors": initial,
        "remaining_survivors": remaining,
        "error": diagnostic,
    }


def _wsl_cleanup_log_line(cleanup: dict[str, object]) -> str:
    if not cleanup.get("applicable"):
        return ""
    return (
        "[runner] wsl cleanup verified={verified} initial={initial} remaining={remaining}"
        "{error}\n"
    ).format(
        verified=1 if cleanup.get("verified") else 0,
        initial=cleanup.get("initial_survivors"),
        remaining=cleanup.get("remaining_survivors"),
        error=(f" error={cleanup.get('error')}" if cleanup.get("error") else ""),
    )


def run_command(command: list[str], log_path: Path, timeout_seconds: int, append: bool = False) -> int:
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **popen_kwargs)
    timed_out = False
    host_process_quiesced = True
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        partial_stdout = error.stdout if isinstance(error.stdout, str) else ""
        partial_stderr = error.stderr if isinstance(error.stderr, str) else ""
        termination = terminate_process(proc)
        host_process_quiesced = bool(termination["host_process_quiesced"])
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stdout, stderr = partial_stdout, partial_stderr
            host_process_quiesced = False
        stdout = stdout or partial_stdout
        stderr = stderr or partial_stderr
    cleanup = verify_wsl_command_quiesced(command)
    text = (stdout or "") + (stderr or "") + _wsl_cleanup_log_line(cleanup)
    if (
        not timed_out
        and cleanup.get("applicable")
        and proc.returncode == 124
    ):
        text += "[runner] inner WSL deadline expired\n"
    if timed_out:
        text += f"\n[runner] exceeded {timeout_seconds}s\n"
    if not host_process_quiesced:
        text += "[runner] Host launcher process did not quiesce\n"
    if append:
        ensure_safe_directory(log_path.parent)
        if _is_link_component(log_path):
            raise ValueError(f"command log is link-backed: {log_path}")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        write_text(log_path, text)
    if not cleanup.get("verified") or not host_process_quiesced:
        return WSL_CLEANUP_UNVERIFIED_EXIT_CODE
    return 124 if timed_out else int(proc.returncode)


def seeded_ucore_deadline_contract(timeout_seconds: int) -> dict[str, object]:
    if (
        type(timeout_seconds) is not int
        or timeout_seconds <= 0
        or timeout_seconds > SEEDED_ACTION_MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "seeded uCore timeout must be an integer between 1 and "
            f"{SEEDED_ACTION_MAX_TIMEOUT_SECONDS} seconds"
        )
    phase_timeout = timeout_seconds + SEEDED_ACTION_PHASE_TIMEOUT_MARGIN_SECONDS
    return {
        "contract": "plain_ucore_action_deadline_v1",
        "contract_version": 1,
        "runner_timeout_seconds": timeout_seconds,
        "phases": list(SEEDED_ACTION_PHASES),
        "phase_timeout_seconds": phase_timeout,
        "observer_cleanup_allowance_seconds": (
            SEEDED_ACTION_OBSERVER_CLEANUP_ALLOWANCE_SECONDS
        ),
        "server_deadline_seconds": (
            len(SEEDED_ACTION_PHASES) * phase_timeout
            + SEEDED_ACTION_OBSERVER_CLEANUP_ALLOWANCE_SECONDS
        ),
    }


def process_group_alive(proc: subprocess.Popen[str]) -> bool:
    if os.name != "posix":
        return proc.poll() is None
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def signal_process_tree(
    proc: subprocess.Popen[str], sig: signal.Signals
) -> tuple[bool, bool]:
    """向所属 leader 和进程组发送信号，并报告已确认的 leader 投递。"""

    leader_sent = False
    group_sent = False
    if proc.poll() is None:
        try:
            if os.name == "nt":
                if sig == signal.SIGTERM:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    tree = subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=False,
                    )
                    if tree.returncode != 0 and proc.poll() is None:
                        proc.kill()
            else:
                os.kill(proc.pid, sig)
            leader_sent = True
        except (OSError, ProcessLookupError):
            pass
    if os.name == "posix":
        try:
            os.killpg(proc.pid, sig)
            group_sent = True
        except (OSError, ProcessLookupError):
            pass
    return leader_sent or group_sent, leader_sent


def terminate_process(proc: subprocess.Popen[str]) -> dict[str, object]:
    """停止所属进程树，且不将清理误判为自然退出。"""

    signals_sent: list[int] = []
    term_sent, term_sent_to_leader = signal_process_tree(proc, signal.SIGTERM)
    if term_sent:
        signals_sent.append(int(signal.SIGTERM))

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        leader_alive = proc.poll() is None
        if not leader_alive and not process_group_alive(proc):
            break
        time.sleep(0.05)

    if proc.poll() is None or process_group_alive(proc):
        kill_sent, _ = signal_process_tree(proc, signal.SIGKILL)
        if kill_sent:
            signals_sent.append(int(signal.SIGKILL))
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        kill_sent, _ = signal_process_tree(proc, signal.SIGKILL)
        if kill_sent:
            signals_sent.append(int(signal.SIGKILL))
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    raw_returncode = proc.returncode
    host_process_quiesced = proc.poll() is not None and not process_group_alive(proc)
    if os.name == "nt":
        term_leader_confirmed = term_sent_to_leader
    else:
        # 成功的 kill(2) 可能与自然退出的僵尸进程竞争；wait 状态用于证明实际由
        # TERM 而非自然的非零退出结束 leader。
        term_leader_confirmed = (
            term_sent_to_leader and raw_returncode == -int(signal.SIGTERM)
        )
    return {
        "signals_sent": tuple(signals_sent),
        "term_leader_confirmed": term_leader_confirmed,
        "raw_returncode": raw_returncode,
        "host_process_quiesced": host_process_quiesced,
    }


def run_observed_command(
    command: list[str],
    log_path: Path,
    timeout_seconds: int,
    append: bool = False,
    pass_marker: str = "",
    failure_pattern: str = DEFAULT_FAILURE_PATTERN,
    idle_notice_seconds: int = 20,
    marker_grace_seconds: int = 2,
) -> dict[str, object]:
    ensure_safe_directory(log_path.parent)
    if _is_link_component(log_path):
        raise ValueError(f"command log is link-backed: {log_path}")
    mode = "a" if append else "w"
    output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["preexec_fn"] = os.setsid
    proc = subprocess.Popen(command, **popen_kwargs)
    assert proc.stdout is not None

    def read_output() -> None:
        try:
            for line in proc.stdout:
                output_queue.put(("line", line))
        except BaseException as error:
            output_queue.put(
                ("error", f"{type(error).__name__}: {error}")
            )
        else:
            output_queue.put(("eof", ""))

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    start = time.monotonic()
    last_output = start
    last_notice = start
    marker_seen_at = 0.0
    marker_seen = False
    failure_seen = False
    failure_line = ""
    failure_reason = ""
    timed_out = False
    idle_notices = 0
    termination = {
        "signals_sent": (),
        "term_leader_confirmed": False,
        "raw_returncode": None,
        "host_process_quiesced": True,
    }
    cleanup: dict[str, object] = {
        "applicable": False,
        "verified": True,
        "initial_survivors": 0,
        "remaining_survivors": 0,
        "error": "",
    }
    output_eof = False
    output_error = ""
    last_lines: list[str] = []
    failure_re = re.compile(failure_pattern, re.IGNORECASE) if failure_pattern else None
    normalized_marker = normalize_guest_log_line(pass_marker)

    with log_path.open(mode, encoding="utf-8", errors="replace") as handle:
        def consume_output(item: str) -> None:
            nonlocal last_output, failure_seen, failure_line, failure_reason
            nonlocal marker_seen, marker_seen_at, last_lines

            last_output = time.monotonic()
            handle.write(item)
            handle.flush()
            line = item.rstrip("\n")
            last_lines.append(line)
            if len(last_lines) > 80:
                last_lines = last_lines[-80:]
            normalized_line = normalize_guest_log_line(item)
            if (
                not failure_seen
                and failure_re
                and failure_re.fullmatch(normalized_line)
            ):
                failure_seen = True
                failure_line = normalized_line
                failure_reason = (
                    classify_guest_failure(normalized_line)
                    or "custom_failure_pattern"
                )
                handle.write(
                    f"[runner] guest failure detected: {failure_reason}\n"
                )
                handle.flush()
            if normalized_marker and normalized_line == normalized_marker and not marker_seen:
                marker_seen = True
                marker_seen_at = time.monotonic()
                handle.write("[runner] pass marker observed\n")
                handle.flush()

        def consume_output_event(event: tuple[str, str]) -> None:
            nonlocal output_eof, output_error

            event_kind, payload = event
            if event_kind == "line":
                consume_output(payload)
            elif event_kind == "eof":
                output_eof = True
            elif event_kind == "error":
                if not output_error:
                    output_error = payload or "unknown output reader error"
                    handle.write(f"[runner] output reader failed: {output_error}\n")
                    handle.flush()
            elif not output_error:
                output_error = f"unknown output event: {event_kind}"
                handle.write(f"[runner] output reader failed: {output_error}\n")
                handle.flush()

        while True:
            now = time.monotonic()
            if now - start > timeout_seconds:
                timed_out = True
                handle.write(f"\n[runner] exceeded {timeout_seconds}s\n")
                break
            if now - last_output >= idle_notice_seconds and now - last_notice >= idle_notice_seconds:
                idle_notices += 1
                notice = f"[runner] no output for {int(now - last_output)}s\n"
                handle.write(notice)
                handle.flush()
                print(notice.rstrip())
                last_notice = now
            if marker_seen and marker_seen_at > 0 and now - marker_seen_at >= marker_grace_seconds:
                if proc.poll() is None:
                    handle.write(f"[runner] pass marker observed; stopping process after {marker_grace_seconds}s grace\n")
                    handle.flush()
                    break
            try:
                event = output_queue.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None and output_eof:
                    break
                continue
            consume_output_event(event)
            if output_eof and proc.poll() is not None:
                break
            if failure_seen or output_error:
                break

        natural_completion = proc.poll() is not None and output_eof
        if not natural_completion:
            termination = terminate_process(proc)
        cleanup = verify_wsl_command_quiesced(command)
        cleanup_log_line = _wsl_cleanup_log_line(cleanup)
        if cleanup_log_line:
            handle.write(cleanup_log_line)
            handle.flush()

        drain_deadline = time.monotonic() + 2
        while not output_eof and time.monotonic() < drain_deadline:
            try:
                event = output_queue.get(timeout=0.1)
            except queue.Empty:
                if not reader.is_alive():
                    break
                continue
            consume_output_event(event)
        reader.join(timeout=max(0.0, drain_deadline - time.monotonic()))
        while True:
            try:
                event = output_queue.get_nowait()
            except queue.Empty:
                break
            consume_output_event(event)
        if (
            not timed_out
            and cleanup.get("applicable")
            and proc.returncode == 124
        ):
            timed_out = True
            handle.write("[runner] inner WSL deadline expired\n")
        if (
            timed_out
            or failure_seen
            or output_error
            or (normalized_marker and not marker_seen)
        ):
            handle.write("[runner] last log lines:\n")
            for line in last_lines[-40:]:
                handle.write(line + "\n")

    elapsed = time.monotonic() - start
    raw_returncode = proc.returncode
    signals_sent = tuple(int(item) for item in termination["signals_sent"])
    runner_terminated = (
        marker_seen
        and not failure_seen
        and not output_error
        and not timed_out
        and signals_sent == (int(signal.SIGTERM),)
        and bool(termination["term_leader_confirmed"])
        and bool(termination["host_process_quiesced"])
        and output_eof
    )
    natural_exit_ok = raw_returncode == 0 and not signals_sent
    exit_ok = natural_exit_ok or runner_terminated
    ok = (
        not timed_out
        and not failure_seen
        and not output_error
        and bool(termination["host_process_quiesced"])
        and bool(cleanup.get("verified"))
        and (not normalized_marker or marker_seen)
        and output_eof
        and exit_ok
    )
    if not ok and not failure_reason:
        if timed_out:
            failure_reason = "guest_timeout"
        elif not termination["host_process_quiesced"]:
            failure_reason = "host_launcher_cleanup_unverified"
        elif not cleanup.get("verified"):
            failure_reason = "wsl_cleanup_unverified"
        elif output_error:
            failure_reason = "guest_output_error"
        elif raw_returncode not in (0, None):
            failure_reason = "guest_exit_nonzero"
        elif normalized_marker and not marker_seen:
            failure_reason = "pass_marker_missing"
        elif not output_eof:
            failure_reason = "guest_output_incomplete"
        elif signals_sent:
            failure_reason = "runner_termination_unconfirmed"
        else:
            failure_reason = "guest_failed"
    returncode = raw_returncode
    if ok and runner_terminated and returncode not in (0, None):
        returncode = 0
    elif not ok and returncode in (0, None):
        returncode = 1
    return {
        "returncode": returncode,
        "marker_seen": marker_seen,
        "failure_seen": failure_seen,
        "failure_line": failure_line,
        "failure_reason": failure_reason,
        "timed_out": timed_out,
        "runner_terminated": runner_terminated,
        "runner_signals": signals_sent,
        "raw_returncode": raw_returncode,
        "host_process_quiesced": bool(termination["host_process_quiesced"]),
        "output_eof": output_eof,
        "output_error": output_error,
        "wsl_cleanup_applicable": bool(cleanup.get("applicable")),
        "wsl_cleanup_verified": bool(cleanup.get("verified")),
        "wsl_cleanup_initial_survivors": cleanup.get("initial_survivors"),
        "wsl_cleanup_remaining_survivors": cleanup.get("remaining_survivors"),
        "wsl_cleanup_error": str(cleanup.get("error") or ""),
        "idle_notices": idle_notices,
        "elapsed_seconds": round(elapsed, 3),
    }


def make_wsl_command(
    command_text: str,
    wsl_distro: str,
    timeout_seconds: int | None = None,
    command_identity: str | None = None,
) -> list[str]:
    identity = command_identity or secrets.token_hex(16)
    if re.fullmatch(r"[0-9a-f]{32}", identity) is None:
        raise ValueError("WSL command identity must be 128-bit lowercase hex")
    environment = _controlled_shell_environment()
    env_executable = os.environ.get("AGENTOS_WSL_ENV", "env")
    bash_executable = environment["SHELL"]
    timeout_executable = os.environ.get("AGENTOS_WSL_TIMEOUT", "timeout")
    launcher_executable = os.environ.get(
        "AGENTOS_WSL_LAUNCHER", "wsl.exe" if os.name == "nt" else env_executable
    )
    if any(
        character in executable
        for executable in (env_executable, timeout_executable, launcher_executable)
        for character in "\x00\r\n"
    ):
        raise ValueError("controlled shell executable is invalid")
    clean_assignments = " ".join(
        shell_quote(f"{name}={environment[name]}")
        for name in CONTROLLED_SHELL_VARIABLES
    )
    clean_exec = f"{shell_quote(env_executable)} -i {clean_assignments}"
    bash_command = shell_quote(bash_executable)
    timeout_command = shell_quote(timeout_executable)
    if timeout_seconds is not None:
        reserved = (
            WSL_TIMEOUT_KILL_AFTER_SECONDS + WSL_HOST_DEADLINE_MARGIN_SECONDS
        )
        if timeout_seconds <= reserved:
            raise ValueError(
                "WSL command timeout must exceed the inner escalation and "
                "Host verification margin"
            )
        inner_timeout = timeout_seconds - reserved
        command_text = (
            f"exec {clean_exec} {WSL_COMMAND_ID_ENV}={identity} "
            f"{timeout_command} --signal=TERM "
            f"--kill-after={WSL_TIMEOUT_KILL_AFTER_SECONDS}s "
            f"{inner_timeout}s {bash_command} --noprofile --norc -c "
            f"{shell_quote(command_text)}"
        )
    else:
        command_text = (
            f"exec {clean_exec} {WSL_COMMAND_ID_ENV}={identity} "
            f"{bash_command} --noprofile --norc -c {shell_quote(command_text)}"
        )
    controlled_command = _controlled_shell_command(command_text)
    if os.name == "nt":
        return [launcher_executable, "-d", wsl_distro, "--", *controlled_command]
    return controlled_command


def c_string_literal(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\r", "")
        .replace("\n", "\\n")
    )
    return "\"" + escaped + "\""


def compact_seed_text(text: str) -> str:
    keep_by_kind = {
        "studio_launch": {"title", "goal", "workbench_id", "workbench"},
        "research_run": {"run_id", "title", "question", "provider", "dataset_rows", "reference_entries", "workspace_files", "csv_file", "reference_file"},
        "research_rerun": {"run_id", "parent_run", "source_run", "provider", "question", "dataset_rows", "reference_entries", "workspace_files"},
        "dataset": {"title", "dataset_rows", "columns"},
        "dataset_preview": {"dataset_id", "rows", "quality"},
        "dataset_visualization": {"dataset_id", "chart", "x_field", "y_field", "group_field", "points"},
        "dataset_card": {"dataset_id", "readiness", "warnings"},
        "dataset_answer": {"dataset_id", "question", "answer"},
        "dataset_run": {"dataset_id", "run_id", "provider_id", "question", "artifacts"},
        "dataset_run_comparison": {"dataset_id", "left_run", "right_run", "decision"},
        "dataset_portfolio": {"dataset_id", "filter", "datasets", "ready"},
        "library_source": {"citation_key", "tags"},
        "template": {"name", "question", "provider_id"},
        "workspace_inspect": {"root", "max_files"},
        "workspace_import": {"root", "max_files", "manifest", "title", "question"},
        "workspace_import_run": {"root", "max_files", "manifest", "title", "question"},
        "literature_search": {"query", "provider", "max_results"},
        "evidence_review": {"search_id", "reviewer", "include_terms", "included"},
        "evidence_protocol": {"title", "research_question", "outcome"},
        "human_review": {"run_id", "reviewer", "decision"},
        "revision_task": {"review_id", "targets"},
        "revision_run": {"run_id", "task_id"},
        "agentcompare": {"profile"},
        "bundle_export": {"run_id", "bundle"},
        "research_export": {"run_id", "bundle"},
        "delivery": {"run_id", "bundle"},
        "notebook_export": {"run_id", "format"},
        "workbench": {"workbench", "workbench_title", "literature_query"},
        "workbench_complete": {"workbench"},
        "workbench_advance": {"workbench", "task"},
        "workbench_auto_advance": {"step_limit"},
        "workbench_task": {"workbench", "task", "status"},
        "workbench_note": {"workbench", "note_kind", "title", "body"},
        "workbench_notes": {"workbench", "notes_filter"},
        "workbench_handoff_package": {"workbench", "handoff_scope"},
        "workbench_readiness": {"workbench"},
        "workbench_answer": {"question"},
        "workbench_answer_audit": set(),
        "workbench_evidence_search": {"query"},
        "workbench_brief": {"workbench", "brief_format"},
        "workbench_evidence_dossier": {"dossier_format"},
        "workbench_evidence_graph": {"graph_format"},
        "workbench_citations": {"citation_format"},
        "workbench_manuscript": {"manuscript_format"},
        "workbench_manuscript_audit": {"audit_scope"},
        "workbench_manuscript_revision_plan": {"revision_area"},
        "workbench_manuscript_revision_task": {"revision_task", "revision_status"},
        "workbench_task_board": {"board_filter"},
        "workbench_task_board_row": {"row_id", "row_status"},
        "workbench_plan_queue_row": {"workbench_id", "plan_item_id", "status"},
        "workbench_plan_queue_execute": {"workbench_id", "plan_item_id"},
        "workbench_runbook": {"runbook_format"},
        "workbench_timeline": {"timeline_format"},
        "workbench_file_manifest": {"workbench", "manifest", "files", "sha_records"},
        "workbench_file_verify": {"workbench", "manifest", "files", "sha_records", "verified", "missing"},
        "workbench_export": {"workbench", "bundle"},
        "workbench_quality_gate": {"workbench_id"},
        "workbench_quality_repair_plan": {"workbench_id"},
        "workbench_quality_repair_execute": {"workbench_id", "repair_id"},
        "workbench_action_item": {"workbench_id", "title", "status"},
        "workbench_delivery_dashboard": {"tag", "query"},
        "workbench_delivery_execute_next": {"tag", "query"},
        "operations_report": {"format"},
        "operations_advance_next": {"review_decision"},
        "operations_execute_next_plan": set(),
        "project_scaffold": {"template_id", "project_id", "title", "dataset_id", "library_source_id", "files", "workspace"},
        "project_launch": {"project_id", "scaffold_id", "workbench_id", "run_id", "provider_id", "question"},
        "project_action_execute": {"project_id", "action_id", "action_key", "provider_id", "max_steps", "result"},
        "sample_workbench": {"workbench_id", "template_id", "dataset_id", "question"},
        "study_protocol": {"protocol_id", "title", "question", "hypothesis", "dataset_tags", "source_tags"},
        "study_protocol_run": {"protocol_id", "run_id", "provider_id"},
        "study_protocol_compliance": {"run_id", "decision", "findings"},
        "study_protocol_bundle": {"run_id", "bundle", "files"},
        "study_protocol_launch": {"launch_id", "protocol_id", "run_id", "provider_id"},
        "study_protocol_launch_rerun": {"launch_id", "rerun_id", "provider_id"},
        "study_protocol_launch_comparison": {"launch_id", "left", "right", "changed_metrics"},
        "study_protocol_reproduction_package": {"launch_id", "package_id", "files", "notebooks", "datasets"},
        "study_protocol_reproduction_package_review": {"package_id", "decision", "reviewer"},
        "study_protocol_reproduction_package_action_plan": {"package_id", "steps", "owner"},
        "study_protocol_reproduction_package_action_execute": {"package_id", "steps_done", "result", "provider_id"},
        "source_portfolio": {"source_id", "query", "sources", "reviewed"},
        "project_space": {"workbench_id", "project_id", "query"},
        "project_space_note": {"workbench_id", "kind", "title"},
        "project_space_action_item": {"workbench_id", "title", "status"},
        "project_space_review": {"workbench_id", "project_id", "decision", "reviewer", "required_changes"},
        "project_space_answer": {"workbench_id", "question", "limit"},
        "project_space_repair_execute": {"workbench_id", "repair_id"},
        "project_space_task_board_row": {"workbench_id", "row_id", "row_status", "row_note"},
        "project_handoff_audit": {"project_id", "scope", "decision"},
        "project_release_gate": {"project_id", "decision", "checks", "required_actions", "suggested_actions"},
        "project_snapshot": {"project_id", "snapshot_id", "files", "hash_records", "changes"},
        "project_snapshot_comparison": {"project_id", "left", "right", "changed_files", "decision"},
        "project_reproducibility_audit": {"project_id", "inputs", "outputs", "notebooks", "claim_audits", "decision"},
        "project_provenance_graph": {"project_id", "nodes", "edges", "dot"},
        "project_delivery": {"project_id", "bundle", "decision", "release_gate", "handoff"},
        "package_intake": {"package_id", "label", "files", "sha256", "decision"},
        "research_search_save": {"query", "name"},
        "research_search_export": {"query", "limit"},
        "research_search_note": {"workbench_id", "query", "title"},
        "research_search_action_item": {"workbench_id", "query", "title"},
        "host_workflow": {"workflow_id", "run_id", "engine", "dag", "retry_stage", "cache_hit_stage", "worker_slots", "queue_depth", "observer_events", "retry_reason"},
        "host_workflow_export": {"workflow_id", "run_id", "format", "bundle"},
        "host_workflow_stage": {"workflow_id", "run_id", "stage", "attempt", "status", "command", "duration_ms"},
        "host_workflow_cache": {"workflow_id", "run_id", "stage", "cache_key", "cache_result", "cache_policy"},
        "host_workflow_retry": {"workflow_id", "run_id", "stage", "retry_reason", "next_attempt", "decision"},
        "host_workflow_artifact": {"workflow_id", "run_id", "artifact", "artifact_kind", "sha256", "bytes"},
        "host_workflow_report": {"workflow_id", "run_id", "report", "format", "sections", "status"},
        "artifact_input": {
            "run_id", "challenge", "provenance_protocol", "file",
            "artifact_kind", "sha256",
            "bytes", "source", "content_hex",
        },
        "artifact_derive": {
            "run_id", "input", "output", "operation", "stage", "sha256",
            "bytes", "input_sha256",
        },
        "artifact_log": {"run_id", "stage", "log", "level", "message"},
        "artifact_chart": {"run_id", "chart", "chart_type", "data_file", "points"},
        "artifact_package": {"run_id", "package", "manifest", "files", "status"},
        "workflow_portability": {"import_id", "source_format", "source", "target_runtime", "execution_plan", "compare_profile", "scenario_id", "rehearsal_status", "readiness_decision", "package"},
        "workflow_portability_import": {"import_id", "source_format", "source", "normalized_steps", "adapter_id"},
        "workflow_portability_plan": {"import_id", "migration_plan", "target_runtime", "migration_steps", "risk_items"},
        "workflow_portability_bind": {"execution_plan", "compare_profile", "scenario_id", "backend_cases"},
        "workflow_portability_rehearse": {"rehearsal_id", "binding_id", "rehearsal_status", "observed_ready", "skipped"},
        "workflow_portability_review": {"review_id", "readiness_decision", "blocking_items", "work_items"},
        "workflow_portability_package": {"import_id", "package", "export_format", "bundle"},
        "llm_relay_request": {"request_id", "route", "provider"},
        "llm_relay_response": {"response_id", "summary"},
        "llm_relay_fallback": {"case", "action"},
    }
    lines: list[str] = []
    for raw in text.splitlines():
        fields = [field for field in raw.split(";") if field]
        kind = ""
        saw_action_field = False
        saw_path_field = False
        saw_status_field = False
        for field in fields:
            if field.startswith("kind="):
                kind = field.split("=", 1)[1]
                break
        keep_keys = keep_by_kind.get(kind)
        compact: list[str] = []
        for field in fields:
            if field.startswith("kind="):
                compact.insert(0, field)
            else:
                key = field.split("=", 1)[0]
                if key == "action" and not saw_action_field:
                    saw_action_field = True
                    continue
                if key == "path" and not saw_path_field:
                    saw_path_field = True
                    continue
                if key == "status" and not saw_status_field:
                    saw_status_field = True
                    continue
                if keep_keys is not None and key not in keep_keys:
                    continue
                if kind.startswith("workbench_") and key == "workbench":
                    continue
                compact.append(field)
        if compact:
            lines.append(";".join(compact))
    return "\n".join(lines) + ("\n" if lines else "")


def write_seed_header(action_state: Path, repo_dir: Path, seed_file: Path) -> int:
    inbox = action_state / "rp_host_action_inbox"
    text = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
    seed_text = compact_seed_text(text)
    write_fs_aligned_seed(seed_file, seed_text)
    header = repo_dir / "user" / "build" / "generated" / "rp_host_action_seed.h"
    ensure_safe_directory(header.parent)
    atomic_write_text(
        header,
        "#ifndef __RP_HOST_ACTION_SEED_H__\n"
        "#define __RP_HOST_ACTION_SEED_H__\n"
        f"#define RP_HOST_ACTION_SEED {c_string_literal('')}\n"
        f"#define RP_HOST_ACTION_BOOTSTRAP_SEED {c_string_literal(seed_text)}\n"
        "#endif\n",
    )
    return len([line for line in seed_text.splitlines() if line.strip()])




def find_log_value(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if prefix in line:
            return line.strip()
    return ""


def find_exact_guest_log_line(text: str, expected: str) -> str:
    normalized_expected = normalize_guest_log_line(expected)
    for line in text.splitlines():
        normalized = normalize_guest_log_line(line)
        if normalized == normalized_expected:
            return normalized
    return ""


def write_run_result_state(next_state: Path, run_summary: dict[str, object], log_text: str) -> None:
    # 摘要是内部类型合同。畸形真值必须按失败处理，避免诊断状态误报 Guest 成功。
    passed = run_summary.get("passed") is True
    guest_state_files, guest_state_sha256 = guest_state_inventory_sha256(
        next_state, excluded_names=HOST_STATE_FILES
    )
    reported_state_files = run_summary.get("extracted_state_files", 0)
    if passed and (
        type(reported_state_files) is not int
        or reported_state_files != guest_state_files
    ):
        passed = False
        run_summary["passed"] = False
        run_summary["status"] = "failed"
        run_summary["returncode"] = 1
        run_summary["failure_phase"] = "receipt"
        run_summary["failure_reason"] = "guest_state_count_mismatch"
    lines = [
        "host_runner=plain_ucore_action_runner",
        f"target={line_value(run_summary.get('target_identity', ''))}",
        f"chapter={line_value(run_summary.get('chapter', ''))}",
        f"init_proc={line_value(run_summary.get('init_proc', ''))}",
        f"status={'ready' if passed else 'failed'}",
        f"passed={1 if passed else 0}",
        f"embedded_action_records={run_summary.get('embedded_action_records', 0)}",
        f"extracted_state_files={run_summary.get('extracted_state_files', 0)}",
        f"guest_state_receipt_schema={GUEST_STATE_RECEIPT_SCHEMA}",
        f"guest_state_files={guest_state_files}",
        f"guest_state_sha256={guest_state_sha256}",
        f"build_returncode={run_summary.get('build_returncode', '')}",
        f"guest_returncode={run_summary.get('guest_returncode', '')}",
        f"guest_raw_returncode={run_summary.get('guest_raw_returncode', '')}",
        f"failure_phase={line_value(run_summary.get('failure_phase', ''))}",
        f"failure_reason={line_value(run_summary.get('failure_reason', ''))}",
        f"build_log={line_value(run_summary.get('build_log', ''))}",
        f"log={line_value(run_summary.get('log', ''))}",
        f"qemu_elapsed_seconds={run_summary.get('elapsed_seconds', 0)}",
        f"qemu_idle_notices={run_summary.get('idle_notices', 0)}",
        f"qemu_timed_out={1 if run_summary.get('timed_out') else 0}",
        f"qemu_runner_terminated={1 if run_summary.get('runner_terminated') else 0}",
        f"qemu_output_eof={1 if run_summary.get('output_eof') else 0}",
        "qemu_runner_signals="
        + ",".join(str(item) for item in run_summary.get("runner_signals", ())),
    ]
    host_reader_actions = find_log_value(log_text, "rp_web_export: host_reader_actions=")
    host_actions_verified = find_log_value(log_text, "rp_compare_plain: host_actions=")
    orch_passed = find_exact_guest_log_line(log_text, "rp_orch: passed")
    if host_reader_actions:
        lines.append("qemu_" + host_reader_actions)
    if host_actions_verified:
        lines.append("qemu_" + host_actions_verified)
    if passed and orch_passed:
        lines.append("qemu_orch_passed=1")
    (next_state / "rp_host_run_result").write_bytes(
        ("\n".join(lines) + "\n").encode("utf-8")
    )


def revoke_published_receipt(
    state_dir: Path, host_run_result_path: Path | None = None
) -> None:
    try:
        state_dir = require_safe_directory(state_dir)
    except (OSError, ValueError) as error:
        raise ValueError("Guest state destination is missing or unsafe")
    state_root = state_dir.resolve(strict=True)
    legacy_run_result = state_dir / "rp_host_run_result"
    if _is_link_component(legacy_run_result) or (
        legacy_run_result.exists() and not legacy_run_result.is_file()
    ):
        raise ValueError("Guest state receipt is unsafe")
    if host_run_result_path is not None:
        if host_run_result_path.name in ("", ".", ".."):
            raise ValueError("Host run result sidecar is unsafe")
        try:
            sidecar_parent = require_safe_directory(host_run_result_path.parent)
        except (OSError, ValueError) as error:
            raise ValueError("Host run result sidecar parent is unsafe")
        sidecar_candidate = sidecar_parent / host_run_result_path.name
        try:
            sidecar_candidate.relative_to(state_root)
        except ValueError:
            pass
        else:
            raise ValueError("Host run result sidecar must be outside Guest state")
        if _is_link_component(host_run_result_path) or (
            host_run_result_path.exists() and not host_run_result_path.is_file()
        ):
            raise ValueError("Host run result sidecar is unsafe")

    if legacy_run_result.is_file():
        require_regular_file(legacy_run_result)
        legacy_run_result.unlink()
    if host_run_result_path is not None and host_run_result_path.is_file():
        require_regular_file(host_run_result_path)
        host_run_result_path.unlink()


def publish_next_state(
    next_state: Path, state_dir: Path, host_run_result_path: Path | None = None
) -> None:
    try:
        next_state = require_safe_directory(next_state)
    except (OSError, ValueError) as error:
        raise ValueError("Guest state source is missing or unsafe")
    next_root = next_state.resolve(strict=True)
    try:
        state_dir = ensure_safe_directory(state_dir)
    except (OSError, ValueError) as error:
        raise ValueError("Guest state destination is unsafe")
    state_root = state_dir.resolve(strict=True)
    try:
        next_root.relative_to(state_root)
    except ValueError:
        pass
    else:
        raise ValueError("Guest state source must be outside its destination")
    try:
        state_root.relative_to(next_root)
    except ValueError:
        pass
    else:
        raise ValueError("Guest state destination must be outside its source")

    existing: dict[str, Path] = {}
    for item in sorted(state_dir.iterdir()):
        if not item.name.startswith("rp_"):
            continue
        try:
            require_regular_file(item)
        except (OSError, ValueError) as error:
            raise ValueError(f"Guest state destination is unsafe: {item.name}")
        existing[item.name] = item

    receipt_source = next_state / "rp_host_run_result"
    receipt_temporary: Path | None = None
    sidecar_parent: Path | None = None
    if host_run_result_path is not None:
        if host_run_result_path.name in ("", ".", ".."):
            raise ValueError("Host run result sidecar is unsafe")
        try:
            sidecar_parent = ensure_safe_directory(host_run_result_path.parent)
        except (OSError, ValueError) as error:
            raise ValueError("Host run result sidecar parent is unsafe")
        sidecar_parent = sidecar_parent.resolve(strict=True)
        sidecar_candidate = sidecar_parent / host_run_result_path.name
        try:
            sidecar_candidate.relative_to(state_root)
        except ValueError:
            pass
        else:
            raise ValueError("Host run result sidecar must be outside Guest state")
        try:
            sidecar_candidate.relative_to(next_root)
        except ValueError:
            pass
        else:
            raise ValueError("Host run result sidecar must be outside Guest state source")
        if _is_link_component(host_run_result_path) or (
            host_run_result_path.exists() and not host_run_result_path.is_file()
        ):
            raise ValueError("Host run result sidecar is unsafe")

    # 回执是 Guest 快照的提交标记。检查或发布候选 generation 前先使全部旧标记失效。
    revoke_published_receipt(state_dir, host_run_result_path)

    sources: dict[str, Path] = {}
    for item in sorted(next_state.iterdir()):
        if not item.name.startswith("rp_"):
            continue
        try:
            require_regular_file(item)
        except (OSError, ValueError) as error:
            raise ValueError(f"Guest state source is unsafe: {item.name}")
        if item.name == "rp_host_run_result":
            continue
        if item.name in HOST_STATE_FILES:
            raise ValueError(
                f"Guest snapshot contains Host-only state: {item.name}"
            )
        sources[item.name] = item
    if host_run_result_path is not None and (
        _is_link_component(receipt_source) or not receipt_source.is_file()
    ):
        raise ValueError("Host run result source is missing or unsafe")

    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    installed: set[str] = set()
    try:
        if host_run_result_path is not None and sidecar_parent is not None:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{host_run_result_path.name}.",
                suffix=".tmp",
                dir=sidecar_parent,
            )
            os.close(fd)
            receipt_temporary = Path(temporary_name)
            shutil.copy2(receipt_source, receipt_temporary)
        for name, source in sources.items():
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".tmp", dir=state_root
            )
            os.close(fd)
            temporary = Path(temporary_name)
            staged[name] = temporary
            shutil.copy2(source, temporary)

        for name, destination in existing.items():
            if name == "rp_host_run_result":
                continue
            if _is_link_component(destination) or not destination.is_file():
                raise ValueError(f"Guest state destination is unsafe: {name}")
            fd, backup_name = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".bak", dir=state_root
            )
            os.close(fd)
            backup = Path(backup_name)
            try:
                replace_path(destination, backup)
            except Exception:
                if backup.is_file() and not _is_link_component(backup):
                    backup.unlink()
                raise
            backups[name] = backup

        for name, temporary in staged.items():
            destination = state_dir / name
            if _is_link_component(destination) or destination.exists():
                raise ValueError(f"Guest state destination is unsafe: {name}")
            replace_path(temporary, destination)
            installed.add(name)

        if (
            receipt_temporary is not None
            and host_run_result_path is not None
        ):
            replace_path(receipt_temporary, host_run_result_path)
    except Exception as publish_error:
        rollback_errors: list[str] = []
        for name in installed:
            destination = state_dir / name
            try:
                if _is_link_component(destination) or not destination.is_file():
                    raise ValueError(f"unsafe installed state: {name}")
                destination.unlink()
            except (OSError, ValueError) as error:
                rollback_errors.append(f"remove {name}: {error}")
        for name, backup in backups.items():
            destination = state_dir / name
            try:
                if not backup.is_file() or _is_link_component(backup):
                    raise ValueError(f"unsafe backup state: {name}")
                if destination.exists() or _is_link_component(destination):
                    raise ValueError(f"occupied restore destination: {name}")
                replace_path(backup, destination)
            except (OSError, ValueError) as error:
                rollback_errors.append(f"restore {name}: {error}")
        for temporary in staged.values():
            if temporary.is_file() and not _is_link_component(temporary):
                temporary.unlink()
        if (
            receipt_temporary is not None
            and receipt_temporary.is_file()
            and not _is_link_component(receipt_temporary)
        ):
            receipt_temporary.unlink()
        if host_run_result_path is not None and (
            host_run_result_path.is_file() and not _is_link_component(host_run_result_path)
        ):
            host_run_result_path.unlink()
        if rollback_errors:
            raise RuntimeError(
                "Guest state publication rollback failed: "
                + "; ".join(rollback_errors)
            ) from publish_error
        raise
    for backup in backups.values():
        try:
            if backup.is_file() and not _is_link_component(backup):
                backup.unlink()
        except OSError:
            pass


def _run_seeded_ucore_locked(
    repo_dir: Path,
    run_dir: Path,
    timeout_seconds: int,
    wsl_distro: str,
    chapter: str = "platform_seeded",
    init_proc: str = "rp_seed_orch",
    pass_marker: str = "rp_orch: passed",
    target_identity: str = "plain",
) -> dict[str, object]:
    repo_dir = require_safe_directory(_absolute_lexical_path(repo_dir)).resolve(strict=True)
    deadline_contract = seeded_ucore_deadline_contract(timeout_seconds)
    phase_timeout_seconds = int(deadline_contract["phase_timeout_seconds"])
    run_dir = require_private_directory(run_dir)
    build_log_path = run_dir / "ucore-build.log"
    log_path = run_dir / "ucore-run.log"
    next_state = run_dir / "state-next"
    if next_state.exists() or _is_link_component(next_state):
        reject_link_components(next_state)
        if not next_state.is_dir():
            raise ValueError("Guest action state directory is unsafe")
    else:
        create_private_directory(next_state)
    host_input_dir = run_dir / "host-input"
    host_input_dir = create_private_directory(host_input_dir)
    try:
        source_identity = capture_source_identity(repo_dir)
    except ValueError as error:
        write_text(build_log_path, f"[runner] source identity rejected: {error}\n")
        summary = {
            "commands": [],
            "returncode": 1,
            "build_returncode": None,
            "guest_returncode": None,
            "guest_raw_returncode": None,
            "embedded_action_records": 0,
            "extracted_state_files": 0,
            "target_identity": target_identity,
            "chapter": chapter,
            "init_proc": init_proc,
            "passed": False,
            "build_log": str(build_log_path),
            "log": str(log_path),
            "failure_phase": "source",
            "failure_reason": "source_identity_unavailable",
            "error": str(error),
            "status": "failed",
        }
        write_run_result_state(next_state, summary, "")
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary
    repo_bash = bash_path(repo_dir)
    build_jobs = adaptive_build_jobs(repo_dir)
    build_jobs_argument = make_var_arg("AGENTOS_BUILD_JOBS", str(build_jobs))
    make_executable = shell_quote(os.environ.get("AGENTOS_WSL_MAKE", "make"))
    clean_command = (
        f"cd {shell_quote(repo_bash)} && "
        f"{make_executable} -C user clean >/dev/null && "
        f"{make_executable} clean >/dev/null"
    )
    clean_code = run_command(
        make_wsl_command(clean_command, wsl_distro, phase_timeout_seconds),
        build_log_path,
        phase_timeout_seconds,
    )
    if clean_code != 0:
        summary = {
            "commands": [clean_command],
            "build_jobs": build_jobs,
            "returncode": clean_code,
            "build_returncode": clean_code,
            "guest_returncode": None,
            "guest_raw_returncode": None,
            "embedded_action_records": 0,
            "extracted_state_files": 0,
            "target_identity": target_identity,
            "chapter": chapter,
            "init_proc": init_proc,
            "passed": False,
            "build_log": str(build_log_path),
            "log": str(log_path),
            "failure_phase": "clean",
            "failure_reason": "clean_failed",
            "status": "failed",
        }
        write_run_result_state(next_state, summary, "")
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary
    seed_file = host_input_dir / "rp_host_action_seed"
    embedded_records = write_seed_header(next_state, repo_dir, seed_file)
    pad_state_files_for_ucore_fs(next_state)
    seed_file_bash = bash_path(seed_file)
    toolprefix = toolprefix_arg()
    build_command_text = (
        f"cd {shell_quote(repo_bash)} && "
        f"{make_executable} -j{build_jobs} user {toolprefix} "
        f"{build_jobs_argument} CHAPTER={chapter}"
        " && "
        f"cp {shell_quote(seed_file_bash)} user/target/bin/rp_host_action_seed"
        " && "
        "rm -rf nfs/fs nfs/fs.img nfs/fs-copy.img && "
        f"{make_executable} -j{build_jobs} nfs/fs-copy.img {toolprefix} "
        f"{build_jobs_argument} CHAPTER={chapter} && "
        f"{make_executable} -j{build_jobs} build {toolprefix} "
        f"{build_jobs_argument} CHAPTER={chapter} LOG=warn INIT_PROC={init_proc}"
    )
    build_code = run_command(
        make_wsl_command(build_command_text, wsl_distro, phase_timeout_seconds),
        build_log_path,
        phase_timeout_seconds,
        append=True,
    )
    if build_code != 0:
        summary = {
            "commands": [clean_command, f"embedded_action_records={embedded_records}", build_command_text],
            "build_jobs": build_jobs,
            "returncode": build_code,
            "build_returncode": build_code,
            "guest_returncode": None,
            "guest_raw_returncode": None,
            "embedded_action_records": embedded_records,
            "extracted_state_files": 0,
            "target_identity": target_identity,
            "chapter": chapter,
            "init_proc": init_proc,
            "passed": False,
            "build_log": str(build_log_path),
            "log": str(log_path),
            "failure_phase": "build",
            "failure_reason": "build_failed",
            "status": "failed",
        }
        write_run_result_state(next_state, summary, "")
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary
    run_command_text = (
        f"cd {shell_quote(repo_bash)} && "
        f"{make_executable} run-prebuilt {toolprefix} {build_jobs_argument} "
        f"CHAPTER={chapter} LOG=warn INIT_PROC={init_proc}"
    )
    observed = run_observed_command(
        make_wsl_command(run_command_text, wsl_distro, phase_timeout_seconds),
        log_path,
        phase_timeout_seconds,
        append=False,
        pass_marker=pass_marker,
        idle_notice_seconds=int(os.environ.get("SEEDED_ACTION_IDLE_NOTICE_SECONDS", "20")),
    )
    code = int(observed["returncode"])
    text = log_path.read_text(encoding="utf-8", errors="replace")
    passed = (
        bool(observed["marker_seen"])
        and not bool(observed["failure_seen"])
        and not bool(observed["timed_out"])
        and code == 0
    )
    guest_passed = passed
    failure_phase = "" if passed else "guest"
    failure_reason = str(observed["failure_reason"])
    extract_summary: dict[str, object] = {"status": "skipped", "extracted_state_files": 0}
    image_path = repo_dir / "nfs" / "fs-copy.img"
    if image_path.exists():
        try:
            extract_summary = extract_state_files(
                image_path,
                run_dir / "state-extracted",
                repo_dir,
                require_single_scope=True,
            )
        # 构建输入包含仅供 Host 使用的动作文件；创建回执前必须以精确抽取的 Guest
        # generation 替换它。
            clean_copy_state(run_dir / "state-extracted", next_state)
        except (OSError, ValueError) as error:
            passed = False
            if guest_passed:
                failure_phase = "extract"
                failure_reason = "extract_failed"
            extract_summary = {
                "status": "failed",
                "error": str(error),
                "extracted_state_files": 0,
            }
    else:
        passed = False
        if guest_passed:
            failure_phase = "extract"
            failure_reason = "missing_image"
        extract_summary = {"status": "missing_image", "extracted_state_files": 0}
    runtime_artifacts: dict[str, object] = {}
    if passed:
        try:
            runtime_artifacts = archive_runtime_artifacts(repo_dir, run_dir)
        except (OSError, ValueError) as error:
            passed = False
            failure_phase = "archive"
            failure_reason = "artifact_archive_failed"
            runtime_artifacts = {"error": str(error)}
    if passed:
        try:
            verify_source_identity(repo_dir, source_identity)
        except ValueError as error:
            passed = False
            failure_phase = "source"
            failure_reason = "source_identity_changed"
            runtime_artifacts = {**runtime_artifacts, "source_error": str(error)}
    successful_source_identity = source_identity if passed else {}
    summary = {
        "commands": [
            clean_command,
            f"embedded_action_records={embedded_records}",
            build_command_text,
            run_command_text,
        ],
        "build_jobs": build_jobs,
        "returncode": code if passed or code != 0 else 1,
        "build_returncode": build_code,
        "guest_returncode": code,
        "guest_raw_returncode": observed["raw_returncode"],
        "marker_seen": bool(observed["marker_seen"]),
        "failure_seen": bool(observed["failure_seen"]),
        "failure_line": str(observed["failure_line"]),
        "failure_reason": failure_reason,
        "failure_phase": failure_phase,
        "timed_out": bool(observed["timed_out"]),
        "runner_terminated": bool(observed["runner_terminated"]),
        "runner_signals": list(observed["runner_signals"]),
        "output_eof": bool(observed["output_eof"]),
        "wsl_cleanup_applicable": bool(observed["wsl_cleanup_applicable"]),
        "wsl_cleanup_verified": bool(observed["wsl_cleanup_verified"]),
        "wsl_cleanup_initial_survivors": observed[
            "wsl_cleanup_initial_survivors"
        ],
        "wsl_cleanup_remaining_survivors": observed[
            "wsl_cleanup_remaining_survivors"
        ],
        "idle_notices": int(observed["idle_notices"]),
        "elapsed_seconds": observed["elapsed_seconds"],
        "embedded_action_records": embedded_records,
        "extracted_state_files": extract_summary.get("extracted_state_files", 0),
        "extract_status": extract_summary.get("status", "unknown"),
        "runtime_artifacts": runtime_artifacts,
        "target_identity": target_identity,
        "chapter": chapter,
        "init_proc": init_proc,
        "passed": passed,
        "build_log": str(build_log_path),
        "log": str(log_path),
        "status": "ready" if passed else "failed",
        **successful_source_identity,
    }
    write_run_result_state(next_state, summary, text)
    write_json(run_dir / "ucore-run-summary.json", summary)
    return summary


def run_seeded_ucore(
    repo_dir: Path,
    run_dir: Path,
    timeout_seconds: int,
    wsl_distro: str,
    chapter: str = "platform_seeded",
    init_proc: str = "rp_seed_orch",
    pass_marker: str = "rp_orch: passed",
    target_identity: str = "plain",
) -> dict[str, object]:
    repo_dir = require_safe_directory(_absolute_lexical_path(repo_dir)).resolve(strict=True)
    if run_dir.exists() or _is_link_component(run_dir):
        run_dir = require_private_directory(run_dir)
    else:
        run_dir = create_private_directory(run_dir)
    try:
        with exclusive_repo_run_lock(repo_dir):
            return _run_seeded_ucore_locked(
                repo_dir,
                run_dir,
                timeout_seconds,
                wsl_distro,
                chapter,
                init_proc,
                pass_marker,
                target_identity,
            )
    except RepoRunBusy as error:
        summary = {
            "commands": [],
            "returncode": 1,
            "build_returncode": None,
            "guest_returncode": None,
            "guest_raw_returncode": None,
            "embedded_action_records": 0,
            "extracted_state_files": 0,
            "target_identity": target_identity,
            "chapter": chapter,
            "init_proc": init_proc,
            "extract_status": "skipped",
            "passed": False,
            "failure_phase": "coordination",
            "failure_reason": "repo_busy",
            "error": str(error),
            "build_log": str(run_dir / "ucore-build.log"),
            "log": str(run_dir / "ucore-run.log"),
            "status": "failed",
        }
        next_state = run_dir / "state-next"
        if next_state.exists() or _is_link_component(next_state):
            reject_link_components(next_state)
            if not next_state.is_dir():
                raise ValueError("Guest action state directory is unsafe")
        else:
            create_private_directory(next_state)
        write_run_result_state(next_state, summary, "")
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary


def run_plain_ucore(repo_dir: Path, run_dir: Path, timeout_seconds: int, wsl_distro: str) -> dict[str, object]:
    return run_seeded_ucore(repo_dir, run_dir, timeout_seconds, wsl_distro)


def append_records(existing: Iterable[dict[str, object]], extra: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    records = list(existing)
    next_sequence = 1
    if records:
        next_sequence = max(int(record.get("sequence", 0) or 0) for record in records) + 1
    for record in extra:
        copy = dict(record)
        copy.setdefault("sequence", next_sequence)
        copy.setdefault("status", "accepted")
        records.append(copy)
        next_sequence += 1
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare plain uCore input from host actions.")
    parser.add_argument("--actions", type=Path, required=True, help="Host action JSONL input.")
    parser.add_argument("--state-dir", type=Path, required=True, help="Current rp_* state directory.")
    parser.add_argument("--run-dir", type=Path, required=True, help="Directory for prepared action package and logs.")
    parser.add_argument("--add-action", action="append", default=[], help="Add an action path, for example /actions/research/run.")
    parser.add_argument("--payload", action="append", default=[], help="Payload key=value for --add-action records.")
    parser.add_argument("--run-ucore", action="store_true", help="Run the plain uCore seeded path after preparing actions.")
    parser.add_argument("--repo-dir", type=Path, default=Path("."), help="Repository root for --run-ucore.")
    parser.add_argument("--timeout", type=int, default=80, help="QEMU run timeout in seconds.")
    parser.add_argument("--wsl-distro", default="Ubuntu", help="WSL distribution name on Windows.")
    parser.add_argument(
        "--update-state-dir",
        action="store_true",
        help="Copy prepared Guest rp_* action state back to --state-dir; publish the Host run receipt only through --run-result-sidecar.",
    )
    parser.add_argument(
        "--run-result-sidecar",
        type=Path,
        default=None,
        help="Host-only run receipt destination outside --state-dir.",
    )
    args = parser.parse_args()

    if args.update_state_dir and not args.run_ucore:
        parser.error("--update-state-dir requires --run-ucore")
    if args.update_state_dir and args.run_result_sidecar is None:
        parser.error("--update-state-dir requires --run-result-sidecar")
    if args.run_result_sidecar is not None and not args.update_state_dir:
        parser.error("--run-result-sidecar requires --update-state-dir")

    extra_payload: dict[str, str] = {}
    for item in args.payload:
        if "=" in item:
            key, value = item.split("=", 1)
            extra_payload[key] = value
    extra_actions = [{"path": path, "payload": extra_payload} for path in args.add_action]

    actions = append_records(read_jsonl(args.actions), extra_actions)
    summary = prepare_action_state(actions, args.state_dir, args.run_dir)
    print(
        "plain_ucore_action_runner: actions={actions} accepted={accepted} status={status}".format(
            **summary
        )
    )
    if args.run_ucore:
        if args.update_state_dir:
            revoke_published_receipt(args.state_dir, args.run_result_sidecar)
        run_summary = run_plain_ucore(args.repo_dir, args.run_dir, args.timeout, args.wsl_distro)
        if args.update_state_dir and run_summary.get("passed") is True:
            publish_next_state(
                args.run_dir / "state-next",
                args.state_dir,
                args.run_result_sidecar,
            )
        print("plain_ucore_action_runner: ucore_status={status} passed={passed}".format(**run_summary))
        return 0 if run_summary["passed"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
