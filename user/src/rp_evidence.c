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
	ok = ok && rp_file_contains("rp_mail", "to=evidence");
	if (!ok) return 1;
	if (!rp_write_file("rp_evidence",
			   "claims=8\nevidence_links=5\nprovenance_nodes=12\nrun=RUN-042\nstatus=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_claimrec",
			   "claim=1;kind=result;source=rp_data;evidence=lit-a,calc-a;status=supported\n"
			   "claim=2;kind=method;source=rp_protocol;evidence=protocol-a;status=supported\n"
			   "claim=3;kind=recovery;source=rp_fix;evidence=retrylog-a;status=supported\n"
			   "claim=4;kind=dataset;source=rp_datadic;evidence=schema-a;status=supported\n"
			   "claim=5;kind=lab;source=rp_labops;evidence=maintenance-a;status=supported\n"
			   "claim=6;kind=privacy;source=rp_privacy;evidence=llm-policy-a;status=supported\n"
			   "claim=7;kind=release;source=rp_audit;evidence=audit-a;status=supported\n"
			   "claim=8;kind=repro;source=rp_repro;evidence=repro-a;status=supported\n")) {
		return 1;
	}
	if (!rp_write_file("rp_provpath",
			   "run=RUN-042\n"
			   "nodes=12\n"
			   "edges=11\n"
			   "claim_records=8\n"
			   "critical_paths=3\n"
			   "path1=plan>data>review>repair>audit\n"
			   "path2=plan>lit>evidence>knowledge>package\n"
			   "path3=plan>llm_queue>privacy>release>dossier\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_knowledge",
			   "knowledge_docs=4\n"
			   "text_chunks=12\n"
			   "semantic_entities=9\n"
			   "semantic_relations=6\n"
			   "systematic_records=5\n"
			   "library_sources=1\n"
			   "library_tag=reusable\n"
			   "library_source_id=usable-source:library2026:1\n"
			   "citation_key=library2026\n"
			   "library_query=reusable\n"
			   "literature_search_id=usable-literature-search:RUN-900:1\n"
			   "screening_decisions=9;included=3;excluded=6\n"
			   "evidence_extractions=3;fields=mechanism,evidence_type,reported_outcome\n"
			   "evidence_protocol=usable-evidence-protocol:RUN-900:1;status=registered\n"
			   "prisma_flow=usable-prisma-flow:RUN-900:1;identified=9;included=3\n"
			   "evidence_synthesis=usable-evidence-synthesis:RUN-900:1;themes=traceability,reproducibility,recovery\n"
			   "synthesis=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=library_source")) {
		char value[96];
		if (!rp_append_file("rp_knowledge", "host_action_library_source=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=library_source", "citation_key=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agentlibrary2026");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_library_citation=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=library_source", "tags=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agent reusable");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_library_tags=", value)) return 1;
	}
	if (rp_host_seed_has("kind=literature_search")) {
		char value[96];
		if (!rp_append_file("rp_knowledge", "host_action_literature_search=ready")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "query=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agent workflow provenance");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_literature_query=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "max_results=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "5");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_literature_max_results=", value)) return 1;
	}
	if (rp_host_seed_has("kind=evidence_review")) {
		char value[96];
		if (!rp_append_file("rp_knowledge", "host_action_evidence_review=ready")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "reviewer=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "wang");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_evidence_reviewer=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_review", "included=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "3");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_evidence_included=", value)) return 1;
	}
	if (rp_host_seed_has("kind=evidence_protocol")) {
		char value[96];
		if (!rp_append_file("rp_knowledge", "host_action_evidence_protocol=ready")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Agent workflow evidence protocol");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_protocol_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "outcome=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "traceability");
		}
		if (!rp_append_host_action_line("rp_knowledge", "host_action_protocol_outcome=", value)) return 1;
	}
	if (!rp_append_file("rp_ack", "ack=evidence;msg=8;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=evidence.build_path;target=rp_evidence;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=evidence.map_claims;target=rp_claimrec;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=evidence.trace_paths;target=rp_provpath;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=evidence.synthesize_knowledge;target=rp_knowledge;status=ok")) return 1;
	if (!rp_append_status("evidence=ready")) return 1;
	if (!rp_append_status("claimrec=ready")) return 1;
	if (!rp_append_status("provpath=ready")) return 1;
	if (!rp_append_status("knowledge=ready")) return 1;
	printf("rp_evidence: claims=8 links=5 claim_records=8 paths=3 status=ready\n");
	return 0;
}
