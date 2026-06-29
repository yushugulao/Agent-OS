#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_data_quality", "decision=accepted");
	ok = ok && rp_file_contains("rp_data_transform", "transform=normalize_fastq");
	ok = ok && rp_file_contains("rp_dataset_collection", "collection=lab-gene-x-run042-analysis");
	ok = ok && rp_file_contains("rp_package", "artifacts=52");
	if (!ok) return 1;

	if (!rp_write_file("rp_calculation",
			   "service=calculation\n"
			   "run_id=RUN-042\n"
			   "project=lab-gene-x\n"
			   "calculation_checks=84\n"
			   "computers=1\n"
			   "codes=1\n"
			   "jobs=1\n"
			   "submissions=1\n"
			   "retrieved_files=3\n"
			   "parser_results=1\n"
			   "exports=1\n"
			   "computer=calculation-computer:local-agentos;label=local-agentos-workdir;hostname=localhost;scheduler=direct;transport=local;status=active\n"
			   "code=calculation-code:metadata-qc:v1;computer=calculation-computer:local-agentos;entry=agent_platform.calculations:metadata_qc;parser=metadata-qc-parser;version=1.0;status=active\n"
			   "job=calculation-job:lab-gene-x:run042-qc;process_type=aiida.calculations:agent.metadata_qc;state=finished;exit_status=0;code=calculation-code:metadata-qc:v1;computer=calculation-computer:local-agentos\n"
			   "job_inputs=dataset-collection:lab-gene-x:run042-analysis,data-transform-run:lab-gene-x:run042-normalize\n"
			   "scheduler_record=calculation-submission:run042-qc;status=finished;attempts=1;command=metadata-qc\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_calc_files",
			   "job=calculation-job:lab-gene-x:run042-qc\n"
			   "retrieved_files=3\n"
			   "retrieved=calculation-retrieved:run042-qc:stdout-txt;path=stdout.txt;kind=retrieved_output;checksum=stdout042;status=available\n"
			   "retrieved=calculation-retrieved:run042-qc:results-json;path=results.json;kind=retrieved_output;checksum=results042;status=available\n"
			   "retrieved=calculation-retrieved:run042-qc:provenance-json;path=provenance.json;kind=provenance_manifest;checksum=prov042;status=available\n"
			   "output_snapshot=dataset-snapshot:calculation:run042-qc;rows=3;files=3;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_calc_parse",
			   "job=calculation-job:lab-gene-x:run042-qc\n"
			   "parser_result=calculation-parser-result:run042-qc;parser=metadata-qc-parser;status=ok;output_snapshot=dataset-snapshot:calculation:run042-qc\n"
			   "metric=input_count;value=2;status=ready\n"
			   "metric=collection_items;value=4;status=ready\n"
			   "metric=ready_ratio;value=1.00;status=ready\n"
			   "warnings=0\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_calc_export",
			   "job=calculation-job:lab-gene-x:run042-qc\n"
			   "export=calculation-export:lab-gene-x:run042-qc;type=markdown;path=calculation-export-run042-qc.md;checksum=calcexport042;status=ready\n"
			   "package=calculation-package:lab-gene-x:run042-qc;files=3;parser_results=1;exports=1;status=ready\n"
			   "reader_page=calculations.html\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_lineage", "calculation:run042-qc->artifact:RUN-042:alignment-table")) return 1;
	if (!rp_append_file("rp_package", "calculation_package=rp_calculation;job=calculation-job:lab-gene-x:run042-qc;retrieved=3;parser=ok;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "calculations_page=rp_calculation;jobs=1;retrieved=3;parser_results=1;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=calculations;source=rp_calculation;jobs=1;retrieved=3;checks=84;outcome=passed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "calculation_checks=84;computers=1;codes=1;jobs=1;retrieved=3;parser_results=1;exports=1;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=calculation;msg=calc;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.register_computer")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.register_code")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.submit_job")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.run_job")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.retrieve_files")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.parse_results")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.export")) return 1;
	if (!rp_append_file("rp_tool", "tool=calculation.package")) return 1;
	if (!rp_append_status("calculation=ready")) return 1;
	printf("rp_calculation: computers=1 codes=1 jobs=1 retrieved=3 parser=1 exports=1 checks=84 errors=0 status=ready\n");
	return 0;
}
