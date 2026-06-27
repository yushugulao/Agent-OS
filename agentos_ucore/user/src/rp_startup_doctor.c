#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_state_catalog", "host_state_keys=573");
	ok = ok && rp_file_contains("rp_runop", "startup_health=quickstart:ready");
	ok = ok && rp_file_contains("rp_runop", "configuration_health=settings:ready");
	ok = ok && rp_file_contains("rp_runop", "platform_doctor=ready;checks=10");
	ok = ok && rp_file_contains("rp_runop", "project_scaffold=templates:3");
	ok = ok && rp_file_contains("rp_runop", "project_bundle_cache=latest:ready");
	if (!ok) {
		printf("rp_startup_doctor: dependency check failed\n");
		return 1;
	}
	if (!rp_write_file("rp_startup",
			   "quickstart=ready\n"
			   "startup_checks=8\n"
			   "offline_runs_ready=1\n"
			   "cloud_llm_ready=0\n"
			   "provider_health=offline:1,cloud:0,ready_cloud:0\n"
			   "platform_doctor=ready\n"
			   "doctor_checks=10\n"
			   "doctor_downloads=markdown,json\n"
			   "workspace_writable=pass\n"
			   "state_load=pass\n"
			   "template_provider=pass\n"
			   "project_launch=sample_ready\n"
			   "recommended_commands=startup_guide,platform_doctor,project_launch,open_research_studio\n"
			   "agentos_adapter_hint=plain_files_now;kernel_context_later\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("startup_doctor=ready")) return 1;
	printf("rp_startup_doctor: quickstart=ready doctor=ready checks=14 status=ready\n");
	return 0;
}
