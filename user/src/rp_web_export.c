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
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	if (!ok) return 1;

	if (!rp_write_file("rp_web_routes",
			   "service=host-web-ui\n"
			   "routes=7\n"
			   "route=/;payload=rp_api_home;status=ready\n"
			   "route=/run/RUN-042;payload=rp_api_run;status=ready\n"
			   "route=/agents;payload=rp_api_agents;status=ready\n"
			   "route=/evidence;payload=rp_api_evidence;status=ready\n"
			   "route=/compare;payload=rp_api_compare;status=ready\n"
			   "route=/artifacts;payload=rp_api_artifacts;status=ready\n"
			   "route=/data;payload=rp_api_data;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_home",
			   "api=home\n"
			   "title=Research Agent Platform\n"
			   "run_id=RUN-042\n"
			   "cards=run,agents,evidence,data,llm_relay,compare\n"
			   "source=rp_ui_home\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_api_run",
			   "api=run-detail\n"
			   "run_id=RUN-042\n"
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
	if (!rp_write_file("rp_web_bundle",
			   "bundle=host-web-ui\n"
			   "routes=7\n"
			   "api_payloads=7\n"
			   "source_pages=5\n"
			   "runner_files=5\n"
			   "data_pipeline_files=6\n"
			   "llm_relay_files=5\n"
			   "agent_records=5\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_ack", "ack=web_export;msg=web;status=ready")) return 1;
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
	printf("rp_web_export: routes=7 api_payloads=7 bundle=ready status=ready\n");
	return 0;
}
