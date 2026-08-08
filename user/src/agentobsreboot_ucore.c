#include <agent.h>
#include <agent_observe_test_phase_abi.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PHASE_FILE "obsphase"
#define ARTIFACT_FILE "obseed"
#define PHASE_MAGIC AGENT_OBSERVE_TEST_PHASE_MAGIC
#define AUDIT_READ_MAX 16
#define AUDIT_READ_BATCH 8
#define RECOVERY_RECORD_MAX 6
/* 覆盖两次有界全 bank 扫描及目录发布。 */
#define PHASE_OPEN_ATTEMPTS 256
#define WORKFLOW_CREATE_ATTEMPTS 256

struct child_result {
	int ok;
	struct agent_observe_test_evidence_identity identity;
};

struct observe_record_workspace {
	/* 恢复阶段与在线审计快照串行复用此缓冲区。 */
	union {
		struct agent_audit_record audit[AUDIT_READ_BATCH];
		struct agent_observe_recovery_record
			recovery[RECOVERY_RECORD_MAX];
	} records;
	struct agent_audit_filter filter;
};

static struct observe_record_workspace record_workspace;

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentobsreboot_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void read_exact(int fd, void *dst, int bytes, const char *message)
{
	char *p = dst;
	int done = 0;

	while (done < bytes) {
		int n = read(fd, p + done, bytes - done);

		check(n > 0, message);
		done += n;
	}
}

static void write_exact(int fd, const void *src, int bytes,
			const char *message)
{
	const char *p = src;
	int done = 0;

	while (done < bytes) {
		int n = write(fd, p + done, bytes - done);

		check(n > 0, message);
		done += n;
	}
}

static int lifecycle_equal(struct agent_workflow_lifecycle_key left,
			   struct agent_workflow_lifecycle_key right)
{
	return left.id == right.id && left.reserved == 0 &&
	       right.reserved == 0 && left.generation == right.generation;
}

static void print_phase_identity(
	const char *tag,
	const struct agent_observe_test_evidence_identity *identity)
{
	printf("agentobsreboot_ucore: %s scope=%u agent=%u lifecycle_id=%u lifecycle_generation=%llu max_sequence=%llu max_span_id=%llu max_event_id=%llu actor_control_id=%llu receipt_sequence=%llu receipt_record_hash=%llu receipt_id=%llu\n",
	       tag, identity->scope_id, identity->agent_id,
	       identity->lifecycle.id, identity->lifecycle.generation,
	       identity->max_sequence, identity->max_span_id,
	       identity->max_event_id, identity->actor_control_id,
	       identity->receipt_sequence, identity->receipt_record_hash,
	       identity->receipt_id);
}

static unsigned long long emit_identity_activity(void)
{
	struct agent_op op;
	struct agent_result result;
	struct agent_event event;

	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_ECHO;
	op.request_id = 0x6f627369ULL;
	strcpy(op.payload, "durable-identity-allocator-probe");
	check(agent_run(&op, &result, 1, 0) == 1 &&
	      result.status == AGENT_STATUS_OK,
	      "allocate stable span identity");
	check(agent_watch(AGENT_EVENT_MESSAGE, "observe-id") ==
	      AGENT_STATUS_OK, "watch identity event");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 0x6f627369ULL;
	strcpy(event.payload, "observe-id");
	check(agent_wake(getpid(), &event) == AGENT_STATUS_OK,
	      "allocate stable event identity");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 50) == AGENT_STATUS_OK &&
	      event.event_id != 0 && event.span_id != 0,
	      "read stable event identity");
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "observe-id") ==
	      1, "remove identity event watch");
	return event.event_id;
}

static __attribute__((noinline)) int
snapshot_audit_identity(struct agent_observe_test_evidence_identity *identity,
			int agent_id,
			struct agent_workflow_lifecycle_key lifecycle,
			unsigned long long start_sequence)
{
	struct agent_audit_record *records = record_workspace.records.audit;
	struct agent_audit_filter *filter = &record_workspace.filter;
	int total = 0;

	while (total < AUDIT_READ_MAX) {
		int limit = AUDIT_READ_MAX - total;
		int count;

		if (limit > AUDIT_READ_BATCH)
			limit = AUDIT_READ_BATCH;
		memset(filter, 0, sizeof(*filter));
		if (start_sequence != 0) {
			filter->flags = AGENT_AUDIT_FILTER_START_SEQUENCE;
			filter->start_sequence = start_sequence;
		}
		memset(records, 0, sizeof(record_workspace.records.audit));
		count = agent_audit_query(filter, records, limit);
		if (count < 0)
			return count;
		for (int i = 0; i < count; i++) {
			if (records[i].agent_id != agent_id ||
			    records[i].workflow_lifecycle_id != lifecycle.id ||
			    records[i].workflow_lifecycle_generation !=
				    lifecycle.generation)
				continue;
			if (records[i].sequence > identity->max_sequence)
				identity->max_sequence = records[i].sequence;
			if (records[i].span_id > identity->max_span_id)
				identity->max_span_id = records[i].span_id;
			if (records[i].actor_control_id > identity->actor_control_id)
				identity->actor_control_id =
					records[i].actor_control_id;
		}
		total += count;
		if (count < limit)
			break;
		if (records[count - 1].sequence == ~0ULL)
			break;
		start_sequence = records[count - 1].sequence + 1;
	}
	return total;
}

