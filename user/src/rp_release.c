#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_package", "release=ready")) return 1;
	if (!rp_file_contains("rp_audit", "status=passed")) return 1;
	if (!rp_file_contains("rp_privacy", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_datarel", "fair=passed")) return 1;
	if (!rp_file_contains("rp_repro", "reproduction_checks=9")) return 1;
	if (!rp_file_contains("rp_mail", "to=release")) return 1;
	if (!rp_write_file("rp_release",
			   "release_id=release:RUN-042:plain-ucore\n"
			   "run_id=RUN-042\n"
			   "package=ready\n"
			   "audit=passed\n"
			   "privacy=accepted\n"
			   "fair=passed\n"
			   "repro=ready\n"
			   "llm_packet=ready\n"
			   "decision=release\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=release;msg=12;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=release.decide;target=rp_release;status=ok")) return 1;
	if (!rp_append_status("release=ready")) return 1;
	printf("rp_release: decision=release checks=5 status=ready\n");
	return 0;
}
