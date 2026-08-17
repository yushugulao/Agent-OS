#include "agent_context.h"
#include "agent_context_path.h"
#include "agent_internal.h"
#include "agent_sha256.h"
#include "defs.h"
#include "open_file_io_lease.h"

#define AGENT_CONTEXT_ARTIFACT_CAPACITY 64U
#define AGENT_CONTEXT_ARTIFACT_BINDINGS AGENT_CONTEXT_ARTIFACT_CAPACITY
#define AGENT_CONTEXT_ARTIFACT_CHUNK    512U

struct agent_context_artifact_record {
	int used;
	uint state;
	uint64 handle;
	uint kind;
	uint flags;
	uint64 length;
	uint64 source_context_sequence;
	uint64 task_id;
	uint64 retain_until_tick;
	struct workflow_lifecycle_key lifecycle;
	int producer_pid;
	uint producer_agent_id;
	uint64 producer_control_id;
	uint references;
	uchar content_sha256[AGENT_SHA256_DIGEST_SIZE];
};

struct agent_context_artifact_binding {
	int used;
	uint reserved;
	uint64 control_id;
	uint64 sequence;
	uint64 handle;
};

struct agent_utf8_state {
	uint remaining;
	uchar next_min;
	uchar next_max;
};

static struct agent_context_artifact_record
	agent_context_artifacts[AGENT_CONTEXT_ARTIFACT_CAPACITY];
static struct agent_context_artifact_binding
	agent_context_artifact_bindings[NPROC][AGENT_CONTEXT_ARTIFACT_BINDINGS];

extern struct proc pool[NPROC];

static struct workflow_lifecycle_key
agent_context_artifact_lifecycle(const struct proc *p)
{
	struct workflow_lifecycle_key key = workflow_lifecycle_none();

	if (p != 0 && p->workflow_lifecycle_charged) {
		key.id = p->workflow_lifecycle_id;
		key.generation = p->workflow_lifecycle_generation;
	}
	return key;
}

static int
agent_context_artifact_active_sequence(struct proc *p, uint64 sequence)
{
	uint64 cursor;

	if (p == 0 || sequence == 0 || p->context_path_capacity == 0)
		return 0;
	cursor = p->context_path_visible_head;
	for (uint count = 0; cursor != 0 && count < AGENT_CONTEXT_MAX_RECORDS;
	     count++) {
		struct agent_context_record record;

		if (cursor == sequence)
			return 1;
		if (cursor < p->context_path_oldest ||
		    cursor > p->context_path_latest ||
		    agent_context_read_record(
			    p, (cursor - 1U) % p->context_path_capacity,
			    &record) < 0 ||
		    record.sequence != cursor || record.record_hash == 0 ||
		    record.record_hash != agent_context_record_hash(&record))
			return 0;
		cursor = record.path_parent_sequence;
	}
	return 0;
}

static int
agent_utf8_consume(struct agent_utf8_state *state, uchar byte)
{
	if (state->remaining != 0) {
		if (byte < state->next_min || byte > state->next_max)
			return -1;
		state->remaining--;
		state->next_min = 0x80U;
		state->next_max = 0xbfU;
		return 0;
	}
	if (byte <= 0x7fU)
		return byte == 0 ? -1 : 0;
	state->next_min = 0x80U;
	state->next_max = 0xbfU;
	if (byte >= 0xc2U && byte <= 0xdfU) {
		state->remaining = 1;
		return 0;
	}
	if (byte >= 0xe0U && byte <= 0xefU) {
		state->remaining = 2;
		if (byte == 0xe0U)
			state->next_min = 0xa0U;
		else if (byte == 0xedU)
			state->next_max = 0x9fU;
		return 0;
	}
	if (byte >= 0xf0U && byte <= 0xf4U) {
		state->remaining = 3;
		if (byte == 0xf0U)
			state->next_min = 0x90U;
		else if (byte == 0xf4U)
			state->next_max = 0x8fU;
		return 0;
	}
	return -1;
}

