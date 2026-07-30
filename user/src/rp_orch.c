#include <agent.h>
#include <exec_policy_manifest.h>
#include <research_platform_state.h>
#include <rp_evidence.h>
#include <rp_launch_attestation.h>
#include <rp_program_manifest.h>
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

#define RP_WORKFLOW_HANDOFF_MAGIC 0x52505754U
#define RP_WORKFLOW_COMPLETION_MAGIC 0x52505743U
#define RP_WORKFLOW_HANDOFF_VERSION 1U
#define RP_WORKFLOW_HANDOFF_PREFIX "--rp-workflow-timing-fd="
#define RP_WORKFLOW_COMPLETION_PREFIX "--rp-workflow-completion-fd="
#define RP_WORKFLOW_PHASE_ALL ((1ULL << 8) - 1)

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
	if (read(fd, &extra, 1) != 0) {
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
};

struct declared_role_policy {
	const char *program;
	const char *role;
};

#define TRUSTED_LAUNCH_ROW(source, image, flags, role_mask, launch_role, profile) \
	{ source, image, launch_role },
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

static int orchestrator_context(void)
{
	struct agent_info info;

	return agent_info(&info) == 0 && info.is_agent &&
	       info.agent_role == AGENT_ROLE_ORCHESTRATOR;
}

static int launch_manifest_valid(void)
{
	int declared = (int)(sizeof(DECLARED_ROLES) / sizeof(DECLARED_ROLES[0]));
	int trusted = 0;

	for (uint i = 0; i < sizeof(PROGRAMS) / sizeof(PROGRAMS[0]); i++) {
		const struct trusted_launch_policy *policy =
			trusted_launch_for_program(PROGRAMS[i].program);
		const char *role = declared_role_for_program(PROGRAMS[i].program);

		if ((policy == 0) != (role == 0) ||
		    (policy && strcmp(role_name(policy->role), role) != 0))
			return 0;
		if (policy)
			trusted++;
	}
	return trusted == declared;
}

static int read_launch_attestation(int fd,
				   struct rp_launch_attestation *attestation)
{
	char *bytes = (char *)attestation;
	int received = 0;

	memset(attestation, 0, sizeof(*attestation));
	while (received < (int)sizeof(*attestation)) {
		int n = read(fd, bytes + received,
			     sizeof(*attestation) - received);

		if (n <= 0)
			return 0;
		received += n;
	}
	return 1;
}

static int launch_attestation_valid(
	const struct program_launch_policy *launch,
	const struct trusted_launch_policy *policy, const char *launcher,
	int pid, const struct rp_launch_attestation *attestation)
{
	if (attestation->magic != RP_LAUNCH_ATTEST_MAGIC ||
	    attestation->version != RP_LAUNCH_ATTEST_VERSION ||
	    attestation->status != 0 || attestation->pid != pid ||
	    attestation->filesystem_domain == 0)
		return 0;
	if (policy != 0)
		return strcmp(launcher, "agent_create_role") == 0 &&
		       attestation->is_agent == 1 &&
		       attestation->agent_role == policy->role &&
		       attestation->filesystem_capability_mask != 0;
	return strcmp(launcher, "agent_worker_create") == 0 &&
	       attestation->is_agent == 0 && attestation->agent_role == 0 &&
	       attestation->filesystem_capability_mask ==
		       launch->worker_capabilities;
}

