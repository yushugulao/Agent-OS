#include <agent_nexus.h>
#include <agent_nexus_source.h>
#include <agent_nexus_source_anchor.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#define NXS_MANIFEST_NAME "nxsrcmeta"
#define NXS_MANIFEST_HEADER_SIZE 128U
#define NXS_DESCRIPTOR_SIZE 56U
#define NXS_RECORD_HEADER_SIZE 96U
#define NXS_MAX_VOLUMES 16U
#define NXS_MAX_SOURCES 9999U
#define NXS_MAX_CORPUS_BYTES (8U * 1024U * 1024U)
#define NXS_MAX_SOURCE_BYTES 258048U
#define NXS_MAX_LINE_BYTES 192U

struct nxs_sha256_context {
	unsigned int state[8];
	unsigned long long bit_count;
	unsigned char block[64];
	unsigned int used;
};

struct nxs_volume_descriptor {
	char name[16];
	unsigned int size;
	unsigned int records;
	unsigned char sha256[AGENT_NEXUS_SHA256_SIZE];
};

struct nxs_state {
	int ready;
	struct agent_nexus_source_info info;
	struct nxs_volume_descriptor volumes[NXS_MAX_VOLUMES];
};

struct nxs_reader {
	int fd;
	unsigned int offset;
	struct nxs_sha256_context digest;
};

enum nxs_scan_mode {
	NXS_SCAN_VERIFY = 0,
	NXS_SCAN_SEARCH,
	NXS_SCAN_READ,
};

struct nxs_scan {
	enum nxs_scan_mode mode;
	const char *query;
	unsigned int query_length;
	const char *prefix;
	unsigned int prefix_length;
	struct agent_nexus_source_search_result *search;
	const char *target_id;
	unsigned int read_start;
	unsigned int read_lines;
	char *read_content;
	unsigned int read_capacity;
	struct agent_nexus_source_read_result *read;
	unsigned int read_found;
	unsigned int read_overflow;
	struct nxs_sha256_context revision;
	unsigned int source_number;
	unsigned int source_bytes;
	char previous_path[AGENT_NEXUS_SOURCE_PATH_SIZE];
};

struct nxs_record_workspace {
	unsigned char header[NXS_RECORD_HEADER_SIZE];
	unsigned char expected_header_sha[AGENT_NEXUS_SHA256_SIZE];
	unsigned char source_digest[AGENT_NEXUS_SHA256_SIZE];
	struct nxs_sha256_context source_sha;
	struct nxs_sha256_context read_chunk_sha;
	char source_id[AGENT_NEXUS_SOURCE_ID_SIZE];
	char expected_id[AGENT_NEXUS_SOURCE_ID_SIZE];
	char path[AGENT_NEXUS_SOURCE_PATH_SIZE];
	char line[NXS_MAX_LINE_BYTES + 1U];
	unsigned char block[256];
	unsigned char size_bytes[8];
	unsigned char chunk_digest[AGENT_NEXUS_SHA256_SIZE];
	struct nxs_sha256_context line_sha;
	unsigned char line_digest[AGENT_NEXUS_SHA256_SIZE];
};

/*
 * A uCore process has one 4 KiB user stack.  Keep the bounded streaming
 * buffers in per-process BSS so nested scanner calls cannot consume most of
 * that page before reading a byte.  The guard makes overlapping or concurrent
 * use fail closed instead of sharing partially updated scratch state.
 */
struct nxs_workspace {
	unsigned int active;
	struct nxs_scan scan;
	struct nxs_reader reader;
	struct nxs_record_workspace record;
	unsigned char volume_digest[AGENT_NEXUS_SHA256_SIZE];
	unsigned char revision_digest[AGENT_NEXUS_SHA256_SIZE];
	char revision_hex[AGENT_NEXUS_SOURCE_SHA256_HEX_SIZE];
	unsigned char manifest_header[NXS_MANIFEST_HEADER_SIZE];
	unsigned char descriptors_sha[AGENT_NEXUS_SHA256_SIZE];
	unsigned char manifest_sha[AGENT_NEXUS_SHA256_SIZE];
	struct nxs_sha256_context descriptor_digest;
	unsigned char descriptor[NXS_DESCRIPTOR_SIZE];
};

static struct nxs_state nxs_corpus;
static struct nxs_workspace nxs_work;

static int nxs_workspace_acquire(void)
{
	return __atomic_exchange_n(&nxs_work.active, 1U, __ATOMIC_ACQUIRE) == 0;
}

static void nxs_workspace_release(void)
{
	__atomic_store_n(&nxs_work.active, 0U, __ATOMIC_RELEASE);
}

static unsigned int nxs_rotr(unsigned int value, unsigned int shift)
{
	return (value >> shift) | (value << (32U - shift));
}

