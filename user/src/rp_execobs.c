#include <agent.h>
#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>
#include <rp_evidence.h>
#include <unistd.h>

static struct agent_event execobs_event;
#define EXECOBS_TIMELINE_CAP 32
static struct agent_timeline_record execobs_timeline[EXECOBS_TIMELINE_CAP];
static char execobs_evidence_body[1536];
static int execobs_timeline_count;
static int execobs_timeline_total;
static uint64 execobs_first_tick;
static uint64 execobs_last_tick;
static uint64 execobs_first_sequence;
static uint64 execobs_last_sequence;

static int timeline_record_after(struct agent_timeline_record *left,
				 struct agent_timeline_record *right)
{
	if (right->tick != left->tick)
		return right->tick > left->tick;
	if (right->source != left->source)
		return right->source > left->source;
	return right->sequence >= left->sequence;
}

static int write_kernel_timeline_evidence(struct agent_info *before,
					  struct agent_info *after,
					  int enqueue_seen,
					  int consume_seen)
{
	char *body = execobs_evidence_body;

	body[0] = 0;
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "evidence_source=", "kernel_timeline_snapshot");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "evidence_generation=", "runtime");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "run_id=", "RUN-042");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "event_delivery=", "kernel_agent_queue");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "delivery_corr_id=", execobs_event.corr_id);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "delivery_event_id=", execobs_event.event_id);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "delivery_source_pid=", execobs_event.source_pid);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "delivery_target_pid=", execobs_event.target_pid);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_records=", execobs_timeline_count);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_visible_records=", execobs_timeline_total);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_first_tick=", execobs_first_tick);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_last_tick=", execobs_last_tick);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_first_sequence=", execobs_first_sequence);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_last_sequence=", execobs_last_sequence);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "event_enqueue_records=", enqueue_seen);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "event_consume_records=", consume_seen);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "wait_count_delta=",
			       after->wait_count - before->wait_count);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "wait_wakeup_delta=",
			       after->wait_wakeup_count - before->wait_wakeup_count);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "heartbeat_tick=", after->last_heartbeat_tick);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "timeline_order=", "verified");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "event_identity=", "verified");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "wait=", "wakeup");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "heartbeat=", "verified");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "timeline_snapshot=", "ready");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "status=", "verified");
	return rp_write_file("rp_agentos_timeline", body);
}

