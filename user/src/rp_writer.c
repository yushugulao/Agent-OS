#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_review", "status=accepted")) return 1;
	if (!rp_write_file("rp_report",
			   "sections=6\ncitations=9\nresponse_items=3\nreport=RUN-042-recovery\nstatus=packaged\n")) {
		return 1;
	}
	if (!rp_append_status("writer=packaged")) return 1;
	printf("rp_writer: sections=6 citations=9 response_items=3 status=packaged\n");
	return 0;
}
