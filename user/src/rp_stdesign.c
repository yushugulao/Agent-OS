#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_samples", "sheet=RUN-042:4");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_checks=20");
	ok = ok && rp_file_contains("rp_evidence", "claims=8");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	if (!ok) return 1;

	if (!rp_write_file("rp_stdesign",
			   "service=statistical-design\n"
			   "statistical_design_checks=120\n"
			   "design=stat-design:lab-gene-x:run042-primary\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "study=study:lab-gene-x-treatment-response\n"
			   "hypothesis=hypothesis:RUN-042:align-memory\n"
			   "primary_endpoint=candidate_genes_mean\n"
			   "comparison=treatment_vs_control\n"
			   "effect_size=1.25\n"
			   "alpha=0.05\n"
			   "target_power=0.80\n"
			   "allocation_ratio=1.00\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_power",
			   "analysis=power-analysis:lab-gene-x:run042-primary\n"
			   "design=stat-design:lab-gene-x:run042-primary\n"
			   "method=two_sample_normal_approximation\n"
			   "required_per_group=11\n"
			   "required_total=22\n"
			   "actual_min_group_size=2\n"
			   "achieved_power=0.239\n"
			   "status=underpowered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_random",
			   "randomization=randomization:lab-gene-x:run042-primary\n"
			   "design=stat-design:lab-gene-x:run042-primary\n"
			   "arms=control,treatment\n"
			   "strata=batch\n"
			   "seed=RUN-042-deterministic\n"
			   "assignments=4\n"
			   "assignment=S-001:control\n"
			   "assignment=S-002:treatment\n"
			   "assignment=S-003:control\n"
			   "assignment=S-004:treatment\n"
			   "balance=arms:control:2,treatment:2\n"
			   "status=balanced\n")) {
		return 1;
	}
	if (!rp_write_file("rp_blind",
			   "blinding=blinding-check:lab-gene-x:run042-primary\n"
			   "design=stat-design:lab-gene-x:run042-primary\n"
			   "blinded_roles=reporter,auditor,statistician\n"
			   "unblinded_roles=lab-operator\n"
			   "leaks=0\n"
			   "status=ok\n")) {
		return 1;
	}
	if (!rp_write_file("rp_streview",
			   "review=stat-design-review:lab-gene-x:run042-primary\n"
			   "design=stat-design:lab-gene-x:run042-primary\n"
			   "reviewer=methodologist\n"
			   "stat_result=approved_with_sample_size_note\n"
			   "finding=current sample count is below planned power target\n"
			   "evidence_ids=5\n"
			   "export=stat-design-export:lab-gene-x:run042-primary\n"
			   "export_type=markdown\n"
			   "checksum=stdesign-md-042\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "statistical_design=rp_stdesign;stat_result=approved_with_sample_size_note;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "statistical_design_page=rp_stdesign;designs=1;power=underpowered;randomization=balanced;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=statistical_design;source=rp_stdesign;checks=120;stat_result=approved_with_sample_size_note;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "statistical_design_checks=120;designs=1;power=underpowered;randomization=balanced;blinding=ok;stat_result=approved_with_sample_size_note;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=statistical_design;msg=design;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.create")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.analyze_power")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.create_randomization")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.check_blinding")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.review")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.export_markdown")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.link_package")) return 1;
	if (!rp_append_file("rp_tool", "tool=stat_design.publish_reader")) return 1;
	if (!rp_append_status("statistical_design=ready")) return 1;
	printf("rp_stdesign: designs=1 power=underpowered randomization=balanced blinding=ok checks=120 stat_result=approved_with_sample_size_note status=ready\n");
	return 0;
}
