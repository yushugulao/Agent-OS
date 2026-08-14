#ifndef AGENT_TASK_CHANNEL_H
#define AGENT_TASK_CHANNEL_H

#include "riscv.h"
#include "types.h"
#include "../include/agent_task_channel_abi.h"

struct proc;
struct thread;
struct file;

#define AGENT_TASK_CHANNEL_MAPPED_PAGES  2U
#define AGENT_TASK_CHANNEL_PRIVATE_PAGES 2U
#define AGENT_TASK_CHANNEL_STATE_PAGES \
	(AGENT_TASK_CHANNEL_MAPPED_PAGES + AGENT_TASK_CHANNEL_PRIVATE_PAGES)
#define AGENT_TASK_CHANNEL_RESOURCE_CAPACITY 8U
#define AGENT_TASK_RESOURCE_SNAPSHOT_SIZE \
	(AGENT_TASK_RESOURCE_UTF8_MAX + 1U)

/* Integration reserves these two pages by lowering USER_IMAGE_LIMIT. */
#define AGENT_TASK_CHANNEL_SQ_BASE (AGENT_CONTEXT_BASE - 2U * PAGE_SIZE)
#define AGENT_TASK_CHANNEL_CQ_BASE (AGENT_CONTEXT_BASE - PAGE_SIZE)

enum agent_task_request_state {
	AGENT_TASK_REQUEST_FREE = 0,
	AGENT_TASK_REQUEST_ACCEPTED,
	AGENT_TASK_REQUEST_RUNNING,
	AGENT_TASK_REQUEST_EVIDENCE_PENDING,
	AGENT_TASK_REQUEST_TERMINAL,
	AGENT_TASK_REQUEST_CQ_VISIBLE,
};

#define AGENT_TASK_COMPLETION_INTERNAL_F_CACHED (1U << 0)
#define AGENT_TASK_COMPLETION_INTERNAL_F_ALL \
	AGENT_TASK_COMPLETION_INTERNAL_F_CACHED

struct agent_task_completion {
	int status;
	uint decision_reason;
	uint flags;
	uint internal_flags;
	struct agent_task_resource_handle result;
	uint64 context_sequence;
	uint64 evidence_ticket;
	uint64 provenance_labels;
	uint64 completion_tick;
	/* Kernel-only execution decision time; providers cannot supply this via UAPI. */
	uint64 terminal_tick;
};

/* Successful imports transfer a copied kernel snapshot to the channel. */
struct agent_task_resource_import {
	uint resource_type;
	uint resource_flags;
	uint producer_node_id;
	int producer_pid;
	uint64 producer_control_id;
	uint64 source_handle;
	uint64 length;
	uint64 provenance_labels;
	uint64 producer_context_sequence;
	uchar content_digest[AGENT_TASK_CHANNEL_SCHEMA_SIZE];
	uchar snapshot[AGENT_TASK_RESOURCE_SNAPSHOT_SIZE];
};

/* source_handle is the import-time source id, never a raw kernel pointer. */
struct agent_task_resource_view {
	struct agent_task_resource_handle handle;
	uint64 source_handle;
	uint64 length;
	uint64 provenance_labels;
	uint64 producer_context_sequence;
	uint64 producer_control_id;
	uchar content_digest[AGENT_TASK_CHANNEL_SCHEMA_SIZE];
	uchar snapshot[AGENT_TASK_RESOURCE_SNAPSHOT_SIZE];
	uint producer_node_id;
	int producer_pid;
};

struct agent_task_validation {
	uint output_artifact_type;
	uint reserved;
	uint64 output_provenance_labels;
};

/* validate/submit/cancel return one of these deterministic hook outcomes. */
#define AGENT_TASK_HOOK_PENDING  0
#define AGENT_TASK_HOOK_COMPLETE 1
#define AGENT_TASK_HOOK_DENIED   2

