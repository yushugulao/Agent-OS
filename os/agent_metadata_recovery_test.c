#include "agent_metadata_recovery_test.h"
#if defined(AGENT_METADATA_BOOT_READ_FAULT) || \
	defined(AGENT_METADATA_SELECT_FAULT_BANK)
#include "agent_internal.h"
#include "agent_metadata_probe.h"
#include "defs.h"

#ifdef AGENT_METADATA_SELECT_FAULT_BANK
#ifndef AGENT_METADATA_SELECT_FAULT_COUNT
#define AGENT_METADATA_SELECT_FAULT_COUNT 3U
#endif
#if AGENT_METADATA_SELECT_FAULT_BANK != 0 && \
	AGENT_METADATA_SELECT_FAULT_BANK != 1
#error "AGENT_METADATA_SELECT_FAULT_BANK must select bank0(0) or bank1(1)"
#endif
#if AGENT_METADATA_SELECT_FAULT_COUNT < 1
#error "AGENT_METADATA_SELECT_FAULT_COUNT must be positive"
#endif
static uint select_remaining;
#endif

#ifdef AGENT_METADATA_BOOT_READ_FAULT
#ifndef AGENT_METADATA_BOOT_READ_FAULT_COUNT
#define AGENT_METADATA_BOOT_READ_FAULT_COUNT 2
#endif
#ifndef AGENT_METADATA_BOOT_READ_FAULT_BANK
#define AGENT_METADATA_BOOT_READ_FAULT_BANK -1
#endif
#if AGENT_METADATA_BOOT_READ_FAULT < 1 || \
	AGENT_METADATA_BOOT_READ_FAULT > 3
#error "AGENT_METADATA_BOOT_READ_FAULT must select busy(1), io(2), or interrupted(3)"
#endif
#if AGENT_METADATA_BOOT_READ_FAULT_COUNT < 1
#error "AGENT_METADATA_BOOT_READ_FAULT_COUNT must be positive"
#endif
#if AGENT_METADATA_BOOT_READ_FAULT_BANK < -1
#error "AGENT_METADATA_BOOT_READ_FAULT_BANK must select all(-1), bank0, or bank1"
#endif
#if AGENT_METADATA_BOOT_READ_FAULT_BANK >= 0
#if AGENT_METADATA_BOOT_READ_FAULT_BANK >= AGENT_META_STORE_BANKS
#error "AGENT_METADATA_BOOT_READ_FAULT_BANK must select all(-1), bank0, or bank1"
#endif
#endif

static uint remaining[AGENT_META_STORE_BANKS];
#endif

void
agent_metadata_recovery_test_init(void)
{
#ifdef AGENT_METADATA_SELECT_FAULT_BANK
	select_remaining = AGENT_METADATA_SELECT_FAULT_COUNT;
#endif
#ifdef AGENT_METADATA_BOOT_READ_FAULT
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++)
		remaining[bank] = AGENT_METADATA_BOOT_READ_FAULT_COUNT;
#endif
}

int
agent_metadata_recovery_test_fault(int bank, int allowed)
{
#ifdef AGENT_METADATA_SELECT_FAULT_BANK
	if (allowed && bank == AGENT_METADATA_SELECT_FAULT_BANK &&
	    select_remaining != 0) {
		select_remaining--;
		printf("agentmeta_select_fault: bank=%d remaining=%d\n", bank,
		       select_remaining);
		return AGENT_META_BANK_INTERRUPTED;
	}
#endif
#ifdef AGENT_METADATA_BOOT_READ_FAULT
	if (!allowed || bank < 0 || bank >= AGENT_META_STORE_BANKS ||
	    (AGENT_METADATA_BOOT_READ_FAULT_BANK >= 0 &&
	     bank != AGENT_METADATA_BOOT_READ_FAULT_BANK) ||
	    remaining[bank] == 0)
		return 0;
	remaining[bank]--;
	printf("agentmeta_boot_fault: kind=%s bank=%d remaining=%d\n",
	       AGENT_METADATA_BOOT_READ_FAULT == 1 ? "busy" :
	       AGENT_METADATA_BOOT_READ_FAULT == 2 ? "io" : "interrupted", bank,
	       remaining[bank]);
	return AGENT_METADATA_BOOT_READ_FAULT == 1 ? AGENT_META_BANK_BUSY :
	       AGENT_METADATA_BOOT_READ_FAULT == 2 ? AGENT_META_BANK_IO :
					      AGENT_META_BANK_INTERRUPTED;
#else
	return 0;
#endif
}

#ifdef AGENT_METADATA_BOOT_READ_FAULT
void
agent_metadata_recovery_test_retry(int status, uint failures, uint64 now,
				   uint64 deadline)
{
	if (status == AGENT_METADATA_LOAD_PROGRESS) {
		uint64 sequence = 0;
		uint phase = 0, offset = 0;
		int bank = -1;

		agent_metadata_probe_progress(&sequence, &bank, &phase, &offset);
		printf("agentmeta_boot_reprobe: progress sequence=%p bank=%d phase=%d offset=%d\n",
		       sequence, bank, phase, offset);
	} else
		printf("agentmeta_boot_reprobe: deferred attempt=%d now=%p deadline=%p\n",
		       failures, now, deadline);
}

void
agent_metadata_recovery_test_admission(int status)
{
	if (status != 0)
		printf("agentmeta_boot_reprobe: admission_rejected status=%d\n",
		       status);
}
#endif
#endif
