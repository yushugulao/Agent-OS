#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_lineage", "edges=7")) return 1;
	if (!rp_write_file("rp_site",
			   "site=static-review\n"
			   "page=overview;source=rp_objects\n"
			   "page=run;source=rp_runview\n"
			   "page=agents;source=rp_agent_run\n"
			   "page=evidence;source=rp_evidence\n"
			   "page=artifacts;source=rp_artifact_manifest\n"
			   "page=data;source=rp_dataset_collection\n"
			   "page=bio;source=rp_sreg\n"
			   "page=lab_resources;source=rp_instr\n"
			   "page=publication;source=rp_resrev\n"
			   "page=knowledge;source=rp_knowledge\n"
			   "page=runtime;source=rp_runenv\n"
			   "page=compare;source=rp_agentcmp\n"
			   "page=workflow_templates;source=rp_wfio\n"
			   "page=workflow_invocations;source=rp_invocation\n"
			   "page=workflow_comparisons;source=rp_backend\n"
			   "page=workflow_completion;source=rp_completion\n"
			   "page=workflow_portability;source=rp_wfio\n"
			   "page=execution_cache;source=rp_cache_index\n"
			   "page=execution_control;source=rp_execplan\n"
			   "page=worker_operations;source=rp_worker\n"
			   "page=resource_budget;source=rp_budget\n"
			   "page=run_telemetry;source=rp_telemetry\n"
			   "page=backend_scenarios;source=rp_backend_exec\n"
			   "page=runbooks;source=rp_retry_plan\n"
			   "page=notebooks;source=rp_nbexec\n"
			   "page=samples;source=rp_samples\n"
			   "page=cohort;source=rp_cohort\n"
			   "page=studies;source=rp_protocol\n"
			   "page=ethics;source=rp_ethics\n"
			   "page=lab_operations;source=rp_labops\n"
			   "page=analysis_results;source=rp_compute\n"
			   "page=visualizations;source=rp_figrec\n"
			   "page=fair_data;source=rp_fairpkg\n"
			   "page=provenance;source=rp_provpath\n"
			   "page=review_threads;source=rp_review2\n"
			   "page=delivery;source=rp_package\n"
			   "page=llm_relay;source=rp_llm_packets\n"
			   "page=privacy;source=rp_privacy\n"
			   "page=release;source=rp_release\n"
			   "page=dossier;source=rp_dossier\n"
			   "page=agentos_readiness;source=rp_consistency\n"
			   "page=downloads;source=rp_package\n"
			   "pages=42\n"
			   "primary_pages=12\n"
			   "workflow_pages=8\n"
			   "research_service_pages=10\n"
			   "review_page_count=6\n"
			   "ops_page_count=6\n"
			   "json_payloads=14\n"
			   "route_records=21\n"
			   "download_links=8\n"
			   "preview_pages=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("site_export=ready")) return 1;
	printf("rp_site_export: pages=42 status=ready\n");
	return 0;
}
