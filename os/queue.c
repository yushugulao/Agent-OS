#include "queue.h"

void queue_init(struct queue *q, int size, int *data)
{
	q->size = size;
	q->data = data;
	q->front = q->tail = 0;
	q->count = 0;
}

int queue_push_locked(struct queue *q, int value)
{
	if (q == 0 || q->data == 0 || q->size <= 0 || q->count >= q->size)
		return -1;
	q->data[q->tail] = value;
	q->tail = (q->tail + 1) % q->size;
	q->count++;
	return 0;
}

int queue_pop_locked(struct queue *q)
{
	if (q == 0 || q->count == 0)
		return -1;
	int value = q->data[q->front];
	q->front = (q->front + 1) % q->size;
	q->count--;
	return value;
}

int queue_remove_locked(struct queue *q, int value)
{
	int kept;
	int original;
	int removed = 0;

	if (q == 0)
		return 0;
	original = q->count;
	for (int i = 0; i < original; i++) {
		kept = queue_pop_locked(q);
		if (kept == value) {
			removed++;
			continue;
		}
		if (queue_push_locked(q, kept) < 0)
			return -1;
	}
	return removed;
}
