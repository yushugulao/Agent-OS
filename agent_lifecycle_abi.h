#ifndef AGENT_LIFECYCLE_ABI_H
#define AGENT_LIFECYCLE_ABI_H

#define AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 2U

#define AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT (1U << 0)

/* 仅含身份与比较数据，不是可转让的持有者凭据。 */
struct agent_workflow_lifecycle_key {
	unsigned int id;
	unsigned int reserved;
	unsigned long long generation;
};

struct agent_workflow_lifecycle_info {
	unsigned int version;
	unsigned int struct_size;
	unsigned int charged;
	unsigned int reserved;
	struct agent_workflow_lifecycle_key key;
	unsigned int context_lane_depth;
	unsigned int context_lane_waiters;
	unsigned int metadata_txn_owned;
	unsigned int metadata_txn_waiters;
	/* 仅用于查询自身身份；系统调用不会把它当作权限。 */
	unsigned int resource_account_valid;
	unsigned int resource_account_slot;
	unsigned long long resource_account_generation;
};

_Static_assert(sizeof(unsigned int) == 4,
	       "workflow lifecycle ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "workflow lifecycle ABI requires 64-bit unsigned long long");
_Static_assert(sizeof(struct agent_workflow_lifecycle_key) == 16,
	       "workflow lifecycle key ABI layout");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_key,
				  generation) == 8,
	       "workflow lifecycle generation ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info, key) ==
	       16,
	       "workflow lifecycle key ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  context_lane_depth) == 32,
	       "workflow lifecycle runtime ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  metadata_txn_owned) == 40,
	       "workflow lifecycle metadata ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  metadata_txn_waiters) == 44,
	       "workflow lifecycle metadata waiter ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  resource_account_valid) == 48,
	       "workflow lifecycle resource account validity ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  resource_account_slot) == 52,
	       "workflow lifecycle resource account slot ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  resource_account_generation) == 56,
	       "workflow lifecycle resource account generation ABI offset");
_Static_assert(sizeof(struct agent_workflow_lifecycle_info) == 64,
	       "workflow lifecycle info ABI layout");

#endif
