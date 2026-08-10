#include "agent_context.h"
#include "agent_context_path.h"
#include "agent_evidence_ring.h"
#include "agent_internal.h"
#include "agent_provenance.h"
#include "defs.h"
#include "kernel_work.h"
#include "timer.h"
#include "trap.h"

struct agent_context_private_slot {
	struct agent_context_detail detail;
	uint64 span_owner;
	uint64 cause_control;
	uint64 cause_branch_generation;
	uint64 actor_identity;
};

#define AGENT_PRIVATE_STATE_PAGE_COUNT \
	(AGENT_CONTEXT_SIDECAR_PAGE_COUNT + AGENT_COLD_STATE_PAGE_COUNT)

#define AGENT_CONTEXT_SLOTS_PER_PAGE \
	(PAGE_SIZE / sizeof(struct agent_context_private_slot))
#define AGENT_CONTEXT_LAST_PAGE_RECORDS \
	(AGENT_CONTEXT_MAX_RECORDS - \
	 (AGENT_CONTEXT_SIDECAR_PAGE_COUNT - 1) * \
		 AGENT_CONTEXT_SLOTS_PER_PAGE)
#define AGENT_CONTEXT_PATH_INDEX_MAGIC 0x4354585041544831ULL
#define AGENT_CONTEXT_PROVENANCE_STATE_OFFSET \
	(AGENT_CONTEXT_LAST_PAGE_RECORDS * \
		 sizeof(struct agent_context_private_slot) + \
	 sizeof(struct agent_context_path_index))

struct agent_context_path_summary {
	uint64 workflow_lifecycle_id;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
	uint64 head_sequence;
	uint64 head_hash;
	uint64 count;
	uint64 oldest_sequence;
};

struct agent_context_path_index {
	uint64 magic;
	struct agent_context_path_summary summary;
	uint64 successors[AGENT_CONTEXT_MAX_RECORDS];
};

struct agent_context_append_receipt {
	struct agent_context_path_summary before;
	uint64 archive_count;
	uint64 archive_oldest;
	uint64 archive_latest;
	uint64 archive_head;
	uint64 sequence;
	uint64 slot;
	uint64 evicted_sequence;
	uint64 evicted_successor;
	int evicted_active;
};

_Static_assert(AGENT_CONTEXT_SLOTS_PER_PAGE > 0,
	       "an Agent context slot must fit in one page");
_Static_assert(sizeof(struct agent_context_private_slot) *
			       AGENT_CONTEXT_SLOTS_PER_PAGE <=
		       PAGE_SIZE,
	       "Agent context slots must not cross page boundaries");
_Static_assert(AGENT_CONTEXT_SIDECAR_PAGE_COUNT *
			       AGENT_CONTEXT_SLOTS_PER_PAGE >=
		       AGENT_CONTEXT_MAX_RECORDS,
	       "nine sidecar pages must cover every context record");
_Static_assert((AGENT_CONTEXT_SIDECAR_PAGE_COUNT - 1) *
			       AGENT_CONTEXT_SLOTS_PER_PAGE <
		       AGENT_CONTEXT_MAX_RECORDS,
	       "eight sidecar pages must remain insufficient");
_Static_assert(AGENT_CONTEXT_LAST_PAGE_RECORDS > 0 &&
		       AGENT_CONTEXT_LAST_PAGE_RECORDS <=
			       AGENT_CONTEXT_SLOTS_PER_PAGE,
	       "Agent context last-page record count");
_Static_assert(AGENT_CONTEXT_LAST_PAGE_RECORDS *
			       sizeof(struct agent_context_private_slot) +
		       sizeof(struct agent_context_path_index) +
		       AGENT_CONTEXT_PROVENANCE_STATE_SIZE <=
		       PAGE_SIZE,
	       "Agent context path index and provenance must fit sidecar slack");
_Static_assert((AGENT_CONTEXT_PROVENANCE_STATE_OFFSET & 7U) == 0,
	       "Agent provenance sidecar alignment");
_Static_assert(AGENT_STATE_PAGE_COUNT ==
		       AGENT_CONTEXT_SIDECAR_PAGE_COUNT +
			       AGENT_COLD_STATE_PAGE_COUNT +
			       AGENT_CONTEXT_PAGES,
	       "Agent state accounting must cover private and mapped pages");
_Static_assert(AGENT_COLD_STATE_PAGE_COUNT == 1U,
	       "冷状态页绑定必须与固定的 IPC/观测页一致");

static void
agent_context_metadata_reset(struct proc *p)
{
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		p->agent_context_sidecar_kva[page] = 0;
	p->agent_ipc_observe_cold = 0;
	p->agent_state_account = resource_account_none();
	p->agent_state_charge_class = RESOURCE_CHARGE_CLASS_COUNT;
	p->agent_state_charged_pages = 0;
	agent_provenance_proc_reset(p);
}

void
agent_context_proc_activate(struct proc *p)
{
	if (p == 0)
		return;
	p->agent_ctx_base = AGENT_CONTEXT_BASE;
}

void
agent_context_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	if (!agent_context_is_empty(p))
		panic("Agent context reset with live state");
	p->agent_ctx_base = 0;
	p->agent_call_count = 0;
	p->context_path_count = 0;
	p->context_path_capacity = 0;
	p->context_path_head = 0;
	p->context_path_oldest = 0;
	p->context_path_latest = 0;
	p->context_path_visible_head = 0;
	p->context_active_path_count = 0;
	p->context_active_path_oldest = 0;
	p->context_branch_generation = 0;
	p->context_cause_branch_generation = 0;
	p->context_path_rollback_count = 0;
	p->agent_current_span_id = 0;
	p->agent_current_span_owner = 0;
	p->agent_current_cause_sequence = 0;
	p->agent_current_cause_pid = 0;
	p->agent_current_cause_control = 0;
	p->agent_context_chain_hash = 0;
	agent_provenance_proc_reset(p);
}

int
agent_context_is_empty(const struct proc *p)
{
	if (p == 0 || p->agent_state_charged_pages != 0 ||
	    resource_account_handle_valid(p->agent_state_account))
		return 0;
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		if (p->agent_context_sidecar_kva[page] != 0)
			return 0;
	if (p->agent_ipc_observe_cold != 0)
		return 0;
	for (uint page = 0; page < AGENT_CONTEXT_PAGES; page++)
		if (p->agent_ctx_kva[page] != 0)
			return 0;
	return 1;
}

static int
agent_state_reservation_ready(struct proc *p)
{
	if (p == 0 ||
	    p->agent_state_charged_pages != AGENT_STATE_PAGE_COUNT ||
	    p->agent_state_charge_class < RESOURCE_CHARGE_ORDINARY ||
	    p->agent_state_charge_class >= RESOURCE_CHARGE_CLASS_COUNT ||
	    !resource_account_handle_valid(p->agent_state_account))
		return 0;
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		if (p->agent_context_sidecar_kva[page] == 0)
			return 0;
	if (p->agent_ipc_observe_cold == 0)
		return 0;
	return 1;
}

static int
agent_context_ready(struct proc *p)
{
	if (!agent_state_reservation_ready(p))
		return 0;
	for (uint page = 0; page < AGENT_CONTEXT_PAGES; page++)
		if (p->agent_ctx_kva[page] == 0)
			return 0;
	return 1;
}

static struct agent_context_private_slot *
agent_context_slot(struct proc *p, uint64 slot)
{
	uint page = slot / AGENT_CONTEXT_SLOTS_PER_PAGE;
	uint index = slot % AGENT_CONTEXT_SLOTS_PER_PAGE;

	return &((struct agent_context_private_slot *)
			 p->agent_context_sidecar_kva[page])[index];
}

