#ifndef AGENT_WORKSPACE_MUTATION_ABI_H
#define AGENT_WORKSPACE_MUTATION_ABI_H

#include "agent_lifecycle_abi.h"

#define AGENT_WORKSPACE_MUTATION_VERSION 1U
#define AGENT_WORKSPACE_MUTATION_APPLY_PATCH 1U
#define AGENT_WORKSPACE_MUTATION_WRITE_FILE  2U

#define AGENT_WORKSPACE_MUTATION_F_CREATE  0x1U
#define AGENT_WORKSPACE_MUTATION_F_REPLACE 0x2U
#define AGENT_WORKSPACE_MUTATION_F_ALL \
	(AGENT_WORKSPACE_MUTATION_F_CREATE | \
	 AGENT_WORKSPACE_MUTATION_F_REPLACE)

#define AGENT_WORKSPACE_PATH_SIZE 256U
#define AGENT_WORKSPACE_SHA256_SIZE 32U

/*
 * The payload bytes remain in a Guest artifact.  This descriptor binds those
 * bytes to one lifecycle, Host manifest object and expected revision before a
 * future Nexus workspace broker is allowed to execute the mutation.
 */
struct agent_workspace_mutation_request {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	unsigned long long request_id;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long object_id;
	unsigned long long expected_revision;
	unsigned int content_artifact_handle;
	unsigned int content_size;
	unsigned char content_sha256[AGENT_WORKSPACE_SHA256_SIZE];
	char path[AGENT_WORKSPACE_PATH_SIZE];
};

struct agent_workspace_mutation_receipt {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int operation;
	unsigned long long request_id;
	unsigned long long object_id;
	unsigned long long previous_revision;
	unsigned long long new_revision;
	unsigned int written_size;
	unsigned int reserved;
	unsigned char content_sha256[AGENT_WORKSPACE_SHA256_SIZE];
};

#endif
