#include "agent.h"
#include "agent_context.h"
#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_query.h"
#include "agent_metadata_scan.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "trap.h"
#include "vfs_security.h"

/*
 * Authoritative Agent object catalog. Live object tables, dependency graph,
 * and their transaction coordination share this owner. Query, scan,
 * incarnation-bound state, and durable storage have dedicated modules.
 */
#define AGENT_ACTION_HISTORY_MAX 32
#define AGENT_DEPENDENCY_MAX 64
#define AGENT_DEPENDENCY_SCOPE_LIMIT 16
#define AGENT_ACTION_SCOPE_LIMIT 8
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_DEPENDENCY_SCOPE_LIMIT <=
	       AGENT_DEPENDENCY_MAX,
	       "dependency table must reserve every workflow partition");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_ACTION_SCOPE_LIMIT <=
	       AGENT_ACTION_HISTORY_MAX,
	       "action table must reserve every workflow partition");

struct agent_action_history_entry {
	int tool_id;
	uint scope_id;
	uint64 sequence;
	uint64 request_id;
	char project[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
};

struct agent_dependency_entry {
	int used;
	uint scope_id;
	uint64 flags;
	char namespace[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char source[AGENT_FILE_FIELD_SIZE];
	char target[AGENT_FILE_FIELD_SIZE];
	char relation[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
};

static struct agent_action_history_entry
	agent_action_history[AGENT_ACTION_HISTORY_MAX];
/* Explicit user edges only; file dependency masks are resolved on demand. */
static struct agent_dependency_entry agent_dependencies[AGENT_DEPENDENCY_MAX];
static int agent_action_history_count;
static uint64 agent_action_next_sequence;
static uint64 agent_dependency_generation;

static int agent_query_from_payload(struct agent_file_query *q, char *payload);
static void agent_text_append(char *dst, int n, const char *src);
static void agent_action_history_clear_scope(uint scope_id);
static int agent_dependency_for_label(uint scope_id, char *label,
				      char *namespace, char *run_id,
				      uint64 *mask);
static uint64 agent_label_bit(const char *label);
static void agent_file_catalog_sync(const struct agent_catalog_delta *);
static int agent_file_store_complete(struct agent_metadata_store_commit *, int);

static void agent_result_text(struct agent_result *res, const char *text) {
	safestrcpy(res->result, text, sizeof(res->result));
}
void
agent_metadata_objects_init(void)
{
	agent_file_state_init();
	agent_metadata_query_init();
	agent_metadata_scan_init();
	agent_action_history_count = 0;
	agent_action_next_sequence = 1;
	memset(agent_action_history, 0, sizeof(agent_action_history));
	agent_dependency_generation = 0;
	memset(agent_dependencies, 0, sizeof(agent_dependencies));
	agent_metadata_catalog_init();
	agent_metadata_store_init();
}

void
agent_metadata_storage_init(void)
{
	struct agent_metadata_store_commit commit;
	int result;

	if (!agent_metadata_txn_lock(1))
		panic("Agent metadata storage transaction invariant");
	result = agent_metadata_store_load(&commit);
	result = agent_file_store_complete(&commit, result);
	if (result < 0)
		agent_metadata_store_fail_closed_at_boot();
}

static int agent_file_read_slot(int slot, struct agent_catalog_view *view) {
	return agent_metadata_catalog_borrow(0, slot, view);
}

static void
agent_file_catalog_sync(const struct agent_catalog_delta *delta)
{
	if (delta == 0 ||
	    (!delta->full_reset && delta->scope_id == VFS_SCOPE_NONE))
		return;
	agent_dependency_generation++;
	agent_metadata_query_invalidate_locked(delta->scope_id,
					       delta->full_reset);
	agent_metadata_scan_catalog_sync(delta);
	agent_metadata_txn_projection_ack();
}

static int
agent_file_store_complete(struct agent_metadata_store_commit *commit,
			  int result)
{
	agent_file_catalog_sync(&commit->delta);
	result = agent_metadata_store_finish(commit, result);
	agent_metadata_txn_unlock();
	return result;
}

static int
agent_file_store_load(void)
{
	struct agent_metadata_store_commit commit;
	int result;

	if (!agent_metadata_txn_lock(1))
		return -1;
	result = agent_metadata_store_load(&commit);
	return agent_file_store_complete(&commit, result);
}

static int
agent_file_store_reload(uint scope_id)
{
	struct agent_metadata_store_commit commit;
	int result;

	if (!agent_metadata_txn_lock(1))
		return -1;
	result = agent_metadata_store_reload(scope_id, &commit);
	return agent_file_store_complete(&commit, result);
}

static int agent_text_empty(char *s) {
	return s == 0 || s[0] == 0;
}

static void agent_direct_effect_audit(struct proc *p, int tool_id, int status,
				      char *text, uint64 value0,
				      uint64 value1, uint64 value2,
				      uint64 flags, int authority_effect)
{
	if (p == 0 || !p->is_agent)
		return;
	agent_observe_record_effect(p, tool_id, status, text, value0, value1,
				    value2, flags, authority_effect);
}

static void agent_file_restore_slot(int slot,
				    struct agent_file_meta *previous,
				    uint previous_scope, int had_previous)
{
	agent_metadata_catalog_restore(slot, previous, previous_scope,
				       had_previous);
	agent_dependency_generation++;
}

void
agent_metadata_background_maintain(void)
{
	uint64 now;
	uint changes;
	int plan;
	int load_ok = 1;

	vfs_scope_reap_pending();
	/* A scan storm must not starve an already due durable checkpoint. */
	agent_metadata_store_background_maintain();
	if (agent_metadata_store_take_reconcile_request())
		agent_file_request_scan();
	now = agent_file_state_now();
	plan = agent_metadata_scan_plan(now);
	if (plan == AGENT_METADATA_SCAN_IDLE)
		return;
	if (!bio_background_begin(FS_OWNER_SYSTEM))
		return;
	if (!agent_metadata_txn_try_external())
		goto out_io;
	now = agent_file_state_now();
	plan = agent_metadata_scan_plan(now);
	if (plan == AGENT_METADATA_SCAN_IDLE)
		goto out_txn;
	if (plan == AGENT_METADATA_SCAN_START)
		load_ok = agent_file_store_load() >= 0;
	changes = agent_metadata_scan_step(now, plan, load_ok);
	if (changes)
		agent_metadata_note_catalog_changes(changes);
out_txn:
	agent_metadata_txn_unlock();
out_io:
	bio_background_end();
}

int agent_metadata_inode_trackable(struct inode *ip)
{
	if (ip == 0)
		return 0;
	ivalid(ip);
	return vfs_inode_label_valid(ip) &&
	       ip->vfs_policy == VFS_POLICY_WORKFLOW && ip->type == T_FILE &&
	       agent_object_scope_valid(ip->vfs_scope_id);
}

static int agent_file_path_autopersist(uint scope_id, char *path,
				       struct agent_file_meta *binding)
{
	struct inode *ip;
	int eligible;

	if (!agent_scope_valid(scope_id) || path == 0 || path[0] == 0 ||
	    agent_file_is_meta_store_name(path))
		return 0;
	ip = namei_scope(path, VFS_POLICY_WORKFLOW, scope_id);
	if (ip == 0)
		return 0;
	eligible = agent_metadata_inode_trackable(ip) &&
		   ip->vfs_scope_id == scope_id && vfs_scope_active(scope_id) &&
		   exec_policy_inode_mutable(ip);
	if (eligible && binding) {
		binding->dev = ip->dev;
		binding->inum = ip->inum;
		binding->incarnation = ip->vfs_incarnation;
		binding->size = ip->size;
	}
	iput(ip);
	return eligible;
}

/*
 * Metadata mutations declare the fields they changed. Secondary indexes are
 * rebuilt only for affected fields. Legacy dependency masks stay canonical in
 * the file records and are interpreted by consumers, so topology changes only
 * advance a generation and never materialize a global derived graph.
 */
void agent_metadata_note_catalog_changes(uint changes)
{
	if (changes & (AGENT_FILE_CHANGE_STAGE |
		       AGENT_FILE_CHANGE_SCOPE_KEYS |
		       AGENT_FILE_CHANGE_DEPENDENCY |
		       AGENT_FILE_CHANGE_MEMBERSHIP))
		agent_dependency_generation++;
}

static int agent_file_install_empty_store(void)
{
	struct agent_metadata_store_commit commit;
	int result;

	if (!agent_metadata_txn_lock(1))
		return -1;
	result = agent_metadata_store_install_empty(&commit);
	result = agent_file_store_complete(&commit, result);
	if (result < 0)
		return -1;
	agent_file_request_scan();
	return 0;
}

int agent_scope_reclaim_begin(uint scope_id, uint64 *metadata_target)
{
	int result;
	int changed;
	int dependency_changed = 0;
	int metadata_available;

	if (!agent_scope_valid(scope_id) || metadata_target == 0)
		return -1;
	*metadata_target = 0;
	if (!agent_metadata_txn_try_external())
		return -1;
	metadata_available = agent_metadata_store_available();
	if (metadata_available && agent_file_store_load() < 0) {
		agent_file_request_scan();
		result = -1;
		goto out_txn;
	}
	/* A corrupt global bank blocks metadata APIs, not VFS-labelled cleanup. */
	changed = metadata_available ?
		agent_metadata_store_shadow_has_scope(scope_id) : 0;
	if (metadata_available &&
	    agent_metadata_catalog_reclaim_scope(scope_id) > 0) {
		changed = 1;
	}
	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++)
		if (agent_dependencies[i].used &&
		    agent_dependencies[i].scope_id == scope_id) {
			memset(&agent_dependencies[i], 0,
			       sizeof(agent_dependencies[i]));
			dependency_changed = 1;
		}
	if (dependency_changed)
		agent_dependency_generation++;
	agent_action_history_clear_scope(scope_id);
	agent_metadata_query_invalidate_locked(scope_id, 0);
	agent_observe_scope_reclaim(scope_id);
	agent_file_state_scope_reclaim(scope_id);
	if (metadata_available)
		agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_STATUS |
				    AGENT_FILE_CHANGE_STAGE |
				    AGENT_FILE_CHANGE_KIND);
	if (changed) {
		*metadata_target = agent_metadata_store_mark_dirty(scope_id);
		if (*metadata_target == 0) {
			agent_file_request_scan();
			result = -1;
			goto out_txn;
		}
		// A quiesced scope cannot create another write burst. Do not hold its
		// identity and I/O owner for the interactive coalescing window.
		agent_metadata_store_expedite(scope_id);
	}
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int agent_scope_reclaim_metadata_done(uint scope_id, uint64 metadata_target)
{
	return agent_metadata_store_scope_target_done(scope_id, metadata_target);
}

static int __attribute__((noinline)) agent_file_query_internal(uint scope_id,
				     struct agent_file_query *q,
				     struct agent_file_query_result *r,
				     int *hit_slots)
{
	int result;

	memset(r, 0, sizeof(*r));
	for (int i = 0; i < AGENT_FILE_QUERY_MAX_HITS; i++)
		hit_slots[i] = -1;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_scope_valid(scope_id)) {
		result = AGENT_STATUS_DENIED;
		goto out_txn;
	}
	r->plan = AGENT_FILE_QUERY_PLAN_SCAN;
	r->index_bucket = -1;
	if (agent_file_store_load() < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out_txn;
	}
	result = agent_metadata_query_execute_locked(
		scope_id, q, r, hit_slots, agent_metadata_scan_query_stable());
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

static uint64 agent_label_bit(const char *label)
{
	uint64 hash = 1469598103934665603ULL;
	int bit;

	if (label == 0 || label[0] == 0)
		return 0;
	for (int i = 0; label[i] && i < AGENT_FILE_FIELD_SIZE; i++) {
		hash ^= (unsigned char)label[i];
		hash *= 1099511628211ULL;
	}
	bit = hash % 60;
	return 1ULL << bit;
}

static int agent_key_is(char *key, char *want)
{
	return strncmp(key, want, AGENT_FILE_FIELD_SIZE) == 0;
}

static int agent_dependency_scope_count(uint scope_id)
{
	int count = 0;

	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		if (agent_dependencies[i].used &&
		    agent_dependencies[i].scope_id == scope_id)
			count++;
		agent_metadata_txn_work_charge(1);
	}
	return count;
}

static int agent_dependency_update_from_payload(uint scope_id, char *payload,
						struct agent_result *res)
{
	struct agent_dependency_entry dep;
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_SUMMARY_SIZE];
	int free_slot = -1;
	int slot = -1;
	int i = 0;
	int k;
	int v;

