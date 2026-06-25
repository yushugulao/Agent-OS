#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_site", "pages=6")) return 1;
	if (!rp_write_file("rp_dossier",
			   "dossier_id=dossier:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "sections=8\n"
			   "includes=plan,lit,data,review,report,evidence,lineage,release\n"
			   "site_pages=6\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("dossier=ready")) return 1;
	printf("rp_dossier: sections=8 status=ready\n");
	return 0;
}
