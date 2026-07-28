#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef unsigned int uint;
typedef unsigned char uchar;
typedef uint64_t uint64;

#define AGENT_OBSERVE_CAPACITY_H
#define AGENT_DURABLE_SECTION_H
#define AGENT_INTERNAL_H
#define AGENT_OBSERVE_STORE_H
#define DEFS_H
#define __FS_H__
#define RISCV_H

#define WORKFLOW_LIFECYCLE_CAP 8U
#define VFS_SCOPE_FIRST_DYNAMIC 2U
#define VFS_SCOPE_SYSTEM 1U
#define FS_OWNER_SCOPE_FLAG 0x80000000U
#define AGENT_DURABLE_SECTION_OBSERVE 1U
#define AGENT_DURABLE_DIRTY_URGENT (1U << 0)

#define AGENT_OBSERVE_CHECKPOINT_MAGIC 0x41474f4253323031ULL
#define AGENT_OBSERVE_CHECKPOINT_VERSION 7U
#define AGENT_OBSERVE_CHECKPOINT_SCOPES 4U
#define AGENT_OBSERVE_CHECKPOINT_PER_SCOPE 8U
#define AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY 3U
#define AGENT_OBSERVE_RESERVED_SCOPE_SLOTS 1U
#define AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS 3U
#define AGENT_OBSERVE_RECOVERY_SCOPE_SLOT 3U
#define AGENT_OBSERVE_SCOPE_USED               (1U << 0)
#define AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR (1U << 1)
#define AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED    (1U << 2)
#define AGENT_OBSERVE_SCOPE_FLAGS_ALL 7U

struct workflow_lifecycle_key {
	uint id;
	uint64 generation;
};

enum agent_observe_capacity_class {
	AGENT_OBSERVE_CAPACITY_ORDINARY = 0,
	AGENT_OBSERVE_CAPACITY_RECOVERY,
};

struct agent_observe_capacity_claim {
	uint slot;
	uint replace;
	uint recovery;
	uint expected_scope_id;
	struct workflow_lifecycle_key expected_lifecycle;
};

struct agent_observe_reap_cookie {
	uint slot;
	uint scope_id;
	uint reserved;
	uint reserved2;
	uint64 token;
	uint64 source_generation;
	uint64 bank_generation;
	struct workflow_lifecycle_key lifecycle;
};

enum agent_observe_reap_action {
	AGENT_OBSERVE_REAP_NONE = 0,
	AGENT_OBSERVE_REAP_AUTHORIZE,
	AGENT_OBSERVE_REAP_ERASE,
};

void agent_observe_capacity_release(
	uint, struct workflow_lifecycle_key);

struct agent_observe_checkpoint_scope {
	uint used;
	uint scope_id;
	uint lifecycle_id;
	uint record_count;
	uint64 lifecycle_generation;
	uint64 total_records;
	uint64 admission_drops;
	uint64 ledger_hash;
	uchar records[8];
};

struct agent_observe_checkpoint {
	uint64 magic;
	uint version;
	uint bytes;
	uint64 generation;
	uint64 audit_lease_end;
	uint64 span_lease_end;
	uint64 event_lease_end;
	uint64 control_lease_end;
	uint agent_lease_end;
	uint retention_policy;
	uint scope_count;
	uint allocator_exhausted;
	uint reserved_scope_slots;
	uint reserved;
	uint64 lifecycle_lease_ends[WORKFLOW_LIFECYCLE_CAP];
	struct agent_observe_checkpoint_scope
		scopes[AGENT_OBSERVE_CHECKPOINT_SCOPES];
};

static struct agent_observe_checkpoint disk;
static uint64 active_generation;
static uint64 next_serial;
static uint64 next_target;
static int dirty_failures;
static int lifecycle_state[32];
static int background_requests;

static int intr_save(void) { return 1; }
static void intr_restore(int enabled) { assert(enabled == 1); }

static int workflow_lifecycle_key_valid(struct workflow_lifecycle_key key)
{
	return key.id != 0 && key.generation != 0;
}

static int workflow_lifecycle_key_equal(struct workflow_lifecycle_key a,
					struct workflow_lifecycle_key b)
{
	return a.id == b.id && a.generation == b.generation;
}

