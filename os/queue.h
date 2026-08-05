#ifndef QUEUE_H
#define QUEUE_H

struct queue {
	int *data;
	int size;
	int front;
	int tail;
	int count;
};

void queue_init(struct queue *, int, int *);

/* Scheduler queues are serialized by their caller's interrupt-off section. */
int queue_push_locked(struct queue *, int);
int queue_pop_locked(struct queue *);
int queue_remove_locked(struct queue *, int);

#endif // QUEUE_H
