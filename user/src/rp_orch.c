#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <research_platform_state.h>

static const char *PROGRAMS[] = {
	"rp_catalog",
	"rp_object_store",
	"rp_object_query",
	"rp_lineage",
	"rp_site_export",
	"rp_planner",
	"rp_portability",
	"rp_retriever",
	"rp_analyst",
	"rp_reviewer",
	"rp_lab",
	"rp_governance",
	"rp_writer",
	"rp_repair",
	"rp_auditor",
	"rp_query",
	"rp_evidence",
	"rp_llm_bridge",
	"rp_llm_relay",
	"rp_privacy",
	"rp_runconf",
	"rp_execobs",
	"rp_invoke",
	"rp_complete",
	"rp_artifact_ops",
	"rp_data_pipeline",
	"rp_workflow_runner",
	"rp_workbench",
	"rp_agent_collab",
	"rp_package",
	"rp_delta",
	"rp_release",
	"rp_dossier",
	"rp_service_surface",
	"rp_notebook_export",
	"rp_backend",
	"rp_consistency",
	"rp_metrics",
	"rp_ui_export",
	"rp_web_export",
	"rp_test_suite",
	"rp_compare_plain",
};

static int run_child(const char *program)
{
	int pid = fork();
	if (pid == 0) {
		char *argv[] = { (char *)program, 0 };
		if (exec(program, argv) < 0) {
			printf("rp_orch: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		return 0;
	}
	return 1;
}

int main(void)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	printf("rp_orch: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(PROGRAMS[i]);
	}
	printf("rp_orch: programs_ok=%d programs_total=%d\n", ok, total);
	if (ok != total) {
		printf("rp_orch: failed\n");
		return 1;
	}
	int state_ok = 1;
	state_ok = state_ok && rp_file_contains("rp_status", "catalog=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "object_store=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "object_query=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "lineage=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "site_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "planner=planned");
	state_ok = state_ok && rp_file_contains("rp_status", "mail=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "schedule=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "taskrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "budget=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "wfio=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "portability=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "adapters=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "migration=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "port_rehearsal=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "port_review=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "policy=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "retriever=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "analyst=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "datadict=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dataprof=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compute=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "figrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "failure=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "samples=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "quality=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewer=accepted");
	state_ok = state_ok && rp_file_contains("rp_status", "review2=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "protocol=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "sop=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "experiment=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "trialrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "labops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "training=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "lab_resources=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "instrument_registry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "inventory=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "procurement=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "resource_schedule=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "governance=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "risk=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "capa=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "writer=packaged");
	state_ok = state_ok && rp_file_contains("rp_status", "revision=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "repair=recovered");
	state_ok = state_ok && rp_file_contains("rp_status", "retry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "telemetry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "auditor=passed");
	state_ok = state_ok && rp_file_contains("rp_status", "query=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "rank=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "runview=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "evidence=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "claimrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "provpath=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "knowledge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "knowledge_services=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "lit_review=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "citation_graph=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "semantic_index=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "knowledge_answers=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llm_bridge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmqueue=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "relay=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "promptops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmtrace=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmeval=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmrelay=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "relay_packets=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "relay_routes=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "relay_guard=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "relay_fallback=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "privacy=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compliance=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "bio_services=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "sample_registry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ethics_review=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "access_requests=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "cohort_view=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "params=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "runconf=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "configval=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "configdrift=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "execplan=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "worker=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "timeline=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "execobs=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "invocation=recovered");
	state_ok = state_ok && rp_file_contains("rp_status", "steps=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "attempts=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "invoke_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "hooks=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "completion=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "actions=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "complete_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "input=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "request_form=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "upload_files=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "data_pipeline=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ingest_files=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dataset_snapshot=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "data_preview=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "data_quality=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "data_transform=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dataset_collection=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "runner=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "stage_dag=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "artifact_ops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "workflow_runner=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "workbench=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "workbench_tasks=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "workspace_inspection=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "workspace_import_service=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "workbench_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "stage_state=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "cache_index=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "retry_plan=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "artifact_manifest=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "agents=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "decisions=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "handoff=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "deliberation=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "collab=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "runtime_services=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "runtime_env=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "notebook_exec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "notebook_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "notebook_package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "download_manifest=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "eln_record=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "worker_pool=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "datarel=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dataver=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "repro=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "delivery_manifest=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "export_bundle=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "review_page=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "human_review=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "revision_task_package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "diff=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "delta=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "release=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dossier=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "submit=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "publication_services=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "result_review=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "publication_plan=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "peer_review_response=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "fair_package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "agentcmp=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "health=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ui_home=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ui_run=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ui_agent=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ui_evidence=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "ui_compare=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "web_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "web_routes=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "web_bundle=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_home=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_run=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_agents=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_evidence=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_compare=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_artifacts=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_data=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_bio=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_lab_resources=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_publication=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_knowledge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_runtime=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "api_actions=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "action_validation=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "action_side_effects=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "actionio=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "usable_research=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "action_exports=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "tests=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "backend=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "backend_exec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "backend_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "study=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "consistency=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compare=ready");
	state_ok = state_ok && rp_file_contains("rp_audit", "status=passed");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "plain_kernel=passed");
	state_ok = state_ok && rp_file_contains("rp_package", "package_manifest=ready");
	state_ok = state_ok && rp_file_contains("rp_package", "downloadable_units=3");
	state_ok = state_ok && rp_file_contains("rp_package", "custom_sources=rp_input,rp_runner,rp_uresrun");
	state_ok = state_ok && rp_file_contains("rp_package", "workbench=rp_runner");
	state_ok = state_ok && rp_file_contains("rp_package", "workspace_imports=1");
	state_ok = state_ok && rp_file_contains("rp_package", "delivery_manifest=rp_package");
	state_ok = state_ok && rp_file_contains("rp_package", "delivery_files=8");
	state_ok = state_ok && rp_file_contains("rp_package", "delivery_checks=3");
	state_ok = state_ok && rp_file_contains("rp_package", "delivery_check=human_review;status=pass");
	state_ok = state_ok && rp_file_contains("rp_package", "delivery_manifest_json=delivery-manifest.json");
	state_ok = state_ok && rp_file_contains("rp_package", "evidence_bundle_zip=research-evidence-bundle.zip");
	state_ok = state_ok && rp_file_contains("rp_package", "evidence_bundle_entries=12");
	state_ok = state_ok && rp_file_contains("rp_package", "deliverables=8");
	state_ok = state_ok && rp_file_contains("rp_package", "raw_links=5");
	state_ok = state_ok && rp_file_contains("rp_package", "decision_controls=2");
	state_ok = state_ok && rp_file_contains("rp_package", "human_reviews=1");
	state_ok = state_ok && rp_file_contains("rp_package", "revision_tasks=1");
	state_ok = state_ok && rp_file_contains("rp_package", "revision_change_count=2");
	state_ok = state_ok && rp_file_contains("rp_package", "revision_evidence=rp_revision");
	state_ok = state_ok && rp_file_contains("rp_package", "review_action_items=2");
	state_ok = state_ok && rp_file_contains("rp_package", "llm_matched_responses=3");
	state_ok = state_ok && rp_file_contains("rp_package", "evidence_protocols=1");
	state_ok = state_ok && rp_file_contains("rp_package", "evidence_extractions=3");
	state_ok = state_ok && rp_file_contains("rp_package", "workflow_portability=rp_wfio");
	state_ok = state_ok && rp_file_contains("rp_package", "portability_exports=5");
	state_ok = state_ok && rp_file_contains("rp_package", "migration_steps=9");
	state_ok = state_ok && rp_file_contains("rp_object_query", "hits=8");
	state_ok = state_ok && rp_file_contains("rp_lineage", "edges=7");
	state_ok = state_ok && rp_file_contains("rp_site", "pages=42");
	state_ok = state_ok && rp_file_contains("rp_site", "page=agentos_readiness");
	state_ok = state_ok && rp_file_contains("rp_llm_resp", "responses=3");
	state_ok = state_ok && rp_file_contains("rp_release", "decision=release");
	state_ok = state_ok && rp_file_contains("rp_dossier", "sections=36");
	state_ok = state_ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	state_ok = state_ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	state_ok = state_ok && rp_file_contains("rp_claimrec", "claim=8");
	state_ok = state_ok && rp_file_contains("rp_provpath", "critical_paths=3");
	state_ok = state_ok && rp_file_contains("rp_datarel", "fair=passed");
	state_ok = state_ok && rp_file_contains("rp_dataver", "release_candidate=v2");
	state_ok = state_ok && rp_file_contains("rp_reviewops", "governance=passed");
	state_ok = state_ok && rp_file_contains("rp_wfio", "portable_steps=10");
	state_ok = state_ok && rp_file_contains("rp_wfio", "imports=5");
	state_ok = state_ok && rp_file_contains("rp_wfio", "format=nextflow");
	state_ok = state_ok && rp_file_contains("rp_wfio", "adapter_specs=6");
	state_ok = state_ok && rp_file_contains("rp_wfio", "migration_steps=9");
	state_ok = state_ok && rp_file_contains("rp_wfio", "cases=4");
	state_ok = state_ok && rp_file_contains("rp_wfio", "decision=ready_for_agentos");
	state_ok = state_ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	state_ok = state_ok && rp_file_contains("rp_policy", "access_profiles=4");
	state_ok = state_ok && rp_file_contains("rp_compliance", "decision=accepted");
	state_ok = state_ok && rp_file_contains("rp_review2", "remaining_blockers=0");
	state_ok = state_ok && rp_file_contains("rp_review2", "review_threads=2");
	state_ok = state_ok && rp_file_contains("rp_review2", "action_items=2");
	state_ok = state_ok && rp_file_contains("rp_review2", "review_summary=all_review_comments_resolved");
	state_ok = state_ok && rp_file_contains("rp_review2", "human_review=usable-review:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_review2", "requested_change=methods_retry_scope");
	state_ok = state_ok && rp_file_contains("rp_review2", "requested_change=chart_caption");
	state_ok = state_ok && rp_file_contains("rp_revision", "draft_versions=3");
	state_ok = state_ok && rp_file_contains("rp_revision", "applied_changes=2");
	state_ok = state_ok && rp_file_contains("rp_revision", "report_delta=methods_and_caption_updated");
	state_ok = state_ok && rp_file_contains("rp_sched", "queue_items=21");
	state_ok = state_ok && rp_file_contains("rp_taskrec", "msg=21");
	state_ok = state_ok && rp_file_contains("rp_rank", "selected=10");
	state_ok = state_ok && rp_file_contains("rp_runview", "ranked_tasks=21");
	state_ok = state_ok && rp_file_contains("rp_budget", "decision=within_budget");
	state_ok = state_ok && rp_file_contains("rp_fail", "recoverable=1");
	state_ok = state_ok && rp_file_contains("rp_retrylog", "attempts=2");
	state_ok = state_ok && rp_file_contains("rp_relay", "network_stack=host_only");
	state_ok = state_ok && rp_file_contains("rp_relay", "relay_packets=3");
	state_ok = state_ok && rp_file_contains("rp_runview", "budget_state=within_budget");
	state_ok = state_ok && rp_file_contains("rp_health", "healthy=4");
	state_ok = state_ok && rp_file_contains("rp_datadic", "schema_drift=0");
	state_ok = state_ok && rp_file_contains("rp_dataprof", "profiles=4");
	state_ok = state_ok && rp_file_contains("rp_compute", "replay=ready");
	state_ok = state_ok && rp_file_contains("rp_figrec", "exported=3");
	state_ok = state_ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	state_ok = state_ok && rp_file_contains("rp_risk", "open_risks=0");
	state_ok = state_ok && rp_file_contains("rp_capa", "verifications=2");
	state_ok = state_ok && rp_file_contains("rp_diff", "changed_items=20");
	state_ok = state_ok && rp_file_contains("rp_delta", "decision=accepted");
	state_ok = state_ok && rp_file_contains("rp_labops", "maintenance=passed");
	state_ok = state_ok && rp_file_contains("rp_training", "competency_checks=3");
	state_ok = state_ok && rp_file_contains("rp_instr", "instruments=4");
	state_ok = state_ok && rp_file_contains("rp_invent", "inventory_items=9");
	state_ok = state_ok && rp_file_contains("rp_procure", "requests=3");
	state_ok = state_ok && rp_file_contains("rp_ressched", "bookings=6");
	state_ok = state_ok && rp_file_contains("rp_labresop", "ops=6");
	state_ok = state_ok && rp_file_contains("rp_prompt", "routes=4");
	state_ok = state_ok && rp_file_contains("rp_llmq", "queued=3");
	state_ok = state_ok && rp_file_contains("rp_llmeval", "passed=7");
	state_ok = state_ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	state_ok = state_ok && rp_file_contains("rp_llmlog", "request_packets=3");
	state_ok = state_ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	state_ok = state_ok && rp_file_contains("rp_lit", "literature_search=usable-literature-search:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_llm_packets", "packets=3");
	state_ok = state_ok && rp_file_contains("rp_llm_packets", "matched_responses=3");
	state_ok = state_ok && rp_file_contains("rp_llm_packets", "roundtrip=ready");
	state_ok = state_ok && rp_file_contains("rp_llm_routes", "routes=4");
	state_ok = state_ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	state_ok = state_ok && rp_file_contains("rp_llm_hostreq", "cloud_mode=optional_host_side");
	state_ok = state_ok && rp_file_contains("rp_llm_hostreq", "roundtrip=ready");
	state_ok = state_ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	state_ok = state_ok && rp_file_contains("rp_llm_fallback", "offline_template_verified=1");
	state_ok = state_ok && rp_file_contains("rp_llm_resp", "host_relay_roundtrip=ready");
	state_ok = state_ok && rp_file_contains("rp_sreg", "samples=8");
	state_ok = state_ok && rp_file_contains("rp_ethics", "consent_forms=6");
	state_ok = state_ok && rp_file_contains("rp_access", "approved=2");
	state_ok = state_ok && rp_file_contains("rp_cohort", "cohorts=2");
	state_ok = state_ok && rp_file_contains("rp_bioop", "op=access_decision");
	state_ok = state_ok && rp_file_contains("rp_execobs", "timeline_events=9");
	state_ok = state_ok && rp_file_contains("rp_worker", "heartbeats=4");
	state_ok = state_ok && rp_file_contains("rp_runconf", "profiles=2");
	state_ok = state_ok && rp_file_contains("rp_configval", "validations=2");
	state_ok = state_ok && rp_file_contains("rp_invocation", "status=recovered");
	state_ok = state_ok && rp_file_contains("rp_completion", "actions=4");
	state_ok = state_ok && rp_file_contains("rp_backend", "cases=4");
	state_ok = state_ok && rp_file_contains("rp_backend_exec", "passed_cases=2");
	state_ok = state_ok && rp_file_contains("rp_consistency", "state_relation=passed");
	state_ok = state_ok && rp_file_contains("rp_consistency", "checks=101");
	state_ok = state_ok && rp_file_contains("rp_consistency", "coherence_checks=9");
	state_ok = state_ok && rp_file_contains("rp_ingest_files", "files=2");
	state_ok = state_ok && rp_file_contains("rp_ingest_files", "derived_items=5");
	state_ok = state_ok && rp_file_contains("rp_dataset_snapshot", "snapshots=2");
	state_ok = state_ok && rp_file_contains("rp_dataset_snapshot", "normalized_fastq=rp_artifact:rp_normalized_fastq");
	state_ok = state_ok && rp_file_contains("rp_data_preview", "previews=2");
	state_ok = state_ok && rp_file_contains("rp_data_quality", "passed=7");
	state_ok = state_ok && rp_file_contains("rp_data_transform", "transforms=2");
	state_ok = state_ok && rp_file_contains("rp_data_transform", "derived=alignment");
	state_ok = state_ok && rp_file_contains("rp_dataset_collection", "items=4");
	state_ok = state_ok && rp_file_contains("rp_runner", "stages=5");
	state_ok = state_ok && rp_file_contains("rp_runner", "real_artifact_items=5");
	state_ok = state_ok && rp_file_contains("rp_stage_state", "stages=5");
	state_ok = state_ok && rp_file_contains("rp_stage_state", "dependency_checks=5");
	state_ok = state_ok && rp_file_contains("rp_stage_state", "command=align:agent-align");
	state_ok = state_ok && rp_file_contains("rp_stage_state", "output=rp_artifact:rp_align_table");
	state_ok = state_ok && rp_file_contains("rp_stage_state", "output=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv");
	state_ok = state_ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	state_ok = state_ok && rp_file_contains("rp_cache_index", "cache_policy=content_keyed");
	state_ok = state_ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	state_ok = state_ok && rp_file_contains("rp_retry_plan", "failure_reason=tool_output_missing");
	state_ok = state_ok && rp_file_contains("rp_run_events", "events=8");
	state_ok = state_ok && rp_file_contains("rp_run_events", "decision=retry_align_only");
	state_ok = state_ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	state_ok = state_ok && rp_file_contains("rp_artifact_manifest", "real_artifact_items=5");
	state_ok = state_ok && rp_file_contains("rp_artifact_manifest", "support_entries=2");
	state_ok = state_ok && rp_file_contains("rp_stage_log", "status=ready");
	state_ok = state_ok && rp_file_contains("rp_artifact", "status=recovered");
	state_ok = state_ok && rp_file_contains("rp_artifact", "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA");
	state_ok = state_ok && rp_file_contains("rp_artifact", "section=rp_align_table");
	state_ok = state_ok && rp_file_contains("rp_artifact", "\"variants\":2");
	state_ok = state_ok && rp_file_contains("rp_artifact", "section=rp_gene_counts_csv;geneA=18");
	state_ok = state_ok && rp_file_contains("rp_artifact", "section=rp_archive_manifest;files=5");
	state_ok = state_ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	state_ok = state_ok && rp_file_contains("rp_input", "custom_requests=3");
	state_ok = state_ok && rp_file_contains("rp_input", "custom_run_2=usable-run:RUN-901");
	state_ok = state_ok && rp_file_contains("rp_input", "custom_run_3=usable-run:RUN-902");
	state_ok = state_ok && rp_file_contains("rp_input", "custom_dataset_rows=3");
	state_ok = state_ok && rp_file_contains("rp_input", "form_fields=8");
	state_ok = state_ok && rp_file_contains("rp_input", "csv_rows_total=9");
	state_ok = state_ok && rp_file_contains("rp_input", "library_sources=1");
	state_ok = state_ok && rp_file_contains("rp_runner", "custom_source=rp_input");
	state_ok = state_ok && rp_file_contains("rp_runner", "custom_runs=3");
	state_ok = state_ok && rp_file_contains("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	state_ok = state_ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	state_ok = state_ok && rp_file_contains("rp_runner", "workbench_task_done=8");
	state_ok = state_ok && rp_file_contains("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_runner", "custom_status=ok");
	state_ok = state_ok && rp_file_contains("rp_runner", "revision_status=completed");
	state_ok = state_ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	state_ok = state_ok && rp_file_contains("rp_runner", "citation_plan_entries=3");
	state_ok = state_ok && rp_file_contains("rp_agents", "agents=7");
	state_ok = state_ok && rp_file_contains("rp_decisions", "decisions=8");
	state_ok = state_ok && rp_file_contains("rp_handoff", "handoffs=6");
	state_ok = state_ok && rp_file_contains("rp_deliberation", "items=5");
	state_ok = state_ok && rp_file_contains("rp_agent_run", "agent_messages=21");
	state_ok = state_ok && rp_file_contains("rp_runenv", "environments=4");
	state_ok = state_ok && rp_file_contains("rp_nbexec", "executed_cells=8");
	state_ok = state_ok && rp_file_contains("rp_nbexec", "notebook=reproducible-analysis.ipynb");
	state_ok = state_ok && rp_file_contains("rp_nbexec", "exports=2");
	state_ok = state_ok && rp_file_contains("rp_repro", "downloadable_units=4");
	state_ok = state_ok && rp_file_contains("rp_eln", "integrity_checks=3");
	state_ok = state_ok && rp_file_contains("rp_wpool", "worker_pools=2");
	state_ok = state_ok && rp_file_contains("rp_runop", "op=notebook_replay");
	state_ok = state_ok && rp_file_contains("rp_ui_home", "page=home");
	state_ok = state_ok && rp_file_contains("rp_ui_home", "nav_items=12");
	state_ok = state_ok && rp_file_contains("rp_ui_home", "static_site_pages=42");
	state_ok = state_ok && rp_file_contains("rp_ui_run", "run_id=RUN-042");
	state_ok = state_ok && rp_file_contains("rp_ui_run", "timeline_rows=5");
	state_ok = state_ok && rp_file_contains("rp_ui_run", "artifact_preview=rp_report_text,rp_chart_data,rp_artifact");
	state_ok = state_ok && rp_file_contains("rp_ui_run", "review_threads=2");
	state_ok = state_ok && rp_file_contains("rp_ui_run", "revision_delta=rp_revision");
	state_ok = state_ok && rp_file_contains("rp_ui_agent", "decision_rows=8");
	state_ok = state_ok && rp_file_contains("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	state_ok = state_ok && rp_file_contains("rp_ui_evidence", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_ui_compare", "pain_file_scans=128");
	state_ok = state_ok && rp_file_contains("rp_ui_compare", "metric_rows=8");
	state_ok = state_ok && rp_file_contains("rp_web_routes", "routes=22");
	state_ok = state_ok && rp_file_contains("rp_web_routes", "get_routes=14");
	state_ok = state_ok && rp_file_contains("rp_api_home", "api=home");
	state_ok = state_ok && rp_file_contains("rp_api_home", "custom_run=usable-run:RUN-900");
	state_ok = state_ok && rp_file_contains("rp_api_home", "custom_runs=3");
	state_ok = state_ok && rp_file_contains("rp_api_home", "nav_items=12");
	state_ok = state_ok && rp_file_contains("rp_api_home", "static_site_pages=42");
	state_ok = state_ok && rp_file_contains("rp_api_run", "runner_exec_files=5");
	state_ok = state_ok && rp_file_contains("rp_api_run", "custom_research=rp_runner");
	state_ok = state_ok && rp_file_contains("rp_api_run", "custom_research_runs=3");
	state_ok = state_ok && rp_file_contains("rp_api_run", "request_form=rp_input");
	state_ok = state_ok && rp_file_contains("rp_api_run", "delivery_manifest=rp_package");
	state_ok = state_ok && rp_file_contains("rp_api_run", "delivery_files=8");
	state_ok = state_ok && rp_file_contains("rp_api_run", "delivery_checks=3");
	state_ok = state_ok && rp_file_contains("rp_api_run", "evidence_bundle_zip=research-evidence-bundle.zip");
	state_ok = state_ok && rp_file_contains("rp_api_run", "llm_roundtrip=ready");
	state_ok = state_ok && rp_file_contains("rp_api_run", "bibliography=rp_runner");
	state_ok = state_ok && rp_file_contains("rp_api_run", "review_action_items=2");
	state_ok = state_ok && rp_file_contains("rp_api_run", "revision_delta=rp_revision");
	state_ok = state_ok && rp_file_contains("rp_api_run", "timeline_rows=5");
	state_ok = state_ok && rp_file_contains("rp_api_run", "dependency_checks=5");
	state_ok = state_ok && rp_file_contains("rp_api_run", "manifest_support_entries=2");
	state_ok = state_ok && rp_file_contains("rp_api_agents", "agents=7");
	state_ok = state_ok && rp_file_contains("rp_api_evidence", "provenance_paths=3");
	state_ok = state_ok && rp_file_contains("rp_api_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	state_ok = state_ok && rp_file_contains("rp_api_compare", "workflow_runner_files=5");
	state_ok = state_ok && rp_file_contains("rp_api_compare", "coherence_checks=9");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "manifest_records=4");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "evidence_package=rp_package");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "export_bundle=rp_package");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "delivery_files=8");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "delivery_checks=3");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "evidence_bundle_entries=12");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "llm_matched_responses=3");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "library_sources=rp_knowledge");
	state_ok = state_ok && rp_file_contains("rp_api_artifacts", "preview_files=rp_report_text,rp_chart_data,rp_artifact");
	state_ok = state_ok && rp_file_contains("rp_api_data", "dataset_snapshots=2");
	state_ok = state_ok && rp_file_contains("rp_api_bio", "sample_registry=rp_sreg");
	state_ok = state_ok && rp_file_contains("rp_api_labres", "instrument_registry=rp_instr");
	state_ok = state_ok && rp_file_contains("rp_api_pub", "result_review=rp_resrev");
	state_ok = state_ok && rp_file_contains("rp_api_know", "semantic_index=rp_semindex");
	state_ok = state_ok && rp_file_contains("rp_api_know", "evidence_protocols=1");
	state_ok = state_ok && rp_file_contains("rp_api_runtime", "runtime_env=rp_runenv");
	state_ok = state_ok && rp_file_contains("rp_api_action", "actions=8");
	state_ok = state_ok && rp_file_contains("rp_api_action", "revision_task_runner=1");
	state_ok = state_ok && rp_file_contains("rp_api_action", "validated_requests=8");
	state_ok = state_ok && rp_file_contains("rp_api_action", "precondition_checks=8");
	state_ok = state_ok && rp_file_contains("rp_api_action", "side_effect_records=16");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "api_payloads=14");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "downloadable_units=3");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "static_site_pages=42");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "action_validation=passed");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "download_manifest_generated=1");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "render_sections=7");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "artifact_previews=3");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "runner_detail_fields=16");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "delivery_manifest=rp_package");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "delivery_files=8");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "delivery_checks=3");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "evidence_bundle_entries=12");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "llm_roundtrip=ready");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "export_bundle=rp_package");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "revision_delta=rp_revision");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "library_sources=rp_knowledge");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "workspace_imports=1");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "workbench=rp_runner");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "evidence_protocols=1");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "coherence_checks=9");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "custom_research_files=1");
	state_ok = state_ok && rp_file_contains("rp_web_bundle", "review_threads=2");
	state_ok = state_ok && rp_file_contains("rp_actionio", "requests=8");
	state_ok = state_ok && rp_file_contains("rp_actionio", "responses=8");
	state_ok = state_ok && rp_file_contains("rp_actionio", "actions=8");
	state_ok = state_ok && rp_file_contains("rp_actionio", "effect=revision_run_created");
	state_ok = state_ok && rp_file_contains("rp_actionio", "applied_changes=2");
	state_ok = state_ok && rp_file_contains("rp_actionio", "revision_status=completed");
	state_ok = state_ok && rp_file_contains("rp_actionio", "dataset_file=rp_input");
	state_ok = state_ok && rp_file_contains("rp_actionio", "generated_runs=3");
	state_ok = state_ok && rp_file_contains("rp_actionio", "tag=reusable");
	state_ok = state_ok && rp_file_contains("rp_actionio", "request_validation=passed");
	state_ok = state_ok && rp_file_contains("rp_actionio", "precondition_checks=8");
	state_ok = state_ok && rp_file_contains("rp_actionio", "precheck=4;path=/actions/research/run");
	state_ok = state_ok && rp_file_contains("rp_actionio", "side_effect_records=16");
	state_ok = state_ok && rp_file_contains("rp_actionio", "state_write=10;target=rp_package;field=download_manifest");
	state_ok = state_ok && rp_file_contains("rp_actionio", "idempotency_checks=8");
	state_ok = state_ok && rp_file_contains("rp_actionio", "post_action_state=rp_stage_state,rp_package,rp_runner,rp_revision,rp_agentcmp");
	state_ok = state_ok && rp_file_contains("rp_actionio", "download_manifest_generated=1");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "artifacts=36");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "runs=3");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "run_id_2=usable-run:RUN-901");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "revision_run=usable-run:RUN-900-rev1");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "source_run=rp_runner");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "source_form=rp_input");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "export_bundle=rp_package");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "library_sources=rp_knowledge");
	state_ok = state_ok && rp_file_contains("rp_uresrun", "dataset_rows_total=9");
	state_ok = state_ok && rp_file_contains("rp_actionio", "Comparison Metrics");
	state_ok = state_ok && rp_file_contains("rp_actionio", "passed_cases=3");
	state_ok = state_ok && rp_file_contains("rp_tests", "tests=548");
	state_ok = state_ok && rp_file_contains("rp_tests", "static_site=passed");
	state_ok = state_ok && rp_file_contains("rp_tests", "workflow_portability=passed");
	state_ok = state_ok && rp_file_contains("rp_tests", "coherence=passed");
	state_ok = state_ok && rp_file_contains("rp_tests", "status=passed");
	state_ok = state_ok && rp_file_contains("rp_litrev", "papers=9");
	state_ok = state_ok && rp_file_contains("rp_litrev", "evidence_extractions=3");
	state_ok = state_ok && rp_file_contains("rp_litrev", "prisma_flow=usable-prisma-flow:RUN-900:1");
	state_ok = state_ok && rp_file_contains("rp_citegraph", "bibtex_entries=9");
	state_ok = state_ok && rp_file_contains("rp_semindex", "documents=17");
	state_ok = state_ok && rp_file_contains("rp_kanswers", "answers=4");
	state_ok = state_ok && rp_file_contains("rp_knowop", "op=llm_grounding");
	state_ok = state_ok && rp_file_contains("rp_resrev", "accepted=10");
	state_ok = state_ok && rp_file_contains("rp_pubplan", "journal_targets=2");
	state_ok = state_ok && rp_file_contains("rp_peerresp", "responses=6");
	state_ok = state_ok && rp_file_contains("rp_fairpkg", "fair_checks=8");
	state_ok = state_ok && rp_file_contains("rp_pubop", "op=submission_bundle");
	state_ok = state_ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	state_ok = state_ok && rp_file_contains("rp_submit", "review_response=ready");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "message_acks=35");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "tool_events=115");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "scheduler_items=21");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "ranked_tasks=21");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "selected_tasks=10");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "policy_checks=8");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "compliance=accepted");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "risk_items=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "capa_actions=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "delta_items=20");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "diff_records=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "claim_records=8");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "provenance_paths=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "data_profiles=4");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "figure_records=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "trial_records=4");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "workflow_exports=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "migration_steps=9");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "portability_rehearsal_cases=4");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "review_rounds=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "data_versions=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "data_pipeline_files=6");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "data_quality_checks=7");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "retry_attempts=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "relay_packets=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "llm_requests=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "llm_eval_passed=7");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "run_views=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "timeline_events=9");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "worker_heartbeats=4");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "workflow_invocations=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "completion_actions=4");
	state_ok = state_ok && rp_file_contains("rp_backend_export", "exports=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "health_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "agent_roles=7");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "collaboration_decisions=8");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "handoffs=6");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "relay_protocol_files=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "workflow_runner_files=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "bio_service_files=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "lab_resource_files=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "knowledge_service_files=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "runtime_service_files=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "notebook_exports=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "publication_service_files=5");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=lab_resources;msg=res;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=knowledge_services;msg=know;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=bio_services;msg=bio;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=runtime_services;msg=run;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=publication_services;msg=pub;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=portability;msg=wf;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=consistency;msg=22;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=artifact_ops;msg=artifact;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=data_pipeline;msg=data;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=workflow_runner;msg=runner;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=workbench;msg=research_board;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=agent_collab;msg=agents;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=llm_relay;msg=relay;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=ui_export;msg=ui;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=web_export;msg=web;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=api_actions;msg=action;status=ready");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=test_suite;msg=test;status=passed");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=portability.plan_migration");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=consistency.check_tasks");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=artifact_ops.write_artifact");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=data_pipeline.collection");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=workflow_runner.write_stage_state");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=agent_collab.write_decisions");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=llm_relay.write_packets");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=ui_export.write_compare");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=web_export.write_bundle");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=test_suite.check_artifacts");
	state_ok = state_ok && (rp_count_lines("rp_ack") >= 37);
	state_ok = state_ok && (rp_count_lines("rp_tool") >= 144);
	state_ok = state_ok && rp_file_contains("rp_protocol", "ethics=approved");
	printf("rp_orch: state_ok=%d\n", state_ok);
	if (!state_ok) {
		printf("rp_orch: failed\n");
		return 1;
	}
	printf("rp_orch: passed\n");
	return 0;
}
