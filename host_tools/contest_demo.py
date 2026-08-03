#!/usr/bin/env python3
"""Verify and render the short, fully offline AgentOS contest demo."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from evaluation_contract import (
    FILE_QUERY_PATH_INDEX,
    MARKER_PREFIX,
    EvaluationError,
    _parse_marker,
    load_suite,
    validate_guest_log,
)
from agenteval_measurement_source_receipt import build_measurement_source_receipt
from agenteval_measurement_source_policy import _receipt_source_paths
from evidence_delivery_contract import (
    DeliveryContractError,
    SAFE_GIT_CONFIG_ARGUMENTS,
    controlled_git_environment,
    tracked_worktree_identity,
)
from committed_source_identity import committed_source_path_sample


SCHEMA_VERSION = 3
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
LAB_PATTERNS = {
    "startup": re.compile(r"^labdemo_ucore: startup_barrier ready=3 event_queued=1 released=3$"),
    "audit": re.compile(r"^labdemo_ucore: global_audit=1 records=([1-9][0-9]*) agents=3 context=1 event=1 sched=1 prefetch=1$"),
    "timeline": re.compile(r"^labdemo_ucore: unified_timeline records=([1-9][0-9]*) context=1 event=1 sched=1 prefetch=1$"),
    "provenance": re.compile(r"^labdemo_ucore: provenance_graph edges=([1-9][0-9]*) message=1 prefetch=1$"),
    "scenario": re.compile(r"^labdemo_ucore: passed$"),
    "parent": re.compile(r"^labdemo_ucore: parent passed$"),
}


def _load_guest_failure_classifier() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "guest_failure_classifier.py"
    spec = importlib.util.spec_from_file_location(
        "_agentos_contest_demo_guest_failure_classifier", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Guest failure classifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUEST_FAILURE_CLASSIFIER = _load_guest_failure_classifier()


class ContestDemoError(RuntimeError):
    """The live evidence cannot be proved from its source and Guest logs."""


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *SAFE_GIT_CONFIG_ARGUMENTS, "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        env=controlled_git_environment(),
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
    try:
        tracked_clean, _tracked_digest = tracked_worktree_identity("git", root)
    except DeliveryContractError as error:
        raise ContestDemoError(f"tracked source identity is unsafe: {error}") from error
    if not tracked_clean:
        raise ContestDemoError("tracked source bytes differ from HEAD; worktree is dirty")
    dirty = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise ContestDemoError("source worktree is dirty; commit before running contest-demo")
    if _run_git(root, "rev-parse", "--verify", "HEAD") != commit:
        raise ContestDemoError("source commit changed during identity verification")
    return commit


def _measurement_source_sample(
    root: Path, commit: str, *, snapshot_root: Path | None = None
) -> tuple[tuple[str, int, str, str], ...]:
    try:
        return committed_source_path_sample(
            "git", root, commit, tuple(_receipt_source_paths()),
            snapshot_root=snapshot_root,
        )
    except DeliveryContractError as error:
        raise ContestDemoError(
            f"measurement source is not bound to commit: {error}"
        ) from error


def _require_measurement_receipt_sample(
    receipt: object, sample: tuple[tuple[str, int, str, str], ...]
) -> None:
    expected = [
        {"path": path, "bytes": size, "sha256": sha256}
        for path, size, sha256, _oid in sample
    ]
    if not isinstance(receipt, dict) or receipt.get("sources") != expected:
        raise ContestDemoError(
            "measurement source receipt differs from committed source sample"
        )


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
    for line in lines:
        failure = _GUEST_FAILURE_CLASSIFIER.classify_output_line(
            line, phase=_GUEST_FAILURE_CLASSIFIER.PHASE_GUEST
        )
        if failure is not None:
            raise ContestDemoError(f"{label} contains Guest failure: {failure}")
    return lines


def _unique_match(lines: list[str], pattern: re.Pattern[str], label: str) -> re.Match[str]:
    found = [match for line in lines if (match := pattern.fullmatch(line))]
    if len(found) != 1:
        raise ContestDemoError(f"{label} must occur exactly once")
    return found[0]


def _verify_evaluation(
    path: Path, suite_path: Path, challenge: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the canonical evaluation parser for Task1-5 and Task4 timing."""
    lines = _read_guest(path, "Task 1-5 evaluation Guest log")
    try:
        suite = load_suite(suite_path)
        receipt = validate_guest_log(path, suite, challenge)
    except EvaluationError as error:
        raise ContestDemoError(f"Task 1-5 evaluation failed: {error}") from error

    experiment = next(
        item for item in suite["experiments"] if item["id"] == FILE_QUERY_PATH_INDEX
    )
    samples: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for line_number, line in enumerate(lines, 1):
        if not line.startswith(MARKER_PREFIX):
            continue
        try:
            marker = _parse_marker(line, line_number)
        except EvaluationError as error:
            raise ContestDemoError(f"Task 4 sample parse failed: {error}") from error
        if marker["experiment"] != FILE_QUERY_PATH_INDEX:
            continue
        role = next(
            (
                candidate
                for candidate in ("baseline", "treatment")
                if marker["variant"] == experiment[candidate]["id"]
            ),
            None,
        )
        if role is None:
            raise ContestDemoError("Task 4 path-index sample has an unknown variant")
        samples.setdefault(marker["load"], {"baseline": [], "treatment": []})[
            role
        ].append(marker)

    expected_loads = experiment["loads"]
    if sorted(samples) != sorted(expected_loads):
        raise ContestDemoError("Task 4 path-index samples are incomplete")
    loads: dict[str, Any] = {}
    minimum_pairs = suite["pairing"]["minimum_inner_pairs"]
    for load in expected_loads:
        roles = samples[load]
        if any(len(roles[role]) != minimum_pairs for role in roles):
            raise ContestDemoError("Task 4 path-index inner pairs are incomplete")
        baseline = statistics.median(
            row["duration_us"] / row["operations"] for row in roles["baseline"]
        )
        treatment = statistics.median(
            row["duration_us"] / row["operations"] for row in roles["treatment"]
        )
        loads[str(load)] = {
            "baseline_us_per_query": round(baseline, 3),
            "treatment_us_per_query": round(treatment, 3),
            "baseline_records_per_query": round(
                statistics.median(
                    row["records_examined"] / row["operations"]
                    for row in roles["baseline"]
                ),
                3,
            ),
            "treatment_records_per_query": round(
                statistics.median(
                    row["records_examined"] / row["operations"]
                    for row in roles["treatment"]
                ),
                3,
            ),
            "speedup": round(baseline / treatment, 2) if treatment > 0 else None,
            "inner_pairs": minimum_pairs,
        }
    headline_load = max(expected_loads)
    headline = loads[str(headline_load)]
    return receipt, {
        "experiment": FILE_QUERY_PATH_INDEX,
        "design": "single_boot_challenge_bound_ab_ba",
        "loads": loads,
        "comparison": {
            "load": headline_load,
            "baseline": experiment["baseline"]["id"],
            "treatment": experiment["treatment"]["id"],
            "baseline_us_per_query": headline["baseline_us_per_query"],
            "treatment_us_per_query": headline["treatment_us_per_query"],
            "speedup": headline["speedup"],
            "scope": "single_boot_path_index_observation",
        },
    }


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


