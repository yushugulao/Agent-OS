#include "proc.h"

unsigned char kernel_budget_struct_proc[sizeof(struct proc)];
unsigned char kernel_budget_kernel_stack_virtual_capacity
	[KSTACK_VIRTUAL_CAPACITY_BYTES];
unsigned char kernel_budget_kernel_stack_reserved_physical_pool
	[KSTACK_RESERVED_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_context_sidecar_per_process
	[AGENT_CONTEXT_SIDECAR_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_context_sidecar_pool
	[NPROC * AGENT_CONTEXT_SIDECAR_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_context_sidecar_ordinary_pool
	[PROC_ORDINARY_SLOTS * AGENT_CONTEXT_SIDECAR_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_context_sidecar_reserved_pool
	[PROC_RESERVED_SLOTS * AGENT_CONTEXT_SIDECAR_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_context_sidecar_domain_ordinary
	[PROC_RESOURCE_DOMAIN_LIMIT * AGENT_CONTEXT_SIDECAR_PAGE_COUNT *
	 PAGE_SIZE];
unsigned char kernel_budget_agent_context_sidecar_domain_reserved
	[PROC_RESOURCE_DOMAIN_RESERVED_LIMIT *
	 AGENT_CONTEXT_SIDECAR_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_state_total_per_process
	[AGENT_STATE_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_state_total_pool
	[NPROC * AGENT_STATE_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_state_total_ordinary_pool
	[PROC_ORDINARY_SLOTS * AGENT_STATE_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_state_total_reserved_pool
	[PROC_RESERVED_SLOTS * AGENT_STATE_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_state_total_domain_ordinary
	[PROC_RESOURCE_DOMAIN_LIMIT * AGENT_STATE_PAGE_COUNT * PAGE_SIZE];
unsigned char kernel_budget_agent_state_total_domain_reserved
	[PROC_RESOURCE_DOMAIN_RESERVED_LIMIT * AGENT_STATE_PAGE_COUNT *
	 PAGE_SIZE];
