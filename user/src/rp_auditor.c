#include <agent.h>
#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>
#include <rp_evidence.h>
#include <unistd.h>

#define AUDITOR_AUDIT_CAP 128
#define AUDITOR_EDGE_CAP 128
#define AUDITOR_ECHO_MARKER_1 "audit-e2e-1"
#define AUDITOR_ECHO_MARKER_2 "audit-e2e-2"

_Static_assert(sizeof(AUDITOR_ECHO_MARKER_1) <= AGENT_CONTEXT_TEXT_SIZE &&
	       sizeof(AUDITOR_ECHO_MARKER_2) <= AGENT_CONTEXT_TEXT_SIZE,
	       "auditor markers must survive Context projection");

static struct agent_audit_record kernel_audit_records[AUDITOR_AUDIT_CAP];
static struct agent_provenance_edge kernel_edges[AUDITOR_EDGE_CAP];
static struct agent_ledger_summary kernel_ledger;
static struct agent_context_header kernel_header;
static struct agent_context_record kernel_records[8];
static struct agent_op auditor_ops[2];
static struct agent_result auditor_results[2];
static char auditor_evidence_body[2048];
static int auditor_audit_count;
static int auditor_edge_count;
static int auditor_edge_total;
static int auditor_context_count;
static int auditor_identity_count;
static int auditor_projection_boundaries;

static uint64 auditor_hash_mix(uint64 hash, uint64 value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= (unsigned char)(value & 0xff);
		hash *= RP_EVIDENCE_FNV_PRIME;
		value >>= 8;
	}
	return hash;
}

static uint64 auditor_hash_fixed(uint64 hash, char *text, int n)
{
	for (int i = 0; i < n; i++) {
		hash ^= (unsigned char)text[i];
		hash *= RP_EVIDENCE_FNV_PRIME;
	}
	return hash;
}

static uint64 auditor_context_hash(struct agent_context_record *record)
{
	uint64 hash = RP_EVIDENCE_FNV_OFFSET;

	hash = auditor_hash_mix(hash, record->prev_hash);
	hash = auditor_hash_mix(hash, record->sequence);
	hash = auditor_hash_mix(hash, record->request_id);
	hash = auditor_hash_mix(hash, record->cause_sequence);
	hash = auditor_hash_mix(hash, record->span_id);
	hash = auditor_hash_mix(hash, record->branch_generation);
	hash = auditor_hash_mix(hash, record->path_parent_sequence);
	hash = auditor_hash_mix(hash, record->arg0);
	hash = auditor_hash_mix(hash, record->value0);
	hash = auditor_hash_mix(hash, record->value1);
	hash = auditor_hash_mix(hash, record->value2);
	hash = auditor_hash_mix(hash, record->tick);
	hash = auditor_hash_mix(hash, record->flags);
	hash = auditor_hash_mix(hash, (uint64)(uint)record->tool_id);
	hash = auditor_hash_mix(hash, (uint64)(uint)record->status);
	hash = auditor_hash_fixed(hash, record->payload,
				  sizeof(record->payload));
	hash = auditor_hash_fixed(hash, record->result,
				  sizeof(record->result));
	return hash ? hash : 1;
}

