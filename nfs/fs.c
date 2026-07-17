#include <assert.h>
#include <elf.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "fs.h"
#include "../user/include/exec_policy_manifest.h"

#ifndef static_assert
#define static_assert(a, b)                                                    \
	do {                                                                   \
		switch (0)                                                     \
		case 0:                                                        \
		case (a):;                                                     \
	} while (0)
#endif

#define NINODES NINODE
#define USER_IMAGE_BASE 0x1000ULL
#define USER_PAGE_SIZE  4096ULL

// Disk layout:
// [ boot | super | inode blocks | bitmap | owner map | data blocks ]

int nbitmap = (FSSIZE + BSIZE * 8 - 1) / (BSIZE * 8);
int nqmap = (FSSIZE + QPB - 1) / QPB;
int ninodeblocks = (NINODES + IPB - 1) / IPB;
int nmeta; // Number of meta blocks (boot, sb, nlog, inode, bitmap)
int nblocks; // Number of data blocks

int fsfd;
struct superblock sb;
char zeroes[BSIZE];
uint freeinode = 1;
uint freeblock;

struct exec_policy_entry {
	const char *source;
	const char *image;
	uint flags;
	uint role_mask;
	int launch_role;
	uint vfs_profile;
};

struct exec_layout {
	uint version;
	uint rw_offset;
};

struct host_image {
	unsigned char *data;
	size_t size;
	struct exec_layout layout;
};

#define EXEC_POLICY_ROW(source, image, flags, role_mask, launch_role, profile) \
	{ source, image, flags, role_mask, launch_role, profile },
static const struct exec_policy_entry exec_policy[] = {
	EXEC_POLICY_ENTRIES(EXEC_POLICY_ROW)
};
#undef EXEC_POLICY_ROW

static char installed_names[NINODE][DIRSIZ];
static int installed_name_count;

char *basename(char *);
void balloc(int);
void wsect(uint, void *);
void winode(uint, struct dinode *);
void rinode(uint inum, struct dinode *ip);
void rsect(uint sec, void *buf);
uint ialloc(ushort type);
void iappend(uint inum, void *p, int n);
void require_free_block(const char *context);
void install_file(uint rootino, const char *host_path, const char *image,
		  uint flags, uint role_mask, uint vfs_profile,
		  const struct host_image *host_image);
struct host_image read_host_image(const char *host_path);
void validate_exec_policy(void);
void label_inode(uint inum, uint flags, uint domain, uint policy,
		 uint exec_profile);
