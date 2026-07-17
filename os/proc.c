#include "proc.h"
#include "defs.h"
#include "exec_policy.h"
#include "loader.h"
#include "trap.h"
#include "vm.h"
#include "queue.h"
#include "vfs_security.h"

struct proc pool[NPROC];
__attribute__((aligned(4096))) char trapframe[NPROC][NTHREAD][TRAP_PAGE_SIZE];
static struct file fd_reservation;

extern char boot_stack_top[];
struct thread *current_thread;
struct thread idle;
struct queue task_queue;
#define TASK_QUEUE_SIZE (NPROC * NTHREAD)
static int process_queue_data[TASK_QUEUE_SIZE];
static int scheduler_retained[TASK_QUEUE_SIZE];
static int scheduler_agent_hint;
static uint64 scheduler_agent_burst;
static uint64 scheduler_score_burst;

#define KSTACK_POISON 0xa5
#define KSTACK_CANARY 0x6b737461636b4f53ULL

_Static_assert(KSTACK_SIZE >= 2 * PGSIZE,
	       "kernel stacks need room for calls and interrupt frames");
_Static_assert(KSTACK_SIZE % PGSIZE == 0,
	       "kernel stack size must be page aligned");
_Static_assert(KSTACK_GUARD_SIZE >= PGSIZE,
	       "kernel stack guard must cover at least one page");
_Static_assert(KSTACK_GUARD_SIZE % PGSIZE == 0,
	       "kernel stack guard size must be page aligned");
_Static_assert((uint64)NPROC * NTHREAD * KSTACK_SLOT_SIZE < TRAMPOLINE,
	       "kernel stack virtual region must not wrap");

static uint64 kernel_stack_slot(int proc_index, int tid)
{
	return (uint64)proc_index * NTHREAD + tid;
}

static uint64 kernel_stack_guard(uint64 slot)
{
	return TRAMPOLINE - (slot + 1) * KSTACK_SLOT_SIZE;
}

static uint64 kernel_stack_base(int proc_index, int tid)
{
	return kernel_stack_guard(kernel_stack_slot(proc_index, tid)) +
	       KSTACK_GUARD_SIZE;
}

static uint64 kernel_stack_canary(uint64 slot)
{
	return KSTACK_CANARY ^ (slot * 0x9e3779b97f4a7c15ULL);
}

static void kernel_stack_reset_slot(struct proc *p, int tid)
{
	uint64 slot = kernel_stack_slot(p - pool, tid);
	uint64 *low = (uint64 *)kernel_stack_base(p - pool, tid);
	uint64 canary = kernel_stack_canary(slot);

	memset((void *)low, KSTACK_POISON, KSTACK_SIZE);
	low[0] = canary;
	low[1] = ~canary;
}

void proc_mapstacks(pagetable_t kpgtbl)
{
	for (int proc_index = 0; proc_index < NPROC; proc_index++) {
		for (int tid = 0; tid < NTHREAD; tid++) {
			uint64 slot = kernel_stack_slot(proc_index, tid);
			uint64 guard = kernel_stack_guard(slot);
			uint64 stack = guard + KSTACK_GUARD_SIZE;
			uint64 *low = 0;

			for (uint64 off = 0; off < KSTACK_SIZE; off += PGSIZE) {
				char *mem = kalloc();

				if (mem == 0)
					panic("kernel stack allocation");
				memset(mem, KSTACK_POISON, PGSIZE);
				if (off == 0)
					low = (uint64 *)mem;
				kvmmap(kpgtbl, stack + off, (uint64)mem, PGSIZE,
				       PTE_R | PTE_W);
			}
			low[0] = kernel_stack_canary(slot);
			low[1] = ~low[0];
			for (uint64 off = 0; off < KSTACK_GUARD_SIZE;
			     off += PGSIZE) {
				pte_t *guard_pte = walk(kpgtbl, guard + off, 0);

				if (guard_pte != 0 && (*guard_pte & PTE_V))
					panic("kernel stack guard mapped");
			}
		}
	}
}

