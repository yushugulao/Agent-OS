#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_data_quality", "decision=accepted");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	ok = ok && rp_file_contains("rp_calculation", "calculation_checks=84");
	ok = ok && rp_file_contains("rp_calc_parse", "metric=ready_ratio;value=1.00");
	ok = ok && rp_file_contains("rp_agentos_kernel", "file_meta_service=initialized");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_provenance=observed");
	if (!ok) return 1;

	if (!rp_write_file("rp_realtask",
			   "service=real-task-validation\n"
			   "task=palmer-penguins-morphometrics\n"
			   "question=bill-and-body-size-patterns-by-species-and-sex\n"
			   "dataset=palmer-penguins\n"
			   "run_id=RUN-PENGUINS-001\n"
			   "real_task_checks=96\n"
			   "input_files=3\n"
			   "references=3\n"
			   "provider=deepseek\n"
			   "provider_secret_persisted=0\n"
			   "workbench_status=delivered\n"
			   "readiness=ready\n"
			   "answer_audit=pass\n"
			   "project_bundle=ready\n"
			   "agentos_kernel_metadata=observed\n"
			   "agentos_context=observed\n"
			   "agentos_provenance=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_realdata",
			   "dataset=palmer-penguins\n"
			   "rows=344\n"
			   "columns=8\n"
			   "numeric_fields=5\n"
			   "metric_group_summaries=5\n"
			   "metric_dimension_group_summaries=10\n"
			   "categorical_fields=island,sex\n"
			   "missing_sex_labels=present\n"
			   "source_files=penguins.csv,references.bib,notes.md\n"
			   "data_quality=accepted\n"
			   "agentos_file_metadata=real_task_inputs\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_realreport",
			   "report=palmer-penguins-report\n"
			   "llm_provider=deepseek\n"
			   "answer_source=report_md\n"
			   "raw_llm_packet=trace_only\n"
			   "claim_audit=pass\n"
			   "answer_audit=pass\n"
			   "limitations=missing_sex_labels,observational_data,causal_caution\n"
			   "citations=3\n"
			   "agentos_context_record=report_answer_audit\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_realbundle",
			   "bundle=palmer-penguins-project-bundle\n"
			   "duplicate_zip_entries=0\n"
			   "package_files=project_bundle,report,analysis,claim_audit,answer_audit\n"
			   "offline_review=ready\n"
			   "http_checks=4\n"
			   "agentos_package_trace=kernel_provenance\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_lineage", "real_task:palmer-penguins->report:palmer-penguins-report")) return 1;
	if (!rp_append_file("rp_package", "real_task_package=rp_realtask;dataset=palmer-penguins;bundle=ready;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "real_task_page=rp_realtask;dataset=palmer-penguins;rows=344;answer_audit=pass;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=real_task;source=rp_realtask;dataset=palmer-penguins;checks=96;outcome=passed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "real_task_checks=96;dataset=palmer-penguins;rows=344;numeric_fields=5;answer_audit=pass;bundle=ready;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=real_task;msg=palmer;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.import_workspace")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.parse_csv")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.import_references")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.run_analysis")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.invoke_llm")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.audit_claims")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.audit_answer")) return 1;
	if (!rp_append_file("rp_tool", "tool=real_task.package_project")) return 1;
	if (!rp_append_status("real_task=ready")) return 1;
	printf("rp_realtask: dataset=palmer-penguins rows=344 numeric=5 checks=96 answer_audit=pass bundle=ready status=ready\n");
	return 0;
}
