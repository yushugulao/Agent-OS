#ifndef __NFS_ELF_COMPAT_H__
#define __NFS_ELF_COMPAT_H__

#include <stddef.h>
#include <stdint.h>

#define EI_NIDENT 16
#define EI_CLASS 4
#define EI_DATA 5
#define EI_VERSION 6
#define ELFMAG "\177ELF"
#define SELFMAG 4
#define ELFCLASS64 2
#define ELFDATA2LSB 1
#define EV_CURRENT 1
#define ET_EXEC 2
#define EM_RISCV 243
#define PT_LOAD 1
#define PF_X 1
#define PF_W 2
#define PF_R 4

typedef struct {
	unsigned char e_ident[EI_NIDENT];
	uint16_t e_type;
	uint16_t e_machine;
	uint32_t e_version;
	uint64_t e_entry;
	uint64_t e_phoff;
	uint64_t e_shoff;
	uint32_t e_flags;
	uint16_t e_ehsize;
	uint16_t e_phentsize;
	uint16_t e_phnum;
	uint16_t e_shentsize;
	uint16_t e_shnum;
	uint16_t e_shstrndx;
} Elf64_Ehdr;

typedef struct {
	uint32_t p_type;
	uint32_t p_flags;
	uint64_t p_offset;
	uint64_t p_vaddr;
	uint64_t p_paddr;
	uint64_t p_filesz;
	uint64_t p_memsz;
	uint64_t p_align;
} Elf64_Phdr;

_Static_assert(sizeof(Elf64_Ehdr) == 64 &&
	       offsetof(Elf64_Ehdr, e_phoff) == 32,
	       "ELF64 header compatibility layout");
_Static_assert(sizeof(Elf64_Phdr) == 56 &&
	       offsetof(Elf64_Phdr, p_flags) == 4,
	       "ELF64 program header compatibility layout");

#endif
