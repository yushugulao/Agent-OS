#!/usr/bin/env python3
"""编译内核磁盘布局探针并与 Host 契约比较。"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_tools.agent_metadata_disk_format import (
    DEFAULT_CONTRACT,
    SUPPORTED_DESCRIPTOR_MAGIC,
    SUPPORTED_DESCRIPTOR_VERSION,
    SUPPORTED_HASH,
    SUPPORTED_HASH_ID,
    load_contract,
)


PROBE = ROOT / "scripts" / "probes" / "agent-metadata-disk-layout.c"
SECTION = ".agent_metadata_layout"
WORD_COUNT = 28
WORDS = struct.Struct(f"<{WORD_COUNT}Q")


class ProbeError(RuntimeError):
    pass


def _run(command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        raise ProbeError(f"cannot execute {command[0]}: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ProbeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}")


def compile_descriptor(cc: str, objcopy: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="agent-metadata-layout-") as directory:
        temporary = Path(directory)
        object_path = temporary / "layout.o"
        section_path = temporary / "layout.bin"
        _run(
            [
                cc,
                "-std=gnu11",
                "-march=rv64imac_zicsr_zifencei",
                "-mabi=lp64",
                "-mcmodel=medany",
                "-fno-builtin",
                "-ffreestanding",
                "-fno-pie",
                f"-I{ROOT}",
                f"-I{ROOT / 'os'}",
                "-c",
                str(PROBE),
                "-o",
                str(object_path),
            ]
        )
        _run(
            [
                objcopy,
                "--dump-section",
                f"{SECTION}={section_path}",
                str(object_path),
            ]
        )
        try:
            return section_path.read_bytes()
        except OSError as error:
            raise ProbeError(f"cannot read probe section: {error}") from error


def descriptor_contract(raw: bytes) -> dict[str, object]:
    if len(raw) < WORDS.size:
        raise ProbeError(f"short layout descriptor: {len(raw)} bytes")
    words = WORDS.unpack_from(raw)
    (
        descriptor_magic,
        descriptor_version,
        descriptor_bytes,
        disk_magic,
        disk_version,
        hash_algorithm,
        hash_initial,
        hash_prime,
        header_bytes,
        header_integer_bytes,
        header_magic,
        header_version,
        header_count,
        header_generation,
        header_payload_hash,
        durable_arena_bytes,
        record_bytes,
        record_used,
        record_used_bytes,
        record_fid,
        record_fid_bytes,
        record_physical,
        record_physical_bytes,
        record_status,
        record_status_bytes,
        max_count,
        bank_name_bytes,
        bank_count,
    ) = words
    if descriptor_magic != SUPPORTED_DESCRIPTOR_MAGIC:
        raise ProbeError(f"bad descriptor magic 0x{descriptor_magic:016x}")
    if descriptor_version != SUPPORTED_DESCRIPTOR_VERSION:
        raise ProbeError(f"unsupported descriptor version {descriptor_version}")
    if hash_algorithm != SUPPORTED_HASH_ID:
        raise ProbeError(f"unsupported hash algorithm id {hash_algorithm}")
    if descriptor_bytes != len(raw):
        raise ProbeError(
            f"descriptor size mismatch: header={descriptor_bytes} section={len(raw)}"
        )
    if bank_count != 2 or bank_name_bytes == 0:
        raise ProbeError(
            f"unsupported bank vector count={bank_count} width={bank_name_bytes}"
        )
    names_bytes = bank_count * bank_name_bytes
    if WORDS.size + names_bytes != len(raw):
        raise ProbeError("descriptor bank-name vector has inconsistent size")
    names: list[str] = []
    for index in range(bank_count):
        start = WORDS.size + index * bank_name_bytes
        field = raw[start : start + bank_name_bytes]
        end = field.find(b"\0")
        if end <= 0:
            raise ProbeError(f"bank name {index} is empty or unterminated")
        try:
            names.append(field[:end].decode("ascii", errors="strict"))
        except UnicodeDecodeError as error:
            raise ProbeError(f"bank name {index} is not ASCII") from error

    return {
        "schema": 1,
        "descriptor": {
            "magic": f"0x{descriptor_magic:016x}",
            "version": descriptor_version,
            "bytes": descriptor_bytes,
        },
        "disk": {
            "magic": f"0x{disk_magic:016x}",
            "version": disk_version,
            "byte_order": "little",
            "bank_name_bytes": bank_name_bytes,
            "bank_names": names,
            "hash": {
                "algorithm": SUPPORTED_HASH,
                "algorithm_id": hash_algorithm,
                "initial": f"0x{hash_initial:016x}",
                "prime": f"0x{hash_prime:016x}",
            },
            "header": {
                "bytes": header_bytes,
                "integer_bytes": header_integer_bytes,
                "fields": {
                    "magic": header_magic,
                    "version": header_version,
                    "count": header_count,
                    "generation": header_generation,
                    "payload_hash": header_payload_hash,
                },
                "payload_hash_bytes": header_integer_bytes,
            },
            "durable_arena_bytes": durable_arena_bytes,
            "record": {
                "bytes": record_bytes,
                "fields": {
                    "used": record_used,
                    "fid": record_fid,
                    "physical_name": record_physical,
                    "status": record_status,
                },
                "used_bytes": record_used_bytes,
                "fid_bytes": record_fid_bytes,
                "physical_name_bytes": record_physical_bytes,
                "status_bytes": record_status_bytes,
                "max_count": max_count,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--cc", default="riscv64-linux-gnu-gcc")
    parser.add_argument("--objcopy", default="riscv64-linux-gnu-objcopy")
    parser.add_argument("--print-actual", action="store_true")
    args = parser.parse_args()

    try:
        load_contract(args.contract)
        expected = json.loads(args.contract.read_text(encoding="utf-8"))
        actual = descriptor_contract(compile_descriptor(args.cc, args.objcopy))
    except (OSError, ValueError, ProbeError) as error:
        parser.error(str(error))
    if args.print_actual:
        print(json.dumps(actual, indent=2, sort_keys=True))
    if actual != expected:
        print("agent metadata disk-format contract drifted", flush=True)
        print("expected:", json.dumps(expected, sort_keys=True), flush=True)
        print("actual:  ", json.dumps(actual, sort_keys=True), flush=True)
        return 1
    print("agent metadata disk-format contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
