#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_objects", "objects=500")) return 1;
	if (!rp_write_file("rp_object_records",
			   "workflow:RUN-042:lab-gene-x:ready\n"
			   "dataset:sample.fastq:input:ready\n"
			   "dataset:counts.tsv:result:ready\n"
			   "agent:planner:orchestrator:ready\n"
			   "agent:analyst:analysis:ready\n"
			   "claim:kernel-support:observability:accepted\n"
			   "evidence:RUN-042:links:ready\n"
			   "package:RUN-042:release:ready\n")) {
		return 1;
	}
	if (!rp_append_status("object_store=ready")) return 1;
	printf("rp_object_store: records=8 status=ready\n");
	return 0;
}
