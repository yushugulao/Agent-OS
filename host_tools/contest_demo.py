#!/usr/bin/env python3
"""Verify and render the short, fully offline AgentOS contest demo."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from measured_experiments import MeasurementError, extract_file_query_measurements


SCHEMA_VERSION = 2
KIND = "agentos-contest-demo"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[0-9a-f]{16}$")
TASK_LABELS = {
    "task1": "Agent 进程与直接 Context",
    "task2": "结构化工具协议",
    "task3": "多轮 Context 与回滚",
    "task4": "文件属性查询与索引",
    "task5": "事件、心跳与调度",
    "task6": "多 Agent 恢复场景",
}
FAILURE_LINE = re.compile(
    r"^(?:\[PANIC |\[ERROR |(?:agentfinal|agentbench|labdemo)_ucore: "
    r"(?:check failed|failed|result status mismatch))",
    re.IGNORECASE,
)
FUNCTIONAL_PATTERNS = {
    "task1": (
        re.compile(r"^agentfinal_ucore: context size=([1-9][0-9]*) capacity=([1-9][0-9]*)$"),
        re.compile(r"^agentfinal_ucore: context_commit_lane=1 sequence=1\.\.3 hash=1$"),
        re.compile(r"^agentfinal_ucore: batch first_seq=1 last_seq=64$"),
    ),
    "task2": (
        re.compile(r"^agentfinal_ucore: generic_action_abi=1$"),
        re.compile(r"^agentfinal_ucore: llm_template_relay=1$"),
        re.compile(r"^agentfinal_ucore: legacy_name_protocol=1$"),
    ),
    "task3": (
        re.compile(r"^agentfinal_ucore: context_rollback_branch=1 sequence_reuse=0 provenance_bound=1$"),
        re.compile(r"^agentfinal_ucore: context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1$"),
        re.compile(r"^agentfinal_ucore: context_rollback_negative nonexistent=1 evicted=1$"),
    ),
    "task4": (
        re.compile(r"^agentfinal_ucore: file_query hits=([1-9][0-9]*) scanned=([1-9][0-9]*) used_index=1$"),
        re.compile(r"^agentfinal_ucore: prefetch_hints=1 count=([1-9][0-9]*) first_stage=[A-Za-z0-9._-]+$"),
    ),
    "task5": (
        re.compile(r"^agentfinal_ucore: event_wait=1 payload=self wake$"),
        re.compile(r"^agentfinal_ucore: runtime_trace=1 records=([1-9][0-9]*) context=1 sched=1 wait=1$"),
        re.compile(r"^agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=([1-9][0-9]*) wakeups=([1-9][0-9]*)$"),
    ),
}
LAB_PATTERNS = {
    "startup": re.compile(r"^labdemo_ucore: startup_barrier ready=3 event_queued=1 released=3$"),
    "audit": re.compile(r"^labdemo_ucore: global_audit=1 records=([1-9][0-9]*) agents=3 context=1 event=1 sched=1 prefetch=1$"),
    "timeline": re.compile(r"^labdemo_ucore: unified_timeline records=([1-9][0-9]*) context=1 event=1 sched=1 prefetch=1$"),
    "provenance": re.compile(r"^labdemo_ucore: provenance_graph edges=([1-9][0-9]*) message=1 prefetch=1$"),
    "scenario": re.compile(r"^labdemo_ucore: passed$"),
    "parent": re.compile(r"^labdemo_ucore: parent passed$"),
}


class ContestDemoError(RuntimeError):
    """The live evidence cannot be proved from its source and Guest logs."""


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        raise ContestDemoError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def clean_source_identity(root: Path) -> str:
    """Return HEAD only when all tracked and untracked source is committed."""
    root = root.resolve()
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise ContestDemoError("source root is not the Git worktree root")
    commit = _run_git(root, "rev-parse", "--verify", "HEAD")
    if not COMMIT.fullmatch(commit):
        raise ContestDemoError("HEAD is not a full commit identity")
    dirty = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise ContestDemoError("source worktree is dirty; commit before running contest-demo")
    return commit


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ContestDemoError(f"{label} must be a regular non-symlink file")
    return path


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_guest(path: Path, label: str) -> list[str]:
    path = _regular_file(path, label)
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if any(FAILURE_LINE.match(line) for line in lines):
        raise ContestDemoError(f"{label} contains a Guest failure line")
    return lines


def _unique_match(lines: list[str], pattern: re.Pattern[str], label: str) -> re.Match[str]:
    found = [match for line in lines if (match := pattern.fullmatch(line))]
    if len(found) != 1:
        raise ContestDemoError(f"{label} must occur exactly once")
    return found[0]


def _verify_functional(path: Path) -> dict[str, Any]:
    lines = _read_guest(path, "Task 1-5 Guest log")
    _unique_match(
        lines, re.compile(r"^agentfinal_ucore: parent passed$"), "functional parent pass"
    )
    receipts: dict[str, Any] = {}
    for task, patterns in FUNCTIONAL_PATTERNS.items():
        matches = [
            _unique_match(lines, pattern, f"{task} mechanism marker {index}")
            for index, pattern in enumerate(patterns, 1)
        ]
        receipts[task] = {
            "mechanism_markers": len(matches),
            "captured_values": [list(match.groups()) for match in matches if match.groups()],
        }
    return receipts


def _verify_labdemo(path: Path) -> dict[str, int]:
    lines = _read_guest(path, "Task 6 Guest log")
    matches: dict[str, tuple[int, re.Match[str]]] = {}
    for key, pattern in LAB_PATTERNS.items():
        found = [
            (index, match)
            for index, line in enumerate(lines)
            if (match := pattern.fullmatch(line)) is not None
        ]
        if len(found) != 1:
            raise ContestDemoError(f"Task 6 marker {key!r} must occur exactly once")
        matches[key] = found[0]
    if [matches[key][0] for key in LAB_PATTERNS] != sorted(
        matches[key][0] for key in LAB_PATTERNS
    ):
        raise ContestDemoError("Task 6 lifecycle markers are out of order")
    return {
        "audit_records": int(matches["audit"][1].group(1)),
        "timeline_records": int(matches["timeline"][1].group(1)),
        "provenance_edges": int(matches["provenance"][1].group(1)),
    }


def _verify_benchmark(path: Path, commit: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _read_guest(path, "benchmark Guest log")
    try:
        receipt = extract_file_query_measurements(
            path,
            "benchmark-qemu.log",
            ["make", "contest-demo"],
            commit,
            run_id,
        )
    except MeasurementError as error:
        raise ContestDemoError(f"benchmark validation failed: {error}") from error
    rows = receipt["rows"]
    if len(rows) != 3 or {row["path"] for row in rows} != {
        "traversal",
        "cold_index",
        "warm_index",
    }:
        raise ContestDemoError("benchmark must contain one complete three-path trial")
    paths = {}
    for row in rows:
        operations = int(row["operations"])
        paths[str(row["path"])] = {
            "duration_us": int(row["duration_value"]),
            "operations": operations,
            "us_per_operation": round(int(row["duration_value"]) / operations, 3),
            "records": int(row["primary_value"]),
            # The Guest receipt stores one query's candidate count, not a
            # total accumulated across the timed repetitions.
            "records_per_query": int(row["primary_value"]),
            "rebuild_records": int(row["rebuild_records"]),
        }
    baseline = paths["traversal"]["us_per_operation"]
    optimized = paths["warm_index"]["us_per_operation"]
    comparison = {
        "baseline": "metadata_table_traversal",
        "treatment": "warm_metadata_index",
        "baseline_us_per_operation": baseline,
        "treatment_us_per_operation": optimized,
        "speedup": round(baseline / optimized, 2) if optimized > 0 else None,
        "improvement_percent": (
            round((baseline - optimized) * 100.0 / baseline, 1) if baseline > 0 else None
        ),
        "scope": "single_boot_internal_metadata_paths",
    }
    return receipt, {"paths": paths, "comparison": comparison}


def build_report(
    source_root: Path,
    functional_log: Path,
    benchmark_log: Path,
    lab_log: Path,
    run_id: str,
    commit: str,
    elapsed_seconds: float,
    artifacts: list[Path],
) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id) or int(run_id, 16) == 0:
        raise ContestDemoError("run id must be 16 nonzero lowercase hex digits")
    if not COMMIT.fullmatch(commit):
        raise ContestDemoError("commit must be a full Git object id")
    if elapsed_seconds <= 0:
        raise ContestDemoError("elapsed time must be positive")
    if clean_source_identity(source_root) != commit:
        raise ContestDemoError("source identity changed during the demo")
    functional_log = _regular_file(functional_log, "Task 1-5 Guest log")
    benchmark_log = _regular_file(benchmark_log, "benchmark Guest log")
    lab_log = _regular_file(lab_log, "Task 6 Guest log")
    functional = _verify_functional(functional_log)
    benchmark_receipt, performance = _verify_benchmark(benchmark_log, commit, run_id)
    task6 = _verify_labdemo(lab_log)
    artifact_paths = [functional_log, benchmark_log, lab_log]
    artifact_paths.extend(_regular_file(path, "demo artifact") for path in artifacts)
    if len({path.resolve() for path in artifact_paths}) != len(artifact_paths):
        raise ContestDemoError("demo artifact list contains duplicates")
    tasks = {
        task: {
            "label": TASK_LABELS[task],
            "status": "passed",
            "source": "agentfinal_ucore" if task != "task6" else "labdemo_ucore",
        }
        for task in TASK_LABELS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": "passed",
        "evidence_scope": "live_single_run_demo",
        "formal_claim": False,
        "run_id": run_id,
        "commit": commit,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "qemu_boots": 3,
        "tasks": tasks,
        "functional_receipt": functional,
        "benchmark_receipt": benchmark_receipt,
        "task6_receipt": task6,
        "performance": performance,
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in artifact_paths
        },
        "interpretation": (
            "本页来自本次三次真实 RISC-V QEMU 启动。任务覆盖是本提交 Guest 的"
            "自检回执；性能只比较同一 Guest 内的 metadata 全表遍历、冷索引和暖索引"
            "路径，是现场单次观测，不构成源码语义证明，也不替代正式多启动统计。"
        ),
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "不可用"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AgentOS 竞赛现场演示",
        "",
        f"- 状态：`{report['status']}`",
        f"- 提交：`{report['commit']}`",
        f"- 运行标识：`{report['run_id']}`",
        f"- 真实 QEMU 启动：{report['qemu_boots']} 次",
        f"- 总耗时：{report['elapsed_seconds']:.1f} 秒",
        "",
        "## 任务覆盖",
        "",
        "| 任务 | 动态路径 | 状态 |",
        "| --- | --- | --- |",
    ]
    for task, item in report["tasks"].items():
        lines.append(f"| {task.upper()} {item['label']} | `{item['source']}` | 通过 |")
    lines.extend(
        [
            "",
            "## 现场性能观测",
            "",
            "| 路径 | us/op | records/query | 重建记录 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    labels = {"traversal": "全表遍历", "cold_index": "冷索引", "warm_index": "暖索引"}
    for name in ("traversal", "cold_index", "warm_index"):
        row = report["performance"]["paths"][name]
        lines.append(
            f"| {labels[name]} | {_fmt(row['us_per_operation'], 3)} | "
            f"{row['records_per_query']} | {row['rebuild_records']} |"
        )
    comparison = report["performance"]["comparison"]
    lines.extend(
        [
            "",
            f"暖索引相对遍历的本轮速度比：`{_fmt(comparison['speedup'])}x`。",
            "",
            f"> {report['interpretation']}",
            "",
        ]
    )
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    task_cards = "".join(
        '<article class="task"><span>{task}</span><h3>{label}</h3><p>Guest 自检回执</p></article>'.format(
            task=html.escape(task.upper()), label=html.escape(item["label"])
        )
        for task, item in report["tasks"].items()
    )
    labels = {"traversal": "Metadata 全表遍历", "cold_index": "冷索引（含重建）", "warm_index": "暖索引"}
    metric_rows = "".join(
        "<tr><td><strong>{label}</strong><small>{name}</small></td>"
        "<td>{duration}</td><td>{records}</td><td>{rebuild}</td></tr>".format(
            label=labels[name],
            name=name,
            duration=_fmt(report["performance"]["paths"][name]["us_per_operation"], 3),
            records=report["performance"]["paths"][name]["records_per_query"],
            rebuild=report["performance"]["paths"][name]["rebuild_records"],
        )
        for name in ("traversal", "cold_index", "warm_index")
    )
    comparison = report["performance"]["comparison"]
    artifact_digest = report["artifacts"]["benchmark-qemu.log"]["sha256"]
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentOS 竞赛现场演示</title>
<style>
:root{{--ink:#172129;--muted:#5c6870;--line:#d9e0e4;--paper:#fff;--bg:#f3f6f7;--green:#197149;--cyan:#0a7180;--gold:#9a6500}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;letter-spacing:0}}
header{{background:#153238;color:#fff;padding:28px max(24px,calc((100% - 1180px)/2)) 24px;border-bottom:5px solid #35aa70}}header p{{margin:6px 0 0;color:#d7e7e8}}h1{{margin:0;font-size:38px;line-height:1.15}}
main{{max-width:1180px;margin:auto;padding:24px}}.status{{display:grid;grid-template-columns:1.4fr repeat(3,1fr);border:1px solid var(--line);background:var(--paper);border-radius:6px;overflow:hidden}}
.status>div{{padding:18px;border-right:1px solid var(--line)}}.status>div:last-child{{border:0}}.status b{{display:block;font-size:24px;color:var(--green)}}.status span,small{{display:block;color:var(--muted);font-size:13px;margin-top:4px}}
h2{{font-size:20px;margin:28px 0 12px}}.tasks{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.task{{background:var(--paper);border:1px solid var(--line);border-left:4px solid var(--green);border-radius:6px;padding:14px;min-height:104px}}
.task span{{font:700 12px ui-monospace,monospace;color:var(--cyan)}}.task h3{{font-size:15px;margin:8px 0 0}}.task p{{font-size:13px;color:var(--green);margin:8px 0 0}}
.table-wrap{{overflow-x:auto;background:var(--paper);border:1px solid var(--line);border-radius:6px}}table{{width:100%;border-collapse:collapse;min-width:700px}}th,td{{padding:12px 14px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}}th{{font-size:12px;color:var(--muted);background:#edf2f3}}th:first-child,td:first-child{{text-align:left}}tr:last-child td{{border:0}}
.note{{margin-top:14px;padding:14px 16px;border-left:4px solid var(--gold);background:#fff8e8;color:#5f4a1f}}.proof code{{display:block;overflow-wrap:anywhere;background:#e9eff1;padding:12px;border-radius:4px;font-size:12px}}footer{{padding:20px 0 8px;color:var(--muted);font-size:12px}}
@media(max-width:760px){{h1{{font-size:30px}}.status{{grid-template-columns:1fr 1fr}}.status>div{{border-bottom:1px solid var(--line)}}.tasks{{grid-template-columns:1fr}}main{{padding:16px}}}}
</style></head><body>
<header><h1>AgentOS 竞赛现场演示</h1><p>真实 RISC-V QEMU，离线验证任务一至六与关键优化路径</p></header><main>
<section class="status"><div><span>现场状态</span><b>Guest 自检通过</b><small>本提交的机制回执</small></div><div><span>QEMU 启动</span><b>{report['qemu_boots']}</b><small>隔离 Guest 语料</small></div><div><span>覆盖任务</span><b>6 / 6</b><small>必做与选做模块</small></div><div><span>现场耗时</span><b>{report['elapsed_seconds']:.1f}s</b><small>构建、运行、核验</small></div></section>
<h2>赛题任务 Guest 自检覆盖</h2><section class="tasks">{task_cards}</section>
<h2>真实计时对照</h2><div class="table-wrap"><table><thead><tr><th>路径</th><th>us/op</th><th>records/query</th><th>重建记录</th></tr></thead><tbody>{metric_rows}</tbody></table></div>
<p class="note">暖索引相对全表遍历：<strong>{_fmt(comparison['speedup'])}x</strong>。{html.escape(report['interpretation'])}</p>
<h2>可追溯身份</h2><section class="proof"><code>commit {html.escape(report['commit'])}<br>run {html.escape(report['run_id'])}<br>benchmark log sha256 {artifact_digest}</code></section>
<footer>页面由本轮已核验的 Guest 原始日志生成，不读取历史 results，也不访问云 API。</footer>
</main></body></html>"""


