#ifndef AGENT_METADATA_STORE_IO_H
#define AGENT_METADATA_STORE_IO_H

#include "agent_metadata_internal.h"
#include "agent_metadata_store_format.h"

void agent_meta_store_io_init(void);
int agent_meta_store_io_enter(void);
void agent_meta_store_io_leave(void);
int agent_meta_store_io_owned(void);
char *agent_meta_store_io_name(int);
struct inode *agent_meta_store_io_lookup_bank(char *, int, int *);

#endif
