#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>
#include <rp_host_action_seed.h>

static int text_contains_silent(const char *text, const char *needle)
{
	int needle_len = (int)strlen(needle);
	int text_len = (int)strlen(text);
	if (needle_len > text_len) return 0;
	for (int i = 0; i <= text_len - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (text[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) return 1;
	}
	return 0;
}

static int file_contains_silent(const char *path, const char *needle)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return 0;
	return text_contains_silent(buf, needle);
}

static int count_lines_silent(const char *text)
{
	int n = (int)strlen(text);
	if (n <= 0) return 0;
	int count = 0;
	for (int i = 0; i < n; i++) {
		if (text[i] == '\n') count++;
	}
	if (text[n - 1] != '\n') count++;
	return count;
}

static void make_host_action_count_line(char *out, int cap, int count)
{
	const char *prefix = "host_reader_actions=";
	int pos = 0;
	for (int i = 0; prefix[i] && pos + 1 < cap; i++) {
		out[pos++] = prefix[i];
	}
	if (count <= 0) {
		if (pos + 1 < cap) out[pos++] = '0';
	} else {
		char digits[16];
		int ndigits = 0;
		while (count > 0 && ndigits < (int)sizeof(digits)) {
			digits[ndigits++] = (char)('0' + (count % 10));
			count /= 10;
		}
		for (int i = ndigits - 1; i >= 0 && pos + 1 < cap; i--) {
			out[pos++] = digits[i];
		}
	}
	out[pos] = 0;
}

static int host_seed_has_workbench_action(void)
{
	return rp_host_seed_has("kind=workbench") ||
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
	       rp_host_seed_has("kind=workbench_export");
}

