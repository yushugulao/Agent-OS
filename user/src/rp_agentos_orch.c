#include <agent.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <io_policy.h>
#include <research_platform_state.h>
#include <rp_resource_stability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_info orch_info;
static struct agent_op orch_op;
static struct agent_result orch_result;
static struct agent_op orch_echo_op;
static struct agent_op orch_followup_op;
static struct agent_result orch_echo_result;
static struct agent_result orch_followup_result;
static struct agent_context_header orch_header;
static struct agent_context_record orch_records[4];
static struct agent_timeline_record orch_timeline_records[4];
static struct agent_provenance_edge orch_provenance_edges[4];
static struct agent_ledger_summary orch_ledger;
static struct agent_file_query orch_file_query;
static struct agent_file_query_result orch_file_query_result;
static struct agent_file_hit orch_metadata_hit;
static struct agent_file_prefetch_hint orch_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
static char orch_acceptance_body[4096];
static char orch_state_body[1024];
static char orch_stability_body[32768];
static char orch_check_token[160];
static struct rp_resource_stability_report
	orch_stability_reports[RP_RESOURCE_STABILITY_WORKFLOWS];
static struct agent_resource_snapshot
	orch_stability_global_before[RP_RESOURCE_STABILITY_WORKFLOWS];
static struct agent_resource_snapshot
	orch_stability_global_after[RP_RESOURCE_STABILITY_WORKFLOWS];
static const char *const orch_resource_kind_names[AGENT_RESOURCE_KIND_COUNT] = {
	"process", "thread", "file_object", "fs_block", "fs_inode",
	"buffer_cache", "agent_state_page", "physical_page",
};
static const uint64 orch_resource_growth_bounds[AGENT_RESOURCE_KIND_COUNT] = {
	[AGENT_RESOURCE_FS_BLOCK] =
		RP_RESOURCE_STABILITY_FS_BLOCK_GROWTH_BOUND,
	[AGENT_RESOURCE_BUFFER_CACHE] =
		RP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND,
};

struct rp_challenge_workflow {
	char suffix[16];
	char run_id[24];
	char rerun_id[32];
	char workflow_id[24];
	char input_sha256[65];
	char derived_sha256[65];
	char kernel_run_id[16];
	char echo_payload[32];
	char target_physical[32];
	char target_summary[AGENT_FILE_SUMMARY_SIZE];
	uint64 request_id;
};

static struct rp_challenge_workflow orch_workflow;
static struct rp_challenge_workflow orch_expected_workflow;
static struct rp_challenge_workflow orch_stability_workflow;

#define RP_WORKFLOW_HANDOFF_MAGIC 0x52505754U
#define RP_WORKFLOW_COMPLETION_MAGIC 0x52505743U
#define RP_WORKFLOW_HANDOFF_VERSION 1U
#define RP_WORKFLOW_HANDOFF_PREFIX "--rp-workflow-timing-fd="
#define RP_WORKFLOW_COMPLETION_PREFIX "--rp-workflow-completion-fd="
#define RP_WORKFLOW_PHASE_AGENT_CREATE   (1ULL << 0)
#define RP_WORKFLOW_PHASE_AGENT_INFO     (1ULL << 1)
#define RP_WORKFLOW_PHASE_CHALLENGE      (1ULL << 2)
#define RP_WORKFLOW_PHASE_METADATA       (1ULL << 3)
#define RP_WORKFLOW_PHASE_SCAFFOLD       (1ULL << 4)
#define RP_WORKFLOW_PHASE_ALL            ((1ULL << 5) - 1)

struct rp_workflow_handoff {
	uint magic;
	uint version;
	uint64 start_ms;
	uint64 ready_ms;
	uint64 steady_start_ms;
	uint64 phase_mask;
	uint64 guard;
};

static uint64 workflow_handoff_mix(uint64 hash, uint64 value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= value & 0xff;
		hash *= 1099511628211ULL;
		value >>= 8;
	}
	return hash;
}

static uint64 workflow_handoff_guard(const struct rp_workflow_handoff *record)
{
	uint64 hash = 1469598103934665603ULL;

	hash = workflow_handoff_mix(hash, record->magic);
	hash = workflow_handoff_mix(hash, record->version);
	hash = workflow_handoff_mix(hash, record->start_ms);
	hash = workflow_handoff_mix(hash, record->ready_ms);
	hash = workflow_handoff_mix(hash, record->steady_start_ms);
	return workflow_handoff_mix(hash, record->phase_mask);
}

static int write_workflow_handoff(int fd, uint64 start_ms, uint64 ready_ms,
				  uint64 phase_mask)
{
	struct rp_workflow_handoff record;
	const char *bytes = (const char *)&record;
	int written = 0;

	memset(&record, 0, sizeof(record));
	record.magic = RP_WORKFLOW_HANDOFF_MAGIC;
	record.version = RP_WORKFLOW_HANDOFF_VERSION;
	record.start_ms = start_ms;
	record.ready_ms = ready_ms;
	record.steady_start_ms = 0;
	record.phase_mask = phase_mask;
	record.guard = workflow_handoff_guard(&record);
	while (written < (int)sizeof(record)) {
		int n = write(fd, bytes + written, sizeof(record) - written);

		if (n <= 0)
			return 0;
		written += n;
	}
	return 1;
}

static int read_workflow_completion(int fd,
				    struct rp_workflow_handoff *record)
{
	char *bytes = (char *)record;
	char extra;
	int received = 0;

	memset(record, 0, sizeof(*record));
	while (received < (int)sizeof(*record)) {
		int n = read(fd, bytes + received, sizeof(*record) - received);

		if (n <= 0) {
			close(fd);
			return 0;
		}
		received += n;
	}
	if (read(fd, &extra, 1) > 0) {
		close(fd);
		return 0;
	}
	close(fd);
	return record->magic == RP_WORKFLOW_COMPLETION_MAGIC &&
	       record->version == RP_WORKFLOW_HANDOFF_VERSION &&
	       record->phase_mask == RP_WORKFLOW_PHASE_ALL &&
	       record->guard == workflow_handoff_guard(record) &&
	       record->ready_ms >= record->start_ms &&
	       record->steady_start_ms >= record->ready_ms;
}

