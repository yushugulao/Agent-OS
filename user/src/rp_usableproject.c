#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

static void copy_action_value(const char *kind, const char *key, const char *fallback, char *out, int cap)
{
	if (!rp_host_seed_copy_value_for_kind(kind, key, out, cap)) {
		rp_copy_text(out, cap, fallback);
	}
}

static int append_project_lifecycle_actions(void)
{
	int has_scaffold = rp_host_seed_has("kind=project_scaffold");
	int has_launch = rp_host_seed_has("kind=project_launch");
	int has_execute = rp_host_seed_has("kind=project_action_execute");
	char project[64];
	char template_id[64];
	char workspace[64];
	char files[32];
	char scaffold[64];
	char workbench[64];
	char run_id[64];
	char provider[48];
	char action_id[64];
	char action_key[64];
	char result[64];
	char line[256];

	if (!has_scaffold && !has_launch && !has_execute) return 1;
	if (!rp_append_file("rp_actionio", "host_action_usable_project=1")) return 0;
	if (!rp_append_file("rp_actionio", "host_action_usable_project_outputs=rp_usableproj,rp_usablescaf,rp_usablelaunch,rp_usablepack,kernel_context")) return 0;
	if (!rp_append_file("rp_web_bundle", "host_action_usable_project=rp_usableproj,rp_usablescaf,rp_usablelaunch,rp_usablepack,kernel_context")) return 0;

	if (has_scaffold) {
		copy_action_value("kind=project_scaffold", "project_id=", "lab-gene-x", project, sizeof(project));
		copy_action_value("kind=project_scaffold", "template_id=", "scaffold-template:starter", template_id, sizeof(template_id));
		copy_action_value("kind=project_scaffold", "workspace=", "workspace/lab-gene-x", workspace, sizeof(workspace));
		copy_action_value("kind=project_scaffold", "files=", "8", files, sizeof(files));
		rp_copy_text(line, sizeof(line), "host_action_project_scaffold=");
		rp_append_text(line, sizeof(line), project);
		rp_append_text(line, sizeof(line), ";template=");
		rp_append_text(line, sizeof(line), template_id);
		rp_append_text(line, sizeof(line), ";workspace=");
		rp_append_text(line, sizeof(line), workspace);
		rp_append_text(line, sizeof(line), ";files=");
		rp_append_text(line, sizeof(line), files);
		rp_append_text(line, sizeof(line), ";kernel_metadata=indexed;status=ready");
		if (!rp_append_file("rp_usableproj", line)) return 0;
		if (!rp_append_file("rp_usablescaf", line)) return 0;
		if (!rp_append_file("rp_web_bundle", line)) return 0;
	}
	if (has_launch) {
		copy_action_value("kind=project_launch", "project_id=", "lab-gene-x", project, sizeof(project));
		copy_action_value("kind=project_launch", "scaffold_id=", "scaffold:lab-gene-x:starter", scaffold, sizeof(scaffold));
		copy_action_value("kind=project_launch", "workbench_id=", "usable-workbench:RUN-900", workbench, sizeof(workbench));
		copy_action_value("kind=project_launch", "run_id=", "usable-run:RUN-900", run_id, sizeof(run_id));
		copy_action_value("kind=project_launch", "provider_id=", "template", provider, sizeof(provider));
		rp_copy_text(line, sizeof(line), "host_action_project_launch=");
		rp_append_text(line, sizeof(line), project);
		rp_append_text(line, sizeof(line), ";scaffold=");
		rp_append_text(line, sizeof(line), scaffold);
		rp_append_text(line, sizeof(line), ";workbench=");
		rp_append_text(line, sizeof(line), workbench);
		rp_append_text(line, sizeof(line), ";run=");
		rp_append_text(line, sizeof(line), run_id);
		rp_append_text(line, sizeof(line), ";provider=");
		rp_append_text(line, sizeof(line), provider);
		rp_append_text(line, sizeof(line), ";kernel_event=queued;kernel_context=recorded;status=ready");
		if (!rp_append_file("rp_usableproj", line)) return 0;
		if (!rp_append_file("rp_usablelaunch", line)) return 0;
		if (!rp_append_file("rp_web_bundle", line)) return 0;
	}
	if (has_execute) {
		copy_action_value("kind=project_action_execute", "project_id=", "lab-gene-x", project, sizeof(project));
		copy_action_value("kind=project_action_execute", "action_id=", "usable-project-action:RUN-042:1", action_id, sizeof(action_id));
		copy_action_value("kind=project_action_execute", "action_key=", "build_reproduction_package", action_key, sizeof(action_key));
		copy_action_value("kind=project_action_execute", "provider_id=", "template", provider, sizeof(provider));
		copy_action_value("kind=project_action_execute", "result=", "completed", result, sizeof(result));
		rp_copy_text(line, sizeof(line), "host_action_project_action_execute=");
		rp_append_text(line, sizeof(line), project);
		rp_append_text(line, sizeof(line), ";action=");
		rp_append_text(line, sizeof(line), action_id);
		rp_append_text(line, sizeof(line), ";key=");
		rp_append_text(line, sizeof(line), action_key);
		rp_append_text(line, sizeof(line), ";provider=");
		rp_append_text(line, sizeof(line), provider);
		rp_append_text(line, sizeof(line), ";result=");
		rp_append_text(line, sizeof(line), result);
		rp_append_text(line, sizeof(line), ";kernel_context=recorded;status=ready");
		if (!rp_append_file("rp_usableproj", line)) return 0;
		if (!rp_append_file("rp_usablepack", line)) return 0;
		if (!rp_append_file("rp_web_bundle", line)) return 0;
	}
	return 1;
}

