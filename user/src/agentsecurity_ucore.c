#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentsecurity_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void make_op(struct agent_op *op, int tool, uint64 id, uint64 arg0,
		    const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool;
	op->request_id = id;
	op->arg0 = arg0;
	if (payload)
		strcpy(op->payload, payload);
}

static void run_one(struct agent_op *op, struct agent_result *res, int status,
		    const char *msg)
{
	check(agent_run(op, res, 1, 0) == 1, msg);
	check(res->status == status, msg);
}

static uint64 expected_caps(int role)
{
	if (role == AGENT_ROLE_SENTINEL)
		return AGENT_CAP_META_READ | AGENT_CAP_PROCESS_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_AUDIT_WRITE;
	if (role == AGENT_ROLE_RECOVERY)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_RECOVER_STAGE | AGENT_CAP_REPORT_WRITE |
		       AGENT_CAP_AUDIT_WRITE;
	if (role == AGENT_ROLE_ORCHESTRATOR)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
		       AGENT_CAP_WATCH | AGENT_CAP_RECOVER_STAGE |
		       AGENT_CAP_REPORT_WRITE | AGENT_CAP_AUDIT_WRITE |
		       AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE;
	return 0;
}

static void check_role(int role, const char *name)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "agent_info");
	check(info.is_agent == 1, "is agent");
	check(info.agent_role == role, name);
	check(info.capability_mask == expected_caps(role), "capability mask");
	printf("agentsecurity_ucore: role=%s capability_checked=1\n", name);
}

static void set_align_failed(const char *run_id, int fid, const char *physical)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, run_id);
	strcpy(meta.stage, "align");
	strcpy(meta.kind, "log");
	strcpy(meta.status, "failed");
	strcpy(meta.summary, "security test failure");
	meta.dependency_mask = AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
			       AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
	check(agent_file_meta_set(&meta) == 0, "set failed meta");
}

static void check_align_status(const char *run_id, const char *status)
{
	struct agent_file_query query;
	struct agent_file_query_result result;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "lab-gene-x");
	strcpy(query.run_id, run_id);
	strcpy(query.stage, "align");
	strcpy(query.status, status);
	check(agent_file_query(&query, &result) >= 1, "query align status");
	check(result.total_hits >= 1, "align status hit");
}

static void check_preinit_index_query(void)
{
	struct agent_file_query query;
	struct agent_file_query_result result;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.status, "ok");
	check(agent_file_query(&query, &result) == 0, "preinit query return");
	check(result.total_hits == 0, "preinit query hits");
	printf("agentsecurity_ucore: preinit_index_query=1\n");
}

static void check_legacy_tool_mismatch(void)
{
	struct agent_request req;
	struct agent_response resp;

	memset(&req, 0, sizeof(req));
	memset(&resp, 0, sizeof(resp));
	req.version = AGENT_CALL_VERSION;
	req.tool_id = AGENT_TOOL_ECHO;
	req.request_id = 8301;
	strcpy(req.tool_name, "pid_info");
	strcpy(req.payload, "mismatch-payload");
	check(agent_call(&req, &resp) == 0, "legacy mismatch call");
	check(resp.status == AGENT_STATUS_BAD_REQUEST, "legacy mismatch status");
	check(strcmp(resp.result, "tool_mismatch") == 0, "legacy mismatch text");
	printf("agentsecurity_ucore: legacy_tool_mismatch=1\n");
}

static void run_min_orchestrator(void)
{
	check_role(AGENT_ROLE_ORCHESTRATOR, "orchestrator_child");
	exit(0);
}

static void check_plain_child_orchestrator_allowed(void)
{
	int wrapper_pid;
	int agent_pid;
	int status = 0;

	wrapper_pid = fork();
	check(wrapper_pid >= 0, "fork wrapper");
	if (wrapper_pid == 0) {
		agent_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
		check(agent_pid >= 0, "plain child create orchestrator");
		if (agent_pid == 0)
			run_min_orchestrator();
		check(waitpid(agent_pid, &status) == agent_pid,
		      "wait child orchestrator");
		check(status == 0, "child orchestrator status");
		exit(0);
	}
	check(waitpid(wrapper_pid, &status) == wrapper_pid, "wait wrapper");
	check(status == 0, "wrapper status");
	printf("agentsecurity_ucore: plain_child_orchestrator=1\n");
}

