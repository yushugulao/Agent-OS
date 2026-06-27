#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_runop", "model_registry:2");
	ok = ok && rp_file_contains("rp_status", "relay=ready");
	ok = ok && rp_file_contains("rp_privacy", "decision=accepted");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	if (!ok) return 1;

	if (!rp_write_file("rp_modelreg",
			   "service=model-registry\n"
			   "model_registry_service_checks=96\n"
			   "registered_models=1\n"
			   "model=registered-model:agent-triage-template\n"
			   "name=agent-triage-template\n"
			   "model_type=template-agent\n"
			   "task=scientific workflow triage and report drafting\n"
			   "owner=wang\n"
			   "tags=agent,triage,research\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_modelver",
			   "version=model-version:agent-triage-template:v1\n"
			   "model=registered-model:agent-triage-template\n"
			   "version_label=v1\n"
			   "source_type=model_card\n"
			   "source_id=model-card:agent-triage-template:v1\n"
			   "training_run=RUN-042\n"
			   "artifact_ids=rp_report_text,rp_package,rp_llm_packets\n"
			   "metric_artifact_count=52\n"
			   "metric_prompt_eval_score=0.875\n"
			   "status=staged\n")) {
		return 1;
	}
	if (!rp_write_file("rp_modeleval",
			   "evaluation=model-evaluation:agent-triage-template:v1:RUN-042\n"
			   "version=model-version:agent-triage-template:v1\n"
			   "dataset_snapshot=dataset-snapshot:RUN-042:quality\n"
			   "evaluator=offline-evaluator\n"
			   "metric_evidence_coverage=1.000\n"
			   "metric_report_status_ok=1.000\n"
			   "metric_prompt_eval_score=0.875\n"
			   "outputs=report:run-042-recovery-report:v1\n"
			   "status=passed\n")) {
		return 1;
	}
	if (!rp_write_file("rp_modeldep",
			   "deployment=model-deployment:agent-triage-template:v1:template\n"
			   "version=model-version:agent-triage-template:v1\n"
			   "target_environment=template\n"
			   "policy=offline_review_candidate\n"
			   "check_model_card=ok\n"
			   "check_evaluation=ok\n"
			   "check_provider=ok\n"
			   "check_secret_policy=not_required\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_modelserve",
			   "serving_check=model-serving-check:agent-triage-template:v1:template\n"
			   "deployment=model-deployment:agent-triage-template:v1:template\n"
			   "provider=template\n"
			   "latency_ms=12\n"
			   "message=offline provider ready\n"
			   "status=ok\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "model_registry=rp_modelreg;version=v1;evaluation=passed;deployment=ready;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "model_registry_page=rp_modelreg;models=1;versions=1;evaluations=1;deployments=1;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=model_registry;source=rp_modelreg;checks=96;evaluation=passed;deployment=ready;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "model_registry_service_checks=96;models=1;versions=1;evaluations=1;deployments=1;serving_checks=1;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=model_registry;msg=model;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.register_model")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.create_version")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.evaluate_version")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.create_deployment")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.check_serving")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.link_package")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.publish_reader")) return 1;
	if (!rp_append_file("rp_tool", "tool=model_registry.compare_agentos")) return 1;
	if (!rp_append_status("model_registry=ready")) return 1;
	printf("rp_modelreg: models=1 versions=1 evaluations=1 deployments=1 serving=1 checks=96 status=ready\n");
	return 0;
}
