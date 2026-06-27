#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_package", "release=ready")) return 1;
	if (!rp_file_contains("rp_audit", "status=passed")) return 1;
	if (!rp_file_contains("rp_privacy", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_datarel", "fair=passed")) return 1;
	if (!rp_file_contains("rp_dataver", "release_candidate=v2")) return 1;
	if (!rp_file_contains("rp_repro", "reproduction_checks=9")) return 1;
	if (!rp_file_contains("rp_repro", "retry_replay=passed")) return 1;
	if (!rp_file_contains("rp_risk", "open_risks=0")) return 1;
	if (!rp_file_contains("rp_capa", "verifications=2")) return 1;
	if (!rp_file_contains("rp_delta", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_diff", "changed_items=20")) return 1;
	if (!rp_file_contains("rp_relay", "status=ready")) return 1;
	if (!rp_file_contains("rp_llmq", "queued=3")) return 1;
	if (!rp_file_contains("rp_llmeval", "passed=7")) return 1;
	if (!rp_file_contains("rp_compliance", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_execobs", "observer=ready")) return 1;
	if (!rp_file_contains("rp_timeline", "critical_path=align_repair")) return 1;
	if (!rp_file_contains("rp_runconf", "profiles=2")) return 1;
	if (!rp_file_contains("rp_invocation", "status=recovered")) return 1;
	if (!rp_file_contains("rp_completion", "status=ready")) return 1;
	if (!rp_file_contains("rp_mail", "to=release")) return 1;
	if (!rp_write_file("rp_release",
			   "release_id=release:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "package=ready\n"
			   "audit=passed\n"
			   "privacy=accepted\n"
			   "fair=passed\n"
			   "data_version=v2\n"
			   "repro=ready\n"
			   "risk=accepted\n"
			   "capa=verified\n"
			   "delta=accepted\n"
			   "diff=ready\n"
			   "relay=ready\n"
			   "llm_packet=ready\n"
			   "llm_queue=ready\n"
			   "llm_eval=passed\n"
			   "compliance=accepted\n"
			   "execution=observed\n"
			   "timeline=ready\n"
			   "run_configuration=ready\n"
			   "invocation=recovered\n"
			   "completion=ready\n"
			   "decision=release\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=release;msg=12;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=release.decide")) return 1;
	if (!rp_append_status("release=ready")) return 1;
	printf("rp_release: decision=release checks=17 status=ready\n");
	return 0;
}
