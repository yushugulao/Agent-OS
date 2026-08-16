#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentloop_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void send_self(uint64 corr_id, const char *payload, int expect)
{
	struct agent_event event;
	int rc;

	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = corr_id;
	strcpy(event.payload, payload);
	rc = agent_wake(getpid(), &event);
	check(rc == expect, "agent_wake result");
}

static void expect_event(uint64 corr_id, const char *payload)
{
	struct agent_event event;

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 50) == AGENT_STATUS_OK, "wait event");
	check(event.corr_id == corr_id, "corr id");
	check(strcmp(event.payload, payload) == 0, "payload");
	check(event.cause_sequence != 0, "event cause");
	check(event.span_id != 0, "event span");
}

static void expect_heartbeat(void)
{
	struct agent_event event;

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 50) == AGENT_STATUS_OK,
	      "wait reserved heartbeat");
	check(event.type == AGENT_EVENT_TIMER, "reserved heartbeat type");
	check(event.source_pid == 0, "reserved heartbeat source");
	check(strcmp(event.payload, "timer=heartbeat") == 0,
	      "reserved heartbeat payload");
}

static void run_queue_source(int target_pid, int attributed, int gate_fd,
			     int report_fd)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	char phase;

	check(read(gate_fd, &phase, 1) == 1 && phase == 'G',
	      "wait queue source gate");
	if (attributed) {
		for (int i = 0; i < AGENT_EVENT_SOURCE_LIMIT; i++) {
			memset(&op, 0, sizeof(op));
			op.version = AGENT_CALL_VERSION;
			op.tool_id = AGENT_TOOL_ACTION_COMMIT;
			op.request_id = 300 + i;
			strcpy(op.payload, "queue-reserve-denied");
			check(agent_run(&op, &res, 1, 0) == 1,
			      "emit attributed event");
			check(res.status == AGENT_STATUS_DENIED,
			      "attributed operation denied");
		}
		phase = 'A';
	} else {
		for (int i = 0; i < AGENT_EVENT_SOURCE_LIMIT; i++) {
			memset(&event, 0, sizeof(event));
			event.type = AGENT_EVENT_MESSAGE;
			event.corr_id = 200 + i;
			strcpy(event.payload, "external-fill");
			check(agent_wake(target_pid, &event) == AGENT_STATUS_OK,
			      "fill directed event class");
		}
		phase = 'D';
	}
	check(write(report_fd, &phase, 1) == 1, "report queue source done");
	exit(0);
}

static void run_external_probe(int gate_fd, int report_fd)
{
	struct agent_op op;
	struct agent_result res;
	char phase;

	for (int i = 0; i < 2; i++) {
		check(read(gate_fd, &phase, 1) == 1 &&
			      phase == (i == 0 ? 'G' : 'R'),
		      "wait external probe gate");
		memset(&op, 0, sizeof(op));
		op.version = AGENT_CALL_VERSION;
		op.tool_id = AGENT_TOOL_ACTION_COMMIT;
		op.request_id = 380 + i;
		strcpy(op.payload, "external-limit-probe");
		check(agent_run(&op, &res, 1, 0) == 1,
		      "emit external probe");
		check(res.status == AGENT_STATUS_DENIED,
		      "external probe operation denied");
		phase = i == 0 ? 'X' : 'Y';
		check(write(report_fd, &phase, 1) == 1,
		      "report external probe");
	}
	exit(0);
}

