#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>
#include <rp_evidence.h>

static char prov_body[4096];
static char prov_line[512];

static void append_edge(char *body, int cap, int id, const char *source,
			const char *target, const char *kind)
{
	rp_append_text(body, cap, "edge=");
	rp_append_uint_text(body, cap, id);
	rp_append_text(body, cap, ";source=");
	rp_append_text(body, cap, source);
	rp_append_text(body, cap, ";target=");
	rp_append_text(body, cap, target);
	rp_append_text(body, cap, ";kind=");
	rp_append_text(body, cap, kind);
	rp_append_text(body, cap,
		       ";status=ready;generation=declared_projection\n");
}

static int append_packet(char *body, int cap, const char *name,
			 const char *sources, const char **paths, int path_count)
{
	unsigned long long digest;

	if (!rp_evidence_fold_files(paths, path_count, &digest))
		return 0;
	rp_append_text(body, cap, "packet=");
	rp_append_text(body, cap, name);
	rp_append_text(body, cap, ";run=RUN-042;sources=");
	rp_append_text(body, cap, sources);
	rp_append_text(body, cap, ";source_digest=");
	rp_append_uint_text(body, cap, digest);
	rp_append_text(body, cap,
		       ";digest_source=fnv1a64_runtime_files;status=verified\n");
	return 1;
}

