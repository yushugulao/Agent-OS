#!/usr/bin/env python3
"""Focused integrity and token tests for Host source attestation."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_source_attestation as attestation


_BUILDER_PATH = _ROOT / "scripts" / "build-agentnexus-source-corpus.py"
_BUILDER_SPEC = importlib.util.spec_from_file_location(
    "test_agentnexus_source_corpus_builder", _BUILDER_PATH
)
assert _BUILDER_SPEC is not None and _BUILDER_SPEC.loader is not None
_BUILDER = importlib.util.module_from_spec(_BUILDER_SPEC)
_BUILDER_SPEC.loader.exec_module(_BUILDER)


class SourceAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source_root = self.base / "root"
        self.corpus = self.base / "corpus"
        self._write_fixture(self.source_root)
        self.summary = _BUILDER.build_corpus(self.source_root, self.corpus)
        self.loaded = self._load(self.corpus)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_fixture(root: Path) -> None:
        for directory in _BUILDER.ALLOWLIST:
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "include/a.h").write_bytes(b"#define A 1\n")
        (root / "os/a.c").write_bytes(b"first\n\xc2\xb5-second\nlast")
        (root / "user/include/z.h").write_bytes(b"line1\n\nline3\n")
        (root / "user/lib/many.c").write_bytes(
            b"".join(
                f"line-{index}-{'x' * 170}\n".encode("ascii")
                for index in range(1, 16)
            )
        )
        (root / "user/lib/tool.c").write_bytes(b"one\r\ntwo\r\n")

    def _load(
        self,
        corpus: Path,
        *,
        revision: str | None = None,
        manifest: str | None = None,
    ) -> attestation.SourceAttestation:
        return attestation.load_source_attestation(
            corpus,
            expected_revision=revision or str(self.summary["revision"]),
            expected_manifest_sha256=manifest or str(self.summary["manifest_sha256"]),
        )

    @staticmethod
    def _record_by_path(
        loaded: attestation.SourceAttestation, path: str
    ) -> attestation.SourceRecord:
        return next(record for record in loaded.sources.values() if record.path == path)

    @staticmethod
    def _chunk(
        record: attestation.SourceRecord, start_line: int, end_line: int
    ) -> bytes:
        start = record.line_starts[start_line - 1]
        end = (
            record.line_starts[end_line]
            if end_line < record.line_count
            else len(record.content)
        )
        return record.content[start:end]

    def _event(
        self,
        record: attestation.SourceRecord,
        start_line: int,
        end_line: int,
        *,
        loaded: attestation.SourceAttestation | None = None,
    ) -> dict[str, object]:
        corpus = loaded or self.loaded
        replay = corpus.attest_read(record.source_id, start_line, end_line)
        return {
            "scope": attestation.SCOPE,
            "corpus_revision": corpus.corpus_revision,
            "manifest_sha256": corpus.manifest_sha256,
            "source_id": record.source_id,
            "path": record.path,
            "start_line": start_line,
            "end_line": end_line,
            "citation": (
                f"[{record.source_id}:L{start_line}-L{end_line}]"
            ),
            "full_sha256": record.full_sha256,
            "chunk_sha256": hashlib.sha256(
                self._chunk(record, start_line, end_line)
            ).hexdigest(),
            "artifact_sha256": replay.artifact_sha256,
            "turn_id": 17,
            "projection_sha256": replay.projection_sha256,
        }

    def _clone(self, name: str) -> Path:
        destination = self.base / name
        shutil.copytree(self.corpus, destination)
        return destination

    @staticmethod
    def _resign_volume(corpus: Path, volume_name: str) -> str:
        volume_index = int(volume_name.removeprefix(attestation.VOLUME_PREFIX))
        volume = (corpus / volume_name).read_bytes()
        manifest_path = corpus / attestation.MANIFEST_NAME
        manifest = bytearray(manifest_path.read_bytes())
        descriptor = (
            attestation.MANIFEST_HEADER_SIZE
            + volume_index * attestation.VOLUME_DESCRIPTOR_SIZE
        )
        struct.pack_into("<I", manifest, descriptor + 16, len(volume))
        manifest[descriptor + 24:descriptor + 56] = hashlib.sha256(volume).digest()
        manifest[72:104] = hashlib.sha256(
            manifest[attestation.MANIFEST_HEADER_SIZE:]
        ).digest()
        manifest_path.write_bytes(manifest)
        return hashlib.sha256(manifest).hexdigest()

    @staticmethod
    def _locate_record(corpus: Path, wanted_path: str) -> tuple[Path, int, int, int]:
        for volume_path in sorted(corpus.glob("nxsrc[0-9][0-9][0-9]")):
            volume = volume_path.read_bytes()
            offset = 0
            while offset < len(volume):
                path_size, content_size = struct.unpack_from("<II", volume, offset + 16)
                path_start = offset + attestation.RECORD_HEADER_SIZE
                content_start = path_start + path_size
                path = volume[path_start:content_start].decode("ascii")
                if path == wanted_path:
                    return volume_path, offset, path_start, content_start
                offset = content_start + content_size
        raise AssertionError(f"record not found: {wanted_path}")

    def test_loads_immutable_snapshot_and_verifies_exact_lf_ranges(self) -> None:
        self.assertEqual(self.loaded.corpus_revision, self.summary["revision"])
        self.assertEqual(self.loaded.manifest_sha256, self.summary["manifest_sha256"])
        self.assertEqual(self.loaded.source_count, 5)
        self.assertEqual(self.loaded.source_bytes, self.summary["source_bytes"])
        self.assertEqual(self.loaded.volume_count, self.summary["volumes"])
        with self.assertRaises(TypeError):
            self.loaded.sources["S9999"] = next(iter(self.loaded.sources.values()))

        record = self._record_by_path(self.loaded, "os/a.c")
        self.assertEqual(record.content, b"first\n\xc2\xb5-second\nlast")
        event = self._event(record, 2, 3)
        self.assertTrue(self.loaded.verify_evidence_event(event))
        self.assertTrue(
            self.loaded.verify_read(
                event["source_id"],
                event["path"],
                event["start_line"],
                event["end_line"],
                event["citation"],
                event["full_sha256"],
                event["chunk_sha256"],
            )
        )

        trailing = self._record_by_path(self.loaded, "include/a.h")
        self.assertEqual(trailing.line_count, 1)
        self.assertTrue(self.loaded.verify_evidence_event(self._event(trailing, 1, 1)))
        beyond = self._event(trailing, 1, 1)
        beyond["end_line"] = 2
        beyond["citation"] = f"[{trailing.source_id}:L1-L2]"
        self.assertFalse(self.loaded.verify_evidence_event(beyond))

        blank = self._record_by_path(self.loaded, "user/include/z.h")
        blank_event = self._event(blank, 2, 2)
        self.assertEqual(self._chunk(blank, 2, 2), b"\n")
        self.assertTrue(self.loaded.verify_evidence_event(blank_event))

        many = self._record_by_path(self.loaded, "user/lib/many.c")
        self.assertTrue(self.loaded.verify_evidence_event(self._event(many, 1, 12)))
        overlong = self._event(many, 1, 12)
        overlong["end_line"] = 13
        overlong["citation"] = f"[{many.source_id}:L1-L13]"
        self.assertFalse(self.loaded.verify_evidence_event(overlong))

    def test_verify_event_rejects_each_forged_or_noncanonical_field(self) -> None:
        record = self._record_by_path(self.loaded, "os/a.c")
        valid = self._event(record, 1, 2)
        mutations = {
            "scope": "full_repository",
            "corpus_revision": "0" * 64,
            "manifest_sha256": "1" * 64,
            "source_id": "S9999",
            "path": "os/other.c",
            "start_line": True,
            "end_line": 4,
            "citation": f"[{record.source_id}:L1-L3]",
            "full_sha256": record.full_sha256.upper(),
            "chunk_sha256": "f" * 64,
            "artifact_sha256": "d" * 64,
            "projection_sha256": "e" * 64,
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                forged = dict(valid)
                forged[field] = replacement
                self.assertFalse(self.loaded.verify_evidence_event(forged))
        missing = dict(valid)
        del missing["chunk_sha256"]
        self.assertFalse(self.loaded.verify_evidence_event(missing))
        missing_artifact = dict(valid)
        del missing_artifact["artifact_sha256"]
        self.assertFalse(self.loaded.verify_evidence_event(missing_artifact))
        artifact_only = dict(valid)
        artifact_only["artifact_sha256"] = "a" * 64
        self.assertFalse(self.loaded.verify_evidence_event(artifact_only))
        self.assertFalse(self.loaded.verify_evidence_event("not-a-mapping"))

    def test_attest_read_reconstructs_exact_projection_and_canonical_root(self) -> None:
        record = self._record_by_path(self.loaded, "os/a.c")
        replay = self.loaded.attest_read(record.source_id, 2, 3)
        self.assertEqual(replay.content, b"\xc2\xb5-second\nlast")
        expected_projection = (
            "scope=build_source_snapshot\n"
            "bounded=1\n"
            "allowlist=os/,include/,user/lib/,user/include/\n"
            "content_untrusted=1\n"
            f"citation=[{record.source_id}:L2-L3]\n"
            f"source_id={record.source_id}\n"
            f"path={record.path}\n"
            "start_line=2\n"
            "end_line=3\n"
            f"revision={self.loaded.corpus_revision}\n"
            f"manifest_sha256={self.loaded.manifest_sha256}\n"
            f"full_sha256={record.full_sha256}\n"
            f"chunk_sha256={hashlib.sha256(replay.content).hexdigest()}\n"
            "--- source data ---\n"
            "µ-second\nlast"
        )
        self.assertEqual(replay.projection, expected_projection)
        self.assertEqual(
            replay.projection_sha256,
            hashlib.sha256(expected_projection.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(replay.artifact_sha256, replay.projection_sha256)
        self.assertRegex(replay.corpus_bound_sha256, r"^[0-9a-f]{64}$")

        first = replay.evidence_binding(7, 101, 8)
        second_record = self._record_by_path(self.loaded, "include/a.h")
        second = self.loaded.attest_read(
            second_record.source_id, 1, 1
        ).evidence_binding(9, 102, 8)
        root = attestation.canonical_evidence_root([first, second])
        self.assertRegex(root, r"^[0-9a-f]{64}$")
        self.assertEqual(root, attestation.canonical_evidence_root([first, second]))
        artifact_mutation = dict(first)
        artifact_mutation["artifact_sha256"] = "f" * 64
        with self.assertRaises(attestation.SourceAttestationError):
            attestation.canonical_evidence_root([artifact_mutation])
        matched_mutation = dict(first)
        matched_mutation["artifact_sha256"] = "f" * 64
        matched_mutation["projection_sha256"] = "f" * 64
        self.assertNotEqual(
            root,
            attestation.canonical_evidence_root([matched_mutation, second]),
        )
        with self.assertRaises(attestation.SourceAttestationError):
            attestation.canonical_evidence_root([second, first])
        with self.assertRaises(attestation.SourceAttestationError):
            attestation.canonical_evidence_root([first, first])
        reordered = {key: first[key] for key in reversed(tuple(first))}
        with self.assertRaises(attestation.SourceAttestationError):
            attestation.canonical_evidence_root([reordered])

    def test_attest_search_replays_ascii_fold_prefix_order_and_truncation(self) -> None:
        search = self.loaded.attest_search("LiNe", "user/lib/")
        self.assertGreater(len(search.matches), 0)
        self.assertLess(len(search.matches), 8)
        self.assertTrue(search.truncated)
        self.assertEqual(search.matches[0].path, "user/lib/many.c")
        self.assertEqual(search.matches[0].line, 1)
        self.assertEqual(search.matches[0].snippet, f"line-1-{'x' * 170}")
        self.assertIn("query=LiNe\npath_prefix=user/lib/\n", search.projection)
        self.assertIn(
            f"match_count={len(search.matches)}\ntruncated=1\n",
            search.projection,
        )
        self.assertEqual(
            search.projection_sha256,
            hashlib.sha256(search.projection.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(search.trust, "discovery")

        path_search = self.loaded.attest_search("A.C")
        self.assertEqual(len(path_search.matches), 1)
        self.assertEqual(path_search.matches[0].path, "os/a.c")
        self.assertEqual(path_search.matches[0].line, 1)
        self.assertEqual(
            path_search.matches[0].chunk_sha256,
            hashlib.sha256(b"first\n").hexdigest(),
        )
        with self.assertRaises(attestation.SourceAttestationError):
            self.loaded.attest_search("does-not-exist")
        with self.assertRaises(attestation.SourceAttestationError):
            self.loaded.attest_search("line", "../os")
        with self.assertRaises(attestation.SourceAttestationError):
            self.loaded.attest_search("line\n")

    def test_anchor_is_mandatory_canonical_and_rejects_replacement(self) -> None:
        with self.assertRaises(attestation.SourceAttestationError):
            attestation.load_source_attestation(
                self.corpus,
                expected_revision=str(self.summary["revision"]).upper(),
                expected_manifest_sha256=str(self.summary["manifest_sha256"]),
            )
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(self.corpus, manifest="0" * 64)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(self.corpus, revision="0" * 64)

        replacement_root = self.base / "replacement-root"
        replacement = self.base / "replacement"
        self._write_fixture(replacement_root)
        (replacement_root / "user/lib/tool.c").write_bytes(b"replacement\n")
        replacement_summary = _BUILDER.build_corpus(replacement_root, replacement)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(replacement)
        loaded_replacement = self._load(
            replacement,
            revision=str(replacement_summary["revision"]),
            manifest=str(replacement_summary["manifest_sha256"]),
        )
        self.assertNotEqual(loaded_replacement.corpus_revision, self.loaded.corpus_revision)

    def test_rejects_volume_tamper_before_record_use(self) -> None:
        corpus = self._clone("volume-tamper")
        volume_path = sorted(corpus.glob("nxsrc[0-9][0-9][0-9]"))[0]
        volume = bytearray(volume_path.read_bytes())
        volume[-1] ^= 1
        volume_path.write_bytes(volume)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(corpus)

    def test_rejects_resigned_truncation_and_trailing_record_bytes(self) -> None:
        for name, mutate in (
            ("truncated", lambda value: value[:-1]),
            ("trailing", lambda value: value + b"x"),
        ):
            with self.subTest(mutation=name):
                corpus = self._clone(name)
                volume_path = sorted(corpus.glob("nxsrc[0-9][0-9][0-9]"))[-1]
                volume_path.write_bytes(mutate(volume_path.read_bytes()))
                manifest = self._resign_volume(corpus, volume_path.name)
                with self.assertRaises(attestation.SourceAttestationError):
                    self._load(corpus, manifest=manifest)

    def test_rejects_resigned_nonportable_record_path(self) -> None:
        corpus = self._clone("bad-path")
        volume_path, _offset, path_start, _content_start = self._locate_record(
            corpus, "os/a.c"
        )
        volume = bytearray(volume_path.read_bytes())
        volume[path_start + 3] = ord("+")
        volume_path.write_bytes(volume)
        manifest = self._resign_volume(corpus, volume_path.name)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(corpus, manifest=manifest)

    def test_rejects_resigned_invalid_utf8_content(self) -> None:
        corpus = self._clone("bad-utf8")
        volume_path, _offset, _path_start, content_start = self._locate_record(
            corpus, "os/a.c"
        )
        volume = bytearray(volume_path.read_bytes())
        volume[content_start] = 0xFF
        volume_path.write_bytes(volume)
        manifest = self._resign_volume(corpus, volume_path.name)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(corpus, manifest=manifest)

    def test_rejects_resigned_false_full_source_digest(self) -> None:
        corpus = self._clone("bad-full-sha")
        volume_path, record_offset, _path_start, _content_start = self._locate_record(
            corpus, "os/a.c"
        )
        volume = bytearray(volume_path.read_bytes())
        volume[record_offset + 32] ^= 1
        volume[record_offset + 64:record_offset + 96] = hashlib.sha256(
            volume[record_offset:record_offset + 64]
        ).digest()
        volume_path.write_bytes(volume)
        manifest = self._resign_volume(corpus, volume_path.name)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(corpus, manifest=manifest)

    def test_rejects_manifest_revision_trailing_bytes_and_undeclared_file(self) -> None:
        revision_corpus = self._clone("bad-revision")
        manifest_path = revision_corpus / attestation.MANIFEST_NAME
        manifest = bytearray(manifest_path.read_bytes())
        manifest[40] ^= 1
        manifest_path.write_bytes(manifest)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(
                revision_corpus,
                revision=bytes(manifest[40:72]).hex(),
                manifest=hashlib.sha256(manifest).hexdigest(),
            )

        trailing_corpus = self._clone("trailing-manifest")
        manifest_path = trailing_corpus / attestation.MANIFEST_NAME
        trailing_manifest = manifest_path.read_bytes() + b"\0"
        manifest_path.write_bytes(trailing_manifest)
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(
                trailing_corpus,
                manifest=hashlib.sha256(trailing_manifest).hexdigest(),
            )

        extra_corpus = self._clone("extra-entry")
        (extra_corpus / "nxsrc999").write_bytes(b"undeclared")
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(extra_corpus)

    def test_rejects_cross_revision_evidence_tokens(self) -> None:
        second_root = self.base / "second-root"
        second_corpus = self.base / "second-corpus"
        self._write_fixture(second_root)
        (second_root / "user/lib/tool.c").write_bytes(b"changed elsewhere\n")
        second_summary = _BUILDER.build_corpus(second_root, second_corpus)
        second = self._load(
            second_corpus,
            revision=str(second_summary["revision"]),
            manifest=str(second_summary["manifest_sha256"]),
        )
        unchanged = self._record_by_path(self.loaded, "include/a.h")
        token = self._event(unchanged, 1, 1)
        self.assertTrue(self.loaded.verify_evidence_event(token))
        token["corpus_revision"] = second.corpus_revision
        token["manifest_sha256"] = second.manifest_sha256
        self.assertFalse(self.loaded.verify_evidence_event(token))
        second_record = self._record_by_path(second, "include/a.h")
        second_replay = second.attest_read(second_record.source_id, 1, 1)
        token["artifact_sha256"] = second_replay.artifact_sha256
        token["projection_sha256"] = second_replay.projection_sha256
        self.assertTrue(second.verify_evidence_event(token))

    def test_rejects_symlinked_corpus_path_when_supported(self) -> None:
        link = self.base / "corpus-link"
        try:
            os.symlink(self.corpus, link, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"directory symlink is unavailable: {error}")
        with self.assertRaises(attestation.SourceAttestationError):
            self._load(link)


if __name__ == "__main__":
    unittest.main()
