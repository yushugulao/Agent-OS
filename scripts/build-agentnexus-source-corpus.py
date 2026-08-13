#!/usr/bin/env python3
"""Build and verify the bounded, image-resident Nexus source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import tempfile


FORMAT_VERSION = 1
SCOPE = b"build_source_snapshot"
MANIFEST_NAME = "nxsrcmeta"
ANCHOR_HEADER_NAME = "agent_nexus_source_anchor.h"
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
ALLOWLIST = ("os", "include", "user/lib", "user/include")


class CorpusError(ValueError):
    pass


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def _normalized_source(path: Path) -> bytes:
    raw = path.read_bytes()
    if b"\0" in raw:
        raise CorpusError(f"NUL byte is forbidden in source: {path}")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorpusError(f"source is not UTF-8: {path}: {error}") from error
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not normalized:
        raise CorpusError(f"empty source is forbidden: {path}")
    for line_number, line in enumerate(normalized.split(b"\n"), 1):
        if len(line) > MAX_LINE_BYTES:
            raise CorpusError(
                f"source line exceeds {MAX_LINE_BYTES} bytes: {path}:{line_number}"
            )
    return normalized


def _portable_path(relative: str) -> bool:
    return bool(relative) and all(
        ("a" <= character <= "z")
        or ("A" <= character <= "Z")
        or ("0" <= character <= "9")
        or character in "_./-"
        for character in relative
    )


def _is_link_like(path: Path) -> bool:
    """Reject symlinks and Windows junctions/reparse-point directory aliases."""
    if path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    ):
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _regular_tree_files(base: Path) -> list[Path]:
    """Walk a tree without ever descending through a filesystem alias."""
    files: list[Path] = []
    pending = [base]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        except OSError as error:
            raise CorpusError(f"cannot scan allowlisted directory: {directory}") from error
        children: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            if _is_link_like(path):
                raise CorpusError(f"source symlink or junction is forbidden: {path}")
            try:
                if entry.is_dir(follow_symlinks=False):
                    children.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
            except OSError as error:
                raise CorpusError(f"cannot inspect allowlisted path: {path}") from error
        pending.extend(reversed(children))
    return files


def _line_count(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + int(not content.endswith(b"\n"))


def collect_sources(root: Path) -> list[tuple[str, bytes]]:
    root = root.resolve()
    collected: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for directory in ALLOWLIST:
        base = root / directory
        if not base.is_dir() or _is_link_like(base):
            raise CorpusError(f"missing allowlisted directory: {directory}")
        resolved_base = base.resolve(strict=True)
        for path in _regular_tree_files(base):
            if path.suffix not in (".c", ".h"):
                continue
            # A source snapshot must never dereference a repository alias.
            cursor = path
            while cursor != root:
                if _is_link_like(cursor):
                    raise CorpusError(f"source symlink or junction is forbidden: {path}")
                cursor = cursor.parent
            try:
                path.resolve(strict=True).relative_to(root)
                path.resolve(strict=True).relative_to(resolved_base)
            except (OSError, ValueError) as error:
                raise CorpusError(f"source escapes its build allowlist: {path}") from error
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                raise CorpusError(f"duplicate source path: {relative}")
            if str(PurePosixPath(relative)) != relative or ".." in PurePosixPath(relative).parts:
                raise CorpusError(f"non-canonical source path: {relative}")
            try:
                encoded_path = relative.encode("ascii")
            except UnicodeEncodeError as error:
                raise CorpusError(f"source path is not portable ASCII: {relative}") from error
            if not encoded_path or len(encoded_path) > MAX_PATH_BYTES:
                raise CorpusError(f"source path length is out of bounds: {relative}")
            if not _portable_path(relative):
                raise CorpusError(f"source path has non-portable characters: {relative}")
            seen.add(relative)
            collected.append((relative, _normalized_source(path)))
    collected.sort(key=lambda item: item[0].encode("ascii"))
    if not collected or len(collected) > MAX_SOURCES:
        raise CorpusError(f"source count is out of bounds: {len(collected)}")
    return collected


def _record(source_number: int, relative: str, content: bytes) -> bytes:
    source_id = f"S{source_number:04d}".encode("ascii")
    path = relative.encode("ascii")
    line_count = _line_count(content)
    prefix = b"".join(
        (
            RECORD_MAGIC,
            source_id.ljust(8, b"\0"),
            _u32(len(path)),
            _u32(len(content)),
            _u32(line_count),
            _u32(0),
            hashlib.sha256(content).digest(),
        )
    )
    assert len(prefix) == 64
    return prefix + hashlib.sha256(prefix).digest() + path + content


def _revision(sources: list[tuple[str, bytes]]) -> bytes:
    digest = hashlib.sha256()
    digest.update(b"NXSRCREV1")
    for relative, content in sources:
        path = relative.encode("ascii")
        digest.update(_u32(len(path)))
        digest.update(path)
        digest.update(_u64(len(content)))
        digest.update(content)
    return digest.digest()


def _anchor_header_bytes(manifest: bytes, sources: int,
                         volumes: int, source_bytes: int) -> bytes:
    revision = manifest[40:72].hex()
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    return (
        "#ifndef USER_AGENT_NEXUS_SOURCE_ANCHOR_H\n"
        "#define USER_AGENT_NEXUS_SOURCE_ANCHOR_H\n"
        f'#define AGENT_NEXUS_SOURCE_ANCHOR_SCOPE "{SCOPE.decode("ascii")}"\n'
        f'#define AGENT_NEXUS_SOURCE_ANCHOR_ALLOWLIST "{",".join(f"{item}/" for item in ALLOWLIST)}"\n'
        f'#define AGENT_NEXUS_SOURCE_ANCHOR_REVISION "{revision}"\n'
        f'#define AGENT_NEXUS_SOURCE_ANCHOR_MANIFEST_SHA256 "{manifest_sha}"\n'
        f"#define AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_COUNT {sources}U\n"
        f"#define AGENT_NEXUS_SOURCE_ANCHOR_VOLUME_COUNT {volumes}U\n"
        f"#define AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_BYTES {source_bytes}ULL\n"
        "#endif\n"
    ).encode("ascii")


def _write_anchor_header(path: Path, manifest: bytes, sources: int,
                         volumes: int, source_bytes: int) -> None:
    body = _anchor_header_bytes(manifest, sources, volumes, source_bytes)
    if _is_link_like(path):
        raise CorpusError("source corpus anchor must not be a link or junction")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(body)
    if path.is_file() and path.read_bytes() == body:
        temporary.unlink()
    else:
        os.replace(temporary, path)


def build_corpus(root: Path, output_dir: Path,
                 anchor_header: Path | None = None) -> dict[str, object]:
    sources = collect_sources(root)
    records = [_record(index, path, content) for index, (path, content) in enumerate(sources, 1)]
    volumes: list[bytes] = []
    volume_counts: list[int] = []
    current = bytearray()
    current_count = 0
    for record in records:
        if len(record) > VOLUME_MAX_BYTES:
            raise CorpusError(f"one source record exceeds volume limit: {len(record)}")
        if current and len(current) + len(record) > VOLUME_MAX_BYTES:
            volumes.append(bytes(current))
            volume_counts.append(current_count)
            current.clear()
            current_count = 0
        current.extend(record)
        current_count += 1
    if current:
        volumes.append(bytes(current))
        volume_counts.append(current_count)
    if not volumes or len(volumes) > MAX_VOLUMES:
        raise CorpusError(f"volume count is out of bounds: {len(volumes)}")

    descriptors = bytearray()
    for index, (volume, count) in enumerate(zip(volumes, volume_counts, strict=True)):
        name = f"{VOLUME_PREFIX}{index:03d}".encode("ascii")
        if len(name) > 14:
            raise CorpusError(f"volume name exceeds uCore DIRSIZ: {name!r}")
        descriptors.extend(name.ljust(16, b"\0"))
        descriptors.extend(_u32(len(volume)))
        descriptors.extend(_u32(count))
        descriptors.extend(hashlib.sha256(volume).digest())
    assert len(descriptors) == len(volumes) * VOLUME_DESCRIPTOR_SIZE

    source_bytes = sum(len(content) for _, content in sources)
    header = bytearray(MANIFEST_HEADER_SIZE)
    header[0:8] = MANIFEST_MAGIC
    struct.pack_into("<IIIIIIQ", header, 8, FORMAT_VERSION, MANIFEST_HEADER_SIZE,
                     VOLUME_DESCRIPTOR_SIZE, len(sources), len(volumes), 0,
                     source_bytes)
    header[40:72] = _revision(sources)
    header[72:104] = hashlib.sha256(descriptors).digest()
    header[104:104 + len(SCOPE)] = SCOPE
    manifest = bytes(header) + bytes(descriptors)
    total_bytes = len(manifest) + sum(map(len, volumes))
    if total_bytes > CORPUS_MAX_BYTES:
        raise CorpusError(f"corpus exceeds {CORPUS_MAX_BYTES} bytes: {total_bytes}")

    if _is_link_like(output_dir):
        raise CorpusError("source corpus output directory must not be a link or junction")
    output_dir = output_dir.resolve()
    if output_dir == root.resolve():
        raise CorpusError("source corpus output directory must differ from repository root")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nxsrc-", dir=output_dir.parent) as temporary:
        stage = Path(temporary)
        (stage / MANIFEST_NAME).write_bytes(manifest)
        for index, volume in enumerate(volumes):
            (stage / f"{VOLUME_PREFIX}{index:03d}").write_bytes(volume)
        verify_corpus(stage)
        output_dir.mkdir(parents=True, exist_ok=True)
        for old in output_dir.iterdir():
            if (
                _is_link_like(old)
                or not old.is_file()
                or not old.name.startswith(VOLUME_PREFIX)
            ):
                raise CorpusError(f"undeclared source corpus output entry: {old.name}")
        for old in output_dir.iterdir():
            old.unlink()
        for produced in sorted(stage.iterdir()):
            os.replace(produced, output_dir / produced.name)
    if anchor_header is not None:
        _write_anchor_header(
            anchor_header, manifest, len(sources), len(volumes), source_bytes
        )

    return {
        "scope": SCOPE.decode("ascii"),
        "revision": header[40:72].hex(),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "sources": len(sources),
        "volumes": len(volumes),
        "source_bytes": source_bytes,
        "corpus_bytes": total_bytes,
    }


def verify_corpus(directory: Path) -> dict[str, object]:
    if _is_link_like(directory) or not directory.is_dir():
        raise CorpusError("source corpus directory must be a regular directory")
    for entry in directory.iterdir():
        if _is_link_like(entry) or not entry.is_file():
            raise CorpusError(f"source corpus entry is not a regular file: {entry.name}")
    manifest = (directory / MANIFEST_NAME).read_bytes()
    if len(manifest) < MANIFEST_HEADER_SIZE or manifest[:8] != MANIFEST_MAGIC:
        raise CorpusError("invalid source manifest magic or size")
    version, header_size, descriptor_size, source_count, volume_count, reserved, source_bytes = (
        struct.unpack_from("<IIIIIIQ", manifest, 8)
    )
    if (version, header_size, descriptor_size, reserved) != (
        FORMAT_VERSION, MANIFEST_HEADER_SIZE, VOLUME_DESCRIPTOR_SIZE, 0
    ):
        raise CorpusError("invalid source manifest header")
    if not 0 < source_count <= MAX_SOURCES or not 0 < volume_count <= MAX_VOLUMES:
        raise CorpusError("source manifest counts are out of bounds")
    if len(manifest) != header_size + volume_count * descriptor_size:
        raise CorpusError("source manifest has trailing or missing bytes")
    scope_field = manifest[104:128]
    scope = scope_field.split(b"\0", 1)[0]
    if scope != SCOPE or scope_field != SCOPE.ljust(24, b"\0"):
        raise CorpusError("source manifest scope mismatch")
    descriptors = manifest[header_size:]
    if hashlib.sha256(descriptors).digest() != manifest[72:104]:
        raise CorpusError("source manifest descriptor digest mismatch")

    revision = hashlib.sha256()
    revision.update(b"NXSRCREV1")
    seen_paths: list[bytes] = []
    # Every accepted file name is declared by the manifest; stale volumes are fatal.
    expected_files = {MANIFEST_NAME} | {
        f"{VOLUME_PREFIX}{index:03d}" for index in range(volume_count)
    }
    actual_files = {path.name for path in directory.iterdir()}
    if actual_files != expected_files:
        raise CorpusError("source corpus contains missing or undeclared volumes")
    actual_sources = 0
    actual_source_bytes = 0
    for volume_index in range(volume_count):
        descriptor = descriptors[
            volume_index * descriptor_size:(volume_index + 1) * descriptor_size
        ]
        name_field, volume_size, record_count, volume_sha = struct.unpack(
            "<16sII32s", descriptor
        )
        name = name_field.split(b"\0", 1)[0]
        expected_name = f"{VOLUME_PREFIX}{volume_index:03d}".encode("ascii")
        if name != expected_name or name_field != expected_name.ljust(16, b"\0"):
            raise CorpusError("non-canonical source volume name")
        volume = (directory / name.decode("ascii")).read_bytes()
        if (
            not volume
            or len(volume) > VOLUME_MAX_BYTES
            or len(volume) != volume_size
            or hashlib.sha256(volume).digest() != volume_sha
        ):
            raise CorpusError(f"source volume integrity failure: {name.decode()}")
        offset = 0
        seen_records = 0
        while offset < len(volume):
            if offset + RECORD_HEADER_SIZE > len(volume):
                raise CorpusError("truncated source record header")
            record_header = volume[offset:offset + RECORD_HEADER_SIZE]
            prefix = record_header[:64]
            if (
                prefix[:8] != RECORD_MAGIC
                or hashlib.sha256(prefix).digest() != record_header[64:96]
            ):
                raise CorpusError("invalid source record header")
            source_id_field = prefix[8:16]
            source_id = source_id_field.split(b"\0", 1)[0]
            path_size, content_size, line_count, record_reserved = struct.unpack_from(
                "<IIII", prefix, 16
            )
            expected_id = f"S{actual_sources + 1:04d}".encode("ascii")
            if (
                source_id != expected_id
                or source_id_field != expected_id.ljust(8, b"\0")
                or record_reserved != 0
                or not 0 < path_size <= MAX_PATH_BYTES
            ):
                raise CorpusError("invalid source record identity or bounds")
            end = offset + RECORD_HEADER_SIZE + path_size + content_size
            if end > len(volume):
                raise CorpusError("truncated source record content")
            path = volume[
                offset + RECORD_HEADER_SIZE:offset + RECORD_HEADER_SIZE + path_size
            ]
            content = volume[offset + RECORD_HEADER_SIZE + path_size:end]
            try:
                relative = path.decode("ascii")
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise CorpusError("invalid source record encoding") from error
            if (
                str(PurePosixPath(relative)) != relative
                or ".." in PurePosixPath(relative).parts
                or not _portable_path(relative)
            ):
                raise CorpusError("invalid source record path")
            if not any(relative.startswith(f"{allowed}/") for allowed in ALLOWLIST):
                raise CorpusError("source record escapes the build allowlist")
            if not relative.endswith((".c", ".h")):
                raise CorpusError("source record has an invalid extension")
            if seen_paths and path <= seen_paths[-1]:
                raise CorpusError("source record paths are not strictly sorted")
            if (not content or b"\0" in content or b"\r" in content or
                    _line_count(content) != line_count or
                    any(len(line) > MAX_LINE_BYTES for line in content.split(b"\n"))):
                raise CorpusError("source record normalization mismatch")
            if hashlib.sha256(content).digest() != prefix[32:64]:
                raise CorpusError("source content digest mismatch")
            seen_paths.append(path)
            revision.update(_u32(len(path)))
            revision.update(path)
            revision.update(_u64(len(content)))
            revision.update(content)
            actual_sources += 1
            actual_source_bytes += len(content)
            seen_records += 1
            offset = end
        if seen_records != record_count:
            raise CorpusError("source volume record count mismatch")
    if actual_sources != source_count or actual_source_bytes != source_bytes:
        raise CorpusError("source manifest aggregate count mismatch")
    if revision.digest() != manifest[40:72]:
        raise CorpusError("source snapshot revision mismatch")
    return {
        "scope": scope.decode("ascii"),
        "revision": manifest[40:72].hex(),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "sources": actual_sources,
        "volumes": volume_count,
        "source_bytes": actual_source_bytes,
    }


def verify_anchor(directory: Path, anchor_header: Path) -> None:
    summary = verify_corpus(directory)
    manifest = (directory / MANIFEST_NAME).read_bytes()
    expected = _anchor_header_bytes(
        manifest, int(summary["sources"]), int(summary["volumes"]),
        int(summary["source_bytes"])
    )
    if _is_link_like(anchor_header) or not anchor_header.is_file():
        raise CorpusError("source corpus anchor is not a regular file")
    if anchor_header.read_bytes() != expected:
        raise CorpusError("source corpus does not match its compiled anchor")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--anchor-header", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        result = verify_corpus(args.output_dir)
        if args.anchor_header is not None:
            verify_anchor(args.output_dir, args.anchor_header)
    else:
        result = build_corpus(args.root, args.output_dir, args.anchor_header)
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
