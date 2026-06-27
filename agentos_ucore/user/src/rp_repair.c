#include <agent.h>
#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>

static struct agent_op repair_ops[2];
static struct agent_result repair_results[2];
static struct agent_context_header repair_header;
static struct agent_context_record repair_records[4];
static struct agent_file_query repair_query;
static struct agent_file_query_result repair_query_result;

static void make_recovery_op(struct agent_op *op, int tool_id,
			     uint64 request_id, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool_id;
	op->request_id = request_id;
	if (payload)
		strcpy(op->payload, payload);
}

static int run_kernel_recovery(void)
{
	struct agent_info info;

	if (agent_info(&info) < 0 || !info.is_agent)
		return 0;
	if (info.agent_role != AGENT_ROLE_RECOVERY) {
		printf("rp_repair: unexpected_agent_role role=%d\n",
		       info.agent_role);
		return -1;
	}

	make_recovery_op(&repair_ops[0], AGENT_TOOL_RERUN_STAGE, 4201,
			 "stage=align;project=lab-gene-x;run_id=RUN-042");
	make_recovery_op(&repair_ops[1], AGENT_TOOL_WRITE_REPORT, 4202,
			 "stage=report;project=lab-gene-x;run_id=RUN-042");
	if (agent_run(repair_ops, repair_results, 2, 0) != 2 ||
	    repair_results[0].status != AGENT_STATUS_OK ||
	    repair_results[1].status != AGENT_STATUS_OK) {
		printf("rp_repair: kernel_recovery_failed status=%d/%d\n",
		       repair_results[0].status, repair_results[1].status);
		return -1;
	}
	if (context_snapshot(&repair_header, repair_records, 4) < 2 ||
	    repair_header.latest_sequence < repair_results[1].sequence) {
		printf("rp_repair: context_snapshot_failed\n");
		return -1;
	}
	memset(&repair_query, 0, sizeof(repair_query));
	repair_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	repair_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(repair_query.project, "lab-gene-x");
	strcpy(repair_query.run_id, "RUN-042");
	strcpy(repair_query.stage, "align");
	strcpy(repair_query.status, "ok");
	if (agent_file_query(&repair_query, &repair_query_result) < 1 ||
	    repair_query_result.total_hits < 1 ||
	    !repair_query_result.used_index) {
		printf("rp_repair: repaired_metadata_query_failed hits=%d index=%d\n",
		       repair_query_result.total_hits,
		       repair_query_result.used_index);
		return -1;
	}
	if (!rp_write_file("rp_agentos_recovery",
			   "stage=align\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "kernel_tool=rerun_stage,write_report\n"
			   "context_snapshot=trusted\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=recovery;failure_recovery=kernel_tool;kernel_rerun_stage=ok;kernel_write_report=ok;context_snapshot=trusted;metadata_after_repair=used_index;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	int kernel_recovery = run_kernel_recovery();

	if (kernel_recovery < 0)
		return 1;
	if (!rp_file_contains("rp_data", "failed_stage=align")) return 1;
	if (!rp_file_contains("rp_fail", "recoverable=1")) return 1;
	if (!rp_file_contains("rp_mail", "to=repair")) return 1;
	if (!rp_file_contains("rp_retryq", "status=pending")) return 1;
	if (!rp_write_file("rp_fix",
			   "failed_stage=align\n"
			   "action=minimal_rerun\n"
			   "result=align.bam\n"
			   "agentos_recovery=kernel_tool\n"
			   "kernel_rerun_stage=ok\n"
			   "kernel_write_report=ok\n"
			   "status=recovered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_retrylog",
			   "failed_stage=align\n"
			   "dedupe_key=RUN-042:align\n"
			   "attempts=2\n"
			   "backoff_ticks=1\n"
			   "kernel_recovery_context=trusted\n"
			   "kernel_recovery_result=rerun_stage_ok\n"
			   "final_result=recovered\n"
			   "status=ready\n")) {
		return 1;
	}
	if (kernel_recovery &&
	    !rp_append_file("rp_tool", "tool=agentos.rerun_stage")) {
		return 1;
	}
	if (kernel_recovery &&
	    !rp_append_file("rp_tool", "tool=agentos.write_report")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=repair;msg=6;status=recovered")) return 1;
	if (!rp_append_file("rp_tool", "tool=repair.rerun_stage")) return 1;
	if (!rp_append_file("rp_tool", "tool=repair.record_retry")) return 1;
	if (!rp_append_status("repair=recovered")) return 1;
	if (!rp_append_status("retry=ready")) return 1;
	printf("rp_repair: failed_stage=align action=minimal_rerun attempts=2 status=recovered\n");
	return 0;
}
