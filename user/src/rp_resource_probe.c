#include <agent.h>
#include <fcntl.h>
#include <io_policy.h>
#include <rp_resource_stability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PAGE_BYTES 4096
#define RESOURCE_FILE_BYTES 1024

static char resource_file_data[RESOURCE_FILE_BYTES];
static struct rp_resource_stability_report report;
static struct agent_workflow_lifecycle_info initial_lifecycle;
static struct agent_workflow_lifecycle_info final_lifecycle;
static struct io_policy_info initial_io;
static struct io_policy_info final_io;
static struct agent_info initial_agent;
static struct agent_info final_agent;
static struct agent_file_meta metadata_file;
static struct agent_file_query metadata_query;
static struct agent_file_query_result metadata_query_result;

static int parse_uint_argument(const char *argument, const char *prefix,
			       unsigned int maximum, unsigned int *out)
{
	unsigned long long value = 0;
	int prefix_len = strlen(prefix);
	const char *digits;

	if (argument == 0 || strncmp(argument, prefix, prefix_len) != 0)
		return 0;
	digits = argument + prefix_len;
	if (*digits == 0 || (digits[0] == '0' && digits[1] != 0))
		return 0;
	for (; *digits; digits++) {
		unsigned int digit;

		if (*digits < '0' || *digits > '9')
			return 0;
		digit = *digits - '0';
		if (value > maximum / 10U ||
		    (value == maximum / 10U && digit > maximum % 10U))
			return 0;
		value = value * 10U + digit;
	}
	*out = (unsigned int)value;
	return 1;
}

static int parse_u64_argument(const char *argument, const char *prefix,
			      unsigned long long *out)
{
	unsigned long long value = 0;
	int prefix_len = strlen(prefix);
	const char *digits;

	if (argument == 0 || strncmp(argument, prefix, prefix_len) != 0)
		return 0;
	digits = argument + prefix_len;
	if (*digits == 0 || (digits[0] == '0' && digits[1] != 0))
		return 0;
	for (; *digits; digits++) {
		unsigned int digit;

		if (*digits < '0' || *digits > '9')
			return 0;
		digit = *digits - '0';
		if (value > ~0ULL / 10ULL ||
		    (value == ~0ULL / 10ULL && digit > ~0ULL % 10ULL))
			return 0;
		value = value * 10ULL + digit;
	}
	*out = value;
	return 1;
}

static int write_exact(int fd, const void *buffer, int size)
{
	const char *bytes = buffer;
	int written = 0;

	while (written < size) {
		int n = write(fd, bytes + written, size - written);

		if (n <= 0)
			return 0;
		written += n;
	}
	return 1;
}

static int snapshot_state(struct agent_workflow_lifecycle_info *lifecycle,
			  struct io_policy_info *io, struct agent_info *info)
{
	memset(lifecycle, 0, sizeof(*lifecycle));
	memset(io, 0, sizeof(*io));
	memset(info, 0, sizeof(*info));
	return agent_workflow_lifecycle_info(lifecycle, 0) == AGENT_STATUS_OK &&
	       lifecycle->version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	       lifecycle->struct_size == sizeof(*lifecycle) &&
	       lifecycle->charged == 1 && lifecycle->key.id != 0 &&
	       lifecycle->key.generation != 0 &&
	       lifecycle->resource_account_valid == 1 &&
	       lifecycle->resource_account_generation != 0 &&
	       io_policy_info(io) == 0 && io->version == IO_POLICY_VERSION &&
	       io->struct_size == sizeof(*io) && agent_info(info) == AGENT_STATUS_OK &&
	       info->is_agent && info->agent_role == AGENT_ROLE_ORCHESTRATOR &&
	       info->filesystem_domain != 0;
}

