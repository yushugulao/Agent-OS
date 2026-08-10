#include "agent_task_channel.h"
#include "defs.h"
#include "timer.h"
#include "../agent_provenance_abi.h"

#define AGENT_TASK_CHANNEL_OWNER_FREE       0U
#define AGENT_TASK_CHANNEL_OWNER_SETTING_UP 1U
#define AGENT_TASK_CHANNEL_OWNER_LIVE       2U
#define AGENT_TASK_CHANNEL_OWNER_RECLAIMING 3U
#define AGENT_TASK_CHANNEL_OWNER_FINALIZING 4U

#define AGENT_TASK_EXEC_ALIAS_NONE      0U
#define AGENT_TASK_EXEC_ALIAS_STAGED    1U
#define AGENT_TASK_EXEC_ALIAS_COMMITTED 2U

#define AGENT_TASK_REQUEST_F_INPUT_HELD    (1U << 0)
#define AGENT_TASK_REQUEST_F_CANCEL_REQUESTED (1U << 1)
#define AGENT_TASK_REQUEST_F_LIFECYCLE_HELD (1U << 2)
#define AGENT_TASK_REQUEST_F_DEADLINE_DUE   (1U << 3)
#define AGENT_TASK_REQUEST_F_CANCEL_DENIED  (1U << 4)

struct agent_task_sq_page {
	struct agent_task_ring_header header;
	struct agent_task_sqe entries[AGENT_TASK_CHANNEL_CAPACITY];
	uchar padding[PAGE_SIZE - sizeof(struct agent_task_ring_header) -
		      AGENT_TASK_CHANNEL_CAPACITY * sizeof(struct agent_task_sqe)];
};

struct agent_task_cq_page {
	struct agent_task_ring_header header;
	struct agent_task_cqe entries[AGENT_TASK_CHANNEL_CAPACITY];
	uchar padding[PAGE_SIZE - sizeof(struct agent_task_ring_header) -
		      AGENT_TASK_CHANNEL_CAPACITY * sizeof(struct agent_task_cqe)];
};

struct agent_task_private_header {
	uint64 generation;
	uint64 sq_head;
	uint64 sq_tail;
	uint64 cq_head;
	uint64 cq_tail;
	uint64 submitted;
	uint64 completed;
	uint64 backpressure;
	uint64 protocol_faults;
	uint64 resync_count;
	uint64 issuer_generation;
	int issuer_tid;
	uint flags;
	uint live_requests;
	uint terminal_pending;
	uint resource_count;
	uint mapped_page_count;
	uint64 last_accepted_request_id;
	uint callback_refs;
	uint exec_alias_refs;
};

struct agent_task_request_slot {
	struct agent_task_sqe sqe;
	struct agent_task_resource_handle result;
	uint64 context_sequence;
	uint64 evidence_ticket;
	uint64 provenance_labels;
	uint64 completion_tick;
	uint64 cq_position;
	uint64 accepted_tick;
	int status;
	uint decision_reason;
	uint state;
	uint cqe_flags;
	uint flags;
	uint expected_output_type;
	uint reserved[2];
};

struct agent_task_resource_slot {
	uint64 generation;
	uint64 source_handle;
	uint64 length;
	uint64 owner_request_id;
	uint64 provenance_labels;
	uint64 producer_context_sequence;
	uint64 producer_control_id;
	uchar content_digest[AGENT_TASK_CHANNEL_SCHEMA_SIZE];
	uint64 owner_ring_generation;
	uint64 owner_slot_generation;
	uint state;
	uint references;
	uint producer_node_id;
	int producer_pid;
	ushort type;
	ushort flags;
	uint reserved;
};

struct agent_task_private_page {
	struct agent_task_private_header header;
	struct agent_task_request_slot requests[AGENT_TASK_CHANNEL_CAPACITY];
	uchar padding[PAGE_SIZE - sizeof(struct agent_task_private_header) -
		      AGENT_TASK_CHANNEL_CAPACITY *
			      sizeof(struct agent_task_request_slot)];
};

struct agent_task_resource_page {
	struct agent_task_resource_slot
		resources[AGENT_TASK_CHANNEL_RESOURCE_CAPACITY];
	uchar padding[PAGE_SIZE - AGENT_TASK_CHANNEL_RESOURCE_CAPACITY *
		      sizeof(struct agent_task_resource_slot)];
};

struct agent_task_channel_state {
	uint state;
	struct proc *owner;
	int owner_pid;
	uint departure_held;
	uint64 generation_highwater;
	uint64 resource_generation_highwater[
		AGENT_TASK_CHANNEL_RESOURCE_CAPACITY];
	struct workflow_lifecycle_key lifecycle;
	const struct agent_task_channel_ops *ops;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	struct agent_task_sq_page *sq;
	struct agent_task_cq_page *cq;
	struct agent_task_private_page *private;
	struct agent_task_resource_page *resource_private;
};

struct agent_task_release {
	int valid;
	uint type;
	uint flags;
	uint64 source_handle;
	uint64 length;
};

static struct agent_task_channel_state agent_task_channels[NPROC];
extern struct proc pool[NPROC];

static int agent_task_deadline_completion(
	struct proc *, const struct agent_task_channel_ops *,
	const struct agent_task_sqe *, struct agent_task_completion *);
static void agent_task_channel_publish_locked(
	struct agent_task_channel_state *);

_Static_assert(CPU_FREQ % TICKS_PER_SEC == 0,
	       "Task Channel tick conversion must be exact");
_Static_assert(sizeof(struct agent_task_sq_page) == PAGE_SIZE,
	       "Task Channel SQ must occupy one page");
_Static_assert(sizeof(struct agent_task_cq_page) == PAGE_SIZE,
	       "Task Channel CQ must occupy one page");
_Static_assert(sizeof(struct agent_task_private_header) == 128,
	       "Task Channel private header layout");
_Static_assert(sizeof(struct agent_task_request_slot) == 224,
	       "Task Channel private request layout");
_Static_assert(sizeof(struct agent_task_resource_slot) == 128,
	       "Task Channel private resource layout");
_Static_assert(sizeof(struct agent_task_resource_import) == 88,
	       "Task Channel resource import hook layout");
_Static_assert(sizeof(struct agent_task_resource_view) == 96,
	       "Task Channel resource view hook layout");
_Static_assert(sizeof(struct agent_task_private_page) == PAGE_SIZE,
	       "Task Channel copied descriptors and terminal state must fit one page");
_Static_assert(sizeof(struct agent_task_resource_page) == PAGE_SIZE,
	       "Task Channel authoritative resource state must fit one page");
_Static_assert(AGENT_TASK_CHANNEL_SQ_BASE + PAGE_SIZE ==
	       AGENT_TASK_CHANNEL_CQ_BASE,
	       "Task Channel mapped pages must be adjacent");
_Static_assert(AGENT_TASK_CHANNEL_CQ_BASE + PAGE_SIZE == AGENT_CONTEXT_BASE,
	       "Task Channel pages must end at the frozen Context base");
_Static_assert(AGENT_CONTEXT_BASE + AGENT_CONTEXT_SIZE <=
	       TRAPFRAME - (NTHREAD - 1U) * PAGE_SIZE,
	       "Agent Context must not overlap thread trapframes");

static uint64
agent_task_now(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

/* The SQ descriptor is already consumed even if its deadline won no work. */
static int
agent_task_consumed_after_expire(int status)
{
	return status >= 0 ? 1 : status;
}

static int
agent_task_lifecycle_matches(const struct proc *p,
			     struct agent_workflow_lifecycle_key key)
{
	return p != 0 && key.reserved == 0 && key.id != 0 &&
	       key.generation != 0 && p->workflow_lifecycle_charged &&
	       p->workflow_lifecycle_id == key.id &&
	       p->workflow_lifecycle_generation == key.generation;
}

static struct workflow_lifecycle_key
agent_task_lifecycle_key(const struct proc *p)
{
	struct workflow_lifecycle_key key = workflow_lifecycle_none();

	if (p != 0) {
		key.id = p->workflow_lifecycle_id;
		key.generation = p->workflow_lifecycle_generation;
	}
	return key;
}

static int
agent_task_handle_null(struct agent_task_resource_handle handle)
{
	return handle.slot == 0 && handle.type == 0 && handle.flags == 0 &&
	       handle.generation == 0;
}

static int
agent_task_handle_shape_valid(struct agent_task_resource_handle handle)
{
	uint ownership;

	if (handle.slot == 0)
		return agent_task_handle_null(handle);
	ownership = handle.flags & AGENT_TASK_HANDLE_F_ALL;
	return handle.slot <= AGENT_TASK_CHANNEL_RESOURCE_CAPACITY &&
	       handle.type > AGENT_ARTIFACT_NONE &&
	       handle.type < AGENT_ARTIFACT_TYPE_COUNT &&
	       handle.generation != 0 &&
	       (handle.flags & ~AGENT_TASK_HANDLE_F_ALL) == 0 &&
	       (ownership == AGENT_TASK_HANDLE_F_OWNED ||
		ownership == AGENT_TASK_HANDLE_F_BORROWED);
}

static int
agent_task_schema_present(const uchar digest[AGENT_TASK_CHANNEL_SCHEMA_SIZE])
{
	uchar value = 0;

	for (uint i = 0; i < AGENT_TASK_CHANNEL_SCHEMA_SIZE; i++)
		value |= digest[i];
	return value != 0;
}

static int
agent_task_validation_valid(const struct agent_task_validation *validation)
{
	if (validation == 0 || validation->reserved != 0 ||
	    validation->output_artifact_type >= AGENT_ARTIFACT_TYPE_COUNT ||
	    (validation->output_provenance_labels & ~AGENT_PROVENANCE_ALL) != 0)
		return 0;
	return validation->output_artifact_type == AGENT_ARTIFACT_NONE ?
		validation->output_provenance_labels == 0 :
		validation->output_provenance_labels != 0;
}

static int
agent_task_contract_equal(struct agent_execution_contract_key a,
			  struct agent_execution_contract_key b)
{
	return a.lifecycle.id == b.lifecycle.id &&
	       a.lifecycle.reserved == b.lifecycle.reserved &&
	       a.lifecycle.generation == b.lifecycle.generation &&
	       a.generation == b.generation;
}

static struct agent_task_channel_state *
agent_task_channel_find_locked(const struct proc *p)
{
	struct agent_task_channel_state *state;
	uint index;

	if (p == 0 || p < pool || p >= &pool[NPROC])
		return 0;
	index = (uint)(p - pool);
	state = &agent_task_channels[index];
	return state->state != AGENT_TASK_CHANNEL_OWNER_FREE &&
	       state->owner == p && state->owner_pid == p->pid ? state : 0;
}

static struct agent_task_channel_state *
agent_task_channel_alloc_locked(struct proc *p, uint64 *generation)
{
	struct agent_task_channel_state *state;
	uint64 next;

	if (p == 0 || p < pool || p >= &pool[NPROC])
		return 0;
	state = &agent_task_channels[p - pool];
	if (state->state != AGENT_TASK_CHANNEL_OWNER_FREE ||
	    state->generation_highwater == ~0ULL)
		return 0;
	next = state->generation_highwater + 1;
	if (next == 0)
		return 0;
	state->generation_highwater = next;
	state->state = AGENT_TASK_CHANNEL_OWNER_SETTING_UP;
	state->owner = p;
	state->owner_pid = p->pid;
	state->lifecycle = workflow_lifecycle_none();
	state->account = resource_account_none();
	state->charge_class = RESOURCE_CHARGE_CLASS_COUNT;
	state->sq = 0;
	state->cq = 0;
	state->private = 0;
	state->resource_private = 0;
	*generation = next;
	return state;
}

static void
agent_task_channel_reset_locked(struct agent_task_channel_state *state)
{
	uint64 highwater = state->generation_highwater;
	uint64 resource_highwater[AGENT_TASK_CHANNEL_RESOURCE_CAPACITY];

	memmove(resource_highwater, state->resource_generation_highwater,
		sizeof(resource_highwater));
	memset(state, 0, sizeof(*state));
	state->generation_highwater = highwater;
	memmove(state->resource_generation_highwater, resource_highwater,
		sizeof(resource_highwater));
	state->lifecycle = workflow_lifecycle_none();
	state->account = resource_account_none();
	state->charge_class = RESOURCE_CHARGE_CLASS_COUNT;
}

static int
agent_task_channel_owner_valid_locked(struct agent_task_channel_state *state,
				      const struct proc *p)
{
	struct agent_task_private_header *header;

	if (state == 0 ||
	    (state->state != AGENT_TASK_CHANNEL_OWNER_LIVE &&
	     state->state != AGENT_TASK_CHANNEL_OWNER_RECLAIMING &&
	     state->state != AGENT_TASK_CHANNEL_OWNER_FINALIZING) ||
	    state->owner != p || p == 0 || state->owner_pid != p->pid ||
	    state->private == 0 || state->resource_private == 0 ||
	    state->sq == 0 || state->cq == 0 ||
	    !p->workflow_lifecycle_charged ||
	    p->workflow_lifecycle_id == 0 ||
	    p->workflow_lifecycle_generation == 0 ||
	    state->lifecycle.id != p->workflow_lifecycle_id ||
	    state->lifecycle.generation !=
		    p->workflow_lifecycle_generation ||
	    !resource_account_handle_equal(state->account,
					   p->resource_account))
		return 0;
	header = &state->private->header;
	return header->generation != 0 &&
	       header->mapped_page_count == AGENT_TASK_CHANNEL_MAPPED_PAGES;
}

static int
agent_task_channel_issuer_valid_locked(
	struct agent_task_channel_state *state, const struct proc *p,
	const struct thread *issuer)
{
	return agent_task_channel_owner_valid_locked(state, p) && issuer != 0 &&
	       state->state == AGENT_TASK_CHANNEL_OWNER_LIVE &&
	       proc_teardown_live(p) &&
	       workflow_lifecycle_active(state->lifecycle) &&
	       issuer == curr_thread() &&
	       issuer->process == p && issuer->identity_generation != 0 &&
	       issuer->tid == state->private->header.issuer_tid &&
	       issuer->identity_generation ==
		       state->private->header.issuer_generation;
}

static struct agent_task_request_slot *
agent_task_channel_request_authorized_locked(
	struct agent_task_channel_state *state, uint64 ring_generation,
	uint64 request_id, uint64 slot_generation)
{
	if (ring_generation == 0 || request_id == 0 || slot_generation == 0 ||
	    state == 0 || state->private == 0 ||
	    state->state != AGENT_TASK_CHANNEL_OWNER_LIVE)
		return 0;
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
		struct agent_task_request_slot *request =
			&state->private->requests[i];

		if (request->state == AGENT_TASK_REQUEST_RUNNING &&
		    request->sqe.opcode == AGENT_TASK_CHANNEL_OP_SUBMIT &&
		    request->sqe.ring_generation == ring_generation &&
		    request->sqe.request_id == request_id &&
		    request->sqe.slot_generation == slot_generation &&
		    (request->flags &
		     AGENT_TASK_REQUEST_F_LIFECYCLE_HELD) != 0)
			return request;
	}
	return 0;
}

