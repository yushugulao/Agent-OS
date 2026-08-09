#include "workflow_lifecycle.h"
#include "agent_identity_lease.h"
#include "fs.h"
#include "riscv.h"

struct workflow_lifecycle_record {
	int used;
	uint scope_id;
	uint64 generation;
	uint64 controller_control_id;
	uint64 next_context_branch;
	uint members;
	enum workflow_lifecycle_state state;
};

static struct workflow_lifecycle_record
	workflow_lifecycles[WORKFLOW_LIFECYCLE_CAP];
static uint64 workflow_lifecycle_generations[WORKFLOW_LIFECYCLE_CAP];

int
workflow_lifecycle_prepare_create(void)
{
	int available = 0;
	int needs_lease = 0;
	int enabled = intr_save();

	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++) {
		uint64 generation;

		if (workflow_lifecycles[i].used ||
		    workflow_lifecycle_generations[i] == ~0ULL)
			continue;
		needs_lease = 1;
		generation = workflow_lifecycle_generations[i] + 1;
		if (generation != 0 &&
		    agent_identity_lease_lifecycle_contains(i, generation)) {
			available = 1;
			break;
		}
	}
	intr_restore(enabled);
	if (available)
		return 0;
	/* 容量耗尽并非租约不足；反复接纳失败也要保留租约维护能力以便回收。 */
	if (!needs_lease)
		return -1;
	return agent_identity_lease_lifecycle_renew();
}

static struct workflow_lifecycle_record *
workflow_lifecycle_find_locked(struct workflow_lifecycle_key key)
{
	uint slot;
	struct workflow_lifecycle_record *record;

	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return 0;
	slot = key.id - 1;
	record = &workflow_lifecycles[slot];
	if (!record->used || record->generation != key.generation)
		return 0;
	return record;
}

static struct workflow_lifecycle_key
workflow_lifecycle_record_key(uint slot,
			      const struct workflow_lifecycle_record *record)
{
	struct workflow_lifecycle_key key = {
		.id = slot + 1,
		.generation = record->generation,
	};

	return key;
}

int workflow_lifecycle_create(uint scope_id,
			      struct workflow_lifecycle_key *key)
{
	int enabled;
	int result = -1;
	uint allocated_slot = WORKFLOW_LIFECYCLE_CAP;
	uint64 next_generation = 0;

	if (key == 0 || scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
		return -1;
	*key = workflow_lifecycle_none();
	enabled = intr_save();
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++)
		if (workflow_lifecycles[i].used &&
		    workflow_lifecycles[i].scope_id == scope_id)
			goto out;
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++) {
		struct workflow_lifecycle_record *record =
			&workflow_lifecycles[i];
		uint64 generation;

		if (record->used ||
		    workflow_lifecycle_generations[i] == ~0ULL)
			continue;
		generation = workflow_lifecycle_generations[i] + 1;
		if (generation == 0 ||
		    !agent_identity_lease_lifecycle_contains(i, generation))
			continue;
		workflow_lifecycle_generations[i] = generation;
		record->used = 1;
		record->scope_id = scope_id;
		record->generation = generation;
		record->controller_control_id = 0;
		record->next_context_branch = 0;
		record->members = 1;
		record->state = WORKFLOW_LIFECYCLE_ACTIVE;
		*key = workflow_lifecycle_record_key(i, record);
		allocated_slot = i;
		next_generation = generation == ~0ULL ? 0 : generation + 1;
		result = 0;
		break;
	}
out:
	intr_restore(enabled);
	if (allocated_slot < WORKFLOW_LIFECYCLE_CAP)
		agent_identity_lease_lifecycle_note_next(
			allocated_slot, next_generation);
	return result;
}

int workflow_lifecycle_join(struct workflow_lifecycle_key key)
{
	int enabled;
	int result = -1;
	struct workflow_lifecycle_record *record;

	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 && record->state == WORKFLOW_LIFECYCLE_ACTIVE &&
	    record->members != (uint)-1) {
		record->members++;
		result = 0;
	}
	intr_restore(enabled);
	return result;
}

