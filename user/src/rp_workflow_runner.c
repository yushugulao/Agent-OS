#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_log", "first_attempt status=failed");
	ok = ok && rp_file_contains("rp_input_fastq", "@RUN-042-read-1");
	ok = ok && rp_file_contains("rp_artifact", "normalized_read=RUN-042-read-2");
	ok = ok && rp_file_contains("rp_artifact", "align_row=RUN-042-read-2;diffs=2");
	ok = ok && rp_file_contains("rp_artifact", "\"reads\":2");
	ok = ok && rp_file_contains("rp_artifact", "geneB=11");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_runner", "stages=5");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_input", "custom_dataset_rows=3");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_knowledge", "library_tag=reusable");
	if (!ok) return 1;

	if (!rp_write_file("rp_stage_state",
			   "run_id=RUN-042\n"
			   "stage=ingest;order=1;input=rp_input_fastq;attempts=1;state=done\n"
			   "stage=align;order=2;input=rp_artifact:rp_normalized_fastq;attempts=2;state=recovered\n"
			   "stage=profile;order=3;input=rp_artifact:rp_align_table;attempts=1;state=cached\n"
			   "stage=review;order=4;input=rp_claimrec;attempts=1;state=accepted\n"
			   "stage=package;order=5;input=rp_report_text;attempts=1;state=ready\n"
			   "command=ingest:read_fastq;output=rp_artifact:rp_normalized_fastq\n"
			   "command=align:agent-align;output=rp_artifact:rp_align_table;first_status=failed;second_status=recovered\n"
			   "command=profile:derive_metrics;output=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv;cache=hit\n"
			   "command=review:claim_review;output=rp_review;claims=8\n"
			   "command=package:assemble;output=rp_package;report=rp_report_text\n"
			   "dependency_checks=5\n"
			   "outputs=5\n"
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
			   "cache_policy=content_keyed\n"
			   "reuse_stage=profile\n"
			   "refreshed_stage=align\n"
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
			   "failure_reason=tool_output_missing\n"
			   "rerun_inputs=rp_input_fastq\n"
			   "rerun_outputs=rp_artifact\n"
			   "skip_stages=ingest,profile,review,package\n"
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
			   "decision=retry_align_only;reason=tool_output_missing\n"
			   "report_ref=rp_report_text\n"
			   "evidence_ref=rp_evidence\n"
			   "events=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_artifact_manifest",
			   "run_id=RUN-042\n"
			   "manifest=plain-ucore-runner-artifacts\n"
			   "record=1;kind=input;path=rp_input_fastq;status=ready\n"
			   "record=2;kind=prepared_input;path=rp_artifact;section=rp_normalized_fastq;status=ready\n"
			   "record=3;kind=alignment;path=rp_artifact;section=rp_align_table;status=ready\n"
			   "record=4;kind=metrics;path=rp_artifact;section=rp_metrics_json;status=ready\n"
			   "record=5;kind=counts;path=rp_artifact;section=rp_gene_counts_csv;status=ready\n"
			   "record=6;kind=artifact;path=rp_artifact;status=recovered\n"
			   "record=7;kind=report;path=rp_report_text;status=ready\n"
			   "record=8;kind=chart;path=rp_chart_data;status=ready\n"
			   "record=9;kind=archive;path=rp_artifact;section=rp_archive_manifest;status=ready\n"
			   "support=stage_log;path=rp_stage_log;status=ready\n"
			   "support=package_index;path=rp_package;status=ready\n"
			   "manifest_records=4\n"
			   "real_artifact_items=5\n"
			   "support_entries=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "custom_runs=3")) return 1;
	if (!rp_append_file("rp_runner", "custom_run=usable-run:RUN-900")) return 1;
	if (!rp_append_file("rp_runner", "custom_run_2=usable-run:RUN-901")) return 1;
	if (!rp_append_file("rp_runner", "custom_run_3=usable-run:RUN-902")) return 1;
	if (!rp_append_file("rp_runner", "dynamic_input_runs=4")) return 1;
	if (!rp_append_file("rp_runner", "dynamic_run=usable-run:RUN-904;source=api;status=queued;next=validate")) return 1;
	if (!rp_append_file("rp_runner", "dynamic_replay_plan=RUN-900->RUN-904;shared_template=usable-template:workspace-900")) return 1;
	if (!rp_append_file("rp_runner", "human_review_id=usable-review:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "human_review_decision=needs_revision")) return 1;
	if (!rp_append_file("rp_runner", "revision_task_id=usable-revision-task:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "revision_requested_changes=2")) return 1;
	if (!rp_append_file("rp_runner", "revision_change=methods_retry_scope;status=applied")) return 1;
	if (!rp_append_file("rp_runner", "revision_change=chart_caption;status=applied")) return 1;
	if (!rp_append_file("rp_runner", "revision_status=completed")) return 1;
	if (!rp_append_file("rp_runner", "revision_run=usable-run:RUN-900-rev1")) return 1;
	if (!rp_append_file("rp_runner", "revision_delta=rp_revision")) return 1;
	if (!rp_append_file("rp_runner", "revision_review_source=rp_review2")) return 1;
	if (!rp_append_file("rp_runner", "revision_reason=reviewer_requested_changes")) return 1;
	if (!rp_append_file("rp_runner", "revision_artifacts=12")) return 1;
	if (!rp_append_file("rp_runner", "custom_source=rp_input")) return 1;
	if (!rp_append_file("rp_runner", "custom_dataset_rows=3")) return 1;
	if (!rp_append_file("rp_runner", "custom_dataset_rows_total=9")) return 1;
	if (!rp_append_file("rp_runner", "custom_stages=5")) return 1;
	if (!rp_append_file("rp_runner", "custom_artifacts=36")) return 1;
	if (!rp_append_file("rp_runner", "custom_agent_messages=21")) return 1;
	if (!rp_append_file("rp_runner", "custom_agent_decisions=15")) return 1;
	if (!rp_append_file("rp_runner", "library_source_count=1")) return 1;
	if (!rp_append_file("rp_runner", "library_tag=reusable")) return 1;
	if (!rp_append_file("rp_runner", "library_source=usable-source:library2026:1")) return 1;
	if (!rp_append_file("rp_runner", "library_backed_run=usable-run:RUN-900")) return 1;
	if (!rp_append_file("rp_runner", "bibliography_entries=3")) return 1;
	if (!rp_append_file("rp_runner", "citation_plan_entries=3")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis=mean_control:12,mean_treatment:20,stronger:treatment")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis_2=mean_control:8,mean_treatment:13,stronger:treatment")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis_3=mean_control:30,mean_treatment:28,stronger:control")) return 1;
	if (!rp_append_file("rp_runner", "custom_report=custom research task completed from ordinary uCore files")) return 1;
	if (!rp_append_file("rp_runner", "custom_export=review_html")) return 1;
	if (!rp_append_file("rp_runner", "custom_contains=Stage DAG,Agent Decisions,Artifacts,LLM Relay")) return 1;
	if (!rp_append_file("rp_runner", "custom_status=ok")) return 1;
	if (!rp_append_file("rp_runner", "custom_batch_status=ok")) return 1;
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		if (!rp_host_seed_copy_value("run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		if (!rp_append_host_action_line("rp_runner", "host_action_run=usable-run:", seed_run)) return 1;
		if (!rp_append_file("rp_runner", "host_action_kind=research_run")) return 1;
		if (!rp_append_file("rp_runner", "host_action_stages=5")) return 1;
		if (!rp_append_file("rp_runner", "host_action_artifacts=6")) return 1;
		if (!rp_append_file("rp_runner", "host_action_report=host action research task completed from seeded inbox")) return 1;
		if (!rp_append_file("rp_runner", "host_action_status=completed")) return 1;
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		if (!rp_append_file("rp_runner", "host_action_compare=plain_ucore;status=ready")) return 1;
	}
	if (rp_host_seed_has("kind=host_workflow")) {
		if (!rp_append_file("rp_runner", "host_action_workflow=executed;status=ready")) return 1;
	}
	if (rp_host_seed_has("kind=revision_run")) {
		if (!rp_append_file("rp_runner", "host_action_revision_run=usable-run:RUN-900-rev2;status=completed")) return 1;
	}
	if (!rp_append_file("rp_runner", "real_artifact_items=5")) return 1;
	if (!rp_append_file("rp_runner", "derived_alignment=rp_artifact:rp_align_table")) return 1;
	if (!rp_append_file("rp_runner", "derived_metrics=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv")) return 1;
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
	if (!rp_append_status("revision_task=ready")) return 1;
	printf("rp_workflow_runner: stages=5 events=8 retries=1 cache_hits=1 custom_runs=3 status=ready\n");
	return 0;
}
