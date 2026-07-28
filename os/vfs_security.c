#include "vfs_security.h"
#include "agent.h"
#include "bio.h"
#include "const.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "proc.h"
#include "workflow_lifecycle.h"
#include "../user/include/exec_policy_manifest.h"
#include "../exec_image_policy.h"

_Static_assert(EXEC_MANIFEST_VFS_CONTENT_READ == VFS_CAP_CONTENT_READ,
	       "content-read capability mismatch");
_Static_assert(EXEC_MANIFEST_VFS_ARTIFACT_WRITE == VFS_CAP_ARTIFACT_WRITE,
	       "artifact-write capability mismatch");

enum vfs_scope_reclaim_phase {
	VFS_SCOPE_RECLAIM_BEGIN = 0,
	VFS_SCOPE_RECLAIM_FILES,
	VFS_SCOPE_RECLAIM_METADATA,
	VFS_SCOPE_RECLAIM_RETIRE,
	VFS_SCOPE_RECLAIM_DONE,
};

_Static_assert(VFS_SCOPE_RECLAIM_BEGIN < VFS_SCOPE_RECLAIM_FILES &&
	       VFS_SCOPE_RECLAIM_FILES < VFS_SCOPE_RECLAIM_METADATA &&
	       VFS_SCOPE_RECLAIM_METADATA < VFS_SCOPE_RECLAIM_RETIRE &&
	       VFS_SCOPE_RECLAIM_RETIRE < VFS_SCOPE_RECLAIM_DONE,
	       "workflow reclaim phases must remain forward-only");

struct vfs_scope_ref {
	int used;
	uint scope_id;
	int retiring;
	int preserve_on_retire;
	uint reclaim_phase;
	uint64 reclaim_metadata_target;
	struct workflow_lifecycle_key lifecycle;
	struct resource_account_handle storage_account;
};

static struct vfs_scope_ref vfs_scope_refs[NPROC];
static uint vfs_scope_reap_cursor;

_Static_assert(VFS_SCOPE_MAX_ACTIVE == WORKFLOW_LIFECYCLE_MAX_ACTIVE,
	       "workflow active limit mismatch");
_Static_assert(VFS_SCOPE_LIFECYCLE_CAP == WORKFLOW_LIFECYCLE_CAP,
	       "workflow lifecycle limit mismatch");

static int vfs_scope_create(uint scope_id,
			    struct workflow_lifecycle_key *lifecycle)
{
	int free_slot = -1;
	int allocated = 0;
	int retiring = 0;
	int enabled;
	struct workflow_lifecycle_key created = workflow_lifecycle_none();
	struct resource_account_handle storage = resource_account_none();

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC || lifecycle == 0)
		return -1;
	*lifecycle = workflow_lifecycle_none();
	/* Lease renewal may sleep; it must precede the scope allocation lock. */
	if (workflow_lifecycle_prepare_create() < 0)
		return -1;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (ref->used && ref->scope_id == scope_id) {
			intr_restore(enabled);
			return -1;
		}
		if (ref->used) {
			if (ref->retiring)
				retiring++;
			else if (workflow_lifecycle_active(ref->lifecycle) ||
				 workflow_lifecycle_closing(ref->lifecycle))
				allocated++;
		} else if (free_slot < 0) {
			free_slot = i;
		}
	}
	if (free_slot >= 0 && allocated < VFS_SCOPE_MAX_ACTIVE &&
	    allocated + retiring < VFS_SCOPE_LIFECYCLE_CAP &&
	    fs_storage_scope_admissible() &&
	    fs_storage_scope_account_create(scope_id, &storage) == 0 &&
	    workflow_lifecycle_create(scope_id, &created) == 0 &&
	    bio_scope_acquire(scope_id, storage) == 0) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[free_slot];

		ref->used = 1;
		ref->scope_id = scope_id;
		ref->retiring = 0;
		ref->preserve_on_retire = 0;
		ref->reclaim_phase = VFS_SCOPE_RECLAIM_BEGIN;
		ref->reclaim_metadata_target = 0;
		ref->lifecycle = created;
		ref->storage_account = storage;
		*lifecycle = created;
	} else {
		if (workflow_lifecycle_key_valid(created)) {
			(void)workflow_lifecycle_leave(created);
			(void)workflow_lifecycle_reclaim(created);
			bio_scope_quiesce(scope_id);
			bio_scope_retire(scope_id);
		}
		if (resource_account_handle_valid(storage))
			fs_storage_scope_account_close(storage);
		free_slot = -1;
	}
	intr_restore(enabled);
	return free_slot >= 0 ? 0 : -1;
}

static int vfs_scope_join(uint scope_id,
			  struct workflow_lifecycle_key lifecycle)
{
	int result = -1;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return -1;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id ||
		    ref->retiring ||
		    !workflow_lifecycle_key_equal(ref->lifecycle, lifecycle))
			continue;
		result = workflow_lifecycle_join(lifecycle);
		break;
	}
	intr_restore(enabled);
	return result;
}

static int vfs_scope_release(uint scope_id,
			     struct workflow_lifecycle_key lifecycle)
{
	struct vfs_scope_ref *matched = 0;
	struct resource_account_handle storage = resource_account_none();
	int last = -1;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return -1;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id ||
		    !workflow_lifecycle_key_equal(ref->lifecycle, lifecycle))
			continue;
		matched = ref;
		break;
	}
	if (matched != 0) {
		last = workflow_lifecycle_leave(lifecycle);
		if (last > 0) {
			matched->retiring = 1;
			matched->reclaim_phase = VFS_SCOPE_RECLAIM_BEGIN;
			matched->reclaim_metadata_target = 0;
			storage = matched->storage_account;
		}
	}
	intr_restore(enabled);
	if (last > 0) {
		if (!resource_account_handle_valid(storage))
			panic("workflow storage account missing");
		fs_storage_scope_account_close(storage);
		bio_scope_quiesce(scope_id);
		/*
		 * The last reference is the edge that makes this lifecycle
		 * reclaimable.  Publish it only after the storage and I/O owners are
		 * quiesced; every exit, revocation, and rollback path reaches this
		 * common release boundary.
		 */
		agent_background_request();
	}
	return last;
}

static int
vfs_scope_reclaim_advance(uint scope_id,
			  struct workflow_lifecycle_key lifecycle,
			  uint expected, uint next, uint64 metadata_target)
{
	int advanced = 0;
	int enabled;

	/* Slow phase work runs unlocked; the immutable key guards publication. */
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id || !ref->retiring ||
		    !workflow_lifecycle_key_equal(ref->lifecycle, lifecycle))
			continue;
		if (ref->reclaim_phase == expected &&
		    workflow_lifecycle_retiring(lifecycle)) {
			ref->reclaim_metadata_target = metadata_target;
			ref->reclaim_phase = next;
			advanced = 1;
		}
		break;
	}
	intr_restore(enabled);
	return advanced;
}

