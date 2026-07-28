#include "../../os/agent_file_name_policy.h"
#include "../../os/agent_durable_section.h"
#include "../../os/workflow_lifecycle.h"
#include "../../os/agent_metadata_catalog.h"
#include "../../os/agent_metadata_disk.h"

#define LAYOUT_DESCRIPTOR_MAGIC 0x41474d4449534b31ULL
#define LAYOUT_DESCRIPTOR_VERSION 1U
#define LAYOUT_WORDS 28U

#define MEMBER_OFFSET(type, member) __builtin_offsetof(type, member)
#define MEMBER_SIZE(type, member) sizeof(((type *)0)->member)
#define RECORD_META_OFFSET MEMBER_OFFSET(struct agent_meta_record, meta)

struct agent_metadata_disk_layout_descriptor {
	uint64 words[LAYOUT_WORDS];
	char bank_names[AGENT_META_STORE_BANKS][DIRSIZ];
} __attribute__((packed));

_Static_assert(AGENT_META_STORE_BANKS == 2U,
	       "host descriptor currently serializes exactly two COW banks");
_Static_assert(AGENT_META_STORE_DURABLE_BYTES ==
	       sizeof(struct agent_durable_arena),
	       "durable arena size must match the disk ABI");

const struct agent_metadata_disk_layout_descriptor
	agent_metadata_disk_layout_descriptor
	__attribute__((used, section(".agent_metadata_layout"))) = {
	.words = {
		LAYOUT_DESCRIPTOR_MAGIC,
		LAYOUT_DESCRIPTOR_VERSION,
		sizeof(struct agent_metadata_disk_layout_descriptor),
		AGENT_META_STORE_MAGIC,
		AGENT_META_STORE_VERSION,
		AGENT_META_STORE_HASH_ALGORITHM,
		AGENT_META_STORE_HASH_INITIAL,
		AGENT_META_STORE_HASH_PRIME,
		sizeof(struct agent_meta_store_header),
		MEMBER_SIZE(struct agent_meta_store_header, magic),
		MEMBER_OFFSET(struct agent_meta_store_header, magic),
		MEMBER_OFFSET(struct agent_meta_store_header, version),
		MEMBER_OFFSET(struct agent_meta_store_header, count),
		MEMBER_OFFSET(struct agent_meta_store_header, generation),
		MEMBER_OFFSET(struct agent_meta_store_header, payload_hash),
		AGENT_META_STORE_DURABLE_BYTES,
		sizeof(struct agent_meta_record),
		RECORD_META_OFFSET + MEMBER_OFFSET(struct agent_file_meta, used),
		MEMBER_SIZE(struct agent_file_meta, used),
		RECORD_META_OFFSET + MEMBER_OFFSET(struct agent_file_meta, fid),
		MEMBER_SIZE(struct agent_file_meta, fid),
		RECORD_META_OFFSET +
			MEMBER_OFFSET(struct agent_file_meta, physical_name),
		MEMBER_SIZE(struct agent_file_meta, physical_name),
		RECORD_META_OFFSET + MEMBER_OFFSET(struct agent_file_meta, status),
		MEMBER_SIZE(struct agent_file_meta, status),
		AGENT_FILE_META_MAX,
		DIRSIZ,
		AGENT_META_STORE_BANKS,
	},
	.bank_names = {
		AGENT_META_STORE_NAME_0,
		AGENT_META_STORE_NAME_1,
	},
};
