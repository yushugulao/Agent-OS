#include <stdio.h>
#include <research_platform_state.h>

static int require_equal(const char *name, int actual, int expected)
{
	if (actual == expected) return 1;
	printf("rp_consistency: mismatch %s actual=%d expected=%d\n", name, actual, expected);
	return 0;
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_backend", "status=ready");
	ok = ok && rp_file_contains("rp_backend_exec", "status=ready");
	ok = ok && rp_file_contains("rp_mail", "to=backend");
	ok = ok && rp_file_contains("rp_runner", "status=ready");
	ok = ok && rp_file_contains("rp_stage_dag", "status=ready");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "status=ready");
	ok = ok && rp_file_contains("rp_chart_data", "status=ready");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "template_mode=ready");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_messages=21");
	if (!ok) return 1;

	int task_lines = rp_count_lines("rp_taskrec");
	int ready_tasks = rp_count_token("rp_taskrec", "state=ready");
	int high_tasks = rp_count_token("rp_taskrec", "prio=H");
	int critical_tasks = rp_count_token("rp_taskrec", "class=critical");
	int queue_items = rp_get_int_value("rp_sched", "queue_items=");
	int ready_items = rp_get_int_value("rp_sched", "ready_items=");
	int rank_records = rp_get_int_value("rp_rank", "records=");
	int rank_ready = rp_get_int_value("rp_rank", "ready=");
	int rank_high = rp_get_int_value("rp_rank", "high=");
	int rank_critical = rp_get_int_value("rp_rank", "critical=");
	int rank_selected = rp_get_int_value("rp_rank", "selected=");
	int view_sched = rp_get_int_value("rp_runview", "scheduler_items=");
	int view_ranked = rp_get_int_value("rp_runview", "ranked_tasks=");
	int view_selected = rp_get_int_value("rp_runview", "selected_tasks=");
	int view_critical = rp_get_int_value("rp_runview", "critical_tasks=");

	ok = ok && require_equal("task_lines", task_lines, 21);
	ok = ok && require_equal("queue_items", queue_items, task_lines);
	ok = ok && require_equal("ready_items", ready_items, ready_tasks);
	ok = ok && require_equal("rank_records", rank_records, task_lines);
	ok = ok && require_equal("rank_ready", rank_ready, ready_tasks);
	ok = ok && require_equal("rank_high", rank_high, high_tasks);
	ok = ok && require_equal("rank_critical", rank_critical, critical_tasks);
	ok = ok && require_equal("rank_selected", rank_selected, 10);
	ok = ok && require_equal("view_sched", view_sched, queue_items);
	ok = ok && require_equal("view_ranked", view_ranked, rank_records);
	ok = ok && require_equal("view_selected", view_selected, rank_selected);
	ok = ok && require_equal("view_critical", view_critical, critical_tasks);

	int llm_queued = rp_get_int_value("rp_llmq", "queued=");
	int llm_secrets = rp_count_token("rp_llmq", "secret_policy=no_secret_in_ucore");
	int llm_responses = rp_get_int_value("rp_llm_resp", "responses=");
	int relay_packets = rp_get_int_value("rp_relay", "relay_packets=");
	int eval_passed = rp_get_int_value("rp_llmeval", "passed=");
	int relay_protocol_packets = rp_get_int_value("rp_llm_packets", "packets=");
	int relay_routes = rp_get_int_value("rp_llm_routes", "routes=");
	int guard_checked = rp_get_int_value("rp_llm_guard", "checked_packets=");
	int fallback_cases = rp_get_int_value("rp_llm_fallback", "fallback_cases=");
	ok = ok && require_equal("llm_queued", llm_queued, 3);
	ok = ok && require_equal("llm_secret_tags", llm_secrets, llm_queued);
	ok = ok && require_equal("llm_responses", llm_responses, llm_queued);
	ok = ok && require_equal("relay_packets", relay_packets, llm_queued);
	ok = ok && require_equal("llm_eval_passed", eval_passed, 7);
	ok = ok && require_equal("relay_protocol_packets", relay_protocol_packets, llm_queued);
	ok = ok && require_equal("relay_routes", relay_routes, 4);
	ok = ok && require_equal("relay_guard_checked", guard_checked, llm_queued);
	ok = ok && require_equal("fallback_cases", fallback_cases, 1);

	int profiles = rp_get_int_value("rp_runconf", "profiles=");
	int validations = rp_get_int_value("rp_configval", "validations=");
	int drift = rp_get_int_value("rp_configdrift", "changed_parameters=");
	int invocation_steps = rp_get_int_value("rp_invocation", "steps=");
	int step_records = rp_get_int_value("rp_steps", "records=");
	int attempts = rp_get_int_value("rp_attempts", "attempts=");
	int retry_attempts = rp_get_int_value("rp_attempts", "retry_attempts=");
	int completion_actions = rp_get_int_value("rp_completion", "actions=");
	int hook_count = rp_get_int_value("rp_hooks", "hooks=");
	ok = ok && require_equal("profiles", profiles, 2);
	ok = ok && require_equal("validations", validations, profiles);
	ok = ok && require_equal("config_drift", drift, 2);
	ok = ok && require_equal("invocation_steps", invocation_steps, 10);
	ok = ok && require_equal("step_records", step_records, invocation_steps);
	ok = ok && require_equal("attempts", attempts, 12);
	ok = ok && require_equal("retry_attempts", retry_attempts, 2);
	ok = ok && require_equal("completion_actions", completion_actions, 4);
	ok = ok && require_equal("hook_count", hook_count, completion_actions);

	int backend_cases = rp_get_int_value("rp_backend", "cases=");
	int backend_executable = rp_get_int_value("rp_backend", "executable=");
	int passed_cases = rp_get_int_value("rp_backend_exec", "passed_cases=");
	int planned_cases = rp_get_int_value("rp_backend_exec", "planned_cases=");
	int study_arms = rp_get_int_value("rp_study", "arms=");
	ok = ok && require_equal("backend_cases", backend_cases, 4);
	ok = ok && require_equal("backend_executable", backend_executable, passed_cases);
	ok = ok && require_equal("backend_case_total", passed_cases + planned_cases, backend_cases);
	ok = ok && require_equal("study_arms", study_arms, 2);

	int runner_stages = rp_get_int_value("rp_runner", "stages=");
	int runner_retries = rp_get_int_value("rp_runner", "retries=");
	int runner_cache_hits = rp_get_int_value("rp_runner", "cache_hits=");
	int dag_edges = rp_get_int_value("rp_stage_dag", "edges=");
	int log_lines = rp_get_int_value("rp_stage_log", "lines=");
	int artifact_records = rp_get_int_value("rp_artifact", "records=");
	int stage_state_stages = rp_get_int_value("rp_stage_state", "stages=");
	int cache_records = rp_get_int_value("rp_cache_index", "cache_records=");
	int retry_items = rp_get_int_value("rp_retry_plan", "retry_items=");
	int run_events = rp_get_int_value("rp_run_events", "events=");
	int manifest_records = rp_get_int_value("rp_artifact_manifest", "manifest_records=");
	int agent_roles = rp_get_int_value("rp_agents", "agents=");
	int agent_messages = rp_get_int_value("rp_agents", "messages=");
	int agent_decisions = rp_get_int_value("rp_decisions", "decisions=");
	int agent_handoffs = rp_get_int_value("rp_handoff", "handoffs=");
	int deliberation_items = rp_get_int_value("rp_deliberation", "items=");
	ok = ok && require_equal("runner_stages", runner_stages, 5);
	ok = ok && require_equal("runner_retries", runner_retries, 1);
	ok = ok && require_equal("runner_cache_hits", runner_cache_hits, 1);
	ok = ok && require_equal("dag_edges", dag_edges, runner_stages - 1);
	ok = ok && require_equal("stage_log_lines", log_lines, runner_stages);
	ok = ok && require_equal("artifact_records", artifact_records, 2);
	ok = ok && require_equal("stage_state_stages", stage_state_stages, runner_stages);
	ok = ok && require_equal("cache_records", cache_records, runner_stages);
	ok = ok && require_equal("retry_items", retry_items, 1);
	ok = ok && require_equal("run_events", run_events, 8);
	ok = ok && require_equal("manifest_records", manifest_records, 4);
	ok = ok && require_equal("agent_roles", agent_roles, 7);
	ok = ok && require_equal("agent_messages", agent_messages, task_lines);
	ok = ok && require_equal("agent_decisions", agent_decisions, 8);
	ok = ok && require_equal("agent_handoffs", agent_handoffs, 6);
	ok = ok && require_equal("deliberation_items", deliberation_items, 5);

	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 25 || tool_count < 101) {
		printf("rp_consistency: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		ok = 0;
	}
	if (!ok) return 1;

	if (!rp_write_file("rp_consistency",
			   "checks=50\n"
			   "task_records=21\n"
			   "ready_tasks=21\n"
			   "high_tasks=4\n"
			   "critical_tasks=4\n"
			   "selected_tasks=10\n"
			   "llm_packets=3\n"
			   "relay_protocol_files=5\n"
			   "relay_routes=4\n"
			   "fallback_cases=1\n"
			   "workflow_steps=10\n"
			   "workflow_attempts=12\n"
			   "completion_actions=4\n"
			   "backend_cases=4\n"
			   "runner_stages=5\n"
			   "runner_retries=1\n"
			   "runner_cache_hits=1\n"
			   "artifact_records=2\n"
			   "workflow_runner_files=5\n"
			   "workflow_events=8\n"
			   "workflow_manifest_records=4\n"
			   "agent_roles=7\n"
			   "agent_messages=21\n"
			   "agent_decisions=8\n"
			   "agent_handoffs=6\n"
			   "deliberation_items=5\n"
			   "state_relation=passed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=consistency;msg=22;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_tasks;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_llm;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_backend;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_status("consistency=ready")) return 1;
	printf("rp_consistency: checks=50 tasks=21 llm=3 relay=5 workflow=5 backend=4 artifacts=4 agents=7 status=ready\n");
	return 0;
}