static void vfs_scope_reclaim_complete(uint scope_id)
{
	int preserve_files = 0;
	uint phase = VFS_SCOPE_RECLAIM_BEGIN;
	uint64 metadata_target = 0;
	int eligible = 0;
	int enabled;
	struct workflow_lifecycle_key lifecycle =
		workflow_lifecycle_none();

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id)
			continue;
		lifecycle = ref->lifecycle;
		eligible = ref->retiring &&
			   workflow_lifecycle_retiring(lifecycle);
		preserve_files = ref->preserve_on_retire;
		phase = ref->reclaim_phase;
		metadata_target = ref->reclaim_metadata_target;
		break;
	}
	intr_restore(enabled);
	if (!eligible)
		return;

	if (phase == VFS_SCOPE_RECLAIM_BEGIN) {
		uint64 target = 0;
		uint next = preserve_files ? VFS_SCOPE_RECLAIM_METADATA :
					     VFS_SCOPE_RECLAIM_FILES;

		if (agent_scope_reclaim_begin(scope_id, &target) < 0)
			return;
		(void)vfs_scope_reclaim_advance(scope_id, lifecycle, phase,
					 next, target);
		return;
	}
	if (phase == VFS_SCOPE_RECLAIM_FILES) {
		int status = fs_reclaim_scope_files(scope_id);

		if (status == FS_RECLAIM_PENDING)
			return;
		if (status < 0) {
			/* A failed labelled cleanup needs a full consistency pass. */
			agent_file_request_scan();
			return;
		}
		(void)vfs_scope_reclaim_advance(
			scope_id, lifecycle, phase, VFS_SCOPE_RECLAIM_METADATA,
			metadata_target);
		return;
	}
	if (phase == VFS_SCOPE_RECLAIM_METADATA) {
		if (!agent_scope_reclaim_metadata_done(scope_id,
						       metadata_target))
			return;
		(void)vfs_scope_reclaim_advance(
			scope_id, lifecycle, phase, VFS_SCOPE_RECLAIM_RETIRE,
			metadata_target);
		return;
	}
	if (phase == VFS_SCOPE_RECLAIM_RETIRE) {
		bio_scope_retire(scope_id);
		(void)vfs_scope_reclaim_advance(scope_id, lifecycle, phase,
					 VFS_SCOPE_RECLAIM_DONE,
					 metadata_target);
		return;
	}
	if (phase != VFS_SCOPE_RECLAIM_DONE)
		panic("invalid workflow reclaim phase");

	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id ||
		    !workflow_lifecycle_key_equal(ref->lifecycle, lifecycle))
			continue;
		if (ref->retiring &&
		    ref->reclaim_phase == VFS_SCOPE_RECLAIM_DONE &&
		    workflow_lifecycle_retiring(lifecycle)) {
			if (preserve_files &&
			    workflow_lifecycle_reclaim(lifecycle) == 0) {
				// A completed boot lease remains an admitted, inactive
				// storage owner until reboot. This keeps its durable
				// output in the same guarantee calculation.
				ref->retiring = 0;
				ref->lifecycle = workflow_lifecycle_none();
			} else if (!preserve_files &&
				   resource_account_state_get(
					   ref->storage_account) ==
					   RESOURCE_ACCOUNT_FREE &&
				   workflow_lifecycle_reclaim(lifecycle) == 0) {
				memset(ref, 0, sizeof(*ref));
			}
		}
		break;
	}
	intr_restore(enabled);
}

static int vfs_scope_preserve_on_retire(uint scope_id)
{
	int result = -1;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id)
			continue;
		if (!ref->retiring &&
		    workflow_lifecycle_active(ref->lifecycle)) {
			ref->preserve_on_retire = 1;
			result = 0;
		}
		break;
	}
	intr_restore(enabled);
	return result;
}

static int vfs_scope_reap_work_pending(void)
{
	int pending = 0;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (ref->used && ref->retiring &&
		    workflow_lifecycle_retiring(ref->lifecycle)) {
			pending = 1;
			break;
		}
	}
	intr_restore(enabled);
	return pending;
}

void vfs_scope_reap_pending(void)
{
	uint scope_id = VFS_SCOPE_NONE;
	uint reclaim_phase = VFS_SCOPE_RECLAIM_BEGIN;
	int enabled = intr_save();

	for (uint scanned = 0; scanned < NPROC; scanned++) {
		uint i = (vfs_scope_reap_cursor + scanned) % NPROC;

		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].retiring &&
		    workflow_lifecycle_retiring(
			    vfs_scope_refs[i].lifecycle)) {
			scope_id = vfs_scope_refs[i].scope_id;
			reclaim_phase = vfs_scope_refs[i].reclaim_phase;
			vfs_scope_reap_cursor = (i + 1) % NPROC;
			break;
		}
	}
	intr_restore(enabled);
	if (scope_id == VFS_SCOPE_NONE)
		return;
	if (reclaim_phase >= VFS_SCOPE_RECLAIM_METADATA) {
		vfs_scope_reclaim_complete(scope_id);
	} else if (bio_background_begin(FS_OWNER_SCOPE(scope_id))) {
		vfs_scope_reclaim_complete(scope_id);
		bio_background_end();
	}
	/*
	 * One checkpoint performs at most one bounded phase.  Re-arm the neutral
	 * edge from level state after the phase: requests concurrent with the
	 * earlier atomic take remain set, while requests coalesced into that take
	 * cannot strand a still-retiring lifecycle.
	 */
	if (vfs_scope_reap_work_pending())
		agent_background_request();
}

int vfs_scope_active(uint scope_id)
{
	int active = 0;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++)
		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].scope_id == scope_id &&
		    !vfs_scope_refs[i].retiring &&
		    workflow_lifecycle_active(
			    vfs_scope_refs[i].lifecycle)) {
			active = 1;
			break;
		}
	intr_restore(enabled);
	return active;
}

int vfs_scope_retiring(uint scope_id)
{
	int retiring = 0;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++)
		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].scope_id == scope_id &&
		    vfs_scope_refs[i].retiring &&
		    workflow_lifecycle_retiring(
			    vfs_scope_refs[i].lifecycle)) {
			retiring = 1;
			break;
		}
	intr_restore(enabled);
	return retiring;
}

int vfs_scope_retained(uint scope_id)
{
	int retained = 0;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++)
		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].scope_id == scope_id) {
			retained = 1;
			break;
		}
	intr_restore(enabled);
	return retained;
}

int
vfs_scope_lifecycle(uint scope_id, struct workflow_lifecycle_key *lifecycle)
{
	int result = -1;
	int enabled;

	if (lifecycle == 0)
		return -1;
	*lifecycle = workflow_lifecycle_none();
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++)
		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].scope_id == scope_id &&
		    workflow_lifecycle_key_valid(vfs_scope_refs[i].lifecycle)) {
			*lifecycle = vfs_scope_refs[i].lifecycle;
			result = 0;
			break;
		}
	intr_restore(enabled);
	return result;
}

