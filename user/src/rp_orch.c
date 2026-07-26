#include <agent.h>
#include <exec_policy_manifest.h>
#include <research_platform_state.h>
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
#define RP_PROGRAM(name) { name, RP_WORKFLOW_WORKER }
static const struct program_launch_policy PROGRAMS[] = {
	RP_PROGRAM("rp_catalog"),
	RP_PROGRAM("rp_state_catalog"),
	RP_PROGRAM("rp_object_store"),
	RP_PROGRAM("rp_object_query"),
	RP_PROGRAM("rp_lineage"),
	RP_PROGRAM("rp_site_export"),
	RP_PROGRAM("rp_planner"),
	RP_PROGRAM("rp_portability"),
	RP_PROGRAM("rp_retriever"),
	RP_PROGRAM("rp_analyst"),
	RP_PROGRAM("rp_reviewer"),
	RP_PROGRAM("rp_lab"),
	RP_PROGRAM("rp_governance"),
	RP_PROGRAM("rp_writer"),
	RP_PROGRAM("rp_repair"),
	RP_PROGRAM("rp_auditor"),
	RP_PROGRAM("rp_query"),
	RP_PROGRAM("rp_evidence"),
	RP_PROGRAM("rp_llm_bridge"),
	RP_PROGRAM("rp_llm_relay"),
	RP_PROGRAM("rp_privacy"),
	RP_PROGRAM("rp_runconf"),
	RP_PROGRAM("rp_execobs"),
	RP_PROGRAM("rp_invoke"),
	RP_PROGRAM("rp_complete"),
	RP_PROGRAM("rp_artifact_ops"),
	RP_PROGRAM("rp_data_pipeline"),
	RP_PROGRAM("rp_workflow_runner"),
	RP_PROGRAM("rp_workbench"),
	RP_PROGRAM("rp_agent_collab"),
	RP_PROGRAM("rp_package"),
	RP_PROGRAM("rp_calculation"),
	RP_PROGRAM("rp_realtask"),
	RP_PROGRAM("rp_analysisres"),
	RP_PROGRAM("rp_campaign"),
	RP_PROGRAM("rp_delta"),
	RP_PROGRAM("rp_release"),
	RP_PROGRAM("rp_dossier"),
	RP_PROGRAM("rp_service_surface"),
	RP_PROGRAM("rp_startup_doctor"),
	RP_PROGRAM("rp_notebook_export"),
	RP_PROGRAM("rp_backend"),
	RP_PROGRAM("rp_consistency"),
	RP_PROGRAM("rp_metrics"),
	RP_PROGRAM("rp_ui_export"),
	RP_PROGRAM("rp_web_export"),
	RP_PROGRAM("rp_revdash"),
	RP_PROGRAM("rp_modelreg"),
	RP_PROGRAM("rp_sysreview"),
	RP_PROGRAM("rp_expsched"),
	RP_PROGRAM("rp_traincomp"),
	RP_PROGRAM("rp_publication"),
	RP_PROGRAM("rp_runbooks"),
	RP_PROGRAM("rp_projectrel"),
	RP_PROGRAM("rp_studyproto"),
	RP_PROGRAM("rp_stdesign"),
	RP_PROGRAM("rp_opsboard"),
	RP_PROGRAM("rp_reviewboard"),
	RP_PROGRAM("rp_controlplane"),
	RP_PROGRAM("rp_integrityplane"),
	RP_PROGRAM("rp_coherenceplane"),
	RP_PROGRAM("rp_mature"),
	RP_PROGRAM("rp_prov_view"),
	RP_PROGRAM("rp_prov_query"),
	RP_PROGRAM("rp_reldossier"),
	RP_PROGRAM("rp_decsupport"),
	RP_PROGRAM("rp_usable"),
	RP_PROGRAM("rp_usableproject"),
	RP_PROGRAM("rp_compare_plain"),
	RP_PROGRAM("rp_test_suite"),
};
#undef RP_PROGRAM

struct trusted_launch_policy {
	const char *program;
	const char *image;
	int role;
};

#define TRUSTED_LAUNCH_ROW(source, image, flags, role_mask, launch_role, profile) \
	{ source, image, launch_role },
static const struct trusted_launch_policy TRUSTED_LAUNCHES[] = {
	EXEC_POLICY_ENTRIES(TRUSTED_LAUNCH_ROW)
};
#undef TRUSTED_LAUNCH_ROW

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

static const char *launch_role_name(int role, int agent_child)
{
	return agent_child ? role_name(role) : "plain";
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

static void record_timing(const char *program, int role, int agent_child,
			  int ok, int code, unsigned long long elapsed_ms)
{
	char line[224];

	rp_copy_text(line, sizeof(line), "program=");
	rp_append_text(line, sizeof(line), program);
	rp_append_text(line, sizeof(line), ";role=");
	rp_append_text(line, sizeof(line), launch_role_name(role, agent_child));
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

static int run_child(const struct program_launch_policy *launch,
		     int in_orchestrator)
{
	int pid;
	const char *program = launch->program;
	const struct trusted_launch_policy *policy =
		trusted_launch_for_program(program);
	int agent_child = in_orchestrator && policy != 0;
	int role = policy ? policy->role : AGENT_ROLE_SENTINEL;
	char worker_image[11];
	const char *image = program;
	int64 start = get_mtime();

	if (agent_child)
		image = policy->image;
	else if (in_orchestrator) {
		exec_manifest_worker_image(program, worker_image);
		image = worker_image;
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
		record_timing(program, role, agent_child, 0, -1, 0);
		return 0;
	}
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
	int in_orchestrator = orchestrator_context();
	if (in_orchestrator) {
		if (!rp_write_file("rp_agentos_roles",
				   "launcher=agentos-orchestrator\n"
				   "stage_launch=agent_create_role\n"
				   "support_launch=fork\n"
				   "support_role=plain_process\n"
				   "role_policy=program_specific\n"
				   "launch_policy=kernel_bound_programs_agent_plain_support_fork\n"
				   "agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_service_surface,rp_backend\n"
				   "execution_ledger=rp_orch_timing\n"
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
		ok += run_child(&PROGRAMS[i], in_orchestrator);
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
