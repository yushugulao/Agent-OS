#include <agent.h>
#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>

static struct agent_audit_record kernel_audit_records[8];
static struct agent_provenance_edge kernel_edges[8];
static struct agent_ledger_summary kernel_ledger;
static struct agent_context_header kernel_header;
static struct agent_context_record kernel_records[8];
static struct agent_op auditor_op;
static struct agent_result auditor_result;

static int run_kernel_audit(void)
{
	struct agent_info info;
	int audit_count;
	int edge_count;
	int context_count;

	if (agent_info(&info) < 0 || !info.is_agent)
		return 0;
	if ((info.capability_mask & AGENT_CAP_AUDIT_WRITE) == 0) {
		printf("rp_auditor: audit_capability_missing\n");
		return -1;
	}
	memset(&auditor_op, 0, sizeof(auditor_op));
	auditor_op.version = AGENT_CALL_VERSION;
	auditor_op.tool_id = AGENT_TOOL_ECHO;
	auditor_op.request_id = 4101;
	strcpy(auditor_op.payload, "audit-kernel-check");
	if (agent_run(&auditor_op, &auditor_result, 1, 0) != 1 ||
	    auditor_result.status != AGENT_STATUS_OK) {
		printf("rp_auditor: agent_run_failed status=%d\n",
		       auditor_result.status);
		return -1;
	}
	audit_count = agent_audit_snapshot(kernel_audit_records, 8);
	edge_count = agent_provenance_snapshot(kernel_edges, 8);
	context_count = context_snapshot(&kernel_header, kernel_records, 8);
	if (audit_count < 1 || edge_count < 0 || context_count < 1 ||
	    agent_ledger_snapshot(&kernel_ledger) < 0 ||
	    kernel_ledger.total_records < (uint64)context_count) {
		printf("rp_auditor: kernel_audit_snapshot_failed audit=%d edge=%d context=%d\n",
		       audit_count, edge_count, context_count);
		return -1;
	}
	if (!rp_write_file("rp_agentos_audit",
			   "audit_source=kernel_ledger\n"
			   "context_source=kernel_shadow\n"
			   "provenance_source=kernel_edges\n"
			   "record_hash=observed\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=audit;provenance_audit=kernel_ledger;ledger_hash=observed;audit_records=observed;provenance_edges=observed;context_records=observed;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_audit = run_kernel_audit();

	if (kernel_audit < 0)
		return 1;
	ok = ok && rp_file_contains("rp_plan", "assignments=7");
	ok = ok && rp_file_contains("rp_lit", "status=ready");
	ok = ok && rp_file_contains("rp_data", "failed_stage=align");
	ok = ok && rp_file_contains("rp_review", "decision=accepted_after_repair");
	ok = ok && rp_file_contains("rp_report", "status=packaged");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_fail", "failure_class=tool_output_missing");
	ok = ok && rp_file_contains("rp_retrylog", "status=ready");
	ok = ok && rp_file_contains("rp_datadic", "schema_drift=0");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "verifications=2");
	ok = ok && rp_file_contains("rp_mail", "to=auditor");
	if (!ok) return 1;
	if (!rp_write_file("rp_audit",
			   "provenance=verified\n"
			   "agentos_audit=kernel_ledger\n"
			   "agentos_context=kernel_shadow\n"
			   "agentos_provenance=kernel_edges\n"
			   "release=ready\n"
			   "package=ready\n"
			   "schema=verified\n"
			   "replay=verified\n"
			   "labops=verified\n"
			   "risk=verified\n"
			   "capa=verified\n"
			   "retry=verified\n"
			   "failure=verified\n"
			   "status=passed\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=auditor;msg=7;status=passed")) return 1;
	if (!rp_append_file("rp_tool", "tool=auditor.verify_provenance")) return 1;
	if (kernel_audit &&
	    !rp_append_file("rp_tool", "tool=agentos.audit_snapshot")) {
		return 1;
	}
	if (kernel_audit &&
	    !rp_append_file("rp_tool", "tool=agentos.provenance_snapshot")) {
		return 1;
	}
	if (!rp_append_status("auditor=passed")) return 1;
	printf("rp_auditor: provenance=verified release=ready package=ready status=passed\n");
	return 0;
}
