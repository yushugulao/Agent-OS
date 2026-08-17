#ifndef AGENT_TASK_CHANNEL_ABI_H
#define AGENT_TASK_CHANNEL_ABI_H

#include "agent_execution_contract_abi.h"

/* Frozen syscall numbers. Dispatch and user wrappers live outside this ABI. */
#define AGENT_TASK_CHANNEL_SETUP_SYSCALL    563U
#define AGENT_TASK_CHANNEL_ENTER_SYSCALL    564U
#define AGENT_TASK_CHANNEL_RESOURCE_SYSCALL 565U
#define AGENT_TASK_DELEGATE_CLAIM_SYSCALL   567U
#define AGENT_TASK_DELEGATE_COMPLETE_SYSCALL 568U

#define AGENT_TASK_CHANNEL_VERSION       1U
#define AGENT_TASK_CHANNEL_ENTRY_VERSION 1U
#define AGENT_TASK_CHANNEL_CAPACITY      16U
#define AGENT_TASK_CHANNEL_SCHEMA_SIZE   32U

/* UTF-8 imports reserve one byte for a kernel-written trailing NUL. */
#define AGENT_TASK_RESOURCE_UTF8_MAX      63U
#define AGENT_TASK_RESOURCE_SNAPSHOT_SIZE 128U

#define AGENT_TASK_DELEGATE_VERSION 1U
#define AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION 2U

#define AGENT_TASK_DELEGATE_F_NONE 0U

#define AGENT_TASK_DELEGATE_CLAIM_F_WAIT (1U << 0)
#define AGENT_TASK_DELEGATE_CLAIM_F_ALL AGENT_TASK_DELEGATE_CLAIM_F_WAIT

#define AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL (1U << 0)
#define AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL (1U << 1)
#define AGENT_TASK_DELEGATE_COMPLETE_F_QUERY_TERMINAL (1U << 2)
#define AGENT_TASK_DELEGATE_COMPLETE_F_ALL \
	(AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL | \
	 AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL | \
	 AGENT_TASK_DELEGATE_COMPLETE_F_QUERY_TERMINAL)

#define AGENT_TASK_DELEGATE_STATE_NONE    0U
#define AGENT_TASK_DELEGATE_STATE_QUEUED  1U
#define AGENT_TASK_DELEGATE_STATE_CLAIMED 2U
#define AGENT_TASK_DELEGATE_STATE_READY   3U

/* DELEGATE_TASK terminal Context value0 packs the executor identity. */
#define AGENT_TASK_DELEGATE_EXECUTOR_PID_MASK 0xffffffffULL
#define AGENT_TASK_DELEGATE_EXECUTOR_AGENT_SHIFT 32U

#define AGENT_TASK_CHANNEL_SQ_MAGIC 0x4147545343513031ULL
#define AGENT_TASK_CHANNEL_CQ_MAGIC 0x4147544343513031ULL

#define AGENT_TASK_CHANNEL_RING_F_ACTIVE       (1U << 0)
#define AGENT_TASK_CHANNEL_RING_F_RESYNC       (1U << 1)
#define AGENT_TASK_CHANNEL_RING_F_CQ_FULL      (1U << 2)
#define AGENT_TASK_CHANNEL_RING_F_RECLAIMING   (1U << 3)
#define AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE (1U << 4)
#define AGENT_TASK_CHANNEL_RING_F_ALL \
	(AGENT_TASK_CHANNEL_RING_F_ACTIVE | \
	 AGENT_TASK_CHANNEL_RING_F_RESYNC | \
	 AGENT_TASK_CHANNEL_RING_F_CQ_FULL | \
	 AGENT_TASK_CHANNEL_RING_F_RECLAIMING | \
	 AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE)

#define AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER (1U << 0)

#define AGENT_TASK_CHANNEL_ENTER_F_RESYNC (1U << 0)
#define AGENT_TASK_CHANNEL_ENTER_F_DRAIN  (1U << 1)
#define AGENT_TASK_CHANNEL_ENTER_F_ALL \
	(AGENT_TASK_CHANNEL_ENTER_F_RESYNC | \
	 AGENT_TASK_CHANNEL_ENTER_F_DRAIN)

#define AGENT_TASK_CHANNEL_OP_SUBMIT 1U
#define AGENT_TASK_CHANNEL_OP_CANCEL 2U

#define AGENT_TASK_SQE_F_LINK          (1U << 0)
#define AGENT_TASK_SQE_F_CANCEL        (1U << 1)
#define AGENT_TASK_SQE_F_HARD_DEADLINE (1U << 2)
#define AGENT_TASK_SQE_F_ALL \
	(AGENT_TASK_SQE_F_LINK | AGENT_TASK_SQE_F_CANCEL | \
	 AGENT_TASK_SQE_F_HARD_DEADLINE)

