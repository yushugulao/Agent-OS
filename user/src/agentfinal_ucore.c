#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define FINAL_PROVENANCE_MAX 256

static struct agent_op ops[AGENT_BATCH_MAX];
static struct agent_result results[AGENT_BATCH_MAX];
static struct agent_context_record records[AGENT_CONTEXT_MAX_RECORDS];
static struct agent_trace_record trace_records[AGENT_TRACE_MAX_RECORDS];
static struct agent_audit_record span_records[AGENT_AUDIT_MAX_RECORDS];
static struct agent_timeline_record timeline_records[AGENT_TIMELINE_MAX_RECORDS];
static struct agent_timeline_filter timeline_filter;
static struct agent_provenance_edge
	provenance_edges[FINAL_PROVENANCE_MAX];
static struct agent_ledger_summary final_ledger;
static struct agent_info final_info;
static struct agent_context_header final_header;
static struct agent_context_detail final_detail;
static struct agent_context_record final_manual;
static struct agent_file_query final_query;
static struct agent_file_query_result final_query_result;
static struct agent_file_prefetch_hint
	final_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
static struct agent_event final_event;
static struct agent_request final_req;
static struct agent_response final_resp;
static volatile int context_lane_slow_ready;
static volatile int context_lane_slow_done;
static struct agent_result context_lane_slow_result;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentfinal_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int text_contains(const char *text, const char *needle)
{
	int n = strlen(needle);

	if (n == 0)
		return 1;
	for (int i = 0; text[i]; i++)
		if (strncmp(text + i, needle, n) == 0)
			return 1;
	return 0;
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
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, "RUN-042");
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
			      agent_dependency_label_bit("report"));
	set_demo_meta(2, "r42anlz", "analyze", "status", "pending",
		      "analysis waits for align",
		      agent_dependency_label_bit("report"));
	set_demo_meta(3, "r42report", "report", "report", "pending",
		      "report waits for analyze", 0);
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

static void make_echo(struct agent_op *op, uint64 id, const char *text)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = id;
	op->arg0 = id;
	op->arg1 = id + 1;
	strcpy(op->payload, text);
}

static void context_lane_slow_worker(void *unused)
{
	struct agent_op op;
	struct agent_result result;

	(void)unused;
	make_echo(&op, 7201, "context-lane-ready");
	if (agent_run(&op, &result, 1, 0) != 1 ||
	    result.status != AGENT_STATUS_OK)
		exit(71);
	__sync_synchronize();
	context_lane_slow_ready = 1;
	__sync_synchronize();
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_RERUN_STAGE;
	op.request_id = 7202;
	strcpy(op.payload, "align");
	if (agent_run(&op, &result, 1, 0) != 1 ||
	    result.status != AGENT_STATUS_OK)
		exit(72);
	context_lane_slow_result = result;
	__sync_synchronize();
	context_lane_slow_done = 1;
	__sync_synchronize();
	exit(0);
}

static void check_context_commit_lane(void)
{
	int tid;
	int status;
	int n;

	check(agent_file_meta_init() == 0, "context lane meta init");
	seed_demo_metadata();
	check(context_clear() == 0, "context lane clear");
	context_lane_slow_ready = 0;
	context_lane_slow_done = 0;
	memset(&context_lane_slow_result, 0,
	       sizeof(context_lane_slow_result));
	tid = thread_create(context_lane_slow_worker, 0);
	check(tid >= 0, "context lane slow thread");
	while (!context_lane_slow_ready)
		sched_yield();
	for (;;) {
		check(agent_info(&final_info) == 0, "context lane progress");
		if (final_info.agent_call_count >= 2)
			break;
		check(!context_lane_slow_done, "context lane overlap");
		sched_yield();
	}
	check(!context_lane_slow_done, "context lane slow path yielded");
	make_echo(&ops[0], 7203, "context-lane-fast");
	check(agent_run(&ops[0], &results[0], 1, 0) == 1,
	      "context lane fast run");
	status = waittid(tid);
	check(status == 0, "context lane slow join");
	check(context_lane_slow_done, "context lane slow completed");
	check(context_lane_slow_result.sequence == 2,
	      "context lane slow sequence");
	check(results[0].sequence == 3, "context lane fast sequence");
	n = context_snapshot(&final_header, records,
			     AGENT_CONTEXT_MAX_RECORDS);
	check(n == 3, "context lane snapshot count");
	check(final_header.oldest_sequence == 1,
	      "context lane oldest");
	check(final_header.latest_sequence == 3,
	      "context lane latest");
	for (int i = 0; i < n; i++) {
		check(records[i].sequence == (uint64)i + 1,
		      "context lane monotonic sequence");
		check(records[i].record_hash != 0,
		      "context lane record hash");
		if (i > 0)
			check(records[i].prev_hash ==
				      records[i - 1].record_hash,
			      "context lane hash chain");
	}
	check(final_header.latest_record_hash ==
		      records[n - 1].record_hash,
	      "context lane header hash");
	printf("agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1\n");
}

