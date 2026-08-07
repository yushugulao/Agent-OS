#include "proc.h"
#include "agent_context.h"
#include "agent_lifecycle.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "fs_epoch.h"
#include "kernel_work.h"
#include "loader.h"
#include "sbi.h"
#include "trap.h"
#include "user_stack_layout.h"
#include "vm.h"
#include "queue.h"
#include "vfs_security.h"
#ifdef WAIT_ATOMIC_TEST_PROFILE
#include "wait_atomic_test.h"
#endif

struct proc pool[NPROC];
static struct file fd_reservation;

extern char boot_stack_top[];
struct thread *current_thread;
struct thread idle;
static struct context scheduler_context;

struct scheduler_lane {
	short head;
	short tail;
	unsigned short count;
};

_Static_assert(NPROC * NTHREAD <= 0x7fff,
	       "scheduler task ids must fit a signed lane link");
_Static_assert(THREAD_RESOURCE_DOMAIN_QUEUE_LIMIT <= 0xffff,
	       "scheduler lane count must fit its compact field");

static struct queue scheduler_active_domains;
static int scheduler_active_domain_data[PROC_RESOURCE_DOMAIN_CAP];
static uchar scheduler_domain_active[PROC_RESOURCE_DOMAIN_CAP];
static short scheduler_task_next[NPROC * NTHREAD];

__attribute__((noreturn, noinline, cold))
static void scheduler_invariant_stop(uint code)
{
	static const char prefix[] = "[PANIC] scheduler invariant ";

	for (uint i = 0; i < sizeof(prefix) - 1; i++)
		console_putchar(prefix[i]);
	console_putchar('0' + (code / 10) % 10);
	console_putchar('0' + code % 10);
	console_putchar('\n');
	shutdown();
	__builtin_unreachable();
}

enum proc_admission {
	PROC_ADMIT_BOOT,
	PROC_ADMIT_NORMAL,
	PROC_ADMIT_WORKFLOW,
	PROC_ADMIT_AGENT,
	PROC_ADMIT_WORKER,
};

struct proc_resource_domain {
	int used;
	struct resource_account_handle account;
	struct scheduler_lane scheduler_normal;
	struct scheduler_lane scheduler_agent;
	uint64 scheduler_agent_burst;
	uint64 scheduler_score_burst;
};

static struct proc_resource_domain
	proc_resource_domains[PROC_RESOURCE_DOMAIN_CAP];
static uint64 proc_resource_next_account_id;
static uint proc_scope_next_id;
static int proc_scope_id_exhausted;
static uint kernel_stack_live_slots;
static uint kernel_stack_live_ordinary;
static uint kernel_stack_live_reserved;
static uint64 thread_identity_next_generation;

/* 包括启动线程在内，每个存活线程都获得不可复用的 incarnation。 */
static void thread_identity_activate(struct thread *t)
{
	int enabled = intr_save();

	if (t->identity_generation == 0) {
		thread_identity_next_generation++;
		if (thread_identity_next_generation == 0)
			thread_identity_next_generation++;
		t->identity_generation = thread_identity_next_generation;
	}
	intr_restore(enabled);
}

static void proc_reset_thread_slot(struct thread *t);
static void proc_recycle(struct proc *p);
static void proc_thread_resource_release(struct thread *t);
static void proc_teardown_advance(struct proc *p,
				  enum proc_teardown_state expected,
				  enum proc_teardown_state next);

#define KSTACK_POISON 0xa5
#define KSTACK_CANARY 0x6b737461636b4f53ULL
_Static_assert(KSTACK_SIZE >= 2 * PGSIZE,
	       "kernel stacks need room for calls and interrupt frames");
_Static_assert(KSTACK_SIZE % PGSIZE == 0,
	       "kernel stack size must be page aligned");
_Static_assert(PGSIZE == PAGE_SIZE,
	       "kernel stack page definitions must agree");
_Static_assert(KSTACK_GUARD_SIZE >= PGSIZE,
	       "kernel stack guard must cover at least one page");
_Static_assert(KSTACK_GUARD_SIZE % PGSIZE == 0,
	       "kernel stack guard size must be page aligned");
_Static_assert(KSTACK_PAGES_PER_THREAD > 0,
	       "kernel stacks must contain at least one page");
_Static_assert(KSTACK_VIRTUAL_CAPACITY_BYTES <
		       (uint64)NPROC * NTHREAD * KSTACK_SLOT_SIZE,
	       "guard pages must remain outside stack capacity");
_Static_assert((uint64)NPROC * NTHREAD * KSTACK_SLOT_SIZE <
		       TRAMPOLINE,
	       "kernel stack virtual region must not wrap");
_Static_assert(KSTACK_RESERVED_PAGE_COUNT ==
		       (uint)THREAD_RESOURCE_RESERVED_LIMIT *
			       KSTACK_PAGES_PER_THREAD,
	       "reserved thread slots need complete physical stacks");
_Static_assert(PROC_RESERVED_SLOTS > 0 && PROC_RESERVED_SLOTS < NPROC,
	       "process reserve must leave both resource classes usable");
_Static_assert(RESOURCE_ACCOUNT_CAP >=
		       PROC_RESOURCE_DOMAIN_CAP + NPROC + 2,
	       "resource accounts must fit exec and persistent principals");
_Static_assert(AGENT_STATE_PAGE_ORDINARY_LIMIT +
		       AGENT_STATE_PAGE_RESERVED_LIMIT ==
		       AGENT_STATE_PAGE_POOL_SIZE,
	       "Agent state page classes must partition the global pool");
_Static_assert(USER_HEAP_LIMIT <= 0x7fffffffULL,
	       "program break must fit the signed syscall result ABI");
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

static int kernel_stack_identity(struct thread *t, struct proc **owner,
				 int *tid_out)
{
	uint64 pool_start = (uint64)&pool[0];
	uint64 pool_end = (uint64)&pool[NPROC];
	uint64 thread_address;
	uint64 thread_start;
	uint64 thread_end;
	struct proc *p;
	int proc_index;
	int tid;

	if (t == 0 || t == &idle || owner == 0 || tid_out == 0)
		return -1;
	thread_address = (uint64)t;
	if (thread_address < pool_start || thread_address >= pool_end)
		return -1;
	proc_index = (thread_address - pool_start) / sizeof(struct proc);
	p = &pool[proc_index];
	thread_start = (uint64)&p->threads[0];
	thread_end = (uint64)&p->threads[NTHREAD];
	if (thread_address < thread_start || thread_address >= thread_end ||
	    (thread_address - thread_start) % sizeof(struct thread) != 0 ||
	    t->process != p)
		return -1;
	tid = (thread_address - thread_start) / sizeof(struct thread);
	if (t->kstack != kernel_stack_base(proc_index, tid))
		return -1;
	*owner = p;
	*tid_out = tid;
	return 0;
}

static enum resource_charge_class
thread_physical_charge_class(const struct thread *t)
{
	return t->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
					  RESOURCE_CHARGE_ORDINARY;
}

static int thread_trapframe_acquire(struct thread *t)
{
	void *page;

	if (t == 0 || t == &idle || !t->resource_slot_charged ||
	    !resource_account_handle_valid(t->resource_account))
		panic("trapframe acquire owner");
	if (t->trapframe != 0)
		return 0;
	page = kalloc_account_page(t->resource_account,
				   thread_physical_charge_class(t));
	if (page == 0)
		return -1;
	memset(page, 0, TRAP_PAGE_SIZE);
	if (t->trapframe != 0)
		panic("trapframe acquire race");
	t->trapframe = page;
	return 0;
}

static void thread_trapframe_release(struct thread *t)
{
	void *page;

	if (t == 0 || t->trapframe == 0)
		return;
	if (!t->resource_slot_charged ||
	    !resource_account_handle_valid(t->resource_account))
		panic("trapframe release owner");
	page = t->trapframe;
	t->trapframe = 0;
	memset(page, 6, TRAP_PAGE_SIZE);
	if (kfree_account_page(page, t->resource_account,
				thread_physical_charge_class(t)) < 0)
		panic("trapframe resource release");
}

static void kernel_stack_reset_slot(struct proc *p, int tid)
{
	uint64 slot = kernel_stack_slot(p - pool, tid);
	uint64 *low = (uint64 *)kernel_stack_base(p - pool, tid);
	uint64 canary = kernel_stack_canary(slot);

	if (p < pool || p >= &pool[NPROC] || tid < 0 || tid >= NTHREAD ||
	    p->threads[tid].kstack_state != KSTACK_LIVE ||
	    p->threads[tid].kstack != (uint64)low)
		panic("kernel stack reset");
	memset((void *)low, KSTACK_POISON, KSTACK_SIZE);
	low[0] = canary;
	low[1] = ~canary;
}

void proc_mapstacks(pagetable_t kpgtbl)
{
	if (kalloc_stack_reserve_init(KSTACK_RESERVED_PAGE_COUNT) < 0)
		panic("kernel stack reserve");
	for (int proc_index = 0; proc_index < NPROC; proc_index++) {
		for (int tid = 0; tid < NTHREAD; tid++) {
			uint64 guard =
				kernel_stack_guard(
					kernel_stack_slot(proc_index, tid));
			uint64 stack = guard + KSTACK_GUARD_SIZE;

			for (uint64 off = 0; off < KSTACK_SIZE; off += PGSIZE) {
				pte_t *pte = walk(kpgtbl, stack + off, 1);

				if (pte == 0 || *pte != 0)
					panic("kernel stack leaf preparation");
			}
			for (uint64 off = 0; off < KSTACK_GUARD_SIZE;
			     off += PGSIZE) {
				pte_t *guard_pte = walk(kpgtbl, guard + off, 0);

				if (guard_pte != 0 && *guard_pte != 0)
					panic("kernel stack guard mapped");
			}
		}
	}
	if (kalloc_stack_reserved_total_pages() !=
		    KSTACK_RESERVED_PAGE_COUNT ||
	    kalloc_stack_reserved_free_pages() !=
		    KSTACK_RESERVED_PAGE_COUNT)
		panic("kernel stack reserve accounting");
}

static int kernel_stack_acquire(struct thread *t)
{
	void *pages[KSTACK_PAGES_PER_THREAD];
	pte_t *ptes[KSTACK_PAGES_PER_THREAD];
	struct proc *p;
	int tid;
	int reserved;
	int allocated = 0;
	int enabled;
	uint64 slot;
	uint64 base;
	struct resource_request page_request = {
		.kind = RESOURCE_PHYSICAL_PAGE,
		.amount = KSTACK_PAGES_PER_THREAD,
	};
	struct resource_reservation page_reservation;
	enum resource_charge_class page_charge_class;
	int page_reservation_active = 0;

	if (kernel_stack_identity(t, &p, &tid) < 0 ||
	    !t->resource_slot_charged)
		panic("kernel stack acquire owner");
	if (t->kstack_state == KSTACK_LIVE)
		return 0;
	if (t->kstack_state != KSTACK_NONE)
		panic("kernel stack acquire state");
	reserved = t->resource_slot_reserved != 0;
	page_charge_class = thread_physical_charge_class(t);
	/* 预留栈使用 THREAD 计费栈池，不占页保留。 */
	if (!reserved) {
		if (resource_reserve_many(t->resource_account,
					  page_charge_class, &page_request, 1,
					  &page_reservation) < 0)
			return -1;
		page_reservation_active = 1;
	}
	for (; allocated < KSTACK_PAGES_PER_THREAD; allocated++) {
		pages[allocated] = kalloc_stack_page(reserved);
		if (pages[allocated] == 0)
			goto fail;
		memset(pages[allocated], KSTACK_POISON, PGSIZE);
	}
	slot = kernel_stack_slot(p - pool, tid);
	base = kernel_stack_base(p - pool, tid);
	((uint64 *)pages[0])[0] = kernel_stack_canary(slot);
	((uint64 *)pages[0])[1] = ~((uint64 *)pages[0])[0];
	enabled = intr_save();
	if (t->kstack_state != KSTACK_NONE)
		panic("kernel stack acquire race");
	for (int page = 0; page < KSTACK_PAGES_PER_THREAD; page++) {
		ptes[page] =
			walk(kernel_pagetable, base + page * PGSIZE, 0);
		if (ptes[page] == 0 || *ptes[page] != 0)
			panic("kernel stack leaf invariant");
	}
	for (uint64 off = 0; off < KSTACK_GUARD_SIZE; off += PGSIZE) {
		pte_t *guard = walk(kernel_pagetable,
				    kernel_stack_guard(slot) + off, 0);

		if (guard != 0 && *guard != 0)
			panic("kernel stack guard invariant");
	}
	for (int page = 0; page < KSTACK_PAGES_PER_THREAD; page++)
		*ptes[page] =
			PA2PTE((uint64)pages[page]) |
			PTE_R | PTE_W | PTE_V;
	sfence_vma();
	t->kstack_state = KSTACK_LIVE;
	kernel_stack_live_slots++;
	if (reserved)
		kernel_stack_live_reserved++;
	else
		kernel_stack_live_ordinary++;
	if (page_reservation_active &&
	    resource_reservation_commit(&page_reservation) < 0)
		panic("kernel stack page commit");
	intr_restore(enabled);
	return 0;

fail:
	while (allocated > 0)
		kfree_stack_page(pages[--allocated], reserved);
	if (page_reservation_active)
		resource_reservation_cancel(&page_reservation);
	return -1;
}

static void kernel_stack_mark_reap(struct thread *t)
{
	int enabled = intr_save();

	if (t == 0 || t != current_thread ||
	    t->kstack_state != KSTACK_LIVE ||
	    t->state != T_DYING)
		panic("kernel stack reap request");
	t->kstack_state = KSTACK_REAP;
	intr_restore(enabled);
}

