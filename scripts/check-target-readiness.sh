#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[target-readiness] target structure"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"
