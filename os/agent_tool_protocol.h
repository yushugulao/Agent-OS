#ifndef AGENT_TOOL_PROTOCOL_H
#define AGENT_TOOL_PROTOCOL_H

#include "../agent_tool_abi.h"
#include "riscv.h"
#include "types.h"

struct agent_tool_match {
	int tool_id;
	uint64 flags;
	char name[AGENT_TOOL_NAME_SIZE];
};

void agent_tool_protocol_init(void);
uint64 agent_tool_protocol_flags(int tool_id);
int agent_tool_protocol_resolve(int tool_id, char *name,
				struct agent_tool_match *match,
				char *error, int error_size);
int agent_tool_protocol_decode_v1(struct agent_request *request,
				  struct agent_tool_match *match,
				  struct agent_op *op, char *error,
				  int error_size);
int agent_tool_protocol_decode_v2(pagetable_t pagetable,
				  struct agent_request_v2 *request,
				  struct agent_tool_match *match,
				  struct agent_op *op, char *error,
				  int error_size);
int agent_tool_protocol_list_v1(pagetable_t pagetable, uint64 address,
				int max);
int agent_tool_protocol_list_v2(pagetable_t pagetable, uint64 address,
				int max, uint desc_size, uint version);

#endif
