#include <agent.h>
#include <agent_nexus.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

#define PUBLISH_HEADER_SIZE 32U
#define PUBLISH_PAYLOAD_SIZE 96U

static const char race_path[] = "pubrace";
static const char bad_pointer_path[] = "pubbadptr";
static const char bad_payload_path[] = "pubbadpay";
static const char bad_slash_path[] = "pub/bad";
static const char bad_long_path[] = "123456789012345";
static const char bad_size_path[] = "pubbadsize";
static const char warmup_path[] = "pubwarm";
static const char warmup_second_path[] = "pubwarm2";
static unsigned char race_header[2][PUBLISH_HEADER_SIZE];
static unsigned char race_payload[2][PUBLISH_PAYLOAD_SIZE];
static unsigned char file_image[AGENT_FILE_PUBLISH_MAX_BYTES + 1U];
static unsigned char nexus_payload[48];
static unsigned char nexus_changed_payload[48];
static struct agent_resource_snapshot invalid_before;
static struct agent_resource_snapshot invalid_after;
static struct agent_resource_snapshot race_before;
static struct agent_resource_snapshot race_after;
static struct agent_resource_snapshot duplicate_after;
static struct agent_resource_snapshot cleanup_after;
static struct agent_workflow_lifecycle_info lifecycle;
static struct agent_nexus_artifact_actor nexus_actor;
static struct agent_nexus_artifact_manifest nexus_manifest;
static struct agent_nexus_artifact_header nexus_first;
static struct agent_nexus_artifact_header nexus_second;
static struct agent_nexus_artifact_header nexus_read_header;
static unsigned char nexus_read_payload[sizeof(nexus_payload)];
static int snapshot_request_fd = -1;
static int snapshot_ack_fd = -1;

static void fail(const char *message)
{
	printf("agentpublish_ucore: check failed: %s\n", message);
	exit(1);
}

static void check(int ok, const char *message)
{
	if (!ok)
		fail(message);
}

static int bytes_equal(const void *left, const void *right, unsigned int size)
{
	const unsigned char *a = left;
	const unsigned char *b = right;

	for (unsigned int i = 0; i < size; i++)
		if (a[i] != b[i])
			return 0;
	return 1;
}

static void fill_pattern(unsigned char *bytes, unsigned int size,
			 unsigned int seed)
{
	for (unsigned int i = 0; i < size; i++)
		bytes[i] = (unsigned char)(seed + i * 29U + (i >> 1));
}

static void expect_absent(const char *path, const char *message)
{
	int fd = open(path, O_RDONLY);

	if (fd >= 0) {
		(void)close(fd);
		fail(message);
	}
}

static void expect_exact_file(const char *path, const void *header,
			      unsigned int header_size, const void *payload,
			      unsigned int payload_size, const char *message)
{
	unsigned int total = header_size + payload_size;
	char tail;
	int fd;
	int got;

	check(total <= sizeof(file_image), "exact image bound");
	fd = open(path, O_RDONLY);
	check(fd >= 0, message);
	for (unsigned int offset = 0; offset < total;) {
		got = read(fd, file_image + offset, total - offset);
		check(got > 0, message);
		offset += (unsigned int)got;
	}
	check(read(fd, &tail, 1) == 0, "official file has exact EOF");
	check(close(fd) == 0, "close exact file");
	check(bytes_equal(file_image, header, header_size) &&
	      bytes_equal(file_image + header_size, payload, payload_size),
	      message);
}

static void snapshot(struct agent_resource_snapshot *value,
		     const char *message)
{
	memset(value, 0, sizeof(*value));
	value->version = AGENT_RESOURCE_SNAPSHOT_VERSION;
	value->struct_size = sizeof(*value);
	check(agent_resource_snapshot(value) == AGENT_STATUS_OK &&
	      value->kind_count == AGENT_RESOURCE_KIND_COUNT &&
	      (value->measured_mask &
	       ((1U << AGENT_RESOURCE_FS_BLOCK) |
		(1U << AGENT_RESOURCE_FS_INODE) |
		(1U << AGENT_RESOURCE_PHYSICAL_PAGE))) ==
		      ((1U << AGENT_RESOURCE_FS_BLOCK) |
		       (1U << AGENT_RESOURCE_FS_INODE) |
		       (1U << AGENT_RESOURCE_PHYSICAL_PAGE)),
	      message);
}