static struct agent_context_path_index *
agent_context_path_index(struct proc *p)
{
	char *last_page;

	if (p == 0 ||
	    p->agent_context_sidecar_kva[AGENT_CONTEXT_SIDECAR_PAGE_COUNT - 1] ==
		    0)
		return 0;
	last_page = (char *)p->agent_context_sidecar_kva
		[AGENT_CONTEXT_SIDECAR_PAGE_COUNT - 1];
	return (struct agent_context_path_index *)(
		last_page + AGENT_CONTEXT_LAST_PAGE_RECORDS *
				    sizeof(struct agent_context_private_slot));
}

void *
agent_context_provenance_sidecar(struct proc *p)
{
	char *last_page;

	if (p == 0 ||
	    p->agent_context_sidecar_kva[AGENT_CONTEXT_SIDECAR_PAGE_COUNT - 1] ==
		    0)
		return 0;
	last_page = (char *)p->agent_context_sidecar_kva
		[AGENT_CONTEXT_SIDECAR_PAGE_COUNT - 1];
	return last_page + AGENT_CONTEXT_PROVENANCE_STATE_OFFSET;
}

static int
agent_context_path_index_matches(struct proc *p,
				 struct agent_context_path_index *index)
{
	struct agent_context_path_summary *summary;

	if (p == 0 || index == 0 || index->magic != AGENT_CONTEXT_PATH_INDEX_MAGIC)
		return 0;
	summary = &index->summary;
	if (summary->workflow_lifecycle_id != p->workflow_lifecycle_id ||
	    summary->workflow_lifecycle_generation !=
		    p->workflow_lifecycle_generation ||
	    summary->branch_generation != p->context_branch_generation ||
	    summary->head_sequence != p->context_path_visible_head ||
	    summary->head_hash != p->agent_context_chain_hash ||
	    summary->count != p->context_active_path_count ||
	    summary->oldest_sequence != p->context_active_path_oldest ||
	    summary->count > p->context_path_count)
		return 0;
	if (summary->count == 0)
		return summary->head_sequence == 0 && summary->head_hash == 0 &&
		       summary->oldest_sequence == 0;
	return summary->head_sequence >= p->context_path_oldest &&
	       summary->head_sequence <= p->context_path_latest &&
	       summary->oldest_sequence >= p->context_path_oldest &&
	       summary->oldest_sequence <= summary->head_sequence &&
	       summary->branch_generation != 0;
}

static int
agent_context_path_summary_capture(struct proc *p,
				   struct agent_context_path_summary *summary)
{
	struct agent_context_path_index *index = agent_context_path_index(p);

	if (summary == 0 || !agent_context_path_index_matches(p, index))
		return -1;
	*summary = index->summary;
	return 0;
}

static int
agent_context_path_summary_matches(struct proc *p,
				   const struct agent_context_path_summary *summary)
{
	struct agent_context_path_index *index = agent_context_path_index(p);
	struct agent_context_path_summary current;

	if (p == 0 || summary == 0 || index == 0 ||
	    index->magic != AGENT_CONTEXT_PATH_INDEX_MAGIC)
		return 0;
	current = index->summary;
	return current.workflow_lifecycle_id == summary->workflow_lifecycle_id &&
	       current.workflow_lifecycle_generation ==
		       summary->workflow_lifecycle_generation &&
	       current.branch_generation == summary->branch_generation &&
	       current.head_sequence == summary->head_sequence &&
	       current.head_hash == summary->head_hash &&
	       current.count == summary->count &&
	       current.oldest_sequence == summary->oldest_sequence &&
	       p->workflow_lifecycle_id == summary->workflow_lifecycle_id &&
	       p->workflow_lifecycle_generation ==
		       summary->workflow_lifecycle_generation &&
	       p->context_branch_generation == summary->branch_generation &&
	       p->context_path_visible_head == summary->head_sequence &&
	       p->agent_context_chain_hash == summary->head_hash &&
	       p->context_active_path_count == summary->count &&
	       p->context_active_path_oldest == summary->oldest_sequence;
}

static int
agent_context_path_index_reset(struct proc *p, uint64 branch_generation)
{
	struct agent_context_path_index *index = agent_context_path_index(p);

	if (!agent_context_ready(p) || index == 0 || branch_generation == 0)
		return -1;
	memset(index, 0, sizeof(*index));
	index->magic = AGENT_CONTEXT_PATH_INDEX_MAGIC;
	index->summary.workflow_lifecycle_id = p->workflow_lifecycle_id;
	index->summary.workflow_lifecycle_generation =
		p->workflow_lifecycle_generation;
	index->summary.branch_generation = branch_generation;
	return 0;
}

static int
agent_context_append_receipt_prepare(
	struct proc *p, const struct agent_context_record *record, uint64 slot,
	struct agent_context_append_receipt *receipt)
{
	struct agent_context_path_index *index = agent_context_path_index(p);
	uint64 successor;

	if (p == 0 || record == 0 || receipt == 0 ||
	    !agent_context_path_index_matches(p, index) ||
	    p->context_path_capacity == 0 || slot >= p->context_path_capacity ||
	    slot != p->context_path_head % p->context_path_capacity ||
	    p->context_path_latest == ~0ULL ||
	    record->sequence != p->context_path_latest + 1 ||
	    record->branch_generation != index->summary.branch_generation ||
	    record->path_parent_sequence != index->summary.head_sequence ||
	    record->prev_hash != index->summary.head_hash ||
	    record->record_hash == 0 ||
	    record->record_hash != agent_context_record_hash(record) ||
	    (index->summary.count == 0) !=
		    (record->path_parent_sequence == 0) ||
	    (index->summary.head_sequence != 0 &&
	     index->successors[(index->summary.head_sequence - 1) %
			       p->context_path_capacity] != 0))
		return -1;
	memset(receipt, 0, sizeof(*receipt));
	receipt->before = index->summary;
	receipt->archive_count = p->context_path_count;
	receipt->archive_oldest = p->context_path_oldest;
	receipt->archive_latest = p->context_path_latest;
	receipt->archive_head = p->context_path_head;
	receipt->sequence = record->sequence;
	receipt->slot = slot;
	if (p->context_path_count < p->context_path_capacity)
		return 0;
	receipt->evicted_sequence = p->context_path_oldest;
	if ((receipt->evicted_sequence - 1) % p->context_path_capacity != slot)
		return -1;
	if (receipt->evicted_sequence != index->summary.oldest_sequence)
		return 0;
	receipt->evicted_active = 1;
	successor = index->successors[slot];
	if ((index->summary.count == 1 && successor != 0) ||
	    (index->summary.count > 1 &&
	     (successor <= receipt->evicted_sequence ||
	      successor > index->summary.head_sequence ||
	      successor < p->context_path_oldest)))
		return -1;
	receipt->evicted_successor = successor;
	return 0;
}

