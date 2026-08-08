#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned int uint;
typedef unsigned char uchar;
typedef uint64_t uint64;

#define TYPES_H
#include "../../os/agent_observe_persist_context.h"

#define WORKFLOW_LIFECYCLE_CAP 8U
#define AGENT_IDENTITY_LEASE_CHUNK 4096ULL
#define AGENT_IDENTITY_LEASE_LOW_WATER \
	(AGENT_IDENTITY_LEASE_CHUNK / 2ULL)

enum agent_identity_allocator_kind {
	AGENT_IDENTITY_ALLOCATOR_AUDIT = 0,
	AGENT_IDENTITY_ALLOCATOR_SPAN,
	AGENT_IDENTITY_ALLOCATOR_EVENT,
	AGENT_IDENTITY_ALLOCATOR_CONTROL,
	AGENT_IDENTITY_ALLOCATOR_AGENT,
	AGENT_IDENTITY_ALLOCATOR_COUNT,
};

struct agent_identity_lease_snapshot {
	uint64 ends[AGENT_IDENTITY_ALLOCATOR_COUNT];
	uint64 lifecycle_ends[WORKFLOW_LIFECYCLE_CAP];
};

typedef int (*agent_identity_lease_persist_fn)(uint64 *, uint64 *);

static int interrupts_enabled = 1;
static int persist_result = 1;
static uint persist_calls;

static int intr_save(void)
{
	int prior = interrupts_enabled;

	interrupts_enabled = 0;
	return prior;
}
static void intr_restore(int enabled) { interrupts_enabled = enabled; }

#define panic(message) abort()
#define AGENT_IDENTITY_LEASE_H
#define DEFS_H
#define RISCV_H
#include "../../os/agent_identity_lease.c"

static int persist_candidate(uint64 *serial, uint64 *target)
{
	struct agent_identity_lease_snapshot snapshot;

	assert(agent_identity_lease_maintenance_pending());
	persist_calls++;
	agent_identity_lease_snapshot(&snapshot);
	assert(snapshot.ends[AGENT_IDENTITY_ALLOCATOR_AUDIT] > 1);
	assert(snapshot.lifecycle_ends[0] > 1);
	if (*serial == 0)
		*serial = persist_calls;
	if (*target == 0)
		*target = persist_calls;
	return persist_result;
}

static void reset_lease(void)
{
	interrupts_enabled = 1;
	persist_result = 1;
	persist_calls = 0;
	agent_identity_lease_init();
	agent_identity_lease_set_persist(persist_candidate);
}

static void verify_allocator_deferred_renew(void)
{
	uint calls;

	reset_lease();
	assert(agent_identity_lease_storage_ready() == 0);
	assert(agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT,
		       AGENT_IDENTITY_LEASE_CHUNK));
	assert(!agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT,
		       AGENT_IDENTITY_LEASE_CHUNK + 1));
	calls = persist_calls;
	interrupts_enabled = 0;
	assert(agent_identity_lease_allocator_renew(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT) == -1);
	assert(persist_calls == calls);
	assert(agent_identity_lease_maintenance_pending());
	agent_identity_lease_maintain();
	assert(persist_calls == calls + 1);
	assert(!agent_identity_lease_maintenance_pending());
	assert(agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT, 257));
	puts("identity_lease_deferred: interrupt_no_persist=1 maintain_resumed=1");
}

static void verify_allocator_reserve_and_proactive_renew(void)
{
	reset_lease();
	assert(agent_identity_lease_storage_ready() == 0);
	assert(agent_identity_lease_allocator_admit(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT,
		       AGENT_IDENTITY_LEASE_CHUNK - 64, 64));
	assert(!agent_identity_lease_allocator_admit(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT,
		       AGENT_IDENTITY_LEASE_CHUNK - 63, 64));
	assert(agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT,
		       AGENT_IDENTITY_LEASE_CHUNK - 63));
	assert(!agent_identity_lease_maintenance_pending());
	agent_identity_lease_allocator_note_next(
		AGENT_IDENTITY_ALLOCATOR_AUDIT,
		AGENT_IDENTITY_LEASE_LOW_WATER + 1);
	assert(agent_identity_lease_maintenance_pending());
	agent_identity_lease_maintain();
	assert(agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT,
		       2 * AGENT_IDENTITY_LEASE_CHUNK));
	puts("identity_lease_deferred: causal_reserve=64 proactive_half_window=1");
}

