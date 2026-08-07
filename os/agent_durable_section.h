#ifndef AGENT_DURABLE_SECTION_H
#define AGENT_DURABLE_SECTION_H

#include "types.h"
#include "workflow_lifecycle.h"
#include "../agent_metadata_disk_abi.h"

#define AGENT_DURABLE_DIRTY_MAX (WORKFLOW_LIFECYCLE_CAP + 1U)
#define AGENT_DURABLE_DIRTY_URGENT (1U << 0)

_Static_assert(AGENT_DURABLE_DIRTY_MAX > WORKFLOW_LIFECYCLE_CAP,
	       "durable dirty table reserves a system writeback slot");

enum agent_durable_section_kind {
	AGENT_DURABLE_SECTION_OBSERVE = 1,
};

struct agent_durable_section_ops {
	uint kind;
	uint version;
	uint image_bytes;
	int (*update_scope)(void *, uint, uint,
			    struct workflow_lifecycle_key, uint64 *);
	int (*validate)(const void *, uint);
	int (*recover)(const void *, uint);
	int (*has_scope)(const void *, uint, uint);
	void (*replicated_scope)(uint);
};

/* 持久区只发布回写意图，元数据存储模块负责执行，依赖保持单向。 */
struct agent_durable_store_ops {
	uint64 (*mark_dirty)(uint);
	void (*expedite)(uint);
	int (*replicated)(uint, uint64);
	int (*active_replicated)(uint64);
	int (*persist_scope)(uint);
};

void agent_durable_section_init(void);
int agent_durable_section_register(const struct agent_durable_section_ops *);
void agent_durable_section_set_store_provider(
	const struct agent_durable_store_ops *);
int agent_durable_section_retry_pending(void);
uint64 agent_durable_section_mark_dirty(uint, uint);
uint64 agent_durable_section_mark_dirty_evidence(uint, uint, uint64 *, uint);
int agent_durable_section_replicated(uint, uint64);
int agent_durable_section_active_replicated(uint64);
int agent_durable_section_persist_scope(uint);
int agent_durable_arena_init(struct agent_durable_arena *);
int agent_durable_arena_validate(const struct agent_durable_arena *);
int agent_durable_arena_update_scope(struct agent_durable_arena *, uint,
				     struct workflow_lifecycle_key,
				     uint64 *);
int agent_durable_arena_recover(const struct agent_durable_arena *);
int agent_durable_arena_has_scope(const struct agent_durable_arena *, uint);
int agent_durable_section_scope_pending(uint);
void agent_durable_section_commit_scope(uint, uint64);
void agent_durable_section_mirror_scope(uint);
void agent_durable_section_active_bind(const struct agent_durable_arena *,
				       uint64);
uint64 agent_durable_section_active_generation(void);
/* 调用者须禁止中断；返回的只读视图不得跨越临界区或发生阻塞。 */
const uchar *agent_durable_section_active_view(uint, uint *, uint64 *);

#endif
