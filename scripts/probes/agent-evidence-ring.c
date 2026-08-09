#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../os/const.h"
#include "../../os/resource_controller.h"
#include "../../os/workflow_lifecycle.h"

#define AGENT_INTERNAL_H
#define DEFS_H
#define KALLOC_H
#define TRAP_H
#define VFS_SECURITY_H

#define AGENT_CONTEXT_TEXT_SIZE 16
#define AGENT_AUDIT_TEXT_SIZE 32
#define AGENT_STATUS_OK 0
#define AGENT_AUDIT_KIND_CONTEXT 1
#define AGENT_TIMELINE_SOURCE_CONTEXT 1
#define AGENT_TRACE_KIND_CONTEXT 1

struct proc {
	int is_agent;
	uint workflow_lifecycle_id;
	uint64 workflow_lifecycle_generation;
	struct resource_account_handle resource_account;
	int resource_slot_reserved;
	int pid;
	uint64 agent_control_id;
	int agent_id;
	int agent_role;
	int loop_state;
	uint vfs_scope_id;
};

struct thread {
	struct proc *process;
	int tid;
	int agent_loop_state;
};

struct agent_context_record {
	uint64 sequence;
	uint64 request_id;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 branch_generation;
	uint64 path_parent_sequence;
	uint64 arg0;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 tick;
	uint64 flags;
	uint64 prev_hash;
	uint64 record_hash;
	int tool_id;
	int status;
	char payload[AGENT_CONTEXT_TEXT_SIZE];
	char result[AGENT_CONTEXT_TEXT_SIZE];
};

struct agent_audit_record {
	uint64 sequence;
	uint64 tick;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
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
	int kind;
	uint workflow_lifecycle_id;
	int pid;
	int tid;
	int source_pid;
	int target_pid;
	int agent_id;
	int role;
	int loop_state;
	int tool_id;
	int event_type;
	int status;
	char text[AGENT_AUDIT_TEXT_SIZE];
};

struct agent_timeline_record {
	uint64 tick;
	uint64 sequence;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
	uint64 cause_branch_generation;
	uint64 actor_control_id;
	uint64 cause_control_id;
	uint64 cause_record_hash;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 flags;
	int source;
	int kind;
	uint workflow_lifecycle_id;
	int pid;
	int tid;
	int source_pid;
	int target_pid;
	int role;
	int loop_state;
	int tool_id;
	int event_type;
	int status;
	char text[AGENT_AUDIT_TEXT_SIZE];
};

static struct workflow_lifecycle_key current_key;
static uint current_scope;
static int current_retiring;
static struct thread current_thread;
static uint logical_pages;
static uint physical_pages;
static uint resource_domains_live;
static uint resource_domain_reaps;

struct probe_page {
	void *address;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
};

static struct probe_page probe_pages[WORKFLOW_EVIDENCE_PAGE_COUNT];

struct resource_account_handle
resource_account_none(void)
{
	struct resource_account_handle account = {0};

	return account;
}

int
resource_account_handle_valid(struct resource_account_handle account)
{
	return account.slot != 0 && account.generation != 0;
}

int
resource_account_handle_equal(struct resource_account_handle left,
			      struct resource_account_handle right)
{
	return left.slot == right.slot && left.generation == right.generation;
}

int
resource_account_active(struct resource_account_handle account)
{
	return resource_account_handle_valid(account) && !current_retiring;
}

int
resource_acquire_many(struct resource_account_handle account,
		      enum resource_charge_class charge_class,
		      const struct resource_request *requests, uint count)
{
	if (!resource_account_active(account) ||
	    charge_class != RESOURCE_CHARGE_RESERVED || requests == 0 ||
	    count != 1 || requests[0].kind != RESOURCE_AGENT_STATE_PAGE ||
	    requests[0].amount != WORKFLOW_EVIDENCE_PAGE_COUNT ||
	    logical_pages != 0)
		return -1;
	logical_pages = WORKFLOW_EVIDENCE_PAGE_COUNT;
	resource_domains_live = 1;
	return 0;
}