static int
agent_context_append_receipt_commit(
	struct proc *p, const struct agent_context_record *record,
	const struct agent_context_append_receipt *receipt)
{
	struct agent_context_path_index *index = agent_context_path_index(p);
	uint64 expected_archive_count;
	uint64 expected_archive_oldest;
	uint64 active_count;
	uint64 active_oldest;

	if (p == 0 || record == 0 || receipt == 0 ||
	    !agent_context_path_summary_matches(p, &receipt->before) ||
	    record->sequence != receipt->sequence ||
	    record->branch_generation != receipt->before.branch_generation ||
	    record->path_parent_sequence != receipt->before.head_sequence ||
	    record->prev_hash != receipt->before.head_hash ||
	    receipt->slot >= p->context_path_capacity ||
	    receipt->archive_head != receipt->slot ||
	    receipt->archive_latest == ~0ULL ||
	    receipt->archive_latest + 1 != receipt->sequence)
		return -1;
	expected_archive_count = receipt->archive_count < p->context_path_capacity ?
				 receipt->archive_count + 1 :
				 receipt->archive_count;
	expected_archive_oldest = receipt->archive_count == 0 ?
				  receipt->sequence :
				  receipt->archive_oldest;
	if (receipt->archive_count == p->context_path_capacity)
		expected_archive_oldest =
			receipt->sequence - p->context_path_capacity + 1;
	if (p->context_path_count != expected_archive_count ||
	    p->context_path_oldest != expected_archive_oldest ||
	    p->context_path_latest != receipt->sequence ||
	    p->context_path_head !=
		    (receipt->slot + 1) % p->context_path_capacity)
		return -1;
	active_count = receipt->before.count + 1;
	if (receipt->evicted_active)
		active_count--;
	if (receipt->before.count == 0 ||
	    (receipt->evicted_active && receipt->before.count == 1))
		active_oldest = receipt->sequence;
	else if (receipt->evicted_active)
		active_oldest = receipt->evicted_successor;
	else
		active_oldest = receipt->before.oldest_sequence;
	if (active_count == 0 || active_count > p->context_path_count ||
	    active_oldest < p->context_path_oldest ||
	    active_oldest > receipt->sequence)
		return -1;
	if (receipt->before.head_sequence != 0 &&
	    receipt->before.head_sequence != receipt->evicted_sequence)
		index->successors[(receipt->before.head_sequence - 1) %
				  p->context_path_capacity] = receipt->sequence;
	index->successors[receipt->slot] = 0;
	index->summary.head_sequence = receipt->sequence;
	index->summary.head_hash = record->record_hash;
	index->summary.count = active_count;
	index->summary.oldest_sequence = active_oldest;
	p->context_path_visible_head = receipt->sequence;
	p->context_active_path_count = active_count;
	p->context_active_path_oldest = active_oldest;
	return 0;
}

int
agent_context_alloc(struct proc *p)
{
	void *pages[AGENT_PRIVATE_STATE_PAGE_COUNT];
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	struct resource_reservation reservation;
	struct resource_request request = {
		.kind = RESOURCE_AGENT_STATE_PAGE,
		.amount = AGENT_STATE_PAGE_COUNT,
	};
	uint allocated = 0;
	int pid;
	int enabled;

	enabled = intr_save();
	if (p == 0 || !proc_teardown_live(p) ||
	    !agent_context_is_empty(p) ||
	    !resource_account_active(p->resource_account)) {
		intr_restore(enabled);
		return -1;
	}
	pid = p->pid;
	account = p->resource_account;
	charge_class = p->resource_slot_reserved ?
			       RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY;
	if (resource_reserve_many(account, charge_class, &request, 1,
				  &reservation) < 0) {
		intr_restore(enabled);
		return -1;
	}
	intr_restore(enabled);
	while (allocated < AGENT_PRIVATE_STATE_PAGE_COUNT) {
		pages[allocated] =
			kalloc_account_page(account, charge_class);
		if (pages[allocated] == 0)
			goto fail;
		memset(pages[allocated], 0, PAGE_SIZE);
		allocated++;
	}

	enabled = intr_save();
	if (!proc_teardown_live(p) || p->pid != pid ||
	    !agent_context_is_empty(p) ||
	    !resource_account_handle_equal(p->resource_account, account) ||
	    (p->resource_slot_reserved != 0) !=
		    (charge_class == RESOURCE_CHARGE_RESERVED)) {
		intr_restore(enabled);
		goto fail;
	}
	if (resource_reservation_commit(&reservation) < 0) {
		intr_restore(enabled);
		goto fail;
	}
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		p->agent_context_sidecar_kva[page] = (uint64)pages[page];
	p->agent_ipc_observe_cold = pages[AGENT_CONTEXT_SIDECAR_PAGE_COUNT];
	p->agent_state_account = account;
	p->agent_state_charge_class = charge_class;
	p->agent_state_charged_pages = AGENT_STATE_PAGE_COUNT;
	intr_restore(enabled);
	return 0;

fail:
	while (allocated > 0)
		(void)kfree_account_page(pages[--allocated], account,
					 charge_class);
	resource_reservation_cancel(&reservation);
	return -1;
}

void
agent_context_free(struct proc *p)
{
	void *pages[AGENT_PRIVATE_STATE_PAGE_COUNT];
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	struct resource_request request = {
		.kind = RESOURCE_AGENT_STATE_PAGE,
		.amount = AGENT_STATE_PAGE_COUNT,
	};
	int enabled;

	if (p == 0)
		return;
	enabled = intr_save();
	if (agent_context_is_empty(p)) {
		agent_context_metadata_reset(p);
		intr_restore(enabled);
		return;
	}
	if (!agent_state_reservation_ready(p) ||
	    !resource_account_handle_equal(
		    p->agent_state_account, p->resource_account))
		panic("Agent context state");
	account = p->agent_state_account;
	charge_class = p->agent_state_charge_class;
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		pages[page] = (void *)p->agent_context_sidecar_kva[page];
	pages[AGENT_CONTEXT_SIDECAR_PAGE_COUNT] = p->agent_ipc_observe_cold;
	agent_context_metadata_reset(p);
	for (uint page = 0; page < AGENT_PRIVATE_STATE_PAGE_COUNT; page++)
		(void)kfree_account_page(pages[page], account,
					 charge_class);
	if (resource_release_many(account, charge_class, &request, 1) < 0)
		panic("Agent context resource release");
	intr_restore(enabled);
}

int
agent_context_clear(struct proc *p)
{
	uint64 prior_provenance_labels;
	int enabled = intr_save();

	if (!agent_context_ready(p)) {
		intr_restore(enabled);
		return -1;
	}
	prior_provenance_labels = agent_provenance_current_labels(p);
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		memset((void *)p->agent_context_sidecar_kva[page], 0,
		       PAGE_SIZE);
	if (agent_provenance_merge_current(p, prior_provenance_labels) !=
	    AGENT_STATUS_OK)
		panic("Agent provenance clear restore");
	intr_restore(enabled);
	return 0;
}

int
agent_context_store(struct proc *p, uint64 slot,
		    const struct agent_context_detail *detail,
		    uint64 span_owner, int cause_pid, uint64 cause_control)
{
	struct agent_context_private_slot staged;
	struct agent_context_private_slot *target;
	struct thread *t = curr_thread();
	int actor_loop_state;
	int enabled = intr_save();

	if (detail == 0 || slot >= AGENT_CONTEXT_MAX_RECORDS ||
	    !agent_context_ready(p)) {
		intr_restore(enabled);
		return -1;
	}
	memset(&staged, 0, sizeof(staged));
	memmove(&staged.detail, detail, sizeof(staged.detail));
	staged.span_owner = span_owner;
	staged.cause_control = cause_control;
	staged.cause_branch_generation = p->context_cause_branch_generation;
	actor_loop_state = t != 0 && t->process == p ?
				   t->agent_loop_state : p->loop_state;
	staged.actor_identity = (uint64)((uint)cause_pid & 0xffffffU) |
				((uint64)((uint)(t ? t->tid : 0) & 0xffffffU) << 24) |
				((uint64)((uint)p->agent_role & 0xffU) << 48) |
				((uint64)((uint)actor_loop_state & 0xffU) << 56);
	target = agent_context_slot(p, slot);
	memmove(target, &staged, sizeof(*target));
	intr_restore(enabled);
	return 0;
}

int
agent_context_load_actor(struct proc *p, uint64 slot, int *tid, int *role,
			 int *loop_state)
{
	struct agent_context_private_slot *source;
	int enabled = intr_save();

	if (tid == 0 || role == 0 || loop_state == 0 ||
	    slot >= AGENT_CONTEXT_MAX_RECORDS || !agent_context_ready(p)) {
		intr_restore(enabled);
		return -1;
	}
	source = agent_context_slot(p, slot);
	*tid = (int)((source->actor_identity >> 24) & 0xffffffU);
	*role = (int)((source->actor_identity >> 48) & 0xffU);
	*loop_state = (int)((source->actor_identity >> 56) & 0xffU);
	intr_restore(enabled);
	return 0;
}

