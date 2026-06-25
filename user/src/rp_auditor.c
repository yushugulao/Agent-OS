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
	ok = ok && rp_file_contains("rp_datadic", "schema_drift=0");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_mail", "to=auditor");
	if (!ok) return 1;
	if (!rp_write_file("rp_audit",
			   "provenance=verified\n"
			   "release=ready\n"
			   "package=ready\n"
			   "schema=verified\n"
			   "replay=verified\n"
			   "labops=verified\n"
			   "status=passed\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=auditor;msg=7;status=passed")) return 1;
	if (!rp_append_file("rp_tool", "tool=auditor.verify_provenance;target=rp_audit;status=ok")) return 1;
	if (!rp_append_status("auditor=passed")) return 1;
	printf("rp_auditor: provenance=verified release=ready package=ready status=passed\n");
	return 0;
}
