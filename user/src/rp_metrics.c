#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_fix", "status=recovered")) return 1;
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_repro", "status=ready")) return 1;
	if (!rp_file_contains("rp_prompt", "routes=3")) return 1;
	if (!rp_file_contains("rp_mail", "to=metrics")) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 14 || tool_count < 20) return 1;
	if (!rp_write_file("rp_telemetry",
			   "run_id=RUN-042\n"
			   "trace_spans=8\n"
			   "bottlenecks=1\n"
			   "message_acks=14\n"
			   "tool_events=20\n"
			   "poll_rounds=18\n"
			   "scanned_records=128\n"
			   "state_files=44\n"
			   "ticks=42\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_agentcmp",
			   "case=user_on_plain_ucore\n"
			   "scanned_records=128\n"
			   "poll_rounds=18\n"
			   "syscall_count=0\n"
			   "spoof_denied=0\n"
			   "context_trusted=0\n"
			   "audit_events=1\n"
			   "report_ok=1\n"
			   "repro_ok=1\n"
			   "llm_guarded=1\n"
			   "message_acks=14\n"
			   "tool_events=20\n"
			   "ticks=42\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=metrics;msg=14;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=metrics.measure_plain;target=rp_agentcmp;status=ok")) return 1;
	if (!rp_append_status("telemetry=ready")) return 1;
	if (!rp_append_status("agentcmp=ready")) return 1;
	printf("rp_metrics: telemetry_spans=8 acks=14 tools=20 scanned=128 report_ok=1 repro_ok=1 status=ready\n");
	return 0;
}
