#include "console.h"
#include "agent.h"
#include "agent_internal.h"
#include "bio.h"
#include "defs.h"
#include "fcntl.h"
#include "fs_epoch.h"
#include "kernel_work.h"
#include "loader.h"
#include "sync.h"
#include "syscall.h"
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

/* Keep the Linux-compatible user Stat layout from user/include/stddef.h. */
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
	if (f->type == FD_INODE && ivalid(f->ip) == 0 &&
	    vfs_inode_authorize(f->ip, &cred, VFS_OP_READ)) {
		status.dev = f->ip->dev;
		status.ino = f->ip->inum;
		status.mode = f->ip->type == T_DIR ? 0x040000U : 0x100000U;
		/* This filesystem has no linkat: a named inode has one link. */
		status.nlink = f->ip->removed ? 0U : 1U;
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

int sys_kernel_work_receipt_snapshot(uint64 addr, uint64 user_size)
{
	struct proc *p = curr_proc();
	struct kernel_work_receipt receipt;

	if (addr == 0 || user_size != sizeof(receipt) ||
	    user_range_check(p->pagetable, addr, sizeof(receipt), PTE_W) < 0 ||
	    kernel_work_receipt_snapshot(curr_thread(), &receipt) < 0)
		return -1;
	return copyout(p->pagetable, addr, (char *)&receipt, sizeof(receipt));
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
		return p->syscall_count[id];
	default:
		return -1;
	}
}

int sys_mailwrite(int pid, uint64 buf, int len)
{
	struct proc *sender = curr_proc();
	char payload[MAILBOX_PAYLOAD_SIZE];

	if (len <= 0 || len > MAILBOX_PAYLOAD_SIZE)
		return -1;
	if (copyin(sender->pagetable, payload, buf, len) < 0)
		return -1;
	return agent_ipc_legacy_public_send(sender, pid, payload, len);
}

