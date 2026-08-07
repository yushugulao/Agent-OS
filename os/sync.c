#include "defs.h"
#include "proc.h"
#include "sync.h"

static int mutex_owned_by(const struct mutex *m, const struct thread *t)
{
	return m != 0 && t != 0 && t->identity_generation != 0 &&
	       m->owner == t &&
	       m->owner_generation == t->identity_generation;
}

// 所有权直接交给先进先出队首，避免新到线程在选中等待者运行前抢走阻塞互斥锁。
static void mutex_release_locked(struct mutex *m)
{
	struct thread *next = wait_queue_wake_one_thread(&m->waiters);

	if (next != 0) {
		m->locked = 1;
		m->owner = next;
		m->owner_generation = next->identity_generation;
		return;
	}
	m->locked = 0;
	m->owner = 0;
	m->owner_generation = 0;
}

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
	m->owner = 0;
	m->owner_generation = 0;
	wait_queue_init(&m->waiters, WAIT_REASON_MUTEX);
	return m;
}

int mutex_lock(struct mutex *m)
{
	struct thread *self = curr_thread();
	int enabled;
	int wait_status;

	if (m == 0 || self == 0 || self->identity_generation == 0)
		return -1;
	for (;;) {
		enabled = intr_save();
		if (proc_thread_exit_requested()) {
			intr_restore(enabled);
			return -1;
		}
		if (mutex_owned_by(m, self)) {
			intr_restore(enabled);
			return MUTEX_RECURSIVE_LOCK;
		}
		if (!m->locked) {
			m->locked = 1;
			m->owner = self;
			m->owner_generation = self->identity_generation;
			intr_restore(enabled);
			debugf("lock a free mutex");
			return 0;
		}
		if (!m->blocking) {
			intr_restore(enabled);
			yield();
			continue;
		}
		debugf("block to wait for mutex");
		wait_status = wait_queue_sleep_irq(&m->waiters);
		if (mutex_owned_by(m, self)) {
			if (wait_status == WAIT_QUEUE_OK &&
			    !proc_thread_exit_requested()) {
				intr_restore(enabled);
				debugf("blocking mutex passed to me");
				return 0;
			}
			mutex_release_locked(m);
		}
		intr_restore(enabled);
		if (wait_status != WAIT_QUEUE_OK)
			return -1;
	}
}

int mutex_unlock(struct mutex *m)
{
	struct thread *self = curr_thread();
	int enabled = intr_save();

	if (!m->locked || !mutex_owned_by(m, self)) {
		intr_restore(enabled);
		return -1;
	}
	mutex_release_locked(m);
	intr_restore(enabled);
	return 0;
}

void mutex_release_thread_locks(struct thread *t)
{
	struct proc *p;
	int enabled;

	if (t == 0 || (p = t->process) == 0 || t->identity_generation == 0)
		return;
	enabled = intr_save();
	for (uint i = 0; i < p->next_mutex_id && i < LOCK_POOL_SIZE; i++) {
		struct mutex *m = &p->mutex_pool[i];

		if (m->locked && mutex_owned_by(m, t))
			mutex_release_locked(m);
	}
	intr_restore(enabled);
}

static int sync_wait_queue_empty(const struct wait_queue *q)
{
	return q != 0 && q->head == 0 && q->tail == 0;
}

/*
 * 映像替换会更换全部用户可见同步名字空间。校验与复位分离，使畸形或陈旧
 * 等待者能在凭据和虚拟内存发布不可逆前中止。
 */
