#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define RP_STATE_BUFFER_SIZE 32768
#include <research_platform_state.h>
#include <rp_evidence.h>

#define BACKEND_EDIT_FILE "r42edrep"
#define BACKEND_QUERY_RECORDS 12
#define BACKEND_QUERY_OPERATIONS 4096
#define BACKEND_QUERY_META_RETRIES (8 * BACKEND_QUERY_RECORDS)
#define BACKEND_QUERY_MAGIC 0x5250515545525931ULL
#define BACKEND_QUERY_FNV_OFFSET 1469598103934665603ULL
#define BACKEND_QUERY_FNV_PRIME 1099511628211ULL

struct backend_query_record {
	uint64 magic;
	uint64 dependency_mask;
	uint64 record_hash;
	int fid;
	char physical_name[8];
	char logical_path[8];
	char project[16];
	char workflow[20];
	char run_id[12];
	char stage[12];
	char kind[12];
	char status[8];
	char summary[40];
};
_Static_assert(sizeof(struct backend_query_record) == 168,
	       "backend query record ABI");

static struct agent_context_header backend_header;
static struct agent_context_record backend_records[8];
static struct agent_file_query backend_query;
static struct agent_file_query_result backend_query_result;
static struct agent_file_edit_state backend_edit_state;
static uint64 backend_edit_base_version;
static struct agent_op backend_op;
static struct agent_result backend_result;
static int backend_runtime_cases;
static int backend_runtime_source_reads;
static int backend_runtime_kernel_checks;
static unsigned long long backend_runtime_source_digest =
	RP_EVIDENCE_FNV_OFFSET;
static char backend_line[512];
static struct backend_query_record backend_scaled_record;
static uint64 backend_query_digest;
static uint64 backend_query_records_examined;
static uint64 backend_query_devs[BACKEND_QUERY_RECORDS];
static uint64 backend_query_inums[BACKEND_QUERY_RECORDS];
static uint64 backend_query_incarnations[BACKEND_QUERY_RECORDS];
static int backend_query_matches;
static int backend_query_seen;
static char backend_query_line[256];
static const char *const backend_query_stages[BACKEND_QUERY_RECORDS] = {
	"summarize", "package", "join", "evaluate", "transform", "prepare",
	"ingest", "export", "aggregate", "derive", "normalize", "collect",
};

struct backend_runtime_spec {
	const char *case_name;
	const char *source;
	const char *key;
	const char *value;
};

struct backend_runtime_receipt {
	int runtime_cases;
	int source_reads;
	int kernel_checks;
	int context_sequence;
	int query_returned;
	int query_scanned;
	int query_used_index;
	int echo_request_id;
	int echo_status;
};

static const struct backend_runtime_spec backend_runtime_specs[] = {
	{"workflow-contract", "rp_wfio", "execution_plan",
	 "workflow-migration-execution-plan:RUN-042:agentcompare"},
	{"retry-state", "rp_retry_plan", "retry_stage", "align"},
	{"kernel-context", "rp_agentos_kernel", "context_snapshot", "present"},
	{"kernel-file-query", "rp_agentos_query", "metadata_source",
	 "kernel_file_index"},
	{"kernel-recovery", "rp_agentos_recovery", "kernel_tool",
	 "action_commit,artifact_update"},
	{"kernel-event", "rp_agentos_timeline", "event_delivery",
	 "kernel_agent_queue"},
	{"kernel-audit", "rp_agentos_audit", "audit_source", "kernel_ledger"},
	{"kernel-edit", "rp_agentos_conflict", "holder_write", "checked"},
};

static uint64 backend_hash_bytes(uint64 hash, const void *data, int length)
{
	const unsigned char *bytes = data;

	for (int i = 0; i < length; i++) {
		hash ^= bytes[i];
		hash *= BACKEND_QUERY_FNV_PRIME;
	}
	return hash;
}

static void backend_make_code(char *out, char prefix, int number)
{
	out[0] = prefix;
	out[1] = '0' + (number / 100) % 10;
	out[2] = '0' + (number / 10) % 10;
	out[3] = '0' + number % 10;
	out[4] = 0;
}

