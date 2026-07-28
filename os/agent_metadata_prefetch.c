#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_actions.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_prefetch.h"
#include "defs.h"
#include "trap.h"

struct prefetch_namespace {
	char project[AGENT_FILE_PROJECT_SIZE];
	char workflow[AGENT_FILE_WORKFLOW_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
};

struct prefetch_selection {
	int source_slot;
	int target_slot;
	char stage[AGENT_FILE_FIELD_SIZE];
};

static int prefetch_hit_valid(uint64 generation, uint scope_id, int slot,
			      struct agent_file_hit *hit)
{
	struct agent_catalog_view view;
	int valid;

	if (hit == 0 || slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    agent_metadata_catalog_borrow(generation, slot, &view) <= 0)
		return 0;
	valid = agent_object_scope_visible(scope_id, view.scope_id) &&
		view.meta->dev == hit->dev && view.meta->inum == hit->inum &&
		view.meta->incarnation == hit->incarnation &&
		view.meta->fid == hit->fid;
	view.meta = 0;
	return valid;
}

static int
prefetch_target_matches(const char *stage, const char *project,
			const char *workflow, const char *run_id,
			const struct agent_file_meta *target)
{
	return (!stage || !stage[0] ||
		strncmp(target->stage, stage, sizeof(target->stage)) == 0) &&
	       (!project || !project[0] ||
		strncmp(target->project, project, sizeof(target->project)) == 0) &&
	       (!workflow || !workflow[0] ||
		strncmp(target->workflow, workflow,
			sizeof(target->workflow)) == 0) &&
	       (!run_id || !run_id[0] ||
		strncmp(target->run_id, run_id, sizeof(target->run_id)) == 0);
}

static int prefetch_count_stage(uint64 generation, int source_slot, char *stage)
{
	struct agent_catalog_view source;
	struct agent_catalog_view entry;
	struct prefetch_namespace namespace;
	uint source_scope;
	int count = 0;
	int slot;

	if (!stage[0] || agent_metadata_catalog_borrow(
				 generation, source_slot, &source) <= 0)
		return 0;
	memmove(namespace.project, source.meta->project, sizeof(namespace.project));
	memmove(namespace.workflow, source.meta->workflow,
		sizeof(namespace.workflow));
	memmove(namespace.run_id, source.meta->run_id, sizeof(namespace.run_id));
	source_scope = source.scope_id;
	source.meta = 0;
	for (slot = agent_metadata_catalog_index_seek(
		     generation, AGENT_CATALOG_INDEX_STAGE, stage, -1, 0, 0);
	     slot >= 0;
	     slot = agent_metadata_catalog_index_seek(
		     generation, AGENT_CATALOG_INDEX_STAGE, 0, slot, 0, 0)) {
		agent_metadata_txn_work_charge(1);
		if (agent_metadata_catalog_borrow(generation, slot, &entry) > 0 &&
		    source_scope == entry.scope_id &&
		    prefetch_target_matches(
			    stage, namespace.project, namespace.workflow,
			    namespace.run_id, entry.meta))
			count++;
		entry.meta = 0;
	}
	return count;
}

static void __attribute__((noinline))
prefetch_hint_build(struct agent_file_prefetch_hint *hint,
		    const struct agent_file_meta *target, uint scope_id,
		    int source_fid, uint64 source_sequence, uint64 span_id,
		    uint64 reason, int source_pid, int target_pid, int candidates)
{
	memset(hint, 0, sizeof(*hint));
	hint->source_sequence = source_sequence;
	hint->span_id = span_id;
	hint->reason = reason;
	hint->fs_generation = agent_file_state_scope_generation(scope_id);
	hint->fid = target->fid;
	hint->source_fid = source_fid;
	hint->source_pid = source_pid;
	hint->target_pid = target_pid;
	hint->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
	hint->candidate_records = candidates;
	hint->total_hits = candidates;
	hint->score = 1000 + candidates;
	if (strncmp(target->status, "pending", AGENT_FILE_FIELD_SIZE) == 0) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_PENDING;
		hint->score += 100;
	}
	if (target->stage[0]) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
		hint->score += 50;
	}
	agent_file_state_project_hit(&hint->hit, target, scope_id);
}

