#ifndef SYNC_H
#define SYNC_H
#include "types.h"
#include "wait.h"

struct mutex {
	uint blocking;
	uint locked;
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
struct semaphore *semaphore_create(int count);
int semaphore_up(struct semaphore *);
int semaphore_down(struct semaphore *);
struct condvar *condvar_create();
void cond_signal(struct condvar *);
int cond_wait(struct condvar *, struct mutex *);
#endif