static void snapshot_identity_since(
	struct agent_observe_test_evidence_identity *identity,
	unsigned long long event_id, unsigned long long start_sequence)
{
	struct agent_workflow_lifecycle_info lifecycle;
	struct agent_info info;
	int count;

	memset(identity, 0, sizeof(*identity));
	memset(&info, 0, sizeof(info));
	memset(&lifecycle, 0, sizeof(lifecycle));
	check(agent_info(&info) == AGENT_STATUS_OK && info.is_agent &&
	      info.agent_id > 0 && info.filesystem_domain >= 3,
	      "read stable Agent identity");
	check(agent_workflow_lifecycle_info(&lifecycle, 0) == AGENT_STATUS_OK &&
	      lifecycle.key.id != 0 && lifecycle.key.reserved == 0 &&
	      lifecycle.key.generation != 0,
	      "read immutable lifecycle identity");
	count = snapshot_audit_identity(identity, info.agent_id, lifecycle.key,
					start_sequence);
	check(count > 0 && count <= AUDIT_READ_MAX, "read live audit identity");
	identity->scope_id = (unsigned int)info.filesystem_domain;
	identity->agent_id = (unsigned int)info.agent_id;
	identity->lifecycle = lifecycle.key;
	identity->max_event_id = event_id;
	if (identity->max_sequence == 0 || identity->max_span_id == 0 ||
	    identity->max_event_id == 0 || identity->actor_control_id == 0)
		printf("agentobsreboot_ucore: identity_snapshot_unexpected count=%d agent=%d scope=%d lifecycle_id=%u lifecycle_generation=%llu sequence=%llu span=%llu event=%llu control=%llu\n",
		       count, info.agent_id, info.filesystem_domain,
		       lifecycle.key.id, lifecycle.key.generation,
		       identity->max_sequence, identity->max_span_id,
		       identity->max_event_id, identity->actor_control_id);
	check(identity->max_sequence != 0 && identity->max_span_id != 0 &&
	      identity->max_event_id != 0 && identity->actor_control_id != 0,
	      "audit carries stable control identity");
}

static void snapshot_identity(struct agent_observe_test_evidence_identity *identity,
			      unsigned long long event_id)
{
	snapshot_identity_since(identity, event_id, 0);
}

static void latest_audit_record(struct agent_audit_record *record)
{
	struct agent_audit_filter filter;
	struct agent_ledger_summary summary;

	memset(&summary, 0, sizeof(summary));
	check(agent_ledger_snapshot(&summary) == AGENT_STATUS_OK &&
	      summary.latest_sequence != 0, "read latest audit sequence");
	memset(&filter, 0, sizeof(filter));
	filter.flags = AGENT_AUDIT_FILTER_START_SEQUENCE;
	filter.start_sequence = summary.latest_sequence;
	memset(record, 0, sizeof(*record));
	check(agent_audit_query(&filter, record, 1) == 1 &&
	      record->sequence == summary.latest_sequence &&
	      record->record_hash != 0, "read exact latest audit record");
}

static void receipt_request_init(
	struct agent_audit_receipt_request *request,
	const struct agent_audit_record *record, unsigned int operation,
	unsigned long long receipt_id, int timeout_ticks)
{
	memset(request, 0, sizeof(*request));
	request->version = AGENT_AUDIT_RECEIPT_VERSION;
	request->size = sizeof(*request);
	request->operation = operation;
	request->lifecycle.id = record->workflow_lifecycle_id;
	request->lifecycle.generation =
		record->workflow_lifecycle_generation;
	request->sequence = record->sequence;
	request->record_hash = record->record_hash;
	request->receipt_id = receipt_id;
	request->timeout_ticks = timeout_ticks;
}

static void receipt_wait_for_state(
	const struct agent_audit_record *record, unsigned long long receipt_id,
	unsigned int expected)
{
	for (int attempt = 0; attempt < 128; attempt++) {
		struct agent_audit_receipt_request request;
		int status;

		receipt_request_init(&request, record, AGENT_AUDIT_RECEIPT_WAIT,
				     receipt_id, 1000);
		status = agent_audit_receipt(&request);
		check(status == request.status, "receipt WAIT return/status agreement");
		if (status == AGENT_STATUS_OK && request.durability == expected &&
		    request.receipt_id == receipt_id)
			return;
		if (expected == AGENT_AUDIT_DURABILITY_FAILED &&
		    status == AGENT_STATUS_STALE &&
		    request.durability == AGENT_AUDIT_DURABILITY_NOT_FOUND &&
		    request.receipt_id == 0)
			return;
		if (request.durability != AGENT_AUDIT_DURABILITY_PENDING ||
		    (status != AGENT_STATUS_OK && status != AGENT_STATUS_RETRY &&
		     status != AGENT_STATUS_TIMEOUT))
			printf("agentobsreboot_ucore: receipt_wait_unexpected expected=%u status=%d durability=%u receipt=%llu\n",
			       expected, status, request.durability,
			       request.receipt_id);
		check(request.durability == AGENT_AUDIT_DURABILITY_PENDING &&
		      (status == AGENT_STATUS_OK || status == AGENT_STATUS_RETRY ||
		       status == AGENT_STATUS_TIMEOUT),
		      "receipt WAIT remains explicit while pending");
		check(sleep(1) == 0, "wait for receipt persistence deadline");
	}
	check(0, "receipt WAIT reached expected terminal state");
}

