#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/*
 * The kernel is LP64, while native Windows compilers use LLP64.  Model the
 * kernel's fixed-width aliases explicitly before compiling the production
 * owner so this probe cannot silently reduce uint64 to 32 bits.
 */
#define TYPES_H
typedef unsigned int uint;
typedef unsigned short ushort;
typedef unsigned char uchar;
typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef uint64_t uint64;
_Static_assert(sizeof(uint64) == 8, "probe must model RISC-V uint64");

/* Compile the production owner with only its interrupt boundary stubbed. */
#define DEFS_H
#define RISCV_H
static int intr_save(void) { return 1; }
static void intr_restore(int enabled) { assert(enabled == 1); }

#include "../../os/agent_durable_section.c"

static uint64 next_target;
static uint sink_calls;
static uint sink_rejects;
static uint expedite_calls;
static uint expedited_scope;
static uint persist_calls;
static uint inject_update_mark;
static uint64 injected_serial;
static uint64 active_replicated_generation = 7;

static uint64 test_sink(uint scope_id)
{
	assert(scope_id != 0);
	sink_calls++;
	if (sink_rejects != 0) {
		sink_rejects--;
		return 0;
	}
	return next_target++;
}

static int test_replicated(uint scope_id, uint64 target)
{
	return scope_id != 0 && target != 0;
}

static int test_active_replicated(uint64 generation)
{
	return generation == active_replicated_generation;
}

static void test_expedite(uint scope_id)
{
	assert(scope_id != 0);
	expedite_calls++;
	expedited_scope = scope_id;
}

static int test_persist_scope(uint scope_id)
{
	if (scope_id == 0)
		return -1;
	persist_calls++;
	return 0;
}

static const struct agent_durable_store_ops test_store = {
	.mark_dirty = test_sink,
	.expedite = test_expedite,
	.replicated = test_replicated,
	.active_replicated = test_active_replicated,
	.persist_scope = test_persist_scope,
};

static int test_update(void *dst, uint bytes, uint scope_id,
		       struct workflow_lifecycle_key lifecycle, uint64 *generation)
{
	(void)dst;
	(void)bytes;
	(void)scope_id;
	(void)lifecycle;
	if (inject_update_mark) {
		inject_update_mark = 0;
		assert(agent_durable_section_mark_dirty_evidence(
			       AGENT_DURABLE_SECTION_OBSERVE, scope_id,
			       &injected_serial, 0) != 0);
	}
	*generation = 1;
	return 0;
}

static int test_validate(const void *src, uint bytes)
{
	(void)src;
	(void)bytes;
	return 0;
}

static int test_recover(const void *src, uint bytes)
{
	return test_validate(src, bytes);
}

static const struct agent_durable_section_ops test_provider = {
	.kind = AGENT_DURABLE_SECTION_OBSERVE,
	.version = 1,
	.image_bytes = 8,
	.update_scope = test_update,
	.validate = test_validate,
	.recover = test_recover,
};

static void reset_contract(void)
{
	agent_durable_section_init();
	assert(agent_durable_section_register(&test_provider) == 0);
	next_target = 1;
	sink_calls = 0;
	sink_rejects = 0;
	expedite_calls = 0;
	expedited_scope = 0;
	persist_calls = 0;
	inject_update_mark = 0;
	injected_serial = 0;
}

static struct agent_durable_dirty_state *
dirty_state(uint kind, uint scope_id)
{
	for (uint i = 0; i < AGENT_DURABLE_DIRTY_MAX; i++)
		if (agent_durable_dirty[i].used &&
		    agent_durable_dirty[i].kind == kind &&
		    agent_durable_dirty[i].scope_id == scope_id)
			return &agent_durable_dirty[i];
	return 0;
}