int
resource_release_many(struct resource_account_handle account,
		      enum resource_charge_class charge_class,
		      const struct resource_request *requests, uint count)
{
	if (!resource_account_handle_valid(account) ||
	    charge_class != RESOURCE_CHARGE_RESERVED || requests == 0 ||
	    count != 1 || requests[0].kind != RESOURCE_AGENT_STATE_PAGE ||
	    requests[0].amount != WORKFLOW_EVIDENCE_PAGE_COUNT ||
	    logical_pages != WORKFLOW_EVIDENCE_PAGE_COUNT ||
	    physical_pages != 0)
		return -1;
	logical_pages = 0;
	return 0;
}

void
proc_resource_account_reap(struct resource_account_handle account)
{
	assert(resource_account_handle_valid(account));
	if (logical_pages == 0 && physical_pages == 0 &&
	    resource_domains_live != 0) {
		resource_domains_live = 0;
		resource_domain_reaps++;
	}
}

void *
kalloc_account_page(struct resource_account_handle account,
		    enum resource_charge_class charge_class)
{
	void *page;

	if (!resource_account_active(account) ||
	    charge_class != RESOURCE_CHARGE_RESERVED ||
	    physical_pages >= WORKFLOW_EVIDENCE_PAGE_COUNT)
		return 0;
	page = aligned_alloc(PAGE_SIZE, PAGE_SIZE);
	if (page == 0)
		return 0;
	probe_pages[physical_pages].address = page;
	probe_pages[physical_pages].account = account;
	probe_pages[physical_pages].charge_class = charge_class;
	physical_pages++;
	return page;
}

int
kfree_account_page(void *page, struct resource_account_handle account,
		   enum resource_charge_class charge_class)
{
	for (uint i = 0; i < WORKFLOW_EVIDENCE_PAGE_COUNT; i++)
		if (probe_pages[i].address == page) {
			if (!resource_account_handle_equal(
				    probe_pages[i].account, account) ||
			    probe_pages[i].charge_class != charge_class)
				return -1;
			free(page);
			probe_pages[i].address = 0;
			physical_pages--;
			return 0;
		}
	return -1;
}

int
workflow_lifecycle_scope(struct workflow_lifecycle_key key, uint *scope_id)
{
	if (scope_id == 0 || !workflow_lifecycle_key_equal(key, current_key))
		return -1;
	*scope_id = current_scope;
	return 0;
}

int
workflow_lifecycle_retiring(struct workflow_lifecycle_key key)
{
	return current_retiring && workflow_lifecycle_key_equal(key, current_key);
}

int
workflow_lifecycle_active(struct workflow_lifecycle_key key)
{
	return !current_retiring &&
	       workflow_lifecycle_key_equal(key, current_key);
}

static int
intr_save(void)
{
	return 1;
}

static void
intr_restore(int enabled)
{
	(void)enabled;
}

static struct thread *
curr_thread(void)
{
	return &current_thread;
}

static char *
safestrcpy(char *dst, const char *src, int count)
{
	char *start = dst;

	if (count <= 0)
		return start;
	while (--count > 0 && (*dst++ = *src++) != '\0')
		;
	*dst = '\0';
	return start;
}

static void
panic(const char *message)
{
	fprintf(stderr, "panic: %s\n", message);
	abort();
}

#include "../../os/agent_evidence_ring.c"

static int
digest_present(const uchar digest[AGENT_SHA256_DIGEST_SIZE])
{
	for (uint i = 0; i < AGENT_SHA256_DIGEST_SIZE; i++)
		if (digest[i] != 0)
			return 1;
	return 0;
}

