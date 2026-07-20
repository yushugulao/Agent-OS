#ifndef PROC_H
#define PROC_H

#include "riscv.h"
#include "types.h"
#include "sync.h"
#include "agent.h"

#define NPROC (128)
#define NTHREAD (16)
#define FD_BUFFER_SIZE (16)
#define LOCK_POOL_SIZE (8)
#define SYSCALL_COUNT_MAX (600)
#define MAILBOX_SLOT_COUNT (16)
#define MAILBOX_PAYLOAD_SIZE (256)
#define CHILD_RECORD_CAP (NPROC)
#define PROC_RESOURCE_DOMAIN_CAP (NPROC)
#define PROC_RESOURCE_DOMAIN_LIMIT (NPROC / 2)
#define PROC_RESERVED_SLOTS (NPROC / 8)
#define PROC_ORDINARY_SLOTS (NPROC - PROC_RESERVED_SLOTS)

struct file;
struct proc;
struct user_image;

enum childstate { CHILD_FREE, CHILD_LIVE, CHILD_EXITED };

struct child_record {
	enum childstate state;
	int pid;
	int exit_code;
	struct proc *child;
	uint64 exit_sequence;
};

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
struct thread {
	enum threadstate state; // Thread state
	int tid; // Thread ID
	struct proc *process;
	uint64 ustack; // Virtual address of user stack
	uint64 kstack; // Virtual address of kernel stack
	struct trapframe *trapframe; // data page for trampoline.S
	struct context context; // swtch() here to run process
	uint64 exit_code;
	struct wait_queue *wait_channel;
	struct thread *wait_next;
	enum wait_reason wait_reason;
	int wait_interrupted;
	int on_run_queue;
};

enum procstate { P_UNUSED, P_USED };

// Per-process state
struct proc {
	enum procstate state; // Process state
	int pid; // Process ID
	pagetable_t pagetable; // User page table
	uint64 max_page;
	uint64 ustack_base; // Virtual address of user stack base
	struct proc *parent; // Parent process; NULL means kernel-owned
	int parent_record_index;
	int resource_domain_id;
	uint storage_domain_id;
	int resource_slot_reserved;
	int resource_domain_admin;
	uint64 exit_code;
	int exit_requested;
	int exit_owner_tid;
	int exit_finalizing;
	struct child_record child_records[CHILD_RECORD_CAP];
	uint64 child_exit_sequence;
	uint exec_dev;
	uint exec_inum;
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint vfs_domain;
	uint64 vfs_effective_caps;
	uint64 vfs_inheritable_caps;
	uint vfs_pending_domain;
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
	// Use dummy increasing id as index index of lock pool because we don't have destroy method yet
	uint next_mutex_id, next_semaphore_id, next_condvar_id;
	struct mutex mutex_pool[LOCK_POOL_SIZE];
	struct semaphore semaphore_pool[LOCK_POOL_SIZE];
	struct condvar condvar_pool[LOCK_POOL_SIZE];
	uint64 syscall_count[SYSCALL_COUNT_MAX];
	char mail_payload[MAILBOX_SLOT_COUNT][MAILBOX_PAYLOAD_SIZE];
	int mail_len[MAILBOX_SLOT_COUNT];
	int mail_from[MAILBOX_SLOT_COUNT];
	int mail_head;
	int mail_tail;
	int mail_count;
	int is_agent;
	int agent_type;
	int agent_id;
	int agent_role;
	uint64 agent_control_id;
	uint64 agent_controller_id;
	uint64 agent_ctx_base;
	uint64 agent_ctx_size;
	uint64 agent_call_count;
	int heartbeat_interval;
	int resource_quota;
	int loop_state;
	uint64 context_path_count;
	uint64 context_path_capacity;
	uint64 context_path_head;
	uint64 context_path_oldest;
	uint64 context_path_latest;
	uint64 context_path_dropped;
	uint64 context_path_rollback_count;
	uint64 latest_response_offset;
	uint64 records_offset;
	uint64 agent_ctx_kva[AGENT_CONTEXT_PAGES];
	uint64 agent_shadow_kva[AGENT_CONTEXT_PAGES];
	int agent_mailbox_valid;
	int agent_mailbox_from;
	char agent_mailbox[AGENT_EVENT_PAYLOAD_SIZE];
	int agent_watch_count;
	int agent_watch_valid[AGENT_WATCH_MAX];
	int agent_watch_event_type[AGENT_WATCH_MAX];
	char agent_watch_filter[AGENT_WATCH_MAX][AGENT_WATCH_FILTER_SIZE];
	struct agent_event agent_events[AGENT_EVENT_QUEUE_CAP];
	int agent_event_head;
	int agent_event_tail;
	int agent_event_count_queued;
	uint64 agent_event_count;
	uint64 agent_event_dropped;
	uint64 agent_wait_count;
	uint64 agent_wait_loop_count;
	uint64 agent_wait_sleep_count;
	uint64 agent_wait_wakeup_count;
	uint64 agent_wait_cancel_count;
	uint64 agent_timeout_count;
	int agent_wait_deadline_valid;
	uint64 agent_wait_deadline;
	int agent_wait_cancel_pending;
	int agent_wait_cancel_source_pid;
	uint64 agent_wait_cancel_event_id;
	uint64 agent_wait_cancel_corr_id;
	uint64 agent_wait_cancel_tick;
	uint64 agent_wait_cancel_cause_sequence;
	uint64 agent_wait_cancel_span_id;
	char agent_wait_cancel_reason[AGENT_EVENT_PAYLOAD_SIZE];
	uint64 agent_last_heartbeat_tick;
	uint64 agent_capability_mask;
	uint64 agent_role_grant_mask;
	uint64 agent_detail_count;
	uint64 agent_detail_head;
	struct agent_context_detail agent_details[AGENT_CONTEXT_MAX_RECORDS];
	uint64 agent_prefetch_sequence;
	int agent_prefetch_count;
	int agent_prefetch_head;
	struct agent_file_prefetch_hint
		agent_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
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
	uint64 agent_current_cause_sequence;
	uint64 agent_context_chain_hash;
	uint64 agent_provenance_edges;
	uint64 agent_observe_epoch;
	uint64 agent_timeline_wait_count;
	uint64 agent_timeline_wait_sleep_count;
	uint64 agent_timeline_wait_wakeup_count;
	uint64 agent_timeline_wait_timeout_count;
	int agent_timeline_waiting;
	int agent_timeline_wait_deadline_valid;
	uint64 agent_timeline_wait_deadline;
	struct agent_timeline_filter agent_timeline_wait_filter;
};

int cpuid();
struct proc *curr_proc();
struct thread *curr_thread(void);
int proc_thread_exit_requested(void);
void exit(int);
void proc_init();
void proc_mapstacks(pagetable_t);
void kernel_stack_check(struct thread *);
void proc_storage_set_cookie_floor(uint);
int proc_storage_reserve(uint, int, uint);
void proc_storage_release(uint, int);
void scheduler() __attribute__((noreturn));
void sched();
void yield();
int fork();
int agent_create_proc();
int agent_create_role_proc(int role);
int agent_worker_create_proc(char *, uint64);
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
int fdalloc(struct file *);
int fdreserve();
int fdinstall(int, struct file *);
void fdrelease(int);
int fd_is_reserved(struct file *);
int init_stdio(struct proc *);
int push_argv(struct proc *, char **);
int push_argv_image(pagetable_t, uint64, struct trapframe *, char **);
void proc_install_user_image(struct proc *, struct user_image *,
			     struct trapframe *, int);
// swtch.S
void swtch(struct context *, struct context *);

#endif // PROC_H
