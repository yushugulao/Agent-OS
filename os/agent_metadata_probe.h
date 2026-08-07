#ifndef AGENT_METADATA_PROBE_H
#define AGENT_METADATA_PROBE_H

#include "agent_metadata_internal.h"
#include "agent_metadata_journal.h"
#include "agent_metadata_store_format.h"

#define AGENT_META_BANK_VALID 0
#define AGENT_META_BANK_ABSENT 1
#define AGENT_META_BANK_CORRUPT AGENT_METADATA_LOAD_CORRUPT
#define AGENT_META_BANK_INTERRUPTED AGENT_METADATA_LOAD_INTERRUPTED
#define AGENT_META_BANK_BUSY AGENT_METADATA_LOAD_BUSY
#define AGENT_META_BANK_IO AGENT_METADATA_LOAD_IO
#define AGENT_META_BANK_PROGRESS AGENT_METADATA_LOAD_PROGRESS
#define AGENT_META_BANK_UNCOMMITTED 2

struct agent_metadata_probe_key {
	uint64 authority_cookie;
	uint64 store_epoch;
	uint reload_scope;
	uint workflow_lifecycle_id;
	uint64 workflow_lifecycle_generation;
	int force;
};

void agent_metadata_probe_init(void);
void agent_metadata_probe_reset(void);
void agent_metadata_probe_invalidate(const struct agent_metadata_probe_key *);
uint64 agent_metadata_probe_epoch(void);
int agent_metadata_probe_summary(const struct agent_metadata_probe_key *, int,
				 struct agent_meta_store *, uint64 *, uint64 *,
				 int *, int);
int agent_metadata_probe_confirm(const struct agent_metadata_probe_key *, int,
				 struct agent_meta_store *, uint64, uint64, int);
void agent_metadata_probe_finish(uint64);
void agent_metadata_probe_catalog_progress(int, uint);
#ifdef AGENT_METADATA_BOOT_READ_FAULT
void agent_metadata_probe_progress(uint64 *, int *, uint *, uint *);
#endif
int agent_metadata_probe_journal_cursor(
	int, struct agent_meta_journal_cursor *);

#endif