static void check_queue_reservations(void)
{
	struct agent_info info;
	struct agent_event event;
	int gates[4][2];
	int report[2];
	int pids[4];
	uint64 deadline;
	int directed = 0;
	int attributed = 0;
	int kernel_events;
	int status = 0;
	char phase = 'G';

	check(agent_watch(AGENT_EVENT_MESSAGE, "external-fill") == 0,
	      "watch external fill");
	check(agent_watch(AGENT_EVENT_POLICY_DENIED, "action=action_commit") ==
		      0,
	      "watch attributed denial");
	check(pipe(report) == 0, "queue report pipe");
	for (int i = 0; i < 4; i++) {
		check(pipe(gates[i]) == 0, "queue gate pipe");
		check(agent_scope_delegate_fd(gates[i][0]) ==
				      AGENT_STATUS_OK &&
			      agent_scope_delegate_fd(report[1]) ==
				      AGENT_STATUS_OK,
		      "delegate queue source pipe endpoints");
		pids[i] = agent_create_role(AGENT_ROLE_SENTINEL);
		check(pids[i] >= 0, "create queue source");
		if (pids[i] == 0 && i < 3)
			run_queue_source(getppid(), i == 2, gates[i][0],
					 report[1]);
		if (pids[i] == 0)
			run_external_probe(gates[i][0], report[1]);
	}
	for (int i = 0; i < 2; i++) {
		check(agent_route_config(pids[i], getpid(),
					 AGENT_IPC_EVENT_MESSAGE,
					 AGENT_IPC_ROUTE_GRANT) ==
			      AGENT_STATUS_OK,
		      "grant queue source route");
		check(write(gates[i][1], &phase, 1) == 1,
		      "release directed queue source");
	}
	for (int i = 0; i < 2; i++)
		check(read(report[0], &phase, 1) == 1 && phase == 'D',
		      "wait directed queue source");
	check(agent_info(&info) == 0, "info at ipc class limit");
	check(info.event_queue_count == AGENT_EVENT_IPC_LIMIT,
	      "ipc class limit reached");
	send_self(299, "external-fill", AGENT_STATUS_NO_SPACE);

	phase = 'G';
	check(write(gates[2][1], &phase, 1) == 1,
	      "release attributed queue source");
	check(read(report[0], &phase, 1) == 1 && phase == 'A',
	      "wait attributed queue source");
	check(agent_info(&info) == 0, "info at external limit");
	check(info.event_queue_count == AGENT_EVENT_EXTERNAL_LIMIT,
	      "external event limit reached");
	phase = 'G';
	check(write(gates[3][1], &phase, 1) == 1,
	      "release external limit probe");
	check(read(report[0], &phase, 1) == 1 && phase == 'X',
	      "wait rejected external probe");
	check(agent_info(&info) == 0 &&
		      info.event_queue_count == AGENT_EVENT_EXTERNAL_LIMIT,
	      "external limit rejects another source");

	check(agent_heartbeat_configure(1) == 0, "reserved kernel heartbeat set");
	check(agent_info(&info) == 0, "info before kernel reserve");
	deadline = info.current_tick + 50;
	do {
		check(agent_info(&info) == 0, "poll kernel reserve");
		if (info.event_queue_count == AGENT_EVENT_EXTERNAL_LIMIT + 1)
			break;
		sched_yield();
	} while (info.current_tick < deadline);
	check(info.event_queue_count == AGENT_EVENT_EXTERNAL_LIMIT + 1,
	      "kernel event uses reserved capacity");
	deadline = info.current_tick + 8;
	do {
		sched_yield();
		check(agent_info(&info) == 0, "poll coalesced kernel event");
	} while (info.current_tick < deadline);
	check(info.event_queue_count == AGENT_EVENT_EXTERNAL_LIMIT + 1,
	      "pending heartbeat is coalesced");
	check(agent_heartbeat_configure(0) == 0, "reserved kernel heartbeat stop");
	check(agent_info(&info) == 0, "info after kernel reserve");
	kernel_events = (int)info.event_queue_count - AGENT_EVENT_EXTERNAL_LIMIT;
	check(kernel_events == 1, "coalesced kernel event count");

	for (int i = 0; i < AGENT_EVENT_EXTERNAL_LIMIT; i++) {
		memset(&event, 0, sizeof(event));
		check(agent_wait(&event, 50) == AGENT_STATUS_OK,
		      "drain external queue");
		if (event.type == AGENT_EVENT_MESSAGE) {
			check(strcmp(event.payload, "external-fill") == 0,
			      "directed fill payload");
			directed++;
		} else if (event.type == AGENT_EVENT_POLICY_DENIED) {
			check(strcmp(event.payload, "action=action_commit") == 0,
			      "attributed fill payload");
			attributed++;
		} else {
			check(0, "unexpected external event type");
		}
	}
	check(directed == AGENT_EVENT_IPC_LIMIT,
	      "directed event class accounting");
	check(attributed == AGENT_EVENT_CLASS_RESERVE,
	      "attributed event class accounting");
	for (int i = 0; i < kernel_events; i++)
		expect_heartbeat();
	check(agent_info(&info) == 0 && info.event_queue_count == 0,
	      "reservation queue drained");
	send_self(298, "external-fill", AGENT_STATUS_OK);
	expect_event(298, "external-fill");
	phase = 'R';
	check(write(gates[3][1], &phase, 1) == 1,
	      "release reclaimed external probe");
	check(read(report[0], &phase, 1) == 1 && phase == 'Y',
	      "wait reclaimed external probe");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 50) == AGENT_STATUS_OK,
	      "receive reclaimed attributed event");
	check(event.type == AGENT_EVENT_POLICY_DENIED &&
		      event.source_pid == pids[3] && event.corr_id == 381 &&
		      strcmp(event.payload, "action=action_commit") == 0,
	      "reclaimed attributed event source");
	check(agent_info(&info) == 0 && info.event_queue_count == 0,
	      "private event counters reclaimed");

	for (int i = 0; i < 4; i++) {
		check(waitpid(pids[i], &status) == pids[i],
		      "wait queue source");
		check(status == 0, "queue source status");
		close(gates[i][0]);
		close(gates[i][1]);
	}
	close(report[0]);
	close(report[1]);
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "external-fill") == 1,
	      "unwatch external fill");
	check(agent_unwatch(AGENT_EVENT_POLICY_DENIED,
			    "action=action_commit") == 1,
	      "unwatch attributed denial");
	printf("agentloop_ucore: ipc_class_limit=%d\n", AGENT_EVENT_IPC_LIMIT);
	printf("agentloop_ucore: external_limit=%d\n",
	       AGENT_EVENT_EXTERNAL_LIMIT);
	printf("agentloop_ucore: system_event_reserved=%d\n",
	       AGENT_EVENT_KERNEL_RESERVE);
	printf("agentloop_ucore: heartbeat_reserve_coalesced=1\n");
	printf("agentloop_ucore: external_reject_reclaim=1\n");
}

