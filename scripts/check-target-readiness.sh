#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[target-readiness] dual-target program compatibility"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"
