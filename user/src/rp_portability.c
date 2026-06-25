#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_wfio", "portable_steps=10")) return 1;
	if (!rp_file_contains("rp_wfio", "compatibility_checks=6")) return 1;

	if (!rp_append_file("rp_wfio", "portability_imports=5")) return 1;
	if (!rp_append_file("rp_wfio", "format=snakemake;source=Snakefile.lab-gene-x;rules=5;normalized=1")) return 1;
	if (!rp_append_file("rp_wfio", "format=galaxy;source=workflow-import_lab-gene-x-galaxy.ga;steps=5;normalized=1")) return 1;
	if (!rp_append_file("rp_wfio", "format=dvc;source=dvc.lab-gene-x.yaml;stages=5;normalized=1")) return 1;
	if (!rp_append_file("rp_wfio", "format=cwl;source=workflow.lab-gene-x.cwl;steps=5;normalized=1")) return 1;
	if (!rp_append_file("rp_wfio", "format=nextflow;source=main.lab-gene-x.nf;processes=5;normalized=1")) return 1;
	if (!rp_append_file("rp_wfio", "normalized_steps=15")) return 1;
	if (!rp_append_file("rp_wfio", "shared_run_id=RUN-042")) return 1;
	if (!rp_append_file("rp_wfio", "adapter_specs=6")) return 1;
	if (!rp_append_file("rp_wfio", "adapter_reports=6")) return 1;
	if (!rp_append_file("rp_wfio", "unsupported_steps=0")) return 1;
	if (!rp_append_file("rp_wfio", "plans=3")) return 1;
	if (!rp_append_file("rp_wfio", "plan=plain-ucore-to-agentos;target=agentos-ucore;steps=3;status=planned")) return 1;
	if (!rp_append_file("rp_wfio", "plan=nextflow-to-agentos;target=agentos-ucore;steps=3;status=planned")) return 1;
	if (!rp_append_file("rp_wfio", "plan=galaxy-to-agentos;target=agentos-ucore;steps=3;status=planned")) return 1;
	if (!rp_append_file("rp_wfio", "migration_steps=9")) return 1;
	if (!rp_append_file("rp_wfio", "work_items=6")) return 1;
	if (!rp_append_file("rp_wfio", "tool_mappings=8")) return 1;
	if (!rp_append_file("rp_wfio", "risk_items=4")) return 1;
	if (!rp_append_file("rp_wfio", "rehearsals=2")) return 1;
	if (!rp_append_file("rp_wfio", "binding=workflow-migration-binding:RUN-042:plain-ucore")) return 1;
	if (!rp_append_file("rp_wfio", "case=plain-ucore;status=passed;runner=rp_workflow_runner")) return 1;
	if (!rp_append_file("rp_wfio", "case=snakemake-export;status=passed;runner=host-adapter")) return 1;
	if (!rp_append_file("rp_wfio", "case=nextflow-export;status=passed;runner=host-adapter")) return 1;
	if (!rp_append_file("rp_wfio", "case=galaxy-export;status=manual-review;runner=host-adapter")) return 1;
	if (!rp_append_file("rp_wfio", "cases=4")) return 1;
	if (!rp_append_file("rp_wfio", "passed_cases=3")) return 1;
	if (!rp_append_file("rp_wfio", "manual_review_cases=1")) return 1;
	if (!rp_append_file("rp_wfio", "review=workflow-migration-readiness:RUN-042")) return 1;
	if (!rp_append_file("rp_wfio", "source_formats=5")) return 1;
	if (!rp_append_file("rp_wfio", "rehearsal_cases=4")) return 1;
	if (!rp_append_file("rp_wfio", "same_run_id=RUN-042")) return 1;
	if (!rp_append_file("rp_wfio", "blocking_items=0")) return 1;
	if (!rp_append_file("rp_wfio", "decision=ready_for_agentos")) return 1;
	if (!rp_append_file("rp_wfio", "package=workflow-portability")) return 1;
	if (!rp_append_file("rp_ack", "ack=portability;msg=wf;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=portability.plan_migration;target=rp_wfio;status=ok")) return 1;
	if (!rp_append_status("portability=ready")) return 1;
	if (!rp_append_status("adapters=ready")) return 1;
	if (!rp_append_status("migration=ready")) return 1;
	if (!rp_append_status("port_rehearsal=ready")) return 1;
	if (!rp_append_status("port_review=ready")) return 1;
	printf("rp_portability: imports=5 adapters=6 migration_steps=9 rehearsals=2 status=ready\n");
	return 0;
}
