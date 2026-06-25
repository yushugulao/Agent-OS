#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_services", "workflow=34");
	ok = ok && rp_file_contains("rp_services", "agent=26");
	ok = ok && rp_file_contains("rp_services", "evidence=10");
	ok = ok && rp_file_contains("rp_sched", "queue_items=17");
	ok = ok && rp_file_contains("rp_taskrec", "msg=17");
	ok = ok && rp_file_contains("rp_fail", "status=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	if (!ok) return 1;
	int task_lines = rp_count_lines("rp_taskrec");
	int high_tasks = rp_count_token("rp_taskrec", "prio=H");
	int critical_tasks = rp_count_token("rp_taskrec", "class=critical");
	int ready_tasks = rp_count_token("rp_taskrec", "state=ready");
	if (task_lines != 17 || high_tasks != 3 || critical_tasks != 4 || ready_tasks != 17) {
		printf("rp_query: bad_task_records lines=%d high=%d critical=%d ready=%d\n",
		       task_lines, high_tasks, critical_tasks, ready_tasks);
		return 1;
	}
	if (!rp_write_file("rp_query",
			   "query=workflow,agent,evidence\nworkflow_hits=34\nagent_hits=26\nevidence_hits=10\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_rank",
			   "source=rp_taskrec\n"
			   "records=17\n"
			   "ready=17\n"
			   "high=3\n"
			   "critical=4\n"
			   "selected=8\n"
			   "selected_stages=literature,profile,align,decision,measure,risk_capa,release_delta,execution_trace\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_runview",
			   "view=RUN-042\n"
			   "workflow_hits=34\n"
			   "agent_hits=26\n"
			   "evidence_hits=10\n"
			   "scheduler_items=17\n"
			   "ranked_tasks=17\n"
			   "selected_tasks=8\n"
			   "critical_tasks=4\n"
			   "failure_items=1\n"
			   "budget_state=within_budget\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_tool", "tool=query.rank_tasks;target=rp_rank;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=query.build_runview;target=rp_runview;status=ok")) return 1;
	if (!rp_append_status("query=ready")) return 1;
	if (!rp_append_status("rank=ready")) return 1;
	if (!rp_append_status("runview=ready")) return 1;
	printf("rp_query: workflow=34 agent=26 evidence=10 ranked=17 selected=8 status=ready\n");
	return 0;
}
