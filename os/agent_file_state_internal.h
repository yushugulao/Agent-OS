#ifndef AGENT_FILE_STATE_INTERNAL_H
#define AGENT_FILE_STATE_INTERNAL_H

#include "agent.h"
#include "workflow_lifecycle.h"

#define AGENT_INODE_META_VERSION 2
#define AGENT_INODE_META_DEFERRED_SLOT (-1)

/* 单次目录日志事务捕获的精确内容覆盖。 */
struct agent_file_content_receipt {
	uint64 sequence;
	uint64 dev, inum, incarnation;
	uint scope_id, slot;
	struct workflow_lifecycle_key lifecycle;
};

/* 与 incarnation 绑定的文件版本、编辑租约和摘要缓存。 */
void agent_file_state_init(void);
uint64 agent_file_state_now(void);
void agent_file_state_scope_reclaim(uint);
uint64 agent_file_state_scope_generation(uint);
uint64 agent_file_state_generation_next(uint);
/* begin/end 界定一次关中断快照，覆盖内容只在区间内有效。 */
int agent_file_state_snapshot_begin(uint64 *);
void agent_file_state_snapshot_overlay_receipt(
	struct agent_file_meta *, uint, uint,
	struct workflow_lifecycle_key,
	struct agent_file_content_receipt *);
void agent_file_state_snapshot_end(int);
void agent_file_state_content_bump(struct inode *);
int agent_file_state_content_publish(
	struct inode *, struct agent_file_content_receipt *);
void agent_file_state_unbind_catalog_identity(uint64, uint64, uint64, uint);
int agent_file_state_content_settle(
	const struct agent_file_content_receipt *, struct agent_file_meta *);
void agent_file_state_content_absorb_volatile(struct inode *, uint);
int agent_file_state_size_settle(struct agent_file_meta *, uint, uint,
	struct workflow_lifecycle_key, uint64);
int agent_file_state_index_deferred(struct inode *);
int agent_file_state_set_index(struct inode *, short, short, int);
void agent_file_state_overlay_published_size(struct agent_file_meta *, uint);
void agent_file_state_project_hit(struct agent_file_hit *,
				  const struct agent_file_meta *, uint);
int agent_file_state_digest_cacheable(struct inode *);
int agent_file_state_digest_cache_lookup(struct inode *,
					 struct agent_result *, uint64 *);
void agent_file_state_digest_cache_store(struct inode *, uint64, uint64,
					 uint64, uint64, char *);
void agent_file_state_fill_info(struct agent_info *);

#endif
