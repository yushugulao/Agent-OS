#ifndef SYNC_H
#define SYNC_H
#include "types.h"
#include "wait.h"

struct thread;
struct proc;

#define MUTEX_RECURSIVE_LOCK (-0xdead)

struct mutex {
	uint blocking;
	uint locked;
	struct thread *owner;
	uint64 owner_generation;
	struct wait_queue waiters;
};

struct semaphore {
	int count;
	struct wait_queue waiters;
};

struct condvar {
	struct wait_queue waiters;
};

struct mutex *mutex_create(int blocking);
int mutex_lock(struct mutex *);
int mutex_unlock(struct mutex *);
void mutex_release_thread_locks(struct thread *);
int sync_proc_exec_validate_locked(struct proc *, struct thread *);
void sync_proc_exec_reset_locked(struct proc *, struct thread *);
struct semaphore *semaphore_create(int count);
int semaphore_up(struct semaphore *);
int semaphore_down(struct semaphore *);
struct condvar *condvar_create();
void cond_signal(struct condvar *);
int cond_wait(struct condvar *, struct mutex *);
#endif
