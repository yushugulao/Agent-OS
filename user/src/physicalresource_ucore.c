#include <agent.h>
#include <physical_page_test_abi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

#define MAX_HOLDERS 8
#define MAX_WORKERS 15
#define MAX_PRESSURE_PAGES 256
#define MAX_PRESSURE_PIPES 5
#define PAGE_BYTES 4096
#define WORKFLOW_RETRY_LIMIT 256

static volatile int worker_stop;
static volatile int reserved_probe_ran;

struct reserve_report {
	int page_holders;
	int pipe_holders;
	uint scope_id;
	uint64 physical_usage;
	uint64 physical_limit;
};

struct legacy_mail_account_report {
	uint64 allocation_delta;
	uint64 before_fork;
	uint64 after_reap;
	int ok;
};

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("physicalresource_ucore: check failed: %s\n", message);
	exit(1);
}

static void worker(void *arg)
{
	int code = (int)(unsigned long)arg;

	while (!worker_stop)
		sched_yield();
	exit(code);
}

static void reserved_probe(void *arg)
{
	(void)arg;
	reserved_probe_ran = 1;
	exit(73);
}

static void write_full(int fd, const void *buffer, int length)
{
	const char *bytes = buffer;
	int written = 0;

	while (written < length) {
		int n = write(fd, bytes + written, length - written);

		check(n > 0, "write coordination payload");
		written += n;
	}
}

static void read_full(int fd, void *buffer, int length)
{
	char *bytes = buffer;
	int received = 0;

	while (received < length) {
		int n = read(fd, bytes + received, length - received);

		check(n > 0, "read coordination payload");
		received += n;
	}
}

static uint workflow_scope(void)
{
	struct agent_info info;

	check(agent_info(&info) == AGENT_STATUS_OK && info.is_agent,
	      "read workflow identity");
	return info.filesystem_domain;
}

static void physical_page_snapshot(
	struct physical_page_account_snapshot *snapshot)
{
	memset(snapshot, 0, sizeof(*snapshot));
	check(syscall(SYS_physical_page_test, PHYSICAL_PAGE_TEST_SNAPSHOT, snapshot,
		      sizeof(*snapshot)) == 0 &&
		      snapshot->header.version == PHYSICAL_PAGE_TEST_ABI_VERSION &&
		      snapshot->header.size == sizeof(*snapshot) &&
		      snapshot->header.command == PHYSICAL_PAGE_TEST_SNAPSHOT,
	      "read physical page test snapshot");
}

static void physical_page_lifecycle(
	struct physical_page_lifecycle_report *report)
{
	memset(report, 0, sizeof(*report));
	check(syscall(SYS_physical_page_test,
		      PHYSICAL_PAGE_TEST_PROMISE_LIFECYCLE, report,
		      sizeof(*report)) == 0 &&
		      report->header.version == PHYSICAL_PAGE_TEST_ABI_VERSION &&
		      report->header.size == sizeof(*report) &&
		      report->header.command ==
			      PHYSICAL_PAGE_TEST_PROMISE_LIFECYCLE,
	      "read physical page lifecycle report");
	for (uint i = 0; i < report->receipt_count; i++) {
		const struct physical_page_test_receipt *receipt =
			&report->receipts[i];

		printf("physicalresource_ucore: raw step=%llu result=%lld value0=%llu value1=%llu\n",
		       receipt->step, receipt->result, receipt->value0,
		       receipt->value1);
	}
}

static uint64 physical_class_usage(
	const struct physical_page_account_snapshot *snapshot)
{
	return snapshot->reserved ? snapshot->reserved_usage :
				    snapshot->ordinary_usage;
}

static uint64 physical_class_limit(
	const struct physical_page_account_snapshot *snapshot)
{
	return snapshot->reserved ? snapshot->reserved_limit :
				    snapshot->ordinary_limit;
}

static uint64 physical_account_usage(
	const struct physical_page_account_snapshot *snapshot)
{
	return snapshot->ordinary_usage + snapshot->reserved_usage;
}

static const struct physical_page_test_receipt *
physical_receipt(const struct physical_page_lifecycle_report *snapshot,
		 uint index, uint step)
{
	check(index < snapshot->receipt_count &&
		      snapshot->receipts[index].step == step,
	      "physical page receipt order");
	return &snapshot->receipts[index];
}

