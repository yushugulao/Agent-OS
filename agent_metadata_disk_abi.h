#ifndef AGENT_METADATA_DISK_ABI_H
#define AGENT_METADATA_DISK_ABI_H

#if !defined(__BYTE_ORDER__) || !defined(__ORDER_LITTLE_ENDIAN__) || \
	__BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "metadata disk ABI is little endian"
#endif

/* 共享定宽 ABI。 */
typedef unsigned int amd_u32;
typedef unsigned long long amd_u64;

#define AGENT_META_STORE_BANKS 2U
#define AGENT_META_STORE_NAME_0 ".agentmeta"
#define AGENT_META_STORE_NAME_1 ".agentmeta1"
#define AGENT_META_STORE_MAGIC 0x41474d4554413036ULL
#define AGENT_META_STORE_VERSION_V5 5U
#define AGENT_META_STORE_VERSION_V7 7U
#define AGENT_META_STORE_VERSION 8U
#define AGENT_META_STORE_GENESIS_GENERATION 1ULL
#define AGENT_META_STORE_HASH_ALGORITHM 1U
#define AGENT_META_STORE_HASH_INITIAL 1469598103934665603ULL
#define AGENT_META_STORE_HASH_PRIME 1099511628211ULL
#define AGENT_META_STORE_DURABLE_BYTES 8192U
#define AGENT_META_STORE_RECORD_BYTES 416U
#define AGENT_META_STORE_MAX_RECORDS 512U

/*
 * 版本 8 保留规范完整 bank 映像作为压缩基线，并以块对齐的预分配尾部
 * 记录普通 workflow 增量。日志代际绝不覆写尾块：压缩先切换到对端 bank
 * 并建立新基线代际，之后才可复用旧尾部。
 */
#define AGENT_META_JOURNAL_MAGIC 0x41474d4a4e4c3038ULL
#define AGENT_META_JOURNAL_VERSION 1U
#define AGENT_META_JOURNAL_KIND_DATA 1U
#define AGENT_META_JOURNAL_KIND_COMMIT 2U
#define AGENT_META_JOURNAL_KIND_PAD 3U
#define AGENT_META_JOURNAL_OP_NONE 0U
#define AGENT_META_JOURNAL_OP_UPSERT 1U
#define AGENT_META_JOURNAL_OP_DELETE 2U
#define AGENT_META_JOURNAL_OP_ARENA_PATCH 3U
#define AGENT_META_JOURNAL_BLOCK_BYTES 1024U
#define AGENT_META_JOURNAL_SLOT_BYTES 512U
#define AGENT_META_JOURNAL_HEADER_BYTES 96U
#define AGENT_META_JOURNAL_PAYLOAD_BYTES AGENT_META_STORE_RECORD_BYTES
#define AGENT_META_JOURNAL_SLOTS_PER_BLOCK 2U
#define AGENT_META_JOURNAL_BLOCKS 32U
#define AGENT_META_JOURNAL_SLOTS \
	(AGENT_META_JOURNAL_BLOCKS * AGENT_META_JOURNAL_SLOTS_PER_BLOCK)
#define AGENT_META_JOURNAL_MAX_DATA_RECORDS 15U
#define AGENT_META_JOURNAL_MAX_TXN_BLOCKS 8U
#define AGENT_META_JOURNAL_COMPACTION_RESERVE_BLOCKS 8U

#define AGENT_DURABLE_ARENA_MAGIC 0x4147445552413031ULL
#define AGENT_DURABLE_ARENA_VERSION 1U
#define AGENT_DURABLE_ARENA_BYTES AGENT_META_STORE_DURABLE_BYTES
#define AGENT_DURABLE_SECTION_MAX 2U

typedef struct agent_meta_store_header {
	amd_u64 magic, version, count, generation, payload_hash;
} amd_header;

typedef struct agent_durable_section_desc {
	amd_u32 kind, version, offset, bytes;
	amd_u64 generation, payload_hash;
} amd_section;

