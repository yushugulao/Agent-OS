#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DEMO_OBSERVE_PAGE_RECORDS 128
#define DEMO_PROVENANCE_MAX      128

_Static_assert(DEMO_OBSERVE_PAGE_RECORDS <= AGENT_AUDIT_MAX_RECORDS,
	       "demo audit page exceeds the UAPI limit");
_Static_assert(DEMO_OBSERVE_PAGE_RECORDS <= AGENT_TIMELINE_MAX_RECORDS,
	       "demo timeline page exceeds the UAPI limit");
_Static_assert(DEMO_PROVENANCE_MAX <= AGENT_PROVENANCE_MAX_EDGES,
	       "demo provenance page exceeds the UAPI limit");

static int recovery_pid;
static int investigator_pid;
static int ready_fd = -1;
static int start_fd = -1;
static int progress_fd = -1;
/* Snapshot formats are sequential; one scratch avoids duplicate eager copies. */
static union {
	struct agent_audit_record audit[DEMO_OBSERVE_PAGE_RECORDS];
	struct agent_timeline_record timeline[DEMO_OBSERVE_PAGE_RECORDS];
	struct agent_provenance_edge provenance[DEMO_PROVENANCE_MAX];
	struct agent_file_prefetch_hint
		span_prefetch[AGENT_FILE_PREFETCH_SPAN_MAX];
} demo_observe_scratch;
#define demo_audit_records demo_observe_scratch.audit
#define demo_timeline_records demo_observe_scratch.timeline
#define demo_provenance_edges demo_observe_scratch.provenance
#define demo_span_prefetch demo_observe_scratch.span_prefetch
static struct agent_audit_filter demo_audit_filter;
static struct agent_timeline_filter demo_timeline_filter;

#define DEMO_PROJECT "lab-gene-x"
#define DEMO_WORKFLOW "nightly-regression"
#define DEMO_RUN "RUN-042"
#define DEMO_INCIDENT "INC-RUN-042-ALIGN-OOM"
#define DEMO_PLAN "PLAN-RUN-042-RECOVER-1"
#define DEMO_ALIGN_CORR "RUN-042-align-rerun-1"
#define DEMO_REPORT_CORR "RUN-042-report-write-1"
#define DEMO_LLM_REQUEST "LLM-RUN-042-RCA-1"
#define DEMO_ALIGN_LOG "labalignerr"
#define DEMO_ALIGN_LOG_BODY "align memory_limit evidence"

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("labdemo_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int timeline_after_cursor(struct agent_timeline_record *record,
				 uint64 tick, int source, uint64 sequence)
{
	if (record->tick != tick)
		return record->tick > tick;
	if (record->source != source)
		return record->source > source;
	return record->sequence > sequence;
}

static int event_tick(void)
{
	return (int)get_mtime();
}

static uint64 digest_text(const char *text)
{
	uint64 hash = 1469598103934665603ULL;

	while (*text) {
		hash ^= (unsigned char)*text++;
		hash *= 1099511628211ULL;
	}
	return hash;
}

static void check_investigator_digest_observation(uint64 digest_sequence)
{
	int timeline_count;
	int provenance_count;
	int timeline_verified = 0;
	int provenance_verified = 0;

	memset(&demo_timeline_filter, 0, sizeof(demo_timeline_filter));
	demo_timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
				     AGENT_TIMELINE_FILTER_KIND |
				     AGENT_TIMELINE_FILTER_TOOL_ID |
				     AGENT_TIMELINE_FILTER_STATUS;
	demo_timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_CONTEXT;
	demo_timeline_filter.kind = AGENT_AUDIT_KIND_CONTEXT;
	demo_timeline_filter.tool_id = AGENT_TOOL_READ_FILE_DIGEST;
	demo_timeline_filter.status = AGENT_STATUS_OK;
	timeline_count = agent_timeline_query(&demo_timeline_filter,
					      demo_timeline_records,
					      DEMO_OBSERVE_PAGE_RECORDS);
	check(timeline_count >= 1, "investigator timeline digest");
	for (int i = 0; i < timeline_count; i++) {
		struct agent_timeline_record *record = &demo_timeline_records[i];

		check(record->source == AGENT_TIMELINE_SOURCE_CONTEXT,
		      "investigator timeline digest source");
		check(record->kind == AGENT_AUDIT_KIND_CONTEXT,
		      "investigator timeline digest kind");
		check(record->tool_id == AGENT_TOOL_READ_FILE_DIGEST,
		      "investigator timeline digest tool");
		if (record->pid == getpid() &&
		    record->source_pid == getpid() &&
		    record->target_pid == getpid() &&
		    record->sequence == digest_sequence &&
		    record->value0 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    record->value1 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    record->value2 == digest_text(DEMO_ALIGN_LOG_BODY) &&
		    (record->flags & AGENT_CONTEXT_RECORD_F_TRUNCATED) != 0 &&
		    strlen(record->text) == AGENT_CONTEXT_TEXT_SIZE - 1 &&
		    strncmp(DEMO_ALIGN_LOG_BODY, record->text,
			    strlen(record->text)) == 0)
			timeline_verified = 1;
	}
	check(timeline_verified, "investigator timeline digest value");

	provenance_count = agent_provenance_snapshot(demo_provenance_edges,
						      DEMO_PROVENANCE_MAX);
	check(provenance_count >= 1, "investigator digest provenance");
	for (int i = 0; i < provenance_count; i++) {
		struct agent_provenance_edge *edge = &demo_provenance_edges[i];

		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_pid == getpid() &&
		    edge->target_pid == getpid() &&
		    edge->target_sequence == digest_sequence &&
		    edge->tool_id == AGENT_TOOL_READ_FILE_DIGEST &&
		    edge->status == AGENT_STATUS_OK &&
		    edge->value0 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    edge->value1 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    (edge->flags & AGENT_CONTEXT_RECORD_F_TRUNCATED) != 0 &&
		    strlen(edge->text) == AGENT_CONTEXT_TEXT_SIZE - 1 &&
		    strncmp(DEMO_ALIGN_LOG_BODY, edge->text,
			    strlen(edge->text)) == 0)
			provenance_verified = 1;
	}
	check(provenance_verified, "investigator digest provenance value");
	printf("labdemo_ucore: investigator digest_observation timeline=%d provenance=%d verified=1\n",
	       timeline_count, provenance_count);
}

