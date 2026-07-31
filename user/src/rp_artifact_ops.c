#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

#define TASK6_ARTIFACT_MAX 256
#define TASK6_SHA256_TEXT 65
#define TASK6_FNV_OFFSET 1469598103934665603ULL
#define TASK6_FNV_PRIME 1099511628211ULL

struct task6_artifact_action {
	char challenge[24];
	char protocol[40];
	char input_name[64];
	char input_kind[32];
	char input_sha256[TASK6_SHA256_TEXT];
	char input_bytes_text[24];
	char input_source[48];
	char input_hex[TASK6_ARTIFACT_MAX * 2 + 1];
	char derive_input[64];
	char output_name[64];
	char operation[48];
	char stage[48];
	char output_sha256[TASK6_SHA256_TEXT];
	char output_bytes_text[24];
	char derive_input_sha256[TASK6_SHA256_TEXT];
};

static struct task6_artifact_action task6_action;
static char task6_input_bytes[TASK6_ARTIFACT_MAX];
static char task6_output_bytes[TASK6_ARTIFACT_MAX];
static char task6_name_a[64];
static char task6_name_b[64];
static char task6_receipt_line[768];

static unsigned long long task6_hash_bytes(const char *buf, int n)
{
	unsigned long long hash = TASK6_FNV_OFFSET;

	for (int i = 0; i < n; i++) {
		hash ^= (unsigned char)buf[i];
		hash *= TASK6_FNV_PRIME;
	}
	return hash;
}

static int task6_hex_digit(char c)
{
	if (c >= '0' && c <= '9') return c - '0';
	if (c >= 'a' && c <= 'f') return c - 'a' + 10;
	return -1;
}

static int task6_parse_uint(const char *text, unsigned long long *value)
{
	unsigned long long result = 0;

	if (!text || !text[0] || !value) return 0;
	for (int i = 0; text[i]; i++) {
		unsigned digit = (unsigned)(text[i] - '0');

		if (digit > 9 || result > (~0ULL - digit) / 10) return 0;
		result = result * 10 + digit;
	}
	*value = result;
	return 1;
}

static int task6_is_sha256(const char *text)
{
	if (!text || strlen(text) != 64) return 0;
	for (int i = 0; i < 64; i++) {
		if (!((text[i] >= '0' && text[i] <= '9') ||
		      (text[i] >= 'a' && text[i] <= 'f')))
			return 0;
	}
	return 1;
}

static int task6_bytes_equal(const char *left, const char *right, int n)
{
	if (!left || !right || n < 0) return 0;
	for (int i = 0; i < n; i++) {
		if (left[i] != right[i]) return 0;
	}
	return 1;
}

static int task6_decode_input(int *byte_count)
{
	int hex_len = strlen(task6_action.input_hex);

	if (!byte_count || (hex_len & 1) != 0 ||
	    hex_len / 2 <= 0 || hex_len / 2 >= TASK6_ARTIFACT_MAX)
		return 0;
	for (int i = 0; i < hex_len / 2; i++) {
		int high = task6_hex_digit(task6_action.input_hex[i * 2]);
		int low = task6_hex_digit(task6_action.input_hex[i * 2 + 1]);

		if (high < 0 || low < 0) return 0;
		task6_input_bytes[i] = (char)((high << 4) | low);
		if (task6_input_bytes[i] == 0) return 0;
	}
	*byte_count = hex_len / 2;
	task6_input_bytes[*byte_count] = 0;
	return 1;
}

static int task6_parse_row(const char *buf, int n, int *offset,
			   char *name, int name_cap,
			   unsigned long long *count)
{
	int out = 0;
	unsigned long long value = 0;

	if (!buf || !offset || !name || name_cap < 2 || !count) return 0;
	while (*offset < n && buf[*offset] != ',') {
		char c = buf[(*offset)++];

		if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
		      (c >= '0' && c <= '9') || c == '-' || c == '_' ||
		      c == '.' || c == ':') || out + 1 >= name_cap)
			return 0;
		name[out++] = c;
	}
	if (out == 0 || *offset >= n || buf[(*offset)++] != ',') return 0;
	name[out] = 0;
	if (*offset >= n || buf[*offset] < '0' || buf[*offset] > '9') return 0;
	while (*offset < n && buf[*offset] != '\n') {
		unsigned digit = (unsigned)(buf[(*offset)++] - '0');

		if (digit > 9 || value > (~0ULL - digit) / 10) return 0;
		value = value * 10 + digit;
	}
	if (*offset >= n || buf[(*offset)++] != '\n' || value == 0) return 0;
	*count = value;
	return 1;
}

