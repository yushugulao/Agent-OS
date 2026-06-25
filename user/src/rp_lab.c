#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_plan", "run=RUN-042")) return 1;
	if (!rp_file_contains("rp_data", "datasets=4")) return 1;
	if (!rp_file_contains("rp_datadic", "schema_fields=17")) return 1;
	if (!rp_file_contains("rp_compute", "replay=ready")) return 1;
	if (!rp_file_contains("rp_review", "status=accepted")) return 1;
	if (!rp_write_file("rp_samples",
			   "sample:S-001:cohort-A:input:ready\n"
			   "sample:S-002:cohort-A:input:ready\n"
			   "sample:S-003:cohort-B:input:ready\n"
			   "sample:S-004:cohort-B:hold:reviewed\n"
			   "sheet=RUN-042:4\n"
			   "custody_events=6\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_quality",
			   "dataset=count-table\n"
			   "checks=7\n"
			   "passed=7\n"
			   "schema_fields=8\n"
			   "drift=0\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_protocol",
			   "protocol=protocol:RUN-042:recovery\n"
			   "ethics=approved\n"
			   "analysis_plan=locked\n"
			   "checks=5\n"
			   "amendments=1\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_soplog",
			   "sop=SOP-LIB-PREP:v2\n"
			   "steps=4\n"
			   "completed=4\n"
			   "deviation=handled\n"
			   "training=qualified\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_exper",
			   "campaign=campaign:RUN-042:param-sweep\n"
			   "trials=4\n"
			   "best=trial-3\n"
			   "metric_delta=12\n"
			   "result_review=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_labops",
			   "instruments=2\n"
			   "reservations=1\n"
			   "reagent_lots=2\n"
			   "inventory_transactions=6\n"
			   "maintenance=passed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_training",
			   "personnel=3\n"
			   "requirements=4\n"
			   "competency_checks=3\n"
			   "gaps=0\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("samples=ready")) return 1;
	if (!rp_append_status("quality=ready")) return 1;
	if (!rp_append_status("protocol=ready")) return 1;
	if (!rp_append_status("sop=ready")) return 1;
	if (!rp_append_status("experiment=ready")) return 1;
	if (!rp_append_status("labops=ready")) return 1;
	if (!rp_append_status("training=ready")) return 1;
	printf("rp_lab: samples=4 quality_checks=7 protocol_checks=5 trials=4 labops=ready status=ready\n");
	return 0;
}
