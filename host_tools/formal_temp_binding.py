#!/usr/bin/env python3
"""为正式证据执行绑定 POSIX 与原生临时路径。"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    from .evaluation_source_gate import ToolAttestationError
    from .safe_host_paths import (
        loaded_msys_path_api,
        path_is_link,
        require_safe_directory,
    )
except ImportError:
    from evaluation_source_gate import ToolAttestationError
    from safe_host_paths import loaded_msys_path_api, path_is_link, require_safe_directory


_CCP_POSIX_TO_WIN_W = 1
_CCP_WIN_W_TO_POSIX = 3


def _loaded_cygwin_path_api():
    """仅通过进程内已加载的 POSIX 运行时解析路径转换。"""

    api = loaded_msys_path_api()
    if api is None:
        raise ToolAttestationError("loaded Cygwin path conversion API is unavailable")
    ctypes, function, _attributes, runtime_name = api
    return ctypes, function, runtime_name


def _cygwin_convert_path(value: str, *, to_windows: bool) -> str:
    ctypes, function, _runtime_name = _loaded_cygwin_path_api()
    direction = _CCP_POSIX_TO_WIN_W if to_windows else _CCP_WIN_W_TO_POSIX
    source = (
        ctypes.create_string_buffer(os.fsencode(value))
        if to_windows else ctypes.create_unicode_buffer(value)
    )
    source_pointer = ctypes.cast(source, ctypes.c_void_p)
    size = int(function(direction, source_pointer, None, 0))
    if size <= 0 or size > 128 * 1024:
        raise ToolAttestationError("Cygwin path conversion size is invalid")
    target = ctypes.create_string_buffer(size)
    if function(direction, source_pointer, ctypes.cast(target, ctypes.c_void_p), size):
        raise ToolAttestationError("Cygwin path conversion failed")
    converted = (
        target.raw.decode("utf-16-le").split("\0", 1)[0]
        if to_windows else os.fsdecode(target.value)
    )
    if not converted or any(character in converted for character in "\n\r\0"):
        raise ToolAttestationError("Cygwin path conversion output is invalid")
    return converted


def cygwin_native_directory(path: Path) -> str:
    try:
        posix_path = str(path.resolve(strict=True))
        if not path.is_dir() or path_is_link(path):
            raise OSError("temporary path is not an ordinary directory")
        native_path = _cygwin_convert_path(posix_path, to_windows=True)
        roundtrip = _cygwin_convert_path(native_path, to_windows=False)
        if not os.path.samefile(posix_path, native_path) or not os.path.samefile(
            posix_path, roundtrip
        ):
            raise OSError("temporary path conversion changed identity")
    except (OSError, ValueError) as error:
        raise ToolAttestationError(
            "Cygwin temporary directory conversion is not identity-preserving"
        ) from error
    canonical = native_path.replace("\\", "/")
    if re.fullmatch(
        r"[A-Z]:/(?:[^\\/:\x00-\x1f\x7f]+(?:/[^\\/:\x00-\x1f\x7f]+)*)?",
        canonical,
    ) is None:
        raise ToolAttestationError("Cygwin native temporary path is not canonical")
    return canonical


def capture_formal_temporary_binding(
    environment: dict[str, str],
) -> dict[str, object]:
    """记录 POSIX 与原生临时路径共享的已校验身份。"""

    try:
        temporary = require_safe_directory(Path(environment["TMPDIR"]))
        posix_path = str(temporary.resolve(strict=True))
        if environment["TMPDIR"] != posix_path:
            raise ValueError("POSIX temporary path is not canonical")
        info = temporary.stat()
        if sys.platform == "cygwin":
            native_path = cygwin_native_directory(temporary)
            roundtrip_path = _cygwin_convert_path(native_path, to_windows=False)
            _ctypes, _function, runtime_name = _loaded_cygwin_path_api()
            execution_platform = "cygwin"
            conversion_api = f"{runtime_name}:cygwin_conv_path"
        else:
            native_path = roundtrip_path = posix_path
            execution_platform, conversion_api = "posix", "identity"
        if (
            environment["TEMP"] != native_path
            or environment["TMP"] != native_path
            or roundtrip_path != posix_path
            or not os.path.samefile(posix_path, native_path)
            or not os.path.samefile(posix_path, roundtrip_path)
        ):
            raise ValueError("temporary paths do not share one identity")
        identities = {
            label: {
                "device": int(path_info.st_dev),
                "inode": int(path_info.st_ino),
            }
            for label, path_info in (
                ("posix", info),
                ("native", os.stat(native_path)),
                ("roundtrip", os.stat(roundtrip_path)),
            )
        }
        if len({tuple(identity.values()) for identity in identities.values()}) != 1:
            raise ValueError("temporary path identities differ")
    except (KeyError, OSError, ValueError) as error:
        raise ToolAttestationError(
            "formal temporary directory identity binding failed"
        ) from error
    return {
        "schema_version": 1,
        "kind": "formal-temporary-directory-binding",
        "execution_platform": execution_platform,
        "conversion_api": conversion_api,
        "posix_path": posix_path,
        "native_path": native_path,
        "roundtrip_path": roundtrip_path,
        "identities": identities,
        "checks": ["posix-native-samefile", "posix-roundtrip-samefile"],
    }
