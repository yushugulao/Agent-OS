#include "console.h"
#include "agent.h"
#include "defs.h"
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

static int user_can_write(pagetable_t pagetable, uint64 va)
{
	pte_t *pte;

	if (va >= MAXVA)
		return 0;
	pte = walk(pagetable, va, 0);
	if (pte == 0)
		return 0;
	return (*pte & PTE_V) && (*pte & PTE_U) && (*pte & PTE_W);
}

uint64 console_write(uint64 va, uint64 len)
{
	struct proc *p = curr_proc();
	char str[MAX_STR_LEN];
	int size = copyinstr(p->pagetable, str, va, MIN(len, MAX_STR_LEN));
	tracef("write size = %d", size);
	for (int i = 0; i < size; ++i) {
		console_putchar(str[i]);
	}
	return len;
}

uint64 console_read(uint64 va, uint64 len)
{
	struct proc *p = curr_proc();
	char str[MAX_STR_LEN];
	tracef("read size = %d", len);
	for (int i = 0; i < len; ++i) {
		int c;
		do {
			c = consgetc();
		} while (c < 0);
		str[i] = c;
	}
	copyout(p->pagetable, va, str, len);
	return len;
}

uint64 sys_write(int fd, uint64 va, uint64 len)
{
	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	struct proc *p = curr_proc();
	struct file *f = p->files[fd];
	if (f == NULL) {
		errorf("invalid fd %d\n", fd);
		return -1;
	}
	switch (f->type) {
	case FD_STDIO:
		return console_write(va, len);
	case FD_PIPE:
		return pipewrite(f->pipe, va, len);
	case FD_INODE:
		return inodewrite(f, va, len);
	default:
		panic("unknown file type %d\n", f->type);
	}
}

