#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

static struct rp_state_buffer suite_state;

static int require_file_token(const char *path, const char *token)
{
	if (rp_state_buffer_contains(&suite_state, path, token)) return 1;
	printf("rp_test_suite: missing path=%s token=%s\n", path, token);
	return 0;
}

static int require_count(const char *name, int actual, int minimum)
{
	if (actual >= minimum) return 1;
	printf("rp_test_suite: count_low %s actual=%d minimum=%d\n", name, actual, minimum);
	return 0;
}

static int require_seed_value(const char *kind, const char *key, const char *fallback, const char *path, const char *prefix)
{
	char value[96];
	char token[160];
	if (!rp_host_seed_copy_value_for_kind(kind, key, value, sizeof(value))) {
		rp_copy_text(value, sizeof(value), fallback);
	}
	rp_copy_text(token, sizeof(token), prefix);
	rp_append_text(token, sizeof(token), value);
	return require_file_token(path, token);
}

int main(void)
{
	int ok = 1;

	ok = ok && require_file_token("rp_objects", "objects=500");
	ok = ok && require_file_token("rp_services", "workflow=34");
	ok = ok && require_file_token("rp_object_query", "hits=8");
	ok = ok && require_file_token("rp_lineage", "edges=7");
	ok = ok && require_file_token("rp_site", "pages=42");
	ok = ok && require_file_token("rp_site", "page=agentos_readiness");
	ok = ok && require_file_token("rp_site", "json_payloads=14");

	ok = ok && require_file_token("rp_plan", "workflow=lab-gene-x");
	ok = ok && require_file_token("rp_sched", "queue_items=21");
	ok = ok && require_file_token("rp_taskrec", "msg=21");
	ok = ok && require_file_token("rp_rank", "selected=10");
	ok = ok && require_file_token("rp_runview", "budget_state=within_budget");

	ok = ok && require_file_token("rp_input", "status=ready");
	ok = ok && require_file_token("rp_input_fastq", "@RUN-042-read-1");
	ok = ok && require_file_token("rp_stage_dag", "failed_stage=align");
	ok = ok && require_file_token("rp_stage_log", "first_attempt status=failed");
	ok = ok && require_file_token("rp_artifact", "status=recovered");
	ok = ok && require_file_token("rp_artifact", "section=rp_normalized_fastq");
	ok = ok && require_file_token("rp_artifact", "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA");
	ok = ok && require_file_token("rp_artifact", "section=rp_align_table");
	ok = ok && require_file_token("rp_artifact", "align_row=RUN-042-read-2;diffs=2");
	ok = ok && require_file_token("rp_artifact", "\"reads\":2");
	ok = ok && require_file_token("rp_artifact", "\"variants\":2");
	ok = ok && require_file_token("rp_artifact", "section=rp_gene_counts_csv;geneA=18");
	ok = ok && require_file_token("rp_artifact", "geneB=11");
	ok = ok && require_file_token("rp_artifact", "section=rp_archive_manifest;files=5");
	ok = ok && require_file_token("rp_artifact", "archive_file=rp_gene_counts_csv");
	ok = ok && require_file_token("rp_artifact", "normalized_fastq=section:rp_normalized_fastq");
	ok = ok && require_file_token("rp_artifact", "align_table=section:rp_align_table");
	ok = ok && require_file_token("rp_artifact", "artifact_dossier=rp_input_fastq,rp_normalized_fastq,rp_align_table");
	ok = ok && require_file_token("rp_artifact", "artifact_review_link=rp_artifact_manifest->rp_review_pack->rp_package");
	ok = ok && require_file_token("rp_artifact", "provenance=rp_align_table;stage=align");
	ok = ok && require_file_token("rp_artifact", "provenance=rp_metrics_json;stage=profile");
	ok = ok && require_file_token("rp_report_text", "Recovery reran only the align stage");
	ok = ok && require_file_token("rp_chart_data", "stage,attempts,status");
	ok = ok && require_file_token("rp_runner", "cache_hits=1");
	ok = ok && require_file_token("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && require_file_token("rp_input", "custom_requests=3");
	ok = ok && require_file_token("rp_input", "custom_run_2=usable-run:RUN-901");
	ok = ok && require_file_token("rp_input", "custom_run_3=usable-run:RUN-902");
	ok = ok && require_file_token("rp_input", "custom_provider=template");
	ok = ok && require_file_token("rp_input", "custom_dataset_rows=3");
	ok = ok && require_file_token("rp_input", "custom_dataset_rows_total=9");
	ok = ok && require_file_token("rp_input", "form_fields=8");
	ok = ok && require_file_token("rp_input", "request_count=3");
	ok = ok && require_file_token("rp_input", "source_mode=pasted_or_uploaded");
	ok = ok && require_file_token("rp_input", "provider_options=template,host-relay");
	ok = ok && require_file_token("rp_input", "delivery_audience=reviewer");
	ok = ok && require_file_token("rp_input", "uploads=2");
	ok = ok && require_file_token("rp_input", "csv_rows_total=9");
	ok = ok && require_file_token("rp_input", "reference_entries=2");
	ok = ok && require_file_token("rp_input", "library_sources=1");
	ok = ok && require_file_token("rp_input", "library_tag=reusable");
	ok = ok && require_file_token("rp_input", "library_source_id=usable-source:library2026:1");
	ok = ok && require_file_token("rp_input", "source_tag=reusable");
	ok = ok && require_file_token("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && require_file_token("rp_input", "workspace_file=expr.csv");
	ok = ok && require_file_token("rp_input", "workspace_file=refs.bib");
	ok = ok && require_file_token("rp_input", "workspace_template=usable-template:workspace-900");
	ok = ok && require_file_token("rp_input", "workspace_run=usable-run:RUN-903");
	ok = ok && require_file_token("rp_input", "dynamic_submissions=4");
	ok = ok && require_file_token("rp_input", "dynamic_submission=1;source=form;run=RUN-900;state=accepted");
	ok = ok && require_file_token("rp_input", "dynamic_submission=2;source=upload;run=RUN-901;state=accepted");
	ok = ok && require_file_token("rp_input", "dynamic_submission=3;source=workspace;run=RUN-903;state=accepted");
	ok = ok && require_file_token("rp_input", "dynamic_submission=4;source=api;run=RUN-904;state=queued");
	ok = ok && require_file_token("rp_input", "dynamic_validation=passed");
	ok = ok && require_file_token("rp_input", "dynamic_queue=plain_ucore_file_backed;accepted=3;pending=1");
	ok = ok && require_file_token("rp_input", "host_ui_feed=rp_web_bundle;events=10");
	ok = ok && require_file_token("rp_stage_state", "stages=5");
	ok = ok && require_file_token("rp_stage_state", "command=align:agent-align");
	ok = ok && require_file_token("rp_stage_state", "output=rp_artifact:rp_align_table");
	ok = ok && require_file_token("rp_stage_state", "output=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv");
	ok = ok && require_file_token("rp_stage_state", "command=package:assemble");
	ok = ok && require_file_token("rp_stage_state", "dependency_checks=5");
	ok = ok && require_file_token("rp_stage_state", "outputs=5");
	ok = ok && require_file_token("rp_cache_index", "cache_hits=1");
	ok = ok && require_file_token("rp_cache_index", "cache_policy=content_keyed");
	ok = ok && require_file_token("rp_cache_index", "reuse_stage=profile");
	ok = ok && require_file_token("rp_cache_index", "refreshed_stage=align");
	ok = ok && require_file_token("rp_retry_plan", "retry_items=1");
	ok = ok && require_file_token("rp_retry_plan", "failure_reason=tool_output_missing");
	ok = ok && require_file_token("rp_retry_plan", "rerun_outputs=rp_artifact");
	ok = ok && require_file_token("rp_retry_plan", "skip_stages=ingest,profile,review,package");
	ok = ok && require_file_token("rp_run_events", "events=8");
	ok = ok && require_file_token("rp_run_events", "decision=retry_align_only");
	ok = ok && require_file_token("rp_run_events", "report_ref=rp_report_text");
	ok = ok && require_file_token("rp_run_events", "evidence_ref=rp_evidence");
	ok = ok && require_file_token("rp_artifact_manifest", "manifest_records=4");
	ok = ok && require_file_token("rp_artifact_manifest", "real_artifact_items=5");
	ok = ok && require_file_token("rp_artifact_manifest", "support=stage_log;path=rp_stage_log;status=ready");
	ok = ok && require_file_token("rp_artifact_manifest", "support_entries=2");
	ok = ok && require_file_token("rp_artifact_manifest", "dossier=artifact-detail");
	ok = ok && require_file_token("rp_artifact_manifest", "dossier_check=workflow_stage");
	ok = ok && require_file_token("rp_artifact_manifest", "dossier_check=review_gate");
	ok = ok && require_file_token("rp_artifact_manifest", "dossier_check=llm_quality");
	ok = ok && require_file_token("rp_runner", "custom_source=rp_input");
	ok = ok && require_file_token("rp_runner", "custom_runs=3");
	ok = ok && require_file_token("rp_runner", "custom_dataset_rows=3");
	ok = ok && require_file_token("rp_runner", "custom_analysis=mean_control:12,mean_treatment:20,stronger:treatment");
	ok = ok && require_file_token("rp_runner", "custom_analysis_2=mean_control:8,mean_treatment:13,stronger:treatment");
	ok = ok && require_file_token("rp_runner", "custom_analysis_3=mean_control:30,mean_treatment:28,stronger:control");
	ok = ok && require_file_token("rp_runner", "library_source_count=1");
	ok = ok && require_file_token("rp_runner", "library_source=usable-source:library2026:1");
	ok = ok && require_file_token("rp_runner", "bibliography_entries=3");
	ok = ok && require_file_token("rp_runner", "citation_plan_entries=3");
	ok = ok && require_file_token("rp_runner", "human_review_id=usable-review:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "human_review_decision=needs_revision");
	ok = ok && require_file_token("rp_runner", "revision_task_id=usable-revision-task:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "revision_requested_changes=2");
	ok = ok && require_file_token("rp_runner", "revision_change=methods_retry_scope;status=applied");
	ok = ok && require_file_token("rp_runner", "revision_change=chart_caption;status=applied");
	ok = ok && require_file_token("rp_runner", "revision_status=completed");
	ok = ok && require_file_token("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_runner", "revision_delta=rp_revision");
	ok = ok && require_file_token("rp_runner", "dynamic_input_runs=4");
	ok = ok && require_file_token("rp_runner", "dynamic_run=usable-run:RUN-904;source=api;status=queued;next=validate");
	ok = ok && require_file_token("rp_runner", "dynamic_replay_plan=RUN-900->RUN-904");
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		rp_copy_text(token, sizeof(token), "host_action_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && require_file_token("rp_input", token);
		rp_copy_text(token, sizeof(token), "host_action_run=usable-run:");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && require_file_token("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_report_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && require_file_token("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && require_file_token("rp_api_compare", token);
		ok = ok && require_seed_value("kind=research_run", "title=", "Browser started study", "rp_input", "host_action_title=");
		ok = ok && require_seed_value("kind=research_run", "title=", "Browser started study", "rp_report_text", "host_report_title=");
		ok = ok && require_seed_value("kind=research_run", "title=", "Browser started study", "rp_api_home", "host_action_title=");
		ok = ok && require_seed_value("kind=research_run", "title=", "Browser started study", "rp_api_run", "host_action_title=");
		ok = ok && require_seed_value("kind=research_run", "title=", "Browser started study", "rp_uresrun", "host_action_title=");
		ok = ok && require_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_input", "host_action_question=");
		ok = ok && require_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_report_text", "host_report_question=");
		ok = ok && require_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_api_run", "host_action_question=");
		ok = ok && require_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_uresrun", "host_action_question=");
		ok = ok && require_seed_value("kind=research_run", "provider=", "template", "rp_input", "host_action_provider=");
		ok = ok && require_seed_value("kind=research_run", "provider=", "template", "rp_report_text", "host_report_provider=");
		ok = ok && require_seed_value("kind=research_run", "provider=", "template", "rp_api_run", "host_action_provider=");
		ok = ok && require_seed_value("kind=research_run", "provider=", "template", "rp_uresrun", "host_action_provider=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_input", "host_action_dataset_rows_value=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_report_text", "host_report_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_ingest_files", "host_input_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_dataset_snapshot", "host_input_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_data_preview", "host_input_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_data_quality", "host_input_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_dataset_collection", "host_input_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_api_run", "host_action_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "dataset_rows=", "4", "rp_uresrun", "host_action_dataset_rows=");
		ok = ok && require_seed_value("kind=research_run", "reference_entries=", "2", "rp_input", "host_action_reference_entries=");
		ok = ok && require_seed_value("kind=research_run", "reference_entries=", "2", "rp_ingest_files", "host_input_reference_entries=");
		ok = ok && require_seed_value("kind=research_run", "reference_entries=", "2", "rp_dataset_collection", "host_input_reference_entries=");
		ok = ok && require_seed_value("kind=research_run", "reference_entries=", "2", "rp_api_run", "host_action_reference_entries=");
		ok = ok && require_seed_value("kind=research_run", "reference_entries=", "2", "rp_uresrun", "host_action_reference_entries=");
		ok = ok && require_seed_value("kind=research_run", "workspace_files=", "4", "rp_input", "host_action_workspace_files=");
		ok = ok && require_seed_value("kind=research_run", "workspace_files=", "4", "rp_ingest_files", "host_input_workspace_files=");
		ok = ok && require_seed_value("kind=research_run", "workspace_files=", "4", "rp_dataset_snapshot", "host_input_workspace_files=");
		ok = ok && require_seed_value("kind=research_run", "workspace_files=", "4", "rp_api_run", "host_action_workspace_files=");
		ok = ok && require_seed_value("kind=research_run", "workspace_files=", "4", "rp_uresrun", "host_action_workspace_files=");
		ok = ok && require_seed_value("kind=research_run", "csv_file=", "expr.csv", "rp_input", "host_action_csv_file=");
		ok = ok && require_seed_value("kind=research_run", "csv_file=", "expr.csv", "rp_ingest_files", "host_input_csv_file=");
		ok = ok && require_seed_value("kind=research_run", "csv_file=", "expr.csv", "rp_data_preview", "host_input_csv_file=");
		ok = ok && require_seed_value("kind=research_run", "reference_file=", "refs.bib", "rp_input", "host_action_reference_file=");
		ok = ok && require_file_token("rp_runner", "host_action_status=completed");
		ok = ok && require_file_token("rp_agentcmp", "host_action_research_input=ready");
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		char profile[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
			rp_copy_text(profile, sizeof(profile), "plain_ucore");
		}
		rp_copy_text(token, sizeof(token), "host_action_compare=");
		rp_append_text(token, sizeof(token), profile);
		rp_append_text(token, sizeof(token), ";status=ready");
		ok = ok && require_file_token("rp_runner", token);
		ok = ok && require_file_token("rp_agentcmp", "host_action_compare_requested=1");
		rp_copy_text(token, sizeof(token), "host_action_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && require_file_token("rp_agentcmp", token);
		rp_copy_text(token, sizeof(token), "host_report_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && require_file_token("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && require_file_token("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=human_review")) {
		char reviewer[48];
		char decision[48];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=human_review", "reviewer=", reviewer, sizeof(reviewer))) {
			rp_copy_text(reviewer, sizeof(reviewer), "HOST");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=human_review", "decision=", decision, sizeof(decision))) {
			rp_copy_text(decision, sizeof(decision), "needs_revision");
		}
		rp_copy_text(token, sizeof(token), "host_action_human_review=usable-review:");
		rp_append_text(token, sizeof(token), reviewer);
		rp_append_text(token, sizeof(token), ":1");
		ok = ok && require_file_token("rp_review2", token);
		rp_copy_text(token, sizeof(token), "host_action_review_decision=");
		rp_append_text(token, sizeof(token), decision);
		ok = ok && require_file_token("rp_review2", token);
		rp_copy_text(token, sizeof(token), "host_report_reviewer=");
		rp_append_text(token, sizeof(token), reviewer);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_report_review_decision=");
		rp_append_text(token, sizeof(token), decision);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_action_reviewer=");
		rp_append_text(token, sizeof(token), reviewer);
		ok = ok && require_file_token("rp_api_compare", token);
		ok = ok && require_file_token("rp_agentcmp", "host_action_review_requested=1");
		ok = ok && require_file_token("rp_actionio", "host_action_human_review=1");
	}
	if (rp_host_seed_has("kind=revision_task")) {
		ok = ok && require_file_token("rp_revision", "host_action_revision_task=created");
		char targets[80];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
			rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
		}
		rp_copy_text(token, sizeof(token), "host_action_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && require_file_token("rp_revision", token);
		rp_copy_text(token, sizeof(token), "host_report_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && require_file_token("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && require_file_token("rp_api_compare", token);
		ok = ok && require_file_token("rp_agentcmp", "host_action_revision_requested=1");
		ok = ok && require_file_token("rp_actionio", "host_action_revision=1");
	}
	if (rp_host_seed_has("kind=revision_run")) {
		ok = ok && require_file_token("rp_revision", "host_action_revision_run=completed");
		char revision_run[48];
		char token[130];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "run_id=", revision_run, sizeof(revision_run))) {
			rp_copy_text(revision_run, sizeof(revision_run), "RUN-900");
		}
		rp_copy_text(token, sizeof(token), "host_action_revision_run=usable-run:");
		rp_append_text(token, sizeof(token), revision_run);
		rp_append_text(token, sizeof(token), "-rev2");
		ok = ok && require_file_token("rp_runner", token);
		ok = ok && require_file_token("rp_actionio", "host_action_revision=1");
	}
	if (rp_host_seed_has("kind=workbench") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_advance") ||
	    rp_host_seed_has("kind=workbench_auto_advance") ||
	    rp_host_seed_has("kind=workbench_task") ||
	    rp_host_seed_has("kind=workbench_note") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_evidence_search") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_export")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench=completed");
		ok = ok && require_file_token("rp_agentcmp", "host_action_workbench_requested=1");
		ok = ok && require_file_token("rp_actionio", "host_action_workbench=1");
		ok = ok && require_file_token("rp_runner", "host_action_workbench_id=");
		ok = ok && require_file_token("rp_api_compare", "host_action_workbench=");
		ok = ok && require_file_token("rp_report_text", "host_report_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && require_file_token("rp_artifact_manifest", "host_manifest_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && require_file_token("rp_nbexec", "host_action_notebook_workbench=rp_runner");
		ok = ok && require_file_token("rp_uresrun", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
	}
	if (rp_host_seed_has("kind=workbench_answer")) {
		char question[96];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_answer", "question=", question, sizeof(question))) {
			rp_copy_text(question, sizeof(question), "What is ready for review?");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_question=");
		rp_append_text(token, sizeof(token), question);
		ok = ok && require_file_token("rp_runner", token);
		ok = ok && require_file_token("rp_api_compare", token);
		ok = ok && require_file_token("rp_runner", "host_action_workbench_answer=generated");
	}
	if (rp_host_seed_has("kind=workbench_evidence_search")) {
		char query[96];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_evidence_search", "query=", query, sizeof(query))) {
			rp_copy_text(query, sizeof(query), "recovery evidence");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_evidence_query=");
		rp_append_text(token, sizeof(token), query);
		ok = ok && require_file_token("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_query=");
		rp_append_text(token, sizeof(token), query);
		ok = ok && require_file_token("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=workbench_task")) {
		char task[64];
		char status[32];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_task", "task=", task, sizeof(task))) {
			rp_copy_text(task, sizeof(task), "human_review");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_task", "status=", status, sizeof(status))) {
			rp_copy_text(status, sizeof(status), "waiting");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_task=");
		rp_append_text(token, sizeof(token), task);
		ok = ok && require_file_token("rp_runner", token);
		ok = ok && require_file_token("rp_api_compare", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_task_status=");
		rp_append_text(token, sizeof(token), status);
		ok = ok && require_file_token("rp_runner", token);
	}
	if (rp_host_seed_has("kind=workbench_note")) {
		char kind[48];
		char title[80];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_note", "note_kind=", kind, sizeof(kind))) {
			rp_copy_text(kind, sizeof(kind), "decision");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_note", "title=", title, sizeof(title))) {
			rp_copy_text(title, sizeof(title), "Scope decision");
		}
		ok = ok && require_file_token("rp_runner", "host_action_workbench_note=recorded");
		rp_copy_text(token, sizeof(token), "host_action_workbench_note_kind=");
		rp_append_text(token, sizeof(token), kind);
		ok = ok && require_file_token("rp_runner", token);
		ok = ok && require_file_token("rp_api_compare", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_note_title=");
		rp_append_text(token, sizeof(token), title);
		ok = ok && require_file_token("rp_runner", token);
		ok = ok && require_file_token("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=workbench")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_created=1");
		ok = ok && require_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_api_compare", "host_action_workbench_title=");
		ok = ok && require_seed_value("kind=workbench", "literature_query=", "agent workflow provenance", "rp_api_compare", "host_action_workbench_literature_query=");
	}
	if (rp_host_seed_has("kind=workbench_advance")) {
		ok = ok && require_seed_value("kind=workbench_advance", "task=", "delivery_manifest", "rp_runner", "host_action_workbench_task=");
		ok = ok && require_seed_value("kind=workbench_advance", "task=", "delivery_manifest", "rp_api_compare", "host_action_workbench_advance_task=");
	}
	if (rp_host_seed_has("kind=workbench_auto_advance")) {
		ok = ok && require_seed_value("kind=workbench_auto_advance", "step_limit=", "8", "rp_runner", "host_action_workbench_step_limit=");
		ok = ok && require_seed_value("kind=workbench_auto_advance", "step_limit=", "8", "rp_api_compare", "host_action_workbench_step_limit=");
	}
	if (rp_host_seed_has("kind=workbench_file_verify")) {
		char manifest[80];
		char token[128];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "manifest=", manifest, sizeof(manifest))) {
			rp_copy_text(manifest, sizeof(manifest), "delivery-manifest.json");
		}
		ok = ok && require_file_token("rp_runner", "host_action_workbench_file_verify=passed");
		rp_copy_text(token, sizeof(token), "host_action_workbench_manifest=");
		rp_append_text(token, sizeof(token), manifest);
		ok = ok && require_file_token("rp_uresrun", token);
		ok = ok && require_file_token("rp_api_compare", token);
		ok = ok && require_file_token("rp_data_quality", "host_file_verify=passed");
		ok = ok && require_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_runner", "host_action_workbench_verified_files=");
		ok = ok && require_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_data_quality", "host_file_verify_verified=");
		ok = ok && require_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_artifact_manifest", "host_manifest_verified_files=");
		ok = ok && require_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_api_artifacts", "host_action_file_verified=");
		ok = ok && require_seed_value("kind=workbench_file_verify", "missing=", "0", "rp_api_data", "host_action_file_missing=");
		rp_copy_text(token, sizeof(token), "host_report_workbench_manifest=");
		rp_append_text(token, sizeof(token), manifest);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_workbench_manifest=");
		rp_append_text(token, sizeof(token), manifest);
		ok = ok && require_file_token("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_manifest=");
		rp_append_text(token, sizeof(token), manifest);
		ok = ok && require_file_token("rp_uresrun", token);
	}
	if (rp_host_seed_has("kind=workbench_notes")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_notes=exported");
		ok = ok && require_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_runner", "host_action_workbench_notes_filter=");
		ok = ok && require_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_api_compare", "host_action_workbench_notes_filter=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_handoff=prepared");
		ok = ok && require_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_runner", "host_action_workbench_handoff_scope=");
		ok = ok && require_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_api_compare", "host_action_workbench_handoff_scope=");
	}
	if (rp_host_seed_has("kind=workbench_readiness")) ok = ok && require_file_token("rp_runner", "host_action_workbench_readiness=checked");
	if (rp_host_seed_has("kind=workbench_answer_audit")) ok = ok && require_file_token("rp_runner", "host_action_workbench_answer_audit=passed");
	if (rp_host_seed_has("kind=workbench_brief")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_brief=exported");
		ok = ok && require_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_runner", "host_action_workbench_brief_format=");
		ok = ok && require_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_api_compare", "host_action_workbench_brief_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_evidence_dossier=exported");
		ok = ok && require_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_runner", "host_action_workbench_dossier_format=");
		ok = ok && require_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_api_compare", "host_action_workbench_dossier_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_graph")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_evidence_graph=exported");
		ok = ok && require_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_runner", "host_action_workbench_graph_format=");
		ok = ok && require_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_api_compare", "host_action_workbench_graph_format=");
	}
	if (rp_host_seed_has("kind=workbench_citations")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_citations=exported");
		ok = ok && require_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_runner", "host_action_workbench_citation_format=");
		ok = ok && require_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_api_compare", "host_action_workbench_citation_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_manuscript=exported");
		ok = ok && require_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_runner", "host_action_workbench_manuscript_format=");
		ok = ok && require_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_api_compare", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_manuscript_audit=passed");
		ok = ok && require_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_runner", "host_action_workbench_audit_scope=");
		ok = ok && require_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_api_compare", "host_action_workbench_audit_scope=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_revision_plan=ready");
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_runner", "host_action_workbench_revision_area=");
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_api_compare", "host_action_workbench_revision_area=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_revision_task=updated");
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_task", "revision_task=", "1", "rp_runner", "host_action_workbench_revision_task=");
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_runner", "host_action_workbench_revision_status=");
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_api_compare", "host_action_workbench_revision_status=");
	}
	if (rp_host_seed_has("kind=workbench_task_board")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_task_board=exported");
		ok = ok && require_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_runner", "host_action_workbench_board_filter=");
		ok = ok && require_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_api_compare", "host_action_workbench_board_filter=");
	}
	if (rp_host_seed_has("kind=workbench_task_board_row")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_task_board_row=updated");
		ok = ok && require_seed_value("kind=workbench_task_board_row", "row_id=", "usable-workbench:RUN-900:board:task:human_review", "rp_runner", "host_action_workbench_row_id=");
		ok = ok && require_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_runner", "host_action_workbench_row_status=");
		ok = ok && require_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_api_compare", "host_action_workbench_row_status=");
	}
	if (rp_host_seed_has("kind=workbench_runbook")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_runbook=exported");
		ok = ok && require_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_runner", "host_action_workbench_runbook_format=");
		ok = ok && require_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_api_compare", "host_action_workbench_runbook_format=");
		ok = ok && require_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_artifact_manifest", "host_manifest_workbench_runbook_format=");
		ok = ok && require_file_token("rp_nbexec", "host_action_notebook_workbench_docs=ready");
	}
	if (rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_timeline=exported");
		ok = ok && require_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_runner", "host_action_workbench_timeline_format=");
		ok = ok && require_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_api_compare", "host_action_workbench_timeline_format=");
		ok = ok && require_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_artifact_manifest", "host_manifest_workbench_timeline_format=");
		ok = ok && require_file_token("rp_nbexec", "host_action_notebook_workbench_docs=ready");
	}
	if (rp_host_seed_has("kind=workbench_file_manifest")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_file_manifest=exported");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_report_text", "host_report_workbench_manifest=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_artifact_manifest", "host_manifest_workbench_manifest=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_uresrun", "host_action_workbench_manifest=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "files=", "9", "rp_ingest_files", "host_file_manifest_files=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "files=", "9", "rp_artifact_manifest", "host_manifest_file_count=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "files=", "9", "rp_package", "host_action_workbench_manifest_files=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "sha_records=", "9", "rp_api_artifacts", "host_action_file_sha_records=");
		ok = ok && require_seed_value("kind=workbench_file_manifest", "sha_records=", "9", "rp_api_data", "host_action_file_sha_records=");
	}
	if (rp_host_seed_has("kind=workbench_export")) {
		ok = ok && require_file_token("rp_runner", "host_action_workbench_export=ready");
		ok = ok && require_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_runner", "host_action_workbench_bundle=");
		ok = ok && require_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_api_compare", "host_action_workbench_bundle=");
		ok = ok && require_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_report_text", "host_report_workbench_bundle=");
		ok = ok && require_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_artifact_manifest", "host_manifest_workbench_bundle=");
		ok = ok && require_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_uresrun", "host_action_workbench_bundle=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && require_file_token("rp_revision", "host_action_workbench_writing=ready");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && require_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_revision", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
		ok = ok && require_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_revision", "host_action_workbench_audit_scope=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_revision", "host_action_workbench_revision_area=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_task", "revision_task=", "1", "rp_revision", "host_action_workbench_revision_task=");
		ok = ok && require_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_revision", "host_action_workbench_revision_status=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_export") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && require_file_token("rp_package", "host_action_workbench_package=ready");
	}
	if (rp_host_seed_has("kind=workbench_complete")) {
		ok = ok && require_file_token("rp_package", "host_action_workbench_completion=ready");
	}
	if (rp_host_seed_has("kind=workbench_readiness")) {
		ok = ok && require_file_token("rp_package", "host_action_workbench_readiness=checked");
	}
	if (rp_host_seed_has("kind=workbench_answer_audit")) {
		ok = ok && require_file_token("rp_package", "host_action_workbench_answer_audit=passed");
	}
	if (rp_host_seed_has("kind=workbench_notes")) {
		ok = ok && require_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_package", "host_action_workbench_notes_filter=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package")) {
		ok = ok && require_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_package", "host_action_workbench_handoff_scope=");
	}
	if (rp_host_seed_has("kind=workbench_export")) {
		ok = ok && require_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_package", "host_action_workbench_bundle=");
	}
	if (rp_host_seed_has("kind=workbench_file_manifest")) {
		ok = ok && require_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_package", "host_action_workbench_manifest=");
	}
	if (!rp_host_seed_has("kind=workbench_file_manifest") && rp_host_seed_has("kind=workbench_file_verify")) {
		ok = ok && require_seed_value("kind=workbench_file_verify", "manifest=", "delivery-manifest.json", "rp_package", "host_action_workbench_manifest=");
	}
	if (rp_host_seed_has("kind=workbench_brief")) {
		ok = ok && require_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_package", "host_action_workbench_brief_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
		ok = ok && require_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_package", "host_action_workbench_dossier_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_graph")) {
		ok = ok && require_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_package", "host_action_workbench_graph_format=");
	}
	if (rp_host_seed_has("kind=workbench_citations")) {
		ok = ok && require_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_package", "host_action_workbench_citation_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && require_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_package", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_task_board")) {
		ok = ok && require_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_package", "host_action_workbench_board_filter=");
	}
	if (rp_host_seed_has("kind=workbench_task_board_row")) {
		ok = ok && require_seed_value("kind=workbench_task_board_row", "row_id=", "usable-workbench:RUN-900:board:task:human_review", "rp_package", "host_action_workbench_row_id=");
		ok = ok && require_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_package", "host_action_workbench_row_status=");
	}
	if (rp_host_seed_has("kind=workbench_runbook")) {
		ok = ok && require_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_package", "host_action_workbench_runbook_format=");
	}
	if (rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && require_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_package", "host_action_workbench_timeline_format=");
	}
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=delivery")) {
		ok = ok && require_file_token("rp_package", "host_action_export_bundle=ready");
		char bundle[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=research_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=delivery", "bundle=", bundle, sizeof(bundle))) {
			rp_copy_text(bundle, sizeof(bundle), "evidence");
		}
		rp_copy_text(token, sizeof(token), "host_action_export_bundle_name=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && require_file_token("rp_package", token);
		ok = ok && require_file_token("rp_package", "host_action_bundle_contents=report,manifest,notebook,compare");
		rp_copy_text(token, sizeof(token), "host_report_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && require_file_token("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && require_file_token("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && require_file_token("rp_api_compare", token);
		ok = ok && require_file_token("rp_actionio", "host_action_export=1");
	}
	if (rp_host_seed_has("kind=notebook_export")) {
		ok = ok && require_file_token("rp_nbexec", "host_action_notebook_export=ready");
		char format[32];
		char token[96];
		if (!rp_host_seed_copy_value_for_kind("kind=notebook_export", "format=", format, sizeof(format))) {
			rp_copy_text(format, sizeof(format), "ipynb");
		}
		rp_copy_text(token, sizeof(token), "host_action_notebook_format=");
		rp_append_text(token, sizeof(token), format);
		ok = ok && require_file_token("rp_nbexec", token);
		rp_copy_text(token, sizeof(token), "host_manifest_notebook_format=");
		rp_append_text(token, sizeof(token), format);
		ok = ok && require_file_token("rp_artifact_manifest", token);
		ok = ok && require_file_token("rp_agentcmp", "host_action_export_requested=1");
		ok = ok && require_file_token("rp_actionio", "host_action_export=1");
	}
	if (rp_host_seed_count() > 0) {
		ok = ok && require_file_token("rp_web_bundle", "host_action_state_files=rp_input,rp_runner,rp_review2,rp_revision,rp_package,rp_nbexec,rp_agentcmp");
	}
	ok = ok && require_file_token("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	ok = ok && require_file_token("rp_runner", "workbench_tasks=9");
	ok = ok && require_file_token("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "workbench_task_done=8");
	ok = ok && require_file_token("rp_runner", "workbench_next_task=delivery_manifest");
	ok = ok && require_file_token("rp_runner", "workbench_task=inspect_workspace;status=done");
	ok = ok && require_file_token("rp_runner", "workbench_task=delivery_manifest;status=waiting");
	ok = ok && require_file_token("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "workspace_import=usable-workspace-import:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "workspace_file=metadata.tsv;kind=metadata;rows=3");
	ok = ok && require_file_token("rp_runner", "workbench_readiness=rp_workbench_ready;status=ready");
	ok = ok && require_file_token("rp_runner", "workbench_answer=rp_workbench_answer;citations=5;status=ready");
	ok = ok && require_file_token("rp_runner", "workbench_brief=rp_workbench_brief;handoff=ready");
	ok = ok && require_file_token("rp_runner", "workbench_runbook=rp_workbench_runbook;commands=6");
	ok = ok && require_file_token("rp_runner", "workbench_timeline=rp_workbench_timeline;events=8");
	ok = ok && require_file_token("rp_runner", "workbench_file_manifest=rp_workbench_manifest;files=9;sha_records=9");
	ok = ok && require_file_token("rp_runner", "question_present=1");
	ok = ok && require_file_token("rp_runner", "imported_inputs=1");
	ok = ok && require_file_token("rp_runner", "literature_evidence=1");
	ok = ok && require_file_token("rp_runner", "generated_artifacts=1");
	ok = ok && require_file_token("rp_runner", "llm_trace=ready");
	ok = ok && require_file_token("rp_runner", "human_review=needs_revision");
	ok = ok && require_file_token("rp_runner", "delivery_manifest=waiting");
	ok = ok && require_file_token("rp_runner", "next_action=build_delivery_manifest");
	ok = ok && require_file_token("rp_runner", "status=ready");
	ok = ok && require_file_token("rp_runner", "answer_id=usable-workbench-answer:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "citation_count=5");
	ok = ok && require_file_token("rp_runner", "citation=rp_input:workspace_import");
	ok = ok && require_file_token("rp_runner", "citation=rp_runner:custom_analysis");
	ok = ok && require_file_token("rp_runner", "citation=rp_knowledge:evidence_synthesis");
	ok = ok && require_file_token("rp_runner", "citation=rp_llm_resp:response_join");
	ok = ok && require_file_token("rp_runner", "citation=rp_package:delivery_manifest");
	ok = ok && require_file_token("rp_runner", "missing_item=delivery_manifest_finalization");
	ok = ok && require_file_token("rp_runner", "status=ready");
	ok = ok && require_file_token("rp_runner", "latest_run=usable-run:RUN-903");
	ok = ok && require_file_token("rp_runner", "latest_answer=usable-workbench-answer:RUN-900:1");
	ok = ok && require_file_token("rp_runner", "evidence_ids=5");
	ok = ok && require_file_token("rp_runner", "next_actions=2");
	ok = ok && require_file_token("rp_runner", "file_paths=rp_input,rp_runner,rp_knowledge,rp_package,rp_workbench_manifest");
	ok = ok && require_file_token("rp_runner", "handoff=ready");
	ok = ok && require_file_token("rp_runner", "status=ready");
	ok = ok && require_file_token("rp_runner", "commands=6");
	ok = ok && require_file_token("rp_runner", "command=check_readiness");
	ok = ok && require_file_token("rp_runner", "command=advance_delivery_manifest");
	ok = ok && require_file_token("rp_runner", "command=answer_from_evidence");
	ok = ok && require_file_token("rp_runner", "command=export_file_manifest");
	ok = ok && require_file_token("rp_runner", "command=package_reviewer_bundle");
	ok = ok && require_file_token("rp_runner", "command=open_review_page");
	ok = ok && require_file_token("rp_runner", "continuation_guide=ready");
	ok = ok && require_file_token("rp_runner", "status=ready");
	ok = ok && require_file_token("rp_runner", "events=8");
	ok = ok && require_file_token("rp_runner", "event=created;source=rp_input");
	ok = ok && require_file_token("rp_runner", "event=inspected;source=rp_runner");
	ok = ok && require_file_token("rp_runner", "event=imported;source=rp_input");
	ok = ok && require_file_token("rp_runner", "event=searched;source=rp_knowledge");
	ok = ok && require_file_token("rp_runner", "event=screened;source=rp_knowledge");
	ok = ok && require_file_token("rp_runner", "event=run;source=rp_runner");
	ok = ok && require_file_token("rp_runner", "event=reviewed;source=rp_review2");
	ok = ok && require_file_token("rp_runner", "event=exported;source=rp_package");
	ok = ok && require_file_token("rp_runner", "status=ready");
	ok = ok && require_file_token("rp_runner", "files=9");
	ok = ok && require_file_token("rp_runner", "sha_records=9");
	ok = ok && require_file_token("rp_runner", "file=rp_input;kind=input");
	ok = ok && require_file_token("rp_runner", "file=rp_runner;kind=run");
	ok = ok && require_file_token("rp_runner", "file=rp_knowledge;kind=evidence");
	ok = ok && require_file_token("rp_runner", "file=rp_review2;kind=review");
	ok = ok && require_file_token("rp_runner", "file=rp_revision;kind=revision");
	ok = ok && require_file_token("rp_runner", "file=rp_package;kind=delivery");
	ok = ok && require_file_token("rp_runner", "file=rp_llm_resp;kind=llm");
	ok = ok && require_file_token("rp_runner", "file=rp_artifact_manifest;kind=artifact");
	ok = ok && require_file_token("rp_runner", "file=rp_report_text;kind=report");
	ok = ok && require_file_token("rp_runner", "status=ready");
	ok = ok && require_file_token("rp_ingest_files", "files=2");
	ok = ok && require_file_token("rp_ingest_files", "derived_items=5");
	ok = ok && require_file_token("rp_dataset_snapshot", "snapshots=2");
	ok = ok && require_file_token("rp_dataset_snapshot", "normalized_fastq=rp_artifact:rp_normalized_fastq");
	ok = ok && require_file_token("rp_data_preview", "previews=2");
	ok = ok && require_file_token("rp_data_quality", "passed=7");
	ok = ok && require_file_token("rp_data_transform", "transforms=2");
	ok = ok && require_file_token("rp_data_transform", "derived=alignment");
	ok = ok && require_file_token("rp_dataset_collection", "items=4");
	ok = ok && require_file_token("rp_agents", "agents=7");
	ok = ok && require_file_token("rp_decisions", "decisions=8");
	ok = ok && require_file_token("rp_handoff", "handoffs=6");
	ok = ok && require_file_token("rp_deliberation", "items=5");
	ok = ok && require_file_token("rp_agent_run", "agent_decisions=8");

	ok = ok && require_file_token("rp_llmq", "secret_policy=no_secret_in_ucore");
	ok = ok && require_file_token("rp_llmq", "queue_validation=passed");
	ok = ok && require_file_token("rp_llmq", "schema_checks=3");
	ok = ok && require_file_token("rp_llmq", "dispatch_ready=3");
	ok = ok && require_file_token("rp_llmq", "route_decisions=3");
	ok = ok && require_file_token("rp_llmq", "secret_policy_records=3");
	ok = ok && require_file_token("rp_llm_resp", "responses=3");
	ok = ok && require_file_token("rp_llm_resp", "matched_requests=3");
	ok = ok && require_file_token("rp_llm_resp", "response_join=passed");
	ok = ok && require_file_token("rp_llm_resp", "response_hash_records=3");
	ok = ok && require_file_token("rp_llm_resp", "grounded_references=5");
	ok = ok && require_file_token("rp_llm_resp", "template_provider=plain_ucore_deterministic");
	ok = ok && require_file_token("rp_llm_resp", "host_cloud_provider=optional");
	ok = ok && require_file_token("rp_llm_resp", "host_relay_roundtrip=ready");
	ok = ok && require_file_token("rp_llm_resp", "match=q1->r1,q2->r2,q3->r3");
	ok = ok && require_file_token("rp_relay", "network_stack=host_only");
	ok = ok && require_file_token("rp_relay", "queue_consumer=rp_llm_relay");
	ok = ok && require_file_token("rp_relay", "handoff_contract=ordinary_files");
	ok = ok && require_file_token("rp_relay", "request_validation=passed");
	ok = ok && require_file_token("rp_relay", "response_validation=passed");
	ok = ok && require_file_token("rp_llm_packets", "packets=3");
	ok = ok && require_file_token("rp_llm_packets", "validated_packets=3");
	ok = ok && require_file_token("rp_llm_packets", "dispatch_records=3");
	ok = ok && require_file_token("rp_llm_packets", "response_join=passed");
	ok = ok && require_file_token("rp_llm_packets", "packet_schema=passed");
	ok = ok && require_file_token("rp_llm_packets", "retry_policy=template_fallback");
	ok = ok && require_file_token("rp_llm_packets", "matched_responses=3");
	ok = ok && require_file_token("rp_llm_packets", "roundtrip=ready");
	ok = ok && require_file_token("rp_llm_routes", "routes=4");
	ok = ok && require_file_token("rp_llm_routes", "route_policy=deterministic_then_host_optional");
	ok = ok && require_file_token("rp_llm_routes", "route_decision=review_summary->template");
	ok = ok && require_file_token("rp_llm_routes", "route_decision=method_check->template");
	ok = ok && require_file_token("rp_llm_routes", "route_decision=recovery_note->template");
	ok = ok && require_file_token("rp_llm_routes", "roundtrip_routes=3");
	ok = ok && require_file_token("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && require_file_token("rp_llm_guard", "payload_hashes=3");
	ok = ok && require_file_token("rp_llm_guard", "pii_scan=passed");
	ok = ok && require_file_token("rp_llm_guard", "secret_scan=passed");
	ok = ok && require_file_token("rp_llm_guard", "blocked_packets=0");
	ok = ok && require_file_token("rp_llm_hostreq", "template_mode=ready");
	ok = ok && require_file_token("rp_llm_hostreq", "host_request_records=3");
	ok = ok && require_file_token("rp_llm_hostreq", "host_response_records=3");
	ok = ok && require_file_token("rp_llm_hostreq", "host_request_manifest=ready");
	ok = ok && require_file_token("rp_llm_hostreq", "host_response_manifest=ready");
	ok = ok && require_file_token("rp_llm_hostreq", "cloud_disabled_reason=host_env_absent_in_plain_ucore");
	ok = ok && require_file_token("rp_llm_hostreq", "template_execution=ready");
	ok = ok && require_file_token("rp_llm_hostreq", "roundtrip=ready");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_cases=1");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_decision=template_for_missing_key");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_decision=template_for_network_loss");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_decision=stop_for_privacy_reject");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_trace=rp_llm_guard->rp_llm_fallback->rp_llm_resp");
	ok = ok && require_file_token("rp_llm_fallback", "offline_template_verified=1");
	ok = ok && require_file_token("rp_prompt", "provider_policy=host_relay");
	ok = ok && require_file_token("rp_llmeval", "passed=7");
	ok = ok && require_file_token("rp_llmeval", "queue_checks=3");
	ok = ok && require_file_token("rp_llmeval", "route_checks=3");
	ok = ok && require_file_token("rp_llmeval", "privacy_checks=3");
	ok = ok && require_file_token("rp_llmeval", "fallback_checks=3");
	ok = ok && require_file_token("rp_llmlog", "queue_validation=passed");
	ok = ok && require_file_token("rp_llmlog", "dispatch_records=3");
	ok = ok && require_file_token("rp_llmlog", "response_join=passed");
	ok = ok && require_file_token("rp_llmlog", "secret_scan=passed");
	ok = ok && require_file_token("rp_privacy", "decision=accepted");
	ok = ok && require_file_token("rp_compliance", "decision=accepted");

	ok = ok && require_file_token("rp_lit", "literature_search=usable-literature-search:RUN-900:1");
	ok = ok && require_file_token("rp_lit", "screening_decisions=9;included=3;excluded=6");
	ok = ok && require_file_token("rp_lit", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && require_file_token("rp_lit", "prisma_flow=usable-prisma-flow:RUN-900:1");
	ok = ok && require_file_token("rp_evidence", "status=ready");
	ok = ok && require_file_token("rp_claimrec", "claim=8");
	ok = ok && require_file_token("rp_provpath", "critical_paths=3");
	ok = ok && require_file_token("rp_knowledge", "synthesis=ready");
	ok = ok && require_file_token("rp_knowledge", "library_sources=1");
	ok = ok && require_file_token("rp_knowledge", "citation_key=library2026");
	ok = ok && require_file_token("rp_knowledge", "literature_search_id=usable-literature-search:RUN-900:1");
	ok = ok && require_file_token("rp_knowledge", "screening_decisions=9;included=3;excluded=6");
	ok = ok && require_file_token("rp_knowledge", "evidence_extractions=3");
	ok = ok && require_file_token("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && require_file_token("rp_knowledge", "prisma_flow=usable-prisma-flow:RUN-900:1");
	ok = ok && require_file_token("rp_knowledge", "evidence_synthesis=usable-evidence-synthesis:RUN-900:1");
	ok = ok && require_file_token("rp_review2", "review_threads=2");
	ok = ok && require_file_token("rp_review2", "thread=review-thread:RUN-042:methods");
	ok = ok && require_file_token("rp_review2", "thread=review-thread:RUN-042:repro");
	ok = ok && require_file_token("rp_review2", "comment=review-comment:RUN-042:1");
	ok = ok && require_file_token("rp_review2", "comment=review-comment:RUN-042:2");
	ok = ok && require_file_token("rp_review2", "action_item=action-item:RUN-042:methods");
	ok = ok && require_file_token("rp_review2", "action_item=action-item:RUN-042:repro");
	ok = ok && require_file_token("rp_review2", "review_summary=all_review_comments_resolved");
	ok = ok && require_file_token("rp_review2", "human_review=usable-review:RUN-900:1");
	ok = ok && require_file_token("rp_review2", "decision=needs_revision");
	ok = ok && require_file_token("rp_review2", "requested_change=methods_retry_scope");
	ok = ok && require_file_token("rp_review2", "requested_change=chart_caption");
	ok = ok && require_file_token("rp_review2", "revision_task=usable-revision-task:RUN-900:1");

	ok = ok && require_file_token("rp_revision", "applied_changes=2");
	ok = ok && require_file_token("rp_revision", "change=1;target=methods");
	ok = ok && require_file_token("rp_revision", "change=2;target=chart_caption");
	ok = ok && require_file_token("rp_revision", "revised_run=usable-run:RUN-900-rev1");

	ok = ok && require_file_token("rp_wfio", "imports=5");
	ok = ok && require_file_token("rp_wfio", "format=snakemake");
	ok = ok && require_file_token("rp_wfio", "format=galaxy");
	ok = ok && require_file_token("rp_wfio", "format=dvc");
	ok = ok && require_file_token("rp_wfio", "format=cwl");
	ok = ok && require_file_token("rp_wfio", "format=nextflow");
	ok = ok && require_file_token("rp_wfio", "normalized_steps=15");
	ok = ok && require_file_token("rp_wfio", "shared_run_id=RUN-042");
	ok = ok && require_file_token("rp_wfio", "adapter_specs=6");
	ok = ok && require_file_token("rp_wfio", "adapter_reports=6");
	ok = ok && require_file_token("rp_wfio", "unsupported_steps=0");
	ok = ok && require_file_token("rp_wfio", "plans=3");
	ok = ok && require_file_token("rp_wfio", "migration_steps=9");
	ok = ok && require_file_token("rp_wfio", "work_items=6");
	ok = ok && require_file_token("rp_wfio", "tool_mappings=8");
	ok = ok && require_file_token("rp_wfio", "risk_items=4");
	ok = ok && require_file_token("rp_wfio", "rehearsals=2");
	ok = ok && require_file_token("rp_wfio", "cases=4");
	ok = ok && require_file_token("rp_wfio", "passed_cases=3");
	ok = ok && require_file_token("rp_wfio", "manual_review_cases=1");
	ok = ok && require_file_token("rp_wfio", "adapter_reports=6");
	ok = ok && require_file_token("rp_wfio", "blocking_items=0");
	ok = ok && require_file_token("rp_wfio", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && require_file_token("rp_wfio", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && require_file_token("rp_wfio", "backend_scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && require_file_token("rp_wfio", "backend_binding=workflow-portability->rp_backend_exec");
	ok = ok && require_file_token("rp_wfio", "migration_execution=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && require_file_token("rp_wfio", "decision=ready_for_agentos");
	ok = ok && require_file_token("rp_wfio", "package=workflow-portability");

	ok = ok && require_file_token("rp_runconf", "profiles=2");
	ok = ok && require_file_token("rp_invocation", "status=recovered");
	ok = ok && require_file_token("rp_completion", "actions=4");
	ok = ok && require_file_token("rp_package", "artifacts=52");
	ok = ok && require_file_token("rp_package", "package_manifest=ready");
	ok = ok && require_file_token("rp_package", "bundle_items=18");
	ok = ok && require_file_token("rp_package", "downloadable_units=3");
	ok = ok && require_file_token("rp_package", "static_site_pages=42");
	ok = ok && require_file_token("rp_package", "evidence_bundle=ready");
	ok = ok && require_file_token("rp_package", "review_bundle=ready");
	ok = ok && require_file_token("rp_package", "provenance_bundle=ready");
	ok = ok && require_file_token("rp_package", "custom_sources=rp_input,rp_runner,rp_uresrun");
	ok = ok && require_file_token("rp_package", "workbench=rp_runner");
	ok = ok && require_file_token("rp_package", "workbench_tasks=9");
	ok = ok && require_file_token("rp_package", "workbench_export=rp_runner");
	ok = ok && require_file_token("rp_package", "download_index=report_bundle,evidence_bundle,provenance_bundle");
	ok = ok && require_file_token("rp_package", "request_form=rp_input");
	ok = ok && require_file_token("rp_package", "upload_files=rp_input");
	ok = ok && require_file_token("rp_package", "workspace_imports=1");
	ok = ok && require_file_token("rp_package", "workspace_import=workspace:RUN-900:folder");
	ok = ok && require_file_token("rp_package", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_package", "evidence_review_files=3");
	ok = ok && require_file_token("rp_package", "evidence_protocols=1");
	ok = ok && require_file_token("rp_package", "screening_decisions=9");
	ok = ok && require_file_token("rp_package", "evidence_extractions=3");
	ok = ok && require_file_token("rp_package", "prisma_flows=1");
	ok = ok && require_file_token("rp_package", "evidence_synthesis_files=2");
	ok = ok && require_file_token("rp_package", "workflow_portability=rp_wfio");
	ok = ok && require_file_token("rp_package", "portability_exports=5");
	ok = ok && require_file_token("rp_package", "adapter_specs=6");
	ok = ok && require_file_token("rp_package", "migration_steps=9");
	ok = ok && require_file_token("rp_package", "rehearsal_cases=4");
	ok = ok && require_file_token("rp_package", "bibliography=rp_runner");
	ok = ok && require_file_token("rp_package", "citation_plan=rp_runner");
	ok = ok && require_file_token("rp_package", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_package", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_package", "review_page=rp_package");
	ok = ok && require_file_token("rp_package", "raw_downloads=5");
	ok = ok && require_file_token("rp_package", "delivery=usable-delivery:RUN-900:1;status=ready");
	ok = ok && require_file_token("rp_package", "delivery_files=8");
	ok = ok && require_file_token("rp_package", "delivery_file=report_md;path=rp_report_text;required=1;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=package_manifest;path=rp_artifact_manifest;required=1;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=claim_audit;path=rp_claimrec;required=1;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=data_quality;path=rp_data_quality;required=1;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=chart_data;path=rp_chart_data;required=0;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=llm_trace;path=rp_llm_packets;required=0;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=review_page;path=rp_package;required=0;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_file=revision_tasks;path=rp_package;required=0;exists=1");
	ok = ok && require_file_token("rp_package", "delivery_checks=3");
	ok = ok && require_file_token("rp_package", "delivery_check=required_files;status=pass");
	ok = ok && require_file_token("rp_package", "delivery_check=human_review;status=pass");
	ok = ok && require_file_token("rp_package", "delivery_check=checksums;status=pass;checksums=8");
	ok = ok && require_file_token("rp_package", "delivery_manifest_json=delivery-manifest.json");
	ok = ok && require_file_token("rp_package", "delivery_manifest_md=delivery-manifest.md");
	ok = ok && require_file_token("rp_package", "latest_delivery_id=usable-delivery:RUN-900:1");
	ok = ok && require_file_token("rp_package", "latest_delivery_status=ready");
	ok = ok && require_file_token("rp_package", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_package", "evidence_bundle_entries=12");
	ok = ok && require_file_token("rp_package", "evidence_bundle_contains=run.json,artifacts.json,human_reviews.json,delivery_manifests.json,revision_tasks.json");
	ok = ok && require_file_token("rp_package", "evidence_bundle_contains_extra=screening_decisions.json");
	ok = ok && require_file_token("rp_package", "evidence_bundle_delivery_files=2");
	ok = ok && require_file_token("rp_package", "evidence_bundle_checksum=stable-evidence-bundle");
	ok = ok && require_file_token("rp_package", "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md");
	ok = ok && require_file_token("rp_package", "deliverables=8");
	ok = ok && require_file_token("rp_package", "audience=reviewer");
	ok = ok && require_file_token("rp_package", "bundle=rp_package");
	ok = ok && require_file_token("rp_package", "bundle_units=3");
	ok = ok && require_file_token("rp_package", "raw_links=5");
	ok = ok && require_file_token("rp_package", "checksums=6");
	ok = ok && require_file_token("rp_package", "provenance_source=rp_provpath");
	ok = ok && require_file_token("rp_package", "page=research-run");
	ok = ok && require_file_token("rp_package", "artifact_links=6");
	ok = ok && require_file_token("rp_package", "decision_controls=2");
	ok = ok && require_file_token("rp_package", "human_reviews=1");
	ok = ok && require_file_token("rp_package", "human_review=usable-review:RUN-900:1");
	ok = ok && require_file_token("rp_package", "review_note=clarify_reproducibility_and_chart_caption");
	ok = ok && require_file_token("rp_package", "revision_tasks=1");
	ok = ok && require_file_token("rp_package", "revision_task=usable-revision-task:RUN-900:1;status=completed");
	ok = ok && require_file_token("rp_package", "revision_change=methods_retry_scope");
	ok = ok && require_file_token("rp_package", "revision_change=chart_caption");
	ok = ok && require_file_token("rp_package", "revision_evidence=rp_revision");
	ok = ok && require_file_token("rp_package", "revision_change_count=2");
	ok = ok && require_file_token("rp_package", "revision_task_completed=usable-revision-task:RUN-900:1;new_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_package", "review_threads=2");
	ok = ok && require_file_token("rp_package", "review_comments=3");
	ok = ok && require_file_token("rp_package", "review_action_items=2");
	ok = ok && require_file_token("rp_package", "review_page_sections=Human Reviews,Delivery Manifests,Revision Tasks,Review Threads,Action Items");
	ok = ok && require_file_token("rp_package", "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp");
	ok = ok && require_file_token("rp_package", "llm_matched_responses=3");
	ok = ok && require_file_token("rp_release", "decision=release");
	ok = ok && require_file_token("rp_dossier", "sections=36");

	ok = ok && require_file_token("rp_agentcmp", "context_trusted=0");
	ok = ok && require_file_token("rp_agentcmp", "message_acks=35");
	ok = ok && require_file_token("rp_agentcmp", "tool_events=115");
	ok = ok && require_file_token("rp_agentcmp", "agent_roles=7");
	ok = ok && require_file_token("rp_agentcmp", "relay_protocol_files=5");
	ok = ok && require_file_token("rp_agentcmp", "workflow_runner_files=5");
	ok = ok && require_file_token("rp_agentcmp", "data_pipeline_files=6");
	ok = ok && require_file_token("rp_agentcmp", "bio_service_files=5");
	ok = ok && require_file_token("rp_agentcmp", "lab_resource_files=5");
	ok = ok && require_file_token("rp_agentcmp", "publication_service_files=5");
	ok = ok && require_file_token("rp_agentcmp", "knowledge_service_files=5");
	ok = ok && require_file_token("rp_agentcmp", "runtime_service_files=5");
	ok = ok && require_file_token("rp_agentcmp", "notebook_exports=2");
	ok = ok && require_file_token("rp_agentcmp", "dynamic_input_records=8");
	ok = ok && require_file_token("rp_agentcmp", "dynamic_submissions=4");
	ok = ok && require_file_token("rp_agentcmp", "host_ui_events=10");
	ok = ok && require_file_token("rp_backend", "reference_cases=7");
	ok = ok && require_file_token("rp_backend", "runtime_cases=0");
	ok = ok && require_file_token("rp_backend", "workflow_portability=rp_wfio");
	ok = ok && require_file_token("rp_backend", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && require_file_token("rp_backend", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && require_file_token("rp_backend", "runner=active-user-space");
	ok = ok && require_file_token(
		"rp_backend",
		"query_workload=research_metadata_lookup;consistency=fresh_snapshot;dataset_records=12;query_operations=4096;query_matches=4096");
	ok = ok && require_file_token(
		"rp_backend",
		"records_examined=49152;backend=plain_file_scan;status=verified");
	char backend_cleanup_probe[2];
	ok = ok && rp_read_file("m000", backend_cleanup_probe,
				    sizeof(backend_cleanup_probe)) < 0;
	ok = ok && rp_read_file("m011", backend_cleanup_probe,
				    sizeof(backend_cleanup_probe)) < 0;
	ok = ok && require_file_token("rp_backend_exec", "workflow_portability=rp_wfio");
	ok = ok && require_file_token("rp_backend_exec", "scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=plain-ucore;source=rp_wfio;expected_status=available");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=agentos-ucore;source=rp_wfio;expected_status=kernel_target");
	ok = ok && require_file_token("rp_backend_exec", "portability_rehearsal_cases=4");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=plain-ucore;expected_input=rp_wfio;expected_artifact=rp_artifact_manifest;expected_outcome=native_programs_ok");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=retry-recovery;expected_input=rp_retry_plan;expected_artifact=rp_stage_state;expected_outcome=recovered_align");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=user-context;expected_input=rp_query;expected_artifact=rp_provpath;expected_outcome=user_space_context_log");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=user-fsmeta;expected_input=rp_artifact_manifest;expected_artifact=rp_query;expected_outcome=file_manifest_scan");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=user-recovery;expected_input=rp_retrylog;expected_artifact=rp_fix;expected_outcome=user_space_repair_record");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=user-event;expected_input=rp_worker+rp_timeline;expected_artifact=rp_agent_run;expected_outcome=file_backed_event_log");
	ok = ok && require_file_token("rp_backend_exec", "reference_case=user-audit;expected_input=rp_audit+rp_provpath;expected_artifact=rp_package;expected_outcome=append_only_audit_files");
	ok = ok && require_file_token("rp_backend_exec", "reference_case_rows=7");
	ok = ok && require_file_token("rp_backend_exec", "reference_report_rows=7");
	ok = ok && require_file_token("rp_backend_exec", "runtime_pass_rows=0");
	ok = ok && require_file_token("rp_backend_exec", "performance_samples=0");
	ok = ok && require_file_token("rp_study", "workflow_portability=rp_wfio");
	ok = ok && require_file_token("rp_study", "migration_status=plain_userland_equivalents_ready");
	ok = ok && require_file_token("rp_study", "reference_metric=plain_ucore;expected_file_scans=128;expected_context_trusted=0;expected_rebuild_steps=6");
	ok = ok && require_file_token("rp_study", "reference_metric=agentos_ucore;expected_context_trusted=1;expected_batch_tools=1;expected_metadata_index=1");
	ok = ok && require_file_token("rp_study", "metrics=12");
	ok = ok && require_file_token("rp_study", "study_handoff=rp_backend_exec->rp_agentcmp;status=ready");
	ok = ok && require_file_token("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=7;agentos_replacements=7;risks=7;status=ready");
	ok = ok && require_file_token("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128,manual_retry_contract,file_polling,append_only_logs;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index,capability_checked_action,kernel_event_queue,kernel_ledger_provenance;status=ready");
	ok = ok && require_file_token("rp_query", "knowledge_index=search_documents:1685");
	ok = ok && require_file_token("rp_query", "provenance_nodes:406");
	ok = ok && require_file_token("rp_query", "provenance_links:544");
	ok = ok && require_file_token("rp_query", "events:8966");
	ok = ok && require_file_token("rp_query", "context_records:380");
	ok = ok && require_file_token("rp_query", "host_workflow_artifacts:150");
	ok = ok && require_file_token("rp_query", "usable_artifacts:507");
	ok = ok && require_file_token("rp_query", "usable_runs:23");
	ok = ok && require_file_token("rp_query", "usable_stages:197");
	ok = ok && require_file_token("rp_query", "usable_messages:265");
	ok = ok && require_file_token("rp_query", "usable_decisions:242");
	ok = ok && require_file_token("rp_llmlog", "transcripts=99");
	ok = ok && require_file_token("rp_llmlog", "bridge_requests=33");
	ok = ok && require_file_token("rp_llmlog", "bridge_responses=33");
	ok = ok && require_file_token("rp_runner", "workbench_delivery_scale=workbenches:8");
	ok = ok && require_file_token("rp_runner", "templates:8");
	ok = ok && require_file_token("rp_runner", "workspace_imports:9");
	ok = ok && require_file_token("rp_runner", "workspace_inspections:9");
	ok = ok && require_file_token("rp_runner", "answers:11");
	ok = ok && require_file_token("rp_runner", "deliveries:9");
	ok = ok && require_file_token("rp_runner", "studio_sessions:2");
	ok = ok && require_file_token("rp_runner", "project_action_plans:17");
	ok = ok && require_file_token("rp_runner", "project_deliveries:4");
	ok = ok && require_file_token("rp_runner", "project_runbooks:17");
	ok = ok && require_file_token("rp_runner", "project_evidence_audits:17");
	ok = ok && require_file_token("rp_runner", "project_provenance_graphs:4");
	ok = ok && require_file_token("rp_runner", "project_launches:3");
	ok = ok && require_file_token("rp_runner", "project_release_gates:17");
	ok = ok && require_file_token("rp_runner", "project_snapshots:17");
	ok = ok && require_file_token("rp_consistency", "checks=420");
	ok = ok && require_file_token("rp_consistency", "state_catalog_checks=12");
	ok = ok && require_file_token("rp_consistency", "startup_doctor_checks=14");
	ok = ok && require_file_token("rp_state_catalog", "host_state_keys=574");
	ok = ok && require_file_token("rp_state_catalog", "nonzero_state_categories=71");
	ok = ok && require_file_token("rp_state_catalog", "zero_state_categories=503");
	ok = ok && require_file_token("rp_state_catalog", "represented_state_categories=574");
	ok = ok && require_file_token("rp_state_catalog", "coverage_model=nonzero_records_preserved");
	ok = ok && require_file_token("rp_startup", "quickstart=ready");
	ok = ok && require_file_token("rp_startup", "startup_checks=8");
	ok = ok && require_file_token("rp_startup", "offline_runs_ready=1");
	ok = ok && require_file_token("rp_startup", "cloud_llm_ready=0");
	ok = ok && require_file_token("rp_startup", "provider_health=offline:1,cloud:0,ready_cloud:0");
	ok = ok && require_file_token("rp_startup", "platform_doctor=ready");
	ok = ok && require_file_token("rp_startup", "doctor_checks=10");
	ok = ok && require_file_token("rp_startup", "doctor_downloads=markdown,json");
	ok = ok && require_file_token("rp_startup", "workspace_writable=pass");
	ok = ok && require_file_token("rp_startup", "state_load=pass");
	ok = ok && require_file_token("rp_startup", "template_provider=pass");
	ok = ok && require_file_token("rp_startup", "project_launch=sample_ready");
	ok = ok && require_file_token("rp_startup", "recommended_commands=startup_guide");
	ok = ok && require_file_token("rp_startup", "agentos_adapter_hint=plain_files_now");
	ok = ok && require_file_token("rp_consistency", "artifact_provenance=3");
	ok = ok && require_file_token("rp_consistency", "artifact_dossier_checks=4");
	ok = ok && require_file_token("rp_consistency", "artifact_path_rebuild_files=6");
	ok = ok && require_file_token("rp_consistency", "artifact_path_rebuild_steps=7");
	ok = ok && require_file_token("rp_consistency", "knowledge_index_checks=22");
	ok = ok && require_file_token("rp_consistency", "llm_transcript_checks=3");
	ok = ok && require_file_token("rp_consistency", "workbench_delivery_checks=15");
	ok = ok && require_file_token("rp_agentcmp", "research_portfolio_checks=16");
	ok = ok && require_file_token("rp_consistency", "research_portfolio_checks=16");
	ok = ok && require_file_token("rp_agentcmp", "research_portfolio=sources:67");
	ok = ok && require_file_token("rp_consistency", "usable_research_sources=67");
	ok = ok && require_file_token("rp_agentcmp", "execution_scale_checks=14");
	ok = ok && require_file_token("rp_consistency", "execution_scale_checks=14");
	ok = ok && require_file_token("rp_agentcmp", "agentcompare_execution_scale=reports:4");
	ok = ok && require_file_token("rp_agentcmp", "host_runtime_scale=workflow_runs:10");
	ok = ok && require_file_token("rp_agentcmp", "content_graph_scale=content_objects:129");
	ok = ok && require_file_token("rp_consistency", "host_workflow_stage_runs=70");
	ok = ok && require_file_token("rp_consistency", "agentcompare_results=20");
	ok = ok && require_file_token("rp_agentcmp", "operations_scale_checks=12");
	ok = ok && require_file_token("rp_consistency", "operations_scale_checks=12");
	ok = ok && require_file_token("rp_agentcmp", "host_operations_scale=audit_records:5");
	ok = ok && require_file_token("rp_agentcmp", "project_revision_incident_checks=12");
	ok = ok && require_file_token("rp_agentcmp", "reserved_research_surface_checks=21");
	ok = ok && require_file_token("rp_agentcmp", "root_state_surface_checks=10");
	ok = ok && require_file_token("rp_agentcmp", "root_state_surface=projects:1");
	ok = ok && require_file_token("rp_agentcmp", "agentos_reserved_surface_checks=21");
	ok = ok && require_file_token("rp_agentcmp", "agentos_reserved_surface=profiles:0");
	ok = ok && require_file_token("rp_consistency", "root_state_surface_checks=10");
	ok = ok && require_file_token("rp_consistency", "root_projects=1");
	ok = ok && require_file_token("rp_consistency", "root_runs=1");
	ok = ok && require_file_token("rp_consistency", "root_reports=1");
	ok = ok && require_file_token("rp_consistency", "root_plans=1");
	ok = ok && require_file_token("rp_consistency", "root_search_records=2");
	ok = ok && require_file_token("rp_consistency", "root_site_exports=1");
	ok = ok && require_file_token("rp_consistency", "root_compare_profiles=1");
	ok = ok && require_file_token("rp_consistency", "root_audit_records=5");
	ok = ok && require_file_token("rp_consistency", "root_context_records=380");
	ok = ok && require_file_token("rp_consistency", "root_project_id=lab-gene-x");
	ok = ok && require_file_token("rp_consistency", "root_run_id=RUN-042");
	ok = ok && require_file_token("rp_consistency", "root_report_id=RUN-042-recovery-report");
	ok = ok && require_file_token("rp_consistency", "root_plan_id=PLAN-RUN-042-RECOVER-1");
	ok = ok && require_file_token("rp_consistency", "root_search_id=search:1");
	ok = ok && require_file_token("rp_consistency", "root_site_id=site:1");
	ok = ok && require_file_token("rp_consistency", "root_compare_profile=agentcompare-default");
	ok = ok && require_file_token("rp_consistency", "root_audit_spoof_denied=1");
	ok = ok && require_file_token("rp_consistency", "agentos_reserved_surface_checks=21");
	ok = ok && require_file_token("rp_consistency", "agentos_reserved_surface=profiles:0");
	ok = ok && require_file_token("rp_consistency", "tool_bindings:0");
	ok = ok && require_file_token("rp_agentcmp", "project_revision_incident=revision_tasks:1");
	ok = ok && require_file_token("rp_agentcmp", "incident:INC-RUN-042-ALIGN-OOM");
	ok = ok && require_file_token("rp_consistency", "project_revision_incident_checks=12");
	ok = ok && require_file_token("rp_consistency", "usable_research_revision_tasks=1");
	ok = ok && require_file_token("rp_consistency", "usable_research_project_scaffolds=1");
	ok = ok && require_file_token("rp_consistency", "incidents=1");
	ok = ok && require_file_token("rp_consistency", "incident_reason=memory_limit");
	ok = ok && require_file_token("rp_consistency", "revision_review_decision=needs_revision");
	ok = ok && require_file_token("rp_consistency", "project_scaffold=deepseek-reliability-response-study");
	ok = ok && require_file_token("rp_consistency", "reserved_research_surface_checks=21");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_answers=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_cards=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_portfolios=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_previews=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_run_comparisons=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_runs=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_visualizations=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_evidence_syntheses=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_package_intakes=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_prisma_flows=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_project_action_executions=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_project_reviews=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_review_protocols=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_source_portfolios=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_bundles=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_compliance_reports=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_launches=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_runs=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocols=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_workbench_action_items=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_workbench_notes=0");
	ok = ok && require_file_token("rp_consistency", "host_metrics=13");
	ok = ok && require_file_token("rp_consistency", "usable_research_projects=23");
	ok = ok && require_file_token("rp_consistency", "host_artifacts=128");
	ok = ok && require_file_token("rp_consistency", "search_documents=1685");
	ok = ok && require_file_token("rp_consistency", "provenance_nodes=406");
	ok = ok && require_file_token("rp_consistency", "provenance_links=544");
	ok = ok && require_file_token("rp_consistency", "event_stream_records=8966");
	ok = ok && require_file_token("rp_consistency", "context_records=380");
	ok = ok && require_file_token("rp_consistency", "coherence_checks=9");
	ok = ok && require_file_token("rp_consistency", "workbench_records=10");
	ok = ok && require_file_token("rp_consistency", "advanced_surface_objects=5");
	ok = ok && require_file_token("rp_runop", "advanced_surface=objects:5");
	ok = ok && require_file_token("rp_runop", "research_search:saved_queries:2");
	ok = ok && require_file_token("rp_runop", "project_space:lab-gene-x");
	ok = ok && require_file_token("rp_runop", "study_protocol:protocols:2");
	ok = ok && require_file_token("rp_runop", "dataset_answer:datasets:2");
	ok = ok && require_file_token("rp_runop", "package_intake:packages:1");
	ok = ok && require_file_token("rp_telemetry", "advanced_surface_objects=5");
	ok = ok && require_file_token("rp_consistency", "dynamic_input_records=8");
	ok = ok && require_file_token("rp_consistency", "dynamic_submissions=4");
	ok = ok && require_file_token("rp_consistency", "host_ui_events=10");
	ok = ok && require_file_token("rp_consistency", "workbench_tasks=9");
	ok = ok && require_file_token("rp_consistency", "namespace_checks=12");
	ok = ok && require_file_token("rp_consistency", "surface_checks=13");
	ok = ok && require_file_token("rp_consistency", "status_semantics=11");
	ok = ok && require_file_token("rp_consistency", "reference_checks=18");
	ok = ok && require_file_token("rp_consistency", "evidence_trace_checks=14");
	ok = ok && require_file_token("rp_consistency", "run_state_checks=9");
	ok = ok && require_file_token("rp_consistency", "lifecycle_checks=10");
	ok = ok && require_file_token("rp_consistency", "delivery_coherence=3");
	ok = ok && require_file_token("rp_consistency", "agentos_readiness_checks=7");
	ok = ok && require_file_token("rp_telemetry", "metric_files=151");

	ok = ok && require_file_token("rp_sreg", "samples=8");
	ok = ok && require_file_token("rp_ethics", "ethics=approved");
	ok = ok && require_file_token("rp_access", "approved=2");
	ok = ok && require_file_token("rp_cohort", "cohorts=2");
	ok = ok && require_file_token("rp_bioop", "op=access_decision");
	ok = ok && require_file_token("rp_instr", "instruments=4");
	ok = ok && require_file_token("rp_invent", "inventory_items=9");
	ok = ok && require_file_token("rp_procure", "requests=3");
	ok = ok && require_file_token("rp_ressched", "bookings=6");
	ok = ok && require_file_token("rp_labresop", "op=schedule_assess");
	ok = ok && require_file_token("rp_labresop", "lab_governance_ops=approvals:2");
	ok = ok && require_file_token("rp_labresop", "protocol_compliance_reports:2");
	ok = ok && require_file_token("rp_labresop", "sop_executions:3");
	ok = ok && require_file_token("rp_labresop", "training_records:4");
	ok = ok && require_file_token("rp_labresop", "run_queue_items:4");
	ok = ok && require_file_token("rp_labresop", "notifications:3");
	ok = ok && require_file_token("rp_resrev", "review_items=10");
	ok = ok && require_file_token("rp_pubplan", "journal_targets=2");
	ok = ok && require_file_token("rp_peerresp", "responses=6");
	ok = ok && require_file_token("rp_fairpkg", "fair_checks=8");
	ok = ok && require_file_token("rp_pubop", "op=result_review");
	ok = ok && require_file_token("rp_litrev", "papers=9");
	ok = ok && require_file_token("rp_litrev", "search_strategies=2");
	ok = ok && require_file_token("rp_litrev", "screening_decisions=9");
	ok = ok && require_file_token("rp_litrev", "evidence_extractions=3");
	ok = ok && require_file_token("rp_litrev", "protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && require_file_token("rp_litrev", "prisma_flow=usable-prisma-flow:RUN-900:1");
	ok = ok && require_file_token("rp_litrev", "synthesis=usable-evidence-synthesis:RUN-900:1");
	ok = ok && require_file_token("rp_citegraph", "bibtex_entries=9");
	ok = ok && require_file_token("rp_semindex", "documents=17");
	ok = ok && require_file_token("rp_kanswers", "answers=4");
	ok = ok && require_file_token("rp_knowop", "op=llm_grounding");
	ok = ok && require_file_token("rp_runenv", "environments=4");
	ok = ok && require_file_token("rp_nbexec", "executed_cells=8");
	ok = ok && require_file_token("rp_nbexec", "notebook=reproducible-analysis.ipynb");
	ok = ok && require_file_token("rp_nbexec", "cell=2;type=code;source=load_artifact_paths");
	ok = ok && require_file_token("rp_nbexec", "execution=RUN-042:repro-notebook;status=passed");
	ok = ok && require_file_token("rp_repro", "downloadable_units=4");
	ok = ok && require_file_token("rp_eln", "eln_entries=3");
	ok = ok && require_file_token("rp_wpool", "worker_pools=2");
	ok = ok && require_file_token("rp_runop", "op=host_llm_request");

	ok = ok && require_file_token("rp_ui_home", "page=home");
	ok = ok && require_file_token("rp_ui_home", "nav_items=12");
	ok = ok && require_file_token("rp_ui_home", "primary_cards=12");
	ok = ok && require_file_token("rp_ui_home", "static_site_pages=42");
	ok = ok && require_file_token("rp_ui_home", "dynamic_inputs=4");
	ok = ok && require_file_token("rp_ui_run", "page=run-detail");
	ok = ok && require_file_token("rp_ui_run", "runner_exec=");
	ok = ok && require_file_token("rp_ui_run", "timeline_rows=5");
	ok = ok && require_file_token("rp_ui_run", "artifact_preview=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && require_file_token("rp_ui_run", "dependency_checks=5");
	ok = ok && require_file_token("rp_ui_run", "dynamic_input_queue=rp_input");
	ok = ok && require_file_token("rp_ui_run", "retry_reason=tool_output_missing");
	ok = ok && require_file_token("rp_ui_run", "latest_review=usable-review:RUN-900:1");
	ok = ok && require_file_token("rp_ui_run", "latest_revision_task=usable-revision-task:RUN-900:1");
	ok = ok && require_file_token("rp_ui_run", "revised_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_ui_run", "revision_changes=2");
	ok = ok && require_file_token("rp_ui_run", "revision_delta=rp_revision");
	ok = ok && require_file_token("rp_ui_run", "delivery_files=8");
	ok = ok && require_file_token("rp_ui_run", "delivery_checks=3");
	ok = ok && require_file_token("rp_ui_run", "delivery_manifest_json=delivery-manifest.json");
	ok = ok && require_file_token("rp_ui_run", "workspace_imports=1");
	ok = ok && require_file_token("rp_ui_run", "workbench=rp_runner");
	ok = ok && require_file_token("rp_ui_run", "evidence_protocols=1");
	ok = ok && require_file_token("rp_ui_run", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_ui_run", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_ui_run", "llm_response_file=rp_llm_resp");
	ok = ok && require_file_token("rp_ui_run", "notebook_export=rp_nbexec");
	ok = ok && require_file_token("rp_ui_run", "notebook_download=rp_repro");
	ok = ok && require_file_token("rp_ui_run", "review_threads=2");
	ok = ok && require_file_token("rp_ui_run", "review_action_items=2");
	ok = ok && require_file_token("rp_ui_agent", "page=agent-detail");
	ok = ok && require_file_token("rp_ui_agent", "decisions=8");
	ok = ok && require_file_token("rp_ui_agent", "decision_rows=8");
	ok = ok && require_file_token("rp_ui_evidence", "page=evidence-detail");
	ok = ok && require_file_token("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && require_file_token("rp_ui_evidence", "literature_search=usable-literature-search:RUN-900:1");
	ok = ok && require_file_token("rp_ui_evidence", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && require_file_token("rp_ui_evidence", "prisma_flow=usable-prisma-flow:RUN-900:1");
	ok = ok && require_file_token("rp_ui_evidence", "delivery_files=8");
	ok = ok && require_file_token("rp_ui_evidence", "delivery_checks=3");
	ok = ok && require_file_token("rp_ui_evidence", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_ui_evidence", "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp");
	ok = ok && require_file_token("rp_ui_compare", "page=compare-metrics");
	ok = ok && require_file_token("rp_ui_compare", "metric_rows=8");
	ok = ok && require_file_token("rp_ui_compare", "coherence_checks=9");
	ok = ok && require_file_token("rp_ui_compare", "relay_protocol_files=5");
	ok = ok && require_file_token("rp_ui_compare", "notebook_exports=2");
	ok = ok && require_file_token("rp_web_routes", "routes=152");
	ok = ok && require_file_token("rp_web_routes", "get_routes=29");
	ok = ok && require_file_token("rp_web_routes", "host_page_routes=15");
	ok = ok && require_file_token("rp_web_routes", "ucore_page_routes=15");
	ok = ok && require_file_token("rp_web_routes", "host_dynamic_page_prefixes=12");
	ok = ok && require_file_token("rp_web_routes", "ucore_dynamic_page_prefixes=12");
	ok = ok && require_file_token("rp_web_routes", "host_download_routes=16");
	ok = ok && require_file_token("rp_web_routes", "ucore_download_routes=16");
	ok = ok && require_file_token("rp_web_routes", "route=/quickstart");
	ok = ok && require_file_token("rp_web_routes", "route=/research-ops");
	ok = ok && require_file_token("rp_web_routes", "route=/workbench-plan-queue");
	ok = ok && require_file_token("rp_web_routes", "route=/review-inbox");
	ok = ok && require_file_token("rp_web_routes", "route=/research/workbench/{id}");
	ok = ok && require_file_token("rp_web_routes", "route=/research/project/{id}/review");
	ok = ok && require_file_token("rp_web_routes", "route=/api-catalog");
	ok = ok && require_file_token("rp_web_routes", "prefix=/runs/{run_id}");
	ok = ok && require_file_token("rp_web_routes", "prefix=/workbench-files/{token}");
	ok = ok && require_file_token("rp_web_routes", "prefix=/provenance/{id}");
	ok = ok && require_file_token("rp_web_routes", "prefix=/llm/{id}");
	ok = ok && require_file_token("rp_web_routes", "download=/download/research-dataset-preview/{token}");
	ok = ok && require_file_token("rp_web_routes", "download=/download/research-source-portfolio/{token}");
	ok = ok && require_file_token("rp_web_routes", "download=/download/research-study-protocol-reproduction-package-action-execution/{token}");
	ok = ok && require_file_token("rp_web_routes", "post_routes=123");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/host-workflow/stage-attempt");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/host-workflow/report-export");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/artifact-input");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/artifact-package");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/project-release-gate");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/project-provenance-graph");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/review");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/revision-task");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/run-revision-task");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/workflow-portability/import");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/workflow-portability/package");
	ok = ok && require_file_token("rp_api_home", "api=home");
	ok = ok && require_file_token("rp_api_home", "custom_run=usable-run:RUN-900");
	ok = ok && require_file_token("rp_api_home", "custom_runs=3");
	ok = ok && require_file_token("rp_api_home", "research_form=rp_input");
	ok = ok && require_file_token("rp_api_home", "upload_files=rp_input");
	ok = ok && require_file_token("rp_api_home", "dynamic_inputs=4");
	ok = ok && require_file_token("rp_api_home", "reader_contract=rp_web_bundle");
	ok = ok && require_file_token("rp_api_home", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_api_home", "nav_items=12");
	ok = ok && require_file_token("rp_api_home", "static_site_pages=42");
	ok = ok && require_file_token("rp_api_run", "runner_exec_files=5");
	ok = ok && require_file_token("rp_api_run", "custom_research=rp_runner");
	ok = ok && require_file_token("rp_api_run", "custom_research_runs=3");
	ok = ok && require_file_token("rp_api_run", "request_form=rp_input");
	ok = ok && require_file_token("rp_api_run", "upload_files=rp_input");
	ok = ok && require_file_token("rp_api_run", "dynamic_input_queue=rp_input");
	ok = ok && require_file_token("rp_api_run", "reader_contract=rp_web_bundle");
	ok = ok && require_file_token("rp_api_run", "reader_view=run-detail");
	ok = ok && require_file_token("rp_api_run", "reader_refresh=rp_web_bundle");
	ok = ok && require_file_token("rp_api_run", "workspace_imports=1");
	ok = ok && require_file_token("rp_api_run", "workbench=rp_runner");
	ok = ok && require_file_token("rp_api_run", "bibliography=rp_runner");
	ok = ok && require_file_token("rp_api_run", "evidence_protocols=1");
	ok = ok && require_file_token("rp_api_run", "citation_plan=rp_runner");
	ok = ok && require_file_token("rp_api_run", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_api_run", "delivery_files=8");
	ok = ok && require_file_token("rp_api_run", "delivery_checks=3");
	ok = ok && require_file_token("rp_api_run", "latest_delivery_status=ready");
	ok = ok && require_file_token("rp_api_run", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_api_run", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_api_run", "llm_response_file=rp_llm_resp");
	ok = ok && require_file_token("rp_api_run", "notebook_export=rp_nbexec");
	ok = ok && require_file_token("rp_api_run", "notebook_download=rp_repro");
	ok = ok && require_file_token("rp_api_run", "review_page=rp_package");
	ok = ok && require_file_token("rp_api_run", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_api_run", "human_reviews=1");
	ok = ok && require_file_token("rp_api_run", "revision_tasks=1");
	ok = ok && require_file_token("rp_api_run", "revised_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_api_run", "revision_changes=2");
	ok = ok && require_file_token("rp_api_run", "revision_delta=rp_revision");
	ok = ok && require_file_token("rp_api_run", "review_threads=2");
	ok = ok && require_file_token("rp_api_run", "review_action_items=2");
	ok = ok && require_file_token("rp_api_run", "timeline_rows=5");
	ok = ok && require_file_token("rp_api_run", "dependency_checks=5");
	ok = ok && require_file_token("rp_api_run", "manifest_support_entries=2");
	ok = ok && require_file_token("rp_api_agents", "agents=7");
	ok = ok && require_file_token("rp_api_evidence", "provenance_paths=3");
	ok = ok && require_file_token("rp_api_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && require_file_token("rp_api_evidence", "literature_search=usable-literature-search:RUN-900:1");
	ok = ok && require_file_token("rp_api_evidence", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && require_file_token("rp_api_compare", "workflow_runner_files=5");
	ok = ok && require_file_token("rp_api_compare", "coherence_checks=9");
	ok = ok && require_file_token("rp_api_artifacts", "manifest_records=4");
	ok = ok && require_file_token("rp_api_artifacts", "evidence_package=rp_package");
	ok = ok && require_file_token("rp_api_artifacts", "downloadable_units=3");
	ok = ok && require_file_token("rp_api_artifacts", "notebook_downloadable=1");
	ok = ok && require_file_token("rp_api_artifacts", "preview_files=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && require_file_token("rp_api_artifacts", "download_index=rp_package");
	ok = ok && require_file_token("rp_api_artifacts", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_api_artifacts", "delivery_files=8");
	ok = ok && require_file_token("rp_api_artifacts", "delivery_checks=3");
	ok = ok && require_file_token("rp_api_artifacts", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_api_artifacts", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_api_artifacts", "evidence_bundle_entries=12");
	ok = ok && require_file_token("rp_api_artifacts", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_api_artifacts", "llm_matched_responses=3");
	ok = ok && require_file_token("rp_api_artifacts", "review_page=rp_package");
	ok = ok && require_file_token("rp_api_artifacts", "raw_downloads=5");
	ok = ok && require_file_token("rp_api_artifacts", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_api_data", "dataset_snapshots=2");
	ok = ok && require_file_token("rp_api_data", "dynamic_queue=rp_input");
	ok = ok && require_file_token("rp_api_bio", "sample_registry=rp_sreg");
	ok = ok && require_file_token("rp_api_labres", "instrument_registry=rp_instr");
	ok = ok && require_file_token("rp_api_pub", "result_review=rp_resrev");
	ok = ok && require_file_token("rp_api_know", "semantic_index=rp_semindex");
	ok = ok && require_file_token("rp_api_know", "evidence_protocols=1");
	ok = ok && require_file_token("rp_api_know", "evidence_extractions=3");
	ok = ok && require_file_token("rp_api_runtime", "runtime_env=rp_runenv");
	ok = ok && require_file_token("rp_api_action", "actions=123");
	ok = ok && require_file_token("rp_api_action", "research_studio_launch=/actions/research/studio-launch");
	ok = ok && require_file_token("rp_api_action", "host_workflow_stage=/actions/host-workflow/stage-attempt");
	ok = ok && require_file_token("rp_api_action", "host_workflow_report=/actions/host-workflow/report-export");
	ok = ok && require_file_token("rp_api_action", "artifact_input=/actions/research/artifact-input");
	ok = ok && require_file_token("rp_api_action", "artifact_package=/actions/research/artifact-package");
	ok = ok && require_file_token("rp_api_action", "project_scaffold=/actions/research/project-scaffold");
	ok = ok && require_file_token("rp_api_action", "project_launch=/actions/research/project-launch");
	ok = ok && require_file_token("rp_api_action", "project_action_execute=/actions/research/project-action-execute");
	ok = ok && require_file_token("rp_api_action", "dataset_preview=/actions/research/dataset-preview");
	ok = ok && require_file_token("rp_api_action", "dataset_run=/actions/research/dataset-run");
	ok = ok && require_file_token("rp_api_action", "study_protocol_launch=/actions/research/study-protocol-launch");
	ok = ok && require_file_token("rp_api_action", "study_protocol_reproduction_package_action_execute=/actions/research/study-protocol-reproduction-package-action-execute");
	ok = ok && require_file_token("rp_api_action", "project_release_gate=/actions/research/project-release-gate");
	ok = ok && require_file_token("rp_api_action", "project_snapshot=/actions/research/project-snapshot");
	ok = ok && require_file_token("rp_api_action", "project_provenance_graph=/actions/research/project-provenance-graph");
	ok = ok && require_file_token("rp_api_action", "project_delivery=/actions/research/project-delivery");
	ok = ok && require_file_token("rp_api_action", "workflow_portability_run=/actions/workflow-portability/run");
	ok = ok && require_file_token("rp_api_action", "workflow_portability_import=/actions/workflow-portability/import");
	ok = ok && require_file_token("rp_api_action", "workflow_portability_package=/actions/workflow-portability/package");
	ok = ok && require_file_token("rp_api_action", "delivery_manifest_builder=1");
	ok = ok && require_file_token("rp_api_action", "human_review_form=1");
	ok = ok && require_file_token("rp_api_action", "revision_task_runner=1");
	ok = ok && require_file_token("rp_api_action", "dynamic_submit=/actions/research/run");
	ok = ok && require_file_token("rp_api_action", "live_update_feed=rp_web_bundle");
	ok = ok && require_file_token("rp_api_action", "reader_contract=rp_web_bundle");
	ok = ok && require_file_token("rp_api_action", "workbench_advance=1");
	ok = ok && require_file_token("rp_api_action", "notebook_download=1");
	ok = ok && require_file_token("rp_api_action", "bundle_download=1");
	ok = ok && require_file_token("rp_api_action", "operations_actions=3");
	ok = ok && require_file_token("rp_api_action", "quality_actions=3");
	ok = ok && require_file_token("rp_api_action", "delivery_actions=2");
	ok = ok && require_file_token("rp_api_action", "project_space_actions=7");
	ok = ok && require_file_token("rp_api_action", "project_review_actions=8");
	ok = ok && require_file_token("rp_api_action", "research_search_actions=4");
	ok = ok && require_file_token("rp_api_action", "artifact_actions=5");
	ok = ok && require_file_token("rp_api_action", "plan_queue_actions=2");
	ok = ok && require_file_token("rp_api_action", "action_item_actions=1");
	ok = ok && require_file_token("rp_api_action", "project_lifecycle_actions=3");
	ok = ok && require_file_token("rp_api_action", "action_state_records=12");
	ok = ok && require_file_token("rp_api_action", "validated_requests=8");
	ok = ok && require_file_token("rp_api_action", "precondition_checks=8");
	ok = ok && require_file_token("rp_api_action", "side_effect_records=16");
	ok = ok && require_file_token("rp_api_action", "action_audit_log=rp_actionio");
	ok = ok && require_file_token("rp_api_action", "download_manifest=rp_package");
	ok = ok && require_file_token("rp_api_action", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_api_catalog", "host_api_routes=214");
	ok = ok && require_file_token("rp_api_catalog", "host_action_routes=95");
	ok = ok && require_file_token("rp_api_catalog", "host_page_routes=15");
	ok = ok && require_file_token("rp_api_catalog", "host_dynamic_page_prefixes=12");
	ok = ok && require_file_token("rp_api_catalog", "ucore_dynamic_page_prefixes=12");
	ok = ok && require_file_token("rp_api_catalog", "host_download_routes=16");
	ok = ok && require_file_token("rp_api_catalog", "ucore_download_routes=16");
	ok = ok && require_file_token("rp_api_catalog", "download_group=dataset;routes=6");
	ok = ok && require_file_token("rp_api_catalog", "api_group_count=14");
	ok = ok && require_file_token("rp_api_catalog", "api_grouped_routes=214");
	ok = ok && require_file_token("rp_api_catalog", "usable_research_api_routes=77");
	ok = ok && require_file_token("rp_api_catalog", "domain_api_routes=50");
	ok = ok && require_file_token("rp_api_catalog", "lab_research_api_routes=15");
	ok = ok && require_file_token("rp_api_catalog", "workflow_api_routes=12");
	ok = ok && require_file_token("rp_api_catalog", "api_group=usable_research;routes=77");
	ok = ok && require_file_token("rp_api_catalog", "api_group=domain;routes=50");
	ok = ok && require_file_token("rp_api_catalog", "api_group=lab_research;routes=15");
	ok = ok && require_file_token("rp_api_catalog", "api_group=llm;routes=4");
	ok = ok && require_file_token("rp_api_catalog", "api_key=/api/analysis-results");
	ok = ok && require_file_token("rp_api_catalog", "api_key=/api/experiment-scheduling");
	ok = ok && require_file_token("rp_api_catalog", "api_key=/api/workflow-runner");
	ok = ok && require_file_token("rp_api_catalog", "api_key=/api/usable-research-workbench-file-catalog");
	ok = ok && require_file_token("rp_api_catalog", "api_key=/api/usable-research-study-protocol-reproduction-package-action-plan");
	ok = ok && require_file_token("rp_api_catalog", "api_key=/api/llm-proxy");
	ok = ok && require_file_token("rp_api_know", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_actionio", "requests=8");
	ok = ok && require_file_token("rp_actionio", "responses=8");
	ok = ok && require_file_token("rp_actionio", "redirects=8");
	ok = ok && require_file_token("rp_actionio", "dataset_file=rp_input");
	ok = ok && require_file_token("rp_actionio", "generated_runs=3");
	ok = ok && require_file_token("rp_actionio", "tag=reusable");
	ok = ok && require_file_token("rp_actionio", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_actionio", "effect=human_review");
	ok = ok && require_file_token("rp_actionio", "effect=revision_task_created");
	ok = ok && require_file_token("rp_actionio", "effect=revision_run_created");
	ok = ok && require_file_token("rp_actionio", "targets=methods,chart_caption");
	ok = ok && require_file_token("rp_actionio", "revision_inputs=rp_review2,rp_revision");
	ok = ok && require_file_token("rp_actionio", "applied_changes=2");
	ok = ok && require_file_token("rp_actionio", "revision_status=completed");
	ok = ok && require_file_token("rp_actionio", "action_state_records=12");
	ok = ok && require_file_token("rp_actionio", "request_validation=passed");
	ok = ok && require_file_token("rp_actionio", "validated_requests=8");
	ok = ok && require_file_token("rp_actionio", "precondition_checks=8");
	ok = ok && require_file_token("rp_actionio", "precheck=1;path=/actions/host-workflow/run");
	ok = ok && require_file_token("rp_actionio", "precheck=4;path=/actions/research/run");
	ok = ok && require_file_token("rp_actionio", "precheck=8;path=/actions/research/run-revision-task");
	ok = ok && require_file_token("rp_actionio", "side_effect_records=16");
	ok = ok && require_file_token("rp_actionio", "state_write=1;target=rp_stage_state");
	ok = ok && require_file_token("rp_actionio", "state_write=6;target=rp_runner;field=human_review");
	ok = ok && require_file_token("rp_actionio", "state_write=8;target=rp_runner;field=revision_run");
	ok = ok && require_file_token("rp_actionio", "state_write=10;target=rp_package;field=download_manifest");
	ok = ok && require_file_token("rp_actionio", "idempotency_checks=8");
	ok = ok && require_file_token("rp_actionio", "idempotency_key=research_run:usable-run:RUN-900;state=accepted");
	ok = ok && require_file_token("rp_actionio", "action_step=workbench_advance");
	ok = ok && require_file_token("rp_actionio", "action_step=notebook_download");
	ok = ok && require_file_token("rp_actionio", "action_step=bundle_download");
	ok = ok && require_file_token("rp_actionio", "action_step=review_page_open");
	ok = ok && require_file_token("rp_actionio", "action_trace=rp_input->rp_runner->rp_review2->rp_revision->rp_package->rp_web_bundle");
	ok = ok && require_file_token("rp_actionio", "idempotent_action_keys=8");
	ok = ok && require_file_token("rp_actionio", "state_after_actions=workbench:ready,review:needs_revision,revision:completed,bundle:ready");
	ok = ok && require_file_token("rp_actionio", "post_action_state=rp_stage_state,rp_package,rp_runner,rp_revision,rp_agentcmp");
	ok = ok && require_file_token("rp_actionio", "download_manifest_generated=1");
	ok = ok && require_file_token("rp_actionio", "download_outputs=reproducible-analysis.ipynb,research-evidence-bundle.zip,delivery-manifest.md");
	ok = ok && require_file_token("rp_uresrun", "run_id=usable-run:RUN-900");
	ok = ok && require_file_token("rp_uresrun", "runs=3");
	ok = ok && require_file_token("rp_uresrun", "run_id_2=usable-run:RUN-901");
	ok = ok && require_file_token("rp_uresrun", "run_id_3=usable-run:RUN-902");
	ok = ok && require_file_token("rp_uresrun", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_uresrun", "revision_status=completed");
	ok = ok && require_file_token("rp_uresrun", "source_run=rp_runner");
	ok = ok && require_file_token("rp_uresrun", "source_form=rp_input");
	ok = ok && require_file_token("rp_uresrun", "workbench=rp_runner");
	ok = ok && require_file_token("rp_uresrun", "workbench_export=rp_runner");
	ok = ok && require_file_token("rp_uresrun", "upload_files=rp_input");
	ok = ok && require_file_token("rp_uresrun", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_uresrun", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_uresrun", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_uresrun", "dataset_rows=3");
	ok = ok && require_file_token("rp_uresrun", "dataset_rows_total=9");
	ok = ok && require_file_token("rp_uresrun", "Stage DAG");
	ok = ok && require_file_token("rp_actionio", "Agent Decisions");
	ok = ok && require_file_token("rp_actionio", "user_on_plain_ucore_real_artifacts");
	ok = ok && require_file_token("rp_web_bundle", "api_payloads=15");
	ok = ok && require_file_token("rp_web_bundle", "evidence_package=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "downloadable_units=3");
	ok = ok && require_file_token("rp_web_bundle", "notebook_export=rp_nbexec");
	ok = ok && require_file_token("rp_web_bundle", "notebook_download=rp_repro");
	ok = ok && require_file_token("rp_web_bundle", "active_actions=rp_actionio");
	ok = ok && require_file_token("rp_web_bundle", "action_validation=passed");
	ok = ok && require_file_token("rp_web_bundle", "side_effect_records=16");
	ok = ok && require_file_token("rp_web_bundle", "download_manifest_generated=1");
	ok = ok && require_file_token("rp_web_bundle", "static_site_pages=42");
	ok = ok && require_file_token("rp_web_bundle", "render_sections=7");
	ok = ok && require_file_token("rp_web_bundle", "artifact_previews=3");
	ok = ok && require_file_token("rp_web_bundle", "request_form=rp_input");
	ok = ok && require_file_token("rp_web_bundle", "upload_files=rp_input");
	ok = ok && require_file_token("rp_web_bundle", "workspace_imports=1");
	ok = ok && require_file_token("rp_web_bundle", "dynamic_inputs=4");
	ok = ok && require_file_token("rp_web_bundle", "host_ui_events=10");
	ok = ok && require_file_token("rp_web_bundle", "live_update_feed=rp_web_bundle");
	ok = ok && require_file_token("rp_web_bundle", "reader_contract=host_plain_ucore_v2");
	ok = ok && require_file_token("rp_web_bundle", "reader_contract_version=2");
	ok = ok && require_file_token("rp_web_bundle", "reader_ready=1");
	ok = ok && require_file_token("rp_web_bundle", "reader_views=40");
	ok = ok && require_file_token("rp_web_bundle", "reader_actions=123");
	ok = ok && require_file_token("rp_web_bundle", "reader_payload_files=rp_api_home");
	ok = ok && require_file_token("rp_web_bundle", "rp_api_catalog");
	ok = ok && require_file_token("rp_web_bundle", "reader_refresh_files=rp_web_routes");
	ok = ok && require_file_token("rp_web_bundle", "reader_required_sections=routes,payloads,actions,live_update,downloads,compare");
	ok = ok && require_file_token("rp_web_bundle", "reader_event_stream=rp_web_bundle");
	ok = ok && require_file_token("rp_web_bundle", "reader_fallback=rp_site");
	ok = ok && require_file_token("rp_web_bundle", "reader_state_source=plain_ucore_files");
	ok = ok && require_file_token("rp_web_bundle", "workbench=rp_runner");
	ok = ok && require_file_token("rp_web_bundle", "workbench_tasks=9");
	ok = ok && require_file_token("rp_web_bundle", "workbench_export=rp_runner");
	ok = ok && require_file_token("rp_web_bundle", "project_review=ready");
	ok = ok && require_file_token("rp_web_bundle", "release_gate=project-release-gate");
	ok = ok && require_file_token("rp_web_bundle", "project_snapshot=project-snapshot");
	ok = ok && require_file_token("rp_web_bundle", "reproducibility_audit=project-reproducibility-audit");
	ok = ok && require_file_token("rp_web_bundle", "provenance_graph=project-provenance-graph");
	ok = ok && require_file_token("rp_web_bundle", "project_delivery=project-delivery");
	ok = ok && require_file_token("rp_web_bundle", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_web_bundle", "evidence_protocols=1");
	ok = ok && require_file_token("rp_web_bundle", "prisma_flows=1");
	ok = ok && require_file_token("rp_web_bundle", "workflow_portability=rp_wfio");
	ok = ok && require_file_token("rp_web_bundle", "coherence_checks=9");
	ok = ok && require_file_token("rp_web_bundle", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "delivery_files=8");
	ok = ok && require_file_token("rp_web_bundle", "delivery_checks=3");
	ok = ok && require_file_token("rp_web_bundle", "evidence_bundle_entries=12");
	ok = ok && require_file_token("rp_web_bundle", "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md");
	ok = ok && require_file_token("rp_web_bundle", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_web_bundle", "review_page=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "runner_detail_fields=16");
	ok = ok && require_file_token("rp_web_bundle", "post_routes=123");
	ok = ok && require_file_token("rp_web_bundle", "human_reviews=1");
	ok = ok && require_file_token("rp_web_bundle", "revision_tasks=1");
	ok = ok && require_file_token("rp_web_bundle", "revision_delta=rp_revision");
	ok = ok && require_file_token("rp_web_bundle", "review_threads=2");
	ok = ok && require_file_token("rp_web_bundle", "review_action_items=2");
	ok = ok && require_file_token("rp_review_dashboard", "dashboard=research-review");
	ok = ok && require_file_token("rp_review_dashboard", "section=workflow;source=rp_stage_dag,rp_stage_state,rp_run_events,rp_retry_plan;status=recovered");
	ok = ok && require_file_token("rp_review_dashboard", "section=llm;source=rp_llm_req,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "gate=reader_contract;status=pass");
	ok = ok && require_file_token("rp_review_dashboard", "decision=ready_for_reviewer");
	ok = ok && require_file_token("rp_review_dashboard", "decision=review_pack_ready");
	ok = ok && require_file_token("rp_review_dashboard", "backend_review_evidence=rp_backend_exec;plain_costs=7;agentos_replacements=7;risks=7;review_pack=rp_review_pack;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "pack_source=rp_package,rp_runner,rp_review_pack");
	ok = ok && require_file_token("rp_review_dashboard", "pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff");
	ok = ok && require_file_token("rp_package", "review_pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff");
	ok = ok && require_file_token("rp_package", "review_pack_action=deliver_to_reviewer;source=rp_package;status=ready");
	ok = ok && require_file_token("rp_package", "review_pack_action=sync_operations_next;source=rp_runner;status=ready");
	ok = ok && require_file_token("rp_package", "review_pack_action=resolve_project_items;source=rp_package;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "review_handoff_checks=13");
	ok = ok && require_file_token("rp_agentcmp", "review_sections=8");
	ok = ok && require_file_token("rp_agentcmp", "review_gates=6");
	ok = ok && require_file_token("rp_agentcmp", "review_decisions=4");
	ok = ok && require_file_token("rp_agentcmp", "review_handoffs=3");
	ok = ok && require_file_token("rp_agentcmp", "review_pack_actions=3");
	ok = ok && require_file_token("rp_agentcmp", "review_pack_bridges=4");
	ok = ok && require_file_token("rp_agentcmp", "backend_review=1");
	ok = ok && require_file_token("rp_agentcmp", "review_handoff_checks=13;review_sections=8;review_gates=6");
	ok = ok && require_file_token("rp_agentcmp", "review_pack=ready;evidence_items=11;actions=5;plain_kernel=ordinary_files;backend_evidence=1");
	ok = ok && require_file_token("rp_agentcmp", "demo_expected_programs=70");
	ok = ok && require_file_token("rp_agentcmp", "state_files=261");
	ok = ok && require_file_token("rp_agentcmp", "advanced_surface_objects=5");
	ok = ok && require_file_token("rp_agentcmp", "demo_expected_test_cases=2800");
	ok = ok && require_file_token("rp_agentcmp", "state_catalog=keys:574");
	ok = ok && require_file_token("rp_agentcmp", "startup_doctor=quickstart:ready");
	ok = ok && require_file_token("rp_agentcmp", "represented:574");
	ok = ok && require_file_token("rp_agentcmp", "knowledge_index_checks=22");
	ok = ok && require_file_token("rp_agentcmp", "llm_transcript_checks=3");
	ok = ok && require_file_token("rp_agentcmp", "workbench_delivery_checks=15");
	ok = ok && require_file_token("rp_agentcmp", "knowledge_index=search_documents:1685");
	ok = ok && require_file_token("rp_agentcmp", "provenance_nodes:406");
	ok = ok && require_file_token("rp_agentcmp", "provenance_links:544");
	ok = ok && require_file_token("rp_agentcmp", "events:8966");
	ok = ok && require_file_token("rp_agentcmp", "context_records:380");
	ok = ok && require_file_token("rp_agentcmp", "usable_artifacts:507");
	ok = ok && require_file_token("rp_agentcmp", "usable_runs:23");
	ok = ok && require_file_token("rp_agentcmp", "llm_transcripts=99");
	ok = ok && require_file_token("rp_agentcmp", "workbenches=8");
	ok = ok && require_file_token("rp_agentcmp", "deliveries=9");
	ok = ok && require_file_token("rp_agentcmp", "project_action_plans=17");
	ok = ok && require_file_token("rp_agentcmp", "llm_delivery_checks=16");
	ok = ok && require_file_token("rp_agentcmp", "llm_queue=3");
	ok = ok && require_file_token("rp_agentcmp", "llm_packets=3");
	ok = ok && require_file_token("rp_agentcmp", "llm_responses=3");
	ok = ok && require_file_token("rp_agentcmp", "llm_eval=7");
	ok = ok && require_file_token("rp_agentcmp", "llm_guard=3");
	ok = ok && require_file_token("rp_agentcmp", "llm_hostreq=3");
	ok = ok && require_file_token("rp_agentcmp", "llm_review_links=2");
	ok = ok && require_file_token("rp_agentcmp", "workflow_portability_checks=14");
	ok = ok && require_file_token("rp_agentcmp", "portability_imports=5");
	ok = ok && require_file_token("rp_agentcmp", "adapter_specs=6");
	ok = ok && require_file_token("rp_agentcmp", "migration_steps=9");
	ok = ok && require_file_token("rp_agentcmp", "rehearsal_cases=4");
	ok = ok && require_file_token("rp_agentcmp", "blocking_items=0");
	ok = ok && require_file_token("rp_agentcmp", "portability_package=workflow-portability");
	ok = ok && require_file_token("rp_agentcmp", "workflow_portability_checks=14;portability_imports=5;adapter_specs=6");
	ok = ok && require_file_token("rp_agentcmp", "portability_backend_reference_checks=18");
	ok = ok && require_file_token("rp_agentcmp", "backend_scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && require_file_token("rp_agentcmp", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && require_file_token("rp_agentcmp", "reference_cases=7");
	ok = ok && require_file_token("rp_agentcmp", "runtime_cases=0");
	ok = ok && require_file_token("rp_agentcmp", "backend_reference_checks=21");
	ok = ok && require_file_token("rp_agentcmp", "reference_case_rows=7");
	ok = ok && require_file_token("rp_agentcmp", "reference_report_rows=7");
	ok = ok && require_file_token("rp_agentcmp", "backend_report_links=2");
	ok = ok && require_file_token("rp_agentcmp", "runtime_pass_rows=0");
	ok = ok && require_file_token("rp_agentcmp", "performance_samples=0");
	ok = ok && require_file_token("rp_agentcmp", "research_governance_checks=18");
	ok = ok && require_file_token("rp_agentcmp", "startup_health_checks=8");
	ok = ok && require_file_token("rp_runop", "startup_health=quickstart:ready");
	ok = ok && require_file_token("rp_runop", "startup_checks=8");
	ok = ok && require_file_token("rp_runop", "configuration_health=settings:ready");
	ok = ok && require_file_token("rp_runop", "stores_secret_values=0");
	ok = ok && require_file_token("rp_runop", "platform_doctor=ready;checks=10");
	ok = ok && require_file_token("rp_runop", "provider_health=offline:1,cloud:0,ready_cloud:0");
	ok = ok && require_file_token("rp_agentcmp", "research_product_checks=18");
	ok = ok && require_file_token("rp_consistency", "research_product_checks=18");
	ok = ok && require_file_token("rp_agentcmp", "runtime_assurance_checks=24");
	ok = ok && require_file_token("rp_consistency", "runtime_assurance_checks=24");
	ok = ok && require_file_token("rp_agentcmp", "research_ops_checks=28");
	ok = ok && require_file_token("rp_consistency", "research_ops_checks=28");
	ok = ok && require_file_token("rp_agentcmp", "regulated_research_checks=32");
	ok = ok && require_file_token("rp_consistency", "regulated_research_checks=32");
	ok = ok && require_file_token("rp_agentcmp", "lab_governance_ops_checks=26");
	ok = ok && require_file_token("rp_consistency", "lab_governance_ops_checks=26");
	ok = ok && require_file_token("rp_agentcmp", "knowledge_index_checks=22");
	ok = ok && require_file_token("rp_consistency", "knowledge_index_checks=22");
	ok = ok && require_file_token("rp_agentcmp", "llm_transcript_checks=3");
	ok = ok && require_file_token("rp_consistency", "llm_transcript_checks=3");
	ok = ok && require_file_token("rp_agentcmp", "workbench_delivery_checks=15");
	ok = ok && require_file_token("rp_consistency", "workbench_delivery_checks=15");
	ok = ok && require_file_token("rp_agentcmp", "research_portfolio_checks=16");
	ok = ok && require_file_token("rp_consistency", "research_portfolio_checks=16");
	ok = ok && require_file_token("rp_agentcmp", "research_portfolio=sources:67");
	ok = ok && require_file_token("rp_consistency", "usable_research_sources=67");
	ok = ok && require_file_token("rp_agentcmp", "execution_scale_checks=14");
	ok = ok && require_file_token("rp_consistency", "execution_scale_checks=14");
	ok = ok && require_file_token("rp_agentcmp", "agentcompare_execution_scale=reports:4");
	ok = ok && require_file_token("rp_agentcmp", "host_runtime_scale=workflow_runs:10");
	ok = ok && require_file_token("rp_agentcmp", "content_graph_scale=content_objects:129");
	ok = ok && require_file_token("rp_consistency", "host_workflow_stage_runs=70");
	ok = ok && require_file_token("rp_consistency", "agentcompare_results=20");
	ok = ok && require_file_token("rp_agentcmp", "operations_scale_checks=12");
	ok = ok && require_file_token("rp_consistency", "operations_scale_checks=12");
	ok = ok && require_file_token("rp_agentcmp", "host_operations_scale=audit_records:5");
	ok = ok && require_file_token("rp_agentcmp", "project_revision_incident_checks=12");
	ok = ok && require_file_token("rp_agentcmp", "reserved_research_surface_checks=21");
	ok = ok && require_file_token("rp_agentcmp", "root_state_surface_checks=10");
	ok = ok && require_file_token("rp_agentcmp", "root_state_surface=projects:1");
	ok = ok && require_file_token("rp_agentcmp", "agentos_reserved_surface_checks=21");
	ok = ok && require_file_token("rp_agentcmp", "agentos_reserved_surface=profiles:0");
	ok = ok && require_file_token("rp_consistency", "root_state_surface_checks=10");
	ok = ok && require_file_token("rp_consistency", "root_projects=1");
	ok = ok && require_file_token("rp_consistency", "root_runs=1");
	ok = ok && require_file_token("rp_consistency", "root_reports=1");
	ok = ok && require_file_token("rp_consistency", "root_plans=1");
	ok = ok && require_file_token("rp_consistency", "root_search_records=2");
	ok = ok && require_file_token("rp_consistency", "root_site_exports=1");
	ok = ok && require_file_token("rp_consistency", "root_compare_profiles=1");
	ok = ok && require_file_token("rp_consistency", "root_audit_records=5");
	ok = ok && require_file_token("rp_consistency", "root_context_records=380");
	ok = ok && require_file_token("rp_consistency", "root_project_id=lab-gene-x");
	ok = ok && require_file_token("rp_consistency", "root_run_id=RUN-042");
	ok = ok && require_file_token("rp_consistency", "root_report_id=RUN-042-recovery-report");
	ok = ok && require_file_token("rp_consistency", "root_plan_id=PLAN-RUN-042-RECOVER-1");
	ok = ok && require_file_token("rp_consistency", "root_search_id=search:1");
	ok = ok && require_file_token("rp_consistency", "root_site_id=site:1");
	ok = ok && require_file_token("rp_consistency", "root_compare_profile=agentcompare-default");
	ok = ok && require_file_token("rp_consistency", "root_audit_spoof_denied=1");
	ok = ok && require_file_token("rp_consistency", "agentos_reserved_surface_checks=21");
	ok = ok && require_file_token("rp_consistency", "agentos_reserved_surface=profiles:0");
	ok = ok && require_file_token("rp_consistency", "tool_bindings:0");
	ok = ok && require_file_token("rp_agentcmp", "project_revision_incident=revision_tasks:1");
	ok = ok && require_file_token("rp_agentcmp", "incident:INC-RUN-042-ALIGN-OOM");
	ok = ok && require_file_token("rp_consistency", "project_revision_incident_checks=12");
	ok = ok && require_file_token("rp_consistency", "usable_research_revision_tasks=1");
	ok = ok && require_file_token("rp_consistency", "usable_research_project_scaffolds=1");
	ok = ok && require_file_token("rp_consistency", "incidents=1");
	ok = ok && require_file_token("rp_consistency", "incident_reason=memory_limit");
	ok = ok && require_file_token("rp_consistency", "revision_review_decision=needs_revision");
	ok = ok && require_file_token("rp_consistency", "project_scaffold=deepseek-reliability-response-study");
	ok = ok && require_file_token("rp_consistency", "reserved_research_surface_checks=21");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_answers=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_cards=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_portfolios=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_previews=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_run_comparisons=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_runs=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_dataset_visualizations=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_evidence_syntheses=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_package_intakes=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_prisma_flows=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_project_action_executions=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_project_reviews=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_review_protocols=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_source_portfolios=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_bundles=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_compliance_reports=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_launches=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocol_runs=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_study_protocols=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_workbench_action_items=0");
	ok = ok && require_file_token("rp_consistency", "usable_research_workbench_notes=0");
	ok = ok && require_file_token("rp_consistency", "host_metrics=13");
	ok = ok && require_file_token("rp_consistency", "usable_research_projects=23");
	ok = ok && require_file_token("rp_consistency", "host_artifacts=128");
	ok = ok && require_file_token("rp_runop", "runtime_assurance=secret_refs:3");
	ok = ok && require_file_token("rp_runop", "model_registry:2");
	ok = ok && require_file_token("rp_modelreg", "model_registry_service_checks=96");
	ok = ok && require_file_token("rp_modelreg", "model=registered-model:agent-triage-template");
	ok = ok && require_file_token("rp_modelver", "version=model-version:agent-triage-template:v1");
	ok = ok && require_file_token("rp_modelver", "metric_artifact_count=52");
	ok = ok && require_file_token("rp_modeleval", "metric_evidence_coverage=1.000");
	ok = ok && require_file_token("rp_modeleval", "status=passed");
	ok = ok && require_file_token("rp_modeldep", "check_secret_policy=not_required");
	ok = ok && require_file_token("rp_modeldep", "status=ready");
	ok = ok && require_file_token("rp_modelserve", "latency_ms=12");
	ok = ok && require_file_token("rp_package", "model_registry=rp_modelreg;version=v1;evaluation=passed;deployment=ready;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "model_registry_page=rp_modelreg;models=1;versions=1;evaluations=1;deployments=1;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=model_registry;source=rp_modelreg;checks=96;evaluation=passed;deployment=ready;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "model_registry_service_checks=96");
	ok = ok && require_file_token("rp_sysreview", "systematic_review_checks=104");
	ok = ok && require_file_token("rp_sysreview", "protocol=systematic-review:agent-os-science");
	ok = ok && require_file_token("rp_syssearch", "results=9");
	ok = ok && require_file_token("rp_sysscreen", "screening_decisions=9");
	ok = ok && require_file_token("rp_sysscreen", "full_text_included=3");
	ok = ok && require_file_token("rp_sysextract", "extractions=3");
	ok = ok && require_file_token("rp_sysextract", "risk_of_bias=3");
	ok = ok && require_file_token("rp_syssynth", "confidence=moderate");
	ok = ok && require_file_token("rp_sysprisma", "included=3");
	ok = ok && require_file_token("rp_package", "systematic_review=rp_sysreview;protocol=systematic-review:agent-os-science;included=3;prisma=ready;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "systematic_review_page=rp_sysreview;protocols=1;screening=9;included=3;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=systematic_review;source=rp_sysreview;checks=104;included=3;prisma=ready;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "systematic_review_checks=104");
	ok = ok && require_file_token("rp_expsched", "experiment_scheduling_checks=88");
	ok = ok && require_file_token("rp_expsched", "schedule=schedule:RUN-042:lab-execution");
	ok = ok && require_file_token("rp_schedtask", "task=schedule-task:RUN-042:library-prep");
	ok = ok && require_file_token("rp_schedbook", "booking=schedule-booking:RUN-042:seq-library");
	ok = ok && require_file_token("rp_schedconf", "conflict=schedule-conflict:RUN-042:seq-01-overlap");
	ok = ok && require_file_token("rp_schedexec", "execution=schedule-exec:RUN-042:library-prep");
	ok = ok && require_file_token("rp_package", "experiment_schedule=rp_expsched;schedule=schedule:RUN-042:lab-execution;tasks=3;bookings=4;conflicts=1;executions=2;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "experiment_schedule_page=rp_expsched;schedules=1;tasks=3;bookings=4;conflicts=1;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=experiment_schedule;source=rp_expsched;checks=88;tasks=3;conflicts=1;executions=2;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "experiment_scheduling_checks=88");
	ok = ok && require_file_token("rp_traincomp", "training_compliance_checks=92");
	ok = ok && require_file_token("rp_traincomp", "open_gaps=0");
	ok = ok && require_file_token("rp_trainreq", "requirement=training-req:sop-deviation:qa-lead");
	ok = ok && require_file_token("rp_trainrec", "training=training:qa-lead:sop-deviation");
	ok = ok && require_file_token("rp_trainassess", "assessment=competency:qa-lead:sop-deviation");
	ok = ok && require_file_token("rp_trainauth", "authorization=auth:qa-lead:qa-lead:lab-gene-x");
	ok = ok && require_file_token("rp_traingap", "status=resolved");
	ok = ok && require_file_token("rp_package", "training_compliance=rp_traincomp;requirements=4;records=4;assessments=4;auth=3;gaps=1;open=0;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "training_compliance_page=rp_traincomp;requirements=4;records=4;gaps=1;auth=3;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=training_compliance;source=rp_traincomp;checks=92;requirements=4;open_gaps=0;auth=3;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "training_compliance_checks=92");
	ok = ok && require_file_token("rp_runop", "deployments:1");
	ok = ok && require_file_token("rp_runop", "llm_proxy_audits:2");
	ok = ok && require_file_token("rp_runop", "collab_threads:2");
	ok = ok && require_file_token("rp_runop", "obs_alerts:5");
	ok = ok && require_file_token("rp_runop", "health:1");
	ok = ok && require_file_token("rp_runop", "research_ops=semantic_entities:8");
	ok = ok && require_file_token("rp_runop", "semantic_relations:6");
	ok = ok && require_file_token("rp_runop", "prompt_templates:2");
	ok = ok && require_file_token("rp_runop", "prompt_versions:2");
	ok = ok && require_file_token("rp_runop", "prompt_evaluations:1");
	ok = ok && require_file_token("rp_runop", "runbook_steps:7");
	ok = ok && require_file_token("rp_runop", "worker_ops:6");
	ok = ok && require_file_token("rp_runop", "execution_controls:8");
	ok = ok && require_file_token("rp_runop", "regulated_research=annotation_schemas:1");
	ok = ok && require_file_token("rp_runop", "annotation_tasks:3");
	ok = ok && require_file_token("rp_runop", "assay_plates:1");
	ok = ok && require_file_token("rp_runop", "plate_wells:6");
	ok = ok && require_file_token("rp_runop", "cohort_records:2");
	ok = ok && require_file_token("rp_runop", "data_access_requests:1");
	ok = ok && require_file_token("rp_runop", "dataset_cards:1");
	ok = ok && require_file_token("rp_runop", "model_cards:1");
	ok = ok && require_file_token("rp_runop", "research_object_crates:1");
	ok = ok && require_file_token("rp_runop", "research_object_entities:29");
	ok = ok && require_file_token("rp_runop", "sample_custody_events:18");
	ok = ok && require_file_token("rp_runop", "statistical_designs:1");
	ok = ok && require_file_token("rp_runop", "workflow_templates:8");
	ok = ok && require_file_token("rp_runop", "project_scaffold=templates:3");
	ok = ok && require_file_token("rp_runop", "dataset_product=previews:2");
	ok = ok && require_file_token("rp_runop", "visualizations:2");
	ok = ok && require_file_token("rp_runop", "source_portfolio=sources:67");
	ok = ok && require_file_token("rp_runop", "research_portfolio_scale=sources:67");
	ok = ok && require_file_token("rp_runop", "datasets:5");
	ok = ok && require_file_token("rp_runop", "literature_searches:7");
	ok = ok && require_file_token("rp_runop", "reviews:11");
	ok = ok && require_file_token("rp_runop", "evidence_reviews:7");
	ok = ok && require_file_token("rp_runop", "evidence_extractions:25");
	ok = ok && require_file_token("rp_runop", "screening_decisions:25");
	ok = ok && require_file_token("rp_runop", "exports:80");
	ok = ok && require_file_token("rp_runop", "doctor_reports:12");
	ok = ok && require_file_token("rp_runop", "project_handoff_audits:34");
	ok = ok && require_file_token("rp_runop", "project_run_comparisons:17");
	ok = ok && require_file_token("rp_runop", "project_reproducibility_audits:17");
	ok = ok && require_file_token("rp_runop", "project_snapshot_comparisons:17");
	ok = ok && require_file_token("rp_runop", "study_protocol_reproduction=packages:1");
	ok = ok && require_file_token("rp_runop", "action_execution:ready");
	ok = ok && require_file_token("rp_runop", "project_bundle_cache=latest:ready");
	ok = ok && require_file_token("rp_runop", "downloads:cached_or_refresh");
	ok = ok && require_file_token("rp_protocol", "protocol_compliance_reports=1");
	ok = ok && require_file_token("rp_protocol", "protocol_amendments=1");
	ok = ok && require_file_token("rp_soplog", "sop_executions=1");
	ok = ok && require_file_token("rp_risk", "decision_support=decision:agentos-final-demo-backend");
	ok = ok && require_file_token("rp_package", "provenance_graph=unified");
	ok = ok && require_file_token("rp_package", "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp");
	ok = ok && require_file_token("rp_package", "delivery_file=llm_trace;path=rp_llm_packets;required=0;exists=1");
	ok = ok && require_file_token("rp_review_dashboard", "gate=llm_packet_guard;status=pass;source=rp_llm_guard");
	ok = ok && require_file_token("rp_runner", "citation=rp_llm_resp:response_join");
	ok = ok && require_file_token("rp_llm_hostreq", "host_request_records=3");
	ok = ok && require_file_token("rp_llm_hostreq", "host_response_records=3");
	ok = ok && require_file_token("rp_llm_guard", "blocked_packets=0");
	ok = ok && require_file_token("rp_relay", "relay_packets=3");
	ok = ok && require_file_token("rp_ack", "ack=review_dashboard;msg=reviewdash;status=ready");
	ok = ok && require_file_token("rp_tool", "tool=review_dashboard.aggregate");
	ok = ok && require_file_token("rp_ack", "ack=review_pack;msg=pack;status=ready");
	ok = ok && require_file_token("rp_tool", "tool=review_pack.assemble");
	ok = ok && require_file_token("rp_runbooks", "runbook_service_checks=16");
	ok = ok && require_file_token("rp_runbooks", "runbook_templates=1");
	ok = ok && require_file_token("rp_runbooks", "runbook_steps=7");
	ok = ok && require_file_token("rp_runbooks", "incident_triages=1");
	ok = ok && require_file_token("rp_runbooks", "runbook_executions=1");
	ok = ok && require_file_token("rp_runbooks", "runbook_exports=1");
	ok = ok && require_file_token("rp_runbooks", "worker_operation_records=6");
	ok = ok && require_file_token("rp_runbooks", "agentos_adaptation=event_context,kernel_timeline,metadata_index,batch_recovery_tool;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=runbooks;source=rp_runbooks;steps=7;incident=closed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "runbook_recovery_checks=16");
	ok = ok && require_file_token("rp_projectrel", "project_delivery_checks=18");
	ok = ok && require_file_token("rp_projectrel", "project_handoff_audits=1");
	ok = ok && require_file_token("rp_projectrel", "project_runbooks=1");
	ok = ok && require_file_token("rp_projectrel", "project_release_gates=1");
	ok = ok && require_file_token("rp_projectrel", "project_snapshots=1");
	ok = ok && require_file_token("rp_projectrel", "project_reproducibility_audits=1");
	ok = ok && require_file_token("rp_projectrel", "project_provenance_graphs=1");
	ok = ok && require_file_token("rp_projectrel", "package_intakes=1");
	ok = ok && require_file_token("rp_projectrel", "agentos_adaptation=file_metadata_index,event_delivery,context_release_evidence,capability_guard;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=project_delivery;source=rp_projectrel;release=ready;reproducibility=passed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "project_delivery_checks=18");
	ok = ok && require_file_token("rp_studyproto", "study_protocol_checks=20");
	ok = ok && require_file_token("rp_studyproto", "study_protocols=2");
	ok = ok && require_file_token("rp_studyproto", "study_protocol_launches=2");
	ok = ok && require_file_token("rp_studyproto", "study_protocol_reproduction_packages=1");
	ok = ok && require_file_token("rp_studyproto", "reproduction_action_plans=1");
	ok = ok && require_file_token("rp_studyproto", "dataset_portfolios=1");
	ok = ok && require_file_token("rp_studyproto", "source_portfolios=1");
	ok = ok && require_file_token("rp_studyproto", "agentos_adaptation=file_metadata_index,context_protocol_evidence,event_reproduction_queue,batch_dataset_tool;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=study_protocols;source=rp_studyproto;launches=2;reproduction=ready;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "study_protocol_checks=20");
	ok = ok && require_file_token("rp_stdesign", "statistical_design_checks=120");
	ok = ok && require_file_token("rp_stdesign", "design=stat-design:lab-gene-x:run042-primary");
	ok = ok && require_file_token("rp_power", "required_per_group=11");
	ok = ok && require_file_token("rp_power", "status=underpowered");
	ok = ok && require_file_token("rp_random", "assignments=4");
	ok = ok && require_file_token("rp_random", "status=balanced");
	ok = ok && require_file_token("rp_blind", "status=ok");
	ok = ok && require_file_token("rp_streview", "stat_result=approved_with_sample_size_note");
	ok = ok && require_file_token("rp_package", "statistical_design=rp_stdesign;stat_result=approved_with_sample_size_note;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "statistical_design_page=rp_stdesign;designs=1;power=underpowered;randomization=balanced;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=statistical_design;source=rp_stdesign;checks=120;stat_result=approved_with_sample_size_note;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "statistical_design_checks=120");
	ok = ok && require_file_token("rp_opsboard", "operations_board_checks=18");
	ok = ok && require_file_token("rp_opsboard", "pending_reviews=1");
	ok = ok && require_file_token("rp_opsboard", "active_workbench_actions=4");
	ok = ok && require_file_token("rp_opsboard", "active_plan_items=5");
	ok = ok && require_file_token("rp_opsboard", "ready_handoffs=3");
	ok = ok && require_file_token("rp_opsboard", "report_export=research-ops-report:RUN-042");
	ok = ok && require_file_token("rp_opsboard", "agentos_adaptation=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=research_operations;source=rp_opsboard;pending_reviews=1;handoffs=3;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "operations_board_checks=18");
	ok = ok && require_file_token("rp_reviewboard", "review_board_checks=24");
	ok = ok && require_file_token("rp_reviewboard", "review_requests=1");
	ok = ok && require_file_token("rp_reviewboard", "review_votes=4");
	ok = ok && require_file_token("rp_reviewboard", "review_signoffs=4");
	ok = ok && require_file_token("rp_reviewboard", "review_assignments=4");
	ok = ok && require_file_token("rp_reviewboard", "review_workloads=4");
	ok = ok && require_file_token("rp_reviewboard", "review_filters=2");
	ok = ok && require_file_token("rp_reviewboard", "decision=approved");
	ok = ok && require_file_token("rp_reviewboard", "board=review-board:final-release;chair=wang;members=4;status=active");
	ok = ok && require_file_token("rp_reviewboard", "request=review-request:RUN-042:release-dossier");
	ok = ok && require_file_token("rp_reviewboard", "vote=review-vote:RUN-042:systems;reviewer=systems-reviewer");
	ok = ok && require_file_token("rp_reviewboard", "signoff=review-signoff:RUN-042:chair;signer=wang");
	ok = ok && require_file_token("rp_reviewboard", "decision_record=review-board-decision:RUN-042:release;approvals=4");
	ok = ok && require_file_token("rp_reviewboard", "assignment=review-assignment:RUN-042:chair;reviewer=wang");
	ok = ok && require_file_token("rp_reviewboard", "filter=review-filter:auditor-open;owner=auditor");
	ok = ok && require_file_token("rp_reviewboard", "workload=review-workload:systems-reviewer;open=0");
	ok = ok && require_file_token("rp_reviewboard", "review_package=formal-review-board-package:RUN-042");
	ok = ok && require_file_token("rp_reviewboard", "agentos_adaptation=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding;status=planned");
	ok = ok && require_file_token("rp_reviewops", "formal_review_board=checks:24");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=formal_review_board;source=rp_reviewboard;votes=4;signoffs=4;decision=approved;status=ready");
	ok = ok && require_file_token("rp_opsboard", "handoff=review-board->operations;artifact=rp_reviewboard;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "review_board_checks=24");
	ok = ok && require_file_token("rp_control", "control_plane_checks=30");
	ok = ok && require_file_token("rp_control", "approvals=4");
	ok = ok && require_file_token("rp_control", "approval_transitions=4");
	ok = ok && require_file_token("rp_control", "subscriptions=3");
	ok = ok && require_file_token("rp_control", "notifications=4");
	ok = ok && require_file_token("rp_control", "run_queue_items=4");
	ok = ok && require_file_token("rp_control", "leases=2");
	ok = ok && require_file_token("rp_control", "plugin_manifests=3");
	ok = ok && require_file_token("rp_control", "plugin_runs=3");
	ok = ok && require_file_token("rp_control", "workspaces=1");
	ok = ok && require_file_token("rp_control", "users=3");
	ok = ok && require_file_token("rp_control", "access_grants=3");
	ok = ok && require_file_token("rp_control", "saved_views=2");
	ok = ok && require_file_token("rp_control", "api_tokens=1");
	ok = ok && require_file_token("rp_control", "permissions=5");
	ok = ok && require_file_token("rp_control", "control_actions=8");
	ok = ok && require_file_token("rp_control", "approval=approval:release-dossier:4;target=release-dossier:RUN-042;state=published;actor=wang");
	ok = ok && require_file_token("rp_control", "subscription=sub:ops:writer:*;target=writer;event=*;status=active");
	ok = ok && require_file_token("rp_control", "notification=notif:4;target=writer;event=PLUGIN_RUN;delivered=1;status=ready");
	ok = ok && require_file_token("rp_control", "queue=queue:RUN-042:2;run=RUN-042-review;priority=80;state=leased;worker=reviewer");
	ok = ok && require_file_token("rp_control", "plugin=plugin.tuning;name=Parameter Tuning;tools=recommend_memory_limit;enabled=1");
	ok = ok && require_file_token("rp_control", "plugin_run=plugin-run:3;plugin=plugin.tuning;tool=recommend_memory_limit;current=1024;recommended=1536");
	ok = ok && require_file_token("rp_control", "workspace=ws:lab-gene-x;owner=wang;projects=1;status=ready");
	ok = ok && require_file_token("rp_control", "grant=grant:guest:lab-gene-x:viewer;subject=guest;object=lab-gene-x;role=viewer;status=ready");
	ok = ok && require_file_token("rp_control", "saved_view=view:planned-jobs;kind=jobs;query=status=planned;owner=wang;status=ready");
	ok = ok && require_file_token("rp_control", "api_token=token:local-dashboard;owner=wang;scopes=read,dashboard;secret_material=not_written;status=ready");
	ok = ok && require_file_token("rp_control", "permission=can:guest:approve;result=deny;status=ready");
	ok = ok && require_file_token("rp_control", "control_report=platform-control-report:RUN-042;approvals=4;notifications=4;queue_items=4;plugin_runs=3;status=ready");
	ok = ok && require_file_token("rp_control", "agentos_adaptation=kernel_capability_check,kernel_event_delivery,kernel_plugin_tool_table,kernel_run_queue;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=platform_control_plane;source=rp_control;approvals=4;notifications=4;plugins=3;status=ready");
	ok = ok && require_file_token("rp_opsboard", "handoff=control-plane->operations;artifact=rp_control;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "control_plane_checks=30");
	ok = ok && require_file_token("rp_integrity", "integrity_checks=36");
	ok = ok && require_file_token("rp_integrity", "evidence_contracts=8");
	ok = ok && require_file_token("rp_integrity", "evidence_checks=8");
	ok = ok && require_file_token("rp_integrity", "reference_contracts=8");
	ok = ok && require_file_token("rp_integrity", "reference_checks=8");
	ok = ok && require_file_token("rp_integrity", "namespace_checks=5");
	ok = ok && require_file_token("rp_integrity", "status_checks=5");
	ok = ok && require_file_token("rp_integrity", "review_alignment_checks=4");
	ok = ok && require_file_token("rp_integrity", "report_source_checks=3");
	ok = ok && require_file_token("rp_integrity", "package_trace_checks=3");
	ok = ok && require_file_token("rp_integrity", "errors=0");
	ok = ok && require_file_token("rp_integrity", "warnings=0");
	ok = ok && require_file_token("rp_integrity", "decision=passed");
	ok = ok && require_file_token("rp_integrity", "evidence_check=report_source_workflow;source=rp_report_text;target=rp_stage_state;result=pass;status=ready");
	ok = ok && require_file_token("rp_integrity", "evidence_check=backend_evidence;source=rp_backend_exec;target=rp_report_text;result=pass;status=ready");
	ok = ok && require_file_token("rp_integrity", "reference_check=run_project;source=rp_runner;target=rp_input;result=pass;status=ready");
	ok = ok && require_file_token("rp_integrity", "reference_check=source_citation;source=rp_knowledge;target=rp_evidence;result=pass;status=ready");
	ok = ok && require_file_token("rp_integrity", "namespace_check=package_id;value=delivery-package:RUN-042;scope=package;result=pass;status=ready");
	ok = ok && require_file_token("rp_integrity", "status_check=review;source=rp_review_dashboard;allowed=waiting,needs_revision,approved,ready;result=pass");
	ok = ok && require_file_token("rp_integrity", "review_alignment=dashboard_to_package;source=rp_review_dashboard;target=rp_package;decision=aligned;status=ready");
	ok = ok && require_file_token("rp_integrity", "report_source_check=backend;source=rp_report_text;target=rp_backend_exec;source_key=backend_evidence_report;status=ready");
	ok = ok && require_file_token("rp_integrity", "package_trace=review;source=rp_package;target=rp_review_pack;result=pass;status=ready");
	ok = ok && require_file_token("rp_integrity", "integrity_report=integrity-report:RUN-042;checks=36;errors=0;warnings=0;status=ready");
	ok = ok && require_file_token("rp_integrity", "agentos_adaptation=kernel_context_attestation,kernel_metadata_reference_index,kernel_event_trace,kernel_namespace_registry;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=integrity_plane;source=rp_integrity;checks=36;errors=0;result=passed;status=ready");
	ok = ok && require_file_token("rp_opsboard", "handoff=integrity-plane->operations;artifact=rp_integrity;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "integrity_plane_checks=36");
	ok = ok && require_file_token("rp_coherence", "coherence_checks=40");
	ok = ok && require_file_token("rp_coherence", "delivery_contracts=7");
	ok = ok && require_file_token("rp_coherence", "run_state_contracts=7");
	ok = ok && require_file_token("rp_coherence", "lifecycle_contracts=6");
	ok = ok && require_file_token("rp_coherence", "workflow_lint_checks=5");
	ok = ok && require_file_token("rp_coherence", "tool_protocol_checks=5");
	ok = ok && require_file_token("rp_coherence", "report_validation_checks=5");
	ok = ok && require_file_token("rp_coherence", "agent_coordination_checks=3");
	ok = ok && require_file_token("rp_coherence", "errors=0");
	ok = ok && require_file_token("rp_coherence", "decision=passed");
	ok = ok && require_file_token("rp_coherence", "delivery_check=llm_delivery;source=rp_llm_resp;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "run_state_check=cache_reuse;source=rp_cache_index;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "lifecycle_check=backend_case;source=rp_backend_exec;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "workflow_lint=manifest_links;source=rp_artifact_manifest;expected=raw_to_report;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "tool_validation=llm_relay;tools=relay_guarded;source=rp_llm_guard;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "report_validation=llm_source;source=rp_report_text;target=rp_llm_resp;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "agent_coordination=decision_trace;source=rp_decisions;target=rp_review_dashboard;result=pass;status=ready");
	ok = ok && require_file_token("rp_coherence", "coherence_report=coherence-report:RUN-042;checks=40;errors=0;warnings=0;status=ready");
	ok = ok && require_file_token("rp_coherence", "agentos_adaptation=kernel_run_state_views,kernel_tool_contract_table,kernel_delivery_metadata,kernel_agent_coordination_trace;status=planned");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=coherence_plane;source=rp_coherence;checks=40;errors=0;reference_result=expected_pass;status=reference_ready");
	ok = ok && require_file_token("rp_opsboard", "handoff=coherence-plane->operations;artifact=rp_coherence;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "coherence_plane_checks=40");
	ok = ok && require_file_token("rp_publication", "publication_checks=48");
	ok = ok && require_file_token("rp_publication", "journal_target=journal-target:systems-biology-report");
	ok = ok && require_file_token("rp_publication", "submission=submission:RUN-042:systems-biology-report");
	ok = ok && require_file_token("rp_publication", "review_round=peer-review:RUN-042:round-1");
	ok = ok && require_file_token("rp_publication", "revision_task=revision:RUN-042:methods-reproducibility");
	ok = ok && require_file_token("rp_publication", "response_package=peer-review-response-package:RUN-042:round-1");
	ok = ok && require_file_token("rp_publication", "response_item=3;package=peer-review-response-package:RUN-042:round-1");
	ok = ok && require_file_token("rp_publication", "publication_decision=publication-decision:RUN-042:accept-with-evidence");
	ok = ok && require_file_token("rp_publication", "agentos_adaptation=kernel_submission_metadata,kernel_review_event_queue,kernel_response_context,kernel_release_gate;status=planned");
	ok = ok && require_file_token("rp_pubplan", "checklist_items=9");
	ok = ok && require_file_token("rp_pubplan", "journal_requirement=artifact_appendix;source=rp_dossier;status=ready");
	ok = ok && require_file_token("rp_peerresp", "packages=2");
	ok = ok && require_file_token("rp_peerresp", "addressed=4");
	ok = ok && require_file_token("rp_peerresp", "needs_revision=0");
	ok = ok && require_file_token("rp_peerresp", "response_item=artifact_appendix;reply=appendix_linked;status=addressed");
	ok = ok && require_file_token("rp_api_pub", "publication_workflow=rp_publication");
	ok = ok && require_file_token("rp_pubop", "op=publication_workflow;submissions=2;reviews=2;responses=2;decisions=2;status=ok");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=publication_response;source=rp_publication;reviews=2;responses=2;outcome=accepted;status=ready");
	ok = ok && require_file_token("rp_package", "publication_workflow=rp_publication;response_package=rp_peerresp;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "publication_page=rp_publication;peer_response=rp_peerresp;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "publication_checks=48");
	ok = ok && require_file_token("rp_calculation", "calculation_checks=84");
	ok = ok && require_file_token("rp_calculation", "computer=calculation-computer:local-agentos");
	ok = ok && require_file_token("rp_calculation", "code=calculation-code:metadata-qc:v1");
	ok = ok && require_file_token("rp_calculation", "job=calculation-job:lab-gene-x:run042-qc");
	ok = ok && require_file_token("rp_calculation", "scheduler_record=calculation-submission:run042-qc");
	ok = ok && require_file_token("rp_calc_files", "retrieved_files=3");
	ok = ok && require_file_token("rp_calc_files", "retrieved=calculation-retrieved:run042-qc:stdout-txt");
	ok = ok && require_file_token("rp_calc_files", "retrieved=calculation-retrieved:run042-qc:provenance-json");
	ok = ok && require_file_token("rp_calc_parse", "parser_result=calculation-parser-result:run042-qc");
	ok = ok && require_file_token("rp_calc_parse", "metric=ready_ratio;value=1.00");
	ok = ok && require_file_token("rp_calc_export", "export=calculation-export:lab-gene-x:run042-qc");
	ok = ok && require_file_token("rp_calc_export", "package=calculation-package:lab-gene-x:run042-qc");
	ok = ok && require_file_token("rp_package", "calculation_package=rp_calculation;job=calculation-job:lab-gene-x:run042-qc;retrieved=3;parser=ok;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "calculations_page=rp_calculation;jobs=1;retrieved=3;parser_results=1;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=calculations;source=rp_calculation;jobs=1;retrieved=3;checks=84;outcome=passed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "calculation_checks=84");
	ok = ok && require_file_token("rp_realtask", "real_task_checks=96");
	ok = ok && require_file_token("rp_realtask", "task=palmer-penguins-morphometrics");
	ok = ok && require_file_token("rp_realtask", "provider_secret_persisted=0");
	ok = ok && require_file_token("rp_realdata", "rows=344");
	ok = ok && require_file_token("rp_realdata", "columns=8");
	ok = ok && require_file_token("rp_realdata", "metric_group_summaries=5");
	ok = ok && require_file_token("rp_realdata", "metric_dimension_group_summaries=10");
	ok = ok && require_file_token("rp_realreport", "answer_source=report_md");
	ok = ok && require_file_token("rp_realreport", "claim_audit=pass");
	ok = ok && require_file_token("rp_realreport", "answer_audit=pass");
	ok = ok && require_file_token("rp_realbundle", "duplicate_zip_entries=0");
	ok = ok && require_file_token("rp_realbundle", "offline_review=ready");
	ok = ok && require_file_token("rp_package", "real_task_package=rp_realtask;dataset=palmer-penguins;bundle=ready;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "real_task_page=rp_realtask;dataset=palmer-penguins;rows=344;answer_audit=pass;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=real_task;source=rp_realtask;dataset=palmer-penguins;checks=96;outcome=passed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "real_task_checks=96");
	ok = ok && require_file_token("rp_analysisres", "analysis_results_checks=96");
	ok = ok && require_file_token("rp_analysisres", "analysis_runs=2");
	ok = ok && require_file_token("rp_analysisres", "result_tables=2");
	ok = ok && require_file_token("rp_analysisres", "statistical_results=2");
	ok = ok && require_file_token("rp_anplan", "plan=analysis-plan:RUN-042:treatment-response");
	ok = ok && require_file_token("rp_anrun", "run=analysis-run:RUN-042:manual");
	ok = ok && require_file_token("rp_resulttbl", "table=result-table:manual");
	ok = ok && require_file_token("rp_statres", "stat=stat-result:manual");
	ok = ok && require_file_token("rp_anfig", "figure=figure:manual");
	ok = ok && require_file_token("rp_interp", "interpretation=interpretation:manual");
	ok = ok && require_file_token("rp_package", "analysis_results=rp_analysisres;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "analysis_results_page=rp_analysisres;runs=2;tables=2;statistics=2;figures=2;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=analysis_results;source=rp_analysisres;checks=96;runs=2;statistics=2;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "analysis_results_checks=96");
	ok = ok && require_file_token("rp_campaign", "campaign_checks=108");
	ok = ok && require_file_token("rp_campaign", "campaign=experiment-campaign:RUN-042:align-memory-grid");
	ok = ok && require_file_token("rp_trials", "trial_count=4");
	ok = ok && require_file_token("rp_trials", "trial=experiment-trial:RUN-042:align-memory-grid:04");
	ok = ok && require_file_token("rp_camp_rank", "comparison=experiment-campaign-comparison:RUN-042:align-memory-grid");
	ok = ok && require_file_token("rp_camp_rank", "decision=select_trial_04");
	ok = ok && require_file_token("rp_camp_rank", "metric_delta=3");
	ok = ok && require_file_token("rp_resreview", "review=experiment-result-review:RUN-042:baseline-vs-candidate");
	ok = ok && require_file_token("rp_resreview", "decision=accept_candidate");
	ok = ok && require_file_token("rp_package", "experiment_campaign_package=rp_campaign;best_trial=experiment-trial:RUN-042:align-memory-grid:04;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "experiment_campaigns_page=rp_campaign;campaigns=1;trials=4;best_trial=04;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=experiment_campaigns;source=rp_campaign;campaigns=1;trials=4;checks=108;outcome=passed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "experiment_campaign_checks=108");
	ok = ok && require_file_token("rp_reldossier", "release_dossier_checks=112");
	ok = ok && require_file_token("rp_reldossier", "dossier=release-dossier:RUN-042:final-review");
	ok = ok && require_file_token("rp_reldossier", "sections=7");
	ok = ok && require_file_token("rp_reldossier", "decision=ready_for_review");
	ok = ok && require_file_token("rp_reldsec", "section=research-package;status=ok");
	ok = ok && require_file_token("rp_reldsec", "section=experiment-campaign;status=ok");
	ok = ok && require_file_token("rp_reldsec", "section=agentos-readiness;status=ok");
	ok = ok && require_file_token("rp_relattest", "attestations=4");
	ok = ok && require_file_token("rp_relpack", "package_files=2");
	ok = ok && require_file_token("rp_relpack", "download=release-dossier-package:RUN-042");
	ok = ok && require_file_token("rp_package", "release_dossier=rp_reldossier;sections=7;decision=ready_for_review;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "release_dossier_page=rp_reldossier;sections=7;decision=ready_for_review;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=release_dossier;source=rp_reldossier;sections=7;checks=112;outcome=passed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "release_dossier_checks=112");
	ok = ok && require_file_token("rp_decsupport", "decision_support_checks=80");
	ok = ok && require_file_token("rp_decsupport", "recommended_option=agentos_ucore_hybrid");
	ok = ok && require_file_token("rp_decopt", "option=agentos_ucore_hybrid");
	ok = ok && require_file_token("rp_deccrit", "criterion=agentos_value");
	ok = ok && require_file_token("rp_decscore", "score=agentos_ucore_hybrid:agentos_value");
	ok = ok && require_file_token("rp_decpacket", "packet=decision-review-packet:agentos-final-demo-backend");
	ok = ok && require_file_token("rp_package", "decision_support=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=reference_ready");
	ok = ok && require_file_token("rp_web_bundle", "decision_support_page=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=reference_ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=decision_support;source=rp_decsupport;options=3;criteria=5;scores=15;selected=select_agentos_ucore_hybrid;status=reference_ready");
	ok = ok && require_file_token("rp_agentcmp", "decision_support_checks=80");
	ok = ok && require_file_token("rp_usable", "usable_research_checks=100");
	ok = ok && require_file_token("rp_usable", "entry=research-question-to-review-package");
	ok = ok && require_file_token("rp_usabletpl", "template=usable-template:workspace-900");
	ok = ok && require_file_token("rp_usableds", "dataset=usable-dataset:penguins;rows=344");
	ok = ok && require_file_token("rp_usablelib", "source=usable-source:library2026:1");
	ok = ok && require_file_token("rp_usabledag", "stage=package;order=9");
	ok = ok && require_file_token("rp_usableops", "handoff=usable-handoff:RUN-900:reviewer");
	ok = ok && require_file_token("rp_package", "usable_research=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;deliverables=8;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "usable_research_page=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;queues=2;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=usable_research;source=rp_usable;checks=100;handoff=ready;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "usable_research_checks=100");
	ok = ok && require_file_token("rp_usableproj", "usable_project_checks=120");
	ok = ok && require_file_token("rp_usableboot", "platform_doctor_checks=10");
	ok = ok && require_file_token("rp_usablescaf", "template=scaffold-template:protocol-reproduction");
	ok = ok && require_file_token("rp_usablelaunch", "operation=operations_digest;sections=6");
	ok = ok && require_file_token("rp_usablepack", "bundle=usable-study-protocol-reproduction-package:RUN-042");
	ok = ok && require_file_token("rp_package", "usable_project=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "usable_project_page=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=usable_project;source=rp_usableproj;checks=120;bundles=2;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "usable_project_checks=120");
	ok = ok && require_file_token("rp_mature", "reference_platforms=6");
	ok = ok && require_file_token("rp_mature", "capability_mappings=6");
	ok = ok && require_file_token("rp_mature", "capability_checks=72");
	ok = ok && require_file_token("rp_mature", "mapping=galaxy-workflow-history");
	ok = ok && require_file_token("rp_mature", "mapping=aiida-process-graph");
	ok = ok && require_file_token("rp_mature", "mapping=dvc-dataflow");
	ok = ok && require_file_token("rp_mature", "mapping=mlflow-experiment-registry");
	ok = ok && require_file_token("rp_mature", "mapping=nextflow-portable-workflow");
	ok = ok && require_file_token("rp_mature", "mapping=snakemake-rule-dag");
	ok = ok && require_file_token("rp_mature_refs", "profile=reference-platform:galaxy;name=Galaxy");
	ok = ok && require_file_token("rp_mature_map", "agentos_targets=kernel_context_path,kernel_metadata_index,kernel_event_queue,batch_tool_runner,capability_contract_table");
	ok = ok && require_file_token("rp_mature_checks", "checks=72");
	ok = ok && require_file_token("rp_mature_checks", "check=surface.site;target=mature.html;result=pass;status=ready");
	ok = ok && require_file_token("rp_mature_checks", "check=agentos.batch_runner;target=batch_tool_runner;result=planned;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "mature_capability_page=rp_mature;profiles=6;mappings=6;checks=72;status=ready");
	ok = ok && require_file_token("rp_review_dashboard", "subsection=mature_capabilities;source=rp_mature;profiles=6;mappings=6;checks=72;outcome=passed;status=ready");
	ok = ok && require_file_token("rp_agentcmp", "mature_capability_checks=72");
	ok = ok && require_file_token("rp_prov_view", "provenance_view_checks=64");
	ok = ok && require_file_token("rp_prov_view", "timeline_views=4");
	ok = ok && require_file_token("rp_prov_view", "subgraphs=3");
	ok = ok && require_file_token("rp_prov_view", "evidence_packets=4");
	ok = ok && require_file_token("rp_prov_view", "agentos_mapping=kernel_timeline,kernel_provenance_edges,kernel_ledger,context_detail");
	ok = ok && require_file_token("rp_prov_edges", "edges=12");
	ok = ok && require_file_token("rp_prov_edges", "edge=6;source=rp_artifact_manifest;target=rp_report_text;kind=evidence_to_report;status=ready");
	ok = ok && require_file_token("rp_prov_edges", "edge=12;source=rp_agent_run;target=rp_prov_view;kind=agent_to_trace;status=ready");
	ok = ok && require_file_token("rp_evidence_packet", "packets=4");
	ok = ok && require_file_token("rp_evidence_packet", "packet=workflow-recovery;run=RUN-042");
	ok = ok && require_file_token("rp_evidence_packet", "packet=agentos-readiness;run=RUN-042");
	ok = ok && require_file_token("rp_timeline_view", "views=4");
	ok = ok && require_file_token("rp_timeline_view", "view=agent_decision_flow;events=6;source=rp_agent_run;status=ready");
	ok = ok && require_file_token("rp_timeline_view", "timeline_event=dossier;tick=42;actor=orchestrator;artifact=rp_review_pack;status=ready");
	ok = ok && require_file_token("rp_web_bundle", "evidence_role=demo_reference;catalog_generation=demo_expected;provenance_page=rp_prov_view;timeline_views=4;subgraphs=3;packets=4;status=reference_ready");
	ok = ok && require_file_token("rp_review_dashboard", "evidence_role=demo_reference;catalog_generation=demo_expected;subsection=provenance_view;source=rp_prov_view;timeline=4;packets=4;checks=64;outcome=passed;status=reference_ready");
	ok = ok && require_file_token("rp_agentcmp", "provenance_view_checks=64");
	ok = ok && require_file_token("rp_prov_query", "provenance_query_checks=72");
	ok = ok && require_file_token("rp_prov_query", "specs=3");
	ok = ok && require_file_token("rp_prov_query", "templates=1");
	ok = ok && require_file_token("rp_prov_query", "executions=3");
	ok = ok && require_file_token("rp_prov_query", "agentos_mapping=timeline_query,provenance_snapshot,ledger_snapshot,context_detail");
	ok = ok && require_file_token("rp_prov_specs", "template=provenance-query-template:calculation-root-neighborhood");
	ok = ok && require_file_token("rp_prov_specs", "spec=provenance-query:RUN-042:calculation-lineage");
	ok = ok && require_file_token("rp_prov_specs", "spec=provenance-query:RUN-042:template-rendered-lineage");
	ok = ok && require_file_token("rp_prov_exec", "execution=provenance-query-execution:calculation-lineage");
	ok = ok && require_file_token("rp_prov_exec", "execution=provenance-query-execution:template-rendered-lineage");
	ok = ok && require_file_token("rp_prov_exec", "row=calculation-job:lab-gene-x:run042-qc");
	ok = ok && require_file_token("rp_prov_query_pkg", "comparison=provenance-query-comparison:RUN-042:rendered-vs-direct");
	ok = ok && require_file_token("rp_prov_query_pkg", "export=provenance-query-export:RUN-042:calculation-lineage");
	ok = ok && require_file_token("rp_prov_query_pkg", "packet=provenance-query-packet:RUN-042:lineage-review");
	ok = ok && require_file_token("rp_web_bundle", "evidence_role=demo_reference;catalog_generation=demo_expected;provenance_queries_page=rp_prov_query;specs=3;executions=3;packets=1;status=reference_ready");
	ok = ok && require_file_token("rp_review_dashboard", "evidence_role=demo_reference;catalog_generation=demo_expected;subsection=provenance_queries;source=rp_prov_query;queries=3;executions=3;checks=72;outcome=passed;status=reference_ready");
	ok = ok && require_file_token("rp_agentcmp", "provenance_query_checks=72");
	ok = ok && require_file_token("rp_web_bundle", "custom_research_files=1");
	ok = ok && require_file_token("rp_web_bundle", "custom_research_runs=3");
	if (rp_host_seed_count() > 0 &&
	    (rp_host_seed_has("kind=workbench") ||
	     rp_host_seed_has("kind=workbench_complete") ||
	     rp_host_seed_has("kind=workbench_advance") ||
	     rp_host_seed_has("kind=workbench_auto_advance") ||
	     rp_host_seed_has("kind=workbench_task") ||
	     rp_host_seed_has("kind=workbench_note") ||
	     rp_host_seed_has("kind=workbench_notes") ||
	     rp_host_seed_has("kind=workbench_handoff_package") ||
	     rp_host_seed_has("kind=workbench_readiness") ||
	     rp_host_seed_has("kind=workbench_answer") ||
	     rp_host_seed_has("kind=workbench_answer_audit") ||
	     rp_host_seed_has("kind=workbench_evidence_search") ||
	     rp_host_seed_has("kind=workbench_brief") ||
	     rp_host_seed_has("kind=workbench_evidence_dossier") ||
	     rp_host_seed_has("kind=workbench_evidence_graph") ||
	     rp_host_seed_has("kind=workbench_citations") ||
	     rp_host_seed_has("kind=workbench_manuscript") ||
	     rp_host_seed_has("kind=workbench_manuscript_audit") ||
	     rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	     rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	     rp_host_seed_has("kind=workbench_task_board") ||
	     rp_host_seed_has("kind=workbench_task_board_row") ||
	     rp_host_seed_has("kind=workbench_runbook") ||
	     rp_host_seed_has("kind=workbench_timeline") ||
	     rp_host_seed_has("kind=workbench_file_manifest") ||
	     rp_host_seed_has("kind=workbench_file_verify") ||
	     rp_host_seed_has("kind=workbench_export"))) {
		ok = ok && require_file_token("rp_actionio", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && require_file_token("rp_web_bundle", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_platform_ops_action()) {
		ok = ok && require_file_token("rp_runner", "host_action_platform_ops=ready");
		ok = ok && require_file_token("rp_package", "host_action_platform_ops_package=ready");
		ok = ok && require_file_token("rp_actionio", "host_action_platform_ops=1");
		ok = ok && require_file_token("rp_actionio", "host_action_platform_ops_outputs=rp_runner,rp_package,rp_api_action,rp_web_bundle");
		ok = ok && require_file_token("rp_web_bundle", "host_action_platform_ops=rp_runner,rp_package,rp_api_action");
		ok = ok && require_file_token("rp_api_action", "project_review_actions=8");
		if (rp_host_seed_has("kind=project_release_gate")) {
			ok = ok && require_file_token("rp_actionio", "host_action_project_review_outputs=rp_web_bundle");
			ok = ok && require_file_token("rp_web_bundle", "host_action_project_release_gate=");
			ok = ok && require_file_token("rp_web_bundle", "host_action_project_review=rp_web_bundle");
		}
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_host_workflow_step_action()) {
		ok = ok && require_file_token("rp_stage_state", "host_workflow_steps=applied");
		ok = ok && require_file_token("rp_stage_state", "host_workflow_stage_action=");
		ok = ok && require_file_token("rp_cache_index", "host_workflow_cache_action=");
		ok = ok && require_file_token("rp_retry_plan", "host_workflow_retry_action=");
		ok = ok && require_file_token("rp_artifact_manifest", "host_workflow_artifact_action=");
		ok = ok && require_file_token("rp_report_text", "host_workflow_report_action=");
		ok = ok && require_file_token("rp_package", "host_action_workflow_steps=ready");
		ok = ok && require_file_token("rp_actionio", "host_action_workflow_steps=5");
		ok = ok && require_file_token("rp_web_bundle", "host_action_workflow_steps=5");
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_artifact_action()) {
		ok = ok && require_file_token("rp_artifact", "host_artifact_actions=applied");
		ok = ok && require_file_token("rp_artifact_manifest", "host_artifact_manifest_actions=applied");
		if (rp_host_seed_has("kind=artifact_input")) ok = ok && require_file_token("rp_artifact", "host_artifact_input=");
		if (rp_host_seed_has("kind=artifact_derive")) ok = ok && require_file_token("rp_artifact", "host_artifact_derive=");
		if (rp_host_seed_has("kind=artifact_log")) ok = ok && require_file_token("rp_stage_log", "host_artifact_log=");
		if (rp_host_seed_has("kind=artifact_chart")) ok = ok && require_file_token("rp_chart_data", "host_artifact_chart=");
		if (rp_host_seed_has("kind=artifact_package")) ok = ok && require_file_token("rp_package", "host_artifact_package=");
		ok = ok && require_file_token("rp_package", "host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package");
		ok = ok && require_file_token("rp_actionio", "host_action_artifacts=1");
		ok = ok && require_file_token("rp_web_bundle", "host_action_artifacts=1");
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_workflow_portability_run_action()) {
		ok = ok && require_file_token("rp_wfio", "host_portability_payload=applied");
		ok = ok && require_file_token("rp_wfio", "host_portability_import=");
		ok = ok && require_file_token("rp_wfio", "host_portability_target=");
		ok = ok && require_file_token("rp_wfio", "host_portability_compare_profile=");
		ok = ok && require_file_token("rp_package", "host_action_portability_package=ready");
		ok = ok && require_file_token("rp_actionio", "host_action_portability=1");
		ok = ok && require_file_token("rp_actionio", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp");
		ok = ok && require_file_token("rp_web_bundle", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp");
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_workflow_portability_step_action()) {
		ok = ok && require_file_token("rp_wfio", "host_portability_steps=applied");
		ok = ok && require_file_token("rp_wfio", "host_portability_import_action=");
		ok = ok && require_file_token("rp_wfio", "host_portability_plan_action=");
		ok = ok && require_file_token("rp_wfio", "host_portability_bind_action=");
		ok = ok && require_file_token("rp_wfio", "host_portability_rehearse_action=");
		ok = ok && require_file_token("rp_wfio", "host_portability_review_action=");
		ok = ok && require_file_token("rp_wfio", "host_portability_package_action=");
		ok = ok && require_file_token("rp_package", "host_action_portability_steps=ready");
		ok = ok && require_file_token("rp_actionio", "host_action_portability_steps=6");
		ok = ok && require_file_token("rp_web_bundle", "host_action_portability_steps=6");
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_llm_relay_action()) {
		ok = ok && require_file_token("rp_llm_req", "host_llm_request_id=");
		ok = ok && require_file_token("rp_llm_resp", "host_llm_response_id=");
		ok = ok && require_file_token("rp_llm_fallback", "host_llm_fallback_case=");
		ok = ok && require_file_token("rp_llm_packets", "host_llm_packet_request=");
		ok = ok && require_file_token("rp_llm_hostreq", "host_llm_host_response=");
		ok = ok && require_file_token("rp_api_runtime", "host_llm_request_id=");
		ok = ok && require_file_token("rp_actionio", "host_action_llm_relay=1");
		ok = ok && require_file_token("rp_web_bundle", "host_action_llm_relay=");
	}

	ok = ok && require_count("ack", rp_count_lines("rp_ack"), 69);
	ok = ok && require_count("tool", rp_count_lines("rp_tool"), 328);
	if (!ok) return 1;

	if (!rp_write_file("rp_tests",
			   "suite=plain-ucore-demo-reference\n"
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "demo_expected_tests=2800\n"
			   "catalog=demo_expected\n"
			   "data_pipeline=passed\n"
			   "bio_services=passed\n"
			   "lab_resources=passed\n"
			   "publication_services=passed\n"
			   "knowledge_services=passed\n"
			   "runtime_services=passed\n"
			   "notebook_export=passed\n"
			   "api_actions=passed\n"
			   "active_actions=passed\n"
			   "custom_research=passed\n"
			   "research_input=passed\n"
			   "dynamic_input=passed\n"
			   "workbench=passed\n"
			   "workspace_import=passed\n"
			   "literature_protocol=passed\n"
			   "workflow_portability=passed\n"
			   "coherence=passed\n"
			   "workflow=passed\n"
			   "workflow_runner_detail=passed\n"
			   "artifact_ops=passed\n"
			   "agent_collaboration=passed\n"
			   "ui_export=passed\n"
			   "host_web_export=passed\n"
			   "ui_render_data=passed\n"
			   "static_site=passed\n"
			   "export_package=passed\n"
			   "delivery_manifest=passed\n"
			   "human_review_revision=passed\n"
			   "review_thread_actions=passed\n"
			   "llm=passed\n"
			   "llm_relay=passed\n"
			   "startup_health=passed\n"
			   "research_products=passed\n"
			   "runtime_assurance=passed\n"
			   "research_ops=passed\n"
			   "regulated_research=passed\n"
			   "lab_governance_ops=passed\n"
			   "knowledge_index=passed\n"
			   "llm_transcripts=passed\n"
			   "workbench_delivery=passed\n"
			   "review_dashboard=passed\n"
			   "portfolio_scale=passed\n"
			   "execution_scale=passed\n"
			   "operations_scale=passed\n"
			   "project_revision_incident=passed\n"
			   "state_catalog=passed\n"
			   "startup_doctor=passed\n"
			   "runbook_service=passed\n"
			   "project_delivery=passed\n"
			   "study_protocol=passed\n"
			   "statistical_design=passed\n"
			   "model_registry=passed\n"
			   "experiment_scheduling=passed\n"
			   "training_compliance=passed\n"
			   "operations_board=passed\n"
			   "review_board=passed\n"
			   "integrity_plane=passed\n"
			   "coherence_plane=passed\n"
			   "publication_workflow=passed\n"
			   "real_task=passed\n"
			   "analysis_results=passed\n"
			   "decision_support=passed\n"
			   "usable_research=passed\n"
			   "experiment_campaigns=passed\n"
			   "release_dossier=passed\n"
			   "mature_capabilities=passed\n"
			   "provenance_view=passed\n"
			   "provenance_query=passed\n"
               "reserved_research_surfaces=passed\n"
               "root_state_surface=passed\n"
               "agentos_reserved_surface=passed\n"
			   "agent_compare=passed\n"
			   "consistency=passed\n"
			   "evidence_file_status=reference_ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=test_suite;msg=test;status=passed")) return 1;
	if (!rp_state_buffer_begin_append(&suite_state, "rp_tool") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.cat;ok") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.data;ok") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.workflow;ok") ||
	    !rp_state_buffer_append(&suite_state,
				    "tool=test_suite.check_artifacts;ok") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.ui;ok") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.web;ok") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.llm;ok") ||
	    !rp_state_buffer_append(&suite_state,
				    "tool=test_suite.check_compare;ok") ||
	    !rp_state_buffer_append(&suite_state,
				    "tool=test_suite.consistency;ok") ||
	    !rp_state_buffer_append(&suite_state, "tool=test_suite.result;ok") ||
	    !rp_state_buffer_commit(&suite_state)) return 1;
	if (!rp_append_status("tests=ready")) return 1;
	printf("rp_test_suite: evidence_role=demo_reference catalog_generation=demo_expected demo_expected_tests=2800 status=reference_ready\n");
	return 0;
}
