#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_llm_req", "secret_policy=no_secret_in_ucore")) return 1;
	if (!rp_file_contains("rp_llmq", "queued=3")) return 1;
	if (!rp_file_contains("rp_llmq", "secret_policy=no_secret_in_ucore")) return 1;
	if (!rp_file_contains("rp_llm_resp", "status=ready")) return 1;
	if (!rp_file_contains("rp_relay", "secret_location=host_environment")) return 1;
	if (!rp_file_contains("rp_prompt", "secret_refs=3")) return 1;
	if (!rp_file_contains("rp_llmeval", "passed=7")) return 1;
	if (!rp_file_contains("rp_llmlog", "privacy_checked=1")) return 1;
	if (!rp_file_contains("rp_llm_packets", "cloud_capable=1")) return 1;
	if (!rp_file_contains("rp_llm_packets", "roundtrip=ready")) return 1;
	if (!rp_file_contains("rp_llm_routes", "routes=4")) return 1;
	if (!rp_file_contains("rp_llm_guard", "secrets_in_ucore=0")) return 1;
	if (!rp_file_contains("rp_llm_hostreq", "secret_material=not_in_ucore")) return 1;
	if (!rp_file_contains("rp_llm_hostreq", "matched_responses=3")) return 1;
	if (!rp_file_contains("rp_llm_resp", "host_relay_roundtrip=ready")) return 1;
	if (!rp_file_contains("rp_llm_fallback", "fallback_cases=1")) return 1;
	if (!rp_file_contains("rp_policy", "llm_outbound_rules=4")) return 1;
	if (!rp_file_contains("rp_policy", "data_use_rules=5")) return 1;
	if (!rp_file_contains("rp_mail", "to=privacy")) return 1;
	if (!rp_write_file("rp_privacy",
			   "policy=offline_template_packet\n"
			   "checked_files=13\n"
			   "request_packets=3\n"
			   "sensitive_tokens=0\n"
			   "redactions=0\n"
			   "relay_protocol_files=5\n"
			   "cloud_secrets_in_ucore=0\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_compliance",
			   "run_id=RUN-042\n"
			   "checks=8\n"
			   "access_profiles=4\n"
			   "data_use_rules=5\n"
			   "llm_rules=4\n"
			   "outbound_packets=3\n"
			   "secrets_in_ucore=0\n"
			   "relay_protocol_files=5\n"
			   "fallback_policy=ready\n"
			   "license_checks=2\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=privacy;msg=10;status=accepted")) return 1;
	if (!rp_append_file("rp_tool", "tool=privacy.review_packet;target=rp_privacy;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=privacy.check_llm_queue;target=rp_llmq;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=privacy.check_relay_protocol;target=rp_llm_guard;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=privacy.write_compliance;target=rp_compliance;status=ok")) return 1;
	if (!rp_append_status("privacy=ready")) return 1;
	if (!rp_append_status("compliance=ready")) return 1;
	printf("rp_privacy: checked=13 packets=3 redactions=0 compliance=accepted status=ready\n");
	return 0;
}
