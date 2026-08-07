#include "agent_lifecycle.h"
#include "agent_internal.h"
#include "agent_identity_lease.h"
#include "defs.h"
#include "vfs_security.h"

static uint64 next_control_id;

#define AGENT_CONTEXT_LANE_OWNER_NONE (-1)

/*
 * Context 提交是进程级事务。通道允许睡眠：工具执行可阻塞于 metadata
 * 或 I/O，竞争者进入 FIFO 等待队列而非持续关中断。depth 为零表示所有权
 * 已交给尚未恢复运行的线程。
 */
void
agent_lifecycle_context_lane_init(struct proc *p)
{
	if (p == 0)
		return;
	p->agent_control_state = AGENT_CONTROL_OPEN;
	wait_queue_init(&p->agent_context_lane_waiters,
			WAIT_REASON_AGENT_CONTEXT);
	p->agent_context_lane_owner_tid = AGENT_CONTEXT_LANE_OWNER_NONE;
	p->agent_context_lane_depth = 0;
}

static void
agent_lifecycle_context_lane_handoff_locked(struct proc *p)
{
	struct thread *next;

	p->agent_context_lane_depth = 0;
	if (!proc_teardown_live(p)) {
		p->agent_context_lane_owner_tid =
			AGENT_CONTEXT_LANE_OWNER_NONE;
		wait_queue_wake_all(&p->agent_context_lane_waiters);
		return;
	}
	next = p->agent_context_lane_waiters.head;
	if (next == 0) {
		p->agent_context_lane_owner_tid =
			AGENT_CONTEXT_LANE_OWNER_NONE;
		return;
	}
	if (next->process != p || next->state != SLEEPING ||
	    next->wait_channel != &p->agent_context_lane_waiters ||
	    next->tid < 0 || next->tid >= NTHREAD)
		panic("Agent context lane waiter");
	p->agent_context_lane_owner_tid = next->tid;
	if (wait_queue_wake_one(&p->agent_context_lane_waiters) != 1)
		panic("Agent context lane handoff");
}

int
agent_lifecycle_context_lane_enter(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled;
	int wait_status;

	if (p == 0 || t == 0 || t->process != p ||
	    t->tid < 0 || t->tid >= NTHREAD)
		return -1;
	for (;;) {
		enabled = intr_save();
		if (p->agent_context_lane_owner_tid == t->tid) {
			if (p->agent_context_lane_depth == 0) {
				if (!proc_teardown_live(p)) {
					agent_lifecycle_context_lane_handoff_locked(p);
					intr_restore(enabled);
					return -1;
				}
				p->agent_context_lane_depth = 1;
			} else {
				if (p->agent_context_lane_depth == ~0U)
					panic("Agent context lane depth");
				p->agent_context_lane_depth++;
			}
			intr_restore(enabled);
			return 0;
		}
		if (agent_metadata_txn_owned(0))
			panic("metadata acquired before Agent context lane");
		if (!proc_teardown_live(p)) {
			intr_restore(enabled);
			return -1;
		}
		if (p->agent_context_lane_owner_tid ==
		    AGENT_CONTEXT_LANE_OWNER_NONE) {
			p->agent_context_lane_owner_tid = t->tid;
			p->agent_context_lane_depth = 1;
			intr_restore(enabled);
			return 0;
		}
		/* 从检查所有权到发布入队始终关中断，避免释放方漏唤醒。 */
		wait_status = wait_queue_sleep_irq(
			&p->agent_context_lane_waiters);
		/*
		 * 直接移交会在目标恢复前预留 owner_tid。teardown 可能使已唤醒线程
		 * 仍收到 INTERRUPTED，因此返回前必须接收或取消预留。外层关中断
		 * 边界保证判断期间预留不变。
		 */
		if (p->agent_context_lane_owner_tid == t->tid &&
		    p->agent_context_lane_depth == 0) {
			if (wait_status != WAIT_QUEUE_OK ||
			    !proc_teardown_live(p)) {
				agent_lifecycle_context_lane_handoff_locked(p);
				intr_restore(enabled);
				return -1;
			}
			p->agent_context_lane_depth = 1;
			intr_restore(enabled);
			return 0;
		}
		intr_restore(enabled);
		if (wait_status != WAIT_QUEUE_OK)
			return -1;
	}
}

