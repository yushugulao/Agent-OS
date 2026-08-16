#ifndef AGENT_METADATA_CATALOG_H
#define AGENT_METADATA_CATALOG_H

#include "agent.h"
#include "agent_file_state_internal.h"
#include "fs.h"

#define AGENT_CATALOG_SCOPE_MAX VFS_SCOPE_LIFECYCLE_CAP
#define AGENT_CATALOG_READ_WORDS ((AGENT_FILE_META_MAX + 63) / 64)

#define AGENT_FILE_CHANGE_STATUS       (1U << 0)
#define AGENT_FILE_CHANGE_STAGE        (1U << 1)
#define AGENT_FILE_CHANGE_KIND         (1U << 2)
#define AGENT_FILE_CHANGE_SCOPE_KEYS   (1U << 3)
#define AGENT_FILE_CHANGE_DEPENDENCY   (1U << 4)
#define AGENT_FILE_CHANGE_MEMBERSHIP   (1U << 5)
#define AGENT_FILE_CHANGE_INDEX_ALL \
	(AGENT_FILE_CHANGE_STATUS | AGENT_FILE_CHANGE_STAGE | \
	 AGENT_FILE_CHANGE_KIND | AGENT_FILE_CHANGE_MEMBERSHIP)
#define AGENT_FILE_CHANGE_ALL \
	(AGENT_FILE_CHANGE_INDEX_ALL | AGENT_FILE_CHANGE_SCOPE_KEYS | \
	 AGENT_FILE_CHANGE_DEPENDENCY)

#define AGENT_CATALOG_INDEX_STATUS 1
#define AGENT_CATALOG_INDEX_STAGE  2
#define AGENT_CATALOG_INDEX_KIND   3
#define AGENT_CATALOG_STALE       -2
#define AGENT_CATALOG_CONFLICT    -3
#define AGENT_CATALOG_INDETERMINATE -4
#define AGENT_CATALOG_NO_SPACE    -5
#define AGENT_CATALOG_INTERRUPTED -6

#define AGENT_CATALOG_KEY_FID          (1U << 0)
#define AGENT_CATALOG_KEY_PHYSICAL     (1U << 1)
#define AGENT_CATALOG_KEY_LOGICAL      (1U << 2)
#define AGENT_CATALOG_KEY_IDENTITY     (1U << 3)
#define AGENT_CATALOG_KEY_PATH \
	(AGENT_CATALOG_KEY_PHYSICAL | AGENT_CATALOG_KEY_LOGICAL)

static inline int
agent_metadata_catalog_identity_state(const struct agent_file_meta *meta) {
	int present = meta->dev != 0 && meta->inum != 0 && meta->incarnation != 0;
	int absent = meta->dev == 0 && meta->inum == 0 && meta->incarnation == 0;
	return present ? 1 : absent ? 0 : -1;
}

/* 视图仅属于当前事务，工作检查点前必须释放。 */
struct agent_catalog_view {
	const struct agent_file_meta *meta;
	uint scope_id;
	struct workflow_lifecycle_key lifecycle;
};

/*
 * 读多写少查询只快照有界候选位图。活动变更期间返回重试；单条记录在
 * 短暂关中断区内复制，发布前校验目录、内容和生命周期代际。休眠或等待
 * 文件系统操作或等待时不持有目录指针。
 */
struct agent_catalog_read_snapshot {
	uint64 generation;
	uint64 fs_generation;
	uint scope_id;
	struct workflow_lifecycle_key lifecycle;
	uint64 candidates[AGENT_CATALOG_READ_WORDS];
};

/* 编辑使用目录自有暂存区；提交是唯一变更边界。 */
struct agent_catalog_edit {
	struct agent_file_meta *meta;
	uint scope_id;
	int slot;
};

/*
 * 同步目录变更短暂释放事务门锁执行有界文件系统操作时，持有此目录专用栅栏。
 * 令牌由目录签发和校验，调用方仅负责传递。
 */
struct agent_catalog_mutation_fence {
	uint64 token;
};

/* 回滚内存状态前必须精确匹配变更后的身份。 */
struct agent_catalog_undo_token {
	uint64 fence_token, catalog_generation, slot_binding;
	int slot;
	uint reserved;
};
#define AGENT_CATALOG_UNDO_CREATED (1U << 0)

struct agent_catalog_resolution {
	int slot, owned, ordinary;
	uint provided, matched;
};

static inline int agent_scope_valid(uint scope_id) {
	return scope_id >= VFS_SCOPE_FIRST_DYNAMIC && scope_id < FS_OWNER_SCOPE_FLAG;
}

static inline int agent_object_scope_valid(uint scope_id) {
	return scope_id == VFS_SCOPE_SYSTEM || agent_scope_valid(scope_id);
}

static inline int agent_object_scope_visible(uint requester_scope, uint object_scope) {
	return agent_scope_valid(requester_scope) &&
	       (object_scope == requester_scope || object_scope == VFS_SCOPE_SYSTEM);
}

void agent_metadata_catalog_init(void);
int agent_metadata_catalog_field_contains(const char *, const char *);
uint64 agent_metadata_catalog_generation(void);
int agent_metadata_catalog_borrow(uint64, int, struct agent_catalog_view *);
int agent_metadata_catalog_borrow_scan(int, struct agent_catalog_view *);
int agent_metadata_catalog_read_begin(
	uint, int, const char *, int, struct agent_catalog_read_snapshot *, int *);
int agent_metadata_catalog_read_next(
	const struct agent_catalog_read_snapshot *, int);
int agent_metadata_catalog_read_copy(
	const struct agent_catalog_read_snapshot *, int,
	struct agent_file_meta *, uint *);
int agent_metadata_catalog_read_end(
	const struct agent_catalog_read_snapshot *);
int agent_metadata_catalog_edit_begin(int, uint, struct agent_catalog_edit *);
int agent_metadata_catalog_edit_commit_volatile(
	struct agent_catalog_edit *, uint);
void agent_metadata_catalog_edit_abort(struct agent_catalog_edit *);
int agent_metadata_catalog_mutation_begin(
	struct agent_catalog_mutation_fence *);
int agent_metadata_catalog_mutation_end(
	struct agent_catalog_mutation_fence *);
int agent_metadata_catalog_undo_capture(
	const struct agent_catalog_mutation_fence *, int,
	struct agent_catalog_undo_token *);
int agent_metadata_catalog_undo_note_created(
	const struct agent_catalog_mutation_fence *,
	struct agent_catalog_undo_token *);
int agent_metadata_catalog_bind_volatile(int, int, struct proc *);
int agent_metadata_catalog_clear_slot_volatile(int);
int agent_metadata_catalog_remove_identity_exact(
	struct workflow_lifecycle_key, uint, uint, uint, uint,
	struct agent_file_meta *, uint64 *);
int agent_metadata_catalog_restore_volatile(
	const struct agent_catalog_mutation_fence *,
	const struct agent_catalog_undo_token *,
	const struct agent_file_meta *, uint, int);
void agent_metadata_catalog_resolve(uint, const struct agent_file_meta *, int,
				    struct agent_catalog_resolution *);
int agent_metadata_catalog_alloc_slot(uint);
uint64 agent_metadata_catalog_alloc_fid(uint);
int agent_metadata_catalog_reclaim_scope(
	uint, struct workflow_lifecycle_key);
int agent_metadata_catalog_fence_generation(
	uint, struct workflow_lifecycle_key, uint64 *);

#endif
