#include "agent.h"
#include "agent_context.h"
#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_live_query_events.h"
#include "agent_metadata_actions.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_query.h"
#include "agent_provenance.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "trap.h"
#include "vfs_security.h"

static void agent_text_append(char *dst, int n, const char *src);

#define AGENT_QUERY_SNAPSHOT_RETRIES 2
#define AGENT_METADATA_TOOL_READ_VIEW 2

/* Protected by the metadata transaction; never place a full batch on stack. */
static struct agent_file_meta
	agent_file_meta_batch_scratch[AGENT_FILE_META_BATCH_MAX];

static void agent_result_text(struct agent_result *res, const char *text) {
	safestrcpy(res->result, text, sizeof(res->result));
}

static void agent_result_status(struct agent_result *res, int status,
				const char *text) {
	res->status = status;
	safestrcpy(res->result, text, sizeof(res->result));
}
void
agent_metadata_objects_init(void)
{
	agent_file_state_init();
	agent_metadata_query_init();
	agent_live_query_events_init();
	agent_metadata_actions_init();
	agent_metadata_catalog_init();
}

int
agent_metadata_admission_status(void)
{
	return AGENT_STATUS_OK;
}

static int agent_file_read_slot(int slot, struct agent_catalog_view *view) {
	return agent_metadata_catalog_borrow(0, slot, view);
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

static int agent_file_restore_slot(
	const struct agent_catalog_mutation_fence *fence,
	const struct agent_catalog_undo_token *undo,
	struct agent_file_meta *previous, uint previous_scope, int had_previous)
{
	int restored = agent_metadata_catalog_restore_volatile(
		fence, undo, previous, previous_scope, had_previous);

	if (restored < 0) {
		if (undo != 0)
			(void)agent_metadata_catalog_clear_slot_volatile(undo->slot);
		return -1;
	}
	agent_metadata_actions_generation_advance();
	return 0;
}

static int
agent_file_restore_status(
	const struct agent_catalog_mutation_fence *fence,
	const struct agent_catalog_undo_token *undo,
	struct agent_file_meta *previous, uint previous_scope, int had_previous,
	int fallback)
{
	if (agent_file_restore_slot(fence, undo, previous, previous_scope,
				    had_previous) >= 0)
		return fallback;
	return AGENT_STATUS_INDETERMINATE;
}

static int
agent_file_finish_mutation(struct proc *p, struct agent_file_meta *request,
			   const struct agent_file_meta *before,
			   const struct agent_file_meta *after,
			   uint scope_id, uint64 audit_fid,
			   char *success_text, int publish_event)
{
	struct workflow_lifecycle_key lifecycle = vfs_proc_lifecycle(p);
	uint64 generation = agent_file_state_scope_generation(scope_id);

	if (publish_event && workflow_lifecycle_key_valid(lifecycle))
		(void)agent_live_query_publish_transition(
			p, lifecycle, scope_id, before, after, generation);
	agent_direct_effect_audit(p, AGENT_TOOL_METADATA_INIT, AGENT_STATUS_OK,
		success_text, audit_fid, request->update_mask,
		0, request->flags, 1);
	return 0;
}

static int
agent_lookup_error_status(int fs_status)
{
	return fs_status == FS_LOOKUP_ABSENT ? AGENT_STATUS_NOT_FOUND :
	       fs_status == FS_LOOKUP_BUSY ? AGENT_STATUS_RETRY :
					     AGENT_STATUS_IO_ERROR;
}

static int
agent_catalog_error_status(int catalog_status)
{
	switch (catalog_status) {
	case AGENT_CATALOG_NO_SPACE:
		return AGENT_STATUS_NO_SPACE;
	case AGENT_CATALOG_INTERRUPTED:
	case AGENT_CATALOG_STALE:
		return AGENT_STATUS_RETRY;
	case AGENT_CATALOG_CONFLICT:
		return AGENT_STATUS_CONFLICT;
	case AGENT_CATALOG_INDETERMINATE:
		return AGENT_STATUS_INDETERMINATE;
	default:
		return AGENT_STATUS_IO_ERROR;
	}
}

void
agent_metadata_background_maintain(void)
{
	int content;
	int tombstones;

	vfs_scope_reap_pending(agent_file_state_now());
	if (!agent_metadata_txn_try_external())
		return;
	tombstones = agent_live_query_tombstone_drain(8);
	content = agent_live_query_content_drain(8);
	agent_metadata_txn_unlock();
	if (tombstones == AGENT_STATUS_RETRY ||
	    content == AGENT_STATUS_RETRY)
		agent_background_request();
}

int agent_metadata_inode_trackable(struct inode *ip)
{
	if (ip == 0)
		return 0;
	if (ip->valid && (ip->vfs_policy != VFS_POLICY_WORKFLOW ||
			  ip->type != T_FILE))
		return 0;
	if (ivalid(ip) < 0)
		return 0;
	return vfs_inode_label_valid(ip) &&
	       ip->vfs_policy == VFS_POLICY_WORKFLOW && ip->type == T_FILE &&
	       agent_object_scope_valid(ip->vfs_scope_id);
}

void agent_metadata_note_catalog_changes(uint changes)
{
	agent_metadata_actions_note_changes(changes);
}

int agent_metadata_durability_fence_current(void)
{
	struct proc *p = curr_proc();
	uint64 generation;

	if (p == 0)
		return -1;
	if (!p->is_agent)
		return 0;
	return agent_metadata_quiescence_fence_snapshot_current(&generation);
}

int
agent_metadata_quiescence_fence_snapshot_current(uint64 *metadata_generation)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;
	int result;

	if (metadata_generation == 0)
		return -1;
	*metadata_generation = 0;
	if (p == 0 || !p->is_agent)
		return -1;
	scope_id = agent_identity_proc_scope(p);
	if (!agent_scope_valid(scope_id))
		return -1;
	lifecycle = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    !agent_metadata_txn_lock(1))
		return -1;
	result = agent_live_query_fence_drain(lifecycle, scope_id);
	if (result == AGENT_STATUS_OK)
		result = agent_metadata_catalog_fence_generation(
			scope_id, lifecycle, metadata_generation);
	agent_metadata_txn_unlock();
	return result;
}

