#include <agent.h>
#include <agent_nexus.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

_Static_assert(sizeof(struct agent_nexus_artifact_header) +
		       AGENT_NEXUS_ARTIFACT_MAX <=
	       AGENT_FILE_PUBLISH_MAX_BYTES,
	       "Nexus artifact must fit one atomic publish snapshot");

struct nexus_sha256_context {
	unsigned int state[8];
	unsigned long long bit_count;
	unsigned char block[64];
	unsigned int used;
};

static struct agent_tool_desc_v2 nexus_tool_catalog[AGENT_TOOL_COUNT];
static struct agent_param_v2 nexus_tool_params[AGENT_TOOL_PARAM_MAX];
static int nexus_tool_catalog_count;
static struct agent_nexus_artifact_actor nexus_registered_identity;
static struct agent_nexus_artifact_actor nexus_registry_coordinator;
static int nexus_identity_registered;
static int nexus_identity_registry_ready;

#define NX_ROLE(role) AGENT_NEXUS_TOOL_ROLE(AGENT_NEXUS_ROLE_##role)
#define NX_COORD NX_ROLE(COORDINATOR)
#define NX_SYSTEM NX_ROLE(SYSTEM)
#define NX_RESEARCH NX_ROLE(RESEARCH)
#define NX_RELAY NX_ROLE(RELAY)
#define NX_ALL (NX_COORD | NX_SYSTEM | NX_RESEARCH | NX_RELAY)
#define NX_SPEC(id, roles, caps, effects, tool_name, schema, use, avoid, result) \
	{ id, roles, caps, effects, tool_name, use, avoid, schema, result, \
	  "structured Agent status; BAD_PARAM, DENIED, NOT_FOUND or TIMEOUT" }

static const struct agent_nexus_tool_spec nexus_tool_specs[AGENT_TOOL_COUNT] = {
	NX_SPEC(AGENT_TOOL_ECHO, NX_ALL, 0, 0, "echo",
		"payload:string,arg0:uint64,arg1:uint64", "verify typed transport",
		"do not treat as external evidence", "echoed values and payload"),
	NX_SPEC(AGENT_TOOL_PID_INFO, NX_ALL, 0, 0, "pid_info", "none",
		"inspect the current Agent identity", "not for another pid",
		"pid, agent id, Agent flag"),
	NX_SPEC(AGENT_TOOL_CTX_STAT, NX_ALL, 0, 0, "ctx_stat", "none",
		"inspect current Context counters", "not for Context contents",
		"Context base, size, call count"),
	NX_SPEC(AGENT_TOOL_QUERY_PROCESS, NX_COORD | NX_SYSTEM,
		AGENT_CAP_PROCESS_READ, 0, "query_process", "type?:uint64",
		"count process classes", "not for process mutation",
		"process and Agent counts"),
	NX_SPEC(AGENT_TOOL_GET_SYSTEM_STATUS, NX_COORD | NX_SYSTEM,
		AGENT_CAP_PROCESS_READ, 0, "get_system_status", "none",
		"capture a bounded system snapshot", "not a benchmark",
		"process count, Agent count, tick"),
	NX_SPEC(AGENT_TOOL_READ_CONTEXT, NX_COORD | NX_SYSTEM | NX_RESEARCH,
		0, 0, "read_context", "none",
		"read current Context counters", "not cross-Agent content",
		"post-state Context counters"),
	NX_SPEC(AGENT_TOOL_QUERY_FILE, NX_COORD | NX_SYSTEM | NX_RESEARCH,
		AGENT_CAP_META_READ, 0, "query_file", "path:string",
		"query one workflow path or a structured metadata selector",
		"not for file contents or unrestricted filesystem traversal",
		"path: inode type/inum/size; selector: hits/scanned/index flags and first match"),
	NX_SPEC(AGENT_TOOL_SEND_MESSAGE, NX_ALL, AGENT_CAP_MESSAGE_SEND,
		AGENT_SIDE_EFFECT_IPC, "send_message",
		"target_pid:uint64,message:string", "route a bounded TASK capsule",
		"not for unapproved external publication", "delivery receipt"),
	NX_SPEC(AGENT_TOOL_READ_MESSAGE, NX_ALL, 0, 0, "read_message", "none",
		"inspect current mailbox", "prefer agent_wait for blocking",
		"one bounded message"),
	NX_SPEC(AGENT_TOOL_FILE_META_INIT, NX_COORD, AGENT_CAP_META_WRITE,
		AGENT_SIDE_EFFECT_METADATA, "file_meta_init", "none",
		"initialize metadata once", "not a worker operation", "status"),
	NX_SPEC(AGENT_TOOL_READ_FILE_SUMMARY, NX_COORD | NX_RESEARCH,
		AGENT_CAP_CONTENT_READ, 0, "read_file_summary", "selector:string",
		"read an indexed summary", "not for secrets", "bounded summary"),
	NX_SPEC(AGENT_TOOL_DEPENDENCY_QUERY, NX_COORD | NX_SYSTEM | NX_RESEARCH,
		AGENT_CAP_META_READ, 0, "dependency_query", "label:string",
		"inspect registered dependencies", "not for mutation",
		"dependency counters"),
	NX_SPEC(AGENT_TOOL_CAPABILITY_CHECK, NX_COORD | NX_SYSTEM, 0, 0,
		"capability_check", "role:uint64,action:string",
		"check whether the current Agent identity may perform an action",
		"the role argument is compatibility metadata, not impersonation or approval",
		"allowed flag, current kernel role and current capability mask"),
	NX_SPEC(AGENT_TOOL_RERUN_STAGE, NX_COORD, AGENT_CAP_ACTION_WRITE,
		AGENT_SIDE_EFFECT_METADATA, "rerun_stage",
		"role?:uint64,stage:string", "legacy controlled state update",
		"not a general shell", "updated state"),
	NX_SPEC(AGENT_TOOL_WRITE_REPORT, 0,
		AGENT_CAP_ARTIFACT_WRITE, AGENT_SIDE_EFFECT_ARTIFACT,
		"write_report", "role?:uint64,payload:string",
		"legacy platform compatibility entry",
		"not exposed to any Nexus product role", "legacy state"),
	NX_SPEC(AGENT_TOOL_AGENT_WATCH, NX_ALL, AGENT_CAP_WATCH,
		AGENT_SIDE_EFFECT_WATCH, "agent_watch",
		"event_type:uint64,filter:string", "register an event interest",
		"not for busy polling", "watch receipt"),
	NX_SPEC(AGENT_TOOL_AGENT_WAIT, NX_ALL, 0, 0, "agent_wait",
		"timeout:uint64", "sleep for a watched event",
		"syscall-only; not callable through V2", "event or timeout"),
	NX_SPEC(AGENT_TOOL_AGENT_HEARTBEAT, NX_ALL, 0, 0, "agent_heartbeat",
		"interval:uint64", "maintain liveness", "not a task result", "status"),
	NX_SPEC(AGENT_TOOL_CONTEXT_PUSH, NX_ALL, 0, 0, "context_push",
		"record:string", "append verified local context",
		"syscall-only; never copy hidden reasoning", "Context sequence"),
	NX_SPEC(AGENT_TOOL_READ_FILE_DIGEST, NX_COORD | NX_RESEARCH,
		AGENT_CAP_CONTENT_READ, 0, "read_file_digest", "selector:string",
		"verify bounded file content", "not for unrestricted traversal",
		"preview, full size, hashed byte count and non-cryptographic u64 digest"),
	NX_SPEC(AGENT_TOOL_ACTION_COMMIT, NX_COORD, AGENT_CAP_ACTION_WRITE,
		AGENT_SIDE_EFFECT_METADATA, "action_commit",
		"role?:uint64,selector:string", "commit an approved metadata action",
		"not an internal read", "commit receipt"),
	NX_SPEC(AGENT_TOOL_ARTIFACT_UPDATE, NX_COORD,
		AGENT_CAP_ARTIFACT_WRITE, AGENT_SIDE_EFFECT_ARTIFACT,
		"artifact_update", "role?:uint64,selector:string",
		"update scoped artifact metadata", "does not write artifact bytes",
		"state"),
	NX_SPEC(AGENT_TOOL_LLM_REQUEST, NX_COORD, AGENT_CAP_MESSAGE_SEND,
		AGENT_SIDE_EFFECT_IPC, "llm_request",
		"target_pid:uint64,prompt_summary:string", "route model work to relay",
		"never expose as a model-selected tool", "pending request receipt"),
	NX_SPEC(AGENT_TOOL_LLM_RESPONSE, NX_RELAY, AGENT_CAP_LLM_RELAY,
		AGENT_SIDE_EFFECT_IPC, "llm_response",
		"target_pid:uint64,reply_summary:string", "wake the model owner",
		"relay transport only", "completion receipt"),
	NX_SPEC(AGENT_TOOL_DEPENDENCY_UPDATE, NX_COORD,
		AGENT_CAP_DEPENDENCY_UPDATE, AGENT_SIDE_EFFECT_METADATA,
		"dependency_update", "selector:string", "update a scoped dependency",
		"not a worker read", "updated dependency"),
};

_Static_assert(sizeof(nexus_tool_specs) / sizeof(nexus_tool_specs[0]) ==
	       AGENT_TOOL_COUNT, "Nexus tool policy coverage");

#undef NX_SPEC
#undef NX_ALL
#undef NX_RELAY
#undef NX_RESEARCH
#undef NX_SYSTEM
#undef NX_COORD
#undef NX_ROLE

