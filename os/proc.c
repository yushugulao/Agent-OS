#include "proc.h"
#include "defs.h"
#include "loader.h"
#include "trap.h"
#include "vm.h"
#include "queue.h"

struct proc pool[NPROC];
__attribute__((aligned(16))) char kstack[NPROC][NTHREAD][KSTACK_SIZE];
__attribute__((aligned(4096))) char trapframe[NPROC][NTHREAD][TRAP_PAGE_SIZE];

extern char boot_stack_top[];
struct thread *current_thread;
struct thread idle;
struct queue task_queue;
static int scheduler_retained[QUEUE_SIZE];
static int scheduler_agent_hint;

int procid()
{
	return curr_proc()->pid;
}

int threadid()
{
	return curr_thread()->tid;
}

int cpuid()
{
	return 0;
}

struct proc *curr_proc()
{
	return current_thread->process;
}

struct thread *curr_thread()
{
	return current_thread;
}

// initialize the proc table at boot time.
void proc_init()
{
	struct proc *p;
	for (p = pool; p < &pool[NPROC]; p++) {
		p->state = P_UNUSED;
		for (int tid = 0; tid < NTHREAD; ++tid) {
			struct thread *t = &p->threads[tid];
			t->state = T_UNUSED;
		}
	}
	idle.kstack = (uint64)boot_stack_top;
	current_thread = &idle;
	// for procid() and threadid()
	idle.process = pool;
	idle.tid = -1;
	init_queue(&task_queue, QUEUE_SIZE, process_queue_data);
}

int allocpid()
{
	static int PID = 1;
	return PID++;
}

int alloctid(const struct proc *process)
{
	for (int i = 0; i < NTHREAD; ++i) {
		if (process->threads[i].state == T_UNUSED)
			return i;
	}
	return -1;
}

// get task by unique task id
struct thread *id_to_task(int index)
{
	if (index < 0) {
		return NULL;
	}
	int pool_id = index / NTHREAD;
	int tid = index % NTHREAD;
	struct thread *t = &pool[pool_id].threads[tid];
	return t;
}

// ncode unique task id for each thread
int task_to_id(struct thread *t)
{
	int pool_id = t->process - pool;
	int task_id = pool_id * NTHREAD + t->tid;
	return task_id;
}

struct thread *fetch_task()
{
	int index = pop_queue(&task_queue);
	struct thread *t = id_to_task(index);
	if (t == NULL) {
		debugf("No task to fetch\n");
		return t;
	}
	int tid = t->tid;
	int pid = t->process->pid;
	tracef("fetch index %d(pid=%d, tid=%d, addr=%p) from task queue", index,
	       pid, tid, (uint64)t);
	return t;
}

static struct thread *fetch_best_task()
{
	int best = -1;
	int retained_count = 0;
	int saw_agent = 0;
	int index;
	struct thread *candidate;
	struct thread *best_task;

	for (;;) {
		index = pop_queue(&task_queue);
		if (index < 0)
			break;
		candidate = id_to_task(index);
		if (candidate == NULL || candidate->state != RUNNABLE)
			continue;
		if (candidate->process && candidate->process->is_agent)
			saw_agent = 1;
		if (best < 0 ||
		    agent_sched_better(candidate, id_to_task(best))) {
			if (best >= 0 && retained_count < QUEUE_SIZE)
				scheduler_retained[retained_count++] = best;
			best = index;
		} else if (retained_count < QUEUE_SIZE) {
			scheduler_retained[retained_count++] = index;
		}
	}
	for (int i = 0; i < retained_count; i++)
		push_queue(&task_queue, scheduler_retained[i]);
	if (!saw_agent)
		scheduler_agent_hint = 0;
	if (best < 0)
		return 0;
	best_task = id_to_task(best);
	if (best_task) {
		tracef("fetch best index %d(pid=%d, tid=%d, addr=%p)", best,
		       best_task->process->pid, best_task->tid,
		       (uint64)best_task);
	}
	return best_task;
}

void add_task(struct thread *t)
{
	int task_id = task_to_id(t);
	int pid = t->process->pid;
	agent_sched_on_enqueue(t);
	if (t->process && t->process->is_agent)
		scheduler_agent_hint = 1;
	push_queue(&task_queue, task_id);
	tracef("add index %d(pid=%d, tid=%d, addr=%p) to task queue", task_id,
	       pid, t->tid, (uint64)t);
}