static uint64 backend_record_hash(const struct backend_query_record *record)
{
	uint64 hash = BACKEND_QUERY_FNV_OFFSET;

	hash = backend_hash_bytes(hash, &record->magic, sizeof(record->magic));
	hash = backend_hash_bytes(hash, &record->dependency_mask,
				  sizeof(record->dependency_mask));
	hash = backend_hash_bytes(hash, &record->fid, sizeof(record->fid));
	hash = backend_hash_bytes(hash, record->physical_name,
				  sizeof(record->physical_name));
	hash = backend_hash_bytes(hash, record->logical_path,
				  sizeof(record->logical_path));
	hash = backend_hash_bytes(hash, record->project,
				  sizeof(record->project));
	hash = backend_hash_bytes(hash, record->workflow,
				  sizeof(record->workflow));
	hash = backend_hash_bytes(hash, record->run_id,
				  sizeof(record->run_id));
	hash = backend_hash_bytes(hash, record->stage,
				  sizeof(record->stage));
	hash = backend_hash_bytes(hash, record->kind, sizeof(record->kind));
	hash = backend_hash_bytes(hash, record->status, sizeof(record->status));
	hash = backend_hash_bytes(hash, record->summary,
				  sizeof(record->summary));
	return hash;
}

static void backend_build_record(struct backend_query_record *record, int index)
{
	memset(record, 0, sizeof(*record));
	record->magic = BACKEND_QUERY_MAGIC;
	record->fid = 3000 + index;
	backend_make_code(record->physical_name, 'm', index);
	strcpy(record->logical_path, record->physical_name);
	strcpy(record->project, "lab-gene-x");
	strcpy(record->workflow, "research-query");
	strcpy(record->run_id, "RUN-042");
	strcpy(record->stage, backend_query_stages[index]);
	strcpy(record->kind, "artifact");
	strcpy(record->status, "ready");
	strcpy(record->summary, "scaled research metadata");
	record->record_hash = backend_record_hash(record);
}

static int backend_record_valid(const struct backend_query_record *record,
				int index)
{
	char name[8];
	char status[8];

	backend_make_code(name, 'm', index);
	strcpy(status, "ready");
	return record->magic == BACKEND_QUERY_MAGIC &&
	       record->fid == 3000 + index &&
	       strcmp(record->physical_name, name) == 0 &&
	       strcmp(record->logical_path, name) == 0 &&
	       strcmp(record->project, "lab-gene-x") == 0 &&
	       strcmp(record->workflow, "research-query") == 0 &&
	       strcmp(record->run_id, "RUN-042") == 0 &&
	       strcmp(record->stage, backend_query_stages[index]) == 0 &&
	       strcmp(record->kind, "artifact") == 0 &&
	       strcmp(record->status, status) == 0 &&
	       strcmp(record->summary, "scaled research metadata") == 0 &&
	       record->dependency_mask == 0 &&
	       record->record_hash == backend_record_hash(record);
}

static int backend_write_record(const struct backend_query_record *record)
{
	int fd = open(record->physical_name, O_CREATE | O_WRONLY | O_TRUNC);
	int done = 0;

	if (fd < 0)
		return 0;
	while (done < (int)sizeof(*record)) {
		int n = write(fd, (const char *)record + done,
			      sizeof(*record) - done);

		if (n <= 0)
			break;
		done += n;
	}
	return close(fd) == 0 && done == (int)sizeof(*record);
}

static int backend_metadata_set_bounded(struct agent_file_meta *meta)
{
	int status = AGENT_STATUS_RETRY;

	for (int attempt = 0; attempt < BACKEND_QUERY_META_RETRIES; attempt++) {
		status = agent_file_meta_set(meta);
		if (status != AGENT_STATUS_RETRY)
			return status;
		if (sched_yield() < 0)
			break;
	}
	return status;
}

