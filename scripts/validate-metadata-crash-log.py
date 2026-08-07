#!/usr/bin/env python3
"""校验元数据崩溃触发器经认证的顺序与身份。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PROGRAM = "agentmetacrash_ucore"
PHASE_NAMES = {
    1: "invalidate-stage",
    2: "payload-stage",
    3: "prepared-flush",
    4: "payload-verify",
    5: "publish-stage",
    6: "header-flush",
    7: "header-verify",
    8: "commit",
}


class ValidationError(RuntimeError):
    """该日志不能作为元数据崩溃测试证据。"""


MARKERS = {
    "baseline": (
        f"{PROGRAM}: baseline_dirty",
        re.compile(
            rf"{PROGRAM}: baseline_dirty=(?P<dirty>0x[0-9a-f]{{16}}) "
            r"baseline_durable=(?P<durable>0x[0-9a-f]{16}) "
            r"pending=(?P<pending>0|[1-9][0-9]*)"
        ),
    ),
    "ready": (
        f"{PROGRAM}: baseline_ready",
        re.compile(rf"{PROGRAM}: baseline_ready=1 replicated=1"),
    ),
    "armed": (
        f"{PROGRAM}: target_armed",
        re.compile(
            rf"{PROGRAM}: target_armed "
            r"scope=(?P<scope>0|[1-9a-f][0-9a-f]{0,7}) "
            r"generation=(?P<generation>0x[0-9a-f]{16}) "
            r"token=(?P<token>0x[0-9a-f]{16})"
        ),
    ),
    "bound": (
        f"{PROGRAM}: target_bound",
        re.compile(
            rf"{PROGRAM}: target_bound "
            r"scope=(?P<scope>0|[1-9a-f][0-9a-f]{0,7}) "
            r"generation=(?P<generation>0x[0-9a-f]{16}) "
            r"token=(?P<token>0x[0-9a-f]{16}) "
            r"job=(?P<job>0x[0-9a-f]{16})"
        ),
    ),
    "fire": (
        f"{PROGRAM}: target_fire",
        re.compile(
            rf"{PROGRAM}: target_fire "
            r"scope=(?P<scope>0|[1-9a-f][0-9a-f]{0,7}) "
            r"generation=(?P<generation>0x[0-9a-f]{16}) "
            r"token=(?P<token>0x[0-9a-f]{16}) "
            r"job=(?P<job>0x[0-9a-f]{16}) "
            r"bank=(?P<bank>[01]) phase=(?P<phase>[1-8])"
        ),
    ),
    "phase": (
        f"{PROGRAM}: metadata_phase",
        re.compile(rf"{PROGRAM}: metadata_phase=(?P<phase>[1-8])"),
    ),
}


def _integer(fields: dict[str, str], name: str, base: int = 10) -> int:
    return int(fields[name], base)


def validate_log(raw: bytes, expected_bank: str, expected_phase: int) -> None:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValidationError(f"log is not valid UTF-8: {error}") from error

    found: dict[str, tuple[int, dict[str, str]]] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        for name, (prefix, pattern) in MARKERS.items():
            if prefix not in line:
                continue
            if name in found:
                raise ValidationError(
                    f"duplicate {name} marker at line {line_number}"
                )
            match = pattern.fullmatch(line)
            if match is None:
                raise ValidationError(
                    f"malformed {name} marker at line {line_number}"
                )
            found[name] = (line_number, match.groupdict())

    missing = [name for name in MARKERS if name not in found]
    if missing:
        raise ValidationError("missing marker(s): " + ", ".join(missing))

    baseline_line, baseline = found["baseline"]
    ready_line, _ = found["ready"]
    armed_line, armed = found["armed"]
    bound_line, bound = found["bound"]
    fire_line, fire = found["fire"]
    phase_line, phase = found["phase"]
    if not (
        baseline_line <= ready_line
        and ready_line < armed_line < bound_line < fire_line < phase_line
    ):
        raise ValidationError(
            "marker order must be baseline <= ready < armed < bound < fire < phase"
        )

    dirty = _integer(baseline, "dirty", 16)
    durable = _integer(baseline, "durable", 16)
    pending = _integer(baseline, "pending")
    if dirty != durable or pending != 0:
        raise ValidationError(
            f"baseline is not quiet: dirty={dirty} durable={durable} pending={pending}"
        )

    identity = (armed["scope"], armed["generation"], armed["token"])
    if (bound["scope"], bound["generation"], bound["token"]) != identity:
        raise ValidationError("bound target identity differs from armed target")
    if (fire["scope"], fire["generation"], fire["token"]) != identity:
        raise ValidationError("fired target identity differs from armed target")
    if bound["job"] != fire["job"]:
        raise ValidationError("fired job differs from bound job")

    scope = _integer(armed, "scope", 16)
    generation = _integer(armed, "generation", 16)
    token = _integer(armed, "token", 16)
    job = _integer(bound, "job", 16)
    if scope == 0 or token == 0 or job == 0:
        raise ValidationError("scope, token, and job identities must be non-zero")
    if generation != dirty + 1:
        raise ValidationError(
            f"target generation {generation} does not follow baseline {dirty}"
        )

    expected_bank_number = {"primary": 0, "mirror": 1}[expected_bank]
    if _integer(fire, "bank") != expected_bank_number:
        raise ValidationError(
            f"fired bank does not match requested {expected_bank} bank"
        )
    if _integer(fire, "phase") != expected_phase:
        raise ValidationError("fired phase does not match requested phase")
    if _integer(phase, "phase") != expected_phase:
        raise ValidationError("terminal metadata phase does not match requested phase")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate metadata crash-test marker attestation"
    )
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--bank", required=True, choices=("primary", "mirror"))
    parser.add_argument("--phase", required=True, type=int, choices=PHASE_NAMES)
    args = parser.parse_args(argv)

    try:
        raw = args.log_file.read_bytes()
        validate_log(raw, args.bank, args.phase)
    except (OSError, ValidationError) as error:
        print(f"metadata crash log validation failed: {error}", file=sys.stderr)
        return 1

    print(
        f"metadata crash log: ok bank={args.bank} phase={args.phase} "
        f"checkpoint={PHASE_NAMES[args.phase]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