uint64 sys_read(int fd, uint64 va, uint64 len)
{
	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	struct proc *p = curr_proc();
	struct file *f = p->files[fd];
	if (f == NULL) {
		errorf("invalid fd %d\n", fd);
		return -1;
	}
	switch (f->type) {
	case FD_STDIO:
		return console_read(va, len);
	case FD_PIPE:
		return piperead(f->pipe, va, len);
	case FD_INODE:
		return inoderead(f, va, len);
	default:
		panic("unknown file type %d\n", f->type);
	}
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

uint64 sys_gettimeofday(uint64 val, int _tz)
{
	struct proc *p = curr_proc();
	uint64 cycle = get_cycle();
	TimeVal t;
	t.sec = cycle / CPU_FREQ;
	t.usec = (cycle % CPU_FREQ) * 1000000 / CPU_FREQ;
	copyout(p->pagetable, val, (char *)&t, sizeof(TimeVal));
	return 0;
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
		if (!user_can_write(p->pagetable, id))
			return -1;
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

static inline uint64 fetchaddr(pagetable_t pagetable, uint64 va)
{
	uint64 *addr = (uint64 *)useraddr(pagetable, va);
	return *addr;
}

uint64 sys_exec(uint64 path, uint64 uargv)
{
	struct proc *p = curr_proc();
	char name[MAX_STR_LEN];
	copyinstr(p->pagetable, name, path, MAX_STR_LEN);
	uint64 arg;
	static char strpool[MAX_ARG_NUM][MAX_STR_LEN];
	char *argv[MAX_ARG_NUM];
	int i;
	for (i = 0; uargv && (arg = fetchaddr(p->pagetable, uargv));
	     uargv += sizeof(char *), i++) {
		copyinstr(p->pagetable, (char *)strpool[i], arg, MAX_STR_LEN);
		argv[i] = (char *)strpool[i];
	}
	argv[i] = NULL;
	return exec(name, (char **)argv);
}

uint64 sys_wait(int pid, uint64 va)
{
	struct proc *p = curr_proc();
	int *code = (int *)useraddr(p->pagetable, va);
	return wait(pid, code);
}

uint64 sys_pipe(uint64 fdarray)
{
	struct proc *p = curr_proc();
	int fd0 = -1, fd1 = -1;
	struct file *f0, *f1;
	f0 = filealloc();
	f1 = filealloc();
	if (f0 == 0 || f1 == 0)
		goto err0;
	if (pipealloc(f0, f1) < 0)
		goto err0;
	fd0 = fdalloc(f0);
	fd1 = fdalloc(f1);
	if (fd0 < 0 || fd1 < 0)
		goto err1;
	if (copyout(p->pagetable, fdarray, (char *)&fd0, sizeof(fd0)) < 0 ||
	    copyout(p->pagetable, fdarray + sizeof(int), (char *)&fd1,
		    sizeof(fd1)) < 0) {
		goto err1;
	}
	return 0;

err1:
	if (fd0 >= 0)
		p->files[fd0] = 0;
	if (fd1 >= 0)
		p->files[fd1] = 0;
err0:
	if (f0)
		fileclose(f0);
	if (f1)
		fileclose(f1);
	return -1;
}

uint64 sys_openat(uint64 va, uint64 omode, uint64 _flags)
{
	struct proc *p = curr_proc();
	char path[200];
	copyinstr(p->pagetable, path, va, 200);
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
	if (fd < 0 || fd >= FD_BUFFER_SIZE)
		return -1;
	struct proc *p = curr_proc();
	struct file *f = p->files[fd];
	if (f == NULL) {
		errorf("invalid fd %d", fd);
		return -1;
	}
	fileclose(f);
	p->files[fd] = 0;
	return 0;
}

int sys_thread_create(uint64 entry, uint64 arg)
{
	struct proc *p = curr_proc();
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
	if (tid < 0 || tid >= NTHREAD) {
		errorf("unexpected tid %d", tid);
		return -1;
	}
	struct thread *t = &curr_proc()->threads[tid];
	if (t->state == T_UNUSED || tid == curr_thread()->tid) {
		return -1;
	}
	if (t->state != EXITED) {
		return -2;
	}
	memset((void *)t->kstack, 7, KSTACK_SIZE);
	t->tid = -1;
	t->state = T_UNUSED;
	return t->exit_code;
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
	// LAB5: (4-1) You may want to maintain some variables for detect here
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
	// LAB5: (4-1) You may want to maintain some variables for detect
	//       or call your detect algorithm here
	mutex_lock(&curr_proc()->mutex_pool[mutex_id]);
	return 0;
}

int sys_mutex_unlock(int mutex_id)
{
	if (mutex_id < 0 || mutex_id >= curr_proc()->next_mutex_id) {
		errorf("Unexpected mutex id %d", mutex_id);
		return -1;
	}
	// LAB5: (4-1) You may want to maintain some variables for detect here
	mutex_unlock(&curr_proc()->mutex_pool[mutex_id]);
	return 0;
}

int sys_semaphore_create(int res_count)
{
	struct semaphore *s = semaphore_create(res_count);
	if (s == NULL) {
		errorf("fail to create semaphore: out of resource");
		return -1;
	}
	// LAB5: (4-2) You may want to maintain some variables for detect here
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
	// LAB5: (4-2) You may want to maintain some variables for detect here
	semaphore_up(&curr_proc()->semaphore_pool[semaphore_id]);
	return 0;
}

int sys_semaphore_down(int semaphore_id)
{
	if (semaphore_id < 0 ||
	    semaphore_id >= curr_proc()->next_semaphore_id) {
		errorf("Unexpected semaphore id %d", semaphore_id);
		return -1;
	}
	// LAB5: (4-2) You may want to maintain some variables for detect
	//       or call your detect algorithm here
	semaphore_down(&curr_proc()->semaphore_pool[semaphore_id]);
	return 0;
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
	cond_wait(&curr_proc()->condvar_pool[cond_id],
		  &curr_proc()->mutex_pool[mutex_id]);
	return 0;
}

// LAB5: (2) you may need to define function enable_deadlock_detect here

extern char trap_page[];

void syscall()
{
	struct trapframe *trapframe = curr_thread()->trapframe;
	int id = trapframe->a7, ret;
	uint64 args[6] = { trapframe->a0, trapframe->a1, trapframe->a2,
			   trapframe->a3, trapframe->a4, trapframe->a5 };
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
	case SYS_agent_create:
		ret = sys_agent_create();
		break;
	case SYS_agent_create_role:
		ret = sys_agent_create_role(args[0]);
		break;
	case SYS_agent_info:
		ret = sys_agent_info(args[0]);
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
	// LAB5: (2) you may need to add case SYS_enable_deadlock_detect here
	default:
		ret = -1;
		errorf("unknown syscall %d", id);
	}
	curr_thread()->trapframe->a0 = ret;
	if (id != SYS_write && id != SYS_read && id != SYS_sched_yield) {
		debugf("syscall %d ret %d", id, ret);
	}
}