static void request_snapshot(char stage)
{
	char ack;

	check(write(snapshot_request_fd, &stage, 1) == 1,
	      "request resource snapshot");
	check(read(snapshot_ack_fd, &ack, 1) == 1 && ack == stage,
	      "wait resource snapshot");
}

static void expect_kind_equal(const struct agent_resource_snapshot *left,
			      const struct agent_resource_snapshot *right,
			      unsigned int kind, const char *message)
{
	check(left->kinds[kind].used == right->kinds[kind].used &&
	      left->kinds[kind].pending == right->kinds[kind].pending &&
	      left->kinds[kind].ordinary_used ==
		      right->kinds[kind].ordinary_used &&
	      left->kinds[kind].ordinary_pending ==
		      right->kinds[kind].ordinary_pending &&
	      left->kinds[kind].reserved_used ==
		      right->kinds[kind].reserved_used &&
	      left->kinds[kind].reserved_pending ==
		      right->kinds[kind].reserved_pending,
	      message);
}

static int raw_publish(struct agent_file_publish_request *request)
{
	return syscall(SYS_agent_file_publish, request);
}

static void exercise_invalid_requests(void)
{
	struct agent_file_publish_request request;
	unsigned char byte = 0x5a;

	expect_absent(bad_pointer_path, "bad pointer path precondition");
	expect_absent(bad_payload_path, "bad payload path precondition");
	expect_absent(bad_size_path, "bad size path precondition");
	request_snapshot('A');
	check(syscall(SYS_agent_file_publish, (void *)1) ==
		      AGENT_STATUS_BAD_PARAM,
	      "reject bad request pointer");
	check(agent_file_publish(bad_pointer_path, (void *)1, 1, &byte, 1) ==
		      AGENT_STATUS_BAD_PARAM,
	      "reject bad header pointer");
	check(agent_file_publish(bad_payload_path, &byte, 1, (void *)1, 1) ==
		      AGENT_STATUS_BAD_PARAM,
	      "reject bad payload pointer");
	memset(&request, 0, sizeof(request));
	request.version = AGENT_FILE_PUBLISH_VERSION;
	request.size = sizeof(request);
	request.path = 1;
	request.header = (unsigned long long)&byte;
	request.payload = (unsigned long long)&byte;
	request.header_size = 1;
	request.payload_size = 1;
	check(raw_publish(&request) == AGENT_STATUS_BAD_PARAM,
	      "reject bad path pointer");
	request.path = (unsigned long long)bad_pointer_path;
	request.version = AGENT_FILE_PUBLISH_VERSION + 1U;
	check(raw_publish(&request) == AGENT_STATUS_BAD_VERSION,
	      "reject bad publish ABI version");
	request.version = AGENT_FILE_PUBLISH_VERSION;
	request.size = sizeof(request) - 8U;
	check(raw_publish(&request) == AGENT_STATUS_BAD_SIZE,
	      "reject short publish ABI request");
	request.size = sizeof(request);
	request.reserved = 1;
	check(raw_publish(&request) == AGENT_STATUS_BAD_PARAM,
	      "reject nonzero publish reserved field");
	check(agent_file_publish(bad_slash_path, &byte, 1, &byte, 1) ==
		      AGENT_STATUS_BAD_PARAM,
	      "reject slash in official name");
	check(agent_file_publish(bad_long_path, &byte, 1, &byte, 1) ==
		      AGENT_STATUS_BAD_PARAM,
	      "reject overlong official name");
	check(agent_file_publish(bad_size_path, &byte, 1, &byte,
				 AGENT_FILE_PUBLISH_MAX_BYTES) ==
		      AGENT_STATUS_BAD_SIZE,
	      "reject oversized complete image");
	expect_absent(bad_pointer_path, "bad header pointer named a file");
	expect_absent(bad_payload_path, "bad payload pointer named a file");
	expect_absent(bad_size_path, "bad size named a file");
	request_snapshot('B');
	printf("agentpublish_ucore: invalid_requests=1 bad_pointer=1 "
	       "bad_path=1 bad_size=1 bad_abi=1 "
	       "zero_namespace_side_effect=1\n");
}

