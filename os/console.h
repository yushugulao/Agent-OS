#ifndef CONSOLE_H
#define CONSOLE_H

void consputc(int);
int consgetc();
int console_getc_wait();
void console_input_tick();
void console_init();

#endif // CONSOLE_H
