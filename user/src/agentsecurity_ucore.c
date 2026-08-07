#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TEST_NOINLINE __attribute__((noinline))
#define LOW_AUDIT_CHURN_COUNT 200

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentsecurity_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static struct agent_sched_config security_sched_config;
static struct agent_audit_record
	security_audit_records[AGENT_AUDIT_MAX_RECORDS];
static struct agent_provenance_edge security_provenance_edges[128];
static struct agent_audit_filter security_audit_filter;
static struct agent_event security_audit_event;
static struct agent_info security_agent_info;
static struct agent_info security_role_info;
static struct agent_info security_sentinel_info;
static struct agent_event security_sentinel_event;
static struct agent_op security_sentinel_op;
static struct agent_result security_sentinel_result;
static struct agent_file_meta security_sentinel_meta;
static struct agent_context_record security_sentinel_context;
static struct agent_op security_anchor_op;
static struct agent_result security_anchor_result;
static struct agent_file_query security_file_query;
static struct agent_file_query_result security_file_result;
static uint64 security_orchestrator_span;

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
	if (role == AGENT_ROLE_INVESTIGATOR)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_AUDIT_WRITE;
	if (role == AGENT_ROLE_RECOVERY)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE |
		       AGENT_CAP_AUDIT_WRITE;
	if (role == AGENT_ROLE_ARTIFACT)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_AUDIT_WRITE;
	if (role == AGENT_ROLE_ORCHESTRATOR)
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
		       AGENT_CAP_WATCH | AGENT_CAP_ACTION_WRITE |
		       AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_AUDIT_WRITE |
		       AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE |
		       AGENT_CAP_LLM_RELAY | AGENT_CAP_WAIT_CANCEL |
		       AGENT_CAP_ROUTE_MANAGE;
	return 0;
}

static void check_role(int role, const char *name)
{
	check(agent_info(&security_role_info) == 0, "agent_info");
	check(security_role_info.is_agent == 1, "is agent");
	check(security_role_info.agent_role == role, name);
	check(security_role_info.capability_mask == expected_caps(role),
	      "capability mask");
	if (role == AGENT_ROLE_ORCHESTRATOR) {
		check((security_role_info.capability_mask &
		       AGENT_CAP_WAIT_CANCEL) != 0,
		      "orchestrator wait cancel capability");
		check((security_role_info.capability_mask &
		       AGENT_CAP_ROUTE_MANAGE) != 0,
		      "orchestrator route manage capability");
	} else {
		check((security_role_info.capability_mask &
		       AGENT_CAP_MESSAGE_SEND) != 0,
		      "low role message capability");
		check((security_role_info.capability_mask &
		       AGENT_CAP_WAIT_CANCEL) == 0,
		      "low role lacks wait cancel capability");
		check((security_role_info.capability_mask &
		       AGENT_CAP_ROUTE_MANAGE) == 0,
		      "low role lacks route manage capability");
	}
	check(security_role_info.filesystem_capability_mask ==
		      (expected_caps(role) &
		       (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)),
	      "filesystem capability mask");
	printf("agentsecurity_ucore: role=%s capability_checked=1\n", name);
}

static void check_plain_identity(void)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "plain identity info");
	check(info.is_agent == 0, "plain is not agent");
	check(info.agent_id == 0, "plain agent id cleared");
	check(info.agent_role == 0, "plain role cleared");
	check(info.agent_type == AGENT_TYPE_NONE, "plain type cleared");
	check(info.capability_mask == 0, "plain capability cleared");
	check(info.context_base == 0 && info.context_size == 0,
	      "plain context cleared");
}

static void check_creation_authority_denied(void)
{
	check(agent_create() == AGENT_STATUS_DENIED, "default create denied");
	for (int role = AGENT_ROLE_SENTINEL;
	     role <= AGENT_ROLE_ARTIFACT; role++)
		check(agent_create_role(role) == AGENT_STATUS_DENIED,
		      "role create denied");
	check(agent_create_role(0) == AGENT_STATUS_BAD_PARAM,
	      "invalid low role denied");
	check(agent_create_role(AGENT_ROLE_ARTIFACT + 1) ==
		      AGENT_STATUS_BAD_PARAM,
	      "invalid high role denied");
}

static void check_delegation_denied(const char *name)
{
	check_creation_authority_denied();
	check(agent_wait_cancel(getppid(), "low-role-cancel") ==
		      AGENT_STATUS_DENIED,
	      "low role wait cancel denied");
	printf("agentsecurity_ucore: role=%s delegation_denied=1\n", name);
	printf("agentsecurity_ucore: role=%s wait_cancel_denied=1\n", name);
}

static void check_bootstrap_identity(void)
{
	check_plain_identity();
	printf("agentsecurity_ucore: bootstrap_plain_identity=1\n");
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
	memset(&security_file_query, 0, sizeof(security_file_query));
	security_file_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	security_file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(security_file_query.project, "lab-gene-x");
	strcpy(security_file_query.run_id, run_id);
	strcpy(security_file_query.stage, "align");
	strcpy(security_file_query.status, status);
	check(agent_file_query(&security_file_query, &security_file_result) >= 1,
	      "query align status");
	check(security_file_result.total_hits >= 1, "align status hit");
}

static void check_report_status(const char *run_id, const char *status)
{
	memset(&security_file_query, 0, sizeof(security_file_query));
	security_file_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	security_file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(security_file_query.project, "lab-gene-x");
	strcpy(security_file_query.run_id, run_id);
	strcpy(security_file_query.stage, "report");
	strcpy(security_file_query.kind, "report");
	strcpy(security_file_query.status, status);
	check(agent_file_query(&security_file_query, &security_file_result) >= 1,
	      "query report status");
	check(security_file_result.total_hits >= 1, "report status hit");
}

