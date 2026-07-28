#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_query.h"
#include "defs.h"

struct agent_query_alias {
	char name[10];
	ushort offset, size;
};

#define AGENT_QUERY_ALIAS(name_value, field) \
	{ name_value, (ushort)__builtin_offsetof(struct agent_file_query, field), \
	  (ushort)sizeof(((struct agent_file_query *)0)->field) }
#define AGENT_QUERY_FIELDS 8
#define AGENT_QUERY_INDEX_FIRST 5
#define AGENT_QUERY_META_DELTA \
	((int)__builtin_offsetof(struct agent_file_meta, physical_name) - \
	 (int)__builtin_offsetof(struct agent_file_query, physical_name))
static const struct agent_query_alias agent_query_aliases[] = {
	AGENT_QUERY_ALIAS("path", physical_name),
	AGENT_QUERY_ALIAS("logical", logical_path),
	AGENT_QUERY_ALIAS("project", project),
	AGENT_QUERY_ALIAS("workflow", workflow),
	AGENT_QUERY_ALIAS("run", run_id),
	AGENT_QUERY_ALIAS("status", status),
	AGENT_QUERY_ALIAS("stage", stage),
	AGENT_QUERY_ALIAS("kind", kind),
	AGENT_QUERY_ALIAS("physical", physical_name),
	AGENT_QUERY_ALIAS("object", logical_path),
	AGENT_QUERY_ALIAS("object_id", logical_path),
	AGENT_QUERY_ALIAS("namespace", project),
	AGENT_QUERY_ALIAS("run_id", run_id),
	AGENT_QUERY_ALIAS("state", status),
	AGENT_QUERY_ALIAS("label", stage),
	AGENT_QUERY_ALIAS("type", kind),
	AGENT_QUERY_ALIAS("summary", summary_contains),
};
#undef AGENT_QUERY_ALIAS

int
agent_metadata_query_from_payload(struct agent_file_query *q, char *payload)
{
	int i = 0, key, key_end, val, matched;
	char separator, tail;

	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		key = i;
		while (payload[i] && payload[i] != '=' && payload[i] != ':' &&
		       payload[i] != ';' && payload[i] != ',')
			i++;
		key_end = i;
		separator = payload[i];
		if (key_end - key >= AGENT_FILE_FIELD_SIZE ||
		    (payload[i] != '=' && payload[i] != ':'))
			return -1;
		val = ++i;
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',')
			i++;
		if (i - val >= AGENT_FILE_LOGICAL_SIZE)
			return -1;
		tail = payload[i];
		payload[key_end] = payload[i] = 0;
		matched = 0;
		for (uint j = 0; j < NELEM(agent_query_aliases); j++) {
			const struct agent_query_alias *alias =
				&agent_query_aliases[j];

			if (strncmp(payload + key, alias->name,
				    AGENT_FILE_FIELD_SIZE) != 0)
				continue;
			safestrcpy((char *)q + alias->offset, payload + val,
				   alias->size);
			matched = 1;
			break;
		}
		payload[key_end] = separator;
		payload[i] = tail;
		if (!matched)
			return -1;
	}
	return 0;
}

void
agent_metadata_query_invalidate_locked(uint scope_id, int full)
{
	(void)scope_id;
	(void)full;
	agent_metadata_txn_work_charge(0);
}

int
agent_metadata_query_matches(uint scope, uint owner,
			     const struct agent_file_query *q,
			     const struct agent_file_meta *m)
{
	if (!m->used || !agent_object_scope_visible(scope, owner))
		return 0;
	for (int i = 0; i < AGENT_QUERY_FIELDS; i++) {
		const struct agent_query_alias *field = &agent_query_aliases[i];
		const char *want = (const char *)q + field->offset;
		const char *have = (const char *)m + field->offset +
				   AGENT_QUERY_META_DELTA;

		if (want[0] && strncmp(want, have, field->size) != 0)
			return 0;
	}
	if (q->summary_contains[0] &&
	    !agent_metadata_catalog_field_contains(m->summary,
					    q->summary_contains))
		return 0;
	return 1;
}