static void verify_audit_receipts(
	struct agent_observe_test_evidence_identity *identity)
{
	struct agent_audit_receipt_request request;
	struct agent_context_record context;
	struct agent_audit_record record;
	unsigned long long receipt_id;
	int status;

#ifdef AGENT_OBSERVE_TEST_PROFILE
	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x72636576696374ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "receipt-window-negative");
	strcpy(context.result, "pending");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "append receipt eviction target");
	latest_audit_record(&record);
	receipt_request_init(&request, &record, AGENT_AUDIT_RECEIPT_STATUS,
			     0, 0);
	check(agent_audit_receipt(&request) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK && request.receipt_id != 0 &&
	      request.durability == AGENT_AUDIT_DURABILITY_PENDING,
	      "receipt STATUS returns a non-durable PENDING token");
	receipt_id = request.receipt_id;
	receipt_request_init(
		&request, &record,
		AGENT_AUDIT_RECEIPT_TEST_EVICT_BEFORE_PERSIST, receipt_id, 0);
	status = agent_audit_receipt(&request);
	if (status != AGENT_STATUS_OK || request.status != AGENT_STATUS_OK ||
	    request.receipt_id != receipt_id ||
	    request.durability != AGENT_AUDIT_DURABILITY_PENDING)
		printf("agentobsreboot_ucore: receipt_evict_unexpected status=%d request_status=%d durability=%u receipt=%llu expected_receipt=%llu\n",
		       status, request.status, request.durability,
		       request.receipt_id, receipt_id);
	check(status == request.status && status == AGENT_STATUS_OK &&
	      request.receipt_id == receipt_id &&
	      request.durability == AGENT_AUDIT_DURABILITY_PENDING,
	      "eviction injection preserves the exact pending receipt");
	receipt_wait_for_state(
		&record, receipt_id, AGENT_AUDIT_DURABILITY_FAILED);
#endif
	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x72636475726162ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "receipt-positive");
	strcpy(context.result, "pending");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "append durable receipt target");
	latest_audit_record(&record);
	receipt_request_init(&request, &record, AGENT_AUDIT_RECEIPT_STATUS,
			     0, 0);
	check(agent_audit_receipt(&request) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK && request.receipt_id != 0 &&
	      request.durability == AGENT_AUDIT_DURABILITY_PENDING,
	      "positive receipt starts PENDING");
	receipt_id = request.receipt_id;
	receipt_wait_for_state(
		&record, receipt_id, AGENT_AUDIT_DURABILITY_DURABLE);
	receipt_request_init(&request, &record, AGENT_AUDIT_RECEIPT_STATUS,
			     receipt_id ^ 1ULL, 0);
	check(agent_audit_receipt(&request) == AGENT_STATUS_STALE,
	      "receipt rejects a forged identifier");
	identity->receipt_sequence = record.sequence;
	identity->receipt_record_hash = record.record_hash;
	identity->receipt_id = receipt_id;
	printf("agentobsreboot_ucore: receipt_pending_not_evidence=1 receipt_durable_exact=1 receipt_fake_stale=1 receipt_window_not_evidence=1\n");
}

static void verify_receipt_not_agent(void)
{
	struct agent_audit_receipt_request request;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_AUDIT_RECEIPT_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_AUDIT_RECEIPT_STATUS;
	request.lifecycle.id = 1;
	request.lifecycle.generation = 1;
	request.sequence = 1;
	request.record_hash = 1;
	check(agent_audit_receipt(&request) == AGENT_STATUS_NOT_AGENT,
	      "plain process cannot query audit receipts");
	printf("agentobsreboot_ucore: receipt_permission_not_agent=1\n");
}

static void verify_historical_receipt_status(
	const struct agent_observe_test_evidence_identity *identity,
	int expected_status,
	const char *message)
{
	struct agent_audit_receipt_request request;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_AUDIT_RECEIPT_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_AUDIT_RECEIPT_STATUS;
	request.lifecycle = identity->lifecycle;
	request.sequence = identity->receipt_sequence;
	request.record_hash = identity->receipt_record_hash;
	request.receipt_id = identity->receipt_id;
	check(identity->receipt_sequence != 0 &&
	      identity->receipt_record_hash != 0 && identity->receipt_id != 0 &&
	      agent_audit_receipt(&request) == expected_status,
	      message);
}

static unsigned long long emit_checkpoint_evidence(void)
{
	struct agent_context_record context;
	struct agent_file_meta meta;
#ifdef AGENT_OBSERVE_TEST_PROFILE
	struct agent_ledger_summary summary;
#endif
	int fd;
	char byte = 'e';
	unsigned long long event_id;

#ifdef AGENT_OBSERVE_TEST_PROFILE
	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x64726f706f6e6c79ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "audit-drop-only-probe");
	strcpy(context.result, "admission-dropped");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "publish drop-only audit probe");
#else
	event_id = emit_identity_activity();

	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x6f627331ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "durable-observation-sentinel");
	strcpy(context.result, "checkpointed");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "append observation sentinel");
	printf("agentobsreboot_ucore: boot1_context_ready=1\n");
#endif
	fd = open(ARTIFACT_FILE, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "create checkpoint artifact");
	check(write(fd, &byte, 1) == 1 && close(fd) == 0,
	      "write checkpoint artifact");
	printf("agentobsreboot_ucore: boot1_artifact_ready=1\n");
	memset(&meta, 0, sizeof(meta));
	meta.fid = 1;
	strcpy(meta.physical_name, ARTIFACT_FILE);
	strcpy(meta.logical_path, "/observe/reboot/evidence");
	strcpy(meta.project, "observe-recovery");
	strcpy(meta.workflow, "three-boot");
	strcpy(meta.run_id, "BOOT1");
	strcpy(meta.stage, "checkpoint");
	strcpy(meta.kind, "audit");
	strcpy(meta.status, "durable");
	meta.flags = AGENT_FILE_META_F_PERSIST;
	check(agent_file_meta_set(&meta) == AGENT_STATUS_OK,
	      "commit observation with metadata bank");
	printf("agentobsreboot_ucore: boot1_metadata_ready=1\n");
