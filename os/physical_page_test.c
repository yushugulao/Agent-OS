#include "physical_page_test.h"
#include "agent.h"
#include "agent_internal.h"
#include "defs.h"
#include "exec_policy.h"
#include "kalloc.h"
#include "proc.h"
#include "resource_controller.h"
#include "riscv.h"
#include "vfs_security.h"
#include "../physical_page_test_abi.h"
#include "../user/include/exec_policy_manifest.h"

#ifndef PHYSICAL_PAGE_TEST_INIT_NAME
#error "PHYSICAL_PAGE_TEST_HOOKS requires a sealed init image name"
#endif

enum physical_page_test_run_state {
	PHYSICAL_PAGE_TEST_RUN_IDLE = 0,
	PHYSICAL_PAGE_TEST_RUN_RUNNING,
};

static struct {
	struct proc *controller;
	struct resource_account_handle account;
	uint64 thread_generation;
	uint run_state;
	struct physical_page_lifecycle_report completed;
} physical_page_test_state;

static void physical_page_receipt(struct physical_page_lifecycle_report *report,
				  uint64 step, long long result, uint64 value0,
				  uint64 value1)
{
	struct physical_page_test_receipt *receipt;

	if (report->receipt_count >= PHYSICAL_PAGE_TEST_RECEIPT_CAP)
		panic("physical page receipt overflow");
	receipt = &report->receipts[report->receipt_count++];
	receipt->step = step;
	receipt->result = result;
	receipt->value0 = value0;
	receipt->value1 = value1;
}

void physical_page_test_bind_boot_init(struct proc *p, const char *name)
{
	uint expected_len = strlen(PHYSICAL_PAGE_TEST_INIT_NAME);
	int enabled;

	if (p == 0 || name == 0 || p->parent != 0 ||
	    strlen(name) != expected_len ||
	    strncmp(name, PHYSICAL_PAGE_TEST_INIT_NAME, expected_len) != 0)
		return;
	enabled = intr_save();
	if (physical_page_test_state.controller == 0) {
		physical_page_test_state.controller = p;
		physical_page_test_state.account = p->resource_account;
		physical_page_test_state.thread_generation =
			p->threads[0].identity_generation;
	}
	intr_restore(enabled);
}

static int physical_page_test_controller_authorized(const struct proc *p)
{
	int enabled = intr_save();
	int authorized = p != 0 &&
		p == physical_page_test_state.controller && p->parent == 0 &&
		proc_teardown_live(p) &&
		p->agent_control_state == AGENT_CONTROL_OPEN &&
		resource_account_handle_valid(p->resource_account) &&
		resource_account_handle_equal(
			p->resource_account, physical_page_test_state.account) &&
		p->threads[0].identity_generation != 0 &&
		p->threads[0].identity_generation ==
			physical_page_test_state.thread_generation;

	intr_restore(enabled);
	return authorized && exec_policy_process_bootstrap(p) &&
	       p->exec_flags == EXEC_FLAG_KNOWN &&
	       p->exec_generation == EXEC_MANIFEST_VERSION &&
	       p->exec_role_mask == EXEC_MANIFEST_ROLE_ALL &&
	       p->vfs_effective_caps == VFS_CAP_WORKFLOW &&
	       p->vfs_inheritable_caps == VFS_CAP_WORKFLOW &&
	       p->resource_slot_reserved &&
	       agent_authority_check((struct proc *)p,
				     AGENT_ROLE_ORCHESTRATOR) == AGENT_STATUS_OK;
}

static int physical_page_test_snapshot_authorized(struct proc *p)
{
	return physical_page_test_controller_authorized(p) ||
	       (p != 0 && proc_teardown_live(p) &&
		p->agent_control_state == AGENT_CONTROL_OPEN &&
		agent_identity_proc_scope(p) != VFS_SCOPE_NONE &&
		agent_authority_check(p, AGENT_ROLE_ORCHESTRATOR) ==
			AGENT_STATUS_OK);
}

