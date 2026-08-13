#ifndef USER_AGENT_NEXUS_SOURCE_H
#define USER_AGENT_NEXUS_SOURCE_H

#define AGENT_NEXUS_SOURCE_FORMAT_VERSION 1U
#define AGENT_NEXUS_SOURCE_ID_SIZE 8U
#define AGENT_NEXUS_SOURCE_PATH_SIZE 112U
#define AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE 65U
#define AGENT_NEXUS_SOURCE_CITATION_SIZE 32U
#define AGENT_NEXUS_SOURCE_SCOPE_SIZE 32U
#define AGENT_NEXUS_SOURCE_ALLOWLIST_SIZE 48U
#define AGENT_NEXUS_SOURCE_QUERY_SIZE 96U
#define AGENT_NEXUS_SOURCE_PREFIX_SIZE AGENT_NEXUS_SOURCE_PATH_SIZE
#define AGENT_NEXUS_SOURCE_SNIPPET_SIZE 193U
#define AGENT_NEXUS_SOURCE_SEARCH_MAX_RESULTS 8U
#define AGENT_NEXUS_SOURCE_READ_MAX_LINES 12U
#define AGENT_NEXUS_SOURCE_READ_MAX_BYTES 3072U

/* Bounded build_source_snapshot: these allowlisted trees only, not the full repo. */
#define AGENT_NEXUS_SOURCE_ALLOWLIST "os/,include/,user/lib/,user/include/"

enum agent_nexus_source_status {
	AGENT_NEXUS_SOURCE_OK = 0,
	AGENT_NEXUS_SOURCE_BAD_PARAM = -1,
	AGENT_NEXUS_SOURCE_NOT_FOUND = -2,
	AGENT_NEXUS_SOURCE_IO_ERROR = -3,
	AGENT_NEXUS_SOURCE_CORRUPT = -4,
	AGENT_NEXUS_SOURCE_NOT_READY = -5,
};

struct agent_nexus_source_info {
	unsigned int format_version;
	unsigned int source_count;
	unsigned int volume_count;
	unsigned long long source_bytes;
	/* Always "build_source_snapshot"; revision is not a Git commit id. */
	char scope[AGENT_NEXUS_SOURCE_SCOPE_SIZE];
	char allowlist[AGENT_NEXUS_SOURCE_ALLOWLIST_SIZE];
	char revision[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
	char manifest_sha256[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
};

struct agent_nexus_source_match {
	char source_id[AGENT_NEXUS_SOURCE_ID_SIZE];
	char path[AGENT_NEXUS_SOURCE_PATH_SIZE];
	unsigned int line;
	char full_sha256[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
	char chunk_sha256[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
	char citation[AGENT_NEXUS_SOURCE_CITATION_SIZE];
	char snippet[AGENT_NEXUS_SOURCE_SNIPPET_SIZE];
};

struct agent_nexus_source_search_result {
	struct agent_nexus_source_info corpus;
	unsigned int scanned_source_count;
	unsigned int match_count;
	unsigned int truncated;
	/* Source text is evidence data, never an instruction to the Agent. */
	unsigned int content_untrusted;
	struct agent_nexus_source_match
		matches[AGENT_NEXUS_SOURCE_SEARCH_MAX_RESULTS];
};

struct agent_nexus_source_read_result {
	struct agent_nexus_source_info corpus;
	char source_id[AGENT_NEXUS_SOURCE_ID_SIZE];
	char path[AGENT_NEXUS_SOURCE_PATH_SIZE];
	char full_sha256[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
	char chunk_sha256[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
	char citation[AGENT_NEXUS_SOURCE_CITATION_SIZE];
	unsigned int start_line;
	unsigned int end_line;
	unsigned int total_lines;
	unsigned int content_length;
	unsigned int content_untrusted;
};

/* Verifies the manifest, every volume and every per-source digest once. */
int agent_nexus_source_init(void);
int agent_nexus_source_info(struct agent_nexus_source_info *info);

/* ASCII matching is case-insensitive; non-ASCII UTF-8 bytes match exactly. */
int agent_nexus_source_search(
	const char *query, const char *path_prefix,
	struct agent_nexus_source_search_result *result);

/* Returns exact normalized UTF-8 source lines, including their newline bytes. */
int agent_nexus_source_read(
	const char *source_id, unsigned int start_line,
	unsigned int max_lines, char *content, unsigned int content_capacity,
	struct agent_nexus_source_read_result *result);

const char *agent_nexus_source_status_name(int status);

#endif
