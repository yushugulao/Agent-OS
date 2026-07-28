#!/usr/bin/env python3
"""Reject unchecked filesystem block-I/O results.

The compiler attributes are the first line of defense.  This repository-wide
check also rejects casts and legacy API shapes that can silence or sidestep a
compiler warning, so it is suitable for CI and does not depend on one compiler.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
OS_DIR = ROOT / "os"
CHECKED_APIS = (
    "bread",
    "bwrite",
    "bclaim",
    "bio_durable_flush",
    "ivalid",
    "iupdate",
    "itruncate_reclaim",
)


def strip_c_noise(text: str) -> str:
    pattern = re.compile(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        re.DOTALL,
    )

    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group())

    return pattern.sub(blank, text)


def strip_semantic_dead_code(text: str) -> str:
    """Blank constant-false blocks so contract tokens must be reachable."""
    clean = strip_c_noise(text)
    disabled = re.compile(
        r"^[ \t]*#if\s+0\b.*?^[ \t]*#endif\b[^\n]*",
        re.MULTILINE | re.DOTALL,
    )
    clean = disabled.sub(
        lambda match: "".join(
            "\n" if char == "\n" else " " for char in match.group()
        ),
        clean,
    )
    pattern = re.compile(r"\bif\s*\(\s*0\s*\)\s*\{")
    while True:
        match = pattern.search(clean)
        if match is None:
            return clean
        opening = clean.find("{", match.start())
        closing = matching_brace(clean, opening)
        if closing < 0:
            return clean
        dead = clean[match.start() : closing + 1]
        blanked = "".join("\n" if char == "\n" else " " for char in dead)
        clean = clean[: match.start()] + blanked + clean[closing + 1 :]


def matching_paren(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def matching_brace(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def function_body(text: str, name: str) -> str | None:
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", text):
        opening = text.find("(", match.start())
        closing = matching_paren(text, opening)
        if closing < 0:
            continue
        next_token = re.search(r"\S", text[closing + 1 :])
        if next_token is None or next_token.group() != "{":
            continue
        brace = closing + 1 + next_token.start()
        end = matching_brace(text, brace)
        if end >= 0:
            return text[brace + 1 : end]
    return None


def split_arguments(arguments: str) -> list[str]:
    result: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(arguments):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(arguments[start:index].strip())
            start = index + 1
    result.append(arguments[start:].strip())
    return result


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def checked_context(text: str, call_start: int, call_end: int) -> bool:
    statement_start = max(
        text.rfind(";", 0, call_start),
        text.rfind("{", 0, call_start),
        text.rfind("}", 0, call_start),
    ) + 1
    prefix = text[statement_start:call_start]
    if re.search(r"\(\s*void\s*\)\s*$", prefix):
        return False
    if re.search(r"(?:^|[^=!<>])=(?!=)", prefix):
        return True
    if re.search(r"^\s*return\s*$", prefix):
        return True
    controls = list(re.finditer(r"\b(if|while|for|switch)\s*\(", prefix))
    if not controls:
        return False
    condition = prefix[controls[-1].end() - 1 :]
    return condition.count("(") > condition.count(")")


def check_calls(path: pathlib.Path, failures: list[str]) -> None:
    original = path.read_text(encoding="utf-8")
    text = strip_c_noise(original)
    for api in CHECKED_APIS:
        for match in re.finditer(rf"\b{api}\s*\(", text):
            opening = text.find("(", match.start())
            closing = matching_paren(text, opening)
            line = line_number(text, match.start())
            location = f"{path.relative_to(ROOT)}:{line}"
            if closing < 0:
                failures.append(f"{location}: unterminated {api} call")
                continue
            next_token = re.search(r"\S", text[closing + 1 :])
            if next_token and next_token.group() == "{":
                continue
            if not checked_context(text, match.start(), closing):
                failures.append(f"{location}: unchecked {api} result")
            if api == "bread":
                arguments = split_arguments(text[opening + 1 : closing])
                if len(arguments) != 3 or not arguments[2].startswith("&"):
                    failures.append(
                        f"{location}: bread must use the three-argument out-parameter API"
                    )


def check_lookup_calls(path: pathlib.Path, failures: list[str]) -> None:
    original = path.read_text(encoding="utf-8")
    text = strip_c_noise(original)
    for legacy in ("namei_scope", "root_dir"):
        for match in re.finditer(rf"\b{legacy}\s*\(", text):
            failures.append(
                f"{path.relative_to(ROOT)}:{line_number(text, match.start())}: "
                f"lossy {legacy} API is forbidden"
            )
    for match in re.finditer(
        r"\broot_dir_status\s*\(\s*&\s*([A-Za-z_]\w*)\s*\)", text
    ):
        status = re.escape(match.group(1))
        # The returned pointer and status form one result. Every caller must
        # reject a non-FOUND status even if a future implementation returns a
        # diagnostic inode alongside it.
        following = text[match.end() : match.end() + 500]
        if not re.search(rf"\b{status}\s*!=\s*FS_LOOKUP_FOUND\b", following):
            failures.append(
                f"{path.relative_to(ROOT)}:{line_number(text, match.start())}: "
                "root_dir_status result is not checked for FS_LOOKUP_FOUND"
            )


def check_metadata_bank_status_contracts(
    io_h: str,
    io_c: str,
    store_c: str,
    failures: list[str],
    prefix: str = "",
) -> None:
    location = f"{prefix}: " if prefix else ""
    prototype = re.compile(
        r"struct\s+inode\s*\*\s*agent_meta_store_io_lookup_bank\s*"
        r"\(\s*char\s*\*\s*,\s*int\s*,\s*int\s*\*\s*\)\s*;"
    )
    if not prototype.search(io_h):
        failures.append(
            f"{location}metadata bank lookup must expose a status out-parameter"
        )

    clean_io = strip_semantic_dead_code(io_c)
    body = function_body(clean_io, "agent_meta_store_io_lookup_bank")
    if body is None:
        failures.append(f"{location}missing metadata bank lookup implementation")
    else:
        required_io = (
            "*status_out = FS_LOOKUP_ERROR;",
            "*status_out = result;",
            "*status_out = status;",
            "*status_out = FS_LOOKUP_FOUND;",
            "status != FS_LOOKUP_ABSENT",
        )
        for token in required_io:
            if token not in body:
                failures.append(
                    f"{location}metadata bank lookup loses status: {token}"
                )
        if not re.search(
            r"if\s*\(\s*!create\s*\|\|\s*"
            r"status\s*!=\s*FS_LOOKUP_ABSENT\s*\)\s*return\s+0\s*;",
            body,
        ):
            failures.append(
                f"{location}metadata bank creation must follow only ABSENT"
            )

    clean_store = strip_semantic_dead_code(store_c)
    for name in (
        "agent_meta_persist_target_locked",
        "agent_meta_store_prepare_banks_locked",
    ):
        lookup_body = function_body(clean_store, name)
        if lookup_body is None:
            failures.append(f"{location}missing metadata function {name}")
            continue
        if not re.search(
            r"agent_meta_store_io_lookup_bank\s*\([^;]*"
            r"&\s*lookup_status\s*\)",
            lookup_body,
            re.DOTALL,
        ):
            failures.append(
                f"{location}{name} must consume metadata lookup status"
            )
        if not re.search(
            r"return\s+lookup_status\s*<\s*0\s*\?\s*lookup_status\s*:",
            lookup_body,
        ):
            failures.append(
                f"{location}{name} must preserve negative lookup status"
            )

    prepare = function_body(clean_store, "agent_meta_store_prepare_banks_locked")
    if prepare is not None:
        for token in ("return inode_status;", "return flush_status;"):
            if token not in prepare:
                failures.append(
                    f"{location}metadata bank preparation loses status: {token}"
                )
        if not re.search(
            r"inode_status\s*=\s*fs_preallocate_inode\s*\(", prepare
        ):
            failures.append(
                f"{location}metadata preallocation must preserve exact status"
            )

    device_error = function_body(clean_store, "agent_meta_persist_device_error")
    if device_error is None:
        failures.append(f"{location}missing metadata device error classifier")
    else:
        busy_test = re.search(
            r"result\s*==\s*VIRTIO_DISK_ERR_BUSY", device_error
        )
        busy_return = re.search(
            r"return\s+AGENT_META_PERSIST_DEFERRED\s*;", device_error
        )
        if busy_test is None or busy_return is None or (
            busy_return.start() < busy_test.end()
        ):
            failures.append(f"{location}metadata BUSY must become deferred")
        if not re.search(
            r"return\s+result\s*<\s*0\s*\?\s*result\s*:\s*"
            r"VIRTIO_DISK_ERR_IO\s*;",
            device_error,
        ):
            failures.append(
                f"{location}metadata device classifier must preserve raw status"
            )
    if not re.search(
        r"#define\s+AGENT_META_PERSIST_DEFERRED\s+\(-[0-9]+\)", clean_store
    ) or not re.search(
        r"AGENT_META_PERSIST_DEFERRED\s*<\s*VIRTIO_DISK_ERR_RANGE",
        clean_store,
    ):
        failures.append(
            f"{location}metadata deferred sentinel must be outside device status space"
        )

    start = function_body(clean_store, "agent_meta_persist_start_locked")
    if start is None:
        failures.append(f"{location}missing metadata persist start")
    elif len(re.findall(
        r"return\s+agent_meta_persist_device_error\s*"
        r"\(\s*operation_status\s*\)\s*;",
        start,
    )) < 2:
        failures.append(
            f"{location}metadata persist start must classify both I/O boundaries"
        )

    foreground = function_body(clean_store, "agent_file_persist")
    if foreground is None:
        failures.append(f"{location}missing foreground metadata persist")
    else:
        deferred = foreground.find(
            "start_status == AGENT_META_PERSIST_DEFERRED"
        )
        failed = foreground.find("start_status < 0")
        if deferred < 0 or failed < 0 or deferred > failed:
            failures.append(
                f"{location}foreground persist must classify DEFERRED before error"
            )

    step = function_body(clean_store, "agent_meta_persist_step_locked")
    if step is None or step.count("agent_meta_persist_device_error(n)") < 2:
        failures.append(
            f"{location}metadata restart/mirror must preserve device status"
        )

    maintain = function_body(clean_store, "agent_file_writeback_maintain")
    if maintain is None or not re.search(
        r"step\s*=\s*agent_meta_persist_start_locked\s*\(\s*owner\s*\)",
        maintain,
    ):
        failures.append(
            f"{location}background metadata persist must inspect start status"
        )
    elif "step == AGENT_META_PERSIST_DEFERRED" not in maintain:
        failures.append(
            f"{location}background metadata persist must defer on BUSY"
        )


def require_contracts(failures: list[str]) -> None:
    bio_h = (OS_DIR / "bio.h").read_text(encoding="utf-8")
    fs_h = (OS_DIR / "fs.h").read_text(encoding="utf-8")
    bio_c = (OS_DIR / "bio.c").read_text(encoding="utf-8")
    fs_c = (OS_DIR / "fs.c").read_text(encoding="utf-8")
    metadata_store_c = (OS_DIR / "agent_metadata_store.c").read_text(
        encoding="utf-8"
    )
    metadata_store_io_c = (OS_DIR / "agent_metadata_store_io.c").read_text(
        encoding="utf-8"
    )
    metadata_store_io_h = (OS_DIR / "agent_metadata_store_io.h").read_text(
        encoding="utf-8"
    )
    required = {
        "os/bio.h": (
            "int bread(uint, uint, struct buf **) BIO_MUST_CHECK;",
            "int bwrite(struct buf *) BIO_MUST_CHECK;",
            "int bio_durable_flush(void) BIO_MUST_CHECK;",
        ),
        "os/fs.h": (
            "int ivalid(struct inode *) FS_MUST_CHECK;",
            "int iupdate(struct inode *) FS_MUST_CHECK;",
            "int itruncate_reclaim(struct inode_reclaim *) FS_MUST_CHECK;",
        ),
        "os/bio.c": (
            "*out = 0;",
            "memset(b->data, 0, sizeof(b->data));",
        ),
    }
    sources = {"os/bio.h": bio_h, "os/fs.h": fs_h, "os/bio.c": bio_c}
    for source, tokens in required.items():
        for token in tokens:
            if token not in sources[source]:
                failures.append(f"{source}: missing must-check contract: {token}")
    if "bio_buffer_result" in bio_h or "bio_buffer_result" in bio_c:
        failures.append("os/bio: legacy split-result API must not be restored")
    for token in (
        "n = ivalid(ip);",
        "return agent_meta_persist_device_error(n);",
    ):
        if token not in metadata_store_c:
            failures.append(
                "os/agent_metadata_store.c: inode read result loses device provenance: "
                + token
            )
    clean_fs_c = strip_c_noise(fs_c)
    for name in (
        "readsb",
        "fs_layout_valid",
        "fs_qmap_read",
        "fs_qmap_write",
        "fs_scrub_block_allocated",
        "fs_scrub_mark_block",
        "fs_scrub_mark_inode_blocks",
        "fs_scrub_read_dinode",
        "fs_scrub_inode_block",
        "fs_scrub_mark_root_entries",
        "fs_scrub_retire_inode_forward",
        "fs_mount_scrub",
        "fs_storage_import_account",
        "fs_storage_accounts_sync",
        "fs_storage_rebuild",
        "fs_claim_inode_blocks",
        "fs_recover_public_claims",
        "fs_dinode_has_scope_owner",
        "fs_reap_scope_inode_forward",
        "fs_reap_boot_workflow_objects",
        "fsinit",
    ):
        body = function_body(clean_fs_c, name)
        if body is None:
            failures.append(f"os/fs.c: missing mount validation function {name}")
        elif re.search(r"\bpanic\s*\(", body):
            failures.append(
                f"os/fs.c: mount validation function {name} may not panic"
            )
    if re.search(r"\bnamei_scope\s*\(", fs_h):
        failures.append("os/fs.h: lossy namei_scope API must not be restored")
    if re.search(r"\broot_dir\s*\(", fs_h):
        failures.append("os/fs.h: lossy root_dir API must not be restored")
    for token in (
        "struct inode *namei_scope_status(char *, uint, uint, int *);",
        "struct inode *root_dir_status(int *);",
    ):
        if token not in fs_h:
            failures.append(f"os/fs.h: missing checked lookup contract: {token}")

    create = function_body(strip_semantic_dead_code(fs_c), "fs_create")
    if create is None:
        failures.append("os/fs.c: missing fs_create implementation")
    else:
        for token in (
            "*status = root_status;",
            "*status = lookup_status;",
            "*status = result;",
            "*status = FS_LOOKUP_FOUND;",
        ):
            if token not in create:
                failures.append(
                    "os/fs.c: fs_create loses status provenance: " + token
                )
    if not re.search(
        r"struct\s+inode\s*\*fs_create\s*\([^;]*uint\s*,\s*int\s*\*\s*\)\s*;",
        fs_h,
        re.DOTALL,
    ):
        failures.append("os/fs.h: fs_create must expose a status out-parameter")

    preallocate = function_body(
        strip_semantic_dead_code(fs_c), "fs_preallocate_inode"
    )
    if preallocate is None:
        failures.append("os/fs.c: missing fs_preallocate_inode implementation")
    else:
        for pattern, description in (
            (r"result\s*=\s*map_result\s*<\s*0", "mapping status"),
            (r"result\s*=\s*iupdate\s*\(", "inode status"),
            (r"checkpoint\s*=\s*bio_request_checkpoint\s*\(",
             "typed checkpoint status"),
            (r"bio_checkpoint_should_stop\s*\(\s*checkpoint\s*\)",
             "checkpoint stop classification"),
            (r"checkpoint\.state\s*==\s*BIO_CHECKPOINT_DEFERRED\s*\?\s*"
             r"VIRTIO_DISK_ERR_BUSY\s*:\s*-1",
             "checkpoint status translation"),
            (r"return\s+result\s*;", "returned status"),
        ):
            if not re.search(pattern, preallocate):
                failures.append(
                    f"os/fs.c: fs_preallocate_inode loses {description}"
                )
    check_metadata_bank_status_contracts(
        metadata_store_io_h, metadata_store_io_c, metadata_store_c, failures
    )


def check_checker_regressions(failures: list[str]) -> None:
    samples = {
        "if (bwrite(bp) < 0) { }": True,
        "result = bwrite(bp);": True,
        "return iupdate(ip);": True,
        "if (ivalid(ip) < 0) { }": True,
        "status = ivalid(ip);": True,
        "bwrite(bp);": False,
        "ivalid(ip);": False,
        "(void)ivalid(ip);": False,
        "(void)bwrite(bp);": False,
        "if (ready) bwrite(bp);": False,
    }
    for source, expected in samples.items():
        call = re.search(r"\b(?:bwrite|iupdate|ivalid)\s*\(", source)
        assert call is not None
        opening = source.find("(", call.start())
        end = matching_paren(source, opening)
        actual = checked_context(source, call.start(), end)
        if actual != expected:
            failures.append(f"checker regression for {source!r}")

    lookup_samples = {
        "root = root_dir_status(&status); if (root == 0 || status != FS_LOOKUP_FOUND) return;": True,
        "root = root_dir_status(&status); if (root == 0) return;": False,
    }
    for source, expected in lookup_samples.items():
        match = re.search(
            r"\broot_dir_status\s*\(\s*&\s*([A-Za-z_]\w*)\s*\)", source
        )
        assert match is not None
        status = re.escape(match.group(1))
        actual = bool(
            re.search(
                rf"\b{status}\s*!=\s*FS_LOOKUP_FOUND\b",
                source[match.end() :],
            )
        )
        if actual != expected:
            failures.append(f"lookup checker regression for {source!r}")

    mount_samples = {
        "static int mount_check(void) { return -1; }": False,
        "static int mount_check(void) { panic(\"disk\"); }": True,
    }
    for source, expected_panic in mount_samples.items():
        body = function_body(strip_c_noise(source), "mount_check")
        actual_panic = body is not None and bool(
            re.search(r"\bpanic\s*\(", body)
        )
        if actual_panic != expected_panic:
            failures.append(f"mount checker regression for {source!r}")

    metadata_io_h = (
        "struct inode *agent_meta_store_io_lookup_bank(char *, int, int *);"
    )
    metadata_io_c = r"""
