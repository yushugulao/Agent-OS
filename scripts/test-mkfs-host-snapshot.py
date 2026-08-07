#!/usr/bin/env python3
"""mkfs 输入快照的纯 Host 回归与变异测试。"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile

from host_probe_toolchain import (
    host_compiler,
    probe_environment,
    probe_mode,
    required_sanitizer_flags,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT_SOURCE = ROOT / "nfs" / "host_image_snapshot.c"
SNAPSHOT_HEADER = ROOT / "nfs" / "host_image_snapshot.h"
MKFS_SOURCE = ROOT / "nfs" / "fs.c"
PLAIN_MKFS_SOURCE = ROOT / "baseline_ucore" / "nfs" / "fs.c"
NFS_MAKEFILE = ROOT / "nfs" / "Makefile"
LABDEMO_SOURCE = ROOT / "user" / "src" / "labdemo_ucore.c"

HARNESS_SOURCE = r"""
#include "host_image_snapshot.h"
#include "host_windows_compat.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int mutate_between_reads;

void host_snapshot_test_between_reads(int fd, const char *path)
{
    unsigned char byte;
    int writer;

    (void)fd;
    if (!mutate_between_reads)
        return;
    mutate_between_reads = 0;
    writer = open(path, O_RDWR | O_BINARY);
    if (writer < 0 || pread(writer, &byte, 1, 0) != 1) exit(90);
    byte ^= 0x5a;
    if (pwrite(writer, &byte, 1, 0) != 1 || fsync(writer) < 0 ||
        close(writer) < 0) exit(91);
}

static int expect_status(const char *path, size_t limit,
                         enum host_snapshot_status expected)
{
    struct host_file_snapshot snapshot = {0};
    enum host_snapshot_status status;
    int host_error = 0;

    status = host_snapshot_read(path, limit, &snapshot, &host_error);
    if (status != expected) {
        fprintf(stderr, "status=%d expected=%d os_error=%d\n",
                status, expected, host_error);
        host_snapshot_release(&snapshot);
        return 1;
    }
    if (expected == HOST_SNAPSHOT_TOO_LARGE && snapshot.size <= limit)
        return 2;
    host_snapshot_release(&snapshot);
    return 0;
}

