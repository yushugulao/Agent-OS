#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_review", "decision=accepted_after_repair")) return 1;
	if (!rp_file_contains("rp_quality", "passed=7")) return 1;
	if (!rp_file_contains("rp_protocol", "amendments=1")) return 1;
	if (!rp_file_contains("rp_soplog", "deviation=handled")) return 1;
	if (!rp_file_contains("rp_fail", "failure_class=tool_output_missing")) return 1;
	if (!rp_file_contains("rp_policy", "data_use_rules=5")) return 1;
	if (!rp_file_contains("rp_mail", "to=governance")) return 1;
	if (!rp_write_file("rp_risk",
			   "run_id=RUN-042\n"
			   "risk_items=3\n"
			   "risk=1;kind=tool_output_missing;severity=medium;mitigation=minimal_rerun;status=mitigated\n"
			   "risk=2;kind=protocol_deviation;severity=low;mitigation=sop_review;status=accepted\n"
			   "risk=3;kind=llm_outbound;severity=low;mitigation=host_relay_policy;status=controlled\n"
			   "open_risks=0\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_capa",
			   "run_id=RUN-042\n"
			   "capa_actions=2\n"
			   "action=1;source=align;owner=repair;verification=retrylog;status=verified\n"
			   "action=2;source=protocol;owner=lab;verification=soplog;status=verified\n"
			   "deviation_records=1\n"
			   "verifications=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=governance;msg=15;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=governance.register_risk;target=rp_risk;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=governance.verify_capa;target=rp_capa;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=governance.check_policy;target=rp_policy;status=ok")) return 1;
	if (!rp_append_status("governance=ready")) return 1;
	if (!rp_append_status("risk=ready")) return 1;
	if (!rp_append_status("capa=ready")) return 1;
	printf("rp_governance: risks=3 capa=2 deviations=1 status=ready\n");
	return 0;
}
