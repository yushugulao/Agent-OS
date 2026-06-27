#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_backend_exec", "runner_report_rows=4");
	ok = ok && rp_file_contains("rp_study", "migration_status=baseline_and_agentos_observed");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_package", "status=ready");
	ok = ok && rp_file_contains("rp_reldossier", "decision=ready_for_review");
	ok = ok && rp_file_contains("rp_agentos_kernel", "context_snapshot=present");
	ok = ok && rp_file_contains("rp_agentos_kernel", "file_meta_service=initialized");
	if (!ok) return 1;

	if (!rp_write_file("rp_decsupport",
			   "service=decision-support\n"
			   "decision_support_checks=80\n"
			   "decision=decision:agentos-final-demo-backend\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "target=comparative-study:RUN-042:agentos-readiness\n"
			   "options=3\n"
			   "criteria=5\n"
			   "scores=15\n"
			   "review_packets=1\n"
			   "recommended_option=agentos_ucore_hybrid\n"
			   "selected=select_agentos_ucore_hybrid\n"
			   "weighted_score_userland_only=5.35\n"
			   "weighted_score_agentos_ucore_hybrid=8.80\n"
			   "weighted_score_full_kernel_llm_path=4.55\n"
			   "evidence_sources=rp_backend_exec,rp_study,rp_llm_packets,rp_package,rp_reldossier,rp_agentos_kernel\n"
			   "agentos_context=observed\n"
			   "agentos_file_metadata=observed\n"
			   "agentos_event_audit=observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_decopt",
			   "options=3\n"
			   "option=userland_only;summary=Keep the complete research Agent workflow in ordinary user space.;benefit=replayable_baseline;cost=weak_os_argument;recommendation=baseline_arm;status=ready\n"
			   "option=agentos_ucore_hybrid;summary=Use AgentOS-uCore for lifecycle, Context, metadata, events, and audit while keeping cloud LLM access on the host.;benefit=direct_os_value;cost=syscall_adapter;recommendation=final_target;kernel_observed=1;status=ready\n"
			   "option=full_kernel_llm_path;summary=Move Agent workflow and cloud LLM path into the teaching kernel.;benefit=max_kernel_ownership;cost=tls_dns_secret_risk;recommendation=reject_for_final_delivery;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_deccrit",
			   "criteria=5\n"
			   "criterion=agentos_value;weight=0.30;description=How directly the option proves OS-level Agent support.;status=ready\n"
			   "criterion=reproducibility;weight=0.25;description=Whether the option can be replayed without unstable cloud or host state.;status=ready\n"
			   "criterion=performance_signal;weight=0.20;description=Whether the option exposes measurable context, tool, and metadata signals.;status=ready\n"
			   "criterion=migration_effort;weight=0.15;description=Whether the option preserves the core research workflow during migration.;status=ready\n"
			   "criterion=reviewer_clarity;weight=0.10;description=Whether reviewers can inspect the same-workflow two-backend story.;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_decscore",
			   "scores=15\n"
			   "score=userland_only:agentos_value;option=userland_only;criterion=agentos_value;value=2;rationale=Useful baseline but Agent state remains outside the OS.;status=ready\n"
			   "score=userland_only:reproducibility;option=userland_only;criterion=reproducibility;value=8;rationale=Plain local run is easy to replay.;status=ready\n"
			   "score=userland_only:performance_signal;option=userland_only;criterion=performance_signal;value=4;rationale=Shows file scans and user-space indexing but not kernel fast paths.;status=ready\n"
			   "score=userland_only:migration_effort;option=userland_only;criterion=migration_effort;value=9;rationale=No kernel migration required.;status=ready\n"
			   "score=userland_only:reviewer_clarity;option=userland_only;criterion=reviewer_clarity;value=5;rationale=Clear workflow with weaker operating-system contribution.;status=ready\n"
			   "score=agentos_ucore_hybrid:agentos_value;option=agentos_ucore_hybrid;criterion=agentos_value;value=10;rationale=Kernel Context, metadata, events, provenance, and ledger are observed in the same workflow.;status=ready\n"
			   "score=agentos_ucore_hybrid:reproducibility;option=agentos_ucore_hybrid;criterion=reproducibility;value=8;rationale=Keeps host LLM proxy replay and fixed workflow fixtures.;status=ready\n"
			   "score=agentos_ucore_hybrid:performance_signal;option=agentos_ucore_hybrid;criterion=performance_signal;value=9;rationale=Observed kernel context snapshot and file metadata path strengthen the comparison.;status=ready\n"
			   "score=agentos_ucore_hybrid:migration_effort;option=agentos_ucore_hybrid;criterion=migration_effort;value=6;rationale=Requires adapters while preserving the same research workflow.;status=ready\n"
			   "score=agentos_ucore_hybrid:reviewer_clarity;option=agentos_ucore_hybrid;criterion=reviewer_clarity;value=9;rationale=Same workflow on two backends is directly inspectable.;status=ready\n"
			   "score=full_kernel_llm_path:agentos_value;option=full_kernel_llm_path;criterion=agentos_value;value=7;rationale=Large kernel ownership but much work is networking instead of AgentOS design.;status=ready\n"
			   "score=full_kernel_llm_path:reproducibility;option=full_kernel_llm_path;criterion=reproducibility;value=3;rationale=Cloud API and network details are hard to replay.;status=ready\n"
			   "score=full_kernel_llm_path:performance_signal;option=full_kernel_llm_path;criterion=performance_signal;value=5;rationale=TLS and network cost obscure OS-level measurements.;status=ready\n"
			   "score=full_kernel_llm_path:migration_effort;option=full_kernel_llm_path;criterion=migration_effort;value=2;rationale=High implementation burden and high risk.;status=ready\n"
			   "score=full_kernel_llm_path:reviewer_clarity;option=full_kernel_llm_path;criterion=reviewer_clarity;value=3;rationale=Review focus shifts away from task requirements.;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_decpacket",
			   "packet=decision-review-packet:agentos-final-demo-backend\n"
			   "decision=decision:agentos-final-demo-backend\n"
			   "recommended_option=agentos_ucore_hybrid\n"
			   "option_scores=userland_only:5.35,agentos_ucore_hybrid:8.80,full_kernel_llm_path:4.55\n"
			   "finding=userland_only:baseline_replayable_but_os_signal_weak\n"
			   "finding=agentos_ucore_hybrid:selected_after_kernel_context_and_metadata_observed\n"
			   "finding=full_kernel_llm_path:rejected_due_network_secret_complexity\n"
			   "evidence=rp_backend_exec,rp_study,rp_llm_packets,rp_package,rp_reldossier,rp_agentos_kernel\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_package", "decision_support=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=ready")) return 1;
	if (!rp_append_file("rp_web_bundle", "decision_support_page=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=decision_support;source=rp_decsupport;options=3;criteria=5;scores=15;selected=select_agentos_ucore_hybrid;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "decision_support_checks=80;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;agentos_replacements=4;kernel_observed=1;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=decision_support;msg=architecture_decision;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.create_decision")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.add_option")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.add_criterion")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.score_option")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.build_packet")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.select_option")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.attach_evidence")) return 1;
	if (!rp_append_file("rp_tool", "tool=decision_support.export_review")) return 1;
	if (!rp_append_status("decision_support=ready")) return 1;
	printf("rp_decsupport: options=3 criteria=5 scores=15 selected=agentos_ucore_hybrid checks=80 status=ready\n");
	return 0;
}
