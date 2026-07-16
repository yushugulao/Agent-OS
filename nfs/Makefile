.DEFAULT_GOAL = fs.img

U := ../user
USER_BIN_DIR := target/bin
USER_ELF_DIR := target/elf
EXEC_POLICY := $(U)/include/exec_policy_manifest.h
USER_BINS := $(wildcard $(U)/$(USER_BIN_DIR)/*)
USER_ELFS := $(wildcard $(U)/$(USER_ELF_DIR)/*)

FS_FUSE := fs
HOSTCC ?= cc

.PHONY: .FORCE clean

$(FS_FUSE): fs.c fs.h types.h $(EXEC_POLICY)
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(HOSTCC) fs.c -o "$$tmp"; \
		mv -f "$$tmp" $@; \
		trap - 0 1 2 3 15

fs.img: .FORCE $(FS_FUSE) $(USER_BINS) $(USER_ELFS) $(EXEC_POLICY)
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		./$(FS_FUSE) "$$tmp" $(USER_BINS); \
		mv -f "$$tmp" $@; \
		trap - 0 1 2 3 15

.FORCE:

clean:
	rm -f *.img $(FS_FUSE)
