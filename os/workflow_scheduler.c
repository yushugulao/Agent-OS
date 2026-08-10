#include "workflow_scheduler.h"
#include "defs.h"
#include "proc.h"
#include "riscv.h"
#include "timer.h"
#include "../agent_lifecycle_abi.h"

#define WORKFLOW_SCHEDULER_SLOT_NONE (-1)
#define WORKFLOW_SCHEDULER_SLEEP_DECAY_TICKS 16ULL
#define WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS 8U
#define WORKFLOW_SCHEDULER_REBASE_THRESHOLD (1ULL << 60)
#define WORKFLOW_SCHEDULER_F_DEADLINE_MISSED (1U << 29)
#define WORKFLOW_SCHEDULER_F_CACHE_INVALID (1U << 30)
#define WORKFLOW_SCHEDULER_F_WAKE_PENDING (1U << 31)
#define WORKFLOW_SCHEDULER_NO_DEADLINE (~0ULL)
#define WORKFLOW_SCHEDULER_WAKE_BUCKETS 4U

struct workflow_scheduler_entity {
	struct workflow_lifecycle_key lifecycle;
	struct resource_account_handle account;
	uint64 vruntime;
	uint64 virtual_deadline;
	uint64 remaining_cycles;
	uint64 service_cycles;
	uint64 sleep_start_tick;
	uint64 wake_tick;
	uint64 earliest_deadline_tick;
	uint dispatches;
	uint sleep_decays;
	uint eligibility_misses;
	uint fallbacks;
	uint max_wakeup_ticks;
	uint deadline_misses;
	uint wakeup_latency_buckets[WORKFLOW_SCHEDULER_WAKE_BUCKETS];
	uint flags;
	ushort runnable_threads;
	ushort schedulable_threads;
	signed short domain_id;
	uchar mode;
	uchar latency_class;
	uchar request_ticks;
	uchar cached_latency_class;
};

static struct workflow_scheduler_entity
	workflow_scheduler_entities[WORKFLOW_SCHEDULER_ENTITY_CAP];
/* A signed byte is enough for four entities and keeps the hot map compact. */
static signed char workflow_scheduler_domain_slot[PROC_RESOURCE_DOMAIN_CAP];
static uint64 workflow_scheduler_vtime;

_Static_assert(WORKFLOW_SCHEDULER_ENTITY_CAP == 4,
	       "workflow scheduler table follows the active lifecycle bound");
_Static_assert(sizeof(workflow_scheduler_entities) +
		       sizeof(workflow_scheduler_domain_slot) +
		       sizeof(workflow_scheduler_vtime) <= 768,
	       "workflow scheduler core state must stay below 0.75 KiB");
_Static_assert(CPU_FREQ % TICKS_PER_SEC == 0,
	       "workflow request lengths require integral tick cycles");
_Static_assert(NPROC * NTHREAD <= 0xffffU,
	       "workflow runnable cache uses 16-bit counts");

static uint64 workflow_scheduler_add_sat(uint64 a, uint64 b)
{
	return a > ~0ULL - b ? ~0ULL : a + b;
}

static uint64 workflow_scheduler_counter_add(uint64 value, uint64 amount)
{
	return workflow_scheduler_add_sat(value, amount);
}

static uint workflow_scheduler_counter32_add(uint value, uint amount)
{
	return value > (uint)-1 - amount ? (uint)-1 : value + amount;
}

static int workflow_scheduler_account_key_valid(
	struct resource_account_handle account)
{
	return account.slot < RESOURCE_ACCOUNT_CAP && account.generation != 0;
}

static int workflow_scheduler_identity_valid(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id)
{
	return workflow_lifecycle_key_valid(lifecycle) &&
	       workflow_scheduler_account_key_valid(account) && domain_id >= 0 &&
	       domain_id < PROC_RESOURCE_DOMAIN_CAP;
}

static inline __attribute__((always_inline)) int
workflow_scheduler_entity_matches(
	const struct workflow_scheduler_entity *entity,
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id)
{
	return (entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE) &&
	       entity->domain_id == domain_id &&
	       workflow_lifecycle_key_equal(entity->lifecycle, lifecycle) &&
	       entity->account.slot == account.slot &&
	       entity->account.generation == account.generation;
}

static inline __attribute__((always_inline)) int
workflow_scheduler_find_locked(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id)
{
	int slot;

	if (!workflow_scheduler_identity_valid(lifecycle, account, domain_id))
		return WORKFLOW_SCHEDULER_SLOT_NONE;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot >= 0 && slot < WORKFLOW_SCHEDULER_ENTITY_CAP &&
	    workflow_scheduler_entity_matches(
		    &workflow_scheduler_entities[slot], lifecycle, account,
		    domain_id))
		return slot;
	for (slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++)
		if (workflow_scheduler_entity_matches(
			    &workflow_scheduler_entities[slot], lifecycle, account,
			    domain_id)) {
			workflow_scheduler_domain_slot[domain_id] = slot;
			return slot;
		}
	return WORKFLOW_SCHEDULER_SLOT_NONE;
}

