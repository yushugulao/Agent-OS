#include "agent_context.h"
#include "agent_context_path.h"
#include "agent_internal.h"
#include "agent_sha256.h"
#include "defs.h"
#include "fs.h"
#include "vfs_security.h"

#define AGENT_CONTEXT_PREFETCH_TRANSITIONS 16U
#define AGENT_CONTEXT_PREFETCH_READ_CHUNK 512U

struct agent_prefetch_signature {
	uint flags;
	uint operation_type;
	uint tool_id;
	uint dev;
	uint inum;
	uint incarnation;
	uint64 file_revision;
	uint64 offset;
	uint64 length;
	uint64 context_sequence;
	uint64 cause_sequence;
	uint64 tick;
	uint64 branch_generation;
	uint64 workspace_object_id;
	uchar workspace_revision_sha256[32];
	uchar query_fingerprint[32];
};

struct agent_prefetch_transition {
	int used;
	uint observations;
	uint successes;
	uint reserved;
	uint64 from_hash;
	uint64 to_hash;
	struct agent_prefetch_signature target;
};

struct agent_prefetch_state {
	uint64 control_id;
	struct workflow_lifecycle_key lifecycle;
	int last_valid;
	int predicted_valid;
	int pending_guest;
	uint reserved;
	uint64 last_hash;
	uint64 predicted_hash;
	struct agent_prefetch_signature last;
	struct agent_prefetch_signature predicted;
	struct agent_prefetch_transition
		transitions[AGENT_CONTEXT_PREFETCH_TRANSITIONS];
};

static struct agent_prefetch_state agent_prefetch_states[NPROC];
static struct agent_prefetch_state
	agent_prefetch_shared_states[WORKFLOW_LIFECYCLE_CAP];
extern struct proc pool[NPROC];

static struct workflow_lifecycle_key
agent_prefetch_lifecycle(const struct proc *p)
{
	struct workflow_lifecycle_key key = workflow_lifecycle_none();

	if (p != 0 && p->workflow_lifecycle_charged) {
		key.id = p->workflow_lifecycle_id;
		key.generation = p->workflow_lifecycle_generation;
	}
	return key;
}

static int
agent_prefetch_sequence_active(struct proc *p, uint64 sequence)
{
	uint64 cursor;

	if (p == 0 || sequence == 0 || p->context_path_capacity == 0)
		return 0;
	cursor = p->context_path_visible_head;
	for (uint count = 0; cursor != 0 && count < AGENT_CONTEXT_MAX_RECORDS;
	     count++) {
		struct agent_context_record record;

		if (cursor == sequence)
			return 1;
		if (cursor < p->context_path_oldest ||
		    cursor > p->context_path_latest ||
		    agent_context_read_record(
			    p, (cursor - 1U) % p->context_path_capacity,
			    &record) < 0 ||
		    record.sequence != cursor || record.record_hash == 0 ||
		    record.record_hash != agent_context_record_hash(&record) ||
		    record.branch_generation > p->context_branch_generation)
			return 0;
		cursor = record.path_parent_sequence;
	}
	return 0;
}

static uint64
agent_prefetch_signature_hash(const struct agent_prefetch_signature *signature)
{
	struct agent_prefetch_signature stable;
	uchar digest[32];
	uint64 hash = 0;

	stable = *signature;
	stable.context_sequence = 0;
	stable.cause_sequence = 0;
	stable.tick = 0;
	stable.branch_generation = 0;
	agent_sha256(&stable, sizeof(stable), digest);
	memmove(&hash, digest, sizeof(hash));
	return hash == 0 ? 1 : hash;
}

static int
agent_prefetch_signature_equal(const struct agent_prefetch_signature *left,
			       const struct agent_prefetch_signature *right)
{
	struct agent_prefetch_signature left_stable = *left;
	struct agent_prefetch_signature right_stable = *right;

	left_stable.context_sequence = 0;
	left_stable.cause_sequence = 0;
	left_stable.tick = 0;
	left_stable.branch_generation = 0;
	right_stable.context_sequence = 0;
	right_stable.cause_sequence = 0;
	right_stable.tick = 0;
	right_stable.branch_generation = 0;
	return memcmp(&left_stable, &right_stable, sizeof(left_stable)) == 0;
}