static int nexus_bytes_equal(const void *left, const void *right,
			     unsigned int length)
{
	const unsigned char *a = left;
	const unsigned char *b = right;
	unsigned char difference = 0;

	for (unsigned int i = 0; i < length; i++)
		difference |= a[i] ^ b[i];
	return difference == 0;
}

static int nexus_text_equal_bounded(const char *left, const char *right,
				    unsigned int capacity)
{
	unsigned int left_length;
	unsigned int right_length;

	if (left == 0 || right == 0 || capacity == 0)
		return 0;
	left_length = strnlen(left, capacity);
	right_length = strnlen(right, capacity);
	return left_length < capacity && right_length < capacity &&
	       left_length == right_length &&
	       nexus_bytes_equal(left, right, left_length);
}

static void nexus_copy_text(char *destination, unsigned int capacity,
			    const char *source)
{
	unsigned int length = 0;

	if (capacity == 0)
		return;
	while (source[length] && length + 1U < capacity) {
		destination[length] = source[length];
		length++;
	}
	destination[length] = 0;
}

static unsigned int nexus_rotr(unsigned int value, unsigned int shift)
{
	return (value >> shift) | (value << (32U - shift));
}

static void __attribute__((noinline))
nexus_sha_transform(struct nexus_sha256_context *ctx,
		    const unsigned char block[64])
{
	static const unsigned int constants[64] = {
		0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
		0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
		0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
		0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
		0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
		0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
		0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
		0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
		0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
		0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
		0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
		0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
		0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
		0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
		0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
		0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
	};
	unsigned int words[64];
	unsigned int a, b, c, d, e, f, g, h;

	for (unsigned int i = 0; i < 16; i++)
		words[i] = ((unsigned int)block[i * 4] << 24) |
			   ((unsigned int)block[i * 4 + 1] << 16) |
			   ((unsigned int)block[i * 4 + 2] << 8) |
			   block[i * 4 + 3];
	for (unsigned int i = 16; i < 64; i++) {
		unsigned int s0 = nexus_rotr(words[i - 15], 7) ^
			nexus_rotr(words[i - 15], 18) ^
			(words[i - 15] >> 3);
		unsigned int s1 = nexus_rotr(words[i - 2], 17) ^
			nexus_rotr(words[i - 2], 19) ^
			(words[i - 2] >> 10);

		words[i] = words[i - 16] + s0 + words[i - 7] + s1;
	}
	a = ctx->state[0];
	b = ctx->state[1];
	c = ctx->state[2];
	d = ctx->state[3];
	e = ctx->state[4];
	f = ctx->state[5];
	g = ctx->state[6];
	h = ctx->state[7];
	for (unsigned int i = 0; i < 64; i++) {
		unsigned int s1 = nexus_rotr(e, 6) ^ nexus_rotr(e, 11) ^
			nexus_rotr(e, 25);
		unsigned int choose = (e & f) ^ (~e & g);
		unsigned int t1 = h + s1 + choose + constants[i] + words[i];
		unsigned int s0 = nexus_rotr(a, 2) ^ nexus_rotr(a, 13) ^
			nexus_rotr(a, 22);
		unsigned int majority = (a & b) ^ (a & c) ^ (b & c);
		unsigned int t2 = s0 + majority;

		h = g;
		g = f;
		f = e;
		e = d + t1;
		d = c;
		c = b;
		b = a;
		a = t1 + t2;
	}
	ctx->state[0] += a;
	ctx->state[1] += b;
	ctx->state[2] += c;
	ctx->state[3] += d;
	ctx->state[4] += e;
	ctx->state[5] += f;
	ctx->state[6] += g;
	ctx->state[7] += h;
}

static void nexus_sha_init(struct nexus_sha256_context *ctx)
{
	static const unsigned int initial[8] = {
		0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
		0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
	};

	memset(ctx, 0, sizeof(*ctx));
	memcpy(ctx->state, initial, sizeof(initial));
}

static void nexus_sha_update(struct nexus_sha256_context *ctx,
			     const void *data, unsigned int length)
{
	const unsigned char *bytes = data;

	ctx->bit_count += (unsigned long long)length * 8ULL;
	while (length != 0) {
		unsigned int take = 64U - ctx->used;

		if (take > length)
			take = length;
		memcpy(ctx->block + ctx->used, bytes, take);
		ctx->used += take;
		bytes += take;
		length -= take;
		if (ctx->used == 64U) {
			nexus_sha_transform(ctx, ctx->block);
			ctx->used = 0;
		}
	}
}

static void nexus_sha_final(struct nexus_sha256_context *ctx,
			    unsigned char digest[AGENT_NEXUS_SHA256_SIZE])
{
	unsigned long long bits = ctx->bit_count;
	unsigned char byte = 0x80U;
	unsigned char encoded[8];

	nexus_sha_update(ctx, &byte, 1);
	byte = 0;
	while (ctx->used != 56U)
		nexus_sha_update(ctx, &byte, 1);
	for (unsigned int i = 0; i < 8; i++)
		encoded[7U - i] = (unsigned char)(bits >> (i * 8U));
	nexus_sha_update(ctx, encoded, sizeof(encoded));
	for (unsigned int i = 0; i < 8; i++) {
		digest[i * 4] = (unsigned char)(ctx->state[i] >> 24);
		digest[i * 4 + 1] = (unsigned char)(ctx->state[i] >> 16);
		digest[i * 4 + 2] = (unsigned char)(ctx->state[i] >> 8);
		digest[i * 4 + 3] = (unsigned char)ctx->state[i];
	}
}

void agent_nexus_sha256(const void *data, unsigned int length,
			unsigned char digest[AGENT_NEXUS_SHA256_SIZE])
{
	struct nexus_sha256_context ctx;

	nexus_sha_init(&ctx);
	nexus_sha_update(&ctx, data, length);
	nexus_sha_final(&ctx, digest);
}

void agent_nexus_sha256_hex(
	const unsigned char digest[AGENT_NEXUS_SHA256_SIZE],
	char text[AGENT_NEXUS_SHA256_HEX_SIZE + 1])
{
	static const char hex[] = "0123456789abcdef";

	for (unsigned int i = 0; i < AGENT_NEXUS_SHA256_SIZE; i++) {
		text[i * 2] = hex[digest[i] >> 4];
		text[i * 2 + 1] = hex[digest[i] & 15U];
	}
	text[AGENT_NEXUS_SHA256_HEX_SIZE] = 0;
}

static void nexus_put_u32(unsigned char *out, unsigned int value)
{
	for (unsigned int i = 0; i < 4; i++)
		out[i] = (unsigned char)(value >> (i * 8U));
}

static void nexus_put_u64(unsigned char *out, unsigned long long value)
{
	for (unsigned int i = 0; i < 8; i++)
		out[i] = (unsigned char)(value >> (i * 8U));
}

static unsigned int nexus_get_u32(const unsigned char *in)
{
	unsigned int value = 0;

	for (unsigned int i = 0; i < 4; i++)
		value |= (unsigned int)in[i] << (i * 8U);
	return value;
}

static unsigned long long nexus_get_u64(const unsigned char *in)
{
	unsigned long long value = 0;

	for (unsigned int i = 0; i < 8; i++)
		value |= (unsigned long long)in[i] << (i * 8U);
	return value;
}

static unsigned int nexus_base64_encode(const unsigned char *input,
					unsigned int length, char *output,
					unsigned int capacity)
{
	static const char alphabet[] =
		"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
	unsigned int needed = (length / 3U) * 4U +
		(length % 3U == 0 ? 0U : length % 3U + 1U);
	unsigned int in = 0;
	unsigned int out = 0;

	if (needed + 1U > capacity)
		return 0;
	while (in + 3U <= length) {
		unsigned int value = ((unsigned int)input[in] << 16) |
			((unsigned int)input[in + 1] << 8) | input[in + 2];

		output[out++] = alphabet[(value >> 18) & 63U];
		output[out++] = alphabet[(value >> 12) & 63U];
		output[out++] = alphabet[(value >> 6) & 63U];
		output[out++] = alphabet[value & 63U];
		in += 3U;
	}
	if (length - in == 1U) {
		unsigned int value = (unsigned int)input[in] << 16;

		output[out++] = alphabet[(value >> 18) & 63U];
		output[out++] = alphabet[(value >> 12) & 63U];
	} else if (length - in == 2U) {
		unsigned int value = ((unsigned int)input[in] << 16) |
			((unsigned int)input[in + 1] << 8);

		output[out++] = alphabet[(value >> 18) & 63U];
		output[out++] = alphabet[(value >> 12) & 63U];
		output[out++] = alphabet[(value >> 6) & 63U];
	}
	output[out] = 0;
	return out;
}

static int nexus_base64_value(char value)
{
	if (value >= 'A' && value <= 'Z')
		return value - 'A';
	if (value >= 'a' && value <= 'z')
		return value - 'a' + 26;
	if (value >= '0' && value <= '9')
		return value - '0' + 52;
	if (value == '-')
		return 62;
	if (value == '_')
		return 63;
	return -1;
}

