#include "syscall.h"
#include <agent.h>
#include <agent_metadata_test_abi.h>
#ifdef WAIT_ATOMIC_TEST_PROFILE
#include <wait_atomic_test_abi.h>
#endif
#include <stddef.h>
#include <unistd.h>
#include <stdio.h>


void __write_buffer();
void __clear_buffer();
int __stdio_process_spawn_prepare(void);
int __stdio_process_spawn_finish(int locked, int result);

static int process_spawn_finish(int locked, int result)
{
	return __stdio_process_spawn_finish(locked, result);
}

int open(const char *path, int flags)
{
	return syscall(SYS_openat, path, flags, O_RDWR);
}

int close(int fd)
{
	if (fd == 1) {
		__write_buffer();
		__clear_buffer();
	}
	return syscall(SYS_close, fd);
}

ssize_t read(int fd, void *buf, size_t len)
{
	return syscall(SYS_read, fd, buf, len);
}

ssize_t write(int fd, const void *buf, size_t len)
{
	return syscall(SYS_write, fd, buf, len);
}

int sync(void)
{
	return syscall(SYS_sync);
}

int fsync(int fd)
{
	return syscall(SYS_fsync, fd);
}

int fdatasync(int fd)
{
	return syscall(SYS_fdatasync, fd);
}

int getpid()
{
	return syscall(SYS_getpid);
}

int getppid()
{
	return syscall(SYS_getppid);
}

int sched_yield()
{
	return syscall(SYS_sched_yield);
}

int io_policy_info(struct io_policy_info *info)
{
	return syscall(SYS_io_policy_info, info, sizeof(*info));
}

int fork()
{
	int locked = __stdio_process_spawn_prepare();

	if (locked < 0)
		return -1;
	return process_spawn_finish(locked, syscall(SYS_clone));
}

void exit(int code)
{
	__write_buffer();
	__clear_buffer();
	syscall(SYS_exit, code);
}

int waitpid(int pid, int *code)
{
	return syscall(SYS_wait4, pid, code);
}

static char **null = { 0 };
int exec(const char *name, char **argv)
{
	return syscall(SYS_execve, name, argv == 0 ? null : argv);
}

int64 get_mtime()
{
	TimeVal time;
	int err = sys_get_time(&time, 0);
	if (err == 0) {
		return (time.sec * 1000 + time.usec / 1000);
	}
	// get_time should never failed.
	return -1;
}

int sys_get_time(TimeVal *ts, int tz)
{
	return syscall(SYS_gettimeofday, ts, tz);
}

int sleep(unsigned long long time)
{
	unsigned long long s = get_mtime();
	while (get_mtime() < s + time) {
		sched_yield();
	}
	return 0;
}

int set_priority(int prio)
{
	return syscall(SYS_setpriority, prio);
}

int mmap(void *start, unsigned long long len, int prot, int flags)
{
	return syscall(SYS_mmap, start, len, prot, flags);
}

int munmap(void *start, unsigned long long len)
{
	return syscall(SYS_munmap, start, len);
}

int wait(int *code)
{
	return waitpid(-1, code);
}

int spawn(const char *file)
{
	return syscall(SYS_spawn, file);
}

int pipe(void *p)
{
	return syscall(SYS_pipe2, p);
}

int mailread(void *buf, int len)
{
	return syscall(SYS_mailread, buf, len);
}

int mailwrite(int pid, void *buf, int len)
{
	return syscall(SYS_mailwrite, pid, buf, len);
}

int fstat(int fd, Stat *st)
{
	return syscall(SYS_fstat, fd, st);
}

int sys_linkat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath,
	       unsigned int flags)
{
	return syscall(SYS_linkat, olddirfd, oldpath, newdirfd, newpath, flags);
}

int sys_unlinkat(int dirfd, const char *path, unsigned int flags)
{
	return syscall(SYS_unlinkat, dirfd, path, flags);
}

int link(const char *old_path, const char *new_path)
{
	return sys_linkat(AT_FDCWD, old_path, AT_FDCWD, new_path, 0);
}

int unlink(const char *path)
{
	return sys_unlinkat(AT_FDCWD, path, 0);
}

int thread_create(void *entry, void *arg)
{
	// on first thread create enable, here must be single thread
	if (!buffer_lock_enabled) {
		enable_thread_io_buffer();
	}
	return syscall(SYS_thread_create, (uint64)entry, (uint64)arg);
}

int gettid(void)
{
	return syscall(SYS_gettid);
}

int waittid(int tid)
{
	return syscall(SYS_waittid, tid);
}

int mutex_create()
{
	return syscall(SYS_mutex_create, 0);
}

