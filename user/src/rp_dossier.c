#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_site", "pages=42")) return 1;
	if (!rp_file_contains("rp_site", "page=agentos_readiness")) return 1;
	if (!rp_file_contains("rp_wfio", "portable_steps=10")) return 1;
	if (!rp_file_contains("rp_review2", "rounds=2")) return 1;
	if (!rp_file_contains("rp_revision", "draft_versions=3")) return 1;
	if (!rp_file_contains("rp_claimrec", "claim=8")) return 1;
	if (!rp_file_contains("rp_provpath", "critical_paths=3")) return 1;
	if (!rp_file_contains("rp_dataprof", "profiles=4")) return 1;
	if (!rp_file_contains("rp_figrec", "figures=3")) return 1;
	if (!rp_file_contains("rp_trialrec", "selected=trial-3")) return 1;
	if (!rp_file_contains("rp_policy", "access_profiles=4")) return 1;
	if (!rp_file_contains("rp_compliance", "checks=8")) return 1;
	if (!rp_file_contains("rp_risk", "risk_items=3")) return 1;
	if (!rp_file_contains("rp_capa", "capa_actions=2")) return 1;
	if (!rp_file_contains("rp_diff", "changed_items=20")) return 1;
	if (!rp_file_contains("rp_delta", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_datarel", "publication_targets=1")) return 1;
	if (!rp_file_contains("rp_dataver", "release_candidate=v2")) return 1;
	if (!rp_file_contains("rp_repro", "notebook_replay=passed")) return 1;
	if (!rp_file_contains("rp_retrylog", "attempts=2")) return 1;
	if (!rp_file_contains("rp_prompt", "provider_policy=host_relay")) return 1;
	if (!rp_file_contains("rp_llmq", "queued=3")) return 1;
	if (!rp_file_contains("rp_llmeval", "passed=7")) return 1;
	if (!rp_file_contains("rp_relay", "network_stack=host_only")) return 1;
	if (!rp_file_contains("rp_execobs", "execution_packets=4")) return 1;
	if (!rp_file_contains("rp_execplan", "scheduled_tasks=21")) return 1;
	if (!rp_file_contains("rp_runconf", "profiles=2")) return 1;
	if (!rp_file_contains("rp_invocation", "steps=10")) return 1;
	if (!rp_file_contains("rp_completion", "actions=4")) return 1;
	if (!rp_file_contains("rp_mail", "to=dossier")) return 1;
	if (!rp_write_file("rp_dossier",
			   "dossier_id=dossier:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "sections=36\n"
			   "includes=plan,wfio,policy,compliance,risk,capa,diff,delta,run-configuration,workflow-invocation,workflow-completion,execution-plan,execution-observer,lit,data,data-profile,figures,trials,review,review-rounds,revision,report,evidence,claim-records,provenance-paths,lineage,knowledge,data-version,data-release,retry,repro,llm-relay,llm-queue,llm-eval,llm-governance,release\n"
			   "site_pages=42\n"
			   "site_json_payloads=14\n"
			   "site_download_links=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_reviewops",
			   "review_board=accepted\n"
			   "votes=4\n"
			   "blockers=0\n"
			   "risk_reviews=3\n"
			   "mitigation_actions=3\n"
			   "governance=passed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_submit",
			   "journal_targets=1\n"
			   "cover_letter=ready\n"
			   "data_availability=ready\n"
			   "review_response=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=dossier;msg=13;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=dossier.prepare_material;target=rp_dossier;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=dossier.prepare_submission;target=rp_submit;status=ok")) return 1;
	if (!rp_append_status("dossier=ready")) return 1;
	if (!rp_append_status("reviewops=ready")) return 1;
	if (!rp_append_status("submit=ready")) return 1;
	printf("rp_dossier: sections=36 review_board=accepted submit=ready status=ready\n");
	return 0;
}
