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
#include "../user/include/exec_policy_manifest.h"

_Static_assert(EXEC_MANIFEST_VFS_CONTENT_READ == VFS_CAP_CONTENT_READ,
	       "content-read capability mismatch");
_Static_assert(EXEC_MANIFEST_VFS_ARTIFACT_WRITE == VFS_CAP_ARTIFACT_WRITE,
	       "artifact-write capability mismatch");

struct vfs_scope_ref {
	int used;
	uint scope_id;
	int members;
	int retiring;
	int preserve_on_retire;
	uint storage_blocks;
	uint storage_inodes;
};

static struct vfs_scope_ref vfs_scope_refs[NPROC];
static uint vfs_scope_reap_cursor;

static int vfs_scope_acquire(uint scope_id)
{
	int free_slot = -1;
	int allocated = 0;
	int retiring = 0;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return -1;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].scope_id == scope_id) {
			if (vfs_scope_refs[i].retiring ||
			    vfs_scope_refs[i].members <= 0) {
				intr_restore(enabled);
				return -1;
			}
			vfs_scope_refs[i].members++;
			intr_restore(enabled);
			return 0;
		}
		if (vfs_scope_refs[i].used) {
			if (!vfs_scope_refs[i].retiring &&
			    vfs_scope_refs[i].members > 0)
				allocated++;
			else if (vfs_scope_refs[i].retiring &&
				 vfs_scope_refs[i].members == 0)
				retiring++;
		} else if (free_slot < 0) {
			free_slot = i;
		}
	}
	if (free_slot >= 0 && allocated < VFS_SCOPE_MAX_ACTIVE &&
	    allocated + retiring < VFS_SCOPE_LIFECYCLE_CAP &&
	    fs_storage_scope_admissible() && bio_scope_acquire(scope_id) == 0) {
		vfs_scope_refs[free_slot].used = 1;
		vfs_scope_refs[free_slot].scope_id = scope_id;
		vfs_scope_refs[free_slot].members = 1;
		vfs_scope_refs[free_slot].retiring = 0;
		vfs_scope_refs[free_slot].preserve_on_retire = 0;
		vfs_scope_refs[free_slot].storage_blocks = 0;
		vfs_scope_refs[free_slot].storage_inodes = 0;
	} else {
		free_slot = -1;
	}
	intr_restore(enabled);
	return free_slot >= 0 ? 0 : -1;
}

static int vfs_scope_release(uint scope_id)
{
	int last = 0;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return 0;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		if (!vfs_scope_refs[i].used ||
		    vfs_scope_refs[i].scope_id != scope_id)
			continue;
		if (vfs_scope_refs[i].members <= 0)
			panic("workflow scope refcount");
		vfs_scope_refs[i].members--;
		if (vfs_scope_refs[i].members == 0) {
			vfs_scope_refs[i].retiring = 1;
			last = 1;
		}
		break;
	}
	intr_restore(enabled);
	if (last)
		bio_scope_quiesce(scope_id);
	return last;
}

static void vfs_scope_reclaim_complete(uint scope_id)
{
	int preserve_files = 0;
	int eligible = 0;
	int retired = 0;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		if (!vfs_scope_refs[i].used ||
		    vfs_scope_refs[i].scope_id != scope_id)
			continue;
		eligible = vfs_scope_refs[i].members == 0 &&
			   vfs_scope_refs[i].retiring;
		preserve_files = vfs_scope_refs[i].preserve_on_retire;
		break;
	}
	intr_restore(enabled);
	if (!eligible || agent_scope_reclaim(scope_id, preserve_files) < 0)
		return;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		if (!vfs_scope_refs[i].used ||
		    vfs_scope_refs[i].scope_id != scope_id)
			continue;
		if (vfs_scope_refs[i].members == 0 &&
		    vfs_scope_refs[i].retiring) {
			if (preserve_files)
				// A completed boot lease remains an admitted, inactive
				// storage owner until reboot. This keeps its durable
				// output in the same guarantee calculation.
				vfs_scope_refs[i].retiring = 0;
			else if (vfs_scope_refs[i].storage_blocks == 0 &&
				 vfs_scope_refs[i].storage_inodes == 0) {
				memset(&vfs_scope_refs[i], 0,
				       sizeof(vfs_scope_refs[i]));
				retired = 1;
			}
		}
		break;
	}
	intr_restore(enabled);
	if (retired)
		bio_scope_retire(scope_id);
}