static int workflow_lifecycle_active(struct workflow_lifecycle_key key)
{
	return lifecycle_state[key.id % 32] == 1;
}

static int workflow_lifecycle_closing(struct workflow_lifecycle_key key)
{
	return lifecycle_state[key.id % 32] == 2;
}

static int workflow_lifecycle_retiring(struct workflow_lifecycle_key key)
{
	return lifecycle_state[key.id % 32] == 3;
}

static int agent_durable_section_active_read(
	uint kind, uint offset, void *dst, uint bytes, uint64 *generation)
{
	uint scopes = offsetof(struct agent_observe_checkpoint, scopes);

	assert(kind == AGENT_DURABLE_SECTION_OBSERVE);
	if (offset == 0) {
		assert(bytes <= scopes);
		memcpy(dst, &disk, bytes);
	} else {
		assert(offset >= scopes);
		uint relative = offset - scopes;
		uint slot = relative / sizeof(struct agent_observe_checkpoint_scope);

		assert(slot < AGENT_OBSERVE_CHECKPOINT_SCOPES &&
		       relative % sizeof(struct agent_observe_checkpoint_scope) == 0 &&
		       bytes <= offsetof(struct agent_observe_checkpoint_scope, records));
		memcpy(dst, &disk.scopes[slot], bytes);
	}
	*generation = active_generation;
	return 0;
}

static uint64 agent_durable_section_mark_dirty_evidence(
	uint kind, uint scope_id, uint64 *serial, uint flags)
{
	assert(kind == AGENT_DURABLE_SECTION_OBSERVE && scope_id != 0 &&
	       flags == AGENT_DURABLE_DIRTY_URGENT);
	if (dirty_failures > 0) {
		dirty_failures--;
		*serial = 0;
		return 0;
	}
	*serial = next_serial++;
	return next_target++;
}

static int agent_durable_section_replicated(uint scope_id, uint64 target)
{
	return scope_id != 0 && target != 0;
}

static uint64 agent_durable_section_active_generation(void)
{
	return active_generation;
}

static void agent_background_request(void) { background_requests++; }

#include "../../os/agent_observe_capacity.c"

static struct workflow_lifecycle_key key(uint id, uint64 generation)
{
	struct workflow_lifecycle_key result = {id, generation};

	return result;
}

static void reset_state(void)
{
	memset(&disk, 0, sizeof(disk));
	memset(lifecycle_state, 0, sizeof(lifecycle_state));
	disk.magic = AGENT_OBSERVE_CHECKPOINT_MAGIC;
	disk.version = AGENT_OBSERVE_CHECKPOINT_VERSION;
	disk.bytes = sizeof(disk);
	disk.generation = 41;
	disk.retention_policy =
		AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY;
	disk.reserved_scope_slots = AGENT_OBSERVE_RESERVED_SCOPE_SLOTS;
	active_generation = disk.generation;
	next_serial = 101;
	next_target = 1001;
	dirty_failures = 0;
	background_requests = 0;
	agent_observe_capacity_init();
}

static void set_disk_slot(uint slot, uint flags, uint scope_id,
			  struct workflow_lifecycle_key lifecycle)
{
	struct agent_observe_checkpoint_scope *scope = &disk.scopes[slot];

	memset(scope, 0, sizeof(*scope));
	scope->used = flags;
	scope->scope_id = scope_id;
	scope->lifecycle_id = lifecycle.id;
	scope->lifecycle_generation = lifecycle.generation;
	/* A pure admission-drop scope exercises the header-only capacity path. */
	if (flags != 0) {
		scope->total_records = 1;
		scope->admission_drops = 1;
	}
}

