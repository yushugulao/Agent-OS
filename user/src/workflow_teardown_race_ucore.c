#include <agent.h>
#include <exec_policy_manifest.h>
#include <fcntl.h>
#include <io_policy.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

#ifndef WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP
#error "runner must define WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP"
#endif
#ifndef WORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP
#error "runner must define WORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP"
#endif

#define START_WORKERS 1
#define PHASE_DEADLINE_MS 30000
#define PIPE_FILE_OBJECTS 2
#define PRIMARY_PIN_BASE_OBJECTS (3 * PIPE_FILE_OBJECTS)
#define RECYCLE_PIN_BASE_OBJECTS (2 * PIPE_FILE_OBJECTS)
#define RECLAIM_TARGET_COUNT 2
#define LIFECYCLE_REUSE_MIN_ROUNDS \
	(WORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP + 1)
#define IO_PRESSURE_BLOCKS (IO_POLICY_WORKFLOW_NORMAL_BURST + 16)
#define IO_PRESSURE_BYTES (IO_PRESSURE_BLOCKS * 1024)
#define IO_PROBE_BYTES 1024
#define EXEC_PUBLIC_IMAGE "wf_public"
#define PENDING_EXEC_READY_MARKER \
	"workflow_teardown_race_ucore: pending_exec_public_image=1\n"
#define PENDING_EXEC_ESCAPE_MARKER \
	"workflow_teardown_race_ucore: check failed: pending PUBLIC descendant escaped\n"
#define PENDING_EXEC_ESCAPE_DELAY 500
#define PENDING_EXEC_VERIFY_DELAY (4 * PENDING_EXEC_ESCAPE_DELAY)

/* 工作流子进程会继承创建辅助函数的活动栈帧；保留进程阶段调用边界，使固定用户栈预算可组合测量。 */
#define TEST_PHASE_NOINLINE __attribute__((noinline))

#if WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP <= PRIMARY_PIN_BASE_OBJECTS
#error "workflow teardown file-object capacity is too small"
#endif
#if WORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP == 0
#error "workflow teardown global reserve must not be empty"
#endif

enum race_event_kind {
	EVENT_IO_WRITER_READY = 1,
	EVENT_FILE_PROBE_READY,
	EVENT_FILE_PIN_CONFIRMED,
	EVENT_FILE_PIN_MISSED,
	EVENT_IO_PINNED,
	EVENT_PUBLIC_EXIT,
	EVENT_CPU_MEMBER,
	EVENT_FAILURE,
};

enum primary_flag {
	PRIMARY_IO_PINNED = 1U << 0,
	PRIMARY_PUBLIC_EXIT = 1U << 1,
	PRIMARY_CPU_MEMBER = 1U << 2,
	PRIMARY_FILE_PIN = 1U << 3,
	PRIMARY_LIFECYCLE_ABI = 1U << 4,
	PRIMARY_FRESH_RESOURCES = 1U << 5,
};

enum teardown_mode {
	TEARDOWN_FACTORY_CLOSE = 1,
	TEARDOWN_CONTROLLER_EXIT,
};

enum primary_report_phase {
	PRIMARY_REPORT_IDENTITY = 1,
	PRIMARY_REPORT_FRESH_RESOURCES,
	PRIMARY_REPORT_MEMBERS_READY,
	PRIMARY_REPORT_FILE_PINNED,
	PRIMARY_REPORT_PREPARED,
	PRIMARY_REPORT_FINAL,
};

enum parent_wait_kind {
	PARENT_WAIT_READ_EXACT = 1,
	PARENT_WAIT_READ_EOF,
	PARENT_WAIT_PID,
	PARENT_WAIT_SEMAPHORE,
};

struct race_event {
	int kind;
	int value;
	uint64 value0;
	uint64 value1;
};

struct primary_report {
	uint scope_id;
	uint flags;
	uint mode;
	uint phase;
	struct agent_workflow_lifecycle_info lifecycle;
};

struct progress_report {
	uint scope_id;
	uint owner;
	uint64 before_sequence;
	uint64 after_sequence;
};

struct recycle_report {
	uint scope_id;
	int stale_status[RECLAIM_TARGET_COUNT];
	struct agent_workflow_lifecycle_info lifecycle;
};

struct recycle_command {
	struct agent_workflow_lifecycle_key
		stale_key[RECLAIM_TARGET_COUNT];
};

struct lifecycle_probe_report {
	uint scope_id;
	struct agent_workflow_lifecycle_info lifecycle;
};

struct lifecycle_probe_guard {
	int pid;
	uint scope_id;
	struct agent_workflow_lifecycle_key lifecycle_key;
};

struct pending_exec_report {
	int worker_pid;
	struct agent_workflow_lifecycle_key lifecycle_key;
};

struct teardown_main_state {
	struct primary_report factory_close;
	struct primary_report natural_exit;
	struct agent_workflow_lifecycle_key retired[RECLAIM_TARGET_COUNT];
	struct agent_workflow_lifecycle_key factory_replacement;
	struct agent_workflow_lifecycle_key natural_replacement;
};

struct primary_parent_state {
	struct primary_report identity;
	struct primary_report prepared;
	struct primary_report final;
};

struct parent_wait_state {
	volatile int active;
	volatile int done;
	int kind;
	int fd_or_id;
	void *buffer;
	size_t size;
	int result;
	int status;
};

static struct teardown_main_state teardown_main;
static struct primary_parent_state primary_parent;
static struct primary_report primary_root_report;
static struct parent_wait_state parent_wait;

static char io_pressure[IO_PRESSURE_BYTES];
static struct agent_workflow_lifecycle_info exec_transition_lifecycle;
static struct agent_context_record exec_transition_record;
static struct agent_context_header exec_transition_header;
static struct agent_context_detail exec_transition_detail;
static struct agent_event exec_transition_event;
static struct agent_info exec_transition_info;
static struct agent_info exec_transition_info_before;
static struct io_policy_info exec_transition_io;
static struct io_policy_info exec_transition_io_after;
static volatile int hidden_reader_missed;
static volatile uint64 cpu_counter;
static struct agent_workflow_lifecycle_info factory_lifecycle_baseline;
static int factory_lifecycle_baseline_set;
static int hidden_first_ready;
static int hidden_second_ready;
static int hidden_pipe[2];
static int close_spawn_report_fd = -1;
static int close_spawn_lifetime_fd = -1;
static char control_worker_image[11];

static void check(int ok, const char *message)
{
	if (ok)
		return;
	printf("workflow_teardown_race_ucore: check failed: %s\n", message);
	exit(1);
}

static int bytes_equal(const void *left, const void *right, size_t size)
{
	const unsigned char *left_bytes = left;
	const unsigned char *right_bytes = right;

	for (size_t i = 0; i < size; i++)
		if (left_bytes[i] != right_bytes[i])
			return 0;
	return 1;
}

static void decimal_format(char *out, size_t size, int value)
{
	char reversed[16];
	int count = 0;

	check(out != 0 && size > 1 && value > 0, "format positive pid");
	do {
		reversed[count++] = '0' + value % 10;
		value /= 10;
	} while (value != 0 && count < (int)sizeof(reversed));
	check((size_t)count < size, "formatted pid fits");
	for (int i = 0; i < count; i++)
		out[i] = reversed[count - i - 1];
	out[count] = 0;
}

static int decimal_parse(const char *text)
{
	int value = 0;

	if (text == 0 || *text == 0)
		return -1;
	for (; *text != 0; text++) {
		if (*text < '0' || *text > '9' || value > 1000000)
			return -1;
		value = value * 10 + (*text - '0');
	}
	return value > 0 ? value : -1;
}

static void uint64_format(char *out, size_t size, uint64 value)
{
	char reversed[24];
	int count = 0;

	check(out != 0 && size > 1 && value != 0,
	      "format positive lifecycle component");
	do {
		reversed[count++] = '0' + value % 10;
		value /= 10;
	} while (value != 0 && count < (int)sizeof(reversed));
	check((size_t)count < size, "formatted lifecycle component fits");
	for (int i = 0; i < count; i++)
		out[i] = reversed[count - i - 1];
	out[count] = 0;
}

static int uint64_parse(const char *text, uint64 *out)
{
	uint64 value = 0;

	if (text == 0 || out == 0 || *text == 0)
		return -1;
	for (; *text != 0; text++) {
		uint64 digit;

		if (*text < '0' || *text > '9')
			return -1;
		digit = *text - '0';
		if (value > (~0ULL - digit) / 10)
			return -1;
		value = value * 10 + digit;
	}
	if (value == 0)
		return -1;
	*out = value;
	return 0;
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

static __attribute__((noreturn)) void parent_wait_fail(
	const char *reason, const char *phase)
{
	printf("workflow_teardown_race_ucore: check failed: parent wait %s: %s\n",
	       reason, phase);
	exit(1);
	__builtin_unreachable();
}

static void parent_waittid(int tid, int expected_status, const char *phase);

static TEST_PHASE_NOINLINE void parent_wait_worker(void *unused)
{
	char *cursor = parent_wait.buffer;
	size_t remaining = parent_wait.size;
	char byte;

	(void)unused;
	parent_wait.result = -1;
	parent_wait.status = -1;
	switch (parent_wait.kind) {
	case PARENT_WAIT_READ_EXACT:
		parent_wait.result = 0;
		while (remaining != 0) {
			ssize_t received = read(parent_wait.fd_or_id, cursor,
						remaining);

			if (received <= 0) {
				parent_wait.result = -1;
				break;
			}
			cursor += received;
			remaining -= received;
		}
		break;
	case PARENT_WAIT_READ_EOF:
		parent_wait.result =
			read(parent_wait.fd_or_id, &byte, 1) < 0 ? 0 : -1;
		break;
	case PARENT_WAIT_PID:
		parent_wait.result = waitpid(parent_wait.fd_or_id,
					     &parent_wait.status);
		break;
	case PARENT_WAIT_SEMAPHORE:
		parent_wait.result = semaphore_down(parent_wait.fd_or_id);
		break;
	default:
		parent_wait.result = -1;
		break;
	}
	__sync_synchronize();
	parent_wait.done = 1;
	exit(0);
}

static void parent_wait_run(int kind, int fd_or_id, void *buffer,
			    size_t size, const char *phase)
{
	int tid;
	int64 deadline;

	if (parent_wait.active)
		parent_wait_fail("overlap", phase);
	parent_wait.active = 1;
	parent_wait.done = 0;
	parent_wait.kind = kind;
	parent_wait.fd_or_id = fd_or_id;
	parent_wait.buffer = buffer;
	parent_wait.size = size;
	parent_wait.result = -1;
	parent_wait.status = -1;
	__sync_synchronize();
	tid = thread_create(parent_wait_worker, 0);
	if (tid <= 0)
		parent_wait_fail("helper admission failed", phase);
	deadline = get_mtime() + PHASE_DEADLINE_MS;
	while (!parent_wait.done) {
		if (get_mtime() >= deadline)
			parent_wait_fail("timed out", phase);
		if (sched_yield() < 0)
			parent_wait_fail("yield failed", phase);
	}
	__sync_synchronize();
	parent_waittid(tid, 0, phase);
	parent_wait.active = 0;
	if (parent_wait.result < 0)
		parent_wait_fail("failed", phase);
}

static void parent_read_exact(int fd, void *buffer, size_t size,
			      const char *phase)
{
	parent_wait_run(PARENT_WAIT_READ_EXACT, fd, buffer, size, phase);
}

static void parent_read_eof(int fd, const char *phase)
{
	parent_wait_run(PARENT_WAIT_READ_EOF, fd, 0, 0, phase);
}

static int parent_waitpid(int pid, const char *phase)
{
	parent_wait_run(PARENT_WAIT_PID, pid, 0, 0, phase);
	if (parent_wait.result != pid)
		parent_wait_fail("wrong child", phase);
	return parent_wait.status;
}

static void parent_semaphore_down(int sid, const char *phase)
{
	parent_wait_run(PARENT_WAIT_SEMAPHORE, sid, 0, 0, phase);
}

static void parent_waittid(int tid, int expected_status, const char *phase)
{
	int64 deadline = get_mtime() + PHASE_DEADLINE_MS;
	int status;

	for (;;) {
		status = syscall(SYS_waittid, tid);
		if (status != -2)
			break;
		if (get_mtime() >= deadline)
			parent_wait_fail("timed out", phase);
		if (sched_yield() < 0)
			parent_wait_fail("yield failed", phase);
	}
	if (status != expected_status)
		parent_wait_fail("unexpected thread status", phase);
}

static void send_event(int fd, int kind, int value, uint64 value0,
		       uint64 value1)
{
	struct race_event event;

	event.kind = kind;
	event.value = value;
	event.value0 = value0;
	event.value1 = value1;
	write_exact(fd, &event, sizeof(event), "send race event");
}

static uint current_scope(void)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "read workflow identity");
	check(info.is_agent == 1, "workflow root is an Agent");
	check(info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "workflow root is Orchestrator");
	check(info.filesystem_domain >= 3, "workflow has dynamic scope");
	return (uint)info.filesystem_domain;
}