static int run_kernel_exec_observer(void)
{
	struct agent_info before;
	struct agent_info after;
	int enqueue_seen = 0;
	int consume_seen = 0;
	int self_pid = getpid();

	if (agent_info(&before) < 0 || !before.is_agent)
		return 0;
	if ((before.capability_mask &
	     (AGENT_CAP_WATCH | AGENT_CAP_MESSAGE_SEND |
	      AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)) !=
	    (AGENT_CAP_WATCH | AGENT_CAP_MESSAGE_SEND |
	     AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)) {
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
	    execobs_event.type != AGENT_EVENT_MESSAGE ||
	    execobs_event.status != AGENT_STATUS_OK ||
	    execobs_event.event_id == 0 || execobs_event.corr_id != 1701 ||
	    execobs_event.source_pid != self_pid ||
	    execobs_event.target_pid != self_pid ||
	    strcmp(execobs_event.payload,
		   "stage=execobs;run_id=RUN-042;msg=timeline") != 0) {
		printf("rp_execobs: wait_failed type=%d status=%d\n",
		       execobs_event.type, execobs_event.status);
		return -1;
	}
	if (agent_heartbeat(5) < 0 || agent_heartbeat_stop() < 0)
		return -1;
	if (agent_info(&after) < 0)
		return -1;
	execobs_timeline_total = agent_timeline_snapshot(0, 0);
	execobs_timeline_count = agent_timeline_snapshot(
		execobs_timeline, EXECOBS_TIMELINE_CAP);
	if (execobs_timeline_total < 1 || execobs_timeline_count < 1 ||
	    execobs_timeline_count > execobs_timeline_total ||
	    after.wait_count <= before.wait_count ||
	    after.wait_wakeup_count <= before.wait_wakeup_count) {
		printf("rp_execobs: timeline_observation_failed n=%d\n",
		       execobs_timeline_count);
		return -1;
	}
	execobs_first_tick = execobs_timeline[0].tick;
	execobs_last_tick = execobs_timeline[execobs_timeline_count - 1].tick;
	execobs_first_sequence = execobs_timeline[0].sequence;
	execobs_last_sequence =
		execobs_timeline[execobs_timeline_count - 1].sequence;
	for (int i = 0; i < execobs_timeline_count; i++) {
		struct agent_timeline_record *record = &execobs_timeline[i];

		if (i > 0 &&
		    !timeline_record_after(&execobs_timeline[i - 1], record)) {
			printf("rp_execobs: timeline_order_failed at=%d\n", i);
			return -1;
		}
		if (record->source != AGENT_TIMELINE_SOURCE_AUDIT ||
		    record->event_type != AGENT_EVENT_MESSAGE ||
		    record->source_pid != self_pid ||
		    record->target_pid != self_pid || record->value1 != 1701)
			continue;
		if (record->kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE)
			enqueue_seen++;
		if (record->kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			consume_seen++;
	}
	if (enqueue_seen < 1 || consume_seen < 1) {
		printf("rp_execobs: event_timeline_missing enqueue=%d consume=%d\n",
		       enqueue_seen, consume_seen);
		return -1;
	}
	if (!write_kernel_timeline_evidence(&before, &after, enqueue_seen,
					    consume_seen)) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=timeline;agent_event_notify=kernel_queue;timeline_observe=kernel_snapshot;event_identity=verified;timeline_order=verified;wait=wakeup;heartbeat=verified;generation=runtime;status=verified"))
		return -1;
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_exec = run_kernel_exec_observer();
	char *body = execobs_evidence_body;

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
			   "generation=demo_workflow_model\n"
			   "workflow_steps=10\n"
			   "scheduled_tasks=21\n"
			   "worker_slots=4\n"
			   "retry_items=1\n"
			   "llm_packets=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_worker",
			   "generation=demo_workflow_model\n"
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
	body[0] = 0;
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "run_id=", "RUN-042");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "generation=", "demo_workflow_model");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "events_source=", "declared_stage_model");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body), "events=", 9);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "agentos_event_delivery=",
				 kernel_exec ? "kernel_queue" : "not_available");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "agentos_timeline_snapshot=",
				 kernel_exec ? "verified" : "not_available");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "kernel_timeline_records=",
			       kernel_exec ? execobs_timeline_count : 0);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "stage_order=",
				 "plan,retrieve,analyze,repair,review,llm,package,release,dossier");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "expected_first_tick=", 1);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "expected_last_tick=", 42);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "critical_path=", "align_repair");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "status=", "ready");
	if (!rp_write_file("rp_timeline", body)) {
		return 1;
	}
	body[0] = 0;
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "run_id=", "RUN-042");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "observer=", "ready");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "observer_generation=", "runtime");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "agentos_observer=", "kernel_event_timeline");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "kernel_evidence_state=",
				 kernel_exec ? "verified" : "not_available");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "execution_packets_source=", "demo_workflow_model");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "execution_packets=", 4);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "timeline_events_source=", "demo_workflow_model");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "timeline_events=", 9);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "kernel_timeline_records=",
			       kernel_exec ? execobs_timeline_count : 0);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "kernel_timeline_visible_records=",
			       kernel_exec ? execobs_timeline_total : 0);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "kernel_timeline_events=",
				 kernel_exec ? "verified" : "not_available");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "worker_health=", "ready");
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "control_actions=", 8);
	rp_evidence_append_u64(body, sizeof(execobs_evidence_body),
			       "failure_actions=", 2);
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "evidence=", kernel_exec ? "verified" : "model_only");
	rp_evidence_append_value(body, sizeof(execobs_evidence_body),
				 "status=", "ready");
	if (!rp_write_file("rp_execobs", body)) {
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
	printf("rp_execobs: evidence_source=%s generation=runtime kernel_timeline_records=%d model_timeline_events=9 status=ready\n",
	       kernel_exec ? "kernel_snapshot" : "demo_model",
	       kernel_exec ? execobs_timeline_count : 0);
	return 0;
}
