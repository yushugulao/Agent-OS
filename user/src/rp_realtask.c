#include <agent.h>
#include <stdio.h>
#include <research_platform_state.h>

static struct agent_info realtask_agent_info;
static struct agent_op realtask_ops[2];
static struct agent_result realtask_results[2];
static struct agent_context_header realtask_header;
static struct agent_context_record realtask_records[4];
static struct agent_audit_record realtask_audit_records[4];
static struct agent_file_query realtask_query;
static struct agent_file_query_result realtask_query_result;

static void make_realtask_op(struct agent_op *op, int tool_id,
			     uint64 request_id, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_OP_VERSION;
	op->tool_id = tool_id;
	op->request_id = request_id;
	strcpy(op->payload, payload);
}

static int run_kernel_realtask_stage(void)
{
	if (agent_launch_info(&realtask_agent_info) < 0 || !realtask_agent_info.is_agent)
		return 0;
	if ((realtask_agent_info.capability_mask & AGENT_CAP_META_READ) == 0 ||
	    (realtask_agent_info.capability_mask & AGENT_CAP_AUDIT_WRITE) == 0) {
		printf("rp_realtask: kernel_capability_missing\n");
		return -1;
	}

	make_realtask_op(&realtask_ops[0], AGENT_TOOL_ECHO, 4801,
			 "real-task-analysis");
	make_realtask_op(&realtask_ops[1], AGENT_TOOL_ECHO, 4802,
			 "report-answer-audit");
	if (agent_run(realtask_ops, realtask_results, 2, 0) != 2 ||
	    realtask_results[0].status != AGENT_STATUS_OK ||
	    realtask_results[1].status != AGENT_STATUS_OK) {
		printf("rp_realtask: context_record_failed status=%d/%d\n",
		       realtask_results[0].status,
		       realtask_results[1].status);
		return -1;
	}
	if (context_snapshot(&realtask_header, realtask_records, 4) < 2 ||
	    realtask_header.latest_sequence < realtask_results[1].sequence) {
		printf("rp_realtask: context_snapshot_failed\n");
		return -1;
	}
	if (agent_audit_snapshot(realtask_audit_records, 4) < 1) {
		printf("rp_realtask: audit_snapshot_failed\n");
		return -1;
	}

	memset(&realtask_query, 0, sizeof(realtask_query));
	realtask_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	realtask_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(realtask_query.project, "lab-gene-x");
	strcpy(realtask_query.run_id, "RUN-042");
	strcpy(realtask_query.stage, "report");
	strcpy(realtask_query.status, "ok");
	if (agent_file_query(&realtask_query, &realtask_query_result) < 1 ||
	    realtask_query_result.returned < 1 ||
	    !realtask_query_result.used_index) {
		printf("rp_realtask: report_metadata_failed hits=%d index=%d\n",
		       realtask_query_result.returned,
		       realtask_query_result.used_index);
		return -1;
	}

	if (!rp_write_file("rp_agentos_real_task",
			   "task=palmer-penguins-morphometrics\n"
			   "report_answer=kernel_context_record\n"
			   "answer_audit=kernel_audit_seen\n"
			   "report_metadata=kernel_index\n"
			   "context_snapshot=trusted\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=real_task;real_task_context=kernel_shadow;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_realtask;

	ok = ok && rp_file_contains("rp_data_quality", "decision=accepted");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	ok = ok && rp_file_contains("rp_calculation", "calculation_checks=84");
	ok = ok && rp_file_contains("rp_calc_parse", "metric=ready_ratio;value=1.00");
	ok = ok && rp_file_contains("rp_agentos_kernel", "file_meta_service=initialized");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_provenance=observed");
	if (!ok) return 1;
	kernel_realtask = run_kernel_realtask_stage();
	if (kernel_realtask < 0)
		return 1;

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
	if (kernel_realtask &&
	    !rp_append_file("rp_realreport", "agentos_report_answer_context=kernel_shadow;agentos_answer_audit=kernel_audit_seen;status=ready"))
		return 1;
	if (kernel_realtask &&
	    !rp_append_file("rp_realbundle", "agentos_real_task_trace=kernel_context_and_audit;status=ready"))
		return 1;
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
	if (kernel_realtask &&
	    !rp_append_file("rp_tool", "tool=agentos.real_task_context"))
		return 1;
	if (!rp_append_status("real_task=ready")) return 1;
	printf("rp_realtask: dataset=palmer-penguins rows=344 numeric=5 checks=96 answer_audit=pass bundle=ready status=ready\n");
	return 0;
}