static TEST_NOINLINE void check_preinit_index_query(void)
{
	memset(&security_file_query, 0, sizeof(security_file_query));
	security_file_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	security_file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(security_file_query.status, "preinit-missing");
	check(agent_file_query(&security_file_query, &security_file_result) == 0,
	      "preinit query return");
	check(security_file_result.total_hits == 0, "preinit query hits");
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

static void check_plain_child_creation_denied(void)
{
	int child_pid;
	int status = 0;

	child_pid = fork();
	check(child_pid >= 0, "fork untrusted child");
	if (child_pid == 0) {
		char *argv[] = { "agentsecurity_ucore", "--untrusted-probe", 0 };

		check_plain_identity();
		check_creation_authority_denied();
		if (exec("agentsecurity_ucore", argv) < 0)
			exit(1);
		exit(1);
	}
	check(waitpid(child_pid, &status) == child_pid, "wait untrusted child");
	check(status == 0, "untrusted child status");
	printf("agentsecurity_ucore: plain_child_role_creation_denied=1\n");
}

static TEST_NOINLINE void check_orchestrator_plain_fork_denied(void)
{
	int pid;
	int status = 0;

	pid = fork();
	check(pid >= 0, "fork orchestrator plain child");
	if (pid == 0) {
		check_plain_identity();
		check_creation_authority_denied();
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait orchestrator plain child");
	check(status == 0, "orchestrator plain child status");
	printf("agentsecurity_ucore: orchestrator_plain_fork_denied=1\n");
}

static void check_reaped_agent_slot_cleared(void)
{
	int pid;
	int status = 0;

	pid = fork();
	check(pid >= 0, "fork after agent reap");
	if (pid == 0) {
		check_plain_identity();
		check_creation_authority_denied();
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait post-reap child");
	check(status == 0, "post-reap child status");
	printf("agentsecurity_ucore: reaped_agent_slot_cleared=1\n");
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

static TEST_NOINLINE void run_sentinel(int audit_gate_fd)
{
	uint64 before_span;
	char phase = 0;
	int n;

	check_role(AGENT_ROLE_SENTINEL, "sentinel");
	check_delegation_denied("sentinel");
	check(agent_info(&security_sentinel_info) == 0,
	      "sentinel span before forge");
	before_span = security_sentinel_info.current_span_id;
	memset(&security_sentinel_context, 0,
	       sizeof(security_sentinel_context));
	security_sentinel_context.span_id = security_orchestrator_span;
	security_sentinel_context.cause_sequence = 1;
	security_sentinel_context.tool_id = AGENT_TOOL_ECHO;
	security_sentinel_context.status = AGENT_STATUS_OK;
	strcpy(security_sentinel_context.result, "forged-span");
	check(context_push(&security_sentinel_context) == AGENT_STATUS_BAD_PARAM,
	      "user provenance rejected");
	check(agent_info(&security_sentinel_info) == 0,
	      "sentinel span after forge");
	check(security_sentinel_info.current_span_id == before_span,
	      "forged span not installed");
	n = agent_span_trace_snapshot(security_audit_records,
				      AGENT_AUDIT_MAX_RECORDS);
	check(n >= 0, "sentinel trusted span trace");
	for (int i = 0; i < n; i++)
		check(strcmp(security_audit_records[i].text,
			     "orchestrator-audit-anchor") != 0,
		      "foreign span audit hidden");
	check(read(audit_gate_fd, &phase, 1) == 1 && phase == 'G',
	      "wait trusted audit route");
	memset(&security_sentinel_event, 0, sizeof(security_sentinel_event));
	security_sentinel_event.type = AGENT_EVENT_MESSAGE;
	security_sentinel_event.corr_id = 8699;
	strcpy(security_sentinel_event.payload, "audit-delegation");
	check(agent_wake(getppid(), &security_sentinel_event) == AGENT_STATUS_OK,
	      "send trusted audit delegation");
	check(read(audit_gate_fd, &phase, 1) == 1 && phase == 'C',
	      "wait audit delegation consume");
	close(audit_gate_fd);
	printf("agentsecurity_ucore: trusted_span_authority=1\n");
	check_wake_event_authorization();
	make_op(&security_sentinel_op, AGENT_TOOL_CAPABILITY_CHECK, 8101,
		AGENT_ROLE_RECOVERY, "action_commit");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel spoof cap");
	check(security_sentinel_result.value1 == AGENT_ROLE_SENTINEL,
	      "real sentinel role");
	make_op(&security_sentinel_op, AGENT_TOOL_ACTION_COMMIT, 8105,
		AGENT_ROLE_RECOVERY,
		"label=align;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel generic action");
	make_op(&security_sentinel_op, AGENT_TOOL_ARTIFACT_UPDATE, 8106,
		AGENT_ROLE_RECOVERY,
		"label=report;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel generic artifact");
	make_op(&security_sentinel_op, AGENT_TOOL_LLM_RESPONSE, 8107, getppid(),
		"template response");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel llm relay");
	make_op(&security_sentinel_op, AGENT_TOOL_DEPENDENCY_UPDATE, 8108, 0,
		"source=align;target=review");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED,
		"sentinel dependency update denied");
	make_op(&security_sentinel_op, AGENT_TOOL_RERUN_STAGE, 8102,
		AGENT_ROLE_RECOVERY,
		"align");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel spoof rerun");
	make_op(&security_sentinel_op, AGENT_TOOL_WRITE_REPORT, 8103,
		AGENT_ROLE_RECOVERY,
		"fake report");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel spoof report");
	make_op(&security_sentinel_op, AGENT_TOOL_READ_FILE_DIGEST, 8104, 0,
		"r42align");
	run_one(&security_sentinel_op, &security_sentinel_result,
		AGENT_STATUS_DENIED, "sentinel digest denied");
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
	memset(&security_sentinel_meta, 0, sizeof(security_sentinel_meta));
	strcpy(security_sentinel_meta.stage, "align");
	strcpy(security_sentinel_meta.status, "ok");
	check(agent_file_meta_set(&security_sentinel_meta) == AGENT_STATUS_DENIED,
	      "sentinel meta write denied");
	check_align_status("RUN-042", "failed");
	for (int i = 0; i < LOW_AUDIT_CHURN_COUNT; i++) {
		memset(&security_sentinel_context, 0,
		       sizeof(security_sentinel_context));
		security_sentinel_context.tool_id = AGENT_TOOL_ECHO;
		security_sentinel_context.request_id = 8700 + i;
		security_sentinel_context.status = AGENT_STATUS_OK;
		strcpy(security_sentinel_context.payload, "low-audit-churn");
		strcpy(security_sentinel_context.result,
		       i == LOW_AUDIT_CHURN_COUNT - 1 ?
			       "low-audit-new" : "low-audit-churn");
		check(context_push(&security_sentinel_context) == AGENT_STATUS_OK,
		      "bounded low audit churn");
	}
	printf("agentsecurity_ucore: sentinel spoof_denied=1\n");
	exit(0);
}

static TEST_NOINLINE void run_recovery(void)
{
	struct agent_op op;
	struct agent_result res;

	check_role(AGENT_ROLE_RECOVERY, "recovery");
	check_delegation_denied("recovery");
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

static TEST_NOINLINE void run_investigator(void)
{
	check_role(AGENT_ROLE_INVESTIGATOR, "investigator");
	check_delegation_denied("investigator");
	exit(0);
}

static TEST_NOINLINE void run_artifact(void)
{
	struct agent_file_edit_state state;
	struct agent_op op;
	struct agent_result res;

	check_role(AGENT_ROLE_ARTIFACT, "artifact");
	check_delegation_denied("artifact");
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 9201, AGENT_ROLE_SENTINEL,
		"label=align;run_id=RUN-999;namespace=lab-gene-x");
	run_one(&op, &res, AGENT_STATUS_DENIED, "artifact action denied");
	for (int i = 0; i < 80; i++) {
		memset(&state, 0, sizeof(state));
		check(agent_file_edit_begin("auditobj", 0, 200, &state) == 0,
		      "artifact effect churn begin");
		check(agent_file_edit_abort(state.lease_id) == 0,
		      "artifact effect churn abort");
	}
	printf("agentsecurity_ucore: artifact_action_denied=1\n");
	printf("agentsecurity_ucore: artifact_effect_churn_bounded=1\n");
	exit(0);
}

static TEST_NOINLINE void run_orphan_cancel_victim(int ready_fd, int result_fd,
					   int gate_fd)
{
	char ready = 'R';
	char gate = 0;

	(void)result_fd;
	check_role(AGENT_ROLE_SENTINEL, "cancel-victim");
	check(agent_watch(AGENT_EVENT_MESSAGE, "release=controller") == 0,
	      "watch controller release");
	check(write(ready_fd, &ready, 1) == 1, "report cancel victim ready");
	/* 没有可信祖先，必须由控制器拆除来中断。 */
	(void)read(gate_fd, &gate, 1);
	exit(99);
}

static TEST_NOINLINE void run_retired_cancel_controller(int report_fd,
						 int victim_gate_fd)
{
	int ready_pipe[2];
	int victim_pid;
	char ready = 0;

	check_role(AGENT_ROLE_ORCHESTRATOR, "retired-controller");
	check(pipe(ready_pipe) == 0, "create cancel victim ready pipe");
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate cancel victim ready pipe");
	check(agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK,
	      "delegate cancel victim report pipe");
	check(agent_scope_delegate_fd(victim_gate_fd) == AGENT_STATUS_OK,
	      "delegate cancel victim gate pipe");
	victim_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(victim_pid >= 0, "create orphan cancel victim");
	if (victim_pid == 0)
		run_orphan_cancel_victim(ready_pipe[1], report_fd,
					 victim_gate_fd);
	check(read(ready_pipe[0], &ready, 1) == 1 && ready == 'R',
	      "wait cancel victim ready");
	check(agent_route_config(getpid(), victim_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant retired controller route");
	check(write(report_fd, &victim_pid, sizeof(victim_pid)) ==
		      (int)sizeof(victim_pid),
	      "report orphan cancel victim");
	exit(0);
}

static TEST_NOINLINE void run_replacement_cancel_controller(int victim_pid)
{
	struct agent_event event;
	struct agent_sched_config config;

	check_role(AGENT_ROLE_ORCHESTRATOR, "replacement-controller");
	check(agent_wait_cancel(victim_pid, "stale-controller") ==
		      AGENT_STATUS_NOT_FOUND,
	      "replacement controller cannot cancel reaped orphan");
	check(agent_route_config(getpid(), victim_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_NOT_FOUND,
	      "replacement controller cannot route to reaped orphan");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 8301;
	strcpy(event.payload, "release=controller");
	check(agent_wake(victim_pid, &event) == AGENT_STATUS_NOT_FOUND,
	      "replacement controller stale endpoint absent");
	memset(&config, 0, sizeof(config));
	config.target_pid = victim_pid;
	config.update_mask = AGENT_SCHED_CONFIG_WEIGHT;
	config.weight = 151;
	check(agent_sched_config(&config) == AGENT_STATUS_NOT_FOUND,
	      "replacement controller cannot schedule reaped orphan");
	printf("agentsecurity_ucore: wait_cancel_scope=1\n");
	printf("agentsecurity_ucore: message_route_lifecycle=1\n");
	exit(0);
}

static void check_wait_cancel_controller_lifecycle(void)
{
	int report_pipe[2];
	int victim_gate[2];
	int controller_pid;
	int replacement_pid;
	int victim_pid = -1;
	int status = 0;
	char victim_result = 0;

	check(pipe(report_pipe) == 0, "create cancel controller report pipe");
	check(pipe(victim_gate) == 0, "create stale route victim gate");
	check(agent_scope_delegate_fd(report_pipe[1]) == AGENT_STATUS_OK,
	      "delegate cancel controller report pipe");
	check(agent_scope_delegate_fd(victim_gate[0]) == AGENT_STATUS_OK,
	      "delegate cancel controller victim gate");
	controller_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(controller_pid >= 0, "create retired cancel controller");
	if (controller_pid == 0)
		run_retired_cancel_controller(report_pipe[1], victim_gate[0]);
	check(read(report_pipe[0], &victim_pid, sizeof(victim_pid)) ==
		      (int)sizeof(victim_pid),
	      "read orphan cancel victim");
	check(victim_pid > 0, "orphan cancel victim pid");
	check(close(report_pipe[1]) == 0,
	      "close bootstrap orphan report writer");
	check(waitpid(controller_pid, &status) == controller_pid,
	      "wait retired cancel controller");
	check(status == 0, "retired cancel controller status");
	check(read(report_pipe[0], &victim_result, 1) < 0,
	      "rootless controller child is forcibly reaped");
	replacement_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(replacement_pid >= 0, "create replacement cancel controller");
	if (replacement_pid == 0)
		run_replacement_cancel_controller(victim_pid);
	check(waitpid(replacement_pid, &status) == replacement_pid,
	      "wait replacement cancel controller");
	check(status == 0, "replacement cancel controller status");
	close(report_pipe[0]);
	close(victim_gate[0]);
	close(victim_gate[1]);
	printf("agentsecurity_ucore: wait_cancel_controller_lifecycle=1\n");
	printf("agentsecurity_ucore: orphan_controller_reaped=1\n");
}

static TEST_NOINLINE void run_handoff_victim(int report_fd)
{
	struct agent_event event;
	char done = 'C';

	check_role(AGENT_ROLE_SENTINEL, "handoff-victim");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, -1) == AGENT_STATUS_CANCELLED,
	      "handoff root cancels victim");
	check(write(report_fd, &done, 1) == 1,
	      "report handoff cancellation");
	exit(0);
}

static TEST_NOINLINE void run_departing_subcontroller(int report_fd)
{
	int victim_pid;

	check_role(AGENT_ROLE_ORCHESTRATOR, "departing-subcontroller");
	check(agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK,
	      "delegate handoff victim report");
	victim_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(victim_pid >= 0, "create handoff victim");
	if (victim_pid == 0)
		run_handoff_victim(report_fd);
	check(write(report_fd, &victim_pid, sizeof(victim_pid)) ==
		      (int)sizeof(victim_pid),
	      "publish handoff victim");
	exit(0);
}

static TEST_NOINLINE void run_sibling_sched_probe(int victim_pid)
{
	struct agent_sched_config config;

	check_role(AGENT_ROLE_ORCHESTRATOR, "handoff-sibling");
	memset(&config, 0, sizeof(config));
	config.target_pid = victim_pid;
	config.update_mask = AGENT_SCHED_CONFIG_WEIGHT;
	config.weight = 152;
	check(agent_sched_config(&config) == AGENT_STATUS_DENIED,
	      "sibling controller cannot schedule handoff victim");
	exit(0);
}

static TEST_NOINLINE void check_controller_handoff(void)
{
	struct agent_sched_config config;
	char done = 0;
	int report[2];
	int controller_pid;
	int sibling_pid;
	int victim_pid = -1;
	int status = -1;

	check(pipe(report) == 0, "create controller handoff report");
	check(agent_scope_delegate_fd(report[1]) == AGENT_STATUS_OK,
	      "delegate subcontroller report");
	controller_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(controller_pid >= 0, "create departing subcontroller");
	if (controller_pid == 0)
		run_departing_subcontroller(report[1]);
	check(close(report[1]) == 0, "close root handoff report writer");
	check(read(report[0], &victim_pid, sizeof(victim_pid)) ==
		      (int)sizeof(victim_pid) && victim_pid > 0,
	      "receive handoff victim");
	check(waitpid(controller_pid, &status) == controller_pid && status == 0,
	      "wait departing subcontroller");

	sibling_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(sibling_pid >= 0, "create sibling scheduling probe");
	if (sibling_pid == 0)
		run_sibling_sched_probe(victim_pid);
	status = -1;
	check(waitpid(sibling_pid, &status) == sibling_pid && status == 0,
	      "wait sibling scheduling probe");

	memset(&config, 0, sizeof(config));
	config.target_pid = victim_pid;
	config.update_mask = AGENT_SCHED_CONFIG_WEIGHT;
	config.weight = 153;
	check(agent_sched_config(&config) == AGENT_STATUS_OK,
	      "root schedules handed-off victim");
	check(agent_wait_cancel(victim_pid, "controller-handoff") ==
		      AGENT_STATUS_OK,
	      "root cancels handed-off victim");
	check(read(report[0], &done, 1) == 1 && done == 'C',
	      "handoff victim completes cancellation");
	check(read(report[0], &done, 1) < 0,
	      "handoff victim releases endpoint");
	check(close(report[0]) == 0, "close controller handoff report");
	printf("agentsecurity_ucore: controller_handoff=1 sibling_sched_denied=1\n");
}

static void check_route_tool(int tool, int target_pid, uint64 request_id,
			     const char *payload, int expected_status,
			     const char *msg)
{
	struct agent_op op;
	struct agent_result res;

	make_op(&op, tool, request_id, target_pid, payload);
	run_one(&op, &res, expected_status, msg);
}

static void check_route_wake(int target_pid, uint64 corr_id,
			     const char *payload, int expected_status,
			     const char *msg)
{
	struct agent_event event;

	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = corr_id;
	strcpy(event.payload, payload);
	check(agent_wake(target_pid, &event) == expected_status, msg);
}

static void receive_route_message(int source_pid, uint64 corr_id,
				  const char *payload)
{
	struct agent_event event;

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 200) == AGENT_STATUS_OK,
	      "receive routed message");
	check(event.type == AGENT_EVENT_MESSAGE, "routed message type");
	check(event.source_pid == source_pid, "routed message source");
	check(event.corr_id == corr_id, "routed message correlation");
	check(strcmp(event.payload, payload) == 0, "routed message payload");
}

static TEST_NOINLINE void run_ipc_route_target(int setup_fd, int ready_fd)
{
	struct agent_event event;
	int source_pid = -1;
	char phase = 'T';

	check_role(AGENT_ROLE_RECOVERY, "route-target");
	check(agent_watch(AGENT_EVENT_MESSAGE, "ipc-route-") == 0,
	      "watch routed messages");
	check(write(ready_fd, &phase, 1) == 1, "report route target ready");
	check(read(setup_fd, &source_pid, sizeof(source_pid)) ==
		      (int)sizeof(source_pid) && source_pid > 0,
	      "read route source pid");
	receive_route_message(source_pid, 8521, "ipc-route-allowed-wake");
	receive_route_message(source_pid, 8522, "ipc-route-allowed-tool");
	receive_route_message(source_pid, 8523, "ipc-route-allowed-llm");
	phase = 'A';
	check(write(ready_fd, &phase, 1) == 1,
	      "report routed messages consumed");
	check(read(setup_fd, &phase, 1) == 1 && phase == 'X',
	      "wait revoked route probes");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 0) == AGENT_STATUS_TIMEOUT,
	      "revoked messages not queued");
	printf("agentsecurity_ucore: route_target_isolated=1\n");
	exit(0);
}