	memset(&dep, 0, sizeof(dep));
	if (!agent_scope_valid(scope_id))
		return AGENT_STATUS_DENIED;
	dep.used = 1;
	dep.scope_id = scope_id;
	dep.flags = AGENT_DEPENDENCY_F_USER;
	safestrcpy(dep.relation, "depends_on", sizeof(dep.relation));

	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		if (!payload[i])
			break;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' &&
		       payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':')
			return AGENT_STATUS_BAD_PARAM;
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		if (agent_key_is(key, "source") || agent_key_is(key, "from") ||
		    agent_key_is(key, "label"))
			safestrcpy(dep.source, val, sizeof(dep.source));
		else if (agent_key_is(key, "target") || agent_key_is(key, "to"))
			safestrcpy(dep.target, val, sizeof(dep.target));
		else if (agent_key_is(key, "namespace") ||
			 agent_key_is(key, "project"))
			safestrcpy(dep.namespace, val, sizeof(dep.namespace));
		else if (agent_key_is(key, "run_id") || agent_key_is(key, "run"))
			safestrcpy(dep.run_id, val, sizeof(dep.run_id));
		else if (agent_key_is(key, "relation"))
			safestrcpy(dep.relation, val, sizeof(dep.relation));
		else if (agent_key_is(key, "summary"))
			safestrcpy(dep.summary, val, sizeof(dep.summary));
		else
			return AGENT_STATUS_BAD_PARAM;
	}

	if (!dep.source[0] || !dep.target[0])
		return AGENT_STATUS_BAD_PARAM;
	if (!dep.summary[0])
		safestrcpy(dep.summary, dep.target, sizeof(dep.summary));

	for (int d = 0; d < AGENT_DEPENDENCY_MAX; d++) {
		if (!agent_dependencies[d].used) {
			if (free_slot < 0)
				free_slot = d;
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (agent_dependencies[d].scope_id == scope_id &&
		    strncmp(agent_dependencies[d].namespace, dep.namespace,
			    sizeof(dep.namespace)) == 0 &&
		    strncmp(agent_dependencies[d].run_id, dep.run_id,
			    sizeof(dep.run_id)) == 0 &&
		    strncmp(agent_dependencies[d].source, dep.source,
			    sizeof(dep.source)) == 0 &&
		    strncmp(agent_dependencies[d].target, dep.target,
			    sizeof(dep.target)) == 0) {
			slot = d;
			agent_metadata_txn_work_charge(1);
			break;
		}
		agent_metadata_txn_work_charge(1);
	}
	if (slot < 0)
		slot = free_slot;
	if (slot >= 0 && !agent_dependencies[slot].used &&
	    agent_dependency_scope_count(scope_id) >=
		    AGENT_DEPENDENCY_SCOPE_LIMIT)
		return AGENT_STATUS_NO_SPACE;
	if (slot < 0)
		return AGENT_STATUS_NO_SPACE;

	memmove(&agent_dependencies[slot], &dep, sizeof(dep));
	agent_dependency_generation++;
	res->value0 = agent_dependency_generation;
	res->value1 = agent_label_bit(dep.source);
	res->value2 = agent_label_bit(dep.target);
	agent_result_text(res, "dependency_updated");
	return AGENT_STATUS_OK;
}

static int agent_mask_count(uint64 mask)
{
	int count = 0;

	while (mask) {
		count += mask & 1;
		mask >>= 1;
	}
	return count;
}

static int agent_file_hit_slot_valid(uint64 generation, uint scope_id,
				     int slot, struct agent_file_hit *hit)
{
	struct agent_catalog_view view;
	int valid;

	if (hit == 0 || slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    agent_metadata_catalog_borrow(generation, slot, &view) <= 0)
		return 0;
	valid = agent_object_scope_visible(scope_id, view.scope_id) &&
		view.meta->dev == hit->dev && view.meta->inum == hit->inum &&
		view.meta->incarnation == hit->incarnation &&
		view.meta->fid == hit->fid;
	view.meta = 0;
	return valid;
}

static int agent_file_prefetch_count_stage(uint64 generation, int source_slot,
					   char *stage)
{
	struct agent_catalog_view source;
	struct agent_catalog_view entry;
	struct agent_file_meta source_meta;
	uint source_scope;
	int count = 0;
	int slot;

	if (!stage[0] || agent_metadata_catalog_borrow(
				 generation, source_slot, &source) <= 0)
		return 0;
	source_meta = *source.meta;
	source_scope = source.scope_id;
	source.meta = 0;
	for (slot = agent_metadata_catalog_index_seek(
		     generation, AGENT_CATALOG_INDEX_STAGE, stage, -1, 0);
	     slot >= 0;
	     slot = agent_metadata_catalog_index_seek(
		     generation, AGENT_CATALOG_INDEX_STAGE, 0, slot, 0)) {
		agent_metadata_txn_work_charge(1);
		if (agent_metadata_catalog_borrow(generation, slot, &entry) > 0 &&
		    source_scope == entry.scope_id &&
		    strncmp(entry.meta->stage, stage,
			    sizeof(entry.meta->stage)) == 0 &&
		    (!source_meta.project[0] ||
		     strncmp(entry.meta->project, source_meta.project,
			     sizeof(entry.meta->project)) == 0) &&
		    (!source_meta.workflow[0] ||
		     strncmp(entry.meta->workflow, source_meta.workflow,
			     sizeof(entry.meta->workflow)) == 0) &&
		    (!source_meta.run_id[0] ||
		     strncmp(entry.meta->run_id, source_meta.run_id,
			     sizeof(entry.meta->run_id)) == 0))
			count++;
		entry.meta = 0;
	}
	return count;
}

static void __attribute__((noinline))
agent_file_prefetch_store(struct proc *p, uint64 catalog_generation,
			  int source_slot, int target_slot,
				      uint64 source_sequence, uint64 reason,
				      int source_pid, uint64 span_id,
				      uint64 span_owner, int candidates)
{
	struct agent_catalog_view source;
	struct agent_catalog_view target;
	struct agent_file_meta target_meta;
	struct agent_file_prefetch_hint *hint;
	uint source_scope;
	int source_fid;
	int target_fid;
	int slot;
	int visible;

	if (!p || !p->is_agent ||
	    agent_metadata_catalog_borrow(catalog_generation, source_slot,
					  &source) <= 0 ||
	    agent_metadata_catalog_borrow(catalog_generation, target_slot,
					  &target) <= 0 ||
	    agent_identity_proc_scope(p) != source.scope_id ||
	    source.scope_id != target.scope_id)
		return;
	source_scope = source.scope_id;
	source_fid = source.meta->fid;
	target_fid = target.meta->fid;
	target_meta = *target.meta;
	source.meta = 0;
	target.meta = 0;
	if (span_id == 0) {
		span_id = p->agent_current_span_id;
		span_owner = p->agent_current_span_owner;
	}
	if (span_id == 0 || span_owner == 0)
		return;
	for (int i = 0; i < p->agent_prefetch_count; i++) {
		slot = (p->agent_prefetch_head +
			AGENT_FILE_PREFETCH_MAX_HINTS -
			p->agent_prefetch_count + i) %
		       AGENT_FILE_PREFETCH_MAX_HINTS;
		agent_metadata_txn_work_charge(1);
		if (p->agent_prefetch_hints[slot].fid == target_fid)
			goto fill;
	}
	slot = p->agent_prefetch_head % AGENT_FILE_PREFETCH_MAX_HINTS;
	p->agent_prefetch_head =
		(p->agent_prefetch_head + 1) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	if (p->agent_prefetch_count < AGENT_FILE_PREFETCH_MAX_HINTS)
		p->agent_prefetch_count++;

fill:
	hint = &p->agent_prefetch_hints[slot];
	memset(hint, 0, sizeof(*hint));
	hint->sequence = ++p->agent_prefetch_sequence;
	hint->source_sequence = source_sequence;
	hint->span_id = span_id;
	p->agent_prefetch_span_owner[slot] = span_owner;
	hint->reason = reason;
	hint->tick = agent_file_state_now();
	hint->fs_generation = agent_file_state_scope_generation(
		agent_identity_proc_scope(p));
	hint->fid = target_meta.fid;
	hint->source_fid = source_fid;
	hint->source_pid = source_pid ? source_pid : p->pid;
	hint->target_pid = p->pid;
	hint->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
	hint->candidate_records = candidates;
	hint->total_hits = candidates;
	hint->score = 1000 + candidates;
	if (strncmp(target_meta.status, "pending",
		    AGENT_FILE_FIELD_SIZE) == 0) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_PENDING;
		hint->score += 100;
	}
	if (target_meta.stage[0]) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
		hint->score += 50;
	}
	visible = p->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	hint->score += visible;
	agent_file_state_project_hit(&hint->hit, &target_meta, source_scope);
	agent_observe_record_prefetch(
		p, hint, span_owner, hint->hit.stage,
		(reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) == 0);
	/*
	 * Audit allocation and observe fan-out intentionally remain atomic: they
	 * are shared outside the metadata gate. Charge their fixed upper bound
	 * only after publication, so the next hint cannot amplify that work
	 * without crossing a scheduler checkpoint.
	 */
	agent_metadata_txn_work_charge(
		(reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) ?
			2U * NPROC :
			2U * AGENT_AUDIT_MAX_RECORDS + 5U * NPROC);
}

