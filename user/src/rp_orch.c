#include <agent.h>
#include <exec_policy_manifest.h>
#include <research_platform_state.h>
#include <rp_evidence.h>
#include <rp_launch_attestation.h>
#include <rp_program_manifest.h>
#include <rp_worker_batch.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct program_launch_policy {
	const char *program;
	uint64 worker_capabilities;
};

#define RP_WORKFLOW_WORKER \
	(AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)
#define RP_PROGRAM(name) { name, RP_WORKFLOW_WORKER },
static const struct program_launch_policy PROGRAMS[] = {
	RP_PLATFORM_PROGRAMS(RP_PROGRAM)
};
#undef RP_PROGRAM

#define RP_PROGRAM_NAME(name) name,
static const char *const PROGRAM_NAMES[] = {
	RP_PLATFORM_PROGRAMS(RP_PROGRAM_NAME)
};
#undef RP_PROGRAM_NAME

struct worker_batch_binding {
	const char *program;
	uint8 group;
	uint32 index;
};

struct worker_batch_group {
	const char *runner;
	uint8 group;
	uint32 count;
};

#define RP_BATCH_0_BINDING(index, program) { #program, 0, index },
#define RP_BATCH_1_BINDING(index, program) { #program, 1, index },
#define RP_BATCH_2_BINDING(index, program) { #program, 2, index },
static const struct worker_batch_binding WORKER_BATCH_BINDINGS[] = {
	RP_WORKER_BATCH_0_PROGRAMS(RP_BATCH_0_BINDING)
	RP_WORKER_BATCH_1_PROGRAMS(RP_BATCH_1_BINDING)
	RP_WORKER_BATCH_2_PROGRAMS(RP_BATCH_2_BINDING)
};
#undef RP_BATCH_0_BINDING
#undef RP_BATCH_1_BINDING
#undef RP_BATCH_2_BINDING

#define RP_BATCH_GROUP(group, runner, count) { #runner, group, count },
static const struct worker_batch_group WORKER_BATCH_GROUPS[] = {
	RP_WORKER_BATCH_GROUPS(RP_BATCH_GROUP)
};
#undef RP_BATCH_GROUP

#define RP_DIRECT_PROGRAM(program) #program,
static const char *const WORKER_DIRECT_PROGRAMS[] = {
	RP_WORKER_DIRECT_PROGRAMS(RP_DIRECT_PROGRAM)
};
#undef RP_DIRECT_PROGRAM

struct worker_batch_session {
	struct rp_launch_expectation expected;
	uint64 nonce;
	uint32 next_index;
	uint32 count;
	int pid;
	int command_fd;
	int result_fd;
	uint8 group;
	int active;
	char image[11];
	char identity_arg[RP_LAUNCH_EXPECT_ARG_SIZE];
	char read_fd_arg[4];
	char write_fd_arg[4];
	char nonce_arg[17];
};

static struct worker_batch_session WORKER_BATCH_SESSION;

_Static_assert(sizeof(WORKER_BATCH_BINDINGS) /
	       sizeof(WORKER_BATCH_BINDINGS[0]) == RP_WORKER_BATCH_PROGRAM_COUNT,
	       "worker batch program count mismatch");
_Static_assert(sizeof(WORKER_BATCH_GROUPS) /
	       sizeof(WORKER_BATCH_GROUPS[0]) == RP_WORKER_BATCH_GROUP_COUNT,
	       "worker batch group count mismatch");
_Static_assert(sizeof(WORKER_DIRECT_PROGRAMS) /
	       sizeof(WORKER_DIRECT_PROGRAMS[0]) == RP_WORKER_DIRECT_PROGRAM_COUNT,
	       "worker direct program count mismatch");
_Static_assert(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]) ==
	       10 + RP_WORKER_BATCH_PROGRAM_COUNT +
	       RP_WORKER_DIRECT_PROGRAM_COUNT,
	       "role, batch, and direct launch sets must cover the manifest");

#define RP_WORKFLOW_HANDOFF_MAGIC 0x52505754U
#define RP_WORKFLOW_COMPLETION_MAGIC 0x52505743U
#define RP_WORKFLOW_HANDOFF_VERSION 1U
#define RP_WORKFLOW_HANDOFF_PREFIX "--rp-workflow-timing-fd="
#define RP_WORKFLOW_COMPLETION_PREFIX "--rp-workflow-completion-fd="
#define RP_WORKFLOW_PHASE_ALL ((1ULL << 5) - 1)

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

static int workflow_handoff_fd(int argc, char **argv, int argument,
			       const char *prefix)
{
	int prefix_len = strlen(prefix);
	const char *digits;
	int fd = 0;

	if (argc != 3 || argv == 0 || argv[argument] == 0 ||
	    strncmp(argv[argument], prefix, prefix_len) != 0)
		return -1;
	digits = argv[argument] + prefix_len;
	if (*digits == 0 || (digits[0] == '0' && digits[1] != 0))
		return -1;
	for (; *digits; digits++) {
		int value;

		if (*digits < '0' || *digits > '9')
			return -1;
		value = *digits - '0';
		if (fd > (1024 - value) / 10)
			return -1;
		fd = fd * 10 + value;
	}
	return fd;
}

static int read_workflow_handoff(int argc, char **argv,
				 struct rp_workflow_handoff *record)
{
	char *bytes = (char *)record;
	char extra;
	int received = 0;
	int fd = workflow_handoff_fd(argc, argv, 1,
				     RP_WORKFLOW_HANDOFF_PREFIX);

