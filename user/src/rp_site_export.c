#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_lineage", "edges=7")) return 1;
	if (!rp_write_file("rp_site",
			   "page=overview\n"
			   "page=objects\n"
			   "page=query\n"
			   "page=evidence\n"
			   "page=lineage\n"
			   "page=package\n"
			   "pages=6\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_append_status("site_export=ready")) return 1;
	printf("rp_site_export: pages=6 status=ready\n");
	return 0;
}
