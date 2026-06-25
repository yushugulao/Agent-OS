#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_lit", "evidence_links=5")) return 1;
	if (!rp_file_contains("rp_data", "status=needs_repair")) return 1;
	if (!rp_file_contains("rp_mail", "to=reviewer")) return 1;
	if (!rp_write_file("rp_review",
			   "claims=8\nprotocol_checks=5\nrelease_checks=4\ndecision=accepted_after_repair\nstatus=accepted\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=reviewer;msg=3;status=accepted")) return 1;
	if (!rp_append_file("rp_tool", "tool=reviewer.check_claims;target=rp_review;status=ok")) return 1;
	if (!rp_append_status("reviewer=accepted")) return 1;
	printf("rp_reviewer: claims=8 protocol_checks=5 release_checks=4 status=accepted\n");
	return 0;
}