	if (fd < 0)
		return 0;
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
	return record->magic == RP_WORKFLOW_HANDOFF_MAGIC &&
	       record->version == RP_WORKFLOW_HANDOFF_VERSION &&
	       record->phase_mask == RP_WORKFLOW_PHASE_ALL &&
	       record->guard == workflow_handoff_guard(record) &&
	       record->ready_ms >= record->start_ms &&
	       record->steady_start_ms == 0;
}

static int write_workflow_completion(int fd,
				     const struct rp_workflow_handoff *start,
				     uint64 steady_start_ms)
{
	struct rp_workflow_handoff record = *start;
	const char *bytes = (const char *)&record;
	int written = 0;

	record.magic = RP_WORKFLOW_COMPLETION_MAGIC;
	record.steady_start_ms = steady_start_ms;
	record.guard = workflow_handoff_guard(&record);
	while (written < (int)sizeof(record)) {
		int n = write(fd, bytes + written, sizeof(record) - written);

		if (n <= 0)
			return 0;
		written += n;
	}
	return 1;
}

struct trusted_launch_policy {
	const char *program;
	const char *image;
	int role;
	uint vfs_profile;
};

struct declared_role_policy {
	const char *program;
	const char *role;
};

#define TRUSTED_LAUNCH_ROW(source, image, flags, role_mask, launch_role, profile) \
	{ source, image, launch_role, profile },
static const struct trusted_launch_policy TRUSTED_LAUNCHES[] = {
	EXEC_POLICY_ENTRIES(TRUSTED_LAUNCH_ROW)
};
#undef TRUSTED_LAUNCH_ROW

#define DECLARED_ROLE_ROW(program, role) { program, role },
static const struct declared_role_policy DECLARED_ROLES[] = {
	RP_AGENTOS_ROLE_PROGRAMS(DECLARED_ROLE_ROW)
};
#undef DECLARED_ROLE_ROW

_Static_assert(EXEC_MANIFEST_ROLE_SENTINEL == AGENT_ROLE_SENTINEL,
	       "exec policy sentinel role mismatch");
_Static_assert(EXEC_MANIFEST_ROLE_INVESTIGATOR == AGENT_ROLE_INVESTIGATOR,
	       "exec policy investigator role mismatch");
_Static_assert(EXEC_MANIFEST_ROLE_RECOVERY == AGENT_ROLE_RECOVERY,
	       "exec policy recovery role mismatch");
_Static_assert(EXEC_MANIFEST_ROLE_ORCHESTRATOR == AGENT_ROLE_ORCHESTRATOR,
	       "exec policy orchestrator role mismatch");
_Static_assert(EXEC_MANIFEST_ROLE_ARTIFACT == AGENT_ROLE_ARTIFACT,
	       "exec policy artifact role mismatch");
_Static_assert(EXEC_MANIFEST_VFS_CONTENT_READ == AGENT_CAP_CONTENT_READ,
	       "exec policy content-read capability mismatch");
_Static_assert(EXEC_MANIFEST_VFS_ARTIFACT_WRITE == AGENT_CAP_ARTIFACT_WRITE,
	       "exec policy artifact-write capability mismatch");

static const char *role_name(int role)
{
	switch (role) {
	case AGENT_ROLE_ORCHESTRATOR:
		return "orchestrator";
	case AGENT_ROLE_RECOVERY:
		return "recovery";
	case AGENT_ROLE_ARTIFACT:
		return "artifact";
	case AGENT_ROLE_INVESTIGATOR:
		return "investigator";
	case AGENT_ROLE_SENTINEL:
	default:
		return "sentinel";
	}
}

static uint64 role_filesystem_capabilities(int role)
{
	switch (role) {
	case AGENT_ROLE_INVESTIGATOR:
		return AGENT_CAP_CONTENT_READ;
	case AGENT_ROLE_RECOVERY:
	case AGENT_ROLE_ORCHESTRATOR:
	case AGENT_ROLE_ARTIFACT:
		return RP_WORKFLOW_WORKER;
	case AGENT_ROLE_SENTINEL:
	default:
		return 0;
	}
}

static uint64 exec_profile_filesystem_capabilities(uint profile)
{
	if (profile == EXEC_MANIFEST_VFS_PROFILE_WORKFLOW)
		return EXEC_MANIFEST_VFS_WORKFLOW_CAPS;
	if (profile == EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ)
		return EXEC_MANIFEST_VFS_CONTENT_READ;
	if (profile == EXEC_MANIFEST_VFS_PROFILE_ARTIFACT_WRITE)
		return EXEC_MANIFEST_VFS_ARTIFACT_WRITE;
	return 0;
}

static const char *declared_role_for_program(const char *program)
{
	int total = (int)(sizeof(DECLARED_ROLES) / sizeof(DECLARED_ROLES[0]));

	for (int i = 0; i < total; i++)
		if (strcmp(program, DECLARED_ROLES[i].program) == 0)
			return DECLARED_ROLES[i].role;
	return 0;
}

static const struct trusted_launch_policy *trusted_launch_for_program(
	const char *program)
{
	int total = (int)(sizeof(TRUSTED_LAUNCHES) /
			  sizeof(TRUSTED_LAUNCHES[0]));

	for (int i = 0; i < total; i++)
		if (TRUSTED_LAUNCHES[i].role != 0 &&
		    strcmp(program, TRUSTED_LAUNCHES[i].program) == 0)
			return &TRUSTED_LAUNCHES[i];
	return 0;
}

