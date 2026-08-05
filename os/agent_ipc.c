#include "agent_context.h"
#include "agent_internal.h"
#include "defs.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"
#ifdef WAIT_ATOMIC_TEST_PROFILE
#include "wait_atomic_test.h"
#endif

extern struct proc pool[NPROC];

static uint64 next_legacy_endpoint_generation;

static void
agent_ipc_thread_state_clear_locked(struct thread *t, int state)
{
	t->agent_wait_deadline = 0;
	t->agent_wait_deadline_valid = 0;
	t->agent_loop_state = state;
}

void
agent_ipc_thread_runtime_transition(struct thread *t, int transition)
{
	struct proc *p;
	int enabled = intr_save();

	if (t == 0 || (p = t->process) == 0)
		goto out;
	if (transition != AGENT_THREAD_RUNTIME_ACTIVATE &&
	    transition != AGENT_THREAD_RUNTIME_RELEASE)
		panic("Agent thread runtime transition");
	if (transition == AGENT_THREAD_RUNTIME_ACTIVATE &&
	    t->identity_generation == 0)
		panic("Agent thread activation state");
	agent_ipc_thread_state_clear_locked(
		t, transition != AGENT_THREAD_RUNTIME_RELEASE && p->is_agent ?
			AGENT_LOOP_IDLE : AGENT_LOOP_NONE);
	agent_identity_loop_refresh_locked(p);
out:
	intr_restore(enabled);
}

void
agent_ipc_process_image_install_locked(struct proc *p)
{
	if (intr_get())
		panic("Agent image install unlocked");
	for (int tid = 0; tid < NTHREAD; tid++)
		agent_ipc_thread_state_clear_locked(&p->threads[tid],
						    AGENT_LOOP_NONE);
	agent_identity_loop_refresh_locked(p);
}

struct agent_legacy_domain {
	enum agent_legacy_public_kind {
		AGENT_LEGACY_PUBLIC_INVALID = 0,
		AGENT_LEGACY_PUBLIC_ORDINARY,
		AGENT_LEGACY_PUBLIC_WORKFLOW,
	} kind;
	struct resource_account_handle account;
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;
	uint64 lineage_id;
	uint64 endpoint_generation;
};

struct agent_legacy_mailbox {
	char *payload_page;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	struct agent_legacy_domain domain;
	uint64 next_read_token;
	uint64 read_token;
	int len[MAILBOX_SLOT_COUNT];
	int from[MAILBOX_SLOT_COUNT];
	int head;
	int tail;
	int count;
};

_Static_assert(MAILBOX_SLOT_COUNT * MAILBOX_PAYLOAD_SIZE == PGSIZE,
	       "legacy mailbox payload must occupy exactly one page");
_Static_assert(sizeof(struct agent_legacy_mailbox) <= PGSIZE,
	       "legacy mailbox metadata must fit in one page");

#define AGENT_IPC_PROC_STATE_BYTES \
	(__builtin_offsetof(struct proc, agent_last_heartbeat_tick) + \
	 sizeof(((struct proc *)0)->agent_last_heartbeat_tick) - \
	 __builtin_offsetof(struct proc, agent_mailbox_valid))

void
agent_ipc_init(void)
{
	next_legacy_endpoint_generation = 1;
}

static uint64
agent_ipc_legacy_endpoint_alloc_locked(void)
{
	uint64 generation;

	if (intr_get())
		panic("legacy endpoint allocation unlocked");
	generation = next_legacy_endpoint_generation;
	if (generation == 0 || generation == ~0ULL) {
		next_legacy_endpoint_generation = 0;
		return 0;
	}
	next_legacy_endpoint_generation = generation + 1;
	return generation;
}

static struct proc *
agent_ipc_find_live_proc_locked(int pid, int agent_endpoint)
{
	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (proc_teardown_live(p) && p->pid == pid &&
		    !!p->is_agent == !!agent_endpoint)
			return p;
	return 0;
}

static int
agent_ipc_same_scope(struct proc *left, struct proc *right)
{
	uint scope;

	if (left == 0 || right == 0)
		return 0;
	scope = agent_identity_proc_scope(left);
	return scope != VFS_SCOPE_NONE &&
	       scope == agent_identity_proc_scope(right);
}

static int
agent_ipc_legacy_domain_locked(const struct proc *p,
			       struct agent_legacy_domain *domain)
{
	uint64 root_controller = 0;
	uint scope_id;

	memset(domain, 0, sizeof(*domain));
	domain->account = resource_account_none();
	domain->lifecycle = workflow_lifecycle_none();
	if (!proc_teardown_live(p) || p->is_agent ||
	    p->legacy_mail_endpoint_generation == 0 ||
	    !resource_account_handle_valid(p->resource_account) ||
	    !resource_account_active(p->resource_account))
		return -1;
	domain->account = p->resource_account;
	domain->endpoint_generation =
		p->legacy_mail_endpoint_generation;
	if (!p->workflow_lifecycle_charged) {
		if (p->workflow_lifecycle_id != WORKFLOW_LIFECYCLE_ID_NONE ||
		    p->workflow_lifecycle_generation != 0 ||
		    p->vfs_scope_id != VFS_SCOPE_NONE ||
		    p->vfs_pending_scope_id != VFS_SCOPE_NONE ||
		    p->agent_controller_id != 0 ||
		    p->storage_principal_id != FS_OWNER_PUBLIC)
			return -1;
	} else {
		domain->lifecycle = vfs_proc_lifecycle(p);
		if (!workflow_lifecycle_key_valid(domain->lifecycle) ||
		    !workflow_lifecycle_active(domain->lifecycle) ||
		    workflow_lifecycle_scope(domain->lifecycle, &scope_id) < 0 ||
		    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    !vfs_scope_active(scope_id) ||
		    (p->vfs_scope_id != VFS_SCOPE_NONE &&
		     p->vfs_scope_id != scope_id) ||
		    (p->vfs_pending_scope_id != VFS_SCOPE_NONE &&
		     p->vfs_pending_scope_id != scope_id) ||
		    workflow_lifecycle_controller(domain->lifecycle, scope_id,
						  &root_controller) < 0)
			return -1;
		domain->scope_id = scope_id;
		if (p->agent_controller_id != 0) {
			/* The nearest signed control edge defines IPC lineage. */
			if (!agent_identity_controller_active_locked(
				p->agent_controller_id, domain->lifecycle,
				scope_id))
				return -1;
		} else if (root_controller != 0 ||
			   p->storage_principal_id != FS_OWNER_PUBLIC ||
			   p->vfs_scope_id != VFS_SCOPE_NONE ||
			   p->vfs_pending_scope_id != VFS_SCOPE_NONE) {
			/* Controller-free system lifecycles host ordinary mail. */
			return -1;
		}
	}
	domain->kind = p->agent_controller_id != 0 ?
		AGENT_LEGACY_PUBLIC_WORKFLOW : AGENT_LEGACY_PUBLIC_ORDINARY;
	domain->lineage_id = p->agent_controller_id != 0 ?
		p->agent_controller_id : p->resource_account.generation;
	return 0;
}

static int
agent_ipc_legacy_domain_equal(const struct agent_legacy_domain *left,
			      const struct agent_legacy_domain *right)
{
	return left->kind != AGENT_LEGACY_PUBLIC_INVALID &&
	       left->kind == right->kind &&
	       resource_account_handle_equal(left->account, right->account) &&
	       workflow_lifecycle_key_equal(left->lifecycle,
					    right->lifecycle) &&
	       left->scope_id == right->scope_id &&
	       left->lineage_id != 0 &&
	       left->lineage_id == right->lineage_id;
}