static void check_promise_receipts(
	const struct physical_page_lifecycle_report *snapshot)
{
	const struct physical_page_test_receipt *initial;
	const struct physical_page_test_receipt *receipt;
	uint i = 0;

	check(snapshot->run_state == PHYSICAL_PAGE_TEST_RUN_COMPLETE &&
		      snapshot->receipt_count == 30,
	      "complete physical page receipt stream");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_KIND_ATTRIBUTES);
	check(receipt->result == 0 &&
		      receipt->value0 == PHYSICAL_PAGE_TEST_POOL_AFFINE,
	      "physical pages retain allocator provenance");
	for (uint step = PHYSICAL_PAGE_STEP_SOURCE_CREATE;
	     step <= PHYSICAL_PAGE_STEP_TRANSFER_COMMIT; step++)
		check(physical_receipt(snapshot, i++, step)->result == 0,
		      "prepare physical transfer receipt");
	for (uint step = PHYSICAL_PAGE_STEP_TRANSFER;
	     step <= PHYSICAL_PAGE_STEP_RECONCILE; step++)
		check(physical_receipt(snapshot, i++, step)->result == -1,
		      "reject count-only physical ownership mutation");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_SOURCE_USAGE);
	check(receipt->result == 0 && receipt->value0 == 1 &&
		      receipt->value1 == 1,
	      "rejected transfer preserves source usage");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_TARGET_USAGE);
	check(receipt->result == 0 && receipt->value0 == 0 &&
		      receipt->value1 == 0,
	      "rejected transfer preserves target usage");
	initial = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_PROMISE_INITIAL);
	check(initial->result == 0 && initial->value0 < initial->value1,
	      "read initial reserved promise");
	for (uint step = PHYSICAL_PAGE_STEP_FILL_CREATE;
	     step <= PHYSICAL_PAGE_STEP_PENDING_RESERVE; step++)
		check(physical_receipt(snapshot, i++, step)->result == 0,
		      "fill reserved promise");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_PROMISE_FULL);
	check(receipt->result == 0 && receipt->value0 == receipt->value1 &&
		      receipt->value1 == initial->value1,
	      "pending reservation fills global promise");
	check(physical_receipt(snapshot, i++, PHYSICAL_PAGE_STEP_EXTRA_ACTIVE)
		      ->result == -1,
	      "full promise rejects an extra account");
	receipt = physical_receipt(snapshot, i++, PHYSICAL_PAGE_STEP_CLOSE);
	check(receipt->result == 0 &&
		      receipt->value0 == PHYSICAL_PAGE_TEST_ACCOUNT_CLOSING,
	      "close publishes closing state");
	check(physical_receipt(snapshot, i++, PHYSICAL_PAGE_STEP_EXTRA_CLOSING)
		      ->result == -1,
	      "closing account retains its promise");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_MEMBER_RELEASE);
	check(receipt->result == 0 &&
		      receipt->value0 == PHYSICAL_PAGE_TEST_ACCOUNT_DRAINING,
	      "member release publishes draining state");
	check(physical_receipt(snapshot, i++, PHYSICAL_PAGE_STEP_EXTRA_DRAINING)
		      ->result == -1,
	      "draining account retains pending promise");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_PENDING_CANCEL);
	check(receipt->result == 0 && receipt->value0 == 0 &&
		      receipt->value1 == PHYSICAL_PAGE_TEST_ACCOUNT_DRAINING,
	      "pending cancellation preserves draining usage");
	check(physical_receipt(snapshot, i++,
			       PHYSICAL_PAGE_STEP_EXTRA_AFTER_CANCEL)
		      ->result == -1,
	      "committed usage retains draining promise");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_USAGE_RELEASE);
	check(receipt->result == 0 && receipt->value0 == 0,
	      "last usage release retires account handle");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_PROMISE_REFUNDED);
	check(receipt->result == 0 && receipt->value0 == initial->value0 &&
		      receipt->value1 == initial->value1,
	      "draining account refunds its promise");
	check(physical_receipt(snapshot, i++,
			       PHYSICAL_PAGE_STEP_REPLACEMENT_CREATE)
		      ->result == 0,
	      "refunded promise admits replacement");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_PROMISE_REPLACEMENT);
	check(receipt->result == 0 && receipt->value0 == receipt->value1 &&
		      receipt->value1 == initial->value1,
	      "replacement owns the full available promise");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_REPLACEMENT_CLOSE);
	check(receipt->result == 0 && receipt->value0 == 0,
	      "replacement closes without residue");
	receipt = physical_receipt(
		snapshot, i++, PHYSICAL_PAGE_STEP_PROMISE_FINAL);
	check(receipt->result == 0 && receipt->value0 == initial->value0 &&
		      receipt->value1 == initial->value1 &&
		      i == snapshot->receipt_count,
	      "promise lifecycle returns to its exact baseline");
}

