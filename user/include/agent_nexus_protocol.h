#ifndef USER_AGENT_NEXUS_PROTOCOL_H
#define USER_AGENT_NEXUS_PROTOCOL_H

#include <agent.h>

/* Stable, task-independent autonomous-model request contract. */
#define AGENT_NEXUS_AUTONOMY_CONTRACT_VERSION 2U
#define AGENT_NEXUS_SYSTEM_POLICY_SHA256 \
	"3c6ff394bf6494d80208898e7440ba1da4fde43787e5162e46eaeb51d90c27b4"
#define AGENT_NEXUS_TOOL_CATALOG_SHA256 \
	"4d31b3dedab5b0b8084089a66b609b0f6ffecd17f6da031cd997cbbdf154ffe5"

/*
 * Agent IPC currently carries a NUL-terminated 64-byte payload.  Nexus keeps
 * its task ABI binary and canonical by encoding these exact 44 little-endian
 * bytes as unpadded base64url after the "N1:" discriminator.
 */
#define AGENT_NEXUS_TASK_MAGIC        0x3154584eU /* "NXT1" in LE bytes */
#define AGENT_NEXUS_TASK_VERSION      1U
#define AGENT_NEXUS_TASK_WIRE_SIZE    44U
#define AGENT_NEXUS_TASK_B64_SIZE     59U
#define AGENT_NEXUS_TASK_PREFIX       "N1:"
#define AGENT_NEXUS_TASK_PREFIX_SIZE  3U
#define AGENT_NEXUS_TASK_TEXT_SIZE    62U
#define AGENT_NEXUS_TASK_MAX_DEADLINE_DELTA 12000U

#define AGENT_NEXUS_TASK_OFF_MAGIC                0U
#define AGENT_NEXUS_TASK_OFF_VERSION              4U
#define AGENT_NEXUS_TASK_OFF_KIND                 5U
#define AGENT_NEXUS_TASK_OFF_STATE                6U
#define AGENT_NEXUS_TASK_OFF_FLAGS                7U
#define AGENT_NEXUS_TASK_OFF_LIFECYCLE_ID         8U
#define AGENT_NEXUS_TASK_OFF_LIFECYCLE_GENERATION 16U
#define AGENT_NEXUS_TASK_OFF_PARENT_TASK_ID       24U
#define AGENT_NEXUS_TASK_OFF_DEADLINE_TICK        28U
#define AGENT_NEXUS_TASK_OFF_STATUS               32U
#define AGENT_NEXUS_TASK_OFF_VALUE0               36U
#define AGENT_NEXUS_TASK_OFF_VALUE1               40U

enum agent_nexus_task_kind {
	AGENT_NEXUS_TASK_ASSIGN = 1,
	AGENT_NEXUS_TASK_ACCEPT = 2,
	AGENT_NEXUS_TASK_PROGRESS = 3,
	AGENT_NEXUS_TASK_RESULT = 4,
	AGENT_NEXUS_TASK_FAILED = 5,
	AGENT_NEXUS_TASK_CANCEL = 6,
};

enum agent_nexus_task_state {
	AGENT_NEXUS_TASK_STATE_ASSIGNED = 1,
	AGENT_NEXUS_TASK_STATE_ACCEPTED = 2,
	AGENT_NEXUS_TASK_STATE_RUNNING = 3,
	AGENT_NEXUS_TASK_STATE_WAITING = 4,
	AGENT_NEXUS_TASK_STATE_COMPLETED = 5,
	AGENT_NEXUS_TASK_STATE_FAILED = 6,
	AGENT_NEXUS_TASK_STATE_CANCELLED = 7,
};

#define AGENT_NEXUS_TASK_F_HAS_INPUT     (1U << 0)
#define AGENT_NEXUS_TASK_F_HAS_SECONDARY (1U << 1)
#define AGENT_NEXUS_TASK_F_HAS_RESULT    (1U << 2)
#define AGENT_NEXUS_TASK_F_FINAL         (1U << 3)
#define AGENT_NEXUS_TASK_F_KNOWN_MASK    0x0fU

/* ASSIGN uses status as the task type; terminal messages use Agent status. */
enum agent_nexus_task_type {
	AGENT_NEXUS_TASK_INSPECT_RUNTIME = 1001,
	AGENT_NEXUS_TASK_SOURCE_SEARCH = 1002,
	AGENT_NEXUS_TASK_SOURCE_READ = 1003,
	AGENT_NEXUS_TASK_DRAFT_REPORT = 1004,
	AGENT_NEXUS_TASK_INSPECT_PROCESSES = 1005,
	AGENT_NEXUS_TASK_INSPECT_CONTEXT = 1006,
	AGENT_NEXUS_TASK_USER_TURN = 2001,
	AGENT_NEXUS_TASK_MODEL_REQUEST = 2002,
	AGENT_NEXUS_TASK_APPROVAL = 2003,
	AGENT_NEXUS_TASK_SESSION_CLOSE = 2004,
	AGENT_NEXUS_TASK_INFRA_READY = 2090,
};

