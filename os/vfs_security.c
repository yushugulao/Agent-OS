#include "vfs_security.h"
#include "const.h"
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

uint vfs_label_checksum(uint inum, uint magic, uint version, uint flags,
			uint domain, uint policy, uint exec_profile,
			uint generation, uint incarnation, uint reserved0,
			uint reserved1)
{
	uint hash = 2166136261U ^ inum;
	uint words[] = { magic, version, flags, domain, policy, exec_profile,
			 generation, incarnation, reserved0, reserved1 };

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
				  ip->vfs_flags, ip->vfs_domain,
				  ip->vfs_policy, ip->vfs_exec_profile,
				  ip->vfs_policy_generation,
				  ip->vfs_incarnation, ip->vfs_reserved0,
				  ip->vfs_reserved1);
}

void vfs_cred_kernel(struct vfs_cred *cred)
{
	if (cred == 0)
		return;
	cred->domain = VFS_DOMAIN_PUBLIC;
	cred->capabilities = ~0ULL;
	cred->kernel = 1;
}

void vfs_cred_from_proc(const struct proc *p, struct vfs_cred *cred)
{
	if (cred == 0)
		return;
	cred->domain = p ? p->vfs_domain : VFS_DOMAIN_PUBLIC;
	cred->capabilities = p ? p->vfs_effective_caps : 0;
	cred->kernel = 0;
}

uint vfs_cred_lookup_policy(const struct vfs_cred *cred)
{
	if (cred != 0 && cred->domain == VFS_DOMAIN_WORKFLOW)
		return VFS_POLICY_WORKFLOW;
	return VFS_POLICY_PUBLIC;
}

void vfs_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	p->vfs_domain = VFS_DOMAIN_PUBLIC;
	p->vfs_effective_caps = 0;
	p->vfs_inheritable_caps = 0;
	p->vfs_pending_domain = VFS_DOMAIN_PUBLIC;
	p->vfs_pending_caps = 0;
	p->vfs_pending_exec_dev = 0;
	p->vfs_pending_exec_inum = 0;
	p->vfs_pending_exec_incarnation = 0;
	p->vfs_bound_exec_dev = 0;
	p->vfs_bound_exec_inum = 0;
	p->vfs_bound_exec_incarnation = 0;
}

