#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_plan", "status=planned")) return 1;
	if (!rp_file_contains("rp_mail", "to=retriever")) return 1;
	if (!rp_write_file("rp_lit",
			   "papers=3\nevidence_links=5\nclaim=kernel_support_improves_observability\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_lit", "literature_search=usable-literature-search:RUN-900:1;provider=local;query=agent-os-traceability;candidates=9;imported=3")) return 1;
	if (!rp_append_file("rp_lit", "candidate=lit-agentos-ctx;decision=include;reason=context_traceability")) return 1;
	if (!rp_append_file("rp_lit", "candidate=lit-workflow-repro;decision=include;reason=reproducible_workflow")) return 1;
	if (!rp_append_file("rp_lit", "screening_decisions=9;included=3;excluded=6")) return 1;
	if (!rp_append_file("rp_lit", "evidence_protocol=usable-evidence-protocol:RUN-900:1;status=registered")) return 1;
	if (!rp_append_file("rp_lit", "prisma_flow=usable-prisma-flow:RUN-900:1;identified=9;screened=9;included=3")) return 1;
	if (!rp_append_file("rp_ack", "ack=retriever;msg=1;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=retriever.search_literature;target=rp_lit;status=ok")) return 1;
	if (!rp_append_status("retriever=ready")) return 1;
	printf("rp_retriever: literature=3 evidence_links=5 status=ready\n");
	return 0;
}
