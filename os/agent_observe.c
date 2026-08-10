#include "agent_context.h"
#include "agent_evidence_ring.h"
#include "agent_observe_internal.h"
#include "defs.h"
void agent_observe_init(void)
{
	agent_evidence_init();
	agent_observe_ledger_init();
}

int
agent_observe_record_context_ticket(struct proc *p,
				    struct agent_context_record *record,
				    int authority_effect, int causal_audit,
				    uint64 *ticket_out)
{
	uint64 audit_sequence, evidence_ticket = 0;
	uint64 span_owner, cause_control, cause_branch, slot;
	int source_pid;
	if (ticket_out == 0)
		return -1;
	*ticket_out = 0;
	if (p == 0 || record == 0 || agent_observe_recording_suppressed(p))
		return -1;
	if (record->sequence == 0 || p->context_path_capacity == 0)
		return -1;
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(
		    p, slot, &span_owner, &source_pid, &cause_control,
		    &cause_branch) < 0)
		return -1;
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
		*ticket_out = evidence_ticket;
		return evidence_ticket != 0 ? 0 : -1;
	}
	/* Fail closed to the legacy protected ledger during migration. */
	agent_observe_timeline_record_context(p, record);
	agent_observe_ledger_record_context(
		p, record, span_owner, source_pid, cause_control,
		authority_effect, causal_audit, 0);
	return -1;
}

uint64
agent_observe_commit_context_reserved_ticket(
	struct proc *p, struct agent_context_record *record,
	int authority_effect, int causal_audit,
	struct agent_evidence_context_reservation *reservation)
{
	uint64 audit_sequence, evidence_ticket = 0;
	uint64 span_owner, cause_control, cause_branch, slot;
	int source_pid;

	if (p == 0 || record == 0 || reservation == 0 ||
	    !reservation->active || agent_observe_recording_suppressed(p) ||
	    record->sequence == 0 || p->context_path_capacity == 0)
		panic("reserved context evidence precondition");
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(
		    p, slot, &span_owner, &source_pid, &cause_control,
		    &cause_branch) < 0)
		panic("reserved context evidence attribution");
	audit_sequence = agent_observe_alloc_audit_sequence();
	if (audit_sequence == 0)
		panic("reserved context audit identity");
	if (agent_evidence_context_commit(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, authority_effect, causal_audit,
		    reservation, &evidence_ticket) < 0 || evidence_ticket == 0)
		panic("reserved context evidence commit");
	return evidence_ticket;
}

uint64
agent_observe_commit_security_reserved_ticket(
	struct proc *p, struct agent_context_record *record,
	struct agent_evidence_security_reservation *reservation)
{
	uint64 audit_sequence, evidence_ticket = 0;
	uint64 span_owner, cause_control, cause_branch, slot;
	int source_pid;

	if (p == 0 || record == 0 || reservation == 0 ||
	    !reservation->active || agent_observe_recording_suppressed(p) ||
	    record->sequence == 0 || p->context_path_capacity == 0 ||
	    record->status == AGENT_STATUS_OK)
		panic("security evidence precondition");
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(
		    p, slot, &span_owner, &source_pid, &cause_control,
		    &cause_branch) < 0)
		panic("security evidence attribution");
	audit_sequence = agent_observe_alloc_audit_sequence();
	if (audit_sequence == 0)
		panic("security evidence audit identity");
	if (agent_evidence_security_commit(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, reservation, &evidence_ticket) < 0 ||
	    evidence_ticket == 0)
		panic("security denial Evidence commit");
	return evidence_ticket;
}

void
agent_observe_publish_context_ticket(
	struct proc *p, struct agent_context_record *record,
	int authority_effect, int causal_audit, uint64 evidence_ticket)
{
	uint64 span_owner, cause_control, cause_branch, slot;
	int source_pid;

	if (p == 0 || record == 0 || evidence_ticket == 0 ||
	    record->sequence == 0 || p->context_path_capacity == 0)
		panic("context evidence projection precondition");
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(
		    p, slot, &span_owner, &source_pid, &cause_control,
		    &cause_branch) < 0)
		panic("context evidence projection attribution");
	(void)cause_branch;

	/* Evidence is canonical. These legacy projections run only after Context
	 * release-publication and cannot make the atomic pair fail. */
	agent_observe_timeline_record_context(p, record);
	if (authority_effect || record->status != AGENT_STATUS_OK)
		agent_observe_ledger_record_context(
			p, record, span_owner, source_pid, cause_control,
			authority_effect, causal_audit, evidence_ticket);
}

void
agent_observe_record_context(struct proc *p,
			     struct agent_context_record *record,
			     int authority_effect, int causal_audit)
{
	uint64 ignored_ticket;

	(void)agent_observe_record_context_ticket(
		p, record, authority_effect, causal_audit, &ignored_ticket);
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