void
agent_lifecycle_context_lane_leave(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (p == 0 || t == 0 || t->process != p ||
	    p->agent_context_lane_owner_tid != t->tid ||
	    p->agent_context_lane_depth == 0)
		panic("Agent context lane owner");
	if (p->agent_context_lane_depth == 1 &&
	    agent_metadata_txn_owned(0))
		panic("Agent context lane released with metadata");
	p->agent_context_lane_depth--;
	if (p->agent_context_lane_depth == 0)
		agent_lifecycle_context_lane_handoff_locked(p);
	intr_restore(enabled);
}

int
agent_lifecycle_context_lane_quiescent(struct proc *p)
{
	int enabled = intr_save();
	int quiescent = p != 0 &&
		p->agent_context_lane_owner_tid ==
			AGENT_CONTEXT_LANE_OWNER_NONE &&
		p->agent_context_lane_depth == 0 &&
		p->agent_context_lane_waiters.head == 0 &&
		p->agent_context_lane_waiters.tail == 0;

	intr_restore(enabled);
	return quiescent;
}

void
agent_lifecycle_init(void)
{
	next_control_id = 1;
}

int
sys_agent_workflow_lifecycle_info(uint64 addr, uint64 user_size,
				  uint64 flags, uint64 expected_id,
				  uint64 expected_generation)
{
	struct proc *p = curr_proc();
	struct agent_metadata_runtime_snapshot metadata;
	struct agent_workflow_lifecycle_info info;
	struct workflow_lifecycle_key current = workflow_lifecycle_none();
	struct workflow_lifecycle_key expected = workflow_lifecycle_none();
	uint64 copy_size;
	uint scope_id;
	int enabled;
	int lifecycle_charged;
	int match;
	int status = AGENT_STATUS_OK;

	if (user_size < 2 * sizeof(unsigned int))
		return -1;
	if ((flags & ~((uint64)
		      AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT)) != 0)
		return AGENT_STATUS_BAD_PARAM;
	match = (flags &
		 AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT) != 0;
	if ((!match && (expected_id != 0 || expected_generation != 0)) ||
	    (match &&
	     (expected_id == 0 || expected_id > (uint64)(uint)-1 ||
	      expected_generation == 0)))
		return AGENT_STATUS_BAD_PARAM;
	copy_size = MIN(user_size, sizeof(info));
	if (p == 0 ||
	    user_range_check(p->pagetable, addr, copy_size, PTE_W) < 0)
		return -1;

	memset(&info, 0, sizeof(info));
	info.version = AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION;
	info.struct_size = sizeof(info);
	enabled = intr_save();
	lifecycle_charged = p->workflow_lifecycle_charged != 0;
	if (lifecycle_charged) {
		current.id = p->workflow_lifecycle_id;
		current.generation = p->workflow_lifecycle_generation;
	}
	info.context_lane_depth = p->agent_context_lane_depth;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (t->state == SLEEPING &&
		    t->wait_channel == &p->agent_context_lane_waiters &&
		    t->wait_reason == WAIT_REASON_AGENT_CONTEXT)
			info.context_lane_waiters++;
	}
	agent_metadata_proc_runtime_snapshot(p, &metadata);
	info.metadata_txn_owned = metadata.metadata_txn_owned;
	info.metadata_txn_waiters = metadata.metadata_txn_waiters;
	if (resource_account_handle_valid(p->resource_account)) {
		info.resource_account_valid = 1;
		info.resource_account_slot = p->resource_account.slot;
		info.resource_account_generation =
			p->resource_account.generation;
	}
	intr_restore(enabled);
	if (lifecycle_charged && workflow_lifecycle_key_valid(current) &&
	    workflow_lifecycle_scope(current, &scope_id) == 0) {
		info.charged = 1;
		info.key.id = current.id;
		info.key.generation = current.generation;
	} else if (lifecycle_charged) {
		status = AGENT_STATUS_NOT_FOUND;
	}
	if (match) {
		expected.id = (uint)expected_id;
		expected.generation = expected_generation;
		if (!info.charged)
			status = AGENT_STATUS_NOT_FOUND;
		else if (!workflow_lifecycle_key_equal(current, expected))
			status = AGENT_STATUS_STALE;
	}
	if (copyout(p->pagetable, addr, (char *)&info, copy_size) < 0)
		return -1;
	return status;
}