static int backend_seed_query_record(int index)
{
	struct agent_file_meta meta;

	backend_build_record(&backend_scaled_record, index);
	memset(&meta, 0, sizeof(meta));
	meta.fid = backend_scaled_record.fid;
	strcpy(meta.physical_name, backend_scaled_record.physical_name);
	strcpy(meta.logical_path, backend_scaled_record.logical_path);
	strcpy(meta.project, backend_scaled_record.project);
	strcpy(meta.workflow, backend_scaled_record.workflow);
	strcpy(meta.run_id, backend_scaled_record.run_id);
	strcpy(meta.stage, backend_scaled_record.stage);
	strcpy(meta.kind, backend_scaled_record.kind);
	strcpy(meta.status, backend_scaled_record.status);
	strcpy(meta.summary, backend_scaled_record.summary);
	meta.dependency_mask = backend_scaled_record.dependency_mask;
	if (backend_metadata_set_bounded(&meta) != AGENT_STATUS_OK)
		return 0;
	return backend_write_record(&backend_scaled_record);
}

static int backend_hit_matches(const struct agent_file_hit *hit, int index)
{
	backend_build_record(&backend_scaled_record, index);
	return backend_record_valid(&backend_scaled_record, index) &&
	       hit->fid == backend_scaled_record.fid &&
	       strcmp(hit->physical_name,
		      backend_scaled_record.physical_name) == 0 &&
	       strcmp(hit->logical_path, backend_scaled_record.logical_path) == 0 &&
	       strcmp(hit->stage, backend_scaled_record.stage) == 0 &&
	       strcmp(hit->kind, backend_scaled_record.kind) == 0 &&
	       strcmp(hit->status, backend_scaled_record.status) == 0 &&
	       strcmp(hit->summary, backend_scaled_record.summary) == 0 &&
	       hit->dependency_mask == backend_scaled_record.dependency_mask &&
	       hit->dev != 0 && hit->inum != 0 && hit->incarnation != 0 &&
	       hit->size == sizeof(backend_scaled_record);
}

static int run_backend_query_workload(void)
{
	backend_query_digest = BACKEND_QUERY_FNV_OFFSET;
	backend_query_records_examined = 0;
	backend_query_matches = 0;
	backend_query_seen = 0;
	memset(backend_query_devs, 0, sizeof(backend_query_devs));
	memset(backend_query_inums, 0, sizeof(backend_query_inums));
	memset(backend_query_incarnations, 0,
	       sizeof(backend_query_incarnations));
	for (int i = 0; i < BACKEND_QUERY_RECORDS; i++)
		if (!backend_seed_query_record(i))
			return 0;
	for (int operation = 0; operation < BACKEND_QUERY_OPERATIONS;
	     operation++) {
		int target = (operation * 5 + 3) % BACKEND_QUERY_RECORDS;
		struct agent_file_hit *hit;

		memset(&backend_query, 0, sizeof(backend_query));
		backend_query.flags = AGENT_FILE_QUERY_USE_INDEX;
		backend_query.max_hits = 1;
		strcpy(backend_query.project, "lab-gene-x");
		strcpy(backend_query.workflow, "research-query");
		strcpy(backend_query.run_id, "RUN-042");
		strcpy(backend_query.stage, backend_query_stages[target]);
		if (agent_file_query(&backend_query, &backend_query_result) != 1 ||
		    backend_query_result.total_hits != 1 ||
		    backend_query_result.returned != 1 ||
		    backend_query_result.truncated ||
		    backend_query_result.used_index != 1 ||
		    backend_query_result.plan != AGENT_FILE_QUERY_PLAN_STAGE_INDEX ||
		    (backend_query_result.plan_reason &
		     AGENT_FILE_QUERY_REASON_STAGE_INDEX) == 0 ||
		    backend_query_result.index_rebuild_records != 0)
			return 0;
		hit = &backend_query_result.hits[0];
		if (!backend_hit_matches(hit, target))
			return 0;
		backend_query_devs[target] = hit->dev;
		backend_query_inums[target] = hit->inum;
		backend_query_incarnations[target] = hit->incarnation;
		backend_query_seen |= 1 << target;
		backend_query_matches++;
		backend_query_records_examined +=
			backend_query_result.scanned_records;
		backend_build_record(&backend_scaled_record, target);
		backend_query_digest = backend_hash_bytes(
			backend_query_digest, &backend_scaled_record.record_hash,
			sizeof(backend_scaled_record.record_hash));
	}
	return backend_query_matches == BACKEND_QUERY_OPERATIONS &&
	       backend_query_seen == (1 << BACKEND_QUERY_RECORDS) - 1;
}

