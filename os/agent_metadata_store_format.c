#include "agent_metadata_store_format.h"
#include "string.h"

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

int
agent_meta_format_store_bytes(uint64 count, uint *bytes)
{
	uint64 total;

	if (bytes == 0 || count > AGENT_FILE_META_MAX)
		return -1;
	total = sizeof(struct agent_meta_store_header) +
		sizeof(struct agent_durable_arena) +
		count * sizeof(struct agent_meta_record);
	if (total > sizeof(struct agent_meta_store) || total > MAXFILE * BSIZE)
		return -1;
	*bytes = total;
	return 0;
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
records_valid(struct agent_meta_store *store)
{
	uint ordinary = 0;

	for (uint64 i = 0; i < store->header.count; i++) {
		struct agent_meta_record *record = &store->records[i];
		struct workflow_lifecycle_key lifecycle = record->lifecycle;
		int owned = 0;
		int limit = record->scope_id == VFS_SCOPE_SYSTEM ?
			AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;

		if (!agent_metadata_catalog_record_base_valid(
			    &record->meta, record->scope_id, record->slot))
			return 0;
		if (record->scope_id != VFS_SCOPE_SYSTEM &&
		    ++ordinary > AGENT_FILE_ORDINARY_LIMIT)
			return 0;
		if (record->scope_id == VFS_SCOPE_SYSTEM ?
		    !workflow_lifecycle_key_equal(
			    lifecycle, workflow_lifecycle_none()) :
		    (!workflow_lifecycle_key_valid(lifecycle) ||
		     lifecycle.id > WORKFLOW_LIFECYCLE_CAP))
			return 0;
		for (uint64 j = 0; j < i; j++) {
			struct agent_meta_record *prior = &store->records[j];

			if (prior->slot == record->slot)
				return 0;
			if (prior->scope_id != record->scope_id)
				continue;
			owned++;
			if (prior->meta.fid == record->meta.fid ||
			    strncmp(prior->meta.physical_name,
				    record->meta.physical_name,
				    sizeof(record->meta.physical_name)) == 0 ||
			    (record->meta.logical_path[0] &&
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
	       records_valid(store);
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
agent_meta_format_migrate_v7(struct agent_meta_store *store)
{
	if (store == 0 || store->header.version != AGENT_META_STORE_VERSION_V7 ||
	    !agent_meta_format_records_valid(store))
		return -1;
	store->header.version = AGENT_META_STORE_VERSION;
	store->header.payload_hash = agent_meta_format_store_hash(store);
	return store->header.payload_hash ? 0 : -1;
}