static void verify_four_slots_and_sticky_class(void)
{
	struct agent_observe_capacity_claim claim;
	struct workflow_lifecycle_key ordinary[4] = {
		key(1, 11), key(2, 12), key(3, 13), key(4, 14),
	};
	struct workflow_lifecycle_key recovery = key(5, 15);

	reset_state();
	for (uint i = 0; i < 3; i++) {
		assert(agent_observe_capacity_admit(
			       10 + i, ordinary[i],
			       AGENT_OBSERVE_CAPACITY_ORDINARY) == 1);
		assert(agent_observe_capacity_claim(
			       10 + i, ordinary[i], &claim) == 0 &&
		       claim.slot == i && !claim.recovery);
	}
	assert(agent_observe_capacity_admit(
		       13, ordinary[3], AGENT_OBSERVE_CAPACITY_ORDINARY) < 0);
	assert(agent_observe_capacity_admit(
		       20, recovery, AGENT_OBSERVE_CAPACITY_RECOVERY) == 1);
	assert(agent_observe_capacity_claim(20, recovery, &claim) == 0 &&
	       claim.slot == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT && claim.recovery);
	assert(agent_observe_capacity_admit(
		       20, recovery, AGENT_OBSERVE_CAPACITY_ORDINARY) == 0);
	assert(agent_observe_capacity_admit(
		       10, ordinary[0], AGENT_OBSERVE_CAPACITY_RECOVERY) < 0);
	agent_observe_slots[0].flags |= AGENT_OBSERVE_SLOT_RECOVERY;
	assert(agent_observe_capacity_admit(
		       10, ordinary[0], AGENT_OBSERVE_CAPACITY_ORDINARY) < 0);
	puts("observe_reap_state: four_slots=1 sticky_class=1");
}

static void verify_abort_and_scope_binding(void)
{
	struct agent_observe_capacity_claim claim;
	struct workflow_lifecycle_key workflow = key(6, 21);
	int reused;

	reset_state();
	assert(agent_observe_capacity_admit(
		       30, workflow, AGENT_OBSERVE_CAPACITY_ORDINARY) == 1);
	reused = agent_observe_capacity_admit(
		30, workflow, AGENT_OBSERVE_CAPACITY_ORDINARY);
	assert(reused == 0);
	if (reused > 0)
		agent_observe_capacity_abort(30, workflow);
	assert(agent_observe_capacity_claim(30, workflow, &claim) == 0);
	agent_observe_capacity_abort(31, workflow);
	assert(agent_observe_capacity_claim(30, workflow, &claim) == 0);
	agent_observe_capacity_abort(30, workflow);
	assert(agent_observe_capacity_claim(30, workflow, &claim) < 0);
	assert(agent_observe_capacity_admit(
		       30, workflow, AGENT_OBSERVE_CAPACITY_ORDINARY) == 1);
	puts("observe_reap_state: same_workflow_abort=1 cross_scope=1");
}

static void prepare_full_reap_disk(struct workflow_lifecycle_key victim)
{
	set_disk_slot(0, AGENT_OBSERVE_SCOPE_USED, 40, victim);
	set_disk_slot(1, AGENT_OBSERVE_SCOPE_USED, 41, key(8, 31));
	set_disk_slot(2, AGENT_OBSERVE_SCOPE_USED, 42, key(9, 32));
	set_disk_slot(3, AGENT_OBSERVE_SCOPE_USED |
			 AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR,
		      43, key(10, 33));
}