// Look in the process table for an UNUSED proc.
// If found, initialize state required to run in the kernel.
// If there are no free procs, or a memory allocation fails, return 0.
struct proc *allocproc()
{
	struct proc *p;
	for (p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED) {
			goto found;
		}
	}
	return 0;

found:
	// init proc
	p->pid = allocpid();
	p->state = P_USED;
	p->max_page = 0;
	p->parent = NULL;
	p->exit_code = 0;
	p->pagetable = uvmcreate();
	if (p->pagetable == 0) {
		p->state = P_UNUSED;
		return 0;
	}
	memset((void *)p->files, 0, sizeof(struct file *) * FD_BUFFER_SIZE);
	p->next_mutex_id = 0;
	p->next_semaphore_id = 0;
	p->next_condvar_id = 0;
	memset(p->syscall_count, 0, sizeof(p->syscall_count));
	memset(p->mail_payload, 0, sizeof(p->mail_payload));
	memset(p->mail_len, 0, sizeof(p->mail_len));
	memset(p->mail_from, 0, sizeof(p->mail_from));
	p->mail_head = 0;
	p->mail_tail = 0;
	p->mail_count = 0;
	agent_clear_metadata(p);
	return p;
}

static void wake_proc_threads(struct proc *p)
{
	for (int i = 0; i < NTHREAD; i++) {
		struct thread *t = &p->threads[i];
		if (t->state == SLEEPING) {
			t->state = RUNNABLE;
			add_task(t);
		}
	}
}

inline uint64 get_thread_trapframe_va(int tid)
{
	return TRAPFRAME - tid * TRAP_PAGE_SIZE;
}

struct trapframe *proc_trapframe(struct proc *p, int tid)
{
	if (p < pool || p >= &pool[NPROC] || tid < 0 || tid >= NTHREAD)
		return 0;
	return (struct trapframe *)trapframe[p - pool][tid];
}

inline uint64 get_thread_ustack_base_va(struct thread *t)
{
	return t->process->ustack_base + t->tid * USTACK_SIZE;
}

int allocthread(struct proc *p, uint64 entry, int alloc_user_res)
{
	int tid;
	struct thread *t;
	uint64 old_max_page = p->max_page;
	for (tid = 0; tid < NTHREAD; ++tid) {
		t = &p->threads[tid];
		if (t->state == T_UNUSED) {
			goto found;
		}
	}
	return -1;

found:
	t->tid = tid;
	t->state = T_USED;
	t->process = p;
	t->exit_code = 0;
	// kernel stack
	t->kstack = (uint64)kstack[p - pool][tid];
	// don't clear kstack now for exec()
	// memset((void *)t->kstack, 0, KSTACK_SIZE);
	// user stack
	t->ustack = get_thread_ustack_base_va(t);
	if (alloc_user_res != 0) {
		if (uvmmap(p->pagetable, t->ustack, USTACK_SIZE / PAGE_SIZE,
			   PTE_U | PTE_R | PTE_W) < 0) {
			t->state = T_UNUSED;
			t->tid = -1;
			return -1;
		}
		p->max_page =
			MAX(p->max_page,
			    PGROUNDUP(t->ustack + USTACK_SIZE - 1) / PAGE_SIZE);
	}
	// trap frame
	t->trapframe = proc_trapframe(p, tid);
	memset((void *)t->trapframe, 0, TRAP_PAGE_SIZE);
	if (mappages(p->pagetable, get_thread_trapframe_va(tid), TRAP_PAGE_SIZE,
		     (uint64)t->trapframe, PTE_R | PTE_W) < 0) {
		if (alloc_user_res != 0)
			uvmunmap(p->pagetable, t->ustack,
				 USTACK_SIZE / PAGE_SIZE, 1);
		p->max_page = old_max_page;
		t->state = T_UNUSED;
		t->tid = -1;
		return -1;
	}
	t->trapframe->sp = t->ustack + USTACK_SIZE;
	t->trapframe->epc = entry;
	//task context
	memset(&t->context, 0, sizeof(t->context));
	t->context.ra = (uint64)usertrapret;
	t->context.sp = t->kstack + KSTACK_SIZE;
	// we do not add thread to scheduler immediately
	debugf("allocthread p: %d, o: %d, t: %d, e: %p, sp: %p, spp: %p",
	       p->pid, (p - pool), t->tid, entry, t->ustack,
	       walkaddr(p->pagetable, t->ustack));
	return tid;
}

