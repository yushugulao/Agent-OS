#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_actions.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "vfs_security.h"

#define AGENT_ACTION_HISTORY_MAX 32
#define AGENT_ACTION_SCOPE_LIMIT 8

_Static_assert(VFS_SCOPE_MAX_ACTIVE *
	       AGENT_METADATA_DEPENDENCY_SCOPE_LIMIT <=
	       AGENT_METADATA_DEPENDENCY_MAX,
	       "dependency table must reserve every workflow partition");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_ACTION_SCOPE_LIMIT <=
	       AGENT_ACTION_HISTORY_MAX,
	       "action table must reserve every workflow partition");

struct agent_action_history_entry {
	int tool_id;
	uint scope_id;
	uint64 sequence;
	uint64 request_id;
	char project[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
};

struct agent_status_batch_undo {
	int slot;
	char text[AGENT_FILE_FIELD_SIZE + AGENT_FILE_SUMMARY_SIZE];
	uint64 updated_tick;
	uint64 fs_generation;
};

struct agent_text_field {
	const char *text;
	ushort offset, size;
};

#define AGENT_DEPENDENCY_FIELD(text_value, field) \
	{ text_value, (ushort)__builtin_offsetof( \
		struct agent_metadata_dependency_view, field), \
	  (ushort)sizeof(((struct agent_metadata_dependency_view *)0)->field) }
static const struct agent_text_field agent_dependency_fields[] = {
	AGENT_DEPENDENCY_FIELD("source\0from\0label\0", source),
	AGENT_DEPENDENCY_FIELD("target\0to\0", target),
	AGENT_DEPENDENCY_FIELD("namespace\0project\0", namespace),
	AGENT_DEPENDENCY_FIELD("run_id\0run\0", run_id),
	AGENT_DEPENDENCY_FIELD("relation\0", relation),
	AGENT_DEPENDENCY_FIELD("summary\0", summary),
};
#undef AGENT_DEPENDENCY_FIELD

_Static_assert(__builtin_offsetof(struct agent_file_meta, summary) ==
	       __builtin_offsetof(struct agent_file_meta, status) +
	       AGENT_FILE_FIELD_SIZE,
	       "status rollback snapshot must remain contiguous");

static struct agent_action_history_entry
	agent_action_history[AGENT_ACTION_HISTORY_MAX];
/* Explicit user edges only; file dependency masks are resolved on demand. */
static struct agent_metadata_dependency_view
	agent_dependencies[AGENT_METADATA_DEPENDENCY_MAX];
static uint64 agent_action_next_sequence;
static uint64 agent_dependency_generation;
/* Serialized by the metadata transaction; bounded by one workflow partition. */
static struct agent_status_batch_undo
	agent_status_batch_undo[AGENT_FILE_SCOPE_LIMIT];

static void
agent_text_append(char *dst, int n, const char *src)
{
	int len;

	if (n <= 0 || src == 0)
		return;
	len = strlen(dst);
	if (len < n - 1)
		safestrcpy(dst + len, src, n - len);
}

static void
agent_text_field_append(char *out, int n, const char *prefix,
			const char *value)
{
	if (!value[0])
		return;
	agent_text_append(out, n, prefix);
	agent_text_append(out, n, value);
}

void
agent_metadata_actions_format_file_event(const struct agent_file_meta *meta,
					 char *out, int n)
{
	memset(out, 0, n);
	agent_text_field_append(out, n, "status=", meta->status);
	agent_text_field_append(out, n, ";stage=", meta->stage);
	agent_text_field_append(out, n, ";run_id=", meta->run_id);
	agent_text_field_append(out, n, ";project=", meta->project);
	if (!out[0])
		safestrcpy(out, "status=changed", n);
}

uint64
agent_metadata_actions_label_bit(const char *label)
{
	uint64 hash = 1469598103934665603ULL;

	if (label == 0 || label[0] == 0)
		return 0;
	for (int i = 0; label[i] && i < AGENT_FILE_FIELD_SIZE; i++) {
		hash ^= (unsigned char)label[i];
		hash *= 1099511628211ULL;
	}
	return 1ULL << (hash % 60);
}

void
agent_metadata_actions_init(void)
{
	agent_action_next_sequence = 1;
	memset(agent_action_history, 0, sizeof(agent_action_history));
	agent_dependency_generation = 0;
	memset(agent_dependencies, 0, sizeof(agent_dependencies));
}

void
agent_metadata_actions_generation_advance(void)
{
	agent_dependency_generation++;
}

void
agent_metadata_actions_note_changes(uint changes)
{
	if (changes & (AGENT_FILE_CHANGE_STAGE |
		       AGENT_FILE_CHANGE_SCOPE_KEYS |
		       AGENT_FILE_CHANGE_DEPENDENCY |
		       AGENT_FILE_CHANGE_MEMBERSHIP))
		agent_metadata_actions_generation_advance();
}

static const struct agent_text_field *
agent_dependency_field(const char *key)
{
	for (uint i = 0; i < NELEM(agent_dependency_fields); i++) {
		const struct agent_text_field *field = &agent_dependency_fields[i];

		for (const char *name = field->text; name[0];
		     name += strlen(name) + 1)
			if (strncmp(key, name, AGENT_FILE_FIELD_SIZE) == 0)
				return field;
	}
	return 0;
}

static int
agent_scope_keys_match(uint actual_scope, uint scope_id,
		       const char *actual_namespace, const char *namespace,
		       const char *actual_run_id, const char *run_id)
{
	return actual_scope == scope_id &&
	       (!namespace || !namespace[0] ||
		strncmp(actual_namespace, namespace, AGENT_FILE_PROJECT_SIZE) == 0) &&
	       (!run_id || !run_id[0] ||
		strncmp(actual_run_id, run_id, AGENT_FILE_FIELD_SIZE) == 0);
}

int
agent_metadata_actions_dependency_update(uint scope_id, char *payload,
					 struct agent_result *res)
{
	struct agent_metadata_dependency_view dep;
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_SUMMARY_SIZE];
	const struct agent_text_field *field;
	int free_slot = -1;
	int slot = -1;
	int i = 0;
	int k;
	int v;

