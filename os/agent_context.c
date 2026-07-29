#include "agent_context.h"
#include "agent_context_path.h"
#include "agent_internal.h"
#include "defs.h"
#include "timer.h"
#include "trap.h"

struct agent_context_private_slot {
	struct agent_context_detail detail;
	uint64 span_owner;
	uint64 cause_control;
	uint64 cause_branch_generation;
	uint64 actor_identity;
};

#define AGENT_CONTEXT_SLOTS_PER_PAGE \
	(PAGE_SIZE / sizeof(struct agent_context_private_slot))

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
_Static_assert(AGENT_STATE_PAGE_COUNT ==
		       AGENT_CONTEXT_SIDECAR_PAGE_COUNT +
			       2U * AGENT_CONTEXT_PAGES,
	       "Agent state accounting must cover sidecar, user, and shadow pages");

static void
agent_context_metadata_reset(struct proc *p)
{
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		p->agent_context_sidecar_kva[page] = 0;
	p->agent_state_account = resource_account_none();
	p->agent_state_charge_class = RESOURCE_CHARGE_CLASS_COUNT;
	p->agent_state_charged_pages = 0;
}

void
agent_context_proc_activate(struct proc *p)
{
	if (p == 0)
		return;
	p->agent_ctx_base = AGENT_CONTEXT_BASE;
	p->agent_ctx_size = AGENT_CONTEXT_SIZE;
}

void
agent_context_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	if (!agent_context_is_empty(p))
		panic("Agent context reset with live state");
	p->agent_ctx_base = 0;
	p->agent_ctx_size = 0;
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
	p->context_path_dropped = 0;
	p->context_path_rollback_count = 0;
	p->context_eviction_policy = 0;
	p->latest_response_offset = 0;
	p->records_offset = 0;
	p->agent_current_span_id = 0;
	p->agent_current_span_owner = 0;
	p->agent_current_cause_sequence = 0;
	p->agent_current_cause_pid = 0;
	p->agent_current_cause_control = 0;
	p->agent_context_chain_hash = 0;
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
	for (uint page = 0; page < AGENT_CONTEXT_PAGES; page++)
		if (p->agent_ctx_kva[page] != 0 ||
		    p->agent_shadow_kva[page] != 0)
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
	return 1;
}

