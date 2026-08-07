#!/usr/bin/env python3
"""延迟 I/O 类别与收据保留的变异契约。"""

from pathlib import Path

from thread_cold_source import (
    ThreadColdSourceError,
    normalize_thread_cold_access,
    verify_thread_cold_contract,
)


ROOT = Path(__file__).resolve().parents[1]
BIO_RAW = (ROOT / "os/bio.c").read_text(encoding="utf-8")
BIO_H = (ROOT / "os/bio.h").read_text(encoding="utf-8")
PROC_H_RAW = (ROOT / "os/proc.h").read_text(encoding="utf-8")
verify_thread_cold_contract(PROC_H_RAW, (BIO_RAW,))
BIO = normalize_thread_cold_access(BIO_RAW, "os/bio.c")


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    marker = f"{name}("
    search = 0
    while True:
        start = source.find(marker, search)
        if start < 0:
            raise ContractError(f"missing function {name}")
        opening = source.find("(", start)
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        brace = source.find("{", closing)
        semicolon = source.find(";", closing)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1:index]
        search = closing + 1


def compact(value: str) -> str:
    return " ".join(value.split())


def replace_in_function(source: str, name: str, old: str, new: str) -> str:
    body = function_body(source, name)
    if old not in body:
        raise ContractError(f"mutation anchor drift: {name}: {old}")
    return source.replace(body, body.replace(old, new, 1), 1)


def ordered(body: str, *tokens: str) -> None:
    cursor = 0
    for token in tokens:
        position = body.find(token, cursor)
        if position < 0:
            raise ContractError(f"ordering contract missing {tokens}")
        cursor = position + len(token)