void kernel_stack_check(struct thread *t)
{
	struct proc *p;
	uint64 pool_start = (uint64)&pool[0];
	uint64 pool_end = (uint64)&pool[NPROC];
	uint64 thread_address;
	uint64 thread_start;
	uint64 thread_end;
	int proc_index;
	int tid;
	uint64 slot;
	uint64 expected_base;
	uint64 canary;
	uint64 *low;

	if (t == 0 || t == &idle)
		return;
	thread_address = (uint64)t;
	if (thread_address < pool_start || thread_address >= pool_end)
		panic("invalid kernel stack owner");
	proc_index = (thread_address - pool_start) / sizeof(struct proc);
	p = &pool[proc_index];
	thread_start = (uint64)&p->threads[0];
	thread_end = (uint64)&p->threads[NTHREAD];
	if (thread_address < thread_start || thread_address >= thread_end ||
	    (thread_address - thread_start) % sizeof(struct thread) != 0 ||
	    (uint64)t->process != (uint64)p)
		panic("invalid kernel stack owner");
	tid = (thread_address - thread_start) / sizeof(struct thread);
	slot = kernel_stack_slot(proc_index, tid);
	expected_base = kernel_stack_base(proc_index, tid);
	if (t->kstack != expected_base)
		panic("invalid kernel stack address");
	low = (uint64 *)expected_base;
	canary = kernel_stack_canary(slot);
	if (low[0] != canary || low[1] != ~canary)
		panic("kernel stack overflow");
}

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
			t->wait_channel = 0;
			t->wait_next = 0;
			t->wait_reason = WAIT_REASON_NONE;
			t->on_run_queue = 0;
		}
	}
	idle.kstack = (uint64)boot_stack_top;
	current_thread = &idle;
	// for procid() and threadid()
	idle.process = pool;
	idle.tid = -1;
	idle.wait_channel = 0;
	idle.wait_next = 0;
	idle.wait_reason = WAIT_REASON_NONE;
	idle.on_run_queue = 0;
	init_queue(&task_queue, TASK_QUEUE_SIZE, process_queue_data);
	scheduler_agent_hint = 0;
	scheduler_agent_burst = 0;
	scheduler_score_burst = 0;
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
	if (index < 0 || index >= NPROC * NTHREAD) {
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
	int enabled = intr_save();
	int index = pop_queue(&task_queue);
	struct thread *t = id_to_task(index);
	if (t == NULL) {
		intr_restore(enabled);
		debugf("No task to fetch\n");
		return t;
	}
	t->on_run_queue = 0;
	int tid = t->tid;
	int pid = t->process->pid;
	intr_restore(enabled);
	tracef("fetch index %d(pid=%d, tid=%d, addr=%p) from task queue", index,
	       pid, tid, (uint64)t);
	return t;
}

static struct thread *fetch_best_task()
{
	int enabled = intr_save();
	int best_agent = -1;
	int first_normal = -1;
	int first_runnable = -1;
	int candidate_count = 0;
	int selected;
	int forced_fifo = 0;
	int index;
	struct thread *candidate;
	struct thread *best_task;