static void __attribute__((noinline))
nxs_sha_transform(struct nxs_sha256_context *ctx,
		  const unsigned char block[64])
{
	static const unsigned int constants[64] = {
		0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
		0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
		0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
		0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
		0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
		0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
		0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
		0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
		0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
		0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
		0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
		0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
		0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
		0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
		0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
		0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
	};
	unsigned int words[64];
	unsigned int a, b, c, d, e, f, g, h;

	for (unsigned int i = 0; i < 16; i++)
		words[i] = ((unsigned int)block[i * 4] << 24) |
			   ((unsigned int)block[i * 4 + 1] << 16) |
			   ((unsigned int)block[i * 4 + 2] << 8) |
			   block[i * 4 + 3];
	for (unsigned int i = 16; i < 64; i++) {
		unsigned int s0 = nxs_rotr(words[i - 15], 7) ^
			nxs_rotr(words[i - 15], 18) ^ (words[i - 15] >> 3);
		unsigned int s1 = nxs_rotr(words[i - 2], 17) ^
			nxs_rotr(words[i - 2], 19) ^ (words[i - 2] >> 10);

		words[i] = words[i - 16] + s0 + words[i - 7] + s1;
	}
	a = ctx->state[0]; b = ctx->state[1]; c = ctx->state[2];
	d = ctx->state[3]; e = ctx->state[4]; f = ctx->state[5];
	g = ctx->state[6]; h = ctx->state[7];
	for (unsigned int i = 0; i < 64; i++) {
		unsigned int s1 = nxs_rotr(e, 6) ^ nxs_rotr(e, 11) ^
			nxs_rotr(e, 25);
		unsigned int choose = (e & f) ^ (~e & g);
		unsigned int t1 = h + s1 + choose + constants[i] + words[i];
		unsigned int s0 = nxs_rotr(a, 2) ^ nxs_rotr(a, 13) ^
			nxs_rotr(a, 22);
		unsigned int majority = (a & b) ^ (a & c) ^ (b & c);
		unsigned int t2 = s0 + majority;

		h = g; g = f; f = e; e = d + t1;
		d = c; c = b; b = a; a = t1 + t2;
	}
	ctx->state[0] += a; ctx->state[1] += b;
	ctx->state[2] += c; ctx->state[3] += d;
	ctx->state[4] += e; ctx->state[5] += f;
	ctx->state[6] += g; ctx->state[7] += h;
}

static void nxs_sha_init(struct nxs_sha256_context *ctx)
{
	static const unsigned int initial[8] = {
		0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
		0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
	};

	memset(ctx, 0, sizeof(*ctx));
	memcpy(ctx->state, initial, sizeof(initial));
}

static void nxs_sha_update(struct nxs_sha256_context *ctx,
			   const void *data, unsigned int length)
{
	const unsigned char *bytes = data;

	ctx->bit_count += (unsigned long long)length * 8ULL;
	while (length != 0) {
		unsigned int take = 64U - ctx->used;

		if (take > length)
			take = length;
		memcpy(ctx->block + ctx->used, bytes, take);
		ctx->used += take;
		bytes += take;
		length -= take;
		if (ctx->used == 64U) {
			nxs_sha_transform(ctx, ctx->block);
			ctx->used = 0;
		}
	}
}

static void nxs_sha_final(struct nxs_sha256_context *ctx,
			  unsigned char digest[AGENT_NEXUS_SHA256_SIZE])
{
	unsigned long long bits = ctx->bit_count;
	unsigned char byte = 0x80U;
	unsigned char encoded[8];

	nxs_sha_update(ctx, &byte, 1);
	byte = 0;
	while (ctx->used != 56U)
		nxs_sha_update(ctx, &byte, 1);
	for (unsigned int i = 0; i < 8; i++)
		encoded[7U - i] = (unsigned char)(bits >> (i * 8U));
	nxs_sha_update(ctx, encoded, sizeof(encoded));
	for (unsigned int i = 0; i < 8; i++) {
		digest[i * 4] = (unsigned char)(ctx->state[i] >> 24);
		digest[i * 4 + 1] = (unsigned char)(ctx->state[i] >> 16);
		digest[i * 4 + 2] = (unsigned char)(ctx->state[i] >> 8);
		digest[i * 4 + 3] = (unsigned char)ctx->state[i];
	}
}

static int nxs_bytes_equal(const void *left, const void *right,
			   unsigned int length)
{
	const unsigned char *a = left;
	const unsigned char *b = right;
	unsigned char difference = 0;

	for (unsigned int i = 0; i < length; i++)
		difference |= a[i] ^ b[i];
	return difference == 0;
}

static unsigned int nxs_get_u32(const unsigned char *bytes)
{
	return (unsigned int)bytes[0] |
	       ((unsigned int)bytes[1] << 8) |
	       ((unsigned int)bytes[2] << 16) |
	       ((unsigned int)bytes[3] << 24);
}

static unsigned long long nxs_get_u64(const unsigned char *bytes)
{
	unsigned long long value = 0;

	for (unsigned int i = 0; i < 8; i++)
		value |= (unsigned long long)bytes[i] << (i * 8U);
	return value;
}

static void nxs_put_u64(unsigned char bytes[8], unsigned long long value)
{
	for (unsigned int i = 0; i < 8; i++)
		bytes[i] = (unsigned char)(value >> (i * 8U));
}

static void nxs_text_copy(char *destination, unsigned int capacity,
			  const char *source)
{
	unsigned int length = 0;

	if (capacity == 0)
		return;
	while (source[length] && length + 1U < capacity) {
		destination[length] = source[length];
		length++;
	}
	destination[length] = 0;
}

static int nxs_reader_open(struct nxs_reader *reader, const char *path)
{
	memset(reader, 0, sizeof(*reader));
	reader->fd = open(path, O_RDONLY);
	if (reader->fd < 0)
		return AGENT_NEXUS_SOURCE_IO_ERROR;
	nxs_sha_init(&reader->digest);
	return AGENT_NEXUS_SOURCE_OK;
}

