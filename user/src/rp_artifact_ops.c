#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_taskrec", "stage=align");
	ok = ok && rp_file_contains("rp_data", "failed_stage=align");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_retrylog", "final_result=recovered");
	ok = ok && rp_file_contains("rp_completion", "status=ready");
	if (!ok) return 1;
	if (!rp_write_file("rp_input",
			   "input=RUN-042:sample-fastq\n"
			   "source=ordinary_ucore_file\n"
			   "records=2\n"
			   "bytes=96\n"
			   "checksum=input-demo-042\n"
			   "custom_requests=3\n"
			   "custom_run=usable-run:RUN-900\n"
			   "custom_run_2=usable-run:RUN-901\n"
			   "custom_run_3=usable-run:RUN-902\n"
			   "custom_title=Browser started study\n"
			   "custom_question=Can this platform run a custom research task?\n"
			   "custom_provider=template\n"
			   "custom_dataset_rows=3\n"
			   "custom_dataset_rows_total=9\n"
			   "custom_row=S1,control,12\n"
			   "custom_row=S2,treatment,19\n"
			   "custom_row=S3,treatment,21\n"
			   "custom_row_2=S4,control,8\n"
			   "custom_row_2=S5,treatment,13\n"
			   "custom_row_3=S6,control,30\n"
			   "custom_row_3=S7,treatment,28\n"
			   "custom_outputs=stage_dag,analysis,report,review,export\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_input_fastq",
			   "@RUN-042-read-1\n"
			   "ACGTACGTACGT\n"
			   "+\n"
			   "FFFFFFFFFFFF\n"
			   "@RUN-042-read-2\n"
			   "ACGTTCGTACGA\n"
			   "+\n"
			   "FFFFFFFFFFFF\n")) {
		return 1;
	}
	if (!rp_file_contains("rp_input_fastq", "@RUN-042-read-1")) return 1;
	if (!rp_write_file("rp_stage_dag",
			   "dag=lab-gene-x-nightly\n"
			   "stage=ingest;deps=none;cache=miss;status=done\n"
			   "stage=align;deps=ingest;cache=miss;status=recovered\n"
			   "stage=profile;deps=align;cache=hit;status=done\n"
			   "stage=review;deps=profile;cache=miss;status=done\n"
			   "stage=package;deps=review;cache=miss;status=ready\n"
			   "edges=4\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_stage_log",
			   "run_id=RUN-042\n"
			   "log=ingest read rp_input_fastq records=2 status=ok\n"
			   "log=align first_attempt status=failed reason=tool_output_missing\n"
			   "log=align retry attempt=2 status=recovered artifact=rp_artifact\n"
			   "log=profile cache=hit source=rp_compute status=ok\n"
			   "log=review claims=8 evidence_links=5 status=accepted\n"
			   "lines=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_artifact",
			   "artifact=artifact:RUN-042:align-recovered\n"
			   "input=rp_input_fastq\n"
			   "stage=align\n"
			   "attempt=2\n"
			   "records=2\n"
			   "derived_variants=2\n"
			   "status=recovered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_report_text",
			   "# RUN-042 Recovery Report\n"
			   "The align stage failed because the first tool output was missing.\n"
			   "Recovery reran only the align stage and reused cached profile data.\n"
			   "Evidence links: rp_evidence, rp_claimrec, rp_provpath, rp_stage_log.\n"
			   "Release state: ready.\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_chart_data",
			   "chart=stage_attempts\n"
			   "stage,attempts,status\n"
			   "ingest,1,done\n"
			   "align,2,recovered\n"
			   "profile,1,cached\n"
			   "review,1,accepted\n"
			   "package,1,ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_runner",
			   "runner=plain-ucore-stage-runner\n"
			   "inputs=2\n"
			   "stages=5\n"
			   "dag_edges=4\n"
			   "failed_stages=1\n"
			   "retries=1\n"
			   "cache_hits=1\n"
			   "logs=5\n"
			   "artifacts=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=artifact_ops;msg=artifact;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=research_request;msg=input;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_input;target=rp_input;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.read_input;target=rp_input_fastq;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_dag;target=rp_stage_dag;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_log;target=rp_stage_log;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_artifact;target=rp_artifact;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_report;target=rp_report_text;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_chart;target=rp_chart_data;status=ok")) return 1;
	if (!rp_append_status("input=ready")) return 1;
	if (!rp_append_status("runner=ready")) return 1;
	if (!rp_append_status("stage_dag=ready")) return 1;
	if (!rp_append_status("artifact_ops=ready")) return 1;
	if (!rp_append_status("research_request=ready")) return 1;
	printf("rp_artifact_ops: inputs=2 stages=5 retries=1 artifacts=4 custom_requests=3 status=ready\n");
	return 0;
}
