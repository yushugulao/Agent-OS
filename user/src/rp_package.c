#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_report", "status=packaged");
	ok = ok && rp_file_contains("rp_evidence", "status=ready");
	ok = ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	ok = ok && rp_file_contains("rp_audit", "release=ready");
	if (!ok) return 1;
	if (!rp_write_file("rp_package",
			   "package=research-evidence-package\nartifacts=8\nchecks=13\nrelease=ready\nstatus=ready\n")) {
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
	if (!rp_append_status("package=ready")) return 1;
	if (!rp_append_status("datarel=ready")) return 1;
	printf("rp_package: artifacts=8 checks=13 fair=passed status=ready\n");
	return 0;
}