static int
agent_ipc_legacy_mailbox_domain_matches(
	const struct agent_legacy_domain *snapshot,
	const struct agent_legacy_domain *current)
{
	return agent_ipc_legacy_domain_equal(snapshot, current) &&
	       snapshot->endpoint_generation != 0 &&
	       snapshot->endpoint_generation == current->endpoint_generation;
}

static int
agent_ipc_legacy_public_pair_locked(const struct proc *source,
				    const struct proc *target,
				    struct agent_legacy_domain *target_domain)
{
	struct agent_legacy_domain source_domain;

	return agent_ipc_legacy_domain_locked(source, &source_domain) == 0 &&
	       agent_ipc_legacy_domain_locked(target, target_domain) == 0 &&
	       agent_ipc_legacy_domain_equal(&source_domain, target_domain);
}

static struct agent_legacy_mailbox *
agent_ipc_legacy_mailbox_alloc_locked(
	struct proc *target, const struct agent_legacy_domain *domain)
{
	struct agent_legacy_mailbox *mailbox;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
	char *payload_page;

	if (target->mail_sidecar != 0)
		return target->mail_sidecar;
	if (domain == 0 ||
	    !resource_account_handle_equal(domain->account,
					  target->resource_account) ||
	    domain->endpoint_generation !=
		    target->legacy_mail_endpoint_generation ||
	    !resource_account_active(target->resource_account))
		return 0;
	account = target->resource_account;
	charge_class = target->resource_slot_reserved ?
			       RESOURCE_CHARGE_RESERVED :
			       RESOURCE_CHARGE_ORDINARY;
	payload_page = kalloc_account_page(account, charge_class);
	if (payload_page == 0)
		return 0;
	mailbox = kalloc_account_page(account, charge_class);
	if (mailbox == 0) {
		(void)kfree_account_page(payload_page, account, charge_class);
		return 0;
	}
	memset(payload_page, 0, PGSIZE);
	memset(mailbox, 0, PGSIZE);
	mailbox->payload_page = payload_page;
	mailbox->account = account;
	mailbox->charge_class = charge_class;
	mailbox->domain = *domain;
	mailbox->next_read_token = 1;
	target->mail_sidecar = mailbox;
	return mailbox;
}

static void
agent_ipc_legacy_mailbox_release_locked(struct proc *p)
{
	struct agent_legacy_mailbox *mailbox;
	char *payload_page;

	if (p == 0)
		return;
	if (intr_get())
		panic("legacy mailbox release unlocked");
	mailbox = p->mail_sidecar;
	if (mailbox == 0)
		return;
	payload_page = mailbox->payload_page;
	if (payload_page == 0 ||
	    !resource_account_handle_valid(mailbox->account) ||
	    !resource_account_handle_equal(mailbox->account,
					   p->resource_account) ||
	    mailbox->charge_class < RESOURCE_CHARGE_ORDINARY ||
	    mailbox->charge_class >= RESOURCE_CHARGE_CLASS_COUNT)
		panic("legacy mailbox state");
	p->mail_sidecar = 0;
	mailbox->payload_page = 0;
	(void)kfree_account_page(payload_page, mailbox->account,
				 mailbox->charge_class);
	(void)kfree_account_page(mailbox, mailbox->account,
				 mailbox->charge_class);
}

static struct agent_legacy_mailbox *
agent_ipc_legacy_mailbox_current_locked(
	struct proc *p, const struct agent_legacy_domain *domain)
{
	struct agent_legacy_mailbox *mailbox = p->mail_sidecar;

	if (mailbox != 0 &&
	    !agent_ipc_legacy_mailbox_domain_matches(&mailbox->domain, domain)) {
		agent_ipc_legacy_mailbox_release_locked(p);
		return 0;
	}
	return mailbox;
}

void
agent_ipc_endpoint_capture_locked(struct agent_endpoint_handle *handle,
				  struct proc *p)
{
	memset(handle, 0, sizeof(*handle));
	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	handle->slot = p - pool;
	handle->pid = p->pid;
	handle->scope_id = agent_identity_proc_scope(p);
	handle->control_id = p->agent_control_id;
}

struct proc *
agent_ipc_endpoint_resolve_locked(struct agent_endpoint_handle *handle)
{
	struct proc *p;

	if (handle == 0 || handle->slot < 0 || handle->slot >= NPROC ||
	    handle->pid <= 0 || handle->control_id == 0 ||
	    handle->scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    handle->scope_id >= FS_OWNER_SCOPE_FLAG)
		return 0;
	p = &pool[handle->slot];
	if (!proc_teardown_live(p) || p->pid != handle->pid || !p->is_agent ||
	    p->agent_control_id != handle->control_id ||
	    agent_identity_proc_scope(p) != handle->scope_id)
		return 0;
	return p;
}

static int
agent_ipc_route_find(struct proc *target, uint64 source_control_id)
{
	if (target == 0 || source_control_id == 0)
		return -1;
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++)
		if (target->agent_ipc_route_source[i] == source_control_id)
			return i;
	return -1;
}

static int
agent_ipc_route_allows(struct proc *source, struct proc *target,
		       int event_type)
{
	int slot;

	if (source == 0 || target == 0 || source->agent_control_id == 0 ||
	    target->agent_control_id == 0 || event_type <= AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX || !agent_ipc_same_scope(source, target))
		return 0;
	if (source == target)
		return 1;
	slot = agent_ipc_route_find(target, source->agent_control_id);
	return slot >= 0 &&
	       (target->agent_ipc_route_events[slot] &
		AGENT_EVENT_MASK(event_type)) != 0;
}

static void
agent_ipc_remove_source_locked(uint64 source_control_id)
{
	if (source_control_id == 0)
		return;
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++) {
			if (target->agent_ipc_route_source[i] !=
			    source_control_id)
				continue;
			target->agent_ipc_route_source[i] = 0;
			target->agent_ipc_route_events[i] = 0;
			if (target->agent_ipc_route_count > 0)
				target->agent_ipc_route_count--;
		}
	}
}

void
agent_ipc_remove_source(uint64 source_control_id)
{
	int enabled;

	if (source_control_id == 0)
		return;
	enabled = intr_save();
	agent_ipc_remove_source_locked(source_control_id);
	intr_restore(enabled);
}

/* Legacy mail is PUBLIC-only; Agent IPC uses scoped, quota-bound routes. */
int
agent_ipc_legacy_public_send(struct proc *source, int pid, char *payload,
			     int len)
{
	struct agent_legacy_domain target_domain;
	struct agent_legacy_mailbox *mailbox;
	struct proc *target;
	int enabled;
	int slot;
	int result = -1;

	if (source == 0 || payload == 0 || pid <= 0 || len <= 0 ||
	    len > MAILBOX_PAYLOAD_SIZE)
		return -1;
	enabled = intr_save();
	target = agent_ipc_find_live_proc_locked(pid, 0);
	if (target == 0 ||
	    !agent_ipc_legacy_public_pair_locked(source, target,
						 &target_domain))
		goto out;
	mailbox = agent_ipc_legacy_mailbox_current_locked(target,
							  &target_domain);
	if (mailbox != 0 && mailbox->count >= MAILBOX_SLOT_COUNT)
		goto out;
	if (mailbox == 0) {
		mailbox = agent_ipc_legacy_mailbox_alloc_locked(
			target, &target_domain);
		if (mailbox == 0)
			goto out;
	}
	slot = mailbox->tail;
	memmove(mailbox->payload_page + slot * MAILBOX_PAYLOAD_SIZE,
		payload, len);
	mailbox->len[slot] = len;
	mailbox->from[slot] = source->pid;
	mailbox->tail = (mailbox->tail + 1) % MAILBOX_SLOT_COUNT;
	mailbox->count++;
	result = len;
out:
	intr_restore(enabled);
	return result;
}

