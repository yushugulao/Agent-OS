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
	ok = ok && require_file_token("rp_stage_state", "stages=5");
	ok = ok && require_file_token("rp_cache_index", "cache_hits=1");
	ok = ok && require_file_token("rp_retry_plan", "retry_items=1");
	ok = ok && require_file_token("rp_run_events", "events=8");
	ok = ok && require_file_token("rp_artifact_manifest", "manifest_records=4");
	ok = ok && require_file_token("rp_agents", "agents=7");
	ok = ok && require_file_token("rp_decisions", "decisions=8");
	ok = ok && require_file_token("rp_handoff", "handoffs=6");
	ok = ok && require_file_token("rp_deliberation", "items=5");
	ok = ok && require_file_token("rp_agent_run", "agent_decisions=8");

	ok = ok && require_file_token("rp_llmq", "secret_policy=no_secret_in_ucore");
	ok = ok && require_file_token("rp_llm_resp", "responses=3");
	ok = ok && require_file_token("rp_relay", "network_stack=host_only");
	ok = ok && require_file_token("rp_llm_packets", "packets=3");
	ok = ok && require_file_token("rp_llm_routes", "routes=4");
	ok = ok && require_file_token("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && require_file_token("rp_llm_hostreq", "template_mode=ready");
	ok = ok && require_file_token("rp_llm_fallback", "fallback_cases=1");
	ok = ok && require_file_token("rp_prompt", "provider_policy=host_relay");
	ok = ok && require_file_token("rp_llmeval", "passed=7");
	ok = ok && require_file_token("rp_privacy", "decision=accepted");
	ok = ok && require_file_token("rp_compliance", "decision=accepted");

	ok = ok && require_file_token("rp_evidence", "status=ready");
	ok = ok && require_file_token("rp_claimrec", "claim=8");
	ok = ok && require_file_token("rp_provpath", "critical_paths=3");
	ok = ok && require_file_token("rp_knowledge", "synthesis=ready");

	ok = ok && require_file_token("rp_runconf", "profiles=2");
	ok = ok && require_file_token("rp_invocation", "status=recovered");
	ok = ok && require_file_token("rp_completion", "actions=4");
	ok = ok && require_file_token("rp_package", "artifacts=42");
	ok = ok && require_file_token("rp_release", "decision=release");
	ok = ok && require_file_token("rp_dossier", "sections=36");

	ok = ok && require_file_token("rp_agentcmp", "context_trusted=0");
	ok = ok && require_file_token("rp_agentcmp", "message_acks=27");
	ok = ok && require_file_token("rp_agentcmp", "tool_events=106");
	ok = ok && require_file_token("rp_agentcmp", "agent_roles=7");
	ok = ok && require_file_token("rp_agentcmp", "relay_protocol_files=5");
	ok = ok && require_file_token("rp_agentcmp", "workflow_runner_files=5");
	ok = ok && require_file_token("rp_backend", "cases=4");
	ok = ok && require_file_token("rp_consistency", "checks=50");
	ok = ok && require_file_token("rp_telemetry", "metric_files=120");

	ok = ok && require_file_token("rp_ui_home", "page=home");
	ok = ok && require_file_token("rp_ui_run", "page=run-detail");
	ok = ok && require_file_token("rp_ui_run", "runner_exec=");
	ok = ok && require_file_token("rp_ui_agent", "page=agent-detail");
	ok = ok && require_file_token("rp_ui_agent", "decisions=8");
	ok = ok && require_file_token("rp_ui_evidence", "page=evidence-detail");
	ok = ok && require_file_token("rp_ui_compare", "page=compare-metrics");
	ok = ok && require_file_token("rp_ui_compare", "relay_protocol_files=5");

	ok = ok && require_count("ack", rp_count_lines("rp_ack"), 28);
	ok = ok && require_count("tool", rp_count_lines("rp_tool"), 111);
	if (!ok) return 1;

	if (!rp_write_file("rp_tests",
			   "suite=plain-ucore-research-platform\n"
			   "tests=64\n"
			   "catalog=passed\n"
			   "workflow=passed\n"
			   "artifact_ops=passed\n"
			   "agent_collaboration=passed\n"
			   "ui_export=passed\n"
			   "llm_relay=passed\n"
			   "agent_compare=passed\n"
			   "consistency=passed\n"
			   "status=passed\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=test_suite;msg=test;status=passed")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_catalog;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_workflow;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_artifacts;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_ui;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_llm;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_compare;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.check_consistency;target=rp_tests;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=test_suite.write_result;target=rp_tests;status=ok")) return 1;
	if (!rp_append_status("tests=ready")) return 1;
	printf("rp_test_suite: tests=64 catalog=passed artifacts=passed workflow=passed collaboration=passed ui=passed llm=passed compare=passed status=passed\n");
	return 0;
}