static int vfs_exec_profile_valid(uint profile);
uint vfs_label_checksum(uint inum, uint magic, uint version, uint flags,
			uint domain, uint policy, uint exec_profile,
			uint generation, uint incarnation, uint reserved0,
			uint reserved1);

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
	int i;
	uint rootino, off;
	char buf[BSIZE];
	struct dinode din;
	static_assert(sizeof(int) == 4, "Integers must be 4 bytes!");
	static_assert(sizeof(struct dinode) == 128,
		      "dinode format must stay fixed");
	static_assert(EXEC_MANIFEST_F_TRUSTED == EXEC_FLAG_TRUSTED,
		      "manifest trusted flag mismatch");
	static_assert(EXEC_MANIFEST_F_IMMUTABLE == EXEC_FLAG_IMMUTABLE,
		      "manifest immutable flag mismatch");
	static_assert(EXEC_MANIFEST_F_BOOTSTRAP == EXEC_FLAG_BOOTSTRAP,
		      "manifest bootstrap flag mismatch");
	static_assert(EXEC_MANIFEST_F_DOMAIN_SAFE == EXEC_FLAG_DOMAIN_SAFE,
		      "manifest domain-safe flag mismatch");
	static_assert(EXEC_MANIFEST_VFS_PROFILE_NONE == VFS_EXEC_PROFILE_NONE,
		      "manifest empty VFS profile mismatch");
	static_assert(EXEC_MANIFEST_VFS_PROFILE_WORKFLOW ==
			      VFS_EXEC_PROFILE_WORKFLOW,
		      "manifest workflow VFS profile mismatch");
	static_assert(EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ ==
			      VFS_EXEC_PROFILE_CONTENT_READ,
		      "manifest read-only VFS profile mismatch");
	static_assert(EXEC_MANIFEST_VFS_PROFILE_ARTIFACT_WRITE ==
			      VFS_EXEC_PROFILE_ARTIFACT_WRITE,
		      "manifest write-only VFS profile mismatch");
	if (argc < 2) {
		fprintf(stderr, "Usage: mkfs fs.img files...\n");
		exit(1);
	}
	assert((BSIZE % sizeof(struct dinode)) == 0);
	validate_exec_policy();
	fsfd = open(argv[1], O_RDWR | O_CREAT | O_TRUNC, 0666);
	if (fsfd < 0) {
		perror(argv[1]);
		exit(1);
	}
	// 1 fs block = 1 disk sector
	nmeta = 2 + ninodeblocks + nbitmap + nqmap;
	nblocks = FSSIZE - nmeta;

	sb.magic = FSMAGIC;
	sb.size = xint(FSSIZE);
	sb.nblocks = xint(nblocks);
	sb.ninodes = xint(NINODES);
	sb.inodestart = xint(2);
	sb.bmapstart = xint(2 + ninodeblocks);
	sb.qmapstart = xint(2 + ninodeblocks + nbitmap);
	sb.datastart = xint(nmeta);

	printf("nmeta %d (boot, super, inode %u, bitmap %u, owner %u) "
	       "blocks %d total %d\n",
	       nmeta, ninodeblocks, nbitmap, nqmap, nblocks, FSSIZE);

	freeblock = nmeta; // the first free block that we can allocate

	for (i = 0; i < FSSIZE; i++)
		wsect(i, zeroes);

	memset(buf, 0, sizeof(buf));
	memmove(buf, &sb, sizeof(sb));
	wsect(1, buf);

	rootino = ialloc(T_DIR);
	label_inode(rootino, VFS_LABEL_F_ROOT, VFS_DOMAIN_PUBLIC,
		    VFS_POLICY_ROOT, VFS_EXEC_PROFILE_NONE);

	for (i = 2; i < argc; i++) {
		char *shortname = basename(argv[i]);
		const struct exec_policy_entry *primary = 0;
		struct host_image host_image = read_host_image(argv[i]);

		for (uint p = 0; p < sizeof(exec_policy) / sizeof(exec_policy[0]);
		     p++) {
			if (strcmp(exec_policy[p].source, shortname) != 0 ||
			    strcmp(exec_policy[p].image, shortname) != 0)
				continue;
			if (primary != 0) {
				fprintf(stderr,
					"mkfs: duplicate primary exec policy for %s\n",
					shortname);
				exit(1);
			}
			primary = &exec_policy[p];
		}
		install_file(rootino, argv[i], shortname,
			     primary ? primary->flags : 0,
			     primary ? primary->role_mask : 0,
			     primary ? primary->vfs_profile :
				       VFS_EXEC_PROFILE_NONE,
			     &host_image);

		for (uint p = 0; p < sizeof(exec_policy) / sizeof(exec_policy[0]);
		     p++) {
			if (strcmp(exec_policy[p].source, shortname) != 0 ||
			    strcmp(exec_policy[p].image, shortname) == 0)
				continue;
			install_file(rootino, argv[i], exec_policy[p].image,
				     exec_policy[p].flags,
				     exec_policy[p].role_mask,
				     exec_policy[p].vfs_profile, &host_image);
		}
		if (host_image.layout.version == EXEC_LAYOUT_VERSION) {
			char worker_image[11];

			exec_manifest_worker_image(shortname, worker_image);
			install_file(rootino, argv[i], worker_image,
				     EXEC_FLAG_IMMUTABLE |
					     EXEC_FLAG_DOMAIN_SAFE,
				     0, VFS_EXEC_PROFILE_WORKFLOW,
				     &host_image);
		}
		free(host_image.data);
	}

	// fix size of root inode dir
	rinode(rootino, &din);
	off = xint(din.size);
	off = ((off / BSIZE) + 1) * BSIZE;
	din.size = xint(off);
	winode(rootino, &din);

	balloc(freeblock);
	return 0;
}

