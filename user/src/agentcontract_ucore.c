#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TEST_NAME "agentcontract_ucore"
#define CHARGE_RESERVED 1U
#define DEADLINE_WAIT_MAX_ITERATIONS 10000U
#define CONTRACT_CREATE_MAX_ITERATIONS 10000U
#define CAPABILITY_SETUP_MAX_ITERATIONS 10000U
#define RESOURCE_REAPER_MAX_ITERATIONS 10000U
#define CAPABILITY_EVENT_TIMEOUT_TICKS 2000
#define CONTRACT_BINDING_A_PREFIX "contract-a:"
#define CONTRACT_BINDING_B_PREFIX "contract-b:"
#define CONTRACT_BINDING_PREFIX_SIZE 11U
#define CONTRACT_BINDING_GENERATION_HEX_SIZE 16U
#define CONTRACT_BINDING_DIGEST_HALF_BYTES 16U
#define CONTRACT_BINDING_DIGEST_HALF_HEX_SIZE 32U

struct sha256_ctx {
	uint state[8];
	uint64 bits;
	unsigned char block[64];
	uint used;
};

static struct agent_execution_contract_node nodes[AGENT_EXECUTION_CONTRACT_MAX_NODES];
static struct agent_execution_contract_node queried[AGENT_EXECUTION_CONTRACT_MAX_NODES];
static struct agent_param_v2 params[AGENT_TOOL_PARAM_MAX];
static uint64 next_request = 71000;

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentcontract_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static uint rotr(uint value, uint shift)
{
	return (value >> shift) | (value << (32 - shift));
}

static void sha256_transform(struct sha256_ctx *ctx,
			     const unsigned char block[64])
{
	static const uint k[64] = {
		0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
		0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
		0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
		0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
		0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
		0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
		0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
		0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
		0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
		0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
		0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
		0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
		0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
		0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
		0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
		0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
	};
	uint w[64];
	uint a, b, c, d, e, f, g, h;

	for (uint i = 0; i < 16; i++)
		w[i] = ((uint)block[i * 4] << 24) |
		       ((uint)block[i * 4 + 1] << 16) |
		       ((uint)block[i * 4 + 2] << 8) | block[i * 4 + 3];
	for (uint i = 16; i < 64; i++) {
		uint s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^
			  (w[i - 15] >> 3);
		uint s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^
			  (w[i - 2] >> 10);
		w[i] = w[i - 16] + s0 + w[i - 7] + s1;
	}
	a = ctx->state[0]; b = ctx->state[1]; c = ctx->state[2];
	d = ctx->state[3]; e = ctx->state[4]; f = ctx->state[5];
	g = ctx->state[6]; h = ctx->state[7];
	for (uint i = 0; i < 64; i++) {
		uint s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
		uint ch = (e & f) ^ (~e & g);
		uint t1 = h + s1 + ch + k[i] + w[i];
		uint s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
		uint maj = (a & b) ^ (a & c) ^ (b & c);
		uint t2 = s0 + maj;
		h = g; g = f; f = e; e = d + t1;
		d = c; c = b; b = a; a = t1 + t2;
	}
	ctx->state[0] += a; ctx->state[1] += b;
	ctx->state[2] += c; ctx->state[3] += d;
	ctx->state[4] += e; ctx->state[5] += f;
	ctx->state[6] += g; ctx->state[7] += h;
}

static void sha256_init(struct sha256_ctx *ctx)
{
	static const uint initial[8] = {
		0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
		0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
	};
	memset(ctx, 0, sizeof(*ctx));
	memcpy(ctx->state, initial, sizeof(initial));
}

static void sha256_update(struct sha256_ctx *ctx, const void *data, uint len)
{
	const unsigned char *bytes = data;

	ctx->bits += (uint64)len * 8;
	while (len != 0) {
		uint take = 64 - ctx->used;
		if (take > len)
			take = len;
		memcpy(ctx->block + ctx->used, bytes, take);
		ctx->used += take;
		bytes += take;
		len -= take;
		if (ctx->used == 64) {
			sha256_transform(ctx, ctx->block);
			ctx->used = 0;
		}
	}
}

static void sha256_final(struct sha256_ctx *ctx, unsigned char out[32])
{
	uint64 bits = ctx->bits;
	unsigned char one = 0x80;
	unsigned char zero = 0;
	unsigned char encoded[8];

	sha256_update(ctx, &one, 1);
	while (ctx->used != 56)
		sha256_update(ctx, &zero, 1);
	for (uint i = 0; i < 8; i++)
		encoded[7 - i] = bits >> (i * 8);
	sha256_update(ctx, encoded, sizeof(encoded));
	for (uint i = 0; i < 8; i++) {
		out[i * 4] = ctx->state[i] >> 24;
		out[i * 4 + 1] = ctx->state[i] >> 16;
		out[i * 4 + 2] = ctx->state[i] >> 8;
		out[i * 4 + 3] = ctx->state[i];
	}
}

static void hash_u64(struct sha256_ctx *ctx, uint64 value)
{
	unsigned char encoded[8];
	for (uint i = 0; i < 8; i++)
		encoded[i] = value >> (i * 8);
	sha256_update(ctx, encoded, sizeof(encoded));
}

static void inline_fingerprint(int tool, uint64 arg0, uint64 arg1,
			       uint64 flags, const char *payload,
			       unsigned char out[32])
{
	static const char domain[] = "agentos.execution.inline-input.v1";
	struct sha256_ctx ctx;
	uint length = strlen(payload);

	sha256_init(&ctx);
	sha256_update(&ctx, domain, sizeof(domain) - 1);
	hash_u64(&ctx, (uint)tool);
	hash_u64(&ctx, arg0);
	hash_u64(&ctx, arg1);
	hash_u64(&ctx, flags);
	hash_u64(&ctx, length);
	sha256_update(&ctx, payload, length);
	sha256_final(&ctx, out);
}

static int digest_nonzero(const unsigned char digest[32])
{
	unsigned char aggregate = 0;

	for (uint i = 0; i < 32; i++)
		aggregate |= digest[i];
	return aggregate != 0;
}

