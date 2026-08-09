#ifndef __RP_EVIDENCE_H__
#define __RP_EVIDENCE_H__

#include <research_platform_state.h>
#include "rp_program_manifest.h"

#define RP_EVIDENCE_FNV_OFFSET 1469598103934665603ULL
#define RP_EVIDENCE_FNV_PRIME  1099511628211ULL
#define RP_EVIDENCE_READ_CHUNK_SIZE 128

struct rp_evidence_file_measurement {
	unsigned long long bytes;
	unsigned long long hash;
	int lines;
};

#define RP_EVIDENCE_PROGRAM_MAX 128
#define RP_EVIDENCE_PROGRAM_LINE_MAX 256

struct rp_evidence_program_inventory {
	unsigned long long source_bytes;
	unsigned long long source_hash;
	unsigned long long program_names_digest;
	int programs_observed;
};

static RP_UNUSED unsigned long long
rp_evidence_hash_bytes(unsigned long long hash, const char *buf, int n)
{
	for (int i = 0; i < n; i++) {
		hash ^= (unsigned char)buf[i];
		hash *= RP_EVIDENCE_FNV_PRIME;
	}
	return hash;
}

static RP_UNUSED int
rp_evidence_measure_file(const char *path,
			 struct rp_evidence_file_measurement *out)
{
	char chunk[RP_EVIDENCE_READ_CHUNK_SIZE];
	unsigned long long hash = RP_EVIDENCE_FNV_OFFSET;
	unsigned long long bytes = 0;
	int lines = 0;
	int last = -1;
	int fd;

	if (path == 0 || out == 0)
		return 0;
	fd = open(path, O_RDONLY);
	if (fd < 0)
		return 0;
	for (;;) {
		int n = read(fd, chunk, sizeof(chunk));

		if (n < 0) {
			close(fd);
			return 0;
		}
		if (n == 0)
			break;
		hash = rp_evidence_hash_bytes(hash, chunk, n);
		bytes += (unsigned long long)n;
		for (int i = 0; i < n; i++) {
			if (chunk[i] == '\n')
				lines++;
			last = (unsigned char)chunk[i];
		}
	}
	close(fd);
	if (bytes > 0 && last != '\n')
		lines++;
	out->bytes = bytes;
	out->hash = hash;
	out->lines = lines;
	return 1;
}

static RP_UNUSED int
rp_evidence_measure_file_field(const char *path, const char *key,
			       const char *value,
			       struct rp_evidence_file_measurement *out)
{
	char chunk[RP_EVIDENCE_READ_CHUNK_SIZE];
	unsigned long long hash = RP_EVIDENCE_FNV_OFFSET;
	unsigned long long bytes = 0;
	size_t key_len;
	size_t value_len;
	size_t target_len;
	size_t field_pos = 0;
	int field_matches = 1;
	int matches = 0;
	int lines = 0;
	int last = -1;
	int fd;

	if (path == 0 || key == 0 || value == 0 || out == 0 ||
	    key[0] == 0 || value[0] == 0)
		return 0;
	key_len = strlen(key);
	value_len = strlen(value);
	if (key_len == (size_t)-1 ||
	    value_len > (size_t)-1 - key_len - 1)
		return 0;
	target_len = key_len + value_len + 1;
	fd = open(path, O_RDONLY);
	if (fd < 0)
		return 0;
	for (;;) {
		int n = read(fd, chunk, sizeof(chunk));

		if (n < 0) {
			close(fd);
			return 0;
		}
		if (n == 0)
			break;
		hash = rp_evidence_hash_bytes(hash, chunk, n);
		bytes += (unsigned long long)n;
		for (int i = 0; i < n; i++) {
			int delimiter = chunk[i] == ';' || chunk[i] == '\n';
			char expected = 0;

			if (chunk[i] == '\r' || chunk[i] == 0) {
				close(fd);
				return 0;
			}
			if (!delimiter) {
				if (field_matches && field_pos < target_len) {
					if (field_pos < key_len)
						expected = key[field_pos];
					else if (field_pos == key_len)
						expected = '=';
					else
						expected = value[field_pos - key_len - 1];
					if (chunk[i] != expected)
						field_matches = 0;
				} else {
					field_matches = 0;
				}
				if (field_pos < target_len)
					field_pos++;
			} else {
				if (field_matches && field_pos == target_len &&
				    ++matches > 1) {
					close(fd);
					return 0;
				}
				field_pos = 0;
				field_matches = 1;
				if (chunk[i] == '\n')
					lines++;
			}
			last = (unsigned char)chunk[i];
		}
	}
	close(fd);
	if (field_pos > 0) {
		if (field_matches && field_pos == target_len)
			matches++;
		lines++;
	} else if (bytes > 0 && last != '\n') {
		lines++;
	}
	if (bytes == 0 || matches != 1)
		return 0;
	out->bytes = bytes;
	out->hash = hash;
	out->lines = lines;
	return 1;
}

