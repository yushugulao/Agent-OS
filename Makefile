.PHONY: clean build user user-stack-check run run-prebuilt run-persist debug test doctor kernel-stack-check kernel-budget-check kernel-budget-selftest host-contract-selftest evidence-capture-selftest agent-module-check agent-uapi-check agent-observe-disk-format-check printf-format-static-check printf-format-check ci-check plain-clean plain-platform-build plain-platform-run agentos-user agentos-build agentos-clean agentos-test agentos-platform-user agentos-platform-build agentos-platform-run fs-enospc-test fs-allocator-fault-test proc-reap-test syscall-fairness-test file-resource-test thread-resource-test physical-resource-test workflow-teardown-race-test metadata-recovery-test observe-recovery-test virtio-disk-test reader target-readiness dual-platform-run full-verify dual-clean .FORCE
.DELETE_ON_ERROR:
all: build

K = os
U = user
F = nfs

TOOLPREFIX ?= $(shell if command -v riscv64-unknown-elf-gcc >/dev/null 2>&1; then echo riscv64-unknown-elf-; else echo riscv64-linux-gnu-; fi)
CC = $(TOOLPREFIX)gcc
AS = $(TOOLPREFIX)gcc
LD = $(TOOLPREFIX)ld
OBJCOPY = $(TOOLPREFIX)objcopy
OBJDUMP = $(TOOLPREFIX)objdump
NM = $(TOOLPREFIX)nm
SIZE = $(TOOLPREFIX)size
PYTHON_BIN ?= python3
override PY = $(PYTHON_BIN)
HOST_CC ?= cc
GDB = $(TOOLPREFIX)gdb
CP = cp
BUILDDIR = build
C_SRCS = $(wildcard $K/*.c)
INACTIVE_PROFILE_C_SRCS :=
# Crash-target attestation is a profile-only owner, not a production object.
ifeq ($(strip $(AGENT_METADATA_CRASH_PHASE)$(AGENT_METADATA_EIO_PHASE)),)
C_SRCS := $(filter-out $K/agent_metadata_test.c,$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $K/agent_metadata_test.c
endif
ifeq ($(strip $(AGENT_METADATA_BOOT_READ_FAULT)$(AGENT_METADATA_SELECT_FAULT_BANK)),)
C_SRCS := $(filter-out $K/agent_metadata_recovery_test.c,$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $K/agent_metadata_recovery_test.c
endif
ifeq ($(AGENT_OBSERVE_TEST_PROFILE),)
C_SRCS := $(filter-out $K/agent_observe_test.c,$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $K/agent_observe_test.c
endif
ifeq ($(WAIT_ATOMIC_TEST_PROFILE),)
C_SRCS := $(filter-out $K/wait_atomic_test.c,$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $K/wait_atomic_test.c
endif
ifeq ($(FS_ALLOCATOR_FAULT_TEST_PROFILE),)
C_SRCS := $(filter-out $K/fs_allocator_test.c,$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $K/fs_allocator_test.c
endif
ifeq ($(PHYSICAL_PAGE_TEST_HOOKS),)
C_SRCS := $(filter-out $K/physical_page_test.c,$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $K/physical_page_test.c
endif
INACTIVE_PROFILE_OBJS = $(addprefix $(BUILDDIR)/,$(INACTIVE_PROFILE_C_SRCS:.c=.o))
AS_SRCS = $(wildcard $K/*.S)
C_OBJS = $(addprefix $(BUILDDIR)/, $(addsuffix .o, $(basename $(C_SRCS))))
AS_OBJS = $(addprefix $(BUILDDIR)/, $(addsuffix .o, $(basename $(AS_SRCS))))
OBJS = $(sort $(C_OBJS) $(AS_OBJS))

HEADER_DEP = $(addsuffix .d, $(basename $(C_OBJS)))

ifeq (,$(findstring initproc.o,$(OBJS)))
	AS_OBJS += $(BUILDDIR)/$K/initproc.o
endif

INIT_PROC ?= usershell

$(BUILDDIR)/$(K)/initproc.o: $(K)/initproc.S .FORCE
$(K)/initproc.S: scripts/initproc.py .FORCE
	@$(PY) scripts/initproc.py $(INIT_PROC)

CFLAGS = -Wall -Werror -O -fno-omit-frame-pointer -ggdb
CFLAGS += -MD
CFLAGS += -march=rv64imac_zicsr_zifencei -mabi=lp64
CFLAGS += -mcmodel=medany
CFLAGS += -ffreestanding -fno-common -nostdlib -mno-relax
CFLAGS += -I$K
CFLAGS += $(shell $(CC) -fno-stack-protector -E -x c /dev/null >/dev/null 2>&1 && echo -fno-stack-protector)

KSTACK_SIZE_BYTES ?= 16384
KSTACK_BOOT_SIZE_BYTES ?= 65536
KSTACK_BOOT_ROOT ?= main
KSTACK_GUARD_SIZE_BYTES ?= 4096
KSTACK_FRAME_BUDGET ?= $(KSTACK_GUARD_SIZE_BYTES)
KSTACK_SAFETY_MARGIN ?= 4096
KERNELVEC_FRAME_SIZE_BYTES ?= 256
# swtch changes stacks; usertrapret's indirect jump is the stackless trampoline.
KSTACK_STACK_BOUNDARIES ?= swtch
KSTACK_INDIRECT_CALLERS ?= usertrapret
KSTACK_INDIRECT_CALL_EDGES ?= \
	agent_durable_arena_validate=agent_observe_store_validate \
	agent_durable_arena_update_scope=agent_observe_store_update_scope \
	agent_durable_arena_recover=agent_observe_store_recover \
	agent_durable_arena_has_scope=agent_observe_store_has_scope \
	agent_durable_notify_locked=agent_meta_durable_dirty \
	agent_durable_section_replicated=agent_meta_durable_replicated \
	agent_durable_section_active_replicated=agent_meta_durable_active_replicated \
	agent_durable_section_persist_scope=agent_meta_durable_persist_scope \
	agent_durable_section_mirror_scope=agent_observe_store_replicated_scope \
	agent_identity_lease_progress=agent_observe_lease_persist_bridge
# printf can enter panic once; Sv39 walkers visit at most three page-table levels.
KSTACK_RECURSION_BOUNDS ?= printf=2 freewalk=3 uvm_prune_empty_walk=3
KSTACK_POLICY_ARGS = \
	$(foreach fn,$(KSTACK_STACK_BOUNDARIES),--stack-boundary $(fn)) \
	$(foreach fn,$(KSTACK_INDIRECT_CALLERS),--allow-indirect-from $(fn)) \
	$(foreach edge,$(KSTACK_INDIRECT_CALL_EDGES),--indirect-call-edge $(edge)) \
	$(foreach bound,$(KSTACK_RECURSION_BOUNDS),--recursion-bound $(bound))
KSTACK_TRANSLATION_UNIT_ARGS = \
	$(foreach src,$(basename $(notdir $(C_SRCS))),--translation-unit $(src))
override KSTACK_REQUIRED_CFLAGS = -DKSTACK_SIZE=$(KSTACK_SIZE_BYTES)
override KSTACK_REQUIRED_CFLAGS += -DKSTACK_GUARD_SIZE=$(KSTACK_GUARD_SIZE_BYTES)
override KSTACK_REQUIRED_CFLAGS += -DKERNELVEC_FRAME_SIZE=$(KERNELVEC_FRAME_SIZE_BYTES)
override KSTACK_REQUIRED_CFLAGS += -fcallgraph-info=su
override KSTACK_REQUIRED_CFLAGS += -Wframe-larger-than=$(KSTACK_FRAME_BUDGET)
override KSTACK_REQUIRED_CFLAGS += -Wstack-usage=$(KSTACK_FRAME_BUDGET) -Wvla -Walloca
override KSTACK_REQUIRED_CFLAGS += $(shell $(CC) -fstack-clash-protection -E -x c /dev/null >/dev/null 2>&1 && echo -fstack-clash-protection)

ifneq ($(FS_ICACHE_SIZE),)
CFLAGS += -DFS_ICACHE_SIZE=$(FS_ICACHE_SIZE)
endif

ifneq ($(FILE_RESOURCE_POOL_SIZE),)
CFLAGS += -DFILE_RESOURCE_POOL_SIZE=$(FILE_RESOURCE_POOL_SIZE)
endif
ifneq ($(FILE_RESOURCE_ORDINARY_LIMIT),)
CFLAGS += -DFILE_RESOURCE_ORDINARY_LIMIT=$(FILE_RESOURCE_ORDINARY_LIMIT)
endif
ifneq ($(FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT),)
CFLAGS += -DFILE_RESOURCE_DOMAIN_ORDINARY_LIMIT=$(FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT)
endif
ifneq ($(FILE_RESOURCE_DOMAIN_RESERVED_LIMIT),)
CFLAGS += -DFILE_RESOURCE_DOMAIN_RESERVED_LIMIT=$(FILE_RESOURCE_DOMAIN_RESERVED_LIMIT)
endif

ifneq ($(THREAD_RESOURCE_POOL_SIZE),)
CFLAGS += -DTHREAD_RESOURCE_POOL_SIZE=$(THREAD_RESOURCE_POOL_SIZE)
endif
ifneq ($(THREAD_RESOURCE_ORDINARY_LIMIT),)
CFLAGS += -DTHREAD_RESOURCE_ORDINARY_LIMIT=$(THREAD_RESOURCE_ORDINARY_LIMIT)
endif
ifneq ($(THREAD_RESOURCE_RESERVED_LIMIT),)
CFLAGS += -DTHREAD_RESOURCE_RESERVED_LIMIT=$(THREAD_RESOURCE_RESERVED_LIMIT)
endif
ifneq ($(THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT),)
CFLAGS += -DTHREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT=$(THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT)
endif
ifneq ($(THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT),)
CFLAGS += -DTHREAD_RESOURCE_DOMAIN_RESERVED_LIMIT=$(THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT)
endif

ifneq ($(PHYSICAL_PAGE_SYSTEM_RESERVE),)
CFLAGS += -DPHYSICAL_PAGE_SYSTEM_RESERVE=$(PHYSICAL_PAGE_SYSTEM_RESERVE)
endif
ifneq ($(PHYSICAL_PAGE_RESERVED_DOMAIN_CAP),)
CFLAGS += -DPHYSICAL_PAGE_RESERVED_DOMAIN_CAP=$(PHYSICAL_PAGE_RESERVED_DOMAIN_CAP)
endif
ifneq ($(PHYSICAL_PAGE_ADDRESSABLE_LIMIT),)
CFLAGS += -DPHYSICAL_PAGE_ADDRESSABLE_LIMIT=$(PHYSICAL_PAGE_ADDRESSABLE_LIMIT)
endif
ifneq ($(PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT),)
CFLAGS += -DPHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT=$(PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT)
endif
ifneq ($(PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT),)
CFLAGS += -DPHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT=$(PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT)
endif
ifneq ($(PHYSICAL_PAGE_ORDINARY_LIMIT),)
CFLAGS += -DPHYSICAL_PAGE_ORDINARY_LIMIT=$(PHYSICAL_PAGE_ORDINARY_LIMIT)
endif
ifneq ($(PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT),)
CFLAGS += -DPHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT=$(PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT)
endif
ifneq ($(PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT),)
CFLAGS += -DPHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT=$(PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT)
endif

ifeq ($(PHYSICAL_PAGE_TEST_HOOKS),1)
CFLAGS += -DPHYSICAL_PAGE_TEST_HOOKS
CFLAGS += -DPHYSICAL_PAGE_TEST_INIT_NAME=\"physicalresource_ucore\"
else ifneq ($(PHYSICAL_PAGE_TEST_HOOKS),)
$(error PHYSICAL_PAGE_TEST_HOOKS must be exactly 1 when enabled)
endif

ifeq ($(AGENT_CONTEXT_SYNC_TEST_PROFILE),1)
CFLAGS += -DAGENT_CONTEXT_SYNC_TEST_PROFILE
else ifneq ($(AGENT_CONTEXT_SYNC_TEST_PROFILE),)
$(error AGENT_CONTEXT_SYNC_TEST_PROFILE must be exactly 1 when enabled)
endif

ifeq ($(AGENT_OBSERVE_TEST_PROFILE),1)
CFLAGS += -DAGENT_OBSERVE_TEST_PROFILE
else ifneq ($(AGENT_OBSERVE_TEST_PROFILE),)
$(error AGENT_OBSERVE_TEST_PROFILE must be exactly 1 when enabled)
endif

ifeq ($(WAIT_ATOMIC_TEST_PROFILE),1)
CFLAGS += -DWAIT_ATOMIC_TEST_PROFILE
else ifneq ($(WAIT_ATOMIC_TEST_PROFILE),)
$(error WAIT_ATOMIC_TEST_PROFILE must be exactly 1 when enabled)
endif

ifeq ($(FS_ALLOCATOR_FAULT_TEST_PROFILE),1)
CFLAGS += -DFS_ALLOCATOR_FAULT_TEST_PROFILE
CFLAGS += -DFS_ALLOCATOR_TEST_INIT_NAME=\"fsallocfault_ucore\"
DURABILITY_POWERCUT_TEST_PROFILE := 1
else ifneq ($(FS_ALLOCATOR_FAULT_TEST_PROFILE),)
$(error FS_ALLOCATOR_FAULT_TEST_PROFILE must be exactly 1 when enabled)
endif

ifeq ($(DURABILITY_POWERCUT_TEST_PROFILE),1)
CFLAGS += -DDURABILITY_POWERCUT_TEST_PROFILE
else ifneq ($(DURABILITY_POWERCUT_TEST_PROFILE),)
$(error DURABILITY_POWERCUT_TEST_PROFILE must be exactly 1 when enabled)
endif

ifeq ($(FS_ALLOCATOR_DELETE_BARRIER_MUTANT),1)
ifneq ($(FS_ALLOCATOR_FAULT_TEST_PROFILE),1)
$(error FS_ALLOCATOR_DELETE_BARRIER_MUTANT requires FS_ALLOCATOR_FAULT_TEST_PROFILE=1)
endif
CFLAGS += -DFS_ALLOCATOR_DELETE_BARRIER_MUTANT
else ifneq ($(FS_ALLOCATOR_DELETE_BARRIER_MUTANT),)
$(error FS_ALLOCATOR_DELETE_BARRIER_MUTANT must be exactly 1 when enabled)
endif

ifneq ($(FS_DOMAIN_BLOCK_LIMIT),)
CFLAGS += -DFS_DOMAIN_BLOCK_LIMIT=$(FS_DOMAIN_BLOCK_LIMIT)
endif
ifneq ($(FS_DOMAIN_INODE_LIMIT),)
CFLAGS += -DFS_DOMAIN_INODE_LIMIT=$(FS_DOMAIN_INODE_LIMIT)
endif
ifneq ($(FS_WORKFLOW_DOMAIN_BLOCK_LIMIT),)
CFLAGS += -DFS_WORKFLOW_DOMAIN_BLOCK_LIMIT=$(FS_WORKFLOW_DOMAIN_BLOCK_LIMIT)
endif
ifneq ($(FS_WORKFLOW_DOMAIN_INODE_LIMIT),)
CFLAGS += -DFS_WORKFLOW_DOMAIN_INODE_LIMIT=$(FS_WORKFLOW_DOMAIN_INODE_LIMIT)
endif
ifneq ($(FS_WORKFLOW_BLOCK_RESERVE),)
CFLAGS += -DFS_WORKFLOW_BLOCK_RESERVE=$(FS_WORKFLOW_BLOCK_RESERVE)
endif
ifneq ($(FS_SYSTEM_BLOCK_RESERVE),)
CFLAGS += -DFS_SYSTEM_BLOCK_RESERVE=$(FS_SYSTEM_BLOCK_RESERVE)
endif
ifneq ($(FS_WORKFLOW_INODE_RESERVE),)
CFLAGS += -DFS_WORKFLOW_INODE_RESERVE=$(FS_WORKFLOW_INODE_RESERVE)
endif
ifneq ($(FS_SYSTEM_INODE_RESERVE),)
CFLAGS += -DFS_SYSTEM_INODE_RESERVE=$(FS_SYSTEM_INODE_RESERVE)
endif
ifneq ($(FS_WORKFLOW_BLOCK_MIN_PER_SCOPE),)
CFLAGS += -DFS_WORKFLOW_BLOCK_MIN_PER_SCOPE=$(FS_WORKFLOW_BLOCK_MIN_PER_SCOPE)
endif
ifneq ($(FS_WORKFLOW_INODE_MIN_PER_SCOPE),)
CFLAGS += -DFS_WORKFLOW_INODE_MIN_PER_SCOPE=$(FS_WORKFLOW_INODE_MIN_PER_SCOPE)
endif
ifneq ($(FS_SYSTEM_BLOCK_MIN_RESERVE),)
CFLAGS += -DFS_SYSTEM_BLOCK_MIN_RESERVE=$(FS_SYSTEM_BLOCK_MIN_RESERVE)
endif
ifneq ($(FS_SYSTEM_INODE_MIN_RESERVE),)
CFLAGS += -DFS_SYSTEM_INODE_MIN_RESERVE=$(FS_SYSTEM_INODE_MIN_RESERVE)
endif
ifneq ($(FS_STORAGE_TINY_TEST_PROFILE),)
CFLAGS += -DFS_STORAGE_TINY_TEST_PROFILE=$(FS_STORAGE_TINY_TEST_PROFILE)
endif

ifneq ($(AGENT_METADATA_CRASH_PHASE),)
CFLAGS += -DAGENT_METADATA_CRASH_PHASE=$(AGENT_METADATA_CRASH_PHASE)
AGENT_METADATA_CRASH_BANK ?= primary
ifeq ($(AGENT_METADATA_CRASH_BANK),primary)
CFLAGS += -DAGENT_METADATA_CRASH_BANK=0
else ifeq ($(AGENT_METADATA_CRASH_BANK),mirror)
CFLAGS += -DAGENT_METADATA_CRASH_BANK=1
else
$(error AGENT_METADATA_CRASH_BANK must be primary or mirror)
endif
endif
ifneq ($(AGENT_METADATA_EIO_PHASE),)
CFLAGS += -DAGENT_METADATA_EIO_PHASE=$(AGENT_METADATA_EIO_PHASE)
AGENT_METADATA_EIO_BANK ?= primary
AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS ?= 0
ifeq ($(AGENT_METADATA_EIO_BANK),primary)
CFLAGS += -DAGENT_METADATA_EIO_BANK=0
else ifeq ($(AGENT_METADATA_EIO_BANK),mirror)
CFLAGS += -DAGENT_METADATA_EIO_BANK=1
else
$(error AGENT_METADATA_EIO_BANK must be primary or mirror)
endif
CFLAGS += -DAGENT_METADATA_EIO_SKIP_SCOPE_COMMITS=$(AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS)
CFLAGS += -DVIRTIO_DISK_FAULT_INJECTION
endif

ifneq ($(AGENT_METADATA_SELECT_FAULT_BANK),)
AGENT_METADATA_SELECT_FAULT_COUNT ?= 3
ifeq ($(AGENT_METADATA_SELECT_FAULT_BANK),bank0)
CFLAGS += -DAGENT_METADATA_SELECT_FAULT_BANK=0
else ifeq ($(AGENT_METADATA_SELECT_FAULT_BANK),bank1)
CFLAGS += -DAGENT_METADATA_SELECT_FAULT_BANK=1
else
$(error AGENT_METADATA_SELECT_FAULT_BANK must be bank0 or bank1)
endif
CFLAGS += -DAGENT_METADATA_SELECT_FAULT_COUNT=$(AGENT_METADATA_SELECT_FAULT_COUNT)
endif

ifneq ($(AGENT_METADATA_BOOT_READ_FAULT),)
AGENT_METADATA_BOOT_READ_FAULT_COUNT ?= 2
AGENT_METADATA_BOOT_READ_FAULT_BANK ?= all
ifeq ($(AGENT_METADATA_BOOT_READ_FAULT),busy)
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT=1
else ifeq ($(AGENT_METADATA_BOOT_READ_FAULT),io)
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT=2
else ifeq ($(AGENT_METADATA_BOOT_READ_FAULT),interrupted)
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT=3
else
$(error AGENT_METADATA_BOOT_READ_FAULT must be busy, io, or interrupted)
endif
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT_COUNT=$(AGENT_METADATA_BOOT_READ_FAULT_COUNT)
ifeq ($(AGENT_METADATA_BOOT_READ_FAULT_BANK),all)
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT_BANK=-1
else ifeq ($(AGENT_METADATA_BOOT_READ_FAULT_BANK),bank0)
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT_BANK=0
else ifeq ($(AGENT_METADATA_BOOT_READ_FAULT_BANK),bank1)
CFLAGS += -DAGENT_METADATA_BOOT_READ_FAULT_BANK=1
else
$(error AGENT_METADATA_BOOT_READ_FAULT_BANK must be all, bank0, or bank1)
endif
endif

ifneq ($(VIRTIO_DISK_TEST),)
ifneq ($(VIRTIO_DISK_TEST),1)
$(error VIRTIO_DISK_TEST must be exactly 1 when enabled)
endif
CFLAGS += -DVIRTIO_DISK_FAULT_INJECTION -DVIRTIO_DISK_TEST_PROFILE
CFLAGS += -DVIRTIO_DISK_TEST_INIT_NAME=\"virtiodisk_ucore\"
endif

LOG ?= error

ifeq ($(LOG), error)
CFLAGS += -D LOG_LEVEL_ERROR
else ifeq ($(LOG), warn)
CFLAGS += -D LOG_LEVEL_WARN
else ifeq ($(LOG), info)
CFLAGS += -D LOG_LEVEL_INFO
else ifeq ($(LOG), debug)
CFLAGS += -D LOG_LEVEL_DEBUG
else ifeq ($(LOG), trace)
CFLAGS += -D LOG_LEVEL_TRACE
endif

# Disable PIE when possible (for Ubuntu 16.10 toolchain)
ifneq ($(shell $(CC) -dumpspecs 2>/dev/null | grep -e '[^f]no-pie'),)
CFLAGS += -fno-pie -no-pie
endif
ifneq ($(shell $(CC) -dumpspecs 2>/dev/null | grep -e '[^f]nopie'),)
CFLAGS += -fno-pie -nopie
endif

override CFLAGS += $(KSTACK_REQUIRED_CFLAGS)

# These bounded control/persistence owners favor size after ownership splits.
# Keep this allowlist exact; the module checker rejects any expansion.
AGENT_SIZE_OPTIMIZED_MODULES := agent_context_path agent_file_state agent_ipc agent_metadata agent_metadata_actions agent_metadata_catalog agent_metadata_directory agent_metadata_objects agent_metadata_prefetch agent_metadata_probe agent_metadata_query agent_metadata_recovery agent_metadata_scan agent_metadata_store agent_metadata_store_format agent_metadata_store_io agent_observe_capacity agent_observe_ledger agent_observe_recovery agent_observe_store
AGENT_SIZE_OPTIMIZED_OBJS := $(addprefix $(BUILDDIR)/$(K)/,$(addsuffix .o,$(AGENT_SIZE_OPTIMIZED_MODULES)))
$(AGENT_SIZE_OPTIMIZED_OBJS): private CFLAGS += -Os

KSTACK_BUILD_CONFIG = $(BUILDDIR)/.kernel-stack-config
$(KSTACK_BUILD_CONFIG): .FORCE
	@mkdir -p $(@D)
	@rm -f $(INACTIVE_PROFILE_OBJS)
	@printf '%s\n' \
		'CC=$(CC)' \
		'CFLAGS=$(CFLAGS)' \
		'LDFLAGS=$(LDFLAGS)' \
		'AGENT_SIZE_OPTIMIZED_MODULES=$(AGENT_SIZE_OPTIMIZED_MODULES)' \
		'PHYSICAL_PAGE_TEST_HOOKS=$(PHYSICAL_PAGE_TEST_HOOKS)' \
		'AGENT_CONTEXT_SYNC_TEST_PROFILE=$(AGENT_CONTEXT_SYNC_TEST_PROFILE)' \
		'AGENT_OBSERVE_TEST_PROFILE=$(AGENT_OBSERVE_TEST_PROFILE)' \
		'WAIT_ATOMIC_TEST_PROFILE=$(WAIT_ATOMIC_TEST_PROFILE)' \
		'FS_ALLOCATOR_FAULT_TEST_PROFILE=$(FS_ALLOCATOR_FAULT_TEST_PROFILE)' \
		'FS_ALLOCATOR_DELETE_BARRIER_MUTANT=$(FS_ALLOCATOR_DELETE_BARRIER_MUTANT)' \
		'DURABILITY_POWERCUT_TEST_PROFILE=$(DURABILITY_POWERCUT_TEST_PROFILE)' \
		'AGENT_METADATA_CRASH_PHASE=$(AGENT_METADATA_CRASH_PHASE)' \
		'AGENT_METADATA_CRASH_BANK=$(AGENT_METADATA_CRASH_BANK)' \
		'AGENT_METADATA_EIO_PHASE=$(AGENT_METADATA_EIO_PHASE)' \
		'AGENT_METADATA_EIO_BANK=$(AGENT_METADATA_EIO_BANK)' \
		'AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS=$(AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS)' \
		'AGENT_METADATA_SELECT_FAULT_BANK=$(AGENT_METADATA_SELECT_FAULT_BANK)' \
		'AGENT_METADATA_SELECT_FAULT_COUNT=$(AGENT_METADATA_SELECT_FAULT_COUNT)' \
		'AGENT_METADATA_BOOT_READ_FAULT=$(AGENT_METADATA_BOOT_READ_FAULT)' \
		'AGENT_METADATA_BOOT_READ_FAULT_COUNT=$(AGENT_METADATA_BOOT_READ_FAULT_COUNT)' \
		'AGENT_METADATA_BOOT_READ_FAULT_BANK=$(AGENT_METADATA_BOOT_READ_FAULT_BANK)' \
		'KSTACK_SIZE_BYTES=$(KSTACK_SIZE_BYTES)' \
		'KSTACK_BOOT_SIZE_BYTES=$(KSTACK_BOOT_SIZE_BYTES)' \
		'KSTACK_BOOT_ROOT=$(KSTACK_BOOT_ROOT)' \
		'KSTACK_GUARD_SIZE_BYTES=$(KSTACK_GUARD_SIZE_BYTES)' \
		'KSTACK_FRAME_BUDGET=$(KSTACK_FRAME_BUDGET)' \
		'KSTACK_SAFETY_MARGIN=$(KSTACK_SAFETY_MARGIN)' \
		'KERNELVEC_FRAME_SIZE_BYTES=$(KERNELVEC_FRAME_SIZE_BYTES)' \
		'KSTACK_STACK_BOUNDARIES=$(KSTACK_STACK_BOUNDARIES)' \
		'KSTACK_INDIRECT_CALLERS=$(KSTACK_INDIRECT_CALLERS)' \
		'KSTACK_INDIRECT_CALL_EDGES=$(KSTACK_INDIRECT_CALL_EDGES)' \
		'KSTACK_RECURSION_BOUNDS=$(KSTACK_RECURSION_BOUNDS)' > $@.tmp
	@if ! test -r $@ || ! cmp -s $@.tmp $@; then mv $@.tmp $@; else rm $@.tmp; fi

# empty target
.FORCE:

LDFLAGS = -z max-page-size=4096

$(AS_OBJS): $(BUILDDIR)/$K/%.o : $K/%.S
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@

$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c
	@mkdir -p $(@D)
	@rm -f $(patsubst %.o,%.ci,$@)
	$(CC) $(CFLAGS) -c $< -o $@

$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)
-include $(HEADER_DEP)

INIT_PROC ?= usershell

build: $(BUILDDIR)/kernel

$(BUILDDIR)/kernel: $(OBJS) os/kernel.ld scripts/check-kernel-stack-usage.py $(KSTACK_BUILD_CONFIG) Makefile
	$(PY) scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--boot-stack-size $(KSTACK_BOOT_SIZE_BYTES) \
		--boot-root $(KSTACK_BOOT_ROOT) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) \
		$(KSTACK_POLICY_ARGS) $(KSTACK_TRANSLATION_UNIT_ARGS)
	$(LD) $(LDFLAGS) -T os/kernel.ld -o $(BUILDDIR)/kernel $(OBJS)
	$(OBJDUMP) -S $(BUILDDIR)/kernel > $(BUILDDIR)/kernel.asm
	$(OBJDUMP) -t $(BUILDDIR)/kernel | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > $(BUILDDIR)/kernel.sym
	@echo 'Build kernel done'

kernel-stack-check: build/kernel
	@$(PY) scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--boot-stack-size $(KSTACK_BOOT_SIZE_BYTES) \
		--boot-root $(KSTACK_BOOT_ROOT) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) \
		$(KSTACK_POLICY_ARGS) $(KSTACK_TRANSLATION_UNIT_ARGS)

override KERNEL_BUDGET_CONFIG = ci/kernel-budgets.json
override KERNEL_BUDGET_BUILDDIR = build
override STRUCT_PROC_BUDGET_PROBE = $(KERNEL_BUDGET_BUILDDIR)/ci/struct-proc-size.o
override AGENT_CORE_BOUNDARY_PROBE = $(KERNEL_BUDGET_BUILDDIR)/ci/agent-core-boundary.o
override KERNEL_BUDGET_TOOLPREFIX = $(TOOLPREFIX)
override KERNEL_BUDGET_INIT_PROC = agentfinal_ucore
override KERNEL_BUDGET_LOG = warn
override KERNEL_BUDGET_CHAPTER = agent
override KERNEL_BUDGET_PYTHON = $(PYTHON_BIN)
override KERNEL_BUDGET_SUBMAKE = env \
	-u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES \
	-u CFLAGS -u CPPFLAGS -u LDFLAGS -u ASFLAGS \
	make
override KERNEL_BUDGET_MAKE_ARGS = \
	MAKEOVERRIDES= \
	TOOLPREFIX=$(KERNEL_BUDGET_TOOLPREFIX) \
	LOG=$(KERNEL_BUDGET_LOG) \
	INIT_PROC=$(KERNEL_BUDGET_INIT_PROC) \
	CHAPTER=$(KERNEL_BUDGET_CHAPTER) \
	KSTACK_SIZE_BYTES=16384 \
	KSTACK_BOOT_SIZE_BYTES=65536 \
	KSTACK_BOOT_ROOT=main \
	KSTACK_GUARD_SIZE_BYTES=4096 \
	KSTACK_FRAME_BUDGET=4096 \
	KSTACK_SAFETY_MARGIN=4096 \
	KERNELVEC_FRAME_SIZE_BYTES=256 \
	KSTACK_STACK_BOUNDARIES=swtch \
	KSTACK_INDIRECT_CALLERS=usertrapret \
	KSTACK_INDIRECT_CALL_EDGES='agent_durable_arena_validate=agent_observe_store_validate agent_durable_arena_update_scope=agent_observe_store_update_scope agent_durable_arena_recover=agent_observe_store_recover agent_durable_arena_has_scope=agent_observe_store_has_scope agent_durable_notify_locked=agent_meta_durable_dirty agent_durable_section_replicated=agent_meta_durable_replicated agent_durable_section_active_replicated=agent_meta_durable_active_replicated agent_durable_section_persist_scope=agent_meta_durable_persist_scope agent_durable_section_mirror_scope=agent_observe_store_replicated_scope agent_identity_lease_progress=agent_observe_lease_persist_bridge' \
	KSTACK_RECURSION_BOUNDS='printf=2 freewalk=3 uvm_prune_empty_walk=3' \
	FS_ICACHE_SIZE= \
	FILE_RESOURCE_POOL_SIZE= \
	FILE_RESOURCE_ORDINARY_LIMIT= \
	FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT= \
	FILE_RESOURCE_DOMAIN_RESERVED_LIMIT= \
	THREAD_RESOURCE_POOL_SIZE= \
	THREAD_RESOURCE_ORDINARY_LIMIT= \
	THREAD_RESOURCE_RESERVED_LIMIT= \
	THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT= \
	THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT= \
	PHYSICAL_PAGE_SYSTEM_RESERVE= \
	PHYSICAL_PAGE_RESERVED_DOMAIN_CAP= \
	PHYSICAL_PAGE_ADDRESSABLE_LIMIT= \
	PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT= \
	PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT= \
	PHYSICAL_PAGE_ORDINARY_LIMIT= \
	PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT= \
	PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT= \
	PHYSICAL_PAGE_TEST_HOOKS= \
	AGENT_CONTEXT_SYNC_TEST_PROFILE= \
	AGENT_OBSERVE_TEST_PROFILE= \
	WAIT_ATOMIC_TEST_PROFILE= \
	FS_ALLOCATOR_FAULT_TEST_PROFILE= \
	FS_ALLOCATOR_DELETE_BARRIER_MUTANT= \
	DURABILITY_POWERCUT_TEST_PROFILE= \
	AGENT_METADATA_CRASH_PHASE= \
	AGENT_METADATA_CRASH_BANK= \
	AGENT_METADATA_EIO_PHASE= \
	AGENT_METADATA_EIO_BANK= \
	AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS= \
	AGENT_METADATA_SELECT_FAULT_BANK= \
	AGENT_METADATA_SELECT_FAULT_COUNT= \
	AGENT_METADATA_BOOT_READ_FAULT= \
	AGENT_METADATA_BOOT_READ_FAULT_COUNT= \
	AGENT_METADATA_BOOT_READ_FAULT_BANK= \
	VIRTIO_DISK_TEST= \
	FS_DOMAIN_BLOCK_LIMIT= \
	FS_DOMAIN_INODE_LIMIT= \
	FS_WORKFLOW_DOMAIN_BLOCK_LIMIT= \
	FS_WORKFLOW_DOMAIN_INODE_LIMIT= \
	FS_WORKFLOW_BLOCK_RESERVE= \
	FS_SYSTEM_BLOCK_RESERVE= \
	FS_WORKFLOW_INODE_RESERVE= \
	FS_SYSTEM_INODE_RESERVE= \
	FS_WORKFLOW_BLOCK_MIN_PER_SCOPE= \
	FS_WORKFLOW_INODE_MIN_PER_SCOPE= \
	FS_SYSTEM_BLOCK_MIN_RESERVE= \
	FS_SYSTEM_INODE_MIN_RESERVE= \
	FS_STORAGE_TINY_TEST_PROFILE=

$(STRUCT_PROC_BUDGET_PROBE): scripts/probes/struct-proc-size.c $(wildcard $(K)/*.h) $(wildcard *_policy.h) $(KSTACK_BUILD_CONFIG)
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@

$(AGENT_CORE_BOUNDARY_PROBE): $(K)/agent_core.c $(wildcard $(K)/*.h) $(wildcard *_policy.h) $(KSTACK_BUILD_CONFIG)
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -fno-inline -fkeep-static-functions -c $< -o $@

agent-uapi-check: scripts/check-agent-uapi-layout.py scripts/probes/agent-uapi-layout.c ci/agent-uapi-layout.json $(K)/agent.h user/include/agent.h agent_lifecycle_abi.h agent_tool_abi.h agent_metadata_disk_abi.h
	@$(KERNEL_BUDGET_PYTHON) scripts/check-agent-uapi-layout.py \
		--root . --build-dir $(KERNEL_BUDGET_BUILDDIR)/ci \
		--cc $(KERNEL_BUDGET_TOOLPREFIX)gcc \
		--nm $(KERNEL_BUDGET_TOOLPREFIX)nm

agent-module-check: agent-uapi-check scripts/check-agent-module-boundaries.sh scripts/check-teardown-protocol.py scripts/check-metadata-catalog-capacity.py scripts/check-metadata-catalog-rollback-fence.py scripts/check-kernel-budgets.py $(KERNEL_BUDGET_CONFIG)
	@bash scripts/check-agent-module-boundaries.sh
	@$(KERNEL_BUDGET_PYTHON) scripts/check-metadata-catalog-capacity.py --root .
	@$(KERNEL_BUDGET_PYTHON) scripts/check-metadata-catalog-rollback-fence.py --root .
	@$(KERNEL_BUDGET_SUBMAKE) build/kernel $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_SUBMAKE) $(AGENT_CORE_BOUNDARY_PROBE) $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_PYTHON) scripts/check-kernel-budgets.py \
		--check agent-modules --config $(KERNEL_BUDGET_CONFIG) --root . \
		--agent-core-probe $(AGENT_CORE_BOUNDARY_PROBE) \
		--nm $(KERNEL_BUDGET_TOOLPREFIX)nm \
		--size $(KERNEL_BUDGET_TOOLPREFIX)size

kernel-budget-check: agent-uapi-check scripts/check-agent-module-boundaries.sh scripts/check-teardown-protocol.py scripts/check-metadata-catalog-capacity.py scripts/check-metadata-catalog-rollback-fence.py scripts/check-kernel-budgets.py $(KERNEL_BUDGET_CONFIG)
	@bash scripts/check-agent-module-boundaries.sh
	@$(KERNEL_BUDGET_PYTHON) scripts/check-metadata-catalog-capacity.py --root .
	@$(KERNEL_BUDGET_PYTHON) scripts/check-metadata-catalog-rollback-fence.py --root .
	@$(KERNEL_BUDGET_SUBMAKE) build/kernel $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_SUBMAKE) $(STRUCT_PROC_BUDGET_PROBE) $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_SUBMAKE) $(AGENT_CORE_BOUNDARY_PROBE) $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_PYTHON) scripts/check-kernel-budgets.py \
		--check kernel --config $(KERNEL_BUDGET_CONFIG) --root . \
		--kernel $(KERNEL_BUDGET_BUILDDIR)/kernel \
		--struct-probe $(STRUCT_PROC_BUDGET_PROBE) \
		--cc $(KERNEL_BUDGET_TOOLPREFIX)gcc \
		--objcopy $(KERNEL_BUDGET_TOOLPREFIX)objcopy \
		--nm $(KERNEL_BUDGET_TOOLPREFIX)nm \
		--size $(KERNEL_BUDGET_TOOLPREFIX)size \
		--callgraph-dir $(KERNEL_BUDGET_BUILDDIR)/os
	@$(KERNEL_BUDGET_PYTHON) scripts/check-kernel-budgets.py \
		--check agent-modules --config $(KERNEL_BUDGET_CONFIG) --root . \
		--agent-core-probe $(AGENT_CORE_BOUNDARY_PROBE) \
		--nm $(KERNEL_BUDGET_TOOLPREFIX)nm \
		--size $(KERNEL_BUDGET_TOOLPREFIX)size

override KERNEL_BUDGET_PYTHON_SELFTESTS := \
	scripts/test-check-kernel-budgets.py \
	scripts/test-check-user-stack-usage.py \
	scripts/test-check-user-stack-contract.py \
	scripts/test-check-teardown-protocol.py \
	scripts/test-check-agent-uapi-layout.py \
	scripts/test-context-active-path-wiring.py \
	scripts/test-exec-image-policy.py \
	scripts/test-kernel-work-receipt.py \
	scripts/test-agent-metadata-disk-format.py \
	scripts/test-agent-observe-disk-format.py \
	scripts/test-agent-test-runner.py \
	scripts/test-validate-kernel-test-log.py \
	scripts/test-validate-metadata-crash-log.py \
	scripts/test-metadata-boot-reprobe.py \
	scripts/test-metadata-store-authority.py \
	scripts/test-metadata-catalog-capacity.py \
	scripts/test-metadata-catalog-rollback-fence.py \
	scripts/test-validate-metadata-reprobe-log.py \
	scripts/check-wait-queue-contract.py \
	scripts/test-wait-atomic-wiring.py \
	scripts/check-bio-fs-must-check.py \
	scripts/test-bio-background-context.py \
	scripts/test-audit-lease-admission.py \
	scripts/test-observe-span-retention.py \
	scripts/check-fs-allocator-state.py \
	scripts/test-fs-allocator-image.py \
	scripts/test-fs-allocator-evidence.py \
	scripts/test-mkfs-host-snapshot.py \
	scripts/test-observe-recovery-contract.py \
	scripts/test-physical-brk-wiring.py \
	scripts/test-printf-format-contract.py \
	scripts/test-rp-evidence-file-field.py \
	scripts/test-rp-state-append.py \
	scripts/test-resource-kind-policy.py \
	scripts/test-sync-owner-wiring.py \
	scripts/test-validate-virtio-disk-log.py \
	scripts/test-virtio-disk-wiring.py

kernel-budget-selftest: $(KERNEL_BUDGET_PYTHON_SELFTESTS) scripts/check-agent-metadata-disk-format.py scripts/probes/agent-metadata-disk-layout.c ci/agent-metadata-disk-format.json scripts/test-durable-dirty-retry.sh host_tools/gitlab_ci_contract.py agent-observe-disk-format-check printf-format-static-check
	@set -e; for test in $(KERNEL_BUDGET_PYTHON_SELFTESTS); do \
		$(KERNEL_BUDGET_PYTHON) "$$test"; \
	done
	@$(KERNEL_BUDGET_PYTHON) scripts/check-agent-metadata-disk-format.py \
		--cc $(KERNEL_BUDGET_TOOLPREFIX)gcc \
		--objcopy $(KERNEL_BUDGET_TOOLPREFIX)objcopy
	@CC=cc bash scripts/test-durable-dirty-retry.sh

agent-observe-disk-format-check: scripts/check-agent-observe-disk-format.py scripts/probes/agent-observe-disk-layout.c ci/agent-observe-disk-format.json
	@$(KERNEL_BUDGET_PYTHON) scripts/check-agent-observe-disk-format.py \
		--cc $(KERNEL_BUDGET_TOOLPREFIX)gcc \
		--objcopy $(KERNEL_BUDGET_TOOLPREFIX)objcopy

printf-format-static-check: scripts/check-printf-format-contract.py os/printf.c user/lib/stdio.c
	@$(PYTHON_BIN) scripts/check-printf-format-contract.py --root .

printf-format-check: printf-format-static-check scripts/test-printf-format-contract.py scripts/probes/kernel-printf-integer.c scripts/probes/user-printf-integer.c
	@HOST_CC="$(HOST_CC)" $(PYTHON_BIN) scripts/test-printf-format-contract.py

override HOST_CONTRACT_TESTS := \
	scripts/test-mkfs-host-snapshot.py \
	host_tools/test_check_host_platform_alignment.py \
	host_tools/test_check_host_action_kind_alignment.py \
	host_tools/test_check_seeded_action_state.py \
	host_tools/test_check_host_surface_alignment.py \
	host_tools/test_check_host_test_alignment.py \
	host_tools/test_gitlab_ci_contract.py \
	host_tools/test_remote_ci_evidence.py \
	host_tools/test_agent_observe_disk_evidence.py \
	host_tools/test_plain_ucore_action_runner.py \
	host_tools/test_research_state_manifest.py \
	host_tools/test_plain_ucore_fs_extract.py \
	host_tools/test_plain_ucore_llm_relay.py \
	host_tools/test_llm_relay_mode_contract.py \
	host_tools/test_check_reader_output.py \
	host_tools/test_compare_dual_platform_reader.py \
	host_tools/test_compare_dual_platform_state.py \
	host_tools/test_backend_evidence_contract.py \
	host_tools/test_reference_catalog_contract.py \
	host_tools/test_measured_experiments.py \
	host_tools/test_dual_measurement_source_contract.py \
	host_tools/test_summarize_dual_platform_results.py \
	host_tools/test_result_bundle_contract.py \
	host_tools/test_chart_type_data_contract.py \
	host_tools/test_chart_svg_layout_contract.py \
	host_tools/test_plain_ucore_reader.py

host-contract-selftest: $(HOST_CONTRACT_TESTS)
	@set -e; for test in $(HOST_CONTRACT_TESTS); do \
		$(PYTHON_BIN) "$$test"; \
	done

evidence-capture-selftest: scripts/capture-final-evidence.py scripts/fs-allocator-evidence.py host_tools/agent_metadata_disk_format.py host_tools/agent_observe_disk_acceptance.py host_tools/agent_observe_disk_contract.py host_tools/agent_observe_disk_evidence.py host_tools/agent_observe_disk_fixture.py host_tools/plain_ucore_fs_extract.py ci/agent-metadata-disk-format.json ci/agent-observe-disk-format.json host_tools/measured_experiments.py host_tools/evidence_delivery_contract.py host_tools/dual_state_archive.py host_tools/result_bundle_publication.py host_tools/dual_state_evidence_contract.py host_tools/evidence_semantic_common.py host_tools/evidence_semantic_dual.py host_tools/evidence_semantic_metadata.py host_tools/evidence_semantic_profiles.py host_tools/evidence_semantic_registry.py host_tools/remote_ci_archive.py host_tools/remote_ci_bundle.py host_tools/remote_ci_evidence.py host_tools/remote_ci_job_semantics.py host_tools/remote_ci_test_fixture.py host_tools/test_capture_final_evidence.py host_tools/test_evidence_delivery_contract.py
	@$(PYTHON_BIN) host_tools/test_capture_final_evidence.py
	@$(PYTHON_BIN) host_tools/test_evidence_delivery_contract.py

ci-check: host-contract-selftest evidence-capture-selftest kernel-budget-selftest kernel-budget-check user-stack-check

clean:
	make -C $(U) clean
	rm -rf $(BUILDDIR) os/initproc.S
	rm -f $(F)/*.img $(F)/fs

# BOARD
BOARD		?= qemu
SBI			?= opensbi
ifeq ($(SBI), rustsbi)
BOOTLOADER	:= ./bootloader/rustsbi-qemu.bin
else
BOOTLOADER	:= default
endif

QEMU ?= qemu-system-riscv64
QEMUOPTS = \
	-nographic \
	-machine virt \
	-bios $(BOOTLOADER) \
	-kernel build/kernel	\
	-drive file=$(F)/fs-copy.img,if=none,format=raw,id=x0 \
    -device virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0

$(F)/fs.img: user .FORCE
	make -C $(F)

$(F)/fs-copy.img: $(F)/fs.img
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(CP) "$<" "$$tmp"; \
		mv -f "$$tmp" "$@"; \
		trap - 0 1 2 3 15

run: build/kernel $(F)/fs-copy.img
	$(QEMU) $(QEMUOPTS)

# Start only already-built artifacts. Host-side observers use this target so
# compiler output can never be interpreted as guest runtime output.
run-prebuilt:
	@test -f build/kernel || { echo "missing prebuilt kernel" >&2; exit 1; }
	@test -f $(F)/fs-copy.img || { echo "missing prebuilt filesystem image" >&2; exit 1; }
	$(QEMU) $(QEMUOPTS)

# Reboot the current writable disk explicitly.  Normal `run` always installs
# the freshly built userspace image so code and manifest updates cannot go stale.
run-persist: build/kernel
	@if [ ! -f "$(F)/fs-copy.img" ]; then $(MAKE) $(F)/fs-copy.img; fi
	$(QEMU) $(QEMUOPTS)

# QEMU's gdb stub command line changed in 0.11
QEMUGDB = $(shell if $(QEMU) -help | grep -q '^-gdb'; \
	then echo "-gdb tcp::15234"; \
	else echo "-s -p 15234"; fi)

debug: build/kernel .gdbinit
	@tmux new-session -d \
		$(QEMU) $(QEMUOPTS) -S $(QEMUGDB) && \
		tmux split-window -h "$(GDB) -ex 'target remote localhost:15234'" && \
		tmux -2 attach-session -d

gdbserver: build/kernel
	$(QEMU) $(QEMUOPTS) -S $(QEMUGDB)

gdbclient:
	$(GDB) -ex "target remote localhost:15234"

CHAPTER ?= $(shell git rev-parse --abbrev-ref HEAD | grep -oP 'ch\K[0-9]' || echo 8)

user:
	make -C user CHAPTER=$(CHAPTER) BASE=$(BASE) \
		USER_EXTRA_CFLAGS='$(USER_EXTRA_CFLAGS)'

user-stack-check:
	$(MAKE) -C user user-stack-check TOOLPREFIX=$(TOOLPREFIX) \
		PYTHON_BIN=$(PYTHON_BIN)

test:
	$(MAKE) user CHAPTER=$(CHAPTER) BASE=$(BASE)
	$(MAKE) run CHAPTER=$(CHAPTER) BASE=$(BASE)

doctor:
	bash scripts/check-dependencies.sh

plain-platform-build:
	rm -f baseline_ucore/$(F)/fs.img baseline_ucore/$(F)/fs-copy.img
	$(MAKE) -C baseline_ucore/user clean
	$(MAKE) -C baseline_ucore user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform
	$(MAKE) -C baseline_ucore nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform
	$(MAKE) -C baseline_ucore build TOOLPREFIX=$(TOOLPREFIX) LOG=warn INIT_PROC=rp_orch CHAPTER=platform

plain-platform-run:
	rm -f baseline_ucore/$(F)/fs.img baseline_ucore/$(F)/fs-copy.img
	$(MAKE) -C baseline_ucore/user clean
	$(MAKE) -C baseline_ucore user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform
	$(MAKE) -C baseline_ucore nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform
	$(MAKE) -C baseline_ucore run TOOLPREFIX=$(TOOLPREFIX) LOG=error INIT_PROC=rp_orch CHAPTER=platform

agentos-user:
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=agent

agentos-build:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -C user clean
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=agent
	$(MAKE) nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=agent
	$(MAKE) build TOOLPREFIX=$(TOOLPREFIX) LOG=warn INIT_PROC=agentfinal_ucore

agentos-test:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-agent-tests.sh

fs-enospc-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-fs-enospc-tests.sh

fs-allocator-fault-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-fs-allocator-fault-tests.sh

proc-reap-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-proc-reap-tests.sh

syscall-fairness-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-syscall-fairness-tests.sh

file-resource-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-file-resource-tests.sh

thread-resource-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-thread-resource-tests.sh

physical-resource-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-physical-resource-tests.sh

workflow-teardown-race-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-workflow-teardown-race-tests.sh

metadata-recovery-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-metadata-recovery-tests.sh

observe-recovery-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-observe-recovery-tests.sh

virtio-disk-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-virtio-disk-tests.sh

agentos-platform-user:
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos

agentos-platform-build:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -C user clean
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos
	$(MAKE) nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos
	$(MAKE) build TOOLPREFIX=$(TOOLPREFIX) LOG=warn INIT_PROC=rp_agentos_orch

agentos-platform-run:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -C user clean
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos
	$(MAKE) nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos
	$(MAKE) run TOOLPREFIX=$(TOOLPREFIX) LOG=error INIT_PROC=rp_agentos_orch CHAPTER=platform_agentos

dual-platform-run:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-dual-platforms.sh

reader:
	PYTHON_BIN=$(PYTHON_BIN) bash scripts/serve-reader.sh

target-readiness:
	PYTHON_BIN=$(PYTHON_BIN) bash scripts/check-target-readiness.sh

full-verify:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-full-verification.sh

agentos-clean:
	$(MAKE) clean

plain-clean:
	$(MAKE) -C baseline_ucore clean

dual-clean: clean plain-clean
