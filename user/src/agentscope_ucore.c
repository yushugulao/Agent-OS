#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TARGET_FILE "scopeobj"
#define VOLATILE_FILE "scopevolatile"
#define COMMON_FID  6101
#define VOLATILE_FID 6102
#define COMMON_ACTION_REQUEST 6201
#define SCOPE_LIFECYCLE_ROUNDS 132
#define META_RACE_WRITERS 3
#define META_RACE_FILES 4

struct scope_command {
	int operation;
	uint64 arg0;
	uint64 arg1;
};

struct scope_reply {
	int ok;
	uint scope_id;
	uint64 value0;
	uint64 value1;
};

static struct agent_audit_record scope_audit_records[AGENT_AUDIT_MAX_RECORDS];
static struct agent_file_query_result scope_query_result;
static struct agent_file_query_result meta_race_result;

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentscope_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void write_exact(int fd, const void *buffer, size_t size,
			const char *message)
{
	const char *cursor = buffer;

	while (size > 0) {
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

	while (size > 0) {
		ssize_t received = read(fd, cursor, size);

		check(received > 0, message);
		cursor += received;
		size -= received;
	}
}

static uint current_scope(void)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "agent info");
	check(info.is_agent == 1, "workflow root is agent");
	check(info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "workflow root role");
	check(info.filesystem_domain >= 3, "trusted dynamic scope");
	return (uint)info.filesystem_domain;
}

static void send_reply(int fd, uint scope_id, uint64 value0, uint64 value1)
{
	struct scope_reply reply;

	reply.ok = 1;
	reply.scope_id = scope_id;
	reply.value0 = value0;
	reply.value1 = value1;
	write_exact(fd, &reply, sizeof(reply), "send scope reply");
}

static __attribute__((noinline)) void
create_scoped_object(const char *contents, const char *summary)
{
	struct agent_file_meta meta;
	int fd;

	fd = open(TARGET_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create scoped file");
	check(write(fd, contents, strlen(contents)) ==
		      (ssize_t)strlen(contents),
	      "write scoped file");
	check(close(fd) == 0, "close scoped file");

	memset(&meta, 0, sizeof(meta));
	meta.fid = COMMON_FID;
	strcpy(meta.physical_name, TARGET_FILE);
	strcpy(meta.logical_path, "workflow/shared");
	strcpy(meta.project, "scope-project");
	strcpy(meta.workflow, "scope-test");
	strcpy(meta.run_id, "same-run");
	strcpy(meta.stage, "shared");
	strcpy(meta.kind, "artifact");
	strcpy(meta.status, "ready");
	strcpy(meta.summary, summary);
	check(agent_file_meta_set(&meta) == 0, "set scoped metadata");
}

static void query_scoped_object(const char *summary, const char *status)
{
	struct agent_file_query query;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "scope-project");
	strcpy(query.workflow, "scope-test");
	strcpy(query.run_id, "same-run");
	strcpy(query.stage, "shared");
	strcpy(query.kind, "artifact");
	strcpy(query.status, status);
	memset(&scope_query_result, 0, sizeof(scope_query_result));
	check(agent_file_query(&query, &scope_query_result) == 1,
	      "scoped query count");
	check(scope_query_result.total_hits == 1 &&
	      scope_query_result.returned == 1,
	      "scoped query has one object");
	check(scope_query_result.hits[0].fid == COMMON_FID,
	      "scoped query fid");
	check(strcmp(scope_query_result.hits[0].physical_name, TARGET_FILE) == 0,
	      "scoped query physical name");
	check(strcmp(scope_query_result.hits[0].summary, summary) == 0,
	      "scoped query own summary");
}

static __attribute__((noinline)) void create_volatile_object(void)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = VOLATILE_FID;
	strcpy(meta.physical_name, VOLATILE_FILE);
	strcpy(meta.logical_path, "workflow/volatile");
	strcpy(meta.project, "scope-project");
	strcpy(meta.workflow, "scope-test");
	strcpy(meta.run_id, "volatile-run");
	strcpy(meta.stage, "volatile");
	strcpy(meta.kind, "artifact");
	strcpy(meta.status, "memory-only");
	strcpy(meta.summary, "scope-B-volatile");
	check(agent_file_meta_set(&meta) == 0,
	      "create non-persistent scoped metadata");
}

