#include "agent_evidence_ring.h"
#include "../agent_provenance_abi.h"
#include "agent_internal.h"
#include "agent_observe_internal.h"
#include "defs.h"
#include "kalloc.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"

enum agent_evidence_slot_state {
	AGENT_EVIDENCE_SLOT_FREE = 0,
	AGENT_EVIDENCE_SLOT_BUSY,
	AGENT_EVIDENCE_SLOT_COMMITTED,
	AGENT_EVIDENCE_SLOT_DISCARDED,
};

#define AGENT_EVIDENCE_F_CRITICAL (1U << 0)
#define AGENT_EVIDENCE_F_CAUSAL   (1U << 1)
#define AGENT_EVIDENCE_F_SECURITY_DENIAL (1U << 2)
#define AGENT_EVIDENCE_F_DIRECT_DENIAL   (1U << 3)

#define AGENT_EVIDENCE_DIRECT_SYSCALL_NAMESPACE 0x40000000U
#define AGENT_EVIDENCE_DIRECT_SYSCALL_ID_MASK   0x3fffffffU

struct agent_evidence_event {
	uint64 ticket;
	uint64 audit_sequence;
	uint64 context_sequence;
	uint64 request_id;
	uint64 tick;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 branch_generation;
	uint64 path_parent_sequence;
	uint64 arg0;
	uint64 cause_branch_generation;
	uint64 actor_control_id;
	uint64 cause_control_id;
	uint64 cause_record_hash;
	uint64 prev_hash;
	uint64 record_hash;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 flags;
	uint64 span_owner;
	uint evidence_flags;
	int pid;
	int tid;
	int source_pid;
	int target_pid;
	int agent_id;
	int role;
	int loop_state;
	int tool_id;
	int status;
	char payload[AGENT_CONTEXT_TEXT_SIZE];
	char result[AGENT_CONTEXT_TEXT_SIZE];
};

struct agent_evidence_slot {
	uchar state;
	uchar reserved[7];
	uint64 reserved_ticket;
	struct agent_evidence_event event;
};

#define AGENT_EVIDENCE_SLOTS_PER_PAGE \
	(PAGE_SIZE / sizeof(struct agent_evidence_slot))
#define AGENT_EVIDENCE_ORDINARY_PAGES 3U
#define AGENT_EVIDENCE_CRITICAL_PAGES 1U
#define AGENT_EVIDENCE_PAGE_COUNT WORKFLOW_EVIDENCE_PAGE_COUNT

_Static_assert(sizeof(struct agent_evidence_slot) == 256U,
	       "evidence slots must pack exactly sixteen per page");
_Static_assert(AGENT_EVIDENCE_SLOTS_PER_PAGE *
		       AGENT_EVIDENCE_ORDINARY_PAGES ==
	       AGENT_EVIDENCE_ORDINARY_CAP,
	       "ordinary evidence page layout");
_Static_assert(AGENT_EVIDENCE_SLOTS_PER_PAGE *
		       AGENT_EVIDENCE_CRITICAL_PAGES ==
	       AGENT_EVIDENCE_CRITICAL_CAP,
	       "critical evidence page layout");
_Static_assert(AGENT_EVIDENCE_ORDINARY_PAGES +
		       AGENT_EVIDENCE_CRITICAL_PAGES ==
	       AGENT_EVIDENCE_PAGE_COUNT,
	       "workflow evidence accounting must match the page layout");

struct agent_evidence_domain {
	int used;
	int sealing;
	int allocating;
	int pages_charged;
	int direct_denials_prepared;
	struct workflow_lifecycle_key key;
	struct resource_account_handle page_account;
	enum resource_charge_class page_charge_class;
	uint page_count;
	void *slot_pages[AGENT_EVIDENCE_PAGE_COUNT];
	uint ordinary_cursor;
	uint critical_cursor;
	uint inflight;
	uint reserved;
	uint64 next_ticket;
	uint64 mutation_epoch;
	uint64 total_events;
	uint64 total_critical_events;
	uint64 total_gaps;
	uint64 observe_epoch;
	uint64 segment_sequence;
	uint64 last_fence_sequence;
	uint64 sealed_ticket_highwater;
	uint64 segment_first_ticket;
	uint64 segment_last_ticket;
	uint64 segment_events;
	uint64 segment_gaps;
	uint64 fence_first_ticket;
	uint64 fence_last_ticket;
	uint64 fence_events;
	uint64 fence_gaps;
	uint64 gap_first_ticket;
	uint64 gap_last_ticket;
	/* sealed_root accumulates internal rollovers; fence_root is externally
	 * published and is therefore the previous_root of the next fence. */
	uchar sealed_root[AGENT_SHA256_DIGEST_SIZE];
	uchar fence_root[AGENT_SHA256_DIGEST_SIZE];
};

struct agent_evidence_page_release {
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	uint charged;
	void *pages[AGENT_EVIDENCE_PAGE_COUNT];
};

static struct agent_evidence_domain
	agent_evidence_domains[WORKFLOW_LIFECYCLE_CAP];
static struct agent_evidence_retained_seal
	agent_evidence_retained[WORKFLOW_LIFECYCLE_CAP];

enum agent_evidence_reserve_status {
	AGENT_EVIDENCE_RESERVE_OK = 0,
	AGENT_EVIDENCE_RESERVE_RETRY = -1,
	AGENT_EVIDENCE_RESERVE_ROLLOVER = -2,
};

struct agent_evidence_reservation {
	struct workflow_lifecycle_key key;
	struct agent_evidence_slot *slot;
	uint64 ticket;
	int critical;
};

struct agent_evidence_seal_plan {
	struct agent_evidence_seal_result result;
	uint64 mutation_epoch;
};

static void agent_evidence_segment_reset_locked(
	struct agent_evidence_domain *);
static void agent_evidence_domain_release_locked(
	struct agent_evidence_domain *, struct agent_evidence_page_release *);
static struct agent_evidence_domain *agent_evidence_domain_locked(
	struct workflow_lifecycle_key, int);

static void
agent_evidence_hash_u32(struct agent_sha256_ctx *hash, uint32 value)
{
	uchar encoded[4];

	encoded[0] = (uchar)(value >> 24);
	encoded[1] = (uchar)(value >> 16);
	encoded[2] = (uchar)(value >> 8);
	encoded[3] = (uchar)value;
	agent_sha256_update(hash, encoded, sizeof(encoded));
}

static void
agent_evidence_hash_u64(struct agent_sha256_ctx *hash, uint64 value)
{
	uchar encoded[8];

	for (uint i = 0; i < sizeof(encoded); i++)
		encoded[i] = (uchar)(value >> (56U - i * 8U));
	agent_sha256_update(hash, encoded, sizeof(encoded));
}

static int
agent_evidence_root_present(const uchar root[AGENT_SHA256_DIGEST_SIZE])
{
	for (uint i = 0; i < AGENT_SHA256_DIGEST_SIZE; i++)
		if (root[i] != 0)
			return 1;
	return 0;
}

static void
agent_evidence_hash_event(const struct agent_evidence_event *event,
			  uchar out[AGENT_SHA256_DIGEST_SIZE])
{
	static const char domain[] = "AgentOS evidence event v1";
	static const char direct_domain[] =
		"AgentOS direct syscall denial evidence v1";
	struct agent_sha256_ctx hash;
	const char *selected_domain;
	uint selected_domain_size;

	selected_domain =
		(event->evidence_flags & AGENT_EVIDENCE_F_DIRECT_DENIAL) != 0 ?
			direct_domain : domain;
	selected_domain_size =
		(event->evidence_flags & AGENT_EVIDENCE_F_DIRECT_DENIAL) != 0 ?
			sizeof(direct_domain) - 1U : sizeof(domain) - 1U;
	agent_sha256_init(&hash);
	agent_sha256_update(&hash, selected_domain, selected_domain_size);
	agent_evidence_hash_u64(&hash, event->ticket);
	agent_evidence_hash_u64(&hash, event->audit_sequence);
	agent_evidence_hash_u64(&hash, event->context_sequence);
	agent_evidence_hash_u64(&hash, event->request_id);
	agent_evidence_hash_u64(&hash, event->tick);
	agent_evidence_hash_u64(&hash, event->cause_sequence);
	agent_evidence_hash_u64(&hash, event->span_id);
	agent_evidence_hash_u64(&hash, event->branch_generation);
	agent_evidence_hash_u64(&hash, event->path_parent_sequence);
	agent_evidence_hash_u64(&hash, event->arg0);
	agent_evidence_hash_u64(&hash, event->cause_branch_generation);
	agent_evidence_hash_u64(&hash, event->actor_control_id);
	agent_evidence_hash_u64(&hash, event->cause_control_id);
	agent_evidence_hash_u64(&hash, event->cause_record_hash);
	agent_evidence_hash_u64(&hash, event->prev_hash);
	agent_evidence_hash_u64(&hash, event->record_hash);
	agent_evidence_hash_u64(&hash, event->value0);
	agent_evidence_hash_u64(&hash, event->value1);
	agent_evidence_hash_u64(&hash, event->value2);
	agent_evidence_hash_u64(&hash, event->flags);
	agent_evidence_hash_u64(&hash, event->span_owner);
	agent_evidence_hash_u32(&hash, event->evidence_flags);
	agent_evidence_hash_u32(&hash, AGENT_AUDIT_KIND_CONTEXT);
	agent_evidence_hash_u32(&hash, (uint32)event->pid);
	agent_evidence_hash_u32(&hash, (uint32)event->tid);
	agent_evidence_hash_u32(&hash, (uint32)event->source_pid);
	agent_evidence_hash_u32(&hash, (uint32)event->target_pid);
	agent_evidence_hash_u32(&hash, (uint32)event->agent_id);
	agent_evidence_hash_u32(&hash, (uint32)event->role);
	agent_evidence_hash_u32(&hash, (uint32)event->loop_state);
	agent_evidence_hash_u32(&hash, (uint32)event->tool_id);
	agent_evidence_hash_u32(&hash, 0);
	agent_evidence_hash_u32(&hash, (uint32)event->status);
	agent_sha256_update(&hash, event->payload, sizeof(event->payload));
	agent_sha256_update(&hash, event->result, sizeof(event->result));
	agent_sha256_final(&hash, out);
}

static void
agent_evidence_hash_gap(const struct agent_evidence_domain *state,
			uchar out[AGENT_SHA256_DIGEST_SIZE])
{
	static const char domain[] = "AgentOS evidence gap v1";
	struct agent_sha256_ctx hash;

	agent_sha256_init(&hash);
	agent_sha256_update(&hash, domain, sizeof(domain) - 1U);
	agent_evidence_hash_u64(&hash, state->segment_gaps);
	agent_evidence_hash_u64(&hash, state->gap_first_ticket);
	agent_evidence_hash_u64(&hash, state->gap_last_ticket);
	agent_sha256_final(&hash, out);
}