#ifdef AGENT_OBSERVE_TEST_PROFILE
	check(agent_file_meta_init() == AGENT_STATUS_OK,
	      "finish the queued observation checkpoint");
	memset(&summary, 0, sizeof(summary));
	check(agent_ledger_snapshot(&summary) == AGENT_STATUS_OK &&
	      summary.visible_records == 1 && summary.dropped_records > 0 &&
	      summary.total_records == summary.dropped_records + 1 &&
	      summary.latest_sequence != 0 && summary.ledger_hash != 0,
	      "first successful audit follows a captured drop-only checkpoint");
	printf("agentobsreboot_ucore: audit_drop_only_first_success=1\n");
	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x64726f7072656c73ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "audit-drop-release");
	strcpy(context.result, "normal-admission");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "release the audit admission profile");
	event_id = emit_identity_activity();
	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x6f627331ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "durable-observation-sentinel");
	strcpy(context.result, "checkpointed");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "append observation sentinel");
	printf("agentobsreboot_ucore: boot1_context_ready=1\n");
#endif
	return event_id;
}

static __attribute__((noinline)) void boot1_child(int report_fd, int release_fd)
{
	struct child_result result;
	char release;
	unsigned long long event_id;

	event_id = emit_checkpoint_evidence();
	check(agent_file_meta_init() == AGENT_STATUS_OK,
	      "live metadata reload preserves newer observation state");
	printf("agentobsreboot_ucore: boot1_reload_ready=1\n");
	memset(&result, 0, sizeof(result));
	snapshot_identity(&result.identity, event_id);
	verify_audit_receipts(&result.identity);
	printf("agentobsreboot_ucore: boot1_durable_identity scope=%u lifecycle_id=%u lifecycle_generation=%llu agent_id=%u receipt_sequence=%llu receipt_record_hash=%llu receipt_id=%llu\n",
	       result.identity.scope_id, result.identity.lifecycle.id,
	       result.identity.lifecycle.generation, result.identity.agent_id,
	       result.identity.receipt_sequence,
	       result.identity.receipt_record_hash,
	       result.identity.receipt_id);
	result.ok = 1;
	write_exact(report_fd, &result, sizeof(result), "report boot1 identity");
	read_exact(release_fd, &release, 1, "wait for durable phase state");
	printf("agentobsreboot_ucore: boot1_checkpoint_ready=1\n");
	for (;;)
		sleep(100);
}

static __attribute__((noinline)) void
boot2_live_reload(int expected_fd, int report_fd)
{
	struct agent_context_record context;
	struct agent_ledger_summary before;
	struct agent_ledger_summary after;
	struct agent_observe_test_evidence_identity expected;
	struct child_result result;
	unsigned long long event_id;

	read_exact(expected_fd, &expected, sizeof(expected),
		   "read historical lifecycle for live reload");
	verify_historical_receipt_status(
		&expected, AGENT_STATUS_STALE,
		"successor lifecycle rejects a historical receipt");
	printf("agentobsreboot_ucore: receipt_teardown_stale=1\n");
	event_id = emit_identity_activity();
	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x6f627333ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "live-reload-generation-probe");
	strcpy(context.result, "preserved");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "append live reload generation probe");
	memset(&before, 0, sizeof(before));
	check(agent_ledger_snapshot(&before) == AGENT_STATUS_OK &&
	      before.latest_sequence != 0 && before.ledger_hash != 0,
	      "snapshot live ledger before reload");
	check(agent_file_meta_init() == AGENT_STATUS_OK,
	      "reload historical checkpoint beside active successor");
	memset(&after, 0, sizeof(after));
	check(agent_ledger_snapshot(&after) == AGENT_STATUS_OK &&
	      after.latest_sequence >= before.latest_sequence &&
	      after.latest_sequence != ~0ULL && after.ledger_hash != 0 &&
	      after.total_records >= before.total_records &&
	      (after.latest_sequence != before.latest_sequence ||
	       after.ledger_hash == before.ledger_hash),
	      "live reload never rolls back newer audit state");
	printf("agentobsreboot_ucore: live_reload_ledger_monotonic=1\n");
	/* 保留量密集重载后，用新记录重新验证身份字段。 */
	event_id = emit_identity_activity();
	memset(&result, 0, sizeof(result));
	snapshot_identity_since(&result.identity, event_id,
				after.latest_sequence + 1);
	check(result.identity.lifecycle.id != expected.lifecycle.id ||
	      result.identity.lifecycle.generation >
		      expected.lifecycle.generation,
	      "historical lifecycle key stays behind the active successor");
	result.ok = 1;
	write_exact(report_fd, &result, sizeof(result),
		    "report live reload generation probe");
	printf("agentobsreboot_ucore: boot2_live_reload_ready=1\n");
	exit(0);
}

static int recovery_list(struct agent_observe_recovery_scope *scopes,
			 int max)
{
	struct agent_observe_recovery_request request;
	int status;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_LIST;
	request.max_records = max;
	status = agent_observe_recovery(&request, scopes);
	check(status == request.status, "LIST return/status agreement");
	return status == AGENT_STATUS_OK ? (int)request.returned : status;
}

static void verify_stable_successor(
	const struct agent_observe_test_evidence_identity *old,
	struct agent_observe_test_evidence_identity *current)
{
	struct agent_context_record context;
	unsigned long long event_id = emit_identity_activity();

