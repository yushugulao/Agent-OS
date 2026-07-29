#include <stdio.h>
#include <stdlib.h>
#include <research_platform_state.h>
#include <rp_evidence.h>
#include <rp_program_manifest.h>
#include <unistd.h>

#define RP_PROGRAM_NAME(name) name,
static const char *PROGRAMS[] = {
	RP_PLATFORM_PROGRAMS(RP_PROGRAM_NAME)
};
#undef RP_PROGRAM_NAME

static void record_timing(const char *program, int ok, int code,
			  unsigned long long elapsed_ms)
{
	char line[192];

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	rp_append_text(line, sizeof(line), ";launcher=fork");
	rp_append_text(line, sizeof(line), ";ok=");
	rp_append_text(line, sizeof(line), ok ? "1" : "0");
	rp_append_text(line, sizeof(line), ";code=");
	rp_append_uint_text(line, sizeof(line), code < 0 ? 9999 : (unsigned int)code);
	rp_append_text(line, sizeof(line), ";elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), elapsed_ms);
	rp_append_file("rp_orch_timing", line);
}

static int append_program_inventory_evidence(void)
{
	struct rp_evidence_program_inventory inventory;
	char line[384];
	int expected_programs = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));

	if (!rp_evidence_measure_program_ledger("rp_orch_timing", "rp_orch",
						PROGRAMS,
						expected_programs, "fork", 0,
						&inventory))
		return 0;
	line[0] = 0;
	rp_append_text(line, sizeof(line),
		       "evidence_role=demo_reference;evidence_generation=runtime;observation_source=guest_runtime;program_source=rp_orch_timing;program_source_bytes=");
	rp_append_uint_text(line, sizeof(line), inventory.source_bytes);
	rp_append_text(line, sizeof(line), ";program_source_hash=");
	rp_append_uint_text(line, sizeof(line), inventory.source_hash);
	rp_append_text(line, sizeof(line), ";program_names_digest=");
	rp_append_uint_text(line, sizeof(line), inventory.program_names_digest);
	rp_append_text(line, sizeof(line), ";programs_observed=");
	rp_append_uint_text(line, sizeof(line), inventory.programs_observed);
	rp_append_text(line, sizeof(line), ";status=reference_observed");
	if (!rp_append_file("rp_agentcmp", line))
		return 0;
	for (int i = 0; line[i]; i++)
		if (line[i] == ';')
			line[i] = ' ';
	printf("rp_orch: ");
	puts(line);
	return 1;
}

static int run_child(const char *program)
{
	int64 start = get_mtime();
	int pid = fork();
	if (pid == 0) {
		char *argv[] = {
			(char *)program,
			0,
		};
		if (exec(program, argv) < 0) {
			printf("rp_orch: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	if (pid < 0) {
		printf("rp_orch: fork_failed program=%s\n", program);
		record_timing(program, 0, -1, 0);
		return 0;
	}
	int code = -1;
	int got = waitpid(pid, &code);
	int64 end = get_mtime();
	unsigned long long elapsed = end >= start ? (unsigned long long)(end - start) : 0;
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		record_timing(program, 0, code, elapsed);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		record_timing(program, 0, code, elapsed);
		return 0;
	}
	record_timing(program, 1, code, elapsed);
	return 1;
}

int main(void)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	if (!rp_write_file("rp_orch_timing",
			   "orchestrator=rp_orch\nlauncher=fork\n")) {
		return 1;
	}
	printf("rp_orch: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(PROGRAMS[i]);
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
	printf("rp_orch: state_ok=1\n");
	printf("rp_orch: passed\n");
	return 0;
}