static int vfs_scope_preserve_on_retire(uint scope_id)
{
	int result = -1;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++) {
		if (!vfs_scope_refs[i].used ||
		    vfs_scope_refs[i].scope_id != scope_id)
			continue;
		if (!vfs_scope_refs[i].retiring &&
		    vfs_scope_refs[i].members > 0) {
			vfs_scope_refs[i].preserve_on_retire = 1;
			result = 0;
		}
		break;
	}
	intr_restore(enabled);
	return result;
}

void vfs_scope_reap_pending(void)
{
	uint scope_id = VFS_SCOPE_NONE;
	int enabled = intr_save();

	for (uint scanned = 0; scanned < NPROC; scanned++) {
		uint i = (vfs_scope_reap_cursor + scanned) % NPROC;

		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].retiring &&
		    vfs_scope_refs[i].members == 0) {
			scope_id = vfs_scope_refs[i].scope_id;
			vfs_scope_reap_cursor = (i + 1) % NPROC;
			break;
		}
	}
	intr_restore(enabled);
	if (scope_id != VFS_SCOPE_NONE &&
	    bio_background_begin(FS_OWNER_SCOPE(scope_id))) {
		vfs_scope_reclaim_complete(scope_id);
		bio_background_end();
	}
}

int vfs_scope_active(uint scope_id)
{
	int active = 0;
	int enabled = intr_save();

	for (int i = 0; i < NPROC; i++)
		if (vfs_scope_refs[i].used &&
		    vfs_scope_refs[i].scope_id == scope_id &&
		    vfs_scope_refs[i].members > 0) {
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
		    vfs_scope_refs[i].members == 0) {
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

// Return the unconsumed storage guarantee that an allocation must leave for
// every active or future workflow slot. Retiring scopes keep their exact
// usage charged, while their unused guarantee becomes available to a new
// active scope.
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

		if (!ref->used || ref->retiring || ref->members <= 0)
			continue;
		allocated++;
		if (ref->scope_id == exempt_scope)
			continue;
		used = inode ? ref->storage_inodes : ref->storage_blocks;
		if (used < guarantee)
			required += guarantee - used;
	}
	if (allocated < VFS_SCOPE_MAX_ACTIVE)
		required += (VFS_SCOPE_MAX_ACTIVE - allocated) * guarantee;
	intr_restore(enabled);
	return required;
}

int vfs_scope_storage_reserve(uint scope_id, int inode, uint limit)
{
	int result = -1;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC || limit == 0)
		return -1;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];
		uint *used;

		if (!ref->used || ref->scope_id != scope_id)
			continue;
		used = inode ? &ref->storage_inodes : &ref->storage_blocks;
		if (!ref->retiring && ref->members > 0 && *used < limit) {
			(*used)++;
			result = 0;
		}
		break;
	}
	intr_restore(enabled);
	return result;
}

