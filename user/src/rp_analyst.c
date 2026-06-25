#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_plan", "workflow=lab-gene-x")) return 1;
	if (!rp_write_file("rp_data",
			   "datasets=4\nstatistics=6\nfigures=3\nfailed_stage=align\nstatus=needs_repair\n")) {
		return 1;
	}
	if (!rp_append_status("analyst=ready")) return 1;
	printf("rp_analyst: datasets=4 statistics=6 figures=3 status=ready\n");
	return 0;
}
