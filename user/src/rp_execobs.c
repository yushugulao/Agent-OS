#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_sched", "queue_items=17");
	ok = ok && rp_file_contains("rp_taskrec", "msg=17");
	ok = ok && rp_file_contains("rp_rank", "selected=8");
	ok = ok && rp_file_contains("rp_runview", "ranked_tasks=17");
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
			   "scheduled_tasks=17\n"
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
			   "queue_actions=5\n"
			   "failure_actions=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_timeline",
			   "run_id=RUN-042\n"
			   "events=9\n"
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
			   "execution_packets=4\n"
			   "timeline_events=9\n"
			   "worker_health=ready\n"
			   "control_actions=5\n"
			   "failure_actions=2\n"
			   "evidence=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=execobs;msg=17;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.build_plan;target=rp_execplan;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.check_workers;target=rp_worker;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.write_timeline;target=rp_timeline;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=execobs.package_observer;target=rp_execobs;status=ok")) return 1;
	if (!rp_append_status("execplan=ready")) return 1;
	if (!rp_append_status("worker=ready")) return 1;
	if (!rp_append_status("timeline=ready")) return 1;
	if (!rp_append_status("execobs=ready")) return 1;
	printf("rp_execobs: timeline=9 workers=4 controls=5 observer=ready status=ready\n");
	return 0;
}
