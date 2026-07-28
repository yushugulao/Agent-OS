#include "agent_observe_test.h"
#ifdef AGENT_OBSERVE_TEST_PROFILE
#include "agent_internal.h"
#include "agent_observe_internal.h"
#include "agent_observe_store.h"
#include "defs.h"
#include "proc.h"
static struct {
	uint64 control_id, injected_scan_epoch, injected_current_epoch;
	uint scope_id, injections, rechecks, remaining_injections;
} agent_observe_timeline_wait_test;
static struct {
	uint64 control_id, wakeup_base;
	uint scope_id, peak_active;
} threads_test;
enum agent_observe_drop_test_phase { AGENT_OBSERVE_DROP_TEST_IDLE = 0, AGENT_OBSERVE_DROP_TEST_ARMED, AGENT_OBSERVE_DROP_TEST_DROPPING,
	AGENT_OBSERVE_DROP_TEST_FIRST_SUCCESS, AGENT_OBSERVE_DROP_TEST_DONE };
static struct {
	uint64 drops;
	uint armer_scope_id, target_scope_id, phase;
	int drop_only_captured;
} drop_test;
enum { AGENT_OBSERVE_THREADS_FILTERS = 1U << 0,
	AGENT_OBSERVE_THREADS_DEADLINES = 1U << 1,
	AGENT_OBSERVE_THREADS_GENERATIONS = 1U << 2 };
