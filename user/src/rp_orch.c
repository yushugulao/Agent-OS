#include <stdio.h>
#include <stdlib.h>
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
	"rp_compare_plain",
};

static int run_child(const char *program)
{
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
