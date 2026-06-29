#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

static void copy_study_value(const char *kind, const char *key, const char *fallback, char *out, int cap)
{
	if (!rp_host_seed_copy_value_for_kind(kind, key, out, cap)) {
		rp_copy_text(out, cap, fallback);
	}
}

static int append_study_host_actions(void)
{
	char value[96];
	char line[768];

	if (!rp_host_seed_has_study_protocol_action()) return 1;
	if (!rp_append_file("rp_actionio", "host_action_study_protocol=1")) return 0;
	if (!rp_append_file("rp_web_bundle", "host_action_study_protocol=rp_studyproto,rp_usablepack,rp_review_dashboard")) return 0;
	rp_copy_text(line, sizeof(line), "host_action_study_protocol=applied;");
	if (rp_host_seed_has("kind=study_protocol")) {
		copy_study_value("kind=study_protocol", "protocol_id=", "usable-study-protocol:variant-calling-qc", value, sizeof(value));
		rp_append_text(line, sizeof(line), "protocol=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
		copy_study_value("kind=study_protocol", "title=", "Variant calling QC", value, sizeof(value));
		rp_append_text(line, sizeof(line), "title=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_run")) {
		copy_study_value("kind=study_protocol_run", "run_id=", "usable-study-protocol-run:RUN-042", value, sizeof(value));
		rp_append_text(line, sizeof(line), "protocol_run=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_compliance")) {
		copy_study_value("kind=study_protocol_compliance", "decision=", "pass", value, sizeof(value));
		rp_append_text(line, sizeof(line), "compliance=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_bundle")) {
		copy_study_value("kind=study_protocol_bundle", "bundle=", "study-protocol-bundle.zip", value, sizeof(value));
		rp_append_text(line, sizeof(line), "bundle=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_launch")) {
		copy_study_value("kind=study_protocol_launch", "launch_id=", "study-protocol-launch:RUN-042", value, sizeof(value));
		rp_append_text(line, sizeof(line), "launch=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_launch_rerun")) {
		copy_study_value("kind=study_protocol_launch_rerun", "rerun_id=", "study-protocol-rerun:RUN-042", value, sizeof(value));
		rp_append_text(line, sizeof(line), "rerun=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_launch_comparison")) {
		copy_study_value("kind=study_protocol_launch_comparison", "changed_metrics=", "0", value, sizeof(value));
		rp_append_text(line, sizeof(line), "comparison_changed_metrics=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_reproduction_package")) {
		copy_study_value("kind=study_protocol_reproduction_package", "package_id=", "study-protocol-reproduction-package:RUN-042", value, sizeof(value));
		rp_append_text(line, sizeof(line), "reproduction_package=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_reproduction_package_review")) {
		copy_study_value("kind=study_protocol_reproduction_package_review", "decision=", "approved", value, sizeof(value));
		rp_append_text(line, sizeof(line), "reproduction_review=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_reproduction_package_action_plan")) {
		copy_study_value("kind=study_protocol_reproduction_package_action_plan", "steps=", "5", value, sizeof(value));
		rp_append_text(line, sizeof(line), "action_plan_steps=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	if (rp_host_seed_has("kind=study_protocol_reproduction_package_action_execute")) {
		copy_study_value("kind=study_protocol_reproduction_package_action_execute", "result=", "passed", value, sizeof(value));
		rp_append_text(line, sizeof(line), "action_execute_result=");
		rp_append_text(line, sizeof(line), value);
		rp_append_text(line, sizeof(line), ";");
	}
	rp_append_text(line, sizeof(line), "status=ready");
	if (!rp_append_file("rp_studyproto", line)) return 0;
	if (!rp_append_file("rp_usablepack", line)) return 0;
	if (!rp_append_file("rp_review_dashboard", line)) return 0;
	return 1;
}

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
	if (!append_study_host_actions()) return 1;
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
