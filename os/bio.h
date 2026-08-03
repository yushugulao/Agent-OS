#ifndef BUF_H
#define BUF_H

#include "fs.h"
#include "types.h"
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
	int lru_promote;
	int transient;
	int background_reserved;
	void *holder;
	uint hold_depth;
	struct wait_queue holder_waiters;
	struct buf *hash_next;
	struct buf *prev; // LRU cache list
	struct buf *next;
	uchar data[BSIZE];
};

void binit(void);
int bread(uint, uint, struct buf **) BIO_MUST_CHECK;
int bread_device(uint, uint, struct buf **) BIO_MUST_CHECK;
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
int bio_background_begin(uint);
void bio_background_end(void);
int bio_background_active(uint);
void bio_current_sponsor(uint *, uint *);
enum bio_transfer_type {
	BIO_TRANSFER_READ = 0,
	BIO_TRANSFER_WRITE,
	BIO_TRANSFER_FLUSH,
};

#define BIO_PHYSICAL_STATS_VERSION 1U
struct bio_physical_stats {
	uint version;
	uint size;
	uint64 reads;
	uint64 writes;
	uint64 flushes;
	uint64 failed_transfers;
	uint64 completion_sequence;
};

void bio_account_transfer(uint, uint, enum bio_transfer_type, int);
int bio_physical_snapshot(struct bio_physical_stats *);
uint bio_current_owner(void);
int bio_principal_bind(uint, struct resource_account_handle);
int bio_scope_acquire(uint, struct resource_account_handle);
void bio_scope_quiesce(uint);
void bio_scope_retire(uint);
int bio_policy_snapshot(const struct proc *, struct io_policy_info *);

#endif // BUF_H
