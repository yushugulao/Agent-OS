#include "agent_durable_section.h"
#include "defs.h"
#include "riscv.h"

struct agent_durable_dirty_state {
	int used;
	uint kind;
	uint scope_id;
	int notified;
	uint64 serial;
	uint64 persist_target;
	uint64 urgent_serial;
};

static const struct agent_durable_section_ops
	*agent_durable_providers[AGENT_DURABLE_SECTION_MAX];
static struct agent_durable_dirty_state
	agent_durable_dirty[AGENT_DURABLE_DIRTY_MAX];
static const struct agent_durable_store_ops *agent_durable_store;
static uint64 agent_durable_next_serial;
static const struct agent_durable_arena *agent_durable_active;
static uint64 agent_durable_active_generation;
static uint agent_durable_active_section_count;
static struct agent_durable_section_desc
	agent_durable_active_sections[AGENT_DURABLE_SECTION_MAX];

static uint64
agent_durable_notify_locked(struct agent_durable_dirty_state *state)
{
	uint64 target = agent_durable_store == 0 ? 0 :
		agent_durable_store->mark_dirty(state->scope_id);

	state->persist_target = target;
	state->notified = target != 0;
	if (target != 0 && state->urgent_serial != 0 &&
	    agent_durable_store->expedite != 0)
		agent_durable_store->expedite(state->scope_id);
	return target;
}

static int
agent_durable_retry_locked(void)
{
	int pending = 0;

	for (uint i = 0; i < AGENT_DURABLE_DIRTY_MAX; i++) {
		struct agent_durable_dirty_state *state = &agent_durable_dirty[i];

		if (state->used && !state->notified &&
		    agent_durable_notify_locked(state) == 0)
			pending = 1;
	}
	return pending;
}

static uint64
agent_durable_hash(const void *src, uint bytes)
{
	uint64 hash = agent_disk_hash(AGENT_META_STORE_HASH_INITIAL, src, bytes);

	return hash ? hash : 1;
}

static uint64
agent_durable_arena_hash(const struct agent_durable_arena *arena)
{
	return agent_durable_hash(arena,
		__builtin_offsetof(struct agent_durable_arena, image_hash));
}

static const struct agent_durable_section_ops *
agent_durable_provider(uint kind)
{
	for (uint i = 0; i < AGENT_DURABLE_SECTION_MAX; i++)
		if (agent_durable_providers[i] != 0 &&
		    agent_durable_providers[i]->kind == kind)
			return agent_durable_providers[i];
	return 0;
}

static struct agent_durable_section_desc *
agent_durable_desc(struct agent_durable_arena *arena, uint kind)
{
	for (uint i = 0; i < arena->section_count; i++)
		if (arena->sections[i].kind == kind)
			return &arena->sections[i];
	return 0;
}

static uint64
agent_durable_serial_alloc(void)
{
	uint64 serial = agent_durable_next_serial;

	if (serial == 0)
		return 0;
	agent_durable_next_serial = serial == ~0ULL ? 0 : serial + 1;
	return serial;
}

void
agent_durable_section_init(void)
{
	memset(agent_durable_providers, 0, sizeof(agent_durable_providers));
	memset(agent_durable_dirty, 0, sizeof(agent_durable_dirty));
	agent_durable_store = 0;
	agent_durable_next_serial = 1;
	agent_durable_active = 0;
	agent_durable_active_generation = 0;
	agent_durable_active_section_count = 0;
	memset(agent_durable_active_sections, 0,
	       sizeof(agent_durable_active_sections));
}

int
agent_durable_section_register(const struct agent_durable_section_ops *ops)
{
	if (ops == 0 || ops->kind == 0 || ops->version == 0 ||
	    ops->image_bytes == 0 ||
	    ops->image_bytes > AGENT_DURABLE_PAYLOAD_BYTES ||
	    ops->update_scope == 0 || ops->validate == 0 || ops->recover == 0)
		return -1;
	for (uint i = 0; i < AGENT_DURABLE_SECTION_MAX; i++) {
		if (agent_durable_providers[i] != 0 &&
		    agent_durable_providers[i]->kind == ops->kind)
			return -1;
		if (agent_durable_providers[i] == 0) {
			agent_durable_providers[i] = ops;
			return 0;
		}
	}
	return -1;
}

