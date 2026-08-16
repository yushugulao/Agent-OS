#ifndef AGENT_CONTEXT_ARTIFACT_ABI_H
#define AGENT_CONTEXT_ARTIFACT_ABI_H

#include "agent_lifecycle_abi.h"

#define AGENT_CONTEXT_ARTIFACT_SYSCALL 571U
#define AGENT_CONTEXT_ARTIFACT_VERSION 1U

#define AGENT_CONTEXT_ARTIFACT_SEAL    1U
#define AGENT_CONTEXT_ARTIFACT_BIND    2U
#define AGENT_CONTEXT_ARTIFACT_QUERY   3U
#define AGENT_CONTEXT_ARTIFACT_SHARE   4U
#define AGENT_CONTEXT_ARTIFACT_RELEASE 5U

#define AGENT_CONTEXT_ARTIFACT_F_UTF8          (1U << 0)
#define AGENT_CONTEXT_ARTIFACT_F_SHAREABLE     (1U << 1)
#define AGENT_CONTEXT_ARTIFACT_F_SHARED        (1U << 2)
#define AGENT_CONTEXT_ARTIFACT_F_EXTERNAL      (1U << 3)
#define AGENT_CONTEXT_ARTIFACT_F_ALL \
	(AGENT_CONTEXT_ARTIFACT_F_UTF8 | \
	 AGENT_CONTEXT_ARTIFACT_F_SHAREABLE | \
	 AGENT_CONTEXT_ARTIFACT_F_SHARED | \
	 AGENT_CONTEXT_ARTIFACT_F_EXTERNAL)

#define AGENT_CONTEXT_ARTIFACT_STATE_NONE   0U
#define AGENT_CONTEXT_ARTIFACT_STATE_SEALED 1U

#define AGENT_CONTEXT_ARTIFACT_USER          1U
#define AGENT_CONTEXT_ARTIFACT_TOOL          2U
#define AGENT_CONTEXT_ARTIFACT_FINAL         3U
#define AGENT_CONTEXT_ARTIFACT_FILE          4U
#define AGENT_CONTEXT_ARTIFACT_SEARCH        5U
#define AGENT_CONTEXT_ARTIFACT_PATCH         6U
#define AGENT_CONTEXT_ARTIFACT_BUILD_DIAG    7U
#define AGENT_CONTEXT_ARTIFACT_RUN_LOG       8U
#define AGENT_CONTEXT_ARTIFACT_TEST_RESULT   9U
#define AGENT_CONTEXT_ARTIFACT_SUBTASK      10U
#define AGENT_CONTEXT_ARTIFACT_TEAM_SUMMARY 11U
#define AGENT_CONTEXT_ARTIFACT_KIND_COUNT   12U

#define AGENT_CONTEXT_ARTIFACT_MAX_BYTES (64U * 1024U)

struct agent_context_artifact_control {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	unsigned long long handle;
	int source_fd;
	unsigned int kind;
	unsigned long long length;
	unsigned long long source_context_sequence;
	unsigned long long task_id;
	unsigned long long retain_until_tick;
	unsigned char content_sha256[32];
	unsigned long long reserved_tail[4];
};

struct agent_context_artifact_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int state;
	unsigned long long handle;
	unsigned int kind;
	unsigned int flags;
	unsigned long long length;
	unsigned long long source_context_sequence;
	unsigned long long task_id;
	struct agent_workflow_lifecycle_key lifecycle;
	int producer_pid;
	unsigned int producer_agent_id;
	unsigned long long producer_control_id;
	unsigned int references;
	unsigned int reserved;
	unsigned char content_sha256[32];
};

_Static_assert(sizeof(struct agent_context_artifact_control) == 128,
	       "Context Artifact control ABI layout");
_Static_assert(sizeof(struct agent_context_artifact_result) == 128,
	       "Context Artifact result ABI layout");

#endif
