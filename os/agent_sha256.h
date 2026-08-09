#ifndef AGENT_SHA256_H
#define AGENT_SHA256_H

#include "types.h"

#define AGENT_SHA256_DIGEST_SIZE 32U

struct agent_sha256_ctx {
	uint32 state[8];
	uint64 bytes;
	uchar block[64];
	uint block_used;
};

void agent_sha256_init(struct agent_sha256_ctx *);
void agent_sha256_update(struct agent_sha256_ctx *, const void *, uint);
void agent_sha256_final(struct agent_sha256_ctx *, uchar[AGENT_SHA256_DIGEST_SIZE]);
void agent_sha256(const void *, uint, uchar[AGENT_SHA256_DIGEST_SIZE]);

#endif