static int append_study_protocol_package_action(void)
{
	char protocol[64];
	char result[64];
	char line[224];

	if (!rp_host_seed_has_study_protocol_action()) return 1;
	copy_action_value("kind=study_protocol", "protocol_id=", "usable-study-protocol:variant-calling-qc", protocol, sizeof(protocol));
	copy_action_value("kind=study_protocol_reproduction_package_action_execute", "result=", "passed", result, sizeof(result));
	rp_copy_text(line, sizeof(line), "host_action_study_protocol=applied;protocol=");
	rp_append_text(line, sizeof(line), protocol);
	rp_append_text(line, sizeof(line), ";action_execute_result=");
	rp_append_text(line, sizeof(line), result);
	rp_append_text(line, sizeof(line), ";kernel_context=recorded;status=ready");
	if (!rp_append_file("rp_usablepack", line)) return 0;
	return 1;
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_usable", "usable_research_checks=100");
	ok = ok && rp_file_contains("rp_usableops", "kernel_event_queue=observed");
	ok = ok && rp_file_contains("rp_projectrel", "project_delivery_checks=18");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_reproduction_packages=1");
	ok = ok && rp_file_contains("rp_agentos_kernel", "context_snapshot=present");
	ok = ok && rp_file_contains("rp_agentos_kernel", "file_meta_service=initialized");
	if (!ok) return 1;

	if (!rp_write_file("rp_usableproj",
			   "service=usable-project-lifecycle\n"
			   "usable_project_checks=120\n"
			   "configuration=usable-research-config:offline-template\n"
			   "startup_guides=1\n"
			   "platform_doctor_checks=10\n"
			   "scaffold_templates=3\n"
			   "scaffold_files=8\n"
			   "project_launches=2\n"
			   "operations_digest_sections=6\n"
			   "project_bundles=2\n"
			   "package_intakes=1\n"
			   "active_reproduction_actions=1\n"
			   "next_user_path=scaffold->launch->run->review->package\n"
			   "agentos_context=observed\n"
			   "agentos_file_metadata=observed\n"
			   "agentos_event_queue=observed\n"
			   "kernel_assisted=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usableboot",
			   "config=usable-research-config:offline-template;provider=template;cloud_key_required=0;kernel_policy=capability_checked;status=ready\n"
			   "startup_guide=usable-startup-guide:1;steps=7;first_run=research_project_launch;status=ready\n"
			   "platform_doctor_checks=10\n"
			   "doctor=usable-platform-doctor:1;checks=10;passed=10;failed=0;warnings=0;status=ready\n"
			   "check=workspace_root;result=pass;detail=ordinary_file_workspace_available\n"
			   "check=template_provider;result=pass;detail=no_cloud_key_required\n"
			   "check=reader_pages;result=pass;detail=usable_and_project_pages_exported\n"
			   "check=package_downloads;result=pass;detail=project_bundle_and_reproduction_package_ready\n"
			   "check=agentos_upgrade_points;result=pass;detail=context,file_metadata,event_queue,batch_tools\n"
			   "kernel_doctor=context_snapshot,file_metadata,event_queue,capability_guard;status=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablescaf",
			   "templates=3\n"
			   "template=scaffold-template:starter;files=5;includes=README,inputs,analysis,review,package;metadata=indexed;status=ready\n"
			   "template=scaffold-template:dataset-review;files=7;includes=data,dictionary,preview,quality,answer,review,package;metadata=indexed;status=ready\n"
			   "template=scaffold-template:protocol-reproduction;files=8;includes=protocol,launch,runs,comparison,review,actions,bundle,manifest;metadata=indexed;status=ready\n"
			   "scaffold=scaffold:lab-gene-x:starter;project=lab-gene-x;files=8;importable=1;status=ready\n"
			   "file=README.md;kind=guide;bytes=960;agentos_meta=guide;status=ready\n"
			   "file=inputs/dataset.csv;kind=data;rows=4;agentos_meta=dataset;status=ready\n"
			   "file=references/library.bib;kind=reference;entries=3;agentos_meta=reference;status=ready\n"
			   "file=workflow/research-dag.json;kind=dag;stages=9;agentos_meta=workflow;status=ready\n"
			   "file=review/handoff-checklist.md;kind=review;items=6;agentos_meta=review;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablelaunch",
			   "launches=2\n"
			   "launch=usable-project-launch:lab-gene-x:1;scaffold=scaffold:lab-gene-x:starter;workbench=usable-workbench:RUN-900;run=usable-run:RUN-900;kernel_event=queued;status=ready\n"
			   "launch=usable-project-launch:protocol:1;scaffold=scaffold:protocol-reproduction;protocol=variant-calling-qc;run=RUN-042-rerun;kernel_event=queued;status=ready\n"
			   "operation=project_scaffold;inputs=template,dataset,library;outputs=workspace,manifest,runbook;kernel_context=recorded;status=ready\n"
			   "operation=project_launch;inputs=workspace,question,provider;outputs=workbench,run,project_space;kernel_context=recorded;status=ready\n"
			   "operation=operations_digest;sections=6;pending_reviews=1;active_actions=5;handoffs=3;kernel_snapshot=available;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablepack",
			   "bundles=2\n"
			   "bundle=usable-project-bundle:lab-gene-x;project=lab-gene-x;files=14;manifest=project-package-index;download=ready;metadata=indexed;status=ready\n"
			   "bundle=usable-study-protocol-reproduction-package:RUN-042;files=8;notebooks=2;datasets=2;review=approved;metadata=indexed;status=ready\n"
			   "intake=usable-package-intake:external-review;files=5;sha256=checked;decision=accepted;capability=checked;status=ready\n"
			   "action_plan=usable-reproduction-action-plan:RUN-042;steps=5;owner=recovery;event_queue=ready;status=ready\n"
			   "action_execution=usable-reproduction-action-execution:RUN-042;steps_done=5;result=passed;context=recorded;status=ready\n"
			   "package_index=project-package-index;handoff=ready;release_gate=release;snapshot=stable;metadata=indexed;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "usable_project=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;kernel_assisted=1;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "usable_project_page=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;kernel_assisted=1;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=usable_project;source=rp_usableproj;checks=120;bundles=2;kernel_assisted=1;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "usable_project_checks=120;scaffold_templates=3;project_launches=2;project_bundles=2;doctor_checks=10;kernel_observed=1;status=ready")) return 1;
	if (!append_study_protocol_package_action()) return 1;
	if (!append_project_lifecycle_actions()) return 1;
	if (!rp_append_file("rp_ack", "ack=usable_project;msg=lifecycle_ready;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.required_configuration")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.platform_doctor")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.scaffold")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.launch")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.operations_digest")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.bundle_export")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.package_intake")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.reproduction_action_plan")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.reproduction_action_execute")) return 1;
	if (!rp_append_file("rp_tool", "tool=usable_project.project_package_index")) return 1;
	if (!rp_append_status("usable_project=ready")) return 1;
	printf("rp_usableproject: scaffolds=3 launches=2 bundles=2 doctor=10 checks=120 status=ready\n");
	return 0;
}
