#include "agent_internal.h"
#include "agent_metadata_probe.h"
#include "agent_metadata_recovery_test.h"
#include "agent_metadata_store_io.h"
#include "bio.h"
#include "defs.h"
#include "fs.h"
#include "string.h"
#include "vfs_security.h"
#include "virtio.h"

enum agent_metadata_probe_phase {
	AGENT_META_PROBE_HEADER = 1,
	AGENT_META_PROBE_PAYLOAD,
	AGENT_META_PROBE_VERIFY_HEADER,
	AGENT_META_PROBE_JOURNAL
};

struct agent_metadata_probe_summary {
	signed char classified, status, migration;
	uint64 generation;
	uint64 payload_hash;
	struct agent_meta_journal_cursor journal_cursor;
};

struct agent_metadata_probe_cursor {
	signed char active, bank, confirm;
	uchar phase;
	uint offset, store_bytes, journal_block;
	uint dev, inum, inode_size;
	uint64 incarnation;
	struct agent_meta_store_header verify_header;
	struct agent_meta_journal_replay journal_replay;
	char journal_data[AGENT_META_JOURNAL_BLOCK_BYTES];
};

static struct {
	struct agent_metadata_probe_key key;
	struct agent_metadata_probe_summary summary[AGENT_META_STORE_BANKS];
	struct agent_metadata_probe_cursor cursor;
	uint64 epoch;
	uint64 next_epoch;
	uint64 progress_sequence;
	uint progress_offset;
	uchar cache_ready, progress_phase;
	signed char confirmed_bank, progress_bank;
} probe;

static void agent_metadata_probe_new_epoch(int reuse)
{
	if (!reuse) {
		memset(probe.summary, 0, sizeof(probe.summary));
		memset(&probe.cursor, 0, sizeof(probe.cursor));
		probe.confirmed_bank = -1;
	}
	probe.cache_ready = 0;
	probe.epoch = probe.next_epoch++;
	if (probe.epoch == 0)
		probe.epoch = probe.next_epoch++;
}

void agent_metadata_probe_init(void)
{
	memset(&probe, 0, sizeof(probe));
	probe.next_epoch = 1;
	probe.confirmed_bank = -1;
	probe.progress_bank = -1;
	agent_metadata_recovery_test_init();
}

void agent_metadata_probe_reset(void)
{
	memset(&probe.key, 0, sizeof(probe.key));
	memset(probe.summary, 0, sizeof(probe.summary));
	memset(&probe.cursor, 0, sizeof(probe.cursor));
	probe.cache_ready = 0;
	probe.confirmed_bank = -1;
	probe.epoch = 0;
}

static int
agent_metadata_probe_trusted(const struct agent_metadata_probe_key *key)
{
	return key->reload_scope == VFS_SCOPE_NONE ||
	       key->reload_scope == VFS_SCOPE_SYSTEM;
}

static void agent_metadata_probe_release(int reusable)
{
	if (!reusable) {
		agent_metadata_probe_reset();
		return;
	}
	memset(&probe.cursor, 0, sizeof(probe.cursor));
	probe.cache_ready = 1;
	probe.epoch = 0;
}

