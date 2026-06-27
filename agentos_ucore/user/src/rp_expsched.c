#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_ressched", "bookings=6");
	ok = ok && rp_file_contains("rp_labresop", "op=schedule_assess;bookings=6;status=ok");
	ok = ok && rp_file_contains("rp_instr", "sequencer=ready");
	ok = ok && rp_file_contains("rp_modelreg", "status=ready");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_provenance=observed");
	if (!ok) return 1;

	if (!rp_write_file("rp_expsched",
			   "service=experiment-scheduling\n"
			   "experiment_scheduling_checks=88\n"
			   "schedules=1\n"
			   "schedule=schedule:RUN-042:lab-execution\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "title=RUN-042 controlled lab execution schedule\n"
			   "owner=lab-ops\n"
			   "window_start_tick=80\n"
			   "window_end_tick=200\n"
			   "agentos_context=observed\n"
			   "agentos_metadata=observed\n"
			   "agentos_provenance=observed\n"
			   "status=approved\n")) {
		return 1;
	}
	if (!rp_write_file("rp_schedtask",
			   "tasks=3\n"
			   "task=schedule-task:RUN-042:verify-resources;type=resource_check;target=sop-execution:RUN-042:library-prep;assignee=auditor;status=planned;evidence=eln-record:RUN-042:library-prep,sop-execution:RUN-042:library-prep\n"
			   "task=schedule-task:RUN-042:library-prep;type=lab_operation;target=lab-op:RUN-042:library-prep;assignee=lab-tech;dependency=schedule-task:RUN-042:verify-resources;status=planned;evidence=lab-op:RUN-042:library-prep,artifact:report.md\n"
			   "task=schedule-task:RUN-042:sop-review;type=sop_review;target=sop-deviation:RUN-042:library-prep:03;assignee=qa-lead;dependency=schedule-task:RUN-042:library-prep;status=planned;evidence=sop-deviation:RUN-042:library-prep:03,eln-check:RUN-042:library-prep:seed\n"
			   "agentos_task_context=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_schedbook",
			   "bookings=4\n"
			   "booking=schedule-booking:RUN-042:seq-library;schedule=schedule:RUN-042:lab-execution;resource=instrument:seq-01;task=schedule-task:RUN-042:library-prep;start=100;end=160;status=reserved\n"
			   "booking=schedule-booking:RUN-042:lab-tech;schedule=schedule:RUN-042:lab-execution;resource=person:lab-tech;task=schedule-task:RUN-042:library-prep;start=90;end=170;status=reserved\n"
			   "booking=schedule-booking:RUN-042:qa-reviewer;schedule=schedule:RUN-042:lab-execution;resource=person:qa-lead;task=schedule-task:RUN-042:sop-review;start=170;end=200;status=reserved\n"
			   "booking=schedule-booking:RUN-042:seq-overlap-demo;schedule=schedule:RUN-042:lab-execution;resource=instrument:seq-01;task=schedule-task:RUN-042:sop-review;start=130;end=150;status=conflict\n"
			   "conflicts=1\n"
			   "agentos_booking_metadata=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_schedconf",
			   "conflicts=1\n"
			   "conflict=schedule-conflict:RUN-042:seq-01-overlap;schedule=schedule:RUN-042:lab-execution;booking=schedule-booking:RUN-042:seq-overlap-demo;resource=instrument:seq-01;severity=warning;status=detected;description=overlapping instrument request;resolution=reschedule_or_second_instrument\n"
			   "agentos_conflict_event=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_schedexec",
			   "execution_records=2\n"
			   "execution=schedule-exec:RUN-042:verify-resources;task=schedule-task:RUN-042:verify-resources;status=completed;evidence=rp_ressched,rp_labresop;notes=resources_ready\n"
			   "execution=schedule-exec:RUN-042:library-prep;task=schedule-task:RUN-042:library-prep;status=completed;evidence=rp_stage_log,rp_artifact;notes=operation_completed_after_retry\n"
			   "agentos_execution_trace=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "experiment_schedule=rp_expsched;schedule=schedule:RUN-042:lab-execution;tasks=3;bookings=4;conflicts=1;executions=2;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "experiment_schedule_page=rp_expsched;schedules=1;tasks=3;bookings=4;conflicts=1;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=experiment_schedule;source=rp_expsched;checks=88;tasks=3;conflicts=1;executions=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "experiment_scheduling_checks=88;schedules=1;tasks=3;bookings=4;conflicts=1;executions=2;charts=4;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=experiment_scheduling;msg=schedule;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.create_schedule")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.add_task")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.book_resource")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.detect_conflict")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.resolve_conflict")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.record_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.export_schedule")) return 1;
	if (!rp_append_file("rp_tool", "tool=experiment_scheduling.link_package")) return 1;
	if (!rp_append_status("experiment_scheduling=ready")) return 1;
	printf("rp_expsched: schedules=1 tasks=3 bookings=4 conflicts=1 executions=2 checks=88 status=ready\n");
	return 0;
}
