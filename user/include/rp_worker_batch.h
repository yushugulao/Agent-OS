#ifndef __RP_WORKER_BATCH_H__
#define __RP_WORKER_BATCH_H__

#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include <research_platform_state.h>
#include <rp_launch_attestation.h>

#define RP_WORKER_BATCH_MAGIC 0x52505742U
#define RP_WORKER_BATCH_VERSION 1U
#define RP_WORKER_BATCH_READY_INDEX 0xffffffffU
#define RP_WORKER_BATCH_MAX_FD 15

#define RP_WORKER_BATCH_ARG_READ_FD 2
#define RP_WORKER_BATCH_ARG_WRITE_FD 3
#define RP_WORKER_BATCH_ARG_NONCE 4
#define RP_WORKER_BATCH_ARGC 5
#define RP_WORKER_BATCH_NEXT_STOP (-1)

enum rp_worker_batch_kind {
	RP_WORKER_BATCH_READY = 1,
	RP_WORKER_BATCH_RUN = 2,
	RP_WORKER_BATCH_RESULT = 3,
	RP_WORKER_BATCH_STOP = 4,
	RP_WORKER_BATCH_STOPPED = 5,
};

enum rp_worker_batch_exit {
	RP_WORKER_BATCH_EXIT_USAGE = 90,
	RP_WORKER_BATCH_EXIT_READ = 91,
	RP_WORKER_BATCH_EXIT_PROTOCOL = 92,
	RP_WORKER_BATCH_EXIT_WRITE = 93,
};

/* Fixed-width, padding-free wire record shared with the orchestrator. */
struct rp_worker_batch_frame {
	uint32 magic;
	uint16 version;
	uint8 kind;
	uint8 group;
	uint32 index;
	int32 status;
	uint64 nonce;
	uint64 guard;
};

#ifdef RP_WORKER_BATCH_DISPATCHER
struct rp_worker_batch_runtime {
	struct rp_worker_batch_frame frame;
	uint64 nonce;
	uint32 expected;
	uint32 count;
	int read_fd;
	int write_fd;
	uint8 group;
};

static struct rp_worker_batch_runtime rp_worker_batch_runtime;
#endif

_Static_assert(sizeof(struct rp_worker_batch_frame) == 32,
	       "worker batch wire frame must remain 32 bytes");
_Static_assert(__builtin_offsetof(struct rp_worker_batch_frame, guard) == 24,
	       "worker batch guard offset changed");

static uint64 rp_worker_batch_guard(const struct rp_worker_batch_frame *frame)
{
	uint64 value = 0x726f757465723631ULL ^ frame->nonce;

	value ^= ((uint64)frame->magic << 32) |
		 ((uint64)frame->version << 16) |
		 ((uint64)frame->kind << 8) | frame->group;
	value ^= ((uint64)frame->index << 32) | (uint32)frame->status;
	value ^= value >> 30;
	value *= 0xbf58476d1ce4e5b9ULL;
	value ^= value >> 27;
	value *= 0x94d049bb133111ebULL;
	return value ^ (value >> 31);
}

static void rp_worker_batch_frame_init(struct rp_worker_batch_frame *frame,
				       uint8 kind, uint8 group,
				       uint32 index, int32 status,
				       uint64 nonce)
{
	memset(frame, 0, sizeof(*frame));
	frame->magic = RP_WORKER_BATCH_MAGIC;
	frame->version = RP_WORKER_BATCH_VERSION;
	frame->kind = kind;
	frame->group = group;
	frame->index = index;
	frame->status = status;
	frame->nonce = nonce;
	frame->guard = rp_worker_batch_guard(frame);
}

static int rp_worker_batch_read_exact(int fd, void *buffer, int length)
{
	int offset = 0;

	while (offset < length) {
		int amount = read(fd, (char *)buffer + offset, length - offset);

		if (amount <= 0)
			return 0;
		offset += amount;
	}
	return 1;
}

static int rp_worker_batch_write_exact(int fd, const void *buffer, int length)
{
	int offset = 0;

	while (offset < length) {
		int amount = write(fd, (const char *)buffer + offset,
				   length - offset);

		if (amount <= 0)
			return 0;
		offset += amount;
	}
	return 1;
}

#ifdef RP_WORKER_BATCH_DISPATCHER
static int rp_worker_batch_parse_fd(const char *text, int *fd)
{
	uint32 value = 0;

	if (text == 0 || text[0] < '0' || text[0] > '9' ||
	    (text[0] == '0' && text[1] != 0))
		return 0;
	for (int index = 0; text[index] != 0; index++) {
		uint32 digit;

		if (text[index] < '0' || text[index] > '9')
			return 0;
		digit = (uint32)(text[index] - '0');
		if (value > (RP_WORKER_BATCH_MAX_FD - digit) / 10)
			return 0;
		value = value * 10 + digit;
	}
	*fd = (int)value;
	return 1;
}

static int rp_worker_batch_parse_nonce(const char *text, uint64 *nonce)
{
	uint64 value = 0;

	if (text == 0)
		return 0;
	for (int index = 0; index < 16; index++) {
		char digit = text[index];
		uint64 nibble;

		if (digit >= '0' && digit <= '9')
			nibble = (uint64)(digit - '0');
		else if (digit >= 'a' && digit <= 'f')
			nibble = (uint64)(digit - 'a' + 10);
		else
			return 0;
		value = (value << 4) | nibble;
	}
	if (text[16] != 0 || value == 0)
		return 0;
	*nonce = value;
	return 1;
}
#endif

