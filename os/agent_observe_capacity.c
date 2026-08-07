#include "agent_observe_capacity.h"
#include "agent_durable_section.h"
#include "agent_internal.h"
#include "agent_observe_store.h"
#include "defs.h"
#include "fs.h"
#include "riscv.h"

enum agent_observe_slot_phase {
	AGENT_OBSERVE_SLOT_FREE = 0,
	AGENT_OBSERVE_SLOT_ADMITTED,
	AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING,
	AGENT_OBSERVE_SLOT_ERASE_PENDING,
	AGENT_OBSERVE_SLOT_DONE,
};

#define AGENT_OBSERVE_SLOT_RECOVERY (1U << 0)
#define AGENT_OBSERVE_SLOT_REPLACE  (1U << 1)
#define AGENT_OBSERVE_SLOT_TOKEN_ISSUED (1U << 2)

struct agent_observe_slot_state {
	uint phase;
	uint flags;
	uint scope_id;
	uint slot_or_persist_scope;
	struct workflow_lifecycle_key lifecycle;
	union {
		struct {
			struct workflow_lifecycle_key expected_lifecycle;
			uint expected_scope_id;
			uint reserved;
			uint64 reserved2;
		} admission;
		struct {
			uint64 target;
			uint64 serial;
			uint64 source_generation;
			uint64 token;
		} reap;
	} detail;
};

struct agent_observe_capacity_slot {
	uint flags;
	uint sealed;
	uint scope_id;
	struct workflow_lifecycle_key lifecycle;
};

static struct agent_observe_slot_state
	agent_observe_slots[AGENT_OBSERVE_CHECKPOINT_SCOPES];

_Static_assert(sizeof(struct agent_observe_slot_state) == 64,
	       "observation slot state must not grow store accounting");
_Static_assert(AGENT_OBSERVE_RESERVED_SCOPE_SLOTS == 1 &&
	       AGENT_OBSERVE_RECOVERY_SCOPE_SLOT <
		       AGENT_OBSERVE_CHECKPOINT_SCOPES,
	       "observation store must reserve one Recovery successor slot");

static int
agent_observe_capacity_snapshot(
	struct agent_observe_capacity_slot
		slots[AGENT_OBSERVE_CHECKPOINT_SCOPES],
	uint64 *bank_generation)
{
	const struct agent_observe_checkpoint *image;
	uint bytes = 0;
	uint64 generation = 0;
	int result = -1;
	int enabled = intr_save();

	image = (const struct agent_observe_checkpoint *)
		agent_durable_section_active_view(
			AGENT_DURABLE_SECTION_OBSERVE, &bytes, &generation);
	if (image == 0 || bytes != sizeof(*image) ||
	    image->magic != AGENT_OBSERVE_CHECKPOINT_MAGIC ||
	    image->version != AGENT_OBSERVE_CHECKPOINT_VERSION ||
	    image->bytes != sizeof(*image) ||
	    image->retention_policy !=
		    AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY ||
	    image->reserved_scope_slots !=
		    AGENT_OBSERVE_RESERVED_SCOPE_SLOTS ||
	    image->reserved != 0)
		goto out;
	memset(slots, 0, sizeof(*slots) * AGENT_OBSERVE_CHECKPOINT_SCOPES);
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		const struct agent_observe_checkpoint_scope *scope =
			&image->scopes[i];

		if (scope->used == 0)
			continue;
		if ((scope->used & AGENT_OBSERVE_SCOPE_USED) == 0 ||
		    (scope->used & ~AGENT_OBSERVE_SCOPE_FLAGS_ALL) != 0 ||
		    (i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT) !=
			    !!(scope->used &
			       AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR) ||
		    scope->scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    scope->scope_id >= FS_OWNER_SCOPE_FLAG ||
		    scope->record_count > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE ||
		    scope->total_records == 0 ||
		    scope->total_records < scope->record_count ||
		    scope->admission_drops >
			    scope->total_records - scope->record_count ||
		    (scope->record_count == 0 &&
		     (scope->admission_drops != scope->total_records ||
		      scope->ledger_hash != 0)) ||
		    (scope->record_count != 0 &&
		     (scope->total_records - scope->admission_drops <
			      scope->record_count ||
		      scope->ledger_hash == 0)))
			goto out;
		slots[i].flags = scope->used;
		slots[i].scope_id = scope->scope_id;
		slots[i].lifecycle.id = scope->lifecycle_id;
		slots[i].lifecycle.generation = scope->lifecycle_generation;
		slots[i].sealed =
			!workflow_lifecycle_active(slots[i].lifecycle) &&
			!workflow_lifecycle_closing(slots[i].lifecycle) &&
			!workflow_lifecycle_retiring(slots[i].lifecycle);
	}
	if (bank_generation != 0)
		*bank_generation = generation;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

