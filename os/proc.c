#include "proc.h"
#include "defs.h"
#include "exec_policy.h"
#include "kernel_work.h"
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

enum proc_admission {
	PROC_ADMIT_BOOT,
	PROC_ADMIT_NORMAL,
	PROC_ADMIT_WORKFLOW,
	PROC_ADMIT_AGENT,
	PROC_ADMIT_WORKER,
};

struct proc_resource_domain {
	int used;
	int live;
	int ordinary_live;
	int reserved_live;
	int ordinary_file_slots;
	int reserved_file_slots;
};

static struct proc_resource_domain
	proc_resource_domains[PROC_RESOURCE_DOMAIN_CAP];
static int proc_resource_ordinary_live;
static int proc_resource_reserved_live;
static int proc_file_slots_total;
static int proc_file_slots_ordinary;
static int proc_file_slots_reserved;
static uint proc_scope_next_id;
static int proc_scope_id_exhausted;

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
_Static_assert(PROC_RESERVED_DOMAIN_CAP == VFS_SCOPE_MAX_ACTIVE,
	       "process and workflow reserve partitions must agree");
_Static_assert(PROC_RESERVED_SLOTS % PROC_RESERVED_DOMAIN_CAP == 0,
	       "reserved slots must partition across workflows");
#define PROC_WORKFLOW_RESERVED_WORKERS 2
_Static_assert(PROC_RESOURCE_DOMAIN_RESERVED_LIMIT >=
	       1 + AGENT_ROLE_ARTIFACT + PROC_WORKFLOW_RESERVED_WORKERS,
	       "workflow reserve must fit bootstrap, all roles, and workers");

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

// A VM snapshot may yield between committed pages. While it is active, the
// scheduler keeps sibling threads parked so the source page table cannot
// change underneath the snapshot; unrelated processes remain schedulable.
int proc_vm_snapshot_begin(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled;
	int result = -1;

	if (p == 0 || t == 0 || t == &idle || t->process != p || t->tid < 0 ||
	    t->tid >= NTHREAD)
		return -1;
	enabled = intr_save();
	if (!p->exit_requested && p->vm_snapshot_depth == 0) {
		p->vm_snapshot_owner_tid = t->tid;
		p->vm_snapshot_depth = 1;
		result = 0;
	} else if (!p->exit_requested &&
		   p->vm_snapshot_owner_tid == t->tid &&
		   p->vm_snapshot_depth != (uint)-1) {
		p->vm_snapshot_depth++;
		result = 0;
	}
	intr_restore(enabled);
	return result;
}

void proc_vm_snapshot_end(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (p == 0 || t == 0 || t == &idle || t->process != p ||
	    p->vm_snapshot_depth == 0 || p->vm_snapshot_owner_tid != t->tid)
		panic("vm snapshot owner");
	p->vm_snapshot_depth--;
	if (p->vm_snapshot_depth == 0)
		p->vm_snapshot_owner_tid = -1;
	intr_restore(enabled);
}

static int proc_vm_snapshot_schedulable(const struct thread *t)
{
	const struct proc *p;

	if (t == 0 || (p = t->process) == 0)
		return 0;
	return p->vm_snapshot_depth == 0 ||
	       p->vm_snapshot_owner_tid == t->tid;
}

static void proc_resource_domain_clear(struct proc_resource_domain *domain)
{
	domain->used = 0;
	domain->live = 0;
	domain->ordinary_live = 0;
	domain->reserved_live = 0;
	domain->ordinary_file_slots = 0;
	domain->reserved_file_slots = 0;
}

static void proc_resource_init(void)
{
	for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++)
		proc_resource_domain_clear(&proc_resource_domains[i]);
	proc_resource_ordinary_live = 0;
	proc_resource_reserved_live = 0;
	proc_file_slots_total = 0;
	proc_file_slots_ordinary = 0;
	proc_file_slots_reserved = 0;
	proc_scope_next_id = VFS_SCOPE_FIRST_DYNAMIC;
	proc_scope_id_exhausted = 0;
}

