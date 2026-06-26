#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_file_meta fs_meta;
static struct agent_file_query fs_query;
static struct agent_file_query_result fs_result;
static struct agent_file_prefetch_hint fs_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
static struct agent_op fs_op;
static struct agent_result fs_res;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentfs_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int query_stage(const char *project, const char *run_id,
		       const char *stage, const char *status,
		       struct agent_file_query_result *result)
{
	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	if (project)
		strcpy(fs_query.project, project);
	if (run_id)
		strcpy(fs_query.run_id, run_id);
	if (stage)
		strcpy(fs_query.stage, stage);
	if (status)
		strcpy(fs_query.status, status);
	return agent_file_query(&fs_query, result);
}

static void set_meta(const char *physical, const char *status, uint64 mask)
{
	memset(&fs_meta, 0, sizeof(fs_meta));
	strcpy(fs_meta.physical_name, physical);
	strcpy(fs_meta.logical_path, "/agentfs/issue4");
	strcpy(fs_meta.project, "issue4");
	strcpy(fs_meta.workflow, "fs-metadata");
	strcpy(fs_meta.run_id, "RUN-FS");
	strcpy(fs_meta.stage, "ingest");
	strcpy(fs_meta.kind, "artifact");
	if (status)
		strcpy(fs_meta.status, status);
	strcpy(fs_meta.summary, "fs metadata test file");
	fs_meta.dependency_mask = AGENT_DEP_PREPARE;
	fs_meta.flags = AGENT_FILE_META_F_PERSIST;
	fs_meta.update_mask = mask;
	check(agent_file_meta_set(&fs_meta) == 0, "meta set");
}

static void wait_file_scan_quiet(void)
{
	struct agent_info info;

	for (int i = 0; i < 4000; i++) {
		check(agent_info(&info) == 0, "scan info");
		if (info.file_scan_pending == 0)
			return;
		sched_yield();
	}
	check(0, "file scan quiet");
}

static void make_file(const char *name)
{
	int fd;
	char body[] = "agentfs";

	fd = open(name, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "open create");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "write file");
	check(close(fd) == 0, "close file");
}

static void write_file_body(const char *name, const char *body)
{
	int fd;

	fd = open(name, O_RDWR | O_TRUNC);
	check(fd >= 0, "open rewrite");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "rewrite file");
	check(close(fd) == 0, "close rewrite");
}

static uint64 digest_text(const char *text)
{
	uint64 hash = 1469598103934665603ULL;

	while (*text) {
		hash ^= (unsigned char)*text++;
		hash *= 1099511628211ULL;
	}
	return hash;
}

static void check_digest_text(const char *selector, const char *text)
{
	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_READ_FILE_DIGEST;
	fs_op.request_id = 77001;
	strcpy(fs_op.payload, selector);
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1, "digest run");
	check(fs_res.status == AGENT_STATUS_OK, "digest status");
	check(fs_res.value0 == strlen(text), "digest size");
	check(fs_res.value1 == strlen(text), "digest bytes");
	check(fs_res.value2 == digest_text(text), "digest hash");
	check(strcmp(fs_res.result, text) == 0, "digest preview");
}

static void check_digest_timeline(const char *text)
{
	struct agent_timeline_filter filter;
	struct agent_timeline_record records[8];
	int n;
	int found = 0;

	memset(&filter, 0, sizeof(filter));
	filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
		       AGENT_TIMELINE_FILTER_TOOL_ID;
	filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_CONTEXT;
	filter.tool_id = AGENT_TOOL_READ_FILE_DIGEST;
	n = agent_timeline_query(&filter, records, 8);
	check(n >= 1, "digest timeline query");
	for (int i = 0; i < n; i++) {
		if (records[i].value0 == strlen(text) &&
		    records[i].value1 == strlen(text) &&
		    records[i].value2 == digest_text(text) &&
		    strcmp(records[i].text, text) == 0) {
			found = 1;
			break;
		}
	}
	check(found, "digest timeline record");
}

static void make_code(char *out, char prefix, int n)
{
	out[0] = prefix;
	out[1] = '0' + (n / 100) % 10;
	out[2] = '0' + (n / 10) % 10;
	out[3] = '0' + n % 10;
	out[4] = 0;
}

static void set_bulk_meta(int i)
{
	char name[8];
	char status[8];

	make_code(name, 'm', i);
	make_code(status, 'b', i);
	make_file(name);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 200 + i;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, name);
	strcpy(fs_meta.project, "issue4");
	strcpy(fs_meta.workflow, "fs-metadata");
	strcpy(fs_meta.run_id, "RUN-BULK");
	strcpy(fs_meta.stage, "bulk");
	strcpy(fs_meta.kind, "artifact");
	strcpy(fs_meta.status, status);
	strcpy(fs_meta.summary, "bulk metadata file");
	fs_meta.dependency_mask = AGENT_DEP_PREPARE;
	check(agent_file_meta_set(&fs_meta) == 0, "bulk meta set");
}