static int physical_page_extra_create(
	const struct resource_account_limits *limits, uint64 external_id)
{
	struct resource_account_handle account = resource_account_none();
	int result = resource_account_create(
		RESOURCE_ACCOUNT_EXEC, external_id,
		RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED), limits,
		&account);

	if (result == 0 && resource_account_close(account) < 0)
		panic("physical page extra close");
	return result;
}

static void physical_page_release_account(
	struct resource_account_handle account)
{
	struct resource_request release[2];
	uint count = 0;

	for (uint kind = RESOURCE_PROCESS; kind <= RESOURCE_PHYSICAL_PAGE;
	     kind += RESOURCE_PHYSICAL_PAGE - RESOURCE_PROCESS) {
		uint64 amount = resource_account_class_usage(
			account, RESOURCE_CHARGE_ORDINARY, kind);

		if (amount != 0) {
			release[count].kind = kind;
			release[count++].amount = amount;
		}
	}
	if (count != 0 && resource_release_many(
			account, RESOURCE_CHARGE_ORDINARY, release, count) < 0)
		panic("physical page transfer cleanup");
}

static int physical_page_transfer_receipts(
	struct physical_page_lifecycle_report *report)
{
	struct resource_account_limits limits;
	struct resource_account_handle source = resource_account_none();
	struct resource_account_handle target = resource_account_none();
	struct resource_request bundle[] = {
		{ RESOURCE_PHYSICAL_PAGE, 1 },
		{ RESOURCE_PROCESS, 1 },
	};
	struct resource_reservation reservation;
	int result, complete = 0;

	memset(&limits, 0, sizeof(limits));
	memset(&reservation, 0, sizeof(reservation));
	limits.class_limit[RESOURCE_CHARGE_ORDINARY]
			  [RESOURCE_PHYSICAL_PAGE] = 1;
	limits.class_limit[RESOURCE_CHARGE_ORDINARY][RESOURCE_PROCESS] = 1;
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_KIND_ATTRIBUTES, 0,
		resource_kind_attributes(RESOURCE_PHYSICAL_PAGE), 0);
	result = resource_account_create(
		RESOURCE_ACCOUNT_EXEC, 0xfffffff000000101ULL,
		RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_ORDINARY), &limits,
		&source);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_SOURCE_CREATE,
		result, source.slot, source.generation);
	if (result < 0)
		goto out;
	result = resource_account_create(
		RESOURCE_ACCOUNT_EXEC, 0xfffffff000000102ULL,
		RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_ORDINARY), &limits,
		&target);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_TARGET_CREATE,
		result, target.slot, target.generation);
	if (result < 0)
		goto out;
	bundle[0].kind = RESOURCE_PROCESS;
	bundle[1].kind = RESOURCE_THREAD;
	if (resource_reserve_many(source, RESOURCE_CHARGE_ORDINARY,
		    bundle, 2, &reservation) == 0)
		goto out;
	bundle[0].kind = RESOURCE_PHYSICAL_PAGE;
	bundle[1].kind = RESOURCE_PROCESS;
	result = resource_reserve_many(source, RESOURCE_CHARGE_ORDINARY,
		bundle, 2, &reservation);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_TRANSFER_RESERVE,
		result, reservation.active, 0);
	if (result < 0 || reservation.kind_mask !=
			  ((1U << RESOURCE_PHYSICAL_PAGE) |
			   (1U << RESOURCE_PROCESS)))
		goto out;
	result = resource_reservation_commit(&reservation);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_TRANSFER_COMMIT,
		result, reservation.active, 0);
	if (result < 0)
		goto out;
	result = resource_transfer_usage(
		source, RESOURCE_CHARGE_ORDINARY, target,
		RESOURCE_CHARGE_ORDINARY, bundle, 2);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_TRANSFER, result, 0,
		0);
	result = resource_import_usage(target, RESOURCE_CHARGE_ORDINARY,
		bundle, 1);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_IMPORT, result, 0, 0);
	result = resource_reconcile_usage(source, RESOURCE_CHARGE_ORDINARY,
		bundle, 1);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_RECONCILE, result, 0,
		0);
	physical_page_receipt(
		report, PHYSICAL_PAGE_STEP_SOURCE_USAGE, 0,
		resource_account_class_usage(source, RESOURCE_CHARGE_ORDINARY,
			RESOURCE_PHYSICAL_PAGE),
		resource_account_class_usage(source, RESOURCE_CHARGE_ORDINARY,
			RESOURCE_PROCESS));
	physical_page_receipt(
		report, PHYSICAL_PAGE_STEP_TARGET_USAGE, 0,
		resource_account_class_usage(target, RESOURCE_CHARGE_ORDINARY,
			RESOURCE_PHYSICAL_PAGE),
		resource_account_class_usage(target, RESOURCE_CHARGE_ORDINARY,
			RESOURCE_PROCESS));
	complete = 1;
