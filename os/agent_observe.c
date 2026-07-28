#include "agent_context.h"
#include "agent_observe_internal.h"
#include "agent_observe_capacity.h"
#include "agent_observe_recovery.h"
#include "agent_observe_store.h"
#include "defs.h"
void agent_observe_init(void)
{
	agent_observe_capacity_init();
	agent_observe_ledger_init();
	agent_obsstore_init();
	agent_observe_recovery_init();
}

void
agent_observe_record_context(struct proc *p,
			     struct agent_context_record *record,
			     int authority_effect, int causal_audit)
{
	uint64 span_owner, cause_control, cause_branch, slot;
	int source_pid;
	if (p == 0 || record == 0 || agent_observe_recording_suppressed(p))
		return;
	agent_observe_timeline_record_context(p, record);
	if (record->sequence == 0 || p->context_path_capacity == 0)
		return;
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(
		    p, slot, &span_owner, &source_pid, &cause_control,
		    &cause_branch) < 0)
		return;
	(void)cause_branch;
	agent_observe_ledger_record_context(
		p, record, span_owner, source_pid, cause_control,
		authority_effect, causal_audit);
}

void
agent_observe_record_sched(struct thread *t, struct agent_sched_record *record)
{
	struct proc *p;
	if (t == 0 || record == 0 || (p = t->process) == 0 ||
	    t->agent_observe_suppress_depth != 0)
		return;
	agent_observe_timeline_record_sched(p, record);
	agent_observe_ledger_record_sched(p, record);
}

void
agent_observe_record_event(int kind, struct proc *actor,
			   struct agent_event *event, uint64 span_owner,
			   uint64 audit_principal)
{
	if (!agent_observe_recording_suppressed(actor))
		agent_observe_ledger_record_event(kind, actor, event, span_owner,
						 audit_principal);
}

void
agent_observe_record_effect(struct proc *p, int tool_id, int status,
			    char *text, uint64 value0, uint64 value1,
			    uint64 value2, uint64 flags,
			    int authority_effect)
{
	if (!agent_observe_recording_suppressed(p))
		agent_observe_ledger_record_effect(p, tool_id, status, text, value0,
						 value1, value2, flags,
						 authority_effect);
}

void
agent_observe_record_prefetch(struct proc *p,
			      struct agent_file_prefetch_hint *hint,
			      uint64 span_owner, char *target_stage,
			      int publish_audit)
{
	if (p == 0 || hint == 0 || agent_observe_recording_suppressed(p) ||
	    !agent_observe_ledger_record_prefetch(
		    p, hint, span_owner, target_stage, publish_audit))
		return;
	agent_observe_timeline_record_prefetch(p, hint, span_owner);
}

void
agent_observe_record_prefetch_handoff_locked(
	int source_pid, uint64 source_control_id, struct proc *target,
	struct agent_file_prefetch_hint *hint, uint64 span_owner,
	char *target_stage, uint64 reason)
{
	if (target == 0 || hint == 0 ||
	    agent_observe_recording_suppressed(target) ||
	    !agent_observe_ledger_record_prefetch_handoff_locked(
		    source_pid, source_control_id, target, hint, span_owner,
		    target_stage, reason))
		return;
	agent_observe_timeline_record_prefetch(target, hint, span_owner);
}