static struct agent_evidence_slot *
agent_evidence_slot_at(struct agent_evidence_domain *state, int critical,
		       uint index)
{
	uint page;
	uint offset;

	if (state == 0)
		return 0;
	if (critical) {
		if (index >= AGENT_EVIDENCE_CRITICAL_CAP)
			return 0;
		page = AGENT_EVIDENCE_ORDINARY_PAGES +
		       index / AGENT_EVIDENCE_SLOTS_PER_PAGE;
		offset = index % AGENT_EVIDENCE_SLOTS_PER_PAGE;
	} else {
		if (index >= AGENT_EVIDENCE_ORDINARY_CAP)
			return 0;
		page = index / AGENT_EVIDENCE_SLOTS_PER_PAGE;
		offset = index % AGENT_EVIDENCE_SLOTS_PER_PAGE;
	}
	if (page >= AGENT_EVIDENCE_PAGE_COUNT || state->slot_pages[page] == 0)
		return 0;
	return &((struct agent_evidence_slot *)state->slot_pages[page])[offset];
}

static int
agent_evidence_pages_ready(const struct agent_evidence_domain *state)
{
	if (state == 0 || state->allocating || !state->pages_charged ||
	    state->page_count != AGENT_EVIDENCE_PAGE_COUNT)
		return 0;
	for (uint page = 0; page < AGENT_EVIDENCE_PAGE_COUNT; page++)
		if (state->slot_pages[page] == 0)
			return 0;
	return 1;
}

int
agent_evidence_context_preallocated(
	struct proc *p, struct workflow_lifecycle_key key)
{
	struct agent_evidence_domain *state;
	enum resource_charge_class charge_class;
	int ready;
	int enabled;

	if (p == 0 || !p->is_agent ||
	    !workflow_lifecycle_key_equal(vfs_proc_lifecycle(p), key))
		return 0;
	charge_class = p->resource_slot_reserved ?
		RESOURCE_CHARGE_RESERVED : RESOURCE_CHARGE_ORDINARY;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	ready = agent_evidence_pages_ready(state) &&
		resource_account_handle_equal(state->page_account,
					      p->resource_account) &&
		state->page_charge_class == charge_class;
	intr_restore(enabled);
	return ready;
}

static void
agent_evidence_pages_release(struct agent_evidence_page_release *release)
{
	struct resource_request request = {
		.kind = RESOURCE_AGENT_STATE_PAGE,
		.amount = WORKFLOW_EVIDENCE_PAGE_COUNT,
	};

	if (release == 0 || !release->charged)
		return;
	for (uint page = 0; page < AGENT_EVIDENCE_PAGE_COUNT; page++) {
		if (release->pages[page] == 0 ||
		    kfree_account_page(release->pages[page], release->account,
				       release->charge_class) < 0)
			panic("evidence physical page release");
		release->pages[page] = 0;
	}
	if (resource_release_many(release->account, release->charge_class,
				  &request, 1) < 0)
		panic("evidence state page release");
	proc_resource_account_reap(release->account);
	release->charged = 0;
}

static int
agent_evidence_pages_ensure(struct workflow_lifecycle_key key, struct proc *p)
{
	struct resource_request request = {
		.kind = RESOURCE_AGENT_STATE_PAGE,
		.amount = WORKFLOW_EVIDENCE_PAGE_COUNT,
	};
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	void *pages[AGENT_EVIDENCE_PAGE_COUNT] = {0};
	struct agent_evidence_domain *state;
	uint allocated = 0;
	int charged = 0;
	int enabled;

	if (p == 0 || !p->is_agent)
		return -1;
	account = p->resource_account;
	charge_class = p->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
						 RESOURCE_CHARGE_ORDINARY;
	if (!resource_account_active(account) || !workflow_lifecycle_active(key))
		return -1;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	if (state == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (agent_evidence_pages_ready(state)) {
		int matches = resource_account_handle_equal(
			state->page_account, account) &&
			state->page_charge_class == charge_class;
		intr_restore(enabled);
		return matches ? 0 : -1;
	}
	if (state->sealing || state->allocating || state->pages_charged ||
	    state->page_count != 0 || !workflow_lifecycle_active(key) ||
	    !resource_account_active(account)) {
		intr_restore(enabled);
		return -1;
	}
	for (uint page = 0; page < AGENT_EVIDENCE_PAGE_COUNT; page++)
		if (state->slot_pages[page] != 0) {
			intr_restore(enabled);
			return -1;
		}
	state->allocating = 1;
	state->page_account = account;
	state->page_charge_class = charge_class;
	intr_restore(enabled);

	if (resource_acquire_many(account, charge_class, &request, 1) < 0)
		goto fail;
	charged = 1;
	while (allocated < AGENT_EVIDENCE_PAGE_COUNT) {
		pages[allocated] = kalloc_account_page(account, charge_class);
		if (pages[allocated] == 0)
			goto fail;
		memset(pages[allocated], 0, PAGE_SIZE);
		allocated++;
	}

	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	if (state == 0 || state->sealing || !state->allocating ||
	    state->pages_charged ||
	    state->page_count != 0 || p->workflow_lifecycle_id != key.id ||
	    p->workflow_lifecycle_generation != key.generation ||
	    !resource_account_handle_equal(p->resource_account, account) ||
	    !resource_account_handle_equal(state->page_account, account) ||
	    state->page_charge_class != charge_class ||
	    (p->resource_slot_reserved != 0) !=
		(charge_class == RESOURCE_CHARGE_RESERVED) ||
	    !workflow_lifecycle_active(key) ||
	    !resource_account_active(account)) {
		if (state != 0 && state->allocating &&
		    resource_account_handle_equal(state->page_account, account) &&
		    state->page_charge_class == charge_class) {
			state->allocating = 0;
			state->page_account = resource_account_none();
			state->page_charge_class = RESOURCE_CHARGE_CLASS_COUNT;
		}
		intr_restore(enabled);
		goto fail;
	}
	for (uint page = 0; page < AGENT_EVIDENCE_PAGE_COUNT; page++) {
		state->slot_pages[page] = pages[page];
		pages[page] = 0;
	}
	state->page_account = account;
	state->page_charge_class = charge_class;
	state->page_count = AGENT_EVIDENCE_PAGE_COUNT;
	state->pages_charged = 1;
	state->allocating = 0;
	state->mutation_epoch++;
	intr_restore(enabled);
	return 0;

fail:
	while (allocated > 0) {
		allocated--;
		if (kfree_account_page(pages[allocated], account, charge_class) < 0)
			panic("evidence allocation unwind");
	}
	if (charged && resource_release_many(
			       account, charge_class, &request, 1) < 0)
		panic("evidence state unwind");
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	if (state != 0 && state->allocating && !state->pages_charged &&
	    state->page_count == 0 &&
	    resource_account_handle_equal(state->page_account, account) &&
	    state->page_charge_class == charge_class) {
		state->allocating = 0;
		state->page_account = resource_account_none();
		state->page_charge_class = RESOURCE_CHARGE_CLASS_COUNT;
	}
	intr_restore(enabled);
	return -1;
}

static struct agent_evidence_slot *
agent_evidence_slot_from_ref(struct agent_evidence_domain *state, ushort ref)
{
	uint index = ref & AGENT_EVIDENCE_REF_INDEX_MASK;

	return agent_evidence_slot_at(
		state, (ref & AGENT_EVIDENCE_REF_CRITICAL) != 0, index);
}

static struct agent_evidence_domain *
agent_evidence_domain_locked(struct workflow_lifecycle_key key, int create)
{
	struct agent_evidence_domain *state;

	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return 0;
	state = &agent_evidence_domains[key.id - 1U];
	if (state->used && workflow_lifecycle_key_equal(state->key, key))
		return state;
	if (!create)
		return 0;
	/* Reuse must follow explicit reclaim; never discard an older generation. */
	if (state->used)
		return 0;
	agent_evidence_segment_reset_locked(state);
	memset(state->sealed_root, 0, sizeof(state->sealed_root));
	memset(state->fence_root, 0, sizeof(state->fence_root));
	state->used = 1;
	state->sealing = 0;
	state->allocating = 0;
	state->pages_charged = 0;
	state->direct_denials_prepared = 0;
	state->key = key;
	state->page_account = resource_account_none();
	state->page_charge_class = RESOURCE_CHARGE_CLASS_COUNT;
	state->page_count = 0;
	memset(state->slot_pages, 0, sizeof(state->slot_pages));
	state->inflight = 0;
	state->next_ticket = 1;
	state->mutation_epoch = 1;
	state->total_events = 0;
	state->total_critical_events = 0;
	state->total_gaps = 0;
	state->observe_epoch = 1;
	state->segment_sequence = 0;
	state->last_fence_sequence = 0;
	state->sealed_ticket_highwater = 0;
	state->fence_first_ticket = 0;
	state->fence_last_ticket = 0;
	state->fence_events = 0;
	state->fence_gaps = 0;
	return state;
}

static void
agent_evidence_ref_insert(struct agent_evidence_view *view, ushort ref,
			  uint64 ticket)
{
	uint pos = view->visible_records;

	if (pos >= AGENT_EVIDENCE_CAP)
		return;
	while (pos > 0 && view->entries[pos - 1U].ticket > ticket) {
		view->entries[pos] = view->entries[pos - 1U];
		pos--;
	}
	view->entries[pos].ticket = ticket;
	view->entries[pos].ref = ref;
	view->visible_records++;
}

static uint
agent_evidence_collect_refs_locked(struct agent_evidence_domain *state,
				   ushort refs[AGENT_EVIDENCE_CAP])
{
	uint count = 0;

	for (uint i = 0; i < AGENT_EVIDENCE_ORDINARY_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 0, i);
		if (slot != 0 && slot->state == AGENT_EVIDENCE_SLOT_COMMITTED)
			refs[count++] = (ushort)i;
	}
	for (uint i = 0; i < AGENT_EVIDENCE_CRITICAL_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 1, i);
		if (slot != 0 && slot->state == AGENT_EVIDENCE_SLOT_COMMITTED)
			refs[count++] =
				(ushort)(AGENT_EVIDENCE_REF_CRITICAL | i);
	}
	for (uint i = 1; i < count; i++) {
		ushort selected = refs[i];
		struct agent_evidence_slot *selected_slot =
			agent_evidence_slot_from_ref(state, selected);
		uint j = i;

		while (j > 0) {
			struct agent_evidence_slot *prior =
				agent_evidence_slot_from_ref(state, refs[j - 1U]);
			if (prior->event.ticket <= selected_slot->event.ticket)
				break;
			refs[j] = refs[j - 1U];
			j--;
		}
		refs[j] = selected;
	}
	return count;
}

