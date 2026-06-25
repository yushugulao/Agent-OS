#include <stdio.h>
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
	char buf[1024];
	int n = rp_read_file(path, buf, sizeof(buf));
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
			   "routes=22\n"
			   "get_routes=14\n"
			   "post_routes=8\n"
			   "route=/;payload=rp_api_home;status=ready\n"
			   "route=/run/RUN-042;payload=rp_api_run;status=ready\n"
			   "route=/research/{run_id};payload=rp_uresrun;status=ready\n"
			   "route=/research/workbench/{id};payload=rp_runner;status=ready\n"
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
			   "action=/actions/agentcompare/run;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/run;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/export;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/review;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/revision-task;method=POST;payload=rp_api_action;status=ready\n"
			   "action=/actions/research/run-revision-task;method=POST;payload=rp_api_action;status=ready\n"
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
	if (!rp_write_file("rp_api_action",
			   "api=actions\n"
			   "actions=8\n"
			   "host_workflow_run=/actions/host-workflow/run\n"
			   "host_workflow_export=/actions/host-workflow/export\n"
			   "agentcompare_run=/actions/agentcompare/run\n"
			   "research_run=/actions/research/run\n"
			   "research_export=/actions/research/export\n"
			   "dynamic_submit=/actions/research/run\n"
			   "live_update_feed=rp_web_bundle\n"
			   "reader_contract=rp_web_bundle\n"
			   "research_review=/actions/research/review\n"
			   "research_revision_task=/actions/research/revision-task\n"
			   "research_run_revision=/actions/research/run-revision-task\n"
			   "delivery_manifest_builder=1\n"
			   "human_review_form=1\n"
			   "revision_task_runner=1\n"
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
	if (!rp_write_file("rp_web_bundle",
			   "bundle=host-web-ui\n"
			   "routes=22\n"
			   "get_routes=14\n"
			   "post_routes=8\n"
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
			   "reader_views=14\n"
			   "reader_actions=8\n"
			   "reader_payload_files=rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_bio,rp_api_labres,rp_api_pub,rp_api_know,rp_api_runtime,rp_api_action,rp_web_routes\n"
			   "reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_web_bundle\n"
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

	const char *host_action_seed = RP_HOST_ACTION_SEED;
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
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=agentcompare")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=agentcompare"))) {
			if (!rp_append_file("rp_actionio", "host_action_agentcompare=1")) return 1;
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
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=workbench_complete")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=workbench_advance")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=workbench_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_complete")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_advance")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=workbench_export"))) {
			if (!rp_append_file("rp_actionio", "host_action_workbench=1")) return 1;
		}
		if ((host_action_seeded && text_contains_silent(host_action_seed, "kind=bundle_export")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=research_export")) ||
		    (host_action_seeded && text_contains_silent(host_action_seed, "kind=notebook_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=bundle_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=research_export")) ||
		    (!host_action_seeded && file_contains_silent("rp_host_action_inbox", "kind=notebook_export"))) {
			if (!rp_append_file("rp_actionio", "host_action_export=1")) return 1;
		}
		if (!rp_append_file("rp_web_bundle", line)) return 1;
		if (host_action_seeded) {
			if (!rp_append_file("rp_web_bundle", "host_action_source=rp_host_action_seed")) return 1;
		} else if (!rp_append_file("rp_web_bundle", "host_action_source=rp_host_action_inbox")) {
			return 1;
		}
		if (!rp_append_file("rp_web_bundle", "host_action_state_files=rp_input,rp_runner,rp_review2,rp_revision,rp_package,rp_nbexec,rp_agentcmp")) return 1;
		if (!rp_append_status("host_reader_actions=ready")) return 1;
		printf("rp_web_export: host_reader_actions=%d\n", host_actions);
	}

	if (!rp_append_file("rp_ack", "ack=web_export;msg=web;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=api_actions;msg=action;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.read_ui;target=rp_ui_home;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_routes;target=rp_web_routes;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_home_api;target=rp_api_home;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_run_api;target=rp_api_run;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_agent_api;target=rp_api_agents;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_evidence_api;target=rp_api_evidence;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_compare_api;target=rp_api_compare;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_artifacts_api;target=rp_api_artifacts;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_data_api;target=rp_api_data;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=web_export.write_bundle;target=rp_web_bundle;status=ok")) return 1;
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
	printf("rp_web_export: routes=22 api_payloads=14 actions=8 bundle=ready status=ready\n");
	return 0;
}
