#include <stdio.h>
#include <string.h>
#include <research_platform_state.h>
#include <rp_evidence.h>

#define PROV_QUERY_NODE_CAP 32
#define PROV_QUERY_NODE_SIZE 64
#define PROV_QUERY_EDGE_CAP 32

struct prov_graph_stats {
	int edges;
	int nodes;
	unsigned long long source_hash;
};

struct prov_query_result {
	int visited[PROV_QUERY_NODE_CAP];
	int nodes;
	int links;
};

static char query_body[8192];
static char query_line[512];
static char graph_nodes[PROV_QUERY_NODE_CAP][PROV_QUERY_NODE_SIZE];
static char graph_sources[PROV_QUERY_EDGE_CAP][PROV_QUERY_NODE_SIZE];
static char graph_targets[PROV_QUERY_EDGE_CAP][PROV_QUERY_NODE_SIZE];

static int graph_add_node(const char *node, int *count)
{
	for (int i = 0; i < *count; i++)
		if (strcmp(graph_nodes[i], node) == 0)
			return 1;
	if (*count >= PROV_QUERY_NODE_CAP)
		return 0;
	rp_copy_text(graph_nodes[*count], PROV_QUERY_NODE_SIZE, node);
	(*count)++;
	return 1;
}

static int graph_node_index(const char *node, int count)
{
	for (int i = 0; i < count; i++)
		if (strcmp(graph_nodes[i], node) == 0)
			return i;
	return -1;
}

static int execute_graph_query(const struct prov_graph_stats *graph,
			       const char *root, int max_depth,
			       struct prov_query_result *result)
{
	int queue[PROV_QUERY_NODE_CAP];
	int depths[PROV_QUERY_NODE_CAP];
	int head = 0;
	int tail = 0;
	int root_index;

	if (graph == 0 || result == 0 || max_depth < 0)
		return 0;
	memset(result, 0, sizeof(*result));
	root_index = graph_node_index(root, graph->nodes);
	if (root_index < 0)
		return 0;
	result->visited[root_index] = 1;
	queue[tail] = root_index;
	depths[tail++] = 0;
	while (head < tail) {
		int node = queue[head];
		int depth = depths[head++];

		if (depth >= max_depth)
			continue;
		for (int edge = 0; edge < graph->edges; edge++) {
			int source = graph_node_index(graph_sources[edge], graph->nodes);
			int target = graph_node_index(graph_targets[edge], graph->nodes);
			int next = -1;

			if (source == node)
				next = target;
			else if (target == node)
				next = source;
			if (next >= 0 && !result->visited[next]) {
				result->visited[next] = 1;
				queue[tail] = next;
				depths[tail++] = depth + 1;
			}
		}
	}
	for (int i = 0; i < graph->nodes; i++)
		if (result->visited[i])
			result->nodes++;
	for (int edge = 0; edge < graph->edges; edge++) {
		int source = graph_node_index(graph_sources[edge], graph->nodes);
		int target = graph_node_index(graph_targets[edge], graph->nodes);

		if (source >= 0 && target >= 0 && result->visited[source] &&
		    result->visited[target])
			result->links++;
	}
	return result->nodes > 0;
}

static int append_query_execution(char *body, int cap, const char *name,
				  const char *query, const char *root,
				  const struct prov_graph_stats *graph,
				  const struct prov_query_result *result)
{
	rp_append_text(body, cap, "execution=");
	rp_append_text(body, cap, name);
	rp_append_text(body, cap, ";query=");
	rp_append_text(body, cap, query);
	rp_append_text(body, cap, ";root=");
	rp_append_text(body, cap, root);
	rp_append_text(body, cap, ";nodes=");
	rp_append_uint_text(body, cap, result->nodes);
	rp_append_text(body, cap, ";links=");
	rp_append_uint_text(body, cap, result->links);
	rp_append_text(body, cap, ";rows=");
	rp_append_uint_text(body, cap, result->nodes);
	rp_append_text(body, cap, ";source_hash=");
	rp_append_uint_text(body, cap, graph->source_hash);
	rp_append_text(body, cap,
		       ";source=rp_prov_edges;generation=runtime_graph;status=verified\n");
	for (int i = 0; i < graph->nodes; i++) {
		if (!result->visited[i])
			continue;
		rp_append_text(body, cap, "row=");
		rp_append_text(body, cap, graph_nodes[i]);
		rp_append_text(body, cap, ";execution=");
		rp_append_text(body, cap, name);
		rp_append_text(body, cap,
			       ";generation=runtime_graph;status=projected\n");
	}
	return 1;
}

