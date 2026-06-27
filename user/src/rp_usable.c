#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_realtask", "answer_audit=pass");
	ok = ok && rp_file_contains("rp_analysisres", "analysis_results_checks=96");
	ok = ok && rp_file_contains("rp_decsupport", "recommended_option=agentos_ucore_hybrid");
	if (!ok) return 1;

	if (!rp_write_file("rp_usable",
			   "service=usable-research\n"
			   "usable_research_checks=100\n"
			   "entry=research-question-to-review-package\n"
			   "project=usable-project:lab-gene-x-final-demo\n"
			   "run_id=usable-run:RUN-900\n"
			   "title=Lab Gene X final demonstration workbench\n"
			   "templates=3\n"
			   "datasets=3\n"
			   "library_sources=3\n"
			   "dag_stages=9\n"
			   "stage_edges=10\n"
			   "workbench_tasks=9\n"
			   "plan_queue_rows=4\n"
			   "action_queue_rows=5\n"
			   "handoff_packages=3\n"
			   "deliverables=8\n"
			   "readiness=ready_with_notes\n"
			   "next_action=delivery_manifest\n"
			   "host_parity=tracked\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usabletpl",
			   "templates=3\n"
			   "template=usable-template:workspace-900;name=Reusable response comparison;question=Compare recovered workflow evidence and prepare a reviewer package.;tags=reusable,workflow,agent;status=ready\n"
			   "template=usable-template:dataset-answer;name=Dataset answer with audit;question=Analyze an uploaded table and cite each answer.;tags=dataset,answer,audit;status=ready\n"
			   "template=usable-template:study-protocol;name=Study protocol launch;question=Create a protocol, run it, and package reproduction evidence.;tags=protocol,reproduction;status=ready\n"
			   "selected_template=usable-template:workspace-900\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usableds",
			   "datasets=3\n"
			   "dataset=usable-dataset:response-table;rows=3;columns=4;tags=reusable,response;quality=pass;preview=ready;status=ready\n"
			   "dataset=usable-dataset:penguins;rows=344;columns=8;tags=real-task,morphometrics;quality=accepted;preview=ready;status=ready\n"
			   "dataset=usable-dataset:alignment-qc;rows=4;columns=5;tags=workflow,qc;quality=pass;preview=ready;status=ready\n"
			   "visualizations=2\n"
			   "dataset_answers=2\n"
			   "dataset_runs=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablelib",
			   "library_sources=3\n"
			   "source=usable-source:library2026:1;title=Agent workflow provenance;kind=reference;tags=agent,provenance;status=ready\n"
			   "source=usable-source:dataset-methods:1;title=Dataset audit methods;kind=method-note;tags=analysis,audit;status=ready\n"
			   "source=usable-source:handoff-checklist:1;title=Reviewer handoff checklist;kind=checklist;tags=review,package;status=ready\n"
			   "literature_searches=2\n"
			   "evidence_protocols=2\n"
			   "evidence_synthesis=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usabledag",
			   "dag=usable-research-dag\n"
			   "stage=intake;order=1;depends=none;artifact=rp_input;agent=orchestrator;status=done\n"
			   "stage=plan;order=2;depends=intake;artifact=rp_plan;agent=orchestrator;status=done\n"
			   "stage=retrieve;order=3;depends=plan;artifact=rp_knowledge;agent=retriever;status=done\n"
			   "stage=validate;order=4;depends=intake;artifact=rp_data_quality;agent=analyst;status=done\n"
			   "stage=analyze;order=5;depends=validate;artifact=rp_analysisres;agent=analyst;status=done\n"
			   "stage=draft;order=6;depends=retrieve,analyze;artifact=rp_report_text;agent=writer;status=done\n"
			   "stage=audit;order=7;depends=draft;artifact=rp_review_dashboard;agent=auditor;status=done\n"
			   "stage=review;order=8;depends=audit;artifact=rp_review_pack;agent=reviewer;status=accepted\n"
			   "stage=package;order=9;depends=review;artifact=rp_package;agent=orchestrator;status=ready\n"
			   "cache_hits=1\n"
			   "retry_items=1\n"
			   "llm_provider=template\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usableops",
			   "operations=usable-research-workbench\n"
			   "queue=workbench_action;rows=5;ready=2;needs_action=2;optional=1;status=ready\n"
			   "queue=workbench_plan;rows=4;open=2;waiting=1;done=1;status=ready\n"
			   "handoff=usable-handoff:RUN-900:reviewer;files=8;required_missing=0;decision=ready;status=ready\n"
			   "handoff=usable-handoff:RUN-900:auditor;files=6;required_missing=0;decision=ready;status=ready\n"
			   "handoff=usable-handoff:RUN-900:operator;files=5;required_missing=1;decision=ready_with_notes;status=ready\n"
			   "export=workbench-runbook;format=markdown;commands=6;status=ready\n"
			   "export=workbench-timeline;format=html;events=8;status=ready\n"
			   "export=file-manifest;files=9;sha_records=9;verified=9;missing=0;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "usable_research=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;deliverables=8;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "usable_research_page=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;queues=2;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=usable_research;source=rp_usable;checks=100;handoff=ready;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "usable_research_checks=100;templates=3;datasets=3;library_sources=3;dag_stages=9;queues=2;handoffs=3;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=usable_research;msg=workbench_entry;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.create_workbench")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.add_dataset")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.add_library_source")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.run_dag")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.answer_question")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.audit_answer")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.update_plan_queue")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.export_handoff")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.verify_manifest")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_research.package_delivery")) return 1;
	if (!rp_append_status("usable_research=ready")) return 1;
	printf("rp_usable: templates=3 datasets=3 library=3 stages=9 queues=2 handoffs=3 checks=100 status=ready\n");
	return 0;
}