int mutex_blocking_create()
{
	return syscall(SYS_mutex_create, 1);
}

int mutex_lock(int mid)
{
	return syscall(SYS_mutex_lock, mid);
}

int mutex_unlock(int mid)
{
	return syscall(SYS_mutex_unlock, mid);
}

int semaphore_create(int res_count)
{
	return syscall(SYS_semaphore_create, res_count);
}

int semaphore_up(int sid)
{
	return syscall(SYS_semaphore_up, sid);
}

int semaphore_down(int sid)
{
	return syscall(SYS_semaphore_down, sid);
}

int condvar_create()
{
	return syscall(SYS_condvar_create);
}

int condvar_signal(int cid)
{
	return syscall(SYS_condvar_signal, cid);
}

int condvar_wait(int cid, int mid)
{
	return syscall(SYS_condvar_wait, cid, mid);
}

long kernel_work_last_preemptions(void)
{
	return syscall(SYS_kernel_work_last_preemptions);
}

int kernel_work_receipt_snapshot(struct kernel_work_receipt *receipt)
{
	return syscall(SYS_kernel_work_receipt_snapshot, receipt,
		       sizeof(*receipt));
}

int enable_deadlock_detect(int enabled)
{
	return syscall(SYS_enable_deadlock_detect, enabled);
}

long sbrk(long n)
{
	return syscall(SYS_sbrk, n);
}

int trace(enum trace_request req, unsigned long id, uint8 data)
{
	return syscall(SYS_trace, req, id, data);
}

int trace_read(const uint8 *addr)
{
	return trace(TRACE_READ, (unsigned long) addr, 0);
}

int trace_write(uint8 *addr, uint8 data)
{
	return trace(TRACE_WRITE, (unsigned long) addr, data);
}

int count_syscall(int id)
{
	return trace(TRACE_SYSCALL, id, 0);
}

int agent_create(void)
{
	int locked = __stdio_process_spawn_prepare();

	if (locked < 0)
		return -1;
	return process_spawn_finish(locked, syscall(SYS_agent_create));
}

int agent_create_role(int role)
{
	int locked = __stdio_process_spawn_prepare();

	if (locked < 0)
		return -1;
	return process_spawn_finish(
		locked, syscall(SYS_agent_create_role, role));
}

int agent_workflow_create(int role)
{
	int locked = __stdio_process_spawn_prepare();

	if (locked < 0)
		return -1;
	return process_spawn_finish(
		locked, syscall(SYS_agent_workflow_create, role));
}

int agent_workflow_close(uint64 scope_id)
{
	return syscall(SYS_agent_workflow_close, scope_id);
}

int agent_workflow_lifecycle_info(
	struct agent_workflow_lifecycle_info *info,
	const struct agent_workflow_lifecycle_key *expected)
{
	uint64 flags = 0;
	uint64 expected_id = 0;
	uint64 expected_generation = 0;

	if (expected != 0) {
		if (expected->reserved != 0)
			return AGENT_STATUS_BAD_PARAM;
		flags = AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT;
		expected_id = expected->id;
		expected_generation = expected->generation;
	}
	return syscall(SYS_agent_workflow_lifecycle_info, info,
		       sizeof(*info), flags, expected_id,
		       expected_generation);
}

int agent_resource_snapshot(struct agent_resource_snapshot *snapshot)
{
	return syscall(SYS_agent_resource_snapshot, snapshot,
		       sizeof(*snapshot));
}

int agent_performance_snapshot(struct agent_performance_snapshot *snapshot)
{
	return syscall(SYS_agent_performance_snapshot, snapshot,
		       sizeof(*snapshot));
}

int agent_scope_delegate_fd(int fd)
{
	return syscall(SYS_agent_scope_delegate_fd, fd);
}

int agent_worker_create(const char *image, uint64 capabilities)
{
	int locked = __stdio_process_spawn_prepare();

	if (locked < 0)
		return -1;
	return process_spawn_finish(
		locked, syscall(SYS_agent_worker_create, image, capabilities));
}

int agent_info(struct agent_info *info)
{
	return syscall(SYS_agent_info, info);
}

int agent_sched_snapshot(struct agent_sched_record *records, int max)
{
	return syscall(SYS_agent_sched_snapshot, records, max);
}

int agent_sched_config(struct agent_sched_config *config)
{
	return syscall(SYS_agent_sched_config, config);
}

int agent_trace_snapshot(struct agent_trace_record *records, int max)
{
	return syscall(SYS_agent_trace_snapshot, records, max);
}

int agent_audit_snapshot(struct agent_audit_record *records, int max)
{
	return syscall(SYS_agent_audit_snapshot, records, max);
}