static inline __attribute__((always_inline)) int
workflow_scheduler_alloc_locked(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id)
{
	struct workflow_scheduler_entity *entity;
	int slot;

	slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);
	if (slot >= 0)
		return slot;
	if (workflow_scheduler_domain_slot[domain_id] !=
	    WORKFLOW_SCHEDULER_SLOT_NONE)
		return WORKFLOW_SCHEDULER_SLOT_NONE;
	for (slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++)
		if ((workflow_scheduler_entities[slot].flags &
		     AGENT_WORKFLOW_SCHED_F_ACTIVE) &&
		    workflow_lifecycle_key_equal(
			    workflow_scheduler_entities[slot].lifecycle,
			    lifecycle))
			return WORKFLOW_SCHEDULER_SLOT_NONE;
	for (slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++)
		if (!(workflow_scheduler_entities[slot].flags &
		      AGENT_WORKFLOW_SCHED_F_ACTIVE))
			break;
	if (slot == WORKFLOW_SCHEDULER_ENTITY_CAP)
		return WORKFLOW_SCHEDULER_SLOT_NONE;
	entity = &workflow_scheduler_entities[slot];
	memset(entity, 0, sizeof(*entity));
	entity->lifecycle = lifecycle;
	entity->account = account;
	entity->domain_id = domain_id;
	entity->mode = AGENT_WORKFLOW_SCHED_MODE_EEVDF;
	entity->flags = AGENT_WORKFLOW_SCHED_F_ACTIVE;
	entity->latency_class = AGENT_WORKFLOW_LATENCY_NORMAL;
	entity->cached_latency_class = AGENT_WORKFLOW_LATENCY_BATCH;
	entity->request_ticks = 4;
	entity->vruntime = workflow_scheduler_vtime;
	entity->earliest_deadline_tick = WORKFLOW_SCHEDULER_NO_DEADLINE;
	workflow_scheduler_domain_slot[domain_id] = slot;
	return slot;
}

static uint workflow_scheduler_request_ticks(
	uint latency_class, uint64 deadline_delta_ticks)
{
	uint ticks;

	switch (latency_class) {
	case AGENT_WORKFLOW_LATENCY_URGENT:
		ticks = 1;
		break;
	case AGENT_WORKFLOW_LATENCY_INTERACTIVE:
		ticks = 2;
		break;
	case AGENT_WORKFLOW_LATENCY_NORMAL:
		ticks = 4;
		break;
	case AGENT_WORKFLOW_LATENCY_BATCH:
		ticks = 8;
		break;
	default:
		return 0;
	}
	/* A wall deadline can shorten, but never enlarge, a service request. */
	if (deadline_delta_ticks != ~0ULL && deadline_delta_ticks < ticks)
		ticks = deadline_delta_ticks == 0 ? 1 :
			(uint)deadline_delta_ticks;
	return ticks;
}

static uint64 workflow_scheduler_request_cycles(uint request_ticks)
{
	return (uint64)request_ticks * (CPU_FREQ / TICKS_PER_SEC);
}

static uint64 workflow_scheduler_decay_toward(uint64 value, uint64 target,
					       uint shifts)
{
	uint64 distance;

	if (shifts == 0 || value == target)
		return value;
	if (shifts > WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS)
		shifts = WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS;
	if (value < target) {
		distance = target - value;
		return target - (distance >> shifts);
	}
	distance = value - target;
	return target + (distance >> shifts);
}

static signed long long workflow_scheduler_lag(uint64 vtime,
						uint64 vruntime)
{
	uint64 distance;
	const uint64 signed_max = (~0ULL) >> 1;

	if (vtime >= vruntime) {
		distance = vtime - vruntime;
		return distance > signed_max ? (signed long long)signed_max :
					       (signed long long)distance;
	}
	distance = vruntime - vtime;
	if (distance > signed_max)
		return -((signed long long)signed_max);
	return -((signed long long)distance);
}

static uint64 workflow_scheduler_average(const uint64 *values, uint count)
{
	uint64 quotient_sum = 0;
	uint64 remainder_sum = 0;

	/* quotient_sum cannot overflow: each term is at most UINT64_MAX/count. */
	for (uint i = 0; i < count; i++) {
		quotient_sum += values[i] / count;
		remainder_sum += values[i] % count;
	}
	return quotient_sum + remainder_sum / count;
}

static int workflow_scheduler_before(
	const struct workflow_scheduler_candidate *candidates,
	const uint64 *deadlines, const uint64 *vruntimes, uint a, uint b)
{
	if (deadlines[a] != deadlines[b])
		return deadlines[a] < deadlines[b];
	if (vruntimes[a] != vruntimes[b])
		return vruntimes[a] < vruntimes[b];
	if (candidates[a].lifecycle.id != candidates[b].lifecycle.id)
		return candidates[a].lifecycle.id < candidates[b].lifecycle.id;
	if (candidates[a].lifecycle.generation !=
	    candidates[b].lifecycle.generation)
		return candidates[a].lifecycle.generation <
		       candidates[b].lifecycle.generation;
	return candidates[a].domain_id < candidates[b].domain_id;
}

static uint64 workflow_scheduler_rebase_offset_locked(void)
{
	uint64 minimum = ~0ULL;
	int present = 0;

	for (uint i = 0; i < WORKFLOW_SCHEDULER_ENTITY_CAP; i++) {
		const struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[i];

		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE))
			continue;
		present = 1;
		if (entity->vruntime < minimum)
			minimum = entity->vruntime;
	}
	return present && minimum >= WORKFLOW_SCHEDULER_REBASE_THRESHOLD ?
		       minimum : 0;
}

static int workflow_scheduler_plan_slot_locked(
	const struct workflow_scheduler_candidate *candidate, uint reserved,
	int *is_new)
{
	int mapped = workflow_scheduler_domain_slot[candidate->domain_id];
	int slot;

	if (mapped != WORKFLOW_SCHEDULER_SLOT_NONE) {
		if (mapped < 0 || mapped >= WORKFLOW_SCHEDULER_ENTITY_CAP ||
		    !workflow_scheduler_entity_matches(
			    &workflow_scheduler_entities[mapped],
			    candidate->lifecycle, candidate->account,
			    candidate->domain_id) ||
		    (reserved & (1U << mapped)) != 0)
			return WORKFLOW_SCHEDULER_SLOT_NONE;
		*is_new = 0;
		return mapped;
	}
	for (slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++) {
		if (workflow_scheduler_entity_matches(
			    &workflow_scheduler_entities[slot],
			    candidate->lifecycle, candidate->account,
			    candidate->domain_id)) {
			if ((reserved & (1U << slot)) != 0)
				return WORKFLOW_SCHEDULER_SLOT_NONE;
			*is_new = 0;
			return slot;
		}
	}
	for (slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++)
		if ((workflow_scheduler_entities[slot].flags &
		     AGENT_WORKFLOW_SCHED_F_ACTIVE) &&
		    workflow_lifecycle_key_equal(
			    workflow_scheduler_entities[slot].lifecycle,
			    candidate->lifecycle))
			return WORKFLOW_SCHEDULER_SLOT_NONE;
	for (slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++)
		if (!(workflow_scheduler_entities[slot].flags &
		      AGENT_WORKFLOW_SCHED_F_ACTIVE) &&
		    (reserved & (1U << slot)) == 0) {
			*is_new = 1;
			return slot;
		}
	return WORKFLOW_SCHEDULER_SLOT_NONE;
}