static int
agent_context_ready(struct proc *p)
{
	if (!agent_state_reservation_ready(p))
		return 0;
	for (uint page = 0; page < AGENT_CONTEXT_PAGES; page++)
		if (p->agent_ctx_kva[page] == 0 ||
		    p->agent_shadow_kva[page] == 0)
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

int
agent_context_alloc(struct proc *p)
{
	void *pages[AGENT_CONTEXT_SIDECAR_PAGE_COUNT];
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
	while (allocated < AGENT_CONTEXT_SIDECAR_PAGE_COUNT) {
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
	void *pages[AGENT_CONTEXT_SIDECAR_PAGE_COUNT];
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
	agent_context_metadata_reset(p);
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		(void)kfree_account_page(pages[page], account,
					 charge_class);
	if (resource_release_many(account, charge_class, &request, 1) < 0)
		panic("Agent context resource release");
	intr_restore(enabled);
}

int
agent_context_clear(struct proc *p)
{
	int enabled = intr_save();

	if (!agent_context_ready(p)) {
		intr_restore(enabled);
		return -1;
	}
	for (uint page = 0; page < AGENT_CONTEXT_SIDECAR_PAGE_COUNT; page++)
		memset((void *)p->agent_context_sidecar_kva[page], 0,
		       PAGE_SIZE);
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
	cache_offset = AGENT_CONTEXT_RECORDS_OFFSET +
		       AGENT_CONTEXT_MAX_RECORDS *
			       sizeof(struct agent_context_record);
	return cache_offset < AGENT_CONTEXT_SIZE;
}

static uint64
agent_context_user_cache_offset(void)
{
	return AGENT_CONTEXT_RECORDS_OFFSET +
	       AGENT_CONTEXT_MAX_RECORDS *
		       sizeof(struct agent_context_record);
}

static uint64
agent_context_user_cache_size(void)
{
	uint64 offset = agent_context_user_cache_offset();

	return offset < AGENT_CONTEXT_SIZE ? AGENT_CONTEXT_SIZE - offset : 0;
}

static void
agent_context_free_shadow(uint64 *kva,
			  struct resource_account_handle account,
			  enum resource_charge_class charge_class)
{
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (kva[i] != 0) {
			(void)kfree_account_page((void *)kva[i], account,
						 charge_class);
			kva[i] = 0;
		}
	}
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

struct agent_context_publish_range {
	uint64 offset;
	uint64 length;
};

static int
agent_context_array_range_ready(uint64 *kva, uint64 offset, uint64 len)
{
	uint64 page;
	uint64 page_offset;
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return 0;
	while (len > 0) {
		page = offset / PAGE_SIZE;
		page_offset = offset % PAGE_SIZE;
		if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
			return 0;
		n = PAGE_SIZE - page_offset;
		if (n > len)
			n = len;
		offset += n;
		len -= n;
	}
	return 1;
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

/*
 * Validate every fixed mapping before changing the trusted shadow.  The
 * Context lane prevents teardown or remapping between prepare and commit, so
 * the later mirror copies are infallible unless a kernel invariant is broken.
 */
static int
agent_context_publish_prepare(struct proc *p,
			      const struct agent_context_publish_range *ranges,
			      uint count)
{
	if (!agent_context_ready(p) || ranges == 0 || count == 0)
		return -1;
	for (uint i = 0; i < count; i++)
		if (ranges[i].length == 0 ||
		    !agent_context_array_range_ready(p->agent_shadow_kva,
					     ranges[i].offset,
					     ranges[i].length) ||
		    !agent_context_array_range_ready(p->agent_ctx_kva,
					     ranges[i].offset,
					     ranges[i].length))
			return -1;
#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
	if (agent_context_test_sync_failure(p) != 0)
		return -1;
#endif
	return 0;
}

static int
agent_context_sync_range(struct proc *p, uint64 offset, uint64 len)
{
	char buf[128];
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return -1;
	while (len > 0) {
		n = len > sizeof(buf) ? sizeof(buf) : len;
		if (agent_context_array_read(p->agent_shadow_kva, offset, buf, n) <
			    0 ||
		    agent_context_array_write(p->agent_ctx_kva, offset, buf, n) <
			    0)
			return -1;
		offset += n;
		len -= n;
	}
	return 0;
}

static void
agent_context_publish_commit(struct proc *p,
			     const struct agent_context_publish_range *ranges,
			     uint count)
{
	for (uint i = 0; i < count; i++) {
		if (!agent_context_array_range_ready(p->agent_shadow_kva,
					      ranges[i].offset,
					      ranges[i].length) ||
		    !agent_context_array_range_ready(p->agent_ctx_kva,
					      ranges[i].offset,
					      ranges[i].length) ||
		    agent_context_sync_range(p, ranges[i].offset,
					 ranges[i].length) < 0)
			panic("Agent context prepared commit");
	}
}

static void
agent_context_shadow_zero(struct proc *p, uint64 offset, uint64 len)
{
	char zero[128];
	uint64 n;

	memset(zero, 0, sizeof(zero));
	while (len > 0) {
		n = len > sizeof(zero) ? sizeof(zero) : len;
		if (agent_context_array_write(p->agent_shadow_kva, offset,
					      zero, n) < 0)
			panic("Agent context prepared zero");
		offset += n;
		len -= n;
	}
}

static struct agent_context_header *
agent_context_header_ptr(struct proc *p)
{
	return (struct agent_context_header *)agent_context_array_ptr(
		p->agent_shadow_kva, AGENT_CONTEXT_HEADER_OFFSET,
		sizeof(struct agent_context_header));
}

static struct agent_result *
agent_context_latest_ptr(struct proc *p)
{
	return (struct agent_result *)agent_context_array_ptr(
		p->agent_shadow_kva, AGENT_CONTEXT_LATEST_RESPONSE_OFFSET,
		sizeof(struct agent_result));
}

static uint64
agent_context_record_offset(struct proc *p, uint64 slot)
{
	return p->records_offset + slot * sizeof(struct agent_context_record);
}

static void
agent_context_append_ranges(struct proc *p,
			    struct agent_context_publish_range ranges[3])
{
	uint64 slot = p->context_path_head % p->context_path_capacity;

	ranges[0].offset = agent_context_record_offset(p, slot);
	ranges[0].length = sizeof(struct agent_context_record);
	ranges[1].offset = AGENT_CONTEXT_LATEST_RESPONSE_OFFSET;
	ranges[1].length = sizeof(struct agent_result);
	ranges[2].offset = AGENT_CONTEXT_HEADER_OFFSET;
	ranges[2].length = sizeof(struct agent_context_header);
}

static void
agent_context_full_ranges(struct agent_context_publish_range ranges[2])
{
	ranges[0].offset = sizeof(struct agent_context_header);
	ranges[0].length = agent_context_user_cache_offset() - ranges[0].offset;
	ranges[1].offset = AGENT_CONTEXT_HEADER_OFFSET;
	ranges[1].length = sizeof(struct agent_context_header);
}

int
agent_context_append_prepare(struct proc *p, uint64 sequence)
{
	struct agent_context_publish_range ranges[3];
	struct thread *t = curr_thread();

	if (p == 0 || p->agent_ctx_base == 0 ||
	    p->context_path_capacity == 0 || !agent_context_layout_ok() ||
	    p->agent_current_span_id == 0 ||
	    p->agent_current_span_owner == 0 || t == 0 || t->process != p ||
	    p->agent_context_lane_owner_tid != t->tid ||
	    p->agent_context_lane_depth == 0 ||
	    sequence != p->agent_call_count + 1)
		return -1;
	agent_context_append_ranges(p, ranges);
	return agent_context_publish_prepare(p, ranges, 3);
}

static void
agent_context_write_record_shadow(struct proc *p, uint64 slot,
				  struct agent_context_record *record)
{
	if (p == 0 || record == 0 || slot >= p->context_path_capacity ||
	    agent_context_array_write(p->agent_shadow_kva,
				      agent_context_record_offset(p, slot),
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
	header->dropped_records = p->context_path_dropped;
	header->rollback_count = p->context_path_rollback_count;
	header->latest_response_offset = p->latest_response_offset;
	header->records_offset = p->records_offset;
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
	header->eviction_policy = p->context_eviction_policy;
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
	return agent_context_sync_range(p, AGENT_CONTEXT_HEADER_OFFSET,
					sizeof(*header));
}

static void
agent_context_write_header_shadow(struct proc *p)
{
	struct agent_context_header *header;

	header = agent_context_header_ptr(p);
	if (header == 0)
		panic("Agent context prepared header");
	agent_context_fill_header(p, header);
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
	return agent_context_sync_range(p, AGENT_CONTEXT_LATEST_RESPONSE_OFFSET,
					sizeof(*dst));
}

static void
agent_context_write_latest_shadow(struct proc *p,
				  const struct agent_result *latest)
{
	struct agent_result *dst = agent_context_latest_ptr(p);

	if (dst == 0)
		panic("Agent context prepared latest");
	if (latest != 0)
		memmove(dst, latest, sizeof(*dst));
	else {
		memset(dst, 0, sizeof(*dst));
		dst->version = AGENT_CALL_VERSION;
	}
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
		if (p->agent_shadow_kva[i] == 0 || p->agent_ctx_kva[i] == 0)
			return -1;
		memset((void *)p->agent_shadow_kva[i], 0, PAGE_SIZE);
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
	p->context_path_dropped = 0;
	p->context_path_rollback_count = 0;
	p->context_eviction_policy = AGENT_CONTEXT_EVICT_FIFO;
	p->latest_response_offset = AGENT_CONTEXT_LATEST_RESPONSE_OFFSET;
	p->records_offset = AGENT_CONTEXT_RECORDS_OFFSET;
	p->agent_current_span_id = span_id;
	p->agent_current_span_owner = p->agent_control_id;
	p->agent_current_cause_sequence = 0;
	p->agent_current_cause_pid = 0;
	p->agent_current_cause_control = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	if (agent_context_write_header_locked(p) < 0)
		return -1;
	return agent_context_write_latest(p, 0);
}

int
agent_context_map(struct proc *p)
{
	char *mem;
	char *shadow;
	uint64 ctx_kva[AGENT_CONTEXT_PAGES];
	uint64 shadow_kva[AGENT_CONTEXT_PAGES];
	uint64 va;
	int mapped = 0;

	if (p == 0 || p->pagetable == 0 ||
	    !agent_state_reservation_ready(p))
		return -1;
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++)
		if (p->agent_ctx_kva[i] != 0 || p->agent_shadow_kva[i] != 0)
			return -1;
	memset(ctx_kva, 0, sizeof(ctx_kva));
	memset(shadow_kva, 0, sizeof(shadow_kva));
	for (va = AGENT_CONTEXT_BASE;
	     va < AGENT_CONTEXT_BASE + AGENT_CONTEXT_SIZE;
	     va += PAGE_SIZE) {
		mem = kalloc_account_page(p->agent_state_account,
					  p->agent_state_charge_class);
		if (mem == 0)
			goto bad;
		shadow = kalloc_account_page(p->agent_state_account,
					     p->agent_state_charge_class);
		if (shadow == 0) {
			(void)kfree_account_page(mem, p->agent_state_account,
						 p->agent_state_charge_class);
			goto bad;
		}
		memset(mem, 0, PAGE_SIZE);
		memset(shadow, 0, PAGE_SIZE);
		if (mappages(p->pagetable, va, PAGE_SIZE, (uint64)mem,
			     PTE_R | PTE_W | PTE_U) != 0) {
			(void)kfree_account_page(mem, p->agent_state_account,
						 p->agent_state_charge_class);
			(void)kfree_account_page(shadow, p->agent_state_account,
						 p->agent_state_charge_class);
			goto bad;
		}
		ctx_kva[mapped] = (uint64)mem;
		shadow_kva[mapped] = (uint64)shadow;
		mapped++;
	}
	memmove(p->agent_ctx_kva, ctx_kva, sizeof(ctx_kva));
	memmove(p->agent_shadow_kva, shadow_kva, sizeof(shadow_kva));
	return 0;

bad:
	if (mapped > 0)
		uvmunmap(p->pagetable, AGENT_CONTEXT_BASE, mapped, 1);
	agent_context_free_shadow(shadow_kva, p->agent_state_account,
				  p->agent_state_charge_class);
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
			     PTE_U | PTE_R | PTE_W) < 0)
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
	/* Mapping ownership follows Context state, not the mutable role bit. */
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
	agent_context_free_shadow(p->agent_shadow_kva,
				  p->agent_state_account,
				  p->agent_state_charge_class);
	agent_context_free(p);
}

static int
agent_context_append_flags(struct proc *p, struct agent_op *op,
			   struct agent_result *latest, uint64 tick,
			   uint64 flags, int authority_effect,
			   int causal_audit)
{
	struct agent_context_detail detail;
	struct agent_context_publish_range ranges[3];
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
	slot = p->context_path_head % p->context_path_capacity;
	agent_context_append_ranges(p, ranges);

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
	record.flags = flags;
	if (strlen(op->payload) >= sizeof(record.payload) ||
	    strlen(latest->result) >= sizeof(record.result))
		record.flags |= AGENT_CONTEXT_RECORD_F_TRUNCATED;
	record.tool_id = latest->tool_id;
	record.status = latest->status;
	safestrcpy(record.payload, op->payload, sizeof(record.payload));
	safestrcpy(record.result, latest->result, sizeof(record.result));
	record.prev_hash = p->agent_context_chain_hash;
	record.record_hash = agent_context_record_hash(&record);

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
	agent_context_write_record_shadow(p, slot, &record);
	if (p->context_path_count < p->context_path_capacity) {
		if (p->context_path_count == 0)
			p->context_path_oldest = latest->sequence;
		p->context_path_count++;
	} else {
		p->context_path_dropped++;
		p->context_path_oldest =
			latest->sequence - p->context_path_capacity + 1;
	}
	p->context_path_latest = latest->sequence;
	p->context_path_head = (slot + 1) % p->context_path_capacity;
	if (record.cause_sequence != 0)
		p->agent_provenance_edges++;
	p->context_path_visible_head = latest->sequence;
	if (agent_context_active_measure(
		    p, p->context_path_visible_head,
		    &p->context_active_path_count,
		    &p->context_active_path_oldest) < 0)
		panic("Agent context active path");
	p->agent_context_chain_hash = record.record_hash;
	p->agent_current_cause_sequence = latest->sequence;
	p->context_cause_branch_generation = record.branch_generation;
	p->agent_current_cause_pid = p->pid;
	p->agent_current_cause_control = p->agent_control_id;
	p->agent_current_span_id = record.span_id;
	agent_context_write_latest_shadow(p, latest);
	agent_context_write_header_shadow(p);
	agent_context_publish_commit(p, ranges, 3);
	agent_observe_record_context(p, &record, authority_effect, causal_audit);
	return 0;
}

int
agent_context_append(struct proc *p, struct agent_op *op,
		     struct agent_result *latest, uint64 tick,
		     int authority_effect)
{
	return agent_context_append_flags(
		p, op, latest, tick, AGENT_CONTEXT_RECORD_F_SYSTEM,
		authority_effect, 0);
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
		AGENT_CONTEXT_RECORD_F_SYSTEM, 0, causal_audit);
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
				       AGENT_CONTEXT_RECORD_F_MANUAL, 0, 0) < 0)
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
	struct agent_context_record record;
	uint64 index;
	int copied = 0;
	int read_status;
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
	if (start_sequence > p->context_path_visible_head) {
		result = 0;
		goto out;
	}
	for (index = 0;
	     index < p->context_active_path_count && copied < max;
	     index++) {
		read_status = agent_context_active_record(p, index, &record);
		if (read_status < 0) {
			result = read_status == -2 ? -1 : AGENT_STATUS_NO_SPACE;
			goto out;
		}
		if (start_sequence != 0 && record.sequence < start_sequence)
			continue;
		if (copyout(p->pagetable,
			    outaddr + copied * sizeof(struct agent_context_record),
			    (char *)&record, sizeof(record)) < 0) {
			result = -1;
			goto out;
		}
		copied++;
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
	struct agent_context_publish_range ranges[2];
	struct agent_context_header *header;
	int result;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	agent_context_full_ranges(ranges);
	if (agent_context_publish_prepare(p, ranges, 2) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	agent_context_write_header_shadow(p);
	agent_context_publish_commit(p, ranges, 2);
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
	struct agent_context_publish_range ranges[2] = {
		{ AGENT_CONTEXT_LATEST_RESPONSE_OFFSET,
		  sizeof(struct agent_result) },
		{ AGENT_CONTEXT_HEADER_OFFSET,
		  sizeof(struct agent_context_header) },
	};
	struct agent_context_record record;
	struct agent_result latest;
	uint64 span_owner;
	uint64 cause_control;
	uint64 cause_branch_generation;
	uint64 slot;
	uint64 branch_generation;
	uint64 active_path_count;
	uint64 active_path_oldest;
	int cause_pid;
	int result;

	if (!p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (p->context_path_count == 0 ||
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
	if (agent_context_publish_prepare(p, ranges, 2) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (agent_context_new_branch(p, &branch_generation) < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	p->context_branch_generation = branch_generation;
	p->context_cause_branch_generation = record.branch_generation;
	p->context_path_visible_head = sequence;
	p->context_active_path_count = active_path_count;
	p->context_active_path_oldest = active_path_oldest;
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
	agent_context_write_latest_shadow(p, &latest);
	agent_context_write_header_shadow(p);
	agent_context_publish_commit(p, ranges, 2);
	result = 0;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int
sys_context_clear(void)
{
	struct proc *p = curr_proc();
	struct agent_context_publish_range ranges[2];
	uint64 span_id;
	uint64 branch_generation;
	int result;

	if (!p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	agent_context_full_ranges(ranges);
	if (p->agent_control_id == 0 ||
	    agent_context_publish_prepare(p, ranges, 2) < 0 ||
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
	p->context_path_dropped = 0;
	p->context_path_rollback_count = 0;
	p->agent_current_span_id = span_id;
	p->agent_current_span_owner = p->agent_control_id;
	p->agent_current_cause_sequence = 0;
	p->agent_current_cause_pid = 0;
	p->agent_current_cause_control = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	agent_context_shadow_zero(p, AGENT_CONTEXT_HEADER_OFFSET,
				  agent_context_user_cache_offset());
	agent_context_write_latest_shadow(p, 0);
	agent_context_write_header_shadow(p);
	agent_context_publish_commit(p, ranges, 2);
	result = 0;
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}