static int
agent_observe_slot_matches(const struct agent_observe_slot_state *state,
			   uint scope_id,
			   struct workflow_lifecycle_key lifecycle)
{
	return state->phase != AGENT_OBSERVE_SLOT_FREE &&
	       state->scope_id == scope_id &&
	       workflow_lifecycle_key_equal(state->lifecycle, lifecycle);
}

static uint64
agent_observe_reap_token(const struct agent_observe_slot_state *state)
{
	uint64 fields[] = {
		state->scope_id, state->lifecycle.id,
		state->lifecycle.generation,
		state->detail.reap.source_generation,
		state->detail.reap.serial, state->detail.reap.target,
	};
	uint64 token = 1469598103934665603ULL;

	for (uint i = 0; i < sizeof(fields) / sizeof(fields[0]); i++)
		for (uint byte = 0; byte < sizeof(fields[i]); byte++) {
			token ^= (fields[i] >> (byte * 8)) & 0xffU;
			token *= 1099511628211ULL;
		}
	return token == 0 ? 1 : token;
}

static int
agent_observe_reap_start(struct agent_observe_slot_state *state)
{
	uint64 serial = 0;
	uint64 target;

	target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, state->slot_or_persist_scope,
		&serial, AGENT_DURABLE_DIRTY_URGENT);
	if (target == 0 || serial == 0)
		return -1;
	state->detail.reap.target = target;
	state->detail.reap.serial = serial;
	if ((state->flags & AGENT_OBSERVE_SLOT_TOKEN_ISSUED) &&
	    state->detail.reap.token == 0)
		state->detail.reap.token = agent_observe_reap_token(state);
	return 0;
}

void
agent_observe_capacity_init(void)
{
	memset(agent_observe_slots, 0, sizeof(agent_observe_slots));
}

int
agent_observe_capacity_admit(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	enum agent_observe_capacity_class class)
{
	struct agent_observe_capacity_slot slots[AGENT_OBSERVE_CHECKPOINT_SCOPES];
	uint begin;
	uint end;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    (class != AGENT_OBSERVE_CAPACITY_ORDINARY &&
	     class != AGENT_OBSERVE_CAPACITY_RECOVERY))
		return -1;
	enabled = intr_save();
	if (agent_observe_capacity_snapshot(slots, 0) < 0)
		goto fail;
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];

		if (state->phase == AGENT_OBSERVE_SLOT_ADMITTED &&
		    agent_observe_slot_matches(state, scope_id, lifecycle)) {
			int recovery = class == AGENT_OBSERVE_CAPACITY_RECOVERY;
			int slot_recovery =
				!!(state->flags & AGENT_OBSERVE_SLOT_RECOVERY);

			if (slot_recovery !=
				    (i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT) ||
			    (recovery && !slot_recovery))
				goto fail;
			intr_restore(enabled);
			return 0;
		}
	}
	begin = class == AGENT_OBSERVE_CAPACITY_RECOVERY ?
		AGENT_OBSERVE_RECOVERY_SCOPE_SLOT : 0;
	end = class == AGENT_OBSERVE_CAPACITY_RECOVERY ?
		AGENT_OBSERVE_CHECKPOINT_SCOPES :
		AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS;
	for (uint i = begin; i < end; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];
		int replace = slots[i].flags != 0;

		if (state->phase != AGENT_OBSERVE_SLOT_FREE)
			continue;
		if (replace &&
		    !(class == AGENT_OBSERVE_CAPACITY_RECOVERY &&
		      slots[i].sealed &&
		      (slots[i].flags &
		       (AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR |
			AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED)) ==
		      AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR))
			continue;
		state->phase = AGENT_OBSERVE_SLOT_ADMITTED;
		state->flags =
			(class == AGENT_OBSERVE_CAPACITY_RECOVERY ?
			 AGENT_OBSERVE_SLOT_RECOVERY : 0) |
			(replace ? AGENT_OBSERVE_SLOT_REPLACE : 0);
		state->scope_id = scope_id;
		state->slot_or_persist_scope = i;
		state->lifecycle = lifecycle;
		state->detail.admission.expected_lifecycle = slots[i].lifecycle;
		state->detail.admission.expected_scope_id = slots[i].scope_id;
		intr_restore(enabled);
		return 1;
	}
fail:
	intr_restore(enabled);
	return -1;
}