static int nxs_reader_exact(struct nxs_reader *reader, void *data,
			    unsigned int length)
{
	unsigned char *bytes = data;
	unsigned int received = 0;

	while (received < length) {
		int count = read(reader->fd, bytes + received, length - received);

		if (count == 0)
			return AGENT_NEXUS_SOURCE_CORRUPT;
		if (count < 0)
			return AGENT_NEXUS_SOURCE_IO_ERROR;
		nxs_sha_update(&reader->digest, bytes + received,
			       (unsigned int)count);
		reader->offset += (unsigned int)count;
		received += (unsigned int)count;
	}
	return AGENT_NEXUS_SOURCE_OK;
}

static int nxs_reader_finish(struct nxs_reader *reader,
			     unsigned int expected_size,
			     const unsigned char *expected_digest,
			     unsigned char actual_digest[AGENT_NEXUS_SHA256_SIZE])
{
	unsigned char extra;
	int tail = read(reader->fd, &extra, 1);
	int close_status = close(reader->fd);

	reader->fd = -1;
	if (tail != 0 || close_status < 0 || reader->offset != expected_size)
		return AGENT_NEXUS_SOURCE_CORRUPT;
	nxs_sha_final(&reader->digest, actual_digest);
	if (expected_digest != 0 &&
	    !nxs_bytes_equal(actual_digest, expected_digest,
			     AGENT_NEXUS_SHA256_SIZE))
		return AGENT_NEXUS_SOURCE_CORRUPT;
	return AGENT_NEXUS_SOURCE_OK;
}

static void nxs_reader_abort(struct nxs_reader *reader)
{
	if (reader->fd >= 0)
		close(reader->fd);
	reader->fd = -1;
}

static int nxs_valid_volume_name(const char *name, unsigned int index)
{
	char expected[9] = "nxsrc000";

	expected[5] = (char)('0' + (index / 100U) % 10U);
	expected[6] = (char)('0' + (index / 10U) % 10U);
	expected[7] = (char)('0' + index % 10U);
	return strnlen(name, 16) == 8 && strcmp(name, expected) == 0;
}

static int nxs_zero_bytes(const unsigned char *bytes, unsigned int length)
{
	unsigned char aggregate = 0;

	for (unsigned int i = 0; i < length; i++)
		aggregate |= bytes[i];
	return aggregate == 0;
}

static int nxs_valid_source_id(const char *source_id)
{
	if (source_id == 0 || strnlen(source_id,
				      AGENT_NEXUS_SOURCE_ID_SIZE) != 5 ||
	    source_id[0] != 'S')
		return 0;
	for (unsigned int i = 1; i < 5; i++)
		if (source_id[i] < '0' || source_id[i] > '9')
			return 0;
	return strcmp(source_id, "S0000") != 0;
}

static void nxs_source_id(unsigned int source_number,
			  char source_id[AGENT_NEXUS_SOURCE_ID_SIZE])
{
	source_id[0] = 'S';
	for (unsigned int i = 0; i < 4; i++) {
		unsigned int divisor = i == 0 ? 1000U :
			(i == 1 ? 100U : (i == 2 ? 10U : 1U));
		source_id[i + 1] = (char)('0' + (source_number / divisor) % 10U);
	}
	source_id[5] = 0;
}

static int nxs_valid_path(const char *path, unsigned int length)
{
	int suffix_ok;

	suffix_ok = length >= 2 && path[length - 2] == '.' &&
		(path[length - 1] == 'c' || path[length - 1] == 'h');
	if (length == 0 || length >= AGENT_NEXUS_SOURCE_PATH_SIZE ||
	    path[0] == '/' || path[length - 1] == '/' ||
	    (length >= 2 && path[0] == '.' && path[1] == '.') ||
	    !suffix_ok)
		return 0;
	for (unsigned int i = 0; i < length; i++) {
		unsigned char byte = (unsigned char)path[i];

		if (!((byte >= 'a' && byte <= 'z') ||
		      (byte >= 'A' && byte <= 'Z') ||
		      (byte >= '0' && byte <= '9') || byte == '_' ||
		      byte == '-' || byte == '.' || byte == '/'))
			return 0;
		if (path[i] == '/' &&
		    (i + 1U >= length || path[i + 1U] == '/' ||
		     (path[i + 1U] == '.' &&
		      (i + 2U == length || path[i + 2U] == '/'))))
			return 0;
		if (i + 1U < length && path[i] == '.' && path[i + 1] == '.' &&
		    (i == 0 || path[i - 1] == '/') &&
		    (i + 2U == length || path[i + 2] == '/'))
			return 0;
	}
	return 1;
}

static int nxs_valid_utf8(const unsigned char *bytes, unsigned int length)
{
	unsigned int i = 0;

	while (i < length) {
		unsigned char first = bytes[i++];

		if (first < 0x80U)
			continue;
		if (first >= 0xc2U && first <= 0xdfU) {
			if (i >= length || bytes[i] < 0x80U || bytes[i] > 0xbfU)
				return 0;
			i++;
			continue;
		}
		if (first >= 0xe0U && first <= 0xefU) {
			unsigned char second;

			if (i + 1U >= length)
				return 0;
			second = bytes[i++];
			if ((first == 0xe0U && (second < 0xa0U || second > 0xbfU)) ||
			    (first == 0xedU && (second < 0x80U || second > 0x9fU)) ||
			    (first != 0xe0U && first != 0xedU &&
			     (second < 0x80U || second > 0xbfU)) ||
			    bytes[i] < 0x80U || bytes[i] > 0xbfU)
				return 0;
			i++;
			continue;
		}
		if (first >= 0xf0U && first <= 0xf4U) {
			unsigned char second;

			if (i + 2U >= length)
				return 0;
			second = bytes[i++];
			if ((first == 0xf0U && (second < 0x90U || second > 0xbfU)) ||
			    (first == 0xf4U && (second < 0x80U || second > 0x8fU)) ||
			    (first != 0xf0U && first != 0xf4U &&
			     (second < 0x80U || second > 0xbfU)) ||
			    bytes[i] < 0x80U || bytes[i] > 0xbfU ||
			    bytes[i + 1U] < 0x80U || bytes[i + 1U] > 0xbfU)
				return 0;
			i += 2U;
			continue;
		}
		return 0;
	}
	return 1;
}