	for (;;) {
		index = pop_queue(&task_queue);
		if (index < 0)
			break;
		candidate = id_to_task(index);
		if (candidate)
			candidate->on_run_queue = 0;
		if (candidate == NULL || candidate->state != RUNNABLE)
			continue;
		scheduler_retained[candidate_count++] = index;
		if (first_runnable < 0)
			first_runnable = index;
		if (candidate->process && candidate->process->is_agent) {
			if (best_agent < 0 ||
			    agent_sched_better(candidate,
					       id_to_task(best_agent)))
				best_agent = index;
		} else if (first_normal < 0) {
			first_normal = index;
		}
	}
	// Agent scores prioritize urgent work, but a bounded FIFO escape prevents
	// any score combination from starving another runnable thread forever.
	if (best_agent < 0) {
		scheduler_agent_hint = 0;
		selected = first_normal;
	} else if (first_normal >= 0 &&
		   scheduler_agent_burst >= AGENT_SCHED_MAX_AGENT_BURST) {
		selected = first_normal;
	} else if (scheduler_score_burst >=
		   AGENT_SCHED_MAX_AGENT_BURST) {
		selected = first_runnable;
		forced_fifo = 1;
	} else if (first_normal < 0) {
		selected = best_agent;
	} else if (agent_sched_better(id_to_task(best_agent),
				      id_to_task(first_normal))) {
		selected = best_agent;
	} else {
		selected = first_normal;
	}
	for (int i = 0; i < candidate_count; i++) {
		if (scheduler_retained[i] == selected)
			continue;
		candidate = id_to_task(scheduler_retained[i]);
		if (candidate == 0 || candidate->state != RUNNABLE ||
		    candidate->on_run_queue)
			continue;
		if (push_queue(&task_queue, scheduler_retained[i]) < 0)
			panic("task queue invariant");
		candidate->on_run_queue = 1;
	}
	if (selected < 0) {
		scheduler_agent_burst = 0;
		scheduler_score_burst = 0;
		intr_restore(enabled);
		return 0;
	}
	best_task = id_to_task(selected);
	if (forced_fifo || best_task == 0 || candidate_count <= 1)
		scheduler_score_burst = 0;
	else
		scheduler_score_burst++;
	if (best_task && best_task->process && best_task->process->is_agent &&
	    first_normal >= 0)
		scheduler_agent_burst++;
	else
		scheduler_agent_burst = 0;
	if (best_task) {
		tracef("fetch best index %d(pid=%d, tid=%d, addr=%p)", selected,
		       best_task->process->pid, best_task->tid,
		       (uint64)best_task);
	}
	intr_restore(enabled);
	return best_task;
}

void add_task(struct thread *t)
{
	int enabled = intr_save();

	if (t == 0 || t->process == 0 || t->process < pool ||
	    t->process >= &pool[NPROC] ||
	    t->tid < 0 || t->tid >= NTHREAD || t->state != RUNNABLE ||
	    t->on_run_queue) {
		intr_restore(enabled);
		return;
	}
	int task_id = task_to_id(t);
	int pid = t->process->pid;
	agent_sched_on_enqueue(t);
	if (t->process && t->process->is_agent)
		scheduler_agent_hint = 1;
	if (push_queue(&task_queue, task_id) < 0)
		panic("task queue invariant");
	t->on_run_queue = 1;
	intr_restore(enabled);
	tracef("add index %d(pid=%d, tid=%d, addr=%p) to task queue", task_id,
	       pid, t->tid, (uint64)t);
}

static void remove_task(struct thread *t)
{
	int enabled = intr_save();

	if (t == 0 || t->process == 0 || t->process < pool ||
	    t->process >= &pool[NPROC] ||
	    t->tid < 0 || t->tid >= NTHREAD) {
		intr_restore(enabled);
		return;
	}
	remove_queue_value(&task_queue, task_to_id(t));
	t->on_run_queue = 0;
	intr_restore(enabled);
}