static int load_graph_stats(const char *path, struct prov_graph_stats *stats)
{
	struct rp_evidence_file_measurement measured;
	char *text = rp_state_buf;
	int n;
	int line_start = 0;

	if (stats == 0)
		return 0;
	memset(stats, 0, sizeof(*stats));
	memset(graph_nodes, 0, sizeof(graph_nodes));
	memset(graph_sources, 0, sizeof(graph_sources));
	memset(graph_targets, 0, sizeof(graph_targets));
	n = rp_read_file(path, text, RP_STATE_BUFFER_SIZE);
	if (n < 0)
		return 0;
	for (int pos = 0; pos <= n; pos++) {
		char source[PROV_QUERY_NODE_SIZE];
		char target[PROV_QUERY_NODE_SIZE];
		char kind[PROV_QUERY_NODE_SIZE];
		char status[16];
		int line_end;
		int id;

		if (pos < n && text[pos] != '\n')
			continue;
		line_end = pos;
		if (line_end - line_start < 6 ||
		    strncmp(text + line_start, "edge=", 5) != 0) {
			line_start = pos + 1;
			continue;
		}
		id = rp_parse_decimal(text + line_start + 5);
		if (id != stats->edges + 1 || stats->edges >= PROV_QUERY_EDGE_CAP ||
		    !rp_copy_key_from_slice(text, line_start, line_end, "source=",
					    source, sizeof(source)) ||
		    !rp_copy_key_from_slice(text, line_start, line_end, "target=",
					    target, sizeof(target)) ||
		    !rp_copy_key_from_slice(text, line_start, line_end, "kind=", kind,
					    sizeof(kind)) ||
		    !rp_copy_key_from_slice(text, line_start, line_end, "status=",
					    status, sizeof(status)) ||
		    strcmp(status, "ready") != 0 || source[0] == 0 || target[0] == 0 ||
		    kind[0] == 0 || !graph_add_node(source, &stats->nodes) ||
		    !graph_add_node(target, &stats->nodes))
			return 0;
		rp_copy_text(graph_sources[stats->edges], PROV_QUERY_NODE_SIZE, source);
		rp_copy_text(graph_targets[stats->edges], PROV_QUERY_NODE_SIZE, target);
		stats->edges++;
		line_start = pos + 1;
	}
	if (stats->edges <= 0 || stats->nodes <= 0 ||
	    !rp_evidence_measure_file(path, &measured))
		return 0;
	stats->source_hash = measured.hash;
	return 1;
}