static void write_demo_file(const char *name, const char *body)
{
	int fd;

	fd = open(name, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "demo file open");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "demo file write");
	check(close(fd) == 0, "demo file close");
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
	int n = agent_run(op, res, 1, 0);
	if (n != 1) {
		printf("labdemo_ucore: agent_run failed\n");
		printf("labdemo_ucore: failed check=%s\n", msg);
		printf("labdemo_ucore: failed tool=%d return=%d\n",
		       op->tool_id, n);
	}
	check(n == 1, msg);
	if (res->status != status) {
		printf("labdemo_ucore: result status mismatch\n");
		printf("labdemo_ucore: failed check=%s\n", msg);
		printf("labdemo_ucore: status=%d expected=%d result=%s\n",
		       res->status, status, res->result);
	}
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
	char start;

	if (ready_fd >= 0) {
		check(write(ready_fd, &c, 1) == 1, "ready write");
		check(close(ready_fd) == 0, "ready close");
		ready_fd = -1;
	}
	check(start_fd >= 0, "start barrier descriptor");
	check(read(start_fd, &start, 1) == 1 && start == 'G',
	      "start barrier release");
	check(close(start_fd) == 0, "start barrier close");
	start_fd = -1;
}

static void report_progress(char stage)
{
	check(progress_fd >= 0, "progress descriptor");
	check(write(progress_fd, &stage, 1) == 1, "progress receipt");
	check(close(progress_fd) == 0, "progress close");
	progress_fd = -1;
}

static void run_sentinel(void)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_file_prefetch_hint
		hints[AGENT_FILE_PREFETCH_MAX_HINTS];
	int hint_count;
	int matched = 0;

	created("sentinel");
	check(agent_heartbeat(5) == 0, "heartbeat");
	check(agent_watch(AGENT_EVENT_FILE_STATUS, "status=failed") == 0,
	      "watch failed");
	ready('S');
	printf("agentos:event type=WATCH_REGISTERED tick=%d role=sentinel event=FILE_STATUS filter=status=failed\n",
	       event_tick());
	printf("agentos:event type=AGENT_STATE tick=%d role=sentinel state=WAITING\n",
	       event_tick());
	for (int i = 0; i < 64; i++) {
		check(agent_wait(&event, 300) == AGENT_STATUS_OK,
		      "sentinel wait");
		if (event.type == AGENT_EVENT_FILE_STATUS) {
			matched = 1;
			break;
		}
		check(event.type == AGENT_EVENT_TIMER &&
			      strcmp(event.payload, "timer=heartbeat") == 0,
		      "sentinel intrinsic heartbeat");
	}
	check(matched, "sentinel failed file event");
	check(agent_heartbeat_stop() == 0, "sentinel heartbeat stop");
	printf("labdemo_ucore: sentinel event payload=%s\n", event.payload);
	printf("agentos:event type=AGENT_STATE tick=%d role=sentinel state=RUNNING event_id=%d corr_id=%d\n",
	       event_tick(), (int)event.event_id, (int)event.corr_id);

	make_op(&op, AGENT_TOOL_QUERY_FILE, 1001, 0,
		"project=" DEMO_PROJECT ";run_id=" DEMO_RUN ";status=failed");
	run_one(&op, &res, AGENT_STATUS_OK, "query failed files");
	printf("agentos:event type=TOOL_CALL tick=%d role=sentinel tool=query_file project=%s run_id=%s status=failed hits=%d used_index=%d seq=%d\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, (int)res.value0,
	       (int)(res.value2 & 1), (int)res.sequence);
	hint_count = agent_file_prefetch_snapshot(hints,
						  AGENT_FILE_PREFETCH_MAX_HINTS);
	check(hint_count >= 1, "prefetch hints");
	check((hints[0].reason & AGENT_FILE_PREFETCH_REASON_DEPENDENCY) != 0,
	      "prefetch dependency");
	check(hints[0].hit.stage[0] != 0, "prefetch stage");
	printf("labdemo_ucore: sentinel prefetch_hint stage=%s source_seq=%d plan=%d candidates=%d\n",
	       hints[0].hit.stage, (int)hints[0].source_sequence,
	       hints[0].plan, hints[0].candidate_records);
	printf("agentos:event type=PREFETCH_HINT tick=%d role=sentinel project=%s run_id=%s source_stage=align next_stage=%s source_seq=%d candidates=%d reason=%d\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, hints[0].hit.stage,
	       (int)hints[0].source_sequence, hints[0].candidate_records,
	       (int)hints[0].reason);

	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 1002,
		AGENT_ROLE_SENTINEL, "action_commit");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel denied");
	printf("agentos:event type=AUDIT tick=%d role=sentinel action=action_commit result=DENIED reason=capability corr_id=%s seq=%d\n",
	       event_tick(), DEMO_ALIGN_CORR, (int)res.sequence);

	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 1003, investigator_pid,
		"investigate " DEMO_RUN " align");
	run_one(&op, &res, AGENT_STATUS_OK, "message investigator");
	printf("agentos:event type=MESSAGE tick=%d from=sentinel to=investigator status=OK corr_id=MSG-%s-S-I prefetch_handoff=%s seq=%d\n",
	       event_tick(), DEMO_RUN, hints[0].hit.stage,
	       (int)res.sequence);
	report_progress('S');
	exit(0);
}

