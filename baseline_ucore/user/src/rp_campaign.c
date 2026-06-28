#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_exper", "campaign=campaign:RUN-042:param-sweep");
	ok = ok && rp_file_contains("rp_realtask", "real_task_checks=96");
	ok = ok && rp_file_contains("rp_calculation", "calculation_checks=84");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	if (!ok) return 1;

	if (!rp_write_file("rp_campaign",
			   "service=experiment-campaigns\n"
			   "campaign_checks=108\n"
			   "campaign=experiment-campaign:RUN-042:align-memory-grid\n"
			   "objective=align-stage-memory-parameter-grid\n"
			   "base_run=RUN-042\n"
			   "parameter_space=memory_mb:1024,1536;threads:2,4\n"
			   "trials=4\n"
			   "primary_metric=recovered_artifacts\n"
			   "direction=maximize\n"
			   "best_trial=experiment-trial:RUN-042:align-memory-grid:04\n"
			   "owner=recovery\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_trials",
			   "campaign=experiment-campaign:RUN-042:align-memory-grid\n"
			   "trial_count=4\n"
			   "trial=experiment-trial:RUN-042:align-memory-grid:01;memory_mb=1024;threads=2;status=failed;metric=1;artifact=alignment-table-v1\n"
			   "trial=experiment-trial:RUN-042:align-memory-grid:02;memory_mb=1024;threads=4;status=finished;metric=2;artifact=alignment-table-v2\n"
			   "trial=experiment-trial:RUN-042:align-memory-grid:03;memory_mb=1536;threads=2;status=finished;metric=3;artifact=alignment-table-v3\n"
			   "trial=experiment-trial:RUN-042:align-memory-grid:04;memory_mb=1536;threads=4;status=selected;metric=4;artifact=alignment-table-v4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_camp_rank",
			   "campaign=experiment-campaign:RUN-042:align-memory-grid\n"
			   "comparison=experiment-campaign-comparison:RUN-042:align-memory-grid\n"
			   "ranked_trials=4\n"
			   "best_trial=experiment-trial:RUN-042:align-memory-grid:04\n"
			   "metric_delta=3\n"
			   "decision=select_trial_04\n"
			   "findings=memory_1536_threads_4_recovered_all_artifacts\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_resreview",
			   "review=experiment-result-review:RUN-042:baseline-vs-candidate\n"
			   "baseline=experiment-trial:RUN-042:align-memory-grid:01\n"
			   "candidate=experiment-trial:RUN-042:align-memory-grid:04\n"
			   "metric_deltas=recovered_artifacts:+3\n"
			   "parameter_changes=memory_mb:1024->1536,threads:2->4\n"
			   "artifact_changes=alignment-table-v1->alignment-table-v4\n"
			   "decision=accept_candidate\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_lineage", "campaign:align-memory-grid->trial:experiment-trial:RUN-042:align-memory-grid:04")) return 1;
	if (!rp_append_file("rp_package", "experiment_campaign_package=rp_campaign;best_trial=experiment-trial:RUN-042:align-memory-grid:04;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "experiment_campaigns_page=rp_campaign;campaigns=1;trials=4;best_trial=04;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=experiment_campaigns;source=rp_campaign;campaigns=1;trials=4;checks=108;outcome=passed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "experiment_campaign_checks=108;campaigns=1;trials=4;best_trial=04;result_review=accept_candidate;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=experiment_campaign;msg=campaign;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.create")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.expand_grid")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.materialize_trial_01")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.materialize_trial_02")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.materialize_trial_03")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.materialize_trial_04")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.rank_trials")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.review_result")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.export_report")) return 1;
	if (!rp_append_file("rp_tool", "tool=campaign.package")) return 1;
	if (!rp_append_status("experiment_campaign=ready")) return 1;
	printf("rp_campaign: campaigns=1 trials=4 best=04 checks=108 result_review=accept_candidate status=ready\n");
	return 0;
}