static void check_legacy_name_protocol(void)
{
	struct agent_request *req = &final_req;
	struct agent_response *resp = &final_resp;

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7101;
	strcpy(req->tool_name, "echo");
	strcpy(req->payload_key, "payload");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->arg0_key, "arg0");
	req->arg0_type = AGENT_PARAM_UINT64;
	strcpy(req->arg1_key, "arg1");
	req->arg1_type = AGENT_PARAM_UINT64;
	req->arg0 = 21;
	req->arg1 = 22;
	strcpy(req->payload, "legacy-name");
	check(agent_call(req, resp) == 0, "legacy name echo");
	check(resp->status == AGENT_STATUS_OK, "legacy echo status");
	check(strcmp(resp->result, "legacy-name") == 0, "legacy echo text");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7102;
	strcpy(req->tool_name, "pid_info");
	check(agent_call(req, resp) == 0, "legacy name pid");
	check(resp->status == AGENT_STATUS_OK, "legacy pid status");
	check(resp->value2 == 1, "legacy pid agent");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7103;
	strcpy(req->tool_name, "query_file");
	strcpy(req->payload_key, "path");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload, "r42align");
	check(agent_call(req, resp) == 0, "legacy name query_file");
	check(resp->status == AGENT_STATUS_OK, "legacy query_file status");
	check(resp->value1 != 0, "legacy query_file inum");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7104;
	strcpy(req->tool_name, "read_file_digest");
	strcpy(req->payload_key, "selector");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload, "r42align");
	check(agent_call(req, resp) == 0, "legacy name digest");
	check(resp->status == AGENT_STATUS_OK, "legacy digest status");
	check(resp->value0 >= resp->value1, "legacy digest size");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7105;
	strcpy(req->tool_name, "dependency_update");
	strcpy(req->payload_key, "selector");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload,
	       "source=report;target=align;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_call(req, resp) == 0, "legacy dependency update");
	check(resp->status == AGENT_STATUS_OK, "legacy dependency status");
	check(strcmp(resp->result, "dependency_updated") == 0,
	      "legacy dependency text");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7106;
	strcpy(req->tool_name, "dependency_query");
	strcpy(req->payload_key, "label");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload,
	       "label=report;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_call(req, resp) == 0, "legacy dependency query");
	check(resp->status == AGENT_STATUS_OK, "legacy dependency query status");
	check(text_contains(resp->result, "align"),
	      "legacy dependency query result");
	printf("agentfinal_ucore: legacy_name_protocol=1\n");
}

static void make_generic_op(struct agent_op *op, int tool_id, uint64 id,
			    uint64 arg0, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool_id;
	op->request_id = id;
	op->arg0 = arg0;
	if (payload)
		strcpy(op->payload, payload);
}

static void check_generic_action_and_llm(void)
{
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_event event;

	make_generic_op(&op, AGENT_TOOL_ACTION_COMMIT, 7201, 0,
			"label=align;run_id=RUN-042;namespace=lab-gene-x");
	check(agent_run(&op, &res, 1, 0) == 1, "generic action run");
	check(res.status == AGENT_STATUS_OK, "generic action status");
	check(strcmp(res.result, "action_committed") == 0,
	      "generic action text");

	make_generic_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 7202, 0,
			"label=report;run_id=RUN-042;namespace=lab-gene-x");
	check(agent_run(&op, &res, 1, 0) == 1, "generic artifact run");
	check(res.status == AGENT_STATUS_OK, "generic artifact status");
	check(strcmp(res.result, "artifact_updated") == 0,
	      "generic artifact text");
	printf("agentfinal_ucore: generic_action_abi=1\n");

	check(agent_watch(AGENT_EVENT_LLM_DONE, "template") == 0,
	      "watch llm done");
	make_generic_op(&op, AGENT_TOOL_LLM_REQUEST, 7203, 0,
			"template prompt summary");
	check(agent_run(&op, &res, 1, 0) == 1, "llm request run");
	check(res.status == AGENT_STATUS_OK, "llm request status");
	check(strcmp(res.result, "llm_request") == 0, "llm request text");
	make_generic_op(&op, AGENT_TOOL_LLM_RESPONSE, 7204, getpid(),
			"template response summary");
	check(agent_run(&op, &res, 1, 0) == 1, "llm response run");
	check(res.status == AGENT_STATUS_OK, "llm response status");
	check(res.value2 == 1, "llm response delivered");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 20) == AGENT_STATUS_OK, "wait llm response");
	check(event.type == AGENT_EVENT_LLM_DONE, "llm event type");
	check(strcmp(event.payload, "template response summary") == 0,
	      "llm event payload");
	printf("agentfinal_ucore: llm_template_relay=1\n");
}