def validate(bio: str, bio_h: str) -> None:
    if "int bio_deferred_owner_retain_current(uint, uint *, uint64 *);" not in bio_h:
        raise ContractError("current-class deferred retain API is not exported")
    if "int bio_deferred_sponsor_begin(uint, uint, uint64);" not in bio_h:
        raise ContractError("deferred sponsor origin identity is not exported")
    current = compact(function_body(bio, "bio_deferred_owner_retain_current"))
    for token in (
        "thread->state != RUNNING",
        "bio_deferred_sponsor_current() || bio_background_current()",
        "thread_cold(thread)->io_request_depth == 0",
        "thread_cold(thread)->io_request_id == 0",
        "thread_cold(thread)->io_request_owner != owner",
        "selected = thread_cold(thread)->io_request_class",
        "selected >= IO_POLICY_CLASS_COUNT",
        "io_bucket_burst(owner, selected) == 0",
        "state->retiring || state->quiesced",
        "state->deferred_references++",
        "*io_class = selected",
        "*origin_request_id = thread_cold(thread)->io_request_id",
    ):
        if token not in current:
            raise ContractError(f"current-class retain missing {token}")
    if "IO_POLICY_CLASS_BACKGROUND" in current:
        raise ContractError("foreground retain is silently demoted to background")
    wrapper = compact(function_body(bio, "bio_deferred_owner_retain"))
    if wrapper != (
        "uint64 origin_request_id; return bio_deferred_owner_retain_current( "
        "owner, io_class, &origin_request_id);"
    ):
        raise ContractError("legacy retain entry does not preserve exact class")

    cleanup = compact(function_body(bio, "bio_deferred_owner_retain_cleanup"))
    for token in (
        "selected = io_policy.deferred.io_class",
        "selected = IO_POLICY_CLASS_BACKGROUND",
        "thread_cold(thread)->io_request_owner == owner",
        "selected = thread_cold(thread)->io_request_class",
        "state->deferred_references++",
    ):
        if token not in cleanup:
            raise ContractError(f"explicit cleanup retain missing {token}")
    if "thread_cold(thread)->io_request_depth != 0 ?" in cleanup:
        raise ContractError("cleanup borrows an unrelated ambient request class")

    sponsor = compact(function_body(bio, "bio_deferred_sponsor_begin"))
    for token in (
        "int independent_lease = 0",
        "int reuse_request_lease = 0",
        "background_executor = bio_background_current()",
        "io_policy.background.owner == owner",
        "origin_request_id != 0",
        "!background_executor && !polling_executor",
        "thread_cold(thread)->io_request_id == origin_request_id",
        "thread_cold(thread)->io_request_owner == owner",
        "thread_cold(thread)->io_request_class == io_class",
        "io_bucket_burst(owner, io_class) == 0",
        "effective_class = io_class",
        "reuse_request_lease = 1",
        "effective_class = IO_POLICY_CLASS_BACKGROUND",
        "bio_cleanup_class_select(owner, &effective_class)",
        "bio_cleanup_lease_reserve_locked( state, effective_class",
        "independent_lease = 1",
        "io_policy.deferred.reuse_request_lease = reuse_request_lease",
        "io_policy.deferred.independent_lease = independent_lease",
        "io_policy.deferred.origin_request_id = origin_request_id",
        "io_policy.deferred.io_class = effective_class",
    ):
        if token not in sponsor:
            raise ContractError(f"deferred execution class missing {token}")
    if sponsor.count("if (!bio_io_quiescent_current())") != 1:
        raise ContractError("deferred sponsor starts with active transient I/O")
    cleanup_begin = compact(function_body(bio, "bio_cleanup_token_begin"))
    cleanup_end = compact(function_body(bio, "bio_cleanup_token_end"))
    if cleanup_begin.count("if (!bio_io_quiescent_current())") != 1 or \
            cleanup_end.count("if (!bio_io_quiescent_current())") != 1:
        raise ContractError("cleanup token quiescence is not symmetric")

    retained = compact(function_body(bio, "bio_cache_state_retained"))
    if "state->deferred_references != 0" not in retained:
        raise ContractError("deferred cleanup loses its cache floor after quiesce")
    release = compact(function_body(bio, "bio_deferred_owner_release"))
    ordered(
        release,
        "state->deferred_references--",
        "bio_cache_release_closed_owner(owner)",
        "io_active_request_release(state)",
        "io_owner_reap_retired()",
    )

    transfer = compact(function_body(bio, "bio_account_transfer_batch"))
    deferred_tokens = (
        "if (bio_deferred_sponsor_current()",
        "io_policy.deferred.independent_lease",
        "transfers = &io_policy.deferred.transfers",
        "reservation = &io_policy.deferred.reservation",
        "device_reservation = &io_policy.deferred.device_reservation",
        "unreserved = 0",
        "else if (bio_deferred_sponsor_current()",
        "io_policy.deferred.reuse_request_lease",
        "thread != 0",
        "io_policy.deferred.origin_request_id != 0",
        "thread_cold(thread)->io_request_id == io_policy.deferred.origin_request_id",
        "thread_cold(thread)->io_request_owner == io_policy.deferred.owner",
        "thread_cold(thread)->io_request_class == io_policy.deferred.io_class",
        "owner == io_policy.deferred.owner",
        "io_class == io_policy.deferred.io_class",
        "transfers = &thread_cold(thread)->io_request_transfers",
        "reservation = &thread_cold(thread)->io_request_reservation",
        "device_reservation = &thread_cold(thread)->io_request_device_reservation",
        "unreserved = 0",
        "else if (bio_background_current()",
    )
    ordered(transfer, *deferred_tokens)
    if transfer.count("transfers = &thread_cold(thread)->io_request_transfers") != 2:
        raise ContractError("deferred and direct request lease paths are incomplete")

    sponsor_end = compact(function_body(bio, "bio_deferred_sponsor_end"))
    for token in (
        "independent = io_policy.deferred.independent_lease",
        "transfers = io_policy.deferred.transfers",
        "if (transfers == 0)",
        "io_rate_reservation_refund( state, io_class, &reservation, &device_reservation)",
        "io_wait_for_debt_mode( state, io_class, 1, 1, polling)",
        "io_wait_for_device_debt_mode( owner, io_class, 1, polling)",
    ):
        if token not in sponsor_end:
            raise ContractError(f"independent sponsor settlement missing {token}")
    if sponsor_end.count("if (!bio_io_quiescent_current())") != 1:
        raise ContractError("deferred sponsor end uses a context-specific hold check")
    if transfer.index("if (bio_deferred_sponsor_current()") > transfer.index(
            "else if (thread != 0 && thread->state == RUNNING"):
        raise ContractError("ambient request path hides deferred lease matching")

    begin = compact(function_body(bio, "bio_request_begin_current_mode"))
    ordered(
        begin,
        "thread_cold(thread)->io_request_depth != 0",
        "!bio_thread_request_flags_valid(thread)",
        "thread_cold(thread)->io_request_depth++",
        "!bio_thread_request_flags_valid(thread)",
        "io_active_request_acquire(state)",
        "thread_cold(thread)->io_request_flags = BIO_REQUEST_ACTIVE",
        "thread_cold(thread)->io_request_id = bio_request_identity_allocate()",
        "thread_cold(thread)->io_request_depth = 1",
    )
    end = compact(function_body(bio, "bio_request_end_current_mode"))
    if end.count("bio_thread_request_clear(thread);") != 2:
        raise ContractError("normal and cache-only request exits do not clear identity")
    abort = compact(function_body(bio, "bio_request_abort_thread"))
    if abort.count("bio_thread_request_clear(thread);") != 2:
        raise ContractError("active and cache-only aborts do not clear identity")