static void run_investigator(void)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_context_header header;
	static struct agent_context_record records[8];
	static struct agent_file_prefetch_hint
		hints[AGENT_FILE_PREFETCH_MAX_HINTS];
	int n;
	int summary_seq;
	int digest_seq;
	int dependency_seq;
	int prefetch_seq;
	int hint_count;
	int span_count;
	int span_trace_count;
	int span_found = 0;
	int span_source = 0;
	int span_target = 0;
	int span_trace_context = 0;
	int span_trace_event = 0;
	int span_trace_prefetch = 0;
	char prefetch_stage[AGENT_FILE_FIELD_SIZE];

	created("investigator");
	check(agent_watch(AGENT_EVENT_MESSAGE, "investigate") == 0,
	      "watch message");
	ready('I');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK,
	      "investigator wait");
	check(event.type == AGENT_EVENT_MESSAGE,
	      "investigator message type");
	check(event.corr_id == 1003, "investigator message correlation");
	check(strncmp(event.payload, "investigate " DEMO_RUN " align",
		      strlen("investigate " DEMO_RUN " align")) == 0,
	      "investigator message payload");
	hint_count = agent_file_prefetch_snapshot(hints,
						  AGENT_FILE_PREFETCH_MAX_HINTS);
	check(hint_count >= 1, "handoff prefetch hints");
	check((hints[0].reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0,
	      "handoff prefetch reason");
	check((hints[0].reason & AGENT_FILE_PREFETCH_REASON_DEPENDENCY) != 0,
	      "handoff dependency reason");
	memset(prefetch_stage, 0, sizeof(prefetch_stage));
	strcpy(prefetch_stage, hints[0].hit.stage);
	check(prefetch_stage[0] != 0, "investigator prefetch stage");
	printf("labdemo_ucore: investigator handoff_prefetch stage=%s source_seq=%d reason=%d\n",
	       prefetch_stage, (int)hints[0].source_sequence,
	       (int)hints[0].reason);
	span_count = agent_file_prefetch_span_snapshot(
		demo_span_prefetch, AGENT_FILE_PREFETCH_SPAN_MAX);
	check(span_count >= 1, "span prefetch hints");
	for (int i = 0; i < span_count; i++) {
		if (strcmp(demo_span_prefetch[i].hit.stage, prefetch_stage) == 0 &&
		    demo_span_prefetch[i].target_pid == getpid() &&
		    demo_span_prefetch[i].source_pid != getpid() &&
		    (demo_span_prefetch[i].reason &
		     AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0 &&
		    (demo_span_prefetch[i].reason &
		     AGENT_FILE_PREFETCH_REASON_SPAN_BUS) != 0) {
			span_found = 1;
			span_source = demo_span_prefetch[i].source_pid;
			span_target = demo_span_prefetch[i].target_pid;
			break;
		}
	}
	check(span_found, "span prefetch handoff");
	printf("labdemo_ucore: investigator span_prefetch stage=%s count=%d source_pid=%d target_pid=%d\n",
	       prefetch_stage, span_count, span_source, span_target);
	span_trace_count = agent_span_trace_snapshot(
		demo_audit_records, DEMO_OBSERVE_PAGE_RECORDS);
	check(span_trace_count >= 1, "investigator span trace");
	for (int i = 0; i < span_trace_count; i++) {
		check(demo_audit_records[i].span_id == event.span_id,
		      "span trace id");
		if (demo_audit_records[i].kind == AGENT_AUDIT_KIND_CONTEXT)
			span_trace_context = 1;
		if (demo_audit_records[i].kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
		    demo_audit_records[i].kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			span_trace_event = 1;
		if (demo_audit_records[i].kind == AGENT_AUDIT_KIND_PREFETCH)
			span_trace_prefetch = 1;
	}
	check(span_trace_context, "span trace context");
	check(span_trace_event, "span trace event");
	check(span_trace_prefetch, "span trace prefetch");
	printf("labdemo_ucore: investigator span_trace records=%d context=%d event=%d prefetch=%d\n",
	       span_trace_count, span_trace_context, span_trace_event,
	       span_trace_prefetch);
	make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2001, 0, "align");
	run_one(&op, &res, AGENT_STATUS_OK, "read summary");
	summary_seq = res.sequence;
	printf("labdemo_ucore: investigator reason=%s\n", res.result);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=read_file_summary stage=align status=OK seq=%d\n",
	       event_tick(), summary_seq);
	make_op(&op, AGENT_TOOL_READ_FILE_DIGEST, 2005, 0,
		"project=" DEMO_PROJECT ";run_id=" DEMO_RUN
		";stage=align;status=failed");
	run_one(&op, &res, AGENT_STATUS_OK, "read digest");
	check(res.value0 == strlen(DEMO_ALIGN_LOG_BODY), "digest size");
	check(res.value1 == strlen(DEMO_ALIGN_LOG_BODY), "digest bytes");
	check(res.value2 == digest_text(DEMO_ALIGN_LOG_BODY), "digest hash");
	check(strcmp(res.result, DEMO_ALIGN_LOG_BODY) == 0,
	      "digest preview");
	digest_seq = res.sequence;
	printf("labdemo_ucore: investigator digest bytes=%d preview=%s seq=%d\n",
	       (int)res.value1, res.result, digest_seq);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=read_file_digest stage=align status=OK bytes=%d seq=%d\n",
	       event_tick(), (int)res.value1, digest_seq);
	check_investigator_digest_observation(digest_seq);
	make_op(&op, AGENT_TOOL_DEPENDENCY_QUERY, 2002, 0, "label=align");
	run_one(&op, &res, AGENT_STATUS_OK, "dependency");
	dependency_seq = res.sequence;
	printf("labdemo_ucore: affected labels=%s\n", res.result);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=dependency_query label=align impact=%s seq=%d\n",
	       event_tick(), res.result, dependency_seq);
	make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2004, 0, prefetch_stage);
	run_one(&op, &res, AGENT_STATUS_OK, "prefetch summary");
	prefetch_seq = res.sequence;
	printf("labdemo_ucore: investigator prefetch_summary stage=%s result=%s\n",
	       prefetch_stage, res.result);
	printf("agentos:event type=PREFETCH_USED tick=%d role=investigator stage=%s summary=%s seq=%d\n",
	       event_tick(), prefetch_stage, res.result, prefetch_seq);
	printf("agentos:event type=LLM_CALL tick=%d mode=template task=explain_root_cause llm_request_id=%s project=%s run_id=%s refs=%d,%d,%d,%d status=OK\n",
	       event_tick(), DEMO_LLM_REQUEST, DEMO_PROJECT, DEMO_RUN,
	       summary_seq, digest_seq, dependency_seq, prefetch_seq);
	printf("agentos:event type=LLM_RESULT tick=%d mode=template llm_request_id=%s llm_status=OK llm_explanation=memory_limit referenced_sequences=%d,%d,%d,%d confidence=medium\n",
	       event_tick(), DEMO_LLM_REQUEST, summary_seq, digest_seq,
	       dependency_seq, prefetch_seq);
	printf("agentos:event type=PLAN_CREATED tick=%d role=investigator plan=%s project=%s run_id=%s actions=align,analyze,report skip=prepare prefetch=%s refs=%d,%d,%d,%d\n",
	       event_tick(), DEMO_PLAN, DEMO_PROJECT, DEMO_RUN,
	       prefetch_stage, summary_seq, digest_seq, dependency_seq,
	       prefetch_seq);
	n = context_snapshot(&header, records, 8);
	check(n >= 1, "investigator context");
	printf("agentos:event type=CONTEXT_SNAPSHOT tick=%d role=investigator records=%d latest=%d\n",
	       event_tick(), n, (int)header.latest_sequence);
	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 2003, recovery_pid,
		"recover " DEMO_RUN " align plan=" DEMO_PLAN);
	run_one(&op, &res, AGENT_STATUS_OK, "message recovery");
	printf("agentos:event type=MESSAGE tick=%d from=investigator to=recovery status=OK corr_id=MSG-%s-I-R plan=%s seq=%d\n",
	       event_tick(), DEMO_RUN, DEMO_PLAN, (int)res.sequence);
	report_progress('I');
	exit(0);
}

