#include <agent.h>
#include <fcntl.h>
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

static struct agent_sched_config security_sched_config;

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
		       AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE |
		       AGENT_CAP_AUDIT_WRITE;
	if (role == AGENT_ROLE_ORCHESTRATOR)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
		       AGENT_CAP_WATCH | AGENT_CAP_ACTION_WRITE |
		       AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_AUDIT_WRITE |
		       AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE |
		       AGENT_CAP_LLM_RELAY;
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
	meta.dependency_mask = agent_dependency_label_bit("analyze") |
			       agent_dependency_label_bit("report") |
			       agent_dependency_label_bit("archive");
	check(agent_file_meta_set(&meta) == 0, "set failed meta");
}

static void set_report_failed(const char *run_id, int fid, const char *physical)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, run_id);
	strcpy(meta.stage, "report");
	strcpy(meta.kind, "report");
	strcpy(meta.status, "failed");
	strcpy(meta.summary, "report waits for scoped recovery");
	check(agent_file_meta_set(&meta) == 0, "set report meta");
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

static void check_report_status(const char *run_id, const char *status)
{
	struct agent_file_query query;
	struct agent_file_query_result result;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "lab-gene-x");
	strcpy(query.run_id, run_id);
	strcpy(query.stage, "report");
	strcpy(query.kind, "report");
	strcpy(query.status, status);
	check(agent_file_query(&query, &result) >= 1, "query report status");
	check(result.total_hits >= 1, "report status hit");
}

static void check_preinit_index_query(void)
{
	struct agent_file_query query;
	struct agent_file_query_result result;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.status, "preinit-missing");
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

static void check_legacy_param_validation(void)
{
	struct agent_request req;
	struct agent_response resp;
	struct agent_op op;
	struct agent_result res;

	memset(&req, 0, sizeof(req));
	memset(&resp, 0, sizeof(resp));
	req.version = AGENT_CALL_VERSION;
	req.tool_id = AGENT_TOOL_ECHO;
	req.request_id = 8401;
	strcpy(req.tool_name, "echo");
	strcpy(req.payload_key, "payload");
	req.payload_type = AGENT_PARAM_STRING;
	strcpy(req.arg0_key, "arg0");
	req.arg0_type = AGENT_PARAM_UINT64;
	strcpy(req.arg1_key, "arg1");
	req.arg1_type = AGENT_PARAM_UINT64;
	req.arg0 = 11;
	req.arg1 = 12;
	strcpy(req.payload, "legacy-ok");
	check(agent_call(&req, &resp) == 0, "legacy echo");
	check(resp.status == AGENT_STATUS_OK, "legacy echo status");
	check(strcmp(resp.result, "legacy-ok") == 0, "legacy echo result");

	strcpy(req.payload_key, "bad_payload");
	check(agent_call(&req, &resp) == 0, "legacy bad payload key");
	check(resp.status == AGENT_STATUS_BAD_PARAM,
	      "legacy bad payload status");
	check(strcmp(resp.result, "bad_payload_key") == 0,
	      "legacy bad payload text");

	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_AGENT_WAIT;
	op.request_id = 8402;
	check(agent_run(&op, &res, 1, 0) == 1, "syscall only run");
	check(res.status == AGENT_STATUS_BAD_PARAM, "syscall only status");
	check(strcmp(res.result, "use_agent_wait_syscall") == 0,
	      "syscall only text");
	printf("agentsecurity_ucore: legacy_param_validation=1 syscall_only=1\n");
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

static void check_wake_event_authorization(void)
{
	struct agent_event event;
	struct agent_event received;

	check(agent_watch(AGENT_EVENT_LLM_DONE, "forged-llm") == 0,
	      "watch forged llm event");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_LLM_DONE;
	event.corr_id = 8201;
	strcpy(event.payload, "forged-llm");
	check(agent_wake(getpid(), &event) == AGENT_STATUS_DENIED,
	      "direct llm event denied");
	memset(&received, 0, sizeof(received));
	check(agent_wait(&received, 0) == AGENT_STATUS_TIMEOUT,
	      "denied llm event not queued");
	check(agent_unwatch(AGENT_EVENT_LLM_DONE, "forged-llm") == 1,
	      "unwatch forged llm event");

	event.type = AGENT_EVENT_NONE;
	check(agent_wake(getpid(), &event) == AGENT_STATUS_BAD_PARAM,
	      "empty event type rejected");
	event.type = AGENT_EVENT_MAX + 1;
	check(agent_wake(getpid(), &event) == AGENT_STATUS_BAD_PARAM,
	      "invalid event type rejected");

	check(agent_watch(AGENT_EVENT_MESSAGE, "authorized-message") == 0,
	      "watch authorized message");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 8202;
	strcpy(event.payload, "authorized-message");
	check(agent_wake(getpid(), &event) == AGENT_STATUS_OK,
	      "authorized message delivered");
	memset(&received, 0, sizeof(received));
	check(agent_wait(&received, 0) == AGENT_STATUS_OK,
	      "authorized message received");
	check(received.type == AGENT_EVENT_MESSAGE,
	      "authorized message type");
	check(received.corr_id == 8202, "authorized message correlation");
	check(strcmp(received.payload, "authorized-message") == 0,
	      "authorized message payload");
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "authorized-message") == 1,
	      "unwatch authorized message");
	printf("agentsecurity_ucore: wake_event_authorization=1\n");
}