static void agent_file_prefetch_update(struct proc *p,
				       struct agent_file_query *q,
				       struct agent_file_query_result *r,
				       int *hit_slots,
				       uint64 source_sequence)
{
	uint64 fallback_targets[AGENT_FILE_QUERY_MAX_HITS]
			       [(AGENT_FILE_META_MAX + 63) / 64];
	uint64 selected_targets[(AGENT_FILE_META_MAX + 63) / 64];
	uint64 explicit_target_masks[AGENT_FILE_QUERY_MAX_HITS];
	int dependency_slots[AGENT_FILE_QUERY_MAX_HITS]
			    [AGENT_DEPENDENCY_SCOPE_LIMIT];
	int dependency_counts[AGENT_FILE_QUERY_MAX_HITS];
	int source_slots[AGENT_FILE_QUERY_MAX_HITS];
	int selected_source_slots[AGENT_FILE_PREFETCH_MAX_HINTS];
	int selected_target_slots[AGENT_FILE_PREFETCH_MAX_HINTS];
	struct agent_catalog_view source_view;
	struct agent_catalog_view target_view;
	const struct agent_file_meta *source;
	const struct agent_file_meta *target;
	uint64 target_bit;
	uint64 reason;
	uint64 catalog_generation;
	uint scope_id;
	uint target_work;
	int source_count;
	int selected_count = 0;
	int explicit_found[AGENT_FILE_QUERY_MAX_HITS];

	if (!p || !p->is_agent || !q || !r || !hit_slots ||
	    r->returned <= 0)
		return;
	if (!agent_metadata_txn_lock(1))
		return;
	scope_id = agent_identity_proc_scope(p);
	catalog_generation = agent_metadata_catalog_generation();
	source_count = r->returned;
	if (source_count > AGENT_FILE_QUERY_MAX_HITS)
		source_count = AGENT_FILE_QUERY_MAX_HITS;
	memset(fallback_targets, 0, sizeof(fallback_targets));
	memset(selected_targets, 0, sizeof(selected_targets));
	memset(explicit_target_masks, 0, sizeof(explicit_target_masks));
	memset(dependency_slots, 0, sizeof(dependency_slots));
	memset(dependency_counts, 0, sizeof(dependency_counts));
	memset(source_slots, -1, sizeof(source_slots));
	memset(explicit_found, 0, sizeof(explicit_found));

	/*
	 * Query execution records the exact slots behind each returned hit,
	 * including cache hits. Validate those O(1) identities here instead of
	 * rescanning the global file table once per hit.
	 */
	for (int h = 0; h < source_count; h++)
		if (agent_file_hit_slot_valid(catalog_generation, scope_id,
					     hit_slots[h], &r->hits[h]))
			source_slots[h] = hit_slots[h];

	/*
	 * Build a fixed, scope-quota-bounded selector set once. The hash mask is
	 * only a prefilter; exact stage/namespace/run comparisons below preserve
	 * dependency semantics even when label hashes collide.
	 */
	for (int d = 0; d < AGENT_DEPENDENCY_MAX; d++) {
		agent_metadata_txn_work_charge(1);
		if (!agent_dependencies[d].used ||
		    agent_dependencies[d].scope_id != scope_id)
			goto next_dependency;
		for (int h = 0; h < source_count; h++) {
			if (source_slots[h] < 0)
				continue;
			if (agent_metadata_catalog_borrow(
				    catalog_generation, source_slots[h],
				    &source_view) <= 0)
				continue;
			if (source_view.scope_id != scope_id)
				goto next_source_dependency;
			source = source_view.meta;
			if (strncmp(agent_dependencies[d].source, source->stage,
				    sizeof(agent_dependencies[d].source)) != 0)
				goto next_source_dependency;
			if (agent_dependencies[d].namespace[0] &&
			    strncmp(agent_dependencies[d].namespace,
				    source->project,
				    sizeof(agent_dependencies[d].namespace)) != 0)
				goto next_source_dependency;
			if (agent_dependencies[d].run_id[0] &&
			    strncmp(agent_dependencies[d].run_id,
				    source->run_id,
				    sizeof(agent_dependencies[d].run_id)) != 0)
				goto next_source_dependency;
			if (dependency_counts[h] >=
			    AGENT_DEPENDENCY_SCOPE_LIMIT)
				goto next_source_dependency;
			dependency_slots[h][dependency_counts[h]++] = d;
			explicit_target_masks[h] |=
				agent_label_bit(agent_dependencies[d].target);
next_source_dependency:
			source_view.meta = 0;
		}
next_dependency:
		;
	}

	/*
	 * Scan the file table exactly once. Explicit edges are selected
	 * immediately and fallback candidates are kept in per-source bitmaps
	 * until we know that no explicit target exists. A target slot can be
	 * published only once and side effects are capped by the hint ring.
	 */
	target_work = source_count + 1;
	for (int h = 0; h < source_count; h++)
		target_work += dependency_counts[h];
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(target_work);
		if (agent_metadata_catalog_borrow(
			    catalog_generation, i, &target_view) <= 0 ||
		    target_view.scope_id != scope_id ||
		    target_view.meta->stage[0] == 0)
			goto next_target;
		target = target_view.meta;
		target_bit = agent_label_bit(target->stage);
		for (int h = 0; h < source_count; h++) {
			int explicit_match = 0;
			int source_slot = source_slots[h];

			if (source_slot < 0 || source_slot == i)
				goto next_source;
			if (agent_metadata_catalog_borrow(
				    catalog_generation, source_slot,
				    &source_view) <= 0 ||
			    source_view.scope_id != scope_id)
				goto next_source;
			source = source_view.meta;
			if ((explicit_target_masks[h] & target_bit) != 0)
				for (int k = 0; k < dependency_counts[h]; k++) {
					struct agent_dependency_entry *dep =
						&agent_dependencies[
							dependency_slots[h][k]];

					if (strncmp(target->stage, dep->target,
						    sizeof(target->stage)) != 0)
						continue;
					if (dep->namespace[0] &&
					    strncmp(target->project,
						    dep->namespace,
						    sizeof(target->project)) != 0)
						continue;
					if (dep->run_id[0] &&
					    strncmp(target->run_id, dep->run_id,
						    sizeof(target->run_id)) != 0)
						continue;
					if (source->workflow[0] &&
					    strncmp(target->workflow,
						    source->workflow,
						    sizeof(target->workflow)) != 0)
						continue;
					explicit_match = 1;
					break;
				}
			if (explicit_match) {
				explicit_found[h] = 1;
				if (selected_count <
					    AGENT_FILE_PREFETCH_MAX_HINTS &&
				    (selected_targets[i / 64] &
				     (1ULL << (i % 64))) == 0) {
					selected_targets[i / 64] |=
						1ULL << (i % 64);
					selected_source_slots[selected_count] =
						source_slot;
					selected_target_slots[selected_count++] = i;
				}
			}
			if (target_bit != 0 &&
			    (source->dependency_mask & target_bit) != 0 &&
			    (!source->project[0] ||
			     strncmp(source->project, target->project,
				     sizeof(source->project)) == 0) &&
			    (!source->workflow[0] ||
			     strncmp(source->workflow, target->workflow,
				     sizeof(source->workflow)) == 0) &&
			    (!source->run_id[0] ||
			     strncmp(source->run_id, target->run_id,
				     sizeof(source->run_id)) == 0))
				fallback_targets[h][i / 64] |=
					1ULL << (i % 64);
next_source:
			source_view.meta = 0;
		}
next_target:
		target_view.meta = 0;
	}

	for (int h = 0; h < source_count &&
				selected_count < AGENT_FILE_PREFETCH_MAX_HINTS; h++) {
		if (source_slots[h] < 0 || explicit_found[h])
			continue;
		for (int word = 0;
		     word < (AGENT_FILE_META_MAX + 63) / 64 &&
			     selected_count < AGENT_FILE_PREFETCH_MAX_HINTS;
		     word++) {
			uint64 bits = fallback_targets[h][word];

			agent_metadata_txn_work_charge(1);
			for (int bit = 0; bit < 64 &&
				     selected_count <
					     AGENT_FILE_PREFETCH_MAX_HINTS;
			     bit++) {
				int slot = word * 64 + bit;

				if (slot >= AGENT_FILE_META_MAX ||
				    (bits & (1ULL << bit)) == 0 ||
				    (selected_targets[word] &
				     (1ULL << bit)) != 0)
					continue;
				selected_targets[word] |= 1ULL << bit;
				selected_source_slots[selected_count] =
					source_slots[h];
				selected_target_slots[selected_count++] = slot;
			}
		}
	}

	reason = AGENT_FILE_PREFETCH_REASON_DEPENDENCY |
		 AGENT_FILE_PREFETCH_REASON_SAME_RUN;
	if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) || r->used_index)
		reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
	for (int i = 0; i < selected_count; i++) {
		char stage[AGENT_FILE_FIELD_SIZE];
		int candidates;

		if (agent_metadata_catalog_borrow(
			    catalog_generation, selected_target_slots[i],
			    &target_view) <= 0)
			continue;
		if (target_view.scope_id != scope_id) {
			target_view.meta = 0;
			continue;
		}
		memmove(stage, target_view.meta->stage, sizeof(stage));
		target_view.meta = 0;
		candidates = agent_file_prefetch_count_stage(
			catalog_generation, selected_source_slots[i], stage);
		agent_file_prefetch_store(
			p, catalog_generation, selected_source_slots[i],
			selected_target_slots[i], source_sequence, reason, p->pid,
			p->agent_current_span_id, p->agent_current_span_owner,
			candidates);
	}
	agent_metadata_txn_unlock();
}

