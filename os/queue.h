#ifndef QUEUE_H
#define QUEUE_H

struct queue {
	int *data;
	int size;
	int front;
	int tail;
	int count;
};

void init_queue(struct queue *, int, int *);
int push_queue(struct queue *, int);
int pop_queue(struct queue *);
int remove_queue_value(struct queue *, int);

#endif // QUEUE_H