static int record_workflow_timing(
	const struct rp_workflow_handoff *record, uint64 workflow_end)
{
	char line[640];
	uint64 setup_elapsed;
	uint64 exec_elapsed;
	uint64 steady_elapsed;
	uint64 workflow_elapsed;

	if (workflow_end < record->steady_start_ms)
		return 0;
	setup_elapsed = record->ready_ms - record->start_ms;
	exec_elapsed = record->steady_start_ms - record->ready_ms;
	steady_elapsed = workflow_end - record->steady_start_ms;
	workflow_elapsed = workflow_end - record->start_ms;
	rp_copy_text(line, sizeof(line),
		     "schema=guest_workflow_timing_v3;clock=monotonic_mtime_ms;entry=rp_agentos_orch;handoff=delegated_pipe_v1;init_phase_mask=");
	rp_append_uint_text(line, sizeof(line), record->phase_mask);
	rp_append_text(line, sizeof(line),
		       ";completion=parent_wait_final_validation;completion_phase_mask=3;start_ms=");
	rp_append_uint_text(line, sizeof(line), record->start_ms);
	rp_append_text(line, sizeof(line), ";ready_ms=");
	rp_append_uint_text(line, sizeof(line), record->ready_ms);
	rp_append_text(line, sizeof(line), ";steady_start_ms=");
	rp_append_uint_text(line, sizeof(line), record->steady_start_ms);
	rp_append_text(line, sizeof(line), ";end_ms=");
	rp_append_uint_text(line, sizeof(line), workflow_end);
	rp_append_text(line, sizeof(line), ";setup_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), setup_elapsed);
	rp_append_text(line, sizeof(line), ";exec_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), exec_elapsed);
	rp_append_text(line, sizeof(line), ";steady_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), steady_elapsed);
	rp_append_text(line, sizeof(line), ";workflow_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), workflow_elapsed);
	rp_append_text(line, sizeof(line), "\n");
	return rp_write_file("rp_workflow_timing", line);
}

static int workflow_value_matches(const char *prefix, const char *suffix,
				  const char *actual)
{
	char expected[48];

	rp_copy_text(expected, sizeof(expected), prefix);
	rp_append_text(expected, sizeof(expected), suffix);
	return strcmp(expected, actual) == 0;
}

static int workflow_sha256_valid(const char *value)
{
	if (!value || strlen(value) != 64)
		return 0;
	for (int i = 0; i < 64; i++) {
		if (!((value[i] >= '0' && value[i] <= '9') ||
		      (value[i] >= 'a' && value[i] <= 'f')))
			return 0;
	}
	return 1;
}

static int complete_challenge_workflow(
	struct rp_challenge_workflow *workflow, const char *parent_run)
{
	const char *digits;
	int digit_count = 0;
	uint64 suffix_value = 0;

	if (strncmp(workflow->run_id, "RUN-", 4) != 0 ||
	    strcmp(parent_run, workflow->run_id) != 0) {
		printf("rp_agentos_orch: challenge_workflow_run_invalid\n");
		return 0;
	}
	digits = workflow->run_id + 4;
	if (digits[0] == 0 || (digits[0] == '0' && digits[1] != 0))
		return 0;
	while (digits[digit_count]) {
		int digit = digits[digit_count] - '0';

		if (digit < 0 || digit > 9 || digit_count >= 12)
			return 0;
		suffix_value = suffix_value * 10 + (uint64)digit;
		digit_count++;
	}
	rp_copy_text(workflow->suffix, sizeof(workflow->suffix), digits);
	if (!workflow_value_matches("RUN-", workflow->suffix, parent_run) ||
	    !workflow_value_matches("wf-host-", workflow->suffix,
				    workflow->workflow_id) ||
	    !workflow_sha256_valid(workflow->input_sha256) ||
	    !workflow_sha256_valid(workflow->derived_sha256) ||
	    strcmp(workflow->input_sha256, workflow->derived_sha256) == 0) {
		printf("rp_agentos_orch: challenge_workflow_values_inconsistent\n");
		return 0;
	}
	rp_copy_text(orch_check_token, sizeof(orch_check_token), workflow->run_id);
	rp_append_text(orch_check_token, sizeof(orch_check_token), "-rerun");
	if (strcmp(orch_check_token, workflow->rerun_id) != 0)
		return 0;

	rp_copy_text(workflow->kernel_run_id, sizeof(workflow->kernel_run_id), "r");
	rp_append_text(workflow->kernel_run_id,
		       sizeof(workflow->kernel_run_id), workflow->suffix);
	rp_copy_text(workflow->echo_payload, sizeof(workflow->echo_payload), "wf:");
	rp_append_text(workflow->echo_payload, sizeof(workflow->echo_payload),
		       workflow->suffix);
	rp_copy_text(workflow->target_physical,
		     sizeof(workflow->target_physical), "a");
	rp_append_text(workflow->target_physical,
		       sizeof(workflow->target_physical), workflow->suffix);
	rp_copy_text(workflow->target_summary,
		     sizeof(workflow->target_summary), "input-");
	rp_append_text(workflow->target_summary,
		       sizeof(workflow->target_summary), workflow->input_sha256);
	workflow->request_id = 1000000000000ULL + suffix_value * 4;
	return strlen(workflow->kernel_run_id) < AGENT_FILE_FIELD_SIZE &&
	       strlen(workflow->target_physical) < AGENT_FILE_NAME_SIZE &&
	       strlen(workflow->echo_payload) < AGENT_OP_PAYLOAD_SIZE;
}

static int load_challenge_oracle(struct rp_challenge_workflow *workflow)
{
	char parent_run[24];

	memset(workflow, 0, sizeof(*workflow));
	memset(parent_run, 0, sizeof(parent_run));
	if (!rp_host_seed_copy_value_for_kind(
		    "kind=host_workflow", "run_id=", workflow->run_id,
		    sizeof(workflow->run_id)) ||
	    !rp_host_seed_copy_value_for_kind(
		    "kind=host_workflow", "workflow_id=", workflow->workflow_id,
		    sizeof(workflow->workflow_id)) ||
	    !rp_host_seed_copy_value_for_kind(
		    "kind=research_rerun", "run_id=", workflow->rerun_id,
		    sizeof(workflow->rerun_id)) ||
	    !rp_host_seed_copy_value_for_kind(
		    "kind=research_rerun", "parent_run=", parent_run,
		    sizeof(parent_run)) ||
	    !rp_host_seed_copy_value_for_kind(
		    "kind=artifact_input", "sha256=", workflow->input_sha256,
		    sizeof(workflow->input_sha256)) ||
	    !rp_host_seed_copy_value_for_kind(
		    "kind=artifact_derive", "sha256=", workflow->derived_sha256,
		    sizeof(workflow->derived_sha256))) {
		printf("rp_agentos_orch: challenge_workflow_seed_missing\n");
		return 0;
	}
	return complete_challenge_workflow(workflow, parent_run);
}

static int read_workflow_output(const char *path)
{
	int fd = open(path, O_RDONLY);
	int total = 0;
	char extra;

	if (fd < 0)
		return -1;
	while (total + 1 < RP_STATE_BUFFER_SIZE) {
		int n = read(fd, rp_state_buf + total,
			     RP_STATE_BUFFER_SIZE - 1 - total);

		if (n < 0) {
			close(fd);
			return -1;
		}
		if (n == 0)
			break;
		total += n;
	}
	if (total + 1 == RP_STATE_BUFFER_SIZE && read(fd, &extra, 1) != 0) {
		close(fd);
		return -1;
	}
	close(fd);
	rp_state_buf[total] = 0;
	return total;
}

static int output_record_value(const char *path, const char *anchor,
			       const char *key, char *out, int cap)
{
	int text_len = read_workflow_output(path);
	int anchor_len = strlen(anchor);
	int key_len = strlen(key);
	int line_start = 0;
	int record_count = 0;
	int field_count = 0;

	if (text_len < 0 || cap <= 1 || anchor_len <= 0 || key_len <= 0)
		return 0;
	for (int pos = 0; pos <= text_len; pos++) {
		int line_end;

		if (pos != text_len && rp_state_buf[pos] != '\n')
			continue;
		line_end = pos;
		if (line_end - line_start >= anchor_len &&
		    strncmp(rp_state_buf + line_start, anchor, anchor_len) == 0) {
			int field_start = line_start;

			record_count++;
			while (field_start < line_end) {
				int field_end = field_start;
				int value_len;

				while (field_end < line_end &&
				       rp_state_buf[field_end] != ';')
					field_end++;
				if (field_end - field_start >= key_len &&
				    strncmp(rp_state_buf + field_start, key,
					    key_len) == 0) {
					value_len = field_end - field_start - key_len;
					field_count++;
					if (value_len <= 0 || value_len >= cap)
						return 0;
					memcpy(out, rp_state_buf + field_start + key_len,
					       value_len);
					out[value_len] = 0;
				}
				field_start = field_end + 1;
			}
		}
		line_start = pos + 1;
	}
	return record_count == 1 && field_count == 1;
}

static int load_challenge_workflow_outputs(
	struct rp_challenge_workflow *workflow,
	const struct rp_challenge_workflow *expected)
{
	char parent_run[24];
	char runner_workflow[24];
	char runner_run[24];
	char runner_rerun[48];
	char runner_parent[24];
	char expected_runner_rerun[48];

	memset(workflow, 0, sizeof(*workflow));
	memset(parent_run, 0, sizeof(parent_run));
	if (!output_record_value("rp_input", "host_action_rerun_id=",
				 "host_action_rerun_id=", workflow->rerun_id,
				 sizeof(workflow->rerun_id)) ||
	    !output_record_value("rp_input", "host_action_rerun_parent=",
				 "host_action_rerun_parent=", parent_run,
				 sizeof(parent_run)) ||
	    !output_record_value("rp_stage_dag", "host_workflow_id=",
				 "host_workflow_id=", workflow->workflow_id,
				 sizeof(workflow->workflow_id)) ||
	    !output_record_value("rp_stage_state", "host_workflow_run_id=",
				 "host_workflow_run_id=", workflow->run_id,
				 sizeof(workflow->run_id)) ||
	    !output_record_value("rp_artifact", "host_artifact_input=",
				 "sha256=", workflow->input_sha256,
				 sizeof(workflow->input_sha256)) ||
	    !output_record_value("rp_artifact", "host_artifact_derive=",
				 "sha256=", workflow->derived_sha256,
				 sizeof(workflow->derived_sha256)) ||
	    !output_record_value("rp_runner", "host_action_workflow=",
				 "host_action_workflow=", runner_workflow,
				 sizeof(runner_workflow)) ||
	    !output_record_value("rp_runner", "host_action_workflow=",
				 "run_id=", runner_run, sizeof(runner_run)) ||
	    !output_record_value("rp_runner", "host_action_rerun=",
				 "host_action_rerun=", runner_rerun,
				 sizeof(runner_rerun)) ||
	    !output_record_value("rp_runner", "host_action_rerun=",
				 "parent=", runner_parent,
				 sizeof(runner_parent)) ||
	    !complete_challenge_workflow(workflow, parent_run)) {
		printf("rp_agentos_orch: challenge_workflow_output_missing\n");
		return 0;
	}
	rp_copy_text(expected_runner_rerun, sizeof(expected_runner_rerun),
		     "usable-run:");
	rp_append_text(expected_runner_rerun, sizeof(expected_runner_rerun),
		       workflow->rerun_id);
	if (strcmp(runner_workflow, workflow->workflow_id) != 0 ||
	    strcmp(runner_run, workflow->run_id) != 0 ||
	    strcmp(runner_rerun, expected_runner_rerun) != 0 ||
	    strcmp(runner_parent, workflow->run_id) != 0 ||
	    strcmp(workflow->run_id, expected->run_id) != 0 ||
	    strcmp(workflow->rerun_id, expected->rerun_id) != 0 ||
	    strcmp(workflow->workflow_id, expected->workflow_id) != 0 ||
	    strcmp(workflow->input_sha256, expected->input_sha256) != 0 ||
	    strcmp(workflow->derived_sha256, expected->derived_sha256) != 0) {
		printf("rp_agentos_orch: challenge_workflow_output_mismatch\n");
		return 0;
	}
	return 1;
}

static void make_echo(struct agent_op *op, uint64 request_id,
		      const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = request_id;
	op->arg0 = request_id;
	op->arg1 = request_id + 1;
	strcpy(op->payload, payload);
}

static void make_kernel_op(struct agent_op *op, int tool_id,
			   uint64 request_id, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool_id;
	op->request_id = request_id;
	if (payload)
		strcpy(op->payload, payload);
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

static int seed_meta(int fid, const char *physical, const char *project,
		     const char *workflow, const char *run_id,
		     const char *label, const char *type, const char *state,
		     const char *summary, uint64 deps)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	rp_copy_text(meta.physical_name, sizeof(meta.physical_name), physical);
	rp_copy_text(meta.logical_path, sizeof(meta.logical_path), physical);
	rp_copy_text(meta.project, sizeof(meta.project), project);
	rp_copy_text(meta.workflow, sizeof(meta.workflow), workflow);
	rp_copy_text(meta.run_id, sizeof(meta.run_id), run_id);
	rp_copy_text(meta.stage, sizeof(meta.stage), label);
	rp_copy_text(meta.kind, sizeof(meta.kind), type);
	rp_copy_text(meta.status, sizeof(meta.status), state);
	rp_copy_text(meta.summary, sizeof(meta.summary), summary);
	meta.dependency_mask = deps;
	meta.flags = AGENT_FILE_META_F_PERSIST;
	return agent_file_meta_set(&meta);
}

static int seed_legacy_research_metadata(void)
{
	if (seed_meta(1, "r42align", "lab-gene-x", "nightly-regression",
		      "RUN-042", "align", "artifact", "ok",
		      "align output is ready",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(2, "r42anlz", "lab-gene-x", "nightly-regression",
		      "RUN-042", "analyze", "status", "ok",
		      "analysis completed from align",
		      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(3, "r42report", "lab-gene-x", "nightly-regression",
		      "RUN-042", "report", "report", "ok",
		      "report artifact ready",
		      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(4, "r42archive", "lab-gene-x", "nightly-regression",
		      "RUN-042", "archive", "artifact", "pending",
		      "archive waits for report", 0) < 0)
		return -1;
	return 0;
}

static int seed_challenge_research_metadata(
	const struct rp_challenge_workflow *workflow)
{
	char physical[AGENT_FILE_NAME_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];

	if (seed_meta(101, workflow->target_physical, "lab-gene-x",
		      workflow->workflow_id, workflow->kernel_run_id, "align",
		      "artifact", "ok", workflow->target_summary,
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	for (int index = 0; index < 3; index++) {
		const char prefixes[] = {'n', 'p', 'z'};
		const char *stages[] = {"analyze", "report", "archive"};
		const char *kinds[] = {"status", "report", "artifact"};
		const char *states[] = {"ok", "ok", "pending"};
		uint64 deps[] = {
			agent_dependency_label_bit("report") |
				agent_dependency_label_bit("archive"),
			agent_dependency_label_bit("archive"),
			0,
		};

		physical[0] = prefixes[index];
		physical[1] = 0;
		rp_append_text(physical, sizeof(physical), workflow->suffix);
		rp_copy_text(summary, sizeof(summary),
			     index == 0 ? "derived=" :
			     index == 1 ? "workflow=" : "run=");
		rp_append_text(summary, sizeof(summary),
			       index == 0 ? workflow->derived_sha256 :
			       index == 1 ? workflow->workflow_id : workflow->run_id);
		if (seed_meta(102 + index, physical, "lab-gene-x",
			      workflow->workflow_id, workflow->kernel_run_id,
			      stages[index], kinds[index], states[index], summary,
			      deps[index]) < 0)
			return -1;
	}
	return 0;
}

static int verify_kernel_dependency_path(
	const struct rp_challenge_workflow *workflow)
{
	int hint_count;
	int target_hits = 0;
	char payload[AGENT_OP_PAYLOAD_SIZE];

	rp_copy_text(payload, sizeof(payload),
		     "source=align;target=report;run_id=");
	rp_append_text(payload, sizeof(payload), workflow->kernel_run_id);
	make_kernel_op(&orch_op, AGENT_TOOL_DEPENDENCY_UPDATE,
		       workflow->request_id + 2, payload);
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK) {
		printf("rp_agentos_orch: dependency_update_failed status=%d\n",
		       orch_result.status);
		return -1;
	}

	rp_copy_text(payload, sizeof(payload), "label=align;run_id=");
	rp_append_text(payload, sizeof(payload), workflow->kernel_run_id);
	make_kernel_op(&orch_op, AGENT_TOOL_DEPENDENCY_QUERY,
		       workflow->request_id + 3, payload);
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK ||
	    !text_contains(orch_result.result, "report")) {
		printf("rp_agentos_orch: dependency_query_failed status=%d result=%s\n",
		       orch_result.status, orch_result.result);
		return -1;
	}

	memset(&orch_file_query, 0, sizeof(orch_file_query));
	orch_file_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	orch_file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(orch_file_query.project, "lab-gene-x");
	rp_copy_text(orch_file_query.workflow, sizeof(orch_file_query.workflow),
		     workflow->workflow_id);
	rp_copy_text(orch_file_query.run_id, sizeof(orch_file_query.run_id),
		     workflow->kernel_run_id);
	strcpy(orch_file_query.stage, "align");
	if (agent_file_query(&orch_file_query, &orch_file_query_result) < 1 ||
	    orch_file_query_result.returned < 1 ||
	    orch_file_query_result.used_index != 1 ||
	    orch_file_query_result.plan != AGENT_FILE_QUERY_PLAN_STAGE_INDEX) {
		printf("rp_agentos_orch: metadata_query_failed hits=%d index=%d plan=%d\n",
		       orch_file_query_result.returned,
		       orch_file_query_result.used_index,
		       orch_file_query_result.plan);
		return -1;
	}
	memset(&orch_metadata_hit, 0, sizeof(orch_metadata_hit));
	for (int i = 0; i < orch_file_query_result.returned; i++) {
		struct agent_file_hit *hit = &orch_file_query_result.hits[i];

		if (hit->fid == 101 &&
		    strcmp(hit->physical_name, workflow->target_physical) == 0 &&
		    strcmp(hit->logical_path, workflow->target_physical) == 0 &&
		    strcmp(hit->stage, "align") == 0 &&
		    strcmp(hit->kind, "artifact") == 0 &&
		    strcmp(hit->status, "ok") == 0 &&
		    strcmp(hit->summary, workflow->target_summary) == 0) {
			orch_metadata_hit = *hit;
			target_hits++;
		}
	}
	if (target_hits != 1) {
		printf("rp_agentos_orch: metadata_target_failed matches=%d\n",
		       target_hits);
		return -1;
	}

	hint_count = agent_file_prefetch_snapshot(
		orch_prefetch_hints, AGENT_FILE_PREFETCH_MAX_HINTS);
	if (hint_count < 1 ||
	    (orch_prefetch_hints[0].reason &
	     AGENT_FILE_PREFETCH_REASON_DEPENDENCY) == 0) {
		printf("rp_agentos_orch: prefetch_hint_failed count=%d reason=%d\n",
		       hint_count, (int)orch_prefetch_hints[0].reason);
		return -1;
	}

	if (!rp_append_file("rp_tool", "tool=agentos.dependency_update"))
		return -1;
	if (!rp_append_file("rp_tool", "tool=agentos.dependency_query"))
		return -1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_prefetch"))
		return -1;
	return 0;
}

static int echo_result_matches(const struct agent_op *op,
			       const struct agent_result *result)
{
	return result->version == AGENT_CALL_VERSION &&
	       result->status == AGENT_STATUS_OK &&
	       result->tool_id == op->tool_id &&
	       result->request_id == op->request_id && result->sequence != 0 &&
	       result->value0 == (uint64)strlen(op->payload) &&
	       result->value1 == op->arg0 && result->value2 == op->arg1 &&
	       strcmp(result->result, op->payload) == 0;
}

static struct agent_context_record *find_echo_context(
	int context_records, const struct agent_op *op,
	const struct agent_result *result)
{
	for (int i = 0; i < context_records; i++) {
		struct agent_context_record *record = &orch_records[i];

		if (record->sequence == result->sequence &&
		    record->request_id == op->request_id &&
		    record->tool_id == op->tool_id &&
		    record->status == result->status &&
		    record->arg0 == op->arg0 &&
		    record->value0 == result->value0 &&
		    record->value1 == result->value1 &&
		    record->value2 == result->value2 && record->record_hash != 0 &&
		    strcmp(record->payload, op->payload) == 0 &&
		    strcmp(record->result, result->result) == 0)
			return record;
	}
	return 0;
}

static struct agent_provenance_edge *find_echo_edge(
	int edge_count, const struct agent_context_record *source,
	const struct agent_context_record *target)
{
	int self_pid = getpid();

	for (int i = 0; i < edge_count; i++) {
		struct agent_provenance_edge *edge = &orch_provenance_edges[i];

		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->source_pid == self_pid && edge->target_pid == self_pid &&
		    edge->source_sequence == source->sequence &&
		    edge->target_sequence == target->sequence &&
		    edge->source_record_hash == source->record_hash &&
		    edge->target_record_hash == target->record_hash &&
		    edge->source_record_hash != 0 && edge->target_record_hash != 0 &&
		    edge->tool_id == AGENT_TOOL_ECHO &&
		    edge->status == AGENT_STATUS_OK)
			return edge;
	}
	return 0;
}

static int record_functional_acceptance(
	const struct rp_challenge_workflow *workflow,
	int context_records, const struct agent_op *echo_op,
	const struct agent_result *echo_result,
	const struct agent_context_record *echo_context,
	const struct agent_context_record *followup_context,
	int timeline_count, int edge_count,
	const struct agent_provenance_edge *echo_edge)
{
	char *body = orch_acceptance_body;

	rp_copy_text(body, sizeof(orch_acceptance_body),
		     "schema=agentos_task6_acceptance_v3;module_count=4;workflow_id=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->workflow_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";workflow_run_id=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->run_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";input_sha256=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->input_sha256);
	rp_append_text(body, sizeof(orch_acceptance_body), ";derived_sha256=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->derived_sha256);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       ";workflow_outputs=verified\n"
		     "module=context;operation=context_snapshot;status=verified;records=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), context_records);
	rp_append_text(body, sizeof(orch_acceptance_body), ";latest_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_header.latest_sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";record_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";payload=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_context->payload);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_context->result);
	rp_append_text(body, sizeof(orch_acceptance_body), ";followup_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), followup_context->sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";followup_record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), followup_context->record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       "\nmodule=structured_tool;operation=agent_run_echo;status=verified;request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";request_payload=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_op->payload);
	rp_append_text(body, sizeof(orch_acceptance_body), ";arg0=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->arg0);
	rp_append_text(body, sizeof(orch_acceptance_body), ";arg1=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->arg1);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_version=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->version);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_status=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->status);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_payload=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_result->result);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_value0=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->value0);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_value1=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->value1);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_value2=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->value2);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->sequence);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       "\nmodule=metadata_query;operation=file_query_stage_index;status=verified;project=lab-gene-x;workflow_id=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->workflow_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";workflow_run_id=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->run_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";kernel_run_id=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->kernel_run_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";input_sha256=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->input_sha256);
	rp_append_text(body, sizeof(orch_acceptance_body), ";derived_sha256=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->derived_sha256);
	rp_append_text(body, sizeof(orch_acceptance_body), ";stage=align;returned=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_file_query_result.returned);
	rp_append_text(body, sizeof(orch_acceptance_body), ";used_index=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_file_query_result.used_index);
	rp_append_text(body, sizeof(orch_acceptance_body), ";plan=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_file_query_result.plan);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_fid=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.fid);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_physical=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.physical_name);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_stage=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.stage);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_kind=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.kind);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_status=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.status);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_summary=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.summary);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       "\nmodule=observation;operation=timeline_provenance_ledger;status=verified;timeline_records=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), timeline_count);
	rp_append_text(body, sizeof(orch_acceptance_body), ";provenance_edges=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), edge_count);
	rp_append_text(body, sizeof(orch_acceptance_body), ";ledger_records=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_ledger.visible_records);
	rp_append_text(body, sizeof(orch_acceptance_body), ";ledger_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_ledger.ledger_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";edge_kind=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->kind);
	rp_append_text(body, sizeof(orch_acceptance_body), ";edge_tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";edge_status=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->status);
	rp_append_text(body, sizeof(orch_acceptance_body), ";source_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->source_sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->target_sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";source_record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->source_record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->target_record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";workflow_id=");
	rp_append_text(body, sizeof(orch_acceptance_body), workflow->workflow_id);
	rp_append_text(body, sizeof(orch_acceptance_body), "\n");
	return rp_write_file("rp_agentos_acceptance", body);
}

static int record_agentos_prerequisites(void)
{
	if (!rp_write_file("rp_agentos_kernel",
			   "target=agentos_ucore\n"
		     "mode=kernel_agent_orchestrated\n"
		     "agent_role=orchestrator\n"
		     "agent_context=present\n"
		     "agent_run=echo\n"
		     "context_snapshot=present\n"
		     "agent_timeline=observed\n"
		     "agent_provenance=observed\n"
		     "agent_ledger=observed\n"
		     "provenance_kernel=observed\n"
		     "file_meta_service=initialized\n"
		     "research_platform=rp_orch\n"
		     "dependency_update=generic_record\n"
		     "dependency_query=generic_record\n"
		     "metadata_query=stage_index\n"
		     "prefetch_hint=dependency_driven\n"
		     "status=ready\n"))
		return 0;
	return rp_write_file(
		"rp_agentos_mainflow",
		"stage=entry;context_trusted=kernel_shadow;status=ready\n"
		"stage=entry_dependency;dependency_graph=kernel_records;status=ready\n");
}

static int record_challenge_kernel_binding(
	const struct rp_challenge_workflow *workflow)
{
	char *body = orch_state_body;

	rp_copy_text(body, sizeof(orch_state_body), "challenge_workflow_id=");
	rp_append_text(body, sizeof(orch_state_body), workflow->workflow_id);
	rp_append_text(body, sizeof(orch_state_body), "\nchallenge_run_id=");
	rp_append_text(body, sizeof(orch_state_body), workflow->run_id);
	rp_append_text(body, sizeof(orch_state_body), "\nchallenge_rerun_id=");
	rp_append_text(body, sizeof(orch_state_body), workflow->rerun_id);
	rp_append_text(body, sizeof(orch_state_body), "\nchallenge_input_sha256=");
	rp_append_text(body, sizeof(orch_state_body), workflow->input_sha256);
	rp_append_text(body, sizeof(orch_state_body), "\nchallenge_derived_sha256=");
	rp_append_text(body, sizeof(orch_state_body), workflow->derived_sha256);
	rp_append_text(body, sizeof(orch_state_body),
		       "\nchallenge_output_source=rp_input,rp_stage_dag,rp_stage_state,rp_artifact,rp_runner\n"
		       "challenge_output_validation=exact_unique_records\n");
	return rp_append_file("rp_agentos_kernel", body);
}

static int run_research_orchestrator(int timing_read_fd, int timing_write_fd,
				     int completion_write_fd,
				     uint64 workflow_start)
{
	int timeline_count;
	int edge_count;
	int info_ret;
	int snapshot;
	struct agent_context_record *echo_context;
	struct agent_context_record *followup_context;
	struct agent_provenance_edge *echo_edge;
	uint64 phase_mask = RP_WORKFLOW_PHASE_AGENT_CREATE;
	char timing_arg[48];
	char completion_arg[48];

	info_ret = agent_info(&orch_info);
	if (info_ret < 0 || !orch_info.is_agent ||
	    orch_info.agent_role != AGENT_ROLE_ORCHESTRATOR) {
		printf("rp_agentos_orch: not_orchestrator_agent ret=%d is_agent=%d role=%d caps=%p\n",
		       info_ret, orch_info.is_agent, orch_info.agent_role,
		       (void *)orch_info.capability_mask);
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_AGENT_INFO;
	if (!load_challenge_oracle(&orch_expected_workflow))
		return 1;
	phase_mask |= RP_WORKFLOW_PHASE_CHALLENGE;

	if (agent_file_meta_init() < 0) {
		printf("rp_agentos_orch: file_meta_init_failed\n");
		return 1;
	}
	if (seed_legacy_research_metadata() < 0) {
		printf("rp_agentos_orch: seed_legacy_metadata_failed\n");
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_METADATA;
	if (!record_agentos_prerequisites()) {
		return 1;
	}
	if (!rp_append_status("agentos_kernel=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_meta_init")) return 1;
	phase_mask |= RP_WORKFLOW_PHASE_SCAFFOLD;
	uint64 ready_ms = get_mtime();

	if (phase_mask != RP_WORKFLOW_PHASE_ALL || ready_ms < workflow_start ||
	    !write_workflow_handoff(timing_write_fd, workflow_start, ready_ms,
				    phase_mask)) {
		printf("rp_agentos_orch: workflow_handoff_failed\n");
		return 1;
	}
	close(timing_write_fd);
	if (agent_scope_delegate_fd(timing_read_fd) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(completion_write_fd) != AGENT_STATUS_OK) {
		close(timing_read_fd);
		close(completion_write_fd);
		printf("rp_agentos_orch: workflow_child_pipe_delegate_failed\n");
		return 1;
	}
	rp_copy_text(timing_arg, sizeof(timing_arg), RP_WORKFLOW_HANDOFF_PREFIX);
	rp_append_uint_text(timing_arg, sizeof(timing_arg), timing_read_fd);
	rp_copy_text(completion_arg, sizeof(completion_arg),
		     RP_WORKFLOW_COMPLETION_PREFIX);
	rp_append_uint_text(completion_arg, sizeof(completion_arg),
			    completion_write_fd);
	char *argv[] = {
		"rp_orch",
		timing_arg,
		completion_arg,
		0,
	};
	int workflow_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	if (workflow_pid < 0) {
		close(timing_read_fd);
		close(completion_write_fd);
		printf("rp_agentos_orch: create_workflow_orchestrator_failed\n");
		return 1;
	}
	if (workflow_pid == 0) {
		if (exec("rp_orch", argv) < 0) {
			printf("rp_agentos_orch: exec_failed program=rp_orch\n");
			exit(1);
		}
		exit(1);
	}
	close(timing_read_fd);
	close(completion_write_fd);
	int workflow_code = -1;
	int workflow_got = waitpid(workflow_pid, &workflow_code);
	if (workflow_got != workflow_pid || workflow_code != 0) {
		printf("rp_agentos_orch: workflow_child_failed pid=%d got=%d code=%d\n",
		       workflow_pid, workflow_got, workflow_code);
		return 1;
	}
	if (!load_challenge_workflow_outputs(
		    &orch_workflow, &orch_expected_workflow))
		return 1;

	if (seed_challenge_research_metadata(&orch_workflow) < 0) {
		printf("rp_agentos_orch: seed_challenge_metadata_failed\n");
		return 1;
	}
	make_echo(&orch_echo_op, orch_workflow.request_id,
		  orch_workflow.echo_payload);
	rp_copy_text(orch_check_token, sizeof(orch_check_token), "c:");
	rp_append_text(orch_check_token, sizeof(orch_check_token),
		       orch_workflow.suffix);
	make_echo(&orch_followup_op, orch_workflow.request_id + 1,
		  orch_check_token);
	if (agent_run(&orch_echo_op, &orch_echo_result, 1, 0) != 1 ||
	    !echo_result_matches(&orch_echo_op, &orch_echo_result) ||
	    agent_run(&orch_followup_op, &orch_followup_result, 1, 0) != 1 ||
	    !echo_result_matches(&orch_followup_op, &orch_followup_result) ||
	    orch_followup_result.sequence <= orch_echo_result.sequence) {
		printf("rp_agentos_orch: agent_run_semantics_failed status=%d/%d\n",
		       orch_echo_result.status, orch_followup_result.status);
		return 1;
	}

	snapshot = context_snapshot(&orch_header, orch_records, 4);
	echo_context = find_echo_context(snapshot, &orch_echo_op,
					 &orch_echo_result);
	followup_context = find_echo_context(snapshot, &orch_followup_op,
					     &orch_followup_result);
	if (snapshot < 2 || snapshot > 4 || echo_context == 0 ||
	    followup_context == 0 ||
	    followup_context->cause_sequence != echo_context->sequence ||
	    orch_header.latest_sequence < followup_context->sequence) {
		printf("rp_agentos_orch: context_snapshot_failed n=%d\n",
		       snapshot);
		return 1;
	}
	timeline_count = agent_timeline_snapshot(orch_timeline_records, 4);
	edge_count = agent_provenance_snapshot(orch_provenance_edges, 4);
	echo_edge = find_echo_edge(edge_count, echo_context, followup_context);
	if (timeline_count < 1 || timeline_count > 4 || edge_count < 1 ||
	    edge_count > 4 || echo_edge == 0 ||
	    agent_ledger_snapshot(&orch_ledger) < 0 ||
	    orch_ledger.visible_records < 2 || orch_ledger.ledger_hash == 0) {
		printf("rp_agentos_orch: provenance_snapshot_failed timeline=%d edges=%d\n",
		       timeline_count, edge_count);
		return 1;
	}
	if (verify_kernel_dependency_path(&orch_workflow) < 0)
		return 1;
	if (!record_challenge_kernel_binding(&orch_workflow))
		return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.agent_run.echo")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.context_snapshot")) return 1;

	printf("rp_agentos_orch: agent role=%d context=%p size=%p latest=%d\n",
	       orch_info.agent_role, (void *)orch_info.context_base,
	       (void *)orch_info.context_size, (int)orch_header.latest_sequence);
	if (!record_functional_acceptance(
		    &orch_workflow, snapshot, &orch_echo_op, &orch_echo_result,
		    echo_context, followup_context, timeline_count, edge_count,
		    echo_edge)) {
		printf("rp_agentos_orch: acceptance_receipt_failed\n");
		return 1;
	}
	return 0;
}

static int read_stability_report(
	int fd, struct rp_resource_stability_report *report)
{
	char *bytes = (char *)report;
	int received = 0;

	memset(report, 0, sizeof(*report));
	while (received < (int)sizeof(*report)) {
		int n = read(fd, bytes + received, sizeof(*report) - received);

		if (n <= 0)
			return 0;
		received += n;
	}
	return 1;
}

static int stability_report_valid(
	const struct rp_resource_stability_report *report, uint index,
	uint mode, uint64 challenge_nonce)
{
	uint expected_rounds = mode == RP_RESOURCE_STABILITY_MODE_LOAD ?
		RP_RESOURCE_STABILITY_CHILD_ROUNDS : 0;
	uint64 call_delta;
	uint64 context_delta;

	if (report->magic != RP_RESOURCE_STABILITY_MAGIC ||
	    report->version != RP_RESOURCE_STABILITY_VERSION ||
	    report->struct_size != sizeof(*report) ||
	    report->workflow_index != index || report->mode != mode ||
	    report->challenge_nonce != challenge_nonce ||
	    report->lifecycle_id == 0 || report->lifecycle_generation == 0 ||
	    report->scope_id == 0 ||
	    report->io_owner !=
		    (IO_POLICY_OWNER_SCOPE_FLAG | report->scope_id) ||
	    report->resource_account_reserved != 0 ||
	    report->resource_account_generation == 0 ||
	    report->guard != rp_resource_stability_guard(report))
		return 0;
	if (report->initial_leased != 0 || report->initial_debt != 0 ||
	    report->initial_waiters != 0 ||
	    report->initial_debt_waiters != 0 ||
	    report->initial_admission_waiters != 0 ||
	    report->initial_context_lane_depth != 0 ||
	    report->initial_context_lane_waiters != 0 ||
	    report->initial_metadata_owned != 0 ||
	    report->initial_metadata_waiters != 0 ||
	    report->initial_agent_calls != 0 ||
	    report->initial_context_records != 0)
		return 0;
	if (report->final_leased != 0 || report->final_debt != 0 ||
	    report->final_waiters != 0 || report->final_debt_waiters != 0 ||
	    report->final_admission_waiters != 0 ||
	    report->final_context_lane_depth != 0 ||
	    report->final_context_lane_waiters != 0 ||
	    report->final_metadata_owned != 0 ||
	    report->final_metadata_waiters != 0 ||
	    report->final_agent_calls < report->initial_agent_calls ||
	    report->final_context_records < report->initial_context_records ||
	    report->final_completion_sequence <
		    report->initial_completion_sequence)
		return 0;
	call_delta = report->final_agent_calls - report->initial_agent_calls;
	context_delta = report->final_context_records -
			report->initial_context_records;
	return call_delta == 0 && context_delta == 0 &&
	       report->process_rounds == expected_rounds &&
	       report->file_rounds == expected_rounds &&
	       report->memory_rounds == expected_rounds &&
	       report->metadata_rounds == expected_rounds &&
	       (mode != RP_RESOURCE_STABILITY_MODE_TERMINAL ||
		(report->final_cache_resident ==
			report->initial_cache_resident &&
		 report->final_completion_sequence ==
			report->initial_completion_sequence)) &&
	       (mode != RP_RESOURCE_STABILITY_MODE_LOAD ||
		report->final_completion_sequence >
			report->initial_completion_sequence);
}

static int stability_global_snapshot_valid(
	const struct agent_resource_snapshot *snapshot)
{
	if (snapshot->version != AGENT_RESOURCE_SNAPSHOT_VERSION ||
	    snapshot->struct_size != sizeof(*snapshot) ||
	    snapshot->kind_count != AGENT_RESOURCE_KIND_COUNT ||
	    (snapshot->measured_mask & ~AGENT_RESOURCE_KIND_MASK_ALL) != 0)
		return 0;
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++) {
		const struct agent_resource_kind_snapshot *resource =
			&snapshot->kinds[kind];
		int measured = (snapshot->measured_mask & (1U << kind)) != 0;

		if (!measured) {
			if (resource->capacity != 0 || resource->used != 0 ||
			    resource->pending != 0 || resource->ordinary_used != 0 ||
			    resource->ordinary_pending != 0 ||
			    resource->reserved_used != 0 ||
			    resource->reserved_pending != 0)
				return 0;
			continue;
		}
		if (resource->capacity == 0 ||
		    resource->used != resource->ordinary_used +
					 resource->reserved_used ||
		    resource->pending != resource->ordinary_pending +
					    resource->reserved_pending ||
		    resource->used > resource->capacity ||
		    resource->pending > resource->capacity - resource->used)
			return 0;
	}
	return 1;
}

static int stability_global_pair_valid(
	const struct agent_resource_snapshot *before,
	const struct agent_resource_snapshot *after, uint mode)
{
	if (!stability_global_snapshot_valid(before) ||
	    !stability_global_snapshot_valid(after) ||
	    before->measured_mask != after->measured_mask ||
	    before->ordinary_free_pages != after->ordinary_free_pages ||
	    before->reserved_free_pages != after->reserved_free_pages ||
	    before->stack_reserved_free_pages !=
		    after->stack_reserved_free_pages)
		return 0;
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++) {
		const struct agent_resource_kind_snapshot *left =
			&before->kinds[kind];
		const struct agent_resource_kind_snapshot *right =
			&after->kinds[kind];
		uint64 bound = mode == RP_RESOURCE_STABILITY_MODE_TERMINAL ?
			0 : orch_resource_growth_bounds[kind];

		if ((before->measured_mask & (1U << kind)) == 0)
			continue;
		if (left->capacity != right->capacity ||
		    left->ordinary_pending != 0 ||
		    right->ordinary_pending != 0 ||
		    left->reserved_pending != 0 ||
		    right->reserved_pending != 0 ||
		    (bound == 0 &&
		     (left->ordinary_used != right->ordinary_used ||
		      left->reserved_used != right->reserved_used)) ||
		    (bound != 0 &&
		     (right->ordinary_used < left->ordinary_used ||
		      right->reserved_used < left->reserved_used ||
		      right->ordinary_used - left->ordinary_used +
			      right->reserved_used - left->reserved_used > bound)))
			return 0;
	}
	return 1;
}

static int stability_identity_unique(uint count)
{
	const struct rp_resource_stability_report *latest =
		&orch_stability_reports[count - 1];

	for (uint i = 0; i + 1 < count; i++) {
		const struct rp_resource_stability_report *prior =
			&orch_stability_reports[i];

		if ((prior->lifecycle_id == latest->lifecycle_id &&
		     prior->lifecycle_generation == latest->lifecycle_generation) ||
		    prior->scope_id == latest->scope_id ||
		    prior->io_owner == latest->io_owner ||
		    (prior->resource_account_slot ==
			     latest->resource_account_slot &&
		     prior->resource_account_generation ==
			     latest->resource_account_generation))
			return 0;
	}
	return 1;
}

static int stability_global_sequence_valid(void)
{
	const struct agent_resource_snapshot *first =
		&orch_stability_global_before[0];
	const struct agent_resource_snapshot *terminal =
		&orch_stability_global_after[RP_RESOURCE_STABILITY_WORKFLOWS - 1];

	if (first->ordinary_free_pages != terminal->ordinary_free_pages ||
	    first->reserved_free_pages != terminal->reserved_free_pages ||
	    first->stack_reserved_free_pages !=
		    terminal->stack_reserved_free_pages)
		return 0;
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++) {
		const struct agent_resource_kind_snapshot *left =
			&first->kinds[kind];
		const struct agent_resource_kind_snapshot *right =
			&terminal->kinds[kind];
		const struct agent_resource_kind_snapshot *last_load =
			&orch_stability_global_after[
				RP_RESOURCE_STABILITY_LOAD_WORKFLOWS - 1].kinds[kind];
		uint64 bound = orch_resource_growth_bounds[kind];
		int plateau = 0;

		if ((first->measured_mask & (1U << kind)) == 0)
			continue;
		if (left->ordinary_pending != 0 || left->reserved_pending != 0 ||
		    right->ordinary_pending != 0 || right->reserved_pending != 0 ||
		    (bound == 0 &&
		     (left->ordinary_used != right->ordinary_used ||
		      left->reserved_used != right->reserved_used)) ||
		    (bound != 0 &&
		     (right->ordinary_used < left->ordinary_used ||
		      right->reserved_used < left->reserved_used ||
		      right->ordinary_used - left->ordinary_used +
			      right->reserved_used - left->reserved_used > bound)))
			return 0;
		if (bound == 0)
			continue;
		for (uint index = 1;
		     index < RP_RESOURCE_STABILITY_LOAD_WORKFLOWS;
		     index++) {
			const struct agent_resource_kind_snapshot *prior =
				&orch_stability_global_after[index - 1].kinds[kind];
			const struct agent_resource_kind_snapshot *current =
				&orch_stability_global_after[index].kinds[kind];

			if (current->used <= prior->used)
				plateau = 1;
		}
		if (right->used < last_load->used)
			plateau = 1;
		if (!plateau)
			return 0;
	}
	return 1;
}

static int run_stability_workflow(uint index, uint mode)
{
	struct rp_resource_stability_report *report =
		&orch_stability_reports[index];
	struct agent_resource_snapshot *global_before =
		&orch_stability_global_before[index];
	struct agent_resource_snapshot *global_after =
		&orch_stability_global_after[index];
	int report_pipe[2];
	char report_arg[48];
	char index_arg[40];
	char mode_arg[40];
	char nonce_arg[64];
	uint64 challenge_nonce = rp_resource_stability_nonce(
		orch_stability_workflow.request_id, index, mode);
	char extra;
	int pid = -1;
	int code = -1;
	int got;
	int complete;
	int eof;

	memset(global_before, 0, sizeof(*global_before));
	memset(global_after, 0, sizeof(*global_after));
	if (agent_resource_snapshot(global_before) != AGENT_STATUS_OK ||
	    pipe(report_pipe) < 0)
		return 0;
	for (int attempt = 0; attempt < 256; attempt++) {
		if (agent_scope_delegate_fd(report_pipe[1]) != AGENT_STATUS_OK)
			break;
		pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
		if (pid >= 0)
			break;
		sched_yield();
	}
	if (pid < 0) {
		close(report_pipe[0]);
		close(report_pipe[1]);
		return 0;
	}
	if (pid == 0) {
		/* Workflow constructors inherit only explicitly delegated descriptors. */
		rp_copy_text(report_arg, sizeof(report_arg),
			     RP_RESOURCE_STABILITY_REPORT_PREFIX);
		rp_append_uint_text(report_arg, sizeof(report_arg), report_pipe[1]);
		rp_copy_text(index_arg, sizeof(index_arg),
			     RP_RESOURCE_STABILITY_INDEX_PREFIX);
		rp_append_uint_text(index_arg, sizeof(index_arg), index);
		rp_copy_text(mode_arg, sizeof(mode_arg),
			     RP_RESOURCE_STABILITY_MODE_PREFIX);
		rp_append_uint_text(mode_arg, sizeof(mode_arg), mode);
		rp_copy_text(nonce_arg, sizeof(nonce_arg),
			     RP_RESOURCE_STABILITY_NONCE_PREFIX);
		rp_append_uint_text(nonce_arg, sizeof(nonce_arg), challenge_nonce);
		char *argv[] = {
			"rp_resprobe",
			report_arg,
			index_arg,
			mode_arg,
			nonce_arg,
			0,
		};

		if (exec("rp_resprobe", argv) < 0)
			printf("rp_agentos_orch: stability_exec_failed index=%u\n",
			       index);
		exit(1);
	}
	close(report_pipe[1]);
	complete = read_stability_report(report_pipe[0], report);
	got = waitpid(pid, &code);
	eof = read(report_pipe[0], &extra, 1) == 0;
	close(report_pipe[0]);
	if (agent_resource_snapshot(global_after) != AGENT_STATUS_OK)
		return 0;
	return complete && got == pid && code == 0 && eof &&
	       stability_report_valid(report, index, mode, challenge_nonce) &&
	       stability_identity_unique(index + 1) &&
	       stability_global_pair_valid(global_before, global_after, mode);
}

static void append_stability_global_policy(char *body, uint measured_mask)
{
	rp_append_text(body, sizeof(orch_stability_body),
		       "record=global_policy;measured_mask=");
	rp_append_uint_text(body, sizeof(orch_stability_body), measured_mask);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";measured_mask_semantics=configured_global_resource_kind_counters_only;snapshot_consistency=single_core_irq_coherent;coverage=configured_global_kind_counters;account_counter_coverage=not_measured;rate_budget_coverage=not_measured;free_pages_status=measured;terminal_workflow_pair_bound=0");
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++) {
		const struct agent_resource_kind_snapshot *resource =
			&orch_stability_global_before[0].kinds[kind];

		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body),
			       orch_resource_kind_names[kind]);
		rp_append_text(body, sizeof(orch_stability_body), "_status=");
		rp_append_text(body, sizeof(orch_stability_body),
			       (measured_mask & (1U << kind)) != 0 ?
				       "measured" : "not_measured");
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body),
			       orch_resource_kind_names[kind]);
		rp_append_text(body, sizeof(orch_stability_body), "_capacity=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    resource->capacity);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body),
			       orch_resource_kind_names[kind]);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_per_workflow_growth_bound=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    orch_resource_growth_bounds[kind]);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body),
			       orch_resource_kind_names[kind]);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_terminal_growth_bound=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    orch_resource_growth_bounds[kind]);
	}
	rp_append_text(body, sizeof(orch_stability_body), "\n");
}

static void append_stability_report(
	char *body, const struct rp_resource_stability_report *report,
	const struct agent_resource_snapshot *global_before,
	const struct agent_resource_snapshot *global_after,
	int global_verified)
{
	rp_append_text(body, sizeof(orch_stability_body), "workflow_index=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->workflow_index);
	rp_append_text(body, sizeof(orch_stability_body), ";mode=");
	rp_append_text(body, sizeof(orch_stability_body),
		       report->mode == RP_RESOURCE_STABILITY_MODE_LOAD ?
			       "load" : "terminal");
	rp_append_text(body, sizeof(orch_stability_body), ";challenge_nonce=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->challenge_nonce);
	rp_append_text(body, sizeof(orch_stability_body), ";lifecycle_id=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->lifecycle_id);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";lifecycle_generation=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->lifecycle_generation);
	rp_append_text(body, sizeof(orch_stability_body), ";scope_id=");
	rp_append_uint_text(body, sizeof(orch_stability_body), report->scope_id);
	rp_append_text(body, sizeof(orch_stability_body), ";io_owner=");
	rp_append_uint_text(body, sizeof(orch_stability_body), report->io_owner);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";resource_account_slot=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->resource_account_slot);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";resource_account_reserved=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->resource_account_reserved);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";resource_account_generation=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->resource_account_generation);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_cache_resident=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_cache_resident);
	rp_append_text(body, sizeof(orch_stability_body), ";initial_leased=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_leased);
	rp_append_text(body, sizeof(orch_stability_body), ";initial_debt=");
	rp_append_uint_text(body, sizeof(orch_stability_body), report->initial_debt);
	rp_append_text(body, sizeof(orch_stability_body), ";initial_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_debt_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_debt_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_admission_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_admission_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_context_lane_depth=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_context_lane_depth);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_context_lane_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_context_lane_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_metadata_owned=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_metadata_owned);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_metadata_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_metadata_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_agent_calls=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_agent_calls);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_context_records=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_context_records);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_cache_resident=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_cache_resident);
	rp_append_text(body, sizeof(orch_stability_body), ";final_leased=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_leased);
	rp_append_text(body, sizeof(orch_stability_body), ";final_debt=");
	rp_append_uint_text(body, sizeof(orch_stability_body), report->final_debt);
	rp_append_text(body, sizeof(orch_stability_body), ";final_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_debt_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_debt_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_admission_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_admission_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_context_lane_depth=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_context_lane_depth);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_context_lane_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_context_lane_waiters);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_metadata_owned=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_metadata_owned);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_metadata_waiters=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_metadata_waiters);
	rp_append_text(body, sizeof(orch_stability_body), ";final_agent_calls=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_agent_calls);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_context_records=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_context_records);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";initial_completion_sequence=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->initial_completion_sequence);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";final_completion_sequence=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->final_completion_sequence);
	rp_append_text(body, sizeof(orch_stability_body), ";process_rounds=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->process_rounds);
	rp_append_text(body, sizeof(orch_stability_body), ";file_rounds=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->file_rounds);
	rp_append_text(body, sizeof(orch_stability_body), ";memory_rounds=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->memory_rounds);
	rp_append_text(body, sizeof(orch_stability_body), ";metadata_rounds=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    report->metadata_rounds);
	rp_append_text(body, sizeof(orch_stability_body), ";report_guard=");
	rp_append_uint_text(body, sizeof(orch_stability_body), report->guard);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";ordinary_free_pages_before=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    global_before->ordinary_free_pages);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";ordinary_free_pages_after=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    global_after->ordinary_free_pages);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";reserved_free_pages_before=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    global_before->reserved_free_pages);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";reserved_free_pages_after=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    global_after->reserved_free_pages);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";stack_reserved_free_pages_before=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    global_before->stack_reserved_free_pages);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";stack_reserved_free_pages_after=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    global_after->stack_reserved_free_pages);
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++) {
		const struct agent_resource_kind_snapshot *before =
			&global_before->kinds[kind];
		const struct agent_resource_kind_snapshot *after =
			&global_after->kinds[kind];
		const char *name = orch_resource_kind_names[kind];

		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_ordinary_used_before=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    before->ordinary_used);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_ordinary_used_after=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    after->ordinary_used);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_ordinary_pending_before=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    before->ordinary_pending);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_ordinary_pending_after=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    after->ordinary_pending);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_reserved_used_before=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    before->reserved_used);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_reserved_used_after=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    after->reserved_used);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_reserved_pending_before=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    before->reserved_pending);
		rp_append_text(body, sizeof(orch_stability_body), ";");
		rp_append_text(body, sizeof(orch_stability_body), name);
		rp_append_text(body, sizeof(orch_stability_body),
			       "_reserved_pending_after=");
		rp_append_uint_text(body, sizeof(orch_stability_body),
				    after->reserved_pending);
	}
	rp_append_text(body, sizeof(orch_stability_body),
		       ";per_workflow_bound_status=");
	rp_append_text(body, sizeof(orch_stability_body),
		       global_verified ? "verified" : "not_measured");
	rp_append_text(body, sizeof(orch_stability_body),
		       ";reaped=1;pipe_eof=1;status=verified\n");
}