#define AGENT_TASK_CQE_F_CANCELLED   (1U << 0)
#define AGENT_TASK_CQE_F_DEADLINE    (1U << 1)
#define AGENT_TASK_CQE_F_DENIED      (1U << 2)
#define AGENT_TASK_CQE_F_LINK_FAILED (1U << 3)
#define AGENT_TASK_CQE_F_ALL \
	(AGENT_TASK_CQE_F_CANCELLED | AGENT_TASK_CQE_F_DEADLINE | \
	 AGENT_TASK_CQE_F_DENIED | AGENT_TASK_CQE_F_LINK_FAILED)

#define AGENT_TASK_HANDLE_F_OWNED    (1U << 0)
#define AGENT_TASK_HANDLE_F_BORROWED (1U << 1)
#define AGENT_TASK_HANDLE_F_ALL \
	(AGENT_TASK_HANDLE_F_OWNED | AGENT_TASK_HANDLE_F_BORROWED)

#define AGENT_TASK_RESOURCE_IMPORT  1U
#define AGENT_TASK_RESOURCE_RELEASE 2U
#define AGENT_TASK_RESOURCE_QUERY   3U

#define AGENT_TASK_RESOURCE_STATE_NONE      0U
#define AGENT_TASK_RESOURCE_STATE_LIVE      1U
#define AGENT_TASK_RESOURCE_STATE_IN_FLIGHT 2U

/* Core protocol outcomes. Tool outcomes continue to use AGENT_STATUS_* values. */
#define AGENT_TASK_CHANNEL_OK               0
#define AGENT_TASK_CHANNEL_RETRY           -1
#define AGENT_TASK_CHANNEL_BAD_REQUEST     -2
#define AGENT_TASK_CHANNEL_STALE           -3
#define AGENT_TASK_CHANNEL_RESYNC_REQUIRED -4
#define AGENT_TASK_CHANNEL_NO_SPACE        -5
#define AGENT_TASK_CHANNEL_DENIED          -6
#define AGENT_TASK_CHANNEL_EVIDENCE        -7

/* Slot zero is the typed null handle. Generation prevents slot ABA. */
struct agent_task_resource_handle {
	unsigned int slot;
	unsigned short type;
	unsigned short flags;
	unsigned long long generation;
};

/*
 * The user may write only the SQ page. Kernel-owned counters in that page are
 * advisory; the kernel keeps authoritative copies in its private page.
 */
struct agent_task_ring_header {
	unsigned long long magic;
	unsigned int version;
	unsigned int struct_size;
	unsigned int entry_size;
	unsigned int capacity;
	unsigned long long generation;
	unsigned long long head;
	unsigned long long tail;
	unsigned int flags;
	unsigned int reserved;
	unsigned long long submitted;
	unsigned long long completed;
	unsigned long long backpressure;
	unsigned long long protocol_faults;
	unsigned long long resync_count;
	unsigned long long last_accepted_request_id;
	unsigned long long reserved_tail[3];
};

/*
 * A producer writes the descriptor, then publishes a monotonic sq_tail through
 * enter(). slot_generation is 1 + floor(sq_position / capacity). request_id is
 * strictly increasing for every SQ command in a channel lifetime. CANCEL is a
 * target-only command: it has its own request_id, names the target request in
 * link_request_id, and never creates a second CQE; the target has exactly one
 * terminal CQE when the task reaches a deliverable terminal state. Consumption
 * is unambiguous from sq_head and
 * last_accepted_request_id. A synchronous policy denial returns DENIED from
 * enter() with that cancel id already accepted and leaves the target running.
 * deadline_tick is nonzero exactly when HARD_DEADLINE is set; only SUBMIT
 * accepts that flag. The deadline is part of the execution replay identity.
 * A gateway retains the target result and, after a pending cancel, waits for
 * that target's single CANCELLED or hard-deadline CQE. Cancelling a retained
 * terminal is an idempotent consume; an already-acknowledged target is stale
 * rather than authenticated by a scalar watermark. The kernel copies all 128
 * bytes before validating any field.
 */
struct agent_task_sqe {
	unsigned short version;
	unsigned short size;
	unsigned short opcode;
	unsigned short flags;
	unsigned long long request_id;
	unsigned long long ring_generation;
	unsigned long long slot_generation;
	struct agent_execution_contract_key contract;
	unsigned int node_id;
	unsigned short attempt_id;
	unsigned short tool_id;
	unsigned long long deadline_tick;
	unsigned long long link_request_id;
	struct agent_task_resource_handle input;
	unsigned char schema_digest[AGENT_TASK_CHANNEL_SCHEMA_SIZE];
};