static void run_recovery(void)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_file_query query;
	static struct agent_file_query_result result;

	created("recovery");
	check(agent_watch(AGENT_EVENT_MESSAGE, "recover") == 0, "watch recover");
	ready('R');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK, "recovery wait");
	check(event.type == AGENT_EVENT_MESSAGE, "recovery message type");
	check(event.corr_id == 2003, "recovery message correlation");
	check(strncmp(event.payload, "recover " DEMO_RUN " align plan=" DEMO_PLAN,
		      strlen("recover " DEMO_RUN " align plan=" DEMO_PLAN)) == 0,
	      "recovery message payload");
	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 3001,
		AGENT_ROLE_RECOVERY, "action_commit");
	run_one(&op, &res, AGENT_STATUS_OK, "capability");
	printf("agentos:event type=AUDIT tick=%d role=recovery action=action_commit result=ALLOW plan=%s seq=%d\n",
	       event_tick(), DEMO_PLAN, (int)res.sequence);
	printf("agentos:event type=AUDIT tick=%d role=recovery action=commit_prepare result=DENIED reason=unaffected plan=%s\n",
	       event_tick(), DEMO_PLAN);
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 4201, AGENT_ROLE_RECOVERY,
		"label=align;run_id=" DEMO_RUN ";namespace=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_OK, "commit align");
	printf("agentos:event type=ACTION tick=%d role=recovery label=align status=OK corr_id=%s plan=%s seq=%d duplicate=0\n",
	       event_tick(), DEMO_ALIGN_CORR, DEMO_PLAN, (int)res.sequence);
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 4201, AGENT_ROLE_RECOVERY,
		"label=align;run_id=" DEMO_RUN ";namespace=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_DUPLICATE, "duplicate");
	printf("agentos:event type=AUDIT tick=%d role=recovery action=commit_align result=DUPLICATE corr_id=%s plan=%s seq=%d\n",
	       event_tick(), DEMO_ALIGN_CORR, DEMO_PLAN, (int)res.sequence);
	make_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 4202, AGENT_ROLE_RECOVERY,
		"label=report;run_id=" DEMO_RUN ";namespace=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_OK, "update artifact");
	printf("agentos:event type=ARTIFACT tick=%d role=recovery namespace=%s run_id=%s file=RUN-042-recovery.md status=OK corr_id=%s plan=%s seq=%d llm_enhanced=0\n",
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

static void set_demo_meta(int fid, const char *physical, const char *stage,
			  const char *kind, const char *status,
			  const char *summary, uint64 deps)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.logical_path, physical);
	strcpy(meta.project, DEMO_PROJECT);
	strcpy(meta.workflow, DEMO_WORKFLOW);
	strcpy(meta.run_id, DEMO_RUN);
	strcpy(meta.stage, stage);
	strcpy(meta.kind, kind);
	strcpy(meta.status, status);
	strcpy(meta.summary, summary);
	meta.dependency_mask = deps;
	meta.flags = AGENT_FILE_META_F_PERSIST;
	check(agent_file_meta_set(&meta) == 0, "demo meta set");
}

