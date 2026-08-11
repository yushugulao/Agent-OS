#ifndef AGENT_FILE_STATE_INTERNAL_H
#define AGENT_FILE_STATE_INTERNAL_H

#include "agent.h"
#include "workflow_lifecycle.h"

#define AGENT_INODE_META_VERSION 2

/* 单次内容发布捕获的对象身份和大小序列。 */
struct agent_file_content_receipt {
	uint64 sequence;
	uint64 dev, inum, incarnation;
	uint scope_id, slot;
	struct workflow_lifecycle_key lifecycle;
};

/* 与对象世代绑定的文件版本、编辑租约和摘要缓存。 */
void agent_file_state_init(void);
uint64 agent_file_state_now(void);
void agent_file_state_scope_reclaim(uint);
uint64 agent_file_state_scope_generation(uint);
uint64 agent_file_state_generation_next(uint);
void agent_file_state_content_bump(struct inode *);
int agent_file_state_content_publish(
	struct inode *, struct agent_file_content_receipt *);
void agent_file_state_unbind_catalog_identity(uint64, uint64, uint64, uint);
int agent_file_state_set_index(struct inode *, short, short);
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
