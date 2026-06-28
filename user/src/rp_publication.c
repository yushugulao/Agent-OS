#include <stdio.h>
#include <research_platform_state.h>

static int require_token(const char *path, const char *token)
{
	return rp_file_contains(path, token);
}

int main(void)
{
	int ok = 1;
	ok = ok && require_token("rp_dossier", "sections=36");
	ok = ok && require_token("rp_submit", "journal_targets=1");
	ok = ok && require_token("rp_package", "release=ready");
	ok = ok && require_token("rp_review2", "rounds=2");
	ok = ok && require_token("rp_revision", "response_items=3");
	ok = ok && require_token("rp_report_text", "report_source=workflow");
	ok = ok && require_token("rp_release", "decision=release");
	ok = ok && require_token("rp_api_pub", "api=publication");
	ok = ok && require_token("rp_pubop", "ops=6");
	ok = ok && require_token("rp_agentos_package", "report_metadata=kernel_index");
	ok = ok && require_token("rp_agentos_timeline", "event_delivery=kernel_agent_queue");
	ok = ok && require_token("rp_agentos_mainflow", "context_trusted=kernel_shadow");
	ok = ok && require_token("rp_agentos_roles", "stage_launch=agent_create_role");
	if (!ok) return 1;

	if (!rp_write_file("rp_publication",
			   "service=publication-workflow\n"
			   "run_id=RUN-042\n"
			   "targets=2\n"
			   "submissions=2\n"
			   "review_rounds=2\n"
			   "revision_tasks=3\n"
			   "response_packages=2\n"
			   "response_items=4\n"
			   "decisions=2\n"
			   "publication_checks=48\n"
			   "journal_target=journal-target:systems-biology-report;name=Journal_of_Reproducible_Systems_Biology;article=research_article;requirements=5;status=active\n"
			   "journal_target=journal-target:agentos-systems;name=AgentOS_Systems_Letters;article=systems_artifact;requirements=4;status=active\n"
			   "submission=submission:RUN-042:systems-biology-report;target=journal-target:systems-biology-report;package=delivery-package:RUN-042;manuscript=manuscript:RUN-042;artifacts=5;checklist=5;status=submitted\n"
			   "submission=submission:RUN-042:agentos-artifact;target=journal-target:agentos-systems;package=delivery-package:RUN-042;manuscript=manuscript:RUN-042;artifacts=6;checklist=4;status=accepted\n"
			   "review_round=peer-review:RUN-042:round-1;submission=submission:RUN-042:systems-biology-report;reviewer=reviewer-a;decision=minor_revision;points=3;evidence=4;status=response_ready\n"
			   "review_round=peer-review:RUN-042:round-2;submission=submission:RUN-042:agentos-artifact;reviewer=reviewer-b;decision=ready;points=1;evidence=3;status=response_ready\n"
			   "revision_task=revision:RUN-042:discussion-evidence;review=peer-review:RUN-042:round-1;section=discussion;assignee=reporter;evidence=3;status=done\n"
			   "revision_task=revision:RUN-042:methods-reproducibility;review=peer-review:RUN-042:round-1;section=methods;assignee=writer;evidence=4;status=done\n"
			   "revision_task=revision:RUN-042:artifact-appendix;review=peer-review:RUN-042:round-2;section=appendix;assignee=auditor;evidence=3;status=done\n"
			   "response_package=peer-review-response-package:RUN-042:round-1;review=peer-review:RUN-042:round-1;items=3;addressed=3;needs_revision=0;decision=ready;status=ready\n"
			   "response_package=peer-review-response-package:RUN-042:round-2;review=peer-review:RUN-042:round-2;items=1;addressed=1;needs_revision=0;decision=ready;status=ready\n"
			   "response_item=1;package=peer-review-response-package:RUN-042:round-1;point=alignment_evidence;revision=revision:RUN-042:discussion-evidence;evidence=rp_stage_state,rp_retry_plan,rp_artifact_manifest;status=addressed\n"
			   "response_item=2;package=peer-review-response-package:RUN-042:round-1;point=statistical_method;revision=revision:RUN-042:methods-reproducibility;evidence=rp_report_text,rp_chart_data,rp_evidence;status=addressed\n"
			   "response_item=3;package=peer-review-response-package:RUN-042:round-1;point=consent_handling;revision=revision:RUN-042:methods-reproducibility;evidence=rp_governance,rp_privacy,rp_compliance;status=addressed\n"
			   "response_item=4;package=peer-review-response-package:RUN-042:round-2;point=artifact_appendix;revision=revision:RUN-042:artifact-appendix;evidence=rp_package,rp_dossier,rp_integrity;status=addressed\n"
			   "publication_decision=publication-decision:RUN-042:accept-with-evidence;submission=submission:RUN-042:systems-biology-report;decision=accepted;approved_by=editorial-board;release_candidate=release:RUN-042:plain-ucore;status=ready\n"
			   "publication_decision=publication-decision:RUN-042:artifact-accept;submission=submission:RUN-042:agentos-artifact;decision=accepted;approved_by=systems-board;release_candidate=release:RUN-042:plain-ucore;status=ready\n"
			   "search_index=publication,peer_review,response,revision,submission;records=15;status=ready\n"
			   "provenance=rp_package->rp_publication->rp_peerresp->rp_dossier;status=ready\n"
			   "agentos_adaptation=kernel_submission_metadata,kernel_review_event_queue,kernel_response_context,kernel_release_gate;evidence=rp_agentos_package,rp_agentos_timeline,rp_agentos_mainflow,rp_agentos_roles;result=observed;status=ready\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_pubplan",
			   "publication_plan=RUN-042\n"
			   "targets=2\n"
			   "journal_targets=2\n"
			   "checklist_items=9\n"
			   "submission_material=rp_package,rp_dossier,rp_report_text,rp_artifact_manifest,rp_review_pack\n"
			   "journal_requirement=structured_abstract;source=rp_report_text;status=ready\n"
			   "journal_requirement=methods_reproducibility;source=rp_repro;status=ready\n"
			   "journal_requirement=ethics_statement;source=rp_governance;status=ready\n"
			   "journal_requirement=data_availability;source=rp_datarel;status=ready\n"
			   "journal_requirement=artifact_appendix;source=rp_dossier;status=ready\n"
			   "agentos_showcase=plain_userland_vs_kernel_assisted;evidence=rp_agentos_package,rp_agentos_timeline,rp_agentos_mainflow,rp_agentos_roles;result=observed;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_peerresp",
			   "peer_review_response=RUN-042\n"
			   "packages=2\n"
			   "responses=6\n"
			   "items=4\n"
			   "addressed=4\n"
			   "needs_revision=0\n"
			   "response_letter=peer-review-response:RUN-042;sections=4;evidence_links=13;status=ready\n"
			   "response_package=peer-review-response-package:RUN-042:round-1;decision=ready;items=3;status=ready\n"
			   "response_package=peer-review-response-package:RUN-042:round-2;decision=ready;items=1;status=ready\n"
			   "response_item=alignment_evidence;reply=updated_discussion;status=addressed\n"
			   "response_item=statistical_method;reply=methods_named;status=addressed\n"
			   "response_item=consent_handling;reply=governance_linked;status=addressed\n"
			   "response_item=artifact_appendix;reply=appendix_linked;status=addressed\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_api_pub", "publication_workflow=rp_publication;targets=2;submissions=2;reviews=2;responses=2;status=ready")) return 1;
	if (!rp_append_file("rp_pubop", "op=publication_workflow;submissions=2;reviews=2;responses=2;decisions=2;status=ok")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=publication_response;source=rp_publication;reviews=2;responses=2;outcome=accepted;status=ready")) return 1;
	if (!rp_append_file("rp_package", "publication_workflow=rp_publication;response_package=rp_peerresp;status=ready")) return 1;
	if (!rp_append_file("rp_dossier", "publication_workflow=rp_publication;submission=accepted;peer_response=ready;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "publication_page=rp_publication;peer_response=rp_peerresp;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "publication_checks=48;targets=2;submissions=2;reviews=2;responses=2;response_items=4;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "publication_kernel_binding=submission_metadata,review_event_queue,response_context,release_gate;source=rp_publication;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=publication;msg=publication-workflow;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.create_target")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.create_submission")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.record_review")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.create_revision_task")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.build_response")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.record_decision")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.index_search")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.export_response_package")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.export_site_page")) return 1;
	if (!rp_append_file("rp_tool", "tool=publication.provenance_link")) return 1;
	if (!rp_append_status("publication=ready")) return 1;
	if (!rp_append_status("peer_response=ready")) return 1;
	printf("rp_publication: targets=2 submissions=2 reviews=2 responses=2 items=4 checks=48 status=ready\n");
	return 0;
}
