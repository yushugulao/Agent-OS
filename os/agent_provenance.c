#include "agent_provenance.h"
#include "agent_context.h"
#include "agent_context_path.h"
#include "agent_evidence_ring.h"
#include "agent_internal.h"
#include "agent_observe_internal.h"
#include "defs.h"
#include "proc.h"

#define AGENT_PROVENANCE_CAP_ALL ((1ULL << 14) - 1ULL)

struct agent_provenance_proc_state {
	int used;
	struct workflow_lifecycle_key lifecycle;
	uint64 control_id;
	uint64 current_labels;
	uint64 pending_labels;
	uint64 pending_request_id;
	uint64 mailbox_labels;
	int pending_tool_id;
	int pending;
};

_Static_assert(sizeof(struct agent_provenance_proc_state) <=
		       AGENT_CONTEXT_PROVENANCE_STATE_SIZE,
	       "Agent provenance state must fit Context sidecar slack");

static struct workflow_lifecycle_key
agent_provenance_proc_lifecycle(struct proc *p)
{
	struct workflow_lifecycle_key key = workflow_lifecycle_none();

	if (p != 0 && p->workflow_lifecycle_charged) {
		key.id = p->workflow_lifecycle_id;
		key.generation = p->workflow_lifecycle_generation;
	}
	return key;
}

static uint64
agent_provenance_record_labels(struct proc *p, uint64 sequence)
{
	struct agent_context_record record;
	uint64 labels;

	if (p == 0 || sequence == 0 || p->context_path_capacity == 0 ||
	    sequence < p->context_path_oldest ||
	    sequence > p->context_path_latest ||
	    agent_context_read_record(
		    p, (sequence - 1) % p->context_path_capacity, &record) < 0 ||
	    record.sequence != sequence || record.record_hash == 0 ||
	    record.record_hash != agent_context_record_hash(&record))
		return 0;
	labels = AGENT_CONTEXT_PROVENANCE_DECODE(record.flags);
	return labels == 0 ? AGENT_PROVENANCE_AGENT_DERIVED : labels;
}

static uint64
agent_provenance_initial_labels(struct proc *p)
{
	uint64 labels;

	if (p != 0 && p->context_path_visible_head != 0) {
		labels = agent_provenance_record_labels(
			p, p->context_path_visible_head);
		if (labels != 0)
			return labels;
	}
	return AGENT_PROVENANCE_AGENT_DERIVED;
}

static struct agent_provenance_proc_state *
agent_provenance_state_locked(struct proc *p, int create)
{
	struct agent_provenance_proc_state *state;
	struct workflow_lifecycle_key lifecycle;

	if (intr_get())
		panic("Agent provenance unlocked");
	if (p == 0 ||
	    (state = agent_context_provenance_sidecar(p)) == 0)
		return 0;
	lifecycle = agent_provenance_proc_lifecycle(p);
	if (state->used &&
	    (!workflow_lifecycle_key_equal(state->lifecycle, lifecycle) ||
	     state->control_id != p->agent_control_id))
		memset(state, 0, sizeof(*state));
	if (!state->used && create) {
		state->used = 1;
		state->lifecycle = lifecycle;
		state->control_id = p->agent_control_id;
		state->current_labels = agent_provenance_initial_labels(p);
	}
	return state->used ? state : 0;
}

static void
agent_provenance_stage_labels(struct proc *p, uint64 request_id, int tool_id,
			      uint64 labels)
{
	struct agent_provenance_proc_state *state;
	int enabled;

	if ((labels & ~AGENT_PROVENANCE_ALL) != 0)
		return;
	enabled = intr_save();
	state = agent_provenance_state_locked(p, 1);
	if (state != 0) {
		state->pending = 1;
		state->pending_request_id = request_id;
		state->pending_tool_id = tool_id;
		state->pending_labels = labels | AGENT_PROVENANCE_AGENT_DERIVED;
	}
	intr_restore(enabled);
}

static void
agent_provenance_decide(struct agent_provenance_decision *decision,
			uint reason, int status)
{
	decision->reason = reason;
	decision->status = status;
}