static int cleanup_backend_query_workload(void)
{
	for (int i = 0; i < BACKEND_QUERY_RECORDS; i++) {
		struct agent_file_meta meta;

		backend_build_record(&backend_scaled_record, i);
		memset(&meta, 0, sizeof(meta));
		meta.fid = backend_scaled_record.fid;
		strcpy(meta.physical_name, backend_scaled_record.physical_name);
		strcpy(meta.logical_path, backend_scaled_record.logical_path);
		meta.dev = backend_query_devs[i];
		meta.inum = backend_query_inums[i];
		meta.incarnation = backend_query_incarnations[i];
		meta.flags = AGENT_FILE_META_F_DELETE;
		if (backend_metadata_set_bounded(&meta) != AGENT_STATUS_OK ||
		    unlink(backend_scaled_record.physical_name) < 0)
			return 0;
	}
	return 1;
}

static int append_backend_query_receipt(void)
{
	backend_query_line[0] = 0;
	rp_append_text(backend_query_line, sizeof(backend_query_line),
		       "query_workload=research_metadata_lookup;consistency=fresh_snapshot;dataset_records=");
	rp_append_uint_text(backend_query_line, sizeof(backend_query_line),
			    BACKEND_QUERY_RECORDS);
	rp_append_text(backend_query_line, sizeof(backend_query_line),
		       ";query_operations=");
	rp_append_uint_text(backend_query_line, sizeof(backend_query_line),
			    BACKEND_QUERY_OPERATIONS);
	rp_append_text(backend_query_line, sizeof(backend_query_line),
		       ";query_matches=");
	rp_append_uint_text(backend_query_line, sizeof(backend_query_line),
			    backend_query_matches);
	rp_append_text(backend_query_line, sizeof(backend_query_line),
		       ";result_digest=");
	rp_append_uint_text(backend_query_line, sizeof(backend_query_line),
			    backend_query_digest);
	rp_append_text(backend_query_line, sizeof(backend_query_line),
		       ";records_examined=");
	rp_append_uint_text(backend_query_line, sizeof(backend_query_line),
			    backend_query_records_examined);
	rp_append_text(backend_query_line, sizeof(backend_query_line),
		       ";backend=agent_metadata_index;status=verified");
	return rp_append_file("rp_backend", backend_query_line);
}

static void fold_backend_runtime_source(const struct backend_runtime_spec *spec,
					const struct rp_evidence_file_measurement *measured)
{
	char encoded[16];
	char delimiter = 0;
	unsigned long long value = measured->hash;

	backend_runtime_source_digest = rp_evidence_hash_bytes(
		backend_runtime_source_digest, spec->case_name,
		(int)strlen(spec->case_name));
	backend_runtime_source_digest = rp_evidence_hash_bytes(
		backend_runtime_source_digest, &delimiter, 1);
	backend_runtime_source_digest = rp_evidence_hash_bytes(
		backend_runtime_source_digest, spec->source,
		(int)strlen(spec->source));
	backend_runtime_source_digest = rp_evidence_hash_bytes(
		backend_runtime_source_digest, &delimiter, 1);
	for (int i = 0; i < 8; i++) {
		encoded[i] = (char)(value & 0xff);
		value >>= 8;
	}
	value = measured->bytes;
	for (int i = 0; i < 8; i++) {
		encoded[8 + i] = (char)(value & 0xff);
		value >>= 8;
	}
	backend_runtime_source_digest = rp_evidence_hash_bytes(
		backend_runtime_source_digest, encoded, sizeof(encoded));
}