static __attribute__((noinline)) void check_physical_promise_lifecycle(void)
{
	struct physical_page_lifecycle_report report;

	physical_page_lifecycle(&report);
	check_promise_receipts(&report);
	printf("physicalresource_ucore: physical_transfer_rejected=1 mixed_atomic=1\n");
	printf("physicalresource_ucore: reserved_promise_lifecycle=1 promised=%llu limit=%llu\n",
	       report.receipts[16].value0, report.receipts[16].value1);
}

static void check_brk_contract(void)
{
	struct physical_page_account_snapshot before;
	struct physical_page_account_snapshot peak;
	struct physical_page_account_snapshot after;
	long origin = sbrk(0);
	long current;
	int status = -1;
	int child;

	check(origin > 0 && (origin % PAGE_BYTES) == 0,
	      "brk starts at its guarded page boundary");
	physical_page_snapshot(&before);
	check(sbrk(1) == origin, "byte growth returns old break");
	*(volatile char *)origin = 0x31;
	check(sbrk(2 * PAGE_BYTES - 1) == origin + 1,
	      "second growth keeps byte-granular break");
	current = sbrk(0);
	check(current == origin + 2 * PAGE_BYTES,
	      "brk query sees committed growth");
	*(volatile char *)(current - 1) = 0x62;
	physical_page_snapshot(&peak);
	check(physical_class_usage(&peak) >= physical_class_usage(&before) + 2,
	      "heap leaves are charged to the execution account");

	child = fork();
	check(child >= 0, "fork heap snapshot");
	if (child == 0) {
		if (sbrk(0) != current || *(volatile char *)origin != 0x31 ||
		    *(volatile char *)(current - 1) != 0x62)
			exit(81);
		exit(0);
	}
	check(waitpid(child, &status) == child && status == 0,
	      "fork inherits exact program break and heap bytes");
	check(sbrk(-2 * PAGE_BYTES) == current,
	      "shrink returns old break");
	check(sbrk(0) == origin, "shrink publishes original break");
	physical_page_snapshot(&after);
	check(physical_class_usage(&after) == physical_class_usage(&before),
	      "shrink refunds heap and page-table charges");
	check(sbrk(-1) == -1 && sbrk(0) == origin,
	      "heap underflow is failure atomic");
	check(sbrk((long)0x7fffffffffffffffULL) == -1 &&
		      sbrk(0) == origin,
	      "heap overflow is failure atomic");
	printf("physicalresource_ucore: brk_atomic=1 fork_inherit=1 shrink_refund=1 guard=1\n");
}

