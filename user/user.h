#define SBRK_ERROR ((char *)-1)

struct stat;
struct agent_info;
struct agent_request;
struct agent_response;
struct agent_tool_desc;
struct agent_context_record;
struct agent_op;
struct agent_result;
struct agent_context_header;

// system calls
int fork(void);
int exit(int) __attribute__((noreturn));
int wait(int *);
int pipe(int *);
int write(int, const void *, int);
int read(int, void *, int);
int close(int);
int kill(int);
int exec(const char *, char **);
int open(const char *, int);
int mknod(const char *, short, short);
int unlink(const char *);
int fstat(int fd, struct stat *);
int link(const char *, const char *);
int mkdir(const char *);
int chdir(const char *);
int dup(int);
int getpid(void);
char *sys_sbrk(int, int);
int pause(int);
int uptime(void);
int agent_fork(void);
int agent_create(void);
int agent_info(struct agent_info *);
int agent_call(struct agent_request *, struct agent_response *);
int agent_tool_list(struct agent_tool_desc *, int);
int tool_call(struct agent_request *, struct agent_response *);
int tool_list(struct agent_tool_desc *, int);
int agent_run(struct agent_op *, struct agent_result *, int, uint64);
int context_push(struct agent_context_record *);
int context_query(uint64, struct agent_context_record *, int);
int context_snapshot(struct agent_context_header *, struct agent_context_record *,
                     int);
int context_rollback(uint64);
int context_clear(void);

// ulib.c
int stat(const char *, struct stat *);
char *strcpy(char *, const char *);
void *memmove(void *, const void *, int);
char *strchr(const char *, char c);
int strcmp(const char *, const char *);
char *gets(char *, int max);
uint strlen(const char *);
void *memset(void *, int, uint);
int atoi(const char *);
int memcmp(const void *, const void *, uint);
void *memcpy(void *, const void *, uint);
char *sbrk(int);
char *sbrklazy(int);

// printf.c
void fprintf(int, const char *, ...) __attribute__((format(printf, 2, 3)));
void printf(const char *, ...) __attribute__((format(printf, 1, 2)));

// umalloc.c
void *malloc(uint);
void free(void *);