static int
agent_file_prefetch_handoff(struct agent_endpoint_handle *target_handle,
			    struct agent_endpoint_handle *source_handle)
{
	struct agent_file_prefetch_hint source_hint;
	struct agent_file_prefetch_hint published;
	struct agent_catalog_view source_view;
	struct agent_catalog_view target_view;
	const struct agent_file_meta *target_meta;
	struct proc *source;
	struct proc *target;
	uint64 reason;
	uint64 catalog_generation;
	uint64 span_id;
	uint64 span_owner;
	int source_pid;
	int candidates;
	int visible;
	int start;
	int slot;
	int source_slot;
	int target_slot;
	int copied = 0;
	int enabled;

	if (target_handle == 0 || source_handle == 0 ||
	    target_handle->slot == source_handle->slot ||
	    target_handle->scope_id != source_handle->scope_id ||
	    !agent_scope_valid(source_handle->scope_id))
		return 0;
	if (!agent_metadata_txn_lock(0))
		return 0;
	catalog_generation = agent_metadata_catalog_generation();

	enabled = intr_save();
	source = agent_ipc_endpoint_resolve_locked(source_handle);
	target = agent_ipc_endpoint_resolve_locked(target_handle);
	if (source == 0 || target == 0) {
		intr_restore(enabled);
		goto out;
	}
	visible = source->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	start = (source->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	intr_restore(enabled);

	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		/*
		 * Copy one bounded source record under a short endpoint check.
		 * No proc pointer survives the following budget checkpoints.
		 */
		enabled = intr_save();
		source = agent_ipc_endpoint_resolve_locked(source_handle);
		if (source == 0) {
			intr_restore(enabled);
			break;
		}
		memmove(&source_hint, &source->agent_prefetch_hints[slot],
			sizeof(source_hint));
		source_pid = source_hint.source_pid ?
				     source_hint.source_pid : source_handle->pid;
		if (source_hint.span_id) {
			span_id = source_hint.span_id;
			span_owner = source->agent_prefetch_span_owner[slot];
		} else {
			span_id = source->agent_current_span_id;
			span_owner = source->agent_current_span_owner;
		}
		intr_restore(enabled);

		agent_metadata_txn_work_charge(1);
		if (span_id == 0 || span_owner == 0)
			continue;
		source_slot = agent_metadata_catalog_find(
			source_handle->scope_id, source_hint.source_fid, 0);
		target_slot = agent_metadata_catalog_find(
			source_handle->scope_id, source_hint.fid, 0);
		if (source_slot < 0 || target_slot < 0)
			continue;
		if (agent_metadata_catalog_borrow(
			    catalog_generation, source_slot, &source_view) <= 0)
			continue;
		if (agent_metadata_catalog_borrow(
			    catalog_generation, target_slot, &target_view) <= 0) {
			source_view.meta = 0;
			continue;
		}
		if (source_view.scope_id != source_handle->scope_id ||
		    target_view.scope_id != source_handle->scope_id) {
			source_view.meta = 0;
			target_view.meta = 0;
			continue;
		}
		target_meta = target_view.meta;

		reason = source_hint.reason |
			 AGENT_FILE_PREFETCH_REASON_HANDOFF;
		candidates = source_hint.candidate_records > 0 ?
				     source_hint.candidate_records : 1;
		memset(&published, 0, sizeof(published));
		published.source_sequence = source_hint.source_sequence;
		published.span_id = span_id;
		published.reason = reason;
		published.fs_generation = agent_file_state_scope_generation(
			source_handle->scope_id);
		published.fid = target_meta->fid;
		published.source_fid = source_view.meta->fid;
		published.source_pid = source_pid;
		published.target_pid = target_handle->pid;
		published.plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
		published.candidate_records = candidates;
		published.total_hits = candidates;
		published.score = 1000 + candidates;
		if (strncmp(target_meta->status, "pending",
			    AGENT_FILE_FIELD_SIZE) == 0) {
			published.reason |= AGENT_FILE_PREFETCH_REASON_PENDING;
			published.score += 100;
		}
		if (target_meta->stage[0]) {
			published.reason |=
				AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
			published.score += 50;
		}
		agent_file_state_project_hit(&published.hit, target_meta,
					     target_view.scope_id);
		target_meta = 0;
		source_view.meta = 0;
		target_view.meta = 0;

		/*
		 * Prepay every fixed-size publication scan. The following
		 * endpoint revalidation and commit cannot schedule.
		 */
		agent_metadata_txn_work_charge(
			2U * AGENT_FILE_PREFETCH_SPAN_MAX +
			2U * AGENT_AUDIT_MAX_RECORDS + 5U * NPROC);
		enabled = intr_save();
		target = agent_ipc_endpoint_resolve_locked(target_handle);
		if (target == 0) {
			intr_restore(enabled);
			continue;
		}
		for (int j = 0; j < target->agent_prefetch_count; j++) {
			slot = (target->agent_prefetch_head +
				AGENT_FILE_PREFETCH_MAX_HINTS -
				target->agent_prefetch_count + j) %
			       AGENT_FILE_PREFETCH_MAX_HINTS;
			if (target->agent_prefetch_hints[slot].fid ==
			    published.fid)
				goto fill;
		}
		slot = target->agent_prefetch_head %
		       AGENT_FILE_PREFETCH_MAX_HINTS;
		target->agent_prefetch_head =
			(target->agent_prefetch_head + 1) %
			AGENT_FILE_PREFETCH_MAX_HINTS;
		if (target->agent_prefetch_count <
		    AGENT_FILE_PREFETCH_MAX_HINTS)
			target->agent_prefetch_count++;

fill:
		published.sequence = ++target->agent_prefetch_sequence;
		published.tick = agent_file_state_now();
		published.score += target->agent_prefetch_count;
		memmove(&target->agent_prefetch_hints[slot], &published,
			sizeof(published));
		target->agent_prefetch_span_owner[slot] = span_owner;
		agent_observe_record_prefetch_handoff_locked(
			source_handle->pid, source_handle->control_id, target,
			&published, span_owner, published.hit.stage,
			published.reason);
		intr_restore(enabled);
		copied++;
	}
out:
	agent_metadata_txn_unlock();
	return copied;
}

int agent_metadata_prefetch_handoff(
	struct agent_endpoint_handle *target_handle,
	struct agent_endpoint_handle *source_handle)
{
	return agent_file_prefetch_handoff(target_handle, source_handle);
}

static int agent_file_find(uint scope_id, char *selector)
{
	struct agent_catalog_view view;

	agent_file_store_load();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (agent_file_read_slot(i, &view) <= 0 ||
		    view.scope_id != scope_id)
			continue;
		if (strncmp(selector, view.meta->physical_name,
			    sizeof(view.meta->physical_name)) == 0 ||
		    strncmp(selector, view.meta->logical_path,
			    sizeof(view.meta->logical_path)) == 0 ||
		    strncmp(selector, view.meta->stage,
			    sizeof(view.meta->stage)) == 0)
			return i;
		view.meta = 0;
	}
	return -1;
}

static int
agent_file_summary_read(uint scope_id, char *selector,
			struct agent_result *res)
{
	struct agent_catalog_view view;
	int slot = agent_file_find(scope_id, selector);

	if (slot < 0 || agent_file_read_slot(slot, &view) <= 0 ||
	    view.scope_id != scope_id)
		return -1;
	res->value0 = view.meta->fid;
	res->value1 = view.meta->dependency_mask;
	res->value2 = view.meta->updated_tick;
	agent_result_text(res, view.meta->summary);
	return 0;
}

static int agent_file_digest_select(uint scope_id, char *selector,
				    char *physical, int n)
{
	struct agent_file_query query;
	struct agent_catalog_view view;
	int found;

	if (agent_text_empty(selector))
		return AGENT_STATUS_BAD_PARAM;
	memset(physical, 0, n);
	if (agent_metadata_catalog_field_contains(selector, "=") ||
	    agent_metadata_catalog_field_contains(selector, ":")) {
		if (agent_query_from_payload(&query, selector) < 0)
			return AGENT_STATUS_BAD_PARAM;
		if (!agent_metadata_query_has_filter(&query))
			return AGENT_STATUS_BAD_PARAM;
		agent_file_store_load();
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (agent_file_read_slot(i, &view) <= 0 ||
			    view.scope_id != scope_id)
				continue;
			if (agent_metadata_query_matches(
				    scope_id, view.scope_id, &query, view.meta)) {
				safestrcpy(physical,
					   view.meta->physical_name, n);
				return 0;
			}
			view.meta = 0;
		}
		return AGENT_STATUS_NOT_FOUND;
	}
	found = agent_file_find(scope_id, selector);
	if (found >= 0 && agent_file_read_slot(found, &view) > 0 &&
	    view.scope_id == scope_id) {
		safestrcpy(physical, view.meta->physical_name, n);
		return 0;
	}
	safestrcpy(physical, selector, n);
	return 0;
}

static void agent_file_digest_preview(char *preview, int *pos, char c)
{
	if (*pos >= AGENT_FAST_RESULT_SIZE - 1)
		return;
	if (c == '\n' || c == '\r' || c == '\t')
		c = ' ';
	if (c < 32 || c > 126)
		c = '.';
	preview[*pos] = c;
	(*pos)++;
	preview[*pos] = 0;
}

static void agent_file_digest_read(struct proc *p, char *selector,
				   struct agent_result *res)
{
	char physical[AGENT_FILE_NAME_SIZE];
	char preview[AGENT_FAST_RESULT_SIZE];
	char buf[AGENT_FILE_DIGEST_CHUNK];
	struct inode *ip;
	uint64 hash = 1469598103934665603ULL;
	uint64 content_generation = 0;
	uint64 digest_size;
	uint64 limit;
	uint64 total = 0;
	uint off = 0;
	int pos = 0;
	int rc;
	int cacheable;
	struct vfs_cred cred;

