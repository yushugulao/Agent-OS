#include <stdio.h>
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
	if (!rp_append_file("rp_agentcmp", "review_pack=ready;evidence_items=10;actions=5;plain_kernel=ordinary_files")) return 1;
	printf("rp_review_dashboard: sections=8 gates=6 review_pack=host-materialized status=ready\n");
	return 0;
}