// The file system raises this floor after scanning persistent workflow owners.
// Scope identifiers never wrap or get reused while their objects may exist.
void proc_scope_set_id_floor(uint floor)
{
	int enabled = intr_save();

	if (floor == FS_OWNER_NONE || floor >= FS_OWNER_SCOPE_FLAG) {
		proc_scope_id_exhausted = 1;
	} else {
		if (floor < VFS_SCOPE_FIRST_DYNAMIC)
			floor = VFS_SCOPE_FIRST_DYNAMIC;
		if (!proc_scope_id_exhausted && floor > proc_scope_next_id)
			proc_scope_next_id = floor;
	}
	intr_restore(enabled);
}

static uint proc_scope_alloc_id(void)
{
	uint scope_id;

	if (proc_scope_id_exhausted ||
	    proc_scope_next_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    proc_scope_next_id >= FS_OWNER_SCOPE_FLAG)
		return FS_OWNER_NONE;
	scope_id = proc_scope_next_id++;
	if (proc_scope_next_id >= FS_OWNER_SCOPE_FLAG)
		proc_scope_id_exhausted = 1;
	return scope_id;
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
	uint storage_principal = FS_OWNER_NONE;

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
		} else if (admission == PROC_ADMIT_WORKFLOW) {
			if (!parent->resource_slot_reserved ||
			    !parent->resource_domain_admin)
				goto out;
			new_domain = 1;
		} else {
			// Every process launched inside a workflow shares one immutable
			// resource domain. Agent creation authority must not mint fresh
			// process or PUBLIC-storage quota domains.
			domain_id = parent->resource_domain_id;
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
	} else {
		domain = &proc_resource_domains[domain_id];
		if ((reserved && domain->reserved_live >=
				 PROC_RESOURCE_DOMAIN_RESERVED_LIMIT) ||
		    (!reserved && domain->ordinary_live >=
				  PROC_RESOURCE_DOMAIN_LIMIT))
			goto out;
	}
	for (p = pool; p < &pool[NPROC]; p++)
		if (p->state == P_UNUSED)
			break;
	if (p == &pool[NPROC]) {
		p = 0;
		goto out;
	}
	if (admission == PROC_ADMIT_BOOT || admission == PROC_ADMIT_WORKFLOW) {
		storage_principal = proc_scope_alloc_id();
		if (storage_principal == FS_OWNER_NONE) {
			p = 0;
			goto out;
		}
	} else if (admission == PROC_ADMIT_NORMAL) {
		storage_principal = FS_OWNER_PUBLIC;
	} else {
		storage_principal = parent->storage_principal_id;
		if (storage_principal < VFS_SCOPE_FIRST_DYNAMIC ||
		    storage_principal >= FS_OWNER_SCOPE_FLAG) {
			p = 0;
			goto out;
		}
	}
	if (new_domain) {
		domain = &proc_resource_domains[domain_id];
		proc_resource_domain_clear(domain);
		domain->used = 1;
	}
	domain = &proc_resource_domains[domain_id];
	domain->live++;
	if (reserved) {
		domain->reserved_live++;
		proc_resource_reserved_live++;
	} else {
		domain->ordinary_live++;
		proc_resource_ordinary_live++;
	}
	p->resource_domain_id = domain_id;
	p->storage_principal_id = storage_principal;
	p->resource_slot_reserved = reserved;
	p->resource_domain_admin = admission == PROC_ADMIT_BOOT;
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
		if (proc_resource_reserved_live <= 0 ||
		    domain->reserved_live <= 0)
			panic("process reserve count invariant");
		domain->reserved_live--;
		proc_resource_reserved_live--;
	} else {
		if (proc_resource_ordinary_live <= 0 ||
		    domain->ordinary_live <= 0)
			panic("process ordinary count invariant");
		domain->ordinary_live--;
		proc_resource_ordinary_live--;
	}
	domain->live--;
	if (domain->live == 0 && domain->ordinary_file_slots == 0 &&
	    domain->reserved_file_slots == 0)
		proc_resource_domain_clear(domain);
	p->resource_domain_id = -1;
	p->storage_principal_id = FS_OWNER_NONE;
	p->resource_slot_reserved = 0;
	p->resource_domain_admin = 0;