static void fill_external_target(int target_pid)
{
	int gate[2];
	int report[2];
	int pids[3];
	int directed = 0;
	int attributed = 0;
	int status = 0;
	char phase = 'G';

	check(pipe(report) == 0, "slow watcher fill report pipe");
	check(pipe(gate) == 0, "slow watcher fill gate pipe");
	for (int i = 0; i < 3; i++) {
		check(agent_scope_delegate_fd(gate[0]) ==
				      AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(report[1]) == AGENT_STATUS_OK,
		      "delegate slow watcher filler pipes");
		pids[i] = agent_create_role(AGENT_ROLE_SENTINEL);
		check(pids[i] >= 0, "create slow watcher filler");
		if (pids[i] == 0)
			run_queue_source(target_pid, i == 2, gate[0],
					 report[1]);
	}
	close(gate[0]);
	close(report[1]);
	for (int i = 0; i < 2; i++)
		check(agent_route_config(pids[i], target_pid,
					 AGENT_IPC_EVENT_MESSAGE,
					 AGENT_IPC_ROUTE_GRANT) ==
			      AGENT_STATUS_OK,
		      "grant slow watcher filler route");
	for (int i = 0; i < 3; i++)
		check(write(gate[1], &phase, 1) == 1,
		      "release slow watcher filler");
	for (int i = 0; i < 3; i++) {
		check(read(report[0], &phase, 1) == 1,
		      "wait slow watcher filler");
		if (phase == 'D')
			directed++;
		else if (phase == 'A')
			attributed++;
		else
			check(0, "slow watcher filler phase");
	}
	check(directed == 2 && attributed == 1,
	      "slow watcher filler classes");
	for (int i = 0; i < 3; i++) {
		check(waitpid(pids[i], &status) == pids[i],
		      "wait slow watcher filler process");
		check(status == 0, "slow watcher filler status");
	}
	close(gate[1]);
	close(report[0]);
}

static void run_full_watcher(int ready_fd, int gate_fd)
{
	struct agent_info info;
	char phase = 'W';

	check(agent_watch(AGENT_EVENT_MESSAGE, "external-fill") == 0,
	      "slow watcher message watch");
	check(agent_watch(AGENT_EVENT_POLICY_DENIED, "action=action_commit") ==
		      0,
	      "slow watcher policy watch");
	check(write(ready_fd, &phase, 1) == 1,
	      "report slow watcher installed");
	check(read(gate_fd, &phase, 1) == 1 && phase == 'C',
	      "check slow watcher admission");
	check(agent_info(&info) == 0, "slow watcher info");
	check(info.event_queue_count == AGENT_EVENT_EXTERNAL_LIMIT,
	      "slow watcher external admission saturated");
	phase = 'F';
	check(write(ready_fd, &phase, 1) == 1,
	      "report slow watcher saturated");
	check(read(gate_fd, &phase, 1) == 1 && phase == 'X',
	      "release slow watcher");
	exit(0);
}

