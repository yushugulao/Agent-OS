#include "proc.h"
#include "defs.h"
#include "loader.h"
#include "trap.h"
#include "vm.h"
#include "queue.h"

struct proc pool[NPROC];
__attribute__((aligned(4096))) char trapframe[NPROC][NTHREAD][TRAP_PAGE_SIZE];

extern char boot_stack_top[];
struct thread *current_thread;
struct thread idle;
struct queue task_queue;
#define TASK_QUEUE_SIZE (NPROC * NTHREAD)
static int process_queue_data[TASK_QUEUE_SIZE];

enum proc_admission {
	PROC_ADMIT_BOOT,
	PROC_ADMIT_NORMAL,
	PROC_ADMIT_AGENT,
	PROC_ADMIT_WORKER,
};

struct proc_resource_domain {
	int used;
	int live;
	uint storage_cookie;
	uint storage_blocks;
	uint storage_inodes;
};

static struct proc_resource_domain
	proc_resource_domains[PROC_RESOURCE_DOMAIN_CAP];
static int proc_resource_ordinary_live;
static int proc_resource_reserved_live;
static uint proc_storage_next_cookie;
static uint proc_storage_block_limit;
static uint proc_storage_inode_limit;

static void proc_reset_thread_slot(struct thread *t);
static void proc_recycle(struct proc *p);

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
_Static_assert(PROC_RESERVED_SLOTS > 0 && PROC_RESERVED_SLOTS < NPROC,
	       "process reserve must leave both resource classes usable");
_Static_assert(PROC_RESOURCE_DOMAIN_LIMIT > 0 &&
	       PROC_RESOURCE_DOMAIN_LIMIT <= PROC_ORDINARY_SLOTS,
	       "resource domain limit must fit the ordinary process pool");

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

int proc_thread_exit_requested(void)
{
	struct thread *t = curr_thread();
	struct proc *p;

	if (t == 0 || t == &idle || (p = t->process) == 0)
		return 0;
	return p->exit_requested && t->tid != p->exit_owner_tid;
}

static void proc_resource_domain_clear(struct proc_resource_domain *domain)
{
	domain->used = 0;
	domain->live = 0;
	domain->storage_cookie = PROC_STORAGE_COOKIE_FREE;
	domain->storage_blocks = 0;
	domain->storage_inodes = 0;
}

static void proc_resource_init(void)
{
	for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++)
		proc_resource_domain_clear(&proc_resource_domains[i]);
	proc_resource_ordinary_live = 0;
	proc_resource_reserved_live = 0;
	proc_storage_next_cookie = PROC_STORAGE_COOKIE_MIN;
	proc_storage_block_limit = 0;
	proc_storage_inode_limit = 0;
}

uint proc_storage_cookie(const struct proc *p)
{
	if (p == 0)
		return PROC_STORAGE_COOKIE_SYSTEM;
	return p->storage_cookie;
}

void proc_storage_set_cookie_floor(uint floor)
{
	int enabled = intr_save();

	if (floor == ~0U)
		panic("storage cookie exhausted");
	if (proc_storage_next_cookie < PROC_STORAGE_COOKIE_MIN)
		proc_storage_next_cookie = PROC_STORAGE_COOKIE_MIN;
	if (proc_storage_next_cookie <= floor)
		proc_storage_next_cookie = floor + 1;
	intr_restore(enabled);
}

void proc_storage_set_limits(uint block_limit, uint inode_limit)
{
	int enabled = intr_save();

	if (block_limit == 0 || inode_limit == 0)
		panic("invalid storage domain limits");
	proc_storage_block_limit = block_limit;
	proc_storage_inode_limit = inode_limit;
	intr_restore(enabled);
}