static void kernel_stack_release_inactive(struct thread *t)
{
	void *pages[KSTACK_PAGES_PER_THREAD];
	pte_t *ptes[KSTACK_PAGES_PER_THREAD];
	struct proc *p;
	int tid;
	int reserved;
	int enabled;
	uint64 slot;
	uint64 base;
	struct resource_request page_request = {
		.kind = RESOURCE_PHYSICAL_PAGE,
		.amount = KSTACK_PAGES_PER_THREAD,
	};

	if (kernel_stack_identity(t, &p, &tid) < 0)
		panic("kernel stack release owner");
	enabled = intr_save();
	if (t->kstack_state == KSTACK_NONE) {
		intr_restore(enabled);
		return;
	}
	if (t == current_thread ||
	    (t->kstack_state != KSTACK_LIVE &&
	     t->kstack_state != KSTACK_REAP))
		panic("active kernel stack release");
	reserved = t->resource_slot_reserved != 0;
	slot = kernel_stack_slot(p - pool, tid);
	base = kernel_stack_base(p - pool, tid);
	for (int page = 0; page < KSTACK_PAGES_PER_THREAD; page++) {
		uint64 flags;

		ptes[page] =
			walk(kernel_pagetable, base + page * PGSIZE, 0);
		if (ptes[page] == 0)
			panic("kernel stack release leaf");
		flags = PTE_FLAGS(*ptes[page]);
		if ((flags & (PTE_V | PTE_R | PTE_W)) !=
			    (PTE_V | PTE_R | PTE_W) ||
		    (flags & (PTE_U | PTE_X)) != 0)
			panic("kernel stack release mapping");
		pages[page] = (void *)PTE2PA(*ptes[page]);
		for (int prior = 0; prior < page; prior++)
			if (pages[prior] == pages[page])
				panic("kernel stack duplicate page");
	}
	for (uint64 off = 0; off < KSTACK_GUARD_SIZE; off += PGSIZE) {
		pte_t *guard = walk(kernel_pagetable,
				    kernel_stack_guard(slot) + off, 0);

		if (guard != 0 && *guard != 0)
			panic("kernel stack guard release");
	}
	for (int page = 0; page < KSTACK_PAGES_PER_THREAD; page++)
		*ptes[page] = 0;
	sfence_vma();
	t->kstack_state = KSTACK_NONE;
	if (kernel_stack_live_slots == 0)
		panic("kernel stack live count");
	kernel_stack_live_slots--;
	if (reserved) {
		if (kernel_stack_live_reserved == 0)
			panic("kernel stack reserve live count");
		kernel_stack_live_reserved--;
	} else {
		if (kernel_stack_live_ordinary == 0)
			panic("kernel stack ordinary live count");
		kernel_stack_live_ordinary--;
	}
	for (int page = 0; page < KSTACK_PAGES_PER_THREAD; page++)
		kfree_stack_page(pages[page], reserved);
	if (!reserved &&
	    resource_release_many(
		    t->resource_account, RESOURCE_CHARGE_ORDINARY,
		    &page_request, 1) < 0)
		panic("kernel stack page release");
	intr_restore(enabled);
}

static void kernel_stack_assert_quiescent(void)
{
	if (current_thread != &idle || kernel_stack_live_slots != 0 ||
	    kernel_stack_live_ordinary != 0 ||
	    kernel_stack_live_reserved != 0 ||
	    kalloc_stack_reserved_free_pages() !=
		    kalloc_stack_reserved_total_pages())
		panic("kernel stack shutdown accounting");
	for (int proc_index = 0; proc_index < NPROC; proc_index++) {
		for (int tid = 0; tid < NTHREAD; tid++) {
			uint64 slot = kernel_stack_slot(proc_index, tid);
			uint64 guard = kernel_stack_guard(slot);
			uint64 base = kernel_stack_base(proc_index, tid);

			if (pool[proc_index].threads[tid].kstack_state !=
			    KSTACK_NONE)
				panic("kernel stack shutdown state");
			if (pool[proc_index].threads[tid].trapframe != 0)
				panic("trapframe shutdown state");
			for (uint64 off = 0; off < KSTACK_SIZE;
			     off += PGSIZE) {
				pte_t *pte =
					walk(kernel_pagetable, base + off, 0);

				if (pte == 0 || *pte != 0)
					panic("kernel stack shutdown mapping");
			}
			for (uint64 off = 0; off < KSTACK_GUARD_SIZE;
			     off += PGSIZE) {
				pte_t *pte =
					walk(kernel_pagetable, guard + off, 0);

				if (pte != 0 && *pte != 0)
					panic("kernel stack shutdown guard");
			}
		}
	}
}

void kernel_stack_check(struct thread *t)
{
	struct proc *p;
	int proc_index;
	int tid;
	uint64 slot;
	uint64 expected_base;
	uint64 canary;
	uint64 *low;

	if (t == 0 || t == &idle)
		return;
	if (kernel_stack_identity(t, &p, &tid) < 0 ||
	    (t->kstack_state != KSTACK_LIVE &&
	     t->kstack_state != KSTACK_REAP))
		panic("invalid kernel stack owner");
	proc_index = p - pool;
	slot = kernel_stack_slot(proc_index, tid);
	expected_base = kernel_stack_base(proc_index, tid);
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

int proc_teardown_live(const struct proc *p)
{
	return p != 0 && p->state == P_USED &&
	       p->teardown_state == PROC_TEARDOWN_LIVE;
}

int proc_thread_exit_requested(void)
{
	struct thread *t = curr_thread();
	struct proc *p;

	if (t == 0 || t == &idle || (p = t->process) == 0)
		return 0;
	return p->teardown_state >= PROC_TEARDOWN_REQUESTED &&
	       p->teardown_state < PROC_TEARDOWN_HANDOFF &&
	       t->tid != p->teardown_owner_tid;
}

// 关中断调用；首次请求确定退出状态，后续撤销仅唤醒且不转移清理所有权。
static int proc_teardown_request_locked(struct proc *p, int code)
{
	if (p == 0 || p->state != P_USED)
		return -1;
	if (p->teardown_state == PROC_TEARDOWN_LIVE) {
		p->exit_code = (uint64)code;
		p->teardown_owner_tid = PROC_TEARDOWN_OWNER_NONE;
		p->teardown_state = PROC_TEARDOWN_REQUESTED;
		return 1;
	}
	if (p->teardown_state >= PROC_TEARDOWN_REQUESTED &&
	    p->teardown_state < PROC_TEARDOWN_HANDOFF)
		return 0;
	return -1;
}

// 关中断调用；仅主线程或未发布回滚执行者可认领进程，所有权一经确定不再转移。
static int proc_teardown_claim_locked(struct proc *p, int owner)
{
	if (p == 0 || p->state != P_USED ||
	    p->teardown_state != PROC_TEARDOWN_REQUESTED ||
	    p->teardown_owner_tid != PROC_TEARDOWN_OWNER_NONE ||
	    (owner != 0 && owner != PROC_TEARDOWN_OWNER_KERNEL))
		return -1;
	p->teardown_owner_tid = owner;
	p->teardown_state = PROC_TEARDOWN_QUIESCING;
	return 0;
}

int proc_request_workflow_exit(struct workflow_lifecycle_key lifecycle,
			       int code)
{
	int requested = 0;
	int enabled;

	if (!workflow_lifecycle_key_valid(lifecycle))
		return 0;
	enabled = intr_save();
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state != P_USED ||
		    p->teardown_state >= PROC_TEARDOWN_DETACHED ||
		    !p->workflow_lifecycle_charged ||
		    p->workflow_lifecycle_id != lifecycle.id ||
		    p->workflow_lifecycle_generation !=
			    lifecycle.generation)
			continue;
		requested++;
		(void)proc_teardown_request_locked(p, code);
		for (int tid = 0; tid < NTHREAD; tid++) {
			struct thread *t = &p->threads[tid];

			if (tid != p->teardown_owner_tid &&
			    t->state == SLEEPING)
				wait_queue_interrupt(t);
		}
	}
	intr_restore(enabled);
	return requested;
}

/* 将遗弃的控制子树交给常规清理状态机；恢复中断前先闭合有界控制图，
 * 析构仍只经逐进程清理路径执行。 */
int proc_request_controller_exit(struct workflow_lifecycle_key lifecycle,
				 uint64 controller_id, int code)
{
	uint64 controllers[(NPROC + 63) / 64];
	uint64 matched[(NPROC + 63) / 64];
	int requested = 0;
	int enabled;
	int expanded;

	if (!workflow_lifecycle_key_valid(lifecycle) || controller_id == 0)
		return 0;
	memset(controllers, 0, sizeof(controllers));
	memset(matched, 0, sizeof(matched));
	enabled = intr_save();
	do {
		expanded = 0;
		for (struct proc *p = pool; p < &pool[NPROC]; p++) {
			uint slot = p - pool;
			uint64 bit = 1ULL << (slot & 63);
			int controlled = p->agent_controller_id == controller_id;

			if (p->state != P_USED ||
			    !p->workflow_lifecycle_charged ||
			    p->workflow_lifecycle_id != lifecycle.id ||
			    p->workflow_lifecycle_generation != lifecycle.generation)
				continue;
			if (!controlled && p->agent_controller_id != 0)
				for (int i = 0; i < NPROC; i++) {
					uint64 controller_bit =
						1ULL << (i & 63);

					if ((controllers[i / 64] & controller_bit) != 0 &&
					    pool[i].agent_control_id ==
						    p->agent_controller_id) {
						controlled = 1;
						break;
					}
				}
			if (!controlled)
				continue;
			if (p->is_agent && p->agent_control_id != 0) {
				if ((controllers[slot / 64] & bit) == 0) {
					controllers[slot / 64] |= bit;
					expanded = 1;
				}
			}
			if ((matched[slot / 64] & bit) != 0)
				continue;
			matched[slot / 64] |= bit;
			if (p->teardown_state >= PROC_TEARDOWN_DETACHED)
				continue;
			requested++;
			(void)proc_teardown_request_locked(p, code);
			for (int tid = 0; tid < NTHREAD; tid++) {
				struct thread *t = &p->threads[tid];

				if (tid != p->teardown_owner_tid &&
				    t->state == SLEEPING)
					wait_queue_interrupt(t);
			}
		}
	} while (expanded);
	intr_restore(enabled);
	return requested;
}

// 虚拟内存快照可在已提交页面之间让出处理器。快照存续期间，调度器暂停
// 兄弟线程以保持源页表稳定；无关进程仍可调度。
int proc_vm_snapshot_begin(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled;
	int result = -1;

	if (p == 0 || t == 0 || t == &idle || t->process != p || t->tid < 0 ||
	    t->tid >= NTHREAD)
		return -1;
	enabled = intr_save();
	if (p->teardown_state == PROC_TEARDOWN_LIVE &&
	    p->vm_snapshot_depth == 0) {
		p->vm_snapshot_owner_tid = t->tid;
		p->vm_snapshot_depth = 1;
		result = 0;
	} else if (p->teardown_state == PROC_TEARDOWN_LIVE &&
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

/* 堆位于全部固定线程栈槽上方且两侧有保护页；逐页 VM 快照时暂停兄弟线程，
 * 使 brk 与 fork 及 exec 可见的地址空间状态保持原子。 */
int proc_sbrk(long delta)
{
	struct proc *p = curr_proc();
	pagetable_t pagetable;
	uint64 old_break, new_break;
	uint64 old_end, new_end;
	uint64 mapped_pages = 0;
	uint64 magnitude;
	int result = -1;
	int enabled;

	if (proc_vm_snapshot_begin(p) < 0)
		return -1;
	enabled = intr_save();
	if (!proc_teardown_live(p) || p->pagetable == 0 ||
	    p->heap_base == 0 || (p->heap_base % PGSIZE) != 0 ||
	    p->heap_base > USER_HEAP_LIMIT ||
	    p->heap_break < p->heap_base ||
	    p->heap_break > USER_HEAP_LIMIT) {
		intr_restore(enabled);
		goto out;
	}
	pagetable = p->pagetable;
	old_break = p->heap_break;
	intr_restore(enabled);
	if (delta == 0) {
		result = (int)old_break;
		goto out;
	}
	if (delta > 0) {
		magnitude = (uint64)delta;
		if (magnitude > USER_HEAP_LIMIT - old_break)
			goto out;
		new_break = old_break + magnitude;
	} else {
		/* 无符号减法无需对 LONG_MIN 取负。 */
		magnitude = 0 - (uint64)delta;
		if (magnitude > old_break - p->heap_base)
			goto out;
		new_break = old_break - magnitude;
	}
	old_end = PGROUNDUP(old_break);
	new_end = PGROUNDUP(new_break);
	if (new_end > old_end) {
		uint64 pages = (new_end - old_end) / PGSIZE;

		while (mapped_pages < pages) {
			if (uvmmap(pagetable,
				   old_end + mapped_pages * PGSIZE, 1,
				   PTE_U | PTE_R | PTE_W) < 0)
				goto rollback_growth;
			mapped_pages++;
			if (kernel_work_checkpoint(KERNEL_WORK_PAGE_UNITS) < 0)
				goto rollback_growth;
		}
		enabled = intr_save();
		if (!proc_teardown_live(p) || p->pagetable != pagetable ||
		    p->heap_break != old_break) {
			intr_restore(enabled);
			goto rollback_growth;
		}
		p->heap_break = new_break;
		p->max_page = MAX(p->max_page, new_end / PGSIZE);
		intr_restore(enabled);
	} else {
		/* 首个叶页退款后收缩不可逆，后续须公平完成。 */
		if (old_end > new_end)
			uvm_unmap_reclaim(pagetable, new_end,
					  (old_end - new_end) / PGSIZE);
		enabled = intr_save();
		if (p->pagetable != pagetable || p->heap_break != old_break)
			panic("brk snapshot changed");
		p->heap_break = new_break;
		p->max_page = MAX(p->heap_base / PGSIZE,
				  new_end / PGSIZE);
		intr_restore(enabled);
	}
	result = (int)old_break;
	goto out;

rollback_growth:
	/* 同时清理 mappages() 失败遗留的空页表页。 */
	uvm_unmap_reclaim(pagetable, old_end, mapped_pages);
out:
	proc_vm_snapshot_end(p);
	return result;
}

static int proc_vm_snapshot_schedulable(const struct thread *t)
{
	const struct proc *p;

	if (t == 0 || (p = t->process) == 0)
		return 0;
	return p->vm_snapshot_depth == 0 ||
	       p->vm_snapshot_owner_tid == t->tid;
}

static void scheduler_lane_init(struct scheduler_lane *lane)
{
	lane->head = -1;
	lane->tail = -1;
	lane->count = 0;
}

static void scheduler_lane_push(struct scheduler_lane *lane, int task_id)
{
	if (lane == 0 || task_id < 0 || task_id >= NPROC * NTHREAD ||
	    lane->count >= THREAD_RESOURCE_DOMAIN_QUEUE_LIMIT)
		scheduler_invariant_stop(19);
	if (scheduler_task_next[task_id] != -1)
		scheduler_invariant_stop(20);
	if (lane->count == 0) {
		if (lane->head != -1 || lane->tail != -1)
			scheduler_invariant_stop(19);
		lane->head = task_id;
	} else {
		if (lane->tail < 0 || lane->tail >= NPROC * NTHREAD ||
		    scheduler_task_next[lane->tail] != -1)
			scheduler_invariant_stop(19);
		scheduler_task_next[lane->tail] = task_id;
	}
	lane->tail = task_id;
	lane->count++;
}

static void scheduler_lane_remove_after(struct scheduler_lane *lane,
					int previous, int task_id)
{
	int next;

	if (lane == 0 || task_id < 0 || task_id >= NPROC * NTHREAD ||
	    lane->count <= 0)
		scheduler_invariant_stop(21);
	next = scheduler_task_next[task_id];
	if (previous < 0) {
		if (lane->head != task_id)
			scheduler_invariant_stop(22);
		lane->head = next;
	} else {
		if (previous >= NPROC * NTHREAD ||
		    scheduler_task_next[previous] != task_id)
			scheduler_invariant_stop(22);
		scheduler_task_next[previous] = next;
	}
	if (next < 0 && lane->tail != task_id)
		scheduler_invariant_stop(23);
	if (lane->tail == task_id)
		lane->tail = previous;
	scheduler_task_next[task_id] = -1;
	lane->count--;
	if ((lane->count == 0 &&
	     (lane->head != -1 || lane->tail != -1)) ||
	    (lane->count > 0 && (lane->head < 0 || lane->tail < 0)))
		scheduler_invariant_stop(24);
}

static int scheduler_domain_task_count(const struct proc_resource_domain *domain)
{
	return domain->scheduler_normal.count + domain->scheduler_agent.count;
}

static void scheduler_queues_init(void)
{
	queue_init(&scheduler_active_domains, PROC_RESOURCE_DOMAIN_CAP,
		   scheduler_active_domain_data);
	for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++) {
		scheduler_lane_init(
			&proc_resource_domains[i].scheduler_normal);
		scheduler_lane_init(
			&proc_resource_domains[i].scheduler_agent);
		scheduler_domain_active[i] = 0;
	}
	for (int i = 0; i < NPROC * NTHREAD; i++) {
		scheduler_task_next[i] = -1;
	}
}

static void proc_resource_domain_clear(struct proc_resource_domain *domain)
{
	int domain_id = domain - proc_resource_domains;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		panic("resource domain address invariant");
	if (scheduler_domain_task_count(domain) != 0 ||
	    scheduler_domain_active[domain_id])
		panic("resource domain run queue invariant");
	scheduler_lane_init(&domain->scheduler_normal);
	scheduler_lane_init(&domain->scheduler_agent);
	domain->used = 0;
	domain->account = resource_account_none();
	domain->scheduler_agent_burst = 0;
	domain->scheduler_score_burst = 0;
}

void proc_resource_account_reap(struct resource_account_handle account)
{
	int enabled = intr_save();

	if (resource_account_state_get(account) == RESOURCE_ACCOUNT_FREE) {
		for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++)
			if (proc_resource_domains[i].used &&
			    resource_account_handle_equal(
				    proc_resource_domains[i].account, account)) {
				proc_resource_domain_clear(
					&proc_resource_domains[i]);
				break;
			}
	}
	intr_restore(enabled);
}

