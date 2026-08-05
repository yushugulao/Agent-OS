.DEFAULT_GOAL = fs.img

ifeq ($(FUNCTIONAL_REVIEW_BUILD),1)
override FUNCTIONAL_REVIEW_FORBIDDEN_IMAGE_VARS := \
	U USER_BIN_DIR USER_ELF_DIR HOSTCC CFLAGS CPPFLAGS LDFLAGS \
	STORAGE_POLICY_CPPFLAGS FS_FUSE USER_BINS USER_ELFS EXEC_POLICY
override FUNCTIONAL_REVIEW_EXTERNAL_IMAGE_VARS := $(strip $(foreach variable,$(FUNCTIONAL_REVIEW_FORBIDDEN_IMAGE_VARS),$(if $(filter command line environment override,$(origin $(variable))),$(variable))))
ifneq ($(FUNCTIONAL_REVIEW_EXTERNAL_IMAGE_VARS),)
$(error FUNCTIONAL_REVIEW_BUILD rejects external image variables: $(FUNCTIONAL_REVIEW_EXTERNAL_IMAGE_VARS))
endif
override FUNCTIONAL_REVIEW_USER_CFLAGS := $(USER_EXTRA_CFLAGS)
override FUNCTIONAL_REVIEW_FLAGS_STATUS := $(shell python3 -I -S -B ../scripts/validate-functional-review-flags.py '$(subst ','"'"',$(FUNCTIONAL_REVIEW_USER_CFLAGS))' '$(subst ','"'"',$(CHAPTER))' '$(subst ','"'"',$(CH_TESTS))' '$(subst ','"'"',$(INIT_PROC))' 2>/dev/null)
ifneq ($(FUNCTIONAL_REVIEW_FLAGS_STATUS),ok)
$(error FUNCTIONAL_REVIEW_BUILD rejects USER_EXTRA_CFLAGS or its build context)
endif
override USER_EXTRA_CFLAGS := $(FUNCTIONAL_REVIEW_USER_CFLAGS)
endif

U := ../user
USER_BIN_DIR := target/bin
USER_ELF_DIR := target/elf
EXEC_POLICY := $(U)/include/exec_policy_manifest.h
USER_BINS := $(wildcard $(U)/$(USER_BIN_DIR)/*)
USER_ELFS := $(wildcard $(U)/$(USER_ELF_DIR)/*)

FS_FUSE := fs
HOSTCC ?= cc
STORAGE_POLICY_CPPFLAGS := \
	$(if $(FS_WORKFLOW_BLOCK_RESERVE),-DFS_WORKFLOW_BLOCK_RESERVE=$(FS_WORKFLOW_BLOCK_RESERVE)) \
	$(if $(FS_SYSTEM_BLOCK_RESERVE),-DFS_SYSTEM_BLOCK_RESERVE=$(FS_SYSTEM_BLOCK_RESERVE)) \
	$(if $(FS_WORKFLOW_INODE_RESERVE),-DFS_WORKFLOW_INODE_RESERVE=$(FS_WORKFLOW_INODE_RESERVE)) \
	$(if $(FS_SYSTEM_INODE_RESERVE),-DFS_SYSTEM_INODE_RESERVE=$(FS_SYSTEM_INODE_RESERVE)) \
	$(if $(FS_WORKFLOW_BLOCK_MIN_PER_SCOPE),-DFS_WORKFLOW_BLOCK_MIN_PER_SCOPE=$(FS_WORKFLOW_BLOCK_MIN_PER_SCOPE)) \
	$(if $(FS_WORKFLOW_INODE_MIN_PER_SCOPE),-DFS_WORKFLOW_INODE_MIN_PER_SCOPE=$(FS_WORKFLOW_INODE_MIN_PER_SCOPE)) \
	$(if $(FS_SYSTEM_BLOCK_MIN_RESERVE),-DFS_SYSTEM_BLOCK_MIN_RESERVE=$(FS_SYSTEM_BLOCK_MIN_RESERVE)) \
	$(if $(FS_SYSTEM_INODE_MIN_RESERVE),-DFS_SYSTEM_INODE_MIN_RESERVE=$(FS_SYSTEM_INODE_MIN_RESERVE)) \
	$(if $(FS_STORAGE_TINY_TEST_PROFILE),-DFS_STORAGE_TINY_TEST_PROFILE=$(FS_STORAGE_TINY_TEST_PROFILE))

.PHONY: .FORCE clean $(FS_FUSE)

$(FS_FUSE): fs.c host_image_snapshot.c host_image_snapshot.h \
	host_windows_compat.h elf_compat.h fs.h types.h ../fs_storage_policy.h \
	../agent_metadata_disk_abi.h ../exec_image_policy.h $(EXEC_POLICY)
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(HOSTCC) $(STORAGE_POLICY_CPPFLAGS) fs.c host_image_snapshot.c -o "$$tmp"; \
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
