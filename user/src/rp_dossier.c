#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_site", "pages=6")) return 1;
	if (!rp_file_contains("rp_datarel", "publication_targets=1")) return 1;
	if (!rp_write_file("rp_dossier",
			   "dossier_id=dossier:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "sections=10\n"
			   "includes=plan,lit,data,review,report,evidence,lineage,knowledge,data-release,release\n"
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
	if (!rp_append_status("dossier=ready")) return 1;
	if (!rp_append_status("reviewops=ready")) return 1;
	printf("rp_dossier: sections=10 review_board=accepted status=ready\n");
	return 0;
}