static int transient_resource_child(unsigned int workflow_index,
				    unsigned int round,
				    unsigned long long challenge_nonce)
{
	int pipes[4][2];
	int files[4];
	long memory;
	char names[4][8];

	memory = sbrk(RP_RESOURCE_STABILITY_MEMORY_PAGES * PAGE_BYTES);
	if (memory == -1)
		return 11;
	for (unsigned int page = 0;
	     page < RP_RESOURCE_STABILITY_MEMORY_PAGES; page++)
		*(volatile char *)(memory + page * PAGE_BYTES) =
			(char)(workflow_index + round + page +
			       (challenge_nonce >> ((page & 7U) * 8U)));
	for (unsigned int byte = 0; byte < sizeof(resource_file_data); byte++)
		resource_file_data[byte] =
			(char)(challenge_nonce >> ((byte & 7U) * 8U));
	for (unsigned int index = 0; index < 4; index++) {
		memset(names[index], 0, sizeof(names[index]));
		names[index][0] = 'r';
		names[index][1] = (char)('0' + workflow_index);
		names[index][2] = (char)('0' + round / 10U);
		names[index][3] = (char)('0' + round % 10U);
		names[index][4] = (char)('0' + index);
		names[index][5] = "0123456789abcdef"[
			(challenge_nonce >> (index * 4U)) & 0xfU];
		if (pipe(pipes[index]) < 0)
			return 12;
		files[index] = open(names[index], O_CREATE | O_WRONLY | O_TRUNC);
		if (files[index] < 0)
			return 13;
		if (write(files[index], resource_file_data,
			  sizeof(resource_file_data)) != sizeof(resource_file_data) ||
		    unlink(names[index]) < 0)
			return 14;
	}
	/* 故意把页面和全部十二个文件对象留给退出路径回收。 */
	return 0;
}

static int run_child_round(unsigned int workflow_index, unsigned int round,
			   unsigned long long challenge_nonce)
{
	int pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	int status = -1;

	if (pid < 0)
		return 0;
	if (pid == 0)
		exit(transient_resource_child(workflow_index, round,
					      challenge_nonce));
	return waitpid(pid, &status) == pid && status == 0;
}

static int run_metadata_round(unsigned int workflow_index,
			      unsigned int round,
			      unsigned long long challenge_nonce)
{
	char name[8] = { 'm', '0', '0', '0', 0 };
	char payload[24] = "resource-state";
	int fid = 52000 + (int)(challenge_nonce % 1000ULL) * 1000 +
		  workflow_index * 100U + round;
	int fd;
	int queried;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;

	name[1] = (char)('0' + workflow_index);
	name[2] = (char)('0' + round / 10U);
	name[3] = (char)('0' + round % 10U);
	name[4] = "0123456789abcdef"[(challenge_nonce >> 60) & 0xfU];
	for (unsigned int index = 0; index < 8; index++)
		payload[14 + index] = "0123456789abcdef"[
			(challenge_nonce >> ((7U - index) * 4U)) & 0xfU];
	payload[22] = 0;
	fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);
	if (fd < 0 ||
	    write(fd, payload, strlen(payload)) != strlen(payload) ||
	    close(fd) < 0)
		return 0;
	memset(&metadata_file, 0, sizeof(metadata_file));
	metadata_file.fid = fid;
	strcpy(metadata_file.physical_name, name);
	strcpy(metadata_file.logical_path, name);
	strcpy(metadata_file.project, "rp-stability");
	strcpy(metadata_file.workflow, "resource-reuse");
	strcpy(metadata_file.run_id, "same-boot");
	strcpy(metadata_file.stage, "teardown");
	strcpy(metadata_file.kind, "artifact");
	strcpy(metadata_file.status, "temporary");
	strcpy(metadata_file.summary, "dynamic resource lifecycle probe");
	metadata_file.dependency_mask = agent_dependency_label_bit("temporary");
	if (agent_file_meta_set(&metadata_file) != AGENT_STATUS_OK)
		return 0;
	memset(&metadata_query, 0, sizeof(metadata_query));
	memset(&metadata_query_result, 0, sizeof(metadata_query_result));
	metadata_query.flags = AGENT_FILE_QUERY_SCAN;
	metadata_query.max_hits = 1;
	strcpy(metadata_query.physical_name, name);
	strcpy(metadata_query.project, "rp-stability");
	strcpy(metadata_query.workflow, "resource-reuse");
	strcpy(metadata_query.run_id, "same-boot");
	strcpy(metadata_query.stage, "teardown");
	strcpy(metadata_query.kind, "artifact");
	strcpy(metadata_query.status, "temporary");
	queried = agent_file_query(&metadata_query, &metadata_query_result);
	if (queried != 1 || metadata_query_result.returned != 1 ||
	    metadata_query_result.total_hits != 1 ||
	    metadata_query_result.hits[0].fid != fid ||
	    strcmp(metadata_query_result.hits[0].physical_name, name) != 0)
		return 0;
	dev = metadata_query_result.hits[0].dev;
	inum = metadata_query_result.hits[0].inum;
	incarnation = metadata_query_result.hits[0].incarnation;
	memset(&metadata_file, 0, sizeof(metadata_file));
	metadata_file.fid = fid;
	strcpy(metadata_file.physical_name, name);
	metadata_file.dev = dev;
	metadata_file.inum = inum;
	metadata_file.incarnation = incarnation;
	metadata_file.flags = AGENT_FILE_META_F_DELETE;
	return agent_file_meta_set(&metadata_file) == AGENT_STATUS_OK &&
	       unlink(name) == 0;
}