int
agent_metadata_query_has_filter(const struct agent_file_query *q)
{
	for (int i = 0; i < AGENT_QUERY_FIELDS; i++)
		if (*((const char *)q + agent_query_aliases[i].offset))
			return 1;
	return q->summary_contains[0] != 0;
}

int
agent_metadata_query_execute_locked(
	uint scope, const struct agent_file_query *q,
	struct agent_file_query_result *r,
	int slots[AGENT_FILE_QUERY_MAX_HITS], int allow_insert)
{
	struct agent_catalog_view view;
	struct agent_file_query key;
	int cursor = -1, index = 0, use_index = 0, bucket = -1;
	int rebuild_records = 0;
	int limit;
	uint64 start, generation, reason = 0;

	agent_metadata_txn_projection_require_idle();
	(void)allow_insert;
	limit = q->max_hits;
	if (limit <= 0 || limit > AGENT_FILE_QUERY_MAX_HITS)
		limit = AGENT_FILE_QUERY_MAX_HITS;
	memmove(&key, q, sizeof(key));
	key.max_hits = limit;
	start = agent_file_state_now();
	generation = agent_metadata_catalog_generation();
	if (key.flags & AGENT_FILE_QUERY_SCAN) {
		reason |= AGENT_FILE_QUERY_REASON_FORCED_SCAN;
	} else if (key.flags & AGENT_FILE_QUERY_USE_INDEX) {
		for (int i = AGENT_QUERY_INDEX_FIRST;
		     i < AGENT_QUERY_FIELDS; i++) {
			const struct agent_query_alias *alias =
				&agent_query_aliases[i];
			char *value = (char *)&key + alias->offset;

			if (!value[0])
				continue;
			index = AGENT_CATALOG_INDEX_STATUS +
				i - AGENT_QUERY_INDEX_FIRST;
			cursor = agent_metadata_catalog_index_seek(
				generation, index, value, -1, &bucket,
				&rebuild_records);
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STATUS_INDEX +
				i - AGENT_QUERY_INDEX_FIRST;
			reason |= AGENT_FILE_QUERY_REASON_STATUS_INDEX <<
				  (i - AGENT_QUERY_INDEX_FIRST);
			break;
		}
		if (!use_index)
			reason |= AGENT_FILE_QUERY_REASON_NO_INDEX_KEY;
	} else {
		reason |= AGENT_FILE_QUERY_REASON_INDEX_OFF;
	}
	if (use_index)
		r->index_bucket = bucket;
	for (int i = use_index ? cursor : 0;
	     i >= 0 && i < AGENT_FILE_META_MAX;
	     i = use_index ? agent_metadata_catalog_index_seek(
				     generation, index, 0, i, 0, 0) : i + 1) {
		agent_metadata_txn_work_charge(1);
		if (agent_metadata_catalog_borrow(generation, i, &view) <= 0)
			continue;
		if (!agent_object_scope_visible(scope, view.scope_id)) {
			view.meta = 0;
			continue;
		}
		r->scanned_records++;
		if (agent_metadata_query_matches(scope, view.scope_id, &key,
					       view.meta)) {
			r->total_hits++;
			if (r->returned < limit) {
				slots[r->returned] = i;
				agent_file_state_project_hit(
					&r->hits[r->returned++],
					view.meta, view.scope_id);
			} else {
				r->truncated = 1;
			}
		}
		view.meta = 0;
	}
	r->used_index = use_index;
	r->candidate_records = r->scanned_records;
	r->index_rebuild_records = rebuild_records;
	r->query_ticks = agent_file_state_now() - start;
	r->plan_reason = reason;
	r->fs_generation = agent_file_state_scope_generation(scope);
	return r->returned;
}
