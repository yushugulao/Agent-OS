#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
driver = (root / "os/virtio_disk.c").read_text(encoding="utf-8")
syscall = (root / "os/syscall.c").read_text(encoding="utf-8")
test_abi = (root / "virtio_test_abi.h").read_text(encoding="utf-8")
test_program = (root / "user/src/virtiodisk_ucore.c").read_text(encoding="utf-8")
filesystem = (root / "os/fs.c").read_text(encoding="utf-8")
main = (root / "os/main.c").read_text(encoding="utf-8")
trap = (root / "os/trap.c").read_text(encoding="utf-8")
runner = (root / "scripts/run-virtio-disk-tests.sh").read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def matching_delimiter(text, start, opener, closer):
    if start < 0 or start >= len(text) or text[start] != opener:
        raise ContractError(f"missing opening delimiter {opener!r}")
    depth = 0
    state = "code"
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state == "quoted":
            if char == "\\":
                index += 1
            elif char == quote:
                state = "code"
        elif char == "/" and following == "/":
            state = "line-comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            index += 1
        elif char in ('"', "'"):
            state = "quoted"
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ContractError(f"unterminated delimiter {opener!r}")


def function_region(source, signature):
    signature_start = source.find(signature)
    if signature_start < 0:
        raise ContractError(f"missing function {signature}")
    body_open = source.find("{", signature_start + len(signature))
    body_close = matching_delimiter(source, body_open, "{", "}")
    return body_open + 1, body_close, source[body_open + 1:body_close]


def split_call_arguments(arguments):
    spans = []
    start = 0
    parens = brackets = braces = 0
    state = "code"
    quote = ""
    index = 0
    while index < len(arguments):
        char = arguments[index]
        following = arguments[index + 1] if index + 1 < len(arguments) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state == "quoted":
            if char == "\\":
                index += 1
            elif char == quote:
                state = "code"
        elif char == "/" and following == "/":
            state = "line-comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            index += 1
        elif char in ('"', "'"):
            state = "quoted"
            quote = char
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
        elif char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif char == "," and parens == brackets == braces == 0:
            spans.append((start, index))
            start = index + 1
        index += 1
    spans.append((start, len(arguments)))
    return spans


def function_calls(text, name):
    calls = []
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", text):
        opening = text.find("(", match.start())
        closing = matching_delimiter(text, opening, "(", ")")
        argument_text = text[opening + 1:closing]
        argument_spans = split_call_arguments(argument_text)
        calls.append({
            "start": match.start(),
            "opening": opening,
            "closing": closing,
            "arguments": [
                (opening + 1 + begin, opening + 1 + end,
                 argument_text[begin:end].strip())
                for begin, end in argument_spans
            ],
        })
    return calls


def preprocessor_branches(body, macro):
    opening = re.search(
        rf"(?m)^[ \t]*#ifdef[ \t]+{re.escape(macro)}[ \t]*$", body
    )
    if opening is None:
        raise ContractError(f"missing profile branch {macro}")
    directive = re.compile(
        r"(?m)^[ \t]*#[ \t]*(if|ifdef|ifndef|else|endif)\b[^\n]*$"
    )
    depth = 1
    alternate = None
    for match in directive.finditer(body, opening.end()):
        kind = match.group(1)
        if kind in ("if", "ifdef", "ifndef"):
            depth += 1
        elif kind == "endif":
            depth -= 1
            if depth == 0:
                if alternate is None:
                    raise ContractError(f"profile branch {macro} has no #else")
                return (
                    opening.end(), alternate.start(),
                    body[opening.end():alternate.start()],
                    alternate.end(), match.start(),
                    body[alternate.end():match.start()],
                )
        elif kind == "else" and depth == 1:
            if alternate is not None:
                raise ContractError(f"profile branch {macro} has two #else arms")
            alternate = match
    raise ContractError(f"unterminated profile branch {macro}")


