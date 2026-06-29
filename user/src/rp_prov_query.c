#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_prov_view", "provenance_view_checks=64");
	ok = ok && rp_file_contains("rp_prov_edges", "edges=12");
	ok = ok && rp_file_contains("rp_evidence_packet", "packets=4");
	ok = ok && rp_file_contains("rp_timeline_view", "views=4");
	ok = ok && rp_file_contains("rp_mature", "mapping=aiida-process-graph");
	ok = ok && rp_file_contains("rp_package", "provenance_graph=unified");
	ok = ok && rp_file_contains("rp_calculation", "job=calculation-job:lab-gene-x:run042-qc");
	ok = ok && rp_file_contains("rp_calc_parse", "parser_result=calculation-parser-result:run042-qc");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_timeline=observed");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_provenance=observed");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_ledger=observed");
	if (!ok) return 1;

	if (!rp_write_file("rp_prov_query",
			   "run_id=RUN-042\n"
			   "provenance_query_checks=72\n"
			   "specs=3\n"
			   "templates=1\n"
			   "executions=3\n"
			   "comparisons=1\n"
			   "exports=1\n"
			   "evidence_packets=1\n"
			   "projected_rows=14\n"
			   "reader_page=provenance-queries.html\n"
			   "package=provenance-query-execution:calculation-lineage\n"
			   "agentos_mapping=timeline_query,provenance_snapshot,ledger_snapshot,context_detail\n"
			   "agentos_kernel_timeline=observed\n"
			   "agentos_kernel_provenance=observed\n"
			   "agentos_kernel_ledger=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_prov_specs",
			   "specs=3\n"
			   "template=provenance-query-template:calculation-root-neighborhood;owner=auditor;direction=both;depth=2;params=root_id,query_name;status=ready\n"
			   "spec=provenance-query:RUN-042:calculation-lineage;owner=auditor;root=calculation-job:lab-gene-x:run042-qc;direction=both;depth=2;projection=node_id,node_type,title,status;status=ready\n"
			   "spec=provenance-query:RUN-042:finished-calculations;owner=auditor;node_type=calculation_job;node_status=finished;direction=both;depth=1;projection=node_id,node_type,title,status;status=ready\n"
			   "spec=provenance-query:RUN-042:template-rendered-lineage;owner=auditor;root=calculation-job:lab-gene-x:run042-qc;template=calculation-root-neighborhood;direction=both;depth=2;status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_prov_exec",
			   "executions=3\n"
			   "execution=provenance-query-execution:calculation-lineage;query=provenance-query:RUN-042:calculation-lineage;nodes=8;links=7;rows=8;status=ok\n"
			   "execution=provenance-query-execution:finished-calculations;query=provenance-query:RUN-042:finished-calculations;nodes=2;links=3;rows=2;status=ok\n"
			   "execution=provenance-query-execution:template-rendered-lineage;query=provenance-query:RUN-042:template-rendered-lineage;nodes=8;links=7;rows=8;status=ok\n"
			   "row=calculation-job:lab-gene-x:run042-qc;node_type=calculation_job;title=RUN-042 QC calculation;status=finished\n"
			   "row=artifact:RUN-042:alignment-table;node_type=artifact;title=alignment table;status=ready\n"
			   "row=report-section:RUN-042:methods;node_type=report_section;title=methods;status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_prov_query_pkg",
			   "comparisons=1\n"
			   "comparison=provenance-query-comparison:RUN-042:rendered-vs-direct;base=provenance-query-execution:calculation-lineage;candidate=provenance-query-execution:template-rendered-lineage;added=0;removed=0;row_delta=0;status=ok\n"
			   "export=provenance-query-export:RUN-042:calculation-lineage;execution=provenance-query-execution:calculation-lineage;type=markdown;checksum=provquery042;status=ready\n"
			   "packet=provenance-query-packet:RUN-042:lineage-review;comparison=provenance-query-comparison:RUN-042:rendered-vs-direct;executions=2;nodes=8;links=7;checksum=packet042;status=ready\n"
			   "package_entry=provenance_query;specs=3;executions=3;comparisons=1;exports=1;packets=1;status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_web_bundle", "provenance_queries_page=rp_prov_query;specs=3;executions=3;packets=1;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=provenance_queries;source=rp_prov_query;queries=3;executions=3;checks=72;outcome=passed;status=ready")) return 1;
	if (!rp_append_file("rp_package", "provenance_query_package=rp_prov_query;specs=3;executions=3;packets=1;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "provenance_query_checks=72;specs=3;templates=1;executions=3;comparisons=1;exports=1;packets=1;agentos_replacements=4;kernel_timeline=observed;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=provenance_query;msg=provq;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_query.seed")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_query.instantiate_template")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_query.execute")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_query.compare")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_query.export")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_query.packetize")) return 1;
	if (!rp_append_status("provenance_query=ready")) return 1;
	printf("rp_prov_query: specs=3 templates=1 executions=3 comparisons=1 packets=1 checks=72 errors=0 status=ready\n");
	return 0;
}
