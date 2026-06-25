#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_report", "status=packaged");
	ok = ok && rp_file_contains("rp_revision", "final_status=ready");
	ok = ok && rp_file_contains("rp_evidence", "status=ready");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_provpath", "critical_paths=3");
	ok = ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	ok = ok && rp_file_contains("rp_wfio", "compatibility_checks=6");
	ok = ok && rp_file_contains("rp_datadic", "schema_fields=17");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_figrec", "exported=3");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "capa_actions=2");
	ok = ok && rp_file_contains("rp_fail", "recoverable=1");
	ok = ok && rp_file_contains("rp_retrylog", "final_result=recovered");
	ok = ok && rp_file_contains("rp_prompt", "routes=4");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llmlog", "replay=ready");
	ok = ok && rp_file_contains("rp_llmlog", "request_packets=3");
	ok = ok && rp_file_contains("rp_relay", "mode=host_file_relay");
	ok = ok && rp_file_contains("rp_relay", "relay_packets=3");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "template_mode=ready");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_configval", "validations=2");
	ok = ok && rp_file_contains("rp_invocation", "status=recovered");
	ok = ok && rp_file_contains("rp_completion", "actions=4");
	ok = ok && rp_file_contains("rp_input", "form_fields=8");
	ok = ok && rp_file_contains("rp_input", "csv_rows_total=9");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_runner", "status=ready");
	ok = ok && rp_file_contains("rp_runner", "library_source_count=1");
	ok = ok && rp_file_contains("rp_runner", "revision_task_id=usable-revision-task:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_stage_dag", "status=ready");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "status=ready");
	ok = ok && rp_file_contains("rp_chart_data", "status=ready");
	ok = ok && rp_file_contains("rp_ingest_files", "files=2");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "snapshots=2");
	ok = ok && rp_file_contains("rp_data_preview", "previews=2");
	ok = ok && rp_file_contains("rp_data_quality", "passed=7");
	ok = ok && rp_file_contains("rp_data_transform", "transforms=2");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_review2", "review_summary=all_review_comments_resolved");
	ok = ok && rp_file_contains("rp_review2", "action_items=2");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_decisions=8");
	ok = ok && rp_file_contains("rp_policy", "license_checks=2");
	ok = ok && rp_file_contains("rp_compliance", "decision=accepted");
	ok = ok && rp_file_contains("rp_audit", "release=ready");
	ok = ok && rp_file_contains("rp_mail", "to=package");
	if (!ok) return 1;
	if (!rp_write_file("rp_package",
			   "package=research-evidence-package\n"
			   "artifacts=48\n"
			   "checks=69\n"
			   "package_manifest=ready\n"
			   "bundle_items=18\n"
			   "downloadable_units=3\n"
			   "evidence_bundle=ready\n"
			   "review_bundle=ready\n"
			   "provenance_bundle=ready\n"
			   "manifest_sources=rp_report_text,rp_chart_data,rp_artifact_manifest,rp_stage_log,rp_agents,rp_llm_packets\n"
			   "review_sources=rp_review,rp_review2,rp_revision,rp_resrev,rp_peerresp,rp_dossier\n"
			   "review_threads=2\n"
			   "review_comments=3\n"
			   "review_action_items=2\n"
			   "review_thread_source=rp_review2\n"
			   "review_action_source=rp_review2\n"
			   "provenance_sources=rp_evidence,rp_claimrec,rp_provpath,rp_lineage,rp_repro,rp_audit\n"
			   "custom_sources=rp_input,rp_runner,rp_uresrun\n"
			   "download_index=report_bundle,evidence_bundle,provenance_bundle\n"
			   "package_reader=host_web_bundle\n"
			   "request_form=rp_input\n"
			   "upload_files=rp_input\n"
			   "library_sources=rp_knowledge\n"
			   "bibliography=rp_runner\n"
			   "citation_plan=rp_runner\n"
			   "delivery_manifest=rp_package\n"
			   "export_bundle=rp_package\n"
			   "review_page=rp_package\n"
			   "human_reviews=1\n"
			   "human_review=usable-review:RUN-900:1;reviewer=Wang;decision=needs_revision;requested_changes=2\n"
			   "review_note=clarify_reproducibility_and_chart_caption\n"
			   "revision_tasks=1\n"
			   "revision_task=usable-revision-task:RUN-900:1;status=completed;owner=Wang;requested_changes=2\n"
			   "revision_task_source=usable-review:RUN-900:1\n"
			   "revision_task_completed=usable-revision-task:RUN-900:1;new_run=usable-run:RUN-900-rev1\n"
			   "revision_run=usable-run:RUN-900-rev1\n"
			   "revision_task_export=revision_tasks.json\n"
			   "raw_downloads=5\n"
			   "delivery_manifest_detail=deliverables=8;audience=reviewer;bundle=rp_package\n"
			   "export_bundle_detail=bundle_units=3;raw_links=5;checksums=6;provenance_source=rp_provpath;delivery_manifest=rp_package\n"
			   "review_page_detail=page=research-run;artifact_links=6;decision_controls=2;export_bundle=rp_package\n"
			   "review_page_sections=Human Reviews,Delivery Manifests,Revision Tasks,Review Threads,Action Items\n"
			   "real_inputs=1\n"
			   "data_pipeline=1\n"
			   "stage_logs=1\n"
			   "chart_data=1\n"
			   "workflow_runner=1\n"
			   "agent_collaboration=1\n"
			   "llm_relay_protocol=1\n"
			   "release=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_datarel",
			   "fair_checks=5\n"
			   "fair=passed\n"
			   "data_products=1\n"
			   "dataset_deposits=1\n"
			   "doi_records=1\n"
			   "publication_targets=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_dataver",
			   "product=data-product:RUN-042\n"
			   "versions=2\n"
			   "snapshots=3\n"
			   "schema_versions=2\n"
			   "release_candidate=v2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_repro",
			   "env_locks=4\n"
			   "notebook_replay=passed\n"
			   "reproduction_checks=9\n"
			   "retry_replay=passed\n"
			   "calculation_exports=1\n"
			   "research_object_crates=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=package;msg=11;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.build_artifacts;target=rp_package;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.version_data;target=rp_dataver;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.build_repro;target=rp_repro;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_llm_eval;target=rp_llmeval;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_llm_relay_protocol;target=rp_llm_packets;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_evidence_path;target=rp_provpath;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_data_records;target=rp_dataprof;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_execobs;target=rp_execobs;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_run_config;target=rp_runconf;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_invocation;target=rp_invocation;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_completion;target=rp_completion;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_runner_artifacts;target=rp_runner;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_workflow_runner;target=rp_stage_state;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_data_pipeline;target=rp_dataset_collection;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_agent_collab;target=rp_agent_run;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_human_review;target=rp_package;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=package.attach_revision_task;target=rp_runner;status=ok")) return 1;
	if (!rp_append_status("package=ready")) return 1;
	if (!rp_append_status("datarel=ready")) return 1;
	if (!rp_append_status("dataver=ready")) return 1;
	if (!rp_append_status("repro=ready")) return 1;
	if (!rp_append_status("delivery_manifest=ready")) return 1;
	if (!rp_append_status("export_bundle=ready")) return 1;
	if (!rp_append_status("review_page=ready")) return 1;
	if (!rp_append_status("human_review=ready")) return 1;
	if (!rp_append_status("revision_task_package=ready")) return 1;
	printf("rp_package: artifacts=48 checks=69 fair=passed repro=ready status=ready\n");
	return 0;
}
