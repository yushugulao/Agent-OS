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

int main(void)
{
	int ok = 1;

	ok = ok && require_file_token("rp_objects", "objects=500");
	ok = ok && require_file_token("rp_services", "workflow=34");
	ok = ok && require_file_token("rp_object_query", "hits=8");
	ok = ok && require_file_token("rp_lineage", "edges=7");
	ok = ok && require_file_token("rp_site", "pages=6");

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
	ok = ok && require_file_token("rp_stage_state", "stages=5");
	ok = ok && require_file_token("rp_stage_state", "command=align:agent-align");
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
	ok = ok && require_file_token("rp_runner", "revision_status=completed");
	ok = ok && require_file_token("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_ingest_files", "files=2");
	ok = ok && require_file_token("rp_dataset_snapshot", "snapshots=2");
	ok = ok && require_file_token("rp_data_preview", "previews=2");
	ok = ok && require_file_token("rp_data_quality", "passed=7");
	ok = ok && require_file_token("rp_data_transform", "transforms=2");
	ok = ok && require_file_token("rp_dataset_collection", "items=4");
	ok = ok && require_file_token("rp_agents", "agents=7");
	ok = ok && require_file_token("rp_decisions", "decisions=8");
	ok = ok && require_file_token("rp_handoff", "handoffs=6");
	ok = ok && require_file_token("rp_deliberation", "items=5");
	ok = ok && require_file_token("rp_agent_run", "agent_decisions=8");

	ok = ok && require_file_token("rp_llmq", "secret_policy=no_secret_in_ucore");
	ok = ok && require_file_token("rp_llm_resp", "responses=3");
	ok = ok && require_file_token("rp_llm_resp", "matched_requests=3");
	ok = ok && require_file_token("rp_llm_resp", "host_relay_roundtrip=ready");
	ok = ok && require_file_token("rp_llm_resp", "match=q1->r1,q2->r2,q3->r3");
	ok = ok && require_file_token("rp_relay", "network_stack=host_only");
	ok = ok && require_file_token("rp_llm_packets", "packets=3");
	ok = ok && require_file_token("rp_llm_packets", "matched_responses=3");
	ok = ok && require_file_token("rp_llm_packets", "roundtrip=ready");
	ok = ok && require_file_token("rp_llm_routes", "routes=4");
	ok = ok && require_file_token("rp_llm_routes", "roundtrip_routes=3");
	ok = ok && require_file_token("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && require_file_token("rp_llm_hostreq", "template_mode=ready");
	ok = ok && require_file_token("rp_llm_hostreq", "host_request_records=3");
	ok = ok && require_file_token("rp_llm_hostreq", "host_response_records=3");
	ok = ok && require_file_token("rp_llm_hostreq", "roundtrip=ready");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_cases=1");
	ok = ok && require_file_token("rp_llm_fallback", "offline_template_verified=1");
	ok = ok && require_file_token("rp_prompt", "provider_policy=host_relay");
	ok = ok && require_file_token("rp_llmeval", "passed=7");
	ok = ok && require_file_token("rp_privacy", "decision=accepted");
	ok = ok && require_file_token("rp_compliance", "decision=accepted");

	ok = ok && require_file_token("rp_evidence", "status=ready");
	ok = ok && require_file_token("rp_claimrec", "claim=8");
	ok = ok && require_file_token("rp_provpath", "critical_paths=3");
	ok = ok && require_file_token("rp_knowledge", "synthesis=ready");
	ok = ok && require_file_token("rp_knowledge", "library_sources=1");
	ok = ok && require_file_token("rp_knowledge", "citation_key=library2026");
	ok = ok && require_file_token("rp_review2", "review_threads=2");
	ok = ok && require_file_token("rp_review2", "thread=review-thread:RUN-042:methods");
	ok = ok && require_file_token("rp_review2", "thread=review-thread:RUN-042:repro");
	ok = ok && require_file_token("rp_review2", "comment=review-comment:RUN-042:1");
	ok = ok && require_file_token("rp_review2", "comment=review-comment:RUN-042:2");
	ok = ok && require_file_token("rp_review2", "action_item=action-item:RUN-042:methods");
	ok = ok && require_file_token("rp_review2", "action_item=action-item:RUN-042:repro");
	ok = ok && require_file_token("rp_review2", "review_summary=all_review_comments_resolved");

	ok = ok && require_file_token("rp_runconf", "profiles=2");
	ok = ok && require_file_token("rp_invocation", "status=recovered");
	ok = ok && require_file_token("rp_completion", "actions=4");
	ok = ok && require_file_token("rp_package", "artifacts=48");
	ok = ok && require_file_token("rp_package", "package_manifest=ready");
	ok = ok && require_file_token("rp_package", "bundle_items=18");
	ok = ok && require_file_token("rp_package", "downloadable_units=3");
	ok = ok && require_file_token("rp_package", "evidence_bundle=ready");
	ok = ok && require_file_token("rp_package", "review_bundle=ready");
	ok = ok && require_file_token("rp_package", "provenance_bundle=ready");
	ok = ok && require_file_token("rp_package", "custom_sources=rp_input,rp_runner,rp_uresrun");
	ok = ok && require_file_token("rp_package", "download_index=report_bundle,evidence_bundle,provenance_bundle");
	ok = ok && require_file_token("rp_package", "request_form=rp_input");
	ok = ok && require_file_token("rp_package", "upload_files=rp_input");
	ok = ok && require_file_token("rp_package", "library_sources=rp_knowledge");
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
	ok = ok && require_file_token("rp_agentcmp", "message_acks=33");
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
	ok = ok && require_file_token("rp_backend", "cases=4");
	ok = ok && require_file_token("rp_consistency", "checks=86");
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
	ok = ok && require_file_token("rp_citegraph", "bibtex_entries=9");
	ok = ok && require_file_token("rp_semindex", "documents=17");
	ok = ok && require_file_token("rp_kanswers", "answers=4");
	ok = ok && require_file_token("rp_knowop", "op=llm_grounding");
	ok = ok && require_file_token("rp_runenv", "environments=4");
	ok = ok && require_file_token("rp_nbexec", "executed_cells=8");
	ok = ok && require_file_token("rp_eln", "eln_entries=3");
	ok = ok && require_file_token("rp_wpool", "worker_pools=2");
	ok = ok && require_file_token("rp_runop", "op=host_llm_request");

	ok = ok && require_file_token("rp_ui_home", "page=home");
	ok = ok && require_file_token("rp_ui_home", "nav_items=12");
	ok = ok && require_file_token("rp_ui_home", "primary_cards=12");
	ok = ok && require_file_token("rp_ui_run", "page=run-detail");
	ok = ok && require_file_token("rp_ui_run", "runner_exec=");
	ok = ok && require_file_token("rp_ui_run", "timeline_rows=5");
	ok = ok && require_file_token("rp_ui_run", "artifact_preview=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && require_file_token("rp_ui_run", "dependency_checks=5");
	ok = ok && require_file_token("rp_ui_run", "retry_reason=tool_output_missing");
	ok = ok && require_file_token("rp_ui_run", "latest_review=usable-review:RUN-900:1");
	ok = ok && require_file_token("rp_ui_run", "latest_revision_task=usable-revision-task:RUN-900:1");
	ok = ok && require_file_token("rp_ui_run", "revised_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_ui_run", "delivery_files=8");
	ok = ok && require_file_token("rp_ui_run", "delivery_checks=3");
	ok = ok && require_file_token("rp_ui_run", "delivery_manifest_json=delivery-manifest.json");
	ok = ok && require_file_token("rp_ui_run", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_ui_run", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_ui_run", "llm_response_file=rp_llm_resp");
	ok = ok && require_file_token("rp_ui_run", "review_threads=2");
	ok = ok && require_file_token("rp_ui_run", "review_action_items=2");
	ok = ok && require_file_token("rp_ui_agent", "page=agent-detail");
	ok = ok && require_file_token("rp_ui_agent", "decisions=8");
	ok = ok && require_file_token("rp_ui_agent", "decision_rows=8");
	ok = ok && require_file_token("rp_ui_evidence", "page=evidence-detail");
	ok = ok && require_file_token("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && require_file_token("rp_ui_evidence", "delivery_files=8");
	ok = ok && require_file_token("rp_ui_evidence", "delivery_checks=3");
	ok = ok && require_file_token("rp_ui_evidence", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_ui_evidence", "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp");
	ok = ok && require_file_token("rp_ui_compare", "page=compare-metrics");
	ok = ok && require_file_token("rp_ui_compare", "metric_rows=8");
	ok = ok && require_file_token("rp_ui_compare", "relay_protocol_files=5");
	ok = ok && require_file_token("rp_web_routes", "routes=21");
	ok = ok && require_file_token("rp_web_routes", "get_routes=13");
	ok = ok && require_file_token("rp_web_routes", "post_routes=8");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/review");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/revision-task");
	ok = ok && require_file_token("rp_web_routes", "action=/actions/research/run-revision-task");
	ok = ok && require_file_token("rp_api_home", "api=home");
	ok = ok && require_file_token("rp_api_home", "custom_run=usable-run:RUN-900");
	ok = ok && require_file_token("rp_api_home", "custom_runs=3");
	ok = ok && require_file_token("rp_api_home", "research_form=rp_input");
	ok = ok && require_file_token("rp_api_home", "upload_files=rp_input");
	ok = ok && require_file_token("rp_api_home", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_api_home", "nav_items=12");
	ok = ok && require_file_token("rp_api_run", "runner_exec_files=5");
	ok = ok && require_file_token("rp_api_run", "custom_research=rp_runner");
	ok = ok && require_file_token("rp_api_run", "custom_research_runs=3");
	ok = ok && require_file_token("rp_api_run", "request_form=rp_input");
	ok = ok && require_file_token("rp_api_run", "upload_files=rp_input");
	ok = ok && require_file_token("rp_api_run", "bibliography=rp_runner");
	ok = ok && require_file_token("rp_api_run", "citation_plan=rp_runner");
	ok = ok && require_file_token("rp_api_run", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_api_run", "delivery_files=8");
	ok = ok && require_file_token("rp_api_run", "delivery_checks=3");
	ok = ok && require_file_token("rp_api_run", "latest_delivery_status=ready");
	ok = ok && require_file_token("rp_api_run", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && require_file_token("rp_api_run", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_api_run", "llm_response_file=rp_llm_resp");
	ok = ok && require_file_token("rp_api_run", "review_page=rp_package");
	ok = ok && require_file_token("rp_api_run", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_api_run", "human_reviews=1");
	ok = ok && require_file_token("rp_api_run", "revision_tasks=1");
	ok = ok && require_file_token("rp_api_run", "revised_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_api_run", "review_threads=2");
	ok = ok && require_file_token("rp_api_run", "review_action_items=2");
	ok = ok && require_file_token("rp_api_run", "timeline_rows=5");
	ok = ok && require_file_token("rp_api_run", "dependency_checks=5");
	ok = ok && require_file_token("rp_api_run", "manifest_support_entries=2");
	ok = ok && require_file_token("rp_api_agents", "agents=7");
	ok = ok && require_file_token("rp_api_evidence", "provenance_paths=3");
	ok = ok && require_file_token("rp_api_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && require_file_token("rp_api_compare", "workflow_runner_files=5");
	ok = ok && require_file_token("rp_api_artifacts", "manifest_records=4");
	ok = ok && require_file_token("rp_api_artifacts", "evidence_package=rp_package");
	ok = ok && require_file_token("rp_api_artifacts", "downloadable_units=3");
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
	ok = ok && require_file_token("rp_api_bio", "sample_registry=rp_sreg");
	ok = ok && require_file_token("rp_api_labres", "instrument_registry=rp_instr");
	ok = ok && require_file_token("rp_api_pub", "result_review=rp_resrev");
	ok = ok && require_file_token("rp_api_know", "semantic_index=rp_semindex");
	ok = ok && require_file_token("rp_api_runtime", "runtime_env=rp_runenv");
	ok = ok && require_file_token("rp_api_action", "actions=8");
	ok = ok && require_file_token("rp_api_action", "delivery_manifest_builder=1");
	ok = ok && require_file_token("rp_api_action", "human_review_form=1");
	ok = ok && require_file_token("rp_api_action", "revision_task_runner=1");
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
	ok = ok && require_file_token("rp_uresrun", "run_id=usable-run:RUN-900");
	ok = ok && require_file_token("rp_uresrun", "runs=3");
	ok = ok && require_file_token("rp_uresrun", "run_id_2=usable-run:RUN-901");
	ok = ok && require_file_token("rp_uresrun", "run_id_3=usable-run:RUN-902");
	ok = ok && require_file_token("rp_uresrun", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && require_file_token("rp_uresrun", "revision_status=completed");
	ok = ok && require_file_token("rp_uresrun", "source_run=rp_runner");
	ok = ok && require_file_token("rp_uresrun", "source_form=rp_input");
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
	ok = ok && require_file_token("rp_web_bundle", "render_sections=7");
	ok = ok && require_file_token("rp_web_bundle", "artifact_previews=3");
	ok = ok && require_file_token("rp_web_bundle", "request_form=rp_input");
	ok = ok && require_file_token("rp_web_bundle", "upload_files=rp_input");
	ok = ok && require_file_token("rp_web_bundle", "library_sources=rp_knowledge");
	ok = ok && require_file_token("rp_web_bundle", "delivery_manifest=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "delivery_files=8");
	ok = ok && require_file_token("rp_web_bundle", "delivery_checks=3");
	ok = ok && require_file_token("rp_web_bundle", "evidence_bundle_entries=12");
	ok = ok && require_file_token("rp_web_bundle", "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md");
	ok = ok && require_file_token("rp_web_bundle", "llm_roundtrip=ready");
	ok = ok && require_file_token("rp_web_bundle", "review_page=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "export_bundle=rp_package");
	ok = ok && require_file_token("rp_web_bundle", "runner_detail_fields=16");
	ok = ok && require_file_token("rp_web_bundle", "post_routes=8");
	ok = ok && require_file_token("rp_web_bundle", "human_reviews=1");
	ok = ok && require_file_token("rp_web_bundle", "revision_tasks=1");
	ok = ok && require_file_token("rp_web_bundle", "review_threads=2");
	ok = ok && require_file_token("rp_web_bundle", "review_action_items=2");
	ok = ok && require_file_token("rp_web_bundle", "custom_research_files=1");
	ok = ok && require_file_token("rp_web_bundle", "custom_research_runs=3");

	ok = ok && require_count("ack", rp_count_lines("rp_ack"), 38);
	ok = ok && require_count("tool", rp_count_lines("rp_tool"), 133);
	if (!ok) return 1;

	if (!rp_write_file("rp_tests",
			   "suite=plain-ucore-research-platform\n"
			   "tests=322\n"
			   "catalog=passed\n"
			   "data_pipeline=passed\n"
			   "bio_services=passed\n"
			   "lab_resources=passed\n"
			   "publication_services=passed\n"
			   "knowledge_services=passed\n"
			   "runtime_services=passed\n"
			   "api_actions=passed\n"
			   "custom_research=passed\n"
			   "research_input=passed\n"
			   "workflow=passed\n"
			   "workflow_runner_detail=passed\n"
			   "artifact_ops=passed\n"
			   "agent_collaboration=passed\n"
			   "ui_export=passed\n"
			   "host_web_export=passed\n"
			   "ui_render_data=passed\n"
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
	printf("rp_test_suite: tests=322 catalog=passed data=passed services=passed actions=passed custom=passed artifacts=passed workflow=passed collaboration=passed ui=passed web=passed llm=passed compare=passed status=passed\n");
	return 0;
}
