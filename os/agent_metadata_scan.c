#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_scan.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "vfs_security.h"

#define SCAN_INTERVAL 20
#define SCAN_STEP 16
#define SCAN_REST_MULTIPLIER 4
#define SCAN_BIND_RETRY 1
#define SCAN_BIND_DEFERRED 2
#define SCAN_URGENT 2
#define SCOPE_MAX (VFS_SCOPE_MAX_ACTIVE + 1)

static struct {
	uint64 offset, next_tick, last_step_tick, quanta;
	uint64 runs, entries, added, updated, removed, failures, deferred;
	uint marked[SCOPE_MAX * 2], nmarked;
	uchar seen[AGENT_META_STALE_BYTES];
	int retry, sweep_uncertain;
} scan;
#define SCAN_SEEN(slot) (scan.seen[(slot) / 8] & (1U << ((slot) % 8)))
#define SCAN_NOTE(slot) (scan.seen[(slot) / 8] |= 1U << ((slot) % 8))
static struct { signed char pending; uchar on, active; } scan_ctl;

struct scan_field { ushort offset, size; uint changes; const char *value; };
#define SCAN_DEFAULT(field, change_bits, default_value) \
	{ (ushort)__builtin_offsetof(struct agent_file_meta, field), \
	  (ushort)sizeof(((struct agent_file_meta *)0)->field), change_bits, \
	  default_value }
static const struct scan_field scan_default_fields[] = {
	SCAN_DEFAULT(physical_name, AGENT_FILE_CHANGE_SCOPE_KEYS, 0),
	SCAN_DEFAULT(logical_path, AGENT_FILE_CHANGE_SCOPE_KEYS, 0),
	SCAN_DEFAULT(project, AGENT_FILE_CHANGE_SCOPE_KEYS, "root"),
	SCAN_DEFAULT(workflow, AGENT_FILE_CHANGE_SCOPE_KEYS, "background-scan"),
	SCAN_DEFAULT(run_id, AGENT_FILE_CHANGE_SCOPE_KEYS, "ROOT"),
	SCAN_DEFAULT(stage, AGENT_FILE_CHANGE_STAGE, "scan"),
	SCAN_DEFAULT(summary, 0, "auto scanned root file"),
};
#undef SCAN_DEFAULT

static const char *const rules[][3] = {
	{ "md", "txt", "document" },
	{ "log", "err", "log" },
	{ "status", "ok", "status" },
	{ "data", "csv", "dataset" },
	{ "fail", "err", "failed" },
	{ "ok", "pass", "ok" },
};

void agent_metadata_scan_init(void) {
	memset(&scan, 0, sizeof(scan)); memset(&scan_ctl, 0, sizeof(scan_ctl));
	scan.last_step_tick = ~0ULL;
}

static void scan_infer(char *name, char *out, int n, uint first, uint count,
		       const char *def) {
	for (uint i = first; i < first + count; i++)
		if (agent_metadata_catalog_field_contains(name, rules[i][0]) ||
		    agent_metadata_catalog_field_contains(name, rules[i][1])) {
			safestrcpy(out, rules[i][2], n);
			return;
		}
	safestrcpy(out, def, n);
}

uint agent_metadata_scan_apply_defaults(struct agent_file_meta *meta,
					char *path, int *out) {
	int dirty = 0, is_auto = meta->flags & AGENT_FILE_META_F_AUTOSCAN;
	uint changes = 0;
	for (uint i = 0; i < NELEM(scan_default_fields); i++) {
		const struct scan_field *rule = &scan_default_fields[i];
		char *field = (char *)meta + rule->offset;
		const char *value = rule->value ? rule->value : path;

		if ((!is_auto && rule->value) || (field[0] && (rule->value || !is_auto)))
			continue;
		if (strncmp(field, value, rule->size)) {
			safestrcpy(field, value, rule->size);
			dirty = 1;
			changes |= rule->changes;
		}
	}
	if (is_auto && !meta->kind[0]) {
		scan_infer(path, meta->kind, sizeof(meta->kind), 0, 4, "file");
		dirty = 1;
		changes |= AGENT_FILE_CHANGE_KIND;
	}
	if (is_auto && !meta->status[0]) {
		scan_infer(path, meta->status, sizeof(meta->status), 4, 2,
			   "present");
		dirty = 1;
		changes |= AGENT_FILE_CHANGE_STATUS;
	}
	if (out)
		*out = dirty;
	return changes;
}

