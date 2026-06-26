#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <research_platform_state.h>

static const char *PROGRAMS[] = {
	"rp_catalog",
	"rp_object_store",
	"rp_object_query",
	"rp_lineage",
	"rp_site_export",
	"rp_planner",
	"rp_portability",
	"rp_retriever",
	"rp_analyst",
	"rp_reviewer",
	"rp_lab",
	"rp_governance",
	"rp_writer",
	"rp_repair",
	"rp_auditor",
	"rp_query",
	"rp_evidence",
	"rp_llm_bridge",
	"rp_llm_relay",
	"rp_privacy",
	"rp_runconf",
	"rp_execobs",
	"rp_invoke",
	"rp_complete",
	"rp_artifact_ops",
	"rp_data_pipeline",
	"rp_workflow_runner",
	"rp_workbench",
	"rp_agent_collab",
	"rp_package",
	"rp_delta",
	"rp_release",
	"rp_dossier",
	"rp_service_surface",
	"rp_notebook_export",
	"rp_backend",
	"rp_consistency",
	"rp_metrics",
	"rp_ui_export",
	"rp_web_export",
	"rp_test_suite",
	"rp_compare_plain",
};

#define RP_SEED_CHUNK_SIZE 280
#define RP_MAX_SEED_CHUNKS 12

static int text_eq(const char *a, const char *b)
{
	return strcmp(a, b) == 0;
}

static int line_has(const char *start, const char *end, const char *needle)
{
	int n = (int)strlen(needle);
	for (const char *p = start; p + n <= end; p++) {
		int same = 1;
		for (int i = 0; i < n; i++) {
			if (p[i] != needle[i]) {
				same = 0;
				break;
			}
		}
		if (same) return 1;
	}
	return 0;
}

static int line_is_workbench(const char *start, const char *end)
{
	return line_has(start, end, "kind=workbench");
}

static int program_wants_line(const char *program, const char *start, const char *end)
{
	if (text_eq(program, "rp_reviewer")) return line_has(start, end, "kind=human_review");
	if (text_eq(program, "rp_retriever")) {
		return line_has(start, end, "kind=literature_search") ||
		       line_has(start, end, "kind=evidence_review") ||
		       line_has(start, end, "kind=evidence_protocol");
	}
	if (text_eq(program, "rp_evidence")) {
		return line_has(start, end, "kind=library_source") ||
		       line_has(start, end, "kind=literature_search") ||
		       line_has(start, end, "kind=evidence_review") ||
		       line_has(start, end, "kind=evidence_protocol");
	}
	if (text_eq(program, "rp_writer")) {
		return line_has(start, end, "kind=revision_task") ||
		       line_has(start, end, "kind=revision_run") ||
		       line_has(start, end, "kind=workbench_manuscript");
	}
	if (text_eq(program, "rp_artifact_ops")) {
		return line_has(start, end, "kind=research_run") ||
		       line_has(start, end, "kind=dataset") ||
		       line_has(start, end, "kind=library_source") ||
		       line_has(start, end, "kind=template") ||
		       line_has(start, end, "kind=workspace_") ||
		       line_has(start, end, "kind=human_review") ||
		       line_has(start, end, "kind=revision_task") ||
		       line_has(start, end, "kind=bundle_export") ||
		       line_has(start, end, "kind=agentcompare") ||
		       line_is_workbench(start, end);
	}
	if (text_eq(program, "rp_data_pipeline")) return line_has(start, end, "kind=research_run");
	if (text_eq(program, "rp_workflow_runner")) {
		return line_has(start, end, "kind=research_run") ||
		       line_has(start, end, "kind=revision_task") ||
		       line_has(start, end, "kind=revision_run") ||
		       line_has(start, end, "kind=notebook_export") ||
		       line_has(start, end, "kind=bundle_export") ||
		       line_has(start, end, "kind=agentcompare") ||
		       line_is_workbench(start, end);
	}
	if (text_eq(program, "rp_workbench")) {
		return 1;
	}
	if (text_eq(program, "rp_package")) {
		return line_has(start, end, "kind=bundle_export") ||
		       line_has(start, end, "kind=research_export") ||
		       line_has(start, end, "kind=delivery") ||
		       line_is_workbench(start, end);
	}
	if (text_eq(program, "rp_notebook_export")) {
		return line_has(start, end, "kind=notebook_export") ||
		       line_is_workbench(start, end);
	}
	if (text_eq(program, "rp_metrics")) {
		return line_has(start, end, "kind=research_run") ||
		       line_has(start, end, "kind=agentcompare") ||
		       line_has(start, end, "kind=human_review") ||
		       line_has(start, end, "kind=revision_task") ||
		       line_has(start, end, "kind=revision_run") ||
		       line_has(start, end, "kind=notebook_export") ||
		       line_has(start, end, "kind=bundle_export") ||
		       line_has(start, end, "kind=research_export") ||
		       line_has(start, end, "kind=delivery") ||
		       line_is_workbench(start, end);
	}
	if (text_eq(program, "rp_web_export") ||
	    text_eq(program, "rp_test_suite") ||
	    text_eq(program, "rp_compare_plain")) {
		return 1;
	}
	return 0;
}

