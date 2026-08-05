#include <stddef.h>
#include <string.h>

#include "traditional_perf_seed.h"

static void fixed_hex16(uint64 value, char text[17])
{
	static const char digits[] = "0123456789abcdef";

	for (int index = 15; index >= 0; index--) {
		text[index] = digits[value & 0xfULL];
		value >>= 4;
	}
	text[16] = 0;
}

static int parse_u32(const char *text, uint *value)
{
	uint parsed = 0;

	if (text == 0 || *text == 0)
		return -1;
	for (; *text != 0; text++) {
		uint digit;

		if (*text < '0' || *text > '9')
			return -1;
		digit = (uint)(*text - '0');
		if (parsed > (UINT_MAX - digit) / 10U)
			return -1;
		parsed = parsed * 10U + digit;
	}
	*value = parsed;
	return 0;
}

static int exec_probe_status(uint iteration)
{
	uint lane = iteration & 7U;
	uint value = (uint)(TRADPERF_RUN_NONCE >> (lane * 8U));

	value ^= TRADPERF_SAMPLE_ID * 7U;
	value ^= iteration * 17U;
	return (int)(value & 0x3fU);
}

int main(int argc, char **argv)
{
	char nonce[17];
	uint sample;
	uint order_slot;
	uint iteration;

	if (argc != 5)
		return 120;
	fixed_hex16(TRADPERF_RUN_NONCE, nonce);
	if (strcmp(argv[1], nonce) != 0 || parse_u32(argv[2], &sample) < 0 ||
	    parse_u32(argv[3], &order_slot) < 0 ||
	    parse_u32(argv[4], &iteration) < 0 ||
	    sample != TRADPERF_SAMPLE_ID ||
	    order_slot != TRADPERF_ORDER_SLOT)
		return 121;
	return exec_probe_status(iteration);
}