#define AGENT_DURABLE_PAYLOAD_BYTES 8088U

typedef struct agent_durable_arena {
	amd_u64 magic;
	amd_u32 version, bytes, section_count, used_bytes;
	amd_u64 generation;
	amd_section sections[AGENT_DURABLE_SECTION_MAX];
	unsigned char payload[AGENT_DURABLE_PAYLOAD_BYTES];
	amd_u64 image_hash;
} amd_arena;

typedef struct agent_meta_store_genesis {
	amd_header header;
	amd_arena durable;
} amd_genesis;

#define AGENT_META_STORE_GENESIS_BYTES sizeof(amd_genesis)
#define AGENT_META_STORE_SNAPSHOT_MAX_BYTES \
	(AGENT_META_STORE_GENESIS_BYTES + \
	 AGENT_META_STORE_MAX_RECORDS * AGENT_META_STORE_RECORD_BYTES)
#define AGENT_META_STORE_V7_MAX_BYTES AGENT_META_STORE_SNAPSHOT_MAX_BYTES
#define AGENT_META_JOURNAL_OFFSET 222208U
#define AGENT_META_JOURNAL_BYTES \
	(AGENT_META_JOURNAL_BLOCKS * AGENT_META_JOURNAL_BLOCK_BYTES)
#define AGENT_META_STORE_MAX_BYTES \
	(AGENT_META_JOURNAL_OFFSET + AGENT_META_JOURNAL_BYTES)

typedef struct agent_meta_journal_header {
	amd_u64 magic;
	amd_u32 version, kind;
	amd_u64 base_generation, generation;
	amd_u32 scope_id, lifecycle_id;
	amd_u64 lifecycle_generation;
	amd_u32 record_index, record_count, operation, payload_bytes;
	amd_u64 previous_commit_hash, payload_hash, group_hash, slot_hash;
} amd_journal_header;

typedef struct agent_meta_journal_slot {
	amd_journal_header header;
	unsigned char payload[AGENT_META_JOURNAL_PAYLOAD_BYTES];
} amd_journal_slot;

/*
 * durable arena 增量是规范的 400 字节窗口。旧窗口哈希把重放绑定到上一
 * 已提交代际产生的精确基线，槽校验和保护新字节。
 */
#define AGENT_META_JOURNAL_PATCH_DATA_BYTES 400U
typedef struct agent_meta_journal_arena_patch {
	amd_u32 offset, bytes;
	amd_u64 before_hash;
	unsigned char data[AGENT_META_JOURNAL_PATCH_DATA_BYTES];
} amd_journal_arena_patch;

_Static_assert(sizeof(amd_u32) == 4U && sizeof(amd_u64) == 8U &&
	       sizeof(amd_header) == 40U && sizeof(amd_section) == 32U &&
	       sizeof(amd_arena) == AGENT_DURABLE_ARENA_BYTES &&
	       sizeof(amd_genesis) == 8232U,
	       "metadata ABI");
_Static_assert(AGENT_META_STORE_SNAPSHOT_MAX_BYTES == 221224U &&
	       AGENT_META_JOURNAL_OFFSET == 217U *
		       AGENT_META_JOURNAL_BLOCK_BYTES &&
	       AGENT_META_STORE_SNAPSHOT_MAX_BYTES <=
		       AGENT_META_JOURNAL_OFFSET &&
	       AGENT_META_STORE_MAX_BYTES == 249U *
		       AGENT_META_JOURNAL_BLOCK_BYTES,
	       "metadata v8 fixed tail layout");
