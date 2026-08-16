#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

#define PRELOAD_QUERY_FILE "fspreqry"
#define PRELOAD_PARTIAL_FILE "fspartial"
#define META_SET_RETRY_LIMIT (8U * AGENT_FILE_META_MAX)
#define VERSION_RESIDENCY_FILES 120
#define META_BATCH_CASE_COUNT 6
#define META_BATCH_STATUS_GUARD 0x13579bdf

static struct agent_file_meta fs_meta;
static struct agent_file_query fs_query;
static struct agent_file_query_result fs_result;
static struct agent_file_hit stale_before;
static struct agent_file_hit stale_after;
static struct agent_op fs_op;
static struct agent_result fs_res;
static struct agent_file_meta fs_batch_items[AGENT_FILE_META_BATCH_MAX];
static int fs_batch_statuses[AGENT_FILE_META_BATCH_MAX + 1];
static struct agent_file_hit fs_batch_hits[3];

struct digest_counters {
	uint64 hits;
	uint64 misses;
};

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentfs_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

/* 内存 catalog 事务争用是短暂背压；调用者有界让出 CPU 后重试。 */
static int metadata_set_bounded(struct agent_file_meta *meta)
{
	int status = AGENT_STATUS_RETRY;

	for (uint attempts = 0; attempts < META_SET_RETRY_LIMIT; attempts++) {
		status = agent_file_meta_set(meta);
		if (status != AGENT_STATUS_RETRY)
			return status;
		if (sched_yield() < 0)
			break;
	}
	return status;
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
	fs_meta.dependency_mask = agent_dependency_label_bit("ready");
	fs_meta.flags = 0;
	fs_meta.update_mask = mask;
	check(metadata_set_bounded(&fs_meta) == 0, "meta set");
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

static void fill_batch_meta(struct agent_file_meta *meta, int fid,
			    const char *physical, const char *status)
{
	memset(meta, 0, sizeof(*meta));
	meta->fid = fid;
	strcpy(meta->physical_name, physical);
	strcpy(meta->logical_path, physical);
	strcpy(meta->project, "meta-batch");
	strcpy(meta->workflow, "ordered-prefix");
	strcpy(meta->run_id, "RUN-BATCH");
	strcpy(meta->stage, "register");
	strcpy(meta->kind, "artifact");
	strcpy(meta->status, status);
	strcpy(meta->summary, "batched metadata member");
}

static void query_batch_member(const char *physical, const char *status,
			       struct agent_file_hit *hit)
{
	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(fs_query.physical_name, physical);
	strcpy(fs_query.status, status);
	memset(&fs_result, 0, sizeof(fs_result));
	check(agent_file_query(&fs_query, &fs_result) == 1,
	      "batch member query");
	check(fs_result.used_index == 1 && fs_result.returned == 1,
	      "batch member uses index");
	check(fs_result.hits[0].dev != 0 && fs_result.hits[0].inum != 0 &&
	      fs_result.hits[0].incarnation != 0,
	      "batch member inode identity");
	if (hit != 0)
		*hit = fs_result.hits[0];
}

static void check_file_meta_batch_prefix(void)
{
	static const int expected[META_BATCH_CASE_COUNT] = {
		AGENT_STATUS_OK,
		AGENT_STATUS_BAD_PARAM,
		AGENT_STATUS_OK,
		AGENT_STATUS_CONFLICT,
		AGENT_STATUS_NOT_FOUND,
		AGENT_STATUS_OK,
	};
	static const char *const names[3] = {
		"bmeta0", "bmeta2", "bmeta5",
	};
	static const char *const statuses[3] = {
		"batch0", "batch2", "batch5",
	};
	static const int item_indexes[3] = { 0, 2, 5 };
	int processed;

	for (int i = 0; i < 3; i++)
		make_file(names[i]);
	memset(fs_batch_items, 0, sizeof(fs_batch_items));
	fill_batch_meta(&fs_batch_items[0], 480, names[0], statuses[0]);
	fill_batch_meta(&fs_batch_items[1], 0, "bmeta-bad", "invalid");
	fs_batch_items[1].flags = AGENT_FILE_META_F_PERSIST;
	fill_batch_meta(&fs_batch_items[2], 481, names[1], statuses[1]);
	fill_batch_meta(&fs_batch_items[3], 480, names[1], "conflict");
	strcpy(fs_batch_items[4].physical_name, "bmeta-missing");
	fs_batch_items[4].flags = AGENT_FILE_META_F_DELETE;
	fill_batch_meta(&fs_batch_items[5], 482, names[2], statuses[2]);
	for (int i = 0; i <= AGENT_FILE_META_BATCH_MAX; i++)
		fs_batch_statuses[i] = META_BATCH_STATUS_GUARD;

	processed = agent_file_meta_set_batch(
		fs_batch_items, fs_batch_statuses, META_BATCH_CASE_COUNT,
		AGENT_FILE_META_BATCH_F_NONE);
	check(processed == META_BATCH_CASE_COUNT, "batch processed prefix");
	for (int i = 0; i < META_BATCH_CASE_COUNT; i++)
		check(fs_batch_statuses[i] == expected[i],
		      "batch ordered item status");
	check(fs_batch_statuses[META_BATCH_CASE_COUNT] ==
		      META_BATCH_STATUS_GUARD,
	      "batch status guard untouched");
	for (int i = 0; i < 3; i++)
		query_batch_member(names[i], statuses[i], &fs_batch_hits[i]);
	for (int i = 0; i < 3; i++)
		check(fs_batch_hits[i].fid ==
		      fs_batch_items[item_indexes[i]].fid,
		      "batch member keeps requested fid");
	printf("agentfs_ucore: meta_batch prefix=OK,BAD_PARAM,OK,CONFLICT,NOT_FOUND,OK guard=1 no_rollback=1\n");

	check(agent_metadata_init() == AGENT_STATUS_OK,
	      "same lifecycle metadata init");
	for (int i = 0; i < 3; i++) {
		struct agent_file_hit after;

		memset(&after, 0, sizeof(after));
		query_batch_member(names[i], statuses[i], &after);
		check(after.fid == fs_batch_hits[i].fid &&
		      after.dev == fs_batch_hits[i].dev &&
		      after.inum == fs_batch_hits[i].inum &&
		      after.incarnation == fs_batch_hits[i].incarnation,
		      "meta init preserves batch identity");
	}
	printf("agentfs_ucore: meta_batch same_lifecycle_reuse=1 identity=1 indexed=1\n");

	fs_batch_statuses[0] = META_BATCH_STATUS_GUARD;
	check(agent_file_meta_set_batch(0, fs_batch_statuses, 1,
					AGENT_FILE_META_BATCH_F_NONE) == -1,
	      "batch null items rejected");
	check(fs_batch_statuses[0] == META_BATCH_STATUS_GUARD,
	      "batch null items leave status untouched");
	make_file("bpreflight");
	fill_batch_meta(&fs_batch_items[6], 483, "bpreflight", "preflight");
	check(agent_file_meta_set_batch(&fs_batch_items[6], 0, 1,
					AGENT_FILE_META_BATCH_F_NONE) == -1,
	      "batch null statuses rejected");
	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(fs_query.physical_name, "bpreflight");
	memset(&fs_result, 0, sizeof(fs_result));
	check(agent_file_query(&fs_query, &fs_result) == 0,
	      "status preflight prevents metadata commit");
	check(unlink("bpreflight") == 0, "unlink status preflight file");
	check(agent_file_meta_set_batch(fs_batch_items, fs_batch_statuses, -1,
					AGENT_FILE_META_BATCH_F_NONE) == -1,
	      "batch negative count rejected");
	check(agent_file_meta_set_batch(
		fs_batch_items, fs_batch_statuses,
		AGENT_FILE_META_BATCH_MAX + 1,
		AGENT_FILE_META_BATCH_F_NONE) == -1,
	      "batch over-capacity rejected");
	check(agent_file_meta_set_batch(fs_batch_items, fs_batch_statuses, 1,
					1) == -1,
	      "batch flags rejected");
	check(agent_file_meta_set_batch(
		fs_batch_items, (int *)&fs_batch_items[1].flags, 2,
		AGENT_FILE_META_BATCH_F_NONE) == -1,
	      "batch overlapping buffers rejected");
	check(fs_batch_items[1].flags == AGENT_FILE_META_F_PERSIST,
	      "batch overlap rejection preserves input");
	check(agent_file_meta_set_batch(0, 0, 0,
					AGENT_FILE_META_BATCH_F_NONE) == 0,
	      "empty batch succeeds without buffers");
	check(fs_batch_statuses[0] == META_BATCH_STATUS_GUARD,
	      "invalid batches leave status untouched");
	printf("agentfs_ucore: meta_batch invalid_args=6 empty_batch=1 status_untouched=1 status_preflight=1 overlap=1\n");

	for (int i = 0; i < 3; i++)
		check(unlink(names[i]) == 0, "unlink batch member");
}

static void version_residency_name(char *name, int index)
{
	strcpy(name, "vrs000");
	name[3] = '0' + index / 100;
	name[4] = '0' + (index / 10) % 10;
	name[5] = '0' + index % 10;
}

/* 顺序编辑的活文件数超过单域驻留槽，冷版本必须可回收而不是报满。 */
static void check_version_residency_reclaim(void)
{
	struct agent_file_edit_state state;
	char name[8];

	for (int i = 0; i < VERSION_RESIDENCY_FILES; i++) {
		version_residency_name(name, i);
		make_file(name);
	}
	for (int i = 0; i < VERSION_RESIDENCY_FILES; i++) {
		version_residency_name(name, i);
		memset(&state, 0, sizeof(state));
		check(agent_file_edit_begin(name, 0, 20, &state) == 0,
		      "version residency edit begin");
		check(agent_file_edit_abort(state.lease_id) == 0,
		      "version residency edit abort");
	}
	for (int i = 0; i < VERSION_RESIDENCY_FILES; i++) {
		version_residency_name(name, i);
		check(unlink(name) == 0, "version residency unlink");
	}
	printf("agentfs_ucore: version_residency_files=%d evictable=1\n",
	       VERSION_RESIDENCY_FILES);
}

static void query_physical(const char *name)
{
	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(fs_query.physical_name, name);
	memset(&fs_result, 0, sizeof(fs_result));
	check(agent_file_query(&fs_query, &fs_result) == 1,
	      "physical metadata query");
	check(fs_result.returned == 1 &&
	      strcmp(fs_result.hits[0].physical_name, name) == 0,
	      "physical metadata identity");
}

static void check_dirent_name_boundary(void)
{
	const char *canonical = "nmlimit1234567";
	const char *long_name = "nmlimit1234567x";
	const char *same_alias = "nmlimit1234567y";
	char body[3] = {0};
	int fd;

	check(strlen(canonical) == 14 && strlen(long_name) == 15,
	      "dirent name boundary fixture");
	fd = open(long_name, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "create canonical long-name alias");
	check(write(fd, "A", 1) == 1 && write(fd, "B", 1) == 1,
	      "append through long-name create");
	check(close(fd) == 0, "close long-name create");
	fd = open(same_alias, O_RDONLY);
	check(fd >= 0 && read(fd, body, 2) == 2 && strcmp(body, "AB") == 0,
	      "same prefix reopens one legacy alias");
	check(close(fd) == 0, "close long-name reopen");
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 499;
	strcpy(fs_meta.physical_name, canonical);
	strcpy(fs_meta.logical_path, "/agentfs/name-boundary");
	strcpy(fs_meta.status, "explicit");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == AGENT_STATUS_OK,
	      "register canonical metadata explicitly");
	query_physical(canonical);
	check(unlink(same_alias) == 0, "remove canonical long-name alias");
	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(fs_query.physical_name, canonical);
	memset(&fs_result, 0, sizeof(fs_result));
	check(agent_file_query(&fs_query, &fs_result) == 0,
	      "canonical metadata delete");
	printf("agentfs_ucore: dirent_name_bound=14 legacy_alias=1 "
	       "explicit_metadata_canonical=1\n");
}

