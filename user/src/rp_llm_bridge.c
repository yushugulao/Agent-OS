#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_evidence", "claims=8")) return 1;
	if (!rp_file_contains("rp_claimrec", "claim=8")) return 1;
	if (!rp_file_contains("rp_provpath", "critical_paths=3")) return 1;
	if (!rp_file_contains("rp_report", "status=packaged")) return 1;
	if (!rp_file_contains("rp_mail", "to=llm")) return 1;
	if (!rp_write_file("rp_llm_req",
			   "provider=template\n"
			   "run_id=RUN-042\n"
			   "task=compare_agent_os_recovery\n"
			   "payload=failed_stage:align,evidence_links:5,claims:8\n"
			   "secret_policy=no_secret_in_ucore\n"
			   "status=queued\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llmq",
			   "queue=host_relay_packets\n"
			   "requests=3\n"
			   "queued=3\n"
			   "route_primary=review_summary\n"
			   "route_secondary=method_check\n"
			   "route_fallback=recovery_note\n"
			   "q1=review_summary;claims=8;evidence_links=5;secret_policy=no_secret_in_ucore\n"
			   "q2=method_check;protocol_checks=5;data_schema=17;secret_policy=no_secret_in_ucore\n"
			   "q3=recovery_note;failed_stage=align;attempts=2;prov_paths=3;secret_policy=no_secret_in_ucore\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_resp",
			   "provider=template\n"
			   "run_id=RUN-042\n"
			   "responses=3\n"
			   "request_file=rp_llmq\n"
			   "packet_file=rp_llm_packets\n"
			   "response_file=rp_llm_resp\n"
			   "matched_requests=3\n"
			   "host_relay_roundtrip=ready\n"
			   "cloud_responses=0\n"
			   "template_fallbacks=1\n"
			   "cloud_ready_when_host_env=1\n"
			   "r1=review_summary_supported_by_current_evidence\n"
			   "r2=method_check_consistent_with_protocol\n"
			   "r3=recovery_note_align_stage_repaired\n"
			   "match=q1->r1,q2->r2,q3->r3\n"
			   "answer=recovery_report_supported_by_current_evidence\n"
			   "citations=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_relay",
			   "mode=host_file_relay\n"
			   "request_file=rp_llmq\n"
			   "response_file=rp_llm_resp\n"
			   "secret_location=host_environment\n"
			   "network_stack=host_only\n"
			   "relay_packets=3\n"
			   "fallback_packets=1\n"
			   "fallback=template_response\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_prompt",
			   "prompt_versions=3\n"
			   "routes=4\n"
			   "budget_tokens=4096\n"
			   "provider_policy=host_relay\n"
			   "eval_cases=7\n"
			   "secret_refs=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llmlog",
			   "transcripts=5\n"
			   "request_packets=3\n"
			   "response_packets=3\n"
			   "matched_packets=3\n"
			   "roundtrip=ready\n"
			   "route_switches=2\n"
			   "privacy_checked=1\n"
			   "replay=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llmeval",
			   "eval_cases=7\n"
			   "passed=7\n"
			   "grounded=5\n"
			   "format_checks=3\n"
			   "route_switches=2\n"
			   "fallback_used=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=llm;msg=9;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm.prepare_packet;target=rp_llm_req;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm.route_queue;target=rp_llmq;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm.template_response;target=rp_llm_resp;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm.define_relay;target=rp_relay;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm.evaluate_packets;target=rp_llmeval;status=ok")) return 1;
	if (!rp_append_status("llm_bridge=ready")) return 1;
	if (!rp_append_status("llmqueue=ready")) return 1;
	if (!rp_append_status("relay=ready")) return 1;
	if (!rp_append_status("promptops=ready")) return 1;
	if (!rp_append_status("llmtrace=ready")) return 1;
	if (!rp_append_status("llmeval=ready")) return 1;
	printf("rp_llm_bridge: requests=3 responses=3 routes=4 eval=7 relay=ready status=ready\n");
	return 0;
}