static void
agent_task_ring_header_fill(struct agent_task_ring_header *ring, uint64 magic,
			    uint entry_size,
			    const struct agent_task_private_header *state,
			    int completion)
{
	memset(ring, 0, sizeof(*ring));
	ring->magic = magic;
	ring->version = AGENT_TASK_CHANNEL_VERSION;
	ring->struct_size = sizeof(*ring);
	ring->entry_size = entry_size;
	ring->capacity = AGENT_TASK_CHANNEL_CAPACITY;
	ring->generation = state->generation;
	ring->head = completion ? state->cq_head : state->sq_head;
	ring->tail = completion ? state->cq_tail : state->sq_tail;
	ring->flags = state->flags & AGENT_TASK_CHANNEL_RING_F_ALL;
	ring->submitted = state->submitted;
	ring->completed = state->completed;
	ring->backpressure = state->backpressure;
	ring->protocol_faults = state->protocol_faults;
	ring->resync_count = state->resync_count;
	ring->last_accepted_request_id = state->last_accepted_request_id;
}

static void
agent_task_channel_publish_locked(struct agent_task_channel_state *state)
{
	struct agent_task_ring_header sq;
	struct agent_task_ring_header cq;

	if (state == 0 || state->private == 0 || state->sq == 0 ||
	    state->cq == 0)
		return;
	agent_task_ring_header_fill(&sq, AGENT_TASK_CHANNEL_SQ_MAGIC,
				    sizeof(struct agent_task_sqe),
				    &state->private->header, 0);
	agent_task_ring_header_fill(&cq, AGENT_TASK_CHANNEL_CQ_MAGIC,
				    sizeof(struct agent_task_cqe),
				    &state->private->header, 1);
	memmove(&state->sq->header, &sq, sizeof(sq));
	memmove(&state->cq->header, &cq, sizeof(cq));
	__sync_synchronize();
}

static int
agent_task_channel_protocol_fault_locked(
	struct agent_task_channel_state *state)
{
	struct agent_task_private_header *header = &state->private->header;

	header->protocol_faults++;
	if ((header->flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) == 0) {
		header->flags |= AGENT_TASK_CHANNEL_RING_F_RESYNC;
		header->resync_count++;
		if (header->generation != ~0ULL) {
			header->generation++;
			state->generation_highwater = header->generation;
		}
		header->sq_head = 0;
		header->sq_tail = 0;
		memset(state->sq->entries, 0, sizeof(state->sq->entries));
	}
	agent_task_channel_publish_locked(state);
	return AGENT_TASK_CHANNEL_RESYNC_REQUIRED;
}

static struct agent_task_request_slot *
agent_task_request_find_locked(struct agent_task_private_page *private,
			       uint64 request_id)
{
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
		struct agent_task_request_slot *slot = &private->requests[i];

		if (slot->state != AGENT_TASK_REQUEST_FREE &&
		    slot->sqe.request_id == request_id)
			return slot;
	}
	return 0;
}

static struct agent_task_request_slot *
agent_task_request_alloc_locked(struct agent_task_private_page *private)
{
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++)
		if (private->requests[i].state == AGENT_TASK_REQUEST_FREE)
			return &private->requests[i];
	return 0;
}

static struct agent_task_resource_slot *
agent_task_resource_find_locked(struct agent_task_resource_page *resources,
				struct agent_task_resource_handle handle)
{
	struct agent_task_resource_slot *slot;

	if (!agent_task_handle_shape_valid(handle) || handle.slot == 0)
		return 0;
	slot = &resources->resources[handle.slot - 1];
	if (slot->state == AGENT_TASK_RESOURCE_STATE_NONE ||
	    slot->generation != handle.generation || slot->type != handle.type ||
	    slot->flags != AGENT_TASK_HANDLE_F_OWNED)
		return 0;
	return slot;
}

static int
agent_task_resource_import_valid(
	const struct agent_task_resource_import *imported)
{
	return imported != 0 && imported->producer_control_id != 0 &&
	       imported->producer_pid > 0 &&
	       imported->resource_type > AGENT_ARTIFACT_NONE &&
	       imported->resource_type < AGENT_ARTIFACT_TYPE_COUNT &&
	       imported->resource_flags == AGENT_TASK_HANDLE_F_OWNED &&
	       imported->source_handle != 0 &&
	       imported->provenance_labels != 0 &&
	       (imported->provenance_labels & ~AGENT_PROVENANCE_ALL) == 0 &&
	       imported->producer_context_sequence != 0 &&
	       (imported->producer_node_id <
		AGENT_EXECUTION_CONTRACT_MAX_NODES ||
		imported->producer_node_id == AGENT_EXECUTION_NODE_NONE) &&
	       agent_task_schema_present(imported->content_digest);
}

static int
agent_task_resource_view_locked(
	struct agent_task_resource_page *resources,
	struct agent_task_resource_handle handle,
	struct agent_task_resource_view *view)
{
	struct agent_task_resource_slot *slot;

	memset(view, 0, sizeof(*view));
	if (agent_task_handle_null(handle))
		return 0;
	slot = agent_task_resource_find_locked(resources, handle);
	if (slot == 0 || slot->state != AGENT_TASK_RESOURCE_STATE_LIVE ||
	    slot->owner_request_id != 0)
		return -1;
	view->handle = handle;
	view->source_handle = slot->source_handle;
	view->length = slot->length;
	view->provenance_labels = slot->provenance_labels;
	view->producer_context_sequence = slot->producer_context_sequence;
	view->producer_control_id = slot->producer_control_id;
	memmove(view->content_digest, slot->content_digest,
		AGENT_TASK_CHANNEL_SCHEMA_SIZE);
	view->producer_node_id = slot->producer_node_id;
	view->producer_pid = slot->producer_pid;
	return 0;
}

static int
agent_task_resource_allocate_locked(
	struct agent_task_channel_state *state,
	const struct agent_task_resource_import *imported,
	uint64 owner_ring_generation, uint64 owner_request_id,
	uint64 owner_slot_generation,
	struct agent_task_resource_handle *handle)
{
	struct agent_task_private_page *private = state->private;
	struct agent_task_resource_page *resources = state->resource_private;

	if (!agent_task_resource_import_valid(imported))
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	for (uint i = 0; i < AGENT_TASK_CHANNEL_RESOURCE_CAPACITY; i++) {
		struct agent_task_resource_slot *slot = &resources->resources[i];
		uint64 generation;

		if (slot->state != AGENT_TASK_RESOURCE_STATE_NONE ||
		    state->resource_generation_highwater[i] == ~0ULL)
			continue;
		generation = state->resource_generation_highwater[i] + 1;
		if (generation == 0)
			continue;
		state->resource_generation_highwater[i] = generation;
		memset(slot, 0, sizeof(*slot));
		slot->generation = generation;
		slot->source_handle = imported->source_handle;
		slot->length = imported->length;
		slot->owner_request_id = owner_request_id;
		slot->provenance_labels = imported->provenance_labels;
		slot->producer_context_sequence =
			imported->producer_context_sequence;
		slot->producer_control_id = imported->producer_control_id;
		memmove(slot->content_digest, imported->content_digest,
			AGENT_TASK_CHANNEL_SCHEMA_SIZE);
		slot->owner_ring_generation = owner_ring_generation;
		slot->owner_slot_generation = owner_slot_generation;
		slot->state = AGENT_TASK_RESOURCE_STATE_LIVE;
		slot->producer_node_id = imported->producer_node_id;
		slot->producer_pid = imported->producer_pid;
		slot->type = (ushort)imported->resource_type;
		slot->flags = (ushort)imported->resource_flags;
		private->header.resource_count++;
		handle->slot = i + 1;
		handle->type = (ushort)imported->resource_type;
		handle->flags = (ushort)imported->resource_flags;
		handle->generation = generation;
		return 0;
	}
	return AGENT_TASK_CHANNEL_NO_SPACE;
}

static void
agent_task_release_capture_locked(struct agent_task_private_page *private,
				  struct agent_task_resource_slot *slot,
				  struct agent_task_release *release)
{
	uint64 generation = slot->generation;

	release->valid = 1;
	release->type = slot->type;
	release->flags = slot->flags;
	release->source_handle = slot->source_handle;
	release->length = slot->length;
	memset(slot, 0, sizeof(*slot));
	slot->generation = generation;
	if (private->header.resource_count == 0)
		panic("Task Channel resource count");
	private->header.resource_count--;
}

static void
agent_task_release_invoke(struct proc *p,
			  const struct agent_task_channel_ops *ops,
			  const struct agent_task_release *release)
{
	if (release->valid && (ops == 0 || ops->resource_release == 0))
		panic("Task Channel missing resource destructor");
	if (release->valid)
		ops->resource_release(p, release->type, release->flags,
				      release->source_handle, release->length);
}

static void
agent_task_callback_get_locked(struct agent_task_channel_state *state)
{
	if (state == 0 || state->private == 0 ||
	    state->private->header.callback_refs == (uint)-1)
		panic("Task Channel callback get");
	state->private->header.callback_refs++;
	agent_task_channel_publish_locked(state);
}

static void
agent_task_callback_put_locked(struct agent_task_channel_state *state)
{
	if (state == 0 || state->private == 0 ||
	    state->private->header.callback_refs == 0)
		panic("Task Channel callback put");
	state->private->header.callback_refs--;
	agent_task_channel_publish_locked(state);
}

static void
agent_task_resource_op_finish_locked(struct agent_task_channel_state *state)
{
	agent_task_callback_put_locked(state);
	workflow_lifecycle_operation_leave(state->lifecycle);
}

static void
agent_task_reclaim_deferred(struct proc *p,
			    const struct agent_task_channel_ops *ops)
{
	struct agent_task_channel_state *state;
	int ready;
	int enabled = intr_save();

	state = agent_task_channel_find_locked(p);
	ready = agent_task_channel_owner_valid_locked(state, p) &&
		state->state == AGENT_TASK_CHANNEL_OWNER_RECLAIMING &&
		state->ops == ops && state->private->header.callback_refs == 0;
	intr_restore(enabled);
	if (ready)
		(void)agent_task_channel_reclaim(p, ops);
}

static void
agent_task_validation_abort(struct proc *p,
			    const struct agent_task_channel_ops *ops)
{
	int enabled = intr_save();
	struct agent_task_channel_state *state =
		agent_task_channel_find_locked(p);

	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->ops != ops || state->private->header.callback_refs == 0)
		panic("Task Channel validation abort");
	agent_task_callback_put_locked(state);
	workflow_lifecycle_operation_leave(state->lifecycle);
	intr_restore(enabled);
	agent_task_reclaim_deferred(p, ops);
}