static int task6_normalize_counts(int input_bytes)
{
	static const char header[] = "sample,count\n";
	unsigned long long count_a;
	unsigned long long count_b;
	unsigned long long total;
	unsigned long long ppm_a;
	int offset = sizeof(header) - 1;

	if (input_bytes <= offset ||
	    !task6_bytes_equal(task6_input_bytes, header, sizeof(header) - 1) ||
	    !task6_parse_row(task6_input_bytes, input_bytes, &offset,
			     task6_name_a, sizeof(task6_name_a), &count_a) ||
	    !task6_parse_row(task6_input_bytes, input_bytes, &offset,
			     task6_name_b, sizeof(task6_name_b), &count_b) ||
	    offset != input_bytes || count_a > ~0ULL - count_b)
		return -1;
	total = count_a + count_b;
	if (count_a > ~0ULL / 1000000ULL) return -1;
	ppm_a = count_a * 1000000ULL / total;
	rp_copy_text(task6_output_bytes, sizeof(task6_output_bytes),
		     "sample,normalized_ppm\n");
	rp_append_text(task6_output_bytes, sizeof(task6_output_bytes), task6_name_a);
	rp_append_text(task6_output_bytes, sizeof(task6_output_bytes), ",");
	rp_append_uint_text(task6_output_bytes, sizeof(task6_output_bytes), ppm_a);
	rp_append_text(task6_output_bytes, sizeof(task6_output_bytes), "\n");
	rp_append_text(task6_output_bytes, sizeof(task6_output_bytes), task6_name_b);
	rp_append_text(task6_output_bytes, sizeof(task6_output_bytes), ",");
	rp_append_uint_text(task6_output_bytes, sizeof(task6_output_bytes),
			    1000000ULL - ppm_a);
	rp_append_text(task6_output_bytes, sizeof(task6_output_bytes), "\n");
	return strlen(task6_output_bytes);
}

