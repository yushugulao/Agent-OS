#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_REAP="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_REAP}"' EXIT
source "${ROOT}/scripts/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_REAP}"

host_probe_compile "${TMPDIR_REAP}/observe-reap-state" \
	-std=c11 -Wall -Werror \
	"${ROOT}/scripts/probes/observe-reap-state.c"
host_probe_run "${TMPDIR_REAP}/observe-reap-state" \
	>"${TMPDIR_REAP}/output.log"

for marker in \
	"observe_reap_state: five_slots=1 sticky_class=1" \
	"observe_reap_state: class_admission=1 active_full=1 pending_retry=1" \
	"observe_reap_state: same_workflow_abort=1 cross_scope=1" \
	"observe_reap_state: zero_target_retry=1 same_token=1" \
	"observe_reap_state: lost_callback_recovery=2 tick_rearm=1" \
	"observe_reap_state: attach_generation_stable=1" \
	"observe_reap_state: serial_target_token=1 reap_retry_same_token=1 reap_delivery_reissue=1 done_race=1 cookie_fields=6 delivery_retry=1 consume_once=1" \
	"observe_reap_state: recover_pending_idempotent=1 recover_done_idempotent=1 conflict_closed=1 recover_authorized_promote=1 recovered_generation_token=1" \
	"observe_reap_state: passed"; do
	grep -Fxq "${marker}" "${TMPDIR_REAP}/output.log"
done
cat "${TMPDIR_REAP}/output.log"
host_probe_report "observe-reap-state"