int vfs_scope_bind_controller(uint scope_id,
			      struct workflow_lifecycle_key lifecycle,
			      uint64 control_id)
{
	int result = -1;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC || control_id == 0)
		return -1;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id ||
		    ref->retiring ||
		    !workflow_lifecycle_key_equal(ref->lifecycle, lifecycle))
			continue;
		result = workflow_lifecycle_bind_controller(
			lifecycle, scope_id, control_id);
		break;
	}
	intr_restore(enabled);
	return result;
}

int vfs_scope_close_owned(uint scope_id,
			  struct workflow_lifecycle_key lifecycle,
			  uint64 control_id,
			  struct workflow_lifecycle_key *closed)
{
	int result = -1;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC || closed == 0)
		return -1;
	*closed = workflow_lifecycle_none();
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id ||
		    ref->retiring ||
		    !workflow_lifecycle_key_equal(ref->lifecycle, lifecycle))
			continue;
		result = workflow_lifecycle_close_owned(
			scope_id, lifecycle, control_id, closed);
		break;
	}
	intr_restore(enabled);
	return result;
}

int vfs_scope_close_trusted(uint scope_id,
			    struct workflow_lifecycle_key *closed)
{
	int result = -1;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC || closed == 0)
		return -1;
	*closed = workflow_lifecycle_none();
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];

		if (!ref->used || ref->scope_id != scope_id ||
		    ref->retiring)
			continue;
		result = workflow_lifecycle_close_trusted(scope_id, closed);
		if (result == 0 &&
		    !workflow_lifecycle_key_equal(*closed, ref->lifecycle)) {
			*closed = workflow_lifecycle_none();
			result = -1;
		}
		break;
	}
	intr_restore(enabled);
	return result;
}

// Return the unconsumed storage guarantee that an allocation must leave for
// every active, closing, or future workflow slot. Retiring scopes keep their
// exact usage charged, while their unused guarantee becomes available to a
// new active scope.
uint vfs_scope_storage_guarantee(uint exempt_scope, int inode, uint guarantee)
{
	uint required = 0;
	int allocated = 0;
	int enabled;

	if (guarantee == 0)
		return 0;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];
		uint used;

		if (!ref->used || ref->retiring ||
		    (!workflow_lifecycle_active(ref->lifecycle) &&
		     !workflow_lifecycle_closing(ref->lifecycle)))
			continue;
		allocated++;
		if (ref->scope_id == exempt_scope)
			continue;
		used = resource_account_usage(
			ref->storage_account,
			inode ? RESOURCE_FS_INODE : RESOURCE_FS_BLOCK);
		if (used < guarantee)
			required += guarantee - used;
	}
	if (allocated < VFS_SCOPE_MAX_ACTIVE)
		required += (VFS_SCOPE_MAX_ACTIVE - allocated) * guarantee;
	intr_restore(enabled);
	return required;
}

uint
vfs_scope_metadata_inode_usage(void)
{
	uint64 total = 0;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];
		uint64 used;

		if (!ref->used ||
		    (!ref->retiring &&
		     !workflow_lifecycle_key_valid(ref->lifecycle)) ||
		    !resource_account_handle_valid(ref->storage_account))
			continue;
		used = resource_account_usage(ref->storage_account,
					      RESOURCE_FS_INODE);
		if (used > (uint)-1 - total) {
			total = (uint)-1;
			break;
		}
		total += used;
	}
	intr_restore(enabled);
	return (uint)total;
}

uint vfs_label_checksum(uint inum, uint magic, uint version, uint flags,
			uint scope_id, uint policy, uint exec_profile,
			uint generation, uint incarnation, uint fs_owner_domain,
			uint fs_owner_version)
{
	uint hash = 2166136261U ^ inum;
	uint words[] = { magic, version, flags, scope_id, policy, exec_profile,
			 generation, incarnation, fs_owner_domain,
			 fs_owner_version };

	for (uint i = 0; i < sizeof(words) / sizeof(words[0]); i++) {
		hash ^= words[i];
		hash *= 16777619U;
		hash ^= words[i] >> 16;
	}
	return hash ? hash : 1U;
}

static uint vfs_inode_checksum(struct inode *ip)
{
	return vfs_label_checksum(ip->inum, ip->vfs_magic, ip->vfs_version,
				  ip->vfs_flags, ip->vfs_scope_id,
				  ip->vfs_policy, ip->vfs_exec_profile,
				  ip->vfs_policy_generation,
				  ip->vfs_incarnation, ip->fs_owner_domain,
				  ip->fs_owner_version);
}

static int vfs_system_workflow_data_valid(struct inode *ip)
{
	return ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE &&
	       ip->exec_flags == 0 && ip->exec_generation == 0 &&
	       ip->exec_role_mask == 0 && ip->exec_layout_version == 0 &&
	       ip->exec_rw_offset == 0;
}

static int vfs_system_workflow_exec_valid(struct inode *ip)
{
	return exec_image_protected_shape_valid(
		ip->type == T_FILE, ip->size, ip->exec_flags,
		ip->exec_generation, ip->exec_role_mask,
		ip->exec_layout_version, ip->exec_rw_offset,
		ip->vfs_exec_profile, PAGE_SIZE);
}

void vfs_cred_kernel(struct vfs_cred *cred)
{
	if (cred == 0)
		return;
	cred->scope_id = VFS_SCOPE_NONE;
	cred->storage_principal_id = FS_OWNER_SYSTEM;
	cred->capabilities = ~0ULL;
	cred->kernel = 1;
}

static int vfs_cred_valid(const struct vfs_cred *cred)
{
	if (cred == 0)
		return 0;
	if (cred->kernel)
		return cred->scope_id == VFS_SCOPE_NONE &&
		       cred->storage_principal_id == FS_OWNER_SYSTEM;
	if (cred->scope_id == VFS_SCOPE_NONE)
		return cred->capabilities == 0 &&
		       cred->storage_principal_id == FS_OWNER_PUBLIC;
	return cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	       cred->scope_id < FS_OWNER_SCOPE_FLAG &&
	       cred->storage_principal_id == cred->scope_id &&
	       (cred->capabilities & ~VFS_CAP_WORKFLOW) == 0 &&
	       vfs_scope_active(cred->scope_id);
}

void vfs_cred_from_proc(const struct proc *p, struct vfs_cred *cred)
{
	if (cred == 0)
		return;
	cred->scope_id = p ? p->vfs_scope_id : VFS_SCOPE_NONE;
	// A provisional boot/workflow principal is not an effective credential.
	// Until the trusted image install activates its scope, the process has only
	// PUBLIC filesystem authority.  This also lets delegated workers resolve
	// their sealed image without exposing the pending workflow principal.
	cred->storage_principal_id = p == 0 ? FS_OWNER_NONE :
		p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
		p->storage_principal_id : FS_OWNER_PUBLIC;
	cred->capabilities = p ? p->vfs_effective_caps : 0;
	cred->kernel = 0;
}

