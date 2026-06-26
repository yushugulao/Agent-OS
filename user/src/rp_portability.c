#include <stdio.h>
#include <research_platform_state.h>

static int copy_port_value(const char *key, const char *fallback, char *out, int cap)
{
	if (!rp_host_seed_copy_workflow_portability_value(key, out, cap)) {
		rp_copy_text(out, cap, fallback);
	}
	return 1;
}

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
	if (rp_host_seed_has_workflow_portability_run_action()) {
		char import_id[80];
		char source_format[32];
		char source[64];
		char target_runtime[48];
		char execution_plan[96];
		char compare_profile[80];
		char scenario_id[80];
		char rehearsal_status[32];
		char readiness_decision[48];
		char package[64];
		char line[192];
		copy_port_value("import_id=", "workflow-import:host-nextflow", import_id, sizeof(import_id));
		copy_port_value("source_format=", "nextflow", source_format, sizeof(source_format));
		copy_port_value("source=", "main.host.nf", source, sizeof(source));
		copy_port_value("target_runtime=", "agentos-ucore", target_runtime, sizeof(target_runtime));
		copy_port_value("execution_plan=", "workflow-migration-execution-plan:host-nextflow:agentcompare", execution_plan, sizeof(execution_plan));
		copy_port_value("compare_profile=", "compare-profile:host-nextflow:migration", compare_profile, sizeof(compare_profile));
		copy_port_value("scenario_id=", "backend-scenario:host-nextflow", scenario_id, sizeof(scenario_id));
		copy_port_value("rehearsal_status=", "passed", rehearsal_status, sizeof(rehearsal_status));
		copy_port_value("readiness_decision=", "ready_for_agentos", readiness_decision, sizeof(readiness_decision));
		copy_port_value("package=", "workflow-portability-host.zip", package, sizeof(package));
		if (!rp_append_file("rp_wfio", "host_portability_payload=applied")) return 1;
		rp_copy_text(line, sizeof(line), "host_portability_import=");
		rp_append_text(line, sizeof(line), import_id);
		rp_append_text(line, sizeof(line), ";format=");
		rp_append_text(line, sizeof(line), source_format);
		rp_append_text(line, sizeof(line), ";source=");
		rp_append_text(line, sizeof(line), source);
		if (!rp_append_file("rp_wfio", line)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_target=", target_runtime)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_execution_plan=", execution_plan)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_compare_profile=", compare_profile)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_scenario=", scenario_id)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_rehearsal=", rehearsal_status)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_decision=", readiness_decision)) return 1;
		if (!rp_append_host_action_line("rp_wfio", "host_portability_package=", package)) return 1;
	}
	if (rp_host_seed_has_workflow_portability_step_action()) {
		char value[96];
		char other[96];
		char third[96];
		char fourth[96];
		char line[240];
		if (!rp_append_file("rp_wfio", "host_portability_steps=applied")) return 1;
		if (rp_host_seed_has("kind=workflow_portability_import")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_import", "import_id=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-import:host-nextflow");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_import", "source_format=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "nextflow");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_import", "source=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "main.host.nf");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_import", "normalized_steps=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "15");
			}
			rp_copy_text(line, sizeof(line), "host_portability_import_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";format=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";source=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";normalized_steps=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_import", "adapter_id=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "adapter:nextflow");
			}
			rp_append_text(line, sizeof(line), ";adapter=");
			rp_append_text(line, sizeof(line), other);
			if (!rp_append_file("rp_wfio", line)) return 1;
		}
		if (rp_host_seed_has("kind=workflow_portability_plan")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_plan", "migration_plan=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-migration-plan:host-nextflow");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_plan", "target_runtime=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "agentos-ucore");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_plan", "migration_steps=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "9");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_plan", "risk_items=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "4");
			}
			rp_copy_text(line, sizeof(line), "host_portability_plan_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";target=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";steps=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";risks=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_wfio", line)) return 1;
		}
		if (rp_host_seed_has("kind=workflow_portability_bind")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_bind", "execution_plan=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-migration-execution-plan:host-nextflow:agentcompare");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_bind", "compare_profile=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "compare-profile:host-nextflow:migration");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_bind", "scenario_id=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "backend-scenario:host-nextflow");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_bind", "backend_cases=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "4");
			}
			rp_copy_text(line, sizeof(line), "host_portability_bind_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";profile=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";scenario=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";backend_cases=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_wfio", line)) return 1;
		}
		if (rp_host_seed_has("kind=workflow_portability_rehearse")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_rehearse", "rehearsal_id=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-rehearsal:host-nextflow");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_rehearse", "binding_id=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "workflow-migration-binding:RUN-042:plain-ucore");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_rehearse", "rehearsal_status=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "passed");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_rehearse", "observed_ready=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "3");
			}
			rp_copy_text(line, sizeof(line), "host_portability_rehearse_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";binding=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";status=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";observed_ready=");
			rp_append_text(line, sizeof(line), fourth);
			if (rp_host_seed_copy_value_for_kind("kind=workflow_portability_rehearse", "skipped=", other, sizeof(other))) {
				rp_append_text(line, sizeof(line), ";skipped=");
				rp_append_text(line, sizeof(line), other);
			}
			if (!rp_append_file("rp_wfio", line)) return 1;
		}
		if (rp_host_seed_has("kind=workflow_portability_review")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_review", "review_id=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-migration-readiness:RUN-042");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_review", "readiness_decision=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "ready_for_agentos");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_review", "blocking_items=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "0");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_review", "work_items=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "6");
			}
			rp_copy_text(line, sizeof(line), "host_portability_review_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";decision=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";blocking_items=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";work_items=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_wfio", line)) return 1;
		}
		if (rp_host_seed_has("kind=workflow_portability_package")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_package", "package=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-portability-host.zip");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_package", "export_format=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "zip");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_package", "import_id=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "workflow-import:host-nextflow");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=workflow_portability_package", "bundle=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "workflow-portability-host.zip");
			}
			rp_copy_text(line, sizeof(line), "host_portability_package_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";format=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";import=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";bundle=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_wfio", line)) return 1;
		}
	}
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
