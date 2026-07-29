#!/usr/bin/env python3
"""Link-safe host path primitives shared by acceptance tools."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def absolute_lexical_path(path: Path) -> Path:
    """Return an absolute path without resolving a link component."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def path_components(path: Path) -> list[Path]:
    current = absolute_lexical_path(path)
    components = [current]
    while current.parent != current:
        current = current.parent
        components.append(current)
    components.reverse()
    return components


def path_is_link(path: Path, mode: int | None = None) -> bool:
    if mode is None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            mode = 0
    if stat.S_ISLNK(mode):
        return True
    junction_test = getattr(path, "is_junction", None)
    try:
        return bool(junction_test and junction_test())
    except OSError:
        return True


def reject_link_components(path: Path) -> Path:
    """Reject every existing symlink or junction in a lexical path."""

    absolute = absolute_lexical_path(path)
    for component in path_components(absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if path_is_link(component, info.st_mode):
            raise ValueError(f"Path contains a symbolic link or junction: {component}")
    return absolute


def ensure_safe_directory(path: Path, mode: int = 0o700) -> Path:
    """Create a directory tree without accepting a link component."""

    absolute = absolute_lexical_path(path)
    for component in path_components(absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(component, mode)
            except FileExistsError:
                info = component.lstat()
            else:
                info = component.lstat()
        if path_is_link(component, info.st_mode):
            raise ValueError(f"Path contains a symbolic link or junction: {component}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Path component is not a directory: {component}")
    return absolute


def _require_owned_directory(path: Path) -> Path:
    absolute = reject_link_components(path)
    try:
        info = absolute.stat()
    except FileNotFoundError as error:
        raise ValueError(f"Private directory is missing: {absolute}") from error
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Private directory is unsafe: {absolute}")
    if os.name != "nt" and hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ValueError(f"Private directory owner is unsafe: {absolute}")
    return absolute


def create_private_directory(path: Path) -> Path:
    """Atomically claim a new owner-only directory at an unlinked path."""

    absolute = absolute_lexical_path(path)
    ensure_safe_directory(absolute.parent)
    try:
        os.mkdir(absolute, 0o700)
    except FileExistsError as error:
        reject_link_components(absolute)
        raise ValueError(f"Private directory already exists: {absolute}") from error
    try:
        os.chmod(absolute, 0o700)
        return _require_owned_directory(absolute)
    except Exception:
        try:
            absolute.rmdir()
        except OSError:
            pass
        raise


def require_private_directory(path: Path) -> Path:
    """Validate an existing owner-controlled directory."""

    return _require_owned_directory(path)


def ensure_private_directory(path: Path) -> Path:
    """Create or tighten an owner-controlled directory without following links."""

    absolute = ensure_safe_directory(path, 0o700)
    absolute = _require_owned_directory(absolute)
    os.chmod(absolute, 0o700)
    return _require_owned_directory(absolute)