int workflow_lifecycle_leave(struct workflow_lifecycle_key key)
{
	int enabled;
	int result = -1;
	struct workflow_lifecycle_record *record;

	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 && record->members > 0) {
		record->members--;
		result = 0;
		if (record->members == 0) {
			record->state = WORKFLOW_LIFECYCLE_RETIRING;
			result = 1;
		}
	}
	intr_restore(enabled);
	return result;
}

int workflow_lifecycle_bind_controller(struct workflow_lifecycle_key key,
				       uint scope_id, uint64 control_id)
{
	int enabled;
	int result = -1;
	struct workflow_lifecycle_record *record;

	if (control_id == 0)
		return -1;
	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 && record->scope_id == scope_id &&
	    record->state == WORKFLOW_LIFECYCLE_ACTIVE &&
	    (record->controller_control_id == 0 ||
	     record->controller_control_id == control_id)) {
		record->controller_control_id = control_id;
		result = 0;
	}
	intr_restore(enabled);
	return result;
}

static int
workflow_lifecycle_close(uint scope_id,
			 struct workflow_lifecycle_key expected,
			 uint64 control_id, int trusted,
			 struct workflow_lifecycle_key *closed)
{
	int enabled;
	int result = -1;

	if (closed == 0 || scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return -1;
	*closed = workflow_lifecycle_none();
	enabled = intr_save();
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++) {
		struct workflow_lifecycle_record *record =
			&workflow_lifecycles[i];
		struct workflow_lifecycle_key key;

		if (!record->used || record->scope_id != scope_id)
			continue;
		key = workflow_lifecycle_record_key(i, record);
		if ((!trusted &&
		     (!workflow_lifecycle_key_equal(key, expected) ||
		      control_id == 0 ||
		      record->controller_control_id != control_id)) ||
		    record->controller_control_id == 0 ||
		    record->members == 0 ||
		    record->state == WORKFLOW_LIFECYCLE_RETIRING)
			break;
		if (record->state == WORKFLOW_LIFECYCLE_ACTIVE)
			record->state = WORKFLOW_LIFECYCLE_CLOSING;
		if (record->state == WORKFLOW_LIFECYCLE_CLOSING) {
			*closed = key;
			result = 0;
		}
		break;
	}
	intr_restore(enabled);
	return result;
}

int workflow_lifecycle_close_owned(uint scope_id,
				   struct workflow_lifecycle_key expected,
				   uint64 control_id,
				   struct workflow_lifecycle_key *closed)
{
	return workflow_lifecycle_close(scope_id, expected, control_id, 0,
					closed);
}

int workflow_lifecycle_close_trusted(uint scope_id,
				     struct workflow_lifecycle_key *closed)
{
	return workflow_lifecycle_close(scope_id, workflow_lifecycle_none(), 0,
					1, closed);
}

static int
workflow_lifecycle_has_state(struct workflow_lifecycle_key key,
			     enum workflow_lifecycle_state state)
{
	int enabled;
	int result = 0;
	struct workflow_lifecycle_record *record;

	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 && record->state == state && record->members > 0)
		result = 1;
	intr_restore(enabled);
	return result;
}

int workflow_lifecycle_active(struct workflow_lifecycle_key key)
{
	return workflow_lifecycle_has_state(key, WORKFLOW_LIFECYCLE_ACTIVE);
}

int workflow_lifecycle_closing(struct workflow_lifecycle_key key)
{
	return workflow_lifecycle_has_state(key, WORKFLOW_LIFECYCLE_CLOSING);
}

int workflow_lifecycle_retiring(struct workflow_lifecycle_key key)
{
	int enabled;
	int result = 0;
	struct workflow_lifecycle_record *record;

	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 &&
	    record->state == WORKFLOW_LIFECYCLE_RETIRING &&
	    record->members == 0)
		result = 1;
	intr_restore(enabled);
	return result;
}

