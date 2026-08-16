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

_Static_assert(AGENT_TASK_RESOURCE_SNAPSHOT_SIZE >= AGENT_OP_PAYLOAD_SIZE,
	       "Task resource snapshot must cover one tool payload");

#define AGENT_TASK_DELEGATE_CAPACITY NPROC
#define AGENT_TASK_DELEGATE_SLOT_FREE       0U
#define AGENT_TASK_DELEGATE_SLOT_PREPARING  1U
#define AGENT_TASK_DELEGATE_SLOT_QUEUED     2U
#define AGENT_TASK_DELEGATE_SLOT_CLAIMED    3U
#define AGENT_TASK_DELEGATE_SLOT_READY      4U
#define AGENT_TASK_DELEGATE_SLOT_FINALIZING 5U
#define AGENT_TASK_DELEGATE_SLOT_CLAIMING   6U

struct agent_task_delegate_slot {
	uint state;
	uint64 enqueue_sequence;
	struct workflow_lifecycle_key lifecycle;
	struct agent_task_sqe sqe;
	struct agent_task_delegate_descriptor descriptor;
	struct agent_op op;
	struct agent_execution_claim claim;
	struct agent_provenance_decision provenance;
	struct resource_phase_lease phase_lease;
	int owner_pid;
	int owner_agent_id;
	uint64 owner_control_id;
	int worker_pid;
	int worker_agent_id;
	uint64 worker_control_id;
	uint64 worker_thread_generation;
	uint effect_refs;
	int helper_tid;
	uint64 helper_thread_generation;
	uint worker_completion_recorded;
	uint completion_ack_pending;
	uint submitted_flags;
	int submitted_status;
	int terminal_status;
	uint terminal_pending;
	uint terminal_generation;
	uint64 terminal_tick;
};

struct agent_task_delegate_receipt {
	uint valid;
	uint64 sequence;
	struct workflow_lifecycle_key lifecycle;
	int worker_pid;
	int worker_agent_id;
	uint64 worker_control_id;
	uint64 worker_thread_generation;
	int owner_pid;
	int owner_agent_id;
	uint64 owner_control_id;
	uint64 channel_generation;
	uint64 request_id;
	uint64 slot_generation;
	uint64 task_id;
	uint64 correlation_id;
	uint submitted_flags;
	int submitted_status;
	int terminal_status;
	uint terminal_generation;
};

struct agent_task_delegate_cancel_receipt {
	uint valid;
	uint64 sequence;
	struct workflow_lifecycle_key lifecycle;
	int requester_pid;
	int requester_agent_id;
	uint64 requester_control_id;
	int owner_pid;
	int owner_agent_id;
	uint64 owner_control_id;
	uint64 channel_generation;
	uint64 request_id;
	uint64 slot_generation;
	uint64 task_id;
	uint64 correlation_id;
	uint state;
	int terminal_status;
	uint terminal_generation;
};

static struct agent_task_delegate_slot
	agent_task_delegate_slots[AGENT_TASK_DELEGATE_CAPACITY];
static struct wait_queue agent_task_delegate_waiters[NPROC];
static uint64 agent_task_delegate_next_sequence;
static struct agent_task_delegate_receipt
	agent_task_delegate_receipts[AGENT_TASK_DELEGATE_CAPACITY];
static uint64 agent_task_delegate_next_receipt;
static struct agent_task_delegate_cancel_receipt
	agent_task_delegate_cancel_receipts[AGENT_TASK_DELEGATE_CAPACITY];
static uint64 agent_task_delegate_next_cancel_receipt;
extern struct proc pool[NPROC];

static uint agent_task_bridge_admission_policy(
	const struct agent_task_sqe *);
static uint64 agent_task_bridge_now(void);
static int agent_task_bridge_delegate_pump(struct proc *);

static void
agent_task_delegate_slot_reset_locked(struct agent_task_delegate_slot *slot)
{
	if (slot == 0)
		return;
	if (slot->effect_refs != 0)
		panic("delegated Task effect reference");
	memset(slot, 0, sizeof(*slot));
}

static struct proc *
agent_task_delegate_find_proc_locked(int pid, int agent_id,
				     uint64 control_id)
{
	for (struct proc *candidate = pool; candidate < &pool[NPROC];
	     candidate++)
		if (proc_teardown_live(candidate) && candidate->is_agent &&
		    candidate->pid == pid && candidate->agent_id == agent_id &&
		    candidate->agent_control_id == control_id)
			return candidate;
	return 0;
}

static int
agent_task_delegate_lifecycle_matches(
	const struct proc *p, struct workflow_lifecycle_key lifecycle)
{
	return p != 0 && p->workflow_lifecycle_charged &&
	       p->workflow_lifecycle_id == lifecycle.id &&
	       p->workflow_lifecycle_generation == lifecycle.generation;
}

static int
agent_task_delegate_would_cycle_locked(
	const struct proc *owner, const struct proc *target,
	struct workflow_lifecycle_key lifecycle,
	const struct agent_task_delegate_slot *ignore)
{
	uint64 reachable[(NPROC + 63U) / 64U];
	uint owner_index;
	uint target_index;

	if (intr_get())
		panic("delegated Task graph unlocked");
	if (owner == 0 || target == 0 || owner < pool || owner >= &pool[NPROC] ||
	    target < pool || target >= &pool[NPROC])
		return 1;
	memset(reachable, 0, sizeof(reachable));
	owner_index = (uint)(owner - pool);
	target_index = (uint)(target - pool);
	reachable[target_index / 64U] |= 1ULL << (target_index % 64U);
	for (uint round = 0; round < NPROC; round++) {
		int changed = 0;

		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			const struct agent_task_delegate_slot *slot =
				&agent_task_delegate_slots[i];
			struct proc *edge_owner;
			struct proc *edge_target;
			uint edge_owner_index;
			uint edge_target_index;

			if (slot == ignore ||
			    slot->state == AGENT_TASK_DELEGATE_SLOT_FREE ||
			    !workflow_lifecycle_key_equal(slot->lifecycle, lifecycle))
				continue;
			edge_owner = agent_task_delegate_find_proc_locked(
				slot->owner_pid, slot->owner_agent_id,
				slot->owner_control_id);
			edge_target = agent_task_delegate_find_proc_locked(
				slot->descriptor.target_pid,
				(int)slot->descriptor.target_agent_id,
				slot->descriptor.target_control_id);
			if (edge_owner == 0 || edge_target == 0)
				continue;
			edge_owner_index = (uint)(edge_owner - pool);
			edge_target_index = (uint)(edge_target - pool);
			if ((reachable[edge_owner_index / 64U] &
			     (1ULL << (edge_owner_index % 64U))) == 0)
				continue;
			if (edge_target_index == owner_index)
				return 1;
			if ((reachable[edge_target_index / 64U] &
			     (1ULL << (edge_target_index % 64U))) == 0) {
				reachable[edge_target_index / 64U] |=
					1ULL << (edge_target_index % 64U);
				changed = 1;
			}
		}
		if (!changed)
			break;
	}
	return (reachable[owner_index / 64U] &
		(1ULL << (owner_index % 64U))) != 0;
}

static struct proc *
agent_task_delegate_target_locked(
	struct proc *owner, const struct agent_task_delegate_descriptor *descriptor,
	struct workflow_lifecycle_key lifecycle,
	const struct agent_task_delegate_slot *ignore)
{
	struct proc *target;

	if (owner == 0 || descriptor == 0 || !proc_teardown_live(owner) ||
	    !owner->is_agent || owner->agent_control_id == 0 ||
	    !agent_task_delegate_lifecycle_matches(owner, lifecycle) ||
	    !agent_identity_has_cap(owner, AGENT_CAP_ORCHESTRATE))
		return 0;
	target = agent_task_delegate_find_proc_locked(
		descriptor->target_pid, (int)descriptor->target_agent_id,
		descriptor->target_control_id);
	if (target == 0 ||
	    target == owner ||
	    !agent_task_delegate_lifecycle_matches(target, lifecycle) ||
	    !agent_identity_has_cap(target, AGENT_CAP_TASK_ACCEPT) ||
	    (descriptor->required_capabilities & owner->agent_capability_mask) !=
		    descriptor->required_capabilities ||
	    (descriptor->required_capabilities & target->agent_capability_mask) !=
		    descriptor->required_capabilities ||
	    (descriptor->allowed_tools & owner->agent_tool_grant_mask) !=
		    descriptor->allowed_tools ||
	    (descriptor->allowed_tools & target->agent_tool_grant_mask) !=
		    descriptor->allowed_tools ||
	    descriptor->resource_budget == 0 ||
	    descriptor->resource_budget > target->agent_sched_budget ||
	    descriptor->read_budget == 0 ||
	    descriptor->read_budget > target->agent_artifact_read_limit ||
	    agent_task_delegate_would_cycle_locked(
		    owner, target, lifecycle, ignore) ||
	    !agent_ipc_task_route_allows_locked(owner, target))
		return 0;
	return target;
}

static struct agent_task_delegate_slot *
agent_task_delegate_alloc_locked(void)
{
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++)
		if (agent_task_delegate_slots[i].state ==
		    AGENT_TASK_DELEGATE_SLOT_FREE)
			return &agent_task_delegate_slots[i];
	return 0;
}

