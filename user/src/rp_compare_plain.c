#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_object_query", "hits=8");
	ok = ok && rp_file_contains("rp_lineage", "edges=7");
	ok = ok && rp_file_contains("rp_site", "pages=6");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_knowledge", "semantic_relations=6");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_provpath", "critical_paths=3");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_ingest_files", "files=2");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "snapshots=2");
	ok = ok && rp_file_contains("rp_data_preview", "previews=2");
	ok = ok && rp_file_contains("rp_data_quality", "passed=7");
	ok = ok && rp_file_contains("rp_data_transform", "transforms=2");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_figrec", "exported=3");
	ok = ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	ok = ok && rp_file_contains("rp_dataver", "release_candidate=v2");
	ok = ok && rp_file_contains("rp_reviewops", "governance=passed");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "verifications=2");
	ok = ok && rp_file_contains("rp_delta", "decision=accepted");
	ok = ok && rp_file_contains("rp_diff", "changed_items=20");
	ok = ok && rp_file_contains("rp_wfio", "compatibility_checks=6");
	ok = ok && rp_file_contains("rp_review2", "rounds=2");
	ok = ok && rp_file_contains("rp_revision", "draft_versions=3");
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
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	ok = ok && rp_file_contains("rp_llmlog", "request_packets=3");
	ok = ok && rp_file_contains("rp_sched", "queue_items=21");
	ok = ok && rp_file_contains("rp_retrylog", "attempts=2");
	ok = ok && rp_file_contains("rp_relay", "network_stack=host_only");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "cloud_mode=optional_host_side");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_submit", "data_availability=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "message_acks=33");
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
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_exports=2");
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
	ok = ok && rp_file_contains("rp_citegraph", "citations=14");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_kanswers", "answers=4");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	ok = ok && rp_file_contains("rp_nbexec", "executed_cells=8");
	ok = ok && rp_file_contains("rp_eln", "eln_entries=3");
	ok = ok && rp_file_contains("rp_wpool", "workers=4");
	ok = ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	ok = ok && rp_file_contains("rp_package", "artifacts=48");
	ok = ok && rp_file_contains("rp_package", "package_manifest=ready");
	ok = ok && rp_file_contains("rp_package", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_package", "custom_sources=rp_input,rp_runner,rp_uresrun");
	ok = ok && rp_file_contains("rp_package", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_package", "deliverables=8");
	ok = ok && rp_file_contains("rp_package", "raw_links=5");
	ok = ok && rp_file_contains("rp_package", "decision_controls=2");
	ok = ok && rp_file_contains("rp_query", "workflow_hits=34");
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
	ok = ok && rp_file_contains("rp_artifact_manifest", "support_entries=2");
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
	ok = ok && rp_file_contains("rp_consistency", "checks=86");
	ok = ok && rp_file_contains("rp_consistency", "runner_stages=5");
	ok = ok && rp_file_contains("rp_ui_home", "page=home");
	ok = ok && rp_file_contains("rp_ui_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_ui_run", "page=run-detail");
	ok = ok && rp_file_contains("rp_ui_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_ui_run", "artifact_preview=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && rp_file_contains("rp_ui_agent", "page=agent-detail");
	ok = ok && rp_file_contains("rp_ui_agent", "decision_rows=8");
	ok = ok && rp_file_contains("rp_ui_evidence", "page=evidence-detail");
	ok = ok && rp_file_contains("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_ui_compare", "page=compare-metrics");
	ok = ok && rp_file_contains("rp_ui_compare", "metric_rows=8");
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
	ok = ok && rp_file_contains("rp_web_routes", "routes=18");
	ok = ok && rp_file_contains("rp_web_routes", "get_routes=13");
	ok = ok && rp_file_contains("rp_web_routes", "post_routes=5");
	ok = ok && rp_file_contains("rp_api_home", "api=home");
	ok = ok && rp_file_contains("rp_api_home", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_api_home", "custom_runs=3");
	ok = ok && rp_file_contains("rp_api_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_api_run", "runner_exec_files=5");
	ok = ok && rp_file_contains("rp_api_run", "custom_research=rp_runner");
	ok = ok && rp_file_contains("rp_api_run", "custom_research_runs=3");
	ok = ok && rp_file_contains("rp_api_run", "request_form=rp_input");
	ok = ok && rp_file_contains("rp_api_run", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_api_run", "bibliography=rp_runner");
	ok = ok && rp_file_contains("rp_api_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_api_run", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_api_run", "manifest_support_entries=2");
	ok = ok && rp_file_contains("rp_api_agents", "agents=7");
	ok = ok && rp_file_contains("rp_api_evidence", "provenance_paths=3");
	ok = ok && rp_file_contains("rp_api_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_api_compare", "workflow_runner_files=5");
	ok = ok && rp_file_contains("rp_api_artifacts", "manifest_records=4");
	ok = ok && rp_file_contains("rp_api_artifacts", "evidence_package=rp_package");
	ok = ok && rp_file_contains("rp_api_artifacts", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_api_artifacts", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_api_artifacts", "preview_files=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && rp_file_contains("rp_api_data", "dataset_snapshots=2");
	ok = ok && rp_file_contains("rp_api_bio", "sample_registry=rp_sreg");
	ok = ok && rp_file_contains("rp_api_labres", "instrument_registry=rp_instr");
	ok = ok && rp_file_contains("rp_api_pub", "result_review=rp_resrev");
	ok = ok && rp_file_contains("rp_api_know", "semantic_index=rp_semindex");
	ok = ok && rp_file_contains("rp_api_runtime", "runtime_env=rp_runenv");
	ok = ok && rp_file_contains("rp_api_action", "actions=5");
	ok = ok && rp_file_contains("rp_actionio", "requests=5");
	ok = ok && rp_file_contains("rp_actionio", "responses=5");
	ok = ok && rp_file_contains("rp_actionio", "completed=5");
	ok = ok && rp_file_contains("rp_actionio", "dataset_file=rp_input");
	ok = ok && rp_file_contains("rp_actionio", "generated_runs=3");
	ok = ok && rp_file_contains("rp_actionio", "tag=reusable");
	ok = ok && rp_file_contains("rp_uresrun", "runs=3");
	ok = ok && rp_file_contains("rp_uresrun", "run_id_3=usable-run:RUN-902");
	ok = ok && rp_file_contains("rp_uresrun", "source_form=rp_input");
	ok = ok && rp_file_contains("rp_uresrun", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_uresrun", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_uresrun", "artifacts=36");
	ok = ok && rp_file_contains("rp_uresrun", "dataset_rows=3");
	ok = ok && rp_file_contains("rp_uresrun", "LLM Relay");
	ok = ok && rp_file_contains("rp_actionio", "Stage DAG");
	ok = ok && rp_file_contains("rp_actionio", "passed_cases=3");
	ok = ok && rp_file_contains("rp_web_bundle", "api_payloads=14");
	ok = ok && rp_file_contains("rp_web_bundle", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_web_bundle", "render_sections=7");
	ok = ok && rp_file_contains("rp_web_bundle", "artifact_previews=3");
	ok = ok && rp_file_contains("rp_web_bundle", "runner_detail_fields=16");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_web_bundle", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_web_bundle", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_web_bundle", "custom_research_files=1");
	ok = ok && rp_file_contains("rp_tests", "tests=241");
	ok = ok && rp_file_contains("rp_tests", "status=passed");
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
	if (!ok) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 39 || tool_count < 140) {
		printf("rp_compare_plain: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		return 1;
	}
	if (!rp_write_file("rp_compare",
			   "profile=plain_ucore\n"
			   "plain_kernel=passed\n"
			   "agentos_kernel=pending\n"
			   "objects=500\n"
			   "programs=39\n"
			   "state_files=169\n"
			   "message_acks=39\n"
			   "tool_events=140\n"
			   "consistency_checks=86\n"
			   "runner_stages=5\n"
			   "workflow_runner_files=5\n"
			   "runner_detail_fields=16\n"
			   "package_bundle_items=18\n"
			   "downloadable_units=3\n"
			   "data_pipeline_files=6\n"
			   "dataset_snapshots=2\n"
			   "data_quality_checks=7\n"
			   "bio_service_files=5\n"
			   "lab_resource_files=5\n"
			   "publication_service_files=5\n"
			   "knowledge_service_files=5\n"
			   "runtime_service_files=5\n"
			   "relay_protocol_files=5\n"
			   "agent_roles=7\n"
			   "collaboration_decisions=8\n"
			   "ui_pages=5\n"
			   "ui_render_sections=7\n"
			   "artifact_previews=3\n"
			   "custom_research_runs=3\n"
			   "custom_research_files=1\n"
			   "research_input_files=2\n"
			   "delivery_files=3\n"
			   "library_sources=1\n"
			   "citation_plan_entries=3\n"
			   "web_routes=18\n"
			   "web_api_payloads=14\n"
			   "web_action_routes=5\n"
			   "web_action_outputs=2\n"
			   "test_cases=241\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("compare=ready")) return 1;
	printf("rp_compare_plain: plain_kernel=passed objects=500 programs=39 state_files=169 acks=39 tools=140 status=ready\n");
	return 0;
}