int
agent_metadata_quiescence_fence_current(void)
{
	struct proc *p = curr_proc();
	uint64 metadata_generation;

	if (p == 0 || !p->is_agent)
		return 0;
	return agent_metadata_quiescence_fence_snapshot_current(
		&metadata_generation);
}

int agent_scope_reclaim_begin(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint64 *metadata_target)
{
	struct workflow_lifecycle_key current = workflow_lifecycle_none();
	int result;
	int reclaimed;

	if (!agent_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    vfs_scope_lifecycle(scope_id, &current) < 0 ||
	    !workflow_lifecycle_key_equal(current, lifecycle) ||
	    metadata_target == 0)
		return -1;
	*metadata_target = 0;
	if (!agent_metadata_txn_try_external())
		return -1;
	reclaimed = agent_metadata_catalog_reclaim_scope(scope_id, lifecycle);
	if (reclaimed < 0) {
		result = -1;
		goto out_txn;
	}
	agent_metadata_actions_reclaim_scope(scope_id);
	agent_metadata_query_invalidate_locked(scope_id, 0);
	agent_live_query_reclaim(lifecycle, scope_id);
	agent_context_artifact_reclaim_lifecycle(lifecycle);
	agent_context_prefetch_reclaim_lifecycle(lifecycle);
	if (agent_observe_scope_reclaim(scope_id) < 0) {
		result = -1;
		goto out_txn;
	}
	agent_file_state_scope_reclaim(scope_id);
	if (reclaimed > 0)
		agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_STATUS |
				    AGENT_FILE_CHANGE_STAGE |
				    AGENT_FILE_CHANGE_KIND |
				    AGENT_FILE_CHANGE_MEMBERSHIP);
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

static void agent_file_query_reset(struct agent_file_query_result *result)
{
	memset(result, 0, sizeof(*result));
	result->provenance_labels =
		AGENT_PROVENANCE_UNTRUSTED_FILE_DATA |
		AGENT_PROVENANCE_AGENT_DERIVED;
}

static int __attribute__((noinline)) agent_file_query_internal(uint scope_id,
				     struct agent_file_query *q,
				     struct agent_file_query_result *r)
{
	int result;

	agent_file_query_reset(r);
	if (!agent_scope_valid(scope_id)) {
		return AGENT_STATUS_DENIED;
	}
	for (int attempt = 0;
	     attempt < AGENT_QUERY_SNAPSHOT_RETRIES; attempt++) {
		result = agent_metadata_query_execute_snapshot(scope_id, q, r);
		if (result >= 0)
			return result;
		if (result != AGENT_CATALOG_STALE &&
		    result != AGENT_CATALOG_CONFLICT)
			break;
	}
	agent_file_query_reset(r);
	return AGENT_STATUS_RETRY;
}

static int agent_file_find(uint scope_id, char *selector)
{
	struct agent_catalog_view view;
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
	return AGENT_STATUS_NOT_FOUND;
}

static int __attribute__((noinline))
agent_file_summary_read(uint scope_id, char *selector,
			struct agent_result *res)
{
	struct agent_catalog_view view;
	struct agent_file_meta meta;
	int slot = agent_file_find(scope_id, selector);

	if (slot < 0)
		return slot;
	if (agent_file_read_slot(slot, &view) <= 0 ||
	    view.scope_id != scope_id)
		return AGENT_STATUS_IO_ERROR;
	meta = *view.meta;
	view.meta = 0;
	agent_file_state_overlay_published_size(&meta, scope_id);
	res->value0 = meta.fid;
	res->value1 = meta.dependency_mask;
	res->value2 = meta.updated_tick;
	agent_result_text(res, meta.summary);
	return 0;
}