void agent_metadata_scan_catalog_sync(const struct agent_catalog_delta *delta) {
	if (!scan_ctl.active)
		return;
	if (delta->full_reset) {
		scan.offset = 0;
		memset(scan.seen, 0, sizeof(scan.seen));
	}
	agent_metadata_txn_work_charge(AGENT_META_STALE_BYTES);
	for (int i = 0; i < AGENT_META_STALE_BYTES; i++)
		scan.seen[i] |= delta->applied_slots[i];
}

void agent_metadata_scan_note_slot(int slot) {
	if (scan_ctl.active && slot >= 0 && slot < AGENT_FILE_META_MAX)
		SCAN_NOTE(slot);
}

int agent_metadata_scan_query_stable(void) { return !scan_ctl.active; }

static int scan_mark(uint scope, int add, int saturated) {
	uint mark = scope | (saturated ? FS_OWNER_SCOPE_FLAG : 0);
	for (uint i = 0; i < scan.nmarked; i++)
		if (scan.marked[i] == mark)
			return 1;
	if (!add || scan.nmarked >= NELEM(scan.marked))
		return add && !saturated && (scan.sweep_uncertain = 1);
	scan.marked[scan.nmarked++] = mark;
	return 1;
}

#define scan_scope_failed(scope, add) scan_mark(scope, add, 0)
#define scan_scope_full(scope, add) scan_mark(scope, add, 1)

static int scan_remove(int slot, uint scope, int persist) {
	if (agent_metadata_catalog_clear_slot(slot) < 0)
		return -1;
	scan.removed++;
	agent_metadata_scan_slot_freed(scope);
	if (persist)
		agent_metadata_store_mark_dirty(scope);
	return 0;
}

static int scan_matches(const struct agent_file_meta *meta, struct inode *ip) {
	return meta->dev == ip->dev && meta->inum == ip->inum &&
	       meta->incarnation == ip->vfs_incarnation;
}

static uint64 scan_rest_deadline(uint64 quanta, uint64 now) {
	uint64 rest = quanta;
	if (rest > ~0ULL / SCAN_REST_MULTIPLIER)
		return ~0ULL;
	rest *= SCAN_REST_MULTIPLIER;
	if (rest < SCAN_INTERVAL)
		rest = SCAN_INTERVAL;
	return rest > ~0ULL - now ? ~0ULL : now + rest;
}

static void scan_pause(int retry, int resume) {
	uint64 current = agent_file_state_now(), now = current;
	int irq = intr_save();
	now = scan_rest_deadline(scan_ctl.active ? scan.quanta : 0, now);
	scan_ctl.active = 0;
	if (!retry || !resume)
		scan.quanta = 0;
	if (scan_ctl.pending == SCAN_URGENT) {
		scan.quanta = 0;
		scan_ctl.pending = 1;
		scan.next_tick = current;
	} else if (!retry && !resume && scan_ctl.pending == 0) {
		/* 完整协调只执行一次；仅显式请求、启动或恢复缺口、重试会再次扫描。 */
		scan_ctl.on = 0;
		scan.next_tick = 0;
	} else {
		if (retry) {
			if (!resume || scan_ctl.pending > 0)
				scan_ctl.pending = 1;
			else if (scan_ctl.pending == 0)
				scan_ctl.pending = -1;
		}
		if (scan.next_tick < now)
			scan.next_tick = now;
	}
	intr_restore(irq);
}