static void check_index_scan_gap(void)
{
	static struct agent_file_query_result scan;
	static struct agent_file_query_result index;

	for (int i = 0; i < 100; i++)
		set_bulk_meta(i);
	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(fs_query.project, "issue4");
	strcpy(fs_query.run_id, "RUN-BULK");
	strcpy(fs_query.stage, "bulk");
	strcpy(fs_query.status, "b042");
	fs_query.flags = AGENT_FILE_QUERY_SCAN;
	check(agent_file_query(&fs_query, &scan) >= 1, "bulk scan query");
	check(scan.plan == AGENT_FILE_QUERY_PLAN_SCAN, "scan plan");
	check(scan.index_bucket == -1, "scan bucket");
	check((scan.plan_reason & AGENT_FILE_QUERY_REASON_FORCED_SCAN) != 0,
	      "scan reason");
	check(scan.candidate_records == scan.scanned_records,
	      "scan candidates");
	fs_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	check(agent_file_query(&fs_query, &index) >= 1, "bulk index query");
	check(index.plan == AGENT_FILE_QUERY_PLAN_STATUS_INDEX,
	      "status index plan");
	check(index.index_bucket >= 0, "index bucket");
	check((index.plan_reason & AGENT_FILE_QUERY_REASON_STATUS_INDEX) != 0,
	      "index reason");
	check(index.candidate_records == index.scanned_records,
	      "index candidates");
	check(index.fs_generation >= scan.fs_generation,
	      "index generation");
	check(scan.total_hits == index.total_hits, "scan index hits");
	check(scan.returned == index.returned, "scan index returned");
	check(strcmp(scan.hits[0].physical_name,
		     index.hits[0].physical_name) == 0,
	      "scan index first hit");
	check(scan.scanned_records > index.scanned_records,
	      "index scans fewer records");
	printf("agentfs_ucore: bulk_index scan=%d index=%d hits=%d\n",
	       scan.scanned_records, index.scanned_records,
	       index.total_hits);
	printf("agentfs_ucore: query_plan scan_plan=%d index_plan=%d reason=%d bucket=%d candidates=%d\n",
	       scan.plan, index.plan, (int)index.plan_reason,
	       index.index_bucket, index.candidate_records);
	printf("agentfs_ucore: scan_index_consistent=1\n");

	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	fs_query.max_hits = 3;
	strcpy(fs_query.project, "issue4");
	strcpy(fs_query.run_id, "RUN-BULK");
	strcpy(fs_query.stage, "bulk");
	check(agent_file_query(&fs_query, &index) == 3,
	      "truncated index query");
	check(index.plan == AGENT_FILE_QUERY_PLAN_STAGE_INDEX,
	      "truncated stage index plan");
	check(index.total_hits > index.returned, "truncated hit count");
	check(index.returned == 3, "truncated returned");
	check(index.truncated == 1, "truncated flag");
	printf("agentfs_ucore: truncated_query total=%d returned=%d truncated=%d\n",
	       index.total_hits, index.returned, index.truncated);
}

static void check_prefetch_hints(void)
{
	int n;
	int found = 0;

	n = agent_file_prefetch_snapshot(fs_hints,
					 AGENT_FILE_PREFETCH_MAX_HINTS);
	check(n >= 1, "prefetch snapshot");
	for (int i = 0; i < n; i++) {
		check(fs_hints[i].source_sequence > 0,
		      "prefetch source sequence");
		check((fs_hints[i].reason &
		       AGENT_FILE_PREFETCH_REASON_DEPENDENCY) != 0,
		      "prefetch dependency reason");
		check((fs_hints[i].reason &
		       AGENT_FILE_PREFETCH_REASON_STAGE_INDEX) != 0,
		      "prefetch stage index reason");
		check(fs_hints[i].plan == AGENT_FILE_QUERY_PLAN_STAGE_INDEX,
		      "prefetch plan");
		check(fs_hints[i].candidate_records > 0,
		      "prefetch candidates");
		if (strcmp(fs_hints[i].hit.stage, "analyze") == 0 ||
		    strcmp(fs_hints[i].hit.stage, "report") == 0)
			found = 1;
	}
	check(found, "prefetch dependent stage");
	printf("agentfs_ucore: prefetch_hints=1 count=%d first_stage=%s source_seq=%d\n",
	       n, fs_hints[0].hit.stage, (int)fs_hints[0].source_sequence);
}

