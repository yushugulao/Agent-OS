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
			   "msg=14;from=planner;to=metrics;task=measure_plain_kernel\n"
			   "msg=15;from=planner;to=governance;task=register_risk_capa\n"
			   "msg=16;from=planner;to=delta;task=review_release_delta\n"
			   "msg=17;from=planner;to=execobs;task=observe_execution\n"
			   "msg=18;from=planner;to=runconf;task=validate_run_configuration\n"
			   "msg=19;from=planner;to=invoke;task=record_workflow_invocation\n"
			   "msg=20;from=planner;to=completion;task=process_workflow_completion\n"
			   "msg=21;from=planner;to=backend;task=prepare_backend_scenario\n")) {
		return 1;
	}
	if (!rp_write_file("rp_sched",
			   "queue_items=21\n"
			   "ready_items=21\n"
			   "priority_high=4\n"
			   "priority_normal=17\n"
			   "retry_policy=minimal_rerun\n"
			   "deadline_model=stage_order\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_taskrec",
			   "msg=1;owner=retriever;stage=literature;prio=H;class=critical;state=ready\n"
			   "msg=2;owner=analyst;stage=profile;prio=H;class=critical;state=ready\n"
			   "msg=3;owner=reviewer;stage=claims;prio=N;class=review;state=ready\n"
			   "msg=4;owner=lab;stage=records;prio=N;class=lab;state=ready\n"
			   "msg=5;owner=writer;stage=report;prio=N;class=paper;state=ready\n"
			   "msg=6;owner=repair;stage=align;prio=H;class=critical;state=ready\n"
			   "msg=7;owner=auditor;stage=release;prio=N;class=review;state=ready\n"
			   "msg=8;owner=evidence;stage=path;prio=N;class=knowledge;state=ready\n"
			   "msg=9;owner=llm;stage=packet;prio=N;class=llm;state=ready\n"
			   "msg=10;owner=privacy;stage=packet_check;prio=N;class=llm;state=ready\n"
			   "msg=11;owner=package;stage=package;prio=N;class=paper;state=ready\n"
			   "msg=12;owner=release;stage=decision;prio=N;class=critical;state=ready\n"
			   "msg=13;owner=dossier;stage=material;prio=N;class=paper;state=ready\n"
			   "msg=14;owner=metrics;stage=measure;prio=N;class=telemetry;state=ready\n"
			   "msg=15;owner=governance;stage=risk_capa;prio=N;class=review;state=ready\n"
			   "msg=16;owner=delta;stage=release_delta;prio=N;class=release;state=ready\n"
			   "msg=17;owner=execobs;stage=execution_trace;prio=N;class=telemetry;state=ready\n"
			   "msg=18;owner=runconf;stage=run_configuration;prio=N;class=config;state=ready\n"
			   "msg=19;owner=invoke;stage=workflow_invocation;prio=H;class=execution;state=ready\n"
			   "msg=20;owner=completion;stage=completion_hooks;prio=N;class=execution;state=ready\n"
			   "msg=21;owner=backend;stage=backend_scenario;prio=N;class=compare;state=ready\n")) {
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
	if (!rp_write_file("rp_policy",
			   "run_id=RUN-042\n"
			   "access_profiles=4\n"
			   "data_use_rules=5\n"
			   "llm_outbound_rules=4\n"
			   "license_checks=2\n"
			   "retention=release_package_only\n"
			   "decision=ready\n"
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
	if (!rp_append_file("rp_tool", "tool=planner.create_plan")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.schedule_tasks")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.write_taskrec")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.assign_budget")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.workflow_io")) return 1;
	if (!rp_append_file("rp_tool", "tool=planner.write_policy")) return 1;
	if (!rp_append_status("planner=planned")) return 1;
	if (!rp_append_status("mail=ready")) return 1;
	if (!rp_append_status("schedule=ready")) return 1;
	if (!rp_append_status("taskrec=ready")) return 1;
	if (!rp_append_status("budget=ready")) return 1;
	if (!rp_append_status("wfio=ready")) return 1;
	if (!rp_append_status("policy=ready")) return 1;
	printf("rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 messages=21 schedule=ready status=planned\n");
	return 0;
}
