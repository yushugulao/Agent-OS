#ifndef __RP_PROGRAM_MANIFEST_H__
#define __RP_PROGRAM_MANIFEST_H__

/* 研究平台验收顺序的唯一来源。 */
#define RP_PLATFORM_PROGRAMS(APPLY) \
	APPLY("rp_catalog") \
	APPLY("rp_state_catalog") \
	APPLY("rp_object_store") \
	APPLY("rp_object_query") \
	APPLY("rp_lineage") \
	APPLY("rp_site_export") \
	APPLY("rp_planner") \
	APPLY("rp_portability") \
	APPLY("rp_retriever") \
	APPLY("rp_analyst") \
	APPLY("rp_reviewer") \
	APPLY("rp_lab") \
	APPLY("rp_governance") \
	APPLY("rp_writer") \
	APPLY("rp_repair") \
	APPLY("rp_auditor") \
	APPLY("rp_query") \
	APPLY("rp_evidence") \
	APPLY("rp_llm_bridge") \
	APPLY("rp_llm_relay") \
	APPLY("rp_privacy") \
	APPLY("rp_runconf") \
	APPLY("rp_execobs") \
	APPLY("rp_invoke") \
	APPLY("rp_complete") \
	APPLY("rp_artifact_ops") \
	APPLY("rp_data_pipeline") \
	APPLY("rp_workflow_runner") \
	APPLY("rp_workbench") \
	APPLY("rp_agent_collab") \
	APPLY("rp_package") \
	APPLY("rp_calculation") \
	APPLY("rp_realtask") \
	APPLY("rp_analysisres") \
	APPLY("rp_campaign") \
	APPLY("rp_delta") \
	APPLY("rp_release") \
	APPLY("rp_dossier") \
	APPLY("rp_service_surface") \
	APPLY("rp_startup_doctor") \
	APPLY("rp_notebook_export") \
	APPLY("rp_backend") \
	APPLY("rp_consistency") \
	APPLY("rp_metrics") \
	APPLY("rp_ui_export") \
	APPLY("rp_web_export") \
	APPLY("rp_revdash") \
	APPLY("rp_modelreg") \
	APPLY("rp_sysreview") \
	APPLY("rp_expsched") \
	APPLY("rp_traincomp") \
	APPLY("rp_publication") \
	APPLY("rp_runbooks") \
	APPLY("rp_projectrel") \
	APPLY("rp_studyproto") \
	APPLY("rp_stdesign") \
	APPLY("rp_opsboard") \
	APPLY("rp_reviewboard") \
	APPLY("rp_controlplane") \
	APPLY("rp_integrityplane") \
	APPLY("rp_coherenceplane") \
	APPLY("rp_mature") \
	APPLY("rp_prov_view") \
	APPLY("rp_prov_query") \
	APPLY("rp_reldossier") \
	APPLY("rp_decsupport") \
	APPLY("rp_usable") \
	APPLY("rp_usableproject") \
	APPLY("rp_compare_plain") \
	APPLY("rp_test_suite")

/* AgentOS 启动身份合同；未列出的程序均为委派的非 Agent 工作进程。
 * 普通 uCore 通过 fork 依序运行完整清单。 */
#define RP_AGENTOS_ROLE_PROGRAMS(APPLY) \
	APPLY("rp_query", "artifact") \
	APPLY("rp_repair", "recovery") \
	APPLY("rp_execobs", "artifact") \
	APPLY("rp_agent_collab", "orchestrator") \
	APPLY("rp_auditor", "orchestrator") \
	APPLY("rp_workbench", "artifact") \
	APPLY("rp_package", "orchestrator") \
	APPLY("rp_realtask", "orchestrator") \
	APPLY("rp_service_surface", "artifact") \
	APPLY("rp_backend", "orchestrator")

/* Canonical persistent-worker grouping for non-role platform programs.
 * The explicit indices are part of the fail-closed dispatch protocol. */
#define RP_WORKER_BATCH_0_PROGRAMS(APPLY) \
	APPLY(0, rp_catalog) \
	APPLY(1, rp_state_catalog) \
	APPLY(2, rp_object_store) \
	APPLY(3, rp_object_query) \
	APPLY(4, rp_lineage) \
	APPLY(5, rp_site_export) \
	APPLY(6, rp_planner) \
	APPLY(7, rp_portability) \
	APPLY(8, rp_retriever) \
	APPLY(9, rp_analyst) \
	APPLY(10, rp_reviewer) \
	APPLY(11, rp_lab) \
	APPLY(12, rp_governance) \
	APPLY(13, rp_writer) \
	APPLY(14, rp_evidence) \
	APPLY(15, rp_llm_bridge) \
	APPLY(16, rp_llm_relay) \
	APPLY(17, rp_privacy) \
	APPLY(18, rp_runconf) \
	APPLY(19, rp_invoke) \
	APPLY(20, rp_complete) \
	APPLY(21, rp_artifact_ops) \
	APPLY(22, rp_data_pipeline) \
	APPLY(23, rp_workflow_runner) \
	APPLY(24, rp_calculation) \
	APPLY(25, rp_analysisres) \
	APPLY(26, rp_campaign) \
	APPLY(27, rp_delta) \
	APPLY(28, rp_release) \
	APPLY(29, rp_dossier) \
	APPLY(30, rp_startup_doctor) \
	APPLY(31, rp_notebook_export)

#define RP_WORKER_BATCH_1_PROGRAMS(APPLY) \
	APPLY(0, rp_consistency) \
	APPLY(1, rp_metrics) \
	APPLY(2, rp_ui_export) \
	APPLY(3, rp_web_export) \
	APPLY(4, rp_revdash) \
	APPLY(5, rp_modelreg) \
	APPLY(6, rp_sysreview) \
	APPLY(7, rp_expsched) \
	APPLY(8, rp_traincomp)

#define RP_WORKER_BATCH_2_PROGRAMS(APPLY) \
	APPLY(0, rp_publication) \
	APPLY(1, rp_runbooks) \
	APPLY(2, rp_projectrel) \
	APPLY(3, rp_studyproto) \
	APPLY(4, rp_stdesign) \
	APPLY(5, rp_opsboard) \
	APPLY(6, rp_reviewboard) \
	APPLY(7, rp_controlplane) \
	APPLY(8, rp_integrityplane) \
	APPLY(9, rp_coherenceplane) \
	APPLY(10, rp_mature) \
	APPLY(11, rp_prov_view) \
	APPLY(12, rp_prov_query) \
	APPLY(13, rp_reldossier) \
	APPLY(14, rp_decsupport) \
	APPLY(15, rp_usable) \
	APPLY(16, rp_usableproject)

#define RP_WORKER_BATCH_GROUPS(APPLY) \
	APPLY(0, rp_wbatch0, 32) \
	APPLY(1, rp_wbatch1, 9) \
	APPLY(2, rp_wbatch2, 17)

/* These one-shot support programs remain direct launches: batching them would
 * add a protocol round trip without eliminating any process startup. */
#define RP_WORKER_DIRECT_PROGRAMS(APPLY) \
	APPLY(rp_compare_plain) \
	APPLY(rp_test_suite)

#define RP_WORKER_BATCH_GROUP_COUNT 3
#define RP_WORKER_BATCH_PROGRAM_COUNT 58
#define RP_WORKER_DIRECT_PROGRAM_COUNT 2

#endif
