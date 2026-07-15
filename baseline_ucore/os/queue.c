#include "queue.h"
#include "riscv.h"

void init_queue(struct queue *q, int size, int *data)
{
	int enabled = intr_save();

	q->size = size;
	q->data = data;
	q->front = q->tail = 0;
	q->count = 0;
	intr_restore(enabled);
}

int push_queue(struct queue *q, int value)
{
	int enabled = intr_save();

	if (q == 0 || q->data == 0 || q->size <= 0 || q->count >= q->size) {
		intr_restore(enabled);
		return -1;
	}
	q->data[q->tail] = value;
	q->tail = (q->tail + 1) % q->size;
	q->count++;
	intr_restore(enabled);
	return 0;
}

int pop_queue(struct queue *q)
{
	int enabled = intr_save();

	if (q == 0 || q->count == 0) {
		intr_restore(enabled);
		return -1;
	}
	int value = q->data[q->front];
	q->front = (q->front + 1) % q->size;
	q->count--;
	intr_restore(enabled);
	return value;
}

int remove_queue_value(struct queue *q, int value)
{
	int kept;
	int original;
	int removed = 0;
	int enabled = intr_save();

	if (q == 0) {
		intr_restore(enabled);
		return 0;
	}
	original = q->count;
	for (int i = 0; i < original; i++) {
		kept = pop_queue(q);
		if (kept == value) {
			removed++;
			continue;
		}
		if (push_queue(q, kept) < 0) {
			intr_restore(enabled);
			return -1;
		}
	}
	intr_restore(enabled);
	return removed;
}
