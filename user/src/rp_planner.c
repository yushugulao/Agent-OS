#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_write_file("rp_plan",
			   "run=RUN-042\nworkflow=lab-gene-x\nassignments=7\npolicy=minimal_rerun\nstatus=planned\n")) {
		return 1;
	}
	if (!rp_append_status("planner=planned")) return 1;
	printf("rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 status=planned\n");
	return 0;
}