static char hex_digit(uint value)
{
	return value < 10 ? '0' + value : 'a' + value - 10;
}

static int hex_value(char value)
{
	if (value >= '0' && value <= '9')
		return value - '0';
	if (value >= 'a' && value <= 'f')
		return value - 'a' + 10;
	return -1;
}

static void encode_u64_hex(uint64 value, char out[16])
{
	for (uint i = 0; i < CONTRACT_BINDING_GENERATION_HEX_SIZE; i++) {
		out[CONTRACT_BINDING_GENERATION_HEX_SIZE - 1 - i] =
			hex_digit(value & 0xf);
		value >>= 4;
	}
}

static int decode_u64_hex(const char in[16], uint64 *value)
{
	uint64 decoded = 0;

	for (uint i = 0; i < CONTRACT_BINDING_GENERATION_HEX_SIZE; i++) {
		int digit = hex_value(in[i]);

		if (digit < 0)
			return -1;
		decoded = (decoded << 4) | (uint)digit;
	}
	*value = decoded;
	return 0;
}

static void encode_digest_half(const unsigned char *digest, char out[32])
{
	for (uint i = 0; i < CONTRACT_BINDING_DIGEST_HALF_BYTES; i++) {
		out[i * 2] = hex_digit(digest[i] >> 4);
		out[i * 2 + 1] = hex_digit(digest[i] & 0xf);
	}
}

static int decode_digest_half(const char in[32], unsigned char *digest)
{
	for (uint i = 0; i < CONTRACT_BINDING_DIGEST_HALF_BYTES; i++) {
		int high = hex_value(in[i * 2]);
		int low = hex_value(in[i * 2 + 1]);

		if (high < 0 || low < 0)
			return -1;
		digest[i] = (high << 4) | low;
	}
	return 0;
}

static void node_security(struct agent_execution_contract_node *node,
			  int tool)
{
	node->accepted_input_labels = AGENT_PROVENANCE_ALL;
	node->output_add_labels = AGENT_PROVENANCE_AGENT_DERIVED;
	switch (tool) {
	case AGENT_TOOL_QUERY_FILE:
		node->required_capabilities = AGENT_CAP_META_READ;
		node->output_add_labels |= AGENT_PROVENANCE_UNTRUSTED_FILE_DATA;
		break;
	case AGENT_TOOL_SEND_MESSAGE:
		node->required_capabilities = AGENT_CAP_MESSAGE_SEND;
		node->accepted_input_labels = AGENT_PROVENANCE_KERNEL_FACT |
			AGENT_PROVENANCE_TRUSTED_USER_CONTROL |
			AGENT_PROVENANCE_AGENT_DERIVED;
		node->side_effect_mask = AGENT_SIDE_EFFECT_IPC;
		break;
	case AGENT_TOOL_READ_MESSAGE:
		node->output_add_labels |= AGENT_PROVENANCE_CROSS_AGENT_DATA;
		break;
	case AGENT_TOOL_ACTION_COMMIT:
		node->required_capabilities = AGENT_CAP_ACTION_WRITE;
		node->accepted_input_labels = AGENT_PROVENANCE_KERNEL_FACT |
			AGENT_PROVENANCE_TRUSTED_USER_CONTROL |
			AGENT_PROVENANCE_AGENT_DERIVED;
		node->side_effect_mask = AGENT_SIDE_EFFECT_METADATA;
		break;
	}
}

static void node_init(uint index, int tool, uint64 predecessors,
		      uint64 deadline)
{
	struct agent_execution_contract_node *node = &nodes[index];

	memset(node, 0, sizeof(*node));
	node->version = AGENT_EXECUTION_CONTRACT_NODE_VERSION;
	node->size = sizeof(*node);
	node->node_id = index;
	node->tool_id = tool;
	node->predecessor_mask = predecessors;
	node->deadline_tick = deadline;
	node->input_artifact_type = AGENT_ARTIFACT_UTF8;
	node->output_artifact_type = AGENT_ARTIFACT_UTF8;
	node->max_attempts = 1;
	node->cancel_policy = AGENT_EXECUTION_CANCEL_ALLOW;
	node->charge_class = CHARGE_RESERVED;
	node->exec_envelope[AGENT_RESOURCE_PROCESS] = 1;
	node_security(node, tool);
}

static void param_uint(uint index, const char *key, uint64 value)
{
	memset(&params[index], 0, sizeof(params[index]));
	params[index].version = AGENT_PARAM_VERSION;
	params[index].size = sizeof(params[index]);
	params[index].type = AGENT_PARAM_UINT64;
	params[index].value_size = sizeof(uint64);
	strcpy(params[index].key, key);
	params[index].value.uint64_value = value;
}

static void param_string(uint index, const char *key, const char *value)
{
	memset(&params[index], 0, sizeof(params[index]));
	params[index].version = AGENT_PARAM_VERSION;
	params[index].size = sizeof(params[index]);
	params[index].type = AGENT_PARAM_STRING;
	params[index].value_size = strlen(value) + 1;
	strcpy(params[index].key, key);
	strcpy(params[index].value.string_value, value);
}

static void v3_request_init(struct agent_request_v3 *request,
			    const struct agent_execution_contract_key *key,
			    uint node_id, uint attempt, int tool,
			    uint64 arg0, uint64 arg1, const char *payload,
			    uint param_count, uint source_node,
			    uint64 source_sequence)
{
	memset(request, 0, sizeof(*request));
	request->version = AGENT_CALL_VERSION_V3;
	request->size = sizeof(*request);
	request->tool_id = tool;
	request->param_count = param_count;
	request->request_id = ++next_request;
	request->params = param_count ? (uint64)params : 0;
	request->contract = *key;
	request->node_id = node_id;
	request->attempt_id = attempt;
	inline_fingerprint(tool, arg0, arg1, 0, payload,
			   request->input_fingerprint);
	request->source_context_sequence = source_sequence;
	memcpy(request->schema_digest, queried[node_id].schema_digest,
		sizeof(request->schema_digest));
	request->input_artifact_type = queried[node_id].input_artifact_type;
	request->source_node_id = source_node;
}

