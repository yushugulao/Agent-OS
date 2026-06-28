#include <agent.h>
#include <research_platform_state.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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

static const char *role_name(int role)
{
	switch (role) {
	case AGENT_ROLE_ORCHESTRATOR:
		return "orchestrator";
	case AGENT_ROLE_RECOVERY:
		return "recovery";
	case AGENT_ROLE_INVESTIGATOR:
		return "investigator";
	case AGENT_ROLE_SENTINEL:
	default:
		return "sentinel";
	}
}

static int role_for_program(const char *program)
{
	if (strcmp(program, "rp_repair") == 0)
		return AGENT_ROLE_RECOVERY;
	if (strcmp(program, "rp_agent_collab") == 0 ||
	    strcmp(program, "rp_auditor") == 0 ||
	    strcmp(program, "rp_package") == 0 ||
	    strcmp(program, "rp_realtask") == 0 ||
	    strcmp(program, "rp_backend") == 0)
		return AGENT_ROLE_ORCHESTRATOR;
	if (strcmp(program, "rp_query") == 0 ||
	    strcmp(program, "rp_execobs") == 0 ||
	    strcmp(program, "rp_consistency") == 0 ||
	    strcmp(program, "rp_metrics") == 0 ||
	    strcmp(program, "rp_compare_plain") == 0)
		return AGENT_ROLE_INVESTIGATOR;
	return AGENT_ROLE_SENTINEL;
}

static int agent_child_for_program(const char *program)
{
	if (strcmp(program, "rp_query") == 0 ||
	    strcmp(program, "rp_repair") == 0 ||
	    strcmp(program, "rp_execobs") == 0 ||
	    strcmp(program, "rp_agent_collab") == 0 ||
	    strcmp(program, "rp_auditor") == 0 ||
	    strcmp(program, "rp_workbench") == 0 ||
	    strcmp(program, "rp_package") == 0 ||
	    strcmp(program, "rp_realtask") == 0 ||
	    strcmp(program, "rp_backend") == 0)
		return 1;
	return 0;
}

static int orchestrator_context(void)
{
	struct agent_info info;

	return agent_info(&info) == 0 && info.is_agent &&
	       info.agent_role == AGENT_ROLE_ORCHESTRATOR;
}

static void record_stage_role(const char *program, int role, int agent_child)
{
	char line[160];

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	rp_append_text(line, sizeof(line), ";role=");
	rp_append_text(line, sizeof(line), role_name(role));
	rp_append_text(line, sizeof(line), ";launcher=");
	rp_append_text(line, sizeof(line),
		       agent_child ? "agent_create_role" : "fork");
	rp_append_text(line, sizeof(line), ";status=started");
	rp_append_file("rp_agentos_roles", line);
}

static void record_timing(const char *program, int role, int agent_child,
			  int ok, int code, unsigned long long elapsed_ms)
{
	char line[224];

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	rp_append_text(line, sizeof(line), ";role=");
	rp_append_text(line, sizeof(line), role_name(role));
	rp_append_text(line, sizeof(line), ";launcher=");
	rp_append_text(line, sizeof(line),
		       agent_child ? "agent_create_role" : "fork");
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
	int pid;
	int agent_child = 0;
	int role = role_for_program(program);
	int64 start = get_mtime();

	if (orchestrator_context() && agent_child_for_program(program)) {
		pid = agent_create_role(role);
		agent_child = 1;
	} else {
		pid = fork();
	}
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
		printf("rp_orch: create_failed program=%s role=%s\n",
		       program, role_name(role));
		record_timing(program, role, agent_child, 0, -1, 0);
		return 0;
	}
	record_stage_role(program, role, agent_child);
	int code = -1;
	int got = waitpid(pid, &code);
	int64 end = get_mtime();
	unsigned long long elapsed = end >= start ? (unsigned long long)(end - start) : 0;
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		record_timing(program, role, agent_child, 0, code, elapsed);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		record_timing(program, role, agent_child, 0, code, elapsed);
		return 0;
	}
	record_timing(program, role, agent_child, 1, code, elapsed);
	return 1;
}

int main(void)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	if (orchestrator_context()) {
		if (!rp_write_file("rp_agentos_roles",
				   "launcher=agentos-orchestrator\n"
				   "stage_launch=agent_create_role\n"
				   "support_launch=fork\n"
				   "role_policy=program_specific\n"
				   "launch_policy=kernel_bound_programs_agent_plain_support_fork\n"
				   "agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_backend\n"
				   "status=ready\n")) {
			return 1;
		}
	}
	if (!rp_write_file("rp_orch_timing",
			   "orchestrator=rp_orch\nlauncher=agent_create_role\n")) {
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