	memset(&context, 0, sizeof(context));
	context.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	context.request_id = 0x6f627332ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "stable-identity-successor");
	strcpy(context.result, "verified");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "append successor observation");
	snapshot_identity(current, event_id);
	check(current->agent_id > old->agent_id &&
	      current->actor_control_id > old->actor_control_id &&
	      current->max_sequence > old->max_sequence &&
	      current->max_span_id > old->max_span_id &&
	      current->max_event_id > old->max_event_id &&
	      (current->lifecycle.id != old->lifecycle.id ||
	       current->lifecycle.generation > old->lifecycle.generation),
	      "persisted identity and lifecycle high-water marks");
}

static __attribute__((noinline)) void
boot3_identity_successor(int expected_fd, int report_fd)
{
	struct agent_observe_test_evidence_identity expected;
	struct child_result result;

	read_exact(expected_fd, &expected, sizeof(expected),
		   "read prior active identity for boot3 successor");
	memset(&result, 0, sizeof(result));
	verify_stable_successor(&expected, &result.identity);
	result.ok = 1;
	write_exact(report_fd, &result, sizeof(result),
		    "report boot3 identity successor");
	printf("agentobsreboot_ucore: boot3_identity_successor=1\n");
	exit(0);
}

#ifdef AGENT_OBSERVE_TEST_PROFILE
#define TIMELINE_THREADS_TOOL_A 0x7f010001
#define TIMELINE_THREADS_TOOL_B 0x7f010002
static volatile int timeline_thread_results[2];

static void allocate_identity_cut_ids(
	struct agent_observe_test_identity_ids *ids, int successor)
{
	struct agent_observe_recovery_request request;

	memset(&request, 0, sizeof(request));
	memset(ids, 0, sizeof(*ids));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation =
		successor ?
			AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR :
			AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_CUT;
	check(agent_observe_recovery(&request, ids) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK && ids->audit_sequence != 0 &&
	      ids->span_id != 0 && ids->event_id != 0 &&
	      ids->control_id != 0 && ids->agent_id != 0 &&
	      ids->lifecycle_generation != 0,
	      "allocate every durable identity immediately before cut");
}

static __attribute__((noinline)) void
boot_identity_cut(int report_fd, int successor)
{
	struct agent_observe_test_identity_ids ids;
	struct child_result result;

	allocate_identity_cut_ids(&ids, successor);
	if (!successor)
		for (;;)
			sleep(100);
	memset(&result, 0, sizeof(result));
	result.ok = 1;
	write_exact(report_fd, &result, sizeof(result),
		    "report immediate identity allocation");
	exit(0);
}

static void timeline_wait_thread(void *arg)
{
	int lane = (int)(long)arg;
	struct agent_timeline_filter filter;

	memset(&filter, 0, sizeof(filter));
	filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
		       AGENT_TIMELINE_FILTER_TOOL_ID;
	filter.source_mask = lane == 0 ?
		AGENT_TIMELINE_SOURCE_MASK_CONTEXT :
		AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	filter.tool_id = lane == 0 ?
		TIMELINE_THREADS_TOOL_A : TIMELINE_THREADS_TOOL_B;
	timeline_thread_results[lane] =
		agent_timeline_wait(&filter, lane == 0 ? 200 : 40);
	exit(0);
}

static __attribute__((noinline)) void verify_timeline_wait_threads(void)
{
	struct agent_observe_recovery_request request;
	struct agent_context_record context;
	int tids[2];
	int observed = 0;

	timeline_thread_results[0] = -999;
	timeline_thread_results[1] = -999;
	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_THREADS;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK,
	      "arm per-thread timeline wait profile");
	tids[0] = thread_create(timeline_wait_thread, (void *)0);
	tids[1] = thread_create(timeline_wait_thread, (void *)1);
	check(tids[0] > 0 && tids[1] > 0, "create timeline wait threads");
	for (int attempt = 0; attempt < 200; attempt++) {
		memset(&request, 0, sizeof(request));
		request.version = AGENT_OBSERVE_RECOVERY_VERSION;
		request.size = sizeof(request);
		request.operation =
			AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_THREADS_STATUS;
		check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
		      request.status == AGENT_STATUS_OK,
		      "query concurrent timeline waiters");
		if (request.after_sequence == 2 &&
		    request.bank_generation == 7) {
			observed = 1;
			break;
		}
		sched_yield();
	}
	printf("agentobsreboot_ucore: timeline_wait_threads_status active=%llu features=%llu peak=%llu wakeups=%d results=%d,%d\n",
	       request.after_sequence, request.bank_generation,
	       request.completion_token, request.returned,
	       timeline_thread_results[0], timeline_thread_results[1]);
	check(observed, "observe two isolated timeline waiters");
	memset(&context, 0, sizeof(context));
	context.tool_id = TIMELINE_THREADS_TOOL_A;
	context.request_id = 0x74687265616473ULL;
	context.status = AGENT_STATUS_OK;
	strcpy(context.payload, "timeline-thread-target");
	strcpy(context.result, "published");
	check(context_push(&context) == AGENT_STATUS_OK,
	      "publish a real matching Context record");
	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation =
		AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_THREADS_STATUS;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK && request.returned == 1,
	      "real Context wakes only its matching timeline waiter");
	check(waittid(tids[0]) == 0 && waittid(tids[1]) == 0,
	      "join isolated timeline waiters");
	check(timeline_thread_results[0] > 0 &&
	      timeline_thread_results[1] == AGENT_STATUS_TIMEOUT,
	      "timeline filters and deadlines remain independent");
	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation =
		AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_THREADS_STATUS;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.after_sequence == 0 && request.completion_token == 2,
	      "timeline waiter teardown clears every sidecar");
	printf("agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1\n");
}

