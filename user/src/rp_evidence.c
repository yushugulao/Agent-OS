#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_lit", "evidence_links=5");
	ok = ok && rp_file_contains("rp_review", "status=accepted");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_audit", "status=passed");
	if (!ok) return 1;
	if (!rp_write_file("rp_evidence",
			   "claims=8\nevidence_links=5\nprovenance_nodes=12\nrun=RUN-042\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_knowledge",
			   "knowledge_docs=4\n"
			   "text_chunks=12\n"
			   "semantic_entities=9\n"
			   "semantic_relations=6\n"
			   "systematic_records=5\n"
			   "synthesis=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("evidence=ready")) return 1;
	if (!rp_append_status("knowledge=ready")) return 1;
	printf("rp_evidence: claims=8 links=5 provenance=12 knowledge=4 status=ready\n");
	return 0;
}
