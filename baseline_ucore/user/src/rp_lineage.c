#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_object_query", "hits=8")) return 1;
	if (!rp_write_file("rp_lineage",
			   "dataset:sample.fastq->workflow:RUN-042\n"
			   "workflow:RUN-042->dataset:counts.tsv\n"
			   "agent:planner->workflow:RUN-042\n"
			   "agent:analyst->dataset:counts.tsv\n"
			   "claim:kernel-support->evidence:RUN-042\n"
			   "evidence:RUN-042->package:RUN-042\n"
			   "package:RUN-042->release:ready\n"
			   "edges=7\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_append_status("lineage=ready")) return 1;
	printf("rp_lineage: edges=7 status=ready\n");
	return 0;
}
