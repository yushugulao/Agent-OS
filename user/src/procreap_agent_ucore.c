#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define LOW_SCORE_YIELDS 512
#define UNREAPED_PRESSURE_ROUNDS 256
#define LIVE_GLOBAL_PRESSURE_ROUNDS 256

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

static void run_unreaped_holder(int ready_fd, int release_fd)
{
	char token = 0;
	int denied = 0;

	for (int i = 0; i < UNREAPED_PRESSURE_ROUNDS; i++) {
		int child = fork();

		if (child == 0)
			exit(i & 0x3f);
		if (child < 0)
			denied++;
		else
			sched_yield();
	}
	if (denied == 0)
		exit(10);
	token = 'R';
	if (write(ready_fd, &token, 1) != 1)
		exit(11);
	close(ready_fd);
	if (read(release_fd, &token, 1) != 1 || token != 'X')
		exit(12);
	close(release_fd);
	exit(0);
}

static void check_agent_creation_under_unreaped_pressure(void)
{
	int ready_pipe[2];
	int release_pipe[2];
	int holder;
	int agent;
	int status = -1;
	char token = 0;

	check(pipe(ready_pipe) == 0, "create Agent pressure ready pipe");
	check(pipe(release_pipe) == 0,
	      "create Agent pressure release pipe");
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate Agent pressure ready pipe");
	check(agent_scope_delegate_fd(release_pipe[0]) == AGENT_STATUS_OK,
	      "delegate Agent pressure release pipe");
	holder = fork();
	check(holder >= 0, "create ordinary unreaped holder");
	if (holder == 0) {
		close(ready_pipe[0]);
		close(release_pipe[1]);
		run_unreaped_holder(ready_pipe[1], release_pipe[0]);
	}
	close(ready_pipe[1]);
	close(release_pipe[0]);
	check(read(ready_pipe[0], &token, 1) == 1 && token == 'R',
	      "ordinary holder reached child quota");
	close(ready_pipe[0]);
	agent = agent_create();
	check(agent >= 0, "Agent creation survives ordinary child pressure");
	if (agent == 0) {
		close(release_pipe[1]);
		exit(37);
	}
	check(waitpid(agent, &status) == agent && status == 37,
	      "wait Agent pressure probe");
	token = 'X';
	check(write(release_pipe[1], &token, 1) == 1,
	      "release ordinary unreaped holder");
	close(release_pipe[1]);
	status = -1;
	check(waitpid(holder, &status) == holder && status == 0,
	      "reap ordinary unreaped holder");
}

// Direct children of the trusted bootstrap process receive independent user
// domains. Holding them all alive reaches the ordinary global boundary; the
// Agent allocation must still make progress through the reserved class.
static void check_agent_reserve_under_live_pressure(void)
{
	int release_pipe[2];
	int created = 0;
	int denied = 0;
	int agent;
	int status = -1;
	int reaped = 0;
	char token = 0;

	check(pipe(release_pipe) == 0,
	      "create live global pressure pipe");
	for (int i = 0; i < LIVE_GLOBAL_PRESSURE_ROUNDS; i++) {
		int child;

		check(agent_scope_delegate_fd(release_pipe[0]) ==
			      AGENT_STATUS_OK,
		      "delegate live global pressure pipe");
		child = fork();

		if (child == 0) {
			close(release_pipe[1]);
			if (read(release_pipe[0], &token, 1) != -1)
				exit(20);
			close(release_pipe[0]);
			exit(0);
		}
		if (child < 0) {
			denied = 1;
			break;
		}
		created++;
		sched_yield();
	}
	check(denied && created > 0,
	      "ordinary live allocation reaches global limit");
	close(release_pipe[0]);
	agent = agent_create();
	check(agent >= 0, "reserved Agent survives live global pressure");
	if (agent == 0) {
		close(release_pipe[1]);
		exit(37);
	}
	check(waitpid(agent, &status) == agent && status == 37,
	      "wait reserved Agent pressure probe");
	close(release_pipe[1]);
	for (;;) {
		int child = wait(&status);

		if (child < 0)
			break;
		check(status == 0, "ordinary pressure child status");
		reaped++;
	}
	check(reaped == created, "reap all ordinary pressure children");
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
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate normal readiness pipe");
	check(agent_scope_delegate_fd(stop_pipe[0]) == AGENT_STATUS_OK,
	      "delegate normal stop pipe");
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
	check_agent_creation_under_unreaped_pressure();
	printf("procreap_agent_ucore: child-pressure-isolated=1\n");
	check_agent_reserve_under_live_pressure();
	printf("procreap_agent_ucore: reserved-agent-slot=1\n");
	check_high_score_agent_boundary();
	check_normal_score_boundary();
	printf("procreap_agent_ucore: adversarial-agent=1\n");
	printf("procreap_agent_ucore: parent passed\n");
	return 0;
}