static int initial_state_is_fresh(void)
{
	return initial_io.owner ==
		       (IO_POLICY_OWNER_SCOPE_FLAG | initial_agent.filesystem_domain) &&
	       initial_io.io_class == IO_POLICY_CLASS_CONTROL &&
	       initial_io.leased == 0 && initial_io.debt == 0 &&
	       initial_io.waiters == 0 &&
	       initial_io.debt_waiters == 0 &&
	       initial_io.admission_waiters == 0 &&
	       initial_lifecycle.context_lane_depth == 0 &&
	       initial_lifecycle.context_lane_waiters == 0 &&
	       initial_lifecycle.metadata_txn_owned == 0 &&
	       initial_lifecycle.metadata_txn_waiters == 0 &&
	       initial_agent.agent_call_count == 0 &&
	       initial_agent.context_path_count == 0;
}

static unsigned int final_state_mismatch(unsigned int expected_rounds,
					 unsigned int mode)
{
	uint64 expected_observations =
		(uint64)expected_rounds *
		RP_RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND;
	unsigned int mismatch = 0;

	if (final_lifecycle.key.id != initial_lifecycle.key.id ||
	    final_lifecycle.key.generation !=
		    initial_lifecycle.key.generation ||
	    final_agent.filesystem_domain != initial_agent.filesystem_domain)
		mismatch |= 1U << 0;
	if (final_lifecycle.resource_account_valid != 1 ||
	    final_lifecycle.resource_account_slot !=
		    initial_lifecycle.resource_account_slot ||
	    final_lifecycle.resource_account_generation !=
		    initial_lifecycle.resource_account_generation)
		mismatch |= 1U << 1;
	if (final_io.owner != initial_io.owner || final_io.leased != 0 ||
	    final_io.debt != 0 || final_io.waiters != 0 ||
	    final_io.debt_waiters != 0 || final_io.admission_waiters != 0)
		mismatch |= 1U << 2;
	if (final_lifecycle.context_lane_depth != 0 ||
	    final_lifecycle.context_lane_waiters != 0 ||
	    final_lifecycle.metadata_txn_owned != 0 ||
	    final_lifecycle.metadata_txn_waiters != 0)
		mismatch |= 1U << 3;
	if (final_agent.context_path_capacity !=
		    initial_agent.context_path_capacity ||
	    initial_agent.context_path_count >
		    initial_agent.context_path_capacity ||
	    expected_observations >
		    initial_agent.context_path_capacity -
			    initial_agent.context_path_count ||
	    final_agent.agent_call_count !=
		    initial_agent.agent_call_count + expected_observations ||
	    final_agent.context_path_count !=
		    initial_agent.context_path_count + expected_observations)
		mismatch |= 1U << 4;
	if (mode == RP_RESOURCE_STABILITY_MODE_TERMINAL &&
	    (final_io.cache_resident != initial_io.cache_resident ||
	     final_io.completion_sequence != initial_io.completion_sequence))
		mismatch |= 1U << 5;
	if (mode == RP_RESOURCE_STABILITY_MODE_LOAD &&
	    final_io.completion_sequence <= initial_io.completion_sequence)
		mismatch |= 1U << 6;
	return mismatch;
}

