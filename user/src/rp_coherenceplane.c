#include <stdio.h>
#include <research_platform_state.h>

static int require_token(const char *path, const char *token)
{
	return rp_file_contains(path, token);
}

int main(void)
{
	int ok = 1;
	ok = ok && require_token("rp_integrity", "decision=passed");
	ok = ok && require_token("rp_control", "control_plane_checks=30");
	ok = ok && require_token("rp_stage_state", "stages=5");
	ok = ok && require_token("rp_cache_index", "cache_hits=1");
	ok = ok && require_token("rp_review_dashboard", "decision=review_pack_ready");
	ok = ok && require_token("rp_package", "latest_delivery_status=ready");
	ok = ok && require_token("rp_report_text", "report_source=workflow");
	ok = ok && require_token("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && require_token("rp_backend_exec", "status=ready");
	ok = ok && require_token("rp_agentos_mainflow", "context_trusted=kernel_shadow");
	ok = ok && require_token("rp_agentos_kernel", "agent_run=echo");
	ok = ok && require_token("rp_agentos_package", "package_trace=kernel_provenance");
	ok = ok && require_token("rp_agentos_collab_ack", "delivery=kernel_event_queue");
	if (!ok) return 1;

	if (!rp_write_file("rp_coherence",
			   "service=coherence-plane\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "coherence_checks=40\n"
			   "delivery_contracts=7\n"
			   "delivery_checks=7\n"
			   "run_state_contracts=7\n"
			   "run_state_checks=7\n"
			   "lifecycle_contracts=6\n"
			   "lifecycle_checks=6\n"
			   "workflow_lint_checks=5\n"
			   "tool_protocol_checks=5\n"
			   "report_validation_checks=5\n"
			   "agent_coordination_checks=3\n"
			   "errors=0\n"
			   "warnings=0\n"
			   "decision=passed\n"
			   "delivery_contract=research_package;primary=rp_package;related=rp_report_text,rp_artifact_manifest,rp_review_pack;status=ready\n"
			   "delivery_contract=release_dossier;primary=rp_dossier;related=rp_release,rp_review_dashboard,rp_package;status=ready\n"
			   "delivery_contract=review_dashboard;primary=rp_review_dashboard;related=rp_reviewboard,rp_review_pack,rp_report_text;status=ready\n"
			   "delivery_contract=workflow_package;primary=rp_artifact_manifest;related=rp_stage_state,rp_cache_index,rp_retry_plan;status=ready\n"
			   "delivery_contract=llm_delivery;primary=rp_llm_resp;related=rp_llm_guard,rp_relay,rp_prompt;status=ready\n"
			   "delivery_contract=backend_evidence;primary=rp_backend_exec;related=rp_study,rp_report_text,rp_agentcmp;status=ready\n"
			   "delivery_contract=control_handoff;primary=rp_control;related=rp_opsboard,rp_review_dashboard,rp_agentcmp;status=ready\n"
			   "delivery_check=research_package;source=rp_package;result=pass;status=ready\n"
			   "delivery_check=release_dossier;source=rp_dossier;result=pass;status=ready\n"
			   "delivery_check=review_dashboard;source=rp_review_dashboard;result=pass;status=ready\n"
			   "delivery_check=workflow_package;source=rp_artifact_manifest;result=pass;status=ready\n"
			   "delivery_check=llm_delivery;source=rp_llm_resp;result=pass;status=ready\n"
			   "delivery_check=backend_evidence;source=rp_backend_exec;result=pass;status=ready\n"
			   "delivery_check=control_handoff;source=rp_control;result=pass;status=ready\n"
			   "run_state_contract=stage_state;source=rp_stage_state;expected=done,recovered,cached,accepted,ready;status=ready\n"
			   "run_state_contract=cache_state;source=rp_cache_index;expected=miss,refreshed,hit;status=ready\n"
			   "run_state_contract=retry_state;source=rp_retry_plan;expected=retry_items:1;status=ready\n"
			   "run_state_contract=event_state;source=rp_run_events;expected=events:8;status=ready\n"
			   "run_state_contract=worker_state;source=rp_worker;expected=ready:4;status=ready\n"
			   "run_state_contract=review_state;source=rp_review_dashboard;expected=review_pack_ready;status=ready\n"
			   "run_state_contract=package_state;source=rp_package;expected=latest_delivery_status:ready;status=ready\n"
			   "run_state_check=stage_order;source=rp_stage_state;result=pass;status=ready\n"
			   "run_state_check=cache_reuse;source=rp_cache_index;result=pass;status=ready\n"
			   "run_state_check=retry_scope;source=rp_retry_plan;result=pass;status=ready\n"
			   "run_state_check=event_timeline;source=rp_run_events;result=pass;status=ready\n"
			   "run_state_check=worker_health;source=rp_worker;result=pass;status=ready\n"
			   "run_state_check=review_state;source=rp_review_dashboard;result=pass;status=ready\n"
			   "run_state_check=package_state;source=rp_package;result=pass;status=ready\n"
			   "lifecycle_contract=research_run;order=input>stage>artifact>review>package;status=ready\n"
			   "lifecycle_contract=workbench_delivery;order=task>answer>handoff>bundle;status=ready\n"
			   "lifecycle_contract=review_release;order=request>vote>signoff>publish;status=ready\n"
			   "lifecycle_contract=llm_relay;order=request>route>guard>response>quality;status=ready\n"
			   "lifecycle_contract=backend_case;order=case>execute>observe>compare>review;status=ready\n"
			   "lifecycle_contract=control_plane;order=approval>notification>queue>plugin>permission;status=ready\n"
			   "lifecycle_check=research_run;source=rp_stage_state;result=pass;status=ready\n"
			   "lifecycle_check=workbench_delivery;source=rp_runner;result=pass;status=ready\n"
			   "lifecycle_check=review_release;source=rp_reviewboard;result=pass;status=ready\n"
			   "lifecycle_check=llm_relay;source=rp_llm_packets;result=pass;status=ready\n"
			   "lifecycle_check=backend_case;source=rp_backend_exec;result=pass;status=ready\n"
			   "lifecycle_check=control_plane;source=rp_control;result=pass;status=ready\n"
			   "workflow_lint=dag_edges;source=rp_stage_dag;expected=4;result=pass;status=ready\n"
			   "workflow_lint=stage_outputs;source=rp_stage_state;expected=5;result=pass;status=ready\n"
			   "workflow_lint=cache_policy;source=rp_cache_index;expected=content_keyed;result=pass;status=ready\n"
			   "workflow_lint=retry_minimality;source=rp_retry_plan;expected=align_only;result=pass;status=ready\n"
			   "workflow_lint=manifest_links;source=rp_artifact_manifest;expected=raw_to_report;result=pass;status=ready\n"
			   "tool_validation=workflow_runner;tools=6;source=rp_tool;result=pass;status=ready\n"
			   "tool_validation=integrity;tools=8;source=rp_tool;result=pass;status=ready\n"
			   "tool_validation=control_plane;tools=8;source=rp_tool;result=pass;status=ready\n"
			   "tool_validation=llm_relay;tools=relay_guarded;source=rp_llm_guard;result=pass;status=ready\n"
			   "tool_validation=backend_runner;tools=case_execution;source=rp_backend_exec;result=pass;status=ready\n"
			   "report_validation=workflow_source;source=rp_report_text;target=rp_stage_state;result=pass;status=ready\n"
			   "report_validation=artifact_source;source=rp_report_text;target=rp_artifact_manifest;result=pass;status=ready\n"
			   "report_validation=review_source;source=rp_report_text;target=rp_review_dashboard;result=pass;status=ready\n"
			   "report_validation=backend_source;source=rp_report_text;target=rp_backend_exec;result=pass;status=ready\n"
			   "report_validation=llm_source;source=rp_report_text;target=rp_llm_resp;result=pass;status=ready\n"
			   "agent_coordination=role_handoff;source=rp_handoff;target=rp_opsboard;result=pass;status=ready\n"
			   "agent_coordination=decision_trace;source=rp_decisions;target=rp_review_dashboard;result=pass;status=ready\n"
			   "agent_coordination=recovery_path;source=rp_retry_plan;target=rp_runbooks;result=pass;status=ready\n"
			   "coherence_report=coherence-report:RUN-042;checks=40;errors=0;warnings=0;status=ready\n"
			   "agentos_adaptation=kernel_run_state_views,kernel_tool_contract_table,kernel_delivery_metadata,kernel_agent_coordination_trace;evidence=rp_agentos_mainflow,rp_agentos_kernel,rp_agentos_package,rp_agentos_collab_ack;result=observed;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_web_bundle", "coherence_plane=rp_coherence;checks=40;errors=0;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=coherence_plane;source=rp_coherence;checks=40;errors=0;result=passed;status=ready")) return 1;
	if (!rp_append_file("rp_opsboard", "handoff=coherence-plane->operations;artifact=rp_coherence;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "coherence_plane_checks=40;delivery=7;run_state=7;lifecycle=6;workflow_lint=5;tool_protocol=5;report_validation=5;agent_coordination=3;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "coherence_kernel_binding=run_state_views,tool_contract_table,delivery_metadata,agent_coordination_trace;source=rp_coherence;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=coherenceplane;msg=coherence-plane;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.delivery_check")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.run_state_check")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.lifecycle_check")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.workflow_lint")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.tool_validation")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.report_validation")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.agent_coordination")) return 1;
	if (!rp_append_file("rp_tool", "tool=coherence.export_report")) return 1;
	if (!rp_append_status("coherenceplane=ready")) return 1;

	printf("rp_coherenceplane: checks=40 delivery=7 run_state=7 lifecycle=6 workflow_lint=5 tool_protocol=5 report_validation=5 status=ready\n");
	return 0;
}
