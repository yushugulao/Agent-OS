#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
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
	if (rp_host_seed_has("kind=literature_search")) {
		char value[96];
		if (!rp_append_file("rp_lit", "host_action_literature_search=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "query=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agent workflow provenance");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_literature_query=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "provider=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "local");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_literature_provider=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "max_results=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "5");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_literature_max_results=", value)) return 1;
	}
	if (rp_host_seed_has("kind=evidence_review")) {
		char value[96];
		if (!rp_append_file("rp_lit", "host_action_evidence_review=screened")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "search_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "usable-literature-search:RUN-E2E:1");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_evidence_search=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "include_terms=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "workflow provenance agent");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_evidence_include=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "included=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "3");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_evidence_included=", value)) return 1;
	}
	if (rp_host_seed_has("kind=evidence_protocol")) {
		char value[96];
		if (!rp_append_file("rp_lit", "host_action_evidence_protocol=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Agent workflow evidence protocol");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_protocol_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "research_question=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Which mechanisms improve traceability?");
		}
		if (!rp_append_host_action_line("rp_lit", "host_action_protocol_question=", value)) return 1;
	}
	if (!rp_append_file("rp_ack", "ack=retriever;msg=1;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=retriever.search_literature")) return 1;
	if (!rp_append_status("retriever=ready")) return 1;
	printf("rp_retriever: literature=3 evidence_links=5 status=ready\n");
	return 0;
}