static int agent_metadata_probe_bind(const struct agent_metadata_probe_key *key)
{
	int base = probe.key.authority_cookie == key->authority_cookie &&
		   probe.key.store_epoch == key->store_epoch &&
		   probe.key.force == key->force;
	int reuse;

	if ((probe.epoch != 0 || probe.cache_ready) && !base)
		agent_metadata_probe_reset();
	if (probe.epoch != 0 && !agent_metadata_probe_trusted(&probe.key)) {
		struct workflow_lifecycle_key lifecycle = {
			.id = probe.key.workflow_lifecycle_id,
			.generation = probe.key.workflow_lifecycle_generation
		};
		uint scope;

		if (workflow_lifecycle_scope(lifecycle, &scope) < 0 ||
		    scope != probe.key.reload_scope)
			agent_metadata_probe_reset();
	}
	if (probe.epoch != 0 &&
	    probe.key.authority_cookie == key->authority_cookie &&
	    probe.key.store_epoch == key->store_epoch &&
	    probe.key.reload_scope == key->reload_scope &&
	    probe.key.workflow_lifecycle_id == key->workflow_lifecycle_id &&
	    probe.key.workflow_lifecycle_generation ==
		    key->workflow_lifecycle_generation &&
	    probe.key.force == key->force)
		return AGENT_META_BANK_VALID;
	if (probe.epoch != 0 && agent_metadata_probe_trusted(&probe.key) &&
	    !agent_metadata_probe_trusted(key))
		return AGENT_META_BANK_BUSY;
	if (probe.epoch != 0 && probe.key.reload_scope == key->reload_scope &&
	    (probe.key.workflow_lifecycle_id != key->workflow_lifecycle_id ||
	     probe.key.workflow_lifecycle_generation !=
		     key->workflow_lifecycle_generation))
		agent_metadata_probe_reset();

	/*
	 * bank 游标和摘要只绑定物理 store。兼容的普通 scope 直接接管，
	 * 并用新 epoch 隔离后续的 scope-specific catalog plan。
	 */
	reuse = probe.epoch != 0 || probe.cache_ready;
	probe.key = *key;
	agent_metadata_probe_new_epoch(reuse);
	return AGENT_META_BANK_VALID;
}