int
agent_context_load_detail(struct proc *p, uint64 slot,
			  struct agent_context_detail *detail)
{
	struct agent_context_private_slot *source;
	int enabled = intr_save();

	if (detail == 0 || slot >= AGENT_CONTEXT_MAX_RECORDS ||
	    !agent_context_ready(p)) {
		intr_restore(enabled);
		return -1;
	}
	source = agent_context_slot(p, slot);
	memmove(detail, &source->detail, sizeof(*detail));
	intr_restore(enabled);
	return 0;
}

int
agent_context_load_attribution(struct proc *p, uint64 slot,
			       uint64 *span_owner, int *cause_pid,
			       uint64 *cause_control, uint64 *cause_branch_generation)
{
	struct agent_context_private_slot *source;
	int enabled = intr_save();

	if (span_owner == 0 || cause_pid == 0 || cause_control == 0 ||
	    cause_branch_generation == 0 ||
	    slot >= AGENT_CONTEXT_MAX_RECORDS ||
	    !agent_context_ready(p)) {
		intr_restore(enabled);
		return -1;
	}
	source = agent_context_slot(p, slot);
	*span_owner = source->span_owner;
	*cause_pid = (int)(source->actor_identity & 0xffffffU);
	*cause_control = source->cause_control;
	*cause_branch_generation = source->cause_branch_generation;
	intr_restore(enabled);
	return 0;
}

static uint64
agent_context_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int
agent_context_layout_ok(void)
{
	uint64 cache_offset;

	if (AGENT_CONTEXT_LATEST_RESPONSE_OFFSET !=
	    sizeof(struct agent_context_header))
		return 0;
	if (AGENT_CONTEXT_RECORDS_OFFSET != PAGE_SIZE)
		return 0;
	if (AGENT_CONTEXT_LATEST_RESPONSE_OFFSET + sizeof(struct agent_result) >
		    AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET ||
	    AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET + sizeof(uint64) !=
		    AGENT_CONTEXT_RECORDS_OFFSET)
		return 0;
	cache_offset = AGENT_CONTEXT_RECORDS_OFFSET +
		       AGENT_CONTEXT_MAX_RECORDS *
			       sizeof(struct agent_context_record);
	return cache_offset <= AGENT_CONTEXT_KERNEL_PAGES * PAGE_SIZE &&
	       AGENT_CONTEXT_KERNEL_PAGES + 1 == AGENT_CONTEXT_PAGES;
}

static uint64
agent_context_user_cache_offset(void)
{
	return AGENT_CONTEXT_KERNEL_PAGES * PAGE_SIZE;
}

static uint64
agent_context_user_cache_size(void)
{
	uint64 offset = agent_context_user_cache_offset();

	return offset < AGENT_CONTEXT_SIZE ? AGENT_CONTEXT_SIZE - offset : 0;
}

static char *
agent_context_array_ptr(uint64 *kva, uint64 offset, uint64 len)
{
	uint64 page;
	uint64 page_offset;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return 0;
	page = offset / PAGE_SIZE;
	page_offset = offset % PAGE_SIZE;
	if (page >= AGENT_CONTEXT_PAGES || page_offset + len > PAGE_SIZE ||
	    kva[page] == 0)
		return 0;
	return (char *)(kva[page] + page_offset);
}

#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
static int
agent_context_array_read(uint64 *kva, uint64 offset, char *dst, uint64 len)
{
	uint64 page;
	uint64 page_offset;
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return -1;
	while (len > 0) {
		page = offset / PAGE_SIZE;
		page_offset = offset % PAGE_SIZE;
		if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
			return -1;
		n = PAGE_SIZE - page_offset;
		if (n > len)
			n = len;
		memmove(dst, (char *)(kva[page] + page_offset), n);
		dst += n;
		offset += n;
		len -= n;
	}
	return 0;
}
#endif

static int
agent_context_array_write(uint64 *kva, uint64 offset, char *src, uint64 len)
{
	uint64 page;
	uint64 page_offset;
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return -1;
	while (len > 0) {
		page = offset / PAGE_SIZE;
		page_offset = offset % PAGE_SIZE;
		if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
			return -1;
		n = PAGE_SIZE - page_offset;
		if (n > len)
			n = len;
		memmove((char *)(kva[page] + page_offset), src, n);
		src += n;
		offset += n;
		len -= n;
	}
	return 0;
}

#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
static int
agent_context_test_sync_failure(struct proc *p)
{
	uint64 marker = 0;
	uint64 offset = agent_context_user_cache_offset();

	if (agent_context_user_cache_size() < sizeof(marker) ||
	    agent_context_array_read(p->agent_ctx_kva, offset,
				     (char *)&marker, sizeof(marker)) < 0)
		return -1;
	if (marker != ~AGENT_CONTEXT_MAGIC)
		return 0;
	marker = 0;
	if (agent_context_array_write(p->agent_ctx_kva, offset,
				      (char *)&marker, sizeof(marker)) < 0)
		return -1;
	return 1;
}
#endif

static int
agent_context_publish_prepare(struct proc *p)
{
	if (!agent_context_ready(p))
		return -1;
#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
	if (agent_context_test_sync_failure(p) != 0)
		return -1;
#endif
	return 0;
}

static uint64 *
agent_context_publish_sequence(struct proc *p)
{
	return (uint64 *)agent_context_array_ptr(
		p->agent_ctx_kva, AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET,
		sizeof(uint64));
}

static void
agent_context_publish_begin(struct proc *p)
{
	uint64 *sequence = agent_context_publish_sequence(p);
	uint64 previous;

	if (sequence == 0)
		panic("Agent context publish sequence");
	previous = __atomic_fetch_add(sequence, 1, __ATOMIC_ACQ_REL);
	if ((previous & 1) != 0 || previous >= ~0ULL - 1)
		panic("Agent context overlapping publish");
}

static void
agent_context_publish_end(struct proc *p)
{
	uint64 *sequence = agent_context_publish_sequence(p);
	uint64 previous;

	if (sequence == 0)
		panic("Agent context publish sequence");
	previous = __atomic_fetch_add(sequence, 1, __ATOMIC_RELEASE);
	if ((previous & 1) == 0)
		panic("Agent context publish completion");
}

static void
agent_context_managed_zero_range(struct proc *p, uint64 offset, uint64 len)
{
	char zero[128];
	uint64 n;

	memset(zero, 0, sizeof(zero));
	while (len > 0) {
		n = len > sizeof(zero) ? sizeof(zero) : len;
		if (agent_context_array_write(p->agent_ctx_kva, offset,
					      zero, n) < 0)
			panic("Agent context managed zero");
		offset += n;
		len -= n;
	}
}

static void
agent_context_managed_zero(struct proc *p)
{
	uint64 after_sequence = AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET +
				sizeof(uint64);

	agent_context_managed_zero_range(
		p, AGENT_CONTEXT_HEADER_OFFSET,
		AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET -
			AGENT_CONTEXT_HEADER_OFFSET);
	agent_context_managed_zero_range(
		p, after_sequence,
		agent_context_user_cache_offset() - after_sequence);
}

static struct agent_context_header *
agent_context_header_ptr(struct proc *p)
{
	return (struct agent_context_header *)agent_context_array_ptr(
		p->agent_ctx_kva, AGENT_CONTEXT_HEADER_OFFSET,
		sizeof(struct agent_context_header));
}

static struct agent_result *
agent_context_latest_ptr(struct proc *p)
{
	return (struct agent_result *)agent_context_array_ptr(
		p->agent_ctx_kva, AGENT_CONTEXT_LATEST_RESPONSE_OFFSET,
		sizeof(struct agent_result));
}

