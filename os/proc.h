#ifndef PROC_H
#define PROC_H

#include "const.h"
#include "riscv.h"
#include "types.h"
#include "sync.h"
#include "agent.h"
#include "resource_controller.h"
#include "workflow_lifecycle.h"
#include "../physical_page_policy.h"

#define NPROC (128)
#define NTHREAD (16)
#define FD_BUFFER_SIZE (16)
#define LOCK_POOL_SIZE (8)
#define SYSCALL_COUNT_MAX (600)
#define MAILBOX_SLOT_COUNT (16)
#define MAILBOX_PAYLOAD_SIZE (256)
#define MAILBOX_SIDECAR_PAGE_COUNT (2)

struct agent_legacy_mailbox;
#define CHILD_EXIT_CAP (NPROC)
#define PROC_RESOURCE_DOMAIN_CAP (NPROC)
#define PROC_RESOURCE_DOMAIN_LIMIT (NPROC / 2)
#define PROC_RESERVED_SLOTS (NPROC / 4)
#define PROC_ORDINARY_SLOTS (NPROC - PROC_RESERVED_SLOTS)
#define PROC_RESERVED_DOMAIN_CAP PHYSICAL_PAGE_RESERVED_DOMAIN_CAP
#define PROC_RESOURCE_DOMAIN_RESERVED_LIMIT \
	(PROC_RESERVED_SLOTS / PROC_RESERVED_DOMAIN_CAP)
#define AGENT_STATE_PAGE_POOL_SIZE \
	((uint64)NPROC * AGENT_STATE_PAGE_COUNT)
#define AGENT_STATE_PAGE_ORDINARY_LIMIT \
	((uint64)PROC_ORDINARY_SLOTS * AGENT_STATE_PAGE_COUNT)
#define AGENT_STATE_PAGE_RESERVED_LIMIT \
	((uint64)PROC_RESERVED_SLOTS * AGENT_STATE_PAGE_COUNT)
#define AGENT_STATE_PAGE_DOMAIN_ORDINARY_LIMIT \
	((uint64)PROC_RESOURCE_DOMAIN_LIMIT * AGENT_STATE_PAGE_COUNT)
#define AGENT_STATE_PAGE_DOMAIN_RESERVED_LIMIT \
	((uint64)PROC_RESOURCE_DOMAIN_RESERVED_LIMIT * AGENT_STATE_PAGE_COUNT)

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

// Saved registers for kernel context switches.
struct context {
	uint64 ra;
	uint64 sp;

	// callee-saved
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
	enum threadstate state; // Thread state
	int tid; // Thread ID
	// Slot addresses and tids are reusable; this generation makes ownership
	// references stable across waittid/recreate cycles.
	uint64 identity_generation;
	struct proc *process;
	uint64 ustack; // Virtual address of user stack
	uint64 kstack; // Virtual address of kernel stack
	struct trapframe *trapframe; // data page for trampoline.S
	struct context context; // swtch() here to run process
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
	// Scheduler partition index; accounting identity is resource_account.
	int resource_domain_id;
	int resource_slot_reserved;
	int resource_slot_charged;
	uint kernel_work_depth;
	uint kernel_work_resumed;
	uint kernel_resched_pending;
	uint kernel_work_units;
	uint64 kernel_slice_deadline;
	uint64 kernel_work_redispatches;
	uint64 kernel_syscall_preemptions_start;
	uint64 kernel_last_syscall_preemptions;
	uint64 kernel_receipt_generation;
	uint64 kernel_receipt_completion_timer_epoch;
	int kernel_receipt_syscall_id;
	int kernel_work_target_syscall_id;
	uint kernel_work_publish_receipt;
	/* BIO request state occupies the padding before the 64-bit identity. */
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
	/* Event-wait ownership follows this reusable thread generation. */
	uint64 agent_wait_deadline;
	uchar agent_wait_deadline_valid;
	uchar agent_loop_state;
	/* Observation persistence must not recursively evict its own evidence. */
	uchar agent_observe_suppress_depth;
	/* Published only while this thread is queued in timeline wait. */
	struct agent_timeline_wait_state *agent_timeline_wait_state;
	// Tickets belong to the calling thread's next principal creation.
	uchar fd_delegate_ticket[FD_BUFFER_SIZE];
};

enum procstate { P_UNUSED, P_USED };

