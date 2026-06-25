#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "assignments=7");
	ok = ok && rp_file_contains("rp_lit", "status=ready");
	ok = ok && rp_file_contains("rp_data", "failed_stage=align");
	ok = ok && rp_file_contains("rp_review", "decision=accepted_after_repair");
	ok = ok && rp_file_contains("rp_report", "status=packaged");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	if (!ok) return 1;
	if (!rp_write_file("rp_audit",
			   "provenance=verified\nrelease=ready\npackage=ready\nstatus=passed\n")) {
		return 1;
	}
	if (!rp_append_status("auditor=passed")) return 1;
	printf("rp_auditor: provenance=verified release=ready package=ready status=passed\n");
	return 0;
}
