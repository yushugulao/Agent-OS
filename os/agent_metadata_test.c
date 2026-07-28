#include "metadata_crash_test.h"

#if defined(AGENT_METADATA_CRASH_PHASE) || defined(AGENT_METADATA_EIO_PHASE)
#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "exec_policy.h"
#include "vfs_security.h"
#include "virtio.h"
#include "../agent_metadata_test_abi.h"

#ifdef AGENT_METADATA_CRASH_PHASE
#ifndef AGENT_METADATA_CRASH_BANK
#define AGENT_METADATA_CRASH_BANK 0
#endif
#if AGENT_METADATA_CRASH_PHASE < 1 || AGENT_METADATA_CRASH_PHASE > 8
#error "AGENT_METADATA_CRASH_PHASE must be in [1, 8]"
#endif
#if AGENT_METADATA_CRASH_BANK != 0 && AGENT_METADATA_CRASH_BANK != 1
#error "AGENT_METADATA_CRASH_BANK must select primary(0) or mirror(1)"
#endif
#endif

#ifdef AGENT_METADATA_EIO_PHASE
#ifndef AGENT_METADATA_EIO_BANK
#define AGENT_METADATA_EIO_BANK 0
#endif
#ifndef AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS
#define AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS 0
#endif
#if AGENT_METADATA_EIO_PHASE < 1 || AGENT_METADATA_EIO_PHASE > 8
#error "AGENT_METADATA_EIO_PHASE must be in [1, 8]"
#endif
#if AGENT_METADATA_EIO_BANK != 0 && AGENT_METADATA_EIO_BANK != 1
#error "AGENT_METADATA_EIO_BANK must select primary(0) or mirror(1)"
#endif
static int eio_armed;
static uint eio_scope_id;
static uint64 eio_job_id;
static uint completed_scope_commits;
#endif

#ifdef AGENT_METADATA_CRASH_PHASE
#define AGENT_META_TEST_IDLE 0U
#define AGENT_META_TEST_ARMED 1U
#define AGENT_META_TEST_BOUND 2U

struct agent_metadata_test_target {
	uint state;
	uint scope_id;
	struct workflow_lifecycle_key lifecycle;
	uint64 baseline_generation;
	uint64 target_generation;
	uint64 arm_token;
	uint64 job_id;
};

static struct agent_metadata_test_target target;
static uint64 next_token;
#endif

void
agent_metadata_test_init(void)
{
#ifdef AGENT_METADATA_CRASH_PHASE
	memset(&target, 0, sizeof(target));
	next_token = 1;
#endif
#ifdef AGENT_METADATA_EIO_PHASE
	eio_armed = 0;
	eio_scope_id = VFS_SCOPE_NONE;
	eio_job_id = 0;
	completed_scope_commits = 0;
#endif
}

#ifdef AGENT_METADATA_CRASH_PHASE
static uint64
agent_metadata_test_next_token(void)
{
	uint64 token = next_token++;

	if (token == 0)
		token = next_token++;
	return token == 0 ? 1 : token;
}

int
sys_agent_metadata_test(uint command, uint64 armaddr, uint64 user_size)
{
	struct proc *p = curr_proc();
	struct agent_metadata_test_arm request, receipt;
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;
	uint64 baseline;
	uint64 token;
	int result = -1;

	if (command != AGENT_METADATA_TEST_ARM_NEXT ||
	    user_size != sizeof(request) || armaddr == 0 || p == 0 ||
	    !p->is_agent || p->agent_role != AGENT_ROLE_ORCHESTRATOR ||
	    !p->vfs_scope_controller || !exec_policy_process_bootstrap(p) ||
	    !exec_policy_process_allows_role(p, AGENT_ROLE_ORCHESTRATOR) ||
	    copyin(p->pagetable, (char *)&request, armaddr, sizeof(request)) < 0 ||
	    request.version != AGENT_METADATA_TEST_ABI_VERSION ||
	    request.flags != 0 || request.scope_id != 0 || request.reserved != 0 ||
	    request.lifecycle_id != 0 || request.lifecycle_generation != 0 ||
	    request.baseline_generation != 0 || request.target_generation != 0 ||
	    request.arm_token != 0)
		return -1;
	scope_id = agent_identity_proc_scope(p);
	if (!agent_scope_valid(scope_id) || !agent_metadata_txn_lock(1))
		return -1;
	if (target.state != AGENT_META_TEST_IDLE ||
	    vfs_scope_lifecycle(scope_id, &lifecycle) < 0 ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    agent_metadata_store_test_quiet_generation(scope_id, &baseline) < 0)
		goto out;
	token = agent_metadata_test_next_token();
	memset(&target, 0, sizeof(target));
	target.state = AGENT_META_TEST_ARMED;
	target.scope_id = scope_id;
	target.lifecycle = lifecycle;
	target.baseline_generation = baseline;
	target.target_generation = baseline + 1;
	target.arm_token = token;

	memset(&receipt, 0, sizeof(receipt));
	receipt.version = AGENT_METADATA_TEST_ABI_VERSION;
	receipt.flags = AGENT_METADATA_TEST_F_ARMED;
	receipt.scope_id = scope_id;
	receipt.lifecycle_id = lifecycle.id;
	receipt.lifecycle_generation = lifecycle.generation;
	receipt.baseline_generation = baseline;
	receipt.target_generation = target.target_generation;
	receipt.arm_token = token;
	if (copyout(p->pagetable, armaddr, (char *)&receipt, sizeof(receipt)) < 0) {
		memset(&target, 0, sizeof(target));
		goto out;
	}
	printf("agentmetacrash_ucore: target_armed scope=%x generation=%p token=%p\n",
	       scope_id, receipt.target_generation, token);
	result = 0;
out:
	agent_metadata_txn_unlock();
	return result;
}

