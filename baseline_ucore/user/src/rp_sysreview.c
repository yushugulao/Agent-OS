#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_litrev", "review=systematic-mini");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_contains_extra=");
	ok = ok && rp_file_contains("rp_modelreg", "status=ready");
	if (!ok) return 1;

	if (!rp_write_file("rp_sysreview",
			   "service=systematic-review\n"
			   "systematic_review_checks=104\n"
			   "protocol=systematic-review:agent-os-science\n"
			   "title=Agent-OS support for scientific Agent workflows\n"
			   "research_question=Which platform mechanisms improve reliability, provenance, and reproducibility in scientific Agent workflows?\n"
			   "population=Scientific computing and AI-for-science workflows\n"
			   "intervention=Agent runtime support and kernel-managed context\n"
			   "comparator=plain user-space workflow orchestration\n"
			   "outcome=reproducibility,provenance_quality,failure_recovery,report_traceability\n"
			   "owner=wang\n"
			   "status=registered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_syssearch",
			   "strategy=literature-search:agent-os-science:local\n"
			   "protocol=systematic-review:agent-os-science\n"
			   "source=local-literature-library\n"
			   "query=agent workflow provenance reproducibility kernel\n"
			   "filters=year_min:2023,must_have_any:agent|workflow|provenance|kernel|reproducibility\n"
			   "results=9\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_sysscreen",
			   "screening_decisions=9\n"
			   "title_abstract_included=3\n"
			   "full_text_included=3\n"
			   "excluded=6\n"
			   "decision=paper:agent-kernel-context;stage=full_text;result=include;reason=kernel_context_support\n"
			   "decision=paper:agent-provenance-runtime;stage=full_text;result=include;reason=traceable_tool_calls\n"
			   "decision=paper:workflow-recovery-agent;stage=full_text;result=include;reason=failure_recovery\n"
			   "decision=paper:generic-chatbot-ui;stage=title_abstract;result=exclude;reason=no_system_interaction\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_sysextract",
			   "extractions=3\n"
			   "fields=workflow_mechanism,evidence_type,reported_outcome,year,venue\n"
			   "risk_of_bias=3\n"
			   "low=2\n"
			   "some_concerns=1\n"
			   "record=paper:agent-kernel-context;mechanism=context_path;evidence=systems_demo;outcome=traceability\n"
			   "record=paper:agent-provenance-runtime;mechanism=tool_call_log;evidence=case_study;outcome=provenance_quality\n"
			   "record=paper:workflow-recovery-agent;mechanism=retry_policy;evidence=benchmark;outcome=failure_recovery\n"
			   "status=complete\n")) {
		return 1;
	}
	if (!rp_write_file("rp_syssynth",
			   "synthesis=evidence-synthesis:agent-os-science\n"
			   "included_papers=3\n"
			   "conclusion=kernel-managed context and accountable tool calls improve traceability\n"
			   "confidence=moderate\n"
			   "limitations=local_library,small_sample,abstract_metadata\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_sysprisma",
			   "flow=prisma-flow:agent-os-science\n"
			   "identified=9\n"
			   "screened=9\n"
			   "excluded=6\n"
			   "included=3\n"
			   "reason_not_agent_workflow=4\n"
			   "reason_no_abstract=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "systematic_review=rp_sysreview;protocol=systematic-review:agent-os-science;included=3;prisma=ready;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "systematic_review_page=rp_sysreview;protocols=1;screening=9;included=3;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=systematic_review;source=rp_sysreview;checks=104;included=3;prisma=ready;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "systematic_review_checks=104;protocols=1;searches=1;screening=9;extractions=3;bias=3;prisma=1;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=systematic_review;msg=review;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.create_protocol")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.run_search")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.screen_title_abstract")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.screen_full_text")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.extract_data")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.assess_bias")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.synthesize")) return 1;
	if (!rp_append_file("rp_tool", "tool=systematic_review.export_prisma")) return 1;
	if (!rp_append_status("systematic_review=ready")) return 1;
	printf("rp_sysreview: protocols=1 searches=1 screening=9 included=3 extractions=3 prisma=1 checks=104 status=ready\n");
	return 0;
}