int
agent_task_bridge_effect_pin_locked(
	struct proc *worker, struct thread *thread, uint64 side_effect_mask,
	int *slot_out, uint64 *generation_out)
{
	if (intr_get())
		panic("delegated Task effect pin unlocked");
	if (slot_out != 0)
		*slot_out = -1;
	if (generation_out != 0)
		*generation_out = 0;
	if (worker == 0 || thread == 0 || slot_out == 0 ||
	    generation_out == 0 || thread->process != worker ||
	    thread->identity_generation == 0 ||
	    (side_effect_mask & ~AGENT_SIDE_EFFECT_ALL) != 0)
		return 0;
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *slot =
			&agent_task_delegate_slots[i];
		int exact_main;
		int exact_helper;
		int exact_owner;

		if (slot->state != AGENT_TASK_DELEGATE_SLOT_CLAIMED)
			continue;
		/* A Task owner may keep orchestrating while a child holds the
		 * delegated effect lease. The same node manifest still limits every
		 * owner-side control effect, and the lease disappears with the claim. */
		exact_owner =
			thread == &worker->threads[0] &&
			slot->owner_pid == worker->pid &&
			slot->owner_agent_id == worker->agent_id &&
			slot->owner_control_id == worker->agent_control_id &&
			workflow_lifecycle_key_equal(
				slot->lifecycle, vfs_proc_lifecycle(worker));
		if (!exact_owner) {
			if (slot->worker_pid != worker->pid ||
			    slot->worker_agent_id != worker->agent_id ||
			    slot->worker_control_id != worker->agent_control_id ||
			    !workflow_lifecycle_key_equal(
				    slot->lifecycle, vfs_proc_lifecycle(worker)))
				continue;
			exact_main = thread == &worker->threads[0] &&
				     thread->identity_generation ==
					     slot->worker_thread_generation;
			exact_helper = thread->tid > 0 &&
				       thread->tid == slot->helper_tid &&
				       thread->identity_generation ==
					       slot->helper_thread_generation;
			if (!exact_main && !exact_helper)
				continue;
		}
		if ((side_effect_mask & ~slot->claim.manifest.side_effect_mask) != 0 ||
		    slot->effect_refs == (uint)-1 || slot->enqueue_sequence == 0)
			return 0;
		slot->effect_refs++;
		*slot_out = (int)i;
		*generation_out = slot->enqueue_sequence;
		return 1;
	}
	return 0;
}

void
agent_task_bridge_effect_unpin_locked(int slot_index, uint64 generation)
{
	struct agent_task_delegate_slot *slot;

	if (intr_get())
		panic("delegated Task effect unpin unlocked");
	if (slot_index < 0 || slot_index >= AGENT_TASK_DELEGATE_CAPACITY ||
	    generation == 0)
		panic("delegated Task effect token");
	slot = &agent_task_delegate_slots[slot_index];
	if (slot->state != AGENT_TASK_DELEGATE_SLOT_CLAIMED ||
	    slot->enqueue_sequence != generation || slot->effect_refs == 0)
		panic("delegated Task effect owner");
	slot->effect_refs--;
}

void
agent_task_bridge_thread_runtime_transition(struct thread *thread,
					    int transition)
{
	struct proc *worker;
	struct thread *parent;
	int enabled;

	if (thread == 0 || (worker = thread->process) == 0)
		return;
	enabled = intr_save();
	if (transition == AGENT_THREAD_RUNTIME_ACTIVATE) {
		parent = curr_thread();
		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *slot =
				&agent_task_delegate_slots[i];

			if (slot->state != AGENT_TASK_DELEGATE_SLOT_CLAIMED ||
			    parent != &worker->threads[0] || thread->tid <= 0 ||
			    slot->worker_pid != worker->pid ||
			    slot->worker_agent_id != worker->agent_id ||
			    slot->worker_control_id != worker->agent_control_id ||
			    slot->worker_thread_generation !=
				    parent->identity_generation ||
			    (slot->claim.manifest.side_effect_mask &
			     AGENT_SIDE_EFFECT_PROCESS) == 0)
				continue;
			if (slot->helper_thread_generation == 0) {
				slot->helper_tid = thread->tid;
				slot->helper_thread_generation =
					thread->identity_generation;
			}
			break;
		}
	} else if (transition == AGENT_THREAD_RUNTIME_RELEASE) {
		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *slot =
				&agent_task_delegate_slots[i];

			if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED &&
			    slot->worker_pid == worker->pid &&
			    slot->worker_agent_id == worker->agent_id &&
			    slot->worker_control_id == worker->agent_control_id &&
			    slot->helper_tid == thread->tid &&
			    slot->helper_thread_generation ==
				    thread->identity_generation) {
				slot->helper_tid = 0;
				slot->helper_thread_generation = 0;
				break;
			}
		}
	}
	intr_restore(enabled);
}

static void
agent_task_delegate_wake_target_locked(int target_pid, int target_agent_id,
				       uint64 target_control_id)
{
	struct proc *target = agent_task_delegate_find_proc_locked(
		target_pid, target_agent_id, target_control_id);

	if (target != 0)
		(void)wait_queue_interrupt(&target->threads[0]);
	if (target != 0)
		(void)wait_queue_wake_one(
			&agent_task_delegate_waiters[target - pool]);
}

static void
agent_task_delegate_wake_owner_locked(const struct agent_task_delegate_slot *slot)
{
	struct proc *owner;

	if (slot == 0)
		return;
	owner = agent_task_delegate_find_proc_locked(
		slot->owner_pid, slot->owner_agent_id, slot->owner_control_id);
	if (owner == 0)
		return;
	(void)agent_task_channel_notify(owner, slot->sqe.ring_generation);
}

static int
agent_task_delegate_worker_status_valid(int status)
{
	return status == AGENT_STATUS_OK ||
	       status == AGENT_STATUS_BAD_REQUEST ||
	       status == AGENT_STATUS_UNKNOWN_TOOL ||
	       status == AGENT_STATUS_BAD_PARAM ||
	       status == AGENT_STATUS_NOT_FOUND ||
	       status == AGENT_STATUS_NO_SPACE ||
	       status == AGENT_STATUS_DUPLICATE ||
	       status == AGENT_STATUS_CONFLICT ||
	       status == AGENT_STATUS_BAD_VERSION ||
	       status == AGENT_STATUS_BAD_SIZE ||
	       status == AGENT_STATUS_BAD_TYPE ||
	       status == AGENT_STATUS_UNKNOWN_PARAM ||
	       status == AGENT_STATUS_IO_ERROR ||
	       status == AGENT_STATUS_DURABILITY ||
	       status == AGENT_STATUS_INDETERMINATE;
}

static int
agent_task_delegate_ack_status_valid(int status)
{
	return status == AGENT_STATUS_CANCELLED ||
	       status == AGENT_STATUS_TIMEOUT ||
	       status == AGENT_STATUS_DENIED ||
	       status == AGENT_STATUS_STALE ||
	       status == AGENT_STATUS_INDETERMINATE;
}

static void
agent_task_delegate_terminal_intent_locked(
	struct agent_task_delegate_slot *slot, int status)
{
	if (slot == 0)
		return;
	/* First decision wins; an already-due hard deadline is authoritative. */
	if (!slot->terminal_pending ||
	    (status == AGENT_STATUS_TIMEOUT &&
	     slot->terminal_status != AGENT_STATUS_TIMEOUT)) {
		if (++slot->terminal_generation == 0)
			panic("delegated Task terminal generation");
		slot->terminal_status = status;
		slot->terminal_tick = agent_task_bridge_now();
		slot->terminal_pending = 1;
	}
}

static int
agent_task_delegate_kernel_terminal_locked(
	const struct agent_task_delegate_slot *slot, int fallback, uint64 now)
{
	if (slot != 0 &&
	    (((slot->sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
	      now >= slot->sqe.deadline_tick) ||
	     (slot->terminal_pending &&
	      slot->terminal_status == AGENT_STATUS_TIMEOUT)))
		return AGENT_STATUS_TIMEOUT;
	return fallback;
}

static int
agent_task_delegate_receipt_binding_matches(
	const struct agent_task_delegate_receipt *receipt,
	const struct proc *worker, const struct thread *thread,
	const struct agent_task_delegate_complete *complete,
	struct workflow_lifecycle_key lifecycle)
{
	return receipt != 0 && receipt->valid && worker != 0 && thread != 0 &&
	       complete != 0 &&
	       workflow_lifecycle_key_equal(receipt->lifecycle, lifecycle) &&
	       receipt->worker_pid == worker->pid &&
	       receipt->worker_agent_id == worker->agent_id &&
	       receipt->worker_control_id == worker->agent_control_id &&
	       receipt->worker_thread_generation == thread->identity_generation &&
	       receipt->owner_pid == complete->owner_pid &&
	       receipt->owner_control_id == complete->owner_control_id &&
	       receipt->channel_generation == complete->channel_generation &&
	       receipt->request_id == complete->request_id &&
	       receipt->slot_generation == complete->slot_generation &&
	       receipt->task_id == complete->task_id &&
	       receipt->correlation_id == complete->correlation_id;
}

static void
agent_task_delegate_receipt_record_locked(
	const struct agent_task_delegate_slot *slot)
{
	struct agent_task_delegate_receipt *receipt = 0;
	uint64 oldest = ~0ULL;

	if (slot == 0 || !slot->worker_completion_recorded ||
	    slot->worker_pid <= 0 ||
	    slot->worker_control_id == 0 ||
	    slot->worker_thread_generation == 0)
		return;
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		if (!agent_task_delegate_receipts[i].valid) {
			receipt = &agent_task_delegate_receipts[i];
			break;
		}
		if (agent_task_delegate_receipts[i].sequence < oldest) {
			oldest = agent_task_delegate_receipts[i].sequence;
			receipt = &agent_task_delegate_receipts[i];
		}
	}
	if (receipt == 0)
		panic("delegated Task receipt slot");
	memset(receipt, 0, sizeof(*receipt));
	if (++agent_task_delegate_next_receipt == 0)
		panic("delegated Task receipt sequence");
	receipt->valid = 1;
	receipt->sequence = agent_task_delegate_next_receipt;
	receipt->lifecycle = slot->lifecycle;
	receipt->worker_pid = slot->worker_pid;
	receipt->worker_agent_id = slot->worker_agent_id;
	receipt->worker_control_id = slot->worker_control_id;
	receipt->worker_thread_generation = slot->worker_thread_generation;
	receipt->owner_pid = slot->owner_pid;
	receipt->owner_agent_id = slot->owner_agent_id;
	receipt->owner_control_id = slot->owner_control_id;
	receipt->channel_generation = slot->sqe.ring_generation;
	receipt->request_id = slot->sqe.request_id;
	receipt->slot_generation = slot->sqe.slot_generation;
	receipt->task_id = slot->descriptor.task_id;
	receipt->correlation_id = slot->descriptor.correlation_id;
	receipt->submitted_flags = slot->submitted_flags;
	receipt->submitted_status = slot->submitted_status;
	receipt->terminal_status = slot->terminal_status;
	receipt->terminal_generation = slot->terminal_generation;
}