void
agent_durable_section_set_store_provider(
	const struct agent_durable_store_ops *store)
{
	int enabled = intr_save();

	agent_durable_store = store;
	(void)agent_durable_retry_locked();
	intr_restore(enabled);
}

int
agent_durable_section_retry_pending(void)
{
	int enabled = intr_save();
	int pending = agent_durable_retry_locked();

	intr_restore(enabled);
	return pending;
}

uint64
agent_durable_section_mark_dirty(uint kind, uint scope_id)
{
	return agent_durable_section_mark_dirty_evidence(kind, scope_id, 0, 0);
}

uint64
agent_durable_section_mark_dirty_evidence(uint kind, uint scope_id,
					  uint64 *serial, uint flags)
{
	struct agent_durable_dirty_state *free_state = 0;
	int enabled;

	if (serial != 0)
		*serial = 0;
	if ((flags & ~AGENT_DURABLE_DIRTY_URGENT) != 0 ||
	    agent_durable_provider(kind) == 0)
		return 0;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_DURABLE_DIRTY_MAX; i++) {
		struct agent_durable_dirty_state *state = &agent_durable_dirty[i];

		if (state->used && state->kind == kind &&
		    state->scope_id == scope_id) {
			uint64 next_serial = agent_durable_serial_alloc();

			/* 序列号耗尽时保留最后一个待提交代次。 */
			if (next_serial == 0) {
				intr_restore(enabled);
				return 0;
			}
			state->serial = next_serial;
			if (flags & AGENT_DURABLE_DIRTY_URGENT)
				state->urgent_serial = next_serial;
			/* 证据请求必须在序列号可见后分配回写目标，防止旧合并目标
			 * 为尚未进入快照的变更误报复制完成。 */
			if ((serial != 0 || !state->notified) &&
			    agent_durable_store != 0)
				(void)agent_durable_notify_locked(state);
			uint64 target = state->persist_target;
			if (serial != 0)
				*serial = state->serial;
			intr_restore(enabled);
			return target;
		}
		if (!state->used && free_state == 0)
			free_state = state;
	}
	if (free_state != 0) {
		uint64 next_serial = agent_durable_serial_alloc();

		if (next_serial == 0) {
			intr_restore(enabled);
			return 0;
		}
		free_state->used = 1;
		free_state->kind = kind;
		free_state->scope_id = scope_id;
		free_state->serial = next_serial;
		free_state->urgent_serial =
			(flags & AGENT_DURABLE_DIRTY_URGENT) ? next_serial : 0;
		(void)agent_durable_notify_locked(free_state);
		uint64 target = free_state->persist_target;
		if (serial != 0)
			*serial = free_state->serial;
		intr_restore(enabled);
		return target;
	}
	intr_restore(enabled);
	return 0;
}

int
agent_durable_section_replicated(uint scope_id, uint64 target)
{
	if (target == 0 || agent_durable_store == 0)
		return 0;
	return agent_durable_store->replicated(scope_id, target);
}

int
agent_durable_section_active_replicated(uint64 generation)
{
	if (generation == 0 || agent_durable_store == 0 ||
	    agent_durable_store->active_replicated == 0)
		return 0;
	return agent_durable_store->active_replicated(generation);
}

int
agent_durable_section_persist_scope(uint scope_id)
{
	if (agent_durable_store == 0)
		return -1;
	return agent_durable_store->persist_scope(scope_id);
}

void
agent_durable_section_mirror_scope(uint scope_id)
{
	for (uint i = 0; i < AGENT_DURABLE_SECTION_MAX; i++)
		if (agent_durable_providers[i] != 0 &&
		    agent_durable_providers[i]->replicated_scope != 0)
			agent_durable_providers[i]->replicated_scope(scope_id);
}

int
agent_durable_arena_init(struct agent_durable_arena *arena)
{
	if (arena == 0)
		return -1;
	agent_durable_disk_init_empty(arena);
	return 0;
}