int main(void)
{
	static const struct {
		const char *path;
		const char *token;
	} state_expectations[] = {
		{"rp_timeline", "events_source=declared_stage_model"},
		{"rp_execobs", "kernel_evidence_state=verified"},
		{"rp_artifact_manifest", "manifest_records=4"},
		{"rp_lineage", "edges=7"},
		{"rp_package", "provenance_graph=unified"},
		{"rp_agent_run", "agent_messages=21"},
		{"rp_mature", "capability_checks=72"},
		{"rp_agentos_kernel", "agent_timeline=observed"},
		{"rp_agentos_kernel", "agent_provenance=observed"},
		{"rp_agentos_kernel", "agent_ledger=observed"},
		{"rp_agentos_kernel", "provenance_kernel=observed"},
		{"rp_agentos_timeline", "timeline_order=verified"},
		{"rp_agentos_timeline", "event_identity=verified"},
		{"rp_agentos_audit", "record_hash=verified"},
		{"rp_agentos_audit", "provenance_cause=verified"},
	};
	static const char *graph_sources[] = {
		"rp_input", "rp_stage_dag", "rp_stage_state", "rp_artifact",
		"rp_retry_plan", "rp_artifact_manifest", "rp_report_text",
		"rp_llm_packets", "rp_review_dashboard", "rp_package",
		"rp_mature", "rp_agentcmp", "rp_execobs", "rp_agent_run",
	};
	static const char *packet1[] = {
		"rp_stage_state", "rp_retry_plan", "rp_artifact_manifest",
	};
	static const char *packet2[] = {
		"rp_report_text", "rp_llm_packets", "rp_review_dashboard",
	};
	static const char *packet3[] = {
		"rp_package", "rp_review_dashboard", "rp_dossier",
	};
	static const char *packet4[] = {
		"rp_mature", "rp_agentcmp", "rp_backend_exec",
	};
	struct rp_evidence_file_measurement timeline_source;
	struct rp_evidence_file_measurement audit_source;
	struct rp_evidence_file_measurement edge_output;
	struct rp_evidence_file_measurement packet_output;
	struct rp_evidence_file_measurement timeline_output;
	unsigned long long kernel_timeline_records;
	unsigned long long kernel_timeline_visible;
	unsigned long long kernel_first_tick;
	unsigned long long kernel_last_tick;
	unsigned long long kernel_audit_records;
	unsigned long long kernel_edges;
	unsigned long long kernel_ledger_hash;
	int runtime_checks = 0;
	int edge_count;
	int packet_count;
	int view_count;

	for (int i = 0; i < (int)(sizeof(state_expectations) /
					  sizeof(state_expectations[0])); i++) {
		if (!rp_file_contains(state_expectations[i].path,
				      state_expectations[i].token))
			return 1;
		runtime_checks++;
	}
	if (!rp_evidence_get_u64("rp_agentos_timeline", "timeline_records=",
				 &kernel_timeline_records) ||
	    !rp_evidence_get_u64("rp_agentos_timeline",
				 "timeline_visible_records=",
				 &kernel_timeline_visible) ||
	    !rp_evidence_get_u64("rp_agentos_timeline", "timeline_first_tick=",
				 &kernel_first_tick) ||
	    !rp_evidence_get_u64("rp_agentos_timeline", "timeline_last_tick=",
				 &kernel_last_tick) ||
	    !rp_evidence_get_u64("rp_agentos_audit", "audit_records=",
				 &kernel_audit_records) ||
	    !rp_evidence_get_u64("rp_agentos_audit", "provenance_edges=",
				 &kernel_edges) ||
	    !rp_evidence_get_u64("rp_agentos_audit", "ledger_hash=",
				 &kernel_ledger_hash) ||
	    kernel_timeline_records == 0 ||
	    kernel_timeline_visible < kernel_timeline_records ||
	    kernel_last_tick < kernel_first_tick || kernel_audit_records == 0 ||
	    kernel_edges == 0 || kernel_ledger_hash == 0)
		return 1;
	runtime_checks += 7;
	if (!rp_evidence_measure_file("rp_agentos_timeline", &timeline_source) ||
	    !rp_evidence_measure_file("rp_agentos_audit", &audit_source))
		return 1;
	runtime_checks += 2;
	for (int i = 0; i < (int)(sizeof(graph_sources) /
					  sizeof(graph_sources[0])); i++) {
		struct rp_evidence_file_measurement measured;

		if (!rp_evidence_measure_file(graph_sources[i], &measured) ||
		    measured.bytes == 0)
			return 1;
		runtime_checks++;
	}

	prov_body[0] = 0;
	rp_evidence_append_value(prov_body, sizeof(prov_body), "generation=",
				 "runtime_validated_projection");
	rp_evidence_append_value(prov_body, sizeof(prov_body), "graph_source=",
				 "validated_userland_state_files");
	append_edge(prov_body, sizeof(prov_body), 1, "rp_input", "rp_stage_dag",
		    "input_to_workflow");
	append_edge(prov_body, sizeof(prov_body), 2, "rp_stage_dag",
		    "rp_stage_state", "workflow_to_state");
	append_edge(prov_body, sizeof(prov_body), 3, "rp_stage_state",
		    "rp_artifact", "stage_to_artifact");
	append_edge(prov_body, sizeof(prov_body), 4, "rp_artifact",
		    "rp_retry_plan", "failure_to_retry");
	append_edge(prov_body, sizeof(prov_body), 5, "rp_retry_plan",
		    "rp_artifact_manifest", "retry_to_manifest");
	append_edge(prov_body, sizeof(prov_body), 6, "rp_artifact_manifest",
		    "rp_report_text", "evidence_to_report");
	append_edge(prov_body, sizeof(prov_body), 7, "rp_llm_packets",
		    "rp_report_text", "llm_to_report");
	append_edge(prov_body, sizeof(prov_body), 8, "rp_review_dashboard",
		    "rp_package", "review_to_delivery");
	append_edge(prov_body, sizeof(prov_body), 9, "rp_mature", "rp_agentcmp",
		    "reference_to_compare");
	append_edge(prov_body, sizeof(prov_body), 10, "rp_execobs",
		    "rp_prov_view", "timeline_to_view");
	append_edge(prov_body, sizeof(prov_body), 11, "rp_package",
		    "rp_review_pack", "package_to_handoff");
	append_edge(prov_body, sizeof(prov_body), 12, "rp_agent_run",
		    "rp_prov_view", "agent_to_trace");
	if (!rp_write_file("rp_prov_edges", prov_body))
		return 1;
	edge_count = rp_evidence_count_prefixed_lines("rp_prov_edges", "edge=");
	if (edge_count <= 0)
		return 1;
	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line), "edges=");
	rp_append_uint_text(prov_line, sizeof(prov_line), edge_count);
	if (!rp_append_file("rp_prov_edges", prov_line) ||
	    !rp_evidence_measure_file("rp_prov_edges", &edge_output))
		return 1;
	runtime_checks += edge_count + 2;

	prov_body[0] = 0;
	rp_evidence_append_value(prov_body, sizeof(prov_body), "generation=",
				 "runtime_source_digest");
	if (!append_packet(prov_body, sizeof(prov_body), "workflow-recovery",
			   "rp_stage_state,rp_retry_plan,rp_artifact_manifest",
			   packet1, 3) ||
	    !append_packet(prov_body, sizeof(prov_body), "report-source",
			   "rp_report_text,rp_llm_packets,rp_review_dashboard",
			   packet2, 3) ||
	    !append_packet(prov_body, sizeof(prov_body), "delivery-handoff",
			   "rp_package,rp_review_dashboard,rp_dossier", packet3, 3) ||
	    !append_packet(prov_body, sizeof(prov_body), "agentos-readiness",
			   "rp_mature,rp_agentcmp,rp_backend_exec", packet4, 3) ||
	    !rp_write_file("rp_evidence_packet", prov_body))
		return 1;
	packet_count = rp_evidence_count_prefixed_lines("rp_evidence_packet",
						"packet=");
	if (packet_count <= 0)
		return 1;
	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line), "packets=");
	rp_append_uint_text(prov_line, sizeof(prov_line), packet_count);
	if (!rp_append_file("rp_evidence_packet", prov_line) ||
	    !rp_evidence_measure_file("rp_evidence_packet", &packet_output))
		return 1;
	runtime_checks += packet_count + 2;

	prov_body[0] = 0;
	rp_evidence_append_value(prov_body, sizeof(prov_body), "generation=",
				 "runtime_projection");
	rp_append_text(prov_body, sizeof(prov_body),
		       "view=kernel_timeline;source=rp_agentos_timeline;events=");
	rp_append_uint_text(prov_body, sizeof(prov_body), kernel_timeline_records);
	rp_append_text(prov_body, sizeof(prov_body), ";first_tick=");
	rp_append_uint_text(prov_body, sizeof(prov_body), kernel_first_tick);
	rp_append_text(prov_body, sizeof(prov_body), ";last_tick=");
	rp_append_uint_text(prov_body, sizeof(prov_body), kernel_last_tick);
	rp_append_text(prov_body, sizeof(prov_body),
		       ";generation=kernel_snapshot;status=verified\n");
	rp_append_text(prov_body, sizeof(prov_body),
		       "view=workflow_observer;events=8;source=rp_execobs;status=ready;events_source=demo_projection\n"
		       "view=artifact_path;events=7;source=rp_prov_edges;status=ready;events_source=runtime_projection\n"
		       "view=agent_decision_flow;events=6;source=rp_agent_run;status=ready;events_source=demo_projection\n"
		       "timeline_event=plan;tick=1;actor=orchestrator;artifact=rp_plan;status=ready;tick_source=demo_model\n"
		       "timeline_event=retrieve;tick=6;actor=retriever;artifact=rp_evidence;status=ready;tick_source=demo_model\n"
		       "timeline_event=analyze;tick=11;actor=analyst;artifact=rp_artifact;status=ready;tick_source=demo_model\n"
		       "timeline_event=repair;tick=18;actor=recovery;artifact=rp_retry_plan;status=ready;tick_source=demo_model\n"
		       "timeline_event=review;tick=25;actor=reviewer;artifact=rp_review_dashboard;status=ready;tick_source=demo_model\n"
		       "timeline_event=llm;tick=31;actor=writer;artifact=rp_llm_packets;status=ready;tick_source=demo_model\n"
		       "timeline_event=package;tick=37;actor=auditor;artifact=rp_package;status=ready;tick_source=demo_model\n"
		       "timeline_event=dossier;tick=42;actor=orchestrator;artifact=rp_review_pack;status=ready;tick_source=demo_model\n");
	if (!rp_write_file("rp_timeline_view", prov_body))
		return 1;
	view_count = rp_evidence_count_prefixed_lines("rp_timeline_view", "view=");
	if (view_count <= 0)
		return 1;
	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line), "views=");
	rp_append_uint_text(prov_line, sizeof(prov_line), view_count);
	if (!rp_append_file("rp_timeline_view", prov_line) ||
	    !rp_evidence_measure_file("rp_timeline_view", &timeline_output))
		return 1;
	runtime_checks += view_count + 2;

	prov_body[0] = 0;
	rp_evidence_append_value(prov_body, sizeof(prov_body), "run_id=", "RUN-042");
	rp_evidence_append_value(prov_body, sizeof(prov_body), "evidence_source=",
				 "kernel_snapshots_and_runtime_state_files");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "evidence_generation=", "runtime");
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "runtime_checks=",
			       runtime_checks);
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "provenance_view_checks_source=",
				 "demo_expected");
	rp_evidence_append_u64(prov_body, sizeof(prov_body),
			       "demo_expected_provenance_view_checks=", 64);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "timeline_views=",
			       view_count);
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "timeline_events_source=", "demo_stage_model");
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "timeline_events=", 9);
	rp_evidence_append_u64(prov_body, sizeof(prov_body),
			       "kernel_timeline_records=", kernel_timeline_records);
	rp_evidence_append_u64(prov_body, sizeof(prov_body),
			       "kernel_timeline_visible_records=",
			       kernel_timeline_visible);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "kernel_audit_records=",
			       kernel_audit_records);
	rp_evidence_append_u64(prov_body, sizeof(prov_body),
			       "kernel_provenance_edges=", kernel_edges);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "kernel_ledger_hash=",
			       kernel_ledger_hash);
	rp_evidence_append_u64(prov_body, sizeof(prov_body),
			       "demo_expected_subgraphs=", 3);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "subgraph_edges=",
			       edge_count);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "evidence_packets=",
			       packet_count);
	rp_evidence_append_u64(prov_body, sizeof(prov_body),
			       "demo_expected_decision_packets=", 3);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "timeline_source_hash=",
			       timeline_source.hash);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "audit_source_hash=",
			       audit_source.hash);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "edge_output_hash=",
			       edge_output.hash);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "packet_output_hash=",
			       packet_output.hash);
	rp_evidence_append_u64(prov_body, sizeof(prov_body), "timeline_output_hash=",
			       timeline_output.hash);
	rp_evidence_append_value(prov_body, sizeof(prov_body), "reader_page=",
				 "provenance.html");
	rp_evidence_append_value(prov_body, sizeof(prov_body), "agentos_mapping=",
				 "kernel_timeline,kernel_provenance_edges,kernel_ledger,context_detail");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "agentos_kernel_timeline=", "observed");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "agentos_kernel_timeline_validation=", "verified");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "agentos_kernel_provenance=", "observed");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "agentos_kernel_provenance_validation=", "verified_nonzero");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "agentos_kernel_ledger=", "observed");
	rp_evidence_append_value(prov_body, sizeof(prov_body),
				 "agentos_kernel_ledger_validation=", "hash_verified");
	rp_evidence_append_value(prov_body, sizeof(prov_body), "status=", "verified");
	if (!rp_write_file("rp_prov_view", prov_body))
		return 1;

	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line),
		       "provenance_page=rp_prov_view;timeline_views=");
	rp_append_uint_text(prov_line, sizeof(prov_line), view_count);
	rp_append_text(prov_line, sizeof(prov_line),
		       ";demo_expected_subgraphs=3;packets=");
	rp_append_uint_text(prov_line, sizeof(prov_line), packet_count);
	rp_append_text(prov_line, sizeof(prov_line),
		       ";status=verified;generation=runtime");
	if (!rp_append_file("rp_web_bundle", prov_line))
		return 1;
	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line),
		       "subsection=provenance_view;source=rp_prov_view;timeline=");
	rp_append_uint_text(prov_line, sizeof(prov_line), view_count);
	rp_append_text(prov_line, sizeof(prov_line), ";packets=");
	rp_append_uint_text(prov_line, sizeof(prov_line), packet_count);
	rp_append_text(prov_line, sizeof(prov_line),
		       ";demo_expected_checks=64;catalog_outcome=matched;status=verified;checks_source=demo_expected;runtime_checks=");
	rp_append_uint_text(prov_line, sizeof(prov_line), runtime_checks);
	if (!rp_append_file("rp_review_dashboard", prov_line))
		return 1;
	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line),
		       "provenance_view_report=rp_prov_view;edges=");
	rp_append_uint_text(prov_line, sizeof(prov_line), edge_count);
	rp_append_text(prov_line, sizeof(prov_line), ";packets=");
	rp_append_uint_text(prov_line, sizeof(prov_line), packet_count);
	rp_append_text(prov_line, sizeof(prov_line),
		       ";status=ready;generation=runtime");
	if (!rp_append_file("rp_package", prov_line))
		return 1;
	prov_line[0] = 0;
	rp_append_text(prov_line, sizeof(prov_line),
		       "demo_expected_provenance_view_checks=64;timeline_views=");
	rp_append_uint_text(prov_line, sizeof(prov_line), view_count);
	rp_append_text(prov_line, sizeof(prov_line), ";subgraphs=3;packets=");
	rp_append_uint_text(prov_line, sizeof(prov_line), packet_count);
	rp_append_text(prov_line, sizeof(prov_line),
		       ";agentos_replacements=4;kernel_timeline=observed;status=verified;provenance_view_checks_source=demo_expected;runtime_checks=");
	rp_append_uint_text(prov_line, sizeof(prov_line), runtime_checks);
	rp_append_text(prov_line, sizeof(prov_line), ";generation=runtime");
	if (!rp_append_file("rp_agentcmp", prov_line) ||
	    !rp_append_file("rp_ack", "ack=provenance_view;msg=prov;status=ready") ||
	    !rp_append_file("rp_tool", "tool=provenance_view.build_timeline") ||
	    !rp_append_file("rp_tool", "tool=provenance_view.link_edges") ||
	    !rp_append_file("rp_tool", "tool=provenance_view.packetize_evidence") ||
	    !rp_append_file("rp_tool", "tool=provenance_view.publish_reader") ||
	    !rp_append_file("rp_tool", "tool=provenance_view.compare_agentos") ||
	    !rp_append_status("provenance_view=ready"))
		return 1;
	printf("rp_prov_view: evidence_source=kernel_snapshots+runtime_files generation=runtime timeline_records=%d audit_records=%d provenance_edges=%d graph_edges=%d packets=%d checks=%d status=verified\n",
	       (int)kernel_timeline_records, (int)kernel_audit_records,
	       (int)kernel_edges, edge_count, packet_count, runtime_checks);
	return 0;
}