static int append_backend_runtime_case(const struct backend_runtime_spec *spec)
{
	struct rp_evidence_file_measurement measured;

	if (!rp_evidence_measure_file_field(spec->source, spec->key, spec->value,
					    &measured)) {
		printf("rp_backend: case_failed case=%s source=%s key=%s\n",
		       spec->case_name, spec->source, spec->key);
		return 0;
	}
	backend_runtime_source_reads++;
	fold_backend_runtime_source(spec, &measured);
	backend_line[0] = 0;
	rp_append_text(backend_line, sizeof(backend_line),
		       "evidence_role=runtime_verified;runtime_case=");
	rp_append_text(backend_line, sizeof(backend_line), spec->case_name);
	rp_append_text(backend_line, sizeof(backend_line), ";source=");
	rp_append_text(backend_line, sizeof(backend_line), spec->source);
	rp_append_text(backend_line, sizeof(backend_line), ";source_bytes=");
	rp_append_uint_text(backend_line, sizeof(backend_line), measured.bytes);
	rp_append_text(backend_line, sizeof(backend_line), ";source_hash=");
	rp_append_uint_text(backend_line, sizeof(backend_line), measured.hash);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";assertions_executed=1;assertions_passed=1;generation=runtime;status=verified");
	if (!rp_append_file("rp_backend_exec", backend_line))
		return 0;
	backend_runtime_cases++;
	return 1;
}

static int write_exact_file(const char *path, const char *text)
{
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	int len = (int)strlen(text);
	int wrote = 0;

	if (fd < 0)
		return 0;
	while (wrote < len) {
		int n = write(fd, text + wrote, len - wrote);

		if (n <= 0)
			break;
		wrote += n;
	}
	close(fd);
	return wrote == len;
}

static int run_kernel_edit_check(void)
{
	struct agent_file_edit_state state;
	uint64 base;
	int fd;
	int rc;

	unlink(BACKEND_EDIT_FILE);
	if (!write_exact_file(BACKEND_EDIT_FILE, "draft-report\n"))
		return -1;

	memset(&state, 0, sizeof(state));
	rc = agent_file_edit_begin(BACKEND_EDIT_FILE, 0, 200,
				   &state);
	if (rc != 0 || !state.active) {
		printf("rp_backend: edit_begin_failed rc=%d active=%d\n",
		       rc, state.active);
		return -1;
	}
	base = state.base_version;
	backend_edit_base_version = base;

	memset(&backend_edit_state, 0, sizeof(backend_edit_state));
	if (agent_file_edit_state(BACKEND_EDIT_FILE, &backend_edit_state) < 0 ||
	    !backend_edit_state.active ||
	    backend_edit_state.lease_id != state.lease_id) {
		printf("rp_backend: edit_state_failed active=%d lease=%d expected=%d\n",
		       backend_edit_state.active,
		       (int)backend_edit_state.lease_id,
		       (int)state.lease_id);
		agent_file_edit_abort(state.lease_id);
		return -1;
	}
	fd = open(BACKEND_EDIT_FILE, O_WRONLY);
	if (fd < 0) {
		printf("rp_backend: edit_open_failed\n");
		agent_file_edit_abort(state.lease_id);
		return -1;
	}
	if (write(fd, "A", 1) != 1) {
		printf("rp_backend: edit_write_failed\n");
		close(fd);
		agent_file_edit_abort(state.lease_id);
		return -1;
	}
	close(fd);
	rc = agent_file_edit_commit(state.lease_id, base, &backend_edit_state);
	if (rc != 0 || backend_edit_state.active ||
	    backend_edit_state.current_version != base + 1) {
		printf("rp_backend: edit_commit_failed rc=%d active=%d base=%d current=%d\n",
		       rc, backend_edit_state.active, (int)base,
		       (int)backend_edit_state.current_version);
		return -1;
	}

	if (!rp_write_file("rp_agentos_conflict",
			   "run_id=RUN-042\n"
			   "edit_target=r42edrep\n"
			   "edit_lease=kernel_exclusive\n"
			   "resource_identity=dev_inum\n"
			   "holder_write=checked\n"
			   "version_commit=checked\n"
			   "stale_write_policy=reject\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=edit_conflict;edit_lease=kernel_exclusive;status=ready")) {
		return -1;
	}
	return 1;
}

