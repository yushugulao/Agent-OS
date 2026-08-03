#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "fs.h"

#ifndef O_BINARY
#define O_BINARY 0
#endif

#ifndef static_assert
#define static_assert(a, b)                                                    \
	do {                                                                   \
		switch (0)                                                     \
		case 0:                                                        \
		case (a):;                                                     \
	} while (0)
#endif

#define NINODES NINODE

// Disk layout:
// [ boot | super | inode blocks | free bitmap | owner map | data blocks ]

int nbitmap = (FSSIZE + BPB - 1) / BPB;
int nqmap = (FSSIZE + QPB - 1) / QPB;
int ninodeblocks = (NINODES + IPB - 1) / IPB;
int nmeta; // Number of metadata blocks.
int nblocks; // Number of data blocks

int fsfd;
struct superblock sb;
char zeroes[BSIZE];
uint freeinode = 1;
uint freeblock;

char *basename(char *);
void balloc(int);
void qalloc(int);
void wsect(uint, void *);
void winode(uint, struct dinode *);
void rinode(uint inum, struct dinode *ip);
void rsect(uint sec, void *buf);
uint ialloc(ushort type);
void iappend(uint inum, void *p, int n);
void require_free_block(const char *context);

// convert to intel byte order
ushort xshort(ushort x)
{
	ushort y;
	uchar *a = (uchar *)&y;
	a[0] = x;
	a[1] = x >> 8;
	return y;
}

uint xint(uint x)
{
	uint y;
	uchar *a = (uchar *)&y;
	a[0] = x;
	a[1] = x >> 8;
	a[2] = x >> 16;
	a[3] = x >> 24;
	return y;
}

int main(int argc, char *argv[])
{
	int i, cc, fd;
	uint rootino, inum, off;
	struct dirent de;
	char buf[BSIZE];
	struct dinode din;
	static_assert(sizeof(int) == 4, "Integers must be 4 bytes!");
	if (argc < 2) {
		fprintf(stderr, "Usage: mkfs fs.img files...\n");
		exit(1);
	}
	assert((BSIZE % sizeof(struct dinode)) == 0);
	fsfd = open(argv[1], O_RDWR | O_CREAT | O_TRUNC | O_BINARY, 0666);
	if (fsfd < 0) {
		perror(argv[1]);
		exit(1);
	}
	// 1 fs block = 1 disk sector
	nmeta = 2 + ninodeblocks + nbitmap + nqmap;
	nblocks = FSSIZE - nmeta;
	if (nblocks <= 0) {
		fprintf(stderr, "mkfs: metadata does not fit file system\n");
		exit(1);
	}

	sb.magic = FSMAGIC;
	sb.size = xint(FSSIZE);
	sb.nblocks = xint(nblocks);
	sb.ninodes = xint(NINODES);
	sb.inodestart = xint(2);
	sb.bmapstart = xint(2 + ninodeblocks);
	sb.qmapstart = xint(2 + ninodeblocks + nbitmap);
	sb.datastart = xint(nmeta);
	sb.public_principal = xint(FS_OWNER_PUBLIC);

	printf("nmeta %d (boot, super, inode blocks %u, bitmap blocks %u, "
	       "owner blocks %u) blocks %d total %d\n",
	       nmeta, ninodeblocks, nbitmap, nqmap, nblocks, FSSIZE);

	freeblock = nmeta; // the first free block that we can allocate

	for (i = 0; i < FSSIZE; i++)
		wsect(i, zeroes);

	memset(buf, 0, sizeof(buf));
	memmove(buf, &sb, sizeof(sb));
	wsect(1, buf);

	rootino = ialloc(T_DIR);

	for (i = 2; i < argc; i++) {
		char *shortname = basename(argv[i]);
		assert(strchr(shortname, '/') == 0);

		if ((fd = open(argv[i], O_RDONLY | O_BINARY)) < 0) {
			perror(argv[i]);
			exit(1);
		}

		inum = ialloc(T_FILE);

		memset(&de, 0, sizeof(de));
		de.inum = xshort(inum);
		strncpy(de.name, shortname, DIRSIZ);
		iappend(rootino, &de, sizeof(de));

		while ((cc = read(fd, buf, sizeof(buf))) > 0)
			iappend(inum, buf, cc);

		close(fd);
	}

	// fix size of root inode dir
	rinode(rootino, &din);
	off = xint(din.size);
	if (off % BSIZE != 0)
		off = ((off / BSIZE) + 1) * BSIZE;
	din.size = xint(off);
	winode(rootino, &din);

	qalloc(freeblock);
	balloc(freeblock);
	return 0;
}

char *basename(char *path)
{
	while (strchr(path, '/') != 0) {
		path = strchr(path, '/') + 1;
	}
	return path;
}

