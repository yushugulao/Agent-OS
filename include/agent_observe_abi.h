#ifndef AGENT_OBSERVE_ABI_H
#define AGENT_OBSERVE_ABI_H

#define AGENT_AUDIT_LOW_PRINCIPAL_MAX 16U

#define AGENT_AUDIT_RECEIPT_VERSION 1U
#define AGENT_AUDIT_RECEIPT_STATUS 1U
#define AGENT_AUDIT_RECEIPT_WAIT   2U
#define AGENT_AUDIT_RECEIPT_F_NONE 0U

#define AGENT_AUDIT_DURABILITY_NOT_FOUND 0U
#define AGENT_AUDIT_DURABILITY_PENDING   1U
#define AGENT_AUDIT_DURABILITY_FENCE_SEALED 2U
#define AGENT_AUDIT_DURABILITY_FAILED    3U
#define AGENT_AUDIT_RECEIPT_WAIT_MAX_TICKS 1000

/*
 * 肯定的证据证明只能是 (OK, FENCE_SEALED, supplied receipt)。它表示记录
 * 已进入 challenge-bound workflow fence root，不表示元数据或日志已落盘。
 * 精确保留绑定可报告 (OK, FAILED, supplied receipt)。有界绑定消失后，非零回执返回
 * (STALE, NOT_FOUND, 0)，但不能证明令牌曾存在；零回执发现返回
 * (NOT_FOUND, NOT_FOUND, 0)。
 */

struct agent_audit_receipt_request {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long sequence;
	unsigned long long record_hash;
	unsigned long long receipt_id;
	int timeout_ticks;
	unsigned int durability;
	int status;
	unsigned int reserved;
};

_Static_assert(sizeof(struct agent_audit_receipt_request) == 72,
	       "audit durability receipt ABI layout");

#endif