static struct agent_file_prefetch_hint * __attribute__((noinline))
prefetch_ring_publish(struct proc *p,
		      struct agent_file_prefetch_hint *published,
		      uint64 span_owner, int charge_scan, int clamp_visible)
{
	int slot;
	int visible;

	for (int i = 0; i < p->agent_prefetch_count; i++) {
		slot = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
			p->agent_prefetch_count + i) %
		       AGENT_FILE_PREFETCH_MAX_HINTS;
		if (charge_scan)
			agent_metadata_txn_work_charge(1);
		if (p->agent_prefetch_hints[slot].fid == published->fid)
			goto fill;
	}
	slot = p->agent_prefetch_head % AGENT_FILE_PREFETCH_MAX_HINTS;
	p->agent_prefetch_head =
		(p->agent_prefetch_head + 1) % AGENT_FILE_PREFETCH_MAX_HINTS;
	if (p->agent_prefetch_count < AGENT_FILE_PREFETCH_MAX_HINTS)
		p->agent_prefetch_count++;

fill:
	published->sequence = ++p->agent_prefetch_sequence;
	published->tick = agent_file_state_now();
	visible = clamp_visible ?
		MIN(p->agent_prefetch_count, AGENT_FILE_PREFETCH_MAX_HINTS) :
		p->agent_prefetch_count;
	published->score += visible;
	memmove(&p->agent_prefetch_hints[slot], published, sizeof(*published));
	p->agent_prefetch_span_owner[slot] = span_owner;
	return &p->agent_prefetch_hints[slot];
}

static void __attribute__((noinline))
prefetch_store(struct proc *p, uint64 catalog_generation,
	       int source_slot, int target_slot, uint64 source_sequence,
	       uint64 reason, int candidates)
{
	struct agent_catalog_view source;
	struct agent_catalog_view target;
	struct agent_file_prefetch_hint published;
	struct agent_file_prefetch_hint *hint;
	uint64 span_id;
	uint64 span_owner;
	uint source_scope;
	int source_fid;

	if (!p || !p->is_agent ||
	    agent_metadata_catalog_borrow(catalog_generation, source_slot,
					  &source) <= 0 ||
	    agent_metadata_catalog_borrow(catalog_generation, target_slot,
					  &target) <= 0 ||
	    agent_identity_proc_scope(p) != source.scope_id ||
	    source.scope_id != target.scope_id)
		return;
	source_scope = source.scope_id;
	source_fid = source.meta->fid;
	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	if (span_id == 0 || span_owner == 0) {
		source.meta = target.meta = 0;
		return;
	}
	prefetch_hint_build(
		&published, target.meta, source_scope, source_fid,
		source_sequence, span_id, reason,
		p->pid, p->pid, candidates);
	source.meta = target.meta = 0;
	hint = prefetch_ring_publish(
		p, &published, span_owner, 1, 1);
	agent_observe_record_prefetch(
		p, hint, span_owner, hint->hit.stage, 1);
	agent_metadata_txn_work_charge(
		2U * AGENT_AUDIT_MAX_RECORDS + 5U * NPROC);
}

