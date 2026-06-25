#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_plan", "status=planned")) return 1;
	if (!rp_write_file("rp_lit",
			   "papers=3\nevidence_links=5\nclaim=kernel_support_improves_observability\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_append_status("retriever=ready")) return 1;
	printf("rp_retriever: literature=3 evidence_links=5 status=ready\n");
	return 0;
}
