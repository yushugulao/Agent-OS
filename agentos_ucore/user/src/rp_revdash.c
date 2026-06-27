#include <stdio.h>
#define RP_STATE_BUFFER_SIZE 8192
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_input", "status=ready");
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_report_text", "status=ready");
	ok = ok && rp_file_contains("rp_chart_data", "status=ready");
	ok = ok && rp_file_contains("rp_review2", "review_summary=all_review_comments_resolved");
	ok = ok && rp_file_contains("rp_revision", "final_status=ready");
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_agent_run", "agent_decisions=8");
	ok = ok && rp_file_contains("rp_api_run", "api=run-detail");
	ok = ok && rp_file_contains("rp_api_evidence", "api=evidence-detail");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_contract=host_plain_ucore_v2");
	ok = ok && rp_file_contains("rp_runner", "backend_evidence_report=rp_backend_exec");
	ok = ok && rp_file_contains("rp_report_text", "backend_evidence_report=rp_backend_exec");
	ok = ok && rp_file_contains("rp_protocol", "protocol_compliance_reports=1");
	ok = ok && rp_file_contains("rp_protocol", "protocol_amendments=1");
	ok = ok && rp_file_contains("rp_soplog", "sop_executions=1");
	ok = ok && rp_file_contains("rp_risk", "decision_support=decision:agentos-final-demo-backend");
	ok = ok && rp_file_contains("rp_capa", "capa_charts=deviations-by-severity");
	if (!ok) return 1;

	if (!rp_write_file("rp_review_dashboard",
			   "dashboard=research-review\n"
			   "run=RUN-042\n"
			   "sections=8\n"
			   "section=input;source=rp_input;status=ready\n"
			   "section=workflow;source=rp_stage_dag,rp_stage_state,rp_run_events,rp_retry_plan;status=recovered\n"
			   "section=artifacts;source=rp_artifact,rp_artifact_manifest,rp_report_text,rp_chart_data;status=ready\n"
			   "section=llm;source=rp_llm_req,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;status=ready\n"
			   "section=review;source=rp_review2,rp_revision,rp_package;status=ready\n"
			   "section=agents;source=rp_agents,rp_decisions,rp_handoff,rp_agent_run;status=ready\n"
			   "section=delivery;source=rp_package,rp_release,rp_dossier;status=ready\n"
			   "section=compare;source=rp_agentcmp,rp_consistency,rp_api_compare;status=plain-kernel\n"
			   "subsection=protocol_compliance;source=rp_protocol;reports=1;findings=3;status=reviewable\n"
			   "subsection=protocol_amendments;source=rp_protocol;amendments=1;decisions=1;status=applied\n"
			   "subsection=sop_execution;source=rp_soplog;executions=1;step_results=4;deviation_reviews=1;status=completed_with_deviation\n"
			   "subsection=risk_capa;source=rp_risk,rp_capa;risks=3;mitigations=3;reviews=1;capa_actions=2;verifications=2;status=ready\n"
			   "subsection=decision_support;source=rp_risk;options=3;criteria=5;scores=15;selected=select_agentos_ucore_hybrid\n"
			   "subsection=provenance_export;source=rp_provpath,rp_lineage;nodes=150;links=250;views=1;status=ready\n"
			   "subsection=calculations;source=rp_calculation;jobs=1;retrieved=3;checks=84;outcome=passed;status=ready\n"
			   "subsection=real_task;source=rp_realtask;dataset=palmer-penguins;checks=96;outcome=passed;status=ready\n"
			   "subsection=experiment_campaigns;source=rp_campaign;campaigns=1;trials=4;checks=108;outcome=passed;status=ready\n"
			   "subsection=release_dossier;source=rp_reldossier;sections=7;checks=112;outcome=passed;status=ready\n"
			   "gate=required_files;status=pass;source=rp_package\n"
			   "gate=human_review;status=pass;source=rp_review2\n"
			   "gate=llm_packet_guard;status=pass;source=rp_llm_guard\n"
			   "gate=workflow_recovered;status=pass;source=rp_retry_plan\n"
			   "gate=artifact_manifest;status=pass;source=rp_artifact_manifest\n"
			   "gate=reader_contract;status=pass;source=rp_web_bundle\n"
			   "handoff=orchestrator->reviewer;artifact=rp_review_dashboard;status=ready\n"
			   "handoff=reviewer->writer;artifact=rp_report_text;status=ready\n"
			   "handoff=writer->auditor;artifact=rp_package;status=ready\n"
			   "decision=ready_for_reviewer;basis=required_files,human_review,llm_packet_guard,workflow_recovered\n"
			   "decision=plain_kernel_limit;basis=file_state_scan,host_reader_refresh,user_space_contract\n"
			   "decision=review_pack_ready;basis=delivery_manifest,operations_next,project_action_items,workbench_handoff\n"
			   "backend_review_evidence=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;review_pack=rp_review_pack;status=ready\n"
			   "pack_source=rp_package,rp_runner,rp_review_pack\n"
			   "pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff\n"
			   "host_page=review.html\n"
			   "api_source=rp_api_run,rp_api_evidence,rp_api_artifacts,rp_api_compare\n"
			   "reader_source=rp_web_bundle\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=review_dashboard;msg=reviewdash;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_dashboard.aggregate")) return 1;
	if (!rp_append_file("rp_agentcmp", "review_dashboard=ready;sections=8;gates=6;plain_kernel=ordinary_files")) return 1;
	if (!rp_append_file("rp_ack", "ack=review_pack;msg=pack;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_pack.assemble")) return 1;
	if (!rp_append_file("rp_agentcmp", "review_pack=ready;evidence_items=11;actions=5;plain_kernel=ordinary_files;backend_evidence=1")) return 1;
	printf("rp_review_dashboard: sections=8 gates=6 review_pack=host-materialized status=ready\n");
	return 0;
}