int proc_storage_reserve(uint cookie, uint blocks, uint inodes)
{
	int enabled = intr_save();
	int result = -1;

	if (blocks == 0 && inodes == 0) {
		result = 0;
		goto out;
	}
	if (cookie == PROC_STORAGE_COOKIE_SYSTEM) {
		result = 0;
		goto out;
	}
	if (cookie < PROC_STORAGE_COOKIE_MIN ||
	    proc_storage_block_limit == 0 || proc_storage_inode_limit == 0)
		goto out;
	for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++) {
		struct proc_resource_domain *domain = &proc_resource_domains[i];

		if (!domain->used || domain->live <= 0 ||
		    domain->storage_cookie != cookie)
			continue;
		if (domain->storage_blocks > proc_storage_block_limit ||
		    domain->storage_inodes > proc_storage_inode_limit ||
		    blocks > proc_storage_block_limit - domain->storage_blocks ||
		    inodes > proc_storage_inode_limit - domain->storage_inodes)
			goto out;
		domain->storage_blocks += blocks;
		domain->storage_inodes += inodes;
		result = 0;
		goto out;
	}
out:
	intr_restore(enabled);
	return result;
}

void proc_storage_release(uint cookie, uint blocks, uint inodes)
{
	int enabled = intr_save();

	if ((blocks == 0 && inodes == 0) ||
	    cookie == PROC_STORAGE_COOKIE_SYSTEM ||
	    cookie < PROC_STORAGE_COOKIE_MIN)
		goto out;
	for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++) {
		struct proc_resource_domain *domain = &proc_resource_domains[i];

		if (!domain->used || domain->live <= 0 ||
		    domain->storage_cookie != cookie)
			continue;
		if (blocks > domain->storage_blocks ||
		    inodes > domain->storage_inodes)
			panic("storage domain count invariant");
		domain->storage_blocks -= blocks;
		domain->storage_inodes -= inodes;
		break;
	}
out:
	intr_restore(enabled);
}

// Reserve a proc slot and charge its immutable resource domain atomically.
// Only a kernel-signed domain admin may create a new domain or use reserve.
static struct proc *proc_resource_reserve(struct proc *parent,
					 enum proc_admission admission)
{
	struct proc_resource_domain *domain;
	struct proc *p = 0;
	int enabled = intr_save();
	int domain_id = -1;
	int new_domain = 0;
	int reserved = admission != PROC_ADMIT_NORMAL;

	if (admission < PROC_ADMIT_BOOT || admission > PROC_ADMIT_WORKER)
		goto out;
	if (admission == PROC_ADMIT_BOOT) {
		if (parent != 0)
			goto out;
		new_domain = 1;
	} else {
		if (parent == 0 || parent->state != P_USED ||
		    parent->resource_domain_id < 0 ||
		    parent->resource_domain_id >= PROC_RESOURCE_DOMAIN_CAP)
			goto out;
		domain = &proc_resource_domains[parent->resource_domain_id];
		if (!domain->used || domain->live <= 0)
			goto out;
		if (admission == PROC_ADMIT_NORMAL) {
			if (parent->resource_domain_admin)
				new_domain = 1;
			else
				domain_id = parent->resource_domain_id;
		} else {
			if (!parent->resource_slot_reserved ||
			    !parent->resource_domain_admin)
				goto out;
			new_domain = 1;
		}
	}
	if (reserved) {
		if (proc_resource_reserved_live >= PROC_RESERVED_SLOTS)
			goto out;
	} else if (proc_resource_ordinary_live >= PROC_ORDINARY_SLOTS) {
		goto out;
	}
	if (new_domain) {
		for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++) {
			if (!proc_resource_domains[i].used) {
				domain_id = i;
				break;
			}
		}
		if (domain_id < 0)
			goto out;
	} else if (proc_resource_domains[domain_id].live >=
		   PROC_RESOURCE_DOMAIN_LIMIT) {
		goto out;
	}
	for (p = pool; p < &pool[NPROC]; p++)
		if (p->state == P_UNUSED)
			break;
	if (p == &pool[NPROC]) {
		p = 0;
		goto out;
	}
	if (new_domain) {
		domain = &proc_resource_domains[domain_id];
		domain->used = 1;
		domain->live = 0;
		if (admission == PROC_ADMIT_BOOT) {
			domain->storage_cookie = PROC_STORAGE_COOKIE_SYSTEM;
		} else {
			if (proc_storage_next_cookie < PROC_STORAGE_COOKIE_MIN ||
			    proc_storage_next_cookie == ~0U) {
				proc_resource_domain_clear(domain);
				p = 0;
				goto out;
			}
			domain->storage_cookie = proc_storage_next_cookie++;
		}
	}
	domain = &proc_resource_domains[domain_id];
	domain->live++;
	if (reserved)
		proc_resource_reserved_live++;
	else
		proc_resource_ordinary_live++;
	p->resource_domain_id = domain_id;
	p->resource_slot_reserved = reserved;
	p->resource_domain_admin = admission == PROC_ADMIT_BOOT;
	p->storage_cookie = domain->storage_cookie;
	p->state = P_USED;
