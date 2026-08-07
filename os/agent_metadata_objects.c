#include "agent.h"
#include "agent_context.h"
#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_actions.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_query.h"
#include "agent_metadata_recovery.h"
#include "agent_metadata_scan.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "trap.h"
#include "vfs_security.h"

static void agent_text_append(char *dst, int n, const char *src);
static void agent_file_catalog_sync(const struct agent_catalog_delta *);
static int agent_file_store_complete(struct agent_metadata_store_commit *, int);

#define AGENT_QUERY_SNAPSHOT_RETRIES 2
#define AGENT_METADATA_TOOL_READ_VIEW 2

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
	agent_metadata_scan_init();
	agent_metadata_actions_init();
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
	if (agent_metadata_recovery_retryable(result))
		agent_metadata_store_defer_boot_reprobe(result);
	else if (result < 0)
		agent_metadata_store_fail_closed_at_boot();
}

int
agent_metadata_durable_status(void)
{
	if (agent_metadata_store_available() &&
	    agent_metadata_store_loaded())
		return AGENT_STATUS_OK;
	return agent_metadata_recovery_pending() ?
		       AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;
}

int
agent_metadata_admission_status(void)
{
	int status = agent_metadata_durable_status();

	if (status != AGENT_STATUS_OK)
		return status;
	return agent_metadata_catalog_reconcile_pending() ?
		       AGENT_STATUS_RETRY : AGENT_STATUS_OK;
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
	agent_metadata_actions_generation_advance();
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

static void
agent_file_store_boot_reprobe(void)
{
	int result = AGENT_METADATA_LOAD_BUSY;

	if (!agent_metadata_recovery_pending())
		return;
	if (!agent_metadata_recovery_due(agent_file_state_now()))
		return;
	if (!bio_background_begin(FS_OWNER_SYSTEM))
		goto complete;
	if (!agent_metadata_txn_try_external())
		goto out_io;
	result = agent_file_store_reload(VFS_SCOPE_NONE);
	agent_metadata_txn_unlock();
out_io:
	bio_background_end();
complete:
	agent_metadata_store_boot_reprobe_complete(result);
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
	if (agent_metadata_catalog_restore(
		    fence, undo, previous, previous_scope, had_previous) < 0)
		return -1;
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
	agent_metadata_store_fail_closed_runtime();
	return AGENT_STATUS_INDETERMINATE;
}

static int
agent_persist_agent_status(const struct agent_metadata_persist_result *persist)
{
	if (persist == 0 || persist->durable)
		return AGENT_STATUS_OK;
	if (persist->irrevocable)
		return AGENT_STATUS_INDETERMINATE;
	switch (persist->cause) {
	case AGENT_METADATA_PERSIST_DURABILITY:
		return AGENT_STATUS_DURABILITY;
	case AGENT_METADATA_PERSIST_IO:
	case AGENT_METADATA_PERSIST_FAIL_CLOSED:
		return AGENT_STATUS_IO_ERROR;
	case AGENT_METADATA_PERSIST_INTERRUPTED:
	case AGENT_METADATA_PERSIST_RECOVERY:
	case AGENT_METADATA_PERSIST_RETRY:
	default:
		return AGENT_STATUS_RETRY;
	}
}

static int
agent_metadata_load_agent_status(int load_status)
{
	return load_status == AGENT_METADATA_LOAD_INTERRUPTED ||
	       load_status == AGENT_METADATA_LOAD_BUSY ||
	       load_status == AGENT_METADATA_LOAD_PROGRESS ?
		       AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;
}

static char *
agent_persist_error_text(const struct agent_metadata_persist_result *persist)
{
	int status = agent_persist_agent_status(persist);

	if (status == AGENT_STATUS_INDETERMINATE)
		return "metadata_commit_indeterminate";
	if (status == AGENT_STATUS_DURABILITY)
		return "metadata_durability_unsupported";
	if (status == AGENT_STATUS_IO_ERROR)
		return "metadata_io_error";
	return "metadata_retry";
}

static int
agent_file_finish_mutation(struct proc *p, struct agent_file_meta *request,
			   struct agent_file_meta *previous, uint scope_id,
			   uint previous_scope,
			   const struct agent_catalog_mutation_fence *fence,
			   const struct agent_catalog_undo_token *undo,
			   int had_previous,
			   int persistent, uint64 audit_fid,
			   char *success_text, char *event_payload)
{
	struct agent_metadata_persist_result persistence;
	int result;

	memset(&persistence, 0, sizeof(persistence));
	if (persistent) {
		persistence.completion_token =
			agent_metadata_store_mark_dirty(scope_id);
		if (persistence.completion_token == 0) {
			result = agent_file_restore_status(
				fence, undo, previous, previous_scope,
				had_previous, AGENT_STATUS_RETRY);
			agent_direct_effect_audit(
				p, AGENT_TOOL_FILE_META_INIT, result,
				"metadata_queue_full", audit_fid,
				request->update_mask, 0, request->flags, 1);
			agent_file_request_scan();
			return result;
		}
#if defined(AGENT_METADATA_CRASH_PHASE) || defined(AGENT_METADATA_EIO_PHASE)
		/* 故障注入以系统调用为边界，持久化阶段各自留下回执。 */
		if (agent_metadata_store_persist_commit(&persistence) < 0) {
			if (!persistence.irrevocable &&
			    agent_file_restore_status(fence, undo, previous,
					      previous_scope,
					      had_previous, 0) ==
				    AGENT_STATUS_INDETERMINATE) {
				persistence.cause =
					AGENT_METADATA_PERSIST_FAIL_CLOSED;
				persistence.irrevocable = 1;
			}
			result = agent_persist_agent_status(&persistence);
			agent_direct_effect_audit(
				p, AGENT_TOOL_FILE_META_INIT, result,
				agent_persist_error_text(&persistence),
				audit_fid, request->update_mask,
				persistence.completion_token, request->flags, 1);
			agent_file_request_scan();
			return result;
		}
#endif
	}
	if (event_payload)
		agent_ipc_deliver_watchers(p, AGENT_EVENT_FILE_STATUS,
			request->fid, p->context_path_latest, event_payload);
	agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT, AGENT_STATUS_OK,
		success_text, audit_fid, request->update_mask,
		persistence.completion_token, request->flags, 1);
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
	uint64 now;
	uint changes;
	int plan;
	int load_ok = 1;

	now = agent_file_state_now();
	vfs_scope_reap_pending(now);
	agent_file_store_boot_reprobe();
	if (!agent_metadata_store_available())
		return;
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
	if (ip->valid && (ip->vfs_policy != VFS_POLICY_WORKFLOW ||
			  ip->type != T_FILE))
		return 0;
	if (ivalid(ip) < 0)
		return 0;
	return vfs_inode_label_valid(ip) &&
	       ip->vfs_policy == VFS_POLICY_WORKFLOW && ip->type == T_FILE &&
	       agent_object_scope_valid(ip->vfs_scope_id);
}