static void proc_resource_init(void)
{
	resource_controller_init();
	if (resource_policy_configure(
		    RESOURCE_PROCESS, NPROC, PROC_ORDINARY_SLOTS,
		    PROC_RESERVED_SLOTS) < 0 ||
	    resource_policy_configure(
		    RESOURCE_THREAD, THREAD_RESOURCE_POOL_SIZE,
		    THREAD_RESOURCE_ORDINARY_LIMIT,
		    THREAD_RESOURCE_RESERVED_LIMIT) < 0 ||
	    resource_policy_configure(
		    RESOURCE_FILE_OBJECT, FILE_RESOURCE_POOL_SIZE,
		    FILE_RESOURCE_ORDINARY_LIMIT,
		    FILE_RESOURCE_POOL_SIZE -
			    FILE_RESOURCE_ORDINARY_LIMIT) < 0 ||
	    resource_policy_configure(
		    RESOURCE_AGENT_STATE_PAGE,
		    AGENT_STATE_PAGE_POOL_SIZE,
		    AGENT_STATE_PAGE_ORDINARY_LIMIT,
		    AGENT_STATE_PAGE_RESERVED_LIMIT) < 0)
		panic("process resource policy");
	for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++)
		proc_resource_domain_clear(&proc_resource_domains[i]);
	proc_resource_next_account_id = 1;
	proc_scope_next_id = VFS_SCOPE_FIRST_DYNAMIC;
	proc_scope_id_exhausted = 0;
}

static void proc_resource_limits(
	struct resource_account_limits *limits)
{
	memset(limits, 0, sizeof(*limits));
	limits->class_limit[RESOURCE_CHARGE_ORDINARY]
			   [RESOURCE_PROCESS] =
		PROC_RESOURCE_DOMAIN_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_RESERVED]
			   [RESOURCE_PROCESS] =
		PROC_RESOURCE_DOMAIN_RESERVED_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_ORDINARY][RESOURCE_THREAD] =
		THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_RESERVED][RESOURCE_THREAD] =
		THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_ORDINARY]
			   [RESOURCE_FILE_OBJECT] =
		FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_RESERVED]
			   [RESOURCE_FILE_OBJECT] =
		FILE_RESOURCE_DOMAIN_RESERVED_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_ORDINARY]
			   [RESOURCE_AGENT_STATE_PAGE] =
		AGENT_STATE_PAGE_DOMAIN_ORDINARY_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_RESERVED]
			   [RESOURCE_AGENT_STATE_PAGE] =
		AGENT_STATE_PAGE_DOMAIN_RESERVED_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_ORDINARY]
			   [RESOURCE_PHYSICAL_PAGE] =
		PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT;
	limits->class_limit[RESOURCE_CHARGE_RESERVED]
			   [RESOURCE_PHYSICAL_PAGE] =
		PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT;
}

static int proc_thread_resource_charge_locked(struct thread *t,
					      int domain_id,
					      int reserved)
{
	struct proc_resource_domain *domain;
	struct resource_request request = {
		.kind = RESOURCE_THREAD,
		.amount = 1,
	};
	struct resource_reservation reservation;

	if (t == 0 || t->resource_slot_charged ||
	    domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		panic("thread resource charge invariant");
	domain = &proc_resource_domains[domain_id];
	if (!domain->used ||
	    resource_reserve_many(
		    domain->account,
		    reserved ? RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY,
		    &request, 1,
				  &reservation) < 0)
		return -1;
	if (resource_reservation_commit(&reservation) < 0)
		panic("thread resource commit");
	t->resource_account = domain->account;
	t->resource_domain_id = domain_id;
	t->resource_slot_reserved = reserved;
	t->resource_slot_charged = 1;
	return 0;
}

static void proc_thread_resource_release(struct thread *t)
{
	int enabled = intr_save();
	int domain_id;
	struct resource_account_handle account;
	struct resource_request request = {
		.kind = RESOURCE_THREAD,
		.amount = 1,
	};

	if (t == 0 || !t->resource_slot_charged)
		goto out;
	if (t->on_run_queue || t->state == RUNNABLE || t->state == RUNNING ||
	    t->state == SLEEPING)
		panic("active thread resource release");
	domain_id = t->resource_domain_id;
	account = t->resource_account;
	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP ||
	    !proc_resource_domains[domain_id].used ||
	    !resource_account_handle_equal(
		    proc_resource_domains[domain_id].account, account))
		panic("thread resource domain invariant");
	if (resource_release_many(
		    account,
		    t->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
						RESOURCE_CHARGE_ORDINARY,
		    &request, 1) < 0)
		panic("thread resource release invariant");
	t->resource_account = resource_account_none();
	t->resource_domain_id = -1;
	t->resource_slot_reserved = 0;
	t->resource_slot_charged = 0;
out:
	intr_restore(enabled);
}

// 文件系统扫描持久工作流属主后抬高该下界；对象可能存活期间 scope 标识不回绕、不复用。
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

// 原子预留进程槽并计费到其不可变资源域。只有内核签名的资源域管理员
// 才能创建新资源域或使用保留量。
static struct proc *proc_resource_reserve(struct proc *parent,
					 enum proc_admission admission)
{
	struct proc_resource_domain *domain;
	struct proc *p = 0;
	int enabled = intr_save();
	int domain_id = -1;
	int new_domain = 0;
	int account_created = 0;
	int member_acquired = 0;
	int reserved = admission != PROC_ADMIT_NORMAL;
	uint storage_principal = FS_OWNER_NONE;
	struct resource_account_handle account = resource_account_none();
	struct resource_account_limits limits;
	struct resource_reservation reservation;
	struct resource_request requests[] = {
		{ .kind = RESOURCE_PROCESS, .amount = 1 },
		{ .kind = RESOURCE_THREAD, .amount = 1 },
	};

	if (admission < PROC_ADMIT_BOOT || admission > PROC_ADMIT_WORKER)
		goto fail;
	if (admission == PROC_ADMIT_BOOT) {
		if (parent != 0)
			goto fail;
		new_domain = 1;
	} else {
		if (!proc_teardown_live(parent) ||
		    parent->resource_domain_id < 0 ||
		    parent->resource_domain_id >= PROC_RESOURCE_DOMAIN_CAP)
			goto fail;
		domain = &proc_resource_domains[parent->resource_domain_id];
		if (!domain->used ||
		    !resource_account_handle_equal(
			    domain->account, parent->resource_account) ||
		    !resource_account_active(domain->account))
			goto fail;
		if (admission == PROC_ADMIT_NORMAL) {
			if (parent->resource_domain_admin)
				new_domain = 1;
			else
				domain_id = parent->resource_domain_id;
		} else if (admission == PROC_ADMIT_WORKFLOW) {
			if (!parent->resource_slot_reserved ||
			    !parent->resource_domain_admin)
				goto fail;
			new_domain = 1;
		} else {
			// 工作流内启动的进程共享同一固定资源域；Agent 创建权不得铸造新的进程或 PUBLIC 存储配额域。
			domain_id = parent->resource_domain_id;
		}
	}
	if (new_domain) {
		for (int i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++) {
			if (!proc_resource_domains[i].used) {
				domain_id = i;
				break;
			}
		}
		if (domain_id < 0)
			goto fail;
		domain = &proc_resource_domains[domain_id];
	}
	for (p = pool; p < &pool[NPROC]; p++)
		if (p->state == P_UNUSED)
			break;
	if (p == &pool[NPROC]) {
		p = 0;
		goto fail;
	}
	if (!agent_context_is_empty(p))
		panic("stale Agent context state");
	for (int tid = 0; tid < NTHREAD; tid++)
		if (p->threads[tid].resource_slot_charged ||
		    p->threads[tid].kstack_state != KSTACK_NONE ||
		    p->threads[tid].trapframe != 0)
			panic("stale thread resource charge");
	if (admission == PROC_ADMIT_BOOT || admission == PROC_ADMIT_WORKFLOW) {
		storage_principal = proc_scope_alloc_id();
		if (storage_principal == FS_OWNER_NONE) {
			p = 0;
			goto fail;
		}
	} else if (admission == PROC_ADMIT_NORMAL) {
		storage_principal = FS_OWNER_PUBLIC;
	} else {
		storage_principal = parent->storage_principal_id;
		if (storage_principal < VFS_SCOPE_FIRST_DYNAMIC ||
		    storage_principal >= FS_OWNER_SCOPE_FLAG) {
			p = 0;
			goto fail;
		}
	}
	if (new_domain) {
		uint grants =
			RESOURCE_CHARGE_GRANT(
				RESOURCE_CHARGE_ORDINARY);

		if (reserved)
			grants |= RESOURCE_CHARGE_GRANT(
				RESOURCE_CHARGE_RESERVED);
		if (proc_resource_next_account_id == 0 ||
		    proc_resource_next_account_id ==
			    RESOURCE_LIMIT_UNBOUNDED) {
			p = 0;
			goto fail;
		}
		proc_resource_limits(&limits);
		if (resource_account_create(
			    RESOURCE_ACCOUNT_EXEC,
			    proc_resource_next_account_id++, grants, &limits,
			    &account) < 0) {
			p = 0;
			goto fail;
		}
		proc_resource_domain_clear(domain);
		domain->used = 1;
		domain->account = account;
		account_created = 1;
	} else {
		domain = &proc_resource_domains[domain_id];
		account = domain->account;
	}
	if (resource_account_member_acquire(account) < 0) {
		p = 0;
		goto fail;
	}
	member_acquired = 1;
	if (resource_reserve_many(
		    account,
		    reserved ? RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY,
		    requests, sizeof(requests) / sizeof(requests[0]),
		    &reservation) < 0) {
		p = 0;
		goto fail;
	}
	if (resource_reservation_commit(&reservation) < 0)
		panic("process resource commit");
	p->resource_account = account;
	p->resource_domain_id = domain_id;
	p->storage_principal_id = storage_principal;
	p->resource_slot_reserved = reserved;
	p->resource_domain_admin = admission == PROC_ADMIT_BOOT;
	p->threads[0].resource_account = account;
	p->threads[0].resource_domain_id = domain_id;
	p->threads[0].resource_slot_reserved = reserved;
	p->threads[0].resource_slot_charged = 1;
	p->state = P_USED;
	intr_restore(enabled);
	return p;
fail:
	if (member_acquired &&
	    resource_account_member_release(account,
					    account_created) < 0)
		panic("process resource member rollback");
	if (account_created &&
	    resource_account_handle_valid(account)) {
		if (resource_account_close(account) < 0)
			panic("process resource account rollback");
	}
	if (account_created &&
	    resource_account_state_get(account) ==
		    RESOURCE_ACCOUNT_FREE)
		proc_resource_domain_clear(
			&proc_resource_domains[domain_id]);
	intr_restore(enabled);
	return 0;
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
	struct resource_account_handle account;
	struct resource_request request = {
		.kind = RESOURCE_PROCESS,
		.amount = 1,
	};