static struct agent_context_artifact_record *
agent_context_artifact_find_locked(struct workflow_lifecycle_key lifecycle,
				   uint64 handle)
{
	if (intr_get())
		panic("Context Artifact lookup unlocked");
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_CAPACITY; i++) {
		struct agent_context_artifact_record *record =
			&agent_context_artifacts[i];

		if (record->used && record->handle == handle &&
		    workflow_lifecycle_key_equal(record->lifecycle, lifecycle))
			return record;
	}
	return 0;
}

static void
agent_context_artifact_result_fill(
	const struct agent_context_artifact_record *record, int status,
	struct agent_context_artifact_result *result)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_CONTEXT_ARTIFACT_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	if (record == 0)
		return;
	result->state = record->state;
	result->handle = record->handle;
	result->kind = record->kind;
	result->flags = record->flags;
	result->length = record->length;
	result->source_context_sequence = record->source_context_sequence;
	result->task_id = record->task_id;
	result->lifecycle.id = record->lifecycle.id;
	result->lifecycle.generation = record->lifecycle.generation;
	result->producer_pid = record->producer_pid;
	result->producer_agent_id = record->producer_agent_id;
	result->producer_control_id = record->producer_control_id;
	result->references = record->references;
	memmove(result->content_sha256, record->content_sha256,
		sizeof(result->content_sha256));
}

static int
agent_context_artifact_hash_file(
	struct proc *p, struct file *file,
	const struct agent_context_artifact_control *control, uchar digest[32])
{
	struct open_file_io_token lease = OPEN_FILE_IO_TOKEN_INIT;
	struct agent_sha256_ctx sha;
	struct agent_utf8_state utf8;
	struct vfs_cred cred;
	uchar chunk[AGENT_CONTEXT_ARTIFACT_CHUNK];
	uint64 offset = 0;
	int status = AGENT_STATUS_IO_ERROR;

	memset(&utf8, 0, sizeof(utf8));
	if (p == 0 || file == 0 || !file->readable || file->type != FD_INODE ||
	    file->ip == 0 || ivalid(file->ip) < 0 || file->ip->type != T_FILE ||
	    file->ip->size != control->length)
		return AGENT_STATUS_BAD_PARAM;
	vfs_cred_from_proc(p, &cred);
	if (!vfs_inode_authorize(file->ip, &cred, VFS_OP_READ))
		return AGENT_STATUS_DENIED;
	if (open_file_io_lease_acquire(file, VFS_OP_READ, &lease, &cred) < 0)
		return AGENT_STATUS_RETRY;
	agent_sha256_init(&sha);
	while (offset < control->length) {
		uint length = control->length - offset > sizeof(chunk) ?
			      sizeof(chunk) : (uint)(control->length - offset);
		int got = readi_lease(file->ip, &cred, &lease, 0,
				      (uint64)chunk, (uint)offset, length);

		if (got != (int)length)
			goto out;
		agent_sha256_update(&sha, chunk, length);
		if ((control->flags & AGENT_CONTEXT_ARTIFACT_F_UTF8) != 0)
			for (uint i = 0; i < length; i++)
				if (agent_utf8_consume(&utf8, chunk[i]) < 0) {
					status = AGENT_STATUS_BAD_TYPE;
					goto out;
				}
		offset += length;
	}
	if (utf8.remaining != 0) {
		status = AGENT_STATUS_BAD_TYPE;
		goto out;
	}
	agent_sha256_final(&sha, digest);
	status = AGENT_STATUS_OK;
out:
	open_file_io_token_end(&lease);
	return status;
}

static int
agent_context_artifact_seal(
	struct proc *p, const struct agent_context_artifact_control *control,
	struct agent_context_artifact_result *result)
{
	struct agent_context_artifact_record staged;
	struct agent_context_artifact_record *slot = 0;
	struct workflow_lifecycle_key lifecycle;
	struct file *file;
	uchar digest[32];
	uint64 bytes = 0;
	uint count = 0;
	int aggregate = 0;
	int status;