static void verify_evidence_expedite(void)
{
	struct agent_durable_dirty_state *state;
	uint64 unchanged;
	uint64 serial = 0;
	uint64 target;
	uint64 committed;
	uint calls;

	/* Ordinary coalesced dirtiness must retain the background cadence. */
	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	target = agent_durable_section_mark_dirty(
		AGENT_DURABLE_SECTION_OBSERVE, 11);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 11);
	assert(target != 0 && state != 0 && state->notified &&
	       state->urgent_serial == 0);
	assert(expedite_calls == 0);
	committed = state->serial;
	agent_durable_section_commit_scope(11, committed);
	assert(!agent_durable_section_scope_pending(11));

	/* A serial alone is a receipt fence, not an urgency request. */
	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 12, &serial, 0);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 12);
	assert(target != 0 && serial != 0 && state != 0 && state->notified &&
	       state->urgent_serial == 0 && expedite_calls == 0);
	committed = state->serial;
	agent_durable_section_commit_scope(12, committed);
	assert(!state->used && !agent_durable_section_scope_pending(12));

	/* Only the explicit flag remains urgent through coalescing and commit. */
	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 12, &serial,
		AGENT_DURABLE_DIRTY_URGENT);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 12);
	assert(target != 0 && serial != 0 && state != 0 && state->notified &&
	       state->urgent_serial == serial);
	assert(expedite_calls == 1 && expedited_scope == 12);
	calls = expedite_calls;
	assert(agent_durable_section_mark_dirty(
		       AGENT_DURABLE_SECTION_OBSERVE, 12) == target);
	assert(state->urgent_serial == serial && expedite_calls == calls);
	committed = state->serial;
	agent_durable_section_commit_scope(12, committed);
	assert(!state->used && state->urgent_serial == 0 &&
	       !agent_durable_section_scope_pending(12));

	/* Provider installation retries an urgent request without downgrading it. */
	reset_contract();
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 13, &serial,
		AGENT_DURABLE_DIRTY_URGENT);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 13);
	assert(target == 0 && serial != 0 && state != 0 &&
	       state->urgent_serial == serial &&
	       !state->notified && expedite_calls == 0);
	agent_durable_section_set_store_provider(&test_store);
	assert(state->urgent_serial == serial && state->notified &&
	       state->persist_target != 0);
	assert(expedite_calls == 1 && expedited_scope == 13);
	committed = state->serial;
	agent_durable_section_commit_scope(13, committed);
	assert(!state->used && state->urgent_serial == 0);

	/* A rejected mark stays urgent and expedites after the retry succeeds. */
	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	sink_rejects = 1;
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 14, &serial,
		AGENT_DURABLE_DIRTY_URGENT);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 14);
	assert(target == 0 && serial != 0 && state != 0 &&
	       state->urgent_serial == serial &&
	       !state->notified && expedite_calls == 0);
	assert(agent_durable_section_retry_pending() == 0);
	assert(state->urgent_serial == serial && state->notified &&
	       state->persist_target != 0);
	assert(expedite_calls == 1 && expedited_scope == 14);
	committed = state->serial;
	agent_durable_section_commit_scope(14, committed);
	assert(!state->used && state->urgent_serial == 0);

	/* Unknown policy bits fail before they can mutate an existing entry. */
	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 15, &serial, 0);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 15);
	assert(target != 0 && state != 0 && state->urgent_serial == 0);
	unchanged = state->serial;
	calls = sink_calls;
	serial = 1;
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 15, &serial,
		       AGENT_DURABLE_DIRTY_URGENT << 1) == 0);
	assert(serial == 0 && state->serial == unchanged &&
	       state->urgent_serial == 0 &&
	       sink_calls == calls && expedite_calls == 0);
	agent_durable_section_commit_scope(15, unchanged);
	assert(!state->used);

	/* Committing an urgent snapshot must not accelerate a later ordinary
	 * generation that raced with capture. */
	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 16, &serial,
		AGENT_DURABLE_DIRTY_URGENT);
	state = dirty_state(AGENT_DURABLE_SECTION_OBSERVE, 16);
	assert(target != 0 && state != 0 && state->urgent_serial == serial);
	committed = serial;
	assert(agent_durable_section_mark_dirty(
		       AGENT_DURABLE_SECTION_OBSERVE, 16) == target);
	assert(state->serial > committed && state->urgent_serial == committed);
	calls = expedite_calls;
	agent_durable_section_commit_scope(16, committed);
	assert(state->used && state->urgent_serial == 0 &&
	       expedite_calls == calls);
	agent_durable_section_commit_scope(16, state->serial);
	assert(!state->used);
	puts("durable_dirty_retry: evidence_expedite=1");
}

static void verify_store_provider_contract(void)
{
	reset_contract();
	assert(agent_durable_section_persist_scope(1) < 0);
	assert(agent_durable_section_active_replicated(7) == 0);
	agent_durable_section_set_store_provider(&test_store);
	assert(agent_durable_section_persist_scope(1) == 0);
	assert(agent_durable_section_active_replicated(7) == 1);
	assert(agent_durable_section_active_replicated(8) == 0);
	assert(persist_calls == 1);
	puts("durable_dirty_retry: store_provider=1");
}