void
agent_observe_capacity_abort(
	uint scope_id, struct workflow_lifecycle_key lifecycle)
{
	agent_observe_capacity_release(scope_id, lifecycle);
}

int
agent_observe_capacity_claim(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	struct agent_observe_capacity_claim *claim)
{
	int enabled;

	if (claim == 0)
		return -1;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];

		if (state->phase != AGENT_OBSERVE_SLOT_ADMITTED ||
		    !agent_observe_slot_matches(state, scope_id, lifecycle))
			continue;
		claim->slot = state->slot_or_persist_scope;
		claim->replace = !!(state->flags & AGENT_OBSERVE_SLOT_REPLACE);
		claim->recovery = !!(state->flags & AGENT_OBSERVE_SLOT_RECOVERY);
		claim->expected_scope_id =
			state->detail.admission.expected_scope_id;
		claim->expected_lifecycle =
			state->detail.admission.expected_lifecycle;
		intr_restore(enabled);
		return 0;
	}
	intr_restore(enabled);
	return -1;
}

void
agent_observe_capacity_release(
	uint scope_id, struct workflow_lifecycle_key lifecycle)
{
	int enabled = intr_save();

	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++)
		if (agent_observe_slots[i].phase ==
			    AGENT_OBSERVE_SLOT_ADMITTED &&
		    agent_observe_slot_matches(
			    &agent_observe_slots[i], scope_id, lifecycle)) {
			memset(&agent_observe_slots[i], 0,
			       sizeof(agent_observe_slots[i]));
			break;
		}
	intr_restore(enabled);
}

int
agent_observe_capacity_reap_begin(
	uint scope_id, struct workflow_lifecycle_key lifecycle, uint64 *token)
{
	struct agent_observe_capacity_slot slots[AGENT_OBSERVE_CHECKPOINT_SCOPES];
	struct agent_observe_slot_state *state = 0;
	uint64 generation = 0;
	int exact_slot = -1;
	int current_admission = 0;
	int enabled;

	if (token != 0)
		*token = 0;
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return -1;
	enabled = intr_save();
	if (agent_observe_capacity_snapshot(slots, &generation) < 0)
		goto fail;
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		if (slots[i].flags != 0 && slots[i].scope_id == scope_id &&
		    workflow_lifecycle_key_equal(slots[i].lifecycle, lifecycle))
			exact_slot = i;
		if (agent_observe_slot_matches(
			    &agent_observe_slots[i], scope_id, lifecycle))
			state = &agent_observe_slots[i];
	}
	if (state != 0 && state->phase >= AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING) {
		if (token != 0 && state->detail.reap.source_generation == 0)
			state->detail.reap.source_generation = generation;
		if (token != 0)
			state->flags |= AGENT_OBSERVE_SLOT_TOKEN_ISSUED;
		if (state->detail.reap.target == 0 &&
		    state->phase != AGENT_OBSERVE_SLOT_DONE)
			(void)agent_observe_reap_start(state);
		else if ((state->flags & AGENT_OBSERVE_SLOT_TOKEN_ISSUED) &&
			 state->detail.reap.token == 0)
			state->detail.reap.token = agent_observe_reap_token(state);
		if (token != 0)
			*token = state->detail.reap.token;
		if (state->phase == AGENT_OBSERVE_SLOT_DONE ||
		    state->detail.reap.target != 0) {
			intr_restore(enabled);
			return 0;
		}
		goto fail;
	}
	if (exact_slot < 0) {
		if (state == 0) {
			intr_restore(enabled);
			return 0;
		}
		if (state->phase != AGENT_OBSERVE_SLOT_ADMITTED ||
		    !workflow_lifecycle_retiring(lifecycle))
			goto fail;
		memset(&state->detail, 0, sizeof(state->detail));
		state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING;
		state->flags = token != 0 ?
			AGENT_OBSERVE_SLOT_TOKEN_ISSUED : 0;
		state->slot_or_persist_scope = scope_id;
		state->detail.reap.source_generation = generation;
		if (agent_observe_reap_start(state) < 0)
			goto fail;
		if (token != 0)
			*token = state->detail.reap.token;
		intr_restore(enabled);
		return 0;
	}
	if (state == 0) {
		if (!slots[exact_slot].sealed ||
		    (slots[exact_slot].flags &
		     AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED))
			goto fail;
		state = &agent_observe_slots[exact_slot];
		if (state->phase != AGENT_OBSERVE_SLOT_FREE)
			goto fail;
	} else if (state->phase != AGENT_OBSERVE_SLOT_ADMITTED ||
		   !workflow_lifecycle_retiring(lifecycle)) {
		goto fail;
	} else
		current_admission = 1;
	memset(&state->detail, 0, sizeof(state->detail));
	state->phase = AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING;
	state->flags = token != 0 ? AGENT_OBSERVE_SLOT_TOKEN_ISSUED : 0;
	state->scope_id = scope_id;
	state->slot_or_persist_scope = current_admission ?
		scope_id : VFS_SCOPE_SYSTEM;
	state->lifecycle = lifecycle;
	state->detail.reap.source_generation = generation;
	if (agent_observe_reap_start(state) < 0)
		goto fail;
	if (token != 0)
		*token = state->detail.reap.token;
	intr_restore(enabled);
	return 0;