static int nexus_base64_decode(const char *input, unsigned int length,
			       unsigned char *output, unsigned int capacity,
			       unsigned int *output_length)
{
	unsigned int cursor = 0;
	unsigned int written = 0;

	if (length == 0 || length % 4U == 1U)
		return -1;
	while (cursor + 4U <= length) {
		int a = nexus_base64_value(input[cursor]);
		int b = nexus_base64_value(input[cursor + 1]);
		int c = nexus_base64_value(input[cursor + 2]);
		int d = nexus_base64_value(input[cursor + 3]);
		unsigned int value;

		if (a < 0 || b < 0 || c < 0 || d < 0 || written + 3U > capacity)
			return -1;
		value = ((unsigned int)a << 18) | ((unsigned int)b << 12) |
			((unsigned int)c << 6) | (unsigned int)d;
		output[written++] = (unsigned char)(value >> 16);
		output[written++] = (unsigned char)(value >> 8);
		output[written++] = (unsigned char)value;
		cursor += 4U;
	}
	if (length - cursor == 2U) {
		int a = nexus_base64_value(input[cursor]);
		int b = nexus_base64_value(input[cursor + 1]);

		if (a < 0 || b < 0 || (b & 15) != 0 || written + 1U > capacity)
			return -1;
		output[written++] = (unsigned char)(((unsigned int)a << 2) |
			((unsigned int)b >> 4));
	} else if (length - cursor == 3U) {
		int a = nexus_base64_value(input[cursor]);
		int b = nexus_base64_value(input[cursor + 1]);
		int c = nexus_base64_value(input[cursor + 2]);

		if (a < 0 || b < 0 || c < 0 || (c & 3) != 0 ||
		    written + 2U > capacity)
			return -1;
		output[written++] = (unsigned char)(((unsigned int)a << 2) |
			((unsigned int)b >> 4));
		output[written++] = (unsigned char)(((unsigned int)b << 4) |
			((unsigned int)c >> 2));
	} else if (cursor != length) {
		return -1;
	}
	*output_length = written;
	return 0;
}

static int nexus_task_type_valid(int task_type)
{
	switch (task_type) {
	case AGENT_NEXUS_TASK_INSPECT_SYSTEM:
	case AGENT_NEXUS_TASK_INSPECT_PROCESSES:
	case AGENT_NEXUS_TASK_INSPECT_CONTEXT:
	case AGENT_NEXUS_TASK_SEARCH_FILES:
	case AGENT_NEXUS_TASK_READ_FILE:
	case AGENT_NEXUS_TASK_USER_TURN:
	case AGENT_NEXUS_TASK_MODEL_REQUEST:
	case AGENT_NEXUS_TASK_APPROVAL:
	case AGENT_NEXUS_TASK_SESSION_CLOSE:
	case AGENT_NEXUS_TASK_INFRA_READY:
		return 1;
	default:
		return 0;
	}
}

static int nexus_system_task_type(int task_type)
{
	return task_type == AGENT_NEXUS_TASK_INSPECT_SYSTEM ||
	       task_type == AGENT_NEXUS_TASK_INSPECT_PROCESSES ||
	       task_type == AGENT_NEXUS_TASK_INSPECT_CONTEXT;
}

int agent_nexus_task_validate(const struct agent_nexus_task *task)
{
	if (task == 0 || task->kind < AGENT_NEXUS_TASK_ASSIGN ||
	    task->kind > AGENT_NEXUS_TASK_CANCEL ||
	    task->state < AGENT_NEXUS_TASK_STATE_ASSIGNED ||
	    task->state > AGENT_NEXUS_TASK_STATE_CANCELLED ||
	    (task->flags & ~AGENT_NEXUS_TASK_F_KNOWN_MASK) != 0 ||
	    task->reserved != 0 || task->lifecycle_id == 0 ||
	    task->lifecycle_generation == 0 || task->deadline_tick == 0)
		return 0;
	if (task->kind == AGENT_NEXUS_TASK_ASSIGN &&
	    nexus_system_task_type(task->status)) {
		if (task->flags != (AGENT_NEXUS_TASK_F_HAS_INPUT |
				    AGENT_NEXUS_TASK_F_HAS_SECONDARY) ||
		    (task->value0 == 0 && task->value1 == 0))
			return 0;
	} else if (task->kind != AGENT_NEXUS_TASK_PROGRESS) {
		unsigned int value1_flags = task->flags &
			(AGENT_NEXUS_TASK_F_HAS_SECONDARY |
			 AGENT_NEXUS_TASK_F_HAS_RESULT);

		if (((task->flags & AGENT_NEXUS_TASK_F_HAS_INPUT) != 0) !=
		    (task->value0 != 0))
			return 0;
		if (task->kind == AGENT_NEXUS_TASK_ASSIGN) {
			if (value1_flags == (AGENT_NEXUS_TASK_F_HAS_SECONDARY |
					     AGENT_NEXUS_TASK_F_HAS_RESULT) ||
			    ((value1_flags != 0) != (task->value1 != 0)))
				return 0;
		} else if (((task->flags & AGENT_NEXUS_TASK_F_HAS_RESULT) != 0) !=
			   (task->value1 != 0) ||
			   (task->flags & AGENT_NEXUS_TASK_F_HAS_SECONDARY) != 0) {
			return 0;
		}
	}
	switch (task->kind) {
	case AGENT_NEXUS_TASK_ASSIGN:
		return task->state == AGENT_NEXUS_TASK_STATE_ASSIGNED &&
		       (task->flags & AGENT_NEXUS_TASK_F_FINAL) == 0 &&
		       nexus_task_type_valid(task->status);
	case AGENT_NEXUS_TASK_ACCEPT:
		return task->state == AGENT_NEXUS_TASK_STATE_ACCEPTED &&
		       task->flags == 0 && task->status == AGENT_STATUS_OK;
	case AGENT_NEXUS_TASK_PROGRESS:
		return (task->state == AGENT_NEXUS_TASK_STATE_RUNNING ||
			task->state == AGENT_NEXUS_TASK_STATE_WAITING) &&
		       task->flags == 0 && task->status == AGENT_STATUS_OK &&
		       task->value0 != 0;
	case AGENT_NEXUS_TASK_RESULT:
		return task->state == AGENT_NEXUS_TASK_STATE_COMPLETED &&
		       (task->flags & AGENT_NEXUS_TASK_F_FINAL) != 0 &&
		       task->status == AGENT_STATUS_OK;
	case AGENT_NEXUS_TASK_FAILED:
		return task->state == AGENT_NEXUS_TASK_STATE_FAILED &&
		       (task->flags & AGENT_NEXUS_TASK_F_FINAL) != 0 &&
		       task->status < AGENT_STATUS_OK;
	default:
		return task->state == AGENT_NEXUS_TASK_STATE_CANCELLED &&
		       task->flags == AGENT_NEXUS_TASK_F_FINAL &&
		       task->status == AGENT_STATUS_CANCELLED;
	}
}

int agent_nexus_task_validate_runtime(
	const struct agent_nexus_task *task,
	const struct agent_workflow_lifecycle_key *expected_lifecycle,
	unsigned int current_tick)
{
	unsigned int delta;

	if (!agent_nexus_task_validate(task) || expected_lifecycle == 0 ||
	    task->lifecycle_id != expected_lifecycle->id ||
	    task->lifecycle_generation != expected_lifecycle->generation)
		return 0;
	delta = task->deadline_tick - current_tick;
	return delta != 0 && delta <= AGENT_NEXUS_TASK_MAX_DEADLINE_DELTA;
}

int agent_nexus_task_transition_validate(
	const struct agent_nexus_task *previous,
	const struct agent_nexus_task *next)
{
	if (!agent_nexus_task_validate(previous) ||
	    !agent_nexus_task_validate(next) ||
	    previous->lifecycle_id != next->lifecycle_id ||
	    previous->lifecycle_generation != next->lifecycle_generation ||
	    previous->parent_task_id != next->parent_task_id ||
	    previous->deadline_tick != next->deadline_tick ||
	    previous->kind == AGENT_NEXUS_TASK_RESULT ||
	    previous->kind == AGENT_NEXUS_TASK_FAILED ||
	    previous->kind == AGENT_NEXUS_TASK_CANCEL)
		return 0;
	if (next->kind == AGENT_NEXUS_TASK_CANCEL ||
	    next->kind == AGENT_NEXUS_TASK_FAILED)
		return 1;
	if (previous->kind == AGENT_NEXUS_TASK_ASSIGN)
		return next->kind == AGENT_NEXUS_TASK_ACCEPT;
	if (previous->kind == AGENT_NEXUS_TASK_ACCEPT)
		return next->kind == AGENT_NEXUS_TASK_PROGRESS ||
		       next->kind == AGENT_NEXUS_TASK_RESULT;
	return next->kind == AGENT_NEXUS_TASK_PROGRESS ||
	       next->kind == AGENT_NEXUS_TASK_RESULT;
}

int agent_nexus_task_encode(const struct agent_nexus_task *task,
			    char text[AGENT_EVENT_PAYLOAD_SIZE])
{
	unsigned char wire[AGENT_NEXUS_TASK_WIRE_SIZE];
	unsigned int encoded;

	if (text == 0 || !agent_nexus_task_validate(task))
		return -1;
	memset(wire, 0, sizeof(wire));
	nexus_put_u32(wire + AGENT_NEXUS_TASK_OFF_MAGIC,
		      AGENT_NEXUS_TASK_MAGIC);
	wire[AGENT_NEXUS_TASK_OFF_VERSION] = AGENT_NEXUS_TASK_VERSION;
	wire[AGENT_NEXUS_TASK_OFF_KIND] = task->kind;
	wire[AGENT_NEXUS_TASK_OFF_STATE] = task->state;
	wire[AGENT_NEXUS_TASK_OFF_FLAGS] = task->flags;
	nexus_put_u64(wire + AGENT_NEXUS_TASK_OFF_LIFECYCLE_ID,
		      task->lifecycle_id);
	nexus_put_u64(wire + AGENT_NEXUS_TASK_OFF_LIFECYCLE_GENERATION,
		      task->lifecycle_generation);
	nexus_put_u32(wire + AGENT_NEXUS_TASK_OFF_PARENT_TASK_ID,
		      task->parent_task_id);
	nexus_put_u32(wire + AGENT_NEXUS_TASK_OFF_DEADLINE_TICK,
		      task->deadline_tick);
	nexus_put_u32(wire + AGENT_NEXUS_TASK_OFF_STATUS,
		      (unsigned int)task->status);
	nexus_put_u32(wire + AGENT_NEXUS_TASK_OFF_VALUE0, task->value0);
	nexus_put_u32(wire + AGENT_NEXUS_TASK_OFF_VALUE1, task->value1);
	memcpy(text, AGENT_NEXUS_TASK_PREFIX, AGENT_NEXUS_TASK_PREFIX_SIZE);
	encoded = nexus_base64_encode(
		wire, sizeof(wire), text + AGENT_NEXUS_TASK_PREFIX_SIZE,
		AGENT_EVENT_PAYLOAD_SIZE - AGENT_NEXUS_TASK_PREFIX_SIZE);
	return encoded == AGENT_NEXUS_TASK_B64_SIZE ? 0 : -1;
}