out:
	if (reservation.active)
		resource_reservation_cancel(&reservation);
	if (resource_account_handle_valid(source)) {
		physical_page_release_account(source);
		if (resource_account_close(source) < 0)
			panic("physical page source close");
	}
	if (resource_account_handle_valid(target)) {
		physical_page_release_account(target);
		if (resource_account_close(target) < 0)
			panic("physical page target close");
	}
	return complete;
}

static int physical_page_promise_receipts(
	struct physical_page_lifecycle_report *report)
{
	struct resource_account_limits fill_limits, extra_limits;
	struct resource_account_handle fill = resource_account_none();
	struct resource_account_handle replacement = resource_account_none();
	struct resource_request page = { RESOURCE_PHYSICAL_PAGE, 1 };
	struct resource_reservation pending;
	uint64 promised = 0, limit = 0, room;
	int result, member = 0, used = 0, complete = 0;

	memset(&fill_limits, 0, sizeof(fill_limits));
	memset(&extra_limits, 0, sizeof(extra_limits));
	memset(&pending, 0, sizeof(pending));
	result = resource_policy_reserved_snapshot(
		RESOURCE_PHYSICAL_PAGE, &promised, &limit);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PROMISE_INITIAL,
		result, promised, limit);
	if (result < 0 || promised >= limit || limit - promised < 2)
		goto out;
	room = limit - promised;
	fill_limits.class_limit[RESOURCE_CHARGE_RESERVED]
			       [RESOURCE_PHYSICAL_PAGE] = room;
	extra_limits.class_limit[RESOURCE_CHARGE_RESERVED]
				[RESOURCE_PHYSICAL_PAGE] = 1;
	result = resource_account_create(
		RESOURCE_ACCOUNT_EXEC, 0xfffffff000000001ULL,
		RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED), &fill_limits,
		&fill);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_FILL_CREATE, result,
		fill.slot, fill.generation);
	if (result < 0)
		goto out;
	result = resource_account_member_acquire(fill);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_MEMBER_ACQUIRE,
		result, resource_account_state_get(fill), 0);
	if (result < 0)
		goto out;
	member = 1;
	result = resource_reserve_many(fill, RESOURCE_CHARGE_RESERVED, &page, 1,
		&pending);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_FIRST_RESERVE, result,
		pending.active, 0);
	if (result < 0 || pending.kind_mask !=
			  (1U << RESOURCE_PHYSICAL_PAGE))
		goto out;
	result = resource_reservation_commit(&pending);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_FIRST_COMMIT, result,
		pending.active, 0);
	if (result < 0)
		goto out;
	used = 1;
	result = resource_reserve_many(fill, RESOURCE_CHARGE_RESERVED, &page, 1,
		&pending);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PENDING_RESERVE,
		result, pending.active, 0);
	if (result < 0)
		goto out;
	result = resource_policy_reserved_snapshot(
		RESOURCE_PHYSICAL_PAGE, &promised, &limit);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PROMISE_FULL, result,
		promised, limit);
	physical_page_receipt(
		report, PHYSICAL_PAGE_STEP_EXTRA_ACTIVE,
		physical_page_extra_create(&extra_limits, 0xfffffff000000002ULL),
		0, 0);
	result = resource_account_close(fill);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_CLOSE, result,
		resource_account_state_get(fill), 0);
	if (result < 0)
		goto out;
	physical_page_receipt(
		report, PHYSICAL_PAGE_STEP_EXTRA_CLOSING,
		physical_page_extra_create(&extra_limits, 0xfffffff000000003ULL),
		0, 0);
	result = resource_account_member_release(fill, 0);
	member = result < 0;
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_MEMBER_RELEASE,
		result, resource_account_state_get(fill), 0);
	if (result < 0)
		goto out;
	physical_page_receipt(
		report, PHYSICAL_PAGE_STEP_EXTRA_DRAINING,
		physical_page_extra_create(&extra_limits, 0xfffffff000000004ULL),
		0, 0);
	resource_reservation_cancel(&pending);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PENDING_CANCEL, 0,
		pending.active, resource_account_state_get(fill));
	physical_page_receipt(
		report, PHYSICAL_PAGE_STEP_EXTRA_AFTER_CANCEL,
		physical_page_extra_create(&extra_limits, 0xfffffff000000005ULL),
		0, 0);
	result = resource_release_many(
		fill, RESOURCE_CHARGE_RESERVED, &page, 1);
	used = result < 0;
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_USAGE_RELEASE, result,
		resource_account_handle_valid(fill), 0);
	if (result < 0)
		goto out;
	result = resource_policy_reserved_snapshot(
		RESOURCE_PHYSICAL_PAGE, &promised, &limit);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PROMISE_REFUNDED,
		result, promised, limit);
	result = resource_account_create(
		RESOURCE_ACCOUNT_EXEC, 0xfffffff000000006ULL,
		RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED), &fill_limits,
		&replacement);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_REPLACEMENT_CREATE,
		result, replacement.slot, replacement.generation);
	if (result < 0)
		goto out;
	result = resource_policy_reserved_snapshot(
		RESOURCE_PHYSICAL_PAGE, &promised, &limit);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PROMISE_REPLACEMENT,
		result, promised, limit);
	result = resource_account_close(replacement);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_REPLACEMENT_CLOSE,
		result, resource_account_handle_valid(replacement), 0);
	if (result < 0)
		goto out;
	result = resource_policy_reserved_snapshot(
		RESOURCE_PHYSICAL_PAGE, &promised, &limit);
	physical_page_receipt(report, PHYSICAL_PAGE_STEP_PROMISE_FINAL, result,
		promised, limit);
	complete = 1;