def validate_accessor_regressions() -> None:
    const_prefix, const_suffix = PROC_H_RAW.rsplit(
        "THREAD_TRAP_COLD_OFFSET);", 1
    )
    mutations = (
        (
            PROC_H_RAW.replace("THREAD_TRAP_COLD_OFFSET);", "0);", 1),
            BIO_RAW,
            "mutable accessor offset",
        ),
        (
            const_prefix + "0);" + const_suffix,
            BIO_RAW,
            "const accessor offset",
        ),
        (
            PROC_H_RAW,
            BIO_RAW + "\n#define thread_trap_cold(t) ((void *)0)\n",
            "accessor macro shadow",
        ),
        (
            PROC_H_RAW,
            "# if 0\n" + BIO_RAW + "\n#endif\n",
            "disabled source stub",
        ),
    )
    for proc_h, bio, label in mutations:
        try:
            verify_thread_cold_contract(proc_h, (bio,))
        except ThreadColdSourceError:
            continue
        raise ContractError(f"thread cold accessor mutation survived: {label}")
    noise = normalize_thread_cold_access(
        '(void)"thread_trap_cold(thread)->io_request_owner"; '
        "/* thread_trap_cold(thread)->io_request_owner */"
    )
    if "thread_cold(thread)->io_request_owner" in noise:
        raise ContractError("comment or string forged a cold accessor")


validate(BIO, BIO_H)
validate_accessor_regressions()

