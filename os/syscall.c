#include "console.h"
#include "agent.h"
#include "agent_execution_contract.h"
#include "agent_internal.h"
#include "agent_provenance.h"
#include "bio.h"
#include "defs.h"
#include "fcntl.h"
#include "fs_epoch.h"
#include "kernel_work.h"
#include "loader.h"
#include "sync.h"
#include "syscall.h"
#include "syscall_counter.h"
#include "syscall_ids.h"
#include "timer.h"
#include "trap.h"
#include "user_stack_layout.h"
#include "vfs_security.h"
#ifdef VIRTIO_DISK_TEST_PROFILE
#include "virtio.h"
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
#include "physical_page_test.h"
#endif
#ifdef WAIT_ATOMIC_TEST_PROFILE
#include "wait_atomic_test.h"
#endif
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
#include "fs_allocator_test.h"
#endif

extern struct proc pool[NPROC];

#ifdef VIRTIO_DISK_TEST_PROFILE
static int sys_virtio_disk_test(uint command, uint64 arg0, uint64 arg1,
				uint64 arg2, uint64 arg3, uint64 arg4)
{
	const uint64 known = VIRTIO_TEST_DROP_COMPLETION |
		VIRTIO_TEST_DELAY_COMPLETION | VIRTIO_TEST_FORCE_STATUS |
		VIRTIO_TEST_DISABLE_FLUSH | VIRTIO_TEST_STALL_COMPLETION |
		VIRTIO_TEST_REPEAT | VIRTIO_TEST_STUCK_RESET |
		VIRTIO_TEST_FORGE_USED_INDEX | VIRTIO_TEST_DUPLICATE_USED |
		VIRTIO_TEST_FULL_RING_RECLAIM;
	struct virtio_test_stats stats;
	uint completion_faults;
	uint structural_faults;

	if (!virtio_disk_test_authorized(curr_proc()))
		return -1;

	switch (command) {
	case VIRTIO_TEST_CONFIGURE:
		completion_faults = arg0 & (VIRTIO_TEST_DROP_COMPLETION |
			VIRTIO_TEST_DELAY_COMPLETION |
			VIRTIO_TEST_STALL_COMPLETION);
		structural_faults = arg0 & (VIRTIO_TEST_FORGE_USED_INDEX |
			VIRTIO_TEST_DUPLICATE_USED |
			VIRTIO_TEST_FULL_RING_RECLAIM);
		if ((arg0 & ~known) != 0 || arg1 > 1000 ||
		    arg2 > VIRTIO_TEST_STATUS_UNSUPPORTED ||
		    arg3 > VIRTIO_DISK_REQUEST_TIMEOUT_TICKS || arg4 > 1024 ||
		    (completion_faults & (completion_faults - 1)) != 0 ||
		    (!!(arg0 & (VIRTIO_TEST_DELAY_COMPLETION |
				 VIRTIO_TEST_FULL_RING_RECLAIM)) != !!arg1) ||
		    (!!(arg0 & VIRTIO_TEST_FORCE_STATUS) != !!arg2) ||
		    (structural_faults &&
		     (structural_faults & (structural_faults - 1))) ||
		    ((arg0 & (VIRTIO_TEST_FORGE_USED_INDEX |
			      VIRTIO_TEST_DUPLICATE_USED)) &&
		     (arg0 != structural_faults || arg1 != 0 || arg2 != 0 ||
		      arg4 != 0)) ||
		    ((arg0 & VIRTIO_TEST_FULL_RING_RECLAIM) &&
		     (arg0 != VIRTIO_TEST_FULL_RING_RECLAIM || arg1 > 100 ||
		      arg3 <= 4 * arg1 || arg4 != 0)) ||
		    ((arg0 & VIRTIO_TEST_STUCK_RESET) &&
		     !(arg0 & VIRTIO_TEST_STALL_COMPLETION)) ||
		    ((arg0 & VIRTIO_TEST_DISABLE_FLUSH) &&
		     (arg0 & ~VIRTIO_TEST_DISABLE_FLUSH)))
			return -1;
		return virtio_disk_test_configure(arg0, arg1, arg2, arg3,
						  arg4);
	case VIRTIO_TEST_READ:
		return arg0 < FSSIZE && arg1 == 0 && arg2 == 0 && arg3 == 0 &&
			       arg4 == 0 ?
			virtio_disk_test_read(arg0) : -1;
	case VIRTIO_TEST_READ_RANGE:
		return (arg0 | arg1 | arg2 | arg3 | arg4) == 0 ?
			virtio_disk_test_read_range() : -1;
	case VIRTIO_TEST_FLUSH:
		return (arg0 | arg1 | arg2 | arg3 | arg4) == 0 ?
			virtio_disk_test_flush() : -1;
	case VIRTIO_TEST_STATS:
		if (arg0 == 0 || arg1 != 0 || arg2 != 0 || arg3 != 0 || arg4 != 0)
			return -1;
		virtio_disk_test_stats(&stats);
		return copyout(curr_proc()->pagetable, arg0, (char *)&stats,
			       sizeof(stats));
	default:
		return -1;
	}
}
#endif

#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
static int sys_fs_allocator_fault_test(uint command, uint64 arg0,
				       uint64 arg1, uint64 arg2,
				       uint64 arg3, uint64 arg4)
{
	struct fsalloc_test_snapshot snapshot;

	if (!fs_allocator_test_authorized(curr_proc()))
		return -1;
	switch (command) {
	case FSALLOC_TEST_ARM:
		if (arg4 != 0)
			return -1;
		if (bio_durable_flush() < 0)
			return -1;
		return fs_allocator_test_arm(arg0, arg1, arg2, arg3);
	case FSALLOC_TEST_SNAPSHOT:
		if (arg0 == 0 || arg1 != sizeof(snapshot) ||
		    (arg2 | arg3 | arg4) != 0)
			return -1;
		fs_allocator_test_snapshot(&snapshot);
		return copyout(curr_proc()->pagetable, arg0, (char *)&snapshot,
			       sizeof(snapshot));
	case FSALLOC_TEST_DISARM:
		if ((arg0 | arg1 | arg2 | arg3 | arg4) != 0)
			return -1;
		fs_allocator_test_disarm();
		return 0;
	case FSALLOC_TEST_FLUSH:
		if ((arg0 | arg1 | arg2 | arg3 | arg4) != 0)
			return -1;
		return bio_durable_flush();
	default:
		return -1;
	}
}
#endif

enum trace_request {
	TRACE_READ,
	TRACE_WRITE,
	TRACE_SYSCALL,
};

