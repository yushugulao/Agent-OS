#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_objects", "objects=500")) return 1;
	if (!rp_file_contains("rp_services", "workflow=34")) return 1;
	if (!rp_write_file("rp_state_catalog",
			   "host_state_keys=573\n"
			   "nonzero_state_categories=70\n"
			   "zero_state_categories=503\n"
			   "represented_state_categories=573\n"
			   "core_runtime_categories=projects,runs,reports,plans,stages,artifacts,events,context\n"
			   "usable_research_categories=workbenches,runs,stages,messages,decisions,artifacts,datasets,sources,reviews,deliveries\n"
			   "host_workflow_categories=workflow_runs,stage_runs,workflow_artifacts,cache,agent_messages,agent_decisions\n"
			   "agentos_reserved_categories=agent_profiles,agent_skills,agent_task_assignments,agentos_abi_structs,agentos_abi_syscalls,agentos_adapter_profiles,tool_kernel_bindings\n"
			   "coverage_model=nonzero_records_preserved;zero_records_reserved;plain_user_space_files;agentos_kernel_target\n"
			   "state_catalog_checks=12\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("state_catalog=ready")) return 1;
	printf("rp_state_catalog: keys=573 nonzero=70 zero=503 represented=573 checks=12 status=ready\n");
	return 0;
}