	if (p == 0)
		goto out;
	for (int tid = 0; tid < NTHREAD; tid++)
		if (p->threads[tid].resource_slot_charged)
			panic("process released with charged thread");
	domain_id = p->resource_domain_id;
	account = p->resource_account;
	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		panic("process resource domain invariant");
	domain = &proc_resource_domains[domain_id];
	if (!domain->used ||
	    !resource_account_handle_equal(domain->account, account))
		panic("process resource count invariant");
	if (resource_release_many(
		    account,
		    p->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
						RESOURCE_CHARGE_ORDINARY,
		    &request, 1) < 0 ||
	    resource_account_member_release(account, 1) < 0)
		panic("process resource release invariant");
	if (resource_account_state_get(account) == RESOURCE_ACCOUNT_FREE)
		proc_resource_domain_clear(domain);
	p->resource_account = resource_account_none();
	p->resource_domain_id = -1;
	p->storage_principal_id = FS_OWNER_NONE;
	p->resource_slot_reserved = 0;
	p->resource_domain_admin = 0;
out:
	intr_restore(enabled);
}

// 唯一打开文件对象计费到创建者的固定准入类；fork 继承及阻塞调用引用共享该计费，
// 最后一次 fileclose 退款。
int proc_file_slots_reserve(struct proc *owner, uint count,
			    struct resource_account_handle *account,
			    int *reserved)
{
	struct proc_resource_domain *domain;
	int enabled = intr_save();
	int result = -1;
	int id;
	int use_reserve;
	struct resource_request request = {
		.kind = RESOURCE_FILE_OBJECT,
		.amount = count,
	};
	struct resource_reservation reservation;

	if (!proc_teardown_live(owner) || account == 0 || reserved == 0 ||
	    count == 0)
		goto out;
	id = owner->resource_domain_id;
	if (id < 0 || id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	domain = &proc_resource_domains[id];
	if (!domain->used ||
	    !resource_account_handle_equal(
		    domain->account, owner->resource_account))
		goto out;
	use_reserve = owner->resource_slot_reserved != 0;
	if (resource_reserve_many(
		    domain->account,
		    use_reserve ? RESOURCE_CHARGE_RESERVED :
				  RESOURCE_CHARGE_ORDINARY,
		    &request, 1, &reservation) < 0)
		goto out;
	if (resource_reservation_commit(&reservation) < 0)
		panic("file resource commit");
	*account = domain->account;
	*reserved = use_reserve;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

void proc_file_slot_release(struct resource_account_handle account,
			    int reserved)
{
	int enabled = intr_save();
	struct resource_request request = {
		.kind = RESOURCE_FILE_OBJECT,
		.amount = 1,
	};

	if (resource_release_many(
		    account,
		    reserved ? RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY,
		    &request, 1) < 0)
		panic("file resource release invariant");
	proc_resource_account_reap(account);
	intr_restore(enabled);
}

static void proc_child_state_reset(struct proc *p)
{
	p->live_child_count = 0;
	p->child_exit_head = 0;
	p->child_exit_count = 0;
}

// 子进程关系和完成记录共用一个私有配额；进程表启用后仅可在关中断状态调用。
static void proc_child_counts_validate(struct proc *parent)
{
	if (parent->live_child_count > CHILD_EXIT_CAP ||
	    parent->child_exit_count >
		    CHILD_EXIT_CAP - parent->live_child_count)
		panic("child completion quota");
}

static uint proc_child_exit_index(struct proc *parent, uint offset)
{
	uint index = parent->child_exit_head + offset;

	if (index >= CHILD_EXIT_CAP)
		index -= CHILD_EXIT_CAP;
	return index;
}

static int proc_child_has_capacity(struct proc *parent)
{
	int enabled = intr_save();
	int available;

	proc_child_counts_validate(parent);
	available = parent->live_child_count + parent->child_exit_count <
		    CHILD_EXIT_CAP;
	intr_restore(enabled);
	return available;
}

// 派生过程完成全部可能失败的步骤后，才绑定子进程所有权。
// 存活关系预留对应的完成环槽。
static int proc_child_bind(struct proc *parent, struct proc *child)
{
	int enabled = intr_save();
	int bound = -1;

	if (!proc_teardown_live(parent) || !proc_teardown_live(child) ||
	    child->parent != 0)
		goto out;
	proc_child_counts_validate(parent);
	if (parent->live_child_count + parent->child_exit_count >=
	    CHILD_EXIT_CAP)
		goto out;
	parent->live_child_count++;
	child->parent = parent;
	bound = 0;
out:
	intr_restore(enabled);
	return bound;
}

// 复用可执行进程槽前先发布等待结果。
static void proc_child_publish_exit(struct proc *child)
{
	struct proc *parent;
	struct child_exit *completion;
	int enabled = intr_save();
	uint tail;

	if (child == 0)
		panic("null child publication");
	if ((parent = child->parent) == 0)
		goto out;
	proc_child_counts_validate(parent);
	if (parent->live_child_count == 0 ||
	    parent->child_exit_count >= CHILD_EXIT_CAP)
		panic("child completion publication");
	tail = proc_child_exit_index(parent, parent->child_exit_count);
	completion = &parent->child_exits[tail];
	completion->pid = child->pid;
	completion->exit_code = (int)child->exit_code;
	parent->live_child_count--;
	parent->child_exit_count++;
	child->parent = 0;
	wait_queue_wake_all(&parent->child_waiters);
out:
	intr_restore(enabled);
}

// 复用 proc 槽前回滚未发布的子进程关系。
static void proc_child_unbind(struct proc *child)
{
	struct proc *parent;
	int enabled = intr_save();

	if (child == 0 || (parent = child->parent) == 0)
		goto out;
	proc_child_counts_validate(parent);
	if (parent->live_child_count == 0)
		panic("child relationship rollback");
	parent->live_child_count--;
	child->parent = 0;
	wait_queue_wake_all(&parent->child_waiters);
out:
	intr_restore(enabled);
}

// 有结果返回 1，匹配子进程仍存活返回 0，无匹配子进程返回 -1；须关中断调用。
static int proc_child_wait_result(struct proc *parent, int pid,
				  int *child_pid, int *exit_code)
{
	struct child_exit completion;
	uint selected = 0;

	proc_child_counts_validate(parent);
	if (pid <= 0) {
		if (parent->child_exit_count == 0)
			return parent->live_child_count > 0 ? 0 : -1;
		completion = parent->child_exits[parent->child_exit_head];
		parent->child_exit_head++;
		if (parent->child_exit_head == CHILD_EXIT_CAP)
			parent->child_exit_head = 0;
		parent->child_exit_count--;
		if (parent->child_exit_count == 0)
			parent->child_exit_head = 0;
	} else {
		for (; selected < parent->child_exit_count; selected++) {
			uint index = proc_child_exit_index(parent, selected);

			if (parent->child_exits[index].pid == pid)
				break;
		}
		if (selected == parent->child_exit_count) {
			for (struct proc *child = pool;
			     child < &pool[NPROC]; child++)
				if (child->parent == parent && child->pid == pid)
					return 0;
			return -1;
		}
		completion = parent->child_exits[
			proc_child_exit_index(parent, selected)];
		for (; selected + 1 < parent->child_exit_count; selected++) {
			uint current = proc_child_exit_index(parent, selected);
			uint next = proc_child_exit_index(parent, selected + 1);

			parent->child_exits[current] = parent->child_exits[next];
		}
		parent->child_exit_count--;
		if (parent->child_exit_count == 0)
			parent->child_exit_head = 0;
	}
	*child_pid = completion.pid;
	*exit_code = completion.exit_code;
	return 1;
}

// child->parent 是唯一存活关系；父进程退出时在一次关中断操作中结算全部边并丢弃私有完成项。
static void proc_orphan_children(struct proc *parent)
{
	int enabled = intr_save();
	uint detached = 0;

	proc_child_counts_validate(parent);
	for (struct proc *child = pool; child < &pool[NPROC]; child++) {
		if (child == parent || child->parent != parent)
			continue;
		child->parent = 0;
		detached++;
	}
	if (detached != parent->live_child_count)
		panic("orphan child relationship");
	proc_child_state_reset(parent);
	intr_restore(enabled);
}

// initialize the proc table at boot time.
void proc_init()
{
	struct proc *p;
	scheduler_queues_init();
	proc_resource_init();
	filepool_init();
	kernel_stack_live_slots = 0;
	kernel_stack_live_ordinary = 0;
	kernel_stack_live_reserved = 0;
	thread_identity_next_generation = 0;
	for (p = pool; p < &pool[NPROC]; p++) {
		p->state = P_UNUSED;
		p->parent = 0;
		p->resource_account = resource_account_none();
		p->resource_domain_id = -1;
		p->storage_principal_id = FS_OWNER_NONE;
		p->resource_slot_reserved = 0;
		p->resource_domain_admin = 0;
		p->heap_base = 0;
		p->heap_break = 0;
		agent_context_free(p);
		p->teardown_state = PROC_TEARDOWN_RECYCLED;
		p->teardown_owner_tid = PROC_TEARDOWN_OWNER_NONE;
		p->vm_snapshot_depth = 0;
		p->vm_snapshot_owner_tid = -1;
		proc_child_state_reset(p);
		for (int tid = 0; tid < NTHREAD; ++tid) {
			struct thread *t = &p->threads[tid];
			kernel_work_reset(t);
			t->state = T_UNUSED;
			t->tid = -1;
			t->identity_generation = 0;
			t->process = p;
			t->ustack = 0;
			t->kstack = kernel_stack_base(p - pool, tid);
			t->kstack_state = KSTACK_NONE;
			t->trapframe = 0;
			t->wait_channel = 0;
			t->wait_next = 0;
			t->wait_reason = WAIT_REASON_NONE;
			t->wait_key = 0;
			t->wait_interrupted = 0;
			t->wait_interruptible = 0;
			t->on_run_queue = 0;
			t->run_queue_agent = -1;
			t->resource_account = resource_account_none();
			t->resource_domain_id = -1;
			t->resource_slot_reserved = 0;
			t->resource_slot_charged = 0;
			t->agent_wait_deadline = 0;
			t->agent_wait_deadline_valid = 0;
			t->agent_observe_suppress_depth = 0;
			t->agent_timeline_wait_state = 0;
			t->agent_loop_state = AGENT_LOOP_NONE;
			memset(t->fd_delegate_ticket, 0,
			       sizeof(t->fd_delegate_ticket));
		}
	}
	idle.kstack = (uint64)boot_stack_top;
	idle.kstack_state = KSTACK_LIVE;
	current_thread = &idle;
	// for procid() and threadid()
	idle.process = pool;
	idle.tid = -1;
	idle.identity_generation = 0;
	idle.wait_channel = 0;
	idle.wait_next = 0;
	idle.wait_reason = WAIT_REASON_NONE;
	idle.wait_key = 0;
	idle.wait_interrupted = 0;
	idle.wait_interruptible = 0;
	idle.on_run_queue = 0;
	idle.run_queue_agent = -1;
	idle.resource_account = resource_account_none();
	idle.resource_domain_id = -1;
	idle.resource_slot_reserved = 0;
	idle.resource_slot_charged = 0;
	idle.agent_wait_deadline = 0;
	idle.agent_wait_deadline_valid = 0;
	idle.agent_observe_suppress_depth = 0;
	idle.agent_timeline_wait_state = 0;
	idle.agent_loop_state = AGENT_LOOP_NONE;
	kernel_work_reset(&idle);
}

int allocpid()
{
	static uint64 next_pid = 1;
	int enabled = intr_save();
	int pid = -1;

	/* 传统 PID ABI 保持不复用；耗尽后拒绝新进程。 */
	if (next_pid <= 0x7fffffffULL)
		pid = (int)next_pid++;
	intr_restore(enabled);
	return pid;
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

static void scheduler_activate_domain(int domain_id)
{
	struct proc_resource_domain *domain;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		scheduler_invariant_stop(1);
	domain = &proc_resource_domains[domain_id];
	if (scheduler_domain_task_count(domain) == 0 ||
	    scheduler_domain_active[domain_id])
		return;
	if (queue_push_locked(&scheduler_active_domains, domain_id) < 0)
		scheduler_invariant_stop(2);
	scheduler_domain_active[domain_id] = 1;
}

static void scheduler_deactivate_domain(int domain_id)
{
	int removed;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		scheduler_invariant_stop(3);
	if (!scheduler_domain_active[domain_id])
		return;
	removed = queue_remove_locked(&scheduler_active_domains, domain_id);
	if (removed != 1)
		scheduler_invariant_stop(4);
	scheduler_domain_active[domain_id] = 0;
}

static void scheduler_drop_queued_thread(struct proc_resource_domain *domain,
					 struct thread *t)
{
	if (domain == 0 || t == 0 || !t->on_run_queue)
		scheduler_invariant_stop(5);
	t->on_run_queue = 0;
	t->run_queue_agent = -1;
}

static void scheduler_validate_queued_thread(struct thread *t, int domain_id)
{
	if (t == 0 || !t->on_run_queue || !t->resource_slot_charged ||
	    (t->run_queue_agent != 0 && t->run_queue_agent != 1) ||
	    t->resource_domain_id != domain_id || t->process == 0 ||
	    t->process->state != P_USED ||
	    t->process->resource_domain_id != domain_id ||
	    !resource_account_handle_equal(
		    t->resource_account, t->process->resource_account) ||
	    !resource_account_handle_equal(
		    t->resource_account,
		    proc_resource_domains[domain_id].account))
		scheduler_invariant_stop(7);
}

static int scheduler_lane_first_schedulable(struct scheduler_lane *lane,
					    int domain_id, int *previous_task)
{
	struct proc_resource_domain *domain =
		&proc_resource_domains[domain_id];
	int task_id = lane->head;
	int previous = -1;
	int remaining = lane->count;

	while (remaining-- > 0) {
		struct thread *t;
		int next;

		if (task_id < 0 || task_id >= NPROC * NTHREAD)
			scheduler_invariant_stop(25);
		next = scheduler_task_next[task_id];
		t = id_to_task(task_id);
		scheduler_validate_queued_thread(t, domain_id);
		if (t->state != RUNNABLE) {
			scheduler_lane_remove_after(lane, previous, task_id);
			scheduler_drop_queued_thread(domain, t);
			task_id = next;
			continue;
		}
		if (proc_vm_snapshot_schedulable(t)) {
			*previous_task = previous;
			return task_id;
		}
		previous = task_id;
		task_id = next;
	}
	*previous_task = -1;
	return -1;
}

static struct thread *fetch_domain_fifo(int domain_id)
{
	struct proc_resource_domain *domain =
		&proc_resource_domains[domain_id];
	struct scheduler_lane *lane = &domain->scheduler_normal;
	int previous;
	int task_id = scheduler_lane_first_schedulable(
		lane, domain_id, &previous);
	struct thread *t;

	if (task_id < 0)
		return 0;
	t = id_to_task(task_id);
	scheduler_lane_remove_after(lane, previous, task_id);
	scheduler_drop_queued_thread(domain, t);
	return t;
}

static int scheduler_find_best_agent(int domain_id, int *first_agent,
				     int *first_previous,
				     int *best_previous,
				     int *eligible_agents)
{
	struct proc_resource_domain *domain =
		&proc_resource_domains[domain_id];
	struct scheduler_lane *lane = &domain->scheduler_agent;
	int task_id = lane->head;
	int previous = -1;
	int remaining = lane->count;
	int best_agent = -1;

	*first_agent = -1;
	*first_previous = -1;
	*best_previous = -1;
	*eligible_agents = 0;
	while (remaining-- > 0) {
		struct thread *candidate;
		int next;

		if (task_id < 0 || task_id >= NPROC * NTHREAD)
			scheduler_invariant_stop(26);
		next = scheduler_task_next[task_id];
		candidate = id_to_task(task_id);
		scheduler_validate_queued_thread(candidate, domain_id);
		if (candidate->state != RUNNABLE) {
			scheduler_lane_remove_after(lane, previous, task_id);
			scheduler_drop_queued_thread(domain, candidate);
			task_id = next;
			continue;
		}
		if (proc_vm_snapshot_schedulable(candidate)) {
			(*eligible_agents)++;
			if (*first_agent < 0) {
				*first_agent = task_id;
				*first_previous = previous;
			}
			if (best_agent < 0 ||
			    agent_sched_better(candidate,
					       id_to_task(best_agent))) {
				best_agent = task_id;
				*best_previous = previous;
			}
		}
		previous = task_id;
		task_id = next;
	}
	return best_agent;
}

static struct thread *fetch_domain_best(int domain_id)
{
	struct proc_resource_domain *domain =
		&proc_resource_domains[domain_id];
	int first_normal;
	int normal_previous;
	int first_agent;
	int first_agent_previous;
	int best_agent_previous;
	int eligible_agents;
	int best_agent;
	int selected;
	int selected_previous;
	int forced_fifo = 0;
	struct thread *best_task;

	first_normal = scheduler_lane_first_schedulable(
		&domain->scheduler_normal, domain_id, &normal_previous);
	best_agent = scheduler_find_best_agent(
		domain_id, &first_agent, &first_agent_previous,
		&best_agent_previous, &eligible_agents);
	/* 普通线程保持 FIFO；Agent 仅在自己的 lane 内评分，两个突发上限分别保证跨类和类内进展。 */
	if (best_agent < 0) {
		selected = first_normal;
		selected_previous = normal_previous;
	} else if (first_normal >= 0 &&
		   domain->scheduler_agent_burst >=
			   AGENT_SCHED_MAX_AGENT_BURST) {
		selected = first_normal;
		selected_previous = normal_previous;
	} else if (domain->scheduler_score_burst >=
		   AGENT_SCHED_MAX_AGENT_BURST) {
		selected = first_agent;
		selected_previous = first_agent_previous;
		forced_fifo = 1;
	} else if (first_normal < 0) {
		selected = best_agent;
		selected_previous = best_agent_previous;
	} else if (agent_sched_better(id_to_task(best_agent),
				      id_to_task(first_normal))) {
		selected = best_agent;
		selected_previous = best_agent_previous;
	} else {
		selected = first_normal;
		selected_previous = normal_previous;
	}
	if (selected < 0) {
		domain->scheduler_agent_burst = 0;
		domain->scheduler_score_burst = 0;
		return 0;
	}
	best_task = id_to_task(selected);
	scheduler_validate_queued_thread(best_task, domain_id);
	scheduler_lane_remove_after(best_task->run_queue_agent ?
					    &domain->scheduler_agent :
					    &domain->scheduler_normal,
				    selected_previous, selected);
	scheduler_drop_queued_thread(domain, best_task);
	if (forced_fifo ||
	    (eligible_agents <= 1 && first_normal < 0) ||
	    eligible_agents == 0)
		domain->scheduler_score_burst = 0;
	else
		domain->scheduler_score_burst++;
	if (best_task->process->is_agent && first_normal >= 0)
		domain->scheduler_agent_burst++;
	else
		domain->scheduler_agent_burst = 0;
	return best_task;
}

struct thread *fetch_task()
{
	int enabled = intr_save();
	int remaining = scheduler_active_domains.count;

	while (remaining-- > 0) {
		int domain_id = queue_pop_locked(&scheduler_active_domains);
		struct proc_resource_domain *domain;
		struct thread *t;

		if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP ||
		    !scheduler_domain_active[domain_id])
			scheduler_invariant_stop(11);
		scheduler_domain_active[domain_id] = 0;
		domain = &proc_resource_domains[domain_id];
		if (!domain->used || scheduler_domain_task_count(domain) <= 0)
			scheduler_invariant_stop(12);
		t = domain->scheduler_agent.count > 0 ?
			    fetch_domain_best(domain_id) :
			    fetch_domain_fifo(domain_id);
		scheduler_activate_domain(domain_id);
		if (t != 0) {
			int tid = t->tid;
			int pid = t->process->pid;

			intr_restore(enabled);
			tracef("fetch domain %d(pid=%d, tid=%d, addr=%p)",
			       domain_id, pid, tid, (uint64)t);
			return t;
		}
	}
	intr_restore(enabled);
	debugf("No task to fetch\n");
	return 0;
}

void add_task(struct thread *t)
{
	int enabled = intr_save();
	struct proc_resource_domain *domain;
	struct scheduler_lane *lane;
	int domain_id;

	if (t == 0 || t->process == 0 || t->process < pool ||
	    t->process >= &pool[NPROC] ||
	    t->tid < 0 || t->tid >= NTHREAD || t->state != RUNNABLE ||
	    t->on_run_queue) {
		intr_restore(enabled);
		return;
	}
	if (!t->resource_slot_charged)
		scheduler_invariant_stop(13);
	domain_id = t->resource_domain_id;
	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP ||
	    t->process->resource_domain_id != domain_id ||
	    !proc_resource_domains[domain_id].used ||
	    !resource_account_handle_equal(
		    t->resource_account, t->process->resource_account) ||
	    !resource_account_handle_equal(
		    t->resource_account,
		    proc_resource_domains[domain_id].account))
		scheduler_invariant_stop(14);
	domain = &proc_resource_domains[domain_id];
	int task_id = task_to_id(t);
	int pid = t->process->pid;
	agent_sched_on_enqueue(t);
	t->run_queue_agent = t->process->is_agent != 0;
	lane = t->run_queue_agent ? &domain->scheduler_agent :
				   &domain->scheduler_normal;
	if (scheduler_domain_task_count(domain) >=
	    THREAD_RESOURCE_DOMAIN_QUEUE_LIMIT)
		scheduler_invariant_stop(15);
	scheduler_lane_push(lane, task_id);
	t->on_run_queue = 1;
	scheduler_activate_domain(domain_id);
	intr_restore(enabled);
	tracef("add domain %d index %d(pid=%d, tid=%d, addr=%p)",
	       domain_id, task_id, pid, t->tid, (uint64)t);
}

static void remove_task(struct thread *t)
{
	int enabled = intr_save();
	struct proc_resource_domain *domain;
	struct scheduler_lane *lane;
	int domain_id;

	if (t == 0 || t->process == 0 || t->process < pool ||
	    t->process >= &pool[NPROC] ||
	    t->tid < 0 || t->tid >= NTHREAD) {
		intr_restore(enabled);
		return;
	}
	if (!t->on_run_queue) {
		intr_restore(enabled);
		return;
	}
	domain_id = t->resource_domain_id;
	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		scheduler_invariant_stop(17);
	domain = &proc_resource_domains[domain_id];
	lane = t->run_queue_agent ? &domain->scheduler_agent :
				   &domain->scheduler_normal;
	int task_id = task_to_id(t);
	int previous = -1;
	int current = lane->head;
	int remaining = lane->count;

	while (remaining-- > 0 && current != task_id) {
		if (current < 0 || current >= NPROC * NTHREAD)
			scheduler_invariant_stop(18);
		previous = current;
		current = scheduler_task_next[current];
	}
	if (current != task_id)
		scheduler_invariant_stop(18);
	scheduler_lane_remove_after(lane, previous, task_id);
	scheduler_drop_queued_thread(domain, t);
	if (scheduler_domain_task_count(domain) == 0)
		scheduler_deactivate_domain(domain_id);
	else
		scheduler_activate_domain(domain_id);
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
	int pid;
	struct proc *p;

	p = proc_resource_reserve(parent, admission);
	if (p == 0)
		return 0;
	pid = allocpid();
	if (pid <= 0) {
		int enabled;

		proc_thread_resource_release(&p->threads[0]);
		proc_resource_release(p);
		enabled = intr_save();
		p->state = P_UNUSED;
		intr_restore(enabled);
		return 0;
	}
	// init proc
	p->pid = pid;
	wait_queue_init(&p->child_waiters, WAIT_REASON_CHILD);
	wait_queue_init(&p->thread_exit_waiters, WAIT_REASON_THREAD_EXIT);
	wait_queue_init(&p->agent_event_waiters, WAIT_REASON_EVENT);
	wait_queue_init(&p->agent_timeline_waiters, WAIT_REASON_TIMELINE);
	agent_lifecycle_context_lane_init(p);
	p->max_page = 0;
	p->heap_base = 0;
	p->heap_break = 0;
	p->parent = NULL;
	p->exit_code = 0;
	p->teardown_state = PROC_TEARDOWN_LIVE;
	p->teardown_owner_tid = PROC_TEARDOWN_OWNER_NONE;
	p->agent_control_state = AGENT_CONTROL_OPEN;
	p->vm_snapshot_depth = 0;
	p->vm_snapshot_owner_tid = -1;
	proc_child_state_reset(p);
	p->exec_dev = 0;
	p->exec_inum = 0;
	p->exec_flags = 0;
	p->exec_generation = 0;
	p->exec_role_mask = 0;
	p->exec_layout_version = 0;
	p->exec_rw_offset = 0;
	if (p->workflow_lifecycle_charged)
		panic("stale workflow lifecycle charge");
	p->workflow_lifecycle_id = WORKFLOW_LIFECYCLE_ID_NONE;
	p->workflow_lifecycle_generation = 0;
	p->workflow_lifecycle_charged = 0;
	vfs_proc_reset(p);
	p->pagetable = uvmcreate_account(
		p->resource_account,
		p->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
					    RESOURCE_CHARGE_ORDINARY);
	if (p->pagetable == 0) {
		freeproc(p);
		return 0;
	}
	if (thread_trapframe_acquire(&p->threads[0]) < 0) {
		freeproc(p);
		return 0;
	}
	if (kernel_stack_acquire(&p->threads[0]) < 0) {
		freeproc(p);
		return 0;
	}
	kernel_stack_reset_slot(p, 0);
	memset((void *)p->files, 0, sizeof(struct file *) * FD_BUFFER_SIZE);
	p->next_mutex_id = 0;
	p->next_semaphore_id = 0;
	p->next_condvar_id = 0;
	memset(p->syscall_count, 0, sizeof(p->syscall_count));
	agent_proc_prepare(p);
	return p;
}

// 内核启动分配是全部签名资源域的根。
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
	return p->threads[tid].trapframe;
}

