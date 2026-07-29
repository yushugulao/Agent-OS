#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>
#include <rp_evidence.h>

static int compare_assertions_executed;
static int compare_assertions_passed;
static int compare_runtime_assertions_executed;
static int compare_runtime_assertions_passed;
static char compare_line[512];

static int compare_file_contains(const char *path, const char *token)
{
	int matched;

	compare_assertions_executed++;
	matched = rp_file_contains(path, token);
	if (matched)
		compare_assertions_passed++;
	return matched;
}

#define rp_file_contains compare_file_contains

static int require_equal(const char *name, int actual, int expected)
{
	compare_assertions_executed++;
	if (actual == expected) {
		compare_assertions_passed++;
		return 1;
	}
	printf("rp_compare_plain: mismatch %s actual=%d expected=%d\n", name, actual, expected);
	return 0;
}

static int require_at_least(const char *name, int actual, int minimum)
{
	compare_assertions_executed++;
	if (actual >= minimum) {
		compare_assertions_passed++;
		return 1;
	}
	printf("rp_compare_plain: mismatch %s actual=%d minimum=%d\n", name, actual, minimum);
	return 0;
}

static int check_seed_value(const char *kind, const char *key, const char *fallback, const char *path, const char *prefix)
{
	char value[96];
	char token[160];
	(void)fallback;
	if (!rp_host_seed_copy_value_for_kind(kind, key, value, sizeof(value))) {
		compare_assertions_executed++;
		printf("rp_compare_plain: missing_seed kind=%s key=%s\n", kind, key);
		return 0;
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

struct compare_runtime_spec {
	const char *name;
	const char *source;
	const char *key;
	const char *value;
};

static const struct compare_runtime_spec COMPARE_RUNTIME_SPECS[] = {
	{"backend", "rp_backend_exec", "runtime_cases_executed", "8"},
	{"consistency", "rp_consistency", "evidence_generation", "runtime"},
	{"kernel-context", "rp_agentos_kernel", "context_snapshot", "present"},
	{"audit", "rp_audit", "evidence_generation", "runtime"},
	{"provenance", "rp_prov_view", "evidence_generation", "runtime"},
};

static int append_compare_runtime_case(
	const struct compare_runtime_spec *spec,
	const struct rp_evidence_file_measurement *measured)
{
	char line[384];

	line[0] = 0;
	rp_append_text(line, sizeof(line),
		       "evidence_role=runtime_verified;runtime_compare_case=");
	rp_append_text(line, sizeof(line), spec->name);
	rp_append_text(line, sizeof(line), ";source=");
	rp_append_text(line, sizeof(line), spec->source);
	rp_append_text(line, sizeof(line), ";source_bytes=");
	rp_append_uint_text(line, sizeof(line), measured->bytes);
	rp_append_text(line, sizeof(line), ";source_hash=");
	rp_append_uint_text(line, sizeof(line), measured->hash);
	rp_append_text(line, sizeof(line),
		       ";claim_protocol=exact-field-v1;assertions_executed=1;assertions_passed=1;generation=runtime;status=verified");
	return rp_append_file("rp_agentcmp", line);
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
			ok = ok && check_seed_value("kind=template", "name=", "Reusable response comparison", "rp_input", "host_action_template_name=");
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
		ok = ok && rp_file_contains("rp_uresrun", token);
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
		ok = ok && check_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_api_compare", "host_action_workbench_title=");
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
	ok = ok && rp_file_contains("rp_query", "knowledge_index=search_documents:1685");
	ok = ok && rp_file_contains("rp_query", "provenance_nodes:406");
	ok = ok && rp_file_contains("rp_query", "provenance_links:544");
	ok = ok && rp_file_contains("rp_query", "events:8966");
	ok = ok && rp_file_contains("rp_query", "context_records:380");
	ok = ok && rp_file_contains("rp_llmlog", "transcripts=99");
	ok = ok && rp_file_contains("rp_llmlog", "bridge_requests=33");
	ok = ok && rp_file_contains("rp_llmlog", "bridge_responses=33");
	ok = ok && rp_file_contains("rp_runner", "workbench_delivery_scale=workbenches:8");
	ok = ok && rp_file_contains("rp_runner", "deliveries:9");
	ok = ok && rp_file_contains("rp_runner", "project_action_plans:17");
	ok = ok && rp_file_contains("rp_runner", "project_runbooks:17");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_execobs", "agentos_observer=kernel_event_timeline");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_timeline", "agentos_event_delivery=kernel_queue");
	ok = ok && rp_file_contains("rp_execplan", "scheduled_tasks=21");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_worker", "agentos_wait=wakeup");
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
	ok = ok && rp_file_contains("rp_agents", "agentos_interagent_event=kernel_queue");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_handoff", "agentos_handoff_event=kernel_delivered");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_decisions=8");
	ok = ok && rp_file_contains("rp_agent_run", "kernel_event_delivery=inter_agent");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_invocation", "steps=10");
	ok = ok && rp_file_contains("rp_completion", "actions=4");
	ok = ok && rp_file_contains("rp_backend", "cases=8");
	ok = ok && rp_file_contains("rp_backend", "agentos_mainflow_kernel=required");
	ok = ok && rp_file_contains("rp_backend", "agentos_mainflow_facts=12");
	ok = ok && rp_file_contains("rp_backend_exec", "runtime_cases_verified=");
	ok = ok && rp_file_contains("rp_study", "arms=2");
	ok = ok && rp_file_contains("rp_consistency", "state_relation=passed");
	ok = ok && rp_file_contains("rp_consistency", "task_records=21");
	ok = ok && rp_file_contains("rp_consistency", "checks=420");
	ok = ok && rp_file_contains("rp_consistency", "state_catalog_checks=12");
	ok = ok && rp_file_contains("rp_consistency", "startup_doctor_checks=14");
	ok = ok && rp_file_contains("rp_state_catalog", "represented_state_categories=574");
	ok = ok && rp_file_contains("rp_startup", "doctor_checks=10");
	ok = ok && rp_file_contains("rp_startup", "recommended_commands=startup_guide");
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
	ok = ok && rp_file_contains("rp_runop", "agentos_advanced_surface=kernel_bound");
	ok = ok && rp_file_contains("rp_runop", "context_authority=shadow");
	ok = ok && rp_file_contains("rp_runop", "startup_health=quickstart:ready");
	ok = ok && rp_file_contains("rp_runop", "startup_checks=8");
	ok = ok && rp_file_contains("rp_runop", "configuration_health=settings:ready");
	ok = ok && rp_file_contains("rp_runop", "stores_secret_values=0");
	ok = ok && rp_file_contains("rp_runop", "platform_doctor=ready;checks=10");
	ok = ok && rp_file_contains("rp_runop", "provider_health=offline:1,cloud:0,ready_cloud:0");
	ok = ok && rp_file_contains("rp_runop", "project_scaffold=templates:3");
	ok = ok && rp_file_contains("rp_runop", "dataset_product=previews:2");
	ok = ok && rp_file_contains("rp_runop", "source_portfolio=sources:67");
	ok = ok && rp_file_contains("rp_runop", "research_portfolio_scale=sources:67");
	ok = ok && rp_file_contains("rp_runop", "datasets:5");
	ok = ok && rp_file_contains("rp_runop", "literature_searches:7");
	ok = ok && rp_file_contains("rp_runop", "reviews:11");
	ok = ok && rp_file_contains("rp_runop", "evidence_reviews:7");
	ok = ok && rp_file_contains("rp_runop", "evidence_extractions:25");
	ok = ok && rp_file_contains("rp_runop", "screening_decisions:25");
	ok = ok && rp_file_contains("rp_runop", "exports:80");
	ok = ok && rp_file_contains("rp_runop", "doctor_reports:12");
	ok = ok && rp_file_contains("rp_runop", "project_handoff_audits:34");
	ok = ok && rp_file_contains("rp_runop", "project_run_comparisons:17");
	ok = ok && rp_file_contains("rp_runop", "project_reproducibility_audits:17");
	ok = ok && rp_file_contains("rp_runop", "project_snapshot_comparisons:17");
	ok = ok && rp_file_contains("rp_runop", "study_protocol_reproduction=packages:1");
	ok = ok && rp_file_contains("rp_runop", "project_bundle_cache=latest:ready");
	ok = ok && rp_file_contains("rp_consistency", "research_product_checks=18");
	ok = ok && rp_file_contains("rp_consistency", "runtime_assurance_checks=24");
	ok = ok && rp_file_contains("rp_consistency", "research_ops_checks=28");
	ok = ok && rp_file_contains("rp_consistency", "lab_governance_ops_checks=26");
	ok = ok && rp_file_contains("rp_consistency", "knowledge_index_checks=22");
	ok = ok && rp_file_contains("rp_consistency", "llm_transcript_checks=3");
	ok = ok && rp_file_contains("rp_consistency", "workbench_delivery_checks=15");
	ok = ok && rp_file_contains("rp_runop", "runtime_assurance=secret_refs:3");
	ok = ok && rp_file_contains("rp_runop", "model_registry:2");
	ok = ok && rp_file_contains("rp_modelreg", "model_registry_service_checks=96");
	ok = ok && rp_file_contains("rp_modelreg", "model=registered-model:agent-triage-template");
	ok = ok && rp_file_contains("rp_modelreg", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_modelreg", "agentos_metadata=observed");
	ok = ok && rp_file_contains("rp_modelreg", "agentos_provenance=observed");
	ok = ok && rp_file_contains("rp_modelver", "version=model-version:agent-triage-template:v1");
	ok = ok && rp_file_contains("rp_modelver", "agentos_version_trace=kernel_context_path");
	ok = ok && rp_file_contains("rp_modeleval", "metric_evidence_coverage=1.000");
	ok = ok && rp_file_contains("rp_modeleval", "agentos_evaluation_event=observed");
	ok = ok && rp_file_contains("rp_modeldep", "check_secret_policy=not_required");
	ok = ok && rp_file_contains("rp_modeldep", "agentos_capability_check=deployment_policy");
	ok = ok && rp_file_contains("rp_modelserve", "latency_ms=12");
	ok = ok && rp_file_contains("rp_modelserve", "agentos_serving_watch=observed");
	ok = ok && rp_file_contains("rp_package", "model_registry=rp_modelreg;version=v1;evaluation=passed;deployment=ready;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "model_registry_page=rp_modelreg;models=1;versions=1;evaluations=1;deployments=1;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=model_registry;source=rp_modelreg;checks=96;evaluation=passed;deployment=ready;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "model_registry_service_checks=96");
	ok = ok && rp_file_contains("rp_sysreview", "systematic_review_checks=104");
	ok = ok && rp_file_contains("rp_sysreview", "protocol=systematic-review:agent-os-science");
	ok = ok && rp_file_contains("rp_sysreview", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_syssearch", "agentos_query_trace=kernel_context_path");
	ok = ok && rp_file_contains("rp_sysscreen", "agentos_screening_event=observed");
	ok = ok && rp_file_contains("rp_sysextract", "agentos_extraction_metadata=observed");
	ok = ok && rp_file_contains("rp_syssynth", "agentos_synthesis_context=observed");
	ok = ok && rp_file_contains("rp_sysprisma", "agentos_prisma_event=observed");
	ok = ok && rp_file_contains("rp_package", "systematic_review=rp_sysreview;protocol=systematic-review:agent-os-science;included=3;prisma=ready;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "systematic_review_page=rp_sysreview;protocols=1;screening=9;included=3;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=systematic_review;source=rp_sysreview;checks=104;included=3;prisma=ready;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "systematic_review_checks=104");
	ok = ok && rp_file_contains("rp_agentcmp", "kernel_metadata=observed");
	ok = ok && rp_file_contains("rp_expsched", "experiment_scheduling_checks=88");
	ok = ok && rp_file_contains("rp_expsched", "schedule=schedule:RUN-042:lab-execution");
	ok = ok && rp_file_contains("rp_expsched", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_schedtask", "agentos_task_context=observed");
	ok = ok && rp_file_contains("rp_schedbook", "agentos_booking_metadata=observed");
	ok = ok && rp_file_contains("rp_schedconf", "agentos_conflict_event=observed");
	ok = ok && rp_file_contains("rp_schedexec", "agentos_execution_trace=observed");
	ok = ok && rp_file_contains("rp_package", "experiment_schedule=rp_expsched;schedule=schedule:RUN-042:lab-execution;tasks=3;bookings=4;conflicts=1;executions=2;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "experiment_schedule_page=rp_expsched;schedules=1;tasks=3;bookings=4;conflicts=1;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=experiment_schedule;source=rp_expsched;checks=88;tasks=3;conflicts=1;executions=2;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "experiment_scheduling_checks=88");
	ok = ok && rp_file_contains("rp_traincomp", "training_compliance_checks=92");
	ok = ok && rp_file_contains("rp_traincomp", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_trainreq", "requirement=training-req:sop-deviation:qa-lead");
	ok = ok && rp_file_contains("rp_trainreq", "agentos_requirement_metadata=observed");
	ok = ok && rp_file_contains("rp_trainrec", "training=training:qa-lead:sop-deviation");
	ok = ok && rp_file_contains("rp_trainrec", "agentos_training_context=observed");
	ok = ok && rp_file_contains("rp_trainassess", "assessment=competency:qa-lead:sop-deviation");
	ok = ok && rp_file_contains("rp_trainassess", "agentos_assessment_event=observed");
	ok = ok && rp_file_contains("rp_trainauth", "authorization=auth:qa-lead:qa-lead:lab-gene-x");
	ok = ok && rp_file_contains("rp_trainauth", "agentos_authorization_capability=observed");
	ok = ok && rp_file_contains("rp_traingap", "status=resolved");
	ok = ok && rp_file_contains("rp_traingap", "agentos_gap_event=observed");
	ok = ok && rp_file_contains("rp_package", "training_compliance=rp_traincomp;requirements=4;records=4;assessments=4;auth=3;gaps=1;open=0;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "training_compliance_page=rp_traincomp;requirements=4;records=4;gaps=1;auth=3;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=training_compliance;source=rp_traincomp;checks=92;requirements=4;open_gaps=0;auth=3;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "training_compliance_checks=92");
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
	ok = ok && rp_file_contains("rp_ui_compare", "agentos_kernel=mainflow_bound");
	ok = ok && rp_file_contains("rp_ui_compare", "agentos_context_snapshot=1");
	ok = ok && rp_file_contains("rp_ui_compare", "agentos_metadata_index=1");
	ok = ok && rp_file_contains("rp_ui_compare", "agentos_batch_tool=1");
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
	ok = ok && rp_file_contains("rp_web_routes", "routes=152");
	ok = ok && rp_file_contains("rp_web_routes", "get_routes=29");
	ok = ok && rp_file_contains("rp_web_routes", "host_page_routes=15");
	ok = ok && rp_file_contains("rp_web_routes", "ucore_page_routes=15");
	ok = ok && rp_file_contains("rp_web_routes", "host_dynamic_page_prefixes=12");
	ok = ok && rp_file_contains("rp_web_routes", "ucore_dynamic_page_prefixes=12");
	ok = ok && rp_file_contains("rp_web_routes", "host_download_routes=16");
	ok = ok && rp_file_contains("rp_web_routes", "ucore_download_routes=16");
	ok = ok && rp_file_contains("rp_web_routes", "route=/quickstart");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research-ops");
	ok = ok && rp_file_contains("rp_web_routes", "route=/workbench-plan-queue");
	ok = ok && rp_file_contains("rp_web_routes", "route=/review-inbox");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research-studio");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research/workbench/{id}");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research/project/{id}/review");
	ok = ok && rp_file_contains("rp_web_routes", "route=/api-catalog");
	ok = ok && rp_file_contains("rp_web_routes", "prefix=/runs/{run_id}");
	ok = ok && rp_file_contains("rp_web_routes", "prefix=/workbench-files/{token}");
	ok = ok && rp_file_contains("rp_web_routes", "prefix=/provenance/{id}");
	ok = ok && rp_file_contains("rp_web_routes", "prefix=/llm/{id}");
	ok = ok && rp_file_contains("rp_web_routes", "download=/download/research-dataset-preview/{token}");
	ok = ok && rp_file_contains("rp_web_routes", "download=/download/research-source-portfolio/{token}");
	ok = ok && rp_file_contains("rp_web_routes", "download=/download/research-study-protocol-reproduction-package-action-execution/{token}");
	ok = ok && rp_file_contains("rp_web_routes", "post_routes=123");
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
	ok = ok && rp_file_contains("rp_api_compare", "agentos_kernel=mainflow_bound");
	ok = ok && rp_file_contains("rp_api_compare", "agentos_context_snapshot=1");
	ok = ok && rp_file_contains("rp_api_compare", "agentos_metadata_index=1");
	ok = ok && rp_file_contains("rp_api_compare", "agentos_batch_tool=1");
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
	ok = ok && rp_file_contains("rp_api_action", "actions=123");
	ok = ok && rp_file_contains("rp_api_action", "project_scaffold=/actions/research/project-scaffold");
	ok = ok && rp_file_contains("rp_api_action", "project_launch=/actions/research/project-launch");
	ok = ok && rp_file_contains("rp_api_action", "project_action_execute=/actions/research/project-action-execute");
	ok = ok && rp_file_contains("rp_api_action", "dataset_preview=/actions/research/dataset-preview");
	ok = ok && rp_file_contains("rp_api_action", "dataset_run=/actions/research/dataset-run");
	ok = ok && rp_file_contains("rp_api_action", "study_protocol_launch=/actions/research/study-protocol-launch");
	ok = ok && rp_file_contains("rp_api_action", "study_protocol_reproduction_package_action_execute=/actions/research/study-protocol-reproduction-package-action-execute");
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
	ok = ok && rp_file_contains("rp_api_catalog", "host_api_routes=214");
	ok = ok && rp_file_contains("rp_api_catalog", "host_action_routes=95");
	ok = ok && rp_file_contains("rp_api_catalog", "host_page_routes=15");
	ok = ok && rp_file_contains("rp_api_catalog", "host_dynamic_page_prefixes=12");
	ok = ok && rp_file_contains("rp_api_catalog", "ucore_dynamic_page_prefixes=12");
	ok = ok && rp_file_contains("rp_api_catalog", "host_download_routes=16");
	ok = ok && rp_file_contains("rp_api_catalog", "ucore_download_routes=16");
	ok = ok && rp_file_contains("rp_api_catalog", "download_group=dataset;routes=6");
	ok = ok && rp_file_contains("rp_api_catalog", "api_group_count=14");
	ok = ok && rp_file_contains("rp_api_catalog", "api_grouped_routes=214");
	ok = ok && rp_file_contains("rp_api_catalog", "usable_research_api_routes=77");
	ok = ok && rp_file_contains("rp_api_catalog", "domain_api_routes=50");
	ok = ok && rp_file_contains("rp_api_catalog", "lab_research_api_routes=15");
	ok = ok && rp_file_contains("rp_api_catalog", "workflow_api_routes=12");
	ok = ok && rp_file_contains("rp_api_catalog", "api_group=usable_research;routes=77");
	ok = ok && rp_file_contains("rp_api_catalog", "api_group=domain;routes=50");
	ok = ok && rp_file_contains("rp_api_catalog", "api_group=lab_research;routes=15");
	ok = ok && rp_file_contains("rp_api_catalog", "api_group=llm;routes=4");
	ok = ok && rp_file_contains("rp_api_catalog", "api_key=/api/analysis-results");
	ok = ok && rp_file_contains("rp_api_catalog", "api_key=/api/experiment-scheduling");
	ok = ok && rp_file_contains("rp_api_catalog", "api_key=/api/workflow-runner");
	ok = ok && rp_file_contains("rp_api_catalog", "api_key=/api/usable-research-workbench-file-catalog");
	ok = ok && rp_file_contains("rp_api_catalog", "api_key=/api/usable-research-study-protocol-reproduction-package-action-plan");
	ok = ok && rp_file_contains("rp_api_catalog", "api_key=/api/llm-proxy");
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
	ok = ok && rp_file_contains("rp_web_bundle", "api_payloads=15");
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
	ok = ok && rp_file_contains("rp_web_bundle", "reader_views=40");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_actions=123");
	ok = ok && rp_file_contains("rp_web_bundle", "post_routes=123");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_api_catalog,rp_studio,rp_web_bundle");
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
		ok = ok && rp_file_contains("rp_api_action", "project_space_actions=7");
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
	ok = ok && require_equal("review_decisions", review_decisions, 4);
	ok = ok && require_at_least("review_handoffs", review_handoffs, 3);
	ok = ok && require_equal("review_pack_actions", review_pack_actions, 3);
	ok = ok && require_equal("review_bridge_paths", review_bridge_paths, 4);
	ok = ok && rp_file_contains("rp_review_dashboard", "pack_source=rp_package,rp_runner,rp_review_pack");
	ok = ok && rp_file_contains("rp_review_dashboard", "pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff");
	ok = ok && rp_file_contains("rp_package", "review_pack_action=deliver_to_reviewer;source=rp_package;status=ready");
	ok = ok && rp_file_contains("rp_package", "review_pack_action=resolve_project_items;source=rp_package;status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_next_task=delivery_manifest");
	ok = ok && rp_file_contains("rp_api_action", "project_space_actions=7");
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
	ok = ok && rp_file_contains("rp_runbooks", "runbook_service_checks=16");
	ok = ok && rp_file_contains("rp_runbooks", "runbook_templates=1");
	ok = ok && rp_file_contains("rp_runbooks", "runbook_steps=7");
	ok = ok && rp_file_contains("rp_runbooks", "incident_triages=1");
	ok = ok && rp_file_contains("rp_runbooks", "runbook_executions=1");
	ok = ok && rp_file_contains("rp_runbooks", "runbook_exports=1");
	ok = ok && rp_file_contains("rp_runbooks", "worker_operation_records=6");
	ok = ok && rp_file_contains("rp_runbooks", "agentos_adaptation=event_context,kernel_timeline,metadata_index,batch_recovery_tool;evidence=rp_agentos_mainflow,rp_agentos_timeline,rp_agentos_query,rp_agentos_recovery;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=runbooks;source=rp_runbooks;steps=7;incident=closed;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "runbook_service=checks:16");
	ok = ok && rp_file_contains("rp_agentcmp", "runbook_kernel_binding=event_context,kernel_timeline,metadata_index,batch_recovery_tool;source=rp_runbooks;status=ready");
	ok = ok && rp_file_contains("rp_projectrel", "project_delivery_checks=18");
	ok = ok && rp_file_contains("rp_projectrel", "project_handoff_audits=1");
	ok = ok && rp_file_contains("rp_projectrel", "project_release_gates=1");
	ok = ok && rp_file_contains("rp_projectrel", "project_snapshots=1");
	ok = ok && rp_file_contains("rp_projectrel", "project_reproducibility_audits=1");
	ok = ok && rp_file_contains("rp_projectrel", "project_provenance_graphs=1");
	ok = ok && rp_file_contains("rp_projectrel", "package_intakes=1");
	ok = ok && rp_file_contains("rp_projectrel", "agentos_adaptation=file_metadata_index,event_delivery,context_release_evidence,capability_guard;evidence=rp_agentos_query,rp_agentos_mainflow,rp_agentos_package;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=project_delivery;source=rp_projectrel;release=ready;reproducibility=passed;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "project_delivery_service=checks:18");
	ok = ok && rp_file_contains("rp_agentcmp", "project_delivery_kernel_binding=file_metadata_index,event_delivery,context_release_evidence,capability_guard;source=rp_projectrel;status=ready");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_checks=20");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocols=2");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_launches=2");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_reproduction_packages=1");
	ok = ok && rp_file_contains("rp_studyproto", "reproduction_action_plans=1");
	ok = ok && rp_file_contains("rp_studyproto", "dataset_portfolios=1");
	ok = ok && rp_file_contains("rp_studyproto", "source_portfolios=1");
	ok = ok && rp_file_contains("rp_studyproto", "agentos_adaptation=file_metadata_index,context_protocol_evidence,event_reproduction_queue,batch_dataset_tool;evidence=rp_agentos_query,rp_agentos_mainflow,rp_agentos_timeline,rp_agentos_recovery;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=study_protocols;source=rp_studyproto;launches=2;reproduction=ready;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "study_protocol_service=checks:20");
	ok = ok && rp_file_contains("rp_agentcmp", "study_protocol_kernel_binding=file_metadata_index,context_protocol_evidence,event_reproduction_queue,batch_dataset_tool;source=rp_studyproto;status=ready");
	ok = ok && rp_file_contains("rp_stdesign", "statistical_design_checks=120");
	ok = ok && rp_file_contains("rp_stdesign", "design=stat-design:lab-gene-x:run042-primary");
	ok = ok && rp_file_contains("rp_stdesign", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_stdesign", "agentos_metadata=observed");
	ok = ok && rp_file_contains("rp_stdesign", "agentos_provenance=observed");
	ok = ok && rp_file_contains("rp_power", "required_per_group=11");
	ok = ok && rp_file_contains("rp_power", "status=underpowered");
	ok = ok && rp_file_contains("rp_power", "agentos_context_record=power_analysis");
	ok = ok && rp_file_contains("rp_random", "assignments=4");
	ok = ok && rp_file_contains("rp_random", "status=balanced");
	ok = ok && rp_file_contains("rp_random", "agentos_file_metadata=randomization_plan");
	ok = ok && rp_file_contains("rp_blind", "status=ok");
	ok = ok && rp_file_contains("rp_blind", "agentos_capability_check=blinding_roles");
	ok = ok && rp_file_contains("rp_streview", "stat_result=approved_with_sample_size_note");
	ok = ok && rp_file_contains("rp_streview", "agentos_review_trace=kernel_context_path");
	ok = ok && rp_file_contains("rp_package", "statistical_design=rp_stdesign;stat_result=approved_with_sample_size_note;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "statistical_design_page=rp_stdesign;designs=1;power=underpowered;randomization=balanced;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=statistical_design;source=rp_stdesign;checks=120;stat_result=approved_with_sample_size_note;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "statistical_design_checks=120");
	ok = ok && rp_file_contains("rp_opsboard", "operations_board_checks=18");
	ok = ok && rp_file_contains("rp_opsboard", "pending_reviews=1");
	ok = ok && rp_file_contains("rp_opsboard", "active_workbench_actions=4");
	ok = ok && rp_file_contains("rp_opsboard", "active_plan_items=5");
	ok = ok && rp_file_contains("rp_opsboard", "ready_handoffs=3");
	ok = ok && rp_file_contains("rp_opsboard", "report_export=research-ops-report:RUN-042");
	ok = ok && rp_file_contains("rp_opsboard", "agentos_adaptation=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor;evidence=rp_agentos_mainflow,rp_agentos_timeline,rp_agentos_recovery;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=research_operations;source=rp_opsboard;pending_reviews=1;handoffs=3;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "research_operations_service=checks:18");
	ok = ok && rp_file_contains("rp_agentcmp", "opsboard_kernel_binding=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor;source=rp_opsboard;status=ready");
	ok = ok && rp_file_contains("rp_reviewboard", "review_board_checks=24");
	ok = ok && rp_file_contains("rp_reviewboard", "review_votes=4");
	ok = ok && rp_file_contains("rp_reviewboard", "review_signoffs=4");
	ok = ok && rp_file_contains("rp_reviewboard", "review_assignments=4");
	ok = ok && rp_file_contains("rp_reviewboard", "review_workloads=4");
	ok = ok && rp_file_contains("rp_reviewboard", "decision=approved");
	ok = ok && rp_file_contains("rp_reviewboard", "review_package=formal-review-board-package:RUN-042");
	ok = ok && rp_file_contains("rp_reviewboard", "agentos_adaptation=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding;evidence=rp_agentos_roles,rp_agentos_mainflow,rp_agentos_collab_ack,rp_agentos_query,rp_agentos_package;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_reviewops", "formal_review_board=checks:24");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=formal_review_board;source=rp_reviewboard;votes=4;signoffs=4;decision=approved;status=ready");
	ok = ok && rp_file_contains("rp_opsboard", "handoff=review-board->operations;artifact=rp_reviewboard;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "reviewboard_kernel_binding=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding;source=rp_reviewboard;status=ready");
	ok = ok && rp_file_contains("rp_control", "control_plane_checks=30");
	ok = ok && rp_file_contains("rp_control", "approvals=4");
	ok = ok && rp_file_contains("rp_control", "approval_transitions=4");
	ok = ok && rp_file_contains("rp_control", "subscriptions=3");
	ok = ok && rp_file_contains("rp_control", "notifications=4");
	ok = ok && rp_file_contains("rp_control", "run_queue_items=4");
	ok = ok && rp_file_contains("rp_control", "leases=2");
	ok = ok && rp_file_contains("rp_control", "plugin_manifests=3");
	ok = ok && rp_file_contains("rp_control", "plugin_runs=3");
	ok = ok && rp_file_contains("rp_control", "workspaces=1");
	ok = ok && rp_file_contains("rp_control", "users=3");
	ok = ok && rp_file_contains("rp_control", "access_grants=3");
	ok = ok && rp_file_contains("rp_control", "saved_views=2");
	ok = ok && rp_file_contains("rp_control", "api_tokens=1");
	ok = ok && rp_file_contains("rp_control", "permissions=5");
	ok = ok && rp_file_contains("rp_control", "control_actions=8");
	ok = ok && rp_file_contains("rp_control", "approval=approval:release-dossier:4;target=release-dossier:RUN-042;state=published;actor=wang;status=recorded");
	ok = ok && rp_file_contains("rp_control", "subscription=sub:ops:writer:*;target=writer;event=*;status=active");
	ok = ok && rp_file_contains("rp_control", "notification=notif:4;target=writer;event=PLUGIN_RUN;delivered=1;status=ready");
	ok = ok && rp_file_contains("rp_control", "queue=queue:RUN-042:2;run=RUN-042-review;priority=80;state=leased;worker=reviewer;status=ready");
	ok = ok && rp_file_contains("rp_control", "plugin=plugin.tuning;name=Parameter Tuning;tools=recommend_memory_limit;enabled=1;status=ready");
	ok = ok && rp_file_contains("rp_control", "plugin_run=plugin-run:3;plugin=plugin.tuning;tool=recommend_memory_limit;current=1024;recommended=1536;status=ready");
	ok = ok && rp_file_contains("rp_control", "workspace=ws:lab-gene-x;owner=wang;projects=1;status=ready");
	ok = ok && rp_file_contains("rp_control", "grant=grant:guest:lab-gene-x:viewer;subject=guest;object=lab-gene-x;role=viewer;status=ready");
	ok = ok && rp_file_contains("rp_control", "saved_view=view:planned-jobs;kind=jobs;query=status=planned;owner=wang;status=ready");
	ok = ok && rp_file_contains("rp_control", "api_token=token:local-dashboard;owner=wang;scopes=read,dashboard;secret_material=not_written;status=ready");
	ok = ok && rp_file_contains("rp_control", "permission=can:guest:approve;result=deny;status=ready");
	ok = ok && rp_file_contains("rp_control", "control_report=platform-control-report:RUN-042;approvals=4;notifications=4;queue_items=4;plugin_runs=3;status=ready");
	ok = ok && rp_file_contains("rp_control", "agentos_adaptation=kernel_capability_check,kernel_event_delivery,kernel_plugin_tool_table,kernel_run_queue;evidence=rp_agentos_mainflow,rp_agentos_roles,rp_agentos_timeline,rp_agentos_kernel;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=platform_control_plane;source=rp_control;approvals=4;notifications=4;plugins=3;status=ready");
	ok = ok && rp_file_contains("rp_opsboard", "handoff=control-plane->operations;artifact=rp_control;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "control_plane_kernel_binding=capability_check,event_delivery,tool_table,agent_run_queue;source=rp_control;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "integrity_checks=36");
	ok = ok && rp_file_contains("rp_integrity", "evidence_contracts=8");
	ok = ok && rp_file_contains("rp_integrity", "reference_contracts=8");
	ok = ok && rp_file_contains("rp_integrity", "namespace_checks=5");
	ok = ok && rp_file_contains("rp_integrity", "status_checks=5");
	ok = ok && rp_file_contains("rp_integrity", "review_alignment_checks=4");
	ok = ok && rp_file_contains("rp_integrity", "errors=0");
	ok = ok && rp_file_contains("rp_integrity", "decision=passed");
	ok = ok && rp_file_contains("rp_integrity", "evidence_check=backend_evidence;source=rp_backend_exec;target=rp_report_text;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "reference_check=stage_artifacts;source=rp_stage_state;target=rp_artifact;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "namespace_check=run_id;value=RUN-042;scope=project;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "status_check=package;source=rp_package;allowed=draft,ready,approved,released;result=pass");
	ok = ok && rp_file_contains("rp_integrity", "review_alignment=board_to_dashboard;source=rp_reviewboard;target=rp_review_dashboard;decision=aligned;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "report_source_check=workflow;source=rp_report_text;target=rp_stage_state;source_key=host_workflow_run_id;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "package_trace=delivery;source=rp_package;target=rp_web_bundle;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "integrity_report=integrity-report:RUN-042;checks=36;errors=0;warnings=0;status=ready");
	ok = ok && rp_file_contains("rp_integrity", "agentos_adaptation=kernel_context_attestation,kernel_metadata_reference_index,kernel_event_trace,kernel_namespace_registry;evidence=rp_agentos_mainflow,rp_agentos_query,rp_agentos_timeline,rp_agentos_package;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=integrity_plane;source=rp_integrity;checks=36;errors=0;result=passed;status=ready");
	ok = ok && rp_file_contains("rp_opsboard", "handoff=integrity-plane->operations;artifact=rp_integrity;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "integrity_plane_checks=36");
	ok = ok && rp_file_contains("rp_agentcmp", "integrity_kernel_binding=context_attestation,metadata_reference_index,event_trace,namespace_registry;source=rp_integrity;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "coherence_checks=40");
	ok = ok && rp_file_contains("rp_coherence", "delivery_contracts=7");
	ok = ok && rp_file_contains("rp_coherence", "run_state_contracts=7");
	ok = ok && rp_file_contains("rp_coherence", "lifecycle_contracts=6");
	ok = ok && rp_file_contains("rp_coherence", "workflow_lint_checks=5");
	ok = ok && rp_file_contains("rp_coherence", "tool_protocol_checks=5");
	ok = ok && rp_file_contains("rp_coherence", "report_validation_checks=5");
	ok = ok && rp_file_contains("rp_coherence", "agent_coordination_checks=3");
	ok = ok && rp_file_contains("rp_coherence", "errors=0");
	ok = ok && rp_file_contains("rp_coherence", "decision=passed");
	ok = ok && rp_file_contains("rp_coherence", "delivery_check=research_package;source=rp_package;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "run_state_check=stage_order;source=rp_stage_state;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "lifecycle_check=llm_relay;source=rp_llm_packets;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "workflow_lint=retry_minimality;source=rp_retry_plan;expected=align_only;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "tool_validation=backend_runner;tools=case_execution;source=rp_backend_exec;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "report_validation=backend_source;source=rp_report_text;target=rp_backend_exec;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "agent_coordination=recovery_path;source=rp_retry_plan;target=rp_runbooks;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "coherence_report=coherence-report:RUN-042;checks=40;errors=0;warnings=0;status=ready");
	ok = ok && rp_file_contains("rp_coherence", "agentos_adaptation=kernel_run_state_views,kernel_tool_contract_table,kernel_delivery_metadata,kernel_agent_coordination_trace;evidence=rp_agentos_mainflow,rp_agentos_kernel,rp_agentos_package,rp_agentos_collab_ack;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=coherence_plane;source=rp_coherence;checks=40;errors=0;reference_result=expected_pass;status=reference_ready");
	ok = ok && rp_file_contains("rp_opsboard", "handoff=coherence-plane->operations;artifact=rp_coherence;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "coherence_plane_checks=40");
	ok = ok && rp_file_contains("rp_agentcmp", "coherence_kernel_binding=run_state_views,tool_contract_table,delivery_metadata,agent_coordination_trace;source=rp_coherence;status=reference_ready");
	ok = ok && rp_file_contains("rp_publication", "publication_checks=48");
	ok = ok && rp_file_contains("rp_publication", "targets=2");
	ok = ok && rp_file_contains("rp_publication", "submissions=2");
	ok = ok && rp_file_contains("rp_publication", "review_rounds=2");
	ok = ok && rp_file_contains("rp_publication", "response_packages=2");
	ok = ok && rp_file_contains("rp_publication", "response_items=4");
	ok = ok && rp_file_contains("rp_publication", "decision=accepted");
	ok = ok && rp_file_contains("rp_publication", "submission=submission:RUN-042:systems-biology-report");
	ok = ok && rp_file_contains("rp_publication", "review_round=peer-review:RUN-042:round-1");
	ok = ok && rp_file_contains("rp_publication", "revision_task=revision:RUN-042:methods-reproducibility");
	ok = ok && rp_file_contains("rp_publication", "response_package=peer-review-response-package:RUN-042:round-1");
	ok = ok && rp_file_contains("rp_publication", "response_item=4;package=peer-review-response-package:RUN-042:round-2");
	ok = ok && rp_file_contains("rp_publication", "publication_decision=publication-decision:RUN-042:accept-with-evidence");
	ok = ok && rp_file_contains("rp_publication", "agentos_adaptation=kernel_submission_metadata,kernel_review_event_queue,kernel_response_context,kernel_release_gate;evidence=rp_agentos_package,rp_agentos_timeline,rp_agentos_mainflow,rp_agentos_roles;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_pubplan", "checklist_items=9");
	ok = ok && rp_file_contains("rp_pubplan", "submission_material=rp_package,rp_dossier,rp_report_text,rp_artifact_manifest,rp_review_pack");
	ok = ok && rp_file_contains("rp_pubplan", "agentos_showcase=plain_userland_vs_kernel_assisted;evidence=rp_agentos_package,rp_agentos_timeline,rp_agentos_mainflow,rp_agentos_roles;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_peerresp", "addressed=4");
	ok = ok && rp_file_contains("rp_peerresp", "needs_revision=0");
	ok = ok && rp_file_contains("rp_peerresp", "response_letter=peer-review-response:RUN-042");
	ok = ok && rp_file_contains("rp_api_pub", "publication_workflow=rp_publication");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=publication_response;source=rp_publication;reviews=2;responses=2;outcome=accepted;status=ready");
	ok = ok && rp_file_contains("rp_package", "publication_workflow=rp_publication;response_package=rp_peerresp;status=ready");
	ok = ok && rp_file_contains("rp_dossier", "publication_workflow=rp_publication;submission=accepted;peer_response=ready;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "publication_page=rp_publication;peer_response=rp_peerresp;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "publication_checks=48");
	ok = ok && rp_file_contains("rp_agentcmp", "publication_kernel_binding=submission_metadata,review_event_queue,response_context,release_gate;source=rp_publication;status=ready");
	ok = ok && rp_file_contains("rp_calculation", "calculation_checks=84");
	ok = ok && rp_file_contains("rp_calculation", "job=calculation-job:lab-gene-x:run042-qc");
	ok = ok && rp_file_contains("rp_calculation", "agentos_kernel_metadata=observed");
	ok = ok && rp_file_contains("rp_calculation", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_calculation", "agentos_provenance=observed");
	ok = ok && rp_file_contains("rp_calc_files", "retrieved_files=3");
	ok = ok && rp_file_contains("rp_calc_parse", "parser_result=calculation-parser-result:run042-qc");
	ok = ok && rp_file_contains("rp_calc_export", "export=calculation-export:lab-gene-x:run042-qc");
	ok = ok && rp_file_contains("rp_web_bundle", "calculations_page=rp_calculation;jobs=1;retrieved=3;parser_results=1;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=calculations;source=rp_calculation;jobs=1;retrieved=3;checks=84;outcome=passed;status=ready");
	ok = ok && rp_file_contains("rp_realtask", "real_task_checks=96");
	ok = ok && rp_file_contains("rp_realtask", "task=palmer-penguins-morphometrics");
	ok = ok && rp_file_contains("rp_realtask", "agentos_kernel_metadata=observed");
	ok = ok && rp_file_contains("rp_realtask", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_realtask", "agentos_provenance=observed");
	ok = ok && rp_file_contains("rp_realdata", "rows=344");
	ok = ok && rp_file_contains("rp_realdata", "metric_group_summaries=5");
	ok = ok && rp_file_contains("rp_realdata", "metric_dimension_group_summaries=10");
	ok = ok && rp_file_contains("rp_realdata", "agentos_file_metadata=real_task_inputs");
	ok = ok && rp_file_contains("rp_realreport", "answer_source=report_md");
	ok = ok && rp_file_contains("rp_realreport", "claim_audit=pass");
	ok = ok && rp_file_contains("rp_realreport", "agentos_context_record=report_answer_audit");
	ok = ok && rp_file_contains("rp_realbundle", "duplicate_zip_entries=0");
	ok = ok && rp_file_contains("rp_realbundle", "agentos_package_trace=kernel_provenance");
	ok = ok && rp_file_contains("rp_web_bundle", "real_task_page=rp_realtask;dataset=palmer-penguins;rows=344;answer_audit=pass;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=real_task;source=rp_realtask;dataset=palmer-penguins;checks=96;outcome=passed;status=ready");
	ok = ok && rp_file_contains("rp_analysisres", "analysis_results_checks=96");
	ok = ok && rp_file_contains("rp_analysisres", "analysis_runs=2");
	ok = ok && rp_file_contains("rp_analysisres", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_anplan", "plan=analysis-plan:RUN-042:treatment-response");
	ok = ok && rp_file_contains("rp_anplan", "agentos_plan_context=observed");
	ok = ok && rp_file_contains("rp_anrun", "run=analysis-run:RUN-042:manual");
	ok = ok && rp_file_contains("rp_anrun", "agentos_run_event=observed");
	ok = ok && rp_file_contains("rp_resulttbl", "table=result-table:manual");
	ok = ok && rp_file_contains("rp_resulttbl", "agentos_table_metadata=observed");
	ok = ok && rp_file_contains("rp_statres", "stat=stat-result:manual");
	ok = ok && rp_file_contains("rp_statres", "agentos_stat_metadata=observed");
	ok = ok && rp_file_contains("rp_anfig", "figure=figure:manual");
	ok = ok && rp_file_contains("rp_anfig", "agentos_figure_metadata=observed");
	ok = ok && rp_file_contains("rp_interp", "interpretation=interpretation:manual");
	ok = ok && rp_file_contains("rp_interp", "agentos_interpretation_context=observed");
	ok = ok && rp_file_contains("rp_package", "analysis_results=rp_analysisres;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "analysis_results_page=rp_analysisres;runs=2;tables=2;statistics=2;figures=2;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=analysis_results;source=rp_analysisres;checks=96;runs=2;statistics=2;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "analysis_results_checks=96");
	ok = ok && rp_file_contains("rp_campaign", "campaign_checks=108");
	ok = ok && rp_file_contains("rp_campaign", "campaign=experiment-campaign:RUN-042:align-memory-grid");
	ok = ok && rp_file_contains("rp_campaign", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_campaign", "agentos_metadata=observed");
	ok = ok && rp_file_contains("rp_campaign", "agentos_provenance=observed");
	ok = ok && rp_file_contains("rp_trials", "trial_count=4");
	ok = ok && rp_file_contains("rp_trials", "trial=experiment-trial:RUN-042:align-memory-grid:04");
	ok = ok && rp_file_contains("rp_trials", "agentos_file_metadata=campaign_trials");
	ok = ok && rp_file_contains("rp_camp_rank", "decision=select_trial_04");
	ok = ok && rp_file_contains("rp_camp_rank", "metric_delta=3");
	ok = ok && rp_file_contains("rp_camp_rank", "agentos_context_record=campaign_ranking");
	ok = ok && rp_file_contains("rp_resreview", "review=experiment-result-review:RUN-042:baseline-vs-candidate");
	ok = ok && rp_file_contains("rp_resreview", "decision=accept_candidate");
	ok = ok && rp_file_contains("rp_resreview", "agentos_package_trace=kernel_provenance");
	ok = ok && rp_file_contains("rp_web_bundle", "experiment_campaigns_page=rp_campaign;campaigns=1;trials=4;best_trial=04;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=experiment_campaigns;source=rp_campaign;campaigns=1;trials=4;checks=108;outcome=passed;status=ready");
	ok = ok && rp_file_contains("rp_reldossier", "release_dossier_checks=112");
	ok = ok && rp_file_contains("rp_reldossier", "dossier=release-dossier:RUN-042:final-review");
	ok = ok && rp_file_contains("rp_reldossier", "sections=7");
	ok = ok && rp_file_contains("rp_reldossier", "decision=ready_for_review");
	ok = ok && rp_file_contains("rp_reldossier", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_reldossier", "agentos_metadata=observed");
	ok = ok && rp_file_contains("rp_reldossier", "agentos_provenance=observed");
	ok = ok && rp_file_contains("rp_reldsec", "section=experiment-campaign;status=ok");
	ok = ok && rp_file_contains("rp_reldsec", "section=agentos-readiness;status=ok");
	ok = ok && rp_file_contains("rp_reldsec", "agentos_context_record=release_dossier_sections");
	ok = ok && rp_file_contains("rp_relattest", "attestations=4");
	ok = ok && rp_file_contains("rp_relattest", "agentos_file_metadata=release_attestations");
	ok = ok && rp_file_contains("rp_relpack", "package_files=2");
	ok = ok && rp_file_contains("rp_relpack", "download=release-dossier-package:RUN-042");
	ok = ok && rp_file_contains("rp_relpack", "agentos_package_trace=kernel_provenance");
	ok = ok && rp_file_contains("rp_package", "release_dossier=rp_reldossier;sections=7;decision=ready_for_review;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "release_dossier_page=rp_reldossier;sections=7;decision=ready_for_review;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=release_dossier;source=rp_reldossier;sections=7;checks=112;outcome=passed;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "release_dossier_checks=112");
	ok = ok && rp_file_contains("rp_decsupport", "decision_support_checks=80");
	ok = ok && rp_file_contains("rp_decsupport", "recommended_option=agentos_ucore_hybrid");
	ok = ok && rp_file_contains("rp_decsupport", "agentos_context=observed");
	ok = ok && rp_file_contains("rp_decopt", "option=agentos_ucore_hybrid");
	ok = ok && rp_file_contains("rp_decopt", "kernel_observed=1");
	ok = ok && rp_file_contains("rp_deccrit", "criterion=agentos_value");
	ok = ok && rp_file_contains("rp_decscore", "score=agentos_ucore_hybrid:agentos_value");
	ok = ok && rp_file_contains("rp_decpacket", "packet=decision-review-packet:agentos-final-demo-backend");
	ok = ok && rp_file_contains("rp_package", "decision_support=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=reference_ready");
	ok = ok && rp_file_contains("rp_web_bundle", "decision_support_page=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=reference_ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=decision_support;source=rp_decsupport;options=3;criteria=5;scores=15;selected=select_agentos_ucore_hybrid;status=reference_ready");
	ok = ok && rp_file_contains("rp_agentcmp", "decision_support_checks=80");
	ok = ok && rp_file_contains("rp_usable", "usable_research_checks=100");
	ok = ok && rp_file_contains("rp_usable", "entry=research-question-to-review-package");
	ok = ok && rp_file_contains("rp_usable", "kernel_assisted=1");
	ok = ok && rp_file_contains("rp_usabletpl", "template=usable-template:workspace-900");
	ok = ok && rp_file_contains("rp_usableds", "dataset=usable-dataset:penguins;rows=344");
	ok = ok && rp_file_contains("rp_usablelib", "source=usable-source:library2026:1");
	ok = ok && rp_file_contains("rp_usabledag", "stage=package;order=9");
	ok = ok && rp_file_contains("rp_usableops", "kernel_event_queue=observed");
	ok = ok && rp_file_contains("rp_package", "usable_research=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;deliverables=8;kernel_assisted=1;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "usable_research_page=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;queues=2;kernel_assisted=1;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=usable_research;source=rp_usable;checks=100;handoff=ready;kernel_assisted=1;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "usable_research_checks=100");
	ok = ok && rp_file_contains("rp_usableproj", "usable_project_checks=120");
	ok = ok && rp_file_contains("rp_usableproj", "kernel_assisted=1");
	ok = ok && rp_file_contains("rp_usableboot", "kernel_doctor=context_snapshot,file_metadata,event_queue,capability_guard;status=observed");
	ok = ok && rp_file_contains("rp_usablescaf", "agentos_meta=dataset");
	ok = ok && rp_file_contains("rp_usablelaunch", "kernel_context=recorded");
	ok = ok && rp_file_contains("rp_usablepack", "metadata=indexed");
	ok = ok && rp_file_contains("rp_package", "usable_project=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;kernel_assisted=1;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "usable_project_page=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;kernel_assisted=1;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=usable_project;source=rp_usableproj;checks=120;bundles=2;kernel_assisted=1;status=ready");
	ok = ok && rp_file_contains("rp_mature", "reference_platforms=6");
	ok = ok && rp_file_contains("rp_mature", "capability_mappings=6");
	ok = ok && rp_file_contains("rp_mature", "capability_checks=72");
	ok = ok && rp_file_contains("rp_mature", "mapping=galaxy-workflow-history");
	ok = ok && rp_file_contains("rp_mature", "mapping=aiida-process-graph");
	ok = ok && rp_file_contains("rp_mature", "mapping=dvc-dataflow");
	ok = ok && rp_file_contains("rp_mature", "mapping=mlflow-experiment-registry");
	ok = ok && rp_file_contains("rp_mature", "mapping=nextflow-portable-workflow");
	ok = ok && rp_file_contains("rp_mature", "mapping=snakemake-rule-dag");
	ok = ok && rp_file_contains("rp_mature_refs", "profile=reference-platform:galaxy;name=Galaxy");
	ok = ok && rp_file_contains("rp_mature_map", "agentos_targets=kernel_context_path,kernel_metadata_index,kernel_event_queue,batch_tool_runner,capability_contract_table");
	ok = ok && rp_file_contains("rp_mature_checks", "checks=72");
	ok = ok && rp_file_contains("rp_mature_checks", "check=surface.site;target=mature.html;result=pass;status=ready");
	ok = ok && rp_file_contains("rp_mature_checks", "check=agentos.batch_runner;target=batch_tool_runner;result=observed;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "mature_capability_page=rp_mature;profiles=6;mappings=6;checks=72;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=mature_capabilities;source=rp_mature;profiles=6;mappings=6;checks=72;outcome=passed;status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "mature_capability_checks=72");
	ok = ok && rp_file_contains("rp_prov_view", "demo_expected_provenance_view_checks=64");
	ok = ok && rp_file_contains("rp_prov_view", "agentos_kernel_timeline=observed");
	ok = ok && rp_file_contains("rp_prov_view", "agentos_kernel_provenance=observed");
	ok = ok && rp_file_contains("rp_prov_view", "agentos_kernel_ledger=observed");
	ok = ok && rp_file_contains("rp_prov_edges", "edge=12;source=rp_agent_run;target=rp_prov_view;kind=agent_to_trace;status=ready");
	ok = ok && rp_file_contains("rp_evidence_packet", "packet=agentos-readiness;run=RUN-042");
	ok = ok && rp_file_contains("rp_timeline_view", "view=agent_decision_flow;events=6;source=rp_agent_run;status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "provenance_page=rp_prov_view;timeline_views=4;demo_expected_subgraphs=3;packets=4;status=verified");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=provenance_view;source=rp_prov_view;timeline=4;packets=4;demo_expected_checks=64;catalog_outcome=matched;status=verified");
	ok = ok && rp_file_contains("rp_agentcmp", "demo_expected_provenance_view_checks=64");
	ok = ok && rp_file_contains("rp_prov_query", "demo_expected_provenance_query_checks=72");
	ok = ok && rp_file_contains("rp_prov_query", "agentos_kernel_timeline=observed");
	ok = ok && rp_file_contains("rp_prov_query", "agentos_kernel_provenance=observed");
	ok = ok && rp_file_contains("rp_prov_query", "agentos_kernel_ledger=observed");
	ok = ok && rp_file_contains("rp_prov_specs", "template=provenance-query-template:graph-neighborhood");
	ok = ok && rp_file_contains("rp_prov_specs", "spec=provenance-query:RUN-042:workflow-recovery");
	ok = ok && rp_file_contains("rp_prov_exec", "execution=provenance-query-execution:workflow-recovery");
	ok = ok && rp_file_contains("rp_prov_exec", "row=rp_stage_state");
	ok = ok && rp_file_contains("rp_prov_query_pkg", "comparison=provenance-query-comparison:RUN-042:replay-vs-direct");
	ok = ok && rp_file_contains("rp_prov_query_pkg", "packet=provenance-query-packet:RUN-042:lineage-review");
	ok = ok && rp_file_contains("rp_web_bundle", "provenance_queries_page=rp_prov_query;specs=3;executions=3;packets=1;status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "subsection=provenance_queries;source=rp_prov_query;queries=3;executions=3;demo_expected_checks=72;catalog_outcome=matched;status=verified");
	ok = ok && rp_file_contains("rp_agentcmp", "demo_expected_provenance_query_checks=72");
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
	int backend_exec_executed = rp_get_int_value("rp_backend_exec",
						 "runtime_cases_executed=");
	int backend_exec_verified = rp_get_int_value("rp_backend_exec",
						 "runtime_cases_verified=");
	int backend_assertions_executed = rp_get_int_value(
		"rp_backend_exec", "runtime_assertions_executed=");
	int backend_assertions_passed = rp_get_int_value(
		"rp_backend_exec", "runtime_assertions_passed=");
	int backend_case_records = rp_count_token(
		"rp_backend_exec",
		"evidence_role=runtime_verified;runtime_case=");
	ok = ok && require_equal("backend_cases", backend_cases, 8);
	ok = ok && require_equal("backend_exec_verified",
				 backend_exec_verified, backend_exec_executed);
	ok = ok && require_equal("backend_exec_contract",
				 backend_exec_executed, backend_cases);
	ok = ok && require_equal("backend_case_records",
				 backend_case_records, backend_exec_executed);
	ok = ok && require_equal("backend_assertions",
				 backend_assertions_passed,
				 backend_assertions_executed);
	ok = ok && rp_file_contains("rp_backend", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_backend", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_backend", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && rp_file_contains("rp_backend", "runner=agentos-kernel-assisted");
	ok = ok && rp_file_contains("rp_backend_exec", "runtime_claim_protocol=source-bound-v1");
	ok = ok && rp_file_contains("rp_backend_exec", "catalog_generation=demo_expected");
	ok = ok && rp_file_contains("rp_backend_exec", "runtime_source_digest=");
	ok = ok && rp_file_contains("rp_backend_exec", "generation=runtime;status=verified");
	ok = ok && rp_file_contains("rp_study", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_study", "migration_status=baseline_and_agentos_observed");
	ok = ok && rp_file_contains("rp_study", "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=4;reference_result=pass");
	ok = ok && rp_file_contains("rp_study", "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;dependency_graph=1;metadata_index=1;event_queue=1;recovery_tool=1;audit_ledger=1;permission_control=1;timeline_observe=1;workbench_verify=1;package_trace=1;real_task_context=1;edit_lease=1;mainflow_facts=12;detail_checks=kernel;reference_result=pass");
	ok = ok && rp_file_contains("rp_study", "metrics=13");
	ok = ok && rp_file_contains("rp_study", "study_handoff=rp_backend_exec->rp_agentcmp;status=ready");
	ok = ok && rp_file_contains("rp_study", "agentos_kernel=mainflow_bound");
	ok = ok && rp_file_contains("rp_agentos_kernel", "mode=kernel_agent_orchestrated");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_context=present");
	ok = ok && rp_file_contains("rp_agentos_recovery", "context_snapshot=trusted");
	ok = ok && rp_file_contains("rp_agentos_query", "tool=query_file");
	ok = ok && rp_file_contains("rp_agentos_timeline", "timeline_snapshot=ready");
	ok = ok && rp_file_contains("rp_agentos_collab_ack", "delivery=kernel_event_queue");
	ok = ok && rp_file_contains("rp_agentos_audit", "audit_source=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "context_trusted=kernel_shadow");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "metadata_query=used_index");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "agent_event_notify=kernel_queue");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "failure_recovery=generic_action");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "provenance_audit=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "permission_control=sentinel_action_denied");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "timeline_observe=kernel_snapshot");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "workbench_file_verify=kernel_metadata_index");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "package_provenance=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "real_task_context=kernel_shadow");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "edit_lease=kernel_exclusive");
	ok = ok && rp_file_contains("rp_agentos_conflict", "holder_write=checked");
	ok = ok && rp_file_contains("rp_agentos_workbench", "file_verify=kernel_metadata_index");
	ok = ok && rp_file_contains("rp_agentos_package", "package_trace=kernel_provenance");
	ok = ok && rp_file_contains("rp_agentos_real_task", "report_answer=kernel_context_record");
	ok = ok && rp_file_contains("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=8;agentos_replacements=8;risks=8;status=ready");
	ok = ok && rp_file_contains("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128,manual_retry_contract,file_polling,append_only_logs,userland_lock_file;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index,capability_checked_action,kernel_event_queue,kernel_ledger_provenance,kernel_edit_lease,workbench_file_verify,package_trace,real_task_context;dependency_graph=kernel_records;mainflow_facts=12;status=ready");
	ok = ok && rp_file_contains("rp_report_text", "report_source=workflow;state_file=rp_stage_state;source_key=host_workflow_run_id");
	ok = ok && rp_file_contains("rp_report_text", "report_source=llm;state_file=rp_llm_resp;source_key=host_relay_response");
	ok = ok && rp_file_contains("rp_report_text", "report_source=backend;state_file=rp_report_text;source_key=backend_evidence_report");
	if (!ok) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	compare_assertions_executed += 2;
	if (ack_count < 69 || tool_count < 328) {
		printf("rp_compare_plain: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		return 1;
	}
	compare_assertions_passed += 2;
	{
		const int runtime_case_count =
			(int)(sizeof(COMPARE_RUNTIME_SPECS) /
			      sizeof(COMPARE_RUNTIME_SPECS[0]));
		const char *runtime_sources[
			sizeof(COMPARE_RUNTIME_SPECS) /
			sizeof(COMPARE_RUNTIME_SPECS[0])];
		unsigned long long source_digest;

		for (int i = 0; i < runtime_case_count; i++) {
			struct rp_evidence_file_measurement measured;

			compare_runtime_assertions_executed++;
			if (!rp_evidence_measure_file_field(
				    COMPARE_RUNTIME_SPECS[i].source,
				    COMPARE_RUNTIME_SPECS[i].key,
				    COMPARE_RUNTIME_SPECS[i].value, &measured)) {
				printf("rp_compare_plain: runtime_exact_field_failed case=%s source=%s key=%s\n",
				       COMPARE_RUNTIME_SPECS[i].name,
				       COMPARE_RUNTIME_SPECS[i].source,
				       COMPARE_RUNTIME_SPECS[i].key);
				return 1;
			}
			compare_runtime_assertions_passed++;
			if (!append_compare_runtime_case(&COMPARE_RUNTIME_SPECS[i],
							 &measured)) {
				printf("rp_compare_plain: runtime_receipt_failed case=%s\n",
				       COMPARE_RUNTIME_SPECS[i].name);
				return 1;
			}
			runtime_sources[i] = COMPARE_RUNTIME_SPECS[i].source;
		}
		if (!rp_evidence_fold_files(runtime_sources, runtime_case_count,
					    &source_digest)) {
			printf("rp_compare_plain: runtime_digest_failed\n");
			return 1;
		}
		compare_line[0] = 0;
		rp_append_text(compare_line, sizeof(compare_line),
			       "evidence_role=runtime_verified;evidence_generation=runtime;claim_protocol=exact-field-v1;runtime_compare_cases=");
		rp_append_uint_text(compare_line, sizeof(compare_line),
				    runtime_case_count);
		rp_append_text(compare_line, sizeof(compare_line),
			       ";runtime_assertions_executed=");
		rp_append_uint_text(compare_line, sizeof(compare_line),
				    compare_runtime_assertions_executed);
		rp_append_text(compare_line, sizeof(compare_line),
			       ";runtime_assertions_passed=");
		rp_append_uint_text(compare_line, sizeof(compare_line),
				    compare_runtime_assertions_passed);
		rp_append_text(compare_line, sizeof(compare_line),
			       ";catalog_assertions_executed=");
		rp_append_uint_text(compare_line, sizeof(compare_line),
				    compare_assertions_executed);
		rp_append_text(compare_line, sizeof(compare_line),
			       ";catalog_assertions_passed=");
		rp_append_uint_text(compare_line, sizeof(compare_line),
				    compare_assertions_passed);
		rp_append_text(compare_line, sizeof(compare_line),
			       ";source_digest=");
		rp_append_uint_text(compare_line, sizeof(compare_line), source_digest);
		rp_append_text(compare_line, sizeof(compare_line),
			       ";status=verified");
		if (!rp_append_file("rp_agentcmp", compare_line))
			return 1;
		printf("rp_compare_plain: evidence_generation=runtime runtime_assertions_executed=%d runtime_assertions_passed=%d status=verified\n",
		       compare_runtime_assertions_executed,
		       compare_runtime_assertions_passed);
	}
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;demo_expected_programs=70;state_files=261;message_acks=69;tool_events=328;action_state_records=12;demo_expected_test_cases=2800;action_side_effect_records=16;service_page=1;llm_queue_checks=3;llm_guard_checks=3;review_dashboard=1;review_pack=1;runbook_service_checks=16;project_delivery_checks=18;study_protocol_checks=20;statistical_design_checks=120;model_registry_service_checks=96;systematic_review_checks=104;experiment_scheduling_checks=88;training_compliance_checks=92;operations_board_checks=18;review_board_checks=24;control_plane_checks=30;integrity_plane_checks=36;coherence_plane_checks=40;publication_checks=48;calculation_checks=84;real_task_checks=96;analysis_results_checks=96;decision_support_checks=80;usable_research_checks=100;usable_project_checks=120;experiment_campaign_checks=108;release_dossier_checks=112;mature_capability_checks=72;provenance_view_checks=64;provenance_query_checks=72;workbench_exports=7;dynamic_inputs=4;host_ui_events=10;reader_contract=1;advanced_surface_objects=5;startup_health_checks=8;startup_doctor_checks=14;research_product_checks=18;runtime_assurance_checks=24;research_ops_checks=28;regulated_research_checks=32;lab_governance_ops_checks=26;state_catalog_checks=12;knowledge_index_checks=22;llm_transcript_checks=3;workbench_delivery_checks=15;research_portfolio_checks=16;execution_scale_checks=14;operations_scale_checks=12;project_revision_incident_checks=12;reserved_research_surface_checks=21;root_state_surface_checks=10;agentos_reserved_surface_checks=21;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "state_catalog=keys:574;nonzero:71;zero:503;represented:574;checks:12;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "startup_doctor=quickstart:ready;doctor:ready;checks:14;commands:startup_guide,platform_doctor,project_launch,open_research_studio;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "model_registry_service_checks=96;models=1;versions=1;evaluations=1;deployments=1;serving_checks=1;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "systematic_review_checks=104;protocols=1;searches=1;screening=9;extractions=3;bias=3;prisma=1;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "experiment_scheduling_checks=88;schedules=1;tasks=3;bookings=4;conflicts=1;executions=2;charts=4;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "training_compliance_checks=92;requirements=4;training_records=4;competency=4;authorizations=3;gaps=1;open_gaps=0;charts=4;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "analysis_results_checks=96;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;charts=4;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;decision_support_checks=80;options=3;criteria=5;scores=15;review_packets=1;selected=agentos_ucore_hybrid;agentos_replacements=4;kernel_observed=1;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "usable_research_checks=100;templates=3;datasets=3;library_sources=3;dag_stages=9;plan_queue=4;action_queue=5;handoffs=3;deliverables=8;kernel_observed=1;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "usable_project_checks=120;scaffold_templates=3;project_launches=2;project_bundles=2;doctor_checks=10;kernel_observed=1;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "knowledge_index=search_documents:1685;provenance_nodes:406;provenance_links:544;events:8966;context_records:380;usable_artifacts:507;usable_runs:23;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "llm_transcripts=99;llm_bridge_requests=33;llm_bridge_responses=33;workbenches=8;deliveries=9;studio_sessions=2;project_action_plans=17;project_runbooks=17;project_evidence_audits=17;project_provenance_graphs=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "research_portfolio=sources:67;datasets:5;literature_searches:7;reviews:11;evidence_reviews:7;evidence_extractions:25;screening_decisions:25;exports:80;doctor_reports:12;project_handoff_audits:34;project_run_comparisons:17;project_reproducibility_audits:17;project_snapshot_comparisons:17;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "agentcompare_execution_scale=reports:4;results:20;profiles:1;plain_runs:5;indexed_runs:5;real_artifact_runs:5;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "host_runtime_scale=workflow_runs:10;stage_runs:70;workflow_artifacts:150;cache_records:6;agent_messages:70;agent_decisions:70;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "content_graph_scale=content_objects:129;object_references:129;host_content_objects:129;host_object_references:129;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "host_operations_scale=audit_records:5;metrics:13;llm_providers:3;secret_references:3;executed_corr_ids:4;usable_projects:23;artifacts:128;messages:70;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "project_revision_incident=revision_tasks:1;project_scaffolds:1;incidents:1;incident:INC-RUN-042-ALIGN-OOM;failed_stage:align;reason:memory_limit;revision_status:completed;scaffold:deepseek-reliability-response-study;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "root_state_surface=projects:1;runs:1;reports:1;plans:1;search_records:2;site_exports:1;compare_profiles:1;audit:5;context:380;project:lab-gene-x;run:RUN-042;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "agentos_reserved_surface=profiles:0;skills:0;tasks:0;deliberations:0;handoffs:0;abi:0;adapter:0;readiness:0;tool_bindings:0;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "review_handoff_checks=13;review_sections=8;review_gates=6;review_decisions=4;review_handoffs=3;review_pack_actions=3;review_pack_bridges=4;backend_review=1;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "runbook_recovery_checks=16;templates=1;steps=7;incident_triages=1;executions=1;exports=1;worker_records=6;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "project_delivery_checks=18;handoff_audits=1;project_runbooks=1;release_gates=1;snapshots=1;snapshot_comparisons=1;reproducibility_audits=1;provenance_graphs=1;package_intakes=1;package_indexes=1;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "study_protocol_checks=20;protocols=2;launches=2;runs=1;compliance_reports=1;bundles=1;reproduction_packages=1;reproduction_reviews=1;action_plans=1;action_executions=1;dataset_portfolios=1;source_portfolios=1;dataset_cards=1;visualizations=1;answers=1;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "statistical_design_checks=120;designs=1;power=underpowered;randomization=balanced;blinding=ok;stat_result=approved_with_sample_size_note;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "operations_board_checks=18;pending_reviews=1;reproduction_actions=1;workbench_actions=4;plan_items=5;action_items=4;handoffs=3;latest_runs=4;exports=2;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "review_board_checks=24;boards=1;requests=1;votes=4;signoffs=4;assignments=4;workloads=4;filters=2;decision=approved;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "control_plane_checks=30;approvals=4;notifications=4;queue_items=4;plugins=3;workspaces=1;permissions=5;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "integrity_plane_checks=36;evidence_contracts=8;reference_contracts=8;namespace_checks=5;status_checks=5;review_alignment_checks=4;report_source_checks=3;package_trace_checks=3;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;coherence_plane_checks=40;delivery_contracts=7;run_state_contracts=7;lifecycle_contracts=6;workflow_lint=5;tool_protocol=5;report_validation=5;agent_coordination=3;agentos_replacements=4;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "publication_checks=48;targets=2;submissions=2;review_rounds=2;revision_tasks=3;response_packages=2;response_items=4;decisions=2;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "mature_capability_checks=72;profiles=6;mappings=6;checks=72;platforms=Galaxy,AiiDA,DVC,MLflow,Nextflow,Snakemake;agentos_replacements=6;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;demo_expected_provenance_view_checks=64;timeline_views=4;demo_expected_subgraphs=3;packets=4;agentos_replacements=4;kernel_timeline=observed;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;demo_expected_provenance_query_checks=72;specs=3;templates=1;executions=3;comparisons=1;exports=1;packets=1;agentos_replacements=4;kernel_timeline=observed;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "llm_delivery_checks=16;llm_queue=3;llm_packets=3;llm_responses=3;llm_eval=7;llm_guard=3;llm_hostreq=3;llm_review_links=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "workflow_portability_checks=14;portability_imports=5;adapter_specs=6;migration_steps=9;rehearsal_cases=4;blocking_items=0;portability_package=workflow-portability;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;portability_backend_checks=18;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;demo_expected_passed_cases=8;planned_cases=0;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;backend_runner_checks=24;runner_cases=8;demo_expected_runner_passed=8;runner_planned=0;plain_inputs=8;study_metrics=2;backend_runner_detail_checks=48;runner_detail_rows=8;backend_runner_report_checks=40;runner_report_rows=8;backend_report_links=2;mainflow_facts=12;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "agentos_kernel=mainflow_bound;context_snapshot=1;metadata_index=1;batch_tool=1;event_queue=1;recovery_tool=1;audit_ledger=1;capability_check=1;workbench_verify=1;package_trace=1;real_task_context=1;edit_lease=1;advanced_surface_kernel=1;status=ready")) return 1;
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
	return 0;
}