static int v3_call(struct agent_request_v3 *request,
		   struct agent_response_v3 *response, const char *message)
{
	int rc;

	memset(response, 0, sizeof(*response));
	rc = tool_call_v3(request, response);
	check(rc == 0, message);
	check(response->version == AGENT_CALL_VERSION_V3 &&
	      response->size == sizeof(*response), "v3 response framing");
	return rc;
}

static __attribute__((noinline)) void legacy_echo(void)
{
	struct agent_request_v2 request;
	struct agent_response_v2 response;

	param_string(0, "payload", "legacy-v2");
	param_uint(1, "arg0", 17);
	param_uint(2, "arg1", 19);
	memset(&request, 0, sizeof(request));
	memset(&response, 0, sizeof(response));
	request.version = AGENT_CALL_VERSION_V2;
	request.size = sizeof(request);
	request.tool_id = AGENT_TOOL_ECHO;
	request.param_count = 3;
	request.request_id = ++next_request;
	request.params = (uint64)params;
	check(tool_call(&request, &response) == 0 &&
	      response.status == AGENT_STATUS_OK &&
	      strcmp(response.result, "legacy-v2") == 0,
	      "legacy v2 before enforce");
}

static void contract_control_init(struct agent_execution_contract_control *control,
				  uint operation,
				  struct agent_workflow_lifecycle_key lifecycle)
{
	memset(control, 0, sizeof(*control));
	control->version = AGENT_EXECUTION_CONTRACT_VERSION;
	control->size = sizeof(*control);
	control->operation = operation;
	control->key.lifecycle = lifecycle;
	control->request_id = ++next_request;
}

static void build_nodes(uint64 now)
{
	for (uint i = 0; i < AGENT_EXECUTION_CONTRACT_MAX_NODES; i++)
		node_init(i, AGENT_TOOL_ECHO, 0, 0);
	node_init(0, AGENT_TOOL_SEND_MESSAGE, 0, 0);
	node_init(1, AGENT_TOOL_QUERY_FILE, 1ULL << 0, 0);
	node_init(2, AGENT_TOOL_ACTION_COMMIT, 1ULL << 1, 0);
	node_init(3, AGENT_TOOL_ECHO, 0, 0);
	node_init(5, AGENT_TOOL_ECHO, 0, 0);
	nodes[5].required_capabilities |= AGENT_CAP_LLM_RELAY;
	node_init(6, AGENT_TOOL_ECHO, 0, now + 3);
	nodes[6].max_attempts = 2;
	nodes[6].retry_policy = AGENT_EXECUTION_RETRY_TIMEOUT;
	node_init(7, AGENT_TOOL_ECHO, 0, 0);
	node_init(8, AGENT_TOOL_ECHO, 0, 0);
	nodes[8].max_attempts = 2;
	nodes[8].retry_policy = AGENT_EXECUTION_RETRY_FAILURE;
	nodes[8].exec_envelope[AGENT_RESOURCE_PROCESS] = 0;
	nodes[8].exec_envelope[AGENT_RESOURCE_PHYSICAL_PAGE] = 65535;
	node_init(9, AGENT_TOOL_READ_MESSAGE, 0, 0);
	node_init(10, AGENT_TOOL_SEND_MESSAGE, 1ULL << 9, 0);
	node_init(11, AGENT_TOOL_SEND_MESSAGE, 0, 0);
	node_init(12, AGENT_TOOL_SEND_MESSAGE, 0, 0);
}

static __attribute__((noinline)) void invalid_contracts(
		struct agent_workflow_lifecycle_key lifecycle,
			      uint64 now)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;

	build_nodes(now);
	contract_control_init(&control, AGENT_EXECUTION_CONTRACT_CREATE,
			      lifecycle);
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.nodes = (uint64)nodes;
	control.node_count = AGENT_EXECUTION_CONTRACT_MAX_NODES;
	control.node_size = sizeof(nodes[0]);
	nodes[0].predecessor_mask = 1;
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_BAD_PARAM,
	      "contract rejects non-topological predecessor");

	build_nodes(now);
	contract_control_init(&control, AGENT_EXECUTION_CONTRACT_CREATE,
			      lifecycle);
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.nodes = (uint64)nodes;
	control.node_count = AGENT_EXECUTION_CONTRACT_MAX_NODES;
	control.node_size = sizeof(nodes[0]);
	nodes[0].schema_digest[0] = 1;
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_STALE,
	      "contract rejects stale schema digest");

	build_nodes(now);
	contract_control_init(&control, AGENT_EXECUTION_CONTRACT_CREATE,
			      lifecycle);
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.nodes = (uint64)nodes;
	control.node_count = AGENT_EXECUTION_CONTRACT_MAX_NODES;
	control.node_size = sizeof(nodes[0]);
	nodes[3].required_capabilities |= 1ULL << 63;
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_BAD_PARAM,
	      "contract rejects unknown capability");

	build_nodes(now);
	lifecycle.generation++;
	contract_control_init(&control, AGENT_EXECUTION_CONTRACT_CREATE,
			      lifecycle);
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.nodes = (uint64)nodes;
	control.node_count = AGENT_EXECUTION_CONTRACT_MAX_NODES;
	control.node_size = sizeof(nodes[0]);
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_DENIED,
	      "contract rejects stale full lifecycle key");
}

