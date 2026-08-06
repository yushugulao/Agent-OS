#include "agent_metadata_journal.h"
#include "string.h"
#include "workflow_lifecycle.h"

static uint64
journal_hash_nonzero(uint64 hash)
{
	return hash ? hash : 1ULL;
}

static uint64
journal_bytes_hash(const void *payload, uint bytes)
{
	return journal_hash_nonzero(agent_disk_hash(
		AGENT_META_STORE_HASH_INITIAL, payload, bytes));
}

static uint64
journal_payload_hash(const void *payload)
{
	return journal_bytes_hash(payload, AGENT_META_JOURNAL_PAYLOAD_BYTES);
}

uint64
agent_meta_journal_slot_hash(const amd_journal_slot *slot)
{
	static const amd_u64 zero;
	uint64 hash;

	if (slot == 0)
		return 0;
	hash = agent_disk_hash(AGENT_META_STORE_HASH_INITIAL, slot,
		__builtin_offsetof(amd_journal_header, slot_hash));
	hash = agent_disk_hash(hash, &zero, sizeof(zero));
	hash = agent_disk_hash(hash, slot->payload, sizeof(slot->payload));
	return journal_hash_nonzero(hash);
}

uint64
agent_meta_journal_base_hash(uint64 generation, uint64 payload_hash)
{
	uint64 hash = AGENT_META_STORE_HASH_INITIAL;

	hash = agent_meta_disk_hash_mix(hash, AGENT_META_JOURNAL_MAGIC);
	hash = agent_meta_disk_hash_mix(hash, AGENT_META_JOURNAL_VERSION);
	hash = agent_meta_disk_hash_mix(hash, generation);
	hash = agent_meta_disk_hash_mix(hash, payload_hash);
	return journal_hash_nonzero(hash);
}

uint
agent_meta_journal_txn_blocks(uint count)
{
	if (count == 0 || count > AGENT_META_JOURNAL_MAX_DATA_RECORDS)
		return 0;
	return (count + 2U) / AGENT_META_JOURNAL_SLOTS_PER_BLOCK;
}

int
agent_meta_journal_cursor_init(struct agent_meta_journal_cursor *cursor,
			       uint64 generation, uint64 payload_hash)
{
	if (cursor == 0 || generation == 0 || payload_hash == 0)
		return AGENT_META_JOURNAL_CORRUPT;
	memset(cursor, 0, sizeof(*cursor));
	cursor->base_generation = generation;
	cursor->base_payload_hash = payload_hash;
	cursor->generation = generation;
	cursor->commit_hash = agent_meta_journal_base_hash(
		generation, payload_hash);
	return AGENT_META_JOURNAL_OK;
}

static int
journal_slot_zero(const amd_journal_slot *slot)
{
	const uchar *bytes = (const uchar *)slot;

	for (uint i = 0; i < sizeof(*slot); i++)
		if (bytes[i] != 0)
			return 0;
	return 1;
}

static int
journal_slot_checksum_valid(const amd_journal_slot *slot)
{
	return slot->header.magic == AGENT_META_JOURNAL_MAGIC &&
	       slot->header.version == AGENT_META_JOURNAL_VERSION &&
	       slot->header.slot_hash != 0 &&
	       slot->header.slot_hash == agent_meta_journal_slot_hash(slot);
}

static void
journal_header_init(amd_journal_header *header, uint kind,
		    const struct agent_meta_journal_cursor *cursor,
		    uint scope_id, struct workflow_lifecycle_key lifecycle,
		    uint index, uint count)
{
	memset(header, 0, sizeof(*header));
	header->magic = AGENT_META_JOURNAL_MAGIC;
	header->version = AGENT_META_JOURNAL_VERSION;
	header->kind = kind;
	header->base_generation = cursor->base_generation;
	header->generation = cursor->generation + 1;
	header->scope_id = scope_id;
	header->lifecycle_id = lifecycle.id;
	header->lifecycle_generation = lifecycle.generation;
	header->record_index = index;
	header->record_count = count;
	header->previous_commit_hash = cursor->commit_hash;
}

static uint64
journal_group_hash(const struct agent_meta_journal_plan *plan,
		   const struct agent_meta_journal_cursor *cursor)
{
	const amd_journal_header *first = &plan->slots[0].header;
	uint64 hash = cursor->commit_hash;

	hash = agent_meta_disk_hash_mix(hash, first->base_generation);
	hash = agent_meta_disk_hash_mix(hash, first->generation);
	hash = agent_meta_disk_hash_mix(hash, first->scope_id);
	hash = agent_meta_disk_hash_mix(hash, first->lifecycle_id);
	hash = agent_meta_disk_hash_mix(hash, first->lifecycle_generation);
	hash = agent_meta_disk_hash_mix(hash, first->record_count);
	for (uint i = 0; i < first->record_count; i++)
		hash = agent_meta_disk_hash_mix(
			hash, plan->slots[i].header.slot_hash);
	return journal_hash_nonzero(hash);
}