static void workflow_scheduler_rebase_locked(void)
{
	uint64 minimum = ~0ULL;
	int present = 0;

	for (uint i = 0; i < WORKFLOW_SCHEDULER_ENTITY_CAP; i++) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[i];

		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE))
			continue;
		present = 1;
		if (entity->vruntime < minimum)
			minimum = entity->vruntime;
	}
	if (!present || minimum < WORKFLOW_SCHEDULER_REBASE_THRESHOLD)
		return;
	for (uint i = 0; i < WORKFLOW_SCHEDULER_ENTITY_CAP; i++) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[i];

		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE))
			continue;
		entity->vruntime -= minimum;
		entity->virtual_deadline =
			entity->virtual_deadline >= minimum ?
				entity->virtual_deadline - minimum : 0;
	}
	workflow_scheduler_vtime = workflow_scheduler_vtime >= minimum ?
					       workflow_scheduler_vtime - minimum :
					       0;
}

void workflow_scheduler_init(void)
{
	int enabled = intr_save();

	memset(workflow_scheduler_entities, 0,
	       sizeof(workflow_scheduler_entities));
	for (uint i = 0; i < PROC_RESOURCE_DOMAIN_CAP; i++)
		workflow_scheduler_domain_slot[i] =
			WORKFLOW_SCHEDULER_SLOT_NONE;
	workflow_scheduler_vtime = 0;
	intr_restore(enabled);
}

void workflow_scheduler_forget_domain(
	int domain_id, struct resource_account_handle account)
{
	int enabled = intr_save();
	int slot;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot < 0 || slot >= WORKFLOW_SCHEDULER_ENTITY_CAP)
		goto clear_map;
	if ((workflow_scheduler_entities[slot].flags &
	     AGENT_WORKFLOW_SCHED_F_ACTIVE) &&
	    workflow_scheduler_account_key_valid(account) &&
	    !resource_account_handle_equal(
		    workflow_scheduler_entities[slot].account, account))
		goto out;
	memset(&workflow_scheduler_entities[slot], 0,
	       sizeof(workflow_scheduler_entities[slot]));
clear_map:
	workflow_scheduler_domain_slot[domain_id] =
		WORKFLOW_SCHEDULER_SLOT_NONE;
out:
	intr_restore(enabled);
}

