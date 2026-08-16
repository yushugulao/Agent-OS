#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_file_query live_query;
static struct agent_file_query_result live_result;
static struct agent_file_meta live_batch_items[AGENT_FILE_META_BATCH_MAX];
static int live_batch_statuses[AGENT_FILE_META_BATCH_MAX + 1];
static struct agent_op live_generic_watch_op;
static struct agent_result live_generic_watch_result;
static struct agent_info live_agent_info;

#define LIVE_BATCH_STATUS_GUARD 0x2468ace

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentscan_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int query_physical(const char *name)
{
	memset(&live_query, 0, sizeof(live_query));
	live_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(live_query.physical_name, name);
	memset(&live_result, 0, sizeof(live_result));
	return agent_file_query(&live_query, &live_result);
}

static int query_status_physical(const char *status, const char *name)
{
	memset(&live_query, 0, sizeof(live_query));
	live_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	live_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(live_query.status, status);
	strcpy(live_query.physical_name, name);
	memset(&live_result, 0, sizeof(live_result));
	return agent_file_query(&live_query, &live_result);
}

static void make_file(const char *name, const char *body)
{
	int fd = open(name, O_CREATE | O_RDWR | O_TRUNC);

	check(fd >= 0, "create file");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "write file");
	check(close(fd) == 0, "close file");
}

static void fill_explicit_meta(struct agent_file_meta *meta,
			       const char *name, const char *status,
			       uint64 update_mask)
{
	memset(meta, 0, sizeof(*meta));
	strcpy(meta->physical_name, name);
	strcpy(meta->logical_path, "/live-query/explicit");
	strcpy(meta->project, "live-query");
	strcpy(meta->workflow, "explicit-files");
	strcpy(meta->run_id, "RUN-LIVE");
	strcpy(meta->stage, "observe");
	strcpy(meta->kind, "artifact");
	strcpy(meta->status, status);
	strcpy(meta->summary, "explicit live-query object");
	meta->flags = 0;
	meta->update_mask = update_mask;
}

static uint64 wait_change(const char *change, uint64 expected_fid,
			  uint64 previous_generation)
{
	struct agent_event event;

	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 200) == AGENT_STATUS_OK,
	      "wait typed live-query event");
	check(event.type == AGENT_EVENT_FILE_QUERY,
	      "typed live-query event type");
	check(event.status == AGENT_STATUS_OK && event.corr_id == expected_fid,
	      "typed live-query event identity");
	check(event.cause_sequence > previous_generation,
	      "typed live-query event advances generation");
	check(strncmp(event.payload, change, strlen(change)) == 0,
	      "typed live-query change payload");
	return event.cause_sequence;
}

