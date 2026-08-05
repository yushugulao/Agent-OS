#include "loader.h"
#include "agent.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "kernel_work.h"
#include "trap.h"
#include "vfs_security.h"
#ifdef VIRTIO_DISK_TEST_PROFILE
#include "virtio.h"
#endif
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
#include "fs_allocator_test.h"
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
#include "physical_page_test.h"
#endif

extern char INIT_PROC[];

enum user_image_read_status {
	USER_IMAGE_READ_COMPLETE,
	USER_IMAGE_READ_ERROR,
	USER_IMAGE_READ_EOF,
	USER_IMAGE_READ_INTERRUPTED,
	USER_IMAGE_READ_DEFERRED,
};

#define USER_IMAGE_RX_CACHE_SET_COUNT 16U
#define USER_IMAGE_RX_CACHE_WAYS 4U
#define USER_IMAGE_RX_CACHE_PRESSURE_RECLAIM 4U
#define USER_IMAGE_RX_CACHE_PAGE_CAP \
	(USER_IMAGE_RX_CACHE_SET_COUNT * USER_IMAGE_RX_CACHE_WAYS)
#define USER_IMAGE_RX_CACHE_ACCOUNT_ID 1ULL

struct user_image_rx_cache_key {
	uint64 length;
	uint64 offset;
	uint dev;
	uint inum;
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint vfs_exec_profile;
	uint vfs_exec_incarnation;
};

struct user_image_rx_cache_entry {
	struct user_image_rx_cache_key key;
	struct resource_account_handle source_account;
	enum resource_charge_class source_charge_class;
	char *page;
	uint64 stamp;
};

static struct user_image_rx_cache_entry
	user_image_rx_cache[USER_IMAGE_RX_CACHE_SET_COUNT]
			   [USER_IMAGE_RX_CACHE_WAYS];
static uint64 user_image_rx_cache_clock;
static struct resource_account_handle user_image_rx_cache_account;
static uint user_image_rx_cache_account_state;
static struct user_image_rx_cache_stats user_image_rx_cache_stats = {
	.version = USER_IMAGE_RX_CACHE_STATS_VERSION,
	.size = sizeof(struct user_image_rx_cache_stats),
};

enum user_image_rx_cache_account_state {
	USER_IMAGE_RX_CACHE_ACCOUNT_UNINITIALIZED = 0,
	USER_IMAGE_RX_CACHE_ACCOUNT_READY,
	USER_IMAGE_RX_CACHE_ACCOUNT_DISABLED,
};

static int user_image_rx_cache_account_ensure(void)
{
	struct resource_account_limits limits;
	struct resource_account_handle account;
	int enabled = intr_save();
	int result = -1;

	account = resource_account_none();
	if (user_image_rx_cache_account_state ==
	    USER_IMAGE_RX_CACHE_ACCOUNT_READY) {
		result = 0;
		goto out;
	}
	if (user_image_rx_cache_account_state !=
	    USER_IMAGE_RX_CACHE_ACCOUNT_UNINITIALIZED)
		goto out;
	memset(&limits, 0, sizeof(limits));
	limits.class_limit[RESOURCE_CHARGE_ORDINARY]
			  [RESOURCE_PHYSICAL_PAGE] =
		USER_IMAGE_RX_CACHE_PAGE_CAP;
	if (resource_account_create(
		    RESOURCE_ACCOUNT_CACHE, USER_IMAGE_RX_CACHE_ACCOUNT_ID,
		    RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_ORDINARY), &limits,
		    &account) < 0 ||
	    resource_account_member_acquire(account) < 0) {
		if (resource_account_handle_valid(account) &&
		    resource_account_close(account) < 0)
			panic("exec cache account rollback");
		user_image_rx_cache_account_state =
			USER_IMAGE_RX_CACHE_ACCOUNT_DISABLED;
		goto out;
	}
	user_image_rx_cache_account = account;
	user_image_rx_cache_account_state =
		USER_IMAGE_RX_CACHE_ACCOUNT_READY;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

static int
user_image_rx_cache_key_equal(const struct user_image_rx_cache_key *left,
			      const struct user_image_rx_cache_key *right)
{
	return left->length == right->length &&
	       left->offset == right->offset && left->dev == right->dev &&
	       left->inum == right->inum &&
	       left->exec_flags == right->exec_flags &&
	       left->exec_generation == right->exec_generation &&
	       left->exec_role_mask == right->exec_role_mask &&
	       left->exec_layout_version == right->exec_layout_version &&
	       left->exec_rw_offset == right->exec_rw_offset &&
	       left->vfs_exec_profile == right->vfs_exec_profile &&
	       left->vfs_exec_incarnation == right->vfs_exec_incarnation;
}

