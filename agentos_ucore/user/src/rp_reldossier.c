#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	ok = ok && rp_file_contains("rp_projectrel", "project_delivery_checks=18");
	ok = ok && rp_file_contains("rp_projectrel", "release_gate=project-release-gate");
	ok = ok && rp_file_contains("rp_publication", "publication_checks=48");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	ok = ok && rp_file_contains("rp_campaign", "campaign_checks=108");
	ok = ok && rp_file_contains("rp_camp_rank", "decision=select_trial_04");
	ok = ok && rp_file_contains("rp_prov_view", "provenance_view_checks=64");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report_rows=7");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_provenance=observed");
	if (!ok) return 1;

	if (!rp_write_file("rp_reldossier",
			   "service=release-dossier\n"
			   "release_dossier_checks=112\n"
			   "dossier=release-dossier:RUN-042:final-review\n"
			   "run_id=RUN-042\n"
			   "candidate=release-candidate:RUN-042:final\n"
			   "research_package=rp_package\n"
			   "sections=7\n"
			   "evidence_ids=18\n"
			   "decision=ready_for_review\n"
			   "checksum=rel-dossier-042\n"
			   "agentos_context=observed\n"
			   "agentos_metadata=observed\n"
			   "agentos_provenance=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_reldsec",
			   "dossier=release-dossier:RUN-042:final-review\n"
			   "section=research-package;status=ok;summary=52_artifacts_75_checks;evidence=rp_package\n"
			   "section=governance;status=ok;summary=release_gate_and_review_board_passed;evidence=rp_projectrel,rp_reviewboard\n"
			   "section=publication;status=ok;summary=2_submissions_2_decisions;evidence=rp_publication\n"
			   "section=data-release;status=ok;summary=fair_validation_and_data_version_ready;evidence=rp_datarel,rp_dataver\n"
			   "section=experiment-campaign;status=ok;summary=1_campaign_4_trials_best_04;evidence=rp_campaign,rp_trials,rp_camp_rank,rp_resreview\n"
			   "section=execution-evidence;status=ok;summary=4_packets_and_provenance_ready;evidence=rp_execobs,rp_prov_view,rp_prov_query\n"
			   "section=agentos-readiness;status=ok;summary=kernel_agent_services_observed;evidence=rp_agentos_kernel,rp_backend_exec,rp_study\n"
			   "agentos_context_record=release_dossier_sections\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_relattest",
			   "dossier=release-dossier:RUN-042:final-review\n"
			   "attestations=4\n"
			   "attestation=review-board;status=accepted;source=rp_reviewboard\n"
			   "attestation=integrity-plane;status=passed;source=rp_integrity\n"
			   "attestation=coherence-plane;status=passed;source=rp_coherence\n"
			   "attestation=publication;status=accepted;source=rp_publication\n"
			   "agentos_file_metadata=release_attestations\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_relpack",
			   "dossier=release-dossier:RUN-042:final-review\n"
			   "package_files=2\n"
			   "file=release-dossier.json;kind=json;checksum=reljson042;status=ready\n"
			   "file=release-dossier.md;kind=markdown;checksum=relmd042;status=ready\n"
			   "download=release-dossier-package:RUN-042\n"
			   "agentos_package_trace=kernel_provenance\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "release_dossier=rp_reldossier;sections=7;decision=ready_for_review;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "release_dossier_page=rp_reldossier;sections=7;decision=ready_for_review;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=release_dossier;source=rp_reldossier;sections=7;checks=112;outcome=passed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "release_dossier_checks=112;sections=7;evidence_ids=18;decision=ready_for_review;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=release_dossier;msg=dossier;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.collect_package")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.check_governance")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.check_publication")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.check_data_release")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.check_campaign")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.check_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.check_agentos")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.render_json")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.render_markdown")) return 1;
	if (!rp_append_file("rp_tool", "tool=release_dossier.package")) return 1;
	if (!rp_append_status("release_dossier=ready")) return 1;
	printf("rp_reldossier: sections=7 evidence=18 checks=112 decision=ready_for_review status=ready\n");
	return 0;
}
