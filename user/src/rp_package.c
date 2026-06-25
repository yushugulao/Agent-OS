#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_report", "status=packaged");
	ok = ok && rp_file_contains("rp_evidence", "status=ready");
	ok = ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	ok = ok && rp_file_contains("rp_datadic", "schema_fields=17");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_prompt", "routes=3");
	ok = ok && rp_file_contains("rp_llmlog", "replay=ready");
	ok = ok && rp_file_contains("rp_audit", "release=ready");
	if (!ok) return 1;
	if (!rp_write_file("rp_package",
			   "package=research-evidence-package\n"
			   "artifacts=12\n"
			   "checks=19\n"
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
	if (!rp_write_file("rp_repro",
			   "env_locks=4\n"
			   "notebook_replay=passed\n"
			   "reproduction_checks=9\n"
			   "calculation_exports=1\n"
			   "research_object_crates=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("package=ready")) return 1;
	if (!rp_append_status("datarel=ready")) return 1;
	if (!rp_append_status("repro=ready")) return 1;
	printf("rp_package: artifacts=12 checks=19 fair=passed repro=ready status=ready\n");
	return 0;
}