static int
agent_task_delegate_cancel_receipt_binding_matches(
	const struct agent_task_delegate_cancel_receipt *receipt,
	const struct proc *requester,
	const struct agent_task_delegate_complete *complete,
	struct workflow_lifecycle_key lifecycle)
{
	return receipt != 0 && receipt->valid && requester != 0 &&
	       complete != 0 &&
	       workflow_lifecycle_key_equal(receipt->lifecycle, lifecycle) &&
	       receipt->requester_pid == requester->pid &&
	       receipt->requester_agent_id == requester->agent_id &&
	       receipt->requester_control_id == requester->agent_control_id &&
	       receipt->owner_pid == complete->owner_pid &&
	       receipt->owner_control_id == complete->owner_control_id &&
	       receipt->channel_generation == complete->channel_generation &&
	       receipt->request_id == complete->request_id &&
	       receipt->slot_generation == complete->slot_generation &&
	       receipt->task_id == complete->task_id &&
	       receipt->correlation_id == complete->correlation_id;
}

static void
agent_task_delegate_cancel_receipt_record_locked(
	const struct agent_task_delegate_slot *slot, const struct proc *requester,
	uint state)
{
	struct agent_task_delegate_cancel_receipt *receipt = 0;
	uint64 oldest = ~0ULL;

	if (slot == 0 || requester == 0 || requester->pid <= 0 ||
	    requester->agent_id <= 0 || requester->agent_control_id == 0)
		panic("delegated Task cancel receipt binding");
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		if (!agent_task_delegate_cancel_receipts[i].valid) {
			receipt = &agent_task_delegate_cancel_receipts[i];
			break;
		}
		if (agent_task_delegate_cancel_receipts[i].sequence < oldest) {
			oldest = agent_task_delegate_cancel_receipts[i].sequence;
			receipt = &agent_task_delegate_cancel_receipts[i];
		}
	}
	if (receipt == 0)
		panic("delegated Task cancel receipt slot");
	memset(receipt, 0, sizeof(*receipt));
	if (++agent_task_delegate_next_cancel_receipt == 0)
		panic("delegated Task cancel receipt sequence");
	receipt->valid = 1;
	receipt->sequence = agent_task_delegate_next_cancel_receipt;
	receipt->lifecycle = slot->lifecycle;
	receipt->requester_pid = requester->pid;
	receipt->requester_agent_id = requester->agent_id;
	receipt->requester_control_id = requester->agent_control_id;
	receipt->owner_pid = slot->owner_pid;
	receipt->owner_agent_id = slot->owner_agent_id;
	receipt->owner_control_id = slot->owner_control_id;
	receipt->channel_generation = slot->sqe.ring_generation;
	receipt->request_id = slot->sqe.request_id;
	receipt->slot_generation = slot->sqe.slot_generation;
	receipt->task_id = slot->descriptor.task_id;
	receipt->correlation_id = slot->descriptor.correlation_id;
	receipt->state = state;
	receipt->terminal_status = slot->terminal_status;
	receipt->terminal_generation = slot->terminal_generation;
}

static void
agent_task_delegate_claim_result_fill(
	const struct agent_task_delegate_slot *slot,
	struct agent_task_delegate_claim_result *result, int status, uint state)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_DELEGATE_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	result->state = state;
	if (slot == 0)
		return;
	result->lifecycle.id = slot->lifecycle.id;
	result->lifecycle.generation = slot->lifecycle.generation;
	result->descriptor = slot->descriptor;
	result->owner_pid = slot->owner_pid;
	result->owner_agent_id = slot->owner_agent_id;
	result->owner_control_id = slot->owner_control_id;
	result->channel_generation = slot->sqe.ring_generation;
	result->request_id = slot->sqe.request_id;
	result->slot_generation = slot->sqe.slot_generation;
}

static int
agent_task_delegate_claim_prepare(
	struct proc *worker, struct thread *thread,
	const struct agent_task_delegate_claim *claim,
	struct agent_task_delegate_claim_result *result)
{
	struct workflow_lifecycle_key lifecycle;
	int enabled;

	agent_task_delegate_claim_result_fill(
		0, result, AGENT_TASK_CHANNEL_BAD_REQUEST,
		AGENT_TASK_DELEGATE_STATE_NONE);
	if (worker == 0 || thread == 0 || claim == 0 ||
	    claim->version != AGENT_TASK_DELEGATE_VERSION ||
	    claim->size != sizeof(*claim) ||
	    (claim->flags & ~AGENT_TASK_DELEGATE_CLAIM_F_ALL) != 0 ||
	    claim->reserved != 0 || claim->reserved_tail[0] != 0 ||
	    claim->reserved_tail[1] != 0 || claim->reserved_tail[2] != 0 ||
	    claim->reserved_tail[3] != 0 || thread != curr_thread() ||
	    thread->process != worker || thread != &worker->threads[0] ||
	    thread->identity_generation == 0)
		return result->status;
	lifecycle.id = claim->lifecycle.id;
	lifecycle.generation = claim->lifecycle.generation;
	for (;;) {
		struct agent_task_delegate_slot *selected = 0;
		uint64 first_sequence = ~0ULL;
		int wait_status;

		enabled = intr_save();
		if (!proc_teardown_live(worker) || !worker->is_agent ||
		    worker->agent_control_id == 0 ||
		    !agent_task_delegate_lifecycle_matches(worker, lifecycle) ||
		    !workflow_lifecycle_active(lifecycle) ||
		    !agent_identity_has_cap(worker, AGENT_CAP_TASK_ACCEPT)) {
			intr_restore(enabled);
			result->status = AGENT_TASK_CHANNEL_STALE;
			return result->status;
		}
		/* A committed claim survives a failed result copyout. */
		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *slot =
				&agent_task_delegate_slots[i];

			if (slot->state != AGENT_TASK_DELEGATE_SLOT_CLAIMED ||
			    slot->worker_pid != worker->pid ||
			    slot->worker_agent_id != worker->agent_id ||
			    slot->worker_control_id != worker->agent_control_id ||
			    slot->worker_thread_generation !=
				    thread->identity_generation ||
			    !workflow_lifecycle_key_equal(
				    slot->lifecycle, lifecycle))
				continue;
			agent_task_delegate_claim_result_fill(
				slot, result, AGENT_TASK_CHANNEL_OK,
				AGENT_TASK_DELEGATE_STATE_CLAIMED);
			intr_restore(enabled);
			return result->status;
		}
		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *slot =
				&agent_task_delegate_slots[i];
			struct proc *owner;
			struct proc *target;

			if (slot->state != AGENT_TASK_DELEGATE_SLOT_QUEUED ||
			    slot->descriptor.target_pid != worker->pid ||
			    slot->descriptor.target_agent_id !=
				    (uint)worker->agent_id ||
			    slot->descriptor.target_control_id !=
				    worker->agent_control_id ||
			    !workflow_lifecycle_key_equal(
				    slot->lifecycle, lifecycle) ||
			    slot->enqueue_sequence >= first_sequence)
				continue;
			owner = agent_task_delegate_find_proc_locked(
				slot->owner_pid, slot->owner_agent_id,
				slot->owner_control_id);
			target = agent_task_delegate_target_locked(
				owner, &slot->descriptor, slot->lifecycle, slot);
			if (target != worker) {
				/*
				 * Route and capability decisions remain revocable until
				 * the provider starts the effect. Do not expose a stale
				 * descriptor; hand terminalization back to the owner lane.
				 */
				slot->terminal_status = owner == 0 ?
					AGENT_STATUS_INDETERMINATE :
					AGENT_STATUS_DENIED;
				slot->terminal_tick = agent_task_bridge_now();
				slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
				agent_task_delegate_wake_owner_locked(slot);
				continue;
			}
			selected = slot;
			first_sequence = slot->enqueue_sequence;
		}
		if (selected != 0) {
			selected->state = AGENT_TASK_DELEGATE_SLOT_CLAIMING;
			selected->worker_pid = worker->pid;
			selected->worker_agent_id = worker->agent_id;
			selected->worker_control_id = worker->agent_control_id;
			selected->worker_thread_generation =
				thread->identity_generation;
			agent_task_delegate_claim_result_fill(
				selected, result, AGENT_TASK_CHANNEL_OK,
				AGENT_TASK_DELEGATE_STATE_CLAIMED);
			intr_restore(enabled);
			return result->status;
		}
		if ((claim->flags & AGENT_TASK_DELEGATE_CLAIM_F_WAIT) == 0) {
			intr_restore(enabled);
			result->status = AGENT_TASK_CHANNEL_RETRY;
			return result->status;
		}
		wait_status = wait_queue_sleep_irq(
			&agent_task_delegate_waiters[worker - pool]);
		intr_restore(enabled);
		/* Enqueue/cancel interrupts are notifications, not claim failure. */
		if (wait_status == WAIT_QUEUE_INTERRUPTED)
			continue;
		if (wait_status != WAIT_QUEUE_OK) {
			result->status = AGENT_TASK_CHANNEL_STALE;
			return result->status;
		}
	}
}