uint64
agent_lifecycle_alloc_control_id(void)
{
	int enabled = intr_save();
	uint64 id = next_control_id;

	if (id == 0) {
		intr_restore(enabled);
		return 0;
	}
	if (agent_identity_lease_allocator_contains(
		    AGENT_IDENTITY_ALLOCATOR_CONTROL, id)) {
		next_control_id = id == ~0ULL ? 0 : id + 1;
		intr_restore(enabled);
		agent_identity_lease_allocator_note_next(
			AGENT_IDENTITY_ALLOCATOR_CONTROL, next_control_id);
		return id;
	}
	intr_restore(enabled);
	(void)agent_identity_lease_allocator_renew(
		AGENT_IDENTITY_ALLOCATOR_CONTROL);
	return 0;
}

void
agent_lifecycle_control_id_floor(uint64 floor)
{
	if (floor == 0) {
		next_control_id = 0;
		return;
	}
	if (next_control_id != 0 && floor > next_control_id)
		next_control_id = floor;
}

uint64
agent_lifecycle_controller_departing_locked(struct proc *p)
{
	struct agent_controller_departure departure;
	struct workflow_lifecycle_key closed;
	int finish;
	int close_status;

	if (intr_get())
		panic("controller departure unlocked");
	if (p == 0)
		return 0;
	finish = p->teardown_state == PROC_TEARDOWN_LIVE ||
		 p->teardown_state >= PROC_TEARDOWN_RECLAIMING;
	/* 权限已关闭，迟到的通道提交静默后才能最终退出。 */
	if (finish && p->teardown_state != PROC_TEARDOWN_LIVE &&
	    !agent_lifecycle_context_lane_quiescent(p))
		panic("controller retired with active Context lane");
	if (agent_identity_controller_depart(p, finish, &departure) <= 0)
		return 0;
	/* 权限首次关闭时撤销失去控制器的边。 */
	if (!departure.scope_controller) {
		proc_request_controller_exit(departure.lifecycle,
				     departure.control_id,
				     AGENT_STATUS_CANCELLED);
		return departure.control_id;
	}
	/* 一次性所有权绑定只负责触发关闭，后代销毁仍以不可变生命周期 key 为准。 */
	close_status = vfs_scope_close_owned(
		departure.scope_id, departure.lifecycle,
		departure.control_id, &closed);
	if (close_status == 0)
		proc_request_workflow_exit(closed, AGENT_STATUS_CANCELLED);
	else if (workflow_lifecycle_closing(departure.lifecycle))
		proc_request_workflow_exit(departure.lifecycle,
				   AGENT_STATUS_CANCELLED);
	else
		panic("controller retirement without lifecycle close");
	/* 第一阶段关闭接纳，第二阶段发布已退出身份。 */
	if (!finish)
		return departure.control_id;
	/* 关闭失败时保留 QUIESCING 与绑定，供后续重试。 */
	if (agent_identity_controller_close_commit(p, &departure) < 0)
		panic("controller lifecycle close commit");
	return departure.control_id;
}

int
agent_lifecycle_spawn_publish_locked(struct proc *parent, struct proc *child)
{
	return agent_identity_spawn_publish_locked(parent, child);
}
