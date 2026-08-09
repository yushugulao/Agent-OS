#include "agent_context.h"
#include "agent_evidence_ring.h"
#include "agent_observe_internal.h"
#include "defs.h"
void agent_observe_init(void)
{
	agent_evidence_init();
	agent_observe_ledger_init();
}

void
agent_observe_record_context(struct proc *p,
			     struct agent_context_record *record,
			     int authority_effect, int causal_audit)
{
	uint64 audit_sequence, evidence_ticket = 0;
	uint64 span_owner, cause_control, cause_branch, slot;
	int source_pid;
	if (p == 0 || record == 0 || agent_observe_recording_suppressed(p))
		return;
	if (record->sequence == 0 || p->context_path_capacity == 0)
		return;
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(
		    p, slot, &span_owner, &source_pid, &cause_control,
		    &cause_branch) < 0)
		return;
	audit_sequence = agent_observe_alloc_audit_sequence();
	if (audit_sequence != 0 && agent_evidence_append_context(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, authority_effect, causal_audit,
		    &evidence_ticket) == 0) {
		/* The canonical ring owns storage; this call only wakes readers. */
		agent_observe_timeline_record_context(p, record);
		/* Migration rare path: deny/authority evidence is immediately
		 * sealed by one legacy projection. Successful ordinary events never
		 * pay its durable receipt or hash-chain cost. */
		if (authority_effect || record->status != AGENT_STATUS_OK)
			agent_observe_ledger_record_context(
				p, record, span_owner, source_pid, cause_control,
				authority_effect, causal_audit,
				evidence_ticket);
		return;
	}
	/* Fail closed to the legacy protected ledger during migration. */
	agent_observe_timeline_record_context(p, record);
	agent_observe_ledger_record_context(
		p, record, span_owner, source_pid, cause_control,
		authority_effect, causal_audit, 0);
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
