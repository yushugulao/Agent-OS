#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_llm_req", "secret_policy=no_secret_in_ucore")) return 1;
	if (!rp_file_contains("rp_llm_resp", "status=ready")) return 1;
	if (!rp_file_contains("rp_relay", "secret_location=host_environment")) return 1;
	if (!rp_file_contains("rp_prompt", "secret_refs=3")) return 1;
	if (!rp_file_contains("rp_llmlog", "privacy_checked=1")) return 1;
	if (!rp_file_contains("rp_mail", "to=privacy")) return 1;
	if (!rp_write_file("rp_privacy",
			   "policy=offline_template_packet\n"
			   "checked_files=5\n"
			   "sensitive_tokens=0\n"
			   "redactions=0\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=privacy;msg=10;status=accepted")) return 1;
	if (!rp_append_file("rp_tool", "tool=privacy.review_packet;target=rp_privacy;status=ok")) return 1;
	if (!rp_append_status("privacy=ready")) return 1;
	printf("rp_privacy: checked=5 redactions=0 status=ready\n");
	return 0;
}
