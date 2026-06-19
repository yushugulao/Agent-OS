#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int recovery_pid;
static int investigator_pid;
static int ready_fd = -1;

#define DEMO_PROJECT "lab-gene-x"
#define DEMO_WORKFLOW "nightly-regression"
#define DEMO_RUN "RUN-042"
#define DEMO_INCIDENT "INC-RUN-042-ALIGN-OOM"
#define DEMO_PLAN "PLAN-RUN-042-RECOVER-1"
#define DEMO_ALIGN_CORR "RUN-042-align-rerun-1"
#define DEMO_REPORT_CORR "RUN-042-report-write-1"
#define DEMO_LLM_REQUEST "LLM-RUN-042-RCA-1"

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("labdemo_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int event_tick(void)
{
	return (int)get_mtime();
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
	printf("agentos:event type=AGENT_CREATED tick=%d role=%s pid=%d context=%p\n",
	       event_tick(), role, getpid(), (void *)info.context_base);
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
	printf("agentos:event type=WATCH_REGISTERED tick=%d role=sentinel event=FILE_STATUS filter=status=failed\n",
	       event_tick());
	printf("agentos:event type=AGENT_STATE tick=%d role=sentinel state=WAITING\n",
	       event_tick());
	check(agent_wait(&event, 300) == AGENT_STATUS_OK, "sentinel wait");
	printf("labdemo_ucore: sentinel event payload=%s\n", event.payload);
	printf("agentos:event type=AGENT_STATE tick=%d role=sentinel state=RUNNING event_id=%d corr_id=%d\n",
	       event_tick(), (int)event.event_id, (int)event.corr_id);

	make_op(&op, AGENT_TOOL_QUERY_FILE, 1001, 0,
		"project=" DEMO_PROJECT ";run_id=" DEMO_RUN ";status=failed");
	run_one(&op, &res, AGENT_STATUS_OK, "query failed files");
	printf("agentos:event type=TOOL_CALL tick=%d role=sentinel tool=query_file project=%s run_id=%s status=failed hits=%d used_index=%d seq=%d\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, (int)res.value0,
	       (int)(res.value2 & 1), (int)res.sequence);

	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 1002,
		AGENT_ROLE_SENTINEL, "rerun_stage");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel denied");
	printf("agentos:event type=AUDIT tick=%d role=sentinel action=rerun_stage result=DENIED reason=capability corr_id=%s seq=%d\n",
	       event_tick(), DEMO_ALIGN_CORR, (int)res.sequence);

	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 1003, investigator_pid,
		"investigate " DEMO_RUN " align");
	run_one(&op, &res, AGENT_STATUS_OK, "message investigator");
	printf("agentos:event type=MESSAGE tick=%d from=sentinel to=investigator status=OK corr_id=MSG-%s-S-I seq=%d\n",
	       event_tick(), DEMO_RUN, (int)res.sequence);
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
	int summary_seq;
	int dependency_seq;

	created("investigator");
	check(agent_watch(AGENT_EVENT_MESSAGE, "investigate") == 0,
	      "watch message");
	ready('I');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK,
	      "investigator wait");
	make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2001, 0, "align");
	run_one(&op, &res, AGENT_STATUS_OK, "read summary");
	summary_seq = res.sequence;
	printf("labdemo_ucore: investigator reason=%s\n", res.result);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=read_file_summary stage=align status=OK seq=%d\n",
	       event_tick(), summary_seq);
	make_op(&op, AGENT_TOOL_DEPENDENCY_QUERY, 2002, 0, "align");
	run_one(&op, &res, AGENT_STATUS_OK, "dependency");
	dependency_seq = res.sequence;
	printf("labdemo_ucore: affected stages=%s\n", res.result);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=dependency_query stage=align impact=%s seq=%d\n",
	       event_tick(), res.result, dependency_seq);
	printf("agentos:event type=LLM_CALL tick=%d mode=template task=explain_root_cause llm_request_id=%s project=%s run_id=%s refs=%d,%d status=OK\n",
	       event_tick(), DEMO_LLM_REQUEST, DEMO_PROJECT, DEMO_RUN,
	       summary_seq, dependency_seq);
	printf("agentos:event type=LLM_RESULT tick=%d mode=template llm_request_id=%s llm_status=OK llm_explanation=memory_limit referenced_sequences=%d,%d confidence=medium\n",
	       event_tick(), DEMO_LLM_REQUEST, summary_seq, dependency_seq);
	printf("agentos:event type=PLAN_CREATED tick=%d role=investigator plan=%s project=%s run_id=%s actions=align,report skip=prepare refs=%d,%d\n",
	       event_tick(), DEMO_PLAN, DEMO_PROJECT, DEMO_RUN, summary_seq,
	       dependency_seq);
	n = context_snapshot(&header, records, 8);
	check(n >= 1, "investigator context");
	printf("agentos:event type=CONTEXT_SNAPSHOT tick=%d role=investigator records=%d latest=%d\n",
	       event_tick(), n, (int)header.latest_sequence);
	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 2003, recovery_pid,
		"recover " DEMO_RUN " align plan=" DEMO_PLAN);
	run_one(&op, &res, AGENT_STATUS_OK, "message recovery");
	printf("agentos:event type=MESSAGE tick=%d from=investigator to=recovery status=OK corr_id=MSG-%s-I-R plan=%s seq=%d\n",
	       event_tick(), DEMO_RUN, DEMO_PLAN, (int)res.sequence);
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
	printf("agentos:event type=AUDIT tick=%d role=recovery action=rerun_stage result=ALLOW plan=%s seq=%d\n",
	       event_tick(), DEMO_PLAN, (int)res.sequence);
	printf("agentos:event type=AUDIT tick=%d role=recovery action=rerun_prepare result=DENIED reason=unaffected plan=%s\n",
	       event_tick(), DEMO_PLAN);
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 4201, AGENT_ROLE_RECOVERY,
		"stage=align;run_id=" DEMO_RUN ";project=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_OK, "rerun align");
	printf("agentos:event type=ACTION tick=%d role=recovery stage=align status=OK corr_id=%s plan=%s seq=%d duplicate=0\n",
	       event_tick(), DEMO_ALIGN_CORR, DEMO_PLAN, (int)res.sequence);
	make_op(&op, AGENT_TOOL_RERUN_STAGE, 4201, AGENT_ROLE_RECOVERY,
		"stage=align;run_id=" DEMO_RUN ";project=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_DUPLICATE, "duplicate");
	printf("agentos:event type=AUDIT tick=%d role=recovery action=rerun_align result=DUPLICATE corr_id=%s plan=%s seq=%d\n",
	       event_tick(), DEMO_ALIGN_CORR, DEMO_PLAN, (int)res.sequence);
	make_op(&op, AGENT_TOOL_WRITE_REPORT, 4202, AGENT_ROLE_RECOVERY,
		"stage=report;run_id=" DEMO_RUN ";project=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_OK, "write report");
	printf("agentos:event type=REPORT tick=%d role=recovery project=%s run_id=%s file=RUN-042-recovery.md status=OK corr_id=%s plan=%s seq=%d llm_enhanced=0\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, DEMO_REPORT_CORR,
	       DEMO_PLAN, (int)res.sequence);
	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, DEMO_PROJECT);
	strcpy(query.run_id, DEMO_RUN);
	strcpy(query.status, "ok");
	strcpy(query.kind, "report");
	check(agent_file_query(&query, &result) >= 1, "final query");
	printf("labdemo_ucore: final report_query hits=%d used_index=%d scanned=%d\n",
	       result.total_hits, result.used_index, result.scanned_records);
	printf("agentos:event type=FINAL tick=%d project=%s run_id=%s status=RECOVERED plan=%s\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, DEMO_PLAN);
	exit(0);
}

static void inject_failure(void)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = 3;
	strcpy(meta.physical_name, "lab_RUN042_align_err");
	strcpy(meta.project, DEMO_PROJECT);
	strcpy(meta.workflow, DEMO_WORKFLOW);
	strcpy(meta.run_id, DEMO_RUN);
	strcpy(meta.stage, "align");
	strcpy(meta.kind, "log");
	strcpy(meta.status, "failed");
	strcpy(meta.summary, "memory limit exceeded at align stage");
	meta.dependency_mask = AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
			       AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
	check(agent_file_meta_set(&meta) == 0, "inject failure");
	printf("agentos:event type=INCIDENT_CREATED tick=%d id=%s project=%s workflow=%s run_id=%s stage=align reason=memory_limit\n",
	       event_tick(), DEMO_INCIDENT, DEMO_PROJECT, DEMO_WORKFLOW,
	       DEMO_RUN);
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
	printf("agentos:event type=RUN_OBJECT tick=%d project=%s workflow=%s run_id=%s desired_state=RECOVERED policy=minimal_rerun\n",
	       event_tick(), DEMO_PROJECT, DEMO_WORKFLOW, DEMO_RUN);
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