static int rp_worker_batch_frame_guard_valid(
	const struct rp_worker_batch_frame *frame)
{
	return frame->magic == RP_WORKER_BATCH_MAGIC &&
	       frame->version == RP_WORKER_BATCH_VERSION &&
	       frame->guard == rp_worker_batch_guard(frame);
}

#ifdef RP_WORKER_BATCH_DISPATCHER
static int rp_worker_batch_command_valid(
	const struct rp_worker_batch_frame *frame, uint8 kind, uint8 group,
	uint32 index, uint64 nonce)
{
	return rp_worker_batch_frame_guard_valid(frame) &&
	       frame->kind == kind && frame->group == group &&
	       frame->index == index && frame->status == 0 &&
	       frame->nonce == nonce;
}

static void rp_worker_batch_finish(void)
{
	close(rp_worker_batch_runtime.read_fd);
	close(rp_worker_batch_runtime.write_fd);
	rp_worker_batch_runtime.read_fd = -1;
	rp_worker_batch_runtime.write_fd = -1;
}

static __attribute__((noinline)) int
rp_worker_batch_start(uint8 group, uint32 count)
{
	struct rp_worker_batch_runtime *runtime = &rp_worker_batch_runtime;
	int read_fd;
	int write_fd;
	uint64 nonce;

	if (__argc != RP_WORKER_BATCH_ARGC || __argv == 0 ||
	    __argv[1] == 0 ||
	    strncmp(__argv[1], RP_LAUNCH_EXPECT_PREFIX,
		    strlen(RP_LAUNCH_EXPECT_PREFIX)) != 0 ||
	    !rp_worker_batch_parse_fd(__argv[RP_WORKER_BATCH_ARG_READ_FD],
				      &read_fd) ||
	    !rp_worker_batch_parse_fd(__argv[RP_WORKER_BATCH_ARG_WRITE_FD],
				      &write_fd) ||
	    read_fd == write_fd ||
	    !rp_worker_batch_parse_nonce(__argv[RP_WORKER_BATCH_ARG_NONCE],
					 &nonce) || count == 0)
		return RP_WORKER_BATCH_EXIT_USAGE;

	memset(runtime, 0, sizeof(*runtime));
	runtime->read_fd = read_fd;
	runtime->write_fd = write_fd;
	runtime->group = group;
	runtime->count = count;
	runtime->nonce = nonce;
	rp_worker_batch_frame_init(&runtime->frame, RP_WORKER_BATCH_READY,
				   group,
				   RP_WORKER_BATCH_READY_INDEX, 0, nonce);
	if (!rp_worker_batch_write_exact(write_fd, &runtime->frame,
					 sizeof(runtime->frame))) {
		rp_worker_batch_finish();
		return RP_WORKER_BATCH_EXIT_WRITE;
	}
	return 0;
}

static __attribute__((noinline)) int rp_worker_batch_next(void)
{
	struct rp_worker_batch_runtime *runtime = &rp_worker_batch_runtime;

	if (!rp_worker_batch_read_exact(runtime->read_fd, &runtime->frame,
					sizeof(runtime->frame))) {
		rp_worker_batch_finish();
		return -RP_WORKER_BATCH_EXIT_READ;
	}
	if (runtime->frame.kind == RP_WORKER_BATCH_RUN &&
	    runtime->expected < runtime->count &&
	    rp_worker_batch_command_valid(&runtime->frame,
					  RP_WORKER_BATCH_RUN,
					  runtime->group,
					  runtime->expected,
					  runtime->nonce)) {
		memset(rp_state_buf, 0, sizeof(rp_state_buf));
		return (int)runtime->expected;
	}
	if (runtime->frame.kind == RP_WORKER_BATCH_STOP &&
	    runtime->expected == runtime->count &&
	    rp_worker_batch_command_valid(&runtime->frame,
					  RP_WORKER_BATCH_STOP,
					  runtime->group,
					  runtime->expected,
					  runtime->nonce)) {
		rp_worker_batch_frame_init(&runtime->frame,
				   RP_WORKER_BATCH_STOPPED, runtime->group,
				   runtime->expected, 0, runtime->nonce);
		if (!rp_worker_batch_write_exact(runtime->write_fd,
						 &runtime->frame,
						 sizeof(runtime->frame))) {
			rp_worker_batch_finish();
			return -RP_WORKER_BATCH_EXIT_WRITE;
		}
		rp_worker_batch_finish();
		return RP_WORKER_BATCH_NEXT_STOP;
	}
	rp_worker_batch_finish();
	return -RP_WORKER_BATCH_EXIT_PROTOCOL;
}

static __attribute__((noinline)) int rp_worker_batch_report(int status)
{
	struct rp_worker_batch_runtime *runtime = &rp_worker_batch_runtime;

	rp_worker_batch_frame_init(&runtime->frame, RP_WORKER_BATCH_RESULT,
				   runtime->group, runtime->expected,
				   status, runtime->nonce);
	if (!rp_worker_batch_write_exact(runtime->write_fd, &runtime->frame,
					 sizeof(runtime->frame))) {
		rp_worker_batch_finish();
		return RP_WORKER_BATCH_EXIT_WRITE;
	}
	runtime->expected++;
	if (status != 0)
		rp_worker_batch_finish();
	return status;
}
#endif

#endif
