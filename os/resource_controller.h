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
	/* Counter ownership may move without moving an allocator-owned object. */
	RESOURCE_KIND_COUNT_TRANSFERABLE = 1U << 0,
	/* The allocator's concrete pool/class provenance must move with the object. */
	RESOURCE_KIND_POOL_AFFINE = 1U << 1,
};

enum resource_account_kind {
	RESOURCE_ACCOUNT_EXEC = 1,
	RESOURCE_ACCOUNT_STORAGE,
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

/* One-lock view of one resource kind in an account. */
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

/*
 * A reservation is a short-lived kernel lease. It never crosses a blocking
 * boundary: callers either commit it to durable usage or cancel it before
 * returning. Keeping the complete vector here makes multi-resource admission
 * (process + first thread, or both pipe endpoints) one atomic operation.
 */
struct resource_reservation {
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	uint64 amounts[RESOURCE_KIND_COUNT];
	int active;
};

#define RESOURCE_RESERVE_ALLOW_CLOSING (1U << 0)

/*
 * Replenishing budgets are a separate facet from durable resources.  Lanes
 * are account-local policy dimensions; global pools can be composed with a
 * lane in one atomic lease to model hierarchical admission.
 */
#define RESOURCE_RATE_LANE_CAP 4U
#define RESOURCE_RATE_GLOBAL_CAP 2U
#define RESOURCE_RATE_BUNDLE_CAP 2U
#define RESOURCE_RATE_LEASE_CAP 2049U

#define RESOURCE_RATE_PROFILE_ALLOW_CLOSING (1U << 0)
#define RESOURCE_RATE_ENDPOINT_ALLOW_DEBT (1U << 0)

enum resource_rate_endpoint_scope {
	RESOURCE_RATE_ACCOUNT = 1,
	RESOURCE_RATE_GLOBAL,
};

struct resource_rate_profile {
	uint64 burst;
	uint64 refill;
	uint flags;
};

struct resource_rate_endpoint {
	enum resource_rate_endpoint_scope scope;
	struct resource_account_handle account;
	uint index;
	uint flags;
	uint64 amount;
};

struct resource_rate_lease_handle {
	uint slot;
	uint generation;
};

struct resource_rate_snapshot {
	uint64 tokens;
	uint64 leased;
	uint64 debt;
	uint64 pending_debt;
	uint64 burst;
	uint64 refill;
	uint flags;
};

void resource_controller_init(void);
uint resource_kind_attributes(enum resource_kind);
int resource_policy_configure(enum resource_kind, uint64, uint64, uint64);
int resource_policy_guarantee_reserved(enum resource_kind);
#ifdef PHYSICAL_PAGE_TEST_HOOKS
int resource_policy_reserved_snapshot(enum resource_kind, uint64 *, uint64 *);
#endif

struct resource_account_handle resource_account_none(void);
int resource_account_handle_valid(struct resource_account_handle);
int resource_account_handle_equal(struct resource_account_handle,
				  struct resource_account_handle);
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

struct resource_rate_lease_handle resource_rate_lease_none(void);
int resource_rate_lease_valid(struct resource_rate_lease_handle);
int resource_rate_account_configure(
	struct resource_account_handle,
	const struct resource_rate_profile *, uint);
int resource_rate_global_configure(
	uint, const struct resource_rate_profile *);
int resource_rate_reserve_many(
	const struct resource_rate_endpoint *, uint,
	struct resource_rate_lease_handle *);
int resource_rate_lease_commit(struct resource_rate_lease_handle);
void resource_rate_lease_cancel(struct resource_rate_lease_handle);
int resource_rate_charge_many(
	const struct resource_rate_endpoint *, uint);
int resource_rate_account_refill(
	struct resource_account_handle, uint, uint64 *);
int resource_rate_global_refill(uint, uint64 *);
int resource_rate_account_snapshot(
	struct resource_account_handle, uint,
	struct resource_rate_snapshot *);
int resource_rate_global_snapshot(
	uint, struct resource_rate_snapshot *);
int resource_rate_account_idle(struct resource_account_handle);

#endif
