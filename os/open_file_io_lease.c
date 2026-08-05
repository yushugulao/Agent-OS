#include "open_file_io_lease.h"

#include "agent.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "proc.h"
#include "resource_controller.h"
#include "syscall_ids.h"
#include "timer.h"
#include "workflow_lifecycle.h"

#define OPEN_FILE_IO_READ  (1U << 0)
#define OPEN_FILE_IO_WRITE (1U << 1)
#define OPEN_FILE_IO_CACHE_CAP 64U

struct open_file_io_grant {
	struct file *file;
	struct inode *inode;
	struct proc *subject;
	struct resource_account_handle account;
	struct workflow_lifecycle_key lifecycle;
	struct vfs_cred cred;
	uint64 edit_generation;
	uint64 edit_deadline_tick;
	uint64 sequence;
	uint file_generation;
	uint inode_incarnation;
	uint inode_checksum;
	uint inode_size;
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
	uint file_generations[FILEPOOLSIZE];
	struct open_file_io_lease_stats stats;
	uint64 edit_generation;
	uint64 grant_sequence;
	uint64 token_secret;
};

static struct open_file_io_state open_file_io_state;

_Static_assert((OPEN_FILE_IO_CACHE_CAP & (OPEN_FILE_IO_CACHE_CAP - 1)) == 0,
	       "open-file grant cache must be a power of two");
_Static_assert(OPEN_FILE_IO_CACHE_CAP < FILEPOOLSIZE,
	       "open-file grants must remain a compact sidecar");
_Static_assert(sizeof(struct open_file_io_token) == 4 * sizeof(uint64),
	       "open-file token must remain stack-compact");

static uint64 open_file_io_rotl(uint64 value, uint shift)
{
	return (value << shift) | (value >> (64 - shift));
}

