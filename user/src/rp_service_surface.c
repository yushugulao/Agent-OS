#include <stdio.h>
#include <research_platform_state.h>

static int write_bio_services(void)
{
	if (!rp_write_file("rp_sreg",
			   "registry=sample-registry:RUN-042\n"
			   "samples=8\n"
			   "source_samples=4\n"
			   "aliquots=12\n"
			   "cohorts=2\n"
			   "custody_events=18\n"
			   "quarantine=0\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_ethics",
			   "protocol=protocol:RUN-042:recovery\n"
			   "ethics=approved\n"
			   "consent_forms=6\n"
			   "privacy_checks=8\n"
			   "deidentification=passed\n"
			   "external_release=blocked_until_review\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_access",
			   "requests=3\n"
			   "approved=2\n"
			   "denied=1\n"
			   "least_privilege=1\n"
			   "audit_records=3\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_cohort",
			   "cohorts=2\n"
			   "cohort_A=4\n"
			   "cohort_B=4\n"
			   "balance_checks=2\n"
			   "annotation_records=3\n"
			   "screening_decisions=6\n"
			   "status=ready\n")) {
		return 0;
	}
	return rp_write_file("rp_bioop",
			     "ops=7\n"
			     "op=sample_lookup;records=8;status=ok\n"
			     "op=custody_audit;records=18;status=ok\n"
			     "op=aliquot_plan;records=12;status=ok\n"
			     "op=ethics_gate;checks=8;status=ok\n"
			     "op=access_decision;requests=3;status=ok\n"
			     "op=cohort_balance;checks=2;status=ok\n"
			     "op=annotation_export;records=3;status=ok\n"
			     "status=ready\n");
}

static int write_lab_resources(void)
{
	if (!rp_write_file("rp_instr",
			   "instruments=4\n"
			   "sequencer=ready\n"
			   "imager=ready\n"
			   "gpu_node=ready\n"
			   "cold_storage=ready\n"
			   "maintenance_records=4\n"
			   "service_contracts=2\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_invent",
			   "inventory_items=9\n"
			   "reserved_items=4\n"
			   "reagent_lots=3\n"
			   "low_stock_alerts=1\n"
			   "blocked_lots=0\n"
			   "transactions=14\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_procure",
			   "requests=3\n"
			   "vendors=2\n"
			   "orders=2\n"
			   "receipts=1\n"
			   "budget_state=within_budget\n"
			   "sla_risk=low\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_ressched",
			   "bookings=6\n"
			   "conflicts=1\n"
			   "resolved_conflicts=1\n"
			   "training_requirements=4\n"
			   "qualified_people=3\n"
			   "ready_slots=5\n"
			   "status=ready\n")) {
		return 0;
	}
	return rp_write_file("rp_labresop",
			     "ops=6\n"
			     "op=instrument_check;records=4;status=ok\n"
			     "op=inventory_reserve;items=4;status=ok\n"
			     "op=procurement_plan;orders=2;status=ok\n"
			     "op=schedule_assess;bookings=6;status=ok\n"
			     "op=training_gate;requirements=4;status=ok\n"
			     "op=cost_guard;budget=within_budget;status=ok\n"
			     "status=ready\n");
}

static int write_publication_services(void)
{
	if (!rp_write_file("rp_resrev",
			   "review_items=10\n"
			   "accepted=10\n"
			   "blockers=0\n"
			   "statistical_design=accepted\n"
			   "visualization_review=passed\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_pubplan",
			   "journal_targets=2\n"
			   "primary_target=systems-bioinformatics-demo\n"
			   "sections=8\n"
			   "figures=3\n"
			   "data_availability=ready\n"
			   "code_availability=ready\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_peerresp",
			   "review_rounds=2\n"
			   "comments=6\n"
			   "responses=6\n"
			   "remaining_blockers=0\n"
			   "response_package=ready\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_fairpkg",
			   "fair_checks=8\n"
			   "findable=passed\n"
			   "accessible=passed\n"
			   "interoperable=passed\n"
			   "reusable=passed\n"
			   "doi_records=1\n"
			   "status=ready\n")) {
		return 0;
	}
	return rp_write_file("rp_pubop",
			     "ops=6\n"
			     "op=result_review;items=10;status=ok\n"
			     "op=publication_plan;targets=2;status=ok\n"
			     "op=peer_response;comments=6;status=ok\n"
			     "op=fair_package;checks=8;status=ok\n"
			     "op=release_gate;decision=release;status=ok\n"
			     "op=submission_bundle;sections=36;status=ok\n"
			     "status=ready\n");
}