static int append_seed_char(char c, char seed_chunks[RP_MAX_SEED_CHUNKS][RP_SEED_CHUNK_SIZE + 1],
			    int *chunk, int *offset)
{
	if (*chunk >= RP_MAX_SEED_CHUNKS) return 0;
	seed_chunks[*chunk][*offset] = c;
	(*offset)++;
	if (*offset == RP_SEED_CHUNK_SIZE) {
		seed_chunks[*chunk][*offset] = 0;
		(*chunk)++;
		*offset = 0;
	}
	return 1;
}

static int build_seed_chunks(const char *program,
			     char seed_chunks[RP_MAX_SEED_CHUNKS][RP_SEED_CHUNK_SIZE + 1])
{
#ifdef RP_HOST_ACTION_BOOTSTRAP_SEED
	const char *seed = RP_HOST_ACTION_BOOTSTRAP_SEED;
	int chunk = 0;
	int offset = 0;
	int copied = 0;
	while (*seed) {
		const char *start = seed;
		while (*seed && *seed != '\n') seed++;
		const char *end = seed;
		if (program_wants_line(program, start, end)) {
			for (const char *p = start; p < end; p++) {
				if (!append_seed_char(*p, seed_chunks, &chunk, &offset)) return -1;
			}
			if (!append_seed_char('\n', seed_chunks, &chunk, &offset)) return -1;
			copied = 1;
		}
		if (*seed == '\n') seed++;
	}
	if (!copied) return 0;
	if (chunk >= RP_MAX_SEED_CHUNKS) return -1;
	seed_chunks[chunk][offset] = 0;
	return chunk + 1;
#else
	return 0;
#endif
}

static int build_child_argv(const char *program, char **argv, int cap,
			    char seed_chunks[RP_MAX_SEED_CHUNKS][RP_SEED_CHUNK_SIZE + 1])
{
	argv[0] = (char *)program;
	int chunks = build_seed_chunks(program, seed_chunks);
	if (chunks < 0 || cap < chunks + 3) return -1;
	if (chunks == 0) {
		argv[1] = 0;
		return 1;
	}
	argv[1] = RP_HOST_SEED_ARG_MARK;
	for (int i = 0; i < chunks; i++) {
		argv[2 + i] = seed_chunks[i];
	}
	argv[2 + chunks] = 0;
	return 2 + chunks;
}

static int run_child(const char *program)
{
	int pid = fork();
	if (pid == 0) {
		char *argv[RP_MAX_SEED_CHUNKS + 3];
		char seed_chunks[RP_MAX_SEED_CHUNKS][RP_SEED_CHUNK_SIZE + 1];
		if (build_child_argv(program, argv, (int)(sizeof(argv) / sizeof(argv[0])), seed_chunks) < 0) {
			printf("rp_orch: seed_arg_failed program=%s\n", program);
			exit(1);
		}
		if (exec(program, argv) < 0) {
			printf("rp_orch: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		return 0;
	}
	return 1;
}

int main(void)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	printf("rp_orch: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(PROGRAMS[i]);
	}
	printf("rp_orch: programs_ok=%d programs_total=%d\n", ok, total);
	if (ok != total) {
		printf("rp_orch: failed\n");
		return 1;
	}
	printf("rp_orch: state_ok=1\n");
	printf("rp_orch: passed\n");
	return 0;
}
