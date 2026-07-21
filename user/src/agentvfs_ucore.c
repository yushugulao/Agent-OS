#include <agent.h>
#include <exec_policy_manifest.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SECRET_FILE "vfssecret"
#define PRECREATED_FILE "vfsprecreate"
#define REVERSE_FILE "vfsreverse"
#define READER_IMAGE "vfs_reader"
#define WRITER_IMAGE "vfs_writer"
#define INHERITED_FD 3

static const char public_origin[] = "public-origin";
static const char workflow_origin[] = "workflow-origin";
static const char reverse_public[] = "reverse-public";

static struct agent_file_edit_state investigator_edit_state;
static struct agent_file_meta metadata;
static struct agent_file_query metadata_query;
static struct agent_file_query_result metadata_result;

static void check(int ok, const char *message);

static uint64 workflow_process_count(void)
{
	struct agent_op op;
	struct agent_result result;

	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_GET_SYSTEM_STATUS;
	memset(&result, 0, sizeof(result));
	check(agent_run(&op, &result, 1, 0) == 1,
	      "query workflow process count");
	check(result.status == AGENT_STATUS_OK,
	      "workflow process count authorized");
	return result.value0;
}

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentvfs_ucore: check failed: %s\n", message);
		exit(1);
	}
}

int main(void)
{
	static const char secret[] = "trusted-workflow-state";
	char actual[sizeof(secret)];
	struct agent_info info;
	char *argv[] = { "agentvfs_probe", "--plain", 0 };
	int fd;
	int pid;
	int status = -1;

	printf("agentvfs_ucore: filesystem capability test\n");
	check(agent_info(&info) == 0, "read bootstrap credentials");
	check(info.filesystem_domain != 0 &&
	      (info.filesystem_capability_mask &
	       (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)) ==
		      (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE),
	      "bootstrap filesystem delegation");
	check(agent_worker_create("agentvfs_probe", AGENT_CAP_CONTENT_READ) ==
		      AGENT_STATUS_DENIED,
	      "plain bootstrap cannot delegate workers");
	unlink(SECRET_FILE);
	fd = open(SECRET_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create protected artifact");
	check(write(fd, secret, sizeof(secret)) == sizeof(secret),
	      "write protected artifact");
	check(close(fd) == 0, "close protected artifact");
	fd = open(REVERSE_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create workflow-first twin");
	check(write(fd, workflow_origin, sizeof(workflow_origin)) ==
		      sizeof(workflow_origin),
	      "write workflow-first twin");
	check(close(fd) == 0, "close workflow-first twin");
	pid = fork();
	check(pid >= 0, "fork reverse-order creator");
	if (pid == 0) {
		char reverse_actual[sizeof(reverse_public)];

		fd = open(REVERSE_FILE, O_CREATE | O_RDWR | O_TRUNC);
		check(fd >= 0, "create public-second twin");
		check(write(fd, reverse_public, sizeof(reverse_public)) ==
			      sizeof(reverse_public),
		      "write public-second twin");
		check(close(fd) == 0, "close public-second twin");
		fd = open(REVERSE_FILE, O_RDONLY);
		check(fd >= 0, "open public-second twin");
		check(read(fd, reverse_actual, sizeof(reverse_actual)) ==
			      sizeof(reverse_actual),
		      "read public-second twin");
		check(strncmp(reverse_actual, reverse_public,
			      sizeof(reverse_public)) == 0,
		      "public-second content isolated");
		check(close(fd) == 0, "close public-second read");
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait reverse-order creator");
	check(status == 0, "reverse-order creator status");
	status = -1;
	fd = open(REVERSE_FILE, O_RDONLY);
	check(fd >= 0, "open workflow-first twin");
	memset(actual, 0, sizeof(actual));
	check(read(fd, actual, sizeof(workflow_origin)) ==
		      sizeof(workflow_origin),
	      "read workflow-first twin");
	check(strncmp(actual, workflow_origin, sizeof(workflow_origin)) == 0,
	      "workflow-first content isolated");
	check(close(fd) == 0, "close workflow-first read");

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create sentinel");
	if (pid == 0) {
		struct agent_info child_info;

		check(agent_info(&child_info) == 0, "read sentinel credentials");
		check(child_info.filesystem_capability_mask == 0,
		      "sentinel has no file capability");
		check(open(SECRET_FILE, O_RDONLY) == -1,
		      "sentinel read denied");
		check(open(SECRET_FILE, O_WRONLY) == -1,
		      "sentinel write denied");
		check(unlink(SECRET_FILE) == -1, "sentinel unlink denied");
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait sentinel");
	check(status == 0, "sentinel status");
	status = -1;

	pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
	check(pid >= 0, "create investigator");
	if (pid == 0) {
		struct agent_info child_info;
		char byte;

		check(agent_info(&child_info) == 0,
		      "read investigator credentials");
		check(child_info.filesystem_capability_mask ==
			      AGENT_CAP_CONTENT_READ,
		      "investigator is read only");
		fd = open(SECRET_FILE, O_RDONLY);
		check(fd >= 0, "investigator read open");
		check(read(fd, &byte, 1) == 1 && byte == secret[0],
		      "investigator read");
		check(close(fd) == 0, "investigator close");
		check(open(SECRET_FILE, O_WRONLY | O_TRUNC) == -1,
		      "investigator truncate denied");
		check(unlink(SECRET_FILE) == -1,
		      "investigator unlink denied");
		check(agent_file_edit_begin(SECRET_FILE, 0, 100,
				    &investigator_edit_state) ==
			      AGENT_STATUS_DENIED,
		      "investigator edit lease denied");
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait investigator");
	check(status == 0, "investigator status");
	status = -1;

	fd = open(SECRET_FILE, O_RDWR);
	check(fd == 3, "open inherited descriptor");
	pid = fork();
	check(pid >= 0, "fork attenuated child");
	if (pid == 0) {
		struct agent_info child_info;
		char byte;

		check(agent_info(&child_info) == 0,
		      "read fork child credentials");
		check(!child_info.is_agent &&
		      child_info.filesystem_capability_mask == 0,
		      "plain fork attenuated");
		check(read(fd, &byte, 1) == -1, "deny fork inherited read");
		check(write(fd, "x", 1) == -1, "deny fork inherited write");
		check(close(fd) == -1, "fork descriptor revoked");
		check(open(SECRET_FILE, O_RDONLY) == -1,
		      "deny fork protected open");
		check(open(SECRET_FILE, O_WRONLY | O_TRUNC) == -1,
		      "deny fork truncate");
		check(unlink(SECRET_FILE) == -1, "deny fork unlink");
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait attenuated child");
	check(status == 0, "attenuated child status");
	status = -1;
	pid = fork();
	check(pid >= 0, "fork plain probe");
	if (pid == 0) {
		if (exec("agentvfs_probe", argv) < 0)
			exit(2);
		exit(3);
	}
	check(waitpid(pid, &status) == pid, "wait plain probe");
	check(status == 0, "plain probe status");
	status = -1;
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create metadata orchestrator");
	if (pid == 0) {
		char worker_image[11];
		char *read_argv[] = { READER_IMAGE, "--delegated-read", 0 };
		char *write_argv[] = {
			WRITER_IMAGE, "--delegated-write-failure", 0
		};
		char *wrong_argv[] = { "agentvfs_probe", "--wrong-first", 0 };
		int worker;
		int worker_status = -1;
		int worker_gate[2];
		uint64 processes_before;
		uint64 processes_after;
		char gate = 'G';

		check(agent_worker_create(READER_IMAGE, 0) ==
			      AGENT_STATUS_BAD_PARAM,
		      "reject empty delegation");
		check(agent_worker_create(READER_IMAGE, 1ULL << 63) ==
			      AGENT_STATUS_BAD_PARAM,
		      "reject unknown delegation");
		check(agent_worker_create(READER_IMAGE,
				  AGENT_CAP_ARTIFACT_WRITE) == -1,
		      "enforce image capability ceiling");
		processes_before = workflow_process_count();
		check(pipe(worker_gate) == 0, "create pending worker gate");
		check(agent_scope_delegate_fd(worker_gate[0]) == AGENT_STATUS_OK,
		      "delegate pending worker gate");
		worker = agent_worker_create(READER_IMAGE,
					 AGENT_CAP_CONTENT_READ);
		check(worker >= 0, "create read worker");
		if (worker == 0) {
			int public_child;
			int public_status = -1;
			char received = 0;

			check(read(worker_gate[0], &received, 1) == 1 &&
				      received == 'G',
			      "pending worker waits in workflow snapshot");
			check(close(worker_gate[0]) == 0,
			      "close pending worker gate");
			public_child = fork();
			check(public_child >= 0, "fork pending worker");
			if (public_child == 0) {
				struct agent_info public_info;

				check(agent_info(&public_info) == 0,
				      "read pending-fork credentials");
				check(public_info.filesystem_domain == 0,
				      "pending worker fork drops scope");
				check(close(INHERITED_FD) == -1,
				      "pending worker fork revokes workflow fd");
				exit(0);
			}
			check(waitpid(public_child, &public_status) == public_child,
			      "wait pending worker fork");
			check(public_status == 0, "pending worker fork status");
			if (exec(READER_IMAGE, read_argv) < 0)
				exit(2);
			exit(3);
		}
		processes_after = workflow_process_count();
		check(processes_after >= processes_before + 1,
		      "pending worker appears in workflow snapshot");
		check(write(worker_gate[1], &gate, 1) == 1,
		      "release pending worker");
		check(close(worker_gate[0]) == 0 &&
			      close(worker_gate[1]) == 0,
		      "close pending worker gates");
		check(waitpid(worker, &worker_status) == worker,
		      "wait read worker");
		check(worker_status == 0, "read worker status");
		worker = agent_worker_create(WRITER_IMAGE,
					 AGENT_CAP_ARTIFACT_WRITE);
		check(worker >= 0, "create write worker");
		if (worker == 0) {
			if (exec(WRITER_IMAGE, write_argv) < 0)
				exit(2);
			exit(3);
		}
		worker_status = -1;
		check(waitpid(worker, &worker_status) == worker,
		      "wait write worker");
		check(worker_status == 0, "write worker status");
		exec_manifest_worker_image("agentvfs_probe", worker_image);
		worker = agent_worker_create(READER_IMAGE,
					 AGENT_CAP_CONTENT_READ);
		check(worker >= 0, "create wrong-image worker");
		if (worker == 0) {
			if (exec(worker_image, wrong_argv) < 0)
				exit(2);
			exit(3);
		}
		worker_status = -1;
		check(waitpid(worker, &worker_status) == worker,
		      "wait wrong-image worker");
		check(worker_status == 0, "wrong-image worker status");

		memset(&metadata, 0, sizeof(metadata));
		metadata.fid = 490;
		strcpy(metadata.physical_name, PRECREATED_FILE);
		strcpy(metadata.project, "vfs-security");
		strcpy(metadata.workflow, "policy-collision");
		strcpy(metadata.run_id, "RUN-VFS");
		strcpy(metadata.stage, "bind");
		strcpy(metadata.kind, "artifact");
		strcpy(metadata.status, "ready");
		strcpy(metadata.summary, "public collision must stay unmanaged");
		check(agent_file_meta_set(&metadata) == 0,
		      "bind workflow metadata beside public name");
		memset(&metadata_query, 0, sizeof(metadata_query));
		metadata_query.flags = AGENT_FILE_QUERY_USE_INDEX;
		metadata_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
		strcpy(metadata_query.physical_name, PRECREATED_FILE);
		check(agent_file_query(&metadata_query, &metadata_result) == 1 &&
			      metadata_result.total_hits == 1,
		      "query workflow metadata namespace");
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait metadata orchestrator");
	check(status == 0, "metadata orchestrator status");

	memset(actual, 0, sizeof(actual));
	check(read(fd, actual, sizeof(actual)) == sizeof(actual),
	      "privileged inherited descriptor");
	for (int i = 0; i < (int)sizeof(secret); i++)
		check(actual[i] == secret[i], "protected artifact unchanged");
	check(close(fd) == 0, "close inherited descriptor");
	fd = open(PRECREATED_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "replace public precreation");
	check(write(fd, "trusted", 8) == 8, "write replaced artifact");
	check(close(fd) == 0, "close replaced artifact");
	pid = fork();
	check(pid >= 0, "fork precreation verifier");
	if (pid == 0) {
		char public_actual[sizeof(public_origin)];

		fd = open(PRECREATED_FILE, O_RDONLY);
		check(fd >= 0, "open preserved public namespace");
		check(read(fd, public_actual, sizeof(public_actual)) ==
			      sizeof(public_actual),
		      "read preserved public namespace");
		check(strncmp(public_actual, public_origin,
			      sizeof(public_origin)) == 0,
		      "public namespace content preserved");
		check(close(fd) == 0, "close public namespace");
		exit(0);
	}
	status = -1;
	check(waitpid(pid, &status) == pid, "wait precreation verifier");
	check(status == 0, "precreation verifier status");
	check(unlink(PRECREATED_FILE) == 0, "remove replaced artifact");
	check(unlink(REVERSE_FILE) == 0, "remove workflow-first twin");
	pid = fork();
	check(pid >= 0, "fork public cleanup");
	if (pid == 0) {
		check(unlink(PRECREATED_FILE) == 0,
		      "remove public namespace");
		check(unlink(REVERSE_FILE) == 0,
		      "remove public-second twin");
		check(unlink(READER_IMAGE) == 0,
		      "remove public executable shadow");
		exit(0);
	}
	status = -1;
	check(waitpid(pid, &status) == pid, "wait public cleanup");
	check(status == 0, "public cleanup status");
	check(unlink(SECRET_FILE) == 0, "remove protected artifact");
	printf("agentvfs_ucore: inherited_fd_revalidated=1\n");
	printf("agentvfs_ucore: protected_paths=1\n");
	printf("agentvfs_ucore: parent passed\n");
	return 0;
}