int agent_nexus_task_decode_n(const char *text, unsigned int length,
			      struct agent_nexus_task *task)
{
	unsigned char wire[AGENT_NEXUS_TASK_WIRE_SIZE];
	char canonical[AGENT_EVENT_PAYLOAD_SIZE];
	unsigned int decoded = 0;

	if (text == 0 || task == 0 || length != AGENT_NEXUS_TASK_TEXT_SIZE ||
	    strncmp(text, AGENT_NEXUS_TASK_PREFIX,
		    AGENT_NEXUS_TASK_PREFIX_SIZE) != 0 ||
	    nexus_base64_decode(text + AGENT_NEXUS_TASK_PREFIX_SIZE,
			AGENT_NEXUS_TASK_B64_SIZE, wire, sizeof(wire),
			&decoded) < 0 || decoded != sizeof(wire) ||
	    nexus_get_u32(wire + AGENT_NEXUS_TASK_OFF_MAGIC) !=
		    AGENT_NEXUS_TASK_MAGIC ||
	    wire[AGENT_NEXUS_TASK_OFF_VERSION] != AGENT_NEXUS_TASK_VERSION)
		return -1;
	memset(task, 0, sizeof(*task));
	task->kind = wire[AGENT_NEXUS_TASK_OFF_KIND];
	task->state = wire[AGENT_NEXUS_TASK_OFF_STATE];
	task->flags = wire[AGENT_NEXUS_TASK_OFF_FLAGS];
	task->lifecycle_id =
		nexus_get_u64(wire + AGENT_NEXUS_TASK_OFF_LIFECYCLE_ID);
	task->lifecycle_generation = nexus_get_u64(
		wire + AGENT_NEXUS_TASK_OFF_LIFECYCLE_GENERATION);
	task->parent_task_id =
		nexus_get_u32(wire + AGENT_NEXUS_TASK_OFF_PARENT_TASK_ID);
	task->deadline_tick =
		nexus_get_u32(wire + AGENT_NEXUS_TASK_OFF_DEADLINE_TICK);
	task->status = (int)nexus_get_u32(wire + AGENT_NEXUS_TASK_OFF_STATUS);
	task->value0 = nexus_get_u32(wire + AGENT_NEXUS_TASK_OFF_VALUE0);
	task->value1 = nexus_get_u32(wire + AGENT_NEXUS_TASK_OFF_VALUE1);
	if (!agent_nexus_task_validate(task) ||
	    agent_nexus_task_encode(task, canonical) < 0 ||
	    !nexus_bytes_equal(text, canonical, AGENT_NEXUS_TASK_TEXT_SIZE))
		return -1;
	return 0;
}

int agent_nexus_task_decode(const char *text, struct agent_nexus_task *task)
{
	unsigned int length;

	if (text == 0)
		return -1;
	length = strnlen(text, AGENT_EVENT_PAYLOAD_SIZE);
	if (length == AGENT_EVENT_PAYLOAD_SIZE)
		return -1;
	return agent_nexus_task_decode_n(text, length, task);
}

int agent_nexus_tools_discover(void)
{
	unsigned char seen[AGENT_TOOL_COUNT];
	int count;

	memset(nexus_tool_catalog, 0, sizeof(nexus_tool_catalog));
	memset(seen, 0, sizeof(seen));
	nexus_tool_catalog_count = 0;
	count = tool_list(nexus_tool_catalog, AGENT_TOOL_COUNT);
	if (count != AGENT_TOOL_COUNT)
		return -1;
	for (int i = 0; i < count; i++) {
		const struct agent_tool_desc_v2 *descriptor =
			&nexus_tool_catalog[i];
		const struct agent_nexus_tool_spec *spec;

		if (descriptor->version != AGENT_CALL_VERSION_V2 ||
		    nexus_tool_catalog[i].size != sizeof(nexus_tool_catalog[i]) ||
		    descriptor->tool_id <= 0 ||
		    descriptor->tool_id > AGENT_TOOL_COUNT ||
		    strnlen(descriptor->name, sizeof(descriptor->name)) ==
			    sizeof(descriptor->name) ||
		    strnlen(descriptor->params, sizeof(descriptor->params)) ==
			    sizeof(descriptor->params) ||
		    seen[descriptor->tool_id - 1] != 0)
			goto invalid;
		spec = &nexus_tool_specs[descriptor->tool_id - 1];
		if (spec->tool_id != descriptor->tool_id ||
		    !nexus_text_equal_bounded(spec->name, descriptor->name,
					      AGENT_TOOL_NAME_SIZE) ||
		    !nexus_text_equal_bounded(spec->parameters,
					      descriptor->params,
					      AGENT_TOOL_PARAMS_SIZE))
			goto invalid;
		seen[descriptor->tool_id - 1] = 1;
	}
	nexus_tool_catalog_count = count;
	return count;

invalid:
	memset(nexus_tool_catalog, 0, sizeof(nexus_tool_catalog));
	return -1;
}

const struct agent_tool_desc_v2 *agent_nexus_tool_find(const char *name)
{
	if (name == 0 || strnlen(name, AGENT_TOOL_NAME_SIZE) ==
			       AGENT_TOOL_NAME_SIZE)
		return 0;
	if (nexus_tool_catalog_count != AGENT_TOOL_COUNT &&
	    agent_nexus_tools_discover() < 0)
		return 0;
	for (int i = 0; i < nexus_tool_catalog_count; i++)
		if (nexus_text_equal_bounded(name, nexus_tool_catalog[i].name,
					     AGENT_TOOL_NAME_SIZE))
			return &nexus_tool_catalog[i];
	return 0;
}

const struct agent_nexus_tool_spec *agent_nexus_tool_spec_find(
	const char *name)
{
	if (name == 0 || strnlen(name, AGENT_TOOL_NAME_SIZE) ==
			       AGENT_TOOL_NAME_SIZE)
		return 0;
	for (unsigned int i = 0; i < AGENT_TOOL_COUNT; i++)
		if (nexus_text_equal_bounded(name, nexus_tool_specs[i].name,
					     AGENT_TOOL_NAME_SIZE))
			return &nexus_tool_specs[i];
	return 0;
}

static int nexus_tool_runtime_only(int tool_id)
{
	switch (tool_id) {
	case AGENT_TOOL_SEND_MESSAGE:
	case AGENT_TOOL_READ_MESSAGE:
	case AGENT_TOOL_FILE_META_INIT:
	case AGENT_TOOL_AGENT_WATCH:
	case AGENT_TOOL_AGENT_WAIT:
	case AGENT_TOOL_AGENT_HEARTBEAT:
	case AGENT_TOOL_CONTEXT_PUSH:
	case AGENT_TOOL_LLM_REQUEST:
	case AGENT_TOOL_LLM_RESPONSE:
		return 1;
	default:
		return 0;
	}
}

int agent_nexus_tool_views_for_role_class(
	unsigned int product_role, unsigned int view_class,
	struct agent_nexus_tool_view *views, int max)
{
	unsigned int role_bit;
	unsigned long long capabilities;
	int count = 0;

	if (max < 0 || (max != 0 && views == 0) ||
	    (view_class != AGENT_NEXUS_TOOL_VIEW_READ_ONLY &&
	     view_class != AGENT_NEXUS_TOOL_VIEW_EFFECTS) ||
	    agent_nexus_product_kernel_role(product_role) == 0)
		return -1;
	if (nexus_tool_catalog_count != AGENT_TOOL_COUNT &&
	    agent_nexus_tools_discover() < 0)
		return -1;
	role_bit = AGENT_NEXUS_TOOL_ROLE(product_role);
	capabilities = agent_nexus_product_capabilities(product_role);
	for (int i = 0; i < nexus_tool_catalog_count; i++) {
		const struct agent_tool_desc_v2 *descriptor =
			&nexus_tool_catalog[i];
		const struct agent_nexus_tool_spec *spec =
			&nexus_tool_specs[descriptor->tool_id - 1];

		if ((descriptor->flags & AGENT_TOOL_F_CALLABLE) == 0 ||
		    nexus_tool_runtime_only(descriptor->tool_id) ||
		    (spec->product_role_mask & role_bit) == 0 ||
		    (capabilities & spec->required_capabilities) !=
			    spec->required_capabilities ||
		    (view_class == AGENT_NEXUS_TOOL_VIEW_READ_ONLY ?
			    spec->side_effect_mask != 0 :
			    spec->side_effect_mask == 0))
			continue;
		if (count < max) {
			views[count].descriptor = descriptor;
			views[count].spec = spec;
		}
		count++;
	}
	return max == 0 || count <= max ? count : max;
}

