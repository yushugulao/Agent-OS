#ifndef METADATA_CRASH_TEST_H
#define METADATA_CRASH_TEST_H

#include "types.h"

#if defined(AGENT_METADATA_CRASH_PHASE) || defined(AGENT_METADATA_EIO_PHASE)
void agent_metadata_test_init(void);
#else
static inline void agent_metadata_test_init(void) {}
#endif

#ifdef AGENT_METADATA_EIO_PHASE
void agent_metadata_test_eio_start(uint, uint64);
void agent_metadata_test_eio_cancel(uint, uint64);
void agent_metadata_test_eio_pre_io(uint, uint64, int, uint);
void agent_metadata_test_eio_commit(uint, uint64);
#else
static inline void agent_metadata_test_eio_start(uint scope_id, uint64 job_id)
{
	(void)scope_id;
	(void)job_id;
}
static inline void agent_metadata_test_eio_cancel(uint scope_id, uint64 job_id)
{
	(void)scope_id;
	(void)job_id;
}
static inline void
agent_metadata_test_eio_pre_io(uint scope_id, uint64 job_id, int mirroring,
			       uint phase)
{
	(void)scope_id;
	(void)job_id;
	(void)mirroring;
	(void)phase;
}
static inline void agent_metadata_test_eio_commit(uint scope_id, uint64 job_id)
{
	(void)scope_id;
	(void)job_id;
}
#endif

#ifdef AGENT_METADATA_CRASH_PHASE
void agent_metadata_test_bind(uint, uint64, uint64);
void agent_metadata_test_checkpoint(uint, uint64, int, uint);
#else
static inline void
agent_metadata_test_bind(uint scope_id, uint64 generation, uint64 job_id)
{
	(void)scope_id;
	(void)generation;
	(void)job_id;
}

static inline void
agent_metadata_test_checkpoint(uint scope_id, uint64 job_id, int mirroring,
			       uint phase)
{
	(void)scope_id;
	(void)job_id;
	(void)mirroring;
	(void)phase;
}
#endif

#endif