static __attribute__((noinline)) void verify_timeline_wait_epoch_recheck(void)
{
	struct agent_observe_recovery_request request;
	struct agent_timeline_filter filter;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_WAIT;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK,
	      "arm timeline wait epoch race");
	memset(&filter, 0, sizeof(filter));
	filter.flags = AGENT_TIMELINE_FILTER_AFTER_CURSOR;
	filter.after_tick = ~0ULL;
	check(agent_timeline_wait(&filter, 0) == AGENT_STATUS_TIMEOUT,
	      "timeline wait retries changed epoch before timeout");
	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_WAIT_STATUS;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK &&
	      request.after_sequence == 2 && request.completion_token == 1,
	      "timeline wait final epoch recheck observed injection");
	printf("agentobsreboot_ucore: timeline_wait_epoch_recheck=1 injection=2 retries=1 bounded_timeout=1\n");
}

static __attribute__((noinline)) void
read_event_queue_state(unsigned long long *queued,
		       unsigned long long *dropped, const char *message)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == AGENT_STATUS_OK, message);
	*queued = info.event_queue_count;
	*dropped = info.event_dropped;
}

static __attribute__((noinline)) void verify_event_identity_exhaustion(void)
{
	struct agent_observe_recovery_request request;
	struct agent_event event;
	unsigned long long before_queued;
	unsigned long long before_dropped;
	unsigned long long after_queued;
	unsigned long long after_dropped;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_TEST_EXHAUST_EVENT_ID;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK,
	      "inject exhausted event identity allocator");
	read_event_queue_state(&before_queued, &before_dropped,
			       "snapshot event queue before exhaustion probe");
	check(agent_watch(AGENT_EVENT_MESSAGE, "event-id-exhausted") ==
	      AGENT_STATUS_OK, "watch exhausted event identity probe");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 0x65786861757374ULL;
	strcpy(event.payload, "event-id-exhausted");
	check(agent_wake(getpid(), &event) == AGENT_STATUS_NO_SPACE,
	      "event identity exhaustion fails closed");
	read_event_queue_state(&after_queued, &after_dropped,
			       "snapshot event queue after exhaustion probe");
	check(after_queued == before_queued &&
	      after_dropped == before_dropped + 1,
	      "exhausted event identity is never published");
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "event-id-exhausted") ==
	      1, "remove exhausted event identity watch");
}
#endif

static __attribute__((noinline)) void
verify_recovery_scope(
	const struct agent_observe_test_evidence_identity *expected)
{
	struct agent_observe_recovery_scope
		scopes[AGENT_OBSERVE_RECOVERY_MAX_SCOPES];
	int count;
	int matched = 0;

	memset(scopes, 0, sizeof(scopes));
	count = recovery_list(scopes, AGENT_OBSERVE_RECOVERY_MAX_SCOPES);
	check(count > 0 && count <= AGENT_OBSERVE_RECOVERY_MAX_SCOPES,
	      "LIST exposes bounded sealed lifecycles");
	for (int i = 0; i < count; i++)
		if (scopes[i].scope_id == expected->scope_id &&
		    lifecycle_equal(scopes[i].lifecycle, expected->lifecycle) &&
		    scopes[i].record_count == RECOVERY_RECORD_MAX &&
		    scopes[i].dropped_records > 0 && scopes[i].ledger_hash != 0)
			matched = 1;
	check(matched, "LIST preserves the boot1 sealed lifecycle");
	printf("agentobsreboot_ucore: audit_drop_recovered=1\n");
}

static __attribute__((noinline)) void
verify_recovery_records(
	const struct agent_observe_test_evidence_identity *expected)
{
	struct agent_observe_recovery_record *records =
		record_workspace.records.recovery;
	struct agent_observe_recovery_request request;
	int matched = 0;
	int receipt_matched = 0;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_READ;
	request.evidence = expected->lifecycle;
	request.max_records = RECOVERY_RECORD_MAX;
	check(agent_observe_recovery(&request, records) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK &&
	      request.returned == RECOVERY_RECORD_MAX &&
	      request.bank_generation != 0,
	      "READ sealed observation records");
	for (unsigned int i = 0; i < request.returned; i++) {
		check(records[i].reserved == 0 &&
		      records[i].durability == AGENT_AUDIT_DURABILITY_DURABLE &&
		      records[i].bank_generation == request.bank_generation,
		      "READ binds every record to one durable active bank");
		if (records[i].record.agent_id == (int)expected->agent_id &&
		    records[i].record.actor_control_id == expected->actor_control_id &&
		    records[i].record.workflow_lifecycle_id == expected->lifecycle.id &&
		    records[i].record.workflow_lifecycle_generation ==
			    expected->lifecycle.generation)
			matched = 1;
		if (records[i].record.sequence == expected->receipt_sequence &&
		    records[i].record.record_hash == expected->receipt_record_hash &&
		    records[i].receipt_id == expected->receipt_id)
			receipt_matched = 1;
	}
	check(matched, "READ preserves stable actor and lifecycle identity");
	check(receipt_matched,
	      "READ re-verifies the exact durable receipt after reboot");
	printf("agentobsreboot_ucore: checkpoint_v8_recovered=1 records=%u\n",
	       request.returned);
}