int
agent_ipc_legacy_public_read_begin(
	struct proc *p, char *payload, int len,
	struct agent_legacy_read_receipt *receipt)
{
	struct agent_legacy_domain domain;
	struct agent_legacy_mailbox *mailbox;
	int enabled;
	int slot;
	int n;

	if (p == 0 || payload == 0 || receipt == 0 || len <= 0 ||
	    len > MAILBOX_PAYLOAD_SIZE)
		return -1;
	memset(receipt, 0, sizeof(*receipt));
	enabled = intr_save();
	if (agent_ipc_legacy_domain_locked(p, &domain) < 0) {
		n = -1;
		goto out;
	}
	mailbox = agent_ipc_legacy_mailbox_current_locked(p, &domain);
	if (mailbox == 0 || mailbox->count <= 0) {
		n = 0;
		goto out;
	}
	if (mailbox->read_token != 0) {
		n = -1;
		goto out;
	}
	slot = mailbox->head;
	n = MIN(len, mailbox->len[slot]);
	memmove(payload,
		mailbox->payload_page + slot * MAILBOX_PAYLOAD_SIZE, n);
	mailbox->read_token = mailbox->next_read_token++;
	if (mailbox->next_read_token == 0)
		mailbox->next_read_token = 1;
	receipt->endpoint_generation = domain.endpoint_generation;
	receipt->token = mailbox->read_token;
	receipt->pid = p->pid;
	receipt->slot = slot;
out:
	intr_restore(enabled);
	return n;
}

int
agent_ipc_legacy_public_read_finish(
	struct proc *p, const struct agent_legacy_read_receipt *receipt, int commit)
{
	struct agent_legacy_mailbox *mailbox;
	int enabled;
	int result = -1;

	if (p == 0 || receipt == 0 || receipt->token == 0)
		return -1;
	enabled = intr_save();
	mailbox = p->mail_sidecar;
	if (p->pid != receipt->pid ||
	    p->legacy_mail_endpoint_generation !=
		    receipt->endpoint_generation ||
	    mailbox == 0 || mailbox->read_token != receipt->token ||
	    mailbox->head != receipt->slot || mailbox->count <= 0)
		goto out;
	if (commit) {
		memset(mailbox->payload_page +
			       receipt->slot * MAILBOX_PAYLOAD_SIZE,
		       0, MAILBOX_PAYLOAD_SIZE);
		mailbox->len[receipt->slot] = 0;
		mailbox->from[receipt->slot] = 0;
		mailbox->head = (mailbox->head + 1) % MAILBOX_SLOT_COUNT;
		mailbox->count--;
	}
	mailbox->read_token = 0;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

int
agent_ipc_legacy_mailbox_empty(const struct proc *p)
{
	int empty;
	int enabled = intr_save();

	empty = p == 0 || p->mail_sidecar == 0;
	intr_restore(enabled);
	return empty;
}

void
agent_ipc_legacy_fill_info(struct proc *p, struct agent_info *info)
{
	struct agent_legacy_mailbox *mailbox;
	int enabled;

	if (p == 0 || info == 0)
		return;
	enabled = intr_save();
	mailbox = p->mail_sidecar;
	if (mailbox != 0) {
		info->legacy_mailbox_allocated = 1;
		info->legacy_mailbox_pages = MAILBOX_SIDECAR_PAGE_COUNT;
		info->legacy_mailbox_queue_count = mailbox->count;
	}
	intr_restore(enabled);
}

static int
agent_ipc_route_update(struct proc *target, uint64 source_control_id,
		       uint64 event_mask, int operation)
{
	int free_slot = -1;
	int slot = agent_ipc_route_find(target, source_control_id);

	if (operation == AGENT_IPC_ROUTE_REVOKE) {
		if (slot < 0)
			return AGENT_STATUS_OK;
		target->agent_ipc_route_events[slot] &= ~event_mask;
		if (target->agent_ipc_route_events[slot] == 0) {
			target->agent_ipc_route_source[slot] = 0;
			if (target->agent_ipc_route_count > 0)
				target->agent_ipc_route_count--;
		}
		return AGENT_STATUS_OK;
	}
	if (slot >= 0) {
		target->agent_ipc_route_events[slot] |= event_mask;
		return AGENT_STATUS_OK;
	}
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++)
		if (target->agent_ipc_route_source[i] == 0) {
			free_slot = i;
			break;
		}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	target->agent_ipc_route_source[free_slot] = source_control_id;
	target->agent_ipc_route_events[free_slot] = event_mask;
	target->agent_ipc_route_count++;
	return AGENT_STATUS_OK;
}

/* Event endpoints, queues, delivery, and wait operations. */

static uint64
agent_ipc_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int
agent_ipc_contains(char *haystack, char *needle)
{
	int nlen;

	if (needle == 0 || needle[0] == 0)
		return 1;
	if (haystack == 0)
		return 0;
	nlen = strlen(needle);
	for (; haystack[0] != 0; haystack++)
		if (strncmp(haystack, needle, nlen) == 0)
			return 1;
	return 0;
}

static void agent_ipc_wake_event_waiters(struct proc *p)
{
	wait_queue_wake_all(&p->agent_event_waiters);
	p->agent_wait_wakeup_count++;
}

static int agent_ipc_filter_matches(struct proc *target, int type, char *payload)
{
	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!target->agent_watch_valid[i])
			continue;
		if (target->agent_watch_event_type[i] != AGENT_EVENT_NONE &&
		    target->agent_watch_event_type[i] != type)
			continue;
		if (target->agent_watch_filter[i][0] &&
		    !agent_ipc_contains(payload, target->agent_watch_filter[i]))
			continue;
		return 1;
	}
	return 0;
}

int agent_ipc_watch_set(struct proc *p, int event_type, char *filter)
{
	int free_slot = -1;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (p->agent_watch_valid[i] &&
		    p->agent_watch_event_type[i] == event_type &&
		    strncmp(p->agent_watch_filter[i], filter,
			    AGENT_WATCH_FILTER_SIZE) == 0)
			return 0;
		if (!p->agent_watch_valid[i] && free_slot < 0)
			free_slot = i;
	}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	p->agent_watch_valid[free_slot] = 1;
	p->agent_watch_event_type[free_slot] = event_type;
	safestrcpy(p->agent_watch_filter[free_slot], filter,
		   sizeof(p->agent_watch_filter[free_slot]));
	p->agent_watch_count++;
	return 0;
}

static int agent_ipc_watch_clear(struct proc *p, int event_type, char *filter)
{
	int removed = 0;
	int clear_all = event_type == AGENT_EVENT_NONE && filter[0] == 0;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!p->agent_watch_valid[i])
			continue;
		if (!clear_all) {
			if (event_type != AGENT_EVENT_NONE &&
			    p->agent_watch_event_type[i] != event_type)
				continue;
			if (filter[0] &&
			    strncmp(p->agent_watch_filter[i], filter,
				    AGENT_WATCH_FILTER_SIZE) != 0)
				continue;
		}
		p->agent_watch_valid[i] = 0;
		p->agent_watch_event_type[i] = AGENT_EVENT_NONE;
		memset(p->agent_watch_filter[i], 0,
		       sizeof(p->agent_watch_filter[i]));
		removed++;
	}
	p->agent_watch_count -= removed;
	if (p->agent_watch_count < 0)
		p->agent_watch_count = 0;
	return removed;
}

