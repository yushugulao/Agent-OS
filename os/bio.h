#ifndef BUF_H
#define BUF_H

#include "fs.h"
#include "types.h"
#include "wait.h"
#include "../io_policy.h"

struct proc;
struct thread;

#define BIO_CHECKPOINT_INTERRUPTED (-1)
#define BIO_CHECKPOINT_DEFERRED (-2)

struct buf {
	int valid; // has data been read from disk?
	int disk; // does disk "own" buf?
	uint dev;
	uint blockno;
	uint refcnt;
	uint cache_owner;
	int lru_promote;
	int transient;
	int background_reserved;
	void *holder;
	uint hold_depth;
	struct wait_queue holder_waiters;
	struct buf *prev; // LRU cache list
	struct buf *next;
	uchar data[BSIZE];
};

void binit(void);
struct buf *bread(uint, uint);
void brelse(struct buf *);
void bwrite(struct buf *);
void bclaim(struct buf *);
void bpin(struct buf *);
void bunpin(struct buf *);

void bio_policy_start(void);
void bio_policy_tick(void);
int bio_request_begin_current(void);
int bio_request_begin_current_cleanup(void);
int bio_request_checkpoint(void);
int bio_request_checkpoint_cleanup(void);
int bio_request_checkpoint_quiescent(void);
int bio_request_checkpoint_quiescent_cleanup(void);
int bio_request_end_current(int);
int bio_request_end_current_cleanup(void);
void bio_request_abort_thread(struct thread *);
void bio_fs_atomic_enter(void);
void bio_fs_atomic_leave(void);
int bio_background_begin(uint);
void bio_background_end(void);
int bio_background_active(uint);
void bio_current_sponsor(uint *, uint *);
void bio_account_transfer(uint, uint, int);
uint bio_current_owner(void);
int bio_scope_acquire(uint);
void bio_scope_quiesce(uint);
void bio_scope_retire(uint);
int bio_policy_snapshot(const struct proc *, struct io_policy_info *);

#endif // BUF_H
