#!/usr/bin/env python3
"""Strict, read-only Host attestation for a Nexus source snapshot.

The loader consumes the complete corpus into immutable memory before QEMU is
started.  Callers must supply the revision and manifest digest from a trusted
build anchor; values carried by a Guest event are never accepted as anchors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from types import MappingProxyType


FORMAT_VERSION = 1
SCOPE = "build_source_snapshot"
MANIFEST_NAME = "nxsrcmeta"
VOLUME_PREFIX = "nxsrc"
MANIFEST_MAGIC = b"NXSMETA1"
RECORD_MAGIC = b"NXSREC01"
MANIFEST_HEADER_SIZE = 128
VOLUME_DESCRIPTOR_SIZE = 56
RECORD_HEADER_SIZE = 96
VOLUME_MAX_BYTES = 258_048
CORPUS_MAX_BYTES = 8 * 1024 * 1024
MAX_VOLUMES = 16
MAX_SOURCES = 9_999
MAX_PATH_BYTES = 111
MAX_LINE_BYTES = 192
MAX_READ_LINES = 12
MAX_READ_BYTES = 3_072
MAX_SEARCH_RESULTS = 8
MAX_SEARCH_QUERY_BYTES = 95
GUEST_READ_BUFFER_CAPACITY = 2_400
GUEST_PROJECTION_CAPACITY = 3_072
GUEST_SEARCH_BODY_CAPACITY = 2_400
ALLOWLIST = ("os", "include", "user/lib", "user/include")
ALLOWLIST_TEXT = "os/,include/,user/lib/,user/include/"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_ID_RE = re.compile(r"S[0-9]{4}\Z")
_ATTESTED_EVENT_FIELDS = (
    "scope",
    "corpus_revision",
    "manifest_sha256",
    "source_id",
    "path",
    "start_line",
    "end_line",
    "citation",
    "full_sha256",
    "chunk_sha256",
    "artifact_sha256",
    "projection_sha256",
)
_EVIDENCE_BINDING_FIELDS = (
    "version",
    "corr_id",
    "tool",
    "task_id",
    "provenance",
    "scope",
    "corpus_revision",
    "manifest_sha256",
    "source_id",
    "path",
    "start_line",
    "end_line",
    "citation",
    "full_sha256",
    "chunk_sha256",
    "artifact_sha256",
    "projection_sha256",
)


class SourceAttestationError(ValueError):
    """The configured anchor or source corpus is not canonical and intact."""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One immutable, fully verified source record."""

    source_id: str
    path: str
    content: bytes
    full_sha256: str
    line_starts: tuple[int, ...]

    @property
    def line_count(self) -> int:
        return len(self.line_starts)

    def _range_bytes(self, start_line: int, end_line: int) -> bytes:
        start_offset = self.line_starts[start_line - 1]
        end_offset = (
            self.line_starts[end_line]
            if end_line < self.line_count
            else len(self.content)
        )
        return self.content[start_offset:end_offset]