int workflow_scheduler_select(
	const struct workflow_scheduler_candidate *candidates, uint count,
	uint64 now_cycle, uint64 now_tick, int *selected_domain)
{
	int slots[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint planned_latency[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint planned_request_ticks[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint planned_decays[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint64 planned_vruntime[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint64 planned_remaining[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint64 planned_deadline[WORKFLOW_SCHEDULER_ENTITY_CAP];
	uint reserved = 0;
	uint new_slots = 0;
	uint wake_transitions = 0;
	uint64 rebase_offset;
	uint64 planned_vtime;
	int selected = -1;
	int enabled;

	(void)now_cycle;
	if (selected_domain == 0 || candidates == 0 || count == 0 ||
	    count > WORKFLOW_SCHEDULER_ENTITY_CAP)
		return -1;
	*selected_domain = -1;
	for (uint i = 0; i < WORKFLOW_SCHEDULER_ENTITY_CAP; i++)
		slots[i] = WORKFLOW_SCHEDULER_SLOT_NONE;
	enabled = intr_save();
	/* Phase one is read-only: validate every identity and reserve local slots. */
	for (uint i = 0; i < count; i++) {
		int is_new = 0;

		if (!workflow_scheduler_identity_valid(
			    candidates[i].lifecycle, candidates[i].account,
			    candidates[i].domain_id) ||
		    candidates[i].runnable_threads == 0 ||
		    candidates[i].runnable_threads > 0xffffU ||
		    candidates[i].latency_class >
			    AGENT_WORKFLOW_LATENCY_BATCH)
			goto fail;
		for (uint j = 0; j < i; j++)
			if (candidates[j].domain_id ==
				    candidates[i].domain_id ||
			    workflow_lifecycle_key_equal(
				    candidates[j].lifecycle,
				    candidates[i].lifecycle) ||
			    resource_account_handle_equal(
				    candidates[j].account,
				    candidates[i].account))
				goto fail;
		planned_request_ticks[i] = workflow_scheduler_request_ticks(
			candidates[i].latency_class,
			candidates[i].deadline_delta_ticks);
		if (planned_request_ticks[i] == 0)
			goto fail;
		slots[i] = workflow_scheduler_plan_slot_locked(
			&candidates[i], reserved, &is_new);
		if (slots[i] < 0)
			goto fail;
		reserved |= 1U << slots[i];
		if (is_new)
			new_slots |= 1U << i;
		else if (workflow_scheduler_entities[slots[i]].flags &
			 WORKFLOW_SCHEDULER_F_CACHE_INVALID)
			goto fail;
	}

	/* Compute the entire post-selection state locally before any mutation. */
	rebase_offset = workflow_scheduler_rebase_offset_locked();
	planned_vtime = workflow_scheduler_vtime >= rebase_offset ?
				  workflow_scheduler_vtime - rebase_offset : 0;
	for (uint i = 0; i < count; i++) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slots[i]];
		uint64 request_cycles = workflow_scheduler_request_cycles(
			planned_request_ticks[i]);

		planned_decays[i] = 0;
		if (new_slots & (1U << i)) {
			planned_vruntime[i] = planned_vtime;
			planned_remaining[i] = 0;
			planned_latency[i] = AGENT_WORKFLOW_LATENCY_NORMAL;
			wake_transitions |= 1U << i;
		} else {
			planned_vruntime[i] = entity->vruntime - rebase_offset;
			planned_remaining[i] = entity->remaining_cycles;
			planned_latency[i] = entity->latency_class;
			planned_request_ticks[i] = entity->request_ticks;
			if (!(entity->flags &
			      AGENT_WORKFLOW_SCHED_F_RUNNABLE)) {
				wake_transitions |= 1U << i;
				planned_remaining[i] = 0;
				if (entity->flags &
				    AGENT_WORKFLOW_SCHED_F_SLEEPING) {
					uint64 slept =
						now_tick >= entity->sleep_start_tick ?
							now_tick -
								entity->sleep_start_tick :
							0;

					planned_decays[i] = slept /
						WORKFLOW_SCHEDULER_SLEEP_DECAY_TICKS;
					if (planned_decays[i] >
					    WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS)
						planned_decays[i] =
							WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS;
					planned_vruntime[i] =
						workflow_scheduler_decay_toward(
							planned_vruntime[i],
							planned_vtime,
							planned_decays[i]);
				}
			}
		}
		/* Urgency may shorten an open request, never mint more service. */
		if (planned_remaining[i] == 0 ||
		    request_cycles < planned_remaining[i]) {
			planned_remaining[i] = request_cycles;
			planned_request_ticks[i] = workflow_scheduler_request_ticks(
				candidates[i].latency_class,
				candidates[i].deadline_delta_ticks);
			planned_latency[i] = candidates[i].latency_class;
		}
		if (planned_vruntime[i] > ~0ULL - planned_remaining[i])
			goto fail;
		planned_deadline[i] =
			planned_vruntime[i] + planned_remaining[i];
	}
	if (count == 1) {
		if (planned_vruntime[0] > planned_vtime)
			planned_vtime = planned_vruntime[0];
	} else {
		uint64 average = workflow_scheduler_average(
			planned_vruntime, count);

		if (average > planned_vtime)
			planned_vtime = average;
	}
	for (uint i = 0; i < count; i++) {
		if (planned_vruntime[i] <= planned_vtime) {
			if (selected < 0 ||
			    workflow_scheduler_before(candidates,
					      planned_deadline,
					      planned_vruntime, i,
					      (uint)selected))
				selected = i;
		}
	}
	if (selected < 0)
		goto fail;

	/* Phase two commits a plan that can no longer fail. */
	if (rebase_offset != 0)
		workflow_scheduler_rebase_locked();
	for (uint i = 0; i < count; i++) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slots[i]];

		if (new_slots & (1U << i)) {
			memset(entity, 0, sizeof(*entity));
			entity->lifecycle = candidates[i].lifecycle;
			entity->account = candidates[i].account;
			entity->domain_id = candidates[i].domain_id;
			entity->flags = AGENT_WORKFLOW_SCHED_F_ACTIVE;
			entity->earliest_deadline_tick =
				WORKFLOW_SCHEDULER_NO_DEADLINE;
			entity->cached_latency_class =
				(uchar)candidates[i].latency_class;
			entity->schedulable_threads =
				(ushort)candidates[i].runnable_threads;
		}
		workflow_scheduler_domain_slot[candidates[i].domain_id] =
			(signed char)slots[i];
		entity->vruntime = planned_vruntime[i];
		entity->remaining_cycles = planned_remaining[i];
		entity->virtual_deadline = planned_deadline[i];
		entity->request_ticks = (uchar)planned_request_ticks[i];
		entity->latency_class = (uchar)planned_latency[i];
		entity->runnable_threads =
			(ushort)candidates[i].runnable_threads;
		entity->mode = AGENT_WORKFLOW_SCHED_MODE_EEVDF;
		if (wake_transitions & (1U << i)) {
			entity->sleep_decays = workflow_scheduler_counter32_add(
				entity->sleep_decays, planned_decays[i]);
			entity->wake_tick = now_tick;
			entity->flags |= WORKFLOW_SCHEDULER_F_WAKE_PENDING;
		}
		entity->flags &= ~(AGENT_WORKFLOW_SCHED_F_SLEEPING |
				   AGENT_WORKFLOW_SCHED_F_ELIGIBLE |
				   AGENT_WORKFLOW_SCHED_F_FALLBACK);
		entity->flags |= AGENT_WORKFLOW_SCHED_F_RUNNABLE;
		if (planned_vruntime[i] <= planned_vtime)
			entity->flags |= AGENT_WORKFLOW_SCHED_F_ELIGIBLE;
		else
			entity->eligibility_misses =
				workflow_scheduler_counter32_add(
					entity->eligibility_misses, 1);
	}
	workflow_scheduler_vtime = planned_vtime;
	if (candidates[selected].deadline_delta_ticks == 0) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slots[selected]];

		if (!(entity->flags & WORKFLOW_SCHEDULER_F_DEADLINE_MISSED)) {
			entity->flags |= WORKFLOW_SCHEDULER_F_DEADLINE_MISSED;
			entity->deadline_misses = workflow_scheduler_counter32_add(
				entity->deadline_misses, 1);
		}
	}
	*selected_domain = candidates[selected].domain_id;
	intr_restore(enabled);
	return 0;
fail:
	intr_restore(enabled);
	return -1;
}