int agent_audit_query(struct agent_audit_filter *filter,
		      struct agent_audit_record *records, int max)
{
	return syscall(SYS_agent_audit_query, filter, records, max);
}

int agent_audit_receipt(struct agent_audit_receipt_request *request)
{
	return syscall(SYS_agent_audit_receipt, request);
}

int agent_span_trace_snapshot(struct agent_audit_record *records, int max)
{
	return syscall(SYS_agent_span_trace_snapshot, records, max);
}

int agent_timeline_snapshot(struct agent_timeline_record *records, int max)
{
	return syscall(SYS_agent_timeline_snapshot, records, max);
}

int agent_timeline_query(struct agent_timeline_filter *filter,
			 struct agent_timeline_record *records, int max)
{
	return syscall(SYS_agent_timeline_query, filter, records, max);
}

int agent_timeline_wait(struct agent_timeline_filter *filter, int timeout_ticks)
{
	return syscall(SYS_agent_timeline_wait, filter, timeout_ticks);
}

int agent_timeline_read(struct agent_timeline_filter *filter,
			struct agent_timeline_record *records, int max,
			int timeout_ticks)
{
	return syscall(SYS_agent_timeline_read, filter, records, max,
		       timeout_ticks);
}

int agent_provenance_snapshot(struct agent_provenance_edge *edges, int max)
{
	return syscall(SYS_agent_provenance_snapshot, edges, max);
}

int agent_ledger_snapshot(struct agent_ledger_summary *summary)
{
	return syscall(SYS_agent_ledger_snapshot, summary);
}

int agent_file_prefetch_snapshot(struct agent_file_prefetch_hint *hints,
				 int max)
{
	return syscall(SYS_agent_file_prefetch_snapshot, hints, max);
}

int agent_file_prefetch_span_snapshot(struct agent_file_prefetch_hint *hints,
				      int max)
{
	return syscall(SYS_agent_file_prefetch_span_snapshot, hints, max);
}

int agent_run(struct agent_op *ops, struct agent_result *results, int count,
	      uint64 flags)
{
	return syscall(SYS_agent_run, ops, results, count, flags);
}

int agent_call(struct agent_request *req, struct agent_response *resp)
{
	return syscall(SYS_agent_call, req, resp);
}

int agent_tool_list(struct agent_tool_desc *out, int max)
{
	return syscall(SYS_agent_tool_list, out, max);
}

int sys_tool_call(struct agent_request_v2 *req, struct agent_response_v2 *resp)
{
	return syscall(SYS_tool_call, req, resp);
}

int sys_tool_list(struct agent_tool_desc_v2 *out, int max)
{
	return syscall(SYS_tool_list, out, max,
		       sizeof(struct agent_tool_desc_v2), AGENT_CALL_VERSION_V2);
}

int tool_call(struct agent_request_v2 *req, struct agent_response_v2 *resp)
{
	return sys_tool_call(req, resp);
}

int tool_list(struct agent_tool_desc_v2 *out, int max)
{
	return sys_tool_list(out, max);
}

int agent_observe_recovery(struct agent_observe_recovery_request *request,
			   void *records)
{
	return syscall(SYS_agent_observe_recovery, request, records);
}

int context_push(struct agent_context_record *record)
{
	return syscall(SYS_context_push, record);
}

int context_query(uint64 start_sequence, struct agent_context_record *out,
		  int max)
{
	return syscall(SYS_context_query, start_sequence, out, max);
}

int context_snapshot(struct agent_context_header *header,
		     struct agent_context_record *records, int max)
{
	return syscall(SYS_context_snapshot, header, records, max);
}

static uint64 context_mirror_hash_mix(uint64 hash, uint64 value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= (unsigned char)(value & 0xff);
		hash *= 1099511628211ULL;
		value >>= 8;
	}
	return hash;
}

static uint64 context_mirror_hash_bytes(uint64 hash, const char *buffer,
					int length)
{
	for (int i = 0; i < length; i++) {
		hash ^= (unsigned char)buffer[i];
		hash *= 1099511628211ULL;
	}
	return hash;
}