out:
	intr_restore(enabled);
}

// Charge one unique open-file object to its creator's immutable admission
// class. References inherited by fork or held by a blocking syscall share this
// charge; the final fileclose refunds it.
int proc_file_slot_reserve(struct proc *owner, int *domain_id,
			   int *reserved)
{
	struct proc_resource_domain *domain;
	int enabled = intr_save();
	int result = -1;
	int id;
	int use_reserve;

	if (owner == 0 || domain_id == 0 || reserved == 0 ||
	    owner->state != P_USED)
		goto out;
	id = owner->resource_domain_id;
	if (id < 0 || id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	domain = &proc_resource_domains[id];
	if (!domain->used || domain->live <= 0 ||
	    proc_file_slots_total >= FILE_RESOURCE_POOL_SIZE)
		goto out;
	use_reserve = owner->resource_slot_reserved != 0;
	if (use_reserve) {
		if (domain->reserved_file_slots >=
		    FILE_RESOURCE_DOMAIN_RESERVED_LIMIT)
			goto out;
		domain->reserved_file_slots++;
		proc_file_slots_reserved++;
	} else {
		if (proc_file_slots_ordinary >= FILE_RESOURCE_ORDINARY_LIMIT ||
		    domain->ordinary_file_slots >=
			    FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT)
			goto out;
		domain->ordinary_file_slots++;
		proc_file_slots_ordinary++;
	}
	proc_file_slots_total++;
	*domain_id = id;
	*reserved = use_reserve;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

void proc_file_slot_release(int domain_id, int reserved)
{
	struct proc_resource_domain *domain;
	int enabled = intr_save();

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		panic("file resource domain invariant");
	domain = &proc_resource_domains[domain_id];
	if (!domain->used || proc_file_slots_total <= 0)
		panic("file resource count invariant");
	if (reserved) {
		if (domain->reserved_file_slots <= 0 ||
		    proc_file_slots_reserved <= 0)
			panic("file reserve count invariant");
		domain->reserved_file_slots--;
		proc_file_slots_reserved--;
	} else {
		if (domain->ordinary_file_slots <= 0 ||
		    proc_file_slots_ordinary <= 0)
			panic("file ordinary count invariant");
		domain->ordinary_file_slots--;
		proc_file_slots_ordinary--;
	}
	proc_file_slots_total--;
	if (domain->live == 0 && domain->ordinary_file_slots == 0 &&
	    domain->reserved_file_slots == 0)
		proc_resource_domain_clear(domain);
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
		p->storage_principal_id = FS_OWNER_NONE;
		p->resource_slot_reserved = 0;
		p->resource_domain_admin = 0;
		p->vm_snapshot_depth = 0;
		p->vm_snapshot_owner_tid = -1;
		child_records_reset(p);
		for (int tid = 0; tid < NTHREAD; ++tid) {
			struct thread *t = &p->threads[tid];
			kernel_work_reset(t);
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
	kernel_work_reset(&idle);
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
	int remaining = task_queue.count;
	int index;
	struct thread *t;

	while (remaining-- > 0) {
		index = pop_queue(&task_queue);
		t = id_to_task(index);
		if (t == 0)
			continue;
		if (t->state != RUNNABLE) {
			t->on_run_queue = 0;
			continue;
		}
		if (!proc_vm_snapshot_schedulable(t)) {
			if (push_queue(&task_queue, index) < 0)
				panic("task queue invariant");
			continue;
		}
		t->on_run_queue = 0;
		int tid = t->tid;
		int pid = t->process->pid;
		intr_restore(enabled);
		tracef("fetch index %d(pid=%d, tid=%d, addr=%p) from task queue",
		       index, pid, tid, (uint64)t);
		return t;
	}
	intr_restore(enabled);
	debugf("No task to fetch\n");
	return 0;
}

static struct thread *fetch_best_task()
{
	int enabled = intr_save();
	int best_agent = -1;
	int first_normal = -1;
	int first_runnable = -1;
	int candidate_count = 0;
	int eligible_count = 0;
	int runnable_agent = 0;
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
		if (candidate->process && candidate->process->is_agent)
			runnable_agent = 1;
		if (!proc_vm_snapshot_schedulable(candidate))
			continue;
		eligible_count++;
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
		scheduler_agent_hint = runnable_agent;
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
	if (forced_fifo || best_task == 0 || eligible_count <= 1)
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
	wait_queue_init(&p->agent_event_waiters, WAIT_REASON_EVENT);
	wait_queue_init(&p->agent_timeline_waiters, WAIT_REASON_TIMELINE);
	p->max_page = 0;
	p->parent = NULL;
	p->parent_record_index = -1;
	p->exit_code = 0;
	p->exit_requested = 0;
	p->exit_owner_tid = -1;
	p->exit_finalizing = 0;
	p->vm_snapshot_depth = 0;
	p->vm_snapshot_owner_tid = -1;
	child_records_reset(p);
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
		proc_resource_release(p);
		p->state = P_UNUSED;
		return 0;
	}
	kernel_stack_reset_slot(p, 0);
	memset((void *)p->files, 0, sizeof(struct file *) * FD_BUFFER_SIZE);
	memset(p->fd_scope_delegate, 0, sizeof(p->fd_scope_delegate));
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
	kernel_work_reset(t);
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
		p->files[i] = stdio_init(i, p);
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
		kernel_work_on_dispatch(t);
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

// Open objects are part of a workflow credential, not ambient POSIX state.
// vfs_proc_reset() is the single revocation boundary for both active and
// pending workflow identities. A successful pending-to-active exec does not
// reset the identity and therefore keeps explicitly delegated bootstrap pipes.
void proc_revoke_vfs_scope_fds(struct proc *p)
{
	struct file *files[FD_BUFFER_SIZE];
	uint scope_id;

	memset(files, 0, sizeof(files));
	if (p == 0)
		return;
	scope_id = p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
			   p->vfs_scope_id : p->vfs_pending_scope_id;
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return;
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		struct file *f = p->files[i];

		p->fd_scope_delegate[i] = 0;
		if (f == 0 || (!fd_is_reserved(f) && f->type == FD_STDIO))
			continue;
		p->files[i] = 0;
		if (!fd_is_reserved(f))
			files[i] = f;
	}
	for (int i = 0; i < FD_BUFFER_SIZE; i++)
		if (files[i] != 0)
			fileclose(files[i]);
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
	if (running && !p->is_agent)
		proc_resource_drop_admin(p);
	image->pagetable = 0;

	for (int tid = 0; tid < NTHREAD; tid++) {
		detach_task(&p->threads[tid]);
		if (!running || tid != 0)
			kernel_work_reset(&p->threads[tid]);
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
	kernel_work_reset(t);
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
		p->fd_scope_delegate[i] = 0;
	}
	for (int i = 0; i < FD_BUFFER_SIZE; i++)
		if (files[i] != 0 && !fd_is_reserved(files[i]))
			fileclose(files[i]);
	if (p->is_agent)
		agent_free_proc_context(p);
	else
		agent_clear_metadata(p);
	if (teardown != 0)
		freethread(teardown);
	wait_queue_init(&p->child_waiters, WAIT_REASON_CHILD);
	wait_queue_init(&p->thread_exit_waiters, WAIT_REASON_THREAD_EXIT);
	wait_queue_init(&p->agent_event_waiters, WAIT_REASON_EVENT);
	wait_queue_init(&p->agent_timeline_waiters, WAIT_REASON_TIMELINE);
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
	p->vm_snapshot_depth = 0;
	p->vm_snapshot_owner_tid = -1;
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

static int fork_common(int make_agent, int agent_role,
		       struct inode *delegated_image, uint64 delegated_caps,
		       enum proc_admission admission,
		       enum vfs_spawn_scope_mode scope_mode)
{
	struct proc *np;
	struct proc *p = curr_proc();
	uchar scope_delegate[FD_BUFFER_SIZE];
	uint parent_scope;
	int boundary_attempt;
	int copy_status;
	int i;

	memset(scope_delegate, 0, sizeof(scope_delegate));
	parent_scope = p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
			       p->vfs_scope_id : p->vfs_pending_scope_id;
	boundary_attempt = parent_scope >= VFS_SCOPE_FIRST_DYNAMIC &&
		(scope_mode == VFS_SPAWN_SCOPE_FRESH ||
		 (scope_mode == VFS_SPAWN_SCOPE_DROP && delegated_image == 0));
	// Delegation tickets authorize one boundary attempt, not one successful
	// fork. Consume them before any allocation failure can leave authority
	// ambient for a later child.
	if (boundary_attempt)
		for (i = 0; i < FD_BUFFER_SIZE; i++) {
			scope_delegate[i] = p->fd_scope_delegate[i];
			p->fd_scope_delegate[i] = 0;
		}
	if (p->exit_requested || !proc_child_has_capacity(p))
		return -1;
	// Allocate process.
	if ((np = allocproc_admit(p, admission)) == 0) {
		return -1;
	}
	// Keep this thread as the process's sole dispatchable thread while the
	// page-by-page snapshot yields to unrelated processes.
	if (proc_vm_snapshot_begin(p) < 0) {
		freeproc(np);
		return -1;
	}
	copy_status = uvmcopy(p->pagetable, np->pagetable, p->max_page);
	if (copy_status == 0) {
		np->max_page = p->max_page;
		np->ustack_base = p->ustack_base;
		np->exec_dev = p->exec_dev;
		np->exec_inum = p->exec_inum;
		np->exec_flags = p->exec_flags;
		np->exec_generation = p->exec_generation;
		np->exec_role_mask = p->exec_role_mask;
		np->exec_layout_version = p->exec_layout_version;
		np->exec_rw_offset = p->exec_rw_offset;
	}
	proc_vm_snapshot_end(p);
	if (copy_status < 0) {
		freeproc(np);
		return -1;
	}
	if (vfs_proc_spawn_scope(p, np, scope_mode) < 0) {
		freeproc(np);
		return -1;
	}
	if (delegated_image != 0 &&
	    vfs_proc_delegate_exec(p, np, delegated_image, delegated_caps) < 0) {
		freeproc(np);
		return -1;
	}
	// A pipe crossing a workflow boundary is an explicit one-shot object
	// delegation. Same-scope workers keep normal POSIX inheritance.
	uint inherited_scope = np->vfs_scope_id != VFS_SCOPE_NONE ?
			       np->vfs_scope_id : np->vfs_pending_scope_id;
	int scope_changed = parent_scope != inherited_scope;
	// Copy file table to new proc
	for (i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] != NULL &&
		    !fd_is_reserved(p->files[i])) {
			int boundary_allowed = p->files[i]->type == FD_STDIO ||
				(p->files[i]->type == FD_PIPE &&
				 scope_delegate[i]);

			if (scope_changed && !boundary_allowed)
				continue;
			// Sharing an existing open-file object does not mint a new
			// resource charge; its creator remains accountable until the
			// final reference closes.
			np->files[i] = filedup(p->files[i]);
			if (np->files[i] == 0) {
				freeproc(np);
				return -1;
			}
		}
	}
	memset(np->syscall_count, 0, sizeof(np->syscall_count));
	memset(np->mail_payload, 0, sizeof(np->mail_payload));
	memset(np->mail_len, 0, sizeof(np->mail_len));
	memset(np->mail_from, 0, sizeof(np->mail_from));
	np->mail_head = 0;
	np->mail_tail = 0;
	np->mail_count = 0;

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

int fork()
{
	return fork_common(0, AGENT_ROLE_SENTINEL, 0, 0,
			   PROC_ADMIT_NORMAL, VFS_SPAWN_SCOPE_DROP);
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
	return fork_common(1, role, 0, 0, PROC_ADMIT_AGENT,
			   VFS_SPAWN_SCOPE_INHERIT);
}

int agent_workflow_create_proc(int role)
{
	struct proc *p = curr_proc();
	int status = agent_authority_check(p, role);

	if (status != AGENT_STATUS_OK)
		return status;
	// Creating a security namespace is a distinct, non-inheritable authority.
	// A role grant lets an Orchestrator populate its own workflow, but it does
	// not let that Agent mint fresh quota and object domains.
	if (p == 0 || p->is_agent || !p->resource_domain_admin ||
	    !exec_policy_process_bootstrap(p))
		return AGENT_STATUS_DENIED;
	return fork_common(1, role, 0, 0, PROC_ADMIT_WORKFLOW,
			   VFS_SPAWN_SCOPE_FRESH);
}

int agent_worker_create_proc(char *path, uint64 requested_caps)
{
	struct inode *ip;
	struct proc *p = curr_proc();
	struct vfs_cred cred;
	int pid;

	if (path == 0 ||
	    (ip = namei_scope(path, VFS_POLICY_WORKFLOW,
			      VFS_SCOPE_SYSTEM)) == 0)
		return -1;
	vfs_cred_from_proc(p, &cred);
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_EXEC)) {
		iput(ip);
		return -1;
	}
	pid = fork_common(0, AGENT_ROLE_SENTINEL, ip, requested_caps,
			  PROC_ADMIT_WORKER, VFS_SPAWN_SCOPE_DROP);
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
		ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,
					VFS_SCOPE_SYSTEM, &lookup_status);
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
			vfs_proc_drop_to_public(p);
		return ip;
	}
	policies[0] = vfs_cred_lookup_policy(cred);
	policies[1] = policies[0] == VFS_POLICY_WORKFLOW ?
			      VFS_POLICY_PUBLIC : VFS_POLICY_WORKFLOW;
	for (int i = 0; i < 2; i++) {
		ip = namei_scope_status(
			path, policies[i],
			policies[i] == VFS_POLICY_WORKFLOW ?
				VFS_SCOPE_SYSTEM : VFS_SCOPE_NONE,
			&lookup_status);
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

int fd_is_reserved(struct file *f)
{
	return f == &fd_reservation;
}

int fdreserve()
{
	struct proc *p = curr_proc();
	int enabled = intr_save();

	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] == 0) {
			p->files[i] = &fd_reservation;
			p->fd_scope_delegate[i] = 0;
			intr_restore(enabled);
			return i;
		}
	}
	intr_restore(enabled);
	return -1;
}