int agent_nexus_tool_views_for_role(
	unsigned int product_role, struct agent_nexus_tool_view *views, int max)
{
	return agent_nexus_tool_views_for_role_class(
		product_role, AGENT_NEXUS_TOOL_VIEW_READ_ONLY, views, max);
}

static int nexus_schema_arguments_valid(
	const struct agent_nexus_tool_spec *spec,
	const struct agent_nexus_tool_argument *arguments,
	unsigned int argument_count)
{
	const char *cursor = spec->parameters;
	unsigned int argument_index = 0;

	if (nexus_text_equal_bounded(cursor, "none",
				     AGENT_TOOL_PARAMS_SIZE))
		return argument_count == 0;
	while (*cursor) {
		const char *key = cursor;
		const char *type;
		unsigned int key_length = 0;
		unsigned int type_length = 0;
		int optional = 0;
		int matched = 0;

		while (cursor[key_length] && cursor[key_length] != '?' &&
		       cursor[key_length] != ':' &&
		       cursor[key_length] != ',')
			key_length++;
		if (key_length == 0)
			return 0;
		cursor += key_length;
		if (*cursor == '?') {
			optional = 1;
			cursor++;
		}
		if (*cursor != ':')
			return 0;
		type = ++cursor;
		while (type[type_length] && type[type_length] != ',')
			type_length++;
		if (argument_index < argument_count) {
			unsigned int actual_key_length = strnlen(
				arguments[argument_index].key,
				sizeof(arguments[argument_index].key));

			matched = actual_key_length <
					  sizeof(arguments[argument_index].key) &&
				  actual_key_length == key_length &&
				  nexus_bytes_equal(arguments[argument_index].key,
						    key, key_length);
			if (matched) {
				if (arguments[argument_index].type ==
				    AGENT_PARAM_UINT64) {
					if (type_length != 6U ||
					    !nexus_bytes_equal(type, "uint64", 6))
						return 0;
				} else if (arguments[argument_index].type ==
					   AGENT_PARAM_STRING) {
					if (type_length != 6U ||
					    !nexus_bytes_equal(type, "string", 6) ||
					    strnlen(arguments[argument_index].text,
						    sizeof(arguments[argument_index].text)) ==
						    sizeof(arguments[argument_index].text))
						return 0;
				} else {
					return 0;
				}
				argument_index++;
			}
		}
		if (!matched && !optional)
			return 0;
		cursor = type + type_length;
		if (*cursor == ',')
			cursor++;
	}
	return argument_index == argument_count;
}

int agent_nexus_tool_call(const char *name, unsigned long long request_id,
			  const struct agent_nexus_tool_argument *arguments,
			  unsigned int argument_count,
			  struct agent_response_v2 *response)
{
	const struct agent_tool_desc_v2 *tool = agent_nexus_tool_find(name);
	const struct agent_nexus_tool_spec *spec = agent_nexus_tool_spec_find(name);
	struct agent_request_v2 request;
	struct agent_nexus_artifact_actor identity;
	struct agent_info info;
	unsigned int role_bit;

	if (tool == 0 || spec == 0 || response == 0 || request_id == 0 ||
	    argument_count > AGENT_TOOL_PARAM_MAX ||
	    (argument_count != 0 && arguments == 0) ||
	    (tool->flags & AGENT_TOOL_F_CALLABLE) == 0 ||
	    argument_count > tool->param_count ||
	    !nexus_schema_arguments_valid(spec, arguments, argument_count))
		return -1;
	if (agent_nexus_identity_current(&identity) < 0)
		return -1;
	role_bit = AGENT_NEXUS_TOOL_ROLE(identity.product_role);
	memset(&info, 0, sizeof(info));
	if (agent_info(&info) != 0 || !info.is_agent ||
	    info.agent_id != (int)identity.agent_id ||
	    (spec->product_role_mask & role_bit) == 0 ||
	    (info.capability_mask & spec->required_capabilities) !=
		    spec->required_capabilities)
		return -1;
	memset(&request, 0, sizeof(request));
	memset(response, 0, sizeof(*response));
	memset(nexus_tool_params, 0, sizeof(nexus_tool_params));
	request.version = AGENT_CALL_VERSION_V2;
	request.size = sizeof(request);
	request.tool_id = tool->tool_id;
	request.param_count = argument_count;
	request.request_id = request_id;
	request.params = argument_count ?
		(unsigned long long)nexus_tool_params : 0;
	strcpy(request.tool_name, tool->name);
	for (unsigned int i = 0; i < argument_count; i++) {
		struct agent_param_v2 *param = &nexus_tool_params[i];

		param->version = AGENT_PARAM_VERSION;
		param->size = sizeof(*param);
		param->type = arguments[i].type;
		strcpy(param->key, arguments[i].key);
		if (arguments[i].type == AGENT_PARAM_UINT64) {
			param->value_size = sizeof(unsigned long long);
			param->value.uint64_value = arguments[i].number;
		} else {
			unsigned int length = strnlen(arguments[i].text,
						 sizeof(arguments[i].text)) + 1U;

			param->value_size = length;
			strcpy(param->value.string_value, arguments[i].text);
		}
	}
	if (tool_call(&request, response) != 0 ||
	    response->version != AGENT_CALL_VERSION_V2 ||
	    response->size != sizeof(*response) ||
	    response->request_id != request_id || response->tool_id != tool->tool_id ||
	    strnlen(response->tool_name, sizeof(response->tool_name)) ==
		    sizeof(response->tool_name) ||
	    !nexus_text_equal_bounded(response->tool_name, tool->name,
				      AGENT_TOOL_NAME_SIZE))
		return -1;
	return 0;
}

int agent_nexus_tool_call_as(
	unsigned int product_role, const char *name,
	unsigned long long request_id,
	const struct agent_nexus_tool_argument *arguments,
	unsigned int argument_count, struct agent_response_v2 *response)
{
	const struct agent_nexus_tool_spec *spec =
		agent_nexus_tool_spec_find(name);
	struct agent_nexus_artifact_actor identity;
	struct agent_info info;
	unsigned int role_bit;

	if (spec == 0 || agent_nexus_product_kernel_role(product_role) == 0 ||
	    agent_nexus_identity_current(&identity) < 0 ||
	    identity.product_role != product_role)
		return -1;
	role_bit = AGENT_NEXUS_TOOL_ROLE(product_role);
	memset(&info, 0, sizeof(info));
	if (agent_info(&info) != 0 || !info.is_agent ||
	    info.agent_role != agent_nexus_product_kernel_role(product_role) ||
	    (spec->product_role_mask & role_bit) == 0 ||
	    (info.capability_mask & spec->required_capabilities) !=
		    spec->required_capabilities)
		return -1;
	return agent_nexus_tool_call(name, request_id, arguments,
				     argument_count, response);
}

int agent_nexus_task_send(int target_pid, unsigned long long task_id,
			  const struct agent_nexus_task *task,
			  struct agent_response_v2 *response)
{
	struct agent_nexus_tool_argument arguments[2];
	char message[AGENT_EVENT_PAYLOAD_SIZE];

	if (target_pid <= 0 || task_id == 0 || response == 0 ||
	    agent_nexus_task_encode(task, message) < 0)
		return -1;
	memset(arguments, 0, sizeof(arguments));
	strcpy(arguments[0].key, "target_pid");
	arguments[0].type = AGENT_PARAM_UINT64;
	arguments[0].number = (unsigned long long)target_pid;
	strcpy(arguments[1].key, "message");
	arguments[1].type = AGENT_PARAM_STRING;
	strcpy(arguments[1].text, message);
	if (agent_nexus_tool_call("send_message", task_id, arguments, 2,
				  response) < 0)
		return -1;
	return response->status;
}

static char nexus_hex(unsigned int value)
{
	return value < 10U ? (char)('0' + value) : (char)('a' + value - 10U);
}

int agent_nexus_product_kernel_role(unsigned int product_role)
{
	switch (product_role) {
	case AGENT_NEXUS_ROLE_COORDINATOR:
	case AGENT_NEXUS_ROLE_RELAY:
		return AGENT_ROLE_ORCHESTRATOR;
	case AGENT_NEXUS_ROLE_SYSTEM:
		return AGENT_ROLE_SENTINEL;
	case AGENT_NEXUS_ROLE_RESEARCH:
		return AGENT_ROLE_INVESTIGATOR;
	default:
		return 0;
	}
}

unsigned long long agent_nexus_product_capabilities(
	unsigned int product_role)
{
	switch (product_role) {
	case AGENT_NEXUS_ROLE_SYSTEM:
		return AGENT_CAP_META_READ | AGENT_CAP_PROCESS_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_AUDIT_WRITE;
	case AGENT_NEXUS_ROLE_RESEARCH:
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_AUDIT_WRITE;
	case AGENT_NEXUS_ROLE_COORDINATOR:
	case AGENT_NEXUS_ROLE_RELAY:
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
		       AGENT_CAP_WATCH | AGENT_CAP_ACTION_WRITE |
		       AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_AUDIT_WRITE |
		       AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE |
		       AGENT_CAP_LLM_RELAY | AGENT_CAP_WAIT_CANCEL |
		       AGENT_CAP_ROUTE_MANAGE;
	default:
		return 0;
	}
}

unsigned long long agent_nexus_product_permission(
	unsigned int product_role)
{
	switch (product_role) {
	case AGENT_NEXUS_ROLE_COORDINATOR:
	case AGENT_NEXUS_ROLE_SYSTEM:
	case AGENT_NEXUS_ROLE_RESEARCH:
	case AGENT_NEXUS_ROLE_RELAY:
		return 1ULL << (product_role - 1U);
	default:
		return 0;
	}
}