	if (!agent_identity_has_cap(p, AGENT_CAP_ARTIFACT_WRITE) ||
	    control->handle == 0 || control->source_fd < 0 ||
	    control->kind == 0 ||
	    control->kind >= AGENT_CONTEXT_ARTIFACT_KIND_COUNT ||
	    control->length == 0 ||
	    control->length > AGENT_CONTEXT_ARTIFACT_MAX_BYTES ||
	    control->source_context_sequence == 0 || control->task_id == 0 ||
	    (control->flags & ~AGENT_CONTEXT_ARTIFACT_F_ALL) != 0 ||
	    (control->flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) != 0 ||
	    !agent_context_artifact_active_sequence(
		    p, control->source_context_sequence))
		return AGENT_STATUS_BAD_PARAM;
	for (uint i = 0; i < sizeof(control->content_sha256); i++)
		aggregate |= control->content_sha256[i];
	if (aggregate == 0)
		return AGENT_STATUS_BAD_PARAM;
	file = fdget(control->source_fd);
	if (file == 0)
		return AGENT_STATUS_BAD_PARAM;
	status = agent_context_artifact_hash_file(p, file, control, digest);
	fileclose(file);
	if (status != AGENT_STATUS_OK)
		return status;
	if (memcmp(digest, control->content_sha256, sizeof(digest)) != 0)
		return AGENT_STATUS_CONFLICT;
	lifecycle = agent_context_artifact_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle))
		return AGENT_STATUS_STALE;
	memset(&staged, 0, sizeof(staged));
	staged.used = 1;
	staged.state = AGENT_CONTEXT_ARTIFACT_STATE_SEALED;
	staged.handle = control->handle;
	staged.kind = control->kind;
	staged.flags = control->flags;
	staged.length = control->length;
	staged.source_context_sequence = control->source_context_sequence;
	staged.task_id = control->task_id;
	staged.retain_until_tick = control->retain_until_tick;
	staged.lifecycle = lifecycle;
	staged.producer_pid = p->pid;
	staged.producer_agent_id = p->agent_id;
	staged.producer_control_id = p->agent_control_id;
	memmove(staged.content_sha256, digest, sizeof(digest));
	int enabled = intr_save();
	if (!proc_teardown_live(p) ||
	    !workflow_lifecycle_key_equal(
		    lifecycle, agent_context_artifact_lifecycle(p)) ||
	    agent_context_artifact_find_locked(lifecycle, control->handle) != 0) {
		status = AGENT_STATUS_STALE;
		goto out;
	}
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_CAPACITY; i++) {
		struct agent_context_artifact_record *record =
			&agent_context_artifacts[i];

		if (!record->used && slot == 0)
			slot = record;
		if (record->used &&
		    workflow_lifecycle_key_equal(record->lifecycle, lifecycle) &&
		    record->producer_control_id == p->agent_control_id) {
			count++;
			bytes += record->length;
		}
	}
	if (slot == 0 || count >= p->agent_artifact_count_limit ||
	    bytes > p->agent_artifact_bytes_limit ||
	    control->length > p->agent_artifact_bytes_limit - bytes) {
		status = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	*slot = staged;
	status = AGENT_STATUS_OK;
	agent_context_artifact_result_fill(slot, status, result);
out:
	intr_restore(enabled);
	return status;
}

static int
agent_context_artifact_bind(
	struct proc *p, const struct agent_context_artifact_control *control,
	struct agent_context_artifact_result *result)
{
	struct workflow_lifecycle_key lifecycle =
		agent_context_artifact_lifecycle(p);
	struct agent_context_artifact_record *record;
	struct agent_context_artifact_binding *binding = 0;
	int enabled = intr_save();

	record = agent_context_artifact_find_locked(lifecycle, control->handle);
	if (record == 0 ||
	    (record->producer_control_id != p->agent_control_id &&
	     ((record->flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) == 0 ||
	      !agent_identity_has_cap(p, AGENT_CAP_CONTENT_READ))) ||
	    !agent_context_artifact_active_sequence(
		    p, control->source_context_sequence)) {
		intr_restore(enabled);
		return AGENT_STATUS_DENIED;
	}
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_BINDINGS; i++) {
		struct agent_context_artifact_binding *candidate =
			&agent_context_artifact_bindings[p - pool][i];

		if (candidate->used && candidate->control_id == p->agent_control_id &&
		    candidate->sequence == control->source_context_sequence &&
		    candidate->handle == control->handle) {
			binding = candidate;
			break;
		}
		if (!candidate->used && binding == 0)
			binding = candidate;
	}
	if (binding == 0) {
		intr_restore(enabled);
		return AGENT_STATUS_NO_SPACE;
	}
	if (!binding->used) {
		binding->used = 1;
		binding->control_id = p->agent_control_id;
		binding->sequence = control->source_context_sequence;
		binding->handle = control->handle;
		record->references++;
	}
	agent_context_artifact_result_fill(record, AGENT_STATUS_OK, result);
	intr_restore(enabled);
	return AGENT_STATUS_OK;
}

