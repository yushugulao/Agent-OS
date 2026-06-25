#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_evidence", "claims=8")) return 1;
	if (!rp_file_contains("rp_report", "status=packaged")) return 1;
	if (!rp_write_file("rp_llm_req",
			   "provider=template\n"
			   "run_id=RUN-042\n"
			   "task=compare_agent_os_recovery\n"
			   "payload=failed_stage:align,evidence_links:5,claims:8\n"
			   "secret_policy=no_secret_in_ucore\n"
			   "status=queued\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_resp",
			   "provider=template\n"
			   "run_id=RUN-042\n"
			   "answer=recovery_report_supported_by_current_evidence\n"
			   "citations=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("llm_bridge=ready")) return 1;
	printf("rp_llm_bridge: requests=1 responses=1 mode=template status=ready\n");
	return 0;
}
