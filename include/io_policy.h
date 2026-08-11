#ifndef IO_POLICY_H
#define IO_POLICY_H

#define IO_POLICY_VERSION 6U

#define IO_POLICY_OWNER_SYSTEM 1U
#define IO_POLICY_OWNER_PUBLIC 2U
#define IO_POLICY_OWNER_SCOPE_FLAG 0x80000000U

enum io_policy_class {
	IO_POLICY_CLASS_NORMAL = 0,
	IO_POLICY_CLASS_CONTROL = 1,
	IO_POLICY_CLASS_SYSTEM = 2,
	IO_POLICY_CLASS_BACKGROUND = 3,
	IO_POLICY_CLASS_COUNT = 4,
};

/*
 * credits 计量设备已提交的 1 KiB 块传输与持久性 flush，完成失败也计费；
 * 能力和范围拒绝未到达设备，单独作为逻辑请求证据。层级 profile 展平为
 * 受保护的所有者切片：PUBLIC 一个普通切片，活动 workflow 有普通、控制、
 * 后台切片，退役 workflow 只留清理后台切片，SYSTEM 有系统和后台切片。
 * 各保障总和低于设备包络；共享池只是机会型门，每次仍消耗设备根额度，
 * 因而保持工作守恒且不增加物理 I/O。
 */
#define IO_POLICY_PUBLIC_NORMAL_BURST 32U
#define IO_POLICY_PUBLIC_NORMAL_REFILL 16U
#define IO_POLICY_WORKFLOW_NORMAL_BURST 24U
#define IO_POLICY_WORKFLOW_NORMAL_REFILL 12U
#define IO_POLICY_WORKFLOW_CONTROL_BURST 48U
#define IO_POLICY_WORKFLOW_CONTROL_REFILL 24U
#define IO_POLICY_WORKFLOW_BACKGROUND_BURST 8U
#define IO_POLICY_WORKFLOW_BACKGROUND_REFILL 4U
#define IO_POLICY_SYSTEM_BURST 96U
#define IO_POLICY_SYSTEM_REFILL 48U
#define IO_POLICY_SYSTEM_BACKGROUND_BURST 16U
#define IO_POLICY_SYSTEM_BACKGROUND_REFILL 8U
#define IO_POLICY_SHARED_BURST 560U
#define IO_POLICY_SHARED_REFILL 280U
#define IO_POLICY_DEVICE_BURST 560U
#define IO_POLICY_DEVICE_REFILL 280U

#define IO_CACHE_SYSTEM_FLOOR 40U
#define IO_CACHE_PUBLIC_FLOOR 24U
#define IO_CACHE_WORKFLOW_FLOOR 36U
#define IO_CACHE_SYSTEM_CAP 96U
#define IO_CACHE_PUBLIC_CAP 48U
#define IO_CACHE_WORKFLOW_CAP 64U

struct io_policy_info {
	unsigned int version;
	unsigned int struct_size;
	unsigned int owner;
	unsigned int io_class;
	unsigned int tokens;
	unsigned int debt;
	unsigned int waiters;
	unsigned int cache_resident;
	unsigned int cache_floor;
	unsigned int cache_cap;
	unsigned int shared_tokens;
	unsigned int leased;
	unsigned int shared_leased;
	unsigned int class_burst;
	unsigned int class_refill;
	unsigned int device_burst;
	unsigned int device_refill;
	unsigned int device_tokens;
	unsigned int device_debt;
	unsigned int device_leased;
	unsigned int admission_waiters;
	unsigned int debt_waiters;
	unsigned int admission_granted;
	unsigned long long admissions;
	unsigned long long throttles;
	unsigned long long waits;
	unsigned long long refills;
	unsigned long long reserved_grants;
	unsigned long long shared_grants;
	unsigned long long physical_reads;
	unsigned long long physical_writes;
	unsigned long long cache_hits;
	unsigned long long cache_misses;
	unsigned long long cache_evictions;
	unsigned long long unreserved_transfers;
	unsigned long long completion_sequence;
	unsigned long long physical_flushes;
	unsigned long long failed_transfers;
	unsigned long long lazy_started;
	unsigned long long upgraded;
	unsigned long long cache_only;
};

#endif // IO_POLICY_H
