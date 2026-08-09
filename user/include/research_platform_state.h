#ifndef __RESEARCH_PLATFORM_STATE_H__
#define __RESEARCH_PLATFORM_STATE_H__

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <rp_host_action_seed.h>

#define RP_UNUSED __attribute__((unused))
#ifndef RP_STATE_BUFFER_SIZE
#ifdef RP_ENABLE_HOST_ACTION_SEED
#define RP_STATE_BUFFER_SIZE 32768
#else
#define RP_STATE_BUFFER_SIZE 32768
#endif
#endif
#define RP_HOST_SEED_ARG_MARK "__rp_seed_v1__"
#define RP_HOST_SEED_BUFFER_SIZE 32768

extern char rp_state_buf[RP_STATE_BUFFER_SIZE];
_Static_assert(sizeof(rp_state_buf) == RP_STATE_BUFFER_SIZE,
	       "research platform state scratch size mismatch");
#ifdef RP_ENABLE_HOST_ACTION_SEED
extern char rp_host_seed_buf[RP_HOST_SEED_BUFFER_SIZE];
extern int rp_host_seed_loaded;
_Static_assert(sizeof(rp_host_seed_buf) == RP_HOST_SEED_BUFFER_SIZE,
	       "research platform host seed scratch size mismatch");
#endif
extern int __argc;
extern char **__argv;

static RP_UNUSED int rp_write_file(const char *path, const char *body)
{
	int fd = open(path, O_WRONLY | O_TRUNC);
	if (fd < 0) {
		fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	}
	if (fd < 0) {
		printf("rp_state: open_write_failed path=%s\n", path);
		return 0;
	}
	int len = (int)strlen(body);
	int wrote = 0;
	while (wrote < len) {
		int n = write(fd, body + wrote, len - wrote);
		if (n <= 0)
			break;
		wrote += n;
	}
	close(fd);
	if (wrote != len) {
		printf("rp_state: write_failed path=%s expected=%d actual=%d\n", path, len, wrote);
		return 0;
	}
	return 1;
}

static RP_UNUSED int rp_read_file(const char *path, char *buf, int cap)
{
	if (cap <= 0) return -1;
	int fd = open(path, O_RDONLY);
	if (fd < 0) return -1;
	int total = 0;
	while (total + 1 < cap) {
		int n = read(fd, buf + total, cap - 1 - total);
		if (n < 0) {
			close(fd);
			return -1;
		}
		if (n == 0) break;
		total += n;
	}
	if (total + 1 == cap) {
		char extra;
		int n = read(fd, &extra, 1);
		if (n != 0) {
			close(fd);
			return -1;
		}
	}
	close(fd);
	buf[total] = 0;
	return total;
}

static RP_UNUSED int rp_open_bounded_append(
	const char *path, char *body, int capacity, int *used)
{
	int fd;
	int total = 0;

	if (capacity <= 1 || used == 0)
		return -1;
	fd = open(path, O_RDWR);
	if (fd < 0)
		fd = open(path, O_CREATE | O_RDWR);
	if (fd < 0) {
		printf("rp_state: open_append_failed path=%s\n", path);
		return -1;
	}
	while (total + 1 < capacity) {
		int n = read(fd, body + total, capacity - 1 - total);

		if (n < 0) {
			printf("rp_state: read_append_failed path=%s\n", path);
			close(fd);
			return -1;
		}
		if (n == 0)
			break;
		for (int index = 0; index < n; index++) {
			if (body[total + index] == 0) {
				printf("rp_state: append_binary path=%s\n", path);
				close(fd);
				return -1;
			}
		}
		total += n;
	}
	if (total + 1 == capacity) {
		char extra;
		int n = read(fd, &extra, 1);

		if (n != 0) {
			printf("rp_state: append_full path=%s\n", path);
			close(fd);
			return -1;
		}
	}
	body[total] = 0;
	*used = total;
	return fd;
}

static RP_UNUSED int rp_write_append_suffix(
	int fd, const char *path, const char *suffix, int length)
{
	int wrote = 0;

	while (wrote < length) {
		int n = write(fd, suffix + wrote, length - wrote);

		if (n <= 0)
			break;
		wrote += n;
	}
	if (close(fd) < 0 || wrote != length) {
		printf("rp_state: append_write_failed path=%s expected=%d actual=%d\n",
		       path, length, wrote);
		return 0;
	}
	return 1;
}

