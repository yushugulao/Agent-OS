#include "console.h"
#include "agent.h"
#include "bio.h"
#include "defs.h"
#include "kernel_work.h"
#include "loader.h"
#include "sync.h"
#include "syscall.h"
#include "syscall_ids.h"
#include "timer.h"
#include "trap.h"

extern struct proc pool[NPROC];

enum trace_request {
	TRACE_READ,
	TRACE_WRITE,
	TRACE_SYSCALL,
};

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
			    kernel_work_checkpoint(KERNEL_WORK_STREAM_GRANULE) < 0)
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
		int c = consgetc();
		if (c < 0) {
			if (got != 0)
				break;
			if (proc_thread_exit_requested())
				return (uint64)-1;
			yield();
			continue;
		}
		buf[got++] = c;
	}
	if (copyout(p->pagetable, va, buf, got) < 0)
		return (uint64)-1;
	return got;
}

uint64 sys_write(int fd, uint64 va, uint64 len)
{
	uint64 result;
	struct file *f;
	struct proc *p;

	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	p = curr_proc();
	f = fdget(fd);
	if (f == NULL) {
		errorf("invalid fd %d\n", fd);
		return -1;
	}
	if (!f->writable || len > MAX_RW_COUNT ||
	    user_range_check(p->pagetable, va, len, PTE_R) < 0) {
		fileclose(f);
		return -1;
	}
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
	fileclose(f);
	return result;
}

uint64 sys_read(int fd, uint64 va, uint64 len)
{
	uint64 result;
	struct file *f;
	struct proc *p;

	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	p = curr_proc();
	f = fdget(fd);
	if (f == NULL) {
		errorf("invalid fd %d\n", fd);
		return -1;
	}
	if (!f->readable || len > MAX_RW_COUNT ||
	    user_range_check(p->pagetable, va, len, PTE_W) < 0) {
		fileclose(f);
		return -1;
	}
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

uint64 sys_kernel_work_last_preemptions(void)
{
	return curr_thread()->kernel_last_syscall_preemptions;
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
	struct proc *target = 0;
	struct proc *sender = curr_proc();
	char payload[MAILBOX_PAYLOAD_SIZE];
	int slot;

	if (len <= 0 || len > MAILBOX_PAYLOAD_SIZE)
		return -1;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state != P_UNUSED && p->pid == pid) {
			target = p;
			break;
		}
	}
	if (target == 0 || target->mail_count >= MAILBOX_SLOT_COUNT)
		return -1;
	if (copyin(sender->pagetable, payload, buf, len) < 0)
		return -1;
	slot = target->mail_tail;
	memmove(target->mail_payload[slot], payload, len);
	target->mail_len[slot] = len;
	target->mail_from[slot] = sender->pid;
	target->mail_tail = (target->mail_tail + 1) % MAILBOX_SLOT_COUNT;
	target->mail_count++;
	return len;
}

