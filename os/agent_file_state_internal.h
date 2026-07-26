#ifndef AGENT_FILE_STATE_INTERNAL_H
#define AGENT_FILE_STATE_INTERNAL_H

#include "agent.h"

#define AGENT_INODE_META_VERSION 2

/* Incarnation-bound file versions, edit leases, and digest cache. */
void agent_file_state_init(void);
void agent_file_state_scope_reclaim(uint);
uint64 agent_file_state_scope_generation(uint);
uint64 agent_file_state_generation_next(uint);
/* Begin/end delimit one IRQ-disabled snapshot; overlay is valid only inside it. */
int agent_file_state_snapshot_begin(uint64 *);
void agent_file_state_snapshot_overlay(struct agent_file_meta *, uint);
void agent_file_state_snapshot_end(int);
void agent_file_state_content_bump(struct inode *);
int agent_file_state_size_publish(struct inode *, int);
void agent_file_state_overlay_published_size(struct agent_file_meta *, uint);
void agent_file_state_sizes_persisted(uint, uint64);
int agent_file_state_digest_cacheable(struct inode *);
int agent_file_state_digest_cache_lookup(struct inode *,
					 struct agent_result *, uint64 *);
void agent_file_state_digest_cache_store(struct inode *, uint64, uint64,
					 uint64, uint64, char *);
void agent_file_state_fill_info(struct agent_info *);

#endif
