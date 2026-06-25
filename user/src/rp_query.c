#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_services", "workflow=34");
	ok = ok && rp_file_contains("rp_services", "agent=26");
	ok = ok && rp_file_contains("rp_services", "evidence=10");
	if (!ok) return 1;
	if (!rp_write_file("rp_query",
			   "query=workflow,agent,evidence\nworkflow_hits=34\nagent_hits=26\nevidence_hits=10\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_append_status("query=ready")) return 1;
	printf("rp_query: workflow=34 agent=26 evidence=10 status=ready\n");
	return 0;
}