	rc = agent_file_digest_select(agent_identity_proc_scope(p), selector, physical,
				      sizeof(physical));
	if (rc < 0) {
		res->status = rc;
		agent_result_text(res, rc == AGENT_STATUS_NOT_FOUND ?
					   "digest_not_found" :
					   "bad_selector");
		return;
	}
	if (agent_file_is_meta_store_name(physical)) {
		res->status = AGENT_STATUS_DENIED;
		agent_result_text(res, "denied");
		return;
	}
	if ((ip = namei_scope(physical, VFS_POLICY_WORKFLOW,
			      agent_identity_proc_scope(p))) == 0) {
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "digest_not_found");
		return;
	}
	vfs_cred_from_proc(p, &cred);
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "not_file");
		return;
	}
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_READ)) {
		iput(ip);
		res->status = AGENT_STATUS_DENIED;
		agent_result_text(res, "denied");
		return;
	}
	cacheable = agent_file_state_digest_cacheable(ip);
	if (cacheable && agent_file_state_digest_cache_lookup(
			 ip, res, &content_generation)) {
		iput(ip);
		return;
	}
	memset(preview, 0, sizeof(preview));
	digest_size = ip->size;
	limit = digest_size < AGENT_FILE_DIGEST_MAX_BYTES ?
			digest_size :
			AGENT_FILE_DIGEST_MAX_BYTES;
	while (total < limit) {
		uint want = MIN((uint)(limit - total),
				(uint)sizeof(buf));
		int got = readi(ip, &cred, 0, (uint64)buf, off, want);
		if (got < 0) {
			iput(ip);
			res->status = AGENT_STATUS_BAD_REQUEST;
			agent_result_text(res, "digest_read_error");
			return;
		}
		if (got == 0)
			break;
		for (int i = 0; i < got; i++) {
			hash ^= (unsigned char)buf[i];
			hash *= 1099511628211ULL;
			agent_file_digest_preview(preview, &pos, buf[i]);
		}
		total += got;
		off += got;
	}
	res->value0 = digest_size;
	res->value1 = total;
	res->value2 = hash;
	if (cacheable)
		agent_file_state_digest_cache_store(ip, content_generation,
						    digest_size, total, hash,
						    preview);
	iput(ip);
	agent_result_text(res, preview[0] ? preview : "empty_file");
}

static int agent_dependency_for_label(uint scope_id, char *label,
				      char *namespace, char *run_id,
				      uint64 *mask)
{
	struct agent_catalog_view view;
	uint64 found = 0;

	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		if (!agent_dependencies[i].used ||
		    agent_dependencies[i].scope_id != scope_id) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (strncmp(agent_dependencies[i].source, label,
			    sizeof(agent_dependencies[i].source)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (namespace && namespace[0] &&
		    strncmp(agent_dependencies[i].namespace, namespace,
			    sizeof(agent_dependencies[i].namespace)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (run_id && run_id[0] &&
		    strncmp(agent_dependencies[i].run_id, run_id,
			    sizeof(agent_dependencies[i].run_id)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (agent_dependencies[i].target[0]) {
			found |= agent_label_bit(agent_dependencies[i].source);
			found |= agent_label_bit(agent_dependencies[i].target);
		}
		agent_metadata_txn_work_charge(1);
	}
	if (found) {
		*mask = found;
		return 0;
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_file_read_slot(i, &view) <= 0)
			continue;
		if (view.scope_id != scope_id || view.meta->dependency_mask == 0) {
			view.meta = 0;
			continue;
		}
		if (namespace && namespace[0] &&
		    strncmp(view.meta->project, namespace,
			    sizeof(view.meta->project)) != 0) {
			view.meta = 0;
			continue;
		}
		if (run_id && run_id[0] &&
		    strncmp(view.meta->run_id, run_id,
			    sizeof(view.meta->run_id)) != 0) {
			view.meta = 0;
			continue;
		}
		if (strncmp(view.meta->stage, label,
			    sizeof(view.meta->stage)) == 0 ||
		    strncmp(view.meta->physical_name, label,
			    sizeof(view.meta->physical_name)) == 0 ||
		    strncmp(view.meta->logical_path, label,
			    sizeof(view.meta->logical_path)) == 0)
			found |= agent_label_bit(view.meta->stage) |
				 view.meta->dependency_mask;
		view.meta = 0;
	}
	if (found) {
		*mask = found;
		return 0;
	}
	return -1;
}

static void agent_stage_text(uint scope_id, uint64 mask, char *out, int n)
{
	struct agent_catalog_view view;
	int first = 1;
	uint64 bit;
	uint64 emitted = 0;

	memset(out, 0, n);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_file_read_slot(i, &view) <= 0)
			continue;
		if (view.scope_id != scope_id || view.meta->stage[0] == 0) {
			view.meta = 0;
			continue;
		}
		bit = agent_label_bit(view.meta->stage);
		if ((mask & bit) == 0) {
			view.meta = 0;
			continue;
		}
		if ((emitted & bit) != 0) {
			view.meta = 0;
			continue;
		}
		emitted |= bit;
		if (!first)
			agent_text_append(out, n, "+");
		agent_text_append(out, n, view.meta->stage);
		first = 0;
		view.meta = 0;
	}
	if (!out[0])
		safestrcpy(out, "none", n);
}

static int agent_action_seen(uint scope_id, int tool_id, char *project,
			     char *run_id,
			     char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;

	if (request_id == 0)
		return 0;
	for (int i = 0; i < agent_action_history_count; i++) {
		e = &agent_action_history[i];
		if (e->scope_id == scope_id && e->tool_id == tool_id &&
		    e->request_id == request_id &&
		    strncmp(e->project, project, sizeof(e->project)) == 0 &&
		    strncmp(e->run_id, run_id, sizeof(e->run_id)) == 0 &&
		    strncmp(e->stage, stage, sizeof(e->stage)) == 0)
			return 1;
	}
	return 0;
}

static void agent_action_remember(uint scope_id, int tool_id, char *project,
				  char *run_id,
				  char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;
	int owned = 0;
	int replace = -1;
	uint64 oldest = ~0ULL;

	if (request_id == 0)
		return;
	for (int i = 0; i < agent_action_history_count; i++)
		if (agent_action_history[i].scope_id == scope_id) {
			if (agent_action_history[i].sequence < oldest) {
				replace = i;
				oldest = agent_action_history[i].sequence;
			}
			owned++;
		}
	if (owned >= AGENT_ACTION_SCOPE_LIMIT) {
		e = &agent_action_history[replace];
	} else if (agent_action_history_count < AGENT_ACTION_HISTORY_MAX) {
		e = &agent_action_history[agent_action_history_count++];
	} else {
		return;
	}
	memset(e, 0, sizeof(*e));
	e->tool_id = tool_id;
	e->scope_id = scope_id;
	e->sequence = agent_action_next_sequence++;
	e->request_id = request_id;
	safestrcpy(e->project, project, sizeof(e->project));
	safestrcpy(e->run_id, run_id, sizeof(e->run_id));
	safestrcpy(e->stage, stage, sizeof(e->stage));
}

static void agent_action_history_clear_scope(uint scope_id)
{
	int out = 0;

	for (int i = 0; i < agent_action_history_count; i++) {
		if (agent_action_history[i].scope_id == scope_id)
			continue;
		if (out != i)
			agent_action_history[out] = agent_action_history[i];
		out++;
	}
	while (agent_action_history_count > out)
		memset(&agent_action_history[--agent_action_history_count], 0,
		       sizeof(agent_action_history[0]));
}

static void agent_text_append(char *dst, int n, const char *src)
{
	int len;

	if (n <= 0 || src == 0)
		return;
	len = strlen(dst);
	if (len >= n - 1)
		return;
	safestrcpy(dst + len, src, n - len);
}

static void agent_file_event_payload(const struct agent_file_meta *meta,
			     char *out,
				     int n)
{
	memset(out, 0, n);
	if (meta->status[0]) {
		agent_text_append(out, n, "status=");
		agent_text_append(out, n, meta->status);
	}
	if (meta->stage[0]) {
		agent_text_append(out, n, ";stage=");
		agent_text_append(out, n, meta->stage);
	}
	if (meta->run_id[0]) {
		agent_text_append(out, n, ";run_id=");
		agent_text_append(out, n, meta->run_id);
	}
	if (meta->project[0]) {
		agent_text_append(out, n, ";project=");
		agent_text_append(out, n, meta->project);
	}
	if (!out[0])
		safestrcpy(out, "status=changed", n);
}

static int agent_parse_selector(char *payload, char *stage, int stage_n,
				char *project, int project_n, char *run_id,
				int run_id_n)
{
	struct agent_file_query query;

	memset(stage, 0, stage_n);
	memset(project, 0, project_n);
	memset(run_id, 0, run_id_n);
	if (agent_metadata_catalog_field_contains(payload, "=") ||
	    agent_metadata_catalog_field_contains(payload, ":")) {
		if (agent_query_from_payload(&query, payload) < 0)
			return -1;
		safestrcpy(stage, query.stage, stage_n);
		safestrcpy(project, query.project, project_n);
		safestrcpy(run_id, query.run_id, run_id_n);
	} else {
		safestrcpy(stage, payload, stage_n);
	}
	return 0;
}

static int agent_file_update_status_batch(uint scope_id, char *stage,
					  char *project, char *run_id,
					  char *status, char *summary,
					  uint64 dependency_mask,
					  int propagate_dependencies)
{
	uchar selected[(AGENT_FILE_META_MAX + 7) / 8];
	uchar primary[(AGENT_FILE_META_MAX + 7) / 8];
	struct agent_catalog_view view;
	struct agent_catalog_edit edit;
	struct agent_file_meta *meta;
	int persistent_updated = 0;
	int primary_updated = 0;
	int updated = 0;

	memset(selected, 0, sizeof(selected));
	memset(primary, 0, sizeof(primary));
	if (!agent_metadata_txn_lock(1))
		return 0;
	if (agent_file_store_load() < 0)
		goto out_txn;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_file_read_slot(i, &view) <= 0)
			continue;
		if (view.scope_id != scope_id) {
			view.meta = 0;
			continue;
		}
		if (strncmp(view.meta->stage, stage,
			    sizeof(view.meta->stage)) == 0 &&
		    (!project[0] ||
		     strncmp(view.meta->project, project,
			     sizeof(view.meta->project)) == 0) &&
		    (!run_id[0] ||
		     strncmp(view.meta->run_id, run_id,
			     sizeof(view.meta->run_id)) == 0)) {
			selected[i / 8] |= 1U << (i % 8);
			primary[i / 8] |= 1U << (i % 8);
			primary_updated++;
		}
		view.meta = 0;
	}
	if (primary_updated == 0)
		goto out_txn;
	if (propagate_dependencies && dependency_mask != 0)
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			uint64 target_bit;

			agent_metadata_txn_work_charge(1);
			if (agent_file_read_slot(i, &view) <= 0)
				goto next_dependency;
			if (view.scope_id != scope_id)
				goto next_dependency;
			if (project[0] &&
			    strncmp(view.meta->project, project,
				    sizeof(view.meta->project)) != 0)
				goto next_dependency;
			if (run_id[0] &&
			    strncmp(view.meta->run_id, run_id,
				    sizeof(view.meta->run_id)) != 0)
				goto next_dependency;
			target_bit = agent_label_bit(view.meta->stage);
			if (dependency_mask & target_bit)
				selected[i / 8] |= 1U << (i % 8);
next_dependency:
			view.meta = 0;
		}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if ((selected[i / 8] & (1U << (i % 8))) == 0) {
			continue;
		}
		if (agent_metadata_catalog_edit_begin(
			    i, scope_id, &edit) < 0)
			continue;
		if (!edit.meta->used || edit.scope_id != scope_id) {
			agent_metadata_catalog_edit_abort(&edit);
			continue;
		}
		meta = edit.meta;
		safestrcpy(meta->status, status, sizeof(meta->status));
		if (primary[i / 8] & (1U << (i % 8))) {
			if (summary && summary[0])
				safestrcpy(meta->summary, summary,
					   sizeof(meta->summary));
		} else {
			safestrcpy(meta->summary,
				   "dependency refreshed",
				   sizeof(meta->summary));
		}
		meta->updated_tick = agent_file_state_now();
		meta->fs_generation =
			agent_file_state_generation_next(scope_id);
		if (meta->flags & AGENT_FILE_META_F_PERSIST)
			persistent_updated = 1;
		if (agent_metadata_catalog_edit_commit(
			    &edit, AGENT_FILE_CHANGE_STATUS) < 0)
			continue;
		updated++;
	}
	if (persistent_updated)
		agent_metadata_store_mark_dirty(scope_id);