static int nxs_path_allowlisted(const char *path)
{
	return strncmp(path, "os/", 3) == 0 ||
	       strncmp(path, "include/", 8) == 0 ||
	       strncmp(path, "user/lib/", 9) == 0 ||
	       strncmp(path, "user/include/", 13) == 0;
}

static int nxs_valid_input(const char *text, unsigned int capacity,
			   int allow_empty, int path_input,
			   unsigned int *length_out)
{
	unsigned int length;

	if (text == 0 || capacity == 0)
		return 0;
	length = strnlen(text, capacity);
	if (length >= capacity || (!allow_empty && length == 0))
		return 0;
	for (unsigned int i = 0; i < length; i++) {
		unsigned char byte = (unsigned char)text[i];

		if (byte < 0x20U || byte == 0x7fU)
			return 0;
	}
	if (!path_input && !nxs_valid_utf8((const unsigned char *)text, length))
		return 0;
	if (path_input && length != 0) {
		if (text[0] == '/' ||
		    (length >= 2 && text[0] == '.' && text[1] == '.'))
			return 0;
		for (unsigned int i = 0; i < length; i++) {
			unsigned char byte = (unsigned char)text[i];

			if (!((byte >= 'a' && byte <= 'z') ||
			      (byte >= 'A' && byte <= 'Z') ||
			      (byte >= '0' && byte <= '9') || byte == '_' ||
			      byte == '-' || byte == '.' || byte == '/'))
				return 0;
			if (i + 1U < length && text[i] == '.' &&
			    text[i + 1] == '.' && (i == 0 || text[i - 1] == '/') &&
			    (i + 2U == length || text[i + 2] == '/'))
				return 0;
		}
	}
	*length_out = length;
	return 1;
}

static unsigned char nxs_ascii_fold(unsigned char byte)
{
	return byte >= 'A' && byte <= 'Z' ? (unsigned char)(byte + 32U) : byte;
}

static int nxs_contains(const char *text, unsigned int text_length,
			const char *query, unsigned int query_length)
{
	if (query_length > text_length)
		return 0;
	for (unsigned int i = 0; i + query_length <= text_length; i++) {
		unsigned int j;

		for (j = 0; j < query_length; j++)
			if (nxs_ascii_fold((unsigned char)text[i + j]) !=
			    nxs_ascii_fold((unsigned char)query[j]))
				break;
		if (j == query_length)
			return 1;
	}
	return 0;
}

static unsigned int nxs_decimal(char *destination, unsigned int capacity,
				unsigned int value)
{
	char reverse[10];
	unsigned int count = 0;

	do {
		reverse[count++] = (char)('0' + value % 10U);
		value /= 10U;
	} while (value != 0 && count < sizeof(reverse));
	if (count > capacity)
		return 0;
	for (unsigned int i = 0; i < count; i++)
		destination[i] = reverse[count - i - 1U];
	return count;
}

static int nxs_citation(char *citation, unsigned int capacity,
			const char *source_id, unsigned int start,
			unsigned int end)
{
	unsigned int used = 0;
	unsigned int amount;

	if (capacity < 12U)
		return 0;
	citation[used++] = '[';
	for (unsigned int i = 0; source_id[i]; i++)
		citation[used++] = source_id[i];
	citation[used++] = ':';
	citation[used++] = 'L';
	amount = nxs_decimal(citation + used, capacity - used - 1U, start);
	if (amount == 0)
		return 0;
	used += amount;
	citation[used++] = '-';
	citation[used++] = 'L';
	amount = nxs_decimal(citation + used, capacity - used - 2U, end);
	if (amount == 0 || used + amount + 2U > capacity)
		return 0;
	used += amount;
	citation[used++] = ']';
	citation[used] = 0;
	return 1;
}

static void nxs_copy_corpus_info(struct agent_nexus_source_info *destination)
{
	memcpy(destination, &nxs_corpus.info, sizeof(*destination));
}

