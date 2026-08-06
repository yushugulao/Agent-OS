#define CONSOLE_H
#define DEFS_H
typedef unsigned long long uint64;

static char captured[2048];
static unsigned captured_count;
static int failed;

static void consputc(int c)
{
	if (captured_count >= sizeof(captured)) {
		failed = 1;
		return;
	}
	captured[captured_count++] = c;
}

static void shutdown(void)
{
	failed = 1;
}

#define printf kernel_printf
#include "../../os/printf.c"
#undef printf

_Static_assert(sizeof(int) == 4, "printf probe requires 32-bit int");
_Static_assert(sizeof(long) == 4 || sizeof(long) == 8,
	       "printf probe requires a 32-bit or 64-bit long");
_Static_assert(sizeof(long long) == 8, "printf probe requires 64-bit long long");

static int same(const char *expected)
{
	unsigned i = 0;

	while (expected[i] != 0) {
		if (i >= captured_count || captured[i] != expected[i])
			return 0;
		i++;
	}
	return i == captured_count && !failed;
}

static void reset(void)
{
	captured_count = 0;
	failed = 0;
}

int main(void)
{
	reset();
#if __SIZEOF_LONG__ == 8
	kernel_printf("%d|%u|%x|%ld|%lu|%lx|%lld|%llu|%llx|%p|%s|%%",
		      (-2147483647 - 1), ~0U, ~0U,
		      (-9223372036854775807L - 1L), ~0UL, ~0UL,
		      (-9223372036854775807LL - 1LL), ~0ULL, ~0ULL,
		      (uint64)0x123, "ok");
	if (!same("-2147483648|4294967295|ffffffff|"
		  "-9223372036854775808|18446744073709551615|"
		  "ffffffffffffffff|-9223372036854775808|"
		  "18446744073709551615|ffffffffffffffff|"
		  "0x0000000000000123|ok|%"))
		return 1;
#else
	kernel_printf("%d|%u|%x|%ld|%lu|%lx|%lld|%llu|%llx|%p|%s|%%",
		      (-2147483647 - 1), ~0U, ~0U,
		      (-2147483647L - 1L), ~0UL, ~0UL,
		      (-9223372036854775807LL - 1LL), ~0ULL, ~0ULL,
		      (uint64)0x123, "ok");
	if (!same("-2147483648|4294967295|ffffffff|"
		  "-2147483648|4294967295|ffffffff|"
		  "-9223372036854775808|18446744073709551615|"
		  "ffffffffffffffff|0x0000000000000123|ok|%"))
		return 1;
#endif

	reset();
	kernel_printf("%u|%llu|%d|%x|%c|%s", 17U, 4294967296ULL, -3,
		      0xfeedU, 'K', (char *)0);
	if (!same("17|4294967296|-3|feed|K|(null)"))
		return 2;

	reset();
	kernel_printf("%q|%d|%l%s|%llq|end%", 7, "ok");
	if (!same("%q|7|%lok|%llq|end"))
		return 3;
	return 0;
}
