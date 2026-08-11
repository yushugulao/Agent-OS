#ifndef FS_STORAGE_POLICY_H
#define FS_STORAGE_POLICY_H

// mkfs 与内核共享存储保障。配置保留量是上限目标，完整镜像只能把它
// 降到显式的每 workflow 最小值。
#define FS_WORKFLOW_SCOPE_SLOTS 4U
#define FS_WORKFLOW_MAX_FREE_NUMERATOR 3U
#define FS_WORKFLOW_MAX_FREE_DENOMINATOR 4U
#define FS_PUBLIC_PRINCIPAL_ID 2U
#define FS_WORKFLOW_SCOPE_FIRST_ID (FS_PUBLIC_PRINCIPAL_ID + 1U)
#define FS_STORAGE_POLICY_VERSION 2U

#ifndef FS_STORAGE_TINY_TEST_PROFILE
#define FS_STORAGE_TINY_TEST_PROFILE 0
#endif

#ifndef FS_WORKFLOW_BLOCK_RESERVE
#define FS_WORKFLOW_BLOCK_RESERVE (FSSIZE / 2U)
#endif
#ifndef FS_SYSTEM_BLOCK_RESERVE
#define FS_SYSTEM_BLOCK_RESERVE 0U
#endif
#ifndef FS_WORKFLOW_INODE_RESERVE
#define FS_WORKFLOW_INODE_RESERVE ((NINODE * 2U) / 3U)
#endif
#ifndef FS_SYSTEM_INODE_RESERVE
#define FS_SYSTEM_INODE_RESERVE 0U
#endif

#ifndef FS_SYSTEM_BLOCK_MIN_RESERVE
#define FS_SYSTEM_BLOCK_MIN_RESERVE 512U
#endif
#ifndef FS_SYSTEM_INODE_MIN_RESERVE
#define FS_SYSTEM_INODE_MIN_RESERVE 8U
#endif

#ifndef FS_WORKFLOW_BLOCK_MIN_PER_SCOPE
#define FS_WORKFLOW_BLOCK_MIN_PER_SCOPE 512U
#endif
#ifndef FS_WORKFLOW_INODE_MIN_PER_SCOPE
#define FS_WORKFLOW_INODE_MIN_PER_SCOPE 320U
#endif

static inline unsigned int
fs_policy_div_round_up(unsigned int value, unsigned int divisor)
{
	return value / divisor + (value % divisor != 0);
}

static inline unsigned int
fs_policy_reserve_value(unsigned int configured, unsigned int total,
			unsigned int divisor)
{
	unsigned int value = configured;

	if (value == 0 && total != 0)
		value = fs_policy_div_round_up(total, divisor);
	if (value > total)
		value = total;
	return value;
}

static inline unsigned int
fs_policy_system_reserve(unsigned int configured, unsigned int total,
			 unsigned int minimum)
{
	unsigned int reserve =
		fs_policy_reserve_value(configured, total, 32);

	if (reserve < minimum)
		reserve = minimum < total ? minimum : total;
	return reserve;
}

static inline unsigned int
fs_policy_workflow_guarantee(unsigned int configured, unsigned int total,
			     unsigned int system_reserve,
			     unsigned int free_count,
			     unsigned int minimum)
{
	unsigned int layout_available = total > system_reserve ?
					total - system_reserve : 0;
	unsigned int runtime_available = free_count > system_reserve ?
					 free_count - system_reserve : 0;
	unsigned int reserve = fs_policy_reserve_value(
		configured, layout_available, 16);
	unsigned int guarantee = fs_policy_div_round_up(
		reserve, FS_WORKFLOW_SCOPE_SLOTS);
	unsigned int layout_limit =
		layout_available / FS_WORKFLOW_SCOPE_SLOTS;
	unsigned int runtime_reserve =
		(runtime_available / FS_WORKFLOW_MAX_FREE_DENOMINATOR) *
			FS_WORKFLOW_MAX_FREE_NUMERATOR +
		(runtime_available % FS_WORKFLOW_MAX_FREE_DENOMINATOR) *
			FS_WORKFLOW_MAX_FREE_NUMERATOR /
			FS_WORKFLOW_MAX_FREE_DENOMINATOR;
	unsigned int runtime_limit =
		runtime_reserve / FS_WORKFLOW_SCOPE_SLOTS;
	unsigned int fundable_limit =
		runtime_available / FS_WORKFLOW_SCOPE_SLOTS;

	if (guarantee > layout_limit)
		guarantee = layout_limit;
	if (guarantee > runtime_limit)
		guarantee = runtime_limit;
	if (guarantee < minimum && fundable_limit >= minimum)
		guarantee = minimum;
	return guarantee;
}