static void check_preload_create_query(void)
{
	const char *name = PRELOAD_QUERY_FILE;

	memset(&fs_query, 0, sizeof(fs_query));
	fs_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(fs_query.physical_name, name);
	memset(&fs_result, 0, sizeof(fs_result));
	check(agent_file_query(&fs_query, &fs_result) == 0,
	      "ordinary preload is not auto-cataloged");
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 498;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, "/agentfs/preload");
	strcpy(fs_meta.status, "explicit");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == AGENT_STATUS_OK,
	      "explicitly register preload");
	query_physical(name);
	check(fs_result.hits[0].dev != 0 && fs_result.hits[0].inum != 0,
	      "explicit preload binds inode identity");
	check(unlink(name) == 0, "remove preload create probe");
	printf("agentfs_ucore: explicit_create_query=1 ordinary_unindexed=1\n");
}

static void check_partial_update_binding(void)
{
	const char *name = PRELOAD_PARTIAL_FILE;

	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 500;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, "/agentfs/partial");
	strcpy(fs_meta.status, "initial");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == 0,
	      "explicit registration binds existing inode");
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 500;
	strcpy(fs_meta.status, "partial");
	fs_meta.flags = 0;
	fs_meta.update_mask = AGENT_FILE_META_UPDATE_STATUS;
	check(metadata_set_bounded(&fs_meta) == 0,
	      "partial update targets explicit member");
	query_physical(name);
	check(strcmp(fs_result.hits[0].status, "partial") == 0 &&
	      fs_result.hits[0].dev != 0 && fs_result.hits[0].inum != 0,
	      "partial update keeps inode identity");
	printf("agentfs_ucore: partial_update_binding=1\n");
}