void agent_context_artifact_init(void)
{
	memset(agent_context_artifacts, 0, sizeof(agent_context_artifacts));
	memset(agent_context_artifact_bindings, 0,
	       sizeof(agent_context_artifact_bindings));
}

static void
agent_context_artifact_binding_drop_locked(
	struct workflow_lifecycle_key lifecycle,
	struct agent_context_artifact_binding *binding)
{
	struct agent_context_artifact_record *record;

	if (intr_get())
		panic("Context Artifact binding drop unlocked");
	if (binding == 0 || !binding->used)
		return;
	record = agent_context_artifact_find_locked(lifecycle, binding->handle);
	if (record != 0 && record->references != 0)
		record->references--;
	memset(binding, 0, sizeof(*binding));
}

void agent_context_artifact_rollback(struct proc *p, uint64 sequence)
{
	struct workflow_lifecycle_key lifecycle;
	int enabled;

	(void)sequence;
	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	lifecycle = agent_context_artifact_lifecycle(p);
	enabled = intr_save();
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_BINDINGS; i++) {
		struct agent_context_artifact_binding *binding =
			&agent_context_artifact_bindings[p - pool][i];

		if (binding->used && binding->control_id == p->agent_control_id &&
		    !agent_context_artifact_active_sequence(p, binding->sequence))
			agent_context_artifact_binding_drop_locked(
				lifecycle, binding);
	}
	intr_restore(enabled);
}

void agent_context_artifact_proc_reset(struct proc *p)
{
	struct workflow_lifecycle_key lifecycle;
	uint64 control_id;
	int enabled;

	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	lifecycle = agent_context_artifact_lifecycle(p);
	control_id = p->agent_control_id;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_BINDINGS; i++)
		agent_context_artifact_binding_drop_locked(
			lifecycle,
			&agent_context_artifact_bindings[p - pool][i]);
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_CAPACITY; i++) {
		struct agent_context_artifact_record *record =
			&agent_context_artifacts[i];

		if (record->used && record->producer_control_id == control_id &&
		    workflow_lifecycle_key_equal(record->lifecycle, lifecycle) &&
		    record->references == 0 &&
		    (record->flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) == 0)
			memset(record, 0, sizeof(*record));
	}
	intr_restore(enabled);
}

void agent_context_artifact_reclaim_lifecycle(
	struct workflow_lifecycle_key lifecycle)
{
	int enabled;

	if (!workflow_lifecycle_key_valid(lifecycle))
		return;
	enabled = intr_save();
	for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_CAPACITY; i++) {
		struct agent_context_artifact_record *record =
			&agent_context_artifacts[i];

		if (record->used && workflow_lifecycle_key_equal(
					    record->lifecycle, lifecycle))
			memset(record, 0, sizeof(*record));
	}
	intr_restore(enabled);
}

int agent_context_artifact_task_result_valid(
	struct proc *p, uint64 handle, uint64 task_id, uint expected_kind,
	struct workflow_lifecycle_key lifecycle)
{
	struct agent_context_artifact_record *record;
	int valid;
	int enabled = intr_save();

	record = agent_context_artifact_find_locked(lifecycle, handle);
	valid = p != 0 && handle != 0 && task_id != 0 && record != 0 &&
		record->state == AGENT_CONTEXT_ARTIFACT_STATE_SEALED &&
		record->producer_pid == p->pid &&
		record->producer_agent_id == (uint)p->agent_id &&
		record->producer_control_id == p->agent_control_id &&
		record->task_id == task_id && record->kind == expected_kind &&
		record->references != 0;
	intr_restore(enabled);
	return valid;
}

int agent_context_artifact_task_input_valid(
	struct proc *p, uint64 handle,
	struct workflow_lifecycle_key lifecycle)
{
	struct agent_context_artifact_record *record;
	int bound = 0;
	int enabled = intr_save();