static __attribute__((noinline)) struct agent_execution_contract_key create_contract(
	struct agent_workflow_lifecycle_key lifecycle, uint64 now)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;
	const char *failure_reason = "none";
	uint create_attempts = 0;
	int rc;
	int created;

	build_nodes(now);
	contract_control_init(&control, AGENT_EXECUTION_CONTRACT_CREATE,
			      lifecycle);
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.nodes = (uint64)nodes;
	control.node_count = AGENT_EXECUTION_CONTRACT_MAX_NODES;
	control.node_size = sizeof(nodes[0]);
	do {
		create_attempts++;
		memset(&result, 0, sizeof(result));
		rc = agent_execution_contract(&control, &result);
		if (rc != 0 || result.status != AGENT_STATUS_RETRY ||
		    create_attempts == CONTRACT_CREATE_MAX_ITERATIONS)
			break;
		check(sched_yield() == 0,
		      "yield before replaying retryable contract create");
	} while (create_attempts < CONTRACT_CREATE_MAX_ITERATIONS);
	created = rc == 0 && result.status == AGENT_STATUS_OK &&
		result.state == AGENT_EXECUTION_CONTRACT_FROZEN &&
		result.node_count == AGENT_EXECUTION_CONTRACT_MAX_NODES &&
		result.key.generation != 0;
	if (!created) {
		if (rc != 0)
			failure_reason = "syscall";
		else if (result.status != AGENT_STATUS_OK)
			failure_reason = "result_status";
		else if (result.state != AGENT_EXECUTION_CONTRACT_FROZEN)
			failure_reason = "contract_state";
		else if (result.node_count != AGENT_EXECUTION_CONTRACT_MAX_NODES)
			failure_reason = "node_count";
		else if (result.key.generation == 0)
			failure_reason = "contract_generation";
		printf("agentcontract_ucore: create diagnostic rc=%d result_status=%d reason=%s attempts=%u max_attempts=%u version=%u size=%u state=%u lifecycle_id=%llu lifecycle_generation=%llu contract_generation=%llu request_id=%llu control_request_id=%llu node_count=%u expected_node_count=%u flags=%u deadline_tick=%llu created_tick=%llu\n",
		       rc, result.status, failure_reason, create_attempts,
		       CONTRACT_CREATE_MAX_ITERATIONS, result.version,
		       result.size, result.state, result.key.lifecycle.id,
		       result.key.lifecycle.generation, result.key.generation,
		       result.request_id, control.request_id, result.node_count,
		       AGENT_EXECUTION_CONTRACT_MAX_NODES, result.flags,
		       result.deadline_tick, result.created_tick);
	}
	check(created,
	      "create frozen 24-node contract");

	contract_control_init(&control, AGENT_EXECUTION_CONTRACT_QUERY,
			      lifecycle);
	control.key = result.key;
	control.nodes = (uint64)queried;
	control.node_count = AGENT_EXECUTION_CONTRACT_MAX_NODES;
	control.node_size = sizeof(queried[0]);
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_OK &&
	      result.node_count == AGENT_EXECUTION_CONTRACT_MAX_NODES,
	      "query frozen contract and schemas");
	for (uint i = 0; i < AGENT_EXECUTION_CONTRACT_MAX_NODES; i++)
		check(queried[i].node_id == i &&
		      digest_nonzero(queried[i].schema_digest),
		      "query returns all node schemas");
	return result.key;
}

static struct agent_resource_snapshot global_before;
static struct agent_resource_snapshot global_after;
static struct agent_workflow_lifecycle_info run_lifecycle_info;
static struct agent_info run_info;

static int resource_kind_equal(const struct agent_resource_snapshot *a,
			       const struct agent_resource_snapshot *b,
			       uint kind)
{
	return a->kinds[kind].used == b->kinds[kind].used &&
		a->kinds[kind].pending == b->kinds[kind].pending &&
		a->kinds[kind].ordinary_used == b->kinds[kind].ordinary_used &&
		a->kinds[kind].ordinary_pending ==
			b->kinds[kind].ordinary_pending &&
		a->kinds[kind].reserved_used == b->kinds[kind].reserved_used &&
		a->kinds[kind].reserved_pending == b->kinds[kind].reserved_pending;
}

static int resource_snapshot_equal(const struct agent_resource_snapshot *a,
				   const struct agent_resource_snapshot *b)
{
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++)
		if (!resource_kind_equal(a, b, kind))
			return 0;
	return 1;
}

static void check_resource_equal(const struct agent_resource_snapshot *a,
				 const struct agent_resource_snapshot *b)
{
	for (uint kind = 0; kind < AGENT_RESOURCE_KIND_COUNT; kind++) {
		int equal = resource_kind_equal(a, b, kind);

		if (!equal)
			printf("agentcontract_ucore: workflow resource leak diagnostic kind=%u before_used=%llu after_used=%llu before_pending=%llu after_pending=%llu before_ordinary_used=%llu after_ordinary_used=%llu before_ordinary_pending=%llu after_ordinary_pending=%llu before_reserved_used=%llu after_reserved_used=%llu before_reserved_pending=%llu after_reserved_pending=%llu\n",
			       kind, a->kinds[kind].used, b->kinds[kind].used,
			       a->kinds[kind].pending, b->kinds[kind].pending,
			       a->kinds[kind].ordinary_used,
			       b->kinds[kind].ordinary_used,
			       a->kinds[kind].ordinary_pending,
			       b->kinds[kind].ordinary_pending,
			       a->kinds[kind].reserved_used,
			       b->kinds[kind].reserved_used,
			       a->kinds[kind].reserved_pending,
			       b->kinds[kind].reserved_pending);
		check(equal, "retired workflow leaves no global resource charge");
	}
}

