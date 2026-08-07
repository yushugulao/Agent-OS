#include "agent_metadata_store_format.h"
#include "string.h"

/* v5 缺少生命周期键，仅供单向迁移解码。 */
struct agent_meta_record_v5 {
	struct agent_file_meta meta;
	uint scope_id, slot;
};

uint64
agent_meta_format_hash_mix(uint64 h, uint64 v)
{
	return agent_meta_disk_hash_mix(h, v);
}

uint64
agent_meta_format_hash_bytes(uint64 h, const char *buf, uint n)
{
	for (uint i = 0; i < n; i++) {
		h ^= (uchar)buf[i];
		h *= AGENT_META_STORE_HASH_PRIME;
	}
	return h;
}

static int
format_bytes(uint64 count, uint64 prefix, uint64 stride, uint *bytes)
{
	uint64 total;

	if (bytes == 0 || count > AGENT_FILE_META_MAX)
		return -1;
	total = sizeof(struct agent_meta_store_header) +
		prefix + count * stride;
	if (total > sizeof(struct agent_meta_store) || total > MAXFILE * BSIZE)
		return -1;
	*bytes = total;
	return 0;
}

int
agent_meta_format_store_bytes(uint64 count, uint *bytes)
{
	return format_bytes(count, sizeof(struct agent_durable_arena),
			    sizeof(struct agent_meta_record), bytes);
}

int
agent_meta_format_store_v7_bytes(uint64 count, uint *bytes)
{
	return format_bytes(count, sizeof(struct agent_durable_arena),
			    sizeof(struct agent_meta_record), bytes);
}

int
agent_meta_format_store_v5_bytes(uint64 count, uint *bytes)
{
	return format_bytes(count, 0, sizeof(struct agent_meta_record_v5), bytes);
}

uint64
agent_meta_format_payload_hash(const struct agent_meta_store_header *header,
			       const char *payload, uint payload_bytes)
{
	return agent_meta_disk_payload_hash(header, payload, payload_bytes);
}

uint64
agent_meta_format_store_hash(struct agent_meta_store *store)
{
	uint bytes;

	if (store == 0 ||
	    agent_meta_format_store_bytes(store->header.count, &bytes) < 0)
		return 0;
	return agent_meta_format_payload_hash(
		&store->header, (char *)&store->durable,
		bytes - sizeof(struct agent_meta_store_header));
}

static int
records_valid(struct agent_meta_store *store, uint stride, int legacy)
{
	uint ordinary = 0;

	for (uint64 i = 0; i < store->header.count; i++) {
		struct agent_meta_record_v5 *record =
			(void *)((char *)store->records + i * stride);
		int owned = 0;
		int limit = record->scope_id == VFS_SCOPE_SYSTEM ?
			AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;

		if (!agent_metadata_catalog_record_base_valid(
			    &record->meta, record->scope_id, record->slot))
			return 0;
		if (record->scope_id != VFS_SCOPE_SYSTEM &&
		    ++ordinary > AGENT_FILE_ORDINARY_LIMIT)
			return 0;
		if (!legacy) {
			struct workflow_lifecycle_key lifecycle =
				((struct agent_meta_record *)(void *)record)->lifecycle;

			if (record->scope_id == VFS_SCOPE_SYSTEM ?
			    !workflow_lifecycle_key_equal(
				    lifecycle, workflow_lifecycle_none()) :
			    (!workflow_lifecycle_key_valid(lifecycle) ||
			     lifecycle.id > WORKFLOW_LIFECYCLE_CAP))
				return 0;
		}
		for (uint64 j = 0; j < i; j++) {
			struct agent_meta_record_v5 *prior =
				(void *)((char *)store->records + j * stride);

			if (prior->slot == record->slot)
				return 0;
			if (prior->scope_id != record->scope_id)
				continue;
			owned++;
			if (prior->meta.fid == record->meta.fid ||
			    strncmp(prior->meta.physical_name,
				    record->meta.physical_name,
				    sizeof(record->meta.physical_name)) == 0 ||
			    (!legacy && record->meta.logical_path[0] &&
			     strncmp(prior->meta.logical_path,
				     record->meta.logical_path,
				     sizeof(record->meta.logical_path)) == 0) ||
			    (record->meta.dev != 0 &&
			     prior->meta.dev == record->meta.dev &&
			     prior->meta.inum == record->meta.inum &&
			     prior->meta.incarnation == record->meta.incarnation))
				return 0;
		}
		if (owned >= limit)
			return 0;
	}
	return 1;
}

int
agent_meta_format_records_valid(struct agent_meta_store *store)
{
	return agent_durable_arena_validate(&store->durable) >= 0 &&
	       records_valid(store, sizeof(struct agent_meta_record), 0);
}

int
agent_meta_format_v7_records_valid(struct agent_meta_store *store)
{
	return agent_durable_arena_validate(&store->durable) >= 0 &&
	       records_valid(store, sizeof(struct agent_meta_record), 0);
}

int
agent_meta_format_recover_identifiers(const struct agent_meta_store *store)
{
	for (uint64 i = 0; i < store->header.count; i++) {
		const struct agent_meta_record *record = &store->records[i];

		if (record->scope_id != VFS_SCOPE_SYSTEM &&
		    workflow_lifecycle_generation_floor(record->lifecycle) < 0)
			return -1;
	}
	return agent_durable_arena_recover(&store->durable);
}

int
agent_meta_format_v5_records_valid(struct agent_meta_store *store)
{
	return records_valid(store, sizeof(struct agent_meta_record_v5), 1);
}

int
agent_meta_format_migrate_v7(struct agent_meta_store *store)
{
	if (store == 0 || store->header.version != AGENT_META_STORE_VERSION_V7 ||
	    !agent_meta_format_v7_records_valid(store))
		return -1;
	store->header.version = AGENT_META_STORE_VERSION;
	store->header.payload_hash = agent_meta_format_store_hash(store);
	return store->header.payload_hash ? 0 : -1;
}

int
agent_meta_format_migrate_v5(struct agent_meta_store *store)
{
	struct agent_meta_record_v5 *legacy =
		(struct agent_meta_record_v5 *)store->records;
	uint64 count = store->header.count, out = 0;
	uint bytes;

	/* 逆向展开，避免原地覆盖源记录。 */
	for (uint64 i = count; i > 0; i--) {
		struct agent_meta_record_v5 source = legacy[i - 1];
		struct agent_meta_record *target = &store->records[i - 1];

		memset(target, 0, sizeof(*target));
		target->meta = source.meta;
		target->scope_id = source.scope_id;
		target->slot = source.slot;
		target->lifecycle = workflow_lifecycle_none();
	}
	/* v5 动态记录缺少可信代次，予以隔离。 */
	for (uint64 i = 0; i < count; i++) {
		if (store->records[i].scope_id != VFS_SCOPE_SYSTEM)
			continue;
		if (out != i)
			store->records[out] = store->records[i];
		out++;
	}
	if (agent_durable_arena_init(&store->durable) < 0)
		return -1;
	store->header.version = AGENT_META_STORE_VERSION;
	store->header.count = out;
	store->header.payload_hash = agent_meta_format_store_hash(store);
	if (agent_meta_format_store_bytes(out, &bytes) < 0)
		return -1;
	memset((char *)store + bytes, 0, sizeof(*store) - bytes);
	return 0;
}
