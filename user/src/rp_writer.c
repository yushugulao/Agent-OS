#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_review", "status=accepted")) return 1;
	if (!rp_file_contains("rp_datadic", "schema_fields=17")) return 1;
	if (!rp_file_contains("rp_labops", "maintenance=passed")) return 1;
	if (!rp_write_file("rp_report",
			   "sections=8\n"
			   "citations=9\n"
			   "response_items=3\n"
			   "data_dictionary=attached\n"
			   "lab_operations=attached\n"
			   "report=RUN-042-recovery\n"
			   "status=packaged\n")) {
		return 1;
	}
	if (!rp_append_status("writer=packaged")) return 1;
	printf("rp_writer: sections=8 citations=9 response_items=3 status=packaged\n");
	return 0;
}
