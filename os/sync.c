#include "defs.h"
#include "proc.h"
#include "sync.h"

struct mutex *mutex_create(int blocking)
{
	struct proc *p = curr_proc();
	if (p->next_mutex_id >= LOCK_POOL_SIZE) {
		return NULL;
	}
	struct mutex *m = &p->mutex_pool[p->next_mutex_id];
	p->next_mutex_id++;
	m->blocking = blocking;
	m->locked = 0;
	wait_queue_init(&m->waiters, WAIT_REASON_MUTEX);
	return m;
}

int mutex_lock(struct mutex *m)
{
	if (proc_thread_exit_requested())
		return -1;
	if (!m->locked) {
		m->locked = 1;
		debugf("lock a free mutex");
		return 0;
	}
	if (!m->blocking) {
		debugf("try to lock spin mutex");
		while (m->locked) {
			if (proc_thread_exit_requested())
				return -1;
			yield();
		}
		m->locked = 1;
		debugf("lock spin mutex after some trials");
		return 0;
	}
	debugf("block to wait for mutex");
	if (wait_queue_sleep(&m->waiters) < 0)
		return -1;
	debugf("blocking mutex passed to me");
	return 0;
}

int mutex_unlock(struct mutex *m)
{
	if (!m->locked)
		return -1;
	if (m->blocking) {
		if (!wait_queue_wake_one(&m->waiters)) {
			m->locked = 0;
			debugf("blocking mutex released");
		} else {
			debugf("blocking mutex passed to waiter");
		}
	} else {
		m->locked = 0;
		debugf("spin mutex unlocked");
	}
	return 0;
}

struct semaphore *semaphore_create(int count)
{
	struct proc *p = curr_proc();
	if (count < 0 || p->next_semaphore_id >= LOCK_POOL_SIZE) {
		return NULL;
	}
	struct semaphore *s = &p->semaphore_pool[p->next_semaphore_id];
	p->next_semaphore_id++;
	s->count = count;
	wait_queue_init(&s->waiters, WAIT_REASON_SEMAPHORE);
	return s;
}

int semaphore_up(struct semaphore *s)
{
	if (s->count == 0x7fffffff)
		return -1;
	s->count++;
	if (s->count <= 0) {
		if (!wait_queue_wake_one(&s->waiters)) {
			s->count--;
			return -1;
		}
		debugf("semaphore up and notify another task");
	}
	debugf("semaphore up from %d to %d", s->count - 1, s->count);
	return 0;
}

int semaphore_down(struct semaphore *s)
{
	if (proc_thread_exit_requested())
		return -1;
	s->count--;
	if (s->count < 0) {
		debugf("semaphore down to %d and wait...", s->count);
		if (wait_queue_sleep(&s->waiters) < 0) {
			s->count++;
			return -1;
		}
		debugf("semaphore up to %d and wake up", s->count);
	}
	debugf("finish semaphore_down with count = %d", s->count);
	return 0;
}

struct condvar *condvar_create()
{
	struct proc *p = curr_proc();
	if (p->next_condvar_id >= LOCK_POOL_SIZE) {
		return NULL;
	}
	struct condvar *c = &p->condvar_pool[p->next_condvar_id];
	p->next_condvar_id++;
	wait_queue_init(&c->waiters, WAIT_REASON_CONDVAR);
	return c;
}

void cond_signal(struct condvar *cond)
{
	if (wait_queue_wake_one(&cond->waiters)) {
		debugf("signal woke a condition waiter");
	} else {
		debugf("dummpy signal");
	}
}

int cond_wait(struct condvar *cond, struct mutex *m)
{
	int wait_result;

	if (proc_thread_exit_requested())
		return -1;
	if (mutex_unlock(m) < 0)
		return -1;
	debugf("wait for cond");
	wait_result = wait_queue_sleep(&cond->waiters);
	if (wait_result < 0) {
		if (wait_result != WAIT_QUEUE_INTERRUPTED)
			mutex_lock(m);
		return -1;
	}
	debugf("wake up from cond");
	return mutex_lock(m);
}