def submission_accounting_guard(body, transfer_start):
    candidates = list(re.finditer(r"\bif\s*\(", body[:transfer_start]))
    for candidate in reversed(candidates):
        opening = body.find("(", candidate.start())
        closing = matching_delimiter(body, opening, "(", ")")
        between = body[closing + 1:transfer_start].strip()
        if not between:
            return opening + 1, closing, body[opening + 1:closing]
        if between == "{":
            block_close = matching_delimiter(body, closing + 1 +
                                             body[closing + 1:].find("{"),
                                             "{", "}")
            if block_close > transfer_start:
                return opening + 1, closing, body[opening + 1:closing]
    raise ContractError("physical I/O accounting is not controlled by an if guard")


def validate_submission_accounting(source):
    body_start, _, body = function_region(source, "static int disk_submit(")
    transfers = function_calls(body, "bio_account_transfer")
    if len(transfers) != 1:
        raise ContractError(
            f"disk_submit must have one accounting publication, got {len(transfers)}"
        )
    transfer = transfers[0]
    condition_start, condition_end, condition = submission_accounting_guard(
        body, transfer["start"]
    )
    condition_tokens = re.findall(r"[A-Za-z_]\w*|&&|\|\||!|[()]", condition)
    if re.sub(r"\s+", "", condition) != "".join(condition_tokens):
        raise ContractError("disk_submit accounting guard is not a boolean expression")
    while (len(condition_tokens) >= 2 and condition_tokens[0] == "(" and
           condition_tokens[-1] == ")"):
        condition_tokens = condition_tokens[1:-1]
    if condition_tokens != ["submitted"]:
        raise ContractError(
            "disk_submit accounting must require a real device submission"
        )
    out_label = re.search(r"(?m)^[ \t]*out:[ \t]*$", body)
    if out_label is None:
        raise ContractError("disk_submit has no common result tail")
    restore = body.find("intr_restore(enabled);", out_label.end())
    returned = body.find("return status;", transfer["closing"])
    notify = body.find("*R(VIRTIO_MMIO_QUEUE_NOTIFY) = 0;")
    submit_publish = body.find("submitted = 1;", notify)
    if (min(restore, returned, notify, submit_publish) < 0 or not (
            notify < submit_publish < out_label.start() < restore <
            transfer["start"] < returned)):
        raise ContractError("disk_submit accounting is outside the submit/result tail")
    return body_start + condition_start, body_start + condition_end


def validate_overlay_accounting(source):
    _, _, body = function_region(source, "int virtio_disk_rw(")
    (overlay_start, _, overlay, production_start, _, production) = \
        preprocessor_branches(body, "DURABILITY_POWERCUT_TEST_PROFILE")
    overlay_transfers = function_calls(overlay, "bio_account_transfer")
    overlay_submits = function_calls(overlay, "disk_submit")
    overlay_returns = list(re.finditer(r"\breturn\s+result\s*;", overlay))
    if (overlay_transfers or not overlay_submits or len(overlay_returns) != 1):
        raise ContractError(
            "volatile overlay I/O is incorrectly counted as physical I/O"
        )
    production_transfers = function_calls(production, "bio_account_transfer")
    production_submits = function_calls(production, "disk_submit")
    if production_transfers or len(production_submits) != 1:
        raise ContractError(
            "production read/write must delegate exactly one charge to disk_submit"
        )
    signature_start = source.find("static int disk_submit(")
    signature_end = source.find("{", signature_start)
    if (signature_start < 0 or signature_end < 0 or
            "account_transfer" in source[signature_start:signature_end]):
        raise ContractError("physical submission still exposes an accounting bypass")


def validate_barrier_accounting(source):
    _, _, body = function_region(source, "static int disk_durability_barrier(")
    _, _, overlay, _, _, production = preprocessor_branches(
        body, "DURABILITY_POWERCUT_TEST_PROFILE"
    )
    if function_calls(overlay, "bio_account_transfer"):
        raise ContractError("overlay barrier aggregates physical submissions")
    if len(function_calls(overlay, "disk_submit")) != 2:
        raise ContractError("overlay commit does not expose write and flush submissions")
    if (function_calls(production, "bio_account_transfer") or
            len(function_calls(production, "disk_submit")) != 1):
        raise ContractError("production barrier bypasses submission accounting")


