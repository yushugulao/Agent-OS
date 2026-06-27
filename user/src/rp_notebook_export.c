#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_package", "status=ready");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_nbexec", "executed_cells=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "real_artifact_items=5");
	ok = ok && rp_file_contains("rp_report_text", "RUN-042 Recovery Report");
	ok = ok && rp_file_contains("rp_chart_data", "chart=stage_attempts");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	if (!ok) return 1;

	if (!rp_append_file("rp_nbexec", "notebook=reproducible-analysis.ipynb;run=RUN-042;cells=5;status=ready")) return 1;
	if (!rp_append_file("rp_nbexec", "notebook=manual-executable-analysis.md;run=RUN-042;cells=3;status=ready")) return 1;
	if (!rp_append_file("rp_nbexec", "cell=2;type=code;source=load_artifact_paths;refs=rp_artifact_manifest")) return 1;
	if (!rp_append_file("rp_nbexec", "cell=6;type=code;source=llm_trace_roundtrip;refs=rp_llm_packets")) return 1;
	if (!rp_append_file("rp_nbexec", "execution=RUN-042:repro-notebook;status=passed;outputs=4")) return 1;
	if (!rp_append_file("rp_nbexec", "execution=RUN-042:manual-notebook;status=passed;outputs=2")) return 1;
	if (!rp_append_file("rp_nbexec", "exports=2")) return 1;
	if (!rp_append_file("rp_nbexec", "export=notebook_json;path=reproducible-analysis.ipynb;status=ready")) return 1;
	if (!rp_append_file("rp_nbexec", "export=notebook_markdown;path=manual-executable-analysis.md;status=ready")) return 1;
	if (!rp_append_file("rp_nbexec", "download=repro_notebook;path=reproducible-analysis.ipynb;status=ready")) return 1;
	if (rp_host_seed_has("kind=notebook_export")) {
		char format[32];
		char run_id[48];
		char line[160];
		if (!rp_host_seed_copy_value_for_kind("kind=notebook_export", "format=", format, sizeof(format))) {
			rp_copy_text(format, sizeof(format), "ipynb");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=notebook_export", "run_id=", run_id, sizeof(run_id))) {
			rp_copy_text(run_id, sizeof(run_id), "RUN-900");
		}
		rp_copy_text(line, sizeof(line), "host_action_notebook_export=ready;run_id=");
		rp_append_text(line, sizeof(line), run_id);
		rp_append_text(line, sizeof(line), ";format=");
		rp_append_text(line, sizeof(line), format);
		rp_append_text(line, sizeof(line), ";path=reproducible-analysis.ipynb;source=rp_host_action_seed");
		if (!rp_append_file("rp_nbexec", line)) return 1;
		if (!rp_append_host_action_line("rp_nbexec", "host_action_notebook_format=", format)) return 1;
	}
	if (rp_host_seed_has_workbench_action()) {
		char value[96];
		if (!rp_append_file("rp_nbexec", "host_action_notebook_workbench=rp_runner")) return 1;
		if (!rp_host_seed_copy_workbench_value("workbench=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "usable-workbench:RUN-900");
		}
		if (!rp_append_host_action_line("rp_nbexec", "host_action_notebook_workbench_id=", value)) return 1;
		if (rp_host_seed_has("kind=workbench_brief") ||
		    rp_host_seed_has("kind=workbench_evidence_dossier") ||
		    rp_host_seed_has("kind=workbench_evidence_graph") ||
		    rp_host_seed_has("kind=workbench_citations") ||
		    rp_host_seed_has("kind=workbench_manuscript") ||
		    rp_host_seed_has("kind=workbench_runbook") ||
		    rp_host_seed_has("kind=workbench_timeline") ||
		    rp_host_seed_has("kind=workbench_file_manifest") ||
		    rp_host_seed_has("kind=workbench_file_verify") ||
		    rp_host_seed_has("kind=workbench_export")) {
			if (!rp_append_file("rp_nbexec", "host_action_notebook_workbench_docs=ready")) return 1;
		}
	}
	if (!rp_append_file("rp_repro", "notebook_export=rp_nbexec;exports=2;downloadable_units=4;download=reproducible-analysis.ipynb")) return 1;
	if (!rp_append_file("rp_ack", "ack=notebook_export;msg=notebook;status=ready")) return 1;
	if (!rp_append_status("notebook_export=ready")) return 1;
	if (!rp_append_status("notebook_package=ready")) return 1;
	if (!rp_append_status("download_manifest=ready")) return 1;
	printf("rp_notebook_export: notebooks=2 cells=8 downloads=4 status=ready\n");
	return 0;
}
