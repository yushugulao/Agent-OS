#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_recovery.h"
#include "riscv.h"
#include "timer.h"

#define AGENT_META_BOOT_REPROBE_MAX_TICKS TICKS_PER_SEC
#define AGENT_META_BOOT_REPROBE_MAX_SHIFT 6U

struct agent_metadata_recovery_state {
	int pending;
	uint failures;
	uint64 retry_tick;
};

static struct agent_metadata_recovery_state recovery;

void
agent_metadata_recovery_init(void)
{
	recovery = (struct agent_metadata_recovery_state){0};
}

int
agent_metadata_recovery_retryable(int status)
{
	return status == AGENT_METADATA_LOAD_INTERRUPTED ||
	       status == AGENT_METADATA_LOAD_BUSY ||
	       status == AGENT_METADATA_LOAD_IO ||
	       status == AGENT_METADATA_LOAD_PROGRESS;
}

int
agent_metadata_recovery_defer(int status, uint64 now)
{
	int enabled;

	if (!agent_metadata_recovery_retryable(status))
		return -1;
	enabled = intr_save();
	recovery.pending = 1;
	recovery.failures = 0;
	recovery.retry_tick = now;
	intr_restore(enabled);
	return 0;
}

void
agent_metadata_recovery_cancel(void)
{
	int enabled = intr_save();

	recovery = (struct agent_metadata_recovery_state){0};
	intr_restore(enabled);
}

int
agent_metadata_recovery_pending(void)
{
	int enabled = intr_save();
	int pending = recovery.pending;

	intr_restore(enabled);
	return pending;
}

int
agent_metadata_recovery_due(uint64 now)
{
	int enabled = intr_save();
	int due = recovery.pending && (long)(now - recovery.retry_tick) >= 0;

	intr_restore(enabled);
	return due;
}

int
agent_metadata_recovery_complete(int status, uint64 now, uint *failures,
				 uint64 *deadline)
{
	int outcome = AGENT_METADATA_RECOVERY_RETRY;
	int enabled = intr_save();
	uint observed_failures = recovery.failures;

	if (deadline != 0)
		*deadline = 0;
	if (!recovery.pending)
		goto out;
	if (status >= 0 || !agent_metadata_recovery_retryable(status)) {
		outcome = status >= 0 ? AGENT_METADATA_RECOVERY_READY :
			  AGENT_METADATA_RECOVERY_PERMANENT;
		recovery.pending = 0;
		if (status >= 0) {
			recovery.failures = 0;
			recovery.retry_tick = 0;
		}
		goto out;
	}
	if (status == AGENT_METADATA_LOAD_PROGRESS) {
		recovery.retry_tick = now == ~0ULL ? now : now + 1;
		goto out;
	}
	if (recovery.failures != ~0U)
		recovery.failures++;
	observed_failures = recovery.failures;
	uint shift = recovery.failures < AGENT_META_BOOT_REPROBE_MAX_SHIFT ?
			     recovery.failures : AGENT_META_BOOT_REPROBE_MAX_SHIFT;
	uint64 delay = 1ULL << shift;
	if (delay > AGENT_META_BOOT_REPROBE_MAX_TICKS)
		delay = AGENT_META_BOOT_REPROBE_MAX_TICKS;
	recovery.retry_tick = now > ~0ULL - delay ? ~0ULL : now + delay;
out:
	if (failures != 0)
		*failures = observed_failures;
	if (deadline != 0 && outcome == AGENT_METADATA_RECOVERY_RETRY)
		*deadline = recovery.retry_tick;
	intr_restore(enabled);
	return outcome;
}
