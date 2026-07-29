#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_execobs", "execution_packets=4");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_lineage", "edges=7");
	ok = ok && rp_file_contains("rp_package", "provenance_graph=unified");
	ok = ok && rp_file_contains("rp_agent_run", "agent_messages=21");
	ok = ok && rp_file_contains("rp_mature", "capability_checks=72");
	if (!ok) return 1;

	if (!rp_write_file("rp_prov_view",
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "evidence_file_status=reference_ready\n"
			   "run_id=RUN-042\n"
			   "provenance_view_checks=64\n"
			   "timeline_views=4\n"
			   "timeline_events=9\n"
			   "subgraphs=3\n"
			   "subgraph_edges=12\n"
			   "evidence_packets=4\n"
			   "decision_packets=3\n"
			   "reader_page=provenance.html\n"
			   "agentos_mapping=kernel_timeline,kernel_provenance_edges,kernel_ledger,context_detail\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_prov_edges",
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "evidence_file_status=reference_ready\n"
			   "edges=12\n"
			   "edge=1;source=rp_input;target=rp_stage_dag;kind=input_to_workflow;status=ready\n"
			   "edge=2;source=rp_stage_dag;target=rp_stage_state;kind=workflow_to_state;status=ready\n"
			   "edge=3;source=rp_stage_state;target=rp_artifact;kind=stage_to_artifact;stage=align;status=ready\n"
			   "edge=4;source=rp_artifact;target=rp_retry_plan;kind=failure_to_retry;status=ready\n"
			   "edge=5;source=rp_retry_plan;target=rp_artifact_manifest;kind=retry_to_manifest;status=ready\n"
			   "edge=6;source=rp_artifact_manifest;target=rp_report_text;kind=evidence_to_report;status=ready\n"
			   "edge=7;source=rp_llm_packets;target=rp_report_text;kind=llm_to_report;status=ready\n"
			   "edge=8;source=rp_review_dashboard;target=rp_package;kind=review_to_delivery;status=ready\n"
			   "edge=9;source=rp_mature;target=rp_agentcmp;kind=reference_to_compare;status=ready\n"
			   "edge=10;source=rp_execobs;target=rp_prov_view;kind=timeline_to_view;status=ready\n"
			   "edge=11;source=rp_package;target=rp_review_pack;kind=package_to_handoff;status=ready\n"
			   "edge=12;source=rp_agent_run;target=rp_prov_view;kind=agent_to_trace;status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_evidence_packet",
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "evidence_file_status=reference_ready\n"
			   "packets=4\n"
			   "packet=workflow-recovery;run=RUN-042;sources=rp_stage_state,rp_retry_plan,rp_artifact_manifest;checks=16;status=ready\n"
			   "packet=report-source;run=RUN-042;sources=rp_report_text,rp_llm_packets,rp_review_dashboard;checks=16;status=ready\n"
			   "packet=delivery-handoff;run=RUN-042;sources=rp_package,rp_review_pack,rp_dossier;checks=16;status=ready\n"
			   "packet=agentos-readiness;run=RUN-042;sources=rp_mature,rp_agentcmp,rp_backend_exec;checks=16;status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_timeline_view",
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "evidence_file_status=reference_ready\n"
			   "views=4\n"
			   "view=run_timeline;events=9;source=rp_timeline;status=ready\n"
			   "view=workflow_observer;events=8;source=rp_execobs;status=ready\n"
			   "view=artifact_path;events=7;source=rp_prov_edges;status=ready\n"
			   "view=agent_decision_flow;events=6;source=rp_agent_run;status=ready\n"
			   "timeline_event=plan;tick=1;actor=orchestrator;artifact=rp_plan;status=ready\n"
			   "timeline_event=retrieve;tick=6;actor=retriever;artifact=rp_evidence;status=ready\n"
			   "timeline_event=analyze;tick=11;actor=analyst;artifact=rp_artifact;status=ready\n"
			   "timeline_event=repair;tick=18;actor=recovery;artifact=rp_retry_plan;status=ready\n"
			   "timeline_event=review;tick=25;actor=reviewer;artifact=rp_review_dashboard;status=ready\n"
			   "timeline_event=llm;tick=31;actor=writer;artifact=rp_llm_packets;status=ready\n"
			   "timeline_event=package;tick=37;actor=auditor;artifact=rp_package;status=ready\n"
			   "timeline_event=dossier;tick=42;actor=orchestrator;artifact=rp_review_pack;status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_web_bundle", "evidence_role=demo_reference;catalog_generation=demo_expected;provenance_page=rp_prov_view;timeline_views=4;subgraphs=3;packets=4;status=reference_ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "evidence_role=demo_reference;catalog_generation=demo_expected;subsection=provenance_view;source=rp_prov_view;timeline=4;packets=4;checks=64;outcome=passed;status=reference_ready")) return 1;
	if (!rp_append_file("rp_package", "evidence_role=demo_reference;catalog_generation=demo_expected;provenance_view_report=rp_prov_view;edges=12;packets=4;status=reference_ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "evidence_role=demo_reference;catalog_generation=demo_expected;provenance_view_checks=64;timeline_views=4;subgraphs=3;packets=4;agentos_replacements=4;status=reference_ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=provenance_view;msg=prov;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_view.build_timeline")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_view.link_edges")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_view.packetize_evidence")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_view.publish_reader")) return 1;
	if (!rp_append_file("rp_tool", "tool=provenance_view.compare_agentos")) return 1;
	if (!rp_append_status("provenance_view=ready")) return 1;
	printf("rp_prov_view: evidence_role=demo_reference catalog_generation=demo_expected timelines=4 subgraphs=3 packets=4 checks=64 errors=0 status=reference_ready\n");
	return 0;
}