	record = agent_context_artifact_find_locked(lifecycle, handle);
	if (p != 0 && p >= pool && p < &pool[NPROC] && handle != 0 &&
	    record != 0 && record->state == AGENT_CONTEXT_ARTIFACT_STATE_SEALED &&
	    (record->producer_control_id == p->agent_control_id ||
	     (record->flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) != 0))
		for (uint i = 0; i < AGENT_CONTEXT_ARTIFACT_BINDINGS; i++) {
			struct agent_context_artifact_binding *binding =
				&agent_context_artifact_bindings[p - pool][i];

			if (binding->used &&
			    binding->control_id == p->agent_control_id &&
			    binding->handle == handle &&
			    agent_context_artifact_active_sequence(
				    p, binding->sequence)) {
				bound = 1;
				break;
			}
		}
	intr_restore(enabled);
	return bound;
}

int sys_agent_context_artifact(uint64 controladdr, uint64 resultaddr)
{
	struct agent_context_artifact_control control;
	struct agent_context_artifact_result result;
	struct agent_context_artifact_record *record;
	struct workflow_lifecycle_key lifecycle;
	struct proc *p = curr_proc();
	int status = AGENT_STATUS_BAD_PARAM;
	int enabled;

	memset(&result, 0, sizeof(result));
	if (p == 0 || !p->is_agent || controladdr == 0 || resultaddr == 0 ||
	    user_range_check(p->pagetable, controladdr, sizeof(control), PTE_R) < 0 ||
	    user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0 ||
	    copyin(p->pagetable, (char *)&control, controladdr,
		   sizeof(control)) < 0)
		return -1;
	if (control.version != AGENT_CONTEXT_ARTIFACT_VERSION ||
	    control.size != sizeof(control) || control.reserved_tail[0] != 0 ||
	    control.reserved_tail[1] != 0 || control.reserved_tail[2] != 0 ||
	    control.reserved_tail[3] != 0)
		goto copy;
	if (control.operation == AGENT_CONTEXT_ARTIFACT_SEAL) {
		status = agent_context_artifact_seal(p, &control, &result);
		goto copy;
	}
	if (control.operation == AGENT_CONTEXT_ARTIFACT_BIND) {
		status = agent_context_artifact_bind(p, &control, &result);
		goto copy;
	}
	lifecycle = agent_context_artifact_lifecycle(p);
	enabled = intr_save();
	record = agent_context_artifact_find_locked(lifecycle, control.handle);
	if (record == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto unlock;
	}
	if (control.operation == AGENT_CONTEXT_ARTIFACT_QUERY) {
		if (record->producer_control_id != p->agent_control_id &&
		    ((record->flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) == 0 ||
		     !agent_identity_has_cap(p, AGENT_CAP_CONTENT_READ))) {
			status = AGENT_STATUS_DENIED;
			goto unlock;
		}
		status = AGENT_STATUS_OK;
	} else if (control.operation == AGENT_CONTEXT_ARTIFACT_SHARE) {
		if (record->producer_control_id != p->agent_control_id ||
		    !agent_identity_has_cap(p, AGENT_CAP_ARTIFACT_WRITE) ||
		    (record->flags & AGENT_CONTEXT_ARTIFACT_F_SHAREABLE) == 0) {
			status = AGENT_STATUS_DENIED;
			goto unlock;
		}
		record->flags |= AGENT_CONTEXT_ARTIFACT_F_SHARED;
		status = AGENT_STATUS_OK;
	} else if (control.operation == AGENT_CONTEXT_ARTIFACT_RELEASE) {
		if (record->producer_control_id != p->agent_control_id ||
		    record->references != 0 ||
		    (record->flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) != 0) {
			status = AGENT_STATUS_CONFLICT;
			goto unlock;
		}
		memset(record, 0, sizeof(*record));
		status = AGENT_STATUS_OK;
		record = 0;
	} else {
		status = AGENT_STATUS_BAD_PARAM;
	}
unlock:
	agent_context_artifact_result_fill(record, status, &result);
	intr_restore(enabled);
copy:
	if (result.version == 0)
		agent_context_artifact_result_fill(0, status, &result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	return status;
}
