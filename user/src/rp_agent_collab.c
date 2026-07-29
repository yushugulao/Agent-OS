#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <research_platform_state.h>
#include <unistd.h>

static struct agent_event collab_event;
static struct agent_op collab_op;
static struct agent_result collab_result;

static int collab_waiter(int parent_pid, int gate_fd)
{
	struct agent_event event;
	char gate;

	if (agent_watch(AGENT_EVENT_MESSAGE, "handoff=recovery-auditor") < 0)
		return 1;
	if (read(gate_fd, &gate, 1) != 1)
		return 1;
	close(gate_fd);
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.source_pid = getpid();
	event.target_pid = parent_pid;
	event.corr_id = 2100;
	strcpy(event.payload, "collab=ready;agent=sentinel");
	if (agent_wake(parent_pid, &event) < 0)
		return 1;
	memset(&collab_event, 0, sizeof(collab_event));
	if (agent_wait(&collab_event, 100) != AGENT_STATUS_OK ||
	    collab_event.type != AGENT_EVENT_MESSAGE ||
	    !rp_text_contains(collab_event.payload,
			      "handoff=recovery-auditor")) {
		return 1;
	}
	memset(&collab_op, 0, sizeof(collab_op));
	collab_op.version = AGENT_CALL_VERSION;
	collab_op.tool_id = AGENT_TOOL_ACTION_COMMIT;
	collab_op.request_id = 2100;
	strcpy(collab_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	if (agent_run(&collab_op, &collab_result, 1, 0) != 1 ||
	    collab_result.status != AGENT_STATUS_DENIED) {
		return 1;
	}
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.source_pid = getpid();
	event.target_pid = parent_pid;
	event.corr_id = 2102;
	strcpy(event.payload,
	       "collab=ack;handoff=recovery-auditor;status=ready");
	if (agent_wake(parent_pid, &event) < 0)
		return 1;
	return 0;
}

static int run_kernel_collaboration(void)
{
	struct agent_info info;
	int route_gate[2];
	int pid;
	int code = -1;
	char gate = 'g';

	if (agent_info(&info) < 0 || !info.is_agent)
		return 0;
	if (info.agent_role != AGENT_ROLE_ORCHESTRATOR ||
	    (info.capability_mask & AGENT_CAP_ORCHESTRATE) == 0) {
		printf("rp_agent_collab: orchestrate_capability_missing\n");
		return -1;
	}
	if (agent_watch(AGENT_EVENT_MESSAGE, "collab=") < 0)
		return -1;
	if (pipe(route_gate) < 0)
		return -1;
	if (agent_scope_delegate_fd(route_gate[0]) != AGENT_STATUS_OK) {
		close(route_gate[0]);
		close(route_gate[1]);
		return -1;
	}

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	if (pid < 0)
		return -1;
	if (pid == 0) {
		// Cross-scope principals receive only explicitly delegated endpoints.
		// The write end is intentionally absent from this child.
		exit(collab_waiter(getppid(), route_gate[0]));
	}
	close(route_gate[0]);
	if (agent_route_config(pid, getpid(), AGENT_IPC_EVENT_MESSAGE,
			       AGENT_IPC_ROUTE_GRANT) != AGENT_STATUS_OK ||
	    agent_route_config(getpid(), pid, AGENT_IPC_EVENT_MESSAGE,
			       AGENT_IPC_ROUTE_GRANT) != AGENT_STATUS_OK ||
	    write(route_gate[1], &gate, 1) != 1) {
		close(route_gate[1]);
		return -1;
	}
	close(route_gate[1]);

	memset(&collab_event, 0, sizeof(collab_event));
	if (agent_wait(&collab_event, 100) != AGENT_STATUS_OK ||
	    collab_event.source_pid != pid ||
	    !rp_text_contains(collab_event.payload, "collab=ready")) {
		printf("rp_agent_collab: waiter_not_ready\n");
		return -1;
	}
	if (!rp_write_file("rp_ac_ready",
			   "agent=sentinel\n"
			   "watch=handoff=recovery-auditor\n"
			   "delivery=kernel_event_queue\n"
			   "status=ready\n"))
		return -1;

	memset(&collab_event, 0, sizeof(collab_event));
	collab_event.type = AGENT_EVENT_MESSAGE;
	collab_event.source_pid = getpid();
	collab_event.target_pid = pid;
	collab_event.corr_id = 2101;
	strcpy(collab_event.payload,
	       "handoff=recovery-auditor;run_id=RUN-042;source=orchestrator");
	if (agent_wake(pid, &collab_event) < 0)
		return -1;
	memset(&collab_event, 0, sizeof(collab_event));
	if (agent_wait(&collab_event, 100) != AGENT_STATUS_OK ||
	    collab_event.source_pid != pid ||
	    !rp_text_contains(collab_event.payload, "collab=ack"))
		return -1;
	if (waitpid(pid, &code) != pid || code != 0)
		return -1;
	if (!rp_write_file("rp_agentos_collab_ack",
			   "agent=sentinel\n"
			   "event=handoff\n"
			   "route=recovery-auditor\n"
			   "delivery=kernel_event_queue\n"
			   "permission_control=sentinel_action_denied\n"
			   "status=ready\n"))
		return -1;

	memset(&collab_op, 0, sizeof(collab_op));
	collab_op.version = AGENT_CALL_VERSION;
	collab_op.tool_id = AGENT_TOOL_CAPABILITY_CHECK;
	collab_op.request_id = 2102;
	strcpy(collab_op.payload, "action_commit");
	if (agent_run(&collab_op, &collab_result, 1, 0) != 1 ||
	    collab_result.status != AGENT_STATUS_OK ||
	    collab_result.value0 != 1) {
		printf("rp_agent_collab: capability_check_failed status=%d\n",
		       collab_result.status);
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=collaboration;permission_control=sentinel_action_denied;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_collab = run_kernel_collaboration();

	if (kernel_collab < 0)
		return 1;
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
			   "agentos_interagent_event=kernel_queue\n"
			   "agentos_role_launch=kernel_bound\n"
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
			   "agentos_handoff_event=kernel_delivered\n"
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
			   "kernel_agent_launch=role_specific\n"
			   "kernel_event_delivery=inter_agent\n"
			   "kernel_capability_check=orchestrator_allow\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=agent_collab;msg=agents;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_agents")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_decisions")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_handoff")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_deliberation")) return 1;
	if (!rp_append_file("rp_tool", "tool=agent_collab.write_run")) return 1;
	if (kernel_collab &&
	    !rp_append_file("rp_tool", "tool=agentos.inter_agent_event")) {
		return 1;
	}
	if (kernel_collab &&
	    !rp_append_file("rp_tool", "tool=agentos.capability_check")) {
		return 1;
	}
	if (!rp_append_status("agents=ready")) return 1;
	if (!rp_append_status("decisions=ready")) return 1;
	if (!rp_append_status("handoff=ready")) return 1;
	if (!rp_append_status("deliberation=ready")) return 1;
	if (!rp_append_status("collab=ready")) return 1;
	printf("rp_agent_collab: agents=7 messages=21 decisions=8 handoffs=6 status=ready\n");
	return 0;
}