static int nxs_search_emit(struct nxs_scan *scan,
			   struct nxs_record_workspace *workspace,
			   const char *source_id,
			   const char *path, unsigned int line_number,
			   const char *line, unsigned int line_length,
			   int has_newline, const unsigned char full_sha[32])
{
	static const unsigned char newline = '\n';
	struct agent_nexus_source_match *match;

	if (scan->search->match_count >= AGENT_NEXUS_SOURCE_SEARCH_MAX_RESULTS) {
		scan->search->truncated = 1;
		return AGENT_NEXUS_SOURCE_OK;
	}
	match = &scan->search->matches[scan->search->match_count++];
	memset(match, 0, sizeof(*match));
	nxs_text_copy(match->source_id, sizeof(match->source_id), source_id);
	nxs_text_copy(match->path, sizeof(match->path), path);
	match->line = line_number;
	agent_nexus_sha256_hex(full_sha, match->full_sha256);
	nxs_sha_init(&workspace->line_sha);
	nxs_sha_update(&workspace->line_sha, line, line_length);
	if (has_newline)
		nxs_sha_update(&workspace->line_sha, &newline, 1);
	nxs_sha_final(&workspace->line_sha, workspace->line_digest);
	agent_nexus_sha256_hex(workspace->line_digest, match->chunk_sha256);
	if (!nxs_citation(match->citation, sizeof(match->citation), source_id,
			  line_number, line_number))
		return AGENT_NEXUS_SOURCE_CORRUPT;
	memcpy(match->snippet, line, line_length);
	match->snippet[line_length] = 0;
	return AGENT_NEXUS_SOURCE_OK;
}

static int nxs_scan_record(struct nxs_reader *reader, struct nxs_scan *scan,
			   struct nxs_record_workspace *workspace)
{
	unsigned char *header = workspace->header;
	unsigned char *expected_header_sha = workspace->expected_header_sha;
	unsigned char *source_digest = workspace->source_digest;
	struct nxs_sha256_context *source_sha = &workspace->source_sha;
	struct nxs_sha256_context *read_chunk_sha = &workspace->read_chunk_sha;
	char *source_id = workspace->source_id;
	char *expected_id = workspace->expected_id;
	char *path = workspace->path;
	char *line = workspace->line;
	unsigned char *size_bytes = workspace->size_bytes;
	unsigned int path_length;
	unsigned int content_size;
	unsigned int expected_lines;
	unsigned int line_number = 1;
	unsigned int line_length = 0;
	unsigned int actual_lines = 0;
	unsigned int path_match = 0;
	unsigned int target = 0;
	int status;

	status = nxs_reader_exact(reader, header, sizeof(workspace->header));
	if (status < 0)
		return status;
	if (!nxs_bytes_equal(header, "NXSREC01", 8))
		return AGENT_NEXUS_SOURCE_CORRUPT;
	agent_nexus_sha256(header, 64, expected_header_sha);
	if (!nxs_bytes_equal(expected_header_sha, header + 64, 32))
		return AGENT_NEXUS_SOURCE_CORRUPT;
	if (!nxs_zero_bytes(header + 13, 3))
		return AGENT_NEXUS_SOURCE_CORRUPT;
	memcpy(source_id, header + 8, sizeof(workspace->source_id));
	source_id[sizeof(workspace->source_id) - 1U] = 0;
	scan->source_number++;
	nxs_source_id(scan->source_number, expected_id);
	path_length = nxs_get_u32(header + 16);
	content_size = nxs_get_u32(header + 20);
	expected_lines = nxs_get_u32(header + 24);
	if (!nxs_valid_source_id(source_id) || strcmp(source_id, expected_id) != 0 ||
	    nxs_get_u32(header + 28) != 0 || path_length == 0 ||
	    path_length >= sizeof(workspace->path) || content_size == 0 ||
	    content_size > NXS_MAX_SOURCE_BYTES)
		return AGENT_NEXUS_SOURCE_CORRUPT;
	status = nxs_reader_exact(reader, path, path_length);
	if (status < 0)
		return status;
	path[path_length] = 0;
	if (!nxs_valid_path(path, path_length) || !nxs_path_allowlisted(path) ||
	    (scan->source_number > 1 && strcmp(path, scan->previous_path) <= 0))
		return AGENT_NEXUS_SOURCE_CORRUPT;
	nxs_text_copy(scan->previous_path, sizeof(scan->previous_path), path);
	if (scan->mode == NXS_SCAN_SEARCH &&
	    (scan->prefix_length == 0 ||
	     (path_length >= scan->prefix_length &&
	      strncmp(path, scan->prefix, scan->prefix_length) == 0)))
		path_match = nxs_contains(path, path_length, scan->query,
					  scan->query_length);
	if (scan->mode == NXS_SCAN_READ && strcmp(source_id, scan->target_id) == 0) {
		target = 1;
		scan->read_found = 1;
		nxs_text_copy(scan->read->source_id, sizeof(scan->read->source_id),
			      source_id);
		nxs_text_copy(scan->read->path, sizeof(scan->read->path), path);
		scan->read->total_lines = expected_lines;
		agent_nexus_sha256_hex(header + 32, scan->read->full_sha256);
		nxs_sha_init(read_chunk_sha);
	}

