#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_package", "status=ready")) return 1;
	if (!rp_file_contains("rp_dataver", "release_candidate=v2")) return 1;
	if (!rp_file_contains("rp_revision", "draft_versions=3")) return 1;
	if (!rp_file_contains("rp_repro", "notebook_replay=passed")) return 1;
	if (!rp_file_contains("rp_risk", "open_risks=0")) return 1;
	if (!rp_file_contains("rp_mail", "to=delta")) return 1;
	if (!rp_write_file("rp_diff",
			   "run_id=RUN-042\n"
			   "base=v1\n"
			   "candidate=v2\n"
			   "changed_items=20\n"
			   "report_sections=8\n"
			   "data_snapshots=3\n"
			   "figure_exports=3\n"
			   "risk_items=3\n"
			   "repro_checks=9\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_delta",
			   "release_delta=RUN-042:v1..v2\n"
			   "items=20\n"
			   "reviews=1\n"
			   "accepted=1\n"
			   "blocked=0\n"
			   "package=ready\n"
			   "risk=accepted\n"
			   "repro=ready\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=delta;msg=16;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=delta.write_diff")) return 1;
	if (!rp_append_file("rp_tool", "tool=delta.review_release_delta")) return 1;
	if (!rp_append_file("rp_tool", "tool=delta.attach_repro_risk")) return 1;
	if (!rp_append_status("diff=ready")) return 1;
	if (!rp_append_status("delta=ready")) return 1;
	printf("rp_delta: items=20 reviews=1 decision=accepted status=ready\n");
	return 0;
}