static int agent_observe_test_empty_request(const struct agent_observe_recovery_request *request,
					    uint64 recordsaddr)
{
	return recordsaddr == 0 && request->evidence.id == 0 &&
	       request->evidence.generation == 0 &&
	       request->evidence.reserved == 0 && request->after_sequence == 0 &&
	       request->completion_token == 0 && request->max_records == 0;
}
static void agent_observe_test_arm_drop_sequence(struct proc *p)
{
	int enabled = intr_save();
	memset(&drop_test, 0, sizeof(drop_test));
	drop_test.armer_scope_id = agent_identity_proc_scope(p); drop_test.phase = AGENT_OBSERVE_DROP_TEST_ARMED;
	intr_restore(enabled);
}
int agent_observe_test_drop_audit(struct proc *actor, uint scope_id, int kind, int tool_id,
				  int status, int authority_effect)
{
	int drop = 0, enabled;
	if (actor == 0 || !actor->is_agent)
		return 0;
	enabled = intr_save();
	if (drop_test.phase == AGENT_OBSERVE_DROP_TEST_ARMED &&
	    actor->agent_role == AGENT_ROLE_ORCHESTRATOR &&
	    scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	    scope_id != drop_test.armer_scope_id) {
		drop_test.target_scope_id = scope_id; drop_test.phase = AGENT_OBSERVE_DROP_TEST_DROPPING;
	}
	if (scope_id != drop_test.target_scope_id)
		goto out;
	if (drop_test.phase == AGENT_OBSERVE_DROP_TEST_DROPPING) {
		if (kind == AGENT_AUDIT_KIND_CONTEXT &&
		    tool_id == AGENT_TOOL_FILE_META_INIT &&
		    status == AGENT_STATUS_OK && authority_effect &&
		    drop_test.drops != 0 && drop_test.drop_only_captured)
			drop_test.phase = AGENT_OBSERVE_DROP_TEST_FIRST_SUCCESS;
		else {
			drop_test.drops++;
			drop = 1;
		}
	} else if (drop_test.phase == AGENT_OBSERVE_DROP_TEST_FIRST_SUCCESS) {
		if (kind == AGENT_AUDIT_KIND_SCHED) {
			drop_test.drops++;
			drop = 1;
		} else
			drop_test.phase = AGENT_OBSERVE_DROP_TEST_DONE;
	}
out:
	intr_restore(enabled); return drop;
}
void agent_observe_test_drop_only_captured(uint scope_id, uint64 total_records,
					   uint64 admission_drops)
{
	int enabled = intr_save();
	if (drop_test.phase == AGENT_OBSERVE_DROP_TEST_DROPPING &&
	    drop_test.target_scope_id == scope_id && total_records != 0 &&
	    total_records == admission_drops && admission_drops == drop_test.drops)
		drop_test.drop_only_captured = 1;
	intr_restore(enabled);
}
int agent_observe_test_evict_checkpoint_window(struct proc *p)
{
	if (p == 0 || !p->is_agent)
		return -1;
	for (uint i = 0; i <= AGENT_OBSERVE_CHECKPOINT_PER_SCOPE; i++)
		agent_observe_ledger_record_effect(
			p, 0x7f020001, AGENT_STATUS_OK,
			"receipt-retention-eviction", i, 0, 0, 0, 0);
	return 0;
}
static int agent_observe_test_owner(struct proc *p, uint64 control_id, uint scope_id)
{
	return p != 0 && p->agent_control_id != 0 && p->agent_control_id == control_id &&
	       agent_identity_proc_scope(p) == scope_id;
}
static uint agent_observe_timeline_threads_snapshot(struct proc *p, uint *active)
{
	struct agent_timeline_wait_state *first = 0;
	uint features = 0, count = 0;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];
		struct agent_timeline_wait_state *state =
			t->agent_timeline_wait_state;
		if (state == 0 ||
		    state->thread_generation != t->identity_generation ||
		    state->scope_id != threads_test.scope_id ||
		    t->state != SLEEPING ||
		    t->wait_channel != &p->agent_timeline_waiters ||
		    t->wait_reason != WAIT_REASON_TIMELINE ||
		    t->wait_key != state->thread_generation)
			continue;
		count++;
		if (first == 0) {
			first = state;
			continue;
		}
		if (state->filter.source_mask != first->filter.source_mask ||
		    state->filter.tool_id != first->filter.tool_id)
			features |= AGENT_OBSERVE_THREADS_FILTERS;
		if (state->deadline_valid && first->deadline_valid &&
		    state->deadline != first->deadline)
			features |= AGENT_OBSERVE_THREADS_DEADLINES;
		if (state->thread_generation != first->thread_generation)
			features |= AGENT_OBSERVE_THREADS_GENERATIONS;
	}
	if (count > threads_test.peak_active)
		threads_test.peak_active = count;
	*active = count; return features;
}
static int agent_observe_test_timeline_point(struct agent_observe_recovery_request *request,
					     int *status)
{
	struct proc *p = curr_proc();
	struct agent_timeline_record record = {0};
	uint scope_id = request->evidence.id;
	uint64 current_epoch;
	int enabled;
	if (request->operation != AGENT_OBSERVE_TEST_TIMELINE_WINDOW &&
	    request->operation != AGENT_OBSERVE_TEST_TIMELINE_RECHECK)
		return 0;
	enabled = intr_save();
	if (!agent_observe_test_owner(
		    p, agent_observe_timeline_wait_test.control_id,
		    agent_observe_timeline_wait_test.scope_id) ||
	    agent_observe_timeline_wait_test.scope_id != scope_id)
		goto out;
	if (request->operation == AGENT_OBSERVE_TEST_TIMELINE_WINDOW &&
	    agent_observe_timeline_wait_test.remaining_injections != 0) {
		current_epoch = agent_observe_scope_epoch(scope_id);
		if (current_epoch != request->after_sequence)
			goto out;
		record.source = AGENT_TIMELINE_SOURCE_CONTEXT; record.pid = p->pid;
		agent_observe_timeline_publish_locked(scope_id, &record, 0);
		current_epoch = agent_observe_scope_epoch(scope_id);
		if (current_epoch == 0 || current_epoch == request->after_sequence) {
			*status = AGENT_STATUS_IO_ERROR;
			goto out;
		}
		agent_observe_timeline_wait_test.remaining_injections--;
		agent_observe_timeline_wait_test.injected_scan_epoch =
			request->after_sequence;
		agent_observe_timeline_wait_test.injected_current_epoch = current_epoch;
		agent_observe_timeline_wait_test.injections++;
	} else if (request->operation == AGENT_OBSERVE_TEST_TIMELINE_RECHECK &&
		   request->after_sequence ==
			   agent_observe_timeline_wait_test.injected_scan_epoch &&
		   request->bank_generation ==
			   agent_observe_timeline_wait_test.injected_current_epoch &&
		   agent_observe_timeline_wait_test.rechecks <
			   agent_observe_timeline_wait_test.injections) {
		agent_observe_timeline_wait_test.rechecks++;
	}
out:
	intr_restore(enabled); return 1;
}
_Static_assert(AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR -
	AGENT_OBSERVE_RECOVERY_TEST_EXHAUST_EVENT_ID == 6, "observation test operation range");
