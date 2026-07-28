#include <agent.h>
#include <fcntl.h>
#include <io_policy.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

#define IO_BLOCK_SIZE 1024
#define PRESSURE_BLOCKS 10
#define PRESSURE_BYTES (PRESSURE_BLOCKS * IO_BLOCK_SIZE)
#define COLD_CACHE_BLOCKS (IO_CACHE_WORKFLOW_CAP + 1)
#define COLD_RATE_BLOCKS \
	(IO_POLICY_WORKFLOW_NORMAL_BURST + IO_POLICY_SHARED_BURST + 1)
#define COLD_PRESSURE_BLOCKS \
	(COLD_CACHE_BLOCKS > COLD_RATE_BLOCKS ? \
	 COLD_CACHE_BLOCKS : COLD_RATE_BLOCKS)
#define COLD_PRESSURE_BYTES (COLD_PRESSURE_BLOCKS * IO_BLOCK_SIZE)
#define ATTACKER_MAX_MS 30000
#define EXIT_LEASE_ROUNDS 72

static char block_data[IO_BLOCK_SIZE];
static char pressure_data[PRESSURE_BYTES];
static char pressure_readback[PRESSURE_BYTES];
static char cold_pressure_data[COLD_PRESSURE_BYTES];
static volatile int attacker_stop;

struct workflow_report {
	int ready;
	int cache_isolated;
	int bounded_progress;
	int control_class;
	uint owner;
	uint resident;
};

