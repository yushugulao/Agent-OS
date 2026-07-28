#include "agent_tool_protocol.h"
#include "defs.h"

struct agent_tool_definition {
	int tool_id;
	uint64 flags;
	const char *name;
	const char *description;
};

#define AGENT_TOOL_REGISTRY(X) \
	X(AGENT_TOOL_ECHO, AGENT_TOOL_F_CALLABLE, "echo", "return payload and numeric parameters") \
	X(AGENT_TOOL_PID_INFO, AGENT_TOOL_F_CALLABLE, "pid_info", "return pid, agent id, and agent flag") \
	X(AGENT_TOOL_CTX_STAT, AGENT_TOOL_F_CALLABLE, "ctx_stat", "return Agent Context base, size, and call count") \
	X(AGENT_TOOL_QUERY_PROCESS, AGENT_TOOL_F_CALLABLE, "query_process", "count processes and Agent processes") \
	X(AGENT_TOOL_GET_SYSTEM_STATUS, AGENT_TOOL_F_CALLABLE, "get_system_status", "return process count, agent count, and uptime tick") \
	X(AGENT_TOOL_READ_CONTEXT, AGENT_TOOL_F_CALLABLE, "read_context", "return post-state Context Path counters") \
	X(AGENT_TOOL_QUERY_FILE, AGENT_TOOL_F_CALLABLE, "query_file", "query file inode metadata or Agent file metadata") \
	X(AGENT_TOOL_SEND_MESSAGE, AGENT_TOOL_F_CALLABLE, "send_message", "send a short Agent message") \
	X(AGENT_TOOL_READ_MESSAGE, AGENT_TOOL_F_CALLABLE, "read_message", "read current Agent mailbox") \
	X(AGENT_TOOL_FILE_META_INIT, AGENT_TOOL_F_CALLABLE, "file_meta_init", "reload file object metadata and rebuild indexes") \
	X(AGENT_TOOL_READ_FILE_SUMMARY, AGENT_TOOL_F_CALLABLE, "read_file_summary", "read one indexed file summary") \
	X(AGENT_TOOL_DEPENDENCY_QUERY, AGENT_TOOL_F_CALLABLE, "dependency_query", "return registered dependent object labels") \
	X(AGENT_TOOL_CAPABILITY_CHECK, AGENT_TOOL_F_CALLABLE, "capability_check", "check role capability") \
	X(AGENT_TOOL_RERUN_STAGE, AGENT_TOOL_F_CALLABLE, "rerun_stage", "legacy action alias for a scoped state update") \
	X(AGENT_TOOL_WRITE_REPORT, AGENT_TOOL_F_CALLABLE, "write_report", "legacy artifact alias for a scoped state update") \
	X(AGENT_TOOL_AGENT_WATCH, AGENT_TOOL_F_CALLABLE, "agent_watch", "register an Agent Loop watch") \
	X(AGENT_TOOL_AGENT_WAIT, AGENT_TOOL_F_SYSCALL_ONLY, "agent_wait", "wait for an authorized queued, intrinsic, cancelled, or timeout event") \
	X(AGENT_TOOL_AGENT_HEARTBEAT, AGENT_TOOL_F_CALLABLE, "agent_heartbeat", "set heartbeat interval; zero stops the legacy tool path") \
	X(AGENT_TOOL_CONTEXT_PUSH, AGENT_TOOL_F_SYSCALL_ONLY, "context_push", "manual Context Path append") \
	X(AGENT_TOOL_READ_FILE_DIGEST, AGENT_TOOL_F_CALLABLE, "read_file_digest", "read a real file preview and content digest") \
	X(AGENT_TOOL_ACTION_COMMIT, AGENT_TOOL_F_CALLABLE, "action_commit", "commit a generic Agent action against object metadata") \
	X(AGENT_TOOL_ARTIFACT_UPDATE, AGENT_TOOL_F_CALLABLE, "artifact_update", "update a generic Agent artifact state") \
	X(AGENT_TOOL_LLM_REQUEST, AGENT_TOOL_F_CALLABLE, "llm_request", "record and route a structured LLM request") \
	X(AGENT_TOOL_LLM_RESPONSE, AGENT_TOOL_F_CALLABLE, "llm_response", "return a structured LLM relay response") \
	X(AGENT_TOOL_DEPENDENCY_UPDATE, AGENT_TOOL_F_CALLABLE, "dependency_update", "register or update a generic object dependency")

