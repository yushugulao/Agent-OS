#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>

#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define MIN(a, b) ((a) < (b) ? (a) : (b))

int getchar()
{
	char byte = 0;
	if (1 == read(stdin, &byte, 1)) {
		return byte;
	} else {
		return EOF;
	}
}

#define __LINE_WIDTH 256

static char buffer[__LINE_WIDTH];
static int buffer_len;
static int buffer_lock;
int buffer_lock_enabled = 0;

int __stdio_process_spawn_prepare(void)
{
	if (!buffer_lock_enabled)
		return 0;
	return mutex_lock(buffer_lock) == 0 ? 1 : -1;
}

int __stdio_process_spawn_finish(int locked, int result)
{
	if (result == 0) {
		/* A child has a new kernel synchronization namespace. */
		buffer_lock = -1;
		buffer_lock_enabled = 0;
		return 0;
	}
	if (locked)
		assert(mutex_unlock(buffer_lock) == 0);
	return result;
}

// Returns the bytes drained; an unwritten suffix remains buffered on failure.
int __write_buffer()
{
	int written = 0;

	while (buffer_len > 0) {
		int r = write(stdout, buffer, buffer_len);

		if (r <= 0)
			return -1;
		if (r > buffer_len)
			return -1;
		written += r;
		buffer_len -= r;
		for (int i = 0; i < buffer_len; i++)
			buffer[i] = buffer[i + r];
	}
	return written;
}

// Discard any buffered output.
void __clear_buffer()
{
	buffer_len = 0;
}

int __fflush()
{
	int r = __write_buffer();
	return r >= 0 ? 0 : r;
}

int fflush(int fd)
{
	if (fd == 1) {
		int len = 0;
		if (buffer_lock_enabled == 1) {
			// for multiple threads io
			mutex_lock(buffer_lock);
			len = __fflush();
			mutex_unlock(buffer_lock);
		} else {
			len = __fflush();
		}
		return len;
	}
	return 0;
}

// enable_thread_io_buffer must be called before use mutiple thread stdout
void enable_thread_io_buffer()
{
	// Assume it's in single thread so there is no consistency problem
	// for buffer_lock and buffer_lock_enabled
	assert((buffer_lock = mutex_create()) >= 0);
	buffer_lock_enabled = 1;
}

static int out_unlocked(const char *s, size_t l)
{
	int ret = 0;
	for (size_t i = 0; i < l; i++) {
		if (buffer_len >= __LINE_WIDTH) {
			int r = __write_buffer();

			if (r < 0)
				return r;
			ret += r;
		}
		char c = s[i];
		buffer[buffer_len++] = c;
		if (buffer_len >= __LINE_WIDTH || c == '\n') {
			int r = __write_buffer();
			if (r < 0) {
				return r;
			}
			ret += r;
		}
	}
	return ret;
}

static int out(int f, const char *s, size_t l)
{
	if (f != stdout)
		return write(f, s, l);
	int len = 0;
	if (buffer_lock_enabled == 1) {
		// for multiple threads io
		mutex_lock(buffer_lock);
		len = out_unlocked(s, l);
		mutex_unlock(buffer_lock);
	} else {
		len = out_unlocked(s, l);
	}
	return len;
}

int putchar(int c)
{
	char byte = c;
	return out(stdout, &byte, 1);
}

int puts(const char *s)
{
	int r;
	r = -(out(stdout, s, strlen(s)) < 0 || putchar('\n') < 0);
	return r;
}

static char digits[] = "0123456789abcdef";

static void printint(unsigned long long value, int base, int negative)
{
	char buf[20];
	int i;

	if (negative)
		out(stdout, "-", 1);
	i = sizeof(buf);
	do {
		buf[--i] = digits[value % (unsigned)base];
	} while ((value /= (unsigned)base) != 0);
	out(stdout, buf + i, sizeof(buf) - i);
}

static int integer_conversion(int c)
{
	return c == 'd' || c == 'u' || c == 'x';
}

static void printptr(uint64 x)
{
	int i = 0, j;
	char buf[32 + 1];
	buf[i++] = '0';
	buf[i++] = 'x';
	for (j = 0; j < (sizeof(uint64) * 2); j++, x <<= 4)
		buf[i++] = digits[x >> (sizeof(uint64) * 8 - 4)];
	buf[i] = 0;
	out(stdout, buf, i);
}

// Integer formats support the default, l, and ll widths.
void printf(const char *fmt, ...)
{
	va_list ap;
	const char *percent, *s = fmt;
	char *text;
	int f = stdout;

	va_start(ap, fmt);
	while (*s) {
		const char *literal = s;
		int length = 0;
		char conversion;

		while (*s && *s != '%')
			s++;
		if (s != literal)
			out(f, literal, s - literal);
		if (*s == 0)
			break;
		percent = s++;
		if (*s == 0)
			break;
		if (*s == 'l') {
			if (s[1] == 'l' && integer_conversion(s[2])) {
				length = 2;
				s += 2;
			} else if (integer_conversion(s[1])) {
				length = 1;
				s++;
			}
		}
		conversion = *s++;
		switch (conversion) {
		case 'd': {
			long long value;
			int negative;

			if (length == 0)
				value = va_arg(ap, int);
			else if (length == 1)
				value = va_arg(ap, long);
			else
				value = va_arg(ap, long long);
			negative = value < 0;
			printint(negative ? 0ULL - (unsigned long long)value :
				 (unsigned long long)value, 10, negative);
			break;
		}
		case 'u':
		case 'x': {
			unsigned long long value;

			if (length == 0)
				value = va_arg(ap, unsigned int);
			else if (length == 1)
				value = va_arg(ap, unsigned long);
			else
				value = va_arg(ap, unsigned long long);
			printint(value, conversion == 'x' ? 16 : 10, 0);
			break;
		}
		case 'p':
			printptr(va_arg(ap, uint64));
			break;
		case 's':
			if ((text = va_arg(ap, char *)) == 0)
				text = "(null)";
			out(f, text, strnlen(text, 200));
			break;
		case '%':
			out(f, percent, 1);
			break;
		default:
			// Print unknown % sequence to draw attention.
			out(f, percent, s - percent);
			break;
		}
	}
	va_end(ap);
}
