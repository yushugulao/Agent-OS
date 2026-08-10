#ifndef PROC_H
#define PROC_H

#include "const.h"
#include "riscv.h"
#include "trap.h"
#include "types.h"
#include "sync.h"
#include "agent.h"
#include "agent_task_channel.h"
#include "resource_controller.h"
#include "syscall_counter.h"
#include "workflow_lifecycle.h"
#include "../physical_page_policy.h"

#define NPROC (128)
#define NTHREAD (16)
#define FD_BUFFER_SIZE (16)
#define LOCK_POOL_SIZE (8)
#define CHILD_EXIT_CAP (NPROC)
#define PROC_RESOURCE_DOMAIN_CAP (NPROC)
#define PROC_RESOURCE_DOMAIN_LIMIT (NPROC / 2)
#define PROC_RESERVED_SLOTS (NPROC / 4)
#define PROC_ORDINARY_SLOTS (NPROC - PROC_RESERVED_SLOTS)
#define PROC_RESERVED_DOMAIN_CAP PHYSICAL_PAGE_RESERVED_DOMAIN_CAP
#define PROC_RESOURCE_DOMAIN_RESERVED_LIMIT \
	(PROC_RESERVED_SLOTS / PROC_RESERVED_DOMAIN_CAP)
/* Capacity only: Context and Task Channel allocations remain separate charges. */
#define AGENT_STATE_PAGE_PROCESS_LIMIT \
	(AGENT_STATE_PAGE_COUNT + AGENT_TASK_CHANNEL_STATE_PAGES)
#define AGENT_STATE_PAGE_POOL_SIZE \
	((uint64)NPROC * AGENT_STATE_PAGE_PROCESS_LIMIT + \
	 (uint64)WORKFLOW_LIFECYCLE_MAX_ACTIVE * \
		 WORKFLOW_EVIDENCE_PAGE_COUNT)
#define AGENT_STATE_PAGE_ORDINARY_LIMIT \
	((uint64)PROC_ORDINARY_SLOTS * AGENT_STATE_PAGE_PROCESS_LIMIT)
#define AGENT_STATE_PAGE_RESERVED_LIMIT \
	((uint64)PROC_RESERVED_SLOTS * AGENT_STATE_PAGE_PROCESS_LIMIT + \
	 (uint64)WORKFLOW_LIFECYCLE_MAX_ACTIVE * \
		 WORKFLOW_EVIDENCE_PAGE_COUNT)
#define AGENT_STATE_PAGE_DOMAIN_ORDINARY_LIMIT \
	((uint64)PROC_RESOURCE_DOMAIN_LIMIT * \
	 AGENT_STATE_PAGE_PROCESS_LIMIT)
#define AGENT_STATE_PAGE_DOMAIN_RESERVED_LIMIT \
	((uint64)PROC_RESOURCE_DOMAIN_RESERVED_LIMIT * \
		 AGENT_STATE_PAGE_PROCESS_LIMIT + \
	 WORKFLOW_EVIDENCE_PAGE_COUNT)

#if PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT_DERIVED
_Static_assert(1ULL * PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT >=
	       1ULL * PHYSICAL_PAGE_DOMAIN_RESERVED_VM_FLOOR +
		       AGENT_STATE_PAGE_DOMAIN_RESERVED_LIMIT,
	       "reserved physical domains must fund VM and Agent state");
#endif

#include "../file_resource_policy.h"
#include "../thread_resource_policy.h"

#define KSTACK_PAGES_PER_THREAD (KSTACK_SIZE / PAGE_SIZE)
#define KSTACK_VIRTUAL_CAPACITY_BYTES \
	((uint64)NPROC * NTHREAD * KSTACK_SIZE)
#define KSTACK_RESERVED_PAGE_COUNT \
	((uint)THREAD_RESOURCE_RESERVED_LIMIT * KSTACK_PAGES_PER_THREAD)

struct file;
struct file_close_receipt;
struct proc;
struct user_image;
struct agent_timeline_wait_state;

struct child_exit {
	int pid;
	int exit_code;
};

_Static_assert(sizeof(struct child_exit) == 2 * sizeof(int),
	       "child completions must stay compact");