static TEST_NOINLINE void run_ipc_route_source(int target_pid,
					      int orchestrator_pid, int gate_fd,
					      int report_fd)
{
	char phase;

	check_role(AGENT_ROLE_SENTINEL, "route-source");
	check(agent_route_config(getpid(), target_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_DENIED,
	      "low role cannot grant route");

	check_route_wake(target_pid, 8501, "ipc-route-denied-recovery-wake",
			 AGENT_STATUS_DENIED, "unrouted recovery wake denied");
	check_route_tool(AGENT_TOOL_SEND_MESSAGE, target_pid, 8502,
			 "ipc-route-denied-recovery-tool",
			 AGENT_STATUS_DENIED,
			 "unrouted recovery send tool denied");
	check_route_tool(AGENT_TOOL_LLM_REQUEST, target_pid, 8503,
			 "ipc-route-denied-recovery-llm",
			 AGENT_STATUS_DENIED,
			 "unrouted recovery llm request denied");
	check_route_wake(orchestrator_pid, 8511,
			 "ipc-route-denied-orchestrator-wake",
			 AGENT_STATUS_DENIED,
			 "unrouted orchestrator wake denied");
	check_route_tool(AGENT_TOOL_SEND_MESSAGE, orchestrator_pid, 8512,
			 "ipc-route-denied-orchestrator-tool",
			 AGENT_STATUS_DENIED,
			 "unrouted orchestrator send tool denied");
	check_route_tool(AGENT_TOOL_LLM_REQUEST, orchestrator_pid, 8513,
			 "ipc-route-denied-orchestrator-llm",
			 AGENT_STATUS_DENIED,
			 "unrouted orchestrator llm request denied");
	phase = 'D';
	check(write(report_fd, &phase, 1) == 1, "report route denial phase");
	check(read(gate_fd, &phase, 1) == 1 && phase == 'G',
	      "wait route grant");

	check_route_wake(target_pid, 8521, "ipc-route-allowed-wake",
			 AGENT_STATUS_OK, "granted recovery wake");
	check_route_tool(AGENT_TOOL_SEND_MESSAGE, target_pid, 8522,
			 "ipc-route-allowed-tool", AGENT_STATUS_OK,
			 "granted recovery send tool");
	check_route_tool(AGENT_TOOL_LLM_REQUEST, target_pid, 8523,
			 "ipc-route-allowed-llm", AGENT_STATUS_OK,
			 "granted recovery llm request");
	phase = 'A';
	check(write(report_fd, &phase, 1) == 1, "report routed messages");
	check(read(gate_fd, &phase, 1) == 1 && phase == 'R',
	      "wait route revoke");

	check_route_wake(target_pid, 8531, "ipc-route-revoked-wake",
			 AGENT_STATUS_DENIED, "revoked recovery wake denied");
	check_route_tool(AGENT_TOOL_SEND_MESSAGE, target_pid, 8532,
			 "ipc-route-revoked-tool", AGENT_STATUS_DENIED,
			 "revoked recovery send tool denied");
	check_route_tool(AGENT_TOOL_LLM_REQUEST, target_pid, 8533,
			 "ipc-route-revoked-llm", AGENT_STATUS_DENIED,
			 "revoked recovery llm request denied");
	phase = 'X';
	check(write(report_fd, &phase, 1) == 1, "report route revoke phase");
	printf("agentsecurity_ucore: route_source_enforced=1\n");
	exit(0);
}

static TEST_NOINLINE void check_ipc_route_authorization(void)
{
	struct agent_event event;
	int target_setup[2];
	int target_ready[2];
	int source_gate[2];
	int source_report[2];
	int target_pid;
	int source_pid;
	int status = 0;
	char phase = 0;

	check(pipe(target_setup) == 0, "create route target setup pipe");
	check(pipe(target_ready) == 0, "create route target ready pipe");
	check(pipe(source_gate) == 0, "create route source gate pipe");
	check(pipe(source_report) == 0, "create route source report pipe");
	check(agent_watch(AGENT_EVENT_MESSAGE, "ipc-route-") == 0,
	      "watch orchestrator route probes");
	check(agent_scope_delegate_fd(target_setup[0]) == AGENT_STATUS_OK,
	      "delegate route target setup pipe");
	check(agent_scope_delegate_fd(target_ready[1]) == AGENT_STATUS_OK,
	      "delegate route target ready pipe");
	target_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(target_pid >= 0, "create route target");
	if (target_pid == 0)
		run_ipc_route_target(target_setup[0], target_ready[1]);
	check(read(target_ready[0], &phase, 1) == 1 && phase == 'T',
	      "wait route target ready");

	check(agent_scope_delegate_fd(source_gate[0]) == AGENT_STATUS_OK,
	      "delegate route source gate pipe");
	check(agent_scope_delegate_fd(source_report[1]) == AGENT_STATUS_OK,
	      "delegate route source report pipe");
	source_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(source_pid >= 0, "create route source");
	if (source_pid == 0)
		run_ipc_route_source(target_pid, getppid(), source_gate[0],
				     source_report[1]);
	check(write(target_setup[1], &source_pid, sizeof(source_pid)) ==
		      (int)sizeof(source_pid),
	      "configure route target source");
	check(read(source_report[0], &phase, 1) == 1 && phase == 'D',
	      "wait unauthorized route probes");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 0) == AGENT_STATUS_TIMEOUT,
	      "unrouted orchestrator messages not queued");

	check(agent_route_config(source_pid, target_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "orchestrator grants child route");
	phase = 'G';
	check(write(source_gate[1], &phase, 1) == 1, "release granted source");
	check(read(source_report[0], &phase, 1) == 1 && phase == 'A',
	      "wait granted route traffic");
	check(read(target_ready[0], &phase, 1) == 1 && phase == 'A',
	      "wait routed messages consumed");
	check(agent_route_config(source_pid, target_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_REVOKE) == AGENT_STATUS_OK,
	      "orchestrator revokes child route");
	phase = 'R';
	check(write(source_gate[1], &phase, 1) == 1, "release revoked source");
	check(read(source_report[0], &phase, 1) == 1 && phase == 'X',
	      "wait revoked route probes");
	phase = 'X';
	check(write(target_setup[1], &phase, 1) == 1,
	      "release route target after revoke");

	check(waitpid(source_pid, &status) == source_pid, "wait route source");
	check(status == 0, "route source status");
	check(waitpid(target_pid, &status) == target_pid, "wait route target");
	check(status == 0, "route target status");
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "ipc-route-") == 1,
	      "unwatch orchestrator route probes");
	printf("agentsecurity_ucore: ipc_route_authorization=1\n");
}

