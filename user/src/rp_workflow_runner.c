#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_log", "first_attempt status=failed");
	ok = ok && rp_file_contains("rp_input_fastq", "@RUN-042-read-1");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_runner", "stages=5");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_dataset_rows=3");
	if (!ok) return 1;

	if (!rp_write_file("rp_stage_state",
			   "run_id=RUN-042\n"
			   "stage=ingest;order=1;input=rp_input_fastq;attempts=1;state=done\n"
			   "stage=align;order=2;input=rp_input_fastq;attempts=2;state=recovered\n"
			   "stage=profile;order=3;input=rp_artifact;attempts=1;state=cached\n"
			   "stage=review;order=4;input=rp_claimrec;attempts=1;state=accepted\n"
			   "stage=package;order=5;input=rp_report_text;attempts=1;state=ready\n"
			   "stages=5\n"
			   "done=5\n"
			   "recovered=1\n"
			   "cached=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_cache_index",
			   "run_id=RUN-042\n"
			   "cache_key=ingest:RUN-042;state=miss;source=rp_input_fastq\n"
			   "cache_key=align:RUN-042;state=refreshed;source=rp_artifact\n"
			   "cache_key=profile:RUN-042;state=hit;source=rp_compute\n"
			   "cache_key=review:RUN-042;state=miss;source=rp_claimrec\n"
			   "cache_key=package:RUN-042;state=miss;source=rp_report_text\n"
			   "cache_records=5\n"
			   "cache_hits=1\n"
			   "cache_misses=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_retry_plan",
			   "run_id=RUN-042\n"
			   "retry_items=1\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "attempts=2\n"
			   "dedupe_key=RUN-042:align\n"
			   "minimal_rerun=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_run_events",
			   "run_id=RUN-042\n"
			   "event=1;stage=ingest;action=read_input;status=done\n"
			   "event=2;stage=align;action=first_attempt;status=failed\n"
			   "event=3;stage=align;action=retry_scheduled;status=ready\n"
			   "event=4;stage=align;action=rerun;status=recovered\n"
			   "event=5;stage=profile;action=cache_lookup;status=hit\n"
			   "event=6;stage=review;action=claim_review;status=accepted\n"
			   "event=7;stage=package;action=manifest_ready;status=ready\n"
			   "event=8;stage=run;action=finish;status=ready\n"
			   "events=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_artifact_manifest",
			   "run_id=RUN-042\n"
			   "manifest=plain-ucore-runner-artifacts\n"
			   "record=1;kind=input;path=rp_input_fastq;status=ready\n"
			   "record=2;kind=artifact;path=rp_artifact;status=recovered\n"
			   "record=3;kind=report;path=rp_report_text;status=ready\n"
			   "record=4;kind=chart;path=rp_chart_data;status=ready\n"
			   "manifest_records=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "custom_run=usable-run:RUN-900")) return 1;
	if (!rp_append_file("rp_runner", "custom_source=rp_input")) return 1;
	if (!rp_append_file("rp_runner", "custom_dataset_rows=3")) return 1;
	if (!rp_append_file("rp_runner", "custom_stages=5")) return 1;
	if (!rp_append_file("rp_runner", "custom_artifacts=12")) return 1;
	if (!rp_append_file("rp_runner", "custom_agent_messages=7")) return 1;
	if (!rp_append_file("rp_runner", "custom_agent_decisions=5")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis=mean_control:12,mean_treatment:20,stronger:treatment")) return 1;
	if (!rp_append_file("rp_runner", "custom_report=custom research task completed from ordinary uCore files")) return 1;
	if (!rp_append_file("rp_runner", "custom_export=review_html")) return 1;
	if (!rp_append_file("rp_runner", "custom_contains=Stage DAG,Agent Decisions,Artifacts,LLM Relay")) return 1;
	if (!rp_append_file("rp_runner", "custom_status=ok")) return 1;
	if (!rp_append_file("rp_ack", "ack=workflow_runner;msg=runner;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=custom_research;msg=runner;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.read_dag;target=rp_stage_dag;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.read_input;target=rp_input_fastq;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_stage_state;target=rp_stage_state;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_cache_index;target=rp_cache_index;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_retry_plan;target=rp_retry_plan;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_manifest;target=rp_artifact_manifest;status=ok")) return 1;
	if (!rp_append_status("workflow_runner=ready")) return 1;
	if (!rp_append_status("stage_state=ready")) return 1;
	if (!rp_append_status("cache_index=ready")) return 1;
	if (!rp_append_status("retry_plan=ready")) return 1;
	if (!rp_append_status("artifact_manifest=ready")) return 1;
	if (!rp_append_status("custom_research=ready")) return 1;
	printf("rp_workflow_runner: stages=5 events=8 retries=1 cache_hits=1 custom_runs=1 status=ready\n");
	return 0;
}