int
agent_provenance_prepare_denial(
	const struct agent_provenance_request *request, int status, uint reason,
	uint64 input_labels, struct agent_provenance_decision *decision)
{
	if (request == 0 || decision == 0 || request->tool_id <= 0 ||
	    status == AGENT_STATUS_OK || reason == AGENT_PROVENANCE_DENY_NONE ||
	    (input_labels & ~AGENT_PROVENANCE_ALL) != 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(decision, 0, sizeof(*decision));
	decision->input_labels = input_labels |
		AGENT_PROVENANCE_AGENT_DERIVED;
	decision->output_labels = decision->input_labels;
	decision->side_effect_mask = request->declared_side_effect_mask;
	decision->request_id = request->request_id;
	decision->source_node_id = request->source_node_id;
	decision->target_node_id = request->target_node_id;
	decision->tool_id = request->tool_id;
	decision->reason = reason;
	decision->status = status;
	return AGENT_STATUS_OK;
}

uint64
agent_provenance_current_labels(struct proc *p)
{
	struct agent_provenance_proc_state *state;
	uint64 labels = AGENT_PROVENANCE_AGENT_DERIVED;
	int enabled = intr_save();

	state = agent_provenance_state_locked(p, 1);
	if (state != 0)
		labels = state->current_labels;
	intr_restore(enabled);
	return labels;
}

int
agent_provenance_merge_current(struct proc *p, uint64 labels)
{
	struct agent_provenance_proc_state *state;
	int enabled;

	if (p == 0 || (labels & ~AGENT_PROVENANCE_ALL) != 0)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	state = agent_provenance_state_locked(p, 1);
	if (state != 0)
		state->current_labels |=
			labels | AGENT_PROVENANCE_AGENT_DERIVED;
	intr_restore(enabled);
	return state == 0 ? AGENT_STATUS_BAD_PARAM : AGENT_STATUS_OK;
}

int
agent_provenance_authorize_tool(
	struct proc *p, const struct agent_provenance_request *request,
	const struct agent_provenance_manifest *manifest,
	struct agent_provenance_decision *decision)
{
	struct workflow_lifecycle_key current;
	uint64 labels;

	if (decision == 0)
		return AGENT_STATUS_DENIED;
	memset(decision, 0, sizeof(*decision));
	agent_provenance_decide(
		decision, AGENT_PROVENANCE_DENY_BAD_REQUEST,
		AGENT_STATUS_DENIED);
	if (p == 0 || request == 0 || manifest == 0 || !p->is_agent ||
	    request->tool_id <= 0)
		return decision->status;
	decision->request_id = request->request_id;
	decision->source_node_id = request->source_node_id;
	decision->target_node_id = request->target_node_id;
	decision->tool_id = request->tool_id;
	decision->side_effect_mask = manifest->side_effect_mask;
	if ((request->flags & ~AGENT_PROVENANCE_AUTH_F_ALL) != 0 ||
	    (manifest->accepted_input_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    (manifest->output_add_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    (manifest->output_add_labels &
	     AGENT_PROVENANCE_TRUSTED_USER_CONTROL) != 0 ||
	    (manifest->required_capabilities & ~AGENT_PROVENANCE_CAP_ALL) != 0 ||
	    (manifest->side_effect_mask & ~AGENT_SIDE_EFFECT_ALL) != 0 ||
	    (request->declared_side_effect_mask & ~AGENT_SIDE_EFFECT_ALL) != 0)
		return decision->status;
	current = agent_provenance_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(current) ||
	    !workflow_lifecycle_key_equal(current, request->lifecycle) ||
	    !workflow_lifecycle_active(current)) {
		agent_provenance_decide(
			decision, AGENT_PROVENANCE_DENY_STALE_LIFECYCLE,
			AGENT_STATUS_STALE);
		return decision->status;
	}
	if ((request->flags & AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT) != 0) {
		if (request->contract_generation == 0) {
			agent_provenance_decide(
				decision, AGENT_PROVENANCE_DENY_MISSING_CONTRACT,
				AGENT_STATUS_DENIED);
			return decision->status;
		}
		if ((request->flags & AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED) ==
		    0) {
			agent_provenance_decide(
				decision,
				AGENT_PROVENANCE_DENY_ILLEGAL_PREDECESSOR,
				AGENT_STATUS_DENIED);
			return decision->status;
		}
		if (request->declared_side_effect_mask !=
		    manifest->side_effect_mask) {
			agent_provenance_decide(
				decision, AGENT_PROVENANCE_DENY_EFFECT_MISMATCH,
				AGENT_STATUS_DENIED);
			return decision->status;
		}
	} else if ((request->flags &
		    AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED) != 0) {
		return decision->status;
	}
	if ((p->agent_capability_mask & manifest->required_capabilities) !=
	    manifest->required_capabilities) {
		agent_provenance_decide(
			decision, AGENT_PROVENANCE_DENY_CAPABILITY_MISSING,
			AGENT_STATUS_DENIED);
		return decision->status;
	}
	labels = agent_provenance_current_labels(p);
	if (request->source_context_sequence != 0) {
		/*
		 * A frozen contract already binds an exact completed predecessor
		 * sequence.  Parallel DAG branches may legitimately refer to an
		 * older retained Context record, while legacy calls remain limited
		 * to the currently visible/cause records.
		 */
		if ((request->flags &
		     (AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT |
		      AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED)) !=
			    (AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT |
			     AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED) &&
		    request->source_context_sequence !=
			    p->context_path_visible_head &&
		    request->source_context_sequence !=
			    p->agent_current_cause_sequence) {
			agent_provenance_decide(
				decision,
				AGENT_PROVENANCE_DENY_UNKNOWN_PROVENANCE,
				AGENT_STATUS_DENIED);
			return decision->status;
		}
		uint64 source_labels = agent_provenance_record_labels(
			p, request->source_context_sequence);
		if (source_labels == 0) {
			agent_provenance_decide(
				decision,
				AGENT_PROVENANCE_DENY_UNKNOWN_PROVENANCE,
				AGENT_STATUS_DENIED);
			return decision->status;
		}
		/* Selecting a predecessor can add provenance, never erase taint. */
		labels |= source_labels;
	}
	if ((request->flags & AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT) != 0)
		labels |= AGENT_PROVENANCE_TRUSTED_USER_CONTROL;
	labels |= AGENT_PROVENANCE_AGENT_DERIVED;
	decision->input_labels = labels;
	if ((labels & ~manifest->accepted_input_labels) != 0) {
		agent_provenance_decide(
			decision,
			AGENT_PROVENANCE_DENY_PROVENANCE_NOT_ACCEPTED,
			AGENT_STATUS_DENIED);
		return decision->status;
	}
	decision->output_labels = labels | manifest->output_add_labels |
				  AGENT_PROVENANCE_AGENT_DERIVED;
	agent_provenance_decide(
		decision, AGENT_PROVENANCE_DENY_NONE, AGENT_STATUS_OK);
	return AGENT_STATUS_OK;
}

int
agent_provenance_commit_tool_output(
	struct proc *p, const struct agent_provenance_decision *decision,
	int tool_status)
{
	(void)tool_status;
	if (p == 0 || decision == 0 ||
	    decision->status != AGENT_STATUS_OK || decision->tool_id <= 0 ||
	    (decision->input_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    (decision->output_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    (decision->output_labels & decision->input_labels &
	     AGENT_PROVENANCE_UNTRUSTED_MASK) !=
		    (decision->input_labels & AGENT_PROVENANCE_UNTRUSTED_MASK))
		return AGENT_STATUS_BAD_PARAM;
	agent_provenance_stage_labels(
		p, decision->request_id, decision->tool_id,
		decision->output_labels |
			agent_provenance_current_labels(p));
	return AGENT_STATUS_OK;
}

uint64
agent_provenance_ipc_output_labels(struct proc *source, int kernel_generated)
{
	if (kernel_generated)
		return AGENT_PROVENANCE_KERNEL_FACT |
		       AGENT_PROVENANCE_AGENT_DERIVED;
	if (source == 0)
		return AGENT_PROVENANCE_AGENT_DERIVED |
		       AGENT_PROVENANCE_CROSS_AGENT_DATA;
	return agent_provenance_current_labels(source) |
	       AGENT_PROVENANCE_AGENT_DERIVED |
	       AGENT_PROVENANCE_CROSS_AGENT_DATA;
}

void
agent_provenance_mailbox_publish(struct proc *target, uint64 labels)
{
	struct agent_provenance_proc_state *state;
	int enabled;

	if ((labels & ~AGENT_PROVENANCE_ALL) != 0)
		return;
	enabled = intr_save();
	state = agent_provenance_state_locked(target, 1);
	if (state != 0)
		state->mailbox_labels = labels |
			AGENT_PROVENANCE_AGENT_DERIVED |
			AGENT_PROVENANCE_CROSS_AGENT_DATA;
	intr_restore(enabled);
}

uint64
agent_provenance_mailbox_take(struct proc *p)
{
	struct agent_provenance_proc_state *state;
	uint64 labels = 0;
	int enabled = intr_save();

	state = agent_provenance_state_locked(p, 0);
	if (state != 0) {
		labels = state->mailbox_labels;
		state->mailbox_labels = 0;
		if (labels != 0)
			state->current_labels |= labels |
				AGENT_PROVENANCE_AGENT_DERIVED |
				AGENT_PROVENANCE_CROSS_AGENT_DATA;
	}
	intr_restore(enabled);
	return labels;
}

uint64
agent_provenance_context_flags(struct proc *p, uint64 request_id, int tool_id,
			       uint64 flags)
{
	struct agent_provenance_proc_state *state;
	uint64 labels = AGENT_PROVENANCE_AGENT_DERIVED;
	int enabled = intr_save();

	state = agent_provenance_state_locked(p, 1);
	if (state != 0) {
		labels = state->current_labels;
		if (state->pending && state->pending_request_id == request_id &&
		    state->pending_tool_id == tool_id)
			labels |= state->pending_labels;
	}
	intr_restore(enabled);
	return (flags & ~AGENT_CONTEXT_PROVENANCE_MASK) |
	       AGENT_CONTEXT_PROVENANCE_ENCODE(labels);
}

void
agent_provenance_context_committed(struct proc *p, uint64 request_id,
				   int tool_id, uint64 flags)
{
	struct agent_provenance_proc_state *state;
	uint64 labels = AGENT_CONTEXT_PROVENANCE_DECODE(flags);
	int enabled = intr_save();

	state = agent_provenance_state_locked(p, 1);
	if (state != 0) {
		state->current_labels |= labels == 0 ?
			AGENT_PROVENANCE_AGENT_DERIVED : labels;
		if (state->pending && state->pending_request_id == request_id &&
		    state->pending_tool_id == tool_id) {
			state->pending = 0;
			state->pending_labels = 0;
			state->pending_request_id = 0;
			state->pending_tool_id = 0;
		}
	}
	intr_restore(enabled);
}

void
agent_provenance_context_restore(struct proc *p, uint64 flags)
{
	struct agent_provenance_proc_state *state;
	uint64 labels = AGENT_CONTEXT_PROVENANCE_DECODE(flags);
	int enabled = intr_save();

	state = agent_provenance_state_locked(p, 1);
	if (state != 0) {
		state->current_labels |= labels == 0 ?
			AGENT_PROVENANCE_AGENT_DERIVED : labels;
		state->pending = 0;
		state->pending_labels = 0;
		state->pending_request_id = 0;
		state->pending_tool_id = 0;
	}
	intr_restore(enabled);
}

void
agent_provenance_proc_reset(struct proc *p)
{
	struct agent_provenance_proc_state *state;
	int enabled;

	if (p == 0)
		return;
	enabled = intr_save();
	state = agent_context_provenance_sidecar(p);
	if (state != 0)
		memset(state, 0, sizeof(*state));
	intr_restore(enabled);
}

static void
agent_provenance_abort_staged_labels(struct proc *p, uint64 request_id,
				     int tool_id)
{
	struct agent_provenance_proc_state *state;
	int enabled = intr_save();

	state = agent_provenance_state_locked(p, 0);
	if (state != 0 && state->pending &&
	    state->pending_request_id == request_id &&
	    state->pending_tool_id == tool_id) {
		state->pending = 0;
		state->pending_labels = 0;
		state->pending_request_id = 0;
		state->pending_tool_id = 0;
	}
	intr_restore(enabled);
}

int
agent_provenance_append_security_denial(
	struct proc *p, const struct agent_provenance_request *request,
	const struct agent_provenance_decision *decision, uint64 *ticket_out)
{
	struct agent_evidence_security_reservation reservation;
	struct workflow_lifecycle_key evidence_lifecycle;
	uint64 ticket = 0;
	int result;

	if (ticket_out == 0)
		return AGENT_STATUS_BAD_PARAM;
	*ticket_out = 0;
	if (p == 0 || request == 0 || decision == 0 ||
	    decision->status == AGENT_STATUS_OK ||
	    decision->reason == AGENT_PROVENANCE_DENY_NONE ||
	    request->request_id != decision->request_id ||
	    request->tool_id != decision->tool_id ||
	    request->source_node_id != decision->source_node_id ||
	    request->target_node_id != decision->target_node_id)
		return AGENT_STATUS_BAD_PARAM;
	evidence_lifecycle = agent_provenance_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(evidence_lifecycle) ||
	    workflow_lifecycle_operation_enter(evidence_lifecycle) < 0)
		return AGENT_STATUS_RETRY;
	if (agent_evidence_security_reserve(p, &reservation) < 0) {
		result = AGENT_STATUS_RETRY;
		goto out_operation;
	}
	if (agent_lifecycle_context_lane_enter(p) < 0) {
		agent_evidence_security_abort(&reservation);
		result = AGENT_STATUS_RETRY;
		goto out_operation;
	}
	agent_provenance_stage_labels(
		p, request->request_id, request->tool_id,
		decision->input_labels | AGENT_PROVENANCE_AGENT_DERIVED);
	if (agent_context_append_security_denial_record(
		    p, request, decision, &reservation, &ticket) < 0) {
		agent_evidence_security_abort(&reservation);
		agent_provenance_abort_staged_labels(
			p, request->request_id, request->tool_id);
		result = AGENT_STATUS_NO_SPACE;
		goto out_context_lane;
	}
	if (ticket == 0 || reservation.active)
		panic("security denial atomic publication");
	*ticket_out = ticket;
	result = decision->status;
out_context_lane:
	agent_lifecycle_context_lane_leave(p);
out_operation:
	workflow_lifecycle_operation_leave(evidence_lifecycle);
	return result;
}
