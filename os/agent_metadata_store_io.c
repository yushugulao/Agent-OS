#include "agent_internal.h"
#include "agent_file_name_policy.h"
#include "agent_metadata_store_io.h"
#include "defs.h"
#include "fs.h"
#include "riscv.h"
#include "vfs_security.h"
static int agent_meta_store_io_busy;
void
agent_meta_store_io_init(void)
{
	agent_meta_store_io_busy = 0;
}

int
agent_meta_store_io_enter(void)
{
	int enabled = intr_save();
	int entered = 0;

	if (!agent_meta_store_io_busy) {
		agent_meta_store_io_busy = 1;
		entered = 1;
	}
	intr_restore(enabled);
	return entered;
}

void
agent_meta_store_io_leave(void)
{
	int enabled = intr_save();

	if (!agent_meta_store_io_busy)
		panic("Agent metadata store lock invariant");
	agent_meta_store_io_busy = 0;
	intr_restore(enabled);
}

int
agent_meta_store_io_owned(void)
{
	int enabled = intr_save();
	int owned = agent_meta_store_io_busy;

	intr_restore(enabled);
	return owned;
}

char *
agent_meta_store_io_name(int bank)
{
	if (bank == 0)
		return AGENT_META_STORE_NAME_0;
	if (bank == 1)
		return AGENT_META_STORE_NAME_1;
	return 0;
}

struct inode *
agent_meta_store_io_lookup_bank(char *name, int create, int *status_out)
{
	struct inode *ip;
	struct vfs_cred kernel_cred;
	int status;
	int result;

	if (status_out == 0)
		return 0;
	*status_out = FS_LOOKUP_ERROR;
	ip = namei_scope_status(name, VFS_POLICY_KERNEL_PRIVATE,
				VFS_SCOPE_NONE, &status);
	if (ip != 0) {
		result = ivalid(ip);
		if (result < 0) {
			*status_out = result;
			iput(ip);
			return 0;
		}
		if (ip->type == T_FILE && vfs_inode_label_valid(ip) &&
		    ip->vfs_policy == VFS_POLICY_KERNEL_PRIVATE) {
			*status_out = FS_LOOKUP_FOUND;
			return ip;
		}
		iput(ip);
		return 0;
	}
	*status_out = status;
	if (!create || status != FS_LOOKUP_ABSENT)
		return 0;
	vfs_cred_kernel(&kernel_cred);
	ip = fs_create(name, T_FILE, 0, &kernel_cred,
		       VFS_POLICY_KERNEL_PRIVATE, &status);
	if (ip == 0) {
		*status_out = status;
		return 0;
	}
	result = ivalid(ip);
	if (result < 0) {
		*status_out = result;
		iput(ip);
		return 0;
	}
	*status_out = FS_LOOKUP_FOUND;
	return ip;
}
