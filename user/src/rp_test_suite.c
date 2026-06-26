#include <stdio.h>
#include <research_platform_state.h>

static int require_file_token(const char *path, const char *token)
{
	if (rp_file_contains(path, token)) return 1;
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
		ok = ok && require_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_runner", "host_action_workbench_title=");
		ok = ok && require_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_api_compare", "host_action_workbench_title=");
		ok = ok && require_seed_value("kind=workbench", "literature_query=", "agent workflow provenance", "rp_runner", "host_action_workbench_literature_query=");
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
		ok = ok && require_file_token("rp_runner", token);
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
	ok = ok && require_file_token("rp_backend", "cases=4");
	ok = ok && require_file_token("rp_consistency", "checks=113");
	ok = ok && require_file_token("rp_consistency", "coherence_checks=9");
	ok = ok && require_file_token("rp_consistency", "workbench_records=10");
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
	ok = ok && require_file_token("rp_web_routes", "routes=46");
	ok = ok && require_file_token("rp_web_routes", "get_routes=14");
	ok = ok && require_file_token("rp_web_routes", "route=/research/workbench/{id}");
	ok = ok && require_file_token("rp_web_routes", "post_routes=32");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/review");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/revision-task");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/run-revision-task");
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
	ok = ok && require_file_token("rp_api_action", "actions=32");
	ok = ok && require_file_token("rp_api_action", "workflow_portability_run=/actions/workflow-portability/run");
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
	ok = ok && require_file_token("rp_api_action", "project_space_actions=5");
	ok = ok && require_file_token("rp_api_action", "research_search_actions=4");
	ok = ok && require_file_token("rp_api_action", "plan_queue_actions=2");
	ok = ok && require_file_token("rp_api_action", "action_item_actions=1");
	ok = ok && require_file_token("rp_api_action", "action_state_records=12");
	ok = ok && require_file_token("rp_api_action", "validated_requests=8");
	ok = ok && require_file_token("rp_api_action", "precondition_checks=8");
	ok = ok && require_file_token("rp_api_action", "side_effect_records=16");
	ok = ok && require_file_token("rp_api_action", "action_audit_log=rp_actionio");
	ok = ok && require_file_token("rp_api_action", "download_manifest=rp_package");
	ok = ok && require_file_token("rp_api_action", "export_bundle=rp_package");
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
	ok = ok && require_file_token("rp_web_bundle", "api_payloads=14");
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
	ok = ok && require_file_token("rp_web_bundle", "reader_views=14");
	ok = ok && require_file_token("rp_web_bundle", "reader_actions=32");
	ok = ok && require_file_token("rp_web_bundle", "reader_payload_files=rp_api_home");
	ok = ok && require_file_token("rp_web_bundle", "reader_refresh_files=rp_web_routes");
	ok = ok && require_file_token("rp_web_bundle", "reader_required_sections=routes,payloads,actions,live_update,downloads,compare");
	ok = ok && require_file_token("rp_web_bundle", "reader_event_stream=rp_web_bundle");
	ok = ok && require_file_token("rp_web_bundle", "reader_fallback=rp_site");
	ok = ok && require_file_token("rp_web_bundle", "reader_state_source=plain_ucore_files");
	ok = ok && require_file_token("rp_web_bundle", "workbench=rp_runner");
	ok = ok && require_file_token("rp_web_bundle", "workbench_tasks=9");
	ok = ok && require_file_token("rp_web_bundle", "workbench_export=rp_runner");
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
	ok = ok && require_file_token("rp_web_bundle", "post_routes=32");
	ok = ok && require_file_token("rp_web_bundle", "human_reviews=1");
	ok = ok && require_file_token("rp_web_bundle", "revision_tasks=1");
	ok = ok && require_file_token("rp_web_bundle", "revision_delta=rp_revision");
	ok = ok && require_file_token("rp_web_bundle", "review_threads=2");
	ok = ok && require_file_token("rp_web_bundle", "review_action_items=2");
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
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_workflow_portability_action()) {
		ok = ok && require_file_token("rp_wfio", "host_portability_payload=applied");
		ok = ok && require_file_token("rp_wfio", "host_portability_import=");
		ok = ok && require_file_token("rp_wfio", "host_portability_target=");
		ok = ok && require_file_token("rp_wfio", "host_portability_compare_profile=");
		ok = ok && require_file_token("rp_package", "host_action_portability_package=ready");
		ok = ok && require_file_token("rp_actionio", "host_action_portability=1");
		ok = ok && require_file_token("rp_actionio", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp");
		ok = ok && require_file_token("rp_web_bundle", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp");
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

	ok = ok && require_count("ack", rp_count_lines("rp_ack"), 38);
	ok = ok && require_count("tool", rp_count_lines("rp_tool"), 133);
	if (!ok) return 1;

	if (!rp_write_file("rp_tests",
			   "suite=plain-ucore-research-platform\n"
			   "tests=693\n"
			   "catalog=passed\n"
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
			   "llm_relay=passed\n"
			   "agent_compare=passed\n"
			   "consistency=passed\n"
			   "status=passed\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=test_suite;msg=test;status=passed")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.cat;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.data;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.workflow;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_artifacts;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.ui;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.web;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.llm;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_compare;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.consistency;ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.result;ok")) return 1;
	if (!rp_append_status("tests=ready")) return 1;
	printf("rp_test_suite: tests=693 catalog=passed data=passed services=passed actions=passed active_actions=passed custom=passed dynamic=passed workbench=passed notebook=passed portability=passed coherence=passed static_site=passed artifacts=passed workflow=passed collaboration=passed ui=passed web=passed llm=passed compare=passed status=passed\n");
	return 0;
}