static void
agent_prefetch_signature_from_control(
	const struct agent_context_prefetch_control *control,
	struct agent_prefetch_signature *signature)
{
	memset(signature, 0, sizeof(*signature));
	signature->flags = control->flags;
	signature->operation_type = control->operation_type;
	signature->tool_id = control->tool_id;
	signature->dev = control->dev;
	signature->inum = control->inum;
	signature->incarnation = control->incarnation;
	signature->file_revision = control->file_revision;
	signature->offset = control->offset;
	signature->length = control->length;
	signature->context_sequence = control->context_sequence;
	signature->cause_sequence = control->cause_sequence;
	signature->tick = control->tick;
	signature->branch_generation = control->branch_generation;
	signature->workspace_object_id = control->workspace_object_id;
	memmove(signature->workspace_revision_sha256,
		control->workspace_revision_sha256,
		sizeof(signature->workspace_revision_sha256));
	memmove(signature->query_fingerprint, control->query_fingerprint,
		sizeof(signature->query_fingerprint));
}

static struct agent_prefetch_state *
agent_prefetch_state_for(struct proc *p)
{
	if (p == 0 || p < pool || p >= &pool[NPROC])
		return 0;
	return &agent_prefetch_states[p - pool];
}

static void
agent_prefetch_state_bind_locked(struct proc *p,
				 struct agent_prefetch_state *state)
{
	struct workflow_lifecycle_key lifecycle = agent_prefetch_lifecycle(p);

	if (state->control_id != p->agent_control_id ||
	    !workflow_lifecycle_key_equal(state->lifecycle, lifecycle)) {
		memset(state, 0, sizeof(*state));
		state->control_id = p->agent_control_id;
		state->lifecycle = lifecycle;
	}
}

static struct agent_prefetch_state *
agent_prefetch_shared_state_bind_locked(struct workflow_lifecycle_key lifecycle)
{
	struct agent_prefetch_state *state;

	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    lifecycle.id > WORKFLOW_LIFECYCLE_CAP)
		return 0;
	state = &agent_prefetch_shared_states[lifecycle.id - 1U];
	if (!workflow_lifecycle_key_equal(state->lifecycle, lifecycle)) {
		memset(state, 0, sizeof(*state));
		state->lifecycle = lifecycle;
	}
	return state;
}

static struct agent_prefetch_transition *
agent_prefetch_transition_find_locked(struct agent_prefetch_state *state,
				      uint64 from_hash, uint64 to_hash,
				      int create)
{
	struct agent_prefetch_transition *free_slot = 0;
	struct agent_prefetch_transition *least = 0;

	for (uint i = 0; i < AGENT_CONTEXT_PREFETCH_TRANSITIONS; i++) {
		struct agent_prefetch_transition *transition =
			&state->transitions[i];

		if (transition->used && transition->from_hash == from_hash &&
		    transition->to_hash == to_hash)
			return transition;
		if (!transition->used && free_slot == 0)
			free_slot = transition;
		if (transition->used &&
		    (least == 0 || transition->observations < least->observations))
			least = transition;
	}
	if (!create)
		return 0;
	if (free_slot != 0)
		return free_slot;
	return least;
}

static struct agent_prefetch_transition *
agent_prefetch_transition_predict_locked(struct proc *p,
					 struct agent_prefetch_state *state,
					 uint64 from_hash)
{
	struct agent_prefetch_transition *best = 0;

	for (uint i = 0; i < AGENT_CONTEXT_PREFETCH_TRANSITIONS; i++) {
		struct agent_prefetch_transition *transition =
			&state->transitions[i];
		uint confidence;

		if (!transition->used || transition->from_hash != from_hash ||
		    transition->observations < p->agent_prefetch_min_observations)
			continue;
		confidence = transition->observations == 0 ? 0 :
			(uint)(((uint64)transition->successes *
				AGENT_CONTEXT_PREFETCH_CONFIDENCE_SCALE) /
			       transition->observations);
		if (confidence < p->agent_prefetch_confidence_threshold_ppm)
			continue;
		if (best == 0 || transition->successes > best->successes ||
		    (transition->successes == best->successes &&
		     transition->observations > best->observations))
			best = transition;
	}
	return best;
}

