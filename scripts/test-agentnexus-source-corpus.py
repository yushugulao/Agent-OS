#!/usr/bin/env python3
"""Boundary, determinism and mutation tests for the Nexus source corpus."""

from __future__ import annotations

import importlib.util
import hashlib
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/build-agentnexus-source-corpus.py"
SPEC = importlib.util.spec_from_file_location("agentnexus_source_corpus", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CORPUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORPUS)
NEXUS_LIBRARY = (ROOT / "user/lib/agent_nexus.c").read_text(encoding="utf-8")
NFS_BUILDER = (ROOT / "nfs/fs.c").read_text(encoding="utf-8")
VFS_SECURITY = (ROOT / "os/vfs_security.c").read_text(encoding="utf-8")
VFS_RUNTIME_TEST = (ROOT / "user/src/agentvfs_probe.c").read_text(
    encoding="utf-8"
)


class SourceCorpusTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        for directory in CORPUS.ALLOWLIST:
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "os/zeta.c").write_bytes(b"int zeta(void) {\r\n\treturn 7;\r\n}\r\n")
        (root / "include/alpha.h").write_text("#define ALPHA 1\n", encoding="utf-8")
        (root / "user/lib/tool.c").write_text("const char *tool = \"evidence\";\n", encoding="utf-8")
        (root / "user/include/tool.h").write_text("int tool_read(void);\n", encoding="utf-8")

    @staticmethod
    def corpus_bytes(directory: Path) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}

    def test_deterministic_sorted_normalized_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            first = base / "first"
            second = base / "second"
            first_anchor = base / "first-anchor.h"
            second_anchor = base / "second-anchor.h"
            self.fixture(root)
            summary = CORPUS.build_corpus(root, first, first_anchor)
            CORPUS.build_corpus(root, second, second_anchor)
            self.assertEqual(self.corpus_bytes(first), self.corpus_bytes(second))
            self.assertEqual(first_anchor.read_bytes(), second_anchor.read_bytes())
            self.assertEqual(summary["scope"], "build_source_snapshot")
            self.assertEqual(summary["sources"], 4)
            joined = b"".join(self.corpus_bytes(first).values())
            self.assertNotIn(b"\r", joined)
            self.assertEqual(CORPUS.verify_corpus(first)["revision"], summary["revision"])
            CORPUS.verify_anchor(first, first_anchor)

    def test_self_consistent_replacement_fails_compiled_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            original = base / "original"
            replacement = base / "replacement"
            anchor = base / "compiled-anchor.h"
            self.fixture(root)
            CORPUS.build_corpus(root, original, anchor)
            (root / "os/zeta.c").write_text(
                "int zeta(void) { return 999; }\n", encoding="utf-8"
            )
            CORPUS.build_corpus(root, replacement)
            CORPUS.verify_corpus(replacement)
            with self.assertRaisesRegex(CORPUS.CorpusError, "compiled anchor"):
                CORPUS.verify_anchor(replacement, anchor)

    def test_guest_incremental_sha256_known_vectors(self) -> None:
        compiler = shutil.which("cc") or shutil.which("gcc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            include_dir = base / "user/include"
            shutil.copytree(ROOT / "user/include", include_dir)
            shutil.copytree(ROOT / "include", base / "include")
            (base / "user/lib").mkdir(parents=True)
            shutil.copyfile(
                ROOT / "user/lib/agent_nexus_source.c",
                base / "user/lib/agent_nexus_source.c",
            )
            (base / "agent_nexus_source_anchor.h").write_text(
                "#define AGENT_NEXUS_SOURCE_ANCHOR_SCOPE \"build_source_snapshot\"\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_ALLOWLIST "
                "\"os/,include/,user/lib/,user/include/\"\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_REVISION \"\"\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_MANIFEST_SHA256 \"\"\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_COUNT 0U\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_VOLUME_COUNT 0U\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_BYTES 0ULL\n",
                encoding="ascii",
            )
            harness = base / "sha-vectors.c"
            harness.write_text(
                textwrap.dedent(
                    f"""
                    #include \"user/lib/agent_nexus_source.c\"

                    void agent_nexus_sha256(const void *data, unsigned int length,
                                            unsigned char digest[32])
                    {{
                        struct nxs_sha256_context context;
                        nxs_sha_init(&context);
                        nxs_sha_update(&context, data, length);
                        nxs_sha_final(&context, digest);
                    }}

                    void agent_nexus_sha256_hex(const unsigned char digest[32],
                                                char text[65])
                    {{
                        static const char hex[] = \"0123456789abcdef\";
                        for (unsigned int i = 0; i < 32; i++) {{
                            text[i * 2] = hex[digest[i] >> 4];
                            text[i * 2 + 1] = hex[digest[i] & 15];
                        }}
                        text[64] = 0;
                    }}

                    static int vector(const unsigned char *data, unsigned int length,
                                      const char *expected)
                    {{
                        struct nxs_sha256_context context;
                        unsigned char digest[32];
                        char actual[65];
                        unsigned int offset = 0;
                        nxs_sha_init(&context);
                        while (offset < length) {{
                            unsigned int amount = length - offset;
                            if (amount > 13U)
                                amount = 13U;
                            nxs_sha_update(&context, data + offset, amount);
                            offset += amount;
                        }}
                        nxs_sha_final(&context, digest);
                        agent_nexus_sha256_hex(digest, actual);
                        return strcmp(actual, expected) == 0;
                    }}

                    int main(void)
                    {{
                        static const unsigned char empty[] = \"\";
                        static const unsigned char abc[] = \"abc\";
                        static const unsigned char many[] =
                            \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"
                            \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"
                            \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"
                            \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\";
                        if (!vector(empty, 0,
                            \"e3b0c44298fc1c149afbf4c8996fb924\"
                            \"27ae41e4649b934ca495991b7852b855\"))
                            return 1;
                        if (!vector(abc, 3,
                            \"ba7816bf8f01cfea414140de5dae2223\"
                            \"b00361a396177a9cb410ff61f20015ad\"))
                            return 2;
                        if (!vector(many, sizeof(many) - 1U,
                            \"c2a908d98f5df987ade41b5fce213067\"
                            \"efbcc21ef2240212a41e54b5e7c28ae5\"))
                            return 3;
                        return 0;
                    }}
                    """
                ),
                encoding="utf-8",
            )
            executable = base / ("sha-vectors.exe" if os.name == "nt" else "sha-vectors")
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-O2",
                    "-D__riscv_xlen=64",
                    f"-I{base}",
                    f"-I{include_dir}",
                    str(harness),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            subprocess.run([str(executable)], check=True)

    def test_guest_source_scanner_riscv_frames_are_bounded(self) -> None:
        compiler = shutil.which("riscv64-linux-gnu-gcc")
        if compiler is None:
            self.skipTest("GNU RISC-V compiler unavailable")
        makefile = (ROOT / "user/Makefile").read_text(encoding="utf-8")
        budget_match = re.search(
            r"override NEXUS_SOURCE_STACK_FRAME_MAX := ([0-9]+)", makefile
        )
        hot_budget_match = re.search(
            r"override NEXUS_SOURCE_STACK_HOT_FRAME_MAX := ([0-9]+)",
            makefile,
        )
        self.assertIsNotNone(budget_match)
        self.assertIsNotNone(hot_budget_match)
        assert budget_match is not None
        assert hot_budget_match is not None
        frame_budget = int(budget_match.group(1))
        hot_frame_budget = int(hot_budget_match.group(1))
        self.assertLessEqual(frame_budget, 512)
        self.assertLessEqual(hot_frame_budget, 192)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "agent_nexus_source_anchor.h").write_text(
                "#define AGENT_NEXUS_SOURCE_ANCHOR_SCOPE "
                '"build_source_snapshot"\n'
                "#define AGENT_NEXUS_SOURCE_ANCHOR_ALLOWLIST "
                '"os/,include/,user/lib/,user/include/"\n'
                '#define AGENT_NEXUS_SOURCE_ANCHOR_REVISION ""\n'
                '#define AGENT_NEXUS_SOURCE_ANCHOR_MANIFEST_SHA256 ""\n'
                "#define AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_COUNT 0U\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_VOLUME_COUNT 0U\n"
                "#define AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_BYTES 0ULL\n",
                encoding="ascii",
            )
            obj = base / "agent_nexus_source.o"
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=gnu11",
                    "-march=rv64imac_zicsr_zifencei",
                    "-mabi=lp64",
                    "-mcmodel=medany",
                    "-fno-builtin",
                    "-nostdinc",
                    "-fno-stack-protector",
                    "-Wall",
                    "-Os",
                    "-fno-pie",
                    "-static",
                    "-fstack-usage",
                    "-fcallgraph-info=su",
                    f"-Werror=frame-larger-than={frame_budget}",
                    f"-I{base}",
                    f"-I{ROOT / 'user/include'}",
                    "-c",
                    str(ROOT / "user/lib/agent_nexus_source.c"),
                    "-o",
                    str(obj),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            usage = obj.with_suffix(".su")
            self.assertTrue(usage.is_file())
            frames: dict[str, int] = {}
            for line in usage.read_text(encoding="utf-8").splitlines():
                fields = line.split("\t")
                self.assertEqual(len(fields), 3, line)
                function = fields[0].rsplit(":", 1)[-1]
                frames[function] = int(fields[1])

            def compiled_frame(name: str) -> int:
                matches = [
                    size
                    for function, size in frames.items()
                    if function == name or function.startswith(name + ".")
                ]
                self.assertEqual(len(matches), 1, (name, frames))
                return matches[0]

            for function in (
                "nxs_scan_corpus",
                "nxs_search_emit",
                "agent_nexus_source_init",
                "agent_nexus_source_search",
                "agent_nexus_source_read",
            ):
                self.assertLessEqual(compiled_frame(function), hot_frame_budget)
            self.assertLessEqual(max(frames.values()), frame_budget)

    def test_guest_scanner_search_read_and_workspace_guard(self) -> None:
        compiler = shutil.which("cc") or shutil.which("gcc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            corpus = base / "corpus"
            include_dir = base / "user/include"
            self.fixture(root)
            shutil.copytree(ROOT / "user/include", include_dir)
            shutil.copytree(ROOT / "include", base / "include")
            (base / "user/lib").mkdir(parents=True)
            shutil.copyfile(
                ROOT / "user/lib/agent_nexus_source.c",
                base / "user/lib/agent_nexus_source.c",
            )
            CORPUS.build_corpus(
                root, corpus, base / "agent_nexus_source_anchor.h"
            )
            harness = base / "scanner-roundtrip.c"
            harness.write_text(
                textwrap.dedent(
                    """
                    #ifdef _WIN32
                    #define open nxs_test_open
                    #endif
                    #include "user/lib/agent_nexus_source.c"

                    #ifdef _WIN32
                    #undef open
                    extern int _open(const char *, int, ...);

                    int nxs_test_open(const char *path, int flags)
                    {
                        return _open(path, flags | 0x8000);
                    }
                    #endif

                    void agent_nexus_sha256(const void *data, unsigned int length,
                                            unsigned char digest[32])
                    {
                        struct nxs_sha256_context context;
                        nxs_sha_init(&context);
                        nxs_sha_update(&context, data, length);
                        nxs_sha_final(&context, digest);
                    }

                    void agent_nexus_sha256_hex(const unsigned char digest[32],
                                                char text[65])
                    {
                        static const char hex[] = "0123456789abcdef";
                        for (unsigned int i = 0; i < 32; i++) {
                            text[i * 2] = hex[digest[i] >> 4];
                            text[i * 2 + 1] = hex[digest[i] & 15];
                        }
                        text[64] = 0;
                    }

                    int main(void)
                    {
                        static struct agent_nexus_source_search_result search;
                        static struct agent_nexus_source_read_result read_result;
                        static char content[128];
                        int status;

                        if (agent_nexus_source_init() != AGENT_NEXUS_SOURCE_OK)
                            return 1;
                        status = agent_nexus_source_search(
                            "EVIDENCE", "user/lib/", &search);
                        if (status != AGENT_NEXUS_SOURCE_OK ||
                            search.match_count != 1 ||
                            search.scanned_source_count != 4 ||
                            !search.content_untrusted ||
                            strcmp(search.matches[0].path,
                                   "user/lib/tool.c") != 0)
                            return 2;
                        status = agent_nexus_source_read(
                            search.matches[0].source_id,
                            search.matches[0].line, 1, content,
                            sizeof(content), &read_result);
                        if (status != AGENT_NEXUS_SOURCE_OK ||
                            !read_result.content_untrusted ||
                            strcmp(content,
                                   "const char *tool = \\\"evidence\\\";\\n") != 0 ||
                            strcmp(read_result.citation,
                                   search.matches[0].citation) != 0)
                            return 3;
                        if (agent_nexus_source_search(
                                "definitely-absent", "", &search) !=
                            AGENT_NEXUS_SOURCE_NOT_FOUND)
                            return 4;

                        __atomic_store_n(&nxs_work.active, 1U,
                                         __ATOMIC_RELEASE);
                        status = agent_nexus_source_search(
                            "evidence", "user/lib/", &search);
                        __atomic_store_n(&nxs_work.active, 0U,
                                         __ATOMIC_RELEASE);
                        if (status != AGENT_NEXUS_SOURCE_NOT_READY)
                            return 5;
                        if (agent_nexus_source_search(
                                "evidence", "user/lib/", &search) !=
                            AGENT_NEXUS_SOURCE_OK)
                            return 6;
                        return 0;
                    }
                    """
                ),
                encoding="utf-8",
            )
            executable = base / (
                "scanner-roundtrip.exe" if os.name == "nt" else "scanner-roundtrip"
            )
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-O2",
                    "-D__riscv_xlen=64",
                    f"-I{base}",
                    f"-I{include_dir}",
                    str(harness),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)], cwd=corpus, capture_output=True
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)

    def test_manifest_and_volume_mutations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            output = base / "out"
            self.fixture(root)
            CORPUS.build_corpus(root, output)
            manifest = output / CORPUS.MANIFEST_NAME
            data = bytearray(manifest.read_bytes())
            data[-1] ^= 1
            manifest.write_bytes(data)
            with self.assertRaisesRegex(CORPUS.CorpusError, "digest"):
                CORPUS.verify_corpus(output)

            CORPUS.build_corpus(root, output)
            volume = output / f"{CORPUS.VOLUME_PREFIX}000"
            data = bytearray(volume.read_bytes())
            data[-1] ^= 1
            volume.write_bytes(data)
            with self.assertRaisesRegex(CORPUS.CorpusError, "integrity"):
                CORPUS.verify_corpus(output)

            CORPUS.build_corpus(root, output)
            (output / f"{CORPUS.VOLUME_PREFIX}999").write_bytes(b"stale")
            with self.assertRaisesRegex(CORPUS.CorpusError, "undeclared"):
                CORPUS.verify_corpus(output)

            CORPUS.build_corpus(root, output)
            (output / "foreign-file").write_bytes(b"stale")
            with self.assertRaisesRegex(CORPUS.CorpusError, "undeclared"):
                CORPUS.verify_corpus(output)

    def test_verifier_rejects_resigned_noncanonical_fields(self) -> None:
        def resign_manifest(output: Path, manifest: bytearray) -> None:
            descriptors = manifest[CORPUS.MANIFEST_HEADER_SIZE:]
            manifest[72:104] = hashlib.sha256(descriptors).digest()
            (output / CORPUS.MANIFEST_NAME).write_bytes(manifest)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            output = base / "out"
            self.fixture(root)
            CORPUS.build_corpus(root, output)
            manifest = bytearray((output / CORPUS.MANIFEST_NAME).read_bytes())
            manifest[127] = 1
            resign_manifest(output, manifest)
            with self.assertRaisesRegex(CORPUS.CorpusError, "scope"):
                CORPUS.verify_corpus(output)

            CORPUS.build_corpus(root, output)
            manifest = bytearray((output / CORPUS.MANIFEST_NAME).read_bytes())
            manifest[CORPUS.MANIFEST_HEADER_SIZE + 15] = 1
            resign_manifest(output, manifest)
            with self.assertRaisesRegex(CORPUS.CorpusError, "volume name"):
                CORPUS.verify_corpus(output)

    def test_verifier_rejects_resigned_noncanonical_record(self) -> None:
        def resign_volume(output: Path, volume: bytearray) -> None:
            prefix = volume[:64]
            volume[64:96] = hashlib.sha256(prefix).digest()
            volume_path = output / f"{CORPUS.VOLUME_PREFIX}000"
            volume_path.write_bytes(volume)
            manifest_path = output / CORPUS.MANIFEST_NAME
            manifest = bytearray(manifest_path.read_bytes())
            descriptor = CORPUS.MANIFEST_HEADER_SIZE
            manifest[descriptor + 24:descriptor + 56] = hashlib.sha256(volume).digest()
            descriptors = manifest[CORPUS.MANIFEST_HEADER_SIZE:]
            manifest[72:104] = hashlib.sha256(descriptors).digest()
            manifest_path.write_bytes(manifest)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            output = base / "out"
            self.fixture(root)
            CORPUS.build_corpus(root, output)
            path = output / f"{CORPUS.VOLUME_PREFIX}000"
            volume = bytearray(path.read_bytes())
            volume[15] = 1
            resign_volume(output, volume)
            with self.assertRaisesRegex(CORPUS.CorpusError, "identity"):
                CORPUS.verify_corpus(output)

            CORPUS.build_corpus(root, output)
            volume = bytearray(path.read_bytes())
            path_size = struct.unpack_from("<I", volume, 16)[0]
            record_path = CORPUS.RECORD_HEADER_SIZE
            original = volume[record_path:record_path + path_size]
            slash = original.index(ord("/"))
            self.assertGreater(slash, 0)
            volume[record_path + slash] = ord("+")
            resign_volume(output, volume)
            with self.assertRaisesRegex(CORPUS.CorpusError, "path"):
                CORPUS.verify_corpus(output)

    def test_build_rejects_undeclared_output_and_nonportable_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            output = base / "out"
            self.fixture(root)
            output.mkdir()
            (output / "foreign-file").write_bytes(b"must not be installed")
            with self.assertRaisesRegex(CORPUS.CorpusError, "undeclared"):
                CORPUS.build_corpus(root, output)
            self.assertEqual(
                (output / "foreign-file").read_bytes(), b"must not be installed"
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / "os/a+b.c").write_text("int plus;\n", encoding="utf-8")
            with self.assertRaisesRegex(CORPUS.CorpusError, "non-portable"):
                CORPUS.build_corpus(root, root / "out")

    def test_source_symlink_cannot_escape_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            outside = base / "outside.c"
            self.fixture(root)
            outside.write_text("const char *secret = \"outside\";\n", encoding="utf-8")
            link = root / "os/leak.c"
            try:
                os.symlink(outside, link)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            with self.assertRaisesRegex(CORPUS.CorpusError, "symlink"):
                CORPUS.build_corpus(root, root / "out")

    def test_source_junction_cannot_cross_allowlist(self) -> None:
        if os.name != "nt":
            self.skipTest("NTFS junction regression is Windows-only")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            self.fixture(root)
            target = root / "user/src"
            target.mkdir(parents=True)
            (target / "secret.c").write_text(
                "const char *secret = \"outside allowlist\";\n", encoding="utf-8"
            )
            junction = root / "os/alias"
            creation = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            if creation.returncode != 0:
                self.skipTest(f"junction creation unavailable: {creation.stderr.strip()}")
            try:
                self.assertTrue(junction.is_junction())
                with self.assertRaisesRegex(CORPUS.CorpusError, "junction"):
                    CORPUS.build_corpus(root, root / "out")
            finally:
                os.rmdir(junction)

    def test_verifier_rejects_symlinked_corpus_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            output = base / "out"
            external = base / "external"
            self.fixture(root)
            CORPUS.build_corpus(root, output)
            volume = output / f"{CORPUS.VOLUME_PREFIX}000"
            external.write_bytes(volume.read_bytes())
            volume.unlink()
            try:
                os.symlink(external, volume)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            with self.assertRaisesRegex(CORPUS.CorpusError, "regular file"):
                CORPUS.verify_corpus(output)

    def test_rejects_encoding_path_line_and_record_boundaries(self) -> None:
        cases = (
            ("empty", b"", "empty source"),
            ("nul", b"x\0y", "NUL"),
            ("utf8", b"\xff\n", "UTF-8"),
            ("line", b"x" * (CORPUS.MAX_LINE_BYTES + 1), "line exceeds"),
        )
        for name, contents, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root)
                (root / "os/zeta.c").write_bytes(contents)
                with self.assertRaisesRegex(CORPUS.CorpusError, message):
                    CORPUS.build_corpus(root, root / "out")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            long_name = "x" * (CORPUS.MAX_PATH_BYTES + 1 - len("user/include/")) + ".h"
            (root / "user/include" / long_name).write_text("x\n", encoding="utf-8")
            with self.assertRaisesRegex(CORPUS.CorpusError, "path length"):
                CORPUS.build_corpus(root, root / "out")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            content = b"x\n" * (CORPUS.VOLUME_MAX_BYTES // 2)
            (root / "os/zeta.c").write_bytes(content)
            with self.assertRaisesRegex(CORPUS.CorpusError, "record exceeds"):
                CORPUS.build_corpus(root, root / "out")

    def test_real_repository_pack_fits_ucore_image_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "corpus"
            summary = CORPUS.build_corpus(ROOT, output)
            self.assertGreater(summary["sources"], 150)
            self.assertLess(summary["corpus_bytes"], CORPUS.CORPUS_MAX_BYTES)
            for path in output.iterdir():
                self.assertLessEqual(len(path.name), 14)
                if path.name != CORPUS.MANIFEST_NAME:
                    self.assertLessEqual(path.stat().st_size, CORPUS.VOLUME_MAX_BYTES)
            self.assertEqual(CORPUS.verify_corpus(output)["manifest_sha256"],
                             summary["manifest_sha256"])

    def test_specialist_artifact_roles_are_kind_exact(self) -> None:
        # System and Research cannot publish owned artifacts.  Their verified
        # results are brokered by the Coordinator while preserving the worker
        # as producer; only Analyst owns its report directly.
        owned = NEXUS_LIBRARY[
            NEXUS_LIBRARY.index("static int nexus_owned_manifest_valid(") :
            NEXUS_LIBRARY.index("static int nexus_brokered_manifest_valid(")
        ]
        brokered = NEXUS_LIBRARY[
            NEXUS_LIBRARY.index("static int nexus_brokered_manifest_valid(") :
            NEXUS_LIBRARY.index("static int nexus_manifest_relationship_valid(")
        ]
        relationship = NEXUS_LIBRARY[
            NEXUS_LIBRARY.index("static int nexus_manifest_relationship_valid(") :
            NEXUS_LIBRARY.index("static void nexus_header_from_manifest(")
        ]
        self.assertNotIn("AGENT_NEXUS_ROLE_SYSTEM", owned)
        self.assertNotIn("AGENT_NEXUS_ROLE_RESEARCH", owned)
        self.assertIn("AGENT_NEXUS_ROLE_ANALYST", owned)
        self.assertIn("AGENT_NEXUS_ARTIFACT_REPORT", owned)
        for body in (brokered, relationship):
            self.assertIn("AGENT_NEXUS_ROLE_COORDINATOR", body)
            self.assertIn("AGENT_NEXUS_ROLE_SYSTEM", body)
            self.assertIn("AGENT_NEXUS_ARTIFACT_SYSTEM_RESULT", body)
            self.assertIn("AGENT_NEXUS_ROLE_RESEARCH", body)
            self.assertIn("AGENT_NEXUS_ARTIFACT_RESEARCH_RESULT", body)
        self.assertNotIn("AGENT_NEXUS_ARTIFACT_SEED", NEXUS_LIBRARY)
        self.assertIn("kind >= AGENT_NEXUS_ARTIFACT_TOOL_INPUT", NEXUS_LIBRARY)
        self.assertIn("strlen(shortname) > DIRSIZ", NFS_BUILDER)
        for field in (
            "VFS_LABEL_F_PROTECTED",
            "VFS_SCOPE_SYSTEM",
            "VFS_POLICY_WORKFLOW",
            "VFS_EXEC_PROFILE_NONE",
        ):
            self.assertIn(field, NFS_BUILDER)
        self.assertIn(
            "return (op == VFS_OP_LOOKUP || op == VFS_OP_READ)", VFS_SECURITY
        )
        for denial in (
            "delegated write denied",
            "delegated truncate denied",
            "delegated unlink denied",
        ):
            self.assertIn(denial, VFS_RUNTIME_TEST)


if __name__ == "__main__":
    unittest.main()
