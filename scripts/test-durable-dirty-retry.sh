#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_RETRY="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_RETRY}"' EXIT
source "${ROOT}/scripts/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_RETRY}"

host_probe_compile "${TMPDIR_RETRY}/durable-dirty-retry" \
	-std=c11 -Wall -Werror \
	"${ROOT}/scripts/probes/durable-dirty-retry.c"
host_probe_run "${TMPDIR_RETRY}/durable-dirty-retry" \
	>"${TMPDIR_RETRY}/output.log"

for marker in \
	"durable_dirty_retry: store_provider=1" \
	"durable_dirty_retry: system_sink_zero=1" \
	"durable_dirty_retry: commit_retry=1" \
	"durable_dirty_retry: system_reserve_slot=1" \
	"durable_dirty_retry: serial_exhaustion_fail_closed=1" \
	"durable_dirty_retry: capture_serial_fence=1" \
	"durable_dirty_retry: passed"; do
	grep -Fxq "${marker}" "${TMPDIR_RETRY}/output.log"
done
cat "${TMPDIR_RETRY}/output.log"
host_probe_report "durable-dirty-retry"