static int trusted_launch_count_for_program(const char *program)
{
	int total = (int)(sizeof(TRUSTED_LAUNCHES) /
			  sizeof(TRUSTED_LAUNCHES[0]));
	int matches = 0;

	for (int i = 0; i < total; i++)
		if (TRUSTED_LAUNCHES[i].role != 0 &&
		    strcmp(program, TRUSTED_LAUNCHES[i].program) == 0)
			matches++;
	return matches;
}

static const struct worker_batch_binding *worker_batch_binding_for_program(
	const char *program)
{
	int total = (int)(sizeof(WORKER_BATCH_BINDINGS) /
			  sizeof(WORKER_BATCH_BINDINGS[0]));

	for (int i = 0; i < total; i++)
		if (strcmp(program, WORKER_BATCH_BINDINGS[i].program) == 0)
			return &WORKER_BATCH_BINDINGS[i];
	return 0;
}

static const struct worker_batch_group *worker_batch_group_for_id(uint8 group)
{
	int total = (int)(sizeof(WORKER_BATCH_GROUPS) /
			  sizeof(WORKER_BATCH_GROUPS[0]));

	for (int i = 0; i < total; i++)
		if (WORKER_BATCH_GROUPS[i].group == group)
			return &WORKER_BATCH_GROUPS[i];
	return 0;
}

static int worker_direct_program(const char *program)
{
	int total = (int)(sizeof(WORKER_DIRECT_PROGRAMS) /
			  sizeof(WORKER_DIRECT_PROGRAMS[0]));

	for (int i = 0; i < total; i++)
		if (strcmp(program, WORKER_DIRECT_PROGRAMS[i]) == 0)
			return 1;
	return 0;
}

static int platform_program_count(const char *program)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int matches = 0;

	for (int i = 0; i < total; i++)
		if (strcmp(program, PROGRAMS[i].program) == 0)
			matches++;
	return matches;
}

static int orchestrator_context(struct agent_info *info)
{
	int pid;

	memset(info, 0, sizeof(*info));
	pid = agent_launch_info(info);
	if (pid <= 0)
		return -1;
	if (!info->is_agent)
		return 0;
	return info->agent_role == AGENT_ROLE_ORCHESTRATOR &&
	       info->filesystem_domain != 0 &&
	       info->filesystem_capability_mask != 0 ? 1 : -1;
}

static int launch_manifest_valid(void)
{
	int declared = (int)(sizeof(DECLARED_ROLES) / sizeof(DECLARED_ROLES[0]));
	int trusted = 0;
	uint32 group_entries[RP_WORKER_BATCH_GROUP_COUNT];
	int batch_total = (int)(sizeof(WORKER_BATCH_BINDINGS) /
				 sizeof(WORKER_BATCH_BINDINGS[0]));
	int direct_total = (int)(sizeof(WORKER_DIRECT_PROGRAMS) /
				  sizeof(WORKER_DIRECT_PROGRAMS[0]));

	if (declared != 10 ||
	    batch_total != RP_WORKER_BATCH_PROGRAM_COUNT ||
	    direct_total != RP_WORKER_DIRECT_PROGRAM_COUNT)
		return 0;
	memset(group_entries, 0, sizeof(group_entries));
	for (int i = 0; i < RP_WORKER_BATCH_GROUP_COUNT; i++) {
		if (WORKER_BATCH_GROUPS[i].group != (uint8)i ||
		    WORKER_BATCH_GROUPS[i].runner == 0 ||
		    WORKER_BATCH_GROUPS[i].runner[0] == 0 ||
		    WORKER_BATCH_GROUPS[i].count == 0)
			return 0;
	}
	for (int i = 0; i < batch_total; i++) {
		const struct worker_batch_binding *binding =
			&WORKER_BATCH_BINDINGS[i];
		const struct worker_batch_group *group =
			worker_batch_group_for_id(binding->group);

		if (group == 0 || binding->index != group_entries[binding->group] ||
		    binding->index >= group->count ||
		    platform_program_count(binding->program) != 1 ||
		    declared_role_for_program(binding->program) != 0 ||
		    worker_direct_program(binding->program))
			return 0;
		for (int j = 0; j < i; j++)
			if (strcmp(binding->program,
				   WORKER_BATCH_BINDINGS[j].program) == 0)
				return 0;
		group_entries[binding->group]++;
	}
	for (int i = 0; i < RP_WORKER_BATCH_GROUP_COUNT; i++)
		if (group_entries[i] != WORKER_BATCH_GROUPS[i].count)
			return 0;
	for (int i = 0; i < direct_total; i++) {
		if (platform_program_count(WORKER_DIRECT_PROGRAMS[i]) != 1 ||
		    declared_role_for_program(WORKER_DIRECT_PROGRAMS[i]) != 0 ||
		    worker_batch_binding_for_program(WORKER_DIRECT_PROGRAMS[i]) != 0)
			return 0;
		for (int j = 0; j < i; j++)
			if (strcmp(WORKER_DIRECT_PROGRAMS[i],
				   WORKER_DIRECT_PROGRAMS[j]) == 0)
				return 0;
	}
	for (int i = 0; i < declared; i++) {
		if (platform_program_count(DECLARED_ROLES[i].program) != 1 ||
		    worker_batch_binding_for_program(DECLARED_ROLES[i].program) ||
		    worker_direct_program(DECLARED_ROLES[i].program))
			return 0;
		for (int j = 0; j < i; j++)
			if (strcmp(DECLARED_ROLES[i].program,
				   DECLARED_ROLES[j].program) == 0)
				return 0;
	}

	for (uint i = 0; i < sizeof(PROGRAMS) / sizeof(PROGRAMS[0]); i++) {
		const struct trusted_launch_policy *policy =
			trusted_launch_for_program(PROGRAMS[i].program);
		const char *role = declared_role_for_program(PROGRAMS[i].program);
		int categories = (role != 0) +
			(worker_batch_binding_for_program(PROGRAMS[i].program) != 0) +
			worker_direct_program(PROGRAMS[i].program);

		if (platform_program_count(PROGRAMS[i].program) != 1 ||
		    categories != 1 || trusted_launch_count_for_program(
			    PROGRAMS[i].program) != (role != 0) ||
		    (policy == 0) != (role == 0) ||
		    (policy && strcmp(role_name(policy->role), role) != 0))
			return 0;
		if (policy)
			trusted++;
	}
	return trusted == 10 && trusted == declared;
}