int main(int argc, char **argv)
{
    struct host_file_snapshot snapshot = {0};
    enum host_snapshot_status status;
    int host_error = 0;
    int writer;
    unsigned char changed = 'X';

    if (argc < 3) return 80;
    if (strcmp(argv[1], "stable") == 0) {
        status = host_snapshot_read(argv[2], 0, &snapshot, &host_error);
        if (status != HOST_SNAPSHOT_OK || snapshot.size != 14 ||
            memcmp(snapshot.data, "stable-content", 14) != 0) {
            fprintf(stderr, "stable read status=%d size=%llu os_error=%d\n",
                    status, (unsigned long long)snapshot.size, host_error);
            return 1;
        }
        status = host_snapshot_validate_path(argv[2], &snapshot, &host_error);
        if (status != HOST_SNAPSHOT_OK) {
            fprintf(stderr, "stable validate status=%d os_error=%d\n",
                    status, host_error);
            return 1;
        }
        host_snapshot_release(&snapshot);
        return 0;
    }
    if (strcmp(argv[1], "limit") == 0)
        return expect_status(argv[2], 4, HOST_SNAPSHOT_TOO_LARGE);
    if (strcmp(argv[1], "empty") == 0)
        return expect_status(argv[2], 0, HOST_SNAPSHOT_EMPTY);
    if (strcmp(argv[1], "missing") == 0)
        return expect_status(argv[2], 0, HOST_SNAPSHOT_NOT_FOUND);
    if (strcmp(argv[1], "symlink") == 0)
        return expect_status(argv[2], 0, HOST_SNAPSHOT_NOT_REGULAR);
    if (strcmp(argv[1], "changed-read") == 0) {
        mutate_between_reads = 1;
        return expect_status(argv[2], 0, HOST_SNAPSHOT_CHANGED);
    }
    if (strcmp(argv[1], "changed-content") == 0) {
        status = host_snapshot_read(argv[2], 0, &snapshot, &host_error);
        if (status != HOST_SNAPSHOT_OK) return 10;
        writer = open(argv[2], O_WRONLY | O_BINARY);
        if (writer < 0 || pwrite(writer, &changed, 1, 0) != 1 ||
            fsync(writer) < 0 || close(writer) < 0) return 11;
        status = host_snapshot_validate_path(argv[2], &snapshot, &host_error);
        host_snapshot_release(&snapshot);
        return status == HOST_SNAPSHOT_CHANGED ? 0 : 12;
    }
    if (strcmp(argv[1], "changed-path") == 0) {
        if (argc != 4) return 20;
        status = host_snapshot_read(argv[2], 0, &snapshot, &host_error);
        if (status != HOST_SNAPSHOT_OK)
            return 21;
#ifdef _WIN32
        if (!MoveFileExA(argv[3], argv[2], MOVEFILE_REPLACE_EXISTING))
            return 21;
#else
        if (rename(argv[3], argv[2]) < 0)
            return 21;
#endif
        status = host_snapshot_validate_path(argv[2], &snapshot, &host_error);
        host_snapshot_release(&snapshot);
        return status == HOST_SNAPSHOT_CHANGED ? 0 : 22;
    }
    if (strcmp(argv[1], "status-strings") == 0) {
        return strcmp(host_snapshot_status_string(HOST_SNAPSHOT_TOO_LARGE),
                      "size limit exceeded") == 0 &&
                       strcmp(host_snapshot_status_string(
                                  HOST_SNAPSHOT_CHANGED),
                              "path or contents changed while reading") == 0
                   ? 0
                   : 30;
    }
    return 81;
}
"""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_harness(
    directory: pathlib.Path,
    source_text: str,
    name: str,
    compiler: list[str],
    sanitizer_flags: list[str],
) -> pathlib.Path:
    source = directory / f"{name}.c"
    harness = directory / f"{name}-harness.c"
    output = directory / name
    source.write_text(source_text, encoding="utf-8")
    harness.write_text(HARNESS_SOURCE, encoding="utf-8")
    subprocess.run(
        compiler
        + [
            *sanitizer_flags,
            "-std=gnu11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DHOST_SNAPSHOT_TESTING",
            "-I",
            str(SNAPSHOT_HEADER.parent),
            str(source),
            str(harness),
            "-o",
            str(output),
        ],
        check=True,
        cwd=ROOT,
    )
    return output


def run(
    executable: pathlib.Path,
    mode: str,
    *paths: pathlib.Path,
    sanitizer_flags: list[str],
) -> int:
    return subprocess.run(
        [str(executable), mode, *(str(path) for path in paths)],
        cwd=ROOT,
        env=probe_environment(sanitizer_flags),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).returncode


def replace_once(source: str, old: str, new: str, name: str) -> str:
    require(source.count(old) == 1, f"{name}: mutation anchor drifted")
    return source.replace(old, new, 1)


def main() -> int:
    source = SNAPSHOT_SOURCE.read_text(encoding="utf-8")
    mkfs = MKFS_SOURCE.read_text(encoding="utf-8")
    plain_mkfs = PLAIN_MKFS_SOURCE.read_text(encoding="utf-8")
    makefile = NFS_MAKEFILE.read_text(encoding="utf-8")
    labdemo = LABDEMO_SOURCE.read_text(encoding="utf-8")

    require("invalid host image snapshot" not in mkfs,
            "mkfs still collapses capacity errors into an invalid snapshot")
    require("host_snapshot_read(host_path, (size_t)MAXFILE * BSIZE" in mkfs,
            "mkfs binary snapshots are not bounded by inode capacity")
    for owner, text in (("AgentOS", mkfs), ("Plain", plain_mkfs)):
        require("buf + (off - (fbn * BSIZE))" in text,
                f"{owner} mkfs recreates an out-of-bounds intermediate pointer")
        require("buf + off - (fbn * BSIZE)" not in text,
                f"{owner} mkfs retains undefined pointer arithmetic")
    require(mkfs.count("host_snapshot_validate_path(") >= 3,
            "mkfs does not revalidate the binary/ELF pair")
    require("fs.c host_image_snapshot.c" in makefile,
            "mkfs does not link the stable snapshot owner")
    require("demo_audit_records[AGENT_AUDIT_MAX_RECORDS]" not in labdemo and
            "demo_timeline_records[AGENT_TIMELINE_MAX_RECORDS]" not in labdemo,
            "labdemo still materializes kernel-wide observation tables")

    with tempfile.TemporaryDirectory(prefix="mkfs-snapshot-") as raw_tmp:
        tmp = pathlib.Path(raw_tmp)
        compiler = host_compiler()
        sanitizer_flags = required_sanitizer_flags(compiler, tmp)
        stable = tmp / "stable.bin"
        empty = tmp / "empty.bin"
        missing = tmp / "missing.bin"
        symlink = tmp / "symlink.bin"
        replacement = tmp / "replacement.bin"
        stable.write_bytes(b"stable-content")
        empty.write_bytes(b"")
        symlink.symlink_to(stable.name)
        replacement.write_bytes(b"replacement---")

        executable = compile_harness(
            tmp, source, "snapshot-test", compiler, sanitizer_flags
        )
        cases = [
            ("stable", (stable,)),
            ("limit", (stable,)),
            ("empty", (empty,)),
            ("missing", (missing,)),
            ("changed-read", (stable,)),
            ("status-strings", (stable,)),
        ]
        if symlink.is_symlink():
            cases.insert(4, ("symlink", (symlink,)))
        else:
            symlink.unlink()
            print("[mkfs-snapshot] symlink case skipped: platform lacks real symlinks")
        for mode, paths in cases:
            stable.write_bytes(b"stable-content")
            require(run(
                executable,
                mode,
                *paths,
                sanitizer_flags=sanitizer_flags,
            ) == 0,
                    f"host snapshot regression failed: {mode}")

        stable.write_bytes(b"stable-content")
        require(run(
            executable,
            "changed-content",
            stable,
            sanitizer_flags=sanitizer_flags,
        ) == 0,
                "post-snapshot content mutation was not rejected")
        stable.write_bytes(b"stable-content")
        replacement.write_bytes(b"replacement---")
        require(run(
            executable,
            "changed-path",
            stable,
            replacement,
            sanitizer_flags=sanitizer_flags,
        ) == 0,
                "post-snapshot path replacement was not rejected")

        stability_guard = """\
