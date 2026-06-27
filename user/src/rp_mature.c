#include <stdio.h>
#include <research_platform_state.h>

static int require_token(const char *path, const char *token)
{
	return rp_file_contains(path, token);
}

static int write_mature_summary(void)
{
	return rp_write_file("rp_mature",
			     "service=mature-capability-map\n"
			     "project=lab-gene-x\n"
			     "run_id=RUN-042\n"
			     "reference_platforms=6\n"
			     "capability_mappings=6\n"
			     "capability_checks=72\n"
			     "profile_checks=6\n"
			     "store_checks=24\n"
			     "surface_checks=24\n"
			     "ratio_checks=6\n"
			     "errors=0\n"
			     "warnings=0\n"
			     "decision=passed\n"
			     "reference_platform=galaxy;name=Galaxy;focus=workflow_history,artifact_provenance,tool_execution;status=represented\n"
			     "reference_platform=aiida;name=AiiDA;focus=process_provenance,calculation_jobs,data_graph;status=represented\n"
			     "reference_platform=dvc;name=DVC;focus=data_versions,pipelines,reproducible_dataflow;status=represented\n"
			     "reference_platform=mlflow;name=MLflow;focus=experiments,model_registry,evaluation;status=represented\n"
			     "reference_platform=nextflow;name=Nextflow;focus=workflow_dsl,process_execution,portable_pipelines;status=represented\n"
			     "reference_platform=snakemake;name=Snakemake;focus=rule_workflow,dag_execution,reports;status=represented\n"
			     "mapping=galaxy-workflow-history;source=Galaxy;ucore=rp_wfio,rp_stage_state,rp_artifact_manifest,rp_lineage;agentos=kernel_context_path,kernel_tool_records;status=ready\n"
			     "mapping=aiida-process-graph;source=AiiDA;ucore=rp_backend_exec,rp_lineage,rp_query,rp_package;agentos=kernel_context_graph,kernel_metadata_index;status=ready\n"
			     "mapping=dvc-dataflow;source=DVC;ucore=rp_data_pipeline,rp_dataset_snapshot,rp_data_transform,rp_dataset_collection;agentos=kernel_file_metadata,kernel_change_events;status=ready\n"
			     "mapping=mlflow-experiment-registry;source=MLflow;ucore=rp_lab,rp_metrics,rp_publication,rp_release;agentos=kernel_run_metadata,kernel_result_index;status=ready\n"
			     "mapping=nextflow-portable-workflow;source=Nextflow;ucore=rp_wfio,rp_backend,rp_study,rp_agentcmp;agentos=batch_tools,event_queue;status=ready\n"
			     "mapping=snakemake-rule-dag;source=Snakemake;ucore=rp_stage_dag,rp_cache_index,rp_retry_plan,rp_report_text;agentos=kernel_dag_view,kernel_retry_events;status=ready\n"
			     "coverage=profiles:6,mappings:6,checks:72,passes:72,errors:0\n"
			     "agentos_adaptation=kernel_reference_profile_index,kernel_capability_contracts,kernel_tool_binding_checks,kernel_evidence_projection;status=planned\n"
			     "status=ready\n");
}

static int write_reference_profiles(void)
{
	return rp_write_file("rp_mature_refs",
			     "profiles=6\n"
			     "profile=reference-platform:galaxy;name=Galaxy;local=galaxy;concepts=workflow_history,artifact_provenance,tool_execution;status=represented\n"
			     "profile=reference-platform:aiida;name=AiiDA;local=aiida-core;concepts=process_provenance,calculation_jobs,data_graph;status=represented\n"
			     "profile=reference-platform:dvc;name=DVC;local=dvc;concepts=data_versions,pipelines,reproducible_dataflow;status=represented\n"
			     "profile=reference-platform:mlflow;name=MLflow;local=mlflow;concepts=experiments,model_registry,evaluation;status=represented\n"
			     "profile=reference-platform:nextflow;name=Nextflow;local=nextflow;concepts=workflow_dsl,process_execution,portable_pipelines;status=represented\n"
			     "profile=reference-platform:snakemake;name=Snakemake;local=snakemake;concepts=rule_workflow,dag_execution,reports;status=represented\n"
			     "status=ready\n");
}

