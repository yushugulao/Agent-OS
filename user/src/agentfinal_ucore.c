#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_op ops[AGENT_BATCH_MAX];
static struct agent_result results[AGENT_BATCH_MAX];
static struct agent_context_record records[AGENT_CONTEXT_MAX_RECORDS];

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentfinal_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void make_echo(struct agent_op *op, uint64 id, const char *text)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = id;
	op->arg0 = id;
	op->arg1 = id + 1;
	strcpy(op->payload, text);
}

static void run_agent_child(void)
{
	struct agent_info info;
	struct agent_context_header header;
	struct agent_context_header *direct_header;
	struct agent_result *latest;
	struct agent_file_query q;
	struct agent_file_query_result qr;
	struct agent_event event;
	int n;

	check(agent_info(&info) == 0, "agent_info");
	check(info.is_agent == 1, "is agent");
	check(info.agent_role == AGENT_ROLE_ORCHESTRATOR, "orchestrator role");
	check((info.capability_mask & AGENT_CAP_META_WRITE) != 0,
	      "meta write cap");
	check((info.capability_mask & AGENT_CAP_ORCHESTRATE) != 0,
	      "orchestrate cap");
	check(info.context_base == AGENT_CONTEXT_BASE, "context base");
	check(info.context_size == AGENT_CONTEXT_SIZE, "context size");
	direct_header = (struct agent_context_header *)info.context_base;
	latest = (struct agent_result *)(info.context_base +
					 info.latest_response_offset);
	check(direct_header->magic == AGENT_CONTEXT_MAGIC, "context magic");
	printf("agentfinal_ucore: context size=%d capacity=%d\n",
	       (int)info.context_size, (int)direct_header->capacity);

	check(context_clear() == 0, "context clear");
	for (int i = 0; i < AGENT_BATCH_MAX; i++)
		make_echo(&ops[i], i + 1, i == 7 ? "ucore-final" : "final");
	check(agent_run(ops, results, AGENT_BATCH_MAX, 0) == AGENT_BATCH_MAX,
	      "agent_run batch");
	check(results[0].sequence == 1, "first sequence");
	check(results[AGENT_BATCH_MAX - 1].sequence == AGENT_BATCH_MAX,
	      "last sequence");
	check(latest->sequence == AGENT_BATCH_MAX, "latest direct");
	printf("agentfinal_ucore: batch first_seq=%d last_seq=%d\n",
	       (int)results[0].sequence,
	       (int)results[AGENT_BATCH_MAX - 1].sequence);

	n = context_snapshot(&header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot count");
	check(header.latest_sequence == AGENT_BATCH_MAX, "snapshot latest");
	check(strcmp(records[7].payload, "ucore-final") == 0,
	      "short payload");
	check(strcmp(records[7].result, "ucore-final") == 0, "short result");
	printf("agentfinal_ucore: short_text_history=1 payload=%s result=%s\n",
	       records[7].payload, records[7].result);

	records[0].sequence = 9999;
	((struct agent_context_record *)(info.context_base +
					 info.records_offset))[0]
		.sequence = 9999;
	n = context_snapshot(&header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot after tamper");
	check(records[0].sequence == 1, "shadow protects snapshot");
	check(((struct agent_context_record *)(info.context_base +
					       info.records_offset))[0]
		      .sequence == 1,
	      "snapshot refreshes mirror");
	printf("agentfinal_ucore: tamper_protected=1\n");

	for (int round = 0; round < 2; round++) {
		for (int i = 0; i < AGENT_BATCH_MAX; i++)
			make_echo(&ops[i], 1000 + round * AGENT_BATCH_MAX + i,
				  "wrap");
		check(agent_run(ops, results, AGENT_BATCH_MAX, 0) ==
			      AGENT_BATCH_MAX,
		      "wrap batch");
	}
	n = context_snapshot(&header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_CONTEXT_MAX_RECORDS, "fifo count");
	check(header.oldest_sequence == 65, "fifo oldest");
	check(header.latest_sequence == 192, "fifo latest");
	check(header.dropped_records == 64, "fifo dropped");
	printf("agentfinal_ucore: fifo oldest=%d latest=%d dropped=%d\n",
	       (int)header.oldest_sequence, (int)header.latest_sequence,
	       (int)header.dropped_records);

	check(agent_file_meta_init() == 0, "meta init");
	memset(&q, 0, sizeof(q));
	q.flags = AGENT_FILE_QUERY_USE_INDEX;
	q.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(q.project, "lab-gene-x");
	strcpy(q.run_id, "RUN-042");
	strcpy(q.stage, "align");
	check(agent_file_query(&q, &qr) >= 1, "file query");
	check(qr.used_index == 1, "file query index");
	printf("agentfinal_ucore: file_query hits=%d scanned=%d used_index=%d\n",
	       qr.total_hits, qr.scanned_records, qr.used_index);

	check(agent_watch(AGENT_EVENT_MESSAGE, "self") == 0, "watch");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 7001;
	strcpy(event.payload, "self wake");
	check(agent_wake(info.is_agent ? getpid() : 0, &event) == 0,
	      "wake self");
	check(agent_wait(&event, 20) == AGENT_STATUS_OK, "wait self");
	check(event.corr_id == 7001, "wait corr");
	printf("agentfinal_ucore: event_wait=1 payload=%s\n", event.payload);

	printf("agentfinal_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentfinal_ucore: Agent-OS on uCore final verification\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "agent_create_role orchestrator");
	if (pid == 0)
		run_agent_child();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentfinal_ucore: parent passed\n");
	return 0;
}