static RP_UNUSED int
rp_evidence_fold_files(const char **paths, int count,
		       unsigned long long *digest)
{
	unsigned long long hash = RP_EVIDENCE_FNV_OFFSET;

	if (paths == 0 || count < 0 || digest == 0)
		return 0;
	for (int i = 0; i < count; i++) {
		struct rp_evidence_file_measurement measured;
		unsigned long long value;
		char bytes[16];

		if (!rp_evidence_measure_file(paths[i], &measured))
			return 0;
		hash = rp_evidence_hash_bytes(hash, paths[i], strlen(paths[i]));
		value = measured.hash;
		for (int j = 0; j < 8; j++) {
			bytes[j] = (char)(value & 0xff);
			value >>= 8;
		}
		value = measured.bytes;
		for (int j = 0; j < 8; j++) {
			bytes[8 + j] = (char)(value & 0xff);
			value >>= 8;
		}
		hash = rp_evidence_hash_bytes(hash, bytes, sizeof(bytes));
	}
	*digest = hash;
	return 1;
}

static RP_UNUSED int
rp_evidence_field_equal(const char *text, int len, const char *expected)
{
	int expected_len = (int)strlen(expected);

	if (len != expected_len)
		return 0;
	for (int i = 0; i < len; i++)
		if (text[i] != expected[i])
			return 0;
	return 1;
}

static RP_UNUSED int
rp_evidence_valid_program_name(const char *text, int len)
{
	if (len < 4 || text[0] != 'r' || text[1] != 'p' || text[2] != '_')
		return 0;
	for (int i = 3; i < len; i++)
		if (!((text[i] >= 'a' && text[i] <= 'z') ||
		      (text[i] >= '0' && text[i] <= '9') || text[i] == '_'))
			return 0;
	return 1;
}

static RP_UNUSED int
rp_evidence_consume_field(const char *line, int len, int *position,
			  const char *expected_key, const char **value,
			  int *value_len)
{
	int start;
	int equals = -1;
	int end;

	if (line == 0 || position == 0 || expected_key == 0 || value == 0 ||
	    value_len == 0 || *position < 0 || *position >= len)
		return 0;
	start = *position;
	end = start;
	while (end < len && line[end] != ';') {
		if (line[end] == '=') {
			if (equals >= 0)
				return 0;
			equals = end;
		}
		end++;
	}
	if (equals <= start || equals + 1 >= end ||
	    !rp_evidence_field_equal(line + start, equals - start, expected_key))
		return 0;
	for (int i = equals + 1; i < end; i++)
		if (line[i] == ' ' || line[i] == '\t' || line[i] == '\r')
			return 0;
	*value = line + equals + 1;
	*value_len = end - equals - 1;
	*position = end < len ? end + 1 : end;
	return *position <= len && (end == len || *position < len);
}

static RP_UNUSED int
rp_evidence_valid_role(const char *role, int role_len)
{
	static const char *roles[] = {
		"plain", "orchestrator", "recovery", "artifact",
		"investigator", "sentinel",
	};

	for (int i = 0; i < (int)(sizeof(roles) / sizeof(roles[0])); i++)
		if (rp_evidence_field_equal(role, role_len, roles[i]))
			return 1;
	return 0;
}