static __attribute__((noinline)) void query_volatile_object(void)
{
	struct agent_file_query query;
	struct agent_file_query_result result;

	memset(&query, 0, sizeof(query));
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.physical_name, VOLATILE_FILE);
	memset(&result, 0, sizeof(result));
	check(agent_file_query(&query, &result) == 1,
	      "volatile scoped query count");
	check(result.total_hits == 1 && result.returned == 1 &&
	      result.hits[0].fid == VOLATILE_FID,
	      "volatile scoped metadata retained");
}

static __attribute__((noinline)) void query_scoped_object_missing(void)
{
	struct agent_file_query query;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "scope-project");
	strcpy(query.workflow, "scope-test");
	strcpy(query.run_id, "same-run");
	strcpy(query.stage, "shared");
	strcpy(query.kind, "artifact");
	strcpy(query.status, "ready");
	memset(&scope_query_result, 0, sizeof(scope_query_result));
	check(agent_file_query(&query, &scope_query_result) == 0,
	      "foreign metadata is not visible");
	check(scope_query_result.total_hits == 0 &&
	      scope_query_result.returned == 0,
	      "foreign query result is empty");
}

static void read_scoped_object(const char *contents)
{
	char buffer[16];
	ssize_t received;
	int fd;

	memset(buffer, 0, sizeof(buffer));
	fd = open(TARGET_FILE, O_RDONLY);
	check(fd >= 0, "open own scoped file");
	received = read(fd, buffer, sizeof(buffer) - 1);
	check(received == (ssize_t)strlen(contents), "read own scoped file");
	check(close(fd) == 0, "close own scoped file");
	buffer[received] = 0;
	check(strcmp(buffer, contents) == 0, "own scoped file contents");
}

static __attribute__((noinline)) void same_scope_probe(uint expected_scope)
{
	check(current_scope() == expected_scope, "child inherited scope");
	check(agent_workflow_create(AGENT_ROLE_ORCHESTRATOR) ==
		      AGENT_STATUS_DENIED,
	      "workflow factory authority is not inherited");
	read_scoped_object("alpha");
	query_scoped_object("scope-A", "ready");
	exit(0);
}

static void metadata_race_name(char *name, char group, int index)
{
	name[0] = 'm';
	name[1] = 'r';
	name[2] = group;
	name[3] = '0' + index;
	name[4] = 0;
}

static __attribute__((noinline)) void
metadata_race_writer(char group, int start_fd)
{
	struct agent_info info;
	struct agent_file_meta meta;
	char name[8];
	char run_id[] = "race-a";
	char token;

	read_exact(start_fd, &token, 1, "wait metadata race start");
	run_id[5] = group;
	for (int i = 0; i < META_RACE_FILES; i++) {
		int fd;

		metadata_race_name(name, group, i);
		fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);
		check(fd >= 0, "create metadata race file");
		check(write(fd, &group, 1) == 1, "write metadata race file");
		check(close(fd) == 0, "close metadata race file");
		memset(&meta, 0, sizeof(meta));
		meta.fid = 7000 + (group - 'a') * 16 + i;
		strcpy(meta.physical_name, name);
		strcpy(meta.logical_path, name);
		strcpy(meta.project, "txn-race");
		strcpy(meta.workflow, "metadata-transaction");
		strcpy(meta.run_id, run_id);
		strcpy(meta.stage, "parallel");
		strcpy(meta.kind, "artifact");
		strcpy(meta.status, "committed");
		strcpy(meta.summary, "serialized metadata writer");
		meta.flags = AGENT_FILE_META_F_PERSIST;
		check(agent_file_meta_set(&meta) == 0,
		      "commit concurrent metadata");
	}
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "read metadata contention count");
	exit(info.metadata_txn_wait_count > 0 ? 1 : 0);
}