#define ASSERT_TOOL_STRINGS(id, flags, name, description) \
	_Static_assert(sizeof(name) <= AGENT_TOOL_NAME_SIZE, \
		       "Agent tool name exceeds its wire field"); \
	_Static_assert(sizeof(description) <= AGENT_TOOL_DESC_SIZE, \
		       "Agent tool description exceeds its wire field");
AGENT_TOOL_REGISTRY(ASSERT_TOOL_STRINGS)
#undef ASSERT_TOOL_STRINGS

#define TOOL_ENTRY(id, flags, name, description) \
	{ id, flags, name, description },
static const struct agent_tool_definition agent_tools[] = {
	AGENT_TOOL_REGISTRY(TOOL_ENTRY)
};
#undef TOOL_ENTRY

enum param_target { PARAM_ARG0, PARAM_ARG1, PARAM_PAYLOAD };
struct param_rule { const char *key; uint type, target, required; };
#define DECLARE_PARAM_KEY(symbol, literal) \
	static const char agent_param_key_##symbol[] = literal;
AGENT_PARAM_KEY_REGISTRY(DECLARE_PARAM_KEY)
#undef DECLARE_PARAM_KEY

#define R(key, type, target, required) \
	{ agent_param_key_##key, type, target, required }
static const struct param_rule
	rules[AGENT_TOOL_COUNT][AGENT_TOOL_PARAM_MAX] = {
	[AGENT_TOOL_ECHO - 1] = { R(PAYLOAD, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1), R(ARG0, AGENT_PARAM_UINT64, PARAM_ARG0, 1), R(ARG1, AGENT_PARAM_UINT64, PARAM_ARG1, 1) },
	[AGENT_TOOL_QUERY_PROCESS - 1] = { R(TYPE, AGENT_PARAM_UINT64, PARAM_ARG0, 0) },
	[AGENT_TOOL_QUERY_FILE - 1] = { R(PATH, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_SEND_MESSAGE - 1] = { R(TARGET_PID, AGENT_PARAM_UINT64, PARAM_ARG0, 1), R(MESSAGE, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_READ_FILE_SUMMARY - 1] = { R(SELECTOR, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_DEPENDENCY_QUERY - 1] = { R(LABEL, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_CAPABILITY_CHECK - 1] = { R(ROLE, AGENT_PARAM_UINT64, PARAM_ARG0, 1), R(ACTION, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_RERUN_STAGE - 1] = { R(ROLE, AGENT_PARAM_UINT64, PARAM_ARG0, 0), R(STAGE, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_WRITE_REPORT - 1] = { R(ROLE, AGENT_PARAM_UINT64, PARAM_ARG0, 0), R(PAYLOAD, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_AGENT_WATCH - 1] = { R(EVENT_TYPE, AGENT_PARAM_UINT64, PARAM_ARG0, 1), R(FILTER, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_AGENT_WAIT - 1] = { R(TIMEOUT, AGENT_PARAM_UINT64, PARAM_ARG0, 1) },
	[AGENT_TOOL_AGENT_HEARTBEAT - 1] = { R(INTERVAL, AGENT_PARAM_UINT64, PARAM_ARG0, 1) },
	[AGENT_TOOL_CONTEXT_PUSH - 1] = { R(RECORD, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_READ_FILE_DIGEST - 1] = { R(SELECTOR, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_ACTION_COMMIT - 1] = { R(ROLE, AGENT_PARAM_UINT64, PARAM_ARG0, 0), R(SELECTOR, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_ARTIFACT_UPDATE - 1] = { R(ROLE, AGENT_PARAM_UINT64, PARAM_ARG0, 0), R(SELECTOR, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_LLM_REQUEST - 1] = { R(TARGET_PID, AGENT_PARAM_UINT64, PARAM_ARG0, 1), R(PROMPT_SUMMARY, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_LLM_RESPONSE - 1] = { R(TARGET_PID, AGENT_PARAM_UINT64, PARAM_ARG0, 1), R(REPLY_SUMMARY, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
	[AGENT_TOOL_DEPENDENCY_UPDATE - 1] = { R(SELECTOR, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1) },
};
#undef R
#undef AGENT_TOOL_REGISTRY

_Static_assert(sizeof(agent_tools) / sizeof(agent_tools[0]) ==
	       AGENT_TOOL_COUNT, "tool descriptor count must match the ABI");
_Static_assert(sizeof(rules) / sizeof(rules[0]) == AGENT_TOOL_COUNT,
	       "tool rule count must match the ABI");

static int string_length(const char *text, uint capacity, uint *length)
{
	for (uint i = 0; i < capacity; i++)
		if (text[i] == 0) {
			if (length)
				*length = i;
			return 0;
		}
	return -1;
}

static uint rule_count(int tool_id)
{
	uint count = 0;

	while (count < AGENT_TOOL_PARAM_MAX &&
	       rules[tool_id - 1][count].key != 0)
		count++;
	return count;
}

static int schema_append(char *schema, uint capacity, uint *used,
			 const char *text)
{
	uint length = strlen(text);

	if (*used + length >= capacity)
		return -1;
	memmove(schema + *used, text, length);
	*used += length;
	schema[*used] = 0;
	return 0;
}

static int agent_tool_schema(int tool_id, char *schema, uint capacity)
{
	uint count = rule_count(tool_id);
	uint used = 0;

	if (schema == 0 || capacity == 0)
		return -1;
	schema[0] = 0;
	if (count == 0)
		return schema_append(schema, capacity, &used, "none");
	for (uint i = 0; i < count; i++) {
		const struct param_rule *rule = &rules[tool_id - 1][i];
		const char *type = rule->type == AGENT_PARAM_UINT64 ?
					   "uint64" : "string";

		if ((i != 0 && schema_append(schema, capacity, &used, ",") < 0) ||
		    schema_append(schema, capacity, &used, rule->key) < 0 ||
		    (!rule->required &&
		     schema_append(schema, capacity, &used, "?") < 0) ||
		    schema_append(schema, capacity, &used, ":") < 0 ||
		    schema_append(schema, capacity, &used, type) < 0)
			return -1;
	}
	return 0;
}

static int agent_tool_protocol_schema_valid(void)
{
	char schema[AGENT_TOOL_PARAMS_SIZE];

	for (uint tool = 0; tool < AGENT_TOOL_COUNT; tool++) {
		uint count = rule_count(tool + 1);
		uint targets = 0;

		if (agent_tools[tool].tool_id != (int)tool + 1 ||
		    agent_tools[tool].name == 0 ||
		    agent_tools[tool].description == 0 ||
		    string_length(agent_tools[tool].name,
				  AGENT_TOOL_NAME_SIZE, 0) < 0 ||
		    string_length(agent_tools[tool].description,
				  AGENT_TOOL_DESC_SIZE, 0) < 0 ||
		    (agent_tools[tool].flags != AGENT_TOOL_F_CALLABLE &&
		     agent_tools[tool].flags != AGENT_TOOL_F_SYSCALL_ONLY) ||
		    agent_tool_schema(tool + 1, schema, sizeof(schema)) < 0)
			return 0;
		for (uint other = 0; other < tool; other++)
			if (!strncmp(agent_tools[tool].name,
				     agent_tools[other].name,
				     AGENT_TOOL_NAME_SIZE))
				return 0;
		for (uint i = 0; i < AGENT_TOOL_PARAM_MAX; i++) {
			const struct param_rule *rule = &rules[tool][i];

			if (i >= count) {
				if (rule->key != 0 || rule->type != 0 ||
				    rule->target != 0 || rule->required != 0)
					return 0;
				continue;
			}
			if (string_length(rule->key, AGENT_PARAM_KEY_SIZE, 0) < 0 ||
			    rule->key[0] == 0 || rule->required > 1 ||
			    rule->target > PARAM_PAYLOAD ||
			    (targets & (1U << rule->target)) != 0 ||
			    (rule->target == PARAM_PAYLOAD &&
			     rule->type != AGENT_PARAM_STRING) ||
			    (rule->target != PARAM_PAYLOAD &&
			     rule->type != AGENT_PARAM_UINT64))
				return 0;
			targets |= 1U << rule->target;
			for (uint other = 0; other < i; other++)
				if (!strncmp(rule->key, rules[tool][other].key,
					     AGENT_PARAM_KEY_SIZE))
					return 0;
		}
	}
	return 1;
}

void agent_tool_protocol_init(void)
{
	if (!agent_tool_protocol_schema_valid())
		panic("Agent tool schema");
}

static const struct agent_tool_definition *tool_by_id(int tool_id)
{
	if (tool_id <= 0 || tool_id > AGENT_TOOL_COUNT ||
	    agent_tools[tool_id - 1].tool_id != tool_id)
		return 0;
	return &agent_tools[tool_id - 1];
}

#define REJECT(status, message) do { \
	safestrcpy(error, message, error_size); \
	return status; \
} while (0)

uint64 agent_tool_protocol_flags(int tool_id)
{
	const struct agent_tool_definition *tool = tool_by_id(tool_id);

	return tool ? tool->flags : 0;
}

int agent_tool_protocol_resolve(int tool_id, char *name,
				struct agent_tool_match *match,
				char *error, int error_size)
{
	const struct agent_tool_definition *tool = 0;

	if (string_length(name, AGENT_TOOL_NAME_SIZE, 0) < 0)
		REJECT(AGENT_STATUS_BAD_SIZE, "tool_name_too_long");
	if (tool_id != 0) {
		tool = tool_by_id(tool_id);
		if (!tool)
			REJECT(AGENT_STATUS_UNKNOWN_TOOL, "unknown_tool_id");
		if (name[0] && strncmp(name, tool->name, AGENT_TOOL_NAME_SIZE))
			REJECT(AGENT_STATUS_BAD_REQUEST, "tool_mismatch");
	} else if (name[0]) {
		for (uint i = 0; i < AGENT_TOOL_COUNT; i++)
			if (!strncmp(name, agent_tools[i].name,
				     AGENT_TOOL_NAME_SIZE)) {
				tool = &agent_tools[i];
				break;
			}
	}
	if (!tool)
		REJECT(AGENT_STATUS_UNKNOWN_TOOL, "unknown_tool");
	memset(match, 0, sizeof(*match));
	match->tool_id = tool->tool_id;
	match->flags = tool->flags;
	safestrcpy(match->name, tool->name, sizeof(match->name));
	return AGENT_STATUS_OK;
}

static int rule_for_target(int tool_id, uint target)
{
	for (uint i = 0; i < rule_count(tool_id); i++)
		if (rules[tool_id - 1][i].target == target)
			return i;
	return -1;
}

int agent_tool_protocol_decode_v1(struct agent_request *request,
				  struct agent_tool_match *match,
				  struct agent_op *op, char *error,
				  int error_size)
{
	char *keys[] = { request->arg0_key, request->arg1_key,
			 request->payload_key };
	int types[] = { request->arg0_type, request->arg1_type,
			request->payload_type };
	int present[] = {
		keys[0][0] || types[0] != AGENT_PARAM_NONE,
		keys[1][0] || types[1] != AGENT_PARAM_NONE,
		keys[2][0] || types[2] != AGENT_PARAM_NONE || request->payload[0],
	};
	uint seen = 0;

	if (string_length(keys[0], sizeof(request->arg0_key), 0) < 0 ||
	    string_length(keys[1], sizeof(request->arg1_key), 0) < 0 ||
	    string_length(keys[2], sizeof(request->payload_key), 0) < 0 ||
	    string_length(request->payload, sizeof(request->payload), 0) < 0)
		REJECT(AGENT_STATUS_BAD_SIZE, "unterminated_field");
	for (uint target = 0; target < 3; target++) {
		int index;

		if (!present[target]) {
			uint64 hidden_value = target == PARAM_ARG0 ? request->arg0 :
				target == PARAM_ARG1 ? request->arg1 : 0;

			if (hidden_value != 0)
				REJECT(AGENT_STATUS_BAD_PARAM, "untyped_param_value");
			continue;
		}
		index = rule_for_target(match->tool_id, target);
		if (index < 0)
			REJECT(AGENT_STATUS_BAD_PARAM, "unexpected_param");
		if (strncmp(keys[target], rules[match->tool_id - 1][index].key,
			    AGENT_PARAM_KEY_SIZE))
			REJECT(AGENT_STATUS_BAD_PARAM, target == PARAM_PAYLOAD ?
			       "bad_payload_key" : "bad_arg_key");
		if (types[target] != (int)rules[match->tool_id - 1][index].type)
			REJECT(AGENT_STATUS_BAD_PARAM, target == PARAM_PAYLOAD ?
			       "bad_payload_type" : "bad_arg_type");
		seen |= 1U << index;
	}
	for (uint i = 0; i < rule_count(match->tool_id); i++)
		if (rules[match->tool_id - 1][i].required && !(seen & (1U << i)))
			REJECT(AGENT_STATUS_BAD_PARAM, "missing_param");
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION_V1;
	op->tool_id = match->tool_id;
	op->request_id = request->request_id;
	op->arg0 = request->arg0;
	op->arg1 = request->arg1;
	safestrcpy(op->payload, request->payload, sizeof(op->payload));
	return AGENT_STATUS_OK;
}

int agent_tool_protocol_decode_v2(pagetable_t pagetable,
				  struct agent_request_v2 *request,
				  struct agent_tool_match *match,
				  struct agent_op *op, char *error,
				  int error_size)
{
	uint seen = 0, count = rule_count(match->tool_id);

	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION_V1;
	op->tool_id = match->tool_id;
	op->request_id = request->request_id;
	for (uint i = 0; i < request->param_count; i++) {
		struct agent_param_v2 param;
		uint text_length = 0;
		int index = -1;

		if (copyin(pagetable, (char *)&param,
			   request->params + (uint64)i * sizeof(param),
			   sizeof(param)) < 0)
			return -1;
		if (param.version != AGENT_PARAM_VERSION)
			REJECT(AGENT_STATUS_BAD_VERSION, "bad_param_version");
		if (param.size != sizeof(param) ||
		    string_length(param.key, AGENT_PARAM_KEY_SIZE, 0) < 0)
			REJECT(AGENT_STATUS_BAD_SIZE, "bad_param_size");
		for (uint j = 0; j < count; j++)
			if (!strncmp(param.key, rules[match->tool_id - 1][j].key,
				     AGENT_PARAM_KEY_SIZE)) {
				index = j;
				break;
			}
		if (index < 0)
			REJECT(AGENT_STATUS_UNKNOWN_PARAM, "unknown_param");
		if (seen & (1U << index))
			REJECT(AGENT_STATUS_DUPLICATE, "duplicate_param");
		seen |= 1U << index;
		if (param.type != rules[match->tool_id - 1][index].type)
			REJECT(AGENT_STATUS_BAD_TYPE, "bad_param_type");
		if (param.type == AGENT_PARAM_UINT64) {
			if (param.value_size != sizeof(param.value.uint64_value))
				REJECT(AGENT_STATUS_BAD_SIZE, "bad_value_size");
		} else if (param.value_size == 0 ||
			   param.value_size > AGENT_PARAM_STRING_SIZE ||
			   string_length(param.value.string_value, param.value_size,
					 &text_length) < 0 ||
			   text_length + 1 != param.value_size)
			REJECT(AGENT_STATUS_BAD_SIZE, "bad_value_size");
		switch (rules[match->tool_id - 1][index].target) {
		case PARAM_ARG0: op->arg0 = param.value.uint64_value; break;
		case PARAM_ARG1: op->arg1 = param.value.uint64_value; break;
		default: safestrcpy(op->payload, param.value.string_value,
				    sizeof(op->payload)); break;
		}
	}
	for (uint i = 0; i < count; i++)
		if (rules[match->tool_id - 1][i].required && !(seen & (1U << i)))
			REJECT(AGENT_STATUS_BAD_PARAM, "missing_param");
	return AGENT_STATUS_OK;
}

#undef REJECT

int agent_tool_protocol_list_v1(pagetable_t pagetable, uint64 address, int max)
{
	struct agent_tool_desc desc;
	int count = max > AGENT_TOOL_COUNT ? AGENT_TOOL_COUNT : max;

	if (max < 0)
		return -1;
	if (count && user_range_check(pagetable, address,
				      (uint64)count * sizeof(desc), PTE_W) < 0)
		return -1;
	for (int i = 0; i < count; i++) {
		memset(&desc, 0, sizeof(desc));
		desc.tool_id = agent_tools[i].tool_id;
		desc.flags = agent_tools[i].flags;
		safestrcpy(desc.name, agent_tools[i].name, sizeof(desc.name));
		if (agent_tool_schema(i + 1, desc.params,
				      sizeof(desc.params)) < 0)
			return -1;
		safestrcpy(desc.description, agent_tools[i].description,
			   sizeof(desc.description));
		if (copyout(pagetable, address + (uint64)i * sizeof(desc),
			    (char *)&desc, sizeof(desc)) < 0)
			return -1;
	}
	return AGENT_TOOL_COUNT;
}

int agent_tool_protocol_list_v2(pagetable_t pagetable, uint64 address, int max,
				uint desc_size, uint version)
{
	struct agent_tool_desc_v2 desc;
	int count = max > AGENT_TOOL_COUNT ? AGENT_TOOL_COUNT : max;

	if (version != AGENT_CALL_VERSION_V2)
		return AGENT_STATUS_BAD_VERSION;
	if (desc_size != sizeof(desc))
		return AGENT_STATUS_BAD_SIZE;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (count && user_range_check(pagetable, address,
				      (uint64)count * sizeof(desc), PTE_W) < 0)
		return -1;
	for (int i = 0; i < count; i++) {
		memset(&desc, 0, sizeof(desc));
		desc.version = AGENT_CALL_VERSION_V2;
		desc.size = sizeof(desc);
		desc.tool_id = agent_tools[i].tool_id;
		desc.param_count = rule_count(i + 1);
		desc.flags = agent_tools[i].flags;
		safestrcpy(desc.name, agent_tools[i].name, sizeof(desc.name));
		if (agent_tool_schema(i + 1, desc.params,
				      sizeof(desc.params)) < 0)
			return -1;
		safestrcpy(desc.description, agent_tools[i].description,
			   sizeof(desc.description));
		if (copyout(pagetable, address + (uint64)i * sizeof(desc),
			    (char *)&desc, sizeof(desc)) < 0)
			return -1;
	}
	return AGENT_TOOL_COUNT;
}