_Static_assert(sizeof(amd_journal_header) ==
	       AGENT_META_JOURNAL_HEADER_BYTES &&
	       sizeof(amd_journal_slot) == AGENT_META_JOURNAL_SLOT_BYTES &&
	       sizeof(amd_journal_arena_patch) ==
		       AGENT_META_JOURNAL_PAYLOAD_BYTES &&
	       __builtin_offsetof(amd_journal_header, magic) == 0U &&
	       __builtin_offsetof(amd_journal_header, version) == 8U &&
	       __builtin_offsetof(amd_journal_header, kind) == 12U &&
	       __builtin_offsetof(amd_journal_header, base_generation) == 16U &&
	       __builtin_offsetof(amd_journal_header, generation) == 24U &&
	       __builtin_offsetof(amd_journal_header, scope_id) == 32U &&
	       __builtin_offsetof(amd_journal_header, lifecycle_id) == 36U &&
	       __builtin_offsetof(amd_journal_header, lifecycle_generation) == 40U &&
	       __builtin_offsetof(amd_journal_header, record_index) == 48U &&
	       __builtin_offsetof(amd_journal_header, record_count) == 52U &&
	       __builtin_offsetof(amd_journal_header, operation) == 56U &&
	       __builtin_offsetof(amd_journal_header, payload_bytes) == 60U &&
	       __builtin_offsetof(amd_journal_header, previous_commit_hash) == 64U &&
	       __builtin_offsetof(amd_journal_header, payload_hash) == 72U &&
	       __builtin_offsetof(amd_journal_header, group_hash) == 80U &&
	       __builtin_offsetof(amd_journal_header, slot_hash) == 88U,
	       "metadata v8 journal slot ABI");
_Static_assert(AGENT_META_JOURNAL_MAX_DATA_RECORDS + 1U <=
	       AGENT_META_JOURNAL_MAX_TXN_BLOCKS *
		       AGENT_META_JOURNAL_SLOTS_PER_BLOCK &&
	       AGENT_META_JOURNAL_SLOTS == 64U,
	       "metadata v8 transaction bound");

static inline amd_u64
agent_disk_hash(amd_u64 hash, const void *data, amd_u32 bytes)
{
	const unsigned char *p = data;

	while (bytes--) {
		hash ^= *p++;
		hash *= AGENT_META_STORE_HASH_PRIME;
	}
	return hash;
}

static inline amd_u64
agent_meta_disk_hash_mix(amd_u64 hash, amd_u64 value)
{
	for (amd_u32 i = 0; i < sizeof(value); i++, value >>= 8) {
		hash ^= (unsigned char)value;
		hash *= AGENT_META_STORE_HASH_PRIME;
	}
	return hash;
}

static inline amd_u64
agent_meta_disk_payload_hash(const amd_header *header,
			     const void *payload, amd_u32 bytes)
{
	amd_u64 hash = agent_disk_hash(
		AGENT_META_STORE_HASH_INITIAL, header,
		__builtin_offsetof(amd_header, payload_hash));

	return agent_disk_hash(hash, payload, bytes);
}

static inline void
agent_durable_disk_init_empty(amd_arena *arena)
{
	unsigned char *p = (unsigned char *)arena;

	for (amd_u32 i = 0; i < sizeof(*arena); i++)
		p[i] = 0;
	arena->magic = AGENT_DURABLE_ARENA_MAGIC;
	arena->version = AGENT_DURABLE_ARENA_VERSION;
	arena->bytes = sizeof(*arena);
	arena->generation = AGENT_META_STORE_GENESIS_GENERATION;
	amd_u64 hash = agent_disk_hash(AGENT_META_STORE_HASH_INITIAL,
		arena, __builtin_offsetof(amd_arena, image_hash));
	arena->image_hash = hash ? hash : 1ULL;
}

static inline void
agent_meta_disk_init_genesis(amd_genesis *image)
{
	agent_durable_disk_init_empty(&image->durable);
	image->header.magic = AGENT_META_STORE_MAGIC;
	image->header.version = AGENT_META_STORE_VERSION;
	image->header.count = 0;
	image->header.generation = AGENT_META_STORE_GENESIS_GENERATION;
	image->header.payload_hash = agent_meta_disk_payload_hash(
		&image->header, &image->durable, sizeof(image->durable));
}

#endif