@dataclass(frozen=True, slots=True)
class ReadAttestation:
    """Host-replayed source_read data and its exact Guest projection."""

    version: int
    tool: str
    scope: str
    corpus_revision: str
    manifest_sha256: str
    source_id: str
    path: str
    start_line: int
    end_line: int
    citation: str
    full_sha256: str
    chunk_sha256: str
    artifact_sha256: str
    projection_sha256: str
    content: bytes
    projection: str
    corpus_bound_sha256: str

    def evidence_binding(
        self, corr_id: object, task_id: object, provenance: object
    ) -> Mapping[str, object]:
        """Return a frozen, canonically ordered final-evidence binding."""

        if not _bounded_positive_int(corr_id, maximum=(1 << 64) - 1):
            raise SourceAttestationError("evidence corr_id is not a positive u64")
        if not _bounded_positive_int(task_id, maximum=(1 << 32) - 1):
            raise SourceAttestationError("evidence task_id is not a positive u32")
        if not _bounded_int(provenance, minimum=0, maximum=(1 << 64) - 1):
            raise SourceAttestationError("evidence provenance is not a u64")
        return MappingProxyType(
            {
                "version": self.version,
                "corr_id": corr_id,
                "tool": self.tool,
                "task_id": task_id,
                "provenance": provenance,
                "scope": self.scope,
                "corpus_revision": self.corpus_revision,
                "manifest_sha256": self.manifest_sha256,
                "source_id": self.source_id,
                "path": self.path,
                "start_line": self.start_line,
                "end_line": self.end_line,
                "citation": self.citation,
                "full_sha256": self.full_sha256,
                "chunk_sha256": self.chunk_sha256,
                "artifact_sha256": self.artifact_sha256,
                "projection_sha256": self.projection_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class SearchMatch:
    source_id: str
    path: str
    line: int
    citation: str
    full_sha256: str
    chunk_sha256: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchAttestation:
    """Host replay of a bounded discovery query, not final claim evidence."""

    scope: str
    corpus_revision: str
    manifest_sha256: str
    query: str
    path_prefix: str
    matches: tuple[SearchMatch, ...]
    truncated: bool
    projection: str
    projection_sha256: str
    trust: str = "discovery"


@dataclass(frozen=True, slots=True)
class SourceAttestation:
    """An anchored, immutable in-memory view of a complete source corpus."""

    corpus_revision: str
    manifest_sha256: str
    source_bytes: int
    corpus_bytes: int
    sources: Mapping[str, SourceRecord]
    volume_sha256: Mapping[str, str]

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def volume_count(self) -> int:
        return len(self.volume_sha256)

    def verify_read(
        self,
        source_id: object,
        path: object,
        start_line: object,
        end_line: object,
        citation: object,
        full_sha256: object,
        chunk_sha256: object,
    ) -> bool:
        """Return whether a source_read token exactly describes corpus bytes."""

        try:
            replay = self.attest_read(source_id, start_line, end_line)
        except SourceAttestationError:
            return False
        return (
            path == replay.path
            and citation == replay.citation
            and _digest_matches(full_sha256, replay.full_sha256)
            and _digest_matches(chunk_sha256, replay.chunk_sha256)
        )

    def attest_read(
        self, source_id: object, start_line: object, end_line: object
    ) -> ReadAttestation:
        """Reconstruct exact source bytes and the canonical Guest projection."""

        if type(source_id) is not str or _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise SourceAttestationError("source read id is malformed")
        if not _bounded_positive_int(start_line, maximum=(1 << 32) - 1):
            raise SourceAttestationError("source read start line is invalid")
        if not _bounded_positive_int(end_line, maximum=(1 << 32) - 1):
            raise SourceAttestationError("source read end line is invalid")
        if end_line < start_line or end_line - start_line + 1 > MAX_READ_LINES:
            raise SourceAttestationError("source read range is invalid")
        record = self.sources.get(source_id)
        if record is None or end_line > record.line_count:
            raise SourceAttestationError("source read range is outside the corpus")
        content = record._range_bytes(start_line, end_line)
        if len(content) >= GUEST_READ_BUFFER_CAPACITY or len(content) > MAX_READ_BYTES:
            raise SourceAttestationError("source read range exceeds the Guest byte bound")
        citation = f"[{source_id}:L{start_line}-L{end_line}]"
        chunk_sha256 = hashlib.sha256(content).hexdigest()
        try:
            source_text = content.decode("utf-8")
        except UnicodeDecodeError as error:  # Defensive; load already proved this.
            raise SourceAttestationError("source read content is not UTF-8") from error
        projection = (
            f"scope={SCOPE}\n"
            f"bounded=1\n"
            f"allowlist={ALLOWLIST_TEXT}\n"
            f"content_untrusted=1\n"
            f"citation={citation}\n"
            f"source_id={source_id}\n"
            f"path={record.path}\n"
            f"start_line={start_line}\n"
            f"end_line={end_line}\n"
            f"revision={self.corpus_revision}\n"
            f"manifest_sha256={self.manifest_sha256}\n"
            f"full_sha256={record.full_sha256}\n"
            f"chunk_sha256={chunk_sha256}\n"
            f"--- source data ---\n{source_text}"
        )
        if len(projection.encode("utf-8")) >= GUEST_PROJECTION_CAPACITY:
            raise SourceAttestationError("source read projection exceeds the Guest byte bound")
        projection_sha256 = hashlib.sha256(projection.encode("utf-8")).hexdigest()
        corpus_fields = {
            "version": 1,
            "tool": "source_read",
            "scope": SCOPE,
            "corpus_revision": self.corpus_revision,
            "manifest_sha256": self.manifest_sha256,
            "source_id": source_id,
            "path": record.path,
            "start_line": start_line,
            "end_line": end_line,
            "citation": citation,
            "full_sha256": record.full_sha256,
            "chunk_sha256": chunk_sha256,
            "artifact_sha256": projection_sha256,
            "projection_sha256": projection_sha256,
        }
        return ReadAttestation(
            version=1,
            tool="source_read",
            scope=SCOPE,
            corpus_revision=self.corpus_revision,
            manifest_sha256=self.manifest_sha256,
            source_id=source_id,
            path=record.path,
            start_line=start_line,
            end_line=end_line,
            citation=citation,
            full_sha256=record.full_sha256,
            chunk_sha256=chunk_sha256,
            artifact_sha256=projection_sha256,
            projection_sha256=projection_sha256,
            content=content,
            projection=projection,
            corpus_bound_sha256=hashlib.sha256(
                _canonical_json_bytes(corpus_fields)
            ).hexdigest(),
        )

    def attest_search(
        self, query: object, path_prefix: object = ""
    ) -> SearchAttestation:
        """Replay Guest literal search and reconstruct its canonical projection."""

        query_bytes = _valid_search_text(query, allow_empty=False, path=False)
        prefix_bytes = _valid_search_text(path_prefix, allow_empty=True, path=True)
        scanned_matches: list[SearchMatch] = []
        scan_truncated = False
        for record in self.sources.values():
            path_bytes = record.path.encode("ascii")
            prefix_matches = not prefix_bytes or path_bytes.startswith(prefix_bytes)
            path_matches = prefix_matches and _ascii_contains(path_bytes, query_bytes)
            for line_number in range(1, record.line_count + 1):
                line_bytes = record._range_bytes(line_number, line_number)
                snippet_bytes = (
                    line_bytes[:-1] if line_bytes.endswith(b"\n") else line_bytes
                )
                content_matches = prefix_matches and _ascii_contains(
                    snippet_bytes, query_bytes
                )
                if not content_matches and not (path_matches and line_number == 1):
                    continue
                if len(scanned_matches) >= MAX_SEARCH_RESULTS:
                    scan_truncated = True
                    continue
                scanned_matches.append(
                    SearchMatch(
                        source_id=record.source_id,
                        path=record.path,
                        line=line_number,
                        citation=(
                            f"[{record.source_id}:L{line_number}-L{line_number}]"
                        ),
                        full_sha256=record.full_sha256,
                        chunk_sha256=hashlib.sha256(line_bytes).hexdigest(),
                        snippet=snippet_bytes.decode("utf-8"),
                    )
                )
        if not scanned_matches:
            raise SourceAttestationError("source search has no matches")
        emitted: list[SearchMatch] = []
        body_parts: list[str] = []
        body_bytes = 0
        for match in scanned_matches:
            line = (
                "match="
                f"{match.source_id}|{match.path}|{match.line}|{match.citation}|"
                f"{match.full_sha256}|{match.chunk_sha256}|{match.snippet}\n"
            )
            line_bytes = len(line.encode("utf-8"))
            if body_bytes + line_bytes + 1 > GUEST_SEARCH_BODY_CAPACITY:
                break
            body_parts.append(line)
            body_bytes += line_bytes
            emitted.append(match)
        body = "".join(body_parts)
        truncated = scan_truncated or len(emitted) != len(scanned_matches)
        query_text = query_bytes.decode("utf-8")
        prefix_text = prefix_bytes.decode("ascii")
        projection = (
            f"scope={SCOPE}\n"
            f"bounded=1\n"
            f"allowlist={ALLOWLIST_TEXT}\n"
            f"content_untrusted=1\n"
            f"revision={self.corpus_revision}\n"
            f"manifest_sha256={self.manifest_sha256}\n"
            f"query={query_text}\n"
            f"path_prefix={prefix_text}\n"
            f"match_count={len(emitted)}\n"
            f"truncated={int(truncated)}\n{body}"
        )
        return SearchAttestation(
            scope=SCOPE,
            corpus_revision=self.corpus_revision,
            manifest_sha256=self.manifest_sha256,
            query=query_text,
            path_prefix=prefix_text,
            matches=tuple(emitted),
            truncated=truncated,
            projection=projection,
            projection_sha256=hashlib.sha256(projection.encode("utf-8")).hexdigest(),
        )

    def verify_evidence_event(self, event: object) -> bool:
        """Verify all corpus-bound fields of an EVIDENCE_EVENT mapping.

        Envelope, task and provenance checks remain the relay state machine's
        responsibility.  Extra envelope fields are intentionally ignored here;
        every corpus-bound field and the exact projection digest are required.
        """

        if not isinstance(event, Mapping):
            return False
        try:
            values = {field: event[field] for field in _ATTESTED_EVENT_FIELDS}
        except (KeyError, TypeError):
            return False
        if values["scope"] != SCOPE:
            return False
        if not _digest_matches(values["corpus_revision"], self.corpus_revision):
            return False
        if not _digest_matches(values["manifest_sha256"], self.manifest_sha256):
            return False
        if not self.verify_read(
            values["source_id"],
            values["path"],
            values["start_line"],
            values["end_line"],
            values["citation"],
            values["full_sha256"],
            values["chunk_sha256"],
        ):
            return False
        projection_sha256 = values["projection_sha256"]
        artifact_sha256 = values["artifact_sha256"]
        try:
            replay = self.attest_read(
                values["source_id"], values["start_line"], values["end_line"]
            )
        except SourceAttestationError:
            return False
        return (
            _digest_matches(artifact_sha256, replay.artifact_sha256)
            and _digest_matches(projection_sha256, replay.projection_sha256)
            and hmac.compare_digest(artifact_sha256, projection_sha256)
        )


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    attributes: int


def _identity(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _canonical_digest(value: object) -> bool:
    return type(value) is str and _DIGEST_RE.fullmatch(value) is not None


def _bounded_int(value: object, *, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _bounded_positive_int(value: object, *, maximum: int) -> bool:
    return _bounded_int(value, minimum=1, maximum=maximum)


def _digest_matches(value: object, expected: str) -> bool:
    return _canonical_digest(value) and hmac.compare_digest(value, expected)


def _configured_digest(value: object, label: str) -> str:
    if not _canonical_digest(value):
        raise SourceAttestationError(f"{label} is not a canonical SHA-256 digest")
    return value


def _is_link_like(path: Path) -> bool:
    if path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    ):
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_link_chain(path: Path) -> None:
    cursor = _absolute_without_resolving(path)
    chain = (cursor, *cursor.parents)
    for entry in reversed(chain):
        if _is_link_like(entry):
            raise SourceAttestationError(
                f"source corpus path contains a symlink or junction: {entry}"
            )


def _directory_snapshot(directory: Path) -> tuple[_FileIdentity, dict[str, _FileIdentity]]:
    _reject_link_chain(directory)
    try:
        directory_identity = _identity(directory.lstat())
    except OSError as error:
        raise SourceAttestationError("source corpus directory is unavailable") from error
    if not stat.S_ISDIR(directory_identity.mode):
        raise SourceAttestationError("source corpus path is not a regular directory")
    entries: dict[str, _FileIdentity] = {}
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_VOLUMES + 1:
                    raise SourceAttestationError(
                        "source corpus contains too many directory entries"
                    )
                path = Path(entry.path)
                if _is_link_like(path):
                    raise SourceAttestationError(
                        f"source corpus entry is a symlink or junction: {entry.name}"
                    )
                identity = _identity(path.lstat())
                if not stat.S_ISREG(identity.mode):
                    raise SourceAttestationError(
                        f"source corpus entry is not a regular file: {entry.name}"
                    )
                entries[entry.name] = identity
    except SourceAttestationError:
        raise
    except OSError as error:
        raise SourceAttestationError("source corpus directory cannot be scanned") from error
    return directory_identity, entries


def _read_snapshot_file(
    path: Path, expected: _FileIdentity, *, maximum: int
) -> bytes:
    if expected.size < 0 or expected.size > maximum or _is_link_like(path):
        raise SourceAttestationError(f"source corpus file size or type is invalid: {path.name}")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceAttestationError(f"source corpus file cannot be opened: {path.name}") from error
    data = bytearray()
    try:
        before = _identity(os.fstat(descriptor))
        if before != expected or not stat.S_ISREG(before.mode):
            raise SourceAttestationError(
                f"source corpus file changed before reading: {path.name}"
            )
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = _identity(os.fstat(descriptor))
        if len(data) > maximum or before != after or len(data) != before.size:
            raise SourceAttestationError(
                f"source corpus file changed while reading: {path.name}"
            )
    except SourceAttestationError:
        raise
    except OSError as error:
        raise SourceAttestationError(f"source corpus file cannot be read: {path.name}") from error
    finally:
        os.close(descriptor)
    try:
        final_identity = _identity(path.lstat())
    except OSError as error:
        raise SourceAttestationError(
            f"source corpus file disappeared after reading: {path.name}"
        ) from error
    if final_identity != expected or _is_link_like(path):
        raise SourceAttestationError(
            f"source corpus file changed after reading: {path.name}"
        )
    return bytes(data)


def _portable_path(relative: str) -> bool:
    return bool(relative) and all(
        ("a" <= character <= "z")
        or ("A" <= character <= "Z")
        or ("0" <= character <= "9")
        or character in "_./-"
        for character in relative
    )


def _canonical_source_path(relative: str) -> bool:
    return (
        bool(relative)
        and str(PurePosixPath(relative)) == relative
        and not relative.startswith("/")
        and all(part not in ("", ".", "..") for part in relative.split("/"))
        and _portable_path(relative)
        and any(relative.startswith(f"{allowed}/") for allowed in ALLOWLIST)
        and relative.endswith((".c", ".h"))
    )


def _valid_search_text(value: object, *, allow_empty: bool, path: bool) -> bytes:
    if type(value) is not str:
        raise SourceAttestationError("source search input is not text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SourceAttestationError("source search input is not UTF-8") from error
    maximum = MAX_PATH_BYTES if path else MAX_SEARCH_QUERY_BYTES
    if len(encoded) > maximum or (not allow_empty and not encoded):
        raise SourceAttestationError("source search input length is out of bounds")
    if any(byte < 0x20 or byte == 0x7F for byte in encoded):
        raise SourceAttestationError("source search input has a control byte")
    if path:
        try:
            relative = encoded.decode("ascii")
        except UnicodeDecodeError as error:
            raise SourceAttestationError("source search prefix is not portable ASCII") from error
        if relative and (
            relative.startswith("/")
            or relative.startswith("..")
            or not _portable_path(relative)
            or ".." in PurePosixPath(relative).parts
        ):
            raise SourceAttestationError("source search prefix is non-canonical")
    return encoded


def _ascii_contains(text: bytes, query: bytes) -> bool:
    def fold(byte: int) -> int:
        return byte + 32 if 0x41 <= byte <= 0x5A else byte

    if len(query) > len(text):
        return False
    return any(
        all(fold(text[offset + index]) == fold(value) for index, value in enumerate(query))
        for offset in range(len(text) - len(query) + 1)
    )


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise SourceAttestationError("evidence cannot be encoded canonically") from error


def canonical_evidence_root(bindings: object) -> str:
    """Hash strictly increasing, complete read-evidence bindings."""

    if not isinstance(bindings, (tuple, list)):
        raise SourceAttestationError("evidence bindings must be a bounded sequence")
    if len(bindings) > MAX_SOURCES:
        raise SourceAttestationError("too many evidence bindings")
    canonical: list[dict[str, object]] = []
    previous_corr_id = 0
    for binding in bindings:
        if not isinstance(binding, Mapping) or tuple(binding) != _EVIDENCE_BINDING_FIELDS:
            raise SourceAttestationError("evidence binding fields or order are malformed")
        value = {field: binding[field] for field in _EVIDENCE_BINDING_FIELDS}
        corr_id = value["corr_id"]
        if (
            not _bounded_positive_int(corr_id, maximum=(1 << 64) - 1)
            or corr_id <= previous_corr_id
        ):
            raise SourceAttestationError(
                "evidence bindings are not in unique increasing corr_id order"
            )
        if value["version"] != 1 or value["tool"] != "source_read":
            raise SourceAttestationError("evidence binding identity is malformed")
        if not _bounded_positive_int(value["task_id"], maximum=(1 << 32) - 1):
            raise SourceAttestationError("evidence binding task_id is malformed")
        if not _bounded_int(value["provenance"], minimum=0, maximum=(1 << 64) - 1):
            raise SourceAttestationError("evidence binding provenance is malformed")
        if value["scope"] != SCOPE:
            raise SourceAttestationError("evidence binding scope is malformed")
        for field in (
            "corpus_revision",
            "manifest_sha256",
            "full_sha256",
            "chunk_sha256",
            "artifact_sha256",
            "projection_sha256",
        ):
            if not _canonical_digest(value[field]):
                raise SourceAttestationError(f"evidence binding {field} is malformed")
        if (
            type(value["source_id"]) is not str
            or _SOURCE_ID_RE.fullmatch(value["source_id"]) is None
            or type(value["path"]) is not str
            or not _canonical_source_path(value["path"])
            or not _bounded_positive_int(value["start_line"], maximum=(1 << 32) - 1)
            or not _bounded_positive_int(value["end_line"], maximum=(1 << 32) - 1)
            or type(value["citation"]) is not str
        ):
            raise SourceAttestationError("evidence binding source token is malformed")
        if not hmac.compare_digest(
            str(value["artifact_sha256"]), str(value["projection_sha256"])
        ):
            raise SourceAttestationError(
                "source evidence artifact and projection digests differ"
            )
        canonical.append(value)
        previous_corr_id = corr_id
    return hashlib.sha256(_canonical_json_bytes(canonical)).hexdigest()


def _line_starts(content: bytes) -> tuple[int, ...]:
    starts = [0]
    starts.extend(
        offset + 1
        for offset, value in enumerate(content)
        if value == 0x0A and offset + 1 < len(content)
    )
    return tuple(starts)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def load_source_attestation(
    directory: str | os.PathLike[str],
    *,
    expected_revision: str,
    expected_manifest_sha256: str,
) -> SourceAttestation:
    """Load and fully verify one corpus against trusted build anchor values."""

    expected_revision = _configured_digest(expected_revision, "expected revision")
    expected_manifest_sha256 = _configured_digest(
        expected_manifest_sha256, "expected manifest digest"
    )
    corpus_dir = _absolute_without_resolving(Path(directory))
    initial_directory, initial_files = _directory_snapshot(corpus_dir)
    manifest_identity = initial_files.get(MANIFEST_NAME)
    if manifest_identity is None:
        raise SourceAttestationError("source corpus manifest is missing")
    manifest_max = MANIFEST_HEADER_SIZE + MAX_VOLUMES * VOLUME_DESCRIPTOR_SIZE
    manifest = _read_snapshot_file(
        corpus_dir / MANIFEST_NAME, manifest_identity, maximum=manifest_max
    )
    manifest_sha256 = hashlib.sha256(manifest).hexdigest()
    if not hmac.compare_digest(manifest_sha256, expected_manifest_sha256):
        raise SourceAttestationError("source corpus manifest does not match the build anchor")
    if len(manifest) < MANIFEST_HEADER_SIZE or manifest[:8] != MANIFEST_MAGIC:
        raise SourceAttestationError("source corpus manifest magic or size is invalid")
    (
        version,
        header_size,
        descriptor_size,
        source_count,
        volume_count,
        reserved,
        declared_source_bytes,
    ) = struct.unpack_from("<IIIIIIQ", manifest, 8)
    if (version, header_size, descriptor_size, reserved) != (
        FORMAT_VERSION,
        MANIFEST_HEADER_SIZE,
        VOLUME_DESCRIPTOR_SIZE,
        0,
    ):
        raise SourceAttestationError("source corpus manifest header is non-canonical")
    if not 0 < source_count <= MAX_SOURCES or not 0 < volume_count <= MAX_VOLUMES:
        raise SourceAttestationError("source corpus manifest counts are out of bounds")
    if not 0 < declared_source_bytes <= CORPUS_MAX_BYTES:
        raise SourceAttestationError("source corpus byte count is out of bounds")
    if len(manifest) != header_size + volume_count * descriptor_size:
        raise SourceAttestationError("source corpus manifest has trailing or missing bytes")
    scope_field = manifest[104:128]
    scope = SCOPE.encode("ascii")
    if scope_field != scope.ljust(24, b"\0"):
        raise SourceAttestationError("source corpus scope is non-canonical")
    descriptors = manifest[header_size:]
    if not hmac.compare_digest(
        hashlib.sha256(descriptors).digest(), manifest[72:104]
    ):
        raise SourceAttestationError("source corpus descriptor digest is invalid")

    expected_files = {MANIFEST_NAME} | {
        f"{VOLUME_PREFIX}{index:03d}" for index in range(volume_count)
    }
    if set(initial_files) != expected_files:
        raise SourceAttestationError("source corpus has missing or undeclared files")

    descriptor_values: list[tuple[str, int, int, bytes]] = []
    declared_records = 0
    declared_corpus_bytes = len(manifest)
    for volume_index in range(volume_count):
        descriptor = descriptors[
            volume_index * descriptor_size:(volume_index + 1) * descriptor_size
        ]
        name_field, volume_size, record_count, volume_sha = struct.unpack(
            "<16sII32s", descriptor
        )
        expected_name = f"{VOLUME_PREFIX}{volume_index:03d}".encode("ascii")
        if name_field != expected_name.ljust(16, b"\0"):
            raise SourceAttestationError("source corpus volume name is non-canonical")
        if (
            not 0 < volume_size <= VOLUME_MAX_BYTES
            or not 0 < record_count <= source_count
        ):
            raise SourceAttestationError("source corpus volume bounds are invalid")
        name = expected_name.decode("ascii")
        if initial_files[name].size != volume_size:
            raise SourceAttestationError(f"source corpus volume size is invalid: {name}")
        descriptor_values.append((name, volume_size, record_count, volume_sha))
        declared_records += record_count
        declared_corpus_bytes += volume_size
    if declared_records != source_count or declared_corpus_bytes > CORPUS_MAX_BYTES:
        raise SourceAttestationError("source corpus aggregate bounds are invalid")

    revision = hashlib.sha256()
    revision.update(b"NXSRCREV1")
    records: dict[str, SourceRecord] = {}
    volume_digests: dict[str, str] = {}
    previous_path: bytes | None = None
    actual_source_bytes = 0
    for name, _volume_size, record_count, expected_volume_sha in descriptor_values:
        volume = _read_snapshot_file(
            corpus_dir / name, initial_files[name], maximum=VOLUME_MAX_BYTES
        )
        actual_volume_sha = hashlib.sha256(volume).digest()
        if not hmac.compare_digest(actual_volume_sha, expected_volume_sha):
            raise SourceAttestationError(f"source corpus volume digest is invalid: {name}")
        volume_digests[name] = actual_volume_sha.hex()
        offset = 0
        records_in_volume = 0
        while offset < len(volume):
            if offset + RECORD_HEADER_SIZE > len(volume):
                raise SourceAttestationError("source corpus record header is truncated")
            header = volume[offset:offset + RECORD_HEADER_SIZE]
            prefix = header[:64]
            if prefix[:8] != RECORD_MAGIC or not hmac.compare_digest(
                hashlib.sha256(prefix).digest(), header[64:96]
            ):
                raise SourceAttestationError("source corpus record header is invalid")
            source_number = len(records) + 1
            expected_source_id = f"S{source_number:04d}".encode("ascii")
            source_id_field = prefix[8:16]
            path_size, content_size, declared_lines, record_reserved = struct.unpack_from(
                "<IIII", prefix, 16
            )
            if (
                source_id_field != expected_source_id.ljust(8, b"\0")
                or record_reserved != 0
                or not 0 < path_size <= MAX_PATH_BYTES
                or content_size == 0
                or declared_lines == 0
            ):
                raise SourceAttestationError("source corpus record identity or bounds are invalid")
            record_end = offset + RECORD_HEADER_SIZE + path_size + content_size
            if record_end > len(volume):
                raise SourceAttestationError("source corpus record content is truncated")
            path_bytes = volume[
                offset + RECORD_HEADER_SIZE:offset + RECORD_HEADER_SIZE + path_size
            ]
            content = volume[offset + RECORD_HEADER_SIZE + path_size:record_end]
            try:
                relative = path_bytes.decode("ascii")
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SourceAttestationError("source corpus record encoding is invalid") from error
            if not _canonical_source_path(relative):
                raise SourceAttestationError("source corpus record path is non-canonical")
            if previous_path is not None and path_bytes <= previous_path:
                raise SourceAttestationError("source corpus paths are not strictly sorted")
            if b"\0" in content or b"\r" in content:
                raise SourceAttestationError("source corpus content is not normalized")
            if any(len(line) > MAX_LINE_BYTES for line in content.split(b"\n")):
                raise SourceAttestationError("source corpus line length is out of bounds")
            starts = _line_starts(content)
            if len(starts) != declared_lines:
                raise SourceAttestationError("source corpus line count is invalid")
            full_digest = hashlib.sha256(content).digest()
            if not hmac.compare_digest(full_digest, prefix[32:64]):
                raise SourceAttestationError("source corpus content digest is invalid")
            source_id = expected_source_id.decode("ascii")
            records[source_id] = SourceRecord(
                source_id=source_id,
                path=relative,
                content=content,
                full_sha256=full_digest.hex(),
                line_starts=starts,
            )
            revision.update(_u32(len(path_bytes)))
            revision.update(path_bytes)
            revision.update(_u64(len(content)))
            revision.update(content)
            previous_path = path_bytes
            actual_source_bytes += len(content)
            records_in_volume += 1
            offset = record_end
        if records_in_volume != record_count:
            raise SourceAttestationError("source corpus volume record count is invalid")

    if len(records) != source_count or actual_source_bytes != declared_source_bytes:
        raise SourceAttestationError("source corpus source aggregate is invalid")
    actual_revision = revision.hexdigest()
    manifest_revision = manifest[40:72].hex()
    if not hmac.compare_digest(actual_revision, manifest_revision):
        raise SourceAttestationError("source corpus revision is invalid")
    if not hmac.compare_digest(actual_revision, expected_revision):
        raise SourceAttestationError("source corpus revision does not match the build anchor")

    final_directory, final_files = _directory_snapshot(corpus_dir)
    if final_directory != initial_directory or final_files != initial_files:
        raise SourceAttestationError("source corpus changed while it was loaded")
    return SourceAttestation(
        corpus_revision=actual_revision,
        manifest_sha256=manifest_sha256,
        source_bytes=actual_source_bytes,
        corpus_bytes=declared_corpus_bytes,
        sources=MappingProxyType(records),
        volume_sha256=MappingProxyType(volume_digests),
    )


__all__ = (
    "SCOPE",
    "ReadAttestation",
    "SearchAttestation",
    "SearchMatch",
    "SourceAttestation",
    "SourceAttestationError",
    "SourceRecord",
    "canonical_evidence_root",
    "load_source_attestation",
)