static void run_later_watcher(int ready_fd, int report_fd)
{
	struct agent_event event;
	char phase = 'L';

	check(agent_watch(AGENT_EVENT_POLICY_DENIED, "action=action_commit") ==
		      0,
	      "later watcher policy watch");
	check(write(ready_fd, &phase, 1) == 1, "report later watcher ready");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 200) == AGENT_STATUS_OK,
	      "later watcher receives broadcast");
	check(event.type == AGENT_EVENT_POLICY_DENIED,
	      "later watcher event type");
	check(strcmp(event.payload, "action=action_commit") == 0,
	      "later watcher event payload");
	phase = 'B';
	check(write(report_fd, &phase, 1) == 1,
	      "report later watcher broadcast");
	exit(0);
}

static void run_broadcast_source(int gate_fd)
{
	struct agent_op op;
	struct agent_result res;
	char phase;

	check(read(gate_fd, &phase, 1) == 1 && phase == 'G',
	      "release broadcast source");
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_ACTION_COMMIT;
	op.request_id = 390;
	strcpy(op.payload, "broadcast-isolation");
	check(agent_run(&op, &res, 1, 0) == 1,
	      "emit isolated broadcast");
	check(res.status == AGENT_STATUS_DENIED,
	      "broadcast source operation denied");
	exit(0);
}

static void check_broadcast_isolation(void)
{
	int ready[2];
	int report[2];
	int full_gate[2];
	int source_gate[2];
	int full_pid;
	int later_pid;
	int source_pid;
	int status = 0;
	char phase;

	check(pipe(ready) == 0, "broadcast ready pipe");
	check(pipe(full_gate) == 0, "full watcher gate pipe");
	check(agent_scope_delegate_fd(ready[1]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(full_gate[0]) == AGENT_STATUS_OK,
	      "delegate full watcher pipe endpoints");
	full_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(full_pid >= 0, "create full watcher");
	if (full_pid == 0)
		run_full_watcher(ready[1], full_gate[0]);
	close(full_gate[0]);
	check(read(ready[0], &phase, 1) == 1 && phase == 'W',
	      "wait slow watcher install");
	fill_external_target(full_pid);
	phase = 'C';
	check(write(full_gate[1], &phase, 1) == 1,
	      "check slow watcher saturation");
	check(read(ready[0], &phase, 1) == 1 && phase == 'F',
	      "wait slow watcher saturation");
	check(pipe(report) == 0, "broadcast report pipe");
	check(pipe(source_gate) == 0, "broadcast source gate pipe");
	check(agent_scope_delegate_fd(ready[1]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(report[1]) == AGENT_STATUS_OK,
	      "delegate later watcher pipe endpoints");
	later_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(later_pid >= 0, "create later watcher");
	if (later_pid == 0)
		run_later_watcher(ready[1], report[1]);
	close(ready[1]);
	close(report[1]);
	check(agent_scope_delegate_fd(source_gate[0]) == AGENT_STATUS_OK,
	      "delegate broadcast source gate");
	source_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(source_pid >= 0, "create broadcast source");
	if (source_pid == 0)
		run_broadcast_source(source_gate[0]);
	close(source_gate[0]);
	check(read(ready[0], &phase, 1) == 1 && phase == 'L',
	      "wait later watcher ready");
	phase = 'G';
	check(write(source_gate[1], &phase, 1) == 1,
	      "start broadcast source");
	check(read(report[0], &phase, 1) == 1 && phase == 'B',
	      "later watcher received after full watcher");
	phase = 'X';
	check(write(full_gate[1], &phase, 1) == 1,
	      "stop full watcher");
	check(waitpid(source_pid, &status) == source_pid,
	      "wait broadcast source");
	check(status == 0, "broadcast source status");
	check(waitpid(later_pid, &status) == later_pid,
	      "wait later watcher");
	check(status == 0, "later watcher status");
	check(waitpid(full_pid, &status) == full_pid, "wait full watcher");
	check(status == 0, "full watcher status");
	close(ready[0]);
	close(report[0]);
	close(full_gate[1]);
	close(source_gate[1]);
	printf("agentloop_ucore: broadcast_slow_watcher_isolated=1\n");
}

static void run_cancel_waiter(void)
{
	struct agent_info before;
	struct agent_info after;
	struct agent_event event;

	check(agent_info(&before) == 0, "cancel waiter info before");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 100) == AGENT_STATUS_CANCELLED,
	      "wait cancelled");
	check(event.type == AGENT_EVENT_CANCELLED, "cancel event type");
	check(event.status == AGENT_STATUS_CANCELLED, "cancel event status");
	check(strcmp(event.payload, "cancel=operator") == 0,
	      "cancel reason");
	check(event.source_pid > 0, "cancel source");
	check(event.cause_sequence != 0, "cancel cause");
	check(event.span_id != 0, "cancel span");
	check(agent_info(&after) == 0, "cancel waiter info after");
	check(after.wait_count == before.wait_count + 1, "cancel wait count");
	check(after.wait_sleep_count > before.wait_sleep_count,
	      "cancel woke sleeping waiter");
	check(after.wait_cancel_count >= 1, "cancel count");
	check(after.context_path_latest > before.context_path_latest,
	      "cancel context");
	exit(0);
}

