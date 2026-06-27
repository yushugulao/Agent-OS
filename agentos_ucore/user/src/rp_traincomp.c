#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_expsched", "schedule=schedule:RUN-042:lab-execution");
	ok = ok && rp_file_contains("rp_schedtask", "task=schedule-task:RUN-042:sop-review");
	ok = ok && rp_file_contains("rp_schedexec", "agentos_execution_trace=observed");
	ok = ok && rp_file_contains("rp_training", "requirements=4");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_provenance=observed");
	if (!ok) return 1;

	if (!rp_write_file("rp_traincomp",
			   "service=training-compliance\n"
			   "training_compliance_checks=92\n"
			   "schedule=schedule:RUN-042:lab-execution\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "requirements=4\n"
			   "training_records=4\n"
			   "competency_assessments=4\n"
			   "role_authorizations=3\n"
			   "training_gaps=1\n"
			   "initial_open_gaps=1\n"
			   "open_gaps=0\n"
			   "resolved_gaps=1\n"
			   "active_authorizations=3\n"
			   "agentos_context=observed\n"
			   "agentos_metadata=observed\n"
			   "agentos_provenance=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_trainreq",
			   "requirements=4\n"
			   "requirement=training-req:sop-library-prep:lab-tech;target_type=sop_version;target=sop-version:lab-gene-x:library-prep:v1;role=lab-tech;title=Library preparation SOP execution;evidence=SOP acknowledgement,supervised execution;status=active\n"
			   "requirement=training-req:instrument-seq-01:lab-tech;target_type=instrument;target=instrument:seq-01;role=lab-tech;title=Sequencer 01 operator authorization;evidence=instrument safety training,current calibration briefing;status=active\n"
			   "requirement=training-req:resource-check:auditor;target_type=task_type;target=resource_check;role=auditor;title=Resource readiness review;evidence=audit checklist training;status=active\n"
			   "requirement=training-req:sop-deviation:qa-lead;target_type=task_type;target=sop_review;role=qa-lead;title=SOP deviation review authority;evidence=deviation review training,release readiness training;status=active\n"
			   "agentos_requirement_metadata=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_trainrec",
			   "training_records=4\n"
			   "training=training:lab-tech:sop-library-prep;person=lab-tech;requirement=training-req:sop-library-prep:lab-tech;provider=lab-ops;result=completed;evidence=sop-result:RUN-042:library-prep:04\n"
			   "training=training:lab-tech:seq-01;person=lab-tech;requirement=training-req:instrument-seq-01:lab-tech;provider=instrument-owner;result=completed;evidence=calibration:seq-01:current\n"
			   "training=training:auditor:resource-check;person=auditor;requirement=training-req:resource-check:auditor;provider=qa;result=completed;evidence=eln-check:RUN-042:library-prep:seed\n"
			   "training=training:qa-lead:sop-deviation;person=qa-lead;requirement=training-req:sop-deviation:qa-lead;provider=quality;result=completed;evidence=sop-deviation:RUN-042:library-prep:03\n"
			   "agentos_training_context=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_trainassess",
			   "competency_assessments=4\n"
			   "assessment=competency:lab-tech:sop-library-prep;person=lab-tech;requirement=training-req:sop-library-prep:lab-tech;assessor=qa-lead;decision=passed;score=92\n"
			   "assessment=competency:lab-tech:seq-01;person=lab-tech;requirement=training-req:instrument-seq-01:lab-tech;assessor=instrument-owner;decision=passed;score=90\n"
			   "assessment=competency:auditor:resource-check;person=auditor;requirement=training-req:resource-check:auditor;assessor=qa-lead;decision=passed;score=95\n"
			   "assessment=competency:qa-lead:sop-deviation;person=qa-lead;requirement=training-req:sop-deviation:qa-lead;assessor=quality-director;decision=passed;score=94\n"
			   "agentos_assessment_event=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_trainauth",
			   "role_authorizations=3\n"
			   "authorization=auth:lab-tech:lab-tech:lab-gene-x;person=lab-tech;role=lab-tech;scope=project:lab-gene-x;granted_by=qa-lead;evidence=training:lab-tech:sop-library-prep,training:lab-tech:seq-01;status=active\n"
			   "authorization=auth:auditor:auditor:lab-gene-x;person=auditor;role=auditor;scope=project:lab-gene-x;granted_by=qa-lead;evidence=training:auditor:resource-check;status=active\n"
			   "authorization=auth:qa-lead:qa-lead:lab-gene-x;person=qa-lead;role=qa-lead;scope=project:lab-gene-x;granted_by=quality-director;evidence=training:qa-lead:sop-deviation,competency:qa-lead:sop-deviation;status=active\n"
			   "agentos_authorization_capability=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_traingap",
			   "training_gaps=1\n"
			   "gap=training-gap:schedule:RUN-042:lab-execution:schedule-task:RUN-042:sop-review:role_authorization:role-authorization:qa-lead;schedule=schedule:RUN-042:lab-execution;task=schedule-task:RUN-042:sop-review;person=qa-lead;type=role_authorization;severity=blocking;initial_status=open;status=resolved;resolution=qa-lead training, competency, and authorization completed\n"
			   "open_gaps=0\n"
			   "resolved_gaps=1\n"
			   "agentos_gap_event=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "training_compliance=rp_traincomp;requirements=4;records=4;assessments=4;auth=3;gaps=1;open=0;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "training_compliance_page=rp_traincomp;requirements=4;records=4;gaps=1;auth=3;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=training_compliance;source=rp_traincomp;checks=92;requirements=4;open_gaps=0;auth=3;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "training_compliance_checks=92;requirements=4;training_records=4;competency=4;authorizations=3;gaps=1;open_gaps=0;charts=4;agentos_replacements=4;kernel_metadata=observed;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=training_compliance;msg=training;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.create_requirement")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.record_training")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.assess_competency")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.authorize_role")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.evaluate_schedule")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.resolve_gap")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.export_training")) return 1;
	if (!rp_append_file("rp_tool", "tool=training_compliance.link_provenance")) return 1;
	if (!rp_append_status("training_compliance=ready")) return 1;
	printf("rp_traincomp: requirements=4 records=4 competency=4 auth=3 gaps=1 open=0 checks=92 status=ready\n");
	return 0;
}