static int agent_file_digest_select(uint scope_id, char *selector,
				    char *physical, int n)
{
	struct agent_file_query query;
	struct agent_catalog_view view;
	int found;

	if (selector[0] == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(physical, 0, n);
	if (agent_metadata_catalog_field_contains(selector, "=") ||
	    agent_metadata_catalog_field_contains(selector, ":")) {
		if (agent_metadata_query_from_payload(&query, selector) < 0)
			return AGENT_STATUS_BAD_PARAM;
		if (!agent_metadata_query_has_filter(&query))
			return AGENT_STATUS_BAD_PARAM;
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
	if (found < 0 && found != AGENT_STATUS_NOT_FOUND)
		return found;
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

static void __attribute__((noinline))
agent_file_digest_read(struct proc *p, char *selector,
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
	int lookup_status;
	struct vfs_cred cred;

	rc = agent_file_digest_select(agent_identity_proc_scope(p), selector, physical,
				      sizeof(physical));
	if (rc < 0) {
		agent_result_status(
			res, rc, rc == AGENT_STATUS_NOT_FOUND ?
					 "digest_not_found" :
				 rc == AGENT_STATUS_BAD_PARAM ?
					 "bad_selector" : "metadata_unavailable");
		return;
	}
	ip = namei_scope_status(physical, VFS_POLICY_WORKFLOW,
			       agent_identity_proc_scope(p), &lookup_status);
	if (ip == 0) {
		rc = agent_lookup_error_status(lookup_status);
		agent_result_status(res, rc, rc == AGENT_STATUS_NOT_FOUND ?
				    "digest_not_found" : "metadata_unavailable");
		return;
	}
	vfs_cred_from_proc(p, &cred);
	rc = ivalid(ip);
	if (rc < 0) {
		iput(ip);
		agent_result_status(res, agent_lookup_error_status(rc),
				    "metadata_unavailable");
		return;
	}
	if (ip->type != T_FILE) {
		iput(ip);
		agent_result_status(res, AGENT_STATUS_BAD_PARAM, "not_file");
		return;
	}
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_READ)) {
		iput(ip);
		agent_result_status(res, AGENT_STATUS_DENIED, "denied");
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
			agent_result_status(res, agent_lookup_error_status(got),
					    "digest_read_error");
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

struct agent_object_selector {
	char label[AGENT_FILE_FIELD_SIZE];
	char project[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
};

static int agent_parse_selector(char *payload,
				struct agent_object_selector *selector)
{
	struct agent_file_query query;

	memset(selector, 0, sizeof(*selector));
	if (agent_metadata_catalog_field_contains(payload, "=") ||
	    agent_metadata_catalog_field_contains(payload, ":")) {
		if (agent_metadata_query_from_payload(&query, payload) < 0)
			return -1;
		safestrcpy(selector->label, query.stage, sizeof(selector->label));
		safestrcpy(selector->project, query.project,
			   sizeof(selector->project));
		safestrcpy(selector->run_id, query.run_id,
			   sizeof(selector->run_id));
	} else {
		safestrcpy(selector->label, payload, sizeof(selector->label));
	}
	return 0;
}

static int
agent_file_update_status_batch_locked(
	uint scope_id, char *stage, char *project, char *run_id,
	char *status, char *summary, uint64 dependency_mask,
	int propagate_dependencies)
{
	struct agent_catalog_mutation_fence mutation_fence;
	int updated = 0;

	memset(&mutation_fence, 0, sizeof(mutation_fence));
	agent_metadata_txn_require_owned(1, "Agent metadata action transaction");
	if (agent_metadata_catalog_mutation_begin(&mutation_fence) < 0)
		return AGENT_STATUS_RETRY;
	updated = agent_metadata_actions_update_status_locked(
		scope_id, stage, project, run_id, status, summary,
		dependency_mask, propagate_dependencies);
	if (agent_metadata_catalog_mutation_end(&mutation_fence) < 0)
		return AGENT_STATUS_INDETERMINATE;
	return updated;
}
static void agent_object_state_update(struct proc *p, struct agent_op *op,
				      struct agent_result *res,
				      char *ok_text, char *event_action,
				      char *summary, int propagate_deps,
				      int require_selector)
{
	uint64 deps = 0;
	int updated;
	int delivered;
	struct agent_object_selector selector;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

	if (require_selector &&
	    !agent_metadata_catalog_field_contains(op->payload, "=") &&
	    !agent_metadata_catalog_field_contains(op->payload, ":")) {
		agent_result_status(res, AGENT_STATUS_BAD_PARAM,
				    "selector_required");
		return;
	}
	if (agent_parse_selector(op->payload, &selector) < 0) {
		agent_result_status(res, AGENT_STATUS_BAD_PARAM, "bad_selector");
		return;
	}
	if (!selector.label[0]) {
		agent_result_status(res, AGENT_STATUS_BAD_PARAM, "label_required");
		return;
	}
	if (propagate_deps &&
	    agent_metadata_actions_dependency_mask(
		agent_identity_proc_scope(p), selector.label, selector.project,
		selector.run_id, &deps) < 0)
		deps = 0;
	if (agent_metadata_actions_seen(
		agent_identity_proc_scope(p), op->tool_id, selector.project,
		selector.run_id, selector.label, op->request_id)) {
		agent_result_status(res, AGENT_STATUS_DUPLICATE, "duplicate");
		return;
	}
	updated = agent_file_update_status_batch_locked(
		agent_identity_proc_scope(p), selector.label, selector.project,
		selector.run_id, "ok", summary, deps, propagate_deps);
	if (updated < 0) {
		agent_result_status(res, updated,
			updated == AGENT_STATUS_NO_SPACE ?
				"status_batch_too_large" :
			updated == AGENT_STATUS_RETRY ||
			updated == AGENT_STATUS_IO_ERROR ?
				"metadata_unavailable" : "state_update_failed");
		return;
	}
	if (updated == 0) {
		agent_result_status(res, AGENT_STATUS_NOT_FOUND,
				    "target_not_found");
		return;
	}
	agent_metadata_actions_remember(
		agent_identity_proc_scope(p), op->tool_id, selector.project,
		selector.run_id, selector.label, op->request_id);
	res->value0 = deps;
	res->value1 = op->request_id;
	agent_result_text(res, ok_text);
	memset(event_payload, 0, sizeof(event_payload));
	agent_text_append(event_payload, sizeof(event_payload),
			  "state=ok;label=");
	agent_text_append(event_payload, sizeof(event_payload), selector.label);
	if (selector.run_id[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";run_id=");
		agent_text_append(event_payload, sizeof(event_payload),
				  selector.run_id);
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
	return (tool_id >= AGENT_TOOL_QUERY_FILE &&
		tool_id <= AGENT_TOOL_CAPABILITY_CHECK &&
		tool_id != AGENT_TOOL_READ_MESSAGE) ||
	       tool_id == AGENT_TOOL_ACTION_COMMIT ||
	       tool_id == AGENT_TOOL_ARTIFACT_UPDATE ||
	       (tool_id >= AGENT_TOOL_READ_FILE_DIGEST &&
		tool_id <= AGENT_TOOL_DEPENDENCY_UPDATE);
}

static int __attribute__((noinline))
agent_tool_require_cap(struct proc *p, struct agent_result *res, uint64 cap)
{
	if (agent_identity_has_cap(p, cap))
		return 1;
	agent_result_status(res, AGENT_STATUS_DENIED, "denied");
	return 0;
}

static void __attribute__((noinline))
agent_metadata_query_file_tool(struct proc *p, struct agent_op *op,
			       struct agent_result *res)
{
	struct agent_file_query query;
	struct agent_file_query_result query_result;
	struct inode *ip;
	int found;
	int lookup_status;

	if (!agent_tool_require_cap(p, res, AGENT_CAP_META_READ))
		return;
	if (op->payload[0] == 0) {
		agent_result_status(res, AGENT_STATUS_BAD_PARAM,
				    "path_required");
		return;
	}
	if (agent_metadata_catalog_field_contains(op->payload, "=") ||
	    agent_metadata_catalog_field_contains(op->payload, ":")) {
		if (agent_metadata_query_from_payload(&query, op->payload) < 0) {
			agent_result_status(res, AGENT_STATUS_BAD_PARAM,
					    "bad_selector");
			return;
		}
		if (!agent_metadata_query_has_filter(&query)) {
			agent_result_status(res, AGENT_STATUS_BAD_PARAM,
					    "empty_selector");
			return;
		}
		found = agent_file_query_internal(agent_identity_proc_scope(p),
					       &query, &query_result);
		if (found < 0) {
			agent_result_status(res, found, "metadata_unavailable");
			return;
		}
		res->value0 = query_result.total_hits;
		res->value1 = query_result.scanned_records;
		res->value2 = (uint64)query_result.used_index |
			      ((uint64)query_result.truncated << 1);
		if (query_result.returned > 0)
			agent_result_text(res,
					  query_result.hits[0].physical_name);
		else
			agent_result_text(res, "empty");
		return;
	}
	ip = namei_scope_status(op->payload, VFS_POLICY_WORKFLOW,
				agent_identity_proc_scope(p), &lookup_status);
	if (ip == 0) {
		found = agent_lookup_error_status(lookup_status);
		agent_result_status(res, found,
			found == AGENT_STATUS_NOT_FOUND ?
				"file_not_found" : "metadata_unavailable");
		return;
	}
	lookup_status = ivalid(ip);
	if (lookup_status < 0) {
		iput(ip);
		agent_result_status(res, agent_lookup_error_status(lookup_status),
				    "metadata_unavailable");
		return;
	}
	res->value0 = ip->type;
	res->value1 = ip->inum;
	res->value2 = ip->size;
	iput(ip);
	agent_result_text(res, "query_file");
}

static void __attribute__((noinline))
agent_metadata_dependency_query_tool(struct proc *p, struct agent_op *op,
				     struct agent_result *res)
{
	struct agent_object_selector dependency;

	if (!agent_tool_require_cap(p, res, AGENT_CAP_META_READ))
		return;
	if (agent_parse_selector(op->payload, &dependency) < 0) {
		agent_result_status(res, AGENT_STATUS_BAD_PARAM, "bad_selector");
		return;
	}
	if (agent_metadata_actions_dependency_query(
		    agent_identity_proc_scope(p), dependency.label,
		    dependency.project, dependency.run_id, res) < 0)
		agent_result_status(res, AGENT_STATUS_NOT_FOUND,
				    "dependency_not_found");
}

static uint64
agent_metadata_init_locked(struct proc *p)
{
	uint scope_id = agent_identity_proc_scope(p);

	agent_metadata_actions_clear_history(scope_id);
	agent_metadata_query_invalidate_locked(scope_id, 0);
	return agent_metadata_catalog_generation();
}

int
agent_metadata_execute_tool(struct proc *p, struct agent_op *op,
			    struct agent_result *res)
{
	switch (op->tool_id) {
	case AGENT_TOOL_QUERY_FILE:
		agent_metadata_query_file_tool(p, op, res);
		break;
	case AGENT_TOOL_METADATA_INIT:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_META_WRITE))
			break;
		res->value0 = agent_metadata_init_locked(p);
		agent_result_text(res, "metadata_init");
		break;
	case AGENT_TOOL_READ_FILE_SUMMARY: {
		int found;

		if (!agent_tool_require_cap(p, res, AGENT_CAP_CONTENT_READ))
			break;
		found = agent_file_summary_read(
			agent_identity_proc_scope(p), op->payload, res);
		if (found < 0) {
			agent_result_status(
				res, found,
				found == AGENT_STATUS_NOT_FOUND ?
					"summary_not_found" :
					"metadata_unavailable");
		}
		break;
	}
	case AGENT_TOOL_READ_FILE_DIGEST:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_CONTENT_READ))
			break;
		agent_file_digest_read(p, op->payload, res);
		break;
	case AGENT_TOOL_DEPENDENCY_QUERY:
		agent_metadata_dependency_query_tool(p, op, res);
		break;
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		if (!agent_tool_require_cap(p, res,
					    AGENT_CAP_DEPENDENCY_UPDATE))
			break;
		if (op->payload[0] == 0) {
			agent_result_status(res, AGENT_STATUS_BAD_PARAM,
					    "selector_required");
			break;
		}
		res->status = agent_metadata_actions_dependency_update(
			agent_identity_proc_scope(p), op->payload, res);
		if (res->status == AGENT_STATUS_BAD_PARAM)
			agent_result_text(res, "bad_selector");
		else if (res->status == AGENT_STATUS_NO_SPACE)
			agent_result_text(res, "dependency_full");
		break;
	case AGENT_TOOL_ACTION_COMMIT:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_ACTION_WRITE))
			break;
		agent_object_state_update(p, op, res, "action_committed",
					  "action_commit", "action completed",
					  1, 1);
		break;
	case AGENT_TOOL_ARTIFACT_UPDATE:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_ARTIFACT_WRITE))
			break;
		agent_object_state_update(p, op, res, "artifact_updated",
					  "artifact_update",
					  "artifact updated", 0, 1);
		break;
	default:
		return 0;
	}
	return 1;
}