static uint64 finish_explicit_reap(struct workflow_lifecycle_key victim)
{
	uint64 token = 0;
	uint64 repeated = 0;
	uint64 serial_after;
	uint64 target_after;

	assert(agent_observe_capacity_reap_begin(40, victim, &token) == 0 &&
	       token != 0);
	serial_after = next_serial;
	target_after = next_target;
	assert(agent_observe_capacity_reap_begin(40, victim, &repeated) == 0 &&
	       repeated == token && next_serial == serial_after &&
	       next_target == target_after);
	agent_observe_capacity_replicated(VFS_SCOPE_SYSTEM);
	agent_observe_capacity_maintain();
	set_disk_slot(0, 0, 0, key(0, 0));
	agent_observe_capacity_replicated(VFS_SCOPE_SYSTEM);
	assert(agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	return token;
}

static void verify_zero_target_retry(void)
{
	struct workflow_lifecycle_key victim = key(15, 60);
	uint64 token = 99;
	uint64 resumed = 99;
	uint64 repeated = 0;
	uint64 resume_generation = 99;

	reset_state();
	prepare_full_reap_disk(victim);
	dirty_failures = 1;
	assert(agent_observe_capacity_reap_begin(40, victim, &token) < 0 &&
	       token == 0 && agent_observe_slots[0].detail.reap.token == 0 &&
	       agent_observe_slots[0].detail.reap.target == 0);
	assert(agent_observe_capacity_reap_resume(
		       victim, &resumed, &resume_generation) == 0 &&
	       resumed == 0 && resume_generation == 0);
	assert(agent_observe_capacity_reap_begin(40, victim, &token) == 0 &&
	       token != 0);
	assert(agent_observe_capacity_reap_resume(
		       victim, &resumed, &resume_generation) == 1 &&
	       resumed == token && resume_generation == active_generation);
	assert(agent_observe_capacity_reap_begin(40, victim, &repeated) == 0 &&
	       repeated == token);
	puts("observe_reap_state: zero_target_retry=1 same_token=1");
}

static void verify_attach_generation_stable(void)
{
	struct workflow_lifecycle_key victim = key(15, 60);
	uint64 token = 0;
	uint64 resumed = 0;
	uint64 resume_generation = 0;

	reset_state();
	prepare_full_reap_disk(victim);
	assert(agent_observe_capacity_reap_begin(40, victim, 0) == 0 &&
	       agent_observe_slots[0].detail.reap.source_generation == 41 &&
	       agent_observe_slots[0].detail.reap.token == 0);
	active_generation = 42;
	assert(agent_observe_capacity_reap_begin(40, victim, &token) == 0 &&
	       token != 0);
	assert(agent_observe_capacity_reap_resume(
		       victim, &resumed, &resume_generation) == 1 &&
	       resumed == token && resume_generation == 41);
	puts("observe_reap_state: attach_generation_stable=1");
}

static void verify_token_and_done_consumption(void)
{
	struct workflow_lifecycle_key victim = key(7, 30);
	struct workflow_lifecycle_key wrong = key(11, 44);
	struct agent_observe_reap_cookie cookie;
	struct agent_observe_reap_cookie retry_cookie;
	struct agent_observe_reap_cookie wrong_cookie;
	uint64 token;
	uint64 resumed_token = 0;
	uint64 changed_token;
	uint64 generation = 0;
	int replicated = 0;

	reset_state();
	prepare_full_reap_disk(victim);
	token = finish_explicit_reap(victim);
	assert(agent_observe_capacity_reap_resume(
		       victim, &resumed_token, &generation) == 1 &&
	       resumed_token == token && generation == active_generation);
	agent_observe_capacity_abort(40, victim);
	assert(agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	assert(agent_observe_capacity_admit(
		       50, key(12, 45), AGENT_OBSERVE_CAPACITY_ORDINARY) < 0);
	assert(agent_observe_capacity_reap_query(
		       wrong, token, &replicated, &generation, &cookie) < 0);
	assert(agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	assert(agent_observe_capacity_reap_query(
		       victim, token, &replicated, &generation, &cookie) == 0 &&
	       replicated == 1 && generation == active_generation);
	assert(agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	assert(agent_observe_capacity_reap_query(
		       victim, token, &replicated, &generation,
		       &retry_cookie) == 0 &&
	       retry_cookie.slot == cookie.slot &&
	       retry_cookie.scope_id == cookie.scope_id &&
	       retry_cookie.token == cookie.token &&
	       retry_cookie.source_generation == cookie.source_generation &&
	       retry_cookie.bank_generation == cookie.bank_generation);
	wrong_cookie = cookie;
	wrong_cookie.scope_id++;
	assert(agent_observe_capacity_reap_consume(&wrong_cookie) < 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	wrong_cookie = cookie;
	wrong_cookie.lifecycle.generation++;
	assert(agent_observe_capacity_reap_consume(&wrong_cookie) < 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	wrong_cookie = cookie;
	wrong_cookie.token++;
	assert(agent_observe_capacity_reap_consume(&wrong_cookie) < 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	wrong_cookie = cookie;
	wrong_cookie.slot = (wrong_cookie.slot + 1) %
		AGENT_OBSERVE_CHECKPOINT_SCOPES;
	assert(agent_observe_capacity_reap_consume(&wrong_cookie) < 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	wrong_cookie = cookie;
	wrong_cookie.bank_generation++;
	assert(agent_observe_capacity_reap_consume(&wrong_cookie) < 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	wrong_cookie = cookie;
	wrong_cookie.source_generation++;
	assert(agent_observe_capacity_reap_consume(&wrong_cookie) < 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	assert(agent_observe_capacity_reap_consume(&cookie) == 0);
	assert(agent_observe_capacity_reap_query(
		       victim, token, &replicated, &generation, &cookie) < 0);
	assert(agent_observe_capacity_admit(
		       50, key(12, 45), AGENT_OBSERVE_CAPACITY_ORDINARY) == 1);

	reset_state();
	prepare_full_reap_disk(victim);
	next_serial = 501;
	next_target = 9001;
	changed_token = finish_explicit_reap(victim);
	assert(changed_token != token);
	puts("observe_reap_state: serial_target_token=1 reap_retry_same_token=1 reap_delivery_reissue=1 done_race=1 cookie_fields=6 delivery_retry=1 consume_once=1");
}

static void verify_recover_idempotence(void)
{
	struct workflow_lifecycle_key victim = key(13, 55);
	struct workflow_lifecycle_key conflict = key(14, 56);
	uint64 token = 0;
	uint64 generation_token;

	reset_state();
	set_disk_slot(0, AGENT_OBSERVE_SCOPE_USED |
			 AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED,
		      60, victim);
	assert(agent_observe_capacity_recover_reap(0, 60, victim) == 0);
	assert(agent_observe_capacity_recover_reap(0, 60, victim) == 0);
	assert(agent_observe_capacity_recover_reap(0, 61, victim) < 0);
	assert(agent_observe_capacity_recover_reap(0, 60, conflict) < 0);
	assert(agent_observe_capacity_reap_begin(60, victim, &token) == 0 &&
	       token != 0);
	set_disk_slot(0, 0, 0, key(0, 0));
	agent_observe_capacity_replicated(VFS_SCOPE_SYSTEM);
	assert(agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_DONE);
	assert(agent_observe_capacity_recover_reap(0, 60, victim) == 0);
	assert(agent_observe_capacity_recover_reap(0, 60, conflict) < 0);

	reset_state();
	prepare_full_reap_disk(victim);
	assert(agent_observe_capacity_reap_begin(40, victim, &token) == 0 &&
	       agent_observe_slots[0].phase ==
		       AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING);
	disk.scopes[0].used |= AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED;
	background_requests = 0;
	assert(agent_observe_capacity_recover_reap(0, 40, victim) == 0 &&
	       agent_observe_slots[0].phase == AGENT_OBSERVE_SLOT_ERASE_PENDING &&
	       agent_observe_slots[0].slot_or_persist_scope == VFS_SCOPE_SYSTEM &&
	       agent_observe_slots[0].detail.reap.target == 0 &&
	       background_requests == 1);

	reset_state();
	set_disk_slot(0, AGENT_OBSERVE_SCOPE_USED |
			 AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED,
		      60, victim);
	next_serial = 701;
	next_target = 7001;
	assert(agent_observe_capacity_recover_reap(0, 60, victim) == 0);
	assert(agent_observe_capacity_reap_begin(60, victim, &token) == 0 &&
	       agent_observe_slots[0].detail.reap.source_generation == 41);
	generation_token = token;

	reset_state();
	set_disk_slot(0, AGENT_OBSERVE_SCOPE_USED |
			 AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED,
		      60, victim);
	active_generation = 42;
	next_serial = 701;
	next_target = 7001;
	assert(agent_observe_capacity_recover_reap(0, 60, victim) == 0);
	assert(agent_observe_capacity_reap_begin(60, victim, &token) == 0 &&
	       agent_observe_slots[0].detail.reap.source_generation == 42 &&
	       token != generation_token);
	puts("observe_reap_state: recover_pending_idempotent=1 recover_done_idempotent=1 conflict_closed=1 recover_authorized_promote=1 recovered_generation_token=1");
}

int main(void)
{
	verify_four_slots_and_sticky_class();
	verify_abort_and_scope_binding();
	verify_zero_target_retry();
	verify_attach_generation_stable();
	verify_token_and_done_consumption();
	verify_recover_idempotence();
	puts("observe_reap_state: passed");
	return 0;
}
