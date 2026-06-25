#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_invocation", "status=recovered");
	ok = ok && rp_file_contains("rp_attempts", "final_result=recovered");
	ok = ok && rp_file_contains("rp_invoke_export", "status=ready");
	ok = ok && rp_file_contains("rp_mail", "to=completion");
	if (!ok) return 1;
	if (!rp_write_file("rp_hooks",
			   "hooks=4\n"
			   "notify=1\n"
			   "runbook_triage=1\n"
			   "export_invocation=1\n"
			   "audit_record=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_completion",
			   "event=workflow-completion:RUN-042\n"
			   "run_id=RUN-042\n"
			   "invocation_status=recovered\n"
			   "events=1\n"
			   "actions=4\n"
			   "exports=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_actions",
			   "actions=4\n"
			   "ops_notification=sent\n"
			   "runbook=linked\n"
			   "evidence_export=ready\n"
			   "audit_record=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_complete_export",
			   "export=workflow-completion-export:RUN-042\n"
			   "format=markdown\n"
			   "contains=event,actions,hooks,invocation\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=completion;msg=20;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=completion.process_event;target=rp_completion;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=completion.execute_hooks;target=rp_actions;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=completion.export_event;target=rp_complete_export;status=ok")) return 1;
	if (!rp_append_status("hooks=ready")) return 1;
	if (!rp_append_status("completion=ready")) return 1;
	if (!rp_append_status("actions=ready")) return 1;
	if (!rp_append_status("complete_export=ready")) return 1;
	printf("rp_complete: hooks=4 events=1 actions=4 status=ready\n");
	return 0;
}