void agent_file_request_scan(void) {
	uint64 now = agent_file_state_now();
	int irq = intr_save();
	/* 休眠期的新显式请求升级为完整扫描，但不提前原期限。 */
	if (!scan_ctl.on || !scan_ctl.pending || scan_ctl.pending < 0) {
		if (!scan_ctl.on) {
			scan_ctl.on = 1;
			scan.next_tick = now;
		} else if (scan_ctl.active) {
			scan.next_tick = scan_rest_deadline(0, now);
		} else if (now >= scan.next_tick) {
			scan.next_tick = now;
		}
		scan_ctl.pending = 1;
	}
	intr_restore(irq);
	if (agent_metadata_scan_plan(now) != AGENT_METADATA_SCAN_IDLE)
		agent_background_request();
}

void agent_metadata_scan_slot_freed(uint scope) {
	uint64 now;
	int irq = intr_save();
	if (!scan_scope_full(scope, 0)) {
		intr_restore(irq);
		return;
	}
	scan_ctl.on = 1;
	scan_ctl.pending = SCAN_URGENT;
	scan.next_tick = agent_file_state_now();
	now = scan.next_tick;
	intr_restore(irq);
	if (agent_metadata_scan_plan(now) != AGENT_METADATA_SCAN_IDLE)
		agent_background_request();
}

int agent_metadata_scan_plan(uint64 now) {
	int plan = AGENT_METADATA_SCAN_IDLE;
	int irq = intr_save();
	if (scan_ctl.on) {
		if (!scan_ctl.active && !scan_ctl.pending &&
		    now >= scan.next_tick)
			scan_ctl.pending = 1;
		if (scan.last_step_tick != now) {
			if (scan_ctl.active)
				plan = AGENT_METADATA_SCAN_CONTINUE;
			else if (scan_ctl.pending && now >= scan.next_tick)
				plan = AGENT_METADATA_SCAN_START;
		}
	}
	intr_restore(irq);
	return plan;
}

