#ifndef USER_AGENT_NEXUS_H
#define USER_AGENT_NEXUS_H

#include <agent.h>

#define AGENT_NEXUS_DELEGATE_SIDE_EFFECTS \
	(AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA | \
	 AGENT_SIDE_EFFECT_ARTIFACT | AGENT_SIDE_EFFECT_PROCESS | \
	 AGENT_SIDE_EFFECT_PERMISSION | AGENT_SIDE_EFFECT_IPC)

_Static_assert((AGENT_NEXUS_DELEGATE_SIDE_EFFECTS &
		AGENT_SIDE_EFFECT_WATCH) == 0,
	       "delegated task lease must not authorize watch effects");
#include <agent_nexus_protocol.h>

#define AGENT_NEXUS_SHA256_SIZE 32U
#define AGENT_NEXUS_SHA256_HEX_SIZE 64U
/* Complete result-file visibility only; Context, metadata and Fence are separate. */
#define AGENT_NEXUS_ARTIFACT_PUBLISH_IS_ATOMIC 1U

#define AGENT_NEXUS_TOOL_VIEW_READ_ONLY 1U
#define AGENT_NEXUS_TOOL_VIEW_EFFECTS   2U

struct agent_nexus_tool_argument {
	char key[AGENT_PARAM_KEY_SIZE];
	int type;
	unsigned long long number;
	char text[AGENT_PARAM_STRING_SIZE];
};

#define AGENT_NEXUS_TOOL_ROLE(role) (1U << ((unsigned int)(role) - 1U))

struct agent_nexus_tool_spec {
	int tool_id;
	unsigned int product_role_mask;
	unsigned long long required_capabilities;
	unsigned long long side_effect_mask;
	const char *name;
	const char *when_to_use;
	const char *when_not_to_use;
	const char *parameters;
	const char *result_fields;
	const char *errors;
};

struct agent_nexus_tool_view {
	const struct agent_tool_desc_v2 *descriptor;
	const struct agent_nexus_tool_spec *spec;
};

void agent_nexus_sha256(const void *data, unsigned int length,
			unsigned char digest[AGENT_NEXUS_SHA256_SIZE]);
void agent_nexus_sha256_hex(
	const unsigned char digest[AGENT_NEXUS_SHA256_SIZE],
	char text[AGENT_NEXUS_SHA256_HEX_SIZE + 1]);

int agent_nexus_tools_discover(void);
const struct agent_tool_desc_v2 *agent_nexus_tool_find(const char *name);
const struct agent_nexus_tool_spec *agent_nexus_tool_spec_find(
	const char *name);
int agent_nexus_tool_views_for_role(
	unsigned int product_role, struct agent_nexus_tool_view *views, int max);
int agent_nexus_tool_views_for_role_class(
	unsigned int product_role, unsigned int view_class,
	struct agent_nexus_tool_view *views, int max);
int agent_nexus_product_kernel_role(unsigned int product_role);
unsigned long long agent_nexus_product_capabilities(
	unsigned int product_role);
int agent_nexus_tool_call(const char *name, unsigned long long request_id,
			  const struct agent_nexus_tool_argument *arguments,
			  unsigned int argument_count,
			  struct agent_response_v2 *response);
int agent_nexus_tool_call_as(
	unsigned int product_role, const char *name,
	unsigned long long request_id,
	const struct agent_nexus_tool_argument *arguments,
	unsigned int argument_count, struct agent_response_v2 *response);

unsigned int agent_nexus_artifact_handle_make(
	unsigned long long lifecycle_generation, unsigned int slot);
int agent_nexus_artifact_handle_validate(
	unsigned int handle, unsigned long long lifecycle_generation,
	unsigned int *slot);
int agent_nexus_artifact_path(unsigned int handle,
			      char path[AGENT_NEXUS_ARTIFACT_PATH_SIZE]);
unsigned long long agent_nexus_product_permission(
	unsigned int product_role);
int agent_nexus_identity_registry_init(
	unsigned long long coordinator_control_id);
int agent_nexus_identity_register(unsigned int product_role,
				  unsigned long long control_id);
int agent_nexus_identity_bind_control(unsigned long long control_id);
int agent_nexus_identity_current(struct agent_nexus_artifact_actor *actor);
int agent_nexus_artifact_actor_current(
	unsigned int product_role, unsigned long long control_id,
	struct agent_nexus_artifact_actor *actor);
int agent_nexus_artifact_manifest_validate(
	const struct agent_nexus_artifact_manifest *manifest);
int agent_nexus_artifact_publish_owned(
	const struct agent_nexus_artifact_manifest *manifest,
	const void *payload, unsigned int payload_size,
	struct agent_nexus_artifact_header *published);
int agent_nexus_artifact_materialize_brokered(
	const struct agent_nexus_artifact_manifest *manifest,
	const void *payload, unsigned int payload_size,
	struct agent_nexus_artifact_header *published);
int agent_nexus_artifact_read_verify(
	unsigned int handle,
	const struct agent_workflow_lifecycle_key *expected_lifecycle,
	const struct agent_nexus_artifact_actor *reader,
	unsigned int expected_kind,
	struct agent_nexus_artifact_header *header,
	void *payload, unsigned int capacity, unsigned int *payload_size);

/* Compatibility wrappers retain the same verification and broker gates. */
int agent_nexus_artifact_write(
	const struct agent_nexus_artifact_header *header, const void *payload,
	unsigned int payload_size);
int agent_nexus_artifact_read(
	unsigned int handle,
	const struct agent_workflow_lifecycle_key *expected_lifecycle,
	int reader_role, struct agent_nexus_artifact_header *header,
	void *payload, unsigned int capacity, unsigned int *payload_size);

unsigned long long agent_nexus_role_permission(int role);
int agent_nexus_context_note(unsigned long long task_id, int tool_id,
			     int status, unsigned long long provenance,
			     const char *payload, const char *result,
			     unsigned long long value0,
			     unsigned long long value1,
			     unsigned long long value2);
int agent_nexus_artifact_context_note(
	unsigned long long task_id, int tool_id, int status,
	unsigned long long provenance, unsigned int handle,
	unsigned int payload_size,
	const unsigned char digest[AGENT_NEXUS_SHA256_SIZE]);

#endif