static int fastq_profile(int *reads, int *bases, int *diffs)
{
	char *buf = rp_state_buf;
	int n = rp_read_file("rp_input_fastq", buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return 0;
	char seq1[64];
	char seq2[64];
	int len1 = 0;
	int len2 = 0;
	int line = 0;
	int col = 0;
	*reads = 0;
	*bases = 0;
	*diffs = 0;
	for (int i = 0; i < n; i++) {
		char c = buf[i];
		if (c == '\n') {
			if (line % 4 == 1) {
				(*reads)++;
			}
			line++;
			col = 0;
			continue;
		}
		if (line % 4 == 0 && col == 0 && c != '@') {
			return 0;
		}
		if (line % 4 == 1) {
			int read_index = line / 4;
			if (read_index == 0 && len1 < (int)sizeof(seq1) - 1) {
				seq1[len1++] = c;
			} else if (read_index == 1 && len2 < (int)sizeof(seq2) - 1) {
				seq2[len2++] = c;
			}
			(*bases)++;
		}
		col++;
	}
	seq1[len1] = 0;
	seq2[len2] = 0;
	if (len1 <= 0 || len1 != len2) return 0;
	for (int i = 0; i < len1; i++) {
		if (seq1[i] != seq2[i]) {
			(*diffs)++;
		}
	}
	return 1;
}

static int apply_task6_artifact_actions(void)
{
	char line[320];
	unsigned long long declared_input_bytes;
	unsigned long long declared_output_bytes;
	unsigned long long input_fnv64;
	unsigned long long output_fnv64;
	int input_bytes;
	int output_bytes;

	memset(&task6_action, 0, sizeof(task6_action));
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "challenge=",
			task6_action.challenge, sizeof(task6_action.challenge)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input",
			"provenance_protocol=", task6_action.protocol,
			sizeof(task6_action.protocol)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input", "file=",
			task6_action.input_name, sizeof(task6_action.input_name)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input", "artifact_kind=",
			task6_action.input_kind, sizeof(task6_action.input_kind)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input", "sha256=",
			task6_action.input_sha256, sizeof(task6_action.input_sha256)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input", "bytes=",
			task6_action.input_bytes_text,
			sizeof(task6_action.input_bytes_text)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input", "source=",
			task6_action.input_source, sizeof(task6_action.input_source)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_input", "content_hex=",
			task6_action.input_hex, sizeof(task6_action.input_hex)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "input=",
			task6_action.derive_input, sizeof(task6_action.derive_input)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "output=",
			task6_action.output_name, sizeof(task6_action.output_name)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "operation=",
			task6_action.operation, sizeof(task6_action.operation)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "stage=",
			task6_action.stage, sizeof(task6_action.stage)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "sha256=",
			task6_action.output_sha256, sizeof(task6_action.output_sha256)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "bytes=",
			task6_action.output_bytes_text,
			sizeof(task6_action.output_bytes_text)) ||
	    !rp_host_seed_copy_value_for_kind("kind=artifact_derive", "input_sha256=",
			task6_action.derive_input_sha256,
			sizeof(task6_action.derive_input_sha256)))
		return 0;
	if (strcmp(task6_action.protocol, "task6_artifact_bytes_v1") != 0 ||
	    strcmp(task6_action.input_name, task6_action.derive_input) != 0 ||
	    strcmp(task6_action.operation, "normalize_ppm") != 0 ||
	    strcmp(task6_action.input_sha256,
		   task6_action.derive_input_sha256) != 0 ||
	    !task6_is_sha256(task6_action.input_sha256) ||
	    !task6_is_sha256(task6_action.output_sha256) ||
	    strcmp(task6_action.input_sha256, task6_action.output_sha256) == 0 ||
	    !task6_parse_uint(task6_action.input_bytes_text,
			     &declared_input_bytes) ||
	    !task6_parse_uint(task6_action.output_bytes_text,
			     &declared_output_bytes) ||
	    !task6_decode_input(&input_bytes) ||
	    declared_input_bytes != (unsigned long long)input_bytes)
		return 0;
	output_bytes = task6_normalize_counts(input_bytes);
	if (output_bytes <= 0 ||
	    declared_output_bytes != (unsigned long long)output_bytes)
		return 0;
	if (!rp_write_file("rp_task6_raw", task6_input_bytes) ||
	    !rp_write_file("rp_task6_norm", task6_output_bytes))
		return 0;
	input_fnv64 = task6_hash_bytes(task6_input_bytes, input_bytes);
	output_fnv64 = task6_hash_bytes(task6_output_bytes, output_bytes);

	rp_copy_text(line, sizeof(line), "host_artifact_input=");
	rp_append_text(line, sizeof(line), task6_action.input_name);
	rp_append_text(line, sizeof(line), ";kind=");
	rp_append_text(line, sizeof(line), task6_action.input_kind);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), task6_action.input_sha256);
	rp_append_text(line, sizeof(line), ";bytes=");
	rp_append_uint_text(line, sizeof(line), input_bytes);
	rp_append_text(line, sizeof(line), ";source=");
	rp_append_text(line, sizeof(line), task6_action.input_source);
	if (!rp_append_file("rp_artifact", line) ||
	    !rp_append_file("rp_input", line))
		return 0;

	rp_copy_text(line, sizeof(line), "host_artifact_derive=");
	rp_append_text(line, sizeof(line), task6_action.derive_input);
	rp_append_text(line, sizeof(line), ";output=");
	rp_append_text(line, sizeof(line), task6_action.output_name);
	rp_append_text(line, sizeof(line), ";operation=");
	rp_append_text(line, sizeof(line), task6_action.operation);
	rp_append_text(line, sizeof(line), ";stage=");
	rp_append_text(line, sizeof(line), task6_action.stage);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), task6_action.output_sha256);
	if (!rp_append_file("rp_artifact", line)) return 0;

	rp_copy_text(task6_receipt_line, sizeof(task6_receipt_line),
		     "task6_artifact_receipt=task6_artifact_bytes_v1;challenge=");
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       task6_action.challenge);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";input_storage=rp_task6_raw;input_bytes=");
	rp_append_uint_text(task6_receipt_line, sizeof(task6_receipt_line), input_bytes);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";input_fnv64=");
	rp_append_uint_text(task6_receipt_line, sizeof(task6_receipt_line), input_fnv64);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";input_sha256=");
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       task6_action.input_sha256);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";output_storage=rp_task6_norm;output_bytes=");
	rp_append_uint_text(task6_receipt_line, sizeof(task6_receipt_line), output_bytes);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";output_fnv64=");
	rp_append_uint_text(task6_receipt_line, sizeof(task6_receipt_line), output_fnv64);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";output_sha256=");
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       task6_action.output_sha256);
	rp_append_text(task6_receipt_line, sizeof(task6_receipt_line),
		       ";operation=normalize_ppm");
	return rp_append_file("rp_artifact", task6_receipt_line);
}