static int lifecycle_key_equal(struct agent_workflow_lifecycle_key left,
			       struct agent_workflow_lifecycle_key right)
{
	return left.id == right.id && left.generation == right.generation;
}

static void snapshot_current_lifecycle(
	struct agent_workflow_lifecycle_info *info)
{
	memset(info, 0, sizeof(*info));
	check(agent_workflow_lifecycle_info(info, 0) == AGENT_STATUS_OK,
	      "snapshot current workflow lifecycle");
	check(info->version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      info->struct_size == sizeof(*info) && info->charged == 1 &&
	      info->reserved == 0 && info->key.id != 0 &&
	      info->key.reserved == 0 && info->key.generation != 0,
	      "validate immutable workflow lifecycle key");
}

static TEST_PHASE_NOINLINE void check_lifecycle_sized_prefix(void)
{
	struct lifecycle_prefix_probe {
		uint version;
		uint struct_size;
		uint guard;
	} prefix;
	struct lifecycle_short_probe {
		unsigned char bytes[2 * sizeof(uint)];
		uint guard;
	} short_probe;
	struct lifecycle_v2_probe {
		uint version;
		uint struct_size;
		uint charged;
		uint reserved;
		struct agent_workflow_lifecycle_key key;
		uint context_lane_depth;
		uint context_lane_waiters;
		uint metadata_txn_owned;
		uint metadata_txn_waiters;
		uint resource_account_valid;
		uint resource_account_slot;
		uint64 resource_account_generation;
	} v2;
	struct lifecycle_oversized_probe {
		struct agent_workflow_lifecycle_info info;
		uint guard;
	} oversized;
	struct agent_workflow_lifecycle_info bad_parameter;
	struct agent_workflow_lifecycle_info bad_parameter_before;
	struct agent_workflow_lifecycle_info current_before;
	struct agent_workflow_lifecycle_info stale_result;
	struct agent_workflow_lifecycle_info current_after;
	struct agent_workflow_lifecycle_key over_capacity_key;

	memset(&prefix, 0, sizeof(prefix));
	prefix.guard = 0x5a5aa5a5U;
	check(syscall(SYS_agent_workflow_lifecycle_info, &prefix,
		      2 * sizeof(uint), 0, 0, 0) == AGENT_STATUS_OK,
	      "accept workflow lifecycle sized prefix");
	check(prefix.version == AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION &&
	      prefix.struct_size == AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE &&
	      prefix.guard == 0x5a5aa5a5U,
	      "negotiate legacy workflow lifecycle prefix");
	check(sizeof(v2) == AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE,
	      "freeze workflow lifecycle v2 test layout");
	memset(&v2, 0, sizeof(v2));
	check(syscall(SYS_agent_workflow_lifecycle_info, &v2,
		      sizeof(v2), 0, 0, 0) == AGENT_STATUS_OK &&
	      v2.version == AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION &&
	      v2.struct_size == AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE &&
	      v2.charged == 1 && v2.key.id != 0 &&
	      v2.key.generation != 0 && v2.resource_account_valid == 1,
	      "preserve complete workflow lifecycle v2 projection");
	memset(&oversized, 0, sizeof(oversized));
	oversized.guard = 0xa55a3cc3U;
	check(syscall(SYS_agent_workflow_lifecycle_info, &oversized,
		      ~0ULL, 0, 0, 0) == AGENT_STATUS_OK &&
	      oversized.info.struct_size == sizeof(oversized.info) &&
	      oversized.guard == 0xa55a3cc3U,
	      "clamp oversized lifecycle copy before tail guard");
	memset(&short_probe, 0xa5, sizeof(short_probe));
	short_probe.guard = 0x3cc3c33cU;
	check(syscall(SYS_agent_workflow_lifecycle_info, &short_probe,
		      2 * sizeof(uint) - 1, 0, 0, 0) == -1,
	      "reject undersized workflow lifecycle prefix");
	for (uint i = 0; i < sizeof(short_probe.bytes); i++)
		check(short_probe.bytes[i] == 0xa5,
		      "undersized lifecycle call leaves prefix untouched");
	check(short_probe.guard == 0x3cc3c33cU,
	      "undersized lifecycle call leaves guard untouched");
	memset(&bad_parameter, 0x6d, sizeof(bad_parameter));
	bad_parameter_before = bad_parameter;
	check(syscall(SYS_agent_workflow_lifecycle_info, &bad_parameter,
		      sizeof(bad_parameter), 1U << 7, 0, 0) ==
		      AGENT_STATUS_BAD_PARAM &&
	      bytes_equal(&bad_parameter, &bad_parameter_before,
		  sizeof(bad_parameter)),
	      "unknown lifecycle flags fail without writing");
	check(syscall(SYS_agent_workflow_lifecycle_info, &bad_parameter,
		      sizeof(bad_parameter),
		      AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT,
		      0, 0) == AGENT_STATUS_BAD_PARAM &&
	      bytes_equal(&bad_parameter, &bad_parameter_before,
		  sizeof(bad_parameter)),
	      "invalid lifecycle match key fails without writing");
	snapshot_current_lifecycle(&current_before);
	over_capacity_key.id = ~0U;
	over_capacity_key.reserved = 0;
	over_capacity_key.generation = 1;
	memset(&stale_result, 0, sizeof(stale_result));
	check(agent_workflow_lifecycle_info(&stale_result,
				    &over_capacity_key) == AGENT_STATUS_STALE,
	      "well-formed out-of-range lifecycle key is stale");
	snapshot_current_lifecycle(&current_after);
	check(lifecycle_key_equal(current_before.key, current_after.key),
	      "stale capacity probe preserves current lifecycle key");
}

static void check_factory_lifecycle_view(
	struct agent_workflow_lifecycle_info *snapshot)
{
	struct agent_workflow_lifecycle_info info;

	memset(&info, 0, sizeof(info));
	check(agent_workflow_lifecycle_info(&info, 0) == AGENT_STATUS_OK &&
	      info.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      info.struct_size == sizeof(info) && info.charged == 1 &&
	      info.reserved == 0 && info.key.id != 0 &&
	      info.key.reserved == 0 && info.key.generation != 0 &&
	      info.context_lane_depth == 0 && info.context_lane_waiters == 0 &&
	      info.metadata_txn_owned == 0 && info.metadata_txn_waiters == 0,
	      "boot factory has a charged idle lifecycle view");
	if (!factory_lifecycle_baseline_set) {
		factory_lifecycle_baseline = info;
		factory_lifecycle_baseline_set = 1;
	} else {
		check(bytes_equal(&info, &factory_lifecycle_baseline,
				  sizeof(info)),
		      "boot factory lifecycle key and idle view stay stable");
	}
	if (snapshot != 0)
		*snapshot = info;
}

static void check_factory_self_only_foreign_compare(
	struct agent_workflow_lifecycle_key foreign_key)
{
	struct agent_workflow_lifecycle_info before;
	struct agent_workflow_lifecycle_info compared;
	struct agent_workflow_lifecycle_info after;

	check_factory_lifecycle_view(&before);
	check(!lifecycle_key_equal(before.key, foreign_key),
	      "foreign workflow lifecycle differs from factory key");
	memset(&compared, 0xa5, sizeof(compared));
	check(agent_workflow_lifecycle_info(&compared, &foreign_key) ==
		      AGENT_STATUS_STALE,
	      "factory foreign lifecycle comparison is stale");
	check(bytes_equal(&before, &compared, sizeof(before)),
	      "foreign comparison returns only the factory self snapshot");
	check_factory_lifecycle_view(&after);
	check(bytes_equal(&before, &after, sizeof(before)),
	      "foreign comparison cannot alter the factory lifecycle view");
}

static TEST_PHASE_NOINLINE void check_fresh_workflow_resources(
	struct agent_workflow_lifecycle_info *lifecycle)
{
	struct io_policy_info io;
	uint scope_id;
	int fd;

	check_lifecycle_sized_prefix();
	snapshot_current_lifecycle(lifecycle);
	scope_id = current_scope();
	memset(&io, 0, sizeof(io));
	check(io_policy_info(&io) == 0 && io.version == IO_POLICY_VERSION &&
	      io.struct_size == sizeof(io), "snapshot fresh workflow I/O");
	check(io.owner == (IO_POLICY_OWNER_SCOPE_FLAG | scope_id) &&
	      io.io_class == IO_POLICY_CLASS_CONTROL && io.debt == 0 &&
	      io.leased == 0 && io.waiters == 0 && io.debt_waiters == 0 &&
	      io.admission_waiters == 0 && io.cache_resident == 0,
	      "fresh workflow account has no inherited I/O state");
	fd = open("freshino", O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "allocate inode in fresh workflow account");
	check(close(fd) == 0 && unlink("freshino") == 0,
	      "release newly allocated fresh workflow inode");
}

static int create_workflow_with_fds(const int *fds, int count)
{
	int64 deadline = get_mtime() + PHASE_DEADLINE_MS;

	for (;;) {
		int pid;

		for (int i = 0; i < count; i++)
			check(agent_scope_delegate_fd(fds[i]) == AGENT_STATUS_OK,
			      "delegate workflow endpoint");
		pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
		if (pid >= 0)
			return pid;
		if (get_mtime() >= deadline)
			check(0, "workflow admission did not recover");
	}
}

static void delegate_member_fds(int start_fd, int event_fd, int lifetime_fd)
{
	check(agent_scope_delegate_fd(start_fd) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(event_fd) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(lifetime_fd) == AGENT_STATUS_OK,
	      "delegate member endpoints");
}

