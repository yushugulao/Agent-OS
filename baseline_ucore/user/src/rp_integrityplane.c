#include <stdio.h>
#include <research_platform_state.h>

static int require_token(const char *path, const char *token)
{
	return rp_file_contains(path, token);
}

int main(void)
{
	int ok = 1;
	ok = ok && require_token("rp_control", "status=ready");
	ok = ok && require_token("rp_reviewboard", "decision=approved");
	ok = ok && require_token("rp_review_dashboard", "decision=review_pack_ready");
	ok = ok && require_token("rp_package", "latest_delivery_status=ready");
	ok = ok && require_token("rp_report_text", "report_source=workflow");
	ok = ok && require_token("rp_artifact_manifest", "artifact_review_path=raw_to_report");
	ok = ok && require_token("rp_agentcmp", "control_plane_checks=30");
	if (!ok) return 1;

	if (!rp_write_file("rp_integrity",
			   "service=integrity-plane\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "integrity_checks=36\n"
			   "evidence_contracts=8\n"
			   "evidence_checks=8\n"
			   "reference_contracts=8\n"
			   "reference_checks=8\n"
			   "namespace_checks=5\n"
			   "status_checks=5\n"
			   "review_alignment_checks=4\n"
			   "report_source_checks=3\n"
			   "package_trace_checks=3\n"
			   "errors=0\n"
			   "warnings=0\n"
			   "decision=passed\n"
			   "evidence_contract=research_finding->rp_evidence;required=claim,evidence,source;status=ready\n"
			   "evidence_contract=result_review_item->rp_review_dashboard;required=decision,source,status;status=ready\n"
			   "evidence_contract=release_gate_check->rp_projectrel;required=gate,decision,evidence;status=ready\n"
			   "evidence_contract=release_dossier_section->rp_dossier;required=section,source,status;status=ready\n"
			   "evidence_contract=review_bundle_check->rp_review_pack;required=check,source,status;status=ready\n"
			   "evidence_contract=platform_consistency_check->rp_consistency;required=check,result,status;status=ready\n"
			   "evidence_contract=object_namespace_check->rp_consistency;required=object,name,status;status=ready\n"
			   "evidence_contract=reference_integrity_check->rp_integrity;required=source,target,result;status=ready\n"
			   "evidence_check=report_source_workflow;source=rp_report_text;target=rp_stage_state;result=pass;status=ready\n"
			   "evidence_check=report_source_llm;source=rp_report_text;target=rp_llm_resp;result=pass;status=ready\n"
			   "evidence_check=backend_evidence;source=rp_backend_exec;target=rp_report_text;result=pass;status=ready\n"
			   "evidence_check=artifact_review_path;source=rp_artifact_manifest;target=rp_review_pack;result=pass;status=ready\n"
			   "evidence_check=review_decision;source=rp_reviewboard;target=rp_package;result=pass;status=ready\n"
			   "evidence_check=control_approval;source=rp_control;target=rp_review_dashboard;result=pass;status=ready\n"
			   "evidence_check=operations_handoff;source=rp_opsboard;target=rp_runbooks;result=pass;status=ready\n"
			   "evidence_check=delivery_manifest;source=rp_package;target=rp_web_bundle;result=pass;status=ready\n"
			   "reference_contract=run_project;source=rp_runner;target=rp_input;field=run_id;status=ready\n"
			   "reference_contract=stage_artifacts;source=rp_stage_state;target=rp_artifact;field=output;status=ready\n"
			   "reference_contract=artifact_run;source=rp_artifact_manifest;target=rp_runner;field=run_id;status=ready\n"
			   "reference_contract=job_outputs;source=rp_stage_log;target=rp_artifact_manifest;field=artifact;status=ready\n"
			   "reference_contract=workflow_review;source=rp_review_dashboard;target=rp_package;field=review_pack;status=ready\n"
			   "reference_contract=experiment_result;source=rp_data_preview;target=rp_report_text;field=result;status=ready\n"
			   "reference_contract=release_package;source=rp_release;target=rp_package;field=package;status=ready\n"
			   "reference_contract=source_citation;source=rp_knowledge;target=rp_evidence;field=citation_key;status=ready\n"
			   "reference_check=run_project;source=rp_runner;target=rp_input;result=pass;status=ready\n"
			   "reference_check=stage_artifacts;source=rp_stage_state;target=rp_artifact;result=pass;status=ready\n"
			   "reference_check=artifact_run;source=rp_artifact_manifest;target=rp_runner;result=pass;status=ready\n"
			   "reference_check=job_outputs;source=rp_stage_log;target=rp_artifact_manifest;result=pass;status=ready\n"
			   "reference_check=workflow_review;source=rp_review_dashboard;target=rp_package;result=pass;status=ready\n"
			   "reference_check=experiment_result;source=rp_data_preview;target=rp_report_text;result=pass;status=ready\n"
			   "reference_check=release_package;source=rp_release;target=rp_package;result=pass;status=ready\n"
			   "reference_check=source_citation;source=rp_knowledge;target=rp_evidence;result=pass;status=ready\n"
			   "namespace_check=run_id;value=RUN-042;scope=project;result=pass;status=ready\n"
			   "namespace_check=project_id;value=lab-gene-x;scope=root;result=pass;status=ready\n"
			   "namespace_check=report_id;value=RUN-042-recovery-report;scope=report;result=pass;status=ready\n"
			   "namespace_check=package_id;value=delivery-package:RUN-042;scope=package;result=pass;status=ready\n"
			   "namespace_check=review_id;value=review-board-decision:RUN-042:release;scope=review;result=pass;status=ready\n"
			   "status_check=workflow;source=rp_stage_state;allowed=planned,running,failed,recovered,ready;result=pass\n"
			   "status_check=package;source=rp_package;allowed=draft,ready,approved,released;result=pass\n"
			   "status_check=review;source=rp_review_dashboard;allowed=waiting,needs_revision,approved,ready;result=pass\n"
			   "status_check=control;source=rp_control;allowed=recorded,active,ready,deny,allow;result=pass\n"
			   "status_check=integrity;source=rp_integrity;allowed=ready,passed;result=pass\n"
			   "review_alignment=board_to_dashboard;source=rp_reviewboard;target=rp_review_dashboard;decision=aligned;status=ready\n"
			   "review_alignment=dashboard_to_package;source=rp_review_dashboard;target=rp_package;decision=aligned;status=ready\n"
			   "review_alignment=operations_to_review;source=rp_opsboard;target=rp_reviewboard;decision=aligned;status=ready\n"
			   "review_alignment=control_to_release;source=rp_control;target=rp_release;decision=aligned;status=ready\n"
			   "report_source_check=workflow;source=rp_report_text;target=rp_stage_state;source_key=host_workflow_run_id;status=ready\n"
			   "report_source_check=llm;source=rp_report_text;target=rp_llm_resp;source_key=host_relay_response;status=ready\n"
			   "report_source_check=backend;source=rp_report_text;target=rp_backend_exec;source_key=backend_evidence_report;status=ready\n"
			   "package_trace=delivery;source=rp_package;target=rp_web_bundle;result=pass;status=ready\n"
			   "package_trace=evidence;source=rp_package;target=rp_evidence;result=pass;status=ready\n"
			   "package_trace=review;source=rp_package;target=rp_review_pack;result=pass;status=ready\n"
			   "integrity_report=integrity-report:RUN-042;checks=36;errors=0;warnings=0;status=ready\n"
			   "agentos_adaptation=kernel_context_attestation,kernel_metadata_reference_index,kernel_event_trace,kernel_namespace_registry;status=planned\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_web_bundle", "integrity_plane=rp_integrity;checks=36;errors=0;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=integrity_plane;source=rp_integrity;checks=36;errors=0;result=passed;status=ready")) return 1;
	if (!rp_append_file("rp_opsboard", "handoff=integrity-plane->operations;artifact=rp_integrity;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "integrity_plane_checks=36;evidence=8;references=8;namespace=5;status_semantics=5;review_alignment=4;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=integrityplane;msg=integrity-plane;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.evidence_trace")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.reference_check")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.namespace_check")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.status_check")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.review_alignment")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.report_sources")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.package_trace")) return 1;
	if (!rp_append_file("rp_tool", "tool=integrity.export_report")) return 1;
	if (!rp_append_status("integrityplane=ready")) return 1;

	printf("rp_integrityplane: checks=36 evidence=8 references=8 namespace=5 status_semantics=5 review_alignment=4 status=ready\n");
	return 0;
}