static void
activate(struct proc *p, uint id, uint64 generation, uint scope)
{
	assert(logical_pages == 0 && physical_pages == 0 &&
	       resource_domains_live == 0);
	memset(p, 0, sizeof(*p));
	current_key.id = id;
	current_key.generation = generation;
	current_scope = scope;
	current_retiring = 0;
	p->is_agent = 1;
	p->workflow_lifecycle_id = id;
	p->workflow_lifecycle_generation = generation;
	p->resource_account.slot = id;
	p->resource_account.generation = generation;
	p->resource_slot_reserved = 1;
	p->vfs_scope_id = scope;
	p->pid = (int)(100 + id);
	p->agent_control_id = 1000 + id;
	p->agent_id = (int)id;
	p->agent_role = 1;
	current_thread.process = p;
	current_thread.tid = 7;
	current_thread.agent_loop_state = 2;
}

static void
append_ok(struct proc *p, uint64 sequence)
{
	struct agent_context_record record;
	uint64 ticket = 0;

	memset(&record, 0, sizeof(record));
	record.sequence = sequence;
	record.tick = sequence * 3;
	record.branch_generation = 1;
	record.prev_hash = sequence - 1;
	record.record_hash = sequence;
	record.tool_id = 4;
	record.status = AGENT_STATUS_OK;
	memcpy(record.result, "ok", 3);
	assert(agent_evidence_append_context(
		p, &record, sequence + 1000, 0, p->pid,
		p->agent_control_id, 1, 0, 0, &ticket) == 0);
	assert(ticket == sequence);
}

static void
test_retirement_internal_seal(void)
{
	struct agent_evidence_retained_seal retained;
	struct agent_evidence_view view;
	struct proc process;
	struct workflow_lifecycle_key wrong;

	activate(&process, 1, 11, 21);
	append_ok(&process, 1);
	assert(agent_evidence_reclaim(current_key) < 0);
	assert(agent_evidence_view_open(current_key, &view) == 1);
	assert(view.visible_records == 1 && view.total_records == 1);
	current_retiring = 1;
	assert(agent_evidence_reclaim(current_key) == 0);
	assert(logical_pages == 0 && physical_pages == 0);
	assert(agent_evidence_view_open(current_key, &view) == 0);
	assert(agent_evidence_retained_get(current_key, &retained) == 1);
	assert(retained.flags == AGENT_EVIDENCE_RETAINED_F_INTERNAL_RETIRE);
	assert(retained.event_count == 1 && retained.gap_count == 0);
	assert(retained.first_ticket == 1 && retained.last_ticket == 1);
	assert(retained.last_workflow_fence_sequence == 0);
	assert(digest_present(retained.root));
	wrong = current_key;
	wrong.generation++;
	assert(agent_evidence_retained_get(wrong, &retained) == 0);
}

static void
test_external_fence_retention(void)
{
	struct agent_evidence_retained_seal retained;
	struct agent_evidence_seal_result seal;
	struct agent_evidence_seal_result unchanged;
	struct proc process;
	uchar challenge[AGENT_SHA256_DIGEST_SIZE];
	uchar credit_digest[AGENT_SHA256_DIGEST_SIZE];
	uchar sentinel[sizeof(unchanged)];

	activate(&process, 2, 17, 22);
	append_ok(&process, 1);
	append_ok(&process, 2);
	memset(challenge, 0x5a, sizeof(challenge));
	memset(credit_digest, 0x6b, sizeof(credit_digest));
	memset(&seal, 0, sizeof(seal));
	assert(agent_evidence_seal(
		current_key, 1, challenge, 44, 55, credit_digest, &seal) == 0);
	assert(seal.fence_sequence == 1 && seal.event_count == 2);
	assert(agent_evidence_retained_get(current_key, &retained) == 1);
	assert(retained.flags == AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE);
	assert(retained.last_workflow_fence_sequence == 1);
	assert(memcmp(retained.root, seal.root, sizeof(seal.root)) == 0);
	memset(&unchanged, 0xa5, sizeof(unchanged));
	memcpy(sentinel, &unchanged, sizeof(unchanged));
	assert(agent_evidence_seal(
		current_key, 1, challenge, 66, 77, credit_digest,
		&unchanged) < 0);
	assert(memcmp(sentinel, &unchanged, sizeof(unchanged)) == 0);
	current_retiring = 1;
	assert(agent_evidence_reclaim(current_key) == 0);
	assert(logical_pages == 0 && physical_pages == 0);
	assert(agent_evidence_retained_get(current_key, &retained) == 1);
	assert(retained.flags == AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE);
	assert(memcmp(retained.root, seal.root, sizeof(seal.root)) == 0);
}