static TEST_PHASE_NOINLINE void seed_volatile_metadata(void)
{
	struct agent_file_meta meta;
	int fd;

	check(agent_file_meta_init() == 0, "initialize live metadata catalog");
	fd = open("racedirty", O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create volatile metadata object");
	check(write(fd, "D", 1) == 1, "write volatile metadata object");
	check(close(fd) == 0, "close volatile metadata object");
	memset(&meta, 0, sizeof(meta));
	meta.fid = 91001;
	meta.flags = 0;
	strcpy(meta.physical_name, "racedirty");
	strcpy(meta.logical_path, "teardown/racedirty");
	strcpy(meta.project, "teardown-race");
	strcpy(meta.workflow, "forced-revoke");
	strcpy(meta.run_id, "primary");
	strcpy(meta.stage, "armed");
	strcpy(meta.kind, "artifact");
	strcpy(meta.status, "pending");
	strcpy(meta.summary, "volatile teardown record");
	check(agent_file_meta_set(&meta) == AGENT_STATUS_OK,
	      "register volatile teardown metadata");
}

static TEST_PHASE_NOINLINE void io_writer(int start_fd, int event_fd)
{
	struct agent_event event;
	char token;
	int pinned;

	send_event(event_fd, EVENT_IO_WRITER_READY, 0, 0, 0);
	read_exact(start_fd, &token, 1, "release I/O writer");
	pinned = open("riopin", O_CREATE | O_WRONLY | O_TRUNC);
	check(pinned >= 0, "create pinned inode");
	check(unlink("riopin") == 0, "unlink pinned inode");
	write_exact(pinned, io_pressure, sizeof(io_pressure),
		    "write pinned inode pressure");
	send_event(event_fd, EVENT_IO_PINNED, 0, 1, 0);
	for (;;) {
		memset(&event, 0, sizeof(event));
		(void)agent_wait(&event, -1);
	}
}

static int public_identity_is_downgraded(void)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	return agent_info(&info) == 0 && info.is_agent == 0 &&
	       info.capability_mask == 0 && info.filesystem_domain == 0 &&
	       info.filesystem_capability_mask == 0;
}

static TEST_PHASE_NOINLINE void sentinel_public_exec_probe(
	const char *id_text, const char *generation_text, const char *fd_text,
	const char *scope_text)
{
	struct agent_workflow_lifecycle_key expected;
	uint64 id = 0;
	uint64 generation = 0;
	uint64 scope = 0;
	int64 io_wait_started;
	int64 io_wait_now;
	int scoped_fd = decimal_parse(fd_text);
	int io_fd;
	int probe[2];

	check(uint64_parse(id_text, &id) == 0 && id <= ~0U &&
	      uint64_parse(generation_text, &generation) == 0 &&
	      uint64_parse(scope_text, &scope) == 0 && scope <= ~0U &&
	      scope >= 3 && scoped_fd > 0,
	      "parse exec downgrade lineage and endpoint");
	memset(&expected, 0, sizeof(expected));
	expected.id = (uint)id;
	expected.generation = generation;
	memset(&exec_transition_lifecycle, 0,
	       sizeof(exec_transition_lifecycle));
	check(agent_workflow_lifecycle_info(&exec_transition_lifecycle,
					    &expected) ==
		      AGENT_STATUS_OK &&
	      exec_transition_lifecycle.charged == 1 &&
	      lifecycle_key_equal(exec_transition_lifecycle.key, expected),
	      "PUBLIC exec preserves immutable teardown lineage");
	memset(&exec_transition_info, 0, sizeof(exec_transition_info));
	check(agent_info(&exec_transition_info) == 0 &&
	      exec_transition_info.is_agent == 0 &&
	      exec_transition_info.agent_type == AGENT_TYPE_NONE &&
	      exec_transition_info.agent_id == 0 &&
	      exec_transition_info.agent_role == 0 &&
	      exec_transition_info.context_base == 0 &&
	      exec_transition_info.context_size == 0 &&
	      exec_transition_info.capability_mask == 0 &&
	      exec_transition_info.filesystem_domain == 0 &&
	      exec_transition_info.filesystem_capability_mask == 0 &&
	      exec_transition_info.event_queue_count == 0 &&
	      exec_transition_info.watch_count == 0 &&
	      exec_transition_info.heartbeat_interval == 0 &&
	      exec_transition_info.loop_state == AGENT_LOOP_NONE,
	      "PUBLIC exec clears Agent identity and endpoints");
	memset(&exec_transition_io, 0, sizeof(exec_transition_io));
	check(io_policy_info(&exec_transition_io) == 0 &&
	      exec_transition_io.version == IO_POLICY_VERSION &&
	      exec_transition_io.struct_size == sizeof(exec_transition_io) &&
	      exec_transition_io.owner ==
		      (IO_POLICY_OWNER_SCOPE_FLAG | (uint)scope) &&
	      exec_transition_io.io_class == IO_POLICY_CLASS_NORMAL,
	      "PUBLIC exec keeps immutable workflow resource ownership");
	memset(io_pressure, 'E', IO_PROBE_BYTES);
	io_fd = open("execioprobe", O_CREATE | O_WRONLY | O_TRUNC);
	check(io_fd >= 0, "create PUBLIC exec physical I/O probe");
	write_exact(io_fd, io_pressure, IO_PROBE_BYTES,
		    "write PUBLIC exec physical I/O probe");
	check(close(io_fd) == 0, "close PUBLIC exec physical I/O probe");
	io_wait_started = get_mtime();
	check(io_wait_started >= 0, "start PUBLIC exec I/O completion wait");
	for (;;) {
		memset(&exec_transition_io_after, 0,
		       sizeof(exec_transition_io_after));
		check(io_policy_info(&exec_transition_io_after) == 0,
		      "observe asynchronous PUBLIC exec physical I/O");
		io_wait_now = get_mtime();
		check(io_wait_now >= io_wait_started &&
			      io_wait_now - io_wait_started < PHASE_DEADLINE_MS,
		      "bound PUBLIC exec I/O completion wait");
		if (exec_transition_io_after.physical_writes >
			    exec_transition_io.physical_writes &&
		    exec_transition_io_after.completion_sequence >
			    exec_transition_io.completion_sequence)
			break;
		check(sched_yield() == 0,
		      "yield for asynchronous PUBLIC exec physical I/O");
	}
	check(exec_transition_io_after.owner ==
		      (IO_POLICY_OWNER_SCOPE_FLAG | (uint)scope) &&
	      exec_transition_io_after.io_class == IO_POLICY_CLASS_NORMAL &&
	      exec_transition_io_after.physical_writes >
		      exec_transition_io.physical_writes &&
	      exec_transition_io_after.completion_sequence >
		      exec_transition_io.completion_sequence &&
	      exec_transition_io_after.unreserved_transfers ==
		      exec_transition_io.unreserved_transfers,
	      "PUBLIC exec physical I/O stays on lifecycle resource account");
	check(unlink("execioprobe") == 0,
	      "remove PUBLIC exec physical I/O probe");
	check(write(scoped_fd, "X", 1) == -1,
	      "PUBLIC exec revokes delegated scoped endpoint");
	memset(&exec_transition_record, 0, sizeof(exec_transition_record));
	memset(&exec_transition_header, 0, sizeof(exec_transition_header));
	memset(&exec_transition_detail, 0, sizeof(exec_transition_detail));
	memset(&exec_transition_event, 0, sizeof(exec_transition_event));
	check(context_push(&exec_transition_record) == -1 &&
	      context_query(0, &exec_transition_record, 1) == -1 &&
	      context_snapshot(&exec_transition_header,
			       &exec_transition_record, 1) == -1 &&
	      context_detail(1, &exec_transition_detail) == -1 &&
	      context_rollback(1) == -1 && context_clear() == -1 &&
	      agent_wait(&exec_transition_event, 0) == -1 &&
	      agent_heartbeat(1) == -1,
	      "PUBLIC exec rejects Agent and Context operations");
	check(pipe(probe) == 0, "create Context unmap probe");
	check(write(probe[1], (const void *)AGENT_CONTEXT_BASE, 1) == -1,
	      "PUBLIC exec cannot read former Context mapping");
	check(close(probe[0]) == 0 && close(probe[1]) == 0,
	      "close Context unmap probe");
	printf("workflow_teardown_race_ucore: sentinel_public_exec=1 identity_cleared=1 context_unmapped=1 endpoints_revoked=1 scoped_fd_revoked=1 lifecycle_preserved=1 resource_domain_preserved=1 physical_io_charged=1 normal_class=1 argv_build_rollback=1\n");
}

static TEST_PHASE_NOINLINE void public_lineage_member(
	int event_fd, struct agent_workflow_lifecycle_key expected_key)
{
	struct agent_workflow_lifecycle_info lifecycle;
	int grandchild;

	memset(&lifecycle, 0, sizeof(lifecycle));
	if (!public_identity_is_downgraded() ||
	    agent_workflow_lifecycle_info(&lifecycle, 0) != AGENT_STATUS_OK ||
	    !lifecycle.charged ||
	    !lifecycle_key_equal(lifecycle.key, expected_key)) {
		send_event(event_fd, EVENT_FAILURE, 49, lifecycle.key.id,
			   lifecycle.key.generation);
		exit(49);
	}
	grandchild = fork();
	if (grandchild < 0) {
		send_event(event_fd, EVENT_FAILURE, 50, 0, 0);
		exit(50);
	}
	if (grandchild == 0) {
		memset(&lifecycle, 0, sizeof(lifecycle));
		if (!public_identity_is_downgraded() ||
		    agent_workflow_lifecycle_info(&lifecycle, 0) !=
				AGENT_STATUS_OK ||
		    !lifecycle.charged ||
		    !lifecycle_key_equal(lifecycle.key, expected_key)) {
			send_event(event_fd, EVENT_FAILURE, 51,
				   lifecycle.key.id,
				   lifecycle.key.generation);
			exit(51);
		}
		send_event(event_fd, EVENT_CPU_MEMBER, 0, getpid(), 0);
		for (;;)
			cpu_counter++;
	}
	send_event(event_fd, EVENT_PUBLIC_EXIT, 0, grandchild, 0);
	exit(0);
}

static TEST_PHASE_NOINLINE void file_pin_probe(int gate_fd, int event_fd)
{
	struct agent_event event;
	unsigned char token;
	int baseline;

	baseline = open("racedirty", O_RDONLY);
	check(baseline >= 0, "file pin probe baseline open");
	check(close(baseline) == 0, "close file pin probe baseline");
	send_event(event_fd, EVENT_FILE_PROBE_READY, 0, 0, 0);
	for (;;) {
		int fd;

		read_exact(gate_fd, &token, 1, "release file pin probe");
		fd = open("racedirty", O_RDONLY);
		if (fd >= 0) {
			check(close(fd) == 0, "close missed file pin probe");
			send_event(event_fd, EVENT_FILE_PIN_MISSED, token, 0, 0);
			continue;
		}
		send_event(event_fd, EVENT_FILE_PIN_CONFIRMED, token, 0, 0);
		break;
	}
	for (;;) {
		memset(&event, 0, sizeof(event));
		(void)agent_wait(&event, -1);
	}
}

static void hidden_reader(void *unused)
{
	char token;

	(void)unused;
	if (semaphore_up(hidden_first_ready) < 0)
		exit(60);
	if (read(hidden_pipe[0], &token, 1) != 1 || token != 'P') {
		hidden_reader_missed = 1;
		exit(61);
	}
	if (semaphore_up(hidden_second_ready) < 0)
		exit(62);
	/* 本轮返回表示未保留 fdget 引用；容量确认将其视为重试，不发出污染下一轮的无域事件。 */
	(void)read(hidden_pipe[0], &token, 1);
	hidden_reader_missed = 1;
	exit(0);
}