void agent_metadata_prefetch_update(struct proc *p,
				    struct agent_file_query *q,
				    struct agent_file_query_result *r,
				    int *hit_slots,
				    uint64 source_sequence)
{
	uint64 fallback_targets[AGENT_FILE_QUERY_MAX_HITS]
			       [(AGENT_FILE_META_MAX + 63) / 64];
	uint64 selected_targets[(AGENT_FILE_META_MAX + 63) / 64];
	uint64 explicit_target_masks[AGENT_FILE_QUERY_MAX_HITS];
	int dependency_slots[AGENT_FILE_QUERY_MAX_HITS]
			    [AGENT_METADATA_DEPENDENCY_SCOPE_LIMIT];
	int dependency_counts[AGENT_FILE_QUERY_MAX_HITS];
	int source_slots[AGENT_FILE_QUERY_MAX_HITS];
	struct prefetch_selection selected[AGENT_FILE_PREFETCH_MAX_HINTS];
	struct agent_catalog_view source_view;
	struct agent_catalog_view target_view;
	const struct agent_metadata_dependency_view *dependency;
	const struct agent_file_meta *source;
	const struct agent_file_meta *target;
	uint64 target_bit;
	uint64 reason;
	uint64 catalog_generation;
	uint scope_id;
	uint target_work;
	int source_count;
	int selected_count = 0;
	int explicit_found[AGENT_FILE_QUERY_MAX_HITS];

	if (!p || !p->is_agent || !q || !r || !hit_slots ||
	    r->returned <= 0)
		return;
	if (!agent_metadata_txn_lock(1))
		return;
	scope_id = agent_identity_proc_scope(p);
	catalog_generation = agent_metadata_catalog_generation();
	source_count = r->returned;
	if (source_count > AGENT_FILE_QUERY_MAX_HITS)
		source_count = AGENT_FILE_QUERY_MAX_HITS;
	memset(fallback_targets, 0, sizeof(fallback_targets));
	memset(selected_targets, 0, sizeof(selected_targets));
	memset(explicit_target_masks, 0, sizeof(explicit_target_masks));
	memset(dependency_counts, 0, sizeof(dependency_counts));
	memset(source_slots, -1, sizeof(source_slots));
	memset(explicit_found, 0, sizeof(explicit_found));

	for (int h = 0; h < source_count; h++)
		if (prefetch_hit_valid(catalog_generation, scope_id,
				       hit_slots[h], &r->hits[h]))
			source_slots[h] = hit_slots[h];

	for (int d = 0; d < AGENT_METADATA_DEPENDENCY_MAX; d++) {
		agent_metadata_txn_work_charge(1);
		dependency = agent_metadata_actions_dependency_borrow(d);
		if (dependency == 0 || dependency->scope_id != scope_id)
			goto next_dependency;
		for (int h = 0; h < source_count; h++) {
			if (source_slots[h] < 0)
				continue;
			if (agent_metadata_catalog_borrow(
				    catalog_generation, source_slots[h],
				    &source_view) <= 0)
				continue;
			if (source_view.scope_id != scope_id)
				goto next_source_dependency;
			source = source_view.meta;
			if (!prefetch_target_matches(
				    dependency->source, dependency->namespace, 0,
				    dependency->run_id, source))
				goto next_source_dependency;
			if (dependency_counts[h] >=
			    AGENT_METADATA_DEPENDENCY_SCOPE_LIMIT)
				goto next_source_dependency;
			dependency_slots[h][dependency_counts[h]++] = d;
			explicit_target_masks[h] |=
				agent_metadata_actions_label_bit(dependency->target);
next_source_dependency:
			source_view.meta = 0;
		}
next_dependency:
		;
	}

	target_work = source_count + 1;
	for (int h = 0; h < source_count; h++)
		target_work += dependency_counts[h];
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(target_work);
		if (agent_metadata_catalog_borrow(
			    catalog_generation, i, &target_view) <= 0 ||
		    target_view.scope_id != scope_id ||
		    target_view.meta->stage[0] == 0)
			goto next_target;
		target = target_view.meta;
		target_bit = agent_metadata_actions_label_bit(target->stage);
		for (int h = 0; h < source_count; h++) {
			int explicit_match = 0;
			int source_slot = source_slots[h];

			if (source_slot < 0 || source_slot == i)
				goto next_source;
			if (agent_metadata_catalog_borrow(
				    catalog_generation, source_slot,
				    &source_view) <= 0 ||
			    source_view.scope_id != scope_id)
				goto next_source;
			source = source_view.meta;
			if ((explicit_target_masks[h] & target_bit) != 0)
				for (int k = 0; k < dependency_counts[h]; k++) {
					const struct agent_metadata_dependency_view *dep;

					dep = agent_metadata_actions_dependency_borrow(
						dependency_slots[h][k]);
					if (dep == 0)
						continue;

					if (!prefetch_target_matches(
						    dep->target, dep->namespace,
						    source->workflow, dep->run_id, target))
						continue;
					explicit_match = 1;
					break;
				}
			if (explicit_match) {
				explicit_found[h] = 1;
				if (selected_count <
					    AGENT_FILE_PREFETCH_MAX_HINTS &&
				    (selected_targets[i / 64] &
				     (1ULL << (i % 64))) == 0) {
					selected_targets[i / 64] |=
						1ULL << (i % 64);
					selected[selected_count].source_slot = source_slot;
					selected[selected_count].target_slot = i;
					memmove(selected[selected_count].stage,
						target->stage,
						sizeof(selected[selected_count].stage));
					selected_count++;
				}
			}
			if (target_bit != 0 &&
			    (source->dependency_mask & target_bit) != 0 &&
			    prefetch_target_matches(
				    0, source->project, source->workflow,
				    source->run_id, target))
				fallback_targets[h][i / 64] |=
					1ULL << (i % 64);
next_source:
			source_view.meta = 0;
		}
next_target:
		target_view.meta = 0;
	}

	for (int h = 0; h < source_count &&
				selected_count < AGENT_FILE_PREFETCH_MAX_HINTS; h++) {
		if (source_slots[h] < 0 || explicit_found[h])
			continue;
		for (int word = 0;
		     word < (AGENT_FILE_META_MAX + 63) / 64 &&
			     selected_count < AGENT_FILE_PREFETCH_MAX_HINTS;
		     word++) {
			uint64 bits = fallback_targets[h][word];

			agent_metadata_txn_work_charge(1);
			for (int bit = 0; bit < 64 &&
				     selected_count <
					     AGENT_FILE_PREFETCH_MAX_HINTS;
			     bit++) {
				int slot = word * 64 + bit;

				if (slot >= AGENT_FILE_META_MAX ||
				    (bits & (1ULL << bit)) == 0 ||
				    (selected_targets[word] &
				     (1ULL << bit)) != 0)
					continue;
				selected_targets[word] |= 1ULL << bit;
				selected[selected_count].source_slot = source_slots[h];
				selected[selected_count].target_slot = slot;
				selected[selected_count].stage[0] = 0;
				if (agent_metadata_catalog_borrow(
					    catalog_generation, slot,
					    &target_view) > 0 &&
				    target_view.scope_id == scope_id)
					memmove(selected[selected_count].stage,
						target_view.meta->stage,
						sizeof(selected[selected_count].stage));
				target_view.meta = 0;
				selected_count++;
			}
		}
	}

	reason = AGENT_FILE_PREFETCH_REASON_DEPENDENCY |
		 AGENT_FILE_PREFETCH_REASON_SAME_RUN;
	if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) || r->used_index)
		reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
	for (int i = 0; i < selected_count; i++) {
		int candidates;

		if (!selected[i].stage[0])
			continue;
		candidates = prefetch_count_stage(
			catalog_generation, selected[i].source_slot,
			selected[i].stage);
		prefetch_store(
			p, catalog_generation, selected[i].source_slot,
			selected[i].target_slot, source_sequence, reason,
			candidates);
	}
	agent_metadata_txn_unlock();
}

