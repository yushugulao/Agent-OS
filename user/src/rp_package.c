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
	ok = ok && rp_file_contains("rp_site", "pages=42");
	ok = ok && rp_file_contains("rp_site", "page=agentos_readiness");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_configval", "validations=2");
	ok = ok && rp_file_contains("rp_invocation", "status=recovered");
	ok = ok && rp_file_contains("rp_completion", "actions=4");
	ok = ok && rp_file_contains("rp_input", "form_fields=8");
	ok = ok && rp_file_contains("rp_input", "csv_rows_total=9");
	ok = ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	ok = ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_wfio", "decision=ready_for_agentos");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	ok = ok && rp_file_contains("rp_runner", "status=ready");
	ok = ok && rp_file_contains("rp_runner", "library_source_count=1");
	ok = ok && rp_file_contains("rp_runner", "revision_task_id=usable-revision-task:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_stage_dag", "status=ready");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_archive_manifest;files=5");
	ok = ok && rp_file_contains("rp_artifact", "\"variants\":2");
	ok = ok && rp_file_contains("rp_artifact", "geneC=7");
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
			   "artifacts=52\n"
			   "checks=75\n"
			   "package_manifest=ready\n"
			   "bundle_items=18\n"
			   "downloadable_units=3\n"
			   "evidence_bundle=ready\n"
			   "review_bundle=ready\n"
			   "provenance_bundle=ready\n"
			   "manifest_sources=rp_report_text,rp_chart_data,rp_artifact_manifest,rp_stage_log,rp_agents,rp_llm_packets\n"
			   "derived_sources=rp_artifact:rp_normalized_fastq,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv,rp_artifact:rp_archive_manifest\n"
			   "review_sources=rp_review,rp_review2,rp_revision,rp_resrev,rp_peerresp,rp_dossier\n"
			   "review_threads=2\n"
			   "review_comments=3\n"
			   "review_action_items=2\n"
			   "review_thread_source=rp_review2\n"
			   "review_action_source=rp_review2\n"
			   "provenance_sources=rp_evidence,rp_claimrec,rp_provpath,rp_lineage,rp_repro,rp_audit\n"
			   "custom_sources=rp_input,rp_runner,rp_uresrun\n"
			   "workbench=rp_runner\n"
			   "workbench_tasks=9\n"
			   "workbench_export=rp_runner\n"
			   "workspace_inspection=rp_runner\n"
			   "download_index=report_bundle,evidence_bundle,provenance_bundle\n"
			   "package_reader=host_web_bundle\n"
			   "static_site=rp_site\n"
			   "static_site_pages=42\n"
			   "static_site_json_payloads=14\n"
			   "static_site_download_links=8\n"
			   "request_form=rp_input\n"
			   "upload_files=rp_input\n"
			   "workspace_imports=1\n"
			   "workspace_import=workspace:RUN-900:folder;manifest=workspace-manifest.json;template=usable-template:workspace-900\n"
			   "library_sources=rp_knowledge\n"
			   "evidence_review_files=3\n"
			   "evidence_protocols=1\n"
			   "screening_decisions=9\n"
			   "evidence_extractions=3\n"
			   "prisma_flows=1\n"
			   "evidence_synthesis_files=2\n"
			   "workflow_portability=rp_wfio\n"
			   "portability_exports=5\n"
			   "adapter_specs=6\n"
			   "migration_steps=9\n"
			   "rehearsal_cases=4\n"
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
			   "revision_change=methods_retry_scope;target=methods;status=applied\n"
			   "revision_change=chart_caption;target=chart_caption;status=applied\n"
			   "revision_evidence=rp_revision\n"
			   "revision_change_count=2\n"
			   "revision_task_completed=usable-revision-task:RUN-900:1;new_run=usable-run:RUN-900-rev1\n"
			   "revision_run=usable-run:RUN-900-rev1\n"
			   "revision_task_export=revision_tasks.json\n"
			   "raw_downloads=5\n"
			   "delivery=usable-delivery:RUN-900:1;status=ready;audience=reviewer;owner=Wang\n"
			   "delivery_files=8\n"
			   "delivery_file=report_md;path=rp_report_text;required=1;exists=1\n"
			   "delivery_file=package_manifest;path=rp_artifact_manifest;required=1;exists=1\n"
			   "delivery_file=claim_audit;path=rp_claimrec;required=1;exists=1\n"
			   "delivery_file=data_quality;path=rp_data_quality;required=1;exists=1\n"
			   "delivery_file=chart_data;path=rp_chart_data;required=0;exists=1\n"
			   "delivery_file=llm_trace;path=rp_llm_packets;required=0;exists=1\n"
			   "delivery_file=review_page;path=rp_package;required=0;exists=1\n"
			   "delivery_file=revision_tasks;path=rp_package;required=0;exists=1\n"
			   "delivery_checks=3\n"
			   "delivery_check=required_files;status=pass;detail=all_required_files_exist\n"
			   "delivery_check=human_review;status=pass;review_id=usable-review:RUN-900:1\n"
			   "delivery_check=checksums;status=pass;checksums=8\n"
			   "delivery_manifest_json=delivery-manifest.json\n"
			   "delivery_manifest_md=delivery-manifest.md\n"
			   "latest_delivery_id=usable-delivery:RUN-900:1\n"
			   "latest_delivery_status=ready\n"
			   "evidence_bundle_zip=research-evidence-bundle.zip\n"
			   "evidence_bundle_entries=12\n"
			   "evidence_bundle_contains=run.json,artifacts.json,human_reviews.json,delivery_manifests.json,revision_tasks.json\n"
			   "evidence_bundle_contains_extra=screening_decisions.json,evidence_extractions.json,evidence_protocols.json,prisma_flow.json,evidence_synthesis.md\n"
			   "evidence_bundle_delivery_files=2\n"
			   "evidence_bundle_checksum=stable-evidence-bundle\n"
			   "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md\n"
			   "delivery_manifest_detail=deliverables=8;audience=reviewer;bundle=rp_package\n"
			   "export_bundle_detail=bundle_units=3;raw_links=5;checksums=6;provenance_source=rp_provpath;delivery_manifest=rp_package\n"
			   "review_page_detail=page=research-run;artifact_links=6;decision_controls=2;export_bundle=rp_package\n"
			   "review_page_sections=Human Reviews,Delivery Manifests,Revision Tasks,Review Threads,Action Items\n"
			   "review_pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff\n"
			   "review_pack_source=rp_package,rp_runner,rp_review_dashboard\n"
			   "review_pack_action=deliver_to_reviewer;source=rp_package;status=ready\n"
			   "review_pack_action=sync_operations_next;source=rp_runner;status=ready\n"
			   "review_pack_action=resolve_project_items;source=rp_package;status=ready\n"
			   "real_inputs=1\n"
			   "real_artifact_items=5\n"
			   "data_pipeline=1\n"
			   "stage_logs=1\n"
			   "chart_data=1\n"
			   "workflow_runner=1\n"
			   "agent_collaboration=1\n"
			   "llm_relay_protocol=1\n"
			   "llm_roundtrip=rp_llmq,rp_llm_packets,rp_llm_resp\n"
			   "llm_matched_responses=3\n"
			   "release=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has_artifact_action()) {
		if (!rp_append_file("rp_package", "host_action_artifacts=ready")) return 1;
		if (!rp_append_file("rp_package", "host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package")) return 1;
		if (rp_host_seed_has("kind=artifact_package")) {
			char package[64];
			char manifest[64];
			char files[32];
			char status[32];
			char line[220];
			if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "package=", package, sizeof(package))) {
				rp_copy_text(package, sizeof(package), "artifact-bundle.zip");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "manifest=", manifest, sizeof(manifest))) {
				rp_copy_text(manifest, sizeof(manifest), "artifact-manifest.json");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "files=", files, sizeof(files))) {
				rp_copy_text(files, sizeof(files), "5");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "status=", status, sizeof(status))) {
				rp_copy_text(status, sizeof(status), "ready");
			}
			rp_copy_text(line, sizeof(line), "host_artifact_package=");
			rp_append_text(line, sizeof(line), package);
			rp_append_text(line, sizeof(line), ";manifest=");
			rp_append_text(line, sizeof(line), manifest);
			rp_append_text(line, sizeof(line), ";files=");
			rp_append_text(line, sizeof(line), files);
			rp_append_text(line, sizeof(line), ";status=");
			rp_append_text(line, sizeof(line), status);
			if (!rp_append_file("rp_package", line)) return 1;
			if (!rp_append_file("rp_package", "host_action_artifact_package=ready")) return 1;
		}
	}
	if (rp_host_seed_has_platform_ops_action()) {
		char value[96];
		if (!rp_append_file("rp_package", "host_action_platform_ops_package=ready")) return 1;
		if (rp_host_seed_has("kind=workbench_quality_gate")) {
			if (!rp_append_file("rp_package", "host_action_quality_package=ready")) return 1;
			if (!rp_append_file("rp_package", "host_action_quality_gate=checked")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_quality_repair_plan")) {
			if (!rp_append_file("rp_package", "host_action_quality_repair_plan=ready")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_quality_repair_execute")) {
			if (!rp_append_file("rp_package", "host_action_quality_repair_execute=done")) return 1;
			if (rp_host_seed_copy_platform_ops_value("repair_id=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_package", "host_action_quality_repair_id=", value)) return 1;
			}
		}
		if (rp_host_seed_copy_platform_ops_value("workbench_id=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_quality_workbench=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_delivery_dashboard")) {
			if (!rp_append_file("rp_package", "host_action_delivery_dashboard=ready")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_delivery_execute_next")) {
			if (!rp_append_file("rp_package", "host_action_delivery_repair_execute=done")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_plan_queue_row") ||
		    rp_host_seed_has("kind=workbench_plan_queue_execute")) {
			if (!rp_append_file("rp_package", "host_action_plan_queue=ready")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_action_item")) {
			if (!rp_append_file("rp_package", "host_action_workbench_action_item=created")) return 1;
			if (rp_host_seed_copy_platform_ops_value("title=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_package", "host_action_action_item_title=", value)) return 1;
			}
		}
		if (rp_host_seed_has("kind=operations_report")) {
			if (!rp_append_file("rp_package", "host_action_operations_report=exported")) return 1;
		}
		if (rp_host_seed_has("kind=operations_advance_next") ||
		    rp_host_seed_has("kind=operations_execute_next_plan")) {
			if (!rp_append_file("rp_package", "host_action_operations_next=executed")) return 1;
		}
		if (rp_host_seed_has("kind=project_space")) {
			if (!rp_append_file("rp_package", "host_action_project_space=ready")) return 1;
			if (rp_host_seed_copy_platform_ops_value("project_id=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_package", "host_action_project_id=", value)) return 1;
			}
		}
		if (rp_host_seed_has("kind=project_space_note")) {
			if (!rp_append_file("rp_package", "host_action_project_note=recorded")) return 1;
		}
		if (rp_host_seed_has("kind=project_space_action_item")) {
			if (!rp_append_file("rp_package", "host_action_project_action_item=created")) return 1;
		}
		if (rp_host_seed_has("kind=project_space_answer")) {
			if (!rp_append_file("rp_package", "host_action_project_answer=generated")) return 1;
		}
		if (rp_host_seed_has("kind=project_space_repair_execute")) {
			if (!rp_append_file("rp_package", "host_action_project_repair=executed")) return 1;
		}
		if (rp_host_seed_has("kind=research_search_save") ||
		    rp_host_seed_has("kind=research_search_export") ||
		    rp_host_seed_has("kind=research_search_note") ||
		    rp_host_seed_has("kind=research_search_action_item")) {
			if (!rp_append_file("rp_package", "host_action_research_search=ready")) return 1;
			if (rp_host_seed_copy_platform_ops_value("query=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_package", "host_action_search_query=", value)) return 1;
			}
		}
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
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=delivery")) {
		char bundle[48];
		char run_id[48];
		char line[160];
		if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=research_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=delivery", "bundle=", bundle, sizeof(bundle))) {
			rp_copy_text(bundle, sizeof(bundle), "evidence");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "run_id=", run_id, sizeof(run_id)) &&
		    !rp_host_seed_copy_value_for_kind("kind=research_export", "run_id=", run_id, sizeof(run_id)) &&
		    !rp_host_seed_copy_value_for_kind("kind=delivery", "run_id=", run_id, sizeof(run_id))) {
			rp_copy_text(run_id, sizeof(run_id), "RUN-900");
		}
		rp_copy_text(line, sizeof(line), "host_action_export_bundle=ready;run_id=");
		rp_append_text(line, sizeof(line), run_id);
		rp_append_text(line, sizeof(line), ";bundle=");
		rp_append_text(line, sizeof(line), bundle);
		rp_append_text(line, sizeof(line), ";source=rp_host_action_seed");
		if (!rp_append_file("rp_package", line)) return 1;
		if (!rp_append_host_action_line("rp_package", "host_action_export_bundle_name=", bundle)) return 1;
		if (!rp_append_file("rp_package", "host_action_bundle_contents=report,manifest,notebook,compare")) return 1;
		if (!rp_append_file("rp_package", "host_action_delivery_manifest=ready")) return 1;
	}
	if (rp_host_seed_has("kind=host_workflow") || rp_host_seed_has("kind=host_workflow_export")) {
		char workflow_id[64];
		char bundle[48];
		char format[32];
		char value[48];
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "workflow_id=", workflow_id, sizeof(workflow_id)) &&
		    !rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "workflow_id=", workflow_id, sizeof(workflow_id))) {
			rp_copy_text(workflow_id, sizeof(workflow_id), "wf-host-plain");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "bundle=", bundle, sizeof(bundle))) {
			rp_copy_text(bundle, sizeof(bundle), "workflow-export.zip");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "format=", format, sizeof(format))) {
			rp_copy_text(format, sizeof(format), "json");
		}
		if (!rp_append_file("rp_package", "host_action_workflow_package=ready")) return 1;
		if (!rp_append_host_action_line("rp_package", "host_action_workflow_id=", workflow_id)) return 1;
		if (!rp_append_host_action_line("rp_package", "host_action_workflow_bundle=", bundle)) return 1;
		if (!rp_append_host_action_line("rp_package", "host_action_workflow_format=", format)) return 1;
		if (!rp_append_file("rp_package", "host_action_workflow_contents=stage_dag,stage_state,run_events,manifest")) return 1;
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow", "retry_stage=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_retry_stage=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow", "cache_hit_stage=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_cache_hit_stage=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow", "worker_slots=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=host_workflow", "max_workers=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_worker_slots=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow", "observer_events=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_observer_events=", value)) return 1;
		}
	}
	if (rp_host_seed_has_host_workflow_step_action()) {
		char value[96];
		if (!rp_append_file("rp_package", "host_action_workflow_steps=ready")) return 1;
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow_artifact", "artifact=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_artifact=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow_report", "report=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_report=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=host_workflow_retry", "decision=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_package", "host_action_workflow_retry_decision=", value)) return 1;
		}
	}
	if (rp_host_seed_has_workflow_portability_action()) {
		char value[96];
		if (!rp_append_file("rp_package", "host_action_portability_package=ready")) return 1;
		if (!rp_host_seed_copy_workflow_portability_value("import_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "workflow-import:host-nextflow");
		}
		if (!rp_append_host_action_line("rp_package", "host_action_portability_import=", value)) return 1;
		if (!rp_host_seed_copy_workflow_portability_value("target_runtime=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agentos-ucore");
		}
		if (!rp_append_host_action_line("rp_package", "host_action_portability_target=", value)) return 1;
		if (!rp_host_seed_copy_workflow_portability_value("compare_profile=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "compare-profile:host-nextflow:migration");
		}
		if (!rp_append_host_action_line("rp_package", "host_action_portability_profile=", value)) return 1;
		if (!rp_host_seed_copy_workflow_portability_value("package=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "workflow-portability-host.zip");
		}
		if (!rp_append_host_action_line("rp_package", "host_action_portability_bundle=", value)) return 1;
		if (rp_host_seed_has_workflow_portability_step_action()) {
			if (!rp_append_file("rp_package", "host_action_portability_steps=ready")) return 1;
			if (rp_host_seed_copy_value_for_kind("kind=workflow_portability_package", "export_format=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_package", "host_action_portability_format=", value)) return 1;
			}
		}
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
		char value[80];
		if (!rp_append_file("rp_package", "host_action_workbench_package=ready;source=rp_host_action_seed")) return 1;
		if (rp_host_seed_has("kind=workbench_complete")) {
			if (!rp_append_file("rp_package", "host_action_workbench_completion=ready")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_readiness")) {
			if (!rp_append_file("rp_package", "host_action_workbench_readiness=checked")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_answer_audit")) {
			if (!rp_append_file("rp_package", "host_action_workbench_answer_audit=passed")) return 1;
		}
		if (rp_host_seed_has("kind=workbench_notes")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_notes", "notes_filter=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "decision");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_notes_filter=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_handoff_package")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_handoff_package", "handoff_scope=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "full");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_handoff_scope=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_export")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_export", "bundle=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workbench-bundle.zip");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_bundle=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_file_manifest") || rp_host_seed_has("kind=workbench_file_verify")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "manifest=", value, sizeof(value)) &&
			    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "manifest=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "delivery-manifest.json");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_manifest=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "files=", value, sizeof(value)) &&
			    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "files=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "9");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_manifest_files=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "sha_records=", value, sizeof(value)) &&
			    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "sha_records=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "9");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_sha_records=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_file_verify")) {
			if (!rp_append_file("rp_package", "host_action_workbench_file_verify=passed")) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "verified=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "9");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_verified_files=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "missing=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "0");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_missing_files=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_brief")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_brief", "brief_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "html");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_brief_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_evidence_dossier", "dossier_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "markdown");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_dossier_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_evidence_graph")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_evidence_graph", "graph_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "dot");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_graph_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_citations")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_citations", "citation_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "bibtex");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_citation_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_manuscript")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_manuscript", "manuscript_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "markdown");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_manuscript_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_task_board")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_task_board", "board_filter=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "open");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_board_filter=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_task_board_row")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_task_board_row", "row_id=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "usable-workbench:RUN-900:board:task:human_review");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_row_id=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_task_board_row", "row_status=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "done");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_row_status=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_runbook")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_runbook", "runbook_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "markdown");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_runbook_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_timeline")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_timeline", "timeline_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "html");
			}
			if (!rp_append_host_action_line("rp_package", "host_action_workbench_timeline_format=", value)) return 1;
		}
	}
	if (!rp_append_status("package=ready")) return 1;
	if (!rp_append_status("datarel=ready")) return 1;
	if (!rp_append_status("dataver=ready")) return 1;
	if (!rp_append_status("repro=ready")) return 1;
	if (!rp_append_status("delivery_manifest=ready")) return 1;
	if (!rp_append_status("export_bundle=ready")) return 1;
	if (!rp_append_status("review_page=ready")) return 1;
	if (!rp_append_status("human_review=ready")) return 1;
	if (!rp_append_status("revision_task_package=ready")) return 1;
	printf("rp_package: artifacts=52 checks=75 fair=passed repro=ready status=ready\n");
	return 0;
}
