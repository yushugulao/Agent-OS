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
			   "synthesis=ready\n"
			   "status=ready\n")) {
		return 1;
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