static uint
user_image_rx_cache_set(const struct user_image_rx_cache_key *key)
{
	const uchar *bytes = (const uchar *)key;
	uint64 hash = 1469598103934665603ULL;

	for (uint i = 0; i < sizeof(*key); i++) {
		hash ^= bytes[i];
		hash *= 1099511628211ULL;
	}
	return (uint)(hash % USER_IMAGE_RX_CACHE_SET_COUNT);
}

static uint64 user_image_rx_cache_touch_locked(void)
{
	user_image_rx_cache_clock++;
	if (user_image_rx_cache_clock != 0)
		return user_image_rx_cache_clock;
	for (uint set = 0; set < USER_IMAGE_RX_CACHE_SET_COUNT; set++)
		for (uint way = 0; way < USER_IMAGE_RX_CACHE_WAYS; way++)
			if (user_image_rx_cache[set][way].page != 0)
				user_image_rx_cache[set][way].stamp = 1;
	user_image_rx_cache_clock = 2;
	return user_image_rx_cache_clock;
}

static char *user_image_rx_cache_drop_locked(
	struct user_image_rx_cache_entry *entry)
{
	char *page = entry->page;

	if (page != 0) {
		memset(entry, 0, sizeof(*entry));
		user_image_rx_cache_stats.exec_cache_evictions++;
	}
	return page;
}

static char *user_image_rx_cache_lookup(
	const struct user_image_rx_cache_key *key)
{
	char *stale = 0;
	char *page = 0;
	uint set = user_image_rx_cache_set(key);
	int enabled = intr_save();

	for (uint way = 0; way < USER_IMAGE_RX_CACHE_WAYS; way++) {
		struct user_image_rx_cache_entry *entry =
			&user_image_rx_cache[set][way];

		if (entry->page == 0 ||
		    !user_image_rx_cache_key_equal(&entry->key, key))
			continue;
		if (resource_account_state_get(entry->source_account) ==
			    RESOURCE_ACCOUNT_ACTIVE &&
		    kretain_account_page(entry->page) == 0) {
			entry->stamp = user_image_rx_cache_touch_locked();
			user_image_rx_cache_stats.exec_cache_hits++;
			page = entry->page;
		} else {
			stale = user_image_rx_cache_drop_locked(entry);
		}
		break;
	}
	if (page == 0)
		user_image_rx_cache_stats.exec_cache_misses++;
	intr_restore(enabled);
	if (stale != 0 && krelease_account_page(stale) < 0)
		panic("exec cache stale release");
	return page;
}

/*
 * Publish after the disk read. A competing loader may have filled the same
 * slot while this one yielded; in that case its page wins and the caller
 * releases the redundant private copy.
 */
static char *user_image_rx_cache_publish(
	const struct user_image_rx_cache_key *key, char *candidate,
	struct resource_account_handle source_account,
	enum resource_charge_class source_charge_class, int *shared)
{
	struct user_image_rx_cache_entry *slot = 0;
	char *evicted = 0;
	char *result = candidate;
	uint set = user_image_rx_cache_set(key);
	int enabled;

	*shared = 0;
	enabled = intr_save();
	for (uint way = 0; way < USER_IMAGE_RX_CACHE_WAYS; way++) {
		struct user_image_rx_cache_entry *entry =
			&user_image_rx_cache[set][way];

		if (entry->page == 0) {
			if (slot == 0)
				slot = entry;
			continue;
		}
		if (!user_image_rx_cache_key_equal(&entry->key, key))
			continue;
		if (resource_account_state_get(entry->source_account) ==
			    RESOURCE_ACCOUNT_ACTIVE &&
		    kretain_account_page(entry->page) == 0) {
			entry->stamp = user_image_rx_cache_touch_locked();
			result = entry->page;
			*shared = 1;
			goto out;
		}
		evicted = user_image_rx_cache_drop_locked(entry);
		slot = entry;
		break;
	}
	if (slot == 0) {
		slot = &user_image_rx_cache[set][0];
		for (uint way = 1; way < USER_IMAGE_RX_CACHE_WAYS; way++)
			if (user_image_rx_cache[set][way].stamp < slot->stamp)
				slot = &user_image_rx_cache[set][way];
		evicted = user_image_rx_cache_drop_locked(slot);
	}
	if (kretain_account_page(candidate) < 0)
		goto out;
	slot->key = *key;
	slot->source_account = source_account;
	slot->source_charge_class = source_charge_class;
	slot->page = candidate;
	slot->stamp = user_image_rx_cache_touch_locked();

out:
	intr_restore(enabled);
	if (evicted != 0 && krelease_account_page(evicted) < 0)
		panic("exec cache eviction release");
	return result;
}