static int agent_metadata_init_execute(struct proc *p)
{
	int result = AGENT_STATUS_NO_SPACE;
	uint64 generation;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	generation = agent_metadata_init_locked(p);
	agent_direct_effect_audit(p, AGENT_TOOL_METADATA_INIT,
				  AGENT_STATUS_OK, "meta_init", 0,
				  generation, 0,
				  0, 1);
	result = 0;
	agent_metadata_txn_unlock();
	return result;
}

int
sys_agent_file_meta_init(void)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	int result;

	if (p == 0 || !p->is_agent)
		return -1;
	lifecycle = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    workflow_lifecycle_operation_enter(lifecycle) < 0)
		return -1;
	result = agent_metadata_init_execute(p);
	workflow_lifecycle_operation_leave(lifecycle);
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
		const struct agent_file_meta *source, uint mask,
		int *value_changed) {
	uint changes = 0;

	for (uint i = 0; i < NELEM(agent_meta_string_fields); i++) {
		const struct agent_meta_string_field *field = &agent_meta_string_fields[i];
		char *to = (char *)target + field->offset;
		const char *from = (const char *)source + field->offset;

		if ((mask & field->update) == 0 && (mask != 0 || from[0] == 0))
			continue;
		if (strncmp(to, from, field->size) != 0) {
			changes |= field->changes;
			*value_changed = 1;
		}
		safestrcpy(to, from, field->size);
	}
	return changes;
}

