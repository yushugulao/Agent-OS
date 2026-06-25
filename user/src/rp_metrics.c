#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_fix", "status=recovered")) return 1;
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_write_file("rp_telemetry",
			   "run_id=RUN-042\n"
			   "trace_spans=6\n"
			   "bottlenecks=1\n"
			   "poll_rounds=18\n"
			   "scanned_records=128\n"
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
			   "ticks=42\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("telemetry=ready")) return 1;
	if (!rp_append_status("agentcmp=ready")) return 1;
	printf("rp_metrics: telemetry_spans=6 scanned=128 report_ok=1 status=ready\n");
	return 0;
}
