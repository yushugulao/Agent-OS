#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_sched_record sched_records[AGENT_SCHED_TRACE_CAP];
static struct agent_sched_config sched_config;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentsched_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void check_role_child(int role, int expected_weight)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "role child info");
	check(info.agent_role == role, "role child role");
	check(info.sched_policy == AGENT_SCHED_POLICY_ADAPTIVE,
	      "role child policy");
	check(info.sched_weight == expected_weight, "role child weight");
	check(info.sched_priority == 0, "role child priority");
	check(info.sched_budget == AGENT_SCHED_DEFAULT_BUDGET,
	      "role child budget");
	exit(0);
}

static void spawn_role_and_check(int role, int expected_weight)
{
	int pid;
	int status = 0;

	pid = agent_create_role(role);
	check(pid >= 0, "create role child");
	if (pid == 0)
		check_role_child(role, expected_weight);
	check(waitpid(pid, &status) == pid, "wait role child");
	check(status == 0, "role child status");
}

static void run_configured_child(void)
{
	struct agent_info info;
	struct agent_event event;
	int n;
	struct agent_sched_record *latest;

	for (int i = 0; i < 80; i++) {
		check(agent_info(&info) == 0, "configured child info");
		if (info.sched_weight == 150 && info.sched_priority == 20 &&
		    info.sched_budget == 3)
			break;
		sched_yield();
	}
	check(info.sched_policy == AGENT_SCHED_POLICY_ADAPTIVE,
	      "configured policy");
	check(info.sched_weight == 150, "configured weight");
	check(info.sched_priority == 20, "configured priority");
	check(info.sched_budget == 3, "configured budget");
	check(agent_watch(AGENT_EVENT_MESSAGE, "configured") == 0,
	      "configured watch");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 7101;
	strcpy(event.payload, "configured");
	check(agent_wake(getpid(), &event) == 0, "configured self wake");
	sched_yield();
	check(agent_info(&info) == 0, "configured after info");
	check((info.sched_last_reason & AGENT_SCHED_REASON_PRIORITY) != 0,
	      "configured priority reason");
	n = agent_sched_snapshot(sched_records, AGENT_SCHED_TRACE_CAP);
	check(n > 0, "configured snapshot");
	latest = &sched_records[n - 1];
	check(latest->weight == 150, "configured snapshot weight");
	check(latest->priority == 20, "configured snapshot priority");
	check((latest->reason_flags & AGENT_SCHED_REASON_PRIORITY) != 0,
	      "configured snapshot priority reason");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 0) == AGENT_STATUS_OK,
	      "configured consume");
	printf("agentsched_ucore: configurable_policy=1 weight=%d priority=%d budget=%d\n",
	       info.sched_weight, info.sched_priority, (int)info.sched_budget);
	exit(0);
}

static void check_configurable_policy(void)
{
	int pid;
	int status = 0;

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create configurable child");
	if (pid == 0)
		run_configured_child();
	memset(&sched_config, 0, sizeof(sched_config));
	sched_config.target_pid = pid;
	sched_config.update_mask = AGENT_SCHED_CONFIG_WEIGHT;
	sched_config.weight = AGENT_SCHED_WEIGHT_MAX + 1;
	check(agent_sched_config(&sched_config) == AGENT_STATUS_BAD_PARAM,
	      "bad sched weight");
	memset(&sched_config, 0, sizeof(sched_config));
	sched_config.target_pid = pid;
	sched_config.update_mask = AGENT_SCHED_CONFIG_POLICY |
				   AGENT_SCHED_CONFIG_WEIGHT |
				   AGENT_SCHED_CONFIG_PRIORITY |
				   AGENT_SCHED_CONFIG_BUDGET;
	sched_config.policy = AGENT_SCHED_POLICY_ADAPTIVE;
	sched_config.weight = 150;
	sched_config.priority = 20;
	sched_config.budget = 3;
	check(agent_sched_config(&sched_config) == 0,
	      "configure sched policy");
	check(waitpid(pid, &status) == pid, "wait configurable child");
	check(status == 0, "configured child status");
}