#define AGENT_EVENT_ACCOUNT_EXTERNAL   (1ULL << 0)
#define AGENT_EVENT_ACCOUNT_IPC        (1ULL << 1)
#define AGENT_EVENT_ACCOUNT_ATTRIBUTED (1ULL << 2)
#define AGENT_EVENT_ACCOUNT_COALESCED  (1ULL << 3)
#define AGENT_EVENT_ACCOUNT_RESERVED   (1ULL << 4)

#define AGENT_WAIT_CANCEL_PENDING  1
#define AGENT_WAIT_CANCEL_RESERVED 2

#define AGENT_WAIT_CANCEL_SLOT (-1)

struct agent_ipc_wait_reservation {
	int slot;
	uint64 cookie;
	uint64 span_owner;
	uint64 source_control;
	uint64 audit_principal;
};

static int
agent_ipc_wait_reserve_locked(struct proc *p, struct agent_event *event,
			      struct agent_ipc_wait_reservation *reservation)
{
	int slot;

	if (intr_get())
		panic("Agent wait reservation unlocked");
	if (p->agent_event_count_queued > 0 &&
	    (p->agent_event_accounting[p->agent_event_head] &
	     AGENT_EVENT_ACCOUNT_RESERVED) != 0)
		return 0;
	if (p->agent_wait_cancel_pending != 0) {
		if (p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_RESERVED)
			return 0;
		if (p->agent_wait_cancel_pending != AGENT_WAIT_CANCEL_PENDING ||
		    p->agent_wait_cancel_event_id == 0)
			panic("Agent wait cancel reservation state");
		reservation->span_owner = p->agent_wait_cancel_span_owner;
		reservation->source_control = p->agent_wait_cancel_source_control;
		reservation->audit_principal =
			p->agent_wait_cancel_audit_principal;
		event->type = AGENT_EVENT_CANCELLED;
		event->source_pid = p->agent_wait_cancel_source_pid;
		event->target_pid = p->pid;
		event->status = AGENT_STATUS_CANCELLED;
		event->event_id = p->agent_wait_cancel_event_id;
		event->tick = p->agent_wait_cancel_tick;
		event->corr_id = p->agent_wait_cancel_corr_id;
		event->cause_sequence = p->agent_wait_cancel_cause_sequence;
		event->span_id = p->agent_wait_cancel_span_id;
		safestrcpy(event->payload, p->agent_wait_cancel_reason,
			   sizeof(event->payload));
		p->agent_wait_cancel_pending = AGENT_WAIT_CANCEL_RESERVED;
		reservation->slot = AGENT_WAIT_CANCEL_SLOT;
		reservation->cookie = event->event_id;
		return 1;
	}
	if (p->agent_event_count_queued <= 0)
		return 0;
	slot = p->agent_event_head;
	if (p->agent_events[slot].event_id == 0)
		panic("Agent event reservation state");
	*event = p->agent_events[slot];
	reservation->span_owner = p->agent_event_span_owner[slot];
	reservation->source_control = p->agent_event_source_control[slot];
	reservation->audit_principal = p->agent_event_audit_principal[slot];
	p->agent_event_accounting[slot] |= AGENT_EVENT_ACCOUNT_RESERVED;
	reservation->slot = slot;
	reservation->cookie = event->event_id;
	return 1;
}

static void
agent_ipc_event_account_refund(int *count, uint64 accounting, uint64 kind)
{
	if ((accounting & kind) == 0)
		return;
	if (*count <= 0)
		panic("Agent event accounting");
	(*count)--;
}

static void
agent_ipc_wait_finish(struct proc *p,
		      struct agent_ipc_wait_reservation *reservation, int commit)
{
	uint64 accounting;
	int enabled = intr_save();
	int slot = reservation->slot;

	if (slot == AGENT_WAIT_CANCEL_SLOT) {
		if (p->agent_wait_cancel_pending != AGENT_WAIT_CANCEL_RESERVED ||
		    p->agent_wait_cancel_event_id != reservation->cookie)
			panic("Agent wait cancel reservation changed");
	} else {
		if (slot < 0 || slot >= AGENT_EVENT_QUEUE_CAP ||
		    p->agent_event_count_queued <= 0 ||
		    p->agent_event_head != slot ||
		    p->agent_events[slot].event_id != reservation->cookie ||
		    (p->agent_event_accounting[slot] &
		     AGENT_EVENT_ACCOUNT_RESERVED) == 0)
			panic("Agent event reservation changed");
	}
	if (!commit) {
		if (slot == AGENT_WAIT_CANCEL_SLOT)
			p->agent_wait_cancel_pending = AGENT_WAIT_CANCEL_PENDING;
		else
			p->agent_event_accounting[slot] &=
				~AGENT_EVENT_ACCOUNT_RESERVED;
		agent_ipc_wake_event_waiters(p);
		goto out;
	}
	if (slot == AGENT_WAIT_CANCEL_SLOT) {
		p->agent_wait_cancel_pending = 0;
		p->agent_wait_cancel_span_id = 0;
		p->agent_wait_cancel_span_owner = 0;
		p->agent_wait_cancel_source_control = 0;
		p->agent_wait_cancel_audit_principal = 0;
	} else {
		accounting = p->agent_event_accounting[slot] &
			     ~AGENT_EVENT_ACCOUNT_RESERVED;
		memset(&p->agent_events[slot], 0, sizeof(p->agent_events[slot]));
		p->agent_event_source_control[slot] = 0;
		p->agent_event_span_owner[slot] = 0;
		p->agent_event_audit_principal[slot] = 0;
		p->agent_event_accounting[slot] = 0;
		p->agent_event_head = (slot + 1) % AGENT_EVENT_QUEUE_CAP;
		p->agent_event_count_queued--;
		agent_ipc_event_account_refund(
			&p->agent_external_event_count_queued, accounting,
			AGENT_EVENT_ACCOUNT_EXTERNAL);
		agent_ipc_event_account_refund(&p->agent_ipc_count_queued,
			accounting, AGENT_EVENT_ACCOUNT_IPC);
		agent_ipc_event_account_refund(
			&p->agent_attributed_event_count_queued, accounting,
			AGENT_EVENT_ACCOUNT_ATTRIBUTED);
	}
	if (p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_PENDING ||
	    p->agent_event_count_queued > 0)
		agent_ipc_wake_event_waiters(p);
out:
	intr_restore(enabled);
}

enum agent_event_origin {
	AGENT_EVENT_ORIGIN_KERNEL = 1,
	AGENT_EVENT_ORIGIN_DIRECTED,
	AGENT_EVENT_ORIGIN_ATTRIBUTED,
};

enum agent_event_delivery {
	AGENT_EVENT_REQUIRE_WATCH = 1,
	AGENT_EVENT_INTRINSIC_COALESCED,
};

#define AGENT_EVENT_SET(type) AGENT_EVENT_MASK(type)
#define AGENT_EVENT_ATTRIBUTED_SET \
	(AGENT_EVENT_SET(AGENT_EVENT_FILE_STATUS) | \
	 AGENT_EVENT_SET(AGENT_EVENT_JOB_DONE) | \
	 AGENT_EVENT_SET(AGENT_EVENT_POLICY_DENIED) | \
	 AGENT_EVENT_SET(AGENT_EVENT_CONTEXT_LIMIT) | \
	 AGENT_EVENT_SET(AGENT_EVENT_DASHBOARD_EXPORT))