void workflow_scheduler_note_fallback(
	const struct workflow_scheduler_candidate *candidates, uint count)
{
	int enabled = intr_save();

	if (candidates == 0)
		goto out;
	if (count > WORKFLOW_SCHEDULER_ENTITY_CAP)
		count = WORKFLOW_SCHEDULER_ENTITY_CAP;
	for (uint i = 0; i < count; i++) {
		int slot = workflow_scheduler_find_locked(
			candidates[i].lifecycle, candidates[i].account,
			candidates[i].domain_id);

		if (slot < 0)
			continue;
		workflow_scheduler_entities[slot].mode =
			AGENT_WORKFLOW_SCHED_MODE_FALLBACK;
		workflow_scheduler_entities[slot].flags |=
			AGENT_WORKFLOW_SCHED_F_FALLBACK;
		workflow_scheduler_entities[slot].fallbacks =
			workflow_scheduler_counter32_add(
				workflow_scheduler_entities[slot].fallbacks, 1);
	}
out:
	intr_restore(enabled);
}

static void workflow_scheduler_invalidate_locked(
	struct workflow_scheduler_entity *entity)
{
	if (entity == 0 ||
	    !(entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE))
		return;
	entity->mode = AGENT_WORKFLOW_SCHED_MODE_FALLBACK;
	entity->flags |= AGENT_WORKFLOW_SCHED_F_FALLBACK |
			 WORKFLOW_SCHEDULER_F_CACHE_INVALID;
	entity->fallbacks = workflow_scheduler_counter32_add(
		entity->fallbacks, 1);
}

static void workflow_scheduler_invalidate_domain_locked(int domain_id)
{
	int slot;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		return;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot >= 0 && slot < WORKFLOW_SCHEDULER_ENTITY_CAP)
		workflow_scheduler_invalidate_locked(
			&workflow_scheduler_entities[slot]);
}

static void workflow_scheduler_invalidate_lifecycle_locked(
	struct workflow_lifecycle_key lifecycle)
{
	if (!workflow_lifecycle_key_valid(lifecycle))
		return;
	for (uint slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++)
		if ((workflow_scheduler_entities[slot].flags &
		     AGENT_WORKFLOW_SCHED_F_ACTIVE) &&
		    workflow_lifecycle_key_equal(
			    workflow_scheduler_entities[slot].lifecycle,
			    lifecycle))
			workflow_scheduler_invalidate_locked(
				&workflow_scheduler_entities[slot]);
}

void workflow_scheduler_domain_invalidate(int domain_id)
{
	int enabled = intr_save();

	workflow_scheduler_invalidate_domain_locked(domain_id);
	intr_restore(enabled);
}

int workflow_scheduler_on_enqueue(const struct thread *thread,
				  uint64 now_tick)
{
	const struct proc *process;
	struct workflow_lifecycle_key lifecycle;
	struct resource_account_handle account;
	uint64 deadline_tick = WORKFLOW_SCHEDULER_NO_DEADLINE;
	uint latency_class = AGENT_WORKFLOW_LATENCY_BATCH;
	int schedulable;
	int domain_id;

	if (thread == 0 || (process = thread->process) == 0)
		return -1;
	lifecycle.id = process->workflow_lifecycle_id;
	lifecycle.generation = process->workflow_lifecycle_generation;
	account = thread->resource_account;
	domain_id = thread->resource_domain_id;
	schedulable = process->vm_snapshot_depth == 0 ||
		      process->vm_snapshot_owner_tid == thread->tid;
	if (process->is_agent) {
		latency_class = AGENT_WORKFLOW_LATENCY_NORMAL;
		if (process->agent_event_count_queued > 0 ||
		    thread->agent_loop_state == AGENT_LOOP_WAITING ||
		    (process->heartbeat_interval > 0 &&
		     now_tick >= process->agent_last_heartbeat_tick &&
		     now_tick - process->agent_last_heartbeat_tick >=
			     (uint64)process->heartbeat_interval))
			latency_class = AGENT_WORKFLOW_LATENCY_INTERACTIVE;
		if (thread->agent_wait_deadline_valid) {
			uint64 delta = thread->agent_wait_deadline > now_tick ?
				       thread->agent_wait_deadline - now_tick : 0;

			deadline_tick = thread->agent_wait_deadline;
			if (delta <= 2)
				latency_class = AGENT_WORKFLOW_LATENCY_URGENT;
			else if (delta <= 8 &&
				 latency_class >
					 AGENT_WORKFLOW_LATENCY_INTERACTIVE)
				latency_class =
					AGENT_WORKFLOW_LATENCY_INTERACTIVE;
		}
	}

	int enabled = intr_save();
	int slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);
	int result = -1;

	if (slot < 0 && workflow_scheduler_identity_valid(
				lifecycle, account, domain_id)) {
		slot = workflow_scheduler_alloc_locked(
			lifecycle, account, domain_id);
	}
	if (slot < 0) {
		workflow_scheduler_invalidate_domain_locked(domain_id);
		workflow_scheduler_invalidate_lifecycle_locked(lifecycle);
		goto out;
	}
	if (latency_class > AGENT_WORKFLOW_LATENCY_BATCH ||
	    (schedulable != 0 && schedulable != 1)) {
		workflow_scheduler_invalidate_locked(
			&workflow_scheduler_entities[slot]);
		goto out;
	}
	{
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];
		int was_runnable =
			(entity->flags & AGENT_WORKFLOW_SCHED_F_RUNNABLE) != 0;

		if (entity->flags & WORKFLOW_SCHEDULER_F_CACHE_INVALID)
			goto out;

		if (entity->flags & AGENT_WORKFLOW_SCHED_F_SLEEPING) {
			uint64 slept = now_tick >= entity->sleep_start_tick ?
						 now_tick - entity->sleep_start_tick : 0;
			uint decays = slept /
				WORKFLOW_SCHEDULER_SLEEP_DECAY_TICKS;

			if (decays > WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS)
				decays = WORKFLOW_SCHEDULER_MAX_SLEEP_DECAYS;
			entity->vruntime = workflow_scheduler_decay_toward(
				entity->vruntime, workflow_scheduler_vtime, decays);
			entity->sleep_decays = workflow_scheduler_counter32_add(
				entity->sleep_decays, decays);
			entity->remaining_cycles = 0;
		}
		/* Measure the first enqueue as well as later dormant wakeups. */
		if (!was_runnable) {
			entity->runnable_threads = 0;
			entity->schedulable_threads = 0;
			entity->earliest_deadline_tick =
				WORKFLOW_SCHEDULER_NO_DEADLINE;
			entity->cached_latency_class =
				AGENT_WORKFLOW_LATENCY_BATCH;
			entity->wake_tick = now_tick;
			entity->flags |= WORKFLOW_SCHEDULER_F_WAKE_PENDING;
			entity->flags &= ~WORKFLOW_SCHEDULER_F_DEADLINE_MISSED;
		}
		if (entity->runnable_threads == 0xffffU ||
		    (schedulable && entity->schedulable_threads == 0xffffU)) {
			workflow_scheduler_invalidate_locked(entity);
			goto out;
		}
		entity->runnable_threads++;
		if (schedulable)
			entity->schedulable_threads++;
		if (latency_class < entity->cached_latency_class)
			entity->cached_latency_class = (uchar)latency_class;
		if (deadline_tick < entity->earliest_deadline_tick) {
			entity->earliest_deadline_tick = deadline_tick;
			entity->flags &= ~WORKFLOW_SCHEDULER_F_DEADLINE_MISSED;
		}
		entity->flags &= ~(AGENT_WORKFLOW_SCHED_F_SLEEPING |
				   AGENT_WORKFLOW_SCHED_F_ELIGIBLE);
		entity->flags |= AGENT_WORKFLOW_SCHED_F_RUNNABLE;
		result = 0;
	}