int
agent_durable_arena_validate(const struct agent_durable_arena *arena)
{
	uint end = 0;

	if (arena == 0 || arena->magic != AGENT_DURABLE_ARENA_MAGIC ||
	    arena->version != AGENT_DURABLE_ARENA_VERSION ||
	    arena->bytes != sizeof(*arena) ||
	    arena->section_count > AGENT_DURABLE_SECTION_MAX ||
	    arena->used_bytes > sizeof(arena->payload) ||
	    arena->generation == 0 ||
	    arena->image_hash != agent_durable_arena_hash(arena))
		return -1;
	for (uint i = 0; i < arena->section_count; i++) {
		const struct agent_durable_section_desc *desc = &arena->sections[i];
		const struct agent_durable_section_ops *ops =
			agent_durable_provider(desc->kind);

		if (ops == 0 || desc->version != ops->version ||
		    desc->bytes != ops->image_bytes || desc->offset != end ||
		    desc->offset > arena->used_bytes ||
		    desc->bytes > arena->used_bytes - desc->offset ||
		    desc->payload_hash != agent_durable_hash(
			    &arena->payload[desc->offset], desc->bytes) ||
		    ops->validate(&arena->payload[desc->offset],
				  desc->bytes) < 0)
			return -1;
		end += desc->bytes;
		for (uint j = 0; j < i; j++)
			if (arena->sections[j].kind == desc->kind)
				return -1;
	}
	return end == arena->used_bytes ? 0 : -1;
}

int
agent_durable_arena_update_scope(struct agent_durable_arena *arena,
				 uint scope_id,
				 struct workflow_lifecycle_key lifecycle,
				 uint64 *captured_serial)
{
	uint64 captured = 0;
	int changed = 0;

	if (arena == 0 || captured_serial == 0)
		return -1;
	if (arena->magic == 0) {
		if (agent_durable_arena_init(arena) < 0)
			return -1;
	} else if (agent_durable_arena_validate(arena) < 0)
		return -1;
	/* 提供者复制前封存脏代次；与捕获竞争的新标记留待下一轮。 */
	{
		int enabled = intr_save();

		for (uint i = 0; i < AGENT_DURABLE_DIRTY_MAX; i++)
			if (agent_durable_dirty[i].used &&
			    agent_durable_dirty[i].scope_id == scope_id &&
			    agent_durable_dirty[i].serial > captured)
				captured = agent_durable_dirty[i].serial;
		intr_restore(enabled);
	}
	for (uint i = 0; i < AGENT_DURABLE_SECTION_MAX; i++) {
		const struct agent_durable_section_ops *ops =
			agent_durable_providers[i];
		struct agent_durable_section_desc *desc;
		uint64 generation = 0;
		int result;

		if (ops == 0)
			continue;
		desc = agent_durable_desc(arena, ops->kind);
		if (desc == 0) {
			if (arena->section_count >= AGENT_DURABLE_SECTION_MAX ||
			    ops->image_bytes > sizeof(arena->payload) -
						 arena->used_bytes)
				return -1;
			desc = &arena->sections[arena->section_count++];
			memset(desc, 0, sizeof(*desc));
			desc->kind = ops->kind;
			desc->version = ops->version;
			desc->offset = arena->used_bytes;
			desc->bytes = ops->image_bytes;
			memset(&arena->payload[desc->offset], 0, desc->bytes);
			arena->used_bytes += desc->bytes;
			changed = 1;
		}
		result = ops->update_scope(&arena->payload[desc->offset],
					   desc->bytes, scope_id, lifecycle,
					   &generation);
		if (result < 0)
			return -1;
		if (result > 0 || desc->generation != generation) {
			desc->generation = generation;
			desc->payload_hash = agent_durable_hash(
				&arena->payload[desc->offset], desc->bytes);
			changed = 1;
		}
	}
	if (changed) {
		arena->generation++;
		if (arena->generation == 0)
			return -1;
		arena->image_hash = agent_durable_arena_hash(arena);
	}
	*captured_serial = captured;
	return 0;
}