static __attribute__((noinline)) void
metadata_race_query(const char *run_id)
{
	struct agent_file_query query;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "txn-race");
	strcpy(query.workflow, "metadata-transaction");
	strcpy(query.run_id, run_id);
	strcpy(query.stage, "parallel");
	strcpy(query.status, "committed");
	memset(&meta_race_result, 0, sizeof(meta_race_result));
	check(agent_file_query(&query, &meta_race_result) == META_RACE_FILES,
	      "concurrent metadata query count");
	check(meta_race_result.total_hits == META_RACE_FILES &&
		      meta_race_result.returned == META_RACE_FILES,
	      "concurrent metadata transaction retained every record");
}

static __attribute__((noinline)) void check_metadata_transactions(void)
{
	struct agent_file_meta meta;
	char name[8];
	char start[META_RACE_WRITERS] = { 'a', 'b', 'c' };
	int start_pipe[2];
	int children[META_RACE_WRITERS];
	int contentions = 0;
	int status;

	check(pipe(start_pipe) == 0, "create metadata race pipe");
	for (int i = 0; i < META_RACE_WRITERS; i++) {
		children[i] = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
		check(children[i] >= 0, "create metadata race writer");
		if (children[i] == 0)
			metadata_race_writer('a' + i, start_pipe[0]);
	}
	write_exact(start_pipe[1], start, sizeof(start),
		    "release metadata race writers");
	for (int i = 0; i < META_RACE_WRITERS; i++) {
		status = -1;
		check(waitpid(children[i], &status) == children[i],
		      "wait metadata race writer");
		check(status == 0 || status == 1,
		      "metadata race writer status");
		contentions += status;
	}
	check(contentions >= META_RACE_WRITERS - 1,
	      "metadata writers contend on transaction gate");
	check(close(start_pipe[0]) == 0 && close(start_pipe[1]) == 0,
	      "close metadata race pipe");
	check(agent_file_meta_init() == 0,
	      "reload concurrent metadata transactions");
	metadata_race_query("race-a");
	metadata_race_query("race-b");
	metadata_race_query("race-c");
	for (int group = 0; group < META_RACE_WRITERS; group++)
		for (int i = 0; i < META_RACE_FILES; i++) {
			metadata_race_name(name, 'a' + group, i);
			memset(&meta, 0, sizeof(meta));
			meta.fid = 7000 + group * 16 + i;
			meta.flags = AGENT_FILE_META_F_DELETE;
			check(agent_file_meta_set(&meta) == 0,
			      "delete metadata race record");
			check(unlink(name) == 0, "delete metadata race file");
		}
}

static __attribute__((noinline)) int create_quota_files(char group, int max)
{
	char name[] = "qs000";
	int created = 0;

	name[1] = group;
	for (int i = 0; i < max; i++) {
		int fd;

		name[2] = '0' + (i / 100) % 10;
		name[3] = '0' + (i / 10) % 10;
		name[4] = '0' + i % 10;
		fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);
		if (fd < 0)
			break;
		check(close(fd) == 0, "close quota object");
		created++;
	}
	return created;
}

static __attribute__((noinline)) void check_scope_storage_quota(void)
{
	int first;
	int second;
	int pid;

	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create first quota writer");
	if (pid == 0)
		exit(create_quota_files('a', 120));
	check(waitpid(pid, &first) == pid, "wait first quota writer");
	check(first == 120, "first writer consumes part of scope quota");

	pid = fork();
	check(pid >= 0, "create public quota writer");
	if (pid == 0)
		exit(create_quota_files('b', 70));
	check(waitpid(pid, &second) == pid, "wait public quota writer");
	check(second == 70,
	      "public principal is independent of workflow resource domain");
}