uint vfs_cred_lookup_policy(const struct vfs_cred *cred)
{
	if (!vfs_cred_valid(cred))
		return 0;
	if (cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC)
		return VFS_POLICY_WORKFLOW;
	return VFS_POLICY_PUBLIC;
}

struct workflow_lifecycle_key vfs_proc_lifecycle(const struct proc *p)
{
	struct workflow_lifecycle_key lifecycle =
		workflow_lifecycle_none();

	if (p == 0 || !p->workflow_lifecycle_charged)
		return lifecycle;
	lifecycle.id = p->workflow_lifecycle_id;
	lifecycle.generation = p->workflow_lifecycle_generation;
	if (!workflow_lifecycle_key_valid(lifecycle))
		return workflow_lifecycle_none();
	return lifecycle;
}

static int
vfs_proc_lifecycle_attach(struct proc *p, uint scope_id,
			  struct workflow_lifecycle_key lifecycle)
{
	uint bound_scope;

	if (p == 0 || p->workflow_lifecycle_charged ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    workflow_lifecycle_scope(lifecycle, &bound_scope) < 0 ||
	    bound_scope != scope_id)
		return -1;
	p->workflow_lifecycle_id = lifecycle.id;
	p->workflow_lifecycle_generation = lifecycle.generation;
	p->workflow_lifecycle_charged = 1;
	return 0;
}

int vfs_proc_lifecycle_active(const struct proc *p)
{
	struct workflow_lifecycle_key lifecycle;

	if (p == 0)
		return 0;
	if (!p->workflow_lifecycle_charged)
		return 1;
	lifecycle.id = p->workflow_lifecycle_id;
	lifecycle.generation = p->workflow_lifecycle_generation;
	return workflow_lifecycle_key_valid(lifecycle) &&
	       workflow_lifecycle_active(lifecycle);
}

void vfs_proc_lifecycle_release(struct proc *p)
{
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;

	if (p == 0 || !p->workflow_lifecycle_charged)
		return;
	lifecycle = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    workflow_lifecycle_scope(lifecycle, &scope_id) < 0)
		panic("workflow lifecycle credential");
	// Clear only at final process teardown. Credential drop and exec must not
	// let a descendant escape the immutable termination lineage.
	p->workflow_lifecycle_charged = 0;
	p->workflow_lifecycle_id = WORKFLOW_LIFECYCLE_ID_NONE;
	p->workflow_lifecycle_generation = 0;
	if (vfs_scope_release(scope_id, lifecycle) < 0)
		panic("workflow lifecycle refcount");
}

static void vfs_proc_clear_credentials(struct proc *p)
{
	if (p == 0)
		return;
	p->vfs_scope_id = VFS_SCOPE_NONE;
	p->vfs_scope_controller = 0;
	p->vfs_effective_caps = 0;
	p->vfs_inheritable_caps = 0;
	p->vfs_pending_scope_id = VFS_SCOPE_NONE;
	p->vfs_pending_caps = 0;
	p->vfs_pending_exec_dev = 0;
	p->vfs_pending_exec_inum = 0;
	p->vfs_pending_exec_incarnation = 0;
	p->vfs_bound_exec_dev = 0;
	p->vfs_bound_exec_inum = 0;
	p->vfs_bound_exec_incarnation = 0;
}

void vfs_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	agent_scope_controller_departing(p);
	proc_revoke_vfs_scope_fds(p);
	vfs_proc_clear_credentials(p);
}

// Terminal teardown has already revoked controller authority and detached the
// complete FD table. It only clears the no-longer-published credential; the
// immutable lifecycle reference is released by the following settlement phase.
void vfs_proc_terminal_clear(struct proc *p)
{
	vfs_proc_clear_credentials(p);
}

int vfs_proc_spawn_scope(const struct proc *parent, struct proc *child,
			 enum vfs_spawn_scope_mode mode)
{
	struct workflow_lifecycle_key lifecycle =
		workflow_lifecycle_none();
	uint scope_id;
	int enabled;

	if (child == 0 || child->workflow_lifecycle_charged)
		return -1;
	vfs_proc_reset(child);
	if (mode == VFS_SPAWN_SCOPE_DROP) {
		int joined = 0;

		lifecycle = vfs_proc_lifecycle(parent);
		if (!workflow_lifecycle_key_valid(lifecycle))
			return 0;
		if (workflow_lifecycle_scope(lifecycle, &scope_id) < 0)
			return -1;
		enabled = intr_save();
		if (vfs_scope_join(scope_id, lifecycle) == 0)
			joined = 1;
		if (!joined ||
		    vfs_proc_lifecycle_attach(child, scope_id, lifecycle) < 0) {
			if (joined &&
			    vfs_scope_release(scope_id, lifecycle) < 0)
				panic("workflow lifecycle drop rollback");
			intr_restore(enabled);
			return -1;
		}
		intr_restore(enabled);
		return 0;
	}
	if (parent == 0 || parent->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    parent->vfs_scope_id != parent->storage_principal_id)
		return -1;
	if (mode == VFS_SPAWN_SCOPE_FRESH) {
		if (child->storage_principal_id < VFS_SCOPE_FIRST_DYNAMIC)
			return -1;
		scope_id = child->storage_principal_id;
		if (vfs_scope_create(scope_id, &lifecycle) < 0)
			return -1;
	} else if (mode == VFS_SPAWN_SCOPE_INHERIT) {
		if (child->storage_principal_id != parent->storage_principal_id)
			return -1;
		scope_id = parent->vfs_scope_id;
		lifecycle = vfs_proc_lifecycle(parent);
		if (!workflow_lifecycle_key_valid(lifecycle) ||
		    vfs_scope_join(scope_id, lifecycle) < 0)
			return -1;
	} else {
		return -1;
	}
	if (vfs_proc_lifecycle_attach(child, scope_id, lifecycle) < 0) {
		if (vfs_scope_release(scope_id, lifecycle) < 0)
			panic("workflow lifecycle attach rollback");
		return -1;
	}
	child->vfs_scope_id = scope_id;
	child->vfs_scope_controller = mode == VFS_SPAWN_SCOPE_FRESH;
	child->vfs_effective_caps = parent->vfs_effective_caps;
	child->vfs_inheritable_caps = parent->vfs_inheritable_caps;
	return 0;
}

int vfs_proc_scope_publishable(const struct proc *p)
{
	uint scope_id;

	if (p == 0)
		return 0;
	if (!vfs_proc_lifecycle_active(p))
		return 0;
	scope_id = p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC ?
			   p->vfs_scope_id : p->vfs_pending_scope_id;
	return scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	       vfs_scope_active(scope_id);
}