void wsect(uint sec, void *buf)
{
	if (lseek(fsfd, sec * BSIZE, 0) != sec * BSIZE) {
		perror("lseek");
		exit(1);
	}
	if (write(fsfd, buf, BSIZE) != BSIZE) {
		perror("write");
		exit(1);
	}
}

void winode(uint inum, struct dinode *ip)
{
	char buf[BSIZE];
	uint bn;
	struct dinode *dip;

	bn = IBLOCK(inum, sb);
	rsect(bn, buf);
	dip = ((struct dinode *)buf) + (inum % IPB);
	*dip = *ip;
	wsect(bn, buf);
}

void rinode(uint inum, struct dinode *ip)
{
	char buf[BSIZE];
	uint bn;
	struct dinode *dip;

	bn = IBLOCK(inum, sb);
	rsect(bn, buf);
	dip = ((struct dinode *)buf) + (inum % IPB);
	*ip = *dip;
}

void rsect(uint sec, void *buf)
{
	if (lseek(fsfd, sec * BSIZE, 0) != sec * BSIZE) {
		perror("lseek");
		exit(1);
	}
	if (read(fsfd, buf, BSIZE) != BSIZE) {
		perror("read");
		exit(1);
	}
}

void require_free_block(const char *context)
{
	if (freeblock >= FSSIZE) {
		fprintf(stderr,
			"mkfs: out of data blocks while writing %s: freeblock=%u FSSIZE=%d\n",
			context, freeblock, FSSIZE);
		exit(1);
	}
}

uint ialloc(ushort type)
{
	uint inum;
	struct dinode din;

	if (freeinode >= NINODES) {
		fprintf(stderr, "mkfs: out of inodes\n");
		exit(1);
	}
	inum = freeinode++;
	memset(&din, 0, sizeof(din));
	din.type = xshort(type);
	din.fs_owner_version = xshort(FS_OWNER_VERSION);
	din.fs_owner_domain = xint(FS_OWNER_SYSTEM);
	din.size = xint(0);
	// LAB4: You may want to init link count here
	winode(inum, &din);
	return inum;
}

void balloc(int used)
{
	uchar buf[BSIZE];
	int block;

	assert(used <= FSSIZE);
	for (block = 0; block < nbitmap; block++) {
		int base = block * BPB;
		int limit = used - base;

		if (limit < 0)
			limit = 0;
		if (limit > BPB)
			limit = BPB;
		memset(buf, 0, BSIZE);
		for (int i = 0; i < limit; i++)
			buf[i / 8] |= 0x1 << (i % 8);
		wsect(xint(sb.bmapstart) + block, buf);
	}
}

void qalloc(int used)
{
	uint owners[QPB];

	assert(used <= FSSIZE);
	for (int block = 0; block < nqmap; block++) {
		int base = block * QPB;
		int limit = used - base;

		if (limit < 0)
			limit = 0;
		if (limit > (int)QPB)
			limit = (int)QPB;
		memset(owners, 0, sizeof(owners));
		for (int i = 0; i < limit; i++)
			owners[i] = xint(FS_OWNER_SYSTEM);
		wsect(xint(sb.qmapstart) + block, owners);
	}
}

#define min(a, b) ((a) < (b) ? (a) : (b))

void iappend(uint inum, void *xp, int n)
{
	char *p = (char *)xp;
	uint fbn, off, n1;
	struct dinode din;
	char buf[BSIZE];
	uint indirect[NINDIRECT];
	uint x;

	rinode(inum, &din);
	off = xint(din.size);
	while (n > 0) {
		fbn = off / BSIZE;
		assert(fbn < MAXFILE);
		if (fbn < NDIRECT) {
			if (xint(din.addrs[fbn]) == 0) {
				require_free_block("direct file data");
				din.addrs[fbn] = xint(freeblock++);
			}
			x = xint(din.addrs[fbn]);
		} else {
			if (xint(din.addrs[NDIRECT]) == 0) {
				require_free_block("indirect block table");
				din.addrs[NDIRECT] = xint(freeblock++);
			}
			rsect(xint(din.addrs[NDIRECT]), (char *)indirect);
			if (indirect[fbn - NDIRECT] == 0) {
				require_free_block("indirect file data");
				indirect[fbn - NDIRECT] = xint(freeblock++);
				wsect(xint(din.addrs[NDIRECT]),
				      (char *)indirect);
			}
			x = xint(indirect[fbn - NDIRECT]);
		}
		n1 = min(n, (fbn + 1) * BSIZE - off);
		rsect(x, buf);
		memmove(buf + (off - (fbn * BSIZE)), p, n1);
		wsect(x, buf);
		n -= n1;
		off += n1;
		p += n1;
	}
	din.size = xint(off);
	winode(inum, &din);
}