static uint64 open_file_io_now(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static uint64 open_file_io_next64(uint64 *counter)
{
	(*counter)++;
	if (*counter == 0) {
		memset(open_file_io_state.grants, 0,
		       sizeof(open_file_io_state.grants));
		*counter = 1;
	}
	return *counter;
}

static uint open_file_io_next32(uint *counter)
{
	(*counter)++;
	if (*counter == 0) {
		memset(open_file_io_state.grants, 0,
		       sizeof(open_file_io_state.grants));
		*counter = 1;
	}
	return *counter;
}

static void open_file_io_state_init_locked(void)
{
	struct open_file_io_state *state = &open_file_io_state;

	if (state->token_secret != 0)
		return;
	state->token_secret = get_cycle() ^ (uint64)state ^
		0xa0761d6478bd642fULL;
	if (state->token_secret == 0)
		state->token_secret = 0xe7037ed1a0b428dbULL;
	state->edit_generation = 1;
	state->grant_sequence = 1;
}

static int open_file_io_slot(const struct file *file, uint *slot)
{
	uint64 base = (uint64)&filepool[0];
	uint64 address = (uint64)file;
	uint64 offset;

	if (file == 0 || slot == 0 || address < base ||
	    address >= (uint64)&filepool[FILEPOOLSIZE])
		return -1;
	offset = address - base;
	if (offset % sizeof(struct file) != 0)
		return -1;
	*slot = (uint)(offset / sizeof(struct file));
	return 0;
}

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

static int open_file_io_lifecycle_live(
	struct workflow_lifecycle_key lifecycle)
{
	if (!workflow_lifecycle_key_valid(lifecycle))
		return lifecycle.id == WORKFLOW_LIFECYCLE_ID_NONE &&
		       lifecycle.generation == 0;
	return workflow_lifecycle_active(lifecycle);
}

static int open_file_io_inode_matches(
	const struct open_file_io_grant *grant, const struct inode *inode)
{
	return inode != 0 && grant->inode == inode && inode->valid &&
	       inode->type == T_FILE &&
	       grant->inode_incarnation == inode->vfs_incarnation &&
	       grant->inode_checksum == inode->vfs_checksum &&
	       (grant->inode_exec_profile == VFS_EXEC_PROFILE_NONE ||
		grant->inode_size == inode->size) &&
	       grant->inode_exec_flags == inode->exec_flags &&
	       grant->inode_exec_generation == inode->exec_generation &&
	       grant->inode_exec_role_mask == inode->exec_role_mask &&
	       grant->inode_exec_layout_version == inode->exec_layout_version &&
	       grant->inode_exec_rw_offset == inode->exec_rw_offset &&
	       grant->inode_exec_profile == inode->vfs_exec_profile;
}

static int open_file_io_grant_matches_locked(
	const struct open_file_io_grant *grant, struct file *file,
	struct proc *proc, uint slot, enum vfs_operation operation)
{
	struct workflow_lifecycle_key lifecycle = vfs_proc_lifecycle(proc);
	struct vfs_cred cred;
	uint operation_mask = open_file_io_operation_mask(operation);

	if (!grant->valid || operation_mask == 0 || grant->file != file ||
	    grant->subject != proc || grant->file_generation !=
		    open_file_io_state.file_generations[slot] ||
	    file->ref < 1 || file->type != FD_INODE ||
	    file->ip != grant->inode ||
	    (operation == VFS_OP_READ && !file->readable) ||
	    (operation == VFS_OP_WRITE && !file->writable) ||
	    (grant->allowed & operation_mask) == 0 ||
	    proc->teardown_state != PROC_TEARDOWN_LIVE ||
	    !resource_account_handle_equal(grant->account,
					   proc->resource_account) ||
	    !resource_account_active(grant->account) ||
	    !workflow_lifecycle_key_equal(grant->lifecycle, lifecycle) ||
	    !open_file_io_lifecycle_live(lifecycle) ||
	    !open_file_io_inode_matches(grant, file->ip))
		return 0;
	vfs_cred_from_proc(proc, &cred);
	if (!open_file_io_cred_equal(&grant->cred, &cred))
		return 0;
	if (operation == VFS_OP_WRITE && cred.scope_id >= VFS_SCOPE_FIRST_DYNAMIC) {
		if (grant->edit_generation != open_file_io_state.edit_generation)
			return 0;
		if (grant->edit_deadline_tick != 0 &&
		    open_file_io_now() >= grant->edit_deadline_tick)
			return 0;
	}
	return 1;
}

static void open_file_io_grant_capture_locked(
	struct open_file_io_grant *grant, struct file *file,
	struct proc *proc, uint slot, enum vfs_operation operation,
	uint64 edit_deadline_tick)
{
	memset(grant, 0, sizeof(*grant));
	grant->file = file;
	grant->inode = file->ip;
	grant->subject = proc;
	grant->account = proc->resource_account;
	grant->lifecycle = vfs_proc_lifecycle(proc);
	vfs_cred_from_proc(proc, &grant->cred);
	grant->edit_generation = open_file_io_state.edit_generation;
	grant->edit_deadline_tick = edit_deadline_tick;
	grant->sequence = open_file_io_next64(
		&open_file_io_state.grant_sequence);
	grant->file_generation = open_file_io_state.file_generations[slot];
	grant->inode_incarnation = file->ip->vfs_incarnation;
	grant->inode_checksum = file->ip->vfs_checksum;
	grant->inode_size = file->ip->size;
	grant->inode_exec_flags = file->ip->exec_flags;
	grant->inode_exec_generation = file->ip->exec_generation;
	grant->inode_exec_role_mask = file->ip->exec_role_mask;
	grant->inode_exec_layout_version = file->ip->exec_layout_version;
	grant->inode_exec_rw_offset = file->ip->exec_rw_offset;
	grant->inode_exec_profile = file->ip->vfs_exec_profile;
	grant->allowed = open_file_io_operation_mask(operation);
	grant->valid = 1;
}

static uint64 open_file_io_token_seal_locked(
	const struct open_file_io_token *token, const struct thread *thread)
{
	uint64 seal = open_file_io_state.token_secret ^ token->opaque[0];

	seal ^= open_file_io_rotl(token->opaque[1], 17);
	seal ^= open_file_io_rotl(token->opaque[2], 39);
	seal ^= open_file_io_rotl((uint64)thread, 7);
	seal ^= open_file_io_rotl(thread->identity_generation, 31);
	seal ^= open_file_io_rotl(thread->kernel_receipt_generation, 47);
	return seal;
}

static void open_file_io_token_issue_locked(
	struct open_file_io_token *token, const struct open_file_io_grant *grant,
	uint cache_slot, enum vfs_operation operation, struct thread *thread)
{
	memset(token, 0, sizeof(*token));
	token->opaque[0] = ((uint64)cache_slot << 32) | (uint)operation;
	token->opaque[1] = grant->sequence;
	token->opaque[2] = (uint64)grant->inode;
	token->opaque[3] = open_file_io_token_seal_locked(token, thread);
}

void open_file_io_lease_file_init(struct file *file)
{
	uint slot;
	int enabled;

	if (open_file_io_slot(file, &slot) < 0)
		panic("open-file I/O init slot");
	enabled = intr_save();
	open_file_io_state_init_locked();
	open_file_io_next32(&open_file_io_state.file_generations[slot]);
	intr_restore(enabled);
}

void open_file_io_lease_file_retire(struct file *file)
{
	uint slot;
	int enabled;

	if (open_file_io_slot(file, &slot) < 0)
		panic("open-file I/O retire slot");
	enabled = intr_save();
	open_file_io_state_init_locked();
	open_file_io_next32(&open_file_io_state.file_generations[slot]);
	intr_restore(enabled);
}

void open_file_io_lease_edit_changed(void)
{
	int enabled = intr_save();

	open_file_io_state_init_locked();
	open_file_io_next64(&open_file_io_state.edit_generation);
	intr_restore(enabled);
}

int open_file_io_lease_acquire(struct file *file,
			       enum vfs_operation operation,
			       struct open_file_io_token *token,
			       struct vfs_cred *authorized_cred)
{
	struct open_file_io_state *state = &open_file_io_state;
	struct open_file_io_grant candidate;
	struct open_file_io_grant *grant;
	struct proc *proc = curr_proc();
	struct thread *thread = curr_thread();
	uint64 edit_deadline_tick;
	uint cache_slot;
	uint file_slot;
	int authorized;
	int enabled;

	if (token == 0 || authorized_cred == 0)
		return -1;
	memset(token, 0, sizeof(*token));
	memset(authorized_cred, 0, sizeof(*authorized_cred));
	if (open_file_io_operation_mask(operation) == 0 || proc == 0 ||
	    thread == 0 || thread->process != proc ||
	    thread->identity_generation == 0 ||
	    thread->kernel_work_depth == 0 ||
	    thread->kernel_work_target_syscall_id !=
		    (operation == VFS_OP_READ ? SYS_read : SYS_write) ||
	    open_file_io_slot(file, &file_slot) < 0)
		return -1;
	cache_slot = (file_slot ^ proc->resource_account.slot) &
		(OPEN_FILE_IO_CACHE_CAP - 1);

	enabled = intr_save();
	open_file_io_state_init_locked();
	grant = &state->grants[cache_slot];
	if (open_file_io_grant_matches_locked(
		grant, file, proc, file_slot, operation)) {
		state->stats.lease_hit++;
		*authorized_cred = grant->cred;
		open_file_io_token_issue_locked(token, grant, cache_slot,
						operation, thread);
		intr_restore(enabled);
		return 0;
	}
	if (grant->valid)
		state->stats.revalidation++;
	state->stats.full_auth++;
	open_file_io_grant_capture_locked(
		&candidate, file, proc, file_slot, operation, 0);
	intr_restore(enabled);

	authorized = vfs_inode_authorize(candidate.inode, &candidate.cred,
					 operation);
	edit_deadline_tick = 0;
	if (authorized && operation == VFS_OP_WRITE)
		authorized = exec_policy_inode_mutable(candidate.inode) &&
			agent_edit_write_lease_allowed(
				candidate.inode, &edit_deadline_tick);

	enabled = intr_save();
	open_file_io_state_init_locked();
	if (!authorized || candidate.file_generation !=
				state->file_generations[file_slot] ||
	    candidate.edit_generation != state->edit_generation ||
	    !open_file_io_grant_matches_locked(
		&candidate, file, proc, file_slot, operation)) {
		intr_restore(enabled);
		return -1;
	}
	open_file_io_grant_capture_locked(
		grant, file, proc, file_slot, operation, edit_deadline_tick);
	*authorized_cred = grant->cred;
	open_file_io_token_issue_locked(token, grant, cache_slot, operation,
					thread);
	intr_restore(enabled);
	return 0;
}

int open_file_io_token_validate(const struct open_file_io_token *token,
				struct inode *inode,
				enum vfs_operation operation)
{
	struct proc *proc = curr_proc();
	struct thread *thread = curr_thread();
	struct open_file_io_grant *grant;
	uint encoded_operation;
	uint cache_slot;
	int valid = 0;
	int enabled;

	if (token == 0 || inode == 0 || proc == 0 || thread == 0 ||
	    thread->process != proc || thread->identity_generation == 0 ||
	    thread->kernel_work_depth == 0 ||
	    thread->kernel_work_target_syscall_id !=
		    (operation == VFS_OP_READ ? SYS_read : SYS_write))
		return 0;
	cache_slot = (uint)(token->opaque[0] >> 32);
	encoded_operation = (uint)token->opaque[0];
	if (cache_slot >= OPEN_FILE_IO_CACHE_CAP ||
	    encoded_operation != (uint)operation ||
	    open_file_io_operation_mask(operation) == 0)
		return 0;

	enabled = intr_save();
	open_file_io_state_init_locked();
	grant = &open_file_io_state.grants[cache_slot];
	/*
	 * acquire() already performed the revocation, credential, lifecycle and
	 * inode-generation checks.  This second boundary is a sealed hand-off to
	 * the filesystem, not another authorization walk.  Binding it to the
	 * current syscall receipt prevents a stack token from becoming a lasting
	 * capability while keeping the common path constant and cache-local.
	 */
	if (grant->valid && grant->sequence == token->opaque[1] &&
	    grant->inode == inode && token->opaque[2] == (uint64)inode &&
	    grant->subject == proc && grant->file != 0 &&
	    grant->file->ref > 0 && grant->file->type == FD_INODE &&
	    grant->file->ip == inode &&
	    (grant->allowed & open_file_io_operation_mask(operation)) != 0 &&
	    token->opaque[3] == open_file_io_token_seal_locked(token, thread))
		valid = 1;
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
	open_file_io_state_init_locked();
	memmove(out, &open_file_io_state.stats, sizeof(*out));
	intr_restore(enabled);
}