static int run_kernel_audit(void)
{
	struct agent_info info;
	struct agent_audit_record *first_audit = 0;
	struct agent_audit_record *second_audit = 0;
	struct agent_context_record *first = 0;
	struct agent_context_record *second = 0;
	int exact_audit_marker_1 = 0;
	int exact_audit_marker_2 = 0;
	int exact_edge = 0;
	int self_pid = getpid();

	if (agent_launch_info(&info) < 0 || !info.is_agent)
		return 0;
	if ((info.capability_mask & AGENT_CAP_AUDIT_WRITE) == 0) {
		printf("rp_auditor: audit_capability_missing\n");
		return -1;
	}
	memset(auditor_ops, 0, sizeof(auditor_ops));
	memset(auditor_results, 0, sizeof(auditor_results));
	for (int i = 0; i < 2; i++) {
		auditor_ops[i].version = AGENT_OP_VERSION;
		auditor_ops[i].tool_id = AGENT_TOOL_ECHO;
		auditor_ops[i].request_id = 4101 + i;
	}
	strcpy(auditor_ops[0].payload, AUDITOR_ECHO_MARKER_1);
	strcpy(auditor_ops[1].payload, AUDITOR_ECHO_MARKER_2);
	if (agent_run(auditor_ops, auditor_results, 2, 0) != 2 ||
	    auditor_results[0].status != AGENT_STATUS_OK ||
	    auditor_results[1].status != AGENT_STATUS_OK ||
	    auditor_results[0].sequence == 0 ||
	    auditor_results[1].sequence <= auditor_results[0].sequence) {
		printf("rp_auditor: agent_run_failed status=%d/%d\n",
		       auditor_results[0].status, auditor_results[1].status);
		return -1;
	}
	auditor_audit_count = agent_audit_snapshot(kernel_audit_records,
						   AUDITOR_AUDIT_CAP);
	auditor_edge_total = agent_provenance_snapshot(0, 0);
	auditor_edge_count = agent_provenance_snapshot(kernel_edges,
						   AUDITOR_EDGE_CAP);
	auditor_context_count = context_snapshot(&kernel_header, kernel_records, 8);
	if (auditor_audit_count < 2 || auditor_edge_total < 1 ||
	    auditor_edge_count < 1 || auditor_context_count < 2 ||
	    agent_ledger_snapshot(&kernel_ledger) < 0 ||
	    kernel_ledger.version != AGENT_LEDGER_VERSION ||
	    kernel_ledger.visible_records < (uint64)auditor_audit_count ||
	    kernel_ledger.total_records < kernel_ledger.visible_records ||
	    kernel_ledger.total_records < (uint64)auditor_context_count ||
	    kernel_ledger.dropped_records !=
		    kernel_ledger.total_records -
			    kernel_ledger.visible_records ||
	    kernel_ledger.latest_sequence < kernel_ledger.oldest_sequence ||
	    kernel_ledger.ledger_hash == 0 || kernel_ledger.context_records < 2) {
		printf("rp_auditor: kernel_audit_snapshot_failed audit=%d edge=%d context=%d\n",
		       auditor_audit_count, auditor_edge_count,
		       auditor_context_count);
		return -1;
	}
	auditor_identity_count = 0;
	auditor_projection_boundaries = 0;
	for (int i = 0; i < auditor_audit_count; i++) {
		struct agent_audit_record *record = &kernel_audit_records[i];

		if (record->sequence == 0 || record->record_hash == 0 ||
		    (i > 0 && record->sequence <=
				      kernel_audit_records[i - 1].sequence)) {
			printf("rp_auditor: audit_identity_failed at=%d\n", i);
			return -1;
		}
		auditor_identity_count++;
		if (i > 0 && record->prev_hash !=
				      kernel_audit_records[i - 1].record_hash)
			auditor_projection_boundaries++;
		if (record->kind == AGENT_AUDIT_KIND_CONTEXT &&
		    record->pid == self_pid && record->tool_id == AGENT_TOOL_ECHO &&
		    record->status == AGENT_STATUS_OK) {
			if (strcmp(record->text, AUDITOR_ECHO_MARKER_1) == 0) {
				exact_audit_marker_1++;
				first_audit = record;
			}
			if (strcmp(record->text, AUDITOR_ECHO_MARKER_2) == 0) {
				exact_audit_marker_2++;
				second_audit = record;
			}
		}
	}
	if (exact_audit_marker_1 != 1 || exact_audit_marker_2 != 1) {
		printf("rp_auditor: ledger_projection_failed exact=%d/%d boundaries=%d dropped=%d\n",
		       exact_audit_marker_1, exact_audit_marker_2,
		       auditor_projection_boundaries,
		       (int)kernel_ledger.dropped_records);
		return -1;
	}
	for (int i = 0; i < auditor_context_count; i++) {
		struct agent_context_record *record = &kernel_records[i];

		if (record->record_hash == 0 ||
		    auditor_context_hash(record) != record->record_hash) {
			printf("rp_auditor: context_hash_failed at=%d\n", i);
			return -1;
		}
		if (record->sequence == auditor_results[0].sequence)
			first = record;
		if (record->sequence == auditor_results[1].sequence)
			second = record;
	}
	if (first_audit == 0 || second_audit == 0 ||
	    first == 0 || second == 0 ||
	    first->request_id != auditor_ops[0].request_id ||
	    second->request_id != auditor_ops[1].request_id ||
	    first->tool_id != AGENT_TOOL_ECHO ||
	    second->tool_id != AGENT_TOOL_ECHO ||
	    first_audit->prev_hash != first->prev_hash ||
	    second_audit->prev_hash != second->prev_hash ||
	    first_audit->record_hash != first->record_hash ||
	    second_audit->record_hash != second->record_hash ||
	    strcmp(first->payload, AUDITOR_ECHO_MARKER_1) != 0 ||
	    strcmp(first->result, AUDITOR_ECHO_MARKER_1) != 0 ||
	    strcmp(second->payload, AUDITOR_ECHO_MARKER_2) != 0 ||
	    strcmp(second->result, AUDITOR_ECHO_MARKER_2) != 0 ||
	    second->cause_sequence != first->sequence) {
		printf("rp_auditor: context_cause_failed\n");
		return -1;
	}
	for (int i = 0; i < auditor_edge_count; i++) {
		struct agent_provenance_edge *edge = &kernel_edges[i];

		if (edge->source_sequence == 0 || edge->target_sequence == 0 ||
		    edge->target_sequence <= edge->source_sequence)
			continue;
		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->source_pid == self_pid && edge->target_pid == self_pid &&
		    edge->source_sequence == auditor_results[0].sequence &&
		    edge->target_sequence == auditor_results[1].sequence &&
		    edge->source_record_hash == first->record_hash &&
		    edge->target_record_hash == second->record_hash &&
		    edge->tool_id == AGENT_TOOL_ECHO &&
		    edge->status == AGENT_STATUS_OK)
			exact_edge++;
	}
	if (exact_edge != 1) {
		printf("rp_auditor: exact_provenance_missing matches=%d total=%d\n",
		       exact_edge, auditor_edge_total);
		return -1;
	}

	char *body = auditor_evidence_body;
	body[0] = 0;
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "evidence_source=", "kernel_snapshots");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "evidence_generation=", "runtime");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "audit_source=", "kernel_ledger");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "context_source=", "kernel_shadow");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "provenance_source=", "kernel_edges");
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "audit_records=", auditor_audit_count);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "audit_record_identities_verified=",
			       auditor_identity_count);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "audit_projection_boundaries=",
			       auditor_projection_boundaries);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "context_records=", auditor_context_count);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "provenance_edges=", auditor_edge_total);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "provenance_edges_copied=", auditor_edge_count);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "verified_cause_sequence=",
			       auditor_results[0].sequence);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "verified_target_sequence=",
			       auditor_results[1].sequence);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "ledger_visible_records=",
			       kernel_ledger.visible_records);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "ledger_total_records=", kernel_ledger.total_records);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "ledger_hash=", kernel_ledger.ledger_hash);
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "ledger_hash_kind=", "evidence_view_digest_tag");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "record_hash=", "verified");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "provenance_cause=", "verified");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "status=", "verified");
	if (!rp_write_file("rp_agentos_audit", body)) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=audit;provenance_audit=kernel_ledger;status=ready"))
		return -1;
	return 1;
}