def validate_range_execution(source):
    body_start, _, body = function_region(source, "int main(")
    ordered_names = (
        "test_status_errors",
        "test_range_rejection",
        "test_flush_accounting",
    )
    calls = []
    for name in ordered_names:
        matches = function_calls(body, name)
        if len(matches) != 1:
            raise ContractError(f"main must execute {name} exactly once")
        calls.append(matches[0])
    if [call["start"] for call in calls] != sorted(
            call["start"] for call in calls):
        raise ContractError(
            "range rejection must execute between status and flush tests"
        )
    range_call = calls[1]
    end = range_call["closing"] + 1
    while end < len(body) and body[end].isspace() and body[end] != "\n":
        end += 1
    if end >= len(body) or body[end] != ";":
        raise ContractError("range rejection invocation is not a statement")
    return body_start + range_call["start"], body_start + end + 1


def validate_event_driven_queue_checks(source):
    _, _, full_ring = function_region(
        source, "static void test_full_ring_reclaim("
    )
    if re.search(r"\bsleep\s*\(", full_ring):
        raise ContractError("full-ring ordering relies on a wall-time sleep")
    for token in (
        "value.completions > 2",
        "value.completions == 2 && value.descriptor_reclaims >= 2",
        "full_ring_done[0] && full_ring_done[3]",
        "full_ring_done[1] || full_ring_done[2]",
    ):
        if token not in full_ring:
            raise ContractError(f"full-ring event oracle missing {token}")
    loop_end = full_ring.find("check(full_ring_done[0]")
    refresh = full_ring.rfind("value = stats();", 0, loop_end)
    if loop_end < 0 or refresh < 0:
        raise ContractError("full-ring oracle uses a stale completion snapshot")

    _, _, used_ring = function_region(
        source, "static void test_used_ring_validation("
    )
    if re.search(r"\bsleep\s*\(", used_ring):
        raise ContractError("used-ring recovery relies on a wall-time sleep")
    for token in (
        "if (value.reset_recoveries == 1)",
        "get_mtime() < recovery_deadline",
        "sched_yield();",
    ):
        if token not in used_ring:
            raise ContractError(f"used-ring recovery oracle missing {token}")


def expect_accounting_rejected(source, label):
    try:
        validate_submission_accounting(source)
        validate_overlay_accounting(source)
    except ContractError:
        return
    raise SystemExit(f"accounting mutation survived: {label}")


def expect_range_execution_rejected(source):
    try:
        validate_range_execution(source)
    except ContractError:
        return
    raise SystemExit("range execution mutation survived: real call removed")

required_driver = (
    "wait_queue_sleep_irq_uninterruptible(&disk.desc_waiters)",
    "VIRTIO_DISK_TEST_STALL_COMPLETION",
    "disk.test_stats.timer_recoveries++",
    "virtio_disk_durability_barrier",
    "free_chain(id)",
    "VIRTIO_DISK_RESETTING",
    "disk.bounce[disk.info[id].bank][id]",
    "disk.info[id].generation != disk.queue_generation",
    "disk_device_start(1)",
    "status = VIRTIO_DISK_ERR_RANGE;",
    "disk.test_stats.rejected_requests++;",
    "disk.test_stats.range_rejections++;",
    "pending > NUM || pending > outstanding",
    "processed < pending &&",
    "processed < NUM",
    "disk.info[id].completed || disk.info[id].device_done",
    "static void disk_release_request(int id)",
    "disk.info[id].active = 0;",
    "disk.test_stats.descriptor_reclaims++",
)
for token in required_driver:
    if token not in driver:
        raise SystemExit(f"missing driver mechanism: {token}")
if 'panic("virtio_disk_intr status")' in driver:
    raise SystemExit("device status still panics")

submit = driver.find("static int disk_submit(")
range_check = driver.find("status = VIRTIO_DISK_ERR_RANGE;", submit)
descriptor_allocation = driver.find("alloc_desc_chain(idx, count)", submit)
queue_notify = driver.find("*R(VIRTIO_MMIO_QUEUE_NOTIFY) = 0;", submit)
if min(submit, range_check, descriptor_allocation, queue_notify) < 0 or not (
    submit < range_check < descriptor_allocation < queue_notify
):
    raise SystemExit("capacity range rejection is not a pre-submit decision")
