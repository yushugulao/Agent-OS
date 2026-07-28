#ifndef AGENT_METADATA_RECOVERY_H
#define AGENT_METADATA_RECOVERY_H

#include "types.h"

#define AGENT_METADATA_RECOVERY_PERMANENT (-1)
#define AGENT_METADATA_RECOVERY_RETRY 0
#define AGENT_METADATA_RECOVERY_READY 1

void agent_metadata_recovery_init(void);
int agent_metadata_recovery_retryable(int);
int agent_metadata_recovery_defer(int, uint64);
void agent_metadata_recovery_cancel(void);
int agent_metadata_recovery_pending(void);
int agent_metadata_recovery_due(uint64);
int agent_metadata_recovery_complete(int, uint64, uint *, uint64 *);

#endif