void vfs_proc_limit_capabilities(struct proc *p, uint64 capabilities)
{
	uint64 allowed;

	if (p == 0)
		return;
	allowed = capabilities & VFS_CAP_WORKFLOW;
	p->vfs_effective_caps &= allowed;
	p->vfs_inheritable_caps &= allowed;
}

int vfs_exec_profile_valid(uint profile)
{
	return exec_image_profile_valid(profile);
}

uint64 vfs_exec_profile_capabilities(uint profile)
{
	if (profile == VFS_EXEC_PROFILE_WORKFLOW)
		return VFS_CAP_WORKFLOW;
	if (profile == VFS_EXEC_PROFILE_CONTENT_READ)
		return VFS_CAP_CONTENT_READ;
	if (profile == VFS_EXEC_PROFILE_ARTIFACT_WRITE)
		return VFS_CAP_ARTIFACT_WRITE;
	return 0;
}

static int vfs_image_domain_safe(const struct user_image *image)
{
	uint required = EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE;

	return image != 0 &&
	       image->exec_dev != 0 && image->exec_inum != 0 &&
	       image->vfs_exec_incarnation != 0 &&
	       (image->exec_flags & ~EXEC_FLAG_KNOWN) == 0 &&
	       (image->exec_flags & required) == required &&
	       image->exec_generation == EXEC_MANIFEST_VERSION &&
	       (image->exec_role_mask & ~EXEC_MANIFEST_ROLE_ALL) == 0 &&
	       image->exec_layout_version == EXEC_LAYOUT_VERSION &&
	       image->exec_rw_offset >= PAGE_SIZE &&
	       (image->exec_rw_offset % PAGE_SIZE) == 0 &&
	       vfs_exec_profile_valid(image->vfs_exec_profile) &&
	       image->vfs_exec_profile != VFS_EXEC_PROFILE_NONE;
}

int vfs_proc_delegate_exec(const struct proc *parent, struct proc *child,
			   struct inode *image, uint64 requested_caps)
{
	struct workflow_lifecycle_key parent_lifecycle =
		vfs_proc_lifecycle(parent);
	struct workflow_lifecycle_key child_lifecycle =
		vfs_proc_lifecycle(child);
	uint64 ceiling;

	if (parent == 0 || child == 0 || image == 0 ||
	    parent->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    parent->vfs_scope_id != parent->storage_principal_id ||
	    child->storage_principal_id != parent->storage_principal_id ||
	    !workflow_lifecycle_key_equal(parent_lifecycle,
					  child_lifecycle) ||
	    !workflow_lifecycle_active(child_lifecycle) ||
	    !vfs_inode_label_valid(image) ||
	    image->vfs_policy != VFS_POLICY_WORKFLOW ||
	    image->vfs_scope_id != VFS_SCOPE_SYSTEM ||
	    image->vfs_exec_profile == VFS_EXEC_PROFILE_NONE ||
	    (image->exec_flags & (EXEC_FLAG_IMMUTABLE |
				  EXEC_FLAG_DOMAIN_SAFE)) !=
		    (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE))
		return -1;
	ceiling = vfs_exec_profile_capabilities(image->vfs_exec_profile);
	if (requested_caps == 0 || (requested_caps & ~VFS_CAP_WORKFLOW) != 0 ||
	    (requested_caps & parent->vfs_effective_caps) != requested_caps ||
	    (requested_caps & ceiling) != requested_caps)
		return -1;
	vfs_proc_reset(child);
	child->vfs_pending_scope_id = parent->vfs_scope_id;
	child->vfs_pending_caps = requested_caps;
	child->vfs_pending_exec_dev = image->dev;
	child->vfs_pending_exec_inum = image->inum;
	child->vfs_pending_exec_incarnation = image->vfs_incarnation;
	return 0;
}

static int vfs_proc_image_bound(const struct proc *p,
				const struct user_image *image)
{
	return p->vfs_bound_exec_dev == image->exec_dev &&
	       p->vfs_bound_exec_inum == image->exec_inum &&
	       p->vfs_bound_exec_incarnation == image->vfs_exec_incarnation;
}

static int vfs_agent_image_allowed(const struct proc *p,
				   const struct user_image *image)
{
	return p->is_agent && p->agent_role > 0 && p->agent_role < 32 &&
	       image->agent_class == USER_IMAGE_AGENT_TRUSTED &&
	       vfs_image_domain_safe(image) &&
	       (image->exec_flags & EXEC_FLAG_TRUSTED) != 0 &&
	       (image->exec_role_mask & EXEC_ROLE_BIT(p->agent_role)) != 0;
}

static void
vfs_proc_security_state_capture(const struct proc *p,
				struct vfs_proc_security_state *state)
{
	memset(state, 0, sizeof(*state));
	state->scope_id = p->vfs_scope_id;
	state->scope_controller = p->vfs_scope_controller;
	state->effective_caps = p->vfs_effective_caps;
	state->inheritable_caps = p->vfs_inheritable_caps;
	state->pending_scope_id = p->vfs_pending_scope_id;
	state->pending_caps = p->vfs_pending_caps;
	state->pending_exec_dev = p->vfs_pending_exec_dev;
	state->pending_exec_inum = p->vfs_pending_exec_inum;
	state->pending_exec_incarnation =
		p->vfs_pending_exec_incarnation;
	state->bound_exec_dev = p->vfs_bound_exec_dev;
	state->bound_exec_inum = p->vfs_bound_exec_inum;
	state->bound_exec_incarnation = p->vfs_bound_exec_incarnation;
	state->storage_principal_id = p->storage_principal_id;
	state->lifecycle_charged = p->workflow_lifecycle_charged;
	state->lifecycle = vfs_proc_lifecycle(p);
}

static int
vfs_proc_security_state_matches(const struct proc *p,
				const struct vfs_proc_security_state *state)
{
	return p != 0 && state != 0 &&
	       p->vfs_scope_id == state->scope_id &&
	       p->vfs_scope_controller == state->scope_controller &&
	       p->vfs_effective_caps == state->effective_caps &&
	       p->vfs_inheritable_caps == state->inheritable_caps &&
	       p->vfs_pending_scope_id == state->pending_scope_id &&
	       p->vfs_pending_caps == state->pending_caps &&
	       p->vfs_pending_exec_dev == state->pending_exec_dev &&
	       p->vfs_pending_exec_inum == state->pending_exec_inum &&
	       p->vfs_pending_exec_incarnation ==
		       state->pending_exec_incarnation &&
	       p->vfs_bound_exec_dev == state->bound_exec_dev &&
	       p->vfs_bound_exec_inum == state->bound_exec_inum &&
	       p->vfs_bound_exec_incarnation ==
		       state->bound_exec_incarnation &&
	       p->storage_principal_id == state->storage_principal_id &&
	       p->workflow_lifecycle_charged == state->lifecycle_charged &&
	       (!state->lifecycle_charged ||
		workflow_lifecycle_key_equal(vfs_proc_lifecycle(p),
					     state->lifecycle));
}