int workflow_lifecycle_scope(struct workflow_lifecycle_key key,
			     uint *scope_id)
{
	int enabled;
	int result = -1;
	struct workflow_lifecycle_record *record;

	if (scope_id == 0)
		return -1;
	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0) {
		*scope_id = record->scope_id;
		result = 0;
	}
	intr_restore(enabled);
	return result;
}

int
workflow_lifecycle_generation_floor(struct workflow_lifecycle_key key)
{
	uint slot;
	int enabled;

	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return -1;
	slot = key.id - 1;
	enabled = intr_save();
	/* 历史检查点可以早于当前槽位复用代次。 */
	if (workflow_lifecycles[slot].used &&
	    key.generation > workflow_lifecycles[slot].generation) {
		intr_restore(enabled);
		return -1;
	}
	if (key.generation > workflow_lifecycle_generations[slot])
		workflow_lifecycle_generations[slot] = key.generation;
	intr_restore(enabled);
	return 0;
}

int
workflow_lifecycle_generation_lease_floor(uint slot, uint64 lease_end)
{
	uint64 floor;
	int enabled;

	if (slot >= WORKFLOW_LIFECYCLE_CAP)
		return -1;
	floor = lease_end == 0 ? ~0ULL : lease_end - 1;
	enabled = intr_save();
	if (floor > workflow_lifecycle_generations[slot])
		workflow_lifecycle_generations[slot] = floor;
	intr_restore(enabled);
	return 0;
}

#ifdef AGENT_OBSERVE_TEST_PROFILE
int
workflow_lifecycle_test_consume_generation(uint *slot_out,
					   uint64 *generation_out)
{
	uint allocated = WORKFLOW_LIFECYCLE_CAP;
	uint64 generation = 0;
	int enabled;

	if (slot_out == 0 || generation_out == 0)
		return -1;
	enabled = intr_save();
	for (uint slot = 0; slot < WORKFLOW_LIFECYCLE_CAP; slot++) {
		if (workflow_lifecycles[slot].used ||
		    workflow_lifecycle_generations[slot] == ~0ULL)
			continue;
		generation = workflow_lifecycle_generations[slot] + 1;
		if (generation == 0 ||
		    !agent_identity_lease_lifecycle_contains(slot, generation))
			continue;
		workflow_lifecycle_generations[slot] = generation;
		allocated = slot;
		break;
	}
	intr_restore(enabled);
	if (allocated == WORKFLOW_LIFECYCLE_CAP)
		return -1;
	agent_identity_lease_lifecycle_note_next(
		allocated, generation == ~0ULL ? 0 : generation + 1);
	*slot_out = allocated;
	*generation_out = generation;
	return 0;
}
#endif

int workflow_lifecycle_alloc_context_branch(struct workflow_lifecycle_key key,
					    uint64 *branch_generation)
{
	int enabled;
	int result = -1;
	struct workflow_lifecycle_record *record;

	if (branch_generation == 0)
		return -1;
	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 && record->state != WORKFLOW_LIFECYCLE_RETIRING &&
	    record->next_context_branch != ~0ULL) {
		*branch_generation = ++record->next_context_branch;
		result = *branch_generation != 0 ? 0 : -1;
	}
	intr_restore(enabled);
	return result;
}

int workflow_lifecycle_reclaim(struct workflow_lifecycle_key key)
{
	int enabled;
	int result = -1;
	struct workflow_lifecycle_record *record;

	enabled = intr_save();
	record = workflow_lifecycle_find_locked(key);
	if (record != 0 &&
	    record->state == WORKFLOW_LIFECYCLE_RETIRING &&
	    record->members == 0) {
		record->used = 0;
		record->scope_id = 0;
		record->controller_control_id = 0;
		record->next_context_branch = 0;
		record->members = 0;
		record->state = WORKFLOW_LIFECYCLE_FREE;
		result = 0;
	}
	intr_restore(enabled);
	return result;
}