static void seed_demo_metadata(void)
{
	set_demo_meta(1, "r42align", "align", "artifact", "ok",
		      "align output is ready before injected failure",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive"));
	set_demo_meta(2, "r42anlz", "analyze", "status", "pending",
		      "analysis waits for align",
		      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive"));
	set_demo_meta(3, "r42report", "report", "report", "pending",
		      "report waits for analyze",
		      agent_dependency_label_bit("archive"));
	set_demo_meta(4, "r42archive", "archive", "artifact", "pending",
		      "archive waits for report", 0);
}

static void inject_failure(void)
{
	static struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = 5;
	write_demo_file(DEMO_ALIGN_LOG, DEMO_ALIGN_LOG_BODY);
	strcpy(meta.physical_name, DEMO_ALIGN_LOG);
	strcpy(meta.project, DEMO_PROJECT);
	strcpy(meta.workflow, DEMO_WORKFLOW);
	strcpy(meta.run_id, DEMO_RUN);
	strcpy(meta.stage, "align");
	strcpy(meta.kind, "log");
	strcpy(meta.status, "running");
	strcpy(meta.summary, "align stage running before failure");
	meta.dependency_mask = agent_dependency_label_bit("analyze") |
			       agent_dependency_label_bit("report") |
			       agent_dependency_label_bit("archive");
	check(agent_file_meta_set(&meta) == 0, "stage failure transition");
	meta.update_mask = AGENT_FILE_META_UPDATE_STATUS |
			   AGENT_FILE_META_UPDATE_SUMMARY;
	strcpy(meta.status, "failed");
	strcpy(meta.summary, "memory limit exceeded at align stage");
	check(agent_file_meta_set(&meta) == 0, "inject failure");
	printf("agentos:event type=INCIDENT_CREATED tick=%d id=%s project=%s workflow=%s run_id=%s stage=align reason=memory_limit\n",
	       event_tick(), DEMO_INCIDENT, DEMO_PROJECT, DEMO_WORKFLOW,
	       DEMO_RUN);
}

static void check_global_audit(int sentinel_pid)
{
	int n;
	int snapshot_count;
	int has_context = 0;
	int has_enqueue = 0;
	int has_consume = 0;
	int has_sched = 0;
	int has_prefetch = 0;
	int has_sentinel = 0;
	int has_investigator = 0;
	int has_recovery = 0;
	int context_query;
	int span_query;
	int event_query;
	int prefetch_query;
	int start_query;
	uint64 last_sequence = 0;
	uint64 latest_sequence;
	uint64 query_span = 0;

	n = agent_audit_snapshot(demo_audit_records,
				 DEMO_OBSERVE_PAGE_RECORDS);
	check(n > 0, "audit count");
	check(n <= DEMO_OBSERVE_PAGE_RECORDS, "audit page cap");
	snapshot_count = n;
	for (int i = 0; i < n; i++) {
		struct agent_audit_record *r = &demo_audit_records[i];
		check(r->sequence > last_sequence, "audit sequence order");
		last_sequence = r->sequence;
		if (query_span == 0 && r->span_id != 0)
			query_span = r->span_id;
		if (r->pid == sentinel_pid || r->target_pid == sentinel_pid)
			has_sentinel = 1;
		if (r->pid == investigator_pid || r->target_pid == investigator_pid)
			has_investigator = 1;
		if (r->pid == recovery_pid || r->target_pid == recovery_pid)
			has_recovery = 1;
		if (r->kind == AGENT_AUDIT_KIND_CONTEXT)
			has_context = 1;
		if (r->kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE &&
		    r->event_type == AGENT_EVENT_FILE_STATUS &&
		    r->target_pid == sentinel_pid)
			has_enqueue = 1;
		if (r->kind == AGENT_AUDIT_KIND_EVENT_CONSUME &&
		    r->event_type == AGENT_EVENT_FILE_STATUS &&
		    r->pid == sentinel_pid)
			has_consume = 1;
		if (r->kind == AGENT_AUDIT_KIND_SCHED &&
		    (r->pid == sentinel_pid || r->pid == investigator_pid ||
		     r->pid == recovery_pid))
			has_sched = 1;
		if (r->kind == AGENT_AUDIT_KIND_PREFETCH &&
		    r->source_pid == sentinel_pid &&
		    r->target_pid == investigator_pid &&
		    r->event_type == AGENT_EVENT_MESSAGE &&
		    (r->flags & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0)
			has_prefetch = 1;
	}
	check(has_context, "audit context");
	check(has_enqueue, "audit event enqueue");
	check(has_consume, "audit event consume");
	check(has_sched, "audit sched");
	check(has_prefetch, "audit prefetch handoff");
	check(has_sentinel && has_investigator && has_recovery,
	      "audit multi agent");
	latest_sequence = last_sequence;

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_KIND;
	demo_audit_filter.kind = AGENT_AUDIT_KIND_CONTEXT;
	context_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
					  DEMO_OBSERVE_PAGE_RECORDS);
	check(context_query > 0, "audit query context");
	for (int i = 0; i < context_query; i++)
		check(demo_audit_records[i].kind == AGENT_AUDIT_KIND_CONTEXT,
		      "audit query context kind");

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_SPAN_ID;
	demo_audit_filter.span_id = query_span;
	span_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
				       DEMO_OBSERVE_PAGE_RECORDS);
	check(query_span != 0 && span_query > 0, "audit query span");
	for (int i = 0; i < span_query; i++)
		check(demo_audit_records[i].span_id == query_span,
		      "audit query span id");

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags =
		AGENT_AUDIT_FILTER_TARGET_PID | AGENT_AUDIT_FILTER_EVENT_TYPE;
	demo_audit_filter.target_pid = sentinel_pid;
	demo_audit_filter.event_type = AGENT_EVENT_FILE_STATUS;
	event_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
					DEMO_OBSERVE_PAGE_RECORDS);
	check(event_query >= 2, "audit query event");
	for (int i = 0; i < event_query; i++) {
		check(demo_audit_records[i].target_pid == sentinel_pid,
		      "audit query event target");
		check(demo_audit_records[i].event_type ==
			      AGENT_EVENT_FILE_STATUS,
		      "audit query event type");
	}

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_KIND |
				  AGENT_AUDIT_FILTER_SOURCE_PID |
				  AGENT_AUDIT_FILTER_TARGET_PID;
	demo_audit_filter.kind = AGENT_AUDIT_KIND_PREFETCH;
	demo_audit_filter.source_pid = sentinel_pid;
	demo_audit_filter.target_pid = investigator_pid;
	prefetch_query = agent_audit_query(&demo_audit_filter,
					   demo_audit_records,
					   DEMO_OBSERVE_PAGE_RECORDS);
	check(prefetch_query >= 1, "audit query prefetch");
	for (int i = 0; i < prefetch_query; i++) {
		check(demo_audit_records[i].kind == AGENT_AUDIT_KIND_PREFETCH,
		      "audit query prefetch kind");
		check(demo_audit_records[i].source_pid == sentinel_pid,
		      "audit query prefetch source");
		check(demo_audit_records[i].target_pid == investigator_pid,
		      "audit query prefetch target");
		check((demo_audit_records[i].flags &
		       AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0,
		      "audit query prefetch handoff");
	}

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_START_SEQUENCE;
	demo_audit_filter.start_sequence = latest_sequence;
	start_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
					DEMO_OBSERVE_PAGE_RECORDS);
	check(start_query >= 1, "audit query start");
	for (int i = 0; i < start_query; i++)
		check(demo_audit_records[i].sequence >= latest_sequence,
		      "audit query start sequence");

	printf("labdemo_ucore: global_audit=1 records=%d agents=3 context=%d event=%d sched=%d prefetch=%d\n",
	       snapshot_count, has_context, has_enqueue && has_consume,
	       has_sched, has_prefetch);
	printf("labdemo_ucore: audit_query=1 kind=%d span=%d event=%d prefetch=%d start=%d\n",
	       context_query, span_query, event_query, prefetch_query,
	       start_query);
}