struct agent_task_channel_ops {
	/*
	 * validate is a pure check over the copied descriptor and kernel-owned input
	 * view. It must return PENDING with frozen output type/labels and must not
	 * publish Context or Evidence; submit runs after the request is RUNNING.
	 */
	int (*validate)(struct proc *, const struct agent_task_sqe *,
			const struct agent_task_resource_view *,
			struct agent_task_validation *,
			struct agent_task_completion *);
	int (*submit)(struct proc *, const struct agent_task_sqe *,
		      const struct agent_task_resource_view *,
		      const struct agent_task_validation *,
		      struct agent_task_completion *);
	/*
	 * A nonzero request_id is a copied user CANCEL command. Reclaim uses an
	 * internal descriptor with request_id zero and target=link_request_id;
	 * the bridge must route that form through the execution contract's force
	 * cancellation path rather than applying the user's cancel policy.
	 */
	int (*cancel)(struct proc *, const struct agent_task_sqe *,
		      struct agent_task_completion *);
	/*
	 * COMPLETE cancellation is the sole CANCELLED terminal candidate. A
	 * PENDING or DENIED request remains provider-owned until its one eventual
	 * completion; reclaim deliberately retains pages and lifecycle gates.
	 */
	/* Expiry handles only work that submit left PENDING. */
	int (*expire)(struct proc *, const struct agent_task_sqe *,
		      struct agent_task_completion *);
	int (*resource_import)(struct proc *, struct file *,
			       const struct agent_task_channel_resource *,
			       struct agent_task_resource_import *);
	void (*resource_release)(struct proc *, uint, uint, uint64, uint64);
};

void agent_task_channel_init(void);
int agent_task_channel_setup(struct proc *, struct thread *,
			     const struct agent_task_channel_setup *,
			     struct agent_task_channel_setup_result *,
			     const struct agent_task_channel_ops *);
int agent_task_channel_enter(struct proc *, struct thread *,
			     const struct agent_task_channel_enter *,
			     struct agent_task_channel_enter_result *,
			     const struct agent_task_channel_ops *);
int agent_task_channel_complete(struct proc *, uint64, uint64, uint64,
				const struct agent_task_completion *,
				const struct agent_task_channel_ops *);
int agent_task_channel_expire(struct proc *, uint64,
			      const struct agent_task_channel_ops *);
int agent_task_channel_resource(
	struct proc *, struct thread *, struct file *,
	const struct agent_task_channel_resource *,
	struct agent_task_channel_resource_result *,
	const struct agent_task_channel_ops *);
/* Undo an IMPORT whose successful result could not be copied to user space. */
int agent_task_channel_rollback_import(
	struct proc *, uint64, struct agent_task_resource_handle,
	const struct agent_task_channel_ops *);
/*
 * IRQ-safe: marks newly due requests and interrupts the generation-matched
 * issuer's interruptible wait. A schedulable background/user-return safe point
 * must call expire(); timer context never invokes provider or evidence hooks.
 */
uint agent_task_channel_tick(uint64);
/* O(1) sticky per-channel checkpoint for the current task's safe-point drain. */
int agent_task_channel_deadline_due(const struct proc *);
/* Success: alias(new), rebind after commit, then unmap_exec(old). */
int agent_task_channel_alias_exec(struct proc *, pagetable_t);
/* Pre-commit failure only: remove the staged alias and release its token. */
void agent_task_channel_abort_exec_alias(struct proc *, pagetable_t);
/* Post-commit only: retire exact leaves from the old page table. */
void agent_task_channel_unmap_exec(struct proc *, pagetable_t);
int agent_task_channel_rebind_exec(struct proc *, struct thread *);
int agent_task_channel_reclaim(struct proc *,
			       const struct agent_task_channel_ops *);
int agent_task_channel_active(const struct proc *);
/* True only for the setup-bound live issuer thread. */
int agent_task_channel_current_issuer(struct proc *, struct thread *);
/* Teardown-only exact issuer check; does not require a live lifecycle. */
int agent_task_channel_current_issuer_cleanup(struct proc *, struct thread *);
/* IRQ-safe wake of the exact generation-bound channel issuer. */
int agent_task_channel_notify(struct proc *, uint64);
/* Interrupts-disabled validation for an exact delegated RUNNING request. */
int agent_task_channel_delegate_cancel_preflight_locked(
	struct proc *, uint64, uint64, uint64);
/*
 * Interrupts-disabled commit after a successful preflight in the same
 * critical section.  It also supersedes an earlier denied owner SQ CANCEL.
 */
int agent_task_channel_delegate_cancel_locked(
	struct proc *, uint64, uint64, uint64);

#endif
