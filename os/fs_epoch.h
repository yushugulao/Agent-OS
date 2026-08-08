#ifndef FS_EPOCH_H
#define FS_EPOCH_H

#include "types.h"

struct buf;

/*
 * epoch 按持久化顺序发布文件系统修改。调用者先用
 * fs_epoch_note_data() 绑定存储主体，再登记各缓冲区的最终镜像。
 * 前台 I/O 只能由开启 epoch 的同一顶层请求复用。
 */
enum fs_epoch_phase {
	FS_EPOCH_PREPARE = 0,
	FS_EPOCH_NAMESPACE_DETACH,
	FS_EPOCH_INODE,
	FS_EPOCH_NAMESPACE_ATTACH,
	FS_EPOCH_PHASE_COUNT,
};

#define FS_EPOCH_BUFFER_CAP 48U
#define FS_EPOCH_HIGH_WATER 36U
#define FS_EPOCH_MAX_AGE_TICKS 2U

enum fs_epoch_status {
	FS_EPOCH_ERROR = -1,
	FS_EPOCH_OWNER_MISMATCH = -2,
	FS_EPOCH_FULL = -3,
	FS_EPOCH_SYNC_REQUIRED = 0,
	FS_EPOCH_CACHED = 1,
};

#define FS_EPOCH_STATS_VERSION 3U
struct fs_epoch_stats {
	uint version;
	uint size;
	uint runtime_enabled;
	uint dirty;
	uint owner;
	uint pinned_buffers;
	uint phase_buffers[FS_EPOCH_PHASE_COUNT];
	uint data_notices;
	uint request_depth;
	uint request_waiters;
	uint bypass_depth;
	uint committing;
	uint64 active_generation;
	uint64 committed_generation;
	uint64 deadline_cycle;
	uint64 staged_buffers;
	uint64 deduplicated_stages;
	uint64 owner_conflicts;
	uint64 capacity_rejections;
	uint64 commit_attempts;
	uint64 successful_commits;
	uint64 failed_commits;
	uint64 metadata_writes;
	uint64 durable_flushes;
	uint64 forward_busy_retries;
	uint64 request_acquisitions;
	uint64 request_contentions;
	int last_error;
	uint max_lookup_probes;
};

void fs_epoch_init(void);
void fs_epoch_runtime_enable(void);
int fs_epoch_runtime_enabled(void);

/*
 * epoch 持有缓冲区引用时返回 CACHED；运行期启用前或旁路区间返回
 * SYNC_REQUIRED；失败返回负值。同一缓冲区再次修改后必须重新登记。
 */
int fs_epoch_stage(struct buf *, enum fs_epoch_phase);

/* 绑定唯一持久化主体，并记录必须先于 INODE 落盘的数据。 */
int fs_epoch_note_data(uint owner);

/*
 * 首次写入发布前遇到 BUSY 可直接返回，不结算债务；发布开始后只能向前
 * 完成，失败时保留全部引用。请求债务归外层 I/O 租约，调用者应先释放
 * epoch 请求门，再结束租约。
 */
int fs_epoch_commit(void);
int fs_epoch_commit_polling(void);
int fs_epoch_prepare_cleanup_sponsor(uint, uint);
int fs_epoch_should_commit(void);
int fs_epoch_dirty(void);
int fs_epoch_buffer_dirty(uint dev, uint blockno);

/* 修改即将跨主体、容量或时限边界时先提交。 */
int fs_epoch_reserve(uint owner, uint worst_case_buffers);

/*
 * 目录解绑与挂接镜像不能共用 epoch：缓存只保留最新镜像，而两者的
 * 崩溃安全发布顺序相反。修改目录缓冲区前先预留命名空间阶段，迫使
 * 不兼容的 epoch 提前提交。
 */
int fs_epoch_reserve_phase(uint owner, uint worst_case_buffers,
			   enum fs_epoch_phase);

/* 延迟回收必须晚于解除最后一个引用的 epoch。 */
int fs_epoch_generation_fence(uint owner, uint64 *generation);
int fs_epoch_generation_committed(uint64 generation);

/* 文件系统修改者使用可休眠的公平全局串行门。 */
int fs_epoch_request_begin(void);
void fs_epoch_request_end(void);
int fs_epoch_request_held(void);

/* 破坏性路径先提交，再进入可嵌套的原始写区间。 */
int fs_epoch_bypass_begin(void);
void fs_epoch_bypass_end(void);
int fs_epoch_bypass_active(void);

void fs_epoch_stats_snapshot(struct fs_epoch_stats *);

#endif // FS_EPOCH_H