static int
user_image_rx_cache_reclaim_one(enum resource_charge_class charge_class)
{
	struct user_image_rx_cache_entry *victim = 0;
	char *page;
	int enabled = intr_save();

	for (uint set = 0; set < USER_IMAGE_RX_CACHE_SET_COUNT; set++)
		for (uint way = 0; way < USER_IMAGE_RX_CACHE_WAYS; way++) {
			struct user_image_rx_cache_entry *entry =
				&user_image_rx_cache[set][way];

			if (entry->page == 0 ||
			    entry->source_charge_class != charge_class ||
			    !kaccount_page_exclusive(
				    entry->page, entry->source_account,
				    entry->source_charge_class))
				continue;
			if (victim == 0 || entry->stamp < victim->stamp)
				victim = entry;
		}
	if (victim == 0) {
		intr_restore(enabled);
		return 0;
	}
	page = user_image_rx_cache_drop_locked(victim);
	intr_restore(enabled);
	if (krelease_account_page(page) < 0)
		panic("exec cache pressure release");
	return 1;
}

void user_image_rx_cache_stats_snapshot(
	struct user_image_rx_cache_stats *out)
{
	int enabled;

	if (out == 0)
		return;
	enabled = intr_save();
	memmove(out, &user_image_rx_cache_stats, sizeof(*out));
	intr_restore(enabled);
}

static void user_image_rx_cache_record_shared(void)
{
	int enabled = intr_save();

	user_image_rx_cache_stats.exec_cache_shared_pages++;
	intr_restore(enabled);
}

static char *user_image_page_alloc(
	pagetable_t pagetable, enum resource_charge_class charge_class)
{
	char *page = uvm_page_alloc(pagetable);

	for (uint reclaimed = 0;
	     page == 0 && reclaimed < USER_IMAGE_RX_CACHE_PRESSURE_RECLAIM;
	     reclaimed++) {
		if (!user_image_rx_cache_reclaim_one(charge_class))
			break;
		page = uvm_page_alloc(pagetable);
	}
	return page;
}

static char *user_image_rx_cache_page_alloc(void)
{
	char *page;

	if (user_image_rx_cache_account_ensure() < 0)
		return 0;
	page = kalloc_account_page(user_image_rx_cache_account,
				   RESOURCE_CHARGE_ORDINARY);
	for (uint reclaimed = 0;
	     page == 0 && reclaimed < USER_IMAGE_RX_CACHE_PRESSURE_RECLAIM;
	     reclaimed++) {
		if (!user_image_rx_cache_reclaim_one(
			    RESOURCE_CHARGE_ORDINARY))
			break;
		page = kalloc_account_page(user_image_rx_cache_account,
					   RESOURCE_CHARGE_ORDINARY);
	}
	return page;
}

static void user_image_rx_cache_key_init(
	struct user_image_rx_cache_key *key, const struct user_image *image,
	uint64 length, uint64 offset)
{
	memset(key, 0, sizeof(*key));
	key->length = length;
	key->offset = offset;
	key->dev = image->exec_dev;
	key->inum = image->exec_inum;
	key->exec_flags = image->exec_flags;
	key->exec_generation = image->exec_generation;
	key->exec_role_mask = image->exec_role_mask;
	key->exec_layout_version = image->exec_layout_version;
	key->exec_rw_offset = image->exec_rw_offset;
	key->vfs_exec_profile = image->vfs_exec_profile;
	key->vfs_exec_incarnation = image->vfs_exec_incarnation;
}

/*
 * readi() may commit a positive prefix when the I/O governor asks it to
 * leave the filesystem atomic section. Pay that debt outside the section,
 * then resume from the committed offset instead of rejecting a valid short
 * read as a corrupt executable.
 */