static void warm_publish_slot(void)
{
	unsigned char header = 0x41;
	unsigned char payload = 0x72;

	(void)unlink(warmup_path);
	(void)unlink(warmup_second_path);
	check(agent_file_publish(warmup_path, &header, 1, &payload, 1) ==
		      AGENT_STATUS_OK,
	      "warm first namespace slot");
	check(agent_file_publish(warmup_second_path, &header, 1, &payload, 1) ==
		      AGENT_STATUS_OK,
	      "warm second namespace slot");
	check(unlink(warmup_path) == 0 && unlink(warmup_second_path) == 0,
	      "reclaim warm namespace slots");
}

static void race_child(unsigned int index, int gate_fd)
{
	char token;
	int status;

	if (read(gate_fd, &token, 1) != 1)
		exit(3);
	status = agent_file_publish(race_path, race_header[index],
				    sizeof(race_header[index]), race_payload[index],
				    sizeof(race_payload[index]));
	if (status == AGENT_STATUS_OK)
		exit(0);
	if (status == AGENT_STATUS_DUPLICATE)
		exit(1);
	exit(2);
}

static unsigned int exercise_same_name_race(void)
{
	int gate[2];
	int children[2];
	int status;
	unsigned int ok_count = 0;
	unsigned int duplicate_count = 0;
	unsigned int winner = 2;
	char release[2] = { 'A', 'B' };

	warm_publish_slot();
	expect_absent(race_path, "race path precondition");
	request_snapshot('C');
	check(pipe(gate) == 0, "create race gate");
	for (unsigned int i = 0; i < 2; i++) {
		check(agent_scope_delegate_fd(gate[0]) == AGENT_STATUS_OK,
		      "delegate race gate");
		children[i] = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
		check(children[i] >= 0, "create same-scope publisher");
		if (children[i] == 0)
			race_child(i, gate[0]);
	}
	check(write(gate[1], release, sizeof(release)) == sizeof(release),
	      "release same-scope publishers");
	for (unsigned int i = 0; i < 2; i++) {
		status = -1;
		check(waitpid(children[i], &status) == children[i],
		      "wait same-scope publisher");
		if (status == 0) {
			ok_count++;
			winner = i;
		} else if (status == 1) {
			duplicate_count++;
		} else {
			fail("unexpected race publisher status");
		}
	}
	check(close(gate[0]) == 0 && close(gate[1]) == 0,
	      "close race gate");
	check(ok_count == 1 && duplicate_count == 1 && winner < 2,
	      "exactly one same-scope publisher wins");
	expect_exact_file(race_path, race_header[winner],
			  sizeof(race_header[winner]), race_payload[winner],
			  sizeof(race_payload[winner]),
			  "race winner complete image");
	request_snapshot('D');
	check(agent_file_publish(race_path, race_header[1U - winner],
				 sizeof(race_header[0]),
				 race_payload[1U - winner],
				 sizeof(race_payload[0])) ==
		      AGENT_STATUS_DUPLICATE,
	      "different bytes cannot overwrite winner");
	expect_exact_file(race_path, race_header[winner],
			  sizeof(race_header[winner]), race_payload[winner],
			  sizeof(race_payload[winner]),
			  "duplicate changed race winner");
	request_snapshot('E');
	printf("agentpublish_ucore: publish_image=1 header=32 payload=96 eof=1\n");
	printf("agentpublish_ucore: same_scope_race=1 ok=1 duplicate=1 "
	       "no_overwrite=1\n");
	return winner;
}

