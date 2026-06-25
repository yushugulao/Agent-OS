#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_write_file("rp_plan",
			   "run=RUN-042\nworkflow=lab-gene-x\nassignments=7\npolicy=minimal_rerun\nstatus=planned\n")) {
		return 1;
	}
	if (!rp_write_file("rp_mail",
			   "msg=1;from=planner;to=retriever;task=collect_literature\n"
			   "msg=2;from=planner;to=analyst;task=profile_dataset\n"
			   "msg=3;from=planner;to=reviewer;task=review_claims\n"
			   "msg=4;from=planner;to=lab;task=prepare_lab_records\n"
			   "msg=5;from=planner;to=writer;task=assemble_report\n"
			   "msg=6;from=planner;to=repair;task=repair_failed_stage\n"
			   "msg=7;from=planner;to=auditor;task=verify_release\n"
			   "msg=8;from=planner;to=evidence;task=build_evidence_path\n"
			   "msg=9;from=planner;to=llm;task=prepare_llm_packet\n"
			   "msg=10;from=planner;to=privacy;task=review_llm_packet\n"
			   "msg=11;from=planner;to=package;task=build_release_package\n"
			   "msg=12;from=planner;to=release;task=decide_release\n"
			   "msg=13;from=planner;to=dossier;task=prepare_review_material\n"
			   "msg=14;from=planner;to=metrics;task=measure_plain_kernel\n")) {
		return 1;
	}
	if (!rp_write_file("rp_sched",
			   "queue_items=14\n"
			   "ready_items=14\n"
			   "priority_high=3\n"
			   "priority_normal=11\n"
			   "retry_policy=minimal_rerun\n"
			   "deadline_model=stage_order\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_budget",
			   "run_id=RUN-042\n"
			   "token_budget=4096\n"
			   "qemu_ticks_budget=64\n"
			   "storage_files_budget=64\n"
			   "worker_slots=4\n"
			   "decision=within_budget\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_wfio",
			   "imports=2\n"
			   "exports=2\n"
			   "formats=nextflow,snakemake,plain-package\n"
			   "portable_steps=10\n"
			   "compatibility_checks=6\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_retryq",
			   "retry_item=align\n"
			   "owner=repair\n"
			   "attempt=0\n"
			   "dedupe_key=RUN-042:align\n"
			   "reason=failed_stage\n"
			   "status=pending\n")) {
		return 1;
	}
	if (!rp_write_file("rp_ack", "")) return 1;
	if (!rp_write_file("rp_tool", "")) return 1;
	if (!rp_append_file("rp_ack", "ack=planner;msg=0;status=sent")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.create_plan;target=rp_plan;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.schedule_tasks;target=rp_sched;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.assign_budget;target=rp_budget;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.workflow_io;target=rp_wfio;status=ok")) return 1;
	if (!rp_append_status("planner=planned")) return 1;
	if (!rp_append_status("mail=ready")) return 1;
	if (!rp_append_status("schedule=ready")) return 1;
	if (!rp_append_status("budget=ready")) return 1;
	if (!rp_append_status("wfio=ready")) return 1;
	printf("rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 messages=14 schedule=ready status=planned\n");
	return 0;
}