static void check_unified_timeline(int sentinel_pid)
{
	int n;
	int filtered;
	int cursor_filtered;
	int has_context = 0;
	int has_event = 0;
	int has_sched = 0;
	int has_prefetch = 0;
	uint64 last_tick = 0;
	uint64 cursor_tick;
	uint64 cursor_sequence;
	int cursor_source;

	n = agent_timeline_snapshot(demo_timeline_records,
				    DEMO_OBSERVE_PAGE_RECORDS);
	check(n > 0, "timeline count");
	for (int i = 0; i < n; i++) {
		struct agent_timeline_record *r = &demo_timeline_records[i];

		check(r->tick >= last_tick, "timeline order");
		last_tick = r->tick;
		if (r->source != AGENT_TIMELINE_SOURCE_AUDIT)
			continue;
		if (r->kind == AGENT_AUDIT_KIND_CONTEXT)
			has_context = 1;
		if (r->kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
		    r->kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			has_event = 1;
		if (r->kind == AGENT_AUDIT_KIND_SCHED)
			has_sched = 1;
		if (r->kind == AGENT_AUDIT_KIND_PREFETCH &&
		    (r->flags & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0)
			has_prefetch = 1;
	}
	check(has_context, "timeline audit context");
	check(has_event, "timeline audit event");
	check(has_sched, "timeline audit sched");
	check(has_prefetch, "timeline audit prefetch");
	cursor_tick = demo_timeline_records[n / 2].tick;
	cursor_source = demo_timeline_records[n / 2].source;
	cursor_sequence = demo_timeline_records[n / 2].sequence;
	memset(&demo_timeline_filter, 0, sizeof(demo_timeline_filter));
	demo_timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
				     AGENT_TIMELINE_FILTER_KIND |
				     AGENT_TIMELINE_FILTER_SOURCE_PID |
				     AGENT_TIMELINE_FILTER_TARGET_PID |
				     AGENT_TIMELINE_FILTER_FLAGS_ALL;
	demo_timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	demo_timeline_filter.kind = AGENT_AUDIT_KIND_PREFETCH;
	demo_timeline_filter.source_pid = sentinel_pid;
	demo_timeline_filter.target_pid = investigator_pid;
	demo_timeline_filter.require_flags =
		AGENT_FILE_PREFETCH_REASON_HANDOFF;
	filtered = agent_timeline_query(&demo_timeline_filter,
					demo_timeline_records,
					DEMO_OBSERVE_PAGE_RECORDS);
	check(filtered >= 1, "timeline query prefetch");
	for (int i = 0; i < filtered; i++) {
		struct agent_timeline_record *r = &demo_timeline_records[i];

		check(r->source == AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline query source");
		check(r->kind == AGENT_AUDIT_KIND_PREFETCH,
		      "timeline query kind");
		check(r->source_pid == sentinel_pid,
		      "timeline query source pid");
		check(r->target_pid == investigator_pid,
		      "timeline query target pid");
		check((r->flags & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0,
		      "timeline query flags");
	}
	memset(&demo_timeline_filter, 0, sizeof(demo_timeline_filter));
	demo_timeline_filter.flags = AGENT_TIMELINE_FILTER_AFTER_CURSOR;
	demo_timeline_filter.after_tick = cursor_tick;
	demo_timeline_filter.after_source = cursor_source;
	demo_timeline_filter.after_sequence = cursor_sequence;
	cursor_filtered = agent_timeline_query(&demo_timeline_filter,
					       demo_timeline_records,
					       DEMO_OBSERVE_PAGE_RECORDS);
	check(cursor_filtered > 0, "timeline query cursor");
	for (int i = 0; i < cursor_filtered; i++)
		check(timeline_after_cursor(&demo_timeline_records[i],
					    cursor_tick, cursor_source,
					    cursor_sequence),
		      "timeline query cursor order");
	printf("labdemo_ucore: unified_timeline records=%d context=%d event=%d sched=%d prefetch=%d\n",
	       n, has_context, has_event, has_sched, has_prefetch);
	printf("labdemo_ucore: timeline_query prefetch=%d cursor=%d\n",
	       filtered, cursor_filtered);
}

static void check_provenance_graph(int sentinel_pid)
{
	int n;
	int has_message = 0;
	int has_prefetch = 0;

	n = agent_provenance_snapshot(demo_provenance_edges,
				      DEMO_PROVENANCE_MAX);
	check(n > 0, "provenance graph");
	for (int i = 0; i < n; i++) {
		struct agent_provenance_edge *edge =
			&demo_provenance_edges[i];

		if (edge->kind != AGENT_PROVENANCE_EDGE_AUDIT)
			continue;
		if (edge->source_pid == sentinel_pid &&
		    edge->target_pid == investigator_pid) {
			if (edge->event_type == AGENT_EVENT_MESSAGE)
				has_message = 1;
			if (edge->tool_id == AGENT_TOOL_QUERY_FILE &&
			    (edge->flags &
			     AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0)
				has_prefetch = 1;
		}
	}
	check(has_message, "provenance message");
	check(has_prefetch, "provenance prefetch");
	printf("labdemo_ucore: provenance_graph edges=%d message=%d prefetch=%d\n",
	       n, has_message, has_prefetch);
}

static void run_orchestrator(void)
{
	int sentinel_pid;
	int ready_pipe[2];
	int recovery_start[2];
	int investigator_start[2];
	int sentinel_start[2];
	int progress_pipe[2];
	int status = 0;
	int ok = 0;
	int ready_count = 0;
	char ch;

	created("orchestrator");
	printf("agentos:event type=RUN_OBJECT tick=%d project=%s workflow=%s run_id=%s desired_state=RECOVERED policy=minimal_rerun\n",
	       event_tick(), DEMO_PROJECT, DEMO_WORKFLOW, DEMO_RUN);
	check(agent_file_meta_init() == 0, "meta init");
	seed_demo_metadata();
	check(pipe(ready_pipe) == 0, "pipe");
	check(pipe(recovery_start) == 0, "recovery start pipe");
	check(pipe(investigator_start) == 0, "investigator start pipe");
	check(pipe(sentinel_start) == 0, "sentinel start pipe");
	check(pipe(progress_pipe) == 0, "progress pipe");
	ready_fd = ready_pipe[1];
	start_fd = recovery_start[0];
	progress_fd = -1;
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate recovery ready pipe");
	check(agent_scope_delegate_fd(recovery_start[0]) == AGENT_STATUS_OK,
	      "delegate recovery start pipe");
	recovery_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(recovery_pid >= 0, "create recovery");
	if (recovery_pid == 0)
		run_recovery();
	start_fd = investigator_start[0];
	progress_fd = progress_pipe[1];
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate investigator ready pipe");
	check(agent_scope_delegate_fd(investigator_start[0]) == AGENT_STATUS_OK,
	      "delegate investigator start pipe");
	check(agent_scope_delegate_fd(progress_pipe[1]) == AGENT_STATUS_OK,
	      "delegate investigator progress pipe");
	investigator_pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
	check(investigator_pid >= 0, "create investigator");
	if (investigator_pid == 0)
		run_investigator();
	start_fd = sentinel_start[0];
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate sentinel ready pipe");
	check(agent_scope_delegate_fd(sentinel_start[0]) == AGENT_STATUS_OK,
	      "delegate sentinel start pipe");
	check(agent_scope_delegate_fd(progress_pipe[1]) == AGENT_STATUS_OK,
	      "delegate sentinel progress pipe");
	sentinel_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(sentinel_pid >= 0, "create sentinel");
	if (sentinel_pid == 0)
		run_sentinel();
	check(close(ready_pipe[1]) == 0, "close ready send pipe");
	ready_fd = -1;
	check(close(recovery_start[0]) == 0,
	      "close recovery start receive pipe");
	check(close(investigator_start[0]) == 0,
	      "close investigator start receive pipe");
	check(close(sentinel_start[0]) == 0,
	      "close sentinel start receive pipe");
	start_fd = -1;
	check(close(progress_pipe[1]) == 0, "close progress send pipe");
	progress_fd = -1;
	while (ready_count < 3) {
		check(read(ready_pipe[0], &ch, 1) == 1, "ready read");
		ready_count++;
	}
	check(agent_route_config(sentinel_pid, investigator_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant sentinel investigator route");
	check(agent_route_config(investigator_pid, recovery_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant investigator recovery route");
	inject_failure();
	check(write(sentinel_start[1], "G", 1) == 1,
	      "release sentinel start barrier");
	check(close(sentinel_start[1]) == 0, "close sentinel start pipe");
	check(read(progress_pipe[0], &ch, 1) == 1 && ch == 'S',
	      "sentinel event queue receipt");
	check(write(investigator_start[1], "G", 1) == 1,
	      "release investigator start barrier");
	check(close(investigator_start[1]) == 0,
	      "close investigator start pipe");
	check(read(progress_pipe[0], &ch, 1) == 1 && ch == 'I',
	      "investigator message queue receipt");
	check(write(recovery_start[1], "G", 1) == 1,
	      "release recovery start barrier");
	check(close(recovery_start[1]) == 0, "close recovery start pipe");
	check(close(progress_pipe[0]) == 0, "close progress receive pipe");
	check(close(ready_pipe[0]) == 0, "close ready receive pipe");
	printf("labdemo_ucore: startup_barrier ready=3 event_queued=1 released=3\n");
	while (wait(&status) > 0) {
		check(status == 0, "child status");
		ok++;
	}
	check(ok == 3, "three agents");
	check_global_audit(sentinel_pid);
	check_unified_timeline(sentinel_pid);
	check_provenance_graph(sentinel_pid);
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
