#ifndef AGENT_PROVENANCE_ABI_H
#define AGENT_PROVENANCE_ABI_H

/* Fixed provenance vocabulary shared by the kernel and user-space gateways. */
#define AGENT_PROVENANCE_FINGERPRINT_SIZE 32U
#define AGENT_PROVENANCE_KERNEL_FACT           (1ULL << 0)
#define AGENT_PROVENANCE_TRUSTED_USER_CONTROL  (1ULL << 1)
#define AGENT_PROVENANCE_AGENT_DERIVED         (1ULL << 2)
#define AGENT_PROVENANCE_UNTRUSTED_FILE_DATA   (1ULL << 3)
#define AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT (1ULL << 4)
#define AGENT_PROVENANCE_CROSS_AGENT_DATA      (1ULL << 5)
#define AGENT_PROVENANCE_ALL                    ((1ULL << 6) - 1ULL)
#define AGENT_PROVENANCE_UNTRUSTED_MASK \
	(AGENT_PROVENANCE_UNTRUSTED_FILE_DATA | \
	 AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT | \
	 AGENT_PROVENANCE_CROSS_AGENT_DATA)

/* A manifest describes real effects. A contract may not hide any of them. */
#define AGENT_SIDE_EFFECT_FILE       (1ULL << 0)
#define AGENT_SIDE_EFFECT_METADATA   (1ULL << 1)
#define AGENT_SIDE_EFFECT_IPC        (1ULL << 2)
#define AGENT_SIDE_EFFECT_PROCESS    (1ULL << 3)
#define AGENT_SIDE_EFFECT_PERMISSION (1ULL << 4)
#define AGENT_SIDE_EFFECT_ARTIFACT   (1ULL << 5)
#define AGENT_SIDE_EFFECT_WATCH      (1ULL << 6)
#define AGENT_SIDE_EFFECT_ALL        ((1ULL << 7) - 1ULL)

/* Context record layout is unchanged; labels are a hash-bound flag projection. */
#define AGENT_CONTEXT_PROVENANCE_SHIFT 16U
#define AGENT_CONTEXT_PROVENANCE_MASK \
	(AGENT_PROVENANCE_ALL << AGENT_CONTEXT_PROVENANCE_SHIFT)
#define AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL (1ULL << 32)
#define AGENT_CONTEXT_PROVENANCE_ENCODE(labels) \
	(((labels) & AGENT_PROVENANCE_ALL) << AGENT_CONTEXT_PROVENANCE_SHIFT)
#define AGENT_CONTEXT_PROVENANCE_DECODE(flags) \
	(((flags) & AGENT_CONTEXT_PROVENANCE_MASK) >> \
	 AGENT_CONTEXT_PROVENANCE_SHIFT)

#define AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT          (1U << 0)
#define AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED         (1U << 1)
#define AGENT_PROVENANCE_AUTH_F_ALL \
	(AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT | \
	 AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED)

#define AGENT_PROVENANCE_DENY_NONE                    0U
#define AGENT_PROVENANCE_DENY_BAD_REQUEST             1U
#define AGENT_PROVENANCE_DENY_STALE_LIFECYCLE         2U
#define AGENT_PROVENANCE_DENY_MISSING_CONTRACT        3U
#define AGENT_PROVENANCE_DENY_ILLEGAL_PREDECESSOR     4U
#define AGENT_PROVENANCE_DENY_CAPABILITY_MISSING      5U
#define AGENT_PROVENANCE_DENY_UNKNOWN_PROVENANCE      6U
#define AGENT_PROVENANCE_DENY_PROVENANCE_NOT_ACCEPTED 7U
#define AGENT_PROVENANCE_DENY_EFFECT_MISMATCH         8U
#define AGENT_PROVENANCE_DENY_EVIDENCE_UNAVAILABLE    9U

struct agent_provenance_manifest {
	unsigned long long accepted_input_labels;
	unsigned long long output_add_labels;
	unsigned long long required_capabilities;
	unsigned long long side_effect_mask;
};

_Static_assert(sizeof(struct agent_provenance_manifest) == 32,
	       "Agent provenance manifest ABI layout");

#endif
