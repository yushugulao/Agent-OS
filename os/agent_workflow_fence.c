#include "agent_workflow_fence.h"
#include "agent_evidence_ring.h"
#include "agent_internal.h"
#include "defs.h"
#include "fs.h"
#include "fs_epoch.h"
#include "proc.h"
#include "riscv.h"
#include "vfs_security.h"
#include "workflow_credit_domain.h"

struct agent_workflow_fence_cache {
	struct workflow_lifecycle_key key;
	uint64 request_id;
	int valid;
	int delivered;
	struct agent_workflow_fence_receipt receipt;
};

/* One retry-stable receipt per bounded lifecycle slot. */
static struct agent_workflow_fence_cache
	agent_workflow_fence_cache[WORKFLOW_LIFECYCLE_CAP];

static void
agent_workflow_fence_receipt_init(
	struct agent_workflow_fence_receipt *receipt, int status)
{
	if (receipt == 0)
		return;
	memset(receipt, 0, sizeof(*receipt));
	receipt->version = AGENT_WORKFLOW_FENCE_VERSION;
	receipt->struct_size = sizeof(*receipt);
	receipt->status = status;
}

static int
agent_workflow_fence_challenge_equal(const uchar a[32], const uchar b[32])
{
	for (uint i = 0; i < AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE; i++)
		if (a[i] != b[i])
			return 0;
	return 1;
}

static void
agent_workflow_fence_hash_u32(struct agent_sha256_ctx *hash, uint value)
{
	uchar encoded[4];

	for (uint i = 0; i < sizeof(encoded); i++)
		encoded[i] = (uchar)(value >> (i * 8U));
	agent_sha256_update(hash, encoded, sizeof(encoded));
}

static void
agent_workflow_fence_hash_u64(struct agent_sha256_ctx *hash, uint64 value)
{
	uchar encoded[8];

	for (uint i = 0; i < sizeof(encoded); i++)
		encoded[i] = (uchar)(value >> (i * 8U));
	agent_sha256_update(hash, encoded, sizeof(encoded));
}