out_txn:
	agent_metadata_txn_unlock();
	return updated;
}

struct agent_query_alias {
	char name[10];
	ushort offset, size;
};

#define AGENT_QUERY_ALIAS(name_value, field) \
	{ name_value, (ushort)__builtin_offsetof(struct agent_file_query, field), \
	  (ushort)sizeof(((struct agent_file_query *)0)->field) }
static const struct agent_query_alias agent_query_aliases[] = {
	AGENT_QUERY_ALIAS("path", physical_name),
	AGENT_QUERY_ALIAS("physical", physical_name),
	AGENT_QUERY_ALIAS("logical", logical_path),
	AGENT_QUERY_ALIAS("object", logical_path),
	AGENT_QUERY_ALIAS("object_id", logical_path),
	AGENT_QUERY_ALIAS("project", project),
	AGENT_QUERY_ALIAS("namespace", project),
	AGENT_QUERY_ALIAS("workflow", workflow),
	AGENT_QUERY_ALIAS("run", run_id),
	AGENT_QUERY_ALIAS("run_id", run_id),
	AGENT_QUERY_ALIAS("stage", stage),
	AGENT_QUERY_ALIAS("label", stage),
	AGENT_QUERY_ALIAS("kind", kind),
	AGENT_QUERY_ALIAS("type", kind),
	AGENT_QUERY_ALIAS("status", status),
	AGENT_QUERY_ALIAS("state", status),
	AGENT_QUERY_ALIAS("summary", summary_contains),
};
#undef AGENT_QUERY_ALIAS

static int agent_query_from_payload(struct agent_file_query *q, char *payload) {
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_LOGICAL_SIZE];
	int i = 0;
	int k;
	int matched;
	int v;

	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' || payload[i] == ',')
			i++;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' && payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':') {
			return -1;
		}
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		matched = 0;
		for (uint j = 0; j < NELEM(agent_query_aliases); j++) {
			const struct agent_query_alias *alias = &agent_query_aliases[j];

			if (strncmp(key, alias->name, sizeof(key)) != 0)
				continue;
			safestrcpy((char *)q + alias->offset, val, alias->size);
			matched = 1;
			break;
		}
		if (!matched)
			return -1;
	}
	return 0;
}

static void agent_object_state_update(struct proc *p, struct agent_op *op,
				      struct agent_result *res,
				      char *ok_text, char *event_action,
				      char *summary, int propagate_deps,
				      int require_selector,
				      int history_tool_id)
{
	uint64 deps = 0;
	int action_tool_id;
	int updated;
	int delivered;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];
	char selector_label[AGENT_FILE_FIELD_SIZE];
	char selector_project[AGENT_FILE_PROJECT_SIZE];
	char selector_run_id[AGENT_FILE_FIELD_SIZE];

	action_tool_id = history_tool_id ? history_tool_id : op->tool_id;
	if (require_selector &&
	    !agent_metadata_catalog_field_contains(op->payload, "=") &&
	    !agent_metadata_catalog_field_contains(op->payload, ":")) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "selector_required");
		return;
	}
	if (agent_parse_selector(op->payload, selector_label,
				 sizeof(selector_label), selector_project,
				 sizeof(selector_project), selector_run_id,
				 sizeof(selector_run_id)) < 0) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "bad_selector");
		return;
	}
	if (!selector_label[0]) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "label_required");
		return;
	}
	if (propagate_deps &&
	    agent_dependency_for_label(agent_identity_proc_scope(p), selector_label,
				       selector_project,
				       selector_run_id, &deps) < 0)
		deps = 0;
	if (agent_action_seen(agent_identity_proc_scope(p), action_tool_id,
			      selector_project, selector_run_id,
			      selector_label, op->request_id)) {
		res->status = AGENT_STATUS_DUPLICATE;
		agent_result_text(res, "duplicate");
		return;
	}
	updated = agent_file_update_status_batch(
		agent_identity_proc_scope(p), selector_label, selector_project,
		selector_run_id, "ok", summary, deps, propagate_deps);
	if (updated == 0) {
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "target_not_found");
		return;
	}
	agent_action_remember(agent_identity_proc_scope(p), action_tool_id,
			      selector_project, selector_run_id,
			      selector_label, op->request_id);
	res->value0 = deps;
	res->value1 = op->request_id;
	agent_result_text(res, ok_text);
	memset(event_payload, 0, sizeof(event_payload));
	agent_text_append(event_payload, sizeof(event_payload),
			  "state=ok;label=");
	agent_text_append(event_payload, sizeof(event_payload), selector_label);
	if (selector_run_id[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";run_id=");
		agent_text_append(event_payload, sizeof(event_payload),
				  selector_run_id);
	}
	if (event_action && event_action[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";action=");
		agent_text_append(event_payload, sizeof(event_payload),
				  event_action);
	}
	delivered = agent_ipc_deliver_watchers(p, AGENT_EVENT_JOB_DONE,
					   op->request_id,
					   p->agent_call_count + 1,
					   event_payload);
	res->value2 = delivered;
}

static int agent_tool_uses_file_metadata(int tool_id)
{
	switch (tool_id) {
	case AGENT_TOOL_QUERY_FILE:
	case AGENT_TOOL_SEND_MESSAGE:
	case AGENT_TOOL_FILE_META_INIT:
	case AGENT_TOOL_READ_FILE_SUMMARY:
	case AGENT_TOOL_DEPENDENCY_QUERY:
	case AGENT_TOOL_RERUN_STAGE:
	case AGENT_TOOL_WRITE_REPORT:
	case AGENT_TOOL_READ_FILE_DIGEST:
	case AGENT_TOOL_ACTION_COMMIT:
	case AGENT_TOOL_ARTIFACT_UPDATE:
	case AGENT_TOOL_LLM_REQUEST:
	case AGENT_TOOL_LLM_RESPONSE:
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		return 1;
	default:
		return 0;
	}
}

static int __attribute__((noinline))
agent_tool_require_cap(struct proc *p, struct agent_result *res, uint64 cap)
{
	if (agent_identity_has_cap(p, cap))
		return 1;
	res->status = AGENT_STATUS_DENIED;
	agent_result_text(res, "denied");
	return 0;
}

int
agent_metadata_execute_tool(struct proc *p, struct agent_op *op,
			    struct agent_result *res)
{
	struct inode *ip;
	struct agent_file_query query;
	struct agent_file_query_result query_result;
	int query_hit_slots[AGENT_FILE_QUERY_MAX_HITS];
	uint64 deps;
	int found;
	char dependency_label[AGENT_FILE_FIELD_SIZE];
	char dependency_project[AGENT_FILE_PROJECT_SIZE];
	char dependency_run_id[AGENT_FILE_FIELD_SIZE];