fail:
	intr_restore(enabled);
	return -1;
}

int
agent_observe_capacity_reap_resume(
	struct workflow_lifecycle_key lifecycle, uint64 *token,
	uint64 *bank_generation)
{
	int found = 0;
	int matched = 0;
	int enabled;

	if (!workflow_lifecycle_key_valid(lifecycle) || token == 0 ||
	    bank_generation == 0)
		return -1;
	*token = 0;
	*bank_generation = 0;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];

		if (state->phase < AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING ||
		    state->phase > AGENT_OBSERVE_SLOT_DONE ||
		    !workflow_lifecycle_key_equal(state->lifecycle, lifecycle))
			continue;
		if (matched)
			goto fail;
		matched = 1;
		if (!(state->flags & AGENT_OBSERVE_SLOT_TOKEN_ISSUED) ||
		    state->detail.reap.token == 0)
			continue;
		*token = state->detail.reap.token;
		*bank_generation = state->detail.reap.source_generation;
		found = 1;
	}
	intr_restore(enabled);
	return found;
fail:
	*token = 0;
	*bank_generation = 0;
	intr_restore(enabled);
	return -1;
}

int
agent_observe_capacity_reap_action(
	uint slot, uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint persist_scope)
{
	struct agent_observe_slot_state *state;
	int action = AGENT_OBSERVE_REAP_NONE;
	int enabled;

	if (slot >= AGENT_OBSERVE_CHECKPOINT_SCOPES)
		return AGENT_OBSERVE_REAP_NONE;
	enabled = intr_save();
	state = &agent_observe_slots[slot];
	if (agent_observe_slot_matches(state, scope_id, lifecycle) &&
	    state->slot_or_persist_scope == persist_scope) {
		if (state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING)
			action = AGENT_OBSERVE_REAP_AUTHORIZE;
		else if (state->phase == AGENT_OBSERVE_SLOT_ERASE_PENDING)
			action = AGENT_OBSERVE_REAP_ERASE;
	}
	intr_restore(enabled);
	return action;
}

int
agent_observe_capacity_suppresses_capture(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint persist_scope)
{
	int suppress = 0;
	int enabled = intr_save();

	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];

		if ((state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING ||
		     state->phase == AGENT_OBSERVE_SLOT_ERASE_PENDING) &&
		    state->slot_or_persist_scope == persist_scope &&
		    agent_observe_slot_matches(state, scope_id, lifecycle)) {
			suppress = 1;
			break;
		}
	}
	intr_restore(enabled);
	return suppress;
}

static int
agent_observe_reap_replicated(struct agent_observe_slot_state *state)
{
	int replicated;

	if (state->detail.reap.target == 0)
		return 0;
	replicated = agent_durable_section_replicated(
		state->slot_or_persist_scope, state->detail.reap.target);
	if (replicated <= 0)
		return replicated;
	if (state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING) {
		state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING;
		state->slot_or_persist_scope = VFS_SCOPE_SYSTEM;
		state->detail.reap.target = 0;
		state->detail.reap.serial = 0;
		agent_background_request();
		return 0;
	}
	if (state->phase == AGENT_OBSERVE_SLOT_ERASE_PENDING) {
		state->phase = AGENT_OBSERVE_SLOT_DONE;
		if (state->detail.reap.token == 0)
			memset(state, 0, sizeof(*state));
		return 1;
	}
	return state->phase == AGENT_OBSERVE_SLOT_DONE;
}

void
agent_observe_capacity_replicated(uint scope_id)
{
	int enabled = intr_save();

	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++)
		if ((agent_observe_slots[i].phase ==
			     AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING ||
		     agent_observe_slots[i].phase ==
			     AGENT_OBSERVE_SLOT_ERASE_PENDING) &&
		    agent_observe_slots[i].slot_or_persist_scope == scope_id)
			(void)agent_observe_reap_replicated(
				&agent_observe_slots[i]);
	intr_restore(enabled);
}