static void run_agent(void)
{
	const char *name = "fsissue4";

	check(agent_file_meta_init() == 0, "meta init");
	check(query_stage("lab-gene-x", "RUN-042", "align", 0, &fs_result) >= 1,
	      "default query");
	check(fs_result.hits[0].dev != 0 && fs_result.hits[0].inum != 0,
	      "default inode binding");
	printf("agentfs_ucore: default_inode dev=%d inum=%d scanned=%d\n",
	       (int)fs_result.hits[0].dev, (int)fs_result.hits[0].inum,
	       fs_result.scanned_records);
	check_prefetch_hints();

	make_file(name);
	set_meta(name, "ok", 0);
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) >= 1,
	      "custom query");
	check(fs_result.hits[0].dev != 0 && fs_result.hits[0].inum != 0,
	      "custom inode binding");
	check(fs_result.hits[0].size >= 7, "custom size");
	printf("agentfs_ucore: custom_inode dev=%d inum=%d size=%d\n",
	       (int)fs_result.hits[0].dev, (int)fs_result.hits[0].inum,
	       (int)fs_result.hits[0].size);
	struct agent_info digest_before;
	struct agent_info digest_after;
	struct agent_info rewrite_before;
	struct agent_info rewrite_after;

	check(agent_info(&digest_before) == 0, "digest info before");
	check_digest_text(name, "agentfs");
	check_digest_text("project=issue4;run_id=RUN-FS;stage=ingest;status=ok",
			  "agentfs");
	check(agent_info(&digest_after) == 0, "digest info after");
	check(digest_after.file_digest_cache_hits >
		      digest_before.file_digest_cache_hits,
	      "digest cache hit");
	check(digest_after.file_digest_cache_misses >
		      digest_before.file_digest_cache_misses,
	      "digest cache miss");
	printf("agentfs_ucore: content_digest=1 size=%d bytes=%d hash=%d preview=%s\n",
	       (int)fs_res.value0, (int)fs_res.value1, (int)fs_res.value2,
	       fs_res.result);
	printf("agentfs_ucore: digest_cache=1 hits=%d misses=%d\n",
	       (int)(digest_after.file_digest_cache_hits -
		     digest_before.file_digest_cache_hits),
	       (int)(digest_after.file_digest_cache_misses -
		     digest_before.file_digest_cache_misses));
	check(agent_info(&rewrite_before) == 0, "rewrite info before");
	write_file_body(name, "agentfs2");
	check_digest_text(name, "agentfs2");
	check(agent_info(&rewrite_after) == 0, "rewrite info after");
	check(rewrite_after.file_digest_cache_misses >
		      rewrite_before.file_digest_cache_misses,
	      "digest cache invalidated");
	check_digest_timeline("agentfs2");
	printf("agentfs_ucore: digest_cache_invalidated=1 misses=%d\n",
	       (int)(rewrite_after.file_digest_cache_misses -
		     rewrite_before.file_digest_cache_misses));
	printf("agentfs_ucore: digest_timeline=1 tool=%d preview=agentfs2\n",
	       AGENT_TOOL_READ_FILE_DIGEST);

	check(agent_file_meta_init() == 0, "meta reload");
	wait_file_scan_quiet();
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) >= 1,
	      "reload keeps custom query");
	check(strcmp(fs_result.hits[0].physical_name, name) == 0,
	      "reload keeps physical name");
	printf("agentfs_ucore: .agentmeta_reload=1\n");
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) >= 1,
	      "query cache hit query");
	check((fs_result.plan_reason & AGENT_FILE_QUERY_REASON_CACHE_HIT) != 0,
	      "query cache hit reason");
	printf("agentfs_ucore: query_cache=1 reason=%d\n",
	       (int)fs_result.plan_reason);

	check_index_scan_gap();

	set_meta(name, "", AGENT_FILE_META_UPDATE_STATUS);
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) == 0,
	      "cleared status query");
	printf("agentfs_ucore: clear_status=1 cache_invalidated=1\n");

	set_meta(name, "failed", AGENT_FILE_META_UPDATE_STATUS);
	check(query_stage("issue4", "RUN-FS", "ingest", "failed",
			  &fs_result) >= 1,
	      "failed status query");
	check(unlink(name) == 0, "unlink file");
	check(query_stage("issue4", "RUN-FS", "ingest", 0, &fs_result) == 0,
	      "delete clears metadata");
	printf("agentfs_ucore: delete_clears_metadata=1\n");

	memset(&fs_op, 0, sizeof(fs_op));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_RERUN_STAGE;
	fs_op.request_id = 99001;
	strcpy(fs_op.payload, "stage=align;run_id=RUN-NOPE;project=issue4");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1, "rerun missing");
	check(fs_res.status == AGENT_STATUS_NOT_FOUND, "rerun missing status");
	printf("agentfs_ucore: missing_selector_not_found=1\n");

	printf("agentfs_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentfs_ucore: Agent FS metadata test\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_agent();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentfs_ucore: parent passed\n");
	return 0;
}