unsigned int agent_nexus_artifact_handle_make(
	unsigned long long lifecycle_generation, unsigned int slot)
{
	unsigned int generation;

	if (lifecycle_generation == 0 || slot == 0 ||
	    slot > AGENT_NEXUS_ARTIFACT_SLOTS)
		return 0;
	generation = (unsigned int)lifecycle_generation & 0xffffU;
	if (generation == 0)
		return 0;
	return AGENT_NEXUS_ARTIFACT_HANDLE(generation, slot);
}

int agent_nexus_artifact_handle_validate(
	unsigned int handle, unsigned long long lifecycle_generation,
	unsigned int *slot)
{
	unsigned int expected;
	unsigned int actual_slot = AGENT_NEXUS_ARTIFACT_SLOT(handle);

	expected = agent_nexus_artifact_handle_make(lifecycle_generation,
						   actual_slot);
	if (expected == 0 || expected != handle)
		return -1;
	if (slot != 0)
		*slot = actual_slot;
	return 0;
}

int agent_nexus_artifact_path(unsigned int handle,
			      char path[AGENT_NEXUS_ARTIFACT_PATH_SIZE])
{
	unsigned int generation = AGENT_NEXUS_ARTIFACT_GENERATION(handle);
	unsigned int slot = AGENT_NEXUS_ARTIFACT_SLOT(handle);

	if (path == 0 || generation == 0 || slot == 0 ||
	    slot > AGENT_NEXUS_ARTIFACT_SLOTS)
		return -1;
	path[0] = 'n';
	path[1] = 'x';
	for (unsigned int i = 0; i < 4; i++)
		path[2 + i] = nexus_hex((generation >> ((3U - i) * 4U)) & 15U);
	for (unsigned int i = 0; i < 4; i++)
		path[6 + i] = nexus_hex((slot >> ((3U - i) * 4U)) & 15U);
	path[10] = 0;
	return 0;
}

static int nexus_read_all(int fd, void *data, unsigned int length)
{
	char *bytes = data;

	while (length != 0) {
		ssize_t got = read(fd, bytes, length);

		if (got <= 0)
			return -1;
		bytes += got;
		length -= (unsigned int)got;
	}
	return 0;
}

static int nexus_actor_shape_valid(
	const struct agent_nexus_artifact_actor *actor)
{
	int kernel_role;

	if (actor == 0 || actor->control_id == 0 || actor->pid == 0 ||
	    actor->agent_id == 0)
		return 0;
	kernel_role = agent_nexus_product_kernel_role(actor->product_role);
	return kernel_role != 0 &&
	       actor->kernel_role == (unsigned int)kernel_role;
}

static int nexus_actor_equal(const struct agent_nexus_artifact_actor *left,
			     const struct agent_nexus_artifact_actor *right)
{
	return left->control_id == right->control_id && left->pid == right->pid &&
	       left->agent_id == right->agent_id &&
	       left->kernel_role == right->kernel_role &&
	       left->product_role == right->product_role;
}

static void nexus_identity_discard_inherited(void)
{
	if (nexus_identity_registered &&
	    nexus_registered_identity.pid != (unsigned int)getpid()) {
		memset(&nexus_registered_identity, 0,
		       sizeof(nexus_registered_identity));
		nexus_identity_registered = 0;
	}
}

int agent_nexus_identity_registry_init(
	unsigned long long coordinator_control_id)
{
	struct agent_nexus_artifact_actor coordinator;
	struct agent_info info;

	if (coordinator_control_id == 0 || getpid() <= 0)
		return -1;
	memset(&info, 0, sizeof(info));
	if (agent_info(&info) != 0 || !info.is_agent || info.agent_id <= 0 ||
	    info.agent_role != AGENT_ROLE_ORCHESTRATOR)
		return -1;
	memset(&coordinator, 0, sizeof(coordinator));
	coordinator.control_id = coordinator_control_id;
	coordinator.pid = (unsigned int)getpid();
	coordinator.agent_id = (unsigned int)info.agent_id;
	coordinator.kernel_role = (unsigned int)info.agent_role;
	coordinator.product_role = AGENT_NEXUS_ROLE_COORDINATOR;
	if (nexus_identity_registry_ready)
		return nexus_actor_equal(&nexus_registry_coordinator,
					 &coordinator) ? 0 : -1;
	nexus_registry_coordinator = coordinator;
	nexus_identity_registry_ready = 1;
	return 0;
}

int agent_nexus_identity_register(unsigned int product_role,
				  unsigned long long control_id)
{
	struct agent_info info;
	int expected_role = agent_nexus_product_kernel_role(product_role);

	if (!nexus_identity_registry_ready || expected_role == 0 || getpid() <= 0)
		return -1;
	nexus_identity_discard_inherited();
	memset(&info, 0, sizeof(info));
	if (agent_info(&info) != 0 || !info.is_agent || info.agent_id <= 0 ||
	    info.agent_role != expected_role)
		return -1;
	if (product_role == AGENT_NEXUS_ROLE_COORDINATOR) {
		if (control_id == 0 ||
		    nexus_registry_coordinator.control_id != control_id ||
		    nexus_registry_coordinator.pid != (unsigned int)getpid() ||
		    nexus_registry_coordinator.agent_id !=
			    (unsigned int)info.agent_id)
			return -1;
	} else if (product_role == AGENT_NEXUS_ROLE_RELAY &&
		   nexus_registry_coordinator.pid == (unsigned int)getpid()) {
		return -1;
	}
	if (nexus_identity_registered) {
		if (nexus_registered_identity.product_role != product_role ||
		    nexus_registered_identity.agent_id !=
			    (unsigned int)info.agent_id ||
		    (control_id != 0 &&
		     nexus_registered_identity.control_id != 0 &&
		     nexus_registered_identity.control_id != control_id))
			return -1;
		if (control_id != 0)
			nexus_registered_identity.control_id = control_id;
		return 0;
	}
	memset(&nexus_registered_identity, 0,
	       sizeof(nexus_registered_identity));
	nexus_registered_identity.control_id = control_id;
	nexus_registered_identity.pid = (unsigned int)getpid();
	nexus_registered_identity.agent_id = (unsigned int)info.agent_id;
	nexus_registered_identity.kernel_role = (unsigned int)info.agent_role;
	nexus_registered_identity.product_role = product_role;
	nexus_identity_registered = 1;
	return 0;
}

int agent_nexus_identity_bind_control(unsigned long long control_id)
{
	nexus_identity_discard_inherited();
	if (!nexus_identity_registered || control_id == 0 ||
	    (nexus_registered_identity.control_id != 0 &&
	     nexus_registered_identity.control_id != control_id))
		return -1;
	nexus_registered_identity.control_id = control_id;
	return 0;
}

int agent_nexus_identity_current(struct agent_nexus_artifact_actor *actor)
{
	struct agent_info info;

	if (actor == 0)
		return -1;
	nexus_identity_discard_inherited();
	memset(&info, 0, sizeof(info));
	if (!nexus_identity_registered || agent_info(&info) != 0 ||
	    !info.is_agent || info.agent_id <= 0 ||
	    nexus_registered_identity.pid != (unsigned int)getpid() ||
	    nexus_registered_identity.agent_id != (unsigned int)info.agent_id ||
	    nexus_registered_identity.kernel_role !=
		    (unsigned int)info.agent_role)
		return -1;
	*actor = nexus_registered_identity;
	return 0;
}

int agent_nexus_artifact_actor_current(
	unsigned int product_role, unsigned long long control_id,
	struct agent_nexus_artifact_actor *actor)
{
	if (agent_nexus_identity_current(actor) < 0 ||
	    actor->product_role != product_role ||
	    (control_id != 0 && actor->control_id != control_id))
		return -1;
	return 0;
}

static int nexus_artifact_kind_valid(unsigned int kind)
{
	return kind >= AGENT_NEXUS_ARTIFACT_TOOL_INPUT &&
	       kind <= AGENT_NEXUS_ARTIFACT_APPROVAL &&
	       kind != AGENT_NEXUS_ARTIFACT_RESERVED_7;
}

static unsigned long long nexus_artifact_required_provenance(
	const struct agent_nexus_artifact_manifest *manifest)
{
	unsigned long long required;

	switch (manifest->source) {
	case AGENT_NEXUS_SOURCE_SEED:
		required = AGENT_PROVENANCE_UNTRUSTED_FILE_DATA;
		break;
	case AGENT_NEXUS_SOURCE_KERNEL_TOOL:
		required = AGENT_PROVENANCE_KERNEL_FACT |
			   AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;
		break;
	case AGENT_NEXUS_SOURCE_WORKER_METRIC:
		required = AGENT_PROVENANCE_AGENT_DERIVED |
			   AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT |
			   AGENT_PROVENANCE_CROSS_AGENT_DATA;
		break;
	case AGENT_NEXUS_SOURCE_MODEL:
		required = AGENT_PROVENANCE_AGENT_DERIVED |
			   AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;
		break;
	case AGENT_NEXUS_SOURCE_USER:
		required = AGENT_PROVENANCE_TRUSTED_USER_CONTROL;
		break;
	case AGENT_NEXUS_SOURCE_DERIVED:
		required = AGENT_PROVENANCE_AGENT_DERIVED;
		break;
	default:
		return AGENT_PROVENANCE_ALL + 1ULL;
	}
	if ((manifest->flags & AGENT_NEXUS_ARTIFACT_F_BROKERED) != 0)
		required |= AGENT_PROVENANCE_AGENT_DERIVED |
			    AGENT_PROVENANCE_CROSS_AGENT_DATA;
	return required;
}

