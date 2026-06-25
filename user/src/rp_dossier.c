#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_site", "pages=6")) return 1;
	if (!rp_file_contains("rp_datarel", "publication_targets=1")) return 1;
	if (!rp_file_contains("rp_repro", "notebook_replay=passed")) return 1;
	if (!rp_file_contains("rp_retrylog", "attempts=2")) return 1;
	if (!rp_file_contains("rp_prompt", "provider_policy=host_relay")) return 1;
	if (!rp_file_contains("rp_relay", "network_stack=host_only")) return 1;
	if (!rp_file_contains("rp_mail", "to=dossier")) return 1;
	if (!rp_write_file("rp_dossier",
			   "dossier_id=dossier:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "sections=14\n"
			   "includes=plan,lit,data,review,report,evidence,lineage,knowledge,data-release,retry,repro,llm-relay,llm-governance,release\n"
			   "site_pages=6\n"
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
	printf("rp_dossier: sections=14 review_board=accepted submit=ready status=ready\n");
	return 0;
}