static RP_UNUSED int rp_bytes_equal(
	const char *left, const char *right, int length)
{
	for (int index = 0; index < length; index++) {
		if (left[index] != right[index])
			return 0;
	}
	return 1;
}

static RP_UNUSED int rp_file_contains(const char *path, const char *needle)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) {
		printf("rp_state: missing path=%s\n", path);
		return 0;
	}
	int needle_len = (int)strlen(needle);
	int buf_len = (int)strlen(buf);
	if (needle_len > buf_len) {
		printf("rp_state: token_missing path=%s token=%s\n", path, needle);
		return 0;
	}
	for (int i = 0; i <= buf_len - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (buf[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) return 1;
	}
	printf("rp_state: token_missing path=%s token=%s\n", path, needle);
	return 0;
}

static RP_UNUSED int rp_count_lines(const char *path)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return -1;
	int count = 0;
	for (int i = 0; i < n; i++) {
		if (buf[i] == '\n') count++;
	}
	return count;
}

static RP_UNUSED int rp_count_token(const char *path, const char *needle)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return -1;
	int count = 0;
	int needle_len = (int)strlen(needle);
	for (int i = 0; i <= n - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (buf[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) count++;
	}
	return count;
}

static RP_UNUSED int rp_text_contains(const char *text, const char *needle)
{
	int needle_len = (int)strlen(needle);
	int text_len = (int)strlen(text);
	if (needle_len <= 0 || needle_len > text_len) return 0;
	for (int i = 0; i <= text_len - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (text[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) return 1;
	}
	return 0;
}

struct rp_state_buffer {
	char path[64];
	char body[RP_STATE_BUFFER_SIZE];
	int loaded;
	int append_active;
	int append_start;
};

static RP_UNUSED int rp_state_buffer_contains(
	struct rp_state_buffer *state, const char *path, const char *needle)
{
	if (!state->loaded || strcmp(state->path, path) != 0) {
		state->loaded = 0;
		state->append_active = 0;
		state->append_start = 0;
		state->path[0] = 0;
		state->body[0] = 0;
		if (strlen(path) >= sizeof(state->path) ||
		    rp_read_file(path, state->body, sizeof(state->body)) < 0)
			return 0;
		int index = 0;
		while (path[index]) {
			state->path[index] = path[index];
			index++;
		}
		state->path[index] = 0;
		state->loaded = 1;
	}
	return rp_text_contains(state->body, needle);
}

static RP_UNUSED int rp_state_append_line(
	char *body, int capacity, const char *path, const char *line)
{
	int used = (int)strlen(body);
	int add = (int)strlen(line);

	while (add > 0 && line[add - 1] == '\n')
		add--;
	if (add == 0) {
		printf("rp_state: append_empty path=%s\n", path);
		return 0;
	}
	int separator = used > 0 && body[used - 1] != '\n';
	if (used + separator + add + 2 > capacity) {
		printf("rp_state: append_full path=%s\n", path);
		return 0;
	}
	if (separator)
		body[used++] = '\n';
	for (int index = 0; index < add; index++)
		body[used + index] = line[index];
	body[used + add] = '\n';
	body[used + add + 1] = 0;
	return 1;
}

static RP_UNUSED int rp_state_buffer_begin_append(
	struct rp_state_buffer *state, const char *path)
{
	state->loaded = 0;
	state->append_active = 0;
	state->append_start = 0;
	state->path[0] = 0;
	state->body[0] = 0;
	if (strlen(path) >= sizeof(state->path))
		return 0;
	int original = rp_read_file(path, state->body, sizeof(state->body));
	if (original < 0) {
		state->body[0] = 0;
		original = 0;
	}
	int index = 0;
	while (path[index]) {
		state->path[index] = path[index];
		index++;
	}
	state->path[index] = 0;
	state->loaded = 1;
	state->append_active = 1;
	state->append_start = original;
	return 1;
}

static RP_UNUSED int rp_state_buffer_append(
	struct rp_state_buffer *state, const char *line)
{
	if (!state->append_active)
		return 0;
	return rp_state_append_line(state->body, sizeof(state->body),
				    state->path, line);
}

static RP_UNUSED int rp_state_buffer_commit(struct rp_state_buffer *state)
{
	int current;
	int final;
	int fd;

	if (!state->append_active)
		return 0;
	state->append_active = 0;
	final = (int)strlen(state->body);
	if (state->append_start < 0 || state->append_start > final)
		return 0;
	fd = rp_open_bounded_append(
		state->path, rp_state_buf, RP_STATE_BUFFER_SIZE, &current);
	if (fd < 0)
		return 0;
	if (current != state->append_start ||
	    !rp_bytes_equal(rp_state_buf, state->body, current)) {
		printf("rp_state: append_changed path=%s\n", state->path);
		close(fd);
		return 0;
	}
	return rp_write_append_suffix(
		fd, state->path, state->body + current, final - current);
}

static RP_UNUSED __attribute__((noinline)) const char *rp_host_seed_text(void)
{
#ifdef RP_ENABLE_HOST_ACTION_SEED
	if (!rp_host_seed_loaded) {
		int out = 0;
		if (__argc > 2 && __argv && __argv[1] && strcmp(__argv[1], RP_HOST_SEED_ARG_MARK) == 0) {
			for (int arg = 2; arg < __argc && __argv[arg] && out + 1 < (int)sizeof(rp_host_seed_buf); arg++) {
				const char *src = __argv[arg];
				for (int i = 0; src[i] && out + 1 < (int)sizeof(rp_host_seed_buf); i++) {
					rp_host_seed_buf[out++] = src[i];
				}
			}
			rp_host_seed_buf[out] = 0;
		} else {
			int n = rp_read_file("rp_host_action_seed", rp_host_seed_buf, sizeof(rp_host_seed_buf));
			if (n < 0) {
				rp_host_seed_buf[0] = 0;
			}
		}
		const char *bootstrap = RP_HOST_ACTION_BOOTSTRAP_SEED;
		if (bootstrap[0] != 0) {
			int i = 0;
			while (bootstrap[i] && i + 1 < (int)sizeof(rp_host_seed_buf)) {
				rp_host_seed_buf[i] = bootstrap[i];
				i++;
			}
			rp_host_seed_buf[i] = 0;
		}
		if (rp_host_seed_buf[0] == 0) {
			const char *fallback = RP_HOST_ACTION_SEED;
			int i = 0;
			while (fallback[i] && i + 1 < (int)sizeof(rp_host_seed_buf)) {
				rp_host_seed_buf[i] = fallback[i];
				i++;
			}
			rp_host_seed_buf[i] = 0;
		}
		rp_host_seed_loaded = 1;
	}
	return rp_host_seed_buf;
#else
	return RP_HOST_ACTION_SEED;
#endif
}

static RP_UNUSED int rp_host_seed_has(const char *needle)
{
	return rp_text_contains(rp_host_seed_text(), needle);
}

static RP_UNUSED int rp_host_seed_count(void)
{
	const char *text = rp_host_seed_text();
	int n = (int)strlen(text);
	if (n <= 0) return 0;
	int count = 0;
	for (int i = 0; i < n; i++) {
		if (text[i] == '\n') count++;
	}
	if (text[n - 1] != '\n') count++;
	return count;
}

static RP_UNUSED __attribute__((noinline)) int rp_host_seed_copy_value(const char *key, char *out, int cap)
{
	const char *text = rp_host_seed_text();
	int text_len = (int)strlen(text);
	int key_len = (int)strlen(key);
	if (cap <= 0 || key_len <= 0 || key_len > text_len) return 0;
	for (int i = 0; i <= text_len - key_len; i++) {
		int same = 1;
		for (int j = 0; j < key_len; j++) {
			if (text[i + j] != key[j]) {
				same = 0;
				break;
			}
		}
		if (!same) continue;
		int pos = i + key_len;
		int out_pos = 0;
		while (pos < text_len && text[pos] != ';' && text[pos] != '\n' && out_pos + 1 < cap) {
			out[out_pos++] = text[pos++];
		}
		out[out_pos] = 0;
		return out_pos > 0;
	}
	return 0;
}

static RP_UNUSED int rp_slice_contains(const char *text, int start, int end, const char *needle)
{
	int needle_len = (int)strlen(needle);
	if (needle_len <= 0 || start < 0 || end < start || needle_len > end - start) return 0;
	for (int i = start; i <= end - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (text[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_copy_key_from_slice(const char *text, int start, int end, const char *key, char *out, int cap)
{
	int key_len = (int)strlen(key);
	int found = 0;
	if (cap <= 0 || key_len <= 0 || key_len > end - start) return 0;
	for (int i = start; i <= end - key_len; i++) {
		if (i > start && text[i - 1] != ';') continue;
		int same = 1;
		for (int j = 0; j < key_len; j++) {
			if (text[i + j] != key[j]) {
				same = 0;
				break;
			}
		}
		if (!same) continue;
		int pos = i + key_len;
		int out_pos = 0;
		while (pos < end && text[pos] != ';' && text[pos] != '\n' && out_pos + 1 < cap) {
			out[out_pos++] = text[pos++];
		}
		out[out_pos] = 0;
		if (out_pos > 0) found = 1;
	}
	return found;
}

static RP_UNUSED __attribute__((noinline)) int rp_host_seed_copy_value_for_kind(
	const char *kind_token,
	const char *key,
	char *out,
	int cap)
{
	const char *text = rp_host_seed_text();
	int text_len = (int)strlen(text);
	int line_start = 0;
	for (int pos = 0; pos <= text_len; pos++) {
		if (pos != text_len && text[pos] != '\n') continue;
		if (rp_slice_contains(text, line_start, pos, kind_token)) {
			if (rp_copy_key_from_slice(text, line_start, pos, key, out, cap)) {
				return 1;
			}
		}
		line_start = pos + 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_has_workbench_action(void)
{
	return rp_host_seed_has("kind=workbench") ||
	       rp_host_seed_has("kind=workbench_complete") ||
	       rp_host_seed_has("kind=workbench_advance") ||
	       rp_host_seed_has("kind=workbench_auto_advance") ||
	       rp_host_seed_has("kind=workbench_task") ||
	       rp_host_seed_has("kind=workbench_note") ||
	       rp_host_seed_has("kind=workbench_notes") ||
	       rp_host_seed_has("kind=workbench_handoff_package") ||
	       rp_host_seed_has("kind=workbench_readiness") ||
	       rp_host_seed_has("kind=workbench_answer") ||
	       rp_host_seed_has("kind=workbench_answer_audit") ||
	       rp_host_seed_has("kind=workbench_evidence_search") ||
	       rp_host_seed_has("kind=workbench_brief") ||
	       rp_host_seed_has("kind=workbench_evidence_dossier") ||
	       rp_host_seed_has("kind=workbench_evidence_graph") ||
	       rp_host_seed_has("kind=workbench_citations") ||
	       rp_host_seed_has("kind=workbench_manuscript") ||
	       rp_host_seed_has("kind=workbench_manuscript_audit") ||
	       rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	       rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	       rp_host_seed_has("kind=workbench_task_board") ||
	       rp_host_seed_has("kind=workbench_task_board_row") ||
	       rp_host_seed_has("kind=workbench_plan_queue_row") ||
	       rp_host_seed_has("kind=workbench_plan_queue_execute") ||
	       rp_host_seed_has("kind=workbench_runbook") ||
	       rp_host_seed_has("kind=workbench_timeline") ||
	       rp_host_seed_has("kind=workbench_file_manifest") ||
	       rp_host_seed_has("kind=workbench_file_verify") ||
	       rp_host_seed_has("kind=workbench_export") ||
	       rp_host_seed_has("kind=workbench_quality_gate") ||
	       rp_host_seed_has("kind=workbench_quality_repair_plan") ||
	       rp_host_seed_has("kind=workbench_quality_repair_execute") ||
	       rp_host_seed_has("kind=workbench_action_item") ||
	       rp_host_seed_has("kind=workbench_delivery_dashboard") ||
	       rp_host_seed_has("kind=workbench_delivery_execute_next");
}

static RP_UNUSED int rp_host_seed_copy_workbench_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=workbench",
		"kind=workbench_complete",
		"kind=workbench_advance",
		"kind=workbench_auto_advance",
		"kind=workbench_task",
		"kind=workbench_note",
		"kind=workbench_notes",
		"kind=workbench_handoff_package",
		"kind=workbench_readiness",
		"kind=workbench_answer",
		"kind=workbench_answer_audit",
		"kind=workbench_evidence_search",
		"kind=workbench_brief",
		"kind=workbench_evidence_dossier",
		"kind=workbench_evidence_graph",
		"kind=workbench_citations",
		"kind=workbench_manuscript",
		"kind=workbench_manuscript_audit",
		"kind=workbench_manuscript_revision_plan",
		"kind=workbench_manuscript_revision_task",
		"kind=workbench_task_board",
		"kind=workbench_task_board_row",
		"kind=workbench_plan_queue_row",
		"kind=workbench_plan_queue_execute",
		"kind=workbench_runbook",
		"kind=workbench_timeline",
		"kind=workbench_file_manifest",
		"kind=workbench_file_verify",
		"kind=workbench_export",
		"kind=workbench_quality_gate",
		"kind=workbench_quality_repair_plan",
		"kind=workbench_quality_repair_execute",
		"kind=workbench_action_item",
		"kind=workbench_delivery_dashboard",
		"kind=workbench_delivery_execute_next"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_has_research_input_action(void)
{
	return rp_host_seed_has("kind=dataset") ||
	       rp_host_seed_has("kind=library_source") ||
	       rp_host_seed_has("kind=template") ||
	       rp_host_seed_has("kind=workspace_inspect") ||
	       rp_host_seed_has("kind=workspace_import") ||
	       rp_host_seed_has("kind=workspace_import_run");
}

static RP_UNUSED int rp_host_seed_has_dataset_action(void)
{
	return rp_host_seed_has("kind=dataset") ||
	       rp_host_seed_has("kind=dataset_preview") ||
	       rp_host_seed_has("kind=dataset_visualization") ||
	       rp_host_seed_has("kind=dataset_card") ||
	       rp_host_seed_has("kind=dataset_answer") ||
	       rp_host_seed_has("kind=dataset_run") ||
	       rp_host_seed_has("kind=dataset_run_comparison") ||
	       rp_host_seed_has("kind=dataset_portfolio");
}

static RP_UNUSED int rp_host_seed_has_study_protocol_action(void)
{
	return rp_host_seed_has("kind=sample_workbench") ||
	       rp_host_seed_has("kind=study_protocol") ||
	       rp_host_seed_has("kind=study_protocol_run") ||
	       rp_host_seed_has("kind=study_protocol_compliance") ||
	       rp_host_seed_has("kind=study_protocol_bundle") ||
	       rp_host_seed_has("kind=study_protocol_launch") ||
	       rp_host_seed_has("kind=study_protocol_launch_rerun") ||
	       rp_host_seed_has("kind=study_protocol_launch_comparison") ||
	       rp_host_seed_has("kind=study_protocol_reproduction_package") ||
	       rp_host_seed_has("kind=study_protocol_reproduction_package_review") ||
	       rp_host_seed_has("kind=study_protocol_reproduction_package_action_plan") ||
	       rp_host_seed_has("kind=study_protocol_reproduction_package_action_execute");
}

static RP_UNUSED int rp_host_seed_has_evidence_input_action(void)
{
	return rp_host_seed_has("kind=literature_search") ||
	       rp_host_seed_has("kind=evidence_review") ||
	       rp_host_seed_has("kind=evidence_protocol") ||
	       rp_host_seed_has("kind=source_portfolio");
}

static RP_UNUSED int rp_host_seed_has_research_data_action(void)
{
	return rp_host_seed_has_research_input_action() ||
	       rp_host_seed_has_dataset_action() ||
	       rp_host_seed_has_evidence_input_action() ||
	       rp_host_seed_has_study_protocol_action();
}

static RP_UNUSED int rp_host_seed_has_platform_ops_action(void)
{
	return rp_host_seed_has("kind=operations_report") ||
	       rp_host_seed_has("kind=operations_advance_next") ||
	       rp_host_seed_has("kind=operations_execute_next_plan") ||
	       rp_host_seed_has("kind=project_scaffold") ||
	       rp_host_seed_has("kind=project_launch") ||
	       rp_host_seed_has("kind=project_action_execute") ||
	       rp_host_seed_has("kind=project_space") ||
	       rp_host_seed_has("kind=project_space_note") ||
	       rp_host_seed_has("kind=project_space_action_item") ||
	       rp_host_seed_has("kind=project_space_review") ||
	       rp_host_seed_has("kind=project_space_answer") ||
	       rp_host_seed_has("kind=project_space_repair_execute") ||
	       rp_host_seed_has("kind=project_space_task_board_row") ||
	       rp_host_seed_has("kind=project_handoff_audit") ||
	       rp_host_seed_has("kind=project_release_gate") ||
	       rp_host_seed_has("kind=project_snapshot") ||
	       rp_host_seed_has("kind=project_snapshot_comparison") ||
	       rp_host_seed_has("kind=project_reproducibility_audit") ||
	       rp_host_seed_has("kind=project_provenance_graph") ||
	       rp_host_seed_has("kind=project_delivery") ||
	       rp_host_seed_has("kind=package_intake") ||
	       rp_host_seed_has("kind=research_search_save") ||
	       rp_host_seed_has("kind=research_search_export") ||
	       rp_host_seed_has("kind=research_search_note") ||
	       rp_host_seed_has("kind=research_search_action_item") ||
	       rp_host_seed_has("kind=workbench_delivery_dashboard") ||
	       rp_host_seed_has("kind=workbench_delivery_execute_next") ||
	       rp_host_seed_has("kind=workbench_quality_gate") ||
	       rp_host_seed_has("kind=workbench_quality_repair_plan") ||
	       rp_host_seed_has("kind=workbench_quality_repair_execute") ||
	       rp_host_seed_has("kind=workbench_plan_queue_row") ||
	       rp_host_seed_has("kind=workbench_plan_queue_execute") ||
	       rp_host_seed_has("kind=workbench_action_item");
}

static RP_UNUSED int rp_host_seed_copy_platform_ops_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=operations_report",
		"kind=operations_advance_next",
		"kind=operations_execute_next_plan",
		"kind=research_search_save",
		"kind=research_search_export",
		"kind=research_search_note",
		"kind=research_search_action_item",
		"kind=project_space",
		"kind=project_space_note",
		"kind=project_space_action_item",
		"kind=project_space_review",
		"kind=project_space_answer",
		"kind=project_space_repair_execute",
		"kind=project_space_task_board_row",
		"kind=project_handoff_audit",
		"kind=project_release_gate",
		"kind=project_snapshot",
		"kind=project_snapshot_comparison",
		"kind=project_reproducibility_audit",
		"kind=project_provenance_graph",
		"kind=project_delivery",
		"kind=package_intake",
		"kind=project_scaffold",
		"kind=project_launch",
		"kind=project_action_execute",
		"kind=workbench_delivery_dashboard",
		"kind=workbench_delivery_execute_next",
		"kind=workbench_quality_gate",
		"kind=workbench_quality_repair_plan",
		"kind=workbench_quality_repair_execute",
		"kind=workbench_plan_queue_row",
		"kind=workbench_plan_queue_execute",
		"kind=workbench_action_item"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_has_llm_relay_action(void)
{
	return rp_host_seed_has("kind=llm_relay_request") ||
	       rp_host_seed_has("kind=llm_relay_response") ||
	       rp_host_seed_has("kind=llm_relay_fallback");
}

static RP_UNUSED int rp_host_seed_copy_llm_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=llm_relay_request",
		"kind=llm_relay_response",
		"kind=llm_relay_fallback"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_has_host_workflow_step_action(void)
{
	return rp_host_seed_has("kind=host_workflow_stage") ||
	       rp_host_seed_has("kind=host_workflow_cache") ||
	       rp_host_seed_has("kind=host_workflow_retry") ||
	       rp_host_seed_has("kind=host_workflow_artifact") ||
	       rp_host_seed_has("kind=host_workflow_report");
}

static RP_UNUSED int rp_host_seed_has_host_workflow_action(void)
{
	return rp_host_seed_has("kind=host_workflow") ||
	       rp_host_seed_has("kind=host_workflow_export") ||
	       rp_host_seed_has_host_workflow_step_action();
}

static RP_UNUSED int rp_host_seed_copy_host_workflow_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=host_workflow",
		"kind=host_workflow_export",
		"kind=host_workflow_stage",
		"kind=host_workflow_cache",
		"kind=host_workflow_retry",
		"kind=host_workflow_artifact",
		"kind=host_workflow_report"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_has_artifact_action(void)
{
	return rp_host_seed_has("kind=artifact_input") ||
	       rp_host_seed_has("kind=artifact_derive") ||
	       rp_host_seed_has("kind=artifact_log") ||
	       rp_host_seed_has("kind=artifact_chart") ||
	       rp_host_seed_has("kind=artifact_package");
}

static RP_UNUSED int rp_host_seed_copy_artifact_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=artifact_input",
		"kind=artifact_derive",
		"kind=artifact_log",
		"kind=artifact_chart",
		"kind=artifact_package"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_has_workflow_portability_run_action(void)
{
	return rp_host_seed_has("kind=workflow_portability");
}

static RP_UNUSED int rp_host_seed_has_workflow_portability_step_action(void)
{
	return rp_host_seed_has("kind=workflow_portability_import") ||
	       rp_host_seed_has("kind=workflow_portability_plan") ||
	       rp_host_seed_has("kind=workflow_portability_bind") ||
	       rp_host_seed_has("kind=workflow_portability_rehearse") ||
	       rp_host_seed_has("kind=workflow_portability_review") ||
	       rp_host_seed_has("kind=workflow_portability_package");
}

static RP_UNUSED int rp_host_seed_has_workflow_portability_action(void)
{
	return rp_host_seed_has_workflow_portability_run_action() ||
	       rp_host_seed_has_workflow_portability_step_action();
}

static RP_UNUSED int rp_host_seed_copy_workflow_portability_value(const char *key, char *out, int cap)
{
	const char *kinds[] = {
		"kind=workflow_portability",
		"kind=workflow_portability_import",
		"kind=workflow_portability_plan",
		"kind=workflow_portability_bind",
		"kind=workflow_portability_rehearse",
		"kind=workflow_portability_review",
		"kind=workflow_portability_package"
	};
	for (int i = 0; i < (int)(sizeof(kinds) / sizeof(kinds[0])); i++) {
		if (rp_host_seed_copy_value_for_kind(kinds[i], key, out, cap)) return 1;
	}
	return 0;
}

static RP_UNUSED int rp_host_seed_copy_workspace_value(const char *key, char *out, int cap)
{
	if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", key, out, cap)) return 1;
	if (rp_host_seed_copy_value_for_kind("kind=workspace_import", key, out, cap)) return 1;
	if (rp_host_seed_copy_value_for_kind("kind=workspace_import_run", key, out, cap)) return 1;
	return 0;
}

static RP_UNUSED void rp_copy_text(char *dst, int cap, const char *src)
{
	if (cap <= 0) return;
	int i = 0;
	while (src[i] && i + 1 < cap) {
		dst[i] = src[i];
		i++;
	}
	dst[i] = 0;
}

static RP_UNUSED void rp_append_text(char *dst, int cap, const char *src)
{
	int used = (int)strlen(dst);
	int i = 0;
	while (src[i] && used + i + 1 < cap) {
		dst[used + i] = src[i];
		i++;
	}
	dst[used + i] = 0;
}

static RP_UNUSED void rp_append_uint_text(char *dst, int cap, unsigned long long value)
{
	char digits[32];
	int count = 0;
	if (cap <= 0) return;
	if (value == 0) {
		rp_append_text(dst, cap, "0");
		return;
	}
	while (value > 0 && count < (int)sizeof(digits)) {
		digits[count++] = (char)('0' + (value % 10));
		value /= 10;
	}
	while (count > 0) {
		char one[2];
		one[0] = digits[--count];
		one[1] = 0;
		rp_append_text(dst, cap, one);
	}
}

static RP_UNUSED int rp_parse_decimal(const char *s)
{
	int value = 0;
	int found = 0;
	while (*s >= '0' && *s <= '9') {
		found = 1;
		value = value * 10 + (*s - '0');
		s++;
	}
	return found ? value : -1;
}

static RP_UNUSED int rp_get_int_value(const char *path, const char *key)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return -1;
	int key_len = (int)strlen(key);
	for (int i = 0; i <= n - key_len; i++) {
		int same = 1;
		for (int j = 0; j < key_len; j++) {
			if (buf[i + j] != key[j]) {
				same = 0;
				break;
			}
		}
		if (same) {
			return rp_parse_decimal(buf + i + key_len);
		}
	}
	return -1;
}

static RP_UNUSED int rp_append_file(const char *path, const char *line)
{
	char *buf = rp_state_buf;
	int n;
	int fd = rp_open_bounded_append(
		path, buf, RP_STATE_BUFFER_SIZE, &n);

	if (fd < 0)
		return 0;
	if (!rp_state_append_line(buf, RP_STATE_BUFFER_SIZE, path, line)) {
		close(fd);
		return 0;
	}
	return rp_write_append_suffix(
		fd, path, buf + n, (int)strlen(buf) - n);
}

static RP_UNUSED int rp_append_host_action_line(const char *path, const char *prefix, const char *value)
{
	char line[160];
	rp_copy_text(line, sizeof(line), prefix);
	rp_append_text(line, sizeof(line), value);
	return rp_append_file(path, line);
}

static RP_UNUSED int rp_append_status(const char *line)
{
	return rp_append_file("rp_status", line);
}

#endif