static void check_event_priority(void)
{
	struct agent_info before;
	struct agent_info after;
	struct agent_event event;
	int n;
	struct agent_sched_record *latest;

	check(agent_watch(AGENT_EVENT_MESSAGE, "sched") == 0, "watch sched");
	check(agent_info(&before) == 0, "event before info");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 7001;
	strcpy(event.payload, "sched-event");
	check(agent_wake(getpid(), &event) == 0, "self wake");
	sched_yield();
	check(agent_info(&after) == 0, "event after info");
	check(after.sched_dispatch_count > before.sched_dispatch_count,
	      "dispatch count increased");
	check(after.sched_event_dispatch_count >
		      before.sched_event_dispatch_count,
	      "event dispatch count increased");
	check((after.sched_last_reason & AGENT_SCHED_REASON_EVENT_QUEUE) != 0,
	      "last reason event");
	check((after.sched_last_reason & AGENT_SCHED_REASON_ROLE_WEIGHT) != 0,
	      "last reason role");
	check(after.sched_last_score > 0, "last score");
	n = agent_sched_snapshot(sched_records, AGENT_SCHED_TRACE_CAP);
	check(n > 0, "sched snapshot count");
	latest = &sched_records[n - 1];
	check((latest->reason_flags & AGENT_SCHED_REASON_EVENT_QUEUE) != 0,
	      "snapshot event reason");
	check(latest->dispatch_count == after.sched_dispatch_count,
	      "snapshot dispatch count");
	check(latest->event_queue_count > 0, "snapshot event count");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 0) == AGENT_STATUS_OK, "consume event");
	check(event.corr_id == 7001, "event corr id");
	printf("agentsched_ucore: event_priority=1 dispatch=%d event_dispatch=%d\n",
	       (int)after.sched_dispatch_count,
	       (int)after.sched_event_dispatch_count);
	printf("agentsched_ucore: reason_trace=1 records=%d reason=%d score=%d\n",
	       n, (int)latest->reason_flags, (int)latest->score);
}

static void check_fairness_counters(void)
{
	struct agent_info before;
	struct agent_info after;
	int n;

	check(agent_info(&before) == 0, "fair before info");
	for (int i = 0; i < 12; i++)
		sched_yield();
	check(agent_info(&after) == 0, "fair after info");
	check(after.sched_dispatch_count >= before.sched_dispatch_count + 8,
	      "dispatch count after yields");
	check(after.sched_preemptions >= before.sched_preemptions + 8,
	      "preemption count after yields");
	check(after.sched_vruntime > before.sched_vruntime,
	      "vruntime increased");
	check(after.sched_trace_count > before.sched_trace_count,
	      "trace count increased");
	n = agent_sched_snapshot(sched_records, AGENT_SCHED_TRACE_CAP);
	check(n > 0, "fair snapshot count");
	check((sched_records[n - 1].reason_flags &
	       AGENT_SCHED_REASON_ROLE_WEIGHT) != 0,
	      "fair snapshot role reason");
	printf("agentsched_ucore: fairness=1 dispatch=%d preemptions=%d vruntime=%d\n",
	       (int)after.sched_dispatch_count,
	       (int)after.sched_preemptions, (int)after.sched_vruntime);
}

static void run_orchestrator(void)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "orchestrator info");
	check(info.agent_role == AGENT_ROLE_ORCHESTRATOR, "orchestrator role");
	check(info.sched_weight == 110, "orchestrator weight");
	spawn_role_and_check(AGENT_ROLE_SENTINEL, 70);
	spawn_role_and_check(AGENT_ROLE_INVESTIGATOR, 90);
	spawn_role_and_check(AGENT_ROLE_RECOVERY, 120);
	printf("agentsched_ucore: role_weights sentinel=70 investigator=90 recovery=120 orchestrator=110\n");
	check_configurable_policy();
	check_event_priority();
	check_fairness_counters();
	printf("agentsched_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentsched_ucore: adaptive Agent scheduler test\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_orchestrator();
	check(waitpid(pid, &status) == pid, "wait orchestrator");
	check(status == 0, "orchestrator status");
	printf("agentsched_ucore: parent passed\n");
	return 0;
}