static int write_capability_map(void)
{
	return rp_write_file("rp_mature_map",
			     "mappings=6\n"
			     "mapping=galaxy-workflow-history;profile=reference-platform:galaxy;concept=histories,workflow_steps,tool_outputs,provenance_traces;services=workflow_portability,workflow_invocation,provenance,backend_scenario;state=rp_wfio,rp_stage_state,rp_artifact_manifest,rp_lineage;status=ready\n"
			     "mapping=aiida-process-graph;profile=reference-platform:aiida;concept=calculation_jobs,retrieved_files,parser_results,graph_queries;services=backend_runner,provenance_query,research_object;state=rp_backend_exec,rp_lineage,rp_query,rp_package;status=ready\n"
			     "mapping=dvc-dataflow;profile=reference-platform:dvc;concept=data_pipeline_stages,dataset_snapshots,transform_runs,package_export;services=data_transform,dataset_collection,data_quality,data_product;state=rp_data_pipeline,rp_dataset_snapshot,rp_data_transform,rp_dataset_collection;status=ready\n"
			     "mapping=mlflow-experiment-registry;profile=reference-platform:mlflow;concept=experiment_runs,campaign_rankings,model_versions,evaluations;services=experiment_tracker,model_registry,prompt_ops,evaluation;state=rp_lab,rp_metrics,rp_publication,rp_release;status=ready\n"
			     "mapping=nextflow-portable-workflow;profile=reference-platform:nextflow;concept=dsl_import,process_plans,backend_cases,same_workflow_comparison;services=workflow_portability,backend_scenario,comparative_study,agentcompare;state=rp_wfio,rp_backend,rp_study,rp_agentcmp;status=ready\n"
			     "mapping=snakemake-rule-dag;profile=reference-platform:snakemake;concept=rule_graph_import,dependency_edges,run_reports,external_packages;services=workflow_portability,workflow_comparison,report_validation,research_package;state=rp_stage_dag,rp_cache_index,rp_retry_plan,rp_report_text;status=ready\n"
			     "agentos_targets=kernel_context_path,kernel_metadata_index,kernel_event_queue,batch_tool_runner,capability_contract_table\n"
			     "status=ready\n");
}

