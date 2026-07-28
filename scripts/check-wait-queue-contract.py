#!/usr/bin/env python3
"""Fail closed when predicate waits bypass the interrupt-off contract."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OS = ROOT / "os"
WAIT = (OS / "wait.c").read_text(encoding="utf-8")
WAIT_H = (OS / "wait.h").read_text(encoding="utf-8")

IRQ_WAIT = re.compile(
    r"\bwait_queue_sleep(?:_key)?_irq(?:_uninterruptible)?\s*\("
)
PLAIN_WAIT = re.compile(r"\bwait_queue_sleep\s*\(")
FUNCTION = re.compile(
    r"(?m)^[A-Za-z_][\w\s\*]*\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"
)


def strip_comments_and_strings(source: str) -> str:
    return re.sub(
        r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        lambda match: " " * len(match.group(0)),
        source,
        flags=re.DOTALL,
    )


def function_ranges(source: str):
    clean = strip_comments_and_strings(source)
    for match in FUNCTION.finditer(clean):
        depth = 1
        cursor = match.end()
        while cursor < len(clean) and depth:
            if clean[cursor] == "{":
                depth += 1
            elif clean[cursor] == "}":
                depth -= 1
            cursor += 1
        if depth:
            raise ValueError(f"unterminated function {match.group(1)}")
        yield match.group(1), match.start(), cursor, clean[match.start():cursor]


for token in (
    "wait_queue_require_irq_disabled();",
    'panic("wait queue predicate unlocked")',
    "no shared predicate must stay atomic with queue publication",
):
    if token not in WAIT + WAIT_H:
        raise SystemExit(f"missing wait contract token: {token}")


def validate_wake_outcome(source: str) -> None:
    canceled = source.find("canceled = t->wait_interrupted;")
    exiting = source.find(
        "exit_requested = interruptible && proc_thread_exit_requested();",
        canceled,
    )
    canceled_result = source.find("return WAIT_QUEUE_INTERRUPTED;", exiting)
    woken_result = source.find("return WAIT_QUEUE_WOKEN_INTERRUPTED;", exiting)
    if min(canceled, exiting, canceled_result, woken_result) < 0 or not (
        canceled < exiting < canceled_result < woken_result
    ):
        raise ValueError("wait wake and cancellation outcomes are conflated")


validate_wake_outcome(WAIT)
try:
    validate_wake_outcome(
        WAIT.replace("return WAIT_QUEUE_WOKEN_INTERRUPTED;",
                     "return WAIT_QUEUE_INTERRUPTED;", 1)
    )
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: granted interruption was conflated")

for wrapper in (
    "wait_queue_sleep_irq",
    "wait_queue_sleep_key_irq",
    "wait_queue_sleep_irq_uninterruptible",
):
    body = next(
        (body for name, _start, _end, body in function_ranges(WAIT)
         if name == wrapper),
        "",
    )
    if "wait_queue_require_irq_disabled();" not in body:
        raise SystemExit(f"{wrapper} does not assert the caller contract")

violations = []
plain_calls = []
for path in sorted(OS.glob("*.c")):
    source = path.read_text(encoding="utf-8")
    clean = strip_comments_and_strings(source)
    for name, start, end, body in function_ranges(source):
        if path.name != "wait.c" and PLAIN_WAIT.search(body):
            plain_calls.append(f"{path.name}:{name}")
        if path.name == "wait.c" or not IRQ_WAIT.search(body):
            continue
        first_wait = IRQ_WAIT.search(body).start()
        if "intr_save()" not in body[:first_wait]:
            violations.append(f"{path.name}:{name}")

if plain_calls:
    raise SystemExit(
        "production predicate waits must use the explicit *_irq API: "
        + ", ".join(plain_calls)
    )
if violations:
    raise SystemExit(
        "*_irq wait lacks a local interrupt guard: " + ", ".join(violations)
    )

critical_order = {
    "sync.c": (
        ("semaphore_down", "s->count--;", "wait_queue_sleep_irq(&s->waiters)"),
        ("cond_wait", "mutex_unlock(m)", "wait_queue_sleep_irq(&cond->waiters)"),
    ),
    "proc.c": (
        ("wait", "proc_child_wait_result", "wait_queue_sleep_irq(&p->child_waiters)"),
        ("exit", "proc_siblings_quiescent", "wait_queue_sleep_irq(&p->thread_exit_waiters)"),
    ),
    "fs.c": (
        ("fs_claim_gate_lock", "fs_claim_owner == 0", "wait_queue_sleep_irq(&fs_claim_waiters)"),
    ),
    "agent_ipc.c": (
        ("sys_agent_wait", "agent_ipc_wait_reserve_locked", "wait_queue_sleep_key_irq("),
    ),
}

for filename, contracts in critical_order.items():
    source = (OS / filename).read_text(encoding="utf-8")
    bodies = {name: body for name, _start, _end, body in function_ranges(source)}
    for function, predicate, sleep in contracts:
        body = bodies.get(function, "")
        guard = body.find("intr_save()")
        predicate_pos = body.find(predicate)
        sleep_pos = body.find(sleep)
        if min(guard, predicate_pos, sleep_pos) < 0 or not (
            guard < predicate_pos < sleep_pos
        ):
            raise SystemExit(
                f"predicate publication order changed: {filename}:{function}"
            )


def validate_timeline_wait(source: str) -> None:
    bodies = {
        name: body for name, _start, _end, body in function_ranges(source)
    }
    enqueue = bodies.get("agent_timeline_wait_enqueue_atomic", "")
    scan = bodies.get("agent_timeline_wait_for_match", "")
    export_body = bodies.get("agent_timeline_export", "")
    if not enqueue or not scan or not export_body:
        raise ValueError("timeline wait owner definitions are missing")
    patterns = (
        r"\bintr_save\s*\(\s*\)",
        r"\bexpired\s*=\s*timeout_ticks\s*>=\s*0\s*&&\s*"
        r"now\s*-\s*start\s*>=\s*\(\s*uint64\s*\)\s*timeout_ticks",
        r"\bcurrent_epoch\s*=\s*agent_observe_scope_epoch\s*"
        r"\(\s*scope_id\s*\)",
        r"\bif\s*\(\s*current_epoch\s*!=\s*scan_epoch\s*\)",
        r"\bif\s*\(\s*expired\s*&&\s*\*deadline_rescan_used\s*\)",
        r"\*deadline_rescan_used\s*=\s*1\s*;",
        r"\bintr_restore\s*\(\s*enabled\s*\)\s*;\s*"
        r"return\s+AGENT_TIMELINE_WAIT_RETRY\s*;",
        r"\bif\s*\(\s*expired\s*\)\s*"
        r"goto\s+timeline_timeout\s*;",
        r"\bmemmove\s*\(\s*&state->filter\b",
        r"\bagent_observe_timeline_waiter_publish\s*\(\s*t\s*,\s*state\s*\)",
        r"\bwait_queue_sleep_key_irq\s*\(\s*&p->agent_timeline_waiters\s*,\s*"
        r"state->thread_generation\s*\)",
        r"\bagent_observe_timeline_waiter_unpublish\s*\(\s*t\s*,\s*state\s*\)",
        r"\bintr_restore\s*\(\s*enabled\s*\)",
        r"\btimeline_timeout\s*:\s*agent_timeline_wait_finish\s*"
        r"\(\s*p\s*,\s*t\s*,\s*state\s*\)",
        r"\bintr_restore\s*\(\s*enabled\s*\)\s*;\s*"
        r"return\s+AGENT_STATUS_TIMEOUT\s*;",
    )
    positions = []
    cursor = 0
    for pattern in patterns:
        match = re.search(pattern, enqueue[cursor:])
        if match is None:
            raise ValueError(f"timeline atomic contract is missing: {pattern}")
        cursor += match.start()
        positions.append(cursor)
        cursor += max(1, len(match.group(0)))
    if positions != sorted(positions):
        raise ValueError("timeline final recheck and queue publication reordered")
    if re.search(r"\bagent_timeline_export\s*\(", enqueue):
        raise ValueError("timeline export entered the interrupt-off window")
    if re.search(r"\bintr_save\s*\(", scan):
        raise ValueError("timeline full scan runs with interrupts disabled")
    export = re.search(
        r"\bagent_timeline_export\s*\(\s*p\s*,\s*filter\s*,\s*0\s*,\s*0\s*,\s*"
        r"&scan_epoch\s*\)",
        scan,
    )
    enqueue_call = re.search(r"\bagent_timeline_wait_enqueue_atomic\s*\(", scan)
    if None in (export, enqueue_call) or export.start() >= enqueue_call.start():
        raise ValueError("timeline scan epoch is not returned by the unlocked export")
    if re.search(r"\bscan_epoch\s*=\s*agent_observe_scope_epoch", scan):
        raise ValueError("timeline wait sampled epoch before query reservation")
    scan_window = re.search(
        r"for\s*\(\s*;\s*;\s*\)\s*\{\s*"
        r"candidate_epoch\s*=\s*scan_epoch_out\s*!=\s*0\s*\?\s*"
        r"agent_observe_scope_epoch\s*\(\s*agent_identity_proc_scope\s*\(\s*p\s*\)\s*\)",
        export_body,
    )
    if scan_window is None:
        raise ValueError("timeline export sampled epoch outside reservation loop")
    recount = export_body.find("context_visible =", scan_window.end())
    publish = export_body.find("*scan_epoch_out = candidate_epoch", recount)
    reserve = export_body.find("agent_observe_query_reserve_to", publish)
    snapshot_start = export_body.find(
        "span_id = p->agent_current_span_id", reserve
    )
    if min(recount, publish, reserve, snapshot_start) < 0 or not (
        scan_window.start() < recount < publish < reserve < snapshot_start
    ):
        raise ValueError("timeline export epoch does not cover its final scan")
    if not re.search(
        r"scan_epoch_out\s*==\s*0\s*&&\s*max\s*==\s*0", export_body
    ):
        raise ValueError("timeline fast count can bypass wait epoch capture")


timeline_source = (OS / "agent_observe_timeline.c").read_text(encoding="utf-8")
validate_timeline_wait(timeline_source)
for label, mutant in (
    (
        "final epoch recheck disabled",
        timeline_source.replace(
            "if (current_epoch != scan_epoch)",
            "if (0 && current_epoch != scan_epoch)",
            1,
        ),
    ),
    (
        "atomic wait downgraded",
        timeline_source.replace(
            "wait_queue_sleep_key_irq(\n\t\t&p->agent_timeline_waiters, "
            "state->thread_generation)",
            "wait_queue_sleep_irq(&p->agent_timeline_waiters)",
            1,
        ),
    ),
    (
        "deadline retry became unbounded",
        timeline_source.replace(
            "if (expired && *deadline_rescan_used)",
            "if (0 && expired && *deadline_rescan_used)",
            1,
        ),
    ),
    (
        "scan epoch output disconnected",
        timeline_source.replace(
            "agent_timeline_export(p, filter, 0, 0,\n"
            "\t\t\t\t\t       &scan_epoch)",
            "agent_timeline_export(p, filter, 0, 0, 0)",
            1,
        ),
    ),
    (
        "scan epoch sampled before reservation loop",
        timeline_source.replace(
            "for (;;) {\n"
            "\t\tcandidate_epoch = scan_epoch_out != 0 ?\n"
            "\t\t\tagent_observe_scope_epoch(agent_identity_proc_scope(p)) : 0;",
            "candidate_epoch = scan_epoch_out != 0 ?\n"
            "\t\tagent_observe_scope_epoch(agent_identity_proc_scope(p)) : 0;\n"
            "\tfor (;;) {",
            1,
        ),
    ),
):
    try:
        validate_timeline_wait(mutant)
    except ValueError:
        pass
    else:
        raise SystemExit(f"timeline wait mutation survived: {label}")

bio_source = (OS / "bio.c").read_text(encoding="utf-8")
bio_body = next(
    (body for name, _start, _end, body in function_ranges(bio_source)
     if name == "bget"),
    "",
)
if bio_body.count("return b;") != 2:
    raise SystemExit("bget return shape changed; audit its interrupt guard")
if len(re.findall(r"intr_restore\s*\(\s*enabled\s*\)\s*;\s*return b;",
                  bio_body)) != 2:
    raise SystemExit("bget can return with interrupts disabled")

print("[wait-contract] interrupt-off predicate publication passed")