	nxs_sha_update(&scan->revision, header + 16, 4);
	nxs_sha_update(&scan->revision, path, path_length);
	nxs_put_u64(size_bytes, content_size);
	nxs_sha_update(&scan->revision, size_bytes,
		       sizeof(workspace->size_bytes));
	nxs_sha_init(source_sha);
	for (unsigned int consumed = 0; consumed < content_size;) {
		unsigned int amount = content_size - consumed;

		if (amount > sizeof(workspace->block))
			amount = sizeof(workspace->block);
		status = nxs_reader_exact(reader, workspace->block, amount);
		if (status < 0)
			return status;
		nxs_sha_update(source_sha, workspace->block, amount);
		nxs_sha_update(&scan->revision, workspace->block, amount);
		for (unsigned int i = 0; i < amount; i++) {
			unsigned char byte = workspace->block[i];
			int selected = target && line_number >= scan->read_start &&
				line_number < scan->read_start + scan->read_lines;

			if (byte == 0 || byte == '\r')
				return AGENT_NEXUS_SOURCE_CORRUPT;
			if (selected) {
				if (scan->read->content_length + 1U >=
				    scan->read_capacity) {
					scan->read_overflow = 1;
				} else {
					scan->read_content[scan->read->content_length++] =
						(char)byte;
					nxs_sha_update(read_chunk_sha, &byte, 1);
				}
			}
			if (byte == '\n') {
				int content_match = scan->mode == NXS_SCAN_SEARCH &&
					(scan->prefix_length == 0 ||
					 strncmp(path, scan->prefix,
						 scan->prefix_length) == 0) &&
					nxs_contains(line, line_length, scan->query,
						     scan->query_length);

				if (!nxs_valid_utf8((const unsigned char *)line,
						    line_length))
					return AGENT_NEXUS_SOURCE_CORRUPT;
				actual_lines++;
				if (scan->mode == NXS_SCAN_SEARCH &&
				    (content_match || (path_match && line_number == 1))) {
					status = nxs_search_emit(scan, workspace,
							 source_id, path,
							 line_number, line, line_length,
							 1, header + 32);
					if (status < 0)
						return status;
				}
				if (selected)
					scan->read->end_line = line_number;
				line_number++;
				line_length = 0;
			} else {
				if (line_length >= NXS_MAX_LINE_BYTES)
					return AGENT_NEXUS_SOURCE_CORRUPT;
				line[line_length++] = (char)byte;
			}
		}
		consumed += amount;
	}
	if (content_size != 0 && line_length != 0) {
		int selected = target && line_number >= scan->read_start &&
			line_number < scan->read_start + scan->read_lines;
		int content_match = scan->mode == NXS_SCAN_SEARCH &&
			(scan->prefix_length == 0 ||
			 strncmp(path, scan->prefix, scan->prefix_length) == 0) &&
			nxs_contains(line, line_length, scan->query,
				     scan->query_length);

		if (!nxs_valid_utf8((const unsigned char *)line, line_length))
			return AGENT_NEXUS_SOURCE_CORRUPT;
		actual_lines++;
		if (scan->mode == NXS_SCAN_SEARCH &&
		    (content_match || (path_match && line_number == 1))) {
			status = nxs_search_emit(scan, workspace, source_id, path,
					 line_number, line, line_length, 0,
					 header + 32);
			if (status < 0)
				return status;
		}
		if (selected)
			scan->read->end_line = line_number;
	}
	nxs_sha_final(source_sha, source_digest);
	if (!nxs_bytes_equal(source_digest, header + 32, 32) ||
	    actual_lines != expected_lines)
		return AGENT_NEXUS_SOURCE_CORRUPT;
	scan->source_bytes += content_size;
	if (target && scan->read->end_line >= scan->read_start &&
	    !scan->read_overflow) {
		scan->read_content[scan->read->content_length] = 0;
		scan->read->start_line = scan->read_start;
		nxs_sha_final(read_chunk_sha, workspace->chunk_digest);
		agent_nexus_sha256_hex(workspace->chunk_digest,
				       scan->read->chunk_sha256);
		if (!nxs_citation(scan->read->citation,
				  sizeof(scan->read->citation), source_id,
				  scan->read_start, scan->read->end_line))
			return AGENT_NEXUS_SOURCE_CORRUPT;
	}
	return AGENT_NEXUS_SOURCE_OK;
}

static int nxs_scan_corpus(struct nxs_scan *scan,
			   struct nxs_workspace *workspace)
{
	unsigned int records = 0;
	struct nxs_reader *reader = &workspace->reader;
	int status;

	nxs_sha_init(&scan->revision);
	nxs_sha_update(&scan->revision, "NXSRCREV1", 9);
	for (unsigned int volume = 0; volume < nxs_corpus.info.volume_count;
	     volume++) {
		unsigned int before = scan->source_number;

		status = nxs_reader_open(reader, nxs_corpus.volumes[volume].name);
		if (status < 0)
			return status;
		while (reader->offset < nxs_corpus.volumes[volume].size) {
			status = nxs_scan_record(reader, scan, &workspace->record);
			if (status < 0) {
				nxs_reader_abort(reader);
				return status;
			}
		}
		status = nxs_reader_finish(reader,
				nxs_corpus.volumes[volume].size,
				nxs_corpus.volumes[volume].sha256,
				workspace->volume_digest);
		if (status < 0)
			return status;
		if (scan->source_number - before !=
		    nxs_corpus.volumes[volume].records)
			return AGENT_NEXUS_SOURCE_CORRUPT;
		records += nxs_corpus.volumes[volume].records;
	}
	nxs_sha_final(&scan->revision, workspace->revision_digest);
	if (records != nxs_corpus.info.source_count ||
	    scan->source_number != nxs_corpus.info.source_count ||
	    scan->source_bytes != nxs_corpus.info.source_bytes)
		return AGENT_NEXUS_SOURCE_CORRUPT;
	agent_nexus_sha256_hex(workspace->revision_digest,
			       workspace->revision_hex);
	if (strcmp(workspace->revision_hex, nxs_corpus.info.revision) != 0)
		return AGENT_NEXUS_SOURCE_CORRUPT;
	return AGENT_NEXUS_SOURCE_OK;
}

