#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_services", "workflow=34");
	ok = ok && rp_file_contains("rp_services", "agent=26");
	ok = ok && rp_file_contains("rp_services", "evidence=10");
	ok = ok && rp_file_contains("rp_sched", "queue_items=14");
	ok = ok && rp_file_contains("rp_fail", "status=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	if (!ok) return 1;
	if (!rp_write_file("rp_query",
			   "query=workflow,agent,evidence\nworkflow_hits=34\nagent_hits=26\nevidence_hits=10\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_runview",
			   "view=RUN-042\n"
			   "workflow_hits=34\n"
			   "agent_hits=26\n"
			   "evidence_hits=10\n"
			   "scheduler_items=14\n"
			   "failure_items=1\n"
			   "budget_state=within_budget\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_tool", "tool=query.build_runview;target=rp_runview;status=ok")) return 1;
	if (!rp_append_status("query=ready")) return 1;
	if (!rp_append_status("runview=ready")) return 1;
	printf("rp_query: workflow=34 agent=26 evidence=10 runview=ready status=ready\n");
	return 0;
}
