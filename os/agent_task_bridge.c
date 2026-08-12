#include "agent_task_bridge.h"
#include "agent_context.h"
#include "agent_context_path.h"
#include "agent_execution_contract.h"
#include "agent_internal.h"
#include "agent_lifecycle.h"
#include "agent_provenance.h"
#include "agent_sha256.h"
#include "agent_task_channel.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "open_file_io_lease.h"
#include "proc.h"
#include "timer.h"
#include "vfs_security.h"

_Static_assert(AGENT_TASK_RESOURCE_SNAPSHOT_SIZE == AGENT_OP_PAYLOAD_SIZE,
	       "Task resource snapshot must fit one tool payload");

static uint64
agent_task_bridge_now(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static uint64
agent_task_bridge_request_deadline(const struct agent_task_sqe *sqe)
{
	return (sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 ?
		sqe->deadline_tick : 0;
}

static int
agent_task_bridge_handle_null(struct agent_task_resource_handle handle)
{
	return handle.slot == 0 && handle.type == 0 && handle.flags == 0 &&
	       handle.generation == 0;
}

static int
agent_task_bridge_null_input(const struct agent_task_sqe *sqe,
			     const struct agent_task_resource_view *input)
{
	uchar aggregate = 0;

	if (sqe == 0 || input == 0 ||
	    !agent_task_bridge_handle_null(sqe->input) ||
	    !agent_task_bridge_handle_null(input->handle))
		return 0;
	for (uint i = 0; i < sizeof(input->content_digest); i++)
		aggregate |= input->content_digest[i];
	for (uint i = 0; i < sizeof(input->snapshot); i++)
		aggregate |= input->snapshot[i];
	return aggregate == 0 && input->source_handle == 0 && input->length == 0 &&
	       input->provenance_labels == 0 &&
	       input->producer_context_sequence == 0 &&
	       input->producer_control_id == 0 &&
	       input->producer_node_id == 0 && input->producer_pid == 0;
}

static int
agent_task_bridge_utf8_valid(const uchar *text, uint length)
{
	uint i = 0;

	while (i < length) {
		uchar first = text[i++];

		if (first <= 0x7fU)
			continue;
		if (first >= 0xc2U && first <= 0xdfU) {
			if (i >= length || (text[i++] & 0xc0U) != 0x80U)
				return 0;
			continue;
		}
		if (first >= 0xe0U && first <= 0xefU) {
			uchar second;

			if (i + 1U >= length)
				return 0;
			second = text[i++];
			if ((second & 0xc0U) != 0x80U ||
			    (first == 0xe0U && second < 0xa0U) ||
			    (first == 0xedU && second >= 0xa0U) ||
			    (text[i++] & 0xc0U) != 0x80U)
				return 0;
			continue;
		}
		if (first >= 0xf0U && first <= 0xf4U) {
			uchar second;

			if (i + 2U >= length)
				return 0;
			second = text[i++];
			if ((second & 0xc0U) != 0x80U ||
			    (first == 0xf0U && second < 0x90U) ||
			    (first == 0xf4U && second >= 0x90U) ||
			    (text[i++] & 0xc0U) != 0x80U ||
			    (text[i++] & 0xc0U) != 0x80U)
				return 0;
			continue;
		}
		return 0;
	}
	return 1;
}

static int
agent_task_bridge_resource_input(
	const struct agent_task_sqe *sqe,
	const struct agent_task_resource_view *input)
{
	uchar digest = 0;
	uint length;

	if (sqe == 0 || input == 0 ||
	    agent_task_bridge_handle_null(input->handle) ||
	    sqe->tool_id != AGENT_TOOL_ECHO ||
	    input->handle.type != AGENT_ARTIFACT_UTF8 ||
	    (input->handle.flags != AGENT_TASK_HANDLE_F_OWNED &&
	     input->handle.flags != AGENT_TASK_HANDLE_F_BORROWED) ||
	    input->length == 0 ||
	    input->length > AGENT_TASK_RESOURCE_UTF8_MAX ||
	    input->provenance_labels == 0 ||
	    (input->provenance_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    (input->provenance_labels &
	     AGENT_PROVENANCE_UNTRUSTED_FILE_DATA) == 0 ||
	    input->producer_context_sequence == 0 ||
	    input->producer_control_id == 0 || input->producer_pid <= 0 ||
	    input->producer_node_id != AGENT_EXECUTION_NODE_NONE)
		return 0;
	length = (uint)input->length;
	if (input->snapshot[length] != 0)
		return 0;
	for (uint i = 0; i < length; i++)
		if (input->snapshot[i] == 0)
			return 0;
	for (uint i = 0; i < sizeof(input->content_digest); i++)
		digest |= input->content_digest[i];
	return digest != 0 &&
	       agent_task_bridge_utf8_valid(input->snapshot, length);
}

static int
agent_task_bridge_op(const struct agent_task_sqe *sqe,
		     const struct agent_task_resource_view *input,
		     struct agent_op *op)
{
	if (sqe == 0 || input == 0 || op == 0)
		return -1;
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = sqe->tool_id;
	op->request_id = sqe->request_id;
	if (agent_task_bridge_handle_null(input->handle))
		return agent_task_bridge_null_input(sqe, input) ? 0 : -1;
	if (!agent_task_bridge_resource_input(sqe, input))
		return -1;
	memmove(op->payload, input->snapshot, (uint)input->length + 1U);
	return 0;
}

static void
agent_task_bridge_binding(const struct agent_task_sqe *sqe,
			  const struct agent_task_resource_view *input,
			  const struct agent_op *op,
			  struct agent_execution_binding *binding)
{
	memset(binding, 0, sizeof(*binding));
	binding->internal_flags =
		AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL;
	binding->lifecycle.id = sqe->contract.lifecycle.id;
	binding->lifecycle.generation = sqe->contract.lifecycle.generation;
	binding->contract_generation = sqe->contract.generation;
	binding->node_id = sqe->node_id;
	binding->attempt_id = sqe->attempt_id;
	memmove(binding->schema_digest, sqe->schema_digest,
		sizeof(binding->schema_digest));

	if (agent_task_bridge_handle_null(input->handle)) {
		binding->input_artifact_type = AGENT_ARTIFACT_NONE;
		binding->source_node_id = AGENT_EXECUTION_NODE_NONE;
		binding->input_mode = AGENT_EXECUTION_INPUT_INLINE;
		agent_execution_inline_input_fingerprint(
			op, binding->input_fingerprint);
		return;
	}

	binding->input_artifact_type = input->handle.type;
	binding->source_node_id = input->producer_node_id;
	binding->input_mode = AGENT_EXECUTION_INPUT_RESOURCE;
	binding->input_flags =
		input->handle.flags == AGENT_TASK_HANDLE_F_OWNED ?
			AGENT_EXECUTION_INPUT_F_OWNED :
			AGENT_EXECUTION_INPUT_F_BORROWED;
	binding->resource_slot = input->handle.slot;
	binding->resource_generation = input->handle.generation;
	binding->input_provenance_labels = input->provenance_labels;
	binding->source_context_sequence = input->producer_context_sequence;
	binding->source_control_id = input->producer_control_id;
	binding->source_pid = input->producer_pid;
	memmove(binding->input_fingerprint, input->content_digest,
		sizeof(binding->input_fingerprint));
}

static uint
agent_task_bridge_completion_flags(int status, uint decision_reason,
			   int linked)
{
	uint flags = 0;

	if (status == AGENT_STATUS_CANCELLED) {
		flags |= AGENT_TASK_CQE_F_CANCELLED;
		if (linked &&
		    decision_reason == AGENT_EXECUTION_REASON_DEPENDENCY_FAILED)
			flags |= AGENT_TASK_CQE_F_LINK_FAILED;
	} else if (status == AGENT_STATUS_TIMEOUT) {
		flags |= AGENT_TASK_CQE_F_DEADLINE;
	} else if (status == AGENT_STATUS_DENIED) {
		flags |= AGENT_TASK_CQE_F_DENIED;
	}
	return flags;
}

static int
agent_task_bridge_completion_valid(
	const struct agent_task_completion *completion)
{
	return completion->context_sequence != 0 &&
	       completion->evidence_ticket != 0 &&
	       completion->provenance_labels != 0 &&
	       (completion->provenance_labels & ~AGENT_PROVENANCE_ALL) == 0 &&
	       agent_task_bridge_handle_null(completion->result);
}

static void
agent_task_bridge_execution_completion(
	const struct agent_result *result,
	const struct agent_execution_outcome *outcome,
	const struct agent_task_sqe *sqe,
	struct agent_task_completion *completion)
{
	memset(completion, 0, sizeof(*completion));
	completion->status = result->status;
	completion->decision_reason = outcome->decision_reason;
	completion->flags = agent_task_bridge_completion_flags(
		result->status, outcome->decision_reason,
		(sqe->flags & AGENT_TASK_SQE_F_LINK) != 0);
	if ((outcome->completion_flags & AGENT_RESPONSE_V3_F_CACHED) != 0)
		completion->internal_flags |=
			AGENT_TASK_COMPLETION_INTERNAL_F_CACHED;
	completion->context_sequence = result->sequence;
	completion->evidence_ticket = outcome->evidence_ticket;
	completion->provenance_labels = outcome->output_provenance_labels;
	completion->terminal_tick = outcome->terminal_tick;
	completion->completion_tick = agent_task_bridge_now();
}

static int
agent_task_bridge_submit_completion_canonical(
	const struct agent_task_sqe *sqe,
	const struct agent_task_completion *completion)
{
	if (completion->status == AGENT_STATUS_CANCELLED)
		return completion->decision_reason ==
			       AGENT_EXECUTION_REASON_DEPENDENCY_FAILED &&
		       completion->flags ==
			       (AGENT_TASK_CQE_F_CANCELLED |
				(((sqe->flags & AGENT_TASK_SQE_F_LINK) != 0) ?
					 AGENT_TASK_CQE_F_LINK_FAILED : 0));
	if (completion->status == AGENT_STATUS_TIMEOUT)
		return (sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
		       sqe->deadline_tick != 0 &&
		       completion->terminal_tick >= sqe->deadline_tick &&
		       completion->flags == AGENT_TASK_CQE_F_DEADLINE;
	return completion->flags ==
	       (completion->status == AGENT_STATUS_DENIED ?
			AGENT_TASK_CQE_F_DENIED : 0);
}

static void
agent_task_bridge_cancel_request(
	const struct agent_task_sqe *sqe,
	struct agent_execution_cancel_request *request, int target_is_link)
{
	memset(request, 0, sizeof(*request));
	request->lifecycle.id = sqe->contract.lifecycle.id;
	request->lifecycle.generation = sqe->contract.lifecycle.generation;
	request->contract_generation = sqe->contract.generation;
	request->target_request_id = target_is_link ?
					 sqe->link_request_id : sqe->request_id;
	request->node_id = sqe->node_id;
	request->attempt_id = sqe->attempt_id;
	request->tool_id = sqe->tool_id;
	memmove(request->schema_digest, sqe->schema_digest,
		sizeof(request->schema_digest));
}

static uint
agent_task_bridge_admission_policy(const struct agent_task_sqe *sqe)
{
	uint flags = AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY;

	if ((sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0)
		flags |= AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE;
	return flags;
}

static int
agent_task_bridge_validate(struct proc *p, const struct agent_task_sqe *sqe,
			   const struct agent_task_resource_view *input,
			   struct agent_task_validation *validation,
			   struct agent_task_completion *completion)
{
	struct agent_execution_preflight_result preflight;
	struct agent_execution_binding binding;
	struct agent_op op;
	uint flags;
	int status;

	if (validation == 0 || completion == 0 ||
	    agent_task_bridge_op(sqe, input, &op) < 0)
		return AGENT_TASK_CHANNEL_RETRY;
	agent_task_bridge_binding(sqe, input, &op, &binding);
	flags = agent_task_bridge_admission_policy(sqe);
	status = agent_execution_contract_preflight(
		p, &binding, &op, agent_task_bridge_request_deadline(sqe),
		flags, agent_task_bridge_now(), &preflight);
	if (status < 0 || preflight.status == AGENT_STATUS_RETRY ||
	    preflight.status == AGENT_STATUS_NO_SPACE ||
	    preflight.status == AGENT_STATUS_NOT_AGENT)
		return AGENT_TASK_CHANNEL_RETRY;
	if (preflight.output_artifact_type != AGENT_ARTIFACT_NONE &&
	    (preflight.status == AGENT_STATUS_OK ||
	     preflight.status == AGENT_STATUS_TIMEOUT ||
	     preflight.status == AGENT_STATUS_CANCELLED))
		return AGENT_TASK_CHANNEL_RETRY;
	validation->output_artifact_type = AGENT_ARTIFACT_NONE;
	validation->reserved = 0;
	validation->output_provenance_labels = 0;
	return AGENT_TASK_HOOK_PENDING;
}

static int
agent_task_bridge_submit(struct proc *p, const struct agent_task_sqe *sqe,
			 const struct agent_task_resource_view *input,
			 const struct agent_task_validation *validation,
			 struct agent_task_completion *completion)
{
	struct agent_execution_binding binding;
	struct agent_execution_outcome outcome;
	struct agent_result result;
	struct agent_op op;
	int status;

	if (validation == 0 || completion == 0 ||
	    validation->output_artifact_type != AGENT_ARTIFACT_NONE ||
	    validation->reserved != 0 ||
	    validation->output_provenance_labels != 0)
		panic("Task bridge submit validation");
	if (agent_task_bridge_op(sqe, input, &op) < 0)
		panic("Task bridge submit input");
	agent_task_bridge_binding(sqe, input, &op, &binding);
	memset(&result, 0, sizeof(result));
	memset(&outcome, 0, sizeof(outcome));
	status = agent_execution_task_submit_sync(
		p, &op, &binding, agent_task_bridge_request_deadline(sqe),
		agent_task_bridge_admission_policy(sqe), &result, &outcome);
	if (status < 0 || outcome.output_artifact_type != AGENT_ARTIFACT_NONE ||
	    outcome.output_provenance_labels == 0 ||
	    (outcome.output_provenance_labels & ~AGENT_PROVENANCE_ALL) != 0)
		panic("Task bridge submit outcome");
	agent_task_bridge_execution_completion(
		&result, &outcome, sqe, completion);
	if (!agent_task_bridge_completion_valid(completion) ||
	    !agent_task_bridge_submit_completion_canonical(sqe, completion))
		panic("Task bridge submit completion");
	return completion->status == AGENT_STATUS_DENIED ||
		       completion->status == AGENT_STATUS_STALE ?
		       AGENT_TASK_HOOK_DENIED : AGENT_TASK_HOOK_COMPLETE;
}

static int
agent_task_bridge_cancel(struct proc *p, const struct agent_task_sqe *sqe,
			 struct agent_task_completion *completion)
{
	struct agent_execution_cancel_request request;
	struct agent_execution_outcome outcome;
	struct agent_result result;
	int status;

	if (p == 0 || sqe == 0 || completion == 0 ||
	    sqe->link_request_id == 0)
		return AGENT_TASK_CHANNEL_RETRY;
	agent_task_bridge_cancel_request(sqe, &request, 1);
	memset(&result, 0, sizeof(result));
	memset(&outcome, 0, sizeof(outcome));
	if (sqe->request_id == 0) {
		status = agent_execution_force_cancel_sync(
			p, &request, &result, &outcome);
		if (status == AGENT_EXECUTION_FORCE_CANCEL_PENDING)
			return AGENT_TASK_HOOK_PENDING;
		if (status == AGENT_EXECUTION_FORCE_CANCEL_DENIED)
			return AGENT_TASK_HOOK_DENIED;
		if (status != AGENT_EXECUTION_FORCE_CANCEL_COMPLETE &&
		    status != AGENT_EXECUTION_FORCE_CANCEL_CACHED)
			return AGENT_TASK_CHANNEL_RETRY;
	} else {
		status = agent_execution_cancel_sync(
			p, &request, &result, &outcome);
		if (status == AGENT_EXECUTION_CANCEL_SYNC_PENDING)
			return AGENT_TASK_HOOK_PENDING;
		if (status == AGENT_EXECUTION_CANCEL_SYNC_DENIED)
			return AGENT_TASK_HOOK_DENIED;
		if (status != AGENT_EXECUTION_CANCEL_SYNC_COMPLETE)
			return AGENT_TASK_CHANNEL_RETRY;
	}
	agent_task_bridge_execution_completion(
		&result, &outcome, sqe, completion);
	if (!agent_task_bridge_completion_valid(completion) ||
	    completion->status != AGENT_STATUS_CANCELLED ||
	    completion->flags != AGENT_TASK_CQE_F_CANCELLED)
		return AGENT_TASK_CHANNEL_RETRY;
	return AGENT_TASK_HOOK_COMPLETE;
}

static int
agent_task_bridge_expire(struct proc *p, const struct agent_task_sqe *sqe,
			 struct agent_task_completion *completion)
{
	struct agent_execution_cancel_request request;
	struct agent_execution_outcome outcome;
	struct agent_result result;
	uint64 now;
	int status;

	if (p == 0 || sqe == 0 || completion == 0 ||
	    (sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) == 0 ||
	    sqe->deadline_tick == 0)
		return AGENT_TASK_CHANNEL_RETRY;
	now = agent_task_bridge_now();
	if (now < sqe->deadline_tick)
		return AGENT_TASK_CHANNEL_RETRY;
	agent_task_bridge_cancel_request(sqe, &request, 0);
	memset(&result, 0, sizeof(result));
	memset(&outcome, 0, sizeof(outcome));
	status = agent_execution_timeout_sync(
		p, &request, sqe->deadline_tick, now, &result, &outcome);
	if (status != AGENT_EXECUTION_TIMEOUT_SYNC_COMPLETE &&
	    status != AGENT_EXECUTION_TIMEOUT_SYNC_CACHED)
		return AGENT_TASK_CHANNEL_RETRY;
	agent_task_bridge_execution_completion(
		&result, &outcome, sqe, completion);
	if (!agent_task_bridge_completion_valid(completion) ||
	    completion->status != AGENT_STATUS_TIMEOUT ||
	    (completion->flags & AGENT_TASK_CQE_F_DEADLINE) == 0)
		return AGENT_TASK_CHANNEL_RETRY;
	return AGENT_TASK_HOOK_COMPLETE;
}

static int
agent_task_bridge_resource_import(
	struct proc *p, struct file *file,
	const struct agent_task_channel_resource *control,
	struct agent_task_resource_import *imported)
{
	struct agent_context_record context;
	struct open_file_io_token lease = OPEN_FILE_IO_TOKEN_INIT;
	struct vfs_cred cred;
	uint64 context_hash;
	uint64 context_sequence;
	uint64 provenance_labels;
	uint length;
	int got;
	int context_lane_held = 0;
	int lease_held = 0;
	int status = AGENT_TASK_CHANNEL_BAD_REQUEST;

	if (imported == 0)
		return status;
	memset(imported, 0, sizeof(*imported));
	if (p == 0 || control == 0 || !p->is_agent || p->pid <= 0 ||
	    p->agent_control_id == 0 ||
	    control->resource_type != AGENT_ARTIFACT_UTF8 ||
	    control->resource_flags != AGENT_TASK_HANDLE_F_OWNED ||
	    control->source_handle >= FD_BUFFER_SIZE || control->length == 0 ||
	    control->length > AGENT_TASK_RESOURCE_UTF8_MAX || file == 0)
		return status;
	length = (uint)control->length;
	if (!file->readable || file->type != FD_INODE || file->ip == 0) {
		status = AGENT_TASK_CHANNEL_BAD_REQUEST;
		goto out;
	}
	got = ivalid(file->ip);
	if (got < 0) {
		status = got == FS_LOOKUP_BUSY ? AGENT_TASK_CHANNEL_RETRY :
					       AGENT_TASK_CHANNEL_EVIDENCE;
		goto out;
	}
	if (file->ip->type != T_FILE) {
		status = AGENT_TASK_CHANNEL_BAD_REQUEST;
		goto out;
	}
	vfs_cred_from_proc(p, &cred);
	if (!vfs_inode_authorize(file->ip, &cred, VFS_OP_READ)) {
		status = AGENT_TASK_CHANNEL_DENIED;
		goto out;
	}
	if (agent_lifecycle_context_lane_enter(p) < 0) {
		status = AGENT_TASK_CHANNEL_RETRY;
		goto out;
	}
	context_lane_held = 1;
	context_sequence = p->context_path_latest;
	if (context_sequence == 0 || p->context_path_count == 0 ||
	    p->context_path_capacity == 0 ||
	    context_sequence < p->context_path_oldest ||
	    agent_context_read_record(
		    p, (context_sequence - 1U) % p->context_path_capacity,
		    &context) < 0 ||
	    context.sequence != context_sequence || context.record_hash == 0 ||
	    context.record_hash != agent_context_record_hash(&context)) {
		status = AGENT_TASK_CHANNEL_EVIDENCE;
		goto out;
	}
	context_hash = context.record_hash;
	provenance_labels = agent_provenance_current_labels(p);
	if (open_file_io_lease_acquire(
		    file, VFS_OP_READ, &lease, &cred) < 0) {
		status = AGENT_TASK_CHANNEL_RETRY;
		goto out;
	}
	lease_held = 1;
	got = readi_lease(file->ip, &cred, &lease, 0,
			  (uint64)imported->snapshot, 0, length + 1U);
	if (got < 0) {
		status = got == FS_LOOKUP_BUSY ? AGENT_TASK_CHANNEL_RETRY :
					       AGENT_TASK_CHANNEL_EVIDENCE;
		goto out;
	}
	if ((uint)got != length) {
		status = AGENT_TASK_CHANNEL_BAD_REQUEST;
		goto out;
	}
	if (p->context_path_latest != context_sequence ||
	    p->context_path_count == 0 || p->context_path_capacity == 0 ||
	    context_sequence < p->context_path_oldest ||
	    agent_context_read_record(
		    p, (context_sequence - 1U) % p->context_path_capacity,
		    &context) < 0 ||
	    context.sequence != context_sequence ||
	    context.record_hash != context_hash ||
	    context.record_hash != agent_context_record_hash(&context) ||
	    agent_provenance_current_labels(p) != provenance_labels) {
		status = AGENT_TASK_CHANNEL_RETRY;
		goto out;
	}
	for (uint i = 0; i < length; i++) {
		if (imported->snapshot[i] == 0) {
			status = AGENT_TASK_CHANNEL_BAD_REQUEST;
			goto out;
		}
	}
	if (!agent_task_bridge_utf8_valid(imported->snapshot, length)) {
		status = AGENT_TASK_CHANNEL_BAD_REQUEST;
		goto out;
	}
	imported->snapshot[length] = 0;
	status = AGENT_TASK_CHANNEL_OK;

out:
	if (lease_held)
		open_file_io_token_end(&lease);
	if (context_lane_held)
		agent_lifecycle_context_lane_leave(p);
	if (status != AGENT_TASK_CHANNEL_OK) {
		memset(imported, 0, sizeof(*imported));
		return status;
	}
	imported->resource_type = AGENT_ARTIFACT_UTF8;
	imported->resource_flags = AGENT_TASK_HANDLE_F_OWNED;
	imported->producer_node_id = AGENT_EXECUTION_NODE_NONE;
	imported->producer_pid = p->pid;
	imported->producer_control_id = p->agent_control_id;
	imported->source_handle = control->source_handle;
	imported->length = length;
	imported->provenance_labels =
		provenance_labels | AGENT_PROVENANCE_UNTRUSTED_FILE_DATA;
	imported->producer_context_sequence = context_sequence;
	agent_sha256(imported->snapshot, length, imported->content_digest);
	return AGENT_TASK_CHANNEL_OK;
}

static void
agent_task_bridge_resource_release(struct proc *p, uint type, uint flags,
				   uint64 source_handle, uint64 length)
{
	(void)p;
	(void)source_handle;
	if (type != AGENT_ARTIFACT_UTF8 ||
	    flags != AGENT_TASK_HANDLE_F_OWNED || length == 0 ||
	    length > AGENT_TASK_RESOURCE_UTF8_MAX)
		panic("Task bridge resource release");
}

static const struct agent_task_channel_ops agent_task_bridge_ops = {
	.validate = agent_task_bridge_validate,
	.submit = agent_task_bridge_submit,
	.cancel = agent_task_bridge_cancel,
	.expire = agent_task_bridge_expire,
	.resource_import = agent_task_bridge_resource_import,
	.resource_release = agent_task_bridge_resource_release,
};

void
agent_task_bridge_init(void)
{
	agent_task_channel_init();
}

uint
agent_task_bridge_tick(uint64 now)
{
	return agent_task_channel_tick(now);
}

int
agent_task_bridge_current_deadline_due(void)
{
	return agent_task_channel_deadline_due(curr_proc());
}

int
agent_task_bridge_current_deadline_safe_point(void)
{
	struct proc *p = curr_proc();
	int expired = 0;

	while (agent_task_channel_deadline_due(p)) {
		int status = agent_task_channel_expire(
			p, agent_task_bridge_now(), &agent_task_bridge_ops);

		if (status < 0)
			return status;
		expired += status;
		if (status == 0 && agent_task_channel_deadline_due(p))
			return AGENT_TASK_CHANNEL_EVIDENCE;
	}
	return expired;
}

int
agent_task_bridge_reclaim(struct proc *p)
{
	return agent_task_channel_reclaim(p, &agent_task_bridge_ops);
}

int
agent_task_bridge_active(const struct proc *p)
{
	return agent_task_channel_active(p);
}

static void
agent_task_bridge_setup_placeholder(
	struct agent_task_channel_setup_result *result)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = AGENT_TASK_CHANNEL_BAD_REQUEST;
}

static void
agent_task_bridge_enter_placeholder(
	struct agent_task_channel_enter_result *result)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = AGENT_TASK_CHANNEL_BAD_REQUEST;
}

static void
agent_task_bridge_resource_placeholder(
	struct agent_task_channel_resource_result *result)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = AGENT_TASK_CHANNEL_BAD_REQUEST;
}

int
sys_agent_task_channel_setup(uint64 setupaddr, uint64 resultaddr)
{
	struct agent_task_channel_setup setup;
	struct agent_task_channel_setup_result result;
	struct proc *p = curr_proc();
	int was_active;
	int status;

	if (p == 0 || copyin(p->pagetable, (char *)&setup, setupaddr,
			     sizeof(setup)) < 0)
		return -1;
	agent_task_bridge_setup_placeholder(&result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	was_active = agent_task_channel_active(p);
	status = agent_task_channel_setup(
		p, curr_thread(), &setup, &result, &agent_task_bridge_ops);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0) {
		if (!was_active && status == AGENT_TASK_CHANNEL_OK)
			(void)agent_task_channel_reclaim(p, &agent_task_bridge_ops);
		return -1;
	}
	return 0;
}

int
sys_agent_task_channel_enter(uint64 enteraddr, uint64 resultaddr)
{
	struct agent_task_channel_enter enter;
	struct agent_task_channel_enter_result result;
	struct proc *p = curr_proc();

	if (p == 0 || copyin(p->pagetable, (char *)&enter, enteraddr,
			     sizeof(enter)) < 0)
		return -1;
	agent_task_bridge_enter_placeholder(&result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	(void)agent_task_channel_enter(
		p, curr_thread(), &enter, &result, &agent_task_bridge_ops);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	return 0;
}

int
sys_agent_task_channel_resource(uint64 controladdr, uint64 resultaddr,
				struct file *source_file, int source_fd)
{
	struct agent_task_channel_resource control;
	struct agent_task_channel_resource_result result;
	struct proc *p = curr_proc();

	if (p == 0 || copyin(p->pagetable, (char *)&control, controladdr,
			     sizeof(control)) < 0)
		return -1;
	agent_task_bridge_resource_placeholder(&result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	if (control.operation != AGENT_TASK_RESOURCE_IMPORT || source_fd < 0 ||
	    control.source_handle != (uint64)(uint)source_fd)
		source_file = 0;
	(void)agent_task_channel_resource(
		p, curr_thread(), source_file, &control, &result,
		&agent_task_bridge_ops);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0) {
		if (control.operation == AGENT_TASK_RESOURCE_IMPORT &&
		    result.status == AGENT_TASK_CHANNEL_OK) {
			int rollback = agent_task_channel_rollback_import(
				p, result.generation, result.handle,
				&agent_task_bridge_ops);

			if (rollback != AGENT_TASK_CHANNEL_OK)
				panic("Task bridge lost import rollback");
		}
		return -1;
	}
	return 0;
}
