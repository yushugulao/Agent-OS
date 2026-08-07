#include "agent_internal.h"

/*
 * 可调度 Agent 维护任务的边沿触发状态。生产者只负责发布，
 * 由核心协调器统一消费，避免在业务路径内直接执行维护工作。
 */
static int agent_background_pending;

void
agent_background_request(void)
{
	__atomic_store_n(&agent_background_pending, 1, __ATOMIC_RELEASE);
}

int
agent_background_work_pending(void)
{
	return __atomic_load_n(&agent_background_pending, __ATOMIC_ACQUIRE);
}

int
agent_background_take(void)
{
	return __atomic_exchange_n(&agent_background_pending, 0,
				   __ATOMIC_ACQ_REL);
}
