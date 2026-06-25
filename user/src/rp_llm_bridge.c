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
	if (!rp_write_file("rp_prompt",
			   "prompt_versions=2\n"
			   "routes=3\n"
			   "budget_tokens=4096\n"
			   "provider_policy=host_relay\n"
			   "eval_cases=5\n"
			   "secret_refs=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llmlog",
			   "transcripts=3\n"
			   "request_packets=1\n"
			   "response_packets=1\n"
			   "privacy_checked=1\n"
			   "replay=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("llm_bridge=ready")) return 1;
	if (!rp_append_status("promptops=ready")) return 1;
	if (!rp_append_status("llmtrace=ready")) return 1;
	printf("rp_llm_bridge: requests=1 responses=1 routes=3 mode=template status=ready\n");
	return 0;
}