static void record_timing(const char *program, const char *launcher,
			  const struct rp_launch_attestation *attestation,
			  int ok, int code, unsigned long long elapsed_ms)
{
	char line[256];
	int identity_ready = attestation != 0 && attestation->status == 0;

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	rp_append_text(line, sizeof(line), ";role=");
	rp_append_text(line, sizeof(line),
		       identity_ready && attestation->is_agent ?
			       role_name(attestation->agent_role) : "plain");
	rp_append_text(line, sizeof(line), ";launcher=");
	rp_append_text(line, sizeof(line), launcher);
	rp_append_text(line, sizeof(line), ";identity_source=");
	rp_append_text(line, sizeof(line),
		       identity_ready ? "child_after_exec" : "unavailable");
	rp_append_text(line, sizeof(line), ";is_agent=");
	rp_append_uint_text(line, sizeof(line),
			    identity_ready ? attestation->is_agent : 0);
	rp_append_text(line, sizeof(line), ";agent_role=");
	rp_append_uint_text(line, sizeof(line),
			    identity_ready ? attestation->agent_role : 0);
	rp_append_text(line, sizeof(line), ";filesystem_domain=");
	rp_append_uint_text(line, sizeof(line),
			    identity_ready ? attestation->filesystem_domain : 0);
	rp_append_text(line, sizeof(line), ";filesystem_capabilities=");
	rp_append_uint_text(
		line, sizeof(line),
		identity_ready ? attestation->filesystem_capability_mask : 0);
	rp_append_text(line, sizeof(line), ";ok=");
	rp_append_text(line, sizeof(line), ok ? "1" : "0");
	rp_append_text(line, sizeof(line), ";code=");
	rp_append_uint_text(line, sizeof(line), code < 0 ? 9999 : (unsigned int)code);
	rp_append_text(line, sizeof(line), ";elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), elapsed_ms);
	rp_append_file("rp_orch_timing", line);
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

static int append_program_inventory_evidence(void)
{
	struct rp_evidence_program_inventory inventory;
	char line[384];
	int expected_programs = (int)(sizeof(PROGRAM_NAMES) /
				     sizeof(PROGRAM_NAMES[0]));

	if (!rp_evidence_measure_program_ledger("rp_orch_timing", "rp_orch",
						PROGRAM_NAMES,
						expected_programs,
						"mixed_attested", 1,
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

static int run_child(const struct program_launch_policy *launch,
		     int in_orchestrator)
{
	int pid;
	const char *program = launch->program;
	const struct trusted_launch_policy *policy =
		trusted_launch_for_program(program);
	int agent_child = in_orchestrator && policy != 0;
	int role = policy ? policy->role : AGENT_ROLE_SENTINEL;
	int attest_pipe[2];
	struct rp_launch_attestation attestation;
	const char *launcher = agent_child ? "agent_create_role" :
		in_orchestrator ? "agent_worker_create" : "fork";
	char worker_image[11];
	char attest_arg[32];
	const char *image = program;
	int64 start = get_mtime();
	int attested = 0;

	if (agent_child)
		image = policy->image;
	else if (in_orchestrator) {
		exec_manifest_worker_image(program, worker_image);
		image = worker_image;
	}

	if (pipe(attest_pipe) < 0) {
		printf("rp_orch: attest_pipe_failed program=%s\n", program);
		return 0;
	}
	if (in_orchestrator && agent_scope_delegate_fd(attest_pipe[1]) < 0) {
		close(attest_pipe[0]);
		close(attest_pipe[1]);
		printf("rp_orch: attest_delegate_failed program=%s\n", program);
		return 0;
	}
	if (agent_child) {
		pid = agent_create_role(role);
	} else if (in_orchestrator) {
		pid = agent_worker_create(image, launch->worker_capabilities);
	} else {
		pid = fork();
	}
	if (pid == 0) {
		close(attest_pipe[0]);
		rp_copy_text(attest_arg, sizeof(attest_arg),
			     RP_LAUNCH_ATTEST_PREFIX);
		rp_append_uint_text(attest_arg, sizeof(attest_arg), attest_pipe[1]);
		char *argv[] = {
			(char *)program,
			attest_arg,
			0,
		};
		if (exec(image, argv) < 0) {
			printf("rp_orch: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	close(attest_pipe[1]);
	if (pid < 0) {
		close(attest_pipe[0]);
		printf("rp_orch: create_failed program=%s role=%s\n",
		       program, role_name(role));
		record_timing(program, launcher, 0, 0, -1, 0);
		return 0;
	}
	attested = read_launch_attestation(attest_pipe[0], &attestation);
	close(attest_pipe[0]);
	int code = -1;
	int got = waitpid(pid, &code);
	int64 end = get_mtime();
	unsigned long long elapsed = end >= start ? (unsigned long long)(end - start) : 0;
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		record_timing(program, launcher,
			      attested ? &attestation : 0, 0, code, elapsed);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		record_timing(program, launcher,
			      attested ? &attestation : 0, 0, code, elapsed);
		return 0;
	}
	if (!attested || !launch_attestation_valid(launch, policy, launcher, pid,
						    &attestation)) {
		printf("rp_orch: identity_attestation_failed program=%s launcher=%s\n",
		       program, launcher);
		record_timing(program, launcher,
			      attested ? &attestation : 0, 0, code, elapsed);
		return 0;
	}
	record_timing(program, launcher, &attestation, 1, code, elapsed);
	return 1;
}

int main(int argc, char **argv)
{
	int64 steady_clock = get_mtime();
	struct rp_workflow_handoff timing_handoff;
	uint64 workflow_start;
	uint64 ready_ms;
	uint64 phase_mask;
	int completion_fd = -1;
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	int in_orchestrator = orchestrator_context();

	if (steady_clock < 0) {
		printf("rp_orch: workflow_clock_failed\n");
		return 1;
	}
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
	if (in_orchestrator) {
		if (!rp_write_file("rp_agentos_roles",
				   "launcher=agentos-orchestrator\n"
				   "stage_launch=agent_create_role\n"
				   "support_launch=agent_worker_create\n"
				   "support_role=delegated_non_agent_worker\n"
				   "role_policy=program_specific\n"
				   "launch_policy=kernel_bound_roles_and_delegated_workers\n"
				   "agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_service_surface,rp_backend\n"
				   "execution_ledger=rp_orch_timing\n"
				   "status=ready\n")) {
			return 1;
		}
	}
	if (!rp_write_file("rp_orch_timing",
			   "orchestrator=rp_orch\nlauncher=mixed_attested\n")) {
		return 1;
	}
	printf("rp_orch: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(&PROGRAMS[i], in_orchestrator);
	}
	printf("rp_orch: programs_ok=%d programs_total=%d\n", ok, total);
	if (ok != total) {
		printf("rp_orch: failed\n");
		return 1;
	}
	if (!append_program_inventory_evidence()) {
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