/* CQ is read-only in user space and is advanced only by a validated CQ ack. */
struct agent_task_cqe {
	unsigned short version;
	unsigned short size;
	unsigned int flags;
	int status;
	unsigned int decision_reason;
	unsigned long long request_id;
	unsigned long long ring_generation;
	unsigned long long slot_generation;
	struct agent_execution_contract_key contract;
	unsigned int node_id;
	unsigned short attempt_id;
	unsigned short tool_id;
	struct agent_task_resource_handle result;
	unsigned long long context_sequence;
	unsigned long long evidence_ticket;
	unsigned long long provenance_labels;
	unsigned long long completion_tick;
	unsigned long long reserved;
};

struct agent_task_channel_setup {
	unsigned int version;
	unsigned int size;
	unsigned int flags;
	unsigned int reserved;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long reserved_tail[4];
};

struct agent_task_channel_setup_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int flags;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long generation;
	unsigned long long sq_base;
	unsigned long long cq_base;
	unsigned int sq_capacity;
	unsigned int cq_capacity;
	unsigned int sqe_size;
	unsigned int cqe_size;
	unsigned int mapped_page_count;
	unsigned int private_page_count;
	unsigned long long reserved_tail[2];
};

struct agent_task_channel_enter {
	unsigned int version;
	unsigned int size;
	unsigned int flags;
	unsigned int max_submit;
	unsigned long long generation;
	unsigned long long sq_tail;
	unsigned long long cq_head;
	unsigned int min_complete;
	unsigned int reserved;
	unsigned long long reserved_tail[2];
};

struct agent_task_channel_enter_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int flags;
	unsigned long long generation;
	unsigned long long sq_head;
	unsigned long long cq_head;
	unsigned long long cq_tail;
	unsigned int submitted;
	unsigned int completed;
	unsigned int in_flight;
	unsigned int terminal_pending;
	unsigned int resource_count;
	unsigned int reserved;
	unsigned long long protocol_faults;
	unsigned long long resync_count;
	unsigned long long backpressure;
	unsigned long long last_accepted_request_id;
};

struct agent_task_channel_resource {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	struct agent_task_resource_handle handle;
	unsigned int resource_type;
	unsigned int resource_flags;
	/* IMPORT interprets source_handle as a file descriptor. */
	unsigned long long source_handle;
	/* Exact file length; UTF-8 imports accept 1..63 bytes. */
	unsigned long long length;
	unsigned long long channel_generation;
	unsigned long long reserved_tail;
};

struct agent_task_channel_resource_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int state;
	struct agent_task_resource_handle handle;
	unsigned long long source_handle;
	unsigned long long length;
	unsigned long long generation;
	unsigned int references;
	unsigned int reserved;
	unsigned long long reserved_tail[2];
};

/*
 * The descriptor carries immutable routing, authority, workspace, budget, and
 * Artifact bindings. Large bodies remain in sealed application Artifacts.
 */
struct agent_task_delegate_descriptor {
	unsigned short version;
	unsigned short size;
	int target_pid;
	unsigned int target_agent_id;
	unsigned int task_type;
	unsigned long long target_control_id;
	unsigned long long task_id;
	unsigned long long correlation_id;
	unsigned long long parent_task_id;
	/* The objective Artifact contains the sealed task description. */
	unsigned int capsule_handle;
	unsigned int input_artifact_handle;
	unsigned int result_artifact_handle;
	unsigned int expected_result_type;
	unsigned long long required_capabilities;
	unsigned long long allowed_tools;
	unsigned char workspace_revision_sha256[32];
	unsigned int resource_budget;
	unsigned int read_budget;
	unsigned long long deadline_tick;
};

struct agent_task_delegate_claim {
	unsigned int version;
	unsigned int size;
	unsigned int flags;
	unsigned int reserved;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long reserved_tail[4];
};

struct agent_task_delegate_claim_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int state;
	struct agent_workflow_lifecycle_key lifecycle;
	struct agent_task_delegate_descriptor descriptor;
	int owner_pid;
	unsigned int owner_agent_id;
	unsigned long long owner_control_id;
	unsigned long long channel_generation;
	unsigned long long request_id;
	unsigned long long slot_generation;
};