int main(void)
{
	static const struct {
		const char *path;
		const char *token;
	} state_expectations[] = {
		{"rp_plan", "assignments=7"},
		{"rp_lit", "status=ready"},
		{"rp_data", "failed_stage=align"},
		{"rp_review", "decision=accepted_after_repair"},
		{"rp_report", "status=packaged"},
		{"rp_fix", "status=recovered"},
		{"rp_fail", "failure_class=tool_output_missing"},
		{"rp_retrylog", "status=ready"},
		{"rp_datadic", "schema_drift=0"},
		{"rp_compute", "replay=ready"},
		{"rp_labops", "maintenance=passed"},
		{"rp_risk", "open_risks=0"},
		{"rp_capa", "verifications=2"},
		{"rp_mail", "to=auditor"},
	};
	int state_checks = 0;
	int kernel_audit = run_kernel_audit();
	char *body = auditor_evidence_body;

	if (kernel_audit < 0)
		return 1;
	for (int i = 0; i < (int)(sizeof(state_expectations) /
					  sizeof(state_expectations[0])); i++) {
		if (!rp_file_contains(state_expectations[i].path,
				      state_expectations[i].token))
			return 1;
		state_checks++;
	}
	body[0] = 0;
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "evidence_source=", "runtime_state_files");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "evidence_generation=", "runtime");
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "state_checks=", state_checks);
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "provenance=",
				 kernel_audit ? "verified" :
						"userland_state_validated");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "agentos_audit=",
				 kernel_audit ? "kernel_ledger" : "not_available");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "agentos_context=",
				 kernel_audit ? "kernel_shadow" : "not_available");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "agentos_provenance=",
				 kernel_audit ? "kernel_edges" : "not_available");
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "kernel_audit_records=",
			       kernel_audit ? auditor_audit_count : 0);
	rp_evidence_append_u64(body, sizeof(auditor_evidence_body),
			       "kernel_provenance_edges=",
			       kernel_audit ? auditor_edge_total : 0);
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "release=", "ready");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "package=", "ready");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "schema=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "replay=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "labops=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "risk=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "capa=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "retry=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "failure=", "state_match");
	rp_evidence_append_value(body, sizeof(auditor_evidence_body),
				 "status=", "passed");
	if (!rp_write_file("rp_audit", body)) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=auditor;msg=7;status=passed")) return 1;
	if (!rp_append_file("rp_tool", "tool=auditor.verify_provenance")) return 1;
	if (kernel_audit &&
	    !rp_append_file("rp_tool", "tool=agentos.audit_snapshot")) {
		return 1;
	}
	if (kernel_audit &&
	    !rp_append_file("rp_tool", "tool=agentos.provenance_snapshot")) {
		return 1;
	}
	if (!rp_append_status("auditor=passed")) return 1;
	printf("rp_auditor: evidence_source=%s generation=runtime audit_records=%d provenance_edges=%d record_identities_verified=%d state_checks=%d status=passed\n",
	       kernel_audit ? "kernel_snapshots" : "userland_state_files",
	       kernel_audit ? auditor_audit_count : 0,
	       kernel_audit ? auditor_edge_total : 0,
	       kernel_audit ? auditor_identity_count : 0, state_checks);
	return 0;
}