int fdinstall(int fd, struct file *f)
{
	struct proc *p = curr_proc();
	int enabled = intr_save();

	if (fd < 0 || fd >= FD_BUFFER_SIZE || f == 0 ||
	    p->files[fd] != &fd_reservation) {
		intr_restore(enabled);
		return -1;
	}
	p->files[fd] = f;
	p->fd_scope_delegate[fd] = 0;
	intr_restore(enabled);
	return 0;
}

void fdrelease(int fd)
{
	struct proc *p = curr_proc();
	int enabled = intr_save();

	if (fd >= 0 && fd < FD_BUFFER_SIZE &&
	    p->files[fd] == &fd_reservation) {
		p->files[fd] = 0;
		p->fd_scope_delegate[fd] = 0;
	}
	intr_restore(enabled);
}

struct file *fdget(int fd)
{
	struct proc *p = curr_proc();
	struct file *f = 0;
	int enabled = intr_save();

	if (fd >= 0 && fd < FD_BUFFER_SIZE && p != 0)
		f = filedup(p->files[fd]);
	intr_restore(enabled);
	return f;
}

int fdclose(int fd)
{
	struct proc *p = curr_proc();
	struct file *f;
	int enabled = intr_save();

	if (p == 0 || fd < 0 || fd >= FD_BUFFER_SIZE ||
	    (f = p->files[fd]) == 0 || fd_is_reserved(f)) {
		intr_restore(enabled);
		return -1;
	}
	p->files[fd] = 0;
	p->fd_scope_delegate[fd] = 0;
	intr_restore(enabled);
	fileclose(f);
	return 0;
}

int proc_scope_delegate_fd(int fd)
{
	struct proc *p = curr_proc();

	if (p == 0 || fd < 0 || fd >= FD_BUFFER_SIZE ||
	    p->files[fd] == 0 || fd_is_reserved(p->files[fd]) ||
	    p->files[fd]->type != FD_PIPE)
		return AGENT_STATUS_BAD_PARAM;
	p->fd_scope_delegate[fd] = 1;
	return AGENT_STATUS_OK;
}
