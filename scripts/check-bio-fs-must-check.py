#!/usr/bin/env python3
"""拒绝未经检查的文件系统块 I/O 结果。

编译器属性是第一道防线。本仓库级检查还会拒绝可能压制或绕过编译器警告的类型转换
与旧 API 形态，因此适用于 CI 且不依赖单一编译器。
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
    """清空恒假代码块，确保契约 token 必须可达。"""
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
        # 返回的指针和状态构成同一结果。即使未来实现随附诊断 inode，所有调用者
        # 仍必须拒绝非 FOUND 状态。
        following = text[match.end() : match.end() + 500]
        if not re.search(rf"\b{status}\s*!=\s*FS_LOOKUP_FOUND\b", following):
            failures.append(
                f"{path.relative_to(ROOT)}:{line_number(text, match.start())}: "
                "root_dir_status result is not checked for FS_LOOKUP_FOUND"
            )


def require_contracts(failures: list[str]) -> None:
    bio_h = (OS_DIR / "bio.h").read_text(encoding="utf-8")
    fs_h = (OS_DIR / "fs.h").read_text(encoding="utf-8")
    bio_c = (OS_DIR / "bio.c").read_text(encoding="utf-8")
    fs_c = (OS_DIR / "fs.c").read_text(encoding="utf-8")
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