out:
	if (pending.active)
		resource_reservation_cancel(&pending);
	if (used)
		(void)resource_release_many(
			fill, RESOURCE_CHARGE_RESERVED, &page, 1);
	if (member)
		(void)resource_account_member_release(fill, 0);
	if (resource_account_handle_valid(fill))
		(void)resource_account_close(fill);
	if (resource_account_handle_valid(replacement))
		(void)resource_account_close(replacement);
	return complete;
}

static int physical_page_lifecycle_snapshot(
	struct physical_page_lifecycle_report *report)
{
	int enabled, complete;

	enabled = intr_save();
	if (physical_page_test_state.run_state ==
	    PHYSICAL_PAGE_TEST_RUN_COMPLETE) {
		memmove(report, &physical_page_test_state.completed,
			sizeof(*report));
		intr_restore(enabled);
		return 0;
	}
	if (physical_page_test_state.run_state !=
	    PHYSICAL_PAGE_TEST_RUN_IDLE) {
		intr_restore(enabled);
		return -1;
	}
	physical_page_test_state.run_state = PHYSICAL_PAGE_TEST_RUN_RUNNING;
	intr_restore(enabled);
	complete = physical_page_transfer_receipts(report) &&
		   physical_page_promise_receipts(report);
	report->run_state = complete ? PHYSICAL_PAGE_TEST_RUN_COMPLETE :
					PHYSICAL_PAGE_TEST_RUN_IDLE;
	enabled = intr_save();
	if (complete) {
		memmove(&physical_page_test_state.completed, report,
			sizeof(*report));
		physical_page_test_state.run_state =
			PHYSICAL_PAGE_TEST_RUN_COMPLETE;
	} else {
		physical_page_test_state.run_state = PHYSICAL_PAGE_TEST_RUN_IDLE;
	}
	intr_restore(enabled);
	return 0;
}