static enum user_image_read_status
user_image_read_exact(struct inode *ip, const struct vfs_cred *cred,
		      char *dst, uint off, uint length)
{
	uint done = 0;

	while (done < length) {
		int n = readi(ip, cred, 0, (uint64)(dst + done),
			      off + done, length - done);
		struct bio_checkpoint_result checkpoint =
			bio_request_checkpoint();

		if (checkpoint.state == BIO_CHECKPOINT_INTERRUPTED)
			return USER_IMAGE_READ_INTERRUPTED;
		if (n < 0)
			return USER_IMAGE_READ_ERROR;
		if (n == 0)
			return USER_IMAGE_READ_EOF;
		if ((uint)n > length - done)
			return USER_IMAGE_READ_ERROR;
		done += n;
		if (checkpoint.state == BIO_CHECKPOINT_DEFERRED)
			return USER_IMAGE_READ_DEFERRED;
	}
	return USER_IMAGE_READ_COMPLETE;
}

static struct inode *init_image_lookup(char *path)
{
	struct inode *ip;
	int status;

	ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,
				VFS_SCOPE_SYSTEM, &status);
	if (status != FS_LOOKUP_ABSENT)
		return ip;
	return namei_scope_status(path, VFS_POLICY_PUBLIC, VFS_SCOPE_NONE,
				  &status);
}

void user_image_discard(struct user_image *image)
{
	if (image == 0 || image->pagetable == 0)
		return;
	if (image->shared_pages != 0)
		uvmunmap(image->pagetable, image->shared_base,
			 image->shared_pages, 0);
	uvmunmap(image->pagetable, TRAPFRAME, 1, 0);
	uvmunmap(image->pagetable, TRAMPOLINE, 1, 0);
	uvmfree_cleanup(image->pagetable, image->max_page);
	memset(image, 0, sizeof(*image));
}

int user_image_build(struct inode *ip, uint64 trapframe_pa,
		     struct resource_account_handle account,
		     enum resource_charge_class charge_class,
		     struct user_image *image)
{
	char *page;
	uint64 length;
	uint64 va_end;
	int perm;
	int cacheable;
	int trusted;
	struct vfs_cred kernel_cred;

	if (ip == 0 || image == 0 || trapframe_pa == 0)
		return -1;
	memset(image, 0, sizeof(*image));
	if (ivalid(ip) < 0)
		return -1;
	if (!vfs_inode_label_valid(ip) ||
	    !exec_policy_inode_layout_valid(ip) ||
	    !vfs_exec_profile_valid(ip->vfs_exec_profile))
		return -1;
	vfs_cred_kernel(&kernel_cred);
	image->exec_dev = ip->dev;
	image->exec_inum = ip->inum;
	image->exec_flags = ip->exec_flags;
	image->exec_generation = ip->exec_generation;
	image->exec_role_mask = ip->exec_role_mask;
	image->exec_layout_version = ip->exec_layout_version;
	image->exec_rw_offset = ip->exec_rw_offset;
	image->vfs_exec_profile = ip->vfs_exec_profile;
	image->vfs_exec_incarnation = ip->vfs_incarnation;
	trusted = exec_policy_inode_trusted(ip);
	image->agent_class =
		trusted &&
		ip->vfs_exec_profile != VFS_EXEC_PROFILE_NONE &&
		ip->exec_role_mask != 0 ?
			USER_IMAGE_AGENT_TRUSTED :
			USER_IMAGE_AGENT_FORBIDDEN;
	/*
	 * Only immutable manifests enter; generation/incarnation reject stale
	 * inode aliases.
	 */
	cacheable = trusted;
	length = ip->size;
	if (length == 0 || length > MAXVA - BASE_ADDRESS)
		return -1;
	va_end = PGROUNDUP(BASE_ADDRESS + length);
	if (va_end < BASE_ADDRESS ||
	    va_end > USER_HEAP_LIMIT - 3 * PAGE_SIZE -
			     NTHREAD * USTACK_SIZE)
		return -1;

	image->pagetable = uvmcreate_account(account, charge_class);
	if (image->pagetable == 0)
		return -1;
	image->entry = BASE_ADDRESS;
	image->ustack_base = va_end + PAGE_SIZE;
	image->heap_base = image->ustack_base +
		NTHREAD * USTACK_SIZE + PAGE_SIZE;
	image->heap_break = image->heap_base;