static int copy_workbench_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=workbench", "kind=workbench_complete", "kind=workbench_advance",
		"kind=workbench_auto_advance", "kind=workbench_task", "kind=workbench_note", "kind=workbench_notes",
		"kind=workbench_handoff_package", "kind=workbench_readiness",
		"kind=workbench_answer", "kind=workbench_answer_audit", "kind=workbench_evidence_search",
		"kind=workbench_brief", "kind=workbench_evidence_dossier", "kind=workbench_evidence_graph",
		"kind=workbench_citations", "kind=workbench_manuscript", "kind=workbench_manuscript_audit",
		"kind=workbench_manuscript_revision_plan", "kind=workbench_manuscript_revision_task",
		"kind=workbench_task_board", "kind=workbench_task_board_row",
		"kind=workbench_runbook", "kind=workbench_timeline", "kind=workbench_file_manifest",
		"kind=workbench_file_verify", "kind=workbench_export"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_ui_home", "page=home");
	ok = ok && rp_file_contains("rp_ui_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_ui_run", "runner_exec=");
	ok = ok && rp_file_contains("rp_ui_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_ui_run", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_ui_run", "retry_reason=tool_output_missing");
	ok = ok && rp_file_contains("rp_ui_agent", "decisions=8");
	ok = ok && rp_file_contains("rp_ui_agent", "decision_rows=8");
	ok = ok && rp_file_contains("rp_ui_evidence", "stage_log=rp_stage_log");
	ok = ok && rp_file_contains("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_ui_compare", "page=compare-metrics");
	ok = ok && rp_file_contains("rp_ui_compare", "metric_rows=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_artifact_manifest", "real_artifact_items=5");
	ok = ok && rp_file_contains("rp_artifact", "archive_file=rp_gene_counts_csv");
	ok = ok && rp_file_contains("rp_llm_hostreq", "template_mode=ready");
	ok = ok && rp_file_contains("rp_llm_hostreq", "roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_resp", "matched_requests=3");
	ok = ok && rp_file_contains("rp_agentcmp", "status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_analysisres", "analysis_results_checks=96");
	ok = ok && rp_file_contains("rp_package", "package_manifest=ready");
	ok = ok && rp_file_contains("rp_package", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_site", "pages=42");
	ok = ok && rp_file_contains("rp_site", "json_payloads=14");
	ok = ok && rp_file_contains("rp_input", "form_fields=8");
	ok = ok && rp_file_contains("rp_input", "csv_rows_total=9");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && rp_file_contains("rp_input", "dynamic_submissions=4");
	ok = ok && rp_file_contains("rp_input", "dynamic_queue=plain_ucore_file_backed");
	ok = ok && rp_file_contains("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	ok = ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_runner", "dynamic_input_runs=4");
	ok = ok && rp_file_contains("rp_runner", "citation_plan_entries=3");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	ok = ok && rp_file_contains("rp_package", "deliverables=8");
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_package", "delivery_checks=3");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && rp_file_contains("rp_package", "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md");
	ok = ok && rp_file_contains("rp_package", "raw_links=5");
	ok = ok && rp_file_contains("rp_package", "artifact_links=6");
	ok = ok && rp_file_contains("rp_package", "human_reviews=1");
	ok = ok && rp_file_contains("rp_package", "revision_tasks=1");
	ok = ok && rp_file_contains("rp_package", "review_threads=2");
	ok = ok && rp_file_contains("rp_review2", "action_items=2");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_runner", "custom_runs=3");
	ok = ok && rp_file_contains("rp_runner", "custom_status=ok");
	ok = ok && rp_file_contains("rp_runner", "revision_status=completed");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_sreg", "samples=8");
	ok = ok && rp_file_contains("rp_instr", "instruments=4");
	ok = ok && rp_file_contains("rp_resrev", "review_items=10");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	ok = ok && rp_file_contains("rp_nbexec", "notebook=reproducible-analysis.ipynb");
	ok = ok && rp_file_contains("rp_repro", "downloadable_units=4");
	if (!ok) return 1;

	if (!rp_write_file("rp_web_routes",
			   "service=host-web-ui\n"
			   "routes=74\n"
			   "get_routes=17\n"
			   "post_routes=57\n"
			   "route=/;payload=rp_api_home;status=ready\n"
			   "route=/run/RUN-042;payload=rp_api_run;status=ready\n"
			   "route=/research-studio;payload=rp_studio;status=ready\n"
			   "route=/research/{run_id};payload=rp_uresrun;status=ready\n"
			   "route=/research/workbench/{id};payload=rp_runner;status=ready\n"
			   "route=/research/project/{id}/review;payload=rp_web_bundle;status=ready\n"
			   "route=/agents;payload=rp_api_agents;status=ready\n"
			   "route=/evidence;payload=rp_api_evidence;status=ready\n"
			   "route=/compare;payload=rp_api_compare;status=ready\n"
			   "route=/artifacts;payload=rp_api_artifacts;status=ready\n"
			   "route=/data;payload=rp_api_data;status=ready\n"
			   "route=/bio;payload=rp_api_bio;status=ready\n"
			   "route=/lab-resources;payload=rp_api_labres;status=ready\n"
			   "route=/publication;payload=rp_api_pub;status=ready\n"
			   "route=/knowledge;payload=rp_api_know;status=ready\n"
			   "route=/runtime;payload=rp_api_runtime;status=ready\n"
			   "action=/actions/host-workflow/run;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/host-workflow/export;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/host-workflow/stage-attempt;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/host-workflow/cache-decision;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/host-workflow/retry-decision;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/host-workflow/artifact-manifest;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/host-workflow/report-export;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/artifact-input;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/artifact-derive;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/artifact-log;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/artifact-chart;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/artifact-package;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/run;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/import;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/plan;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/bind;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/rehearse;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/review;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/workflow-portability/package;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/agentcompare/run;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/run;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/studio-launch;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/export;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/review;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/revision-task;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/run-revision-task;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/operations-report;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/operations-advance-next;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/operations-execute-next-plan;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-delivery-dashboard;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-delivery-execute-next;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-quality-gate;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-quality-repair-plan;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-quality-repair-execute;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-plan-queue-row;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-plan-queue-execute;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/workbench-action-item;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-space;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-space-note;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-space-action-item;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-space-answer;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-space-repair-execute;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-handoff-audit;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-release-gate;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-snapshot;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-snapshot-comparison;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-reproducibility-audit;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-provenance-graph;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/project-delivery;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/package-intake;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research-search/save;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research-search/export;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research-search/note;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research-search/action-item;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/llm-relay-request;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/llm-relay-response;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/llm-relay-fallback;method=POST;payload=rp_api_action;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_home",
			   "api=home\n"
			   "title=Research Agent Platform\n"
			   "run_id=RUN-042\n"
			   "custom_run=usable-run:RUN-900\n"
			   "custom_runs=3\n"
			   "research_form=rp_input\n"
			   "upload_files=rp_input\n"
			   "dynamic_inputs=4\n"
			   "dynamic_queue=rp_input\n"
			   "live_update_feed=rp_web_bundle\n"
			   "reader_contract=rp_web_bundle\n"
			   "workbench=rp_runner\n"
			   "library_sources=rp_knowledge\n"
			   "nav_items=12\n"
			   "primary_cards=12\n"
			   "static_site_pages=42\n"
			   "static_site=rp_site\n"
			   "cards=run,custom_research,agents,evidence,data,llm_relay,compare\n"
			   "source=rp_ui_home\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_run",
			   "api=run-detail\n"
			   "run_id=RUN-042\n"
			   "custom_research=rp_runner;custom_research_runs=3\n"
			   "request_form=rp_input;upload_files=rp_input;workspace_imports=1\n"
			   "dynamic_input_queue=rp_input;dynamic_input_runs=4;live_update_feed=rp_web_bundle\n"
			   "reader_contract=rp_web_bundle;reader_view=run-detail;reader_refresh=rp_web_bundle\n"
			   "workbench=rp_runner;workbench_tasks=9;workbench_export=rp_runner\n"
			   "library_sources=rp_knowledge;bibliography=rp_runner;citation_plan=rp_runner;evidence_protocols=1;evidence_extractions=3\n"
			   "workflow_portability=rp_wfio;adapter_specs=6;migration_steps=9;rehearsal_cases=4\n"
			   "delivery_manifest=rp_package;review_page=rp_package;export_bundle=rp_package\n"
			   "notebook_export=rp_nbexec;notebook_download=rp_repro\n"
			   "delivery_files=8;delivery_checks=3;latest_delivery_status=ready\n"
			   "evidence_bundle_zip=research-evidence-bundle.zip\n"
			   "llm_roundtrip=ready;llm_response_file=rp_llm_resp\n"
			   "human_reviews=1;revision_tasks=1;latest_revision_task=usable-revision-task:RUN-900:1\n"
			   "review_threads=2;review_comments=3;review_action_items=2;review_thread_source=rp_review2\n"
			   "revised_run=usable-run:RUN-900-rev1\n"
			   "revision_changes=2;revision_delta=rp_revision\n"
			   "workflow=lab-gene-x\n"
			   "stages=5\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "timeline_rows=5\n"
			   "artifact_preview=rp_report_text,rp_chart_data,rp_artifact,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json\n"
			   "dependency_checks=5\n"
			   "stage_outputs=5\n"
			   "real_artifact_items=5\n"
			   "retry_reason=tool_output_missing\n"
			   "runner_exec_files=5\n"
			   "stage_state=rp_stage_state\n"
			   "cache_index=rp_cache_index\n"
			   "retry_plan=rp_retry_plan\n"
			   "run_events=rp_run_events\n"
			   "artifact_manifest=rp_artifact_manifest\n"
			   "manifest_support_entries=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=research_run")) {
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Browser started study");
		}
		if (!rp_append_host_action_line("rp_api_home", "host_action_title=", value)) return 1;
		if (!rp_append_host_action_line("rp_api_run", "host_action_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "question=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Can this platform run a custom research task?");
		}
		if (!rp_append_host_action_line("rp_api_home", "host_action_question=", value)) return 1;
		if (!rp_append_host_action_line("rp_api_run", "host_action_question=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "provider=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_api_home", "host_action_provider=", value)) return 1;
		if (!rp_append_host_action_line("rp_api_run", "host_action_provider=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_api_run", "host_action_dataset_rows=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_entries=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "2");
		}
		if (!rp_append_host_action_line("rp_api_run", "host_action_reference_entries=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "workspace_files=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_api_run", "host_action_workspace_files=", value)) return 1;
	}
	if (rp_host_seed_has("kind=dataset") ||
	    rp_host_seed_has("kind=library_source") ||
	    rp_host_seed_has("kind=template") ||
	    rp_host_seed_has("kind=workspace_inspect") ||
	    rp_host_seed_has("kind=workspace_import") ||
	    rp_host_seed_has("kind=workspace_import_run")) {
		char value[96];
		if (!rp_append_file("rp_api_home", "host_action_research_inputs=ready")) return 1;
		if (!rp_append_file("rp_api_run", "host_action_research_inputs=ready")) return 1;
		if (rp_host_seed_has("kind=dataset")) {
			if (!rp_host_seed_copy_value_for_kind("kind=dataset", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Reusable response table");
			}
			if (!rp_append_host_action_line("rp_api_run", "host_action_dataset_title=", value)) return 1;
		}
		if (rp_host_seed_has("kind=library_source")) {
			if (!rp_host_seed_copy_value_for_kind("kind=library_source", "citation_key=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "agentlibrary2026");
			}
			if (!rp_append_host_action_line("rp_api_run", "host_action_library_citation=", value)) return 1;
		}
		if (rp_host_seed_has("kind=template")) {
			if (!rp_host_seed_copy_value_for_kind("kind=template", "name=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Reusable response comparison");
			}
			if (!rp_append_host_action_line("rp_api_run", "host_action_template_name=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", "root=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import", "root=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "root=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_api_run", "host_action_workspace_root=", value)) return 1;
		}
	}
	if (!rp_write_file("rp_api_agents",
			   "api=agent-detail\n"
			   "agents=7\n"
			   "messages=21\n"
			   "decisions=8\n"
			   "decision_rows=8\n"
			   "handoffs=6\n"
			   "records=rp_agents,rp_decisions,rp_handoff,rp_deliberation,rp_agent_run\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_evidence",
			   "api=evidence-detail\n"
			   "claims=8\n"
			   "links=5\n"
			   "provenance_paths=3\n"
			   "literature_search=usable-literature-search:RUN-900:1\n"
			   "screening_decisions=9\n"
			   "evidence_protocol=usable-evidence-protocol:RUN-900:1\n"
			   "prisma_flow=usable-prisma-flow:RUN-900:1\n"
			   "evidence_synthesis=usable-evidence-synthesis:RUN-900:1\n"
			   "stage_log=rp_stage_log\n"
			   "artifact=rp_artifact\n"
			   "manifest=rp_artifact_manifest\n"
			   "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json\n"
			   "llm_guard=rp_llm_guard\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=literature_search") ||
	    rp_host_seed_has("kind=evidence_review") ||
	    rp_host_seed_has("kind=evidence_protocol")) {
		char value[96];
		if (!rp_append_file("rp_api_evidence", "host_action_evidence_inputs=ready")) return 1;
		if (rp_host_seed_has("kind=literature_search")) {
			if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "query=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "agent workflow provenance");
			}
			if (!rp_append_host_action_line("rp_api_evidence", "host_action_literature_query=", value)) return 1;
		}
		if (rp_host_seed_has("kind=evidence_review")) {
			if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "included=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "3");
			}
			if (!rp_append_host_action_line("rp_api_evidence", "host_action_evidence_included=", value)) return 1;
		}
		if (rp_host_seed_has("kind=evidence_protocol")) {
			if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Agent workflow evidence protocol");
			}
			if (!rp_append_host_action_line("rp_api_evidence", "host_action_protocol_title=", value)) return 1;
		}
	}
	if (!rp_write_file("rp_api_compare",
			   "api=compare-metrics\n"
			   "plain_kernel=passed\n"
			   "agentos_kernel=pending\n"
			   "file_scans=128\n"
			   "state_convention=1\n"
			   "user_permission_only=1\n"
			   "context_trusted=0\n"
			   "rebuild_steps=6\n"
			   "data_pipeline_files=6\n"
			   "workflow_runner_files=5\n"
			   "workflow_portability_records=1\n"
			   "coherence_checks=9\n"
			   "namespace_checks=12\n"
			   "surface_checks=13\n"
			   "status_semantics=11\n"
			   "reference_checks=18\n"
			   "evidence_trace_checks=14\n"
			   "run_state_checks=9\n"
			   "lifecycle_checks=10\n"
			   "delivery_coherence=3\n"
			   "agentos_readiness_checks=7\n"
			   "relay_protocol_files=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_count() > 0) {
		if (!rp_append_file("rp_api_compare", "host_action_payload_applied=1")) return 1;
		if (rp_host_seed_has("kind=research_run")) {
			char seed_run[48];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
				rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
			}
			if (!rp_append_host_action_line("rp_api_compare", "host_action_run_id=", seed_run)) return 1;
		}
		if (rp_host_seed_has("kind=human_review")) {
			char reviewer[48];
			if (!rp_host_seed_copy_value_for_kind("kind=human_review", "reviewer=", reviewer, sizeof(reviewer))) {
				rp_copy_text(reviewer, sizeof(reviewer), "HOST");
			}
			if (!rp_append_host_action_line("rp_api_compare", "host_action_reviewer=", reviewer)) return 1;
		}
		if (rp_host_seed_has("kind=revision_task")) {
			char targets[80];
			if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
				rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
			}
			if (!rp_append_host_action_line("rp_api_compare", "host_action_revision_targets=", targets)) return 1;
		}
		if (rp_host_seed_has("kind=bundle_export")) {
			char bundle[48];
			if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle))) {
				rp_copy_text(bundle, sizeof(bundle), "evidence");
			}
			if (!rp_append_host_action_line("rp_api_compare", "host_action_bundle=", bundle)) return 1;
		}
		if (rp_host_seed_has("kind=agentcompare")) {
			char profile[48];
			if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
				rp_copy_text(profile, sizeof(profile), "plain_ucore");
			}
			if (!rp_append_host_action_line("rp_api_compare", "host_action_compare_profile=", profile)) return 1;
		}
		if (host_seed_has_workbench_action()) {
			char workbench[80];
			char detail[96];
			if (!copy_workbench_value("workbench=", workbench, sizeof(workbench))) {
				rp_copy_text(workbench, sizeof(workbench), "usable-workbench:RUN-900");
			}
			if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench=", workbench)) return 1;
			if (copy_workbench_value("question=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_question=", detail)) return 1;
			}
			if (copy_workbench_value("query=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_query=", detail)) return 1;
			}
			if (rp_host_seed_copy_value_for_kind("kind=workbench_advance", "task=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_advance_task=", detail)) return 1;
			}
			if (copy_workbench_value("step_limit=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_step_limit=", detail)) return 1;
			}
			if (rp_host_seed_copy_value_for_kind("kind=workbench_task", "task=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_task=", detail)) return 1;
			}
			if (copy_workbench_value("workbench_title=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_title=", detail)) return 1;
			}
			if (copy_workbench_value("literature_query=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_literature_query=", detail)) return 1;
			}
			if (copy_workbench_value("note_kind=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_note_kind=", detail)) return 1;
			}
			if (copy_workbench_value("body=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_note_body=", detail)) return 1;
			}
			if (copy_workbench_value("brief_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_brief_format=", detail)) return 1;
			}
			if (copy_workbench_value("dossier_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_dossier_format=", detail)) return 1;
			}
			if (copy_workbench_value("graph_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_graph_format=", detail)) return 1;
			}
			if (copy_workbench_value("citation_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_citation_format=", detail)) return 1;
			}
			if (copy_workbench_value("manuscript_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_manuscript_format=", detail)) return 1;
			}
			if (copy_workbench_value("audit_scope=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_audit_scope=", detail)) return 1;
			}
			if (copy_workbench_value("revision_area=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_revision_area=", detail)) return 1;
			}
			if (copy_workbench_value("revision_task=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_revision_task=", detail)) return 1;
			}
			if (copy_workbench_value("revision_status=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_revision_status=", detail)) return 1;
			}
			if (copy_workbench_value("board_filter=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_board_filter=", detail)) return 1;
			}
			if (copy_workbench_value("row_id=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_row_id=", detail)) return 1;
			}
			if (copy_workbench_value("row_status=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_row_status=", detail)) return 1;
			}
			if (copy_workbench_value("notes_filter=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_notes_filter=", detail)) return 1;
			}
			if (copy_workbench_value("runbook_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_runbook_format=", detail)) return 1;
			}
			if (copy_workbench_value("timeline_format=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_timeline_format=", detail)) return 1;
			}
			if (copy_workbench_value("handoff_scope=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_handoff_scope=", detail)) return 1;
			}
			if (copy_workbench_value("title=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_note_title=", detail)) return 1;
			}
			if (copy_workbench_value("manifest=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_manifest=", detail)) return 1;
			}
			if (copy_workbench_value("bundle=", detail, sizeof(detail))) {
				if (!rp_append_host_action_line("rp_api_compare", "host_action_workbench_bundle=", detail)) return 1;
			}
		}
	}
	if (!rp_write_file("rp_api_artifacts",
			   "api=artifacts\n"
			   "inputs=2\n"
			   "stages=5\n"
			   "artifact_records=4\n"
			   "manifest_records=4\n"
			   "preview_files=rp_report_text,rp_chart_data,rp_artifact,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json\n"
			   "package_manifest=ready\n"
			   "evidence_package=rp_package;download_index=rp_package\n"
			   "library_sources=rp_knowledge;bibliography=rp_runner;citation_plan=rp_runner\n"
			   "delivery_manifest=rp_package;export_bundle=rp_package;review_page=rp_package\n"
			   "delivery_files=8;delivery_checks=3\n"
			   "raw_downloads=5;upload_files=rp_input\n"
			   "real_artifact_items=5\n"
			   "bundle_items=18\n"
			   "evidence_bundle_zip=research-evidence-bundle.zip\n"
			   "evidence_bundle_entries=12\n"
			   "downloadable_units=3\n"
			   "notebook_downloadable=1\n"
			   "report=rp_report_text\n"
			   "chart=rp_chart_data\n"
			   "llm_relay_files=5\n"
			   "llm_roundtrip=ready\n"
			   "llm_matched_responses=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_data",
			   "api=data\n"
			   "run_id=RUN-042\n"
			   "dynamic_inputs=4\n"
			   "dynamic_queue=rp_input\n"
			   "ingested_files=2\n"
			   "dataset_snapshots=2\n"
			   "previews=2\n"
			   "quality_checks=7\n"
			   "transforms=2\n"
			   "collection_items=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify")) {
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "manifest=", value, sizeof(value)) &&
		    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "manifest=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "delivery-manifest.json");
		}
		if (!rp_append_host_action_line("rp_api_artifacts", "host_action_file_manifest=", value)) return 1;
		if (!rp_append_host_action_line("rp_api_data", "host_action_file_manifest=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "files=", value, sizeof(value)) &&
		    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "files=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "9");
		}
		if (!rp_append_host_action_line("rp_api_artifacts", "host_action_file_manifest_files=", value)) return 1;
		if (!rp_append_host_action_line("rp_api_data", "host_action_file_manifest_files=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "sha_records=", value, sizeof(value)) &&
		    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "sha_records=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "9");
		}
		if (!rp_append_host_action_line("rp_api_artifacts", "host_action_file_sha_records=", value)) return 1;
		if (!rp_append_host_action_line("rp_api_data", "host_action_file_sha_records=", value)) return 1;
		if (rp_host_seed_has("kind=workbench_file_verify")) {
			if (!rp_append_file("rp_api_artifacts", "host_action_file_verify=passed")) return 1;
			if (!rp_append_file("rp_api_data", "host_action_file_verify=passed")) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "verified=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "9");
			}
			if (!rp_append_host_action_line("rp_api_artifacts", "host_action_file_verified=", value)) return 1;
			if (!rp_append_host_action_line("rp_api_data", "host_action_file_verified=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "missing=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "0");
			}
			if (!rp_append_host_action_line("rp_api_artifacts", "host_action_file_missing=", value)) return 1;
			if (!rp_append_host_action_line("rp_api_data", "host_action_file_missing=", value)) return 1;
		}
	}
	if (!rp_write_file("rp_api_bio",
			   "api=bio\n"
			   "sample_registry=rp_sreg\n"
			   "ethics_review=rp_ethics\n"
			   "access_requests=rp_access\n"
			   "cohort_view=rp_cohort\n"
			   "sample_count=8\n"
			   "access_requests_count=3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_labres",
			   "api=lab-resources\n"
			   "instrument_registry=rp_instr\n"
			   "inventory=rp_invent\n"
			   "procurement=rp_procure\n"
			   "resource_schedule=rp_ressched\n"
			   "instrument_count=4\n"
			   "bookings=6\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_pub",
			   "api=publication\n"
			   "result_review=rp_resrev\n"
			   "publication_plan=rp_pubplan\n"
			   "peer_review_response=rp_peerresp\n"
			   "fair_package=rp_fairpkg\n"
			   "review_items=10\n"
			   "review_threads=2\n"
			   "review_comments=3\n"
			   "review_action_items=2\n"
			   "journal_targets=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_know",
			   "api=knowledge\n"
			   "lit_review=rp_litrev\n"
			   "citation_graph=rp_citegraph\n"
			   "semantic_index=rp_semindex\n"
			   "knowledge_answers=rp_kanswers\n"
			   "library_sources=rp_knowledge\n"
			   "workflow_portability=rp_wfio\n"
			   "evidence_protocols=1\n"
			   "evidence_extractions=3\n"
			   "prisma_flows=1\n"
			   "documents=17\n"
			   "answers=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_runtime",
			   "api=runtime\n"
			   "runtime_env=rp_runenv\n"
			   "notebook_exec=rp_nbexec\n"
			   "notebook_export=rp_nbexec\n"
			   "download_manifest=rp_repro\n"
			   "eln_record=rp_eln\n"
			   "worker_pool=rp_wpool\n"
			   "environments=4\n"
			   "executed_cells=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has_llm_relay_action()) {
		char value[96];
		if (!rp_host_seed_copy_llm_value("request_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "host-q1");
		}
		if (!rp_append_host_action_line("rp_api_runtime", "host_llm_request_id=", value)) return 1;
		if (!rp_host_seed_copy_llm_value("response_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "host-r1");
		}
		if (!rp_append_host_action_line("rp_api_runtime", "host_llm_response_id=", value)) return 1;
		if (!rp_host_seed_copy_llm_value("provider=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_api_runtime", "host_llm_provider=", value)) return 1;
		if (!rp_host_seed_copy_llm_value("case=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "missing_cloud_key");
		}
		if (!rp_append_host_action_line("rp_api_runtime", "host_llm_fallback=", value)) return 1;
	}
	if (!rp_write_file("rp_api_action",
			   "api=actions\n"
			   "actions=57\n"
			   "host_workflow_run=/actions/host-workflow/run\n"
			   "host_workflow_export=/actions/host-workflow/export\n"
			   "host_workflow_stage=/actions/host-workflow/stage-attempt\n"
			   "host_workflow_cache=/actions/host-workflow/cache-decision\n"
			   "host_workflow_retry=/actions/host-workflow/retry-decision\n"
			   "host_workflow_artifact=/actions/host-workflow/artifact-manifest\n"
			   "host_workflow_report=/actions/host-workflow/report-export\n"
			   "artifact_input=/actions/research/artifact-input\n"
			   "artifact_derive=/actions/research/artifact-derive\n"
			   "artifact_log=/actions/research/artifact-log\n"
			   "artifact_chart=/actions/research/artifact-chart\n"
			   "artifact_package=/actions/research/artifact-package\n"
			   "workflow_portability_run=/actions/workflow-portability/run\n"
			   "workflow_portability_import=/actions/workflow-portability/import\n"
			   "workflow_portability_plan=/actions/workflow-portability/plan\n"
			   "workflow_portability_bind=/actions/workflow-portability/bind\n"
			   "workflow_portability_rehearse=/actions/workflow-portability/rehearse\n"
			   "workflow_portability_review=/actions/workflow-portability/review\n"
			   "workflow_portability_package=/actions/workflow-portability/package\n"
			   "agentcompare_run=/actions/agentcompare/run\n"
			   "research_run=/actions/research/run\n"
			   "research_studio_launch=/actions/research/studio-launch\n"
			   "research_export=/actions/research/export\n"
			   "dynamic_submit=/actions/research/run\n"
			   "live_update_feed=rp_web_bundle\n"
			   "reader_contract=rp_web_bundle\n"
			   "research_review=/actions/research/review\n"
			   "research_revision_task=/actions/research/revision-task\n"
			   "research_run_revision=/actions/research/run-revision-task\n"
			   "operations_report=/actions/research/operations-report\n"
			   "operations_advance_next=/actions/research/operations-advance-next\n"
			   "operations_execute_next_plan=/actions/research/operations-execute-next-plan\n"
			   "workbench_delivery_dashboard=/actions/research/workbench-delivery-dashboard\n"
			   "workbench_delivery_execute_next=/actions/research/workbench-delivery-execute-next\n"
			   "workbench_quality_gate=/actions/research/workbench-quality-gate\n"
			   "workbench_quality_repair_plan=/actions/research/workbench-quality-repair-plan\n"
			   "workbench_quality_repair_execute=/actions/research/workbench-quality-repair-execute\n"
			   "workbench_plan_queue_row=/actions/research/workbench-plan-queue-row\n"
			   "workbench_plan_queue_execute=/actions/research/workbench-plan-queue-execute\n"
			   "workbench_action_item=/actions/research/workbench-action-item\n"
			   "project_space=/actions/research/project-space\n"
			   "project_space_note=/actions/research/project-space-note\n"
			   "project_space_action_item=/actions/research/project-space-action-item\n"
			   "project_space_answer=/actions/research/project-space-answer\n"
			   "project_space_repair_execute=/actions/research/project-space-repair-execute\n"
			   "project_handoff_audit=/actions/research/project-handoff-audit\n"
			   "project_release_gate=/actions/research/project-release-gate\n"
			   "project_snapshot=/actions/research/project-snapshot\n"
			   "project_snapshot_comparison=/actions/research/project-snapshot-comparison\n"
			   "project_reproducibility_audit=/actions/research/project-reproducibility-audit\n"
			   "project_provenance_graph=/actions/research/project-provenance-graph\n"
			   "project_delivery=/actions/research/project-delivery\n"
			   "package_intake=/actions/research/package-intake\n"
			   "research_search_save=/actions/research-search/save\n"
			   "research_search_export=/actions/research-search/export\n"
			   "research_search_note=/actions/research-search/note\n"
			   "research_search_action_item=/actions/research-search/action-item\n"
			   "llm_relay_request=/actions/research/llm-relay-request\n"
			   "llm_relay_response=/actions/research/llm-relay-response\n"
			   "llm_relay_fallback=/actions/research/llm-relay-fallback\n"
			   "delivery_manifest_builder=1\n"
			   "human_review_form=1\n"
			   "revision_task_runner=1\n"
			   "operations_actions=3\n"
			   "quality_actions=3\n"
			   "delivery_actions=2\n"
			   "project_space_actions=5\n"
			   "project_review_actions=8\n"
			   "studio_actions=1\n"
			   "research_search_actions=4\n"
			   "llm_relay_actions=3\n"
			   "artifact_actions=5\n"
			   "plan_queue_actions=2\n"
			   "action_item_actions=1\n"
			   "workbench_advance=1\n"
			   "notebook_download=1\n"
			   "bundle_download=1\n"
			   "action_state_records=12\n"
			   "validated_requests=8\n"
			   "precondition_checks=8\n"
			   "side_effect_records=16\n"
			   "action_audit_log=rp_actionio\n"
			   "download_manifest=rp_package\n"
			   "export_bundle=rp_package\n"
			   "redirect_status=303\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_actionio",
			   "requests=8\n"
			   "request=1;path=/actions/host-workflow/run;run_id=RUN-042;inject_failure=1;use_cache=1\n"
			   "request=2;path=/actions/host-workflow/export;workflow_run_id=RUN-042\n"
			   "request=3;path=/actions/agentcompare/run;profile=plain_ucore\n"
			   "request=4;path=/actions/research/run;provider=template;source_request=rp_input;dataset_file=rp_input;custom_runs=3\n"
			   "library_query=tag=reusable;source=rp_knowledge\n"
			   "request=5;path=/actions/research/export;run_id=usable-run:RUN-900\n"
			   "request=6;path=/actions/research/review;run_id=usable-run:RUN-900;decision=needs_revision;reviewer=Wang\n"
			   "request=7;path=/actions/research/revision-task;review_id=usable-review:RUN-900:1;requested_changes=2;targets=methods,chart_caption\n"
			   "request=8;path=/actions/research/run-revision-task;task_id=usable-revision-task:RUN-900:1;provider=template;revision_delta=rp_revision\n"
			   "responses=8\n"
			   "response=1;status=303;location=/runs/RUN-042;effect=host_workflow_run\n"
			   "response=2;status=303;location=/runs/RUN-042;effect=host_workflow_export\n"
			   "response=3;status=303;location=/compare;effect=agentcompare_run\n"
			   "response=4;status=303;location=/research/usable-run:RUN-900;effect=usable_research_run;generated_runs=3\n"
			   "response=5;status=303;location=/research/usable-run:RUN-900;effect=usable_research_export;delivery_manifest=rp_package;export_bundle=rp_package\n"
			   "response=6;status=303;location=/research/usable-run:RUN-900;effect=human_review;review_id=usable-review:RUN-900:1\n"
			   "response=7;status=303;location=/research/usable-run:RUN-900;effect=revision_task_created;task_id=usable-revision-task:RUN-900:1;revision_inputs=rp_review2,rp_revision\n"
			   "response=8;status=303;location=/research/usable-run:RUN-900-rev1;effect=revision_run_created;new_run=usable-run:RUN-900-rev1;applied_changes=2;revision_status=completed\n"
			   "actions=8\n"
			   "completed=8\n"
			   "failed=0\n"
			   "redirects=8\n"
			   "state_writes=14\n"
			   "audit_records=8\n"
			   "action_state_records=12\n"
			   "request_validation=passed\n"
			   "validated_requests=8\n"
			   "precondition_checks=8\n"
			   "precheck=1;path=/actions/host-workflow/run;requires=rp_stage_dag,rp_input_fastq;result=pass\n"
			   "precheck=2;path=/actions/host-workflow/export;requires=rp_artifact_manifest;result=pass\n"
			   "precheck=3;path=/actions/agentcompare/run;requires=rp_agentcmp;result=pass\n"
			   "precheck=4;path=/actions/research/run;requires=rp_input,rp_knowledge;result=pass\n"
			   "precheck=5;path=/actions/research/export;requires=rp_runner,rp_package;result=pass\n"
			   "precheck=6;path=/actions/research/review;requires=rp_review2;result=pass\n"
			   "precheck=7;path=/actions/research/revision-task;requires=rp_review2,rp_revision;result=pass\n"
			   "precheck=8;path=/actions/research/run-revision-task;requires=rp_revision;result=pass\n"
			   "side_effect_records=16\n"
			   "state_write=1;target=rp_stage_state;field=last_action;value=host_workflow_run\n"
			   "state_write=2;target=rp_package;field=workflow_export;value=ready\n"
			   "state_write=3;target=rp_agentcmp;field=last_compare;value=plain_ucore\n"
			   "state_write=4;target=rp_runner;field=research_run;value=usable-run:RUN-900\n"
			   "state_write=5;target=rp_package;field=research_export;value=ready\n"
			   "state_write=6;target=rp_runner;field=human_review;value=needs_revision\n"
			   "state_write=7;target=rp_revision;field=task_created;value=usable-revision-task:RUN-900:1\n"
			   "state_write=8;target=rp_runner;field=revision_run;value=usable-run:RUN-900-rev1\n"
			   "state_write=9;target=rp_runner;field=workbench_task;value=delivery_manifest_ready\n"
			   "state_write=10;target=rp_package;field=download_manifest;value=ready\n"
			   "state_write=11;target=rp_package;field=bundle_download;value=research-evidence-bundle.zip\n"
			   "state_write=12;target=rp_package;field=review_page;value=review_html\n"
			   "idempotency_checks=8\n"
			   "idempotency_key=host_workflow_run:RUN-042;state=accepted\n"
			   "idempotency_key=research_run:usable-run:RUN-900;state=accepted\n"
			   "idempotency_key=research_revision:usable-revision-task:RUN-900:1;state=accepted\n"
			   "action_step=host_workflow_run;input=rp_stage_dag;output=rp_stage_state;status=ready\n"
			   "action_step=host_workflow_export;input=rp_artifact_manifest;output=rp_package;status=ready\n"
			   "action_step=agentcompare_run;input=rp_agentcmp;output=rp_api_compare;status=ready\n"
			   "action_step=research_run;input=rp_input;output=rp_runner;generated_runs=3;status=ready\n"
			   "action_step=research_export;input=rp_runner;output=rp_package;bundle=research-evidence-bundle.zip;status=ready\n"
			   "action_step=human_review;input=rp_review2;output=rp_runner;decision=needs_revision;status=ready\n"
			   "action_step=revision_task;input=rp_review2;output=rp_revision;targets=methods,chart_caption;status=ready\n"
			   "action_step=run_revision;input=rp_revision;output=rp_runner;new_run=usable-run:RUN-900-rev1;status=ready\n"
			   "action_step=workbench_advance;input=rp_runner;task=delivery_manifest;from=waiting;to=ready;status=ready\n"
			   "action_step=notebook_download;input=rp_nbexec;output=reproducible-analysis.ipynb;status=ready\n"
			   "action_step=bundle_download;input=rp_package;output=research-evidence-bundle.zip;entries=12;status=ready\n"
			   "action_step=review_page_open;input=rp_package;output=review_html;status=ready\n"
			   "action_trace=rp_input->rp_runner->rp_review2->rp_revision->rp_package->rp_web_bundle\n"
			   "idempotent_action_keys=8\n"
			   "state_after_actions=workbench:ready,review:needs_revision,revision:completed,bundle:ready\n"
			   "post_action_state=rp_stage_state,rp_package,rp_runner,rp_revision,rp_agentcmp\n"
			   "download_manifest_generated=1\n"
			   "download_outputs=reproducible-analysis.ipynb,research-evidence-bundle.zip,delivery-manifest.md\n"
			   "host_export=review_html\n"
			   "host_contains=Stage DAG,Agent Decisions,Custom Research,Comparison Metrics\n"
			   "compare_runs=1\n"
			   "passed_cases=3\n"
			   "metrics_case=user_on_plain_ucore_real_artifacts\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_uresrun",
			   "runs=3\n"
			   "run_id=usable-run:RUN-900\n"
			   "run_id_2=usable-run:RUN-901\n"
			   "run_id_3=usable-run:RUN-902\n"
			   "revision_run=usable-run:RUN-900-rev1\n"
			   "revision_task_id=usable-revision-task:RUN-900:1\n"
			   "revision_status=completed\n"
			   "source_request=rp_input;source_form=rp_input;upload_files=rp_input\n"
			   "workbench=rp_runner;workbench_export=rp_runner\n"
			   "library_sources=rp_knowledge;bibliography=rp_runner;citation_plan=rp_runner\n"
			   "source_dataset=rp_input;source_run=rp_runner\n"
			   "title=Browser started study\n"
			   "title_2=Second browser study\n"
			   "title_3=Contrasting browser study\n"
			   "question=Can this platform run a custom research task?\n"
			   "provider=template\n"
			   "dataset_rows=3\n"
			   "dataset_rows_total=9\n"
			   "stages=5\n"
			   "artifacts=36\n"
			   "agent_messages=21\n"
			   "agent_decisions=15\n"
			   "analysis=mean_control:12,mean_treatment:20,stronger:treatment\n"
			   "analysis_2=mean_control:8,mean_treatment:13,stronger:treatment\n"
			   "analysis_3=mean_control:30,mean_treatment:28,stronger:control\n"
			   "export=review_html;review_page=rp_package\n"
			   "delivery_manifest=rp_package;export_bundle=rp_package;raw_downloads=5\n"
			   "export_sections=6\n"
			   "contains=Stage DAG,Agent Decisions,Artifacts,LLM Relay\n"
			   "status=ok\n")) {
		return 1;
	}
	if (!rp_write_file("rp_studio",
			   "studio=usable-research-studio\n"
			   "sessions=1\n"
			   "latest_session=usable-research-studio-session:W1:1\n"
			   "studio_session=usable-research-studio-session:W1:1;title=Research studio project;goal=Compare recovered evidence;direction=evidence review;workbench=W1;run=RUN-042;answer=rp_workbench_answer;decision=studio_completed;status=ready\n"
			   "studio_material=default;notes=rp_input;csv_rows=9;references=5;workspace=rp_input;status=ready\n"
			   "studio_links=default;studio=/research-studio;workbench=/research/workbench/W1;project=/research/project/lab-gene-x;download=/download/research-studio-session/usable-research-studio-session-W1-1;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_count() > 0) {
		if (!rp_append_file("rp_uresrun", "host_action_run_outputs=rp_report_text,rp_artifact_manifest,rp_nbexec,rp_package")) return 1;
		if (rp_host_seed_has("kind=research_run")) {
			char value[96];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Browser started study");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_title=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "question=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Can this platform run a custom research task?");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_question=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "provider=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "template");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_provider=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "4");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_dataset_rows=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_entries=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "2");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_reference_entries=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "workspace_files=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "4");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_workspace_files=", value)) return 1;
		}
		if (rp_host_seed_has_research_data_action()) {
			char value[96];
			if (!rp_append_file("rp_uresrun", "host_action_research_inputs=ready")) return 1;
			if (rp_host_seed_has("kind=dataset")) {
				if (!rp_host_seed_copy_value_for_kind("kind=dataset", "title=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "Reusable response table");
				}
				if (!rp_append_host_action_line("rp_uresrun", "host_action_dataset_title=", value)) return 1;
			}
			if (rp_host_seed_has("kind=library_source")) {
				if (!rp_host_seed_copy_value_for_kind("kind=library_source", "citation_key=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "agentlibrary2026");
				}
				if (!rp_append_host_action_line("rp_uresrun", "host_action_library_citation=", value)) return 1;
			}
			if (rp_host_seed_has("kind=template")) {
				if (!rp_host_seed_copy_value_for_kind("kind=template", "name=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "Reusable response comparison");
				}
				if (!rp_append_host_action_line("rp_uresrun", "host_action_template_name=", value)) return 1;
			}
			if (rp_host_seed_copy_workspace_value("root=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_uresrun", "host_action_workspace_root=", value)) return 1;
			}
			if (rp_host_seed_copy_workspace_value("manifest=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_uresrun", "host_action_workspace_manifest=", value)) return 1;
			}
			if (rp_host_seed_has("kind=literature_search")) {
				if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "query=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "agent workflow provenance");
				}
				if (!rp_append_host_action_line("rp_uresrun", "host_action_literature_query=", value)) return 1;
			}
			if (rp_host_seed_has("kind=evidence_review")) {
				if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "included=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "3");
				}
				if (!rp_append_host_action_line("rp_uresrun", "host_action_evidence_included=", value)) return 1;
			}
			if (rp_host_seed_has("kind=evidence_protocol")) {
				if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "title=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "Agent workflow evidence protocol");
				}
				if (!rp_append_host_action_line("rp_uresrun", "host_action_protocol_title=", value)) return 1;
			}
		}
		if (rp_host_seed_has_workbench_action()) {
			char value[96];
			if (!rp_append_file("rp_uresrun", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package")) return 1;
			if (!rp_host_seed_copy_workbench_value("workbench=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "usable-workbench:RUN-900");
			}
			if (!rp_append_host_action_line("rp_uresrun", "host_action_workbench=", value)) return 1;
			if (rp_host_seed_copy_workbench_value("task=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_uresrun", "host_action_workbench_task=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("manifest=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_uresrun", "host_action_workbench_manifest=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("bundle=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_uresrun", "host_action_workbench_bundle=", value)) return 1;
			}
		}
	}
	if (!rp_write_file("rp_web_bundle",
			   "bundle=host-web-ui\n"
			   "routes=74\n"
			   "get_routes=17\n"
			   "post_routes=57\n"
			   "api_payloads=14\n"
			   "action_payloads=1\n"
			   "action_state_records=12\n"
			   "action_validation=passed\n"
			   "side_effect_records=16\n"
			   "download_manifest_generated=1\n"
			   "source_pages=5\n"
			   "render_sections=7\n"
			   "artifact_previews=3\n"
			   "real_artifact_items=5\n"
			   "request_form=rp_input;upload_files=rp_input;workspace_imports=1\n"
			   "dynamic_inputs=4;dynamic_queue=rp_input;live_update_feed=rp_web_bundle;host_ui_events=10\n"
			   "workbench=rp_runner;workbench_tasks=9;workbench_export=rp_runner\n"
			   "project_review=ready;project=lab-gene-x;source=rp_web_bundle;status=ready\n"
			   "release_gate=project-release-gate:lab-gene-x;project=lab-gene-x;decision=release;checks=6;required_actions=0;suggested_actions=2;status=ready\n"
			   "project_snapshot=project-snapshot:lab-gene-x:1;project=lab-gene-x;files=11;present=11;missing=0;hash_records=11;changes=0;status=ready\n"
			   "snapshot_comparison=project-snapshot-comparison:lab-gene-x:latest;project=lab-gene-x;left=project-snapshot:lab-gene-x:0;right=project-snapshot:lab-gene-x:1;changed_files=0;decision=stable;status=ready\n"
			   "reproducibility_audit=project-reproducibility-audit:lab-gene-x;project=lab-gene-x;inputs=2;outputs=8;notebooks=2;claim_audits=1;decision=passed;status=ready\n"
			   "provenance_graph=project-provenance-graph:lab-gene-x;project=lab-gene-x;nodes=9;edges=12;dot=project-provenance.dot;status=ready\n"
			   "project_delivery=project-delivery:lab-gene-x;project=lab-gene-x;decision=ready;bundle=project-bundle.zip;release_gate=release;handoff=ready;status=ready\n"
			   "package_intake=package-intake:external-review;label=External review package;decision=accepted;files=5;sha256=checked;status=ready\n"
			   "package_index=project-package-index;handoff=ready;release_gate=release;snapshot=stable;reproducibility=passed;provenance=ready;status=ready\n"
			   "library_sources=rp_knowledge;bibliography=rp_runner;citation_plan=rp_runner;evidence_protocols=1;evidence_extractions=3\n"
			   "workflow_portability=rp_wfio;adapter_specs=6;migration_steps=9;rehearsal_cases=4\n"
			   "coherence_checks=9;namespace_checks=12;surface_checks=13;agentos_readiness_checks=7\n"
			   "delivery_manifest=rp_package;review_page=rp_package;export_bundle=rp_package\n"
			   "delivery_files=8;delivery_checks=3;evidence_bundle_entries=12\n"
			   "prisma_flows=1\n"
			   "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md\n"
			   "human_reviews=1;revision_tasks=1;revised_run=usable-run:RUN-900-rev1\n"
			   "revision_delta=rp_revision\n"
			   "review_threads=2;review_comments=3;review_action_items=2\n"
			   "runner_detail_fields=16\n"
			   "evidence_package=rp_package\n"
			   "package_manifest=ready\n"
			   "downloadable_units=3\n"
			   "notebook_export=rp_nbexec;notebook_download=rp_repro\n"
			   "active_actions=rp_actionio;workbench_advance=rp_runner;bundle_download=rp_package\n"
			   "static_site=rp_site\n"
			   "static_site_pages=42\n"
			   "static_site_json_payloads=14\n"
			   "static_site_download_links=8\n"
			   "reader_contract=host_plain_ucore_v2\n"
			   "reader_contract_version=2\n"
			   "reader_ready=1\n"
			   "reader_views=37\n"
			   "calculations_page=rp_calculation;jobs=1;retrieved=3;parser_results=1;status=ready\n"
			   "real_task_page=rp_realtask;dataset=palmer-penguins;rows=344;answer_audit=pass;status=ready\n"
			   "analysis_results_page=rp_analysisres;runs=2;tables=2;statistics=2;figures=2;status=ready\n"
			   "decision_support_page=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=ready\n"
			   "experiment_campaigns_page=rp_campaign;campaigns=1;trials=4;best_trial=04;status=ready\n"
			   "statistical_design_page=rp_stdesign;designs=1;power=underpowered;randomization=balanced;status=ready\n"
			   "model_registry_page=rp_modelreg;models=1;versions=1;evaluations=1;deployments=1;status=ready\n"
			   "release_dossier_page=rp_reldossier;sections=7;decision=ready_for_review;status=ready\n"
			   "reader_actions=57\n"
			   "reader_payload_files=rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_bio,rp_api_labres,rp_api_pub,rp_api_know,rp_api_runtime,rp_api_action,rp_web_routes\n"
			   "reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_studio,rp_web_bundle\n"
			   "reader_required_sections=routes,payloads,actions,live_update,downloads,compare\n"
			   "reader_event_stream=rp_web_bundle\n"
			   "reader_fallback=rp_site\n"
			   "reader_state_source=plain_ucore_files\n"
			   "runner_files=5\n"
			   "workflow_portability_records=1\n"
			   "data_pipeline_files=6\n"
			   "custom_research_files=1\n"
			   "custom_research_runs=3\n"
			   "research_service_files=25\n"
			   "llm_relay_files=5\n"
			   "llm_roundtrip=ready\n"
			   "agent_records=5\n"
			   "status=ready\n")) {
		return 1;
	}

	const char *host_action_seed = rp_host_seed_text();
	int host_actions = count_lines_silent(host_action_seed);
	int host_action_seeded = host_actions > 0;
	if (!host_action_seeded) {
		host_actions = rp_count_lines("rp_host_action_inbox");
	}
	if (host_actions > 0) {
		char line[160];
		make_host_action_count_line(line, sizeof(line), host_actions);
		if (!rp_append_file("rp_actionio", line)) return 1;
		if (host_action_seeded) {
			if (!rp_append_file("rp_actionio", "host_action_source=rp_host_action_seed")) return 1;
		} else if (!rp_append_file("rp_actionio", "host_action_source=rp_host_action_inbox")) {
			return 1;
		}
		if (!host_action_seeded && rp_count_lines("rp_host_action_queue") > 0) {
			if (!rp_append_file("rp_actionio", "host_action_queue=rp_host_action_queue")) return 1;
		}
		if (!host_action_seeded && rp_count_lines("rp_host_action_plan") > 0) {
			if (!rp_append_file("rp_actionio", "host_action_plan=rp_host_action_plan")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=research_run")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=research_run"))) {
			if (!rp_append_file("rp_actionio", "host_action_research_run=1")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=studio_launch")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=studio_launch"))) {
			char title[96];
			char goal[96];
			char direction[64];
			char provider[48];
			char workbench[64];
			char run_id[64];
			char answer[64];
			char material[96];
			char detail[360];
			if (!rp_append_file("rp_actionio", "host_action_studio=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_studio_outputs=rp_studio,rp_runner,rp_package")) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "title=", title, sizeof(title))) {
				rp_copy_text(title, sizeof(title), "Studio evidence review");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "goal=", goal, sizeof(goal))) {
				rp_copy_text(goal, sizeof(goal), "Turn pasted materials into a workbench answer");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "direction=", direction, sizeof(direction))) {
				rp_copy_text(direction, sizeof(direction), "evidence review");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "provider_id=", provider, sizeof(provider))) {
				rp_copy_text(provider, sizeof(provider), "template");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "workbench_id=", workbench, sizeof(workbench)) &&
			    !rp_host_seed_copy_value_for_kind("kind=studio_launch", "workbench=", workbench, sizeof(workbench))) {
				rp_copy_text(workbench, sizeof(workbench), "W1");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "latest_run_id=", run_id, sizeof(run_id))) {
				rp_copy_text(run_id, sizeof(run_id), "RUN-042");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "latest_answer_id=", answer, sizeof(answer))) {
				rp_copy_text(answer, sizeof(answer), "answer-1");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=studio_launch", "material_notes=", material, sizeof(material))) {
				rp_copy_text(material, sizeof(material), "Pasted notes and table rows");
			}
			if (!rp_append_file("rp_studio", "host_action_studio_launch=accepted")) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_title=", title)) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_goal=", goal)) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_direction=", direction)) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_provider=", provider)) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_workbench=", workbench)) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_run=", run_id)) return 1;
			if (!rp_append_host_action_line("rp_studio", "host_action_studio_answer=", answer)) return 1;
			rp_copy_text(detail, sizeof(detail), "latest_session=usable-research-studio-session:");
			rp_append_text(detail, sizeof(detail), workbench);
			rp_append_text(detail, sizeof(detail), ":1");
			if (!rp_append_file("rp_studio", detail)) return 1;
			rp_copy_text(detail, sizeof(detail), "studio_session=usable-research-studio-session:");
			rp_append_text(detail, sizeof(detail), workbench);
			rp_append_text(detail, sizeof(detail), ":1;title=");
			rp_append_text(detail, sizeof(detail), title);
			rp_append_text(detail, sizeof(detail), ";goal=");
			rp_append_text(detail, sizeof(detail), goal);
			rp_append_text(detail, sizeof(detail), ";direction=");
			rp_append_text(detail, sizeof(detail), direction);
			rp_append_text(detail, sizeof(detail), ";workbench=");
			rp_append_text(detail, sizeof(detail), workbench);
			rp_append_text(detail, sizeof(detail), ";run=");
			rp_append_text(detail, sizeof(detail), run_id);
			rp_append_text(detail, sizeof(detail), ";answer=");
			rp_append_text(detail, sizeof(detail), answer);
			rp_append_text(detail, sizeof(detail), ";decision=studio_completed;status=ready");
			if (!rp_append_file("rp_studio", detail)) return 1;
			rp_copy_text(detail, sizeof(detail), "studio_material=host_action;notes=");
			rp_append_text(detail, sizeof(detail), material);
			rp_append_text(detail, sizeof(detail), ";csv_rows=host;references=host;workspace=host_input;status=ready");
			if (!rp_append_file("rp_studio", detail)) return 1;
			rp_copy_text(detail, sizeof(detail), "studio_links=host_action;studio=/research-studio;workbench=/research/workbench/");
			rp_append_text(detail, sizeof(detail), workbench);
			rp_append_text(detail, sizeof(detail), ";project=/research/project/lab-gene-x;download=/download/research-studio-session/usable-research-studio-session-");
			rp_append_text(detail, sizeof(detail), workbench);
			rp_append_text(detail, sizeof(detail), "-1;status=ready");
			if (!rp_append_file("rp_studio", detail)) return 1;
			if (!rp_append_host_action_line("rp_runner", "host_action_studio_session=usable-research-studio-session:", workbench)) return 1;
			if (!rp_append_host_action_line("rp_runner", "host_action_studio_title=", title)) return 1;
			if (!rp_append_host_action_line("rp_runner", "host_action_studio_goal=", goal)) return 1;
			if (!rp_append_file("rp_package", "host_action_studio_session=ready")) return 1;
			if (!rp_append_host_action_line("rp_package", "host_action_studio_download=usable-research-studio-session-", workbench)) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=agentcompare")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=agentcompare"))) {
			if (!rp_append_file("rp_actionio", "host_action_agentcompare=1")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=host_workflow")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=host_workflow_export")) ||
		    (host_action_seeded && rp_host_seed_has_host_workflow_step_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=host_workflow"))) {
			if (!rp_append_file("rp_actionio", "host_action_workflow=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_workflow_outputs=rp_stage_dag,rp_stage_state,rp_run_events,rp_artifact_manifest,rp_package")) return 1;
			if ((host_action_seeded && rp_host_seed_has_host_workflow_step_action()) ||
			    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=host_workflow_"))) {
				if (!rp_append_file("rp_actionio", "host_action_workflow_steps=5")) return 1;
			}
		}
		if ((host_action_seeded && rp_host_seed_has_workflow_portability_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workflow_portability"))) {
			char value[96];
			if (!rp_append_file("rp_actionio", "host_action_portability=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp")) return 1;
			if ((host_action_seeded && rp_host_seed_has_workflow_portability_step_action()) ||
			    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workflow_portability_"))) {
				if (!rp_append_file("rp_actionio", "host_action_portability_steps=6")) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_workflow_portability_value("compare_profile=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_actionio", "host_action_portability_profile=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_workflow_portability_value("target_runtime=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_actionio", "host_action_portability_target=", value)) return 1;
			}
		}
		if ((host_action_seeded && rp_host_seed_has_artifact_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=artifact_"))) {
			if (!rp_append_file("rp_actionio", "host_action_artifacts=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_llm_relay_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=llm_relay"))) {
			if (!rp_append_file("rp_actionio", "host_action_llm_relay=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_llm_outputs=rp_llm_req,rp_llmq,rp_llm_resp,rp_llm_packets,rp_llm_hostreq,rp_llm_fallback")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=human_review")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=human_review"))) {
			if (!rp_append_file("rp_actionio", "host_action_human_review=1")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=revision_task")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=revision_run")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=revision_task")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=revision_run"))) {
			if (!rp_append_file("rp_actionio", "host_action_revision=1")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_research_input_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=dataset")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=library_source")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=template")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workspace_inspect")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workspace_import")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workspace_import_run"))) {
			if (!rp_append_file("rp_actionio", "host_action_research_inputs=1")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_evidence_input_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=literature_search")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=evidence_review")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=evidence_protocol"))) {
			if (!rp_append_file("rp_actionio", "host_action_evidence_inputs=1")) return 1;
		}
		if ((host_action_seeded && host_seed_has_workbench_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_complete")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_advance")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_auto_advance")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_task")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_note")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_notes")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_handoff_package")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_readiness")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_answer")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_answer_audit")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_evidence_search")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_brief")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_evidence_dossier")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_evidence_graph")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_citations")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_manuscript")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_manuscript_audit")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_manuscript_revision_plan")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_manuscript_revision_task")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_task_board")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_task_board_row")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_runbook")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_timeline")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_file_manifest")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_file_verify")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_export"))) {
			if (!rp_append_file("rp_actionio", "host_action_workbench=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=bundle_export")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=research_export")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=notebook_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=bundle_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=research_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=notebook_export"))) {
			if (!rp_append_file("rp_actionio", "host_action_export=1")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_platform_ops_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=operations_")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=project_space")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=project_")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=package_intake")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=research_search")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_quality")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_delivery")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_plan_queue")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_action_item"))) {
			char value[96];
			if (!rp_append_file("rp_actionio", "host_action_platform_ops=1")) return 1;
			if (!rp_append_file("rp_actionio", "host_action_platform_ops_outputs=rp_runner,rp_package,rp_api_action,rp_web_bundle")) return 1;
			if (host_action_seeded && rp_host_seed_copy_platform_ops_value("query=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_actionio", "host_action_search_query=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_platform_ops_value("project_id=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_actionio", "host_action_project_id=", value)) return 1;
			}
			if (host_action_seeded && (rp_host_seed_has("kind=project_handoff_audit") ||
			    rp_host_seed_has("kind=project_release_gate") ||
			    rp_host_seed_has("kind=project_snapshot") ||
			    rp_host_seed_has("kind=project_snapshot_comparison") ||
			    rp_host_seed_has("kind=project_reproducibility_audit") ||
			    rp_host_seed_has("kind=project_provenance_graph") ||
			    rp_host_seed_has("kind=project_delivery") ||
			    rp_host_seed_has("kind=package_intake"))) {
				if (!rp_append_file("rp_actionio", "host_action_project_review=1")) return 1;
				if (!rp_append_file("rp_actionio", "host_action_project_review_outputs=rp_web_bundle")) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_handoff_audit")) {
				if (!rp_append_file("rp_web_bundle", "host_action_project_review_handoff=audited")) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_release_gate")) {
				if (!rp_host_seed_copy_value_for_kind("kind=project_release_gate", "decision=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "release");
				}
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_project_release_gate=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_snapshot")) {
				if (!rp_append_file("rp_web_bundle", "host_action_project_snapshot=recorded")) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_snapshot_comparison")) {
				if (!rp_append_file("rp_web_bundle", "host_action_project_snapshot_comparison=stable")) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_reproducibility_audit")) {
				if (!rp_append_file("rp_web_bundle", "host_action_project_reproducibility=passed")) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_provenance_graph")) {
				if (!rp_append_file("rp_web_bundle", "host_action_project_provenance_graph=exported")) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=project_delivery")) {
				if (!rp_host_seed_copy_value_for_kind("kind=project_delivery", "bundle=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "project-bundle.zip");
				}
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_project_delivery=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_has("kind=package_intake")) {
				if (!rp_host_seed_copy_value_for_kind("kind=package_intake", "label=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "External review package");
				}
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_project_package_intake=", value)) return 1;
			}
		}
		if (!rp_append_file("rp_web_bundle", line)) return 1;
		if (host_action_seeded) {
			if (!rp_append_file("rp_web_bundle", "host_action_source=rp_host_action_seed")) return 1;
		} else if (!rp_append_file("rp_web_bundle", "host_action_source=rp_host_action_inbox")) {
			return 1;
		}
		if (!rp_append_file("rp_web_bundle", "host_action_state_files=rp_input,rp_studio,rp_runner,rp_review2,rp_revision,rp_package,rp_nbexec,rp_agentcmp,rp_lit,rp_knowledge")) return 1;
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=studio_launch")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=studio_launch"))) {
			char value[96];
			if (!rp_append_file("rp_web_bundle", "host_action_studio_outputs=rp_studio,rp_runner,rp_package")) return 1;
			if (host_action_seeded && rp_host_seed_copy_value_for_kind("kind=studio_launch", "title=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_studio_title=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_value_for_kind("kind=studio_launch", "goal=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_studio_goal=", value)) return 1;
			}
		}
		if ((host_action_seeded && rp_host_seed_has_research_input_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=dataset")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=library_source")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=template")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workspace_inspect")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workspace_import")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workspace_import_run"))) {
			if (!rp_append_file("rp_web_bundle", "host_action_research_inputs=rp_input,rp_runner,rp_api_run")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=host_workflow")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=host_workflow_export")) ||
		    (host_action_seeded && rp_host_seed_has_host_workflow_step_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=host_workflow"))) {
			if (!rp_append_file("rp_web_bundle", "host_action_workflow_outputs=rp_stage_dag,rp_stage_state,rp_run_events,rp_artifact_manifest,rp_package")) return 1;
			if ((host_action_seeded && rp_host_seed_has_host_workflow_step_action()) ||
			    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=host_workflow_"))) {
				if (!rp_append_file("rp_web_bundle", "host_action_workflow_steps=5")) return 1;
			}
		}
		if ((host_action_seeded && rp_host_seed_has_workflow_portability_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workflow_portability"))) {
			char value[96];
			if (!rp_append_file("rp_web_bundle", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp")) return 1;
			if ((host_action_seeded && rp_host_seed_has_workflow_portability_step_action()) ||
			    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workflow_portability_"))) {
				if (!rp_append_file("rp_web_bundle", "host_action_portability_steps=6")) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_workflow_portability_value("compare_profile=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_portability_profile=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_workflow_portability_value("target_runtime=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_portability_target=", value)) return 1;
			}
		}
		if ((host_action_seeded && rp_host_seed_has_artifact_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=artifact_"))) {
			if (!rp_append_file("rp_web_bundle", "host_action_artifacts=1")) return 1;
			if (!rp_append_file("rp_web_bundle", "host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_llm_relay_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=llm_relay"))) {
			if (!rp_append_file("rp_web_bundle", "host_action_llm_relay=rp_llm_req,rp_llmq,rp_llm_resp,rp_llm_packets,rp_llm_hostreq,rp_llm_fallback")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_evidence_input_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=literature_search")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=evidence_review")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=evidence_protocol"))) {
			if (!rp_append_file("rp_web_bundle", "host_action_evidence_inputs=rp_lit,rp_knowledge,rp_api_evidence")) return 1;
		}
		if ((host_action_seeded && host_seed_has_workbench_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench"))) {
			if (!rp_append_file("rp_web_bundle", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package")) return 1;
		}
		if ((host_action_seeded && rp_host_seed_has_platform_ops_action()) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=operations_")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=project_space")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=project_")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=package_intake")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=research_search")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_quality")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_delivery")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_plan_queue")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_action_item"))) {
			char value[96];
			if (!rp_append_file("rp_web_bundle", "host_action_platform_ops=rp_runner,rp_package,rp_api_action")) return 1;
			if (host_action_seeded && rp_host_seed_copy_platform_ops_value("query=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_search_query=", value)) return 1;
			}
			if (host_action_seeded && rp_host_seed_copy_platform_ops_value("project_id=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_web_bundle", "host_action_project_id=", value)) return 1;
			}
			if (host_action_seeded && (rp_host_seed_has("kind=project_release_gate") ||
			    rp_host_seed_has("kind=project_snapshot") ||
			    rp_host_seed_has("kind=project_reproducibility_audit") ||
			    rp_host_seed_has("kind=project_provenance_graph") ||
			    rp_host_seed_has("kind=project_delivery") ||
			    rp_host_seed_has("kind=package_intake"))) {
				if (!rp_append_file("rp_web_bundle", "host_action_project_review=rp_web_bundle")) return 1;
			}
		}
		if (!file_contains_silent("rp_runner", "backend_evidence_report=rp_backend_exec")) {
			if (!rp_append_file("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;status=ready")) return 1;
		}
		if (!file_contains_silent("rp_report_text", "backend_evidence_report=rp_backend_exec")) {
			if (!rp_append_file("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index;status=ready")) return 1;
		}
		if (!rp_append_status("host_reader_actions=ready")) return 1;
		printf("rp_web_export: host_reader_actions=%d\n", host_actions);
	}

	if (!rp_append_file("rp_ack", "ack=web_export;msg=web;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=api_actions;msg=action;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.read_ui")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_routes")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_home_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_run_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_agent_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_evidence_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_compare_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_artifacts_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_data_api")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_bundle")) return 1;
	if (!rp_append_status("web_export=ready")) return 1;
	if (!rp_append_status("web_routes=ready")) return 1;
	if (!rp_append_status("web_bundle=ready")) return 1;
	if (!rp_append_status("api_home=ready")) return 1;
	if (!rp_append_status("api_run=ready")) return 1;
	if (!rp_append_status("api_agents=ready")) return 1;
	if (!rp_append_status("api_evidence=ready")) return 1;
	if (!rp_append_status("api_compare=ready")) return 1;
	if (!rp_append_status("api_artifacts=ready")) return 1;
	if (!rp_append_status("api_data=ready")) return 1;
	if (!rp_append_status("api_bio=ready")) return 1;
	if (!rp_append_status("api_lab_resources=ready")) return 1;
	if (!rp_append_status("api_publication=ready")) return 1;
	if (!rp_append_status("api_knowledge=ready")) return 1;
	if (!rp_append_status("api_runtime=ready")) return 1;
	if (!rp_append_status("api_action=ready")) return 1;
	if (!rp_append_status("api_actions=ready")) return 1;
	if (!rp_append_status("action_validation=ready")) return 1;
	if (!rp_append_status("action_side_effects=ready")) return 1;
	if (!rp_append_status("actionio=ready")) return 1;
	if (!rp_append_status("usable_research=ready")) return 1;
	if (!rp_append_status("action_exports=ready")) return 1;
	printf("rp_web_export: routes=74 api_payloads=14 actions=57 bundle=ready status=ready\n");
	return 0;
}