static void detach_task(struct thread *t)
{
	int enabled = intr_save();

	wait_queue_cancel(t);
	remove_task(t);
	intr_restore(enabled);
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
	wait_queue_init(&p->child_waiters, WAIT_REASON_CHILD);
	wait_queue_init(&p->agent_event_waiters, WAIT_REASON_EVENT);
	wait_queue_init(&p->agent_timeline_waiters, WAIT_REASON_TIMELINE);
	p->max_page = 0;
	p->parent = NULL;
	p->exit_code = 0;
	p->exec_dev = 0;
	p->exec_inum = 0;
	p->exec_flags = 0;
	p->exec_generation = 0;
	p->exec_role_mask = 0;
	p->exec_layout_version = 0;
	p->exec_rw_offset = 0;
	vfs_proc_reset(p);
	p->pagetable = uvmcreate();
	if (p->pagetable == 0) {
		p->state = P_UNUSED;
		return 0;
	}
	kernel_stack_reset_slot(p, 0);
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
	t->wait_channel = 0;
	t->wait_next = 0;
	t->wait_reason = WAIT_REASON_NONE;
	t->on_run_queue = 0;
	// kernel stack
	t->kstack = kernel_stack_base(p - pool, tid);
	kernel_stack_reset_slot(p, tid);
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
				intr_off();
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
		kernel_stack_check(t);
		t->state = RUNNING;
		current_thread = t;
		swtch(&idle.context, &t->context);
		kernel_stack_check(t);
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
	kernel_stack_check(t);
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

	vfs_proc_install_image(p, image, running);
	p->pagetable = image->pagetable;
	p->max_page = image->max_page;
	p->ustack_base = image->ustack_base;
	p->exec_dev = image->exec_dev;
	p->exec_inum = image->exec_inum;
	p->exec_flags = image->exec_flags;
	p->exec_generation = image->exec_generation;
	p->exec_role_mask = image->exec_role_mask;
	p->exec_layout_version = image->exec_layout_version;
	p->exec_rw_offset = image->exec_rw_offset;
	agent_authority_on_exec(p);
	image->pagetable = 0;

	for (int tid = 0; tid < NTHREAD; tid++) {
		detach_task(&p->threads[tid]);
		p->threads[tid].state = T_UNUSED;
		p->threads[tid].tid = -1;
		p->threads[tid].wait_channel = 0;
		p->threads[tid].wait_next = 0;
		p->threads[tid].wait_reason = WAIT_REASON_NONE;
		p->threads[tid].on_run_queue = 0;
	}
	main_thread = &p->threads[0];
	main_thread->tid = 0;
	main_thread->state = running ? RUNNING : T_USED;
	main_thread->process = p;
	main_thread->exit_code = 0;
	main_thread->kstack = kernel_stack_base(p - pool, 0);
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
	detach_task(t);
	// fill with junk
	memset((void *)t->trapframe, 6, TRAP_PAGE_SIZE);
	memset(&t->context, 6, sizeof(t->context));
	uvmunmap(pt, get_thread_trapframe_va(t->tid), 1, 0);
	uvmunmap(pt, get_thread_ustack_base_va(t), USTACK_SIZE / PAGE_SIZE, 1);
}

static void proc_reset_thread_slot(struct thread *t)
{
	t->state = T_UNUSED;
	t->tid = -1;
	t->wait_channel = 0;
	t->wait_next = 0;
	t->wait_reason = WAIT_REASON_NONE;
	t->on_run_queue = 0;
}

static struct thread *proc_current_teardown_thread(struct proc *p)
{
	struct thread *t = current_thread;

	if (t == NULL || t == &idle || t->process != p || t->tid < 0 ||
	    t->tid >= NTHREAD || t != &p->threads[t->tid])
		return NULL;
	return t;
}

// Release resources owned by a process without changing who must reap it.
// Keep the exiting thread schedulable until every potentially blocking
// release is complete; failed allocations have no current teardown thread.
static void proc_release_resources(struct proc *p)
{
	struct thread *teardown = proc_current_teardown_thread(p);

	for (int tid = 0; tid < NTHREAD; ++tid) {
		struct thread *t = &p->threads[tid];
		detach_task(t);
		if (t->state != T_UNUSED && t->state != EXITED) {
			freethread(t);
		}
		if (t != teardown)
			proc_reset_thread_slot(t);
	}
	if (teardown != NULL) {
		// Teardown is a schedulable kernel phase. Yield once before the
		// potentially long release so ownership changes cannot depend on a
		// particular resource backend blocking.
		yield();
	}
	wait_queue_init(&p->child_waiters, WAIT_REASON_CHILD);
	wait_queue_init(&p->agent_event_waiters, WAIT_REASON_EVENT);
	wait_queue_init(&p->agent_timeline_waiters, WAIT_REASON_TIMELINE);
	if (p->is_agent)
		agent_free_proc_context(p);
	else
		agent_clear_metadata(p);
	if (p->pagetable)
		freepagetable(p->pagetable, p->max_page);
	p->pagetable = 0;
	p->max_page = 0;
	p->ustack_base = 0;
	p->exec_dev = 0;
	p->exec_inum = 0;
	p->exec_flags = 0;
	p->exec_generation = 0;
	p->exec_role_mask = 0;
	p->exec_layout_version = 0;
	p->exec_rw_offset = 0;
	vfs_proc_reset(p);
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] != NULL) {
			if (!fd_is_reserved(p->files[i]))
				fileclose(p->files[i]);
			p->files[i] = NULL;
		}
	}
	if (teardown != NULL)
		proc_reset_thread_slot(teardown);
}

