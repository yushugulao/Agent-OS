#include "agent.h"
#include "defs.h"
#include "exec_policy.h"
#include "kalloc.h"
#include "proc.h"
#include "resource_controller.h"

_Static_assert((int)AGENT_RESOURCE_PROCESS == (int)RESOURCE_PROCESS,
	       "resource snapshot process kind drift");
_Static_assert((int)AGENT_RESOURCE_THREAD == (int)RESOURCE_THREAD,
	       "resource snapshot thread kind drift");
_Static_assert((int)AGENT_RESOURCE_FILE_OBJECT == (int)RESOURCE_FILE_OBJECT,
	       "resource snapshot file kind drift");
_Static_assert((int)AGENT_RESOURCE_FS_BLOCK == (int)RESOURCE_FS_BLOCK,
	       "resource snapshot block kind drift");
_Static_assert((int)AGENT_RESOURCE_FS_INODE == (int)RESOURCE_FS_INODE,
	       "resource snapshot inode kind drift");
_Static_assert((int)AGENT_RESOURCE_BUFFER_CACHE == (int)RESOURCE_BUFFER_CACHE,
	       "resource snapshot cache kind drift");
_Static_assert((int)AGENT_RESOURCE_AGENT_STATE_PAGE ==
	       (int)RESOURCE_AGENT_STATE_PAGE,
	       "resource snapshot Agent state kind drift");
_Static_assert((int)AGENT_RESOURCE_PHYSICAL_PAGE == (int)RESOURCE_PHYSICAL_PAGE,
	       "resource snapshot physical page kind drift");
_Static_assert((int)AGENT_RESOURCE_KIND_COUNT == (int)RESOURCE_KIND_COUNT,
	       "resource snapshot kind count drift");

static int agent_resource_snapshot_authorized(const struct proc *p)
{
	return p != 0 && !p->is_agent && p->resource_domain_admin &&
	       exec_policy_process_bootstrap(p);
}

int sys_agent_resource_snapshot(uint64 addr, uint64 user_size)
{
	struct resource_policy_snapshot policies[RESOURCE_KIND_COUNT];
	struct agent_resource_snapshot snapshot;
	struct proc *p = curr_proc();
	uint64 copy_size;
	uint measured;
	int enabled;

	if (!agent_resource_snapshot_authorized(p))
		return AGENT_STATUS_DENIED;
	if (user_size < 2 * sizeof(unsigned int))
		return AGENT_STATUS_BAD_PARAM;
	copy_size = MIN(user_size, sizeof(snapshot));
	if (user_range_check(p->pagetable, addr, copy_size, PTE_W) < 0)
		return -1;
	memset(&snapshot, 0, sizeof(snapshot));
	/* One CPU and IRQ-off accounting mutations make this one coherent cut. */
	enabled = intr_save();
	measured = resource_policy_snapshot_all(policies, RESOURCE_KIND_COUNT);
	snapshot.ordinary_free_pages = kalloc_free_pages();
	snapshot.reserved_free_pages = kalloc_physical_reserved_free_pages();
	snapshot.stack_reserved_free_pages = kalloc_stack_reserved_free_pages();
	intr_restore(enabled);
	snapshot.version = AGENT_RESOURCE_SNAPSHOT_VERSION;
	snapshot.struct_size = sizeof(snapshot);
	snapshot.measured_mask = measured;
	snapshot.kind_count = AGENT_RESOURCE_KIND_COUNT;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct agent_resource_kind_snapshot *out = &snapshot.kinds[kind];
		const struct resource_policy_snapshot *in = &policies[kind];

		out->capacity = in->capacity;
		out->used = in->used;
		out->pending = in->pending;
		out->ordinary_used = in->ordinary_used;
		out->ordinary_pending = in->ordinary_pending;
		out->reserved_used = in->reserved_used;
		out->reserved_pending = in->reserved_pending;
	}
	if (copyout(p->pagetable, addr, (char *)&snapshot, copy_size) < 0)
		return -1;
	return AGENT_STATUS_OK;
}