static int valid_policy_name(const char *name)
{
	return name != 0 && name[0] != 0 && index(name, '/') == 0;
}

void validate_exec_policy(void)
{
	for (uint i = 0; i < sizeof(exec_policy) / sizeof(exec_policy[0]); i++) {
		const struct exec_policy_entry *p = &exec_policy[i];
		uint required = EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE |
				EXEC_FLAG_DOMAIN_SAFE;

		if (!valid_policy_name(p->source) || !valid_policy_name(p->image) ||
		    (p->flags & ~EXEC_FLAG_KNOWN) != 0 ||
		    (p->flags & required) != required ||
		    (p->role_mask & ~EXEC_MANIFEST_ROLE_ALL) != 0 ||
		    !vfs_exec_profile_valid(p->vfs_profile) ||
		    ((p->flags & EXEC_FLAG_BOOTSTRAP) != 0 &&
		     p->vfs_profile == VFS_EXEC_PROFILE_NONE) ||
		    ((p->flags & EXEC_FLAG_BOOTSTRAP) != 0 &&
		     p->role_mask == 0)) {
			fprintf(stderr, "mkfs: invalid exec policy row %s -> %s\n",
				p->source, p->image);
			exit(1);
		}
		if (p->launch_role != 0 &&
		    (p->launch_role < 1 || p->launch_role >= 32 ||
		     (p->role_mask & EXEC_ROLE_BIT(p->launch_role)) == 0 ||
		     strcmp(p->source, p->image) == 0 ||
		     strlen(p->image) > DIRSIZ)) {
			fprintf(stderr, "mkfs: invalid launch policy for %s\n",
				p->source);
			exit(1);
		}
		for (uint j = i + 1;
		     j < sizeof(exec_policy) / sizeof(exec_policy[0]); j++) {
			if (strcmp(p->source, exec_policy[j].source) == 0 &&
			    strncmp(p->image, exec_policy[j].image, DIRSIZ) == 0) {
				fprintf(stderr,
					"mkfs: duplicate exec policy image %.14s\n",
					p->image);
				exit(1);
			}
		}
	}
}

static void exec_layout_error(const char *path, const char *reason)
{
	fprintf(stderr, "mkfs: invalid executable layout %s: %s\n", path,
		reason);
	exit(1);
}

static unsigned char *read_host_file(const char *path, size_t *size)
{
	struct stat st;
	unsigned char *data;
	size_t done = 0;
	int fd;

	if (size == 0 || (fd = open(path, O_RDONLY)) < 0) {
		perror(path);
		exit(1);
	}
	if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
	    (uint64_t)st.st_size > (uint64_t)SIZE_MAX) {
		fprintf(stderr, "mkfs: invalid host file %s\n", path);
		exit(1);
	}
	*size = (size_t)st.st_size;
	data = malloc(*size);
	if (data == 0) {
		fprintf(stderr, "mkfs: out of host memory reading %s\n", path);
		exit(1);
	}
	while (done < *size) {
		ssize_t n = read(fd, data + done, *size - done);

		if (n <= 0) {
			perror(path);
			exit(1);
		}
		done += (size_t)n;
	}
	close(fd);
	return data;
}

struct host_image read_host_image(const char *host_path)
{
	char elf_path[PATH_MAX];
	const char *component;
	const char *name;
	unsigned char *binary;
	unsigned char *elf;
	size_t binary_size;
	size_t elf_size;
	Elf64_Ehdr eh;
	Elf64_Phdr rx = { 0 };
	Elf64_Phdr rw = { 0 };
	uint64_t rx_end;
	uint64_t rw_end;
	uint64_t rw_offset;
	int have_rx = 0;
	int have_rw = 0;
	int path_len;
	struct host_image image = { 0 };