static uint64
agent_context_record_offset(uint64 slot)
{
	return AGENT_CONTEXT_RECORDS_OFFSET +
	       slot * sizeof(struct agent_context_record);
}

int
agent_context_append_prepare(struct proc *p, uint64 sequence)
{
	struct thread *t = curr_thread();

	if (p == 0 || p->agent_ctx_base == 0 ||
	    p->context_path_capacity == 0 || !agent_context_layout_ok() ||
	    p->agent_current_span_id == 0 ||
	    p->agent_current_span_owner == 0 || t == 0 || t->process != p ||
	    p->agent_context_lane_owner_tid != t->tid ||
	    p->agent_context_lane_depth == 0 ||
	    sequence != p->agent_call_count + 1)
		return -1;
	return agent_context_publish_prepare(p);
}

static void
agent_context_write_record(struct proc *p, uint64 slot,
			   struct agent_context_record *record)
{
	if (p == 0 || record == 0 || slot >= p->context_path_capacity ||
	    agent_context_array_write(p->agent_ctx_kva,
				      agent_context_record_offset(slot),
				      (char *)record, sizeof(*record)) < 0)
		panic("Agent context prepared record");
}

static void
agent_context_fill_header(struct proc *p,
			  struct agent_context_header *header)
{
	memset(header, 0, sizeof(*header));
	header->magic = AGENT_CONTEXT_MAGIC;
	header->version = AGENT_CONTEXT_VERSION;
	header->capacity = p->context_path_capacity;
	header->count = p->context_path_count;
	header->head = p->context_path_head;
	header->total_calls = p->agent_call_count;
	header->oldest_sequence = p->context_path_oldest;
	header->latest_sequence = p->context_path_latest;
	header->visible_head_sequence = p->context_path_visible_head;
	header->active_path_count = p->context_active_path_count;
	header->active_path_oldest_sequence =
		p->context_active_path_oldest;
	header->dropped_records = p->agent_call_count - p->context_path_count;
	header->rollback_count = p->context_path_rollback_count;
	header->latest_response_offset = AGENT_CONTEXT_LATEST_RESPONSE_OFFSET;
	header->records_offset = AGENT_CONTEXT_RECORDS_OFFSET;
	header->user_cache_offset = agent_context_user_cache_offset();
	header->user_cache_size = agent_context_user_cache_size();
	header->current_span_id = p->agent_current_span_id;
	header->current_cause_sequence = p->agent_current_cause_sequence;
	header->latest_record_hash = p->agent_context_chain_hash;
	header->provenance_edges = p->agent_provenance_edges;
	header->workflow_lifecycle_id = p->workflow_lifecycle_id;
	header->workflow_lifecycle_generation =
		p->workflow_lifecycle_generation;
	header->branch_generation = p->context_branch_generation;
	header->eviction_policy = AGENT_CONTEXT_EVICT_FIFO;
}

static int
agent_context_new_branch(struct proc *p, uint64 *branch_generation)
{
	struct workflow_lifecycle_key key;

	if (p == 0 || !p->workflow_lifecycle_charged)
		return -1;
	key.id = p->workflow_lifecycle_id;
	key.generation = p->workflow_lifecycle_generation;
	return workflow_lifecycle_alloc_context_branch(key, branch_generation);
}

static int
agent_context_write_header_locked(struct proc *p)
{
	struct agent_context_header *header;

	if (p == 0)
		return -1;
	header = agent_context_header_ptr(p);
	if (header == 0)
		return -1;
	agent_context_fill_header(p, header);
	return 0;
}

static int
agent_context_write_latest(struct proc *p, struct agent_result *latest)
{
	struct agent_result *dst = agent_context_latest_ptr(p);

	if (dst == 0)
		return -1;
	if (latest != 0)
		memmove(dst, latest, sizeof(*dst));
	else {
		memset(dst, 0, sizeof(*dst));
		dst->version = AGENT_CALL_VERSION;
	}
	return 0;
}

int
agent_context_init(struct proc *p)
{
	uint64 span_id;
	uint64 branch_generation;

	if (p == 0 || !agent_context_layout_ok() ||
	    p->agent_control_id == 0 ||
	    (span_id = agent_observe_alloc_span_id()) == 0 ||
	    agent_context_new_branch(p, &branch_generation) < 0 ||
	    agent_context_clear(p) < 0)
		return -1;
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (p->agent_ctx_kva[i] == 0)
			return -1;
		memset((void *)p->agent_ctx_kva[i], 0, PAGE_SIZE);
	}
	p->agent_call_count = 0;
	p->context_path_count = 0;
	p->context_path_capacity = AGENT_CONTEXT_MAX_RECORDS;
	p->context_path_head = 0;
	p->context_path_oldest = 0;
	p->context_path_latest = 0;
	p->context_path_visible_head = 0;
	p->context_active_path_count = 0;
	p->context_active_path_oldest = 0;
	p->context_branch_generation = branch_generation;
	p->context_cause_branch_generation = 0;
	p->context_path_rollback_count = 0;
	p->agent_current_span_id = span_id;
	p->agent_current_span_owner = p->agent_control_id;
	p->agent_current_cause_sequence = 0;
	p->agent_current_cause_pid = 0;
	p->agent_current_cause_control = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	if (agent_context_path_index_reset(p, branch_generation) < 0)
		return -1;
	agent_context_publish_begin(p);
	if (agent_context_write_latest(p, 0) < 0 ||
	    agent_context_write_header_locked(p) < 0)
		panic("Agent context initial publish");
	agent_context_publish_end(p);
	return 0;
}

int
agent_context_map(struct proc *p)
{
	char *mem;
	uint64 ctx_kva[AGENT_CONTEXT_PAGES];
	uint64 va;
	int mapped = 0;

	if (p == 0 || p->pagetable == 0 ||
	    !agent_state_reservation_ready(p))
		return -1;
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++)
		if (p->agent_ctx_kva[i] != 0)
			return -1;
	memset(ctx_kva, 0, sizeof(ctx_kva));
	for (va = AGENT_CONTEXT_BASE;
	     va < AGENT_CONTEXT_BASE + AGENT_CONTEXT_SIZE;
	     va += PAGE_SIZE) {
		mem = kalloc_account_page(p->agent_state_account,
					  p->agent_state_charge_class);
		if (mem == 0)
			goto bad;
		memset(mem, 0, PAGE_SIZE);
		if (mappages(p->pagetable, va, PAGE_SIZE, (uint64)mem,
			     PTE_R | PTE_U |
			     (mapped < AGENT_CONTEXT_KERNEL_PAGES ? 0 : PTE_W)) != 0) {
			(void)kfree_account_page(mem, p->agent_state_account,
						 p->agent_state_charge_class);
			goto bad;
		}
		ctx_kva[mapped] = (uint64)mem;
		mapped++;
	}
	memmove(p->agent_ctx_kva, ctx_kva, sizeof(ctx_kva));
	return 0;

bad:
	if (mapped > 0)
		uvmunmap(p->pagetable, AGENT_CONTEXT_BASE, mapped, 1);
	return -1;
}

int
agent_alias_exec_context(struct proc *p, pagetable_t pagetable)
{
	int mapped = 0;

	if (p == 0 || pagetable == 0)
		return -1;
	if (!p->is_agent)
		return 0;
	if (p->agent_ctx_base == 0 ||
	    p->agent_ctx_base > MAXVA - AGENT_CONTEXT_SIZE)
		return -1;
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (p->agent_ctx_kva[i] == 0 ||
		    mappages(pagetable, p->agent_ctx_base + i * PAGE_SIZE,
			     PAGE_SIZE, p->agent_ctx_kva[i],
			     PTE_U | PTE_R |
			     (i < AGENT_CONTEXT_KERNEL_PAGES ? 0 : PTE_W)) < 0)
			goto fail;
		mapped++;
	}
	return 0;