/*
 * Process teardown is a forward-only protocol.  Once REQUESTED is visible,
 * no new process-owned object may be published.  The main thread owns
 * QUIESCING through HANDOFF; the scheduler alone performs PUBLISHED and slot
 * recycling.  Unpublished construction rollback uses the explicit kernel
 * owner and follows the same detach/reclaim/settle phases.
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
 * Controller authority has its own one-way publication barrier because an
 * exec downgrade retires authority without necessarily terminating the
 * process. OPEN -> QUIESCING blocks authority and new child publication,
 * then routes trusted children or requests subtree teardown atomically.
 * QUIESCING -> RETIRED waits for sibling commits to quiesce.
 */
enum agent_control_state {
	AGENT_CONTROL_OPEN = 0,
	AGENT_CONTROL_QUIESCING,
	AGENT_CONTROL_RETIRED,
};

#define PROC_TEARDOWN_OWNER_NONE (-1)
#define PROC_TEARDOWN_OWNER_KERNEL (-2)

// Per-process state
struct proc {
	enum procstate state; // Process state
	int pid; // Process ID
	pagetable_t pagetable; // User page table
	uint64 max_page;
	uint64 ustack_base; // Virtual address of user stack base
	uint64 heap_base; // First byte controlled by brk/sbrk
	uint64 heap_break; // Published byte-granular program break
	struct proc *parent; // Parent process; NULL means kernel-owned
	uint live_child_count;
	struct resource_account_handle resource_account;
	// Scheduler partition index; accounting identity is resource_account.
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
	//File descriptor table, using to record the files opened by the process
	struct file *files[FD_BUFFER_SIZE];
	struct thread threads[NTHREAD];
	struct wait_queue child_waiters;
	struct wait_queue thread_exit_waiters;
	struct wait_queue agent_event_waiters;
	struct wait_queue agent_timeline_waiters;
	struct wait_queue agent_context_lane_waiters;
	int agent_context_lane_owner_tid;
	uint agent_context_lane_depth;
	// Use dummy increasing id as index index of lock pool because we don't have destroy method yet
	uint next_mutex_id, next_semaphore_id, next_condvar_id;
	struct mutex mutex_pool[LOCK_POOL_SIZE];
	struct semaphore semaphore_pool[LOCK_POOL_SIZE];
	struct condvar condvar_pool[LOCK_POOL_SIZE];
	uint64 syscall_count[SYSCALL_COUNT_MAX];
	struct agent_legacy_mailbox *mail_sidecar;
	uint64 legacy_mail_endpoint_generation;
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
	uint64 agent_shadow_kva[AGENT_CONTEXT_PAGES];
	int agent_mailbox_valid;
	int agent_mailbox_from;
	char agent_mailbox[AGENT_EVENT_PAYLOAD_SIZE];
	int agent_watch_count;
	int agent_watch_valid[AGENT_WATCH_MAX];
	int agent_watch_event_type[AGENT_WATCH_MAX];
	char agent_watch_filter[AGENT_WATCH_MAX][AGENT_WATCH_FILTER_SIZE];
	// Inbound routes bind senders to non-reused kernel control IDs.
	int agent_ipc_route_count;
	uint64 agent_ipc_route_source[AGENT_IPC_ROUTE_MAX];
	uint64 agent_ipc_route_events[AGENT_IPC_ROUTE_MAX];
	struct agent_event agent_events[AGENT_EVENT_QUEUE_CAP];
	// Source identities stay private so the public event ABI cannot forge them.
	uint64 agent_event_source_control[AGENT_EVENT_QUEUE_CAP];
	uint64 agent_event_span_owner[AGENT_EVENT_QUEUE_CAP];
	uint64 agent_event_audit_principal[AGENT_EVENT_QUEUE_CAP];
	uint64 agent_event_accounting[AGENT_EVENT_QUEUE_CAP];
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
	uint64 agent_prefetch_sequence;
	int agent_prefetch_count;
	int agent_prefetch_head;
	struct agent_file_prefetch_hint
		agent_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
	uint64 agent_prefetch_span_owner[AGENT_FILE_PREFETCH_MAX_HINTS];
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
	struct agent_sched_record agent_sched_records[AGENT_SCHED_TRACE_CAP];
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
struct thread *id_to_task(int);
int task_to_id(struct thread *);
struct thread *pop_task();
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
/* -1 invalid, 0 detached/non-final, 1 detached with a prepared receipt. */
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