int agent_nexus_source_init(void)
{
	static const char scope[] = "build_source_snapshot";
	struct nxs_reader *reader = &nxs_work.reader;
	struct nxs_scan *scan = &nxs_work.scan;
	unsigned char *header = nxs_work.manifest_header;
	unsigned char *descriptor = nxs_work.descriptor;
	unsigned int source_count;
	unsigned int volume_count;
	unsigned long long source_bytes;
	int status;

	if (!nxs_workspace_acquire())
		return AGENT_NEXUS_SOURCE_NOT_READY;
	if (nxs_corpus.ready) {
		nxs_workspace_release();
		return AGENT_NEXUS_SOURCE_OK;
	}
	memset(&nxs_corpus, 0, sizeof(nxs_corpus));
	status = nxs_reader_open(reader, NXS_MANIFEST_NAME);
	if (status < 0)
		goto fail;
	status = nxs_reader_exact(reader, header,
				  sizeof(nxs_work.manifest_header));
	if (status < 0)
		goto fail;
	if (!nxs_bytes_equal(header, "NXSMETA1", 8) ||
	    nxs_get_u32(header + 8) != AGENT_NEXUS_SOURCE_FORMAT_VERSION ||
	    nxs_get_u32(header + 12) != NXS_MANIFEST_HEADER_SIZE ||
	    nxs_get_u32(header + 16) != NXS_DESCRIPTOR_SIZE ||
	    nxs_get_u32(header + 28) != 0 ||
	    !nxs_bytes_equal(header + 104, scope, sizeof(scope)) ||
	    !nxs_zero_bytes(header + 104 + sizeof(scope), 24U - sizeof(scope))) {
		status = AGENT_NEXUS_SOURCE_CORRUPT;
		goto fail;
	}
	source_count = nxs_get_u32(header + 20);
	volume_count = nxs_get_u32(header + 24);
	source_bytes = nxs_get_u64(header + 32);
	if (source_count == 0 || source_count > NXS_MAX_SOURCES ||
	    volume_count == 0 || volume_count > NXS_MAX_VOLUMES ||
	    source_bytes > NXS_MAX_CORPUS_BYTES) {
		status = AGENT_NEXUS_SOURCE_CORRUPT;
		goto fail;
	}
	nxs_sha_init(&nxs_work.descriptor_digest);
	for (unsigned int i = 0; i < volume_count; i++) {
		struct nxs_volume_descriptor *stored = &nxs_corpus.volumes[i];

		status = nxs_reader_exact(reader, descriptor,
					  sizeof(nxs_work.descriptor));
		if (status < 0)
			goto fail;
		nxs_sha_update(&nxs_work.descriptor_digest, descriptor,
			       sizeof(nxs_work.descriptor));
		memcpy(stored->name, descriptor, sizeof(stored->name));
		stored->name[sizeof(stored->name) - 1U] = 0;
		stored->size = nxs_get_u32(descriptor + 16);
		stored->records = nxs_get_u32(descriptor + 20);
		memcpy(stored->sha256, descriptor + 24, sizeof(stored->sha256));
		if (!nxs_valid_volume_name(stored->name, i) ||
		    !nxs_zero_bytes(descriptor + 8, 8) || stored->size == 0 ||
		    stored->size > NXS_MAX_SOURCE_BYTES || stored->records == 0 ||
		    stored->records > source_count) {
			status = AGENT_NEXUS_SOURCE_CORRUPT;
			goto fail;
		}
	}
	nxs_sha_final(&nxs_work.descriptor_digest, nxs_work.descriptors_sha);
	if (!nxs_bytes_equal(nxs_work.descriptors_sha, header + 72, 32)) {
		status = AGENT_NEXUS_SOURCE_CORRUPT;
		goto fail;
	}
	status = nxs_reader_finish(reader,
		NXS_MANIFEST_HEADER_SIZE + volume_count * NXS_DESCRIPTOR_SIZE,
		0, nxs_work.manifest_sha);
	if (status < 0)
		goto fail;
	nxs_corpus.info.format_version = AGENT_NEXUS_SOURCE_FORMAT_VERSION;
	nxs_corpus.info.source_count = source_count;
	nxs_corpus.info.volume_count = volume_count;
	nxs_corpus.info.source_bytes = source_bytes;
	nxs_text_copy(nxs_corpus.info.scope, sizeof(nxs_corpus.info.scope), scope);
	nxs_text_copy(nxs_corpus.info.allowlist,
		      sizeof(nxs_corpus.info.allowlist),
		      AGENT_NEXUS_SOURCE_ANCHOR_ALLOWLIST);
	agent_nexus_sha256_hex(header + 40, nxs_corpus.info.revision);
	agent_nexus_sha256_hex(nxs_work.manifest_sha,
			       nxs_corpus.info.manifest_sha256);
	if (AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_COUNT == 0 ||
	    strcmp(AGENT_NEXUS_SOURCE_ANCHOR_SCOPE, scope) != 0 ||
	    strcmp(AGENT_NEXUS_SOURCE_ANCHOR_ALLOWLIST,
		   AGENT_NEXUS_SOURCE_ALLOWLIST) != 0 ||
	    source_count != AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_COUNT ||
	    volume_count != AGENT_NEXUS_SOURCE_ANCHOR_VOLUME_COUNT ||
	    source_bytes != AGENT_NEXUS_SOURCE_ANCHOR_SOURCE_BYTES ||
	    strcmp(nxs_corpus.info.revision,
		   AGENT_NEXUS_SOURCE_ANCHOR_REVISION) != 0 ||
	    strcmp(nxs_corpus.info.manifest_sha256,
		   AGENT_NEXUS_SOURCE_ANCHOR_MANIFEST_SHA256) != 0) {
		status = AGENT_NEXUS_SOURCE_CORRUPT;
		goto fail;
	}
	memset(scan, 0, sizeof(*scan));
	scan->mode = NXS_SCAN_VERIFY;
	status = nxs_scan_corpus(scan, &nxs_work);
	if (status < 0)
		goto fail;
	nxs_corpus.ready = 1;
	nxs_workspace_release();
	return AGENT_NEXUS_SOURCE_OK;

fail:
	nxs_reader_abort(reader);
	memset(&nxs_corpus, 0, sizeof(nxs_corpus));
	nxs_workspace_release();
	return status;
}