out:
	intr_restore(enabled);
	return result;
}

void workflow_scheduler_on_dequeue(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, int schedulable)
{
	int enabled = intr_save();
	int slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);

	if (slot < 0) {
		workflow_scheduler_invalidate_domain_locked(domain_id);
		workflow_scheduler_invalidate_lifecycle_locked(lifecycle);
		goto out;
	}
	{
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];

		if (entity->flags & WORKFLOW_SCHEDULER_F_CACHE_INVALID)
			goto out;
		if (entity->runnable_threads == 0 ||
		    (schedulable && entity->schedulable_threads == 0)) {
			workflow_scheduler_invalidate_locked(entity);
			goto out;
		}
		entity->runnable_threads--;
		if (schedulable)
			entity->schedulable_threads--;
	}
out:
	intr_restore(enabled);
}

void workflow_scheduler_schedulable_adjust(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, int delta)
{
	int enabled = intr_save();
	int slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);

	if (slot >= 0 && delta != 0) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];
		signed long long adjusted =
			(signed long long)entity->schedulable_threads + delta;

		if (adjusted < 0 || adjusted > entity->runnable_threads ||
		    adjusted > 0xffff)
			workflow_scheduler_invalidate_locked(entity);
		else
			entity->schedulable_threads = (ushort)adjusted;
	}
	intr_restore(enabled);
}

int workflow_scheduler_candidate_get(
	int domain_id, uint64 now_tick,
	struct workflow_scheduler_candidate *candidate)
{
	int enabled = intr_save();
	int result = -1;
	int slot;

	if (candidate == 0 || domain_id < 0 ||
	    domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot < 0 || slot >= WORKFLOW_SCHEDULER_ENTITY_CAP)
		goto out;
	{
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];
		uint latency_class;
		uint64 deadline_delta = WORKFLOW_SCHEDULER_NO_DEADLINE;

		if (!workflow_scheduler_identity_valid(
			    entity->lifecycle, entity->account, domain_id) ||
		    (entity->flags & (AGENT_WORKFLOW_SCHED_F_ACTIVE |
				      AGENT_WORKFLOW_SCHED_F_RUNNABLE)) !=
			    (AGENT_WORKFLOW_SCHED_F_ACTIVE |
			     AGENT_WORKFLOW_SCHED_F_RUNNABLE) ||
		    (entity->flags & WORKFLOW_SCHEDULER_F_CACHE_INVALID) ||
		    entity->runnable_threads == 0 ||
		    entity->schedulable_threads == 0)
			goto out;
		latency_class = entity->cached_latency_class;
		if (entity->earliest_deadline_tick !=
		    WORKFLOW_SCHEDULER_NO_DEADLINE) {
			deadline_delta =
				entity->earliest_deadline_tick > now_tick ?
					entity->earliest_deadline_tick - now_tick : 0;
			if (deadline_delta <= 2)
				latency_class = AGENT_WORKFLOW_LATENCY_URGENT;
			else if (deadline_delta <= 8 &&
				 latency_class >
					 AGENT_WORKFLOW_LATENCY_INTERACTIVE)
				latency_class =
					AGENT_WORKFLOW_LATENCY_INTERACTIVE;
		}
		memset(candidate, 0, sizeof(*candidate));
		candidate->lifecycle = entity->lifecycle;
		candidate->account = entity->account;
		candidate->domain_id = domain_id;
		candidate->latency_class = latency_class;
		candidate->runnable_threads = entity->runnable_threads;
		candidate->deadline_delta_ticks = deadline_delta;
		result = 1;
	}
out:
	intr_restore(enabled);
	return result;
}