static void check_runtime_trace(void)
{
	int n;
	int has_context = 0;
	int has_sched = 0;
	int has_wait = 0;
	uint64 last_tick = 0;

	n = agent_trace_snapshot(trace_records, AGENT_TRACE_MAX_RECORDS);
	check(n > 0, "runtime trace count");
	check(n <= AGENT_TRACE_MAX_RECORDS, "runtime trace cap");
	for (int i = 0; i < n; i++) {
		check(trace_records[i].tick >= last_tick,
		      "runtime trace order");
		last_tick = trace_records[i].tick;
		if (trace_records[i].kind == AGENT_TRACE_KIND_CONTEXT) {
			has_context = 1;
			check(trace_records[i].sequence != 0,
			      "trace context sequence");
			if (trace_records[i].tool_id == AGENT_TOOL_AGENT_WAIT &&
			    trace_records[i].value0 == AGENT_EVENT_MESSAGE)
				has_wait = 1;
		}
		if (trace_records[i].kind == AGENT_TRACE_KIND_SCHED) {
			has_sched = 1;
			check((trace_records[i].flags &
			       AGENT_SCHED_REASON_ROLE_WEIGHT) != 0,
			      "trace sched reason");
		}
	}
	check(has_context, "trace has context");
	check(has_sched, "trace has sched");
	check(has_wait, "trace has wait");
	printf("agentfinal_ucore: runtime_trace=1 records=%d context=%d sched=%d wait=%d\n",
	       n, has_context, has_sched, has_wait);
}

