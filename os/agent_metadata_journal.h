#ifndef AGENT_METADATA_JOURNAL_H
#define AGENT_METADATA_JOURNAL_H

#include "agent_metadata_store_format.h"

enum agent_meta_journal_status {
	AGENT_META_JOURNAL_OK = 0,
	AGENT_META_JOURNAL_INCOMPLETE = 1,
	AGENT_META_JOURNAL_CORRUPT = -1,
	AGENT_META_JOURNAL_NO_SPACE = -2,
};

struct agent_meta_journal_cursor {
	uint64 base_generation;
	uint64 base_payload_hash;
	uint64 generation;
	uint64 commit_hash;
	uint slots_used;
};

struct agent_meta_journal_change {
	uint operation;
	struct agent_meta_record record;
};

struct agent_meta_journal_plan {
	uint start_slot;
	uint slot_count;
	uint block_count;
	uint data_count;
	uint64 generation;
	uint64 commit_hash;
	amd_journal_slot slots[
		AGENT_META_JOURNAL_MAX_TXN_BLOCKS *
		AGENT_META_JOURNAL_SLOTS_PER_BLOCK];
};

enum agent_meta_journal_tail_state {
	AGENT_META_JOURNAL_TAIL_ACTIVE = 0,
	AGENT_META_JOURNAL_TAIL_CLEAN,
	AGENT_META_JOURNAL_TAIL_INCOMPLETE,
};

struct agent_meta_journal_replay {
	struct agent_meta_journal_cursor cursor;
	struct agent_meta_journal_plan pending;
	uint next_block;
	uint pending_slots;
	uint expected_slots;
	uint tail_state;
	uint64 incomplete_generation;
};

uint agent_meta_journal_txn_blocks(uint);
uint64 agent_meta_journal_slot_hash(const amd_journal_slot *);
uint64 agent_meta_journal_base_hash(uint64, uint64);
int agent_meta_journal_cursor_init(struct agent_meta_journal_cursor *,
				   uint64, uint64);
int agent_meta_journal_plan_delta(
	struct agent_meta_journal_plan *,
	const struct agent_meta_journal_cursor *, uint,
	struct workflow_lifecycle_key,
	const struct agent_meta_journal_change *, uint,
	const struct agent_durable_arena *,
	const struct agent_durable_arena *);
int agent_meta_journal_plan_validate(const struct agent_meta_journal_plan *,
				     const struct agent_meta_journal_cursor *);
int agent_meta_journal_cursor_publish(struct agent_meta_journal_cursor *,
				      const struct agent_meta_journal_plan *);
int agent_meta_journal_replay_init(struct agent_meta_journal_replay *,
				   uint64, uint64);
int agent_meta_journal_replay_block(struct agent_meta_journal_replay *,
				    struct agent_meta_store *, const void *,
				    uint);
int agent_meta_journal_replay_finish(struct agent_meta_journal_replay *);
int agent_meta_journal_apply_trusted(
	struct agent_meta_store *, const struct agent_meta_journal_plan *,
	short *, uint);

#endif