static void exercise_contract(struct agent_execution_contract_key key)
{
	struct agent_request_v3 request, replay;
	struct agent_response_v3 response, cached;
	struct agent_info deadline_info;
	uint64 sequence0, sequence1;
	uint deadline_wait_iterations;
	int rc;
	int planned;
	int deadline_info_rc;
	int deadline_ok;

	param_uint(0, "target_pid", getpid());
	param_string(1, "message", "planned-message");
	v3_request_init(&request, &key, 0, 1, AGENT_TOOL_SEND_MESSAGE,
			getpid(), 0, "planned-message", 2,
			AGENT_EXECUTION_NODE_NONE, 0);
	rc = v3_call(&request, &response, "planned side effect call");
	planned = response.status == AGENT_STATUS_OK &&
		response.sequence != 0 && response.evidence_ticket != 0;
	if (!planned)
		printf("agentcontract_ucore: planned IPC diagnostic rc=%d status=%d decision_reason=%u evidence_ticket=%llu context_sequence=%llu response_source_sequence=%llu output_provenance=%u input_artifact=%u output_artifact=%u request_id=%llu response_request_id=%llu tool_id=%d response_tool_id=%d node_id=%u response_node_id=%u attempt_id=%u response_attempt_id=%u source_node_id=%u source_control_id=%llu source_pid=%d lifecycle_id=%llu lifecycle_generation=%llu contract_generation=%llu input_fingerprint=%x%x%x%x response_fingerprint=%x%x%x%x\n",
		       rc, response.status, response.decision_reason,
		       response.evidence_ticket, response.sequence,
		       response.source_context_sequence,
		       response.output_provenance_labels,
		       request.input_artifact_type,
		       response.output_artifact_type, request.request_id,
		       response.request_id, request.tool_id, response.tool_id,
		       request.node_id, response.node_id, request.attempt_id,
		       response.attempt_id, request.source_node_id,
		       request.source_control_id, request.source_pid,
		       request.contract.lifecycle.id,
		       request.contract.lifecycle.generation,
		       request.contract.generation,
		       request.input_fingerprint[0], request.input_fingerprint[1],
		       request.input_fingerprint[2], request.input_fingerprint[3],
		       response.input_fingerprint[0],
		       response.input_fingerprint[1],
		       response.input_fingerprint[2],
		       response.input_fingerprint[3]);
	check(planned,
	      "planned IPC side effect succeeds with evidence");
	sequence0 = response.sequence;

	param_string(0, "path", "stage=contract-no-match");
	v3_request_init(&request, &key, 1, 1, AGENT_TOOL_QUERY_FILE,
			0, 0, "stage=contract-no-match", 1, 0, sequence0);
	request.source_context_sequence++;
	v3_call(&request, &response, "wrong exact predecessor sequence");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.decision_reason == AGENT_EXECUTION_REASON_SOURCE_SEQUENCE &&
	      response.evidence_ticket != 0,
	      "wrong predecessor Context sequence denied critically");

	request.source_context_sequence = sequence0;
	request.source_node_id = AGENT_EXECUTION_NODE_NONE;
	v3_call(&request, &response, "illegal predecessor call");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.decision_reason == AGENT_EXECUTION_REASON_ILLEGAL_PREDECESSOR &&
	      response.evidence_ticket != 0,
	      "illegal predecessor denied before tool");

	request.source_node_id = 0;
	request.schema_digest[0] ^= 0x80;
	v3_call(&request, &response, "schema mismatch call");
	check(response.status == AGENT_STATUS_STALE &&
	      response.decision_reason == AGENT_EXECUTION_REASON_SCHEMA_MISMATCH &&
	      response.evidence_ticket != 0,
	      "schema mismatch denied critically");

	memcpy(request.schema_digest, queried[1].schema_digest,
		sizeof(request.schema_digest));
	v3_call(&request, &response, "legal exact predecessor call");
	check(response.status == AGENT_STATUS_OK && response.sequence != 0 &&
	      (response.output_provenance_labels &
	       AGENT_PROVENANCE_UNTRUSTED_FILE_DATA) != 0,
	      "legal dependency preserves untrusted file provenance");
	sequence1 = response.sequence;

	param_string(0, "selector", "must-not-commit");
	v3_request_init(&request, &key, 2, 1, AGENT_TOOL_ACTION_COMMIT,
			0, 0, "must-not-commit", 1, 1, sequence1);
	v3_call(&request, &response, "tainted privileged planned call");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.evidence_ticket != 0 &&
	      (response.output_provenance_labels &
	       AGENT_PROVENANCE_UNTRUSTED_FILE_DATA) != 0,
	      "untrusted file data cannot be laundered into privileged effect");

	param_uint(0, "target_pid", getpid());
	param_string(1, "message", "unplanned-privileged-effect");
	v3_request_init(&request, &key, 3, 1, AGENT_TOOL_SEND_MESSAGE,
			getpid(), 0, "unplanned-privileged-effect", 2,
			AGENT_EXECUTION_NODE_NONE, 0);
	v3_call(&request, &response, "unplanned high privilege call");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.decision_reason == AGENT_EXECUTION_REASON_TOOL_MISMATCH &&
	      response.evidence_ticket != 0,
	      "unplanned high privilege tool denied before side effect");

	param_string(0, "payload", "stale-lifecycle");
	param_uint(1, "arg0", 9);
	param_uint(2, "arg1", 10);
	v3_request_init(&request, &key, 4, 1, AGENT_TOOL_ECHO,
			9, 10, "stale-lifecycle", 3,
			AGENT_EXECUTION_NODE_NONE, 0);
	request.contract.lifecycle.generation++;
	v3_call(&request, &response, "stale v3 lifecycle call");
	check(response.status == AGENT_STATUS_STALE &&
	      response.decision_reason == AGENT_EXECUTION_REASON_STALE_LIFECYCLE &&
	      response.evidence_ticket != 0,
	      "v3 binding checks the full lifecycle generation");

	v3_request_init(&request, &key, 9, 1, AGENT_TOOL_READ_MESSAGE,
			0, 0, "", 0, AGENT_EXECUTION_NODE_NONE, 0);
	v3_call(&request, &response, "read cross-agent-labelled mailbox");
	check(response.status == AGENT_STATUS_OK &&
	      (response.output_provenance_labels &
	       AGENT_PROVENANCE_CROSS_AGENT_DATA) != 0,
	      "message output obtains cross-agent provenance");

	param_uint(0, "target_pid", getpid());
	param_string(1, "message", "must-not-send");
	v3_request_init(&request, &key, 10, 1, AGENT_TOOL_SEND_MESSAGE,
			getpid(), 0, "must-not-send", 2, 9, response.sequence);
	v3_call(&request, &response, "cross-agent taint privileged call");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.evidence_ticket != 0 &&
	      (response.output_provenance_labels &
	       AGENT_PROVENANCE_CROSS_AGENT_DATA) != 0,
	      "cross-agent data cannot be laundered into side effect");

	param_string(0, "payload", "replay-stable");
	param_uint(1, "arg0", 1);
	param_uint(2, "arg1", 2);
	v3_request_init(&request, &key, 7, 1, AGENT_TOOL_ECHO,
			1, 2, "replay-stable", 3,
			AGENT_EXECUTION_NODE_NONE, 0);
	replay = request;
	v3_call(&request, &response, "initial replay target");
	check(response.status == AGENT_STATUS_OK, "initial replay target succeeds");
	v3_call(&replay, &cached, "exact replay");
	check(cached.status == AGENT_STATUS_OK &&
	      (cached.completion_flags & AGENT_RESPONSE_V3_F_CACHED) != 0 &&
	      cached.sequence == response.sequence &&
	      cached.evidence_ticket == response.evidence_ticket,
	      "exact replay returns exactly-once cached completion");

	param_string(0, "payload", "phase-denied");
	param_uint(1, "arg0", 3);
	param_uint(2, "arg1", 4);
	v3_request_init(&request, &key, 8, 1, AGENT_TOOL_ECHO,
			3, 4, "phase-denied", 3,
			AGENT_EXECUTION_NODE_NONE, 0);
	v3_call(&request, &response, "phase envelope attempt one");
	check(response.status == AGENT_STATUS_NO_SPACE &&
	      response.decision_reason == AGENT_EXECUTION_REASON_PHASE_CREDIT,
	      "oversized phase envelope rejected atomically");
	request.attempt_id = 2;
	request.request_id = ++next_request;
	v3_call(&request, &response, "phase retry attempt two");
	check(response.status == AGENT_STATUS_NO_SPACE &&
	      response.decision_reason == AGENT_EXECUTION_REASON_PHASE_CREDIT,
	      "retry transition retains immutable envelope");
	replay = request;
	v3_call(&replay, &cached, "phase failure replay");
	check(cached.status == AGENT_STATUS_NO_SPACE &&
	      (cached.completion_flags & AGENT_RESPONSE_V3_F_CACHED) != 0,
	      "failed phase completion replays exactly once");

	memset(&deadline_info, 0, sizeof(deadline_info));
	for (deadline_wait_iterations = 0;
	     deadline_wait_iterations < DEADLINE_WAIT_MAX_ITERATIONS;
	     deadline_wait_iterations++) {
		check(agent_info(&deadline_info) == 0,
		      "query tick while awaiting node deadline");
		if (deadline_info.current_tick > queried[6].deadline_tick)
			break;
		check(sched_yield() == 0, "yield while awaiting node deadline");
	}
	if (deadline_info.current_tick <= queried[6].deadline_tick)
		printf("agentcontract_ucore: deadline wait diagnostic iterations=%u max_iterations=%u current_tick=%llu effective_deadline_tick=%llu\n",
		       deadline_wait_iterations, DEADLINE_WAIT_MAX_ITERATIONS,
		       deadline_info.current_tick, queried[6].deadline_tick);
	check(deadline_info.current_tick > queried[6].deadline_tick,
	      "bounded wait advances beyond node deadline");
	param_string(0, "payload", "expired");
	param_uint(1, "arg0", 5);
	param_uint(2, "arg1", 6);
	v3_request_init(&request, &key, 6, 1, AGENT_TOOL_ECHO,
			5, 6, "expired", 3,
			AGENT_EXECUTION_NODE_NONE, 0);
	rc = v3_call(&request, &response, "expired deadline call");
	deadline_ok = response.status == AGENT_STATUS_TIMEOUT &&
		response.decision_reason == AGENT_EXECUTION_REASON_DEADLINE_EXPIRED &&
		response.evidence_ticket != 0;
	if (!deadline_ok) {
		memset(&deadline_info, 0, sizeof(deadline_info));
		deadline_info_rc = agent_info(&deadline_info);
		printf("agentcontract_ucore: deadline response diagnostic rc=%d wait_iterations=%u status=%d decision_reason=%u evidence_ticket=%llu sequence=%llu effective_deadline_tick=%llu agent_info_rc=%d current_tick=%llu request_id=%llu response_request_id=%llu tool_id=%d response_tool_id=%d node_id=%u response_node_id=%u attempt_id=%u response_attempt_id=%u lifecycle_id=%llu lifecycle_generation=%llu contract_generation=%llu completion_flags=%u\n",
		       rc, deadline_wait_iterations, response.status,
		       response.decision_reason,
		       response.evidence_ticket, response.sequence,
		       queried[6].deadline_tick, deadline_info_rc,
		       deadline_info.current_tick, request.request_id,
		       response.request_id, request.tool_id, response.tool_id,
		       request.node_id, response.node_id, request.attempt_id,
		       response.attempt_id, request.contract.lifecycle.id,
		       request.contract.lifecycle.generation,
		       request.contract.generation, response.completion_flags);
	}
	check(deadline_ok,
	      "deadline terminates before tool execution");
	request.attempt_id = 2;
	request.request_id = ++next_request;
	v3_call(&request, &response, "deadline retry forbidden");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.decision_reason == AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT,
	      "terminal deadline forbids unsafe retry");
}

