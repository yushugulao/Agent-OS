#include <agent.h>
#include <research_platform_state.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_info orch_info;
static struct agent_op orch_op;
static struct agent_result orch_result;
static struct agent_context_header orch_header;
static struct agent_context_record orch_records[4];
static struct agent_timeline_record orch_timeline_records[4];
static struct agent_provenance_edge orch_provenance_edges[4];
static struct agent_ledger_summary orch_ledger;
static struct agent_file_query orch_file_query;
static struct agent_file_query_result orch_file_query_result;
static struct agent_file_prefetch_hint orch_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];

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

static void make_kernel_op(struct agent_op *op, int tool_id,
			   uint64 request_id, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool_id;
	op->request_id = request_id;
	if (payload)
		strcpy(op->payload, payload);
}

static int text_contains(const char *text, const char *needle)
{
	int n = strlen(needle);

	if (n == 0)
		return 1;
	for (int i = 0; text[i]; i++)
		if (strncmp(text + i, needle, n) == 0)
			return 1;
	return 0;
}

static int seed_meta(int fid, const char *physical, const char *label,
		     const char *type, const char *state,
		     const char *summary, uint64 deps)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.logical_path, physical);
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, "RUN-042");
	strcpy(meta.stage, label);
	strcpy(meta.kind, type);
	strcpy(meta.status, state);
	strcpy(meta.summary, summary);
	meta.dependency_mask = deps;
	meta.flags = AGENT_FILE_META_F_PERSIST;
	return agent_file_meta_set(&meta);
}

static int seed_research_metadata(void)
{
	if (seed_meta(1, "r42align", "align", "artifact", "ok",
		      "align output is ready",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(2, "r42anlz", "analyze", "status", "ok",
		      "analysis completed from align",
		      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(3, "r42report", "report", "report", "ok",
		      "report artifact ready",
		      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(4, "r42archive", "archive", "artifact", "pending",
		      "archive waits for report", 0) < 0)
		return -1;
	return 0;
}

static int verify_kernel_dependency_path(void)
{
	int hint_count;

	make_kernel_op(&orch_op, AGENT_TOOL_DEPENDENCY_UPDATE, 9011,
		       "source=align;target=report;namespace=lab-gene-x;run_id=RUN-042");
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK) {
		printf("rp_agentos_orch: dependency_update_failed status=%d\n",
		       orch_result.status);
		return -1;
	}

	make_kernel_op(&orch_op, AGENT_TOOL_DEPENDENCY_QUERY, 9012,
		       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK ||
	    !text_contains(orch_result.result, "report")) {
		printf("rp_agentos_orch: dependency_query_failed status=%d result=%s\n",
		       orch_result.status, orch_result.result);
		return -1;
	}

	memset(&orch_file_query, 0, sizeof(orch_file_query));
	orch_file_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	orch_file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(orch_file_query.project, "lab-gene-x");
	strcpy(orch_file_query.run_id, "RUN-042");
	strcpy(orch_file_query.stage, "align");
	if (agent_file_query(&orch_file_query, &orch_file_query_result) < 1 ||
	    orch_file_query_result.returned < 1 ||
	    orch_file_query_result.used_index != 1 ||
	    orch_file_query_result.plan != AGENT_FILE_QUERY_PLAN_STAGE_INDEX) {
		printf("rp_agentos_orch: metadata_query_failed hits=%d index=%d plan=%d\n",
		       orch_file_query_result.returned,
		       orch_file_query_result.used_index,
		       orch_file_query_result.plan);
		return -1;
	}

	hint_count = agent_file_prefetch_snapshot(
		orch_prefetch_hints, AGENT_FILE_PREFETCH_MAX_HINTS);
	if (hint_count < 1 ||
	    (orch_prefetch_hints[0].reason &
	     AGENT_FILE_PREFETCH_REASON_DEPENDENCY) == 0) {
		printf("rp_agentos_orch: prefetch_hint_failed count=%d reason=%d\n",
		       hint_count, (int)orch_prefetch_hints[0].reason);
		return -1;
	}

	if (!rp_append_file("rp_agentos_kernel",
			    "dependency_update=generic_record\n"
			    "dependency_query=generic_record\n"
			    "metadata_query=stage_index\n"
			    "prefetch_hint=dependency_driven\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=entry_dependency;dependency_graph=kernel_records;status=ready")) {
		return -1;
	}
	if (!rp_append_file("rp_tool", "tool=agentos.dependency_update"))
		return -1;
	if (!rp_append_file("rp_tool", "tool=agentos.dependency_query"))
		return -1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_prefetch"))
		return -1;
	return 0;
}

static int run_research_orchestrator(void)
{
	int timeline_count;
	int edge_count;
	int info_ret;

	info_ret = agent_info(&orch_info);
	if (info_ret < 0 || !orch_info.is_agent ||
	    orch_info.agent_role != AGENT_ROLE_ORCHESTRATOR) {
		printf("rp_agentos_orch: not_orchestrator_agent ret=%d is_agent=%d role=%d caps=%p\n",
		       info_ret, orch_info.is_agent, orch_info.agent_role,
		       (void *)orch_info.capability_mask);
		return 1;
	}

	if (agent_file_meta_init() < 0) {
		printf("rp_agentos_orch: file_meta_init_failed\n");
		return 1;
	}
	if (seed_research_metadata() < 0) {
		printf("rp_agentos_orch: seed_metadata_failed\n");
		return 1;
	}

	make_echo(&orch_op, 9001, "rp-agentos-orch");
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK) {
		printf("rp_agentos_orch: agent_run_failed status=%d\n",
		       orch_result.status);
		return 1;
	}

	int snapshot = context_snapshot(&orch_header, orch_records, 4);
	if (snapshot < 1 || orch_header.latest_sequence == 0) {
		printf("rp_agentos_orch: context_snapshot_failed n=%d\n",
		       snapshot);
		return 1;
	}
	timeline_count = agent_timeline_snapshot(orch_timeline_records, 4);
	edge_count = agent_provenance_snapshot(orch_provenance_edges, 4);
	if (timeline_count < 1 || edge_count < 0 ||
	    agent_ledger_snapshot(&orch_ledger) < 0) {
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
	if (!rp_write_file("rp_agentos_mainflow",
			   "stage=entry;context_trusted=kernel_shadow;status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("agentos_kernel=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.agent_run.echo")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.context_snapshot")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_meta_init")) return 1;
	if (verify_kernel_dependency_path() < 0) return 1;

	printf("rp_agentos_orch: agent role=%d context=%p size=%p latest=%d\n",
	       orch_info.agent_role, (void *)orch_info.context_base,
	       (void *)orch_info.context_size, (int)orch_header.latest_sequence);

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