\tif (!snapshot_fingerprint_same(&fd_before, &fd_middle) ||
\t    !snapshot_fingerprint_same(&fd_before, &fd_after) ||
\t    !snapshot_fingerprint_same(&fd_before, &path_after) ||
\t    memcmp(first, second, snapshot->size) != 0) {"""
        stability_mutant = replace_once(
            source, stability_guard, "\tif (0) {", "stability guard"
        )
        mutant = compile_harness(
            tmp,
            stability_mutant,
            "snapshot-mutant-stable",
            compiler,
            sanitizer_flags,
        )
        stable.write_bytes(b"stable-content")
        require(run(
            mutant,
            "changed-read",
            stable,
            sanitizer_flags=sanitizer_flags,
        ) != 0,
                "mutation survived: unstable two-pass input was accepted")

        limit_guard = "if (limit != 0 && snapshot->size > limit) {"
        limit_mutant = replace_once(
            source, limit_guard, "if ((void)limit, 0) {", "capacity guard"
        )
        mutant = compile_harness(
            tmp,
            limit_mutant,
            "snapshot-mutant-limit",
            compiler,
            sanitizer_flags,
        )
        stable.write_bytes(b"stable-content")
        require(run(
            mutant,
            "limit",
            stable,
            sanitizer_flags=sanitizer_flags,
        ) != 0,
                "mutation survived: oversized input was accepted")

        path_guard = """\
\tunchanged = snapshot_fingerprint_same(&snapshot->fingerprint,
\t\t\t\t\t      &current.fingerprint) &&
\t\t    snapshot->size == current.size &&
\t\t    memcmp(snapshot->data, current.data, snapshot->size) == 0;"""
        path_mutant = replace_once(
            source, path_guard, "\tunchanged = 1;", "path guard"
        )
        mutant = compile_harness(
            tmp,
            path_mutant,
            "snapshot-mutant-path",
            compiler,
            sanitizer_flags,
        )
        stable.write_bytes(b"stable-content")
        replacement.write_bytes(b"replacement---")
        require(run(
            mutant,
            "changed-path",
            stable,
            replacement,
            sanitizer_flags=sanitizer_flags,
        ) != 0,
                "mutation survived: replaced path was accepted")

    print(
        "mkfs host snapshot tests: 10 regressions, 3 mutations PASS; "
        f"mode={probe_mode(sanitizer_flags)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