static TEST_NOINLINE void run_consent_route_target(int ready_fd)
{
	struct agent_event event;
	char phase = 'C';

	check_role(AGENT_ROLE_RECOVERY, "consent-route-target");
	check(agent_watch(AGENT_EVENT_LLM_DONE, "consent-response") == 0,
	      "watch consent response");
	check(agent_route_config(getppid(), getpid(),
				 AGENT_IPC_EVENT_LLM_DONE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "target accepts llm response source");
	check(write(ready_fd, &phase, 1) == 1,
	      "report target route consent");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 200) == AGENT_STATUS_OK,
	      "receive consent response");
	check(event.type == AGENT_EVENT_LLM_DONE,
	      "consent response type");
	check(event.source_pid == getppid(), "consent response source");
	check(event.corr_id == 8552, "consent response correlation");
	check(strcmp(event.payload, "consent-response") == 0,
	      "consent response payload");
	exit(0);
}

static TEST_NOINLINE void check_target_route_consent(void)
{
	int ready[2];
	int target_pid;
	int status = 0;
	char phase = 0;

	check(pipe(ready) == 0, "create consent route ready pipe");
	check(agent_scope_delegate_fd(ready[1]) == AGENT_STATUS_OK,
	      "delegate consent route ready pipe");
	target_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(target_pid >= 0, "create consent route target");
	if (target_pid == 0)
		run_consent_route_target(ready[1]);
	check(read(ready[0], &phase, 1) == 1 && phase == 'C',
	      "wait target route consent");
	check_route_wake(target_pid, 8551, "consent-message-denied",
			 AGENT_STATUS_DENIED,
			 "llm-only route rejects message");
	check_route_tool(AGENT_TOOL_LLM_RESPONSE, target_pid, 8552,
			 "consent-response", AGENT_STATUS_OK,
			 "target-consented llm response");
	check(waitpid(target_pid, &status) == target_pid,
	      "wait consent route target");
	check(status == 0, "consent route target status");
	close(ready[0]);
	close(ready[1]);
	printf("agentsecurity_ucore: target_route_consent=1\n");
}