static int run_resource_stability_acceptance(void)
{
	char *body = orch_stability_body;
	uint measured_mask;
	int global_verified;

	memset(orch_stability_reports, 0, sizeof(orch_stability_reports));
	memset(orch_stability_global_before, 0,
	       sizeof(orch_stability_global_before));
	memset(orch_stability_global_after, 0,
	       sizeof(orch_stability_global_after));
	if (!load_challenge_oracle(&orch_stability_workflow))
		return 0;
	for (uint index = 0; index < RP_RESOURCE_STABILITY_WORKFLOWS;
	     index++) {
		uint mode = index < RP_RESOURCE_STABILITY_LOAD_WORKFLOWS ?
			RP_RESOURCE_STABILITY_MODE_LOAD :
			RP_RESOURCE_STABILITY_MODE_TERMINAL;

		if (!run_stability_workflow(index, mode)) {
			printf("rp_agentos_orch: stability_workflow_failed index=%u\n",
			       index);
			return 0;
		}
		if (index != 0 &&
		    orch_stability_global_before[index].measured_mask !=
			    orch_stability_global_before[0].measured_mask)
			return 0;
	}
	if (!stability_global_sequence_valid())
		return 0;
	measured_mask = orch_stability_global_before[0].measured_mask;
	global_verified = measured_mask == AGENT_RESOURCE_KIND_MASK_ALL;
	rp_copy_text(body, sizeof(orch_stability_body),
		     "schema=agentos_resource_stability_v3;measurement_scope=post_workflow_acceptance;timed_makespan_included=0;claim_scope=configured_global_counter_reclamation;configured_kind_coverage=measured_mask_only;account_coverage=self_identity_only;rate_budget_coverage=not_measured;global_leak_freedom=not_claimed;challenge_suffix=");
	rp_append_text(body, sizeof(orch_stability_body),
		       orch_stability_workflow.suffix);
	rp_append_text(body, sizeof(orch_stability_body), ";load_workflows=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    RP_RESOURCE_STABILITY_LOAD_WORKFLOWS);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";terminal_workflows=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    RP_RESOURCE_STABILITY_TERMINAL_WORKFLOWS);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";child_rounds_per_workflow=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    RP_RESOURCE_STABILITY_CHILD_ROUNDS);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";memory_pages_per_round=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    RP_RESOURCE_STABILITY_MEMORY_PAGES);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";file_objects_per_round=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    RP_RESOURCE_STABILITY_FILE_OBJECTS);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";metadata_ops_per_round=");
	rp_append_uint_text(body, sizeof(orch_stability_body),
			    RP_RESOURCE_STABILITY_METADATA_OPS);
	rp_append_text(body, sizeof(orch_stability_body),
		       ";sequence_bound_status=verified");
	rp_append_text(body, sizeof(orch_stability_body), ";status=");
	rp_append_text(body, sizeof(orch_stability_body),
		       global_verified ? "verified" : "partial");
	rp_append_text(body, sizeof(orch_stability_body), "\n");
	append_stability_global_policy(body, measured_mask);
	for (uint index = 0; index < RP_RESOURCE_STABILITY_WORKFLOWS;
	     index++)
		append_stability_report(
			body, &orch_stability_reports[index],
			&orch_stability_global_before[index],
			&orch_stability_global_after[index], global_verified);
	if (!rp_write_file("rp_resource_stability", body))
		return 0;
	printf("rp_agentos_orch: resource_stability workflows=%u load=%u terminal=%u child_rounds=%u passed\n",
	       RP_RESOURCE_STABILITY_WORKFLOWS,
	       RP_RESOURCE_STABILITY_LOAD_WORKFLOWS,
	       RP_RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
	       RP_RESOURCE_STABILITY_CHILD_ROUNDS);
	return 1;
}