static void
agent_prefetch_result_fill(struct proc *p,
			   const struct agent_prefetch_state *state,
			   int status,
			   struct agent_context_prefetch_result *result)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_CONTEXT_PREFETCH_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	if (p == 0)
		return;
	result->enabled = p->agent_prefetch_enabled;
	result->policy = p->agent_prefetch_policy;
	result->max_prefetch_bytes = p->agent_prefetch_max_bytes;
	result->max_inflight = p->agent_prefetch_max_inflight;
	result->inflight = p->agent_prefetch_inflight;
	result->hits = p->agent_prefetch_hits;
	result->misses = p->agent_prefetch_misses;
	result->cancelled = p->agent_prefetch_cancelled;
	result->denied = p->agent_prefetch_denied;
	result->last_training_sequence = p->agent_prefetch_last_training_sequence;
	if (state == 0 || !state->predicted_valid)
		return;
	result->predicted = 1;
	result->target_flags = state->predicted.flags;
	result->target_dev = state->predicted.dev;
	result->target_inum = state->predicted.inum;
	result->target_incarnation = state->predicted.incarnation;
	result->target_file_revision = state->predicted.file_revision;
	result->target_offset = state->predicted.offset;
	result->target_length = state->predicted.length;
	result->target_workspace_object_id =
		state->predicted.workspace_object_id;
	memmove(result->target_workspace_revision_sha256,
		state->predicted.workspace_revision_sha256,
		sizeof(result->target_workspace_revision_sha256));
	memmove(result->target_query_fingerprint,
		state->predicted.query_fingerprint,
		sizeof(result->target_query_fingerprint));
}

void
agent_context_prefetch_init(void)
{
	memset(agent_prefetch_states, 0, sizeof(agent_prefetch_states));
	memset(agent_prefetch_shared_states, 0,
	       sizeof(agent_prefetch_shared_states));
}

void
agent_context_prefetch_proc_reset(struct proc *p)
{
	struct agent_prefetch_state *state = agent_prefetch_state_for(p);
	int enabled;

	if (state == 0)
		return;
	enabled = intr_save();
	memset(state, 0, sizeof(*state));
	p->agent_prefetch_inflight = 0;
	p->agent_prefetch_last_signature_hash = 0;
	p->agent_prefetch_last_training_sequence = 0;
	intr_restore(enabled);
}

void
agent_context_prefetch_rollback(struct proc *p)
{
	struct agent_prefetch_state *state = agent_prefetch_state_for(p);
	struct agent_prefetch_state *shared;
	struct workflow_lifecycle_key lifecycle;
	int enabled;

	if (state == 0)
		return;
	enabled = intr_save();
	agent_prefetch_state_bind_locked(p, state);
	lifecycle = state->lifecycle;
	if (state->pending_guest) {
		p->agent_prefetch_cancelled++;
	}
	memset(state, 0, sizeof(*state));
	state->control_id = p->agent_control_id;
	state->lifecycle = lifecycle;
	shared = agent_prefetch_shared_state_bind_locked(lifecycle);
	if (shared != 0) {
		memset(shared, 0, sizeof(*shared));
		shared->lifecycle = lifecycle;
	}
	p->agent_prefetch_inflight = 0;
	p->agent_prefetch_last_signature_hash = 0;
	intr_restore(enabled);
}

void agent_context_prefetch_reclaim_lifecycle(
	struct workflow_lifecycle_key lifecycle)
{
	int enabled;

	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    lifecycle.id > WORKFLOW_LIFECYCLE_CAP)
		return;
	enabled = intr_save();
	if (workflow_lifecycle_key_equal(
		    agent_prefetch_shared_states[lifecycle.id - 1U].lifecycle,
		    lifecycle))
		memset(&agent_prefetch_shared_states[lifecycle.id - 1U], 0,
		       sizeof(agent_prefetch_shared_states[0]));
	intr_restore(enabled);
}

