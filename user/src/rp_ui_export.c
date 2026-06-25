#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_runner", "status=ready");
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_stage_state", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_retry_plan", "failure_reason=tool_output_missing");
	ok = ok && rp_file_contains("rp_artifact_manifest", "support_entries=2");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_artifact_manifest", "real_artifact_items=5");
	ok = ok && rp_file_contains("rp_artifact", "archive_file=rp_metrics_json");
	ok = ok && rp_file_contains("rp_report_text", "RUN-042 Recovery Report");
	ok = ok && rp_file_contains("rp_chart_data", "chart=stage_attempts");
	ok = ok && rp_file_contains("rp_evidence", "status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "status=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_telemetry", "status=ready");
	ok = ok && rp_file_contains("rp_consistency", "state_relation=passed");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_input", "form_fields=8");
	ok = ok && rp_file_contains("rp_input", "uploads=2");
	ok = ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_runner", "library_source_count=1");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	ok = ok && rp_file_contains("rp_runner", "custom_status=ok");
	ok = ok && rp_file_contains("rp_package", "deliverables=8");
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_package", "delivery_checks=3");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && rp_file_contains("rp_package", "raw_links=5");
	ok = ok && rp_file_contains("rp_package", "decision_controls=2");
	ok = ok && rp_file_contains("rp_package", "human_reviews=1");
	ok = ok && rp_file_contains("rp_package", "revision_tasks=1");
	ok = ok && rp_file_contains("rp_package", "review_threads=2");
	ok = ok && rp_file_contains("rp_review2", "action_items=2");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_agent_run", "status=ready");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_packets", "matched_responses=3");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "roundtrip=ready");
	ok = ok && rp_file_contains("rp_sreg", "samples=8");
	ok = ok && rp_file_contains("rp_instr", "instruments=4");
	ok = ok && rp_file_contains("rp_resrev", "review_items=10");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	if (!ok) return 1;
	if (!rp_write_file("rp_ui_home",
			   "page=home\n"
			   "title=Research Agent Platform\n"
			   "run=RUN-042\n"
			   "custom_run=usable-run:RUN-900\n"
			   "custom_runs=3\n"
			   "status=recovered\n"
			   "cards=run,custom_research,agents,evidence,data,bio,lab,publication,knowledge,runtime,llm_relay,compare\n"
			   "research_form=rp_input\n"
			   "upload_files=rp_input\n"
			   "library_sources=rp_knowledge\n"
			   "nav_items=12\n"
			   "primary_cards=12\n"
			   "home_sections=overview,run,custom_research,agents,evidence,data,services,llm,compare\n"
			   "source=plain_ucore_files\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_ui_run",
			   "page=run-detail\n"
			   "run_id=RUN-042\n"
			   "workflow=lab-gene-x\n"
			   "stages=5\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "report=rp_report_text\n"
			   "chart=rp_chart_data\n"
			   "timeline_rows=5\n"
			   "artifact_preview=rp_report_text,rp_chart_data,rp_artifact,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json\n"
			   "dependency_checks=5\n"
			   "stage_outputs=5\n"
			   "real_artifact_items=5\n"
			   "retry_reason=tool_output_missing\n"
			   "runner_exec=rp_stage_state,rp_cache_index,rp_retry_plan,rp_run_events,rp_artifact_manifest\n"
			   "data_pipeline=rp_ingest_files,rp_dataset_snapshot,rp_data_preview,rp_data_quality,rp_data_transform,rp_dataset_collection\n"
			   "research_services=rp_sreg,rp_instr,rp_resrev,rp_semindex,rp_runenv\n"
			   "custom_research=rp_runner\n"
			   "custom_research_runs=3\n"
			   "request_form=rp_input\n"
			   "upload_files=rp_input\n"
			   "workspace_imports=1\n"
			   "library_sources=rp_knowledge\n"
			   "evidence_protocols=1\n"
			   "evidence_extractions=3\n"
			   "bibliography=rp_runner\n"
			   "citation_plan=rp_runner\n"
			   "workflow_portability=rp_wfio\n"
			   "adapter_specs=6\n"
			   "migration_steps=9\n"
			   "delivery_manifest=rp_package\n"
			   "delivery_files=8\n"
			   "delivery_checks=3\n"
			   "delivery_manifest_json=delivery-manifest.json\n"
			   "review_page=rp_package\n"
			   "export_bundle=rp_package\n"
			   "evidence_bundle_zip=research-evidence-bundle.zip\n"
			   "human_reviews=1\n"
			   "latest_review=usable-review:RUN-900:1\n"
			   "revision_tasks=1\n"
			   "latest_revision_task=usable-revision-task:RUN-900:1\n"
			   "revised_run=usable-run:RUN-900-rev1\n"
			   "revision_changes=2\n"
			   "revision_delta=rp_revision\n"
			   "review_threads=2\n"
			   "review_comments=3\n"
			   "review_action_items=2\n"
			   "review_thread_source=rp_review2\n"
			   "llm_relay=rp_llm_packets,rp_llm_routes,rp_llm_guard,rp_llm_hostreq,rp_llm_fallback\n"
			   "llm_roundtrip=ready\n"
			   "llm_response_file=rp_llm_resp\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_ui_agent",
			   "page=agent-detail\n"
			   "agents=orchestrator,retriever,analyst,reviewer,writer,recovery,auditor\n"
			   "messages=21\n"
			   "acks=26\n"
			   "decisions=8\n"
			   "decision_rows=8\n"
			   "handoffs=6\n"
			   "decision_records=rp_agents,rp_decisions,rp_handoff,rp_deliberation,rp_agent_run\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_ui_evidence",
			   "page=evidence-detail\n"
			   "claims=8\n"
			   "evidence_links=5\n"
			   "critical_paths=3\n"
			   "literature_search=usable-literature-search:RUN-900:1\n"
			   "screening_decisions=9\n"
			   "evidence_protocol=usable-evidence-protocol:RUN-900:1\n"
			   "prisma_flow=usable-prisma-flow:RUN-900:1\n"
			   "evidence_synthesis=usable-evidence-synthesis:RUN-900:1\n"
			   "stage_log=rp_stage_log\n"
			   "artifact=rp_artifact\n"
			   "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json\n"
			   "delivery_manifest=rp_package\n"
			   "delivery_files=8\n"
			   "delivery_checks=3\n"
			   "export_bundle=rp_package\n"
			   "evidence_bundle_zip=research-evidence-bundle.zip\n"
			   "llm_guard=rp_llm_guard\n"
			   "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_ui_compare",
			   "page=compare-metrics\n"
			   "plain_kernel=passed\n"
			   "agentos_kernel=pending\n"
			   "pain_file_scans=128\n"
			   "pain_state_convention=1\n"
			   "pain_user_permissions=1\n"
			   "pain_untrusted_context=1\n"
			   "pain_rebuild_steps=6\n"
			   "metric_rows=8\n"
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
			   "workflow_runner_files=5\n"
			   "workflow_portability_records=1\n"
			   "data_pipeline_files=6\n"
			   "bio_service_files=5\n"
			   "lab_resource_files=5\n"
			   "publication_service_files=5\n"
			   "knowledge_service_files=5\n"
			   "runtime_service_files=5\n"
			   "message_acks=33\n"
			   "tool_events=115\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=ui_export;msg=ui;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=ui_export.write_home;target=rp_ui_home;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=ui_export.write_run;target=rp_ui_run;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=ui_export.write_agent;target=rp_ui_agent;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=ui_export.write_evidence;target=rp_ui_evidence;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=ui_export.write_compare;target=rp_ui_compare;status=ok")) return 1;
	if (!rp_append_status("ui_home=ready")) return 1;
	if (!rp_append_status("ui_run=ready")) return 1;
	if (!rp_append_status("ui_agent=ready")) return 1;
	if (!rp_append_status("ui_evidence=ready")) return 1;
	if (!rp_append_status("ui_compare=ready")) return 1;
	printf("rp_ui_export: pages=5 run=RUN-042 custom_runs=3 compare=ready status=ready\n");
	return 0;
}