struct agent_task_delegate_complete {
	unsigned int version;
	unsigned int size;
	unsigned int flags;
	unsigned int reserved;
	struct agent_workflow_lifecycle_key lifecycle;
	int owner_pid;
	int terminal_status;
	unsigned long long owner_control_id;
	unsigned long long channel_generation;
	unsigned long long request_id;
	unsigned long long slot_generation;
	unsigned long long task_id;
	unsigned long long correlation_id;
	/* ACK echoes the exact kernel offer while terminal_status stays unchanged. */
	int ack_terminal_status;
	unsigned int terminal_generation;
};

struct agent_task_delegate_complete_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int state;
	unsigned long long channel_generation;
	unsigned long long request_id;
	unsigned long long slot_generation;
	unsigned long long task_id;
	unsigned long long correlation_id;
	/* Kernel-selected offer; both fields must be echoed by a cleanup ACK. */
	int terminal_status;
	unsigned int terminal_generation;
};

/*
 * A normal provider completion uses flags=0 and zero ACK fields. If it returns
 * RETRY/CLAIMED, terminal_status + terminal_generation are a kernel offer:
 * invalidate any prebound output, then repeat the same business status with
 * ACK_TERMINAL and echo both fields. A newer RETRY offer supersedes the old
 * one and must be ACKed without rerunning the task. Only OK/READY is terminal.
 *
 * A same-lifecycle controller may instead submit REQUEST_CANCEL with
 * terminal_status=CANCELLED and zero ACK fields. The exact owner/channel/
 * request/slot/task/correlation tuple is the cancellation capability binding;
 * the kernel additionally requires ORCHESTRATE, WAIT_CANCEL, and a live TASK
 * route to the owner. An OK result acknowledges the control request, not the
 * delegated task's terminal CQE. The provider still observes and ACKs the
 * kernel terminal offer at its effect boundary.
 */

_Static_assert(sizeof(unsigned short) == 2,
	       "Task Channel ABI requires 16-bit unsigned short");
_Static_assert(AGENT_TOOL_COUNT <= 0xffffU,
	       "Task Channel tool ids must fit without narrowing");
_Static_assert(sizeof(struct agent_task_resource_handle) == 16,
	       "Task Channel handle ABI layout");
_Static_assert(sizeof(struct agent_task_ring_header) == 128,
	       "Task Channel ring header ABI layout");
_Static_assert(sizeof(struct agent_task_sqe) == 128,
	       "Task Channel SQE ABI layout");
_Static_assert(__builtin_offsetof(struct agent_task_sqe, schema_digest) == 96,
	       "Task Channel schema digest ABI offset");
_Static_assert(sizeof(struct agent_task_cqe) == 128,
	       "Task Channel CQE ABI layout");
_Static_assert(__builtin_offsetof(struct agent_task_cqe, result) == 72,
	       "Task Channel result handle ABI offset");
_Static_assert(sizeof(struct agent_task_channel_setup) == 64,
	       "Task Channel setup ABI layout");
_Static_assert(sizeof(struct agent_task_channel_setup_result) == 96,
	       "Task Channel setup result ABI layout");
_Static_assert(sizeof(struct agent_task_channel_enter) == 64,
	       "Task Channel enter ABI layout");
_Static_assert(sizeof(struct agent_task_channel_enter_result) == 104,
	       "Task Channel enter result ABI layout");
_Static_assert(sizeof(struct agent_task_channel_resource) == 72,
	       "Task Channel resource ABI layout");
_Static_assert(sizeof(struct agent_task_channel_resource_result) == 80,
	       "Task Channel resource result ABI layout");
_Static_assert(sizeof(struct agent_task_delegate_descriptor) == 128,
	       "delegated task descriptor ABI layout");
_Static_assert(__builtin_offsetof(struct agent_task_delegate_descriptor,
				 target_control_id) == 16,
	       "delegated task target binding ABI offset");
_Static_assert(sizeof(struct agent_task_delegate_claim) == 64,
	       "delegated task claim ABI layout");
_Static_assert(sizeof(struct agent_task_delegate_claim_result) == 200,
	       "delegated task claim result ABI layout");
_Static_assert(sizeof(struct agent_task_delegate_complete) == 96,
	       "delegated task completion ABI layout");
_Static_assert(__builtin_offsetof(struct agent_task_delegate_complete,
				 ack_terminal_status) == 88,
	       "delegated task completion ACK ABI offset");
_Static_assert(sizeof(struct agent_task_delegate_complete_result) == 64,
	       "delegated task completion result ABI layout");
_Static_assert(__builtin_offsetof(struct agent_task_delegate_complete_result,
				 terminal_generation) == 60,
	       "delegated task terminal offer ABI offset");

#endif