int sync_proc_exec_validate_locked(struct proc *p, struct thread *survivor)
{
	if (p == 0 || survivor == 0 || survivor->process != p ||
	    p->next_mutex_id > LOCK_POOL_SIZE ||
	    p->next_semaphore_id > LOCK_POOL_SIZE ||
	    p->next_condvar_id > LOCK_POOL_SIZE)
		return -1;
	for (uint i = 0; i < p->next_mutex_id; i++) {
		struct mutex *m = &p->mutex_pool[i];

		if (!sync_wait_queue_empty(&m->waiters))
			return -1;
		if (m->locked) {
			if (!mutex_owned_by(m, survivor))
				return -1;
		} else if (m->owner != 0 || m->owner_generation != 0) {
			return -1;
		}
	}
	for (uint i = 0; i < p->next_semaphore_id; i++)
		if (!sync_wait_queue_empty(&p->semaphore_pool[i].waiters))
			return -1;
	for (uint i = 0; i < p->next_condvar_id; i++)
		if (!sync_wait_queue_empty(&p->condvar_pool[i].waiters))
			return -1;
	return 0;
}

void sync_proc_exec_reset_locked(struct proc *p, struct thread *survivor)
{
	if (sync_proc_exec_validate_locked(p, survivor) < 0)
		panic("exec synchronization state");
	memset(p->mutex_pool, 0, sizeof(p->mutex_pool));
	memset(p->semaphore_pool, 0, sizeof(p->semaphore_pool));
	memset(p->condvar_pool, 0, sizeof(p->condvar_pool));
	p->next_mutex_id = 0;
	p->next_semaphore_id = 0;
	p->next_condvar_id = 0;
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
	int enabled;

	if (s == 0)
		return -1;
	enabled = intr_save();
	if (s->count == 0x7fffffff) {
		intr_restore(enabled);
		return -1;
	}
	s->count++;
	if (s->count <= 0 && wait_queue_wake_one(&s->waiters))
		debugf("semaphore up and notify another task");
	debugf("semaphore up from %d to %d", s->count - 1, s->count);
	intr_restore(enabled);
	return 0;
}

int semaphore_down(struct semaphore *s)
{
	int enabled;
	int wait_result;

	if (s == 0)
		return -1;
	enabled = intr_save();
	if (proc_thread_exit_requested()) {
		intr_restore(enabled);
		return -1;
	}
	s->count--;
	if (s->count < 0) {
		debugf("semaphore down to %d and wait...", s->count);
		wait_result = wait_queue_sleep_irq(&s->waiters);
		if (wait_result < 0) {
			/*
			 * 撤销本等待者的扣减。正常唤醒后再拆除已消费一次释放操作，须将该
			 * 授予转交下一个先进先出等待者；单纯取消排队则没有授予可传递。
			 */
			s->count++;
			if (wait_result == WAIT_QUEUE_WOKEN_INTERRUPTED &&
			    s->waiters.head != 0)
				(void)wait_queue_wake_one(&s->waiters);
			intr_restore(enabled);
			return -1;
		}
		debugf("semaphore up to %d and wake up", s->count);
	}
	debugf("finish semaphore_down with count = %d", s->count);
	intr_restore(enabled);
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
	int enabled;

	if (cond == 0)
		return;
	enabled = intr_save();
	if (wait_queue_wake_one(&cond->waiters)) {
		debugf("signal woke a condition waiter");
	} else {
		debugf("dummpy signal");
	}
	intr_restore(enabled);
}

int cond_wait(struct condvar *cond, struct mutex *m)
{
	int enabled;
	int lock_result;
	int wait_result;

	if (cond == 0 || m == 0)
		return -1;
	enabled = intr_save();
	if (proc_thread_exit_requested()) {
		intr_restore(enabled);
		return -1;
	}
	if (mutex_unlock(m) < 0)
		goto fail;
	debugf("wait for cond");
/* 解锁与队列发布构成单核原子转换。 */
	wait_result = wait_queue_sleep_irq(&cond->waiters);
	debugf("wake up from cond");
	lock_result = mutex_lock(m);
	intr_restore(enabled);
	return wait_result == WAIT_QUEUE_OK && lock_result == 0 ? 0 : -1;
fail:
	intr_restore(enabled);
	return -1;
}