static __attribute__((noinline)) void
run_scoped_action(int receive_own_event)
{
	struct agent_event event;
	struct agent_op op;
	struct agent_result result;

	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_ACTION_COMMIT;
	op.request_id = COMMON_ACTION_REQUEST;
	strcpy(op.payload,
	       "label=shared;run_id=same-run;namespace=scope-project");
	memset(&result, 0, sizeof(result));
	check(agent_run(&op, &result, 1, 0) == 1,
	      "first scoped action call");
	check(result.status == AGENT_STATUS_OK, "first scoped action succeeds");
	memset(&result, 0, sizeof(result));
	check(agent_run(&op, &result, 1, 0) == 1,
	      "duplicate scoped action call");
	check(result.status == AGENT_STATUS_DUPLICATE,
	      "second scoped action is duplicate");
	query_scoped_object("action completed", "ok");
	if (!receive_own_event)
		return;
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 0) == AGENT_STATUS_OK,
	      "own workflow action event delivered");
	check(event.type == AGENT_EVENT_JOB_DONE,
	      "own workflow action event type");
	check(event.source_pid == getpid() &&
		      event.corr_id == COMMON_ACTION_REQUEST,
	      "own workflow action event identity");
	check(strcmp(event.payload,
		     "state=ok;label=shared;run_id=same-run;action=action_commit") ==
		      0,
	      "own workflow action event payload");
	check(agent_unwatch(AGENT_EVENT_JOB_DONE, "action=action_commit") == 1,
	      "remove scoped action watcher");
}

static __attribute__((noinline)) void
check_foreign_audit_hidden(int foreign_pid)
{
	struct agent_audit_filter filter;
	int count;

	check(foreign_pid > 0, "foreign audit pid");
	memset(scope_audit_records, 0, sizeof(scope_audit_records));
	count = agent_audit_snapshot(scope_audit_records,
				     AGENT_AUDIT_MAX_RECORDS);
	check(count > 0, "own scope audit snapshot");
	for (int i = 0; i < count; i++)
		check(scope_audit_records[i].pid != foreign_pid,
		      "foreign audit absent from snapshot");

	memset(&filter, 0, sizeof(filter));
	filter.flags = AGENT_AUDIT_FILTER_PID |
		       AGENT_AUDIT_FILTER_TOOL_ID;
	filter.pid = foreign_pid;
	filter.tool_id = AGENT_TOOL_ACTION_COMMIT;
	memset(scope_audit_records, 0, sizeof(scope_audit_records));
	check(agent_audit_query(&filter, scope_audit_records,
				AGENT_AUDIT_MAX_RECORDS) == 0,
	      "foreign action audit query is empty");
}

static __attribute__((noinline)) void
check_foreign_lease(uint64 lease_id, uint64 base_version)
{
	struct agent_file_edit_state state;

	check(lease_id != 0, "foreign lease id");
	memset(&state, 0, sizeof(state));
	check(agent_file_edit_commit(lease_id, base_version, &state) ==
		      AGENT_STATUS_NOT_FOUND,
	      "foreign lease commit hidden");
	check(agent_file_edit_abort(lease_id) == AGENT_STATUS_NOT_FOUND,
	      "foreign lease abort hidden");
	memset(&state, 0, sizeof(state));
	check(agent_file_edit_begin(TARGET_FILE, 0, 200, &state) == 0,
	      "same-name local lease succeeds");
	check(state.lease_id != 0 && state.lease_id != lease_id,
	      "local lease identity isolated");
	check(agent_file_edit_abort(state.lease_id) == 0,
	      "abort local isolated lease");
}