static void
vfs_proc_security_state_apply(struct proc *p,
			      const struct vfs_proc_security_state *state)
{
	p->vfs_scope_id = state->scope_id;
	p->vfs_scope_controller = state->scope_controller;
	p->vfs_effective_caps = state->effective_caps;
	p->vfs_inheritable_caps = state->inheritable_caps;
	p->vfs_pending_scope_id = state->pending_scope_id;
	p->vfs_pending_caps = state->pending_caps;
	p->vfs_pending_exec_dev = state->pending_exec_dev;
	p->vfs_pending_exec_inum = state->pending_exec_inum;
	p->vfs_pending_exec_incarnation =
		state->pending_exec_incarnation;
	p->vfs_bound_exec_dev = state->bound_exec_dev;
	p->vfs_bound_exec_inum = state->bound_exec_inum;
	p->vfs_bound_exec_incarnation = state->bound_exec_incarnation;
	p->storage_principal_id = state->storage_principal_id;
}

static void
vfs_exec_target_public(struct vfs_exec_transition *transition)
{
	struct vfs_proc_security_state *target = &transition->target;

	target->scope_id = VFS_SCOPE_NONE;
	target->scope_controller = 0;
	target->effective_caps = 0;
	target->inheritable_caps = 0;
	target->pending_scope_id = VFS_SCOPE_NONE;
	target->pending_caps = 0;
	target->pending_exec_dev = 0;
	target->pending_exec_inum = 0;
	target->pending_exec_incarnation = 0;
	target->bound_exec_dev = 0;
	target->bound_exec_inum = 0;
	target->bound_exec_incarnation = 0;
	target->storage_principal_id = FS_OWNER_PUBLIC;
	transition->identity_policy = VFS_EXEC_IDENTITY_PUBLIC;
	transition->drop_to_public = 1;
}

int vfs_proc_exec_prepare(struct proc *p, const struct user_image *image,
			  int running,
			  struct vfs_exec_transition *transition)
{
	uint64 ceiling;

	if (p == 0 || image == 0 || transition == 0 ||
	    !proc_teardown_live(p))
		return -1;
	memset(transition, 0, sizeof(*transition));
	transition->identity_policy = VFS_EXEC_IDENTITY_PUBLIC;
	vfs_proc_security_state_capture(p, &transition->source);
	transition->target = transition->source;
	ceiling = vfs_exec_profile_capabilities(image->vfs_exec_profile);
	if (!running) {
		uint required = EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE |
				EXEC_FLAG_BOOTSTRAP | EXEC_FLAG_DOMAIN_SAFE;
		struct workflow_lifecycle_key lifecycle =
			workflow_lifecycle_none();
		uint scope_id = p->storage_principal_id;

		if (!vfs_image_domain_safe(image) ||
		    (image->exec_flags & required) != required ||
		    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    p->workflow_lifecycle_charged ||
		    vfs_scope_create(scope_id, &lifecycle) < 0) {
			vfs_exec_target_public(transition);
			goto prepared;
		}
		transition->target.scope_id = scope_id;
		transition->target.effective_caps = ceiling;
		transition->target.inheritable_caps = ceiling;
		transition->target.bound_exec_dev = image->exec_dev;
		transition->target.bound_exec_inum = image->exec_inum;
		transition->target.bound_exec_incarnation =
			image->vfs_exec_incarnation;
		transition->target.lifecycle_charged = 1;
		transition->target.lifecycle = lifecycle;
		transition->lifecycle_reserved = 1;
		goto prepared;
	}
	if (p->vfs_pending_exec_inum != 0) {
		uint pending_scope_id = p->vfs_pending_scope_id;
		uint64 effective_caps = p->vfs_pending_caps & ceiling;
		int matches = p->vfs_pending_exec_dev == image->exec_dev &&
			      p->vfs_pending_exec_inum == image->exec_inum &&
			      p->vfs_pending_exec_incarnation ==
				      image->vfs_exec_incarnation;

		if (!matches || !vfs_image_domain_safe(image) ||
		    pending_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    pending_scope_id != p->storage_principal_id ||
		    p->vfs_scope_id != VFS_SCOPE_NONE || effective_caps == 0 ||
		    !vfs_scope_active(pending_scope_id) ||
		    !vfs_proc_lifecycle_active(p)) {
			vfs_exec_target_public(transition);
			goto prepared;
		}
		transition->target.scope_id = pending_scope_id;
		transition->target.pending_scope_id = VFS_SCOPE_NONE;
		transition->target.pending_caps = 0;
		transition->target.pending_exec_dev = 0;
		transition->target.pending_exec_inum = 0;
		transition->target.pending_exec_incarnation = 0;
		transition->target.effective_caps = effective_caps;
		transition->target.inheritable_caps = effective_caps;
		transition->target.bound_exec_dev = image->exec_dev;
		transition->target.bound_exec_inum = image->exec_inum;
		transition->target.bound_exec_incarnation =
			image->vfs_exec_incarnation;
		goto prepared;
	}
	if (p->is_agent) {
		transition->target.effective_caps =
			p->vfs_inheritable_caps & ceiling;
		transition->target.inheritable_caps &= ceiling;
		if (!vfs_agent_image_allowed(p, image) ||
		    transition->target.effective_caps == 0) {
			vfs_exec_target_public(transition);
			goto prepared;
		}
		transition->target.bound_exec_dev = image->exec_dev;
		transition->target.bound_exec_inum = image->exec_inum;
		transition->target.bound_exec_incarnation =
			image->vfs_exec_incarnation;
		transition->identity_policy =
			VFS_EXEC_IDENTITY_PRESERVE_AGENT;
		goto prepared;
	}
	if (p->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !vfs_image_domain_safe(image) || !vfs_proc_image_bound(p, image)) {
		vfs_exec_target_public(transition);
		goto prepared;
	}
	transition->target.effective_caps =
		p->vfs_inheritable_caps & ceiling;
	transition->target.inheritable_caps &= ceiling;
	if (transition->target.effective_caps == 0)
		vfs_exec_target_public(transition);

prepared:
	/* Prepare is deliberately side-effect free; revocation belongs to commit. */
	transition->prepared = 1;
	return 0;
}

int vfs_proc_exec_validate_locked(
	struct proc *p, const struct vfs_exec_transition *transition)
{
	const struct vfs_proc_security_state *target;
	uint lifecycle_scope = VFS_SCOPE_NONE;