MUTATIONS = (
    (replace_in_function(BIO, "bio_deferred_sponsor_begin",
                         "if (!bio_io_quiescent_current())\n\t\treturn -1;", ""), BIO_H,
     "sponsor begins inside active I/O"),
    (replace_in_function(BIO, "bio_deferred_sponsor_end",
                         "if (!bio_io_quiescent_current())", "if (0)"), BIO_H,
     "sponsor end drops context quiescence"),
    (replace_in_function(BIO, "bio_cleanup_token_begin",
                         "if (!bio_io_quiescent_current())\n\t\treturn -1;", ""), BIO_H,
     "cleanup begins inside active I/O"),
    (replace_in_function(BIO, "bio_cleanup_token_end",
                         "if (!bio_io_quiescent_current())", "if (0)"), BIO_H,
     "cleanup end drops context quiescence"),
    (replace_in_function(BIO, "bio_deferred_owner_retain_current",
                         "thread_cold(thread)->io_request_owner != owner", "0"), BIO_H,
     "owner match removed"),
    (replace_in_function(BIO, "bio_deferred_owner_retain_current",
                         "thread_cold(thread)->io_request_id == 0", "0"), BIO_H,
     "origin request validation removed"),
    (replace_in_function(BIO, "bio_deferred_owner_retain_current",
                         "selected = thread_cold(thread)->io_request_class;",
                         "selected = IO_POLICY_CLASS_BACKGROUND;"), BIO_H,
     "foreground class demoted"),
    (replace_in_function(BIO, "bio_deferred_owner_retain_current",
                         "bio_deferred_sponsor_current() || bio_background_current()", "0"), BIO_H,
     "nested ambient retain accepted"),
    (replace_in_function(BIO, "bio_deferred_owner_retain_current",
                         "state->deferred_references++;", ""), BIO_H,
     "cleanup reference omitted"),
    (replace_in_function(BIO, "bio_deferred_owner_retain",
                         "bio_deferred_owner_retain_current(\n\t\towner, io_class, &origin_request_id)", "-1"), BIO_H,
     "legacy entry disconnected"),
    (replace_in_function(
        BIO, "bio_deferred_owner_retain",
        "return bio_deferred_owner_retain_current(\n"
        "\t\towner, io_class, &origin_request_id);",
        "(void)bio_deferred_owner_retain_current(\n"
        "\t\towner, io_class, &origin_request_id);\n\treturn -1;",
    ), BIO_H, "legacy entry discards the retain result"),
    (replace_in_function(BIO, "bio_deferred_owner_retain_cleanup",
                         "thread_cold(thread)->io_request_owner == owner", "1"), BIO_H,
     "cleanup ambient class accepted"),
    (replace_in_function(BIO, "bio_cache_state_retained",
                         "state->deferred_references != 0", "0"), BIO_H,
     "closing cache floor dropped"),
    (replace_in_function(BIO, "bio_deferred_owner_release",
                         "bio_cache_release_closed_owner(owner);", ""), BIO_H,
     "closing cache release omitted"),
    (replace_in_function(BIO, "bio_account_transfer_batch",
                         "bio_deferred_sponsor_current()", "0"), BIO_H,
     "deferred receipt reuse disabled"),
    (replace_in_function(BIO, "bio_deferred_sponsor_begin",
                         "effective_class = IO_POLICY_CLASS_BACKGROUND;",
                         "effective_class = io_class;"), BIO_H,
     "aged execution kept foreground class"),
    (replace_in_function(BIO, "bio_deferred_sponsor_begin",
                         "thread_cold(thread)->io_request_id == origin_request_id", "1"), BIO_H,
     "stale same-class request reused"),
    (replace_in_function(BIO, "bio_deferred_sponsor_begin",
                         "origin_request_id != 0", "1"), BIO_H,
     "zero-origin async work reused foreground"),
    (replace_in_function(BIO, "bio_deferred_sponsor_begin",
                         "io_bucket_burst(owner, io_class) == 0", "0"), BIO_H,
     "invalid retained class washed into fallback"),
    (replace_in_function(BIO, "bio_deferred_sponsor_begin",
                         "io_policy.deferred.reuse_request_lease = reuse_request_lease;", ""), BIO_H,
     "receipt reuse state removed"),
    (replace_in_function(BIO, "bio_account_transfer_batch",
                         "thread_cold(thread)->io_request_class == io_policy.deferred.io_class", "1"), BIO_H,
     "deferred class match removed"),
    (replace_in_function(BIO, "bio_account_transfer_batch",
                         "thread_cold(thread)->io_request_id == io_policy.deferred.origin_request_id", "1"), BIO_H,
     "transfer request identity match removed"),
    (replace_in_function(BIO, "bio_account_transfer_batch",
                         "reservation = &thread_cold(thread)->io_request_reservation;", ""), BIO_H,
     "deferred reservation reuse removed"),
    (replace_in_function(BIO, "bio_request_begin_current_mode",
                         "thread_cold(thread)->io_request_id = bio_request_identity_allocate();", ""), BIO_H,
     "top-level request identity omitted"),
    (replace_in_function(BIO, "bio_request_end_current_mode",
                         "bio_thread_request_clear(thread);", ""), BIO_H,
     "normal request identity not cleared"),
    (replace_in_function(BIO, "bio_request_abort_thread",
                         "bio_thread_request_clear(thread);", ""), BIO_H,
     "aborted request identity not cleared"),
)

for mutated_bio, mutated_header, label in MUTATIONS:
    try:
        validate(mutated_bio, mutated_header)
    except ContractError:
        continue
    raise SystemExit(f"deferred sponsor mutation survived: {label}")

print(f"[bio-deferred-sponsor] {len(MUTATIONS)} mutations passed")