static void set_demo_meta(int fid, const char *physical, const char *stage,
			  const char *kind, const char *status,
			  const char *summary, uint64 deps)
{
	make_file(physical);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = fid;
	strcpy(fs_meta.physical_name, physical);
	strcpy(fs_meta.logical_path, physical);
	strcpy(fs_meta.project, "lab-gene-x");
	strcpy(fs_meta.workflow, "nightly-regression");
	strcpy(fs_meta.run_id, "RUN-042");
	strcpy(fs_meta.stage, stage);
	strcpy(fs_meta.kind, kind);
	strcpy(fs_meta.status, status);
	strcpy(fs_meta.summary, summary);
	fs_meta.dependency_mask = deps;
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == 0, "demo meta set");
}

static void seed_demo_metadata(void)
{
	set_demo_meta(1, "r42align", "align", "artifact", "ok",
		      "align output is ready before injected failure",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report"));
	set_demo_meta(2, "r42anlz", "analyze", "status", "pending",
		      "analysis waits for align",
		      agent_dependency_label_bit("report"));
	set_demo_meta(3, "r42report", "report", "report", "pending",
		      "report waits for analyze", 0);
}

static void check_selector_consistency(void)
{
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 2;
	strcpy(fs_meta.physical_name, "r42align");
	fs_meta.flags = AGENT_FILE_META_F_DELETE;
	check(metadata_set_bounded(&fs_meta) == AGENT_STATUS_CONFLICT,
	      "conflicting selectors rejected");
	query_physical("r42align");
	query_physical("r42anlz");
	printf("agentfs_ucore: selector_consistency=1\n");
}

static void check_stale_identity_guard(void)
{
	const char *name = "fsstale";

	make_file(name);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 508;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, "/agentfs/stale");
	strcpy(fs_meta.status, "first");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == AGENT_STATUS_OK,
	      "register stale identity source explicitly");
	query_physical(name);
	stale_before = fs_result.hits[0];
	check(unlink(name) == 0, "delete stale identity source");
	make_file(name);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 508;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, "/agentfs/stale");
	strcpy(fs_meta.status, "replacement");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == AGENT_STATUS_OK,
	      "register stale identity replacement explicitly");
	query_physical(name);
	stale_after = fs_result.hits[0];
	check(stale_before.dev != stale_after.dev ||
		      stale_before.inum != stale_after.inum ||
		      stale_before.incarnation != stale_after.incarnation,
	      "recreated file has a fresh inode identity");
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = stale_after.fid;
	strcpy(fs_meta.physical_name, name);
	fs_meta.dev = stale_before.dev;
	fs_meta.inum = stale_before.inum;
	fs_meta.incarnation = stale_before.incarnation;
	fs_meta.flags = AGENT_FILE_META_F_DELETE;
	check(metadata_set_bounded(&fs_meta) == AGENT_STATUS_CONFLICT,
	      "stale inode identity cannot select replacement metadata");
	query_physical(name);
	check(fs_result.hits[0].dev == stale_after.dev &&
		      fs_result.hits[0].inum == stale_after.inum &&
		      fs_result.hits[0].incarnation == stale_after.incarnation,
	      "stale selector leaves replacement metadata intact");
	printf("agentfs_ucore: stale_identity_guard=1\n");
}