static TEST_NOINLINE void run_route_lifetime_source(int gate_fd, uint64 corr_id)
{
	struct agent_event event;
	char phase = 0;

	check_role(AGENT_ROLE_SENTINEL, "route-lifetime-source");
	check(read(gate_fd, &phase, 1) == 1 && phase == 'X',
	      "wait route lifetime release");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = corr_id;
	strcpy(event.payload, "route-lifetime");
	check(agent_wake(getppid(), &event) == AGENT_STATUS_OK,
	      "send through churned route");
	exit(0);
}

static TEST_NOINLINE void check_route_slot_reclamation(void)
{
	int gate[2];
	int source_pid;
	int status = 0;
	char phase = 'X';
	struct agent_event event;

	check(agent_watch(AGENT_EVENT_MESSAGE, "route-lifetime") == 0,
	      "watch route lifetime messages");
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX + 2; i++) {
		check(pipe(gate) == 0, "create route lifetime gate");
		check(agent_scope_delegate_fd(gate[0]) == AGENT_STATUS_OK,
		      "delegate route lifetime gate");
		source_pid = agent_create_role(AGENT_ROLE_SENTINEL);
		check(source_pid >= 0, "create route lifetime source");
		if (source_pid == 0)
			run_route_lifetime_source(gate[0], 8600 + i);
		check(agent_route_config(source_pid, getpid(),
					 AGENT_IPC_EVENT_MESSAGE,
					 AGENT_IPC_ROUTE_GRANT) ==
			      AGENT_STATUS_OK,
		      "grant churned source route");
		check(write(gate[1], &phase, 1) == 1,
		      "release route lifetime source");
		memset(&event, 0, sizeof(event));
		check(agent_wait(&event, 50) == AGENT_STATUS_OK,
		      "receive through churned route");
		check(event.type == AGENT_EVENT_MESSAGE &&
			      event.source_pid == source_pid &&
			      event.corr_id == (uint64)(8600 + i) &&
			      strcmp(event.payload, "route-lifetime") == 0,
		      "validate churned route message");
		check(waitpid(source_pid, &status) == source_pid,
		      "wait route lifetime source");
		check(status == 0, "route lifetime source status");
		close(gate[0]);
		close(gate[1]);
	}
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "route-lifetime") == 1,
	      "unwatch route lifetime messages");
	printf("agentsecurity_ucore: route_slot_reclaimed=1\n");
}