static void verify_lifecycle_deferred_renew(void)
{
	uint calls;

	reset_lease();
	assert(agent_identity_lease_storage_ready() == 0);
	assert(!agent_identity_lease_lifecycle_contains(
		0, AGENT_IDENTITY_LEASE_CHUNK + 1));
	calls = persist_calls;
	interrupts_enabled = 0;
	assert(agent_identity_lease_lifecycle_renew() == -1);
	assert(persist_calls == calls);
	assert(agent_identity_lease_maintenance_pending());
	agent_identity_lease_maintain();
	assert(persist_calls == calls + 1);
	assert(!agent_identity_lease_maintenance_pending());
	assert(agent_identity_lease_lifecycle_contains(
		0, AGENT_IDENTITY_LEASE_CHUNK + 1));
	puts("identity_lease_deferred: lifecycle_maintain_resumed=1");
}

static void verify_pending_boot_lease(void)
{
	reset_lease();
	persist_result = 0;
	assert(agent_identity_lease_storage_ready() == -1);
	assert(!agent_identity_lease_admission_ready());
	assert(!agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT, 1));
	assert(agent_identity_lease_maintenance_pending());
	persist_result = 1;
	interrupts_enabled = 0;
	agent_identity_lease_maintain();
	assert(agent_identity_lease_admission_ready());
	assert(!agent_identity_lease_maintenance_pending());
	assert(agent_identity_lease_allocator_contains(
		       AGENT_IDENTITY_ALLOCATOR_AUDIT, 1));
	puts("identity_lease_deferred: pending_not_published=1 retry_published=1");
}

static void verify_receipt_persist_context(void)
{
	struct agent_observe_persist_context context = {
		.running = 1,
		.kernel_work_depth = 1,
		.io_request_depth = 1,
		.buffer_holds = 0,
		.fs_atomic_depth = 0,
		.sstatus = 0,
		.supervisor_previous_mask = 1ULL << 8,
		.fs_epoch_held = 1,
		.metadata_txn_owned = 0,
		.exit_requested = 0,
	};

	/* 用户态系统调用陷入时 SIE=0、SPP=0。 */
	assert(agent_observe_receipt_persist_context_safe(&context));
	context.sstatus = context.supervisor_previous_mask;
	assert(!agent_observe_receipt_persist_context_safe(&context));
	context.sstatus = 0;
	context.exit_requested = 1;
	assert(!agent_observe_receipt_persist_context_safe(&context));
	context.exit_requested = 0;
	context.metadata_txn_owned = 1;
	assert(!agent_observe_receipt_persist_context_safe(&context));
	context.metadata_txn_owned = 0;
	context.fs_epoch_held = 0;
	assert(!agent_observe_receipt_persist_context_safe(&context));
	context.fs_epoch_held = 1;
	context.io_request_depth = 0;
	assert(!agent_observe_receipt_persist_context_safe(&context));
	puts("observe_receipt_context: sie0_safe=1 interrupt_rejected=1 "
	     "exit_rejected=1 txn_rejected=1 epoch_rejected=1 "
	     "unadmitted_rejected=1");
}

int main(void)
{
	verify_allocator_deferred_renew();
	verify_allocator_reserve_and_proactive_renew();
	verify_lifecycle_deferred_renew();
	verify_pending_boot_lease();
	verify_receipt_persist_context();
	puts("identity_lease_deferred: passed");
	return 0;
}