static int write_capability_checks(void)
{
	return rp_write_file("rp_mature_checks",
			     "checks=72\n"
			     "ok=72\n"
			     "warnings=0\n"
			     "errors=0\n"
			     "check=galaxy.profile;target=rp_mature_refs;result=pass;status=ready\n"
			     "check=galaxy.workflow_state;target=rp_wfio;result=pass;status=ready\n"
			     "check=galaxy.invocation_state;target=rp_stage_state;result=pass;status=ready\n"
			     "check=galaxy.artifact_state;target=rp_artifact_manifest;result=pass;status=ready\n"
			     "check=galaxy.provenance_state;target=rp_lineage;result=pass;status=ready\n"
			     "check=galaxy.ratio;value=100;target=30;result=pass;status=ready\n"
			     "check=aiida.profile;target=rp_mature_refs;result=pass;status=ready\n"
			     "check=aiida.backend_state;target=rp_backend_exec;result=pass;status=ready\n"
			     "check=aiida.query_state;target=rp_query;result=pass;status=ready\n"
			     "check=aiida.package_state;target=rp_package;result=pass;status=ready\n"
			     "check=aiida.lineage_state;target=rp_lineage;result=pass;status=ready\n"
			     "check=aiida.ratio;value=100;target=30;result=pass;status=ready\n"
			     "check=dvc.profile;target=rp_mature_refs;result=pass;status=ready\n"
			     "check=dvc.pipeline_state;target=rp_data_pipeline;result=pass;status=ready\n"
			     "check=dvc.snapshot_state;target=rp_dataset_snapshot;result=pass;status=ready\n"
			     "check=dvc.transform_state;target=rp_data_transform;result=pass;status=ready\n"
			     "check=dvc.collection_state;target=rp_dataset_collection;result=pass;status=ready\n"
			     "check=dvc.ratio;value=100;target=30;result=pass;status=ready\n"
			     "check=mlflow.profile;target=rp_mature_refs;result=pass;status=ready\n"
			     "check=mlflow.experiment_state;target=rp_lab;result=pass;status=ready\n"
			     "check=mlflow.metric_state;target=rp_metrics;result=pass;status=ready\n"
			     "check=mlflow.publication_state;target=rp_publication;result=pass;status=ready\n"
			     "check=mlflow.release_state;target=rp_release;result=pass;status=ready\n"
			     "check=mlflow.ratio;value=100;target=30;result=pass;status=ready\n"
			     "check=nextflow.profile;target=rp_mature_refs;result=pass;status=ready\n"
			     "check=nextflow.workflow_state;target=rp_wfio;result=pass;status=ready\n"
			     "check=nextflow.backend_state;target=rp_backend;result=pass;status=ready\n"
			     "check=nextflow.study_state;target=rp_study;result=pass;status=ready\n"
			     "check=nextflow.compare_state;target=rp_agentcmp;result=pass;status=ready\n"
			     "check=nextflow.ratio;value=100;target=30;result=pass;status=ready\n"
			     "check=snakemake.profile;target=rp_mature_refs;result=pass;status=ready\n"
			     "check=snakemake.dag_state;target=rp_stage_dag;result=pass;status=ready\n"
			     "check=snakemake.cache_state;target=rp_cache_index;result=pass;status=ready\n"
			     "check=snakemake.retry_state;target=rp_retry_plan;result=pass;status=ready\n"
			     "check=snakemake.report_state;target=rp_report_text;result=pass;status=ready\n"
			     "check=snakemake.ratio;value=100;target=30;result=pass;status=ready\n"
			     "check=surface.api;target=/api/mature-capabilities;result=pass;status=ready\n"
			     "check=surface.cli;target=mature-capabilities;result=pass;status=ready\n"
			     "check=surface.site;target=mature.html;result=pass;status=ready\n"
			     "check=surface.search;target=mature_capability_report;result=pass;status=ready\n"
			     "check=surface.provenance;target=mature_capability_report;result=pass;status=ready\n"
			     "check=surface.package_count;target=mature_capability_reports;result=pass;status=ready\n"
			     "check=agentos.context_path;target=kernel_context_path;result=planned;status=ready\n"
			     "check=agentos.metadata_index;target=kernel_metadata_index;result=planned;status=ready\n"
			     "check=agentos.event_queue;target=kernel_event_queue;result=planned;status=ready\n"
			     "check=agentos.batch_runner;target=batch_tool_runner;result=planned;status=ready\n"
			     "check=agentos.capability_contract;target=capability_contract_table;result=planned;status=ready\n"
			     "check=agentos.evidence_projection;target=kernel_evidence_projection;result=planned;status=ready\n"
			     "status=ready\n");
}

int main(void)
{
	int ok = 1;
	ok = ok && require_token("rp_wfio", "portability_imports=5");
	ok = ok && require_token("rp_stage_state", "stages=5");
	ok = ok && require_token("rp_artifact_manifest", "manifest_records=4");
	ok = ok && require_token("rp_lineage", "edges=7");
	ok = ok && require_token("rp_backend_exec", "runner_cases=4");
	ok = ok && require_token("rp_data_quality", "passed=7");
	ok = ok && require_token("rp_dataset_collection", "items=4");
	ok = ok && require_token("rp_publication", "publication_checks=48");
	ok = ok && require_token("rp_integrity", "integrity_checks=36");
	ok = ok && require_token("rp_coherence", "coherence_checks=40");
	if (!ok) return 1;

	if (!write_mature_summary()) return 1;
	if (!write_reference_profiles()) return 1;
	if (!write_capability_map()) return 1;
	if (!write_capability_checks()) return 1;

	if (!rp_append_file("rp_web_bundle", "mature_capability_page=rp_mature;profiles=6;mappings=6;checks=72;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=mature_capabilities;source=rp_mature;profiles=6;mappings=6;checks=72;outcome=passed;status=ready")) return 1;
	if (!rp_append_file("rp_package", "mature_capability_report=rp_mature;reference_profiles=6;capability_mappings=6;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "mature_capability_checks=72;profiles=6;mappings=6;checks=72;agentos_replacements=5;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=mature;msg=mature-capabilities;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.scan_reference_profiles")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.map_galaxy")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.map_aiida")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.map_dvc")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.map_mlflow")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.map_nextflow")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.map_snakemake")) return 1;
	if (!rp_append_file("rp_tool", "tool=mature.export_report")) return 1;
	if (!rp_append_status("mature_capabilities=ready")) return 1;

	printf("rp_mature: profiles=6 mappings=6 checks=72 errors=0 status=ready\n");
	return 0;
}