static void
agent_evidence_merkle_stable(struct agent_evidence_domain *state,
			     uchar root[AGENT_SHA256_DIGEST_SIZE],
			     uint *retained_out)
{
	static const char node_domain[] = "AgentOS evidence node v1";
	static const char empty_domain[] = "AgentOS evidence empty v1";
	uchar hashes[AGENT_EVIDENCE_CAP + 1U][AGENT_SHA256_DIGEST_SIZE];
	ushort refs[AGENT_EVIDENCE_CAP];
	uint count = agent_evidence_collect_refs_locked(state, refs);

	for (uint i = 0; i < count; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_from_ref(state, refs[i]);
		agent_evidence_hash_event(&slot->event, hashes[i]);
	}
	if (state->segment_gaps != 0)
		agent_evidence_hash_gap(state, hashes[count++]);
	*retained_out = count - (state->segment_gaps != 0 ? 1U : 0U);
	if (count == 0) {
		agent_sha256(empty_domain, sizeof(empty_domain) - 1U, root);
		return;
	}
	while (count > 1U) {
		uint next = 0;

		for (uint i = 0; i < count; i += 2U) {
			uint right = i + 1U < count ? i + 1U : i;
			struct agent_sha256_ctx hash;

			agent_sha256_init(&hash);
			agent_sha256_update(&hash, node_domain,
					    sizeof(node_domain) - 1U);
			agent_sha256_update(&hash, hashes[i],
					    AGENT_SHA256_DIGEST_SIZE);
			agent_sha256_update(&hash, hashes[right],
					    AGENT_SHA256_DIGEST_SIZE);
			agent_sha256_final(&hash, hashes[next++]);
		}
		count = next;
	}
	memmove(root, hashes[0], AGENT_SHA256_DIGEST_SIZE);
}

static void
agent_evidence_segment_reset_locked(struct agent_evidence_domain *state)
{
	for (uint i = 0; i < AGENT_EVIDENCE_ORDINARY_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 0, i);
		if (slot == 0)
			continue;
		slot->reserved_ticket = 0;
		__atomic_store_n(&slot->state, AGENT_EVIDENCE_SLOT_FREE,
				 __ATOMIC_RELEASE);
	}
	for (uint i = 0; i < AGENT_EVIDENCE_CRITICAL_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 1, i);
		if (slot == 0)
			continue;
		slot->reserved_ticket = 0;
		__atomic_store_n(&slot->state, AGENT_EVIDENCE_SLOT_FREE,
				 __ATOMIC_RELEASE);
	}
	state->ordinary_cursor = 0;
	state->critical_cursor = 0;
	state->segment_first_ticket = 0;
	state->segment_last_ticket = 0;
	state->segment_events = 0;
	state->segment_gaps = 0;
	state->gap_first_ticket = 0;
	state->gap_last_ticket = 0;
}

static void
agent_evidence_domain_release_locked(
	struct agent_evidence_domain *state,
	struct agent_evidence_page_release *release)
{
	int ready;

	if (state == 0 || release == 0 || state->allocating ||
	    state->inflight != 0)
		panic("evidence domain release state");
	ready = agent_evidence_pages_ready(state);
	if ((state->pages_charged != 0) != ready ||
	    (ready && (!resource_account_handle_valid(state->page_account) ||
		      state->page_charge_class >= RESOURCE_CHARGE_CLASS_COUNT)) ||
	    (!ready && state->page_count != 0))
		panic("evidence domain page provenance");
	memset(release, 0, sizeof(*release));
	release->account = ready ? state->page_account : resource_account_none();
	release->charge_class = ready ? state->page_charge_class :
					RESOURCE_CHARGE_CLASS_COUNT;
	release->charged = ready;
	for (uint page = 0; page < AGENT_EVIDENCE_PAGE_COUNT; page++) {
		if ((state->slot_pages[page] != 0) != ready)
			panic("evidence domain partial pages");
		release->pages[page] = state->slot_pages[page];
		state->slot_pages[page] = 0;
	}
	agent_evidence_segment_reset_locked(state);
	memset(state->sealed_root, 0, sizeof(state->sealed_root));
	memset(state->fence_root, 0, sizeof(state->fence_root));
	state->used = 0;
	state->sealing = 0;
	state->allocating = 0;
	state->pages_charged = 0;
	state->direct_denials_prepared = 0;
	state->key = workflow_lifecycle_none();
	state->page_account = resource_account_none();
	state->page_charge_class = RESOURCE_CHARGE_CLASS_COUNT;
	state->page_count = 0;
	state->inflight = 0;
	state->next_ticket = 0;
	state->mutation_epoch = 0;
	state->total_events = 0;
	state->total_critical_events = 0;
	state->total_gaps = 0;
	state->observe_epoch = 0;
	state->segment_sequence = 0;
	state->last_fence_sequence = 0;
	state->sealed_ticket_highwater = 0;
	state->fence_first_ticket = 0;
	state->fence_last_ticket = 0;
	state->fence_events = 0;
	state->fence_gaps = 0;
}

static void
agent_evidence_retained_publish_locked(
	struct workflow_lifecycle_key key, uint flags,
	const struct agent_evidence_seal_result *result)
{
	struct agent_evidence_retained_seal *retained;

	if (result == 0 || key.id == 0 || key.id > WORKFLOW_LIFECYCLE_CAP)
		return;
	retained = &agent_evidence_retained[key.id - 1U];
	memset(retained, 0, sizeof(*retained));
	retained->key = key;
	retained->flags = flags;
	memmove(retained->previous_root, result->previous_root,
		sizeof(retained->previous_root));
	memmove(retained->root, result->root, sizeof(retained->root));
	retained->first_ticket = result->first_ticket;
	retained->last_ticket = result->last_ticket;
	retained->event_count = result->event_count;
	retained->gap_count = result->gap_count;
	retained->last_workflow_fence_sequence = result->fence_sequence;
	retained->sealed_ticket_highwater =
		agent_evidence_domains[key.id - 1U].sealed_ticket_highwater;
	if ((flags & AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE) != 0 &&
	    result->last_ticket > retained->sealed_ticket_highwater)
		retained->sealed_ticket_highwater = result->last_ticket;
	retained->segment_sequence = result->segment_sequence;
	retained->metadata_generation = result->metadata_generation;
	retained->credit_epoch = result->credit_epoch;
}

static int
agent_evidence_prepare_seal_stable(
	struct agent_evidence_domain *state, uint64 workflow_fence_sequence,
	const uchar challenge[AGENT_SHA256_DIGEST_SIZE],
	uint64 metadata_generation, uint64 credit_epoch,
	const uchar credit_digest[AGENT_SHA256_DIGEST_SIZE], int workflow_fence,
	struct agent_evidence_seal_plan *plan)
{
	static const char fence_domain[] = "AgentOS evidence fence v1";
	static const char roll_domain[] = "AgentOS evidence rollover v1";
	struct agent_sha256_ctx hash;
	uchar merkle_root[AGENT_SHA256_DIGEST_SIZE];
	uint retained = 0;
	uint64 segment_sequence;

	agent_evidence_merkle_stable(state, merkle_root, &retained);
	segment_sequence = state->segment_sequence + 1U;
	if (segment_sequence == 0)
		return -1;
	memset(plan, 0, sizeof(*plan));
	plan->mutation_epoch = state->mutation_epoch;
	memmove(plan->result.previous_root, state->fence_root,
		sizeof(plan->result.previous_root));
	if (!workflow_fence) {
		int have_rollup =
			agent_evidence_root_present(state->sealed_root);
		memmove(plan->result.previous_root,
			have_rollup ? state->sealed_root : state->fence_root,
			sizeof(plan->result.previous_root));
	}
	agent_sha256_init(&hash);
	agent_sha256_update(&hash,
		workflow_fence ? fence_domain : roll_domain,
		workflow_fence ? sizeof(fence_domain) - 1U :
				 sizeof(roll_domain) - 1U);
	agent_evidence_hash_u32(&hash, state->key.id);
	agent_evidence_hash_u64(&hash, state->key.generation);
	agent_evidence_hash_u64(&hash, workflow_fence_sequence);
	agent_evidence_hash_u64(&hash, segment_sequence);
	agent_sha256_update(&hash, challenge, AGENT_SHA256_DIGEST_SIZE);
	agent_sha256_update(&hash, state->fence_root,
			    AGENT_SHA256_DIGEST_SIZE);
	agent_sha256_update(&hash, state->sealed_root,
			    AGENT_SHA256_DIGEST_SIZE);
	agent_evidence_hash_u64(&hash, state->segment_first_ticket);
	agent_evidence_hash_u64(&hash, state->segment_last_ticket);
	agent_evidence_hash_u64(&hash, state->segment_events);
	agent_evidence_hash_u64(&hash, state->segment_gaps);
	agent_evidence_hash_u64(&hash, state->fence_first_ticket);
	agent_evidence_hash_u64(&hash, state->fence_last_ticket);
	agent_evidence_hash_u64(&hash, state->fence_events);
	agent_evidence_hash_u64(&hash, state->fence_gaps);
	agent_evidence_hash_u32(&hash, retained);
	agent_evidence_hash_u64(&hash, metadata_generation);
	agent_evidence_hash_u64(&hash, credit_epoch);
	agent_sha256_update(&hash, credit_digest, AGENT_SHA256_DIGEST_SIZE);
	agent_sha256_update(&hash, merkle_root, sizeof(merkle_root));
	agent_sha256_final(&hash, plan->result.root);
	plan->result.first_ticket = workflow_fence ?
		state->fence_first_ticket : state->segment_first_ticket;
	plan->result.last_ticket = workflow_fence ?
		state->fence_last_ticket : state->segment_last_ticket;
	plan->result.event_count = workflow_fence ?
		state->fence_events : state->segment_events;
	plan->result.gap_count = workflow_fence ?
		state->fence_gaps : state->segment_gaps;
	plan->result.fence_sequence = workflow_fence_sequence;
	plan->result.segment_sequence = segment_sequence;
	plan->result.metadata_generation = metadata_generation;
	plan->result.credit_epoch = credit_epoch;
	return 0;
}

static int
agent_evidence_prepare_retirement_stable(
	struct agent_evidence_domain *state,
	struct agent_evidence_seal_plan *plan)
{
	static const char retirement_domain[] =
		"AgentOS evidence retirement v1";
	struct agent_sha256_ctx hash;
	uchar merkle_root[AGENT_SHA256_DIGEST_SIZE];
	uint retained = 0;
	uint64 segment_sequence;