static void run_runtime_capability_caller(void)
{
	struct agent_workflow_lifecycle_info lifecycle_info;
	struct agent_execution_contract_key key;
	struct agent_request_v3 request;
	struct agent_response_v3 response;
	struct agent_event event;
	uint64 contract_generation = 0;
	int parent_pid = getppid();

	check(agent_watch(AGENT_EVENT_MESSAGE, "contract-") == 0,
	      "watch runtime capability binding");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, CAPABILITY_EVENT_TIMEOUT_TICKS) ==
		      AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_MESSAGE &&
	      event.source_pid == parent_pid &&
	      strcmp(event.payload, "contract-setup") == 0,
	      "receive runtime capability setup event");

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, CAPABILITY_EVENT_TIMEOUT_TICKS) ==
		      AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_MESSAGE &&
	      event.source_pid == parent_pid &&
	      strncmp(event.payload, CONTRACT_BINDING_A_PREFIX,
		      CONTRACT_BINDING_PREFIX_SIZE) == 0 &&
	      strlen(event.payload) ==
		      CONTRACT_BINDING_PREFIX_SIZE +
		      CONTRACT_BINDING_GENERATION_HEX_SIZE + 1 +
		      CONTRACT_BINDING_DIGEST_HALF_HEX_SIZE &&
	      event.payload[CONTRACT_BINDING_PREFIX_SIZE +
			    CONTRACT_BINDING_GENERATION_HEX_SIZE] == ':' &&
	      decode_u64_hex(event.payload + CONTRACT_BINDING_PREFIX_SIZE,
			     &contract_generation) == 0 &&
	      decode_digest_half(
		      event.payload + CONTRACT_BINDING_PREFIX_SIZE +
			      CONTRACT_BINDING_GENERATION_HEX_SIZE + 1,
		      queried[5].schema_digest) == 0,
	      "receive runtime capability binding half A");

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, CAPABILITY_EVENT_TIMEOUT_TICKS) ==
		      AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_MESSAGE &&
	      event.source_pid == parent_pid &&
	      strncmp(event.payload, CONTRACT_BINDING_B_PREFIX,
		      CONTRACT_BINDING_PREFIX_SIZE) == 0 &&
	      strlen(event.payload) ==
		      CONTRACT_BINDING_PREFIX_SIZE +
		      CONTRACT_BINDING_DIGEST_HALF_HEX_SIZE &&
	      decode_digest_half(event.payload + CONTRACT_BINDING_PREFIX_SIZE,
				 queried[5].schema_digest +
					 CONTRACT_BINDING_DIGEST_HALF_BYTES) == 0,
	      "receive runtime capability binding half B");
	check(contract_generation != 0 &&
	      digest_nonzero(queried[5].schema_digest),
	      "decode runtime capability contract binding");

	memset(&lifecycle_info, 0, sizeof(lifecycle_info));
	check(agent_workflow_lifecycle_info(&lifecycle_info, 0) ==
		      AGENT_STATUS_OK &&
	      lifecycle_info.charged && lifecycle_info.key.id != 0 &&
	      lifecycle_info.key.generation != 0,
	      "query runtime capability caller lifecycle");
	memset(&key, 0, sizeof(key));
	key.lifecycle = lifecycle_info.key;
	key.generation = contract_generation;
	queried[5].input_artifact_type = AGENT_ARTIFACT_UTF8;

	param_string(0, "payload", "capability-denied");
	param_uint(1, "arg0", 11);
	param_uint(2, "arg1", 12);
	v3_request_init(&request, &key, 5, 1, AGENT_TOOL_ECHO,
			11, 12, "capability-denied", 3,
			AGENT_EXECUTION_NODE_NONE, 0);
	v3_call(&request, &response, "runtime capability call");
	check(response.status == AGENT_STATUS_DENIED &&
	      response.decision_reason ==
		      AGENT_EXECUTION_REASON_CAPABILITY_MISSING &&
	      response.evidence_ticket != 0,
	      "runtime capability checked before tool execution");
	exit(0);
}