static void legacy_mail_account_root(int report_fd)
{
	struct legacy_mail_account_report report;
	struct physical_page_account_snapshot before;
	struct physical_page_account_snapshot baseline;
	struct physical_page_account_snapshot peak;
	struct physical_page_account_snapshot after;
	char token = 0;
	int child_ready[2];
	int child_control[2];
	int child;
	int status = -1;

	check(pipe(child_ready) == 0 && pipe(child_control) == 0,
	      "create legacy mail accounting pipes");
	check(agent_scope_delegate_fd(child_ready[1]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(child_control[0]) ==
			      AGENT_STATUS_OK,
	      "delegate legacy mail accounting pipes");
	physical_page_snapshot(&before);
	child = fork();
	check(child >= 0, "create legacy mail accounting endpoint");
	if (child == 0) {
		token = 'R';
		check(write(child_ready[1], &token, 1) == 1,
		      "report legacy mail endpoint ready");
		check(read(child_control[0], &token, 1) == 1 && token == 'A',
		      "start legacy mail allocation");
		check(mailwrite(getpid(), "accounted", 10) == 10,
		      "allocate legacy mail sidecar");
		token = 'M';
		check(write(child_ready[1], &token, 1) == 1,
		      "report legacy mail sidecar allocated");
		check(read(child_control[0], &token, 1) == 1 && token == 'X',
		      "release legacy mail accounting endpoint");
		exit(0);
	}
	check(read(child_ready[0], &token, 1) == 1 && token == 'R',
	      "wait legacy mail endpoint ready");
	physical_page_snapshot(&baseline);
	token = 'A';
	check(write(child_control[1], &token, 1) == 1,
	      "request legacy mail sidecar allocation");
	check(read(child_ready[0], &token, 1) == 1 && token == 'M',
	      "wait legacy mail sidecar allocation");
	physical_page_snapshot(&peak);
	check(physical_account_usage(&peak) ==
		      physical_account_usage(&baseline) + 2,
	      "legacy mail sidecar charges exactly two target-account pages");
	token = 'X';
	check(write(child_control[1], &token, 1) == 1,
	      "release legacy mail endpoint");
	check(waitpid(child, &status) == child && status == 0,
	      "wait legacy mail accounting endpoint");
	physical_page_snapshot(&after);
	check(physical_account_usage(&after) ==
		      physical_account_usage(&before),
	      "legacy mail teardown refunds the target account");
	memset(&report, 0, sizeof(report));
	report.allocation_delta = physical_account_usage(&peak) -
				  physical_account_usage(&baseline);
	report.before_fork = physical_account_usage(&before);
	report.after_reap = physical_account_usage(&after);
	report.ok = 1;
	write_full(report_fd, &report, sizeof(report));
	exit(0);
}

static void check_legacy_mail_accounting(void)
{
	struct legacy_mail_account_report report;
	int report_pipe[2];
	int workflow;
	int status = -1;

	check(pipe(report_pipe) == 0,
	      "create legacy mail accounting report pipe");
	check(agent_scope_delegate_fd(report_pipe[1]) == AGENT_STATUS_OK,
	      "delegate legacy mail accounting report");
	workflow = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(workflow >= 0, "create legacy mail accounting workflow");
	if (workflow == 0)
		legacy_mail_account_root(report_pipe[1]);
	check(close(report_pipe[1]) == 0,
	      "close legacy mail accounting child report endpoint");
	read_full(report_pipe[0], &report, sizeof(report));
	check(report.ok && report.allocation_delta == 2 &&
		      report.after_reap == report.before_fork,
	      "validate legacy mail account charge and refund");
	check(waitpid(workflow, &status) == workflow && status == 0,
	      "wait legacy mail accounting workflow");
	check(close(report_pipe[0]) == 0,
	      "close legacy mail accounting report pipe");
	printf("physicalresource_ucore: legacy_mail_accounting=1 alloc_delta=2 exit_delta=0\n");
}

/* Fill one workflow's execution account without consuming another's share. */
static void reserved_domain_pressure(int report_fd, int release_fd)
{
	struct reserve_report report;
	struct physical_page_account_snapshot snapshot;
	int pipes[MAX_PRESSURE_PIPES][2];
	int page_count = 0;
	int pipe_count = 0;
	char token;
	long heap_origin = sbrk(0);
	long full_break;

	check(heap_origin > 0, "read pressure workflow break");
	while (page_count < MAX_PRESSURE_PAGES) {
		long page = sbrk(PAGE_BYTES);

		if (page == -1)
			break;
		*(volatile char *)page = (char)page_count;
		page_count++;
	}
	while (pipe_count < MAX_PRESSURE_PIPES) {
		if (pipe(pipes[pipe_count]) < 0)
			break;
		pipe_count++;
	}
	physical_page_snapshot(&snapshot);
	check(snapshot.reserved && physical_class_limit(&snapshot) > 0 &&
		      physical_class_usage(&snapshot) ==
			      physical_class_limit(&snapshot),
	      "pressure account reaches its exact reserved page promise");
	full_break = sbrk(0);
	for (int i = 0; i < 3; i++)
		check(sbrk(PAGE_BYTES) == -1 && sbrk(0) == full_break,
		      "full physical account rejects additional pages");
	report.page_holders = page_count;
	report.pipe_holders = pipe_count;
	report.scope_id = workflow_scope();
	report.physical_usage = physical_class_usage(&snapshot);
	report.physical_limit = physical_class_limit(&snapshot);
	write_full(report_fd, &report, sizeof(report));
	check(read(release_fd, &token, 1) == 1 && token == 'X',
	      "release pressure workflow");
	for (int i = 0; i < pipe_count; i++)
		check(close(pipes[i][0]) == 0 && close(pipes[i][1]) == 0,
		      "release pressure pipe page");
	if (page_count != 0)
		check(sbrk(-(long)page_count * PAGE_BYTES) != -1,
		      "release pressure heap pages");
	check(sbrk(0) == heap_origin,
	      "pressure heap refund restores exact break");
	exit(0);
}

static void reserved_domain_probe(int report_fd, int release_fd)
{
	struct reserve_report report;
	char token;
	int tid;

	reserved_probe_ran = 0;
	tid = thread_create(reserved_probe, 0);
	check(tid >= 0 && waittid(tid) == 73 && reserved_probe_ran,
	      "independent workflow obtains trapframe and user stack pages");
	report.page_holders = 0;
	report.pipe_holders = 0;
	report.scope_id = workflow_scope();
	report.physical_usage = 0;
	report.physical_limit = 0;
	write_full(report_fd, &report, sizeof(report));
	check(read(release_fd, &token, 1) == 1 && token == 'X',
	      "release reserve probe workflow");
	exit(0);
}

static int spawn_reserved_probe(int report_fd, int release_fd)
{
	for (int attempt = 0; attempt < WORKFLOW_RETRY_LIMIT; attempt++) {
		int pid;

		check(agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK &&
			      agent_scope_delegate_fd(release_fd) ==
				      AGENT_STATUS_OK,
		      "delegate reserve probe endpoints");
		pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
		if (pid >= 0) {
			if (pid == 0)
				reserved_domain_probe(report_fd, release_fd);
			return pid;
		}
		sched_yield();
	}
	return -1;
}

static void check_reserved_domain_fairness(void)
{
	struct reserve_report pressure;
	struct reserve_report probes[3];
	int pressure_report[2];
	int pressure_release[2];
	int probe_report[3][2];
	int probe_release[3][2];
	int pressure_pid;
	int probe_pid[3];
	int status = -1;
	char token = 'X';

	check(pipe(pressure_report) == 0 && pipe(pressure_release) == 0,
	      "create pressure workflow pipes");
	check(agent_scope_delegate_fd(pressure_report[1]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(pressure_release[0]) ==
			      AGENT_STATUS_OK,
	      "delegate pressure workflow pipes");
	pressure_pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pressure_pid >= 0, "create pressure workflow");
	if (pressure_pid == 0)
		reserved_domain_pressure(pressure_report[1],
					 pressure_release[0]);
	check(close(pressure_report[1]) == 0 &&
		      close(pressure_release[0]) == 0,
	      "close pressure child endpoints");
	read_full(pressure_report[0], &pressure, sizeof(pressure));
	check(pressure.scope_id != 0,
	      "pressure workflow reports stable scope");

	/* Root + pressure + two probes occupy all four reserved domains. */
	for (int i = 0; i < 2; i++) {
		check(pipe(probe_report[i]) == 0 && pipe(probe_release[i]) == 0,
		      "create reserve probe pipes");
		probe_pid[i] = spawn_reserved_probe(probe_report[i][1],
						  probe_release[i][0]);
		check(probe_pid[i] >= 0, "create independent reserve probe");
		check(close(probe_report[i][1]) == 0 &&
			      close(probe_release[i][0]) == 0,
		      "close reserve probe child endpoints");
		read_full(probe_report[i][0], &probes[i], sizeof(probes[i]));
		check(probes[i].scope_id != pressure.scope_id &&
			      (i == 0 ||
			       probes[i].scope_id != probes[i - 1].scope_id),
		      "reserved workflows have distinct resource domains");
	}
	printf("physicalresource_ucore: reserved_domain_fairness=1 pressure_pages=%d pressure_pipes=%d physical_usage=%llu physical_limit=%llu\n",
	       pressure.page_holders, pressure.pipe_holders,
	       pressure.physical_usage, pressure.physical_limit);

	/* Drain only the pressured domain; keep both other probes alive. */
	check(write(pressure_release[1], &token, 1) == 1,
	      "release pressure domain holder");
	check(waitpid(pressure_pid, &status) == pressure_pid && status == 0,
	      "pressure workflow teardown refunds its pages");
	check(close(pressure_report[0]) == 0 &&
		      close(pressure_release[1]) == 0,
	      "close pressure parent endpoints");

	check(pipe(probe_report[2]) == 0 && pipe(probe_release[2]) == 0,
	      "create replacement reserve probe pipes");
	probe_pid[2] = spawn_reserved_probe(probe_report[2][1],
					probe_release[2][0]);
	check(probe_pid[2] >= 0,
	      "released reserved promise admits replacement workflow");
	check(close(probe_report[2][1]) == 0 &&
		      close(probe_release[2][0]) == 0,
	      "close replacement child endpoints");
	read_full(probe_report[2][0], &probes[2], sizeof(probes[2]));
	check(probes[2].scope_id != pressure.scope_id &&
		      probes[2].scope_id != probes[0].scope_id &&
		      probes[2].scope_id != probes[1].scope_id,
	      "replacement receives a fresh workflow identity");
	printf("physicalresource_ucore: reserved_domain_refund=1\n");

	for (int i = 0; i < 3; i++) {
		status = -1;
		check(write(probe_release[i][1], &token, 1) == 1,
		      "release reserve probe");
		check(waitpid(probe_pid[i], &status) == probe_pid[i] &&
			      status == 0,
		      "wait reserve probe");
		check(close(probe_report[i][0]) == 0 &&
			      close(probe_release[i][1]) == 0,
		      "close reserve probe parent endpoints");
	}
}

static void holder_main(int ready_fd, int release_fd)
{
	int tids[MAX_WORKERS];
	int count = 0;
	char token = 'R';

	worker_stop = 0;
	while (count < MAX_WORKERS) {
		int tid = thread_create(worker,
					(void *)(unsigned long)(20 + count));

		if (tid < 0)
			break;
		tids[count++] = tid;
	}
	if (count == 0 || count == MAX_WORKERS)
		exit(10);
	for (int i = 0; i < 3; i++)
		if (thread_create(worker, (void *)99) != -1)
			exit(11);
	if (write(ready_fd, &token, 1) != 1 ||
	    read(release_fd, &token, 1) != 1 || token != 'X')
		exit(12);
	worker_stop = 1;
	for (int i = 0; i < count; i++)
		if (waittid(tids[i]) != 20 + i)
			exit(13);
	worker_stop = 0;
	tids[0] = thread_create(worker, (void *)61);
	if (tids[0] < 0)
		exit(14);
	worker_stop = 1;
	if (waittid(tids[0]) != 61)
		exit(15);
	exit(0);
}

int main(void)
{
	int ready[2];
	int release[2];
	int holders[MAX_HOLDERS];
	int holder_count = 0;
	char token;

	check_brk_contract();
	check_legacy_mail_accounting();
	check_physical_promise_lifecycle();
	check_reserved_domain_fairness();

	check(pipe(ready) == 0 && pipe(release) == 0,
	      "create coordination pipes");
	while (holder_count < MAX_HOLDERS) {
		check(agent_scope_delegate_fd(ready[1]) == AGENT_STATUS_OK &&
			      agent_scope_delegate_fd(release[0]) ==
				      AGENT_STATUS_OK,
		      "delegate holder coordination");
		int pid = fork();

		if (pid < 0)
			break;
		if (pid == 0)
			holder_main(ready[1], release[0]);
		holders[holder_count++] = pid;
		check(read(ready[0], &token, 1) == 1 && token == 'R',
		      "holder reached domain quota");
	}
	check(holder_count >= 2 && holder_count < MAX_HOLDERS,
	      "ordinary global waterline reached across domains");
	for (int i = 0; i < 3; i++)
		check(fork() == -1, "ordinary pressure rejection is stable");

	{
		int reserve_pipe[2];
		int tid;

		check(pipe(reserve_pipe) == 0,
		      "reserved control pipe survives ordinary OOM");
		reserved_probe_ran = 0;
		tid = thread_create(reserved_probe, 0);
		check(tid >= 0 && waittid(tid) == 73 && reserved_probe_ran,
		      "reserved control thread survives ordinary OOM");
		check(close(reserve_pipe[0]) == 0 && close(reserve_pipe[1]) == 0,
		      "close reserved control pipe");
	}
	printf("physicalresource_ucore: domain_isolation=1\n");
	printf("physicalresource_ucore: system_reserve=1\n");

	token = 'X';
	for (int i = 0; i < holder_count; i++)
		check(write(release[1], &token, 1) == 1,
		      "release holder");
	for (int i = 0; i < holder_count; i++) {
		int status = -1;

		check(waitpid(holders[i], &status) == holders[i] && status == 0,
		      "holder teardown refunds pages");
	}
	{
		int status = -1;
		int replacement = fork();

		check(replacement >= 0, "ordinary pages reusable after teardown");
		if (replacement == 0)
			exit(0);
		check(waitpid(replacement, &status) == replacement && status == 0,
		      "replacement exits cleanly");
	}
	printf("physicalresource_ucore: teardown_refund=1\n");
	printf("physicalresource_ucore: parent passed\n");
	return 0;
}