static int launch_expectation_for(
	const struct program_launch_policy *launch,
	const struct trusted_launch_policy *policy, const char *launcher,
	const struct agent_info *orchestrator,
	struct rp_launch_expectation *expected)
{
	uint64 expected_capabilities;

	memset(expected, 0, sizeof(*expected));
	if (orchestrator == 0 || !orchestrator->is_agent ||
	    orchestrator->agent_role != AGENT_ROLE_ORCHESTRATOR ||
	    (orchestrator->filesystem_capability_mask &
	     launch->worker_capabilities) != launch->worker_capabilities)
		return 0;
	if (policy != 0) {
		expected_capabilities = role_filesystem_capabilities(policy->role) &
			exec_profile_filesystem_capabilities(policy->vfs_profile) &
			orchestrator->filesystem_capability_mask;
	} else {
		expected_capabilities = launch->worker_capabilities;
	}
	expected->is_agent = policy != 0;
	expected->agent_role = policy ? policy->role : 0;
	expected->filesystem_domain = orchestrator->filesystem_domain;
	expected->filesystem_capability_mask = expected_capabilities;
	return rp_launch_expectation_valid(expected) &&
	       ((policy != 0 && strcmp(launcher, "agent_create_role") == 0) ||
		(policy == 0 &&
		 strcmp(launcher, "agent_worker_create") == 0));
}

static int format_launch_expectation(
	char argument[RP_LAUNCH_EXPECT_ARG_SIZE],
	const struct rp_launch_expectation *expected)
{
	if (!rp_launch_expectation_valid(expected))
		return 0;
	rp_copy_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE,
		     RP_LAUNCH_EXPECT_PREFIX);
	rp_append_uint_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE,
			    expected->is_agent);
	rp_append_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE, ",");
	rp_append_uint_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE,
			    expected->agent_role);
	rp_append_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE, ",");
	rp_append_uint_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE,
			    expected->filesystem_domain);
	rp_append_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE, ",");
	rp_append_uint_text(argument, RP_LAUNCH_EXPECT_ARG_SIZE,
			    expected->filesystem_capability_mask);
	return argument[0] != 0 &&
	       strlen(argument) < RP_LAUNCH_EXPECT_ARG_SIZE - 1;
}