void workflow_scheduler_on_sleep(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, uint64 now_tick)
{
	int enabled = intr_save();
	int slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);

	if (slot >= 0) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];

		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_SLEEPING))
			entity->sleep_start_tick = now_tick;
		entity->flags &= ~(AGENT_WORKFLOW_SCHED_F_RUNNABLE |
				   AGENT_WORKFLOW_SCHED_F_ELIGIBLE |
				   WORKFLOW_SCHEDULER_F_WAKE_PENDING |
				   WORKFLOW_SCHEDULER_F_DEADLINE_MISSED);
		entity->flags |= AGENT_WORKFLOW_SCHED_F_SLEEPING;
		entity->runnable_threads = 0;
		entity->schedulable_threads = 0;
		entity->cached_latency_class =
			AGENT_WORKFLOW_LATENCY_BATCH;
		entity->earliest_deadline_tick =
			WORKFLOW_SCHEDULER_NO_DEADLINE;
		entity->remaining_cycles = 0;
	}
	intr_restore(enabled);
}

int workflow_scheduler_runnable_domains(int *domain_ids, uint capacity)
{
	int enabled = intr_save();
	uint count = 0;

	for (uint slot = 0; slot < WORKFLOW_SCHEDULER_ENTITY_CAP; slot++) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];

		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE) ||
		    !(entity->flags & AGENT_WORKFLOW_SCHED_F_RUNNABLE) ||
		    (entity->flags & WORKFLOW_SCHEDULER_F_CACHE_INVALID) ||
		    entity->runnable_threads == 0 ||
		    entity->schedulable_threads == 0)
			continue;
		if (domain_ids != 0 && count < capacity)
			domain_ids[count] = entity->domain_id;
		count++;
	}
	intr_restore(enabled);
	if (count > (uint)0x7fffffff)
		return -1;
	return (int)count;
}

int workflow_scheduler_domain_runnable(int domain_id)
{
	int enabled = intr_save();
	int runnable = 0;
	int slot;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot >= 0 && slot < WORKFLOW_SCHEDULER_ENTITY_CAP &&
	    (workflow_scheduler_entities[slot].flags &
	     (AGENT_WORKFLOW_SCHED_F_ACTIVE |
	      AGENT_WORKFLOW_SCHED_F_RUNNABLE)) ==
		    (AGENT_WORKFLOW_SCHED_F_ACTIVE |
		     AGENT_WORKFLOW_SCHED_F_RUNNABLE) &&
	    !(workflow_scheduler_entities[slot].flags &
	      WORKFLOW_SCHEDULER_F_CACHE_INVALID) &&
	    workflow_scheduler_entities[slot].runnable_threads != 0 &&
	    workflow_scheduler_entities[slot].schedulable_threads != 0)
		runnable = 1;
out:
	intr_restore(enabled);
	return runnable;
}

int workflow_scheduler_domain_tracked(int domain_id)
{
	int enabled = intr_save();
	int tracked = 0;
	int slot;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot >= 0 && slot < WORKFLOW_SCHEDULER_ENTITY_CAP &&
	    (workflow_scheduler_entities[slot].flags &
	     AGENT_WORKFLOW_SCHED_F_ACTIVE))
		tracked = 1;
out:
	intr_restore(enabled);
	return tracked;
}

void workflow_scheduler_domain_idle(int domain_id, uint64 now_tick)
{
	int enabled = intr_save();
	int slot;

	if (domain_id < 0 || domain_id >= PROC_RESOURCE_DOMAIN_CAP)
		goto out;
	slot = workflow_scheduler_domain_slot[domain_id];
	if (slot >= 0 && slot < WORKFLOW_SCHEDULER_ENTITY_CAP) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];

		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_ACTIVE))
			goto out;
		if (!(entity->flags & AGENT_WORKFLOW_SCHED_F_SLEEPING))
			entity->sleep_start_tick = now_tick;
		entity->flags &= ~(AGENT_WORKFLOW_SCHED_F_RUNNABLE |
				   AGENT_WORKFLOW_SCHED_F_ELIGIBLE |
				   WORKFLOW_SCHEDULER_F_WAKE_PENDING |
				   WORKFLOW_SCHEDULER_F_DEADLINE_MISSED);
		entity->flags |= AGENT_WORKFLOW_SCHED_F_SLEEPING;
		entity->runnable_threads = 0;
		entity->schedulable_threads = 0;
		entity->cached_latency_class =
			AGENT_WORKFLOW_LATENCY_BATCH;
		entity->earliest_deadline_tick =
			WORKFLOW_SCHEDULER_NO_DEADLINE;
		entity->remaining_cycles = 0;
	}
out:
	intr_restore(enabled);
}