static int
agent_task_request_input_acquire_locked(
	struct agent_task_private_page *private,
	struct agent_task_resource_page *resources,
	struct agent_task_request_slot *request)
{
	struct agent_task_resource_slot *resource;
	struct agent_task_resource_handle handle = request->sqe.input;

	if (agent_task_handle_null(handle))
		return 0;
	resource = agent_task_resource_find_locked(resources, handle);
	if (resource == 0 || resource->state != AGENT_TASK_RESOURCE_STATE_LIVE)
		return -1;
	if (handle.flags == AGENT_TASK_HANDLE_F_BORROWED) {
		if (resource->references == (uint)-1 ||
		    resource->owner_request_id != 0)
			return -1;
		resource->references++;
	} else {
		if (resource->references != 0 || resource->owner_request_id != 0)
			return -1;
		resource->state = AGENT_TASK_RESOURCE_STATE_IN_FLIGHT;
		resource->owner_request_id = request->sqe.request_id;
	}
	request->flags |= AGENT_TASK_REQUEST_F_INPUT_HELD;
	return 0;
}

static int
agent_task_completion_valid_locked(
	struct agent_task_private_page *private,
	struct agent_task_resource_page *resources,
	const struct agent_task_request_slot *request,
	const struct agent_task_completion *completion)
{
	struct agent_task_resource_slot *result;
	int cancelled;
	int deadline;
	int denied;
	int link_failed;
	int dependency_reason;
	int dependency_failed;
	int linked_dependency_failed;
	int cancel_due;
	int deadline_due;
	int cached;
	int exact_published;
	int returned_owned_input;

	if (completion == 0 ||
	    (completion->internal_flags &
	     ~AGENT_TASK_COMPLETION_INTERNAL_F_ALL) != 0 ||
	    (completion->flags & ~AGENT_TASK_CQE_F_ALL) != 0 ||
	    completion->context_sequence == 0 ||
	    completion->evidence_ticket == 0 ||
	    completion->provenance_labels == 0 ||
	    (completion->provenance_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    !agent_task_handle_shape_valid(completion->result))
		return 0;
	cancelled = (completion->flags & AGENT_TASK_CQE_F_CANCELLED) != 0;
	deadline = (completion->flags & AGENT_TASK_CQE_F_DEADLINE) != 0;
	denied = (completion->flags & AGENT_TASK_CQE_F_DENIED) != 0;
	cached = (completion->internal_flags &
		  AGENT_TASK_COMPLETION_INTERNAL_F_CACHED) != 0;
	link_failed =
		(completion->flags & AGENT_TASK_CQE_F_LINK_FAILED) != 0;
	dependency_reason = completion->decision_reason ==
		AGENT_EXECUTION_REASON_DEPENDENCY_FAILED;
	dependency_failed =
		completion->status == AGENT_STATUS_CANCELLED &&
		dependency_reason;
	linked_dependency_failed = dependency_failed &&
		(request->sqe.flags & AGENT_TASK_SQE_F_LINK) != 0;
	if (cancelled != (completion->status == AGENT_STATUS_CANCELLED) ||
	    deadline != (completion->status == AGENT_STATUS_TIMEOUT) ||
	    denied != (completion->status == AGENT_STATUS_DENIED) ||
	    (cancelled && deadline) ||
	    dependency_reason != dependency_failed ||
	    link_failed != linked_dependency_failed ||
	    completion->terminal_tick > completion->completion_tick ||
	    (!cached && completion->terminal_tick < request->accepted_tick))
		return 0;
	deadline_due =
		(request->sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
		completion->terminal_tick >= request->sqe.deadline_tick &&
		completion->status != AGENT_STATUS_DENIED &&
		completion->status != AGENT_STATUS_STALE;
	cancel_due =
		((request->flags & AGENT_TASK_REQUEST_F_CANCEL_REQUESTED) != 0 ||
		 dependency_failed) &&
		!deadline_due;
	/* A due hard deadline wins over a still-pending cancellation. */
	if (deadline != deadline_due || cancelled != cancel_due)
		return 0;
	if (completion->status == AGENT_STATUS_OK) {
		if (request->expected_output_type == AGENT_ARTIFACT_NONE) {
			if (!agent_task_handle_null(completion->result))
				return 0;
		} else if (agent_task_handle_null(completion->result) ||
			   completion->result.type !=
				   request->expected_output_type ||
			   completion->provenance_labels !=
				   request->provenance_labels) {
			return 0;
		}
	} else if (!agent_task_handle_null(completion->result)) {
		return 0;
	}
	if (agent_task_handle_null(completion->result))
		return 1;
	if (completion->result.flags != AGENT_TASK_HANDLE_F_OWNED)
		return 0;
	exact_published =
		!agent_task_handle_null(request->result) &&
		completion->result.slot == request->result.slot &&
		completion->result.generation == request->result.generation &&
		completion->result.type == request->result.type &&
		completion->result.flags == request->result.flags;
	returned_owned_input =
		request->sqe.input.flags == AGENT_TASK_HANDLE_F_OWNED &&
		completion->result.slot == request->sqe.input.slot &&
		completion->result.generation == request->sqe.input.generation &&
		completion->result.type == request->sqe.input.type &&
		completion->result.flags == request->sqe.input.flags;
	if (!exact_published && !returned_owned_input)
		return 0;
	result = agent_task_resource_find_locked(resources, completion->result);
	if (result == 0)
		return 0;
	if (exact_published)
		return result->state == AGENT_TASK_RESOURCE_STATE_LIVE &&
		       result->owner_request_id == request->sqe.request_id &&
		       result->owner_ring_generation ==
			       request->sqe.ring_generation &&
		       result->owner_slot_generation ==
			       request->sqe.slot_generation &&
		       result->producer_node_id == request->sqe.node_id &&
		       result->producer_context_sequence ==
			       completion->context_sequence &&
		       result->provenance_labels ==
			       completion->provenance_labels;
	return result->state == AGENT_TASK_RESOURCE_STATE_IN_FLIGHT &&
	       result->owner_request_id == request->sqe.request_id &&
	       result->provenance_labels == completion->provenance_labels;
}

static int
agent_task_request_complete_locked(
	struct agent_task_private_page *private,
	struct agent_task_resource_page *resources,
	struct agent_task_request_slot *request,
	const struct agent_task_completion *completion,
	struct agent_task_release releases[2], uint *release_count)
{
	struct agent_task_resource_slot *input;
	struct agent_task_resource_slot *published;
	struct agent_task_resource_handle published_handle;
	int returns_owned_input;

	if (request->state != AGENT_TASK_REQUEST_RUNNING ||
	    !agent_task_completion_valid_locked(
		private, resources, request, completion))
		return AGENT_TASK_CHANNEL_EVIDENCE;
	published_handle = request->result;
	request->state = AGENT_TASK_REQUEST_EVIDENCE_PENDING;
	request->status = completion->status;
	request->decision_reason = completion->decision_reason;
	request->cqe_flags = completion->flags;
	request->result = completion->result;
	request->context_sequence = completion->context_sequence;
	request->evidence_ticket = completion->evidence_ticket;
	request->provenance_labels = completion->provenance_labels;
	request->completion_tick = completion->completion_tick != 0 ?
					   completion->completion_tick :
					   agent_task_now();
	returns_owned_input =
		request->sqe.input.flags == AGENT_TASK_HANDLE_F_OWNED &&
		completion->result.slot == request->sqe.input.slot &&
		completion->result.generation == request->sqe.input.generation &&
		completion->result.type == request->sqe.input.type &&
		completion->result.flags == AGENT_TASK_HANDLE_F_OWNED;
	if ((request->flags & AGENT_TASK_REQUEST_F_INPUT_HELD) != 0) {
		input = agent_task_resource_find_locked(resources,
						request->sqe.input);
		if (input == 0)
			panic("Task Channel terminal input");
		if (request->sqe.input.flags == AGENT_TASK_HANDLE_F_BORROWED) {
			if (input->references == 0)
				panic("Task Channel terminal borrow");
			input->references--;
		} else if (returns_owned_input) {
			input->state = AGENT_TASK_RESOURCE_STATE_LIVE;
			input->owner_request_id = 0;
		} else {
			if (*release_count >= 2)
				panic("Task Channel terminal release count");
			agent_task_release_capture_locked(
				private, input, &releases[(*release_count)++]);
		}
		request->flags &= ~AGENT_TASK_REQUEST_F_INPUT_HELD;
	}
	if (!agent_task_handle_null(published_handle)) {
		published = agent_task_resource_find_locked(
			resources, published_handle);
		if (published == 0 || published->state !=
		    AGENT_TASK_RESOURCE_STATE_LIVE ||
		    published->owner_request_id != request->sqe.request_id)
			panic("Task Channel published result");
		if (completion->result.slot == published_handle.slot &&
		    completion->result.generation == published_handle.generation &&
		    completion->result.type == published_handle.type &&
		    completion->result.flags == published_handle.flags) {
			/* CQ acknowledgement performs the ownership handoff. */
		} else {
			if (*release_count >= 2)
				panic("Task Channel output release count");
			agent_task_release_capture_locked(
				private, published, &releases[(*release_count)++]);
		}
	}
	if (!agent_task_handle_null(completion->result)) {
		published = agent_task_resource_find_locked(
			resources, completion->result);
		if (published == 0 || published->state !=
		    AGENT_TASK_RESOURCE_STATE_LIVE ||
		    (published->owner_request_id != 0 &&
		     published->owner_request_id != request->sqe.request_id))
			panic("Task Channel result ownership");
		published->owner_request_id = request->sqe.request_id;
	}
	return 0;
}

static int
agent_task_sqe_shape_valid_locked(struct proc *p,
				  struct agent_task_private_page *private,
				  const struct agent_task_sqe *sqe,
				  uint64 position)
{
	uint64 slot_generation;
	uint allowed_flags = AGENT_TASK_SQE_F_ALL;

	if (position / AGENT_TASK_CHANNEL_CAPACITY == ~0ULL)
		return 0;
	slot_generation =
		position / AGENT_TASK_CHANNEL_CAPACITY + 1;
	if (sqe->version != AGENT_TASK_CHANNEL_ENTRY_VERSION ||
	    sqe->size != sizeof(*sqe) || sqe->request_id == 0 ||
	    sqe->request_id <= private->header.last_accepted_request_id ||
	    sqe->ring_generation != private->header.generation ||
	    sqe->slot_generation != slot_generation ||
	    (sqe->opcode != AGENT_TASK_CHANNEL_OP_SUBMIT &&
	     sqe->opcode != AGENT_TASK_CHANNEL_OP_CANCEL) ||
	    (sqe->flags & ~allowed_flags) != 0 ||
	    (((sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0) !=
	     (sqe->deadline_tick != 0)) ||
	    !agent_task_lifecycle_matches(p, sqe->contract.lifecycle) ||
	    sqe->contract.generation == 0 || sqe->tool_id == 0 ||
	    sqe->tool_id > AGENT_TOOL_COUNT ||
	    !agent_task_handle_shape_valid(sqe->input) ||
	    !agent_task_schema_present(sqe->schema_digest))
		return 0;
	/* Node zero is deliberately valid and is interpreted by the contract. */
	if (sqe->opcode == AGENT_TASK_CHANNEL_OP_SUBMIT) {
		if ((sqe->flags & AGENT_TASK_SQE_F_CANCEL) != 0)
			return 0;
		if ((sqe->flags & AGENT_TASK_SQE_F_LINK) != 0)
			return sqe->link_request_id != 0 &&
			       sqe->link_request_id != sqe->request_id &&
			       agent_task_request_find_locked(
				       private, sqe->link_request_id) != 0;
		return sqe->link_request_id == 0;
	}
	if (sqe->opcode != AGENT_TASK_CHANNEL_OP_CANCEL ||
	    sqe->flags != AGENT_TASK_SQE_F_CANCEL ||
	    sqe->link_request_id == 0 ||
	    sqe->link_request_id == sqe->request_id ||
	    !agent_task_handle_null(sqe->input))
		return 0;
	return 1;
}

static void
agent_task_cqe_build(const struct agent_task_request_slot *request,
		     struct agent_task_cqe *cqe)
{
	memset(cqe, 0, sizeof(*cqe));
	cqe->version = AGENT_TASK_CHANNEL_ENTRY_VERSION;
	cqe->size = sizeof(*cqe);
	cqe->flags = request->cqe_flags;
	cqe->status = request->status;
	cqe->decision_reason = request->decision_reason;
	cqe->request_id = request->sqe.request_id;
	cqe->ring_generation = request->sqe.ring_generation;
	cqe->slot_generation = request->sqe.slot_generation;
	cqe->contract = request->sqe.contract;
	cqe->node_id = request->sqe.node_id;
	cqe->attempt_id = request->sqe.attempt_id;
	cqe->tool_id = request->sqe.tool_id;
	cqe->result = request->result;
	cqe->context_sequence = request->context_sequence;
	cqe->evidence_ticket = request->evidence_ticket;
	cqe->provenance_labels = request->provenance_labels;
	cqe->completion_tick = request->completion_tick;
}

static uint
agent_task_channel_flush_locked(struct agent_task_channel_state *state)
{
	struct agent_task_private_page *private = state->private;
	uint flushed = 0;

	while (private->header.terminal_pending != 0) {
		struct agent_task_request_slot *request = 0;
		struct agent_task_cqe cqe;
		uint64 position;

		if (private->header.cq_tail - private->header.cq_head >=
		    AGENT_TASK_CHANNEL_CAPACITY) {
			private->header.flags |= AGENT_TASK_CHANNEL_RING_F_CQ_FULL;
			private->header.backpressure++;
			break;
		}
		for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++)
			if (private->requests[i].state ==
			    AGENT_TASK_REQUEST_TERMINAL) {
				request = &private->requests[i];
				break;
			}
		if (request == 0)
			panic("Task Channel terminal count");
		position = private->header.cq_tail;
		agent_task_cqe_build(request, &cqe);
		memmove(&state->cq->entries[position %
					   AGENT_TASK_CHANNEL_CAPACITY],
			&cqe, sizeof(cqe));
		__sync_synchronize();
		request->cq_position = position;
		request->state = AGENT_TASK_REQUEST_CQ_VISIBLE;
		private->header.cq_tail++;
		private->header.terminal_pending--;
		flushed++;
	}
	if (private->header.cq_tail - private->header.cq_head ==
	    AGENT_TASK_CHANNEL_CAPACITY)
		private->header.flags |= AGENT_TASK_CHANNEL_RING_F_CQ_FULL;
	else
		private->header.flags &= ~AGENT_TASK_CHANNEL_RING_F_CQ_FULL;
	return flushed;
}

static int
agent_task_channel_ack_locked(struct agent_task_channel_state *state,
			      uint64 acknowledged)
{
	struct agent_task_private_page *private = state->private;

	if (acknowledged < private->header.cq_head ||
	    acknowledged > private->header.cq_tail ||
	    acknowledged - private->header.cq_head >
		    AGENT_TASK_CHANNEL_CAPACITY)
		return agent_task_channel_protocol_fault_locked(state);
	for (uint64 position = private->header.cq_head;
	     position < acknowledged; position++) {
		uint found = 0;

		for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
			struct agent_task_request_slot *request =
				&private->requests[i];

			if (request->state == AGENT_TASK_REQUEST_CQ_VISIBLE &&
			    request->cq_position == position) {
				found++;
				break;
			}
		}
		if (found != 1)
			return agent_task_channel_protocol_fault_locked(state);
	}
	for (uint64 position = private->header.cq_head;
	     position < acknowledged; position++) {
		for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
			struct agent_task_request_slot *request =
				&private->requests[i];

			if (request->state != AGENT_TASK_REQUEST_CQ_VISIBLE ||
			    request->cq_position != position)
				continue;
			if (!agent_task_handle_null(request->result)) {
				struct agent_task_resource_slot *result =
					agent_task_resource_find_locked(
						state->resource_private,
						request->result);

				if (result == 0 || result->owner_request_id !=
				    request->sqe.request_id)
					panic("Task Channel CQ result ownership");
				result->owner_request_id = 0;
			}
			memset(request, 0, sizeof(*request));
			memset(&state->cq->entries[position %
						 AGENT_TASK_CHANNEL_CAPACITY],
			       0, sizeof(struct agent_task_cqe));
			if (private->header.live_requests == 0)
				panic("Task Channel request count");
			private->header.live_requests--;
			break;
		}
	}
	private->header.cq_head = acknowledged;
	return 0;
}