static void check_same_name_recreate_live(void)
{
	const char *name = "fsrebind";
	struct agent_file_hit before;

	make_file(name);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 509;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, "/agentfs/rebind");
	strcpy(fs_meta.status, "old");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == 0, "bind live rebind source");
	query_physical(name);
	before = fs_result.hits[0];
	check(unlink(name) == 0, "unlink rebind source");
	make_file(name);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 509;
	strcpy(fs_meta.physical_name, name);
	strcpy(fs_meta.logical_path, "/agentfs/rebind");
	strcpy(fs_meta.status, "rebuilt");
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == 0, "bind recreated pathname");
	query_physical(name);
	check((before.dev != fs_result.hits[0].dev ||
	       before.inum != fs_result.hits[0].inum ||
	       before.incarnation != fs_result.hits[0].incarnation) &&
	      strcmp(fs_result.hits[0].status, "rebuilt") == 0,
	      "recreated pathname has one live fresh identity");
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 509;
	fs_meta.flags = AGENT_FILE_META_F_DELETE;
	check(metadata_set_bounded(&fs_meta) == 0, "delete rebind metadata");
	check(unlink(name) == 0, "delete rebind file");
	printf("agentfs_ucore: same_name_recreate_live=1\n");
}

static void set_scoped_meta(const char *run_id, int fid,
			    const char *physical, const char *label,
			    uint64 deps)
{
	make_file(physical);
	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = fid;
	strcpy(fs_meta.physical_name, physical);
	strcpy(fs_meta.logical_path, physical);
	strcpy(fs_meta.project, "lab-gene-x");
	strcpy(fs_meta.workflow, "nightly-regression");
	strcpy(fs_meta.run_id, run_id);
	strcpy(fs_meta.stage, label);
	strcpy(fs_meta.kind, "artifact");
	strcpy(fs_meta.status, "pending");
	strcpy(fs_meta.summary, "scoped dependency fixture");
	fs_meta.dependency_mask = deps;
	fs_meta.flags = 0;
	check(metadata_set_bounded(&fs_meta) == 0, "scoped meta set");
}

static void seed_scoped_dependency_metadata(void)
{
	set_scoped_meta("RUN-ALT", 11, "altalign", "align",
			agent_dependency_label_bit("archive"));
	set_scoped_meta("RUN-ALT", 12, "altarch", "archive", 0);
}