int agent_observe_test_operation(uint operation)
{
	return operation >= AGENT_OBSERVE_RECOVERY_TEST_EXHAUST_EVENT_ID &&
	       operation <= AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR;
}
int agent_observe_test_execute(struct agent_observe_recovery_request *request, uint64 recordsaddr,
	uint64 *bank_generation, uint *returned, int *status)
{
	struct proc *p;
	int enabled;
	if (agent_observe_test_timeline_point(request, status))
		return 1;
	if (!agent_observe_test_operation(request->operation))
		return 0;
	if (request->operation ==
		    AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_CUT ||
	    request->operation ==
		    AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR) {
		struct agent_observe_test_identity_ids ids;
		const char *tag = request->operation ==
			AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_CUT ?
			"lease_cut_alloc" : "lease_cut_successor";
		p = curr_proc();
		if (recordsaddr == 0 || request->evidence.id != 0 ||
		    request->evidence.generation != 0 ||
		    request->evidence.reserved != 0 ||
		    request->after_sequence != 0 ||
		    request->completion_token != 0 || request->max_records != 0 ||
		    agent_observe_test_allocate_identity_ids(&ids) < 0) {
			*status = AGENT_STATUS_BAD_PARAM;
			return 1;
		}
		printf("agentobsreboot_ucore: %s audit=%llu span=%llu event=%llu control=%llu agent=%u lifecycle_slot=%u lifecycle_generation=%llu\n",
		       tag, ids.audit_sequence, ids.span_id, ids.event_id,
		       ids.control_id, ids.agent_id, ids.lifecycle_slot,
		       ids.lifecycle_generation);
		*status = copyout(p->pagetable, recordsaddr, (char *)&ids,
				  sizeof(ids)) < 0 ?
			  AGENT_STATUS_BAD_PARAM : AGENT_STATUS_OK;
		if (*status == AGENT_STATUS_OK && request->operation ==
		    AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR)
			agent_observe_test_arm_drop_sequence(p);
		return 1;
	}
	if (!agent_observe_test_empty_request(request, recordsaddr)) {
		*status = AGENT_STATUS_BAD_PARAM;
		return 1;
	}
	if (request->operation == AGENT_OBSERVE_RECOVERY_TEST_EXHAUST_EVENT_ID) {
		agent_observe_checkpoint_exhaust_highwater(
			AGENT_OBSERVE_ALLOC_EVENT_EXHAUSTED);
		*status = agent_observe_alloc_event_id() == 0 ?
			AGENT_STATUS_OK : AGENT_STATUS_IO_ERROR;
		return 1;
	}
	p = curr_proc();
	enabled = intr_save();
	*status = AGENT_STATUS_OK;
	switch (request->operation) {
	case AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_WAIT:
		memset(&agent_observe_timeline_wait_test, 0,
		       sizeof(agent_observe_timeline_wait_test));
		agent_observe_timeline_wait_test.control_id = p->agent_control_id;
		agent_observe_timeline_wait_test.scope_id =
			agent_identity_proc_scope(p);
		agent_observe_timeline_wait_test.remaining_injections = 2;
		break;
	case AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_WAIT_STATUS:
		if (!agent_observe_test_owner(
			    p, agent_observe_timeline_wait_test.control_id,
			    agent_observe_timeline_wait_test.scope_id)) {
			*status = AGENT_STATUS_DENIED;
			break;
		}
		request->after_sequence = agent_observe_timeline_wait_test.injections;
		request->completion_token = agent_observe_timeline_wait_test.rechecks;
		break;
	case AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_THREADS:
		memset(&threads_test, 0, sizeof(threads_test));
		threads_test.control_id = p->agent_control_id;
		threads_test.wakeup_base = p->agent_timeline_wait_wakeup_count;
		threads_test.scope_id = agent_identity_proc_scope(p);
		break;
	case AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_THREADS_STATUS: {
		uint active;
		if (!agent_observe_test_owner(
			    p, threads_test.control_id, threads_test.scope_id)) {
			*status = AGENT_STATUS_DENIED;
			break;
		}
		*bank_generation =
			agent_observe_timeline_threads_snapshot(p, &active);
		request->after_sequence = active;
		request->completion_token = threads_test.peak_active;
		*returned = p->agent_timeline_wait_wakeup_count -
			    threads_test.wakeup_base;
		break;
	}
	default:
		*status = AGENT_STATUS_BAD_PARAM;
		break;
	}
	intr_restore(enabled);
	return 1;
}
#endif
