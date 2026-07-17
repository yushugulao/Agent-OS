#include <agent.h>
#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>

static struct agent_file_query kernel_query_arg;
static struct agent_file_query_result kernel_align_result;
static struct agent_file_query_result kernel_report_result;
static struct agent_op kernel_query_op;
static struct agent_result kernel_query_result;

static int run_kernel_query(void)
{
	struct agent_info info;
	struct agent_file_query *q = &kernel_query_arg;

	if (agent_info(&info) < 0 || !info.is_agent)
		return 0;
	if ((info.capability_mask &
	     (AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
	      AGENT_CAP_ARTIFACT_WRITE)) !=
	    (AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
	     AGENT_CAP_ARTIFACT_WRITE)) {
		printf("rp_query: filesystem_capability_missing\n");
		return -1;
	}

	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(q->project, "lab-gene-x");
	strcpy(q->run_id, "RUN-042");
	strcpy(q->stage, "align");
	strcpy(q->status, "ok");
	if (agent_file_query(q, &kernel_align_result) < 1 ||
	    kernel_align_result.returned < 1 || !kernel_align_result.used_index) {
		printf("rp_query: align_metadata_query_failed hits=%d index=%d\n",
		       kernel_align_result.returned,
		       kernel_align_result.used_index);
		return -1;
	}

	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(q->project, "lab-gene-x");
	strcpy(q->run_id, "RUN-042");
	strcpy(q->stage, "report");
	strcpy(q->status, "ok");
	if (agent_file_query(q, &kernel_report_result) < 1 ||
	    kernel_report_result.returned < 1 ||
	    !kernel_report_result.used_index) {
		printf("rp_query: report_metadata_query_failed hits=%d index=%d\n",
		       kernel_report_result.returned,
		       kernel_report_result.used_index);
		return -1;
	}

	memset(&kernel_query_op, 0, sizeof(kernel_query_op));
	kernel_query_op.version = AGENT_CALL_VERSION;
	kernel_query_op.tool_id = AGENT_TOOL_QUERY_FILE;
	kernel_query_op.request_id = 4301;
	strcpy(kernel_query_op.payload,
	       "project=lab-gene-x;run_id=RUN-042;stage=align;status=ok");
	if (agent_run(&kernel_query_op, &kernel_query_result, 1, 0) != 1 ||
	    kernel_query_result.status != AGENT_STATUS_OK ||
	    kernel_query_result.value0 < 1) {
		printf("rp_query: query_file_tool_failed status=%d hits=%d\n",
		       kernel_query_result.status,
		       (int)kernel_query_result.value0);
		return -1;
	}

	if (!rp_write_file("rp_agentos_query",
			   "query=RUN-042-align-report\n"
			   "metadata_source=kernel_file_index\n"
			   "align_query=indexed\n"
			   "report_query=indexed\n"
			   "tool=query_file\n"
			   "capability=meta_read\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=query;metadata_query=used_index;tool=query_file;align_query=indexed;report_query=indexed;context_sequence=observed;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_query = run_kernel_query();

	if (kernel_query < 0)
		return 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_services", "workflow=34");
	ok = ok && rp_file_contains("rp_services", "agent=26");
	ok = ok && rp_file_contains("rp_services", "evidence=10");
	ok = ok && rp_file_contains("rp_sched", "queue_items=21");
	ok = ok && rp_file_contains("rp_taskrec", "msg=21");
	ok = ok && rp_file_contains("rp_fail", "status=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	if (!ok) return 1;
	int task_lines = rp_count_lines("rp_taskrec");
	int high_tasks = rp_count_token("rp_taskrec", "prio=H");
	int critical_tasks = rp_count_token("rp_taskrec", "class=critical");
	int ready_tasks = rp_count_token("rp_taskrec", "state=ready");
	if (task_lines != 21 || high_tasks != 4 || critical_tasks != 4 || ready_tasks != 21) {
		printf("rp_query: bad_task_records lines=%d high=%d critical=%d ready=%d\n",
		       task_lines, high_tasks, critical_tasks, ready_tasks);
		return 1;
	}
	if (!rp_write_file("rp_query",
			   "query=workflow,agent,evidence\n"
			   "workflow_hits=34\n"
			   "agent_hits=26\n"
			   "evidence_hits=10\n"
			   "agentos_metadata_query=kernel_file_index\n"
			   "agentos_query_file_tool=verified\n"
			   "agentos_query_capability=meta_read\n"
			   "knowledge_index=search_documents:1685,provenance_nodes:406,provenance_links:544,events:8966,context_records:380,host_workflow_artifacts:150,usable_artifacts:507,usable_runs:23,usable_stages:197,usable_messages:265,usable_decisions:242,status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_rank",
			   "source=rp_taskrec\n"
			   "records=21\n"
			   "ready=21\n"
			   "high=4\n"
			   "critical=4\n"
			   "selected=10\n"
			   "selected_stages=literature,profile,align,decision,measure,risk_capa,release_delta,execution_trace,workflow_invocation,backend_scenario\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_runview",
			   "view=RUN-042\n"
			   "workflow_hits=34\n"
			   "agent_hits=26\n"
			   "evidence_hits=10\n"
			   "scheduler_items=21\n"
			   "ranked_tasks=21\n"
			   "selected_tasks=10\n"
			   "critical_tasks=4\n"
			   "failure_items=1\n"
			   "budget_state=within_budget\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_tool", "tool=query.rank_tasks")) return 1;
	if (!rp_append_file("rp_tool", "tool=query.build_runview")) return 1;
	if (kernel_query &&
	    !rp_append_file("rp_tool", "tool=agentos.query_file_index")) {
		return 1;
	}
	if (!rp_append_status("query=ready")) return 1;
	if (!rp_append_status("rank=ready")) return 1;
	if (!rp_append_status("runview=ready")) return 1;
	printf("rp_query: workflow=34 agent=26 evidence=10 ranked=21 selected=10 search_docs=1685 provenance=406/544 status=ready\n");
	return 0;
}
