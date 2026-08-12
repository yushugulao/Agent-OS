#ifndef AGENT_FILE_PUBLISH_ABI_H
#define AGENT_FILE_PUBLISH_ABI_H

/* One syscall publishes one complete, workflow-scoped result file. */
#define AGENT_FILE_PUBLISH_SYSCALL 566U
#define AGENT_FILE_PUBLISH_VERSION 1U
#define AGENT_FILE_PUBLISH_MAX_BYTES 4096U

struct agent_file_publish_request {
	unsigned int version;
	unsigned int size;
	unsigned int flags;
	unsigned int reserved;
	unsigned long long path;
	unsigned long long header;
	unsigned long long payload;
	unsigned int header_size;
	unsigned int payload_size;
	unsigned long long reserved_tail[2];
};

_Static_assert(sizeof(struct agent_file_publish_request) == 64,
	       "Agent file publish request ABI layout");

#endif