static uint64 context_mirror_record_hash(
	const struct agent_context_record *record)
{
	uint64 hash = 1469598103934665603ULL;

	hash = context_mirror_hash_mix(hash, record->prev_hash);
	hash = context_mirror_hash_mix(hash, record->sequence);
	hash = context_mirror_hash_mix(hash, record->request_id);
	hash = context_mirror_hash_mix(hash, record->cause_sequence);
	hash = context_mirror_hash_mix(hash, record->span_id);
	hash = context_mirror_hash_mix(hash, record->branch_generation);
	hash = context_mirror_hash_mix(hash, record->path_parent_sequence);
	hash = context_mirror_hash_mix(hash, record->arg0);
	hash = context_mirror_hash_mix(hash, record->value0);
	hash = context_mirror_hash_mix(hash, record->value1);
	hash = context_mirror_hash_mix(hash, record->value2);
	hash = context_mirror_hash_mix(hash, record->tick);
	hash = context_mirror_hash_mix(hash, record->flags);
	hash = context_mirror_hash_mix(hash, (uint64)(uint)record->tool_id);
	hash = context_mirror_hash_mix(hash, (uint64)(uint)record->status);
	hash = context_mirror_hash_bytes(hash, record->payload,
					 sizeof(record->payload));
	hash = context_mirror_hash_bytes(hash, record->result,
					 sizeof(record->result));
	return hash ? hash : 1;
}

static int context_mirror_record(const struct agent_context_header *header,
				 const struct agent_context_record *records,
				 uint64 sequence,
				 struct agent_context_record *record)
{
	uint64 slot;

	if (sequence < header->oldest_sequence ||
	    sequence > header->latest_sequence)
		return -1;
	slot = (sequence - 1) % header->capacity;
	*record = records[slot];
	if (record->sequence != sequence || record->record_hash == 0 ||
	    record->record_hash != context_mirror_record_hash(record) ||
	    (record->path_parent_sequence != 0 &&
	     record->path_parent_sequence >= sequence))
		return -1;
	return 0;
}

static int context_mirror_active_record(
	const struct agent_context_header *header,
	const struct agent_context_record *records, uint64 index,
	struct agent_context_record *record)
{
	struct agent_context_record cursor_record;
	uint64 cursor = header->visible_head_sequence;
	uint64 expected_hash = 0;
	uint64 steps;

	if (index >= header->active_path_count)
		return -1;
	steps = header->active_path_count - index - 1;
	while (steps-- > 0) {
		if (context_mirror_record(header, records, cursor,
					  &cursor_record) < 0 ||
		    (expected_hash != 0 &&
		     cursor_record.record_hash != expected_hash) ||
		    cursor_record.prev_hash == 0 ||
		    cursor_record.path_parent_sequence == 0 ||
		    cursor_record.path_parent_sequence < header->oldest_sequence)
			return -1;
		expected_hash = cursor_record.prev_hash;
		cursor = cursor_record.path_parent_sequence;
	}
	if (context_mirror_record(header, records, cursor, record) < 0 ||
	    (expected_hash != 0 && record->record_hash != expected_hash))
		return -1;
	return 0;
}

int context_mirror_active_query(const struct agent_context_header *header,
				const struct agent_context_record *mirror_records,
				uint64 start_sequence,
				struct agent_context_record *out, int max)
{
	struct agent_context_record record;
	uint64 parent;
	int copied = 0;

	if (header == 0 || mirror_records == 0 || max < 0 ||
	    (max > 0 && out == 0) || header->magic != AGENT_CONTEXT_MAGIC ||
	    header->version != AGENT_CONTEXT_VERSION || header->capacity == 0 ||
	    header->capacity > AGENT_CONTEXT_MAX_RECORDS ||
	    header->count > header->capacity ||
	    header->active_path_count > header->count)
		return -1;
	if (header->active_path_count == 0)
		return header->count == 0 && header->visible_head_sequence == 0 &&
			       header->active_path_oldest_sequence == 0 ?
			       0 :
			       -1;
	if (header->count == 0 || header->oldest_sequence == 0 ||
	    header->latest_sequence < header->oldest_sequence ||
	    header->latest_sequence - header->oldest_sequence + 1 !=
		    header->count ||
	    header->visible_head_sequence < header->oldest_sequence ||
	    header->visible_head_sequence > header->latest_sequence ||
	    header->active_path_oldest_sequence < header->oldest_sequence ||
	    header->active_path_oldest_sequence >
		    header->visible_head_sequence)
		return -1;
	for (uint64 index = 0; index < header->active_path_count; index++) {
		if (context_mirror_active_record(header, mirror_records, index,
						 &record) < 0)
			return -1;
		if ((index == 0 && record.sequence !=
					   header->active_path_oldest_sequence) ||
		    (index + 1 == header->active_path_count &&
		     (record.sequence != header->visible_head_sequence ||
		      record.record_hash != header->latest_record_hash)))
			return -1;
		if (index == 0) {
			parent = record.path_parent_sequence;
			if ((parent == 0 && record.prev_hash != 0) ||
			    (parent != 0 &&
			     (parent >= header->oldest_sequence ||
			      record.prev_hash == 0)))
				return -1;
		} else {
			struct agent_context_record previous;

			if (context_mirror_active_record(header, mirror_records,
						 index - 1, &previous) < 0 ||
			    record.path_parent_sequence != previous.sequence ||
			    record.prev_hash != previous.record_hash)
				return -1;
		}
		if (start_sequence != 0 && record.sequence < start_sequence)
			continue;
		if (copied < max)
			out[copied++] = record;
	}
	return copied;
}

