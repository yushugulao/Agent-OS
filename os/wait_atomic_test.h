#ifndef WAIT_ATOMIC_TEST_H
#define WAIT_ATOMIC_TEST_H

#include "proc.h"
#include "../wait_atomic_test_abi.h"

#ifdef WAIT_ATOMIC_TEST_PROFILE
int sys_wait_atomic_test(uint version, uint command, uint operation,
			 int target_pid, uint64 receiptaddr, uint user_size);
int wait_atomic_test_begin(struct proc *p, uint operation, uint64 flags);
void wait_atomic_test_complete(struct proc *p, uint operation, uint64 flags);
int wait_atomic_test_agent_wait(struct proc *p);
int agent_ipc_wait_test_publish(struct proc *p);
#endif

#endif