	memset(&dep, 0, sizeof(dep));
	if (!agent_scope_valid(scope_id))
		return AGENT_STATUS_DENIED;
	dep.used = 1;
	dep.scope_id = scope_id;
	dep.flags = AGENT_DEPENDENCY_F_USER;
	safestrcpy(dep.relation, "depends_on", sizeof(dep.relation));

	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		if (!payload[i])
			break;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' &&
		       payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':')
			return AGENT_STATUS_BAD_PARAM;
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		field = agent_dependency_field(key);
		if (!field)
			return AGENT_STATUS_BAD_PARAM;
		safestrcpy((char *)&dep + field->offset, val, field->size);
	}

	if (!dep.source[0] || !dep.target[0])
		return AGENT_STATUS_BAD_PARAM;
	if (!dep.summary[0])
		safestrcpy(dep.summary, dep.target, sizeof(dep.summary));

	for (int d = 0; d < AGENT_METADATA_DEPENDENCY_MAX; d++) {
		agent_metadata_txn_work_charge(1);
		if (!agent_dependencies[d].used) {
			if (free_slot < 0)
				free_slot = d;
			continue;
		}
		if (agent_dependencies[d].scope_id == scope_id &&
		    memcmp(agent_dependencies[d].namespace, dep.namespace,
			   sizeof(dep.namespace) + sizeof(dep.run_id) +
			   sizeof(dep.source) + sizeof(dep.target)) == 0) {
			slot = d;
			break;
		}
	}
	slot = slot < 0 ? free_slot : slot;
	if (slot < 0)
		return AGENT_STATUS_NO_SPACE;
	if (!agent_dependencies[slot].used) {
		int scope_count = 0;

		for (int d = 0; d < AGENT_METADATA_DEPENDENCY_MAX; d++) {
			if (agent_dependencies[d].used &&
			    agent_dependencies[d].scope_id == scope_id)
				scope_count++;
			agent_metadata_txn_work_charge(1);
		}
		if (scope_count >= AGENT_METADATA_DEPENDENCY_SCOPE_LIMIT)
			return AGENT_STATUS_NO_SPACE;
	}

	memmove(&agent_dependencies[slot], &dep, sizeof(dep));
	agent_metadata_actions_generation_advance();
	res->value0 = agent_dependency_generation;
	res->value1 = agent_metadata_actions_label_bit(dep.source);
	res->value2 = agent_metadata_actions_label_bit(dep.target);
	safestrcpy(res->result, "dependency_updated", sizeof(res->result));
	return AGENT_STATUS_OK;
}

