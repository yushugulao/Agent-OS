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

struct file;

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

enum threadstate { T_UNUSED, T_USED, SLEEPING, RUNNABLE, RUNNING, EXITED };
struct thread {
	enum threadstate state; // Thread state
	int tid; // Thread ID
	struct proc *process;
	uint64 ustack; // Virtual address of user stack
	uint64 kstack; // Virtual address of kernel stack
	struct trapframe *trapframe; // data page for trampoline.S
	struct context context; // swtch() here to run process
	uint64 exit_code;
};

enum procstate { P_UNUSED, P_USED, ZOMBIE };

// Per-process state
struct proc {
	enum procstate state; // Process state
	int pid; // Process ID
	pagetable_t pagetable; // User page table
	uint64 max_page;
	uint64 ustack_base; // Virtual address of user stack base
	struct proc *parent; // Parent process
	uint64 exit_code;
	//File descriptor table, using to record the files opened by the process
	struct file *files[FD_BUFFER_SIZE];
	struct thread threads[NTHREAD];
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
void exit(int);
void proc_init();
void scheduler() __attribute__((noreturn));
void sched();
void yield();
int fork();
int agent_create_proc();
int agent_create_role_proc(int role);
int exec(char *, char **);
int wait(int, int *);
void add_task(struct thread *);
struct thread *id_to_task(int);
int task_to_id(struct thread *);
struct thread *pop_task();
struct proc *allocproc();
int allocthread(struct proc *p, uint64 entry, int alloc_user_res);
uint64 get_thread_trapframe_va(int tid);
int fdalloc(struct file *);
int init_stdio(struct proc *);
int push_argv(struct proc *, char **);
// swtch.S
void swtch(struct context *, struct context *);

#endif // PROC_H