	component = strstr(host_path, "/bin/");
	name = component ? component + strlen("/bin/") : 0;
	if (component == 0 || name[0] == 0 || strchr(name, '/') != 0)
		exec_layout_error(host_path, "expected paired bin/ and elf/ paths");
	path_len = snprintf(elf_path, sizeof(elf_path), "%.*s/elf/%s",
			    (int)(component - host_path), host_path, name);
	if (path_len < 0 || (size_t)path_len >= sizeof(elf_path))
		exec_layout_error(host_path, "paired ELF path is too long");
	image.data = read_host_file(host_path, &image.size);
	if (access(elf_path, R_OK) < 0)
		return image;

	binary = image.data;
	binary_size = image.size;
	elf = read_host_file(elf_path, &elf_size);
	if (elf_size < sizeof(eh))
		exec_layout_error(elf_path, "truncated ELF header");
	memcpy(&eh, elf, sizeof(eh));
	if (memcmp(eh.e_ident, ELFMAG, SELFMAG) != 0 ||
	    eh.e_ident[EI_CLASS] != ELFCLASS64 ||
	    eh.e_ident[EI_DATA] != ELFDATA2LSB ||
	    eh.e_ident[EI_VERSION] != EV_CURRENT || eh.e_type != ET_EXEC ||
	    eh.e_machine != EM_RISCV || eh.e_version != EV_CURRENT ||
	    eh.e_entry != USER_IMAGE_BASE ||
	    eh.e_phentsize != sizeof(Elf64_Phdr) || eh.e_phnum == 0 ||
	    eh.e_phnum > 32 || eh.e_phoff > elf_size ||
	    (uint64_t)eh.e_phnum * sizeof(Elf64_Phdr) >
		elf_size - eh.e_phoff)
		exec_layout_error(elf_path, "unsupported ELF header");

	for (uint i = 0; i < eh.e_phnum; i++) {
		Elf64_Phdr ph;
		uint64_t end;

		memcpy(&ph, elf + eh.e_phoff + i * sizeof(ph), sizeof(ph));
		if (ph.p_type != PT_LOAD)
			continue;
		if (ph.p_filesz == 0 || ph.p_filesz != ph.p_memsz ||
		    ph.p_vaddr != ph.p_paddr || ph.p_align != USER_PAGE_SIZE ||
		    ph.p_offset > elf_size || ph.p_filesz > elf_size - ph.p_offset ||
		    ph.p_vaddr > UINT64_MAX - ph.p_memsz)
			exec_layout_error(elf_path, "invalid load segment");
		end = ph.p_vaddr + ph.p_memsz;
		if (end <= ph.p_vaddr)
			exec_layout_error(elf_path, "empty load segment");
		if (ph.p_flags == (PF_R | PF_X) && !have_rx) {
			rx = ph;
			have_rx = 1;
		} else if (ph.p_flags == (PF_R | PF_W) && !have_rw) {
			rw = ph;
			have_rw = 1;
		} else {
			exec_layout_error(elf_path,
					  "expected exactly one RX and one RW segment");
		}
	}
	if (!have_rx || !have_rw || rx.p_vaddr != USER_IMAGE_BASE ||
	    eh.e_entry < rx.p_vaddr || eh.e_entry >= rx.p_vaddr + rx.p_memsz)
		exec_layout_error(elf_path, "missing executable or data segment");
	rx_end = rx.p_vaddr + rx.p_memsz;
	if (rx_end > UINT64_MAX - (USER_PAGE_SIZE - 1) ||
	    rw.p_vaddr !=
		((rx_end + USER_PAGE_SIZE - 1) & ~(USER_PAGE_SIZE - 1)))
		exec_layout_error(elf_path, "RW segment is not page-separated");
	rw_end = rw.p_vaddr + rw.p_memsz;
	rw_offset = rw.p_vaddr - USER_IMAGE_BASE;
	if (rw.p_vaddr < USER_IMAGE_BASE || rw_end < USER_IMAGE_BASE ||
	    rw_end - USER_IMAGE_BASE != binary_size ||
	    rw_offset == 0 || rw_offset > UINT_MAX ||
	    rx.p_filesz > rw_offset || rw_offset > binary_size)
		exec_layout_error(elf_path, "flat binary does not match ELF layout");
	if (memcmp(binary, elf + rx.p_offset, (size_t)rx.p_filesz) != 0 ||
	    memcmp(binary + rw_offset, elf + rw.p_offset,
		   (size_t)rw.p_filesz) != 0)
		exec_layout_error(elf_path, "flat binary contents differ from ELF");
	for (uint64_t off = rx.p_filesz; off < rw_offset; off++)
		if (binary[off] != 0)
			exec_layout_error(elf_path,
					  "nonzero data crosses the W^X boundary");