int vfs_scope_storage_release(uint scope_id, int inode)
{
	int handled = 0;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return 0;
	enabled = intr_save();
	for (int i = 0; i < NPROC; i++) {
		struct vfs_scope_ref *ref = &vfs_scope_refs[i];
		uint *used;

		if (!ref->used || ref->scope_id != scope_id)
			continue;
		used = inode ? &ref->storage_inodes : &ref->storage_blocks;
		if (*used == 0)
			panic("workflow storage quota invariant");
		(*used)--;
		handled = 1;
		break;
	}
	intr_restore(enabled);
	return handled;
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

void vfs_proc_reset(struct proc *p)
{
	uint old_scope_id;
	uint pending_scope_id;

	if (p == 0)
		return;
	old_scope_id = p->vfs_scope_id;
	pending_scope_id = p->vfs_pending_scope_id;
	proc_revoke_vfs_scope_fds(p);
	p->vfs_scope_id = VFS_SCOPE_NONE;
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
	// The last reference only transitions the scope to RETIRING. The
	// round-robin reaper is the sole cleanup driver, so every reclaim step is
	// admitted through the scope's BACKGROUND budget and cache partition.
	vfs_scope_release(old_scope_id);
	if (pending_scope_id != old_scope_id)
		vfs_scope_release(pending_scope_id);
}

// Once an image is installed without a workflow credential, storage charging
// must follow the stable PUBLIC tenant rather than a provisional workflow ID.
void vfs_proc_drop_to_public(struct proc *p)
{
	vfs_proc_reset(p);
	if (p != 0)
		p->storage_principal_id = FS_OWNER_PUBLIC;
}

int vfs_proc_spawn_scope(const struct proc *parent, struct proc *child,
			 enum vfs_spawn_scope_mode mode)
{
	if (child == 0)
		return -1;
	vfs_proc_reset(child);
	if (mode == VFS_SPAWN_SCOPE_DROP)
		return 0;
	if (parent == 0 || parent->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    parent->vfs_scope_id != parent->storage_principal_id)
		return -1;
	if (mode == VFS_SPAWN_SCOPE_FRESH) {
		if (child->storage_principal_id < VFS_SCOPE_FIRST_DYNAMIC)
			return -1;
		child->vfs_scope_id = child->storage_principal_id;
	} else if (mode == VFS_SPAWN_SCOPE_INHERIT) {
		if (child->storage_principal_id != parent->storage_principal_id)
			return -1;
		child->vfs_scope_id = parent->vfs_scope_id;
	} else {
		return -1;
	}
	if (vfs_scope_acquire(child->vfs_scope_id) < 0) {
		vfs_proc_reset(child);
		return -1;
	}
	child->vfs_effective_caps = parent->vfs_effective_caps;
	child->vfs_inheritable_caps = parent->vfs_inheritable_caps;
	return 0;
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
	return profile == VFS_EXEC_PROFILE_NONE ||
	       profile == VFS_EXEC_PROFILE_WORKFLOW ||
	       profile == VFS_EXEC_PROFILE_CONTENT_READ ||
	       profile == VFS_EXEC_PROFILE_ARTIFACT_WRITE;
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
	uint64 ceiling;

	if (parent == 0 || child == 0 || image == 0 ||
	    parent->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    parent->vfs_scope_id != parent->storage_principal_id ||
	    child->storage_principal_id != parent->storage_principal_id ||
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
	if (vfs_scope_acquire(parent->vfs_scope_id) < 0)
		return -1;
	child->vfs_pending_scope_id = parent->vfs_scope_id;
	child->vfs_pending_caps = requested_caps;
	child->vfs_pending_exec_dev = image->dev;
	child->vfs_pending_exec_inum = image->inum;
	child->vfs_pending_exec_incarnation = image->vfs_incarnation;
	return 0;
}

static void vfs_proc_bind_image(struct proc *p,
				const struct user_image *image)
{
	p->vfs_bound_exec_dev = image->exec_dev;
	p->vfs_bound_exec_inum = image->exec_inum;
	p->vfs_bound_exec_incarnation = image->vfs_exec_incarnation;
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
	       vfs_image_domain_safe(image) &&
	       (image->exec_flags & EXEC_FLAG_TRUSTED) != 0 &&
	       (image->exec_role_mask & EXEC_ROLE_BIT(p->agent_role)) != 0;
}

void vfs_proc_install_image(struct proc *p, const struct user_image *image,
			    int running)
{
	uint64 ceiling;

	if (p == 0 || image == 0) {
		vfs_proc_drop_to_public(p);
		return;
	}
	ceiling = vfs_exec_profile_capabilities(image->vfs_exec_profile);
	if (!running) {
		uint required = EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE |
				EXEC_FLAG_BOOTSTRAP | EXEC_FLAG_DOMAIN_SAFE;

		if (!vfs_image_domain_safe(image) ||
		    (image->exec_flags & required) != required) {
			vfs_proc_drop_to_public(p);
			return;
		}
		if (p->storage_principal_id < VFS_SCOPE_FIRST_DYNAMIC) {
			vfs_proc_drop_to_public(p);
			return;
		}
		p->vfs_scope_id = p->storage_principal_id;
		if (vfs_scope_acquire(p->vfs_scope_id) < 0) {
			vfs_proc_drop_to_public(p);
			return;
		}
		if (vfs_scope_preserve_on_retire(p->vfs_scope_id) < 0) {
			vfs_proc_drop_to_public(p);
			return;
		}
		p->vfs_effective_caps = ceiling;
		p->vfs_inheritable_caps = ceiling;
		vfs_proc_bind_image(p, image);
		return;
	}
	if (p->vfs_pending_exec_inum != 0) {
		uint pending_scope_id = p->vfs_pending_scope_id;
		uint64 pending_caps = p->vfs_pending_caps;
		uint64 effective_caps = pending_caps & ceiling;
		int matches = p->vfs_pending_exec_dev == image->exec_dev &&
			      p->vfs_pending_exec_inum == image->exec_inum &&
			      p->vfs_pending_exec_incarnation ==
				      image->vfs_exec_incarnation;

		if (!matches || !vfs_image_domain_safe(image) ||
		    pending_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    pending_scope_id != p->storage_principal_id ||
		    p->vfs_scope_id != VFS_SCOPE_NONE || effective_caps == 0) {
			vfs_proc_drop_to_public(p);
			return;
		}
		p->vfs_scope_id = pending_scope_id;
		p->vfs_pending_scope_id = VFS_SCOPE_NONE;
		p->vfs_pending_caps = 0;
		p->vfs_pending_exec_dev = 0;
		p->vfs_pending_exec_inum = 0;
		p->vfs_pending_exec_incarnation = 0;
		p->vfs_effective_caps = effective_caps;
		p->vfs_inheritable_caps = p->vfs_effective_caps;
		vfs_proc_bind_image(p, image);
		return;
	}
	if (p->is_agent) {
		if (!vfs_agent_image_allowed(p, image)) {
			vfs_proc_drop_to_public(p);
			return;
		}
		p->vfs_effective_caps = p->vfs_inheritable_caps & ceiling;
		p->vfs_inheritable_caps &= ceiling;
		if (p->vfs_effective_caps == 0) {
			vfs_proc_drop_to_public(p);
			return;
		}
		vfs_proc_bind_image(p, image);
		return;
	}
	if (p->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !vfs_image_domain_safe(image) || !vfs_proc_image_bound(p, image)) {
		vfs_proc_drop_to_public(p);
		return;
	}
	p->vfs_effective_caps = p->vfs_inheritable_caps & ceiling;
	p->vfs_inheritable_caps &= ceiling;
	if (p->vfs_effective_caps == 0)
		vfs_proc_drop_to_public(p);
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
		if (ip->vfs_scope_id == VFS_SCOPE_SYSTEM)
			return ip->type == T_FILE &&
			       ip->fs_owner_domain == FS_OWNER_SYSTEM &&
			       (ip->exec_flags &
				(EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE)) ==
				(EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE) &&
			       ip->exec_generation == EXEC_MANIFEST_VERSION &&
			       ip->exec_layout_version == EXEC_LAYOUT_VERSION;
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
	ivalid(ip);
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
			ip->vfs_scope_id == VFS_SCOPE_SYSTEM);
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