static void run_scope_root(char identity, int command_fd, int reply_fd,
			   int peer_pid)
{
	const char *contents = identity == 'A' ? "alpha" : "bravo";
	const char *summary = identity == 'A' ? "scope-A" : "scope-B";
	struct agent_file_edit_state lease_state;
	uint scope_id = current_scope();

	check(agent_workflow_create(AGENT_ROLE_ORCHESTRATOR) ==
		      AGENT_STATUS_DENIED,
	      "workflow root cannot mint another workflow");
	memset(&lease_state, 0, sizeof(lease_state));
	send_reply(reply_fd, scope_id, 0, 0);
	for (;;) {
		struct scope_command command;
		uint64 value0 = 0;
		uint64 value1 = 0;

		memset(&command, 0, sizeof(command));
		read_exact(command_fd, &command, sizeof(command),
			   "receive scope command");
		if (command.operation == 'N') {
			int fd = open(TARGET_FILE, O_RDONLY);

			check(fd < 0, "foreign file is not visible");
			query_scoped_object_missing();
		} else if (command.operation == 'C') {
			create_scoped_object(contents, summary);
			query_scoped_object(summary, "ready");
		} else if (command.operation == 'V') {
			read_scoped_object(contents);
			query_scoped_object(summary, "ready");
		} else if (command.operation == 'D') {
			check(identity == 'B', "volatile metadata owner");
			create_volatile_object();
			query_volatile_object();
		} else if (command.operation == 'M') {
			check(identity == 'A', "scope-local reload owner");
			check(agent_file_meta_init() == 0,
			      "reload only caller metadata scope");
		} else if (command.operation == 'E') {
			check(identity == 'B', "volatile metadata verifier");
			query_volatile_object();
		} else if (command.operation == 'S') {
			int status = 0;
			int pid;

			check(identity == 'A', "same-scope command owner");
			pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
			check(pid >= 0, "create same-scope child");
			if (pid == 0)
				same_scope_probe(scope_id);
			check(waitpid(pid, &status) == pid, "wait same-scope child");
			check(status == 0, "same-scope child status");
		} else if (command.operation == 'T') {
			check(identity == 'A', "storage quota command owner");
			check_scope_storage_quota();
		} else if (command.operation == 'Y') {
			check(identity == 'A', "metadata transaction owner");
			check_metadata_transactions();
		} else if (command.operation == 'W') {
			check(identity == 'B', "scoped watcher owner");
			check(agent_watch(AGENT_EVENT_JOB_DONE,
					  "action=action_commit") == 0,
			      "install scoped action watcher");
		} else if (command.operation == 'A') {
			run_scoped_action(identity == 'B');
		} else if (command.operation == 'U') {
			struct agent_event event;

			check(identity == 'B', "audit isolation owner");
			memset(&event, 0, sizeof(event));
			check(agent_wait(&event, 0) == AGENT_STATUS_TIMEOUT,
			      "foreign workflow event not delivered");
			check_foreign_audit_hidden(peer_pid);
		} else if (command.operation == 'I') {
			struct agent_event event;

			check(identity == 'B' && peer_pid > 0,
			      "cross-scope IPC probe owner");
			check(agent_route_config(peer_pid, getpid(),
					 AGENT_IPC_EVENT_MESSAGE,
					 AGENT_IPC_ROUTE_GRANT) ==
				      AGENT_STATUS_DENIED,
			      "target consent cannot cross workflow scope");
			memset(&event, 0, sizeof(event));
			event.type = AGENT_EVENT_MESSAGE;
			event.corr_id = 6301;
			strcpy(event.payload, "cross-scope-message");
			check(agent_wake(peer_pid, &event) == AGENT_STATUS_DENIED,
			      "message delivery cannot cross workflow scope");
		} else if (command.operation == 'L') {
			check(identity == 'A', "lease owner scope");
			memset(&lease_state, 0, sizeof(lease_state));
			check(agent_file_edit_begin(TARGET_FILE, 0, 200,
						    &lease_state) == 0,
			      "begin scoped lease");
			value0 = lease_state.lease_id;
			value1 = lease_state.base_version;
		} else if (command.operation == 'X') {
			check(identity == 'B', "foreign lease probe scope");
			check_foreign_lease(command.arg0, command.arg1);
		} else if (command.operation == 'R') {
			check(identity == 'A' && lease_state.lease_id != 0,
			      "release scoped lease owner");
			check(agent_file_edit_abort(lease_state.lease_id) == 0,
			      "release scoped lease");
			memset(&lease_state, 0, sizeof(lease_state));
		} else if (command.operation == 'Q') {
			send_reply(reply_fd, scope_id, 0, 0);
			exit(0);
		} else {
			check(0, "unknown scope command");
		}
		send_reply(reply_fd, scope_id, value0, value1);
	}
}

static struct scope_reply receive_reply(int fd, const char *message)
{
	struct scope_reply reply;

	read_exact(fd, &reply, sizeof(reply), message);
	check(reply.ok == 1 && reply.scope_id >= 3, message);
	return reply;
}

static struct scope_reply run_command(int command_fd, int reply_fd,
				      int operation, uint64 arg0, uint64 arg1,
				      uint expected_scope, const char *message)
{
	struct scope_command command;
	struct scope_reply reply;

	memset(&command, 0, sizeof(command));
	command.operation = operation;
	command.arg0 = arg0;
	command.arg1 = arg1;
	write_exact(command_fd, &command, sizeof(command), message);
	reply = receive_reply(reply_fd, message);
	check(reply.scope_id == expected_scope, message);
	return reply;
}

