#include "open_file_io_lease.h"

#include "agent.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "proc.h"
#include "resource_controller.h"
#include "syscall_ids.h"
#include "workflow_lifecycle.h"

#define OPEN_FILE_IO_READ  (1U << 0)
#define OPEN_FILE_IO_WRITE (1U << 1)
#define OPEN_FILE_IO_CACHE_CAP 64U

/*
 * 缓存只加速鉴权，不承载权限。获取成功后，内核将上下文复制到 syscall
 * 令牌，避免 syscall 休眠期间的缓存碰撞改变其授权对象。
 */
struct open_file_io_grant {
	struct file *file;
	struct inode *inode;
	struct proc *subject;
	struct resource_account_handle account;
	struct workflow_lifecycle_key lifecycle;
	struct vfs_cred cred;
	uint64 edit_authority_generation;
	uint64 edit_deadline_tick;
	uint inode_incarnation;
	uint inode_checksum;
	uint inode_policy_generation;
	uint inode_exec_size;
	uint inode_exec_flags;
	uint inode_exec_generation;
	uint inode_exec_role_mask;
	uint inode_exec_layout_version;
	uint inode_exec_rw_offset;
	uint inode_exec_profile;
	uint allowed;
	uchar valid;
};

struct open_file_io_state {
	struct open_file_io_grant grants[OPEN_FILE_IO_CACHE_CAP];
	struct open_file_io_lease_stats stats;
};

static struct open_file_io_state open_file_io_state;

_Static_assert((OPEN_FILE_IO_CACHE_CAP & (OPEN_FILE_IO_CACHE_CAP - 1)) == 0,
	       "open-file grant cache must be a power of two");

static uint open_file_io_operation_mask(enum vfs_operation operation)
{
	if (operation == VFS_OP_READ)
		return OPEN_FILE_IO_READ;
	if (operation == VFS_OP_WRITE)
		return OPEN_FILE_IO_WRITE;
	return 0;
}

static int open_file_io_cred_equal(const struct vfs_cred *left,
				   const struct vfs_cred *right)
{
	return left->scope_id == right->scope_id &&
	       left->storage_principal_id == right->storage_principal_id &&
	       left->capabilities == right->capabilities &&
	       left->kernel == right->kernel;
}

static int
open_file_io_lifecycle_live(struct workflow_lifecycle_key lifecycle)
{
	if (!workflow_lifecycle_key_valid(lifecycle))
		return lifecycle.id == WORKFLOW_LIFECYCLE_ID_NONE &&
		       lifecycle.generation == 0;
	return workflow_lifecycle_active(lifecycle);
}

static int open_file_io_inode_matches(
	uint incarnation, uint checksum, uint policy_generation,
	uint exec_size, uint exec_flags, uint exec_generation,
	uint exec_role_mask, uint exec_layout_version, uint exec_rw_offset,
	uint exec_profile, const struct inode *inode)
{
	return inode != 0 && inode->valid && inode->type == T_FILE &&
	       incarnation == inode->vfs_incarnation &&
	       checksum == inode->vfs_checksum &&
	       policy_generation == inode->vfs_policy_generation &&
	       (exec_profile == VFS_EXEC_PROFILE_NONE ||
		exec_size == inode->size) &&
	       exec_flags == inode->exec_flags &&
	       exec_generation == inode->exec_generation &&
	       exec_role_mask == inode->exec_role_mask &&
	       exec_layout_version == inode->exec_layout_version &&
	       exec_rw_offset == inode->exec_rw_offset &&
	       exec_profile == inode->vfs_exec_profile;
}

static int open_file_io_file_matches(const struct file *file,
				     const struct inode *inode,
				     enum vfs_operation operation)
{
	return file != 0 && file->ref >= 1 && file->type == FD_INODE &&
	       file->ip == inode &&
	       (operation != VFS_OP_READ || file->readable) &&
	       (operation != VFS_OP_WRITE || file->writable);
}

