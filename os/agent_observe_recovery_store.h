#ifndef AGENT_OBSERVE_RECOVERY_STORE_H
#define AGENT_OBSERVE_RECOVERY_STORE_H

#include "agent.h"
#include "agent_observe_capacity.h"
#include "workflow_lifecycle.h"

#define AGENT_OBSSTORE_SNAPSHOT_EMPTY 0
#define AGENT_OBSSTORE_SNAPSHOT_READY 1
#define AGENT_OBSSTORE_SNAPSHOT_RETRY 2

/* 应用二进制接口端点只接收校验后的值，不接触持久镜像布局。 */
struct agent_obsstore_scope_view {
	uint scope_id;
	uint record_count;
	struct workflow_lifecycle_key lifecycle;
	uint64 total_records;
	uint64 dropped_records;
	uint64 ledger_hash;
};

struct agent_obsstore_record_view {
	struct agent_audit_record record;
	uint64 receipt_id;
};

int agent_obsstore_snapshot_begin(uint64 *);
uint agent_obsstore_snapshot_scope_capacity(void);
uint agent_obsstore_snapshot_record_capacity(void);
int agent_obsstore_snapshot_scope(
	uint64, uint, struct agent_obsstore_scope_view *);
int agent_obsstore_snapshot_record(
	uint64, uint, uint, uint, struct workflow_lifecycle_key,
	struct agent_obsstore_record_view *);
int agent_obsstore_snapshot_confirm(uint64);
int agent_obsstore_recovery_reap(
	uint, struct workflow_lifecycle_key, uint64 *, uint64 *);
int agent_obsstore_recovery_reap_resume(
	struct workflow_lifecycle_key, uint64 *, uint64 *);
int agent_obsstore_reap_query(
	struct workflow_lifecycle_key, uint64, int *, uint64 *,
	struct agent_observe_reap_cookie *);
int agent_obsstore_reap_consume(
	const struct agent_observe_reap_cookie *);

#endif
