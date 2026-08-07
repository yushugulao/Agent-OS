#ifndef SYSCALL_H
#define SYSCALL_H

#include "types.h"

struct proc;

void syscall();
uint syscall_count_read(const struct proc *, int syscall_id);

#endif // SYSCALL_H