int
agent_durable_arena_recover(const struct agent_durable_arena *arena)
{
	if (agent_durable_arena_validate(arena) < 0)
		return -1;
	for (uint i = 0; i < arena->section_count; i++) {
		const struct agent_durable_section_desc *desc = &arena->sections[i];
		const struct agent_durable_section_ops *ops =
			agent_durable_provider(desc->kind);

		if (ops == 0 || ops->recover(&arena->payload[desc->offset],
					    desc->bytes) < 0)
			return -1;
	}
	return 0;
}

int
agent_durable_arena_has_scope(const struct agent_durable_arena *arena,
			      uint scope_id)
{
	if (agent_durable_arena_validate(arena) < 0)
		return 0;
	for (uint i = 0; i < arena->section_count; i++) {
		const struct agent_durable_section_desc *desc = &arena->sections[i];
		const struct agent_durable_section_ops *ops =
			agent_durable_provider(desc->kind);

		if (ops != 0 && ops->has_scope != 0 &&
		    ops->has_scope(&arena->payload[desc->offset], desc->bytes,
				   scope_id))
			return 1;
	}
	return 0;
}

int
agent_durable_section_scope_pending(uint scope_id)
{
	int pending = 0;
	int enabled = intr_save();

	for (uint i = 0; i < AGENT_DURABLE_DIRTY_MAX; i++)
		if (agent_durable_dirty[i].used &&
		    agent_durable_dirty[i].scope_id == scope_id) {
			pending = 1;
			break;
		}
	intr_restore(enabled);
	return pending;
}

void
agent_durable_section_commit_scope(uint scope_id, uint64 captured_serial)
{
	int enabled = intr_save();

	for (uint i = 0; i < AGENT_DURABLE_DIRTY_MAX; i++) {
		struct agent_durable_dirty_state *state = &agent_durable_dirty[i];

		if (!state->used || state->scope_id != scope_id ||
		    state->serial <= captured_serial) {
			if (state->used && state->scope_id == scope_id &&
			    state->serial <= captured_serial)
				memset(state, 0, sizeof(*state));
			continue;
		}
		/* 已进入提交快照的紧急变更不得顺带加速并发的无关标记。 */
		if (state->urgent_serial != 0 &&
		    state->urgent_serial <= captured_serial)
			state->urgent_serial = 0;
		(void)agent_durable_notify_locked(state);
	}
	intr_restore(enabled);
}

void
agent_durable_section_active_bind(const struct agent_durable_arena *arena,
				  uint64 generation)
{
	struct agent_durable_section_desc
		sections[AGENT_DURABLE_SECTION_MAX];
	uint section_count = 0;
	int enabled;

	if (arena != 0 && generation != 0 &&
	    agent_durable_arena_validate(arena) == 0) {
		section_count = arena->section_count;
		memmove(sections, arena->sections,
			section_count * sizeof(sections[0]));
	}
	enabled = intr_save();
	agent_durable_active = section_count == 0 ? 0 : arena;
	agent_durable_active_generation =
		section_count == 0 ? 0 : generation;
	agent_durable_active_section_count = section_count;
	memset(agent_durable_active_sections, 0,
	       sizeof(agent_durable_active_sections));
	if (section_count != 0)
		memmove(agent_durable_active_sections, sections,
			section_count * sizeof(sections[0]));
	intr_restore(enabled);
}

uint64
agent_durable_section_active_generation(void)
{
	int enabled = intr_save();
	uint64 generation = agent_durable_active_generation;

	intr_restore(enabled);
	return generation;
}

const uchar *
agent_durable_section_active_view(uint kind, uint *bytes, uint64 *generation)
{
	if (bytes == 0 || generation == 0 || intr_get() ||
	    agent_durable_active == 0)
		return 0;
	for (uint i = 0; i < agent_durable_active_section_count; i++)
		if (agent_durable_active_sections[i].kind == kind) {
			const struct agent_durable_section_desc *desc =
				&agent_durable_active_sections[i];

			*bytes = desc->bytes;
			*generation = agent_durable_active_generation;
			return &agent_durable_active->payload[desc->offset];
		}
	return 0;
}