static __attribute__((noinline)) void
verify_recovery_v1(
	const struct agent_observe_test_evidence_identity *expected)
{
	struct agent_observe_recovery_request request;
	struct agent_audit_record record;

	memset(&record, 0, sizeof(record));
	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION_V1;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_READ;
	request.evidence = expected->lifecycle;
	request.after_sequence = expected->receipt_sequence - 1;
	request.max_records = 1;
	check(agent_observe_recovery(&request, &record) == AGENT_STATUS_OK &&
	      request.status == AGENT_STATUS_OK && request.returned >= 1 &&
	      request.bank_generation != 0 &&
	      record.sequence == expected->receipt_sequence &&
	      record.record_hash == expected->receipt_record_hash,
	      "version 1 READ remains binary compatible");
}

static __attribute__((noinline)) void
reap_recovery_evidence(
	const struct agent_observe_test_evidence_identity *expected)
{
	struct agent_observe_recovery_request request;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_OBSERVE_RECOVERY_VERSION;
	request.size = sizeof(request);
	request.operation = AGENT_OBSERVE_RECOVERY_REAP;
	request.evidence = expected->lifecycle;
	check(agent_observe_recovery(&request, 0) == AGENT_STATUS_OK &&
	      request.completion_token != 0,
	      "start sealed evidence erase");
	{
		unsigned long long token = request.completion_token;

		memset(&request, 0, sizeof(request));
		request.version = AGENT_OBSERVE_RECOVERY_VERSION;
		request.size = sizeof(request);
		request.operation = AGENT_OBSERVE_RECOVERY_STATUS;
		request.evidence = expected->lifecycle;
		request.completion_token = token ^ 1ULL;
		check(agent_observe_recovery(&request, 0) ==
			      AGENT_STATUS_BAD_PARAM,
		      "erase rejects a modified completion token");
		request.completion_token = token;
	}
	for (int attempts = 0;; attempts++) {
		unsigned long long token = request.completion_token;
		int status;

		check(attempts < 500, "wait for replicated evidence erase");
		memset(&request, 0, sizeof(request));
		request.version = AGENT_OBSERVE_RECOVERY_VERSION;
		request.size = sizeof(request);
		request.operation = AGENT_OBSERVE_RECOVERY_STATUS;
		request.evidence = expected->lifecycle;
		request.completion_token = token;
		status = agent_observe_recovery(&request, 0);
		if (status == AGENT_STATUS_OK)
			break;
		check(status == AGENT_STATUS_RETRY,
		      "erase reports retry or explicit completion");
		sleep(1);
	}
}

static __attribute__((noinline)) void
report_recovery_result(int report_fd,
		       const struct agent_observe_test_evidence_identity *expected)
{
	struct child_result result;

	memset(&result, 0, sizeof(result));
	result.ok = 1;
	result.identity = *expected;
	write_exact(report_fd, &result, sizeof(result), "report boot2 erase");
}

static __attribute__((noinline)) void
boot2_recovery(int expected_fd, int report_fd)
{
	struct agent_observe_test_evidence_identity expected;

	read_exact(expected_fd, &expected, sizeof(expected),
		   "read expected boot1 identity");
	verify_historical_receipt_status(
		&expected, AGENT_STATUS_DENIED,
		"Recovery role lacks receipt orchestration authority");
	printf("agentobsreboot_ucore: receipt_permission_recovery_denied=1\n");
	verify_recovery_scope(&expected);
	verify_recovery_records(&expected);
	verify_recovery_v1(&expected);
	printf("agentobsreboot_ucore: receipt_recovery_exact=1 receipt_v1_compatible=1 bank_generation_bound=1\n");
	reap_recovery_evidence(&expected);
	report_recovery_result(report_fd, &expected);
	exit(0);
}

static __attribute__((noinline)) void
boot3_recovery(int expected_fd, int report_fd)
{
	struct agent_observe_recovery_scope
		scopes[AGENT_OBSERVE_RECOVERY_MAX_SCOPES];
	struct agent_observe_test_phase_state expected;
	struct child_result result;
	int count;

	read_exact(expected_fd, &expected, sizeof(expected),
		   "read erased and successor lifecycle identities");
	memset(scopes, 0, sizeof(scopes));
	count = recovery_list(scopes, AGENT_OBSERVE_RECOVERY_MAX_SCOPES);
	check(count >= 0 && count <= AGENT_OBSERVE_RECOVERY_MAX_SCOPES,
	      "LIST remains bounded after secure erase");
	for (int i = 0; i < count; i++)
		check(!lifecycle_equal(scopes[i].lifecycle,
				       expected.evidence.lifecycle),
		      "secure erase survives the second reboot");
#ifdef AGENT_OBSERVE_TEST_PROFILE
	verify_timeline_wait_epoch_recheck();
	verify_timeline_wait_threads();
	verify_event_identity_exhaustion();
#endif
	memset(&result, 0, sizeof(result));
	result.ok = 1;
	result.identity = expected.successor;
	write_exact(report_fd, &result, sizeof(result), "report boot3 isolation");
	exit(0);
}

static int phase_state_empty(
	const struct agent_observe_test_phase_state *state)
{
	const unsigned char *bytes = (const unsigned char *)state;

	for (unsigned int i = 0; i < sizeof(*state); i++)
		if (bytes[i] != 0)
			return 0;
	return 1;
}