static __attribute__((noinline)) int create_runtime_capability_caller(void)
{
	struct agent_event event;
	uint attempt;
	int wake_status = AGENT_STATUS_NOT_FOUND;
	int pid;

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create low-capability contract caller before freeze");
	if (pid == 0) {
		run_runtime_capability_caller();
		exit(1);
	}
	check(agent_route_config(getpid(), pid, AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant runtime capability binding route before freeze");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = ++next_request;
	strcpy(event.payload, "contract-setup");
	for (attempt = 0; attempt < CAPABILITY_SETUP_MAX_ITERATIONS; attempt++) {
		wake_status = agent_wake(pid, &event);
		if (wake_status == AGENT_STATUS_OK)
			break;
		check(wake_status == AGENT_STATUS_NOT_FOUND,
		      "retry runtime capability watch setup only while absent");
		check(sched_yield() == 0,
		      "yield while runtime capability watch starts");
	}
	if (wake_status != AGENT_STATUS_OK)
		printf("agentcontract_ucore: capability setup diagnostic pid=%d status=%d attempts=%u max_attempts=%u\n",
		       pid, wake_status, attempt,
		       CAPABILITY_SETUP_MAX_ITERATIONS);
	check(wake_status == AGENT_STATUS_OK,
	      "bind low-capability caller before contract freeze");
	return pid;
}

static __attribute__((noinline)) void bind_runtime_capability_caller(
	struct agent_execution_contract_key key, int pid)
{
	struct agent_request_v3 request;
	struct agent_response_v3 response;
	char binding_a[AGENT_PARAM_STRING_SIZE];
	char binding_b[AGENT_PARAM_STRING_SIZE];
	uint offset;

	memset(binding_a, 0, sizeof(binding_a));
	strcpy(binding_a, CONTRACT_BINDING_A_PREFIX);
	offset = CONTRACT_BINDING_PREFIX_SIZE;
	encode_u64_hex(key.generation, binding_a + offset);
	offset += CONTRACT_BINDING_GENERATION_HEX_SIZE;
	binding_a[offset++] = ':';
	encode_digest_half(queried[5].schema_digest, binding_a + offset);
	offset += CONTRACT_BINDING_DIGEST_HALF_HEX_SIZE;
	binding_a[offset] = 0;

	memset(binding_b, 0, sizeof(binding_b));
	strcpy(binding_b, CONTRACT_BINDING_B_PREFIX);
	offset = CONTRACT_BINDING_PREFIX_SIZE;
	encode_digest_half(queried[5].schema_digest +
			   CONTRACT_BINDING_DIGEST_HALF_BYTES,
			   binding_b + offset);
	offset += CONTRACT_BINDING_DIGEST_HALF_HEX_SIZE;
	binding_b[offset] = 0;

	param_uint(0, "target_pid", pid);
	param_string(1, "message", binding_a);
	v3_request_init(&request, &key, 11, 1, AGENT_TOOL_SEND_MESSAGE,
			pid, 0, binding_a, 2, AGENT_EXECUTION_NODE_NONE, 0);
	v3_call(&request, &response, "send runtime capability binding half A");
	check(response.status == AGENT_STATUS_OK && response.sequence != 0 &&
	      response.evidence_ticket != 0,
	      "deliver runtime capability binding half A");

	param_uint(0, "target_pid", pid);
	param_string(1, "message", binding_b);
	v3_request_init(&request, &key, 12, 1, AGENT_TOOL_SEND_MESSAGE,
			pid, 0, binding_b, 2, AGENT_EXECUTION_NODE_NONE, 0);
	v3_call(&request, &response, "send runtime capability binding half B");
	check(response.status == AGENT_STATUS_OK && response.sequence != 0 &&
	      response.evidence_ticket != 0,
	      "deliver runtime capability binding half B");
}

static __attribute__((noinline)) void check_runtime_capability(int pid)
{
	int status = 0;

	check(waitpid(pid, &status) == pid && status == 0,
	      "wait low-capability contract caller");
}

static __attribute__((noinline)) void check_enforced_legacy_bypass(void)
{
	struct agent_request_v2 request;
	struct agent_response_v2 response;

	memset(&request, 0, sizeof(request));
	memset(&response, 0, sizeof(response));
	request.version = AGENT_CALL_VERSION_V2;
	request.size = sizeof(request);
	request.tool_id = AGENT_TOOL_METADATA_INIT;
	request.request_id = ++next_request;
	check(tool_call(&request, &response) == 0 &&
	      response.status == AGENT_STATUS_DENIED &&
	      strcmp(response.result, "execution_contract_required") == 0,
	      "ENFORCE rejects legacy direct side-effect bypass");
}

static __attribute__((noinline)) void run_agent_test(void)
{
	struct agent_execution_contract_key key;
	int capability_pid;

	memset(&run_lifecycle_info, 0, sizeof(run_lifecycle_info));
	check(agent_workflow_lifecycle_info(&run_lifecycle_info, 0) ==
		      AGENT_STATUS_OK &&
	      run_lifecycle_info.charged && run_lifecycle_info.key.id != 0 &&
	      run_lifecycle_info.key.generation != 0,
	      "query complete workflow lifecycle key");
	check(agent_info(&run_info) == 0, "query agent tick");
	legacy_echo();
	check(agent_metadata_init() == AGENT_STATUS_OK,
	      "legacy direct side effect remains compatible before enforce");
	check(agent_watch(AGENT_EVENT_MESSAGE, "planned-message") == 0,
	      "install planned IPC target before contract enforce");
	invalid_contracts(run_lifecycle_info.key, run_info.current_tick);
	capability_pid = create_runtime_capability_caller();
	key = create_contract(run_lifecycle_info.key, run_info.current_tick);
	bind_runtime_capability_caller(key, capability_pid);
	exercise_contract(key);
	check_runtime_capability(capability_pid);
	check_enforced_legacy_bypass();
	printf("agentcontract_ucore: dag24=1 lifecycle=1 schema=1 capability=1\n");
	printf("agentcontract_ucore: dependency_sequence=1 provenance_file=1 provenance_cross_agent=1\n");
	printf("agentcontract_ucore: planned_effect=1 unplanned_effect_denied=1 evidence=1\n");
	printf("agentcontract_ucore: legacy_v2=1 enforce_bypass_denied=1\n");
}

int main(void)
{
	uint snapshot_attempts;
	int baseline_equal = 0;
	int pid;
	int snapshot_rc;
	int status = 0;

	printf("agentcontract_ucore: execution contract vertical test\n");
	memset(&global_before, 0, sizeof(global_before));
	snapshot_rc = agent_resource_snapshot(&global_before);
	if (snapshot_rc != 0)
		printf("agentcontract_ucore: global resource snapshot diagnostic point=before_workflow rc=%d version=%u size=%u measured_mask=%u kind_count=%u ordinary_free_pages=%llu reserved_free_pages=%llu stack_reserved_free_pages=%llu\n",
		       snapshot_rc, global_before.version,
		       global_before.struct_size, global_before.measured_mask,
		       global_before.kind_count, global_before.ordinary_free_pages,
		       global_before.reserved_free_pages,
		       global_before.stack_reserved_free_pages);
	check(snapshot_rc == 0, "bootstrap resource snapshot before workflow");
	pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create isolated orchestrator workflow");
	if (pid == 0) {
		run_agent_test();
		exit(0);
	}
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait orchestrator");
	for (snapshot_attempts = 1;
	     snapshot_attempts <= RESOURCE_REAPER_MAX_ITERATIONS;
	     snapshot_attempts++) {
		memset(&global_after, 0, sizeof(global_after));
		snapshot_rc = agent_resource_snapshot(&global_after);
		if (snapshot_rc != 0)
			printf("agentcontract_ucore: global resource snapshot diagnostic point=after_workflow rc=%d attempt=%u max_attempts=%u version=%u size=%u measured_mask=%u kind_count=%u ordinary_free_pages=%llu reserved_free_pages=%llu stack_reserved_free_pages=%llu\n",
			       snapshot_rc, snapshot_attempts,
			       RESOURCE_REAPER_MAX_ITERATIONS,
			       global_after.version, global_after.struct_size,
			       global_after.measured_mask, global_after.kind_count,
			       global_after.ordinary_free_pages,
			       global_after.reserved_free_pages,
			       global_after.stack_reserved_free_pages);
		check(snapshot_rc == 0,
		      "bootstrap resource snapshot after workflow");
		baseline_equal = resource_snapshot_equal(&global_before,
						  &global_after);
		if (baseline_equal)
			break;
		if (snapshot_attempts < RESOURCE_REAPER_MAX_ITERATIONS)
			check(sched_yield() == 0,
			      "yield while workflow resources retire");
	}
	check_resource_equal(&global_before, &global_after);
	printf("agentcontract_ucore: replay=1 retry=1 deadline=1 phase_atomic=1 phase_zero_leak=1\n");
	printf("agentcontract_ucore: parent passed\n");
	return 0;
}