// 内核上下文切换时保存的寄存器。
struct context {
	uint64 ra;
	uint64 sp;

	// 被调用者保存寄存器
	uint64 s0;
	uint64 s1;
	uint64 s2;
	uint64 s3;
	uint64 s4;
	uint64 s5;
	uint64 s6;
	uint64 s7;
	uint64 s8;
	uint64 s9;
	uint64 s10;
	uint64 s11;
};

/* 仅在陷阱、系统调用和上下文切换路径访问，放在线程陷阱帧后的冷区。 */
struct thread_trap_cold {
	uint kernel_work_depth;
	uint kernel_work_resumed;
	uint kernel_resched_pending;
	uint kernel_work_units;
	uint64 kernel_work_redispatches;
	uint64 kernel_syscall_preemptions_start;
	uint64 kernel_last_syscall_preemptions;
	uint64 kernel_work_generation;
	int kernel_work_target_syscall_id;
	uint kernel_work_measure_preemptions;
	uint io_request_flags;
	uint64 io_request_id;
	uint io_request_depth;
	uint io_request_owner;
	uint io_request_class;
	uint io_request_reservation;
	uint io_request_device_reservation;
	uint io_request_transfers;
	uint bio_buffer_holds;
	uint bio_fs_atomic_depth;
	/* Controller-owned phase token; tentative claims may not cross sched(). */
	uint resource_phase_lease_slot;
	uint resource_phase_claim_depth;
	uint64 resource_phase_lease_generation;
	struct context context;
	/* 系统调用慢路径独占使用，避免把大事务回执压在内核栈上。 */
	uint64 syscall_transaction[16];
};

_Static_assert(sizeof(struct thread_trap_cold) == 360,
	       "thread trap cold state layout changed");
#define THREAD_TRAP_COLD_OFFSET \
	((sizeof(struct trapframe) + 15) & ~(uint64)15)
_Static_assert(THREAD_TRAP_COLD_OFFSET + sizeof(struct thread_trap_cold) <=
	       PAGE_SIZE, "thread trap cold state must fit after trapframe");

enum threadstate {
	T_UNUSED,
	T_USED,
	SLEEPING,
	RUNNABLE,
	RUNNING,
	T_DYING,
	EXITED
};

enum kernel_stack_state {
	KSTACK_NONE = 0,
	KSTACK_LIVE,
	KSTACK_REAP,
};

struct thread {
	enum threadstate state; // 线程状态
	int tid; // 线程号
	// 槽位地址和 tid 都会复用；代数用于跨 waittid/重建周期稳定标识所有者。
	uint64 identity_generation;
	struct proc *process;
	uint64 ustack; // 用户栈虚拟地址
	uint64 kstack; // 内核栈虚拟地址
	struct trapframe *trapframe; // trampoline.S 使用的数据页
	uint64 exit_code;
	struct wait_queue *wait_channel;
	struct thread *wait_next;
	enum wait_reason wait_reason;
	uint64 wait_key;
	int wait_interrupted;
	int wait_interruptible;
	int on_run_queue;
	int run_queue_agent;
	enum kernel_stack_state kstack_state;
	struct resource_account_handle resource_account;
	// 调度分区索引；计费身份由 resource_account 表示。
	int resource_domain_id;
	int resource_slot_reserved;
	int resource_slot_charged;
	/* 事件等待的所有权跟随可复用线程的当前代数。 */
	uint64 agent_wait_deadline;
	uchar agent_wait_deadline_valid;
	uchar agent_loop_state;
	/* 观测持久化不得递归淘汰自身证据。 */
	uchar agent_observe_suppress_depth;
	/* 精确唤醒令牌随线程槽位存放，复用原有对齐空隙。 */
	uchar agent_event_baton;
	/* 仅在线程进入时间线等待队列期间发布。 */
	struct agent_timeline_wait_state *agent_timeline_wait_state;
	// 委派票据属于调用线程下一次创建的主体。
	uchar fd_delegate_ticket[FD_BUFFER_SIZE];
};

static inline struct thread_trap_cold *thread_trap_cold(struct thread *t)
{
	return (struct thread_trap_cold *)((uchar *)t->trapframe +
					  THREAD_TRAP_COLD_OFFSET);
}

