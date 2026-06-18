#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int recovery_pid;
static int investigator_pid;
static int ready_fd = -1;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("labdemo_ucore: check failed: %s\n", msg);
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

static void created(const char *role)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "agent info");
	printf("labdemo_ucore: created role=%s pid=%d context=%p\n", role,
	       getpid(), (void *)info.context_base);
	printf("agentos:event type=AGENT_CREATED role=%s pid=%d context=%p\n",
	       role, getpid(), (void *)info.context_base);
}

static void ready(char c)
{
	if (ready_fd >= 0)
		check(write(ready_fd, &c, 1) == 1, "ready write");
}

static void run_sentinel(void)
{
	struct agent_event event;
	struct agent_op op;
	struct agent_result res;

	created("sentinel");
	check(agent_heartbeat(5) == 0, "heartbeat");
	check(agent_watch(AGENT_EVENT_FILE_STATUS, "status=failed") == 0,
	      "watch failed");
	ready('S');
	printf("agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed\n");
	check(agent_wait(&event, 300) == AGENT_STATUS_OK, "sentinel wait");
	printf("labdemo_ucore: sentinel event payload=%s\n", event.payload);

	make_op(&op, AGENT_TOOL_QUERY_FILE, 1001, 0,
		"project=lab-gene-x;run_id=RUN-042;status=failed");
	run_one(&op, &res, AGENT_STATUS_OK, "query failed files");
	printf("agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=%d used_index=%d seq=%d\n",
	       (int)res.value0, (int)(res.value2 & 1), (int)res.sequence);

	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 1002,
		AGENT_ROLE_SENTINEL, "rerun_stage");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel denied");
	printf("agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED seq=%d\n",
	       (int)res.sequence);

	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 1003, investigator_pid,
		"investigate RUN-042 align");
	run_one(&op, &res, AGENT_STATUS_OK, "message investigator");
	printf("agentos:event type=MESSAGE from=sentinel to=investigator status=OK seq=%d\n",
	       (int)res.sequence);
	exit(0);
}

static void run_investigator(void)
{
	struct agent_event event;
	struct agent_op op;
	struct agent_result res;
	struct agent_context_header header;
	struct agent_context_record records[8];
	int n;

	created("investigator");
	check(agent_watch(AGENT_EVENT_MESSAGE, "investigate") == 0,
	      "watch message");
	ready('I');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK,
	      "investigator wait");
	make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2001, 0, "align");
	run_one(&op, &res, AGENT_STATUS_OK, "read summary");
	printf("labdemo_ucore: investigator reason=%s\n", res.result);
	make_op(&op, AGENT_TOOL_DEPENDENCY_QUERY, 2002, 0, "align");
	run_one(&op, &res, AGENT_STATUS_OK, "dependency");
	printf("labdemo_ucore: affected stages=%s\n", res.result);
	n = context_snapshot(&header, records, 8);
	check(n >= 1, "investigator context");
	printf("agentos:event type=CONTEXT_SNAPSHOT role=investigator records=%d latest=%d\n",
	       n, (int)header.latest_sequence);
	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 2003, recovery_pid,
		"recover RUN-042 align");
	run_one(&op, &res, AGENT_STATUS_OK, "message recovery");
	exit(0);
}

static void run_recovery(void)
{
	struct agent_event event;
	struct agent_op op;
	struct agent_result res;
	struct agent_file_query query;
	struct agent_file_query_result result;

	created("recovery");
	check(agent_watch(AGENT_EVENT_MESSAGE, "recover") == 0, "watch recover");
	ready('R');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK, "recovery wait");
	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 3001,
		AGENT_ROLE_RECOVERY, "rerun_stage");
	run_one(&op, &res, AGENT_STATUS_OK, "capability");
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 4201, AGENT_ROLE_RECOVERY,
		"align");
	run_one(&op, &res, AGENT_STATUS_OK, "rerun align");
	printf("agentos:event type=ACTION role=recovery stage=align status=OK seq=%d\n",
	       (int)res.sequence);
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 4201, AGENT_ROLE_RECOVERY,
		"align");
	run_one(&op, &res, AGENT_STATUS_DUPLICATE, "duplicate");
	printf("agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE seq=%d\n",
	       (int)res.sequence);
	make_op(&op, AGENT_TOOL_WRITE_REPORT, 4202, AGENT_ROLE_RECOVERY,
		"RUN-042 recovery report");
	run_one(&op, &res, AGENT_STATUS_OK, "write report");
	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.status, "ok");
	strcpy(query.kind, "report");
	check(agent_file_query(&query, &result) >= 1, "final query");
	printf("labdemo_ucore: final report_query hits=%d used_index=%d scanned=%d\n",
	       result.total_hits, result.used_index, result.scanned_records);
	printf("agentos:event type=FINAL status=RECOVERED\n");
	exit(0);
}

static void inject_failure(void)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = 3;
	strcpy(meta.physical_name, "lab_RUN042_align_err");
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, "RUN-042");
	strcpy(meta.stage, "align");
	strcpy(meta.kind, "log");
	strcpy(meta.status, "failed");
	strcpy(meta.summary, "memory limit exceeded at align stage");
	meta.dependency_mask = AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
			       AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
	check(agent_file_meta_set(&meta) == 0, "inject failure");
	printf("agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align\n");
}

static void run_orchestrator(void)
{
	int sentinel_pid;
	int ready_pipe[2];
	int status = 0;
	int ok = 0;
	int ready_count = 0;
	char ch;

	created("orchestrator");
	check(agent_file_meta_init() == 0, "meta init");
	check(pipe(ready_pipe) == 0, "pipe");
	ready_fd = ready_pipe[1];
	recovery_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(recovery_pid >= 0, "create recovery");
	if (recovery_pid == 0)
		run_recovery();
	investigator_pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
	check(investigator_pid >= 0, "create investigator");
	if (investigator_pid == 0)
		run_investigator();
	sentinel_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(sentinel_pid >= 0, "create sentinel");
	if (sentinel_pid == 0)
		run_sentinel();
	close(ready_pipe[1]);
	while (ready_count < 3) {
		check(read(ready_pipe[0], &ch, 1) == 1, "ready read");
		ready_count++;
	}
	inject_failure();
	while (wait(&status) > 0) {
		check(status == 0, "child status");
		ok++;
	}
	check(ok == 3, "three agents");
	printf("labdemo_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int orchestrator_pid;
	int status = 0;

	printf("labdemo_ucore: Agent-OS laboratory recovery demo\n");
	orchestrator_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(orchestrator_pid >= 0, "create orchestrator");
	if (orchestrator_pid == 0)
		run_orchestrator();
	check(waitpid(orchestrator_pid, &status) == orchestrator_pid,
	      "wait orchestrator");
	check(status == 0, "orchestrator status");
	printf("labdemo_ucore: parent passed\n");
	return 0;
}