struct agent_ipc_origin_policy {
	uint64 events;
	uint64 accounting;
};

static const struct agent_ipc_origin_policy agent_ipc_origin_policy[] = {
	[AGENT_EVENT_ORIGIN_KERNEL] = {
		AGENT_EVENT_ATTRIBUTED_SET | AGENT_EVENT_SET(AGENT_EVENT_TIMER) |
			AGENT_EVENT_SET(AGENT_EVENT_CANCELLED), 0 },
	[AGENT_EVENT_ORIGIN_DIRECTED] = {
		AGENT_EVENT_SET(AGENT_EVENT_MESSAGE) |
			AGENT_EVENT_SET(AGENT_EVENT_LLM_DONE),
		AGENT_EVENT_ACCOUNT_EXTERNAL | AGENT_EVENT_ACCOUNT_IPC },
	[AGENT_EVENT_ORIGIN_ATTRIBUTED] = { AGENT_EVENT_ATTRIBUTED_SET,
		AGENT_EVENT_ACCOUNT_EXTERNAL | AGENT_EVENT_ACCOUNT_ATTRIBUTED },
};

static int agent_ipc_queued_event_count(struct proc *target, int type,
					uint64 source_control_id)
{
	int count = 0;
	int slot = target->agent_event_head;

	for (int i = 0; i < target->agent_event_count_queued; i++) {
		if ((source_control_id != 0 &&
		     target->agent_event_source_control[slot] == source_control_id) ||
		    (source_control_id == 0 &&
		     target->agent_events[slot].type == type &&
		     (target->agent_event_accounting[slot] &
		      AGENT_EVENT_ACCOUNT_COALESCED) != 0))
			count++;
		slot = (slot + 1) % AGENT_EVENT_QUEUE_CAP;
	}
	return count;
}

static int
agent_ipc_event_id_alloc(uint64 *event_id)
{
	if (event_id == 0)
		return -1;
	*event_id = agent_observe_alloc_event_id();
	return *event_id == 0 ? -1 : 0;
}

static int agent_ipc_queue_event_locked(struct proc *target, struct proc *source,
				    int origin, int type, uint64 corr_id,
				    uint64 cause_sequence, char *payload,
				    int delivery)
{
	struct agent_event *event;
	uint64 accounting = 0;
	uint64 event_id;
	uint64 source_control_id = 0;
	uint64 span_id = 0;
	uint64 span_owner = 0;
	int source_pid = 0;
	int slot;

	if (!proc_teardown_live(target) || !target->is_agent)
		return 0;
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED ||
	    origin == AGENT_EVENT_ORIGIN_ATTRIBUTED) {
		if (!proc_teardown_live(source) || !source->is_agent ||
		    source->agent_control_id == 0 ||
		    !agent_ipc_same_scope(source, target))
			return 0;
		source_control_id = source->agent_control_id;
		source_pid = source->pid;
		span_id = source->agent_current_span_id;
		span_owner = source->agent_current_span_owner;
	} else if (origin != AGENT_EVENT_ORIGIN_KERNEL || source != 0) {
		return 0;
	} else {
		span_id = target->agent_current_span_id;
		span_owner = target->agent_current_span_owner;
	}
	if (span_id == 0 || span_owner == 0) {
		span_id = 0;
		span_owner = 0;
	}
	if (delivery != AGENT_EVENT_REQUIRE_WATCH &&
	    delivery != AGENT_EVENT_INTRINSIC_COALESCED)
		return 0;
	if (delivery == AGENT_EVENT_INTRINSIC_COALESCED &&
	    origin != AGENT_EVENT_ORIGIN_KERNEL)
		return 0;
	if (delivery == AGENT_EVENT_REQUIRE_WATCH &&
	    !agent_ipc_filter_matches(target, type, payload))
		return 0;
	if (type <= AGENT_EVENT_NONE || type > AGENT_EVENT_MAX ||
	    (agent_ipc_origin_policy[origin].events & AGENT_EVENT_MASK(type)) == 0)
		return 0;
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED &&
	    !agent_ipc_route_allows(source, target, type))
		return 0;
	if (delivery == AGENT_EVENT_INTRINSIC_COALESCED &&
	    agent_ipc_queued_event_count(target, type, 0) != 0)
		return 0;
	accounting = agent_ipc_origin_policy[origin].accounting;
	if (delivery == AGENT_EVENT_INTRINSIC_COALESCED)
		accounting = AGENT_EVENT_ACCOUNT_COALESCED;
	if (target->agent_event_count_queued >= AGENT_EVENT_QUEUE_CAP ||
	    ((accounting & AGENT_EVENT_ACCOUNT_EXTERNAL) != 0 &&
	    (target->agent_external_event_count_queued >=
		     AGENT_EVENT_EXTERNAL_LIMIT ||
	     agent_ipc_queued_event_count(target, AGENT_EVENT_NONE,
					  source_control_id) >=
		     AGENT_EVENT_SOURCE_LIMIT ||
	     ((accounting & AGENT_EVENT_ACCOUNT_IPC) != 0 &&
	      target->agent_ipc_count_queued >= AGENT_EVENT_IPC_LIMIT) ||
	     ((accounting & AGENT_EVENT_ACCOUNT_ATTRIBUTED) != 0 &&
	      target->agent_attributed_event_count_queued >=
		      AGENT_EVENT_ATTRIBUTED_LIMIT))) ||
	    agent_ipc_event_id_alloc(&event_id) < 0) {
		target->agent_event_dropped++;
		return -1;
	}
	slot = target->agent_event_tail;
	event = &target->agent_events[slot];
	memset(event, 0, sizeof(*event));
	event->type = type;
	event->source_pid = source_pid;
	event->target_pid = target->pid;
	event->status = AGENT_STATUS_OK;
	event->event_id = event_id;
	event->tick = agent_ipc_ticks();
	event->corr_id = corr_id;
	event->cause_sequence = cause_sequence;
	event->span_id = span_id;
	safestrcpy(event->payload, payload, sizeof(event->payload));
	target->agent_event_source_control[slot] = source_control_id;
	target->agent_event_span_owner[slot] = span_owner;
	target->agent_event_audit_principal[slot] = source_control_id != 0 ?
						 source_control_id :
						 target->agent_control_id;
	target->agent_event_accounting[slot] = accounting;
	target->agent_event_tail =
		(target->agent_event_tail + 1) % AGENT_EVENT_QUEUE_CAP;
	target->agent_event_count_queued++;
	if ((accounting & AGENT_EVENT_ACCOUNT_EXTERNAL) != 0)
		target->agent_external_event_count_queued++;
	if ((accounting & AGENT_EVENT_ACCOUNT_IPC) != 0)
		target->agent_ipc_count_queued++;
	if ((accounting & AGENT_EVENT_ACCOUNT_ATTRIBUTED) != 0)
		target->agent_attributed_event_count_queued++;
	target->agent_event_count++;
	agent_observe_record_event(
		AGENT_AUDIT_KIND_EVENT_ENQUEUE, target, event, span_owner,
		target->agent_event_audit_principal[slot]);
	agent_ipc_wake_event_waiters(target);
	return 1;
}

static int agent_ipc_queue_event(struct proc *target, struct proc *source,
			     int origin, int type, uint64 corr_id,
			     uint64 cause_sequence, char *payload)
{
	int enabled = intr_save();
	int delivered = agent_ipc_queue_event_locked(target, source, origin, type,
						 corr_id, cause_sequence, payload,
						 AGENT_EVENT_REQUIRE_WATCH);

	intr_restore(enabled);
	return delivered;
}