fail:
	if (mapped != 0)
		uvmunmap(pagetable, p->agent_ctx_base, mapped, 0);
	return -1;
}

void
agent_unmap_exec_context(struct proc *p, pagetable_t pagetable)
{
	/* 映射所有权跟随 Context 状态，不跟随可变角色位。 */
	if (p != 0 && pagetable != 0 && p->agent_ctx_base != 0)
		uvmunmap(pagetable, p->agent_ctx_base, AGENT_CONTEXT_PAGES, 0);
}

void
agent_free_proc_context(struct proc *p)
{
	int mapped = 0;
	uint64 base;

	if (p == 0)
		return;
	if (!agent_lifecycle_context_lane_quiescent(p))
		panic("Agent context freed with active operation");
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (p->agent_ctx_kva[i] == 0)
			break;
		mapped++;
	}
	for (int i = mapped; i < AGENT_CONTEXT_PAGES; i++)
		if (p->agent_ctx_kva[i] != 0)
			panic("Agent context page layout");
	if (mapped != 0) {
		if (p->pagetable == 0)
			panic("Agent context without pagetable");
		base = p->agent_ctx_base != 0 ?
			       p->agent_ctx_base :
			       AGENT_CONTEXT_BASE;
		uvmunmap(p->pagetable, base, mapped, 1);
	}
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++)
		p->agent_ctx_kva[i] = 0;
	agent_context_free(p);
}

static int
agent_context_append_flags(struct proc *p, struct agent_op *op,
			   struct agent_result *latest, uint64 tick,
			   uint64 flags, int authority_effect,
			   int causal_audit,
			   struct agent_evidence_context_reservation *reservation,
			   struct agent_evidence_security_reservation *
				   security_reservation,
			   uint64 *evidence_ticket_out)
{
	struct agent_context_append_receipt receipt;
	struct agent_context_detail detail;
	struct agent_context_record record;
	struct thread *t = curr_thread();
	uint64 slot;

	if (p == 0 || op == 0 || latest == 0 || p->agent_ctx_base == 0 ||
	    p->context_path_capacity == 0 || !agent_context_layout_ok() ||
	    p->agent_current_span_id == 0 ||
	    p->agent_current_span_owner == 0 || t == 0 || t->process != p ||
	    p->agent_context_lane_owner_tid != t->tid ||
	    p->agent_context_lane_depth == 0 ||
	    latest->sequence != p->agent_call_count)
		return -1;
	if ((reservation != 0 && security_reservation != 0) ||
	    ((reservation != 0 || security_reservation != 0) &&
	     (evidence_ticket_out == 0 ||
	      agent_observe_recording_suppressed(p))) ||
	    (reservation != 0 && !reservation->active) ||
	    (security_reservation != 0 && !security_reservation->active))
		return -1;
	slot = p->context_path_head % p->context_path_capacity;

	memset(&record, 0, sizeof(record));
	record.sequence = latest->sequence;
	record.request_id = latest->request_id;
	record.cause_sequence = p->agent_current_cause_sequence;
	record.span_id = p->agent_current_span_id;
	record.branch_generation = p->context_branch_generation;
	record.path_parent_sequence = p->context_path_visible_head;
	record.arg0 = op->arg0;
	record.value0 = latest->value0;
	record.value1 = latest->value1;
	record.value2 = latest->value2;
	record.tick = tick;
	record.flags = agent_provenance_context_flags(
		p, latest->request_id, latest->tool_id, flags);
	if (strlen(op->payload) >= sizeof(record.payload) ||
	    strlen(latest->result) >= sizeof(record.result))
		record.flags |= AGENT_CONTEXT_RECORD_F_TRUNCATED;
	record.tool_id = latest->tool_id;
	record.status = latest->status;
	safestrcpy(record.payload, op->payload, sizeof(record.payload));
	safestrcpy(record.result, latest->result, sizeof(record.result));
	record.prev_hash = p->agent_context_chain_hash;
	record.record_hash = agent_context_record_hash(&record);
	if (agent_context_append_receipt_prepare(p, &record, slot, &receipt) < 0)
		return -1;

	memset(&detail, 0, sizeof(detail));
	detail.sequence = latest->sequence;
	detail.flags = record.flags;
	memmove(&detail.op, op, sizeof(*op));
	memmove(&detail.result, latest, sizeof(*latest));
	if (agent_context_store(p, slot, &detail,
				p->agent_current_span_owner,
				p->agent_current_cause_pid,
				p->agent_current_cause_control) < 0)
		return -1;
	agent_context_publish_begin(p);
	agent_context_write_record(p, slot, &record);
	if (p->context_path_count < p->context_path_capacity) {
		if (p->context_path_count == 0)
			p->context_path_oldest = latest->sequence;
		p->context_path_count++;
	} else {
		p->context_path_oldest =
			latest->sequence - p->context_path_capacity + 1;
	}
	p->context_path_latest = latest->sequence;
	p->context_path_head = (slot + 1) % p->context_path_capacity;
	if (record.cause_sequence != 0)
		p->agent_provenance_edges++;
	if (agent_context_append_receipt_commit(p, &record, &receipt) < 0)
		panic("Agent context append receipt");
	p->agent_context_chain_hash = record.record_hash;
	p->agent_current_cause_sequence = latest->sequence;
	p->context_cause_branch_generation = record.branch_generation;
	p->agent_current_cause_pid = p->pid;
	p->agent_current_cause_control = p->agent_control_id;
	p->agent_current_span_id = record.span_id;
	agent_provenance_context_committed(
		p, record.request_id, record.tool_id, record.flags);
	if (agent_context_write_latest(p, latest) < 0 ||
	    agent_context_write_header_locked(p) < 0)
		panic("Agent context direct publish");
	/* Reserved terminal and denial records stay behind an odd sequence until
	 * their canonical Evidence ticket is committed.  The release below is the
	 * only point at which direct readers may accept the new Context snapshot. */
	if (reservation != 0)
		*evidence_ticket_out =
			agent_observe_commit_context_reserved_ticket(
				p, &record, authority_effect, causal_audit,
				reservation);
	else if (security_reservation != 0)
		*evidence_ticket_out =
			agent_observe_commit_security_reserved_ticket(
				p, &record, security_reservation);
	agent_context_publish_end(p);
	if (reservation != 0 || security_reservation != 0) {
		agent_observe_publish_context_ticket(
			p, &record, authority_effect, causal_audit,
			*evidence_ticket_out);
	} else if (evidence_ticket_out != 0) {
		if (agent_observe_record_context_ticket(
			    p, &record, authority_effect, causal_audit,
			    evidence_ticket_out) < 0)
			return -1;
	} else {
		agent_observe_record_context(
			p, &record, authority_effect, causal_audit);
	}
	return 0;
}

int
agent_context_append(struct proc *p, struct agent_op *op,
		     struct agent_result *latest, uint64 tick,
		     int authority_effect)
{
	return agent_context_append_flags(
		p, op, latest, tick, AGENT_CONTEXT_RECORD_F_SYSTEM,
		authority_effect, 0, 0, 0, 0);
}

int
agent_context_append_ticket(struct proc *p, struct agent_op *op,
			    struct agent_result *latest, uint64 tick,
			    int authority_effect, uint64 *evidence_ticket_out)
{
	if (evidence_ticket_out == 0)
		return -1;
	*evidence_ticket_out = 0;
	return agent_context_append_flags(
		p, op, latest, tick, AGENT_CONTEXT_RECORD_F_SYSTEM,
		authority_effect, 0, 0, 0, evidence_ticket_out);
}

int
agent_context_append_reserved_ticket(
	struct proc *p, struct agent_op *op, struct agent_result *latest,
	uint64 tick, int authority_effect,
	struct agent_evidence_context_reservation *reservation,
	uint64 *evidence_ticket_out)
{
	if (reservation == 0 || evidence_ticket_out == 0)
		return -1;
	*evidence_ticket_out = 0;
	return agent_context_append_flags(
		p, op, latest, tick, AGENT_CONTEXT_RECORD_F_SYSTEM,
		authority_effect, 0, reservation, 0,
		evidence_ticket_out);
}