int agent_nexus_artifact_manifest_validate(
	const struct agent_nexus_artifact_manifest *manifest)
{
	unsigned long long owner_permission;
	unsigned long long required_provenance;

	if (manifest == 0 || manifest->lifecycle.id == 0 ||
	    manifest->lifecycle.reserved != 0 ||
	    manifest->lifecycle.generation == 0 || manifest->task_id == 0 ||
	    (manifest->flags & ~AGENT_NEXUS_ARTIFACT_F_KNOWN_MASK) != 0 ||
	    (manifest->flags & AGENT_NEXUS_ARTIFACT_F_PUBLISHED) == 0 ||
	    !nexus_actor_shape_valid(&manifest->producer) ||
	    !nexus_actor_shape_valid(&manifest->owner) ||
	    !nexus_actor_shape_valid(&manifest->materializer) ||
	    !nexus_artifact_kind_valid(manifest->kind) ||
	    manifest->source < AGENT_NEXUS_SOURCE_SEED ||
	    manifest->source > AGENT_NEXUS_SOURCE_DERIVED ||
	    manifest->reserved != 0 || manifest->provenance_labels == 0 ||
	    (manifest->provenance_labels & ~AGENT_PROVENANCE_ALL) != 0 ||
	    manifest->permission_mask == 0 ||
	    (manifest->permission_mask & ~AGENT_NEXUS_ARTIFACT_READ_ALL) != 0 ||
	    agent_nexus_artifact_handle_validate(
		    manifest->handle, manifest->lifecycle.generation, 0) < 0)
		return 0;
	required_provenance = nexus_artifact_required_provenance(manifest);
	if ((manifest->provenance_labels & required_provenance) !=
	    required_provenance)
		return 0;
	owner_permission = agent_nexus_product_permission(
		manifest->owner.product_role);
	return owner_permission != 0 &&
	       (manifest->permission_mask & owner_permission) != 0;
}

static int nexus_manifest_current_materializer(
	const struct agent_nexus_artifact_manifest *manifest)
{
	struct agent_nexus_artifact_actor current;
	struct agent_workflow_lifecycle_info lifecycle;

	if (agent_nexus_artifact_actor_current(
		    manifest->materializer.product_role,
		    manifest->materializer.control_id, &current) < 0 ||
	    !nexus_actor_equal(&current, &manifest->materializer))
		return 0;
	memset(&lifecycle, 0, sizeof(lifecycle));
	lifecycle.version = AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION;
	lifecycle.struct_size = sizeof(lifecycle);
	return agent_workflow_lifecycle_info(&lifecycle,
					     &manifest->lifecycle) == 0;
}

static int nexus_owned_manifest_valid(
	const struct agent_nexus_artifact_manifest *manifest)
{
	if (!agent_nexus_artifact_manifest_validate(manifest) ||
	    manifest->flags != AGENT_NEXUS_ARTIFACT_F_PUBLISHED ||
	    !nexus_actor_equal(&manifest->producer, &manifest->owner) ||
	    !nexus_actor_equal(&manifest->owner, &manifest->materializer) ||
	    !nexus_manifest_current_materializer(manifest))
		return 0;
	if (manifest->materializer.product_role == AGENT_NEXUS_ROLE_COORDINATOR)
		return manifest->kind == AGENT_NEXUS_ARTIFACT_TOOL_INPUT ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_MODEL_REQUEST ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_TASK_CAPSULE ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_APPROVAL;
	if (manifest->materializer.product_role == AGENT_NEXUS_ROLE_RELAY)
		return manifest->kind == AGENT_NEXUS_ARTIFACT_MODEL_RESPONSE ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_APPROVAL;
	return 0;
}

static int nexus_brokered_manifest_valid(
	const struct agent_nexus_artifact_manifest *manifest)
{
	if (!agent_nexus_artifact_manifest_validate(manifest) ||
	    manifest->flags != (AGENT_NEXUS_ARTIFACT_F_BROKERED |
				AGENT_NEXUS_ARTIFACT_F_PUBLISHED) ||
	    manifest->materializer.product_role !=
		    AGENT_NEXUS_ROLE_COORDINATOR ||
	    !nexus_actor_equal(&manifest->owner, &manifest->materializer) ||
	    !nexus_manifest_current_materializer(manifest))
		return 0;
	if (manifest->producer.product_role == AGENT_NEXUS_ROLE_SYSTEM)
		return manifest->kind == AGENT_NEXUS_ARTIFACT_SYSTEM_RESULT;
	if (manifest->producer.product_role == AGENT_NEXUS_ROLE_RESEARCH)
		return manifest->kind == AGENT_NEXUS_ARTIFACT_RESEARCH_RESULT;
	return 0;
}

static int nexus_manifest_relationship_valid(
	const struct agent_nexus_artifact_manifest *manifest)
{
	if ((manifest->flags & AGENT_NEXUS_ARTIFACT_F_BROKERED) != 0) {
		if (manifest->flags != (AGENT_NEXUS_ARTIFACT_F_BROKERED |
				       AGENT_NEXUS_ARTIFACT_F_PUBLISHED) ||
		    manifest->materializer.product_role !=
			    AGENT_NEXUS_ROLE_COORDINATOR ||
		    !nexus_actor_equal(&manifest->owner,
				       &manifest->materializer))
			return 0;
		return (manifest->producer.product_role ==
				AGENT_NEXUS_ROLE_SYSTEM &&
			manifest->kind == AGENT_NEXUS_ARTIFACT_SYSTEM_RESULT) ||
		       (manifest->producer.product_role ==
				AGENT_NEXUS_ROLE_RESEARCH &&
			manifest->kind == AGENT_NEXUS_ARTIFACT_RESEARCH_RESULT);
	}
	if (manifest->flags != AGENT_NEXUS_ARTIFACT_F_PUBLISHED ||
	    !nexus_actor_equal(&manifest->producer, &manifest->owner) ||
	    !nexus_actor_equal(&manifest->owner, &manifest->materializer))
		return 0;
	if (manifest->materializer.product_role == AGENT_NEXUS_ROLE_COORDINATOR)
		return manifest->kind == AGENT_NEXUS_ARTIFACT_TOOL_INPUT ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_MODEL_REQUEST ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_TASK_CAPSULE ||
		       manifest->kind == AGENT_NEXUS_ARTIFACT_APPROVAL;
	return manifest->materializer.product_role == AGENT_NEXUS_ROLE_RELAY &&
	       (manifest->kind == AGENT_NEXUS_ARTIFACT_MODEL_RESPONSE ||
		manifest->kind == AGENT_NEXUS_ARTIFACT_APPROVAL);
}

static void nexus_header_from_manifest(
	struct agent_nexus_artifact_header *header,
	const struct agent_nexus_artifact_manifest *manifest,
	unsigned int payload_size)
{
	memset(header, 0, sizeof(*header));
	header->magic = AGENT_NEXUS_ARTIFACT_MAGIC;
	header->version = AGENT_NEXUS_ARTIFACT_VERSION;
	header->header_size = sizeof(*header);
	header->lifecycle_id = manifest->lifecycle.id;
	header->handle = manifest->handle;
	header->handle_generation = AGENT_NEXUS_ARTIFACT_GENERATION(
		manifest->handle);
	header->handle_slot = AGENT_NEXUS_ARTIFACT_SLOT(manifest->handle);
	header->flags = manifest->flags;
	header->lifecycle_generation = manifest->lifecycle.generation;
	header->producer = manifest->producer;
	header->owner = manifest->owner;
	header->materializer = manifest->materializer;
	header->task_id = manifest->task_id;
	header->parent_task_id = manifest->parent_task_id;
	header->payload_size = payload_size;
	header->kind = manifest->kind;
	header->source = manifest->source;
	header->provenance_labels = manifest->provenance_labels;
	header->permission_mask = manifest->permission_mask;
}

static void nexus_manifest_from_header(
	struct agent_nexus_artifact_manifest *manifest,
	const struct agent_nexus_artifact_header *header)
{
	memset(manifest, 0, sizeof(*manifest));
	manifest->lifecycle.id = header->lifecycle_id;
	manifest->lifecycle.generation = header->lifecycle_generation;
	manifest->handle = header->handle;
	manifest->flags = header->flags;
	manifest->producer = header->producer;
	manifest->owner = header->owner;
	manifest->materializer = header->materializer;
	manifest->task_id = header->task_id;
	manifest->parent_task_id = header->parent_task_id;
	manifest->kind = header->kind;
	manifest->source = header->source;
	manifest->provenance_labels = header->provenance_labels;
	manifest->permission_mask = header->permission_mask;
}