uint agent_metadata_scan_index_inode(struct inode *ip, char *path, int *failed) {
	struct agent_catalog_view view;
	struct agent_catalog_edit edit;
	struct agent_catalog_resolution resolution;
	struct agent_file_meta selector, *meta;
	uint64 fid = 0;
	int slot, dirty = 0, sidecar, stale_sidecar = 0, pend = 0;
	int persist, mut;
	uint scope, changes = 0;
	*failed = 0;
	scope = ip->vfs_scope_id;
	if (!vfs_inode_label_valid(ip) || ip->vfs_policy != VFS_POLICY_WORKFLOW ||
	    ip->type != T_FILE || !agent_object_scope_valid(scope))
		return 0;
	mut = exec_policy_inode_mutable(ip);
	if (scope == VFS_SCOPE_SYSTEM ? mut || !exec_policy_inode_trusted(ip) :
	    !vfs_scope_active(scope) || !mut)
		return 0;
	slot = ip->agent_meta_slot - 1;
	if (agent_file_state_index_deferred(ip) &&
	    scan_scope_full(scope, 0)) {
		scan.deferred++;
		return 0;
	}
	if (agent_metadata_catalog_borrow(0, slot, &view) <= 0 ||
	    view.scope_id != scope || !scan_matches(view.meta, ip)) {
		stale_sidecar = ip->agent_meta_slot > 0;
		if (ip->dev == 0 || ip->inum == 0 || ip->vfs_incarnation == 0)
			goto retry;
		memset(&selector, 0, sizeof(selector));
		safestrcpy(selector.physical_name, path,
			   sizeof(selector.physical_name));
		safestrcpy(selector.logical_path, path,
			   sizeof(selector.logical_path));
		selector.dev = ip->dev;
		selector.inum = ip->inum;
		selector.incarnation = ip->vfs_incarnation;
		agent_metadata_catalog_resolve(scope, &selector, -1, &resolution);
		if (resolution.slot == AGENT_CATALOG_CONFLICT ||
		    ((resolution.matched & AGENT_CATALOG_KEY_IDENTITY) != 0 &&
		     (resolution.matched & AGENT_CATALOG_KEY_PATH) == 0))
			goto retry;
		slot = (resolution.matched & AGENT_CATALOG_KEY_PATH) != 0 ?
			       resolution.slot : -1;
		if (slot >= 0 &&
		    (agent_metadata_catalog_borrow_scan(slot, &view) <= 0 ||
		     view.scope_id != scope ||
		     agent_metadata_catalog_identity_state(view.meta) < 0 ||
		     (strncmp(view.meta->physical_name, path,
			      sizeof(view.meta->physical_name)) != 0 &&
		      strncmp(view.meta->logical_path, path,
			      sizeof(view.meta->logical_path)) != 0)))
			goto retry;
	}
	if (slot >= 0 &&
	    (view.state & AGENT_CATALOG_STATE_QUARANTINE)) {
		SCAN_NOTE(slot);
		return 0;
	}
	if (slot >= 0 && view.meta->dev &&
	    !scan_matches(view.meta, ip)) {
		int old_persist = view.meta->flags & AGENT_FILE_META_F_PERSIST;
		uint old_scope = view.scope_id;
		SCAN_NOTE(slot);
		if (scan_remove(slot, old_scope, old_persist) < 0)
			goto retry;
		changes |= AGENT_FILE_CHANGE_ALL;
		slot = -1;
	}
	if (slot < 0) {
		slot = agent_metadata_catalog_alloc_slot(
			scope, AGENT_FILE_META_F_AUTOSCAN);
		if (slot == AGENT_CATALOG_NO_SPACE) {
			(void)scan_scope_full(scope, 1);
			scan.deferred++;
			if (agent_file_state_set_index(ip,
			    AGENT_INODE_META_DEFERRED_SLOT, 0, stale_sidecar) < 0)
				goto retry;
			return changes;
		}
		if (slot < 0 || !(fid = agent_metadata_catalog_alloc_fid(scope))) {
			(void)scan_scope_full(scope, 1);
			*failed = SCAN_BIND_DEFERRED;
			return changes;
		}
	} else
		pend = view.state & AGENT_CATALOG_STATE_PENDING;
	SCAN_NOTE(slot);
	if (agent_metadata_catalog_edit_begin_scan(slot, scope, &edit) < 0)
		goto retry;
	meta = edit.meta;
	if (!meta->used) {
		memset(meta, 0, sizeof(*meta));
		meta->used = 1;
		meta->fid = fid;
		meta->flags = AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN;
		edit.scope_id = scope;
		changes |= AGENT_FILE_CHANGE_MEMBERSHIP;
	}
	if (edit.scope_id != scope) {
		agent_metadata_catalog_edit_abort(&edit);
		goto retry;
	}
	changes |= agent_metadata_scan_apply_defaults(meta, path, &dirty);
	if (!scan_matches(meta, ip) || meta->size != ip->size) {
		meta->dev = ip->dev;
		meta->inum = ip->inum;
		meta->incarnation = ip->vfs_incarnation;
		meta->size = ip->size;
		dirty = 1;
	}
	persist = meta->flags & AGENT_FILE_META_F_PERSIST;
	sidecar = ip->agent_meta_slot != slot + 1 ||
		  ip->agent_meta_flags != persist ||
		  ip->agent_meta_version != AGENT_INODE_META_VERSION;
	dirty |= sidecar;
	if (dirty || fid) {
		meta->updated_tick = agent_file_state_now();
		meta->fs_generation = agent_file_state_generation_next(edit.scope_id);
		if (agent_metadata_catalog_edit_commit(&edit, changes) < 0)
			goto retry;
		if (persist)
			agent_metadata_store_mark_dirty(ip->vfs_scope_id);
		if (sidecar &&
		    agent_file_state_set_index(ip, slot + 1, persist, 0) < 0)
			goto retry;
		if (fid) scan.added++;
		else scan.updated++;
	} else {
		agent_metadata_catalog_edit_abort(&edit);
	}
	if (pend) {
		if (agent_metadata_catalog_reconcile_slot(slot) < 0)
			goto retry;
		changes |= AGENT_FILE_CHANGE_MEMBERSHIP;
	}
	/* 易失目录已吸收当前索引节点大小，版本覆盖可立即转为冷缓存。 */
	if (!persist)
		agent_file_state_content_absorb_volatile(ip, slot);
	return changes;
retry:
	*failed = SCAN_BIND_RETRY;
	return changes;
}