int sys_mailread(uint64 buf, int len)
{
	struct proc *p = curr_proc();
	int slot;
	int n;

	if (len <= 0 || len > MAILBOX_PAYLOAD_SIZE)
		return -1;
	if (p->mail_count <= 0)
		return 0;
	slot = p->mail_head;
	n = MIN(len, p->mail_len[slot]);
	if (copyout(p->pagetable, buf, p->mail_payload[slot], n) < 0)
		return -1;
	memset(p->mail_payload[slot], 0, sizeof(p->mail_payload[slot]));
	p->mail_len[slot] = 0;
	p->mail_from[slot] = 0;
	p->mail_head = (p->mail_head + 1) % MAILBOX_SLOT_COUNT;
	p->mail_count--;
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
	uint64 stack_left = USTACK_SIZE;

	if (uargv == 0) {
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
			uint64 pointers = (i + 1) * sizeof(uint64);
			if (pointers > stack_left)
				return -1;
			argv[i] = 0;
			return i;
		}
		if (i == MAX_ARG_NUM || storage_used >= USTACK_SIZE)
			return -1;

		int len = copyinstr(pagetable, storage + storage_used, user_arg,
				    USTACK_SIZE - storage_used);
		if (len < 0 || (uint64)len + 1 > stack_left)
			return -1;
		argv[i] = storage + storage_used;
		storage_used += len + 1;
		stack_left -= len + 1;
		stack_left -= stack_left % 16;
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

	if (copyinstr(p->pagetable, name, path, sizeof(name)) < 0)
		return -1;
	storage = kalloc();
	if (storage == 0)
		return -1;
	if (copy_exec_args(p->pagetable, uargv, argv, storage) < 0) {
		kfree(storage);
		return -1;
	}
	rc = exec(name, argv);
	kfree(storage);
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

uint64 sys_close(int fd)
{
	if (fdclose(fd) < 0) {
		errorf("invalid fd %d", fd);
		return -1;
	}
	return 0;
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

	if (tid < 0 || tid >= NTHREAD) {
		errorf("unexpected tid %d", tid);
		return -1;
	}
	t = &curr_proc()->threads[tid];
	enabled = intr_save();
	if (t->state == T_UNUSED || tid == curr_thread()->tid) {
		intr_restore(enabled);
		return -1;
	}
	if (t->state != EXITED) {
		intr_restore(enabled);
		return -2;
	}
	if (t->kstack_state != KSTACK_NONE ||
	    t->resource_slot_charged || t->on_run_queue)
		panic("waittid unreaped thread");
	exit_code = (int)t->exit_code;
	t->tid = -1;
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

static int syscall_fd_uses_disk(uint64 fd)
{
	struct proc *p = curr_proc();
	struct file *f;
	int result = 0;
	int enabled = intr_save();

	if (p != 0 && fd < FD_BUFFER_SIZE && (f = p->files[fd]) != 0 &&
	    !fd_is_reserved(f) && f->type == FD_INODE)
		result = 1;
	intr_restore(enabled);
	return result;
}

// New syscalls are admitted by default. A syscall may bypass the block-I/O
// governor only when its implementation is known not to reach the filesystem.
static int syscall_may_issue_block_io(int id, uint64 *args)
{
	switch (id) {
	case SYS_read:
	case SYS_write:
	case SYS_close:
		return syscall_fd_uses_disk(args[0]);
	case SYS_sched_yield:
	case SYS_gettimeofday:
	case SYS_getpid:
	case SYS_getppid:
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
	case SYS_io_policy_info:
	case SYS_agent_scope_delegate_fd:
	case SYS_agent_workflow_close:
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
	case SYS_agent_watch:
	case SYS_agent_unwatch:
	case SYS_agent_wait:
	case SYS_agent_wait_cancel:
	case SYS_agent_heartbeat:
	case SYS_agent_wake:
	case SYS_agent_route_config:
		return 0;
	default:
		return 1;
	}
}

void syscall()
{
	struct trapframe *trapframe = curr_thread()->trapframe;
	int id = trapframe->a7, ret;
	int io_admitted = 0;
	uint64 args[6] = { trapframe->a0, trapframe->a1, trapframe->a2,
			   trapframe->a3, trapframe->a4, trapframe->a5 };
	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	kernel_work_begin();
	if (syscall_may_issue_block_io(id, args)) {
		if (bio_request_begin_current() < 0) {
			ret = -1;
			goto syscall_out;
		}
		io_admitted = 1;
	}
	if (id >= 0 && id < SYSCALL_COUNT_MAX)
		curr_proc()->syscall_count[id]++;
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d args = [%x, %x, %x, %x, %x, %x]", id,
		       args[0], args[1], args[2], args[3], args[4], args[5]);
	}
	switch (id) {
	case SYS_write:
		ret = sys_write(args[0], args[1], args[2]);
		break;
	case SYS_read:
		ret = sys_read(args[0], args[1], args[2]);
		break;
	case SYS_openat:
		ret = sys_openat(args[0], args[1], args[2]);
		break;
	case SYS_unlinkat:
		ret = sys_unlinkat(args[0], args[1], args[2]);
		break;
	case SYS_close:
		ret = sys_close(args[0]);
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
	if (io_admitted)
		(void)bio_request_end_current(1);
	kernel_work_end();
	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	curr_thread()->trapframe->a0 = ret;
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d ret %d", id, ret);
	}
}
