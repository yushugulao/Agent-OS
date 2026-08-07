#!/usr/bin/env python3
"""证据合同使用的有界、抗 graft Git 历史检查。"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping

MAX_COMMITS = 100000
MAX_PROCESSES = 4
MAX_SECONDS = 60.0
MAX_COMMIT_BYTES = 4 << 20
MAX_COMMIT_TOTAL_BYTES = 64 << 20
MAX_TOTAL_BYTES = 128 << 20
MAX_DIFF_BYTES = 16 << 20
MAX_CANDIDATE_PATH_BYTES = 256
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")

class GitHistoryError(RuntimeError):
    pass

class HistoryBudget:
    """一次历史验证共享同一墙钟、进程和字节预算。"""

    def __init__(self) -> None:
        self.deadline = time.monotonic() + MAX_SECONDS
        self.processes = 0
        self.bytes = 0

    def reserve_process(self) -> None:
        if self.processes >= MAX_PROCESSES:
            raise GitHistoryError("raw ancestry Git process budget exceeded")
        if time.monotonic() >= self.deadline:
            raise GitHistoryError("raw ancestry time budget exceeded")
        self.processes += 1

    def consume_bytes(self, count: int) -> None:
        self.bytes += count
        if self.bytes > MAX_TOTAL_BYTES:
            raise GitHistoryError("raw ancestry byte budget exceeded")
def _bounded_git_bytes(
    git: str, repo: Path, *args: str,
    input_bytes: bytes | None,
    maximum_bytes: int, budget: HistoryBudget,
    environment: Mapping[str, str], label: str,
) -> bytes:
    """在共享截止时间和实时输出大小护栏下运行 Git。"""
    budget.reserve_process()
    with (tempfile.TemporaryFile() as stdin_file,
          tempfile.TemporaryFile() as stdout_file,
          tempfile.TemporaryFile() as stderr_file):
        if input_bytes is not None:
            stdin_file.write(input_bytes)
            stdin_file.seek(0)
        try:
            process = subprocess.Popen(
                [git, *args], cwd=repo, stdout=stdout_file, stderr=stderr_file,
                stdin=stdin_file if input_bytes is not None else subprocess.DEVNULL,
                env=dict(environment))
        except OSError as error:
            raise GitHistoryError(f"cannot start Git for {label}") from error
        while process.poll() is None:
            output_size = os.fstat(stdout_file.fileno()).st_size
            error_size = os.fstat(stderr_file.fileno()).st_size
            if output_size > maximum_bytes or error_size > (1 << 20):
                process.kill()
                process.wait()
                raise GitHistoryError(f"{label} exceeds its output budget")
            if time.monotonic() >= budget.deadline:
                process.kill()
                process.wait()
                raise GitHistoryError("raw ancestry time budget exceeded")
            time.sleep(0.01)
        output_size = os.fstat(stdout_file.fileno()).st_size
        error_size = os.fstat(stderr_file.fileno()).st_size
        if output_size > maximum_bytes or error_size > (1 << 20):
            raise GitHistoryError(f"{label} exceeds its output budget")
        stdout_file.seek(0)
        stderr_file.seek(0)
        output = stdout_file.read()
        error_output = stderr_file.read()
        budget.consume_bytes(len(output) + len(error_output))
        if process.returncode:
            detail = error_output.decode("utf-8", errors="replace").strip()
            raise GitHistoryError(f"git {' '.join(args)} failed: {detail}")
        return output

def _parents(raw: bytes) -> list[str]:
    headers, separator, _message = raw.partition(b"\n\n")
    if not separator:
        raise GitHistoryError("commit object is missing its header boundary")
    parents: list[str] = []
    for line in headers.splitlines():
        if line.startswith(b"parent "):
            parent = line[7:]
            if re.fullmatch(rb"[0-9a-f]{40}", parent) is None:
                raise GitHistoryError("commit object contains a malformed parent")
            parents.append(parent.decode("ascii"))
    return parents

def raw_commit_ancestry(
    git: str, repo: Path, head: str, environment: Mapping[str, str], *,
    budget: HistoryBudget | None = None,
) -> dict[str, list[str]]:
    """从有界原始提交字节重建 DAG，并拒绝 graft 视图。"""
    if FULL_COMMIT.fullmatch(head) is None:
        raise GitHistoryError("raw ancestry head is invalid")
    active = budget or HistoryBudget()
    raw_list = _bounded_git_bytes(
        git, repo, "rev-list", f"--max-count={MAX_COMMITS + 1}", head, "--",
        input_bytes=None, maximum_bytes=(MAX_COMMITS + 1) * 41,
        budget=active, environment=environment,
        label="raw ancestry commit inventory",
    )
    commits = raw_list.splitlines()
    if (
        not commits or len(commits) > MAX_COMMITS
        or any(re.fullmatch(rb"[0-9a-f]{40}", item) is None for item in commits)
        or len(set(commits)) != len(commits) or head.encode("ascii") not in commits
    ):
        raise GitHistoryError("raw commit ancestry exceeds its budget or is invalid")
    request = b"".join(item + b"\n" for item in commits)
    maximum_object_bytes = min(MAX_COMMIT_TOTAL_BYTES,
                               len(commits) * MAX_COMMIT_BYTES)
    objects = _bounded_git_bytes(
        git, repo, "cat-file", "--batch", input_bytes=request,
        maximum_bytes=maximum_object_bytes + len(commits) * 96, budget=active,
        environment=environment, label="raw ancestry commit object",
    )
    ancestry: dict[str, list[str]] = {}
    offset = 0
    total_commit_bytes = 0
    for expected in commits:
        header_end = objects.find(b"\n", offset)
        header = objects[offset:header_end].split() if header_end >= 0 else []
        if (len(header) != 3 or header[0] != expected
                or header[1] != b"commit" or not header[2].isdigit()):
            raise GitHistoryError("raw ancestry commit stream header is invalid")
        size = int(header[2])
        if size > MAX_COMMIT_BYTES:
            raise GitHistoryError("raw ancestry commit object exceeds its byte budget")
        total_commit_bytes += size
        if total_commit_bytes > MAX_COMMIT_TOTAL_BYTES:
            raise GitHistoryError("raw ancestry commit bytes exceed their budget")
        start, end = header_end + 1, header_end + 1 + size
        if end >= len(objects) or objects[end:end + 1] != b"\n":
            raise GitHistoryError("raw ancestry commit stream is truncated")
        ancestry[expected.decode("ascii")] = _parents(objects[start:end])
        offset = end + 1
    if offset != len(objects):
        raise GitHistoryError("raw ancestry commit stream has trailing bytes")
    reachable: set[str] = set()
    pending = [head]
    while pending:
        commit = pending.pop()
        if commit in reachable:
            continue
        parents = ancestry.get(commit)
        if parents is None:
            raise GitHistoryError("raw ancestry is incomplete")
        reachable.add(commit)
        pending.extend(parents)
    if reachable != set(ancestry):
        raise GitHistoryError("Git ancestry view differs from raw commit parents")
    return ancestry

def commits_containing_path(
    git: str, repo: Path, commits: list[str], path: str,
    environment: Mapping[str, str], budget: HistoryBudget,
) -> set[str]:
    """在一次有界查询中解析每个候选提交的一个路径。"""

    try:
        path_bytes = path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise GitHistoryError("raw ancestry candidate path is invalid") from error
    if (
        not path
        or len(path_bytes) > MAX_CANDIDATE_PATH_BYTES
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(character.isspace() or ord(character) < 0x20 for character in path)
    ):
        raise GitHistoryError("raw ancestry candidate path is invalid")
    if (
        len(commits) > MAX_COMMITS
        or len(set(commits)) != len(commits)
        or any(FULL_COMMIT.fullmatch(commit) is None for commit in commits)
    ):
        raise GitHistoryError("raw ancestry candidate inventory is invalid")
    if not commits:
        return set()
    expressions = [commit.encode("ascii") + b":" + path_bytes for commit in commits]
    request = b"".join(expression + b"\n" for expression in expressions)
    maximum_bytes = sum(max(len(item) + len(b" missing\n"), 96)
                        for item in expressions)
    raw = _bounded_git_bytes(
        git, repo, "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_bytes=request, maximum_bytes=maximum_bytes,
        budget=budget, environment=environment,
        label="raw ancestry candidate path inventory",
    )
    rows = raw.splitlines()
    if len(rows) != len(commits):
        raise GitHistoryError("raw ancestry candidate path inventory is invalid")
    present: set[str] = set()
    for commit, expression, row in zip(commits, expressions, rows):
        if row == expression + b" missing":
            continue
        fields = row.split()
        if (len(fields) != 3
                or re.fullmatch(rb"[0-9a-f]{40}", fields[0]) is None
                or fields[1] not in {b"blob", b"tree", b"commit"}
                or not fields[2].isdigit()):
            raise GitHistoryError("raw ancestry candidate path inventory is invalid")
        present.add(commit)
    return present

def documentation_changed_paths(
    git: str, repo: Path, evidence: str, graph: dict[str, list[str]],
    environment: Mapping[str, str], budget: HistoryBudget,
) -> set[str]:
    """通过一次显式成对 diff 返回 E..HEAD 的全部变更路径。"""

    ancestors: set[str] = set()
    pending = [evidence]
    while pending:
        commit = pending.pop()
        if commit in ancestors:
            continue
        parents = graph.get(commit)
        if parents is None:
            raise GitHistoryError("evidence ancestry is incomplete")
        ancestors.add(commit)
        pending.extend(parents)
    comparisons: list[tuple[str, str]] = []
    for commit in sorted(set(graph) - ancestors):
        parents = graph[commit]
        if not parents:
            raise GitHistoryError("documentation descendant ancestry is invalid")
        comparisons.extend((parent, commit) for parent in parents)
    if not comparisons:
        return set()
    request = "".join(f"{parent} {commit}\n" for parent, commit in comparisons).encode("ascii")
    if len(request) > MAX_COMMITS * 2 * 82:
        raise GitHistoryError("documentation descendant edge budget exceeded")
    raw = _bounded_git_bytes(
        git, repo, "diff-tree", "--stdin", "-r", "--no-commit-id",
        "--name-status", "-z", "--no-renames", "--",
        input_bytes=request, maximum_bytes=MAX_DIFF_BYTES, budget=budget,
        environment=environment, label="documentation descendant diff",
    )
    fields = raw.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise GitHistoryError("Git descendant diff output is truncated")
    paths: set[str] = set()
    for index in range(0, len(fields), 2):
        try:
            fields[index].decode("ascii")
            path = fields[index + 1].decode("utf-8")
        except UnicodeDecodeError as error:
            raise GitHistoryError("Git descendant diff output is invalid") from error
        if not path or any(ord(character) < 0x20 for character in path):
            raise GitHistoryError("Git descendant path contains control characters")
        paths.add(path)
    return paths
