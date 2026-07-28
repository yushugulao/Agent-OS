#include <agent.h>
#include <rp_launch_attestation.h>
#include <string.h>
#include <unistd.h>

extern int main(int, char **);

int __argc;
char **__argv;

static int rp_launch_attest_fd(int argc, char **argv)
{
	const char *prefix = RP_LAUNCH_ATTEST_PREFIX;
	int prefix_len = strlen(prefix);
	int fd = 0;

	if (argc < 2 || argv == 0 || argv[1] == 0 ||
	    strncmp(argv[1], prefix, prefix_len) != 0 ||
	    argv[1][prefix_len] == 0)
		return -1;
	for (const char *digit = argv[1] + prefix_len; *digit; digit++) {
		int value;

		if (*digit < '0' || *digit > '9')
			return -1;
		value = *digit - '0';
		if (fd > (1024 - value) / 10)
			return -1;
		fd = fd * 10 + value;
	}
	return fd;
}

static void rp_report_launch_identity(int argc, char **argv)
{
	struct rp_launch_attestation attestation;
	struct agent_info info;
	char *bytes = (char *)&attestation;
	int fd = rp_launch_attest_fd(argc, argv);
	int written = 0;

	if (fd < 0)
		return;
	memset(&attestation, 0, sizeof(attestation));
	memset(&info, 0, sizeof(info));
	attestation.magic = RP_LAUNCH_ATTEST_MAGIC;
	attestation.version = RP_LAUNCH_ATTEST_VERSION;
	attestation.pid = getpid();
	attestation.status = agent_info(&info);
	if (attestation.status == 0) {
		attestation.is_agent = info.is_agent;
		attestation.agent_role = info.agent_role;
		attestation.filesystem_domain = info.filesystem_domain;
		attestation.filesystem_capability_mask =
			info.filesystem_capability_mask;
	}
	while (written < (int)sizeof(attestation)) {
		int n = write(fd, bytes + written,
			      sizeof(attestation) - written);

		if (n <= 0)
			break;
		written += n;
	}
	close(fd);
}

int __start_main(int argc, char **argv)
{
	__argc = argc;
	__argv = argv;
	rp_report_launch_identity(argc, argv);
	exit(main(argc, argv));
	return 0;
}