static void
agent_task_channel_setup_result_fill(
	const struct proc *p, const struct agent_task_channel_state *state,
	struct agent_task_channel_setup_result *result, int status)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	if (state == 0 || state->private == 0 || p == 0)
		return;
	result->flags = state->private->header.flags;
	result->lifecycle.id = p->workflow_lifecycle_id;
	result->lifecycle.generation = p->workflow_lifecycle_generation;
	result->generation = state->private->header.generation;
	result->sq_base = AGENT_TASK_CHANNEL_SQ_BASE;
	result->cq_base = AGENT_TASK_CHANNEL_CQ_BASE;
	result->sq_capacity = AGENT_TASK_CHANNEL_CAPACITY;
	result->cq_capacity = AGENT_TASK_CHANNEL_CAPACITY;
	result->sqe_size = sizeof(struct agent_task_sqe);
	result->cqe_size = sizeof(struct agent_task_cqe);
	result->mapped_page_count = AGENT_TASK_CHANNEL_MAPPED_PAGES;
	result->private_page_count = AGENT_TASK_CHANNEL_PRIVATE_PAGES;
}

static void
agent_task_channel_enter_result_fill(
	const struct agent_task_channel_state *state,
	struct agent_task_channel_enter_result *result, int status,
	uint submitted, uint completed)
{
	const struct agent_task_private_page *private = state->private;
	uint in_flight = 0;

	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	result->flags = private->header.flags;
	result->generation = private->header.generation;
	result->sq_head = private->header.sq_head;
	result->cq_head = private->header.cq_head;
	result->cq_tail = private->header.cq_tail;
	result->submitted = submitted;
	result->completed = completed;
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++)
		if (private->requests[i].state == AGENT_TASK_REQUEST_ACCEPTED ||
		    private->requests[i].state == AGENT_TASK_REQUEST_RUNNING ||
		    private->requests[i].state ==
			    AGENT_TASK_REQUEST_EVIDENCE_PENDING)
			in_flight++;
	result->in_flight = in_flight;
	result->terminal_pending = private->header.terminal_pending;
	result->resource_count = private->header.resource_count;
	result->protocol_faults = private->header.protocol_faults;
	result->resync_count = private->header.resync_count;
	result->backpressure = private->header.backpressure;
	result->last_accepted_request_id =
		private->header.last_accepted_request_id;
}

static int
agent_task_channel_addresses_available(struct proc *p)
{
	pte_t *sq;
	pte_t *cq;

	if (p == 0 || p->pagetable == 0 ||
	    p->max_page > AGENT_TASK_CHANNEL_SQ_BASE / PAGE_SIZE)
		return 0;
	sq = walk(p->pagetable, AGENT_TASK_CHANNEL_SQ_BASE, 0);
	cq = walk(p->pagetable, AGENT_TASK_CHANNEL_CQ_BASE, 0);
	return (sq == 0 || (*sq & PTE_V) == 0) &&
	       (cq == 0 || (*cq & PTE_V) == 0);
}

void
agent_task_channel_init(void)
{
	memset(agent_task_channels, 0, sizeof(agent_task_channels));
	for (uint i = 0; i < NPROC; i++) {
		agent_task_channels[i].lifecycle = workflow_lifecycle_none();
		agent_task_channels[i].account = resource_account_none();
		agent_task_channels[i].charge_class =
			RESOURCE_CHARGE_CLASS_COUNT;
	}
}

int
agent_task_channel_setup(struct proc *p, struct thread *issuer,
			 const struct agent_task_channel_setup *setup,
			 struct agent_task_channel_setup_result *result,
			 const struct agent_task_channel_ops *ops)
{
	struct agent_task_channel_state *state;
	struct resource_reservation reservation;
	struct resource_request request = {
		.kind = RESOURCE_AGENT_STATE_PAGE,
		.amount = AGENT_TASK_CHANNEL_STATE_PAGES,
	};
	struct resource_account_handle account;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	enum resource_charge_class charge_class;
	void *pages[AGENT_TASK_CHANNEL_STATE_PAGES] = { 0 };
	uint allocated = 0;
	uint mapped = 0;
	uint64 generation = 0;
	int pid;
	int enabled;
	int lifecycle_held = 0;

	if (result == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	agent_task_channel_setup_result_fill(p, 0, result,
					     AGENT_TASK_CHANNEL_BAD_REQUEST);
	if (p == 0 || issuer == 0 || setup == 0 || ops == 0 ||
	    ops->validate == 0 || ops->submit == 0 || ops->cancel == 0 ||
	    ops->expire == 0 || ops->resource_release == 0 ||
	    setup->version != AGENT_TASK_CHANNEL_VERSION ||
	    setup->size != sizeof(*setup) ||
	    setup->flags != AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER ||
	    setup->reserved != 0 || setup->reserved_tail[0] != 0 ||
	    setup->reserved_tail[1] != 0 || setup->reserved_tail[2] != 0 ||
	    setup->reserved_tail[3] != 0 || issuer != curr_thread() ||
	    issuer != &p->threads[0] || issuer->process != p ||
	    issuer->identity_generation == 0 ||
	    !agent_task_lifecycle_matches(p, setup->lifecycle))
		return result->status;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (state != 0 &&
	    state->ops == ops &&
	    agent_task_channel_issuer_valid_locked(state, p, issuer)) {
		agent_task_channel_setup_result_fill(p, state, result,
						     AGENT_TASK_CHANNEL_OK);
		intr_restore(enabled);
		return result->status;
	}
	if (!proc_teardown_live(p) || !p->is_agent || state != 0 ||
	    !resource_account_active(p->resource_account) ||
	    !workflow_lifecycle_active(agent_task_lifecycle_key(p)) ||
	    !agent_task_channel_addresses_available(p)) {
		intr_restore(enabled);
		return result->status;
	}
	lifecycle = agent_task_lifecycle_key(p);
	if (workflow_lifecycle_operation_enter(lifecycle) < 0) {
		intr_restore(enabled);
		return result->status;
	}
	lifecycle_held = 1;
	state = agent_task_channel_alloc_locked(p, &generation);
	if (state == 0) {
		workflow_lifecycle_operation_leave(lifecycle);
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_NO_SPACE;
		return result->status;
	}
	pid = p->pid;
	account = p->resource_account;
	charge_class = p->resource_slot_reserved ?
			       RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY;
	memset(&reservation, 0, sizeof(reservation));
	if (resource_reserve_many(account, charge_class, &request, 1,
				  &reservation) < 0) {
		agent_task_channel_reset_locked(state);
		workflow_lifecycle_operation_leave(lifecycle);
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_NO_SPACE;
		return result->status;
	}
	intr_restore(enabled);
	while (allocated < AGENT_TASK_CHANNEL_STATE_PAGES) {
		pages[allocated] = kalloc_account_page(account, charge_class);
		if (pages[allocated] == 0)
			goto fail;
		memset(pages[allocated], 0, PAGE_SIZE);
		allocated++;
	}
	if (mappages(p->pagetable, AGENT_TASK_CHANNEL_SQ_BASE, PAGE_SIZE,
		     (uint64)pages[0], PTE_U | PTE_R | PTE_W) < 0)
		goto fail;
	mapped = 1;
	if (mappages(p->pagetable, AGENT_TASK_CHANNEL_CQ_BASE, PAGE_SIZE,
		     (uint64)pages[1], PTE_U | PTE_R) < 0)
		goto fail;
	mapped = 2;
	enabled = intr_save();
	if (!proc_teardown_live(p) || p->pid != pid ||
	    state->state != AGENT_TASK_CHANNEL_OWNER_SETTING_UP ||
	    state->owner != p || !agent_task_lifecycle_matches(p,
							 setup->lifecycle) ||
	    !workflow_lifecycle_active(lifecycle) ||
	    !resource_account_handle_equal(p->resource_account, account) ||
	    (p->resource_slot_reserved != 0) !=
		    (charge_class == RESOURCE_CHARGE_RESERVED) ||
	    resource_reservation_commit(&reservation) < 0) {
		intr_restore(enabled);
		goto fail;
	}
	state->account = account;
	state->lifecycle = lifecycle;
	state->ops = ops;
	state->charge_class = charge_class;
	state->sq = pages[0];
	state->cq = pages[1];
	state->private = pages[2];
	state->resource_private = pages[3];
	state->private->header.generation = generation;
	state->private->header.issuer_generation = issuer->identity_generation;
	state->private->header.issuer_tid = issuer->tid;
	state->private->header.flags = AGENT_TASK_CHANNEL_RING_F_ACTIVE;
	state->private->header.mapped_page_count =
		AGENT_TASK_CHANNEL_MAPPED_PAGES;
	state->state = AGENT_TASK_CHANNEL_OWNER_LIVE;
	agent_task_channel_publish_locked(state);
	agent_task_channel_setup_result_fill(p, state, result,
					     AGENT_TASK_CHANNEL_OK);
	workflow_lifecycle_operation_leave(lifecycle);
	lifecycle_held = 0;
	intr_restore(enabled);
	return result->status;

fail:
	if (mapped != 0 && p->pagetable != 0)
		uvmunmap(p->pagetable, AGENT_TASK_CHANNEL_SQ_BASE, mapped, 0);
	while (allocated > 0)
		(void)kfree_account_page(pages[--allocated], account,
					 charge_class);
	resource_reservation_cancel(&reservation);
	enabled = intr_save();
	if (state->owner == p &&
	    state->state == AGENT_TASK_CHANNEL_OWNER_SETTING_UP)
		agent_task_channel_reset_locked(state);
	if (lifecycle_held)
		workflow_lifecycle_operation_leave(lifecycle);
	intr_restore(enabled);
	result->status = AGENT_TASK_CHANNEL_NO_SPACE;
	return result->status;
}

static int
agent_task_channel_complete_finish(
	struct proc *p, uint64 ring_generation, uint64 request_id,
	uint64 slot_generation, const struct agent_task_completion *completion,
	const struct agent_task_channel_ops *ops)
{
	struct agent_task_channel_state *state;
	struct agent_task_request_slot *request;
	struct agent_task_release releases[2];
	uint release_count = 0;
	int status;
	int enabled;

	memset(releases, 0, sizeof(releases));
	if (p == 0 || ring_generation == 0 || request_id == 0 ||
	    slot_generation == 0 ||
	    completion == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->ops != ops) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	request = agent_task_request_find_locked(state->private, request_id);
	if (request == 0 ||
	    request->sqe.ring_generation != ring_generation ||
	    request->sqe.slot_generation != slot_generation ||
	    request->state != AGENT_TASK_REQUEST_RUNNING) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	status = agent_task_request_complete_locked(
		state->private, state->resource_private, request, completion,
		releases, &release_count);
	if (status == AGENT_TASK_CHANNEL_OK)
		agent_task_callback_get_locked(state);
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	if (status < 0)
		return status;
	for (uint i = 0; i < release_count; i++)
		agent_task_release_invoke(p, ops, &releases[i]);
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->ops != ops)
		panic("Task Channel completion cleanup");
	request = agent_task_request_find_locked(state->private, request_id);
	if (request == 0 ||
	    request->sqe.ring_generation != ring_generation ||
	    request->sqe.slot_generation != slot_generation ||
	    request->state != AGENT_TASK_REQUEST_EVIDENCE_PENDING ||
	    (request->flags & AGENT_TASK_REQUEST_F_LIFECYCLE_HELD) == 0)
		panic("Task Channel completion finalization");
	agent_task_callback_put_locked(state);
	request->flags &= ~AGENT_TASK_REQUEST_F_LIFECYCLE_HELD;
	workflow_lifecycle_operation_leave(state->lifecycle);
	request->state = AGENT_TASK_REQUEST_TERMINAL;
	state->private->header.terminal_pending++;
	state->private->header.completed++;
	(void)agent_task_channel_flush_locked(state);
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	return AGENT_TASK_CHANNEL_OK;
}