static void check_span_trace(struct agent_info *info)
{
	int total;
	int n;
	int has_context = 0;
	int has_event = 0;
	uint64 last_sequence = 0;

	check(agent_info(info) == 0, "span trace info");
	check(info->current_span_id != 0, "span trace current span");
	total = agent_span_trace_snapshot(0, 0);
	check(total > 0, "span trace total");
	n = agent_span_trace_snapshot(span_records, AGENT_AUDIT_MAX_RECORDS);
	check(n > 0, "span trace records");
	check(n <= total, "span trace count");
	for (int i = 0; i < n; i++) {
		check(span_records[i].span_id == info->current_span_id,
		      "span trace id");
		check(span_records[i].sequence >= last_sequence,
		      "span trace order");
		last_sequence = span_records[i].sequence;
		if (span_records[i].kind == AGENT_AUDIT_KIND_CONTEXT)
			has_context = 1;
		if (span_records[i].kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
		    span_records[i].kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			has_event = 1;
	}
	check(has_context, "span trace context");
	check(has_event, "span trace event");
	printf("agentfinal_ucore: span_trace=1 records=%d context=%d event=%d\n",
	       n, has_context, has_event);
}

static void check_unified_timeline(void)
{
	int total;
	int n;
	int has_context = 0;
	int has_sched = 0;
	int has_audit = 0;
	int has_prefetch = 0;
	int audit_records = 0;
	int recent_records = 0;
	int cursor_records = 0;
	uint64 last_tick = 0;
	uint64 mid_tick;
	uint64 cursor_tick;
	uint64 cursor_sequence;
	int cursor_source;

	total = agent_timeline_snapshot(0, 0);
	check(total > 0, "timeline total");
	n = agent_timeline_snapshot(timeline_records,
				    AGENT_TIMELINE_MAX_RECORDS);
	check(n > 0, "timeline records");
	check(n <= AGENT_TIMELINE_MAX_RECORDS, "timeline count");
	for (int i = 0; i < n; i++) {
		check(timeline_records[i].tick >= last_tick,
		      "timeline order");
		last_tick = timeline_records[i].tick;
		if (timeline_records[i].source ==
		    AGENT_TIMELINE_SOURCE_CONTEXT)
			has_context = 1;
		if (timeline_records[i].source == AGENT_TIMELINE_SOURCE_SCHED)
			has_sched = 1;
		if (timeline_records[i].source == AGENT_TIMELINE_SOURCE_AUDIT)
			has_audit = 1;
		if (timeline_records[i].source ==
		    AGENT_TIMELINE_SOURCE_PREFETCH)
			has_prefetch = 1;
	}
	check(has_context, "timeline context");
	check(has_sched, "timeline sched");
	check(has_audit, "timeline audit");
	check(has_prefetch, "timeline prefetch");
	mid_tick = timeline_records[n / 2].tick;
	cursor_tick = timeline_records[n / 2].tick;
	cursor_source = timeline_records[n / 2].source;
	cursor_sequence = timeline_records[n / 2].sequence;

	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK;
	timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	audit_records = agent_timeline_query(&timeline_filter, timeline_records,
					     AGENT_TIMELINE_MAX_RECORDS);
	check(audit_records > 0, "timeline query audit");
	for (int i = 0; i < audit_records; i++)
		check(timeline_records[i].source == AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline query audit source");

	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_START_TICK;
	timeline_filter.start_tick = mid_tick;
	recent_records = agent_timeline_query(&timeline_filter,
					      timeline_records,
					      AGENT_TIMELINE_MAX_RECORDS);
	check(recent_records > 0, "timeline query recent");
	for (int i = 0; i < recent_records; i++)
		check(timeline_records[i].tick >= mid_tick,
		      "timeline query recent tick");

	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_AFTER_CURSOR;
	timeline_filter.after_tick = cursor_tick;
	timeline_filter.after_source = cursor_source;
	timeline_filter.after_sequence = cursor_sequence;
	cursor_records = agent_timeline_query(&timeline_filter,
					      timeline_records,
					      AGENT_TIMELINE_MAX_RECORDS);
	check(cursor_records > 0, "timeline query cursor");
	for (int i = 0; i < cursor_records; i++)
		check(timeline_after_cursor(&timeline_records[i], cursor_tick,
					    cursor_source, cursor_sequence),
		      "timeline query cursor order");

	printf("agentfinal_ucore: unified_timeline=1 records=%d context=%d sched=%d audit=%d prefetch=%d\n",
	       n, has_context, has_sched, has_audit, has_prefetch);
	printf("agentfinal_ucore: timeline_query=1 audit=%d recent=%d cursor=%d\n",
	       audit_records, recent_records, cursor_records);
}

static void check_provenance_graph(void)
{
	int total;
	int n;
	int has_context = 0;
	int has_audit = 0;

	total = agent_provenance_snapshot(0, 0);
	check(total >= AGENT_BATCH_MAX - 1, "provenance total");
	n = agent_provenance_snapshot(provenance_edges, FINAL_PROVENANCE_MAX);
	check(n > 0, "provenance records");
	check(n <= total, "provenance count");
	for (int i = 0; i < n; i++) {
		struct agent_provenance_edge *edge = &provenance_edges[i];

		check(edge->source_sequence != 0,
		      "provenance source sequence");
		check(edge->target_sequence != 0,
		      "provenance target sequence");
		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->source_pid == getpid() &&
		    edge->target_pid == getpid() &&
		    edge->source_sequence == 1 &&
		    edge->target_sequence == 2)
			has_context = 1;
		if (edge->kind == AGENT_PROVENANCE_EDGE_AUDIT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_AUDIT &&
		    edge->source_pid == getpid())
			has_audit = 1;
	}
	check(has_context, "provenance context edge");
	check(has_audit, "provenance audit edge");
	printf("agentfinal_ucore: provenance_graph=1 edges=%d context=%d audit=%d\n",
	       n, has_context, has_audit);
}

static void check_run_ledger(void)
{
	int n;
	int chain_gaps = 0;

	n = agent_audit_snapshot(span_records, AGENT_AUDIT_MAX_RECORDS);
	check(n > 0, "ledger audit records");
	check(n <= AGENT_AUDIT_MAX_RECORDS, "ledger audit cap");
	memset(&final_ledger, 0, sizeof(final_ledger));
	check(agent_ledger_snapshot(&final_ledger) == 0, "ledger snapshot");
	check(final_ledger.version == AGENT_LEDGER_VERSION, "ledger version");
	check(final_ledger.visible_records > 0, "ledger visible");
	check(n <= (int)final_ledger.visible_records, "ledger visible audit");
	check(final_ledger.total_records >= final_ledger.visible_records,
	      "ledger total");
	check(final_ledger.latest_sequence >= final_ledger.oldest_sequence,
	      "ledger sequence range");
	check(final_ledger.ledger_hash != 0, "ledger hash");
	check(final_ledger.context_records > 0, "ledger context");
	check(final_ledger.event_records > 0, "ledger event");
	check(final_ledger.sched_records > 0, "ledger sched");
	check(final_ledger.prefetch_records > 0, "ledger prefetch");
	check(final_ledger.timeline_total >= final_ledger.total_records,
	      "ledger timeline total");
	check(span_records[0].sequence >= final_ledger.oldest_sequence,
	      "ledger oldest window");
	check(span_records[n - 1].sequence <= final_ledger.latest_sequence,
	      "ledger latest window");
	if (n == (int)final_ledger.visible_records)
		check(span_records[0].sequence == final_ledger.oldest_sequence,
		      "ledger oldest");
	if (span_records[n - 1].sequence == final_ledger.latest_sequence)
		check(span_records[n - 1].record_hash == final_ledger.ledger_hash,
		      "ledger latest hash");
	for (int i = 0; i < n; i++) {
		check(span_records[i].record_hash != 0, "ledger record hash");
		if (i > 0 && span_records[i].prev_hash !=
				     span_records[i - 1].record_hash)
			chain_gaps++;
	}
	check((uint64)chain_gaps <= final_ledger.dropped_records,
	      "ledger pruning gaps accounted");
	printf("agentfinal_ucore: run_ledger=1 records=%d gaps=%d dropped=%d hash=%d context=%d event=%d sched=%d prefetch=%d\n",
	       n, chain_gaps, (int)final_ledger.dropped_records,
	       (int)final_ledger.ledger_hash,
	       (int)final_ledger.context_records,
	       (int)final_ledger.event_records,
	       (int)final_ledger.sched_records,
	       (int)final_ledger.prefetch_records);
}

static void set_timeline_wait_future_filter(uint64 source_mask,
					    int event_type)
{
	check(agent_info(&final_info) == 0, "timeline wait current tick");
	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
				AGENT_TIMELINE_FILTER_START_TICK;
	timeline_filter.source_mask = source_mask;
	timeline_filter.start_tick = final_info.current_tick + 1;
	if (event_type != 0) {
		timeline_filter.flags |= AGENT_TIMELINE_FILTER_EVENT_TYPE;
		timeline_filter.event_type = event_type;
	}
}

static void check_timeline_wait(void)
{
	int waited;
	int queried;
	int consumed;
	int read_records;
	int timeout_status;
	int gated_status;
	int event_gated_status;
	int source_gate;
	int event_gate;
	uint64 wait_count_before;
	uint64 wait_sleep_before;
	uint64 wait_wakeup_before;
	uint64 wait_sleep_after;
	uint64 wait_wakeup_after;
	uint64 gate_wakeup_before;
	uint64 gate_wakeup_after;
	uint64 event_wakeup_before;
	uint64 event_wakeup_after;
	uint64 read_sleep_before;
	uint64 read_wakeup_before;
	uint64 read_sleep_after;
	uint64 read_wakeup_after;

	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_CONTEXT,
					0);
	waited = agent_timeline_wait(&timeline_filter, 1);
	check(waited == AGENT_STATUS_TIMEOUT, "timeline wait timeout");
	timeout_status = waited;

	check(agent_watch(AGENT_EVENT_TIMER, "heartbeat") == 0,
	      "timeline wait timer watch");
	check(agent_heartbeat(1) == 0, "timeline wait heartbeat");
	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_CONTEXT,
					0);
	check(agent_info(&final_info) == 0,
	      "timeline wait gate before");
	gate_wakeup_before = final_info.timeline_wait_wakeup_count;
	gated_status = agent_timeline_wait(&timeline_filter, 6);
	check(gated_status == AGENT_STATUS_TIMEOUT,
	      "timeline wait source gate timeout");
	check(agent_info(&final_info) == 0,
	      "timeline wait gate after");
	gate_wakeup_after = final_info.timeline_wait_wakeup_count;
	source_gate = gate_wakeup_after == gate_wakeup_before;
	check(source_gate, "timeline wait source gate");

	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_AUDIT,
					AGENT_EVENT_MESSAGE);
	check(agent_info(&final_info) == 0,
	      "timeline wait event gate before");
	event_wakeup_before = final_info.timeline_wait_wakeup_count;
	event_gated_status = agent_timeline_wait(&timeline_filter, 6);
	check(event_gated_status == AGENT_STATUS_TIMEOUT,
	      "timeline wait event gate timeout");
	check(agent_info(&final_info) == 0,
	      "timeline wait event gate after");
	event_wakeup_after = final_info.timeline_wait_wakeup_count;
	event_gate = event_wakeup_after == event_wakeup_before;
	check(event_gate, "timeline wait event gate");

	check(agent_heartbeat_stop() == 0, "timeline wait gate heartbeat stop");
	for (;;) {
		consumed = agent_wait(&final_event, 0);
		if (consumed != AGENT_STATUS_OK)
			break;
	}
	check(consumed == AGENT_STATUS_TIMEOUT, "timeline wait gate drain");

	check(agent_heartbeat(8) == 0, "timeline wait heartbeat restart");
	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_AUDIT,
					AGENT_EVENT_TIMER);
	check(agent_info(&final_info) == 0,
	      "timeline wait info before");
	wait_count_before = final_info.timeline_wait_count;
	wait_sleep_before = final_info.timeline_wait_sleep_count;
	wait_wakeup_before = final_info.timeline_wait_wakeup_count;
	waited = agent_timeline_wait(&timeline_filter, 50);
	check(waited > 0, "timeline wait wake");
	queried = agent_timeline_query(&timeline_filter, timeline_records,
				       AGENT_TIMELINE_MAX_RECORDS);
	check(queried == waited, "timeline wait query count");
	check(queried > 0, "timeline wait query records");
	check(agent_info(&final_info) == 0,
	      "timeline wait info after");
	wait_sleep_after = final_info.timeline_wait_sleep_count;
	wait_wakeup_after = final_info.timeline_wait_wakeup_count;
	check(final_info.timeline_wait_count >= wait_count_before + 1,
	      "timeline wait count");
	check(wait_sleep_after > wait_sleep_before,
	      "timeline wait sleep count");
	check(wait_wakeup_after > wait_wakeup_before,
	      "timeline wait wakeup count");
	check(agent_heartbeat_stop() == 0,
	      "timeline wait heartbeat stop before read");
	for (;;) {
		consumed = agent_wait(&final_event, 0);
		if (consumed != AGENT_STATUS_OK)
			break;
	}
	check(consumed == AGENT_STATUS_TIMEOUT,
	      "timeline wait drain before read");

	check(agent_heartbeat(8) == 0, "timeline read heartbeat restart");
	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_AUDIT,
					AGENT_EVENT_TIMER);
	check(agent_info(&final_info) == 0,
	      "timeline read info before");
	read_sleep_before = final_info.timeline_wait_sleep_count;
	read_wakeup_before = final_info.timeline_wait_wakeup_count;
	read_records = agent_timeline_read(&timeline_filter, timeline_records,
					   AGENT_TIMELINE_MAX_RECORDS, 50);
	check(read_records > 0, "timeline read wake");
	for (int i = 0; i < read_records; i++) {
		check(timeline_records[i].source ==
			      AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline read source");
		check(timeline_records[i].event_type == AGENT_EVENT_TIMER,
		      "timeline read event type");
	}
	check(agent_info(&final_info) == 0,
	      "timeline read info after");
	read_sleep_after = final_info.timeline_wait_sleep_count;
	read_wakeup_after = final_info.timeline_wait_wakeup_count;
	check(read_sleep_after > read_sleep_before,
	      "timeline read sleep count");
	check(read_wakeup_after > read_wakeup_before,
	      "timeline read wakeup count");

	check(agent_heartbeat_stop() == 0, "timeline wait heartbeat stop");
	for (;;) {
		consumed = agent_wait(&final_event, 0);
		if (consumed != AGENT_STATUS_OK)
			break;
	}
	check(consumed == AGENT_STATUS_TIMEOUT, "timeline wait drain timer");
	check(agent_unwatch(AGENT_EVENT_TIMER, "heartbeat") >= 0,
	      "timeline wait unwatch");
	printf("agentfinal_ucore: timeline_wait=1 timeout=%d source_gate=%d event_gate=%d wake=%d query=%d read=%d sleeps=%d wakeups=%d\n",
	       timeout_status, source_gate, event_gate, waited, queried,
	       read_records,
	       (int)(wait_sleep_after - wait_sleep_before),
	       (int)(wait_wakeup_after - wait_wakeup_before));
}

