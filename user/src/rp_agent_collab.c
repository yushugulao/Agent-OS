#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_mail", "msg=21");
	ok = ok && rp_file_contains("rp_stage_log", "first_attempt status=failed");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "Recovery reran only the align stage");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_review", "status=accepted");
	ok = ok && rp_file_contains("rp_audit", "status=passed");
	if (!ok) return 1;

	if (!rp_write_file("rp_agents",
			   "run_id=RUN-042\n"
			   "agent=orchestrator;role=control;state=active;msg=4\n"
			   "agent=retriever;role=evidence;state=done;msg=3\n"
			   "agent=analyst;role=data;state=done;msg=4\n"
			   "agent=reviewer;role=quality;state=accepted;msg=3\n"
			   "agent=writer;role=report;state=packaged;msg=2\n"
			   "agent=recovery;role=repair;state=recovered;msg=3\n"
			   "agent=auditor;role=audit;state=passed;msg=2\n"
			   "agents=7\n"
			   "messages=21\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_decisions",
			   "run_id=RUN-042\n"
			   "decision=1;actor=orchestrator;choice=start_workflow;basis=rp_plan\n"
			   "decision=2;actor=retriever;choice=use_literature_set;basis=rp_lit\n"
			   "decision=3;actor=analyst;choice=flag_align_failure;basis=rp_fail\n"
			   "decision=4;actor=reviewer;choice=request_minimal_rerun;basis=rp_review\n"
			   "decision=5;actor=recovery;choice=rerun_align_only;basis=rp_retryq\n"
			   "decision=6;actor=writer;choice=package_report;basis=rp_report_text\n"
			   "decision=7;actor=auditor;choice=release_ready;basis=rp_audit\n"
			   "decision=8;actor=orchestrator;choice=compare_plain_kernel;basis=rp_agentcmp\n"
			   "decisions=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_handoff",
			   "run_id=RUN-042\n"
			   "handoff=planner->retriever;artifact=rp_plan;status=done\n"
			   "handoff=retriever->analyst;artifact=rp_lit;status=done\n"
			   "handoff=analyst->reviewer;artifact=rp_data;status=done\n"
			   "handoff=reviewer->recovery;artifact=rp_fail;status=done\n"
			   "handoff=recovery->writer;artifact=rp_artifact;status=done\n"
			   "handoff=writer->auditor;artifact=rp_report_text;status=done\n"
			   "handoffs=6\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_deliberation",
			   "run_id=RUN-042\n"
			   "item=1;topic=failed_align;vote=recoverable;source=rp_stage_log\n"
			   "item=2;topic=data_reuse;vote=use_cached_profile;source=rp_runner\n"
			   "item=3;topic=llm_relay;vote=host_only;source=rp_relay\n"
			   "item=4;topic=evidence_quality;vote=accepted;source=rp_claimrec\n"
			   "item=5;topic=release;vote=release;source=rp_release\n"
			   "items=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_agent_run",
			   "run_id=RUN-042\n"
			   "agent_roles=7\n"
			   "agent_messages=21\n"
			   "agent_decisions=8\n"
			   "agent_handoffs=6\n"
			   "agent_deliberation_items=5\n"
			   "recovery_decision=rerun_align_only\n"
			   "audit_decision=release_ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=agent_collab;msg=agents;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_agents")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_decisions")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_handoff")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_deliberation")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_run")) return 1;
	if (!rp_append_status("agents=ready")) return 1;
	if (!rp_append_status("decisions=ready")) return 1;
	if (!rp_append_status("handoff=ready")) return 1;
	if (!rp_append_status("deliberation=ready")) return 1;
	if (!rp_append_status("collab=ready")) return 1;
	printf("rp_agent_collab: agents=7 messages=21 decisions=8 handoffs=6 status=ready\n");
	return 0;
}