static void record_timing(const char *program, const char *launcher,
			  int pid,
			  const struct rp_launch_expectation *expected,
			  const char *identity_source,
			  int ok, int code, unsigned long long elapsed_ms)
{
	char line[256];
	int identity_ready = pid > 0 && rp_launch_expectation_valid(expected);

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	if (strcmp(launcher, "fork") == 0) {
		rp_append_text(line, sizeof(line), ";launcher=fork");
		goto append_outcome;
	}
	rp_append_text(line, sizeof(line), ";role=");
	rp_append_text(line, sizeof(line),
		       identity_ready && expected->is_agent ?
			       role_name(expected->agent_role) : "plain");
	rp_append_text(line, sizeof(line), ";launcher=");
	rp_append_text(line, sizeof(line), launcher);
	rp_append_text(line, sizeof(line), ";identity_source=");
	rp_append_text(line, sizeof(line),
		       identity_ready && identity_source ?
			       identity_source : "unavailable");
	rp_append_text(line, sizeof(line), ";is_agent=");
	rp_append_uint_text(line, sizeof(line),
			    identity_ready ? expected->is_agent : 0);
	rp_append_text(line, sizeof(line), ";agent_role=");
	rp_append_uint_text(line, sizeof(line),
			    identity_ready ? expected->agent_role : 0);
	rp_append_text(line, sizeof(line), ";filesystem_domain=");
	rp_append_uint_text(line, sizeof(line),
			    identity_ready ? expected->filesystem_domain : 0);
	rp_append_text(line, sizeof(line), ";filesystem_capabilities=");
	rp_append_uint_text(
		line, sizeof(line),
		identity_ready ? expected->filesystem_capability_mask : 0);
append_outcome:
	rp_append_text(line, sizeof(line), ";ok=");
	rp_append_text(line, sizeof(line), ok ? "1" : "0");
	rp_append_text(line, sizeof(line), ";code=");
	rp_append_uint_text(line, sizeof(line), code < 0 ? 9999 : (unsigned int)code);
	rp_append_text(line, sizeof(line), ";elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), elapsed_ms);
	rp_state_append_line(rp_state_buf, RP_STATE_BUFFER_SIZE,
			     "rp_orch_timing", line);
}

static int record_workflow_timing(uint64 workflow_start, uint64 ready_ms,
				  uint64 steady_start, uint64 phase_mask,
				  const char *entry, const char *handoff)
{
	char line[512];
	uint64 workflow_end = get_mtime();
	uint64 setup_elapsed;
	uint64 exec_elapsed;
	uint64 steady_elapsed;
	uint64 workflow_elapsed;

	if (ready_ms < workflow_start || steady_start < ready_ms ||
	    workflow_end < steady_start)
		return 0;
	setup_elapsed = ready_ms - workflow_start;
	exec_elapsed = steady_start - ready_ms;
	steady_elapsed = workflow_end - steady_start;
	workflow_elapsed = workflow_end - workflow_start;

	rp_copy_text(line, sizeof(line),
		     "schema=guest_workflow_timing_v3;clock=monotonic_mtime_ms;entry=");
	rp_append_text(line, sizeof(line), entry);
	rp_append_text(line, sizeof(line), ";handoff=");
	rp_append_text(line, sizeof(line), handoff);
	rp_append_text(line, sizeof(line), ";init_phase_mask=");
	rp_append_uint_text(line, sizeof(line), phase_mask);
	rp_append_text(line, sizeof(line),
		       ";completion=local_final_validation;completion_phase_mask=1;start_ms=");
	rp_append_uint_text(line, sizeof(line), workflow_start);
	rp_append_text(line, sizeof(line), ";ready_ms=");
	rp_append_uint_text(line, sizeof(line), ready_ms);
	rp_append_text(line, sizeof(line), ";steady_start_ms=");
	rp_append_uint_text(line, sizeof(line), steady_start);
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

static int append_program_inventory_evidence(int in_orchestrator)
{
	struct rp_evidence_program_inventory inventory;
	char line[384];
	int expected_programs = (int)(sizeof(PROGRAM_NAMES) /
				     sizeof(PROGRAM_NAMES[0]));
	const char *launcher = in_orchestrator ? "mixed_attested" : "fork";

	if (!rp_evidence_measure_program_ledger("rp_orch_timing", "rp_orch",
						PROGRAM_NAMES,
						expected_programs,
						launcher, in_orchestrator,
						&inventory))
		return 0;
	line[0] = 0;
	rp_append_text(line, sizeof(line),
		       "evidence_role=runtime_verified;evidence_generation=runtime;program_source=rp_orch_timing;program_source_bytes=");
	rp_append_uint_text(line, sizeof(line), inventory.source_bytes);
	rp_append_text(line, sizeof(line), ";program_source_hash=");
	rp_append_uint_text(line, sizeof(line), inventory.source_hash);
	rp_append_text(line, sizeof(line), ";program_names_digest=");
	rp_append_uint_text(line, sizeof(line), inventory.program_names_digest);
	rp_append_text(line, sizeof(line), ";programs_observed=");
	rp_append_uint_text(line, sizeof(line), inventory.programs_observed);
	rp_append_text(line, sizeof(line), ";status=verified");
	if (!rp_append_file("rp_agentcmp", line))
		return 0;
	for (int i = 0; line[i]; i++)
		if (line[i] == ';')
			line[i] = ' ';
	printf("rp_orch: ");
	puts(line);
	return 1;
}

static void worker_batch_session_reset(void)
{
	memset(&WORKER_BATCH_SESSION, 0, sizeof(WORKER_BATCH_SESSION));
	WORKER_BATCH_SESSION.pid = -1;
	WORKER_BATCH_SESSION.command_fd = -1;
	WORKER_BATCH_SESSION.result_fd = -1;
}

static int worker_batch_format_fd(char argument[4], int fd)
{
	if (fd < 0 || fd > RP_WORKER_BATCH_MAX_FD)
		return 0;
	argument[0] = 0;
	rp_append_uint_text(argument, 4, (unsigned int)fd);
	return argument[0] != 0 && strlen(argument) < 4;
}

static void worker_batch_format_nonce(char argument[17], uint64 nonce)
{
	static const char hex[] = "0123456789abcdef";

	for (int i = 0; i < 16; i++)
		argument[i] = hex[(nonce >> (60 - i * 4)) & 0xf];
	argument[16] = 0;
}

static uint64 worker_batch_nonce(
	uint8 group, const struct agent_info *orchestrator_identity)
{
	uint64 value = (uint64)get_mtime();

	value ^= orchestrator_identity->filesystem_domain;
	value ^= (uint64)(unsigned int)orchestrator_identity->agent_id << 32;
	value ^= (uint64)(group + 1) * 0x9e3779b97f4a7c15ULL;
	value ^= value >> 30;
	value *= 0xbf58476d1ce4e5b9ULL;
	value ^= value >> 27;
	value *= 0x94d049bb133111ebULL;
	value ^= value >> 31;
	return value ? value : (0x5250574200000001ULL | group);
}

/* Closing the command writer first guarantees a blocked runner observes EOF. */
static int worker_batch_reap(int *child_code)
{
	int pid = WORKER_BATCH_SESSION.pid;
	int code = -1;
	int got = -1;

	if (WORKER_BATCH_SESSION.command_fd >= 0)
		close(WORKER_BATCH_SESSION.command_fd);
	WORKER_BATCH_SESSION.command_fd = -1;
	if (WORKER_BATCH_SESSION.result_fd >= 0)
		close(WORKER_BATCH_SESSION.result_fd);
	WORKER_BATCH_SESSION.result_fd = -1;
	if (pid > 0)
		got = waitpid(pid, &code);
	if (child_code)
		*child_code = code;
	worker_batch_session_reset();
	return pid > 0 && got == pid;
}

static int worker_batch_frame_is(
	const struct rp_worker_batch_frame *frame, uint8 kind, uint8 group,
	uint32 index, uint64 nonce, int require_zero_status)
{
	return rp_worker_batch_frame_guard_valid(frame) &&
	       frame->kind == kind && frame->group == group &&
	       frame->index == index && frame->nonce == nonce &&
	       (!require_zero_status || frame->status == 0);
}

static int worker_batch_start(
	const struct program_launch_policy *launch,
	const struct worker_batch_binding *binding,
	const struct agent_info *orchestrator_identity)
{
	const struct worker_batch_group *group =
		worker_batch_group_for_id(binding->group);
	struct rp_worker_batch_frame ready;
	int command_pipe[2] = { -1, -1 };
	int result_pipe[2] = { -1, -1 };
	int pid;

	if (WORKER_BATCH_SESSION.active || WORKER_BATCH_SESSION.pid > 0 ||
	    binding->index != 0 || group == 0 ||
	    group->count == 0 ||
	    pipe(command_pipe) != 0 || pipe(result_pipe) != 0)
		goto fail;

	WORKER_BATCH_SESSION.group = group->group;
	WORKER_BATCH_SESSION.count = group->count;
	WORKER_BATCH_SESSION.next_index = 0;
	WORKER_BATCH_SESSION.command_fd = command_pipe[1];
	WORKER_BATCH_SESSION.result_fd = result_pipe[0];
	WORKER_BATCH_SESSION.nonce = worker_batch_nonce(
		group->group, orchestrator_identity);
	exec_manifest_worker_image(group->runner, WORKER_BATCH_SESSION.image);
	if (!launch_expectation_for(
		    launch, 0, "agent_worker_create", orchestrator_identity,
		    &WORKER_BATCH_SESSION.expected) ||
	    !format_launch_expectation(WORKER_BATCH_SESSION.identity_arg,
				       &WORKER_BATCH_SESSION.expected) ||
	    !worker_batch_format_fd(WORKER_BATCH_SESSION.read_fd_arg,
				    command_pipe[0]) ||
	    !worker_batch_format_fd(WORKER_BATCH_SESSION.write_fd_arg,
				    result_pipe[1]))
		goto fail;
	worker_batch_format_nonce(WORKER_BATCH_SESSION.nonce_arg,
				  WORKER_BATCH_SESSION.nonce);
	if (agent_scope_delegate_fd(command_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(result_pipe[1]) != AGENT_STATUS_OK)
		goto fail;

	pid = agent_worker_create(WORKER_BATCH_SESSION.image,
				  launch->worker_capabilities);
	if (pid == 0) {
		char *runner_argv[] = {
			(char *)group->runner,
			WORKER_BATCH_SESSION.identity_arg,
			WORKER_BATCH_SESSION.read_fd_arg,
			WORKER_BATCH_SESSION.write_fd_arg,
			WORKER_BATCH_SESSION.nonce_arg,
			0,
		};

		close(command_pipe[1]);
		close(result_pipe[0]);
		if (exec(WORKER_BATCH_SESSION.image, runner_argv) < 0) {
			printf("rp_orch: batch_exec_failed group=%d runner=%s\n",
			       group->group, group->runner);
			exit(1);
		}
		exit(1);
	}
	if (pid < 0)
		goto fail;
	WORKER_BATCH_SESSION.pid = pid;
	close(command_pipe[0]);
	command_pipe[0] = -1;
	close(result_pipe[1]);
	result_pipe[1] = -1;
	if (!rp_worker_batch_read_exact(WORKER_BATCH_SESSION.result_fd,
					&ready, sizeof(ready)) ||
	    !worker_batch_frame_is(
		    &ready, RP_WORKER_BATCH_READY, group->group,
		    RP_WORKER_BATCH_READY_INDEX, WORKER_BATCH_SESSION.nonce, 1)) {
		printf("rp_orch: batch_ready_invalid group=%d runner=%s\n",
		       group->group, group->runner);
		worker_batch_reap(0);
		return 0;
	}
	WORKER_BATCH_SESSION.active = 1;
	return 1;

fail:
	if (command_pipe[0] >= 0)
		close(command_pipe[0]);
	if (command_pipe[1] >= 0 &&
	    command_pipe[1] != WORKER_BATCH_SESSION.command_fd)
		close(command_pipe[1]);
	if (result_pipe[0] >= 0 &&
	    result_pipe[0] != WORKER_BATCH_SESSION.result_fd)
		close(result_pipe[0]);
	if (result_pipe[1] >= 0)
		close(result_pipe[1]);
	if (WORKER_BATCH_SESSION.pid > 0 ||
	    WORKER_BATCH_SESSION.command_fd >= 0 ||
	    WORKER_BATCH_SESSION.result_fd >= 0)
		worker_batch_reap(0);
	else
		worker_batch_session_reset();
	printf("rp_orch: batch_start_failed group=%d program=%s\n",
	       binding->group, launch->program);
	return 0;
}

static int run_worker_batch(
	const struct program_launch_policy *launch,
	const struct worker_batch_binding *binding,
	const struct agent_info *orchestrator_identity)
{
	struct rp_worker_batch_frame frame;
	struct rp_launch_expectation expected;
	int pid;
	int child_code = -1;
	int64 start = get_mtime();
	int64 end;
	unsigned long long elapsed;
	int code = RP_WORKER_BATCH_EXIT_PROTOCOL;

	if ((!WORKER_BATCH_SESSION.active &&
	     !worker_batch_start(launch, binding, orchestrator_identity)) ||
	    !WORKER_BATCH_SESSION.active ||
	    WORKER_BATCH_SESSION.group != binding->group ||
	    WORKER_BATCH_SESSION.next_index != binding->index) {
		if (WORKER_BATCH_SESSION.active || WORKER_BATCH_SESSION.pid > 0)
			worker_batch_reap(0);
		end = get_mtime();
		elapsed = end >= start ? (unsigned long long)(end - start) : 0;
		record_timing(launch->program, "agent_worker_batch", 0, 0, 0,
			      0, code, elapsed);
		return 0;
	}

	pid = WORKER_BATCH_SESSION.pid;
	expected = WORKER_BATCH_SESSION.expected;
	rp_worker_batch_frame_init(&frame, RP_WORKER_BATCH_RUN,
				   WORKER_BATCH_SESSION.group,
				   WORKER_BATCH_SESSION.next_index, 0,
				   WORKER_BATCH_SESSION.nonce);
	if (!rp_worker_batch_write_exact(WORKER_BATCH_SESSION.command_fd,
					 &frame, sizeof(frame)) ||
	    !rp_worker_batch_read_exact(WORKER_BATCH_SESSION.result_fd,
					&frame, sizeof(frame)) ||
	    !worker_batch_frame_is(
		    &frame, RP_WORKER_BATCH_RESULT, binding->group,
		    binding->index, WORKER_BATCH_SESSION.nonce, 0)) {
		printf("rp_orch: batch_result_invalid program=%s group=%d index=%u\n",
		       launch->program, binding->group, binding->index);
		goto fail;
	}
	code = frame.status;
	if (code != 0) {
		printf("rp_orch: batch_program_failed program=%s code=%d\n",
		       launch->program, code);
		goto fail;
	}
	WORKER_BATCH_SESSION.next_index++;
	if (WORKER_BATCH_SESSION.next_index == WORKER_BATCH_SESSION.count) {
		rp_worker_batch_frame_init(&frame, RP_WORKER_BATCH_STOP,
					   WORKER_BATCH_SESSION.group,
					   WORKER_BATCH_SESSION.next_index, 0,
					   WORKER_BATCH_SESSION.nonce);
		if (!rp_worker_batch_write_exact(WORKER_BATCH_SESSION.command_fd,
						 &frame, sizeof(frame)) ||
		    !rp_worker_batch_read_exact(WORKER_BATCH_SESSION.result_fd,
						&frame, sizeof(frame)) ||
		    !worker_batch_frame_is(
			    &frame, RP_WORKER_BATCH_STOPPED, binding->group,
			    WORKER_BATCH_SESSION.next_index,
			    WORKER_BATCH_SESSION.nonce, 1)) {
			printf("rp_orch: batch_stop_invalid group=%d\n",
			       binding->group);
			goto fail;
		}
		if (!worker_batch_reap(&child_code) || child_code != 0) {
			printf("rp_orch: batch_wait_failed group=%d code=%d\n",
			       binding->group, child_code);
			code = child_code;
			goto record_failure;
		}
	}
	end = get_mtime();
	elapsed = end >= start ? (unsigned long long)(end - start) : 0;
	record_timing(launch->program, "agent_worker_batch", pid, &expected,
		      RP_LAUNCH_BATCH_IDENTITY_SOURCE, 1, 0, elapsed);
	return 1;

fail:
	worker_batch_reap(&child_code);
record_failure:
	end = get_mtime();
	elapsed = end >= start ? (unsigned long long)(end - start) : 0;
	record_timing(launch->program, "agent_worker_batch", pid, &expected,
		      RP_LAUNCH_BATCH_IDENTITY_SOURCE, 0, code, elapsed);
	return 0;
}

static int run_child(const struct program_launch_policy *launch,
		     int in_orchestrator,
		     const struct agent_info *orchestrator_identity)
{
	int pid;
	const char *program = launch->program;
	const struct trusted_launch_policy *policy =
		trusted_launch_for_program(program);
	int agent_child = in_orchestrator && policy != 0;
	int role = policy ? policy->role : AGENT_ROLE_SENTINEL;
	struct rp_launch_expectation expected;
	const char *launcher = agent_child ? "agent_create_role" :
		in_orchestrator ? "agent_worker_create" : "fork";
	char worker_image[11];
	char identity_arg[RP_LAUNCH_EXPECT_ARG_SIZE];
	const char *image = program;
	int64 start = get_mtime();
	int expectation_ready = 0;

	if (agent_child)
		image = policy->image;
	else if (in_orchestrator) {
		exec_manifest_worker_image(program, worker_image);
		image = worker_image;
	}
	if (in_orchestrator) {
		expectation_ready = launch_expectation_for(
			launch, policy, launcher, orchestrator_identity, &expected);
		if (!expectation_ready ||
		    !format_launch_expectation(identity_arg, &expected)) {
			printf("rp_orch: launch_expectation_failed program=%s\n",
			       program);
			return 0;
		}
	}
	if (agent_child) {
		pid = agent_create_role(role);
	} else if (in_orchestrator) {
		pid = agent_worker_create(image, launch->worker_capabilities);
	} else {
		pid = fork();
	}
	if (pid == 0) {
		char *argv[] = {
			(char *)program,
			expectation_ready ? identity_arg : 0,
			0,
		};
		if (exec(image, argv) < 0) {
			printf("rp_orch: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	if (pid < 0) {
		printf("rp_orch: create_failed program=%s role=%s\n",
		       program, role_name(role));
		record_timing(program, launcher, 0, 0, 0, 0, -1, 0);
		return 0;
	}
	int code = -1;
	int got = waitpid(pid, &code);
	int64 end = get_mtime();
	unsigned long long elapsed = end >= start ? (unsigned long long)(end - start) : 0;
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		record_timing(program, launcher, 0, 0, 0, 0, code, elapsed);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		record_timing(program, launcher, 0, 0, 0, 0, code, elapsed);
		return 0;
	}
	if (in_orchestrator && !expectation_ready) {
		printf("rp_orch: identity_self_check_unbound program=%s launcher=%s\n",
		       program, launcher);
		record_timing(program, launcher, 0, 0, 0, 0, code, elapsed);
		return 0;
	}
	record_timing(program, launcher, pid,
		      expectation_ready ? &expected : 0,
		      RP_LAUNCH_IDENTITY_SOURCE, 1, code, elapsed);
	return 1;
}

int main(int argc, char **argv)
{
	int64 steady_clock = get_mtime();
	struct rp_workflow_handoff timing_handoff;
	uint64 workflow_start;
	uint64 ready_ms;
	uint64 phase_mask;
	struct agent_info orchestrator_identity;
	int completion_fd = -1;
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	int context_status = orchestrator_context(&orchestrator_identity);
	int in_orchestrator;

	if (steady_clock < 0) {
		printf("rp_orch: workflow_clock_failed\n");
		return 1;
	}
	if (context_status < 0) {
		printf("rp_orch: orchestrator_identity_invalid\n");
		return 1;
	}
	in_orchestrator = context_status > 0;
	if (in_orchestrator) {
		if (!read_workflow_handoff(argc, argv, &timing_handoff) ||
		    timing_handoff.ready_ms > (uint64)steady_clock ||
		    (completion_fd = workflow_handoff_fd(
			     argc, argv, 2,
			     RP_WORKFLOW_COMPLETION_PREFIX)) < 0) {
			printf("rp_orch: workflow_handoff_invalid\n");
			return 1;
		}
		workflow_start = timing_handoff.start_ms;
		ready_ms = timing_handoff.ready_ms;
		phase_mask = timing_handoff.phase_mask;
	} else {
		workflow_start = (uint64)steady_clock;
		ready_ms = (uint64)steady_clock;
		phase_mask = 0;
	}
	if (!launch_manifest_valid()) {
		printf("rp_orch: launch_manifest_invalid\n");
		return 1;
	}
	worker_batch_session_reset();
	if (in_orchestrator) {
		if (!rp_write_file("rp_agentos_roles",
				   "launcher=agentos-orchestrator\n"
				   "stage_launch=agent_create_role\n"
				   "support_launch=agent_worker_batch\n"
				   "support_spawn=agent_worker_create\n"
				   "support_role=delegated_non_agent_worker\n"
				   "worker_batch_groups=3\n"
				   "worker_batch_programs=58\n"
				   "worker_direct_launch=agent_worker_create\n"
				   "worker_direct_programs=2\n"
				   "worker_batch_identity=trusted_crt_batch_dispatch\n"
				   "worker_direct_identity=trusted_crt_self_check\n"
				   "role_policy=program_specific\n"
				   "launch_policy=kernel_bound_roles_and_delegated_workers\n"
				   "agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_service_surface,rp_backend\n"
				   "execution_ledger=rp_orch_timing\n"
				   "status=ready\n")) {
			return 1;
		}
	}
	rp_copy_text(rp_state_buf, RP_STATE_BUFFER_SIZE,
		     in_orchestrator ?
			     "orchestrator=rp_orch\nlauncher=mixed_attested\n" :
			     "orchestrator=rp_orch\nlauncher=fork\n");
	printf("rp_orch: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		const struct worker_batch_binding *binding =
			worker_batch_binding_for_program(PROGRAMS[i].program);
		int passed = in_orchestrator && binding ?
			run_worker_batch(&PROGRAMS[i], binding,
					 &orchestrator_identity) :
			run_child(&PROGRAMS[i], in_orchestrator,
				  &orchestrator_identity);

		if (!passed) {
			if (WORKER_BATCH_SESSION.active ||
			    WORKER_BATCH_SESSION.pid > 0)
				worker_batch_reap(0);
			break;
		}
		ok++;
	}
	if (WORKER_BATCH_SESSION.active || WORKER_BATCH_SESSION.pid > 0) {
		printf("rp_orch: batch_session_left_active\n");
		worker_batch_reap(0);
		ok = 0;
	}
	printf("rp_orch: programs_ok=%d programs_total=%d\n", ok, total);
	if (!rp_write_file("rp_orch_timing", rp_state_buf))
		return 1;
	if (ok != total) {
		printf("rp_orch: failed\n");
		return 1;
	}
	if (!append_program_inventory_evidence(in_orchestrator)) {
		printf("rp_orch: program_inventory_failed\n");
		return 1;
	}
	if (in_orchestrator) {
		if (!write_workflow_completion(completion_fd, &timing_handoff,
					       (uint64)steady_clock)) {
			printf("rp_orch: workflow_completion_failed\n");
			return 1;
		}
		close(completion_fd);
	} else if (!record_workflow_timing(
			   workflow_start, ready_ms, (uint64)steady_clock,
			   phase_mask, "rp_orch", "direct")) {
		printf("rp_orch: workflow_timing_failed\n");
		return 1;
	}
	printf("rp_orch: state_ok=1\n");
	printf("rp_orch: passed\n");
	return 0;
}