int
agent_task_channel_complete(struct proc *p, uint64 ring_generation,
			    uint64 request_id, uint64 slot_generation,
			    const struct agent_task_completion *completion,
			    const struct agent_task_channel_ops *ops)
{
	int status = agent_task_channel_complete_finish(
		p, ring_generation, request_id, slot_generation, completion, ops);

	if (status == AGENT_TASK_CHANNEL_OK)
		agent_task_reclaim_deferred(p, ops);
	return status;
}

static uint
agent_task_deadlines_mark_locked(struct agent_task_channel_state *state,
				 uint64 now, uint *newly_due)
{
	uint due = 0;
	uint marked = 0;

	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
		struct agent_task_request_slot *request =
			&state->private->requests[i];

		if (request->state != AGENT_TASK_REQUEST_RUNNING ||
		    (request->sqe.flags &
		     AGENT_TASK_SQE_F_HARD_DEADLINE) == 0)
			continue;
		if (now >= request->sqe.deadline_tick &&
		    (request->flags &
		     AGENT_TASK_REQUEST_F_DEADLINE_DUE) == 0) {
			request->flags |= AGENT_TASK_REQUEST_F_DEADLINE_DUE;
			marked++;
		}
		if ((request->flags & AGENT_TASK_REQUEST_F_DEADLINE_DUE) != 0)
			due++;
	}
	if (newly_due != 0)
		*newly_due = marked;
	if (due != 0)
		state->private->header.flags |=
			AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE;
	else
		state->private->header.flags &=
			~AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE;
	return due;
}

/* IRQ-safe: mark due work only; callbacks run later from expire/enter. */
uint
agent_task_channel_tick(uint64 now)
{
	uint due = 0;
	int enabled;

	if (now == 0)
		return 0;
	enabled = intr_save();
	for (uint i = 0; i < NPROC; i++) {
		struct agent_task_channel_state *state = &agent_task_channels[i];
		struct thread *issuer;
		uint newly_due = 0;

		if ((state->state != AGENT_TASK_CHANNEL_OWNER_LIVE &&
		     state->state != AGENT_TASK_CHANNEL_OWNER_RECLAIMING) ||
		    state->private == 0)
			continue;
		due += agent_task_deadlines_mark_locked(
			state, now, &newly_due);
		agent_task_channel_publish_locked(state);
		if (newly_due == 0 || state->owner == 0)
			continue;
		issuer = &state->owner->threads[0];
		if (issuer->process == state->owner &&
		    issuer->tid == state->private->header.issuer_tid &&
		    issuer->identity_generation != 0 &&
		    issuer->identity_generation ==
			    state->private->header.issuer_generation)
			(void)wait_queue_interrupt(issuer);
	}
	intr_restore(enabled);
	return due;
}

int
agent_task_channel_deadline_due(const struct proc *p)
{
	struct agent_task_channel_state *state;
	int due = 0;
	int enabled = intr_save();

	state = agent_task_channel_find_locked(p);
	if (agent_task_channel_owner_valid_locked(state, p) &&
	    (state->state == AGENT_TASK_CHANNEL_OWNER_LIVE ||
	     state->state == AGENT_TASK_CHANNEL_OWNER_RECLAIMING) &&
	    (state->private->header.flags &
	     AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE) != 0)
		due = 1;
	intr_restore(enabled);
	return due;
}