static int
agent_context_prefetch_configure(
	struct proc *p, const struct agent_context_prefetch_control *control)
{
	struct agent_prefetch_state *state = agent_prefetch_state_for(p);
	int enabled;

	if (!agent_identity_has_cap(p, AGENT_CAP_PREFETCH) ||
	    control->flags != 0 ||
	    control->policy != AGENT_CONTEXT_PREFETCH_POLICY_TRANSITION ||
	    control->min_observations == 0 ||
	    control->confidence_threshold_ppm == 0 ||
	    control->confidence_threshold_ppm >
		AGENT_CONTEXT_PREFETCH_CONFIDENCE_SCALE ||
	    control->max_prefetch_bytes == 0 ||
	    control->max_prefetch_bytes > AGENT_CONTEXT_PREFETCH_MAX_BYTES ||
	    control->max_inflight == 0 ||
	    control->max_inflight > AGENT_CONTEXT_PREFETCH_MAX_INFLIGHT ||
	    state == 0)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	agent_prefetch_state_bind_locked(p, state);
	p->agent_prefetch_enabled = 1;
	p->agent_prefetch_policy = control->policy;
	p->agent_prefetch_min_observations = control->min_observations;
	p->agent_prefetch_confidence_threshold_ppm =
		control->confidence_threshold_ppm;
	p->agent_prefetch_max_bytes = control->max_prefetch_bytes;
	p->agent_prefetch_max_inflight = control->max_inflight;
	intr_restore(enabled);
	return AGENT_STATUS_OK;
}

static int
agent_context_prefetch_record(
	struct proc *p, const struct agent_context_prefetch_control *control,
	struct agent_context_prefetch_result *result)
{
	struct agent_prefetch_signature signature;
	struct agent_prefetch_transition *transition;
	struct agent_prefetch_transition *prediction;
	struct agent_prefetch_state *state = agent_prefetch_state_for(p);
	struct agent_prefetch_state *history_state;
	struct workflow_lifecycle_key lifecycle = agent_prefetch_lifecycle(p);
	uint64 hash;
	uint64 issued_hash = 0;
	uint issued_observations = 0;
	uint issued_confidence = 0;
	int emit_host_hint = 0;
	int enabled;