void vfs_proc_fork(const struct proc *parent, struct proc *child,
		   int retain_effective)
{
	if (child == 0)
		return;
	if (parent == 0) {
		vfs_proc_reset(child);
		return;
	}
	vfs_proc_reset(child);
	if (!retain_effective)
		return;
	child->vfs_domain = parent->vfs_domain;
	child->vfs_effective_caps = parent->vfs_effective_caps;
	child->vfs_inheritable_caps = parent->vfs_inheritable_caps;
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
	    parent->vfs_domain != VFS_DOMAIN_WORKFLOW ||
	    !vfs_inode_label_valid(image) ||
	    image->vfs_policy != VFS_POLICY_WORKFLOW ||
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
	child->vfs_pending_domain = parent->vfs_domain;
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
		vfs_proc_reset(p);
		return;
	}
	ceiling = vfs_exec_profile_capabilities(image->vfs_exec_profile);
	if (!running) {
		uint required = EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE |
				EXEC_FLAG_BOOTSTRAP | EXEC_FLAG_DOMAIN_SAFE;

		if (!vfs_image_domain_safe(image) ||
		    (image->exec_flags & required) != required) {
			vfs_proc_reset(p);
			return;
		}
		p->vfs_domain = VFS_DOMAIN_WORKFLOW;
		p->vfs_effective_caps = ceiling;
		p->vfs_inheritable_caps = ceiling;
		vfs_proc_bind_image(p, image);
		return;
	}
	if (p->vfs_pending_exec_inum != 0) {
		uint pending_domain = p->vfs_pending_domain;
		uint64 pending_caps = p->vfs_pending_caps;
		int matches = p->vfs_pending_exec_dev == image->exec_dev &&
			      p->vfs_pending_exec_inum == image->exec_inum &&
			      p->vfs_pending_exec_incarnation ==
				      image->vfs_exec_incarnation;

		vfs_proc_reset(p);
		if (!matches || !vfs_image_domain_safe(image))
			return;
		p->vfs_domain = pending_domain;
		p->vfs_effective_caps = pending_caps & ceiling;
		p->vfs_inheritable_caps = p->vfs_effective_caps;
		if (p->vfs_effective_caps == 0) {
			vfs_proc_reset(p);
			return;
		}
		vfs_proc_bind_image(p, image);
		return;
	}
	if (p->is_agent) {
		if (!vfs_agent_image_allowed(p, image)) {
			vfs_proc_reset(p);
			return;
		}
		p->vfs_effective_caps = p->vfs_inheritable_caps & ceiling;
		p->vfs_inheritable_caps &= ceiling;
		if (p->vfs_effective_caps == 0) {
			vfs_proc_reset(p);
			return;
		}
		vfs_proc_bind_image(p, image);
		return;
	}
	if (p->vfs_domain == VFS_DOMAIN_PUBLIC ||
	    !vfs_image_domain_safe(image) || !vfs_proc_image_bound(p, image)) {
		vfs_proc_reset(p);
		return;
	}
	p->vfs_effective_caps = p->vfs_inheritable_caps & ceiling;
	p->vfs_inheritable_caps &= ceiling;
	if (p->vfs_effective_caps == 0)
		vfs_proc_reset(p);
}

static int vfs_label_shape_valid(struct inode *ip)
{
	uint flag;

	if (ip->vfs_magic != VFS_LABEL_MAGIC ||
	    ip->vfs_version != VFS_LABEL_VERSION ||
	    ip->vfs_policy_generation != VFS_POLICY_GENERATION ||
	    ip->vfs_incarnation == 0 || ip->vfs_reserved0 != 0 ||
	    ip->vfs_reserved1 != 0 ||
	    (ip->vfs_flags & ~VFS_LABEL_F_KNOWN) != 0 ||
	    ip->vfs_checksum != vfs_inode_checksum(ip))
		return 0;
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
		       ip->vfs_domain == VFS_DOMAIN_PUBLIC &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	case VFS_POLICY_WORKFLOW:
		return flag == VFS_LABEL_F_PROTECTED &&
		       ip->vfs_domain == VFS_DOMAIN_WORKFLOW;
	case VFS_POLICY_KERNEL_PRIVATE:
		return flag == VFS_LABEL_F_KERNEL_PRIVATE &&
		       ip->vfs_domain == VFS_DOMAIN_PUBLIC &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	case VFS_POLICY_ROOT:
		return flag == VFS_LABEL_F_ROOT &&
		       ip->vfs_domain == VFS_DOMAIN_PUBLIC &&
		       ip->type == T_DIR &&
		       ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE;
	case VFS_POLICY_FREE:
		return flag == VFS_LABEL_F_FREE &&
		       ip->vfs_domain == VFS_DOMAIN_PUBLIC &&
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

	if (cred == 0 || !vfs_inode_label_valid(ip))
		return 0;
	if (cred->kernel)
		return 1;
	if (ip->vfs_policy == VFS_POLICY_FREE)
		return 0;
	if (ip->vfs_policy == VFS_POLICY_KERNEL_PRIVATE)
		return 0;
	if (ip->vfs_policy == VFS_POLICY_ROOT) {
		if (op == VFS_OP_LOOKUP || op == VFS_OP_READ ||
		    op == VFS_OP_EXEC)
			return 1;
		if (op == VFS_OP_CREATE || op == VFS_OP_WRITE ||
		    op == VFS_OP_TRUNCATE || op == VFS_OP_DELETE) {
			if (cred->domain == VFS_DOMAIN_PUBLIC)
				return 1;
			return cred->domain == VFS_DOMAIN_WORKFLOW &&
			       (cred->capabilities & VFS_CAP_ARTIFACT_WRITE) != 0;
		}
		return 0;
	}
	if (op == VFS_OP_EXEC)
		return ip->type == T_FILE;
	if (ip->vfs_policy == VFS_POLICY_PUBLIC)
		return cred->domain == VFS_DOMAIN_PUBLIC;
	if (ip->vfs_policy != VFS_POLICY_WORKFLOW ||
	    cred->domain != ip->vfs_domain)
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
	if (cred == 0)
		return 0;
	if (cred->kernel)
		return VFS_POLICY_KERNEL_PRIVATE;
	if (cred->domain == VFS_DOMAIN_PUBLIC)
		return VFS_POLICY_PUBLIC;
	if (cred->domain == VFS_DOMAIN_WORKFLOW &&
	    (cred->capabilities & VFS_CAP_ARTIFACT_WRITE) != 0)
		return VFS_POLICY_WORKFLOW;
	return 0;
}

