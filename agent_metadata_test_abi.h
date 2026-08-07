#ifndef AGENT_METADATA_TEST_ABI_H
#define AGENT_METADATA_TEST_ABI_H

#define AGENT_METADATA_TEST_ABI_VERSION 1U
#define AGENT_METADATA_TEST_ARM_NEXT 1U
#define AGENT_METADATA_TEST_F_ARMED (1U << 0)

/* 仅供测试配置使用的单次显式 COW 事务回执；内核填写全部字段，调用者传入清零且尺寸精确的对象。 */
struct agent_metadata_test_arm {
	unsigned int version;
	unsigned int flags;
	unsigned int scope_id;
	unsigned int reserved;
	unsigned long long lifecycle_id;
	unsigned long long lifecycle_generation;
	unsigned long long baseline_generation;
	unsigned long long target_generation;
	unsigned long long arm_token;
};

#endif
