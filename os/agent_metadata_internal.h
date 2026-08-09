#ifndef AGENT_METADATA_INTERNAL_H
#define AGENT_METADATA_INTERNAL_H

#include "agent_metadata_catalog.h"

int agent_metadata_inode_trackable(struct inode *);
void agent_metadata_note_catalog_changes(uint);

#endif