int main(void)
{
	int64 workflow_start = get_mtime();
	int timing_pipe[2];
	int completion_pipe[2];
	struct rp_workflow_handoff completion;

	if (workflow_start < 0 || pipe(timing_pipe) < 0) {
		printf("rp_agentos_orch: workflow_pipe_failed\n");
		return 1;
	}
	if (pipe(completion_pipe) < 0) {
		close(timing_pipe[0]);
		close(timing_pipe[1]);
		printf("rp_agentos_orch: completion_pipe_failed\n");
		return 1;
	}
	if (agent_scope_delegate_fd(timing_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(timing_pipe[1]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(completion_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(completion_pipe[1]) != AGENT_STATUS_OK) {
		close(timing_pipe[0]);
		close(timing_pipe[1]);
		close(completion_pipe[0]);
		close(completion_pipe[1]);
		printf("rp_agentos_orch: workflow_pipe_delegate_failed\n");
		return 1;
	}
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	if (pid < 0) {
		close(timing_pipe[0]);
		close(timing_pipe[1]);
		close(completion_pipe[0]);
		close(completion_pipe[1]);
		printf("rp_agentos_orch: create_orchestrator_failed\n");
		return 1;
	}
	if (pid == 0) {
		close(completion_pipe[0]);
		return run_research_orchestrator(
			timing_pipe[0], timing_pipe[1], completion_pipe[1],
			(uint64)workflow_start);
	}
	close(timing_pipe[0]);
	close(timing_pipe[1]);
	close(completion_pipe[1]);

	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		close(completion_pipe[0]);
		printf("rp_agentos_orch: wait_failed pid=%d got=%d\n", pid, got);
		return 1;
	}
	if (code != 0) {
		close(completion_pipe[0]);
		printf("rp_agentos_orch: child_failed code=%d\n", code);
		return 1;
	}
	if (!read_workflow_completion(completion_pipe[0], &completion) ||
	    completion.start_ms != (uint64)workflow_start) {
		printf("rp_agentos_orch: completion_handoff_failed\n");
		return 1;
	}
	if (!rp_file_contains("rp_agentos_kernel", "status=ready") ||
	    !rp_file_contains("rp_agentcmp",
			      "evidence_role=runtime_verified") ||
	    !rp_file_contains("rp_agentcmp", "status=verified") ||
	    !rp_file_contains("rp_agentos_acceptance",
			      "schema=agentos_task6_acceptance_v3")) {
		printf("rp_agentos_orch: state_check_failed\n");
		return 1;
	}
	uint64 workflow_end = get_mtime();
	if (workflow_end < completion.steady_start_ms ||
	    !record_workflow_timing(&completion, workflow_end)) {
		printf("rp_agentos_orch: workflow_timing_failed\n");
		return 1;
	}
	/* This acceptance probe is deliberately outside the Task 6 makespan. */
	if (!run_resource_stability_acceptance()) {
		printf("rp_agentos_orch: resource_stability_failed\n");
		return 1;
	}
	printf("rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready\n");
	printf("rp_agentos_orch: passed\n");
	return 0;
}
