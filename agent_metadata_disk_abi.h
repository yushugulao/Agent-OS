#ifndef AGENT_METADATA_DISK_ABI_H
#define AGENT_METADATA_DISK_ABI_H

#if !defined(__BYTE_ORDER__) || !defined(__ORDER_LITTLE_ENDIAN__) || \
	__BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "metadata disk ABI is little endian"
#endif

/* Shared fixed-width ABI. */
typedef unsigned int amd_u32;
typedef unsigned long long amd_u64;

#define AGENT_META_STORE_BANKS 2U
#define AGENT_META_STORE_NAME_0 ".agentmeta"
#define AGENT_META_STORE_NAME_1 ".agentmeta1"
#define AGENT_META_STORE_MAGIC 0x41474d4554413036ULL
#define AGENT_META_STORE_VERSION_V5 5U
#define AGENT_META_STORE_VERSION 7U
#define AGENT_META_STORE_GENESIS_GENERATION 1ULL
#define AGENT_META_STORE_HASH_ALGORITHM 1U
#define AGENT_META_STORE_HASH_INITIAL 1469598103934665603ULL
#define AGENT_META_STORE_HASH_PRIME 1099511628211ULL
#define AGENT_META_STORE_DURABLE_BYTES 8192U
#define AGENT_META_STORE_RECORD_BYTES 416U
#define AGENT_META_STORE_MAX_RECORDS 512U

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
#define AGENT_META_STORE_MAX_BYTES \
	(AGENT_META_STORE_GENESIS_BYTES + \
	 AGENT_META_STORE_MAX_RECORDS * AGENT_META_STORE_RECORD_BYTES)

_Static_assert(sizeof(amd_u32) == 4U && sizeof(amd_u64) == 8U &&
	       sizeof(amd_header) == 40U && sizeof(amd_section) == 32U &&
	       sizeof(amd_arena) == AGENT_DURABLE_ARENA_BYTES &&
	       sizeof(amd_genesis) == 8232U,
	       "metadata ABI");

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