static uint64 get_thread_ustack_base_va(struct thread *t)
{
	return t->ustack;
}

// 线程 ID 标识内核槽而非用户栈虚址；非主线程 fork 后，子进程须在发起者栈的原虚址恢复，
// 因为已保存帧指针和栈内容可能引用该地址。
static uint64 proc_alloc_thread_ustack(struct proc *p, struct thread *owner)
{
	for (int slot = 0; slot < NTHREAD; slot++) {
		uint64 candidate = p->ustack_base + slot * USTACK_SIZE;
		int in_use = 0;

		// fork 子进程保留父地址空间全部页面；无属主的兄弟栈仍可能被存活线程引用，
		// 因此仅可复用完全未映射的槽。
		for (uint64 offset = 0; offset < USTACK_SIZE; offset += PGSIZE)
			if (walkaddr(p->pagetable, candidate + offset) != 0) {
				in_use = 1;
				break;
			}
		if (in_use)
			continue;
		for (int tid = 0; tid < NTHREAD; tid++) {
			struct thread *t = &p->threads[tid];

			if (t == owner || t->state == T_UNUSED)
				continue;
			if (t->ustack == candidate) {
				in_use = 1;
				break;
			}
		}
		if (!in_use)
			return candidate;
	}
	return 0;
}

int allocthread(struct proc *p, uint64 entry, int alloc_user_res)
{
	int tid;
	struct thread *t;
	uint64 old_max_page;
	uint64 user_stack;
	int enabled;
	int new_charge = 0;
	int user_stack_mapped = 0;

	if (p == 0)
		return -1;
	enabled = intr_save();
	if (!proc_teardown_live(p)) {
		intr_restore(enabled);
		return -1;
	}
	old_max_page = p->max_page;
	for (tid = 0; tid < NTHREAD; ++tid) {
		t = &p->threads[tid];
		if (t->state == T_UNUSED) {
			goto found;
		}
	}
	intr_restore(enabled);
	return -1;

found:
	user_stack = alloc_user_res != 0 ?
			     proc_alloc_thread_ustack(p, t) :
			     p->ustack_base;
	if (user_stack == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (!t->resource_slot_charged) {
		int domain_id = p->resource_domain_id;

		if (domain_id < 0 ||
		    domain_id >= PROC_RESOURCE_DOMAIN_CAP ||
		    proc_thread_resource_charge_locked(
			    t, domain_id,
			    p->resource_slot_reserved) < 0) {
			intr_restore(enabled);
			return -1;
		}
		new_charge = 1;
	} else if (t->resource_domain_id != p->resource_domain_id ||
		   !resource_account_handle_equal(
			   t->resource_account, p->resource_account) ||
		   t->resource_slot_reserved != p->resource_slot_reserved) {
		panic("precharged thread ownership invariant");
	}
	t->tid = tid;
	thread_identity_activate(t);
	t->state = T_USED;
	t->process = p;
	t->exit_code = 0;
	t->wait_channel = 0;
	t->wait_next = 0;
	t->wait_reason = WAIT_REASON_NONE;
	t->wait_key = 0;
	t->wait_interrupted = 0;
	t->wait_interruptible = 0;
	t->on_run_queue = 0;
	t->run_queue_agent = -1;
	t->ustack = user_stack;
	t->agent_wait_deadline = 0;
	t->agent_wait_deadline_valid = 0;
	t->agent_observe_suppress_depth = 0;
	t->agent_timeline_wait_state = 0;
	memset(t->fd_delegate_ticket, 0, sizeof(t->fd_delegate_ticket));
	agent_thread_runtime_transition(t, AGENT_THREAD_RUNTIME_ACTIVATE);
	intr_restore(enabled);
	// kernel stack
	if (kernel_stack_acquire(t) < 0)
		goto fail_slot;
	kernel_stack_reset_slot(p, tid);
	// user stack
	if (alloc_user_res != 0) {
		if (uvmmap(p->pagetable, t->ustack, USTACK_SIZE / PAGE_SIZE,
			   PTE_U | PTE_R | PTE_W) < 0)
			goto fail_slot;
		user_stack_mapped = 1;
		p->max_page =
			MAX(p->max_page,
			    PGROUNDUP(t->ustack + USTACK_SIZE - 1) / PAGE_SIZE);
	}
	// trap frame
	if (thread_trapframe_acquire(t) < 0)
		goto fail_slot;
	kernel_work_reset(t);
	if (mappages(p->pagetable, get_thread_trapframe_va(tid), TRAP_PAGE_SIZE,
		     (uint64)t->trapframe, PTE_R | PTE_W) < 0)
		goto fail_slot;
	t->trapframe->sp = t->ustack + USTACK_SIZE;
	t->trapframe->epc = entry;
	//task context
	memset(&thread_trap_cold(t)->context, 0,
	       sizeof(thread_trap_cold(t)->context));
	thread_trap_cold(t)->context.ra = (uint64)usertrapret;
	thread_trap_cold(t)->context.sp = t->kstack + KSTACK_SIZE;
	// we do not add thread to scheduler immediately
	debugf("allocthread p: %d, o: %d, t: %d, e: %p, sp: %p, spp: %p",
	       p->pid, (p - pool), t->tid, entry, t->ustack,
	       walkaddr(p->pagetable, t->ustack));
	return tid;

fail_slot:
	agent_thread_runtime_transition(t, AGENT_THREAD_RUNTIME_RELEASE);
	agent_observe_thread_reset(t);
	if (t->trapframe != 0) {
		uvmunmap(p->pagetable, get_thread_trapframe_va(tid), 1, 0);
		thread_trapframe_release(t);
	}
	if (user_stack_mapped)
		uvmunmap(p->pagetable, t->ustack,
			 USTACK_SIZE / PAGE_SIZE, 1);
	p->max_page = old_max_page;
	kernel_stack_release_inactive(t);
	t->state = T_UNUSED;
	t->tid = -1;
	t->identity_generation = 0;
	t->ustack = 0;
	t->run_queue_agent = -1;
	t->agent_timeline_wait_state = 0;
	if (new_charge)
		proc_thread_resource_release(t);
	return -1;
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
	if (t->kstack_state != KSTACK_REAP)
		panic("dying thread kernel stack");
	if (t->agent_wait_deadline_valid || t->agent_wait_deadline != 0 ||
	    t->agent_loop_state != AGENT_LOOP_NONE ||
	    t->agent_observe_suppress_depth != 0 ||
	    t->agent_timeline_wait_state != 0)
		panic("dying thread Agent runtime");
	kernel_stack_release_inactive(t);
	if (t->trapframe == 0)
		panic("dying thread trapframe");
	thread_trapframe_release(t);
	if (t->trapframe != 0)
		panic("dying thread trapframe release");
	t->state = EXITED;
	proc_thread_resource_release(t);
	wait_queue_wake_key_all(&p->thread_exit_waiters,
				 t->identity_generation);
	if (tid != p->teardown_owner_tid) {
		wait_queue_wake_key_all(&p->thread_exit_waiters, 0);
		return;
	}
	if (p->teardown_state != PROC_TEARDOWN_HANDOFF)
		panic("teardown scheduler handoff");
	proc_child_publish_exit(p);
	proc_teardown_advance(p, PROC_TEARDOWN_HANDOFF,
			      PROC_TEARDOWN_PUBLISHED);
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
		/* 可运行内核线程可能反复 yield 而不回用户态；每轮调度先投递设备和时钟 IRQ，
		 * 避免内核轮询循环饿死 I/O 等待者。 */
		current_thread = &idle;
		set_kerneltrap();
		intr_delivery_window();
		t = fetch_task();
		/* 仅在无可运行用户线程时轮询回写。 */
		if (t == NULL && fs_epoch_should_commit() &&
		    fs_epoch_request_begin() == 0) {
			if (fs_epoch_should_commit())
				(void)fs_epoch_commit();
			fs_epoch_request_end();
			t = fetch_task();
		}
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
			kernel_stack_assert_quiescent();
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
		swtch(&scheduler_context, &thread_trap_cold(t)->context);
		kernel_stack_check(t);
		current_thread = &idle;
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
	swtch(&thread_trap_cold(t)->context, &scheduler_context);
}

// Give up the CPU for one scheduling round.
void yield()
{
	agent_sched_on_yield(current_thread);
	current_thread->state = RUNNABLE;
	add_task(current_thread);
	sched();
}

int scheduler_has_runnable_peer(void)
{
	int enabled = intr_save();
	int present = scheduler_active_domains.count != 0;

	/* 正在运行的线程不在队列内，因此任一活动域都代表真正的竞争者。 */
	intr_restore(enabled);
	return present;
}

static void freepagetable_cleanup(pagetable_t pagetable, uint64 max_page)
{
	uvmunmap(pagetable, TRAMPOLINE, 1, 0);
	uvmfree_cleanup(pagetable, max_page);
}

// 描述符表属于进程，委派意图属于线程；替换或关闭槽时清除全部线程的对应意图，
// 防止后续对象继承陈旧票据。
static void proc_clear_fd_delegate_tickets(struct proc *p, int fd)
{
	if (p == 0 || fd < 0 || fd >= FD_BUFFER_SIZE)
		return;
	for (int tid = 0; tid < NTHREAD; tid++)
		p->threads[tid].fd_delegate_ticket[fd] = 0;
}

static void proc_clear_all_fd_delegate_tickets(struct proc *p)
{
	if (p == 0)
		return;
	for (int fd = 0; fd < FD_BUFFER_SIZE; fd++)
		proc_clear_fd_delegate_tickets(p, fd);
}

// 打开对象属于工作流凭据而非环境 POSIX 状态；reset 与 exec 共用摘除机制，exec 在 VM 发布门下摘除，
// 恢复中断后才关闭固定引用；成功激活的 exec 保留已委派 pipe。
static void
proc_detach_vfs_scope_fds_locked(struct proc *p,
				 struct file **detached)
{
	uint scope_id;

	if (intr_get())
		panic("exec fd revocation unlocked");
	if (p == 0 || detached == 0)
		return;
	scope_id = p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
			   p->vfs_scope_id : p->vfs_pending_scope_id;
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return;
	proc_clear_all_fd_delegate_tickets(p);
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		struct file *f = p->files[i];

		if (f == 0 || (!fd_is_reserved(f) && f->type == FD_STDIO))
			continue;
		p->files[i] = 0;
		if (!fd_is_reserved(f))
			detached[i] = f;
	}
}

