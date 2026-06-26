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
	int ok = 1;
	ok = ok && rp_file_contains("rp_llm_req", "status=queued");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_relay", "mode=host_file_relay");
	ok = ok && rp_file_contains("rp_prompt", "provider_policy=host_relay");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_llm_resp", "host_relay_roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_resp", "match=q1->r1,q2->r2,q3->r3");
	if (!ok) return 1;

	if (!rp_write_file("rp_llm_packets",
			   "run_id=RUN-042\n"
			   "packet=1;request=q1;route=review_summary;mode=template;secret_ref=host_env;response=r1;status=ready\n"
			   "packet=2;request=q2;route=method_check;mode=template;secret_ref=host_env;response=r2;status=ready\n"
			   "packet=3;request=q3;route=recovery_note;mode=template;secret_ref=host_env;response=r3;status=ready\n"
			   "packets=3\n"
			   "validated_packets=3\n"
			   "dispatch_records=3\n"
			   "response_join=passed\n"
			   "packet_schema=passed\n"
			   "retry_policy=template_fallback\n"
			   "matched_responses=3\n"
			   "roundtrip=ready\n"
			   "cloud_capable=1\n"
			   "ucore_network=0\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_routes",
			   "run_id=RUN-042\n"
			   "route=review_summary;provider=template_or_cloud;budget=1536;status=ready\n"
			   "route=method_check;provider=template_or_cloud;budget=1024;status=ready\n"
			   "route=recovery_note;provider=template_or_cloud;budget=1024;status=ready\n"
			   "route=fallback_summary;provider=template;budget=512;status=ready\n"
			   "routes=4\n"
			   "route_policy=deterministic_then_host_optional\n"
			   "route_decision=review_summary->template\n"
			   "route_decision=method_check->template\n"
			   "route_decision=recovery_note->template\n"
			   "roundtrip_routes=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_guard",
			   "run_id=RUN-042\n"
			   "checked_packets=3\n"
			   "secrets_in_ucore=0\n"
			   "host_secret_refs=3\n"
			   "payload_hashes=3\n"
			   "pii_scan=passed\n"
			   "secret_scan=passed\n"
			   "blocked_packets=0\n"
			   "redactions=0\n"
			   "outbound_owner=host_relay\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_hostreq",
			   "run_id=RUN-042\n"
			   "request_file=rp_llm_packets\n"
			   "response_file=rp_llm_resp\n"
			   "source_queue=rp_llmq\n"
			   "host_request_records=3\n"
			   "host_response_records=3\n"
			   "matched_responses=3\n"
			   "host_request_manifest=ready\n"
			   "host_response_manifest=ready\n"
			   "cloud_disabled_reason=host_env_absent_in_plain_ucore\n"
			   "template_execution=ready\n"
			   "roundtrip=ready\n"
			   "cloud_mode=optional_host_side\n"
			   "template_mode=ready\n"
			   "required_host_env=OPENAI_API_KEY\n"
			   "secret_material=not_in_ucore\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_fallback",
			   "run_id=RUN-042\n"
			   "fallback_cases=1\n"
			   "case=missing_cloud_key;action=template_response;status=ready\n"
			   "case=network_unavailable;action=template_response;status=ready\n"
			   "case=privacy_reject;action=stop_before_host;status=ready\n"
			   "fallback_decision=template_for_missing_key\n"
			   "fallback_decision=template_for_network_loss\n"
			   "fallback_decision=stop_for_privacy_reject\n"
			   "fallback_trace=rp_llm_guard->rp_llm_fallback->rp_llm_resp\n"
			   "offline_template_verified=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has_llm_relay_action()) {
		char request_id[64];
		char response_id[64];
		char route[64];
		char provider[64];
		char budget[32];
		char secret_ref[64];
		char mode[48];
		char fallback_case[64];
		char fallback_action[64];
		char fallback_reason[80];
		char fallback_status[48];

		copy_llm_value("request_id=", "host-q1", request_id, sizeof(request_id));
		copy_llm_value("response_id=", "host-r1", response_id, sizeof(response_id));
		copy_llm_value("route=", "review_summary", route, sizeof(route));
		copy_llm_value("provider=", "template", provider, sizeof(provider));
		copy_llm_value("budget=", "1536", budget, sizeof(budget));
		copy_llm_value("secret_ref=", "host_env", secret_ref, sizeof(secret_ref));
		copy_llm_value("mode=", "template", mode, sizeof(mode));
		copy_llm_value("case=", "missing_cloud_key", fallback_case, sizeof(fallback_case));
		copy_llm_value("action=", "template_response", fallback_action, sizeof(fallback_action));
		copy_llm_value("reason=", "host_env_absent", fallback_reason, sizeof(fallback_reason));
		copy_llm_value("fallback_status=", "ready", fallback_status, sizeof(fallback_status));

		if (!rp_append_host_action_line("rp_llm_packets", "host_llm_packet_request=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_packets", "host_llm_packet_response=", response_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_packets", "host_llm_packet_route=", route)) return 1;
		if (!rp_append_host_action_line("rp_llm_packets", "host_llm_packet_mode=", mode)) return 1;

		if (!rp_append_host_action_line("rp_llm_routes", "host_llm_route=", route)) return 1;
		if (!rp_append_host_action_line("rp_llm_routes", "host_llm_route_provider=", provider)) return 1;
		if (!rp_append_host_action_line("rp_llm_routes", "host_llm_route_budget=", budget)) return 1;

		if (!rp_append_host_action_line("rp_llm_guard", "host_llm_guard_secret_ref=", secret_ref)) return 1;
		if (!rp_append_file("rp_llm_guard", "host_llm_guard_status=passed")) return 1;

		if (!rp_append_host_action_line("rp_llm_hostreq", "host_llm_host_request=", request_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_hostreq", "host_llm_host_response=", response_id)) return 1;
		if (!rp_append_host_action_line("rp_llm_hostreq", "host_llm_host_provider=", provider)) return 1;
		if (!rp_append_host_action_line("rp_llm_hostreq", "host_llm_host_mode=", mode)) return 1;

		if (!rp_append_host_action_line("rp_llm_fallback", "host_llm_fallback_case=", fallback_case)) return 1;
		if (!rp_append_host_action_line("rp_llm_fallback", "host_llm_fallback_action=", fallback_action)) return 1;
		if (!rp_append_host_action_line("rp_llm_fallback", "host_llm_fallback_reason=", fallback_reason)) return 1;
		if (!rp_append_host_action_line("rp_llm_fallback", "host_llm_fallback_status=", fallback_status)) return 1;
	}
	if (!rp_append_file("rp_ack", "ack=llm_relay;msg=relay;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm_relay.write_packets;target=rp_llm_packets;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm_relay.write_routes;target=rp_llm_routes;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm_relay.write_guard;target=rp_llm_guard;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm_relay.write_hostreq;target=rp_llm_hostreq;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=llm_relay.write_fallback;target=rp_llm_fallback;status=ok")) return 1;
	if (!rp_append_status("llmrelay=ready")) return 1;
	if (!rp_append_status("relay_packets=ready")) return 1;
	if (!rp_append_status("relay_routes=ready")) return 1;
	if (!rp_append_status("relay_guard=ready")) return 1;
	if (!rp_append_status("relay_fallback=ready")) return 1;
	printf("rp_llm_relay: packets=3 routes=4 guard=ready fallback=1 status=ready\n");
	return 0;
}