static void load_phase(struct agent_observe_test_phase_state *state)
{
	char trailing;
	int attempts;
	int fd = -1;

	memset(state, 0, sizeof(*state));
	for (attempts = 0; attempts < PHASE_OPEN_ATTEMPTS; attempts++) {
		fd = open(PHASE_FILE, O_RDONLY);
		if (fd >= 0)
			break;
		check(sleep(1) == 0, "wait for bounded metadata boot recovery");
	}
	check(fd >= 0, "open protected phase state");
	printf("agentobsreboot_ucore: phase_open_ready retries=%d\n", attempts);
	read_exact(fd, state, sizeof(*state), "read protected phase state");
	check(read(fd, &trailing, 1) == 0, "phase state has exact size");
	check(close(fd) == 0, "close protected phase state");
	printf("agentobsreboot_ucore: phase_read=1\n");
	if (state->magic == 0)
		check(phase_state_empty(state), "validate empty phase predecessor");
	else
		check(state->magic == PHASE_MAGIC && state->phase <= 2,
		      "validate durable phase state");
	printf("agentobsreboot_ucore: phase_loaded magic=%u phase=%u\n",
	       state->magic, state->phase);
}

static int create_workflow_ready(int role, int report_fd, int input_fd)
{
	struct agent_info info;
	int pid = AGENT_STATUS_RETRY;

	for (int attempts = 0; attempts < WORKFLOW_CREATE_ATTEMPTS; attempts++) {
		check(agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(input_fd) == AGENT_STATUS_OK,
		      "delegate explicit workflow endpoints");
		pid = agent_workflow_create(role);
		if (pid != AGENT_STATUS_RETRY) {
			printf("agentobsreboot_ucore: workflow_create_status=%d attempts=%d\n",
			       pid, attempts + 1);
			return pid;
		}
		check(sleep(1) == 0, "wait for metadata admission readiness");
	}
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "read workflow admission diagnostics");
	printf("agentobsreboot_ucore: workflow_create_scan runs=%llu entries=%llu added=%llu updated=%llu removed=%llu pending=%llu failures=%llu deferred=%llu\n",
	       info.file_scan_runs, info.file_scan_entries, info.file_scan_added,
	       info.file_scan_updated, info.file_scan_removed,
	       info.file_scan_pending, info.file_scan_failures,
	       info.file_scan_deferred);
	printf("agentobsreboot_ucore: workflow_create_status=%d attempts=%d\n",
	       pid, WORKFLOW_CREATE_ATTEMPTS);
	return pid;
}

static void run_child_phase(int role, int phase, const void *expected,
			    struct child_result *result)
{
	int reports[2];
	int inputs[2];
	int pid;
	int status = 0;

	check(pipe(reports) == 0 && pipe(inputs) == 0, "create phase pipes");
	pid = create_workflow_ready(role, reports[1], inputs[0]);
	check(pid >= 0, "create isolated workflow");
	if (pid == 0) {
		close(reports[0]);
		close(inputs[1]);
#ifdef AGENT_OBSERVE_TEST_PROFILE
		if (phase == 4)
			boot_identity_cut(reports[1], 0);
		else if (phase == 5)
			boot_identity_cut(reports[1], 1);
		else
#endif
		if (phase == 6)
			boot3_identity_successor(inputs[0], reports[1]);
		else if (phase == 0)
			boot1_child(reports[1], inputs[0]);
		else if (phase == 1)
			boot2_recovery(inputs[0], reports[1]);
		else if (phase == 3)
			boot2_live_reload(inputs[0], reports[1]);
		else
			boot3_recovery(inputs[0], reports[1]);
		exit(1);
	}
	close(reports[1]);
	close(inputs[0]);
	if (expected != 0)
		write_exact(inputs[1], expected,
			    phase == 2 ?
				    sizeof(struct agent_observe_test_phase_state) :
				    sizeof(struct agent_observe_test_evidence_identity),
			    "send expected evidence identity");
	read_exact(reports[0], result, sizeof(*result), "read child result");
	check(result->ok, "child phase completed checks");
	if (phase == 0) {
		char release = 1;

		print_phase_identity("phase1_identity", &result->identity);
		write_exact(inputs[1], &release, 1, "release boot1 checkpoint");
		close(reports[0]);
		close(inputs[1]);
		return;
	}
	check(close(reports[0]) == 0 && close(inputs[1]) == 0,
	      "close phase pipes");
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait isolated workflow teardown");
}

int main(void)
{
	struct agent_observe_test_phase_state state;
	struct child_result result;

	verify_receipt_not_agent();
	load_phase(&state);
	memset(&result, 0, sizeof(result));
	if (state.magic == 0) {
#ifdef AGENT_OBSERVE_TEST_PROFILE
		run_child_phase(AGENT_ROLE_RECOVERY, 4, 0, &result);
#else
		run_child_phase(AGENT_ROLE_ORCHESTRATOR, 0, 0, &result);
#endif
		for (;;)
			sleep(100);
	}
#ifdef AGENT_OBSERVE_TEST_PROFILE
	if (state.phase == 0) {
		run_child_phase(AGENT_ROLE_RECOVERY, 5, 0, &result);
		run_child_phase(AGENT_ROLE_ORCHESTRATOR, 0, 0, &result);
		for (;;)
			sleep(100);
	}
#endif
	if (state.phase == 1) {
		run_child_phase(AGENT_ROLE_ORCHESTRATOR, 3, &state.evidence,
				&result);
		state.successor = result.identity;
		print_phase_identity("phase2_successor", &state.successor);
		run_child_phase(AGENT_ROLE_RECOVERY, 1, &state.evidence, &result);
		printf("agentobsreboot_ucore: boot2_reap_replicated=1\n");
		for (;;)
			sleep(100);
	}
	run_child_phase(AGENT_ROLE_ORCHESTRATOR, 6, &state.successor, &result);
	run_child_phase(AGENT_ROLE_RECOVERY, 2, &state, &result);
	printf("agentobsreboot_ucore: boot3_erased=1 generation_isolated=1 stable_identity=1\n");
	printf("agentobsreboot_ucore: parent passed\n");
	return 0;
}