static int
agent_task_delegate_claim_finish(
	struct proc *worker, struct thread *thread,
	const struct agent_task_delegate_claim_result *prepared)
{
	struct agent_task_delegate_slot *slot;
	enum agent_execution_effect_admission effect;
	struct proc *owner;
	struct proc *target;
	uint64 provenance_labels;
	int enabled;

	if (worker == 0 || thread == 0 || prepared == 0 ||
	    thread != curr_thread() || thread->process != worker ||
	    prepared->status != AGENT_TASK_CHANNEL_OK)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	enabled = intr_save();
	slot = 0;
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *candidate =
			&agent_task_delegate_slots[i];

		if ((candidate->state == AGENT_TASK_DELEGATE_SLOT_CLAIMING ||
		     candidate->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED) &&
		    candidate->worker_pid == worker->pid &&
		    candidate->worker_agent_id == worker->agent_id &&
		    candidate->worker_control_id == worker->agent_control_id &&
		    candidate->sqe.ring_generation ==
			    prepared->channel_generation &&
		    candidate->sqe.request_id == prepared->request_id &&
		    candidate->sqe.slot_generation ==
			    prepared->slot_generation) {
			slot = candidate;
			break;
		}
	}
	if (slot == 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_OK;
	}
	if (slot->terminal_pending) {
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		slot->terminal_pending = 0;
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	owner = agent_task_delegate_find_proc_locked(
		slot->owner_pid, slot->owner_agent_id, slot->owner_control_id);
	target = agent_task_delegate_target_locked(
		owner, &slot->descriptor, slot->lifecycle, slot);
	if (target != worker || !proc_teardown_live(worker) ||
	    thread->identity_generation != slot->worker_thread_generation) {
		slot->terminal_status = owner == 0 ?
			AGENT_STATUS_INDETERMINATE : AGENT_STATUS_DENIED;
		slot->terminal_tick = agent_task_bridge_now();
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	if ((slot->sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
	    agent_task_bridge_now() >= slot->sqe.deadline_tick) {
		slot->terminal_status = AGENT_STATUS_TIMEOUT;
		slot->terminal_tick = agent_task_bridge_now();
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	provenance_labels = slot->claim.input_provenance_labels |
		AGENT_PROVENANCE_CROSS_AGENT_DATA;
	if (agent_provenance_merge_current(
		    worker, provenance_labels) != AGENT_STATUS_OK) {
		slot->terminal_status = AGENT_STATUS_INDETERMINATE;
		slot->terminal_tick = agent_task_bridge_now();
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_EVIDENCE;
	}
	provenance_labels = agent_provenance_current_labels(worker) |
		AGENT_PROVENANCE_CROSS_AGENT_DATA;
	slot->provenance.output_labels |= provenance_labels;
	if ((provenance_labels &
	     ~slot->claim.manifest.accepted_input_labels) != 0) {
		slot->terminal_status = AGENT_STATUS_DENIED;
		slot->terminal_tick = agent_task_bridge_now();
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	worker->agent_current_cause_sequence =
		slot->claim.source_context_sequence;
	worker->agent_current_cause_pid = slot->claim.source_pid > 0 ?
		slot->claim.source_pid : slot->owner_pid;
	worker->agent_current_cause_control =
		slot->claim.source_control_id != 0 ?
			slot->claim.source_control_id : slot->owner_control_id;
	effect = agent_execution_contract_effect_begin(&slot->claim);
	if (effect != AGENT_EXECUTION_EFFECT_ALLOWED) {
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		slot->terminal_status =
			effect == AGENT_EXECUTION_EFFECT_CANCELLED ?
				AGENT_STATUS_CANCELLED :
			slot->claim.decision_reason ==
				AGENT_EXECUTION_REASON_DEADLINE_EXPIRED ?
				AGENT_STATUS_TIMEOUT : AGENT_STATUS_STALE;
		slot->terminal_tick = agent_task_bridge_now();
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	slot->claim.executor_pid = worker->pid;
	slot->claim.executor_agent_id = worker->agent_id;
	slot->claim.executor_control_id = worker->agent_control_id;
	slot->claim.executor_context_sequence = worker->context_path_latest;
	slot->state = AGENT_TASK_DELEGATE_SLOT_CLAIMED;
	intr_restore(enabled);
	return AGENT_TASK_CHANNEL_OK;
}

static void
agent_task_delegate_complete_result_fill(
	const struct agent_task_delegate_slot *slot,
	struct agent_task_delegate_complete_result *result, int status,
	uint state)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_DELEGATE_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	result->state = state;
	result->terminal_status = slot != 0 ? slot->terminal_status :
		AGENT_STATUS_BAD_REQUEST;
	result->terminal_generation = slot != 0 ?
		slot->terminal_generation : 0;
	if (slot == 0)
		return;
	result->channel_generation = slot->sqe.ring_generation;
	result->request_id = slot->sqe.request_id;
	result->slot_generation = slot->sqe.slot_generation;
	result->task_id = slot->descriptor.task_id;
	result->correlation_id = slot->descriptor.correlation_id;
}

static uint
agent_task_delegate_public_state(const struct agent_task_delegate_slot *slot)
{
	if (slot == 0)
		return AGENT_TASK_DELEGATE_STATE_NONE;
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_QUEUED ||
	    slot->state == AGENT_TASK_DELEGATE_SLOT_PREPARING)
		return AGENT_TASK_DELEGATE_STATE_QUEUED;
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED ||
	    slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMING)
		return AGENT_TASK_DELEGATE_STATE_CLAIMED;
	return AGENT_TASK_DELEGATE_STATE_READY;
}

static int
agent_task_delegate_cancel_request_ready(
	struct proc *requester, struct thread *thread,
	const struct agent_task_delegate_complete *complete,
	struct agent_task_delegate_complete_result *result)
{
	struct workflow_lifecycle_key lifecycle;
	struct agent_task_delegate_slot *slot = 0;
	struct proc *owner;
	enum agent_execution_delegate_cancel_admission admission;
	uint state;
	uint64 now;
	int channel_status;
	int enabled;

	agent_task_delegate_complete_result_fill(
		0, result, AGENT_TASK_CHANNEL_BAD_REQUEST,
		AGENT_TASK_DELEGATE_STATE_NONE);
	if (requester == 0 || thread == 0 || complete == 0 ||
	    complete->version != AGENT_TASK_DELEGATE_VERSION ||
	    complete->size != sizeof(*complete) ||
	    complete->flags != AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL ||
	    complete->reserved != 0 ||
	    complete->terminal_status != AGENT_STATUS_CANCELLED ||
	    complete->ack_terminal_status != 0 ||
	    complete->terminal_generation != 0 || complete->owner_pid <= 0 ||
	    complete->owner_control_id == 0 ||
	    complete->channel_generation == 0 || complete->request_id == 0 ||
	    complete->slot_generation == 0 || complete->task_id == 0 ||
	    complete->correlation_id == 0 || thread != curr_thread() ||
	    thread->process != requester || thread->identity_generation == 0)
		return result->status;
	lifecycle.id = complete->lifecycle.id;
	lifecycle.generation = complete->lifecycle.generation;
	enabled = intr_save();
	if (!proc_teardown_live(requester) || !requester->is_agent ||
	    requester->agent_control_id == 0 ||
	    !agent_task_delegate_lifecycle_matches(requester, lifecycle) ||
	    !workflow_lifecycle_active(lifecycle) ||
	    !agent_identity_has_cap(requester, AGENT_CAP_ORCHESTRATE) ||
	    !agent_identity_has_cap(requester, AGENT_CAP_WAIT_CANCEL)) {
		result->status = AGENT_TASK_CHANNEL_DENIED;
		goto out;
	}
	/* The exact binding is also the idempotency key for a lost result copyout. */
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		const struct agent_task_delegate_cancel_receipt *receipt =
			&agent_task_delegate_cancel_receipts[i];

		if (!agent_task_delegate_cancel_receipt_binding_matches(
			    receipt, requester, complete, lifecycle))
			continue;
		result->status = AGENT_TASK_CHANNEL_OK;
		result->state = receipt->state;
		result->channel_generation = receipt->channel_generation;
		result->request_id = receipt->request_id;
		result->slot_generation = receipt->slot_generation;
		result->task_id = receipt->task_id;
		result->correlation_id = receipt->correlation_id;
		result->terminal_status = receipt->terminal_status;
		result->terminal_generation = receipt->terminal_generation;
		goto out;
	}
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *candidate =
			&agent_task_delegate_slots[i];

		if (candidate->state != AGENT_TASK_DELEGATE_SLOT_FREE &&
		    candidate->owner_pid == complete->owner_pid &&
		    candidate->owner_control_id == complete->owner_control_id &&
		    candidate->sqe.ring_generation ==
			    complete->channel_generation &&
		    candidate->sqe.request_id == complete->request_id &&
		    candidate->sqe.slot_generation ==
			    complete->slot_generation &&
		    candidate->descriptor.task_id == complete->task_id &&
		    candidate->descriptor.correlation_id ==
			    complete->correlation_id &&
		    workflow_lifecycle_key_equal(
			    candidate->lifecycle, lifecycle)) {
			slot = candidate;
			break;
		}
	}
	if (slot == 0) {
		result->status = AGENT_TASK_CHANNEL_STALE;
		goto out;
	}
	owner = agent_task_delegate_find_proc_locked(
		slot->owner_pid, slot->owner_agent_id, slot->owner_control_id);
	if (owner == 0 ||
	    !agent_task_delegate_lifecycle_matches(owner, lifecycle) ||
	    !agent_identity_has_cap(owner, AGENT_CAP_ORCHESTRATE) ||
	    !agent_ipc_task_route_allows_locked(requester, owner)) {
		result->status = AGENT_TASK_CHANNEL_DENIED;
		goto fill;
	}
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_PREPARING ||
	    slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMING) {
		result->status = AGENT_TASK_CHANNEL_RETRY;
		goto fill;
	}
	if (slot->state != AGENT_TASK_DELEGATE_SLOT_QUEUED &&
	    slot->state != AGENT_TASK_DELEGATE_SLOT_CLAIMED) {
		result->status = AGENT_TASK_CHANNEL_STALE;
		goto fill;
	}
	/*
	 * Validate the owner lane before mutating the Contract.  Interrupts stay
	 * disabled through both commits, so a prior denied SQ CANCEL cannot turn
	 * this exact binding into a user-triggerable invariant failure.
	 */
	channel_status = agent_task_channel_delegate_cancel_preflight_locked(
		owner, complete->channel_generation, complete->request_id,
		complete->slot_generation);
	if (channel_status != AGENT_TASK_CHANNEL_OK) {
		result->status = channel_status;
		goto fill;
	}
	now = agent_task_bridge_now();
	admission = agent_execution_contract_delegate_cancel_preflight_locked(
		&slot->claim, now);
	if (admission == AGENT_EXECUTION_DELEGATE_CANCEL_TIMEOUT) {
		agent_task_delegate_terminal_intent_locked(
			slot, AGENT_STATUS_TIMEOUT);
		/* No provider effect exists in QUEUED, so timeout is immediately final. */
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_QUEUED) {
			slot->terminal_pending = 0;
			slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		}
		agent_task_delegate_wake_target_locked(
			slot->descriptor.target_pid,
			(int)slot->descriptor.target_agent_id,
			slot->descriptor.target_control_id);
		agent_task_delegate_wake_owner_locked(slot);
		result->status = AGENT_TASK_CHANNEL_STALE;
		goto fill;
	}
	if (admission == AGENT_EXECUTION_DELEGATE_CANCEL_DENIED) {
		result->status = AGENT_TASK_CHANNEL_DENIED;
		goto fill;
	}
	if (admission != AGENT_EXECUTION_DELEGATE_CANCEL_ALLOWED) {
		result->status = AGENT_TASK_CHANNEL_STALE;
		goto fill;
	}
	channel_status = agent_task_channel_delegate_cancel_locked(
		owner, complete->channel_generation, complete->request_id,
		complete->slot_generation);
	if (channel_status != AGENT_TASK_CHANNEL_OK) {
		result->status = channel_status;
		goto fill;
	}
	agent_execution_contract_delegate_cancel_commit_locked(&slot->claim);
	agent_task_delegate_terminal_intent_locked(slot, AGENT_STATUS_CANCELLED);
	state = slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED ?
		AGENT_TASK_DELEGATE_STATE_CLAIMED :
		AGENT_TASK_DELEGATE_STATE_READY;
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_QUEUED) {
		slot->terminal_pending = 0;
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
	}
	agent_task_delegate_cancel_receipt_record_locked(slot, requester, state);
	agent_task_delegate_wake_target_locked(
		slot->descriptor.target_pid,
		(int)slot->descriptor.target_agent_id,
		slot->descriptor.target_control_id);
	agent_task_delegate_wake_owner_locked(slot);
	result->status = AGENT_TASK_CHANNEL_OK;