// parent == NULL means the kernel owns the final reap.
static void proc_reset_slot(struct proc *p)
{
	p->parent = NULL;
	p->exit_code = 0;
	p->state = P_UNUSED;
}

// ZOMBIE resources were already released by exit(); reaping only returns the
// process-table slot. Kernel stacks are reset when the slot is allocated again.
static int proc_reap(struct proc *p)
{
	if (p == NULL || p->state != ZOMBIE)
		return -1;
	proc_reset_slot(p);
	return 0;
}

// Transfer every child of an exiting process to the kernel reaper. Live
// children will be reaped on exit; zombies can be reclaimed immediately.
static void proc_orphan_children(struct proc *parent)
{
	struct proc *child;

	for (child = pool; child < &pool[NPROC]; child++) {
		if (child->parent != parent)
			continue;
		child->parent = NULL;
		if (child->state == ZOMBIE)
			proc_reap(child);
	}
}

void freeproc(struct proc *p)
{
	proc_release_resources(p);
	proc_reset_slot(p);
}

static int fork_common(int make_agent, int agent_role,
		       struct inode *delegated_image, uint64 delegated_caps)
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
	np->exec_dev = p->exec_dev;
	np->exec_inum = p->exec_inum;
	np->exec_flags = p->exec_flags;
	np->exec_generation = p->exec_generation;
	np->exec_role_mask = p->exec_role_mask;
	np->exec_layout_version = p->exec_layout_version;
	np->exec_rw_offset = p->exec_rw_offset;
	vfs_proc_fork(p, np, make_agent);
	if (delegated_image != 0 &&
	    vfs_proc_delegate_exec(p, np, delegated_image, delegated_caps) < 0) {
		freeproc(np);
		return -1;
	}
	// Copy file table to new proc
	for (i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] != NULL &&
		    !fd_is_reserved(p->files[i])) {
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
	return fork_common(0, AGENT_ROLE_SENTINEL, 0, 0);
}

int agent_create_proc()
{
	return agent_create_role_proc(AGENT_ROLE_SENTINEL);
}

int agent_create_role_proc(int role)
{
	int status = agent_authority_check(curr_proc(), role);

	if (status != AGENT_STATUS_OK)
		return status;
	return fork_common(1, role, 0, 0);
}

int agent_worker_create_proc(char *path, uint64 requested_caps)
{
	struct inode *ip;
	struct proc *p = curr_proc();
	struct vfs_cred cred;
	int pid;

	if (path == 0 ||
	    (ip = namei_policy(path, VFS_POLICY_WORKFLOW)) == 0)
		return -1;
	vfs_cred_from_proc(p, &cred);
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_EXEC)) {
		iput(ip);
		return -1;
	}
	pid = fork_common(0, AGENT_ROLE_SENTINEL, ip, requested_caps);
	iput(ip);
	return pid;
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

static int proc_exec_inode_usable(struct proc *p, struct inode *ip,
				  const struct vfs_cred *cred)
{
	if (ip == 0)
		return 0;
	ivalid(ip);
	if (!vfs_inode_authorize(ip, cred, VFS_OP_EXEC) ||
	    !exec_policy_inode_layout_valid(ip))
		return 0;
	return !p->is_agent ||
	       exec_policy_inode_allows_role(ip, p->agent_role);
}