static int open_file_io_subject_matches(
	struct proc *proc, struct resource_account_handle account,
	struct workflow_lifecycle_key lifecycle, const struct vfs_cred *cred)
{
	struct workflow_lifecycle_key current_lifecycle;
	struct vfs_cred current_cred;

	if (proc == 0 || proc->teardown_state != PROC_TEARDOWN_LIVE ||
	    !resource_account_handle_equal(account, proc->resource_account) ||
	    !resource_account_active(account))
		return 0;
	current_lifecycle = vfs_proc_lifecycle(proc);
	if (!workflow_lifecycle_key_equal(lifecycle, current_lifecycle) ||
	    !open_file_io_lifecycle_live(current_lifecycle))
		return 0;
	vfs_cred_from_proc(proc, &current_cred);
	return open_file_io_cred_equal(cred, &current_cred);
}

static int open_file_io_edit_matches(
	struct inode *inode, const struct vfs_cred *cred,
	enum vfs_operation operation, uint64 authority_generation,
	uint64 deadline_tick)
{
	uint64 current_generation = 0;
	uint64 current_deadline = 0;

	if (operation != VFS_OP_WRITE ||
	    cred->scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return authority_generation == 0 && deadline_tick == 0;
	return agent_edit_write_lease_snapshot(
		       inode, &current_generation, &current_deadline) &&
	       authority_generation == current_generation &&
	       deadline_tick == current_deadline;
}

static int open_file_io_grant_matches(
	const struct open_file_io_grant *grant, struct file *file,
	struct proc *proc, enum vfs_operation operation)
{
	uint mask = open_file_io_operation_mask(operation);

	return grant->valid && mask != 0 && grant->file == file &&
	       grant->subject == proc && (grant->allowed & mask) != 0 &&
	       open_file_io_file_matches(file, grant->inode, operation) &&
	       open_file_io_subject_matches(proc, grant->account,
					    grant->lifecycle, &grant->cred) &&
	       open_file_io_inode_matches(grant->inode_incarnation,
		grant->inode_checksum, grant->inode_policy_generation,
		grant->inode_exec_size, grant->inode_exec_flags,
		grant->inode_exec_generation, grant->inode_exec_role_mask,
		grant->inode_exec_layout_version, grant->inode_exec_rw_offset,
		grant->inode_exec_profile, grant->inode) &&
	       open_file_io_edit_matches(
		       grant->inode, &grant->cred, operation,
		       grant->edit_authority_generation,
		       grant->edit_deadline_tick);
}

static int open_file_io_grant_same_key(
	const struct open_file_io_grant *grant, const struct file *file,
	const struct proc *proc, enum vfs_operation operation)
{
	return grant->valid && grant->file == file && grant->subject == proc &&
	       (grant->allowed & open_file_io_operation_mask(operation)) != 0;
}

static void open_file_io_grant_capture(
	struct open_file_io_grant *grant, struct file *file,
	struct proc *proc, enum vfs_operation operation,
	uint64 edit_authority_generation, uint64 edit_deadline_tick)
{
	memset(grant, 0, sizeof(*grant));
	grant->file = file;
	grant->inode = file->ip;
	grant->subject = proc;
	grant->account = proc->resource_account;
	grant->lifecycle = vfs_proc_lifecycle(proc);
	vfs_cred_from_proc(proc, &grant->cred);
	grant->edit_authority_generation = edit_authority_generation;
	grant->edit_deadline_tick = edit_deadline_tick;
	grant->inode_incarnation = file->ip->vfs_incarnation;
	grant->inode_checksum = file->ip->vfs_checksum;
	grant->inode_policy_generation = file->ip->vfs_policy_generation;
	grant->inode_exec_size = file->ip->vfs_exec_profile ==
		VFS_EXEC_PROFILE_NONE ? 0 : file->ip->size;
	grant->inode_exec_flags = file->ip->exec_flags;
	grant->inode_exec_generation = file->ip->exec_generation;
	grant->inode_exec_role_mask = file->ip->exec_role_mask;
	grant->inode_exec_layout_version = file->ip->exec_layout_version;
	grant->inode_exec_rw_offset = file->ip->exec_rw_offset;
	grant->inode_exec_profile = file->ip->vfs_exec_profile;
	grant->allowed = open_file_io_operation_mask(operation);
	grant->valid = 1;
}

static uint open_file_io_cache_slot(struct file *file, struct proc *proc,
				    enum vfs_operation operation)
{
	uint64 key = ((uint64)file >> 4) ^ ((uint64)proc >> 4);

	key ^= (uint64)open_file_io_operation_mask(operation) *
	       0x9e3779b97f4a7c15ULL;
	key ^= key >> 32;
	return (uint)key & (OPEN_FILE_IO_CACHE_CAP - 1);
}

static int open_file_io_syscall_context(enum vfs_operation operation,
					struct proc **proc_out,
					struct thread **thread_out)
{
	struct proc *proc = curr_proc();
	struct thread *thread = curr_thread();

	if (open_file_io_operation_mask(operation) == 0 || proc == 0 ||
	    thread == 0 || thread->process != proc ||
	    thread->identity_generation == 0 ||
	    thread->kernel_work_depth == 0 ||
	    thread->kernel_work_target_syscall_id !=
		    (operation == VFS_OP_READ ? SYS_read : SYS_write))
		return 0;
	*proc_out = proc;
	*thread_out = thread;
	return 1;
}

static void open_file_io_token_issue(
	struct open_file_io_token *token,
	const struct open_file_io_grant *grant,
	const struct vfs_cred *authorized_cred,
	enum vfs_operation operation, const struct thread *thread)
{
	memset(token, 0, sizeof(*token));
	token->file = grant->file;
	token->inode = grant->inode;
	token->subject = grant->subject;
	token->account = grant->account;
	token->lifecycle = grant->lifecycle;
	token->cred = authorized_cred;
	token->edit_authority_generation = grant->edit_authority_generation;
	token->edit_deadline_tick = grant->edit_deadline_tick;
	token->thread_generation = thread->identity_generation;
	token->syscall_generation = thread->kernel_work_generation;
	token->inode_incarnation = grant->inode_incarnation;
	token->inode_checksum = grant->inode_checksum;
	token->inode_policy_generation = grant->inode_policy_generation;
	token->inode_exec_size = grant->inode_exec_size;
	token->inode_exec_flags = grant->inode_exec_flags;
	token->inode_exec_generation = grant->inode_exec_generation;
	token->inode_exec_role_mask = grant->inode_exec_role_mask;
	token->inode_exec_layout_version = grant->inode_exec_layout_version;
	token->inode_exec_rw_offset = grant->inode_exec_rw_offset;
	token->inode_exec_profile = grant->inode_exec_profile;
	token->operation = operation;
	token->valid = 1;
}

void open_file_io_lease_seed_authorized(
	struct file *file, enum vfs_operation operation,
	const struct vfs_cred *authorized_cred)
{
	struct open_file_io_grant candidate;
	struct open_file_io_grant *grant;
	struct proc *proc = curr_proc();
	struct vfs_cred current_cred;
	uint64 edit_generation = 0;
	uint64 edit_deadline = 0;
	uint slot;
	int enabled;

	if (authorized_cred == 0 || proc == 0 || file == 0 || file->ip == 0 ||
	    open_file_io_operation_mask(operation) == 0)
		return;
	vfs_cred_from_proc(proc, &current_cred);
	if (!open_file_io_cred_equal(authorized_cred, &current_cred))
		return;
	if (operation == VFS_OP_WRITE &&
	    (!exec_policy_inode_mutable(file->ip) ||
	     !agent_edit_write_lease_allowed(
		     file->ip, &edit_generation, &edit_deadline)))
		return;
	open_file_io_grant_capture(&candidate, file, proc, operation,
				   edit_generation, edit_deadline);
	enabled = intr_save();
	if (!open_file_io_grant_matches(&candidate, file, proc, operation)) {
		intr_restore(enabled);
		return;
	}
	slot = open_file_io_cache_slot(file, proc, operation);
	grant = &open_file_io_state.grants[slot];
	*grant = candidate;
	intr_restore(enabled);
}

int open_file_io_lease_acquire(struct file *file,
			       enum vfs_operation operation,
			       struct open_file_io_token *token,
			       struct vfs_cred *authorized_cred)
{
	struct open_file_io_grant candidate;
	struct open_file_io_grant *grant;
	struct proc *proc;
	struct thread *thread;
	uint64 edit_generation = 0;
	uint64 edit_deadline = 0;
	uint slot;
	int authorized;
	int enabled;

	if (token == 0 || authorized_cred == 0)
		return -1;
	memset(token, 0, sizeof(*token));
	memset(authorized_cred, 0, sizeof(*authorized_cred));
	if (!open_file_io_syscall_context(operation, &proc, &thread) ||
	    file == 0 || file->ip == 0)
		return -1;
	slot = open_file_io_cache_slot(file, proc, operation);
	enabled = intr_save();
	grant = &open_file_io_state.grants[slot];
	if (open_file_io_grant_matches(grant, file, proc, operation)) {
		open_file_io_state.stats.lease_hit++;
		*authorized_cred = grant->cred;
		open_file_io_token_issue(
			token, grant, authorized_cred, operation, thread);
		intr_restore(enabled);
		return 0;
	}
	if (open_file_io_grant_same_key(grant, file, proc, operation))
		open_file_io_state.stats.revalidation++;
	open_file_io_state.stats.full_auth++;
	open_file_io_grant_capture(&candidate, file, proc, operation, 0, 0);
	intr_restore(enabled);

	authorized = vfs_inode_authorize(candidate.inode, &candidate.cred,
					 operation);
	if (authorized && operation == VFS_OP_WRITE)
		authorized = exec_policy_inode_mutable(candidate.inode) &&
			agent_edit_write_lease_allowed(
				candidate.inode, &edit_generation,
				&edit_deadline);
	candidate.edit_authority_generation = edit_generation;
	candidate.edit_deadline_tick = edit_deadline;

	enabled = intr_save();
	if (!authorized ||
	    !open_file_io_grant_matches(&candidate, file, proc, operation)) {
		intr_restore(enabled);
		return -1;
	}
	grant = &open_file_io_state.grants[slot];
	*grant = candidate;
	*authorized_cred = candidate.cred;
	open_file_io_token_issue(
		token, &candidate, authorized_cred, operation, thread);
	intr_restore(enabled);
	return 0;
}

int open_file_io_token_validate(const struct open_file_io_token *token,
				struct inode *inode,
				enum vfs_operation operation)
{
	struct proc *proc;
	struct thread *thread;
	int valid;
	int enabled;

	if (token == 0 || !token->valid || token->cred == 0 || inode == 0 ||
	    token->operation != operation ||
	    !open_file_io_syscall_context(operation, &proc, &thread))
		return 0;
	enabled = intr_save();
	valid = token->subject == proc && token->inode == inode &&
		thread->identity_generation == token->thread_generation &&
		thread->kernel_work_generation == token->syscall_generation &&
		open_file_io_file_matches(token->file, inode, operation) &&
		open_file_io_subject_matches(proc, token->account,
					     token->lifecycle, token->cred) &&
		open_file_io_inode_matches(token->inode_incarnation,
			token->inode_checksum, token->inode_policy_generation,
			token->inode_exec_size, token->inode_exec_flags,
			token->inode_exec_generation,
			token->inode_exec_role_mask,
			token->inode_exec_layout_version,
			token->inode_exec_rw_offset,
			token->inode_exec_profile, inode) &&
		open_file_io_edit_matches(
			inode, token->cred, operation,
			token->edit_authority_generation,
			token->edit_deadline_tick);
	intr_restore(enabled);
	return valid;
}

void open_file_io_token_end(struct open_file_io_token *token)
{
	if (token != 0)
		memset(token, 0, sizeof(*token));
}

void open_file_io_lease_stats_snapshot(struct open_file_io_lease_stats *out)
{
	int enabled;

	if (out == 0)
		return;
	enabled = intr_save();
	memmove(out, &open_file_io_state.stats, sizeof(*out));
	intr_restore(enabled);
}