int init_stdio(struct proc *p)
{
	for (int i = 0; i < 3; i++) {
		if (p->files[i] != NULL)
			goto fail;
		p->files[i] = stdio_init(i);
		if (p->files[i] == 0)
			goto fail;
	}
	return 0;

fail:
	for (int i = 0; i < 3; i++) {
		if (p->files[i] != 0) {
			fileclose(p->files[i]);
			p->files[i] = 0;
		}
	}
	return -1;
}

// Scheduler never returns.  It loops, doing:
//  - choose a process to run.
//  - swtch to start running that process.
//  - eventually that process transfers control
//    via swtch back to the scheduler.
void scheduler()
{
	struct thread *t;
	for (;;) {
		agent_background_maintain();
		t = scheduler_agent_hint ? fetch_best_task() : fetch_task();
		if (t == NULL) {
			int live = 0;
			for (struct proc *p = pool; p < &pool[NPROC]; p++) {
				if (p->state != P_USED)
					continue;
				for (int i = 0; i < NTHREAD; i++) {
					if (p->threads[i].state == SLEEPING ||
					    p->threads[i].state == RUNNABLE ||
					    p->threads[i].state == RUNNING) {
						live = 1;
						break;
					}
				}
				if (live)
					break;
			}
			if (live) {
				set_kerneltrap();
				intr_on();
				asm volatile("wfi");
				continue;
			}
			infof("all app are over!");
			shutdown();
			for (;;)
				;
		}
		// throw out freed threads
		if (t->state != RUNNABLE) {
			warnf("not RUNNABLE", t->process->pid, t->tid);
			continue;
		}
		tracef("swtich to proc %d, thread %d", t->process->pid, t->tid);
		agent_sched_on_dispatch(t);
		t->state = RUNNING;
		current_thread = t;
		swtch(&idle.context, &t->context);
	}
}

// Switch to scheduler.  Must hold only p->lock
// and have changed proc->state. Saves and restores
// intena because intena is a property of this
// kernel thread, not this CPU. It should
// be proc->intena and proc->noff, but that would
// break in the few places where a lock is held but
// there's no process.
void sched()
{
	struct thread *t = curr_thread();
	if (t->state == RUNNING)
		panic("sched running");
	swtch(&t->context, &idle.context);
}

// Give up the CPU for one scheduling round.
void yield()
{
	agent_sched_on_yield(current_thread);
	current_thread->state = RUNNABLE;
	add_task(current_thread);
	sched();
}

// Free a process's page table, and free the
// physical memory it refers to.
void freepagetable(pagetable_t pagetable, uint64 max_page)
{
	uvmunmap(pagetable, TRAMPOLINE, 1, 0);
	uvmfree(pagetable, max_page);
}

void proc_install_user_image(struct proc *p, struct user_image *image,
			     struct trapframe *staged, int running)
{
	pagetable_t old_pagetable = p->pagetable;
	uint64 old_max_page = p->max_page;
	struct thread *main_thread;

	p->pagetable = image->pagetable;
	p->max_page = image->max_page;
	p->ustack_base = image->ustack_base;
	image->pagetable = 0;

	for (int tid = 0; tid < NTHREAD; tid++) {
		p->threads[tid].state = T_UNUSED;
		p->threads[tid].tid = -1;
	}
	main_thread = &p->threads[0];
	main_thread->tid = 0;
	main_thread->state = running ? RUNNING : T_USED;
	main_thread->process = p;
	main_thread->exit_code = 0;
	main_thread->kstack = (uint64)kstack[p - pool][0];
	main_thread->ustack = p->ustack_base;
	main_thread->trapframe = proc_trapframe(p, 0);
	memmove(main_thread->trapframe, staged, sizeof(*staged));
	if (!running) {
		memset(&main_thread->context, 0, sizeof(main_thread->context));
		main_thread->context.ra = (uint64)usertrapret;
		main_thread->context.sp = main_thread->kstack + KSTACK_SIZE;
	}

	if (old_pagetable != 0) {
		for (int tid = 0; tid < NTHREAD; tid++)
			uvmunmap(old_pagetable, get_thread_trapframe_va(tid), 1,
				 0);
		agent_unmap_exec_context(p, old_pagetable);
		uvmunmap(old_pagetable, TRAMPOLINE, 1, 0);
		uvmfree(old_pagetable, old_max_page);
	}
	memset(image, 0, sizeof(*image));
}

