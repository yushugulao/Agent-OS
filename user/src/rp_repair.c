#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_data", "failed_stage=align")) return 1;
	if (!rp_file_contains("rp_fail", "recoverable=1")) return 1;
	if (!rp_file_contains("rp_mail", "to=repair")) return 1;
	if (!rp_file_contains("rp_retryq", "status=pending")) return 1;
	if (!rp_write_file("rp_fix",
			   "failed_stage=align\naction=minimal_rerun\nresult=align.bam\nstatus=recovered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_retrylog",
			   "failed_stage=align\n"
			   "dedupe_key=RUN-042:align\n"
			   "attempts=2\n"
			   "backoff_ticks=1\n"
			   "final_result=recovered\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=repair;msg=6;status=recovered")) return 1;
	if (!rp_append_file("rp_tool", "tool=repair.rerun_stage")) return 1;
	if (!rp_append_file("rp_tool", "tool=repair.record_retry")) return 1;
	if (!rp_append_status("repair=recovered")) return 1;
	if (!rp_append_status("retry=ready")) return 1;
	printf("rp_repair: failed_stage=align action=minimal_rerun attempts=2 status=recovered\n");
	return 0;
}
