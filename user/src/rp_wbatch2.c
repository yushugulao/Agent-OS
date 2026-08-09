#include <rp_program_manifest.h>
#define RP_WORKER_BATCH_DISPATCHER 1
#include <rp_worker_batch.h>

#define RP_BATCH_DECLARE(index, program) \
	extern int program##_worker_entry(void);
RP_WORKER_BATCH_2_PROGRAMS(RP_BATCH_DECLARE)
#undef RP_BATCH_DECLARE

static __attribute__((noinline)) int rp_worker_run(uint32 index)
{
	switch (index) {
#define RP_BATCH_CASE(entry_index, program) \
	case entry_index: return program##_worker_entry();
	RP_WORKER_BATCH_2_PROGRAMS(RP_BATCH_CASE)
#undef RP_BATCH_CASE
	default: return RP_WORKER_BATCH_EXIT_PROTOCOL;
	}
}

int main(void)
{
	int status = rp_worker_batch_start(2, 17);

	if (status != 0)
		return status;
	for (;;) {
		int index = rp_worker_batch_next();

		if (index == RP_WORKER_BATCH_NEXT_STOP)
			return 0;
		if (index < RP_WORKER_BATCH_NEXT_STOP)
			return -index;
		status = rp_worker_batch_report(rp_worker_run((uint32)index));
		if (status != 0)
			return status;
	}
}
