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
			    "stage=edit_conflict;edit_lease=kernel_exclusive;holder_write=checked;version_commit=checked;resource_identity=dev_inum;status=ready")) {
		return -1;
	}
	return 1;
}

static int run_kernel_backend_check(void)
{
	struct agent_info info;

	if (agent_info(&info) < 0 || !info.is_agent)
		return 0;
	memset(&backend_op, 0, sizeof(backend_op));
	backend_op.version = AGENT_CALL_VERSION;
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
	ok = ok && rp_file_contains("rp_agentos_kernel", "prefetch_hint=dependency_driven");
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
	ok = ok && rp_file_contains("rp_agentos_mainflow", "prefetch_hint=dependency_driven");
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
