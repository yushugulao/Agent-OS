#include <agent.h>
#include <research_platform_state.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void make_echo(struct agent_op *op, uint64 request_id,
		      const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = request_id;
	op->arg0 = request_id;
	op->arg1 = request_id + 1;
	strcpy(op->payload, payload);
}

static int run_research_orchestrator(void)
{
	struct agent_info info;
	struct agent_op op;
	struct agent_result result;
	struct agent_context_header header;
	struct agent_context_record records[4];
	struct agent_timeline_record timeline_records[4];
	struct agent_provenance_edge provenance_edges[4];
	struct agent_ledger_summary ledger;
	int timeline_count;
	int edge_count;

	if (agent_info(&info) < 0 || !info.is_agent ||
	    info.agent_role != AGENT_ROLE_ORCHESTRATOR) {
		printf("rp_agentos_orch: not_orchestrator_agent\n");
		return 1;
	}

	if (agent_file_meta_init() < 0) {
		printf("rp_agentos_orch: file_meta_init_failed\n");
		return 1;
	}

	make_echo(&op, 9001, "rp-agentos-orch");
	if (agent_run(&op, &result, 1, 0) != 1 ||
	    result.status != AGENT_STATUS_OK) {
		printf("rp_agentos_orch: agent_run_failed status=%d\n",
		       result.status);
		return 1;
	}

	int snapshot = context_snapshot(&header, records, 4);
	if (snapshot < 1 || header.latest_sequence == 0) {
		printf("rp_agentos_orch: context_snapshot_failed n=%d\n",
		       snapshot);
		return 1;
	}
	timeline_count = agent_timeline_snapshot(timeline_records, 4);
	edge_count = agent_provenance_snapshot(provenance_edges, 4);
	if (timeline_count < 1 || edge_count < 0 ||
	    agent_ledger_snapshot(&ledger) < 0) {
		printf("rp_agentos_orch: provenance_snapshot_failed timeline=%d edges=%d\n",
		       timeline_count, edge_count);
		return 1;
	}

	if (!rp_write_file("rp_agentos_kernel",
			   "target=agentos_ucore\n"
			   "mode=kernel_agent_orchestrated\n"
			   "agent_role=orchestrator\n"
			   "agent_context=present\n"
			   "agent_run=echo\n"
			   "context_snapshot=present\n"
			   "agent_timeline=observed\n"
			   "agent_provenance=observed\n"
			   "agent_ledger=observed\n"
			   "provenance_kernel=observed\n"
			   "file_meta_service=initialized\n"
			   "research_platform=rp_orch\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("agentos_kernel=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.agent_run.echo")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.context_snapshot")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_meta_init")) return 1;

	printf("rp_agentos_orch: agent role=%d context=%p size=%p latest=%d\n",
	       info.agent_role, (void *)info.context_base,
	       (void *)info.context_size, (int)header.latest_sequence);

	char *argv[] = {
		"rp_orch",
		0,
	};
	if (exec("rp_orch", argv) < 0) {
		printf("rp_agentos_orch: exec_failed program=rp_orch\n");
		return 1;
	}
	return 1;
}

int main(void)
{
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	if (pid < 0) {
		printf("rp_agentos_orch: create_orchestrator_failed\n");
		return 1;
	}
	if (pid == 0) {
		return run_research_orchestrator();
	}

	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		printf("rp_agentos_orch: wait_failed pid=%d got=%d\n", pid, got);
		return 1;
	}
	if (code != 0) {
		printf("rp_agentos_orch: child_failed code=%d\n", code);
		return 1;
	}
	if (!rp_file_contains("rp_agentos_kernel", "status=ready") ||
	    !rp_file_contains("rp_agentcmp", "status=ready")) {
		printf("rp_agentos_orch: state_check_failed\n");
		return 1;
	}
	printf("rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready\n");
	printf("rp_agentos_orch: passed\n");
	return 0;
}
