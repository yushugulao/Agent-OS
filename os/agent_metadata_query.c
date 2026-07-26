#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_query.h"
#include "defs.h"

#define QUERY_CACHE_MAX 8

struct query_cache_entry {
	int valid;
	uint scope_id;
	uint64 generation;
	struct agent_file_query key;
	struct agent_file_query_result result;
	int slots[AGENT_FILE_QUERY_MAX_HITS];
};

static struct query_cache_entry query_cache[QUERY_CACHE_MAX];
static int query_cache_head;

void
agent_metadata_query_init(void)
{
	memset(query_cache, 0, sizeof(query_cache));
	query_cache_head = 0;
}

void
agent_metadata_query_invalidate_locked(uint scope_id, int full)
{
	agent_metadata_txn_work_charge(0);
	if (full) {
		memset(query_cache, 0, sizeof(query_cache));
		return;
	}
	for (int i = 0; i < QUERY_CACHE_MAX; i++)
		if (query_cache[i].valid && query_cache[i].scope_id == scope_id)
			memset(&query_cache[i], 0, sizeof(query_cache[i]));
}

static int
query_field_match(const char *want, const char *have)
{
	return want[0] == 0 ||
	       strncmp(want, have, AGENT_FILE_LOGICAL_SIZE) == 0;
}

int
agent_metadata_query_matches(uint scope, uint owner,
			     const struct agent_file_query *q,
			     const struct agent_file_meta *m)
{
	if (!m->used || !agent_object_scope_visible(scope, owner))
		return 0;
	if (!query_field_match(q->physical_name, m->physical_name))
		return 0;
	if (!query_field_match(q->logical_path, m->logical_path))
		return 0;
	if (!query_field_match(q->project, m->project))
		return 0;
	if (!query_field_match(q->workflow, m->workflow))
		return 0;
	if (!query_field_match(q->run_id, m->run_id))
		return 0;
	if (!query_field_match(q->stage, m->stage))
		return 0;
	if (!query_field_match(q->kind, m->kind))
		return 0;
	if (!query_field_match(q->status, m->status))
		return 0;
	if (q->summary_contains[0] &&
	    !agent_metadata_catalog_field_contains(m->summary,
					    q->summary_contains))
		return 0;
	return 1;
}

int
agent_metadata_query_has_filter(const struct agent_file_query *q)
{
	return q->physical_name[0] || q->logical_path[0] || q->project[0] ||
	       q->workflow[0] || q->run_id[0] || q->stage[0] || q->kind[0] ||
	       q->status[0] || q->summary_contains[0];
}

static int
query_cacheable(const struct agent_file_query *q)
{
	return (q->flags & AGENT_FILE_QUERY_SCAN) == 0;
}

static int
query_key_equal(const struct agent_file_query *a,
		const struct agent_file_query *b)
{
	return a->flags == b->flags && a->max_hits == b->max_hits &&
	       strncmp(a->physical_name, b->physical_name,
		       sizeof(a->physical_name)) == 0 &&
	       strncmp(a->logical_path, b->logical_path,
		       sizeof(a->logical_path)) == 0 &&
	       strncmp(a->project, b->project, sizeof(a->project)) == 0 &&
	       strncmp(a->workflow, b->workflow, sizeof(a->workflow)) == 0 &&
	       strncmp(a->run_id, b->run_id, sizeof(a->run_id)) == 0 &&
	       strncmp(a->stage, b->stage, sizeof(a->stage)) == 0 &&
	       strncmp(a->kind, b->kind, sizeof(a->kind)) == 0 &&
	       strncmp(a->status, b->status, sizeof(a->status)) == 0 &&
	       strncmp(a->summary_contains, b->summary_contains,
		       sizeof(a->summary_contains)) == 0;
}

static int
query_cache_lookup(uint scope, const struct agent_file_query *key,
		   struct agent_file_query_result *r, int *slots)
{
	struct query_cache_entry *e;

	for (int i = 0; i < QUERY_CACHE_MAX; i++) {
		e = &query_cache[i];
		if (!e->valid || e->scope_id != scope)
			continue;
		if (e->generation != agent_file_state_scope_generation(scope))
			continue;
		if (!query_key_equal(&e->key, key))
			continue;
		memmove(r, &e->result, sizeof(*r));
		memmove(slots, e->slots, sizeof(e->slots));
		r->plan_reason |= AGENT_FILE_QUERY_REASON_CACHE_HIT;
		r->query_ticks = 0;
		return 1;
	}
	return 0;
}

static void
query_cache_store(uint scope, const struct agent_file_query *key,
		  const struct agent_file_query_result *r, const int *slots,
		  int allow_insert)
{
	struct query_cache_entry *e;

	if (r->total_hits <= 0 || !allow_insert)
		return;
	e = &query_cache[query_cache_head % QUERY_CACHE_MAX];
	query_cache_head = (query_cache_head + 1) % QUERY_CACHE_MAX;
	memset(e, 0, sizeof(*e));
	e->valid = 1;
	e->scope_id = scope;
	e->generation = agent_file_state_scope_generation(scope);
	memmove(&e->key, key, sizeof(e->key));
	memmove(&e->result, r, sizeof(e->result));
	memmove(e->slots, slots, sizeof(e->slots));
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
	int limit;
	uint64 start, generation, reason = 0;

	agent_metadata_txn_projection_require_idle();
	limit = q->max_hits;
	if (limit <= 0 || limit > AGENT_FILE_QUERY_MAX_HITS)
		limit = AGENT_FILE_QUERY_MAX_HITS;
	memmove(&key, q, sizeof(key));
	key.max_hits = limit;
	if (query_cacheable(&key) && query_cache_lookup(scope, &key, r, slots))
		return r->returned;
	start = agent_file_state_now();
	generation = agent_metadata_catalog_generation();
	if (key.flags & AGENT_FILE_QUERY_SCAN) {
		reason |= AGENT_FILE_QUERY_REASON_FORCED_SCAN;
	} else if (key.flags & AGENT_FILE_QUERY_USE_INDEX) {
		if (key.status[0]) {
			index = AGENT_CATALOG_INDEX_STATUS;
			cursor = agent_metadata_catalog_index_seek(
				generation, index, key.status, -1, &bucket);
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STATUS_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_STATUS_INDEX;
		} else if (key.stage[0]) {
			index = AGENT_CATALOG_INDEX_STAGE;
			cursor = agent_metadata_catalog_index_seek(
				generation, index, key.stage, -1, &bucket);
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_STAGE_INDEX;
		} else if (key.kind[0]) {
			index = AGENT_CATALOG_INDEX_KIND;
			cursor = agent_metadata_catalog_index_seek(
				generation, index, key.kind, -1, &bucket);
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_KIND_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_KIND_INDEX;
		} else {
			reason |= AGENT_FILE_QUERY_REASON_NO_INDEX_KEY;
		}
	} else {
		reason |= AGENT_FILE_QUERY_REASON_INDEX_OFF;
	}
	if (use_index)
		r->index_bucket = bucket;
	for (int i = use_index ? cursor : 0;
	     i >= 0 && i < AGENT_FILE_META_MAX;
	     i = use_index ? agent_metadata_catalog_index_seek(
				     generation, index, 0, i, 0) : i + 1) {
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
	r->query_ticks = agent_file_state_now() - start;
	r->plan_reason = reason;
	r->fs_generation = agent_file_state_scope_generation(scope);
	if (query_cacheable(&key))
		query_cache_store(scope, &key, r, slots, allow_insert);
	return r->returned;
}