static int append_legacy_artifact_input_action(void)
{
	char input[64];
	char kind[32];
	char input_sha[65];
	char bytes[32];
	char source[48];
	char line[320];

	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "file=",
			input, sizeof(input)))
		rp_copy_text(input, sizeof(input), "reads_R1.fastq");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "artifact_kind=",
			kind, sizeof(kind)))
		rp_copy_text(kind, sizeof(kind), "fastq");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "sha256=",
			input_sha, sizeof(input_sha)))
		rp_copy_text(input_sha, sizeof(input_sha), "sha-host-input");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "bytes=",
			bytes, sizeof(bytes)))
		rp_copy_text(bytes, sizeof(bytes), "2048");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "source=",
			source, sizeof(source)))
		rp_copy_text(source, sizeof(source), "upload");
	rp_copy_text(line, sizeof(line), "host_artifact_input=");
	rp_append_text(line, sizeof(line), input);
	rp_append_text(line, sizeof(line), ";kind=");
	rp_append_text(line, sizeof(line), kind);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), input_sha);
	rp_append_text(line, sizeof(line), ";bytes=");
	rp_append_text(line, sizeof(line), bytes);
	rp_append_text(line, sizeof(line), ";source=");
	rp_append_text(line, sizeof(line), source);
	return rp_append_file("rp_artifact", line) &&
	       rp_append_file("rp_input", line);
}

static int append_legacy_artifact_derive_action(void)
{
	char input[64];
	char output[64];
	char output_sha[65];
	char operation[48];
	char stage[48];
	char line[320];

	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "input=",
			input, sizeof(input)))
		rp_copy_text(input, sizeof(input), "reads_R1.fastq");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "output=",
			output, sizeof(output)))
		rp_copy_text(output, sizeof(output), "clean_reads.fastq");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "operation=",
			operation, sizeof(operation)))
		rp_copy_text(operation, sizeof(operation), "trim");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "stage=",
			stage, sizeof(stage)))
		rp_copy_text(stage, sizeof(stage), "clean");
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "sha256=",
			output_sha, sizeof(output_sha)))
		rp_copy_text(output_sha, sizeof(output_sha), "sha-host-derived");
	rp_copy_text(line, sizeof(line), "host_artifact_derive=");
	rp_append_text(line, sizeof(line), input);
	rp_append_text(line, sizeof(line), ";output=");
	rp_append_text(line, sizeof(line), output);
	rp_append_text(line, sizeof(line), ";operation=");
	rp_append_text(line, sizeof(line), operation);
	rp_append_text(line, sizeof(line), ";stage=");
	rp_append_text(line, sizeof(line), stage);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), output_sha);
	return rp_append_file("rp_artifact", line);
}

static int append_artifact_log_action(void)
{
	char log[64];
	char stage[48];
	char level[32];
	char message[80];
	char line[240];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "log=", log, sizeof(log))) {
		rp_copy_text(log, sizeof(log), "clean.log");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "stage=", stage, sizeof(stage))) {
		rp_copy_text(stage, sizeof(stage), "clean");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "level=", level, sizeof(level))) {
		rp_copy_text(level, sizeof(level), "warn");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "message=", message, sizeof(message))) {
		rp_copy_text(message, sizeof(message), "adapter_trimmed");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_log=");
	rp_append_text(line, sizeof(line), log);
	rp_append_text(line, sizeof(line), ";stage=");
	rp_append_text(line, sizeof(line), stage);
	rp_append_text(line, sizeof(line), ";level=");
	rp_append_text(line, sizeof(line), level);
	rp_append_text(line, sizeof(line), ";message=");
	rp_append_text(line, sizeof(line), message);
	return rp_append_file("rp_stage_log", line);
}

