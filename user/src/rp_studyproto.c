#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_samples", "sheet=RUN-042:4");
	ok = ok && rp_file_contains("rp_protocol", "checks=5");
	ok = ok && rp_file_contains("rp_runop", "study_protocol_reproduction=packages:1");
	ok = ok && rp_file_contains("rp_runop", "study_protocol:protocols:2");
	ok = ok && rp_file_contains("rp_projectrel", "project_delivery_checks=18");
	ok = ok && rp_file_contains("rp_package", "provenance_graph=unified");
	ok = ok && rp_file_contains("rp_web_bundle", "project_delivery_service=rp_projectrel");
	if (!ok) return 1;

	if (!rp_write_file("rp_studyproto",
			   "service=study-protocols\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "study_protocol_checks=20\n"
			   "study_protocols=2\n"
			   "study_protocol_launches=2\n"
			   "study_protocol_runs=1\n"
			   "study_protocol_compliance_reports=1\n"
			   "study_protocol_bundles=1\n"
			   "study_protocol_reproduction_packages=1\n"
			   "reproduction_reviews=1\n"
			   "reproduction_action_plans=1\n"
			   "reproduction_action_executions=1\n"
			   "dataset_portfolios=1\n"
			   "source_portfolios=1\n"
			   "dataset_cards=1\n"
			   "dataset_visualizations=1\n"
			   "dataset_answers=1\n"
			   "launch=study-protocol-launch:lab-gene-x:RUN-042;protocol=variant-calling-qc;criteria=6;agents=4;status=ready\n"
			   "launch=study-protocol-launch:lab-gene-x:RUN-042-rerun;protocol=variant-calling-qc;criteria=6;agents=4;status=ready\n"
			   "rerun=study-protocol-rerun:lab-gene-x:RUN-042;source=RUN-042;result=stable;status=passed\n"
			   "comparison=study-protocol-launch-comparison:RUN-042;left=launch:RUN-042;right=launch:RUN-042-rerun;changed_metrics=0;status=passed\n"
			   "reproduction_package=study-protocol-reproduction-package:RUN-042;files=8;notebooks=2;datasets=2;status=ready\n"
			   "review=study-protocol-reproduction-review:RUN-042;decision=approved;required_actions=0;suggested_actions=2;status=ready\n"
			   "action_plan=study-protocol-reproduction-action-plan:RUN-042;steps=5;owner=recovery;status=ready\n"
			   "action_execution=study-protocol-reproduction-action-execution:RUN-042;steps_done=5;result=passed;status=ready\n"
			   "dataset_portfolio=dataset-portfolio:lab-gene-x;datasets=2;cards=1;visualizations=1;answers=1;status=ready\n"
			   "source_portfolio=source-portfolio:lab-gene-x;sources=42;reviewed=8;exports=2;status=ready\n"
			   "agentos_adaptation=file_metadata_index,context_protocol_evidence,event_reproduction_queue,batch_dataset_tool;status=planned\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_web_bundle", "study_protocol_service=rp_studyproto;checks=20;launches=2;reproduction=ready;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=study_protocols;source=rp_studyproto;launches=2;reproduction=ready;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "study_protocol_service=checks:20;protocols:2;launches:2;runs:1;reproduction:1;action_plan:1;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=studyproto;msg=study-protocol;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=study_protocol.launch")) return 1;
	if (!rp_append_file("rp_tool", "tool=study_protocol.rerun")) return 1;
	if (!rp_append_file("rp_tool", "tool=study_protocol.compare")) return 1;
	if (!rp_append_file("rp_tool", "tool=study_protocol.reproduction_package")) return 1;
	if (!rp_append_file("rp_tool", "tool=study_protocol.review_reproduction")) return 1;
	if (!rp_append_status("studyproto=ready")) return 1;

	printf("rp_studyproto: checks=20 protocols=2 launches=2 reproduction=ready status=ready\n");
	return 0;
}