static int
journal_diff_fill_patch(struct agent_meta_journal_plan *plan, uint index,
			const struct agent_meta_journal_cursor *cursor,
			uint scope_id,
			struct workflow_lifecycle_key lifecycle,
			const struct agent_durable_arena *base,
			const struct agent_durable_arena *next, uint offset,
			uint bytes, uint count)
{
	amd_journal_slot *slot = &plan->slots[index];
	amd_journal_arena_patch *patch =
		(amd_journal_arena_patch *)(void *)slot->payload;
	uint expected;

	if (base == 0 || next == 0 ||
	    offset % AGENT_META_JOURNAL_PATCH_DATA_BYTES != 0 ||
	    offset >= sizeof(*base))
		return AGENT_META_JOURNAL_CORRUPT;
	expected = sizeof(*base) - offset;
	if (expected > AGENT_META_JOURNAL_PATCH_DATA_BYTES)
		expected = AGENT_META_JOURNAL_PATCH_DATA_BYTES;
	if (bytes != expected)
		return AGENT_META_JOURNAL_CORRUPT;
	journal_header_init(&slot->header, AGENT_META_JOURNAL_KIND_DATA,
		cursor, scope_id, lifecycle, index, count);
	slot->header.operation = AGENT_META_JOURNAL_OP_ARENA_PATCH;
	slot->header.payload_bytes = sizeof(*patch);
	patch->offset = offset;
	patch->bytes = bytes;
	patch->before_hash = journal_bytes_hash(
		(const uchar *)base + offset, bytes);
	memmove(patch->data, (const uchar *)next + offset, bytes);
	slot->header.payload_hash = journal_payload_hash(slot->payload);
	slot->header.slot_hash = agent_meta_journal_slot_hash(slot);
	return AGENT_META_JOURNAL_OK;
}

static int
journal_diff_fill_slot(struct agent_meta_journal_plan *plan, uint index,
		       const struct agent_meta_journal_cursor *cursor,
		       uint scope_id,
		       struct workflow_lifecycle_key lifecycle, uint operation,
		       const struct agent_meta_record *record, uint count)
{
	amd_journal_slot *slot = &plan->slots[index];

	if (record == 0 || record->scope_id != scope_id ||
	    !workflow_lifecycle_key_equal(record->lifecycle, lifecycle) ||
	    !agent_metadata_catalog_record_base_valid(
		    &record->meta, record->scope_id, record->slot))
		return AGENT_META_JOURNAL_CORRUPT;
	journal_header_init(&slot->header, AGENT_META_JOURNAL_KIND_DATA,
		cursor, scope_id, lifecycle, index, count);
	slot->header.operation = operation;
	slot->header.payload_bytes = sizeof(*record);
	memmove(slot->payload, record, sizeof(*record));
	slot->header.payload_hash = journal_payload_hash(slot->payload);
	slot->header.slot_hash = agent_meta_journal_slot_hash(slot);
	return AGENT_META_JOURNAL_OK;
}