account_transfer = driver.find("bio_account_transfer(owner, io_class", submit)
try:
    guard_start, guard_end = validate_submission_accounting(driver)
    validate_overlay_accounting(driver)
    validate_barrier_accounting(driver)
except ContractError as error:
    raise SystemExit(f"invalid physical I/O accounting contract: {error}") from error
if account_transfer < 0:
    raise SystemExit("missing physical I/O accounting publication")
unsupported = driver.find("static int disk_durability_barrier(")
unsupported_return = driver.find("return VIRTIO_DISK_ERR_UNSUPPORTED;", unsupported)
if "bio_account_transfer" in driver[unsupported:unsupported_return]:
    raise SystemExit("unsupported pre-submit flush is charged as physical I/O")
for token in (
    "VIRTIO_TEST_ABI_VERSION 4U",
    "VIRTIO_TEST_READ_RANGE = 5",
    "VIRTIO_TEST_REJECTED_RANGE = -6",
    "rejected_requests",
    "range_rejections",
    "VIRTIO_TEST_FORGE_USED_INDEX",
    "VIRTIO_TEST_DUPLICATE_USED",
    "VIRTIO_TEST_FULL_RING_RECLAIM",
    "used_budget_resets",
    "invalid_used_entries",
    "descriptor_reclaims",
):
    if token not in test_abi:
        raise SystemExit(f"missing range provenance ABI: {token}")
range_case = syscall.find("case VIRTIO_TEST_READ_RANGE:")
next_case = syscall.find("case ", range_case + 1)
if range_case < 0 or next_case < 0:
    raise SystemExit("missing isolated range test command")
range_case_body = syscall[range_case:next_case]
for token in (
    "(arg0 | arg1 | arg2 | arg3 | arg4) == 0",
    "virtio_disk_test_read_range()",
):
    if token not in range_case_body:
        raise SystemExit(f"missing strict range test command: {token}")
for token in (
    "result == VIRTIO_TEST_REJECTED_RANGE",
    "value.rejected_requests == 1",
    "value.range_rejections == 1",
    "value.submits == 0",
    "value.io_errors == 0",
    "range-rejection passed",
):
    if token not in test_program:
        raise SystemExit(f"missing dynamic range provenance assertion: {token}")
unsupported_provenance = (
    "value.unsupported_errors == 1 && value.rejected_requests == 1"
)
if unsupported_provenance not in test_program:
    raise SystemExit("pre-submit unsupported result lacks rejection provenance")
try:
    range_call_start, range_call_end = validate_range_execution(test_program)
    validate_event_driven_queue_checks(test_program)
except ContractError as error:
    raise SystemExit(f"invalid dynamic queue execution: {error}") from error
for token in (
    "full-ring-reclaim passed",
    "forged-used-index passed",
    "duplicate-used passed",
    "value.max_used_batch <= 8",
    "value.descriptor_reclaims >= FULL_RING_THREADS",
):
    if token not in test_program:
        raise SystemExit(f"missing bounded queue dynamic assertion: {token}")

release = driver.find("static void disk_release_request(int id)")
release_end = driver.find("\n}\n", release)
release_body = driver[release:release_end]
active_clear = release_body.find("disk.info[id].active = 0;")
wake = release_body.find("wait_queue_wake_all(&disk.desc_waiters);")
if min(release, release_end, active_clear, wake) < 0 or active_clear > wake:
    raise SystemExit("descriptor waiters are exposed before head deactivation")

case = syscall.find("case SYS_virtio_disk_test:")
guard = syscall.rfind("#ifdef VIRTIO_DISK_TEST_PROFILE", 0, case)
previous_end = syscall.rfind("#endif", 0, case)
end = syscall.find("#endif", case)
if case < 0 or guard <= previous_end or end < case:
    raise SystemExit("test syscall is not compile-time guarded")