int agent_metadata_prefetch_handoff(
	struct agent_endpoint_handle *target_handle,
	struct agent_endpoint_handle *source_handle)
{
	struct agent_file_prefetch_hint source_hint;
	struct agent_file_prefetch_hint published;
	struct agent_catalog_view source_view;
	struct agent_catalog_view target_view;
	struct agent_catalog_resolution lookup;
	struct agent_file_meta selector;
	struct proc *source;
	struct proc *target;
	uint64 reason;
	uint64 catalog_generation;
	uint64 span_id;
	uint64 span_owner;
	int source_pid;
	int candidates;
	int visible;
	int start;
	int slot;
	int source_slot;
	int target_slot;
	int copied = 0;
	int enabled;

	if (target_handle == 0 || source_handle == 0 ||
	    target_handle->slot == source_handle->slot ||
	    target_handle->scope_id != source_handle->scope_id ||
	    !agent_scope_valid(source_handle->scope_id))
		return 0;
	if (!agent_metadata_txn_lock(0))
		return 0;
	catalog_generation = agent_metadata_catalog_generation();
	memset(&selector, 0, sizeof(selector));

	enabled = intr_save();
	source = agent_ipc_endpoint_resolve_locked(source_handle);
	target = agent_ipc_endpoint_resolve_locked(target_handle);
	if (source == 0 || target == 0) {
		intr_restore(enabled);
		goto out;
	}
	visible = MIN(source->agent_prefetch_count,
		      AGENT_FILE_PREFETCH_MAX_HINTS);
	start = (source->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	intr_restore(enabled);

	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		/* Do not retain endpoints across checkpoints. */
		enabled = intr_save();
		source = agent_ipc_endpoint_resolve_locked(source_handle);
		if (source == 0) {
			intr_restore(enabled);
			break;
		}
		memmove(&source_hint, &source->agent_prefetch_hints[slot],
			sizeof(source_hint));
		source_pid = source_hint.source_pid ?
				     source_hint.source_pid : source_handle->pid;
		if (source_hint.span_id) {
			span_id = source_hint.span_id;
			span_owner = source->agent_prefetch_span_owner[slot];
		} else {
			span_id = source->agent_current_span_id;
			span_owner = source->agent_current_span_owner;
		}
		intr_restore(enabled);

		agent_metadata_txn_work_charge(1);
		if (span_id == 0 || span_owner == 0)
			continue;
		selector.fid = source_hint.source_fid;
		agent_metadata_catalog_resolve(
			source_handle->scope_id, &selector, -1, &lookup);
		source_slot = lookup.matched == AGENT_CATALOG_KEY_FID ?
			      lookup.slot : -1;
		selector.fid = source_hint.fid;
		agent_metadata_catalog_resolve(
			source_handle->scope_id, &selector, -1, &lookup);
		target_slot = lookup.matched == AGENT_CATALOG_KEY_FID ?
			      lookup.slot : -1;
		if (source_slot < 0 || target_slot < 0)
			continue;
		if (agent_metadata_catalog_borrow(
			    catalog_generation, source_slot, &source_view) <= 0)
			continue;
		if (agent_metadata_catalog_borrow(
			    catalog_generation, target_slot, &target_view) <= 0) {
			source_view.meta = 0;
			continue;
		}
		if (source_view.scope_id != source_handle->scope_id ||
		    target_view.scope_id != source_handle->scope_id) {
			source_view.meta = 0;
			target_view.meta = 0;
			continue;
		}
		reason = source_hint.reason |
			 AGENT_FILE_PREFETCH_REASON_HANDOFF;
		candidates = source_hint.candidate_records > 0 ?
				     source_hint.candidate_records : 1;
		prefetch_hint_build(
			&published, target_view.meta, target_view.scope_id,
			source_view.meta->fid, source_hint.source_sequence,
			span_id, reason, source_pid, target_handle->pid,
			candidates);
		source_view.meta = 0;
		target_view.meta = 0;

		/* Prepay before commit. */
		agent_metadata_txn_work_charge(
			2U * AGENT_FILE_PREFETCH_SPAN_MAX +
			2U * AGENT_AUDIT_MAX_RECORDS + 5U * NPROC);
		enabled = intr_save();
		target = agent_ipc_endpoint_resolve_locked(target_handle);
		if (target == 0) {
			intr_restore(enabled);
			continue;
		}
		prefetch_ring_publish(
			target, &published, span_owner, 0, 0);
		agent_observe_record_prefetch_handoff_locked(
			source_handle->pid, source_handle->control_id, target,
			&published, span_owner, published.hit.stage,
			published.reason);
		intr_restore(enabled);
		copied++;
	}
out:
	agent_metadata_txn_unlock();
	return copied;
}
static int __attribute__((noinline))
prefetch_snapshot(struct proc *p, uint64 hintsaddr, int max, int spans)
{
	struct agent_file_prefetch_hint hint;
	int visible;
	int n;
	int start;
	int slot;
	int result;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (spans) {
		result = agent_observe_prefetch_span_snapshot(p, hintsaddr, max);
		goto out_txn;
	}
	visible = MIN(p->agent_prefetch_count, AGENT_FILE_PREFETCH_MAX_HINTS);
	if (max == 0) {
		result = visible;
		goto out_txn;
	}
	if (hintsaddr == 0) {
		result = AGENT_STATUS_BAD_PARAM;
		goto out_txn;
	}
	n = visible < max ? visible : max;
	start = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < n; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
		if (copyout(p->pagetable,
			    hintsaddr +
				    i * sizeof(struct agent_file_prefetch_hint),
			    (char *)&hint, sizeof(hint)) < 0) {
			result = -1;
			goto out_txn;
		}
	}
	result = n;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_prefetch_snapshot(uint64 hintsaddr, int max)
{
	return prefetch_snapshot(curr_proc(), hintsaddr, max, 0);
}

int sys_agent_file_prefetch_span_snapshot(uint64 hintsaddr, int max)
{
	return prefetch_snapshot(curr_proc(), hintsaddr, max, 1);
}
