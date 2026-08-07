#ifndef SYSCALL_COUNTER_H
#define SYSCALL_COUNTER_H

/*
 * syscall 编号属于用户 ABI，内部计数槽只覆盖内核实际登记的调用。
 * 注册表同时给出执行类别，新增调用若未分类便无法通过闭集检查。
 */
#define SYSCALL_COUNT_MAX 600

enum syscall_class {
	SYSCALL_CLASS_INVALID = 0,
	SYSCALL_CLASS_FAST,
	SYSCALL_CLASS_DESCRIPTOR,
	SYSCALL_CLASS_BLOCK_IO,
	SYSCALL_CLASS_FS_EPOCH,
	SYSCALL_CLASS_BLOCK_IO_FS_EPOCH,
};

#define SYSCALL_ENABLED_ALWAYS 1
#ifdef VIRTIO_DISK_TEST_PROFILE
#define SYSCALL_ENABLED_VIRTIO_TEST 1
#else
#define SYSCALL_ENABLED_VIRTIO_TEST 0
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
#define SYSCALL_ENABLED_PHYSICAL_PAGE_TEST 1
#else
#define SYSCALL_ENABLED_PHYSICAL_PAGE_TEST 0
#endif
#ifdef AGENT_METADATA_CRASH_PHASE
#define SYSCALL_ENABLED_METADATA_TEST 1
#else
#define SYSCALL_ENABLED_METADATA_TEST 0
#endif
#ifdef WAIT_ATOMIC_TEST_PROFILE
#define SYSCALL_ENABLED_WAIT_ATOMIC_TEST 1
#else
#define SYSCALL_ENABLED_WAIT_ATOMIC_TEST 0
#endif
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
#define SYSCALL_ENABLED_FS_ALLOCATOR_TEST 1
#else
#define SYSCALL_ENABLED_FS_ALLOCATOR_TEST 0
#endif

/* 名称、执行类别、构建可用性共同构成唯一 syscall 注册表。 */
#define SYSCALL_REGISTERED(X) \
	X(write, DESCRIPTOR, ALWAYS) \
	X(read, DESCRIPTOR, ALWAYS) \
	X(fstat, FAST, ALWAYS) \
	X(openat, BLOCK_IO, ALWAYS) \
	X(unlinkat, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(close, DESCRIPTOR, ALWAYS) \
	X(sync, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(fsync, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(fdatasync, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(exit, FAST, ALWAYS) \
	X(sched_yield, FAST, ALWAYS) \
	X(brk, FAST, ALWAYS) \
	X(gettimeofday, FAST, ALWAYS) \
	X(getpid, FAST, ALWAYS) \
	X(getppid, FAST, ALWAYS) \
	X(mailread, FAST, ALWAYS) \
	X(mailwrite, FAST, ALWAYS) \
	X(trace, FAST, ALWAYS) \
	X(clone, FAST, ALWAYS) \
	X(execve, BLOCK_IO, ALWAYS) \
	X(wait4, FAST, ALWAYS) \
	X(pipe2, FAST, ALWAYS) \
	X(thread_create, FAST, ALWAYS) \
	X(gettid, FAST, ALWAYS) \
	X(waittid, FAST, ALWAYS) \
	X(mutex_create, FAST, ALWAYS) \
	X(mutex_lock, FAST, ALWAYS) \
	X(mutex_unlock, FAST, ALWAYS) \
	X(semaphore_create, FAST, ALWAYS) \
	X(semaphore_up, FAST, ALWAYS) \
	X(semaphore_down, FAST, ALWAYS) \
	X(condvar_create, FAST, ALWAYS) \
	X(condvar_signal, FAST, ALWAYS) \
	X(condvar_wait, FAST, ALWAYS) \
	X(kernel_work_last_preemptions, FAST, ALWAYS) \
	X(io_policy_info, FAST, ALWAYS) \
	X(agent_create, FAST, ALWAYS) \
	X(agent_create_role, FAST, ALWAYS) \
	X(agent_workflow_create, FAST, ALWAYS) \
	X(agent_scope_delegate_fd, FAST, ALWAYS) \
	X(agent_workflow_close, FS_EPOCH, ALWAYS) \
	X(agent_workflow_lifecycle_info, FAST, ALWAYS) \
	X(agent_resource_snapshot, FAST, ALWAYS) \
	X(agent_performance_snapshot, FAST, ALWAYS) \
	X(agent_info, FAST, ALWAYS) \
	X(agent_sched_snapshot, FAST, ALWAYS) \
	X(agent_sched_config, FAST, ALWAYS) \
	X(agent_trace_snapshot, FAST, ALWAYS) \
	X(agent_audit_snapshot, FAST, ALWAYS) \
	X(agent_audit_query, FAST, ALWAYS) \
	X(agent_audit_receipt, BLOCK_IO, ALWAYS) \
	X(agent_span_trace_snapshot, FAST, ALWAYS) \
	X(agent_timeline_snapshot, FAST, ALWAYS) \
	X(agent_timeline_query, FAST, ALWAYS) \
	X(agent_timeline_wait, FAST, ALWAYS) \
	X(agent_timeline_read, FAST, ALWAYS) \
	X(agent_provenance_snapshot, FAST, ALWAYS) \
	X(agent_ledger_snapshot, FAST, ALWAYS) \
	X(agent_run, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_call, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_tool_list, FAST, ALWAYS) \
	X(tool_call, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(tool_list, FAST, ALWAYS) \
	X(agent_observe_recovery, FS_EPOCH, ALWAYS) \
	X(virtio_disk_test, BLOCK_IO, VIRTIO_TEST) \
	X(physical_page_test, FS_EPOCH, PHYSICAL_PAGE_TEST) \
	X(agent_metadata_test, FS_EPOCH, METADATA_TEST) \
	X(wait_atomic_test, FS_EPOCH, WAIT_ATOMIC_TEST) \
	X(fs_allocator_fault_test, BLOCK_IO_FS_EPOCH, FS_ALLOCATOR_TEST) \
	X(context_push, FAST, ALWAYS) \
	X(context_query, FAST, ALWAYS) \
	X(context_snapshot, FAST, ALWAYS) \
	X(context_detail, FAST, ALWAYS) \
	X(context_rollback, FAST, ALWAYS) \
	X(context_clear, FAST, ALWAYS) \
	X(agent_watch, FAST, ALWAYS) \
	X(agent_unwatch, FAST, ALWAYS) \
	X(agent_wait, FAST, ALWAYS) \
	X(agent_wait_cancel, FAST, ALWAYS) \
	X(agent_heartbeat, FAST, ALWAYS) \
	X(agent_heartbeat_set, FAST, ALWAYS) \
	X(agent_heartbeat_stop, FAST, ALWAYS) \
	X(agent_wake, FAST, ALWAYS) \
	X(agent_file_meta_init, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_file_meta_set, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_file_query, BLOCK_IO, ALWAYS) \
	X(agent_file_edit_begin, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_file_edit_commit, FAST, ALWAYS) \
	X(agent_file_edit_abort, FAST, ALWAYS) \
	X(agent_file_edit_state, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_worker_create, BLOCK_IO_FS_EPOCH, ALWAYS) \
	X(agent_route_config, FAST, ALWAYS)

enum syscall_counter_slot {
#define SYSCALL_COUNTER_ENUM(name, class, enabled) SYSCALL_COUNTER_SLOT_##name,
	SYSCALL_REGISTERED(SYSCALL_COUNTER_ENUM)
#undef SYSCALL_COUNTER_ENUM
	SYSCALL_COUNTER_SLOTS,
};

#endif