static inline const struct thread_trap_cold *
thread_trap_cold_const(const struct thread *t)
{
	return (const struct thread_trap_cold *)((const uchar *)t->trapframe +
						THREAD_TRAP_COLD_OFFSET);
}

enum procstate { P_UNUSED, P_USED };

/*
 * 进程拆除是单向协议。REQUESTED 可见后不得再发布进程所属对象；主线程
 * 负责 QUIESCING 到 HANDOFF，只有调度器能执行 PUBLISHED 和槽位回收。
 * 尚未发布的构造回滚由内核显式持有，并复用分离、回收、结算阶段。
 */
enum proc_teardown_state {
	PROC_TEARDOWN_LIVE = 0,
	PROC_TEARDOWN_REQUESTED,
	PROC_TEARDOWN_QUIESCING,
	PROC_TEARDOWN_DETACHED,
	PROC_TEARDOWN_RECLAIMING,
	PROC_TEARDOWN_SETTLING,
	PROC_TEARDOWN_HANDOFF,
	PROC_TEARDOWN_PUBLISHED,
	PROC_TEARDOWN_RECYCLED,
};

/*
 * 控制权使用独立的单向发布屏障，因为 exec 降权可撤销权限而不终止进程。
 * OPEN -> QUIESCING 阻止权限和新子进程发布，并原子地转交可信子进程或
 * 请求拆除子树；QUIESCING -> RETIRED 等待同级提交静默。
 */
enum agent_control_state {
	AGENT_CONTROL_OPEN = 0,
	AGENT_CONTROL_QUIESCING,
	AGENT_CONTROL_RETIRED,
};

#define PROC_TEARDOWN_OWNER_NONE (-1)
#define PROC_TEARDOWN_OWNER_KERNEL (-2)

/*
 * 普通进程不需要 Agent 的过滤表、路由表和调度轨迹。参考 Linux 以独立对象和
 * 指针管理可选子系统状态的做法，这两页只在 Agent 激活时分配，使传统
 * fork/调度路径保持紧凑。
 */
struct agent_ipc_observe_cold_state {
	int watch_valid[AGENT_WATCH_MAX];
	int watch_event_type[AGENT_WATCH_MAX];
	char watch_filter[AGENT_WATCH_MAX][AGENT_WATCH_FILTER_SIZE];
	uint64 ipc_route_source[AGENT_IPC_ROUTE_MAX];
	uint64 ipc_route_events[AGENT_IPC_ROUTE_MAX];
	struct agent_event events[AGENT_EVENT_QUEUE_CAP];
	uint64 event_source_control[AGENT_EVENT_QUEUE_CAP];
	uint64 event_span_owner[AGENT_EVENT_QUEUE_CAP];
	uint64 event_audit_principal[AGENT_EVENT_QUEUE_CAP];
	uint64 event_accounting[AGENT_EVENT_QUEUE_CAP];
	struct agent_sched_record sched_records[AGENT_SCHED_TRACE_CAP];
};

_Static_assert(sizeof(struct agent_ipc_observe_cold_state) == PAGE_SIZE,
	       "Agent IPC/observe cold state must fill one page");