static void run_sentinel(void)
{
	struct agent_op op;
	struct agent_result res;
	struct agent_file_meta meta;

	check_role(AGENT_ROLE_SENTINEL, "sentinel");
	check_wake_event_authorization();
	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 8101,
		AGENT_ROLE_RECOVERY, "action_commit");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel spoof cap");
	check(res.value1 == AGENT_ROLE_SENTINEL, "real sentinel role");
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 8105, AGENT_ROLE_RECOVERY,
		"label=align;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel generic action");
	make_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 8106, AGENT_ROLE_RECOVERY,
		"label=report;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel generic artifact");
	make_op(&op, AGENT_TOOL_LLM_RESPONSE, 8107, getppid(),
		"template response");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel llm relay");
	make_op(&op, AGENT_TOOL_DEPENDENCY_UPDATE, 8108, 0,
		"source=align;target=review");
	run_one(&op, &res, AGENT_STATUS_DENIED,
		"sentinel dependency update denied");
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 8102, AGENT_ROLE_RECOVERY,
		"align");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel spoof rerun");
	make_op(&op, AGENT_TOOL_WRITE_REPORT, 8103, AGENT_ROLE_RECOVERY,
		"fake report");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel spoof report");
	make_op(&op, AGENT_TOOL_READ_FILE_DIGEST, 8104, 0, "r42align");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel digest denied");
	check(agent_audit_snapshot(0, 0) == AGENT_STATUS_DENIED,
	      "sentinel audit denied");
	check(agent_audit_query(0, 0, 0) == AGENT_STATUS_DENIED,
	      "sentinel audit query denied");
	check(agent_ledger_snapshot(0) == AGENT_STATUS_DENIED,
	      "sentinel ledger denied");
	memset(&security_sched_config, 0, sizeof(security_sched_config));
	security_sched_config.target_pid = getpid();
	security_sched_config.update_mask = AGENT_SCHED_CONFIG_WEIGHT;
	security_sched_config.weight = 150;
	check(agent_sched_config(&security_sched_config) ==
		      AGENT_STATUS_DENIED,
	      "sentinel sched config denied");
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
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 9101, AGENT_ROLE_SENTINEL,
		"label=align;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_OK, "recovery action");
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 9101, AGENT_ROLE_SENTINEL,
		"label=align;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_DUPLICATE, "recovery duplicate");
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 9101, AGENT_ROLE_SENTINEL,
		"label=align;run_id=RUN-998;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_OK, "same request other run");
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 9103, AGENT_ROLE_SENTINEL,
		"label=align;run_id=RUN-NOPE;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_NOT_FOUND, "missing action target");
	make_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 9102, AGENT_ROLE_SENTINEL,
		"label=report;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_OK, "recovery artifact");
	make_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 9104, AGENT_ROLE_SENTINEL,
		"label=report;run_id=RUN-NOPE;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_NOT_FOUND, "missing artifact target");
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 9105, AGENT_ROLE_SENTINEL,
		"stage=align;run_id=RUN-998;project=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_OK, "legacy rerun alias");
	make_op(&op, AGENT_TOOL_WRITE_REPORT, 9106, AGENT_ROLE_SENTINEL,
		"stage=report;run_id=RUN-999;project=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_OK, "legacy report alias");
	printf("agentsecurity_ucore: recovery action_ok=1 duplicate=1\n");
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
	check_legacy_param_validation();
	set_align_failed("RUN-042", 3, "r42aerr");
	set_align_failed("RUN-999", 30, "r999aerr");
	set_align_failed("RUN-998", 31, "r998aerr");
	set_report_failed("RUN-042", 40, "r42rerr");
	set_report_failed("RUN-999", 41, "r999rerr");
	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create sentinel");
	if (pid == 0)
		run_sentinel();
	check(waitpid(pid, &status) == pid, "wait sentinel");
	check(status == 0, "sentinel status");
	check_align_status("RUN-042", "failed");
	check_align_status("RUN-999", "failed");
	check_align_status("RUN-998", "failed");
	check_report_status("RUN-042", "failed");
	check_report_status("RUN-999", "failed");
	pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(pid >= 0, "create recovery");
	if (pid == 0)
		run_recovery();
	check(waitpid(pid, &status) == pid, "wait recovery");
	check(status == 0, "recovery status");
	check_align_status("RUN-999", "ok");
	check_align_status("RUN-998", "ok");
	check_align_status("RUN-042", "failed");
	check_report_status("RUN-999", "ok");
	check_report_status("RUN-042", "failed");
	printf("agentsecurity_ucore: scoped_action=1\n");
	printf("agentsecurity_ucore: scoped_artifact=1\n");
	printf("agentsecurity_ucore: passed\n");
	exit(0);
}