enum agent_nexus_metric_code {
	AGENT_NEXUS_METRIC_AGENT_ID = 1,
	AGENT_NEXUS_METRIC_CONTEXT_COUNT = 2,
	AGENT_NEXUS_METRIC_PROCESS_COUNT = 3,
	AGENT_NEXUS_METRIC_AGENT_COUNT = 4,
	AGENT_NEXUS_METRIC_UPTIME_TICK = 5,
	AGENT_NEXUS_METRIC_CAPABILITY_LOW = 6,
	AGENT_NEXUS_METRIC_FILE_HITS = 20,
	AGENT_NEXUS_METRIC_FILE_BYTES = 21,
	AGENT_NEXUS_METRIC_DIGEST_LOW = 22,
	AGENT_NEXUS_METRIC_DEPENDENCIES = 23,
	AGENT_NEXUS_METRIC_ARTIFACT_HANDLE = 40,
};

struct agent_nexus_task {
	unsigned char kind;
	unsigned char state;
	unsigned char flags;
	unsigned char reserved;
	unsigned long long lifecycle_id;
	unsigned long long lifecycle_generation;
	unsigned int parent_task_id;
	unsigned int deadline_tick;
	int status;
	unsigned int value0;
	unsigned int value1;
};

#define AGENT_NEXUS_ARTIFACT_MAGIC   0x3158414eU /* "NAX1" */
#define AGENT_NEXUS_ARTIFACT_VERSION 1U
#define AGENT_NEXUS_ARTIFACT_MAX     3072U
#define AGENT_NEXUS_ARTIFACT_SLOTS   65535U
#define AGENT_NEXUS_ARTIFACT_PATH_SIZE 14U

#define AGENT_NEXUS_ARTIFACT_HANDLE(generation, slot) \
	((((unsigned int)(generation) & 0xffffU) << 16) | \
	 ((unsigned int)(slot) & 0xffffU))
#define AGENT_NEXUS_ARTIFACT_GENERATION(handle) \
	(((unsigned int)(handle) >> 16) & 0xffffU)
#define AGENT_NEXUS_ARTIFACT_SLOT(handle) ((unsigned int)(handle) & 0xffffU)

#define AGENT_NEXUS_ARTIFACT_READ_COORDINATOR (1ULL << 0)
#define AGENT_NEXUS_ARTIFACT_READ_SYSTEM      (1ULL << 1)
#define AGENT_NEXUS_ARTIFACT_READ_RESEARCH    (1ULL << 2)
#define AGENT_NEXUS_ARTIFACT_READ_ANALYST     (1ULL << 3)
#define AGENT_NEXUS_ARTIFACT_READ_RELAY       (1ULL << 4)
#define AGENT_NEXUS_ARTIFACT_READ_ALL         0x1fULL

enum agent_nexus_product_role {
	AGENT_NEXUS_ROLE_COORDINATOR = 1,
	AGENT_NEXUS_ROLE_SYSTEM = 2,
	AGENT_NEXUS_ROLE_RESEARCH = 3,
	AGENT_NEXUS_ROLE_ANALYST = 4,
	AGENT_NEXUS_ROLE_RELAY = 5,
};

#define AGENT_NEXUS_ARTIFACT_F_BROKERED (1U << 0)
#define AGENT_NEXUS_ARTIFACT_F_PUBLISHED (1U << 1)
#define AGENT_NEXUS_ARTIFACT_F_KNOWN_MASK 0x03U

enum agent_nexus_artifact_kind {
	AGENT_NEXUS_ARTIFACT_TOOL_INPUT = 1,
	AGENT_NEXUS_ARTIFACT_MODEL_REQUEST = 2,
	AGENT_NEXUS_ARTIFACT_MODEL_RESPONSE = 3,
	AGENT_NEXUS_ARTIFACT_TASK_CAPSULE = 4,
	AGENT_NEXUS_ARTIFACT_SYSTEM_RESULT = 5,
	AGENT_NEXUS_ARTIFACT_RESEARCH_RESULT = 6,
	AGENT_NEXUS_ARTIFACT_REPORT = 7,
	AGENT_NEXUS_ARTIFACT_APPROVAL = 8,
};

