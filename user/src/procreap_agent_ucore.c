#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define LOW_SCORE_YIELDS 512

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("procreap_agent_ucore: check failed: %s\n", message);
	exit(1);
}

static void run_high_score_agent(int ready_fd, int stop_fd)
{
	struct agent_event event;
	char token;

	check(agent_watch(AGENT_EVENT_MESSAGE, "reap-hog") == 0,
	      "register high-score watch");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 8101;
	strcpy(event.payload, "reap-hog");
	check(agent_wake(getpid(), &event) == 0,
	      "queue high-score event");
	check(write(ready_fd, "R", 1) == 1, "publish high-score readiness");
	check(close(ready_fd) == 0, "close readiness writer");
	check(read(stop_fd, &token, 1) == 1 && token == 'S',
	      "receive high-score stop");
	check(close(stop_fd) == 0, "close stop reader");
	exit(0);
}

static void run_normal_runner(int ready_fd, int stop_fd)
{
	char token;

	check(write(ready_fd, "R", 1) == 1, "publish normal readiness");
	check(close(ready_fd) == 0, "close normal readiness writer");
	check(read(stop_fd, &token, 1) == 1 && token == 'S',
	      "receive normal stop");
	check(close(stop_fd) == 0, "close normal stop reader");
	exit(0);
}

static void check_high_score_agent_boundary(void)
{
	int ready_pipe[2];
	int stop_pipe[2];
	int hog;
	int victim;
	int status = -1;
	char token;

	check(pipe(ready_pipe) == 0, "create readiness pipe");
	check(pipe(stop_pipe) == 0, "create stop pipe");
	hog = agent_create();
	check(hog >= 0, "create high-score agent");
	if (hog == 0) {
		close(ready_pipe[0]);
		close(stop_pipe[1]);
		run_high_score_agent(ready_pipe[1], stop_pipe[0]);
	}
	check(close(ready_pipe[1]) == 0, "close readiness writer in parent");
	check(close(stop_pipe[0]) == 0, "close stop reader in parent");
	check(read(ready_pipe[0], &token, 1) == 1 && token == 'R',
	      "wait for high-score agent");
	check(close(ready_pipe[0]) == 0, "close readiness reader");

	// The pending event gives hog a permanently higher soft score. The
	// victim must still run, cross the teardown checkpoint, and be reaped.
	victim = agent_create();
	check(victim >= 0, "create teardown victim");
	if (victim == 0) {
		close(stop_pipe[1]);
		exit(0);
	}
	check(waitpid(victim, &status) == victim,
	      "high-score peer cannot starve teardown");
	check(status == 0, "teardown victim status");
	check(write(stop_pipe[1], "S", 1) == 1, "stop high-score agent");
	check(close(stop_pipe[1]) == 0, "close stop writer");
	status = -1;
	check(waitpid(hog, &status) == hog, "reap high-score agent");
	check(status == 0, "high-score agent status");
}

static void check_normal_score_boundary(void)
{
	int ready_pipe[2];
	int stop_pipe[2];
	int runner;
	int victim;
	int status = -1;
	char token;

	check(pipe(ready_pipe) == 0, "create normal readiness pipe");
	check(pipe(stop_pipe) == 0, "create normal stop pipe");
	runner = fork();
	check(runner >= 0, "create normal runner");
	if (runner == 0) {
		close(ready_pipe[0]);
		close(stop_pipe[1]);
		run_normal_runner(ready_pipe[1], stop_pipe[0]);
	}
	check(close(ready_pipe[1]) == 0,
	      "close normal readiness writer in parent");
	check(close(stop_pipe[0]) == 0, "close normal stop reader in parent");
	check(read(ready_pipe[0], &token, 1) == 1 && token == 'R',
	      "wait for normal runner");
	check(close(ready_pipe[0]) == 0, "close normal readiness reader");

	// Repeated dispatches drive the Agent's bounded vruntime penalty below
	// the ordinary task score. FIFO progress must still carry it through exit.
	victim = agent_create();
	check(victim >= 0, "create low-score teardown victim");
	if (victim == 0) {
		close(stop_pipe[1]);
		for (int i = 0; i < LOW_SCORE_YIELDS; i++)
			sched_yield();
		exit(0);
	}
	check(waitpid(victim, &status) == victim,
	      "normal peer cannot starve Agent teardown");
	check(status == 0, "low-score teardown victim status");
	check(write(stop_pipe[1], "S", 1) == 1, "stop normal runner");
	check(close(stop_pipe[1]) == 0, "close normal stop writer");
	status = -1;
	check(waitpid(runner, &status) == runner, "reap normal runner");
	check(status == 0, "normal runner status");
}

int main(void)
{
	printf("procreap_agent_ucore: bounded teardown scheduling\n");
	check_high_score_agent_boundary();
	check_normal_score_boundary();
	printf("procreap_agent_ucore: adversarial-agent=1\n");
	printf("procreap_agent_ucore: parent passed\n");
	return 0;
}
