#!/usr/bin/env python3
"""Host 验收工具共享的失败关闭式 JSON 解码。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DuplicateJSONKey(ValueError):
    """对象成员名重复时抛出。"""


class NonFiniteJSONNumber(ValueError):
    """JSON 使用非标准 NaN 或 Infinity token 时抛出。"""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKey(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise NonFiniteJSONNumber(f"non-finite JSON number {value!r}")


def strict_json_loads(value: str | bytes | bytearray) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8")
    return json.loads(
        value,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_nonfinite,
    )


def read_strict_json(path: Path) -> Any:
    return strict_json_loads(path.read_bytes())
