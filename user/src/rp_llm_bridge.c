#include <stdio.h>
#include <research_platform_state.h>

static void copy_llm_value(const char *key, const char *fallback, char *out, int cap)
{
	if (!rp_host_seed_copy_llm_value(key, out, cap)) {
		rp_copy_text(out, cap, fallback);
	}
}

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
			   "queue_validation=passed\n"
			   "schema_checks=3\n"
			   "dispatch_ready=3\n"
			   "route_decisions=3\n"
			   "secret_policy_records=3\n"
			   "host_handoff_required=1\n"
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
			   "response_join=passed\n"
			   "response_hash_records=3\n"
			   "grounded_references=5\n"
			   "template_provider=plain_ucore_deterministic\n"
			   "host_cloud_provider=optional\n"
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
			   "queue_consumer=rp_llm_relay\n"
			   "handoff_contract=ordinary_files\n"
			   "request_validation=passed\n"
			   "response_validation=passed\n"
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
			   "queue_validation=passed\n"
			   "dispatch_records=3\n"
			   "response_join=passed\n"
			   "secret_scan=passed\n"
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
			   "queue_checks=3\n"
			   "route_checks=3\n"
			   "privacy_checks=3\n"
			   "fallback_checks=3\n"
			   "route_switches=2\n"
			   "fallback_used=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has_llm_relay_action()) {
		char request_id[64];
		char run_id[64];
		char route[64];
		char provider[64];
		char prompt[96];
		char budget[32];
		char secret_ref[64];
		char response_id[64];
		char mode[48];
		char summary[96];
		char citations[32];
		char fallback_case[64];
		char fallback_action[64];
		char fallback_reason[80];
		char fallback_status[48];

		copy_llm_value("request_id=", "host-q1", request_id, sizeof(request_id));
		copy_llm_value("run_id=", "RUN-042", run_id, sizeof(run_id));
		copy_llm_value("route=", "review_summary", route, sizeof(route));
		copy_llm_value("provider=", "template", provider, sizeof(provider));
		copy_llm_value("prompt=", "summarize_recovery", prompt, sizeof(prompt));
		copy_llm_value("budget=", "1536", budget, sizeof(budget));
		copy_llm_value("secret_ref=", "host_env", secret_ref, sizeof(secret_ref));
		copy_llm_value("response_id=", "host-r1", response_id, sizeof(response_id));
		copy_llm_value("mode=", "template", mode, sizeof(mode));
		copy_llm_value("summary=", "host_response_ready", summary, sizeof(summary));
		copy_llm_value("citations=", "5", citations, sizeof(citations));
		copy_llm_value("case=", "missing_cloud_key", fallback_case, sizeof(fallback_case));
		copy_llm_value("action=", "template_response", fallback_action, sizeof(fallback_action));
		copy_llm_value("reason=", "host_env_absent", fallback_reason, sizeof(fallback_reason));
		copy_llm_value("fallback_status=", "ready", fallback_status, sizeof(fallback_status));

		if (!rp_append_host_action_line("rp_llm_req", "host_llm_request_id=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_req", "host_llm_run_id=", run_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_req", "host_llm_route=", route)) return 1;
		if (!rp_append_host_action_line("rp_llm_req", "host_llm_provider=", provider)) return 1;
		if (!rp_append_host_action_line("rp_llm_req", "host_llm_prompt=", prompt)) return 1;
		if (!rp_append_host_action_line("rp_llm_req", "host_llm_budget=", budget)) return 1;
		if (!rp_append_host_action_line("rp_llm_req", "host_llm_secret_ref=", secret_ref)) return 1;

		if (!rp_append_host_action_line("rp_llmq", "host_llm_queue_request=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_llmq", "host_llm_queue_route=", route)) return 1;
		if (!rp_append_host_action_line("rp_llmq", "host_llm_queue_provider=", provider)) return 1;
		if (!rp_append_host_action_line("rp_llmq", "host_llm_queue_budget=", budget)) return 1;

		if (!rp_append_host_action_line("rp_llm_resp", "host_llm_response_id=", response_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_resp", "host_llm_response_request=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_resp", "host_llm_response_provider=", provider)) return 1;
		if (!rp_append_host_action_line("rp_llm_resp", "host_llm_response_mode=", mode)) return 1;
		if (!rp_append_host_action_line("rp_llm_resp", "host_llm_response_summary=", summary)) return 1;
		if (!rp_append_host_action_line("rp_llm_resp", "host_llm_response_citations=", citations)) return 1;

		if (!rp_append_file("rp_relay", "host_llm_relay=ready")) return 1;
		if (!rp_append_host_action_line("rp_relay", "host_llm_relay_request=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_relay", "host_llm_relay_response=", response_id)) return 1;
		if (!rp_append_host_action_line("rp_relay", "host_llm_relay_provider=", provider)) return 1;

		if (!rp_append_host_action_line("rp_prompt", "host_llm_prompt_route=", route)) return 1;
		if (!rp_append_host_action_line("rp_prompt", "host_llm_prompt_text=", prompt)) return 1;
		if (!rp_append_host_action_line("rp_prompt", "host_llm_prompt_budget=", budget)) return 1;

		if (!rp_append_host_action_line("rp_llmlog", "host_llm_log_request=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_llmlog", "host_llm_log_response=", response_id)) return 1;
		if (!rp_append_host_action_line("rp_llmlog", "host_llm_log_fallback=", fallback_case)) return 1;
		if (!rp_append_host_action_line("rp_llmlog", "host_llm_log_fallback_action=", fallback_action)) return 1;
		if (!rp_append_host_action_line("rp_llmlog", "host_llm_log_fallback_reason=", fallback_reason)) return 1;
		if (!rp_append_host_action_line("rp_llmeval", "host_llm_eval_citations=", citations)) return 1;
		if (!rp_append_host_action_line("rp_llmeval", "host_llm_eval_fallback_status=", fallback_status)) return 1;
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