const struct agent_metadata_dependency_view *
agent_metadata_actions_dependency_borrow(int slot)
{
	return slot >= 0 && slot < AGENT_METADATA_DEPENDENCY_MAX &&
	       agent_dependencies[slot].used ? &agent_dependencies[slot] : 0;
}

int
agent_metadata_actions_dependency_mask(uint scope_id, char *label,
				       char *namespace, char *run_id,
				       uint64 *mask)
{
	struct agent_catalog_view view;
	uint64 found = 0;

	for (int i = 0; i < AGENT_METADATA_DEPENDENCY_MAX; i++) {
		struct agent_metadata_dependency_view *dep =
			&agent_dependencies[i];

		if (dep->used &&
		    agent_scope_keys_match(dep->scope_id, scope_id,
				   dep->namespace, namespace,
				   dep->run_id, run_id) &&
		    strncmp(dep->source, label, sizeof(dep->source)) == 0 &&
		    dep->target[0])
			found |= agent_metadata_actions_label_bit(dep->source) |
				 agent_metadata_actions_label_bit(dep->target);
		agent_metadata_txn_work_charge(1);
	}
	if (!found) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			agent_metadata_txn_work_charge(1);
			if (agent_metadata_catalog_borrow(0, i, &view) <= 0)
				continue;
			if (view.meta->dependency_mask &&
			    agent_scope_keys_match(view.scope_id, scope_id,
					   view.meta->project, namespace,
					   view.meta->run_id, run_id) &&
			    (strncmp(view.meta->stage, label,
				     sizeof(view.meta->stage)) == 0 ||
			     strncmp(view.meta->physical_name, label,
				     sizeof(view.meta->physical_name)) == 0 ||
			     strncmp(view.meta->logical_path, label,
				     sizeof(view.meta->logical_path)) == 0))
				found |= agent_metadata_actions_label_bit(
						 view.meta->stage) |
					 view.meta->dependency_mask;
			view.meta = 0;
		}
	}
	if (!found)
		return -1;
	*mask = found;
	return 0;
}

static void
agent_stage_text(uint scope_id, uint64 mask, char *out, int n)
{
	struct agent_catalog_view view;
	uint64 bit;
	uint64 emitted = 0;

	memset(out, 0, n);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_metadata_catalog_borrow(0, i, &view) <= 0)
			continue;
		if (view.scope_id == scope_id && view.meta->stage[0]) {
			bit = agent_metadata_actions_label_bit(view.meta->stage);
			if ((mask & bit) != 0 && (emitted & bit) == 0) {
				if (emitted)
					agent_text_append(out, n, "+");
				emitted |= bit;
				agent_text_append(out, n, view.meta->stage);
			}
		}
		view.meta = 0;
	}
	if (!out[0])
		safestrcpy(out, "none", n);
}

int
agent_metadata_actions_dependency_query(uint scope_id, char *label,
					char *namespace, char *run_id,
					struct agent_result *res)
{
	uint64 mask;