struct inode *agent_meta_store_io_lookup_bank(char *name, int create,
                                               int *status_out) {
  int status;
  int result;
  *status_out = FS_LOOKUP_ERROR;
  ip = namei_scope_status(name, policy, scope, &status);
  if (ip != 0) {
    result = ivalid(ip);
    if (result < 0) { *status_out = result; return 0; }
    *status_out = FS_LOOKUP_FOUND;
    return ip;
  }
  *status_out = status;
  if (!create || status != FS_LOOKUP_ABSENT) return 0;
  ip = fs_create(name);
  if (ip == 0) return 0;
  result = ivalid(ip);
  if (result < 0) { *status_out = result; return 0; }
  *status_out = FS_LOOKUP_FOUND;
  return ip;
}
"""
    metadata_store_c = r"""
#define AGENT_META_PERSIST_DEFERRED (-4096)
_Static_assert(AGENT_META_PERSIST_DEFERRED < VIRTIO_DISK_ERR_RANGE, "unique");
static int agent_meta_persist_device_error(int result) {
  if (result == VIRTIO_DISK_ERR_BUSY) {
    return AGENT_META_PERSIST_DEFERRED;
  }
  return result < 0 ? result : VIRTIO_DISK_ERR_IO;
}
static int agent_meta_persist_target_locked(int target, int full) {
  ip = agent_meta_store_io_lookup_bank(name, 1, &lookup_status);
  if (ip == 0) return lookup_status < 0 ? lookup_status : VIRTIO_DISK_ERR_IO;
  return 0;
}
static int agent_meta_store_prepare_banks_locked(void) {
  ip = agent_meta_store_io_lookup_bank(name, 1, &lookup_status);
  if (ip == 0) return lookup_status < 0 ? lookup_status : VIRTIO_DISK_ERR_IO;
  if (inode_status < 0) return inode_status;
  inode_status = fs_preallocate_inode(ip, cred, size);
  if (inode_status < 0) return inode_status;
  if (flush_status < 0) return flush_status;
  return 0;
}
static int agent_meta_persist_start_locked(uint owner) {
  if (prepare_failed)
    return agent_meta_persist_device_error(operation_status);
  if (target_failed)
    return agent_meta_persist_device_error(operation_status);
  return 0;
}
static int agent_file_persist(void) {
  if (start_status == AGENT_META_PERSIST_DEFERRED) return 1;
  if (start_status < 0) return -1;
  return 0;
}
static int agent_meta_persist_step_locked(void) {
  if (restart) return agent_meta_persist_device_error(n);
  if (mirror) return agent_meta_persist_device_error(n);
  return 0;
}
static void agent_file_writeback_maintain(void) {
  step = agent_meta_persist_start_locked(owner);
  if (step == AGENT_META_PERSIST_DEFERRED) return;
}
"""
    metadata_mutations = {
        "status-out": (
            metadata_io_h.replace(", int *);", ");"),
            metadata_io_c,
            metadata_store_c,
        ),
        "ivalid-provenance": (
            metadata_io_h,
            metadata_io_c.replace("*status_out = result;", "*status_out = FS_LOOKUP_ERROR;"),
            metadata_store_c,
        ),
        "lookup-consumer": (
            metadata_io_h,
            metadata_io_c,
            metadata_store_c.replace(", &lookup_status)", ")", 1),
        ),
        "preallocate-provenance": (
            metadata_io_h,
            metadata_io_c,
            metadata_store_c.replace(
                "inode_status = fs_preallocate_inode(ip, cred, size);\n"
                "  if (inode_status < 0) return inode_status;",
                "if (fs_preallocate_inode(ip, cred, size) < 0) return -1;",
            ),
        ),
        "busy-mapping": (
            metadata_io_h,
            metadata_io_c,
            metadata_store_c.replace(
                "return AGENT_META_PERSIST_DEFERRED;",
                "return -1;",
                1,
            ),
        ),
        "raw-device-provenance": (
            metadata_io_h,
            metadata_io_c,
            metadata_store_c.replace(
                "return result < 0 ? result : VIRTIO_DISK_ERR_IO;",
                "return -1;",
            ),
        ),
        "dead-code-provenance": (
            metadata_io_h,
            metadata_io_c,
            metadata_store_c.replace(
                "return result < 0 ? result : VIRTIO_DISK_ERR_IO;",
                "if (0) { return result < 0 ? result : VIRTIO_DISK_ERR_IO; }\n"
                "  return -1;",
            ),
        ),
        "deferred-order": (
            metadata_io_h,
            metadata_io_c,
            metadata_store_c.replace(
                "if (start_status == AGENT_META_PERSIST_DEFERRED) return 1;\n"
                "  if (start_status < 0) return -1;",
                "if (start_status < 0) return -1;\n"
                "  if (start_status == AGENT_META_PERSIST_DEFERRED) return 1;",
            ),
        ),
    }
    baseline_failures: list[str] = []
    check_metadata_bank_status_contracts(
        metadata_io_h,
        metadata_io_c,
        metadata_store_c,
        baseline_failures,
        "metadata baseline",
    )
    if baseline_failures:
        failures.extend(baseline_failures)
    for name, sources in metadata_mutations.items():
        mutation_failures: list[str] = []
        check_metadata_bank_status_contracts(
            *sources, mutation_failures, prefix=f"metadata mutation {name}"
        )
        if not mutation_failures:
            failures.append(
                f"metadata status checker missed {name} mutation"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=ROOT,
        help="reserved for CI wrappers; must identify this checkout",
    )
    args = parser.parse_args()
    if args.root.resolve() != ROOT:
        parser.error("--root must identify the checkout containing this script")

    failures: list[str] = []
    check_checker_regressions(failures)
    require_contracts(failures)
    for path in sorted(OS_DIR.glob("*.c")):
        check_calls(path, failures)
        check_lookup_calls(path, failures)
    if failures:
        for failure in failures:
            print(f"bio-fs-must-check: {failure}", file=sys.stderr)
        return 1
    print("bio-fs-must-check: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