void freethread(struct thread *t)
{
	pagetable_t pt = t->process->pagetable;
	// fill with junk
	memset((void *)t->trapframe, 6, TRAP_PAGE_SIZE);
	memset(&t->context, 6, sizeof(t->context));
	uvmunmap(pt, get_thread_trapframe_va(t->tid), 1, 0);
	uvmunmap(pt, get_thread_ustack_base_va(t), USTACK_SIZE / PAGE_SIZE, 1);
}

void freeproc(struct proc *p)
{
	for (int tid = 0; tid < NTHREAD; ++tid) {
		struct thread *t = &p->threads[tid];
		if (t->state != T_UNUSED && t->state != EXITED) {
			freethread(t);
		}
		t->state = T_UNUSED;
	}
	if (p->is_agent)
		agent_free_proc_context(p);
	else
		agent_clear_metadata(p);
	if (p->pagetable)
		freepagetable(p->pagetable, p->max_page);
	p->pagetable = 0;
	p->max_page = 0;
	p->ustack_base = 0;
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] != NULL) {
			fileclose(p->files[i]);
			p->files[i] = NULL;
		}
	}
	p->state = P_UNUSED;
}

static int fork_common(int make_agent, int agent_role)
{
	struct proc *np;
	struct proc *p = curr_proc();
	int i;
	// Allocate process.
	if ((np = allocproc()) == 0) {
		return -1;
	}
	// Copy user memory from parent to child.
	if (uvmcopy(p->pagetable, np->pagetable, p->max_page) < 0) {
		freeproc(np);
		return -1;
	}
	np->max_page = p->max_page;
	np->ustack_base = p->ustack_base;
	// Copy file table to new proc
	for (i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] != NULL) {
			// uCore teaching runtime shares the file object reference.
			p->files[i]->ref++;
			np->files[i] = p->files[i];
		}
	}
	memset(np->syscall_count, 0, sizeof(np->syscall_count));
	memset(np->mail_payload, 0, sizeof(np->mail_payload));
	memset(np->mail_len, 0, sizeof(np->mail_len));
	memset(np->mail_from, 0, sizeof(np->mail_from));
	np->mail_head = 0;
	np->mail_tail = 0;
	np->mail_count = 0;

	np->parent = p;
	// currently only copy main thread
	int tid = allocthread(np, 0, 0);
	if (tid < 0) {
		freeproc(np);
		return -1;
	}
	struct thread *nt = &np->threads[tid], *t = &p->threads[0];
	if (make_agent && agent_make_role(np, agent_role) < 0) {
		freeproc(np);
		return -1;
	}
	// copy saved user registers.
	*(nt->trapframe) = *(t->trapframe);
	// Cause fork to return 0 in the child.
	nt->trapframe->a0 = 0;
	nt->state = RUNNABLE;
	add_task(nt);
	return np->pid;
}

int fork()
{
	return fork_common(0, AGENT_ROLE_SENTINEL);
}

int agent_create_proc()
{
	return fork_common(1, AGENT_ROLE_SENTINEL);
}

int agent_create_role_proc(int role)
{
	return fork_common(1, role);
}

int push_argv_image(pagetable_t pagetable, uint64 stack_base,
		    struct trapframe *staged, char **argv)
{
	uint64 argc, argp[MAX_ARG_NUM + 1];
	uint64 sp;
	uint64 layout_sp;

