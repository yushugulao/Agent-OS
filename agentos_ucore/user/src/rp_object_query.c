#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int lines = rp_count_lines("rp_object_records");
	int ready = rp_count_token("rp_object_records", "ready");
	if (lines != 8 || ready < 7) {
		printf("rp_object_query: bad_counts lines=%d ready=%d\n", lines, ready);
		return 1;
	}
	if (!rp_file_contains("rp_object_records", "workflow:RUN-042")) return 1;
	if (!rp_file_contains("rp_object_records", "package:RUN-042")) return 1;
	if (!rp_write_file("rp_object_query",
			   "query=RUN-042,ready\nhits=8\nready_hits=7\nworkflow_hits=1\npackage_hits=1\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_append_status("object_query=ready")) return 1;
	printf("rp_object_query: hits=8 ready_hits=7 status=ready\n");
	return 0;
}