// 每进程状态
struct proc {
	enum procstate state; // 进程状态
	int pid; // 进程号
	pagetable_t pagetable; // 用户页表
	uint64 max_page;
	uint64 ustack_base; // 用户栈基址
	uint64 heap_base; // brk/sbrk 管理的首字节
	uint64 heap_break; // 已发布的字节粒度堆边界
	struct proc *parent; // 父进程；NULL 表示由内核持有
	uint live_child_count;
	struct resource_account_handle resource_account;
	// 调度分区索引；计费身份由 resource_account 表示。
	int resource_domain_id;
	uint storage_principal_id;
	int resource_slot_reserved;
	int resource_domain_admin;
	uint64 exit_code;
	enum proc_teardown_state teardown_state;
	int teardown_owner_tid;
	uint vm_snapshot_depth;
	int vm_snapshot_owner_tid;
	uint child_exit_head;
	uint child_exit_count;
	struct child_exit child_exits[CHILD_EXIT_CAP];
	uint exec_dev;
	uint exec_inum;
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint workflow_lifecycle_id;
	uint64 workflow_lifecycle_generation;
	int workflow_lifecycle_charged;
	uint vfs_scope_id;
	int vfs_scope_controller;
	uint64 vfs_effective_caps;
	uint64 vfs_inheritable_caps;
	uint vfs_pending_scope_id;
	uint64 vfs_pending_caps;
	uint vfs_pending_exec_dev;
	uint vfs_pending_exec_inum;
	uint vfs_pending_exec_incarnation;
	uint vfs_bound_exec_dev;
	uint vfs_bound_exec_inum;
	uint vfs_bound_exec_incarnation;
	// 文件描述符表，记录进程已打开的文件。
	struct file *files[FD_BUFFER_SIZE];
	struct thread threads[NTHREAD];
	struct wait_queue child_waiters;
	struct wait_queue thread_exit_waiters;
	struct wait_queue agent_event_waiters;
	struct wait_queue agent_timeline_waiters;
	struct wait_queue agent_context_lane_waiters;
	int agent_context_lane_owner_tid;
	uint agent_context_lane_depth;
	// 当前同步对象没有销毁接口，暂用单调递增编号索引固定池。
	uint next_mutex_id, next_semaphore_id, next_condvar_id;
	struct mutex mutex_pool[LOCK_POOL_SIZE];
	struct semaphore semaphore_pool[LOCK_POOL_SIZE];
	struct condvar condvar_pool[LOCK_POOL_SIZE];
	/* trace() 返回 int；计数饱和而不回绕，保持性能快照单调。 */
	uint syscall_count[SYSCALL_COUNTER_SLOTS];
	int is_agent;
	int agent_type;
	int agent_id;
	int agent_role;
	enum agent_control_state agent_control_state;
	uint64 agent_control_id;
	uint64 agent_controller_id;
	uint64 agent_ctx_base;
	uint64 agent_call_count;
	uint64 agent_meta_txn_wait_count;
	int heartbeat_interval;
	int resource_quota;
	int loop_state;
	uint64 context_path_count;
	uint64 context_path_capacity;
	uint64 context_path_head;
	uint64 context_path_oldest;
	uint64 context_path_latest;
	uint64 context_path_visible_head;
	uint64 context_active_path_count;
	uint64 context_active_path_oldest;
	uint64 context_branch_generation;
	uint64 context_cause_branch_generation;
	uint64 context_path_rollback_count;
	uint64 agent_ctx_kva[AGENT_CONTEXT_PAGES];
	struct agent_ipc_observe_cold_state *agent_ipc_observe_cold;
	int agent_mailbox_valid;
	int agent_mailbox_from;
	char agent_mailbox[AGENT_EVENT_PAYLOAD_SIZE];
	int agent_watch_count;
	// 入站路由把发送者绑定到不可复用的内核控制标识。
	int agent_ipc_route_count;
	// 事件正文及可信来源身份位于按需冷页，公开 ABI 无法伪造。
	int agent_event_head;
	int agent_event_tail;
	int agent_event_count_queued;
	int agent_external_event_count_queued;
	int agent_ipc_count_queued;
	int agent_attributed_event_count_queued;
	uint64 agent_event_count;
	uint64 agent_event_dropped;
	uint64 agent_wait_count;
	uint64 agent_wait_loop_count;
	uint64 agent_wait_sleep_count;
	uint64 agent_wait_wakeup_count;
	uint64 agent_wait_cancel_count;
	uint64 agent_timeout_count;
	int agent_wait_cancel_pending;
	int agent_wait_cancel_source_pid;
	uint64 agent_wait_cancel_event_id;
	uint64 agent_wait_cancel_corr_id;
	uint64 agent_wait_cancel_tick;
	uint64 agent_wait_cancel_cause_sequence;
	uint64 agent_wait_cancel_span_id;
	uint64 agent_wait_cancel_span_owner;
	uint64 agent_wait_cancel_source_control;
	uint64 agent_wait_cancel_audit_principal;
	char agent_wait_cancel_reason[AGENT_EVENT_PAYLOAD_SIZE];
	uint64 agent_last_heartbeat_tick;
	uint64 agent_capability_mask;
	uint64 agent_role_grant_mask;
	uint64
		agent_context_sidecar_kva[AGENT_CONTEXT_SIDECAR_PAGE_COUNT];
	struct resource_account_handle agent_state_account;
	enum resource_charge_class agent_state_charge_class;
	uint agent_state_charged_pages;
	int agent_sched_policy;
	int agent_sched_weight;
	int agent_sched_priority;
	uint64 agent_sched_ready_tick;
	uint64 agent_sched_last_dispatch_tick;
	uint64 agent_sched_dispatch_count;
	uint64 agent_sched_event_dispatch_count;
	uint64 agent_sched_deadline_dispatch_count;
	uint64 agent_sched_vruntime;
	uint64 agent_sched_preemptions;
	uint64 agent_sched_budget;
	uint64 agent_sched_budget_used;
	uint64 agent_sched_last_score;
	uint64 agent_sched_last_reason;
	uint64 agent_sched_trace_count;
	uint64 agent_sched_trace_head;
	uint64 agent_current_span_id;
	uint64 agent_current_span_owner;
	uint64 agent_current_cause_sequence;
	int agent_current_cause_pid;
	uint64 agent_current_cause_control;
	uint64 agent_context_chain_hash;
	uint64 agent_provenance_edges;
	uint64 agent_observe_epoch;
	uint64 agent_timeline_wait_count;
	uint64 agent_timeline_wait_sleep_count;
	uint64 agent_timeline_wait_wakeup_count;
	uint64 agent_timeline_wait_timeout_count;
};