	if (agent_metadata_actions_dependency_mask(
		    scope_id, label, namespace, run_id, &mask) < 0)
		return -1;
	res->value0 = mask;
	res->value1 = 0;
	for (uint64 bits = mask; bits; bits >>= 1)
		res->value1 += bits & 1;
	res->value2 = agent_dependency_generation;
	agent_stage_text(scope_id, mask, res->result, sizeof(res->result));
	return 0;
}

int
agent_metadata_actions_seen(uint scope_id, int tool_id, char *project,
			    char *run_id, char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;

	if (request_id == 0)
		return 0;
	for (int i = 0; i < AGENT_ACTION_HISTORY_MAX; i++) {
		e = &agent_action_history[i];
		if (e->request_id == request_id && e->scope_id == scope_id &&
		    e->tool_id == tool_id &&
		    strncmp(e->project, project, sizeof(e->project)) == 0 &&
		    strncmp(e->run_id, run_id, sizeof(e->run_id)) == 0 &&
		    strncmp(e->stage, stage, sizeof(e->stage)) == 0)
			return 1;
	}
	return 0;
}

void
agent_metadata_actions_remember(uint scope_id, int tool_id, char *project,
				char *run_id, char *stage,
				uint64 request_id)
{
	struct agent_action_history_entry *e, *free_entry = 0, *oldest = 0;
	int owned = 0;

	if (request_id == 0)
		return;
	for (int i = 0; i < AGENT_ACTION_HISTORY_MAX; i++) {
		e = &agent_action_history[i];
		if (e->request_id == 0) {
			if (!free_entry)
				free_entry = e;
			continue;
		}
		if (e->scope_id == scope_id) {
			if (!oldest || e->sequence < oldest->sequence)
				oldest = e;
			owned++;
		}
	}
	e = owned < AGENT_ACTION_SCOPE_LIMIT ? free_entry : oldest;
	if (!e)
		return;
	memset(e, 0, sizeof(*e));
	e->tool_id = tool_id;
	e->scope_id = scope_id;
	e->sequence = agent_action_next_sequence++;
	e->request_id = request_id;
	safestrcpy(e->project, project, sizeof(e->project));
	safestrcpy(e->run_id, run_id, sizeof(e->run_id));
	safestrcpy(e->stage, stage, sizeof(e->stage));
}

void
agent_metadata_actions_clear_history(uint scope_id)
{
	for (int i = 0; i < AGENT_ACTION_HISTORY_MAX; i++)
		if (agent_action_history[i].scope_id == scope_id)
			memset(&agent_action_history[i], 0,
			       sizeof(agent_action_history[i]));
}

void
agent_metadata_actions_reclaim_scope(uint scope_id)
{
	int changed = 0;

	for (int i = 0; i < AGENT_METADATA_DEPENDENCY_MAX; i++)
		if (agent_dependencies[i].used &&
		    agent_dependencies[i].scope_id == scope_id) {
			memset(&agent_dependencies[i], 0,
			       sizeof(agent_dependencies[i]));
			changed = 1;
		}
	if (changed)
		agent_metadata_actions_generation_advance();
	agent_metadata_actions_clear_history(scope_id);
}

static int
agent_status_select(uint scope_id, char *project, char *run_id,
		    char *stage, uint64 dependency_mask, uchar *selected)
{
	struct agent_catalog_view view;
	int count = 0;

	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_metadata_catalog_borrow(0, i, &view) <= 0)
			continue;
		if (agent_scope_keys_match(view.scope_id, scope_id,
					   view.meta->project, project,
					   view.meta->run_id, run_id) &&
		    (stage ? strncmp(view.meta->stage, stage,
				     sizeof(view.meta->stage)) == 0 :
		     (dependency_mask & agent_metadata_actions_label_bit(
					    view.meta->stage)) != 0)) {
			selected[i / 8] |= 1U << (i % 8);
			count++;
		}
		view.meta = 0;
	}
	return count;
}