void agent_metadata_probe_invalidate(const struct agent_metadata_probe_key *key)
{
	if (key != 0 && probe.epoch != 0 &&
	    probe.key.authority_cookie == key->authority_cookie &&
	    probe.key.store_epoch == key->store_epoch &&
	    probe.key.reload_scope == key->reload_scope &&
	    probe.key.force == key->force)
		agent_metadata_probe_release(0);
}
static int agent_metadata_probe_fault(int bank, int allowed)
{
	return agent_metadata_recovery_test_fault(bank, allowed);
}
static int agent_metadata_probe_open(int bank, struct inode **result)
{
	struct inode *ip;
	int status = FS_LOOKUP_ERROR;
	int inode_status;
	*result = 0;
	ip = namei_scope_status(agent_meta_store_io_name(bank),
				VFS_POLICY_KERNEL_PRIVATE, VFS_SCOPE_NONE,
				&status);
	if (ip == 0)
		return status == FS_LOOKUP_ABSENT ? AGENT_META_BANK_ABSENT :
		       status == FS_LOOKUP_BUSY	  ? AGENT_META_BANK_BUSY :
						    AGENT_META_BANK_IO;
	inode_status = ivalid(ip);
	if (inode_status < 0) {
		iput(ip);
		return inode_status == VIRTIO_DISK_ERR_BUSY ?
			       AGENT_META_BANK_BUSY :
			       AGENT_META_BANK_IO;
	}
	if (ip->type != T_FILE || !vfs_inode_label_valid(ip) ||
	    ip->vfs_policy != VFS_POLICY_KERNEL_PRIVATE) {
		iput(ip);
		return AGENT_META_BANK_CORRUPT;
	}
	*result = ip;
	return AGENT_META_BANK_VALID;
}
static int agent_metadata_probe_piece(struct inode *ip,
				      const struct vfs_cred *cred, char *dst,
				      uint file_offset, uint length)
{
	while (probe.cursor.offset < length) {
		int n = readi_device(ip, cred, 0,
				     (uint64)(dst + probe.cursor.offset),
				     file_offset + probe.cursor.offset,
				     length - probe.cursor.offset);
		struct bio_checkpoint_result checkpoint;
		if (n == VIRTIO_DISK_ERR_BUSY)
			return AGENT_META_BANK_BUSY;
		if (n < 0)
			return AGENT_META_BANK_IO;
		if (n == 0 || (uint)n > length - probe.cursor.offset)
			return AGENT_META_BANK_CORRUPT;
		probe.cursor.offset += n;
		probe.progress_sequence++;
		probe.progress_bank = probe.cursor.bank;
		probe.progress_phase = probe.cursor.phase;
		probe.progress_offset = probe.cursor.offset;
		checkpoint = agent_metadata_txn_checkpoint_unlocked();
		if (!agent_metadata_reload_is_current() ||
		    !agent_meta_store_io_owned())
			return AGENT_META_BANK_INTERRUPTED;
		if (bio_checkpoint_should_stop(checkpoint))
			return checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
				       AGENT_META_BANK_PROGRESS :
				       AGENT_META_BANK_INTERRUPTED;
	}
	return AGENT_META_BANK_VALID;
}
static int agent_metadata_probe_header_valid(struct agent_meta_store *store,
					     uint64 inode_size)
{
	uint64 version = store->header.version;
	uint store_bytes;
	int size_status;
	if (store->header.magic == 0 && version == 0 &&
	    store->header.count == 0 && store->header.generation == 0 &&
	    store->header.payload_hash == 0)
		return AGENT_META_BANK_UNCOMMITTED;
	if (store->header.magic != AGENT_META_STORE_MAGIC ||
	    (version != AGENT_META_STORE_VERSION &&
	     version != AGENT_META_STORE_VERSION_V7 &&
	     version != AGENT_META_STORE_VERSION_V5) ||
	    store->header.generation == 0)
		return AGENT_META_BANK_CORRUPT;
	if (version == AGENT_META_STORE_VERSION)
		size_status = agent_meta_format_store_bytes(store->header.count,
							    &store_bytes);
	else if (version == AGENT_META_STORE_VERSION_V7)
		size_status = agent_meta_format_store_v7_bytes(
			store->header.count, &store_bytes);
	else
		size_status = agent_meta_format_store_v5_bytes(
			store->header.count, &store_bytes);
	if (size_status < 0 || inode_size < store_bytes)
		return AGENT_META_BANK_CORRUPT;
	if (version == AGENT_META_STORE_VERSION &&
	    inode_size < AGENT_META_STORE_MAX_BYTES)
		return AGENT_META_BANK_CORRUPT;
	probe.cursor.store_bytes = store_bytes;
	return AGENT_META_BANK_VALID;
}
static int agent_metadata_probe_validate(struct agent_meta_store *store,
					 uint64 *generation,
					 uint64 *payload_hash, int *migration)
{
	uint64 version = store->header.version;
	char *payload = version == AGENT_META_STORE_VERSION ||
					version == AGENT_META_STORE_VERSION_V7 ?
				(char *)&store->durable :
				(char *)store->records;
	uint payload_bytes = probe.cursor.store_bytes - sizeof(store->header);
	if (memcmp(&store->header, &probe.cursor.verify_header,
		   sizeof(store->header)) != 0 ||
	    store->header.payload_hash !=
		    agent_meta_format_payload_hash(&store->header, payload,
						   payload_bytes) ||
	    (version == AGENT_META_STORE_VERSION ?
		     !agent_meta_format_records_valid(store) :
	     version == AGENT_META_STORE_VERSION_V7 ?
		     !agent_meta_format_v7_records_valid(store) :
		     !agent_meta_format_v5_records_valid(store)))
		return AGENT_META_BANK_CORRUPT;
	*migration = 0;
	if (version == AGENT_META_STORE_VERSION_V5) {
		if (agent_meta_format_migrate_v5(store) < 0)
			return AGENT_META_BANK_CORRUPT;
		*migration = AGENT_META_STORE_VERSION_V5;
	} else if (version == AGENT_META_STORE_VERSION_V7) {
		if (agent_meta_format_migrate_v7(store) < 0)
			return AGENT_META_BANK_CORRUPT;
		*migration = AGENT_META_STORE_VERSION_V7;
	}
	*generation = store->header.generation;
	*payload_hash = store->header.payload_hash;
	return AGENT_META_BANK_VALID;
}
static int agent_metadata_probe_read(const struct agent_metadata_probe_key *key,
				     int bank, int confirm,
				     struct agent_meta_store *store,
				     uint64 *generation, uint64 *payload_hash,
				     int *migration, int allow_fault)
{
	struct inode *ip = 0;
	struct vfs_cred cred;
	char *payload;
	int status;
	uint64 progress_before = probe.progress_sequence;
	status = agent_metadata_probe_bind(key);
	if (status != AGENT_META_BANK_VALID)
		return status;
	if (!confirm && probe.summary[bank].classified) {
		*generation = probe.summary[bank].generation;
		*payload_hash = probe.summary[bank].payload_hash;
		*migration = probe.summary[bank].migration;
		return probe.summary[bank].status;
	}
	if (!probe.cursor.active) {
		status = agent_metadata_probe_fault(bank,
						    allow_fault && !confirm);
		if (status != AGENT_META_BANK_VALID) {
			agent_metadata_probe_release(0);
			return status;
		}
	}
	status = agent_metadata_probe_open(bank, &ip);
	if (status != AGENT_META_BANK_VALID) {
		if (probe.cursor.active && status != AGENT_META_BANK_BUSY &&
		    status != AGENT_META_BANK_IO) {
			agent_metadata_probe_release(0);
			return AGENT_META_BANK_CORRUPT;
		}
		if (probe.cursor.active) {
			agent_metadata_probe_release(0);
			return status;
		}
		if (!confirm && !probe.cursor.active &&
		    (status >= 0 || status == AGENT_META_BANK_CORRUPT)) {
			probe.summary[bank].classified = 1;
			probe.summary[bank].status = status;
		}
		if (status == AGENT_META_BANK_BUSY ||
		    status == AGENT_META_BANK_IO)
			agent_metadata_probe_release(0);
		return status;
	}
	if (!probe.cursor.active) {
		memset(store, 0, sizeof(*store));
		probe.cursor.active = 1;
		probe.cursor.bank = bank;
		probe.cursor.confirm = confirm;
		probe.cursor.phase = AGENT_META_PROBE_HEADER;
		probe.cursor.dev = ip->dev;
		probe.cursor.inum = ip->inum;
		probe.cursor.incarnation = ip->vfs_incarnation;
		probe.cursor.inode_size = ip->size;
	} else if (probe.cursor.bank != bank ||
		   probe.cursor.confirm != confirm ||
		   probe.cursor.dev != ip->dev ||
		   probe.cursor.inum != ip->inum ||
		   probe.cursor.incarnation != ip->vfs_incarnation ||
		   probe.cursor.inode_size != ip->size) {
		iput(ip);
		agent_metadata_probe_release(0);
		return AGENT_META_BANK_CORRUPT;
	}
	if (ip->size < sizeof(store->header)) {
		iput(ip);
		if (!confirm) {
			memset(&probe.cursor, 0, sizeof(probe.cursor));
			probe.summary[bank].classified = 1;
			probe.summary[bank].status =
				AGENT_META_BANK_UNCOMMITTED;
		} else
			agent_metadata_probe_release(0);
		return AGENT_META_BANK_UNCOMMITTED;
	}
	vfs_cred_kernel(&cred);
	if (probe.cursor.phase == AGENT_META_PROBE_HEADER) {
		status = agent_metadata_probe_piece(ip, &cred,
						    (char *)&store->header, 0,
						    sizeof(store->header));
		if (status != AGENT_META_BANK_VALID)
			goto out;
		status = agent_metadata_probe_header_valid(store, ip->size);
		if (status != AGENT_META_BANK_VALID)
			goto out_reset;
		probe.cursor.phase = AGENT_META_PROBE_PAYLOAD;
		probe.cursor.offset = 0;
	}
	payload = store->header.version == AGENT_META_STORE_VERSION ||
				  store->header.version ==
					  AGENT_META_STORE_VERSION_V7 ?
			  (char *)&store->durable :
			  (char *)store->records;
	if (probe.cursor.phase == AGENT_META_PROBE_PAYLOAD) {
		status = agent_metadata_probe_piece(
			ip, &cred, payload, sizeof(store->header),
			probe.cursor.store_bytes - sizeof(store->header));
		if (status != AGENT_META_BANK_VALID)
			goto out;
		probe.cursor.phase = AGENT_META_PROBE_VERIFY_HEADER;
		probe.cursor.offset = 0;
		memset(&probe.cursor.verify_header, 0,
		       sizeof(probe.cursor.verify_header));
	}
	if (probe.cursor.phase == AGENT_META_PROBE_VERIFY_HEADER) {
		status = agent_metadata_probe_piece(
			ip, &cred, (char *)&probe.cursor.verify_header, 0,
			sizeof(probe.cursor.verify_header));
		if (status != AGENT_META_BANK_VALID)
			goto out;
		status = agent_metadata_probe_validate(store, generation,
						       payload_hash, migration);
		if (status != AGENT_META_BANK_VALID)
			goto out_reset;
		status = agent_meta_journal_replay_init(
			&probe.cursor.journal_replay, *generation,
			*payload_hash);
		if (status != AGENT_META_JOURNAL_OK)
			goto corrupt;
		if (*migration != 0)
			goto journal_complete;
		probe.cursor.phase = AGENT_META_PROBE_JOURNAL;
		probe.cursor.offset = probe.cursor.journal_block = 0;
	}
	while (probe.cursor.phase == AGENT_META_PROBE_JOURNAL &&
	       probe.cursor.journal_block < AGENT_META_JOURNAL_BLOCKS) {
		status = agent_metadata_probe_piece(
			ip, &cred, probe.cursor.journal_data,
			AGENT_META_JOURNAL_OFFSET +
				probe.cursor.journal_block *
					AGENT_META_JOURNAL_BLOCK_BYTES,
			AGENT_META_JOURNAL_BLOCK_BYTES);
		if (status != AGENT_META_BANK_VALID)
			goto out;
		status = agent_meta_journal_replay_block(
			&probe.cursor.journal_replay, store,
			probe.cursor.journal_data, probe.cursor.journal_block);
		if (status != AGENT_META_JOURNAL_OK)
			goto corrupt;
		probe.cursor.journal_block++;
		probe.cursor.offset = 0;
	}
	if (probe.cursor.phase == AGENT_META_PROBE_JOURNAL &&
	    agent_meta_journal_replay_finish(&probe.cursor.journal_replay) !=
		    AGENT_META_JOURNAL_OK)
		goto corrupt;
journal_complete:
	*generation = probe.cursor.journal_replay.cursor.generation;
	*payload_hash = store->header.payload_hash;
	{
		struct agent_meta_journal_cursor recovered_cursor =
			probe.cursor.journal_replay.cursor;
		memset(&probe.cursor, 0, sizeof(probe.cursor));
		if (!confirm) {
			probe.summary[bank].classified = 1;
			probe.summary[bank].status = AGENT_META_BANK_VALID;
			probe.summary[bank].generation = *generation;
			probe.summary[bank].payload_hash = *payload_hash;
			probe.summary[bank].migration = *migration;
			probe.summary[bank].journal_cursor = recovered_cursor;
		}
	}
	iput(ip);
	return AGENT_META_BANK_VALID;
corrupt:
	status = AGENT_META_BANK_CORRUPT;
	goto out_reset;
out:
	iput(ip);
	if (status == AGENT_META_BANK_BUSY &&
	    probe.progress_sequence != progress_before)
		status = AGENT_META_BANK_PROGRESS;
	if (status != AGENT_META_BANK_PROGRESS)
		agent_metadata_probe_release(0);
	return status;
out_reset:
	iput(ip);
	if (!confirm && (status >= 0 || status == AGENT_META_BANK_CORRUPT)) {
		memset(&probe.cursor, 0, sizeof(probe.cursor));
		probe.summary[bank].classified = 1;
		probe.summary[bank].status = status;
	} else
		agent_metadata_probe_release(0);
	return status;
}
int agent_metadata_probe_summary(const struct agent_metadata_probe_key *key,
				 int bank, struct agent_meta_store *store,
				 uint64 *generation, uint64 *payload_hash,
				 int *migration, int allow_fault)
{
	if (key == 0 || store == 0 || generation == 0 || payload_hash == 0 ||
	    migration == 0 || bank < 0 || bank >= AGENT_META_STORE_BANKS)
		return AGENT_META_BANK_CORRUPT;
	return agent_metadata_probe_read(key, bank, 0, store, generation,
					 payload_hash, migration, allow_fault);
}
int agent_metadata_probe_confirm(const struct agent_metadata_probe_key *key,
				 int bank, struct agent_meta_store *store,
				 uint64 expected_generation,
				 uint64 expected_hash, int expected_migration)
{
	uint64 generation = 0, payload_hash = 0;
	int migration = 0, status;
	status = agent_metadata_probe_bind(key);
	if (status != AGENT_META_BANK_VALID)
		return status;
	if (probe.confirmed_bank == bank && probe.summary[bank].classified &&
	    probe.summary[bank].status == AGENT_META_BANK_VALID &&
	    probe.summary[bank].generation == expected_generation &&
	    probe.summary[bank].payload_hash == expected_hash &&
	    probe.summary[bank].migration == expected_migration)
		return AGENT_META_BANK_VALID;
	status = agent_metadata_probe_read(key, bank, 1, store, &generation,
					   &payload_hash, &migration, 0);
	if (status != AGENT_META_BANK_VALID) {
		return status;
	}
	if (generation != expected_generation ||
	    payload_hash != expected_hash || migration != expected_migration) {
		agent_metadata_probe_release(0);
		return AGENT_META_BANK_CORRUPT;
	}
	probe.confirmed_bank = bank;
	return AGENT_META_BANK_VALID;
}
int agent_metadata_probe_journal_cursor(
	int bank, struct agent_meta_journal_cursor *cursor)
{
	if (cursor == 0 || bank < 0 || bank >= AGENT_META_STORE_BANKS ||
	    !probe.summary[bank].classified ||
	    probe.summary[bank].status != AGENT_META_BANK_VALID)
		return -1;
	*cursor = probe.summary[bank].journal_cursor;
	return 0;
}
uint64 agent_metadata_probe_epoch(void)
{
	return probe.epoch;
}
void agent_metadata_probe_finish(uint64 epoch)
{
	if (epoch == 0 || probe.epoch != epoch)
		panic("metadata probe epoch invariant");
	agent_metadata_probe_release(1);
}
void agent_metadata_probe_catalog_progress(int bank, uint offset)
{
	if (bank < 0 || bank >= AGENT_META_STORE_BANKS || offset == 0)
		panic("metadata catalog progress invariant");
	probe.progress_sequence++;
	probe.progress_bank = bank;
	probe.progress_phase = 4;
	probe.progress_offset = offset;
}
#ifdef AGENT_METADATA_BOOT_READ_FAULT
void agent_metadata_probe_progress(uint64 *sequence, int *bank, uint *phase,
				   uint *offset)
{
	if (sequence)
		*sequence = probe.progress_sequence;
	if (bank)
		*bank = probe.progress_bank;
	if (phase)
		*phase = probe.progress_phase;
	if (offset)
		*offset = probe.progress_offset;
}
#endif
