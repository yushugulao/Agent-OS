#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_figrec", "figures=3");
	ok = ok && rp_file_contains("rp_calculation", "calculation_checks=84");
	ok = ok && rp_file_contains("rp_realtask", "answer_audit=pass");
	if (!ok) return 1;

	if (!rp_write_file("rp_analysisres",
			   "service=analysis-results\n"
			   "analysis_results_checks=96\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "analysis_plans=1\n"
			   "analysis_runs=2\n"
			   "result_tables=2\n"
			   "statistical_results=2\n"
			   "figures=2\n"
			   "interpretations=2\n"
			   "manual_run=analysis-run:RUN-042:manual\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_anplan",
			   "analysis_plans=1\n"
			   "plan=analysis-plan:RUN-042:treatment-response;project=lab-gene-x;run=RUN-042;study=study:lab-gene-x-treatment-response;method=difference_of_means_demo;inputs=assay:RUN-042:alignment-qc,assay:RUN-042:expression-screen,sample-sheet:RUN-042;status=planned\n"
			   "objective=Estimate treatment response after alignment recovery and summarize report-ready evidence.\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_anrun",
			   "analysis_runs=2\n"
			   "run=analysis-run:RUN-042:treatment-response;plan=analysis-plan:RUN-042:treatment-response;engine=userland-statistics;parameters=group_field:condition,metric:candidate_genes;outputs=result-table:RUN-042:gene-summary,stat-result:RUN-042:treatment-vs-control,figure:RUN-042:treatment-response,interpretation:RUN-042:treatment-response;status=completed\n"
			   "run=analysis-run:RUN-042:manual;plan=analysis-plan:RUN-042:treatment-response;engine=manual-engine;parameters=metric:mapped_percent;outputs=result-table:manual,stat-result:manual,figure:manual,interpretation:manual;status=completed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_resulttbl",
			   "result_tables=2\n"
			   "table=result-table:RUN-042:gene-summary;run=analysis-run:RUN-042:treatment-response;title=Gene Candidate Summary;columns=condition,sample_count,candidate_genes_mean,qc_review_count;rows=2;summary=control_mean:4,treatment_mean:18,reviewed_qc_samples:3;artifact=artifact:report.md;status=ready\n"
			   "table=result-table:manual;run=analysis-run:RUN-042:manual;title=Manual QC Summary;columns=condition,mapped_percent;rows=2;summary=control:92,treatment:88;artifact=artifact:report.md;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_statres",
			   "statistical_results=2\n"
			   "stat=stat-result:RUN-042:treatment-vs-control;run=analysis-run:RUN-042:treatment-response;contrast=treatment_vs_control;method=difference_of_means_demo;statistic=14.0;p_value=0.031;effect_size=3.5;status=significant\n"
			   "stat=stat-result:manual;run=analysis-run:RUN-042:manual;contrast=manual_control_vs_treatment;method=difference;statistic=4.0;p_value=0.08;effect_size=1.2;status=reported\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_anfig",
			   "figures=2\n"
			   "figure=figure:RUN-042:treatment-response;run=analysis-run:RUN-042:treatment-response;title=Treatment Response Summary;type=bar;source=result-table:RUN-042:gene-summary;artifact=artifact:report.md;status=ready\n"
			   "figure=figure:manual;run=analysis-run:RUN-042:manual;title=Manual QC Figure;type=bar;source=result-table:manual;artifact=artifact:report.md;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_interp",
			   "interpretations=2\n"
			   "interpretation=interpretation:RUN-042:treatment-response;run=analysis-run:RUN-042:treatment-response;author=reporter;status=ready_for_report;evidence=result-table:RUN-042:gene-summary,stat-result:RUN-042:treatment-vs-control,figure:RUN-042:treatment-response,lab-operation-validation:2,ethics-validation:1;conclusion=Treatment samples show higher candidate gene counts after the recovered alignment workflow; QC review remains required for batch B3.\n"
			   "interpretation=interpretation:manual;run=analysis-run:RUN-042:manual;author=reporter;status=ready_for_report;evidence=result-table:manual,stat-result:manual,figure:manual;conclusion=Manual QC analysis is ready for review.\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "analysis_results=rp_analysisres;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "analysis_results_page=rp_analysisres;runs=2;tables=2;statistics=2;figures=2;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=analysis_results;source=rp_analysisres;checks=96;runs=2;statistics=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "analysis_results_checks=96;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;charts=4;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=analysis_results;msg=analysis;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.create_plan")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.start_run")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.finish_run")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.record_table")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.record_statistical_result")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.record_figure")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.add_interpretation")) return 1;
	if (!rp_append_file("rp_tool", "tool=analysis_results.export_review")) return 1;
	if (!rp_append_status("analysis_results=ready")) return 1;
	printf("rp_analysisres: plans=1 runs=2 tables=2 statistics=2 figures=2 interpretations=2 checks=96 status=ready\n");
	return 0;
}
