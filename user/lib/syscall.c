#include "syscall.h"
#include <agent.h>
#include <stddef.h>
#include <unistd.h>
#include <stdio.h>


void __write_buffer();
void __clear_buffer();

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
	return syscall(SYS_clone);
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
	int ret = syscall(SYS_waittid, tid);
	while (ret == -2) {
		sched_yield();
		ret = syscall(SYS_waittid, tid);
	}
	return ret;
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
	return syscall(SYS_agent_create);
}

int agent_create_role(int role)
{
	return syscall(SYS_agent_create_role, role);
}

int agent_workflow_create(int role)
{
	return syscall(SYS_agent_workflow_create, role);
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

int agent_scope_delegate_fd(int fd)
{
	return syscall(SYS_agent_scope_delegate_fd, fd);
}

int agent_worker_create(const char *image, uint64 capabilities)
{
	return syscall(SYS_agent_worker_create, image, capabilities);
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

int agent_heartbeat_stop(void)
{
	return agent_heartbeat(0);
}

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
