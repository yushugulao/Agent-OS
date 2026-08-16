#ifndef AGENT_MULTIAGENT_ABI_H
#define AGENT_MULTIAGENT_ABI_H

/* Generic Agent configuration shared by the kernel and every Nexus runtime. */
#define AGENT_RUNTIME_CONTROL_SYSCALL 570U

#define AGENT_RUNTIME_CONFIG_VERSION 1U
#define AGENT_RUNTIME_CONTROL_SPAWN 1U
#define AGENT_RUNTIME_CONTROL_QUERY_SELF 2U

#define AGENT_RUNTIME_F_NONE 0U

#define AGENT_TOOL_GRANT_BIT(tool_id) (1ULL << ((tool_id) - 1U))
#define AGENT_TOOL_GRANT_ALL(tool_count) \
	((tool_count) >= 64U ? ~0ULL : ((1ULL << (tool_count)) - 1ULL))

/* Zero quotas select the workflow defaults. */
struct agent_runtime_config {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	unsigned long long capabilities;
	unsigned long long allowed_tools;
	unsigned long long prompt_artifact_handle;
	unsigned int resource_budget;
	unsigned int artifact_count_limit;
	unsigned long long artifact_bytes_limit;
	unsigned long long artifact_read_limit;
	unsigned int summary_high_watermark;
	unsigned int reserved;
	unsigned long long reserved_tail[3];
};

struct agent_runtime_config_result {
	unsigned int version;
	unsigned int size;
	int status;
	int pid;
	unsigned int agent_id;
	unsigned int reserved;
	unsigned long long control_id;
	unsigned long long capabilities;
	unsigned long long allowed_tools;
	unsigned long long prompt_artifact_handle;
	unsigned int resource_budget;
	unsigned int artifact_count_limit;
	unsigned long long artifact_bytes_limit;
	unsigned long long artifact_read_limit;
	unsigned int summary_high_watermark;
	unsigned int reserved_tail;
	unsigned long long reserved_final;
};

_Static_assert(sizeof(struct agent_runtime_config) == 96,
	       "generic Agent runtime config ABI layout");
_Static_assert(sizeof(struct agent_runtime_config_result) == 96,
	       "generic Agent runtime result ABI layout");

#endif
