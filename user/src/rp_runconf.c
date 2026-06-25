#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "workflow=lab-gene-x");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	ok = ok && rp_file_contains("rp_wfio", "portable_steps=10");
	ok = ok && rp_file_contains("rp_policy", "access_profiles=4");
	ok = ok && rp_file_contains("rp_datadic", "schema_fields=17");
	ok = ok && rp_file_contains("rp_mail", "to=runconf");
	if (!ok) return 1;
	if (!rp_write_file("rp_params",
			   "run_id=RUN-042\n"
			   "parameter_sets=2\n"
			   "baseline_memory_mb=1024\n"
			   "candidate_memory_mb=1536\n"
			   "aligner_baseline=agent-align-v2\n"
			   "aligner_candidate=agent-align-v3\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_runconf",
			   "run_id=RUN-042\n"
			   "profiles=2\n"
			   "baseline=plain-user-runtime\n"
			   "candidate=agentos-ucore\n"
			   "template=lab-gene-x-nightly\n"
			   "environment_refs=2\n"
			   "budget_ref=ready\n"
			   "schema_ref=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_configval",
			   "profiles=2\n"
			   "validations=2\n"
			   "checked_items=12\n"
			   "warnings=0\n"
			   "missing_parameters=0\n"
			   "invalid_parameters=0\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_configdrift",
			   "baseline=plain-user-runtime\n"
			   "candidate=agentos-ucore\n"
			   "changed_parameters=2\n"
			   "environment_change=1\n"
			   "risk=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=runconf;msg=18;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=runconf.write_params;target=rp_params;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=runconf.create_profiles;target=rp_runconf;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=runconf.validate_profiles;target=rp_configval;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=runconf.compare_profiles;target=rp_configdrift;status=ok")) return 1;
	if (!rp_append_status("params=ready")) return 1;
	if (!rp_append_status("runconf=ready")) return 1;
	if (!rp_append_status("configval=ready")) return 1;
	if (!rp_append_status("configdrift=ready")) return 1;
	printf("rp_runconf: profiles=2 validations=2 drift=1 status=ready\n");
	return 0;
}