static int
agent_ipc_queue_intrinsic_timer_locked(struct proc *p, uint64 corr_id,
				       char *payload)
{
	return agent_ipc_queue_event_locked(
		p, 0, AGENT_EVENT_ORIGIN_KERNEL, AGENT_EVENT_TIMER, corr_id,
		p->context_path_latest, payload, AGENT_EVENT_INTRINSIC_COALESCED);
}

static int agent_ipc_queue_heartbeat_if_due(struct proc *p, uint64 now)
{
	int enabled = intr_save();
	int due = p->heartbeat_interval > 0 &&
		  now - p->agent_last_heartbeat_tick >=
			  (uint64)p->heartbeat_interval;

	if (due) {
		p->agent_last_heartbeat_tick = now;
		(void)agent_ipc_queue_intrinsic_timer_locked(
			p, now, "timer=heartbeat");
	}
	intr_restore(enabled);
	return due;
}

int agent_ipc_deliver_pid(int pid, struct proc *source, int type,
			  uint64 corr_id, uint64 cause_sequence,
			  char *payload, int mirror_mailbox,
			  int *delivered)
{
	struct agent_endpoint_handle source_handle;
	struct agent_endpoint_handle target_handle;
	struct proc *target;
	int handoff_ready = 0;
	int enabled;
	int queued;
	int status;

	if (delivered)
		*delivered = 0;
	if (pid <= 0 || source == 0 || type <= AGENT_EVENT_NONE ||
	    type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	if ((AGENT_EVENT_MASK(type) & AGENT_IPC_EVENT_MASK) == 0)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	if (!proc_teardown_live(source) || !source->is_agent ||
	    source->agent_control_id == 0 ||
	    (target = agent_ipc_find_live_proc_locked(pid, 1)) == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (!agent_ipc_route_allows(source, target, type)) {
		status = AGENT_STATUS_DENIED;
		goto out;
	}
	queued = agent_ipc_queue_event_locked(target, source,
					  AGENT_EVENT_ORIGIN_DIRECTED, type, corr_id,
					  cause_sequence, payload,
					  AGENT_EVENT_REQUIRE_WATCH);
	status = queued < 0 ? AGENT_STATUS_NO_SPACE :
		 queued == 0 ? AGENT_STATUS_NOT_FOUND : AGENT_STATUS_OK;
	if (status != AGENT_STATUS_OK)
		goto out;
	if (type == AGENT_EVENT_MESSAGE && mirror_mailbox) {
		target->agent_mailbox_valid = 1;
		target->agent_mailbox_from = source->pid;
		safestrcpy(target->agent_mailbox, payload,
			   sizeof(target->agent_mailbox));
	}
	if (type == AGENT_EVENT_MESSAGE && target != source) {
		agent_ipc_endpoint_capture_locked(&source_handle, source);
		agent_ipc_endpoint_capture_locked(&target_handle, target);
		handoff_ready = source_handle.control_id != 0 &&
				target_handle.control_id != 0;
	}
	if (delivered)
		*delivered = 1;
out:
	intr_restore(enabled);
	if (handoff_ready)
		agent_metadata_prefetch_handoff(&target_handle, &source_handle);
	return status;
}

int agent_ipc_deliver_watchers(struct proc *source, int type,
			       uint64 corr_id, uint64 cause_sequence,
			       char *payload)
{
	int delivered = 0;
	int rc;

	if (source == 0)
		return 0;

	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent ||
		    !agent_ipc_same_scope(p, source))
			continue;
		rc = agent_ipc_queue_event(p, source, AGENT_EVENT_ORIGIN_ATTRIBUTED,
				       type, corr_id, cause_sequence, payload);
		if (rc > 0)
			delivered += rc;
	}
	return delivered;
}

static void
agent_ipc_wait_state_locked(struct thread *t, int state)
{
	agent_ipc_thread_state_clear_locked(t, state);
	agent_identity_loop_refresh_locked(t->process);
}

void
agent_ipc_proc_prepare(struct proc *p)
{
	int enabled;

	if (p == 0)
		return;
	enabled = intr_save();
	if (p->mail_sidecar != 0 || p->legacy_mail_endpoint_generation != 0)
		panic("legacy endpoint prepare state");
	p->legacy_mail_endpoint_generation =
		agent_ipc_legacy_endpoint_alloc_locked();
	intr_restore(enabled);
}

void
agent_ipc_proc_reset(struct proc *p)
{
	int inactive;
	int enabled;

	if (p == 0)
		return;
	inactive = !p->is_agent && p->agent_control_id == 0;
	enabled = intr_save();
	agent_ipc_legacy_mailbox_release_locked(p);
	intr_restore(enabled);
	/* The event plane is initialized only when an Agent role is activated. */
	if (!inactive)
		memset(&p->agent_mailbox_valid, 0, AGENT_IPC_PROC_STATE_BYTES);
	p->heartbeat_interval = 0;
	enabled = intr_save();
	for (int tid = 0; tid < NTHREAD; tid++)
		agent_ipc_thread_state_clear_locked(&p->threads[tid], AGENT_LOOP_NONE);
	agent_identity_loop_refresh_locked(p);
	intr_restore(enabled);
}

void
agent_ipc_proc_activate(struct proc *p)
{
	int enabled;

	if (p == 0)
		return;
	agent_ipc_proc_reset(p);
	enabled = intr_save();
	if (p->legacy_mail_endpoint_generation == 0)
		p->legacy_mail_endpoint_generation =
			agent_ipc_legacy_endpoint_alloc_locked();
	intr_restore(enabled);
	for (int tid = 0; tid < NTHREAD; tid++)
		if (p->threads[tid].state != T_UNUSED &&
		    p->threads[tid].identity_generation != 0)
			agent_ipc_thread_runtime_transition(
				&p->threads[tid], AGENT_THREAD_RUNTIME_ACTIVATE);
	(void)agent_ipc_heartbeat_configure(p, 0, 0);
}

void
agent_ipc_proc_teardown(struct proc *p)
{
	int enabled;

	if (p == 0)
		return;
	agent_ipc_remove_source(p->agent_control_id);
	agent_ipc_proc_reset(p);
	enabled = intr_save();
	p->legacy_mail_endpoint_generation = 0;
	intr_restore(enabled);
}

void
agent_ipc_exec_public(struct proc *p)
{
	int enabled;

	if (p == 0)
		return;
	agent_ipc_remove_source(p->agent_control_id);
	agent_ipc_proc_reset(p);
	enabled = intr_save();
	p->legacy_mail_endpoint_generation =
		agent_ipc_legacy_endpoint_alloc_locked();
	intr_restore(enabled);
}

static void
agent_ipc_sched_snapshot_take(struct proc *p, struct thread *t,
			      struct agent_ipc_sched_snapshot *snapshot)
{
	uint64 earliest = 0;
	int enabled;

	if (snapshot == 0)
		return;
	memset(snapshot, 0, sizeof(*snapshot));
	if (p == 0)
		return;
	enabled = intr_save();
	snapshot->event_count_queued = p->agent_event_count_queued;
	snapshot->last_heartbeat_tick = p->agent_last_heartbeat_tick;
	snapshot->heartbeat_interval = p->heartbeat_interval;
	if (t != 0) {
		snapshot->loop_state = t->agent_loop_state;
		if (t->agent_wait_deadline_valid && t->identity_generation != 0) {
			snapshot->wait_deadline = t->agent_wait_deadline;
			snapshot->wait_deadline_valid = 1;
		}
		goto out;
	}
	snapshot->loop_state = p->loop_state;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *waiter = &p->threads[tid];

