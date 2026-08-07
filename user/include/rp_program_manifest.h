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

#endif