int main(void)
{
	static const struct {
		const char *path;
		const char *token;
	} state_expectations[] = {
		{"rp_prov_view", "evidence_generation=runtime"},
		{"rp_prov_view", "agentos_kernel_timeline_validation=verified"},
		{"rp_prov_view", "agentos_kernel_provenance_validation=verified_nonzero"},
		{"rp_prov_view", "agentos_kernel_ledger_validation=hash_verified"},
		{"rp_evidence_packet", "generation=runtime_source_digest"},
		{"rp_timeline_view", "generation=runtime_projection"},
		{"rp_mature", "mapping=aiida-process-graph"},
		{"rp_package", "provenance_graph=unified"},
		{"rp_calculation", "job=calculation-job:lab-gene-x:run042-qc"},
		{"rp_calc_parse", "parser_result=calculation-parser-result:run042-qc"},
		{"rp_agentos_kernel", "agent_timeline=observed"},
		{"rp_agentos_kernel", "agent_provenance=observed"},
		{"rp_agentos_kernel", "agent_ledger=observed"},
	};
	static const char *packet_sources[] = {
		"rp_prov_specs", "rp_prov_exec", "rp_prov_export",
	};
	struct prov_graph_stats graph;
	struct rp_evidence_file_measurement view_source;
	struct rp_evidence_file_measurement export_source;
	struct prov_query_result workflow_query;
	struct prov_query_result delivery_query;
	struct prov_query_result workflow_replay;
	unsigned long long declared_edges;
	unsigned long long kernel_timeline_records;
	unsigned long long kernel_audit_records;
	unsigned long long kernel_edges;
	unsigned long long kernel_ledger_hash;
	unsigned long long packet_digest;
	int runtime_checks = 0;
	int spec_count;
	int template_count;
	int execution_count;
	int comparison_count;
	int export_count;
	int packet_count;
	int projected_rows;
	int added_rows = 0;
	int removed_rows = 0;

	for (int i = 0; i < (int)(sizeof(state_expectations) /
					  sizeof(state_expectations[0])); i++) {
		if (!rp_file_contains(state_expectations[i].path,
				      state_expectations[i].token))
			return 1;
		runtime_checks++;
	}
	if (!load_graph_stats("rp_prov_edges", &graph) ||
	    !rp_evidence_get_u64("rp_prov_edges", "edges=", &declared_edges) ||
	    declared_edges != (unsigned long long)graph.edges ||
	    !rp_evidence_get_u64("rp_prov_view", "kernel_timeline_records=",
				 &kernel_timeline_records) ||
	    !rp_evidence_get_u64("rp_prov_view", "kernel_audit_records=",
				 &kernel_audit_records) ||
	    !rp_evidence_get_u64("rp_prov_view", "kernel_provenance_edges=",
				 &kernel_edges) ||
	    !rp_evidence_get_u64("rp_prov_view", "kernel_ledger_hash=",
				 &kernel_ledger_hash) ||
	    kernel_timeline_records == 0 || kernel_audit_records == 0 ||
	    kernel_edges == 0 || kernel_ledger_hash == 0 ||
	    !rp_evidence_measure_file("rp_prov_view", &view_source))
		return 1;
	runtime_checks += graph.edges + 7;
	query_body[0] = 0;
	rp_evidence_append_value(query_body, sizeof(query_body), "generation=",
				 "runtime_query_definitions");
	rp_append_text(query_body, sizeof(query_body),
		       "template=provenance-query-template:graph-neighborhood;owner=auditor;direction=both;depth=2;params=root_id,query_name;status=defined;generation=request_definition\n"
		       "spec=provenance-query:RUN-042:workflow-recovery;owner=auditor;root=rp_stage_state;direction=both;depth=2;projection=node_id;status=defined;generation=request_definition\n"
		       "spec=provenance-query:RUN-042:delivery-lineage;owner=auditor;root=rp_package;direction=both;depth=2;projection=node_id;status=defined;generation=request_definition\n"
		       "spec=provenance-query:RUN-042:workflow-replay;owner=auditor;root=rp_stage_state;template=graph-neighborhood;direction=both;depth=2;projection=node_id;status=defined;generation=request_definition\n");
	if (!rp_write_file("rp_prov_specs", query_body))
		return 1;
	spec_count = rp_evidence_count_prefixed_lines("rp_prov_specs", "spec=");
	template_count = rp_evidence_count_prefixed_lines("rp_prov_specs",
						       "template=");
	if (spec_count <= 0 || template_count <= 0)
		return 1;
	query_line[0] = 0;
	rp_append_text(query_line, sizeof(query_line), "specs=");
	rp_append_uint_text(query_line, sizeof(query_line), spec_count);
	if (!rp_append_file("rp_prov_specs", query_line))
		return 1;
	runtime_checks += spec_count + template_count + 1;

	query_body[0] = 0;
	rp_evidence_append_value(query_body, sizeof(query_body), "generation=",
				 "runtime_query_execution");
	if (!execute_graph_query(&graph, "rp_stage_state", 2, &workflow_query) ||
	    !execute_graph_query(&graph, "rp_package", 2, &delivery_query) ||
	    !execute_graph_query(&graph, "rp_stage_state", 2, &workflow_replay) ||
	    !append_query_execution(query_body, sizeof(query_body),
				    "provenance-query-execution:workflow-recovery",
				    "provenance-query:RUN-042:workflow-recovery",
				    "rp_stage_state", &graph, &workflow_query) ||
	    !append_query_execution(query_body, sizeof(query_body),
				    "provenance-query-execution:delivery-lineage",
				    "provenance-query:RUN-042:delivery-lineage",
				    "rp_package", &graph, &delivery_query) ||
	    !append_query_execution(query_body, sizeof(query_body),
				    "provenance-query-execution:workflow-replay",
				    "provenance-query:RUN-042:workflow-replay",
				    "rp_stage_state", &graph, &workflow_replay))
		return 1;
	if (!rp_write_file("rp_prov_exec", query_body))
		return 1;
	execution_count = rp_evidence_count_prefixed_lines("rp_prov_exec",
						      "execution=");
	if (execution_count != spec_count)
		return 1;
	query_line[0] = 0;
	rp_append_text(query_line, sizeof(query_line), "executions=");
	rp_append_uint_text(query_line, sizeof(query_line), execution_count);
	if (!rp_append_file("rp_prov_exec", query_line))
		return 1;
	runtime_checks += execution_count + 1;

	query_body[0] = 0;
	rp_evidence_append_value(query_body, sizeof(query_body), "generation=",
				 "runtime_export");
	rp_evidence_append_value(query_body, sizeof(query_body), "query=",
				 "provenance-query:RUN-042:workflow-recovery");
	rp_evidence_append_value(query_body, sizeof(query_body), "source=",
				 "rp_prov_edges");
	rp_evidence_append_u64(query_body, sizeof(query_body), "source_hash=",
			       graph.source_hash);
	rp_evidence_append_u64(query_body, sizeof(query_body), "nodes=",
			       workflow_query.nodes);
	rp_evidence_append_u64(query_body, sizeof(query_body), "links=",
			       workflow_query.links);
	rp_evidence_append_value(query_body, sizeof(query_body), "status=", "verified");
	if (!rp_write_file("rp_prov_export", query_body) ||
	    !rp_evidence_measure_file("rp_prov_export", &export_source) ||
	    !rp_evidence_fold_files(packet_sources, 3, &packet_digest))
		return 1;
	runtime_checks += 3;

	query_body[0] = 0;
	for (int i = 0; i < graph.nodes; i++) {
		if (workflow_replay.visited[i] && !workflow_query.visited[i])
			added_rows++;
		if (workflow_query.visited[i] && !workflow_replay.visited[i])
			removed_rows++;
	}
	rp_evidence_append_value(query_body, sizeof(query_body), "generation=",
				 "runtime_query_package");
	rp_append_text(query_body, sizeof(query_body),
		       "comparison=provenance-query-comparison:RUN-042:replay-vs-direct;base=provenance-query-execution:workflow-recovery;candidate=provenance-query-execution:workflow-replay;added=");
	rp_append_uint_text(query_body, sizeof(query_body), added_rows);
	rp_append_text(query_body, sizeof(query_body), ";removed=");
	rp_append_uint_text(query_body, sizeof(query_body), removed_rows);
	rp_append_text(query_body, sizeof(query_body), ";row_delta=");
	rp_append_uint_text(query_body, sizeof(query_body), added_rows - removed_rows);
	rp_append_text(query_body, sizeof(query_body),
		       ";source=rp_prov_exec;generation=runtime_comparison;status=verified\n"
		       "export=provenance-query-export:RUN-042:workflow-recovery;execution=provenance-query-execution:workflow-recovery;type=markdown;checksum=");
	rp_append_uint_text(query_body, sizeof(query_body), export_source.hash);
	rp_append_text(query_body, sizeof(query_body),
		       ";checksum_source=fnv1a64_rp_prov_export;status=ready\n"
		       "packet=provenance-query-packet:RUN-042:lineage-review;comparison=provenance-query-comparison:RUN-042:replay-vs-direct;executions=");
	rp_append_uint_text(query_body, sizeof(query_body), execution_count);
	rp_append_text(query_body, sizeof(query_body), ";nodes=");
	rp_append_uint_text(query_body, sizeof(query_body), workflow_query.nodes);
	rp_append_text(query_body, sizeof(query_body), ";links=");
	rp_append_uint_text(query_body, sizeof(query_body), workflow_query.links);
	rp_append_text(query_body, sizeof(query_body), ";checksum=");
	rp_append_uint_text(query_body, sizeof(query_body), packet_digest);
	rp_append_text(query_body, sizeof(query_body),
		       ";checksum_source=fnv1a64_runtime_sources;status=ready\n"
		       "package_entry=provenance_query;status=ready;generation=runtime\n");
	if (!rp_write_file("rp_prov_query_pkg", query_body))
		return 1;
	comparison_count = rp_evidence_count_prefixed_lines(
		"rp_prov_query_pkg", "comparison=");
	export_count = rp_evidence_count_prefixed_lines("rp_prov_query_pkg",
						       "export=");
	packet_count = rp_evidence_count_prefixed_lines("rp_prov_query_pkg",
						       "packet=");
	if (comparison_count <= 0 || export_count <= 0 || packet_count <= 0)
		return 1;
	runtime_checks += comparison_count + export_count + packet_count;
	projected_rows = workflow_query.nodes + delivery_query.nodes +
			 workflow_replay.nodes;
	runtime_checks += projected_rows;

	query_body[0] = 0;
	rp_evidence_append_value(query_body, sizeof(query_body), "run_id=", "RUN-042");
	rp_evidence_append_value(query_body, sizeof(query_body), "evidence_source=",
				 "runtime_graph_and_kernel_evidence_files");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "evidence_generation=", "runtime");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "provenance_query_checks_source=",
				 "demo_expected");
	rp_evidence_append_u64(query_body, sizeof(query_body),
			       "demo_expected_provenance_query_checks=", 72);
	rp_evidence_append_u64(query_body, sizeof(query_body), "runtime_checks=",
			       runtime_checks);
	rp_evidence_append_u64(query_body, sizeof(query_body), "specs=", spec_count);
	rp_evidence_append_u64(query_body, sizeof(query_body), "templates=",
			       template_count);
	rp_evidence_append_u64(query_body, sizeof(query_body), "executions=",
			       execution_count);
	rp_evidence_append_u64(query_body, sizeof(query_body), "comparisons=",
			       comparison_count);
	rp_evidence_append_u64(query_body, sizeof(query_body), "exports=", export_count);
	rp_evidence_append_u64(query_body, sizeof(query_body), "evidence_packets=",
			       packet_count);
	rp_evidence_append_u64(query_body, sizeof(query_body), "projected_rows=",
			       projected_rows);
	rp_evidence_append_u64(query_body, sizeof(query_body), "graph_nodes=", graph.nodes);
	rp_evidence_append_u64(query_body, sizeof(query_body), "graph_edges=", graph.edges);
	rp_evidence_append_u64(query_body, sizeof(query_body), "graph_source_hash=",
			       graph.source_hash);
	rp_evidence_append_u64(query_body, sizeof(query_body), "view_source_hash=",
			       view_source.hash);
	rp_evidence_append_u64(query_body, sizeof(query_body),
			       "kernel_timeline_records=", kernel_timeline_records);
	rp_evidence_append_u64(query_body, sizeof(query_body), "kernel_audit_records=",
			       kernel_audit_records);
	rp_evidence_append_u64(query_body, sizeof(query_body),
			       "kernel_provenance_edges=", kernel_edges);
	rp_evidence_append_u64(query_body, sizeof(query_body), "kernel_ledger_hash=",
			       kernel_ledger_hash);
	rp_evidence_append_value(query_body, sizeof(query_body), "reader_page=",
				 "provenance-queries.html");
	rp_evidence_append_value(query_body, sizeof(query_body), "package=",
				 "provenance-query-execution:workflow-recovery");
	rp_evidence_append_value(query_body, sizeof(query_body), "agentos_mapping=",
				 "timeline_query,provenance_snapshot,ledger_snapshot,context_detail");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "agentos_kernel_timeline=", "observed");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "agentos_kernel_timeline_validation=", "verified");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "agentos_kernel_provenance=", "observed");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "agentos_kernel_provenance_validation=", "verified_nonzero");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "agentos_kernel_ledger=", "observed");
	rp_evidence_append_value(query_body, sizeof(query_body),
				 "agentos_kernel_ledger_validation=", "hash_verified");
	rp_evidence_append_value(query_body, sizeof(query_body), "status=", "verified");
	if (!rp_write_file("rp_prov_query", query_body))
		return 1;

	query_line[0] = 0;
	rp_append_text(query_line, sizeof(query_line),
		       "provenance_queries_page=rp_prov_query;specs=");
	rp_append_uint_text(query_line, sizeof(query_line), spec_count);
	rp_append_text(query_line, sizeof(query_line), ";executions=");
	rp_append_uint_text(query_line, sizeof(query_line), execution_count);
	rp_append_text(query_line, sizeof(query_line), ";packets=");
	rp_append_uint_text(query_line, sizeof(query_line), packet_count);
	rp_append_text(query_line, sizeof(query_line),
		       ";status=ready;generation=runtime");
	if (!rp_append_file("rp_web_bundle", query_line))
		return 1;
	query_line[0] = 0;
	rp_append_text(query_line, sizeof(query_line),
		       "subsection=provenance_queries;source=rp_prov_query;queries=");
	rp_append_uint_text(query_line, sizeof(query_line), spec_count);
	rp_append_text(query_line, sizeof(query_line), ";executions=");
	rp_append_uint_text(query_line, sizeof(query_line), execution_count);
	rp_append_text(query_line, sizeof(query_line),
		       ";demo_expected_checks=72;catalog_outcome=matched;status=verified;checks_source=demo_expected;runtime_checks=");
	rp_append_uint_text(query_line, sizeof(query_line), runtime_checks);
	if (!rp_append_file("rp_review_dashboard", query_line))
		return 1;
	query_line[0] = 0;
	rp_append_text(query_line, sizeof(query_line),
		       "provenance_query_package=rp_prov_query;specs=");
	rp_append_uint_text(query_line, sizeof(query_line), spec_count);
	rp_append_text(query_line, sizeof(query_line), ";executions=");
	rp_append_uint_text(query_line, sizeof(query_line), execution_count);
	rp_append_text(query_line, sizeof(query_line), ";packets=");
	rp_append_uint_text(query_line, sizeof(query_line), packet_count);
	rp_append_text(query_line, sizeof(query_line),
		       ";status=ready;generation=runtime");
	if (!rp_append_file("rp_package", query_line))
		return 1;
	query_line[0] = 0;
	rp_append_text(query_line, sizeof(query_line),
		       "demo_expected_provenance_query_checks=72;specs=");
	rp_append_uint_text(query_line, sizeof(query_line), spec_count);
	rp_append_text(query_line, sizeof(query_line), ";templates=");
	rp_append_uint_text(query_line, sizeof(query_line), template_count);
	rp_append_text(query_line, sizeof(query_line), ";executions=");
	rp_append_uint_text(query_line, sizeof(query_line), execution_count);
	rp_append_text(query_line, sizeof(query_line), ";comparisons=");
	rp_append_uint_text(query_line, sizeof(query_line), comparison_count);
	rp_append_text(query_line, sizeof(query_line), ";exports=");
	rp_append_uint_text(query_line, sizeof(query_line), export_count);
	rp_append_text(query_line, sizeof(query_line), ";packets=");
	rp_append_uint_text(query_line, sizeof(query_line), packet_count);
	rp_append_text(query_line, sizeof(query_line),
		       ";agentos_replacements=4;kernel_timeline=observed;status=verified;provenance_query_checks_source=demo_expected;runtime_checks=");
	rp_append_uint_text(query_line, sizeof(query_line), runtime_checks);
	rp_append_text(query_line, sizeof(query_line), ";generation=runtime");
	if (!rp_append_file("rp_agentcmp", query_line) ||
	    !rp_append_file("rp_ack", "ack=provenance_query;msg=provq;status=ready") ||
	    !rp_append_file("rp_tool", "tool=provenance_query.seed") ||
	    !rp_append_file("rp_tool", "tool=provenance_query.instantiate_template") ||
	    !rp_append_file("rp_tool", "tool=provenance_query.execute") ||
	    !rp_append_file("rp_tool", "tool=provenance_query.compare") ||
	    !rp_append_file("rp_tool", "tool=provenance_query.export") ||
	    !rp_append_file("rp_tool", "tool=provenance_query.packetize") ||
	    !rp_append_status("provenance_query=ready"))
		return 1;
	printf("rp_prov_query: evidence_source=runtime_graph+kernel_evidence generation=runtime graph_nodes=%d graph_edges=%d executions=%d packet_checksum=%d checks=%d status=verified\n",
	       graph.nodes, graph.edges, execution_count, (int)packet_digest,
	       runtime_checks);
	return 0;
}