static void check_plain_process_denied(void)
{
	struct agent_event event;
	struct agent_file_meta meta;
	int fd;

	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	strcpy(event.payload, "plain wake");
	memset(&meta, 0, sizeof(meta));
	strcpy(meta.stage, "align");
	strcpy(meta.status, "failed");
	check(agent_wake(1, &event) == -1, "plain wake denied");
	check(agent_wait_cancel(1, "plain cancel") == -1,
	      "plain wait cancel denied");
	check(agent_audit_snapshot(0, 0) == -1, "plain audit denied");
	check(agent_audit_query(0, 0, 0) == -1, "plain audit query denied");
	check(agent_span_trace_snapshot(0, 0) == -1,
	      "plain span trace denied");
	check(agent_timeline_snapshot(0, 0) == -1, "plain timeline denied");
	check(agent_timeline_query(0, 0, 0) == -1,
	      "plain timeline query denied");
	check(agent_timeline_wait(0, 0) == -1, "plain timeline wait denied");
	check(agent_timeline_read(0, 0, 0, 0) == -1,
	      "plain timeline read denied");
	check(agent_provenance_snapshot(0, 0) == -1,
	      "plain provenance denied");
	check(agent_ledger_snapshot(0) == -1, "plain ledger denied");
	check(agent_sched_config(0) == -1, "plain sched config denied");
	check(agent_file_meta_init() == -1, "plain meta init denied");
	check(agent_file_meta_set(&meta) == -1, "plain meta set denied");
	fd = open(".agentmeta", O_RDONLY);
	check(fd == -1, "plain open agentmeta denied");
	fd = open(".agentmeta", O_CREATE | O_RDWR);
	check(fd == -1, "plain create agentmeta denied");
	check(unlink(".agentmeta") == -1, "plain unlink agentmeta denied");
	printf("agentsecurity_ucore: plain_process_denied=1\n");
	printf("agentsecurity_ucore: .agentmeta_protected=1\n");
}

static void check_plain_mail(void)
{
	char msg[] = "mail-ok";
	char out[16];
	int n;

	memset(out, 0, sizeof(out));
	check(mailread(out, sizeof(out)) == 0, "empty mail");
	n = strlen(msg) + 1;
	check(mailwrite(getpid(), msg, n) == n, "mail write");
	check(mailread(out, sizeof(out)) == n, "mail read");
	check(strcmp(out, msg) == 0, "mail text");
	printf("agentsecurity_ucore: mail_basic=1\n");
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentsecurity_ucore: Agent permission test\n");
	check_plain_mail();
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
