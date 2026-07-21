#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define BLOCK_SIZE 1024
#define BLOCK_PROBE_LIMIT 256
#define INODE_PROBE_LIMIT 64
#define VERSION_CHURN_CYCLES 640

#define BLOCK_ONE_FILE "qone"
#define BLOCK_GROW_FILE "qgrow"
#define BLOCK_FILL_FILE "qblk"
#define EXTRA_FILE "qextra"
#define WORKFLOW_FILE "qwork"
#define VERSION_CHURN_FILE "qver"

struct pressure_report {
	int blocks;
	int inodes;
	int version_churn;
};

static char block_buf[BLOCK_SIZE * 2];
static char read_buf[BLOCK_SIZE * 2];
static struct agent_info bootstrap_info;
static struct agent_info public_attacker_info;
static struct agent_info version_info_before;
static struct agent_info version_info_after;
static struct agent_file_edit_state version_state;
static struct agent_op version_op;
static struct agent_result version_result;

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("fsquota_ucore: check failed: %s\n", message);
	exit(1);
}

static int buffers_equal(const char *left, const char *right, int size)
{
	for (int i = 0; i < size; i++)
		if (left[i] != right[i])
			return 0;
	return 1;
}

static void make_inode_name(char name[11], int index)
{
	static const char hex[] = "0123456789abcdef";

	name[0] = 'q';
	name[1] = 'i';
	for (int i = 0; i < 8; i++) {
		name[9 - i] = hex[index & 15];
		index >>= 4;
	}
	name[10] = 0;
}

static int create_empty(const char *path)
{
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);

	if (fd < 0)
		return -1;
	check(close(fd) == 0, "close empty file");
	return 0;
}

static int churn_public_inode_versions(void)
{
	char byte = 'v';
	int fd;

	check(agent_info(&public_attacker_info) == 0,
	      "read public attacker credentials");
	check(public_attacker_info.filesystem_domain == 0 &&
	      (public_attacker_info.filesystem_capability_mask &
	       (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)) == 0,
	      "attacker stays in public filesystem domain");
	for (int i = 0; i < VERSION_CHURN_CYCLES; i++) {
		fd = open(VERSION_CHURN_FILE,
			  O_CREATE | O_WRONLY | O_TRUNC);
		check(fd >= 0, "create version churn inode");
		check(unlink(VERSION_CHURN_FILE) == 0,
		      "unlink open version churn inode");
		check(write(fd, &byte, 1) == 1,
		      "write unlinked version churn inode");
		check(close(fd) == 0, "reclaim version churn inode");
	}
	return VERSION_CHURN_CYCLES;
}