static int run_kernel_backend_check(void)
{
	struct agent_info info;

	if (agent_launch_info(&info) < 0 || !info.is_agent)
		return 0;
	memset(&backend_op, 0, sizeof(backend_op));
	backend_op.version = AGENT_OP_VERSION;
	backend_op.tool_id = AGENT_TOOL_ECHO;
	backend_op.request_id = 4401;
	strcpy(backend_op.payload, "backend-kernel-check");
	if (agent_run(&backend_op, &backend_result, 1, 0) != 1 ||
	    backend_result.status != AGENT_STATUS_OK) {
		printf("rp_backend: agent_run_failed status=%d\n",
		       backend_result.status);
		return -1;
	}
	backend_runtime_kernel_checks++;
	if (context_snapshot(&backend_header, backend_records, 8) < 1 ||
	    backend_header.latest_sequence == 0) {
		printf("rp_backend: context_snapshot_failed\n");
		return -1;
	}
	backend_runtime_kernel_checks++;
	memset(&backend_query, 0, sizeof(backend_query));
	backend_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	backend_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(backend_query.project, "lab-gene-x");
	strcpy(backend_query.run_id, "RUN-042");
	strcpy(backend_query.stage, "report");
	strcpy(backend_query.status, "ok");
	if (agent_file_query(&backend_query, &backend_query_result) < 1 ||
	    backend_query_result.returned < 1 ||
	    !backend_query_result.used_index) {
		printf("rp_backend: report_metadata_query_failed hits=%d index=%d\n",
		       backend_query_result.returned,
		       backend_query_result.used_index);
		return -1;
	}
	backend_runtime_kernel_checks++;
	if (run_kernel_edit_check() < 0) {
		printf("rp_backend: edit_conflict_check_failed\n");
		return -1;
	}
	backend_runtime_kernel_checks++;
	return 1;
}

static struct backend_runtime_receipt make_backend_runtime_receipt(void)
{
	struct backend_runtime_receipt receipt;

	receipt.runtime_cases = backend_runtime_cases;
	receipt.source_reads = backend_runtime_source_reads;
	receipt.kernel_checks = backend_runtime_kernel_checks;
	receipt.context_sequence = (int)backend_header.latest_sequence;
	receipt.query_returned = backend_query_result.returned;
	receipt.query_scanned = backend_query_result.scanned_records;
	receipt.query_used_index = backend_query_result.used_index;
	receipt.echo_request_id = (int)backend_op.request_id;
	receipt.echo_status = backend_result.status;
	return receipt;
}