static void run_agent_child(void)
{
	struct agent_context_header *direct_header;
	struct agent_result *latest;
	struct agent_info *info = &final_info;
	struct agent_context_header *header = &final_header;
	struct agent_context_detail *detail = &final_detail;
	struct agent_context_record *manual = &final_manual;
	struct agent_file_query *q = &final_query;
	struct agent_file_query_result *qr = &final_query_result;
	struct agent_event *event = &final_event;
	int wake_rc;
	int n;

	check(agent_info(info) == 0, "agent_info");
	check(info->is_agent == 1, "is agent");
	check(info->agent_role == AGENT_ROLE_ORCHESTRATOR, "orchestrator role");
	check((info->capability_mask & AGENT_CAP_META_WRITE) != 0,
	      "meta write cap");
	check((info->capability_mask & AGENT_CAP_ORCHESTRATE) != 0,
	      "orchestrate cap");
	check(info->context_base == AGENT_CONTEXT_BASE, "context base");
	check(info->context_size == AGENT_CONTEXT_SIZE, "context size");
	direct_header = (struct agent_context_header *)info->context_base;
	latest = (struct agent_result *)(info->context_base +
					 info->latest_response_offset);
	check(direct_header->magic == AGENT_CONTEXT_MAGIC, "context magic");
	printf("agentfinal_ucore: context size=%d capacity=%d\n",
	       (int)info->context_size, (int)direct_header->capacity);

	check_context_commit_lane();
	check(context_clear() == 0, "context clear");
	for (int i = 0; i < AGENT_BATCH_MAX; i++)
		make_echo(&ops[i], i + 1, i == 7 ? "ucore-final" : "final");
	check(agent_run(ops, results, AGENT_BATCH_MAX, 0) == AGENT_BATCH_MAX,
	      "agent_run batch");
	check(results[0].sequence == 1, "first sequence");
	check(results[AGENT_BATCH_MAX - 1].sequence == AGENT_BATCH_MAX,
	      "last sequence");
	check(latest->sequence == AGENT_BATCH_MAX, "latest direct");
	printf("agentfinal_ucore: batch first_seq=%d last_seq=%d\n",
	       (int)results[0].sequence,
	       (int)results[AGENT_BATCH_MAX - 1].sequence);

	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot count");
	check(header->latest_sequence == AGENT_BATCH_MAX, "snapshot latest");
	check(header->version == AGENT_CONTEXT_VERSION, "context version");
	check(records[0].cause_sequence == 0, "first cause");
	check(records[0].span_id != 0, "first span");
	check(records[1].cause_sequence == records[0].sequence,
	      "next cause");
	check(records[1].span_id == records[0].span_id, "span continuity");
	check(header->current_cause_sequence == AGENT_BATCH_MAX,
	      "current cause");
	check(header->current_span_id == records[0].span_id, "header span");
	check(header->provenance_edges >= AGENT_BATCH_MAX - 1,
	      "provenance edges");
	printf("agentfinal_ucore: causal_context=1 first_cause=%d next_cause=%d span=%d edges=%d\n",
	       (int)records[0].cause_sequence,
	       (int)records[1].cause_sequence, (int)records[0].span_id,
	       (int)header->provenance_edges);
	check(records[0].prev_hash == 0, "first prev hash");
	check(records[0].record_hash != 0, "first record hash");
	for (int i = 1; i < n; i++) {
		check(records[i].prev_hash == records[i - 1].record_hash,
		      "context chain link");
		check(records[i].record_hash != 0, "context record hash");
	}
	check(header->latest_record_hash == records[n - 1].record_hash,
	      "header latest hash");
	printf("agentfinal_ucore: context_integrity=1 first_hash=%d latest_hash=%d\n",
	       (int)records[0].record_hash,
	       (int)header->latest_record_hash);
	check_provenance_graph();
	check(strcmp(records[7].payload, "ucore-final") == 0,
	      "short payload");
	check(strcmp(records[7].result, "ucore-final") == 0, "short result");
	printf("agentfinal_ucore: short_text_history=1 payload=%s result=%s\n",
	       records[7].payload, records[7].result);
	check(context_detail(records[7].sequence, detail) == 0,
	      "context detail");
	check((detail->flags & AGENT_CONTEXT_RECORD_F_SYSTEM) != 0,
	      "detail system flag");
	check(strcmp(detail->op.payload, "ucore-final") == 0,
	      "detail payload");
	printf("agentfinal_ucore: context_detail=1 sequence=%d\n",
	       (int)detail->sequence);

	records[0].sequence = 9999;
	((struct agent_context_record *)(info->context_base +
					 info->records_offset))[0]
		.sequence = 9999;
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot after tamper");
	check(records[0].sequence == 1, "shadow protects snapshot");
	check(((struct agent_context_record *)(info->context_base +
					       info->records_offset))[0]
		      .sequence == 1,
	      "snapshot refreshes mirror");
	printf("agentfinal_ucore: tamper_protected=1\n");

	check(header->user_cache_size >= 16, "user cache size");
	strcpy((char *)(info->context_base + header->user_cache_offset),
	       "cache-ok");
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot after cache");
	check(strcmp((char *)(info->context_base + header->user_cache_offset),
		     "cache-ok") == 0,
	      "user cache preserved");
	printf("agentfinal_ucore: user_cache_preserved=1 offset=%d size=%d\n",
	       (int)header->user_cache_offset, (int)header->user_cache_size);

	memset(manual, 0, sizeof(*manual));
	manual->tool_id = AGENT_TOOL_CONTEXT_PUSH;
	manual->request_id = 6501;
	manual->status = AGENT_STATUS_OK;
	strcpy(manual->payload, "manual-audit");
	strcpy(manual->result, "manual-ok");
	check(context_push(manual) == 0, "manual context push");
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX + 1, "manual snapshot count");
	check((records[n - 1].flags & AGENT_CONTEXT_RECORD_F_MANUAL) != 0,
	      "manual flag");
	check(context_detail(records[n - 1].sequence, detail) == 0,
	      "manual detail");
	check((detail->flags & AGENT_CONTEXT_RECORD_F_MANUAL) != 0,
	      "manual detail flag");
	printf("agentfinal_ucore: record_flags system=1 manual=1 truncated=%d\n",
	       (int)((records[7].flags & AGENT_CONTEXT_RECORD_F_TRUNCATED) !=
		     0));

	for (int round = 0; round < 2; round++) {
		for (int i = 0; i < AGENT_BATCH_MAX; i++)
			make_echo(&ops[i], 1000 + round * AGENT_BATCH_MAX + i,
				  "wrap");
		check(agent_run(ops, results, AGENT_BATCH_MAX, 0) ==
			      AGENT_BATCH_MAX,
		      "wrap batch");
	}
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_CONTEXT_MAX_RECORDS, "fifo count");
	check(header->oldest_sequence == 66, "fifo oldest");
	check(header->latest_sequence == 193, "fifo latest");
	check(header->dropped_records == 65, "fifo dropped");
	printf("agentfinal_ucore: fifo oldest=%d latest=%d dropped=%d\n",
	       (int)header->oldest_sequence, (int)header->latest_sequence,
	       (int)header->dropped_records);

	check(agent_file_meta_init() == 0, "meta init");
	seed_demo_metadata();
	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(q->project, "lab-gene-x");
	strcpy(q->run_id, "RUN-042");
	strcpy(q->stage, "align");
	check(agent_file_query(q, qr) >= 1, "file query");
	check(qr->used_index == 1, "file query index");
	check(qr->plan == AGENT_FILE_QUERY_PLAN_STAGE_INDEX,
	      "file query plan");
	check((qr->plan_reason & AGENT_FILE_QUERY_REASON_STAGE_INDEX) != 0,
	      "file query reason");
	check(qr->candidate_records == qr->scanned_records,
	      "file query candidates");
	n = agent_file_prefetch_snapshot(final_prefetch_hints,
					 AGENT_FILE_PREFETCH_MAX_HINTS);
	check(n >= 1, "prefetch snapshot");
	check(final_prefetch_hints[0].source_sequence > 0,
	      "prefetch source");
	check((final_prefetch_hints[0].reason &
	       AGENT_FILE_PREFETCH_REASON_DEPENDENCY) != 0,
	      "prefetch dependency");
	printf("agentfinal_ucore: file_query hits=%d scanned=%d used_index=%d\n",
	       qr->total_hits, qr->scanned_records, qr->used_index);
	printf("agentfinal_ucore: prefetch_hints=1 count=%d first_stage=%s\n",
	       n, final_prefetch_hints[0].hit.stage);
	check(agent_info(info) == 0, "agent_info after prefetch");
	n = agent_file_prefetch_span_snapshot(final_prefetch_hints,
					      AGENT_FILE_PREFETCH_MAX_HINTS);
	check(n >= 1, "span prefetch snapshot");
	check(final_prefetch_hints[0].span_id == info->current_span_id,
	      "span prefetch span");
	check((final_prefetch_hints[0].reason &
	       AGENT_FILE_PREFETCH_REASON_SPAN_BUS) != 0,
	      "span prefetch reason");
	check(final_prefetch_hints[0].source_pid == getpid(),
	      "span prefetch source pid");
	check(final_prefetch_hints[0].target_pid == getpid(),
	      "span prefetch target pid");
	printf("agentfinal_ucore: span_prefetch=1 count=%d first_stage=%s\n",
	       n, final_prefetch_hints[0].hit.stage);
	check_generic_action_and_llm();
	check_legacy_name_protocol();

	check(agent_watch(AGENT_EVENT_MESSAGE, "self") == 0, "watch");
	memset(event, 0, sizeof(*event));
	event->type = AGENT_EVENT_MESSAGE;
	event->corr_id = 7001;
	strcpy(event->payload, "self wake");
	wake_rc = agent_wake(info->is_agent ? getpid() : 0, event);
	check(wake_rc == 0, "wake self");
	check(agent_wait(event, 20) == AGENT_STATUS_OK, "wait self");
	check(event->corr_id == 7001, "wait corr");
	printf("agentfinal_ucore: event_wait=1 payload=%s\n", event->payload);
	check_runtime_trace();
	check_span_trace(info);
	check_unified_timeline();
	check_timeline_wait();
	check_run_ledger();

	printf("agentfinal_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentfinal_ucore: Agent-OS on uCore final verification\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "agent_create_role orchestrator");
	if (pid == 0)
		run_agent_child();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentfinal_ucore: parent passed\n");
	return 0;
}