	agent_evidence_merkle_stable(state, merkle_root, &retained);
	segment_sequence = state->segment_sequence + 1U;
	if (segment_sequence == 0)
		return -1;
	memset(plan, 0, sizeof(*plan));
	plan->mutation_epoch = state->mutation_epoch;
	memmove(plan->result.previous_root, state->fence_root,
		sizeof(plan->result.previous_root));
	agent_sha256_init(&hash);
	agent_sha256_update(&hash, retirement_domain,
			    sizeof(retirement_domain) - 1U);
	agent_evidence_hash_u32(&hash, state->key.id);
	agent_evidence_hash_u64(&hash, state->key.generation);
	agent_evidence_hash_u64(&hash, state->last_fence_sequence);
	agent_evidence_hash_u64(&hash, segment_sequence);
	agent_sha256_update(&hash, state->fence_root,
			    AGENT_SHA256_DIGEST_SIZE);
	agent_sha256_update(&hash, state->sealed_root,
			    AGENT_SHA256_DIGEST_SIZE);
	agent_evidence_hash_u64(&hash, state->segment_first_ticket);
	agent_evidence_hash_u64(&hash, state->segment_last_ticket);
	agent_evidence_hash_u64(&hash, state->segment_events);
	agent_evidence_hash_u64(&hash, state->segment_gaps);
	agent_evidence_hash_u64(&hash, state->fence_first_ticket);
	agent_evidence_hash_u64(&hash, state->fence_last_ticket);
	agent_evidence_hash_u64(&hash, state->fence_events);
	agent_evidence_hash_u64(&hash, state->fence_gaps);
	agent_evidence_hash_u32(&hash, retained);
	agent_sha256_update(&hash, merkle_root, sizeof(merkle_root));
	agent_sha256_final(&hash, plan->result.root);
	plan->result.first_ticket = state->fence_first_ticket;
	plan->result.last_ticket = state->fence_last_ticket;
	plan->result.event_count = state->fence_events;
	plan->result.gap_count = state->fence_gaps;
	/* This is the last external fence covered, not a new fence sequence. */
	plan->result.fence_sequence = state->last_fence_sequence;
	plan->result.segment_sequence = segment_sequence;
	return 0;
}

static int
agent_evidence_seal_stable(struct agent_evidence_domain *state,
			   uint64 workflow_fence_sequence,
			   const uchar challenge[AGENT_SHA256_DIGEST_SIZE],
			   uint64 metadata_generation, uint64 credit_epoch,
			   const uchar credit_digest[AGENT_SHA256_DIGEST_SIZE],
			   int workflow_fence,
			   struct agent_evidence_seal_result *out)
{
	struct agent_evidence_seal_plan plan;
	int enabled;
	int status;

	enabled = intr_save();
	if (state == 0 || state->sealing || state->allocating ||
	    state->inflight != 0 ||
	    state->segment_sequence == ~0ULL ||
	    (workflow_fence &&
	     (workflow_fence_sequence == 0 ||
	      workflow_fence_sequence <= state->last_fence_sequence))) {
		intr_restore(enabled);
		return -1;
	}
	state->sealing = 1;
	intr_restore(enabled);

	/* The gate makes the immutable snapshot safe without holding IRQ-off. */
	status = agent_evidence_prepare_seal_stable(
		state, workflow_fence_sequence, challenge, metadata_generation,
		credit_epoch, credit_digest, workflow_fence, &plan);

	enabled = intr_save();
	if (status < 0 || !state->sealing || state->allocating ||
	    state->inflight != 0 ||
	    state->mutation_epoch != plan.mutation_epoch ||
	    (workflow_fence &&
	     workflow_fence_sequence <= state->last_fence_sequence)) {
		state->sealing = 0;
		intr_restore(enabled);
		return -1;
	}
	if (workflow_fence) {
		if (plan.result.last_ticket > state->sealed_ticket_highwater)
			state->sealed_ticket_highwater = plan.result.last_ticket;
		memmove(state->fence_root, plan.result.root,
			sizeof(state->fence_root));
		memset(state->sealed_root, 0, sizeof(state->sealed_root));
		agent_evidence_retained_publish_locked(
			state->key, AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE,
			&plan.result);
	} else {
		memmove(state->sealed_root, plan.result.root,
			sizeof(state->sealed_root));
	}
	state->segment_sequence = plan.result.segment_sequence;
	if (workflow_fence)
		state->last_fence_sequence = workflow_fence_sequence;
	agent_evidence_segment_reset_locked(state);
	if (workflow_fence) {
		state->fence_first_ticket = 0;
		state->fence_last_ticket = 0;
		state->fence_events = 0;
		state->fence_gaps = 0;
	}
	state->mutation_epoch++;
	state->sealing = 0;
	intr_restore(enabled);
	if (out != 0)
		memmove(out, &plan.result, sizeof(*out));
	return 0;
}

static int
agent_evidence_rollover(struct workflow_lifecycle_key key)
{
	static const uchar no_challenge[AGENT_SHA256_DIGEST_SIZE];
	struct agent_evidence_domain *state;
	int enabled = intr_save();

	state = agent_evidence_domain_locked(key, 0);
	intr_restore(enabled);
	return state == 0 ? -1 :
	       agent_evidence_seal_stable(state, 0, no_challenge, 0, 0,
					 no_challenge, 0, 0);
}

static int
agent_evidence_reserve_locked(struct agent_evidence_domain *state,
			      int critical,
			      struct agent_evidence_reservation *reservation)
{
	uint capacity = critical ? AGENT_EVIDENCE_CRITICAL_CAP :
				   AGENT_EVIDENCE_ORDINARY_CAP;
	uint *cursor = critical ? &state->critical_cursor :
				  &state->ordinary_cursor;

	if (state->sealing || state->allocating ||
	    !agent_evidence_pages_ready(state) || state->next_ticket == 0)
		return AGENT_EVIDENCE_RESERVE_RETRY;
	for (uint step = 0; step < capacity; step++) {
		uint index = (*cursor + step) % capacity;
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, critical, index);

		if (slot == 0 ||
		    (slot->state != AGENT_EVIDENCE_SLOT_FREE &&
		     slot->state != AGENT_EVIDENCE_SLOT_DISCARDED))
			continue;
		memset(reservation, 0, sizeof(*reservation));
		reservation->key = state->key;
		reservation->slot = slot;
		reservation->ticket = state->next_ticket++;
		reservation->critical = critical;
		*cursor = (index + 1U) % capacity;
		slot->reserved_ticket = reservation->ticket;
		__atomic_store_n(&slot->state, AGENT_EVIDENCE_SLOT_BUSY,
				 __ATOMIC_RELEASE);
		state->inflight++;
		return AGENT_EVIDENCE_RESERVE_OK;
	}
	return AGENT_EVIDENCE_RESERVE_ROLLOVER;
}

static struct agent_evidence_slot *
agent_evidence_free_slot_locked(struct agent_evidence_domain *state,
				int critical, uint *index_out)
{
	uint capacity = critical ? AGENT_EVIDENCE_CRITICAL_CAP :
				   AGENT_EVIDENCE_ORDINARY_CAP;
	uint cursor = critical ? state->critical_cursor :
				 state->ordinary_cursor;

	for (uint step = 0; step < capacity; step++) {
		uint index = (cursor + step) % capacity;
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, critical, index);

		if (slot != 0 &&
		    (slot->state == AGENT_EVIDENCE_SLOT_FREE ||
		     slot->state == AGENT_EVIDENCE_SLOT_DISCARDED)) {
			*index_out = index;
			return slot;
		}
	}
	return 0;
}

static void
agent_evidence_note_ticket_locked(struct agent_evidence_domain *state,
				  uint64 ticket, int gap, int critical)
{
	state->total_events++;
	if (critical && !gap)
		state->total_critical_events++;
	state->segment_events++;
	state->fence_events++;
	if (state->segment_first_ticket == 0 ||
	    ticket < state->segment_first_ticket)
		state->segment_first_ticket = ticket;
	if (ticket > state->segment_last_ticket)
		state->segment_last_ticket = ticket;
	if (state->fence_first_ticket == 0 || ticket < state->fence_first_ticket)
		state->fence_first_ticket = ticket;
	if (ticket > state->fence_last_ticket)
		state->fence_last_ticket = ticket;
	if (gap) {
		state->total_gaps++;
		state->segment_gaps++;
		state->fence_gaps++;
		if (state->gap_first_ticket == 0 || ticket < state->gap_first_ticket)
			state->gap_first_ticket = ticket;
		if (ticket > state->gap_last_ticket)
			state->gap_last_ticket = ticket;
	}
	state->observe_epoch++;
	if (state->observe_epoch == 0)
		state->observe_epoch = 1;
	state->mutation_epoch++;
}

static int
agent_evidence_commit_locked(struct agent_evidence_domain *state,
			     struct agent_evidence_reservation *reservation)
{
	struct agent_evidence_slot *slot = reservation->slot;

	if (state == 0 || slot == 0 || state->sealing ||
	    !workflow_lifecycle_key_equal(state->key, reservation->key) ||
	    slot->state != AGENT_EVIDENCE_SLOT_BUSY ||
	    slot->reserved_ticket != reservation->ticket ||
	    slot->event.ticket != reservation->ticket || state->inflight == 0)
		return -1;
	slot->reserved_ticket = 0;
	__atomic_store_n(&slot->state, AGENT_EVIDENCE_SLOT_COMMITTED,
			 __ATOMIC_RELEASE);
	state->inflight--;
	agent_evidence_note_ticket_locked(
		state, reservation->ticket, 0, reservation->critical);
	return 0;
}

static void
agent_evidence_discard_locked(struct agent_evidence_domain *state,
			      struct agent_evidence_reservation *reservation)
{
	struct agent_evidence_slot *slot = reservation->slot;

	if (state == 0 || slot == 0 ||
	    !workflow_lifecycle_key_equal(state->key, reservation->key) ||
	    slot->state != AGENT_EVIDENCE_SLOT_BUSY ||
	    slot->reserved_ticket != reservation->ticket)
		return;
	slot->reserved_ticket = 0;
	__atomic_store_n(&slot->state, AGENT_EVIDENCE_SLOT_DISCARDED,
			 __ATOMIC_RELEASE);
	if (state->inflight == 0)
		panic("evidence reservation underflow");
	state->inflight--;
	agent_evidence_note_ticket_locked(state, reservation->ticket, 1, 0);
}

void
agent_evidence_init(void)
{
	memset(agent_evidence_domains, 0, sizeof(agent_evidence_domains));
	memset(agent_evidence_retained, 0, sizeof(agent_evidence_retained));
}

static int
agent_evidence_lifecycle_participant_valid(
	struct proc *p, struct workflow_lifecycle_key key,
	struct thread **thread_out)
{
	struct thread *thread = curr_thread();
	uint scope_id;

