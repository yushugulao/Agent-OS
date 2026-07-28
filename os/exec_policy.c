#include "exec_policy.h"
#include "const.h"
#include "file.h"
#include "fs.h"
#include "proc.h"
#include "../user/include/exec_policy_manifest.h"

static int exec_layout_valid(uint dev, uint inum, uint layout_version,
			     uint rw_offset)
{
	return dev != 0 && inum != 0 &&
	       layout_version == EXEC_LAYOUT_VERSION &&
	       rw_offset >= PAGE_SIZE && (rw_offset % PAGE_SIZE) == 0;
}

static int exec_policy_valid(uint dev, uint inum, uint flags,
			     uint generation, uint role_mask,
			     uint layout_version, uint rw_offset)
{
	uint required = EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE |
			EXEC_FLAG_DOMAIN_SAFE;

	return exec_layout_valid(dev, inum, layout_version, rw_offset) &&
	       generation == EXEC_MANIFEST_VERSION &&
	       (flags & ~EXEC_FLAG_KNOWN) == 0 &&
	       (flags & required) == required &&
	       (role_mask & ~EXEC_MANIFEST_ROLE_ALL) == 0 &&
	       ((flags & EXEC_FLAG_BOOTSTRAP) == 0 || role_mask != 0);
}

static int exec_policy_role_allowed(uint dev, uint inum, uint flags,
				    uint generation, uint role_mask,
				    uint layout_version, uint rw_offset,
				    int role)
{
	if (role < 1 || role >= 32 ||
	    !exec_policy_valid(dev, inum, flags, generation, role_mask,
			       layout_version, rw_offset))
		return 0;
	return (role_mask & EXEC_ROLE_BIT(role)) != 0;
}

_Static_assert(EXEC_MANIFEST_F_TRUSTED == EXEC_FLAG_TRUSTED,
	       "manifest trusted flag mismatch");
_Static_assert(EXEC_MANIFEST_F_IMMUTABLE == EXEC_FLAG_IMMUTABLE,
	       "manifest immutable flag mismatch");
_Static_assert(EXEC_MANIFEST_F_BOOTSTRAP == EXEC_FLAG_BOOTSTRAP,
	       "manifest bootstrap flag mismatch");
_Static_assert(EXEC_MANIFEST_F_DOMAIN_SAFE == EXEC_FLAG_DOMAIN_SAFE,
	       "manifest domain-safe flag mismatch");

int exec_policy_inode_mutable(struct inode *ip)
{
	if (ip == 0)
		return 0;
	if (ivalid(ip) < 0)
		return 0;
	return ip->type != T_FILE || (ip->exec_flags & EXEC_FLAG_IMMUTABLE) == 0;
}

int exec_policy_inode_trusted(struct inode *ip)
{
	if (!exec_policy_inode_layout_valid(ip))
		return 0;
	return exec_policy_valid(ip->dev, ip->inum, ip->exec_flags,
				 ip->exec_generation, ip->exec_role_mask,
				 ip->exec_layout_version,
				 ip->exec_rw_offset);
}

int exec_policy_inode_layout_valid(struct inode *ip)
{
	if (ip == 0)
		return 0;
	if (ivalid(ip) < 0)
		return 0;
	return ip->type == T_FILE && ip->size > ip->exec_rw_offset &&
	       exec_layout_valid(ip->dev, ip->inum,
			 ip->exec_layout_version, ip->exec_rw_offset);
}

int exec_policy_inode_allows_role(struct inode *ip, int role)
{
	if (!exec_policy_inode_trusted(ip))
		return 0;
	return exec_policy_role_allowed(ip->dev, ip->inum, ip->exec_flags,
					ip->exec_generation,
					ip->exec_role_mask,
					ip->exec_layout_version,
					ip->exec_rw_offset, role);
}

int exec_policy_process_bootstrap(const struct proc *p)
{
	if (p == 0 ||
	    !exec_policy_valid(p->exec_dev, p->exec_inum, p->exec_flags,
			       p->exec_generation, p->exec_role_mask,
			       p->exec_layout_version,
			       p->exec_rw_offset))
		return 0;
	return (p->exec_flags & EXEC_FLAG_BOOTSTRAP) != 0;
}

int exec_policy_process_allows_role(const struct proc *p, int role)
{
	if (p == 0)
		return 0;
	return exec_policy_role_allowed(p->exec_dev, p->exec_inum,
					p->exec_flags, p->exec_generation,
					p->exec_role_mask,
					p->exec_layout_version,
					p->exec_rw_offset, role);
}