	switch (op->tool_id) {
	case AGENT_TOOL_QUERY_FILE:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_META_READ))
			break;
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "path_required");
			break;
		}
		if (agent_metadata_catalog_field_contains(op->payload, "=") ||
		    agent_metadata_catalog_field_contains(op->payload, ":")) {
			if (agent_query_from_payload(&query, op->payload) < 0) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "bad_selector");
				break;
			}
			if (!agent_metadata_query_has_filter(&query)) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "empty_selector");
				break;
			}
			found = agent_file_query_internal(agent_identity_proc_scope(p),
						       &query, &query_result,
						       query_hit_slots);
			if (found < 0) {
				res->status = found;
				agent_result_text(res, "metadata_unavailable");
				break;
			}
			agent_file_prefetch_update(p, &query, &query_result,
						   query_hit_slots,
						   p->agent_call_count);
			res->value0 = query_result.total_hits;
			res->value1 = query_result.scanned_records;
			res->value2 = (uint64)query_result.used_index |
				      ((uint64)query_result.truncated << 1);
			if (query_result.returned > 0)
				agent_result_text(
					res,
					query_result.hits[0].physical_name);
			else
				agent_result_text(res, "empty");
			break;
		}
		if ((ip = namei_scope(op->payload, VFS_POLICY_WORKFLOW,
				      agent_identity_proc_scope(p))) == 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "file_not_found");
			break;
		}
		ivalid(ip);
		res->value0 = ip->type;
		res->value1 = ip->inum;
		res->value2 = ip->size;
		iput(ip);
		agent_result_text(res, "query_file");
		break;
	case AGENT_TOOL_FILE_META_INIT:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_META_WRITE))
			break;
		agent_action_history_clear_scope(agent_identity_proc_scope(p));
		res->value0 = agent_file_store_load();
		if ((long)res->value0 < 0) {
			res->status = AGENT_STATUS_NO_SPACE;
			agent_result_text(res, "metadata_unavailable");
			break;
		}
		agent_file_request_scan();
		agent_result_text(res, "file_meta_init");
		break;
	case AGENT_TOOL_READ_FILE_SUMMARY:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_CONTENT_READ))
			break;
		if (agent_file_summary_read(agent_identity_proc_scope(p),
					   op->payload, res) < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "summary_not_found");
		}
		break;
	case AGENT_TOOL_READ_FILE_DIGEST:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_CONTENT_READ))
			break;
		agent_file_digest_read(p, op->payload, res);
		break;
	case AGENT_TOOL_DEPENDENCY_QUERY:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_META_READ))
			break;
		memset(dependency_label, 0, sizeof(dependency_label));
		memset(dependency_project, 0, sizeof(dependency_project));
		memset(dependency_run_id, 0, sizeof(dependency_run_id));
		if (agent_metadata_catalog_field_contains(op->payload, "=") ||
		    agent_metadata_catalog_field_contains(op->payload, ":")) {
			if (agent_parse_selector(op->payload, dependency_label,
						 sizeof(dependency_label),
						 dependency_project,
						 sizeof(dependency_project),
						 dependency_run_id,
						 sizeof(dependency_run_id)) < 0) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "bad_selector");
				break;
			}
		} else {
			safestrcpy(dependency_label, op->payload,
				   sizeof(dependency_label));
		}
		if (agent_dependency_for_label(agent_identity_proc_scope(p),
					       dependency_label,
					       dependency_project,
					       dependency_run_id, &deps) < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "dependency_not_found");
			break;
		}
		res->value0 = deps;
		res->value1 = agent_mask_count(deps);
		res->value2 = agent_dependency_generation;
		agent_stage_text(agent_identity_proc_scope(p), deps, res->result,
				 sizeof(res->result));
		break;
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		if (!agent_tool_require_cap(p, res,
					    AGENT_CAP_DEPENDENCY_UPDATE))
			break;
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "selector_required");
			break;
		}
		res->status = agent_dependency_update_from_payload(
			agent_identity_proc_scope(p), op->payload, res);
		if (res->status == AGENT_STATUS_BAD_PARAM)
			agent_result_text(res, "bad_selector");
		else if (res->status == AGENT_STATUS_NO_SPACE)
			agent_result_text(res, "dependency_full");
		break;
	case AGENT_TOOL_RERUN_STAGE:
	case AGENT_TOOL_ACTION_COMMIT:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_ACTION_WRITE)) {
			if (op->tool_id == AGENT_TOOL_RERUN_STAGE)
				agent_ipc_deliver_watchers(
					p, AGENT_EVENT_POLICY_DENIED,
					op->request_id, p->agent_call_count + 1,
					"action=action_commit;compat=rerun_stage");
			break;
		}
		agent_object_state_update(p, op, res, "action_committed",
					  "action_commit", "action completed",
					  1,
					  op->tool_id == AGENT_TOOL_ACTION_COMMIT,
					  op->tool_id == AGENT_TOOL_RERUN_STAGE ?
						  AGENT_TOOL_ACTION_COMMIT : 0);
		break;
	case AGENT_TOOL_WRITE_REPORT:
	case AGENT_TOOL_ARTIFACT_UPDATE:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_ARTIFACT_WRITE))
			break;
		agent_object_state_update(p, op, res, "artifact_updated",
					  "artifact_update",
					  "artifact updated", 0, 1,
					  op->tool_id == AGENT_TOOL_WRITE_REPORT ?
						  AGENT_TOOL_ARTIFACT_UPDATE : 0);
		break;
	default:
		return 0;
	}
	return 1;
}

int sys_agent_file_meta_init(void)
{
	struct proc *p = curr_proc();
	int loaded;
	int result = AGENT_STATUS_NO_SPACE;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_metadata_store_submit_wait_locked())
		return AGENT_STATUS_NO_SPACE;
	if (agent_metadata_store_loaded() &&
	    agent_metadata_store_scope_pending(agent_identity_proc_scope(p)) &&
	    agent_metadata_store_persist() < 0)
		goto out_txn;
	if (!agent_metadata_store_submit_wait_locked())
		return AGENT_STATUS_NO_SPACE;
	loaded = agent_file_store_reload(agent_identity_proc_scope(p));
	if (loaded < 0) {
		if (!agent_metadata_store_available() ||
		    agent_metadata_store_has_durable_bank())
			goto out_txn;
		if (!agent_metadata_store_loaded()) {
			if (agent_file_install_empty_store() < 0)
				goto out_txn;
		} else if (agent_metadata_store_persist_system() < 0) {
			goto out_txn;
		}
	}
	agent_action_history_clear_scope(agent_identity_proc_scope(p));
	agent_file_request_scan();
	agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT,
				  AGENT_STATUS_OK, "meta_init", 0, 0, 0,
				  0, 1);
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

struct agent_meta_string_field {
	ushort offset, size;
	uint update, changes;
};

#define AGENT_META_STRING(field, update_bit, change_bits) \
	{ (ushort)__builtin_offsetof(struct agent_file_meta, field), \
	  (ushort)sizeof(((struct agent_file_meta *)0)->field), update_bit, \
	  change_bits }
static const struct agent_meta_string_field agent_meta_string_fields[] = {
	AGENT_META_STRING(physical_name, AGENT_FILE_META_UPDATE_PHYSICAL, AGENT_FILE_CHANGE_SCOPE_KEYS),
	AGENT_META_STRING(logical_path, AGENT_FILE_META_UPDATE_LOGICAL, AGENT_FILE_CHANGE_SCOPE_KEYS),
	AGENT_META_STRING(project, AGENT_FILE_META_UPDATE_PROJECT, AGENT_FILE_CHANGE_SCOPE_KEYS),
	AGENT_META_STRING(workflow, AGENT_FILE_META_UPDATE_WORKFLOW, AGENT_FILE_CHANGE_SCOPE_KEYS),
	AGENT_META_STRING(run_id, AGENT_FILE_META_UPDATE_RUN_ID, AGENT_FILE_CHANGE_SCOPE_KEYS),
	AGENT_META_STRING(stage, AGENT_FILE_META_UPDATE_STAGE, AGENT_FILE_CHANGE_STAGE),
	AGENT_META_STRING(kind, AGENT_FILE_META_UPDATE_KIND, AGENT_FILE_CHANGE_KIND),
	AGENT_META_STRING(status, AGENT_FILE_META_UPDATE_STATUS, AGENT_FILE_CHANGE_STATUS),
	AGENT_META_STRING(summary, AGENT_FILE_META_UPDATE_SUMMARY, 0),
};
#undef AGENT_META_STRING

static void agent_file_meta_terminate_strings(struct agent_file_meta *meta) {
	for (uint i = 0; i < NELEM(agent_meta_string_fields); i++) {
		const struct agent_meta_string_field *field = &agent_meta_string_fields[i];
		((char *)meta + field->offset)[field->size - 1] = 0;
	}
}

static uint agent_file_meta_patch_strings(struct agent_file_meta *target,
		const struct agent_file_meta *source, uint mask, int *status_changed) {
	uint changes = 0;

	for (uint i = 0; i < NELEM(agent_meta_string_fields); i++) {
		const struct agent_meta_string_field *field = &agent_meta_string_fields[i];
		char *to = (char *)target + field->offset;
		const char *from = (const char *)source + field->offset;

		if ((mask & field->update) == 0 && (mask != 0 || from[0] == 0))
			continue;
		if (strncmp(to, from, field->size) != 0) {
			changes |= field->changes;
			if (field->update == AGENT_FILE_META_UPDATE_STATUS)
				*status_changed = 1;
		}
		safestrcpy(to, from, field->size);
	}
	return changes;
}