static void proc_close_detached_files(struct file **files)
{
	for (int i = 0; i < FD_BUFFER_SIZE; i++)
		if (files[i] != 0)
			fileclose(files[i]);
}

void proc_revoke_vfs_scope_fds(struct proc *p)
{
	struct file *files[FD_BUFFER_SIZE];
	int enabled;

	memset(files, 0, sizeof(files));
	if (p == 0)
		return;
	enabled = intr_save();
	proc_detach_vfs_scope_fds_locked(p, files);
	intr_restore(enabled);
	proc_close_detached_files(files);
}

static int
proc_user_image_trapframe_valid(struct proc *p, struct user_image *image)
{
	pte_t *pte;
	uint64 flags;
	uint64 expected_heap;

	if (p == 0 || image == 0 || image->pagetable == 0 ||
	    p->threads[0].trapframe == 0)
		return 0;
	if (image->ustack_base > USER_HEAP_LIMIT - PAGE_SIZE -
				       NTHREAD * USTACK_SIZE)
		return 0;
	expected_heap = image->ustack_base + NTHREAD * USTACK_SIZE +
			PAGE_SIZE;
	if ((image->ustack_base % PGSIZE) != 0 ||
	    image->heap_base != expected_heap ||
	    image->heap_break != image->heap_base ||
	    image->heap_base > USER_HEAP_LIMIT - PAGE_SIZE)
		return 0;
	pte = walk(image->pagetable, TRAPFRAME, 0);
	if (pte == 0 || PTE2PA(*pte) != (uint64)p->threads[0].trapframe)
		return 0;
	flags = PTE_FLAGS(*pte);
	return (flags & (PTE_V | PTE_R | PTE_W)) ==
		       (PTE_V | PTE_R | PTE_W) &&
	       (flags & (PTE_U | PTE_X)) == 0;
}

static int
proc_image_install_state_valid_locked(struct proc *p,
				      enum proc_image_install_mode mode)
{
	struct thread *main = &p->threads[0];

	if (intr_get() || main->process != p || main->on_run_queue)
		return 0;
	if (mode == PROC_IMAGE_INSTALL_LIVE_EXEC) {
		if (main != curr_thread() || main->state != RUNNING ||
		    main->tid != 0 || main->identity_generation == 0)
			return 0;
	} else if (mode == PROC_IMAGE_INSTALL_BOOTSTRAP) {
		if (main == curr_thread() || main->state != T_UNUSED ||
		    main->tid != -1 || main->identity_generation != 0)
			return 0;
	} else {
		return 0;
	}
	for (int tid = 1; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (t->process != p || t->on_run_queue)
			return 0;
		if (mode == PROC_IMAGE_INSTALL_LIVE_EXEC) {
			if (t->state != T_UNUSED && t->state != EXITED)
				return 0;
		} else if (t->state != T_UNUSED || t->tid != -1 ||
			   t->identity_generation != 0) {
			return 0;
		}
	}
	return 1;
}

int proc_install_user_image(struct proc *p, struct user_image *image,
			    struct trapframe *staged,
			    enum proc_image_install_mode mode)
{
	pagetable_t old_pagetable = p->pagetable;
	uint64 old_max_page = p->max_page;
	struct vfs_exec_transition transition;
	struct file *revoked_files[FD_BUFFER_SIZE];
	struct thread *main_thread;
	int live_exec;
	int enabled;

	memset(revoked_files, 0, sizeof(revoked_files));
	if (mode != PROC_IMAGE_INSTALL_BOOTSTRAP &&
	    mode != PROC_IMAGE_INSTALL_LIVE_EXEC)
		return -1;
	live_exec = mode == PROC_IMAGE_INSTALL_LIVE_EXEC;
	/* 凭据或 VM 所有权转移前拒绝畸形镜像。 */
	if (!proc_user_image_trapframe_valid(p, image) ||
	    vfs_proc_exec_prepare(p, image, live_exec, &transition) < 0)
		return -1;
	/* 仅策略明确选择保留可信 Agent 的转换后，Context 才可进入替换页表；
	 * 中止路径经 user_image_discard() 持有别名，PUBLIC 镜像不可见。 */
	if (transition.identity_policy ==
	    VFS_EXEC_IDENTITY_PRESERVE_AGENT) {
		if (agent_alias_exec_context(p, image->pagetable) < 0) {
			vfs_proc_exec_abort(&transition);
			return -1;
		}
		image->shared_base = p->agent_ctx_base;
		image->shared_pages = AGENT_CONTEXT_PAGES;
	}
	/* 镜像构造和凭据准备可 yield；凭据校验、权力撤销、Context 清理、FD 摘除及 VM 指针交换
	 * 组成同一发布边界，清理请求后新身份的任一部分均不可见。 */
	enabled = intr_save();
	if (!proc_teardown_live(p) ||
	    !proc_image_install_state_valid_locked(p, mode) ||
	    sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||
	    vfs_proc_exec_validate_locked(p, &transition) < 0) {
		intr_restore(enabled);
		vfs_proc_exec_abort(&transition);
		return -1;
	}
	if (transition.lifecycle_reserved) {
		/* 保留量发布仍可能失败，但不会改变身份。 */
		if (vfs_proc_exec_commit(p, &transition) < 0) {
			intr_restore(enabled);
			vfs_proc_exec_abort(&transition);
			return -1;
		}
	} else {
		if (transition.identity_policy == VFS_EXEC_IDENTITY_PUBLIC &&
		    agent_exec_public_identity_commit(p) < 0) {
			intr_restore(enabled);
			vfs_proc_exec_abort(&transition);
			return -1;
		}
		if (transition.drop_to_public)
			proc_detach_vfs_scope_fds_locked(p, revoked_files);
		/* 所有不可逆撤销共用该中断保护区。 */
		if (vfs_proc_exec_commit(p, &transition) < 0)
			panic("validated exec credential commit");
	}
	agent_process_image_install_locked(p);
	sync_proc_exec_reset_locked(p, &p->threads[0]);
	p->pagetable = image->pagetable;
	p->max_page = image->max_page;
	p->ustack_base = image->ustack_base;
	p->heap_base = image->heap_base;
	p->heap_break = image->heap_break;
	p->exec_dev = image->exec_dev;
	p->exec_inum = image->exec_inum;
	p->exec_flags = image->exec_flags;
	p->exec_generation = image->exec_generation;
	p->exec_role_mask = image->exec_role_mask;
	p->exec_layout_version = image->exec_layout_version;
	p->exec_rw_offset = image->exec_rw_offset;
	image->pagetable = 0;
	intr_restore(enabled);
	proc_close_detached_files(revoked_files);
	agent_authority_on_exec(p);
	if (live_exec && !p->is_agent)
		proc_resource_drop_admin(p);

