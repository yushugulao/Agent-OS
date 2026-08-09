#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#define RP_STATE_BUFFER_SIZE 32768
#include <research_platform_state.h>

#define BACKEND_QUERY_RECORDS 12
#define BACKEND_QUERY_OPERATIONS 4096
#define BACKEND_QUERY_RESULTS 1
#define BACKEND_QUERY_MAGIC 0x5250515545525931ULL
#define BACKEND_QUERY_FNV_OFFSET 1469598103934665603ULL
#define BACKEND_QUERY_FNV_PRIME 1099511628211ULL

struct backend_query_record {
	unsigned long long magic;
	unsigned long long dependency_mask;
	unsigned long long record_hash;
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

static struct backend_query_record backend_query_record;
static struct backend_query_record backend_query_hits[BACKEND_QUERY_RESULTS];
static unsigned long long backend_query_digest;
static unsigned long long backend_query_records_examined;
static int backend_query_matches;
static char backend_query_line[256];
static const char *const backend_query_stages[BACKEND_QUERY_RECORDS] = {
	"summarize", "package", "join", "evaluate", "transform", "prepare",
	"ingest", "export", "aggregate", "derive", "normalize", "collect",
};

static unsigned long long backend_hash_bytes(unsigned long long hash,
					      const void *data, int length)
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

static unsigned long long backend_record_hash(
	const struct backend_query_record *record)
{
	unsigned long long hash = BACKEND_QUERY_FNV_OFFSET;

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
	rp_copy_text(record->logical_path, sizeof(record->logical_path),
		     record->physical_name);
	rp_copy_text(record->project, sizeof(record->project), "lab-gene-x");
	rp_copy_text(record->workflow, sizeof(record->workflow),
		     "research-query");
	rp_copy_text(record->run_id, sizeof(record->run_id), "RUN-042");
	rp_copy_text(record->stage, sizeof(record->stage),
		     backend_query_stages[index]);
	rp_copy_text(record->kind, sizeof(record->kind), "artifact");
	rp_copy_text(record->status, sizeof(record->status), "ready");
	rp_copy_text(record->summary, sizeof(record->summary),
		     "scaled research metadata");
	record->record_hash = backend_record_hash(record);
}

static int backend_record_valid(const struct backend_query_record *record,
				int index)
{
	char name[8];
	char status[8];

	backend_make_code(name, 'm', index);
	rp_copy_text(status, sizeof(status), "ready");
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

static int backend_read_record(const char *name,
			       struct backend_query_record *record)
{
	int fd = open(name, O_RDONLY);
	int done = 0;

	if (fd < 0)
		return 0;
	while (done < (int)sizeof(*record)) {
		int n = read(fd, (char *)record + done, sizeof(*record) - done);

		if (n <= 0)
			break;
		done += n;
	}
	return close(fd) == 0 && done == (int)sizeof(*record);
}

static int run_backend_query_workload(void)
{
	backend_query_digest = BACKEND_QUERY_FNV_OFFSET;
	backend_query_records_examined = 0;
	backend_query_matches = 0;
	for (int i = 0; i < BACKEND_QUERY_RECORDS; i++) {
		backend_build_record(&backend_query_record, i);
		if (!backend_write_record(&backend_query_record))
			return 0;
	}
	for (int operation = 0; operation < BACKEND_QUERY_OPERATIONS;
	     operation++) {
		int target = (operation * 5 + 3) % BACKEND_QUERY_RECORDS;
		int matches = 0;
		const char *target_stage = backend_query_stages[target];
		memset(backend_query_hits, 0, sizeof(backend_query_hits));
		for (int item = 0; item < BACKEND_QUERY_RECORDS; item++) {
			char name[8];

			backend_make_code(name, 'm', item);
			if (!backend_read_record(name, &backend_query_record) ||
			    !backend_record_valid(&backend_query_record, item))
				return 0;
			backend_query_records_examined++;
			if (strcmp(backend_query_record.project, "lab-gene-x") == 0 &&
			    strcmp(backend_query_record.workflow, "research-query") == 0 &&
			    strcmp(backend_query_record.run_id, "RUN-042") == 0 &&
			    strcmp(backend_query_record.stage, target_stage) == 0 &&
			    strcmp(backend_query_record.status, "ready") == 0) {
				if (matches >= BACKEND_QUERY_RESULTS)
					return 0;
				backend_query_hits[matches] = backend_query_record;
				matches++;
			}
		}
		if (matches != BACKEND_QUERY_RESULTS)
			return 0;
		if (!backend_record_valid(&backend_query_hits[0], target))
			return 0;
		backend_query_digest = backend_hash_bytes(
			backend_query_digest,
			&backend_query_hits[0].record_hash,
			sizeof(backend_query_hits[0].record_hash));
		backend_query_matches += matches;
	}
	if (backend_query_matches != BACKEND_QUERY_OPERATIONS)
		return 0;
	for (int i = 0; i < BACKEND_QUERY_RECORDS; i++) {
		char name[8];

		backend_make_code(name, 'm', i);
		if (unlink(name) < 0)
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
		       ";backend=plain_file_scan;status=verified");
	return rp_append_file("rp_backend", backend_query_line);
}

int main(void)
{
	int ok = 1;
	if (!run_backend_query_workload()) return 1;
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
	if (!ok) return 1;
	if (!rp_write_file("rp_backend",
			   "scenario=backend-scenario:RUN-042:agentcompare\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;binding=workflow-migration-binding:RUN-042:plain-ucore\n"
			   "runner=active-user-space;inputs=rp_wfio,rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_query,rp_run_events,rp_audit;outputs=rp_backend_exec,rp_study\n"
			   "reference_cases=7\n"
			   "catalog_entries=7\n"
			   "runtime_cases=0\n"
			   "runtime_evidence=not_claimed\n"
			   "plain_ucore=ready\n"
			   "userland_equivalents=ready\n"
			   "agentos_ucore=kernel_comparison_target\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!append_backend_query_receipt()) return 1;
	if (!rp_write_file("rp_backend_exec",
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "catalog_entries=7\n"
			   "runtime_cases=0\n"
			   "runtime_pass_rows=0\n"
			   "performance_samples=0\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;reference_case=plain-ucore;source=rp_wfio;expected_status=available;reference_case=agentos-ucore;source=rp_wfio;expected_status=kernel_target;portability_rehearsal_cases=4;reference_cases=7\n"
			   "reference_case=plain-ucore;expected_input=rp_wfio;expected_artifact=rp_artifact_manifest;expected_outcome=native_programs_ok;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=retry-recovery;expected_input=rp_retry_plan;expected_artifact=rp_stage_state;expected_outcome=recovered_align;expected_attempts=2;expected_retry=tool_output_missing;status=reference_ready\n"
			   "reference_case=user-context;expected_input=rp_query;expected_artifact=rp_provpath;expected_outcome=user_space_context_log;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=user-fsmeta;expected_input=rp_artifact_manifest;expected_artifact=rp_query;expected_outcome=file_manifest_scan;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=user-recovery;expected_input=rp_retrylog;expected_artifact=rp_fix;expected_outcome=user_space_repair_record;expected_attempts=2;expected_retry=tool_output_missing;status=reference_ready\n"
			   "reference_case=user-event;expected_input=rp_worker+rp_timeline;expected_artifact=rp_agent_run;expected_outcome=file_backed_event_log;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=user-audit;expected_input=rp_audit+rp_provpath;expected_artifact=rp_package;expected_outcome=append_only_audit_files;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=reference_ready\n"
			   "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=reference_ready\n"
			   "runner_report=user-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=reference_ready\n"
			   "runner_report=user-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=reference_ready\n"
			   "runner_report=user-recovery;plain_cost=manual_retry_contract;agentos_replace=capability_checked_action;risk=wrong_object_update;status=reference_ready\n"
			   "runner_report=user-event;plain_cost=file_polling;agentos_replace=kernel_event_queue;risk=lost_handoff;status=reference_ready\n"
			   "runner_report=user-audit;plain_cost=append_only_logs;agentos_replace=kernel_ledger_provenance;risk=tampered_context;status=reference_ready\n"
			   "reference_case_rows=7\n"
			   "reference_report_rows=7\n"
			   "indexed_candidate=ready\n"
			   "decision=reference_catalog_ready\n"
			   "evidence_file_status=reference_ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "metric_generation=demo_expected\n"
			   "workflow_portability=rp_wfio;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;migration_status=plain_userland_equivalents_ready\n"
			   "reference_metric=plain_ucore;expected_file_scans=128;expected_context_trusted=0;expected_rebuild_steps=6;status=reference_ready\n"
			   "reference_metric=agentos_ucore;expected_context_trusted=1;expected_batch_tools=1;expected_metadata_index=1;expected_event_queue=1;expected_recovery_tool=1;expected_audit_ledger=1;status=reference_ready\n"
			   "study_handoff=rp_backend_exec->rp_agentcmp;status=ready\n"
			   "arms=2\n"
			   "metrics=12\n"
			   "plain_kernel=recorded\n"
			   "agentos_kernel=target\n"
			   "conclusion=userland_equivalents_ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=7;agentos_replacements=7;risks=7;status=ready")) return 1;
	if (!rp_append_file("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128,manual_retry_contract,file_polling,append_only_logs;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index,capability_checked_action,kernel_event_queue,kernel_ledger_provenance;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=backend;msg=21;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.create_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.record_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.export_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.write_study")) return 1;
	if (!rp_append_status("backend=ready")) return 1;
	if (!rp_append_status("backend_exec=ready")) return 1;
	if (!rp_append_status("study=ready")) return 1;
	printf("rp_backend: evidence_role=demo_reference catalog_generation=demo_expected cases=7 status=reference_ready\n");
	return 0;
}