static int append_artifact_chart_action(void)
{
	char chart[64];
	char chart_type[32];
	char data_file[64];
	char points[32];
	char line[220];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "chart=", chart, sizeof(chart))) {
		rp_copy_text(chart, sizeof(chart), "qc-chart.json");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "chart_type=", chart_type, sizeof(chart_type))) {
		rp_copy_text(chart_type, sizeof(chart_type), "line");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "data_file=", data_file, sizeof(data_file))) {
		rp_copy_text(data_file, sizeof(data_file), "clean.metrics.json");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "points=", points, sizeof(points))) {
		rp_copy_text(points, sizeof(points), "12");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_chart=");
	rp_append_text(line, sizeof(line), chart);
	rp_append_text(line, sizeof(line), ";type=");
	rp_append_text(line, sizeof(line), chart_type);
	rp_append_text(line, sizeof(line), ";data_file=");
	rp_append_text(line, sizeof(line), data_file);
	rp_append_text(line, sizeof(line), ";points=");
	rp_append_text(line, sizeof(line), points);
	return rp_append_file("rp_chart_data", line);
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_taskrec", "stage=align");
	ok = ok && rp_file_contains("rp_data", "failed_stage=align");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_retrylog", "final_result=recovered");
	ok = ok && rp_file_contains("rp_completion", "status=ready");
	if (!ok) return 1;
	if (!rp_write_file("rp_input",
			   "input=RUN-042:sample-fastq\n"
			   "source=ordinary_ucore_file\n"
			   "records=2\n"
			   "bytes=96\n"
			   "checksum=input-demo-042\n"
			   "custom_requests=3\n"
			   "custom_run=usable-run:RUN-900\n"
			   "custom_run_2=usable-run:RUN-901\n"
			   "custom_run_3=usable-run:RUN-902\n"
			   "custom_title=Browser started study\n"
			   "custom_question=Can this platform run a custom research task?\n"
			   "custom_provider=template\n"
			   "custom_dataset_rows=3\n"
			   "custom_dataset_rows_total=9\n"
			   "custom_row=S1,control,12\n"
			   "custom_row=S2,treatment,19\n"
			   "custom_row=S3,treatment,21\n"
			   "custom_row_2=S4,control,8\n"
			   "custom_row_2=S5,treatment,13\n"
			   "custom_row_3=S6,control,30\n"
			   "custom_row_3=S7,treatment,28\n"
			   "custom_outputs=stage_dag,analysis,report,review,export\n"
			   "request_form=form_fields=8;request_count=3;source_mode=pasted_or_uploaded;provider_options=template,host-relay;delivery_audience=reviewer;reviewer=Wang\n"
			   "upload_files=uploads=2;csv_rows_total=9;reference_entries=2;dataset_target=rp_input\n"
			   "library_sources=1;library_tag=reusable;library_source_id=usable-source:library2026:1;citation_key=library2026\n"
			   "library_backed_run=usable-run:RUN-900;source_tag=reusable;selected_library_sources=1\n"
			   "workspace_import=workspace:RUN-900:folder;files=4;csv=1;refs=2;notes=1;manifest=workspace-manifest.json\n"
			   "workspace_file=expr.csv;kind=dataset;rows=3;target=usable-dataset:workspace-900:expr\n"
			   "workspace_file=refs.bib;kind=references;entries=2;target=usable-source:workspace-900:refs\n"
			   "workspace_file=notes.md;kind=notes;target=usable-template:workspace-900\n"
			   "workspace_template=usable-template:workspace-900;status=ready\n"
			   "workspace_run=usable-run:RUN-903;template=usable-template:workspace-900;status=ready\n"
			   "dynamic_submissions=4\n"
			   "dynamic_submission=1;source=form;run=RUN-900;state=accepted;rows=3\n"
			   "dynamic_submission=2;source=upload;run=RUN-901;state=accepted;rows=2\n"
			   "dynamic_submission=3;source=workspace;run=RUN-903;state=accepted;files=4\n"
			   "dynamic_submission=4;source=api;run=RUN-904;state=queued;rows=4\n"
			   "dynamic_validation=passed;dedupe=passed;schema=sample,group,value\n"
			   "dynamic_queue=plain_ucore_file_backed;accepted=3;pending=1\n"
			   "host_ui_feed=rp_web_bundle;events=10;source=rp_input\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_run_id=", seed_run)) return 1;
		if (!rp_append_host_action_line("rp_input", "host_action_research_run=usable-run:", seed_run)) return 1;
		if (!rp_append_file("rp_input", "host_action_source=rp_host_action_seed")) return 1;
		if (!rp_append_file("rp_input", "host_action_state=accepted")) return 1;
		if (!rp_append_file("rp_input", "host_action_dataset_rows=4")) return 1;
		if (!rp_append_file("rp_input", "host_action_validation=passed")) return 1;
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Browser started study");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "question=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Can this platform run a custom research task?");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_question=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "provider=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_provider=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_rows_value=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_entries=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "2");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_reference_entries=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "workspace_files=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_workspace_files=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "csv_file=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "expr.csv");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_csv_file=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_file=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "refs.bib");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_reference_file=", value)) return 1;
	}
	if (rp_host_seed_has("kind=research_rerun")) {
		char rerun_id[48];
		char parent_run[48];
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=research_rerun", "run_id=", rerun_id, sizeof(rerun_id))) {
			rp_copy_text(rerun_id, sizeof(rerun_id), "RUN-905-rerun");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=research_rerun", "parent_run=", parent_run, sizeof(parent_run)) &&
		    !rp_host_seed_copy_value_for_kind("kind=research_rerun", "source_run=", parent_run, sizeof(parent_run))) {
			rp_copy_text(parent_run, sizeof(parent_run), "RUN-900");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_rerun_id=", rerun_id)) return 1;
		if (!rp_append_host_action_line("rp_input", "host_action_research_rerun=usable-run:", rerun_id)) return 1;
		if (!rp_append_host_action_line("rp_input", "host_action_rerun_parent=", parent_run)) return 1;
		if (!rp_append_file("rp_input", "host_action_source=rp_host_action_seed")) return 1;
		if (!rp_append_file("rp_input", "host_action_state=accepted")) return 1;
		if (!rp_append_file("rp_input", "host_action_validation=passed")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_rerun", "provider=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_rerun_provider=", value)) return 1;
		if (rp_host_seed_copy_value_for_kind("kind=research_rerun", "question=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_rerun_question=", value)) return 1;
		}
	}
	if (rp_host_seed_has("kind=dataset")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_dataset=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=dataset", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Reusable response table");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=dataset", "dataset_rows=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "3");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_rows=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=dataset", "columns=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "sample,group,value");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_columns=", value)) return 1;
	}
	if (rp_host_seed_has("kind=library_source")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_library_source=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=library_source", "citation_key=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agentlibrary2026");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_library_citation=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=library_source", "tags=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agent reusable");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_library_tags=", value)) return 1;
	}
	if (rp_host_seed_has("kind=template")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_template=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=template", "name=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Reusable response comparison");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_template_name=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=template", "question=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Which group is stronger?");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_template_question=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=template", "provider_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_template_provider=", value)) return 1;
	}
	if (rp_host_seed_has("kind=workspace_inspect") ||
	    rp_host_seed_has("kind=workspace_import") ||
	    rp_host_seed_has("kind=workspace_import_run")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_workspace=observed")) return 1;
		if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", "root=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import", "root=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "root=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_root=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", "max_files=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import", "max_files=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "max_files=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_max_files=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "manifest=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "manifest=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_manifest=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "title=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "title=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_title=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "question=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "question=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_question=", value)) return 1;
		}
	}
	if (!rp_write_file("rp_input_fastq",
			   "@RUN-042-read-1\n"
			   "ACGTACGTACGT\n"
			   "+\n"
			   "FFFFFFFFFFFF\n"
			   "@RUN-042-read-2\n"
			   "ACGTTCGTACGA\n"
			   "+\n"
			   "FFFFFFFFFFFF\n")) {
		return 1;
	}
	if (!rp_file_contains("rp_input_fastq", "@RUN-042-read-1")) return 1;
	int reads = 0;
	int bases = 0;
	int diffs = 0;
	if (!fastq_profile(&reads, &bases, &diffs)) return 1;
	if (reads != 2 || bases != 24 || diffs != 2) return 1;
	if (!rp_write_file("rp_stage_dag",
			   "dag=lab-gene-x-nightly\n"
			   "stage=ingest;deps=none;cache=miss;status=done\n"
			   "stage=align;deps=ingest;cache=miss;status=recovered\n"
			   "stage=profile;deps=align;cache=hit;status=done\n"
			   "stage=review;deps=profile;cache=miss;status=done\n"
			   "stage=package;deps=review;cache=miss;status=ready\n"
			   "edges=4\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_stage_log",
			   "run_id=RUN-042\n"
			   "log=ingest read rp_input_fastq records=2 status=ok\n"
			   "log=align first_attempt status=failed reason=tool_output_missing\n"
			   "log=align retry attempt=2 status=recovered artifact=rp_artifact\n"
			   "log=profile cache=hit source=rp_compute status=ok\n"
			   "log=review claims=8 evidence_links=5 status=accepted\n"
			   "lines=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_artifact",
			   "artifact=artifact:RUN-042:align-recovered\n"
			   "input=rp_input_fastq\n"
			   "derived_sections=5\n"
			   "section=rp_normalized_fastq;reads=2;bases=24;status=ready\n"
			   "normalized_read=RUN-042-read-1;sequence=ACGTACGTACGT\n"
			   "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA\n"
			   "section=rp_align_table;reference=RUN-042-read-1;variant_count=2;status=ready\n"
			   "align_row=RUN-042-read-1;diffs=0;status=reference\n"
			   "align_row=RUN-042-read-2;diffs=2;status=variant\n"
			   "section=rp_metrics_json;\"reads\":2;\"bases\":24;\"variants\":2;status=ready\n"
			   "section=rp_gene_counts_csv;geneA=18;geneB=11;geneC=7;status=ready\n"
			   "section=rp_archive_manifest;files=5;status=ready\n"
			   "archive_file=rp_normalized_fastq;kind=prepared_input;status=ready\n"
			   "archive_file=rp_align_table;kind=alignment;status=ready\n"
			   "archive_file=rp_metrics_json;kind=metrics;status=ready\n"
			   "archive_file=rp_gene_counts_csv;kind=counts;status=ready\n"
			   "archive_file=rp_report_text;kind=report;status=ready\n"
			   "stage=align\n"
			   "attempt=2\n"
			   "records=2\n"
			   "derived_variants=2\n"
			   "normalized_fastq=section:rp_normalized_fastq\n"
			   "align_table=section:rp_align_table\n"
			   "metrics=section:rp_metrics_json\n"
			   "counts=section:rp_gene_counts_csv\n"
			   "archive_manifest=section:rp_archive_manifest\n"
			   "artifact_dossier=rp_input_fastq,rp_normalized_fastq,rp_align_table,rp_metrics_json,rp_gene_counts_csv,rp_chart_data,rp_stage_log\n"
			   "artifact_review_link=rp_artifact_manifest->rp_review_pack->rp_package\n"
			   "provenance=rp_align_table;stage=align;event=4;retry=rp_retry_plan;review_gate=artifact_manifest;llm_quality=rp_llmeval;status=recovered\n"
			   "provenance=rp_metrics_json;stage=profile;event=5;cache=hit;review_gate=artifact_manifest;status=ready\n"
			   "provenance=rp_report_text;stage=package;event=7;review_pack=rp_review_pack;status=ready\n"
			   "status=recovered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_report_text",
			   "# RUN-042 Recovery Report\n"
			   "The align stage failed because the first tool output was missing.\n"
			   "Recovery reran only the align stage and reused cached profile data.\n"
			   "Evidence links: rp_evidence, rp_claimrec, rp_provpath, rp_stage_log.\n"
			   "Release state: ready.\n"
			   "report_source=workflow;state_file=rp_stage_state;source_key=host_workflow_run_id\n"
			   "report_source=llm;state_file=rp_llm_resp;source_key=host_relay_response\n"
			   "report_source=backend;state_file=rp_report_text;source_key=backend_evidence_report\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_chart_data",
			   "chart=stage_attempts\n"
			   "stage,attempts,status\n"
			   "ingest,1,done\n"
			   "align,2,recovered\n"
			   "profile,1,cached\n"
			   "review,1,accepted\n"
			   "package,1,ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has_artifact_action()) {
		char provenance_protocol[40];
		int has_input = rp_host_seed_has("kind=artifact_input");
		int has_derive = rp_host_seed_has("kind=artifact_derive");
		int task6 = has_input && rp_host_seed_copy_value_for_kind(
			"kind=artifact_input", "provenance_protocol=",
			provenance_protocol, sizeof(provenance_protocol));

		if (!rp_append_file("rp_artifact", "host_artifact_actions=applied")) return 1;
		if (task6 && (!has_derive || !apply_task6_artifact_actions())) return 1;
		if (!task6 && has_input && !append_legacy_artifact_input_action()) return 1;
		if (!task6 && has_derive && !append_legacy_artifact_derive_action()) return 1;
		if (rp_host_seed_has("kind=artifact_log") && !append_artifact_log_action()) return 1;
		if (rp_host_seed_has("kind=artifact_chart") && !append_artifact_chart_action()) return 1;
		if (!rp_append_file("rp_tool", "tool=artifact_ops.host_artifact_actions")) return 1;
	}
	if (rp_host_seed_count() > 0) {
		if (rp_host_seed_has("kind=research_run")) {
			char seed_run[48];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
				rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_run_id=", seed_run)) return 1;
			char value[96];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Browser started study");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_title=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "question=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Can this platform run a custom research task?");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_question=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "provider=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "template");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_provider=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "4");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_dataset_rows=", value)) return 1;
		}
		if (rp_host_seed_has("kind=research_rerun")) {
			char rerun_id[48];
			char parent_run[48];
			char value[96];
			if (!rp_host_seed_copy_value_for_kind("kind=research_rerun", "run_id=", rerun_id, sizeof(rerun_id))) {
				rp_copy_text(rerun_id, sizeof(rerun_id), "RUN-905-rerun");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=research_rerun", "parent_run=", parent_run, sizeof(parent_run)) &&
			    !rp_host_seed_copy_value_for_kind("kind=research_rerun", "source_run=", parent_run, sizeof(parent_run))) {
				rp_copy_text(parent_run, sizeof(parent_run), "RUN-900");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_rerun_id=", rerun_id)) return 1;
			if (!rp_append_host_action_line("rp_report_text", "host_report_rerun_parent=", parent_run)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_rerun", "provider=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "template");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_rerun_provider=", value)) return 1;
			if (!rp_append_file("rp_report_text", "host_report_rerun_status=completed")) return 1;
		}
		if (rp_host_seed_has("kind=human_review")) {
			char reviewer[48];
			char decision[48];
			if (!rp_host_seed_copy_value_for_kind("kind=human_review", "reviewer=", reviewer, sizeof(reviewer))) {
				rp_copy_text(reviewer, sizeof(reviewer), "HOST");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=human_review", "decision=", decision, sizeof(decision))) {
				rp_copy_text(decision, sizeof(decision), "needs_revision");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_reviewer=", reviewer)) return 1;
			if (!rp_append_host_action_line("rp_report_text", "host_report_review_decision=", decision)) return 1;
		}
		if (rp_host_seed_has("kind=revision_task")) {
			char targets[80];
			if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
				rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_revision_targets=", targets)) return 1;
		}
		if (rp_host_seed_has("kind=bundle_export")) {
			char bundle[48];
			if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle))) {
				rp_copy_text(bundle, sizeof(bundle), "evidence");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_bundle=", bundle)) return 1;
		}
		if (rp_host_seed_has("kind=agentcompare")) {
			char profile[48];
			if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
				rp_copy_text(profile, sizeof(profile), "plain_ucore");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_compare_profile=", profile)) return 1;
		}
		if (rp_host_seed_has_workbench_action()) {
			char value[96];
			if (!rp_append_file("rp_report_text", "host_report_workbench_outputs=rp_runner,rp_revision,rp_package")) return 1;
			if (!rp_host_seed_copy_workbench_value("workbench=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "usable-workbench:RUN-900");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_workbench=", value)) return 1;
			if (rp_host_seed_copy_workbench_value("workbench_title=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_title=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("question=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_question=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("task=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_task=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("title=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_note_title=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("manifest=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_manifest=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("bundle=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_bundle=", value)) return 1;
			}
		}
	}
	if (!rp_write_file("rp_runner",
			   "runner=plain-ucore-stage-runner\n"
			   "inputs=2\n"
			   "stages=5\n"
			   "dag_edges=4\n"
			   "failed_stages=1\n"
			   "retries=1\n"
			   "cache_hits=1\n"
			   "logs=5\n"
			   "artifacts=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=artifact_ops;msg=artifact;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=research_request;msg=input;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_input")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.read_input")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_dag")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_log")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_artifact")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_report")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_chart")) return 1;
	if (!rp_append_status("input=ready")) return 1;
	if (!rp_append_status("request_form=ready")) return 1;
	if (!rp_append_status("upload_files=ready")) return 1;
	if (!rp_append_status("runner=ready")) return 1;
	if (!rp_append_status("stage_dag=ready")) return 1;
	if (!rp_append_status("artifact_ops=ready")) return 1;
	if (!rp_append_status("research_request=ready")) return 1;
	printf("rp_artifact_ops: inputs=2 stages=5 retries=1 artifacts=4 custom_requests=3 status=ready\n");
	return 0;
}