fill:
	state = agent_task_delegate_public_state(slot);
	agent_task_delegate_complete_result_fill(
		slot, result, result->status, state);
out:
	intr_restore(enabled);
	return result->status;
}

static int
agent_task_delegate_complete_ready(
	struct proc *worker, struct thread *thread,
	const struct agent_task_delegate_complete *complete,
	struct agent_task_delegate_complete_result *result)
{
	struct agent_task_delegate_slot *slot = 0;
	struct workflow_lifecycle_key lifecycle;
	uint64 now;
	int enabled;

	agent_task_delegate_complete_result_fill(
		0, result, AGENT_TASK_CHANNEL_BAD_REQUEST,
		AGENT_TASK_DELEGATE_STATE_NONE);
	if (complete != 0 &&
	    complete->flags == AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL)
		return agent_task_delegate_cancel_request_ready(
			worker, thread, complete, result);
	if (worker == 0 || thread == 0 || complete == 0 ||
	    complete->version != AGENT_TASK_DELEGATE_VERSION ||
	    complete->size != sizeof(*complete) ||
	    (complete->flags & ~AGENT_TASK_DELEGATE_COMPLETE_F_ALL) != 0 ||
	    complete->reserved != 0 ||
	    (((complete->flags &
	       AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL) == 0) &&
	     (complete->ack_terminal_status != 0 ||
	      complete->terminal_generation != 0)) ||
	    (((complete->flags &
	       AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL) != 0) &&
	     (complete->terminal_generation == 0 ||
	      !agent_task_delegate_ack_status_valid(
		      complete->ack_terminal_status))) ||
	    complete->owner_pid <= 0 || complete->owner_control_id == 0 ||
	    complete->channel_generation == 0 || complete->request_id == 0 ||
	    complete->slot_generation == 0 || complete->task_id == 0 ||
	    complete->correlation_id == 0 ||
	    !agent_task_delegate_worker_status_valid(
		    complete->terminal_status) || thread != curr_thread() ||
	    thread->process != worker || thread->identity_generation == 0)
		return result->status;
	lifecycle.id = complete->lifecycle.id;
	lifecycle.generation = complete->lifecycle.generation;
	enabled = intr_save();
	if (!proc_teardown_live(worker) || !worker->is_agent ||
	    !agent_task_delegate_lifecycle_matches(worker, lifecycle)) {
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_STALE;
		return result->status;
	}
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *candidate =
			&agent_task_delegate_slots[i];

		if ((candidate->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED ||
		     candidate->state == AGENT_TASK_DELEGATE_SLOT_READY) &&
		    candidate->owner_pid == complete->owner_pid &&
		    candidate->owner_control_id == complete->owner_control_id &&
		    candidate->sqe.ring_generation ==
			    complete->channel_generation &&
		    candidate->sqe.request_id == complete->request_id &&
		    candidate->sqe.slot_generation ==
			    complete->slot_generation &&
		    candidate->descriptor.task_id == complete->task_id &&
		    candidate->descriptor.correlation_id ==
			    complete->correlation_id &&
		    workflow_lifecycle_key_equal(
			    candidate->lifecycle, lifecycle) &&
		    candidate->worker_pid == worker->pid &&
		    candidate->worker_agent_id == worker->agent_id &&
		    candidate->worker_control_id == worker->agent_control_id &&
		    candidate->worker_thread_generation ==
			    thread->identity_generation) {
			slot = candidate;
			break;
		}
	}
	if (slot == 0) {
		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			const struct agent_task_delegate_receipt *receipt =
				&agent_task_delegate_receipts[i];

			if (!agent_task_delegate_receipt_binding_matches(
				    receipt, worker, thread, complete, lifecycle))
				continue;
			agent_task_delegate_complete_result_fill(
				0, result,
				 receipt->submitted_status ==
					complete->terminal_status &&
				 receipt->submitted_flags == complete->flags &&
				 receipt->terminal_generation ==
					complete->terminal_generation &&
				 ((complete->flags &
				   AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL) == 0 ||
				  receipt->terminal_status ==
					complete->ack_terminal_status) ?
					AGENT_TASK_CHANNEL_OK :
					AGENT_TASK_CHANNEL_STALE,
				AGENT_TASK_DELEGATE_STATE_READY);
			result->channel_generation = receipt->channel_generation;
			result->request_id = receipt->request_id;
			result->slot_generation = receipt->slot_generation;
			result->task_id = receipt->task_id;
			result->correlation_id = receipt->correlation_id;
			result->terminal_status = receipt->terminal_status;
			result->terminal_generation = receipt->terminal_generation;
			intr_restore(enabled);
			return result->status;
		}
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_STALE;
		return result->status;
	}
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED &&
	    (slot->effect_refs != 0 || slot->helper_thread_generation != 0)) {
		agent_task_delegate_complete_result_fill(
			slot, result, AGENT_TASK_CHANNEL_RETRY,
			AGENT_TASK_DELEGATE_STATE_CLAIMED);
		intr_restore(enabled);
		return result->status;
	}
	if (slot->state == AGENT_TASK_DELEGATE_SLOT_READY) {
		int status = slot->worker_completion_recorded &&
			     slot->submitted_status == complete->terminal_status &&
			     slot->submitted_flags == complete->flags &&
			     slot->terminal_generation ==
				     complete->terminal_generation &&
			     ((complete->flags &
			       AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL) == 0 ||
			      slot->terminal_status ==
				      complete->ack_terminal_status) ?
			AGENT_TASK_CHANNEL_OK : AGENT_TASK_CHANNEL_STALE;

		agent_task_delegate_complete_result_fill(
			slot, result, status, AGENT_TASK_DELEGATE_STATE_READY);
		intr_restore(enabled);
		return result->status;
	}
	if (slot->completion_ack_pending) {
		now = agent_task_bridge_now();
		if ((slot->sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
		    now >= slot->sqe.deadline_tick)
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_TIMEOUT);
		if (!slot->worker_completion_recorded ||
		    slot->submitted_status != complete->terminal_status) {
			agent_task_delegate_complete_result_fill(
				slot, result, AGENT_TASK_CHANNEL_STALE,
				AGENT_TASK_DELEGATE_STATE_CLAIMED);
			intr_restore(enabled);
			return result->status;
		}
		if ((complete->flags &
		     AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL) == 0) {
			agent_task_delegate_complete_result_fill(
				slot, result, AGENT_TASK_CHANNEL_RETRY,
				AGENT_TASK_DELEGATE_STATE_CLAIMED);
			intr_restore(enabled);
			return result->status;
		}
		if (complete->ack_terminal_status != slot->terminal_status ||
		    complete->terminal_generation != slot->terminal_generation) {
			agent_task_delegate_complete_result_fill(
				slot, result, AGENT_TASK_CHANNEL_RETRY,
				AGENT_TASK_DELEGATE_STATE_CLAIMED);
			intr_restore(enabled);
			return result->status;
		}
		slot->submitted_flags = complete->flags;
		slot->completion_ack_pending = 0;
		slot->terminal_pending = 0;
		slot->provenance.output_labels |=
			agent_provenance_current_labels(worker) |
			AGENT_PROVENANCE_CROSS_AGENT_DATA;
		slot->claim.executor_context_sequence =
			worker->context_path_latest;
		/* Cleanup ACK is the first boundary after which effects are quiescent. */
		slot->terminal_status = agent_task_delegate_kernel_terminal_locked(
			slot, slot->terminal_status, now);
		slot->terminal_tick = now;
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_complete_result_fill(
			slot, result, AGENT_TASK_CHANNEL_OK,
			AGENT_TASK_DELEGATE_STATE_READY);
		agent_task_delegate_wake_owner_locked(slot);
		intr_restore(enabled);
		return result->status;
	}
	if ((complete->flags &
	     AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL) != 0) {
		agent_task_delegate_complete_result_fill(
			slot, result, AGENT_TASK_CHANNEL_STALE,
			AGENT_TASK_DELEGATE_STATE_CLAIMED);
		intr_restore(enabled);
		return result->status;
	}
	if (complete->terminal_status == AGENT_STATUS_OK &&
	    slot->descriptor.expected_result_type != AGENT_ARTIFACT_NONE &&
	    !agent_context_artifact_task_result_valid(
		    worker, slot->descriptor.result_artifact_handle,
		    slot->descriptor.task_id,
		    slot->descriptor.expected_result_type, lifecycle)) {
		agent_task_delegate_complete_result_fill(
			slot, result, AGENT_TASK_CHANNEL_EVIDENCE,
			AGENT_TASK_DELEGATE_STATE_CLAIMED);
		intr_restore(enabled);
		return result->status;
	}
	slot->submitted_status = complete->terminal_status;
	slot->submitted_flags = complete->flags;
	slot->worker_completion_recorded = 1;
	/* Provider taint is monotonic into the owner-side terminal Context. */
	slot->provenance.output_labels |=
		agent_provenance_current_labels(worker) |
		AGENT_PROVENANCE_CROSS_AGENT_DATA;
	slot->claim.executor_context_sequence = worker->context_path_latest;
	now = agent_task_bridge_now();
	if (slot->terminal_pending ||
	    ((slot->sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
	     now >= slot->sqe.deadline_tick)) {
		if (!slot->terminal_pending)
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_TIMEOUT);
		slot->completion_ack_pending = 1;
		agent_task_delegate_complete_result_fill(
			slot, result, AGENT_TASK_CHANNEL_RETRY,
			AGENT_TASK_DELEGATE_STATE_CLAIMED);
		intr_restore(enabled);
		return result->status;
	}
	slot->terminal_status = complete->terminal_status;
	slot->terminal_tick = now;
	slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
	agent_task_delegate_complete_result_fill(
		slot, result, AGENT_TASK_CHANNEL_OK,
		AGENT_TASK_DELEGATE_STATE_READY);
	agent_task_delegate_wake_owner_locked(slot);
	intr_restore(enabled);
	return result->status;
}

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
agent_task_bridge_delegate_descriptor_valid(
	const struct agent_task_delegate_descriptor *descriptor)
{
	return descriptor != 0 &&
	       descriptor->version == AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION &&
	       descriptor->size == sizeof(*descriptor) &&
	       descriptor->target_pid > 0 && descriptor->target_agent_id != 0 &&
	       descriptor->target_control_id != 0 && descriptor->task_id != 0 &&
	       descriptor->correlation_id != 0 && descriptor->task_type != 0 &&
	       descriptor->capsule_handle != 0 &&
	       descriptor->resource_budget != 0 &&
	       descriptor->read_budget != 0 &&
	       (descriptor->required_capabilities & ~AGENT_CAP_KNOWN_ALL) == 0 &&
	       (descriptor->allowed_tools &
		~AGENT_TOOL_GRANT_ALL(AGENT_TOOL_COUNT)) == 0 &&
	       descriptor->expected_result_type <
		       AGENT_CONTEXT_ARTIFACT_KIND_COUNT &&
	       (descriptor->expected_result_type == AGENT_ARTIFACT_NONE ||
		descriptor->result_artifact_handle != 0);
}

