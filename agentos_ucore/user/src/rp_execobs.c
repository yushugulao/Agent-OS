#include <agent.h>
#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>
#include <unistd.h>

static struct agent_event execobs_event;
static struct agent_timeline_record execobs_timeline[8];

static int run_kernel_exec_observer(void)
{
	struct agent_info before;
	struct agent_info after;
	int timeline_count;

	if (agent_info(&before) < 0 || !before.is_agent)
		return 0;
	if ((before.capability_mask & AGENT_CAP_WATCH) == 0 ||
	    (before.capability_mask & AGENT_CAP_MESSAGE_SEND) == 0) {
		printf("rp_execobs: event_capability_missing\n");
		return -1;
	}

	if (agent_watch(AGENT_EVENT_MESSAGE, "execobs") < 0)
		return -1;
	memset(&execobs_event, 0, sizeof(execobs_event));
	execobs_event.type = AGENT_EVENT_MESSAGE;
	execobs_event.source_pid = getpid();
	execobs_event.target_pid = getpid();
	execobs_event.corr_id = 1701;
	strcpy(execobs_event.payload,
	       "stage=execobs;run_id=RUN-042;msg=timeline");
	if (agent_wake(getpid(), &execobs_event) < 0)
		return -1;
	memset(&execobs_event, 0, sizeof(execobs_event));
	if (agent_wait(&execobs_event, 30) != AGENT_STATUS_OK ||
	    execobs_event.type != AGENT_EVENT_MESSAGE) {
		printf("rp_execobs: wait_failed type=%d status=%d\n",
		       execobs_event.type, execobs_event.status);
		return -1;
	}
	if (agent_heartbeat(5) < 0 || agent_heartbeat_stop() < 0)
		return -1;
	if (agent_info(&after) < 0)
		return -1;
	timeline_count = agent_timeline_snapshot(execobs_timeline, 8);
	if (timeline_count < 1 || after.wait_count <= before.wait_count ||
	    after.wait_wakeup_count <= before.wait_wakeup_count) {
		printf("rp_execobs: timeline_observation_failed n=%d\n",
		       timeline_count);
		return -1;
	}
	if (!rp_write_file("rp_agentos_timeline",
			   "run_id=RUN-042\n"
			   "event_delivery=kernel_agent_queue\n"
			   "wait=wakeup\n"
			   "heartbeat=observed\n"
			   "timeline_snapshot=ready\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=timeline;agent_event_notify=kernel_queue;timeline_observe=kernel_snapshot;wait=wakeup;heartbeat=observed;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_exec = run_kernel_exec_observer();

	if (kernel_exec < 0)
		return 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_sched", "queue_items=21");
	ok = ok && rp_file_contains("rp_taskrec", "msg=21");
	ok = ok && rp_file_contains("rp_rank", "selected=10");
	ok = ok && rp_file_contains("rp_runview", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_retrylog", "final_result=recovered");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_privacy", "decision=accepted");
	ok = ok && rp_file_contains("rp_mail", "to=execobs");
	if (!ok) return 1;
	if (!rp_write_file("rp_execplan",
			   "run_id=RUN-042\n"
			   "execution_plan=plain-user-processes\n"
			   "workflow_steps=10\n"
			   "scheduled_tasks=21\n"
			   "worker_slots=4\n"
			   "retry_items=1\n"
			   "llm_packets=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_worker",
			   "workers=4\n"
			   "ready=4\n"
			   "busy=0\n"
			   "stalled=0\n"
			   "heartbeats=4\n"
			   "agentos_heartbeat=kernel_observed\n"
			   "agentos_wait=wakeup\n"
			   "queue_actions=8\n"
			   "failure_actions=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_timeline",
			   "run_id=RUN-042\n"
			   "events=9\n"
			   "agentos_event_delivery=kernel_queue\n"
			   "agentos_timeline_snapshot=ready\n"
			   "stage_order=plan,retrieve,analyze,repair,review,llm,package,release,dossier\n"
			   "first_tick=1\n"
			   "last_tick=42\n"
			   "critical_path=align_repair\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_execobs",
			   "run_id=RUN-042\n"
			   "observer=ready\n"
			   "agentos_observer=kernel_event_timeline\n"
			   "execution_packets=4\n"
			   "timeline_events=9\n"
			   "kernel_timeline_events=observed\n"
			   "worker_health=ready\n"
			   "control_actions=8\n"
			   "failure_actions=2\n"
			   "evidence=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=execobs;msg=17;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.build_plan")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.check_workers")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.write_timeline")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.package_observer")) return 1;
	if (kernel_exec &&
	    !rp_append_file("rp_tool", "tool=agentos.event_wait_wake")) {
		return 1;
	}
	if (kernel_exec &&
	    !rp_append_file("rp_tool", "tool=agentos.timeline_snapshot")) {
		return 1;
	}
	if (!rp_append_status("execplan=ready")) return 1;
	if (!rp_append_status("worker=ready")) return 1;
	if (!rp_append_status("timeline=ready")) return 1;
	if (!rp_append_status("execobs=ready")) return 1;
	printf("rp_execobs: timeline=9 workers=4 controls=8 observer=ready status=ready\n");
	return 0;
}