int
agent_meta_journal_plan_delta(
	struct agent_meta_journal_plan *plan,
	const struct agent_meta_journal_cursor *cursor, uint scope_id,
	struct workflow_lifecycle_key lifecycle,
	const struct agent_meta_journal_change *changes, uint change_count,
	const struct agent_durable_arena *base_arena,
	const struct agent_durable_arena *next_arena)
{
	uint count = change_count, out = 0;
	uint blocks, slots;

	if (plan == 0 || cursor == 0 || base_arena == 0 ||
	    next_arena == 0 || change_count > AGENT_META_JOURNAL_MAX_DATA_RECORDS ||
	    (change_count != 0 && changes == 0) ||
	    !agent_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    agent_durable_arena_validate(base_arena) < 0 ||
	    agent_durable_arena_validate(next_arena) < 0)
		return AGENT_META_JOURNAL_CORRUPT;
	for (uint offset = 0; offset < sizeof(*base_arena);
	     offset += AGENT_META_JOURNAL_PATCH_DATA_BYTES) {
		uint bytes = sizeof(*base_arena) - offset;

		if (bytes > AGENT_META_JOURNAL_PATCH_DATA_BYTES)
			bytes = AGENT_META_JOURNAL_PATCH_DATA_BYTES;
		if (memcmp((const uchar *)base_arena + offset,
			   (const uchar *)next_arena + offset, bytes) != 0)
			count++;
	}
	blocks = agent_meta_journal_txn_blocks(count);
	if (count > AGENT_META_JOURNAL_MAX_DATA_RECORDS || blocks == 0 ||
	    cursor->slots_used % AGENT_META_JOURNAL_SLOTS_PER_BLOCK != 0 ||
	    cursor->slots_used > AGENT_META_JOURNAL_SLOTS)
		return AGENT_META_JOURNAL_NO_SPACE;
	slots = blocks * AGENT_META_JOURNAL_SLOTS_PER_BLOCK;
	if (slots > AGENT_META_JOURNAL_SLOTS - cursor->slots_used)
		return AGENT_META_JOURNAL_NO_SPACE;
	memset(plan, 0, sizeof(*plan));
	plan->start_slot = cursor->slots_used;
	plan->slot_count = slots;
	plan->block_count = blocks;
	plan->data_count = count;
	plan->generation = cursor->generation + 1;
	for (uint offset = 0; offset < sizeof(*base_arena);
	     offset += AGENT_META_JOURNAL_PATCH_DATA_BYTES) {
		uint bytes = sizeof(*base_arena) - offset;

		if (bytes > AGENT_META_JOURNAL_PATCH_DATA_BYTES)
			bytes = AGENT_META_JOURNAL_PATCH_DATA_BYTES;
		if (memcmp((const uchar *)base_arena + offset,
			   (const uchar *)next_arena + offset, bytes) != 0 &&
		    journal_diff_fill_patch(plan, out++, cursor, scope_id,
			    lifecycle, base_arena, next_arena, offset,
			    bytes, count) != AGENT_META_JOURNAL_OK)
			return AGENT_META_JOURNAL_CORRUPT;
	}
	for (uint i = 0; i < change_count; i++)
		if (journal_diff_fill_slot(
			    plan, out++, cursor, scope_id, lifecycle,
			    changes[i].operation, &changes[i].record,
			    count) != AGENT_META_JOURNAL_OK)
			return AGENT_META_JOURNAL_CORRUPT;
	if (out != count)
		return AGENT_META_JOURNAL_CORRUPT;
	amd_journal_slot *commit = &plan->slots[count];
	journal_header_init(&commit->header, AGENT_META_JOURNAL_KIND_COMMIT,
		cursor, scope_id, lifecycle, count, count);
	commit->header.group_hash = journal_group_hash(plan, cursor);
	commit->header.slot_hash = agent_meta_journal_slot_hash(commit);
	plan->commit_hash = commit->header.slot_hash;
	if (slots != count + 1U) {
		amd_journal_slot *pad = &plan->slots[count + 1U];

		journal_header_init(&pad->header,
			AGENT_META_JOURNAL_KIND_PAD, cursor, scope_id,
			lifecycle, count + 1U, count);
		pad->header.previous_commit_hash = plan->commit_hash;
		pad->header.group_hash = commit->header.group_hash;
		pad->header.slot_hash = agent_meta_journal_slot_hash(pad);
	}
	return agent_meta_journal_plan_validate(plan, cursor);
}

static int
journal_header_group_equal(const amd_journal_header *header,
			   const amd_journal_header *first)
{
	return header->base_generation == first->base_generation &&
	       header->generation == first->generation &&
	       header->scope_id == first->scope_id &&
	       header->lifecycle_id == first->lifecycle_id &&
	       header->lifecycle_generation == first->lifecycle_generation &&
	       header->record_count == first->record_count;
}

static int
journal_operation_is_record(uint operation)
{
	return operation == AGENT_META_JOURNAL_OP_UPSERT ||
	       operation == AGENT_META_JOURNAL_OP_DELETE;
}

static const amd_journal_arena_patch *
journal_plan_patch(const struct agent_meta_journal_plan *plan, uint index)
{
	return (const amd_journal_arena_patch *)(const void *)
		plan->slots[index].payload;
}

static int
journal_patch_valid(const amd_journal_arena_patch *patch)
{
	uint expected;

	if (patch == 0 || patch->before_hash == 0 ||
	    patch->offset % AGENT_META_JOURNAL_PATCH_DATA_BYTES != 0 ||
	    patch->offset >= sizeof(struct agent_durable_arena))
		return 0;
	expected = sizeof(struct agent_durable_arena) - patch->offset;
	if (expected > AGENT_META_JOURNAL_PATCH_DATA_BYTES)
		expected = AGENT_META_JOURNAL_PATCH_DATA_BYTES;
	if (patch->bytes != expected)
		return 0;
	for (uint i = patch->bytes; i < sizeof(patch->data); i++)
		if (patch->data[i] != 0)
			return 0;
	return 1;
}

