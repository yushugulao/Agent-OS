#ifndef EXEC_POLICY_MANIFEST_H
#define EXEC_POLICY_MANIFEST_H

/*
 * Build-time executable trust policy. The host mkfs tool consumes every row
 * to provision immutable inode metadata. User-space launchers consume the
 * same rows so executable aliases, roles, and security profiles cannot drift.
 */
#define EXEC_MANIFEST_VERSION 2U

#define EXEC_MANIFEST_F_TRUSTED     0x1U
#define EXEC_MANIFEST_F_IMMUTABLE   0x2U
#define EXEC_MANIFEST_F_BOOTSTRAP   0x4U
#define EXEC_MANIFEST_F_DOMAIN_SAFE 0x8U

#define EXEC_MANIFEST_F_SEALED \
	(EXEC_MANIFEST_F_TRUSTED | EXEC_MANIFEST_F_IMMUTABLE | \
	 EXEC_MANIFEST_F_DOMAIN_SAFE)
#define EXEC_MANIFEST_F_BOOT_SEALED \
	(EXEC_MANIFEST_F_SEALED | EXEC_MANIFEST_F_BOOTSTRAP)

#define EXEC_MANIFEST_ROLE_SENTINEL     1
#define EXEC_MANIFEST_ROLE_INVESTIGATOR 2
#define EXEC_MANIFEST_ROLE_RECOVERY     3
#define EXEC_MANIFEST_ROLE_ORCHESTRATOR 4
#define EXEC_MANIFEST_ROLE_ARTIFACT     5
#define EXEC_MANIFEST_ROLE_BIT(role) (1U << (role))
#define EXEC_MANIFEST_ROLE_ALL 0x3eU

/* Executable security profiles are ceilings, never sources of authority. */
#define EXEC_MANIFEST_VFS_PROFILE_NONE     0U
#define EXEC_MANIFEST_VFS_PROFILE_WORKFLOW 1U
#define EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ 2U
#define EXEC_MANIFEST_VFS_PROFILE_ARTIFACT_WRITE 3U

/* Keep these values aligned with the Agent capability namespace. */
#define EXEC_MANIFEST_VFS_CONTENT_READ   (1ULL << 1)
#define EXEC_MANIFEST_VFS_ARTIFACT_WRITE (1ULL << 6)
#define EXEC_MANIFEST_VFS_WORKFLOW_CAPS \
	(EXEC_MANIFEST_VFS_CONTENT_READ | EXEC_MANIFEST_VFS_ARTIFACT_WRITE)

/*
 * Every non-Agent worker gets a deterministic sealed alias. The alias is
 * derived from the complete source name and mkfs rejects the unlikely hash
 * collision, so DIRSIZ truncation cannot silently select another image.
 */
static inline unsigned int exec_manifest_name_hash(const char *name)
{
	unsigned int hash = 2166136261U;

	for (; name != 0 && *name != 0; name++) {
		hash ^= (unsigned char)*name;
		hash *= 16777619U;
	}
	return hash;
}

static inline void exec_manifest_worker_image(const char *source,
					       char image[11])
{
	static const char hex[] = "0123456789abcdef";
	unsigned int hash = exec_manifest_name_hash(source);

	image[0] = 'w';
	image[1] = 'x';
	for (int i = 0; i < 8; i++)
		image[2 + i] = hex[(hash >> (28 - i * 4)) & 0xfU];
	image[10] = 0;
}

/*
 * X(source binary, installed image, flags, allowed role mask, launch role,
 *   executable security profile)
 *
 * A differing installed image creates a sealed code alias while preserving
 * the source name as a public compatibility image. launch role is zero for
 * entries that are not dispatched as Agents by rp_orch.
 */
#define EXEC_POLICY_ENTRIES(X) \
	X("usershell", "usershell", EXEC_MANIFEST_F_SEALED, 0, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_NONE) \
	X("agentfinal_ucore", "agentfinal_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentfs_ucore", "agentfs_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentscan_ucore", "agentscan_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentloop_ucore", "agentloop_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentsched_ucore", "agentsched_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentconflict_ucore", "agentconflict_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentllm_ucore", "agentllm_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentbench_ucore", "agentbench_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("labbench_ucore", "labbench_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("labdemo_ucore", "labdemo_ucore", EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentsecurity_ucore", "agentsecurity_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentscope_ucore", "agentscope_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("iobudget_ucore", "iobudget_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agenttrust_ucore", "agenttrust_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agenttrust_ucore", "at_orch", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agenttrust_ucore", "at_sentinel", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentvfs_ucore", "agentvfs_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("agentvfs_probe", "vfs_reader", EXEC_MANIFEST_F_SEALED, 0, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ) \
	X("agentvfs_probe", "vfs_writer", EXEC_MANIFEST_F_SEALED, 0, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_ARTIFACT_WRITE) \
	X("fsquota_ucore", "fsquota_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("fspquota_ucore", "fspquota_ucore", EXEC_MANIFEST_F_SEALED, 0, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_NONE) \
	X("procreap_agent_ucore", "procreap_agent_ucore", \
	  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("fileresource_ucore", "fileresource_ucore", \
	  EXEC_MANIFEST_F_SEALED, 0, 0, \
	  EXEC_MANIFEST_VFS_PROFILE_NONE) \
	X("rp_agentos_orch", "rp_agentos_orch", \
	  EXEC_MANIFEST_F_BOOT_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0, \
	  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_orch", "rp_orch", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_ALL, 0, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_query", "ax_query", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ARTIFACT), \
	  EXEC_MANIFEST_ROLE_ARTIFACT, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_repair", "ax_repair", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_RECOVERY), \
	  EXEC_MANIFEST_ROLE_RECOVERY, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_execobs", "ax_execobs", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ARTIFACT), \
	  EXEC_MANIFEST_ROLE_ARTIFACT, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_agent_collab", "ax_collab", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR) | \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_auditor", "ax_auditor", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_workbench", "ax_workbench", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ARTIFACT), \
	  EXEC_MANIFEST_ROLE_ARTIFACT, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_package", "ax_package", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_realtask", "ax_realtask", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_service_surface", "ax_service", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ARTIFACT), \
	  EXEC_MANIFEST_ROLE_ARTIFACT, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW) \
	X("rp_backend", "ax_backend", EXEC_MANIFEST_F_SEALED, \
	  EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), \
	  EXEC_MANIFEST_ROLE_ORCHESTRATOR, EXEC_MANIFEST_VFS_PROFILE_WORKFLOW)

#endif
