#ifndef AGENT_METADATA_SCAN_H
#define AGENT_METADATA_SCAN_H

#include "agent_metadata_catalog.h"

#define AGENT_METADATA_SCAN_IDLE 0
#define AGENT_METADATA_SCAN_START 1
#define AGENT_METADATA_SCAN_CONTINUE 2

void agent_metadata_scan_init(void);
void agent_metadata_scan_catalog_sync(const struct agent_catalog_delta *);
int agent_metadata_scan_plan(uint64);
uint agent_metadata_scan_step(uint64, int, int);
uint agent_metadata_scan_apply_defaults(struct agent_file_meta *, char *, int *);
void agent_metadata_scan_note_slot(int);
int agent_metadata_scan_query_stable(void);
void agent_metadata_scan_fill_info(struct agent_info *);

#endif
