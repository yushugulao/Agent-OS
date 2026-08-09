#ifndef RESOURCE_CONTROLLER_H
#define RESOURCE_CONTROLLER_H

#include "types.h"

#define RESOURCE_ACCOUNT_CAP 272
#define RESOURCE_LIMIT_UNBOUNDED (~0ULL)

enum resource_kind {
	RESOURCE_PROCESS = 0,
	RESOURCE_THREAD,
	RESOURCE_FILE_OBJECT,
	RESOURCE_FS_BLOCK,
	RESOURCE_FS_INODE,
	RESOURCE_BUFFER_CACHE,
	RESOURCE_AGENT_STATE_PAGE,
	RESOURCE_PHYSICAL_PAGE,
	RESOURCE_KIND_COUNT,
};

enum resource_kind_attribute {
	/* 计数归属可独立于分配器对象迁移。 */
	RESOURCE_KIND_COUNT_TRANSFERABLE = 1U << 0,
	/* 分配器池和类别来源必须随对象迁移。 */
	RESOURCE_KIND_POOL_AFFINE = 1U << 1,
};

enum resource_account_kind {
	RESOURCE_ACCOUNT_EXEC = 1,
	RESOURCE_ACCOUNT_STORAGE,
	RESOURCE_ACCOUNT_CACHE,
};

enum resource_charge_class {
	RESOURCE_CHARGE_ORDINARY = 0,
	RESOURCE_CHARGE_RESERVED,
	RESOURCE_CHARGE_CLASS_COUNT,
};

#define RESOURCE_CHARGE_GRANT(charge_class) (1U << (charge_class))

enum resource_account_state {
	RESOURCE_ACCOUNT_FREE = 0,
	RESOURCE_ACCOUNT_ACTIVE,
	RESOURCE_ACCOUNT_CLOSING,
	RESOURCE_ACCOUNT_DRAINING,
};

struct resource_account_handle {
	uint slot;
	uint64 generation;
};

struct resource_request {
	enum resource_kind kind;
	uint64 amount;
};

struct resource_account_limits {
	uint64 class_limit[RESOURCE_CHARGE_CLASS_COUNT][RESOURCE_KIND_COUNT];
};

/* 账户内单类资源的单锁快照。 */
struct resource_account_kind_snapshot {
	struct resource_account_handle handle;
	enum resource_account_state state;
	enum resource_account_kind account_kind;
	uint charge_grants;
	uint members;
	uint64 external_id;
	uint64 limit[RESOURCE_CHARGE_CLASS_COUNT];
	uint64 used[RESOURCE_CHARGE_CLASS_COUNT];
	uint64 pending[RESOURCE_CHARGE_CLASS_COUNT];
};

struct resource_policy_snapshot {
	uint64 capacity;
	uint64 used;
	uint64 pending;
	uint64 ordinary_used;
	uint64 ordinary_pending;
	uint64 reserved_used;
	uint64 reserved_pending;
};

/* 短租约不跨阻塞边界；完整向量保证多资源准入原子提交或撤销。 */
struct resource_reservation {
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	uint kind_mask;
	uint64 amounts[RESOURCE_KIND_COUNT];
	int active;
};

struct workflow_credit_snapshot;

#define RESOURCE_RESERVE_ALLOW_CLOSING (1U << 0)

void resource_controller_init(void);
uint resource_kind_attributes(enum resource_kind);
int resource_policy_configure(enum resource_kind, uint64, uint64, uint64);
int resource_policy_guarantee_reserved(enum resource_kind);
uint resource_policy_snapshot_all(struct resource_policy_snapshot *, uint);
#ifdef PHYSICAL_PAGE_TEST_HOOKS
int resource_policy_reserved_snapshot(enum resource_kind, uint64 *, uint64 *);
#endif

struct resource_account_handle resource_account_none(void);
int resource_account_handle_valid(struct resource_account_handle);
int resource_account_handle_equal(struct resource_account_handle,
				  struct resource_account_handle);
int resource_account_promise_admissible(
	uint, const struct resource_account_limits *);
int resource_account_create(enum resource_account_kind, uint64,
			    uint, const struct resource_account_limits *,
			    struct resource_account_handle *);
int resource_account_find(enum resource_account_kind, uint64,
			  struct resource_account_handle *);
int resource_account_matches(struct resource_account_handle,
			     enum resource_account_kind, uint64);
enum resource_account_state
resource_account_state_get(struct resource_account_handle);
int resource_account_active(struct resource_account_handle);
int resource_account_member_acquire(struct resource_account_handle);
int resource_account_member_release(struct resource_account_handle, int);
int resource_account_close(struct resource_account_handle);

int resource_reserve_many(struct resource_account_handle,
			  enum resource_charge_class,
			  const struct resource_request *, uint,
			  struct resource_reservation *);
int resource_reserve_many_flags(struct resource_account_handle,
				enum resource_charge_class,
				const struct resource_request *, uint, uint,
				struct resource_reservation *);
int resource_reservation_commit(struct resource_reservation *);
void resource_reservation_cancel(struct resource_reservation *);
int resource_acquire_many(struct resource_account_handle,
			  enum resource_charge_class,
			  const struct resource_request *, uint);
int resource_acquire_many_flags(struct resource_account_handle,
				enum resource_charge_class,
				const struct resource_request *, uint, uint);
int resource_release_many(struct resource_account_handle,
			  enum resource_charge_class,
			  const struct resource_request *, uint);
int resource_import_usage(struct resource_account_handle,
			  enum resource_charge_class,
			  const struct resource_request *, uint);
int resource_transfer_usage(struct resource_account_handle,
			    enum resource_charge_class,
			    struct resource_account_handle,
			    enum resource_charge_class,
			    const struct resource_request *, uint);
int resource_transfer_usage_flags(struct resource_account_handle,
				  enum resource_charge_class,
				  struct resource_account_handle,
				  enum resource_charge_class,
				  const struct resource_request *, uint, uint);
int resource_reconcile_usage(struct resource_account_handle,
			     enum resource_charge_class,
			     const struct resource_request *, uint);
uint64 resource_account_usage(struct resource_account_handle,
			      enum resource_kind);
uint64 resource_account_class_usage(struct resource_account_handle,
				    enum resource_charge_class,
				    enum resource_kind);
int resource_account_kind_snapshot(struct resource_account_handle,
				   enum resource_kind,
				   struct resource_account_kind_snapshot *);
int resource_account_trim(struct resource_account_handle);
int resource_credit_snapshot_pair_trim(
	struct resource_account_handle, struct resource_account_handle,
	struct workflow_credit_snapshot *);

#endif