		if (!waiter->agent_wait_deadline_valid ||
		    waiter->identity_generation == 0)
			continue;
		if (earliest == 0 || waiter->agent_wait_deadline < earliest)
			earliest = waiter->agent_wait_deadline;
	}
	snapshot->wait_deadline = earliest;
	snapshot->wait_deadline_valid = earliest != 0;
out:
	intr_restore(enabled);
}

void
agent_ipc_thread_sched_snapshot(struct thread *t,
				struct agent_ipc_sched_snapshot *snapshot)
{
	agent_ipc_sched_snapshot_take(t == 0 ? 0 : t->process, t, snapshot);
}

int
agent_ipc_mailbox_take(struct proc *p, int *source_pid, char *message, int n)
{
	int enabled;
	int available;

	if (p == 0 || message == 0 || n <= 0)
		return 0;
	enabled = intr_save();
	available = p->agent_mailbox_valid;
	if (available) {
		if (source_pid)
			*source_pid = p->agent_mailbox_from;
		safestrcpy(message, p->agent_mailbox, n);
		p->agent_mailbox_valid = 0;
	}
	intr_restore(enabled);
	return available;
}

int
agent_ipc_heartbeat_configure(struct proc *p, uint64 interval, uint64 *tick_out)
{
	uint64 now;
	int enabled;

	if (p == 0 || interval > AGENT_HEARTBEAT_MAX_TICKS)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	now = agent_ipc_ticks();
	p->heartbeat_interval = (int)interval;
	p->agent_last_heartbeat_tick = now;
	intr_restore(enabled);
	if (tick_out != 0)
		*tick_out = now;
	return AGENT_STATUS_OK;
}

void
agent_ipc_tick_proc(struct proc *p, uint64 now)
{
	if (p == 0 || p->state == P_UNUSED || !p->is_agent ||
	    !proc_teardown_live(p))
		return;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (!t->agent_wait_deadline_valid ||
		    now < t->agent_wait_deadline || t->identity_generation == 0 ||
		    t->state != SLEEPING ||
		    t->wait_channel != &p->agent_event_waiters ||
		    t->wait_reason != WAIT_REASON_EVENT ||
		    t->wait_key != t->identity_generation)
			continue;
		if (wait_queue_wake_key_all(&p->agent_event_waiters,
					    t->identity_generation) != 1)
			panic("Agent deadline keyed wake");
		p->agent_wait_wakeup_count++;
	}
	(void)agent_ipc_queue_heartbeat_if_due(p, now);
}


/* IPC-facing system calls. */

#ifdef WAIT_ATOMIC_TEST_PROFILE
int agent_ipc_wait_test_publish(struct proc *p) { return agent_ipc_queue_intrinsic_timer_locked(p, 0, "wait=atomic-injected"); }
#endif

static int agent_ipc_watch_update(int event_type, uint64 filteraddr, int remove)
{
	struct proc *p = curr_proc();
	char filter[AGENT_WATCH_FILTER_SIZE] = {0}; int result = 0;

	if (!p->is_agent) return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WATCH)) return AGENT_STATUS_DENIED;
	if (event_type < AGENT_EVENT_NONE || event_type > AGENT_EVENT_MAX) return AGENT_STATUS_BAD_PARAM;
	if (filteraddr != 0 && copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0) return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0) return -1;
	if (!remove && agent_ipc_watch_set(p, event_type, filter) < 0) { result = AGENT_STATUS_NO_SPACE; goto out; }
	if (remove) result = agent_ipc_watch_clear(p, event_type, filter); else agent_identity_thread_loop_set(curr_thread(), AGENT_LOOP_IDLE);
	agent_context_append_system(p, AGENT_TOOL_AGENT_WATCH, 0, event_type, filter,
				    remove ? "unwatch" : "watch", AGENT_STATUS_OK,
				    remove ? result : event_type, 0, 0);
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int sys_agent_watch(int event_type, uint64 filteraddr) { return agent_ipc_watch_update(event_type, filteraddr, 0); }

int sys_agent_unwatch(int event_type, uint64 filteraddr) { return agent_ipc_watch_update(event_type, filteraddr, 1); }

int sys_agent_wait(uint64 eventaddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	struct agent_event event;
	struct agent_ipc_wait_reservation reservation = {0};
	uint64 start = agent_ipc_ticks();
	uint64 now;
	int enabled;
	int wait_status;
	int status;

	if (!p->is_agent || t == 0 || t->process != p ||
	    t->identity_generation == 0)
		return -1;
	if (eventaddr &&
	    user_range_check(p->pagetable, eventaddr, sizeof(event), PTE_W) < 0)
		return -1;
	memset(&event, 0, sizeof(event));
	enabled = intr_save();
	p->agent_wait_count++;
	agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
	intr_restore(enabled);
	for (;;) {
#ifdef WAIT_ATOMIC_TEST_PROFILE
		wait_status = wait_atomic_test_agent_wait(p);
		if (wait_status < 0) {
			status = -1;
			break;
		}
#endif
		/* Keep predicate recheck and waiter publication in one IRQ-off window. */
		enabled = intr_save();
		p->agent_wait_loop_count++;
		status = AGENT_STATUS_NOT_FOUND;
		if (agent_ipc_wait_reserve_locked(p, &event, &reservation))
			status = reservation.slot == AGENT_WAIT_CANCEL_SLOT ?
				 AGENT_STATUS_CANCELLED : AGENT_STATUS_OK;
		if (status != AGENT_STATUS_NOT_FOUND) {
			agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
			intr_restore(enabled);
			break;
		}
		now = agent_ipc_ticks();
		if (agent_ipc_queue_heartbeat_if_due(p, now)) {
			intr_restore(enabled);
			continue;
		}
		if (timeout_ticks >= 0 && now - start >= (uint64)timeout_ticks) {
			p->agent_timeout_count++;
			agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
			event.type = AGENT_EVENT_TIMER;
			event.target_pid = p->pid;
			event.status = AGENT_STATUS_TIMEOUT;
			event.tick = now;
			safestrcpy(event.payload, "timeout",
				   sizeof(event.payload));
			status = AGENT_STATUS_TIMEOUT;
			intr_restore(enabled);
			break;
		}
		t->agent_loop_state = AGENT_LOOP_WAITING;
		agent_identity_loop_refresh_locked(p);
		if (timeout_ticks >= 0) {
			t->agent_wait_deadline_valid = 1;
			t->agent_wait_deadline = start + timeout_ticks;
		} else {
			t->agent_wait_deadline_valid = 0;
			t->agent_wait_deadline = 0;
		}
		p->agent_wait_sleep_count++;
		wait_status = wait_queue_sleep_key_irq(
			&p->agent_event_waiters, t->identity_generation);
		t->agent_loop_state = AGENT_LOOP_RUNNING;
		agent_identity_loop_refresh_locked(p);
		if (wait_status != WAIT_QUEUE_OK) {
			agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
			status = -1;
			intr_restore(enabled);
			break;
		}
		intr_restore(enabled);
	}
	enabled = intr_save();
	agent_ipc_wait_state_locked(t,
		(status == AGENT_STATUS_OK || status == AGENT_STATUS_CANCELLED) ?
			AGENT_LOOP_RUNNING : AGENT_LOOP_IDLE);
	intr_restore(enabled);
	if (status == AGENT_STATUS_OK || status == AGENT_STATUS_CANCELLED) {
		/* Commit only after copyout and serialized attribution succeed. */
		if (agent_lifecycle_context_lane_enter(p) < 0) {
			agent_ipc_wait_finish(p, &reservation, 0);
			status = -1;
			goto out;
		}
		if (eventaddr &&
		    copyout(p->pagetable, eventaddr, (char *)&event,
			    sizeof(event)) < 0) {
			agent_ipc_wait_finish(p, &reservation, 0);
			agent_lifecycle_context_lane_leave(p);
			status = -1;
			goto out;
		}
		if (event.span_id != 0 && reservation.span_owner != 0) {
			p->agent_current_span_id = event.span_id;
			p->agent_current_span_owner = reservation.span_owner;
		}
		p->agent_current_cause_sequence = event.cause_sequence;
		p->agent_current_cause_pid = event.source_pid > 0 ?
						 event.source_pid : p->pid;
		p->agent_current_cause_control = reservation.source_control != 0 ?
						     reservation.source_control :
						     p->agent_control_id;
		agent_observe_record_event(
			AGENT_AUDIT_KIND_EVENT_CONSUME, p, &event,
			reservation.span_owner, reservation.audit_principal);
		agent_context_append_system_causal(
			p, AGENT_TOOL_AGENT_WAIT, event.event_id, event.type,
			event.payload,
			status == AGENT_STATUS_CANCELLED ? "cancelled" : "event",
			status, event.type, event.source_pid, event.corr_id);
		agent_ipc_wait_finish(p, &reservation, 1);
		agent_lifecycle_context_lane_leave(p);
	} else if (eventaddr &&
		   copyout(p->pagetable, eventaddr, (char *)&event,
			   sizeof(event)) < 0) {
		status = -1;
	}
out:
	enabled = intr_save();
	agent_ipc_wait_state_locked(t,
		p->is_agent && proc_teardown_live(p) ?
			AGENT_LOOP_IDLE : AGENT_LOOP_NONE);
	intr_restore(enabled);
	return status;
}