	for (uint64 va = BASE_ADDRESS, off = 0; va < va_end;
	     va += PAGE_SIZE, off += PAGE_SIZE) {
		struct user_image_rx_cache_key cache_key;
		uint want = MIN(PAGE_SIZE, length - off);
		int cache_shared = 0;
		int cache_rx = cacheable && off < image->exec_rw_offset;

		page = 0;
		if (cache_rx) {
			user_image_rx_cache_key_init(&cache_key, image,
						     length, off);
			page = user_image_rx_cache_lookup(&cache_key);
			cache_shared = page != 0;
		}
		if (page == 0) {
			char *loaded;
			int cache_candidate = 0;

			loaded = cache_rx ?
				user_image_rx_cache_page_alloc() : 0;
			cache_candidate = loaded != 0;
			if (loaded == 0)
				loaded = user_image_page_alloc(image->pagetable,
						       charge_class);
			if (loaded == 0)
				goto fail;
			memset(loaded, 0, PAGE_SIZE);
			if (user_image_read_exact(ip, &kernel_cred, loaded, off,
						  want) !=
			    USER_IMAGE_READ_COMPLETE) {
				(void)uvm_page_free(image->pagetable, loaded);
				goto fail;
			}
			page = loaded;
			if (cache_candidate) {
				page = user_image_rx_cache_publish(
					&cache_key, loaded,
					user_image_rx_cache_account,
					RESOURCE_CHARGE_ORDINARY, &cache_shared);
				if (page != loaded)
					(void)uvm_page_free(image->pagetable,
							    loaded);
			}
		}
		perm = PTE_U | PTE_R;
		perm |= off < image->exec_rw_offset ? PTE_X : PTE_W;
		if (mappages(image->pagetable, va, PAGE_SIZE, (uint64)page,
			     perm) < 0) {
			(void)uvm_page_free(image->pagetable, page);
			goto fail;
		}
		if (cache_shared)
			user_image_rx_cache_record_shared();
		image->max_page = (va + PAGE_SIZE) / PAGE_SIZE;
		if (kernel_work_checkpoint(KERNEL_WORK_PAGE_UNITS) < 0)
			goto fail;
	}

	page = user_image_page_alloc(image->pagetable, charge_class);
	if (page == 0)
		goto fail;
	memset(page, 0, PAGE_SIZE);
	if (mappages(image->pagetable, image->ustack_base, PAGE_SIZE,
		     (uint64)page, PTE_U | PTE_R | PTE_W) < 0) {
		(void)uvm_page_free(image->pagetable, page);
		goto fail;
	}
	image->max_page =
		(image->ustack_base + USTACK_SIZE) / PAGE_SIZE;
	if (mappages(image->pagetable, TRAPFRAME, TRAP_PAGE_SIZE,
		     trapframe_pa, PTE_R | PTE_W) < 0)
		goto fail;
	return 0;

fail:
	user_image_discard(image);
	return -1;
}

// load all apps and init the corresponding `proc` structure.
int load_init_app()
{
	struct inode *ip = 0;
	struct user_image image;
	struct trapframe staged;
	char *argv[2];
	int argc;
	struct proc *p = allocproc();

	if (p == 0)
		return -1;
	if ((ip = init_image_lookup(INIT_PROC)) == 0) {
		errorf("invalid init proc name\n");
		freeproc(p);
		return -1;
	}
	debugf("load init app %s", INIT_PROC);
	if (user_image_build(
		    ip, (uint64)proc_trapframe(p, 0), p->resource_account,
		    p->resource_slot_reserved ? RESOURCE_CHARGE_RESERVED :
					RESOURCE_CHARGE_ORDINARY,
		    &image) < 0) {
		iput(ip);
		freeproc(p);
		return -1;
	}
	iput(ip);
	argv[0] = INIT_PROC;
	argv[1] = NULL;
	memset(&staged, 0, sizeof(staged));
	staged.epc = image.entry;
	argc = push_argv_image(image.pagetable, image.ustack_base, &staged,
			       argv);
	if (argc < 0 || init_stdio(p) < 0) {
		user_image_discard(&image);
		freeproc(p);
		return -1;
	}
	if (proc_install_user_image(p, &image, &staged,
				    PROC_IMAGE_INSTALL_BOOTSTRAP) < 0) {
		user_image_discard(&image);
		freeproc(p);
		return -1;
	}
	if (exec_policy_process_bootstrap(p))
		agent_authority_bootstrap(p);
#ifdef VIRTIO_DISK_TEST_PROFILE
	/* Only the kernel-loaded, boot-sealed init identity controls test faults. */
	virtio_disk_test_bind_boot_init(p, INIT_PROC);
#endif
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
	fs_allocator_test_bind_boot_init(p, INIT_PROC);
#endif
#ifdef PHYSICAL_PAGE_TEST_HOOKS
	physical_page_test_bind_boot_init(p, INIT_PROC);
#endif
	struct thread *t = &p->threads[0];
	t->trapframe->a0 = argc;
	t->state = RUNNABLE;
	add_task(t);
	return 0;
}
