#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_review_dashboard", "sections=8");
	ok = ok && rp_file_contains("rp_runbooks", "runbook_service_checks=16");
	ok = ok && rp_file_contains("rp_web_bundle", "release_gate=project-release-gate");
	ok = ok && rp_file_contains("rp_web_bundle", "project_snapshot=project-snapshot");
	ok = ok && rp_file_contains("rp_web_bundle", "reproducibility_audit=project-reproducibility-audit");
	ok = ok && rp_file_contains("rp_web_bundle", "provenance_graph=project-provenance-graph");
	ok = ok && rp_file_contains("rp_web_bundle", "project_delivery=project-delivery");
	ok = ok && rp_file_contains("rp_web_bundle", "package_intake=package-intake");
	if (!ok) return 1;

	if (!rp_write_file("rp_projectrel",
			   "service=project-delivery-review\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "project_delivery_checks=18\n"
			   "project_handoff_audits=1\n"
			   "project_runbooks=1\n"
			   "project_release_gates=1\n"
			   "project_snapshots=1\n"
			   "project_snapshot_comparisons=1\n"
			   "project_reproducibility_audits=1\n"
			   "project_provenance_graphs=1\n"
			   "package_intakes=1\n"
			   "package_indexes=1\n"
			   "handoff_audit=project-handoff-audit:lab-gene-x;decision=ready;required_actions=0;suggested_actions=2;status=ready\n"
			   "project_runbook=project-runbook:lab-gene-x;steps=7;browser_links=8;cli_commands=9;status=ready\n"
			   "release_gate=project-release-gate:lab-gene-x;decision=release;checks=6;required_actions=0;suggested_actions=2;status=ready\n"
			   "project_snapshot=project-snapshot:lab-gene-x:1;files=11;present=11;missing=0;hash_records=11;changes=0;status=ready\n"
			   "snapshot_comparison=project-snapshot-comparison:lab-gene-x:latest;left=project-snapshot:lab-gene-x:0;right=project-snapshot:lab-gene-x:1;changed_files=0;decision=stable;status=ready\n"
			   "reproducibility_audit=project-reproducibility-audit:lab-gene-x;inputs=2;outputs=8;notebooks=2;claim_audits=1;decision=passed;status=ready\n"
			   "provenance_graph=project-provenance-graph:lab-gene-x;nodes=9;edges=12;dot=project-provenance.dot;status=ready\n"
			   "project_delivery=project-delivery:lab-gene-x;decision=ready;bundle=project-bundle.zip;release_gate=release;handoff=ready;status=ready\n"
			   "package_intake=package-intake:external-review;label=External review package;decision=accepted;files=5;sha256=checked;status=ready\n"
			   "package_index=project-package-index;handoff=ready;release_gate=release;snapshot=stable;reproducibility=passed;provenance=ready;status=ready\n"
			   "source_files=rp_package,rp_release,rp_dossier,rp_web_bundle,rp_review_dashboard,rp_runbooks\n"
			   "agentos_adaptation=file_metadata_index,event_delivery,context_release_evidence,capability_guard;status=planned\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_web_bundle", "project_delivery_service=rp_projectrel;checks=18;release=ready;reproducibility=passed;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=project_delivery;source=rp_projectrel;release=ready;reproducibility=passed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "project_delivery_service=checks:18;handoff:1;release_gate:1;snapshot:1;reproducibility:1;provenance:1;intake:1;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=projectrel;msg=project-delivery;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=project.handoff_audit")) return 1;
	if (!rp_append_file("rp_tool", "tool=project.release_gate")) return 1;
	if (!rp_append_file("rp_tool", "tool=project.snapshot")) return 1;
	if (!rp_append_file("rp_tool", "tool=project.reproducibility_audit")) return 1;
	if (!rp_append_file("rp_tool", "tool=project.package_intake")) return 1;
	if (!rp_append_status("projectrel=ready")) return 1;

	printf("rp_projectrel: checks=18 release=ready reproducibility=passed intake=accepted status=ready\n");
	return 0;
}
