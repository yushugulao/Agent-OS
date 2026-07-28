#ifndef AGENT_METADATA_RECOVERY_TEST_H
#define AGENT_METADATA_RECOVERY_TEST_H

#include "types.h"

#if defined(AGENT_METADATA_BOOT_READ_FAULT) || \
	defined(AGENT_METADATA_SELECT_FAULT_BANK)
void agent_metadata_recovery_test_init(void);
int agent_metadata_recovery_test_fault(int, int);
#else
static inline void agent_metadata_recovery_test_init(void) {}
static inline int
agent_metadata_recovery_test_fault(int bank, int allowed)
{
	(void)bank;
	(void)allowed;
	return 0;
}
#endif

#ifdef AGENT_METADATA_BOOT_READ_FAULT
void agent_metadata_recovery_test_retry(int, uint, uint64, uint64);
void agent_metadata_recovery_test_admission(int);
#else
static inline void
agent_metadata_recovery_test_retry(int status, uint failures, uint64 now,
				   uint64 deadline)
{
	(void)status;
	(void)failures;
	(void)now;
	(void)deadline;
}
static inline void agent_metadata_recovery_test_admission(int status)
{
	(void)status;
}
#endif

#endif