static void exercise_nexus_convergence(void)
{
	char path[AGENT_NEXUS_ARTIFACT_PATH_SIZE];
	unsigned int read_size = 0;
	unsigned int handle;

	memset(&lifecycle, 0, sizeof(lifecycle));
	lifecycle.version = AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION;
	lifecycle.struct_size = sizeof(lifecycle);
	check(agent_workflow_lifecycle_info(&lifecycle, 0) == AGENT_STATUS_OK &&
	      lifecycle.key.id != 0 && lifecycle.key.generation != 0,
	      "read Nexus workflow lifecycle");
	check(agent_nexus_identity_registry_init(0x5042554cU) == 0 &&
	      agent_nexus_identity_register(AGENT_NEXUS_ROLE_COORDINATOR,
				    0x5042554cU) == 0 &&
	      agent_nexus_identity_current(&nexus_actor) == 0,
	      "register Nexus test actor");
	handle = agent_nexus_artifact_handle_make(lifecycle.key.generation, 31);
	check(handle != 0 && agent_nexus_artifact_path(handle, path) == 0,
	      "make Nexus convergence path");
	memset(&nexus_manifest, 0, sizeof(nexus_manifest));
	nexus_manifest.lifecycle = lifecycle.key;
	nexus_manifest.handle = handle;
	nexus_manifest.flags = AGENT_NEXUS_ARTIFACT_F_PUBLISHED;
	nexus_manifest.producer = nexus_actor;
	nexus_manifest.owner = nexus_actor;
	nexus_manifest.materializer = nexus_actor;
	nexus_manifest.task_id = 31001;
	nexus_manifest.kind = AGENT_NEXUS_ARTIFACT_TOOL_INPUT;
	nexus_manifest.source = AGENT_NEXUS_SOURCE_USER;
	nexus_manifest.provenance_labels =
		AGENT_PROVENANCE_TRUSTED_USER_CONTROL;
	nexus_manifest.permission_mask =
		agent_nexus_product_permission(AGENT_NEXUS_ROLE_COORDINATOR);
	check(agent_nexus_artifact_publish_owned(
		      &nexus_manifest, nexus_payload, sizeof(nexus_payload),
		      &nexus_first) == 0,
	      "publish first Nexus artifact");
	check(agent_nexus_artifact_publish_owned(
		      &nexus_manifest, nexus_payload, sizeof(nexus_payload),
		      &nexus_second) == 0 &&
	      bytes_equal(&nexus_first, &nexus_second, sizeof(nexus_first)),
	      "same Nexus bytes converge through readback");
	check(agent_nexus_artifact_publish_owned(
		      &nexus_manifest, nexus_changed_payload,
		      sizeof(nexus_changed_payload), &nexus_second) < 0,
	      "different Nexus bytes do not converge");
	check(agent_nexus_artifact_read_verify(
		      handle, &lifecycle.key, &nexus_actor,
		      AGENT_NEXUS_ARTIFACT_TOOL_INPUT, &nexus_read_header,
		      nexus_read_payload, sizeof(nexus_read_payload),
		      &read_size) == 0 &&
	      read_size == sizeof(nexus_payload) &&
	      bytes_equal(&nexus_read_header, &nexus_first,
			  sizeof(nexus_first)) &&
	      bytes_equal(nexus_read_payload, nexus_payload,
			  sizeof(nexus_payload)),
	      "Nexus official path retains exact first image");
	expect_exact_file(path, &nexus_first, sizeof(nexus_first), nexus_payload,
			  sizeof(nexus_payload), "Nexus exact header payload EOF");
	check(unlink(path) == 0, "remove Nexus convergence artifact");
	printf("agentpublish_ucore: nexus_duplicate=1 exact_readback=1 "
	       "mismatch_rejected=1\n");
}