int
agent_task_channel_expire(struct proc *p, uint64 now,
			  const struct agent_task_channel_ops *ops)
{
	uint expired = 0;

	if (p == 0 || now == 0 || ops == 0 || ops->expire == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	for (;;) {
		struct agent_task_channel_state *state;
		struct agent_task_request_slot *request = 0;
		struct agent_task_completion completion;
		struct agent_task_sqe sqe;
		uint64 current_now = agent_task_now();
		int status;
		int enabled = intr_save();

		if (current_now > now)
			now = current_now;

		state = agent_task_channel_find_locked(p);
		if (!agent_task_channel_owner_valid_locked(state, p) ||
		    state->ops != ops) {
			intr_restore(enabled);
			return expired != 0 ? (int)expired :
			       AGENT_TASK_CHANNEL_STALE;
		}
		(void)agent_task_deadlines_mark_locked(state, now, 0);
		for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
			struct agent_task_request_slot *candidate =
				&state->private->requests[i];

			if (candidate->state == AGENT_TASK_REQUEST_RUNNING &&
			    (candidate->sqe.flags &
			     AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
			    (candidate->flags &
			     AGENT_TASK_REQUEST_F_DEADLINE_DUE) != 0) {
				request = candidate;
				break;
			}
		}
		if (request == 0) {
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return (int)expired;
		}
		sqe = request->sqe;
		agent_task_callback_get_locked(state);
		intr_restore(enabled);
		status = agent_task_deadline_completion(
			p, ops, &sqe, &completion);
		enabled = intr_save();
		state = agent_task_channel_find_locked(p);
		if (!agent_task_channel_owner_valid_locked(state, p) ||
		    state->ops != ops || state->private->header.callback_refs == 0)
			panic("Task Channel expiry callback");
		agent_task_callback_put_locked(state);
		intr_restore(enabled);
		agent_task_reclaim_deferred(p, ops);
		if (status < 0)
			return expired != 0 ? (int)expired :
			       AGENT_TASK_CHANNEL_EVIDENCE;
		status = agent_task_channel_complete(
			p, sqe.ring_generation, sqe.request_id,
			sqe.slot_generation, &completion, ops);
		if (status == AGENT_TASK_CHANNEL_OK) {
			expired++;
			continue;
		}
		if (status == AGENT_TASK_CHANNEL_STALE)
			continue;
		return expired != 0 ? (int)expired : status;
	}
}

static int
agent_task_cancel_descriptor_matches(
	const struct agent_task_sqe *cancel,
	const struct agent_task_request_slot *target)
{
	return agent_task_contract_equal(cancel->contract,
				 target->sqe.contract) &&
	       cancel->node_id == target->sqe.node_id &&
	       cancel->attempt_id == target->sqe.attempt_id &&
	       cancel->tool_id == target->sqe.tool_id &&
	       memcmp(cancel->schema_digest, target->sqe.schema_digest,
		      AGENT_TASK_CHANNEL_SCHEMA_SIZE) == 0;
}

static int
agent_task_deadline_completion(
	struct proc *p, const struct agent_task_channel_ops *ops,
	const struct agent_task_sqe *sqe,
	struct agent_task_completion *completion)
{
	int hook_status;

	if (ops == 0 || ops->expire == 0)
		return AGENT_TASK_CHANNEL_EVIDENCE;
	memset(completion, 0, sizeof(*completion));
	hook_status = ops->expire(p, sqe, completion);
	if ((hook_status != AGENT_TASK_HOOK_COMPLETE &&
	     hook_status != AGENT_TASK_HOOK_DENIED) ||
	    completion->status != AGENT_STATUS_TIMEOUT ||
	    (completion->flags & AGENT_TASK_CQE_F_DEADLINE) == 0)
		return AGENT_TASK_CHANNEL_EVIDENCE;
	return 0;
}

/* Reports consumption separately so a consumed command can still return error. */
static int
agent_task_channel_consume_one(struct proc *p, struct thread *issuer,
			       uint64 generation,
			       const struct agent_task_channel_ops *ops,
			       uint *consumed)
{
	struct agent_task_channel_state *state;
	struct agent_task_private_page *private;
	struct agent_task_request_slot *request;
	struct agent_task_request_slot *target;
	struct agent_task_sqe sqe;
	struct agent_task_sqe target_sqe;
	struct agent_task_completion completion;
	struct agent_task_resource_view input_view;
	struct agent_task_validation validation;
	struct workflow_lifecycle_key lifecycle;
	uint64 position;
	uint64 target_ring_generation = 0;
	uint64 target_slot_generation = 0;
	int hook_status = AGENT_TASK_HOOK_PENDING;
	int enabled;
	int status;

	if (consumed == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	*consumed = 0;
	memset(&completion, 0, sizeof(completion));
	memset(&target_sqe, 0, sizeof(target_sqe));
	memset(&input_view, 0, sizeof(input_view));
	memset(&validation, 0, sizeof(validation));
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_issuer_valid_locked(state, p, issuer) ||
	    state->ops != ops ||
	    state->private->header.generation != generation) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	private = state->private;
	if ((private->header.flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) != 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RESYNC_REQUIRED;
	}
	if (private->header.sq_head == private->header.sq_tail) {
		intr_restore(enabled);
		return 0;
	}
	position = private->header.sq_head;
	/* No descriptor field is read again after this complete private copy. */
	memmove(&sqe,
		&state->sq->entries[position % AGENT_TASK_CHANNEL_CAPACITY],
		sizeof(sqe));
	__sync_synchronize();
	if (!agent_task_sqe_shape_valid_locked(p, private, &sqe, position)) {
		status = agent_task_channel_protocol_fault_locked(state);
		intr_restore(enabled);
		return status;
	}
	if (sqe.opcode == AGENT_TASK_CHANNEL_OP_CANCEL) {
		target = agent_task_request_find_locked(private,
						sqe.link_request_id);
		if (target != 0 &&
		    !agent_task_cancel_descriptor_matches(&sqe, target)) {
			status = agent_task_channel_protocol_fault_locked(state);
			intr_restore(enabled);
			return status;
		}
		private->header.sq_head++;
		private->header.submitted++;
		private->header.last_accepted_request_id = sqe.request_id;
		*consumed = 1;
		if (target == 0) {
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_STALE;
		}
		if (target->state == AGENT_TASK_REQUEST_TERMINAL ||
		    target->state == AGENT_TASK_REQUEST_CQ_VISIBLE ||
		    target->state == AGENT_TASK_REQUEST_EVIDENCE_PENDING) {
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return 1;
		}
		if (target->state != AGENT_TASK_REQUEST_RUNNING &&
		    target->state != AGENT_TASK_REQUEST_ACCEPTED) {
			status = agent_task_channel_protocol_fault_locked(state);
			intr_restore(enabled);
			return status;
		}
		if ((target->flags &
		     AGENT_TASK_REQUEST_F_CANCEL_REQUESTED) != 0) {
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return 1;
		}
		target->flags &= ~AGENT_TASK_REQUEST_F_CANCEL_DENIED;
		target->flags |= AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
		target_ring_generation = target->sqe.ring_generation;
		target_slot_generation = target->sqe.slot_generation;
		target_sqe = target->sqe;
		agent_task_callback_get_locked(state);
		agent_task_channel_publish_locked(state);
		intr_restore(enabled);
		hook_status = ops->cancel(p, &sqe, &completion);
		enabled = intr_save();
		state = agent_task_channel_find_locked(p);
		if (!agent_task_channel_owner_valid_locked(state, p) ||
		    state->ops != ops || state->private->header.callback_refs == 0)
			panic("Task Channel cancel callback");
		agent_task_callback_put_locked(state);
		if ((target_sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
		    agent_task_now() >= target_sqe.deadline_tick) {
			target = agent_task_request_find_locked(
				state->private, sqe.link_request_id);
			if (target != 0 && target->state ==
			    AGENT_TASK_REQUEST_RUNNING)
				target->flags |=
					AGENT_TASK_REQUEST_F_DEADLINE_DUE;
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			agent_task_reclaim_deferred(p, ops);
			status = agent_task_channel_expire(
				p, agent_task_now(), ops);
			return agent_task_consumed_after_expire(status);
		}
		if (hook_status == AGENT_TASK_HOOK_DENIED) {
			target = agent_task_request_find_locked(
				state->private, sqe.link_request_id);
			if (target != 0 &&
			    target->sqe.ring_generation == target_ring_generation &&
			    target->sqe.slot_generation == target_slot_generation &&
			    target->state == AGENT_TASK_REQUEST_RUNNING) {
				target->flags &=
					~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
				target->flags |=
					AGENT_TASK_REQUEST_F_CANCEL_DENIED;
			}
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			agent_task_reclaim_deferred(p, ops);
			return AGENT_TASK_CHANNEL_DENIED;
		}
		if (hook_status != AGENT_TASK_HOOK_PENDING &&
		    hook_status != AGENT_TASK_HOOK_COMPLETE) {
			target = agent_task_request_find_locked(
				state->private, sqe.link_request_id);
			if (target != 0 && target->state ==
			    AGENT_TASK_REQUEST_RUNNING) {
				target->flags &=
					~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
				target->flags |=
					AGENT_TASK_REQUEST_F_CANCEL_DENIED;
			}
			agent_task_channel_publish_locked(state);
		}
		intr_restore(enabled);
		agent_task_reclaim_deferred(p, ops);
		if (hook_status == AGENT_TASK_HOOK_COMPLETE) {
			status = agent_task_channel_complete(
				p, target_ring_generation,
				sqe.link_request_id,
				target_slot_generation,
				&completion, ops);
			if (status == AGENT_TASK_CHANNEL_OK ||
			    status == AGENT_TASK_CHANNEL_STALE)
				return 1;
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops)
				panic("Task Channel cancel completion");
			target = agent_task_request_find_locked(
				state->private, sqe.link_request_id);
			if (target != 0 &&
			    target->sqe.ring_generation == target_ring_generation &&
			    target->sqe.slot_generation == target_slot_generation &&
			    target->state == AGENT_TASK_REQUEST_RUNNING) {
				target->flags &=
					~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
				target->flags |=
					AGENT_TASK_REQUEST_F_CANCEL_DENIED;
			}
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			agent_task_reclaim_deferred(p, ops);
			return status;
		}
		if (hook_status != AGENT_TASK_HOOK_PENDING)
			return AGENT_TASK_CHANNEL_EVIDENCE;
		return 1;
	}
	if ((sqe.flags & AGENT_TASK_SQE_F_LINK) != 0) {
		target = agent_task_request_find_locked(
			private, sqe.link_request_id);
		if (target == 0) {
			status = agent_task_channel_protocol_fault_locked(state);
			intr_restore(enabled);
			return status;
		}
		if (target->state == AGENT_TASK_REQUEST_ACCEPTED ||
		    target->state == AGENT_TASK_REQUEST_RUNNING ||
		    target->state == AGENT_TASK_REQUEST_EVIDENCE_PENDING) {
			private->header.backpressure++;
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return 0;
		}
	}
	if (agent_task_request_find_locked(private, sqe.request_id) != 0) {
		status = agent_task_channel_protocol_fault_locked(state);
		intr_restore(enabled);
		return status;
	}
	if (agent_task_resource_view_locked(
		state->resource_private, sqe.input, &input_view) < 0) {
		status = agent_task_channel_protocol_fault_locked(state);
		intr_restore(enabled);
		return status;
	}
	request = agent_task_request_alloc_locked(private);
	if (request == 0) {
		private->header.backpressure++;
		agent_task_channel_publish_locked(state);
		intr_restore(enabled);
		return 0;
	}
	lifecycle = agent_task_lifecycle_key(p);
	if (workflow_lifecycle_operation_enter(lifecycle) < 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	agent_task_callback_get_locked(state);
	intr_restore(enabled);
	if ((sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
	    (ops == 0 || ops->expire == 0)) {
		agent_task_validation_abort(p, ops);
		return AGENT_TASK_CHANNEL_EVIDENCE;
	}
	hook_status = ops->validate(
		p, &sqe, &input_view, &validation, &completion);
	if (hook_status != AGENT_TASK_HOOK_PENDING) {
		agent_task_validation_abort(p, ops);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	if (!agent_task_validation_valid(&validation)) {
		agent_task_validation_abort(p, ops);
		return AGENT_TASK_CHANNEL_EVIDENCE;
	}
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_issuer_valid_locked(state, p, issuer) ||
	    state->ops != ops ||
	    state->private->header.generation != generation ||
	    state->private->header.sq_head != position) {
		if (!agent_task_channel_owner_valid_locked(state, p) ||
		    state->ops != ops ||
		    state->private->header.callback_refs == 0)
			panic("Task Channel validate cleanup");
		agent_task_callback_put_locked(state);
		workflow_lifecycle_operation_leave(state->lifecycle);
		intr_restore(enabled);
		agent_task_reclaim_deferred(p, ops);
		return AGENT_TASK_CHANNEL_STALE;
	}
	private = state->private;
	request = agent_task_request_alloc_locked(private);
	if (request == 0) {
		private->header.backpressure++;
		if (private->header.callback_refs == 0)
			panic("Task Channel validate pin");
		agent_task_callback_put_locked(state);
		workflow_lifecycle_operation_leave(lifecycle);
		intr_restore(enabled);
		return 0;
	}
	memset(request, 0, sizeof(*request));
	request->sqe = sqe;
	request->accepted_tick = agent_task_now();
	request->expected_output_type = validation.output_artifact_type;
	request->provenance_labels = validation.output_provenance_labels;
	request->state = AGENT_TASK_REQUEST_ACCEPTED;
	request->flags = AGENT_TASK_REQUEST_F_LIFECYCLE_HELD;
	if (private->header.callback_refs == 0)
		panic("Task Channel submit transfer");
	agent_task_callback_put_locked(state);
	if (agent_task_request_input_acquire_locked(
		private, state->resource_private, request) < 0) {
		request->flags &= ~AGENT_TASK_REQUEST_F_LIFECYCLE_HELD;
		workflow_lifecycle_operation_leave(lifecycle);
		memset(request, 0, sizeof(*request));
		status = agent_task_channel_protocol_fault_locked(state);
		intr_restore(enabled);
		return status;
	}
	request->state = AGENT_TASK_REQUEST_RUNNING;
	private->header.sq_head++;
	private->header.submitted++;
	private->header.last_accepted_request_id = sqe.request_id;
	*consumed = 1;
	private->header.live_requests++;
	agent_task_callback_get_locked(state);
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	hook_status = ops->submit(
		p, &sqe, &input_view, &validation, &completion);
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->ops != ops || state->private->header.callback_refs == 0)
		panic("Task Channel submit callback");
	agent_task_callback_put_locked(state);
	intr_restore(enabled);
	agent_task_reclaim_deferred(p, ops);
	if (hook_status == AGENT_TASK_HOOK_PENDING &&
	    (sqe.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
	    agent_task_now() >= sqe.deadline_tick) {
		status = agent_task_channel_expire(p, agent_task_now(), ops);
		return agent_task_consumed_after_expire(status);
	}
	if (hook_status == AGENT_TASK_HOOK_COMPLETE ||
	    hook_status == AGENT_TASK_HOOK_DENIED) {
		status = agent_task_channel_complete(
			p, sqe.ring_generation, sqe.request_id,
			sqe.slot_generation, &completion, ops);
		if (status == AGENT_TASK_CHANNEL_OK)
			return 1;
		return status;
	}
	if (hook_status != AGENT_TASK_HOOK_PENDING)
		return AGENT_TASK_CHANNEL_EVIDENCE;
	return 1;
}

int
agent_task_channel_enter(struct proc *p, struct thread *issuer,
			 const struct agent_task_channel_enter *enter,
			 struct agent_task_channel_enter_result *result,
			 const struct agent_task_channel_ops *ops)
{
	struct agent_task_channel_state *state;
	struct agent_task_private_header *header;
	uint submitted = 0;
	uint completed = 0;
	uint limit;
	int status = AGENT_TASK_CHANNEL_OK;
	int enabled;

	if (result == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = AGENT_TASK_CHANNEL_BAD_REQUEST;
	if (p == 0 || issuer == 0 || enter == 0 ||
	    enter->version != AGENT_TASK_CHANNEL_VERSION ||
	    enter->size != sizeof(*enter) ||
	    (enter->flags & ~AGENT_TASK_CHANNEL_ENTER_F_ALL) != 0 ||
	    ((enter->flags & AGENT_TASK_CHANNEL_ENTER_F_DRAIN) != 0 &&
	     enter->max_submit != 0) ||
	    enter->max_submit > AGENT_TASK_CHANNEL_CAPACITY ||
	    enter->min_complete > AGENT_TASK_CHANNEL_CAPACITY ||
	    enter->reserved != 0 || enter->reserved_tail[0] != 0 ||
	    enter->reserved_tail[1] != 0)
		return result->status;
	limit = (enter->flags & AGENT_TASK_CHANNEL_ENTER_F_DRAIN) != 0 ? 0 :
		(enter->max_submit == 0 ? AGENT_TASK_CHANNEL_CAPACITY :
		 enter->max_submit);
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_issuer_valid_locked(state, p, issuer) ||
	    state->ops != ops) {
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_STALE;
		return result->status;
	}
	header = &state->private->header;
	if (enter->generation != header->generation) {
		agent_task_channel_enter_result_fill(
			state, result, AGENT_TASK_CHANNEL_STALE, 0, 0);
		intr_restore(enabled);
		return result->status;
	}
	status = agent_task_channel_ack_locked(state, enter->cq_head);
	if (status < 0) {
		agent_task_channel_enter_result_fill(state, result, status, 0, 0);
		intr_restore(enabled);
		return result->status;
	}
	header = &state->private->header;
	if ((header->flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) != 0) {
		if ((enter->flags & AGENT_TASK_CHANNEL_ENTER_F_RESYNC) == 0 ||
		    enter->sq_tail != 0) {
			agent_task_channel_enter_result_fill(
				state, result,
				AGENT_TASK_CHANNEL_RESYNC_REQUIRED, 0, 0);
			intr_restore(enabled);
			return result->status;
		}
		header->flags &= ~AGENT_TASK_CHANNEL_RING_F_RESYNC;
	} else if ((enter->flags & AGENT_TASK_CHANNEL_ENTER_F_RESYNC) != 0) {
		agent_task_channel_enter_result_fill(
			state, result, AGENT_TASK_CHANNEL_BAD_REQUEST, 0, 0);
		intr_restore(enabled);
		return result->status;
	}
	if (enter->sq_tail < header->sq_tail ||
	    enter->sq_tail < header->sq_head ||
	    enter->sq_tail - header->sq_head > AGENT_TASK_CHANNEL_CAPACITY) {
		status = agent_task_channel_protocol_fault_locked(state);
		agent_task_channel_enter_result_fill(state, result, status, 0, 0);
		intr_restore(enabled);
		return result->status;
	}
	header->sq_tail = enter->sq_tail;
	completed += agent_task_channel_flush_locked(state);
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	if (ops != 0 && ops->expire != 0) {
		status = agent_task_channel_expire(p, agent_task_now(), ops);
		if (status < 0)
			goto finish;
		status = AGENT_TASK_CHANNEL_OK;
	}
	while (submitted < limit) {
		uint consumed = 0;

		status = agent_task_channel_consume_one(
			p, issuer, enter->generation, ops, &consumed);
		submitted += consumed;
		if (status <= 0)
			break;
		if (consumed != 1)
			panic("Task Channel consumed result");
	}
finish:
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_issuer_valid_locked(state, p, issuer) ||
	    state->ops != ops) {
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_STALE;
		return result->status;
	}
	completed += agent_task_channel_flush_locked(state);
	if (status >= 0)
		status = AGENT_TASK_CHANNEL_OK;
	if (status == AGENT_TASK_CHANNEL_OK && enter->min_complete != 0 &&
	    state->private->header.cq_tail -
		    state->private->header.cq_head < enter->min_complete)
		status = AGENT_TASK_CHANNEL_RETRY;
	agent_task_channel_publish_locked(state);
	agent_task_channel_enter_result_fill(state, result, status, submitted,
					     completed);
	intr_restore(enabled);
	return result->status;
}

static int
agent_task_resource_type_flags_valid(uint type, uint flags)
{
	return type > AGENT_ARTIFACT_NONE &&
	       type < AGENT_ARTIFACT_TYPE_COUNT &&
	       (flags == AGENT_TASK_HANDLE_F_OWNED ||
		flags == AGENT_TASK_HANDLE_F_BORROWED);
}

static void
agent_task_resource_result_fill(
	const struct agent_task_channel_state *state,
	struct agent_task_channel_resource_result *result, int status,
	uint resource_state, struct agent_task_resource_handle handle,
	uint64 source_handle, uint64 length, uint references)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_TASK_CHANNEL_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	result->state = resource_state;
	result->handle = handle;
	result->source_handle = source_handle;
	result->length = length;
	result->generation = state != 0 && state->private != 0 ?
				     state->private->header.generation : 0;
	result->references = references;
}

int
agent_task_channel_resource_publish(
	struct proc *p, uint64 ring_generation, uint64 request_id,
	uint64 slot_generation,
	const struct agent_task_resource_import *imported,
	struct agent_task_resource_handle *handle)
{
	struct agent_task_channel_state *state;
	struct agent_task_request_slot *request;
	int enabled;
	int status;

	if (p == 0 || ring_generation == 0 || request_id == 0 ||
	    slot_generation == 0 || handle == 0 ||
	    !agent_task_resource_import_valid(imported))
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	*handle = (struct agent_task_resource_handle){ 0 };
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    (request = agent_task_channel_request_authorized_locked(
		state, ring_generation, request_id, slot_generation)) == 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	if (!agent_task_handle_null(request->result)) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_NO_SPACE;
	}
	if (imported->resource_type != request->expected_output_type ||
	    imported->provenance_labels != request->provenance_labels ||
	    imported->producer_node_id != request->sqe.node_id ||
	    imported->producer_control_id != p->agent_control_id ||
	    imported->producer_pid != p->pid) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_DENIED;
	}
	status = agent_task_resource_allocate_locked(
		state, imported, ring_generation, request_id, slot_generation,
		handle);
	if (status == AGENT_TASK_CHANNEL_OK)
		request->result = *handle;
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	return status;
}