static int physical_page_test_copyout(struct proc *p, uint64 addr,
				      uint64 user_size, void *source,
				      uint64 source_size)
{
	uint64 copy_size;

	if (user_size < sizeof(struct physical_page_test_header))
		return -1;
	copy_size = user_size < source_size ? user_size : source_size;
	if (user_range_check(p->pagetable, addr, copy_size, PTE_W) < 0)
		return -1;
	return copyout(p->pagetable, addr, source, copy_size);
}

int sys_physical_page_test(uint command, uint64 addr, uint64 user_size)
{
	struct proc *p = curr_proc();

	if (command == PHYSICAL_PAGE_TEST_PROMISE_LIFECYCLE) {
		struct physical_page_lifecycle_report report;

		if (!physical_page_test_controller_authorized(p))
			return -1;
		memset(&report, 0, sizeof(report));
		report.header.version = PHYSICAL_PAGE_TEST_ABI_VERSION;
		report.header.size = sizeof(report);
		report.header.command = command;
		if (physical_page_lifecycle_snapshot(&report) < 0)
			return -1;
		return physical_page_test_copyout(
			p, addr, user_size, &report, sizeof(report));
	}
	if (command == PHYSICAL_PAGE_TEST_SNAPSHOT) {
		struct physical_page_account_snapshot snapshot;
		struct resource_account_kind_snapshot account;

		if (!physical_page_test_snapshot_authorized(p))
			return -1;
		if (resource_account_kind_snapshot(
			    p->resource_account, RESOURCE_PHYSICAL_PAGE,
			    &account) < 0)
			return -1;
		memset(&snapshot, 0, sizeof(snapshot));
		snapshot.header.version = PHYSICAL_PAGE_TEST_ABI_VERSION;
		snapshot.header.size = sizeof(snapshot);
		snapshot.header.command = command;
		snapshot.account_slot = account.handle.slot;
		snapshot.account_generation = account.handle.generation;
		snapshot.account_state = account.state;
		snapshot.charge_grants = account.charge_grants;
		snapshot.ordinary_usage =
			account.used[RESOURCE_CHARGE_ORDINARY];
		snapshot.ordinary_pending =
			account.pending[RESOURCE_CHARGE_ORDINARY];
		snapshot.ordinary_limit =
			account.limit[RESOURCE_CHARGE_ORDINARY];
		snapshot.reserved_usage =
			account.used[RESOURCE_CHARGE_RESERVED];
		snapshot.reserved_pending =
			account.pending[RESOURCE_CHARGE_RESERVED];
		snapshot.reserved_limit =
			account.limit[RESOURCE_CHARGE_RESERVED];
		snapshot.reserve_free = kalloc_physical_reserved_free_pages();
		snapshot.reserve_total = kalloc_physical_reserved_total_pages();
		snapshot.reserved = p->resource_slot_reserved != 0;
		return physical_page_test_copyout(
			p, addr, user_size, &snapshot, sizeof(snapshot));
	}
	return -1;
}
