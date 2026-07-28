#include "console.h"
#include "defs.h"
#include "sbi.h"
#include "wait.h"

#define CONSOLE_INPUT_CAP 128
#define CONSOLE_POLL_BATCH 16

static char input_buffer[CONSOLE_INPUT_CAP];
static uint input_read;
static uint input_write;
static int input_initialized;
static struct wait_queue input_waiters;

// SBI console input is polled, so the timer moves bytes into a kernel queue
// and turns an otherwise runnable polling loop into an interruptible sleep.
static uint console_poll_locked()
{
	uint added = 0;

	while (input_write - input_read < CONSOLE_INPUT_CAP &&
	       added < CONSOLE_POLL_BATCH) {
		int c = console_getchar();

		if (c < 0)
			break;
		input_buffer[input_write % CONSOLE_INPUT_CAP] = (char)c;
		input_write++;
		added++;
	}
	return added;
}

static int console_dequeue_locked()
{
	if (input_read == input_write)
		console_poll_locked();
	if (input_read == input_write)
		return -1;
	return (uchar)input_buffer[input_read++ % CONSOLE_INPUT_CAP];
}

void consputc(int c)
{
	console_putchar(c);
}

void console_init()
{
	int enabled = intr_save();

	if (!input_initialized) {
		input_read = 0;
		input_write = 0;
		wait_queue_init(&input_waiters, WAIT_REASON_CONSOLE_INPUT);
		input_initialized = 1;
	}
	intr_restore(enabled);
}

int consgetc()
{
	int enabled = intr_save();
	int c = console_dequeue_locked();

	intr_restore(enabled);
	return c;
}

int console_getc_wait()
{
	int enabled;
	int c;

	if (!input_initialized)
		console_init();
	for (;;) {
		enabled = intr_save();
		c = console_dequeue_locked();
		if (c >= 0) {
			intr_restore(enabled);
			return c;
		}
		if (wait_queue_sleep_irq(&input_waiters) != WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
		intr_restore(enabled);
	}
}

void console_input_tick()
{
	uint added;
	int enabled;

	if (!input_initialized)
		return;
	enabled = intr_save();
	added = console_poll_locked();
	while (added-- != 0)
		if (!wait_queue_wake_one(&input_waiters))
			break;
	intr_restore(enabled);
}