out:
	intr_restore(enabled);
	return p;
}

static void proc_resource_drop_admin(struct proc *p)
{
	int enabled = intr_save();

	if (p != 0)
		p->resource_domain_admin = 0;
	intr_restore(enabled);
}

static void proc_resource_release(struct proc *p)
{
	struct proc_resource_domain *domain;
	int enabled = intr_save();
	int domain_id;

	if (p == 0)
		goto out;
	domain_id = p->resource_domain_id;
	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		panic("process resource domain invariant");
	domain = &proc_resource_domains[domain_id];
	if (!domain->used || domain->live <= 0)
		panic("process resource count invariant");
	if (p->resource_slot_reserved) {
		if (proc_resource_reserved_live <= 0)
			panic("process reserve count invariant");
		proc_resource_reserved_live--;
	} else {
		if (proc_resource_ordinary_live <= 0)
			panic("process ordinary count invariant");
		proc_resource_ordinary_live--;
	}
	domain->live--;
	if (domain->live == 0)
		proc_resource_domain_clear(domain);
	p->resource_domain_id = -1;
	p->resource_slot_reserved = 0;
	p->resource_domain_admin = 0;
	p->storage_cookie = PROC_STORAGE_COOKIE_FREE;
out:
	intr_restore(enabled);
}

static void child_record_clear(struct child_record *record)
{
	record->state = CHILD_FREE;
	record->pid = 0;
	record->exit_code = 0;
	record->child = 0;
	record->exit_sequence = 0;
}

static void child_records_reset(struct proc *p)
{
	for (int i = 0; i < CHILD_RECORD_CAP; i++)
		child_record_clear(&p->child_records[i]);
	p->child_exit_sequence = 0;
}

static int proc_child_has_capacity(struct proc *parent)
{
	int enabled = intr_save();
	int available = 0;

	for (int i = 0; i < CHILD_RECORD_CAP; i++) {
		if (parent->child_records[i].state == CHILD_FREE) {
			available = 1;
			break;
		}
	}
	intr_restore(enabled);
	return available;
}

// Call with interrupts disabled. The pointer check prevents a recycled proc
// slot from ever being mistaken for the original child.
static int proc_child_find_live(struct proc *parent, struct proc *child,
				int hint)
{
	if (hint >= 0 && hint < CHILD_RECORD_CAP) {
		struct child_record *record = &parent->child_records[hint];

		if (record->state == CHILD_LIVE && record->child == child &&
		    record->pid == child->pid)
			return hint;
	}
	for (int i = 0; i < CHILD_RECORD_CAP; i++) {
		struct child_record *record = &parent->child_records[i];

		if (record->state == CHILD_LIVE && record->child == child &&
		    record->pid == child->pid)
			return i;
	}
	return -1;
}

// Bind child ownership only after fork has completed every fallible step.
// The per-parent table is both the wait contract and a private child quota.
static int proc_child_bind(struct proc *parent, struct proc *child)
{
	int enabled = intr_save();
	int slot = -1;

	if (parent == 0 || child == 0 || parent->state != P_USED ||
	    child->state != P_USED || child->parent != 0)
		goto out;
	for (int i = 0; i < CHILD_RECORD_CAP; i++) {
		struct child_record *record = &parent->child_records[i];

		if (record->state != CHILD_FREE)
			continue;
		record->pid = child->pid;
		record->exit_code = 0;
		record->child = child;
		record->exit_sequence = 0;
		child->parent = parent;
		child->parent_record_index = i;
		record->state = CHILD_LIVE;
		slot = i;
		break;
	}
out:
	intr_restore(enabled);
	return slot;
}