int sys_agent_file_meta_set(uint64 metaaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_meta meta;
	struct agent_file_meta auto_binding;
	struct agent_file_meta previous;
	struct agent_catalog_view view;
	struct agent_catalog_edit edit;
	struct agent_file_meta *working;
	uint scope_id;
	uint previous_scope = VFS_SCOPE_NONE;
	int slot = -1;
	int fid_slot = -1;
	int physical_slot = -1;
	int logical_slot = -1;
	int identity_slot = -1;
	int had_previous;
	int status_changed = 0;
	int auto_persist;
	int persistent;
	int result = AGENT_STATUS_NO_SPACE;
	uint changes = 0;
	uint64 audit_fid;
	uint64 mask;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	scope_id = agent_identity_proc_scope(p);
	if (!agent_scope_valid(scope_id))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&meta, metaaddr, sizeof(meta)) < 0)
		return -1;
	agent_file_meta_terminate_strings(&meta);
	if ((meta.dev != 0 || meta.inum != 0 || meta.incarnation != 0) &&
	    (meta.dev == 0 || meta.inum == 0 || meta.incarnation == 0))
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_metadata_store_submit_wait_locked())
		return AGENT_STATUS_NO_SPACE;
	if (agent_file_store_load() < 0)
		goto out_txn;
	/*
	 * Preserve auto-track persistence for an existing VFS object without
	 * mutating metadata before request validation or scanning the directory.
	 * Metadata-only creation remains volatile.
	 */
	memset(&auto_binding, 0, sizeof(auto_binding));
	auto_persist = agent_file_path_autopersist(scope_id,
					   meta.physical_name,
					   &auto_binding);
	mask = meta.update_mask;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (agent_file_read_slot(i, &view) <= 0 ||
		    view.scope_id != scope_id)
			continue;
		if (meta.fid > 0 && view.meta->fid == meta.fid)
			fid_slot = i;
		if (meta.physical_name[0] &&
		    strncmp(view.meta->physical_name, meta.physical_name,
			    sizeof(meta.physical_name)) == 0)
			physical_slot = i;
		if (meta.logical_path[0] &&
		    strncmp(view.meta->logical_path, meta.logical_path,
			    sizeof(meta.logical_path)) == 0)
			logical_slot = i;
		if (meta.dev != 0 && view.meta->dev == meta.dev &&
		    view.meta->inum == meta.inum &&
		    view.meta->incarnation == meta.incarnation)
			identity_slot = i;
		view.meta = 0;
	}
	{
		int candidates[] = {
			fid_slot, physical_slot, logical_slot, identity_slot,
		};

		for (uint i = 0; i < NELEM(candidates); i++) {
			if (candidates[i] < 0)
				continue;
			if (slot >= 0 && slot != candidates[i]) {
				result = AGENT_STATUS_CONFLICT;
				goto out_txn;
			}
			slot = candidates[i];
		}
	}
	/* The immutable inode identity is always a guard, never an update value. */
	if (meta.dev != 0 && identity_slot < 0) {
		result = slot >= 0 ? AGENT_STATUS_CONFLICT :
				     AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (meta.flags & AGENT_FILE_META_F_DELETE) {
		/* DELETE treats every supplied key as a conjunctive selector. */
		if ((meta.fid > 0 && fid_slot < 0) ||
		    (meta.physical_name[0] && physical_slot < 0) ||
		    (meta.logical_path[0] && logical_slot < 0) ||
		    (meta.dev != 0 && identity_slot < 0)) {
			result = slot >= 0 ? AGENT_STATUS_CONFLICT :
					     AGENT_STATUS_NOT_FOUND;
			goto out_txn;
		}
		if (slot < 0) {
			result = AGENT_STATUS_NOT_FOUND;
			goto out_txn;
		}
		if (agent_file_read_slot(slot, &view) <= 0) {
			result = AGENT_STATUS_NOT_FOUND;
			goto out_txn;
		}
		had_previous = 1;
		previous = *view.meta;
		previous_scope = view.scope_id;
		audit_fid = previous.fid;
		view.meta = 0;
		agent_metadata_catalog_clear_slot(slot);
		agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
		agent_metadata_store_mark_dirty(scope_id);
		if (agent_metadata_store_persist() < 0) {
			agent_file_restore_slot(slot, &previous,
						previous_scope, had_previous);
			agent_file_request_scan();
			goto out_txn;
		}
		agent_file_request_scan();
		agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT,
					  AGENT_STATUS_OK, "meta_delete",
					  audit_fid, mask, slot,
					  meta.flags, 1);
		result = 0;
		goto out_txn;
	}
	if (slot < 0) {
		slot = agent_metadata_catalog_alloc_slot(scope_id);
	}
	if (slot < 0)
		goto out_txn;
	if (meta.fid > 0)
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (i != slot && agent_file_read_slot(i, &view) > 0 &&
			    view.scope_id == scope_id &&
			    view.meta->fid == meta.fid) {
				result = AGENT_STATUS_CONFLICT;
				goto out_txn;
			}
	memset(&previous, 0, sizeof(previous));
	had_previous = agent_file_read_slot(slot, &view) > 0;
	if (had_previous) {
		previous = *view.meta;
		previous_scope = view.scope_id;
		view.meta = 0;
	}
	if (!had_previous) {
		uint64 fid = meta.fid ? meta.fid :
			       agent_metadata_catalog_alloc_fid(scope_id);

		if (fid == 0)
			goto out_txn;
		audit_fid = fid;
	}
	if (agent_metadata_catalog_edit_begin(slot, scope_id, &edit) < 0)
		goto out_txn;
	working = edit.meta;
	if (!had_previous) {
		memset(working, 0, sizeof(*working));
		working->used = 1;
		working->fid = audit_fid;
		edit.scope_id = scope_id;
		if (auto_persist) {
			working->flags = AGENT_FILE_META_F_PERSIST |
					 AGENT_FILE_META_F_AUTOSCAN;
			(void)agent_metadata_scan_apply_defaults(
				working, meta.physical_name, 0);
			working->dev = auto_binding.dev;
			working->inum = auto_binding.inum;
			working->incarnation =
				auto_binding.incarnation;
			working->size = auto_binding.size;
		}
	}
	if (edit.scope_id != scope_id) {
		agent_metadata_catalog_edit_abort(&edit);
		result = AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (meta.fid > 0)
		working->fid = meta.fid;
	changes = agent_file_meta_patch_strings(working, &meta, mask,
						&status_changed);
	if ((mask & AGENT_FILE_META_UPDATE_DEPENDENCY) ||
	    (!mask && meta.dependency_mask))
		working->dependency_mask = meta.dependency_mask;
	working->flags &= ~AGENT_FILE_META_F_AUTOSCAN;
	if (meta.flags & AGENT_FILE_META_F_AUTOSCAN)
		working->flags |= AGENT_FILE_META_F_AUTOSCAN;
	if (meta.flags & AGENT_FILE_META_F_PERSIST)
		working->flags |= AGENT_FILE_META_F_PERSIST;
	working->updated_tick = agent_file_state_now();
	if (!had_previous)
		changes = AGENT_FILE_CHANGE_ALL;
	else if (previous.dependency_mask != working->dependency_mask)
		changes |= AGENT_FILE_CHANGE_DEPENDENCY;
	if (agent_metadata_catalog_edit_commit(&edit, changes) < 0)
		goto out_txn;
	working = 0;
	if (agent_metadata_catalog_bind(slot, 1, p) < 0) {
		agent_file_restore_slot(slot, &previous, previous_scope,
					had_previous);
		result = AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (agent_file_read_slot(slot, &view) <= 0 ||
	    view.scope_id != scope_id) {
		agent_file_restore_slot(slot, &previous, previous_scope,
					had_previous);
		goto out_txn;
	}
	agent_metadata_scan_note_slot(slot);
	if (had_previous &&
	    strncmp(previous.physical_name, view.meta->physical_name,
		    sizeof(previous.physical_name)) != 0)
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	persistent = view.meta->flags & AGENT_FILE_META_F_PERSIST;
	audit_fid = view.meta->fid;
	agent_file_event_payload(view.meta, event_payload,
				 sizeof(event_payload));
	view.meta = 0;
	if (changes)
		agent_metadata_note_catalog_changes(changes);
	if (persistent)
		agent_metadata_store_mark_dirty(scope_id);
	if (persistent && agent_metadata_store_persist() < 0) {
		agent_file_restore_slot(slot, &previous, previous_scope,
					had_previous);
		agent_file_request_scan();
		goto out_txn;
	}
	if (status_changed && meta.status[0])
		agent_ipc_deliver_watchers(p, AGENT_EVENT_FILE_STATUS, meta.fid,
				       p->context_path_latest,
				       event_payload);
	agent_file_request_scan();
	agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT,
				  AGENT_STATUS_OK, "meta_set",
				  audit_fid, mask, slot,
				  meta.flags, 1);
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_query(uint64 queryaddr, uint64 resultaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_query query;
	struct agent_file_query_result result;
	int query_hit_slots[AGENT_FILE_QUERY_MAX_HITS];
	int returned;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&query, queryaddr,
		   sizeof(query)) < 0)
		return -1;
	query.physical_name[sizeof(query.physical_name) - 1] = 0;
	query.logical_path[sizeof(query.logical_path) - 1] = 0;
	query.project[sizeof(query.project) - 1] = 0;
	query.workflow[sizeof(query.workflow) - 1] = 0;
	query.run_id[sizeof(query.run_id) - 1] = 0;
	query.stage[sizeof(query.stage) - 1] = 0;
	query.kind[sizeof(query.kind) - 1] = 0;
	query.status[sizeof(query.status) - 1] = 0;
	query.summary_contains[sizeof(query.summary_contains) - 1] = 0;
	if (user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0)
		return -1;
	if (!agent_metadata_query_has_filter(&query))
		return AGENT_STATUS_BAD_PARAM;
	/*
	 * Context serialization is the outer lock everywhere: agent_run holds it
	 * while metadata tools execute.  Taking the same order here prevents the
	 * query's system-context append from inverting lane -> metadata.
	 */
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (!agent_metadata_txn_lock(1)) {
		agent_lifecycle_context_lane_leave(p);
		return AGENT_STATUS_NO_SPACE;
	}
	returned = agent_file_query_internal(agent_identity_proc_scope(p), &query,
					     &result, query_hit_slots);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0) {
		returned = -1;
		goto out_txn;
	}
	if (returned < 0)
		goto out_txn;
	if (agent_context_append_system(
		    p, AGENT_TOOL_QUERY_FILE, 0, query.flags,
		    query.status[0] ? query.status : query.stage,
		    result.returned ? result.hits[0].physical_name : "empty",
		    AGENT_STATUS_OK, result.total_hits, result.scanned_records,
		    result.used_index) == 0)
		agent_file_prefetch_update(p, &query, &result,
					   query_hit_slots,
					   p->agent_call_count);
out_txn:
	agent_metadata_txn_unlock();
	agent_lifecycle_context_lane_leave(p);
	return returned;
}

int sys_agent_file_prefetch_snapshot(uint64 hintsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_file_prefetch_hint hint;
	int visible;
	int n;
	int start;
	int slot;
	int result;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	visible = p->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	if (max == 0) {
		result = visible;
		goto out_txn;
	}
	if (hintsaddr == 0) {
		result = AGENT_STATUS_BAD_PARAM;
		goto out_txn;
	}
	n = visible < max ? visible : max;
	start = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < n; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
		if (copyout(p->pagetable,
			    hintsaddr +
				    i * sizeof(struct agent_file_prefetch_hint),
			    (char *)&hint, sizeof(hint)) < 0) {
			result = -1;
			goto out_txn;
		}
	}
	result = n;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_prefetch_span_snapshot(uint64 hintsaddr, int max)
{
	struct proc *p = curr_proc();
	int result;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	result = agent_observe_prefetch_span_snapshot(p, hintsaddr, max);
	agent_metadata_txn_unlock();
	return result;
}

int
agent_metadata_tool_enter(int tool_id)
{
	if (!agent_tool_uses_file_metadata(tool_id))
		return 0;
	if (!agent_metadata_txn_lock(1))
		return -1;
	if (!agent_metadata_store_reload_wait_locked())
		return -1;
	return 1;
}

void
agent_metadata_tool_exit(int locked)
{
	if (locked > 0)
		agent_metadata_txn_unlock();
}

void
agent_metadata_fill_info(uint scope_id, struct agent_info *info)
{
	agent_metadata_store_fill_info(scope_id, info);
	if (info == 0)
		return;
	agent_metadata_scan_fill_info(info);
	agent_file_state_fill_info(info);
}

void
agent_metadata_tick(uint64 now)
{
	if (agent_metadata_store_take_reconcile_request())
		agent_file_request_scan();
	(void)agent_metadata_scan_plan(now);
}