int cpuid();
struct proc *curr_proc();
struct thread *curr_thread(void);
int proc_teardown_live(const struct proc *);
int proc_thread_exit_requested(void);
int proc_request_workflow_exit(struct workflow_lifecycle_key, int);
int proc_request_controller_exit(struct workflow_lifecycle_key, uint64, int);
int proc_vm_snapshot_begin(struct proc *);
void proc_vm_snapshot_end(struct proc *);
int proc_sbrk(long);
void exit(int);
void proc_init();
void proc_mapstacks(pagetable_t);
void kernel_stack_check(struct thread *);
void proc_scope_set_id_floor(uint);
void scheduler() __attribute__((noreturn));
void sched();
void yield();
int fork();
int agent_create_proc();
int agent_create_role_proc(int role);
int agent_workflow_create_proc(int role);
int agent_worker_create_proc(char *, uint64);
int proc_delegate_fd(int);
void proc_discard_fd_delegations(void);
void proc_revoke_vfs_scope_fds(struct proc *);
int exec(char *, char **);
int wait(int, int *);
void add_task(struct thread *);
int scheduler_has_runnable_peer(void);
struct thread *id_to_task(int);
int task_to_id(struct thread *);
struct proc *allocproc();
void freeproc(struct proc *);
int allocthread(struct proc *p, uint64 entry, int alloc_user_res);
uint64 get_thread_trapframe_va(int tid);
struct trapframe *proc_trapframe(struct proc *, int);
int fdreserve();
int fdinstall(int, struct file *);
void fdrelease(int);
int fd_is_reserved(struct file *);
struct file *fdget(int);
/* -1 表示无效；0 表示已分离但非末引用；1 表示已分离且回执就绪。 */
int fdclose_prepare(int, struct file_close_receipt *);
int fdclose(int);
int proc_file_slots_reserve(struct proc *, uint,
			    struct resource_account_handle *, int *);
void proc_file_slot_release(struct resource_account_handle, int);
void proc_resource_account_reap(struct resource_account_handle);
int init_stdio(struct proc *);
int push_argv_image(pagetable_t, uint64, struct trapframe *, char **);
enum proc_image_install_mode {
	PROC_IMAGE_INSTALL_BOOTSTRAP = 1,
	PROC_IMAGE_INSTALL_LIVE_EXEC = 2,
};
int proc_install_user_image(struct proc *, struct user_image *,
			    struct trapframe *, enum proc_image_install_mode);
// swtch.S
void swtch(struct context *, struct context *);

#endif // PROC_H