static int
agent_task_bridge_delegate_input(
	const struct agent_task_sqe *sqe,
	const struct agent_task_resource_view *input)
{
	struct agent_task_delegate_descriptor descriptor;

	if (sqe == 0 || input == 0 ||
	    sqe->tool_id != AGENT_TOOL_DELEGATE_TASK ||
	    input->handle.type != AGENT_ARTIFACT_TASK ||
	    (input->handle.flags != AGENT_TASK_HANDLE_F_OWNED &&
	     input->handle.flags != AGENT_TASK_HANDLE_F_BORROWED) ||
	    input->length != sizeof(descriptor) ||
	    input->producer_control_id == 0 || input->producer_pid <= 0 ||
	    input->producer_node_id != AGENT_EXECUTION_NODE_NONE)
		return 0;
	memmove(&descriptor, input->snapshot, sizeof(descriptor));
	return agent_task_bridge_delegate_descriptor_valid(&descriptor);
}

static int
agent_task_bridge_op(const struct agent_task_sqe *sqe,
		     const struct agent_task_resource_view *input,
		     struct agent_op *op)
{
	if (sqe == 0 || input == 0 || op == 0)
		return -1;
	memset(op, 0, sizeof(*op));
	op->version = AGENT_OP_VERSION;
	op->tool_id = sqe->tool_id;
	op->request_id = sqe->request_id;
	if (agent_task_bridge_handle_null(input->handle))
		return agent_task_bridge_null_input(sqe, input) ? 0 : -1;
	if (!agent_task_bridge_resource_input(sqe, input) &&
	    !agent_task_bridge_delegate_input(sqe, input))
		return -1;
	uint length = input->length > sizeof(op->payload) ?
		      sizeof(op->payload) : (uint)input->length;
	memmove(op->payload, input->snapshot, length);
	if (input->handle.type == AGENT_ARTIFACT_UTF8)
		op->payload[input->length] = 0;
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
agent_task_bridge_delegate_submit(
	struct proc *p, const struct agent_task_sqe *sqe,
	const struct agent_task_resource_view *input,
	const struct agent_execution_binding *binding, struct agent_op *op,
	struct agent_task_completion *completion)
{
	struct agent_task_delegate_descriptor descriptor;
	struct agent_task_delegate_slot *slot;
	struct agent_execution_outcome outcome;
	struct agent_result result;
	struct proc *target;
	int begin_status;
	int enabled;