	free(elf);
	image.layout.version = EXEC_LAYOUT_VERSION;
	image.layout.rw_offset = (uint)rw_offset;
	return image;
}

static void reserve_image_name(const char *name)
{
	if (!valid_policy_name(name)) {
		fprintf(stderr, "mkfs: invalid image name\n");
		exit(1);
	}
	for (int i = 0; i < installed_name_count; i++) {
		if (strncmp(installed_names[i], name, DIRSIZ) == 0) {
			fprintf(stderr, "mkfs: duplicate on-disk name %.14s\n", name);
			exit(1);
		}
	}
	if (installed_name_count >= NINODE) {
		fprintf(stderr, "mkfs: too many directory entries\n");
		exit(1);
	}
	bzero(installed_names[installed_name_count], DIRSIZ);
	strncpy(installed_names[installed_name_count], name, DIRSIZ);
	installed_name_count++;
}

void install_file(uint rootino, const char *host_path, const char *image,
		  uint flags, uint role_mask, uint vfs_profile,
		  const struct host_image *host_image)
{
	uint inum;
	uint vfs_domain;
	uint vfs_flags;
	uint vfs_policy;
	struct dirent de;
	struct dinode din;
	const struct exec_layout *layout;

	if (host_image == 0 || host_image->data == 0 ||
	    host_image->size == 0 ||
	    host_image->size > (size_t)MAXFILE * BSIZE) {
		fprintf(stderr, "mkfs: invalid host image snapshot for %s\n",
			host_path);
		exit(1);
	}
	layout = &host_image->layout;
	vfs_flags = flags == 0 && vfs_profile == VFS_EXEC_PROFILE_NONE ?
		VFS_LABEL_F_PUBLIC : VFS_LABEL_F_PROTECTED;
	vfs_domain = flags == 0 && vfs_profile == VFS_EXEC_PROFILE_NONE ?
		VFS_DOMAIN_PUBLIC : VFS_DOMAIN_WORKFLOW;
	vfs_policy = flags == 0 && vfs_profile == VFS_EXEC_PROFILE_NONE ?
		VFS_POLICY_PUBLIC : VFS_POLICY_WORKFLOW;
	if ((layout->version != 0 &&
	     (layout->version != EXEC_LAYOUT_VERSION ||
	      layout->rw_offset == 0)) ||
	    (flags != 0 && layout->version != EXEC_LAYOUT_VERSION) ||
	    !vfs_exec_profile_valid(vfs_profile) ||
	    (vfs_profile != VFS_EXEC_PROFILE_NONE &&
	     ((flags & (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE)) !=
	      (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE)))) {
		fprintf(stderr, "mkfs: invalid executable layout for %s\n",
			host_path);
		exit(1);
	}
	reserve_image_name(image);
	inum = ialloc(T_FILE);
	bzero(&de, sizeof(de));
	de.inum = xshort(inum);
	strncpy(de.name, image, DIRSIZ);
	iappend(rootino, &de, sizeof(de));
	for (size_t off = 0; off < host_image->size;) {
		size_t remaining = host_image->size - off;
		int chunk = (int)(remaining < BSIZE ? remaining : BSIZE);

		iappend(inum, host_image->data + off, chunk);
		off += (size_t)chunk;
	}

	rinode(inum, &din);
	din.exec_flags = xint(flags);
	din.exec_generation = xint(flags ? EXEC_MANIFEST_VERSION : 0);
	din.exec_role_mask = xint(role_mask);
	din.exec_layout_version = xint(layout->version);
	din.exec_rw_offset = xint(layout->rw_offset);
	din.vfs_magic = xint(VFS_LABEL_MAGIC);
	din.vfs_version = xint(VFS_LABEL_VERSION);
	din.vfs_flags = xint(vfs_flags);
	din.vfs_domain = xint(vfs_domain);
	din.vfs_policy = xint(vfs_policy);
	din.vfs_exec_profile = xint(vfs_profile);
	din.vfs_policy_generation = xint(VFS_POLICY_GENERATION);
	din.vfs_incarnation = xint(1);
	din.vfs_checksum = xint(vfs_label_checksum(
		inum, VFS_LABEL_MAGIC, VFS_LABEL_VERSION,
		vfs_flags, vfs_domain, vfs_policy, vfs_profile,
		VFS_POLICY_GENERATION, 1, FS_OWNER_SYSTEM,
		FS_OWNER_VERSION));
	winode(inum, &din);
}