int agent_nexus_source_info(struct agent_nexus_source_info *info)
{
	int status;

	if (info == 0)
		return AGENT_NEXUS_SOURCE_BAD_PARAM;
	status = agent_nexus_source_init();
	if (status < 0)
		return status;
	nxs_copy_corpus_info(info);
	return AGENT_NEXUS_SOURCE_OK;
}

int agent_nexus_source_search(
	const char *query, const char *path_prefix,
	struct agent_nexus_source_search_result *result)
{
	struct nxs_scan *scan = &nxs_work.scan;
	unsigned int query_length;
	unsigned int prefix_length;
	int status;

	if (result == 0 ||
	    !nxs_valid_input(query, AGENT_NEXUS_SOURCE_QUERY_SIZE, 0, 0,
			     &query_length) ||
	    !nxs_valid_input(path_prefix, AGENT_NEXUS_SOURCE_PREFIX_SIZE, 1, 1,
			     &prefix_length))
		return AGENT_NEXUS_SOURCE_BAD_PARAM;
	status = agent_nexus_source_init();
	if (status < 0)
		return status;
	if (!nxs_workspace_acquire())
		return AGENT_NEXUS_SOURCE_NOT_READY;
	memset(result, 0, sizeof(*result));
	nxs_copy_corpus_info(&result->corpus);
	result->content_untrusted = 1;
	memset(scan, 0, sizeof(*scan));
	scan->mode = NXS_SCAN_SEARCH;
	scan->query = query;
	scan->query_length = query_length;
	scan->prefix = path_prefix;
	scan->prefix_length = prefix_length;
	scan->search = result;
	status = nxs_scan_corpus(scan, &nxs_work);
	if (status < 0) {
		nxs_workspace_release();
		return status;
	}
	result->scanned_source_count = scan->source_number;
	status = result->match_count == 0 ? AGENT_NEXUS_SOURCE_NOT_FOUND :
		AGENT_NEXUS_SOURCE_OK;
	nxs_workspace_release();
	return status;
}

int agent_nexus_source_read(
	const char *source_id, unsigned int start_line,
	unsigned int max_lines, char *content, unsigned int content_capacity,
	struct agent_nexus_source_read_result *result)
{
	struct nxs_scan *scan = &nxs_work.scan;
	int status;

	if (result == 0 || content == 0 || content_capacity == 0 ||
	    content_capacity > AGENT_NEXUS_SOURCE_READ_MAX_BYTES + 1U ||
	    !nxs_valid_source_id(source_id) || start_line == 0 ||
	    max_lines == 0 || max_lines > AGENT_NEXUS_SOURCE_READ_MAX_LINES ||
	    start_line > 1000000U || start_line + max_lines < start_line)
		return AGENT_NEXUS_SOURCE_BAD_PARAM;
	status = agent_nexus_source_init();
	if (status < 0)
		return status;
	if (!nxs_workspace_acquire())
		return AGENT_NEXUS_SOURCE_NOT_READY;
	memset(result, 0, sizeof(*result));
	content[0] = 0;
	nxs_copy_corpus_info(&result->corpus);
	result->content_untrusted = 1;
	memset(scan, 0, sizeof(*scan));
	scan->mode = NXS_SCAN_READ;
	scan->target_id = source_id;
	scan->read_start = start_line;
	scan->read_lines = max_lines;
	scan->read_content = content;
	scan->read_capacity = content_capacity;
	scan->read = result;
	status = nxs_scan_corpus(scan, &nxs_work);
	if (status < 0) {
		nxs_workspace_release();
		return status;
	}
	if (scan->read_overflow) {
		memset(result, 0, sizeof(*result));
		content[0] = 0;
		nxs_workspace_release();
		return AGENT_NEXUS_SOURCE_BAD_PARAM;
	}
	if (!scan->read_found || result->end_line < start_line) {
		memset(result, 0, sizeof(*result));
		content[0] = 0;
		nxs_workspace_release();
		return AGENT_NEXUS_SOURCE_NOT_FOUND;
	}
	nxs_workspace_release();
	return AGENT_NEXUS_SOURCE_OK;
}

const char *agent_nexus_source_status_name(int status)
{
	switch (status) {
	case AGENT_NEXUS_SOURCE_OK:
		return "OK";
	case AGENT_NEXUS_SOURCE_BAD_PARAM:
		return "BAD_PARAM";
	case AGENT_NEXUS_SOURCE_NOT_FOUND:
		return "NOT_FOUND";
	case AGENT_NEXUS_SOURCE_IO_ERROR:
		return "IO_ERROR";
	case AGENT_NEXUS_SOURCE_CORRUPT:
		return "CORRUPT";
	case AGENT_NEXUS_SOURCE_NOT_READY:
		return "NOT_READY";
	default:
		return "UNKNOWN";
	}
}
