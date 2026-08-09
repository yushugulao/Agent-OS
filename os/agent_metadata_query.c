#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_query.h"
#include "defs.h"
#include "kernel_work.h"

struct agent_query_alias {
	char name[10];
	ushort offset, size;
};

#define AGENT_QUERY_ALIAS(name_value, field) \
	{ name_value, (ushort)__builtin_offsetof(struct agent_file_query, field), \
	  (ushort)sizeof(((struct agent_file_query *)0)->field) }
#define AGENT_QUERY_FIELDS 8
#define AGENT_QUERY_INDEX_FIRST 5
#define AGENT_QUERY_READ_GRANULE 128
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

struct agent_query_plan {
	const char *index_key;
	int limit;
	int force_scan;
	int index;
	int use_index;
	uint64 reason;
};

static void
agent_query_plan_build(const struct agent_file_query *q,
		       struct agent_file_query_result *r,
		       struct agent_query_plan *plan)
{
	memset(plan, 0, sizeof(*plan));
	plan->limit = q->max_hits;
	if (plan->limit <= 0 || plan->limit > AGENT_FILE_QUERY_MAX_HITS)
		plan->limit = AGENT_FILE_QUERY_MAX_HITS;
	r->plan = AGENT_FILE_QUERY_PLAN_SCAN;
	r->index_bucket = -1;
	if (q->flags & AGENT_FILE_QUERY_SCAN) {
		plan->force_scan = 1;
		plan->reason |= AGENT_FILE_QUERY_REASON_FORCED_SCAN;
		return;
	}
	if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) == 0) {
		plan->reason |= AGENT_FILE_QUERY_REASON_INDEX_OFF;
		return;
	}
	for (int i = AGENT_QUERY_INDEX_FIRST; i < AGENT_QUERY_FIELDS; i++) {
		const struct agent_query_alias *alias = &agent_query_aliases[i];
		const char *value = (const char *)q + alias->offset;

		if (!value[0])
			continue;
		plan->index = AGENT_CATALOG_INDEX_STATUS +
			i - AGENT_QUERY_INDEX_FIRST;
		plan->use_index = 1;
		plan->index_key = value;
		r->plan = AGENT_FILE_QUERY_PLAN_STATUS_INDEX +
			i - AGENT_QUERY_INDEX_FIRST;
		plan->reason |= AGENT_FILE_QUERY_REASON_STATUS_INDEX <<
			(i - AGENT_QUERY_INDEX_FIRST);
		return;
	}
	plan->reason |= AGENT_FILE_QUERY_REASON_NO_INDEX_KEY;
}

static void agent_query_result_reset(struct agent_file_query_result *r)
{
	memset(r, 0, sizeof(*r));
}

int
agent_metadata_query_execute_locked(
	uint scope, const struct agent_file_query *q,
	struct agent_file_query_result *r, int allow_insert)
{
	struct agent_catalog_view view;
	struct agent_query_plan plan;
	int cursor = -1, bucket = -1;
	int rebuild_records = 0;
	uint64 start, generation;

	agent_metadata_txn_projection_require_idle();
	(void)allow_insert;
	agent_query_plan_build(q, r, &plan);
	start = agent_file_state_now();
	generation = agent_metadata_catalog_generation();
	if (plan.use_index) {
		cursor = agent_metadata_catalog_index_seek(
			generation, plan.index, (char *)plan.index_key, -1,
			&bucket, &rebuild_records);
		r->index_bucket = bucket;
	}
	if (plan.force_scan)
		cursor = 0;
	else if (!plan.use_index)
		cursor = agent_metadata_catalog_live_seek(generation, -1);
	for (int i = cursor;
	     i >= 0 && i < AGENT_FILE_META_MAX;
	     i = plan.use_index ? agent_metadata_catalog_index_seek(
				     generation, plan.index, 0, i, 0, 0) :
		 plan.force_scan ? i + 1 :
			     agent_metadata_catalog_live_seek(generation, i)) {
		agent_metadata_txn_work_charge(1);
		r->scanned_records++;
		if (agent_metadata_catalog_borrow(generation, i, &view) <= 0)
			continue;
		if (!agent_object_scope_visible(scope, view.scope_id)) {
			view.meta = 0;
			continue;
		}
		r->candidate_records++;
		if (agent_metadata_query_matches(scope, view.scope_id, q,
					       view.meta)) {
			r->total_hits++;
			if (r->returned < plan.limit) {
				agent_file_state_project_hit(
					&r->hits[r->returned++],
					view.meta, view.scope_id);
			} else {
				r->truncated = 1;
			}
		}
		view.meta = 0;
	}
	r->used_index = plan.use_index;
	r->index_rebuild_records = rebuild_records;
	r->query_ticks = agent_file_state_now() - start;
	r->plan_reason = plan.reason;
	r->fs_generation = agent_file_state_scope_generation(scope);
	return r->returned;
}

int
agent_metadata_query_execute_snapshot(
	uint scope, const struct agent_file_query *q,
	struct agent_file_query_result *r)
{
	struct agent_catalog_read_snapshot snapshot;
	struct agent_file_meta meta;
	struct agent_query_plan plan;
	uint owner;
	uint work = 0;
	int bucket = -1;
	int copied;
	uint64 start;

	agent_query_result_reset(r);
	agent_query_plan_build(q, r, &plan);
	start = agent_file_state_now();
	copied = agent_metadata_catalog_read_begin(
		scope, plan.index, plan.index_key, plan.force_scan,
		&snapshot, &bucket);
	if (copied < 0)
		return copied;
	if (plan.use_index)
		r->index_bucket = bucket;
	for (int slot = agent_metadata_catalog_read_next(&snapshot, -1);
	     slot >= 0;
	     slot = agent_metadata_catalog_read_next(&snapshot, slot)) {
		r->scanned_records++;
		copied = agent_metadata_catalog_read_copy(
			&snapshot, slot, &meta, &owner);
		if (copied < 0)
			return copied;
		if (copied > 0 && agent_object_scope_visible(scope, owner)) {
			r->candidate_records++;
			if (agent_metadata_query_matches(scope, owner, q, &meta)) {
				r->total_hits++;
				if (r->returned < plan.limit) {
					agent_file_state_project_hit(
						&r->hits[r->returned++],
						&meta, owner);
				} else {
					r->truncated = 1;
				}
			}
		}
		if (++work == AGENT_QUERY_READ_GRANULE) {
			work = 0;
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
		}
	}
	if (!agent_metadata_catalog_read_end(&snapshot))
		return AGENT_CATALOG_STALE;
	r->used_index = plan.use_index;
	r->index_rebuild_records = 0;
	r->query_ticks = agent_file_state_now() - start;
	r->plan_reason = plan.reason;
	r->fs_generation = snapshot.fs_generation;
	return r->returned;
}
