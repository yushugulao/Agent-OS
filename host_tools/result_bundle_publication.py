#!/usr/bin/env python3
"""Fail-closed staging and atomic publication for mutable result bundles."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


class ResultPublicationError(RuntimeError):
    pass


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(junction) and junction())


def _existing_components(path: Path) -> list[Path]:
    components: list[Path] = []
    current = path
    while True:
        components.append(current)
        if current == current.parent:
            break
        current = current.parent
    components.reverse()
    return components


def _canonical_target(
    result_dir: Path, protected_paths: tuple[Path, ...] = ()
) -> Path:
    lexical = Path(os.path.abspath(result_dir))
    if lexical == Path(lexical.anchor) or not lexical.name:
        raise ResultPublicationError("result directory must not be a filesystem root")
    parent = lexical.parent
    for component in _existing_components(parent):
        if _is_link(component):
            raise ResultPublicationError("result directory has a symlink or junction ancestor")
        if component.exists() and not component.is_dir():
            raise ResultPublicationError("result directory parent is unsafe")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ResultPublicationError(
            f"result directory parent cannot be created: {error}"
        ) from error
    for component in _existing_components(parent):
        if _is_link(component) or not component.is_dir():
            raise ResultPublicationError("result directory parent became unsafe")
    try:
        parent = parent.resolve(strict=True)
    except OSError as error:
        raise ResultPublicationError(f"result directory parent is unavailable: {error}") from error
    target = parent / lexical.name
    if target.exists() or _is_link(target):
        if _is_link(target) or not target.is_dir():
            raise ResultPublicationError("result directory exists but is not a safe directory")
        try:
            if target.resolve(strict=True).parent != parent:
                raise ResultPublicationError("result directory escaped its canonical parent")
        except OSError as error:
            raise ResultPublicationError(f"result directory is unavailable: {error}") from error
    for protected_input in protected_paths:
        protected_lexical = Path(os.path.abspath(protected_input))
        try:
            protected = protected_lexical.resolve(strict=False)
        except OSError as error:
            raise ResultPublicationError(f"protected path is unavailable: {error}") from error
        if target == protected or target in protected.parents:
            raise ResultPublicationError("result directory would replace a protected path")
    return target


def _stage_prefix(target: Path) -> str:
    return f".{target.name}.staging-"


def atomic_write_bytes(output: Path, data: bytes) -> Path:
    """Publish one regular file through an O_EXCL same-parent temporary."""
    lexical = Path(os.path.abspath(output))
    if lexical == Path(lexical.anchor) or not lexical.name:
        raise ResultPublicationError("output file must not be a filesystem root")
    parent = lexical.parent
    for component in _existing_components(parent):
        if _is_link(component) or (
            component.exists() and not component.is_dir()
        ):
            raise ResultPublicationError("output file parent is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
    for component in _existing_components(parent):
        if _is_link(component) or not component.is_dir():
            raise ResultPublicationError("output file parent became unsafe")
    destination = parent.resolve(strict=True) / lexical.name
    if _is_link(destination) or (
        destination.exists() and not destination.is_file()
    ):
        raise ResultPublicationError("output file is unsafe")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if _is_link(destination) or (
            destination.exists() and not destination.is_file()
        ):
            raise ResultPublicationError("output file became unsafe")
        os.replace(temporary, destination)
        return destination
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        if temporary.is_file() and not _is_link(temporary):
            temporary.unlink()
        raise


def _validated_stage(target: Path, stage_dir: Path) -> Path:
    lexical = Path(os.path.abspath(stage_dir))
    if lexical.parent.resolve(strict=True) != target.parent:
        raise ResultPublicationError("result staging directory is not a sibling of the result")
    if not lexical.name.startswith(_stage_prefix(target)):
        raise ResultPublicationError("result staging directory has an invalid identity")
    if _is_link(lexical) or not lexical.is_dir():
        raise ResultPublicationError("result staging directory is missing or unsafe")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise ResultPublicationError(f"result staging directory is unavailable: {error}") from error
    if resolved.parent != target.parent:
        raise ResultPublicationError("result staging directory escaped its canonical parent")
    return resolved


def begin_publication(
    result_dir: Path, protected_paths: tuple[Path, ...] = ()
) -> Path:
    """Invalidate the prior result and return a private same-parent staging directory."""
    target = _canonical_target(result_dir, protected_paths)
    retired: Path | None = None
    stage: Path | None = None
    try:
        if target.exists():
            retired = Path(
                tempfile.mkdtemp(prefix=f".{target.name}.retired-", dir=target.parent)
            )
            retired.rmdir()
            os.replace(target, retired)
            shutil.rmtree(retired)
            retired = None
        stage = Path(tempfile.mkdtemp(prefix=_stage_prefix(target), dir=target.parent))
        return stage.resolve(strict=True)
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)
        raise


def abort_publication(
    result_dir: Path,
    stage_dir: Path,
    protected_paths: tuple[Path, ...] = (),
) -> None:
    """Discard only the private stage; a failed run never recreates the final path."""
    target = _canonical_target(result_dir, protected_paths)
    stage = _validated_stage(target, stage_dir)
    shutil.rmtree(stage)


def publish_result(
    result_dir: Path,
    stage_dir: Path,
    protected_paths: tuple[Path, ...] = (),
) -> Path:
    """Atomically rename a completed same-parent stage into the final result path."""
    target = _canonical_target(result_dir, protected_paths)
    stage = _validated_stage(target, stage_dir)
    if target.exists() or _is_link(target):
        raise ResultPublicationError("result directory appeared during publication")
    os.replace(stage, target)
    return target


def _paths(values: list[Path] | None) -> tuple[Path, ...]:
    return tuple(values or ())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("begin", "abort", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--result-dir", type=Path, required=True)
        command.add_argument("--protected-path", type=Path, action="append")
        if name != "begin":
            command.add_argument("--stage-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "begin":
            print(begin_publication(args.result_dir, _paths(args.protected_path)))
        elif args.operation == "abort":
            abort_publication(
                args.result_dir, args.stage_dir, _paths(args.protected_path)
            )
        else:
            print(
                publish_result(
                    args.result_dir, args.stage_dir, _paths(args.protected_path)
                )
            )
    except (OSError, ResultPublicationError) as error:
        print(f"result_bundle_publication: {args.operation} failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
