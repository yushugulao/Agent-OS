#include <unistd.h>

extern int main(int, char **);

int __argc;
char **__argv;

int __start_main(int argc, char **argv)
{
	__argc = argc;
	__argv = argv;
	exit(main(argc, argv));
	return 0;
}