static int agent_file_path_autopersist(uint scope_id, char *path,
				       struct agent_file_meta *binding)
{
	struct inode *ip;
	int eligible;
	int lookup_status;
	int result;

	if (!agent_scope_valid(scope_id) || path == 0 || path[0] == 0 ||
	    agent_file_is_meta_store_name(path))
		return 0;
	ip = namei_scope_status(path, VFS_POLICY_WORKFLOW, scope_id,
			       &lookup_status);
	if (ip == 0)
		return lookup_status == FS_LOOKUP_ABSENT ? 0 :
		       agent_lookup_error_status(lookup_status);
	result = ivalid(ip);
	if (result < 0) {
		iput(ip);
		return agent_lookup_error_status(result);
	}
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

void agent_metadata_note_catalog_changes(uint changes)
{
	agent_metadata_actions_note_changes(changes);
}

int agent_metadata_durability_fence_current(void)
{
	struct proc *p = curr_proc();
	uint scope_id;

	if (p == 0 || !p->is_agent)
		return 0;
	scope_id = agent_identity_proc_scope(p);
	if (!agent_scope_valid(scope_id))
		return -1;
	return agent_metadata_store_durability_fence(scope_id);
}

int agent_metadata_quiescence_fence_current(void)
{
	struct proc *p = curr_proc();
	uint scope_id;

	if (p == 0 || !p->is_agent)
		return 0;
	scope_id = agent_identity_proc_scope(p);
	if (!agent_scope_valid(scope_id))
		return -1;
	return agent_metadata_store_quiescence_fence(scope_id);
}

int agent_scope_reclaim_begin(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint64 *metadata_target)
{
	struct workflow_lifecycle_key current = workflow_lifecycle_none();
	int result;
	int changed;
	int reclaimed;
	int metadata_available;

	if (!agent_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    vfs_scope_lifecycle(scope_id, &current) < 0 ||
	    !workflow_lifecycle_key_equal(current, lifecycle) ||
	    metadata_target == 0)
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
	changed = metadata_available ?
		agent_metadata_store_shadow_has_scope(scope_id) : 0;
	if (metadata_available) {
		reclaimed = agent_metadata_catalog_reclaim_scope(scope_id);
		if (reclaimed < 0) {
			result = -1;
			goto out_txn;
		}
		if (reclaimed > 0)
			changed = 1;
	}
	agent_metadata_actions_reclaim_scope(scope_id);
	agent_metadata_query_invalidate_locked(scope_id, 0);
	if (agent_observe_scope_reclaim(scope_id) < 0) {
		result = -1;
		goto out_txn;
	}
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
		agent_metadata_store_expedite(scope_id);
	}
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int agent_scope_reclaim_metadata_done(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint64 metadata_target)
{
	struct workflow_lifecycle_key current = workflow_lifecycle_none();

	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    vfs_scope_lifecycle(scope_id, &current) < 0 ||
	    !workflow_lifecycle_key_equal(current, lifecycle))
		return 0;
	return agent_metadata_store_scope_target_done(scope_id, metadata_target);
}