int
agent_meta_journal_plan_validate(const struct agent_meta_journal_plan *plan,
				 const struct agent_meta_journal_cursor *cursor)
{
	const amd_journal_header *first;
	const amd_journal_slot *commit;
	struct workflow_lifecycle_key lifecycle;
	uint blocks;

	if (plan == 0 || cursor == 0 || plan->data_count == 0 ||
	    plan->data_count > AGENT_META_JOURNAL_MAX_DATA_RECORDS)
		return AGENT_META_JOURNAL_CORRUPT;
	blocks = agent_meta_journal_txn_blocks(plan->data_count);
	if (blocks == 0 || plan->block_count != blocks ||
	    plan->slot_count != blocks * AGENT_META_JOURNAL_SLOTS_PER_BLOCK ||
	    plan->start_slot != cursor->slots_used ||
	    plan->start_slot % AGENT_META_JOURNAL_SLOTS_PER_BLOCK != 0 ||
	    plan->slot_count > AGENT_META_JOURNAL_SLOTS - plan->start_slot)
		return AGENT_META_JOURNAL_CORRUPT;
	for (uint i = 0; i < plan->slot_count; i++)
		if (!journal_slot_checksum_valid(&plan->slots[i]))
			return AGENT_META_JOURNAL_INCOMPLETE;
	first = &plan->slots[0].header;
	lifecycle.id = first->lifecycle_id;
	lifecycle.generation = first->lifecycle_generation;
	if (first->base_generation != cursor->base_generation ||
	    first->generation != cursor->generation + 1 ||
	    first->previous_commit_hash != cursor->commit_hash ||
	    first->scope_id == VFS_SCOPE_SYSTEM ||
	    !agent_scope_valid(first->scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    first->record_count != plan->data_count ||
	    plan->generation != first->generation)
		return AGENT_META_JOURNAL_CORRUPT;
	for (uint i = 0; i < plan->data_count; i++) {
		const amd_journal_slot *slot = &plan->slots[i];
		const amd_journal_header *header = &slot->header;
		const struct agent_meta_record *record =
			(const struct agent_meta_record *)(const void *)slot->payload;

		if (header->kind != AGENT_META_JOURNAL_KIND_DATA ||
		    !journal_header_group_equal(header, first) ||
		    header->record_index != i ||
		    header->group_hash != 0 ||
		    header->previous_commit_hash != cursor->commit_hash ||
		    header->payload_hash != journal_payload_hash(slot->payload))
			return AGENT_META_JOURNAL_CORRUPT;
		if (journal_operation_is_record(header->operation)) {
			if (header->payload_bytes != sizeof(*record) ||
			    record->scope_id != first->scope_id ||
			    !workflow_lifecycle_key_equal(
				    record->lifecycle, lifecycle) ||
			    !agent_metadata_catalog_record_base_valid(
				    &record->meta, record->scope_id,
				    record->slot))
				return AGENT_META_JOURNAL_CORRUPT;
			for (uint prior = 0; prior < i; prior++) {
				const struct agent_meta_record *old;

				if (!journal_operation_is_record(
					    plan->slots[prior].header.operation))
					continue;
				old = (const struct agent_meta_record *)(const void *)
					plan->slots[prior].payload;
				if (old->slot == record->slot)
					return AGENT_META_JOURNAL_CORRUPT;
			}
		} else if (header->operation ==
			   AGENT_META_JOURNAL_OP_ARENA_PATCH) {
			const amd_journal_arena_patch *patch =
				journal_plan_patch(plan, i);

			if (header->payload_bytes != sizeof(*patch) ||
			    !journal_patch_valid(patch))
				return AGENT_META_JOURNAL_CORRUPT;
			for (uint prior = 0; prior < i; prior++)
				if (plan->slots[prior].header.operation ==
					    AGENT_META_JOURNAL_OP_ARENA_PATCH &&
				    journal_plan_patch(plan, prior)->offset ==
					    patch->offset)
					return AGENT_META_JOURNAL_CORRUPT;
		} else {
			return AGENT_META_JOURNAL_CORRUPT;
		}
	}
	commit = &plan->slots[plan->data_count];
	if (commit->header.kind != AGENT_META_JOURNAL_KIND_COMMIT ||
	    !journal_header_group_equal(&commit->header, first) ||
	    commit->header.record_index != plan->data_count ||
	    commit->header.operation != AGENT_META_JOURNAL_OP_NONE ||
	    commit->header.payload_bytes != 0 ||
	    commit->header.previous_commit_hash != cursor->commit_hash ||
	    commit->header.payload_hash != 0 ||
	    commit->header.group_hash != journal_group_hash(plan, cursor))
		return AGENT_META_JOURNAL_CORRUPT;
	for (uint i = 0; i < sizeof(commit->payload); i++)
		if (commit->payload[i] != 0)
			return AGENT_META_JOURNAL_CORRUPT;
	if (plan->slot_count != plan->data_count + 1U) {
		const amd_journal_slot *pad =
			&plan->slots[plan->data_count + 1U];

		if (pad->header.kind != AGENT_META_JOURNAL_KIND_PAD ||
		    !journal_header_group_equal(&pad->header, first) ||
		    pad->header.record_index != plan->data_count + 1U ||
		    pad->header.operation != AGENT_META_JOURNAL_OP_NONE ||
		    pad->header.payload_bytes != 0 ||
		    pad->header.previous_commit_hash != commit->header.slot_hash ||
		    pad->header.payload_hash != 0 ||
		    pad->header.group_hash != commit->header.group_hash)
			return AGENT_META_JOURNAL_CORRUPT;
		for (uint i = 0; i < sizeof(pad->payload); i++)
			if (pad->payload[i] != 0)
				return AGENT_META_JOURNAL_CORRUPT;
	}
	if (plan->commit_hash != commit->header.slot_hash)
		return AGENT_META_JOURNAL_CORRUPT;
	return AGENT_META_JOURNAL_OK;
}

int
agent_meta_journal_cursor_publish(struct agent_meta_journal_cursor *cursor,
				  const struct agent_meta_journal_plan *plan)
{
	int status = agent_meta_journal_plan_validate(plan, cursor);

	if (status != AGENT_META_JOURNAL_OK)
		return status;
	cursor->generation = plan->generation;
	cursor->commit_hash = plan->commit_hash;
	cursor->slots_used += plan->slot_count;
	return AGENT_META_JOURNAL_OK;
}

static int
journal_store_find_slot(const struct agent_meta_store *store, uint slot)
{
	for (uint64 i = 0; i < store->header.count; i++)
		if (store->records[i].slot == slot)
			return i;
	return -1;
}

static const struct agent_meta_record *
journal_plan_record(const struct agent_meta_journal_plan *plan, uint index)
{
	return (const struct agent_meta_record *)(const void *)
		plan->slots[index].payload;
}

static int
journal_records_conflict(const struct agent_meta_record *left,
			 const struct agent_meta_record *right)
{
	if (left->slot == right->slot)
		return 1;
	if (left->scope_id != right->scope_id)
		return 0;
	return left->meta.fid == right->meta.fid ||
	       strncmp(left->meta.physical_name, right->meta.physical_name,
		       sizeof(left->meta.physical_name)) == 0 ||
	       (left->meta.logical_path[0] &&
		strncmp(left->meta.logical_path, right->meta.logical_path,
			sizeof(left->meta.logical_path)) == 0) ||
	       (left->meta.dev != 0 && left->meta.dev == right->meta.dev &&
		left->meta.inum == right->meta.inum &&
		left->meta.incarnation == right->meta.incarnation);
}

static int
journal_change_for_slot(const struct agent_meta_journal_plan *plan, uint slot)
{
	for (uint i = 0; i < plan->data_count; i++)
		if (journal_operation_is_record(
			    plan->slots[i].header.operation) &&
		    journal_plan_record(plan, i)->slot == slot)
			return i;
	return -1;
}

static const struct agent_meta_record *
journal_final_record(const struct agent_meta_store *store,
		     const struct agent_meta_journal_plan *plan, uint index)
{
	uint cursor = 0;

	for (uint64 i = 0; i < store->header.count; i++) {
		int change = journal_change_for_slot(plan, store->records[i].slot);

		if (change >= 0 && plan->slots[change].header.operation ==
					 AGENT_META_JOURNAL_OP_DELETE)
			continue;
		if (cursor++ == index)
			return change >= 0 ? journal_plan_record(plan, change) :
				&store->records[i];
	}
	for (uint i = 0; i < plan->data_count; i++) {
		const struct agent_meta_record *record = journal_plan_record(plan, i);

		if (plan->slots[i].header.operation !=
				AGENT_META_JOURNAL_OP_UPSERT ||
		    journal_store_find_slot(store, record->slot) >= 0)
			continue;
		if (cursor++ == index)
			return record;
	}
	return 0;
}

static int
journal_store_plan_valid(const struct agent_meta_store *store,
			 const struct agent_meta_journal_plan *plan,
			 uint *final_count)
{
	uint count = store->header.count;
	uint ordinary = 0;

	if (store->header.count > AGENT_FILE_META_MAX)
		return 0;
	for (uint i = 0; i < plan->data_count; i++) {
		const amd_journal_header *header = &plan->slots[i].header;

		if (header->operation == AGENT_META_JOURNAL_OP_ARENA_PATCH) {
			const amd_journal_arena_patch *patch =
				journal_plan_patch(plan, i);

			if (journal_bytes_hash(
				    (const uchar *)&store->durable + patch->offset,
				    patch->bytes) != patch->before_hash)
				return 0;
			continue;
		}
		const struct agent_meta_record *record = journal_plan_record(plan, i);
		int found = journal_store_find_slot(store, record->slot);

		if (header->operation == AGENT_META_JOURNAL_OP_DELETE) {
			if (found < 0 || memcmp(&store->records[found], record,
					       sizeof(*record)) != 0)
				return 0;
			count--;
		} else if (found < 0) {
			if (count == AGENT_FILE_META_MAX)
				return 0;
			count++;
		} else if (store->records[found].scope_id != record->scope_id ||
			   !workflow_lifecycle_key_equal(
				   store->records[found].lifecycle,
				   record->lifecycle)) {
			return 0;
		}
	}
	for (uint i = 0; i < count; i++) {
		const struct agent_meta_record *record =
			journal_final_record(store, plan, i);
		uint owned = 0;
		uint limit;

		if (record == 0 ||
		    !agent_metadata_catalog_record_base_valid(
			    &record->meta, record->scope_id, record->slot) ||
		    (record->scope_id == VFS_SCOPE_SYSTEM ?
		     !workflow_lifecycle_key_equal(
			     record->lifecycle, workflow_lifecycle_none()) :
		     (!workflow_lifecycle_key_valid(record->lifecycle) ||
		      record->lifecycle.id > WORKFLOW_LIFECYCLE_CAP)))
			return 0;
		if (record->scope_id != VFS_SCOPE_SYSTEM &&
		    ++ordinary > AGENT_FILE_ORDINARY_LIMIT)
			return 0;
		limit = record->scope_id == VFS_SCOPE_SYSTEM ?
			AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;
		for (uint j = 0; j < i; j++) {
			const struct agent_meta_record *prior =
				journal_final_record(store, plan, j);

			if (prior == 0 || journal_records_conflict(prior, record))
				return 0;
			if (prior->scope_id == record->scope_id)
				owned++;
		}
		if (owned >= limit)
			return 0;
	}
	*final_count = count;
	return 1;
}

static int
journal_store_apply(struct agent_meta_store *store,
		    const struct agent_meta_journal_plan *plan)
{
	uint final_count;
	uint out = 0;

	if (!journal_store_plan_valid(store, plan, &final_count))
		return AGENT_META_JOURNAL_CORRUPT;
	for (uint64 i = 0; i < store->header.count; i++) {
		int change = journal_change_for_slot(plan, store->records[i].slot);

		if (change >= 0 && plan->slots[change].header.operation ==
					 AGENT_META_JOURNAL_OP_DELETE)
			continue;
		if (change >= 0)
			store->records[out++] = *journal_plan_record(plan, change);
		else if (out != i)
			store->records[out++] = store->records[i];
		else
			out++;
	}
	for (uint i = 0; i < plan->data_count; i++) {
		const struct agent_meta_record *record = journal_plan_record(plan, i);
		uint position = 0;

		if (!journal_operation_is_record(
			    plan->slots[i].header.operation) ||
		    plan->slots[i].header.operation !=
				AGENT_META_JOURNAL_OP_UPSERT)
			continue;
		while (position < out &&
		       store->records[position].slot < record->slot)
			position++;
		if (position < out && store->records[position].slot == record->slot)
			continue;
		memmove(&store->records[position + 1], &store->records[position],
			(out - position) * sizeof(store->records[0]));
		store->records[position] = *record;
		out++;
	}
	if (out != final_count)
		return AGENT_META_JOURNAL_CORRUPT;
	for (uint i = 0; i < plan->data_count; i++) {
		const amd_journal_arena_patch *patch;

		if (plan->slots[i].header.operation !=
		    AGENT_META_JOURNAL_OP_ARENA_PATCH)
			continue;
		patch = journal_plan_patch(plan, i);
		memmove((uchar *)&store->durable + patch->offset,
			patch->data, patch->bytes);
	}
	if (agent_durable_arena_validate(&store->durable) < 0)
		return AGENT_META_JOURNAL_CORRUPT;
	memset(&store->records[out], 0,
	       (AGENT_FILE_META_MAX - out) * sizeof(store->records[0]));
	store->header.count = out;
	store->header.generation = plan->generation;
	store->header.payload_hash = agent_meta_format_store_hash(store);
	return store->header.payload_hash ? AGENT_META_JOURNAL_OK :
		AGENT_META_JOURNAL_CORRUPT;
}

int
agent_meta_journal_apply_trusted(
	struct agent_meta_store *store,
	const struct agent_meta_journal_plan *plan, short *slot_index,
	uint index_count)
{
	uint count;

	if (store == 0 || plan == 0 || slot_index == 0 ||
	    index_count < AGENT_FILE_META_MAX ||
	    store->header.count > AGENT_FILE_META_MAX ||
	    store->header.generation + 1 != plan->generation)
		return AGENT_META_JOURNAL_CORRUPT;
	count = store->header.count;
	/* Validate all old-byte guards before changing the live authority. */
	for (uint i = 0; i < plan->data_count; i++) {
		uint operation = plan->slots[i].header.operation;

		if (operation == AGENT_META_JOURNAL_OP_ARENA_PATCH) {
			const amd_journal_arena_patch *patch =
				journal_plan_patch(plan, i);

			if (!journal_patch_valid(patch) ||
			    journal_bytes_hash(
				    (const uchar *)&store->durable + patch->offset,
				    patch->bytes) != patch->before_hash)
				return AGENT_META_JOURNAL_CORRUPT;
			continue;
		}
		if (!journal_operation_is_record(operation))
			return AGENT_META_JOURNAL_CORRUPT;
		const struct agent_meta_record *record =
			journal_plan_record(plan, i);
		int found = slot_index[record->slot];

		if (found < -1 || found >= (int)count ||
		    (found >= 0 && store->records[found].slot != record->slot))
			return AGENT_META_JOURNAL_CORRUPT;
		if (operation == AGENT_META_JOURNAL_OP_DELETE &&
		    (found < 0 || memcmp(&store->records[found], record,
				       sizeof(*record)) != 0))
			return AGENT_META_JOURNAL_CORRUPT;
		if (operation == AGENT_META_JOURNAL_OP_UPSERT && found < 0 &&
		    count == AGENT_FILE_META_MAX)
			return AGENT_META_JOURNAL_NO_SPACE;
	}
	for (uint i = 0; i < plan->data_count; i++) {
		uint operation = plan->slots[i].header.operation;

		if (operation == AGENT_META_JOURNAL_OP_ARENA_PATCH) {
			const amd_journal_arena_patch *patch =
				journal_plan_patch(plan, i);

			memmove((uchar *)&store->durable + patch->offset,
				patch->data, patch->bytes);
			continue;
		}
		const struct agent_meta_record *record =
			journal_plan_record(plan, i);
		int found = slot_index[record->slot];

		if (operation == AGENT_META_JOURNAL_OP_DELETE) {
			count--;
			memmove(&store->records[found],
				&store->records[found + 1],
				(count - found) * sizeof(store->records[0]));
			slot_index[record->slot] = -1;
			for (uint position = found; position < count; position++)
				slot_index[store->records[position].slot] =
					position;
			memset(&store->records[count], 0,
			       sizeof(store->records[0]));
		} else if (found >= 0) {
			store->records[found] = *record;
		} else {
			uint position = 0;

			while (position < count &&
			       store->records[position].slot < record->slot)
				position++;
			memmove(&store->records[position + 1],
				&store->records[position],
				(count - position) * sizeof(store->records[0]));
			store->records[position] = *record;
			count++;
			for (uint cursor = position; cursor < count; cursor++)
				slot_index[store->records[cursor].slot] = cursor;
		}
	}
	store->header.count = count;
	store->header.generation = plan->generation;
	/* Runtime journal shadows are authenticated by the commit chain.  A full
	 * payload hash is regenerated only when the bank is compacted. */
	store->header.payload_hash = plan->commit_hash;
	return AGENT_META_JOURNAL_OK;
}

int
agent_meta_journal_replay_init(struct agent_meta_journal_replay *replay,
			       uint64 generation, uint64 payload_hash)
{
	if (replay == 0)
		return AGENT_META_JOURNAL_CORRUPT;
	memset(replay, 0, sizeof(*replay));
	return agent_meta_journal_cursor_init(
		&replay->cursor, generation, payload_hash);
}

static int
journal_terminal_slots(struct agent_meta_journal_replay *replay,
		       const amd_journal_slot *slots, uint count)
{
	for (uint i = 0; i < count; i++) {
		const amd_journal_header *header = &slots[i].header;

		if (!journal_slot_checksum_valid(&slots[i]) ||
		    header->kind != AGENT_META_JOURNAL_KIND_COMMIT ||
		    header->base_generation != replay->cursor.base_generation)
			continue;
		if (replay->tail_state == AGENT_META_JOURNAL_TAIL_CLEAN ||
		    header->generation > replay->incomplete_generation)
			return AGENT_META_JOURNAL_CORRUPT;
	}
	return AGENT_META_JOURNAL_OK;
}

int
agent_meta_journal_replay_block(struct agent_meta_journal_replay *replay,
				struct agent_meta_store *store,
				const void *raw_block, uint block_index)
{
	const amd_journal_slot *slots = raw_block;

	if (replay == 0 || store == 0 || raw_block == 0 ||
	    block_index != replay->next_block ||
	    block_index >= AGENT_META_JOURNAL_BLOCKS)
		return AGENT_META_JOURNAL_CORRUPT;
	replay->next_block++;
	if (replay->tail_state != AGENT_META_JOURNAL_TAIL_ACTIVE)
		return journal_terminal_slots(
			replay, slots, AGENT_META_JOURNAL_SLOTS_PER_BLOCK);
	for (uint i = 0; i < AGENT_META_JOURNAL_SLOTS_PER_BLOCK; i++) {
		const amd_journal_slot *slot = &slots[i];

		if (replay->pending_slots == 0) {
			uint count;

			if (journal_slot_zero(slot)) {
				replay->tail_state =
					AGENT_META_JOURNAL_TAIL_CLEAN;
				if (i + 1U < AGENT_META_JOURNAL_SLOTS_PER_BLOCK &&
				    !journal_slot_zero(&slots[i + 1U]))
					replay->tail_state =
						AGENT_META_JOURNAL_TAIL_INCOMPLETE;
				return AGENT_META_JOURNAL_OK;
			}
			if (!journal_slot_checksum_valid(slot) ||
			    slot->header.kind != AGENT_META_JOURNAL_KIND_DATA) {
				replay->tail_state =
					AGENT_META_JOURNAL_TAIL_INCOMPLETE;
				replay->incomplete_generation =
					replay->cursor.generation + 1;
				return journal_terminal_slots(
					replay, &slots[i],
					AGENT_META_JOURNAL_SLOTS_PER_BLOCK - i);
			}
			count = slot->header.record_count;
			if (agent_meta_journal_txn_blocks(count) == 0) {
				replay->tail_state =
					AGENT_META_JOURNAL_TAIL_INCOMPLETE;
				replay->incomplete_generation =
					slot->header.generation;
				return AGENT_META_JOURNAL_OK;
			}
			memset(&replay->pending, 0, sizeof(replay->pending));
			replay->pending.start_slot =
				block_index * AGENT_META_JOURNAL_SLOTS_PER_BLOCK;
			replay->pending.data_count = count;
			replay->pending.block_count =
				agent_meta_journal_txn_blocks(count);
			replay->expected_slots = replay->pending.block_count *
				AGENT_META_JOURNAL_SLOTS_PER_BLOCK;
			replay->pending.slot_count = replay->expected_slots;
			replay->pending.generation = slot->header.generation;
		}
		replay->pending.slots[replay->pending_slots++] = *slot;
		if (replay->pending_slots == replay->expected_slots) {
			int status;

			replay->pending.commit_hash = replay->pending.slots[
				replay->pending.data_count].header.slot_hash;
			status = agent_meta_journal_plan_validate(
				&replay->pending, &replay->cursor);
			if (status == AGENT_META_JOURNAL_INCOMPLETE) {
				replay->tail_state =
					AGENT_META_JOURNAL_TAIL_INCOMPLETE;
				replay->incomplete_generation =
					replay->pending.generation;
				return AGENT_META_JOURNAL_OK;
			}
			if (status != AGENT_META_JOURNAL_OK ||
			    journal_store_apply(store, &replay->pending) !=
				    AGENT_META_JOURNAL_OK ||
			    agent_meta_journal_cursor_publish(
				    &replay->cursor, &replay->pending) !=
				    AGENT_META_JOURNAL_OK)
				return AGENT_META_JOURNAL_CORRUPT;
			replay->pending_slots = 0;
			replay->expected_slots = 0;
		}
	}
	return AGENT_META_JOURNAL_OK;
}

int
agent_meta_journal_replay_finish(struct agent_meta_journal_replay *replay)
{
	if (replay == 0 || replay->next_block > AGENT_META_JOURNAL_BLOCKS)
		return AGENT_META_JOURNAL_CORRUPT;
	if (replay->pending_slots != 0) {
		replay->tail_state = AGENT_META_JOURNAL_TAIL_INCOMPLETE;
		replay->incomplete_generation = replay->pending.generation;
	}
	return AGENT_META_JOURNAL_OK;
}
