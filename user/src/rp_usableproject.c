#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_usable", "usable_research_checks=100");
	ok = ok && rp_file_contains("rp_usableops", "export=file-manifest");
	ok = ok && rp_file_contains("rp_projectrel", "project_delivery_checks=18");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_reproduction_packages=1");
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
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usableboot",
			   "config=usable-research-config:offline-template;provider=template;cloud_key_required=0;status=ready\n"
			   "startup_guide=usable-startup-guide:1;steps=7;first_run=research_project_launch;status=ready\n"
			   "doctor=usable-platform-doctor:1;checks=10;passed=10;failed=0;warnings=0;status=ready\n"
			   "check=workspace_root;result=pass;detail=ordinary_file_workspace_available\n"
			   "check=template_provider;result=pass;detail=no_cloud_key_required\n"
			   "check=reader_pages;result=pass;detail=usable_and_project_pages_exported\n"
			   "check=package_downloads;result=pass;detail=project_bundle_and_reproduction_package_ready\n"
			   "check=agentos_upgrade_points;result=pass;detail=context,file_metadata,event_queue,batch_tools\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablescaf",
			   "templates=3\n"
			   "template=scaffold-template:starter;files=5;includes=README,inputs,analysis,review,package;status=ready\n"
			   "template=scaffold-template:dataset-review;files=7;includes=data,dictionary,preview,quality,answer,review,package;status=ready\n"
			   "template=scaffold-template:protocol-reproduction;files=8;includes=protocol,launch,runs,comparison,review,actions,bundle,manifest;status=ready\n"
			   "scaffold=scaffold:lab-gene-x:starter;project=lab-gene-x;files=8;importable=1;status=ready\n"
			   "file=README.md;kind=guide;bytes=960;status=ready\n"
			   "file=inputs/dataset.csv;kind=data;rows=4;status=ready\n"
			   "file=references/library.bib;kind=reference;entries=3;status=ready\n"
			   "file=workflow/research-dag.json;kind=dag;stages=9;status=ready\n"
			   "file=review/handoff-checklist.md;kind=review;items=6;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablelaunch",
			   "launches=2\n"
			   "launch=usable-project-launch:lab-gene-x:1;scaffold=scaffold:lab-gene-x:starter;workbench=usable-workbench:RUN-900;run=usable-run:RUN-900;status=ready\n"
			   "launch=usable-project-launch:protocol:1;scaffold=scaffold:protocol-reproduction;protocol=variant-calling-qc;run=RUN-042-rerun;status=ready\n"
			   "operation=project_scaffold;inputs=template,dataset,library;outputs=workspace,manifest,runbook;status=ready\n"
			   "operation=project_launch;inputs=workspace,question,provider;outputs=workbench,run,project_space;status=ready\n"
			   "operation=operations_digest;sections=6;pending_reviews=1;active_actions=5;handoffs=3;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_usablepack",
			   "bundles=2\n"
			   "bundle=usable-project-bundle:lab-gene-x;project=lab-gene-x;files=14;manifest=project-package-index;download=ready;status=ready\n"
			   "bundle=usable-study-protocol-reproduction-package:RUN-042;files=8;notebooks=2;datasets=2;review=approved;status=ready\n"
			   "intake=usable-package-intake:external-review;files=5;sha256=checked;decision=accepted;status=ready\n"
			   "action_plan=usable-reproduction-action-plan:RUN-042;steps=5;owner=recovery;status=ready\n"
			   "action_execution=usable-reproduction-action-execution:RUN-042;steps_done=5;result=passed;status=ready\n"
			   "package_index=project-package-index;handoff=ready;release_gate=release;snapshot=stable;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "usable_project=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "usable_project_page=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=usable_project;source=rp_usableproj;checks=120;bundles=2;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "usable_project_checks=120;scaffold_templates=3;project_launches=2;project_bundles=2;doctor_checks=10;status=ready")) return 1;
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