static int vfs_exec_profile_valid(uint profile)
{
	return profile == VFS_EXEC_PROFILE_NONE ||
	       profile == VFS_EXEC_PROFILE_WORKFLOW ||
	       profile == VFS_EXEC_PROFILE_CONTENT_READ ||
	       profile == VFS_EXEC_PROFILE_ARTIFACT_WRITE;
}

uint vfs_label_checksum(uint inum, uint magic, uint version, uint flags,
			uint domain, uint policy, uint exec_profile,
			uint generation, uint incarnation, uint fs_owner_domain,
			uint fs_owner_version)
{
	uint hash = 2166136261U ^ inum;
	uint words[] = { magic, version, flags, domain, policy, exec_profile,
			 generation, incarnation, fs_owner_domain,
			 fs_owner_version };

	for (uint i = 0; i < sizeof(words) / sizeof(words[0]); i++) {
		hash ^= words[i];
		hash *= 16777619U;
		hash ^= words[i] >> 16;
	}
	return hash ? hash : 1U;
}

void label_inode(uint inum, uint flags, uint domain, uint policy,
		 uint exec_profile)
{
	struct dinode din;

	rinode(inum, &din);
	din.vfs_magic = xint(VFS_LABEL_MAGIC);
	din.vfs_version = xint(VFS_LABEL_VERSION);
	din.vfs_flags = xint(flags);
	din.vfs_domain = xint(domain);
	din.vfs_policy = xint(policy);
	din.vfs_exec_profile = xint(exec_profile);
	din.vfs_policy_generation = xint(VFS_POLICY_GENERATION);
	din.vfs_incarnation = xint(1);
	din.fs_owner_domain = xint(FS_OWNER_SYSTEM);
	din.fs_owner_version = xint(FS_OWNER_VERSION);
	din.vfs_checksum = xint(vfs_label_checksum(
		inum, VFS_LABEL_MAGIC, VFS_LABEL_VERSION, flags, domain,
		policy, exec_profile, VFS_POLICY_GENERATION, 1,
		FS_OWNER_SYSTEM, FS_OWNER_VERSION));
	winode(inum, &din);
}

char *basename(char *path)
{
	while (index(path, '/') != 0) {
		path = index(path, '/') + 1;
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
	bzero(&din, sizeof(din));
	din.type = xshort(type);
	din.size = xint(0);
	din.fs_owner_domain = xint(FS_OWNER_SYSTEM);
	din.fs_owner_version = xint(FS_OWNER_VERSION);
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
		bzero(buf, BSIZE);
		for (int i = 0; i < limit; i++)
			buf[i / 8] |= 0x1 << (i % 8);
		wsect(xint(sb.bmapstart) + block, buf);
	}
	for (block = 0; block < nqmap; block++) {
		uint base = (uint)block * QPB;

		bzero(buf, BSIZE);
		for (uint i = 0; i < QPB && base + i < FSSIZE; i++)
			if (base + i >= nmeta && base + i < used)
				((uint *)buf)[i] = xint(FS_OWNER_SYSTEM);
		wsect(xint(sb.qmapstart) + block, buf);
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
		bcopy(p, buf + off - (fbn * BSIZE), n1);
		wsect(x, buf);
		n -= n1;
		off += n1;
		p += n1;
	}
	din.size = xint(off);
	winode(inum, &din);
}