def build_report(
    source_root: Path,
    evaluation_log: Path,
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
    with tempfile.TemporaryDirectory(prefix="agentos-committed-source-") as temporary:
        snapshot_root = Path(temporary)
        source_sample_before = _measurement_source_sample(
            source_root, commit, snapshot_root=snapshot_root
        )
        try:
            source_receipt = build_measurement_source_receipt(
                snapshot_root, source_commit=commit
            )
        except ValueError as error:
            raise ContestDemoError(
                f"evaluation source contract failed: {error}"
            ) from error
    source_sample_after = _measurement_source_sample(source_root, commit)
    if source_sample_after != source_sample_before:
        raise ContestDemoError(
            "measurement source changed between committed source samples"
        )
    _require_measurement_receipt_sample(source_receipt, source_sample_after)
    if clean_source_identity(source_root) != commit:
        raise ContestDemoError("source identity changed while validating source contracts")
    evaluation_log = _regular_file(evaluation_log, "Task 1-5 evaluation Guest log")
    lab_log = _regular_file(lab_log, "Task 6 Guest log")
    functional, performance = _verify_evaluation(
        evaluation_log, source_root / "ci" / "evaluation-suite.json", run_id
    )
    task6 = _verify_labdemo(lab_log)
    artifact_paths = [evaluation_log, lab_log]
    artifact_paths.extend(_regular_file(path, "demo artifact") for path in artifacts)
    if len({path.resolve() for path in artifact_paths}) != len(artifact_paths):
        raise ContestDemoError("demo artifact list contains duplicates")
    tasks = {
        task: {
            "label": TASK_LABELS[task],
            "status": "passed",
            "source": "agenteval_ucore",
            "evidence_scope": "challenge_bound_functional_receipt",
        }
        for task in tuple(TASK_LABELS)[:5]
    }
    tasks["task6"] = {
        "label": TASK_LABELS["task6"],
        "status": "partial",
        "source": "labdemo_ucore",
        "evidence_scope": "short_labdemo_scenario",
        "formal_scenario_status": "unavailable",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": "partial",
        "evidence_scope": "live_single_run_demo",
        "formal_claim": False,
        "run_id": run_id,
        "commit": commit,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "qemu_boots": 2,
        "tasks": tasks,
        "functional_receipt": functional,
        "measurement_source_receipt": source_receipt,
        "task6_receipt": task6,
        "performance": performance,
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in artifact_paths
        },
        "interpretation": (
            "本页来自本次两次真实 RISC-V QEMU 启动。任务一至五由现有挑战绑定"
            "agenteval 合同复验；性能是题面 N 路径遍历与索引查询的单启动观测，"
            "不替代正式多启动统计。任务六仅运行短版 labdemo，完整 rp_* 科研平台"
            "场景未纳入本入口，明确记为 unavailable。"
        ),
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "不可用"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    status_labels = {"passed": "通过", "partial": "短场景通过；完整场景不可用"}
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
        lines.append(
            f"| {task.upper()} {item['label']} | `{item['source']}` | "
            f"{status_labels.get(item['status'], '不可用')} |"
        )
    lines.extend(
        [
            "",
            "## 现场性能观测 (`file_query_path_index`)",
            "",
            "| 文件数 | N 路径遍历 us/query | 索引 us/query | 速度比 | 遍历/索引 records/query |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for load, row in report["performance"]["loads"].items():
        lines.append(
            f"| {load} | {_fmt(row['baseline_us_per_query'], 3)} | "
            f"{_fmt(row['treatment_us_per_query'], 3)} | {_fmt(row['speedup'])}x | "
            f"{_fmt(row['baseline_records_per_query'], 1)} / "
            f"{_fmt(row['treatment_records_per_query'], 1)} |"
        )
    comparison = report["performance"]["comparison"]
    lines.extend(
        [
            "",
            f"在 N={comparison['load']} 时，索引相对 N 路径遍历的本轮速度比："
            f"`{_fmt(comparison['speedup'])}x`。",
            "",
            f"> {report['interpretation']}",
            "",
        ]
    )
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    status_labels = {
        "passed": "挑战绑定合同通过",
        "partial": "短场景通过；完整 rp_* unavailable",
    }
    task_cards = "".join(
        '<article class="task {status}"><span>{task}</span><h3>{label}</h3><p>{detail}</p></article>'.format(
            status=html.escape(item["status"]),
            task=html.escape(task.upper()),
            label=html.escape(item["label"]),
            detail=html.escape(status_labels.get(item["status"], "unavailable")),
        )
        for task, item in report["tasks"].items()
    )
    metric_rows = "".join(
        "<tr><td><strong>{load}</strong></td><td>{baseline}</td><td>{treatment}</td>"
        "<td>{speedup}x</td><td>{records}</td></tr>".format(
            load=load,
            baseline=_fmt(row["baseline_us_per_query"], 3),
            treatment=_fmt(row["treatment_us_per_query"], 3),
            speedup=_fmt(row["speedup"]),
            records=(
                f"{_fmt(row['baseline_records_per_query'], 1)} / "
                f"{_fmt(row['treatment_records_per_query'], 1)}"
            ),
        )
        for load, row in report["performance"]["loads"].items()
    )
    comparison = report["performance"]["comparison"]
    artifact_digest = report["artifacts"]["evaluation-qemu.log"]["sha256"]
    passed_count = sum(
        item["status"] == "passed" for item in report["tasks"].values()
    )
    partial_count = sum(
        item["status"] == "partial" for item in report["tasks"].values()
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentOS 竞赛现场演示</title>
<style>
:root{{--ink:#172129;--muted:#5c6870;--line:#d9e0e4;--paper:#fff;--bg:#f3f6f7;--green:#197149;--cyan:#0a7180;--gold:#9a6500}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;letter-spacing:0}}
header{{background:#153238;color:#fff;padding:28px max(24px,calc((100% - 1180px)/2)) 24px;border-bottom:5px solid #35aa70}}header p{{margin:6px 0 0;color:#d7e7e8}}h1{{margin:0;font-size:38px;line-height:1.15}}
main{{max-width:1180px;margin:auto;padding:24px}}.status{{display:grid;grid-template-columns:1.4fr repeat(3,1fr);border:1px solid var(--line);background:var(--paper);border-radius:6px;overflow:hidden}}
.status>div{{padding:18px;border-right:1px solid var(--line)}}.status>div:last-child{{border:0}}.status b{{display:block;font-size:24px;color:var(--green)}}.status span,small{{display:block;color:var(--muted);font-size:13px;margin-top:4px}}
h2{{font-size:20px;margin:28px 0 12px}}.tasks{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.task{{background:var(--paper);border:1px solid var(--line);border-left:4px solid var(--green);border-radius:6px;padding:14px;min-height:104px}}.task.partial{{border-left-color:var(--gold)}}
.task span{{font:700 12px ui-monospace,monospace;color:var(--cyan)}}.task h3{{font-size:15px;margin:8px 0 0}}.task p{{font-size:13px;color:var(--green);margin:8px 0 0}}.task.partial p{{color:var(--gold)}}
.table-wrap{{overflow-x:auto;background:var(--paper);border:1px solid var(--line);border-radius:6px}}table{{width:100%;border-collapse:collapse;min-width:700px}}th,td{{padding:12px 14px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}}th{{font-size:12px;color:var(--muted);background:#edf2f3}}th:first-child,td:first-child{{text-align:left}}tr:last-child td{{border:0}}
.note{{margin-top:14px;padding:14px 16px;border-left:4px solid var(--gold);background:#fff8e8;color:#5f4a1f}}.proof code{{display:block;overflow-wrap:anywhere;background:#e9eff1;padding:12px;border-radius:4px;font-size:12px}}footer{{padding:20px 0 8px;color:var(--muted);font-size:12px}}
@media(max-width:760px){{h1{{font-size:30px}}.status{{grid-template-columns:1fr 1fr}}.status>div{{border-bottom:1px solid var(--line)}}.tasks{{grid-template-columns:1fr}}main{{padding:16px}}}}
</style></head><body>
<header><h1>AgentOS 竞赛现场演示</h1><p>真实 RISC-V QEMU，挑战绑定复验任务一至五与任务六短场景</p></header><main>
<section class="status"><div><span>现场状态</span><b>部分就绪</b><small>完整 rp_* 场景未纳入</small></div><div><span>QEMU 启动</span><b>{report['qemu_boots']}</b><small>隔离 Guest 语料</small></div><div><span>动态覆盖</span><b>{passed_count} 完整</b><small>{partial_count} 项短场景</small></div><div><span>现场耗时</span><b>{report['elapsed_seconds']:.1f}s</b><small>构建、运行、核验</small></div></section>
<h2>赛题任务 Guest 自检覆盖</h2><section class="tasks">{task_cards}</section>
<h2>题面路径查询现场对照</h2><small>file_query_path_index · challenge-bound AB/BA</small><div class="table-wrap"><table><thead><tr><th>文件数 N</th><th>N 路径遍历 us/query</th><th>索引 us/query</th><th>速度比</th><th>遍历/索引 records/query</th></tr></thead><tbody>{metric_rows}</tbody></table></div>
<p class="note">N={comparison['load']} 时索引相对 N 路径遍历：<strong>{_fmt(comparison['speedup'])}x</strong>。{html.escape(report['interpretation'])}</p>
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
    render.add_argument("--evaluation-log", type=Path, required=True)
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
            args.evaluation_log,
            args.lab_log,
            args.run_id,
            args.commit,
            args.elapsed_seconds,
            args.artifact,
        )
        publish(report, args.output_dir)
    except (ContestDemoError, OSError, UnicodeError, ValueError) as error:
        raise SystemExit(f"contest demo failed: {error}") from error
    print("[contest-demo] Task 1-5 canonical receipts: passed")
    print("[contest-demo] Task 6 short scenario: passed; formal rp_*: unavailable")
    print(f"[contest-demo] QEMU boots=2 elapsed={report['elapsed_seconds']:.1f}s")
    comparison = report["performance"]["comparison"]
    print(
        "[contest-demo] path-walk={:.3f}us/query index={:.3f}us/query speedup={}x".format(
            comparison["baseline_us_per_query"],
            comparison["treatment_us_per_query"],
            _fmt(comparison["speedup"]),
        )
    )
    print(f"[contest-demo] report: {(args.output_dir / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
