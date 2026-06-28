#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <research_platform_state.h>

static const char *PROGRAMS[] = {
	"rp_catalog",
	"rp_state_catalog",
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
	"rp_calculation",
	"rp_realtask",
	"rp_analysisres",
	"rp_campaign",
	"rp_delta",
	"rp_release",
	"rp_dossier",
	"rp_service_surface",
	"rp_startup_doctor",
	"rp_notebook_export",
	"rp_backend",
	"rp_consistency",
	"rp_metrics",
	"rp_ui_export",
	"rp_web_export",
	"rp_revdash",
	"rp_modelreg",
	"rp_sysreview",
	"rp_expsched",
	"rp_traincomp",
	"rp_publication",
	"rp_runbooks",
	"rp_projectrel",
	"rp_studyproto",
	"rp_stdesign",
	"rp_opsboard",
	"rp_reviewboard",
	"rp_controlplane",
	"rp_integrityplane",
	"rp_coherenceplane",
	"rp_mature",
	"rp_prov_view",
	"rp_prov_query",
	"rp_reldossier",
	"rp_decsupport",
	"rp_usable",
	"rp_usableproject",
	"rp_compare_plain",
	"rp_test_suite",
};

static int keeps_same_name_state(const char *program)
{
	static const char *KEEP[] = {
		"rp_backend",
		"rp_consistency",
		"rp_campaign",
		"rp_delta",
		"rp_dossier",
		"rp_evidence",
		"rp_execobs",
		"rp_lineage",
		"rp_modelreg",
		"rp_sysreview",
		"rp_expsched",
		"rp_traincomp",
		"rp_object_query",
		"rp_package",
		"rp_calculation",
		"rp_realtask",
		"rp_analysisres",
		"rp_campaign",
		"rp_decsupport",
		"rp_usable",
		"rp_usableproject",
		"rp_projectrel",
		"rp_publication",
		"rp_reldossier",
		"rp_stdesign",
		"rp_studyproto",
		"rp_opsboard",
		"rp_reviewboard",
		"rp_control",
		"rp_integrity",
		"rp_mature",
		"rp_prov_view",
		"rp_prov_query",
		"rp_coherence",
		"rp_privacy",
		"rp_query",
		"rp_release",
		"rp_review_dashboard",
		"rp_runbooks",
		"rp_runconf",
		"rp_state_catalog",
	};
	int total = (int)(sizeof(KEEP) / sizeof(KEEP[0]));
	for (int i = 0; i < total; i++) {
		if (strcmp(program, KEEP[i]) == 0) {
			return 1;
		}
	}
	return 0;
}

static void release_self_image(void)
{
	int fd = open("rp_seed_orch", O_WRONLY | O_TRUNC);
	if (fd >= 0) {
		close(fd);
	}
}

static void record_timing(const char *program, int ok, int code,
			  unsigned long long elapsed_ms)
{
	char line[192];

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	rp_append_text(line, sizeof(line), ";launcher=fork_seeded");
	rp_append_text(line, sizeof(line), ";ok=");
	rp_append_text(line, sizeof(line), ok ? "1" : "0");
	rp_append_text(line, sizeof(line), ";code=");
	rp_append_uint_text(line, sizeof(line), code < 0 ? 9999 : (unsigned int)code);
	rp_append_text(line, sizeof(line), ";elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), elapsed_ms);
	rp_append_file("rp_orch_timing", line);
}

static int run_child(const char *program)
{
	int64 start = get_mtime();
	int pid = fork();
	if (pid == 0) {
		char *argv[] = {(char *)program, 0};
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
	if (keeps_same_name_state(program)) {
		return 1;
	}
	int fd = open(program, O_WRONLY | O_TRUNC);
	if (fd >= 0) {
		close(fd);
	}
	return 1;
}

int main(void)
{
	release_self_image();
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	if (!rp_write_file("rp_orch_timing",
			   "orchestrator=rp_seed_orch\nlauncher=fork_seeded\n")) {
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
	printf("rp_orch: state_ok=1\n");
	printf("rp_orch: passed\n");
	return 0;
}