static struct inode *proc_exec_lookup(struct proc *p, char *path,
				      const struct vfs_cred *cred)
{
	struct inode *ip;
	uint policies[2];
	int lookup_status;

	if (p->vfs_pending_exec_inum != 0) {
		ip = namei_policy_status(path, VFS_POLICY_WORKFLOW,
					 &lookup_status);
		if (lookup_status != FS_LOOKUP_FOUND)
			return 0;
		if (!proc_exec_inode_usable(p, ip, cred)) {
			if (ip)
				iput(ip);
			return 0;
		}
		if (ip->dev != p->vfs_pending_exec_dev ||
		    ip->inum != p->vfs_pending_exec_inum ||
		    ip->vfs_incarnation !=
			    p->vfs_pending_exec_incarnation)
			vfs_proc_reset(p);
		return ip;
	}
	policies[0] = vfs_cred_lookup_policy(cred);
	policies[1] = policies[0] == VFS_POLICY_WORKFLOW ?
			      VFS_POLICY_PUBLIC : VFS_POLICY_WORKFLOW;
	for (int i = 0; i < 2; i++) {
		ip = namei_policy_status(path, policies[i], &lookup_status);
		if (lookup_status == FS_LOOKUP_ERROR)
			return 0;
		if (proc_exec_inode_usable(p, ip, cred))
			return ip;
		if (ip)
			iput(ip);
	}
	return 0;
}

int exec(char *path, char **argv)
{
	struct inode *ip;
	struct proc *p = curr_proc();
	struct user_image image;
	struct trapframe staged;
	struct vfs_cred cred;
	int argc;

	if (!exec_thread_ready(p))
		return -1;
	infof("exec : %s\n", path);
	vfs_cred_from_proc(p, &cred);
	ip = proc_exec_lookup(p, path, &cred);
	if (ip == 0) {
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

	for (;;) {
		// Scan through table looking for exited children.
		havekids = 0;
		for (np = pool; np < &pool[NPROC]; np++) {
			if (np->state != P_UNUSED && np->parent == p &&
			    (pid <= 0 || np->pid == pid)) {
				havekids = 1;
				if (np->state == ZOMBIE) {
					// Found one.
					pid = np->pid;
					*code = np->exit_code;
					if (proc_reap(np) < 0)
						return -1;
					return pid;
				}
			}
		}
		if (!havekids) {
			return -1;
		}
		if (wait_queue_sleep(&p->child_waiters) < 0)
			return -1;
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
		struct proc *parent;

		p->exit_code = code;
		proc_orphan_children(p);
		proc_release_resources(p);
		p->state = ZOMBIE;
		// Resource release can yield. Sample ownership only after it finishes,
		// because the parent may have exited and orphaned us in the meantime.
		parent = p->parent;
		debugf("proc exit");
		if (parent != NULL) {
			// Parent should `wait`
			wait_queue_wake_all(&parent->child_waiters);
		} else
			proc_reap(p);
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

int fd_is_reserved(struct file *f)
{
	return f == &fd_reservation;
}

int fdreserve()
{
	struct proc *p = curr_proc();

	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] == 0) {
			p->files[i] = &fd_reservation;
			return i;
		}
	}
	return -1;
}

int fdinstall(int fd, struct file *f)
{
	struct proc *p = curr_proc();

	if (fd < 0 || fd >= FD_BUFFER_SIZE || f == 0 ||
	    p->files[fd] != &fd_reservation)
		return -1;
	p->files[fd] = f;
	return 0;
}

void fdrelease(int fd)
{
	struct proc *p = curr_proc();

	if (fd >= 0 && fd < FD_BUFFER_SIZE &&
	    p->files[fd] == &fd_reservation)
		p->files[fd] = 0;
}
