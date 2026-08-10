#include <agent.h>
#include <exec_policy_manifest.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SECRET_FILE "vfssecret"
#define PUBLIC_FILE "vfspublic"
#define PRECREATED_FILE "vfsprecreate"
#define READER_IMAGE "vfs_reader"
#define WRITER_IMAGE "vfs_writer"
#define INHERITED_FD 3
#define SAME_BOUND_FD 4

static const char public_origin[] = "public-origin";

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentvfs_probe: check failed: %s\n", message);
		exit(1);
	}
}

static void check_unprivileged_access(const char *public_file,
				      int inherited)
{
	char byte = 0;
	struct agent_info info;
	int fd;

	check(agent_info(&info) == 0, "read plain credentials");
	check(info.filesystem_domain == 0 &&
	      info.filesystem_capability_mask == 0,
	      "plain process has no filesystem delegation");
	check(agent_worker_create("agentvfs_probe", AGENT_CAP_CONTENT_READ) ==
		      AGENT_STATUS_DENIED,
	      "deny worker delegation");
	if (inherited) {
		check(read(INHERITED_FD, &byte, 1) == -1,
		      "deny inherited read");
		check(write(INHERITED_FD, "x", 1) == -1,
		      "deny inherited write");
		check(close(INHERITED_FD) == -1,
		      "revoked inherited descriptor is absent");
		check(close(SAME_BOUND_FD) == -1,
		      "revoked same-scope descriptor is absent");
	}
	check(open(SECRET_FILE, O_RDONLY) == -1, "deny protected read open");
	check(open(SECRET_FILE, O_WRONLY) == -1,
	      "deny protected write open");
	check(open(SECRET_FILE, O_WRONLY | O_TRUNC) == -1,
	      "deny protected truncate");
	check(unlink(SECRET_FILE) == -1, "deny protected unlink");
	unlink(public_file);
	fd = open(public_file, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create public file");
	check(write(fd, "public", 7) == 7, "write public file");
	check(close(fd) == 0, "close public file");
	fd = open(public_file, O_RDONLY);
	check(fd >= 0, "read public file");
	check(read(fd, &byte, 1) == 1 && byte == 'p', "public file content");
	check(close(fd) == 0, "close public read");
	check(unlink(public_file) == 0, "unlink public file");
}

static void check_read_delegation(void)
{
	char byte = 0;
	char *argv[] = { READER_IMAGE, "--same-bound", 0 };
	struct agent_info info;
	int fd;

	check(agent_info(&info) == 0, "read delegated credentials");
	check(info.filesystem_domain != 0 &&
	      info.filesystem_capability_mask == AGENT_CAP_CONTENT_READ,
	      "exact read delegation");
	fd = open(SECRET_FILE, O_RDONLY);
	check(fd == SAME_BOUND_FD, "delegated read descriptor");
	check(read(fd, &byte, 1) == 1 && byte == 't', "delegated read");
	check(open(SECRET_FILE, O_WRONLY) == -1, "delegated write denied");
	check(open(SECRET_FILE, O_WRONLY | O_TRUNC) == -1,
	      "delegated truncate denied");
	check(unlink(SECRET_FILE) == -1, "delegated unlink denied");
	if (exec(READER_IMAGE, argv) < 0)
		exit(2);
	exit(3);
}

static void check_write_delegation_failure(void)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "read write delegation");
	check(info.filesystem_domain != 0 &&
	      info.filesystem_capability_mask == AGENT_CAP_ARTIFACT_WRITE,
	      "exact write delegation");
	check(open(PRECREATED_FILE, O_CREATE | O_RDWR | O_TRUNC) == -1,
	      "reject incomplete create intent before mutation");
	printf("agentvfs_probe: failed_open_atomic=1\n");
}

int main(int argc, char **argv)
{
	char worker_image[11];
	char *sealed_argv[] = { "agentvfs_probe", "--sealed", 0 };
	char *cross_argv[] = { "agentvfs_probe", "--cross-bound", 0 };
	int fd;

	check(argc == 2, "probe arguments");
	if (strcmp(argv[1], "--delegated-read") == 0) {
		check_read_delegation();
		return 1;
	}
	if (strcmp(argv[1], "--delegated-write-failure") == 0) {
		check_write_delegation_failure();
		return 0;
	}
	if (strcmp(argv[1], "--same-bound") == 0) {
		struct agent_info info;
		char byte = 0;

		check(agent_info(&info) == 0, "read rebound credentials");
		check(info.filesystem_domain != 0 &&
		      info.filesystem_capability_mask == AGENT_CAP_CONTENT_READ,
		      "same image retains delegation");
		check(read(SAME_BOUND_FD, &byte, 1) == 1 && byte == 'r',
		      "same-scope exec retains descriptor");
		exec_manifest_worker_image("agentvfs_probe", worker_image);
		if (exec(worker_image, cross_argv) < 0)
			exit(2);
		exit(3);
	}
	if (strcmp(argv[1], "--cross-bound") == 0) {
		check_unprivileged_access("vfspublic3", 1);
		printf("agentvfs_probe: cross_image_attenuated=1\n");
		return 0;
	}
	if (strcmp(argv[1], "--wrong-first") == 0) {
		check_unprivileged_access("vfspublic4", 1);
		printf("agentvfs_probe: wrong_first_exec_attenuated=1\n");
		return 0;
	}
	if (strcmp(argv[1], "--sealed") == 0) {
		check_unprivileged_access("vfspublic2", 1);
		printf("agentvfs_probe: sealed_exec_no_elevation=1\n");
		return 0;
	}
	check(strcmp(argv[1], "--plain") == 0, "plain probe mode");
	check_unprivileged_access(PUBLIC_FILE, 1);
	fd = open(PRECREATED_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "precreate public collision");
	check(write(fd, public_origin, sizeof(public_origin)) ==
		      sizeof(public_origin),
	      "seed public collision");
	check(close(fd) == 0, "close public collision");
	fd = open(READER_IMAGE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create public executable shadow");
	check(write(fd, "public-shadow", 14) == 14,
	      "write public executable shadow");
	check(close(fd) == 0, "close public executable shadow");
	exec_manifest_worker_image("agentvfs_probe", worker_image);
	if (exec(worker_image, sealed_argv) < 0)
		exit(2);
	exit(3);
}
