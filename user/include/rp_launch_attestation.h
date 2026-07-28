#ifndef __RP_LAUNCH_ATTESTATION_H__
#define __RP_LAUNCH_ATTESTATION_H__

#include <agent.h>

#define RP_LAUNCH_ATTEST_MAGIC   0x52504c41U
#define RP_LAUNCH_ATTEST_VERSION 1U
#define RP_LAUNCH_ATTEST_PREFIX  "--rp-launch-attest-fd="

struct rp_launch_attestation {
	uint magic;
	uint version;
	int status;
	int pid;
	int is_agent;
	int agent_role;
	uint64 filesystem_domain;
	uint64 filesystem_capability_mask;
};

#endif
