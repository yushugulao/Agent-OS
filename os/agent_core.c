#include "agent.h"
#include "agent_context.h"
#include "agent_identity_lease.h"
#include "agent_internal.h"
#include "fs_epoch.h"
#include "agent_tool_protocol.h"
#include "agent_workflow_fence.h"
#include "bio.h"
#include "defs.h"
#include "kernel_work.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"

extern struct proc pool[NPROC];

static long agent_sched_score_at(struct thread *t, uint64 now,
				 struct agent_sched_record *record);

struct agent_proc_snapshot {
	int used;
	int agents;
	int runnable;
};

void agent_core_init(void)
{
	agent_tool_protocol_init();
	agent_identity_init();
	agent_lifecycle_init();
	agent_metadata_init();
	agent_identity_lease_init();
	agent_observe_init();
	agent_metadata_objects_init();
	agent_background_request();
}

void
agent_core_storage_init(void)
{
	/* Live metadata and identity generations are boot-epoch memory state. */
}

void
agent_background_maintain(void)
{
	struct thread *t = curr_thread();
	struct proc *p;

	/* 设备 I/O 可能休眠，后台维护必须借用可调度线程。 */
	if (t == 0 || t->tid < 0 || t->state != RUNNING || t->process == 0)
		return;
	p = t->process;
	/* 维护 I/O 不得递归地产生新的观测流量。 */
	if (agent_observe_recording_suppress_begin(p) < 0)
		return;
	(void)fs_deferred_reclaim_maintain();
	agent_metadata_background_maintain();
	agent_observe_recording_suppress_end(p);
}

static int
agent_core_admission_status(void)
{
	return agent_metadata_admission_status();
}

static int
agent_core_create_admission_status(void)
{
	return agent_core_admission_status();
}

void agent_background_checkpoint(void)
{
	struct thread *t = curr_thread();
	int epoch_due;
	int pending;

	if (t == 0 || t->tid < 0 || t->state != RUNNING || t->process == 0)
		return;
	pending = agent_background_take();
	epoch_due = fs_epoch_should_commit();
	if (!pending && !epoch_due)
		return;
	if (fs_epoch_request_begin() < 0) {
		if (pending)
			agent_background_request();
		return;
	}
	if (pending)
		agent_background_maintain();
	if (fs_epoch_should_commit())
		(void)fs_epoch_commit();
	fs_epoch_request_end();
}

void
agent_scope_controller_departing(struct proc *p)
{
	uint64 control_id;
	int enabled = intr_save();

	control_id = agent_lifecycle_controller_departing_locked(p);
	if (control_id != 0)
		agent_ipc_remove_source(control_id);
	intr_restore(enabled);
}

static uint64 agent_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int agent_text_empty(char *s)
{
	return s == 0 || s[0] == 0;
}

static void agent_core_proc_state_reset(struct proc *p)
{
	p->agent_meta_txn_wait_count = 0;
	p->resource_quota = 0;
}

void agent_core_clear_metadata(struct proc *p)
{
	int inactive;

	/* PUBLIC 槽不激活 Agent 状态面，拆除时只需清除身份边。 */
	inactive = p != 0 && !p->is_agent && p->agent_control_id == 0 &&
		   agent_context_is_empty(p);
	if (!inactive) {
		agent_context_proc_reset(p);
		agent_observe_proc_reset(p);
	}
	agent_ipc_proc_teardown(p);
	agent_identity_proc_reset(p, 0);
	if (!inactive)
		agent_core_proc_state_reset(p);
}

void agent_core_proc_prepare(struct proc *p)
{
	if (p == 0 || !proc_teardown_live(p) || p->is_agent ||
	    p->agent_control_id != 0 || p->agent_controller_id != 0 ||
	    !agent_context_is_empty(p))
		panic("Agent prepare outside live process");
	agent_ipc_proc_prepare(p);
}

void agent_core_proc_teardown(struct proc *p)
{
	if (p == 0 || p->teardown_state < PROC_TEARDOWN_QUIESCING ||
	    p->teardown_state > PROC_TEARDOWN_SETTLING)
		panic("Agent teardown phase");
	if (p->is_agent || p->agent_control_id != 0)
		agent_scope_controller_departing(p);
	if (p->teardown_state == PROC_TEARDOWN_QUIESCING)
		return;
	if (p->teardown_state == PROC_TEARDOWN_SETTLING) {
		if (p->is_agent || !agent_context_is_empty(p))
			panic("Agent teardown incomplete");
		return;
	}
	if (p->teardown_state != PROC_TEARDOWN_RECLAIMING)
		panic("Agent teardown transition");
	if (p->is_agent || !agent_context_is_empty(p))
		agent_free_proc_context(p);
	agent_core_clear_metadata(p);
}

int agent_core_exec_public_commit(struct proc *p)
{
	if (intr_get())
		panic("Agent exec identity commit unlocked");
	if (p == 0 || !proc_teardown_live(p))
		return -1;
	if (!p->is_agent) {
		if (p->agent_control_id != 0 || !agent_context_is_empty(p))
			return -1;
		agent_ipc_exec_public(p);
		return 0;
	}
	/* workflow 根节点不能放弃不可转移的关闭权限。 */
	if (p->vfs_scope_controller ||
	    !agent_lifecycle_context_lane_quiescent(p))
		return -1;
	agent_scope_controller_departing(p);
	if (!proc_teardown_live(p))
		panic("non-root Agent exec closed its lifecycle");
	agent_free_proc_context(p);
	agent_context_proc_reset(p);
	agent_observe_proc_reset(p);
	agent_ipc_exec_public(p);
	agent_identity_proc_reset(p, 1);
	agent_core_proc_state_reset(p);
	return 0;
}