	if (state == 0 || !p->agent_prefetch_enabled ||
	    !agent_identity_has_cap(p, AGENT_CAP_PREFETCH) ||
	    control->result_status != AGENT_STATUS_OK ||
	    (control->flags & ~AGENT_CONTEXT_PREFETCH_F_ALL) != 0 ||
	    (control->flags & AGENT_CONTEXT_PREFETCH_F_READ_ONLY) == 0 ||
	    ((control->flags & AGENT_CONTEXT_PREFETCH_F_HOST) == 0 &&
	     (control->dev == 0 || control->inum == 0 ||
	      control->incarnation == 0)) ||
	    ((control->flags & AGENT_CONTEXT_PREFETCH_F_HOST) != 0 &&
	     control->workspace_object_id == 0) ||
	    control->length == 0 || control->context_sequence == 0 ||
	    control->workflow_lifecycle_id != lifecycle.id ||
	    control->workflow_lifecycle_generation != lifecycle.generation ||
	    control->branch_generation != p->context_branch_generation ||
	    control->agent_id != (uint)p->agent_id ||
	    control->agent_control_id != p->agent_control_id ||
	    !agent_prefetch_sequence_active(p, control->context_sequence) ||
	    ((control->flags & AGENT_CONTEXT_PREFETCH_F_SHARED) != 0 &&
	     !agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))) {
		p->agent_prefetch_denied++;
		return AGENT_STATUS_DENIED;
	}
	agent_prefetch_signature_from_control(control, &signature);
	hash = agent_prefetch_signature_hash(&signature);
	enabled = intr_save();
	agent_prefetch_state_bind_locked(p, state);
	if (state->predicted_valid) {
		if (state->predicted_hash == hash &&
		    agent_prefetch_signature_equal(&state->predicted, &signature))
			p->agent_prefetch_hits++;
		else {
			p->agent_prefetch_misses++;
			if (state->pending_guest)
				p->agent_prefetch_cancelled++;
		}
		state->predicted_valid = 0;
		state->pending_guest = 0;
		p->agent_prefetch_inflight = 0;
	}
	history_state = (control->flags & AGENT_CONTEXT_PREFETCH_F_SHARED) != 0 ?
		agent_prefetch_shared_state_bind_locked(lifecycle) : state;
	if (history_state == 0) {
		intr_restore(enabled);
		return AGENT_STATUS_STALE;
	}
	if (history_state->last_valid) {
		transition = agent_prefetch_transition_find_locked(
			history_state, history_state->last_hash, hash, 1);
		if (!transition->used || transition->from_hash != history_state->last_hash ||
		    transition->to_hash != hash) {
			memset(transition, 0, sizeof(*transition));
			transition->used = 1;
			transition->from_hash = history_state->last_hash;
			transition->to_hash = hash;
			transition->target = signature;
		}
		if (transition->observations != (uint)~0U)
			transition->observations++;
		if (transition->successes != (uint)~0U)
			transition->successes++;
		transition->target = signature;
	}
	history_state->last = signature;
	history_state->last_hash = hash;
	history_state->last_valid = 1;
	p->agent_prefetch_last_signature_hash = hash;
	p->agent_prefetch_last_training_sequence = control->context_sequence;
	prediction = agent_prefetch_transition_predict_locked(
		p, history_state, hash);
	if (prediction != 0 && p->agent_prefetch_inflight <
				      p->agent_prefetch_max_inflight) {
		state->predicted = prediction->target;
		state->predicted_hash = prediction->to_hash;
		state->predicted_valid = 1;
		issued_hash = prediction->to_hash;
		issued_observations = prediction->observations;
		issued_confidence = prediction->observations == 0 ? 0 :
			(uint)(((uint64)prediction->successes *
				AGENT_CONTEXT_PREFETCH_CONFIDENCE_SCALE) /
			       prediction->observations);
		if ((prediction->target.flags &
		     AGENT_CONTEXT_PREFETCH_F_HOST) != 0)
			emit_host_hint = 1;
		else {
			state->pending_guest = 1;
			p->agent_prefetch_inflight++;
			agent_background_request();
		}
	}
	agent_prefetch_result_fill(p, state, AGENT_STATUS_OK, result);
	result->observations = issued_observations;
	result->confidence_ppm = issued_confidence;
	intr_restore(enabled);
	if (emit_host_hint &&
	    agent_ipc_prefetch_hint(p, issued_hash) != AGENT_STATUS_OK) {
		enabled = intr_save();
		if (state->predicted_valid && state->predicted_hash ==
						      issued_hash) {
			state->predicted_valid = 0;
			p->agent_prefetch_denied++;
		}
		agent_prefetch_result_fill(p, state, AGENT_STATUS_NO_SPACE, result);
		intr_restore(enabled);
		return AGENT_STATUS_NO_SPACE;
	}
	return AGENT_STATUS_OK;
}