/* 0 表示未登记，其他值为内部槽号加一。固定映射让所有测试配置布局一致。 */
static const uchar syscall_counter_slot_by_id[SYSCALL_COUNT_MAX] = {
#define SYSCALL_COUNTER_MAP(name, class, enabled) \
	[SYS_##name] = SYSCALL_ENABLED_##enabled ? \
			 (uchar)(SYSCALL_COUNTER_SLOT_##name + 1) : 0,
	SYSCALL_REGISTERED(SYSCALL_COUNTER_MAP)
#define SYSCALL_ALIAS_MAP(alias, target) \
	[SYS_##alias] = (uchar)(SYSCALL_COUNTER_SLOT_##target + 1),
	SYSCALL_ALIASES(SYSCALL_ALIAS_MAP)
#undef SYSCALL_ALIAS_MAP
#undef SYSCALL_COUNTER_MAP
};

/* 类别按紧凑计数槽存放，避免为稀疏 ABI 再复制一张 600 字节表。 */
static const uchar syscall_class_by_slot[SYSCALL_COUNTER_SLOTS] = {
#define SYSCALL_CLASS_MAP(name, class, enabled) \
	[SYSCALL_COUNTER_SLOT_##name] = SYSCALL_CLASS_##class,
	SYSCALL_REGISTERED(SYSCALL_CLASS_MAP)
#undef SYSCALL_CLASS_MAP
};

#define SYSCALL_ID_ASSERT(name, class, enabled) \
	_Static_assert(SYS_##name < SYSCALL_COUNT_MAX, \
		       "registered syscall id exceeds lookup table");
SYSCALL_REGISTERED(SYSCALL_ID_ASSERT)
#undef SYSCALL_ID_ASSERT
#define SYSCALL_ALIAS_ID_ASSERT(alias, target) _Static_assert(SYS_##alias < SYSCALL_COUNT_MAX, "syscall alias id exceeds lookup table");
SYSCALL_ALIASES(SYSCALL_ALIAS_ID_ASSERT)
#undef SYSCALL_ALIAS_ID_ASSERT

_Static_assert(SYSCALL_COUNTER_SLOTS < 255,
	       "syscall counter slot must fit in one byte");

static int syscall_counter_slot(int syscall_id)
{
	uchar encoded;

	if (syscall_id < 0 || syscall_id >= SYSCALL_COUNT_MAX)
		return -1;
	encoded = syscall_counter_slot_by_id[syscall_id];
	return encoded == 0 ? -1 : encoded - 1;
}

uint syscall_count_read(const struct proc *p, int syscall_id)
{
	int slot = syscall_counter_slot(syscall_id);

	return p == 0 || slot < 0 ? 0 : p->syscall_count[slot];
}

static void syscall_count_record(struct proc *p, int syscall_id)
{
	int slot = syscall_counter_slot(syscall_id);

	/* trace() 的返回类型为 int，避免计数进入错误码的负数区间。 */
	if (p != 0 && slot >= 0 && p->syscall_count[slot] < 0x7fffffffU)
		p->syscall_count[slot]++;
}

/* 保持与 user/include/stddef.h 一致的 Linux 兼容 Stat 布局。 */
struct user_stat {
	uint64 dev;
	uint64 ino;
	uint mode;
	uint nlink;
	uint64 pad[7];
};

_Static_assert(sizeof(struct user_stat) == 80, "user Stat ABI");

uint64 console_write(uint64 va, uint64 len)
{
	struct proc *p = curr_proc();
	char buf[MAX_STR_LEN];
	uint64 written = 0;

	while (written < len) {
		uint64 n = MIN(len - written, sizeof(buf));
		if (copyin(p->pagetable, buf, va + written, n) < 0)
			return written ? written : (uint64)-1;
		for (uint64 i = 0; i < n; ++i) {
			console_putchar(buf[i]);
			written++;
			if ((written % KERNEL_WORK_STREAM_GRANULE) == 0 &&
			    kernel_work_checkpoint_bytes(
				    KERNEL_WORK_STREAM_GRANULE) < 0)
				return written;
		}
	}
	return written;
}

uint64 console_read(uint64 va, uint64 len)
{
	struct proc *p = curr_proc();
	char buf[MAX_STR_LEN];
	uint64 n = MIN(len, sizeof(buf));
	uint64 got = 0;

	while (got < n) {
		int c = got == 0 ? console_getc_wait() : consgetc();
		if (c < 0) {
			if (got != 0)
				break;
			return (uint64)-1;
		}
		buf[got++] = c;
	}
	if (copyout(p->pagetable, va, buf, got) < 0)
		return (uint64)-1;
	return got;
}

static uint64 sys_write(struct file *f, int fd, uint64 va, uint64 len)
{
	uint64 result;
	struct proc *p;

	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	p = curr_proc();
	if (f == NULL) {
		errorf("invalid fd %d\n", fd);
		return -1;
	}
	if (!f->writable || len > MAX_RW_COUNT ||
	    user_range_check(p->pagetable, va, len, PTE_R) < 0)
		return -1;
	switch (f->type) {
	case FD_STDIO:
		result = console_write(va, len);
		break;
	case FD_PIPE:
		result = pipewrite(f->pipe, va, len);
		break;
	case FD_INODE:
		result = inodewrite(f, va, len);
		break;
	default:
		panic("unknown file type %d\n", f->type);
	}
	return result;
}

static uint64 sys_read(struct file *f, int fd, uint64 va, uint64 len)
{
	uint64 result;
	struct proc *p;

	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	p = curr_proc();
	if (f == NULL) {
		errorf("invalid fd %d\n", fd);
		return -1;
	}
	if (!f->readable || len > MAX_RW_COUNT ||
	    user_range_check(p->pagetable, va, len, PTE_W) < 0)
		return -1;
	switch (f->type) {
	case FD_STDIO:
		result = console_read(va, len);
		break;
	case FD_PIPE:
		result = piperead(f->pipe, va, len);
		break;
	case FD_INODE:
		result = inoderead(f, va, len);
		break;
	default:
		panic("unknown file type %d\n", f->type);
	}
	return result;
}

uint64 sys_fstat(int fd, uint64 stataddr)
{
	struct user_stat status;
	struct file *f;
	struct proc *p = curr_proc();
	struct vfs_cred cred;
	int result = -1;

	if (fd < 0 || fd >= FD_BUFFER_SIZE || stataddr == 0 ||
	    user_range_check(p->pagetable, stataddr, sizeof(status), PTE_W) < 0)
		return -1;
	f = fdget(fd);
	if (f == 0)
		return -1;
	memset(&status, 0, sizeof(status));
	vfs_cred_from_proc(p, &cred);
	/* 打开文件持有已装载 inode；fstat 只读缓存，不在查询中触盘。 */
	if (f->type == FD_INODE && f->ip != 0 && f->ip->valid &&
	    vfs_inode_authorize(f->ip, &cred, VFS_OP_READ)) {
		status.dev = f->ip->dev;
		status.ino = f->ip->inum;
		status.mode = f->ip->type == T_DIR ? 0x040000U : 0x100000U;
		/* 文件系统没有 linkat，具名 inode 只有一个链接。 */
		status.nlink = f->ip->removed ? 0U : 1U;
		if (!p->is_agent ||
		    agent_provenance_merge_current(
			    p, AGENT_PROVENANCE_UNTRUSTED_FILE_DATA) ==
			    AGENT_STATUS_OK)
			result = copyout(p->pagetable, stataddr, (char *)&status,
					 sizeof(status));
	}
	fileclose(f);
	return result;
}

__attribute__((noreturn)) void sys_exit(int code)
{
	exit(code);
	__builtin_unreachable();
}

uint64 sys_sched_yield()
{
	yield();
	return 0;
}

static int sys_sbrk(uint64 raw_delta)
{
	return proc_sbrk((long)raw_delta);
}

uint64 sys_kernel_work_last_preemptions(void)
{
	return kernel_work_last_preemptions(curr_thread());
}

int sys_io_policy_info(uint64 addr, uint64 user_size)
{
	struct proc *p = curr_proc();
	struct io_policy_info info;
	uint64 copy_size;

	if (user_size < 2 * sizeof(unsigned int))
		return -1;
	copy_size = MIN(user_size, sizeof(info));
	if (user_range_check(p->pagetable, addr, copy_size, PTE_W) < 0 ||
	    bio_policy_snapshot(p, &info) < 0)
		return -1;
	return copyout(p->pagetable, addr, (char *)&info, copy_size);
}

uint64 sys_gettimeofday(uint64 val, int _tz)
{
	struct proc *p = curr_proc();
	uint64 cycle = get_cycle();
	TimeVal t;
	t.sec = cycle / CPU_FREQ;
	t.usec = (cycle % CPU_FREQ) * 1000000 / CPU_FREQ;
	return copyout(p->pagetable, val, (char *)&t, sizeof(TimeVal));
}

uint64 sys_getpid()
{
	return curr_proc()->pid;
}

uint64 sys_getppid()
{
	struct proc *p = curr_proc();
	return p->parent == NULL ? IDLE_PID : p->parent->pid;
}

int sys_trace(int req, uint64 id, uint8 data)
{
	struct proc *p = curr_proc();
	uint8 value;

	switch (req) {
	case TRACE_READ:
		if (copyin(p->pagetable, (char *)&value, id, sizeof(value)) < 0)
			return -1;
		return value;
	case TRACE_WRITE:
		value = data;
		if (copyout(p->pagetable, id, (char *)&value, sizeof(value)) < 0)
			return -1;
		return 0;
	case TRACE_SYSCALL:
		if (id >= SYSCALL_COUNT_MAX)
			return -1;
		return syscall_count_read(p, (int)id);
	default:
		return -1;
	}
}

int sys_mailwrite(int pid, uint64 buf, int len)
{
	(void)pid;
	(void)buf;
	(void)len;
	return -1;
}

int sys_mailread(uint64 buf, int len)
{
	(void)buf;
	(void)len;
	return -1;
}

uint64 sys_clone()
{
	debugf("fork!");
	return fork();
}

static int copy_exec_args(pagetable_t pagetable, uint64 uargv, char **argv,
			  char *storage)
{
	uint64 storage_used = 0;
	uint64 checked_layout_bytes;
	struct user_stack_argv_layout layout;

	user_stack_argv_layout_init(&layout);
	if (uargv == 0) {
		if (user_stack_argv_layout_finish(&layout,
						  &checked_layout_bytes) < 0)
			return -1;
		argv[0] = 0;
		return 0;
	}
	for (uint64 i = 0; i <= MAX_ARG_NUM; i++) {
		uint64 slot;
		uint64 user_arg;

		if (checked_user_offset(uargv, i, sizeof(uint64), &slot) < 0 ||
		    fetch_user_u64(pagetable, slot, &user_arg) < 0)
			return -1;
		if (user_arg == 0) {
			if (user_stack_argv_layout_finish(
				    &layout, &checked_layout_bytes) < 0)
				return -1;
			argv[i] = 0;
			return i;
		}
		if (i == MAX_ARG_NUM ||
		    storage_used >= USER_STACK_ARGV_LAYOUT_BYTES)
			return -1;

		int len = copyinstr(pagetable, storage + storage_used, user_arg,
				    USER_STACK_ARGV_LAYOUT_BYTES - storage_used);
		if (len < 0 ||
		    user_stack_argv_layout_add_string(
			    &layout, (uint64)len + 1) < 0 ||
		    user_stack_argv_layout_finish(
			    &layout, &checked_layout_bytes) < 0)
			return -1;
		argv[i] = storage + storage_used;
		storage_used += len + 1;
	}
	return -1;
}

uint64 sys_exec(uint64 path, uint64 uargv)
{
	struct proc *p = curr_proc();
	char name[MAX_STR_LEN];
	char *argv[MAX_ARG_NUM + 1];
	char *storage;
	int rc;
	enum resource_charge_class charge_class =
		p->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
					    RESOURCE_CHARGE_ORDINARY;

	if (copyinstr(p->pagetable, name, path, sizeof(name)) < 0)
		return -1;
	storage = kalloc_account_page(p->resource_account, charge_class);
	if (storage == 0)
		return -1;
	if (copy_exec_args(p->pagetable, uargv, argv, storage) < 0) {
		(void)kfree_account_page(storage, p->resource_account,
					 charge_class);
		return -1;
	}
	rc = exec(name, argv);
	(void)kfree_account_page(storage, p->resource_account,
				 charge_class);
	return rc;
}

uint64 sys_wait(int pid, uint64 va)
{
	struct proc *p = curr_proc();
	struct workflow_lifecycle_key lifecycle;
	int code;
	int child;
	int lifecycle_entered = 0;
	int result;

	if (va &&
	    user_range_check(p->pagetable, va, sizeof(code), PTE_W) < 0)
		return -1;
	child = wait(pid, &code);
	if (child < 0)
		return child;
	lifecycle = vfs_proc_lifecycle(p);
	/*
	 * Waiting itself may sleep while the workflow controller seals a cut.
	 * Child teardown is covered by departure_operations; reacquire the current
	 * generation only for provenance and result delivery instead of holding it
	 * across the sleep.
	 */
	if (workflow_lifecycle_key_valid(lifecycle)) {
		while (workflow_lifecycle_operation_enter(lifecycle) < 0) {
			if (workflow_lifecycle_closing(lifecycle))
				return -1;
			yield();
		}
		lifecycle_entered = 1;
	}
	if (p->is_agent &&
	    agent_provenance_merge_current(
		    p, AGENT_PROVENANCE_CROSS_AGENT_DATA |
			       AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT) !=
		    AGENT_STATUS_OK)
		result = -1;
	else if (va == 0)
		result = child;
	else
		result = copyout(p->pagetable, va, (char *)&code, sizeof(code)) < 0 ?
				 -1 : child;
	if (lifecycle_entered)
		workflow_lifecycle_operation_leave(lifecycle);
	return result;
}

uint64 sys_pipe(uint64 fdarray)
{
	struct proc *p = curr_proc();
	int fds[2] = { -1, -1 };
	struct file *f0 = 0, *f1 = 0;

	if (user_range_check(p->pagetable, fdarray, sizeof(fds), PTE_W) < 0)
		return -1;
	fds[0] = fdreserve();
	fds[1] = fdreserve();
	if (fds[0] < 0 || fds[1] < 0)
		goto err0;
	{
		struct file *pipe_files[2];

		if (filealloc_many(p, pipe_files, 2) < 0)
			goto err0;
		f0 = pipe_files[0];
		f1 = pipe_files[1];
	}
	if (f0 == 0 || f1 == 0)
		goto err0;
	if (pipealloc(f0, f1) < 0)
		goto err0;
	if (copyout(p->pagetable, fdarray, (char *)fds, sizeof(fds)) < 0)
		goto err0;
	// 所有描述符哨兵都在编号公开前安装；除非内核损坏，此提交不会失败。
	if (fdinstall(fds[0], f0) < 0 || fdinstall(fds[1], f1) < 0)
		panic("pipe descriptor reservation");
	return 0;

err0:
	fdrelease(fds[0]);
	fdrelease(fds[1]);
	if (f0)
		fileclose(f0);
	if (f1)
		fileclose(f1);
	return -1;
}

uint64 sys_openat(uint64 va, uint64 omode, uint64 _flags)
{
	struct proc *p = curr_proc();
	char path[MAXPATH];

	if (copyinstr(p->pagetable, path, va, sizeof(path)) < 0)
		return -1;
	return fileopen(path, omode);
}

uint64 sys_unlinkat(int dirfd, uint64 va, uint64 flags)
{
	struct proc *p = curr_proc();
	char path[200];

	if (dirfd != -100 || flags != 0)
		return -1;
	if (copyinstr(p->pagetable, path, va, sizeof(path)) < 0)
		return -1;
	return fileunlink(path);
}

static int sys_sync(void)
{
	int result = agent_metadata_quiescence_fence_current();

	if (result < 0)
		return result;
	result = fs_deferred_reclaim_drain_current();
	if (result < 0)
		return result;
	return fs_epoch_commit();
}

static int sys_fsync(int fd)
{
	struct file *f = fdget(fd);
	int result;
	int valid;

	if (f == 0)
		return -1;
	valid = f->type == FD_INODE;
	fileclose(f);
	if (!valid)
		return -1;
	result = agent_metadata_durability_fence_current();
	if (result < 0)
		return result;
	return fs_epoch_commit();
}

int sys_thread_create(uint64 entry, uint64 arg)
{
	struct proc *p = curr_proc();
	if (!proc_teardown_live(p))
		return -1;
	if (user_range_check(p->pagetable, entry, 1, PTE_X) < 0)
		return -1;
	int tid = allocthread(p, entry, 1);
	if (tid < 0) {
		errorf("fail to create thread");
		return -1;
	}
	struct thread *t = &p->threads[tid];
	t->trapframe->a0 = arg;
	t->state = RUNNABLE;
	add_task(t);
	return tid;
}

int sys_gettid()
{
	return curr_thread()->tid;
}

int sys_waittid(int tid)
{
	struct thread *t;
	int enabled;
	int exit_code;
	uint64 target_generation;

	if (tid < 0 || tid >= NTHREAD) {
		errorf("unexpected tid %d", tid);
		return -1;
	}
	t = &curr_proc()->threads[tid];
	enabled = intr_save();
	target_generation = t->identity_generation;
	if (t->state == T_UNUSED || target_generation == 0 ||
	    tid == curr_thread()->tid) {
		intr_restore(enabled);
		return -1;
	}
	while (t->state != EXITED) {
		if (t->state == T_UNUSED ||
		    t->identity_generation != target_generation ||
		    wait_queue_sleep_key_irq(&curr_proc()->thread_exit_waiters,
					     target_generation) != WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
	}
	if (t->identity_generation != target_generation) {
		intr_restore(enabled);
		return -1;
	}
	if (t->kstack_state != KSTACK_NONE ||
	    t->resource_slot_charged || t->on_run_queue)
		panic("waittid unreaped thread");
	exit_code = (int)t->exit_code;
	agent_thread_runtime_transition(t, AGENT_THREAD_RUNTIME_RELEASE);
	agent_observe_thread_reset(t);
	t->tid = -1;
	t->identity_generation = 0;
	t->ustack = 0;
	t->trapframe = 0;
	t->exit_code = 0;
	t->state = T_UNUSED;
	intr_restore(enabled);
	return exit_code;
}

/* LAB5 参考实现可在此为 mutex 和 semaphore 共用死锁检测。 */

int sys_mutex_create(int blocking)
{
	struct mutex *m = mutex_create(blocking);
	if (m == NULL) {
		errorf("fail to create mutex: out of resource");
		return -1;
	}
	// 保留教学 mutex ABI，AgentOS 不在此扩展死锁检测。
	int mutex_id = m - curr_proc()->mutex_pool;
	debugf("create mutex %d", mutex_id);
	return mutex_id;
}

int sys_mutex_lock(int mutex_id)
{
	if (mutex_id < 0 || mutex_id >= curr_proc()->next_mutex_id) {
		errorf("Unexpected mutex id %d", mutex_id);
		return -1;
	}
	return mutex_lock(&curr_proc()->mutex_pool[mutex_id]);
}

int sys_mutex_unlock(int mutex_id)
{
	if (mutex_id < 0 || mutex_id >= curr_proc()->next_mutex_id) {
		errorf("Unexpected mutex id %d", mutex_id);
		return -1;
	}
	return mutex_unlock(&curr_proc()->mutex_pool[mutex_id]);
}

int sys_semaphore_create(int res_count)
{
	if (res_count < 0)
		return -1;
	struct semaphore *s = semaphore_create(res_count);
	if (s == NULL) {
		errorf("fail to create semaphore: out of resource");
		return -1;
	}
	// 保留教学 semaphore ABI，AgentOS 不在此扩展死锁检测。
	int sem_id = s - curr_proc()->semaphore_pool;
	debugf("create semaphore %d", sem_id);
	return sem_id;
}

int sys_semaphore_up(int semaphore_id)
{
	if (semaphore_id < 0 ||
	    semaphore_id >= curr_proc()->next_semaphore_id) {
		errorf("Unexpected semaphore id %d", semaphore_id);
		return -1;
	}
	// semaphore up 保持原有 uCore 行为。
	return semaphore_up(&curr_proc()->semaphore_pool[semaphore_id]);
}

int sys_semaphore_down(int semaphore_id)
{
	if (semaphore_id < 0 ||
	    semaphore_id >= curr_proc()->next_semaphore_id) {
		errorf("Unexpected semaphore id %d", semaphore_id);
		return -1;
	}
	return semaphore_down(&curr_proc()->semaphore_pool[semaphore_id]);
}

int sys_condvar_create()
{
	struct condvar *c = condvar_create();
	if (c == NULL) {
		errorf("fail to create condvar: out of resource");
		return -1;
	}
	int cond_id = c - curr_proc()->condvar_pool;
	debugf("create condvar %d", cond_id);
	return cond_id;
}

int sys_condvar_signal(int cond_id)
{
	if (cond_id < 0 || cond_id >= curr_proc()->next_condvar_id) {
		errorf("Unexpected condvar id %d", cond_id);
		return -1;
	}
	cond_signal(&curr_proc()->condvar_pool[cond_id]);
	return 0;
}

int sys_condvar_wait(int cond_id, int mutex_id)
{
	if (cond_id < 0 || cond_id >= curr_proc()->next_condvar_id) {
		errorf("Unexpected condvar id %d", cond_id);
		return -1;
	}
	if (mutex_id < 0 || mutex_id >= curr_proc()->next_mutex_id) {
		errorf("Unexpected mutex id %d", mutex_id);
		return -1;
	}
	return cond_wait(&curr_proc()->condvar_pool[cond_id],
			 &curr_proc()->mutex_pool[mutex_id]);
}

// LAB5：可在此定义 enable_deadlock_detect。

extern char trap_page[];

/* 准入与执行共用同一个已固定的文件表身份。 */
struct syscall_transaction_context {
	int id;
	struct file *file;
	struct file_close_receipt close_receipt;
	int close_attempted;
	int close_result;
	int close_final;
	int io_admitted;
	int io_cleanup_admitted;
	int fs_epoch_admitted;
	uint policy;
};

_Static_assert(sizeof(struct syscall_transaction_context) <=
	       sizeof(((struct thread_trap_cold *)0)->syscall_transaction),
	       "syscall transaction must fit in thread trap cold state");

static struct file *syscall_fd_pin(uint64 fd)
{
	if (fd >= FD_BUFFER_SIZE)
		return 0;
	return fdget((int)fd);
}

static int syscall_file_uses_disk(const struct file *file)
{
	return file != 0 && file->type == FD_INODE;
}


#define SYSCALL_POLICY_BLOCK_IO (1U << 0)
#define SYSCALL_POLICY_FS_EPOCH (1U << 1)

/* 调度、I/O 和持久化共用注册表中的同一次闭集分类。 */
static enum syscall_class syscall_classify(int id)
{
	int slot = syscall_counter_slot(id);

	if (slot < 0)
		return SYSCALL_CLASS_INVALID;
	return (enum syscall_class)syscall_class_by_slot[slot];
}

static uint syscall_policy_base(enum syscall_class class)
{
	switch (class) {
	case SYSCALL_CLASS_BLOCK_IO:
		return SYSCALL_POLICY_BLOCK_IO;
	case SYSCALL_CLASS_FS_EPOCH:
		return SYSCALL_POLICY_FS_EPOCH;
	case SYSCALL_CLASS_BLOCK_IO_FS_EPOCH:
		return SYSCALL_POLICY_BLOCK_IO | SYSCALL_POLICY_FS_EPOCH;
	default:
		return 0;
	}
}

static void syscall_transaction_prepare(
	struct syscall_transaction_context *transaction,
	const struct trapframe *trapframe, int id, uint policy)
{
	transaction->id = id;
	transaction->file = 0;
	transaction->policy = policy;
	transaction->close_receipt.state = FILE_CLOSE_RECEIPT_EMPTY;
	transaction->close_attempted = 0;
	transaction->close_result = 0;
	transaction->close_final = 0;
	transaction->io_admitted = 0;
	transaction->io_cleanup_admitted = 0;
	transaction->fs_epoch_admitted = 0;
	switch (transaction->id) {
	case SYS_read:
	case SYS_write:
		transaction->file = syscall_fd_pin(trapframe->a0);
		if (syscall_file_uses_disk(transaction->file)) {
			transaction->policy |= SYSCALL_POLICY_BLOCK_IO;
			if (transaction->id == SYS_write)
				transaction->policy |= SYSCALL_POLICY_FS_EPOCH;
		}
		break;
	case SYS_openat:
		if ((trapframe->a1 & (O_CREATE | O_TRUNC)) != 0)
			transaction->policy |= SYSCALL_POLICY_FS_EPOCH;
		break;
	default:
		break;
	}
}

static int syscall_transaction_begin(
	struct syscall_transaction_context *transaction,
	const struct trapframe *trapframe)
{
	if (transaction->id == SYS_close) {
		int close_status;

		transaction->close_attempted = 1;
		if (trapframe->a0 >= FD_BUFFER_SIZE)
			close_status = -1;
		else
			close_status = fdclose_prepare(
				(int)trapframe->a0,
				&transaction->close_receipt);
		transaction->close_result = close_status < 0 ? -1 : 0;
		if (close_status <= 0)
			return 0;
		transaction->close_final = 1;
		if (transaction->close_receipt.type != FD_INODE)
			return 0;
		return 0;
	}
	/* 先串行化修改操作，再预留稀缺的设备速率额度。 */
	if ((transaction->policy & SYSCALL_POLICY_FS_EPOCH) != 0) {
		if (fs_epoch_request_begin() < 0)
			return -1;
		transaction->fs_epoch_admitted = 1;
	}
	if ((transaction->policy & SYSCALL_POLICY_BLOCK_IO) != 0) {
		int io_begin_result = transaction->fs_epoch_admitted ?
			bio_request_begin_current() :
			bio_request_begin_current_lazy();

		if (io_begin_result < 0) {
			/* 准入失败可能与描述符关闭竞争；释放可能为最后一个的引用前，
			 * 先取得拆除预留。 */
			if (transaction->file != 0 &&
			    (transaction->policy & SYSCALL_POLICY_BLOCK_IO) != 0 &&
			    bio_request_begin_current_cleanup() == 0) {
				transaction->io_admitted = 1;
				transaction->io_cleanup_admitted = 1;
			}
			return -1;
		}
		transaction->io_admitted = 1;
	}
	return 0;
}

static void syscall_transaction_end_io(
	struct syscall_transaction_context *transaction)
{
	if (!transaction->io_admitted)
		return;
	if (transaction->io_cleanup_admitted)
		(void)bio_request_end_current_cleanup();
	else
		(void)bio_request_end_current(1);
	transaction->io_admitted = 0;
	transaction->io_cleanup_admitted = 0;
}

static void syscall_transaction_commit(
	struct syscall_transaction_context *transaction, int *result)
{
	if (transaction->io_admitted && fs_epoch_should_commit()) {
		int commit_result = fs_epoch_commit();

		if (commit_result < 0 && *result >= 0)
			*result = commit_result;
	}
}

static void syscall_transaction_finish(
	struct syscall_transaction_context *transaction, int *result)
{
	struct file_close_receipt *receipt = &transaction->close_receipt;
	int final = 0;

	if (transaction->close_final) {
		if (transaction->close_receipt.state !=
		    FILE_CLOSE_RECEIPT_PREPARED)
			panic("syscall close receipt");
		final = 1;
		transaction->close_final = 0;
	}
	/* 并发关闭或重开不能改变本次引用释放的目标。 */
	if (transaction->file != 0) {
		if (final)
			panic("syscall duplicate file receipt");
		do {
			final = fileclose_prepare(transaction->file, receipt);
			if (final < 0)
				(void)kernel_work_checkpoint_cleanup(
					KERNEL_WORK_OPERATION_UNITS);
		} while (final < 0);
		transaction->file = 0;
	}
	if (final && receipt->type != FD_INODE) {
		fileclose_finish(receipt);
		final = 0;
	}
	if (final) {
		int dropped = fileclose_finish_drop_only(receipt);

		if (dropped < 0)
			panic("syscall file drop-only receipt");
		if (dropped > 0)
			final = 0;
	}
	if (final && !transaction->fs_epoch_admitted) {
		/* 持有 I/O 租约时不得获取文件系统事务门。 */
		syscall_transaction_end_io(transaction);
		if (fs_epoch_request_begin() < 0) {
			fileclose_finish(receipt);
			if (*result >= 0)
				*result = -1;
			return;
		}
		transaction->fs_epoch_admitted = 1;
	}
	if (final) {
		while (fileclose_finish_epoch(receipt) < 0 &&
		       receipt->state == FILE_CLOSE_RECEIPT_PREPARED)
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
	}
	if (transaction->fs_epoch_admitted) {
		syscall_transaction_commit(transaction, result);
		fs_epoch_request_end();
		transaction->fs_epoch_admitted = 0;
	}
	syscall_transaction_end_io(transaction);
	if (final && receipt->state == FILE_CLOSE_RECEIPT_SETTLEMENT) {
		while (fileclose_finish_settle(receipt) !=
		       BIO_CLEANUP_RELEASED)
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
	}
	if (final && fileclose_finish_result(receipt) < 0 && *result >= 0)
		*result = -1;
}

static uint syscall_kernel_work_class(int id)
{
	switch (id) {
	case SYS_kernel_work_last_preemptions:
	case SYS_agent_resource_snapshot:
	case SYS_agent_performance_snapshot:
		return KERNEL_WORK_SYSCALL_OBSERVER;
	default:
		return KERNEL_WORK_SYSCALL_PUBLISH;
	}
}

static uint64 syscall_direct_agent_side_effects(
	int, const struct trapframe *, const struct syscall_transaction_context *);
static int syscall_merge_ingress_provenance(
	int, const struct syscall_transaction_context *);

/*
 * 快慢路径共用调用分派，但不在此分配事务对象。noinline 让传统调用的
 * 栈帧不会被慢路径的关闭回执和 I/O 状态反向放大。
 */
static __attribute__((noinline)) int syscall_dispatch(
	int id, struct trapframe *trapframe,
	struct syscall_transaction_context *transaction)
{
	int ret;

	syscall_count_record(curr_proc(), id);
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d args = [%lx, %lx, %lx, %lx, %lx, %lx]", id,
		       trapframe->a0, trapframe->a1, trapframe->a2,
		       trapframe->a3, trapframe->a4, trapframe->a5);
	}
	if (id == SYS_agent_launch_info)
		return sys_agent_info(trapframe->a0, 1);
	switch (id) {
	case SYS_write:
		ret = sys_write(transaction->file, (int)trapframe->a0,
				trapframe->a1, trapframe->a2);
		break;
	case SYS_read:
		ret = sys_read(transaction->file, (int)trapframe->a0,
			       trapframe->a1, trapframe->a2);
		break;
	case SYS_fstat:
		ret = sys_fstat(trapframe->a0, trapframe->a1);
		break;
	case SYS_openat:
		ret = sys_openat(trapframe->a0, trapframe->a1, trapframe->a2);
		break;
	case SYS_unlinkat:
		ret = sys_unlinkat(trapframe->a0, trapframe->a1,
				trapframe->a2);
		break;
	case SYS_close:
		ret = transaction != 0 && transaction->close_attempted ?
			transaction->close_result : -1;
		break;
	case SYS_sync:
		ret = sys_sync();
		break;
	case SYS_fsync:
	case SYS_fdatasync:
		ret = sys_fsync(trapframe->a0);
		break;
	case SYS_exit:
		sys_exit(trapframe->a0);
		// __builtin_unreachable();
	// case SYS_nanosleep:
	// 	ret = sys_nanosleep(trapframe->a0);
	// 	break;
	case SYS_sched_yield:
		ret = sys_sched_yield();
		break;
	case SYS_brk:
		ret = sys_sbrk(trapframe->a0);
		break;
	case SYS_gettimeofday:
		ret = sys_gettimeofday(trapframe->a0, trapframe->a1);
		break;
	case SYS_getpid:
		ret = sys_getpid();
		break;
	case SYS_getppid:
		ret = sys_getppid();
		break;
	case SYS_mailread:
		ret = sys_mailread(trapframe->a0, trapframe->a1);
		break;
	case SYS_mailwrite:
		ret = sys_mailwrite(trapframe->a0, trapframe->a1,
				trapframe->a2);
		break;
	case SYS_trace:
		ret = sys_trace(trapframe->a0, trapframe->a1, trapframe->a2);
		break;
	case SYS_clone: // SYS_fork
		ret = sys_clone();
		break;
	case SYS_execve:
		ret = sys_exec(trapframe->a0, trapframe->a1);
		break;
	case SYS_wait4:
		ret = sys_wait(trapframe->a0, trapframe->a1);
		break;
	case SYS_pipe2:
		ret = sys_pipe(trapframe->a0);
		break;
	case SYS_thread_create:
		ret = sys_thread_create(trapframe->a0, trapframe->a1);
		break;
	case SYS_gettid:
		ret = sys_gettid();
		break;
	case SYS_waittid:
		ret = sys_waittid(trapframe->a0);
		break;
	case SYS_mutex_create:
		ret = sys_mutex_create(trapframe->a0);
		break;
	case SYS_mutex_lock:
		ret = sys_mutex_lock(trapframe->a0);
		break;
	case SYS_mutex_unlock:
		ret = sys_mutex_unlock(trapframe->a0);
		break;
	case SYS_semaphore_create:
		ret = sys_semaphore_create(trapframe->a0);
		break;
	case SYS_semaphore_up:
		ret = sys_semaphore_up(trapframe->a0);
		break;
	case SYS_semaphore_down:
		ret = sys_semaphore_down(trapframe->a0);
		break;
	case SYS_condvar_create:
		ret = sys_condvar_create();
		break;
	case SYS_condvar_signal:
		ret = sys_condvar_signal(trapframe->a0);
		break;
	case SYS_condvar_wait:
		ret = sys_condvar_wait(trapframe->a0, trapframe->a1);
		break;
	case SYS_kernel_work_last_preemptions:
		ret = sys_kernel_work_last_preemptions();
		break;
	case SYS_io_policy_info:
		ret = sys_io_policy_info(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_create:
		ret = sys_agent_create();
		break;
	case SYS_agent_create_role:
		ret = sys_agent_create_role(trapframe->a0);
		break;
	case SYS_agent_workflow_create:
		ret = sys_agent_workflow_create(trapframe->a0);
		break;
	case SYS_agent_scope_delegate_fd:
		ret = sys_agent_scope_delegate_fd(trapframe->a0);
		break;
	case SYS_agent_workflow_close:
		ret = sys_agent_workflow_close(trapframe->a0);
		break;
	case SYS_agent_workflow_lifecycle_info:
		ret = sys_agent_workflow_lifecycle_info(
			trapframe->a0, trapframe->a1, trapframe->a2,
			trapframe->a3, trapframe->a4);
		break;
	case SYS_agent_execution_contract:
		ret = sys_agent_execution_contract(trapframe->a0,
					       trapframe->a1);
		break;
	case SYS_agent_task_channel_setup:
		ret = sys_agent_task_channel_setup(trapframe->a0,
					   trapframe->a1);
		break;
	case SYS_agent_task_channel_enter:
		ret = sys_agent_task_channel_enter(trapframe->a0,
					   trapframe->a1);
		break;
	case SYS_agent_task_channel_resource:
		ret = sys_agent_task_channel_resource(trapframe->a0,
					      trapframe->a1);
		break;
	case SYS_agent_resource_snapshot:
		ret = sys_agent_resource_snapshot(trapframe->a0,
						  trapframe->a1);
		break;
	case SYS_agent_performance_snapshot:
		ret = sys_agent_performance_snapshot(trapframe->a0,
						     trapframe->a1);
		break;
	case SYS_agent_info:
		ret = sys_agent_info(trapframe->a0, 0);
		break;
	case SYS_agent_sched_snapshot:
		ret = sys_agent_sched_snapshot(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_sched_config:
		ret = sys_agent_sched_config(trapframe->a0);
		break;
	case SYS_agent_trace_snapshot:
		ret = sys_agent_trace_snapshot(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_audit_snapshot:
		ret = sys_agent_audit_snapshot(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_audit_query:
		ret = sys_agent_audit_query(trapframe->a0, trapframe->a1,
					    trapframe->a2);
		break;
	case SYS_agent_audit_receipt:
		ret = sys_agent_audit_receipt(trapframe->a0);
		break;
	case SYS_agent_span_trace_snapshot:
		ret = sys_agent_span_trace_snapshot(trapframe->a0,
						    trapframe->a1);
		break;
	case SYS_agent_timeline_snapshot:
		ret = sys_agent_timeline_snapshot(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_timeline_query:
		ret = sys_agent_timeline_query(trapframe->a0, trapframe->a1,
					       trapframe->a2);
		break;
	case SYS_agent_timeline_wait:
		ret = sys_agent_timeline_wait(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_timeline_read:
		ret = sys_agent_timeline_read(trapframe->a0, trapframe->a1,
					      trapframe->a2, trapframe->a3);
		break;
	case SYS_agent_provenance_snapshot:
		ret = sys_agent_provenance_snapshot(trapframe->a0,
						     trapframe->a1);
		break;
	case SYS_agent_ledger_snapshot:
		ret = sys_agent_ledger_snapshot(trapframe->a0);
		break;
	case SYS_agent_run:
		ret = sys_agent_run(trapframe->a0, trapframe->a1,
				    trapframe->a2, trapframe->a3);
		break;
	case SYS_agent_call:
		ret = sys_agent_call(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_tool_list:
		ret = sys_agent_tool_list(trapframe->a0, trapframe->a1);
		break;
	case SYS_tool_call:
		ret = sys_tool_call(trapframe->a0, trapframe->a1);
		break;
	case SYS_tool_list:
		ret = sys_tool_list(trapframe->a0, trapframe->a1,
				    trapframe->a2, trapframe->a3);
		break;
#ifdef VIRTIO_DISK_TEST_PROFILE
	case SYS_virtio_disk_test:
		ret = sys_virtio_disk_test(
			trapframe->a0, trapframe->a1, trapframe->a2,
			trapframe->a3, trapframe->a4, trapframe->a5);
		break;
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
	case SYS_physical_page_test:
		ret = sys_physical_page_test(trapframe->a0, trapframe->a1,
					     trapframe->a2);
		break;
#endif
#ifdef WAIT_ATOMIC_TEST_PROFILE
	case SYS_wait_atomic_test:
		ret = sys_wait_atomic_test(
			trapframe->a0, trapframe->a1, trapframe->a2,
			trapframe->a3, trapframe->a4, trapframe->a5);
		break;
#endif
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
	case SYS_fs_allocator_fault_test:
		ret = sys_fs_allocator_fault_test(
			trapframe->a0, trapframe->a1, trapframe->a2,
			trapframe->a3, trapframe->a4, trapframe->a5);
		break;
#endif
	case SYS_context_push:
		ret = sys_context_push(trapframe->a0);
		break;
	case SYS_context_query:
		ret = sys_context_query(trapframe->a0, trapframe->a1,
					trapframe->a2);
		break;
	case SYS_context_snapshot:
		ret = sys_context_snapshot(trapframe->a0, trapframe->a1,
					   trapframe->a2);
		break;
	case SYS_context_detail:
		ret = sys_context_detail(trapframe->a0, trapframe->a1);
		break;
	case SYS_context_rollback:
		ret = sys_context_rollback(trapframe->a0);
		break;
	case SYS_context_clear:
		ret = sys_context_clear();
		break;
	case SYS_agent_watch:
		ret = sys_agent_watch(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_unwatch:
		ret = sys_agent_unwatch(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_wait:
		ret = sys_agent_wait(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_wait_cancel:
		ret = sys_agent_wait_cancel(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_heartbeat:
		ret = sys_agent_heartbeat(trapframe->a0);
		break;
	case SYS_agent_heartbeat_set:
		ret = sys_agent_heartbeat_set(trapframe->a0);
		break;
	case SYS_agent_heartbeat_stop:
		ret = sys_agent_heartbeat_stop();
		break;
	case SYS_agent_wake:
		ret = sys_agent_wake(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_file_meta_init:
		ret = sys_agent_file_meta_init();
		break;
	case SYS_agent_file_meta_set:
		ret = sys_agent_file_meta_set(trapframe->a0);
		break;
	case SYS_agent_file_query:
		ret = sys_agent_file_query(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_file_edit_begin:
		ret = sys_agent_file_edit_begin(
			trapframe->a0, trapframe->a1, trapframe->a2,
			trapframe->a3);
		break;
	case SYS_agent_file_edit_commit:
		ret = sys_agent_file_edit_commit(trapframe->a0, trapframe->a1,
						 trapframe->a2);
		break;
	case SYS_agent_file_edit_abort:
		ret = sys_agent_file_edit_abort(trapframe->a0);
		break;
	case SYS_agent_file_edit_state:
		ret = sys_agent_file_edit_state(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_worker_create:
		ret = sys_agent_worker_create(trapframe->a0, trapframe->a1);
		break;
	case SYS_agent_route_config:
		ret = sys_agent_route_config(trapframe->a0, trapframe->a1,
					     trapframe->a2, trapframe->a3);
		break;
	// LAB5：可在此加入 SYS_enable_deadlock_detect 分支。
	default:
		ret = -1;
	}
	return ret;
}

/* 事务对象复用当前线程的陷阱页，传统无事务调用不支付初始化和栈成本。 */
static __attribute__((noinline)) int syscall_slow_path(
	struct trapframe *trapframe, int id, uint policy,
	struct agent_execution_direct_guard *direct_guard,
	int *operation_denied)
{
	struct syscall_transaction_context *transaction =
		(struct syscall_transaction_context *)
			thread_trap_cold(curr_thread())->syscall_transaction;
	uint64 direct_side_effects;
	int ret = -1;

	syscall_transaction_prepare(transaction, trapframe, id, policy);
	direct_side_effects = syscall_direct_agent_side_effects(
		id, trapframe, transaction);
	if (direct_side_effects != 0) {
		ret = agent_execution_contract_gate_direct_syscall(
			curr_proc(), id, direct_side_effects, direct_guard);
		if (ret != AGENT_STATUS_OK) {
			*operation_denied = 1;
			goto finish;
		}
	}
	if (syscall_merge_ingress_provenance(id, transaction) < 0) {
		*operation_denied = 1;
		goto finish;
	}
	if (syscall_transaction_begin(transaction, trapframe) == 0)
		ret = syscall_dispatch(id, trapframe, transaction);

finish:
	syscall_transaction_finish(transaction, &ret);
	return ret;
}

static int syscall_needs_transaction(enum syscall_class class)
{
	return class != SYSCALL_CLASS_FAST && class != SYSCALL_CLASS_INVALID;
}

static int
syscall_mutates_workflow_cut(int id, const struct trapframe *trapframe)
{
	(void)trapframe;
	switch (id) {
	/*
	 * Ordinary calls own one outer cut from before transaction preparation
	 * through descriptor/I/O settlement.  This is deliberately an exact
	 * closed set: adding a syscall requires deciding whether it mutates the
	 * workflow, is self-gated, or is a genuine observer.
	 */
	case SYS_write:
	case SYS_read:
	case SYS_fstat:
	case SYS_openat:
	case SYS_unlinkat:
	case SYS_close:
	case SYS_sync:
	case SYS_fsync:
	case SYS_fdatasync:
	case SYS_brk:
	case SYS_mailread:
	case SYS_mailwrite:
	case SYS_trace:
	case SYS_execve:
	case SYS_pipe2:
	case SYS_thread_create:
	case SYS_waittid:
	case SYS_mutex_create:
	case SYS_mutex_lock:
	case SYS_mutex_unlock:
	case SYS_semaphore_create:
	case SYS_semaphore_up:
	case SYS_semaphore_down:
	case SYS_condvar_create:
	case SYS_condvar_signal:
	case SYS_condvar_wait:
	case SYS_agent_scope_delegate_fd:
	case SYS_agent_execution_contract:
	case SYS_agent_sched_config:
	case SYS_agent_audit_receipt:
	case SYS_virtio_disk_test:
	case SYS_physical_page_test:
	case SYS_wait_atomic_test:
	case SYS_fs_allocator_fault_test:
	case SYS_context_push:
	case SYS_context_rollback:
	case SYS_context_clear:
	case SYS_agent_watch:
	case SYS_agent_unwatch:
	case SYS_agent_wait:
	case SYS_agent_wait_cancel:
	case SYS_agent_heartbeat:
	case SYS_agent_heartbeat_set:
	case SYS_agent_heartbeat_stop:
	case SYS_agent_wake:
	case SYS_agent_file_edit_begin:
	case SYS_agent_file_edit_commit:
	case SYS_agent_file_edit_abort:
	case SYS_agent_file_edit_state:
	case SYS_agent_route_config:
		return 1;
	/*
	 * wait4 sleeps without the outer token.  Child teardown is covered by the
	 * departure protocol and sys_wait reacquires the token for result copyout.
	 */
	case SYS_wait4:
		return 0;
	/* fork/create and Agent execution paths retain their inner cut. */
	case SYS_clone:
	case SYS_agent_create:
	case SYS_agent_create_role:
	case SYS_agent_workflow_create:
	case SYS_agent_run:
	case SYS_agent_call:
	case SYS_tool_call:
	case SYS_agent_file_meta_init:
	case SYS_agent_file_meta_set:
	case SYS_agent_file_query:
	case SYS_agent_worker_create:
	case SYS_agent_task_channel_setup:
	case SYS_agent_task_channel_enter:
	case SYS_agent_task_channel_resource:
		return 0;
	/* Exit/close drive their own lifecycle protocols; never nest the gate. */
	case SYS_exit:
	case SYS_agent_workflow_close:
		return 0;
	/* Scheduler control without pending background work and pure observers. */
	case SYS_sched_yield:
	case SYS_gettimeofday:
	case SYS_getpid:
	case SYS_getppid:
	case SYS_gettid:
	case SYS_kernel_work_last_preemptions:
	case SYS_io_policy_info:
	case SYS_agent_launch_info:
	case SYS_agent_workflow_lifecycle_info:
	case SYS_agent_resource_snapshot:
	case SYS_agent_performance_snapshot:
	case SYS_agent_info:
	case SYS_agent_sched_snapshot:
	case SYS_agent_trace_snapshot:
	case SYS_agent_audit_snapshot:
	case SYS_agent_audit_query:
	case SYS_agent_span_trace_snapshot:
	case SYS_agent_timeline_snapshot:
	case SYS_agent_timeline_query:
	case SYS_agent_timeline_wait:
	case SYS_agent_timeline_read:
	case SYS_agent_provenance_snapshot:
	case SYS_agent_ledger_snapshot:
	case SYS_agent_tool_list:
	case SYS_tool_list:
	case SYS_context_query:
	case SYS_context_snapshot:
	case SYS_context_detail:
		return 0;
	default:
		return 0;
	}
}

static uint64
syscall_file_side_effects(const struct file *file, int closing)
{
	if (file == 0)
		return 0;
	switch (file->type) {
	case FD_STDIO:
		return 0;
	case FD_PIPE:
		return AGENT_SIDE_EFFECT_IPC;
	case FD_INODE:
		return closing ?
			AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA :
			AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_ARTIFACT;
	default:
		return AGENT_SIDE_EFFECT_ALL;
	}
}

static uint64
syscall_direct_agent_side_effects(
	int id, const struct trapframe *trapframe,
	const struct syscall_transaction_context *transaction)
{
	switch (id) {
	case SYS_write:
		return syscall_file_side_effects(
			transaction != 0 ? transaction->file : 0, 0);
	case SYS_openat:
		return trapframe != 0 &&
		       (trapframe->a1 & (O_CREATE | O_TRUNC)) != 0 ?
			AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA : 0;
	case SYS_close:
		/*
		 * The descriptor identity is authoritative only after
		 * fdclose_prepare() detaches it.  Gate the union of every effect a
		 * detachable file can produce instead of racing that detach with a
		 * speculative pin.
		 */
		return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA |
		       AGENT_SIDE_EFFECT_IPC;
	case SYS_unlinkat:
	case SYS_sync:
	case SYS_fsync:
	case SYS_fdatasync:
		return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA;
	case SYS_mailwrite:
	case SYS_agent_heartbeat:
	case SYS_agent_heartbeat_set:
	case SYS_agent_heartbeat_stop:
	case SYS_agent_wake:
		return AGENT_SIDE_EFFECT_IPC;
	case SYS_pipe2:
		return AGENT_SIDE_EFFECT_PROCESS | AGENT_SIDE_EFFECT_IPC;
	case SYS_clone:
	case SYS_execve:
	case SYS_thread_create:
	case SYS_agent_create:
	case SYS_agent_create_role:
	case SYS_agent_workflow_create:
	case SYS_agent_worker_create:
		return AGENT_SIDE_EFFECT_PROCESS;
	case SYS_agent_scope_delegate_fd:
	case SYS_agent_sched_config:
	case SYS_agent_route_config:
		return AGENT_SIDE_EFFECT_PERMISSION;
	case SYS_agent_workflow_close:
		return AGENT_SIDE_EFFECT_PROCESS | AGENT_SIDE_EFFECT_PERMISSION;
	case SYS_context_push:
	case SYS_context_rollback:
	case SYS_context_clear:
		return AGENT_SIDE_EFFECT_METADATA | AGENT_SIDE_EFFECT_PERMISSION;
	case SYS_agent_watch:
	case SYS_agent_unwatch:
	case SYS_agent_wait_cancel:
		return AGENT_SIDE_EFFECT_WATCH;
	case SYS_agent_file_meta_init:
	case SYS_agent_file_meta_set:
	case SYS_agent_file_edit_begin:
	case SYS_agent_file_edit_commit:
	case SYS_agent_file_edit_abort:
		return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA;
	default:
		return 0;
	}
}

static int
syscall_merge_ingress_provenance(
	int id, const struct syscall_transaction_context *transaction)
{
	struct proc *p = curr_proc();
	const struct file *file;
	uint64 labels = 0;

	if (p == 0 || !p->is_agent)
		return 0;
	file = transaction != 0 ? transaction->file : 0;
	switch (id) {
	case SYS_read:
		if (file == 0)
			return 0;
		switch (file->type) {
		case FD_STDIO:
			labels = AGENT_PROVENANCE_TRUSTED_USER_CONTROL;
			break;
		case FD_PIPE:
			labels = AGENT_PROVENANCE_CROSS_AGENT_DATA |
				 AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;
			break;
		case FD_INODE:
			labels = AGENT_PROVENANCE_UNTRUSTED_FILE_DATA;
			break;
		default:
			labels = AGENT_PROVENANCE_UNTRUSTED_MASK;
			break;
		}
		break;
	case SYS_mailread:
		labels = AGENT_PROVENANCE_CROSS_AGENT_DATA |
			 AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;
		break;
	default:
		break;
	}
	if (labels == 0)
		return 0;
	return agent_provenance_merge_current(p, labels) == AGENT_STATUS_OK ?
		0 : -1;
}

/* 拒绝高位别名，避免超宽 a7 截断成已登记的低编号调用。 */
static int syscall_decode_id(uint64 raw_id)
{
	if (raw_id > 0x7fffffffULL)
		return -1;
	return (int)raw_id;
}

void syscall()
{
	struct trapframe *trapframe = curr_thread()->trapframe;
	struct agent_execution_direct_guard direct_guard = { 0 };
	struct agent_execution_direct_guard file_pin_guard = { 0 };
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	uint64 direct_side_effects;
	int id;
	int background_operation_entered = 0;
	int operation_denied = 0;
	int operation_entered = 0;
	int background_done = 0;
	int ret;
	enum syscall_class class;

	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	id = syscall_decode_id(trapframe->a7);
	class = syscall_classify(id);
	kernel_work_begin_syscall(id, syscall_kernel_work_class(id));
	lifecycle = vfs_proc_lifecycle(curr_proc());
	if (class == SYSCALL_CLASS_INVALID)
		ret = -1;
	else {
		uint policy = syscall_policy_base(class);

		if (workflow_lifecycle_key_valid(lifecycle) &&
		    syscall_mutates_workflow_cut(id, trapframe)) {
			if (workflow_lifecycle_operation_enter(lifecycle) < 0) {
				ret = -1;
				operation_denied = 1;
				goto operation_done;
			}
			operation_entered = 1;
		}
		if (id == SYS_read || id == SYS_write || id == SYS_fstat) {
			ret = agent_execution_contract_file_pin_enter(
				curr_proc(), &file_pin_guard);
			if (ret != AGENT_STATUS_OK) {
				operation_denied = 1;
				goto operation_done;
			}
		}
		if (syscall_needs_transaction(class)) {
			ret = syscall_slow_path(
				trapframe, id, policy, &direct_guard,
				&operation_denied);
		} else {
			direct_side_effects = syscall_direct_agent_side_effects(
				id, trapframe, 0);
			if (direct_side_effects != 0) {
				ret = agent_execution_contract_gate_direct_syscall(
					curr_proc(), id, direct_side_effects,
					&direct_guard);
				if (ret != AGENT_STATUS_OK) {
					operation_denied = 1;
					goto operation_done;
				}
			}
			if (syscall_merge_ingress_provenance(id, 0) < 0) {
				ret = -1;
				operation_denied = 1;
				goto operation_done;
			}
			ret = syscall_dispatch(id, trapframe, 0);
		}
	operation_done:
		if (operation_entered) {
			/* Current-scope maintenance is part of the same fence cut. */
			if ((!operation_denied || direct_guard.active ||
			     file_pin_guard.active) &&
			    (agent_background_work_pending() || id == SYS_sched_yield))
				agent_background_checkpoint();
			background_done = 1;
		}
	}
	/*
	 * 仿照 Linux task_work：生产者只发布低成本待办标记，返回任务在
	 * syscall 资源结算后再处理。维护工作不占中断或空闲栈，传统调用也
	 * 无需为每次完整后台扫描付费。
	 */
	if (!background_done && !operation_denied &&
	    !(id == SYS_agent_run && trapframe->a2 == 0 &&
	      trapframe->a3 == AGENT_RUN_F_FENCE) &&
	    (agent_background_work_pending() || id == SYS_sched_yield)) {
		if (!workflow_lifecycle_key_valid(lifecycle)) {
			agent_background_checkpoint();
		} else if (workflow_lifecycle_operation_enter(lifecycle) == 0) {
			/* Observers/self-gated calls only pay this gate when work exists. */
			background_operation_entered = 1;
			agent_background_checkpoint();
		}
	}
	agent_execution_contract_file_pin_leave(&file_pin_guard);
	if (direct_guard.active)
		agent_execution_contract_direct_leave(&direct_guard);
	if (operation_entered)
		workflow_lifecycle_operation_leave(lifecycle);
	if (background_operation_entered)
		workflow_lifecycle_operation_leave(lifecycle);
	kernel_work_end();
	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	trapframe->a0 = ret;
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d ret %d", id, ret);
	}
}