static TEST_PHASE_NOINLINE void arm_hidden_file_pin(int pin_release_fd,
					    int event_read_fd, uint *flags,
					    uint base_objects)
{
	struct race_event event;
	int fillers[WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP];
	unsigned char token;
	uint filler_count;
	int confirmed = 0;
	int attempt = 0;
	int64 deadline = get_mtime() + PHASE_DEADLINE_MS;

	check(base_objects < WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP,
	      "file pin topology fits configured capacity");
	filler_count = WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP - base_objects;
	hidden_first_ready = semaphore_create(0);
	hidden_second_ready = semaphore_create(0);
	check(hidden_first_ready >= 0 && hidden_second_ready >= 0,
	      "create hidden file barriers");
	/* runner 与内核控制器使用同一容量；内部管道占 base_objects，剩余 open 应触及资源域边界。 */
	/* 控制台对象不计入资源域但仍占 FD 表；错误经已计费报告管道传递，可关闭全部继承槽。 */
	check(close(0) == 0 && close(1) == 0 && close(2) == 0,
	      "release console descriptors for file pin oracle");
	while (!confirmed) {
		int hidden_tid;
		int missed = 0;

		attempt++;
		check(attempt <= 255, "hidden file pin retry bound");
		token = (unsigned char)attempt;
		hidden_reader_missed = 0;
		check(pipe(hidden_pipe) == 0, "create hidden file pipe");
		hidden_tid = thread_create(hidden_reader, 0);
		check(hidden_tid > 0, "create hidden file reader");
		parent_semaphore_down(hidden_first_ready,
				      "wait first hidden blocking read");
		{
			char proof = 'P';

			write_exact(hidden_pipe[1], &proof, 1,
			    "prove first blocking read");
		}
		parent_semaphore_down(hidden_second_ready,
				      "wait second hidden blocking read");
		/* 描述符仍安装时填满对象，再完整 yield 一轮；角色探测失败即确认 fdget 仍持有已释放对象。
		 * 未命中时彻底排空并重试，调度延迟不能伪造引用固定。 */
		for (uint i = 0; i < filler_count; i++) {
			fillers[i] = open("racedirty", O_RDONLY);
			check(fillers[i] >= 0,
			      "fill workflow file-object domain");
		}
		check(sleep(1) == 0, "yield hidden reader into fdget");
		check(close(hidden_pipe[0]) == 0,
		      "close descriptor behind temporary file reference");
		write_exact(pin_release_fd, &token, 1,
			    "release file pin oracle");
		for (;;) {
			parent_read_exact(event_read_fd, &event, sizeof(event),
					  "receive file pin oracle result");
			switch (event.kind) {
			case EVENT_FILE_PIN_CONFIRMED:
				check(event.value == attempt,
				      "match confirmed file pin attempt");
				confirmed = 1;
				break;
			case EVENT_FILE_PIN_MISSED:
				check(event.value == attempt,
				      "match missed file pin attempt");
				missed = 1;
				break;
			case EVENT_PUBLIC_EXIT:
				*flags |= PRIMARY_PUBLIC_EXIT;
				continue;
			case EVENT_CPU_MEMBER:
				*flags |= PRIMARY_CPU_MEMBER;
				continue;
			case EVENT_FAILURE:
				check(0, "file pin oracle member failed");
				break;
			default:
				check(0, "unexpected file pin oracle event");
			}
			break;
		}
		for (uint i = 0; i < filler_count; i++)
			check(close(fillers[i]) == 0,
			      "release file pin oracle object");
		if (confirmed)
			break;
		check(missed && close(hidden_pipe[1]) == 0,
		      "release missed hidden pipe");
		parent_waittid(hidden_tid, 0, "reap missed hidden reader");
		check(hidden_reader_missed, "validate missed hidden reader");
		check(get_mtime() < deadline, "file pin oracle did not arm");
	}
	check(close(pin_release_fd) == 0, "close file pin release endpoint");
}

