#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_plan", "workflow=lab-gene-x")) return 1;
	if (!rp_file_contains("rp_mail", "to=analyst")) return 1;
	if (!rp_write_file("rp_data",
			   "datasets=4\nstatistics=6\nfigures=3\nfailed_stage=align\nstatus=needs_repair\n")) {
		return 1;
	}
	if (!rp_write_file("rp_datadic",
			   "dataset=count-table\n"
			   "schema_fields=17\n"
			   "primary_keys=2\n"
			   "controlled_terms=7\n"
			   "transform_specs=4\n"
			   "schema_drift=0\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_compute",
			   "notebook_cells=4\n"
			   "stat_tests=6\n"
			   "figures=3\n"
			   "calculation_jobs=1\n"
			   "replay=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_fail",
			   "failed_stage=align\n"
			   "failure_class=tool_output_missing\n"
			   "severity=medium\n"
			   "recoverable=1\n"
			   "recommended_action=minimal_rerun\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=analyst;msg=2;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=analyst.profile_dataset;target=rp_datadic;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=analyst.replay_notebook;target=rp_compute;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=analyst.classify_failure;target=rp_fail;status=ok")) return 1;
	if (!rp_append_status("analyst=ready")) return 1;
	if (!rp_append_status("datadict=ready")) return 1;
	if (!rp_append_status("compute=ready")) return 1;
	if (!rp_append_status("failure=ready")) return 1;
	printf("rp_analyst: datasets=4 statistics=6 figures=3 schema_fields=17 failure=tool_output_missing status=ready\n");
	return 0;
}
