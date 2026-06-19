#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_file_meta fs_meta;
static struct agent_file_query fs_query;
static struct agent_file_query_result fs_result;
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

static void make_file(const char *name)
{
	int fd;
	char body[] = "agentfs";

	fd = open(name, O_CREATE | O_RDWR);
	check(fd >= 0, "open create");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "write file");
	check(close(fd) == 0, "close file");
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
	fs_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	check(agent_file_query(&fs_query, &index) >= 1, "bulk index query");
	check(scan.scanned_records > index.scanned_records,
	      "index scans fewer records");
	printf("agentfs_ucore: bulk_index scan=%d index=%d hits=%d\n",
	       scan.scanned_records, index.scanned_records,
	       index.total_hits);
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

	check(agent_file_meta_init() == 0, "meta reload");
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) >= 1,
	      "reload keeps custom query");
	check(strcmp(fs_result.hits[0].physical_name, name) == 0,
	      "reload keeps physical name");
	printf("agentfs_ucore: .agentmeta_reload=1\n");

	check_index_scan_gap();

	set_meta(name, "", AGENT_FILE_META_UPDATE_STATUS);
	check(query_stage("issue4", "RUN-FS", "ingest", "ok", &fs_result) == 0,
	      "cleared status query");
	printf("agentfs_ucore: clear_status=1\n");

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