int
agent_task_channel_resource(
	struct proc *p, struct thread *issuer,
	const struct agent_task_channel_resource *control,
	struct agent_task_channel_resource_result *result,
	const struct agent_task_channel_ops *ops)
{
	struct agent_task_channel_state *state;
	struct agent_task_resource_slot *slot;
	struct agent_task_resource_import imported;
	struct agent_task_resource_handle handle = { 0 };
	struct agent_task_release release;
	struct workflow_lifecycle_key lifecycle;
	uint64 generation;
	uint64 source_handle = 0;
	uint64 length = 0;
	uint references = 0;
	uint resource_state = AGENT_TASK_RESOURCE_STATE_NONE;
	int status = AGENT_TASK_CHANNEL_BAD_REQUEST;
	int enabled;

	memset(&release, 0, sizeof(release));
	memset(&imported, 0, sizeof(imported));
	if (result == 0)
		return status;
	agent_task_resource_result_fill(0, result, status, resource_state,
					handle, 0, 0, 0);
	if (p == 0 || issuer == 0 || control == 0 ||
	    control->version != AGENT_TASK_CHANNEL_VERSION ||
	    control->size != sizeof(*control) || control->flags != 0 ||
	    control->reserved_tail != 0)
		return result->status;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_issuer_valid_locked(state, p, issuer) ||
	    state->ops != ops) {
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_STALE;
		return result->status;
	}
	generation = state->private->header.generation;
	if (control->channel_generation != generation) {
		agent_task_resource_result_fill(
			state, result, AGENT_TASK_CHANNEL_STALE, resource_state,
			handle, 0, 0, 0);
		intr_restore(enabled);
		return result->status;
	}
	lifecycle = agent_task_lifecycle_key(p);
	if (control->operation == AGENT_TASK_RESOURCE_IMPORT) {
		if (!agent_task_handle_null(control->handle) ||
		    !agent_task_resource_type_flags_valid(
			    control->resource_type, control->resource_flags) ||
		    control->resource_flags != AGENT_TASK_HANDLE_F_OWNED ||
		    ops == 0 || ops->resource_import == 0 ||
		    workflow_lifecycle_operation_enter(lifecycle) < 0) {
			intr_restore(enabled);
			return result->status;
		}
		agent_task_callback_get_locked(state);
		intr_restore(enabled);
		status = ops->resource_import(p, control, &imported);
		enabled = intr_save();
		state = agent_task_channel_find_locked(p);
		if (!agent_task_channel_owner_valid_locked(state, p) ||
		    state->ops != ops) {
			intr_restore(enabled);
			panic("Task Channel import ownership");
		}
		if (status < 0) {
			agent_task_resource_op_finish_locked(state);
			intr_restore(enabled);
			result->status = AGENT_TASK_CHANNEL_DENIED;
			agent_task_reclaim_deferred(p, ops);
			return result->status;
		}
		if (!agent_task_resource_import_valid(&imported) ||
		    imported.resource_type != control->resource_type ||
		    imported.resource_flags != control->resource_flags) {
			if (imported.source_handle != 0) {
				release.valid = 1;
				release.type = control->resource_type;
				release.flags = AGENT_TASK_HANDLE_F_OWNED;
				release.source_handle = imported.source_handle;
				release.length = imported.length;
			}
			intr_restore(enabled);
			agent_task_release_invoke(p, ops, &release);
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops)
				panic("Task Channel import metadata cleanup");
			agent_task_resource_op_finish_locked(state);
			intr_restore(enabled);
			result->status = AGENT_TASK_CHANNEL_EVIDENCE;
			agent_task_reclaim_deferred(p, ops);
			return result->status;
		}
		if (state->state != AGENT_TASK_CHANNEL_OWNER_LIVE ||
		    state->private->header.generation != generation) {
			release.valid = 1;
			release.type = imported.resource_type;
			release.flags = imported.resource_flags;
			release.source_handle = imported.source_handle;
			release.length = imported.length;
			intr_restore(enabled);
			agent_task_release_invoke(p, ops, &release);
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops)
				panic("Task Channel import stale cleanup");
			agent_task_resource_op_finish_locked(state);
			intr_restore(enabled);
			result->status = AGENT_TASK_CHANNEL_STALE;
			agent_task_reclaim_deferred(p, ops);
			return result->status;
		}
		status = agent_task_resource_allocate_locked(
			state, &imported, 0, 0, 0, &handle);
		if (status < 0) {
			release.valid = 1;
			release.type = imported.resource_type;
			release.flags = imported.resource_flags;
			release.source_handle = imported.source_handle;
			release.length = imported.length;
			intr_restore(enabled);
			agent_task_release_invoke(p, ops, &release);
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops)
				panic("Task Channel import failure cleanup");
			agent_task_resource_op_finish_locked(state);
			intr_restore(enabled);
			result->status = status;
			agent_task_reclaim_deferred(p, ops);
			return result->status;
		}
		source_handle = imported.source_handle;
		length = imported.length;
		resource_state = AGENT_TASK_RESOURCE_STATE_LIVE;
		references = 0;
		agent_task_resource_op_finish_locked(state);
		agent_task_resource_result_fill(
			state, result, AGENT_TASK_CHANNEL_OK, resource_state,
			handle, source_handle, length, references);
		intr_restore(enabled);
		agent_task_reclaim_deferred(p, ops);
		return result->status;
	}
	if (control->resource_type != 0 || control->resource_flags != 0 ||
	    control->source_handle != 0 || control->length != 0 ||
	    !agent_task_handle_shape_valid(control->handle) ||
	    agent_task_handle_null(control->handle)) {
		intr_restore(enabled);
		return result->status;
	}
	if (control->handle.flags != AGENT_TASK_HANDLE_F_OWNED) {
		intr_restore(enabled);
		return result->status;
	}
	slot = agent_task_resource_find_locked(
		state->resource_private, control->handle);
	if (slot == 0) {
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_STALE;
		return result->status;
	}
	if (slot->owner_request_id != 0) {
		intr_restore(enabled);
		result->status = AGENT_TASK_CHANNEL_RETRY;
		return result->status;
	}
	handle = control->handle;
	source_handle = slot->source_handle;
	length = slot->length;
	references = slot->references;
	resource_state = slot->state;
	if (control->operation == AGENT_TASK_RESOURCE_QUERY) {
		agent_task_resource_result_fill(
			state, result, AGENT_TASK_CHANNEL_OK, resource_state,
			handle, source_handle, length, references);
		intr_restore(enabled);
		return result->status;
	}
	if (control->operation != AGENT_TASK_RESOURCE_RELEASE ||
	    slot->state != AGENT_TASK_RESOURCE_STATE_LIVE ||
	    slot->references != 0 ||
	    workflow_lifecycle_operation_enter(lifecycle) < 0) {
		intr_restore(enabled);
		return result->status;
	}
	agent_task_callback_get_locked(state);
	agent_task_release_capture_locked(state->private, slot, &release);
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	agent_task_release_invoke(p, ops, &release);
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->ops != ops)
		panic("Task Channel release cleanup");
	agent_task_resource_op_finish_locked(state);
	agent_task_resource_result_fill(
		state, result, AGENT_TASK_CHANNEL_OK,
		AGENT_TASK_RESOURCE_STATE_NONE, handle, source_handle, length, 0);
	intr_restore(enabled);
	agent_task_reclaim_deferred(p, ops);
	return result->status;
}

int
agent_task_channel_alias_exec(struct proc *p, pagetable_t pagetable)
{
	struct agent_task_channel_state *state;
	uint64 sq;
	uint64 cq;
	int enabled;

	if (p == 0 || pagetable == 0 || curr_thread() != &p->threads[0])
		return -1;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (state == 0) {
		intr_restore(enabled);
		return 0;
	}
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->state != AGENT_TASK_CHANNEL_OWNER_LIVE ||
	    !proc_teardown_live(p) ||
	    !workflow_lifecycle_active(state->lifecycle) ||
	    state->private->header.callback_refs != 0 ||
	    state->private->header.exec_alias_refs !=
		    AGENT_TASK_EXEC_ALIAS_NONE ||
	    state->generation_highwater == ~0ULL) {
		intr_restore(enabled);
		return -1;
	}
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
		uint request_state = state->private->requests[i].state;

		if (request_state == AGENT_TASK_REQUEST_ACCEPTED ||
		    request_state == AGENT_TASK_REQUEST_RUNNING ||
		    request_state == AGENT_TASK_REQUEST_EVIDENCE_PENDING) {
			intr_restore(enabled);
			return -1;
		}
	}
	state->private->header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_STAGED;
	sq = (uint64)state->sq;
	cq = (uint64)state->cq;
	if (mappages(pagetable, AGENT_TASK_CHANNEL_SQ_BASE, PAGE_SIZE, sq,
		     PTE_U | PTE_R | PTE_W) < 0) {
		state->private->header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE;
		intr_restore(enabled);
		return -1;
	}
	if (mappages(pagetable, AGENT_TASK_CHANNEL_CQ_BASE, PAGE_SIZE, cq,
		     PTE_U | PTE_R) < 0) {
		uvmunmap(pagetable, AGENT_TASK_CHANNEL_SQ_BASE, 1, 0);
		state->private->header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE;
		intr_restore(enabled);
		return -1;
	}
	intr_restore(enabled);
	return 0;
}

void
agent_task_channel_abort_exec_alias(struct proc *p, pagetable_t pagetable)
{
	int enabled;
	struct agent_task_channel_state *state;
	const struct agent_task_channel_ops *ops = 0;
	pte_t *sq;
	pte_t *cq;

	if (p == 0 || pagetable == 0)
		return;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (state != 0 && state->private != 0 &&
	    state->state == AGENT_TASK_CHANNEL_OWNER_LIVE &&
	    state->private->header.exec_alias_refs ==
		    AGENT_TASK_EXEC_ALIAS_STAGED) {
		sq = walk(pagetable, AGENT_TASK_CHANNEL_SQ_BASE, 0);
		cq = walk(pagetable, AGENT_TASK_CHANNEL_CQ_BASE, 0);
		if (sq == 0 || cq == 0 || (*sq & PTE_V) == 0 ||
		    (*cq & PTE_V) == 0 || PTE2PA(*sq) != (uint64)state->sq ||
		    PTE2PA(*cq) != (uint64)state->cq)
			panic("Task Channel exec alias");
		uvmunmap(pagetable, AGENT_TASK_CHANNEL_SQ_BASE,
			 AGENT_TASK_CHANNEL_MAPPED_PAGES, 0);
		state->private->header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE;
		ops = state->ops;
	}
	intr_restore(enabled);
	if (ops != 0)
		agent_task_reclaim_deferred(p, ops);
}