int main(int argc, char **argv)
{
	unsigned int report_fd;
	unsigned int workflow_index;
	unsigned int mode;
	unsigned int expected_rounds;
	unsigned long long challenge_nonce;

	if (argc != 5 ||
	    !parse_uint_argument(argv[1], RP_RESOURCE_STABILITY_REPORT_PREFIX,
				  1023, &report_fd) ||
	    !parse_uint_argument(argv[2], RP_RESOURCE_STABILITY_INDEX_PREFIX,
				  RP_RESOURCE_STABILITY_WORKFLOWS - 1,
				  &workflow_index) ||
	    !parse_uint_argument(argv[3], RP_RESOURCE_STABILITY_MODE_PREFIX,
				  RP_RESOURCE_STABILITY_MODE_TERMINAL, &mode) ||
	    !parse_u64_argument(argv[4], RP_RESOURCE_STABILITY_NONCE_PREFIX,
				&challenge_nonce) || challenge_nonce == 0 ||
	    (mode != RP_RESOURCE_STABILITY_MODE_LOAD &&
	     mode != RP_RESOURCE_STABILITY_MODE_TERMINAL)) {
		printf("rp_resource_probe: invalid_arguments\n");
		return 1;
	}
	memset(&report, 0, sizeof(report));
	if (!snapshot_state(&initial_lifecycle, &initial_io, &initial_agent) ||
	    !initial_state_is_fresh()) {
		printf("rp_resource_probe: initial_state_not_fresh\n");
		return 1;
	}
	expected_rounds = mode == RP_RESOURCE_STABILITY_MODE_LOAD ?
		RP_RESOURCE_STABILITY_CHILD_ROUNDS : 0;
	for (unsigned int round = 0; round < expected_rounds; round++) {
		if (!run_child_round(workflow_index, round, challenge_nonce)) {
			printf("rp_resource_probe: child_round_failed index=%u round=%u\n",
			       workflow_index, round);
			return 1;
		}
		report.process_rounds++;
		report.file_rounds++;
		report.memory_rounds++;
		if (!run_metadata_round(workflow_index, round, challenge_nonce)) {
			printf("rp_resource_probe: metadata_round_failed index=%u round=%u\n",
			       workflow_index, round);
			return 1;
		}
		report.metadata_rounds++;
	}
	if (!snapshot_state(&final_lifecycle, &final_io, &final_agent)) {
		printf("rp_resource_probe: final_snapshot_failed\n");
		return 1;
	}
	unsigned int mismatch = final_state_mismatch(expected_rounds, mode);
	if (mismatch != 0) {
		printf("rp_resource_probe: final_state_mismatch mask=%u calls=%llu/%llu context=%llu/%llu completion=%llu/%llu\n",
		       mismatch, initial_agent.agent_call_count,
		       final_agent.agent_call_count,
		       initial_agent.context_path_count,
		       final_agent.context_path_count,
		       initial_io.completion_sequence,
		       final_io.completion_sequence);
		return 1;
	}
	report.magic = RP_RESOURCE_STABILITY_MAGIC;
	report.version = RP_RESOURCE_STABILITY_VERSION;
	report.struct_size = sizeof(report);
	report.workflow_index = workflow_index;
	report.mode = mode;
	report.challenge_nonce = challenge_nonce;
	report.lifecycle_id = initial_lifecycle.key.id;
	report.lifecycle_generation = initial_lifecycle.key.generation;
	report.scope_id = initial_agent.filesystem_domain;
	report.io_owner = initial_io.owner;
	report.resource_account_slot = initial_lifecycle.resource_account_slot;
	report.resource_account_generation =
		initial_lifecycle.resource_account_generation;
	report.initial_cache_resident = initial_io.cache_resident;
	report.initial_leased = initial_io.leased;
	report.initial_debt = initial_io.debt;
	report.initial_waiters = initial_io.waiters;
	report.initial_debt_waiters = initial_io.debt_waiters;
	report.initial_admission_waiters = initial_io.admission_waiters;
	report.initial_context_lane_depth = initial_lifecycle.context_lane_depth;
	report.initial_context_lane_waiters = initial_lifecycle.context_lane_waiters;
	report.initial_metadata_owned = initial_lifecycle.metadata_txn_owned;
	report.initial_metadata_waiters = initial_lifecycle.metadata_txn_waiters;
	report.initial_agent_calls = initial_agent.agent_call_count;
	report.initial_context_records = initial_agent.context_path_count;
	report.final_cache_resident = final_io.cache_resident;
	report.final_leased = final_io.leased;
	report.final_debt = final_io.debt;
	report.final_waiters = final_io.waiters;
	report.final_debt_waiters = final_io.debt_waiters;
	report.final_admission_waiters = final_io.admission_waiters;
	report.final_context_lane_depth = final_lifecycle.context_lane_depth;
	report.final_context_lane_waiters = final_lifecycle.context_lane_waiters;
	report.final_metadata_owned = final_lifecycle.metadata_txn_owned;
	report.final_metadata_waiters = final_lifecycle.metadata_txn_waiters;
	report.final_agent_calls = final_agent.agent_call_count;
	report.final_context_records = final_agent.context_path_count;
	report.initial_completion_sequence = initial_io.completion_sequence;
	report.final_completion_sequence = final_io.completion_sequence;
	report.guard = rp_resource_stability_guard(&report);
	if (!write_exact((int)report_fd, &report, sizeof(report))) {
		printf("rp_resource_probe: report_failed\n");
		return 1;
	}
	printf("rp_resource_probe: index=%u mode=%u process=%u file=%u memory=%u metadata=%u passed\n",
	       workflow_index, mode, report.process_rounds, report.file_rounds,
	       report.memory_rounds, report.metadata_rounds);
	return 0;
}