enum agent_nexus_artifact_source {
	AGENT_NEXUS_SOURCE_SEED = 1,
	AGENT_NEXUS_SOURCE_KERNEL_TOOL = 2,
	AGENT_NEXUS_SOURCE_WORKER_METRIC = 3,
	AGENT_NEXUS_SOURCE_MODEL = 4,
	AGENT_NEXUS_SOURCE_USER = 5,
	AGENT_NEXUS_SOURCE_DERIVED = 6,
};

struct agent_nexus_artifact_actor {
	unsigned long long control_id;
	unsigned int pid;
	unsigned int agent_id;
	unsigned int kernel_role;
	unsigned int product_role;
};

struct agent_nexus_artifact_manifest {
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned int handle;
	unsigned int flags;
	struct agent_nexus_artifact_actor producer;
	struct agent_nexus_artifact_actor owner;
	struct agent_nexus_artifact_actor materializer;
	unsigned long long task_id;
	unsigned int parent_task_id;
	unsigned int kind;
	unsigned int source;
	unsigned int reserved;
	unsigned long long provenance_labels;
	unsigned long long permission_mask;
};

/*
 * This is a user-space security capsule, not a transferable kernel
 * capability.  The kernel independently enforces workflow VFS scope/caps.
 */
struct agent_nexus_artifact_header {
	unsigned int magic;
	unsigned short version;
	unsigned short header_size;
	unsigned int lifecycle_id;
	unsigned int handle;
	unsigned short handle_generation;
	unsigned short handle_slot;
	unsigned int flags;
	unsigned long long lifecycle_generation;
	struct agent_nexus_artifact_actor producer;
	struct agent_nexus_artifact_actor owner;
	struct agent_nexus_artifact_actor materializer;
	unsigned long long task_id;
	unsigned int parent_task_id;
	unsigned int payload_size;
	unsigned int kind;
	unsigned int source;
	unsigned long long provenance_labels;
	unsigned long long permission_mask;
	unsigned char payload_sha256[32];
	unsigned char manifest_sha256[32];
};

struct agent_nexus_task_capsule {
	unsigned int version;
	unsigned int task_type;
	unsigned int input_handle;
	unsigned int secondary_handle;
	unsigned int result_handle;
	unsigned int objective_length;
	char objective[2801];
	unsigned int argument_length;
	char argument[129];
	unsigned char reserved[2];
	struct agent_nexus_artifact_actor target;
};

_Static_assert(AGENT_NEXUS_TASK_PREFIX_SIZE + AGENT_NEXUS_TASK_B64_SIZE ==
	       AGENT_NEXUS_TASK_TEXT_SIZE, "Nexus task text size");
_Static_assert(sizeof(unsigned char) == 1 && sizeof(unsigned short) == 2 &&
	       sizeof(unsigned int) == 4 && sizeof(unsigned long long) == 8,
	       "Nexus protocol scalar widths");
_Static_assert(AGENT_NEXUS_TASK_TEXT_SIZE < AGENT_EVENT_PAYLOAD_SIZE,
	       "Nexus task must fit the Agent event payload");
_Static_assert(AGENT_NEXUS_TASK_OFF_VALUE1 + 4U ==
	       AGENT_NEXUS_TASK_WIRE_SIZE, "Nexus task wire extent");
_Static_assert(sizeof(struct agent_nexus_artifact_actor) == 24,
	       "Nexus artifact actor layout");
_Static_assert(sizeof(struct agent_nexus_artifact_manifest) == 136,
	       "Nexus artifact manifest layout");
_Static_assert(sizeof(struct agent_nexus_artifact_header) == 208,
	       "Nexus artifact header layout");
_Static_assert(__builtin_offsetof(struct agent_nexus_artifact_header,
				  lifecycle_generation) == 24,
	       "Nexus artifact lifecycle offset");
_Static_assert(__builtin_offsetof(struct agent_nexus_artifact_header,
				  payload_sha256) == 144,
	       "Nexus artifact digest offset");
_Static_assert(__builtin_offsetof(struct agent_nexus_artifact_header,
				  manifest_sha256) == 176,
	       "Nexus artifact manifest digest offset");
_Static_assert(sizeof(struct agent_nexus_task_capsule) <=
	       AGENT_NEXUS_ARTIFACT_MAX, "Nexus task capsule bound");
_Static_assert(sizeof(struct agent_nexus_task_capsule) == 2992,
	       "Nexus task capsule layout");
_Static_assert(__builtin_offsetof(struct agent_nexus_task_capsule, target) ==
	       2968, "Nexus task capsule target offset");

#endif