	if (intr_get())
		panic("exec credential validation unlocked");
	if (p == 0 || transition == 0 || !transition->prepared ||
	    !proc_teardown_live(p) ||
	    !vfs_proc_security_state_matches(p, &transition->source))
		return -1;
	target = &transition->target;
	if (transition->identity_policy != VFS_EXEC_IDENTITY_PUBLIC &&
	    transition->identity_policy !=
		    VFS_EXEC_IDENTITY_PRESERVE_AGENT)
		return -1;
	if (transition->identity_policy ==
		    VFS_EXEC_IDENTITY_PRESERVE_AGENT &&
	    (!p->is_agent || transition->drop_to_public))
		return -1;
	if (target->lifecycle_charged) {
		if (!workflow_lifecycle_key_valid(target->lifecycle) ||
		    !workflow_lifecycle_active(target->lifecycle) ||
		    workflow_lifecycle_scope(target->lifecycle,
					     &lifecycle_scope) < 0)
			return -1;
		if (target->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
		    target->scope_id != lifecycle_scope)
			return -1;
	}
	if (target->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	    (!target->lifecycle_charged ||
	     target->scope_id != lifecycle_scope ||
	     !vfs_scope_active(target->scope_id)))
		return -1;
	if (transition->lifecycle_reserved &&
	    p->workflow_lifecycle_charged)
		return -1;
	return 0;
}

int vfs_proc_exec_commit(struct proc *p,
			 struct vfs_exec_transition *transition)
{
	struct vfs_proc_security_state *target;

	if (vfs_proc_exec_validate_locked(p, transition) < 0)
		return -1;
	/* A caller may not publish PUBLIC VFS credentials around stale Agent state. */
	if (transition->identity_policy == VFS_EXEC_IDENTITY_PUBLIC &&
	    (p->is_agent || p->agent_type != AGENT_TYPE_NONE ||
	     p->agent_role != 0 || p->agent_control_id != 0 ||
	     p->agent_ctx_base != 0 || p->agent_capability_mask != 0))
		return -1;
	target = &transition->target;
	if (transition->lifecycle_reserved) {
		if (vfs_scope_preserve_on_retire(target->scope_id) < 0)
			return -1;
		if (vfs_proc_lifecycle_attach(p, target->scope_id,
					      target->lifecycle) < 0)
			panic("exec lifecycle publication");
		transition->lifecycle_reserved = 0;
	}
	vfs_proc_security_state_apply(p, target);
	transition->prepared = 0;
	return 0;
}

void vfs_proc_exec_abort(struct vfs_exec_transition *transition)
{
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;

	if (transition == 0)
		return;
	lifecycle = transition->target.lifecycle;
	scope_id = transition->target.scope_id;
	if (transition->lifecycle_reserved &&
	    vfs_scope_release(scope_id, lifecycle) < 0)
		panic("exec lifecycle rollback");
	memset(transition, 0, sizeof(*transition));
}

static int vfs_label_shape_valid(struct inode *ip)
{
	uint flag;

	if (ip->vfs_magic != VFS_LABEL_MAGIC ||
	    ip->vfs_version != VFS_LABEL_VERSION ||
	    ip->vfs_policy_generation != VFS_POLICY_GENERATION ||
	    ip->vfs_incarnation == 0 ||
	    (ip->vfs_flags & ~VFS_LABEL_F_KNOWN) != 0 ||
	    ip->vfs_checksum != vfs_inode_checksum(ip))
		return 0;
	if (ip->vfs_policy == VFS_POLICY_FREE) {
		if (ip->fs_owner_domain != FS_OWNER_NONE ||
		    ip->fs_owner_version != 0)
			return 0;
	} else if (ip->fs_owner_domain < FS_OWNER_SYSTEM ||
		   ip->fs_owner_version != FS_OWNER_VERSION) {
		return 0;
	}
	if (!vfs_exec_profile_valid(ip->vfs_exec_profile))
		return 0;
	if (ip->vfs_exec_profile != VFS_EXEC_PROFILE_NONE &&
	    (ip->type != T_FILE ||
	     (ip->exec_flags & (EXEC_FLAG_IMMUTABLE |
				EXEC_FLAG_DOMAIN_SAFE)) !=
		     (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE) ||
	     ip->exec_generation != EXEC_MANIFEST_VERSION))
		return 0;
	flag = ip->vfs_flags;
	switch (ip->vfs_policy) {
	case VFS_POLICY_PUBLIC:
		return flag == VFS_LABEL_F_PUBLIC &&
		       ip->vfs_scope_id == VFS_SCOPE_NONE &&
		       FS_OWNER_IS_PUBLIC_OBJECT(ip->fs_owner_domain) &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	case VFS_POLICY_WORKFLOW:
		if (flag != VFS_LABEL_F_PROTECTED ||
		    ip->vfs_scope_id == VFS_SCOPE_NONE)
			return 0;
		if (ip->vfs_scope_id == VFS_SCOPE_SYSTEM) {
			if (ip->type != T_FILE ||
			    ip->fs_owner_domain != FS_OWNER_SYSTEM)
				return 0;
			return vfs_system_workflow_data_valid(ip) ||
			       vfs_system_workflow_exec_valid(ip);
		}
		return ip->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
		       ip->vfs_scope_id < FS_OWNER_SCOPE_FLAG &&
		       FS_OWNER_IS_SCOPE(ip->fs_owner_domain) &&
		       FS_OWNER_SCOPE_ID(ip->fs_owner_domain) ==
			       ip->vfs_scope_id &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE &&
		       ip->exec_flags == 0 && ip->exec_generation == 0 &&
		       ip->exec_role_mask == 0;
	case VFS_POLICY_KERNEL_PRIVATE:
		return flag == VFS_LABEL_F_KERNEL_PRIVATE &&
		       ip->vfs_scope_id == VFS_SCOPE_NONE &&
		       ip->fs_owner_domain == FS_OWNER_SYSTEM &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	case VFS_POLICY_ROOT:
		return flag == VFS_LABEL_F_ROOT &&
		       ip->vfs_scope_id == VFS_SCOPE_NONE &&
		       ip->fs_owner_domain == FS_OWNER_SYSTEM &&
		       ip->type == T_DIR &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	case VFS_POLICY_FREE:
		return flag == VFS_LABEL_F_FREE &&
		       ip->vfs_scope_id == VFS_SCOPE_NONE &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	default:
		return 0;
	}
}

int vfs_inode_label_valid(struct inode *ip)
{
	if (ip == 0)
		return 0;
	if (ivalid(ip) < 0)
		return 0;
	return vfs_label_shape_valid(ip);
}

int vfs_inode_authorize(struct inode *ip, const struct vfs_cred *cred,
			 enum vfs_operation op)
{
	uint64 required = 0;

