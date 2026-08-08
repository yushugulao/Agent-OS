#ifndef AGENT_METADATA_STORE_FORMAT_H
#define AGENT_METADATA_STORE_FORMAT_H

#include "agent_durable_section.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_disk.h"

/* 单个不可变元数据存储区镜像的私有内存视图。 */
struct agent_meta_store {
	struct agent_meta_store_header header;
	struct agent_durable_arena durable;
	struct agent_meta_record records[AGENT_FILE_META_MAX];
};

_Static_assert(AGENT_FILE_META_MAX == AGENT_META_STORE_MAX_RECORDS,
	       "metadata record capacity must match the disk ABI");
_Static_assert(sizeof(struct agent_meta_record) == AGENT_META_STORE_RECORD_BYTES,
	       "metadata record layout must match the disk ABI");
_Static_assert(sizeof(struct agent_meta_store) ==
	       AGENT_META_STORE_SNAPSHOT_MAX_BYTES,
	       "metadata snapshot capacity must match the disk ABI");
_Static_assert(__builtin_offsetof(struct agent_meta_store, records) ==
	       AGENT_META_STORE_GENESIS_BYTES,
	       "canonical genesis must end at the record array");

uint64 agent_meta_format_hash_mix(uint64, uint64);
uint64 agent_meta_format_hash_bytes(uint64, const char *, uint);
uint64 agent_meta_format_payload_hash(
	const struct agent_meta_store_header *, const char *, uint);
uint64 agent_meta_format_store_hash(struct agent_meta_store *);
int agent_meta_format_store_bytes(uint64, uint *);
int agent_meta_format_records_valid(struct agent_meta_store *);
int agent_meta_format_migrate_v7(struct agent_meta_store *);
int agent_meta_format_recover_identifiers(const struct agent_meta_store *);

#endif