	if (p == 0 || sqe == 0 || input == 0 || binding == 0 || op == 0 ||
	    completion == 0 || !agent_task_bridge_delegate_input(sqe, input))
		return AGENT_TASK_CHANNEL_RETRY;
	/* The channel issuer is the process main thread for stable owner identity. */
	if (curr_thread() != &p->threads[0])
		return AGENT_TASK_CHANNEL_RETRY;
	memmove(&descriptor, input->snapshot, sizeof(descriptor));
	if ((descriptor.deadline_tick != 0 &&
	     ((sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) == 0 ||
	      descriptor.deadline_tick != sqe->deadline_tick)) ||
	    (descriptor.required_capabilities & p->agent_capability_mask) !=
		    descriptor.required_capabilities ||
	    (descriptor.allowed_tools & p->agent_tool_grant_mask) !=
		    descriptor.allowed_tools ||
	    (descriptor.input_artifact_handle != 0 &&
	     !agent_context_artifact_task_input_valid(
		     p, descriptor.input_artifact_handle,
		     binding->lifecycle)))
		return AGENT_TASK_CHANNEL_DENIED;
	enabled = intr_save();
	target = agent_task_delegate_target_locked(
		p, &descriptor, binding->lifecycle, 0);
	slot = agent_task_delegate_alloc_locked();
	if (slot == 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	agent_task_delegate_slot_reset_locked(slot);
	slot->state = AGENT_TASK_DELEGATE_SLOT_PREPARING;
	slot->lifecycle = binding->lifecycle;
	slot->sqe = *sqe;
	slot->descriptor = descriptor;
	slot->op = *op;
	slot->owner_pid = p->pid;
	slot->owner_agent_id = p->agent_id;
	slot->owner_control_id = p->agent_control_id;
	intr_restore(enabled);

	memset(&result, 0, sizeof(result));
	memset(&outcome, 0, sizeof(outcome));
	begin_status = agent_execution_task_begin_pending(
		p, &slot->op, binding, agent_task_bridge_request_deadline(sqe),
		agent_task_bridge_admission_policy(sqe), &slot->claim,
		&slot->provenance, &slot->phase_lease, &result, &outcome);
	if (begin_status != AGENT_EXECUTION_TASK_BEGIN_PENDING) {
		enabled = intr_save();
		if (slot->state != AGENT_TASK_DELEGATE_SLOT_PREPARING ||
		    slot->owner_pid != p->pid ||
		    slot->sqe.request_id != sqe->request_id)
			panic("delegated Task begin slot");
		agent_task_delegate_slot_reset_locked(slot);
		intr_restore(enabled);
		if (begin_status != AGENT_EXECUTION_TASK_BEGIN_COMPLETE &&
		    begin_status != AGENT_EXECUTION_TASK_BEGIN_DENIED)
			return AGENT_TASK_CHANNEL_RETRY;
		agent_task_bridge_execution_completion(
			&result, &outcome, sqe, completion);
		return begin_status == AGENT_EXECUTION_TASK_BEGIN_DENIED ?
			AGENT_TASK_HOOK_DENIED : AGENT_TASK_HOOK_COMPLETE;
	}

	enabled = intr_save();
	target = agent_task_delegate_target_locked(
		p, &descriptor, binding->lifecycle, slot);
	if (slot->state != AGENT_TASK_DELEGATE_SLOT_PREPARING ||
	    slot->owner_pid != p->pid ||
	    slot->owner_control_id != p->agent_control_id ||
	    slot->sqe.request_id != sqe->request_id)
		panic("delegated Task publication slot");
	if (target != 0 && !slot->terminal_pending) {
		if (++agent_task_delegate_next_sequence == 0)
			panic("delegated Task sequence");
		slot->enqueue_sequence = agent_task_delegate_next_sequence;
		slot->state = AGENT_TASK_DELEGATE_SLOT_QUEUED;
		agent_task_delegate_wake_target_locked(
			descriptor.target_pid, (int)descriptor.target_agent_id,
			descriptor.target_control_id);
		intr_restore(enabled);
		return AGENT_TASK_HOOK_PENDING;
	}
	begin_status = slot->terminal_pending ?
		AGENT_STATUS_INDETERMINATE : AGENT_STATUS_DENIED;
	slot->state = AGENT_TASK_DELEGATE_SLOT_FINALIZING;
	slot->terminal_pending = 0;
	intr_restore(enabled);
	if (agent_execution_task_finish_pending(
		    p, &slot->op, &slot->claim, &slot->provenance,
		    &slot->phase_lease, begin_status, agent_task_bridge_now(), &result,
		    &outcome) < 0)
		panic("delegated Task target departure");
	agent_task_bridge_execution_completion(&result, &outcome, sqe, completion);
	enabled = intr_save();
	agent_task_delegate_slot_reset_locked(slot);
	intr_restore(enabled);
	return AGENT_TASK_HOOK_DENIED;
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
	if (sqe->tool_id == AGENT_TOOL_DELEGATE_TASK)
		return agent_task_bridge_delegate_submit(
			p, sqe, input, &binding, &op, completion);
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
	uint64 terminal_tick;
	int status;

	if (p != 0 && sqe != 0 && completion != 0 &&
	    sqe->tool_id == AGENT_TOOL_DELEGATE_TASK &&
	    sqe->link_request_id != 0) {
		struct agent_task_delegate_slot *slot = 0;
		struct agent_task_sqe original;
		enum agent_execution_effect_admission effect;
		int enabled = intr_save();

		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *candidate =
				&agent_task_delegate_slots[i];

			if (candidate->state != AGENT_TASK_DELEGATE_SLOT_FREE &&
			    candidate->owner_pid == p->pid &&
			    candidate->owner_agent_id == p->agent_id &&
			    candidate->owner_control_id == p->agent_control_id &&
			    candidate->sqe.ring_generation ==
				    sqe->ring_generation &&
			    candidate->sqe.request_id ==
				    sqe->link_request_id) {
				slot = candidate;
				break;
			}
		}
		if (slot == 0) {
			intr_restore(enabled);
			return AGENT_TASK_HOOK_DENIED;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_READY &&
		    sqe->request_id == 0) {
			/* An accepted provider completion is the immutable winner. */
			intr_restore(enabled);
			return AGENT_TASK_HOOK_PENDING;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED &&
		    sqe->request_id == 0) {
			terminal_tick = agent_task_bridge_now();
			status = (slot->sqe.flags &
				  AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
				 terminal_tick >= slot->sqe.deadline_tick ?
				 AGENT_STATUS_TIMEOUT :
				 AGENT_STATUS_INDETERMINATE;
			agent_task_delegate_terminal_intent_locked(slot, status);
			agent_task_delegate_wake_target_locked(
				slot->descriptor.target_pid,
				(int)slot->descriptor.target_agent_id,
				slot->descriptor.target_control_id);
			intr_restore(enabled);
			return AGENT_TASK_HOOK_PENDING;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_READY ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_FINALIZING) {
			intr_restore(enabled);
			return AGENT_TASK_HOOK_DENIED;
		}
		if (slot->state != AGENT_TASK_DELEGATE_SLOT_QUEUED &&
		    slot->state != AGENT_TASK_DELEGATE_SLOT_CLAIMING) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		slot->state = AGENT_TASK_DELEGATE_SLOT_FINALIZING;
		original = slot->sqe;
		terminal_tick = agent_task_bridge_now();
		intr_restore(enabled);
		if ((original.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
		    terminal_tick >= original.deadline_tick) {
			memset(&result, 0, sizeof(result));
			memset(&outcome, 0, sizeof(outcome));
			slot->claim.decision_reason =
				AGENT_EXECUTION_REASON_DEADLINE_EXPIRED;
			slot->claim.retry_forbidden = 1;
			if (agent_execution_task_finish_pending(
				    p, &slot->op, &slot->claim,
				    &slot->provenance, &slot->phase_lease,
				    AGENT_STATUS_TIMEOUT, terminal_tick,
				    &result, &outcome) < 0)
				panic("delegated Task cancel deadline finish");
			agent_task_bridge_execution_completion(
				&result, &outcome, &original, completion);
			enabled = intr_save();
			agent_task_delegate_slot_reset_locked(slot);
			intr_restore(enabled);
			return AGENT_TASK_HOOK_COMPLETE;
		}
		agent_task_bridge_cancel_request(sqe, &request, 1);
		memset(&result, 0, sizeof(result));
		memset(&outcome, 0, sizeof(outcome));
		status = sqe->request_id == 0 ?
			agent_execution_force_cancel_sync(
				p, &request, &result, &outcome) :
			agent_execution_cancel_sync(
				p, &request, &result, &outcome);
		if (status != AGENT_EXECUTION_CANCEL_SYNC_PENDING &&
		    status != AGENT_EXECUTION_FORCE_CANCEL_PENDING) {
			enabled = intr_save();
			if (slot->state ==
			    AGENT_TASK_DELEGATE_SLOT_FINALIZING)
				slot->state = AGENT_TASK_DELEGATE_SLOT_QUEUED;
			intr_restore(enabled);
			return (status == AGENT_EXECUTION_CANCEL_SYNC_DENIED ||
				status == AGENT_EXECUTION_FORCE_CANCEL_DENIED) ?
				AGENT_TASK_HOOK_DENIED :
				AGENT_TASK_CHANNEL_RETRY;
		}
		effect = agent_execution_contract_effect_begin(&slot->claim);
		if (effect != AGENT_EXECUTION_EFFECT_CANCELLED)
			panic("delegated Task cancel effect");
		if (agent_execution_task_finish_pending(
			    p, &slot->op, &slot->claim, &slot->provenance,
			    &slot->phase_lease, AGENT_STATUS_CANCELLED,
			    terminal_tick, &result,
			    &outcome) < 0)
			panic("delegated Task cancel finish");
		agent_task_bridge_execution_completion(
			&result, &outcome, &original, completion);
		enabled = intr_save();
		agent_task_delegate_slot_reset_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_HOOK_COMPLETE;
	}

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

	if (p != 0 && sqe != 0 && completion != 0 &&
	    sqe->tool_id == AGENT_TOOL_DELEGATE_TASK) {
		struct agent_task_delegate_slot *slot = 0;
		struct agent_task_sqe original;
		int enabled = intr_save();

		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *candidate =
				&agent_task_delegate_slots[i];

			if (candidate->state != AGENT_TASK_DELEGATE_SLOT_FREE &&
			    candidate->owner_pid == p->pid &&
			    candidate->owner_agent_id == p->agent_id &&
			    candidate->owner_control_id == p->agent_control_id &&
			    candidate->sqe.ring_generation ==
				    sqe->ring_generation &&
			    candidate->sqe.request_id == sqe->request_id) {
				slot = candidate;
				break;
			}
		}
		if (slot == 0 ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_FINALIZING) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_READY &&
		    slot->terminal_tick != 0 &&
		    slot->terminal_tick < sqe->deadline_tick) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED) {
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_TIMEOUT);
			agent_task_delegate_wake_target_locked(
				slot->descriptor.target_pid,
				(int)slot->descriptor.target_agent_id,
				slot->descriptor.target_control_id);
			intr_restore(enabled);
			return AGENT_TASK_HOOK_PENDING;
		}
		slot->state = AGENT_TASK_DELEGATE_SLOT_FINALIZING;
		original = slot->sqe;
		intr_restore(enabled);
		memset(&result, 0, sizeof(result));
		memset(&outcome, 0, sizeof(outcome));
		slot->claim.decision_reason =
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED;
		slot->claim.retry_forbidden = 1;
		if (agent_execution_task_finish_pending(
			    p, &slot->op, &slot->claim, &slot->provenance,
			    &slot->phase_lease, AGENT_STATUS_TIMEOUT,
			    agent_task_bridge_now(), &result,
			    &outcome) < 0)
			panic("delegated Task timeout finish");
		agent_task_bridge_execution_completion(
			&result, &outcome, &original, completion);
		enabled = intr_save();
		agent_task_delegate_slot_reset_locked(slot);
		intr_restore(enabled);
		return AGENT_TASK_HOOK_COMPLETE;
	}

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
	    (control->resource_type != AGENT_ARTIFACT_UTF8 &&
	     control->resource_type != AGENT_ARTIFACT_TASK) ||
	    control->resource_flags != AGENT_TASK_HANDLE_F_OWNED ||
	    control->source_handle >= FD_BUFFER_SIZE || control->length == 0 ||
	    control->length > AGENT_TASK_RESOURCE_SNAPSHOT_SIZE ||
	    (control->resource_type == AGENT_ARTIFACT_UTF8 &&
	     control->length > AGENT_TASK_RESOURCE_UTF8_MAX) ||
	    (control->resource_type == AGENT_ARTIFACT_TASK &&
	     control->length != sizeof(struct agent_task_delegate_descriptor)) ||
	    file == 0)
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
	uint read_length = length +
		(control->resource_type == AGENT_ARTIFACT_UTF8 ? 1U : 0U);
	got = readi_lease(file->ip, &cred, &lease, 0,
			  (uint64)imported->snapshot, 0, read_length);
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
	if (control->resource_type == AGENT_ARTIFACT_UTF8) {
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
	} else {
		struct agent_task_delegate_descriptor descriptor;

		memmove(&descriptor, imported->snapshot, sizeof(descriptor));
		if (!agent_task_bridge_delegate_descriptor_valid(&descriptor)) {
			status = AGENT_TASK_CHANNEL_BAD_REQUEST;
			goto out;
		}
	}
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
	imported->resource_type = control->resource_type;
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
	if ((type != AGENT_ARTIFACT_UTF8 && type != AGENT_ARTIFACT_TASK) ||
	    flags != AGENT_TASK_HANDLE_F_OWNED || length == 0 ||
	    length > AGENT_TASK_RESOURCE_SNAPSHOT_SIZE ||
	    (type == AGENT_ARTIFACT_UTF8 &&
	     length > AGENT_TASK_RESOURCE_UTF8_MAX) ||
	    (type == AGENT_ARTIFACT_TASK &&
	     length != sizeof(struct agent_task_delegate_descriptor)))
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

static int
agent_task_bridge_delegate_pump(struct proc *owner)
{
	int completed = 0;

	for (;;) {
		struct agent_task_delegate_slot *slot = 0;
		struct agent_task_completion completion;
		struct agent_execution_outcome outcome;
		struct agent_result result;
		struct agent_task_sqe sqe;
		int terminal_status;
		int enabled = intr_save();

		for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
			struct agent_task_delegate_slot *candidate =
				&agent_task_delegate_slots[i];

			if (candidate->state == AGENT_TASK_DELEGATE_SLOT_READY &&
			    owner != 0 && candidate->owner_pid == owner->pid &&
			    candidate->owner_agent_id == owner->agent_id &&
			    candidate->owner_control_id == owner->agent_control_id) {
				slot = candidate;
				break;
			}
		}
		if (slot == 0) {
			intr_restore(enabled);
			return completed;
		}
		slot->state = AGENT_TASK_DELEGATE_SLOT_FINALIZING;
		sqe = slot->sqe;
		terminal_status = slot->terminal_status;
		intr_restore(enabled);
		memset(&result, 0, sizeof(result));
		memset(&outcome, 0, sizeof(outcome));
		if (agent_execution_task_finish_pending(
			    owner, &slot->op, &slot->claim, &slot->provenance,
			    &slot->phase_lease, terminal_status,
			    slot->terminal_tick, &result,
			    &outcome) < 0)
			panic("delegated Task owner finish");
		agent_task_bridge_execution_completion(
			&result, &outcome, &sqe, &completion);
		if (agent_task_channel_complete(
			    owner, sqe.ring_generation, sqe.request_id,
			    sqe.slot_generation, &completion,
			    &agent_task_bridge_ops) != AGENT_TASK_CHANNEL_OK)
			panic("delegated Task channel completion");
		enabled = intr_save();
		if (slot->state != AGENT_TASK_DELEGATE_SLOT_FINALIZING ||
		    slot->owner_pid != owner->pid ||
		    slot->sqe.request_id != sqe.request_id)
			panic("delegated Task owner cleanup");
		agent_task_delegate_receipt_record_locked(slot);
		agent_task_delegate_slot_reset_locked(slot);
		intr_restore(enabled);
		completed++;
	}
}