static void run_orchestrator(void)
{
	char phase;
	int audit_gate[2];
	int artifact_fd;
	int pid;
	int sentinel_pid;
	int n;
	int found_anchor = 0;
	int found_cause = 0;
	int found_latest = 0;
	int status = 0;

	check_role(AGENT_ROLE_ORCHESTRATOR, "orchestrator");
	check_orchestrator_plain_fork_denied();
	check_ipc_route_authorization();
	check_target_route_consent();
	check_route_slot_reclamation();
	check_controller_handoff();
	check_preinit_index_query();
	check(agent_file_meta_init() == 0, "meta init");
	check_legacy_tool_mismatch();
	check_legacy_param_validation();
	set_align_failed("RUN-042", 3, "r42aerr");
	set_align_failed("RUN-999", 30, "r999aerr");
	set_align_failed("RUN-998", 31, "r998aerr");
	set_align_failed("RUN-AUDIT", 32, "raudit");
	set_report_failed("RUN-042", 40, "r42rerr");
	set_report_failed("RUN-999", 41, "r999rerr");
	check(agent_info(&security_agent_info) == 0,
	      "orchestrator span authority");
	security_orchestrator_span = security_agent_info.current_span_id;
	make_op(&security_anchor_op, AGENT_TOOL_ECHO, 8698, 0,
		"orchestrator-audit-anchor");
	run_one(&security_anchor_op, &security_anchor_result, AGENT_STATUS_OK,
		"orchestrator audit anchor");
	check(pipe(audit_gate) == 0, "create trusted audit gate");
	check(agent_watch(AGENT_EVENT_MESSAGE, "audit-delegation") == 0,
	      "watch trusted audit delegation");
	check(agent_scope_delegate_fd(audit_gate[0]) == AGENT_STATUS_OK,
	      "delegate trusted audit gate");
	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create sentinel");
	if (pid == 0)
		run_sentinel(audit_gate[0]);
	sentinel_pid = pid;
	check(close(audit_gate[0]) == 0, "close parent audit gate reader");
	check(agent_route_config(sentinel_pid, getpid(),
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant trusted audit route");
	phase = 'G';
	check(write(audit_gate[1], &phase, 1) == 1,
	      "release trusted audit sender");
	memset(&security_audit_event, 0, sizeof(security_audit_event));
	check(agent_wait(&security_audit_event, 50) == AGENT_STATUS_OK,
	      "consume trusted audit delegation");
	check(security_audit_event.type == AGENT_EVENT_MESSAGE &&
		      security_audit_event.source_pid == sentinel_pid &&
		      security_audit_event.corr_id == 8699 &&
		      strcmp(security_audit_event.payload,
			     "audit-delegation") == 0,
	      "validate trusted audit delegation");
	check(agent_route_config(sentinel_pid, getpid(),
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_REVOKE) == AGENT_STATUS_OK,
	      "revoke trusted audit route");
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "audit-delegation") == 1,
	      "unwatch trusted audit delegation");
	make_op(&security_anchor_op, AGENT_TOOL_ACTION_COMMIT, 8699, 0,
		"label=align;run_id=RUN-AUDIT;namespace=lab-gene-x");
	run_one(&security_anchor_op, &security_anchor_result, AGENT_STATUS_OK,
		"trusted authority audit anchor");
	n = agent_provenance_snapshot(security_provenance_edges, 128);
	check(n > 0, "trusted cause provenance snapshot");
	for (int i = 0; i < n; i++)
		if (security_provenance_edges[i].kind ==
				AGENT_PROVENANCE_EDGE_CONTEXT &&
		    security_provenance_edges[i].source_pid == sentinel_pid &&
		    security_provenance_edges[i].target_pid == getpid() &&
		    security_provenance_edges[i].tool_id ==
				AGENT_TOOL_AGENT_WAIT)
			found_cause = 1;
	check(found_cause, "trusted IPC cause attributed to sender");
	phase = 'C';
	check(write(audit_gate[1], &phase, 1) == 1,
	      "release trusted audit churn");
	check(close(audit_gate[1]) == 0, "close parent audit gate writer");
	check(waitpid(pid, &status) == pid, "wait sentinel");
	check(status == 0, "sentinel status");
	memset(&security_audit_filter, 0, sizeof(security_audit_filter));
	security_audit_filter.flags = AGENT_AUDIT_FILTER_PID |
				      AGENT_AUDIT_FILTER_KIND;
	security_audit_filter.pid = sentinel_pid;
	security_audit_filter.kind = AGENT_AUDIT_KIND_CONTEXT;
	n = agent_audit_query(&security_audit_filter, security_audit_records,
			      AGENT_AUDIT_MAX_RECORDS);
	check(n > 0 && n <= AGENT_AUDIT_LOW_PRINCIPAL_MAX,
	      "low principal audit partition bounded");
	for (int i = 0; i < n; i++)
		if (strcmp(security_audit_records[i].text,
			   "low-audit-new") == 0)
			found_latest = 1;
	check(found_latest, "low principal audit remains current");
	printf("agentsecurity_ucore: trusted_cause_attribution=1\n");
	check(context_clear() == 0, "leave delegated audit span");
	pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
	check(pid >= 0, "create investigator");
	if (pid == 0)
		run_investigator();
	check(waitpid(pid, &status) == pid, "wait investigator");
	check(status == 0, "investigator status");
	artifact_fd = open("auditobj", O_CREATE | O_WRONLY | O_TRUNC);
	check(artifact_fd >= 0, "create artifact audit object");
	check(write(artifact_fd, "audit", 5) == 5,
	      "write artifact audit object");
	check(close(artifact_fd) == 0, "close artifact audit object");
	for (int i = 0; i < 10; i++) {
		pid = agent_create_role(AGENT_ROLE_ARTIFACT);
		check(pid >= 0, "create artifact audit principal");
		if (pid == 0)
			run_artifact();
		status = -1;
		check(waitpid(pid, &status) == pid,
		      "wait artifact audit principal");
		check(status == 0, "artifact audit principal status");
	}
	memset(&security_audit_filter, 0, sizeof(security_audit_filter));
	security_audit_filter.flags = AGENT_AUDIT_FILTER_PID |
				      AGENT_AUDIT_FILTER_TOOL_ID;
	security_audit_filter.pid = getpid();
	security_audit_filter.tool_id = AGENT_TOOL_ACTION_COMMIT;
	n = agent_audit_query(&security_audit_filter, security_audit_records,
			      AGENT_AUDIT_MAX_RECORDS);
	check(n >= 1, "privileged audit partition visible");
	for (int i = 0; i < n; i++)
		if (strncmp(security_audit_records[i].text, "action_commit",
			    strlen("action_commit")) == 0)
			found_anchor = 1;
	check(found_anchor,
	      "writer churn preserves another principal authority audit");
	printf("agentsecurity_ucore: audit_authority_partition=1\n");
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
	printf("agentsecurity_ucore: wait_cancel_capability_split=1\n");
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
	check(agent_route_config(1, 1, AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == -1,
	      "plain route config denied");
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
	fd = open(".agentmeta1", O_RDONLY);
	check(fd == -1, "plain open agentmeta1 denied");
	fd = open(".agentmeta1", O_CREATE | O_RDWR);
	check(fd == -1, "plain create agentmeta1 denied");
	check(unlink(".agentmeta1") == -1,
	      "plain unlink agentmeta1 denied");
	printf("agentsecurity_ucore: plain_process_denied=1\n");
	printf("agentsecurity_ucore: .agentmeta_protected=1\n");
}

static void check_legacy_mail_fail_closed(void)
{
	char byte = 0;

	check(mailread(&byte, sizeof(byte)) == -1,
	      "retired mailread fails closed");
	check(mailwrite(getpid(), &byte, sizeof(byte)) == -1,
	      "retired mailwrite fails closed");
	check(agent_info(&security_agent_info) == 0 &&
		      security_agent_info.legacy_mailbox_allocated == 0 &&
		      security_agent_info.legacy_mailbox_pages == 0 &&
		      security_agent_info.legacy_mailbox_queue_count == 0,
	      "retired legacy mailbox metrics stay zero");
	printf("agentsecurity_ucore: legacy_mail_fail_closed=1\n");
}

int main(int argc, char **argv)
{
	int pid;
	int status = 0;
	char *exec_probe_argv[] = {
		"agentsecurity_ucore", "--bootstrap-exec-probe", 0
	};

	if (argc > 1 && strcmp(argv[1], "--untrusted-probe") == 0) {
		check_plain_identity();
		check_creation_authority_denied();
		printf("agentsecurity_ucore: untrusted_exec_role_creation_denied=1\n");
		return 0;
	}
	if (argc > 1 && strcmp(argv[1], "--bootstrap-exec-probe") == 0) {
		check_plain_identity();
		check_creation_authority_denied();
		printf("agentsecurity_ucore: bootstrap_exec_grant_revoked=1\n");
		printf("agentsecurity_ucore: parent passed\n");
		return 0;
	}

	printf("agentsecurity_ucore: Agent permission test\n");
	check_bootstrap_identity();
	check_legacy_mail_fail_closed();
	check_plain_process_denied();
	check_plain_child_creation_denied();
	check_wait_cancel_controller_lifecycle();
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_orchestrator();
	printf("agentsecurity_ucore: bootstrap_orchestrator_create=1\n");
	check(waitpid(pid, &status) == pid, "wait orchestrator");
	check(status == 0, "orchestrator status");
	check_reaped_agent_slot_cleared();
	if (exec("agentsecurity_ucore", exec_probe_argv) < 0)
		check(0, "bootstrap exec probe");
	return 1;
}
