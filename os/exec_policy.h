#ifndef EXEC_POLICY_H
#define EXEC_POLICY_H

#include "types.h"

struct inode;
struct proc;

int exec_policy_inode_mutable(struct inode *ip);
int exec_policy_inode_layout_valid(struct inode *ip);
int exec_policy_inode_trusted(struct inode *ip);
int exec_policy_inode_allows_role(struct inode *ip, int role);
int exec_policy_process_bootstrap(const struct proc *p);
int exec_policy_process_allows_role(const struct proc *p, int role);

#endif