static int vfs_policy_subject_allowed(const struct vfs_cred *cred,
				      uint policy)
{
	if (cred == 0)
		return 0;
	if (policy == VFS_POLICY_KERNEL_PRIVATE)
		return cred->kernel;
	if (policy == VFS_POLICY_PUBLIC)
		return !cred->kernel && cred->domain == VFS_DOMAIN_PUBLIC;
	if (policy == VFS_POLICY_WORKFLOW)
		return cred->kernel ||
		       (cred->domain == VFS_DOMAIN_WORKFLOW &&
			(cred->capabilities & VFS_CAP_ARTIFACT_WRITE) != 0);
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
		return cred->domain == VFS_DOMAIN_PUBLIC;
	if (policy != VFS_POLICY_WORKFLOW ||
	    cred->domain != VFS_DOMAIN_WORKFLOW)
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
	    ip->vfs_incarnation == 0)
		return -1;
	ip->vfs_magic = VFS_LABEL_MAGIC;
	ip->vfs_version = VFS_LABEL_VERSION;
	ip->vfs_policy_generation = VFS_POLICY_GENERATION;
	ip->vfs_exec_profile = VFS_EXEC_PROFILE_NONE;
	ip->vfs_reserved0 = 0;
	ip->vfs_reserved1 = 0;
	if (policy == VFS_POLICY_PUBLIC) {
		ip->vfs_flags = VFS_LABEL_F_PUBLIC;
		ip->vfs_domain = VFS_DOMAIN_PUBLIC;
	} else if (policy == VFS_POLICY_WORKFLOW) {
		ip->vfs_flags = VFS_LABEL_F_PROTECTED;
		ip->vfs_domain = VFS_DOMAIN_WORKFLOW;
	} else {
		ip->vfs_flags = VFS_LABEL_F_KERNEL_PRIVATE;
		ip->vfs_domain = VFS_DOMAIN_PUBLIC;
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
		ip->vfs_domain == VFS_DOMAIN_WORKFLOW);
}

void vfs_inode_mark_free(struct inode *ip)
{
	if (ip == 0)
		return;
	ip->vfs_magic = VFS_LABEL_MAGIC;
	ip->vfs_version = VFS_LABEL_VERSION;
	ip->vfs_flags = VFS_LABEL_F_FREE;
	ip->vfs_domain = VFS_DOMAIN_PUBLIC;
	ip->vfs_policy = VFS_POLICY_FREE;
	ip->vfs_exec_profile = VFS_EXEC_PROFILE_NONE;
	ip->vfs_policy_generation = VFS_POLICY_GENERATION;
	ip->vfs_reserved0 = 0;
	ip->vfs_reserved1 = 0;
	ip->vfs_checksum = vfs_inode_checksum(ip);
}