int sys_mailread(uint64 buf, int len)
{
	struct proc *p = curr_proc();
	struct agent_legacy_read_receipt receipt;
	char payload[MAILBOX_PAYLOAD_SIZE];
	int n;

	if (len <= 0 || len > MAILBOX_PAYLOAD_SIZE)
		return -1;
	if (user_range_check(p->pagetable, buf, len, PTE_W) < 0)
		return -1;
	n = agent_ipc_legacy_public_read_begin(p, payload, len, &receipt);
	if (n <= 0)
		return n;
	if (copyout(p->pagetable, buf, payload, n) < 0) {
		(void)agent_ipc_legacy_public_read_finish(p, &receipt, 0);
		return -1;
	}
	if (agent_ipc_legacy_public_read_finish(p, &receipt, 1) < 0)
		return -1;
	return n;
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
	int code;
	int child;

	if (va &&
	    user_range_check(p->pagetable, va, sizeof(code), PTE_W) < 0)
		return -1;
	child = wait(pid, &code);
	if (child < 0 || va == 0)
		return child;
	if (copyout(p->pagetable, va, (char *)&code, sizeof(code)) < 0)
		return -1;
	return child;
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
	// Both sentinels were installed before any operation that can publish
	// these numbers, so this commit cannot fail without kernel corruption.
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

/*
*	LAB5: (3) In the TA's reference implementation, here defines funtion
*					int deadlock_detect(const int available[LOCK_POOL_SIZE],
*						const int allocation[NTHREAD][LOCK_POOL_SIZE],
*						const int request[NTHREAD][LOCK_POOL_SIZE])
*				for both mutex and semaphore detect, you can also
*				use this idea or just ignore it.
*/

int sys_mutex_create(int blocking)
{
	struct mutex *m = mutex_create(blocking);
	if (m == NULL) {
		errorf("fail to create mutex: out of resource");
		return -1;
	}
	// Keep the teaching mutex ABI; AgentOS does not extend deadlock detection here.
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
	// Keep the teaching semaphore ABI; AgentOS does not extend deadlock detection here.
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
	// Semaphore up keeps the original uCore behavior.
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

// LAB5: (2) you may need to define function enable_deadlock_detect here

extern char trap_page[];

/* Admission and execution share one pinned file-table identity. */
struct syscall_transaction_context {
	int id;
	uint64 args[6];
	struct file *file;
	struct file_close_receipt close_receipt;
	int fd_uses_disk;
	int close_attempted;
	int close_result;
	int close_final;
	int io_admitted;
	int io_cleanup_admitted;
	int fs_epoch_admitted;
};

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

// New syscalls are admitted by default. A syscall may bypass the block-I/O
// governor only when its implementation is known not to reach the filesystem.
static int syscall_may_issue_block_io(
	const struct syscall_transaction_context *transaction)
{
	int id = transaction->id;

	switch (id) {
	case SYS_read:
	case SYS_write:
		return transaction->fd_uses_disk;
	case SYS_close:
		return 0;
	case SYS_sched_yield:
	case SYS_gettimeofday:
	case SYS_getpid:
	case SYS_getppid:
	case SYS_brk:
	case SYS_mailread:
	case SYS_mailwrite:
	case SYS_trace:
	case SYS_clone:
	case SYS_wait4:
	case SYS_pipe2:
	case SYS_thread_create:
	case SYS_gettid:
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
	case SYS_kernel_work_last_preemptions:
	case SYS_kernel_work_receipt_snapshot:
	case SYS_io_policy_info:
	case SYS_agent_scope_delegate_fd:
	case SYS_agent_workflow_close:
	case SYS_agent_workflow_lifecycle_info:
	case SYS_agent_resource_snapshot:
	case SYS_agent_performance_snapshot:
	case SYS_agent_info:
	case SYS_agent_sched_snapshot:
	case SYS_agent_sched_config:
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
	case SYS_agent_file_prefetch_snapshot:
	case SYS_agent_file_prefetch_span_snapshot:
	case SYS_agent_tool_list:
	case SYS_tool_list:
	case SYS_agent_watch:
	case SYS_agent_unwatch:
	case SYS_agent_wait:
	case SYS_agent_wait_cancel:
	case SYS_agent_heartbeat:
	case SYS_agent_heartbeat_set:
	case SYS_agent_heartbeat_stop:
	case SYS_agent_wake:
	case SYS_agent_route_config:
	case SYS_agent_observe_recovery:
	case SYS_exit:
#ifdef AGENT_METADATA_CRASH_PHASE
	case SYS_agent_metadata_test:
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
	case SYS_physical_page_test:
#endif
#ifdef WAIT_ATOMIC_TEST_PROFILE
	case SYS_wait_atomic_test:
#endif
		return 0;
	default:
		return 1;
	}
}

/*
 * Filesystem mutation is serialized below the VFS so one ordered epoch can
 * publish allocation, inode and namespace state without per-operation
 * barriers.  Pure reads and non-filesystem teaching calls stay off this path.
 */
static int syscall_needs_fs_epoch(
	const struct syscall_transaction_context *transaction)
{
	int id = transaction->id;
	const uint64 *args = transaction->args;

	switch (id) {
	case SYS_read:
		return 0;
	case SYS_write:
		return transaction->fd_uses_disk;
	case SYS_close:
		return 0;
	case SYS_openat:
		return (args[1] & (O_CREATE | O_TRUNC)) != 0;
	case SYS_unlinkat:
	case SYS_sync:
	case SYS_fsync:
	case SYS_fdatasync:
		return 1;
	case SYS_fstat:
	case SYS_execve:
	case SYS_sched_yield:
	case SYS_gettimeofday:
	case SYS_getpid:
	case SYS_getppid:
	case SYS_brk:
	case SYS_mailread:
	case SYS_mailwrite:
	case SYS_trace:
	case SYS_clone:
	case SYS_wait4:
	case SYS_pipe2:
	case SYS_thread_create:
	case SYS_gettid:
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
	case SYS_kernel_work_last_preemptions:
	case SYS_kernel_work_receipt_snapshot:
	case SYS_io_policy_info:
	case SYS_agent_resource_snapshot:
	case SYS_agent_performance_snapshot:
	case SYS_agent_info:
	case SYS_agent_sched_snapshot:
	case SYS_agent_trace_snapshot:
	case SYS_agent_audit_snapshot:
	case SYS_agent_audit_query:
	case SYS_agent_audit_receipt:
	case SYS_agent_span_trace_snapshot:
	case SYS_agent_timeline_snapshot:
	case SYS_agent_timeline_query:
	case SYS_agent_timeline_wait:
	case SYS_agent_timeline_read:
	case SYS_agent_provenance_snapshot:
	case SYS_agent_ledger_snapshot:
	case SYS_agent_file_prefetch_snapshot:
	case SYS_agent_file_prefetch_span_snapshot:
	case SYS_agent_tool_list:
	case SYS_tool_list:
	case SYS_agent_watch:
	case SYS_agent_unwatch:
	case SYS_agent_wait:
	case SYS_agent_wait_cancel:
	case SYS_agent_heartbeat:
	case SYS_agent_heartbeat_set:
	case SYS_agent_heartbeat_stop:
	case SYS_agent_wake:
	case SYS_agent_route_config:
	case SYS_exit:
#ifdef VIRTIO_DISK_TEST_PROFILE
	case SYS_virtio_disk_test:
#endif
		return 0;
	default:
		/* New Agent calls fail closed into the ordered mutation path. */
		return 1;
	}
}

static void syscall_transaction_prepare(
	struct syscall_transaction_context *transaction,
	const struct trapframe *trapframe)
{
	memset(transaction, 0, sizeof(*transaction));
	transaction->id = trapframe->a7;
	transaction->args[0] = trapframe->a0;
	transaction->args[1] = trapframe->a1;
	transaction->args[2] = trapframe->a2;
	transaction->args[3] = trapframe->a3;
	transaction->args[4] = trapframe->a4;
	transaction->args[5] = trapframe->a5;
	switch (transaction->id) {
	case SYS_read:
	case SYS_write:
		transaction->file = syscall_fd_pin(transaction->args[0]);
		transaction->fd_uses_disk =
			syscall_file_uses_disk(transaction->file);
		break;
	default:
		break;
	}
}

static int syscall_transaction_begin(
	struct syscall_transaction_context *transaction)
{
	if (transaction->id == SYS_close) {
		int close_status;

		transaction->close_attempted = 1;
		if (transaction->args[0] >= FD_BUFFER_SIZE)
			close_status = -1;
		else
			close_status = fdclose_prepare(
				(int)transaction->args[0],
				&transaction->close_receipt);
		transaction->close_result = close_status < 0 ? -1 : 0;
		if (close_status <= 0)
			return 0;
		transaction->close_final = 1;
		if (transaction->close_receipt.type != FD_INODE)
			return 0;
		return 0;
	}
	/* Serialize mutators before reserving scarce device-rate capacity. */
	if (syscall_needs_fs_epoch(transaction)) {
		if (fs_epoch_request_begin() < 0)
			return -1;
		transaction->fs_epoch_admitted = 1;
	}
	if (syscall_may_issue_block_io(transaction)) {
		int io_begin_result = transaction->fs_epoch_admitted ?
			bio_request_begin_current() :
			bio_request_begin_current_lazy();

		if (io_begin_result < 0) {
			/* A failed admission can race descriptor close. Obtain the
			 * teardown reservation before dropping a possibly-final pin. */
			if (transaction->file != 0 &&
			    transaction->fd_uses_disk &&
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
	/* A concurrent close/reopen cannot redirect this reference drop. */
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
		/* Never acquire the filesystem gate while carrying an I/O lease. */
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
	case SYS_kernel_work_receipt_snapshot:
	case SYS_agent_resource_snapshot:
	case SYS_agent_performance_snapshot:
		return KERNEL_WORK_SYSCALL_OBSERVER;
	default:
		return KERNEL_WORK_SYSCALL_PUBLISH;
	}
}

void syscall()
{
	struct trapframe *trapframe = curr_thread()->trapframe;
	struct syscall_transaction_context transaction;
	uint64 *args;
	int id;
	int ret;

	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	syscall_transaction_prepare(&transaction, trapframe);
	id = transaction.id;
	args = transaction.args;
	kernel_work_begin_syscall(id, syscall_kernel_work_class(id));
	if (syscall_transaction_begin(&transaction) < 0) {
		ret = -1;
		goto syscall_out;
	}
	if (id >= 0 && id < SYSCALL_COUNT_MAX)
		curr_proc()->syscall_count[id]++;
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d args = [%lx, %lx, %lx, %lx, %lx, %lx]", id,
		       args[0], args[1], args[2], args[3], args[4], args[5]);
	}
	switch (id) {
	case SYS_write:
		ret = sys_write(transaction.file, (int)args[0],
				args[1], args[2]);
		break;
	case SYS_read:
		ret = sys_read(transaction.file, (int)args[0],
			       args[1], args[2]);
		break;
	case SYS_fstat:
		ret = sys_fstat(args[0], args[1]);
		break;
	case SYS_openat:
		ret = sys_openat(args[0], args[1], args[2]);
		break;
	case SYS_unlinkat:
		ret = sys_unlinkat(args[0], args[1], args[2]);
		break;
	case SYS_close:
		ret = transaction.close_attempted ?
			transaction.close_result : -1;
		break;
	case SYS_sync:
		ret = sys_sync();
		break;
	case SYS_fsync:
	case SYS_fdatasync:
		ret = sys_fsync(args[0]);
		break;
	case SYS_exit:
		sys_exit(args[0]);
		// __builtin_unreachable();
	// case SYS_nanosleep:
	// 	ret = sys_nanosleep(args[0]);
	// 	break;
	case SYS_sched_yield:
		ret = sys_sched_yield();
		break;
	case SYS_brk:
		ret = sys_sbrk(args[0]);
		break;
	case SYS_gettimeofday:
		ret = sys_gettimeofday(args[0], args[1]);
		break;
	case SYS_getpid:
		ret = sys_getpid();
		break;
	case SYS_getppid:
		ret = sys_getppid();
		break;
	case SYS_mailread:
		ret = sys_mailread(args[0], args[1]);
		break;
	case SYS_mailwrite:
		ret = sys_mailwrite(args[0], args[1], args[2]);
		break;
	case SYS_trace:
		ret = sys_trace(args[0], args[1], args[2]);
		break;
	case SYS_clone: // SYS_fork
		ret = sys_clone();
		break;
	case SYS_execve:
		ret = sys_exec(args[0], args[1]);
		break;
	case SYS_wait4:
		ret = sys_wait(args[0], args[1]);
		break;
	case SYS_pipe2:
		ret = sys_pipe(args[0]);
		break;
	case SYS_thread_create:
		ret = sys_thread_create(args[0], args[1]);
		break;
	case SYS_gettid:
		ret = sys_gettid();
		break;
	case SYS_waittid:
		ret = sys_waittid(args[0]);
		break;
	case SYS_mutex_create:
		ret = sys_mutex_create(args[0]);
		break;
	case SYS_mutex_lock:
		ret = sys_mutex_lock(args[0]);
		break;
	case SYS_mutex_unlock:
		ret = sys_mutex_unlock(args[0]);
		break;
	case SYS_semaphore_create:
		ret = sys_semaphore_create(args[0]);
		break;
	case SYS_semaphore_up:
		ret = sys_semaphore_up(args[0]);
		break;
	case SYS_semaphore_down:
		ret = sys_semaphore_down(args[0]);
		break;
	case SYS_condvar_create:
		ret = sys_condvar_create();
		break;
	case SYS_condvar_signal:
		ret = sys_condvar_signal(args[0]);
		break;
	case SYS_condvar_wait:
		ret = sys_condvar_wait(args[0], args[1]);
		break;
	case SYS_kernel_work_last_preemptions:
		ret = sys_kernel_work_last_preemptions();
		break;
	case SYS_kernel_work_receipt_snapshot:
		ret = sys_kernel_work_receipt_snapshot(args[0], args[1]);
		break;
	case SYS_io_policy_info:
		ret = sys_io_policy_info(args[0], args[1]);
		break;
	case SYS_agent_create:
		ret = sys_agent_create();
		break;
	case SYS_agent_create_role:
		ret = sys_agent_create_role(args[0]);
		break;
	case SYS_agent_workflow_create:
		ret = sys_agent_workflow_create(args[0]);
		break;
	case SYS_agent_scope_delegate_fd:
		ret = sys_agent_scope_delegate_fd(args[0]);
		break;
	case SYS_agent_workflow_close:
		ret = sys_agent_workflow_close(args[0]);
		break;
	case SYS_agent_workflow_lifecycle_info:
		ret = sys_agent_workflow_lifecycle_info(
			args[0], args[1], args[2], args[3], args[4]);
		break;
	case SYS_agent_resource_snapshot:
		ret = sys_agent_resource_snapshot(args[0], args[1]);
		break;
	case SYS_agent_performance_snapshot:
		ret = sys_agent_performance_snapshot(args[0], args[1]);
		break;
	case SYS_agent_info:
		ret = sys_agent_info(args[0]);
		break;
	case SYS_agent_sched_snapshot:
		ret = sys_agent_sched_snapshot(args[0], args[1]);
		break;
	case SYS_agent_sched_config:
		ret = sys_agent_sched_config(args[0]);
		break;
	case SYS_agent_trace_snapshot:
		ret = sys_agent_trace_snapshot(args[0], args[1]);
		break;
	case SYS_agent_audit_snapshot:
		ret = sys_agent_audit_snapshot(args[0], args[1]);
		break;
	case SYS_agent_audit_query:
		ret = sys_agent_audit_query(args[0], args[1], args[2]);
		break;
	case SYS_agent_audit_receipt:
		ret = sys_agent_audit_receipt(args[0]);
		break;
	case SYS_agent_span_trace_snapshot:
		ret = sys_agent_span_trace_snapshot(args[0], args[1]);
		break;
	case SYS_agent_timeline_snapshot:
		ret = sys_agent_timeline_snapshot(args[0], args[1]);
		break;
	case SYS_agent_timeline_query:
		ret = sys_agent_timeline_query(args[0], args[1], args[2]);
		break;
	case SYS_agent_timeline_wait:
		ret = sys_agent_timeline_wait(args[0], args[1]);
		break;
	case SYS_agent_timeline_read:
		ret = sys_agent_timeline_read(args[0], args[1], args[2],
					      args[3]);
		break;
	case SYS_agent_provenance_snapshot:
		ret = sys_agent_provenance_snapshot(args[0], args[1]);
		break;
	case SYS_agent_ledger_snapshot:
		ret = sys_agent_ledger_snapshot(args[0]);
		break;
	case SYS_agent_file_prefetch_snapshot:
		ret = sys_agent_file_prefetch_snapshot(args[0], args[1]);
		break;
	case SYS_agent_file_prefetch_span_snapshot:
		ret = sys_agent_file_prefetch_span_snapshot(args[0], args[1]);
		break;
	case SYS_agent_run:
		ret = sys_agent_run(args[0], args[1], args[2], args[3]);
		break;
	case SYS_agent_call:
		ret = sys_agent_call(args[0], args[1]);
		break;
	case SYS_agent_tool_list:
		ret = sys_agent_tool_list(args[0], args[1]);
		break;
	case SYS_tool_call:
		ret = sys_tool_call(args[0], args[1]);
		break;
	case SYS_tool_list:
		ret = sys_tool_list(args[0], args[1], args[2], args[3]);
		break;
	case SYS_agent_observe_recovery:
		ret = sys_agent_observe_recovery(args[0], args[1]);
		break;
#ifdef VIRTIO_DISK_TEST_PROFILE
	case SYS_virtio_disk_test:
		ret = sys_virtio_disk_test(args[0], args[1], args[2], args[3],
					   args[4], args[5]);
		break;
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
	case SYS_physical_page_test:
		ret = sys_physical_page_test(args[0], args[1], args[2]);
		break;
#endif
#ifdef AGENT_METADATA_CRASH_PHASE
	case SYS_agent_metadata_test:
		ret = sys_agent_metadata_test(args[0], args[1], args[2]);
		break;
#endif
#ifdef WAIT_ATOMIC_TEST_PROFILE
	case SYS_wait_atomic_test:
		ret = sys_wait_atomic_test(args[0], args[1], args[2], args[3],
					   args[4], args[5]);
		break;
#endif
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
	case SYS_fs_allocator_fault_test:
		ret = sys_fs_allocator_fault_test(
			args[0], args[1], args[2], args[3], args[4], args[5]);
		break;
#endif
	case SYS_context_push:
		ret = sys_context_push(args[0]);
		break;
	case SYS_context_query:
		ret = sys_context_query(args[0], args[1], args[2]);
		break;
	case SYS_context_snapshot:
		ret = sys_context_snapshot(args[0], args[1], args[2]);
		break;
	case SYS_context_detail:
		ret = sys_context_detail(args[0], args[1]);
		break;
	case SYS_context_rollback:
		ret = sys_context_rollback(args[0]);
		break;
	case SYS_context_clear:
		ret = sys_context_clear();
		break;
	case SYS_agent_watch:
		ret = sys_agent_watch(args[0], args[1]);
		break;
	case SYS_agent_unwatch:
		ret = sys_agent_unwatch(args[0], args[1]);
		break;
	case SYS_agent_wait:
		ret = sys_agent_wait(args[0], args[1]);
		break;
	case SYS_agent_wait_cancel:
		ret = sys_agent_wait_cancel(args[0], args[1]);
		break;
	case SYS_agent_heartbeat:
		ret = sys_agent_heartbeat(args[0]);
		break;
	case SYS_agent_heartbeat_set:
		ret = sys_agent_heartbeat_set(args[0]);
		break;
	case SYS_agent_heartbeat_stop:
		ret = sys_agent_heartbeat_stop();
		break;
	case SYS_agent_wake:
		ret = sys_agent_wake(args[0], args[1]);
		break;
	case SYS_agent_file_meta_init:
		ret = sys_agent_file_meta_init();
		break;
	case SYS_agent_file_meta_set:
		ret = sys_agent_file_meta_set(args[0]);
		break;
	case SYS_agent_file_query:
		ret = sys_agent_file_query(args[0], args[1]);
		break;
	case SYS_agent_file_edit_begin:
		ret = sys_agent_file_edit_begin(args[0], args[1], args[2],
						args[3]);
		break;
	case SYS_agent_file_edit_commit:
		ret = sys_agent_file_edit_commit(args[0], args[1], args[2]);
		break;
	case SYS_agent_file_edit_abort:
		ret = sys_agent_file_edit_abort(args[0]);
		break;
	case SYS_agent_file_edit_state:
		ret = sys_agent_file_edit_state(args[0], args[1]);
		break;
	case SYS_agent_worker_create:
		ret = sys_agent_worker_create(args[0], args[1]);
		break;
	case SYS_agent_route_config:
		ret = sys_agent_route_config(args[0], args[1], args[2], args[3]);
		break;
	// LAB5: (2) you may need to add case SYS_enable_deadlock_detect here
	default:
		ret = -1;
		errorf("unknown syscall %d", id);
	}
syscall_out:
	syscall_transaction_finish(&transaction, &ret);
	/* Timer delivery owns periodic maintenance.  A voluntary yield is the
	 * only syscall-tail assist, so unrelated calls never inherit writeback. */
	if (id == SYS_sched_yield)
		agent_background_checkpoint();
	kernel_work_end();
	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	curr_thread()->trapframe->a0 = ret;
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d ret %d", id, ret);
	}
}