int sys_agent_wait_cancel(int pid, uint64 reasonaddr)
{
	struct proc *p = curr_proc();
	struct proc *target;
	char reason[AGENT_EVENT_PAYLOAD_SIZE] = {0};
	uint64 event_id;
	int enabled;
	int status = AGENT_STATUS_NOT_FOUND;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WAIT_CANCEL))
		return AGENT_STATUS_DENIED;
	if (reasonaddr != 0 &&
	    copyinstr(p->pagetable, reason, reasonaddr, sizeof(reason)) < 0)
		return -1;
	if (reason[0] == 0)
		safestrcpy(reason, "cancel", sizeof(reason));
	enabled = intr_save();
	target = agent_ipc_find_live_proc_locked(pid, 1);
	if (target != 0) {
		if (!agent_identity_controls_target(p, target))
			status = AGENT_STATUS_DENIED;
		else if (target->agent_wait_cancel_pending)
			status = AGENT_STATUS_DUPLICATE;
		else if (agent_ipc_event_id_alloc(&event_id) < 0)
			status = AGENT_STATUS_NO_SPACE;
		else {
			target->agent_wait_cancel_pending =
				AGENT_WAIT_CANCEL_PENDING;
			target->agent_wait_cancel_source_pid = p->pid;
			target->agent_wait_cancel_event_id = event_id;
			target->agent_wait_cancel_corr_id = event_id;
			target->agent_wait_cancel_tick = agent_ipc_ticks();
			target->agent_wait_cancel_cause_sequence =
				p->context_path_latest;
			target->agent_wait_cancel_span_id = p->agent_current_span_id;
			target->agent_wait_cancel_span_owner =
				p->agent_current_span_owner;
			target->agent_wait_cancel_source_control = p->agent_control_id;
			target->agent_wait_cancel_audit_principal =
				p->agent_control_id;
			safestrcpy(target->agent_wait_cancel_reason, reason,
				   sizeof(target->agent_wait_cancel_reason));
			target->agent_wait_cancel_count++;
			agent_ipc_wake_event_waiters(target);
			status = AGENT_STATUS_OK;
		}
	}
	intr_restore(enabled);
	return status;
}

int sys_agent_route_config(int source_pid, int target_pid, uint64 event_mask,
			   int operation)
{
	struct proc *p = curr_proc();
	struct proc *source;
	struct proc *target;
	int enabled;
	int status;

	if (!p->is_agent)
		return -1;
	if (source_pid <= 0 || target_pid <= 0 || event_mask == 0 ||
	    (event_mask & ~AGENT_IPC_EVENT_MASK) != 0 ||
	    (operation != AGENT_IPC_ROUTE_GRANT &&
	     operation != AGENT_IPC_ROUTE_REVOKE))
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	source = agent_ipc_find_live_proc_locked(source_pid, 1);
	target = agent_ipc_find_live_proc_locked(target_pid, 1);
	if (source == 0 || target == 0)
		status = AGENT_STATUS_NOT_FOUND;
	else if (!agent_ipc_same_scope(source, target))
		status = AGENT_STATUS_DENIED;
	else if ((p != target &&
		  (!agent_identity_has_cap(p, AGENT_CAP_ROUTE_MANAGE) ||
		   !agent_identity_controls_or_self(p, source) ||
		   !agent_identity_controls_or_self(p, target))) ||
		 (p == target && !agent_identity_has_cap(p, AGENT_CAP_WATCH)))
		status = AGENT_STATUS_DENIED;
	else if (source == target)
		status = AGENT_STATUS_OK;
	else
		status = agent_ipc_route_update(target, source->agent_control_id,
						event_mask, operation);
	intr_restore(enabled);
	return status;
}

static int agent_ipc_heartbeat_syscall(uint64 interval_ticks, char *action)
{
	struct proc *p = curr_proc();
	uint64 now;
	int status;

	if (!p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	status = agent_ipc_heartbeat_configure(p, interval_ticks, &now);
	if (status != AGENT_STATUS_OK) {
		agent_lifecycle_context_lane_leave(p);
		return status;
	}
	agent_context_append_system(p, AGENT_TOOL_AGENT_HEARTBEAT, 0,
				    interval_ticks, action, action,
				    AGENT_STATUS_OK, interval_ticks,
				    now, 0);
	agent_lifecycle_context_lane_leave(p);
	return 0;
}

int sys_agent_heartbeat(uint64 interval_ticks)
{
	return agent_ipc_heartbeat_syscall(interval_ticks, "heartbeat_legacy");
}

int sys_agent_heartbeat_set(uint64 interval_ticks)
{
	return agent_ipc_heartbeat_syscall(interval_ticks, "heartbeat_set");
}

int sys_agent_heartbeat_stop(void)
{
	return agent_ipc_heartbeat_syscall(0, "heartbeat_stop");
}

int sys_agent_wake(int pid, uint64 eventaddr)
{
	struct proc *p = curr_proc();
	struct agent_event event;
	int status;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_any_cap(p, AGENT_CAP_MESSAGE_SEND | AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (eventaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (copyin(p->pagetable, (char *)&event, eventaddr, sizeof(event)) < 0)
		return -1;
	if (event.type <= AGENT_EVENT_NONE || event.type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	if (event.type != AGENT_EVENT_MESSAGE)
		return AGENT_STATUS_DENIED;
	event.payload[sizeof(event.payload) - 1] = 0;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	status = agent_ipc_deliver_pid(pid, p, event.type, event.corr_id,
				   p->context_path_latest, event.payload, 0,
				   0);
	agent_metadata_txn_unlock();
	return status;
}