static void producer_fill(int report_fd)
{
	struct pressure_report report;
	char name[11];
	int fd;
	int n;
	int inode_fillers = 0;

	memset(&report, 0, sizeof(report));
	fd = open(BLOCK_ONE_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create single-block fixture");
	check(write(fd, block_buf, BLOCK_SIZE) == BLOCK_SIZE,
	      "write single-block fixture");
	check(close(fd) == 0, "close single-block fixture");
	report.blocks = 1;

	check(create_empty(BLOCK_GROW_FILE) == 0, "create growth fixture");
	fd = open(BLOCK_FILL_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create block filler");
	for (int i = 0; i < BLOCK_PROBE_LIMIT; i++) {
		n = write(fd, block_buf, BLOCK_SIZE);
		if (n < 0)
			break;
		check(n == BLOCK_SIZE, "block pressure write is complete");
		report.blocks++;
	}
	check(report.blocks > 1 && report.blocks < BLOCK_PROBE_LIMIT,
	      "block allocation reaches a bounded denial");
	check(write(fd, block_buf, BLOCK_SIZE) == -1,
	      "block denial remains stable");
	check(close(fd) == 0, "close block filler");

	for (; inode_fillers < INODE_PROBE_LIMIT; inode_fillers++) {
		make_inode_name(name, inode_fillers);
		if (create_empty(name) < 0)
			break;
	}
	report.inodes = inode_fillers + 3;
	check(inode_fillers > 0 && inode_fillers < INODE_PROBE_LIMIT,
	      "inode allocation reaches a bounded denial");
	make_inode_name(name, inode_fillers);
	check(open(name, O_CREATE | O_WRONLY) == -1,
	      "inode denial remains stable");
	check(write(report_fd, &report, sizeof(report)) == sizeof(report),
	      "publish pressure report");
	check(close(report_fd) == 0, "close producer report");
	exit(0);
}

static void cleanup_after_reuse(int inode_fillers)
{
	char name[11];

	for (int i = 1; i < inode_fillers; i++) {
		make_inode_name(name, i);
		check(unlink(name) == 0, "remove inode filler");
	}
	check(unlink(EXTRA_FILE) == 0, "remove reused inode");
	check(unlink(BLOCK_ONE_FILE) == 0, "remove released block file");
	check(unlink(BLOCK_GROW_FILE) == 0, "remove reused block file");
	check(unlink(BLOCK_FILL_FILE) == 0, "remove block filler");
}

static void cleanup_without_reuse(int inode_fillers)
{
	char name[11];

	for (int i = 0; i < inode_fillers; i++) {
		make_inode_name(name, i);
		check(unlink(name) == 0, "remove reserved-watermark inode");
	}
	check(unlink(BLOCK_ONE_FILE) == 0,
	      "remove reserved-watermark single block");
	check(unlink(BLOCK_GROW_FILE) == 0,
	      "remove reserved-watermark growth file");
	check(unlink(BLOCK_FILL_FILE) == 0,
	      "remove reserved-watermark block filler");
}

static void run_public_attacker(int ready_fd, int control_fd)
{
	struct pressure_report report;
	int producer_pipe[2];
	char token = 0;
	char name[11];
	int producer;
	int status = -1;
	int fd;
	int version_churn;

	version_churn = churn_public_inode_versions();
	check(pipe(producer_pipe) == 0, "create producer report pipe");
	producer = fork();
	check(producer >= 0, "create pressure producer");
	if (producer == 0) {
		close(producer_pipe[0]);
		close(ready_fd);
		close(control_fd);
		producer_fill(producer_pipe[1]);
	}
	check(close(producer_pipe[1]) == 0, "close producer report writer");
	check(read(producer_pipe[0], &report, sizeof(report)) == sizeof(report),
	      "receive producer report");
	check(close(producer_pipe[0]) == 0, "close producer report reader");
	check(waitpid(producer, &status) == producer && status == 0,
	      "reap pressure producer");
	report.version_churn = version_churn;

	fd = open(BLOCK_GROW_FILE, O_WRONLY);
	check(fd >= 0, "open post-exit growth fixture");
	check(write(fd, block_buf, BLOCK_SIZE) == -1,
	      "block charge survives producer exit");
	check(close(fd) == 0, "close denied growth fixture");
	check(open(EXTRA_FILE, O_CREATE | O_WRONLY) == -1,
	      "inode charge survives producer exit");
	check(write(ready_fd, &report, sizeof(report)) == sizeof(report),
	      "publish persistent pressure");
	check(read(control_fd, &token, 1) == 1 &&
	      (token == 'R' || token == 'C'),
	      "receive pressure release command");

	if (token == 'R') {
		fd = open(BLOCK_ONE_FILE, O_WRONLY | O_TRUNC);
		check(fd >= 0 && close(fd) == 0,
		      "release one charged block");
		fd = open(BLOCK_GROW_FILE, O_WRONLY);
		check(fd >= 0, "open block reuse fixture");
		check(write(fd, block_buf, BLOCK_SIZE) == BLOCK_SIZE,
		      "reuse released block charge");
		check(close(fd) == 0, "close block reuse fixture");

		make_inode_name(name, 0);
		check(unlink(name) == 0, "release one charged inode");
		check(create_empty(EXTRA_FILE) == 0,
		      "reuse released inode charge");
		cleanup_after_reuse(report.inodes - 3);
	} else {
		cleanup_without_reuse(report.inodes - 3);
	}
	token = 'D';
	check(write(ready_fd, &token, 1) == 1, "publish pressure cleanup");
	check(close(ready_fd) == 0, "close attacker report writer");
	check(close(control_fd) == 0, "close attacker control reader");
	exit(0);
}

static void verify_workflow_versions(void)
{
	uint64 base;
	int fd;

	memset(&version_state, 0, sizeof(version_state));
	check(agent_file_edit_begin(WORKFLOW_FILE, 0, 100, &version_state) == 0,
	      "begin workflow edit after public churn");
	base = version_state.base_version;
	fd = open(WORKFLOW_FILE, O_WRONLY);
	check(fd >= 0, "open workflow edit target");
	check(write(fd, "z", 1) == 1, "write workflow edit target");
	check(close(fd) == 0, "close workflow edit target");
	check(agent_file_edit_commit(version_state.lease_id, base,
				     &version_state) == 0,
	      "commit workflow edit after public churn");
	check(version_state.current_version == base + 1,
	      "workflow edit version advances");
	printf("fsquota_ucore: workflow_version_reserve=1\n");

	check(agent_info(&version_info_before) == 0,
	      "read digest counters before");
	for (int i = 0; i < 2; i++) {
		memset(&version_op, 0, sizeof(version_op));
		memset(&version_result, 0, sizeof(version_result));
		version_op.version = AGENT_CALL_VERSION;
		version_op.tool_id = AGENT_TOOL_READ_FILE_DIGEST;
		version_op.request_id = 9000 + i;
		strcpy(version_op.payload, WORKFLOW_FILE);
		check(agent_run(&version_op, &version_result, 1, 0) == 1,
		      "read workflow digest after public churn");
		check(version_result.status == AGENT_STATUS_OK,
		      "workflow digest status");
	}
	check(agent_info(&version_info_after) == 0,
	      "read digest counters after");
	check(version_info_after.file_digest_cache_hits >
		      version_info_before.file_digest_cache_hits,
	      "content version cache survives public churn");
	printf("fsquota_ucore: content_version_reserve=1\n");
}

static void verify_workflow_metadata(void)
{
	struct agent_file_query query;
	struct agent_file_query_result result;
	int agent;
	int status = -1;

	agent = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(agent >= 0, "create metadata orchestrator");
	if (agent == 0) {
		check(agent_file_meta_init() == 0, "reload persistent metadata");
		verify_workflow_versions();
		memset(&query, 0, sizeof(query));
		memset(&result, 0, sizeof(result));
		query.flags = AGENT_FILE_QUERY_USE_INDEX;
		query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
		strcpy(query.physical_name, WORKFLOW_FILE);
		check(agent_file_query(&query, &result) == 1,
		      "query reloaded workflow metadata");
		check(result.total_hits == 1 && result.returned == 1,
		      "single workflow metadata record");
		check(result.hits[0].size == sizeof(block_buf),
		      "persisted workflow size");
		exit(0);
	}
	check(waitpid(agent, &status) == agent && status == 0,
	      "reap metadata orchestrator");
}

static void verify_workflow_storage(void)
{
	int fd;

	fd = open(WORKFLOW_FILE, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "create workflow reserve file");
	check(write(fd, block_buf, sizeof(block_buf)) == sizeof(block_buf),
	      "write workflow reserve blocks");
	check(close(fd) == 0, "close workflow reserve file");
	fd = open(WORKFLOW_FILE, O_RDONLY);
	check(fd >= 0, "reopen workflow reserve file");
	memset(read_buf, 0, sizeof(read_buf));
	check(read(fd, read_buf, sizeof(read_buf)) == sizeof(read_buf),
	      "read workflow reserve blocks");
	check(buffers_equal(read_buf, block_buf, sizeof(block_buf)),
	      "workflow reserve contents");
	check(close(fd) == 0, "close workflow reserve reader");
}

int main(void)
{
	struct pressure_report report;
	int ready_pipe[2];
	int control_pipe[2];
	char token = 0;
	int domain_boundary;
	int attacker;
	int status = -1;

	memset(block_buf, 0x71, sizeof(block_buf));
	check(agent_info(&bootstrap_info) == 0, "read bootstrap credentials");
	check(bootstrap_info.filesystem_domain != 0 &&
	      (bootstrap_info.filesystem_capability_mask &
	       (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)) ==
		      (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE),
	      "trusted workflow bootstrap");
	check(pipe(ready_pipe) == 0, "create attacker report pipe");
	check(pipe(control_pipe) == 0, "create attacker control pipe");
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate attacker report pipe");
	check(agent_scope_delegate_fd(control_pipe[0]) == AGENT_STATUS_OK,
	      "delegate attacker control pipe");
	attacker = fork();
	check(attacker >= 0, "create public attacker domain");
	if (attacker == 0) {
		close(ready_pipe[0]);
		close(control_pipe[1]);
		run_public_attacker(ready_pipe[1], control_pipe[0]);
	}
	check(close(ready_pipe[1]) == 0, "close attacker report writer");
	check(close(control_pipe[0]) == 0, "close attacker control reader");
	check(read(ready_pipe[0], &report, sizeof(report)) == sizeof(report),
	      "wait for persistent public pressure");
	check(report.blocks > 1 && report.blocks < BLOCK_PROBE_LIMIT &&
	      report.inodes > 3 && report.inodes < INODE_PROBE_LIMIT + 3,
	      "validate pressure report");
	check(report.version_churn == VERSION_CHURN_CYCLES,
	      "validate public version churn report");
	printf("fsquota_ucore: public_version_churn=1 cycles=%d\n",
	       report.version_churn);
	printf("fsquota_ucore: public_domain_limited=1 blocks=%d inodes=%d\n",
	       report.blocks, report.inodes);
	printf("fsquota_ucore: post_exit_accounting=1\n");
	domain_boundary = report.blocks <= 16 && report.inodes <= 8;

	verify_workflow_storage();
	printf("fsquota_ucore: workflow_reserve=1\n");
	verify_workflow_metadata();
	printf("fsquota_ucore: kernel_metadata_reserve=1\n");

	token = domain_boundary ? 'R' : 'C';
	check(write(control_pipe[1], &token, 1) == 1,
	      "release public pressure");
	check(close(control_pipe[1]) == 0, "close attacker control writer");
	check(read(ready_pipe[0], &token, 1) == 1 && token == 'D',
	      "wait for pressure cleanup");
	check(close(ready_pipe[0]) == 0, "close attacker report reader");
	check(waitpid(attacker, &status) == attacker && status == 0,
	      "reap public attacker");
	printf("fsquota_ucore: pressure_cleanup=1\n");
	if (domain_boundary)
		printf("fsquota_ucore: quota_reuse=1\n");
	check(unlink(WORKFLOW_FILE) == 0, "remove workflow reserve file");
	printf("fsquota_ucore: parent passed\n");
	return 0;
}
