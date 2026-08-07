"""正式时长配置的闭合模式可重放认证。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from .formal_python_runtime import (
        FormalPythonRuntimeError, validate_duration_profile_policy_marker,
    )
except ImportError:
    from formal_python_runtime import (
        FormalPythonRuntimeError, validate_duration_profile_policy_marker,
    )


SCHEMA_VERSION = 2
CONFIGURATION_PATH = "ci/kernel-budgets.json"
EXECUTION_TOOL_LABELS = (
    "bash", "compiler", "git", "host_cc", "make", "python", "qemu",
)


class DurationAttestationError(ValueError):
    """时长配置认证不完整或伪造时抛出。"""


def _platform_contract() -> tuple[Any, Any, Any]:
    try:
        from .evaluation_campaign import _validate_platform_proof
        from .evaluation_platform import (
            probe_native_collection_domain, validate_duration_profile_binding,
        )
    except ImportError:
        from evaluation_campaign import _validate_platform_proof
        from evaluation_platform import (
            probe_native_collection_domain, validate_duration_profile_binding,
        )
    return (
        probe_native_collection_domain, _validate_platform_proof,
        validate_duration_profile_binding,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _configuration(root: Path) -> dict[str, str]:
    path = root / CONFIGURATION_PATH
    if not path.is_file() or path.is_symlink():
        raise DurationAttestationError("duration profile configuration is unavailable")
    return {"path": CONFIGURATION_PATH, "sha256": _sha256(path)}


def duration_attestation_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def duration_platform_identity(platform: object) -> dict[str, object]:
    if not isinstance(platform, dict) or not isinstance(platform.get("tools"), dict):
        raise DurationAttestationError("duration platform identity is unavailable")
    required = {
        "domain", "duration_profile", "entry_domain", "hardware", "runtime",
        "tools", "uname",
    }
    if not required.issubset(platform):
        raise DurationAttestationError("duration platform identity is incomplete")
    return {key: platform[key] for key in sorted(required)}


def duration_platform_identity_sha256(platform: object) -> str:
    return duration_attestation_sha256(duration_platform_identity(platform))


def _tool_binary_identity(record: object) -> dict[str, object]:
    """返回跨探针身份；每个探针分别校验自身版本日志。"""

    if not isinstance(record, Mapping):
        raise DurationAttestationError("duration execution tool identity is invalid")
    identity = {
        "path": record.get("path"),
        "sha256": record.get("sha256", record.get("executable_sha256")),
    }
    if not all(isinstance(identity[key], str) and identity[key] for key in identity):
        raise DurationAttestationError("duration execution tool identity is invalid")
    return identity


def validate_duration_execution_binding(
    attestation: object, execution_tools: object, *,
    expected_platform: object | None = None,
) -> None:
    if not isinstance(attestation, dict):
        raise DurationAttestationError("duration attestation is unavailable")
    profile = attestation.get("profile")
    if isinstance(profile, dict) and profile.get("name") == "none":
        return
    platform = attestation.get("platform")
    tools = platform.get("tools") if isinstance(platform, dict) else None
    if not isinstance(tools, dict) or not isinstance(execution_tools, Mapping):
        raise DurationAttestationError("local-e3 execution tool binding is unavailable")
    for label in EXECUTION_TOOL_LABELS:
        if _tool_binary_identity(tools.get(label)) != _tool_binary_identity(
            execution_tools.get(label)
        ):
            raise DurationAttestationError(
                f"local-e3 execution tool differs from platform proof: {label}"
            )
    identity = duration_platform_identity_sha256(platform)
    if attestation.get("platform_identity_sha256") != identity:
        raise DurationAttestationError("duration platform identity digest differs")
    if expected_platform is not None and duration_platform_identity_sha256(
        expected_platform
    ) != identity:
        raise DurationAttestationError("duration platform differs from campaign")


def build_duration_attestation(
    *, contract_root: Path, profile: str, toolprefix: str, qemu: str,
    python_bin: str, host_cc: str, shell_bin: str,
) -> dict[str, Any]:
    root = contract_root.resolve(strict=True)
    configuration = _configuration(root)
    if profile == "none":
        return {
            "applicability": "not-applicable-different-runner",
            "configuration": configuration,
            "platform": None,
            "platform_identity_sha256": None,
            "profile": {
                "calibration_status": "not-applicable", "name": "none",
                "profile_id": "none", "status": "disabled-different-runner",
            },
            "schema_version": SCHEMA_VERSION,
        }
    if profile != "local-e3":
        raise DurationAttestationError("duration profile is invalid")
    try:
        probe_domain, validate_platform, _validate_profile = _platform_contract()
        platform = probe_domain(
            repo=root, toolprefix=toolprefix, qemu=qemu,
            python_bin=python_bin, host_cc=host_cc,
            duration_profile=profile, shell_bin=shell_bin,
        )
        platform["entry_domain"] = platform["domain"]
        validate_platform(
            platform, str(platform["entry_domain"]), contract_root=root
        )
    except (OSError, ValueError) as error:
        raise DurationAttestationError(
            f"local-e3 duration profile proof failed: {error}"
        ) from error
    if platform["duration_profile"].get("calibration_status") != "calibrated_full_suite":
        raise DurationAttestationError(
            "local-e3 duration attestation requires calibrated_full_suite"
        )
    return {
        "applicability": "calibrated-local-e3",
        "configuration": configuration,
        "platform": platform,
        "platform_identity_sha256": duration_platform_identity_sha256(platform),
        "profile": dict(platform["duration_profile"]),
        "schema_version": SCHEMA_VERSION,
    }


def validate_duration_attestation(
    value: object, *, contract_root: Path,
    configuration_path: Path | None = None,
) -> str:
    expected = {
        "applicability", "configuration", "platform", "profile",
        "platform_identity_sha256", "schema_version",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise DurationAttestationError("duration attestation schema differs")
    root = contract_root.resolve(strict=True)
    if value.get("configuration") != _configuration(root):
        raise DurationAttestationError(
            "duration attestation configuration differs from trusted source"
        )
    if configuration_path is not None and _sha256(configuration_path) != value[
        "configuration"
    ]["sha256"]:
        raise DurationAttestationError(
            "duration attestation differs from packaged configuration"
        )
    profile = value.get("profile")
    if isinstance(profile, dict) and profile.get("name") == "none":
        canonical = build_duration_attestation(
            contract_root=root, profile="none", toolprefix="not-applicable",
            qemu="not-applicable", python_bin="not-applicable",
            host_cc="not-applicable", shell_bin="not-applicable",
        )
        if value != canonical:
            raise DurationAttestationError("none duration attestation is not canonical")
        return "none"
    platform = value.get("platform")
    if value.get("applicability") != "calibrated-local-e3" or not isinstance(
        platform, dict
    ) or profile != platform.get("duration_profile"):
        raise DurationAttestationError("local-e3 duration attestation is incomplete")
    try:
        _probe_domain, validate_platform, validate_profile = _platform_contract()
        validate_platform(
            platform, str(platform.get("entry_domain", "")), contract_root=root
        )
        name = validate_profile(platform, repository=root)
    except (OSError, ValueError) as error:
        raise DurationAttestationError(
            f"local-e3 duration attestation replay failed: {error}"
        ) from error
    if name != "local-e3":
        raise DurationAttestationError("duration attestation profile differs")
    if profile.get("calibration_status") != "calibrated_full_suite":
        raise DurationAttestationError(
            "local-e3 duration attestation requires calibrated_full_suite"
        )
    if value.get("platform_identity_sha256") != duration_platform_identity_sha256(
        platform
    ):
        raise DurationAttestationError("duration platform identity digest differs")
    return name


def validate_attested_duration_policy(
    attestation: object, *, contract_root: Path,
    environment: object, log_lines: list[str],
    execution_tools: object, expected_platform: object | None = None,
    configuration_path: Path | None = None,
) -> str:
    try:
        policy_profile = validate_duration_profile_policy_marker(
            environment, log_lines
        )
    except FormalPythonRuntimeError as error:
        raise DurationAttestationError(str(error)) from error
    attested_profile = validate_duration_attestation(
        attestation, contract_root=contract_root,
        configuration_path=configuration_path,
    )
    if attested_profile != policy_profile:
        raise DurationAttestationError(
            "duration attestation differs from execution policy"
        )
    validate_duration_execution_binding(
        attestation, execution_tools, expected_platform=expected_platform
    )
    return policy_profile


__all__ = [
    "DurationAttestationError", "build_duration_attestation",
    "duration_attestation_sha256", "duration_platform_identity_sha256",
    "validate_attested_duration_policy", "validate_duration_attestation",
    "validate_duration_execution_binding",
]