static void
test_rollover_coverage(void)
{
	struct agent_audit_record audit;
	struct agent_evidence_retained_seal retained;
	struct agent_evidence_view view;
	struct proc process;
	uchar digest[AGENT_SHA256_DIGEST_SIZE];

	activate(&process, 3, 23, 23);
	for (uint64 sequence = 1; sequence <= 49; sequence++)
		append_ok(&process, sequence);
	assert(agent_evidence_view_open(current_key, &view) == 1);
	assert(view.total_records == 49 && view.visible_records == 1);
	assert(digest_present(view.sealed_root));
	assert(agent_evidence_view_record(&view, 0, &audit, 0) == 1);
	assert(audit.sequence == 1049);
	assert(agent_evidence_view_digest(&view, digest) == 0);
	assert(digest_present(digest));
	current_retiring = 1;
	assert(agent_evidence_reclaim(current_key) == 0);
	assert(logical_pages == 0 && physical_pages == 0);
	assert(agent_evidence_retained_get(current_key, &retained) == 1);
	assert(retained.flags == AGENT_EVIDENCE_RETAINED_F_INTERNAL_RETIRE);
	assert(retained.first_ticket == 1 && retained.last_ticket == 49);
	assert(retained.event_count == 49 && retained.gap_count == 0);
	assert(retained.segment_sequence == 2);
	assert(digest_present(retained.root));
}

static void
test_generation_reuse_reaps_pages(void)
{
	struct agent_evidence_retained_seal retained;
	struct proc process;
	uint reaps_before = resource_domain_reaps;
	uint iterations = WORKFLOW_LIFECYCLE_CAP + 3U;

	for (uint generation = 1; generation <= iterations; generation++) {
		uint id = (generation - 1U) % WORKFLOW_LIFECYCLE_CAP + 1U;

		activate(&process, id, 1000 + generation, 30 + id);
		append_ok(&process, 1);
		assert(logical_pages == WORKFLOW_EVIDENCE_PAGE_COUNT);
		assert(physical_pages == WORKFLOW_EVIDENCE_PAGE_COUNT);
		current_retiring = 1;
		assert(agent_evidence_reclaim(current_key) == 0);
		assert(logical_pages == 0 && physical_pages == 0 &&
		       resource_domains_live == 0);
		assert(agent_evidence_retained_get(current_key, &retained) == 1);
		assert(retained.key.generation == 1000 + generation);
	}
	assert(resource_domain_reaps - reaps_before == iterations);
}

static void
test_empty_fence_reclaims_without_pages(void)
{
	struct agent_evidence_seal_result seal;
	struct proc process;
	uchar challenge[AGENT_SHA256_DIGEST_SIZE];
	uchar credit_digest[AGENT_SHA256_DIGEST_SIZE];
	uint reaps_before = resource_domain_reaps;

	activate(&process, 1, 9001, 41);
	memset(challenge, 0x2a, sizeof(challenge));
	memset(credit_digest, 0x3b, sizeof(credit_digest));
	assert(agent_evidence_seal(
		current_key, 1, challenge, 7, 8, credit_digest, &seal) == 0);
	assert(seal.event_count == 0 && seal.gap_count == 0);
	assert(logical_pages == 0 && physical_pages == 0);
	current_retiring = 1;
	assert(agent_evidence_reclaim(current_key) == 0);
	assert(logical_pages == 0 && physical_pages == 0 &&
	       resource_domains_live == 0);
	assert(resource_domain_reaps == reaps_before);
}

int
main(void)
{
	agent_evidence_init();
	test_retirement_internal_seal();
	test_external_fence_retention();
	test_rollover_coverage();
	test_generation_reuse_reaps_pages();
	test_empty_fence_reclaims_without_pages();
	puts("agent_evidence_ring: retirement=1 external=1 rollover=1");
	return 0;
}
