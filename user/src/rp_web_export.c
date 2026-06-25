#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_ui_home", "page=home");
	ok = ok && rp_file_contains("rp_ui_run", "runner_exec=");
	ok = ok && rp_file_contains("rp_ui_agent", "decisions=8");
	ok = ok && rp_file_contains("rp_ui_evidence", "stage_log=rp_stage_log");
	ok = ok && rp_file_contains("rp_ui_compare", "page=compare-metrics");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_llm_hostreq", "template_mode=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "status=ready");
	ok = ok && rp_file_contains("rp_package", "package_manifest=ready");
	ok = ok && rp_file_contains("rp_package", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_runner", "custom_runs=3");
	ok = ok && rp_file_contains("rp_runner", "custom_status=ok");
	ok = ok && rp_file_contains("rp_sreg", "samples=8");
	ok = ok && rp_file_contains("rp_instr", "instruments=4");
	ok = ok && rp_file_contains("rp_resrev", "review_items=10");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	if (!ok) return 1;

	if (!rp_write_file("rp_web_routes",
			   "service=host-web-ui\n"
			   "routes=18\n"
			   "get_routes=13\n"
			   "post_routes=5\n"
			   "route=/;payload=rp_api_home;status=ready\n"
			   "route=/run/RUN-042;payload=rp_api_run;status=ready\n"
			   "route=/research/{run_id};payload=rp_uresrun;status=ready\n"
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
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_home",
			   "api=home\n"
			   "title=Research Agent Platform\n"
			   "run_id=RUN-042\n"
			   "custom_run=usable-run:RUN-900\n"
			   "custom_runs=3\n"
			   "cards=run,custom_research,agents,evidence,data,llm_relay,compare\n"
			   "source=rp_ui_home\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_run",
			   "api=run-detail\n"
			   "run_id=RUN-042\n"
			   "custom_research=rp_runner\n"
			   "custom_research_runs=3\n"
			   "workflow=lab-gene-x\n"
			   "stages=5\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "runner_exec_files=5\n"
			   "stage_state=rp_stage_state\n"
			   "cache_index=rp_cache_index\n"
			   "retry_plan=rp_retry_plan\n"
			   "run_events=rp_run_events\n"
			   "artifact_manifest=rp_artifact_manifest\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_agents",
			   "api=agent-detail\n"
			   "agents=7\n"
			   "messages=21\n"
			   "decisions=8\n"
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
			   "stage_log=rp_stage_log\n"
			   "artifact=rp_artifact\n"
			   "manifest=rp_artifact_manifest\n"
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
			   "package_manifest=ready\n"
			   "evidence_package=rp_package\n"
			   "bundle_items=18\n"
			   "downloadable_units=3\n"
			   "report=rp_report_text\n"
			   "chart=rp_chart_data\n"
			   "llm_relay_files=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_data",
			   "api=data\n"
			   "run_id=RUN-042\n"
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
			   "documents=17\n"
			   "answers=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_runtime",
			   "api=runtime\n"
			   "runtime_env=rp_runenv\n"
			   "notebook_exec=rp_nbexec\n"
			   "eln_record=rp_eln\n"
			   "worker_pool=rp_wpool\n"
			   "environments=4\n"
			   "executed_cells=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_action",
			   "api=actions\n"
			   "actions=5\n"
			   "host_workflow_run=/actions/host-workflow/run\n"
			   "host_workflow_export=/actions/host-workflow/export\n"
			   "agentcompare_run=/actions/agentcompare/run\n"
			   "research_run=/actions/research/run\n"
			   "research_export=/actions/research/export\n"
			   "redirect_status=303\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_actionio",
			   "requests=5\n"
			   "request=1;path=/actions/host-workflow/run;run_id=RUN-042;inject_failure=1;use_cache=1\n"
			   "request=2;path=/actions/host-workflow/export;workflow_run_id=RUN-042\n"
			   "request=3;path=/actions/agentcompare/run;profile=plain_ucore\n"
			   "request=4;path=/actions/research/run;provider=template;source_request=rp_input;dataset_file=rp_input;custom_runs=3\n"
			   "request=5;path=/actions/research/export;run_id=usable-run:RUN-900\n"
			   "responses=5\n"
			   "response=1;status=303;location=/runs/RUN-042;effect=host_workflow_run\n"
			   "response=2;status=303;location=/runs/RUN-042;effect=host_workflow_export\n"
			   "response=3;status=303;location=/compare;effect=agentcompare_run\n"
			   "response=4;status=303;location=/research/usable-run:RUN-900;effect=usable_research_run;generated_runs=3\n"
			   "response=5;status=303;location=/research/usable-run:RUN-900;effect=usable_research_export\n"
			   "actions=5\n"
			   "completed=5\n"
			   "failed=0\n"
			   "redirects=5\n"
			   "state_writes=11\n"
			   "audit_records=5\n"
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
			   "source_request=rp_input\n"
			   "source_dataset=rp_input\n"
			   "source_run=rp_runner\n"
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
			   "export=review_html\n"
			   "export_sections=6\n"
			   "contains=Stage DAG,Agent Decisions,Artifacts,LLM Relay\n"
			   "status=ok\n")) {
		return 1;
	}
	if (!rp_write_file("rp_web_bundle",
			   "bundle=host-web-ui\n"
			   "routes=18\n"
			   "get_routes=13\n"
			   "post_routes=5\n"
			   "api_payloads=14\n"
			   "action_payloads=1\n"
			   "source_pages=5\n"
			   "evidence_package=rp_package\n"
			   "package_manifest=ready\n"
			   "downloadable_units=3\n"
			   "runner_files=5\n"
			   "data_pipeline_files=6\n"
			   "custom_research_files=1\n"
			   "custom_research_runs=3\n"
			   "research_service_files=25\n"
			   "llm_relay_files=5\n"
			   "agent_records=5\n"
			   "status=ready\n")) {
		return 1;
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
	if (!rp_append_status("actionio=ready")) return 1;
	if (!rp_append_status("usable_research=ready")) return 1;
	if (!rp_append_status("action_exports=ready")) return 1;
	printf("rp_web_export: routes=18 api_payloads=14 actions=5 bundle=ready status=ready\n");
	return 0;
}