static void run_workflow(void)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.is_agent == 1 &&
	      info.agent_role == AGENT_ROLE_ORCHESTRATOR &&
	      (info.filesystem_capability_mask & AGENT_CAP_ARTIFACT_WRITE) != 0,
	      "workflow publisher identity");
	for (unsigned int i = 0; i < 2; i++) {
		fill_pattern(race_header[i], sizeof(race_header[i]),
			     0x21U + i * 0x40U);
		fill_pattern(race_payload[i], sizeof(race_payload[i]),
			     0x37U + i * 0x50U);
	}
	fill_pattern(nexus_payload, sizeof(nexus_payload), 0x49U);
	memcpy(nexus_changed_payload, nexus_payload, sizeof(nexus_payload));
	nexus_changed_payload[sizeof(nexus_changed_payload) / 2U] ^= 0x5aU;
	exercise_invalid_requests();
	(void)exercise_same_name_race();
	exercise_nexus_convergence();
	check(unlink(race_path) == 0, "remove race winner");
	request_snapshot('F');
	return;
}

int main(void)
{
	int pid;
	int status = -1;
	int request_pipe[2];
	int ack_pipe[2];
	char stage;
	static const char expected_stages[] = { 'A', 'B', 'C', 'D', 'E', 'F' };

	printf("agentpublish_ucore: atomic publish Guest\n");
	check(pipe(request_pipe) == 0 && pipe(ack_pipe) == 0,
	      "create resource snapshot pipes");
	check(agent_scope_delegate_fd(request_pipe[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(ack_pipe[0]) == AGENT_STATUS_OK,
	      "delegate resource snapshot pipes");
	pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create isolated publish workflow");
	if (pid == 0) {
		snapshot_request_fd = request_pipe[1];
		snapshot_ack_fd = ack_pipe[0];
		run_workflow();
		exit(0);
	}
	check(close(request_pipe[1]) == 0 && close(ack_pipe[0]) == 0,
	      "close delegated snapshot pipe ends");
	for (unsigned int i = 0; i < sizeof(expected_stages); i++) {
		check(read(request_pipe[0], &stage, 1) == 1 &&
		      stage == expected_stages[i],
		      "ordered resource snapshot request");
		if (stage == 'A')
			snapshot(&invalid_before, "invalid request baseline");
		else if (stage == 'B')
			snapshot(&invalid_after, "invalid request final snapshot");
		else if (stage == 'C')
			snapshot(&race_before, "race baseline");
		else if (stage == 'D')
			snapshot(&race_after, "race result snapshot");
		else if (stage == 'E')
			snapshot(&duplicate_after, "duplicate result snapshot");
		else
			snapshot(&cleanup_after, "publish cleanup snapshot");
		check(write(ack_pipe[1], &stage, 1) == 1,
		      "acknowledge resource snapshot");
	}
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait isolated publish workflow");
	check(close(request_pipe[0]) == 0 && close(ack_pipe[1]) == 0,
	      "close resource snapshot pipes");
	expect_kind_equal(&invalid_before, &invalid_after,
			  AGENT_RESOURCE_FS_BLOCK,
			  "invalid requests leaked blocks");
	expect_kind_equal(&invalid_before, &invalid_after,
			  AGENT_RESOURCE_FS_INODE,
			  "invalid requests leaked inodes");
	expect_kind_equal(&invalid_before, &invalid_after,
			  AGENT_RESOURCE_PHYSICAL_PAGE,
			  "invalid requests leaked snapshot pages");
	expect_kind_equal(&race_after, &duplicate_after,
			  AGENT_RESOURCE_FS_BLOCK,
			  "duplicate leaked blocks");
	expect_kind_equal(&race_after, &duplicate_after,
			  AGENT_RESOURCE_FS_INODE,
			  "duplicate leaked inodes");
	expect_kind_equal(&race_before, &cleanup_after,
			  AGENT_RESOURCE_FS_BLOCK,
			  "publish cleanup did not reclaim blocks");
	expect_kind_equal(&race_before, &cleanup_after,
			  AGENT_RESOURCE_FS_INODE,
			  "publish cleanup did not reclaim inodes");
	printf("agentpublish_ucore: resources=1 invalid_no_leak=1 "
	       "duplicate_no_leak=1 unlink_reclaimed=1\n");
	printf("agentpublish_ucore: parent passed\n");
	return 0;
}
