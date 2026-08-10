#!/usr/bin/env bash
set -eu

missing=0

check_cmd() {
	local name="$1"

	if command -v "${name}" >/dev/null 2>&1; then
		echo "[deps] ok: ${name} -> $(command -v "${name}")"
	else
		echo "[deps] missing: ${name}"
		missing=1
	fi
}

echo "[deps] checking Linux/WSL tools"
check_cmd "${BASH_BIN:-bash}"
check_cmd git
check_cmd "${MAKE_TOOL:-make}"
check_cmd "${PYTHON_BIN:-python3}"
check_cmd "${HOST_CC:-${HOSTCC:-cc}}"
check_cmd "${QEMU:-qemu-system-riscv64}"
check_cmd "${TOOLPREFIX:-riscv64-linux-gnu-}gcc"
check_cmd "${TOOLPREFIX:-riscv64-linux-gnu-}ld"
check_cmd "${TOOLPREFIX:-riscv64-linux-gnu-}objcopy"
check_cmd "${TOOLPREFIX:-riscv64-linux-gnu-}objdump"
if [ "${missing}" -ne 0 ]; then
	cat <<'EOF'
[deps] install on Ubuntu/WSL:
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
EOF
	exit 1
fi

echo "[deps] ready"