def publish(report: dict[str, Any], output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise ContestDemoError("output directory must not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        output_dir / "summary.json",
        (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _atomic_write(output_dir / "report.md", render_markdown(report).encode("utf-8"))
    _atomic_write(output_dir / "index.html", render_html(report).encode("utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("identity", help="print a clean committed source identity")
    identity.add_argument("--root", type=Path, required=True)
    render = commands.add_parser("render", help="verify Guest logs and render the report")
    render.add_argument("--source-root", type=Path, required=True)
    render.add_argument("--functional-log", type=Path, required=True)
    render.add_argument("--benchmark-log", type=Path, required=True)
    render.add_argument("--lab-log", type=Path, required=True)
    render.add_argument("--run-id", required=True)
    render.add_argument("--commit", required=True)
    render.add_argument("--elapsed-seconds", type=float, required=True)
    render.add_argument("--artifact", action="append", type=Path, default=[])
    render.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "identity":
            print(clean_source_identity(args.root))
            return 0
        report = build_report(
            args.source_root,
            args.functional_log,
            args.benchmark_log,
            args.lab_log,
            args.run_id,
            args.commit,
            args.elapsed_seconds,
            args.artifact,
        )
        publish(report, args.output_dir)
    except (ContestDemoError, OSError, UnicodeError, ValueError) as error:
        raise SystemExit(f"contest demo failed: {error}") from error
    print("[contest-demo] Task 1-6 Guest self-check receipts: passed")
    print(f"[contest-demo] QEMU boots=3 elapsed={report['elapsed_seconds']:.1f}s")
    comparison = report["performance"]["comparison"]
    print(
        "[contest-demo] metadata traversal={:.3f}us/op warm-index={:.3f}us/op speedup={}x".format(
            comparison["baseline_us_per_operation"],
            comparison["treatment_us_per_operation"],
            _fmt(comparison["speedup"]),
        )
    )
    print(f"[contest-demo] report: {(args.output_dir / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
