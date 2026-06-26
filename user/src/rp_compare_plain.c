#include <stdio.h>
#include <research_platform_state.h>

static int check_seed_value(const char *kind, const char *key, const char *fallback, const char *path, const char *prefix)
{
	char value[96];
	char token[160];
	if (!rp_host_seed_copy_value_for_kind(kind, key, value, sizeof(value))) {
		rp_copy_text(value, sizeof(value), fallback);
	}
	rp_copy_text(token, sizeof(token), prefix);
	rp_append_text(token, sizeof(token), value);
	return rp_file_contains(path, token);
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_object_query", "hits=8");
	ok = ok && rp_file_contains("rp_lineage", "edges=7");
	ok = ok && rp_file_contains("rp_site", "pages=42");
	ok = ok && rp_file_contains("rp_site", "page=agentos_readiness");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_knowledge", "semantic_relations=6");
	ok = ok && rp_file_contains("rp_knowledge", "citation_key=library2026");
	ok = ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && rp_file_contains("rp_input", "dynamic_submissions=4");
	ok = ok && rp_file_contains("rp_input", "dynamic_queue=plain_ucore_file_backed");
	ok = ok && rp_file_contains("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	ok = ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "workbench_readiness=rp_workbench_ready;status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_answer=rp_workbench_answer;citations=5;status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_brief=rp_workbench_brief;handoff=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_runbook=rp_workbench_runbook;commands=6");
	ok = ok && rp_file_contains("rp_runner", "workbench_timeline=rp_workbench_timeline;events=8");
	ok = ok && rp_file_contains("rp_runner", "workbench_file_manifest=rp_workbench_manifest;files=9;sha_records=9");
	ok = ok && rp_file_contains("rp_runner", "next_action=build_delivery_manifest");
	ok = ok && rp_file_contains("rp_runner", "citation_count=5");
	ok = ok && rp_file_contains("rp_runner", "handoff=ready");
	ok = ok && rp_file_contains("rp_runner", "continuation_guide=ready");
	ok = ok && rp_file_contains("rp_runner", "events=8");
	ok = ok && rp_file_contains("rp_runner", "sha_records=9");
	ok = ok && rp_file_contains("rp_runner", "dynamic_input_runs=4");
	ok = ok && rp_file_contains("rp_runner", "dynamic_run=usable-run:RUN-904");
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		rp_copy_text(token, sizeof(token), "host_action_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_input", token);
		rp_copy_text(token, sizeof(token), "host_action_research_run=usable-run:");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_input", token);
		rp_copy_text(token, sizeof(token), "host_action_run=usable-run:");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_report_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_run_id=");
		rp_append_text(token, sizeof(token), seed_run);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_runner", "host_action_status=completed");
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_research_input=ready");
		ok = ok && rp_file_contains("rp_actionio", "host_action_research_run=1");
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		char profile[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
			rp_copy_text(profile, sizeof(profile), "plain_ucore");
		}
		rp_copy_text(token, sizeof(token), "host_action_compare=");
		rp_append_text(token, sizeof(token), profile);
		rp_append_text(token, sizeof(token), ";status=ready");
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_compare_requested=1");
		rp_copy_text(token, sizeof(token), "host_action_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_agentcmp", token);
		rp_copy_text(token, sizeof(token), "host_report_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_compare_profile=");
		rp_append_text(token, sizeof(token), profile);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_agentcompare=1");
	}
	if (rp_host_seed_has("kind=human_review")) {
		char reviewer[48];
		char decision[48];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=human_review", "reviewer=", reviewer, sizeof(reviewer))) {
			rp_copy_text(reviewer, sizeof(reviewer), "HOST");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=human_review", "decision=", decision, sizeof(decision))) {
			rp_copy_text(decision, sizeof(decision), "needs_revision");
		}
		rp_copy_text(token, sizeof(token), "host_action_human_review=usable-review:");
		rp_append_text(token, sizeof(token), reviewer);
		rp_append_text(token, sizeof(token), ":1");
		ok = ok && rp_file_contains("rp_review2", token);
		rp_copy_text(token, sizeof(token), "host_action_review_decision=");
		rp_append_text(token, sizeof(token), decision);
		ok = ok && rp_file_contains("rp_review2", token);
		rp_copy_text(token, sizeof(token), "host_report_reviewer=");
		rp_append_text(token, sizeof(token), reviewer);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_report_review_decision=");
		rp_append_text(token, sizeof(token), decision);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_action_reviewer=");
		rp_append_text(token, sizeof(token), reviewer);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_review_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_human_review=1");
	}
	if (rp_host_seed_has("kind=revision_task")) {
		ok = ok && rp_file_contains("rp_revision", "host_action_revision_task=created");
		char targets[80];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
			rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
		}
		rp_copy_text(token, sizeof(token), "host_action_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_revision", token);
		rp_copy_text(token, sizeof(token), "host_report_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_revision_targets=");
		rp_append_text(token, sizeof(token), targets);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_revision_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_revision=1");
	}
	if (rp_host_seed_has("kind=revision_run")) {
		ok = ok && rp_file_contains("rp_revision", "host_action_revision_run=completed");
		char revision_run[48];
		char token[130];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "run_id=", revision_run, sizeof(revision_run))) {
			rp_copy_text(revision_run, sizeof(revision_run), "RUN-900");
		}
		rp_copy_text(token, sizeof(token), "host_action_revision_run=usable-run:");
		rp_append_text(token, sizeof(token), revision_run);
		rp_append_text(token, sizeof(token), "-rev2");
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_revision=1");
	}
	if (rp_host_seed_has("kind=workbench") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_advance") ||
	    rp_host_seed_has("kind=workbench_auto_advance") ||
	    rp_host_seed_has("kind=workbench_task") ||
	    rp_host_seed_has("kind=workbench_note") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_evidence_search") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_export")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench=completed");
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_workbench_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_workbench=1");
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_id=");
		ok = ok && rp_file_contains("rp_api_compare", "host_action_workbench=");
	}
	if (rp_host_seed_has("kind=workbench_answer")) {
		char question[96];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_answer", "question=", question, sizeof(question))) {
			rp_copy_text(question, sizeof(question), "What is ready for review?");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_question=");
		rp_append_text(token, sizeof(token), question);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_answer=generated");
	}
	if (rp_host_seed_has("kind=workbench_evidence_search")) {
		char query[96];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_evidence_search", "query=", query, sizeof(query))) {
			rp_copy_text(query, sizeof(query), "recovery evidence");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_evidence_query=");
		rp_append_text(token, sizeof(token), query);
		ok = ok && rp_file_contains("rp_runner", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_query=");
		rp_append_text(token, sizeof(token), query);
		ok = ok && rp_file_contains("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=workbench_task")) {
		char task[64];
		char status[32];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_task", "task=", task, sizeof(task))) {
			rp_copy_text(task, sizeof(task), "human_review");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_task", "status=", status, sizeof(status))) {
			rp_copy_text(status, sizeof(status), "waiting");
		}
		rp_copy_text(token, sizeof(token), "host_action_workbench_task=");
		rp_append_text(token, sizeof(token), task);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_task_status=");
		rp_append_text(token, sizeof(token), status);
		ok = ok && rp_file_contains("rp_runner", token);
	}
	if (rp_host_seed_has("kind=workbench_note")) {
		char kind[48];
		char title[80];
		char token[140];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_note", "note_kind=", kind, sizeof(kind))) {
			rp_copy_text(kind, sizeof(kind), "decision");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_note", "title=", title, sizeof(title))) {
			rp_copy_text(title, sizeof(title), "Scope decision");
		}
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_note=recorded");
		rp_copy_text(token, sizeof(token), "host_action_workbench_note_kind=");
		rp_append_text(token, sizeof(token), kind);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
		rp_copy_text(token, sizeof(token), "host_action_workbench_note_title=");
		rp_append_text(token, sizeof(token), title);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=workbench_file_verify")) {
		char manifest[80];
		char token[128];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "manifest=", manifest, sizeof(manifest))) {
			rp_copy_text(manifest, sizeof(manifest), "delivery-manifest.json");
		}
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_file_verify=passed");
		rp_copy_text(token, sizeof(token), "host_action_workbench_manifest=");
		rp_append_text(token, sizeof(token), manifest);
		ok = ok && rp_file_contains("rp_runner", token);
		ok = ok && rp_file_contains("rp_api_compare", token);
	}
	if (rp_host_seed_has("kind=workbench")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_created=1");
		ok = ok && check_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_runner", "host_action_workbench_title=");
		ok = ok && check_seed_value("kind=workbench", "workbench_title=", "RUN-900 workbench", "rp_api_compare", "host_action_workbench_title=");
		ok = ok && check_seed_value("kind=workbench", "literature_query=", "agent workflow provenance", "rp_runner", "host_action_workbench_literature_query=");
		ok = ok && check_seed_value("kind=workbench", "literature_query=", "agent workflow provenance", "rp_api_compare", "host_action_workbench_literature_query=");
	}
	if (rp_host_seed_has("kind=workbench_advance")) {
		ok = ok && check_seed_value("kind=workbench_advance", "task=", "delivery_manifest", "rp_runner", "host_action_workbench_task=");
		ok = ok && check_seed_value("kind=workbench_advance", "task=", "delivery_manifest", "rp_api_compare", "host_action_workbench_advance_task=");
	}
	if (rp_host_seed_has("kind=workbench_auto_advance")) {
		ok = ok && check_seed_value("kind=workbench_auto_advance", "step_limit=", "8", "rp_runner", "host_action_workbench_step_limit=");
		ok = ok && check_seed_value("kind=workbench_auto_advance", "step_limit=", "8", "rp_api_compare", "host_action_workbench_step_limit=");
	}
	if (rp_host_seed_has("kind=workbench_notes")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_notes=exported");
		ok = ok && check_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_runner", "host_action_workbench_notes_filter=");
		ok = ok && check_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_api_compare", "host_action_workbench_notes_filter=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_handoff=prepared");
		ok = ok && check_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_runner", "host_action_workbench_handoff_scope=");
		ok = ok && check_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_api_compare", "host_action_workbench_handoff_scope=");
	}
	if (rp_host_seed_has("kind=workbench_readiness")) ok = ok && rp_file_contains("rp_runner", "host_action_workbench_readiness=checked");
	if (rp_host_seed_has("kind=workbench_answer_audit")) ok = ok && rp_file_contains("rp_runner", "host_action_workbench_answer_audit=passed");
	if (rp_host_seed_has("kind=workbench_brief")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_brief=exported");
		ok = ok && check_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_runner", "host_action_workbench_brief_format=");
		ok = ok && check_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_api_compare", "host_action_workbench_brief_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_evidence_dossier=exported");
		ok = ok && check_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_runner", "host_action_workbench_dossier_format=");
		ok = ok && check_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_api_compare", "host_action_workbench_dossier_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_graph")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_evidence_graph=exported");
		ok = ok && check_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_runner", "host_action_workbench_graph_format=");
		ok = ok && check_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_api_compare", "host_action_workbench_graph_format=");
	}
	if (rp_host_seed_has("kind=workbench_citations")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_citations=exported");
		ok = ok && check_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_runner", "host_action_workbench_citation_format=");
		ok = ok && check_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_api_compare", "host_action_workbench_citation_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_manuscript=exported");
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_runner", "host_action_workbench_manuscript_format=");
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_api_compare", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_manuscript_audit=passed");
		ok = ok && check_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_runner", "host_action_workbench_audit_scope=");
		ok = ok && check_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_api_compare", "host_action_workbench_audit_scope=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_revision_plan=ready");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_runner", "host_action_workbench_revision_area=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_api_compare", "host_action_workbench_revision_area=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_revision_task=updated");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_task=", "1", "rp_runner", "host_action_workbench_revision_task=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_runner", "host_action_workbench_revision_status=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_api_compare", "host_action_workbench_revision_status=");
	}
	if (rp_host_seed_has("kind=workbench_task_board")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_task_board=exported");
		ok = ok && check_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_runner", "host_action_workbench_board_filter=");
		ok = ok && check_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_api_compare", "host_action_workbench_board_filter=");
	}
	if (rp_host_seed_has("kind=workbench_task_board_row")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_task_board_row=updated");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_id=", "usable-workbench:RUN-900:board:task:human_review", "rp_runner", "host_action_workbench_row_id=");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_runner", "host_action_workbench_row_status=");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_api_compare", "host_action_workbench_row_status=");
	}
	if (rp_host_seed_has("kind=workbench_runbook")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_runbook=exported");
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_runner", "host_action_workbench_runbook_format=");
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_api_compare", "host_action_workbench_runbook_format=");
	}
	if (rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_timeline=exported");
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_runner", "host_action_workbench_timeline_format=");
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_api_compare", "host_action_workbench_timeline_format=");
	}
	if (rp_host_seed_has("kind=workbench_file_manifest")) ok = ok && rp_file_contains("rp_runner", "host_action_workbench_file_manifest=exported");
	if (rp_host_seed_has("kind=workbench_export")) {
		ok = ok && rp_file_contains("rp_runner", "host_action_workbench_export=ready");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_runner", "host_action_workbench_bundle=");
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_api_compare", "host_action_workbench_bundle=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && rp_file_contains("rp_revision", "host_action_workbench_writing=ready");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_revision", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
		ok = ok && check_seed_value("kind=workbench_manuscript_audit", "audit_scope=", "citations", "rp_revision", "host_action_workbench_audit_scope=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_plan", "revision_area=", "methods", "rp_revision", "host_action_workbench_revision_area=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_task=", "1", "rp_revision", "host_action_workbench_revision_task=");
		ok = ok && check_seed_value("kind=workbench_manuscript_revision_task", "revision_status=", "done", "rp_revision", "host_action_workbench_revision_status=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_export") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_package=ready");
	}
	if (rp_host_seed_has("kind=workbench_complete")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_completion=ready");
	}
	if (rp_host_seed_has("kind=workbench_readiness")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_readiness=checked");
	}
	if (rp_host_seed_has("kind=workbench_answer_audit")) {
		ok = ok && rp_file_contains("rp_package", "host_action_workbench_answer_audit=passed");
	}
	if (rp_host_seed_has("kind=workbench_notes")) {
		ok = ok && check_seed_value("kind=workbench_notes", "notes_filter=", "decision", "rp_package", "host_action_workbench_notes_filter=");
	}
	if (rp_host_seed_has("kind=workbench_handoff_package")) {
		ok = ok && check_seed_value("kind=workbench_handoff_package", "handoff_scope=", "full", "rp_package", "host_action_workbench_handoff_scope=");
	}
	if (rp_host_seed_has("kind=workbench_export")) {
		ok = ok && check_seed_value("kind=workbench_export", "bundle=", "workbench-bundle.zip", "rp_package", "host_action_workbench_bundle=");
	}
	if (rp_host_seed_has("kind=workbench_file_manifest")) {
		ok = ok && check_seed_value("kind=workbench_file_manifest", "manifest=", "delivery-manifest.json", "rp_package", "host_action_workbench_manifest=");
	}
	if (!rp_host_seed_has("kind=workbench_file_manifest") && rp_host_seed_has("kind=workbench_file_verify")) {
		ok = ok && check_seed_value("kind=workbench_file_verify", "manifest=", "delivery-manifest.json", "rp_package", "host_action_workbench_manifest=");
	}
	if (rp_host_seed_has("kind=workbench_brief")) {
		ok = ok && check_seed_value("kind=workbench_brief", "brief_format=", "html", "rp_package", "host_action_workbench_brief_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
		ok = ok && check_seed_value("kind=workbench_evidence_dossier", "dossier_format=", "markdown", "rp_package", "host_action_workbench_dossier_format=");
	}
	if (rp_host_seed_has("kind=workbench_evidence_graph")) {
		ok = ok && check_seed_value("kind=workbench_evidence_graph", "graph_format=", "dot", "rp_package", "host_action_workbench_graph_format=");
	}
	if (rp_host_seed_has("kind=workbench_citations")) {
		ok = ok && check_seed_value("kind=workbench_citations", "citation_format=", "bibtex", "rp_package", "host_action_workbench_citation_format=");
	}
	if (rp_host_seed_has("kind=workbench_manuscript")) {
		ok = ok && check_seed_value("kind=workbench_manuscript", "manuscript_format=", "markdown", "rp_package", "host_action_workbench_manuscript_format=");
	}
	if (rp_host_seed_has("kind=workbench_task_board")) {
		ok = ok && check_seed_value("kind=workbench_task_board", "board_filter=", "open", "rp_package", "host_action_workbench_board_filter=");
	}
	if (rp_host_seed_has("kind=workbench_task_board_row")) {
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_id=", "usable-workbench:RUN-900:board:task:human_review", "rp_package", "host_action_workbench_row_id=");
		ok = ok && check_seed_value("kind=workbench_task_board_row", "row_status=", "done", "rp_package", "host_action_workbench_row_status=");
	}
	if (rp_host_seed_has("kind=workbench_runbook")) {
		ok = ok && check_seed_value("kind=workbench_runbook", "runbook_format=", "markdown", "rp_package", "host_action_workbench_runbook_format=");
	}
	if (rp_host_seed_has("kind=workbench_timeline")) {
		ok = ok && check_seed_value("kind=workbench_timeline", "timeline_format=", "html", "rp_package", "host_action_workbench_timeline_format=");
	}
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=delivery")) {
		ok = ok && rp_file_contains("rp_package", "host_action_export_bundle=ready");
		char bundle[48];
		char token[120];
		if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=research_export", "bundle=", bundle, sizeof(bundle)) &&
		    !rp_host_seed_copy_value_for_kind("kind=delivery", "bundle=", bundle, sizeof(bundle))) {
			rp_copy_text(bundle, sizeof(bundle), "evidence");
		}
		rp_copy_text(token, sizeof(token), "host_action_export_bundle_name=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_package", token);
		ok = ok && rp_file_contains("rp_package", "host_action_bundle_contents=report,manifest,notebook,compare");
		rp_copy_text(token, sizeof(token), "host_report_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_report_text", token);
		rp_copy_text(token, sizeof(token), "host_manifest_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		rp_copy_text(token, sizeof(token), "host_action_bundle=");
		rp_append_text(token, sizeof(token), bundle);
		ok = ok && rp_file_contains("rp_api_compare", token);
		ok = ok && rp_file_contains("rp_actionio", "host_action_export=1");
	}
	if (rp_host_seed_has("kind=notebook_export")) {
		ok = ok && rp_file_contains("rp_nbexec", "host_action_notebook_export=ready");
		char format[32];
		char token[96];
		if (!rp_host_seed_copy_value_for_kind("kind=notebook_export", "format=", format, sizeof(format))) {
			rp_copy_text(format, sizeof(format), "ipynb");
		}
		rp_copy_text(token, sizeof(token), "host_action_notebook_format=");
		rp_append_text(token, sizeof(token), format);
		ok = ok && rp_file_contains("rp_nbexec", token);
		rp_copy_text(token, sizeof(token), "host_manifest_notebook_format=");
		rp_append_text(token, sizeof(token), format);
		ok = ok && rp_file_contains("rp_artifact_manifest", token);
		ok = ok && rp_file_contains("rp_agentcmp", "host_action_export_requested=1");
		ok = ok && rp_file_contains("rp_actionio", "host_action_export=1");
	}
	if (rp_host_seed_count() > 0) {
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_state_files=rp_input,rp_runner,rp_review2,rp_revision,rp_package,rp_nbexec,rp_agentcmp");
	}
	ok = ok && rp_file_contains("rp_lit", "literature_search=usable-literature-search:RUN-900:1");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_provpath", "critical_paths=3");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_ingest_files", "files=2");
	ok = ok && rp_file_contains("rp_ingest_files", "derived_items=5");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "snapshots=2");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "normalized_fastq=rp_artifact:rp_normalized_fastq");
	ok = ok && rp_file_contains("rp_data_preview", "previews=2");
	ok = ok && rp_file_contains("rp_data_quality", "passed=7");
	ok = ok && rp_file_contains("rp_data_transform", "transforms=2");
	ok = ok && rp_file_contains("rp_data_transform", "derived=alignment");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_artifact", "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_align_table");
	ok = ok && rp_file_contains("rp_artifact", "\"variants\":2");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_gene_counts_csv;geneA=18");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_archive_manifest;files=5");
	ok = ok && rp_file_contains("rp_figrec", "exported=3");
	ok = ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	ok = ok && rp_file_contains("rp_dataver", "release_candidate=v2");
	ok = ok && rp_file_contains("rp_reviewops", "governance=passed");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "verifications=2");
	ok = ok && rp_file_contains("rp_delta", "decision=accepted");
	ok = ok && rp_file_contains("rp_diff", "changed_items=20");
	ok = ok && rp_file_contains("rp_wfio", "compatibility_checks=6");
	ok = ok && rp_file_contains("rp_wfio", "imports=5");
	ok = ok && rp_file_contains("rp_wfio", "adapter_specs=6");
	ok = ok && rp_file_contains("rp_wfio", "migration_steps=9");
	ok = ok && rp_file_contains("rp_wfio", "cases=4");
	ok = ok && rp_file_contains("rp_wfio", "decision=ready_for_agentos");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	ok = ok && rp_file_contains("rp_review2", "rounds=2");
	ok = ok && rp_file_contains("rp_review2", "review_threads=2");
	ok = ok && rp_file_contains("rp_review2", "action_items=2");
	ok = ok && rp_file_contains("rp_review2", "human_review=usable-review:RUN-900:1");
	ok = ok && rp_file_contains("rp_review2", "requested_change=methods_retry_scope");
	ok = ok && rp_file_contains("rp_review2", "requested_change=chart_caption");
	ok = ok && rp_file_contains("rp_revision", "draft_versions=3");
	ok = ok && rp_file_contains("rp_revision", "applied_changes=2");
	ok = ok && rp_file_contains("rp_revision", "report_delta=methods_and_caption_updated");
	ok = ok && rp_file_contains("rp_datadic", "schema_drift=0");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	ok = ok && rp_file_contains("rp_fail", "failure_class=tool_output_missing");
	ok = ok && rp_file_contains("rp_runview", "scheduler_items=21");
	ok = ok && rp_file_contains("rp_taskrec", "msg=21");
	ok = ok && rp_file_contains("rp_rank", "selected=10");
	ok = ok && rp_file_contains("rp_runview", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_health", "healthy=4");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_prompt", "provider_policy=host_relay");
	ok = ok && rp_file_contains("rp_prompt", "routes=4");
	ok = ok && rp_file_contains("rp_policy", "access_profiles=4");
	ok = ok && rp_file_contains("rp_compliance", "checks=8");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_llmq", "queue_validation=passed");
	ok = ok && rp_file_contains("rp_llmq", "dispatch_ready=3");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llmeval", "fallback_checks=3");
	ok = ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	ok = ok && rp_file_contains("rp_llmlog", "request_packets=3");
	ok = ok && rp_file_contains("rp_llmlog", "secret_scan=passed");
	ok = ok && rp_file_contains("rp_sched", "queue_items=21");
	ok = ok && rp_file_contains("rp_retrylog", "attempts=2");
	ok = ok && rp_file_contains("rp_relay", "network_stack=host_only");
	ok = ok && rp_file_contains("rp_relay", "queue_consumer=rp_llm_relay");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_packets", "validated_packets=3");
	ok = ok && rp_file_contains("rp_llm_packets", "packet_schema=passed");
	ok = ok && rp_file_contains("rp_llm_packets", "matched_responses=3");
	ok = ok && rp_file_contains("rp_llm_packets", "roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_routes", "route_policy=deterministic_then_host_optional");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_guard", "blocked_packets=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "cloud_mode=optional_host_side");
	ok = ok && rp_file_contains("rp_llm_hostreq", "host_request_manifest=ready");
	ok = ok && rp_file_contains("rp_llm_hostreq", "roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_resp", "host_relay_roundtrip=ready");
	ok = ok && rp_file_contains("rp_llm_resp", "response_join=passed");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_trace=rp_llm_guard->rp_llm_fallback->rp_llm_resp");
	ok = ok && rp_file_contains("rp_llm_fallback", "offline_template_verified=1");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_submit", "data_availability=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "message_acks=35");
	ok = ok && rp_file_contains("rp_agentcmp", "tool_events=115");
	ok = ok && rp_file_contains("rp_agentcmp", "scheduler_items=21");
	ok = ok && rp_file_contains("rp_agentcmp", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_agentcmp", "selected_tasks=10");
	ok = ok && rp_file_contains("rp_agentcmp", "policy_checks=8");
	ok = ok && rp_file_contains("rp_agentcmp", "compliance=accepted");
	ok = ok && rp_file_contains("rp_agentcmp", "risk_items=3");
	ok = ok && rp_file_contains("rp_agentcmp", "capa_actions=2");
	ok = ok && rp_file_contains("rp_agentcmp", "delta_items=20");
	ok = ok && rp_file_contains("rp_agentcmp", "diff_records=1");
	ok = ok && rp_file_contains("rp_agentcmp", "claim_records=8");
	ok = ok && rp_file_contains("rp_agentcmp", "provenance_paths=3");
	ok = ok && rp_file_contains("rp_agentcmp", "data_profiles=4");
	ok = ok && rp_file_contains("rp_agentcmp", "data_pipeline_files=6");
	ok = ok && rp_file_contains("rp_agentcmp", "data_quality_checks=7");
	ok = ok && rp_file_contains("rp_agentcmp", "figure_records=3");
	ok = ok && rp_file_contains("rp_agentcmp", "trial_records=4");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_exports=5");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_portability_records=1");
	ok = ok && rp_file_contains("rp_agentcmp", "migration_steps=9");
	ok = ok && rp_file_contains("rp_agentcmp", "portability_rehearsal_cases=4");
	ok = ok && rp_file_contains("rp_agentcmp", "review_rounds=2");
	ok = ok && rp_file_contains("rp_agentcmp", "data_versions=2");
	ok = ok && rp_file_contains("rp_agentcmp", "retry_attempts=2");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_packets=3");
	ok = ok && rp_file_contains("rp_agentcmp", "llm_requests=3");
	ok = ok && rp_file_contains("rp_agentcmp", "llm_eval_passed=7");
	ok = ok && rp_file_contains("rp_agentcmp", "run_views=1");
	ok = ok && rp_file_contains("rp_agentcmp", "health_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "agent_roles=7");
	ok = ok && rp_file_contains("rp_agentcmp", "collaboration_decisions=8");
	ok = ok && rp_file_contains("rp_agentcmp", "handoffs=6");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_protocol_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_runner_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "bio_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "lab_resource_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "publication_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "knowledge_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "runtime_service_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "notebook_exports=2");
	ok = ok && rp_file_contains("rp_sreg", "samples=8");
	ok = ok && rp_file_contains("rp_ethics", "ethics=approved");
	ok = ok && rp_file_contains("rp_access", "requests=3");
	ok = ok && rp_file_contains("rp_cohort", "cohorts=2");
	ok = ok && rp_file_contains("rp_instr", "instruments=4");
	ok = ok && rp_file_contains("rp_invent", "inventory_items=9");
	ok = ok && rp_file_contains("rp_procure", "requests=3");
	ok = ok && rp_file_contains("rp_ressched", "bookings=6");
	ok = ok && rp_file_contains("rp_resrev", "review_items=10");
	ok = ok && rp_file_contains("rp_pubplan", "journal_targets=2");
	ok = ok && rp_file_contains("rp_peerresp", "responses=6");
	ok = ok && rp_file_contains("rp_fairpkg", "fair_checks=8");
	ok = ok && rp_file_contains("rp_litrev", "papers=9");
	ok = ok && rp_file_contains("rp_litrev", "evidence_extractions=3");
	ok = ok && rp_file_contains("rp_litrev", "prisma_flow=usable-prisma-flow:RUN-900:1");
	ok = ok && rp_file_contains("rp_citegraph", "citations=14");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_kanswers", "answers=4");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	ok = ok && rp_file_contains("rp_nbexec", "executed_cells=8");
	ok = ok && rp_file_contains("rp_nbexec", "notebook=reproducible-analysis.ipynb");
	ok = ok && rp_file_contains("rp_repro", "downloadable_units=4");
	ok = ok && rp_file_contains("rp_eln", "eln_entries=3");
	ok = ok && rp_file_contains("rp_wpool", "workers=4");
	ok = ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	ok = ok && rp_file_contains("rp_package", "package_manifest=ready");
	ok = ok && rp_file_contains("rp_package", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_package", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_package", "custom_sources=rp_input,rp_runner,rp_uresrun");
	ok = ok && rp_file_contains("rp_package", "workbench=rp_runner");
	ok = ok && rp_file_contains("rp_package", "workspace_imports=1");
	ok = ok && rp_file_contains("rp_package", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_package", "delivery_file=report_md;path=rp_report_text;required=1;exists=1");
	ok = ok && rp_file_contains("rp_package", "delivery_file=package_manifest;path=rp_artifact_manifest;required=1;exists=1");
	ok = ok && rp_file_contains("rp_package", "delivery_checks=3");
	ok = ok && rp_file_contains("rp_package", "delivery_check=human_review;status=pass");
	ok = ok && rp_file_contains("rp_package", "delivery_manifest_json=delivery-manifest.json");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_zip=research-evidence-bundle.zip");
	ok = ok && rp_file_contains("rp_package", "evidence_bundle_entries=12");
	ok = ok && rp_file_contains("rp_package", "bundle_files=human_reviews.json,delivery_manifests.json,revision_tasks.json,delivery-manifest.json,delivery-manifest.md");
	ok = ok && rp_file_contains("rp_package", "deliverables=8");
	ok = ok && rp_file_contains("rp_package", "raw_links=5");
	ok = ok && rp_file_contains("rp_package", "decision_controls=2");
	ok = ok && rp_file_contains("rp_package", "human_reviews=1");
	ok = ok && rp_file_contains("rp_package", "revision_tasks=1");
	ok = ok && rp_file_contains("rp_package", "revision_change_count=2");
	ok = ok && rp_file_contains("rp_package", "revision_evidence=rp_revision");
	ok = ok && rp_file_contains("rp_package", "review_action_items=2");
	ok = ok && rp_file_contains("rp_package", "llm_matched_responses=3");
	ok = ok && rp_file_contains("rp_package", "evidence_protocols=1");
	ok = ok && rp_file_contains("rp_package", "evidence_extractions=3");
	ok = ok && rp_file_contains("rp_package", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_package", "migration_steps=9");
	ok = ok && rp_file_contains("rp_runner", "revision_status=completed");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_runner", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_query", "workflow_hits=34");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_execplan", "scheduled_tasks=21");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_runner", "stages=5");
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_stage_state", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_stage_state", "command=align:agent-align");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_cache_index", "cache_policy=content_keyed");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_retry_plan", "failure_reason=tool_output_missing");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_run_events", "decision=retry_align_only");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_artifact_manifest", "real_artifact_items=5");
	ok = ok && rp_file_contains("rp_artifact_manifest", "support_entries=2");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "RUN-042 Recovery Report");
	ok = ok && rp_file_contains("rp_chart_data", "chart=stage_attempts");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_decisions=8");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_invocation", "steps=10");
	ok = ok && rp_file_contains("rp_completion", "actions=4");
	ok = ok && rp_file_contains("rp_backend", "cases=4");
	ok = ok && rp_file_contains("rp_backend_exec", "passed_cases=2");
	ok = ok && rp_file_contains("rp_study", "arms=2");
	ok = ok && rp_file_contains("rp_consistency", "state_relation=passed");
	ok = ok && rp_file_contains("rp_consistency", "task_records=21");
	ok = ok && rp_file_contains("rp_consistency", "checks=113");
	ok = ok && rp_file_contains("rp_consistency", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_consistency", "runner_stages=5");
	ok = ok && rp_file_contains("rp_consistency", "workbench_records=10");
	ok = ok && rp_file_contains("rp_consistency", "dynamic_input_records=8");
	ok = ok && rp_file_contains("rp_consistency", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_ui_home", "page=home");
	ok = ok && rp_file_contains("rp_ui_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_ui_home", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_ui_run", "page=run-detail");
	ok = ok && rp_file_contains("rp_ui_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_ui_run", "artifact_preview=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && rp_file_contains("rp_ui_run", "review_threads=2");
	ok = ok && rp_file_contains("rp_ui_run", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_ui_evidence", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_ui_agent", "page=agent-detail");
	ok = ok && rp_file_contains("rp_ui_agent", "decision_rows=8");
	ok = ok && rp_file_contains("rp_ui_evidence", "page=evidence-detail");
	ok = ok && rp_file_contains("rp_ui_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_ui_compare", "page=compare-metrics");
	ok = ok && rp_file_contains("rp_ui_compare", "metric_rows=8");
	ok = ok && rp_file_contains("rp_ui_compare", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_input", "custom_run_2=usable-run:RUN-901");
	ok = ok && rp_file_contains("rp_input", "custom_run_3=usable-run:RUN-902");
	ok = ok && rp_file_contains("rp_input", "custom_dataset_rows=3");
	ok = ok && rp_file_contains("rp_input", "form_fields=8");
	ok = ok && rp_file_contains("rp_input", "csv_rows_total=9");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_runner", "custom_source=rp_input");
	ok = ok && rp_file_contains("rp_runner", "custom_runs=3");
	ok = ok && rp_file_contains("rp_runner", "custom_agent_decisions=15");
	ok = ok && rp_file_contains("rp_runner", "citation_plan_entries=3");
	ok = ok && rp_file_contains("rp_web_routes", "routes=22");
	ok = ok && rp_file_contains("rp_web_routes", "get_routes=14");
	ok = ok && rp_file_contains("rp_web_routes", "route=/research/workbench/{id}");
	ok = ok && rp_file_contains("rp_web_routes", "post_routes=8");
	ok = ok && rp_file_contains("rp_api_home", "api=home");
	ok = ok && rp_file_contains("rp_api_home", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_api_home", "custom_runs=3");
	ok = ok && rp_file_contains("rp_api_home", "dynamic_inputs=4");
	ok = ok && rp_file_contains("rp_api_home", "reader_contract=rp_web_bundle");
	ok = ok && rp_file_contains("rp_api_home", "nav_items=12");
	ok = ok && rp_file_contains("rp_api_home", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_api_run", "runner_exec_files=5");
	ok = ok && rp_file_contains("rp_api_run", "custom_research=rp_runner");
	ok = ok && rp_file_contains("rp_api_run", "custom_research_runs=3");
	ok = ok && rp_file_contains("rp_api_run", "dynamic_input_queue=rp_input");
	ok = ok && rp_file_contains("rp_api_run", "reader_contract=rp_web_bundle");
	ok = ok && rp_file_contains("rp_api_run", "reader_view=run-detail");
	ok = ok && rp_file_contains("rp_api_run", "request_form=rp_input");
	ok = ok && rp_file_contains("rp_api_run", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_api_run", "llm_roundtrip=ready");
	ok = ok && rp_file_contains("rp_api_run", "bibliography=rp_runner");
	ok = ok && rp_file_contains("rp_api_run", "review_action_items=2");
	ok = ok && rp_file_contains("rp_api_run", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_api_run", "timeline_rows=5");
	ok = ok && rp_file_contains("rp_api_run", "dependency_checks=5");
	ok = ok && rp_file_contains("rp_api_run", "manifest_support_entries=2");
	ok = ok && rp_file_contains("rp_api_know", "evidence_protocols=1");
	ok = ok && rp_file_contains("rp_api_agents", "agents=7");
	ok = ok && rp_file_contains("rp_api_evidence", "provenance_paths=3");
	ok = ok && rp_file_contains("rp_api_evidence", "preview_files=rp_stage_log,rp_artifact,rp_artifact_manifest");
	ok = ok && rp_file_contains("rp_api_compare", "workflow_runner_files=5");
	ok = ok && rp_file_contains("rp_api_compare", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_api_artifacts", "manifest_records=4");
	ok = ok && rp_file_contains("rp_api_artifacts", "evidence_package=rp_package");
	ok = ok && rp_file_contains("rp_api_artifacts", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_api_artifacts", "llm_matched_responses=3");
	ok = ok && rp_file_contains("rp_api_artifacts", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_api_artifacts", "preview_files=rp_report_text,rp_chart_data,rp_artifact");
	ok = ok && rp_file_contains("rp_api_data", "dataset_snapshots=2");
	ok = ok && rp_file_contains("rp_api_bio", "sample_registry=rp_sreg");
	ok = ok && rp_file_contains("rp_api_labres", "instrument_registry=rp_instr");
	ok = ok && rp_file_contains("rp_api_pub", "result_review=rp_resrev");
	ok = ok && rp_file_contains("rp_api_know", "semantic_index=rp_semindex");
	ok = ok && rp_file_contains("rp_api_runtime", "runtime_env=rp_runenv");
	ok = ok && rp_file_contains("rp_api_action", "actions=8");
	ok = ok && rp_file_contains("rp_api_action", "revision_task_runner=1");
	ok = ok && rp_file_contains("rp_api_action", "validated_requests=8");
	ok = ok && rp_file_contains("rp_api_action", "precondition_checks=8");
	ok = ok && rp_file_contains("rp_api_action", "side_effect_records=16");
	ok = ok && rp_file_contains("rp_actionio", "requests=8");
	ok = ok && rp_file_contains("rp_actionio", "responses=8");
	ok = ok && rp_file_contains("rp_actionio", "completed=8");
	ok = ok && rp_file_contains("rp_actionio", "dataset_file=rp_input");
	ok = ok && rp_file_contains("rp_actionio", "generated_runs=3");
	ok = ok && rp_file_contains("rp_actionio", "tag=reusable");
	ok = ok && rp_file_contains("rp_actionio", "effect=revision_run_created");
	ok = ok && rp_file_contains("rp_actionio", "applied_changes=2");
	ok = ok && rp_file_contains("rp_actionio", "revision_status=completed");
	ok = ok && rp_file_contains("rp_uresrun", "runs=3");
	ok = ok && rp_file_contains("rp_uresrun", "run_id_3=usable-run:RUN-902");
	ok = ok && rp_file_contains("rp_uresrun", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_uresrun", "source_form=rp_input");
	ok = ok && rp_file_contains("rp_uresrun", "workbench=rp_runner");
	ok = ok && rp_file_contains("rp_uresrun", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_uresrun", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_uresrun", "artifacts=36");
	ok = ok && rp_file_contains("rp_uresrun", "dataset_rows=3");
	ok = ok && rp_file_contains("rp_uresrun", "LLM Relay");
	ok = ok && rp_file_contains("rp_actionio", "Stage DAG");
	ok = ok && rp_file_contains("rp_actionio", "passed_cases=3");
	ok = ok && rp_file_contains("rp_actionio", "action_state_records=12");
	ok = ok && rp_file_contains("rp_actionio", "action_step=workbench_advance");
	ok = ok && rp_file_contains("rp_actionio", "action_step=notebook_download");
	ok = ok && rp_file_contains("rp_actionio", "action_step=bundle_download");
	ok = ok && rp_file_contains("rp_actionio", "state_after_actions=workbench:ready,review:needs_revision,revision:completed,bundle:ready");
	ok = ok && rp_file_contains("rp_actionio", "request_validation=passed");
	ok = ok && rp_file_contains("rp_actionio", "side_effect_records=16");
	ok = ok && rp_file_contains("rp_actionio", "state_write=10;target=rp_package;field=download_manifest");
	ok = ok && rp_file_contains("rp_actionio", "idempotency_checks=8");
	ok = ok && rp_file_contains("rp_actionio", "download_manifest_generated=1");
	ok = ok && rp_file_contains("rp_web_bundle", "api_payloads=14");
	ok = ok && rp_file_contains("rp_web_bundle", "downloadable_units=3");
	ok = ok && rp_file_contains("rp_web_bundle", "static_site_pages=42");
	ok = ok && rp_file_contains("rp_web_bundle", "render_sections=7");
	ok = ok && rp_file_contains("rp_web_bundle", "artifact_previews=3");
	ok = ok && rp_file_contains("rp_web_bundle", "runner_detail_fields=16");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_manifest=rp_package");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_files=8");
	ok = ok && rp_file_contains("rp_web_bundle", "delivery_checks=3");
	ok = ok && rp_file_contains("rp_web_bundle", "evidence_bundle_entries=12");
	ok = ok && rp_file_contains("rp_web_bundle", "llm_roundtrip=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "export_bundle=rp_package");
	ok = ok && rp_file_contains("rp_web_bundle", "revision_delta=rp_revision");
	ok = ok && rp_file_contains("rp_web_bundle", "library_sources=rp_knowledge");
	ok = ok && rp_file_contains("rp_web_bundle", "workspace_imports=1");
	ok = ok && rp_file_contains("rp_web_bundle", "workbench=rp_runner");
	ok = ok && rp_file_contains("rp_web_bundle", "dynamic_inputs=4");
	ok = ok && rp_file_contains("rp_web_bundle", "host_ui_events=10");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_contract=host_plain_ucore_v2");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_ready=1");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_payload_files=rp_api_home");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_refresh_files=rp_web_routes");
	ok = ok && rp_file_contains("rp_web_bundle", "reader_required_sections=routes,payloads,actions,live_update,downloads,compare");
	ok = ok && rp_file_contains("rp_web_bundle", "evidence_protocols=1");
	ok = ok && rp_file_contains("rp_web_bundle", "workflow_portability=rp_wfio");
	ok = ok && rp_file_contains("rp_web_bundle", "coherence_checks=9");
	ok = ok && rp_file_contains("rp_web_bundle", "custom_research_files=1");
	ok = ok && rp_file_contains("rp_web_bundle", "review_threads=2");
	ok = ok && rp_file_contains("rp_web_bundle", "action_validation=passed");
	ok = ok && rp_file_contains("rp_web_bundle", "side_effect_records=16");
	if (rp_host_seed_count() > 0 &&
	    (rp_host_seed_has("kind=workbench") ||
	     rp_host_seed_has("kind=workbench_complete") ||
	     rp_host_seed_has("kind=workbench_advance") ||
	     rp_host_seed_has("kind=workbench_auto_advance") ||
	     rp_host_seed_has("kind=workbench_task") ||
	     rp_host_seed_has("kind=workbench_note") ||
	     rp_host_seed_has("kind=workbench_notes") ||
	     rp_host_seed_has("kind=workbench_handoff_package") ||
	     rp_host_seed_has("kind=workbench_readiness") ||
	     rp_host_seed_has("kind=workbench_answer") ||
	     rp_host_seed_has("kind=workbench_answer_audit") ||
	     rp_host_seed_has("kind=workbench_evidence_search") ||
	     rp_host_seed_has("kind=workbench_brief") ||
	     rp_host_seed_has("kind=workbench_evidence_dossier") ||
	     rp_host_seed_has("kind=workbench_evidence_graph") ||
	     rp_host_seed_has("kind=workbench_citations") ||
	     rp_host_seed_has("kind=workbench_manuscript") ||
	     rp_host_seed_has("kind=workbench_manuscript_audit") ||
	     rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	     rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	     rp_host_seed_has("kind=workbench_task_board") ||
	     rp_host_seed_has("kind=workbench_task_board_row") ||
	     rp_host_seed_has("kind=workbench_runbook") ||
	     rp_host_seed_has("kind=workbench_timeline") ||
	     rp_host_seed_has("kind=workbench_file_manifest") ||
	     rp_host_seed_has("kind=workbench_file_verify") ||
	     rp_host_seed_has("kind=workbench_export"))) {
		ok = ok && rp_file_contains("rp_actionio", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
		ok = ok && rp_file_contains("rp_web_bundle", "host_action_workbench_outputs=rp_runner,rp_revision,rp_package");
	}
	ok = ok && rp_file_contains("rp_tests", "tests=693");
	ok = ok && rp_file_contains("rp_tests", "workbench=passed");
	ok = ok && rp_file_contains("rp_tests", "static_site=passed");
	ok = ok && rp_file_contains("rp_tests", "workflow_portability=passed");
	ok = ok && rp_file_contains("rp_tests", "coherence=passed");
	ok = ok && rp_file_contains("rp_tests", "status=passed");
	ok = ok && rp_file_contains("rp_ack", "ack=consistency;msg=22;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=consistency.check_backend");
	ok = ok && rp_file_contains("rp_ack", "ack=ui_export;msg=ui;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=ui_export.write_compare");
	ok = ok && rp_file_contains("rp_ack", "ack=web_export;msg=web;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=web_export.write_bundle");
	ok = ok && rp_file_contains("rp_ack", "ack=api_actions;msg=action;status=ready");
	ok = ok && rp_file_contains("rp_ack", "ack=test_suite;msg=test;status=passed");
	ok = ok && rp_file_contains("rp_tool", "tool=test_suite.check_compare");
	ok = ok && rp_file_contains("rp_ack", "ack=agent_collab;msg=agents;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=agent_collab.write_decisions");
	ok = ok && rp_file_contains("rp_ack", "ack=llm_relay;msg=relay;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=llm_relay.write_packets");
	ok = ok && rp_file_contains("rp_ack", "ack=data_pipeline;msg=data;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=data_pipeline.collection");
	ok = ok && rp_file_contains("rp_ack", "ack=workflow_runner;msg=runner;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=workflow_runner.write_manifest");
	if (!ok) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 42 || tool_count < 144) {
		printf("rp_compare_plain: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		return 1;
	}
	if (!rp_append_file("rp_agentcmp", "plain_kernel=passed;programs=42;state_files=168;message_acks=42;tool_events=144;action_state_records=12;test_cases=693;action_side_effect_records=16;llm_queue_checks=3;llm_guard_checks=3;workbench_exports=7;dynamic_inputs=4;host_ui_events=10;reader_contract=1;status=ready")) return 1;
	if (rp_host_seed_has("kind=research_run")) {
		if (!rp_append_file("rp_agentcmp", "host_action_research_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		if (!rp_append_file("rp_agentcmp", "host_action_compare_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=human_review")) {
		if (!rp_append_file("rp_agentcmp", "host_action_review_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=revision_task") || rp_host_seed_has("kind=revision_run")) {
		if (!rp_append_file("rp_agentcmp", "host_action_revision_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=workbench") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_advance") ||
	    rp_host_seed_has("kind=workbench_auto_advance") ||
	    rp_host_seed_has("kind=workbench_task") ||
	    rp_host_seed_has("kind=workbench_note") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_evidence_search") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_export")) {
		if (!rp_append_file("rp_agentcmp", "host_action_workbench_verified=1")) return 1;
	}
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=notebook_export")) {
		if (!rp_append_file("rp_agentcmp", "host_action_export_verified=1")) return 1;
	}
	if (!rp_append_status("compare=ready")) return 1;
	if (rp_host_seed_count() > 0) {
		printf("rp_compare_plain: host_actions=%d verified\n", rp_host_seed_count());
	}
	printf("rp_compare_plain: plain_kernel=passed objects=500 programs=42 state_files=168 acks=42 tools=144 dynamic=4 reader=1 status=ready\n");
	return 0;
}
