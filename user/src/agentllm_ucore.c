#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_context_header llm_header;
static struct agent_context_record llm_context_records[8];
static struct agent_timeline_record llm_timeline_records[16];
static struct agent_info llm_info;
static struct agent_op llm_op;
static struct agent_result llm_result;
static struct agent_event llm_event;
static struct agent_ledger_summary llm_ledger;

static void say(const char *text)
{
	write(1, text, strlen(text));
}

static void check(int ok, const char *msg)
{
	if (!ok) {
		say("agentllm_ucore: check failed: ");
		write(1, msg, strlen(msg));
		say("\n");
		exit(1);
	}
}

static int text_contains(const char *text, const char *needle)
{
	int n = strlen(needle);

	if (n == 0)
		return 1;
	for (int i = 0; text[i]; i++) {
		if (strncmp(text + i, needle, n) == 0)
			return 1;
	}
	return 0;
}

static void make_op(struct agent_op *op, int tool, uint64 request_id,
		    uint64 arg0, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool;
	op->request_id = request_id;
	op->arg0 = arg0;
	if (payload)
		strcpy(op->payload, payload);
}

static void check_requester_context(void)
{
	int n;
	int has_request = 0;
	int has_wait = 0;

	memset(&llm_header, 0, sizeof(llm_header));
	memset(llm_context_records, 0, sizeof(llm_context_records));
	n = context_snapshot(&llm_header, llm_context_records, 8);
	check(n >= 2, "requester context count");
	check(llm_header.latest_sequence >= 2, "requester latest sequence");
	for (int i = 0; i < n; i++) {
		if (llm_context_records[i].tool_id == AGENT_TOOL_LLM_REQUEST)
			has_request = 1;
		if (llm_context_records[i].tool_id == AGENT_TOOL_AGENT_WAIT &&
		    llm_context_records[i].status == AGENT_STATUS_OK)
			has_wait = 1;
	}
	check(has_request, "context has llm request");
	check(has_wait, "context has llm wait");
}

static void check_relay_timeline(void)
{
	int n;
	int has_request = 0;
	int has_response = 0;

	memset(llm_timeline_records, 0, sizeof(llm_timeline_records));
	n = agent_timeline_snapshot(llm_timeline_records, 16);
	check(n >= 2, "relay timeline count");
	for (int i = 0; i < n; i++) {
		if (llm_timeline_records[i].tool_id == AGENT_TOOL_LLM_REQUEST)
			has_request = 1;
		if (llm_timeline_records[i].tool_id == AGENT_TOOL_LLM_RESPONSE)
			has_response = 1;
	}
	check(has_response, "relay timeline response");
	(void)n;
	(void)has_request;
	say("agentllm_ucore: relay_timeline=1\n");
}

static void run_requester(int gate_fd)
{
	int relay_pid = getppid();
	char gate;

	memset(&llm_info, 0, sizeof(llm_info));
	check(agent_info(&llm_info) == 0, "requester info");
	check(llm_info.is_agent == 1, "requester is agent");
	check(llm_info.agent_role == AGENT_ROLE_INVESTIGATOR,
	      "requester role");
	check(agent_watch(AGENT_EVENT_LLM_DONE, "template_response") == 0,
	      "watch llm done");
	check(read(gate_fd, &gate, 1) == 1, "wait route grant");
	close(gate_fd);

	make_op(&llm_op, AGENT_TOOL_LLM_REQUEST, 9601, relay_pid,
		"llm_request;prompt=template");
	check(agent_run(&llm_op, &llm_result, 1, 0) == 1,
	      "llm request run");
	check(llm_result.status == AGENT_STATUS_OK, "llm request status");
	check(llm_result.value2 == 1, "llm request delivered");

	memset(&llm_event, 0, sizeof(llm_event));
	check(agent_wait(&llm_event, 100) == AGENT_STATUS_OK,
	      "wait llm response");
	check(llm_event.type == AGENT_EVENT_LLM_DONE, "llm done event");
	check(llm_event.source_pid == relay_pid, "llm relay source");
	check(llm_event.corr_id == 9601, "llm corr id");
	check(text_contains(llm_event.payload, "template_response"),
	      "llm response payload");
	check_requester_context();
	say("agentllm_ucore: requester_done=1\n");
	exit(0);
}

static void run_orchestrator(void)
{
	int route_gate[2];
	int requester_pid;
	int status = 0;
	char gate = 'g';

	memset(&llm_info, 0, sizeof(llm_info));
	check(agent_info(&llm_info) == 0, "orchestrator info");
	check(llm_info.is_agent == 1, "orchestrator is agent");
	check(llm_info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "orchestrator role");
	check((llm_info.capability_mask & AGENT_CAP_LLM_RELAY) != 0,
	      "relay capability");
	check(agent_watch(AGENT_EVENT_MESSAGE, "llm_request") == 0,
	      "relay watch request");
	check(pipe(route_gate) == 0, "route gate pipe");

	requester_pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
	check(requester_pid >= 0, "create requester");
	if (requester_pid == 0) {
		close(route_gate[1]);
		run_requester(route_gate[0]);
	}
	close(route_gate[0]);
	check(agent_route_config(requester_pid, getpid(),
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant request route");
	check(agent_route_config(getpid(), requester_pid,
				 AGENT_IPC_EVENT_LLM_DONE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant response route");
	check(write(route_gate[1], &gate, 1) == 1, "release requester");
	close(route_gate[1]);

	memset(&llm_event, 0, sizeof(llm_event));
	check(agent_wait(&llm_event, 100) == AGENT_STATUS_OK,
	      "relay wait request");
	check(llm_event.type == AGENT_EVENT_MESSAGE, "relay message event");
	check(llm_event.source_pid == requester_pid, "relay source pid");
	check(llm_event.corr_id == 9601, "relay corr id");
	check(text_contains(llm_event.payload, "prompt=template"),
	      "relay prompt payload");

	make_op(&llm_op, AGENT_TOOL_LLM_RESPONSE, llm_event.corr_id,
		requester_pid, "template_response;summary=ok");
	check(agent_run(&llm_op, &llm_result, 1, 0) == 1,
	      "llm response run");
	check(llm_result.status == AGENT_STATUS_OK, "llm response status");
	check(llm_result.value2 == 1, "llm response delivered");
	check_relay_timeline();
	memset(&llm_ledger, 0, sizeof(llm_ledger));
	check(agent_ledger_snapshot(&llm_ledger) == 0, "ledger snapshot");
	check(llm_ledger.total_records > 0, "ledger records");

	check(waitpid(requester_pid, &status) == requester_pid,
	      "wait requester");
	check(status == 0, "requester status");
	say("agentllm_ucore: template_relay=1\n");
	say("agentllm_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	say("agentllm_ucore: Agent LLM relay test\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_orchestrator();
	check(waitpid(pid, &status) == pid, "wait orchestrator");
	check(status == 0, "orchestrator status");
	say("agentllm_ucore: parent passed\n");
	return 0;
}