	if (!p->threads[0].resource_slot_charged ||
	    p->threads[0].resource_domain_id != p->resource_domain_id ||
	    !resource_account_handle_equal(
		    p->threads[0].resource_account, p->resource_account) ||
	    p->threads[0].resource_slot_reserved !=
		    p->resource_slot_reserved)
		panic("main thread resource invariant");
	if (old_pagetable != 0)
		for (int tid = 0; tid < NTHREAD; tid++)
			uvmunmap(old_pagetable, get_thread_trapframe_va(tid), 1,
				 0);
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *slot = &p->threads[tid];

		agent_observe_thread_reset(slot);
		detach_task(slot);
		if (tid != 0) {
			proc_reset_thread_slot(slot);
			continue;
		}
		if (!live_exec)
			kernel_work_reset(slot);
		slot->state = T_UNUSED;
		slot->tid = -1;
		slot->wait_channel = 0;
		slot->wait_next = 0;
		slot->wait_reason = WAIT_REASON_NONE;
		slot->wait_interrupted = 0;
		slot->wait_interruptible = 0;
		slot->on_run_queue = 0;
		slot->run_queue_agent = -1;
		slot->agent_timeline_wait_state = 0;
		memset(slot->fd_delegate_ticket, 0,
		       sizeof(slot->fd_delegate_ticket));
	}
	main_thread = &p->threads[0];
	if (main_thread->kstack_state != KSTACK_LIVE)
		panic("main thread without kernel stack");
	thread_identity_activate(main_thread);
	main_thread->tid = 0;
	main_thread->state = live_exec ? RUNNING : T_USED;
	main_thread->process = p;
	main_thread->exit_code = 0;
	main_thread->kstack = kernel_stack_base(p - pool, 0);
	main_thread->ustack = p->ustack_base;
	main_thread->trapframe = proc_trapframe(p, 0);
	memmove(main_thread->trapframe, staged, sizeof(*staged));
	if (!live_exec) {
		memset(&thread_trap_cold(main_thread)->context, 0,
		       sizeof(thread_trap_cold(main_thread)->context));
		thread_trap_cold(main_thread)->context.ra = (uint64)usertrapret;
		thread_trap_cold(main_thread)->context.sp =
			main_thread->kstack + KSTACK_SIZE;
	}

	if (old_pagetable != 0) {
		agent_unmap_exec_context(p, old_pagetable);
		uvmunmap(old_pagetable, TRAMPOLINE, 1, 0);
		uvmfree_cleanup(old_pagetable, old_max_page);
	}
	memset(image, 0, sizeof(*image));
	return 0;
}

static void thread_user_vm_release(struct thread *t)
{
	pagetable_t pt;

	if (t == 0 || t->process == 0)
		return;
	pt = t->process->pagetable;
	detach_task(t);
	if (pt != 0 && t->tid >= 0 && t->tid < NTHREAD)
		uvmunmap(pt, get_thread_trapframe_va(t->tid), 1, 0);
	/* 当前线程的冷状态须活到终结清理和最后一次 swtch 完成。 */
	if (t != current_thread)
		thread_trapframe_release(t);
	if (pt == 0 || t->tid < 0 || t->tid >= NTHREAD)
		return;
	if (t->ustack != 0)
		uvmunmap(pt, get_thread_ustack_base_va(t),
			 USTACK_SIZE / PAGE_SIZE, 1);
}

void freethread(struct thread *t)
{
	agent_thread_runtime_transition(t, AGENT_THREAD_RUNTIME_RELEASE);
	agent_observe_thread_reset(t);
	mutex_release_thread_locks(t);
	kernel_work_reset(t);
	thread_user_vm_release(t);
}

static void proc_reset_thread_slot(struct thread *t)
{
	agent_thread_runtime_transition(t, AGENT_THREAD_RUNTIME_RELEASE);
	agent_observe_thread_reset(t);
	kernel_work_reset(t);
	kernel_stack_release_inactive(t);
	thread_trapframe_release(t);
	proc_thread_resource_release(t);
	memset(t->fd_delegate_ticket, 0, sizeof(t->fd_delegate_ticket));
	t->state = T_UNUSED;
	t->tid = -1;
	t->identity_generation = 0;
	t->ustack = 0;
	t->wait_channel = 0;
	t->wait_next = 0;
	t->wait_reason = WAIT_REASON_NONE;
	t->wait_key = 0;
	t->wait_interrupted = 0;
	t->wait_interruptible = 0;
	t->on_run_queue = 0;
	t->run_queue_agent = -1;
	t->resource_account = resource_account_none();
	t->resource_domain_id = -1;
	t->resource_slot_reserved = 0;
	t->resource_slot_charged = 0;
}

static void proc_teardown_advance(struct proc *p,
				  enum proc_teardown_state expected,
				  enum proc_teardown_state next)
{
	int enabled = intr_save();

	if (p == 0 || p->state != P_USED ||
	    p->teardown_state != expected || next != expected + 1)
		panic("process teardown transition");
	p->teardown_state = next;
	intr_restore(enabled);
}

static void proc_teardown_file_progress(
	struct file_close_batch *batch)
{
	if (batch == 0)
		panic("process teardown file progress");
	if (!fs_epoch_request_held())
		panic("process teardown settlement gate");
	fs_epoch_request_end();
	if (fileclose_batch_settle(batch) < 0)
		panic("process teardown file settlement");
	(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS);
	if (fs_epoch_request_begin() < 0)
		panic("process teardown settlement reentry");
}

/* 调用方独占清理所有权；任何析构睡眠前先撤销指针发布，延迟 inode 回收在进程退出后保留自身主体。 */
static int proc_teardown_run(struct proc *p, struct thread *owner,
			     int terminal_current)
{
	struct file *files[FD_BUFFER_SIZE];
	struct file_close_batch close_batch = FILE_CLOSE_BATCH_INIT;
	int enabled;

	if (p == 0 || p->teardown_state != PROC_TEARDOWN_QUIESCING)
		return -1;
	if (terminal_current) {
		if (owner == 0 || owner != current_thread || owner->process != p ||
		    owner->tid != 0 ||
		    p->teardown_owner_tid != owner->tid ||
		    owner->state != RUNNING)
			return -1;
	} else if (owner != 0 ||
		   p->teardown_owner_tid != PROC_TEARDOWN_OWNER_KERNEL) {
		return -1;
	}
	for (int tid = 0; tid < NTHREAD; ++tid) {
		struct thread *t = &p->threads[tid];

		if (t == owner)
			continue;
		if (t->state != T_UNUSED && t->state != T_USED &&
		    t->state != EXITED)
			return -1;
	}
	proc_orphan_children(p);
	for (int tid = 0; tid < NTHREAD; ++tid) {
		struct thread *t = &p->threads[tid];

		if (t == owner)
			continue;
		if (t->state == T_USED)
			thread_user_vm_release(t);
		else
			detach_task(t);
	}
	memset(files, 0, sizeof(files));
	enabled = intr_save();
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		files[i] = p->files[i];
		p->files[i] = 0;
	}
	proc_clear_all_fd_delegate_tickets(p);
	intr_restore(enabled);
	proc_teardown_advance(p, PROC_TEARDOWN_QUIESCING,
			      PROC_TEARDOWN_DETACHED);
	proc_teardown_advance(p, PROC_TEARDOWN_DETACHED,
			      PROC_TEARDOWN_RECLAIMING);
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		int close_result;

		if (files[i] == 0 || fd_is_reserved(files[i]))
			continue;
		while ((close_result = fileclose_batch_add(
				&close_batch, files[i])) > 0)
			proc_teardown_file_progress(&close_batch);
		if (close_result < 0)
			panic("process teardown file batch");
		if (close_batch.count != 0)
			proc_teardown_file_progress(&close_batch);
	}
	agent_proc_teardown(p);
	if (owner != 0)
		thread_user_vm_release(owner);
	if (p->pagetable)
		freepagetable_cleanup(p->pagetable, p->max_page);
	p->pagetable = 0;
	p->max_page = 0;
	p->ustack_base = 0;
	p->heap_base = 0;
	p->heap_break = 0;
	p->exec_dev = 0;
	p->exec_inum = 0;
	p->exec_flags = 0;
	p->exec_generation = 0;
	p->exec_role_mask = 0;
	p->exec_layout_version = 0;
	p->exec_rw_offset = 0;
	proc_teardown_advance(p, PROC_TEARDOWN_RECLAIMING,
			      PROC_TEARDOWN_SETTLING);
	agent_proc_teardown(p);
	/* 文件收据和 VM 清理完成后，先释放文件系统门，再结算终止请求及其生命周期。 */
	if (!terminal_current && close_batch.count != 0)
		proc_teardown_file_progress(&close_batch);
	if (terminal_current) {
		fs_epoch_request_end();
		if (fileclose_batch_settle(&close_batch) < 0)
			panic("process teardown file settlement");
		if (bio_request_end_current_cleanup() < 0)
			panic("process teardown I/O settlement");
		kernel_work_end_cleanup();
	}
	vfs_proc_terminal_clear(p);
	vfs_proc_lifecycle_release(p);
	if (p->pagetable != 0 || p->workflow_lifecycle_charged ||
	    p->vfs_scope_id != VFS_SCOPE_NONE ||
	    p->vfs_pending_scope_id != VFS_SCOPE_NONE || p->is_agent)
		panic("process teardown settlement");
	if (terminal_current &&
	    (thread_trap_cold(owner)->io_request_flags != 0 ||
	     thread_trap_cold(owner)->io_request_id != 0 ||
	     thread_trap_cold(owner)->io_request_depth != 0 ||
	     thread_trap_cold(owner)->kernel_work_depth != 0 ||
	     thread_trap_cold(owner)->bio_buffer_holds != 0 ||
	     thread_trap_cold(owner)->bio_fs_atomic_depth != 0))
		panic("process teardown active work");
	proc_teardown_advance(p, PROC_TEARDOWN_SETTLING,
			      PROC_TEARDOWN_HANDOFF);
	return 0;
}

static void proc_reset_slot(struct proc *p)
{
	if (p->teardown_state != PROC_TEARDOWN_PUBLISHED)
		panic("process recycled before publication");
	if (p->workflow_lifecycle_charged)
		panic("process recycled with workflow lifecycle");
	if (!agent_lifecycle_context_lane_quiescent(p))
		panic("process recycled with Agent context operation");
	for (int tid = 0; tid < NTHREAD; tid++)
		if (p->threads[tid].kstack_state != KSTACK_NONE)
			panic("process recycled with kernel stack");
	agent_context_free(p);
	if (!agent_context_is_empty(p))
		panic("process recycled with Agent context state");
	proc_teardown_advance(p, PROC_TEARDOWN_PUBLISHED,
			      PROC_TEARDOWN_RECYCLED);
	p->workflow_lifecycle_id = WORKFLOW_LIFECYCLE_ID_NONE;
	p->workflow_lifecycle_generation = 0;
	p->workflow_lifecycle_charged = 0;
	proc_resource_release(p);
	p->parent = NULL;
	p->pid = 0;
	p->exit_code = 0;
	p->teardown_owner_tid = PROC_TEARDOWN_OWNER_NONE;
	p->agent_control_state = AGENT_CONTROL_OPEN;
	p->vm_snapshot_depth = 0;
	p->vm_snapshot_owner_tid = -1;
	proc_child_state_reset(p);
	p->state = P_UNUSED;
}

// 退出凭据保存在父进程完成环；内核栈和资源静止后即可复用执行槽。
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
	int epoch_entered = 0;
	int enabled;

	if (p == 0)
		return;
	enabled = intr_save();
	if (proc_teardown_request_locked(p, -1) < 0 ||
	    proc_teardown_claim_locked(p, PROC_TEARDOWN_OWNER_KERNEL) < 0) {
		intr_restore(enabled);
		panic("invalid unpublished process rollback");
	}
	intr_restore(enabled);
	if (fs_epoch_runtime_enabled() && !fs_epoch_request_held()) {
		if (fs_epoch_request_begin() < 0)
			panic("process rollback epoch admission");
		epoch_entered = 1;
	}
	agent_proc_teardown(p);
	proc_child_unbind(p);
	if (proc_teardown_run(p, 0, 0) < 0)
		panic("active process release");
	if (epoch_entered)
		fs_epoch_request_end();
	proc_teardown_advance(p, PROC_TEARDOWN_HANDOFF,
			      PROC_TEARDOWN_PUBLISHED);
	proc_recycle(p);
}

struct fd_spawn_snapshot {
	struct file *files[FD_BUFFER_SIZE];
	uchar delegated[FD_BUFFER_SIZE];
};

// 在可能退让的地址空间复制前固定精确的文件对象。创建安全主体时，在同一
// 临界区消费发起线程的一次性委派票据，避免兄弟线程窃取或槽复用后改指。
static void fd_spawn_snapshot_take(struct proc *p, struct thread *issuer,
				   int consume_tickets,
				   struct fd_spawn_snapshot *snapshot)
{
	int enabled;

	memset(snapshot, 0, sizeof(*snapshot));
	enabled = intr_save();
	if (issuer == 0 || issuer == &idle || issuer->process != p)
		panic("fd delegation issuer");
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		struct file *f = p->files[i];

		if (consume_tickets) {
			snapshot->delegated[i] =
				issuer->fd_delegate_ticket[i] != 0;
			issuer->fd_delegate_ticket[i] = 0;
		}
		if (f == 0 || fd_is_reserved(f))
			continue;
		snapshot->files[i] = filedup(f);
		if (snapshot->files[i] == 0)
			panic("fd spawn snapshot");
	}
	intr_restore(enabled);
}

static void fd_spawn_snapshot_release(struct fd_spawn_snapshot *snapshot)
{
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		if (snapshot->files[i] == 0)
			continue;
		fileclose(snapshot->files[i]);
		snapshot->files[i] = 0;
	}
}

// fork 后仅一条线程存活，其栈保留原虚址以兼容已保存的绝对栈指针；其他复制栈只是普通地址空间内容，
// 映射存在时栈槽分配器不会覆盖它们。
static int fork_child_validate_issuer_stack(struct proc *child,
					     struct thread *issuer)
{
	uint64 span = (uint64)NTHREAD * USTACK_SIZE;
	uint64 keep;

	if (child == 0 || issuer == 0 ||
	    child->ustack_base > MAXVA - span)
		return -1;
	keep = issuer->ustack;
	if (keep < child->ustack_base ||
	    keep >= child->ustack_base + span ||
	    (keep - child->ustack_base) % USTACK_SIZE != 0)
		return -1;
	for (uint64 offset = 0; offset < USTACK_SIZE; offset += PGSIZE)
		if (walkaddr(child->pagetable, keep + offset) == 0)
			return -1;
	return 0;
}

