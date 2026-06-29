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

check_python_module() {
	local module="$1"
	if python3 -c "import ${module}" >/dev/null 2>&1; then
		echo "[deps] ok: python module ${module}"
	else
		echo "[deps] recommended missing: python module ${module}"
	fi
}

echo "[deps] checking Linux/WSL tools"
check_cmd bash
check_cmd git
check_cmd make
check_cmd python3
check_cmd qemu-system-riscv64
check_cmd riscv64-linux-gnu-gcc
check_cmd riscv64-linux-gnu-ld
check_cmd riscv64-linux-gnu-objcopy
check_cmd riscv64-linux-gnu-objdump
check_python_module pandas
check_python_module seaborn
check_python_module matplotlib

if [ "${missing}" -ne 0 ]; then
	cat <<'EOF'
[deps] install on Ubuntu/WSL:
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu \
  python3-pandas python3-seaborn python3-matplotlib
EOF
	exit 1
fi

echo "[deps] ready"
