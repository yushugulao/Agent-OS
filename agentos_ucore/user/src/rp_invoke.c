#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_configval", "validations=2");
	ok = ok && rp_file_contains("rp_execplan", "workflow_steps=10");
	ok = ok && rp_file_contains("rp_taskrec", "msg=21");
	ok = ok && rp_file_contains("rp_rank", "selected=10");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_mail", "to=invoke");
	if (!ok) return 1;
	if (!rp_write_file("rp_invocation",
			   "invocation=workflow-invocation:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "template=lab-gene-x-nightly\n"
			   "steps=10\n"
			   "outputs=6\n"
			   "cache_reuse=2\n"
			   "status=recovered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_steps",
			   "records=10\n"
			   "completed=7\n"
			   "cached=2\n"
			   "failed=1\n"
			   "recovered=1\n"
			   "critical_stage=align\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_attempts",
			   "attempts=12\n"
			   "retry_attempts=2\n"
			   "worker=plain-uCore-user\n"
			   "cache_actions=2\n"
			   "final_result=recovered\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_invoke_export",
			   "export=workflow-invocation-export:RUN-042\n"
			   "format=markdown\n"
			   "includes=steps,attempts,outputs,logs\n"
			   "checksum=stable-demo\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=invoke;msg=19;status=recovered")) return 1;
	if (!rp_append_file("rp_tool", "tool=invoke.create_invocation")) return 1;
	if (!rp_append_file("rp_tool", "tool=invoke.sync_steps")) return 1;
	if (!rp_append_file("rp_tool", "tool=invoke.record_attempts")) return 1;
	if (!rp_append_file("rp_tool", "tool=invoke.export_invocation")) return 1;
	if (!rp_append_status("invocation=recovered")) return 1;
	if (!rp_append_status("steps=ready")) return 1;
	if (!rp_append_status("attempts=ready")) return 1;
	if (!rp_append_status("invoke_export=ready")) return 1;
	printf("rp_invoke: steps=10 attempts=12 outputs=6 status=recovered\n");
	return 0;
}