static RP_UNUSED int rp_evidence_worker_batch_program(const char *program)
{
#define RP_EVIDENCE_BATCH_MATCH(index, candidate) \
	if (strcmp(program, #candidate) == 0) return 1;
	RP_WORKER_BATCH_0_PROGRAMS(RP_EVIDENCE_BATCH_MATCH)
	RP_WORKER_BATCH_1_PROGRAMS(RP_EVIDENCE_BATCH_MATCH)
	RP_WORKER_BATCH_2_PROGRAMS(RP_EVIDENCE_BATCH_MATCH)
#undef RP_EVIDENCE_BATCH_MATCH
	return 0;
}

static RP_UNUSED int rp_evidence_worker_direct_program(const char *program)
{
#define RP_EVIDENCE_DIRECT_MATCH(candidate) \
	if (strcmp(program, #candidate) == 0) return 1;
	RP_WORKER_DIRECT_PROGRAMS(RP_EVIDENCE_DIRECT_MATCH)
#undef RP_EVIDENCE_DIRECT_MATCH
	return 0;
}

static RP_UNUSED const char *rp_evidence_declared_role(const char *program)
{
#define RP_EVIDENCE_ROLE_MATCH(candidate, role) \
	if (strcmp(program, candidate) == 0) return role;
	RP_AGENTOS_ROLE_PROGRAMS(RP_EVIDENCE_ROLE_MATCH)
#undef RP_EVIDENCE_ROLE_MATCH
	return 0;
}

static RP_UNUSED int
rp_evidence_role_number(const char *role, int role_len)
{
	if (rp_evidence_field_equal(role, role_len, "sentinel"))
		return 1;
	if (rp_evidence_field_equal(role, role_len, "investigator"))
		return 2;
	if (rp_evidence_field_equal(role, role_len, "recovery"))
		return 3;
	if (rp_evidence_field_equal(role, role_len, "orchestrator"))
		return 4;
	if (rp_evidence_field_equal(role, role_len, "artifact"))
		return 5;
	return 0;
}

static RP_UNUSED int
rp_evidence_parse_uint(const char *value, int value_len,
			       unsigned long long *parsed)
{
	unsigned long long number = 0;

	if (value == 0 || value_len <= 0)
		return 0;
	for (int i = 0; i < value_len; i++) {
		unsigned long long digit;

		if (value[i] < '0' || value[i] > '9')
			return 0;
		digit = (unsigned long long)(value[i] - '0');
		if (number > (~0ULL - digit) / 10ULL)
			return 0;
		number = number * 10ULL + digit;
	}
	if (parsed)
		*parsed = number;
	return 1;
}

static RP_UNUSED int
rp_evidence_parse_program_record(const char *line, int len,
				 const char *expected_program,
				 int expect_role,
				 const char *expected_agent_launcher)
{
	const char *program;
	const char *role = 0;
	const char *launcher;
	const char *value;
	int program_len;
	int role_len = 0;
	int launcher_len;
	int value_len;
	int pos = 0;
	unsigned long long is_agent = 0;
	unsigned long long agent_role = 0;
	unsigned long long filesystem_domain = 0;
	unsigned long long filesystem_capabilities = 0;

	if (len <= 0 || expected_program == 0 || expected_agent_launcher == 0 ||
	    !rp_evidence_consume_field(line, len, &pos, "program", &program,
					 &program_len) ||
	    !rp_evidence_valid_program_name(program, program_len) ||
	    !rp_evidence_field_equal(program, program_len, expected_program))
		return 0;
	if (expect_role &&
	    (!rp_evidence_consume_field(line, len, &pos, "role", &role,
					  &role_len) ||
	     !rp_evidence_valid_role(role, role_len)))
		return 0;
	if (!rp_evidence_consume_field(line, len, &pos, "launcher", &launcher,
				       &launcher_len))
		return 0;
	if (expect_role) {
		int plain = rp_evidence_field_equal(role, role_len, "plain");
		const char *expected_launcher;
		const char *expected_identity_source;
		const char *declared_role =
			rp_evidence_declared_role(expected_program);

		if (plain && rp_evidence_worker_batch_program(expected_program)) {
			expected_launcher = "agent_worker_batch";
			expected_identity_source = "trusted_crt_batch_dispatch";
		} else if (plain &&
			   rp_evidence_worker_direct_program(expected_program)) {
			expected_launcher = "agent_worker_create";
			expected_identity_source = "trusted_crt_self_check";
		} else if (!plain && declared_role != 0 &&
			   rp_evidence_field_equal(role, role_len,
						   declared_role)) {
			expected_launcher = "agent_create_role";
			expected_identity_source = "trusted_crt_self_check";
		} else {
			return 0;
		}
		if (!rp_evidence_field_equal(launcher, launcher_len,
					     expected_launcher))
			return 0;
		if (!rp_evidence_consume_field(line, len, &pos,
					       "identity_source", &value,
					       &value_len) ||
		    !rp_evidence_field_equal(value, value_len,
					     expected_identity_source) ||
		    !rp_evidence_consume_field(line, len, &pos, "is_agent",
					       &value, &value_len) ||
		    !rp_evidence_parse_uint(value, value_len, &is_agent) ||
		    !rp_evidence_consume_field(line, len, &pos, "agent_role",
					       &value, &value_len) ||
		    !rp_evidence_parse_uint(value, value_len, &agent_role) ||
		    !rp_evidence_consume_field(line, len, &pos,
					       "filesystem_domain", &value,
					       &value_len) ||
		    !rp_evidence_parse_uint(value, value_len,
					    &filesystem_domain) ||
		    !rp_evidence_consume_field(line, len, &pos,
					       "filesystem_capabilities", &value,
					       &value_len) ||
		    !rp_evidence_parse_uint(value, value_len,
					    &filesystem_capabilities) ||
		    filesystem_domain == 0 || filesystem_capabilities == 0 ||
		    (plain && (is_agent != 0 || agent_role != 0)) ||
		    (!plain &&
		     (is_agent != 1 || agent_role !=
				       (unsigned)rp_evidence_role_number(role,
							     role_len))))
			return 0;
	} else if (!rp_evidence_field_equal(launcher, launcher_len,
					     expected_agent_launcher)) {
		return 0;
	}
	if (!rp_evidence_consume_field(line, len, &pos, "ok", &value,
				       &value_len) ||
	    !rp_evidence_field_equal(value, value_len, "1") ||
	    !rp_evidence_consume_field(line, len, &pos, "code", &value,
				       &value_len) ||
	    !rp_evidence_field_equal(value, value_len, "0") ||
	    !rp_evidence_consume_field(line, len, &pos, "elapsed_ms", &value,
				       &value_len) || pos != len)
		return 0;
	return rp_evidence_parse_uint(value, value_len, 0);
}

static RP_UNUSED int
rp_evidence_measure_program_ledger(const char *path,
				   const char *expected_orchestrator,
				   const char *const *expected_programs,
				   int expected_program_count,
				   const char *expected_launcher,
				   int expect_role,
				   struct rp_evidence_program_inventory *out)
{
	unsigned long long source_hash = RP_EVIDENCE_FNV_OFFSET;
	unsigned long long program_digest = RP_EVIDENCE_FNV_OFFSET;
	unsigned long long source_bytes = 0;
	char chunk[128];
	char line[RP_EVIDENCE_PROGRAM_LINE_MAX];
	char orchestrator_header[64];
	char launcher_header[64];
	int line_len = 0;
	int programs = 0;
	int line_number = 0;
	int fd;

	if (path == 0 || expected_orchestrator == 0 || expected_programs == 0 ||
	    expected_launcher == 0 ||
	    out == 0 || expected_program_count <= 0 ||
	    expected_program_count > RP_EVIDENCE_PROGRAM_MAX)
		return 0;
	orchestrator_header[0] = 0;
	rp_append_text(orchestrator_header, sizeof(orchestrator_header),
		       "orchestrator=");
	rp_append_text(orchestrator_header, sizeof(orchestrator_header),
		       expected_orchestrator);
	launcher_header[0] = 0;
	rp_append_text(launcher_header, sizeof(launcher_header), "launcher=");
	rp_append_text(launcher_header, sizeof(launcher_header), expected_launcher);
	for (int i = 0; i < expected_program_count; i++) {
		int name_len;

		if (expected_programs[i] == 0)
			return 0;
		name_len = (int)strlen(expected_programs[i]);
		if (!rp_evidence_valid_program_name(expected_programs[i], name_len))
			return 0;
		for (int j = 0; j < i; j++)
			if (strcmp(expected_programs[i], expected_programs[j]) == 0)
				return 0;
	}
	fd = open(path, O_RDONLY);
	if (fd < 0)
		return 0;
	for (;;) {
		int n = read(fd, chunk, sizeof(chunk));

		if (n < 0) {
			close(fd);
			return 0;
		}
		if (n == 0)
			break;
		source_hash = rp_evidence_hash_bytes(source_hash, chunk, n);
		source_bytes += (unsigned long long)n;
		for (int i = 0; i < n; i++) {
			if (chunk[i] != '\n') {
				if (line_len + 1 >= (int)sizeof(line) || chunk[i] == '\r') {
					close(fd);
					return 0;
				}
				line[line_len++] = chunk[i];
				continue;
			}
			if (line_len == 0) {
				close(fd);
				return 0;
			}
			if (line_number == 0) {
				if (!rp_evidence_field_equal(line, line_len,
							     orchestrator_header)) {
					close(fd);
					return 0;
				}
			} else if (line_number == 1) {
				if (!rp_evidence_field_equal(line, line_len,
							     launcher_header)) {
					close(fd);
					return 0;
				}
			} else {
				char delimiter = 0;
				int expected_index = line_number - 2;
				const char *program;
				int program_len;

				if (expected_index >= expected_program_count ||
				    !rp_evidence_parse_program_record(line, line_len,
						expected_programs[expected_index],
						expect_role,
						expected_launcher)) {
					close(fd);
					return 0;
				}
				program = expected_programs[expected_index];
				program_len = (int)strlen(program);
				programs++;
				program_digest = rp_evidence_hash_bytes(program_digest,
								program, program_len);
				program_digest = rp_evidence_hash_bytes(program_digest,
								&delimiter, 1);
			}
			line_len = 0;
			line_number++;
		}
	}
	close(fd);
	if (line_len != 0 || line_number != expected_program_count + 2 ||
	    programs != expected_program_count)
		return 0;
	out->source_bytes = source_bytes;
	out->source_hash = source_hash;
	out->program_names_digest = program_digest;
	out->programs_observed = programs;
	return 1;
}

static RP_UNUSED int
rp_evidence_copy_value(const char *path, const char *key, char *out, int cap)
{
	char *buf = rp_state_buf;
	int key_len;
	int n;

	if (path == 0 || key == 0 || out == 0 || cap <= 0)
		return 0;
	out[0] = 0;
	n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0)
		return 0;
	key_len = (int)strlen(key);
	for (int i = 0; i + key_len <= n; i++) {
		int same = i == 0 || buf[i - 1] == '\n' || buf[i - 1] == ';';

		for (int j = 0; same && j < key_len; j++)
			if (buf[i + j] != key[j])
				same = 0;
		if (!same)
			continue;
		int pos = i + key_len;
		int copied = 0;
		while (pos < n && buf[pos] != '\n' && buf[pos] != ';' &&
		       copied + 1 < cap)
			out[copied++] = buf[pos++];
		out[copied] = 0;
		return copied > 0;
	}
	return 0;
}

static RP_UNUSED int
rp_evidence_get_u64(const char *path, const char *key,
		    unsigned long long *value)
{
	char text[32];
	unsigned long long parsed = 0;
	int digits = 0;

	if (value == 0 || !rp_evidence_copy_value(path, key, text, sizeof(text)))
		return 0;
	for (int i = 0; text[i]; i++) {
		if (text[i] < '0' || text[i] > '9')
			return 0;
		parsed = parsed * 10ULL + (unsigned long long)(text[i] - '0');
		digits++;
	}
	if (digits == 0)
		return 0;
	*value = parsed;
	return 1;
}

static RP_UNUSED int
rp_evidence_count_prefixed_lines(const char *path, const char *prefix)
{
	char *buf = rp_state_buf;
	int prefix_len;
	int count = 0;
	int n;

	if (path == 0 || prefix == 0)
		return -1;
	n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0)
		return -1;
	prefix_len = (int)strlen(prefix);
	for (int i = 0; i + prefix_len <= n; i++) {
		int same = i == 0 || buf[i - 1] == '\n';

		for (int j = 0; same && j < prefix_len; j++)
			if (buf[i + j] != prefix[j])
				same = 0;
		if (same)
			count++;
		while (i < n && buf[i] != '\n')
			i++;
	}
	return count;
}

static RP_UNUSED void
rp_evidence_append_u64(char *body, int cap, const char *key,
		       unsigned long long value)
{
	rp_append_text(body, cap, key);
	rp_append_uint_text(body, cap, value);
	rp_append_text(body, cap, "\n");
}

static RP_UNUSED void
rp_evidence_append_value(char *body, int cap, const char *key,
			 const char *value)
{
	rp_append_text(body, cap, key);
	rp_append_text(body, cap, value);
	rp_append_text(body, cap, "\n");
}

#endif