static int
agent_file_status_batch_rollback(uint scope_id, int undo_count)
{
	for (int i = undo_count - 1; i >= 0; i--) {
		struct agent_status_batch_undo *undo =
			&agent_status_batch_undo[i];
		struct agent_catalog_edit edit;

		memset(&edit, 0, sizeof(edit));
		if (agent_metadata_catalog_edit_begin(
			    undo->slot, scope_id, &edit) < 0)
			return -1;
		if (!edit.meta->used || edit.scope_id != scope_id) {
			agent_metadata_catalog_edit_abort(&edit);
			return -1;
		}
		memmove(edit.meta->status, undo->text, sizeof(undo->text));
		edit.meta->updated_tick = undo->updated_tick;
		edit.meta->fs_generation = undo->fs_generation;
		if (agent_metadata_catalog_edit_commit(
			    &edit, AGENT_FILE_CHANGE_STATUS) < 0)
			return -1;
	}
	if (undo_count != 0)
		agent_metadata_actions_note_changes(AGENT_FILE_CHANGE_STATUS);
	return 0;
}

int
agent_metadata_actions_update_status_locked(
	uint scope_id, char *stage, char *project, char *run_id,
	char *status, char *summary, uint64 dependency_mask,
	int propagate_dependencies,
	struct agent_metadata_persist_result *persist)
{
	uchar selected[(AGENT_FILE_META_MAX + 7) / 8];
	uchar primary[(AGENT_FILE_META_MAX + 7) / 8];
	struct agent_catalog_edit edit;
	struct agent_file_meta *meta;
	struct agent_status_batch_undo *undo;
	int persistent_updated = 0;
	int primary_updated = 0;
	int updated = 0;
	int undo_count = 0;

	memset(selected, 0, sizeof(selected));
	memset(primary, 0, sizeof(primary));
	primary_updated = agent_status_select(
		scope_id, project, run_id, stage, 0, primary);
	if (primary_updated == 0)
		return 0;
	memmove(selected, primary, sizeof(selected));
	if (propagate_dependencies && dependency_mask != 0)
		agent_status_select(scope_id, project, run_id, 0,
				    dependency_mask, selected);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if ((selected[i / 8] & (1U << (i % 8))) == 0)
			continue;
		if (agent_metadata_catalog_edit_begin(i, scope_id, &edit) < 0)
			continue;
		if (!edit.meta->used || edit.scope_id != scope_id) {
			agent_metadata_catalog_edit_abort(&edit);
			continue;
		}
		/* Catalog admission bounds this scope to the undo partition. */
		meta = edit.meta;
		undo = &agent_status_batch_undo[undo_count];
		undo->slot = i;
		memmove(undo->text, meta->status, sizeof(undo->text));
		undo->updated_tick = meta->updated_tick;
		undo->fs_generation = meta->fs_generation;
		int was_persistent = (meta->flags & AGENT_FILE_META_F_PERSIST) != 0;
		const char *new_summary =
			primary[i / 8] & (1U << (i % 8)) ? summary :
			"dependency refreshed";

		safestrcpy(meta->status, status, sizeof(meta->status));
		if (new_summary && new_summary[0])
			safestrcpy(meta->summary, new_summary, sizeof(meta->summary));
		meta->updated_tick = agent_file_state_now();
		meta->fs_generation =
			agent_file_state_generation_next(scope_id);
		if (agent_metadata_catalog_edit_commit(
			    &edit, AGENT_FILE_CHANGE_STATUS) < 0)
			continue;
		undo_count++;
		if (was_persistent)
			persistent_updated = 1;
		updated++;
	}
	if (updated)
		agent_metadata_actions_note_changes(AGENT_FILE_CHANGE_STATUS);
	if (persistent_updated)
		agent_metadata_store_mark_dirty(scope_id);
	if (persistent_updated &&
	    agent_metadata_store_persist_commit(persist) < 0 &&
	    !persist->irrevocable &&
	    agent_file_status_batch_rollback(scope_id, undo_count) < 0) {
		agent_metadata_store_fail_closed_runtime();
		persist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED;
		persist->irrevocable = 1;
	}
	return updated;
}