static __attribute__((noinline)) void scope_lifecycle_child(void)
{
	int fd = open("scopegc", O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "create lifecycle object");
	check(write(fd, "x", 1) == 1, "write lifecycle object");
	check(close(fd) == 0, "close lifecycle object");
	exit(0);
}

static void check_scope_lifecycle(void)
{
	for (int i = 0; i < SCOPE_LIFECYCLE_ROUNDS; i++) {
		int status = 0;
		int pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);

		check(pid >= 0, "allocate recycled workflow scope");
		if (pid == 0)
			scope_lifecycle_child();
		check(waitpid(pid, &status) == pid,
		      "wait recycled workflow scope");
		check(status == 0, "recycled workflow scope status");
	}
}

static void check_scope_capacity_reservation(void)
{
	int children[3];
	int delegated_pipe[2];
	int replacement;
	int status = 0;

	for (int i = 0; i < 3; i++) {
		children[i] = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
		check(children[i] >= 0, "admit reserved workflow partition");
		if (children[i] == 0) {
			sleep(10);
			exit(0);
		}
	}
	check(pipe(delegated_pipe) == 0, "create transactional delegation pipe");
	check(agent_scope_delegate_fd(delegated_pipe[0]) == AGENT_STATUS_OK,
	      "authorize one boundary delegation attempt");
	check(agent_workflow_create(AGENT_ROLE_ORCHESTRATOR) < 0,
	      "reject workflow without a full object-table partition");
	check(waitpid(children[0], &status) == children[0],
	      "wait first capacity workflow");
	check(status == 0, "first capacity workflow status");
	replacement = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(replacement >= 0, "reuse released workflow partition");
	if (replacement == 0) {
		check(close(delegated_pipe[0]) < 0,
		      "failed admission consumes delegation ticket");
		exit(0);
	}
	status = -1;
	check(waitpid(replacement, &status) == replacement,
	      "wait replacement workflow");
	check(status == 0, "replacement workflow status");
	for (int i = 1; i < 3; i++) {
		status = -1;
		check(waitpid(children[i], &status) == children[i],
		      "wait capacity workflow");
		check(status == 0, "capacity workflow status");
	}
	check(close(delegated_pipe[0]) == 0 &&
	      close(delegated_pipe[1]) == 0,
	      "close transactional delegation pipe");
}