int context_detail(uint64 sequence, struct agent_context_detail *detail)
{
	return syscall(SYS_context_detail, sequence, detail);
}

int context_rollback(uint64 sequence)
{
	return syscall(SYS_context_rollback, sequence);
}

int context_clear(void)
{
	return syscall(SYS_context_clear);
}

int agent_watch(int event_type, const char *filter)
{
	return syscall(SYS_agent_watch, event_type, filter);
}

int agent_unwatch(int event_type, const char *filter)
{
	return syscall(SYS_agent_unwatch, event_type, filter);
}

int agent_wait(struct agent_event *event, int timeout_ticks)
{
	return syscall(SYS_agent_wait, event, timeout_ticks);
}

int agent_wait_cancel(int pid, const char *reason)
{
	return syscall(SYS_agent_wait_cancel, pid, reason);
}

int agent_heartbeat(int interval_ticks)
{
	return syscall(SYS_agent_heartbeat, interval_ticks);
}

int sys_agent_heartbeat_set(uint64 interval_ticks)
{
	return syscall(SYS_agent_heartbeat_set, interval_ticks);
}

int sys_agent_heartbeat_stop(void)
{
	return syscall(SYS_agent_heartbeat_stop);
}

int agent_heartbeat_set(uint64 interval_ticks)
{
	return sys_agent_heartbeat_set(interval_ticks);
}

int agent_heartbeat_stop(void)
{
	return sys_agent_heartbeat_stop();
}

int agent_metadata_test_arm_next(struct agent_metadata_test_arm *arm)
{
	return syscall(SYS_agent_metadata_test, AGENT_METADATA_TEST_ARM_NEXT,
		       arm, sizeof(*arm));
}

#ifdef WAIT_ATOMIC_TEST_PROFILE
int wait_atomic_test_arm(uint operation)
{
	return syscall(SYS_wait_atomic_test, WAIT_ATOMIC_TEST_ABI_VERSION,
		       WAIT_ATOMIC_TEST_COMMAND_ARM, operation, 0, 0, 0);
}

int wait_atomic_test_query(uint operation, int target_pid,
			   struct wait_atomic_test_receipt *receipt)
{
	return syscall(SYS_wait_atomic_test, WAIT_ATOMIC_TEST_ABI_VERSION,
		       WAIT_ATOMIC_TEST_COMMAND_QUERY, operation, target_pid,
		       receipt, sizeof(*receipt));
}

int wait_atomic_test_deadline_observe(
	uint phase, struct wait_atomic_deadline_snapshot *snapshot)
{
	return syscall(SYS_wait_atomic_test, WAIT_ATOMIC_TEST_ABI_VERSION,
		       WAIT_ATOMIC_TEST_COMMAND_DEADLINE_OBSERVE, phase, 0,
		       snapshot, sizeof(*snapshot));
}
#endif

int agent_wake(int pid, struct agent_event *event)
{
	return syscall(SYS_agent_wake, pid, event);
}

int agent_file_meta_init(void)
{
	return syscall(SYS_agent_file_meta_init);
}

int agent_file_meta_set(struct agent_file_meta *meta)
{
	return syscall(SYS_agent_file_meta_set, meta);
}

int agent_file_query(struct agent_file_query *query,
		     struct agent_file_query_result *result)
{
	return syscall(SYS_agent_file_query, query, result);
}

int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
			  struct agent_file_edit_state *state)
{
	return syscall(SYS_agent_file_edit_begin, path, flags, ttl_ticks,
		       state);
}

int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			   struct agent_file_edit_state *state)
{
	return syscall(SYS_agent_file_edit_commit, lease_id, expected_version,
		       state);
}

int agent_file_edit_abort(uint64 lease_id)
{
	return syscall(SYS_agent_file_edit_abort, lease_id);
}

int agent_file_edit_state(const char *path,
			  struct agent_file_edit_state *state)
{
	return syscall(SYS_agent_file_edit_state, path, state);
}

int agent_route_config(int source_pid, int target_pid, uint64 event_mask,
		       int operation)
{
	return syscall(SYS_agent_route_config, source_pid, target_pid, event_mask,
		       operation);
}
