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
	check(after.wait_cancel_count >= 1, "cancel count");
	check(after.context_path_latest > before.context_path_latest,
	      "cancel context");
	exit(0);
}

static void run_agent(void)
{
	struct agent_info before;
	struct agent_info after;
	struct agent_event event;
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
	for (int i = 0; i < AGENT_EVENT_QUEUE_CAP; i++)
		send_self(100 + i, "overflow", 0);
	send_self(999, "overflow", AGENT_STATUS_NO_SPACE);
	for (int i = 0; i < AGENT_EVENT_QUEUE_CAP; i++)
		expect_event(100 + i, "overflow");
	check(agent_info(&after) == 0, "info after overflow");
	check(after.event_dropped >= 1, "dropped count");
	printf("agentloop_ucore: overflow_dropped=%d\n",
	       (int)after.event_dropped);

	check(agent_unwatch(AGENT_EVENT_MESSAGE, "overflow") == 1,
	      "unwatch overflow");
	send_self(1000, "overflow", AGENT_STATUS_NOT_FOUND);
	printf("agentloop_ucore: unwatch=1\n");

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

	check(agent_watch(AGENT_EVENT_TIMER, "heartbeat") == 0,
	      "watch heartbeat");
	check(agent_heartbeat(2) == 0, "heartbeat set");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 30) == AGENT_STATUS_OK, "heartbeat wait");
	check(event.type == AGENT_EVENT_TIMER, "heartbeat type");
	check(strcmp(event.payload, "timer=heartbeat") == 0,
	      "heartbeat payload");
	check(event.span_id != 0, "heartbeat span");
	check(agent_unwatch(AGENT_EVENT_TIMER, "heartbeat") == 1,
	      "unwatch heartbeat");
	check(agent_heartbeat(1) == 0, "heartbeat after unwatch");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 3) == AGENT_STATUS_TIMEOUT,
	      "heartbeat unwatched timeout");
	printf("agentloop_ucore: timer_unwatch=1\n");
	check(agent_heartbeat_stop() == 0, "heartbeat stop");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 3) == AGENT_STATUS_TIMEOUT,
	      "heartbeat stopped timeout");
	printf("agentloop_ucore: heartbeat_wake_stop=1\n");

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create cancel waiter");
	if (pid == 0)
		run_cancel_waiter();
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