void
agent_metadata_test_bind(uint scope_id, uint64 generation, uint64 job_id)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();

	if (target.state != AGENT_META_TEST_ARMED ||
	    target.scope_id != scope_id || target.target_generation != generation ||
	    job_id == 0 || vfs_scope_lifecycle(scope_id, &lifecycle) < 0 ||
	    !workflow_lifecycle_key_equal(target.lifecycle, lifecycle))
		return;
	target.state = AGENT_META_TEST_BOUND;
	target.job_id = job_id;
	printf("agentmetacrash_ucore: target_bound scope=%x generation=%p token=%p job=%p\n",
	       scope_id, generation, target.arm_token, job_id);
}

void
agent_metadata_test_checkpoint(uint scope_id, uint64 job_id, int mirroring,
			       uint phase)
{
	if (target.state != AGENT_META_TEST_BOUND || target.scope_id != scope_id ||
	    target.job_id != job_id || !!mirroring != AGENT_METADATA_CRASH_BANK ||
	    phase != AGENT_METADATA_CRASH_PHASE)
		return;
	printf("agentmetacrash_ucore: target_fire scope=%x generation=%p token=%p job=%p bank=%d phase=%d\n",
	       scope_id, target.target_generation, target.arm_token, job_id,
	       AGENT_METADATA_CRASH_BANK, phase);
	printf("agentmetacrash_ucore: metadata_phase=%d\n", phase);
	for (;;)
		__asm__ volatile("wfi");
}
#endif

#ifdef AGENT_METADATA_EIO_PHASE
void
agent_metadata_test_eio_start(uint scope_id, uint64 job_id)
{
	if (job_id == 0 || eio_job_id != 0)
		panic("metadata EIO test lifecycle");
	eio_scope_id = scope_id;
	eio_job_id = job_id;
	eio_armed = scope_id != VFS_SCOPE_SYSTEM &&
		      completed_scope_commits >=
			      AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS;
}

void
agent_metadata_test_eio_cancel(uint scope_id, uint64 job_id)
{
	if (scope_id == eio_scope_id && job_id == eio_job_id) {
		eio_armed = 0;
		eio_scope_id = VFS_SCOPE_NONE;
		eio_job_id = 0;
	}
}

void
agent_metadata_test_eio_pre_io(uint scope_id, uint64 job_id, int mirroring,
			       uint phase)
{
	if (!eio_armed || scope_id != eio_scope_id || job_id != eio_job_id ||
	    !!mirroring != AGENT_METADATA_EIO_BANK ||
	    phase != AGENT_METADATA_EIO_PHASE)
		return;
	eio_armed = 0;
	virtio_disk_test_configure(VIRTIO_DISK_TEST_FORCE_STATUS, 0,
				   VIRTIO_BLK_S_IOERR,
				   VIRTIO_DISK_REQUEST_TIMEOUT_TICKS, 0);
}

void
agent_metadata_test_eio_commit(uint scope_id, uint64 job_id)
{
	if (scope_id != eio_scope_id || job_id != eio_job_id)
		return;
	if (scope_id != VFS_SCOPE_SYSTEM && completed_scope_commits != (uint)-1)
		completed_scope_commits++;
	agent_metadata_test_eio_cancel(scope_id, job_id);
}
#endif
#endif