	if (thread_out != 0)
		*thread_out = 0;
	if (p == 0 || p->pid <= 0 || thread == 0 || thread->process != p ||
	    !proc_teardown_live(p) || thread->state != RUNNING ||
	    thread->tid < 0 || thread->tid >= NTHREAD ||
	    thread->identity_generation == 0 ||
	    !thread->resource_slot_charged ||
	    !p->workflow_lifecycle_charged ||
	    !workflow_lifecycle_key_equal(vfs_proc_lifecycle(p), key) ||
	    !workflow_lifecycle_active(key) ||
	    workflow_lifecycle_scope(key, &scope_id) < 0 ||
	    !vfs_scope_active(scope_id) ||
	    !vfs_proc_scope_publishable(p) ||
	    (p->vfs_scope_id != VFS_SCOPE_NONE &&
	     p->vfs_scope_id != scope_id) ||
	    (p->vfs_scope_id == VFS_SCOPE_NONE &&
	     (p->vfs_scope_controller || p->vfs_effective_caps != 0 ||
	      p->vfs_inheritable_caps != 0)) ||
	    p->resource_domain_id < 0 || p->resource_account.generation == 0 ||
	    !resource_account_active(p->resource_account) ||
	    !resource_account_handle_equal(
		    thread->resource_account, p->resource_account) ||
	    thread->resource_slot_reserved != p->resource_slot_reserved)
		return 0;
	if (thread_out != 0)
		*thread_out = thread;
	return 1;
}

static int
agent_evidence_direct_controller_valid(
	struct proc *controller, struct workflow_lifecycle_key key)
{
	struct thread *thread;
	uint scope_id;

	return agent_evidence_lifecycle_participant_valid(
		       controller, key, &thread) &&
	       controller->is_agent &&
	       workflow_lifecycle_scope(key, &scope_id) == 0 &&
	       controller->vfs_scope_id == scope_id &&
	       agent_identity_has_cap(controller, AGENT_CAP_ORCHESTRATE) &&
	       controller->agent_control_id != 0 &&
	       workflow_lifecycle_controller_matches(
		       key, scope_id, controller->agent_control_id);
}

int
agent_evidence_prepare_direct_denials(
	struct proc *controller, struct workflow_lifecycle_key key)
{
	struct agent_evidence_domain *state;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	int enabled;

	if (!agent_evidence_direct_controller_valid(controller, key))
		return -1;
	account = controller->resource_account;
	charge_class = controller->resource_slot_reserved ?
		RESOURCE_CHARGE_RESERVED : RESOURCE_CHARGE_ORDINARY;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 1);
	intr_restore(enabled);
	if (state == 0 || agent_evidence_pages_ensure(key, controller) < 0 ||
	    !agent_evidence_direct_controller_valid(controller, key))
		return -1;

	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	if (state == 0 || state->sealing || state->allocating ||
	    !agent_evidence_pages_ready(state) ||
	    !agent_evidence_direct_controller_valid(controller, key) ||
	    !resource_account_handle_equal(state->page_account, account) ||
	    state->page_charge_class != charge_class ||
	    !resource_account_active(account) ||
	    !workflow_lifecycle_active(key)) {
		intr_restore(enabled);
		return -1;
	}
	state->direct_denials_prepared = 1;
	intr_restore(enabled);
	return 0;
}

static int
agent_evidence_direct_actor_valid_locked(
	struct proc *actor, struct thread *thread,
	struct workflow_lifecycle_key key,
	const struct agent_evidence_domain *state,
	uint64 thread_generation,
	struct resource_account_handle account)
{
	struct thread *current;

	return agent_evidence_lifecycle_participant_valid(
		       actor, key, &current) &&
	       !actor->is_agent && thread != 0 && current == thread &&
	       thread->identity_generation == thread_generation &&
	       thread_generation != 0 && state != 0 &&
	       state->direct_denials_prepared &&
	       workflow_lifecycle_key_equal(state->key, key) &&
	       agent_evidence_pages_ready(state) &&
	       account.generation != 0 &&
	       resource_account_handle_equal(actor->resource_account, account) &&
	       resource_account_handle_equal(thread->resource_account, account) &&
	       resource_account_handle_equal(state->page_account, account) &&
	       resource_account_active(account) &&
	       state->page_charge_class ==
		       (actor->resource_slot_reserved ?
			       RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY) &&
	       thread->resource_slot_reserved == actor->resource_slot_reserved;
}

static uint64
agent_evidence_direct_record_hash(
	const struct workflow_lifecycle_key key,
	const struct resource_account_handle account,
	const struct agent_evidence_event *event)
{
	static const char domain[] =
		"AgentOS direct syscall denial record v1";
	struct agent_sha256_ctx hash;
	uchar digest[AGENT_SHA256_DIGEST_SIZE];
	uint64 record_hash = 0;

	agent_sha256_init(&hash);
	agent_sha256_update(&hash, domain, sizeof(domain) - 1U);
	agent_evidence_hash_u32(&hash, key.id);
	agent_evidence_hash_u64(&hash, key.generation);
	agent_evidence_hash_u32(&hash, account.slot);
	agent_evidence_hash_u64(&hash, account.generation);
	agent_evidence_hash_u64(&hash, event->ticket);
	agent_evidence_hash_u64(&hash, event->audit_sequence);
	agent_evidence_hash_u64(&hash, event->context_sequence);
	agent_evidence_hash_u64(&hash, event->request_id);
	agent_evidence_hash_u64(&hash, event->value0);
	agent_evidence_hash_u64(&hash, event->value1);
	agent_evidence_hash_u64(&hash, event->value2);
	agent_evidence_hash_u32(&hash, (uint32)event->pid);
	agent_evidence_hash_u32(&hash, (uint32)event->tid);
	agent_evidence_hash_u32(&hash, (uint32)event->tool_id);
	agent_evidence_hash_u32(&hash, (uint32)event->status);
	agent_evidence_hash_u32(
		&hash, AGENT_PROVENANCE_DENY_MISSING_CONTRACT);
	agent_sha256_final(&hash, digest);
	for (uint i = 0; i < sizeof(record_hash); i++)
		record_hash = (record_hash << 8) | digest[i];
	memset(digest, 0, sizeof(digest));
	return record_hash != 0 ? record_hash : 1;
}

int
agent_evidence_append_direct_syscall_denial(
	struct proc *actor, struct workflow_lifecycle_key key, int syscall_id,
	uint64 side_effect_mask, uint64 *ticket_out)
{
	struct agent_evidence_domain *state;
	struct agent_evidence_reservation reservation;
	struct resource_account_handle account;
	struct agent_evidence_event *event;
	struct thread *thread;
	uint64 thread_generation;
	uint64 audit_sequence;
	int reserve_status;
	int commit_status;
	int enabled;

	if (ticket_out == 0)
		return -1;
	*ticket_out = 0;
	if (actor == 0 || actor->is_agent || syscall_id < 0 ||
	    (uint)syscall_id > AGENT_EVIDENCE_DIRECT_SYSCALL_ID_MASK ||
	    side_effect_mask == 0 ||
	    (side_effect_mask & ~AGENT_SIDE_EFFECT_ALL) != 0 ||
	    !agent_evidence_lifecycle_participant_valid(actor, key, &thread))
		return -1;
	account = actor->resource_account;
	thread_generation = thread->identity_generation;

	/* This path may consume only the controller-funded critical reserve. */
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	reserve_status =
		agent_evidence_direct_actor_valid_locked(
			actor, thread, key, state, thread_generation, account) ?
			agent_evidence_reserve_locked(
				state, 1, &reservation) :
			AGENT_EVIDENCE_RESERVE_RETRY;
	intr_restore(enabled);
	/* Full, sealing, unprepared, and generation-mismatched rings fail closed. */
	if (reserve_status != AGENT_EVIDENCE_RESERVE_OK)
		return -1;

	audit_sequence = agent_observe_alloc_audit_sequence();
	if (audit_sequence == 0) {
		enabled = intr_save();
		state = agent_evidence_domain_locked(key, 0);
		agent_evidence_discard_locked(state, &reservation);
		intr_restore(enabled);
		return -1;
	}
	event = &reservation.slot->event;
	memset(event, 0, sizeof(*event));
	event->ticket = reservation.ticket;
	event->audit_sequence = audit_sequence;
	event->context_sequence = 0;
	event->request_id =
		(audit_sequence << 1) ^ reservation.ticket ^
		((uint64)(uint)actor->pid << 32) ^
		(uint64)(uint)syscall_id;
	if (event->request_id == 0)
		event->request_id = 1;
	event->tick = get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
	event->arg0 = side_effect_mask;
	event->value0 = side_effect_mask;
	event->value1 = thread_generation;
	event->value2 = AGENT_PROVENANCE_DENY_MISSING_CONTRACT;
	event->flags = AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL;
	event->evidence_flags = AGENT_EVIDENCE_F_CRITICAL |
		AGENT_EVIDENCE_F_SECURITY_DENIAL |
		AGENT_EVIDENCE_F_DIRECT_DENIAL;
	event->pid = actor->pid;
	event->tid = thread->tid;
	event->source_pid = actor->pid;
	event->target_pid = actor->pid;
	event->tool_id = (int)(AGENT_EVIDENCE_DIRECT_SYSCALL_NAMESPACE |
			       (uint)syscall_id);
	event->status = AGENT_STATUS_DENIED;
	safestrcpy(event->payload, "direct_syscall", sizeof(event->payload));
	safestrcpy(event->result, "deny_missing_contract",
		   sizeof(event->result));
	/* Hash after all projected fields are populated. */
	event->record_hash = agent_evidence_direct_record_hash(
		key, account, event);

	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	commit_status = agent_evidence_direct_actor_valid_locked(
		actor, thread, key, state, thread_generation, account) ?
		agent_evidence_commit_locked(state, &reservation) : -1;
	if (commit_status < 0 && state != 0)
		agent_evidence_discard_locked(state, &reservation);
	intr_restore(enabled);
	if (commit_status < 0)
		return -1;
	*ticket_out = reservation.ticket;
	return reservation.ticket != 0 ? 0 : -1;
}

