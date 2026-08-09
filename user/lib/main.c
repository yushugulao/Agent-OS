#include <agent.h>
#include <rp_launch_attestation.h>
#include <string.h>
#include <unistd.h>

extern int main(int, char **);

int __argc;
char **__argv;

static int rp_parse_launch_uint(const char **cursor, char delimiter,
				uint64 *value)
{
	const char *digits = *cursor;
	uint64 parsed = 0;

	if (*digits < '0' || *digits > '9' ||
	    (*digits == '0' && digits[1] >= '0' && digits[1] <= '9'))
		return 0;
	for (; *digits >= '0' && *digits <= '9'; digits++) {
		uint64 digit = *digits - '0';

		if (parsed > (~0ULL - digit) / 10)
			return 0;
		parsed = parsed * 10 + digit;
	}
	if ((delimiter != 0 && *digits != delimiter) ||
	    (delimiter == 0 && *digits != 0))
		return 0;
	*cursor = delimiter == 0 ? digits : digits + 1;
	*value = parsed;
	return 1;
}

/* 返回 0 表示没有启动约束，1 表示解析成功，-1 表示约束损坏。 */
static int rp_parse_launch_expectation(
	int argc, char **argv, struct rp_launch_expectation *expected)
{
	const char *prefix = RP_LAUNCH_EXPECT_PREFIX;
	int prefix_len = strlen(prefix);
	const char *cursor;
	uint64 is_agent;
	uint64 role;

	memset(expected, 0, sizeof(*expected));
	if (argc < 2 || argv == 0 || argv[1] == 0 ||
	    strncmp(argv[1], prefix, prefix_len) != 0)
		return 0;
	cursor = argv[1] + prefix_len;
	if (!rp_parse_launch_uint(&cursor, ',', &is_agent) || is_agent > 1 ||
	    !rp_parse_launch_uint(&cursor, ',', &role) || role > 0x7fffffffULL ||
	    !rp_parse_launch_uint(&cursor, ',', &expected->filesystem_domain) ||
	    !rp_parse_launch_uint(&cursor, 0,
				  &expected->filesystem_capability_mask))
		return -1;
	expected->is_agent = (int)is_agent;
	expected->agent_role = (int)role;
	return rp_launch_expectation_valid(expected) ? 1 : -1;
}

static int rp_launch_identity_self_check(int argc, char **argv)
{
	struct rp_launch_expectation expected;
	struct agent_info info;
	int parsed = rp_parse_launch_expectation(argc, argv, &expected);
	int pid;

	if (parsed == 0)
		return 1;
	if (parsed < 0)
		return 0;
	memset(&info, 0, sizeof(info));
	pid = agent_launch_info(&info);
	return pid > 0 && info.is_agent == expected.is_agent &&
	       info.agent_role == expected.agent_role &&
	       info.filesystem_domain == expected.filesystem_domain &&
	       info.filesystem_capability_mask ==
		       expected.filesystem_capability_mask;
}

int __start_main(int argc, char **argv)
{
	if (!rp_launch_identity_self_check(argc, argv))
		exit(RP_LAUNCH_SELF_CHECK_EXIT);
	__argc = argc;
	__argv = argv;
	exit(main(argc, argv));
	return 0;
}