void
agent_observe_capacity_maintain(void)
{
	int enabled = intr_save();

	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];

		if ((state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING ||
		     state->phase == AGENT_OBSERVE_SLOT_ERASE_PENDING) &&
		    state->detail.reap.target == 0) {
			if (agent_observe_reap_start(state) < 0)
				agent_background_request();
			break;
		}
	}
	intr_restore(enabled);
}

int
agent_observe_capacity_reap_query(
	struct workflow_lifecycle_key lifecycle, uint64 token, int *replicated,
	uint64 *bank_generation, struct agent_observe_reap_cookie *cookie)
{
	struct agent_observe_capacity_slot slots[AGENT_OBSERVE_CHECKPOINT_SCOPES];
	int enabled;

	if (!workflow_lifecycle_key_valid(lifecycle) || token == 0 ||
	    replicated == 0 || bank_generation == 0 || cookie == 0)
		return -1;
	memset(cookie, 0, sizeof(*cookie));
	enabled = intr_save();
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_slot_state *state = &agent_observe_slots[i];

		if (state->phase < AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING ||
		    !workflow_lifecycle_key_equal(state->lifecycle, lifecycle) ||
		    state->detail.reap.token != token ||
		    state->phase > AGENT_OBSERVE_SLOT_DONE)
			continue;
		*replicated = agent_observe_reap_replicated(state);
		if (state->phase == AGENT_OBSERVE_SLOT_DONE)
			*replicated = 1;
		if (agent_observe_capacity_snapshot(slots, bank_generation) < 0) {
			intr_restore(enabled);
			return -1;
		}
		if (state->phase == AGENT_OBSERVE_SLOT_DONE) {
			cookie->slot = i;
			cookie->scope_id = state->scope_id;
			cookie->token = token;
			cookie->source_generation =
				state->detail.reap.source_generation;
			cookie->bank_generation = *bank_generation;
			cookie->lifecycle = lifecycle;
		}
		intr_restore(enabled);
		return 0;
	}
	intr_restore(enabled);
	return -1;
}

int
agent_observe_capacity_reap_consume(
	const struct agent_observe_reap_cookie *cookie)
{
	struct agent_observe_slot_state *state;
	int result = -1;
	int enabled;

	if (cookie == 0 || cookie->slot >= AGENT_OBSERVE_CHECKPOINT_SCOPES ||
	    cookie->scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    cookie->scope_id >= FS_OWNER_SCOPE_FLAG || cookie->reserved != 0 ||
	    cookie->reserved2 != 0 || cookie->token == 0 ||
	    cookie->source_generation == 0 || cookie->bank_generation == 0 ||
	    !workflow_lifecycle_key_valid(cookie->lifecycle))
		return -1;
	enabled = intr_save();
	state = &agent_observe_slots[cookie->slot];
	if (state->phase == AGENT_OBSERVE_SLOT_DONE &&
	    agent_observe_slot_matches(
		    state, cookie->scope_id, cookie->lifecycle) &&
	    state->detail.reap.token == cookie->token &&
	    state->detail.reap.source_generation == cookie->source_generation &&
	    agent_durable_section_active_generation() ==
		    cookie->bank_generation) {
		memset(state, 0, sizeof(*state));
		result = 0;
	}
	intr_restore(enabled);
	return result;
}

int
agent_observe_capacity_recover_reap(
	uint slot, uint scope_id, struct workflow_lifecycle_key lifecycle)
{
	struct agent_observe_slot_state *state;
	int resume = 0;
	int enabled;
	if (slot >= AGENT_OBSERVE_CHECKPOINT_SCOPES ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return -1;
	enabled = intr_save();
	state = &agent_observe_slots[slot];
	if (state->phase != AGENT_OBSERVE_SLOT_FREE) {
		int same = agent_observe_slot_matches(
			state, scope_id, lifecycle) &&
			state->phase >= AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING &&
			state->phase <= AGENT_OBSERVE_SLOT_DONE;
		if (same && state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING) {
			state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING;
			state->slot_or_persist_scope = VFS_SCOPE_SYSTEM;
			state->detail.reap.target = state->detail.reap.serial = 0;
		}
		resume = same && state->phase != AGENT_OBSERVE_SLOT_DONE &&
			 state->detail.reap.target == 0;
		intr_restore(enabled);
		if (resume)
			agent_background_request();
		return same ? 0 : -1;
	}
	state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING;
	state->scope_id = scope_id;
	state->slot_or_persist_scope = VFS_SCOPE_SYSTEM;
	state->lifecycle = lifecycle;
	intr_restore(enabled);
	agent_background_request();
	return 0;
}
