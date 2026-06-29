#!/usr/bin/env bash
set -eu

if ! command -v sudo >/dev/null 2>&1; then
	echo "[install-deps] sudo is required on Ubuntu/WSL" >&2
	exit 1
fi

echo "[install-deps] updating apt index"
sudo apt update

echo "[install-deps] installing project dependencies"
sudo apt install -y \
	git \
	build-essential \
	make \
	python3 \
	python3-pandas \
	python3-seaborn \
	python3-matplotlib \
	qemu-system-misc \
	gcc-riscv64-linux-gnu \
	binutils-riscv64-linux-gnu

echo "[install-deps] verifying installed tools"
bash scripts/check-dependencies.sh
