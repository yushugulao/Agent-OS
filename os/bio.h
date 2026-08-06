#ifndef BUF_H
#define BUF_H

#include "fs.h"
#include "types.h"

#define BIO_CACHE_CLEANUP_CAP 8U
#include "wait.h"
#include "../io_policy.h"

struct proc;
struct thread;

enum bio_checkpoint_state {
	BIO_CHECKPOINT_READY = 0,
	BIO_CHECKPOINT_DEFERRED,
	BIO_CHECKPOINT_INTERRUPTED,
};

/* Keep scheduling control out of integer device/filesystem error domains. */
struct bio_checkpoint_result {
	enum bio_checkpoint_state state;
};

static inline struct bio_checkpoint_result
bio_checkpoint_make(enum bio_checkpoint_state state)
{
	return (struct bio_checkpoint_result){ .state = state };
}

static inline int
bio_checkpoint_should_stop(struct bio_checkpoint_result result)
{
	return result.state != BIO_CHECKPOINT_READY;
}

#if defined(__GNUC__)
#define BIO_MUST_CHECK __attribute__((warn_unused_result))
#else
#define BIO_MUST_CHECK
#endif

struct buf {
	int valid; // has data been read from disk?
	int disk; // does disk "own" buf?
	int disk_result; // result of the most recent device transfer
	uint dev;
	uint blockno;
	uint refcnt;
	uint cache_owner;
	struct resource_account_handle cache_principal;
	uint cache_charge_class;
	int cache_charged;
	unsigned int lru_promote : 1;
	unsigned int transient : 1;
	unsigned int background_reserved : 1;
	unsigned int idle_class : 2;
	void *holder;
	uint hold_depth;
	struct wait_queue holder_waiters;
	struct buf *hash_next;
	struct buf *prev; // intrusive idle-queue links
	struct buf *next;
	uchar data[BSIZE];
};

#define BIO_OVERWRITE_FALLBACK 1
struct bio_overwrite_receipt {
	struct buf *buf;
	uint dev;
	uint blockno;
	uint active;
	uint skipped_preread;
};

#define BIO_OVERWRITE_RECEIPT_INIT { 0 }

void binit(void);
int bread(uint, uint, struct buf **) BIO_MUST_CHECK;
int bread_batch(uint, const uint *, struct buf **, uint) BIO_MUST_CHECK;
int bread_device(uint, uint, struct buf **) BIO_MUST_CHECK;
int bprepare_overwrite(uint, uint, struct bio_overwrite_receipt *)
	BIO_MUST_CHECK;
int bpublish_overwrite(struct bio_overwrite_receipt *, uint, struct buf **)
	BIO_MUST_CHECK;
void bcancel_overwrite(struct bio_overwrite_receipt *);
void brelse(struct buf *);
int bwrite(struct buf *) BIO_MUST_CHECK;
int bio_durable_flush(void) BIO_MUST_CHECK;
int bclaim(struct buf *) BIO_MUST_CHECK;
void bpin(struct buf *);
void bunpin(struct buf *);

void bio_policy_start(void);
void bio_policy_tick(void);
int bio_request_begin_current(void);
int bio_request_begin_current_cleanup(void);
int bio_request_begin_current_lazy(void);
int bio_request_begin_current_lazy_cleanup(void);
int bio_request_upgrade_current(void);
int bio_request_upgrade_current_cleanup(void);
int bio_request_active_current(void);
struct bio_checkpoint_result bio_request_checkpoint(void) BIO_MUST_CHECK;
struct bio_checkpoint_result bio_request_checkpoint_cleanup(void)
	BIO_MUST_CHECK;
struct bio_checkpoint_result bio_request_checkpoint_quiescent(void)
	BIO_MUST_CHECK;
int bio_request_settle_quiescent_cleanup(void) BIO_MUST_CHECK;
int bio_request_end_current(int);
int bio_request_end_current_cleanup(void);
void bio_request_abort_thread(struct thread *);
void bio_fs_atomic_enter(void);
void bio_fs_atomic_leave(void);
int bio_io_quiescent_current(void);
void bio_cache_retry_notify(void);
int bio_background_begin(uint);
void bio_background_end(void);
int bio_background_active(uint);
uint bio_process_owner(const struct proc *);
void bio_current_sponsor(uint *, uint *);
int bio_deferred_owner_retain(uint, uint *);
int bio_deferred_owner_retain_current(uint, uint *, uint64 *);
int bio_deferred_owner_retain_cleanup(uint, uint *);
void bio_deferred_owner_release(uint);

/* Eight-byte generation checked handle; the mutable receipt lives in BIO. */
struct bio_cleanup_token {
	uint slot;
	uint generation;
};

#define BIO_CLEANUP_TOKEN_INIT { 0 }

int bio_cleanup_token_prepare(uint, struct bio_cleanup_token *);
/* Cleanup tokens are asynchronous identities and never borrow a request. */
int bio_cleanup_token_sponsor(const struct bio_cleanup_token *, uint *, uint *);
int bio_cleanup_token_begin(struct bio_cleanup_token *);
int bio_cleanup_token_end(struct bio_cleanup_token *);
int bio_cleanup_sponsor_covers(uint, uint, uint64);
/*
 * An independent lease returns NEED_SETTLEMENT while the filesystem gate is
 * held.  Request/background reuse owns no lease and may release in the gate.
 */
#define BIO_CLEANUP_RELEASED 0
#define BIO_CLEANUP_NEEDS_SETTLEMENT 1
int bio_cleanup_token_release(struct bio_cleanup_token *, int);

/* A zero origin request id makes the sponsor strictly asynchronous. */
int bio_deferred_sponsor_begin(uint, uint, uint64);
void bio_deferred_sponsor_end(void);
int bio_deferred_polling_current(void);
enum bio_transfer_type {
	BIO_TRANSFER_READ = 0,
	BIO_TRANSFER_WRITE,
	BIO_TRANSFER_FLUSH,
};

#define BIO_PHYSICAL_STATS_VERSION 3U
struct bio_physical_stats {
	uint version;
	uint size;
	uint64 reads;
	uint64 writes;
	uint64 flushes;
	uint64 successful_writes;
	uint64 successful_flushes;
	uint64 failed_transfers;
	uint64 completion_sequence;
	uint64 victim_candidates_examined;
	uint max_victim_candidates_per_miss;
	uint reserved;
	uint64 lazy_started;
	uint64 upgraded;
	uint64 cache_only;
};

void bio_account_transfer(uint, uint, enum bio_transfer_type, int);
void bio_account_transfer_batch(uint, uint, enum bio_transfer_type,
				const int *, uint);
int bio_physical_snapshot(struct bio_physical_stats *);
uint bio_current_owner(void);
int bio_principal_bind(uint, struct resource_account_handle);
int bio_scope_acquire(uint, struct resource_account_handle);
void bio_scope_quiesce(uint);
void bio_scope_retire(uint);
int bio_policy_snapshot(const struct proc *, struct io_policy_info *);

#endif // BUF_H