static void seed_user_dependency_metadata(void)
{
	set_scoped_meta("RUN-GEN", 21, "genalign", "align", 0);
	set_scoped_meta("RUN-GEN", 22, "genreview", "review", 0);
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

static __attribute__((noinline)) void
read_digest_counters(struct digest_counters *counters, const char *msg)
{
	struct agent_info info;

	check(agent_info(&info) == 0, msg);
	counters->hits = info.file_digest_cache_hits;
	counters->misses = info.file_digest_cache_misses;
}

static __attribute__((noinline)) void check_digest_timeline(const char *text)
{
	struct agent_timeline_filter filter;
	static struct agent_timeline_record records[8];
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
	fs_meta.dependency_mask = agent_dependency_label_bit("ready");
	check(metadata_set_bounded(&fs_meta) == 0, "bulk meta set");
	/* 索引夹具保持易失；持久写回由独立用例验证。 */
	write_file_body(name, "agentfs");
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
	check(scan.scanned_records > 0 &&
	      scan.scanned_records <= AGENT_FILE_META_MAX,
	      "forced scan accounts visible live records");
	check(scan.candidate_records == scan.scanned_records,
	      "scan traverses only visible live records");
	fs_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	check(agent_file_query(&fs_query, &index) >= 1, "bulk index query");
	check(index.plan == AGENT_FILE_QUERY_PLAN_STATUS_INDEX,
	      "status index plan");
	check(index.index_bucket >= 0, "index bucket");
	check((index.plan_reason & AGENT_FILE_QUERY_REASON_STATUS_INDEX) != 0,
	      "index reason");
	check(index.candidate_records > 0 &&
	      index.candidate_records <= index.scanned_records,
	      "index candidates are visible live records");
	check(index.scanned_records > 0 &&
	      index.scanned_records < AGENT_FILE_META_MAX,
	      "index dynamically accounts its bucket chain");
	check(index.fs_generation >= scan.fs_generation,
	      "index generation");
	check(scan.total_hits == index.total_hits, "scan index hits");
	check(scan.returned == index.returned, "scan index returned");
	check(scan.hits[0].fid == 242 && scan.hits[0].dev != 0 &&
		      scan.hits[0].inum != 0 &&
		      scan.hits[0].incarnation != 0 &&
		      scan.hits[0].size == strlen("agentfs"),
	      "bulk metadata remains bound to a real file");
	check(strcmp(scan.hits[0].physical_name,
		     index.hits[0].physical_name) == 0,
	      "scan index first hit");
	check(scan.scanned_records > index.scanned_records,
	      "index scans fewer records");
	check(scan.candidate_records > index.candidate_records,
	      "index narrows visible candidates");
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

static uint64 query_preemptions(void)
{
	return (uint64)kernel_work_last_preemptions();
}

struct handoff_replacement_result {
	int pid;
	int event_queue_count;
	int mailbox_valid;
};

struct handoff_target_ready {
	int pid;
	char ready;
};

static void run_handoff_exit_target(int source_pid, int ready_fd,
				    int churn_fd)
{
	struct agent_event event;
	struct handoff_target_ready ready;
	char churn = 'C';

	check(agent_watch(AGENT_EVENT_MESSAGE, "handoff-exit") == 0,
	      "watch handoff exit message");
	check(agent_route_config(source_pid, getpid(),
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "accept handoff exit route");
	memset(&ready, 0, sizeof(ready));
	ready.pid = getpid();
	ready.ready = 'R';
	check(write(ready_fd, &ready, sizeof(ready)) == (int)sizeof(ready),
	      "handoff exit target ready");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, -1) == AGENT_STATUS_OK,
	      "handoff exit target wait");
	check(event.type == AGENT_EVENT_MESSAGE && event.corr_id == 78100,
	      "handoff exit target event");
	check(write(churn_fd, &churn, 1) == 1,
	      "release handoff replacement churn");
	exit(0);
}

static void run_handoff_replacement(int inspect_fd, int report_fd)
{
	struct handoff_replacement_result replacement;
	struct agent_info info;
	struct agent_op op;
	struct agent_result result;
	char inspect = 0;

	check(read(inspect_fd, &inspect, 1) == 1 && inspect == 'I',
	      "wait handoff replacement inspection");
	memset(&replacement, 0, sizeof(replacement));
	replacement.pid = getpid();
	check(agent_info(&info) == AGENT_STATUS_OK,
	      "inspect replacement event state");
	replacement.event_queue_count = info.event_queue_count;
	memset(&op, 0, sizeof(op));
	memset(&result, 0, sizeof(result));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_READ_MESSAGE;
	op.request_id = 78101;
	check(agent_run(&op, &result, 1, 0) == 1,
	      "read replacement mailbox");
	check(result.status == AGENT_STATUS_OK,
	      "replacement mailbox status");
	replacement.mailbox_valid = result.value0 != 0;
	check(write(report_fd, &replacement, sizeof(replacement)) ==
		      (int)sizeof(replacement),
	      "report handoff replacement state");
	exit(0);
}

static void run_handoff_churner(int source_pid, int ready_fd,
				int churn_read_fd, int churn_write_fd,
				int inspect_fd, int report_fd)
{
	int target_pid;
	int target_status = 0;
	int replacement_pid;
	int status = 0;
	char churn = 0;

	check(agent_scope_delegate_fd(ready_fd) == AGENT_STATUS_OK,
	      "delegate handoff target ready pipe");
	check(agent_scope_delegate_fd(churn_write_fd) == AGENT_STATUS_OK,
	      "delegate handoff target churn pipe");
	target_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(target_pid >= 0, "create handoff exit target");
	if (target_pid == 0)
		run_handoff_exit_target(source_pid, ready_fd, churn_write_fd);
	check(read(churn_read_fd, &churn, 1) == 1 && churn == 'C',
	      "wait handoff target recycle");
	check(waitpid(target_pid, &target_status) == target_pid,
	      "reap handoff exit target");
	check(target_status == 0, "handoff exit target status");
	/* waitpid 仅在调度器回收后观察子进程记录，随后创建的替代进程必须复用最低空闲槽位。 */
	check(agent_scope_delegate_fd(inspect_fd) == AGENT_STATUS_OK,
	      "delegate handoff replacement inspect pipe");
	check(agent_scope_delegate_fd(report_fd) == AGENT_STATUS_OK,
	      "delegate handoff replacement report pipe");
	replacement_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(replacement_pid >= 0, "create handoff replacement");
	if (replacement_pid == 0)
		run_handoff_replacement(inspect_fd, report_fd);
	check(write(report_fd, &replacement_pid, sizeof(replacement_pid)) ==
		      (int)sizeof(replacement_pid),
	      "report handoff replacement pid");
	check(waitpid(replacement_pid, &status) == replacement_pid,
	      "reap handoff replacement");
	check(status == 0, "handoff replacement status");
	exit(0);
}

static void check_handoff_target_exit(void)
{
	struct handoff_replacement_result replacement;
	struct handoff_target_ready ready;
	struct agent_event event;
	uint64 preemptions;
	int ready_pipe[2];
	int churn_pipe[2];
	int inspect_pipe[2];
	int report_pipe[2];
	int churner_status = 0;
	int send_status;
	int target_pid;
	int churner_pid;
	int replacement_pid = 0;
	char inspect = 'I';
	int source_pid = getpid();

	check(pipe(ready_pipe) == 0, "handoff exit ready pipe");
	check(pipe(churn_pipe) == 0, "handoff churn pipe");
	check(pipe(inspect_pipe) == 0, "handoff inspect pipe");
	check(pipe(report_pipe) == 0, "handoff report pipe");
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate handoff churner ready pipe");
	check(agent_scope_delegate_fd(churn_pipe[0]) == AGENT_STATUS_OK,
	      "delegate handoff churner wait pipe");
	check(agent_scope_delegate_fd(churn_pipe[1]) == AGENT_STATUS_OK,
	      "delegate handoff churner signal pipe");
	check(agent_scope_delegate_fd(inspect_pipe[0]) == AGENT_STATUS_OK,
	      "delegate handoff churner inspect pipe");
	check(agent_scope_delegate_fd(report_pipe[1]) == AGENT_STATUS_OK,
	      "delegate handoff churner report pipe");
	churner_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(churner_pid >= 0, "create handoff churner");
	if (churner_pid == 0)
		run_handoff_churner(source_pid, ready_pipe[1], churn_pipe[0],
				   churn_pipe[1], inspect_pipe[0],
				   report_pipe[1]);
	memset(&ready, 0, sizeof(ready));
	check(read(ready_pipe[0], &ready, sizeof(ready)) ==
		      (int)sizeof(ready) &&
	      ready.ready == 'R' && ready.pid > 0,
	      "wait handoff exit target ready");
	target_pid = ready.pid;
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 78100;
	strcpy(event.payload, "handoff-exit");
	send_status = agent_wake(target_pid, &event);
	preemptions = kernel_work_last_preemptions();
	printf("agentfs_ucore: handoff_target_exit_send=%d preemptions=%d\n",
	       send_status, (int)preemptions);
	check(send_status == AGENT_STATUS_OK, "send handoff exit message");
	check(read(report_pipe[0], &replacement_pid,
		   sizeof(replacement_pid)) == (int)sizeof(replacement_pid),
	      "read handoff replacement pid");
	check(replacement_pid > 0 && replacement_pid != target_pid,
	      "handoff replacement identity");
	check(write(inspect_pipe[1], &inspect, 1) == 1,
	      "release handoff replacement inspection");
	check(read(report_pipe[0], &replacement, sizeof(replacement)) ==
		      (int)sizeof(replacement),
	      "read handoff replacement state");
	check(replacement.pid == replacement_pid,
	      "handoff replacement report identity");
	check(replacement.event_queue_count == 0,
	      "no stale handoff events");
	check(replacement.mailbox_valid == 0,
	      "no stale handoff mailbox");
	check(waitpid(churner_pid, &churner_status) == churner_pid,
	      "reap handoff churner");
	check(churner_status == 0, "handoff churner status");
	printf("agentfs_ucore: handoff_target_exit=1 endpoint_reuse=1 preemptions=%d replacement=%d clean=1\n",
	       (int)preemptions, replacement_pid);
}

static int text_contains(const char *text, const char *needle)
{
	int n = strlen(needle);

	if (n == 0)
		return 1;
	for (int i = 0; text[i]; i++) {
		if (strncmp(text + i, needle, n) == 0)
			return 1;
	}
	return 0;
}

static void check_scoped_dependency_query(void)
{
	char run042[AGENT_FAST_RESULT_SIZE];

	memset(&fs_op, 0, sizeof(fs_op));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 78001;
	strcpy(fs_op.payload, "label=align;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "dependency run042 run");
	check(fs_res.status == AGENT_STATUS_OK, "dependency run042 status");
	check(text_contains(fs_res.result, "align"), "run042 has align");
	check(text_contains(fs_res.result, "analyze"), "run042 has analyze");
	check(text_contains(fs_res.result, "report"), "run042 has report");
	check(!text_contains(fs_res.result, "archive"), "run042 no archive");
	strcpy(run042, fs_res.result);

	memset(&fs_op, 0, sizeof(fs_op));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 78002;
	strcpy(fs_op.payload, "label=align;namespace=lab-gene-x;run_id=RUN-ALT");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "dependency runalt run");
	check(fs_res.status == AGENT_STATUS_OK, "dependency runalt status");
	check(text_contains(fs_res.result, "align"), "runalt has align");
	check(text_contains(fs_res.result, "archive"), "runalt has archive");
	check(!text_contains(fs_res.result, "analyze"), "runalt no analyze");
	check(!text_contains(fs_res.result, "report"), "runalt no report");
	printf("agentfs_ucore: scoped_dependency=1 run042=%s runalt=%s\n",
	       run042, fs_res.result);
}

static void check_user_dependency_update(void)
{
	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_UPDATE;
	fs_op.request_id = 78003;
	strcpy(fs_op.payload,
	       "source=align;target=review;namespace=lab-gene-x;run_id=RUN-GEN");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "dependency update run");
	check(fs_res.status == AGENT_STATUS_OK, "dependency update status");
	check(strcmp(fs_res.result, "dependency_updated") == 0,
	      "dependency update result");

	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 78004;
	strcpy(fs_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-GEN");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "dependency update query run");
	check(fs_res.status == AGENT_STATUS_OK,
	      "dependency update query status");
	check(text_contains(fs_res.result, "align"), "dependency has source");
	check(text_contains(fs_res.result, "review"), "dependency has target");
	printf("agentfs_ucore: dependency_update=1 result=%s generation=%d\n",
	       fs_res.result, (int)fs_res.value2);
}

static void check_batched_action_maintenance(void)
{
	char dependency_text[AGENT_FAST_RESULT_SIZE];
	uint64 dependency_count;
	uint64 dependency_generation;
	uint64 dependency_mask;
	uint64 preemptions;
	int action_attempts;

	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 78005;
	strcpy(fs_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "action dependency generation query");
	check(fs_res.status == AGENT_STATUS_OK,
	      "action dependency generation status");
	dependency_mask = fs_res.value0;
	dependency_count = fs_res.value1;
	dependency_generation = fs_res.value2;
	strcpy(dependency_text, fs_res.result);

	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 1;
	strcpy(fs_meta.summary, "pre-action summary update");
	fs_meta.update_mask = AGENT_FILE_META_UPDATE_SUMMARY;
	check(metadata_set_bounded(&fs_meta) == 0,
	      "summary-only metadata update");

	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 780051;
	strcpy(fs_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "summary dependency generation query");
	check(fs_res.status == AGENT_STATUS_OK &&
	      fs_res.value2 == dependency_generation,
	      "summary update leaves dependency generation unchanged");

	memset(&fs_meta, 0, sizeof(fs_meta));
	fs_meta.fid = 21;
	fs_meta.dependency_mask = agent_dependency_label_bit("archive");
	fs_meta.update_mask = AGENT_FILE_META_UPDATE_DEPENDENCY;
	check(metadata_set_bounded(&fs_meta) == 0,
	      "topology metadata update");
	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 780052;
	strcpy(fs_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-GEN");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "topology dependency generation query");
	check(fs_res.status == AGENT_STATUS_OK &&
	      fs_res.value2 > dependency_generation,
	      "topology update advances dependency generation");
	dependency_generation = fs_res.value2;

	memset(&fs_op, 0, sizeof(fs_op));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_ACTION_COMMIT;
	fs_op.request_id = 78006;
	strcpy(fs_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	preemptions = 0;
	for (action_attempts = 1; action_attempts <= 64; action_attempts++) {
		memset(&fs_res, 0, sizeof(fs_res));
		check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
		      "batched action run");
		preemptions += kernel_work_last_preemptions();
		if (fs_res.status != AGENT_STATUS_RETRY)
			break;
		sleep(1);
	}
	check(fs_res.status == AGENT_STATUS_OK, "batched action status");

	check(query_stage("lab-gene-x", "RUN-042", "align", "ok",
			  &fs_result) >= 1,
	      "batched action primary status");
	check(strcmp(fs_result.hits[0].summary, "action completed") == 0,
	      "batched action primary summary");
	check(query_stage("lab-gene-x", "RUN-042", "analyze", "ok",
			  &fs_result) >= 1,
	      "batched action dependency status");
	check(strcmp(fs_result.hits[0].summary,
		     "dependency refreshed") == 0,
	      "batched action dependency summary");
	check(query_stage("lab-gene-x", "RUN-042", "report", "ok",
			  &fs_result) >= 1,
	      "batched action transitive status");

	memset(&fs_op, 0, sizeof(fs_op));
	memset(&fs_res, 0, sizeof(fs_res));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
	fs_op.request_id = 78007;
	strcpy(fs_op.payload,
	       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1,
	      "post-action dependency query");
	check(fs_res.status == AGENT_STATUS_OK,
	      "post-action dependency status");
	check(fs_res.value0 == dependency_mask &&
	      fs_res.value1 == dependency_count &&
	      strcmp(fs_res.result, dependency_text) == 0,
	      "non-topology action preserves dependency graph");
	check(fs_res.value2 >= dependency_generation,
	      "dependency generation remains monotonic");
	printf("agentfs_ucore: metadata_action attempts=%d preemptions=%d graph_edges=%d generation_delta=%d\n",
	       action_attempts, (int)preemptions, (int)dependency_count,
	       (int)(fs_res.value2 - dependency_generation));
}

static __attribute__((noinline)) void run_agent(void)
{
	const char *name = "fsissue4";
	uint64 metadata_query_preemptions;
	struct digest_counters digest_before;
	struct digest_counters digest_after;

	check(agent_metadata_init() == AGENT_STATUS_OK,
	      "initialize live metadata catalog");
	check_file_meta_batch_prefix();
	/* 在第一次显式 metadata 写入前创建两个普通文件探针。 */
	make_file(PRELOAD_QUERY_FILE);
	make_file(PRELOAD_PARTIAL_FILE);
	check_partial_update_binding();
	check_preload_create_query();
	check_dirent_name_boundary();
	seed_demo_metadata();
	check_selector_consistency();
	check_same_name_recreate_live();
	check_stale_identity_guard();
	seed_scoped_dependency_metadata();
	seed_user_dependency_metadata();
	check_scoped_dependency_query();
	check_user_dependency_update();
	check_batched_action_maintenance();
	check(query_stage("lab-gene-x", "RUN-042", "align", 0, &fs_result) >= 1,
	      "default query");
	metadata_query_preemptions = query_preemptions();
	check(fs_result.hits[0].dev != 0 && fs_result.hits[0].inum != 0,
	      "default inode binding");
	check(fs_result.plan == AGENT_FILE_QUERY_PLAN_STAGE_INDEX &&
	      (fs_result.plan_reason &
	       AGENT_FILE_QUERY_REASON_STAGE_INDEX) != 0,
	      "default query stage index evidence");
	check((fs_result.hits[0].dependency_mask &
	       (agent_dependency_label_bit("analyze") |
		agent_dependency_label_bit("report"))) ==
		      (agent_dependency_label_bit("analyze") |
		       agent_dependency_label_bit("report")),
	      "default query dependency metadata");
	printf("agentfs_ucore: demo_inode dev=%d inum=%d scanned=%d\n",
	       (int)fs_result.hits[0].dev, (int)fs_result.hits[0].inum,
	       fs_result.scanned_records);
	printf("agentfs_ucore: metadata_dependency_query=1 targets=2 stage_index=1 preemptions=%d uncontended=%d\n",
	       (int)metadata_query_preemptions,
	       metadata_query_preemptions == 0);
	check_handoff_target_exit();

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
	read_digest_counters(&digest_before, "digest info before");
	check_digest_text(name, "agentfs");
	check_digest_text("project=issue4;run_id=RUN-FS;stage=ingest;status=ok",
			  "agentfs");
	read_digest_counters(&digest_after, "digest info after");
	check(digest_after.hits > digest_before.hits,
	      "digest cache hit");
	check(digest_after.misses > digest_before.misses,
	      "digest cache miss");
	printf("agentfs_ucore: content_digest=1 size=%d bytes=%d hash=%d preview=%s\n",
	       (int)fs_res.value0, (int)fs_res.value1, (int)fs_res.value2,
	       fs_res.result);
	printf("agentfs_ucore: digest_cache=1 hits=%d misses=%d\n",
	       (int)(digest_after.hits - digest_before.hits),
	       (int)(digest_after.misses - digest_before.misses));
	read_digest_counters(&digest_before, "rewrite info before");
	write_file_body(name, "agentfs2");
	check_digest_text(name, "agentfs2");
	read_digest_counters(&digest_after, "rewrite info after");
	check(digest_after.misses > digest_before.misses,
	      "digest cache invalidated");
	check_digest_timeline("agentfs2");
	printf("agentfs_ucore: digest_cache_invalidated=1 misses=%d\n",
	       (int)(digest_after.misses - digest_before.misses));
	printf("agentfs_ucore: digest_timeline=1 tool=%d preview=agentfs2\n",
	       AGENT_TOOL_READ_FILE_DIGEST);

	/* 文件仍存活时解绑目录，再做同长度改写并重新绑定。旧摘要若没有按
	 * inode 身份失效，会错误命中相同 size 的缓存项。 */
	memset(&fs_meta, 0, sizeof(fs_meta));
	strcpy(fs_meta.physical_name, name);
	fs_meta.flags = AGENT_FILE_META_F_DELETE;
	check(metadata_set_bounded(&fs_meta) == 0,
	      "unbind live file metadata");
	write_file_body(name, "agentfs3");
	set_meta(name, "ok", 0);
	read_digest_counters(&digest_before, "rebind digest info before");
	check_digest_text(name, "agentfs3");
	read_digest_counters(&digest_after, "rebind digest info after");
	check(digest_after.misses > digest_before.misses,
	      "unbind invalidates digest identity");
	printf("agentfs_ucore: unbind_rebind_digest=1 same_size=1 misses=%d\n",
	       (int)(digest_after.misses - digest_before.misses));

	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) >= 1,
	      "live catalog keeps custom query");
	check(strcmp(fs_result.hits[0].physical_name, name) == 0,
	      "live catalog keeps physical name");
	printf("agentfs_ucore: live_catalog_volatile=1\n");
	check(query_stage("issue4", "RUN-FS", "ingest", "ok",
			  &fs_result) >= 1 && fs_result.used_index,
	      "repeated query executes isolated index");
	check((fs_result.plan_reason & AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0,
	      "kernel query does not share hidden result cache");
	printf("agentfs_ucore: query_execution_isolated=1 kernel_cache_hit=0 reason=%d\n",
	       (int)fs_result.plan_reason);

	check_index_scan_gap();

	set_meta(name, "", AGENT_FILE_META_UPDATE_STATUS);
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) == 0,
	      "cleared status query");
	printf("agentfs_ucore: clear_status=1 stale_query_result=0\n");

	set_meta(name, "failed", AGENT_FILE_META_UPDATE_STATUS);
	check(query_stage("issue4", "RUN-FS", "ingest", "failed",
			  &fs_result) >= 1,
	      "failed status query");
	check(unlink(name) == 0, "unlink file");
	check(query_stage("issue4", "RUN-FS", "ingest", 0, &fs_result) == 0,
	      "delete clears metadata");
	check(query_stage("issue4", "RUN-FS", "ingest", "failed",
			  &fs_result) == 0,
	      "delete invalidates cached metadata query");
	printf("agentfs_ucore: delete_clears_metadata=1\n");

	memset(&fs_op, 0, sizeof(fs_op));
	fs_op.version = AGENT_CALL_VERSION;
	fs_op.tool_id = AGENT_TOOL_ACTION_COMMIT;
	fs_op.request_id = 99001;
	strcpy(fs_op.payload, "label=align;run_id=RUN-NOPE;namespace=issue4");
	check(agent_run(&fs_op, &fs_res, 1, 0) == 1, "action missing");
	check(fs_res.status == AGENT_STATUS_NOT_FOUND, "action missing status");
	printf("agentfs_ucore: missing_selector_not_found=1\n");
	check_version_residency_reclaim();

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