static int nexus_artifact_store(
	struct agent_nexus_artifact_header *stored, const void *payload,
	unsigned int payload_size)
{
	struct agent_nexus_artifact_header digest_header;
	char path[AGENT_NEXUS_ARTIFACT_PATH_SIZE];
	int publish_status;
	struct agent_nexus_artifact_header existing_header;
	unsigned char chunk[64];
	const unsigned char *expected = payload;
	unsigned int offset = 0;
	char extra;
	ssize_t tail;
	int close_status;
	int fd;

	if (payload == 0 || payload_size == 0 ||
	    payload_size > AGENT_NEXUS_ARTIFACT_MAX ||
	    agent_nexus_artifact_path(stored->handle, path) < 0)
		return -1;
	agent_nexus_sha256(payload, payload_size, stored->payload_sha256);
	memset(stored->manifest_sha256, 0, sizeof(stored->manifest_sha256));
	digest_header = *stored;
	agent_nexus_sha256(&digest_header, sizeof(digest_header),
			   stored->manifest_sha256);
	publish_status = agent_file_publish(
		path, stored, sizeof(*stored), payload, payload_size);
	if (publish_status == AGENT_STATUS_OK)
		return 0;
	if (publish_status != AGENT_STATUS_DUPLICATE &&
	    publish_status != AGENT_STATUS_INDETERMINATE)
		return -1;

	/* A duplicate or indeterminate attach converges only when the official
	 * path contains the exact requested bytes. */
	fd = open(path, O_RDONLY);

	if (fd < 0 ||
	    nexus_read_all(fd, &existing_header, sizeof(existing_header)) < 0 ||
	    !nexus_bytes_equal(&existing_header, stored, sizeof(*stored)))
		goto mismatch;
	while (offset < payload_size) {
		unsigned int take = payload_size - offset;

		if (take > sizeof(chunk))
			take = sizeof(chunk);
		if (nexus_read_all(fd, chunk, take) < 0 ||
		    !nexus_bytes_equal(chunk, expected + offset, take))
			goto mismatch;
		offset += take;
	}
	tail = read(fd, &extra, 1);
	close_status = close(fd);
	return tail == 0 && close_status == 0 ? 0 : -1;

mismatch:
	if (fd >= 0)
		(void)close(fd);
	return -1;
}

static int nexus_artifact_publish(
	const struct agent_nexus_artifact_manifest *manifest,
	const void *payload, unsigned int payload_size,
	struct agent_nexus_artifact_header *published, int brokered)
{
	struct agent_nexus_artifact_header stored;

	if (published == 0 ||
	    (brokered ? !nexus_brokered_manifest_valid(manifest) :
			!nexus_owned_manifest_valid(manifest)))
		return -1;
	nexus_header_from_manifest(&stored, manifest, payload_size);
	if (nexus_artifact_store(&stored, payload, payload_size) < 0)
		return -1;
	*published = stored;
	return 0;
}

int agent_nexus_artifact_publish_owned(
	const struct agent_nexus_artifact_manifest *manifest,
	const void *payload, unsigned int payload_size,
	struct agent_nexus_artifact_header *published)
{
	return nexus_artifact_publish(manifest, payload, payload_size,
				    published, 0);
}

int agent_nexus_artifact_materialize_brokered(
	const struct agent_nexus_artifact_manifest *manifest,
	const void *payload, unsigned int payload_size,
	struct agent_nexus_artifact_header *published)
{
	return nexus_artifact_publish(manifest, payload, payload_size,
				    published, 1);
}

int agent_nexus_artifact_write(
	const struct agent_nexus_artifact_header *header, const void *payload,
	unsigned int payload_size)
{
	struct agent_nexus_artifact_manifest manifest;
	struct agent_nexus_artifact_header published;

	if (header == 0 || header->magic != AGENT_NEXUS_ARTIFACT_MAGIC ||
	    header->version != AGENT_NEXUS_ARTIFACT_VERSION ||
	    header->header_size != sizeof(*header) ||
	    header->payload_size != payload_size ||
	    header->handle_generation !=
		    AGENT_NEXUS_ARTIFACT_GENERATION(header->handle) ||
	    header->handle_slot != AGENT_NEXUS_ARTIFACT_SLOT(header->handle))
		return -1;
	nexus_manifest_from_header(&manifest, header);
	return nexus_artifact_publish(
		&manifest, payload, payload_size, &published,
		(manifest.flags & AGENT_NEXUS_ARTIFACT_F_BROKERED) != 0);
}

unsigned long long agent_nexus_role_permission(int role)
{
	if (role == AGENT_ROLE_ORCHESTRATOR)
		return AGENT_NEXUS_ARTIFACT_READ_COORDINATOR |
		       AGENT_NEXUS_ARTIFACT_READ_RELAY;
	if (role == AGENT_ROLE_SENTINEL)
		return AGENT_NEXUS_ARTIFACT_READ_SYSTEM;
	if (role == AGENT_ROLE_INVESTIGATOR)
		return AGENT_NEXUS_ARTIFACT_READ_RESEARCH;
	return 0;
}

int agent_nexus_artifact_read_verify(
	unsigned int handle,
	const struct agent_workflow_lifecycle_key *expected_lifecycle,
	const struct agent_nexus_artifact_actor *reader,
	unsigned int expected_kind,
	struct agent_nexus_artifact_header *header,
	void *payload, unsigned int capacity, unsigned int *payload_size)
{
	struct agent_nexus_artifact_header digest_header;
	struct agent_nexus_artifact_manifest manifest;
	struct agent_nexus_artifact_actor current;
	struct agent_workflow_lifecycle_info lifecycle;
	unsigned char digest[AGENT_NEXUS_SHA256_SIZE];
	char path[AGENT_NEXUS_ARTIFACT_PATH_SIZE];
	char extra;
	ssize_t tail;
	int close_status;
	int fd = -1;

	if (expected_lifecycle == 0 || reader == 0 || header == 0 ||
	    payload == 0 || payload_size == 0 || capacity == 0 ||
	    (expected_kind != 0 && !nexus_artifact_kind_valid(expected_kind)) ||
	    agent_nexus_artifact_path(handle, path) < 0 ||
	    agent_nexus_artifact_actor_current(
		    reader->product_role, reader->control_id, &current) < 0 ||
	    !nexus_actor_equal(&current, reader))
		return -1;
	memset(&lifecycle, 0, sizeof(lifecycle));
	lifecycle.version = AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION;
	lifecycle.struct_size = sizeof(lifecycle);
	if (agent_workflow_lifecycle_info(&lifecycle, expected_lifecycle) != 0)
		return -1;
	memset(header, 0, sizeof(*header));
	*payload_size = 0;
	fd = open(path, O_RDONLY);
	if (fd < 0 || nexus_read_all(fd, header, sizeof(*header)) < 0)
		goto fail;
	nexus_manifest_from_header(&manifest, header);
	if (header->magic != AGENT_NEXUS_ARTIFACT_MAGIC ||
	    header->version != AGENT_NEXUS_ARTIFACT_VERSION ||
	    header->header_size != sizeof(*header) || header->handle != handle ||
	    header->handle_generation != AGENT_NEXUS_ARTIFACT_GENERATION(handle) ||
	    header->handle_slot != AGENT_NEXUS_ARTIFACT_SLOT(handle) ||
	    header->lifecycle_id != expected_lifecycle->id ||
	    header->lifecycle_generation != expected_lifecycle->generation ||
	    header->payload_size == 0 || header->payload_size > capacity ||
	    header->payload_size > AGENT_NEXUS_ARTIFACT_MAX ||
	    !agent_nexus_artifact_manifest_validate(&manifest) ||
	    !nexus_manifest_relationship_valid(&manifest) ||
	    (expected_kind != 0 && header->kind != expected_kind) ||
	    (header->permission_mask & agent_nexus_product_permission(
					 reader->product_role)) == 0)
		goto fail;
	if (nexus_read_all(fd, payload, header->payload_size) < 0)
		goto fail;
	tail = read(fd, &extra, 1);
	close_status = close(fd);
	fd = -1;
	if (tail != 0 || close_status < 0)
		return -1;
	agent_nexus_sha256(payload, header->payload_size, digest);
	if (!nexus_bytes_equal(digest, header->payload_sha256, sizeof(digest)))
		return -1;
	digest_header = *header;
	memset(digest_header.manifest_sha256, 0,
	       sizeof(digest_header.manifest_sha256));
	agent_nexus_sha256(&digest_header, sizeof(digest_header), digest);
	if (!nexus_bytes_equal(digest, header->manifest_sha256, sizeof(digest)))
		return -1;
	*payload_size = header->payload_size;
	return 0;

fail:
	if (fd >= 0)
		(void)close(fd);
	return -1;
}

int agent_nexus_artifact_read(
	unsigned int handle,
	const struct agent_workflow_lifecycle_key *expected_lifecycle,
	int reader_role, struct agent_nexus_artifact_header *header,
	void *payload, unsigned int capacity, unsigned int *payload_size)
{
	struct agent_nexus_artifact_actor reader;
	unsigned int product_role;

	if (reader_role == AGENT_ROLE_SENTINEL)
		product_role = AGENT_NEXUS_ROLE_SYSTEM;
	else if (reader_role == AGENT_ROLE_INVESTIGATOR)
		product_role = AGENT_NEXUS_ROLE_RESEARCH;
	else if (reader_role == AGENT_ROLE_ORCHESTRATOR)
		product_role = AGENT_NEXUS_ROLE_COORDINATOR;
	else
		return -1;
	if (agent_nexus_artifact_actor_current(product_role, 0, &reader) < 0)
		return -1;
	return agent_nexus_artifact_read_verify(
		handle, expected_lifecycle, &reader, 0, header, payload, capacity,
		payload_size);
}

int agent_nexus_context_note(unsigned long long task_id, int tool_id,
			     int status, unsigned long long provenance,
			     const char *payload, const char *result,
			     unsigned long long value0,
			     unsigned long long value1,
			     unsigned long long value2)
{
	struct agent_context_record record;

	if (task_id == 0 || payload == 0 || result == 0)
		return -1;
	memset(&record, 0, sizeof(record));
	record.request_id = task_id;
	record.tool_id = tool_id;
	record.status = status;
	record.value0 = value0;
	record.value1 = value1;
	record.value2 = value2;
	record.flags = AGENT_CONTEXT_RECORD_F_MANUAL |
		AGENT_CONTEXT_PROVENANCE_ENCODE(provenance);
	nexus_copy_text(record.payload, sizeof(record.payload), payload);
	nexus_copy_text(record.result, sizeof(record.result), result);
	return context_push(&record);
}
