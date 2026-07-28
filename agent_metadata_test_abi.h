#ifndef AGENT_METADATA_TEST_ABI_H
#define AGENT_METADATA_TEST_ABI_H

#define AGENT_METADATA_TEST_ABI_VERSION 1U
#define AGENT_METADATA_TEST_ARM_NEXT 1U
#define AGENT_METADATA_TEST_F_ARMED (1U << 0)

/*
 * Test-profile-only receipt for one explicitly armed COW transaction. The
 * kernel fills every field; callers provide a zeroed, exact-sized object.
 */
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
