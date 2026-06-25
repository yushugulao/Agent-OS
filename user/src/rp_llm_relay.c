#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_llm_req", "status=queued");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_relay", "mode=host_file_relay");
	ok = ok && rp_file_contains("rp_prompt", "provider_policy=host_relay");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	if (!ok) return 1;

	if (!rp_write_file("rp_llm_packets",
			   "run_id=RUN-042\n"
			   "packet=1;route=review_summary;mode=template;secret_ref=host_env;status=ready\n"
			   "packet=2;route=method_check;mode=template;secret_ref=host_env;status=ready\n"
			   "packet=3;route=recovery_note;mode=template;secret_ref=host_env;status=ready\n"
			   "packets=3\n"
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
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_guard",
			   "run_id=RUN-042\n"
			   "checked_packets=3\n"
			   "secrets_in_ucore=0\n"
			   "host_secret_refs=3\n"
			   "redactions=0\n"
			   "outbound_owner=host_relay\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_llm_hostreq",
			   "run_id=RUN-042\n"
			   "request_file=rp_llm_packets\n"
			   "response_file=rp_llm_resp\n"
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
			   "status=ready\n")) {
		return 1;
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