uint agent_metadata_scan_step(uint64 now, int plan, int load_ok) {
	struct inode *root, *ip;
	struct dirent de;
	struct vfs_cred cred;
	char name[DIRSIZ + 1];
	uint64 off;
	uint mask = 0;
	int root_status, steps = 0, irq;
	if (plan == AGENT_METADATA_SCAN_START) {
		if (!load_ok) {
			scan_pause(1, 0);
			return 0;
		}
		irq = intr_save();
		scan_ctl.active = 1;
		if (scan_ctl.pending > 0) {
			scan.quanta = 0;
			scan.offset = 0;
			scan.nmarked = 0;
			scan.retry = scan.sweep_uncertain = 0;
			memset(scan.seen, 0, sizeof(scan.seen));
			scan.runs++;
		}
		scan_ctl.pending = 0;
		intr_restore(irq);
	} else if (plan != AGENT_METADATA_SCAN_CONTINUE) {
		return 0;
	}
	scan.last_step_tick = now;
	scan.quanta++;
	vfs_cred_kernel(&cred);
	root = root_dir_status(&root_status);
	if (root == 0 || root_status != FS_LOOKUP_FOUND) {
		if (root)
			iput(root);
		scan.failures++;
		scan_pause(1, 1);
		return 0;
	}
	for (off = scan.offset; off < root->size && steps < SCAN_STEP;
	     off += sizeof(de), steps++) {
		if (readi(root, &cred, 0, (uint64)&de, off, sizeof(de)) != sizeof(de)) {
			scan.failures++;
			scan.offset = off;
			scan_pause(1, 1);
			break;
		}
		scan.entries++;
		if (!de.inum)
			continue;
		memmove(name, de.name, DIRSIZ);
		name[DIRSIZ] = 0;
		if (!name[0] || agent_file_is_meta_store_name(name))
			continue;
		ip = inode_get(root->dev, de.inum);
		if (!ip || ivalid(ip) < 0) {
			if (ip)
				iput(ip);
			scan.failures++;
			scan.retry = scan.sweep_uncertain = 1;
			continue;
		}
		int bind_failed = 0;
		mask |= agent_metadata_scan_index_inode(ip, name, &bind_failed);
		if (bind_failed) {
			scan.failures++;
			scan.retry = 1;
			if (bind_failed == SCAN_BIND_DEFERRED)
				scan.deferred++;
			else
				(void)scan_scope_failed(ip->vfs_scope_id, 1);
		}
		iput(ip);
	}
	if (!scan_ctl.active) {
		iput(root);
		return mask;
	}
	scan.offset = off;
	if (scan.offset >= root->size) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			struct agent_catalog_view view;
			int persist;
			uint scope;

			if (agent_metadata_catalog_borrow_scan(i, &view) <= 0)
				continue;
			if (view.state & AGENT_CATALOG_STATE_QUARANTINE)
				continue;
			if (SCAN_SEEN(i) || scan.sweep_uncertain ||
			    scan_scope_failed(view.scope_id, 0))
				continue;
			scope = view.scope_id;
			persist =
				view.meta->flags & AGENT_FILE_META_F_PERSIST;
			if (scan_remove(i, scope, persist) < 0) {
				scan.failures++;
				scan.retry = 1;
				(void)scan_scope_failed(scope, 1);
				continue;
			}
			mask |= AGENT_FILE_CHANGE_ALL;
		}
		scan_pause(scan.retry, 0);
	}
	iput(root);
	return mask;
}

void agent_metadata_scan_fill_info(struct agent_info *info) {
#define SCAN_INFO(field) info->file_scan_##field = scan.field
	SCAN_INFO(runs); SCAN_INFO(entries); SCAN_INFO(added);
	SCAN_INFO(updated); SCAN_INFO(removed);
#undef SCAN_INFO
	info->file_scan_deferred = scan.deferred;
	info->file_scan_failures = scan.failures;
	info->file_scan_pending =
		scan_ctl.pending || scan_ctl.active;
}