// Publish the wait result before recycling the executable proc slot.
static int proc_child_publish_exit(struct proc *child)
{
	struct proc *parent;
	struct child_record *record;
	int enabled = intr_save();
	int slot;
	int published = 0;

	if (child == 0 || (parent = child->parent) == 0)
		goto out;
	slot = proc_child_find_live(parent, child,
				    child->parent_record_index);
	if (slot < 0)
		goto detach;
	record = &parent->child_records[slot];
	record->exit_code = (int)child->exit_code;
	record->child = 0;
	record->exit_sequence = ++parent->child_exit_sequence;
	record->state = CHILD_EXITED;
	published = 1;
detach:
	child->parent = 0;
	child->parent_record_index = -1;
	if (published)
		wait_queue_wake_all(&parent->child_waiters);
out:
	intr_restore(enabled);
	return published;
}

// Roll back an unpublished child relationship without leaving a LIVE record
// pointing at a proc slot that is about to be recycled.
static void proc_child_unbind(struct proc *child)
{
	struct proc *parent;
	int enabled = intr_save();
	int slot;

	if (child == 0 || (parent = child->parent) == 0)
		goto out;
	slot = proc_child_find_live(parent, child,
				    child->parent_record_index);
	if (slot >= 0)
		child_record_clear(&parent->child_records[slot]);
	child->parent = 0;
	child->parent_record_index = -1;
	wait_queue_wake_all(&parent->child_waiters);
out:
	intr_restore(enabled);
}

// Return 1 with a result, 0 while a matching child is live, or -1 if the
// caller has no matching child. Call with interrupts disabled.
static int proc_child_wait_result(struct proc *parent, int pid,
				  int *child_pid, int *exit_code)
{
	int selected = -1;
	uint64 oldest = ~0ULL;

	for (int i = 0; i < CHILD_RECORD_CAP; i++) {
		struct child_record *record = &parent->child_records[i];

		if (record->state != CHILD_EXITED ||
		    (pid > 0 && record->pid != pid))
			continue;
		if (pid > 0 || record->exit_sequence < oldest) {
			selected = i;
			oldest = record->exit_sequence;
			if (pid > 0)
				break;
		}
	}
	if (selected >= 0) {
		struct child_record *record = &parent->child_records[selected];

		*child_pid = record->pid;
		*exit_code = record->exit_code;
		child_record_clear(record);
		return 1;
	}
	for (int i = 0; i < CHILD_RECORD_CAP; i++) {
		struct child_record *record = &parent->child_records[i];

		if (record->state == CHILD_LIVE &&
		    (pid <= 0 || record->pid == pid))
			return 0;
	}
	return -1;
}

// A parent's completion table owns every child relationship. Dropping the
// table on parent exit atomically transfers live children to the kernel.
static void proc_orphan_children(struct proc *parent)
{
	int enabled = intr_save();

	for (int i = 0; i < CHILD_RECORD_CAP; i++) {
		struct child_record *record = &parent->child_records[i];
		struct proc *child = record->child;

		if (record->state == CHILD_LIVE && child != 0 &&
		    child->parent == parent && child->parent_record_index == i) {
			child->parent = 0;
			child->parent_record_index = -1;
		}
		child_record_clear(record);
	}
	parent->child_exit_sequence = 0;
	intr_restore(enabled);
}