static void verify_system_sink_zero(void)
{
	uint64 serial = 0;

	reset_contract();
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 1, &serial, 0) == 0);
	assert(serial != 0 && agent_durable_section_scope_pending(1));
	agent_durable_section_set_store_provider(&test_store);
	assert(sink_calls == 1 && agent_durable_section_retry_pending() == 0);
	agent_durable_section_commit_scope(1, serial);
	assert(!agent_durable_section_scope_pending(1));
	puts("durable_dirty_retry: system_sink_zero=1");
}

static void verify_commit_retry(void)
{
	uint64 captured = 0;
	uint64 late = 0;

	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 2, &captured, 0) != 0);
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 2, &late, 0) != 0);
	assert(late > captured);
	sink_rejects = 1;
	agent_durable_section_commit_scope(2, captured);
	assert(agent_durable_section_scope_pending(2));
	assert(agent_durable_section_retry_pending() == 0);
	agent_durable_section_commit_scope(2, late);
	assert(!agent_durable_section_scope_pending(2));
	puts("durable_dirty_retry: commit_retry=1");
}

static void verify_system_reserve_slot(void)
{
	uint64 serial;

	reset_contract();
	sink_rejects = AGENT_DURABLE_DIRTY_MAX + 1;
	agent_durable_section_set_store_provider(&test_store);
	for (uint scope = 1; scope <= AGENT_DURABLE_DIRTY_MAX; scope++) {
		serial = 0;
		assert(agent_durable_section_mark_dirty_evidence(
			       AGENT_DURABLE_SECTION_OBSERVE, scope, &serial, 0) == 0);
		assert(serial != 0 && agent_durable_section_scope_pending(scope));
	}
	serial = 1;
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE,
		       AGENT_DURABLE_DIRTY_MAX + 1, &serial, 0) == 0);
	assert(serial == 0);
	puts("durable_dirty_retry: system_reserve_slot=1");
}

static void verify_serial_exhaustion_fail_closed(void)
{
	uint64 serial = 0;
	uint64 exhausted = 1;
	uint64 target;
	uint calls;

	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 7, &serial, 0) != 0);
	agent_durable_next_serial = ~0ULL;
	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, 7, &serial, 0);
	assert(target != 0 && serial == ~0ULL &&
	       agent_durable_next_serial == 0);
	calls = sink_calls;
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 7, &exhausted, 0) == 0);
	assert(exhausted == 0 && sink_calls == calls);
	assert(agent_durable_dirty[0].used &&
	       agent_durable_dirty[0].serial == ~0ULL &&
	       agent_durable_dirty[0].persist_target == target &&
	       agent_durable_dirty[0].notified);
	agent_durable_section_commit_scope(7, ~0ULL);
	assert(!agent_durable_section_scope_pending(7));
	puts("durable_dirty_retry: serial_exhaustion_fail_closed=1");
}

static void verify_capture_serial_fence(void)
{
	struct agent_durable_arena arena;
	struct workflow_lifecycle_key lifecycle = { .id = 1, .generation = 1 };
	uint64 before = 0;
	uint64 captured = 0;

	reset_contract();
	agent_durable_section_set_store_provider(&test_store);
	assert(agent_durable_section_mark_dirty_evidence(
		       AGENT_DURABLE_SECTION_OBSERVE, 9, &before, 0) != 0);
	memset(&arena, 0, sizeof(arena));
	inject_update_mark = 1;
	assert(agent_durable_arena_update_scope(
		       &arena, 9, lifecycle, &captured) == 0);
	assert(captured == before && injected_serial > captured);
	agent_durable_section_commit_scope(9, captured);
	assert(agent_durable_section_scope_pending(9));
	agent_durable_section_commit_scope(9, injected_serial);
	assert(!agent_durable_section_scope_pending(9));
	puts("durable_dirty_retry: capture_serial_fence=1");
}

int main(void)
{
	verify_store_provider_contract();
	verify_evidence_expedite();
	verify_system_sink_zero();
	verify_commit_retry();
	verify_system_reserve_slot();
	verify_serial_exhaustion_fail_closed();
	verify_capture_serial_fence();
	puts("durable_dirty_retry: passed");
	return 0;
}