static int fork_common(int make_agent, int agent_role,
		       struct inode *delegated_image, uint64 delegated_caps,
		       enum proc_admission admission,
		       enum vfs_spawn_scope_mode scope_mode)
{
	struct fd_spawn_snapshot fds;
	struct proc *np = 0;
	struct proc *p = curr_proc();
	struct thread *issuer = curr_thread();
	uint parent_scope;
	int authority_boundary;
	int copy_status;
	int i;

	parent_scope = p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
			       p->vfs_scope_id : p->vfs_pending_scope_id;
	// 资源域管理权只控制记账准入；切换资源域不会创建 Agent/VFS 主体，
	// 也不会撤销 POSIX 文件描述符状态。
	authority_boundary = admission != PROC_ADMIT_NORMAL ||
		make_agent || delegated_image != 0 ||
		scope_mode == VFS_SPAWN_SCOPE_FRESH ||
		(parent_scope >= VFS_SCOPE_FIRST_DYNAMIC &&
		 scope_mode == VFS_SPAWN_SCOPE_DROP);
	// VM 与 FD 共用一个父进程快照窗口。这样清理固定引用时，兄弟线程不能
	// 并发关闭并删除同一 inode，也就不会在借用的 FS epoch 内产生结算债务。
	if (proc_vm_snapshot_begin(p) < 0)
		return -1;
	fd_spawn_snapshot_take(p, issuer, authority_boundary, &fds);
	if (!proc_teardown_live(p) || !proc_child_has_capacity(p))
		goto fail;
	if ((np = allocproc_admit(p, admission)) == 0)
		goto fail;
	copy_status = uvmcopy(p->pagetable, np->pagetable, p->max_page);
	if (copy_status == 0) {
		np->max_page = p->max_page;
		np->ustack_base = p->ustack_base;
		np->heap_base = p->heap_base;
		np->heap_break = p->heap_break;
		np->exec_dev = p->exec_dev;
		np->exec_inum = p->exec_inum;
		np->exec_flags = p->exec_flags;
		np->exec_generation = p->exec_generation;
		np->exec_role_mask = p->exec_role_mask;
		np->exec_layout_version = p->exec_layout_version;
		np->exec_rw_offset = p->exec_rw_offset;
		if (fork_child_validate_issuer_stack(np, issuer) < 0)
			copy_status = -1;
	}
	if (copy_status < 0) {
		freeproc(np);
		np = 0;
		goto fail;
	}
	if (vfs_proc_spawn_scope(p, np, scope_mode) < 0) {
		freeproc(np);
		np = 0;
		goto fail;
	}
	if (delegated_image != 0 &&
	    vfs_proc_delegate_exec(p, np, delegated_image, delegated_caps) < 0) {
		freeproc(np);
		np = 0;
		goto fail;
	}
	// pipe 是持有型能力。新 Agent/worker 主体只能通过本次创建消费的票据
	// 获得端点；同一凭据内的 PUBLIC fork 仍保持普通 POSIX 继承语义。
	uint inherited_scope = np->vfs_scope_id != VFS_SCOPE_NONE ?
			       np->vfs_scope_id : np->vfs_pending_scope_id;
	int scope_changed = parent_scope != inherited_scope;
	for (i = 0; i < FD_BUFFER_SIZE; i++) {
		struct file *f = fds.files[i];
		int allowed;

		if (f == 0)
			continue;
		switch (f->inherit_class) {
		case FD_INHERIT_STDIO:
			allowed = f->type == FD_STDIO;
			break;
		case FD_INHERIT_REAUTHORIZE:
			allowed = !scope_changed;
			break;
		case FD_INHERIT_DELEGATE:
			allowed = (!scope_changed && !authority_boundary) ||
				fds.delegated[i];
			break;
		case FD_INHERIT_DENY:
		default:
			allowed = 0;
			break;
		}
		if (!allowed)
			continue;
		// 转移固定引用；创建它的资源域持续记账，直到最后持有者关闭。
		np->files[i] = f;
		fds.files[i] = 0;
	}
	fd_spawn_snapshot_release(&fds);
	// 新进程只有一个线程，但必须恢复真正发起创建的线程。固定使用线程 0
	// 会把兄弟线程的创建操作错误接回主线程，并破坏线程绑定的委派语义。
	int tid = allocthread(np, 0, 0);
	if (tid < 0) {
		freeproc(np);
		goto fail;
	}
	struct thread *nt = &np->threads[tid], *t = issuer;
	int publish_enabled;

	nt->ustack = t->ustack;
	if (make_agent && agent_make_role(np, agent_role) < 0) {
		freeproc(np);
		goto fail;
	}
	// 最终发布在局部关中断边界内串行并复核生命周期门；已标记退出的子进程，
	// 或 scope 不再 ACTIVE 的待定凭据，均不得变为可运行。
	publish_enabled = intr_save();
	if (!proc_teardown_live(np) || !vfs_proc_scope_publishable(np) ||
	    agent_lifecycle_spawn_publish_locked(p, np) < 0) {
		intr_restore(publish_enabled);
		freeproc(np);
		goto fail;
	}
	if (proc_child_bind(p, np) < 0) {
		intr_restore(publish_enabled);
		freeproc(np);
		goto fail;
	}
	// copy saved user registers.
	*(nt->trapframe) = *(t->trapframe);
	// Cause fork to return 0 in the child.
	nt->trapframe->a0 = 0;
	nt->state = RUNNABLE;
	add_task(nt);
	proc_vm_snapshot_end(p);
	intr_restore(publish_enabled);
	return np->pid;

fail:
	fd_spawn_snapshot_release(&fds);
	proc_vm_snapshot_end(p);
	return -1;
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
	// 创建安全名字空间是独立且不可继承的权力；角色授权允许 Orchestrator 填充自身工作流，
	// 但不得让 Agent 铸造新配额和对象域。
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
	int lookup_status;

	if (path == 0)
		return -1;
	ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,
			       VFS_SCOPE_SYSTEM, &lookup_status);
	if (ip == 0 || lookup_status != FS_LOOKUP_FOUND)
		return -1;
	if (ivalid(ip) < 0) {
		iput(ip);
		return -1;
	}
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
	uint64 stack_top;
	uint64 layout_bytes;
	uint64 argv_sp;
	struct user_stack_argv_layout layout;

	if (pagetable == 0 || staged == 0 || argv == 0 ||
	    stack_base > MAXVA - USTACK_SIZE ||
	    (stack_base & (USER_STACK_ALIGNMENT_BYTES - 1)) != 0)
		return -1;
	stack_top = stack_base + USTACK_SIZE;
	user_stack_argv_layout_init(&layout);
	// 修改新用户栈前校验完整布局。
	for (argc = 0; argv[argc]; argc++) {
		uint64 n;

		if (argc >= MAX_ARG_NUM)
			return -1;
		n = strlen(argv[argc]) + 1;
		if (user_stack_argv_layout_add_string(&layout, n) < 0)
			return -1;
		argp[argc] = stack_top - layout.used;
	}
	if (user_stack_argv_layout_finish(&layout, &layout_bytes) < 0)
		return -1;
	argv_sp = stack_top - layout_bytes;
	argp[argc] = 0;
	if (user_range_check(pagetable, argv_sp, layout_bytes, PTE_W) < 0)
		return -1;
	// 布局完整有效后才可修改新栈。
	for (uint64 index = 0; index < argc; index++) {
		uint64 n = strlen(argv[index]) + 1;

		if (copyout(pagetable, argp[index], argv[index], n) < 0) {
			return -1;
		}
	}
	if (copyout(pagetable, argv_sp, (char *)argp,
		    (argc + 1) * sizeof(uint64)) < 0) {
		return -1;
	}
	staged->a1 = argv_sp;
	staged->sp = argv_sp;
	return argc; // this ends up in a0, the first argument to main(argc, argv)
}

static int exec_thread_ready(struct proc *p)
{
	if (!proc_teardown_live(p) || curr_thread() != &p->threads[0] ||
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
	if (ivalid(ip) < 0)
		return 0;
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
		if (lookup_status < 0)
			return 0;
		if (lookup_status == FS_LOOKUP_FOUND) {
			if (proc_exec_inode_usable(p, ip, cred))
				return ip;
			if (ip)
				iput(ip);
			return 0;
		}
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
	if (user_image_build(
		    ip, (uint64)proc_trapframe(p, 0), p->resource_account,
		    p->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
					RESOURCE_CHARGE_ORDINARY,
		    &image) < 0) {
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
	if (proc_install_user_image(p, &image, &staged,
				    PROC_IMAGE_INSTALL_LIVE_EXEC) < 0) {
		user_image_discard(&image);
		return -1;
	}
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
		state = wait_queue_sleep_irq(&p->child_waiters);
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
	kernel_stack_mark_reap(t);
	sched();
	panic("dead thread resumed");
}

// 主线程协调进程退出。兄弟线程仅在阻塞点中断，并须在释放共享状态前
// 展开各自的内核栈。
void exit(int code)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	int enabled;
	int claimed;

	debugf("thread exit with %d", code);
	if (t->tid != 0)
		thread_exit_current(code);
	enabled = intr_save();
	claimed = proc_teardown_request_locked(p, code);
	if (claimed >= 0)
		claimed = proc_teardown_claim_locked(p, t->tid);
	code = (int)p->exit_code;
	intr_restore(enabled);
	if (claimed < 0)
		panic("process teardown owner");
	agent_proc_teardown(p);
	mutex_release_thread_locks(t);
	for (;;) {
		int wait_status;

		proc_interrupt_siblings(p, t);
#ifdef WAIT_ATOMIC_TEST_PROFILE
		{
			int test_enabled = intr_save();
			int waiter_empty = p->thread_exit_waiters.head == 0;
			int injected = wait_atomic_test_begin(
				p, WAIT_ATOMIC_TEST_TEARDOWN,
				(waiter_empty ? WAIT_ATOMIC_TEST_F_WAITER_EMPTY : 0) |
				WAIT_ATOMIC_TEST_F_SIBLING_OBSERVED);

			intr_restore(test_enabled);
			if (injected && !waiter_empty)
				panic("teardown wait injection queue not empty");
			if (injected) {
				if (proc_siblings_quiescent(p, t))
					panic("teardown wait injection without sibling");
				/* 让最后一个兄弟线程发布 EXITED 并唤醒空队列；下方复核须观察该发布，
				 * 避免消费唤醒后再次睡眠。 */
				while (!proc_siblings_quiescent(p, t))
					yield();
				wait_atomic_test_complete(
					p, WAIT_ATOMIC_TEST_TEARDOWN,
					WAIT_ATOMIC_TEST_F_SIBLING_EXITED);
			}
		}
#endif
		enabled = intr_save();
		/*
		 * 最后一个兄弟线程在关中断状态发布 EXITED 并唤醒本队列。最终静止
		 * 复核与队列发布须处于同一关中断区，避免拆除属主休眠前完成事件唤醒空队列。
		 */
		if (proc_siblings_quiescent(p, t)) {
			intr_restore(enabled);
			break;
		}
		wait_status = wait_queue_sleep_irq(&p->thread_exit_waiters);
		intr_restore(enabled);
		if (wait_status != WAIT_QUEUE_OK)
			yield();
	}
	kernel_work_begin_cleanup();
	if (fs_epoch_request_begin() < 0)
		panic("process teardown epoch admission");
	if (bio_request_begin_current_cleanup() < 0)
		panic("process teardown I/O admission");
	if (proc_teardown_run(p, t, 1) < 0)
		panic("active process exit");
	agent_thread_runtime_transition(t, AGENT_THREAD_RUNTIME_RELEASE);
	agent_observe_thread_reset(t);
	t->exit_code = code;
	t->state = T_DYING;
	kernel_stack_mark_reap(t);
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

	if (!proc_teardown_live(p)) {
		intr_restore(enabled);
		return -1;
	}
	for (int i = 0; i < FD_BUFFER_SIZE; i++) {
		if (p->files[i] == 0) {
			p->files[i] = &fd_reservation;
			proc_clear_fd_delegate_tickets(p, i);
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

	if (!proc_teardown_live(p) || fd < 0 || fd >= FD_BUFFER_SIZE || f == 0 ||
	    p->files[fd] != &fd_reservation) {
		intr_restore(enabled);
		return -1;
	}
	p->files[fd] = f;
	proc_clear_fd_delegate_tickets(p, fd);
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
		proc_clear_fd_delegate_tickets(p, fd);
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

int fdclose_prepare(int fd, struct file_close_receipt *receipt)
{
	struct proc *p = curr_proc();
	struct file *f;
	int final;
	int enabled = intr_save();

	if (receipt == 0 ||
	    receipt->state != FILE_CLOSE_RECEIPT_EMPTY)
		panic("fdclose receipt prepare");
	if (p == 0 || fd < 0 || fd >= FD_BUFFER_SIZE ||
	    (f = p->files[fd]) == 0 || fd_is_reserved(f)) {
		intr_restore(enabled);
		return -1;
	}
	final = fileclose_prepare(f, receipt);
	if (final < 0) {
		intr_restore(enabled);
		return -1;
	}
	p->files[fd] = 0;
	proc_clear_fd_delegate_tickets(p, fd);
	intr_restore(enabled);
	return final;
}

int fdclose(int fd)
{
	struct file_close_receipt receipt = FILE_CLOSE_RECEIPT_INIT;
	int result = fdclose_prepare(fd, &receipt);

	if (result < 0)
		return -1;
	if (result > 0)
		fileclose_finish(&receipt);
	return 0;
}

int proc_delegate_fd(int fd)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (!proc_teardown_live(p) || t == 0 || t == &idle ||
	    t->process != p ||
	    fd < 0 || fd >= FD_BUFFER_SIZE ||
	    p->files[fd] == 0 || fd_is_reserved(p->files[fd]) ||
	    p->files[fd]->inherit_class != FD_INHERIT_DELEGATE) {
		intr_restore(enabled);
		return AGENT_STATUS_BAD_PARAM;
	}
	t->fd_delegate_ticket[fd] = 1;
	intr_restore(enabled);
	return AGENT_STATUS_OK;
}

void proc_discard_fd_delegations(void)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (p != 0 && t != 0 && t != &idle && t->process == p)
		memset(t->fd_delegate_ticket, 0,
		       sizeof(t->fd_delegate_ticket));
	intr_restore(enabled);
}
