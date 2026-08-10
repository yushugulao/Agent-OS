#ifndef WORKFLOW_SCHEDULER_H
#define WORKFLOW_SCHEDULER_H

#include "resource_controller.h"
#include "types.h"
#include "workflow_lifecycle.h"

#define WORKFLOW_SCHEDULER_ENTITY_CAP WORKFLOW_LIFECYCLE_MAX_ACTIVE
#define WORKFLOW_SCHEDULER_BASE_WEIGHT 1024U

struct workflow_scheduler_candidate {
	struct workflow_lifecycle_key lifecycle;
	struct resource_account_handle account;
	int domain_id;
	uint latency_class;
	uint runnable_threads;
	/* UINT64_MAX means that no event deadline is currently visible. */
	uint64 deadline_delta_ticks;
};

struct workflow_scheduler_snapshot {
	uint mode;
	uint flags;
	uint latency_class;
	uint weight;
	uint runnable;
	uint request_ticks;
	uint64 remaining_cycles;
	signed long long lag_cycles;
	uint64 vruntime;
	uint64 virtual_deadline;
	uint64 dispatches;
	uint64 service_cycles;
	uint64 sleep_decays;
	uint64 eligibility_misses;
	uint64 fallbacks;
	uint64 max_wakeup_ticks;
	uint64 deadline_misses;
	uint64 wakeup_samples;
	uint64 wakeup_latency_buckets[4];
};

struct thread;

void workflow_scheduler_init(void);
void workflow_scheduler_forget_domain(
	int domain_id, struct resource_account_handle account);

/*
 * Select one workflow resource domain without changing the run queue.  The
 * caller supplies at most one candidate per full lifecycle/account/domain
 * identity and applies the returned decision only after this function
 * succeeds.  A negative return requires the caller to use the legacy RR
 * scheduler unchanged.
 */
int workflow_scheduler_select(
	const struct workflow_scheduler_candidate *candidates, uint count,
	uint64 now_cycle, uint64 now_tick, int *selected_domain);
void workflow_scheduler_note_fallback(
	const struct workflow_scheduler_candidate *candidates, uint count);

int workflow_scheduler_on_enqueue(const struct thread *thread,
				  uint64 now_tick);
void workflow_scheduler_on_dequeue(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, int schedulable);
void workflow_scheduler_schedulable_adjust(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, int delta);
void workflow_scheduler_on_sleep(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, uint64 now_tick);
int workflow_scheduler_candidate_get(
	int domain_id, uint64 now_tick,
	struct workflow_scheduler_candidate *candidate);
int workflow_scheduler_runnable_domains(int *domain_ids, uint capacity);
int workflow_scheduler_domain_runnable(int domain_id);
int workflow_scheduler_domain_tracked(int domain_id);
void workflow_scheduler_domain_invalidate(int domain_id);
void workflow_scheduler_domain_idle(int domain_id, uint64 now_tick);

void workflow_scheduler_on_dispatch(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, uint64 now_tick);
void workflow_scheduler_charge(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id,
	uint64 service_cycles);

int workflow_scheduler_snapshot_get(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id,
	struct workflow_scheduler_snapshot *snapshot);

#endif