static void agent_file_query_reset(struct agent_file_query_result *result)
{
	memset(result, 0, sizeof(*result));
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
	if (agent_metadata_store_available() &&
	    agent_metadata_store_loaded()) {
		for (int attempt = 0;
		     attempt < AGENT_QUERY_SNAPSHOT_RETRIES; attempt++) {
			result = agent_metadata_query_execute_snapshot(
				scope_id, q, r);
			if (result >= 0 && agent_metadata_store_available() &&
			    agent_metadata_store_loaded())
				return result;
			if (result == AGENT_CATALOG_CONFLICT) {
				agent_file_query_reset(r);
				return AGENT_STATUS_RETRY;
			}
			if (result != AGENT_CATALOG_STALE) {
				agent_file_query_reset(r);
				return AGENT_STATUS_RETRY;
			}
		}
		agent_file_query_reset(r);
		return AGENT_STATUS_RETRY;
	}
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	r->plan = AGENT_FILE_QUERY_PLAN_SCAN;
	r->index_bucket = -1;
	result = agent_file_store_load();
	if (result < 0) {
		result = agent_metadata_load_agent_status(result);
		goto out_txn;
	}
	result = agent_metadata_query_execute_locked(
		scope_id, q, r, agent_metadata_scan_query_stable());
out_txn:
	agent_metadata_txn_unlock();
	if (result < 0)
		agent_file_query_reset(r);
	return result;
}