void
agent_task_bridge_init(void)
{
	memset(agent_task_delegate_slots, 0,
	       sizeof(agent_task_delegate_slots));
	memset(agent_task_delegate_receipts, 0,
	       sizeof(agent_task_delegate_receipts));
	memset(agent_task_delegate_cancel_receipts, 0,
	       sizeof(agent_task_delegate_cancel_receipts));
	agent_task_delegate_next_sequence = 0;
	agent_task_delegate_next_receipt = 0;
	agent_task_delegate_next_cancel_receipt = 0;
	for (uint i = 0; i < NPROC; i++)
		wait_queue_init(&agent_task_delegate_waiters[i],
				WAIT_REASON_EVENT);
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

	if (!agent_task_channel_current_issuer(p, curr_thread()))
		return 0;

	while (agent_task_channel_deadline_due(p)) {
		int status;

		if (agent_task_bridge_delegate_pump(p) != 0)
			continue;
		status = agent_task_channel_expire(
			p, agent_task_bridge_now(), &agent_task_bridge_ops);

		if (status < 0)
			return status;
		expired += status;
		if (status == 0 && agent_task_channel_deadline_due(p))
			return expired;
	}
	return expired;
}

int
agent_task_bridge_reclaim(struct proc *p)
{
	int status;
	int enabled;

	if (p == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	if (agent_task_channel_current_issuer_cleanup(p, curr_thread()))
		(void)agent_task_bridge_delegate_pump(p);
	enabled = intr_save();
	if (p >= pool && p < &pool[NPROC])
		(void)wait_queue_wake_all(
			&agent_task_delegate_waiters[p - pool]);
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *slot =
			&agent_task_delegate_slots[i];

		if (slot->state == AGENT_TASK_DELEGATE_SLOT_FREE ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_FINALIZING ||
		    slot->descriptor.target_pid != p->pid ||
		    slot->descriptor.target_agent_id != (uint)p->agent_id ||
		    slot->descriptor.target_control_id != p->agent_control_id)
			continue;
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_READY) {
			agent_task_delegate_wake_owner_locked(slot);
			continue;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_PREPARING) {
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_INDETERMINATE);
			continue;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMING) {
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_INDETERMINATE);
			continue;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED &&
		    p->teardown_state == PROC_TEARDOWN_QUIESCING) {
			/* The first teardown pass precedes sibling quiescence. */
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_INDETERMINATE);
			continue;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED &&
		    slot->effect_refs != 0)
			panic("delegated Task quiesced effect reference");
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED)
			slot->provenance.output_labels |=
				agent_provenance_current_labels(p) |
				AGENT_PROVENANCE_CROSS_AGENT_DATA;
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED)
			slot->claim.executor_context_sequence =
				p->context_path_latest;
		slot->terminal_tick = agent_task_bridge_now();
		slot->terminal_status = agent_task_delegate_kernel_terminal_locked(
			slot, AGENT_STATUS_INDETERMINATE,
			slot->terminal_tick);
		slot->terminal_pending = 0;
		slot->worker_completion_recorded = 0;
		slot->completion_ack_pending = 0;
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_wake_owner_locked(slot);
	}
	intr_restore(enabled);
	status = agent_task_channel_reclaim(p, &agent_task_bridge_ops);
	if (status != AGENT_TASK_CHANNEL_OK)
		return status;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *slot =
			&agent_task_delegate_slots[i];
		struct agent_task_delegate_receipt *receipt =
			&agent_task_delegate_receipts[i];
		struct agent_task_delegate_cancel_receipt *cancel_receipt =
			&agent_task_delegate_cancel_receipts[i];

		if (slot->state != AGENT_TASK_DELEGATE_SLOT_FREE &&
		    slot->owner_pid == p->pid &&
		    slot->owner_agent_id == p->agent_id &&
		    slot->owner_control_id == p->agent_control_id)
			panic("delegated Task owner reclaim");
		if (receipt->valid &&
		    ((receipt->owner_pid == p->pid &&
		      receipt->owner_agent_id == p->agent_id &&
		      receipt->owner_control_id == p->agent_control_id) ||
		     (receipt->worker_pid == p->pid &&
		      receipt->worker_agent_id == p->agent_id &&
		      receipt->worker_control_id == p->agent_control_id)))
			memset(receipt, 0, sizeof(*receipt));
		if (cancel_receipt->valid &&
		    ((cancel_receipt->owner_pid == p->pid &&
		      cancel_receipt->owner_agent_id == p->agent_id &&
		      cancel_receipt->owner_control_id == p->agent_control_id) ||
		     (cancel_receipt->requester_pid == p->pid &&
		      cancel_receipt->requester_agent_id == p->agent_id &&
		      cancel_receipt->requester_control_id ==
			      p->agent_control_id)))
			memset(cancel_receipt, 0, sizeof(*cancel_receipt));
	}
	intr_restore(enabled);
	return AGENT_TASK_CHANNEL_OK;
}

int
agent_task_bridge_active(const struct proc *p)
{
	return agent_task_channel_active(p);
}

int
agent_task_bridge_endpoint_active(const struct proc *p)
{
	int active = 0;
	int enabled;

	if (p == 0)
		return 0;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		const struct agent_task_delegate_slot *slot =
			&agent_task_delegate_slots[i];

		if (slot->state == AGENT_TASK_DELEGATE_SLOT_FREE)
			continue;
		if ((slot->owner_pid == p->pid &&
		     slot->owner_agent_id == p->agent_id &&
		     slot->owner_control_id == p->agent_control_id) ||
		    (slot->descriptor.target_pid == p->pid &&
		     slot->descriptor.target_agent_id == (uint)p->agent_id &&
		     slot->descriptor.target_control_id ==
			     p->agent_control_id)) {
			active = 1;
			break;
		}
	}
	intr_restore(enabled);
	return active;
}

void
agent_task_bridge_lifecycle_closed(struct workflow_lifecycle_key lifecycle)
{
	int enabled;

	if (!workflow_lifecycle_key_valid(lifecycle))
		return;
	enabled = intr_save();
	for (uint i = 0; i < NPROC; i++)
		(void)wait_queue_wake_all(&agent_task_delegate_waiters[i]);
	for (uint i = 0; i < AGENT_TASK_DELEGATE_CAPACITY; i++) {
		struct agent_task_delegate_slot *slot =
			&agent_task_delegate_slots[i];

		if (slot->state == AGENT_TASK_DELEGATE_SLOT_FREE ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_FINALIZING ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_READY ||
		    !workflow_lifecycle_key_equal(slot->lifecycle, lifecycle))
			continue;
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_PREPARING ||
		    slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMING) {
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_INDETERMINATE);
			continue;
		}
		if (slot->state == AGENT_TASK_DELEGATE_SLOT_CLAIMED) {
			agent_task_delegate_terminal_intent_locked(
				slot, AGENT_STATUS_INDETERMINATE);
			agent_task_delegate_wake_target_locked(
				slot->descriptor.target_pid,
				(int)slot->descriptor.target_agent_id,
				slot->descriptor.target_control_id);
			continue;
		}
		slot->terminal_tick = agent_task_bridge_now();
		slot->terminal_status = agent_task_delegate_kernel_terminal_locked(
			slot, AGENT_STATUS_INDETERMINATE,
			slot->terminal_tick);
		slot->terminal_pending = 0;
		slot->state = AGENT_TASK_DELEGATE_SLOT_READY;
		agent_task_delegate_wake_owner_locked(slot);
	}
	intr_restore(enabled);
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
	if (agent_task_channel_current_issuer(p, curr_thread())) {
		(void)agent_task_bridge_delegate_pump(p);
		(void)agent_task_channel_expire(
			p, agent_task_bridge_now(), &agent_task_bridge_ops);
		(void)agent_task_bridge_delegate_pump(p);
	}
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

int
sys_agent_task_delegate_claim(uint64 claimaddr, uint64 resultaddr)
{
	struct agent_task_delegate_claim claim;
	struct agent_task_delegate_claim_result result;
	struct proc *p = curr_proc();
	int status;

	if (p == 0 || copyin(p->pagetable, (char *)&claim, claimaddr,
			     sizeof(claim)) < 0)
		return -1;
	agent_task_delegate_claim_result_fill(
		0, &result, AGENT_TASK_CHANNEL_BAD_REQUEST,
		AGENT_TASK_DELEGATE_STATE_NONE);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	status = agent_task_delegate_claim_prepare(
		p, curr_thread(), &claim, &result);
	if (status == AGENT_TASK_CHANNEL_OK) {
		status = agent_task_delegate_claim_finish(
			p, curr_thread(), &result);
		if (status != AGENT_TASK_CHANNEL_OK) {
			result.status = status;
			result.state = AGENT_TASK_DELEGATE_STATE_READY;
			memset(&result.descriptor, 0,
			       sizeof(result.descriptor));
		}
	}
	/* An OK descriptor is visible only after effect and provenance commit. */
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	return 0;
}

int
sys_agent_task_delegate_complete(uint64 completionaddr, uint64 resultaddr)
{
	struct agent_task_delegate_complete complete;
	struct agent_task_delegate_complete_result result;
	struct proc *p = curr_proc();

	if (p == 0 || copyin(p->pagetable, (char *)&complete, completionaddr,
			     sizeof(complete)) < 0)
		return -1;
	agent_task_delegate_complete_result_fill(
		0, &result, AGENT_TASK_CHANNEL_BAD_REQUEST,
		AGENT_TASK_DELEGATE_STATE_NONE);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	(void)agent_task_delegate_complete_ready(
		p, curr_thread(), &complete, &result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	return 0;
}
