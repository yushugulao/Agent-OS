#ifndef __RP_LAUNCH_ATTESTATION_H__
#define __RP_LAUNCH_ATTESTATION_H__

#include <agent.h>

#define RP_LAUNCH_EXPECT_PREFIX "--rp-launch-expect="
#define RP_LAUNCH_EXPECT_ARG_SIZE 96
#define RP_LAUNCH_IDENTITY_SOURCE "trusted_crt_self_check"
#define RP_LAUNCH_BATCH_IDENTITY_SOURCE "trusted_crt_batch_dispatch"
#define RP_LAUNCH_SELF_CHECK_EXIT 125

struct rp_launch_expectation {
	int is_agent;
	int agent_role;
	uint64 filesystem_domain;
	uint64 filesystem_capability_mask;
};

static inline int
rp_launch_expectation_valid(const struct rp_launch_expectation *expected)
{
	if (expected == 0 || expected->filesystem_domain == 0 ||
	    expected->filesystem_capability_mask == 0 ||
	    (expected->is_agent != 0 && expected->is_agent != 1))
		return 0;
	if (expected->is_agent)
		return expected->agent_role >= AGENT_ROLE_SENTINEL &&
		       expected->agent_role <= AGENT_ROLE_ARTIFACT;
	return expected->agent_role == 0;
}

#endif
