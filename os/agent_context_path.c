#include "agent_context_path.h"
#include "agent.h"
#include "defs.h"
#include "kernel_work.h"
#include "proc.h"

static uint64
context_hash_mix(uint64 hash, uint64 value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= (uchar)(value & 0xff);
		hash *= 1099511628211ULL;
		value >>= 8;
	}
	return hash;
}

static uint64
context_hash_bytes(uint64 hash, const char *buffer, int length)
{
	for (int i = 0; i < length; i++) {
		hash ^= (uchar)buffer[i];
		hash *= 1099511628211ULL;
	}
	return hash;
}

uint64
agent_context_record_hash(const struct agent_context_record *record)
{
	uint64 hash = 1469598103934665603ULL;

	hash = context_hash_mix(hash, record->prev_hash);
	hash = context_hash_mix(hash, record->sequence);
	hash = context_hash_mix(hash, record->request_id);
	hash = context_hash_mix(hash, record->cause_sequence);
	hash = context_hash_mix(hash, record->span_id);
	hash = context_hash_mix(hash, record->branch_generation);
	hash = context_hash_mix(hash, record->path_parent_sequence);
	hash = context_hash_mix(hash, record->arg0);
	hash = context_hash_mix(hash, record->value0);
	hash = context_hash_mix(hash, record->value1);
	hash = context_hash_mix(hash, record->value2);
	hash = context_hash_mix(hash, record->tick);
	hash = context_hash_mix(hash, record->flags);
	hash = context_hash_mix(hash, (uint64)(uint)record->tool_id);
	hash = context_hash_mix(hash, (uint64)(uint)record->status);
	hash = context_hash_bytes(hash, record->payload, sizeof(record->payload));
	hash = context_hash_bytes(hash, record->result, sizeof(record->result));
	return hash ? hash : 1;
}

static int
context_kernel_read(struct proc *p, uint64 offset, char *destination,
		    uint64 length)
{
	uint64 page, page_offset, chunk;

	if (offset + length < offset ||
	    offset + length > AGENT_CONTEXT_KERNEL_PAGES * PAGE_SIZE)
		return -1;
	while (length > 0) {
		page = offset / PAGE_SIZE;
		page_offset = offset % PAGE_SIZE;
		if (page >= AGENT_CONTEXT_KERNEL_PAGES || p->agent_ctx_kva[page] == 0)
			return -1;
		chunk = PAGE_SIZE - page_offset;
		if (chunk > length)
			chunk = length;
		memmove(destination,
			(char *)(p->agent_ctx_kva[page] + page_offset), chunk);
		destination += chunk;
		offset += chunk;
		length -= chunk;
	}
	return 0;
}

int
agent_context_read_record(struct proc *p, uint64 slot,
			  struct agent_context_record *record)
{
	uint64 offset;

	if (p == 0 || record == 0 || slot >= p->context_path_capacity)
		return -1;
	offset = AGENT_CONTEXT_RECORDS_OFFSET + slot * sizeof(*record);
	return context_kernel_read(p, offset, (char *)record, sizeof(*record));
}

static int
context_read_sequence(struct proc *p, uint64 sequence,
		      struct agent_context_record *record)
{
	uint64 slot;

	if (p == 0 || record == 0 || p->context_path_capacity == 0 ||
	    sequence < p->context_path_oldest ||
	    sequence > p->context_path_latest)
		return -1;
	slot = (sequence - 1) % p->context_path_capacity;
	if (agent_context_read_record(p, slot, record) < 0 ||
	    record->sequence != sequence || record->record_hash == 0 ||
	    record->record_hash != agent_context_record_hash(record))
		return -1;
	return 0;
}

static int
context_active_walk(struct proc *p, uint64 head, uint64 target,
		    struct agent_context_record *result, uint64 *count,
		    uint64 *oldest, int checkpoint, uint64 *successors,
		    uint64 successor_capacity)
{
	struct agent_context_record record;
	uint64 cursor = head, seen = 0, expected_hash = 0, successor = 0;
	int status = -1;
	int terminal;

	if (p == 0 || head == 0 ||
	    (successors != 0 && successor_capacity < p->context_path_capacity))
		goto out;
	while (seen < p->context_path_capacity) {
		if (context_read_sequence(p, cursor, &record) < 0 ||
		    (expected_hash != 0 && record.record_hash != expected_hash) ||
		    record.branch_generation == 0 ||
		    record.branch_generation > p->context_branch_generation)
			goto out;
		terminal = record.path_parent_sequence == 0 ||
			   record.path_parent_sequence < p->context_path_oldest;
		if ((!terminal && (record.path_parent_sequence >= cursor ||
				  record.prev_hash == 0)) ||
		    (terminal && ((record.path_parent_sequence == 0) !=
				 (record.prev_hash == 0))))
			goto out;
		if (successors != 0) {
			successors[(record.sequence - 1) % successor_capacity] =
				successor;
			successor = record.sequence;
		}
		if (seen++ == target) {
			if (result == 0)
				goto out;
			*result = record;
			status = 0;
			goto out;
		}
		if (terminal) {
			if (count == 0 || oldest == 0)
				goto out;
			*count = seen;
			*oldest = cursor;
			status = 0;
			goto out;
		}
		expected_hash = record.prev_hash;
		cursor = record.path_parent_sequence;
		if (checkpoint && kernel_work_checkpoint(1) < 0) {
			status = -2;
			goto out;
		}
	}
out:
	return status;
}

int
agent_context_active_measure(struct proc *p, uint64 head,
			     uint64 *count, uint64 *oldest)
{
	if (p == 0 || count == 0 || oldest == 0)
		return -1;
	if (head == 0) {
		*count = 0;
		*oldest = 0;
		return 0;
	}
	return context_active_walk(p, head, ~0ULL, 0, count, oldest, 0, 0, 0);
}

int
agent_context_active_rebuild(struct proc *p, uint64 head, uint64 *successors,
			     uint64 successor_capacity, uint64 *count,
			     uint64 *oldest)
{
	if (p == 0 || successors == 0 || count == 0 || oldest == 0 ||
	    successor_capacity < p->context_path_capacity)
		return -1;
	memset(successors, 0, successor_capacity * sizeof(*successors));
	return context_active_walk(p, head, ~0ULL, 0, count, oldest, 0,
				   successors, successor_capacity);
}