	if (!vfs_cred_valid(cred) || !vfs_inode_label_valid(ip))
		return 0;
	if (cred->kernel && cred->scope_id == VFS_SCOPE_NONE)
		return 1;
	if (ip->vfs_policy == VFS_POLICY_FREE)
		return 0;
	if (ip->vfs_policy == VFS_POLICY_KERNEL_PRIVATE)
		return 0;
	if (ip->vfs_policy == VFS_POLICY_ROOT) {
		if (op == VFS_OP_LOOKUP || op == VFS_OP_READ)
			return 1;
		// Raw directory bytes are kernel-private. User credentials receive
		// only target-aware namespace operations implemented by
		// fs_create/dirlink/dirunlink.
		if (op == VFS_OP_CREATE || op == VFS_OP_DELETE) {
			if (cred->scope_id == VFS_SCOPE_NONE)
				return 1;
			return cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
			       (cred->capabilities & VFS_CAP_ARTIFACT_WRITE) != 0;
		}
		return 0;
	}
	if (op == VFS_OP_EXEC)
		return ip->type == T_FILE &&
		       (ip->vfs_policy != VFS_POLICY_WORKFLOW ||
			(ip->vfs_scope_id == VFS_SCOPE_SYSTEM &&
			 vfs_system_workflow_exec_valid(ip)));
	if (ip->vfs_policy == VFS_POLICY_PUBLIC)
		return cred->scope_id == VFS_SCOPE_NONE;
	if (ip->vfs_policy == VFS_POLICY_WORKFLOW &&
	    ip->vfs_scope_id == VFS_SCOPE_SYSTEM)
		return (op == VFS_OP_LOOKUP || op == VFS_OP_READ) &&
		       cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
		       (cred->capabilities & VFS_CAP_CONTENT_READ) != 0;
	if (ip->vfs_policy != VFS_POLICY_WORKFLOW ||
	    cred->scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    cred->scope_id != ip->vfs_scope_id)
		return 0;
	if (op == VFS_OP_LOOKUP || op == VFS_OP_READ)
		required = VFS_CAP_CONTENT_READ;
	else if (op == VFS_OP_CREATE || op == VFS_OP_WRITE ||
		 op == VFS_OP_TRUNCATE || op == VFS_OP_DELETE)
		required = VFS_CAP_ARTIFACT_WRITE;
	return required != 0 &&
	       (cred->capabilities & required) == required;
}

uint vfs_default_create_policy(const struct vfs_cred *cred)
{
	if (!vfs_cred_valid(cred))
		return 0;
	if (cred->kernel && cred->scope_id == VFS_SCOPE_NONE)
		return VFS_POLICY_KERNEL_PRIVATE;
	if (cred->scope_id == VFS_SCOPE_NONE)
		return VFS_POLICY_PUBLIC;
	if (cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	    (cred->capabilities & VFS_CAP_ARTIFACT_WRITE) != 0)
		return VFS_POLICY_WORKFLOW;
	return 0;
}

static int vfs_policy_subject_allowed(const struct vfs_cred *cred,
				      uint policy)
{
	if (!vfs_cred_valid(cred))
		return 0;
	if (policy == VFS_POLICY_KERNEL_PRIVATE)
		return cred->kernel && cred->scope_id == VFS_SCOPE_NONE;
	if (policy == VFS_POLICY_PUBLIC)
		return !cred->kernel && cred->scope_id == VFS_SCOPE_NONE;
	if (policy == VFS_POLICY_WORKFLOW)
		return !cred->kernel &&
		       cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
		       (cred->capabilities & VFS_CAP_ARTIFACT_WRITE) != 0;
	return 0;
}

int vfs_create_request_authorize(const struct vfs_cred *cred, uint policy,
				 int readable, int writable, int truncate)
{
	if (!vfs_policy_subject_allowed(cred, policy))
		return 0;
	if (cred->kernel)
		return 1;
	if (policy == VFS_POLICY_PUBLIC)
		return cred->scope_id == VFS_SCOPE_NONE;
	if (policy != VFS_POLICY_WORKFLOW ||
	    cred->scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return 0;
	if (readable &&
	    (cred->capabilities & VFS_CAP_CONTENT_READ) == 0)
		return 0;
	if ((writable || truncate) &&
	    (cred->capabilities & VFS_CAP_ARTIFACT_WRITE) == 0)
		return 0;
	return 1;
}

int vfs_inode_init_label(struct inode *ip, const struct vfs_cred *cred,
			 uint policy)
{
	if (ip == 0 || !vfs_policy_subject_allowed(cred, policy) ||
	    ip->vfs_incarnation == 0 ||
	    ip->fs_owner_domain < FS_OWNER_SYSTEM ||
	    ip->fs_owner_version != FS_OWNER_VERSION)
		return -1;
	if ((policy == VFS_POLICY_WORKFLOW &&
	     (cred->scope_id >= FS_OWNER_SCOPE_FLAG ||
	      ip->fs_owner_domain != FS_OWNER_SCOPE(cred->scope_id))) ||
	    (policy == VFS_POLICY_PUBLIC &&
	     !FS_OWNER_IS_PUBLIC_OBJECT(ip->fs_owner_domain)) ||
	    (policy == VFS_POLICY_KERNEL_PRIVATE &&
	     ip->fs_owner_domain != FS_OWNER_SYSTEM))
		return -1;
	ip->vfs_magic = VFS_LABEL_MAGIC;
	ip->vfs_version = VFS_LABEL_VERSION;
	ip->vfs_policy_generation = VFS_POLICY_GENERATION;
	ip->vfs_exec_profile = VFS_EXEC_PROFILE_NONE;
	if (policy == VFS_POLICY_PUBLIC) {
		ip->vfs_flags = VFS_LABEL_F_PUBLIC;
		ip->vfs_scope_id = VFS_SCOPE_NONE;
	} else if (policy == VFS_POLICY_WORKFLOW) {
		ip->vfs_flags = VFS_LABEL_F_PROTECTED;
		ip->vfs_scope_id = cred->scope_id;
	} else {
		ip->vfs_flags = VFS_LABEL_F_KERNEL_PRIVATE;
		ip->vfs_scope_id = VFS_SCOPE_NONE;
	}
	ip->vfs_policy = policy;
	ip->vfs_checksum = vfs_inode_checksum(ip);
	return 0;
}

int vfs_inode_create_matches(struct inode *ip, const struct vfs_cred *cred,
			     uint policy)
{
	return vfs_policy_subject_allowed(cred, policy) &&
	       vfs_inode_label_valid(ip) && ip->vfs_policy == policy &&
	       (policy != VFS_POLICY_WORKFLOW ||
		ip->vfs_scope_id == cred->scope_id);
}

void vfs_inode_mark_free(struct inode *ip)
{
	if (ip == 0)
		return;
	ip->vfs_magic = VFS_LABEL_MAGIC;
	ip->vfs_version = VFS_LABEL_VERSION;
	ip->vfs_flags = VFS_LABEL_F_FREE;
	ip->vfs_scope_id = VFS_SCOPE_NONE;
	ip->vfs_policy = VFS_POLICY_FREE;
	ip->vfs_exec_profile = VFS_EXEC_PROFILE_NONE;
	ip->vfs_policy_generation = VFS_POLICY_GENERATION;
	ip->fs_owner_domain = FS_OWNER_NONE;
	ip->fs_owner_version = 0;
	ip->vfs_checksum = vfs_inode_checksum(ip);
}