static int
agent_file_meta_write_scope(struct proc *p, uint *scope_id)
{
	if (p == 0 || !p->is_agent)
		return -1;
	*scope_id = agent_identity_proc_scope(p);
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    *scope_id, 0) ||
	    !agent_scope_valid(*scope_id))
		return AGENT_STATUS_DENIED;
	return AGENT_STATUS_OK;
}

static int agent_file_meta_set_execute(
	struct proc *p, uint64 metaaddr,
	const struct agent_file_meta *prepared_meta, uint scope_id,
	int transaction_owned, int *stop_batch)
{
	struct agent_file_meta meta;
	struct agent_file_meta previous;
	struct agent_file_meta current;
	struct agent_catalog_view view;
	struct agent_catalog_edit edit;
	struct agent_catalog_mutation_fence mutation_fence;
	struct agent_catalog_undo_token undo;
	struct agent_catalog_resolution selector;
	struct agent_file_meta *working;
	uint previous_scope = VFS_SCOPE_NONE;
	int slot = -1;
	int had_previous;
	int metadata_changed = 0;
	int commit_status;
	int bind_status;
	int fence_active = 0;
	int release_txn = 0;
	int result = AGENT_STATUS_NO_SPACE;
	uint changes = 0;
	uint64 audit_fid = 0;
	uint64 mask;

	memset(&mutation_fence, 0, sizeof(mutation_fence));
	memset(&undo, 0, sizeof(undo));
	if (stop_batch != 0)
		*stop_batch = 0;

	if (!p->is_agent)
		return -1;
	if (!agent_scope_valid(scope_id))
		return AGENT_STATUS_DENIED;
	if (prepared_meta != 0)
		meta = *prepared_meta;
	else if (copyin(p->pagetable, (char *)&meta, metaaddr,
			 sizeof(meta)) < 0) {
		if (stop_batch != 0)
			*stop_batch = 1;
		return -1;
	}
	agent_file_meta_terminate_strings(&meta);
	if (meta.flags & (AGENT_FILE_META_F_PERSIST |
			  AGENT_FILE_META_F_AUTOSCAN))
		return AGENT_STATUS_BAD_PARAM;
	if (meta.flags & ~AGENT_FILE_META_F_DELETE)
		return AGENT_STATUS_BAD_PARAM;
	if ((meta.dev != 0 || meta.inum != 0 || meta.incarnation != 0) &&
	    (meta.dev == 0 || meta.inum == 0 || meta.incarnation == 0))
		return AGENT_STATUS_BAD_PARAM;
	if (transaction_owned) {
		if (!agent_metadata_txn_owned(1)) {
			if (stop_batch != 0)
				*stop_batch = 1;
			return AGENT_STATUS_INDETERMINATE;
		}
	} else {
		if (!agent_metadata_txn_lock(1))
			return AGENT_STATUS_NO_SPACE;
		release_txn = 1;
	}
	agent_metadata_catalog_resolve(scope_id, &meta, -1, &selector);
	if (agent_metadata_catalog_mutation_begin(&mutation_fence) < 0) {
		result = AGENT_STATUS_RETRY;
		if (stop_batch != 0)
			*stop_batch = 1;
		goto out_txn;
	}
	fence_active = 1;
	mask = meta.update_mask;
	if (selector.slot == AGENT_CATALOG_CONFLICT) {
		result = AGENT_STATUS_CONFLICT;
		goto out_txn;
	}
	slot = selector.slot;
	if (meta.dev != 0 &&
	    (selector.matched & AGENT_CATALOG_KEY_IDENTITY) == 0) {
		result = slot >= 0 ? AGENT_STATUS_CONFLICT :
				     AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (meta.flags & AGENT_FILE_META_F_DELETE) {
		if (selector.matched != selector.provided) {
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
		commit_status = agent_metadata_catalog_clear_slot_volatile(slot);
		if (commit_status < 0) {
			result = agent_catalog_error_status(commit_status);
			agent_direct_effect_audit(
				p, AGENT_TOOL_METADATA_INIT, result,
				"meta_delete_sidecar_io", audit_fid, mask, 0,
				meta.flags, 1);
			goto out_txn;
		}
		if (agent_metadata_catalog_undo_capture(
			    &mutation_fence, slot, &undo) < 0) {
			result = AGENT_STATUS_INDETERMINATE;
			goto out_txn;
		}
		agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
		result = agent_file_finish_mutation(
			p, &meta, &previous, 0, scope_id, audit_fid,
			"meta_delete", 1);
		goto out_txn;
	}
	if (slot < 0) {
		slot = agent_metadata_catalog_alloc_slot(scope_id);
		if (slot < 0) {
			result = agent_catalog_error_status(slot);
			goto out_txn;
		}
	}
	memset(&previous, 0, sizeof(previous));
	had_previous = agent_file_read_slot(slot, &view) > 0;
	if (had_previous) {
		previous = *view.meta;
		previous_scope = view.scope_id;
		view.meta = 0;
	}
	if (!had_previous) {
		audit_fid = meta.fid ? meta.fid :
			agent_metadata_catalog_alloc_fid(scope_id);
		if (audit_fid == 0)
			goto out_txn;
	}
	commit_status = agent_metadata_catalog_edit_begin(slot, scope_id, &edit);
	if (commit_status < 0) {
		if (commit_status == AGENT_CATALOG_CONFLICT)
			result = AGENT_STATUS_RETRY;
		goto out_txn;
	}
	working = edit.meta;
	if (!had_previous) {
		memset(working, 0, sizeof(*working));
		working->used = 1;
		working->fid = audit_fid;
		edit.scope_id = scope_id;
	}
	if (edit.scope_id != scope_id) {
		agent_metadata_catalog_edit_abort(&edit);
		result = AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (meta.fid > 0)
		working->fid = meta.fid;
	changes = agent_file_meta_patch_strings(working, &meta, mask,
						&metadata_changed);
	if ((mask & AGENT_FILE_META_UPDATE_DEPENDENCY) ||
	    (!mask && meta.dependency_mask)) {
		if (working->dependency_mask != meta.dependency_mask)
			metadata_changed = 1;
		working->dependency_mask = meta.dependency_mask;
	}
	if (working->flags != 0)
		metadata_changed = 1;
	working->flags = 0;
	working->updated_tick = agent_file_state_now();
	if (!had_previous) {
		changes = AGENT_FILE_CHANGE_ALL;
		metadata_changed = 1;
	} else if (previous.dependency_mask != working->dependency_mask)
		changes |= AGENT_FILE_CHANGE_DEPENDENCY;
	commit_status = agent_metadata_catalog_edit_commit_volatile(&edit,
							 changes);
	if (commit_status < 0) {
		result = agent_catalog_error_status(commit_status);
		goto out_txn;
	}
	working = 0;
	if (agent_metadata_catalog_undo_capture(
		    &mutation_fence, slot, &undo) < 0) {
		(void)agent_metadata_catalog_clear_slot_volatile(slot);
		result = AGENT_STATUS_INDETERMINATE;
		goto out_txn;
	}
	bind_status = agent_metadata_catalog_bind_volatile(slot, 1, p);
	if (bind_status < 0) {
		result = agent_file_restore_status(
			&mutation_fence, &undo, &previous, previous_scope,
			had_previous,
			bind_status == AGENT_CATALOG_INDETERMINATE ?
				AGENT_STATUS_INDETERMINATE : AGENT_STATUS_IO_ERROR);
		goto out_txn;
	}
	if (agent_metadata_catalog_undo_capture(
		    &mutation_fence, slot, &undo) < 0 ||
	    (bind_status > 0 && agent_metadata_catalog_undo_note_created(
				      &mutation_fence, &undo) < 0)) {
		(void)agent_metadata_catalog_clear_slot_volatile(slot);
		result = AGENT_STATUS_INDETERMINATE;
		goto out_txn;
	}
	if (agent_file_read_slot(slot, &view) <= 0 ||
	    view.scope_id != scope_id) {
		result = agent_file_restore_status(
			&mutation_fence, &undo, &previous, previous_scope,
			had_previous, AGENT_STATUS_IO_ERROR);
		goto out_txn;
	}
	if (had_previous &&
	    strncmp(previous.physical_name, view.meta->physical_name,
		    sizeof(previous.physical_name)) != 0)
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	audit_fid = view.meta->fid;
	current = *view.meta;
	view.meta = 0;
	if (changes)
		agent_metadata_note_catalog_changes(changes);
	result = agent_file_finish_mutation(
		p, &meta, had_previous ? &previous : 0, &current,
		scope_id, audit_fid, "meta_set", metadata_changed);
out_txn:
	if (fence_active &&
	    agent_metadata_catalog_mutation_end(&mutation_fence) < 0) {
		result = AGENT_STATUS_INDETERMINATE;
		if (stop_batch != 0)
			*stop_batch = 1;
	}
	if (result == AGENT_STATUS_INDETERMINATE && stop_batch != 0)
		*stop_batch = 1;
	if (release_txn)
		agent_metadata_txn_unlock();
	return result;
}

int
sys_agent_file_meta_set(uint64 metaaddr)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;
	int result;

	if (p == 0 || !p->is_agent)
		return -1;
	lifecycle = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    workflow_lifecycle_operation_enter(lifecycle) < 0)
		return -1;
	result = agent_file_meta_write_scope(p, &scope_id);
	if (result == AGENT_STATUS_OK)
		result = agent_file_meta_set_execute(
			p, metaaddr, 0, scope_id, 0, 0);
	workflow_lifecycle_operation_leave(lifecycle);
	return result;
}

static int
agent_file_meta_set_batch_execute(struct proc *p, uint64 itemsaddr,
				  uint64 statusesaddr, int count)
{
	uint scope_id;
	int access_status;
	int processed = 0;
	uint64 items_bytes = (uint64)count * sizeof(struct agent_file_meta);
	uint64 statuses_bytes = (uint64)count * sizeof(int);
	uint64 items_end = itemsaddr + items_bytes;
	uint64 statuses_end = statusesaddr + statuses_bytes;

	access_status = agent_file_meta_write_scope(p, &scope_id);
	if (access_status != AGENT_STATUS_OK)
		return access_status;
	/*
	 * Status writes must not alter an item that has not been copied yet.  Stage
	 * all inputs while a VM snapshot pauses sibling address-space changes, and
	 * resolve output COW pages before the first metadata mutation.  The global
	 * scratch is protected by the metadata transaction.
	 */
	if (itemsaddr < statuses_end && statusesaddr < items_end)
		return -1;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (proc_vm_snapshot_begin(p) < 0)
		goto out_txn_error;
	if (user_range_check(p->pagetable, itemsaddr, items_bytes, PTE_R) < 0 ||
	    user_range_check(p->pagetable, statusesaddr, statuses_bytes, PTE_W) < 0)
		goto out_snapshot_error;
	for (int i = 0; i < count; i++) {
		int preserved;
		uint64 itemaddr = itemsaddr +
			(uint64)i * sizeof(struct agent_file_meta);
		uint64 statusaddr = statusesaddr + (uint64)i * sizeof(preserved);

		if (copyin(p->pagetable,
			   (char *)&agent_file_meta_batch_scratch[i], itemaddr,
			   sizeof(agent_file_meta_batch_scratch[i])) < 0 ||
		    copyin(p->pagetable, (char *)&preserved, statusaddr,
			   sizeof(preserved)) < 0 ||
		    copyout(p->pagetable, statusaddr, (char *)&preserved,
			    sizeof(preserved)) < 0)
			goto out_snapshot_error;
	}
	proc_vm_snapshot_end(p);
	for (int i = 0; i < count; i++) {
		int stop_batch = 0;
		int item_status = agent_file_meta_set_execute(
			p, 0, &agent_file_meta_batch_scratch[i], scope_id, 1,
			&stop_batch);

		if (item_status == -1)
			break;
		if (copyout(p->pagetable,
			    statusesaddr + (uint64)i * sizeof(item_status),
			    (char *)&item_status, sizeof(item_status)) < 0) {
			processed = AGENT_STATUS_INDETERMINATE;
			break;
		}
		processed++;
		if (stop_batch || item_status == AGENT_STATUS_INDETERMINATE)
			break;
		agent_metadata_txn_work_charge(1);
	}
	agent_metadata_txn_unlock();
	return processed;

out_snapshot_error:
	proc_vm_snapshot_end(p);
out_txn_error:
	agent_metadata_txn_unlock();
	return -1;
}

int
sys_agent_file_meta_set_batch(uint64 itemsaddr, uint64 statusesaddr,
			      int count, uint64 flags)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	int result;

	if (p == 0 || !p->is_agent)
		return -1;
	if (flags != 0 || count < 0 || count > AGENT_FILE_META_BATCH_MAX)
		return -1;
	if (count == 0)
		return 0;
	if (itemsaddr >= MAXVA || statusesaddr >= MAXVA ||
	    (uint64)count * sizeof(struct agent_file_meta) > MAXVA - itemsaddr ||
	    (uint64)count * sizeof(int) > MAXVA - statusesaddr)
		return -1;
	lifecycle = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    workflow_lifecycle_operation_enter(lifecycle) < 0)
		return -1;
	result = agent_file_meta_set_batch_execute(
		p, itemsaddr, statusesaddr, count);
	workflow_lifecycle_operation_leave(lifecycle);
	return result;
}