static int
agent_evidence_context_event_init(
	struct proc *p, const struct agent_context_record *record,
	uint64 audit_sequence, uint64 span_owner, int source_pid,
	uint64 cause_control, uint64 cause_branch, int authority_effect,
	int causal_audit, struct agent_evidence_event *event, int *critical_out)
{
	struct thread *thread = curr_thread();
	int critical;

	if (p == 0 || record == 0 || event == 0 || critical_out == 0 ||
	    !p->is_agent || record->sequence == 0 || audit_sequence == 0)
		return -1;
	critical = authority_effect || record->status != AGENT_STATUS_OK;
	memset(event, 0, sizeof(*event));
	event->audit_sequence = audit_sequence;
	event->context_sequence = record->sequence;
	event->request_id = record->request_id;
	event->tick = record->tick;
	event->cause_sequence = record->cause_sequence;
	event->span_id = record->span_id;
	event->branch_generation = record->branch_generation;
	event->path_parent_sequence = record->path_parent_sequence;
	event->arg0 = record->arg0;
	event->cause_branch_generation = cause_branch;
	event->actor_control_id = p->agent_control_id;
	event->cause_control_id = cause_control;
	event->cause_record_hash = cause_control == p->agent_control_id ?
					 record->prev_hash : 0;
	event->prev_hash = record->prev_hash;
	event->record_hash = record->record_hash;
	event->value0 = record->value0;
	event->value1 = record->value1;
	event->value2 = record->value2;
	event->flags = record->flags;
	event->span_owner = span_owner;
	event->evidence_flags =
		(critical ? AGENT_EVIDENCE_F_CRITICAL : 0) |
		(causal_audit ? AGENT_EVIDENCE_F_CAUSAL : 0) |
		((record->flags &
		  AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL) != 0 ?
			 AGENT_EVIDENCE_F_SECURITY_DENIAL : 0);
	event->pid = p->pid;
	event->tid = thread != 0 && thread->process == p ? thread->tid : 0;
	event->source_pid = source_pid > 0 ? source_pid : p->pid;
	event->target_pid = p->pid;
	event->agent_id = p->agent_id;
	event->role = p->agent_role;
	event->loop_state = thread != 0 && thread->process == p ?
				    thread->agent_loop_state : p->loop_state;
	event->tool_id = record->tool_id;
	event->status = record->status;
	memmove(event->payload, record->payload, sizeof(event->payload));
	memmove(event->result, record->result, sizeof(event->result));
	*critical_out = critical;
	return 0;
}

int
agent_evidence_append_context(struct proc *p,
			      const struct agent_context_record *record,
			      uint64 audit_sequence, uint64 span_owner,
			      int source_pid,
			      uint64 cause_control, uint64 cause_branch,
			      int authority_effect, int causal_audit,
			      uint64 *ticket_out)
{
	struct agent_evidence_event event;
	struct agent_evidence_domain *state;
	struct agent_evidence_reservation reservation;
	struct workflow_lifecycle_key key;
	int critical;
	int enabled;
	int reserve_status;
	int commit_status;

	if (ticket_out != 0)
		*ticket_out = 0;
	if (agent_evidence_context_event_init(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, authority_effect, causal_audit,
		    &event, &critical) < 0)
		return -1;
	key.id = p->workflow_lifecycle_id;
	key.generation = p->workflow_lifecycle_generation;
	/* Proc publication already binds scope to this immutable generation. */
	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return -1;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 1);
	intr_restore(enabled);
	if (state == 0 || agent_evidence_pages_ensure(key, p) < 0)
		return -1;

	for (uint attempt = 0; attempt < 2U; attempt++) {
		enabled = intr_save();
		state = agent_evidence_domain_locked(key, 0);
		reserve_status = state == 0 ? AGENT_EVIDENCE_RESERVE_RETRY :
			agent_evidence_reserve_locked(
				state, critical, &reservation);
		intr_restore(enabled);
		if (reserve_status == AGENT_EVIDENCE_RESERVE_ROLLOVER) {
			if (agent_evidence_rollover(key) < 0)
				return -1;
			continue;
		}
		if (reserve_status != AGENT_EVIDENCE_RESERVE_OK)
			return -1;

		/* Fill is outside IRQ-off; BUSY prevents readers from observing it. */
		event.ticket = reservation.ticket;
		memmove(&reservation.slot->event, &event, sizeof(event));
		enabled = intr_save();
		state = agent_evidence_domain_locked(key, 0);
		commit_status = agent_evidence_commit_locked(
			state, &reservation);
		if (commit_status < 0)
			agent_evidence_discard_locked(state, &reservation);
		intr_restore(enabled);
		if (commit_status == 0 && ticket_out != 0)
			*ticket_out = reservation.ticket;
		return commit_status;
	}
	return -1;
}

int
agent_evidence_context_reserve(
	struct proc *p, struct agent_evidence_context_reservation *out)
{
	struct agent_evidence_domain *state;
	struct agent_evidence_slot *ordinary, *critical;
	struct workflow_lifecycle_key key;
	uint ordinary_index = 0, critical_index = 0;
	int reserve_status;
	int enabled;

	if (out == 0)
		return -1;
	memset(out, 0, sizeof(*out));
	if (p == 0 || !p->is_agent)
		return -1;
	key.id = p->workflow_lifecycle_id;
	key.generation = p->workflow_lifecycle_generation;
	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return -1;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 1);
	intr_restore(enabled);
	if (state == 0 || agent_evidence_pages_ensure(key, p) < 0)
		return -1;

	for (uint attempt = 0; attempt < 2U; attempt++) {
		enabled = intr_save();
		state = agent_evidence_domain_locked(key, 0);
		reserve_status = AGENT_EVIDENCE_RESERVE_RETRY;
		ordinary = 0;
		critical = 0;
		if (state != 0 && !state->sealing && !state->allocating &&
		    agent_evidence_pages_ready(state) &&
		    state->next_ticket != 0 && state->inflight <= (uint)-3) {
			ordinary = agent_evidence_free_slot_locked(
				state, 0, &ordinary_index);
			critical = agent_evidence_free_slot_locked(
				state, 1, &critical_index);
			reserve_status = ordinary != 0 && critical != 0 ?
				AGENT_EVIDENCE_RESERVE_OK :
				AGENT_EVIDENCE_RESERVE_ROLLOVER;
		}
		if (reserve_status == AGENT_EVIDENCE_RESERVE_OK) {
			out->key = key;
			out->ordinary_slot = ordinary;
			out->critical_slot = critical;
			out->ticket = state->next_ticket++;
			out->active = 1;
			state->ordinary_cursor =
				(ordinary_index + 1U) % AGENT_EVIDENCE_ORDINARY_CAP;
			state->critical_cursor =
				(critical_index + 1U) % AGENT_EVIDENCE_CRITICAL_CAP;
			ordinary->reserved_ticket = out->ticket;
			critical->reserved_ticket = out->ticket;
			__atomic_store_n(&ordinary->state,
					 AGENT_EVIDENCE_SLOT_BUSY,
					 __ATOMIC_RELEASE);
			__atomic_store_n(&critical->state,
					 AGENT_EVIDENCE_SLOT_BUSY,
					 __ATOMIC_RELEASE);
			state->inflight += 2U;
		}
		intr_restore(enabled);
		if (reserve_status == AGENT_EVIDENCE_RESERVE_OK)
			return 0;
		if (reserve_status != AGENT_EVIDENCE_RESERVE_ROLLOVER ||
		    agent_evidence_rollover(key) < 0)
			return -1;
	}
	return -1;
}

static int
agent_evidence_context_release_locked(
	struct agent_evidence_domain *state,
	struct agent_evidence_context_reservation *reservation, int gap)
{
	struct agent_evidence_slot *ordinary = reservation->ordinary_slot;
	struct agent_evidence_slot *critical = reservation->critical_slot;

	if (state == 0 || ordinary == 0 || critical == 0 ||
	    !workflow_lifecycle_key_equal(state->key, reservation->key) ||
	    ordinary->state != AGENT_EVIDENCE_SLOT_BUSY ||
	    critical->state != AGENT_EVIDENCE_SLOT_BUSY ||
	    ordinary->reserved_ticket != reservation->ticket ||
	    critical->reserved_ticket != reservation->ticket ||
	    state->inflight < 2U)
		return -1;
	ordinary->reserved_ticket = 0;
	critical->reserved_ticket = 0;
	__atomic_store_n(&ordinary->state,
		gap ? AGENT_EVIDENCE_SLOT_DISCARDED : AGENT_EVIDENCE_SLOT_FREE,
		__ATOMIC_RELEASE);
	__atomic_store_n(&critical->state,
		gap ? AGENT_EVIDENCE_SLOT_DISCARDED : AGENT_EVIDENCE_SLOT_FREE,
		__ATOMIC_RELEASE);
	state->inflight -= 2U;
	if (gap)
		agent_evidence_note_ticket_locked(
			state, reservation->ticket, 1, 0);
	return 0;
}

void
agent_evidence_context_abort(
	struct agent_evidence_context_reservation *reservation)
{
	struct agent_evidence_domain *state;
	int enabled;

	if (reservation == 0 || !reservation->active)
		return;
	enabled = intr_save();
	state = agent_evidence_domain_locked(reservation->key, 0);
	if (agent_evidence_context_release_locked(state, reservation, 1) < 0)
		panic("evidence context reservation abort");
	intr_restore(enabled);
	memset(reservation, 0, sizeof(*reservation));
}

int
agent_evidence_context_commit(
	struct proc *p, const struct agent_context_record *record,
	uint64 audit_sequence, uint64 span_owner, int source_pid,
	uint64 cause_control, uint64 cause_branch, int authority_effect,
	int causal_audit,
	struct agent_evidence_context_reservation *reservation,
	uint64 *ticket_out)
{
	struct agent_evidence_event event;
	struct agent_evidence_reservation selected;
	struct agent_evidence_domain *state;
	struct agent_evidence_slot *unused;
	struct workflow_lifecycle_key key;
	int critical;
	int enabled;
	int result = -1;

	if (ticket_out == 0)
		return -1;
	*ticket_out = 0;
	if (p == 0 || record == 0 || reservation == 0 ||
	    !reservation->active || reservation->ticket == 0 ||
	    agent_evidence_context_event_init(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, authority_effect, causal_audit,
		    &event, &critical) < 0)
		return -1;
	key.id = p->workflow_lifecycle_id;
	key.generation = p->workflow_lifecycle_generation;
	if (!workflow_lifecycle_key_equal(key, reservation->key))
		return -1;
	memset(&selected, 0, sizeof(selected));
	selected.key = reservation->key;
	selected.slot = critical ? reservation->critical_slot :
				   reservation->ordinary_slot;
	selected.ticket = reservation->ticket;
	selected.critical = critical;
	unused = critical ? reservation->ordinary_slot :
			    reservation->critical_slot;
	event.ticket = selected.ticket;
	memmove(&selected.slot->event, &event, sizeof(event));

	enabled = intr_save();
	state = agent_evidence_domain_locked(selected.key, 0);
	if (state == 0 || state->sealing || unused == 0 ||
	    unused->state != AGENT_EVIDENCE_SLOT_BUSY ||
	    unused->reserved_ticket != selected.ticket ||
	    selected.slot->state != AGENT_EVIDENCE_SLOT_BUSY ||
	    selected.slot->reserved_ticket != selected.ticket ||
	    state->inflight < 2U)
		panic("evidence context reservation commit");
	unused->reserved_ticket = 0;
	__atomic_store_n(&unused->state, AGENT_EVIDENCE_SLOT_FREE,
			 __ATOMIC_RELEASE);
	state->inflight--;
	result = agent_evidence_commit_locked(state, &selected);
	if (result < 0)
		panic("evidence context commit");
	intr_restore(enabled);
	if (result == 0)
		*ticket_out = selected.ticket;
	memset(reservation, 0, sizeof(*reservation));
	return result;
}