	if (pagetable == 0 || staged == 0 || argv == 0 ||
	    stack_base > MAXVA - USTACK_SIZE)
		return -1;
	sp = stack_base + USTACK_SIZE;
	layout_sp = sp;
	// Validate the complete layout before modifying the new user stack.
	for (argc = 0; argv[argc]; argc++) {
		uint64 n;

		if (argc >= MAX_ARG_NUM)
			return -1;
		n = strlen(argv[argc]) + 1;
		if (n > layout_sp - stack_base)
			return -1;
		layout_sp -= n;
		layout_sp -= layout_sp % 16;
	}
	uint64 pointer_bytes = (argc + 1) * sizeof(uint64);
	if (pointer_bytes > layout_sp - stack_base)
		return -1;
	// Push argument strings, prepare rest of stack in ustack.
	for (argc = 0; argv[argc]; argc++) {
		uint64 n = strlen(argv[argc]) + 1;

		sp -= n;
		sp -= sp % 16; // riscv sp must be 16-byte aligned
		if (copyout(pagetable, sp, argv[argc], n) < 0) {
			return -1;
		}
		argp[argc] = sp;
	}
	argp[argc] = 0;
	// push the array of argv[] pointers.
	sp -= (argc + 1) * sizeof(uint64);
	sp -= sp % 16;
	if (copyout(pagetable, sp, (char *)argp,
		    (argc + 1) * sizeof(uint64)) < 0) {
		return -1;
	}
	staged->a1 = sp;
	staged->sp = sp;
	return argc; // this ends up in a0, the first argument to main(argc, argv)
}

int push_argv(struct proc *p, char **argv)
{
	struct thread *t = &p->threads[0];

	return push_argv_image(p->pagetable, t->ustack, t->trapframe, argv);
}

static int exec_thread_ready(struct proc *p)
{
	if (curr_thread() != &p->threads[0] || curr_thread()->state != RUNNING)
		return 0;
	for (int tid = 1; tid < NTHREAD; tid++) {
		if (p->threads[tid].state != T_UNUSED &&
		    p->threads[tid].state != EXITED)
			return 0;
	}
	return 1;
}

int exec(char *path, char **argv)
{
	struct inode *ip;
	struct proc *p = curr_proc();
	struct user_image image;
	struct trapframe staged;
	int argc;

	if (!exec_thread_ready(p))
		return -1;
	infof("exec : %s\n", path);
	if ((ip = namei(path)) == 0) {
		errorf("invalid file name %s\n", path);
		return -1;
	}
	if (user_image_build(ip, (uint64)proc_trapframe(p, 0), &image) < 0) {
		iput(ip);
		return -1;
	}
	iput(ip);
	if (p->is_agent) {
		if (agent_alias_exec_context(p, image.pagetable) < 0) {
			user_image_discard(&image);
			return -1;
		}
		image.shared_base = p->agent_ctx_base;
		image.shared_pages = AGENT_CONTEXT_PAGES;
	}
	memset(&staged, 0, sizeof(staged));
	staged.epc = image.entry;
	argc = push_argv_image(image.pagetable, image.ustack_base, &staged,
			       argv);
	if (argc < 0) {
		user_image_discard(&image);
		return -1;
	}
	proc_install_user_image(p, &image, &staged, 1);
	return argc;
}

int wait(int pid, int *code)
{
	struct proc *np;
	int havekids;
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();

	for (;;) {
		// Scan through table looking for exited children.
		havekids = 0;
		for (np = pool; np < &pool[NPROC]; np++) {
			if (np->state != P_UNUSED && np->parent == p &&
			    (pid <= 0 || np->pid == pid)) {
				havekids = 1;
				if (np->state == ZOMBIE) {
					// Found one.
					np->state = P_UNUSED;
					pid = np->pid;
					*code = np->exit_code;
					memset((void *)np->threads[0].kstack, 9,
					       KSTACK_SIZE);
					return pid;
				}
			}
		}
		if (!havekids) {
			return -1;
		}
		t->state = SLEEPING;
		sched();
	}
}

// Exit the current process.
void exit(int code)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	t->exit_code = code;
	t->state = EXITED;
	int tid = t->tid;
	debugf("thread exit with %d", code);
	freethread(t);
	if (tid == 0) {
		p->exit_code = code;
		freeproc(p);
		debugf("proc exit");
		if (p->parent != NULL) {
			// Parent should `wait`
			p->state = ZOMBIE;
			wake_proc_threads(p->parent);
		}
		// Set the `parent` of all children to NULL
		struct proc *np;
		for (np = pool; np < &pool[NPROC]; np++) {
			if (np->parent == p) {
				np->parent = NULL;
			}
		}
	}
	sched();
}

int fdalloc(struct file *f)
{
	debugf("debugf f = %p, type = %d", f, f->type);
	struct proc *p = curr_proc();
	for (int i = 0; i < FD_BUFFER_SIZE; ++i) {
		if (p->files[i] == NULL) {
			p->files[i] = f;
			debugf("debugf fd = %d, f = %p", i, p->files[i]);
			return i;
		}
	}
	return -1;
}
