#ifndef VFS_SECURITY_H
#define VFS_SECURITY_H

#include "types.h"
#include "workflow_lifecycle.h"

struct inode;
struct proc;
struct user_image;

struct vfs_cred {
	uint scope_id;
	uint storage_principal_id;
	uint64 capabilities;
	int kernel;
};

struct vfs_proc_security_state {
	uint scope_id;
	int scope_controller;
	uint64 effective_caps;
	uint64 inheritable_caps;
	uint pending_scope_id;
	uint64 pending_caps;
	uint pending_exec_dev;
	uint pending_exec_inum;
	uint pending_exec_incarnation;
	uint bound_exec_dev;
	uint bound_exec_inum;
	uint bound_exec_incarnation;
	uint storage_principal_id;
	int lifecycle_charged;
	struct workflow_lifecycle_key lifecycle;
};

enum vfs_exec_identity_policy {
	VFS_EXEC_IDENTITY_PUBLIC = 0,
	VFS_EXEC_IDENTITY_PRESERVE_AGENT,
};

/*
 * 执行映像先完整准备凭据替换但不发布。调用方须在交换虚拟内存指针的
 * 同一关中断区内提交，或中止并释放未发布的生命周期预留。
 */
struct vfs_exec_transition {
	struct vfs_proc_security_state source;
	struct vfs_proc_security_state target;
	enum vfs_exec_identity_policy identity_policy;
	int prepared;
	int drop_to_public;
	int lifecycle_reserved;
};

enum vfs_spawn_scope_mode {
	VFS_SPAWN_SCOPE_DROP = 0,
	VFS_SPAWN_SCOPE_INHERIT,
	VFS_SPAWN_SCOPE_FRESH,
};

enum vfs_operation {
	VFS_OP_LOOKUP = 1,
	VFS_OP_READ,
	VFS_OP_CREATE,
	VFS_OP_WRITE,
	VFS_OP_TRUNCATE,
	VFS_OP_DELETE,
	VFS_OP_EXEC,
};

#define VFS_CAP_CONTENT_READ   (1ULL << 1)
#define VFS_CAP_ARTIFACT_WRITE (1ULL << 6)
#define VFS_CAP_WORKFLOW \
	(VFS_CAP_CONTENT_READ | VFS_CAP_ARTIFACT_WRITE)

// 最多四个工作流可处于活动、关闭中或退役中；退役身份在回收完成前
// 保留固定目录分区。
#define VFS_SCOPE_MAX_ACTIVE WORKFLOW_LIFECYCLE_MAX_ACTIVE
// 可恢复文件系统回收器为每个退役作用域保留一个游标。计入准入的活动、
// 关闭中和退役身份共用此有界生命周期台账，避免退出突发压垮回收器。
#define VFS_SCOPE_LIFECYCLE_CAP WORKFLOW_LIFECYCLE_CAP
#define VFS_SCOPE_MAX_RETIRING VFS_SCOPE_LIFECYCLE_CAP

void vfs_cred_kernel(struct vfs_cred *);
void vfs_cred_from_proc(const struct proc *, struct vfs_cred *);
uint vfs_cred_lookup_policy(const struct vfs_cred *);
void vfs_proc_reset(struct proc *);
void vfs_proc_terminal_clear(struct proc *);
int vfs_scope_active(uint scope_id);
int vfs_scope_retiring(uint scope_id);
int vfs_scope_retained(uint scope_id);
int vfs_scope_lifecycle(uint scope_id, struct workflow_lifecycle_key *);
int vfs_scope_bind_controller(uint scope_id,
			      struct workflow_lifecycle_key lifecycle,
			      uint64 control_id);
int vfs_scope_close_owned(uint scope_id,
			  struct workflow_lifecycle_key lifecycle,
			  uint64 control_id,
			  struct workflow_lifecycle_key *closed);
int vfs_scope_close_trusted(uint scope_id,
			    struct workflow_lifecycle_key *closed);
uint vfs_scope_storage_guarantee(uint exempt_scope, int inode,
				 uint guarantee);
void vfs_scope_reap_pending(uint64 now);
void vfs_scope_reap_tick(uint64 now);
int vfs_proc_spawn_scope(const struct proc *, struct proc *,
			 enum vfs_spawn_scope_mode);
struct workflow_lifecycle_key vfs_proc_lifecycle(const struct proc *);
int vfs_proc_scope_publishable(const struct proc *);
int vfs_proc_lifecycle_active(const struct proc *);
void vfs_proc_lifecycle_release(struct proc *);
void vfs_proc_limit_capabilities(struct proc *, uint64);
int vfs_proc_delegate_exec(const struct proc *, struct proc *, struct inode *,
			   uint64);
int vfs_proc_exec_prepare(struct proc *, const struct user_image *, int,
			  struct vfs_exec_transition *);
int vfs_proc_exec_validate_locked(struct proc *,
				  const struct vfs_exec_transition *);
int vfs_proc_exec_commit(struct proc *, struct vfs_exec_transition *);
void vfs_proc_exec_abort(struct vfs_exec_transition *);

int vfs_exec_profile_valid(uint);
uint64 vfs_exec_profile_capabilities(uint);
int vfs_inode_label_valid(struct inode *);
int vfs_inode_authorize(struct inode *, const struct vfs_cred *,
			 enum vfs_operation);
uint vfs_default_create_policy(const struct vfs_cred *);
int vfs_create_request_authorize(const struct vfs_cred *, uint, int, int,
				 int);
int vfs_inode_init_label(struct inode *, const struct vfs_cred *, uint);
int vfs_inode_create_matches(struct inode *, const struct vfs_cred *, uint);
void vfs_inode_mark_free(struct inode *);
uint vfs_label_checksum(uint, uint, uint, uint, uint, uint, uint, uint, uint,
			uint, uint);

#endif