static inline unsigned int
fs_policy_contract_checksum(unsigned int version, unsigned int scope_slots,
			    unsigned int public_principal,
			    unsigned int workflow_blocks,
			    unsigned int workflow_inodes,
			    unsigned int system_blocks,
			    unsigned int system_inodes)
{
	unsigned int values[] = {
		version, scope_slots, public_principal, workflow_blocks,
		workflow_inodes,
		system_blocks, system_inodes,
	};
	unsigned int hash = 2166136261U;

	for (unsigned int i = 0; i < sizeof(values) / sizeof(values[0]); i++) {
		hash ^= values[i];
		hash *= 16777619U;
	}
	return hash;
}

static inline int
fs_policy_contract_geometry_valid(unsigned int total_blocks,
				  unsigned int total_inodes,
				  unsigned int version,
				  unsigned int scope_slots,
				  unsigned int public_principal,
				  unsigned int workflow_blocks,
				  unsigned int workflow_inodes,
				  unsigned int system_blocks,
				  unsigned int system_inodes,
				  unsigned int checksum)
{
	if (version != FS_STORAGE_POLICY_VERSION ||
	    scope_slots != FS_WORKFLOW_SCOPE_SLOTS ||
	    public_principal != FS_PUBLIC_PRINCIPAL_ID ||
	    workflow_blocks < FS_WORKFLOW_BLOCK_MIN_PER_SCOPE ||
	    workflow_inodes < FS_WORKFLOW_INODE_MIN_PER_SCOPE ||
	    system_blocks < FS_SYSTEM_BLOCK_MIN_RESERVE ||
	    system_inodes < FS_SYSTEM_INODE_MIN_RESERVE ||
	    workflow_blocks > total_blocks / scope_slots ||
	    workflow_inodes > total_inodes / scope_slots)
		return 0;
	if (system_blocks > total_blocks - workflow_blocks * scope_slots ||
	    system_inodes > total_inodes - workflow_inodes * scope_slots)
		return 0;
	return checksum == fs_policy_contract_checksum(
		version, scope_slots, public_principal, workflow_blocks,
		workflow_inodes,
		system_blocks, system_inodes);
}

static inline int
fs_policy_contract_initially_funded(unsigned int free_blocks,
				    unsigned int free_inodes,
				    unsigned int workflow_blocks,
				    unsigned int workflow_inodes,
				    unsigned int system_blocks,
				    unsigned int system_inodes)
{
	if (free_blocks < system_blocks || free_inodes < system_inodes)
		return 0;
	return (free_blocks - system_blocks) / FS_WORKFLOW_SCOPE_SLOTS >=
		       workflow_blocks &&
	       (free_inodes - system_inodes) / FS_WORKFLOW_SCOPE_SLOTS >=
		       workflow_inodes;
}

static inline int
fs_policy_contract_runtime_funded(unsigned int free_blocks,
				  unsigned int free_inodes,
				  unsigned int workflow_blocks,
				  unsigned int workflow_inodes)
{
	return free_blocks / FS_WORKFLOW_SCOPE_SLOTS >= workflow_blocks &&
	       free_inodes / FS_WORKFLOW_SCOPE_SLOTS >= workflow_inodes;
}

static inline unsigned int
fs_policy_system_remaining(unsigned int free_count,
			   unsigned int workflow_guarantee,
			   unsigned int system_reserve)
{
	unsigned int workflow_total =
		workflow_guarantee * FS_WORKFLOW_SCOPE_SLOTS;

	if (free_count <= workflow_total)
		return 0;
	free_count -= workflow_total;
	return free_count < system_reserve ? free_count : system_reserve;
}

#endif
