#ifndef EXEC_POLICY_MANIFEST_H
#define EXEC_POLICY_MANIFEST_H

/*
 * Build-time executable trust policy. The host mkfs tool consumes every row
 * to provision immutable inode metadata. User-space launchers consume the
 * same rows so executable aliases and requested roles cannot drift apart.
 */
#define EXEC_MANIFEST_VERSION 1U

#define EXEC_MANIFEST_F_TRUSTED   0x1U
#define EXEC_MANIFEST_F_IMMUTABLE 0x2U
#define EXEC_MANIFEST_F_BOOTSTRAP 0x4U

#define EXEC_MANIFEST_F_SEALED \
	(EXEC_MANIFEST_F_TRUSTED | EXEC_MANIFEST_F_IMMUTABLE)
#define EXEC_MANIFEST_F_BOOT_SEALED \
	(EXEC_MANIFEST_F_SEALED | EXEC_MANIFEST_F_BOOTSTRAP)

#define EXEC_MANIFEST_ROLE_SENTINEL     1
#define EXEC_MANIFEST_ROLE_INVESTIGATOR 2
#define EXEC_MANIFEST_ROLE_RECOVERY     3
#define EXEC_MANIFEST_ROLE_ORCHESTRATOR 4
#define EXEC_MANIFEST_ROLE_BIT(role) (1U << (role))
#define EXEC_MANIFEST_ROLE_ALL 0x1eU

/*
 * X(source binary, installed image, flags, allowed role mask, launch role)
 *
 * A differing installed image creates a sealed code alias while preserving
 * the source name as a mutable workflow artifact. launch role is zero for
 * entries that are not dispatched by rp_orch.
 */
#define EXEC_POLICY_ENTRIES(X) \
	X("usershell", "usershell", EXEC_MANIFEST_F_SEALED, 0, 0) \
	X("agentfinal_ucore", "agentfinal_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentfs_ucore", "agentfs_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentscan_ucore", "agentscan_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentloop_ucore", "agentloop_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentsched_ucore", "agentsched_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentconflict_ucore", "agentconflict_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentllm_ucore", "agentllm_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentbench_ucore", "agentbench_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("labbench_ucore", "labbench_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0) \
	X("labdemo_ucore", "labdemo_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agentsecurity_ucore", "agentsecurity_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("agenttrust_ucore", "agenttrust_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0) \
	X("agenttrust_ucore", "at_orch", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0) \
	X("agenttrust_ucore", "at_sentinel", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), 0) \
	X("procreap_agent_ucore", "procreap_agent_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0) \
	X("rp_agentos_orch", "rp_agentos_orch", \
	  EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0) \
	X("rp_orch", "rp_orch", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0) \
	X("rp_query", "ax_query", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_INVESTIGATOR), \
	  EXEC_MANIFEST_ROLE_INVESTIGATOR) \
	X("rp_repair", "ax_repair", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_RECOVERY), \
	  EXEC_MANIFEST_ROLE_RECOVERY) \
	X("rp_execobs", "ax_execobs", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_INVESTIGATOR), \
	  EXEC_MANIFEST_ROLE_INVESTIGATOR) \
	X("rp_agent_collab", "ax_collab", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR) | \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR) \
	X("rp_auditor", "ax_auditor", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR) \
	X("rp_workbench", "ax_workbench", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), \
	  EXEC_MANIFEST_ROLE_SENTINEL) \
	X("rp_package", "ax_package", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR) \
	X("rp_realtask", "ax_realtask", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR) \
	X("rp_service_surface", "ax_service", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), \
	  EXEC_MANIFEST_ROLE_SENTINEL) \
	X("rp_backend", "ax_backend", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR)

#endif
