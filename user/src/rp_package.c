#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_report", "status=packaged");
	ok = ok && rp_file_contains("rp_revision", "final_status=ready");
	ok = ok && rp_file_contains("rp_evidence", "status=ready");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_provpath", "critical_paths=3");
	ok = ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	ok = ok && rp_file_contains("rp_wfio", "compatibility_checks=6");
	ok = ok && rp_file_contains("rp_datadic", "schema_fields=17");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_figrec", "exported=3");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "capa_actions=2");
	ok = ok && rp_file_contains("rp_fail", "recoverable=1");
	ok = ok && rp_file_contains("rp_retrylog", "final_result=recovered");
	ok = ok && rp_file_contains("rp_prompt", "routes=4");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llmlog", "replay=ready");
	ok = ok && rp_file_contains("rp_llmlog", "request_packets=3");
	ok = ok && rp_file_contains("rp_relay", "mode=host_file_relay");
	ok = ok && rp_file_contains("rp_relay", "relay_packets=3");
	ok = ok && rp_file_contains("rp_policy", "license_checks=2");
	ok = ok && rp_file_contains("rp_compliance", "decision=accepted");
	ok = ok && rp_file_contains("rp_audit", "release=ready");
	ok = ok && rp_file_contains("rp_mail", "to=package");
	if (!ok) return 1;
	if (!rp_write_file("rp_package",
			   "package=research-evidence-package\n"
			   "artifacts=15\n"
			   "checks=31\n"
			   "release=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_datarel",
			   "fair_checks=5\n"
			   "fair=passed\n"
			   "data_products=1\n"
			   "dataset_deposits=1\n"
			   "doi_records=1\n"
			   "publication_targets=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_dataver",
			   "product=data-product:RUN-042\n"
			   "versions=2\n"
			   "snapshots=3\n"
			   "schema_versions=2\n"
			   "release_candidate=v2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_repro",
			   "env_locks=4\n"
			   "notebook_replay=passed\n"
			   "reproduction_checks=9\n"
			   "retry_replay=passed\n"
			   "calculation_exports=1\n"
			   "research_object_crates=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=package;msg=11;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.build_artifacts;target=rp_package;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.version_data;target=rp_dataver;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.build_repro;target=rp_repro;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_llm_eval;target=rp_llmeval;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_evidence_path;target=rp_provpath;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_data_records;target=rp_dataprof;status=ok")) return 1;
	if (!rp_append_status("package=ready")) return 1;
	if (!rp_append_status("datarel=ready")) return 1;
	if (!rp_append_status("dataver=ready")) return 1;
	if (!rp_append_status("repro=ready")) return 1;
	printf("rp_package: artifacts=15 checks=31 fair=passed repro=ready status=ready\n");
	return 0;
}