static int agent_file_query_execute(struct proc *p, uint64 queryaddr,
				    uint64 resultaddr)
{
	struct agent_file_query query;
	struct agent_file_query_result result;
	int returned;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&query, queryaddr,
		   sizeof(query)) < 0)
		return -1;
#define TERMINATE_QUERY_FIELD(field) \
	query.field[sizeof(query.field) - 1] = 0
	TERMINATE_QUERY_FIELD(physical_name);
	TERMINATE_QUERY_FIELD(logical_path);
	TERMINATE_QUERY_FIELD(project);
	TERMINATE_QUERY_FIELD(workflow);
	TERMINATE_QUERY_FIELD(run_id);
	TERMINATE_QUERY_FIELD(stage);
	TERMINATE_QUERY_FIELD(kind);
	TERMINATE_QUERY_FIELD(status);
	TERMINATE_QUERY_FIELD(summary_contains);
#undef TERMINATE_QUERY_FIELD
	if (user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0)
		return -1;
	if (!agent_metadata_query_has_filter(&query))
		return AGENT_STATUS_BAD_PARAM;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	returned = agent_file_query_internal(agent_identity_proc_scope(p), &query,
					     &result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0) {
		returned = -1;
		goto out_lane;
	}
	if (returned < 0)
		goto out_lane;
	(void)agent_provenance_merge_current(
		p, AGENT_PROVENANCE_UNTRUSTED_FILE_DATA);
	(void)agent_context_append_system(
		p, AGENT_TOOL_QUERY_FILE, 0, query.flags,
		query.status[0] ? query.status : query.stage,
		result.returned ? result.hits[0].physical_name : "empty",
		AGENT_STATUS_OK, result.total_hits, result.scanned_records,
		result.used_index);