void workflow_scheduler_on_dispatch(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id, uint64 now_tick)
{
	int enabled = intr_save();
	int slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);

	if (slot < 0 && workflow_scheduler_identity_valid(
				lifecycle, account, domain_id)) {
		slot = workflow_scheduler_alloc_locked(
			lifecycle, account, domain_id);
	}
	if (slot >= 0) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];
		if (entity->earliest_deadline_tick !=
			    WORKFLOW_SCHEDULER_NO_DEADLINE &&
		    now_tick >= entity->earliest_deadline_tick &&
		    !(entity->flags & WORKFLOW_SCHEDULER_F_DEADLINE_MISSED)) {
			entity->flags |= WORKFLOW_SCHEDULER_F_DEADLINE_MISSED;
			entity->deadline_misses = workflow_scheduler_counter32_add(
				entity->deadline_misses, 1);
		}
		if (entity->flags & WORKFLOW_SCHEDULER_F_WAKE_PENDING) {
			uint64 wakeup = now_tick >= entity->wake_tick ?
						 now_tick - entity->wake_tick : 0;
			uint bucket = wakeup <= 1 ? 0 :
				      wakeup <= 2 ? 1 : wakeup <= 8 ? 2 : 3;

			if (wakeup > entity->max_wakeup_ticks)
				entity->max_wakeup_ticks = wakeup > (uint)-1 ?
							   (uint)-1 : (uint)wakeup;
			entity->wakeup_latency_buckets[bucket] =
				workflow_scheduler_counter32_add(
					entity->wakeup_latency_buckets[bucket], 1);
			entity->flags &= ~WORKFLOW_SCHEDULER_F_WAKE_PENDING;
		}

		entity->dispatches = workflow_scheduler_counter32_add(
			entity->dispatches, 1);
		if (entity->remaining_cycles == 0) {
			uint latency_class = entity->cached_latency_class;
			uint64 deadline_delta = WORKFLOW_SCHEDULER_NO_DEADLINE;
			uint request_ticks;

			if (latency_class > AGENT_WORKFLOW_LATENCY_BATCH)
				latency_class = AGENT_WORKFLOW_LATENCY_NORMAL;
			if (entity->earliest_deadline_tick !=
			    WORKFLOW_SCHEDULER_NO_DEADLINE)
				deadline_delta =
					entity->earliest_deadline_tick > now_tick ?
						entity->earliest_deadline_tick -
							now_tick : 0;
			request_ticks = workflow_scheduler_request_ticks(
				latency_class, deadline_delta);
			if (request_ticks == 0)
				request_ticks = 4;
			entity->request_ticks = (uchar)request_ticks;
			entity->latency_class = (uchar)latency_class;
			entity->remaining_cycles =
				workflow_scheduler_request_cycles(request_ticks);
			entity->virtual_deadline =
				workflow_scheduler_add_sat(
					entity->vruntime,
					entity->remaining_cycles);
		}
		entity->mode = AGENT_WORKFLOW_SCHED_MODE_EEVDF;
		entity->flags &= ~AGENT_WORKFLOW_SCHED_F_FALLBACK;
		entity->flags |= AGENT_WORKFLOW_SCHED_F_ELIGIBLE;
	}
	intr_restore(enabled);
}

void workflow_scheduler_charge(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id,
	uint64 service_cycles)
{
	int enabled = intr_save();
	int slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);

	if (slot >= 0) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];
		int other_runnable = 0;

		entity->service_cycles = workflow_scheduler_counter_add(
			entity->service_cycles, service_cycles);
		/* All workflow entities have one fixed weight, independent of threads. */
		entity->vruntime = workflow_scheduler_add_sat(
			entity->vruntime, service_cycles);
		if (service_cycles >= entity->remaining_cycles)
			entity->remaining_cycles = 0;
		else
			entity->remaining_cycles -= service_cycles;
		entity->virtual_deadline = workflow_scheduler_add_sat(
			entity->vruntime, entity->remaining_cycles);
		for (uint i = 0; i < WORKFLOW_SCHEDULER_ENTITY_CAP; i++) {
			const struct workflow_scheduler_entity *other =
				&workflow_scheduler_entities[i];

			if ((int)i == slot)
				continue;
			if ((other->flags &
			     (AGENT_WORKFLOW_SCHED_F_ACTIVE |
			      AGENT_WORKFLOW_SCHED_F_RUNNABLE)) ==
			    (AGENT_WORKFLOW_SCHED_F_ACTIVE |
			     AGENT_WORKFLOW_SCHED_F_RUNNABLE) &&
			    !(other->flags & WORKFLOW_SCHEDULER_F_CACHE_INVALID) &&
			    other->runnable_threads != 0 &&
			    other->schedulable_threads != 0) {
				other_runnable = 1;
				break;
			}
		}
		if (!other_runnable && entity->vruntime > workflow_scheduler_vtime)
			workflow_scheduler_vtime = entity->vruntime;
	}
	intr_restore(enabled);
}

int workflow_scheduler_snapshot_get(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle account, int domain_id,
	struct workflow_scheduler_snapshot *snapshot)
{
	int enabled = intr_save();
	int slot;
	int result = -1;

	if (snapshot == 0)
		goto out;
	memset(snapshot, 0, sizeof(*snapshot));
	slot = workflow_scheduler_find_locked(
		lifecycle, account, domain_id);
	if (slot >= 0) {
		struct workflow_scheduler_entity *entity =
			&workflow_scheduler_entities[slot];

		snapshot->mode = entity->mode;
		snapshot->flags = entity->flags &
		(AGENT_WORKFLOW_SCHED_F_ACTIVE |
		 AGENT_WORKFLOW_SCHED_F_RUNNABLE |
		 AGENT_WORKFLOW_SCHED_F_ELIGIBLE |
		 AGENT_WORKFLOW_SCHED_F_SLEEPING |
		 AGENT_WORKFLOW_SCHED_F_FALLBACK);
		snapshot->latency_class = entity->latency_class;
		snapshot->weight = WORKFLOW_SCHEDULER_BASE_WEIGHT;
		snapshot->runnable = entity->runnable_threads;
		snapshot->request_ticks = entity->request_ticks;
		snapshot->remaining_cycles = entity->remaining_cycles;
		snapshot->lag_cycles = workflow_scheduler_lag(
			workflow_scheduler_vtime, entity->vruntime);
		snapshot->vruntime = entity->vruntime;
		snapshot->virtual_deadline = entity->virtual_deadline;
		snapshot->dispatches = entity->dispatches;
		snapshot->service_cycles = entity->service_cycles;
		snapshot->sleep_decays = entity->sleep_decays;
		snapshot->eligibility_misses = entity->eligibility_misses;
		snapshot->fallbacks = entity->fallbacks;
		snapshot->max_wakeup_ticks = entity->max_wakeup_ticks;
		snapshot->deadline_misses = entity->deadline_misses;
		for (uint i = 0; i < WORKFLOW_SCHEDULER_WAKE_BUCKETS; i++) {
			snapshot->wakeup_latency_buckets[i] =
				entity->wakeup_latency_buckets[i];
			snapshot->wakeup_samples =
				workflow_scheduler_counter_add(
					snapshot->wakeup_samples,
					entity->wakeup_latency_buckets[i]);
		}
		result = 0;
	}
out:
	intr_restore(enabled);
	return result;
}