static void run_agent(void)
{
	const char *plain_name = "liveqplain";
	const char *live_name = "liveqobj";
	struct agent_file_live_watch watch;
	uint64 fid;
	uint64 generation;
	uint64 previous_generation;

	check(agent_metadata_init() == AGENT_STATUS_OK, "metadata init");
	make_file(plain_name, "ordinary-file");
	check(query_physical(plain_name) == 0,
	      "ordinary file is absent from explicit catalog");
	check(live_result.total_hits == 0 && live_result.returned == 0,
	      "ordinary file query remains empty");
	printf("agentscan_ucore: explicit_admission ordinary_unindexed=1\n");

	make_file(live_name, "live-query-body");
	memset(&live_generic_watch_op, 0, sizeof(live_generic_watch_op));
	memset(&live_generic_watch_result, 0, sizeof(live_generic_watch_result));
	live_generic_watch_op.version = AGENT_CALL_VERSION;
	live_generic_watch_op.tool_id = AGENT_TOOL_AGENT_WATCH;
	live_generic_watch_op.request_id = 62001;
	live_generic_watch_op.arg0 = AGENT_EVENT_FILE_QUERY;
	strcpy(live_generic_watch_op.payload, "typed-query-must-not-be-generic");
	check(agent_run(&live_generic_watch_op, &live_generic_watch_result, 1, 0) ==
		      1,
	      "run generic FILE_QUERY watch probe");
	check(live_generic_watch_result.status == AGENT_STATUS_BAD_PARAM,
	      "generic FILE_QUERY tool watch rejected");
	check(agent_info(&live_agent_info) == AGENT_STATUS_OK &&
	      live_agent_info.watch_count == 0,
	      "rejected generic FILE_QUERY leaves no cold watch slot");
	check(agent_watch(AGENT_EVENT_MESSAGE, "batch-generic") ==
		      AGENT_STATUS_OK,
	      "install generic watch before typed watch");
	memset(&watch, 0, sizeof(watch));
	watch.version = AGENT_FILE_LIVE_WATCH_VERSION;
	watch.query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(watch.query.physical_name, live_name);
	check(agent_live_watch(&watch) == AGENT_STATUS_OK,
	      "install typed live-query watch");
	check(watch.watch_id != 0 && watch.catalog_generation != 0,
	      "typed watch generation handshake");
	check((watch.flags & AGENT_FILE_LIVE_WATCH_F_RESYNC_REQUIRED) == 0,
	      "typed watch starts synchronized");
	check(agent_unwatch(AGENT_EVENT_NONE, 0) == 1,
	      "generic clear-all preserves typed watch");

	fill_explicit_meta(&live_batch_items[0], live_name, "ready", 0);
	fill_explicit_meta(&live_batch_items[1], live_name, "reviewed",
			   AGENT_FILE_META_UPDATE_STATUS);
	for (int i = 0; i <= AGENT_FILE_META_BATCH_MAX; i++)
		live_batch_statuses[i] = LIVE_BATCH_STATUS_GUARD;
	check(agent_file_meta_set_batch(
		live_batch_items, live_batch_statuses, 2,
		AGENT_FILE_META_BATCH_F_NONE) == 2,
	      "set ordered live-query metadata batch");
	check(live_batch_statuses[0] == AGENT_STATUS_OK &&
	      live_batch_statuses[1] == AGENT_STATUS_OK &&
	      live_batch_statuses[2] == LIVE_BATCH_STATUS_GUARD,
	      "ordered live-query batch statuses");
	check(query_status_physical("reviewed", live_name) == 1,
	      "explicit batch enters indexed query");
	check(live_result.used_index == 1 && live_result.returned == 1,
	      "explicit query uses status index");
	check(live_result.hits[0].dev != 0 && live_result.hits[0].inum != 0 &&
	      live_result.hits[0].incarnation != 0,
	      "explicit file has bound inode identity");
	check(live_result.hits[0].size == strlen("live-query-body"),
	      "explicit file projects current size");
	fid = (uint64)live_result.hits[0].fid;
	generation = live_result.fs_generation;
	previous_generation = wait_change(
		"change=ENTER", fid, watch.initial_generation);
	check(wait_change("change=UPDATE", fid, previous_generation) == generation,
	      "batched update event matches visible generation");

	previous_generation = generation;
	check(unlink(live_name) == 0, "unlink explicit file");
	generation = wait_change("change=LEAVE", fid, previous_generation);
	check(query_physical(live_name) == 0,
	      "unlink removes explicit live-query member");
	check(live_result.fs_generation == generation,
	      "leave event matches visible generation");
	check(agent_live_unwatch(&watch) == AGENT_STATUS_OK,
	      "remove typed live-query watch");
	memset(&watch, 0, sizeof(watch));
	watch.version = AGENT_FILE_LIVE_WATCH_VERSION;
	watch.query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(watch.query.physical_name, live_name);
	check(agent_live_watch(&watch) == AGENT_STATUS_OK,
	      "typed watch capacity released for reinstall");
	check(agent_live_unwatch(&watch) == AGENT_STATUS_OK,
	      "remove reinstalled typed watch");
	check(unlink(plain_name) == 0, "unlink ordinary file");
	printf("agentscan_ucore: meta_batch items=2 enter=1 update=1 leave=1 ordered=1 guard=1 generic_tool_rejected=1 generic_clear_preserved=1 typed_reinstall=1\n");
	printf("agentscan_ucore: live_query enter=1 update=1 leave=1 indexed=1\n");
	printf("agentscan_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentscan_ucore: explicit live-query file test\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_agent();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentscan_ucore: parent passed\n");
	return 0;
}
