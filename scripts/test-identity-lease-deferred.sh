#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_LEASE="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_LEASE}"' EXIT

"${CC:-cc}" -std=c11 -Wall -Werror \
	"${ROOT}/scripts/probes/identity-lease-deferred.c" \
	-o "${TMPDIR_LEASE}/identity-lease-deferred"
"${TMPDIR_LEASE}/identity-lease-deferred" >"${TMPDIR_LEASE}/output.log"

for marker in \
	"identity_lease_deferred: interrupt_no_persist=1 maintain_resumed=1" \
	"identity_lease_deferred: causal_reserve=64 proactive_half_window=1" \
	"identity_lease_deferred: lifecycle_maintain_resumed=1" \
	"identity_lease_deferred: pending_not_published=1 retry_published=1" \
	"observe_receipt_context: sie0_safe=1 interrupt_rejected=1 exit_rejected=1 txn_rejected=1 unadmitted_rejected=1" \
	"identity_lease_deferred: passed"; do
	grep -Fxq "${marker}" "${TMPDIR_LEASE}/output.log"
done
cat "${TMPDIR_LEASE}/output.log"