static void
agent_workflow_fence_credit_digest(
	const struct workflow_credit_snapshot *credit,
	uchar digest[AGENT_SHA256_DIGEST_SIZE])
{
	static const char domain[] = "AgentOS workflow credit exact v1";
	struct agent_sha256_ctx hash;

	agent_sha256_init(&hash);
	agent_sha256_update(&hash, domain, sizeof(domain) - 1U);
	agent_workflow_fence_hash_u32(&hash, credit->key.id);
	agent_workflow_fence_hash_u64(&hash, credit->key.generation);
	agent_workflow_fence_hash_u64(&hash, credit->epoch);
	for (uint role = 0; role < WORKFLOW_CREDIT_ACCOUNT_COUNT; role++) {
		agent_workflow_fence_hash_u32(
			&hash, credit->account[role].handle.slot);
		agent_workflow_fence_hash_u64(
			&hash, credit->account[role].handle.generation);
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		agent_workflow_fence_hash_u64(&hash, credit->used[kind]);
	agent_sha256_final(&hash, digest);
}

/*
 * Return 1 for an exact cached retry, 0 for a new request, or a negative
 * Agent status for a stale/conflicting request id.
 */
static int
agent_workflow_fence_cache_lookup(
	struct workflow_lifecycle_key key,
	const struct agent_workflow_fence_request *request,
	struct agent_workflow_fence_receipt *receipt)
{
	struct agent_workflow_fence_cache *cache;
	int enabled;
	int result = 0;

	if (request == 0)
		return 0;
	if (key.id == 0 || key.id > WORKFLOW_LIFECYCLE_CAP)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	cache = &agent_workflow_fence_cache[key.id - 1];
	if (!cache->valid ||
	    !workflow_lifecycle_key_equal(cache->key, key)) {
		cache->valid = 0;
	} else if (request->request_id == cache->request_id) {
		if (!agent_workflow_fence_challenge_equal(
			    request->challenge, cache->receipt.challenge)) {
			result = AGENT_STATUS_CONFLICT;
		} else {
			if (receipt != 0)
				*receipt = cache->receipt;
			result = 1;
		}
	} else if (request->request_id < cache->request_id) {
		result = AGENT_STATUS_STALE;
	} else if (!cache->delivered) {
		/* Do not evict a committed receipt until copyout has succeeded. */
		result = AGENT_STATUS_RETRY;
	}
	intr_restore(enabled);
	return result;
}

static void
agent_workflow_fence_cache_publish(
	struct workflow_lifecycle_key key, uint64 request_id,
	const struct agent_workflow_fence_receipt *receipt)
{
	struct agent_workflow_fence_cache *cache;
	int enabled;

	if (request_id == 0 || receipt == 0 || key.id == 0 ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return;
	enabled = intr_save();
	cache = &agent_workflow_fence_cache[key.id - 1];
	cache->key = key;
	cache->request_id = request_id;
	cache->receipt = *receipt;
	cache->valid = 1;
	cache->delivered = 0;
	intr_restore(enabled);
}

void
agent_workflow_fence_receipt_delivered(
	struct workflow_lifecycle_key key, uint64 request_id)
{
	struct agent_workflow_fence_cache *cache;
	int enabled;

	if (request_id == 0 || key.id == 0 ||
	    key.id > WORKFLOW_LIFECYCLE_CAP)
		return;
	enabled = intr_save();
	cache = &agent_workflow_fence_cache[key.id - 1];
	if (cache->valid && workflow_lifecycle_key_equal(cache->key, key) &&
	    cache->request_id == request_id)
		cache->delivered = 1;
	intr_restore(enabled);
}

static void
agent_workflow_fence_commit_cached(
	struct workflow_lifecycle_key key, uint64 fence_sequence,
	uint64 request_id, const struct agent_workflow_fence_receipt *receipt)
{
	int enabled = intr_save();

	/* Publish the retry receipt before another thread can enter the gate. */
	if (workflow_lifecycle_fence_end(key, fence_sequence, 1) < 0)
		panic("workflow fence publish");
	agent_workflow_fence_cache_publish(key, request_id, receipt);
	intr_restore(enabled);
}

static int
agent_workflow_fence_request_validate(
	const struct agent_workflow_fence_request *request)
{
	if (request == 0)
		return AGENT_STATUS_OK;
	if (request->version != AGENT_WORKFLOW_FENCE_VERSION)
		return AGENT_STATUS_BAD_VERSION;
	if (request->struct_size != sizeof(*request))
		return AGENT_STATUS_BAD_SIZE;
	if (request->flags != 0 || request->reserved != 0 ||
	    request->request_id == 0)
		return AGENT_STATUS_BAD_PARAM;
	return AGENT_STATUS_OK;
}

int
agent_workflow_fence_execute(
	struct proc *p, const struct agent_workflow_fence_request *request,
	struct agent_workflow_fence_receipt *receipt)
{
	struct workflow_lifecycle_key key;
	struct workflow_credit_snapshot credit;
	struct agent_evidence_seal_result evidence;
	struct resource_account_handle storage_account;
	struct agent_workflow_fence_receipt completed;
	uchar challenge[AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE];
	uchar credit_digest[AGENT_SHA256_DIGEST_SIZE];
	uint64 fence_sequence = 0;
	uint64 request_id = request != 0 ? request->request_id : 0;
	uint64 metadata_generation;
	int cache_status;
	int status;

	agent_workflow_fence_receipt_init(receipt, AGENT_STATUS_BAD_REQUEST);
	status = agent_workflow_fence_request_validate(request);
	if (status != AGENT_STATUS_OK)
		goto fail;
	if (p == 0 || !p->is_agent ||
	    !agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
		status = AGENT_STATUS_DENIED;
		goto fail;
	}
	key = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(key)) {
		status = AGENT_STATUS_BAD_PARAM;
		goto fail;
	}
	if (!workflow_lifecycle_controller_matches(
		    key, p->vfs_scope_id, p->agent_control_id)) {
		status = AGENT_STATUS_DENIED;
		goto fail;
	}
	cache_status = agent_workflow_fence_cache_lookup(key, request,
							 receipt);
	if (cache_status == 1)
		return AGENT_STATUS_OK;
	if (cache_status < 0) {
		status = cache_status;
		goto fail;
	}
	if (workflow_lifecycle_fence_begin(key, &fence_sequence) < 0) {
		status = AGENT_STATUS_RETRY;
		goto fail;
	}
	metadata_generation = 0;
	/*
	 * Metadata may perform I/O while reaching its cut.  Seal that state first,
	 * then drain the resulting filesystem work before taking the exact credit
	 * snapshot.  No workflow operation can enter while the fence gate is held.
	 */
	if (agent_metadata_quiescence_fence_snapshot_current(
		    &metadata_generation) < 0 || metadata_generation == 0) {
		status = AGENT_STATUS_RETRY;
		goto abort_fence;
	}
	if (fs_deferred_reclaim_drain_current() < 0 ||
	    fs_epoch_commit() < 0) {
		status = AGENT_STATUS_IO_ERROR;
		goto abort_fence;
	}
	storage_account = resource_account_none();
	if (fs_storage_owner_account(FS_OWNER_SCOPE(p->vfs_scope_id),
				     &storage_account) < 0 ||
	    workflow_credit_domain_fence(key, p->resource_account,
					 storage_account, &credit) < 0) {
		status = AGENT_STATUS_RETRY;
		goto abort_fence;
	}
	memset(challenge, 0, sizeof(challenge));
	if (request != 0)
		memmove(challenge, request->challenge, sizeof(challenge));
	agent_workflow_fence_credit_digest(&credit, credit_digest);
	memset(&evidence, 0, sizeof(evidence));
	if (agent_evidence_seal(key, fence_sequence, challenge,
				metadata_generation, credit.epoch, credit_digest,
				&evidence) < 0) {
		status = AGENT_STATUS_RETRY;
		goto abort_fence;
	}
	if (evidence.fence_sequence != fence_sequence)
		panic("workflow fence evidence sequence");
	agent_workflow_fence_receipt_init(&completed, AGENT_STATUS_OK);
	completed.flags =
		AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE |
		AGENT_WORKFLOW_FENCE_RECEIPT_F_CREDIT_EXACT |
		AGENT_WORKFLOW_FENCE_RECEIPT_F_EVIDENCE_SEALED |
		AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE;
	completed.key.id = key.id;
	completed.key.generation = key.generation;
	completed.request_id = request_id;
	completed.fence_sequence = fence_sequence;
	completed.metadata_generation = metadata_generation;
	completed.credit_epoch = credit.epoch;
	completed.evidence_first_sequence = evidence.first_ticket;
	completed.evidence_last_sequence = evidence.last_ticket;
	completed.evidence_event_count = evidence.event_count;
	completed.evidence_dropped_success = evidence.gap_count;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		completed.resource_used[kind] = credit.used[kind];
	completed.credit_exec_account.slot =
		credit.account[WORKFLOW_CREDIT_EXEC].handle.slot;
	completed.credit_exec_account.generation =
		credit.account[WORKFLOW_CREDIT_EXEC].handle.generation;
	completed.credit_storage_account.slot =
		credit.account[WORKFLOW_CREDIT_STORAGE].handle.slot;
	completed.credit_storage_account.generation =
		credit.account[WORKFLOW_CREDIT_STORAGE].handle.generation;
	memmove(completed.credit_digest, credit_digest,
		sizeof(completed.credit_digest));
	memmove(completed.challenge, challenge, sizeof(completed.challenge));
	memmove(completed.previous_root, evidence.previous_root,
		sizeof(completed.previous_root));
	memmove(completed.evidence_root, evidence.root,
		sizeof(completed.evidence_root));
	agent_workflow_fence_commit_cached(
		key, fence_sequence, request_id, &completed);
	if (receipt != 0)
		*receipt = completed;
	return AGENT_STATUS_OK;

abort_fence:
	if (workflow_lifecycle_fence_end(key, fence_sequence, 0) < 0)
		panic("workflow fence abort");
fail:
	if (receipt != 0)
		receipt->status = status;
	return status;
}