int
agent_evidence_security_reserve(
	struct proc *p, struct agent_evidence_security_reservation *out)
{
	struct agent_evidence_domain *state;
	struct agent_evidence_reservation reservation;
	struct workflow_lifecycle_key key;
	int reserve_status;
	int enabled;

	if (out == 0)
		return -1;
	memset(out, 0, sizeof(*out));
	if (p == 0 || !p->is_agent)
		return -1;
	key.id = p->workflow_lifecycle_id;
	key.generation = p->workflow_lifecycle_generation;
	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return -1;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 1);
	intr_restore(enabled);
	if (state == 0 || agent_evidence_pages_ensure(key, p) < 0)
		return -1;
	for (uint attempt = 0; attempt < 2U; attempt++) {
		enabled = intr_save();
		state = agent_evidence_domain_locked(key, 0);
		reserve_status = state == 0 ? AGENT_EVIDENCE_RESERVE_RETRY :
			agent_evidence_reserve_locked(
				state, 1, &reservation);
		intr_restore(enabled);
		if (reserve_status == AGENT_EVIDENCE_RESERVE_ROLLOVER) {
			if (agent_evidence_rollover(key) < 0)
				return -1;
			continue;
		}
		if (reserve_status != AGENT_EVIDENCE_RESERVE_OK)
			return -1;
		out->key = reservation.key;
		out->slot = reservation.slot;
		out->ticket = reservation.ticket;
		out->active = 1;
		return 0;
	}
	return -1;
}

void
agent_evidence_security_abort(
	struct agent_evidence_security_reservation *reservation)
{
	struct agent_evidence_reservation internal;
	struct agent_evidence_domain *state;
	int enabled;

	if (reservation == 0 || !reservation->active)
		return;
	memset(&internal, 0, sizeof(internal));
	internal.key = reservation->key;
	internal.slot = reservation->slot;
	internal.ticket = reservation->ticket;
	internal.critical = 1;
	enabled = intr_save();
	state = agent_evidence_domain_locked(reservation->key, 0);
	if (state != 0)
		agent_evidence_discard_locked(state, &internal);
	intr_restore(enabled);
	memset(reservation, 0, sizeof(*reservation));
}

int
agent_evidence_security_commit(
	struct proc *p, const struct agent_context_record *record,
	uint64 audit_sequence, uint64 span_owner, int source_pid,
	uint64 cause_control, uint64 cause_branch,
	struct agent_evidence_security_reservation *reservation,
	uint64 *ticket_out)
{
	struct agent_evidence_event event;
	struct agent_evidence_reservation internal;
	struct agent_evidence_domain *state;
	struct workflow_lifecycle_key key;
	int critical;
	int commit_status;
	int enabled;

	if (ticket_out == 0)
		return -1;
	*ticket_out = 0;
	if (p == 0 || record == 0 || reservation == 0 ||
	    !reservation->active || reservation->slot == 0 ||
	    reservation->ticket == 0 || record->status == AGENT_STATUS_OK ||
	    (record->flags & AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL) == 0 ||
	    agent_evidence_context_event_init(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, 1, 1, &event, &critical) < 0 ||
	    !critical)
		return -1;
	key.id = p->workflow_lifecycle_id;
	key.generation = p->workflow_lifecycle_generation;
	if (!workflow_lifecycle_key_equal(key, reservation->key))
		return -1;
	memset(&internal, 0, sizeof(internal));
	internal.key = reservation->key;
	internal.slot = reservation->slot;
	internal.ticket = reservation->ticket;
	internal.critical = 1;
	event.ticket = internal.ticket;
	memmove(&internal.slot->event, &event, sizeof(event));
	enabled = intr_save();
	state = agent_evidence_domain_locked(internal.key, 0);
	commit_status = agent_evidence_commit_locked(state, &internal);
	if (commit_status < 0 && state != 0)
		agent_evidence_discard_locked(state, &internal);
	intr_restore(enabled);
	if (commit_status == 0)
		*ticket_out = internal.ticket;
	memset(reservation, 0, sizeof(*reservation));
	return commit_status;
}

int
agent_evidence_append_security_denial(
	struct proc *p, const struct agent_context_record *record,
	uint64 audit_sequence, uint64 span_owner, int source_pid,
	uint64 cause_control, uint64 cause_branch, uint64 *ticket_out)
{
	struct agent_evidence_security_reservation reservation;

	if (ticket_out == 0)
		return -1;
	*ticket_out = 0;
	if (record == 0 || record->status == AGENT_STATUS_OK ||
	    (record->flags & AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL) == 0)
		return -1;
	if (agent_evidence_security_reserve(p, &reservation) < 0)
		return -1;
	if (agent_evidence_security_commit(
		    p, record, audit_sequence, span_owner, source_pid,
		    cause_control, cause_branch, &reservation, ticket_out) < 0) {
		agent_evidence_security_abort(&reservation);
		return -1;
	}
	return *ticket_out != 0 ? 0 : -1;
}

int
agent_evidence_view_open(struct workflow_lifecycle_key key,
			 struct agent_evidence_view *view)
{
	struct agent_evidence_domain *state;
	uint scope_id;
	int enabled;

	if (view == 0)
		return -1;
	memset(view, 0, sizeof(*view));
	if (workflow_lifecycle_scope(key, &scope_id) < 0)
		return 0;
	(void)scope_id;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	if (state == 0) {
		intr_restore(enabled);
		return 0;
	}
	view->key = key;
	uint64 publish_limit = ~0ULL;
	uint64 published_first = 0;
	uint64 published_last = 0;
	uint64 hidden_events = 0;
	uint64 hidden_critical = 0;
	uint64 hidden_gaps = 0;

	/* A later producer may commit while an earlier ticket is still BUSY. */
	for (uint i = 0; i < AGENT_EVIDENCE_ORDINARY_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 0, i);
		if (slot != 0 && __atomic_load_n(&slot->state, __ATOMIC_ACQUIRE) ==
				 AGENT_EVIDENCE_SLOT_BUSY &&
		    slot->reserved_ticket < publish_limit)
			publish_limit = slot->reserved_ticket;
	}
	for (uint i = 0; i < AGENT_EVIDENCE_CRITICAL_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 1, i);
		if (slot != 0 && __atomic_load_n(&slot->state, __ATOMIC_ACQUIRE) ==
				 AGENT_EVIDENCE_SLOT_BUSY &&
		    slot->reserved_ticket < publish_limit)
			publish_limit = slot->reserved_ticket;
	}

	view->observe_epoch = state->observe_epoch;
	view->last_fence_sequence = state->last_fence_sequence;
	int have_rollup = agent_evidence_root_present(state->sealed_root);
	memmove(view->sealed_root,
		have_rollup ? state->sealed_root : state->fence_root,
		sizeof(view->sealed_root));
	for (uint i = 0; i < AGENT_EVIDENCE_ORDINARY_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 0, i);
		uchar slot_state = slot == 0 ? AGENT_EVIDENCE_SLOT_FREE :
			__atomic_load_n(&slot->state, __ATOMIC_ACQUIRE);
		uint64 ticket = slot == 0 ? 0 : slot->event.ticket;

		if (slot_state != AGENT_EVIDENCE_SLOT_COMMITTED &&
		    slot_state != AGENT_EVIDENCE_SLOT_DISCARDED)
			continue;
		if (ticket >= publish_limit) {
			hidden_events++;
			if (slot_state == AGENT_EVIDENCE_SLOT_DISCARDED)
				hidden_gaps++;
			continue;
		}
		if (published_first == 0 || ticket < published_first)
			published_first = ticket;
		if (ticket > published_last)
			published_last = ticket;
		if (slot_state == AGENT_EVIDENCE_SLOT_COMMITTED)
			agent_evidence_ref_insert(view, (ushort)i, ticket);
	}
	for (uint i = 0; i < AGENT_EVIDENCE_CRITICAL_CAP; i++) {
		struct agent_evidence_slot *slot =
			agent_evidence_slot_at(state, 1, i);
		uchar slot_state = slot == 0 ? AGENT_EVIDENCE_SLOT_FREE :
			__atomic_load_n(&slot->state, __ATOMIC_ACQUIRE);
		uint64 ticket = slot == 0 ? 0 : slot->event.ticket;

		if (slot_state != AGENT_EVIDENCE_SLOT_COMMITTED &&
		    slot_state != AGENT_EVIDENCE_SLOT_DISCARDED)
			continue;
		if (ticket >= publish_limit) {
			hidden_events++;
			if (slot_state == AGENT_EVIDENCE_SLOT_COMMITTED)
				hidden_critical++;
			else
				hidden_gaps++;
			continue;
		}
		if (published_first == 0 || ticket < published_first)
			published_first = ticket;
		if (ticket > published_last)
			published_last = ticket;
		if (slot_state == AGENT_EVIDENCE_SLOT_COMMITTED)
			agent_evidence_ref_insert(
				view,
				(ushort)(AGENT_EVIDENCE_REF_CRITICAL | i), ticket);
	}
	view->total_records = state->total_events - hidden_events;
	view->critical_records = state->total_critical_events - hidden_critical;
	view->gap_count = state->total_gaps - hidden_gaps;
	view->first_ticket = published_first;
	view->last_ticket = published_last;
	intr_restore(enabled);
	return 1;
}

static int
agent_evidence_view_event_locked(const struct agent_evidence_view *view,
				 uint index,
				 struct agent_evidence_event *event)
{
	struct agent_evidence_domain *state;
	struct agent_evidence_slot *slot;

	if (view == 0 || event == 0 || index >= view->visible_records ||
	    view->visible_records > AGENT_EVIDENCE_CAP)
		return 0;
	state = agent_evidence_domain_locked(view->key, 0);
	if (state == 0)
		return 0;
	slot = agent_evidence_slot_from_ref(state, view->entries[index].ref);
	if (slot == 0 ||
	    __atomic_load_n(&slot->state, __ATOMIC_ACQUIRE) !=
		    AGENT_EVIDENCE_SLOT_COMMITTED ||
	    slot->event.ticket != view->entries[index].ticket)
		return 0;
	memmove(event, &slot->event, sizeof(*event));
	return 1;
}