struct io_policy_prefix_probe {
	unsigned int version;
	unsigned int struct_size;
	unsigned int guard;
};

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("iobudget_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void check_sized_abi(void)
{
	struct io_policy_prefix_probe prefix = {
		.version = 0,
		.struct_size = 0,
		.guard = 0x5a5aa5a5U,
	};
	unsigned char short_buffer[8];

	memset(short_buffer, 0x6d, sizeof(short_buffer));
	check(syscall(SYS_io_policy_info, &prefix,
		      2 * sizeof(unsigned int)) == 0,
	      "accept sized I/O policy prefix");
	check(prefix.version == IO_POLICY_VERSION &&
	      prefix.struct_size == sizeof(struct io_policy_info) &&
	      prefix.guard == 0x5a5aa5a5U,
	      "bounded I/O policy prefix copy");
	check(syscall(SYS_io_policy_info, short_buffer,
		      2 * sizeof(unsigned int) - 1) == -1,
	      "reject undersized I/O policy prefix");
	for (unsigned int i = 0; i < sizeof(short_buffer); i++)
		check(short_buffer[i] == 0x6d,
		      "undersized I/O policy leaves user memory unchanged");
}

static void exit_lease_worker(void *arg)
{
	(void)arg;
	exit(0);
}

static void check_thread_exit_lease_cleanup(void)
{
	struct io_policy_info info;

	for (int i = 0; i < EXIT_LEASE_ROUNDS; i++) {
		int tid = thread_create(exit_lease_worker, 0);

		check(tid > 0, "create I/O lease cleanup thread");
		check(waittid(tid) == 0, "join I/O lease cleanup thread");
	}
	check(io_policy_info(&info) == 0, "read post-exit I/O state");
	check(info.leased == 0,
	      "thread teardown releases syscall I/O admission lease");
}

static void write_exact(int fd, const void *buffer, size_t size,
			const char *message);
static void read_exact(int fd, void *buffer, size_t size,
		       const char *message);
static void create_file(const char *name, const void *data, size_t size);

static void check_scheduler_interrupt_progress(void)
{
	int gate[2];
	int ready_pipe[2];
	int waiter;
	int status = -1;
	char signal = 1;

	check(pipe(gate) == 0 && pipe(ready_pipe) == 0,
	      "create scheduler progress pipes");
	check(agent_scope_delegate_fd(gate[0]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate scheduler progress endpoints");
	waiter = fork();
	check(waiter >= 0, "create kernel pipe waiter");
	if (waiter == 0) {
		write_exact(ready_pipe[1], &signal, sizeof(signal),
			    "publish kernel pipe waiter");
		read_exact(gate[0], &signal, sizeof(signal),
			   "release kernel pipe waiter");
		check(close(gate[0]) == 0 && close(ready_pipe[1]) == 0,
		      "close kernel pipe waiter endpoints");
		exit(0);
	}
	check(close(gate[0]) == 0 && close(ready_pipe[1]) == 0,
	      "close scheduler progress child endpoints");
	read_exact(ready_pipe[0], &signal, sizeof(signal),
		   "wait for kernel pipe waiter");
	memset(cold_pressure_data, 'T', sizeof(cold_pressure_data));
	create_file("iotick", cold_pressure_data,
		    (IO_POLICY_WORKFLOW_NORMAL_BURST + 8) * IO_BLOCK_SIZE);
	write_exact(gate[1], &signal, sizeof(signal),
		    "stop kernel pipe waiter");
	check(waitpid(waiter, &status) == waiter && status == 0,
	      "reap kernel pipe waiter");
	check(close(gate[1]) == 0 && close(ready_pipe[0]) == 0,
	      "close scheduler progress endpoints");
	check(unlink("iotick") == 0, "remove scheduler progress file");
}

static void run_fault_exit_child(int report_fd)
{
	struct io_policy_info before;
	volatile unsigned char *invalid = (volatile unsigned char *)0;
	int fd;

	memset(pressure_data, 'F', sizeof(pressure_data));
	fd = open("iofault", O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create fault-exit file");
	write_exact(fd, pressure_data, sizeof(pressure_data),
		    "populate fault-exit file");
	check(unlink("iofault") == 0, "unlink open fault-exit file");
	check(io_policy_info(&before) == 0,
	      "snapshot lineage state before fault exit");
	write_exact(report_fd, &before, sizeof(before),
		    "report pre-fault lineage state");
	check(close(report_fd) == 0, "close pre-fault report");
	printf("iobudget_ucore: fault_exit_armed=1\n");
	*invalid = 1;
	exit(99);
}

static void run_lineage_snapshot_child(int report_fd)
{
	struct io_policy_info after;

	check(io_policy_info(&after) == 0,
	      "snapshot lineage state after fault exit");
	write_exact(report_fd, &after, sizeof(after),
		    "report post-fault lineage state");
	check(close(report_fd) == 0, "close post-fault report");
	exit(0);
}

static void check_fault_exit_cleanup(uint expected_owner)
{
	struct io_policy_info before;
	struct io_policy_info after;
	int before_pipe[2];
	int after_pipe[2];
	int child;
	int observer;
	int status = -1;

	check(pipe(before_pipe) == 0, "create pre-fault report pipe");
	check(agent_scope_delegate_fd(before_pipe[1]) == AGENT_STATUS_OK,
	      "delegate pre-fault report pipe");
	child = fork();
	check(child >= 0, "create fault-exit child");
	if (child == 0)
		run_fault_exit_child(before_pipe[1]);
	check(close(before_pipe[1]) == 0, "close pre-fault child endpoint");
	read_exact(before_pipe[0], &before, sizeof(before),
		   "receive pre-fault lineage state");
	check(close(before_pipe[0]) == 0, "close pre-fault parent endpoint");
	check(waitpid(child, &status) == child && status == -2,
	      "fault child exits through user trap");

	check(pipe(after_pipe) == 0, "create post-fault report pipe");
	check(agent_scope_delegate_fd(after_pipe[1]) == AGENT_STATUS_OK,
	      "delegate post-fault report pipe");
	observer = fork();
	check(observer >= 0, "create post-fault lineage observer");
	if (observer == 0)
		run_lineage_snapshot_child(after_pipe[1]);
	check(close(after_pipe[1]) == 0, "close post-fault child endpoint");
	read_exact(after_pipe[0], &after, sizeof(after),
		   "receive post-fault lineage state");
	check(close(after_pipe[0]) == 0, "close post-fault parent endpoint");
	status = -1;
	check(waitpid(observer, &status) == observer && status == 0,
	      "reap post-fault lineage observer");
	check(before.owner == expected_owner &&
	      after.owner == before.owner &&
	      before.io_class == IO_POLICY_CLASS_NORMAL &&
	      after.io_class == IO_POLICY_CLASS_NORMAL,
	      "fault cleanup retains immutable lineage owner");
	check(after.physical_writes > before.physical_writes,
	      "fault cleanup performs attributed block I/O");
	check(after.unreserved_transfers == before.unreserved_transfers,
	      "fault cleanup cannot bypass I/O attribution");
	check(after.leased == 0 && after.debt == 0 &&
	      after.device_debt == 0,
	      "fault cleanup settles owner and device debt");
}

static void write_exact(int fd, const void *buffer, size_t size,
			const char *message)
{
	const char *cursor = buffer;

	while (size != 0) {
		ssize_t written = write(fd, cursor, size);

		check(written > 0, message);
		cursor += written;
		size -= written;
	}
}

static void read_exact(int fd, void *buffer, size_t size,
		       const char *message)
{
	char *cursor = buffer;

	while (size != 0) {
		ssize_t received = read(fd, cursor, size);

		check(received > 0, message);
		cursor += received;
		size -= received;
	}
}

static void create_file(const char *name, const void *data, size_t size)
{
	int fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "create pressure file");
	write_exact(fd, data, size, "populate pressure file");
	check(close(fd) == 0, "close pressure file");
}

static void setup_lineage_pressure(void)
{
	memset(pressure_data, 'P', sizeof(pressure_data));
	create_file("iopress", pressure_data, sizeof(pressure_data));
}

static void attacker_stop_listener(void *arg)
{
	char signal;
	int fd = (int)(long)arg;

	read_exact(fd, &signal, sizeof(signal), "read attacker stop");
	__sync_synchronize();
	attacker_stop = 1;
	exit(0);
}

static void run_lineage_attacker(int ready_fd, int stop_fd,
				 uint expected_owner)
{
	struct io_policy_info before;
	struct io_policy_info after;
	char byte = 'a';
	char ready = 1;
	int ready_sent = 0;
	int stop_tid;
	int rounds = 0;
	int64 deadline;
	unsigned long long read_transfers;
	unsigned long long write_transfers;
	unsigned long long flush_transfers;
	unsigned long long transfers;
	unsigned long long reserved_decisions;
	unsigned long long shared_decisions;
	unsigned long long throttle_decisions;
	unsigned long long budget_decisions;

	setup_lineage_pressure();
	check(io_policy_info(&before) == 0, "read initial lineage I/O state");
	check(before.version == IO_POLICY_VERSION,
	      "lineage process observes current I/O policy ABI");
	check(before.struct_size == sizeof(before),
	      "lineage process observes sized I/O policy ABI");
	check(before.owner == expected_owner,
	      "attacker retains immutable workflow owner");
	check(before.io_class == IO_POLICY_CLASS_NORMAL,
	      "lineage attacker uses normal I/O class");
	check(before.class_burst == IO_POLICY_WORKFLOW_NORMAL_BURST &&
	      before.class_refill == IO_POLICY_WORKFLOW_NORMAL_REFILL,
	      "lineage attacker uses the workflow normal profile");
	memset(cold_pressure_data, 'C', sizeof(cold_pressure_data));
	create_file("iocold", cold_pressure_data, sizeof(cold_pressure_data));
	check(io_policy_info(&after) == 0, "read cold-pressure I/O state");
	check(after.owner == expected_owner &&
	      after.io_class == IO_POLICY_CLASS_NORMAL,
	      "cold pressure retains immutable lineage attribution");
	check(after.physical_reads >= before.physical_reads &&
	      after.physical_writes >= before.physical_writes &&
	      after.physical_flushes >= before.physical_flushes &&
	      after.reserved_grants >= before.reserved_grants &&
	      after.shared_grants >= before.shared_grants &&
	      after.throttles >= before.throttles,
	      "lineage I/O counters remain monotonic");
	read_transfers = after.physical_reads - before.physical_reads;
	write_transfers = after.physical_writes - before.physical_writes;
	flush_transfers = after.physical_flushes - before.physical_flushes;
	check(read_transfers <= ~0ULL - write_transfers,
	      "physical transfer delta fits the accounting type");
	transfers = read_transfers + write_transfers;
	check(transfers <= ~0ULL - flush_transfers,
	      "physical transfer total fits the accounting type");
	transfers += flush_transfers;
	reserved_decisions = after.reserved_grants - before.reserved_grants;
	shared_decisions = after.shared_grants - before.shared_grants;
	throttle_decisions = after.throttles - before.throttles;
	check(reserved_decisions <= ~0ULL - shared_decisions,
	      "rate decision delta fits the accounting type");
	budget_decisions = reserved_decisions + shared_decisions;
	check(budget_decisions <= ~0ULL - throttle_decisions,
	      "rate decision total fits the accounting type");
	budget_decisions += throttle_decisions;
	check(write_transfers > (unsigned long long)before.class_burst +
				 IO_POLICY_SHARED_BURST,
	      "cold pressure crosses the initial credit envelope");
	check(transfers <= budget_decisions,
	      "aggregate rate decisions cover physical transfers");
	check(after.refills > before.refills ||
	      after.throttles > before.throttles,
	      "cold pressure advances through refill or throttling");
	check(after.cache_evictions > before.cache_evictions,
	      "fresh working set reaches the lineage cache cap");
	check(after.leased <= after.class_burst &&
	      after.tokens <= after.class_burst - after.leased &&
	      after.shared_leased <= IO_POLICY_SHARED_BURST &&
	      after.shared_tokens <=
		      IO_POLICY_SHARED_BURST - after.shared_leased,
	      "outstanding leases remain inside configured bursts");
	check(after.device_burst == IO_POLICY_DEVICE_BURST &&
	      after.device_refill == IO_POLICY_DEVICE_REFILL &&
	      after.device_leased <= after.device_burst &&
	      after.device_tokens <= after.device_burst - after.device_leased,
	      "runtime device envelope remains bounded");
	check(after.leased == 0 && after.shared_leased == 0 &&
	      after.device_leased == 0 && after.debt == 0 &&
	      after.device_debt == 0 &&
	      after.completion_sequence > before.completion_sequence,
	      "cold pressure settles leases and records completion");
	write_exact(ready_fd, &ready, sizeof(ready),
		    "report attacker pressure");
	ready_sent = 1;
	attacker_stop = 0;
	stop_tid = thread_create(attacker_stop_listener, (void *)(long)stop_fd);
	check(stop_tid > 0, "create attacker stop listener");
	deadline = get_mtime() + ATTACKER_MAX_MS;
	while (!attacker_stop || rounds < 2) {
		int fd = open("iopress", O_RDONLY);

		check(fd >= 0, "open pressure file");
		read_exact(fd, pressure_readback, sizeof(pressure_readback),
			   "read pressure file");
		check(close(fd) == 0, "close pressure reader");
		byte = 'a' + rounds % 26;
		fd = open("iopress", O_WRONLY);

		check(fd >= 0, "open micro-write target");
		check(write(fd, &byte, 1) == 1, "perform micro write");
		check(close(fd) == 0, "close micro-write target");
		rounds++;
		check(io_policy_info(&after) == 0, "read lineage I/O state");
		check(get_mtime() < deadline || attacker_stop,
		      "attacker pressure has a bounded deadline");
		if ((rounds & 3) == 0)
			check(sched_yield() == 0, "yield pressure loop");
	}
	check(ready_sent, "observe both budget and cache pressure");
	check(after.cache_resident <= after.cache_cap,
	      "lineage cache occupancy stays capped");
	check(after.unreserved_transfers == before.unreserved_transfers,
	      "all lineage transfers retain syscall attribution");
	check(waittid(stop_tid) >= 0, "join attacker stop listener");
	check(close(ready_fd) == 0 && close(stop_fd) == 0,
	      "close attacker controls");
	check(unlink("iocold") == 0, "remove cold pressure file");
	check(unlink("iopress") == 0, "remove pressure file");
	exit(0);
}

static void run_workflow(int command_fd, int report_fd)
{
	struct io_policy_info initial;
	struct io_policy_info pressured;
	struct io_policy_info after_read;
	struct io_policy_info after_write;
	struct workflow_report report;
	uint protected_resident;
	char command;
	char value = 'W';
	int fd;

	printf("iobudget_ucore: workflow_enter=1\n");
	memset(block_data, 'W', sizeof(block_data));
	create_file("wfhot", block_data, sizeof(block_data));
	printf("iobudget_ucore: workflow_file=1\n");
	fd = open("wfhot", O_RDONLY);
	check(fd >= 0, "open workflow hot block");
	check(read(fd, &value, 1) == 1, "preheat workflow hot block");
	check(io_policy_info(&initial) == 0, "read workflow I/O state");
	check(initial.version == IO_POLICY_VERSION,
	      "workflow observes current I/O policy ABI");
	check(initial.struct_size == sizeof(initial),
	      "workflow observes sized I/O policy ABI");
	check(initial.cache_resident != 0 &&
	      initial.cache_floor == IO_CACHE_WORKFLOW_FLOOR &&
	      initial.cache_cap == IO_CACHE_WORKFLOW_CAP,
	      "workflow starts with the configured cache partition");
	protected_resident = initial.cache_resident < initial.cache_floor ?
		initial.cache_resident : initial.cache_floor;
	memset(&report, 0, sizeof(report));
	report.ready = 1;
	report.owner = initial.owner;
	write_exact(report_fd, &report, sizeof(report), "report workflow ready");
	read_exact(command_fd, &command, sizeof(command), "read workflow probe");

	check(io_policy_info(&pressured) == 0,
	      "snapshot pressured workflow cache state");
	check(read(fd, &value, 1) == 1 && value == 'W',
	      "read protected workflow hot block");
	check(close(fd) == 0, "close workflow probe reader");
	check(io_policy_info(&after_read) == 0,
	      "snapshot workflow after read");
	fd = open("wfhot", O_WRONLY);
	check(fd >= 0, "open workflow progress writer");
	check(write(fd, &value, 1) == 1, "complete workflow control write");
	check(close(fd) == 0, "close workflow progress writer");
	check(io_policy_info(&after_write) == 0,
	      "snapshot workflow after write");

	report.ready = 0;
	report.cache_isolated =
		pressured.owner == initial.owner &&
		pressured.cache_floor == initial.cache_floor &&
		pressured.cache_cap == initial.cache_cap &&
		pressured.cache_resident >= protected_resident &&
		pressured.cache_resident <= pressured.cache_cap;
	report.bounded_progress =
		after_write.physical_writes > after_read.physical_writes;
	report.control_class =
		after_write.io_class == IO_POLICY_CLASS_CONTROL;
	report.owner = after_write.owner;
	report.resident = after_read.cache_resident;
	write_exact(report_fd, &report, sizeof(report),
		    "report workflow probe result");
	check(close(command_fd) == 0 && close(report_fd) == 0,
	      "close workflow controls");
	exit(0);
}

int main(void)
{
	struct io_policy_info lineage;
	struct workflow_report report;
	int workflow_command[2];
	int workflow_report[2];
	int attacker_ready[2];
	int attacker_stop_pipe[2];
	int workflow;
	int attacker;
	int status = -1;
	char signal = 1;
	uint workflow_owner;

	printf("iobudget_ucore: block I/O isolation test\n");
	check_sized_abi();
	check(io_policy_info(&lineage) == 0,
	      "snapshot boot workflow I/O identity");
	check((lineage.owner & IO_POLICY_OWNER_SCOPE_FLAG) != 0,
	      "boot workload has an immutable workflow owner");
	check_thread_exit_lease_cleanup();
	printf("iobudget_ucore: thread_exit_lease_cleanup=1\n");
	check_scheduler_interrupt_progress();
	printf("iobudget_ucore: scheduler_interrupt_progress=1\n");
	check_fault_exit_cleanup(lineage.owner);
	printf("iobudget_ucore: fault_exit_cleanup=1\n");
	check(pipe(workflow_command) == 0 && pipe(workflow_report) == 0,
	      "create workflow control pipes");
	printf("iobudget_ucore: workflow_pipes=1\n");
	check(agent_scope_delegate_fd(workflow_command[0]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(workflow_report[1]) == AGENT_STATUS_OK,
	      "delegate workflow control endpoints");
	printf("iobudget_ucore: workflow_delegate=1\n");
	workflow = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(workflow >= 0, "create control workflow");
	printf("iobudget_ucore: workflow_fork=%d\n", workflow);
	if (workflow == 0)
		run_workflow(workflow_command[0], workflow_report[1]);
	check(close(workflow_command[0]) == 0 && close(workflow_report[1]) == 0,
	      "close parent copies of workflow endpoints");
	read_exact(workflow_report[0], &report, sizeof(report),
		   "receive workflow readiness");
	check(report.ready &&
	      (report.owner & IO_POLICY_OWNER_SCOPE_FLAG) != 0 &&
	      report.owner != lineage.owner,
	      "workflow has a persistent scoped owner");
	workflow_owner = report.owner;

	check(pipe(attacker_ready) == 0 && pipe(attacker_stop_pipe) == 0,
	      "create attacker control pipes");
	check(agent_scope_delegate_fd(attacker_ready[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(attacker_stop_pipe[0]) == AGENT_STATUS_OK,
	      "delegate lineage attacker controls");
	attacker = fork();
	check(attacker >= 0, "create lineage attacker");
	if (attacker == 0)
		run_lineage_attacker(attacker_ready[1], attacker_stop_pipe[0],
				     lineage.owner);
	check(close(attacker_ready[1]) == 0 && close(attacker_stop_pipe[0]) == 0,
	      "close parent copies of attacker endpoints");
	read_exact(attacker_ready[0], &signal, sizeof(signal),
		   "wait for lineage pressure");
	printf("iobudget_ucore: lineage_rate_accounting=1 immutable_owner=1\n");
	printf("iobudget_ucore: nested_io_attribution=1\n");
	write_exact(workflow_command[1], &signal, sizeof(signal),
		    "start workflow probe");
	read_exact(workflow_report[0], &report, sizeof(report),
		   "receive workflow result");
	check(report.cache_isolated, "retain workflow cache floor under pressure");
	check(report.bounded_progress, "workflow makes progress under pressure");
	check(report.control_class, "trusted workflow uses control reserve");
	check(report.owner == workflow_owner,
	      "fresh workflow receives an isolated immutable owner");
	printf("iobudget_ucore: cache_scope_isolation=1\n");
	printf("iobudget_ucore: workflow_bounded_progress=1\n");
	printf("iobudget_ucore: control_reserve_progress=1\n");

	write_exact(attacker_stop_pipe[1], &signal, sizeof(signal),
		    "stop lineage attacker");
	check(waitpid(attacker, &status) == attacker && status == 0,
	      "wait lineage attacker");
	status = -1;
	check(waitpid(workflow, &status) == workflow && status == 0,
	      "wait control workflow");
	check(close(workflow_command[1]) == 0 &&
	      close(workflow_report[0]) == 0 && close(attacker_ready[0]) == 0 &&
	      close(attacker_stop_pipe[1]) == 0,
	      "close parent controls");
	printf("iobudget_ucore: parent passed\n");
	return 0;
}