static void run_sentinel(void)
{
	struct agent_op op;
	struct agent_result res;
	struct agent_file_meta meta;

	check_role(AGENT_ROLE_SENTINEL, "sentinel");
	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 8101,
		AGENT_ROLE_RECOVERY, "rerun_stage");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel spoof cap");
	check(res.value1 == AGENT_ROLE_SENTINEL, "real sentinel role");
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 8102, AGENT_ROLE_RECOVERY,
		"align");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel spoof rerun");
	make_op(&op, AGENT_TOOL_WRITE_REPORT, 8103, AGENT_ROLE_RECOVERY,
		"fake report");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel spoof report");
	memset(&meta, 0, sizeof(meta));
	strcpy(meta.stage, "align");
	strcpy(meta.status, "ok");
	check(agent_file_meta_set(&meta) == AGENT_STATUS_DENIED,
	      "sentinel meta write denied");
	check_align_status("RUN-042", "failed");
	printf("agentsecurity_ucore: sentinel spoof_denied=1\n");
	exit(0);
}

static void run_recovery(void)
{
	struct agent_op op;
	struct agent_result res;

	check_role(AGENT_ROLE_RECOVERY, "recovery");
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 9101, AGENT_ROLE_SENTINEL,
		"stage=align;run_id=RUN-999;project=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_OK, "recovery rerun");
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 9101, AGENT_ROLE_SENTINEL,
		"stage=align;run_id=RUN-999;project=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_DUPLICATE, "recovery duplicate");
	make_op(&op, AGENT_TOOL_WRITE_REPORT, 9102, AGENT_ROLE_SENTINEL,
		"security recovery report");
	run_one(&op, &res, AGENT_STATUS_OK, "recovery write report");
	printf("agentsecurity_ucore: recovery rerun_ok=1 duplicate=1\n");
	exit(0);
}

static void run_orchestrator(void)
{
	int pid;
	int status = 0;

	check_role(AGENT_ROLE_ORCHESTRATOR, "orchestrator");
	check_preinit_index_query();
	check(agent_file_meta_init() == 0, "meta init");
	check_legacy_tool_mismatch();
	set_align_failed("RUN-042", 3, "lab_RUN042_align_err");
	set_align_failed("RUN-999", 30, "lab_RUN999_align_err");
	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create sentinel");
	if (pid == 0)
		run_sentinel();
	check(waitpid(pid, &status) == pid, "wait sentinel");
	check(status == 0, "sentinel status");
	check_align_status("RUN-042", "failed");
	check_align_status("RUN-999", "failed");
	pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(pid >= 0, "create recovery");
	if (pid == 0)
		run_recovery();
	check(waitpid(pid, &status) == pid, "wait recovery");
	check(status == 0, "recovery status");
	check_align_status("RUN-999", "ok");
	check_align_status("RUN-042", "failed");
	printf("agentsecurity_ucore: scoped_rerun=1\n");
	printf("agentsecurity_ucore: passed\n");
	exit(0);
}

static void check_plain_process_denied(void)
{
	struct agent_event event;
	struct agent_file_meta meta;

	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	strcpy(event.payload, "plain wake");
	memset(&meta, 0, sizeof(meta));
	strcpy(meta.stage, "align");
	strcpy(meta.status, "failed");
	check(agent_wake(1, &event) == -1, "plain wake denied");
	check(agent_file_meta_init() == -1, "plain meta init denied");
	check(agent_file_meta_set(&meta) == -1, "plain meta set denied");
	printf("agentsecurity_ucore: plain_process_denied=1\n");
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentsecurity_ucore: Agent permission boundary test\n");
	check_plain_process_denied();
	check_plain_child_orchestrator_allowed();
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_orchestrator();
	check(waitpid(pid, &status) == pid, "wait orchestrator");
	check(status == 0, "orchestrator status");
	printf("agentsecurity_ucore: parent passed\n");
	return 0;
}
