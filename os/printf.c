#include <stdarg.h>
#include "console.h"
#include "defs.h"
#include "sbi.h"
extern void shutdown(void);

static char digits[] = "0123456789abcdef";

static void printint(unsigned long long value, int base, int negative)
{
	char buf[20];
	int i;

	if (negative)
		console_putchar('-');
	i = 0;
	do {
		buf[i++] = digits[value % (unsigned)base];
	} while ((value /= (unsigned)base) != 0);

	while (--i >= 0)
		console_putchar(buf[i]);
}

static int integer_conversion(int c)
{
	return c == 'd' || c == 'u' || c == 'x';
}

static void printptr(uint64 x)
{
	int i;
	console_putchar('0');
	console_putchar('x');
	for (i = 0; i < (sizeof(uint64) * 2); i++, x <<= 4)
		console_putchar(digits[x >> (sizeof(uint64) * 8 - 4)]);
}

// Print to the console. Integer formats support the default, l, and ll widths.
void printf(char *fmt, ...)
{
	va_list ap;
	int i, c, length;
	char *s;

	if (fmt == 0) {
		static const char fatal[] = "[PANIC] null printf format\n";

		for (unsigned int i = 0; i < sizeof(fatal) - 1; i++)
			console_putchar(fatal[i]);
		shutdown();
		__builtin_unreachable();
	}

	va_start(ap, fmt);
	for (i = 0; (c = fmt[i] & 0xff) != 0; i++) {
		if (c != '%') {
			console_putchar(c);
			continue;
		}
		length = 0;
		c = fmt[++i] & 0xff;
		if (c == 0)
			break;
		if (c == 'l') {
			if (fmt[i + 1] == 'l' &&
			    integer_conversion(fmt[i + 2] & 0xff)) {
				length = 2;
				i += 2;
				c = fmt[i] & 0xff;
			} else if (integer_conversion(fmt[i + 1] & 0xff)) {
				length = 1;
				c = fmt[++i] & 0xff;
			}
		}
		switch (c) {
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
			printint(value, c == 'x' ? 16 : 10, 0);
			break;
		}
		case 'p':
			printptr(va_arg(ap, uint64));
			break;
		case 'c':
			console_putchar(va_arg(ap, int));
			break;
		case 's':
			if ((s = va_arg(ap, char *)) == 0)
				s = "(null)";
			for (; *s; s++)
				console_putchar(*s);
			break;
		case '%':
			console_putchar('%');
			break;
		default:
			// Print unknown % sequence to draw attention.
			console_putchar('%');
			while (length-- > 0)
				console_putchar('l');
			console_putchar(c);
			break;
		}
	}
	va_end(ap);
}