static int write_knowledge_services(void)
{
	if (!rp_write_file("rp_litrev",
			   "review=systematic-mini\n"
			   "papers=9\n"
			   "screened=17\n"
			   "included=9\n"
			   "prisma_records=1\n"
			   "risk_of_bias_records=3\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_citegraph",
			   "citations=14\n"
			   "bibtex_entries=9\n"
			   "missing_keys=0\n"
			   "duplicate_keys=0\n"
			   "reference_integrity=passed\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_semindex",
			   "documents=17\n"
			   "chunks=17\n"
			   "entities=9\n"
			   "relations=6\n"
			   "tags=8\n"
			   "query_templates=3\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_kanswers",
			   "answers=4\n"
			   "answer=method_rationale;claims=3;status=supported\n"
			   "answer=failure_cause;claims=2;status=supported\n"
			   "answer=data_quality;claims=2;status=supported\n"
			   "answer=release_readiness;claims=1;status=supported\n"
			   "status=ready\n")) {
		return 0;
	}
	return rp_write_file("rp_knowop",
			     "ops=6\n"
			     "op=literature_screen;papers=17;status=ok\n"
			     "op=citation_validate;entries=9;status=ok\n"
			     "op=semantic_extract;entities=9;status=ok\n"
			     "op=query_answer;answers=4;status=ok\n"
			     "op=claim_link;claims=8;status=ok\n"
			     "op=llm_grounding;responses=3;status=ok\n"
			     "status=ready\n");
}

static int write_runtime_services(void)
{
	if (!rp_write_file("rp_runenv",
			   "environments=4\n"
			   "locks=4\n"
			   "validated=4\n"
			   "network_mode=host_relay_only\n"
			   "secret_values=0\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_nbexec",
			   "notebooks=2\n"
			   "cells=8\n"
			   "executed_cells=8\n"
			   "outputs=6\n"
			   "replay=passed\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_eln",
			   "eln_entries=3\n"
			   "signatures=2\n"
			   "attachments=4\n"
			   "integrity_checks=3\n"
			   "tamper_flags=0\n"
			   "status=ready\n")) {
		return 0;
	}
	if (!rp_write_file("rp_wpool",
			   "worker_pools=2\n"
			   "workers=4\n"
			   "heartbeats=4\n"
			   "slot_reservations=4\n"
			   "queue_depth=3\n"
			   "status=ready\n")) {
		return 0;
	}
	return rp_write_file("rp_runop",
			     "ops=7\n"
			     "op=env_lock;locks=4;status=ok\n"
			     "op=notebook_replay;cells=8;status=ok\n"
			     "op=eln_sign;signatures=2;status=ok\n"
			     "op=worker_heartbeat;workers=4;status=ok\n"
			     "op=host_llm_request;packets=3;status=ok\n"
			     "op=object_account;artifacts=48;status=ok\n"
			     "op=secret_policy;secret_values=0;status=ok\n"
			     "status=ready\n");
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_samples", "sheet=RUN-042:4");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_compliance", "decision=accepted");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_lit", "papers=3");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	if (!ok) return 1;

	if (!write_bio_services()) return 1;
	if (!write_lab_resources()) return 1;
	if (!write_publication_services()) return 1;
	if (!write_knowledge_services()) return 1;
	if (!write_runtime_services()) return 1;

	if (!rp_append_file("rp_ack", "ack=bio_services;msg=bio;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=lab_resources;msg=res;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=publication_services;msg=pub;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=knowledge_services;msg=know;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=runtime_services;msg=run;status=ready")) return 1;
	if (!rp_append_status("bio_services=ready")) return 1;
	if (!rp_append_status("sample_registry=ready")) return 1;
	if (!rp_append_status("ethics_review=ready")) return 1;
	if (!rp_append_status("access_requests=ready")) return 1;
	if (!rp_append_status("cohort_view=ready")) return 1;
	if (!rp_append_status("lab_resources=ready")) return 1;
	if (!rp_append_status("instrument_registry=ready")) return 1;
	if (!rp_append_status("inventory=ready")) return 1;
	if (!rp_append_status("procurement=ready")) return 1;
	if (!rp_append_status("resource_schedule=ready")) return 1;
	if (!rp_append_status("publication_services=ready")) return 1;
	if (!rp_append_status("result_review=ready")) return 1;
	if (!rp_append_status("publication_plan=ready")) return 1;
	if (!rp_append_status("peer_review_response=ready")) return 1;
	if (!rp_append_status("fair_package=ready")) return 1;
	if (!rp_append_status("knowledge_services=ready")) return 1;
	if (!rp_append_status("lit_review=ready")) return 1;
	if (!rp_append_status("citation_graph=ready")) return 1;
	if (!rp_append_status("semantic_index=ready")) return 1;
	if (!rp_append_status("knowledge_answers=ready")) return 1;
	if (!rp_append_status("runtime_services=ready")) return 1;
	if (!rp_append_status("runtime_env=ready")) return 1;
	if (!rp_append_status("notebook_exec=ready")) return 1;
	if (!rp_append_status("eln_record=ready")) return 1;
	if (!rp_append_status("worker_pool=ready")) return 1;
	printf("rp_service_surface: bio=ready lab_resources=ready publication=ready knowledge=ready runtime=ready status=ready\n");
	return 0;
}