void
agent_context_prefetch_background_maintain(struct proc *p)
{
	struct agent_prefetch_state *state = agent_prefetch_state_for(p);
	struct agent_prefetch_signature signature;
	struct workflow_lifecycle_key lifecycle;
	struct inode *ip = 0;
	struct vfs_cred cred;
	uchar chunk[AGENT_CONTEXT_PREFETCH_READ_CHUNK];
	uint64 remaining;
	uint64 offset;
	uint64 hash;
	int status = AGENT_STATUS_STALE;
	int enabled;

	if (state == 0)
		return;
	enabled = intr_save();
	agent_prefetch_state_bind_locked(p, state);
	if (!state->pending_guest || !state->predicted_valid) {
		intr_restore(enabled);
		return;
	}
	signature = state->predicted;
	hash = state->predicted_hash;
	lifecycle = state->lifecycle;
	intr_restore(enabled);
	if (!proc_teardown_live(p) || !p->agent_prefetch_enabled ||
	    !workflow_lifecycle_key_equal(lifecycle,
					 agent_prefetch_lifecycle(p)))
		goto done;
	ip = inode_get(signature.dev, signature.inum);
	if (ip == 0 || ivalid(ip) < 0 || ip->type != T_FILE ||
	    ip->vfs_incarnation != signature.incarnation ||
	    (signature.file_revision != 0 &&
	     ip->vfs_policy_generation != signature.file_revision))
		goto done;
	vfs_cred_from_proc(p, &cred);
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_READ)) {
		status = AGENT_STATUS_DENIED;
		goto done;
	}
	remaining = signature.length > p->agent_prefetch_max_bytes ?
			p->agent_prefetch_max_bytes : signature.length;
	offset = signature.offset;
	status = AGENT_STATUS_OK;
	while (remaining != 0) {
		uint length = remaining > sizeof(chunk) ?
			      sizeof(chunk) : (uint)remaining;
		int got = readi(ip, &cred, 0, (uint64)chunk, (uint)offset,
				length);

		if (got < 0) {
			status = AGENT_STATUS_IO_ERROR;
			break;
		}
		if (got == 0)
			break;
		offset += (uint)got;
		remaining -= (uint)got;
	}
done:
	if (ip != 0)
		iput(ip);
	enabled = intr_save();
	if (state->control_id == p->agent_control_id &&
	    workflow_lifecycle_key_equal(state->lifecycle, lifecycle) &&
	    state->pending_guest && state->predicted_valid &&
	    state->predicted_hash == hash) {
		state->pending_guest = 0;
		if (p->agent_prefetch_inflight != 0)
			p->agent_prefetch_inflight--;
		if (status != AGENT_STATUS_OK) {
			state->predicted_valid = 0;
			if (status == AGENT_STATUS_DENIED)
				p->agent_prefetch_denied++;
			else
				p->agent_prefetch_cancelled++;
		}
	}
	intr_restore(enabled);
}

int
sys_agent_context_prefetch(uint64 controladdr, uint64 resultaddr)
{
	struct agent_context_prefetch_control control;
	struct agent_context_prefetch_result result;
	struct agent_prefetch_state *state;
	struct proc *p = curr_proc();
	int status = AGENT_STATUS_BAD_PARAM;
	int enabled;

	memset(&result, 0, sizeof(result));
	if (p == 0 || !p->is_agent || controladdr == 0 || resultaddr == 0 ||
	    user_range_check(p->pagetable, controladdr, sizeof(control), PTE_R) < 0 ||
	    user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0 ||
	    copyin(p->pagetable, (char *)&control, controladdr,
		   sizeof(control)) < 0)
		return -1;
	if (control.version != AGENT_CONTEXT_PREFETCH_VERSION ||
	    control.size != sizeof(control))
		goto copy;
	for (uint i = 0; i < 5; i++)
		if (control.reserved_tail[i] != 0)
			goto copy;
	state = agent_prefetch_state_for(p);
	if (control.operation == AGENT_CONTEXT_PREFETCH_CONFIGURE) {
		status = agent_context_prefetch_configure(p, &control);
	} else if (control.operation == AGENT_CONTEXT_PREFETCH_RECORD) {
		status = agent_context_prefetch_record(p, &control, &result);
	} else if (control.operation == AGENT_CONTEXT_PREFETCH_STATUS) {
		enabled = intr_save();
		agent_prefetch_state_bind_locked(p, state);
		status = AGENT_STATUS_OK;
		agent_prefetch_result_fill(p, state, status, &result);
		intr_restore(enabled);
	} else if (control.operation == AGENT_CONTEXT_PREFETCH_CLEAR) {
		agent_context_prefetch_proc_reset(p);
		status = AGENT_STATUS_OK;
	} else {
		status = AGENT_STATUS_BAD_PARAM;
	}
copy:
	if (result.version == 0)
		agent_prefetch_result_fill(p, agent_prefetch_state_for(p),
					   status, &result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	return status;
}