static void
agent_evidence_event_to_audit(const struct agent_evidence_view *view,
			      const struct agent_evidence_event *event,
			      struct agent_audit_record *record)
{
	memset(record, 0, sizeof(*record));
	record->sequence = event->audit_sequence;
	record->tick = event->tick;
	record->cause_sequence = event->cause_sequence;
	record->span_id = event->span_id;
	record->workflow_lifecycle_id = view->key.id;
	record->workflow_lifecycle_generation = view->key.generation;
	record->branch_generation = event->branch_generation;
	record->cause_branch_generation = event->cause_branch_generation;
	record->actor_control_id = event->actor_control_id;
	record->cause_control_id = event->cause_control_id;
	record->cause_record_hash = event->cause_record_hash;
	record->prev_hash = event->prev_hash;
	record->record_hash = event->record_hash;
	record->value0 = event->value0;
	record->value1 = event->value1;
	record->value2 = event->value2;
	record->flags = event->flags;
	record->kind = AGENT_AUDIT_KIND_CONTEXT;
	record->pid = event->pid;
	record->tid = event->tid;
	record->source_pid = event->source_pid;
	record->target_pid = event->target_pid;
	record->agent_id = event->agent_id;
	record->role = event->role;
	record->loop_state = event->loop_state;
	record->tool_id = event->tool_id;
	record->event_type = 0;
	record->status = event->status;
	safestrcpy(record->text,
		event->result[0] ? event->result : event->payload,
		sizeof(record->text));
}

int
agent_evidence_view_record(const struct agent_evidence_view *view, uint index,
			   struct agent_audit_record *record,
			   uint64 *span_owner)
{
	struct agent_evidence_event event;
	int found;
	int enabled = intr_save();

	found = agent_evidence_view_event_locked(view, index, &event);
	if (found && record != 0)
		agent_evidence_event_to_audit(view, &event, record);
	if (found && span_owner != 0)
		*span_owner = event.span_owner;
	intr_restore(enabled);
	return found;
}

int
agent_evidence_view_timeline(const struct agent_evidence_view *view,
			     uint index,
			     struct agent_timeline_record *timeline,
			     uint64 *span_owner)
{
	struct agent_evidence_event event;
	int found;
	int enabled = intr_save();

	if (timeline == 0) {
		intr_restore(enabled);
		return 0;
	}
	found = agent_evidence_view_event_locked(view, index, &event);
	if (found) {
		memset(timeline, 0, sizeof(*timeline));
		timeline->source = AGENT_TIMELINE_SOURCE_CONTEXT;
		timeline->kind = AGENT_TRACE_KIND_CONTEXT;
		timeline->tick = event.tick;
		timeline->sequence = event.context_sequence;
		timeline->cause_sequence = event.cause_sequence;
		timeline->span_id = event.span_id;
		timeline->workflow_lifecycle_id = view->key.id;
		timeline->workflow_lifecycle_generation = view->key.generation;
		timeline->branch_generation = event.branch_generation;
		timeline->cause_branch_generation = event.cause_branch_generation;
		timeline->actor_control_id = event.actor_control_id;
		timeline->cause_control_id = event.cause_control_id;
		timeline->cause_record_hash = event.cause_record_hash;
		timeline->value0 = event.value0;
		timeline->value1 = event.value1;
		timeline->value2 = event.value2;
		timeline->flags = event.flags;
		timeline->pid = event.pid;
		timeline->tid = event.tid;
		timeline->source_pid = event.source_pid;
		timeline->target_pid = event.target_pid;
		timeline->role = event.role;
		timeline->loop_state = event.loop_state;
		timeline->tool_id = event.tool_id;
		timeline->event_type = 0;
		timeline->status = event.status;
		safestrcpy(timeline->text,
			event.result[0] ? event.result : event.payload,
			sizeof(timeline->text));
		if (span_owner != 0)
			*span_owner = event.span_owner;
	}
	intr_restore(enabled);
	return found;
}

uint
agent_evidence_view_count_pid(const struct agent_evidence_view *view, int pid)
{
	struct agent_evidence_event event;
	uint count = 0;
	int enabled = intr_save();

	for (uint i = 0; view != 0 && i < view->visible_records; i++)
		if (agent_evidence_view_event_locked(view, i, &event) &&
		    event.pid == pid)
			count++;
	intr_restore(enabled);
	return count;
}

int
agent_evidence_view_digest(
	const struct agent_evidence_view *view,
	uchar out[AGENT_SHA256_DIGEST_SIZE])
{
	static const char domain[] = "AgentOS evidence view v1";
	struct agent_evidence_event event;
	struct agent_sha256_ctx hash;
	uchar leaf[AGENT_SHA256_DIGEST_SIZE];

	if (out == 0)
		return -1;
	memset(out, 0, AGENT_SHA256_DIGEST_SIZE);
	if (view == 0 || !workflow_lifecycle_key_valid(view->key) ||
	    view->visible_records > AGENT_EVIDENCE_CAP)
		return -1;
	agent_sha256_init(&hash);
	agent_sha256_update(&hash, domain, sizeof(domain) - 1U);
	agent_evidence_hash_u32(&hash, view->key.id);
	agent_evidence_hash_u64(&hash, view->key.generation);
	agent_evidence_hash_u64(&hash, view->last_fence_sequence);
	agent_sha256_update(&hash, view->sealed_root,
			    AGENT_SHA256_DIGEST_SIZE);
	agent_evidence_hash_u64(&hash, view->total_records);
	agent_evidence_hash_u64(&hash, view->critical_records);
	agent_evidence_hash_u64(&hash, view->gap_count);
	agent_evidence_hash_u64(&hash, view->first_ticket);
	agent_evidence_hash_u64(&hash, view->last_ticket);
	agent_evidence_hash_u64(&hash, view->observe_epoch);
	agent_evidence_hash_u32(&hash, view->visible_records);
	for (uint i = 0; i < view->visible_records; i++) {
		int found;
		int enabled = intr_save();

		found = agent_evidence_view_event_locked(view, i, &event);
		intr_restore(enabled);
		if (!found) {
			memset(&event, 0, sizeof(event));
			memset(leaf, 0, sizeof(leaf));
			return -1;
		}
		agent_evidence_hash_event(&event, leaf);
		agent_sha256_update(&hash, leaf, sizeof(leaf));
	}
	agent_sha256_final(&hash, out);
	memset(&event, 0, sizeof(event));
	memset(leaf, 0, sizeof(leaf));
	return 0;
}

int
agent_evidence_seal(struct workflow_lifecycle_key key,
		    uint64 workflow_fence_sequence,
		    const uchar challenge[AGENT_SHA256_DIGEST_SIZE],
		    uint64 metadata_generation, uint64 credit_epoch,
		    const uchar credit_digest[AGENT_SHA256_DIGEST_SIZE],
		    struct agent_evidence_seal_result *out)
{
	struct agent_evidence_domain *state;
	uint scope_id;
	int status;
	int enabled;

	if (challenge == 0 || credit_digest == 0 || out == 0 ||
	    workflow_lifecycle_scope(key, &scope_id) < 0)
		return -1;
	(void)scope_id;
	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 1);
	intr_restore(enabled);
	if (state == 0)
		return -1;
	status = agent_evidence_seal_stable(
		state, workflow_fence_sequence, challenge, metadata_generation,
		credit_epoch, credit_digest, 1, out);
	return status;
}

int
agent_evidence_reclaim(struct workflow_lifecycle_key key)
{
	struct agent_evidence_page_release release;
	struct agent_evidence_seal_plan plan;
	struct agent_evidence_domain *state;
	int enabled;
	int status;
	int uncovered;

	enabled = intr_save();
	state = agent_evidence_domain_locked(key, 0);
	if (state == 0) {
		intr_restore(enabled);
		return 0;
	}
	if (state->sealing || state->allocating || state->inflight != 0 ||
	    !workflow_lifecycle_retiring(key)) {
		intr_restore(enabled);
		return -1;
	}
	uncovered = state->fence_events != 0 || state->fence_gaps != 0 ||
		    state->segment_events != 0 || state->segment_gaps != 0 ||
		    agent_evidence_root_present(state->sealed_root);
	if (!uncovered) {
		/* Zero-event domains and externally fenced domains are disposable. */
		if (state->total_events != 0 && state->last_fence_sequence == 0) {
			intr_restore(enabled);
			return -1;
		}
		agent_evidence_domain_release_locked(state, &release);
		intr_restore(enabled);
		agent_evidence_pages_release(&release);
		return 0;
	}
	if (state->segment_sequence == ~0ULL) {
		intr_restore(enabled);
		return -1;
	}
	state->sealing = 1;
	intr_restore(enabled);

	/* Retirement roots are internal tombstones, never external receipts. */
	status = agent_evidence_prepare_retirement_stable(state, &plan);
	enabled = intr_save();
	if (status < 0 || !state->sealing || state->allocating ||
	    state->inflight != 0 ||
	    state->mutation_epoch != plan.mutation_epoch ||
	    !workflow_lifecycle_retiring(key)) {
		state->sealing = 0;
		intr_restore(enabled);
		return -1;
	}
	agent_evidence_retained_publish_locked(
		key, AGENT_EVIDENCE_RETAINED_F_INTERNAL_RETIRE, &plan.result);
	agent_evidence_domain_release_locked(state, &release);
	intr_restore(enabled);
	agent_evidence_pages_release(&release);
	return 0;
}

int
agent_evidence_retained_get(struct workflow_lifecycle_key key,
			    struct agent_evidence_retained_seal *out)
{
	struct agent_evidence_retained_seal *retained;
	int enabled;

	if (out == 0 || !workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return -1;
	memset(out, 0, sizeof(*out));
	enabled = intr_save();
	retained = &agent_evidence_retained[key.id - 1U];
	if (!workflow_lifecycle_key_equal(retained->key, key)) {
		intr_restore(enabled);
		return 0;
	}
	memmove(out, retained, sizeof(*out));
	intr_restore(enabled);
	return 1;
}

int
agent_evidence_ticket_fence_sealed(struct workflow_lifecycle_key key,
				   uint64 ticket, uint64 *fence_sequence)
{
	struct agent_evidence_domain *state;
	struct agent_evidence_retained_seal *retained;
	uint64 highwater = 0;
	uint64 sequence = 0;
	int enabled;

	if (fence_sequence != 0)
		*fence_sequence = 0;
	if (!workflow_lifecycle_key_valid(key) ||
	    key.id > WORKFLOW_LIFECYCLE_CAP || ticket == 0)
		return 0;
	enabled = intr_save();
	state = &agent_evidence_domains[key.id - 1U];
	if (state->used && workflow_lifecycle_key_equal(state->key, key)) {
		highwater = state->sealed_ticket_highwater;
		sequence = state->last_fence_sequence;
	} else {
		retained = &agent_evidence_retained[key.id - 1U];
		if (workflow_lifecycle_key_equal(retained->key, key) &&
		    (retained->flags &
		     AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE) != 0) {
			highwater = retained->sealed_ticket_highwater;
			sequence = retained->last_workflow_fence_sequence;
		}
	}
	intr_restore(enabled);
	if (ticket > highwater || sequence == 0)
		return 0;
	if (fence_sequence != 0)
		*fence_sequence = sequence;
	return 1;
}