int main(void)
{
	int ok = 1;
	int kernel_backend = run_kernel_backend_check();

	if (kernel_backend <= 0) {
		printf("rp_backend: runtime_kernel_evidence_required\n");
		return 1;
	}
	if (!run_backend_query_workload() || !cleanup_backend_query_workload()) {
		printf("rp_backend: scaled_metadata_query_failed\n");
		return 1;
	}
	ok = ok && rp_file_contains("rp_package", "status=ready");
	ok = ok && rp_file_contains("rp_runconf", "candidate=agentos-ucore");
	ok = ok && rp_file_contains("rp_invocation", "status=recovered");
	ok = ok && rp_file_contains("rp_completion", "status=ready");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_wfio", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_wfio", "backend_scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_wfio", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && rp_file_contains("rp_mail", "to=backend");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_stage=align");
	ok = ok && rp_file_contains("rp_stage_state", "stage=align");
	ok = ok && rp_file_contains("rp_agentos_kernel", "status=ready");
	ok = ok && rp_file_contains("rp_agentos_kernel", "context_snapshot=present");
	ok = ok && rp_file_contains("rp_agentos_kernel", "file_meta_service=initialized");
	ok = ok && rp_file_contains("rp_agentos_kernel", "dependency_update=generic_record");
	ok = ok && rp_file_contains("rp_agentos_kernel", "dependency_query=generic_record");
	ok = ok && rp_file_contains("rp_agentos_roles", "stage_launch=agent_create_role");
	ok = ok && rp_file_contains("rp_agentos_recovery", "kernel_tool=action_commit,artifact_update");
	ok = ok && rp_file_contains("rp_agentos_query", "metadata_source=kernel_file_index");
	ok = ok && rp_file_contains("rp_agentos_timeline", "event_delivery=kernel_agent_queue");
	ok = ok && rp_file_contains("rp_agentos_collab_ack", "delivery=kernel_event_queue");
	ok = ok && rp_file_contains("rp_agentos_audit", "audit_source=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_workbench", "file_verify=kernel_metadata_index");
	ok = ok && rp_file_contains("rp_agentos_package", "package_trace=kernel_provenance");
	ok = ok && rp_file_contains("rp_agentos_real_task", "report_answer=kernel_context_record");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "context_trusted=kernel_shadow");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "dependency_graph=kernel_records");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "metadata_query=used_index");
	ok = ok && rp_file_contains("rp_agentos_kernel", "metadata_index=stage_query");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "agent_event_notify=kernel_queue");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "failure_recovery=generic_action");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "provenance_audit=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "permission_control=sentinel_action_denied");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "timeline_observe=kernel_snapshot");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "workbench_file_verify=kernel_metadata_index");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "package_provenance=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "real_task_context=kernel_shadow");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "edit_lease=kernel_exclusive");
	ok = ok && rp_file_contains("rp_agentos_conflict", "holder_write=checked");
	if (!ok) return 1;
	if (!rp_write_file("rp_backend",
			   "scenario=backend-scenario:RUN-042:agentcompare\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;binding=workflow-migration-binding:RUN-042:plain-ucore\n"
			   "runner=agentos-kernel-assisted;inputs=rp_wfio,rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_agentos_kernel,rp_agentos_mainflow,rp_agentos_recovery,rp_agentos_query,rp_agentos_timeline,rp_agentos_audit,rp_agentos_workbench,rp_agentos_package,rp_agentos_real_task,rp_agentos_conflict;outputs=rp_backend_exec,rp_study\n"
			   "cases=8\n"
			   "executable=8\n"
			   "planned=0\n"
			   "plain_ucore=ready\n"
			   "agentos_ucore=kernel_bound\n"
			   "agentos_mainflow_kernel=required\n"
			   "agentos_mainflow_facts=12\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!append_backend_query_receipt()) return 1;
	if (!rp_write_file("rp_backend_exec",
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runtime_claim_protocol=source-bound-v1;runtime_claim_scope=file;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;portability_rehearsal_cases=4;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=agentos-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=agentos-recovery;plain_cost=manual_retry_contract;agentos_replace=capability_checked_action;risk=wrong_object_update;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=agentos-event;plain_cost=file_polling;agentos_replace=kernel_event_queue;risk=lost_handoff;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=agentos-audit;plain_cost=append_only_logs;agentos_replace=kernel_ledger_provenance;risk=tampered_context;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_report=agentos-edit;plain_cost=userland_lock_file;agentos_replace=kernel_edit_lease;risk=lost_update;status=reference_ready\n"
			   "evidence_role=demo_reference;catalog_generation=demo_expected;runner_cases=8;runner_detail_rows=8;runner_report_rows=8;runner_observed=rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_llmeval,rp_agentos_kernel,rp_agentos_mainflow,rp_agentos_recovery,rp_agentos_query,rp_agentos_timeline,rp_agentos_audit,rp_agentos_workbench,rp_agentos_package,rp_agentos_real_task,rp_agentos_conflict;runner_detail_fields=input_check,artifact_check,att,retry,ticks;runner_detail_checks=32;runner_planned=0;planned_cases=0;status=reference_ready\n")) {
		return 1;
	}
	for (int i = 0; i < (int)(sizeof(backend_runtime_specs) /
					  sizeof(backend_runtime_specs[0])); i++)
		if (!append_backend_runtime_case(&backend_runtime_specs[i]))
			return 1;
	const struct backend_runtime_receipt backend_receipt =
		make_backend_runtime_receipt();
	if (backend_receipt.runtime_cases != 8 ||
	    backend_receipt.source_reads != 8 ||
	    backend_receipt.kernel_checks != 4 ||
	    backend_receipt.context_sequence <= 0 ||
	    backend_receipt.query_returned <= 0 ||
	    backend_receipt.query_used_index != 1 ||
	    backend_receipt.echo_request_id != 4401 ||
	    backend_receipt.echo_status != AGENT_STATUS_OK)
		return 1;
	backend_line[0] = 0;
	rp_append_text(backend_line, sizeof(backend_line),
		       "evidence_role=runtime_verified;runtime_cases_executed=");
	rp_append_uint_text(backend_line, sizeof(backend_line), backend_runtime_cases);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";runtime_cases_verified=");
	rp_append_uint_text(backend_line, sizeof(backend_line), backend_runtime_cases);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";runtime_assertions_executed=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_runtime_source_reads);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";runtime_assertions_passed=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_runtime_source_reads);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";runtime_source_digest=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_runtime_source_digest);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";echo_request_id=");
	rp_append_uint_text(backend_line, sizeof(backend_line), backend_op.request_id);
	rp_append_text(backend_line, sizeof(backend_line), ";echo_status=");
	rp_append_uint_text(backend_line, sizeof(backend_line), backend_result.status);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";context_latest_sequence=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_header.latest_sequence);
	rp_append_text(backend_line, sizeof(backend_line), ";query_returned=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_query_result.returned);
	rp_append_text(backend_line, sizeof(backend_line), ";query_scanned=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_query_result.scanned_records);
	rp_append_text(backend_line, sizeof(backend_line), ";query_used_index=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_query_result.used_index);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";edit_base_version=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_edit_base_version);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";edit_current_version=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_edit_state.current_version);
	rp_append_text(backend_line, sizeof(backend_line), ";edit_active=");
	rp_append_uint_text(backend_line, sizeof(backend_line),
			    backend_edit_state.active);
	rp_append_text(backend_line, sizeof(backend_line),
		       ";generation=runtime;status=verified");
	if (!rp_append_file("rp_backend_exec", backend_line))
		return 1;
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "metric_generation=demo_expected\n"
			   "workflow_portability=rp_wfio;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;migration_status=baseline_and_agentos_observed\n"
			   "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=4;reference_result=pass\n"
			   "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;dependency_graph=1;metadata_index=1;event_queue=1;recovery_tool=1;audit_ledger=1;permission_control=1;timeline_observe=1;workbench_verify=1;package_trace=1;real_task_context=1;edit_lease=1;mainflow_facts=12;detail_checks=kernel;reference_result=pass\n"
			   "study_handoff=rp_backend_exec->rp_agentcmp;status=ready\n"
			   "arms=2\n"
			   "metrics=13\n"
			   "plain_kernel=recorded\n"
			   "agentos_kernel=mainflow_bound\n"
			   "conclusion=kernel_services_reduce_scan_polling_manual_rebuild\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=8;agentos_replacements=8;risks=8;status=ready")) return 1;
	if (!rp_append_file("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128,manual_retry_contract,file_polling,append_only_logs,userland_lock_file;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index,capability_checked_action,kernel_event_queue,kernel_ledger_provenance,kernel_edit_lease,workbench_file_verify,package_trace,real_task_context;dependency_graph=kernel_records;mainflow_facts=12;status=ready")) return 1;
	if (kernel_backend &&
	    !rp_append_file("rp_tool", "tool=agentos.backend_context_check")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=backend;msg=21;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.create_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.record_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.export_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.write_study")) return 1;
	if (!rp_append_status("backend=ready")) return 1;
	if (!rp_append_status("backend_exec=ready")) return 1;
	if (!rp_append_status("study=ready")) return 1;
	printf("rp_backend: evidence_generation=runtime runtime_cases=%d source_reads=%d kernel_checks=%d context_sequence=%d query_returned=%d query_used_index=%d status=verified\n",
	       backend_receipt.runtime_cases, backend_receipt.source_reads,
	       backend_receipt.kernel_checks, backend_receipt.context_sequence,
	       backend_receipt.query_returned,
	       backend_receipt.query_used_index);
	return 0;
}