static void run_agent(void)
{
	static struct agent_info before;
	static struct agent_info after;
	static struct agent_event event;
	int pid;
	int status = 0;

	check(agent_watch(AGENT_EVENT_MESSAGE, "fifo") == 0, "watch fifo");
	send_self(1, "fifo-one", 0);
	send_self(2, "fifo-two", 0);
	send_self(3, "fifo-three", 0);
	expect_event(1, "fifo-one");
	expect_event(2, "fifo-two");
	expect_event(3, "fifo-three");
	printf("agentloop_ucore: fifo=1\n");
	printf("agentloop_ucore: event_causality=1\n");

	check(agent_watch(AGENT_EVENT_MESSAGE, "overflow") == 0,
	      "watch overflow");
	for (int i = 0; i < AGENT_EVENT_SOURCE_LIMIT; i++)
		send_self(100 + i, "overflow", 0);
	send_self(999, "overflow", AGENT_STATUS_NO_SPACE);
	check(agent_info(&after) == 0, "info after source limit");
	check(after.event_queue_count == AGENT_EVENT_SOURCE_LIMIT,
	      "source limit queue depth");
	expect_event(100, "overflow");
	send_self(100 + AGENT_EVENT_SOURCE_LIMIT, "overflow",
		  AGENT_STATUS_OK);
	for (int i = 1; i < AGENT_EVENT_SOURCE_LIMIT; i++)
		expect_event(100 + i, "overflow");
	expect_event(100 + AGENT_EVENT_SOURCE_LIMIT, "overflow");
	check(agent_info(&after) == 0, "info after overflow");
	check(after.event_dropped >= 1, "dropped count");
	check(after.event_queue_count == 0, "reserved queue drained");
	printf("agentloop_ucore: overflow_dropped=%d\n",
	       (int)after.event_dropped);
	printf("agentloop_ucore: message_source_limit=%d\n",
	       AGENT_EVENT_SOURCE_LIMIT);

	check(agent_unwatch(AGENT_EVENT_MESSAGE, "overflow") == 1,
	      "unwatch overflow");
	send_self(1000, "overflow", AGENT_STATUS_NOT_FOUND);
	printf("agentloop_ucore: unwatch=1\n");
	check_queue_reservations();
	check_broadcast_isolation();

	check(agent_info(&before) == 0, "info before timeout");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 2) == AGENT_STATUS_TIMEOUT, "timeout");
	check(event.status == AGENT_STATUS_TIMEOUT, "timeout status");
	check(agent_info(&after) == 0, "info after timeout");
	check(after.timeout_count == before.timeout_count + 1,
	      "timeout count");
	check(after.wait_sleep_count > before.wait_sleep_count,
	      "sleep count");
	check(after.wait_loop_count <= before.wait_loop_count + 3,
	      "finite timeout no polling");
	printf("agentloop_ucore: timeout_sleep_no_poll=1\n");

	check(agent_unwatch(AGENT_EVENT_TIMER, "heartbeat") == 0,
	      "heartbeat starts without timer watch");
	check(sys_agent_heartbeat_set(2) == 0, "heartbeat set syscall");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 30) == AGENT_STATUS_OK,
	      "intrinsic heartbeat wait");
	check(event.type == AGENT_EVENT_TIMER, "heartbeat type");
	check(strcmp(event.payload, "timer=heartbeat") == 0,
	      "heartbeat payload");
	check(event.span_id != 0, "heartbeat span");

	check(agent_heartbeat_configure(AGENT_HEARTBEAT_MAX_TICKS) == 0,
	      "heartbeat slow frequency");
	sched_yield();
	check(agent_heartbeat_configure(1) == 0, "heartbeat dynamic frequency");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 6) == AGENT_STATUS_OK,
	      "adjusted heartbeat wakes promptly");
	check(event.type == AGENT_EVENT_TIMER &&
		      strcmp(event.payload, "timer=heartbeat") == 0,
	      "adjusted heartbeat event");

	check(agent_heartbeat_configure(1) == 0, "heartbeat coalesce set");
	check(agent_info(&after) == 0, "heartbeat coalesce info start");
	{
		uint64 deadline = after.current_tick + 8;

		do {
			sched_yield();
			check(agent_info(&after) == 0,
			      "heartbeat coalesce info poll");
		} while (after.current_tick < deadline);
	}
	check(after.event_queue_count == 1,
	      "only one pending heartbeat is queued");
	check(sys_agent_heartbeat_stop() == 0, "heartbeat stop syscall");
	expect_heartbeat();
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 3) == AGENT_STATUS_TIMEOUT,
	      "heartbeat stopped timeout");

	check(agent_heartbeat_configure(AGENT_HEARTBEAT_MAX_TICKS) == 0,
	      "heartbeat maximum accepted");
	check(agent_heartbeat_configure(0) == 0, "stop maximum heartbeat");
	check(agent_heartbeat_configure(AGENT_HEARTBEAT_MAX_TICKS + 1ULL) ==
		      AGENT_STATUS_BAD_PARAM,
	      "heartbeat above maximum rejected");
	check(agent_heartbeat_configure(~(uint64)0) == AGENT_STATUS_BAD_PARAM,
	      "heartbeat uint64 overflow rejected");
	check(agent_heartbeat(-1) == AGENT_STATUS_BAD_PARAM,
	      "legacy negative heartbeat rejected");
	{
		static struct agent_op heartbeat_op;
		static struct agent_result heartbeat_res;

		memset(&heartbeat_op, 0, sizeof(heartbeat_op));
		heartbeat_op.version = AGENT_CALL_VERSION;
		heartbeat_op.tool_id = AGENT_TOOL_HEARTBEAT_CONFIGURE;
		heartbeat_op.request_id = 391;
		heartbeat_op.arg0 = AGENT_HEARTBEAT_MAX_TICKS + 1ULL;
		check(agent_run(&heartbeat_op, &heartbeat_res, 1, 0) == 1,
		      "heartbeat tool boundary call");
		check(heartbeat_res.status == AGENT_STATUS_BAD_PARAM,
		      "heartbeat tool boundary rejected");
	}
	check(agent_info(&after) == 0 && after.heartbeat_interval == 0,
	      "invalid heartbeat leaves stopped state");
	check(agent_heartbeat(1) == 0, "legacy heartbeat ABI set");
	expect_heartbeat();
	check(agent_heartbeat(0) == 0, "legacy heartbeat ABI stop");
	check(sys_agent_heartbeat_stop() == 0, "heartbeat stop syscall");
	check(sys_agent_heartbeat_stop() == 0, "heartbeat stop idempotent");
	printf("agentloop_ucore: heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1\n");

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create cancel waiter");
	if (pid == 0)
		run_cancel_waiter();
	sleep(2);
	check(agent_wait_cancel(pid, "cancel=operator") == 0,
	      "cancel waiter");
	check(waitpid(pid, &status) == pid, "wait cancel waiter");
	check(status == 0, "cancel waiter status");
	printf("agentloop_ucore: wait_cancel=1\n");

	printf("agentloop_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentloop_ucore: Agent event queue test\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_agent();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentloop_ucore: parent passed\n");
	return 0;
}