// initialize the proc table at boot time.
void proc_init()
{
	struct proc *p;
	proc_resource_init();
	for (p = pool; p < &pool[NPROC]; p++) {
		p->state = P_UNUSED;
		p->parent = 0;
		p->parent_record_index = -1;
		p->resource_domain_id = -1;
		p->resource_slot_reserved = 0;
		p->resource_domain_admin = 0;
		p->storage_cookie = PROC_STORAGE_COOKIE_FREE;
		child_records_reset(p);
		for (int tid = 0; tid < NTHREAD; ++tid) {
			struct thread *t = &p->threads[tid];
			t->state = T_UNUSED;
			t->wait_channel = 0;
			t->wait_next = 0;
			t->wait_reason = WAIT_REASON_NONE;
			t->wait_interrupted = 0;
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
	idle.wait_interrupted = 0;
	idle.on_run_queue = 0;
	init_queue(&task_queue, TASK_QUEUE_SIZE, process_queue_data);
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

static struct proc *allocproc_admit(struct proc *parent,
				    enum proc_admission admission)
{
	struct proc *p = proc_resource_reserve(parent, admission);

	if (p == 0)
		return 0;
	// init proc
	p->pid = allocpid();
	wait_queue_init(&p->child_waiters, WAIT_REASON_CHILD);
	wait_queue_init(&p->thread_exit_waiters, WAIT_REASON_THREAD_EXIT);
	p->max_page = 0;
	p->parent = NULL;
	p->parent_record_index = -1;
	p->exit_code = 0;
	p->exit_requested = 0;
	p->exit_owner_tid = -1;
	p->exit_finalizing = 0;
	child_records_reset(p);
	p->pagetable = uvmcreate();
	if (p->pagetable == 0) {
		proc_resource_release(p);
		p->state = P_UNUSED;
		return 0;
	}
	kernel_stack_reset_slot(p, 0);
	memset((void *)p->files, 0, sizeof(struct file *) * FD_BUFFER_SIZE);
	p->next_mutex_id = 0;
	p->next_semaphore_id = 0;
	p->next_condvar_id = 0;
	// LAB5: (1) you may initialize your new proc variables here
	return p;
}

// Kernel bootstrap allocation is the root of all signed resource domains.
struct proc *allocproc()
{
	return allocproc_admit(0, PROC_ADMIT_BOOT);
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
	uint64 old_max_page;

	if (p == 0 || p->exit_requested)
		return -1;
	old_max_page = p->max_page;
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
	t->wait_interrupted = 0;
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

static void scheduler_finish_dying_thread(struct thread *t)
{
	struct proc *p;
	int tid;

	if (t == 0 || t->state != T_DYING || (p = t->process) == 0)
		return;
	tid = t->tid;
	t->state = EXITED;
	if (tid != p->exit_owner_tid || !p->exit_finalizing) {
		wait_queue_wake_all(&p->thread_exit_waiters);
		return;
	}
	proc_child_publish_exit(p);
	proc_recycle(p);
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
		t = fetch_task();
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
		kernel_stack_check(t);
		t->state = RUNNING;
		current_thread = t;
		swtch(&idle.context, &t->context);
		kernel_stack_check(t);
		scheduler_finish_dying_thread(t);
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
	if (running)
		proc_resource_drop_admin(p);
	image->pagetable = 0;

	for (int tid = 0; tid < NTHREAD; tid++) {
		detach_task(&p->threads[tid]);
		p->threads[tid].state = T_UNUSED;
		p->threads[tid].tid = -1;
		p->threads[tid].wait_channel = 0;
		p->threads[tid].wait_next = 0;
		p->threads[tid].wait_reason = WAIT_REASON_NONE;
		p->threads[tid].wait_interrupted = 0;
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
	t->wait_interrupted = 0;
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

// Release a quiescent process. Published threads must unwind and stop first;
// T_USED slots are safe because they have never entered their kernel stack.
static int proc_release_resources(struct proc *p)
{
	struct thread *teardown = proc_current_teardown_thread(p);
	struct file *files[FD_BUFFER_SIZE];

	for (int tid = 0; tid < NTHREAD; ++tid) {
		struct thread *t = &p->threads[tid];

		if (t == teardown)
			continue;
		if (t->state != T_UNUSED && t->state != T_USED &&
		    t->state != EXITED)
			return -1;
	}
	for (int tid = 0; tid < NTHREAD; ++tid) {
		struct thread *t = &p->threads[tid];

		if (t == teardown)
			continue;
		detach_task(t);
		if (t->state == T_USED)
			freethread(t);
		proc_reset_thread_slot(t);
	}
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		files[i] = p->files[i];
		p->files[i] = 0;
	}
	for (int i = 0; i < FD_BUFFER_SIZE; i++)
		if (files[i] != 0)
			fileclose(files[i]);
	if (teardown != 0)
		freethread(teardown);
	wait_queue_init(&p->child_waiters, WAIT_REASON_CHILD);
	wait_queue_init(&p->thread_exit_waiters, WAIT_REASON_THREAD_EXIT);
	if (p->pagetable)
		freepagetable(p->pagetable, p->max_page);
	p->pagetable = 0;
	p->max_page = 0;
	p->ustack_base = 0;
	return 0;
}

static void proc_reset_slot(struct proc *p)
{
	proc_resource_release(p);
	p->parent = NULL;
	p->parent_record_index = -1;
	p->pid = 0;
	p->exit_code = 0;
	p->exit_requested = 0;
	p->exit_owner_tid = -1;
	p->exit_finalizing = 0;
	child_records_reset(p);
	p->state = P_UNUSED;
}

// Exit credentials live in the parent's child table, so an execution slot can
// be recycled as soon as all kernel stacks and resources have quiesced.
static void proc_recycle(struct proc *p)
{
	if (p == 0)
		return;
	for (int tid = 0; tid < NTHREAD; tid++)
		proc_reset_thread_slot(&p->threads[tid]);
	proc_reset_slot(p);
}

void freeproc(struct proc *p)
{
	proc_child_unbind(p);
	proc_orphan_children(p);
	if (proc_release_resources(p) < 0)
		panic("active process release");
	proc_recycle(p);
}

int fork()
{
	struct proc *np;
	struct proc *p = curr_proc();
	int i;
	if (p->exit_requested || !proc_child_has_capacity(p))
		return -1;
	// Allocate process.
	if ((np = allocproc_admit(p, PROC_ADMIT_NORMAL)) == 0) {
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
			// Preserve the shared file entry reference across fork.
			p->files[i]->ref++;
			np->files[i] = p->files[i];
		}
	}

	// currently only copy main thread
	int tid = allocthread(np, 0, 0);
	if (tid < 0) {
		freeproc(np);
		return -1;
	}
	struct thread *nt = &np->threads[tid], *t = &p->threads[0];
	if (proc_child_bind(p, np) < 0) {
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
	if (p->exit_requested || curr_thread() != &p->threads[0] ||
	    curr_thread()->state != RUNNING)
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
	struct proc *p = curr_proc();
	int child_pid;
	int child_status;
	int state;

	for (;;) {
		int enabled = intr_save();

		state = proc_child_wait_result(p, pid, &child_pid,
					       &child_status);
		if (state > 0) {
			*code = child_status;
			intr_restore(enabled);
			return child_pid;
		}
		if (state < 0) {
			intr_restore(enabled);
			return -1;
		}
		state = wait_queue_sleep(&p->child_waiters);
		intr_restore(enabled);
		if (state < 0)
			return -1;
	}
}

static int proc_siblings_quiescent(struct proc *p, struct thread *owner)
{
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (t == owner || t->state == T_UNUSED || t->state == EXITED)
			continue;
		return 0;
	}
	return 1;
}

static void proc_interrupt_siblings(struct proc *p, struct thread *owner)
{
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (t == owner)
			continue;
		if (t->state == SLEEPING)
			wait_queue_interrupt(t);
		else if (t->state == T_USED) {
			t->state = RUNNABLE;
			add_task(t);
		}
	}
}

static __attribute__((noreturn)) void thread_exit_current(int code)
{
	struct thread *t = curr_thread();

	t->exit_code = code;
	freethread(t);
	t->state = T_DYING;
	sched();
	panic("dead thread resumed");
}

// The main thread coordinates process exit. Siblings are interrupted only at
// blocking points and must unwind their own kernel stacks before shared state
// is released.
void exit(int code)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();

	debugf("thread exit with %d", code);
	if (t->tid != 0)
		thread_exit_current(code);
	p->exit_code = code;
	p->exit_requested = 1;
	p->exit_owner_tid = t->tid;
	for (;;) {
		proc_interrupt_siblings(p, t);
		if (proc_siblings_quiescent(p, t))
			break;
		if (wait_queue_sleep(&p->thread_exit_waiters) < 0)
			yield();
	}
	proc_orphan_children(p);
	if (proc_release_resources(p) < 0)
		panic("active process exit");
	p->exit_finalizing = 1;
	t->exit_code = code;
	t->state = T_DYING;
	sched();
	panic("exited process resumed");
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