static TEST_PHASE_NOINLINE void primary_workflow_root(
	int report_fd, int lifetime_fd, int pin_gate_read, int pin_gate_write,
	int arm_fd, int teardown_mode)
{
	struct primary_report *report = &primary_root_report;
	struct race_event event;
	int start[2];
	int events[2];
	int writer_ready = 0;
	int file_probe_ready = 0;
	int public_child;
	int public_status = -1;
	uint flags = 0;
	char release[START_WORKERS];
	char selection;
	struct agent_workflow_lifecycle_key selected_key;

	memset(report, 0, sizeof(*report));
	report->mode = teardown_mode;
	snapshot_current_lifecycle(&report->lifecycle);
	selected_key = report->lifecycle.key;
	report->scope_id = current_scope();
	report->phase = PRIMARY_REPORT_IDENTITY;
	write_exact(report_fd, report, sizeof(*report),
		    "report primary lifecycle identity");
	parent_read_exact(arm_fd, &selection, 1,
			  "receive primary lifecycle selection");
	if (selection == 'R')
		exit(0);
	check(selection == 'S', "accept primary lifecycle selection");
	memset(io_pressure, 'I', sizeof(io_pressure));
	memset(release, 'R', sizeof(release));
	check_fresh_workflow_resources(&report->lifecycle);
	check(lifecycle_key_equal(report->lifecycle.key, selected_key),
	      "selected primary lifecycle remains immutable");
	flags |= PRIMARY_LIFECYCLE_ABI | PRIMARY_FRESH_RESOURCES;
	seed_volatile_metadata();
	report->flags = flags;
	report->phase = PRIMARY_REPORT_FRESH_RESOURCES;
	write_exact(report_fd, report, sizeof(*report),
		    "report fresh primary resources");
	check(pipe(start) == 0 && pipe(events) == 0,
	      "create member coordination pipes");
	delegate_member_fds(start[0], events[1], lifetime_fd);
	int writer = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(writer >= 0, "create I/O writer member");
	if (writer == 0)
		io_writer(start[0], events[1]);
	check(agent_scope_delegate_fd(pin_gate_read) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(events[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(lifetime_fd) == AGENT_STATUS_OK,
	      "delegate file pin probe endpoints");
	int file_probe = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(file_probe >= 0, "create file pin oracle member");
	if (file_probe == 0)
		file_pin_probe(pin_gate_read, events[1]);
	check(close(pin_gate_read) == 0,
	      "close root file pin probe reader");

	check(agent_scope_delegate_fd(events[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(lifetime_fd) == AGENT_STATUS_OK,
	      "delegate PUBLIC lineage endpoints");
	public_child = fork();
	check(public_child >= 0, "create PUBLIC lifecycle member");
	if (public_child == 0)
		public_lineage_member(events[1], report->lifecycle.key);
	check(close(events[1]) == 0 && close(lifetime_fd) == 0,
	      "close root member and lifecycle endpoints");

	while (!writer_ready || !file_probe_ready) {
		parent_read_exact(events[0], &event, sizeof(event),
				  "receive primary member readiness");
		switch (event.kind) {
		case EVENT_IO_WRITER_READY:
			writer_ready = 1;
			break;
		case EVENT_FILE_PROBE_READY:
			file_probe_ready = 1;
			break;
		case EVENT_PUBLIC_EXIT:
			flags |= PRIMARY_PUBLIC_EXIT;
			break;
		case EVENT_CPU_MEMBER:
			flags |= PRIMARY_CPU_MEMBER;
			break;
		case EVENT_FAILURE:
			check(0, "member failed before release");
			break;
		default:
			check(0, "unexpected readiness event");
		}
	}
	report->flags = flags;
	report->phase = PRIMARY_REPORT_MEMBERS_READY;
	write_exact(report_fd, report, sizeof(*report),
		    "report primary members ready");

	arm_hidden_file_pin(pin_gate_write, events[0], &flags,
			     PRIMARY_PIN_BASE_OBJECTS);
	flags |= PRIMARY_FILE_PIN;
	report->flags = flags;
	report->phase = PRIMARY_REPORT_FILE_PINNED;
	write_exact(report_fd, report, sizeof(*report),
		    "report hidden file pin");
	write_exact(start[1], release, sizeof(release),
		    "release pressure members");
	check(close(start[0]) == 0 && close(start[1]) == 0,
	      "close member start pipe");
	while ((flags & (PRIMARY_IO_PINNED | PRIMARY_PUBLIC_EXIT |
			 PRIMARY_CPU_MEMBER)) !=
	       (PRIMARY_IO_PINNED | PRIMARY_PUBLIC_EXIT |
		PRIMARY_CPU_MEMBER)) {
		parent_read_exact(events[0], &event, sizeof(event),
				  "receive armed primary resource state");
		switch (event.kind) {
		case EVENT_IO_PINNED:
			flags |= PRIMARY_IO_PINNED;
			break;
		case EVENT_PUBLIC_EXIT:
			flags |= PRIMARY_PUBLIC_EXIT;
			break;
		case EVENT_CPU_MEMBER:
			flags |= PRIMARY_CPU_MEMBER;
			break;
		case EVENT_FAILURE:
			check(0, "member failed while arming revoke");
			break;
		default:
			break;
		}
	}
	public_status = parent_waitpid(public_child,
				      "reap voluntarily exiting PUBLIC member");
	check(public_status == 0, "validate exiting PUBLIC member status");
	check(!hidden_reader_missed, "hidden file reader remains blocked");

	report->scope_id = current_scope();
	report->flags = flags;
	report->phase = PRIMARY_REPORT_PREPARED;
	write_exact(report_fd, report, sizeof(*report),
		    "report primary prepared state");
	{
		char arm;

		parent_read_exact(arm_fd, &arm, 1,
				  "receive final teardown arm");
		check(arm == 'A' && close(arm_fd) == 0,
		      "consume final teardown arm");
	}
	snapshot_current_lifecycle(&report->lifecycle);
	check(lifecycle_key_equal(report->lifecycle.key, selected_key),
	      "final primary lifecycle remains immutable");
	report->flags = flags;
	report->phase = PRIMARY_REPORT_FINAL;
	write_exact(report_fd, report, sizeof(*report),
		    "report final active teardown snapshot");
	check(close(report_fd) == 0, "close primary report endpoint");
	if (teardown_mode == TEARDOWN_CONTROLLER_EXIT)
		exit(0);
	for (;;) {
		struct agent_event wait_event;

		memset(&wait_event, 0, sizeof(wait_event));
		(void)agent_wait(&wait_event, -1);
	}
}

static TEST_PHASE_NOINLINE void lifecycle_probe_root(int report_fd)
{
	struct lifecycle_probe_report report;

	memset(&report, 0, sizeof(report));
	snapshot_current_lifecycle(&report.lifecycle);
	report.scope_id = current_scope();
	write_exact(report_fd, &report, sizeof(report),
		    "report lifecycle reclamation probe");
	check(close(report_fd) == 0,
	      "close lifecycle reclamation report endpoint");
	for (;;) {
		struct agent_event event;

		memset(&event, 0, sizeof(event));
		(void)agent_wait(&event, -1);
	}
}

static TEST_PHASE_NOINLINE int try_launch_lifecycle_probe(
	struct lifecycle_probe_guard *guard)
{
	struct lifecycle_probe_report report;
	int reports[2];
	int pid;

	check(pipe(reports) == 0,
	      "create lifecycle reclamation probe pipe");
	check(agent_scope_delegate_fd(reports[1]) == AGENT_STATUS_OK,
	      "delegate lifecycle reclamation probe endpoints");
	pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	if (pid < 0) {
		check(close(reports[0]) == 0 && close(reports[1]) == 0,
		      "close rejected lifecycle probe pipes");
		return 0;
	}
	if (pid == 0)
		lifecycle_probe_root(reports[1]);
	check(close(reports[1]) == 0,
	      "close lifecycle reclamation child endpoints");
	memset(&report, 0, sizeof(report));
	parent_read_exact(reports[0], &report, sizeof(report),
			  "receive lifecycle reclamation probe");
	check(close(reports[0]) == 0,
	      "close lifecycle reclamation report reader");
	guard->pid = pid;
	guard->scope_id = report.scope_id;
	guard->lifecycle_key = report.lifecycle.key;
	return 1;
}

static TEST_PHASE_NOINLINE void close_lifecycle_probes(
	struct lifecycle_probe_guard *guards, int count)
{
	for (int i = 0; i < count; i++)
		check(agent_workflow_close(guards[i].scope_id) ==
			      AGENT_STATUS_OK,
		      "close lifecycle reclamation probe");
	for (int i = 0; i < count; i++) {
		int status = parent_waitpid(
			guards[i].pid, "reap lifecycle reclamation probe");

		check(status == AGENT_STATUS_CANCELLED,
		      "reap lifecycle reclamation probe");
	}
}

static TEST_PHASE_NOINLINE struct agent_workflow_lifecycle_key
prove_lifecycle_reclaimed(
	struct agent_workflow_lifecycle_key retired)
{
	struct lifecycle_probe_guard
		guards[WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP];
	int64 deadline = get_mtime() + PHASE_DEADLINE_MS;

	check(retired.id != 0 && retired.generation != 0,
	      "retired lifecycle key is valid");
	for (;;) {
		struct agent_workflow_lifecycle_key replacement;
		int count = 0;
		int found = 0;

		memset(&replacement, 0, sizeof(replacement));
		/* 保持先前候选存活，使最低槽分配继续推进。 */
		while (count < WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP &&
		       try_launch_lifecycle_probe(&guards[count])) {
			if (guards[count].lifecycle_key.id == retired.id) {
				check(guards[count].lifecycle_key.generation >
					      retired.generation,
				      "reused lifecycle id advances generation");
				replacement = guards[count].lifecycle_key;
				found = 1;
			}
			count++;
			if (found)
				break;
		}
		if (count != 0)
			close_lifecycle_probes(guards, count);
		if (found)
			return replacement;
		check(get_mtime() < deadline,
		      "retired lifecycle id was not reusable");
		check(sleep(1) == 0, "yield lifecycle reclamation retry");
	}
}

static TEST_PHASE_NOINLINE void seed_file_pin_object(void)
{
	int fd = open("racedirty", O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "create recycle file pin object");
	check(write(fd, "R", 1) == 1 && close(fd) == 0,
	      "initialize recycle file pin object");
}

static TEST_PHASE_NOINLINE void recycle_workflow_root(
	int command_fd, int report_fd, int lifetime_fd, int pin_gate_read,
	int pin_gate_write)
{
	struct agent_workflow_lifecycle_info matched;
	struct agent_workflow_lifecycle_info after;
	struct recycle_command command;
	struct recycle_report report;
	struct race_event event;
	int events[2];
	int file_probe;
	uint flags = 0;

	memset(&report, 0, sizeof(report));
	parent_read_exact(command_fd, &command, sizeof(command),
			  "receive stale lifecycle key");
	check(close(command_fd) == 0, "close recycle command endpoint");
	snapshot_current_lifecycle(&report.lifecycle);
	memset(&matched, 0, sizeof(matched));
	check(agent_workflow_lifecycle_info(&matched,
				    &report.lifecycle.key) == AGENT_STATUS_OK &&
	      lifecycle_key_equal(matched.key, report.lifecycle.key),
	      "match current recycle lifecycle key");
	seed_file_pin_object();
	check(pipe(events) == 0, "create recycle file pin events");
	check(agent_scope_delegate_fd(pin_gate_read) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(events[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(lifetime_fd) == AGENT_STATUS_OK,
	      "delegate recycle file pin endpoints");
	file_probe = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(file_probe >= 0, "create recycle file pin probe");
	if (file_probe == 0)
		file_pin_probe(pin_gate_read, events[1]);
	check(close(pin_gate_read) == 0 && close(events[1]) == 0 &&
	      close(lifetime_fd) == 0,
	      "close recycle delegated endpoints");
	for (;;) {
		parent_read_exact(events[0], &event, sizeof(event),
				  "receive recycle file pin readiness");
		if (event.kind == EVENT_FILE_PROBE_READY)
			break;
		check(event.kind != EVENT_FAILURE,
		      "recycle file pin probe failed before arm");
	}
	arm_hidden_file_pin(pin_gate_write, events[0], &flags,
			     RECYCLE_PIN_BASE_OBJECTS);
	check((flags & PRIMARY_FILE_PIN) == 0 && !hidden_reader_missed,
	      "recycle hidden fdget remains blocked");
	check(unlink("racedirty") == 0,
	      "remove recycle file pin object before teardown");
	report.scope_id = current_scope();
	/* STALE 是语义比较结果而非调用错误；用两侧成功快照证明旧键比较未改动当前 generation。 */
	for (int i = 0; i < RECLAIM_TARGET_COUNT; i++) {
		memset(&matched, 0, sizeof(matched));
		report.stale_status[i] = agent_workflow_lifecycle_info(
			&matched, &command.stale_key[i]);
		snapshot_current_lifecycle(&after);
		check(lifecycle_key_equal(after.key, report.lifecycle.key),
		      "stale lifecycle comparison preserves current generation");
	}
	write_exact(report_fd, &report, sizeof(report),
		    "report recycle fdget and lifecycle state");
	check(close(report_fd) == 0, "close recycle report endpoint");
	for (;;) {
		struct agent_event event;

		memset(&event, 0, sizeof(event));
		(void)agent_wait(&event, -1);
	}
}

static TEST_PHASE_NOINLINE struct recycle_report run_lifecycle_reuse_round(
	const struct agent_workflow_lifecycle_key
		stale_key[RECLAIM_TARGET_COUNT])
{
	struct recycle_command command;
	struct recycle_report report;
	int commands[2];
	int reports[2];
	int lifetime[2];
	int pin_gate[2];
	int fds[5];
	int status = -1;
	int pid;

	check(pipe(commands) == 0 && pipe(reports) == 0 && pipe(lifetime) == 0 &&
	      pipe(pin_gate) == 0,
	      "create lifecycle reuse pipes");
	fds[0] = commands[0];
	fds[1] = reports[1];
	fds[2] = lifetime[1];
	fds[3] = pin_gate[0];
	fds[4] = pin_gate[1];
	pid = create_workflow_with_fds(fds, 5);
	check(pid >= 0, "create recycled workflow");
	if (pid == 0)
		recycle_workflow_root(commands[0], reports[1], lifetime[1],
				      pin_gate[0], pin_gate[1]);
	check(close(commands[0]) == 0 && close(reports[1]) == 0 &&
	      close(lifetime[1]) == 0 && close(pin_gate[0]) == 0 &&
	      close(pin_gate[1]) == 0, "close recycled child endpoints");
	for (int i = 0; i < RECLAIM_TARGET_COUNT; i++)
		command.stale_key[i] = stale_key[i];
	write_exact(commands[1], &command, sizeof(command),
		    "send stale lifecycle key");
	check(close(commands[1]) == 0, "close recycle command writer");
	parent_read_exact(reports[0], &report, sizeof(report),
			  "receive recycled workflow state");
	check(report.lifecycle.charged == 1 &&
	      report.lifecycle.key.id != 0 &&
	      report.lifecycle.key.generation != 0,
	      "recycled workflow has a distinct lifecycle generation");
	for (int i = 0; i < RECLAIM_TARGET_COUNT; i++) {
		check(!lifecycle_key_equal(report.lifecycle.key, stale_key[i]),
		      "recycled workflow differs from retired lifecycle");
		check(report.stale_status[i] == AGENT_STATUS_STALE,
		      "old lifecycle key is stale in replacement workflow");
	}
	check(agent_workflow_close(report.scope_id) == AGENT_STATUS_OK,
	      "close recycled workflow");
	status = parent_waitpid(pid, "reap recycled workflow");
	check(status == AGENT_STATUS_CANCELLED,
	      "recycled workflow is cancelled");
	parent_read_eof(lifetime[0], "drain recycled lifecycle members");
	check(close(reports[0]) == 0 && close(lifetime[0]) == 0,
	      "close lifecycle reuse pipes");
	return report;
}

static TEST_PHASE_NOINLINE void resource_probe_root(int report_fd)
{
	struct progress_report report;
	struct io_policy_info before;
	struct io_policy_info after;
	struct agent_workflow_lifecycle_info lifecycle;
	int fd;

	memset(io_pressure, 'R', IO_PROBE_BYTES);
	snapshot_current_lifecycle(&lifecycle);
	memset(&before, 0, sizeof(before));
	check(io_policy_info(&before) == 0,
	      "snapshot reusable I/O account");
	check(before.owner ==
		      (IO_POLICY_OWNER_SCOPE_FLAG | current_scope()) &&
	      before.io_class == IO_POLICY_CLASS_CONTROL &&
	      before.leased == 0 && before.debt == 0 &&
	      before.waiters == 0 && before.debt_waiters == 0 &&
	      before.admission_waiters == 0 && before.cache_resident == 0,
	      "replacement workflow starts with a clean resource account");
	check(agent_file_meta_init() == 0,
	      "metadata store reusable after lifecycle churn");
	fd = open("resreuse", O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "allocate reusable file and inode");
	write_exact(fd, io_pressure, IO_PROBE_BYTES, "write reusable file");
	check(close(fd) == 0 && unlink("resreuse") == 0,
	      "release reusable file and inode");
	check(io_policy_info(&after) == 0, "observe reusable I/O account");
	check(after.leased == 0 && after.debt == 0,
	      "replacement workflow has settled I/O account");
	report.scope_id = current_scope();
	report.owner = after.owner;
	report.before_sequence = before.completion_sequence;
	report.after_sequence = after.completion_sequence;
	write_exact(report_fd, &report, sizeof(report),
		    "report reusable resources");
	exit(0);
}

static TEST_PHASE_NOINLINE void run_resource_probe(void)
{
	struct progress_report report;
	int reports[2];
	int lifetime[2];
	int fds[2];
	int status = -1;
	int pid;

	check(pipe(reports) == 0 && pipe(lifetime) == 0,
	      "create resource probe pipes");
	fds[0] = reports[1];
	fds[1] = lifetime[1];
	pid = create_workflow_with_fds(fds, 2);
	check(pid >= 0, "create resource replacement workflow");
	if (pid == 0)
		resource_probe_root(reports[1]);
	check(close(reports[1]) == 0 && close(lifetime[1]) == 0,
	      "close resource probe child endpoints");
	parent_read_exact(reports[0], &report, sizeof(report),
			  "receive resource reuse report");
	check((report.owner & IO_POLICY_OWNER_SCOPE_FLAG) != 0 &&
	      report.after_sequence > report.before_sequence,
	      "replacement workflow uses fresh scoped resources");
	status = parent_waitpid(pid, "reap resource replacement workflow");
	check(status == 0, "validate resource replacement workflow status");
	parent_read_eof(lifetime[0],
			"drain resource replacement workflow lifecycle");
	check(close(reports[0]) == 0 && close(lifetime[0]) == 0,
	      "close resource probe parent endpoints");
}

static TEST_PHASE_NOINLINE struct primary_report run_primary_teardown(
	int teardown_mode,
	const struct agent_workflow_lifecycle_key *required_predecessor)
{
	struct primary_report *identity = &primary_parent.identity;
	struct primary_report *prepared = &primary_parent.prepared;
	struct primary_report *final = &primary_parent.final;
	char arm = 'A';
	char selection;
	int reports[2];
	int lifetime[2];
	int pin_gate[2];
	int arm_gate[2];
	int fds[5];
	int status = -1;
	int primary;
	int64 selection_deadline = get_mtime() + PHASE_DEADLINE_MS;
	uint prepared_required = PRIMARY_IO_PINNED | PRIMARY_PUBLIC_EXIT |
		PRIMARY_CPU_MEMBER |
		PRIMARY_FILE_PIN | PRIMARY_LIFECYCLE_ABI |
		PRIMARY_FRESH_RESOURCES;

	check(teardown_mode == TEARDOWN_FACTORY_CLOSE ||
	      teardown_mode == TEARDOWN_CONTROLLER_EXIT,
	      "valid primary teardown mode");
	check(pipe(reports) == 0 && pipe(lifetime) == 0 &&
	      pipe(pin_gate) == 0 && pipe(arm_gate) == 0,
	      "create primary workflow pipes");
	fds[0] = reports[1];
	fds[1] = lifetime[1];
	fds[2] = pin_gate[0];
	fds[3] = pin_gate[1];
	fds[4] = arm_gate[0];
	for (;;) {
		primary = create_workflow_with_fds(fds, 5);
		check(primary >= 0, "create primary teardown workflow");
		if (primary == 0)
			primary_workflow_root(reports[1], lifetime[1],
					      pin_gate[0], pin_gate[1],
					      arm_gate[0], teardown_mode);
		memset(identity, 0, sizeof(*identity));
		parent_read_exact(reports[0], identity, sizeof(*identity),
				  "receive primary lifecycle identity");
		check(identity->mode == (uint)teardown_mode &&
		      identity->phase == PRIMARY_REPORT_IDENTITY &&
		      identity->scope_id >= 3 && identity->lifecycle.charged == 1,
		      "validate primary lifecycle identity");
		if (required_predecessor == 0 ||
		    (identity->lifecycle.key.id == required_predecessor->id &&
		     identity->lifecycle.key.generation >
			     required_predecessor->generation)) {
			selection = 'S';
			write_exact(arm_gate[1], &selection, 1,
				    "accept primary lifecycle identity");
			break;
		}
		selection = 'R';
		write_exact(arm_gate[1], &selection, 1,
			    "reject primary lifecycle identity");
		status = parent_waitpid(primary,
				       "reap rejected primary lifecycle candidate");
		check(status == 0,
		      "validate rejected primary lifecycle candidate status");
		status = -1;
		check(get_mtime() < selection_deadline,
		      "required primary lifecycle id was not reusable");
		check(sleep(1) == 0, "yield primary lifecycle selection");
	}
	check(close(reports[1]) == 0 && close(lifetime[1]) == 0 &&
	      close(pin_gate[0]) == 0 && close(pin_gate[1]) == 0 &&
	      close(arm_gate[0]) == 0,
	      "close primary child endpoints");
	memset(prepared, 0, sizeof(*prepared));
	parent_read_exact(reports[0], prepared, sizeof(*prepared),
			  "receive primary fresh-resource phase");
	check(prepared->phase == PRIMARY_REPORT_FRESH_RESOURCES &&
	      prepared->scope_id == identity->scope_id &&
	      lifecycle_key_equal(prepared->lifecycle.key,
			  identity->lifecycle.key) &&
	      (prepared->flags &
	       (PRIMARY_LIFECYCLE_ABI | PRIMARY_FRESH_RESOURCES)) ==
		      (PRIMARY_LIFECYCLE_ABI | PRIMARY_FRESH_RESOURCES),
	      "validate primary fresh-resource phase");
	parent_read_exact(reports[0], prepared, sizeof(*prepared),
			  "receive primary members-ready phase");
	check(prepared->phase == PRIMARY_REPORT_MEMBERS_READY &&
	      prepared->scope_id == identity->scope_id &&
	      lifecycle_key_equal(prepared->lifecycle.key,
			  identity->lifecycle.key),
	      "validate primary members-ready phase");
	parent_read_exact(reports[0], prepared, sizeof(*prepared),
			  "receive primary hidden-fdget phase");
	check(prepared->phase == PRIMARY_REPORT_FILE_PINNED &&
	      (prepared->flags & PRIMARY_FILE_PIN) != 0 &&
	      prepared->scope_id == identity->scope_id &&
	      lifecycle_key_equal(prepared->lifecycle.key,
			  identity->lifecycle.key),
	      "validate primary hidden-fdget phase");
	parent_read_exact(reports[0], prepared, sizeof(*prepared),
			  "receive primary prepared state");
	check(prepared->mode == (uint)teardown_mode &&
	      prepared->phase == PRIMARY_REPORT_PREPARED &&
	      (prepared->flags & prepared_required) == prepared_required &&
	      prepared->lifecycle.charged == 1 &&
	      prepared->scope_id == identity->scope_id &&
	      lifecycle_key_equal(prepared->lifecycle.key,
			  identity->lifecycle.key),
	      "all non-final teardown resources are prepared");
	check_factory_self_only_foreign_compare(prepared->lifecycle.key);
	write_exact(arm_gate[1], &arm, 1, "arm final teardown window");
	check(close(arm_gate[1]) == 0, "close final teardown arm writer");
	memset(final, 0, sizeof(*final));
	parent_read_exact(reports[0], final, sizeof(*final),
			  "receive final active teardown snapshot");
	check(final->mode == (uint)teardown_mode &&
	      final->phase == PRIMARY_REPORT_FINAL &&
	      (final->flags & prepared_required) == prepared_required &&
	      final->scope_id == prepared->scope_id &&
	      final->lifecycle.version ==
		      AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      final->lifecycle.struct_size == sizeof(final->lifecycle) &&
	      final->lifecycle.charged == 1 &&
	      final->lifecycle.reserved == 0 &&
	      final->lifecycle.key.reserved == 0 &&
	      lifecycle_key_equal(final->lifecycle.key,
				  prepared->lifecycle.key),
	      "final teardown report preserves prepared identity");
	check_factory_self_only_foreign_compare(final->lifecycle.key);
	if (teardown_mode == TEARDOWN_FACTORY_CLOSE) {
		check(agent_workflow_close(final->scope_id) == AGENT_STATUS_OK,
		      "factory closes primary workflow immediately");
		status = parent_waitpid(primary,
				       "reap factory-closed primary workflow");
		check(status == AGENT_STATUS_CANCELLED,
		      "factory-closed workflow exits as cancelled");
	} else {
		status = parent_waitpid(primary,
				       "reap naturally exiting primary workflow");
		check(status == 0,
		      "controller exits naturally after final snapshot");
	}
	parent_read_eof(lifetime[0],
			"drain primary members and temporary references");
	check(close(reports[0]) == 0 && close(lifetime[0]) == 0,
	      "close primary parent endpoints");
	return *final;
}

static __attribute__((noreturn)) void control_race_hold(void)
{
	for (;;)
		(void)sched_yield();
}

static TEST_PHASE_NOINLINE void close_spawn_racer(void *unused)
{
	char ready = 'R';
	int announced = 0;

	(void)unused;
	for (int round = 0;; round++) {
		int pid;

		check(agent_scope_delegate_fd(close_spawn_lifetime_fd) ==
			      AGENT_STATUS_OK,
		      "delegate close/spawn lifetime");
		pid = (round & 1) != 0 ?
			agent_worker_create(control_worker_image,
					    AGENT_CAP_CONTENT_READ) :
			agent_create_role(AGENT_ROLE_SENTINEL);
		if (pid == 0)
			control_race_hold();
		if (pid > 0 && !announced) {
			write_exact(close_spawn_report_fd, &ready, 1,
				    "report close/spawn publication");
			announced = 1;
		}
		(void)sched_yield();
	}
}

static TEST_PHASE_NOINLINE void close_spawn_root(int report_fd,
						 int lifetime_fd)
{
	uint scope_id = current_scope();

	close_spawn_report_fd = report_fd;
	close_spawn_lifetime_fd = lifetime_fd;
	write_exact(report_fd, &scope_id, sizeof(scope_id),
		    "report close/spawn scope");
	check(thread_create(close_spawn_racer, 0) > 0,
	      "create close/spawn racer");
	control_race_hold();
}

static TEST_PHASE_NOINLINE void run_close_spawn_race(void)
{
	char ready = 0;
	uint scope_id = 0;
	int reports[2];
	int lifetime[2];
	int fds[2];
	int status;
	int pid;

	check(pipe(reports) == 0 && pipe(lifetime) == 0,
	      "create close/spawn race pipes");
	fds[0] = reports[1];
	fds[1] = lifetime[1];
	pid = create_workflow_with_fds(fds, 2);
	check(pid >= 0, "create close/spawn workflow");
	if (pid == 0)
		close_spawn_root(reports[1], lifetime[1]);
	check(close(reports[1]) == 0 && close(lifetime[1]) == 0,
	      "close factory close/spawn writers");
	parent_read_exact(reports[0], &scope_id, sizeof(scope_id),
			  "receive close/spawn scope");
	parent_read_exact(reports[0], &ready, 1,
			  "receive close/spawn publication");
	check(ready == 'R' && agent_workflow_close(scope_id) == AGENT_STATUS_OK,
	      "close workflow during child publication");
	status = parent_waitpid(pid, "reap close/spawn workflow");
	check(status == AGENT_STATUS_CANCELLED,
	      "close/spawn workflow is cancelled");
	parent_read_eof(lifetime[0], "drain close/spawn descendants");
	check(close(reports[0]) == 0 && close(lifetime[0]) == 0,
	      "close close/spawn readers");
}

static TEST_PHASE_NOINLINE void pending_exec_controller(int report_fd,
						 int worker_gate_fd,
						 int controller_gate_fd,
						 int exec_proof_fd)
{
	struct agent_workflow_lifecycle_info lifecycle;
	struct pending_exec_report report;
	char id_text[24];
	char generation_text[24];
	char image_ready = 'I';
	char token;
	int worker;
	int status;

	snapshot_current_lifecycle(&lifecycle);
	check(agent_scope_delegate_fd(worker_gate_fd) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(exec_proof_fd) == AGENT_STATUS_OK,
	      "delegate pending exec endpoints");
	worker = agent_worker_create(control_worker_image,
				     AGENT_CAP_CONTENT_READ);
	check(worker >= 0, "create pending exec worker");
	if (worker == 0) {
		char *argv[] = {
			"workflow_teardown_race_ucore",
			"--pending-exec-escape",
			id_text,
			generation_text,
			0,
		};

		uint64_format(id_text, sizeof(id_text), lifecycle.key.id);
		uint64_format(generation_text, sizeof(generation_text),
			      lifecycle.key.generation);
		read_exact(worker_gate_fd, &token, 1,
			   "release pending exec worker");
		write_exact(exec_proof_fd, &token, 1,
			    "report pending exec publication");
		if (exec("workflow_teardown_race_ucore", argv) < 0)
			exit(91);
		exit(92);
	}
	check(close(exec_proof_fd) == 0,
	      "close pending exec controller proof writer");
	memset(&report, 0, sizeof(report));
	report.worker_pid = worker;
	report.lifecycle_key = lifecycle.key;
	write_exact(report_fd, &report, sizeof(report),
		    "report pending exec identity");
	check(waitpid(worker, &status) == worker && status == 0,
	      "observe pending exec image completion");
	write_exact(report_fd, &image_ready, 1,
		    "report pending exec image readiness");
	read_exact(controller_gate_fd, &token, 1,
		   "release pending exec controller");
	exit(0);
}

static TEST_PHASE_NOINLINE void run_pending_exec_race(void)
{
	struct pending_exec_report report;
	char image_ready = 0;
	char release = 'R';
	char proof = 0;
	int reports[2];
	int worker_gate[2];
	int controller_gate[2];
	int exec_proof[2];
	int controller;
	int status;

	check(pipe(reports) == 0 && pipe(worker_gate) == 0 &&
		      pipe(controller_gate) == 0 && pipe(exec_proof) == 0,
	      "create pending exec race pipes");
	check(agent_scope_delegate_fd(reports[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(worker_gate[0]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(controller_gate[0]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(exec_proof[1]) == AGENT_STATUS_OK,
	      "delegate pending exec controller pipes");
	controller = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(controller >= 0, "create pending exec controller");
	if (controller == 0)
		pending_exec_controller(reports[1], worker_gate[0],
					controller_gate[0], exec_proof[1]);
	check(close(reports[1]) == 0 && close(worker_gate[0]) == 0 &&
	      close(controller_gate[0]) == 0 && close(exec_proof[1]) == 0,
	      "close pending exec child endpoints");
	memset(&report, 0, sizeof(report));
	parent_read_exact(reports[0], &report, sizeof(report),
			  "receive pending exec identity");
	check(report.worker_pid > 0 && report.lifecycle_key.id != 0 &&
	      report.lifecycle_key.generation != 0 &&
	      write(worker_gate[1], &release, 1) == 1,
	      "release pending exec publication");
	parent_read_exact(exec_proof[0], &proof, 1,
			  "receive pending exec publication");
	parent_read_eof(exec_proof[0],
			"successful PUBLIC exec revokes inherited endpoint");
	check(proof == 'R', "pending exec publication proof");
	parent_read_exact(reports[0], &image_ready, 1,
			  "receive pending exec image readiness");
	check(image_ready == 'I', "pending exec image readiness proof");
	check(write(controller_gate[1], &release, 1) == 1,
	      "retire controller after PUBLIC exec commit");
	status = parent_waitpid(controller, "reap pending exec controller");
	check(status == 0, "pending exec controller status");
	parent_read_eof(reports[0], "close pending exec controller report");
	check(sleep(PENDING_EXEC_VERIFY_DELAY) == 0,
	      "wait pending exec escape deadline");
	check(close(reports[0]) == 0 && close(worker_gate[1]) == 0 &&
	      close(controller_gate[1]) == 0 && close(exec_proof[0]) == 0,
	      "close pending exec parent endpoints");
	printf("workflow_teardown_race_ucore: pending_exec_public_ready=1 inherited_fd_revoked=1 controller_cancelled=1\n");
}

static TEST_PHASE_NOINLINE void run_agent_public_exec_transition(void);

static __attribute__((noreturn)) void controller_public_exec_rejection_member(
	int report_fd)
{
	struct agent_workflow_lifecycle_key lifecycle_key;
	char *argv[] = {
		EXEC_PUBLIC_IMAGE,
		"--controller-reject-unexpected",
		0,
	};
	uint scope_id = current_scope();
	char ready = 'R';

	check(context_clear() == AGENT_STATUS_OK,
	      "clear controller Context before rejected exec");
	memset(&exec_transition_record, 0, sizeof(exec_transition_record));
	exec_transition_record.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	exec_transition_record.request_id = 7399;
	exec_transition_record.status = AGENT_STATUS_OK;
	strcpy(exec_transition_record.payload, "exec-controller");
	strcpy(exec_transition_record.result, "armed");
	check(context_push(&exec_transition_record) == AGENT_STATUS_OK &&
	      agent_watch(AGENT_EVENT_MESSAGE, "exec-controller") ==
		      AGENT_STATUS_OK &&
	      agent_heartbeat(7) == AGENT_STATUS_OK,
	      "seed controller identity endpoints before rejected exec");
	memset(&exec_transition_event, 0, sizeof(exec_transition_event));
	exec_transition_event.type = AGENT_EVENT_MESSAGE;
	exec_transition_event.corr_id = 7399;
	strcpy(exec_transition_event.payload, "exec-controller");
	check(agent_wake(getpid(), &exec_transition_event) == AGENT_STATUS_OK,
	      "seed controller event before rejected exec");
	memset(&exec_transition_info_before, 0,
	       sizeof(exec_transition_info_before));
	check(agent_info(&exec_transition_info_before) == 0 &&
	      exec_transition_info_before.is_agent == 1 &&
	      exec_transition_info_before.agent_role ==
		      AGENT_ROLE_ORCHESTRATOR &&
	      exec_transition_info_before.context_base == AGENT_CONTEXT_BASE &&
	      exec_transition_info_before.filesystem_domain == scope_id &&
	      exec_transition_info_before.watch_count != 0 &&
	      exec_transition_info_before.event_queue_count != 0 &&
	      exec_transition_info_before.heartbeat_interval == 7,
	      "snapshot controller state before rejected exec");
	snapshot_current_lifecycle(&exec_transition_lifecycle);
	lifecycle_key = exec_transition_lifecycle.key;
	memset(&exec_transition_io, 0, sizeof(exec_transition_io));
	check(io_policy_info(&exec_transition_io) == 0 &&
	      exec_transition_io.owner ==
		      (IO_POLICY_OWNER_SCOPE_FLAG | scope_id) &&
	      exec_transition_io.io_class == IO_POLICY_CLASS_CONTROL,
	      "snapshot controller resource domain before rejected exec");
	check(exec(EXEC_PUBLIC_IMAGE, argv) < 0,
	      "scope controller rejects PUBLIC exec after prepare");
	memset(&exec_transition_info, 0, sizeof(exec_transition_info));
	memset(&exec_transition_record, 0, sizeof(exec_transition_record));
	memset(&exec_transition_lifecycle, 0,
	       sizeof(exec_transition_lifecycle));
	memset(&exec_transition_io, 0, sizeof(exec_transition_io));
	check(agent_info(&exec_transition_info) == 0 &&
	      exec_transition_info.is_agent == 1 &&
	      exec_transition_info.agent_id ==
		      exec_transition_info_before.agent_id &&
	      exec_transition_info.agent_role == AGENT_ROLE_ORCHESTRATOR &&
	      exec_transition_info.context_base ==
		      exec_transition_info_before.context_base &&
	      exec_transition_info.capability_mask ==
		      exec_transition_info_before.capability_mask &&
	      exec_transition_info.filesystem_domain == scope_id &&
	      exec_transition_info.filesystem_capability_mask ==
		      exec_transition_info_before.filesystem_capability_mask &&
	      exec_transition_info.watch_count != 0 &&
	      exec_transition_info.event_queue_count != 0 &&
	      exec_transition_info.heartbeat_interval == 7 &&
	      context_query(1, &exec_transition_record, 1) == 1 &&
	      exec_transition_record.request_id == 7399 &&
	      agent_workflow_lifecycle_info(&exec_transition_lifecycle,
					    &lifecycle_key) ==
		      AGENT_STATUS_OK &&
	      lifecycle_key_equal(exec_transition_lifecycle.key,
			  lifecycle_key) &&
	      io_policy_info(&exec_transition_io) == 0 &&
	      exec_transition_io.owner ==
		      (IO_POLICY_OWNER_SCOPE_FLAG | scope_id) &&
	      exec_transition_io.io_class == IO_POLICY_CLASS_CONTROL,
	      "rejected PUBLIC exec preserves controller state and lineage");
	/* 两次 exec 转换属于同一可信工作流根。 */
	run_agent_public_exec_transition();
	write_exact(report_fd, &ready, 1,
		    "rejected PUBLIC exec preserves delegated endpoint");
	exit(0);
	__builtin_unreachable();
}

static TEST_PHASE_NOINLINE void run_controller_public_exec_rejection(void)
{
	char ready = 0;
	int reports[2];
	int controller;
	int status;

	check(pipe(reports) == 0,
	      "create controller PUBLIC exec rollback pipe");
	controller = create_workflow_with_fds(&reports[1], 1);
	check(controller >= 0, "create controller PUBLIC exec workflow");
	if (controller == 0)
		controller_public_exec_rejection_member(reports[1]);
	check(close(reports[1]) == 0,
	      "close controller PUBLIC exec parent writer");
	parent_read_exact(reports[0], &ready, 1,
			  "receive controller PUBLIC exec rollback proof");
	status = parent_waitpid(controller,
				"reap controller PUBLIC exec workflow");
	check(ready == 'R' && status == 0,
	      "controller PUBLIC exec rollback status");
	parent_read_eof(reports[0],
			"drain controller PUBLIC exec rollback pipe");
	check(close(reports[0]) == 0,
	      "close controller PUBLIC exec rollback reader");
	printf("workflow_teardown_race_ucore: controller_public_exec_rejected=1 post_prepare_rollback=1 identity_preserved=1 context_preserved=1 fd_preserved=1\n");
}

static __attribute__((noreturn)) void sentinel_public_exec_member(
	struct agent_workflow_lifecycle_key lifecycle_key, int scoped_fd,
	uint scope_id)
{
	char id_text[24];
	char generation_text[24];
	char fd_text[16];
	char scope_text[24];
	char *failed_argv[] = {
		EXEC_PUBLIC_IMAGE,
		io_pressure,
		0,
	};
	char *argv[] = {
		EXEC_PUBLIC_IMAGE,
		"--sentinel-public-exec",
		id_text,
		generation_text,
		fd_text,
		scope_text,
		0,
	};

	memset(&exec_transition_record, 0, sizeof(exec_transition_record));
	exec_transition_record.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	exec_transition_record.request_id = 7401;
	exec_transition_record.status = AGENT_STATUS_OK;
	strcpy(exec_transition_record.payload, "exec-sentinel");
	strcpy(exec_transition_record.result, "armed");
	check(context_push(&exec_transition_record) == AGENT_STATUS_OK &&
	      agent_watch(AGENT_EVENT_MESSAGE, "exec-downgrade") ==
		      AGENT_STATUS_OK &&
	      agent_heartbeat(7) == AGENT_STATUS_OK,
	      "seed identity endpoints before PUBLIC exec");
	memset(&exec_transition_event, 0, sizeof(exec_transition_event));
	exec_transition_event.type = AGENT_EVENT_MESSAGE;
	exec_transition_event.corr_id = 7401;
	strcpy(exec_transition_event.payload, "exec-downgrade");
	check(agent_wake(getpid(), &exec_transition_event) == AGENT_STATUS_OK,
	      "seed queued event before PUBLIC exec");
	/* 以合法镜像和超限 argv 验证镜像回滚。 */
	memset(io_pressure, 'X', AGENT_PAGE_SIZE);
	io_pressure[AGENT_PAGE_SIZE - 1] = 0;
	check(exec(EXEC_PUBLIC_IMAGE, failed_argv) < 0,
	      "oversized argv rejects exec before publication");
	memset(&exec_transition_info, 0, sizeof(exec_transition_info));
	memset(&exec_transition_record, 0, sizeof(exec_transition_record));
	memset(&exec_transition_lifecycle, 0,
	       sizeof(exec_transition_lifecycle));
	check(agent_info(&exec_transition_info) == 0 &&
	      exec_transition_info.is_agent == 1 &&
	      exec_transition_info.agent_role == AGENT_ROLE_SENTINEL &&
	      exec_transition_info.watch_count != 0 &&
	      exec_transition_info.event_queue_count != 0 &&
	      exec_transition_info.heartbeat_interval == 7 &&
	      context_query(1, &exec_transition_record, 1) == 1 &&
	      exec_transition_record.request_id == 7401 &&
	      agent_workflow_lifecycle_info(&exec_transition_lifecycle,
					    &lifecycle_key) ==
		      AGENT_STATUS_OK &&
	      lifecycle_key_equal(exec_transition_lifecycle.key,
			  lifecycle_key),
	      "failed exec preserves Agent identity, endpoints and Context");
	write_exact(scoped_fd, "R", 1,
		    "failed exec preserves delegated scoped endpoint");
	uint64_format(id_text, sizeof(id_text), lifecycle_key.id);
	uint64_format(generation_text, sizeof(generation_text),
		      lifecycle_key.generation);
	decimal_format(fd_text, sizeof(fd_text), scoped_fd);
	uint64_format(scope_text, sizeof(scope_text), scope_id);
	if (exec(EXEC_PUBLIC_IMAGE, argv) < 0)
		exit(91);
	exit(92);
	__builtin_unreachable();
}

static TEST_PHASE_NOINLINE void run_agent_public_exec_transition(void)
{
	char ready = 0;
	uint scope_id = current_scope();
	int scoped[2];
	int sentinel;
	int status;

	check(pipe(scoped) == 0, "create Sentinel scoped exec endpoint");
	check(agent_scope_delegate_fd(scoped[1]) == AGENT_STATUS_OK,
	      "delegate Sentinel scoped exec endpoint");
	sentinel = agent_create_role(AGENT_ROLE_SENTINEL);
	check(sentinel >= 0, "create Sentinel PUBLIC exec member");
	if (sentinel == 0) {
		snapshot_current_lifecycle(&exec_transition_lifecycle);
		sentinel_public_exec_member(exec_transition_lifecycle.key,
					    scoped[1], scope_id);
	}
	check(close(scoped[1]) == 0, "close Sentinel scoped parent writer");
	parent_read_exact(scoped[0], &ready, 1,
			  "receive failed exec endpoint rollback proof");
	status = parent_waitpid(sentinel, "reap Sentinel PUBLIC exec member");
	check(ready == 'R' && status == 0,
	      "Sentinel PUBLIC exec probe status");
	parent_read_eof(scoped[0],
			"successful PUBLIC exec revokes Sentinel endpoint");
	check(close(scoped[0]) == 0, "close Sentinel scoped exec reader");
}

static TEST_PHASE_NOINLINE void controller_exit_spinner(void *unused)
{
	(void)unused;
	control_race_hold();
}

static TEST_PHASE_NOINLINE void parallel_handoff_victim(int report_fd)
{
	struct agent_event event;
	char done = 'D';

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, -1) == AGENT_STATUS_CANCELLED,
	      "root cancels parallel handoff victim");
	write_exact(report_fd, &done, 1, "report parallel handoff victim");
	exit(0);
}

static TEST_PHASE_NOINLINE void parallel_handoff_leaf(int gate_fd,
						      int report_fd)
{
	char release;
	int victim;

	check(agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK,
	      "delegate handoff victim report");
	victim = agent_create_role(AGENT_ROLE_SENTINEL);
	check(victim >= 0, "create parallel handoff victim");
	if (victim == 0)
		parallel_handoff_victim(report_fd);
	write_exact(report_fd, &victim, sizeof(victim),
		    "report parallel handoff victim pid");
	check(thread_create(controller_exit_spinner, 0) > 0,
	      "create leaf controller sibling");
	read_exact(gate_fd, &release, 1, "release leaf controller");
	exit(0);
}

static TEST_PHASE_NOINLINE void parallel_handoff_parent(int gate_fd,
						int report_fd)
{
	char release;
	int leaf;

	check(agent_scope_delegate_fd(gate_fd) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK,
	      "delegate leaf controller endpoints");
	leaf = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(leaf >= 0, "create leaf handoff controller");
	if (leaf == 0)
		parallel_handoff_leaf(gate_fd, report_fd);
	check(thread_create(controller_exit_spinner, 0) > 0,
	      "create parent controller sibling");
	read_exact(gate_fd, &release, 1, "release parent controller");
	exit(0);
}

static TEST_PHASE_NOINLINE void parallel_handoff_root(int result_fd)
{
	struct agent_sched_config config;
	char releases[2] = { 'A', 'B' };
	char done = 0;
	int gate[2];
	int reports[2];
	int parent;
	int victim = -1;
	int status;
	int64 deadline;

	check(pipe(gate) == 0 && pipe(reports) == 0,
	      "create parallel handoff pipes");
	check(agent_scope_delegate_fd(gate[0]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(reports[1]) == AGENT_STATUS_OK,
	      "delegate parent controller endpoints");
	parent = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(parent >= 0, "create parent handoff controller");
	if (parent == 0)
		parallel_handoff_parent(gate[0], reports[1]);
	check(close(gate[0]) == 0 && close(reports[1]) == 0,
	      "close root handoff child endpoints");
	parent_read_exact(reports[0], &victim, sizeof(victim),
			  "receive parallel handoff victim");
	check(victim > 0 && write(gate[1], releases, sizeof(releases)) ==
			      (int)sizeof(releases),
	      "release both controllers");
	status = parent_waitpid(parent, "reap parent handoff controller");
	check(status == 0, "parent handoff controller status");
	memset(&config, 0, sizeof(config));
	config.target_pid = victim;
	config.update_mask = AGENT_SCHED_CONFIG_WEIGHT;
	config.weight = 149;
	deadline = get_mtime() + PHASE_DEADLINE_MS;
	for (;;) {
		status = agent_sched_config(&config);
		if (status == AGENT_STATUS_OK)
			break;
		check(status == AGENT_STATUS_DENIED && get_mtime() < deadline,
		      "parallel handoff reaches root");
		(void)sched_yield();
	}
	check(agent_wait_cancel(victim, "parallel-handoff") == AGENT_STATUS_OK,
	      "cancel parallel handoff victim");
	parent_read_exact(reports[0], &done, 1,
			  "receive parallel handoff completion");
	check(done == 'D', "parallel handoff victim completed");
	write_exact(result_fd, &done, 1, "report parallel handoff result");
	check(close(gate[1]) == 0 && close(reports[0]) == 0,
	      "close parallel handoff root endpoints");
	exit(0);
}

static TEST_PHASE_NOINLINE void run_parallel_handoff_race(void)
{
	char done = 0;
	int reports[2];
	int pid;
	int status;

	check(pipe(reports) == 0, "create parallel handoff result pipe");
	pid = create_workflow_with_fds(&reports[1], 1);
	check(pid >= 0, "create parallel handoff workflow");
	if (pid == 0)
		parallel_handoff_root(reports[1]);
	check(close(reports[1]) == 0, "close parallel handoff result writer");
	parent_read_exact(reports[0], &done, 1,
			  "receive parallel handoff result");
	status = parent_waitpid(pid, "reap parallel handoff workflow");
	check(done == 'D' && status == 0, "parallel handoff workflow status");
	check(close(reports[0]) == 0, "close parallel handoff result reader");
}

int main(int argc, char **argv)
{
	if (argc > 5 && strcmp(argv[1], "--sentinel-public-exec") == 0) {
		sentinel_public_exec_probe(argv[2], argv[3], argv[4], argv[5]);
		return 0;
	}
	if (argc > 1 &&
	    strcmp(argv[1], "--controller-reject-unexpected") == 0) {
		printf("workflow_teardown_race_ucore: check failed: controller PUBLIC exec unexpectedly committed\n");
		return 93;
	}
	if (argc > 3 && strcmp(argv[1], "--pending-exec-escape") == 0) {
		struct agent_workflow_lifecycle_info lifecycle;
		struct agent_workflow_lifecycle_key expected;
		uint64 id = 0;
		uint64 generation = 0;
		int descendant;

		check(uint64_parse(argv[2], &id) == 0 && id > 0 && id <= ~0U &&
		      uint64_parse(argv[3], &generation) == 0 && generation > 0,
		      "parse pending exec lifecycle");
		memset(&expected, 0, sizeof(expected));
		expected.id = (uint)id;
		expected.generation = generation;
		memset(&lifecycle, 0, sizeof(lifecycle));
		check(public_identity_is_downgraded() &&
		      agent_workflow_lifecycle_info(&lifecycle, &expected) ==
			      AGENT_STATUS_OK &&
		      lifecycle.charged == 1 &&
		      lifecycle_key_equal(lifecycle.key, expected),
		      "pending exec preserves teardown lineage");
		descendant = fork();
		check(descendant >= 0, "publish pending exec PUBLIC descendant");
		if (descendant == 0) {
			(void)sleep(PENDING_EXEC_ESCAPE_DELAY);
			(void)write(1, PENDING_EXEC_ESCAPE_MARKER,
				    sizeof(PENDING_EXEC_ESCAPE_MARKER) - 1);
			return 94;
		}
		check(write(1, PENDING_EXEC_READY_MARKER,
			    sizeof(PENDING_EXEC_READY_MARKER) - 1) ==
			      (int)sizeof(PENDING_EXEC_READY_MARKER) - 1,
		      "publish pending exec PUBLIC readiness");
		return 0;
	}
	exec_manifest_worker_image("workflow_teardown_race_ucore",
				   control_worker_image);
	printf("workflow_teardown_race_ucore: combined teardown test\n");
	check_factory_lifecycle_view(0);
	run_controller_public_exec_rejection();
	run_close_spawn_race();
	run_pending_exec_race();
	run_parallel_handoff_race();
	teardown_main.factory_close =
		run_primary_teardown(TEARDOWN_FACTORY_CLOSE, 0);
	teardown_main.retired[0] = teardown_main.factory_close.lifecycle.key;
	teardown_main.natural_exit = run_primary_teardown(
		TEARDOWN_CONTROLLER_EXIT, &teardown_main.retired[0]);
	teardown_main.factory_replacement =
		teardown_main.natural_exit.lifecycle.key;
	check(teardown_main.factory_replacement.id ==
		      teardown_main.retired[0].id &&
	      teardown_main.factory_replacement.generation >
		      teardown_main.retired[0].generation,
	      "factory-close lifecycle receives final reclamation proof");
	teardown_main.retired[1] = teardown_main.natural_exit.lifecycle.key;
	teardown_main.natural_replacement =
		prove_lifecycle_reclaimed(teardown_main.retired[1]);
	check(teardown_main.natural_replacement.id ==
		      teardown_main.retired[1].id &&
	      teardown_main.natural_replacement.generation >
		      teardown_main.retired[1].generation,
	      "natural-exit lifecycle receives final reclamation proof");
	printf("workflow_teardown_race_ucore: lifecycle_abi_prefix=1 bad_param_no_write=1 factory_charged=1 self_only_stale=1\n");
	printf("workflow_teardown_race_ucore: factory_close=1 final_snapshot=1 public_lineage=1 lifecycle_reclaimed=1\n");
	printf("workflow_teardown_race_ucore: natural_exit=1 final_snapshot=1 lifecycle_reclaimed=1\n");
	printf("workflow_teardown_race_ucore: spawn_descendants_drained=1 public_exec_escape_blocked=1 nested_multithread_handoff=1 root_control_reached=1\n");

	for (int round = 0; round < LIFECYCLE_REUSE_MIN_ROUNDS; round++)
		(void)run_lifecycle_reuse_round(teardown_main.retired);
	printf("workflow_teardown_race_ucore: blocked_fdget_cycles=%d domain_cap=%d global_reserved_cap=%d\n",
	       LIFECYCLE_REUSE_MIN_ROUNDS,
	       WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP,
	       WORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP);
	printf("workflow_teardown_race_ucore: blocked_fdget_capacity_crossed=1 file_objects_reclaimed=1\n");
	printf("workflow_teardown_race_ucore: lifecycle_id_reused=1 generation_advanced=1 factory_reclaimed=1 natural_reclaimed=1 stale_keys_rejected=1\n");
	run_resource_probe();
	check_factory_lifecycle_view(0);
	printf("workflow_teardown_race_ucore: fresh_account=1 io_debt=0 cache=0 inode_reusable=1\n");
	printf("workflow_teardown_race_ucore: parent passed\n");
	return 0;
}
