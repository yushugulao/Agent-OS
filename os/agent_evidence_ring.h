#ifndef AGENT_EVIDENCE_RING_H
#define AGENT_EVIDENCE_RING_H

#include "agent_sha256.h"
#include "workflow_lifecycle.h"

struct proc;
struct agent_context_record;
struct agent_audit_record;
struct agent_timeline_record;

/* Keep critical evidence independently admissible from success telemetry. */
#define AGENT_EVIDENCE_ORDINARY_CAP 48U
#define AGENT_EVIDENCE_CRITICAL_CAP 16U
#define AGENT_EVIDENCE_CAP \
	(AGENT_EVIDENCE_ORDINARY_CAP + AGENT_EVIDENCE_CRITICAL_CAP)

#define AGENT_EVIDENCE_REF_CRITICAL 0x8000U
#define AGENT_EVIDENCE_REF_INDEX_MASK 0x00ffU

struct agent_evidence_view_entry {
	uint64 ticket;
	ushort ref;
};

/* Immutable read cursor. A slot is accepted only while its ticket matches. */
struct agent_evidence_view {
	struct workflow_lifecycle_key key;
	uint visible_records;
	uint reserved;
	uint64 total_records;
	uint64 critical_records;
	uint64 gap_count;
	uint64 observe_epoch;
	uint64 first_ticket;
	uint64 last_ticket;
	uint64 last_fence_sequence;
	uchar sealed_root[AGENT_SHA256_DIGEST_SIZE];
	struct agent_evidence_view_entry entries[AGENT_EVIDENCE_CAP];
};

struct agent_evidence_seal_result {
	uchar previous_root[AGENT_SHA256_DIGEST_SIZE];
	uchar root[AGENT_SHA256_DIGEST_SIZE];
	uint64 first_ticket;
	uint64 last_ticket;
	uint64 event_count;
	uint64 gap_count;
	uint64 fence_sequence;
	uint64 segment_sequence;
	uint64 metadata_generation;
	uint64 credit_epoch;
};

#define AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE (1U << 0)
#define AGENT_EVIDENCE_RETAINED_F_INTERNAL_RETIRE (1U << 1)

/*
 * Kernel-only tombstone for the last root published from a lifecycle slot.
 * INTERNAL_RETIRE is deliberately a different contract from a
 * challenge-bound workflow-fence receipt.
 */
struct agent_evidence_retained_seal {
	struct workflow_lifecycle_key key;
	uint flags;
	uint reserved;
	uchar previous_root[AGENT_SHA256_DIGEST_SIZE];
	uchar root[AGENT_SHA256_DIGEST_SIZE];
	uint64 first_ticket;
	uint64 last_ticket;
	uint64 event_count;
	uint64 gap_count;
	uint64 last_workflow_fence_sequence;
	uint64 sealed_ticket_highwater;
	uint64 segment_sequence;
	uint64 metadata_generation;
	uint64 credit_epoch;
};

/* Kernel-only two-phase ticket used by fail-before-effect security denial. */
struct agent_evidence_security_reservation {
	struct workflow_lifecycle_key key;
	void *slot;
	uint64 ticket;
	int active;
};

/*
 * A tool does not know whether its terminal record will be ordinary or
 * critical until after execution.  Reserving one slot in each partition with
 * one shared ticket keeps the critical reserve independent while making the
 * eventual Context/Evidence commit infallible after a side effect.
 */
struct agent_evidence_context_reservation {
	struct workflow_lifecycle_key key;
	void *ordinary_slot;
	void *critical_slot;
	uint64 ticket;
	int active;
};

void agent_evidence_init(void);
/*
 * A frozen execution contract may deny a direct syscall from a non-Agent
 * participant that has no Context pages of its own.  The trusted controller
 * must provision the lifecycle ring before publishing that contract.  The
 * append path is deliberately allocation- and rollover-free.
 */
int agent_evidence_prepare_direct_denials(
	struct proc *, struct workflow_lifecycle_key);
int agent_evidence_context_preallocated(
	struct proc *, struct workflow_lifecycle_key);
int agent_evidence_append_direct_syscall_denial(
	struct proc *, struct workflow_lifecycle_key, int, uint64, uint64 *);
int agent_evidence_append_context(
	struct proc *, const struct agent_context_record *, uint64, uint64, int,
	uint64, uint64, int, int, uint64 *);
int agent_evidence_context_reserve(
	struct proc *, struct agent_evidence_context_reservation *);
void agent_evidence_context_abort(
	struct agent_evidence_context_reservation *);
int agent_evidence_context_commit(
	struct proc *, const struct agent_context_record *, uint64, uint64, int,
	uint64, uint64, int, int,
	struct agent_evidence_context_reservation *, uint64 *);
int agent_evidence_security_reserve(
	struct proc *, struct agent_evidence_security_reservation *);
void agent_evidence_security_abort(
	struct agent_evidence_security_reservation *);
int agent_evidence_security_commit(
	struct proc *, const struct agent_context_record *, uint64, uint64, int,
	uint64, uint64, struct agent_evidence_security_reservation *, uint64 *);
int agent_evidence_view_open(struct workflow_lifecycle_key,
			     struct agent_evidence_view *);
int agent_evidence_view_record(const struct agent_evidence_view *, uint,
			       struct agent_audit_record *, uint64 *);
int agent_evidence_view_timeline(const struct agent_evidence_view *, uint,
				 struct agent_timeline_record *, uint64 *);
uint agent_evidence_view_count_pid(const struct agent_evidence_view *, int);
int agent_evidence_view_digest(
	const struct agent_evidence_view *,
	uchar[AGENT_SHA256_DIGEST_SIZE]);
int agent_evidence_reclaim(struct workflow_lifecycle_key);
int agent_evidence_retained_get(struct workflow_lifecycle_key,
				struct agent_evidence_retained_seal *);
int agent_evidence_ticket_fence_sealed(struct workflow_lifecycle_key, uint64,
				       uint64 *);

int agent_evidence_seal(struct workflow_lifecycle_key, uint64,
			const uchar[AGENT_SHA256_DIGEST_SIZE], uint64, uint64,
			const uchar[AGENT_SHA256_DIGEST_SIZE],
			struct agent_evidence_seal_result *);

#endif