int main(void)
{
	struct scope_reply ready_a;
	struct scope_reply ready_b;
	struct scope_reply lease_a;
	int a_command[2];
	int a_reply[2];
	int b_command[2];
	int b_reply[2];
	int pid_a;
	int pid_b;
	int status = 0;

	printf("agentscope_ucore: workflow scope isolation test\n");
	check(pipe(a_command) == 0 && pipe(a_reply) == 0,
	      "create scope A pipes");
	check(pipe(b_command) == 0 && pipe(b_reply) == 0,
	      "create scope B pipes");

	check(agent_scope_delegate_fd(a_command[0]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(a_reply[1]) == AGENT_STATUS_OK,
	      "delegate scope A pipe endpoints");
	pid_a = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid_a >= 0, "create fresh scope A");
	if (pid_a == 0) {
		check(close(b_command[0]) < 0,
		      "undelegated scope B pipe is not inherited");
		run_scope_root('A', a_command[0], a_reply[1], -1);
	}
	check(agent_scope_delegate_fd(b_command[0]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(b_reply[1]) == AGENT_STATUS_OK,
	      "delegate scope B pipe endpoints");
	pid_b = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid_b >= 0, "create fresh scope B");
	if (pid_b == 0) {
		check(close(a_command[0]) < 0,
		      "undelegated scope A pipe is not inherited");
		run_scope_root('B', b_command[0], b_reply[1], pid_a);
	}

	ready_a = receive_reply(a_reply[0], "scope A ready");
	ready_b = receive_reply(b_reply[0], "scope B ready");
	check(ready_a.scope_id != ready_b.scope_id,
	      "fresh workflows have distinct scopes");

	run_command(a_command[1], a_reply[0], 'C', 0, 0, ready_a.scope_id,
		    "scope A creates object");
	run_command(b_command[1], b_reply[0], 'N', 0, 0, ready_b.scope_id,
		    "scope B cannot see scope A");
	run_command(b_command[1], b_reply[0], 'C', 0, 0, ready_b.scope_id,
		    "scope B creates same-name object");
	run_command(a_command[1], a_reply[0], 'V', 0, 0, ready_a.scope_id,
		    "scope A retains own object");
	run_command(b_command[1], b_reply[0], 'V', 0, 0, ready_b.scope_id,
		    "scope B retains own object");
	run_command(b_command[1], b_reply[0], 'D', 0, 0, ready_b.scope_id,
		    "scope B creates volatile metadata");
	run_command(a_command[1], a_reply[0], 'M', 0, 0, ready_a.scope_id,
		    "scope A reloads own metadata");
	run_command(b_command[1], b_reply[0], 'E', 0, 0, ready_b.scope_id,
		    "scope A reload preserves scope B volatile metadata");
	run_command(b_command[1], b_reply[0], 'I', 0, 0, ready_b.scope_id,
		    "cross-scope IPC isolation");
	run_command(a_command[1], a_reply[0], 'S', 0, 0, ready_a.scope_id,
		    "same-scope child collaboration");
	run_command(a_command[1], a_reply[0], 'Y', 0, 0, ready_a.scope_id,
		    "serialize concurrent metadata transactions");
	run_command(a_command[1], a_reply[0], 'T', 0, 0, ready_a.scope_id,
		    "same-scope aggregate storage quota");
	printf("agentscope_ucore: cross_scope_isolation=1\n");
	printf("agentscope_ucore: ipc_scope_isolation=1\n");
	printf("agentscope_ucore: same_scope_collaboration=1\n");
	printf("agentscope_ucore: metadata_transactions=1\n");
	printf("agentscope_ucore: scope_storage_quota=1\n");
	printf("agentscope_ucore: scope_reload_isolation=1\n");

	run_command(b_command[1], b_reply[0], 'W', 0, 0, ready_b.scope_id,
		    "scope B installs watcher");
	run_command(a_command[1], a_reply[0], 'A', 0, 0, ready_a.scope_id,
		    "scope A action history");
	run_command(b_command[1], b_reply[0], 'U', 0, 0, ready_b.scope_id,
		    "scope B audit and event isolation");
	run_command(b_command[1], b_reply[0], 'A', 0, 0, ready_b.scope_id,
		    "scope B independent action history");
	printf("agentscope_ucore: action_scope_isolation=1\n");
	printf("agentscope_ucore: audit_event_scope_isolation=1\n");

	lease_a = run_command(a_command[1], a_reply[0], 'L', 0, 0,
			      ready_a.scope_id, "scope A begins lease");
	check(lease_a.value0 != 0, "scope A lease reply");
	run_command(b_command[1], b_reply[0], 'X', lease_a.value0,
		    lease_a.value1, ready_b.scope_id,
		    "scope B cannot use scope A lease");
	run_command(a_command[1], a_reply[0], 'R', 0, 0, ready_a.scope_id,
		    "scope A releases lease");
	printf("agentscope_ucore: lease_scope_isolation=1\n");

	run_command(a_command[1], a_reply[0], 'Q', 0, 0, ready_a.scope_id,
		    "stop scope A");
	run_command(b_command[1], b_reply[0], 'Q', 0, 0, ready_b.scope_id,
		    "stop scope B");
	check(waitpid(pid_a, &status) == pid_a, "wait scope A");
	check(status == 0, "scope A status");
	check(waitpid(pid_b, &status) == pid_b, "wait scope B");
	check(status == 0, "scope B status");
	check_scope_capacity_reservation();
	printf("agentscope_ucore: scope_capacity_reservation=1\n");
	printf("agentscope_ucore: transactional_fd_delegation=1\n");
	check_scope_lifecycle();
	printf("agentscope_ucore: lifecycle_reclamation=1\n");
	printf("agentscope_ucore: parent passed\n");
	return 0;
}