static uint64 agent_cap_for_action(char *action)
{
	if (strncmp(action, "action_commit", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "state_update", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "rerun_stage", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_ACTION_WRITE;
	if (strncmp(action, "artifact_update", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "record_result", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "write_report", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_ARTIFACT_WRITE;
	if (strncmp(action, "llm_response", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "llm_relay", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_LLM_RELAY;
	if (strncmp(action, "llm_request", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_MESSAGE_SEND;
	if (strncmp(action, "query", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "query_file", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_meta", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_META_READ;
	if (strncmp(action, "read_file_summary", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_file_digest", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_content", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_CONTENT_READ;
	if (strncmp(action, "send_message", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_MESSAGE_SEND;
	if (strncmp(action, "watch", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_WATCH;
	if (strncmp(action, "meta_write", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "file_meta_write", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "dependency_update", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_META_WRITE;
	if (strncmp(action, "orchestrate", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_ORCHESTRATE;
	return 0;
}

static int agent_action_allowed(struct proc *p, char *action)
{
	uint64 cap = agent_cap_for_action(action);

	return cap != 0 && agent_identity_has_cap(p, cap);
}

void agent_sched_on_enqueue(struct thread *t)
{
	struct proc *p;

	if (t == 0 || t->process == 0)
		return;
	p = t->process;
	if (!p->is_agent)
		return;
	p->agent_sched_ready_tick = agent_ticks();
	if (p->agent_sched_policy == 0)
		p->agent_sched_policy = AGENT_SCHED_POLICY_ADAPTIVE;
	if (p->agent_sched_weight <= 0)
		p->agent_sched_weight = agent_identity_role_sched_weight(p->agent_role);
	if (p->agent_sched_budget == 0)
		p->agent_sched_budget = AGENT_SCHED_DEFAULT_BUDGET;
}

void agent_sched_on_yield(struct thread *t)
{
	struct proc *p;

	if (t == 0 || t->process == 0)
		return;
	p = t->process;
	if (!p->is_agent)
		return;
	p->agent_sched_preemptions++;
}

static int agent_sched_should_trace(struct proc *p,
				    struct agent_sched_record *record)
{
	uint64 important;

	if (p == 0 || record == 0)
		return 0;
	if (record->dispatch_count <= 4)
		return 1;
	important = AGENT_SCHED_REASON_EVENT_QUEUE |
		    AGENT_SCHED_REASON_DEADLINE_NEAR |
		    AGENT_SCHED_REASON_DEADLINE_NOW |
		    AGENT_SCHED_REASON_HEARTBEAT_DUE |
		    AGENT_SCHED_REASON_BUDGET_USED |
		    AGENT_SCHED_REASON_PRIORITY;
	if ((record->reason_flags & important) != 0)
		return 1;
	if ((record->dispatch_count & 0xf) == 0)
		return 1;
	return 0;
}

void agent_sched_on_dispatch(struct thread *t)
{
	struct proc *p;
	struct agent_ipc_sched_snapshot ipc;
	uint64 now;
	uint64 weight;
	uint64 cost;
	struct agent_sched_record record;
	long score;
	int trace;

	if (t == 0 || t->process == 0)
		return;
	p = t->process;
	if (!p->is_agent)
		return;
	now = agent_ticks();
	agent_ipc_thread_sched_snapshot(t, &ipc);
	score = agent_sched_score_at(t, now, &record);
	weight = p->agent_sched_weight > 0 ? p->agent_sched_weight : 50;
	cost = 1000 / weight;
	if (cost == 0)
		cost = 1;
	p->agent_sched_dispatch_count++;
	if (p->agent_event_count_queued > 0)
		p->agent_sched_event_dispatch_count++;
	if (ipc.wait_deadline_valid && ipc.wait_deadline <= now + 2)
		p->agent_sched_deadline_dispatch_count++;
	p->agent_sched_last_dispatch_tick = now;
	p->agent_sched_vruntime += cost;
	if (p->agent_sched_budget &&
	    p->agent_sched_budget_used >= p->agent_sched_budget)
		p->agent_sched_budget_used = 0;
	p->agent_sched_budget_used++;
	record.dispatch_count = p->agent_sched_dispatch_count;
	p->agent_sched_last_score = score > 0 ? (uint64)score : 0;
	p->agent_sched_last_reason = record.reason_flags;
	record.score = p->agent_sched_last_score;
	trace = agent_sched_should_trace(p, &record);
	if (trace)
		agent_observe_record_sched(t, &record);
}

static long agent_sched_score_at(struct thread *t, uint64 now,
				 struct agent_sched_record *record)
{
	struct proc *p;
	struct agent_ipc_sched_snapshot ipc;
	uint64 age;
	uint64 heartbeat_due = 0;
	long score;
	long penalty;
	uint64 reason = 0;

	if (t == 0 || t->state != RUNNABLE || t->process == 0)
		return -1000000;
	p = t->process;
	agent_ipc_thread_sched_snapshot(t, &ipc);
	if (!p->is_agent) {
		return 200 + (long)((now > p->agent_sched_ready_tick) ?
					    MIN(now - p->agent_sched_ready_tick,
						(uint64)80) :
					    0);
	}
	age = now > p->agent_sched_ready_tick ?
		      now - p->agent_sched_ready_tick :
		      0;
	if (age > 200)
		age = 200;
	score = 300 + p->agent_sched_weight * 4 + (long)age;
	reason |= AGENT_SCHED_REASON_ROLE_WEIGHT;
	if (p->agent_sched_priority != 0) {
		score += p->agent_sched_priority * 8;
		reason |= AGENT_SCHED_REASON_PRIORITY;
	}
	if (age > 0)
		reason |= AGENT_SCHED_REASON_READY_AGE;
	if (p->agent_event_count_queued > 0) {
		score += 900 + p->agent_event_count_queued * 20;
		reason |= AGENT_SCHED_REASON_EVENT_QUEUE;
	}
	if (ipc.loop_state == AGENT_LOOP_WAITING) {
		score += 180;
		reason |= AGENT_SCHED_REASON_WAITING;
	}
	if (ipc.wait_deadline_valid) {
		if (ipc.wait_deadline <= now + 2) {
			score += 700;
			reason |= AGENT_SCHED_REASON_DEADLINE_NOW;
		} else if (ipc.wait_deadline <= now + 8) {
			score += 250;
			reason |= AGENT_SCHED_REASON_DEADLINE_NEAR;
		}
	}
	if (p->heartbeat_interval > 0 &&
	    now - p->agent_last_heartbeat_tick >=
		    (uint64)p->heartbeat_interval) {
		score += 260;
		heartbeat_due = 1;
		reason |= AGENT_SCHED_REASON_HEARTBEAT_DUE;
	}
	if (p->agent_sched_budget &&
	    p->agent_sched_budget_used >= p->agent_sched_budget) {
		score -= 160;
		reason |= AGENT_SCHED_REASON_BUDGET_USED;
	}
	penalty = p->agent_sched_vruntime > 4000 ?
			  500 :
			  (long)(p->agent_sched_vruntime / 8);
	if (penalty > 0)
		reason |= AGENT_SCHED_REASON_VRUNTIME;
	score -= penalty;
	if (record) {
		memset(record, 0, sizeof(*record));
		record->tick = now;
		record->score = score > 0 ? (uint64)score : 0;
		record->reason_flags = reason;
		record->event_queue_count = p->agent_event_count_queued;
		record->ready_age = age;
		if (ipc.wait_deadline_valid)
			record->deadline_delta =
				ipc.wait_deadline > now ?
					ipc.wait_deadline - now :
					0;
		record->heartbeat_due = heartbeat_due;
		record->vruntime = p->agent_sched_vruntime;
		record->budget_used = p->agent_sched_budget_used;
		record->pid = p->pid;
		record->tid = t->tid;
		record->role = p->agent_role;
		record->loop_state = ipc.loop_state;
		record->weight = p->agent_sched_weight;
		record->priority = p->agent_sched_priority;
	}
	return score;
}

static long agent_sched_score(struct thread *t)
{
	return agent_sched_score_at(t, agent_ticks(), 0);
}

int agent_sched_better(struct thread *a, struct thread *b)
{
	struct proc *pa;
	struct proc *pb;
	long sa = agent_sched_score(a);
	long sb = agent_sched_score(b);

	if (sa != sb)
		return sa > sb;
	if (a == 0)
		return 0;
	if (b == 0)
		return 1;
	pa = a->process;
	pb = b->process;
	if (pa && pb && pa->is_agent && pb->is_agent &&
	    pa->agent_sched_vruntime != pb->agent_sched_vruntime)
		return pa->agent_sched_vruntime < pb->agent_sched_vruntime;
	if (pa && pb && pa->agent_sched_ready_tick != pb->agent_sched_ready_tick)
		return pa->agent_sched_ready_tick < pb->agent_sched_ready_tick;
	return task_to_id(a) < task_to_id(b);
}

static int agent_workflow_admission_status(struct proc *p, int role)
{
	int status = agent_identity_authority_check(p, role);

	if (status != AGENT_STATUS_OK)
		return status;
	if (p == 0 || p->is_agent || !p->resource_domain_admin ||
	    !exec_policy_process_bootstrap(p))
		return AGENT_STATUS_DENIED;
	status = vfs_scope_fresh_admission_status();
	if (status != AGENT_STATUS_OK)
		return status;
	return AGENT_STATUS_OK;
}

int agent_make_role(struct proc *p, int role)
{
	const struct agent_role_policy *policy = agent_identity_role_policy(role);
	struct proc *controller = curr_proc();
	uint64 control_id;
	uint64 controller_id = 0;

	if (policy == 0 || p->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !exec_policy_process_allows_role(p, role))
		return -1;
	if (controller != 0 && controller->is_agent &&
	    agent_identity_proc_scope(controller) == p->vfs_scope_id) {
		if (controller->agent_control_id == 0)
			return -1;
		controller_id = controller->agent_control_id;
	}
	if (p->max_page * PAGE_SIZE > AGENT_CONTEXT_BASE)
		return -1;
	control_id = agent_lifecycle_alloc_control_id();
	if (control_id == 0)
		return -1;
	if (agent_context_alloc(p) < 0)
		return -1;
	if (agent_context_map(p) < 0)
		goto fail;
	p->is_agent = 1;
	p->agent_type = AGENT_TYPE_AGENT;
	p->agent_id = agent_identity_alloc_id();
	if (p->agent_id == 0)
		goto fail;
	p->agent_role = role;
	p->agent_control_id = control_id;
	p->agent_controller_id = controller_id;
	agent_context_proc_activate(p);
	p->resource_quota = AGENT_CONTEXT_MAX_RECORDS;
	agent_ipc_proc_activate(p);
	p->agent_meta_txn_wait_count = 0;
	p->agent_capability_mask = policy->capability_mask;
	p->agent_role_grant_mask = policy->role_grant_mask;
	vfs_proc_limit_capabilities(p, policy->capability_mask);
	agent_observe_proc_init(p, policy->sched_weight, agent_ticks());
	if (agent_context_init(p) < 0)
		goto fail;
	if (p->vfs_scope_controller &&
	    vfs_scope_bind_controller(p->vfs_scope_id,
				      vfs_proc_lifecycle(p),
				      p->agent_control_id) < 0)
		goto fail;
	return 0;

fail:
	agent_free_proc_context(p);
	agent_core_clear_metadata(p);
	return -1;
}

static __attribute__((noinline)) void agent_info_fill(struct proc *p, struct agent_info *info)
{
	memset(info, 0, sizeof(*info));
	info->is_agent = p->is_agent;
	info->agent_id = p->agent_id;
	info->agent_role = p->agent_role;
	info->context_base = p->agent_ctx_base;
	info->context_size = p->agent_ctx_base == 0 ? 0 : AGENT_CONTEXT_SIZE;
	info->agent_type = p->agent_type;
	info->heartbeat_interval = p->heartbeat_interval;
	info->resource_quota = p->resource_quota;
	info->loop_state = p->loop_state;
	info->agent_call_count = p->agent_call_count;
	info->metadata_txn_wait_count = p->agent_meta_txn_wait_count;
	agent_metadata_fill_info(agent_identity_proc_scope(p), info);
	info->context_path_count = p->context_path_count;
	info->context_path_capacity = p->context_path_capacity;
	info->context_path_head = p->context_path_head;
	info->context_path_oldest = p->context_path_oldest;
	info->context_path_latest = p->context_path_latest;
	info->context_path_dropped =
		p->agent_call_count - p->context_path_count;
	info->context_path_rollback_count = p->context_path_rollback_count;
	info->latest_response_offset = AGENT_CONTEXT_LATEST_RESPONSE_OFFSET;
	info->records_offset = AGENT_CONTEXT_RECORDS_OFFSET;
	info->event_count = p->agent_event_count;
	info->event_dropped = p->agent_event_dropped;
	info->event_queue_count = p->agent_event_count_queued;
	info->watch_count = p->agent_watch_count;
	info->wait_count = p->agent_wait_count;
	info->wait_loop_count = p->agent_wait_loop_count;
	info->wait_sleep_count = p->agent_wait_sleep_count;
	info->wait_wakeup_count = p->agent_wait_wakeup_count;
	info->wait_cancel_count = p->agent_wait_cancel_count;
	info->timeout_count = p->agent_timeout_count;
	info->last_heartbeat_tick = p->agent_last_heartbeat_tick;
	info->current_tick = agent_ticks();
	info->capability_mask = agent_identity_proc_scope(p) != VFS_SCOPE_NONE ?
				p->agent_capability_mask : 0;
	info->sched_policy = p->agent_sched_policy;
	info->sched_weight = p->agent_sched_weight;
	info->sched_priority = p->agent_sched_priority;
	info->sched_budget = p->agent_sched_budget;
	info->sched_dispatch_count = p->agent_sched_dispatch_count;
	info->sched_event_dispatch_count = p->agent_sched_event_dispatch_count;
	info->sched_deadline_dispatch_count =
		p->agent_sched_deadline_dispatch_count;
	info->sched_vruntime = p->agent_sched_vruntime;
	info->sched_ready_tick = p->agent_sched_ready_tick;
	info->sched_last_dispatch_tick = p->agent_sched_last_dispatch_tick;
	info->sched_preemptions = p->agent_sched_preemptions;
	info->sched_budget_used = p->agent_sched_budget_used;
	info->sched_last_score = p->agent_sched_last_score;
	info->sched_last_reason = p->agent_sched_last_reason;
	info->sched_trace_count = p->agent_sched_trace_count;
	info->current_span_id = p->agent_current_span_id;
	info->current_cause_sequence = p->agent_current_cause_sequence;
	info->provenance_edges = p->agent_provenance_edges;
	info->observe_epoch = agent_observe_scope_epoch(
		agent_identity_proc_scope(p));
	info->timeline_wait_count = p->agent_timeline_wait_count;
	info->timeline_wait_sleep_count = p->agent_timeline_wait_sleep_count;
	info->timeline_wait_wakeup_count = p->agent_timeline_wait_wakeup_count;
	info->timeline_wait_timeout_count = p->agent_timeline_wait_timeout_count;
	info->filesystem_domain = p->vfs_scope_id;
	info->filesystem_capability_mask =
		vfs_scope_active(p->vfs_scope_id) ? p->vfs_effective_caps : 0;
}

static void agent_result_text(struct agent_result *res, char *text)
{
	safestrcpy(res->result, text, sizeof(res->result));
}

static void agent_result_init(struct agent_result *res, struct agent_op *op)
{
	memset(res, 0, sizeof(*res));
	res->version = AGENT_CALL_VERSION;
	res->status = AGENT_STATUS_OK;
	res->tool_id = op->tool_id;
	res->request_id = op->request_id;
}

static void agent_collect_proc_snapshot(struct proc *requester,
					int filter_agents,
					struct agent_proc_snapshot *snapshot)
{
	struct proc *pp;
	uint observed_scope;

	memset(snapshot, 0, sizeof(*snapshot));
	for (pp = pool; pp < &pool[NPROC]; pp++) {
		if (requester && requester->is_agent) {
			observed_scope =
				pp->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
					pp->vfs_scope_id :
					pp->vfs_pending_scope_id;
			if (observed_scope != agent_identity_proc_scope(requester))
				continue;
		}
		if (pp->state != P_UNUSED) {
			if (!filter_agents || pp->is_agent) {
				snapshot->used++;
				if (pp->is_agent)
					snapshot->agents++;
				for (int i = 0; i < NTHREAD; i++) {
					if (pp->threads[i].state == RUNNABLE ||
					    pp->threads[i].state == RUNNING) {
						snapshot->runnable++;
						break;
					}
				}
			}
		}
	}
}

static void agent_execute_op(struct proc *p, struct agent_op *op,
			     struct agent_result *res)
{
	struct agent_proc_snapshot snapshot;
	uint64 configured_tick;
	int delivered;
	int delivery_status;
	int metadata_locked;

	if (op->version != AGENT_CALL_VERSION) {
		res->status = AGENT_STATUS_BAD_REQUEST;
		agent_result_text(res, "bad_request");
		return;
	}
	metadata_locked = agent_metadata_tool_enter(op->tool_id);
	if (metadata_locked < 0) {
		res->status = AGENT_STATUS_NO_SPACE;
		agent_result_text(res, "metadata_busy");
		return;
	}
	if (metadata_locked > 0 &&
	    agent_metadata_execute_tool(p, op, res))
		goto out;

	switch (op->tool_id) {
	case AGENT_TOOL_ECHO:
		res->value0 = strlen(op->payload);
		res->value1 = op->arg0;
		res->value2 = op->arg1;
		agent_result_text(res, op->payload);
		break;
	case AGENT_TOOL_PID_INFO:
		res->value0 = p->pid;
		res->value1 = p->agent_id;
		res->value2 = p->is_agent;
		agent_result_text(res, "pid_info");
		break;
	case AGENT_TOOL_CTX_STAT:
		res->value0 = p->agent_ctx_base;
		res->value1 = p->agent_ctx_base == 0 ? 0 : AGENT_CONTEXT_SIZE;
		res->value2 = p->agent_call_count;
		agent_result_text(res, "ctx_stat");
		break;
	case AGENT_TOOL_QUERY_PROCESS:
		if (!agent_identity_has_cap(p, AGENT_CAP_PROCESS_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_collect_proc_snapshot(p,
					    op->arg0 == AGENT_TYPE_AGENT,
					    &snapshot);
		res->value0 = snapshot.used;
		res->value1 = snapshot.agents;
		res->value2 = snapshot.runnable;
		agent_result_text(res, "query_process");
		break;
	case AGENT_TOOL_GET_SYSTEM_STATUS:
		if (!agent_identity_has_cap(p, AGENT_CAP_PROCESS_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_collect_proc_snapshot(p, 0, &snapshot);
		res->value0 = snapshot.used;
		res->value1 = snapshot.agents;
		res->value2 = agent_ticks();
		agent_result_text(res, "system_status");
		break;
	case AGENT_TOOL_READ_CONTEXT:
		res->value0 = p->context_path_count < p->context_path_capacity ?
				      p->context_path_count + 1 :
				      p->context_path_capacity;
		res->value1 = p->context_path_capacity ?
				      (p->context_path_head + 1) %
					      p->context_path_capacity :
				      0;
		res->value2 = p->agent_call_count;
		agent_result_text(res, "read_context");
		break;
	case AGENT_TOOL_SEND_MESSAGE:
		if (!agent_identity_has_cap(p, AGENT_CAP_MESSAGE_SEND)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 == 0 || op->arg0 > 0x7fffffffULL ||
		    agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "message_required");
			break;
		}
		delivery_status = agent_ipc_deliver_pid(
			(int)op->arg0, p, AGENT_EVENT_MESSAGE, op->request_id,
			p->agent_call_count + 1, op->payload, 1, &delivered);
		if (delivery_status != AGENT_STATUS_OK) {
			res->status = delivery_status;
			if (delivery_status == AGENT_STATUS_DENIED)
				agent_result_text(res, "route_denied");
			else if (delivery_status == AGENT_STATUS_NO_SPACE)
				agent_result_text(res, "event_queue_full");
			else
				agent_result_text(res, "target_missing");
			break;
		}
		res->value0 = op->arg0;
		res->value1 = p->pid;
		res->value2 = strlen(op->payload);
		agent_result_text(res, "send_message");
		break;
	case AGENT_TOOL_READ_MESSAGE:
		if (agent_ipc_mailbox_take(p, &delivery_status, res->result,
					   sizeof(res->result))) {
			res->value0 = 1;
			res->value1 = delivery_status;
			res->value2 = strlen(res->result);
		} else {
			res->value0 = 0;
			agent_result_text(res, "no_message");
		}
		break;
	case AGENT_TOOL_CAPABILITY_CHECK:
		res->value1 = p->agent_role;
		res->value2 = p->agent_capability_mask;
		if (agent_action_allowed(p, op->payload)) {
			res->value0 = 1;
			agent_result_text(res, "allow");
		} else {
			res->value0 = 0;
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
		}
		break;
	case AGENT_TOOL_LLM_REQUEST:
		if (!agent_identity_has_cap(p, AGENT_CAP_MESSAGE_SEND)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 > 0x7fffffffULL || agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "prompt_required");
			break;
		}
		delivered = 0;
		if (op->arg0 != 0) {
			delivery_status = agent_ipc_deliver_pid(
				(int)op->arg0, p, AGENT_EVENT_MESSAGE,
				op->request_id, p->agent_call_count + 1,
				op->payload, 0, &delivered);
			if (delivery_status != AGENT_STATUS_OK) {
				res->status = delivery_status;
				agent_result_text(res,
					delivery_status == AGENT_STATUS_DENIED ?
						"route_denied" :
						delivery_status == AGENT_STATUS_NO_SPACE ?
							"event_queue_full" :
							"target_missing");
				break;
			}
		}
		res->value0 = op->request_id;
		res->value1 = op->arg0;
		res->value2 = delivered;
		agent_result_text(res, "llm_request");
		break;
	case AGENT_TOOL_LLM_RESPONSE:
		if (!agent_identity_has_cap(p, AGENT_CAP_LLM_RELAY)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 == 0 || op->arg0 > 0x7fffffffULL ||
		    agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "response_required");
			break;
		}
		delivery_status = agent_ipc_deliver_pid(
			(int)op->arg0, p, AGENT_EVENT_LLM_DONE, op->request_id,
			p->agent_call_count + 1, op->payload, 0, &delivered);
		if (delivery_status != AGENT_STATUS_OK) {
			res->status = delivery_status;
			agent_result_text(res,
				delivery_status == AGENT_STATUS_DENIED ?
					"route_denied" :
					delivery_status == AGENT_STATUS_NO_SPACE ?
						"event_queue_full" : "target_missing");
			break;
		}
		res->value0 = op->request_id;
		res->value1 = op->arg0;
		res->value2 = delivered;
		agent_result_text(res, "llm_response");
		break;
	case AGENT_TOOL_AGENT_WATCH:
		if (!agent_identity_has_cap(p, AGENT_CAP_WATCH)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 > AGENT_EVENT_MAX) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "bad_event_type");
			break;
		}
		if (agent_ipc_watch_set(p, op->arg0, op->payload) < 0) {
			res->status = AGENT_STATUS_NO_SPACE;
			agent_result_text(res, "watch_full");
			break;
		}
		res->value0 = op->arg0;
		agent_result_text(res, "watch");
		break;
	case AGENT_TOOL_AGENT_HEARTBEAT:
		if (agent_ipc_heartbeat_configure(p, op->arg0,
						  &configured_tick) != AGENT_STATUS_OK) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "bad_heartbeat_interval");
			break;
		}
		res->value0 = op->arg0;
		res->value1 = configured_tick;
		agent_result_text(res, "heartbeat");
		break;
	case AGENT_TOOL_AGENT_WAIT:
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "use_agent_wait_syscall");
		break;
	default:
		res->status = AGENT_STATUS_UNKNOWN_TOOL;
		agent_result_text(res, "unknown_tool");
		break;
	}
out:
	agent_metadata_tool_exit(metadata_locked);
}

static uint64 agent_tool_effect_capability(int tool_id)
{
	switch (tool_id) {
	case AGENT_TOOL_FILE_META_INIT:
		return AGENT_CAP_META_WRITE;
	case AGENT_TOOL_RERUN_STAGE:
	case AGENT_TOOL_ACTION_COMMIT:
		return AGENT_CAP_ACTION_WRITE;
	case AGENT_TOOL_WRITE_REPORT:
	case AGENT_TOOL_ARTIFACT_UPDATE:
		return AGENT_CAP_ARTIFACT_WRITE;
	case AGENT_TOOL_LLM_RESPONSE:
		return AGENT_CAP_LLM_RELAY;
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		return AGENT_CAP_DEPENDENCY_UPDATE;
	default:
		return 0;
	}
}

static int agent_tool_effect_committed(struct proc *p,
				       struct agent_op *op,
				       struct agent_result *res)
{
	uint64 capability;

	if (p == 0 || op == 0 || res == 0 || res->status != AGENT_STATUS_OK)
		return 0;
	capability = agent_tool_effect_capability(op->tool_id);
	return capability != 0 && agent_identity_has_cap(p, capability);
}

static int agent_execute_one(struct proc *p, struct agent_op *op,
			     struct agent_result *res, uint64 tick)
{
	int result;

	op->payload[AGENT_OP_PAYLOAD_SIZE - 1] = 0;
	agent_result_init(res, op);
	if (agent_lifecycle_context_lane_enter(p) < 0) {
		res->status = AGENT_STATUS_NO_SPACE;
		agent_result_text(res, "context_interrupted");
		return -1;
	}
	if (agent_context_append_prepare(p, p->agent_call_count + 1) < 0) {
		res->status = AGENT_STATUS_NO_SPACE;
		agent_result_text(res, "context_sync_unavailable");
		agent_lifecycle_context_lane_leave(p);
		return -1;
	}
	if (agent_tool_protocol_flags(op->tool_id) & AGENT_TOOL_F_SYSCALL_ONLY) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "use_agent_wait_syscall");
		p->agent_call_count++;
		res->sequence = p->agent_call_count;
		result = agent_context_append(p, op, res, tick, 0);
		if (result < 0)
			p->agent_call_count--;
		goto out;
	}
	p->agent_call_count++;
	res->sequence = p->agent_call_count;
	agent_execute_op(p, op, res);
	result = agent_context_append(
		p, op, res, tick, agent_tool_effect_committed(p, op, res));
	if (result < 0)
		p->agent_call_count--;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int sys_agent_create(void)
{
	int result = agent_core_create_admission_status();

	if (result == AGENT_STATUS_OK)
		result = agent_create_proc();

	if (result < 0)
		proc_discard_fd_delegations();
	return result;
}

int sys_agent_create_role(int role)
{
	int result = agent_core_create_admission_status();

	if (result == AGENT_STATUS_OK)
		result = agent_create_role_proc(role);

	if (result < 0)
		proc_discard_fd_delegations();
	return result;
}

int sys_agent_workflow_create(int role)
{
	int result = agent_core_create_admission_status();

	if (result == AGENT_STATUS_OK)
		result = agent_workflow_admission_status(curr_proc(), role);
	if (result == AGENT_STATUS_OK)
		result = agent_workflow_create_proc(role);

	if (result < 0)
		proc_discard_fd_delegations();
	return result;
}

int sys_agent_workflow_close(uint64 requested_scope_id)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle =
		workflow_lifecycle_none();
	struct workflow_lifecycle_key closed =
		workflow_lifecycle_none();
	int factory = p != 0 && !p->is_agent && p->resource_domain_admin &&
		      exec_policy_process_bootstrap(p);
	uint scope_id;
	int result;

	if (requested_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    requested_scope_id >= FS_OWNER_SCOPE_FLAG)
		return AGENT_STATUS_BAD_PARAM;
	scope_id = (uint)requested_scope_id;
	// 非工厂调用者只能由根进程进入可运行态前绑定且不复用的生命周期
	// 控制器 ID 授权。
	if (!factory) {
		if (p == 0 || !p->is_agent || !p->vfs_scope_controller ||
		    p->vfs_scope_id != scope_id || p->agent_control_id == 0)
			return AGENT_STATUS_DENIED;
		lifecycle = vfs_proc_lifecycle(p);
		result = vfs_scope_close_owned(scope_id, lifecycle,
					       p->agent_control_id, &closed);
		if (result < 0)
			return AGENT_STATUS_DENIED;
		if (result > 0)
			return AGENT_STATUS_RETRY;
	} else {
		result = vfs_scope_close_trusted(scope_id, &closed);
		if (result < 0)
			return AGENT_STATUS_NOT_FOUND;
		if (result > 0)
			return AGENT_STATUS_RETRY;
	}
	proc_request_workflow_exit(closed, AGENT_STATUS_CANCELLED);
	return AGENT_STATUS_OK;
}

int sys_agent_scope_delegate_fd(int fd)
{
	struct proc *p = curr_proc();
	int factory = p != 0 && !p->is_agent && p->resource_domain_admin &&
		      exec_policy_process_bootstrap(p);

	if (!factory && !agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	return proc_delegate_fd(fd);
}

int sys_agent_worker_create(uint64 pathaddr, uint64 requested_caps)
{
	struct proc *p = curr_proc();
	char path[MAXPATH];
	int admission = agent_core_create_admission_status();

	if (admission != AGENT_STATUS_OK) {
		proc_discard_fd_delegations();
		return admission;
	}

	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
		proc_discard_fd_delegations();
		return AGENT_STATUS_DENIED;
	}
	if (requested_caps == 0 ||
	    (requested_caps & ~(AGENT_CAP_CONTENT_READ |
				 AGENT_CAP_ARTIFACT_WRITE)) != 0 ||
	    (requested_caps & p->agent_capability_mask) != requested_caps) {
		proc_discard_fd_delegations();
		return AGENT_STATUS_BAD_PARAM;
	}
	if (copyinstr(p->pagetable, path, pathaddr, sizeof(path)) < 0) {
		proc_discard_fd_delegations();
		return -1;
	}
	path[sizeof(path) - 1] = 0;
	{
		int result = agent_worker_create_proc(path, requested_caps);

		if (result < 0)
			proc_discard_fd_delegations();
		return result;
	}
}

int sys_agent_info(uint64 addr, int launch_identity)
{
	struct proc *p = curr_proc();
	struct agent_info info;
	int result;

	if (launch_identity) {
		memset(&info, 0, sizeof(info));
		info.is_agent = p->is_agent;
		info.agent_id = p->agent_id;
		info.agent_role = p->agent_role;
		info.capability_mask = agent_identity_proc_scope(p) != VFS_SCOPE_NONE ?
			p->agent_capability_mask : 0;
		info.filesystem_domain = p->vfs_scope_id;
		info.filesystem_capability_mask =
			vfs_scope_active(p->vfs_scope_id) ? p->vfs_effective_caps : 0;
	} else {
		agent_info_fill(p, &info);
	}
	result = copyout(p->pagetable, addr, (char *)&info, sizeof(info));
	return result < 0 || !launch_identity ? result : p->pid;
}

int sys_agent_run(uint64 opsaddr, uint64 resultsaddr, int count, uint64 flags)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	struct workflow_lifecycle_key lifecycle;
	struct agent_op op;
	struct agent_result res;
	uint64 tick;
	int status = 0;

	if (!p->is_agent)
		return -1;
	if (count < 0 || count > AGENT_BATCH_MAX)
		return -1;
	if (count == 0 && flags == AGENT_RUN_F_FENCE) {
		struct agent_workflow_fence_request request;
		struct agent_workflow_fence_receipt receipt;
		const struct agent_workflow_fence_request *request_ptr = 0;
		struct agent_workflow_fence_receipt *receipt_ptr = 0;

		/* Anonymous fences are fire-and-forget; receipts require a retry id. */
		if (opsaddr == 0 && resultsaddr != 0)
			return -1;
		if (opsaddr != 0) {
			if (user_range_check(p->pagetable, opsaddr,
					     sizeof(request), PTE_R) < 0 ||
			    copyin(p->pagetable, (char *)&request, opsaddr,
				   sizeof(request)) < 0)
				return -1;
			request_ptr = &request;
		}
		if (resultsaddr != 0) {
			if (user_range_check(p->pagetable, resultsaddr,
					     sizeof(receipt), PTE_W) < 0)
				return -1;
			receipt_ptr = &receipt;
		}
		status = agent_workflow_fence_execute(
			p, request_ptr, receipt_ptr);
		if (receipt_ptr != 0 &&
		    copyout(p->pagetable, resultsaddr, (char *)&receipt,
			    sizeof(receipt)) < 0)
			return -1;
		/* A request without a receipt has no fallible delivery step. */
		if (status == AGENT_STATUS_OK && request_ptr != 0)
			agent_workflow_fence_receipt_delivered(
				vfs_proc_lifecycle(p), request.request_id);
		return status;
	}
	if (flags != 0)
		return -1;
	if (count == 0)
		return 0;
	if (user_range_check(p->pagetable, opsaddr,
			     (uint64)count * sizeof(struct agent_op), PTE_R) < 0)
		return -1;
	if (user_range_check(p->pagetable, resultsaddr,
			     (uint64)count * sizeof(struct agent_result),
			     PTE_W) < 0)
		return -1;
	lifecycle = vfs_proc_lifecycle(p);
	if (workflow_lifecycle_key_valid(lifecycle) &&
	    workflow_lifecycle_operation_enter(lifecycle) < 0)
		return -1;
	tick = agent_ticks();
	agent_identity_thread_loop_set(t, AGENT_LOOP_RUNNING);
	for (int i = 0; i < count; i++) {
		struct bio_checkpoint_result checkpoint;

		if (copyin(p->pagetable, (char *)&op,
			   opsaddr + i * sizeof(struct agent_op),
			   sizeof(op)) < 0) {
			status = -1;
			break;
		}
		if (agent_execute_one(p, &op, &res, tick) < 0 ||
		    copyout(p->pagetable,
			    resultsaddr + i * sizeof(struct agent_result),
			    (char *)&res, sizeof(res)) < 0) {
			status = -1;
			break;
		}
		status = i + 1;
		checkpoint = bio_request_checkpoint();
		if (bio_checkpoint_should_stop(checkpoint))
			break;
		if (kernel_work_checkpoint(KERNEL_WORK_OPERATION_UNITS) < 0)
			break;
	}
	agent_identity_thread_loop_set(t, AGENT_LOOP_IDLE);
	if (workflow_lifecycle_key_valid(lifecycle))
		workflow_lifecycle_operation_leave(lifecycle);
	return status;
}

int sys_agent_tool_list(uint64 addr, int max)
{
	return agent_tool_protocol_list_v1(curr_proc()->pagetable, addr, max);
}

int sys_tool_list(uint64 addr, int max, uint desc_size, uint version)
{
	return agent_tool_protocol_list_v2(curr_proc()->pagetable, addr, max,
					   desc_size, version);
}

int sys_agent_call(uint64 reqaddr, uint64 respaddr)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	struct agent_request req;
	struct agent_response resp;
	struct agent_op op;
	struct agent_result res;
	struct agent_tool_match tool;
	char validate_error[AGENT_RESULT_SIZE];
	int status;

	if (copyin(p->pagetable, (char *)&req, reqaddr, sizeof(req)) < 0)
		return -1;
	if (user_range_check(p->pagetable, respaddr, sizeof(resp), PTE_W) < 0)
		return -1;
	memset(&resp, 0, sizeof(resp));
	resp.version = AGENT_CALL_VERSION_V1;
	resp.request_id = req.request_id;
	if (req.version != AGENT_CALL_VERSION_V1) {
		resp.status = AGENT_STATUS_BAD_VERSION;
		safestrcpy(resp.result, "bad_request_version", sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	if (!p->is_agent) {
		resp.status = AGENT_STATUS_NOT_AGENT;
		safestrcpy(resp.result, "not_agent", sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	memset(validate_error, 0, sizeof(validate_error));
	status = agent_tool_protocol_resolve(req.tool_id, req.tool_name, &tool,
					     validate_error,
					     sizeof(validate_error));
	if (status != AGENT_STATUS_OK) {
		resp.status = status;
		resp.tool_id = req.tool_id;
		safestrcpy(resp.result, validate_error, sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	if (tool.flags & AGENT_TOOL_F_SYSCALL_ONLY) {
		resp.status = AGENT_STATUS_BAD_PARAM;
		resp.tool_id = tool.tool_id;
		safestrcpy(validate_error, "syscall_only", sizeof(validate_error));
	} else {
		status = agent_tool_protocol_decode_v1(&req, &tool, &op,
						       validate_error,
						       sizeof(validate_error));
		resp.status = status;
	}
	if (resp.status != AGENT_STATUS_OK) {
		resp.tool_id = tool.tool_id;
		safestrcpy(resp.tool_name, tool.name, sizeof(resp.tool_name));
		safestrcpy(resp.result, validate_error, sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	lifecycle = vfs_proc_lifecycle(p);
	if (workflow_lifecycle_key_valid(lifecycle) &&
	    workflow_lifecycle_operation_enter(lifecycle) < 0) {
		resp.status = AGENT_STATUS_RETRY;
		resp.tool_id = tool.tool_id;
		safestrcpy(resp.tool_name, tool.name, sizeof(resp.tool_name));
		safestrcpy(resp.result, "workflow_fenced", sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	if (agent_execute_one(p, &op, &res, agent_ticks()) < 0)
		res.status = AGENT_STATUS_NO_SPACE;
	if (workflow_lifecycle_key_valid(lifecycle))
		workflow_lifecycle_operation_leave(lifecycle);
	resp.status = res.status;
	resp.tool_id = res.tool_id;
	resp.request_id = res.request_id;
	resp.sequence = res.sequence;
	resp.value0 = res.value0;
	resp.value1 = res.value1;
	resp.value2 = res.value2;
	safestrcpy(resp.tool_name, tool.name, sizeof(resp.tool_name));
	safestrcpy(resp.result, res.result, sizeof(resp.result));
	return copyout(p->pagetable, respaddr, (char *)&resp, sizeof(resp));
}

int sys_tool_call(uint64 reqaddr, uint64 respaddr)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	struct agent_request_v2 req;
	struct agent_response_v2 resp;
	struct agent_tool_match tool;
	struct agent_op op;
	struct agent_result result;
	char error[AGENT_RESULT_SIZE];
	int operation_entered = 0;
	int status = AGENT_STATUS_OK;

	if (copyin(p->pagetable, (char *)&req, reqaddr, sizeof(req)) < 0 ||
	    user_range_check(p->pagetable, respaddr, sizeof(resp), PTE_W) < 0)
		return -1;
	memset(&resp, 0, sizeof(resp));
	resp.version = AGENT_CALL_VERSION_V2;
	resp.size = sizeof(resp);
	resp.request_id = req.request_id;
	memset(error, 0, sizeof(error));
	if (req.version != AGENT_CALL_VERSION_V2) {
		status = AGENT_STATUS_BAD_VERSION;
		safestrcpy(error, "bad_request_version", sizeof(error));
	} else if (req.size != sizeof(req)) {
		status = AGENT_STATUS_BAD_SIZE;
		safestrcpy(error, "bad_request_size", sizeof(error));
	} else if (req.flags != 0) {
		status = AGENT_STATUS_BAD_PARAM;
		safestrcpy(error, "unsupported_flags", sizeof(error));
	} else if (req.param_count > AGENT_TOOL_PARAM_MAX) {
		status = AGENT_STATUS_BAD_SIZE;
		safestrcpy(error, "too_many_params", sizeof(error));
	} else if (!p->is_agent) {
		status = AGENT_STATUS_NOT_AGENT;
		safestrcpy(error, "not_agent", sizeof(error));
	} else {
		status = agent_tool_protocol_resolve(req.tool_id, req.tool_name,
					     &tool, error, sizeof(error));
	}
	if (status != AGENT_STATUS_OK)
		goto out;
	resp.tool_id = tool.tool_id;
	safestrcpy(resp.tool_name, tool.name, sizeof(resp.tool_name));
	if (tool.flags & AGENT_TOOL_F_SYSCALL_ONLY) {
		status = AGENT_STATUS_BAD_PARAM;
		safestrcpy(error, "syscall_only", sizeof(error));
		goto out;
	}
	if ((req.param_count == 0 && req.params != 0) ||
	    (req.param_count != 0 && req.params == 0)) {
		status = AGENT_STATUS_BAD_PARAM;
		safestrcpy(error, "bad_param_pointer", sizeof(error));
		goto out;
	}
	if (req.param_count != 0 &&
	    user_range_check(p->pagetable, req.params,
			     (uint64)req.param_count *
				     sizeof(struct agent_param_v2), PTE_R) < 0)
		return -1;
	status = agent_tool_protocol_decode_v2(p->pagetable, &req, &tool, &op,
					       error, sizeof(error));
	if (status == -1)
		return -1;
	if (status != AGENT_STATUS_OK)
		goto out;
	lifecycle = vfs_proc_lifecycle(p);
	if (workflow_lifecycle_key_valid(lifecycle)) {
		if (workflow_lifecycle_operation_enter(lifecycle) < 0) {
			status = AGENT_STATUS_RETRY;
			safestrcpy(error, "workflow_fenced", sizeof(error));
			goto out;
		}
		operation_entered = 1;
	}
	if (agent_execute_one(p, &op, &result, agent_ticks()) < 0)
		result.status = AGENT_STATUS_NO_SPACE;
	status = result.status;
	resp.sequence = result.sequence;
	resp.value0 = result.value0;
	resp.value1 = result.value1;
	resp.value2 = result.value2;
	safestrcpy(resp.result, result.result, sizeof(resp.result));
out:
	if (operation_entered)
		workflow_lifecycle_operation_leave(lifecycle);
	resp.status = status;
	/* 协议错误采用校验器诊断；执行错误沿用工具返回的稳定错误文本。 */
	if (status != AGENT_STATUS_OK && error[0] != 0)
		safestrcpy(resp.result, error, sizeof(resp.result));
	return copyout(p->pagetable, respaddr, (char *)&resp, sizeof(resp));
}

void agent_core_tick(void)
{
	uint64 now = agent_ticks();

	fs_deferred_reclaim_tick(now);
	vfs_scope_reap_tick(now);
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent)
			continue;
		agent_ipc_tick_proc(p, now);
		agent_observe_tick_proc(p, now);
	}
}
