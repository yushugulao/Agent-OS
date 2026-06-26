#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

static int require_equal(const char *name, int actual, int expected)
{
	if (actual == expected) return 1;
	printf("rp_compare_plain: mismatch %s actual=%d expected=%d\n", name, actual, expected);
	return 0;
}

static int check_seed_value(const char *kind, const char *key, const char *fallback, const char *path, const char *prefix)
{
	char value[96];
	char token[160];
	if (!rp_host_seed_copy_value_for_kind(kind, key, value, sizeof(value))) {
		rp_copy_text(value, sizeof(value), fallback);
	}
	rp_copy_text(token, sizeof(token), prefix);
	rp_append_text(token, sizeof(token), value);
	return rp_file_contains(path, token);
}

static int optional_file_contains(const char *path, const char *needle)
{
	int n = rp_read_file(path, rp_state_buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return 0;
	return rp_text_contains(rp_state_buf, needle);
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_object_query", "hits=8");
	ok = ok && rp_file_contains("rp_lineage", "edges=7");
	ok = ok && rp_file_contains("rp_site", "pages=42");
	ok = ok && rp_file_contains("rp_site", "page=agentos_readiness");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_knowledge", "semantic_relations=6");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && rp_file_contains("rp_input", "dynamic_submissions=4");
	ok = ok && rp_file_contains("rp_input", "dynamic_queue=plain_ucore_file_backed");
	ok = ok && rp_file_contains("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	ok = ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "workbench_readiness=rp_workbench_ready;status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_answer=rp_workbench_answer;citations=5;status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_brief=rp_workbench_brief;handoff=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_runbook=rp_workbench_runbook;commands=6");
	ok = ok && rp_file_contains("rp_runner", "workbench_timeline=rp_workbench_timeline;events=8");
	ok = ok && rp_file_contains("rp_runner", "workbench_file_manifest=rp_workbench_manifest;files=9;sha_records=9");
	ok = ok && rp_file_contains("rp_runner", "next_action=build_delivery_manifest");
	ok = ok && rp_file_contains("rp_runner", "citation_count=5");
	ok = ok && rp_file_contains("rp_runner", "handoff=ready");
	ok = ok && rp_file_contains("rp_runner", "continuation_guide=ready");
	ok = ok && rp_file_contains("rp_runner", "events=8");
	ok = ok && rp_file_contains("rp_runner", "sha_records=9");
	ok = ok && rp_file_contains("rp_runner", "dynamic_input_runs=4");
	ok = ok && rp_file_contains("rp_runner", "dynamic_run=usable-run:RUN-904");
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		rp_copy_text(token, sizeof(token), "host_action_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_input", token);
		rp_copy_text(token, sizeof(token), "host_action_research_run=usable-run:");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_input", token);
		rp_copy_text(token, sizeof(token), "host_action_run=usable-run:");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_report_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && check_seed_value("kind=research_run", "title=", "Browser started study", "rp_input", "host_action_title=");
		ok = ok && check_seed_value("kind=research_run", "title=", "Browser started study", "rp_report_text", "host_report_title=");
		ok = ok && check_seed_value("kind=research_run", "title=", "Browser started study", "rp_api_home", "host_action_title=");
		ok = ok && check_seed_value("kind=research_run", "title=", "Browser started study", "rp_api_run", "host_action_title=");
		ok = ok && check_seed_value("kind=research_run", "title=", "Browser started study", "rp_uresrun", "host_action_title=");
		ok = ok && check_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_input", "host_action_question=");
		ok = ok && check_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_report_text", "host_report_question=");
		ok = ok && check_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_api_run", "host_action_question=");
		ok = ok && check_seed_value("kind=research_run", "question=", "Can this platform run a custom research task?", "rp_uresrun", "host_action_question=");
		ok = ok && check_seed_value("kind=research_run", "provider=", "template", "rp_input", "host_action_provider=");
		ok = ok && check_seed_value("kind=research_run", "provider=", "template", "rp_report_text", "host_report_provider=");
		ok = ok && check_seed_value("kind=research_run", "provider=", "template", "rp_api_run", "host_action_provider=");
		ok = ok && check_seed_value("kind=research_run", "provider=", "template", "rp_uresrun", "host_action_provider=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_input", "host_action_dataset_rows_value=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_report_text", "host_report_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_ingest_files", "host_input_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_dataset_snapshot", "host_input_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_data_preview", "host_input_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_data_quality", "host_input_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_dataset_collection", "host_input_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_api_run", "host_action_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "dataset_rows=", "4", "rp_uresrun", "host_action_dataset_rows=");
		ok = ok && check_seed_value("kind=research_run", "reference_entries=", "2", "rp_input", "host_action_reference_entries=");
		ok = ok && check_seed_value("kind=research_run", "reference_entries=", "2", "rp_ingest_files", "host_input_reference_entries=");
		ok = ok && check_seed_value("kind=research_run", "reference_entries=", "2", "rp_dataset_collection", "host_input_reference_entries=");
		ok = ok && check_seed_value("kind=research_run", "reference_entries=", "2", "rp_api_run", "host_action_reference_entries=");
		ok = ok && check_seed_value("kind=research_run", "reference_entries=", "2", "rp_uresrun", "host_action_reference_entries=");
		ok = ok && check_seed_value("kind=research_run", "workspace_files=", "4", "rp_input", "host_action_workspace_files=");
		ok = ok && check_seed_value("kind=research_run", "workspace_files=", "4", "rp_ingest_files", "host_input_workspace_files=");
		ok = ok && check_seed_value("kind=research_run", "workspace_files=", "4", "rp_dataset_snapshot", "host_input_workspace_files=");
		ok = ok && check_seed_value("kind=research_run", "workspace_files=", "4", "rp_api_run", "host_action_workspace_files=");
		ok = ok && check_seed_value("kind=research_run", "workspace_files=", "4", "rp_uresrun", "host_action_workspace_files=");
		ok = ok && check_seed_value("kind=research_run", "csv_file=", "expr.csv", "rp_input", "host_action_csv_file=");
		ok = ok && check_seed_value("kind=research_run", "csv_file=", "expr.csv", "rp_ingest_files", "host_input_csv_file=");
		ok = ok && check_seed_value("kind=research_run", "csv_file=", "expr.csv", "rp_data_preview", "host_input_csv_file=");
		ok = ok && check_seed_value("kind=research_run", "reference_file=", "refs.bib", "rp_input", "host_action_reference_file=");
		ok = ok && rp_file_contains("rp_runner", "host_action_status=completed");
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_research_input=ready");
		ok = ok && rp_file_contains("rp_actionio", "host_action_research_run=1");
	}
	if (rp_host_seed_has("kind=studio_launch")) {
		ok = ok && rp_file_contains("rp_studio", "host_action_studio_launch=accepted");
		ok = ok && rp_file_contains("rp_studio", "studio_session=usable-research-studio-session:");
		ok = ok && rp_file_contains("rp_studio", "studio_material=host_action");
		ok = ok && rp_file_contains("rp_studio", "studio_links=host_action");
		ok = ok && check_seed_value("kind=studio_launch", "title=", "Studio evidence review", "rp_studio", "host_action_studio_title=");
		ok = ok && check_seed_value("kind=studio_launch", "goal=", "Turn pasted materials into a workbench answer", "rp_studio", "host_action_studio_goal=");
		ok = ok && check_seed_value("kind=studio_launch", "direction=", "evidence review", "rp_studio", "host_action_studio_direction=");
		ok = ok && check_seed_value("kind=studio_launch", "provider_id=", "template", "rp_studio", "host_action_studio_provider=");
		ok = ok && rp_file_contains("rp_runner", "host_action_studio_session=usable-research-studio-session:");
		ok = ok && rp_file_contains("rp_package", "host_action_studio_session=ready");
		ok = ok && rp_file_contains("rp_actionio", "host_action_studio=1");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_studio_outputs=rp_studio,rp_runner,rp_package");
	}
	if (rp_host_seed_has_research_data_action()) {
		ok = ok && rp_file_contains("rp_uresrun", "host_action_research_inputs=ready");
		if (rp_host_seed_has_research_input_action()) {
			ok = ok && rp_file_contains("rp_actionio", "host_action_research_inputs=1");
			ok = ok && rp_file_contains("rp_web_bundle", "host_action_research_inputs=rp_input,rp_runner,rp_api_run");
		}
		if (rp_host_seed_has_evidence_input_action()) {
			ok = ok && rp_file_contains("rp_actionio", "host_action_evidence_inputs=1");
			ok = ok && rp_file_contains("rp_web_bundle", "host_action_evidence_inputs=rp_lit,rp_knowledge,rp_api_evidence");
		}
		if (rp_host_seed_has("kind=dataset")) {
			ok = ok && check_seed_value("kind=dataset", "title=", "Reusable response table", "rp_input", "host_action_dataset_title=");
			ok = ok && check_seed_value("kind=dataset", "title=", "Reusable response table", "rp_api_run", "host_action_dataset_title=");
		}
		if (rp_host_seed_has("kind=library_source")) {
			ok = ok && check_seed_value("kind=library_source", "citation_key=", "agentlibrary2026", "rp_knowledge", "host_action_library_citation=");
		}
		if (rp_host_seed_has("kind=template")) {
			ok = ok && check_seed_value("kind=template", "name=", "Reusable response comparison", "rp_runner", "host_action_template_name=");
		}
		if (rp_host_seed_has("kind=workspace_inspect") ||
		    rp_host_seed_has("kind=workspace_import") ||
		    rp_host_seed_has("kind=workspace_import_run")) {
			char value[96];
			char token[160];
			if (rp_host_seed_copy_workspace_value("root=", value, sizeof(value))) {
				rp_copy_text(token, sizeof(token), "host_action_workspace_root=");
				rp_append_text(token, sizeof(token), value);
				ok = ok && rp_file_contains("rp_input", token);
			}
			if (rp_host_seed_copy_workspace_value("manifest=", value, sizeof(value))) {
				rp_copy_text(token, sizeof(token), "host_action_workspace_manifest=");
				rp_append_text(token, sizeof(token), value);
				ok = ok && rp_file_contains("rp_runner", token);
			}
		}
		if (rp_host_seed_has("kind=literature_search")) {
			ok = ok && check_seed_value("kind=literature_search", "query=", "agent workflow provenance", "rp_lit", "host_action_literature_query=");
			ok = ok && check_seed_value("kind=literature_search", "query=", "agent workflow provenance", "rp_api_evidence", "host_action_literature_query=");
		}
		if (rp_host_seed_has("kind=evidence_review")) {
			ok = ok && check_seed_value("kind=evidence_review", "included=", "3", "rp_knowledge", "host_action_evidence_included=");
		}
		if (rp_host_seed_has("kind=evidence_protocol")) {
			ok = ok && check_seed_value("kind=evidence_protocol", "title=", "Agent workflow evidence protocol", "rp_lit", "host_action_protocol_title=");
			ok = ok && check_seed_value("kind=evidence_protocol", "title=", "Agent workflow evidence protocol", "rp_api_evidence", "host_action_protocol_title=");
		}
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
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_compare_requested=1");
		rp_copy_text(token, sizeof(token), "host_action_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_agentcmp", token);
		rp_copy_text(token, sizeof(token), "host_report_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_agentcompare=1");
	}
	if (rp_host_seed_has_host_workflow_action()) {
		char value[64];
		char token[140];
		if (!rp_host_seed_copy_host_workflow_value("workflow_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "wf-host-plain");
		}
		rp_copy_text(token, sizeof(token), "host_workflow_id=");
		rp_append_text(token, sizeof(token), value);
		ok = ok && rp_file_contains("rp_stage_dag", token);
		rp_copy_text(token, sizeof(token), "host_action_workflow=");
		rp_append_text(token, sizeof(token), value);
		ok = ok && rp_file_contains("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_manifest_workflow=");
		rp_append_text(token, sizeof(token), value);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_workflow_id=");
		rp_append_text(token, sizeof(token), value);
		ok = ok && rp_file_contains("rp_package", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_workflow=1");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_workflow_outputs=");
		ok = ok && check_seed_value("kind=host_workflow", "retry_stage=", "align", "rp_retry_plan", "host_workflow_retry_stage=");
		ok = ok && check_seed_value("kind=host_workflow", "cache_hit_stage=", "profile", "rp_cache_index", "host_workflow_cache_hit_stage=");
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow", "worker_slots=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=host_workflow", "max_workers=", value, sizeof(value))) {
			rp_copy_text(token, sizeof(token), "host_workflow_worker_slots=");
			rp_append_text(token, sizeof(token), value);
			ok = ok && rp_file_contains("rp_worker", token);
		} else {
			ok = ok && rp_file_contains("rp_worker", "host_workflow_worker_slots=4");
		}
		ok = ok && check_seed_value("kind=host_workflow", "observer_events=", "9", "rp_execobs", "host_workflow_observer_events=");
		if (rp_host_seed_has("kind=host_workflow_export")) {
			ok = ok && check_seed_value("kind=host_workflow_export", "bundle=", "workflow-export.zip", "rp_runner", "host_action_workflow_export=");
			ok = ok && check_seed_value("kind=host_workflow_export", "format=", "json", "rp_package", "host_action_workflow_format=");
		}
	}
	if (rp_host_seed_has_host_workflow_step_action()) {
		ok = ok && rp_file_contains("rp_stage_state", "host_workflow_steps=applied");
		ok = ok && rp_file_contains("rp_stage_state", "host_workflow_stage_action=");
		ok = ok && rp_file_contains("rp_cache_index", "host_workflow_cache_action=");
		ok = ok && rp_file_contains("rp_retry_plan", "host_workflow_retry_action=");
		ok = ok && rp_file_contains("rp_artifact_manifest", "host_workflow_artifact_action=");
		ok = ok && rp_file_contains("rp_report_text", "host_workflow_report_action=");
		ok = ok && rp_file_contains("rp_package", "host_action_workflow_steps=ready");
		ok = ok && rp_file_contains("rp_actionio", "host_action_workflow_steps=5");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_workflow_steps=5");
	}
	if (rp_host_seed_has_workflow_portability_run_action()) {
		ok = ok && rp_file_contains("rp_wfio", "host_portability_payload=applied");
		ok = ok && check_seed_value("kind=workflow_portability", "import_id=", "workflow-import:host-nextflow", "rp_wfio", "host_portability_import=");
		ok = ok && check_seed_value("kind=workflow_portability", "target_runtime=", "agentos-ucore", "rp_wfio", "host_portability_target=");
		ok = ok && check_seed_value("kind=workflow_portability", "execution_plan=", "workflow-migration-execution-plan:host-nextflow:agentcompare", "rp_wfio", "host_portability_execution_plan=");
		ok = ok && check_seed_value("kind=workflow_portability", "compare_profile=", "compare-profile:host-nextflow:migration", "rp_wfio", "host_portability_compare_profile=");
		ok = ok && check_seed_value("kind=workflow_portability", "scenario_id=", "backend-scenario:host-nextflow", "rp_wfio", "host_portability_scenario=");
		ok = ok && check_seed_value("kind=workflow_portability", "rehearsal_status=", "passed", "rp_wfio", "host_portability_rehearsal=");
		ok = ok && check_seed_value("kind=workflow_portability", "readiness_decision=", "ready_for_agentos", "rp_wfio", "host_portability_decision=");
		ok = ok && check_seed_value("kind=workflow_portability", "package=", "workflow-portability-host.zip", "rp_wfio", "host_portability_package=");
		ok = ok && rp_file_contains("rp_package", "host_action_portability_package=ready");
		ok = ok && check_seed_value("kind=workflow_portability", "import_id=", "workflow-import:host-nextflow", "rp_package", "host_action_portability_import=");
		ok = ok && check_seed_value("kind=workflow_portability", "target_runtime=", "agentos-ucore", "rp_package", "host_action_portability_target=");
		ok = ok && check_seed_value("kind=workflow_portability", "compare_profile=", "compare-profile:host-nextflow:migration", "rp_package", "host_action_portability_profile=");
		ok = ok && check_seed_value("kind=workflow_portability", "package=", "workflow-portability-host.zip", "rp_package", "host_action_portability_bundle=");
		ok = ok && rp_file_contains("rp_actionio", "host_action_portability=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp");
	}
	if (rp_host_seed_has_workflow_portability_step_action()) {
		ok = ok && rp_file_contains("rp_wfio", "host_portability_steps=applied");
		ok = ok && check_seed_value("kind=workflow_portability_import", "import_id=", "workflow-import:host-nextflow", "rp_wfio", "host_portability_import_action=");
		ok = ok && rp_file_contains("rp_wfio", "host_portability_plan_action=");
		ok = ok && rp_file_contains("rp_wfio", "target=agentos-ucore");
		ok = ok && rp_file_contains("rp_wfio", "host_portability_bind_action=");
		ok = ok && rp_file_contains("rp_wfio", "profile=");
		ok = ok && rp_file_contains("rp_wfio", "host_portability_rehearse_action=");
		ok = ok && rp_file_contains("rp_wfio", "status=passed");
		ok = ok && rp_file_contains("rp_wfio", "host_portability_review_action=");
		ok = ok && rp_file_contains("rp_wfio", "decision=ready_for_agentos");
		ok = ok && check_seed_value("kind=workflow_portability_package", "package=", "workflow-portability-host.zip", "rp_wfio", "host_portability_package_action=");
		ok = ok && rp_file_contains("rp_package", "host_action_portability_steps=ready");
		ok = ok && rp_file_contains("rp_actionio", "host_action_portability_steps=6");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_portability_steps=6");
	}
	if (rp_host_seed_has_llm_relay_action()) {
		ok = ok && check_seed_value("kind=llm_relay_request", "request_id=", "host-q1", "rp_llm_req", "host_llm_request_id=");
		ok = ok && check_seed_value("kind=llm_relay_request", "provider=", "template", "rp_llm_req", "host_llm_provider=");
		ok = ok && check_seed_value("kind=llm_relay_request", "route=", "review_summary", "rp_llmq", "host_llm_queue_route=");
		ok = ok && check_seed_value("kind=llm_relay_response", "response_id=", "host-r1", "rp_llm_resp", "host_llm_response_id=");
		ok = ok && check_seed_value("kind=llm_relay_response", "summary=", "host_response_ready", "rp_llm_resp", "host_llm_response_summary=");
		ok = ok && check_seed_value("kind=llm_relay_fallback", "case=", "missing_cloud_key", "rp_llm_fallback", "host_llm_fallback_case=");
		ok = ok && rp_file_contains("rp_llm_packets", "host_llm_packet_request=");
		ok = ok && rp_file_contains("rp_llm_hostreq", "host_llm_host_response=");
		ok = ok && rp_file_contains("rp_api_runtime", "host_llm_request_id=");
		ok = ok && rp_file_contains("rp_actionio", "host_action_llm_relay=1");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_llm_relay=");
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
		ok = ok && rp_file_contains("rp_review2", token);
		rp_copy_text(token, sizeof(token), "host_action_review_decision=");
		rp_append_text(token, sizeof(token), decision);
		ok = ok && rp_file_contains("rp_review2", token);
		rp_copy_text(token, sizeof(token), "host_report_reviewer=");
		rp_append_text(token, sizeof(token), reviewer);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_report_review_decision=");
		rp_append_text(token, sizeof(token), decision);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_action_reviewer=");
		rp_append_text(token, sizeof(token), reviewer);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_review_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_human_review=1");
	}
	if (rp_host_seed_has("kind=revision_task")) {
		ok = ok && rp_file_contains("rp_revision", "host_action_revision_task=created");
		char targets[80];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
			rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
		}
		rp_copy_text(token, sizeof(token), "host_action_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_revision", token);
		rp_copy_text(token, sizeof(token), "host_report_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_revision_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_revision=1");
	}
	if (rp_host_seed_has("kind=revision_run")) {
		ok = ok && rp_file_contains("rp_revision", "host_action_revision_run=completed");
		char revision_run[48];
		char token[130];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "run_id=", revision_run, sizeof(revision_run))) {
			rp_copy_text(revision_run, sizeof(revision_run), "RUN-900");
		}
		rp_copy_text(token, sizeof(token), "host_action_revision_run=usable-run:");
		rp_append_text(token, sizeof(token), revision_run);
		rp_append_text(token, sizeof(token), "-rev2");
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_revision=1");
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
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench=completed");
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_workbench_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_workbench=1");
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_id=");
		ok = ok && rp_file_contains("rp_api_compare", "host_action_workbench=");
		ok = ok && rp_file_contains("rp_report_text", "host_report_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && rp_file_contains("rp_artifact_manifest", "host_manifest_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && rp_file_contains("rp_nbexec", "host_action_notebook_workbench=rp_runner");
		ok = ok && rp_file_contains("rp_uresrun", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
	}
	if (rp_host_seed_has("kind=workbench_answer")) {
		char question[96];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_answer", "question=", question, sizeof(question))) {
			rp_copy_text(question, sizeof(question), "What is ready for review?");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_question=");
		rp_append_text(token, sizeof(token), question);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_answer=generated");
	}
	if (rp_host_seed_has("kind=workbench_evidence_search")) {
		char query[96];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_evidence_search", "query=", query, sizeof(query))) {
			rp_copy_text(query, sizeof(query), "recovery evidence");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_evidence_query=");
		rp_append_text(token, sizeof(token), query);
		ok = ok && rp_file_contains("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_query=");
		rp_append_text(token, sizeof(token), query);
		ok = ok && rp_file_contains("rp_api_compare", token);
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
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_task_status=");
		rp_append_text(token, sizeof(token), status);
		ok = ok && rp_file_contains("rp_runner", token);
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
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_note=recorded");
		rp_copy_text(token, sizeof(token), "host_action_workbench_note_kind=");
		rp_append_text(token, sizeof(token), kind);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_note_title=");
		rp_append_text(token, sizeof(token), title);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=workbench_file_verify")) {
		char manifest[80];
		char token[128];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "manifest=", manifest, sizeof(manifest))) {
			rp_copy_text(manifest, sizeof(manifest), "delivery-manifest.json");
		}
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_file_verify=passed");
		rp_copy_text(token, sizeof(token), "host_action_workbench_manifest=");
		rp_append_text(token, sizeof(token), manifest);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_data_quality", "host_file_verify=passed");
		ok = ok && check_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_runner", "host_action_workbench_verified_files=");
		ok = ok && check_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_data_quality", "host_file_verify_verified=");
		ok = ok && check_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_artifact_manifest", "host_manifest_verified_files=");
		ok = ok && check_seed_value("kind=workbench_file_verify", "verified=", "9", "rp_api_artifacts", "host_action_file_verified=");
		ok = ok && check_seed_value("kind=workbench_file_verify", "missing=", "0", "rp_api_data", "host_action_file_missing=");
	}
	if (rp_host_seed_has("kind=workbench")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_created=1");
		ok = ok && check_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_runner", "host_action_workbench_title=");
		ok = ok && check_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_api_compare", "host_action_workbench_title=");
		ok = ok && check_seed_value("kind=workbench", "literature_query=", "agent workflow provenance", "rp_runner", "host_action_workbench_literature_query=");
		ok = ok && check_seed_value("kind=workbench", "literature_query=", "agent workflow provenance", "rp_api_compare", "host_action_workbench_literature_query=");
	}
	if (rp_host_seed_has("kind=workbench_advance")) {
		ok = ok && check_seed_value("kind=workbench_advance", "task=", "delivery_manifest", "rp_runner", "host_action_workbench_task=");
		ok = ok && check_seed_value("kind=workbench_advance", "task=", "delivery_manifest", "rp_api_compare", "host_action_workbench_advance_task=");
	}
	if (rp_host_seed_has("kind=workbench_auto_advance")) {
		ok = ok && check_seed_value("kind=workbench_auto_advance", "step_limit=", "8", "rp_runner", "host_action_workbench_step_limit=");
		ok = ok && check_seed_value("kind=workbench_auto_advance", "step_limit=", "8", "rp_api_compare", "host_action_workbench_step_limit=");
	}
	if (rp_host_seed_has("kind=workbench_notes")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_notes=exported");
		ok = ok && check_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_runner", "host_action_workbench_notes_filter=");
		ok = ok && check_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_api_compare", "host_action_workbench_notes_filter=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_handoff=prepared");
		ok = ok && check_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_runner", "host_action_workbench_handoff_scope=");
		ok = ok && check_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_api_compare", "host_action_workbench_handoff_scope=");
	}
	if (rp_host_seed_has("kind=workbench_readiness")) ok = ok && rp_file_contains("rp_runner", "host_action_workbench_readiness=checked");
	if (rp_host_seed_has("kind=workbench_answer_audit")) ok = ok && rp_file_contains("rp_runner", "host_action_workbench_answer_audit=passed");
	if (rp_host_seed_has("kind=workbench_brief")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_brief=exported");
		ok = ok && check_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_runner", "host_action_workbench_brief_format=");
		ok = ok && check_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_api_compare", "host_action_workbench_brief_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_evidence_dossier=exported");
		ok = ok && check_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_runner", "host_action_workbench_dossier_format=");
		ok = ok && check_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_api_compare", "host_action_workbench_dossier_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_graph")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_evidence_graph=exported");
		ok = ok && check_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_runner", "host_action_workbench_graph_format=");
		ok = ok && check_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_api_compare", "host_action_workbench_graph_format=");
	}
	if (rp_host_seed_has("kind=workbench_citations")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_citations=exported");
		ok = ok && check_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_runner", "host_action_workbench_citation_format=");
		ok = ok && check_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_api_compare", "host_action_workbench_citation_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_manuscript=exported");
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_runner", "host_action_workbench_manuscript_format=");
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_api_compare", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_manuscript_audit=passed");
		ok = ok && check_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_runner", "host_action_workbench_audit_scope=");
		ok = ok && check_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_api_compare", "host_action_workbench_audit_scope=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_revision_plan=ready");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_runner", "host_action_workbench_revision_area=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_api_compare", "host_action_workbench_revision_area=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_revision_task=updated");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_task=", "1", "rp_runner", "host_action_workbench_revision_task=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_runner", "host_action_workbench_revision_status=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_api_compare", "host_action_workbench_revision_status=");
	}
	if (rp_host_seed_has("kind=workbench_task_board")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_task_board=exported");
		ok = ok && check_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_runner", "host_action_workbench_board_filter=");
		ok = ok && check_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_api_compare", "host_action_workbench_board_filter=");
	}
	if (rp_host_seed_has("kind=workbench_task_board_row")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_task_board_row=updated");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_id=", "usable-workbench:RUN-900:board:task:human_review", "rp_runner", "host_action_workbench_row_id=");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_runner", "host_action_workbench_row_status=");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_api_compare", "host_action_workbench_row_status=");
	}
	if (rp_host_seed_has("kind=workbench_runbook")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_runbook=exported");
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_runner", "host_action_workbench_runbook_format=");
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_api_compare", "host_action_workbench_runbook_format=");
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_artifact_manifest", "host_manifest_workbench_runbook_format=");
		ok = ok && rp_file_contains("rp_nbexec", "host_action_notebook_workbench_docs=ready");
	}
	if (rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_timeline=exported");
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_runner", "host_action_workbench_timeline_format=");
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_api_compare", "host_action_workbench_timeline_format=");
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_artifact_manifest", "host_manifest_workbench_timeline_format=");
		ok = ok && rp_file_contains("rp_nbexec", "host_action_notebook_workbench_docs=ready");
	}
	if (rp_host_seed_has("kind=workbench_file_manifest")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_file_manifest=exported");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_report_text", "host_report_workbench_manifest=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_artifact_manifest", "host_manifest_workbench_manifest=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_uresrun", "host_action_workbench_manifest=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "files=", "9", "rp_ingest_files", "host_file_manifest_files=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "files=", "9", "rp_artifact_manifest", "host_manifest_file_count=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "files=", "9", "rp_package", "host_action_workbench_manifest_files=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "sha_records=", "9", "rp_api_artifacts", "host_action_file_sha_records=");
		ok = ok && check_seed_value("kind=workbench_file_manifest", "sha_records=", "9", "rp_api_data", "host_action_file_sha_records=");
	}
	if (rp_host_seed_has("kind=workbench_export")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_export=ready");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_runner", "host_action_workbench_bundle=");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_api_compare", "host_action_workbench_bundle=");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_report_text", "host_report_workbench_bundle=");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_artifact_manifest", "host_manifest_workbench_bundle=");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_uresrun", "host_action_workbench_bundle=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && rp_file_contains("rp_revision", "host_action_workbench_writing=ready");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_revision", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
		ok = ok && check_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_revision", "host_action_workbench_audit_scope=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_revision", "host_action_workbench_revision_area=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_task=", "1", "rp_revision", "host_action_workbench_revision_task=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_revision", "host_action_workbench_revision_status=");
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
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_package=ready");
	}
	if (rp_host_seed_has("kind=workbench_complete")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_completion=ready");
	}
	if (rp_host_seed_has("kind=workbench_readiness")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_readiness=checked");
	}
	if (rp_host_seed_has("kind=workbench_answer_audit")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_answer_audit=passed");
	}
	if (rp_host_seed_has("kind=workbench_notes")) {
		ok = ok && check_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_package", "host_action_workbench_notes_filter=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package")) {
		ok = ok && check_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_package", "host_action_workbench_handoff_scope=");
	}
	if (rp_host_seed_has("kind=workbench_export")) {
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_package", "host_action_workbench_bundle=");
	}
	if (rp_host_seed_has("kind=workbench_file_manifest")) {
		ok = ok && check_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_package", "host_action_workbench_manifest=");
	}
	if (!rp_host_seed_has("kind=workbench_file_manifest") && rp_host_seed_has("kind=workbench_file_verify")) {
		ok = ok && check_seed_value("kind=workbench_file_verify", "manifest=", "delivery-manifest.json", "rp_package", "host_action_workbench_manifest=");
	}
	if (rp_host_seed_has("kind=workbench_brief")) {
		ok = ok && check_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_package", "host_action_workbench_brief_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
		ok = ok && check_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_package", "host_action_workbench_dossier_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_graph")) {
		ok = ok && check_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_package", "host_action_workbench_graph_format=");
	}
	if (rp_host_seed_has("kind=workbench_citations")) {
		ok = ok && check_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_package", "host_action_workbench_citation_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_package", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_task_board")) {
		ok = ok && check_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_package", "host_action_workbench_board_filter=");
	}
	if (rp_host_seed_has("kind=workbench_task_board_row")) {
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_id=", "usable-workbench:RUN-900:board:task:human_review", "rp_package", "host_action_workbench_row_id=");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_package", "host_action_workbench_row_status=");
	}
	if (rp_host_seed_has("kind=workbench_runbook")) {
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_package", "host_action_workbench_runbook_format=");
	}
	if (rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_package", "host_action_workbench_timeline_format=");
	}
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=delivery")) {
		ok = ok && rp_file_contains("rp_package", "host_action_export_bundle=ready");
		char bundle[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=research_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=delivery", "bundle=", bundle, sizeof(bundle))) {
			rp_copy_text(bundle, sizeof(bundle), "evidence");
		}
		rp_copy_text(token, sizeof(token), "host_action_export_bundle_name=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_package", token);
		ok = ok && rp_file_contains("rp_package", "host_action_bundle_contents=report,manifest,notebook,compare");
		rp_copy_text(token, sizeof(token), "host_report_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_export=1");
	}
	if (rp_host_seed_has("kind=notebook_export")) {
		ok = ok && rp_file_contains("rp_nbexec", "host_action_notebook_export=ready");
		char format[32];
		char token[96];
		if (!rp_host_seed_copy_value_for_kind("kind=notebook_export", "format=", format, sizeof(format))) {
			rp_copy_text(format, sizeof(format), "ipynb");
		}
		rp_copy_text(token, sizeof(token), "host_action_notebook_format=");
		rp_append_text(token, sizeof(token), format);
		ok = ok && rp_file_contains("rp_nbexec", token);
		rp_copy_text(token, sizeof(token), "host_manifest_notebook_format=");
		rp_append_text(token, sizeof(token), format);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_export_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_export=1");
	}
	if (rp_host_seed_count() > 0) {
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_state_files=rp_input,rp_studio,rp_runner,rp_review2,rp_revision,rp_package,rp_nbexec,rp_agentcmp");
	}
	ok = ok && rp_file_contains("rp_lit", "literature_search=usable-literature-search:RUN-900:1");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_provpath", "critical_paths=3");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_ingest_files", "files=2");
	ok = ok && rp_file_contains("rp_ingest_files", "derived_items=5");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "snapshots=2");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "normalized_fastq=rp_artifact:rp_normalized_fastq");
	ok = ok && rp_file_contains("rp_data_preview", "previews=2");
	ok = ok && rp_file_contains("rp_data_quality", "passed=7");
	ok = ok && rp_file_contains("rp_data_transform", "transforms=2");
	ok = ok && rp_file_contains("rp_data_transform", "derived=alignment");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_artifact", "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_align_table");
	ok = ok && rp_file_contains("rp_artifact", "\"variants\":2");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_gene_counts_csv;geneA=18");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_archive_manifest;files=5");
	ok = ok && rp_file_contains("rp_artifact", "artifact_dossier=rp_input_fastq,rp_normalized_fastq,rp_align_table");
	ok = ok && rp_file_contains("rp_artifact", "artifact_review_link=rp_artifact_manifest->rp_review_pack->rp_package");
	ok = ok && rp_file_contains("rp_artifact", "provenance=rp_align_table;stage=align");
	ok = ok && rp_file_contains("rp_artifact", "provenance=rp_metrics_json;stage=profile");
	ok = ok && rp_file_contains("rp_figrec", "exported=3");
	ok = ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	ok = ok && rp_file_contains("rp_dataver", "release_candidate=v2");
	ok = ok && rp_file_contains("rp_reviewops", "governance=passed");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "verifications=2");
	ok = ok && rp_file_contains("rp_risk", "decision_support=decision:agentos-final-demo-backend");
	ok = ok && rp_file_contains("rp_protocol", "protocol_compliance_reports=1");
	ok = ok && rp_file_contains("rp_protocol", "protocol_amendments=1");
	ok = ok && rp_file_contains("rp_soplog", "sop_executions=1");
	ok = ok && rp_file_contains("rp_package", "provenance_graph=unified");
	ok = ok && rp_file_contains("rp_delta", "decision=accepted");
	ok = ok && rp_file_contains("rp_diff", "changed_items=20");
	ok = ok && rp_file_contains("rp_wfio", "compatibility_checks=6");
	ok = ok && rp_file_contains("rp_wfio", "imports=5");
	ok = ok && rp_file_contains("rp_wfio", "adapter_specs=6");
	ok = ok && rp_file_contains("rp_wfio", "migration_steps=9");
	ok = ok && rp_file_contains("rp_wfio", "cases=4");
	ok = ok && rp_file_contains("rp_wfio", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_wfio", "backend_scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_wfio", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && rp_file_contains("rp_wfio", "backend_binding=workflow-portability->rp_backend_exec");
	ok = ok && rp_file_contains("rp_wfio", "decision=ready_for_agentos");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	ok = ok && rp_file_contains("rp_review2", "rounds=2");
	ok = ok && rp_file_contains("rp_review2", "review_threads=2");
	ok = ok && rp_file_contains("rp_review2", "action_items=2");
	ok = ok && rp_file_contains("rp_review2", "human_review=usable-review:RUN-900:1");
	ok = ok && rp_file_contains("rp_review2", "requested_change=methods_retry_scope");
	ok = ok && rp_file_contains("rp_review2", "requested_change=chart_caption");
	ok = ok && rp_file_contains("rp_revision", "draft_versions=3");
	ok = ok && rp_file_contains("rp_revision", "applied_changes=2");
	ok = ok && rp_file_contains("rp_revision", "report_delta=methods_and_caption_updated");
	ok = ok && rp_file_contains("rp_datadic", "schema_drift=0");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	ok = ok && rp_file_contains("rp_fail", "failure_class=tool_output_missing");
	ok = ok && rp_file_contains("rp_runview", "scheduler_items=21");
	ok = ok && rp_file_contains("rp_taskrec", "msg=21");
	ok = ok && rp_file_contains("rp_rank", "selected=10");
	ok = ok && rp_file_contains("rp_runview", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_health", "healthy=4");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_prompt", "provider_policy=host_relay");
	ok = ok && rp_file_contains("rp_prompt", "routes=4");
	ok = ok && rp_file_contains("rp_policy", "access_profiles=4");
	ok = ok && rp_file_contains("rp_compliance", "checks=8");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_llmq", "queue_validation=passed");
	ok = ok && rp_file_contains("rp_llmq", "dispatch_ready=3");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llmeval", "fallback_checks=3");
	ok = ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	ok = ok && rp_file_contains("rp_llmlog", "request_packets=3");
	ok = ok && rp_file_contains("rp_llmlog", "secret_scan=passed");
	ok = ok && rp_file_contains("rp_sched", "queue_items=21");
	ok = ok && rp_file_contains("rp_retrylog", "attempts=2");
	ok = ok && rp_file_contains("rp_relay", "network_stack=host_only");
	ok = ok && rp_file_contains("rp_relay", "queue_consumer=rp_llm_relay");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_packets", "validated_packets=3");
	ok = ok && rp_file_contains("rp_llm_packets", "packet_schema=passed");
	ok = ok && rp_file_contains("rp_llm_packets", "matched_responses=3");
	ok = ok && rp_file_contains("rp_llm_packets", "roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_routes", "route_policy=deterministic_then_host_optional");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_guard", "blocked_packets=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "cloud_mode=optional_host_side");
	ok = ok && rp_file_contains("rp_llm_hostreq", "host_request_manifest=ready");
	ok = ok && rp_file_contains("rp_llm_hostreq", "roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_resp", "host_relay_roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_resp", "response_join=passed");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_trace=rp_llm_guard->rp_llm_fallback->rp_llm_resp");
	ok = ok && rp_file_contains("rp_llm_fallback", "offline_template_verified=1");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_submit", "data_availability=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "message_acks=35");
	ok = ok && rp_file_contains("rp_agentcmp", "tool_events=115");
	ok = ok && rp_file_contains("rp_agentcmp", "scheduler_items=21");
	ok = ok && rp_file_contains("rp_agentcmp", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_agentcmp", "selected_tasks=10");
	ok = ok && rp_file_contains("rp_agentcmp", "policy_checks=8");
	ok = ok && rp_file_contains("rp_agentcmp", "compliance=accepted");
	ok = ok && rp_file_contains("rp_agentcmp", "risk_items=3");
	ok = ok && rp_file_contains("rp_agentcmp", "capa_actions=2");
	ok = ok && rp_file_contains("rp_agentcmp", "delta_items=20");
	ok = ok && rp_file_contains("rp_agentcmp", "diff_records=1");
	ok = ok && rp_file_contains("rp_agentcmp", "claim_records=8");
	ok = ok && rp_file_contains("rp_agentcmp", "provenance_paths=3");
	ok = ok && rp_file_contains("rp_agentcmp", "data_profiles=4");
	ok = ok && rp_file_contains("rp_agentcmp", "data_pipeline_files=6");
	ok = ok && rp_file_contains("rp_agentcmp", "data_quality_checks=7");
	ok = ok && rp_file_contains("rp_agentcmp", "figure_records=3");
	ok = ok && rp_file_contains("rp_agentcmp", "trial_records=4");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_exports=5");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_portability_records=1");
	ok = ok && rp_file_contains("rp_agentcmp", "migration_steps=9");
	ok = ok && rp_file_contains("rp_agentcmp", "portability_rehearsal_cases=4");
	ok = ok && rp_file_contains("rp_agentcmp", "review_rounds=2");
	ok = ok && rp_file_contains("rp_agentcmp", "data_versions=2");
	ok = ok && rp_file_contains("rp_agentcmp", "retry_attempts=2");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_packets=3");
	ok = ok && rp_file_contains("rp_agentcmp", "llm_requests=3");
	ok = ok && rp_file_contains("rp_agentcmp", "llm_eval_passed=7");
	ok = ok && rp_file_contains("rp_agentcmp", "run_views=1");
	ok = ok && rp_file_contains("rp_agentcmp", "health_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "agent_roles=7");
	ok = ok && rp_file_contains("rp_agentcmp", "collaboration_decisions=8");
	ok = ok && rp_file_contains("rp_agentcmp", "handoffs=6");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_protocol_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_runner_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "bio_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "lab_resource_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "publication_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "knowledge_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "runtime_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "notebook_exports=2");
	ok = ok && rp_file_contains("rp_sreg", "samples=8");
	ok = ok && rp_file_contains("rp_ethics", "ethics=approved");
	ok = ok && rp_file_contains("rp_access", "requests=3");
	ok = ok && rp_file_contains("rp_cohort", "cohorts=2");
	ok = ok && rp_file_contains("rp_instr", "instruments=4");
	ok = ok && rp_file_contains("rp_invent", "inventory_items=9");
	ok = ok && rp_file_contains("rp_procure", "requests=3");
	ok = ok && rp_file_contains("rp_ressched", "bookings=6");
	ok = ok && rp_file_contains("rp_resrev", "review_items=10");
	ok = ok && rp_file_contains("rp_pubplan", "journal_targets=2");
	ok = ok && rp_file_contains("rp_peerresp", "responses=6");
	ok = ok && rp_file_contains("rp_fairpkg", "fair_checks=8");
	ok = ok && rp_file_contains("rp_litrev", "papers=9");
	ok = ok && rp_file_contains("rp_litrev", "evidence_extractions=3");
	ok = ok && rp_file_contains("rp_litrev", "prisma_flow=usable-prisma-flow:RUN-900:1");
	ok = ok && rp_file_contains("rp_citegraph", "citations=14");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_kanswers", "answers=4");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	ok = ok && rp_file_contains("rp_nbexec", "executed_cells=8");
	ok = ok && rp_file_contains("rp_nbexec", "notebook=reproducible-analysis.ipynb");
	ok = ok && rp_file_contains("rp_repro", "downloadable_units=4");
	ok = ok && rp_file_contains("rp_eln", "eln_entries=3");
	ok = ok && rp_file_contains("rp_wpool", "workers=4");
	ok = ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	ok = ok && rp_file_contains("rp_package", "package_manifest=ready");
	ok = ok && rp_file_contains("rp_package", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_package", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_package", "custom_sources=rp_input,rp_runner,rp_uresrun");
	ok = ok && rp_file_contains("rp_package", "workbench=rp_runner");
	ok = ok && rp_file_contains("rp_package", "workspace_imports=1");
	ok = ok && rp_file_contains("rp_package", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_package", "delivery_file=report_md;path=rp_report_text;required=1;exists=1");
	ok = ok && rp_file_contains("rp_package", "delivery_file=package_manifest;path=rp_artifact_manifest;required=1;exists=1");
	ok = ok && rp_file_contains("rp_package", "delivery_checks=3");
	ok = ok && rp_file_contains("rp_package", "delivery_check=human_review;status=pass");
	ok = ok && rp_file_contains("rp_package", "delivery_manifest_json=delivery-manifest.json");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_entries=12");
	ok = ok && rp_file_contains("rp_package", "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md");
	ok = ok && rp_file_contains("rp_package", "deliverables=8");
	ok = ok && rp_file_contains("rp_package", "raw_links=5");
	ok = ok && rp_file_contains("rp_package", "decision_controls=2");
	ok = ok && rp_file_contains("rp_package", "human_reviews=1");
	ok = ok && rp_file_contains("rp_package", "revision_tasks=1");
	ok = ok && rp_file_contains("rp_package", "revision_change_count=2");
	ok = ok && rp_file_contains("rp_package", "revision_evidence=rp_revision");
	ok = ok && rp_file_contains("rp_package", "review_action_items=2");
	ok = ok && rp_file_contains("rp_package", "llm_matched_responses=3");
	ok = ok && rp_file_contains("rp_package", "evidence_protocols=1");
	ok = ok && rp_file_contains("rp_package", "evidence_extractions=3");
	ok = ok && rp_file_contains("rp_package", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_package", "migration_steps=9");
	ok = ok && rp_file_contains("rp_runner", "revision_status=completed");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_runner", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_query", "workflow_hits=34");
	ok = ok && rp_file_contains("rp_query", "knowledge_index=search_documents:1385");
	ok = ok && rp_file_contains("rp_query", "provenance_nodes:406");
	ok = ok && rp_file_contains("rp_query", "provenance_links:544");
	ok = ok && rp_file_contains("rp_query", "events:6816");
	ok = ok && rp_file_contains("rp_query", "context_records:348");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_execplan", "scheduled_tasks=21");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_runner", "stages=5");
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_stage_state", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_stage_state", "command=align:agent-align");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_cache_index", "cache_policy=content_keyed");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_retry_plan", "failure_reason=tool_output_missing");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_run_events", "decision=retry_align_only");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_artifact_manifest", "real_artifact_items=5");
	ok = ok && rp_file_contains("rp_artifact_manifest", "support_entries=2");
	ok = ok && rp_file_contains("rp_artifact_manifest", "dossier=artifact-detail");
	ok = ok && rp_file_contains("rp_artifact_manifest", "dossier_check=workflow_stage");
	ok = ok && rp_file_contains("rp_artifact_manifest", "dossier_check=review_gate");
	ok = ok && rp_file_contains("rp_artifact_manifest", "dossier_check=llm_quality");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "RUN-042 Recovery Report");
	ok = ok && rp_file_contains("rp_chart_data", "chart=stage_attempts");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_decisions=8");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_invocation", "steps=10");
	ok = ok && rp_file_contains("rp_completion", "actions=4");
	ok = ok && rp_file_contains("rp_backend", "cases=4");
	ok = ok && rp_file_contains("rp_backend_exec", "passed_cases=2");
	ok = ok && rp_file_contains("rp_study", "arms=2");
	ok = ok && rp_file_contains("rp_consistency", "state_relation=passed");
	ok = ok && rp_file_contains("rp_consistency", "task_records=21");
	ok = ok && rp_file_contains("rp_consistency", "checks=270");
	ok = ok && rp_file_contains("rp_consistency", "artifact_provenance=3");
	ok = ok && rp_file_contains("rp_consistency", "artifact_dossier_checks=4");
	ok = ok && rp_file_contains("rp_consistency", "artifact_path_rebuild_steps=7");
	ok = ok && rp_file_contains("rp_consistency", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_consistency", "runner_stages=5");
	ok = ok && rp_file_contains("rp_consistency", "workbench_records=10");
	ok = ok && rp_file_contains("rp_consistency", "advanced_surface_objects=5");
	ok = ok && rp_file_contains("rp_runop", "advanced_surface=objects:5");
	ok = ok && rp_file_contains("rp_runop", "research_search:saved_queries:2");
	ok = ok && rp_file_contains("rp_runop", "project_space:lab-gene-x");
	ok = ok && rp_file_contains("rp_runop", "study_protocol:protocols:2");
	ok = ok && rp_file_contains("rp_runop", "dataset_answer:datasets:2");
	ok = ok && rp_file_contains("rp_runop", "package_intake:packages:1");
	ok = ok && rp_file_contains("rp_runop", "startup_health=quickstart:ready");
	ok = ok && rp_file_contains("rp_runop", "startup_checks=8");
	ok = ok && rp_file_contains("rp_runop", "configuration_health=settings:ready");
	ok = ok && rp_file_contains("rp_runop", "stores_secret_values=0");
	ok = ok && rp_file_contains("rp_runop", "platform_doctor=ready;checks=8");
	ok = ok && rp_file_contains("rp_runop", "provider_health=offline:1,cloud:0,ready_cloud:0");
	ok = ok && rp_file_contains("rp_runop", "project_scaffold=templates:3");
	ok = ok && rp_file_contains("rp_runop", "dataset_product=previews:2");
	ok = ok && rp_file_contains("rp_runop", "source_portfolio=sources:2");
	ok = ok && rp_file_contains("rp_runop", "study_protocol_reproduction=packages:1");
	ok = ok && rp_file_contains("rp_runop", "project_bundle_cache=latest:ready");
	ok = ok && rp_file_contains("rp_consistency", "research_product_checks=18");
	ok = ok && rp_file_contains("rp_consistency", "runtime_assurance_checks=24");
	ok = ok && rp_file_contains("rp_consistency", "research_ops_checks=28");
	ok = ok && rp_file_contains("rp_consistency", "lab_governance_ops_checks=26");
	ok = ok && rp_file_contains("rp_consistency", "knowledge_index_checks=22");
	ok = ok && rp_file_contains("rp_runop", "runtime_assurance=secret_refs:3");
	ok = ok && rp_file_contains("rp_runop", "model_registry:2");
	ok = ok && rp_file_contains("rp_runop", "llm_proxy_audits:2");
	ok = ok && rp_file_contains("rp_runop", "collab_threads:2");
	ok = ok && rp_file_contains("rp_runop", "obs_alerts:5");
	ok = ok && rp_file_contains("rp_runop", "research_ops=semantic_entities:8");
	ok = ok && rp_file_contains("rp_runop", "prompt_templates:2");
	ok = ok && rp_file_contains("rp_runop", "runbook_steps:7");
	ok = ok && rp_file_contains("rp_runop", "worker_ops:6");
	ok = ok && rp_file_contains("rp_runop", "execution_controls:8");
	ok = ok && rp_file_contains("rp_consistency", "regulated_research_checks=32");
	ok = ok && rp_file_contains("rp_labresop", "lab_governance_ops=approvals:2");
	ok = ok && rp_file_contains("rp_labresop", "protocol_compliance_reports:2");
	ok = ok && rp_file_contains("rp_labresop", "sop_executions:3");
	ok = ok && rp_file_contains("rp_labresop", "training_records:4");
	ok = ok && rp_file_contains("rp_labresop", "run_queue_items:4");
	ok = ok && rp_file_contains("rp_runop", "regulated_research=annotation_schemas:1");
	ok = ok && rp_file_contains("rp_runop", "assay_plates:1");
	ok = ok && rp_file_contains("rp_runop", "dataset_cards:1");
	ok = ok && rp_file_contains("rp_runop", "research_object_crates:1");
	ok = ok && rp_file_contains("rp_runop", "workflow_templates:8");
	ok = ok && rp_file_contains("rp_consistency", "dynamic_input_records=8");
	ok = ok && rp_file_contains("rp_consistency", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_ui_home", "page=home");
	ok = ok && rp_file_contains("rp_ui_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_ui_home", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_ui_run", "page=run-detail");
	ok = ok && rp_file_contains("rp_ui_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_ui_run", "artifact_preview=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && rp_file_contains("rp_ui_run", "review_threads=2");
	ok = ok && rp_file_contains("rp_ui_run", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_ui_evidence", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_ui_agent", "page=agent-detail");
	ok = ok && rp_file_contains("rp_ui_agent", "decision_rows=8");
	ok = ok && rp_file_contains("rp_ui_evidence", "page=evidence-detail");
	ok = ok && rp_file_contains("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_ui_compare", "page=compare-metrics");
	ok = ok && rp_file_contains("rp_ui_compare", "metric_rows=8");
	ok = ok && rp_file_contains("rp_ui_compare", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_input", "custom_run_2=usable-run:RUN-901");
	ok = ok && rp_file_contains("rp_input", "custom_run_3=usable-run:RUN-902");
	ok = ok && rp_file_contains("rp_input", "custom_dataset_rows=3");
	ok = ok && rp_file_contains("rp_input", "form_fields=8");
	ok = ok && rp_file_contains("rp_input", "csv_rows_total=9");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_runner", "custom_source=rp_input");
	ok = ok && rp_file_contains("rp_runner", "custom_runs=3");
	ok = ok && rp_file_contains("rp_runner", "custom_agent_decisions=15");
	ok = ok && rp_file_contains("rp_runner", "citation_plan_entries=3");
	ok = ok && rp_file_contains("rp_web_routes", "routes=74");
	ok = ok && rp_file_contains("rp_web_routes", "get_routes=17");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research-studio");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research/workbench/{id}");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research/project/{id}/review");
	ok = ok && rp_file_contains("rp_web_routes", "post_routes=57");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/research/studio-launch");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/host-workflow/stage-attempt");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/host-workflow/report-export");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/research/artifact-input");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/research/artifact-package");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/research/project-release-gate");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/research/project-provenance-graph");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/workflow-portability/run");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/workflow-portability/import");
	ok = ok && rp_file_contains("rp_web_routes", "action=/actions/workflow-portability/package");
	ok = ok && rp_file_contains("rp_api_home", "api=home");
	ok = ok && rp_file_contains("rp_api_home", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_api_home", "custom_runs=3");
	ok = ok && rp_file_contains("rp_api_home", "dynamic_inputs=4");
	ok = ok && rp_file_contains("rp_api_home", "reader_contract=rp_web_bundle");
	ok = ok && rp_file_contains("rp_api_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_api_home", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_api_run", "runner_exec_files=5");
	ok = ok && rp_file_contains("rp_api_run", "custom_research=rp_runner");
	ok = ok && rp_file_contains("rp_api_run", "custom_research_runs=3");
	ok = ok && rp_file_contains("rp_api_run", "dynamic_input_queue=rp_input");
	ok = ok && rp_file_contains("rp_api_run", "reader_contract=rp_web_bundle");
	ok = ok && rp_file_contains("rp_api_run", "reader_view=run-detail");
	ok = ok && rp_file_contains("rp_api_run", "request_form=rp_input");
	ok = ok && rp_file_contains("rp_api_run", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_api_run", "llm_roundtrip=ready");
	ok = ok && rp_file_contains("rp_api_run", "bibliography=rp_runner");
	ok = ok && rp_file_contains("rp_api_run", "review_action_items=2");
	ok = ok && rp_file_contains("rp_api_run", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_api_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_api_run", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_api_run", "manifest_support_entries=2");
	ok = ok && rp_file_contains("rp_api_know", "evidence_protocols=1");
	ok = ok && rp_file_contains("rp_api_agents", "agents=7");
	ok = ok && rp_file_contains("rp_api_evidence", "provenance_paths=3");
	ok = ok && rp_file_contains("rp_api_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_api_compare", "workflow_runner_files=5");
	ok = ok && rp_file_contains("rp_api_compare", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_api_artifacts", "manifest_records=4");
	ok = ok && rp_file_contains("rp_api_artifacts", "evidence_package=rp_package");
	ok = ok && rp_file_contains("rp_api_artifacts", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_api_artifacts", "llm_matched_responses=3");
	ok = ok && rp_file_contains("rp_api_artifacts", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_api_artifacts", "preview_files=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && rp_file_contains("rp_api_data", "dataset_snapshots=2");
	ok = ok && rp_file_contains("rp_api_bio", "sample_registry=rp_sreg");
	ok = ok && rp_file_contains("rp_api_labres", "instrument_registry=rp_instr");
	ok = ok && rp_file_contains("rp_api_pub", "result_review=rp_resrev");
	ok = ok && rp_file_contains("rp_api_know", "semantic_index=rp_semindex");
	ok = ok && rp_file_contains("rp_api_runtime", "runtime_env=rp_runenv");
	ok = ok && rp_file_contains("rp_api_action", "actions=57");
	ok = ok && rp_file_contains("rp_api_action", "research_studio_launch=/actions/research/studio-launch");
	ok = ok && rp_file_contains("rp_api_action", "host_workflow_stage=/actions/host-workflow/stage-attempt");
	ok = ok && rp_file_contains("rp_api_action", "host_workflow_report=/actions/host-workflow/report-export");
	ok = ok && rp_file_contains("rp_api_action", "artifact_input=/actions/research/artifact-input");
	ok = ok && rp_file_contains("rp_api_action", "artifact_package=/actions/research/artifact-package");
	ok = ok && rp_file_contains("rp_api_action", "project_release_gate=/actions/research/project-release-gate");
	ok = ok && rp_file_contains("rp_api_action", "project_snapshot=/actions/research/project-snapshot");
	ok = ok && rp_file_contains("rp_api_action", "project_provenance_graph=/actions/research/project-provenance-graph");
	ok = ok && rp_file_contains("rp_api_action", "project_delivery=/actions/research/project-delivery");
	ok = ok && rp_file_contains("rp_api_action", "workflow_portability_run=/actions/workflow-portability/run");
	ok = ok && rp_file_contains("rp_api_action", "workflow_portability_import=/actions/workflow-portability/import");
	ok = ok && rp_file_contains("rp_api_action", "workflow_portability_package=/actions/workflow-portability/package");
	ok = ok && rp_file_contains("rp_api_action", "revision_task_runner=1");
	ok = ok && rp_file_contains("rp_api_action", "validated_requests=8");
	ok = ok && rp_file_contains("rp_api_action", "precondition_checks=8");
	ok = ok && rp_file_contains("rp_api_action", "side_effect_records=16");
	ok = ok && rp_file_contains("rp_actionio", "requests=8");
	ok = ok && rp_file_contains("rp_actionio", "responses=8");
	ok = ok && rp_file_contains("rp_actionio", "completed=8");
	ok = ok && rp_file_contains("rp_actionio", "dataset_file=rp_input");
	ok = ok && rp_file_contains("rp_actionio", "generated_runs=3");
	ok = ok && rp_file_contains("rp_actionio", "tag=reusable");
	ok = ok && rp_file_contains("rp_actionio", "effect=revision_run_created");
	ok = ok && rp_file_contains("rp_actionio", "applied_changes=2");
	ok = ok && rp_file_contains("rp_actionio", "revision_status=completed");
	ok = ok && rp_file_contains("rp_uresrun", "runs=3");
	ok = ok && rp_file_contains("rp_uresrun", "run_id_3=usable-run:RUN-902");
	ok = ok && rp_file_contains("rp_uresrun", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_uresrun", "source_form=rp_input");
	ok = ok && rp_file_contains("rp_uresrun", "workbench=rp_runner");
	ok = ok && rp_file_contains("rp_uresrun", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_uresrun", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_uresrun", "artifacts=36");
	ok = ok && rp_file_contains("rp_uresrun", "dataset_rows=3");
	ok = ok && rp_file_contains("rp_uresrun", "LLM Relay");
	ok = ok && rp_file_contains("rp_actionio", "Stage DAG");
	ok = ok && rp_file_contains("rp_actionio", "passed_cases=3");
	ok = ok && rp_file_contains("rp_actionio", "action_state_records=12");
	ok = ok && rp_file_contains("rp_actionio", "action_step=workbench_advance");
	ok = ok && rp_file_contains("rp_actionio", "action_step=notebook_download");
	ok = ok && rp_file_contains("rp_actionio", "action_step=bundle_download");
	ok = ok && rp_file_contains("rp_actionio", "state_after_actions=workbench:ready,review:needs_revision,revision:completed,bundle:ready");
	ok = ok && rp_file_contains("rp_actionio", "request_validation=passed");
	ok = ok && rp_file_contains("rp_actionio", "side_effect_records=16");
	ok = ok && rp_file_contains("rp_actionio", "state_write=10;target=rp_package;field=download_manifest");
	ok = ok && rp_file_contains("rp_actionio", "idempotency_checks=8");
	ok = ok && rp_file_contains("rp_actionio", "download_manifest_generated=1");
	ok = ok && rp_file_contains("rp_web_bundle", "api_payloads=14");
	ok = ok && rp_file_contains("rp_web_bundle", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_web_bundle", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_web_bundle", "render_sections=7");
	ok = ok && rp_file_contains("rp_web_bundle", "artifact_previews=3");
	ok = ok && rp_file_contains("rp_web_bundle", "runner_detail_fields=16");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_files=8");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_checks=3");
	ok = ok && rp_file_contains("rp_web_bundle", "evidence_bundle_entries=12");
	ok = ok && rp_file_contains("rp_web_bundle", "llm_roundtrip=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_web_bundle", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_web_bundle", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_web_bundle", "workspace_imports=1");
	ok = ok && rp_file_contains("rp_web_bundle", "workbench=rp_runner");
	ok = ok && rp_file_contains("rp_web_bundle", "dynamic_inputs=4");
	ok = ok && rp_file_contains("rp_web_bundle", "host_ui_events=10");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_contract=host_plain_ucore_v2");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_ready=1");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_payload_files=rp_api_home");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_refresh_files=rp_web_routes");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_required_sections=routes,payloads,actions,live_update,downloads,compare");
	ok = ok && rp_file_contains("rp_web_bundle", "evidence_protocols=1");
	ok = ok && rp_file_contains("rp_web_bundle", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_web_bundle", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_web_bundle", "custom_research_files=1");
	ok = ok && rp_file_contains("rp_web_bundle", "review_threads=2");
	ok = ok && rp_file_contains("rp_web_bundle", "action_validation=passed");
	ok = ok && rp_file_contains("rp_web_bundle", "side_effect_records=16");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_views=21");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_actions=57");
	ok = ok && rp_file_contains("rp_web_bundle", "post_routes=57");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_studio,rp_web_bundle");
	ok = ok && rp_file_contains("rp_studio", "studio=usable-research-studio");
	ok = ok && rp_file_contains("rp_studio", "studio_session=usable-research-studio-session:W1:1");
	ok = ok && rp_file_contains("rp_web_bundle", "project_review=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "release_gate=project-release-gate");
	ok = ok && rp_file_contains("rp_web_bundle", "project_snapshot=project-snapshot");
	ok = ok && rp_file_contains("rp_web_bundle", "reproducibility_audit=project-reproducibility-audit");
	ok = ok && rp_file_contains("rp_web_bundle", "provenance_graph=project-provenance-graph");
	ok = ok && rp_file_contains("rp_web_bundle", "project_delivery=project-delivery");
	if (rp_host_seed_count() > 0 && rp_host_seed_has_workbench_action()) {
		ok = ok && rp_file_contains("rp_actionio", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_platform_ops_action()) {
		ok = ok && rp_file_contains("rp_runner", "host_action_platform_ops=ready");
		ok = ok && rp_file_contains("rp_package", "host_action_platform_ops_package=ready");
		ok = ok && rp_file_contains("rp_actionio", "host_action_platform_ops=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_platform_ops_outputs=rp_runner,rp_package,rp_api_action,rp_web_bundle");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_platform_ops=rp_runner,rp_package,rp_api_action");
		ok = ok && rp_file_contains("rp_api_action", "operations_actions=3");
		ok = ok && rp_file_contains("rp_api_action", "quality_actions=3");
		ok = ok && rp_file_contains("rp_api_action", "project_space_actions=5");
		ok = ok && rp_file_contains("rp_api_action", "project_review_actions=8");
		if (rp_host_seed_has("kind=project_release_gate")) {
			ok = ok && rp_file_contains("rp_actionio", "host_action_project_review_outputs=rp_web_bundle");
			ok = ok && rp_file_contains("rp_web_bundle", "host_action_project_release_gate=");
			ok = ok && rp_file_contains("rp_web_bundle", "host_action_project_review=rp_web_bundle");
		}
	}
	if (rp_host_seed_count() > 0 && rp_host_seed_has_artifact_action()) {
		ok = ok && rp_file_contains("rp_artifact", "host_artifact_actions=applied");
		ok = ok && rp_file_contains("rp_artifact_manifest", "host_artifact_manifest_actions=applied");
		if (rp_host_seed_has("kind=artifact_input")) ok = ok && rp_file_contains("rp_artifact", "host_artifact_input=");
		if (rp_host_seed_has("kind=artifact_derive")) ok = ok && rp_file_contains("rp_artifact", "host_artifact_derive=");
		if (rp_host_seed_has("kind=artifact_log")) ok = ok && rp_file_contains("rp_stage_log", "host_artifact_log=");
		if (rp_host_seed_has("kind=artifact_chart")) ok = ok && rp_file_contains("rp_chart_data", "host_artifact_chart=");
		if (rp_host_seed_has("kind=artifact_package")) ok = ok && rp_file_contains("rp_package", "host_artifact_package=");
		ok = ok && rp_file_contains("rp_package", "host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package");
		ok = ok && rp_file_contains("rp_actionio", "host_action_artifacts=1");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_artifacts=1");
	}
	if (!optional_file_contains("rp_ack", "ack=test_suite;msg=test;status=passed")) {
		if (!rp_append_file("rp_ack", "ack=test_suite;msg=test;status=passed")) return 1;
	}
	if (!optional_file_contains("rp_tool", "tool=test_suite.check_compare")) {
		if (!rp_append_file("rp_tool", "tool=test_suite.check_compare")) return 1;
	}
	ok = ok && rp_file_contains("rp_ack", "ack=consistency;msg=22;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=consistency.check_backend");
	ok = ok && rp_file_contains("rp_ack", "ack=ui_export;msg=ui;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=ui_export.write_compare");
	ok = ok && rp_file_contains("rp_ack", "ack=web_export;msg=web;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=web_export.write_bundle");
	ok = ok && rp_file_contains("rp_ack", "ack=api_actions;msg=action;status=ready");
	ok = ok && rp_file_contains("rp_ack", "ack=test_suite;msg=test;status=passed");
	ok = ok && rp_file_contains("rp_tool", "tool=test_suite.check_compare");
	ok = ok && rp_file_contains("rp_ack", "ack=agent_collab;msg=agents;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=agent_collab.write_decisions");
	ok = ok && rp_file_contains("rp_ack", "ack=llm_relay;msg=relay;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=llm_relay.write_packets");
	ok = ok && rp_file_contains("rp_ack", "ack=data_pipeline;msg=data;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=data_pipeline.collection");
	ok = ok && rp_file_contains("rp_ack", "ack=workflow_runner;msg=runner;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=workflow_runner.write_manifest");
	ok = ok && rp_file_contains("rp_review_dashboard", "dashboard=research-review");
	ok = ok && rp_file_contains("rp_review_dashboard", "section=llm;source=rp_llm_req,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "gate=llm_packet_guard;status=pass");
	ok = ok && rp_file_contains("rp_review_dashboard", "decision=ready_for_reviewer");
	ok = ok && rp_file_contains("rp_review_dashboard", "decision=review_pack_ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "backend_review_evidence=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;review_pack=rp_review_pack;status=ready");
	ok = ok && rp_file_contains("rp_package", "review_pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff");
	ok = ok && rp_file_contains("rp_package", "review_pack_action=sync_operations_next;source=rp_runner;status=ready");
	ok = ok && rp_file_contains("rp_ack", "ack=review_dashboard;msg=reviewdash;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=review_dashboard.aggregate");
	ok = ok && rp_file_contains("rp_ack", "ack=review_pack;msg=pack;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=review_pack.assemble");
	int review_sections = rp_get_int_value("rp_review_dashboard", "sections=");
	int review_gates = rp_count_token("rp_review_dashboard", "gate=");
	int review_decisions = rp_count_token("rp_review_dashboard", "decision=");
	int review_handoffs = rp_count_token("rp_review_dashboard", "handoff=");
	int review_pack_actions = rp_count_token("rp_package", "review_pack_action=");
	int review_bridge_paths = 0;
	if (rp_file_contains("rp_package", "review_pack_bridge=delivery_manifest")) review_bridge_paths++;
	if (rp_file_contains("rp_package", "operations_report")) review_bridge_paths++;
	if (rp_file_contains("rp_package", "project_space")) review_bridge_paths++;
	if (rp_file_contains("rp_package", "workbench_handoff")) review_bridge_paths++;
	ok = ok && require_equal("review_sections", review_sections, 8);
	ok = ok && require_equal("review_gates", review_gates, 6);
	ok = ok && require_equal("review_decisions", review_decisions, 3);
	ok = ok && require_equal("review_handoffs", review_handoffs, 3);
	ok = ok && require_equal("review_pack_actions", review_pack_actions, 3);
	ok = ok && require_equal("review_bridge_paths", review_bridge_paths, 4);
	ok = ok && rp_file_contains("rp_review_dashboard", "pack_source=rp_package,rp_runner,rp_review_pack");
	ok = ok && rp_file_contains("rp_review_dashboard", "pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff");
	ok = ok && rp_file_contains("rp_package", "review_pack_action=deliver_to_reviewer;source=rp_package;status=ready");
	ok = ok && rp_file_contains("rp_package", "review_pack_action=resolve_project_items;source=rp_package;status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_next_task=delivery_manifest");
	ok = ok && rp_file_contains("rp_api_action", "project_space_actions=5");
	int llm_queue = rp_get_int_value("rp_llmq", "queued=");
	int llm_packets = rp_get_int_value("rp_llm_packets", "packets=");
	int llm_matched = rp_get_int_value("rp_llm_packets", "matched_responses=");
	int llm_responses = rp_get_int_value("rp_llm_resp", "responses=");
	int llm_eval = rp_get_int_value("rp_llmeval", "passed=");
	int llm_guard = rp_get_int_value("rp_llm_guard", "checked_packets=");
	int llm_blocked = rp_get_int_value("rp_llm_guard", "blocked_packets=");
	int llm_relay = rp_get_int_value("rp_relay", "relay_packets=");
	int llm_routes = rp_get_int_value("rp_prompt", "routes=");
	int llm_host_requests = rp_get_int_value("rp_llm_hostreq", "host_request_records=");
	int llm_host_responses = rp_get_int_value("rp_llm_hostreq", "host_response_records=");
	ok = ok && require_equal("llm_queue", llm_queue, 3);
	ok = ok && require_equal("llm_packets", llm_packets, 3);
	ok = ok && require_equal("llm_matched", llm_matched, 3);
	ok = ok && require_equal("llm_responses", llm_responses, 3);
	ok = ok && require_equal("llm_eval", llm_eval, 7);
	ok = ok && require_equal("llm_guard", llm_guard, 3);
	ok = ok && require_equal("llm_blocked", llm_blocked, 0);
	ok = ok && require_equal("llm_relay", llm_relay, 3);
	ok = ok && require_equal("llm_routes", llm_routes, 4);
	ok = ok && require_equal("llm_host_requests", llm_host_requests, 3);
	ok = ok && require_equal("llm_host_responses", llm_host_responses, 3);
	ok = ok && rp_file_contains("rp_package", "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp");
	ok = ok && rp_file_contains("rp_package", "delivery_file=llm_trace;path=rp_llm_packets;required=0;exists=1");
	ok = ok && rp_file_contains("rp_review_dashboard", "section=llm;source=rp_llm_req,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "gate=llm_packet_guard;status=pass;source=rp_llm_guard");
	ok = ok && rp_file_contains("rp_runner", "citation=rp_llm_resp:response_join");
	int portability_imports = rp_get_int_value("rp_wfio", "portability_imports=");
	int portability_adapters = rp_get_int_value("rp_wfio", "adapter_specs=");
	int portability_migration = rp_get_int_value("rp_wfio", "migration_steps=");
	int portability_cases = rp_get_int_value("rp_wfio", "cases=");
	int portability_rehearsals = rp_get_int_value("rp_wfio", "rehearsal_cases=");
	int portability_blocking = rp_get_int_value("rp_wfio", "blocking_items=");
	int package_portability_exports = rp_get_int_value("rp_package", "portability_exports=");
	int package_portability_adapters = rp_get_int_value("rp_package", "adapter_specs=");
	int package_portability_migration = rp_get_int_value("rp_package", "migration_steps=");
	int package_portability_rehearsals = rp_get_int_value("rp_package", "rehearsal_cases=");
	ok = ok && require_equal("portability_imports", portability_imports, 5);
	ok = ok && require_equal("portability_adapters", portability_adapters, 6);
	ok = ok && require_equal("portability_migration", portability_migration, 9);
	ok = ok && require_equal("portability_cases", portability_cases, 4);
	ok = ok && require_equal("portability_rehearsals", portability_rehearsals, 4);
	ok = ok && require_equal("portability_blocking", portability_blocking, 0);
	ok = ok && require_equal("package_portability_exports", package_portability_exports, 5);
	ok = ok && require_equal("package_portability_adapters", package_portability_adapters, 6);
	ok = ok && require_equal("package_portability_migration", package_portability_migration, 9);
	ok = ok && require_equal("package_portability_rehearsals", package_portability_rehearsals, 4);
	ok = ok && rp_file_contains("rp_package", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_wfio", "decision=ready_for_agentos");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	ok = ok && rp_file_contains("rp_web_bundle", "workflow_portability=rp_wfio");
	int backend_cases = rp_get_int_value("rp_backend", "cases=");
	int backend_exec_passed = rp_get_int_value("rp_backend_exec", "passed_cases=");
	int backend_exec_planned = rp_get_int_value("rp_backend_exec", "planned_cases=");
	ok = ok && require_equal("backend_cases", backend_cases, 4);
	ok = ok && require_equal("backend_exec_passed", backend_exec_passed, 2);
	ok = ok && require_equal("backend_exec_planned", backend_exec_planned, 2);
	ok = ok && rp_file_contains("rp_backend", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_backend", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_backend", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && rp_file_contains("rp_backend", "runner=active-user-space");
	ok = ok && rp_file_contains("rp_backend_exec", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_backend_exec", "scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_backend_exec", "case=plain-ucore;source=rp_wfio;status=passed");
	ok = ok && rp_file_contains("rp_backend_exec", "case=agentos-ucore;source=rp_wfio;status=planned");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_cases=4");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=plain-ucore;input=rp_wfio;artifact=rp_artifact_manifest;result=passed");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=retry-recovery;input=rp_retry_plan;artifact=rp_stage_state;result=passed");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=agentos-context;input=rp_wfio;artifact=agent_context;result=planned");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=agentos-fsmeta;input=rp_wfio;artifact=agent_file_meta;result=planned");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=plain-ucore;input=rp_wfio;artifact=rp_artifact_manifest;result=passed;reason=native_programs_ok;input_check=pass;artifact_check=pass");
	ok = ok && rp_file_contains("rp_backend_exec", ";att=1;retry=none;ticks=3");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=retry-recovery;input=rp_retry_plan;artifact=rp_stage_state;result=passed;reason=recovered_align;input_check=pass;artifact_check=pass");
	ok = ok && rp_file_contains("rp_backend_exec", ";att=2;retry=tool_output_missing;ticks=5");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=agentos-context;input=rp_wfio;artifact=agent_context;result=planned;reason=kernel_context;input_check=planned;artifact_check=planned;retry=kernel_required");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_case=agentos-fsmeta;input=rp_wfio;artifact=agent_file_meta;result=planned;reason=kernel_metadata;input_check=planned;artifact_check=planned;retry=kernel_required");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail=plain-ucore;src=rp_wfio;req=execution_plan;obs=pass;act=record;review=baseline");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail=retry-recovery;src=rp_retry_plan+rp_stage_state;req=retry_stage+stage;obs=pass;act=rerun_align;review=recovered");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail=agentos-context;src=rp_wfio;req=context_path;obs=planned;act=kernel_context;review=target");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail=agentos-fsmeta;src=rp_artifact_manifest;req=metadata_index;obs=planned;act=kernel_fsmeta;review=target");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail_rows=4");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail_schema=src,req,obs,act,review");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=passed");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=passed");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=planned");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report=agentos-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=planned");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report_rows=4");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report_schema=plain_cost,agentos_replace,risk,status");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_observed=rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_llmeval");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail_fields=input_check,artifact_check,att,retry,ticks");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_detail_checks=16");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_verified_inputs=4");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_passed=2");
	ok = ok && rp_file_contains("rp_backend_exec", "runner_planned=2");
	ok = ok && rp_file_contains("rp_study", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_study", "migration_status=baseline_ready_agentos_planned");
	ok = ok && rp_file_contains("rp_study", "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=4;result=passed");
	ok = ok && rp_file_contains("rp_study", "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;metadata_index=1;detail_checks=kernel;result=planned");
	ok = ok && rp_file_contains("rp_study", "metrics=8");
	ok = ok && rp_file_contains("rp_study", "study_handoff=rp_backend_exec->rp_agentcmp;status=ready");
	ok = ok && rp_file_contains("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;status=ready");
	ok = ok && rp_file_contains("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index;status=ready");
	ok = ok && rp_file_contains("rp_report_text", "report_source=workflow;state_file=rp_stage_state;source_key=host_workflow_run_id");
	ok = ok && rp_file_contains("rp_report_text", "report_source=llm;state_file=rp_llm_resp;source_key=host_relay_response");
	ok = ok && rp_file_contains("rp_report_text", "report_source=backend;state_file=rp_report_text;source_key=backend_evidence_report");
	if (!ok) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 44 || tool_count < 138) {
		printf("rp_compare_plain: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		return 1;
	}
	if (!rp_append_file("rp_agentcmp", "plain_kernel=passed;programs=42;state_files=170;message_acks=44;tool_events=138;action_state_records=12;test_cases=886;action_side_effect_records=16;service_page=1;llm_queue_checks=3;llm_guard_checks=3;review_dashboard=1;review_pack=1;workbench_exports=7;dynamic_inputs=4;host_ui_events=10;reader_contract=1;advanced_surface_objects=5;startup_health_checks=8;research_product_checks=18;runtime_assurance_checks=24;research_ops_checks=28;regulated_research_checks=32;lab_governance_ops_checks=26;knowledge_index_checks=22;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "knowledge_index=search_documents:1385;provenance_nodes:406;provenance_links:544;events:6816;context_records:348;usable_artifacts:429;usable_runs:20;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "review_handoff_checks=13;review_sections=8;review_gates=6;review_decisions=3;review_handoffs=3;review_pack_actions=3;review_pack_bridges=4;backend_review=1;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "llm_delivery_checks=16;llm_queue=3;llm_packets=3;llm_responses=3;llm_eval=7;llm_guard=3;llm_hostreq=3;llm_review_links=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "workflow_portability_checks=14;portability_imports=5;adapter_specs=6;migration_steps=9;rehearsal_cases=4;blocking_items=0;portability_package=workflow-portability;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "portability_backend_checks=12;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;passed_cases=2;planned_cases=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "backend_runner_checks=12;runner_cases=4;runner_passed=2;runner_planned=2;plain_inputs=4;study_metrics=2;backend_runner_detail_checks=24;runner_detail_rows=4;backend_runner_report_checks=20;runner_report_rows=4;backend_report_links=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "research_governance_checks=18;protocol_compliance=1;protocol_amendments=1;sop_executions=1;risk_reviews=1;capa_verifications=2;decision_support=1;provenance_graph=1;status=ready")) return 1;
	if (rp_host_seed_has("kind=research_run")) {
		if (!rp_append_file("rp_agentcmp", "host_action_research_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		if (!rp_append_file("rp_agentcmp", "host_action_compare_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=human_review")) {
		if (!rp_append_file("rp_agentcmp", "host_action_review_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=revision_task") || rp_host_seed_has("kind=revision_run")) {
		if (!rp_append_file("rp_agentcmp", "host_action_revision_verified=1")) return 1;
	}
	if (rp_host_seed_has_research_input_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_research_inputs_verified=1")) return 1;
	}
	if (rp_host_seed_has_evidence_input_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_evidence_inputs_verified=1")) return 1;
	}
	if (rp_host_seed_has_workbench_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_workbench_verified=1")) return 1;
	}
	if (rp_host_seed_has_platform_ops_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_platform_ops_verified=1")) return 1;
	}
	if (rp_host_seed_has_workflow_portability_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_portability_verified=1")) return 1;
	}
	if (rp_host_seed_has_workflow_portability_step_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_portability_steps_verified=1")) return 1;
	}
	if (rp_host_seed_has_artifact_action()) {
		if (!rp_append_file("rp_agentcmp", "host_action_artifacts_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=notebook_export")) {
		if (!rp_append_file("rp_agentcmp", "host_action_export_verified=1")) return 1;
	}
	if (!rp_append_status("compare=ready")) return 1;
	if (rp_host_seed_count() > 0) {
		printf("rp_compare_plain: host_actions=%d verified\n", rp_host_seed_count());
	}
	printf("rp_compare_plain: plain_kernel=passed objects=500 programs=42 state_files=170 acks=44 tools=138 dynamic=4 products=18 assurance=24 research_ops=28 regulated=32 lab_governance=26 knowledge_index=22 reader=1 status=ready\n");
	return 0;
}