void
agent_task_channel_unmap_exec(struct proc *p, pagetable_t pagetable)
{
	struct agent_task_channel_state *state;
	const struct agent_task_channel_ops *ops;
	pte_t *sq;
	pte_t *cq;
	int enabled;

	if (p == 0 || pagetable == 0)
		return;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (state == 0 || state->private == 0) {
		intr_restore(enabled);
		return;
	}
	if (state->private->header.exec_alias_refs !=
	    AGENT_TASK_EXEC_ALIAS_COMMITTED)
		panic("Task Channel exec transition");
	sq = walk(pagetable, AGENT_TASK_CHANNEL_SQ_BASE, 0);
	cq = walk(pagetable, AGENT_TASK_CHANNEL_CQ_BASE, 0);
	if (sq == 0 || cq == 0 || (*sq & PTE_V) == 0 ||
	    (*cq & PTE_V) == 0 || PTE2PA(*sq) != (uint64)state->sq ||
	    PTE2PA(*cq) != (uint64)state->cq)
		panic("Task Channel exec unmap");
	uvmunmap(pagetable, AGENT_TASK_CHANNEL_SQ_BASE,
		 AGENT_TASK_CHANNEL_MAPPED_PAGES, 0);
	state->private->header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE;
	ops = state->ops;
	intr_restore(enabled);
	agent_task_reclaim_deferred(p, ops);
}

int
agent_task_channel_rebind_exec(struct proc *p, struct thread *issuer)
{
	struct agent_task_channel_state *state;
	struct agent_task_private_header *header;
	uint64 generation;
	int enabled;

	if (p == 0 || issuer == 0 || issuer != curr_thread() ||
	    issuer != &p->threads[0] || issuer->process != p ||
	    issuer->identity_generation == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->state != AGENT_TASK_CHANNEL_OWNER_LIVE ||
	    !proc_teardown_live(p) ||
	    !workflow_lifecycle_active(state->lifecycle)) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	header = &state->private->header;
	if (header->callback_refs != 0 ||
	    header->exec_alias_refs != AGENT_TASK_EXEC_ALIAS_STAGED) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
		uint request_state = state->private->requests[i].state;

		if (request_state == AGENT_TASK_REQUEST_ACCEPTED ||
		    request_state == AGENT_TASK_REQUEST_RUNNING ||
		    request_state == AGENT_TASK_REQUEST_EVIDENCE_PENDING) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
	}
	if (state->generation_highwater == ~0ULL) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	generation = state->generation_highwater + 1;
	if (generation == 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	state->generation_highwater = generation;
	header->generation = generation;
	header->sq_head = 0;
	header->sq_tail = 0;
	header->issuer_tid = issuer->tid;
	header->issuer_generation = issuer->identity_generation;
	header->flags &= ~(AGENT_TASK_CHANNEL_RING_F_RESYNC |
			   AGENT_TASK_CHANNEL_RING_F_RECLAIMING |
			   AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE);
	header->flags |= AGENT_TASK_CHANNEL_RING_F_ACTIVE;
	header->resync_count++;
	header->exec_alias_refs = AGENT_TASK_EXEC_ALIAS_COMMITTED;
	memset(state->sq->entries, 0, sizeof(state->sq->entries));
	agent_task_channel_publish_locked(state);
	intr_restore(enabled);
	return AGENT_TASK_CHANNEL_OK;
}

int
agent_task_channel_reclaim(struct proc *p,
			   const struct agent_task_channel_ops *ops)
{
	struct agent_task_channel_state *state;
	struct agent_task_release
		releases[AGENT_TASK_CHANNEL_RESOURCE_CAPACITY];
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	struct resource_request request = {
		.kind = RESOURCE_AGENT_STATE_PAGE,
		.amount = AGENT_TASK_CHANNEL_STATE_PAGES,
	};
	void *pages[AGENT_TASK_CHANNEL_STATE_PAGES];
	struct workflow_lifecycle_key lifecycle;
	uint release_count = 0;
	int enabled;

	if (p == 0 || ops == 0)
		return AGENT_TASK_CHANNEL_BAD_REQUEST;
	memset(releases, 0, sizeof(releases));

retry:
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (state == 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_OK;
	}
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->ops != ops) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_STALE;
	}
	if (state->state == AGENT_TASK_CHANNEL_OWNER_FINALIZING) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	if (state->state == AGENT_TASK_CHANNEL_OWNER_LIVE &&
	    state->private->header.exec_alias_refs != 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	if (state->state == AGENT_TASK_CHANNEL_OWNER_LIVE) {
		if (workflow_lifecycle_departure_enter(state->lifecycle) < 0) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		state->departure_held = 1;
		state->state = AGENT_TASK_CHANNEL_OWNER_RECLAIMING;
		state->private->header.flags |=
			AGENT_TASK_CHANNEL_RING_F_RECLAIMING;
		agent_task_channel_publish_locked(state);
	}
	if (!state->departure_held)
		panic("Task Channel reclaim departure");
	if (state->private->header.callback_refs != 0 ||
	    state->private->header.exec_alias_refs != 0) {
		intr_restore(enabled);
		return AGENT_TASK_CHANNEL_RETRY;
	}
	for (uint i = 0; i < AGENT_TASK_CHANNEL_CAPACITY; i++) {
		struct agent_task_request_slot *slot =
			&state->private->requests[i];
		struct agent_task_completion completion;
		struct agent_task_sqe cancel;
		struct agent_task_sqe original;
		int hook_status;

		if (slot->state == AGENT_TASK_REQUEST_ACCEPTED ||
		    slot->state == AGENT_TASK_REQUEST_EVIDENCE_PENDING) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		if (slot->state != AGENT_TASK_REQUEST_RUNNING)
			continue;
		original = slot->sqe;
		if ((original.flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&
		    (agent_task_now() >= original.deadline_tick ||
		     (slot->flags & AGENT_TASK_REQUEST_F_DEADLINE_DUE) != 0)) {
			int expire_status;
			int complete_status;

			slot->flags |= AGENT_TASK_REQUEST_F_DEADLINE_DUE;
			agent_task_callback_get_locked(state);
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			expire_status = agent_task_deadline_completion(
				p, ops, &original, &completion);
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops ||
			    state->private->header.callback_refs == 0)
				panic("Task Channel reclaim expiry callback");
			agent_task_callback_put_locked(state);
			intr_restore(enabled);
			if (expire_status < 0)
				return expire_status;
			complete_status = agent_task_channel_complete_finish(
				p, original.ring_generation, original.request_id,
				original.slot_generation, &completion, ops);
			if (complete_status != AGENT_TASK_CHANNEL_OK &&
			    complete_status != AGENT_TASK_CHANNEL_STALE)
				return complete_status;
			goto retry;
		}
		if ((slot->flags & AGENT_TASK_REQUEST_F_CANCEL_REQUESTED) != 0) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		if ((slot->flags & AGENT_TASK_REQUEST_F_CANCEL_DENIED) != 0) {
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_RETRY;
		}
		slot->flags |= AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
		cancel = original;
		cancel.opcode = AGENT_TASK_CHANNEL_OP_CANCEL;
		cancel.flags = AGENT_TASK_SQE_F_CANCEL;
		cancel.link_request_id = original.request_id;
		cancel.request_id = 0;
		cancel.input = (struct agent_task_resource_handle){ 0 };
		agent_task_callback_get_locked(state);
		agent_task_channel_publish_locked(state);
		intr_restore(enabled);
		memset(&completion, 0, sizeof(completion));
		hook_status = ops->cancel(p, &cancel, &completion);
		enabled = intr_save();
		state = agent_task_channel_find_locked(p);
		if (!agent_task_channel_owner_valid_locked(state, p) ||
		    state->ops != ops || state->private->header.callback_refs == 0)
			panic("Task Channel reclaim cancel callback");
		agent_task_callback_put_locked(state);
		intr_restore(enabled);
		if (hook_status == AGENT_TASK_HOOK_COMPLETE) {
			int complete_status = agent_task_channel_complete_finish(
				p, original.ring_generation,
				original.request_id, original.slot_generation,
				&completion, ops);

			if (complete_status != AGENT_TASK_CHANNEL_OK &&
			    complete_status != AGENT_TASK_CHANNEL_STALE) {
				enabled = intr_save();
				state = agent_task_channel_find_locked(p);
				if (!agent_task_channel_owner_valid_locked(state, p) ||
				    state->ops != ops)
					panic("Task Channel reclaim completion");
				slot = agent_task_request_find_locked(
					state->private, original.request_id);
				if (slot != 0 && slot->state ==
				    AGENT_TASK_REQUEST_RUNNING) {
					slot->flags &=
						~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
					slot->flags |=
						AGENT_TASK_REQUEST_F_CANCEL_DENIED;
				}
				agent_task_channel_publish_locked(state);
				intr_restore(enabled);
				return complete_status;
			}
		} else if (hook_status == AGENT_TASK_HOOK_DENIED) {
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops)
				panic("Task Channel reclaim cancel denial");
			slot = agent_task_request_find_locked(
				state->private, original.request_id);
			if (slot != 0 && slot->state == AGENT_TASK_REQUEST_RUNNING) {
				slot->flags &=
					~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
				slot->flags |= AGENT_TASK_REQUEST_F_CANCEL_DENIED;
			}
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_DENIED;
		} else if (hook_status != AGENT_TASK_HOOK_PENDING) {
			enabled = intr_save();
			state = agent_task_channel_find_locked(p);
			if (!agent_task_channel_owner_valid_locked(state, p) ||
			    state->ops != ops)
				panic("Task Channel reclaim cancel error");
			slot = agent_task_request_find_locked(
				state->private, original.request_id);
			if (slot != 0 && slot->state == AGENT_TASK_REQUEST_RUNNING) {
				slot->flags &=
					~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED;
				slot->flags |= AGENT_TASK_REQUEST_F_CANCEL_DENIED;
			}
			agent_task_channel_publish_locked(state);
			intr_restore(enabled);
			return AGENT_TASK_CHANNEL_EVIDENCE;
		}
		goto retry;
	}
	state->state = AGENT_TASK_CHANNEL_OWNER_FINALIZING;
	for (uint i = 0; i < AGENT_TASK_CHANNEL_RESOURCE_CAPACITY; i++) {
		struct agent_task_resource_slot *slot =
			&state->resource_private->resources[i];

		if (slot->state == AGENT_TASK_RESOURCE_STATE_NONE)
			continue;
		agent_task_release_capture_locked(
			state->private, slot, &releases[release_count++]);
	}
	account = state->account;
	charge_class = state->charge_class;
	lifecycle = state->lifecycle;
	pages[0] = state->sq;
	pages[1] = state->cq;
	pages[2] = state->private;
	pages[3] = state->resource_private;
	intr_restore(enabled);
	for (uint i = 0; i < release_count; i++)
		agent_task_release_invoke(p, ops, &releases[i]);
	enabled = intr_save();
	state = agent_task_channel_find_locked(p);
	if (!agent_task_channel_owner_valid_locked(state, p) ||
	    state->state != AGENT_TASK_CHANNEL_OWNER_FINALIZING ||
	    state->ops != ops || !state->departure_held)
		panic("Task Channel final reclaim");
	if (p->pagetable != 0)
		uvmunmap(p->pagetable, AGENT_TASK_CHANNEL_SQ_BASE,
			 AGENT_TASK_CHANNEL_MAPPED_PAGES, 0);
	agent_task_channel_reset_locked(state);
	intr_restore(enabled);
	for (uint i = 0; i < AGENT_TASK_CHANNEL_STATE_PAGES; i++)
		if (kfree_account_page(pages[i], account, charge_class) < 0)
			panic("Task Channel physical release");
	if (resource_release_many(account, charge_class, &request, 1) < 0)
		panic("Task Channel state release");
	proc_resource_account_reap(account);
	workflow_lifecycle_departure_leave(lifecycle);
	return AGENT_TASK_CHANNEL_OK;
}

int
agent_task_channel_active(const struct proc *p)
{
	struct agent_task_channel_state *state;
	int active;
	int enabled = intr_save();

	state = agent_task_channel_find_locked(p);
	active = agent_task_channel_owner_valid_locked(state, p);
	intr_restore(enabled);
	return active;
}