for token in (
    "virtio_disk_test_authorized(curr_proc())",
    "VIRTIO_DISK_TEST must be exactly 1 when enabled",
    "-DVIRTIO_DISK_TEST_PROFILE",
    "VIRTIO_DISK_TEST_INIT_NAME",
):
    if token not in syscall and token not in (root / "Makefile").read_text(
        encoding="utf-8"
    ):
        raise SystemExit(f"missing test control boundary: {token}")
if "source \"${SCRIPT_DIR}/evidence-wiring.sh\"" not in runner or \
        "evidence_append_guest_log" not in runner or \
        "scripts/test-validate-virtio-disk-log.py" not in runner:
    raise SystemExit("runner is not wired into final evidence")

boot_listing = main.find("show_all_files();")
runtime_start = main.find("virtio_disk_runtime_start();")
scheduler_start = main.find("scheduler();")
if min(boot_listing, runtime_start, scheduler_start) < 0 or not (
    boot_listing < runtime_start < scheduler_start
):
    raise SystemExit("runtime disk mode starts before boot-only filesystem I/O ends")

for token in (
    "FS_IO_UNAVAILABLE",
    "FS_FAILURE_SCHEDULING_UNAVAILABLE",
    "FS_FAILURE_METADATA_WRITE_INDETERMINATE",
    "fs_write_metadata_block",
    "fs_write_data_block",
    "if (result == VIRTIO_DISK_ERR_BUSY)",
    "result == VIRTIO_DISK_ERR_RANGE",
    "return VIRTIO_DISK_ERR_BUSY;",
    "ip->type == T_DIR ?",
):
    if token not in filesystem:
        raise SystemExit(f"missing filesystem I/O health contract: {token}")
if "fs_io_indeterminate" in filesystem:
    raise SystemExit("legacy one-bit filesystem poison state restored")
if filesystem.count("fs_io_health = FS_IO_INDETERMINATE;") != 1:
    raise SystemExit("filesystem poison must have one classified transition")
if trap.count("agent_background_checkpoint();") != 1:
    raise SystemExit("user interrupt path lacks one bounded background checkpoint")

expect_accounting_rejected(
    driver[:guard_start] + "1" + driver[guard_end:],
    "submitted predicate removed",
)
for old, new, label in (
    (
        "\tif (overlay_acquired)\n\t\tdisk_durability_overlay_leave();\n"
        "\treturn result;",
        "\tif (overlay_acquired)\n\t\tdisk_durability_overlay_leave();\n"
        "\tbio_account_transfer(0, 0, BIO_TRANSFER_WRITE, result);\n"
        "\treturn result;",
        "volatile overlay charged as a physical transfer",
    ),
    (
        "\tdisk_durability_overlay_leave();\n\treturn result;\n#else\n"
        "\treturn disk_submit(0, VIRTIO_BLK_T_FLUSH, test_direct, 0);",
        "\tdisk_durability_overlay_leave();\n"
        "\tbio_account_transfer(0, 0, BIO_TRANSFER_FLUSH, result);\n"
        "\treturn result;\n#else\n"
        "\treturn disk_submit(0, VIRTIO_BLK_T_FLUSH, test_direct, 0);",
        "overlay barrier aggregated physical submissions",
    ),
):
    if old not in driver:
        raise SystemExit(f"accounting mutation anchor drifted: {label}")
    try:
        validate_overlay_accounting(driver.replace(old, new, 1))
        validate_barrier_accounting(driver.replace(old, new, 1))
    except ContractError:
        continue
    raise SystemExit(f"physical accounting mutation survived: {label}")
range_call_removed = (
    test_program[:range_call_start] + test_program[range_call_end:]
)
if ("static void test_range_rejection" not in range_call_removed or
        "virtiodisk_ucore: range-rejection passed" not in range_call_removed):
    raise SystemExit("range execution mutation did not preserve residual strings")
expect_range_execution_rejected(range_call_removed)
for old, label in (
    ("value.completions > 2", "long-peer completion sentinel removed"),
    ("if (value.reset_recoveries == 1)", "reset recovery event removed"),
):
    mutated = test_program.replace(old, "0", 1)
    try:
        validate_event_driven_queue_checks(mutated)
    except ContractError:
        continue
    raise SystemExit(f"queue event mutation survived: {label}")
print("[virtio-disk] static wiring passed")