static int agent_file_find(uint scope_id, char *selector)
{
	struct agent_catalog_view view;
	int result;

	result = agent_file_store_load();
	if (result < 0)
		return agent_metadata_load_agent_status(result);
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

static int
agent_file_summary_read(uint scope_id, char *selector,
			struct agent_result *res)
{
	struct agent_catalog_view view;
	int slot = agent_file_find(scope_id, selector);

	if (slot < 0)
		return slot;
	if (agent_file_read_slot(slot, &view) <= 0 ||
	    view.scope_id != scope_id)
		return AGENT_STATUS_IO_ERROR;
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

	if (selector[0] == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(physical, 0, n);
	if (agent_metadata_catalog_field_contains(selector, "=") ||
	    agent_metadata_catalog_field_contains(selector, ":")) {
		if (agent_metadata_query_from_payload(&query, selector) < 0)
			return AGENT_STATUS_BAD_PARAM;
		if (!agent_metadata_query_has_filter(&query))
			return AGENT_STATUS_BAD_PARAM;
		found = agent_file_store_load();
		if (found < 0)
			return agent_metadata_load_agent_status(found);
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
	if (agent_file_is_meta_store_name(physical)) {
		agent_result_status(res, AGENT_STATUS_DENIED, "denied");
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
	int propagate_dependencies,
	struct agent_metadata_persist_result *persist)
{
	struct agent_catalog_mutation_fence mutation_fence;
	int updated = 0;
	int load_status;

	memset(&mutation_fence, 0, sizeof(mutation_fence));
	memset(persist, 0, sizeof(*persist));
	persist->durable = 1;
	agent_metadata_txn_require_owned(1, "Agent metadata action transaction");
	if (!agent_metadata_store_submit_wait_locked()) {
		persist->durable = 0;
		persist->status = -1;
		persist->cause = agent_metadata_store_available() ?
			AGENT_METADATA_PERSIST_RETRY :
			AGENT_METADATA_PERSIST_FAIL_CLOSED;
		return 0;
	}
	load_status = agent_file_store_load();
	if (load_status < 0)
		return agent_metadata_load_agent_status(load_status);
	if (agent_metadata_catalog_mutation_begin(&mutation_fence) < 0) {
		persist->durable = 0;
		persist->status = -1;
		persist->cause = AGENT_METADATA_PERSIST_RETRY;
		return 0;
	}
	updated = agent_metadata_actions_update_status_locked(
		scope_id, stage, project, run_id, status, summary,
		dependency_mask, propagate_dependencies, persist);
	if (agent_metadata_catalog_mutation_end(&mutation_fence) < 0) {
		agent_metadata_store_fail_closed_runtime();
		persist->durable = 0;
		persist->status = -1;
		persist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED;
		persist->irrevocable = 1;
	}
	return updated;
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
	struct agent_metadata_persist_result persistence;
	struct agent_object_selector selector;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

	action_tool_id = history_tool_id ? history_tool_id : op->tool_id;
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
		agent_identity_proc_scope(p), action_tool_id, selector.project,
		selector.run_id, selector.label, op->request_id)) {
		agent_result_status(res, AGENT_STATUS_DUPLICATE, "duplicate");
		return;
	}
	updated = agent_file_update_status_batch_locked(
		agent_identity_proc_scope(p), selector.label, selector.project,
		selector.run_id, "ok", summary, deps, propagate_deps,
		&persistence);
	if (persistence.status < 0 || !persistence.durable) {
		res->status = agent_persist_agent_status(&persistence);
		res->value0 = persistence.completion_token;
		res->value1 = persistence.job_id;
		agent_result_text(res, agent_persist_error_text(&persistence));
		return;
	}
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
		agent_identity_proc_scope(p), action_tool_id, selector.project,
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
		tool_id <= AGENT_TOOL_WRITE_REPORT &&
		tool_id != AGENT_TOOL_READ_MESSAGE &&
		tool_id != AGENT_TOOL_CAPABILITY_CHECK) ||
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

int
agent_metadata_execute_tool(struct proc *p, struct agent_op *op,
			    struct agent_result *res)
{
	struct inode *ip;
	struct agent_file_query query;
	struct agent_file_query_result query_result;
	struct agent_object_selector dependency;
	int found;
	int lookup_status;

	switch (op->tool_id) {
	case AGENT_TOOL_QUERY_FILE:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_META_READ))
			break;
		if (op->payload[0] == 0) {
			agent_result_status(res, AGENT_STATUS_BAD_PARAM,
					    "path_required");
			break;
		}
		if (agent_metadata_catalog_field_contains(op->payload, "=") ||
		    agent_metadata_catalog_field_contains(op->payload, ":")) {
			if (agent_metadata_query_from_payload(&query,
						      op->payload) < 0) {
				agent_result_status(res, AGENT_STATUS_BAD_PARAM,
						    "bad_selector");
				break;
			}
			if (!agent_metadata_query_has_filter(&query)) {
				agent_result_status(res, AGENT_STATUS_BAD_PARAM,
						    "empty_selector");
				break;
			}
			found = agent_file_query_internal(agent_identity_proc_scope(p),
						       &query, &query_result);
			if (found < 0) {
				agent_result_status(res, found,
						    "metadata_unavailable");
				break;
			}
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
		ip = namei_scope_status(op->payload, VFS_POLICY_WORKFLOW,
				       agent_identity_proc_scope(p),
				       &lookup_status);
		if (ip == 0) {
			found = agent_lookup_error_status(lookup_status);
			agent_result_status(res, found,
				found == AGENT_STATUS_NOT_FOUND ?
					"file_not_found" : "metadata_unavailable");
			break;
		}
		lookup_status = ivalid(ip);
		if (lookup_status < 0) {
			iput(ip);
			agent_result_status(
				res, agent_lookup_error_status(lookup_status),
				"metadata_unavailable");
			break;
		}
		res->value0 = ip->type;
		res->value1 = ip->inum;
		res->value2 = ip->size;
		iput(ip);
		agent_result_text(res, "query_file");
		break;
	case AGENT_TOOL_FILE_META_INIT:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_META_WRITE))
			break;
		agent_metadata_actions_clear_history(
			agent_identity_proc_scope(p));
		res->value0 = agent_file_store_load();
		if ((long)res->value0 < 0) {
			agent_result_status(
				res, agent_metadata_load_agent_status(
					     (int)(long)res->value0),
					    "metadata_unavailable");
			break;
		}
		agent_file_request_scan();
		agent_result_text(res, "file_meta_init");
		break;
	case AGENT_TOOL_READ_FILE_SUMMARY:
		if (!agent_tool_require_cap(p, res, AGENT_CAP_CONTENT_READ))
			break;
		found = agent_file_summary_read(agent_identity_proc_scope(p),
					     op->payload, res);
		if (found < 0) {
			agent_result_status(
				res, found, found == AGENT_STATUS_NOT_FOUND ?
						    "summary_not_found" :
						    "metadata_unavailable");
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
		if (agent_parse_selector(op->payload, &dependency) < 0) {
			agent_result_status(res, AGENT_STATUS_BAD_PARAM,
					    "bad_selector");
			break;
		}
		found = agent_file_store_load();
		if (found < 0) {
			agent_result_status(res,
				agent_metadata_load_agent_status(found),
				"metadata_unavailable");
			break;
		}
		if (agent_metadata_actions_dependency_query(
			    agent_identity_proc_scope(p), dependency.label,
			    dependency.project, dependency.run_id, res) < 0) {
			agent_result_status(res, AGENT_STATUS_NOT_FOUND,
					    "dependency_not_found");
			break;
		}
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
	struct agent_metadata_persist_result persistence;
	int loaded;
	int result = AGENT_STATUS_NO_SPACE;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_metadata_store_submit_wait_locked()) {
		result = agent_metadata_store_available() ?
			AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;
		goto out_txn;
	}
	if (agent_metadata_store_loaded() &&
	    agent_metadata_store_scope_pending(agent_identity_proc_scope(p)) &&
	    agent_metadata_store_persist_commit(&persistence) != 0) {
		result = agent_persist_agent_status(&persistence);
		goto out_txn;
	}
	if (!agent_metadata_store_submit_wait_locked()) {
		result = agent_metadata_store_available() ?
			AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;
		goto out_txn;
	}
	loaded = agent_file_store_reload(agent_identity_proc_scope(p));
	if (loaded < 0) {
		result = agent_metadata_load_agent_status(loaded);
		goto out_txn;
	}
	agent_metadata_actions_clear_history(agent_identity_proc_scope(p));
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
	struct agent_catalog_mutation_fence mutation_fence;
	struct agent_catalog_undo_token undo;
	struct agent_catalog_resolution selector;
	struct agent_file_meta *working;
	uint scope_id;
	uint previous_scope = VFS_SCOPE_NONE;
	int slot = -1;
	int had_previous;
	int status_changed = 0;
	int auto_persist;
	int persistent;
	int commit_status;
	int bind_status;
	int identity_rebound = 0;
	int fence_active = 0;
	int result = AGENT_STATUS_NO_SPACE;
	uint changes = 0;
	uint64 audit_fid = 0;
	uint64 mask;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

	memset(&mutation_fence, 0, sizeof(mutation_fence));
	memset(&undo, 0, sizeof(undo));

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
	if (!agent_metadata_store_submit_wait_locked()) {
		result = agent_metadata_store_available() ?
			AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;
		goto out_txn;
	}
	commit_status = agent_file_store_load();
	if (commit_status < 0) {
		result = agent_metadata_load_agent_status(commit_status);
		goto out_txn;
	}
	agent_metadata_catalog_resolve(scope_id, &meta, -1, &selector);
	if (agent_metadata_catalog_mutation_begin(&mutation_fence) < 0) {
		result = AGENT_STATUS_RETRY;
		goto out_txn;
	}
	fence_active = 1;
	if (selector.states != 0) {
		result = (selector.states & AGENT_CATALOG_STATE_PENDING) ?
			 AGENT_STATUS_RETRY : AGENT_STATUS_CONFLICT;
		goto out_txn;
	}
	memset(&auto_binding, 0, sizeof(auto_binding));
	auto_persist = agent_file_path_autopersist(
		scope_id, meta.physical_name, &auto_binding);
	if (auto_persist < 0) {
		result = auto_persist;
		goto out_txn;
	}
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
		commit_status = agent_metadata_catalog_clear_slot(slot);
		if (commit_status < 0) {
			result = agent_catalog_error_status(commit_status);
			agent_direct_effect_audit(
				p, AGENT_TOOL_FILE_META_INIT, result,
				"meta_delete_sidecar_io", audit_fid, mask, 0,
				meta.flags, 1);
			goto out_txn;
		}
		if (agent_metadata_catalog_undo_capture(
			    &mutation_fence, slot, &undo) < 0) {
			agent_metadata_store_fail_closed_runtime();
			result = AGENT_STATUS_INDETERMINATE;
			goto out_txn;
		}
		agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
		result = agent_file_finish_mutation(
			p, &meta, &previous, scope_id, previous_scope,
			&mutation_fence, &undo,
			had_previous, 1, audit_fid, "meta_delete", 0);
		goto out_txn;
	}
	if (slot < 0) {
		slot = agent_metadata_catalog_alloc_slot(scope_id, 0);
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
	if (had_previous && auto_persist &&
	    (selector.matched & AGENT_CATALOG_KEY_PHYSICAL) &&
	    meta.dev == 0 &&
	    (working->dev != auto_binding.dev ||
	     working->inum != auto_binding.inum ||
	     working->incarnation != auto_binding.incarnation)) {
		working->dev = auto_binding.dev;
		working->inum = auto_binding.inum;
		working->incarnation = auto_binding.incarnation;
		working->size = auto_binding.size;
		identity_rebound = 1;
	}
	changes = agent_file_meta_patch_strings(working, &meta, mask,
						&status_changed);
	if (identity_rebound)
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
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
	commit_status = agent_metadata_catalog_edit_commit(&edit, changes);
	if (commit_status < 0) {
		result = agent_catalog_error_status(commit_status);
		goto out_txn;
	}
	working = 0;
	if (agent_metadata_catalog_undo_capture(
		    &mutation_fence, slot, &undo) < 0) {
		agent_metadata_store_fail_closed_runtime();
		result = AGENT_STATUS_INDETERMINATE;
		goto out_txn;
	}
	bind_status = agent_metadata_catalog_bind(slot, 1, p);
	if (bind_status < 0) {
		result = agent_file_restore_status(
			&mutation_fence, &undo, &previous, previous_scope,
			had_previous,
			bind_status == AGENT_CATALOG_INDETERMINATE ?
				AGENT_STATUS_INDETERMINATE : AGENT_STATUS_IO_ERROR);
		if (bind_status == AGENT_CATALOG_INDETERMINATE)
			agent_metadata_store_fail_closed_runtime();
		goto out_txn;
	}
	if (agent_metadata_catalog_undo_capture(
		    &mutation_fence, slot, &undo) < 0 ||
	    (bind_status > 0 && agent_metadata_catalog_undo_note_created(
				      &mutation_fence, &undo) < 0)) {
		agent_metadata_store_fail_closed_runtime();
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
	agent_metadata_scan_note_slot(slot);
	if (had_previous &&
	    strncmp(previous.physical_name, view.meta->physical_name,
		    sizeof(previous.physical_name)) != 0)
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	persistent = view.meta->flags & AGENT_FILE_META_F_PERSIST;
	audit_fid = view.meta->fid;
	agent_metadata_actions_format_file_event(
		view.meta, event_payload, sizeof(event_payload));
	view.meta = 0;
	if (changes)
		agent_metadata_note_catalog_changes(changes);
	result = agent_file_finish_mutation(
		p, &meta, &previous, scope_id, previous_scope,
		&mutation_fence, &undo,
		had_previous, persistent, audit_fid, "meta_set",
		status_changed && meta.status[0] ? event_payload : 0);
out_txn:
	if (fence_active &&
	    agent_metadata_catalog_mutation_end(&mutation_fence) < 0) {
		agent_metadata_store_fail_closed_runtime();
		result = AGENT_STATUS_INDETERMINATE;
	}
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_query(uint64 queryaddr, uint64 resultaddr)
{
	struct proc *p = curr_proc();
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
agent_metadata_tool_enter(int tool_id)
{
	if (tool_id == AGENT_TOOL_QUERY_FILE)
		return AGENT_METADATA_TOOL_READ_VIEW;
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
	if (locked == 1)
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
	agent_metadata_store_tick(now);
	if (agent_metadata_store_take_reconcile_request())
		agent_file_request_scan();
	if (agent_metadata_recovery_pending() &&
	    agent_metadata_recovery_due(now))
		agent_background_request();
	if (agent_metadata_scan_plan(now) != AGENT_METADATA_SCAN_IDLE)
		agent_background_request();
}