static void
agent_context_result_text(struct agent_result *result, char *text)
{
	safestrcpy(result->result, text, sizeof(result->result));
}

static int
agent_context_append_system_class(struct proc *p, int tool_id,
				  uint64 request_id, uint64 arg0,
				  char *payload, char *result, int status,
				  uint64 value0, uint64 value1,
				  uint64 value2, int causal_audit)
{
	struct agent_op op;
	struct agent_result latest;
	int append_status;

	if (p == 0 || !p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = tool_id;
	op.request_id = request_id;
	op.arg0 = arg0;
	safestrcpy(op.payload, payload, sizeof(op.payload));
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = status;
	latest.tool_id = tool_id;
	latest.request_id = request_id;
	latest.sequence = p->agent_call_count + 1;
	latest.value0 = value0;
	latest.value1 = value1;
	latest.value2 = value2;
	agent_context_result_text(&latest, result);
	if (agent_context_append_prepare(p, latest.sequence) < 0) {
		agent_lifecycle_context_lane_leave(p);
		return AGENT_STATUS_NO_SPACE;
	}
	p->agent_call_count = latest.sequence;
	append_status = agent_context_append_flags(
		p, &op, &latest, agent_context_ticks(),
		AGENT_CONTEXT_RECORD_F_SYSTEM, 0, causal_audit, 0, 0, 0);
	if (append_status < 0)
		p->agent_call_count--;
	agent_lifecycle_context_lane_leave(p);
	return append_status < 0 ? AGENT_STATUS_NO_SPACE : 0;
}

int
agent_context_append_system(struct proc *p, int tool_id, uint64 request_id,
			    uint64 arg0, char *payload, char *result,
			    int status, uint64 value0, uint64 value1,
			    uint64 value2)
{
	return agent_context_append_system_class(
		p, tool_id, request_id, arg0, payload, result, status,
		value0, value1, value2, 0);
}

int
agent_context_append_system_causal(
	struct proc *p, int tool_id, uint64 request_id, uint64 arg0,
	char *payload, char *result, int status, uint64 value0,
	uint64 value1, uint64 value2)
{
	return agent_context_append_system_class(
		p, tool_id, request_id, arg0, payload, result, status,
		value0, value1, value2, 1);
}

int
agent_context_append_security_denial_record(
	struct proc *p, const struct agent_provenance_request *request,
	const struct agent_provenance_decision *decision,
	struct agent_evidence_security_reservation *reservation,
	uint64 *evidence_ticket_out)
{
	struct agent_op op;
	struct agent_result latest;
	int append_status;

	if (p == 0 || request == 0 || decision == 0 || reservation == 0 ||
	    evidence_ticket_out == 0 || !reservation->active || !p->is_agent ||
	    decision->status == AGENT_STATUS_OK)
		return -1;
	*evidence_ticket_out = 0;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = request->tool_id;
	op.request_id = request->request_id;
	op.arg0 = request->contract_generation;
	safestrcpy(op.payload, "provenance", sizeof(op.payload));
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = decision->status;
	latest.tool_id = request->tool_id;
	latest.request_id = request->request_id;
	latest.sequence = p->agent_call_count + 1;
	latest.value0 = request->source_node_id;
	latest.value1 = request->target_node_id;
	latest.value2 = decision->reason;
	agent_context_result_text(&latest, "security_denied");
	if (agent_context_append_prepare(p, latest.sequence) < 0) {
		agent_lifecycle_context_lane_leave(p);
		return AGENT_STATUS_NO_SPACE;
	}
	p->agent_call_count = latest.sequence;
	append_status = agent_context_append_flags(
		p, &op, &latest, agent_context_ticks(),
		AGENT_CONTEXT_RECORD_F_SYSTEM |
			AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL,
		1, 1, 0, reservation, evidence_ticket_out);
	if (append_status < 0)
		p->agent_call_count--;
	agent_lifecycle_context_lane_leave(p);
	return append_status < 0 ? AGENT_STATUS_NO_SPACE : AGENT_STATUS_OK;
}

int
sys_context_push(uint64 recordaddr)
{
	struct proc *p = curr_proc();
	struct agent_context_record record;
	struct agent_op op;
	struct agent_result latest;
	int result = 0;

	if (!p->is_agent)
		return -1;
	if (copyin(p->pagetable, (char *)&record, recordaddr,
		   sizeof(record)) < 0)
		return -1;
	if (record.span_id != 0 || record.cause_sequence != 0)
		return AGENT_STATUS_BAD_PARAM;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	record.payload[sizeof(record.payload) - 1] = 0;
	record.result[sizeof(record.result) - 1] = 0;
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = record.tool_id;
	op.request_id = record.request_id;
	op.arg0 = record.arg0;
	safestrcpy(op.payload, record.payload, sizeof(op.payload));
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = record.status;
	latest.tool_id = record.tool_id;
	latest.request_id = record.request_id;
	latest.sequence = p->agent_call_count + 1;
	latest.value0 = record.value0;
	latest.value1 = record.value1;
	latest.value2 = record.value2;
	agent_context_result_text(&latest,
				  record.result[0] ? record.result : "manual");
	if (agent_context_append_prepare(p, latest.sequence) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	p->agent_call_count = latest.sequence;
	if (agent_context_append_flags(p, &op, &latest,
				       agent_context_ticks(),
				       AGENT_CONTEXT_RECORD_F_MANUAL, 0, 0,
				       0, 0, 0) < 0)
		result = AGENT_STATUS_NO_SPACE;
	if (result != 0)
		p->agent_call_count--;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int
sys_context_query(uint64 start_sequence, uint64 outaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_path_summary query_summary;
	struct agent_context_path_index *path_index;
	struct agent_context_record record;
	uint64 cursor;
	uint64 successor;
	uint64 previous_sequence = 0;
	uint64 previous_hash = 0;
	uint64 records_examined = 0;
	int copied = 0;
	int first = 1;
	int finished = 0;
	int terminal;
	int result;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (max == 0 || p->context_active_path_count == 0) {
		result = 0;
		goto out;
	}
	if (agent_context_path_summary_capture(p, &query_summary) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	path_index = agent_context_path_index(p);
	if (start_sequence > query_summary.head_sequence) {
		result = 0;
		goto out;
	}
	cursor = query_summary.oldest_sequence;
	while (records_examined < query_summary.count && copied < max) {
		if (agent_context_read_record(
			    p, (cursor - 1) % p->context_path_capacity,
			    &record) < 0) {
			result = AGENT_STATUS_NO_SPACE;
			goto out;
		}
		records_examined++;
		if (record.sequence != cursor || record.record_hash == 0 ||
		    record.record_hash != agent_context_record_hash(&record) ||
		    record.branch_generation == 0 ||
		    record.branch_generation > query_summary.branch_generation) {
			result = AGENT_STATUS_NO_SPACE;
			goto out;
		}
		terminal = record.path_parent_sequence == 0 ||
			   record.path_parent_sequence < p->context_path_oldest;
		if (first) {
			if (cursor != query_summary.oldest_sequence || !terminal ||
			    ((record.path_parent_sequence == 0) !=
			     (record.prev_hash == 0))) {
				result = AGENT_STATUS_NO_SPACE;
				goto out;
			}
		} else if (terminal ||
			   record.path_parent_sequence != previous_sequence ||
			   record.prev_hash != previous_hash) {
			result = AGENT_STATUS_NO_SPACE;
			goto out;
		}
		successor = path_index->successors[
			(record.sequence - 1) % p->context_path_capacity];
		if (successor == 0) {
			if (record.sequence != query_summary.head_sequence ||
			    record.record_hash != query_summary.head_hash ||
			    records_examined != query_summary.count) {
				result = AGENT_STATUS_NO_SPACE;
				goto out;
			}
			finished = 1;
		} else if (successor <= record.sequence ||
			   successor > query_summary.head_sequence ||
			   records_examined >= query_summary.count) {
			result = AGENT_STATUS_NO_SPACE;
			goto out;
		}
		if (kernel_work_checkpoint(1) < 0) {
			result = -1;
			goto out;
		}
		if (!agent_context_path_summary_matches(p, &query_summary)) {
			result = AGENT_STATUS_STALE;
			goto out;
		}
		if ((start_sequence == 0 || record.sequence >= start_sequence) &&
		    copyout(p->pagetable,
			    outaddr + copied *
					      sizeof(struct agent_context_record),
			    (char *)&record, sizeof(record)) < 0) {
			result = -1;
			goto out;
		}
		if (start_sequence == 0 || record.sequence >= start_sequence)
			copied++;
		if (finished || copied == max)
			break;
		previous_sequence = record.sequence;
		previous_hash = record.record_hash;
		cursor = successor;
		first = 0;
	}
	if (!agent_context_path_summary_matches(p, &query_summary)) {
		result = AGENT_STATUS_STALE;
		goto out;
	}
	if (copied < max && !finished) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	result = copied;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int
sys_context_snapshot(uint64 headeraddr, uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_header *header;
	int result;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (agent_context_publish_prepare(p) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	header = agent_context_header_ptr(p);
	if (header == 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (headeraddr != 0 &&
	    copyout(p->pagetable, headeraddr, (char *)header,
		    sizeof(*header)) < 0) {
		result = -1;
		goto out;
	}
	result = max == 0 || recordsaddr == 0 ?
		       0 : sys_context_query(0, recordsaddr, max);
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int
sys_context_detail(uint64 sequence, uint64 detailaddr)
{
	struct proc *p = curr_proc();
	struct agent_context_detail detail;
	uint64 slot;
	int result;

	if (!p->is_agent)
		return -1;
	if (detailaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (p->context_path_count == 0 ||
	    sequence < p->context_path_oldest ||
	    sequence > p->context_path_latest) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	slot = (sequence - 1) % p->context_path_capacity;
	if (agent_context_load_detail(p, slot, &detail) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (detail.sequence != sequence) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (copyout(p->pagetable, detailaddr, (char *)&detail,
		    sizeof(detail)) < 0) {
		result = -1;
		goto out;
	}
	result = 0;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int
sys_context_rollback(uint64 sequence)
{
	struct proc *p = curr_proc();
	struct agent_context_path_summary source_summary;
	struct agent_context_path_index *path_index;
	struct agent_context_record record;
	struct agent_result latest;
	uint64 span_owner;
	uint64 cause_control;
	uint64 cause_branch_generation;
	uint64 slot;
	uint64 branch_generation;
	uint64 active_path_count;
	uint64 active_path_oldest;
	uint64 rebuilt_path_count;
	uint64 rebuilt_path_oldest;
	int cause_pid;
	int result;

	if (!p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (agent_context_path_summary_capture(p, &source_summary) < 0 ||
	    p->context_path_count == 0 ||
	    sequence < p->context_path_oldest ||
	    sequence > p->context_path_latest) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	slot = (sequence - 1) % p->context_path_capacity;
	if (agent_context_read_record(p, slot, &record) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (record.sequence != sequence) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (agent_context_load_attribution(p, slot, &span_owner, &cause_pid,
					   &cause_control,
					   &cause_branch_generation) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	(void)cause_branch_generation;
	if (record.span_id == 0 || span_owner == 0) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (agent_context_active_measure(p, sequence, &active_path_count,
					 &active_path_oldest) < 0) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (agent_context_publish_prepare(p) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (agent_context_new_branch(p, &branch_generation) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (branch_generation <= source_summary.branch_generation ||
	    !agent_context_path_summary_matches(p, &source_summary)) {
		result = AGENT_STATUS_STALE;
		goto out;
	}
	path_index = agent_context_path_index(p);
	if (path_index == 0)
		panic("Agent context rollback path index");
	agent_context_publish_begin(p);
	path_index->magic = 0;
	if (agent_context_active_rebuild(
		    p, sequence, path_index->successors,
		    AGENT_CONTEXT_MAX_RECORDS, &rebuilt_path_count,
		    &rebuilt_path_oldest) < 0 ||
	    rebuilt_path_count != active_path_count ||
	    rebuilt_path_oldest != active_path_oldest)
		panic("Agent context rollback path receipt");
	p->context_branch_generation = branch_generation;
	p->context_cause_branch_generation = record.branch_generation;
	p->context_path_visible_head = sequence;
	p->context_active_path_count = active_path_count;
	p->context_active_path_oldest = active_path_oldest;
	path_index->summary.workflow_lifecycle_id =
		source_summary.workflow_lifecycle_id;
	path_index->summary.workflow_lifecycle_generation =
		source_summary.workflow_lifecycle_generation;
	path_index->summary.branch_generation = branch_generation;
	path_index->summary.head_sequence = sequence;
	path_index->summary.head_hash = record.record_hash;
	path_index->summary.count = active_path_count;
	path_index->summary.oldest_sequence = active_path_oldest;
	p->context_path_rollback_count++;
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = record.status;
	latest.tool_id = record.tool_id;
	latest.request_id = record.request_id;
	latest.sequence = record.sequence;
	latest.value0 = record.value0;
	latest.value1 = record.value1;
	latest.value2 = record.value2;
	agent_context_result_text(&latest, "rollback");
	p->agent_current_cause_sequence = record.sequence;
	p->agent_current_cause_pid = p->pid;
	p->agent_current_cause_control = p->agent_control_id;
	p->agent_context_chain_hash = record.record_hash;
	p->agent_current_span_id = record.span_id;
	p->agent_current_span_owner = span_owner;
	agent_provenance_context_restore(p, record.flags);
	path_index->magic = AGENT_CONTEXT_PATH_INDEX_MAGIC;
	if (!agent_context_path_index_matches(p, path_index))
		panic("Agent context rollback summary");
	if (agent_context_write_latest(p, &latest) < 0 ||
	    agent_context_write_header_locked(p) < 0)
		panic("Agent context rollback publish");
	agent_context_publish_end(p);
	result = 0;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int
sys_context_clear(void)
{
	struct proc *p = curr_proc();
	uint64 span_id;
	uint64 branch_generation;
	int result;

	if (!p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (p->agent_control_id == 0 ||
	    agent_context_publish_prepare(p) < 0 ||
	    (span_id = agent_observe_alloc_span_id()) == 0 ||
	    agent_context_new_branch(p, &branch_generation) < 0 ||
	    agent_context_clear(p) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	p->agent_call_count = 0;
	p->context_path_count = 0;
	p->context_path_head = 0;
	p->context_path_oldest = 0;
	p->context_path_latest = 0;
	p->context_path_visible_head = 0;
	p->context_active_path_count = 0;
	p->context_active_path_oldest = 0;
	p->context_branch_generation = branch_generation;
	p->context_cause_branch_generation = 0;
	p->context_path_rollback_count = 0;
	p->agent_current_span_id = span_id;
	p->agent_current_span_owner = p->agent_control_id;
	p->agent_current_cause_sequence = 0;
	p->agent_current_cause_pid = 0;
	p->agent_current_cause_control = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	if (agent_context_path_index_reset(p, branch_generation) < 0)
		panic("Agent context cleared path index");
	agent_context_publish_begin(p);
	agent_context_managed_zero(p);
	if (agent_context_write_latest(p, 0) < 0 ||
	    agent_context_write_header_locked(p) < 0)
		panic("Agent context clear publish");
	agent_context_publish_end(p);
	result = 0;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}
