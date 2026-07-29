#ifndef AGENT_CONTEXT_H
#define AGENT_CONTEXT_H

#include "types.h"

struct agent_context_detail;
struct agent_context_record;
struct agent_op;
struct agent_result;
struct proc;

int agent_context_alloc(struct proc *);
void agent_context_free(struct proc *);
void agent_context_proc_activate(struct proc *);
void agent_context_proc_reset(struct proc *);
int agent_context_clear(struct proc *);
int agent_context_store(struct proc *, uint64,
			const struct agent_context_detail *, uint64, int,
			uint64);
int agent_context_load_detail(struct proc *, uint64,
			      struct agent_context_detail *);
int agent_context_load_attribution(struct proc *, uint64, uint64 *, int *,
				   uint64 *, uint64 *);
int agent_context_load_actor(struct proc *, uint64, int *, int *, int *);
int agent_context_is_empty(const struct proc *);
int agent_context_map(struct proc *);
int agent_context_init(struct proc *);
int agent_context_read_record(struct proc *, uint64,
			      struct agent_context_record *);
int agent_context_append_prepare(struct proc *, uint64);
int agent_context_append(struct proc *, struct agent_op *,
			 struct agent_result *, uint64, int);
int agent_context_append_system(struct proc *, int, uint64, uint64, char *,
				char *, int, uint64, uint64, uint64);
int agent_context_append_system_causal(struct proc *, int, uint64, uint64,
				       char *, char *, int, uint64, uint64,
				       uint64);

#endif