out_lane:
	agent_lifecycle_context_lane_leave(p);
	return returned;
}

int
sys_agent_file_query(uint64 queryaddr, uint64 resultaddr)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	int result;

	if (p == 0 || !p->is_agent)
		return -1;
	lifecycle = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    workflow_lifecycle_operation_enter(lifecycle) < 0)
		return -1;
	result = agent_file_query_execute(p, queryaddr, resultaddr);
	workflow_lifecycle_operation_leave(lifecycle);
	return result;
}

int
agent_metadata_tool_enter(int tool_id)
{
	if (tool_id == AGENT_TOOL_QUERY_FILE)
		return AGENT_METADATA_TOOL_READ_VIEW;
	if (!agent_tool_uses_file_metadata(tool_id))
		return 0;
	if (!agent_metadata_txn_lock(1))
		return -1;
	return 1;
}

void
agent_metadata_tool_exit(int locked)
{
	if (locked == 1)
		agent_metadata_txn_unlock();
}

void
agent_metadata_fill_info(uint scope_id, struct agent_info *info)
{
	if (info == 0)
		return;
	(void)scope_id;
	info->metadata_writeback_dirty = 0;
	info->metadata_writeback_durable = 0;
	info->metadata_writeback_requests = 0;
	info->metadata_writeback_coalesced = 0;
	info->metadata_writeback_commits = 0;
	info->metadata_writeback_pending = 0;
	info->file_scan_runs = 0;
	info->file_scan_entries = 0;
	info->file_scan_added = 0;
	info->file_scan_updated = 0;
	info->file_scan_removed = 0;
	info->file_scan_generation = 0;
	info->file_scan_pending = 0;
	info->file_scan_deferred = 0;
	info->file_scan_failures = 0;
	agent_file_state_fill_info(info);
}
