.PHONY: clean build user user-stack-check run run-prebuilt run-persist debug test doctor kernel-stack-check host-contract-selftest local-host-selftests local-check agent-module-check agent-uapi-check printf-format-static-check printf-format-check plain-clean plain-platform-build plain-platform-run agentos-user agentos-build agentos-clean agentos-test agent-live-demo agent-live-demo-check agentos-console-image agentos-console agentos-cli agentos-observe agentos-console-check agentos-console-replay agentos-console-deepseek agentos-harness-image agentos-harness-native-test agentos-nexus-harness-integration agentos-nexus-image agentos-nexus agentos-nexus-demo agentos-nexus-cli agentos-nexus-observe agentos-nexus-harness agentos-nexus-check agentos-nexus-replay agentos-nexus-deepseek contest-demo contest-demo-check agentos-platform-user agentos-platform-build agentos-platform-run ch3-trace-test fs-enospc-test fs-allocator-fault-test fs-epoch-test proc-reap-test syscall-fairness-test file-resource-test thread-resource-test physical-resource-test workflow-teardown-race-test virtio-disk-test dual-platform-run full-verify dual-clean clean-workspace-dry-run clean-workspace .FORCE
.DELETE_ON_ERROR:
unexport BASH_ENV ENV
all: build

K = os
U = user
F = nfs

# Keep results/ out of git-clean pathspecs: because the ignored parent directory
# collapses child pathspecs, naming results/contest-demo would delete all runs.
WORKSPACE_GENERATED_PATHS = \
	build build-* target target-* asm-* \
	user/build user/build-* user/target user/target-* user/asm user/asm-* \
	baseline_ucore/build baseline_ucore/build-* \
	baseline_ucore/target baseline_ucore/target-* baseline_ucore/asm-* \
	baseline_ucore/user/build baseline_ucore/user/build-* \
	baseline_ucore/user/target baseline_ucore/user/target-* \
	baseline_ucore/user/asm baseline_ucore/user/asm-* \
	nfs/*.img nfs/fs nfs/*.exe os/initproc.S \
	baseline_ucore/nfs/*.img baseline_ucore/nfs/fs baseline_ucore/nfs/*.exe \
	baseline_ucore/os/initproc.S .pytest_cache \
	host_tools/__pycache__ scripts/__pycache__

TOOLPREFIX ?= $(shell if command -v riscv64-unknown-elf-gcc >/dev/null 2>&1; then echo riscv64-unknown-elf-; else echo riscv64-linux-gnu-; fi)
CC = $(TOOLPREFIX)gcc
AS = $(TOOLPREFIX)gcc
LD = $(TOOLPREFIX)ld
OBJCOPY = $(TOOLPREFIX)objcopy
OBJDUMP = $(TOOLPREFIX)objdump
NM = $(TOOLPREFIX)nm
SIZE = $(TOOLPREFIX)size
PYTHON_BIN ?= python3
BASH_BIN ?= bash
override PY = $(PYTHON_BIN)
HOST_CC ?= $(if $(strip $(HOSTCC)),$(HOSTCC),cc)
override AGENTOS_JOB_VALUES := \
	1 2 3 4 5 6 7 8 9 10 11 12 \
	13 14 15 16 17 18 19 20 21 22 23 24
override AGENTOS_QEMU_JOB_VALUES := 1 2 3 4 5 6 7 8
ifneq ($(origin AGENTOS_MAX_JOBS),undefined)
ifneq ($(words $(AGENTOS_MAX_JOBS)),1)
$(error AGENTOS_MAX_JOBS must be an integer between 1 and 24)
endif
ifeq ($(filter $(AGENTOS_MAX_JOBS),$(AGENTOS_JOB_VALUES)),)
$(error AGENTOS_MAX_JOBS must be an integer between 1 and 24)
endif
endif
AGENTOS_BUILD_JOBS ?= $(or $(shell $(PYTHON_BIN) -I -S -B scripts/resource-jobs.py --kind build 2>/dev/null),1)
AGENTOS_TEST_JOBS ?= $(or $(shell $(PYTHON_BIN) -I -S -B scripts/resource-jobs.py --kind host 2>/dev/null),1)
AGENTOS_QEMU_JOBS ?= $(or $(shell $(PYTHON_BIN) -I -S -B scripts/resource-jobs.py --kind qemu 2>/dev/null),1)
ifneq ($(words $(AGENTOS_BUILD_JOBS)),1)
$(error AGENTOS_BUILD_JOBS must be an integer between 1 and 24)
endif
ifeq ($(filter $(AGENTOS_BUILD_JOBS),$(AGENTOS_JOB_VALUES)),)
$(error AGENTOS_BUILD_JOBS must be an integer between 1 and 24)
endif
ifneq ($(words $(AGENTOS_TEST_JOBS)),1)
$(error AGENTOS_TEST_JOBS must be an integer between 1 and 24)
endif
ifeq ($(filter $(AGENTOS_TEST_JOBS),$(AGENTOS_JOB_VALUES)),)
$(error AGENTOS_TEST_JOBS must be an integer between 1 and 24)
endif
ifneq ($(words $(AGENTOS_QEMU_JOBS)),1)
$(error AGENTOS_QEMU_JOBS must be an integer between 1 and 8)
endif
ifeq ($(filter $(AGENTOS_QEMU_JOBS),$(AGENTOS_QEMU_JOB_VALUES)),)
$(error AGENTOS_QEMU_JOBS must be an integer between 1 and 8)
endif
# 优先复用外层 GNU make jobserver；串行顶层 make 为各独立递归构建
# 分配配置好的有界工作池。
AGENTOS_SUBMAKE_JOBS = $(if $(filter -j% --jobs=% --jobserver-auth=% --jobserver-fds=%,$(MAKEFLAGS)),,-j$(AGENTOS_BUILD_JOBS))
GDB = $(TOOLPREFIX)gdb
# 上述变量保留原始工具身份；配方和探针须在拼接后缀后整体引用，
# 确保带空格的前缀和宿主工具路径仍是一个 shell 参数。
shell_quote = '$(subst ','"'"',$(1))'
CC_CMD = $(call shell_quote,$(CC))
AS_CMD = $(call shell_quote,$(AS))
LD_CMD = $(call shell_quote,$(LD))
OBJCOPY_CMD = $(call shell_quote,$(OBJCOPY))
OBJDUMP_CMD = $(call shell_quote,$(OBJDUMP))
NM_CMD = $(call shell_quote,$(NM))
SIZE_CMD = $(call shell_quote,$(SIZE))
PYTHON_CMD = $(call shell_quote,$(PYTHON_BIN))
GDB_CMD = $(call shell_quote,$(GDB))
CP = cp
BUILDDIR = build
C_SRCS = $(wildcard $K/*.c)
INACTIVE_PROFILE_C_SRCS :=
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
	@$(PYTHON_CMD) -I -S scripts/initproc.py $(INIT_PROC)

CFLAGS = -Wall -Werror -O -fno-omit-frame-pointer -ggdb
CFLAGS += -MD
CFLAGS += -march=rv64imac_zicsr_zifencei -mabi=lp64
CFLAGS += -mcmodel=medany
CFLAGS += -ffreestanding -fno-common -nostdlib -mno-relax
# 每个符号独立成节，最终镜像只保留从内核入口可达的实现。
CFLAGS += -ffunction-sections -fdata-sections
CFLAGS += -I$K
CFLAGS += $(shell $(CC_CMD) -fno-stack-protector -E -x c /dev/null >/dev/null 2>&1 && echo -fno-stack-protector)

KSTACK_SIZE_BYTES ?= 16384
KSTACK_BOOT_SIZE_BYTES ?= 65536
KSTACK_BOOT_ROOT ?= main
KSTACK_GUARD_SIZE_BYTES ?= 4096
KSTACK_FRAME_BUDGET ?= $(KSTACK_GUARD_SIZE_BYTES)
KSTACK_SAFETY_MARGIN ?= 4096
KERNELVEC_FRAME_SIZE_BYTES ?= 256
# swtch 会切换栈；usertrapret 的间接跳转是无栈蹦床。
KSTACK_STACK_BOUNDARIES ?= swtch
KSTACK_INDIRECT_CALLERS ?= usertrapret
KSTACK_INDIRECT_CALL_EDGES ?= \
	agent_task_deadline_completion=agent_task_bridge_expire \
	agent_task_release_invoke=agent_task_bridge_resource_release \
	agent_task_channel_reclaim=agent_task_bridge_cancel \
	agent_task_channel_consume_one=agent_task_bridge_cancel \
	agent_task_channel_consume_one=agent_task_bridge_validate \
	agent_task_channel_consume_one=agent_task_bridge_submit \
	agent_task_channel_resource=agent_task_bridge_resource_import
# Sv39 遍历最多访问三级页表。
KSTACK_RECURSION_BOUNDS ?= freewalk=3 uvm_prune_empty_walk=3
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
override KSTACK_REQUIRED_CFLAGS += $(shell $(CC_CMD) -fstack-clash-protection -E -x c /dev/null >/dev/null 2>&1 && echo -fstack-clash-protection)

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
ifneq ($(shell $(CC_CMD) -dumpspecs 2>/dev/null | grep -e '[^f]no-pie'),)
CFLAGS += -fno-pie -no-pie
endif
ifneq ($(shell $(CC_CMD) -dumpspecs 2>/dev/null | grep -e '[^f]nopie'),)
CFLAGS += -fno-pie -nopie
endif

override CFLAGS += $(KSTACK_REQUIRED_CFLAGS)

# 冷控制面按体积优化；高频 credit、ring 与 live-query 事件路径保留 -O。
# 白名单必须精确，
# 模块检查器会拒绝任何扩张。
AGENT_SIZE_OPTIMIZED_MODULES := agent_context_path agent_file_state agent_ipc agent_metadata agent_metadata_actions agent_metadata_catalog agent_metadata_directory agent_metadata_objects agent_metadata_query agent_observe_ledger
AGENT_SIZE_OPTIMIZED_OBJS := $(addprefix $(BUILDDIR)/$(K)/,$(addsuffix .o,$(AGENT_SIZE_OPTIMIZED_MODULES)))
$(AGENT_SIZE_OPTIMIZED_OBJS): private CFLAGS += -Os

KSTACK_BUILD_CONFIG = $(BUILDDIR)/.kernel-stack-config
$(KSTACK_BUILD_CONFIG): .FORCE
	@mkdir -p $(@D)
	@rm -f $(INACTIVE_PROFILE_OBJS)
	@printf '%s\n' \
		'CC=$(CC)' \
		'CC1=$(shell $(CC_CMD) -print-prog-name=cc1)' \
		'AS_SUBPROGRAM=$(shell $(CC_CMD) -print-prog-name=as)' \
		'LD=$(LD)' \
		'OBJDUMP=$(OBJDUMP)' \
		'CFLAGS=$(CFLAGS)' \
		'LDFLAGS=$(LDFLAGS)' \
		'AGENT_SIZE_OPTIMIZED_MODULES=$(AGENT_SIZE_OPTIMIZED_MODULES)' \
		'PHYSICAL_PAGE_TEST_HOOKS=$(PHYSICAL_PAGE_TEST_HOOKS)' \
		'AGENT_CONTEXT_SYNC_TEST_PROFILE=$(AGENT_CONTEXT_SYNC_TEST_PROFILE)' \
		'WAIT_ATOMIC_TEST_PROFILE=$(WAIT_ATOMIC_TEST_PROFILE)' \
		'FS_ALLOCATOR_FAULT_TEST_PROFILE=$(FS_ALLOCATOR_FAULT_TEST_PROFILE)' \
		'FS_ALLOCATOR_DELETE_BARRIER_MUTANT=$(FS_ALLOCATOR_DELETE_BARRIER_MUTANT)' \
		'DURABILITY_POWERCUT_TEST_PROFILE=$(DURABILITY_POWERCUT_TEST_PROFILE)' \
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

LDFLAGS = -m elf64lriscv -z max-page-size=4096 --gc-sections

$(AS_OBJS): $(BUILDDIR)/$K/%.o : $K/%.S
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c
	@mkdir -p $(@D)
	@rm -f $(patsubst %.o,%.ci,$@)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)
-include $(HEADER_DEP)

INIT_PROC ?= usershell

ifneq ($(filter -j% --jobs=% --jobserver-auth=% --jobserver-fds=%,$(MAKEFLAGS)),)
build: $(BUILDDIR)/kernel
else
build:
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory $(BUILDDIR)/kernel
endif

$(BUILDDIR)/kernel: $(OBJS) os/kernel.ld scripts/check-kernel-stack-usage.py $(KSTACK_BUILD_CONFIG) Makefile
	$(PYTHON_CMD) -I -S scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--boot-stack-size $(KSTACK_BOOT_SIZE_BYTES) \
		--boot-root $(KSTACK_BOOT_ROOT) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) \
		$(KSTACK_POLICY_ARGS) $(KSTACK_TRANSLATION_UNIT_ARGS)
	$(LD_CMD) $(LDFLAGS) -T os/kernel.ld -o $(BUILDDIR)/kernel $(OBJS)
	$(OBJDUMP_CMD) -S $(BUILDDIR)/kernel > $(BUILDDIR)/kernel.asm
	$(OBJDUMP_CMD) -t $(BUILDDIR)/kernel | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > $(BUILDDIR)/kernel.sym
	@echo 'Build kernel done'

kernel-stack-check: build/kernel
	@$(PYTHON_CMD) -I -S scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--boot-stack-size $(KSTACK_BOOT_SIZE_BYTES) \
		--boot-root $(KSTACK_BOOT_ROOT) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) \
		$(KSTACK_POLICY_ARGS) $(KSTACK_TRANSLATION_UNIT_ARGS)

override AGENT_CHECK_BUILDDIR := build/agent-check

agent-uapi-check: scripts/check-agent-uapi-layout.py scripts/probes/agent-uapi-layout.c ci/agent-uapi-layout.json $(K)/agent.h user/include/agent.h include/agent_execution_contract_abi.h include/agent_file_publish_abi.h include/agent_lifecycle_abi.h include/agent_provenance_abi.h include/agent_task_channel_abi.h include/agent_tool_abi.h include/agent_workflow_fence_abi.h include/agent_workspace_mutation_abi.h include/agent_performance_abi.h include/agent_resource_abi.h
	@$(PYTHON_CMD) scripts/check-agent-uapi-layout.py \
		--root . --build-dir $(AGENT_CHECK_BUILDDIR) \
		--cc $(CC_CMD) --nm $(NM_CMD)

agent-module-check: agent-uapi-check scripts/check-agent-module-boundaries.sh scripts/check-agent-live-query-fs.py scripts/check-workflow-fence.py
	@bash scripts/check-agent-module-boundaries.sh
	@$(PYTHON_CMD) -I -S -B scripts/check-agent-live-query-fs.py
	@$(PYTHON_CMD) -I -S -B scripts/check-workflow-fence.py

override PRODUCT_STATIC_CHECKS := \
	scripts/check-agent-file-generation-index.py \
	scripts/check-agent-file-version-sparse.py \
	scripts/check-vfs-scope-registry.py \
	scripts/check-agent-live-query-fs.py \
	scripts/check-workflow-fence.py

override PRODUCT_STATIC_TESTS := \
	scripts/test-agent-file-generation-index.py \
	scripts/test-agent-file-version-sparse.py \
	scripts/test-check-user-stack-usage.py \
	scripts/test-check-user-stack-contract.py \
	scripts/test-check-teardown-protocol.py \
	scripts/test-agent-lifecycle-copy.py \
	scripts/test-workflow-teardown-lifecycle-view.py \
	scripts/test-check-agent-uapi-layout.py \
	scripts/test-agent-execution-contract.py \
	scripts/test-agent-live-loop.py \
	scripts/test-agent-file-publish-atomicity.py \
	scripts/test-agent-task-channel.py \
	scripts/test-agent-direct-denial-evidence.py \
	scripts/test-context-evidence-atomicity.py \
	scripts/test-context-snapshot-reader-atomicity.py \
	scripts/test-agent-direct-syscall-provenance.py \
	scripts/test-agent-provenance-monotonicity.py \
	host_tools/test_workflow_scheduler_model.py \
	scripts/test-context-active-path-wiring.py \
	scripts/test-exec-image-policy.py \
	scripts/test-agent-live-query-fs.py \
	scripts/test-agent-evidence-ring.py \
	scripts/test-workflow-credit-domain.py \
	scripts/test-workflow-fence.py \
	scripts/test-workflow-syscall-cut.py \
	scripts/test-host-probe-toolchain.py \
	scripts/test-agent-test-runner.py \
	scripts/test-ch3-trace-acceptance.py \
	scripts/test-validate-kernel-test-log.py \
	scripts/check-wait-queue-contract.py \
	scripts/test-wait-atomic-wiring.py \
	scripts/check-bio-fs-must-check.py \
	scripts/test-bio-background-context.py \
	scripts/test-cache-index-wiring.py \
	scripts/test-buffer-cache-victim-index.py \
	scripts/test-bio-deferred-sponsor.py \
	scripts/test-bio-overwrite-cache.py \
	scripts/test-fs-epoch-sponsor.py \
	scripts/check-fs-epoch-index.py \
	scripts/test-fs-overwrite-fastpath.py \
	scripts/check-copyoutv-window.py \
	scripts/test-io-work-conserving-wiring.py \
	scripts/check-syscall-file-transaction.py \
	scripts/check-traditional-io-fastpath.py \
	scripts/test-traditional-io-fastpath.py \
	scripts/check-open-file-io-lease.py \
	scripts/test-lazy-bio-admission.py \
	scripts/check-inode-mapping-guard.py \
	scripts/check-read-epoch-lazy-finalizer.py \
	scripts/check-close-lazy-finalizer.py \
	scripts/check-background-dispatch-fastpath.py \
	scripts/check-filepool-freelist.py \
	scripts/check-vm-page-table-fastpath.py \
	scripts/check-fs-allocator-state.py \
	scripts/test-fs-dentry-index.py \
	scripts/test-fs-allocator-image.py \
	scripts/test-fs-epoch-regression.py \
	scripts/test-mkfs-host-snapshot.py \
	scripts/test-printf-format-contract.py \
	scripts/test-rp-evidence-file-field.py \
	scripts/test-rp-state-append.py \
	scripts/test-sync-owner-wiring.py \
	scripts/test-validate-virtio-disk-log.py \
	scripts/check-sequential-read-batch.py \
	scripts/test-virtio-disk-wiring.py \
	scripts/test-parallel-test-runner.py \
	scripts/test-resource-jobs.py \
	scripts/test-bio-rate-controller.py

printf-format-static-check: scripts/check-printf-format-contract.py os/printf.c user/lib/stdio.c
	@$(PYTHON_CMD) scripts/check-printf-format-contract.py --root .

printf-format-check: printf-format-static-check scripts/test-printf-format-contract.py scripts/probes/kernel-printf-integer.c scripts/probes/user-printf-integer.c
	@HOST_CC=$(call shell_quote,$(HOST_CC)) $(PYTHON_CMD) scripts/test-printf-format-contract.py

override HOST_PRODUCT_TESTS := \
	scripts/test-mkfs-host-snapshot.py \
	scripts/test-agent-feature-guest-wiring.py \
	scripts/test-agent-file-publish-guest.py \
	scripts/test-trap-callgraph-separation.py \
	host_tools/test_check_host_action_kind_alignment.py \
	host_tools/test_check_seeded_action_state.py \
	host_tools/test_check_host_surface_alignment.py \
	host_tools/test_check_host_test_alignment.py \
	host_tools/test_agent_task_transport.py \
	host_tools/test_agentos_console.py \
	host_tools/test_agentos_nexus_dev.py \
	host_tools/test_agentos_nexus_dev_replay.py \
	host_tools/test_agentos_nexus_multiagent.py \
	host_tools/test_agentos_native_task_channel.py \
	host_tools/test_agentos_workspace.py \
	host_tools/test_guest_llm_relay.py \
	host_tools/test_mcp_a2a_gateway.py \
	host_tools/test_evaluation_contract.py \
	host_tools/test_plain_ucore_action_runner.py \
	host_tools/test_research_state_manifest.py \
	host_tools/test_plain_ucore_fs_extract.py \
	host_tools/test_contest_demo.py \
	host_tools/test_backend_evidence_contract.py \
	host_tools/test_check_host_platform_alignment.py \
	host_tools/test_compare_dual_platform_state.py \
	host_tools/test_reference_catalog_contract.py \
	host_tools/test_safe_host_paths.py

host-contract-selftest: $(HOST_PRODUCT_TESTS) scripts/run-parallel-tests.py
	@$(PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--timeout 900 \
		--python $(call shell_quote,$(PYTHON_BIN)) \
		$(HOST_PRODUCT_TESTS)

override LOCAL_PRODUCT_TESTS := \
	$(HOST_PRODUCT_TESTS) \
	$(filter-out $(HOST_PRODUCT_TESTS),$(PRODUCT_STATIC_TESTS) $(PRODUCT_STATIC_CHECKS))

local-host-selftests: $(LOCAL_PRODUCT_TESTS) scripts/run-parallel-tests.py printf-format-static-check
	@$(PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--timeout 900 \
		--python $(call shell_quote,$(PYTHON_BIN)) \
		$(LOCAL_PRODUCT_TESTS)

local-check:
	+@$(MAKE) --no-print-directory build
	+@$(MAKE) --no-print-directory agent-module-check
	+@$(MAKE) --no-print-directory local-host-selftests
	+@$(MAKE) --no-print-directory user-stack-check

clean:
	$(MAKE) -rR -C $(U) -f Makefile clean
	rm -rf $(BUILDDIR) os/initproc.S
	rm -f $(F)/*.img $(F)/fs

# BOARD
BOARD		?= qemu
BOOTLOADER	:= default

QEMU ?= qemu-system-riscv64
QEMU_CMD = $(call shell_quote,$(QEMU))
AGENT_LIVE_PROVIDER ?= replay
AGENT_LIVE_REPLAY_FILE ?= ci/agent-live-replay.jsonl
AGENT_LIVE_DEEPSEEK_GOAL := Query Guest workspace file agentlive.note with query_file. If it succeeds use echo to record its size as arg0 and inode as arg1. Then summarize the verified result in under 240 ASCII characters. Call at most one tool per turn.
AGENT_LIVE_REPLAY_GOAL := Inspect AgentOS and exercise the available tools. Then summarize the observed result.
AGENT_LIVE_GOAL ?= $(if $(filter deepseek,$(AGENT_LIVE_PROVIDER)),$(AGENT_LIVE_DEEPSEEK_GOAL),$(AGENT_LIVE_REPLAY_GOAL))
AGENT_LIVE_MODEL ?= $(if $(filter deepseek,$(AGENT_LIVE_PROVIDER)),deepseek-v4-flash,)
AGENT_LIVE_ENDPOINT ?= $(if $(filter deepseek,$(AGENT_LIVE_PROVIDER)),https://api.deepseek.com/chat/completions,)
AGENT_LIVE_API_KEY_ENV ?=
AGENT_LIVE_DEEPSEEK_KEY_CANDIDATES ?= ../deepseek_api.txt ../计算机操作系统能力竞赛/deepseek_api.txt
AGENT_LIVE_DEEPSEEK_KEY_FILE ?= $(firstword $(wildcard $(AGENT_LIVE_DEEPSEEK_KEY_CANDIDATES)))
AGENT_LIVE_API_KEY_FILE ?= $(if $(filter deepseek,$(AGENT_LIVE_PROVIDER)),$(AGENT_LIVE_DEEPSEEK_KEY_FILE),)
AGENT_LIVE_APPROVED_TOOLS ?= $(if $(filter replay,$(AGENT_LIVE_PROVIDER)),send_message,)
AGENT_LIVE_REPLAY_DEP = $(if $(filter replay,$(AGENT_LIVE_PROVIDER)),$(AGENT_LIVE_REPLAY_FILE))
AGENT_LIVE_REPLAY_ARGS = $(if $(filter replay,$(AGENT_LIVE_PROVIDER)),--replay-file $(call shell_quote,$(AGENT_LIVE_REPLAY_FILE)))
AGENT_LIVE_MODEL_ARGS = $(if $(strip $(AGENT_LIVE_MODEL)),--model $(call shell_quote,$(AGENT_LIVE_MODEL)))
AGENT_LIVE_ENDPOINT_ARGS = $(if $(strip $(AGENT_LIVE_ENDPOINT)),--endpoint $(call shell_quote,$(AGENT_LIVE_ENDPOINT)))
AGENT_LIVE_KEY_ARGS = $(if $(strip $(AGENT_LIVE_API_KEY_FILE)),--api-key-file $(call shell_quote,$(AGENT_LIVE_API_KEY_FILE)),$(if $(strip $(AGENT_LIVE_API_KEY_ENV)),--api-key-env $(call shell_quote,$(AGENT_LIVE_API_KEY_ENV))))
AGENT_LIVE_APPROVAL_ARGS = $(foreach tool,$(AGENT_LIVE_APPROVED_TOOLS),--approve-tool $(call shell_quote,$(tool)))
AGENT_LIVE_VERIFY_ARGS = \
	--require-guest-marker $(call shell_quote,agentlive_ucore: discovery=1 rich_overlay=3) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: passed) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: parent passed)
AGENT_LIVE_REPLAY_VERIFY_ARGS = $(if $(filter replay,$(AGENT_LIVE_PROVIDER)),\
	--require-guest-marker $(call shell_quote,agentlive_ucore: query_file=1 echo=1 send_message=1 approved=1) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: reject_unknown=1 reject_bad_args=1 reject_replay=0) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: transcript_turns=5 retained=5 dropped=0) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: relay_rounds_done=1 unknown=1 bad_args=1 replay=0 send_sink=1))
AGENT_LIVE_DEEPSEEK_VERIFY_ARGS = $(if $(and $(filter deepseek,$(AGENT_LIVE_PROVIDER)),$(filter file,$(origin AGENT_LIVE_GOAL))),\
	--require-guest-marker $(call shell_quote,agentlive_ucore: query_file=1 echo=1 send_message=0 approved=0) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: reject_unknown=0 reject_bad_args=0 reject_replay=0) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: transcript_turns=2 retained=2 dropped=0) \
	--require-guest-marker $(call shell_quote,agentlive_ucore: relay_rounds_done=1 unknown=0 bad_args=0 replay=0 send_sink=0))
AGENTOS_CONSOLE_PROVIDER ?= deepseek
AGENTOS_CONSOLE_MODEL ?= $(if $(filter deepseek,$(AGENTOS_CONSOLE_PROVIDER)),deepseek-v4-flash,)
AGENTOS_CONSOLE_ENDPOINT ?= $(if $(filter deepseek,$(AGENTOS_CONSOLE_PROVIDER)),https://api.deepseek.com/chat/completions,)
AGENTOS_CONSOLE_API_KEY_ENV ?=
AGENTOS_CONSOLE_API_KEY_FILE ?= $(if $(filter deepseek,$(AGENTOS_CONSOLE_PROVIDER)),$(AGENT_LIVE_DEEPSEEK_KEY_FILE),)
AGENTOS_CONSOLE_REPLAY_FILE ?= ci/agentos-interactive-replay.jsonl
AGENTOS_CONSOLE_REPLAY_DEP = $(if $(filter replay,$(AGENTOS_CONSOLE_PROVIDER)),$(AGENTOS_CONSOLE_REPLAY_FILE))
AGENTOS_CONSOLE_REPLAY_ARGS = $(if $(filter replay,$(AGENTOS_CONSOLE_PROVIDER)),--replay-file $(call shell_quote,$(AGENTOS_CONSOLE_REPLAY_FILE)))
AGENTOS_CONSOLE_MODEL_ARGS = $(if $(strip $(AGENTOS_CONSOLE_MODEL)),--model $(call shell_quote,$(AGENTOS_CONSOLE_MODEL)))
AGENTOS_CONSOLE_ENDPOINT_ARGS = $(if $(strip $(AGENTOS_CONSOLE_ENDPOINT)),--endpoint $(call shell_quote,$(AGENTOS_CONSOLE_ENDPOINT)))
AGENTOS_CONSOLE_KEY_ARGS = $(if $(strip $(AGENTOS_CONSOLE_API_KEY_FILE)),--api-key-file $(call shell_quote,$(AGENTOS_CONSOLE_API_KEY_FILE)),$(if $(strip $(AGENTOS_CONSOLE_API_KEY_ENV)),--api-key-env $(call shell_quote,$(AGENTOS_CONSOLE_API_KEY_ENV))))
AGENTOS_CONSOLE_PROVIDER_ARGS = \
	--provider $(call shell_quote,$(AGENTOS_CONSOLE_PROVIDER)) \
	$(AGENTOS_CONSOLE_REPLAY_ARGS) $(AGENTOS_CONSOLE_MODEL_ARGS) \
	$(AGENTOS_CONSOLE_ENDPOINT_ARGS) $(AGENTOS_CONSOLE_KEY_ARGS)
AGENTOS_NEXUS_MODEL ?= deepseek-v4-flash
AGENTOS_NEXUS_ENDPOINT ?= https://api.deepseek.com/chat/completions
AGENTOS_NEXUS_API_KEY_FILE ?= $(AGENT_LIVE_DEEPSEEK_KEY_FILE)
AGENTOS_NEXUS_HARNESS_GOAL ?=
AGENTOS_NEXUS_HARNESS_CONFIG ?=
AGENTOS_NEXUS_HARNESS_TIMEOUT ?= 900
AGENTOS_NEXUS_HARNESS_PROGRESS ?= auto
AGENTOS_NEXUS_HARNESS_STATUS_INTERVAL ?= 1.0
AGENTOS_NEXUS_HARNESS_TRACE_FILE ?=
AGENTOS_HARNESS_MAX_BINARY ?= 274432
QEMUOPTS = \
	-nographic \
	-machine virt \
	-bios $(BOOTLOADER) \
	-kernel build/kernel	\
	-drive file=$(F)/fs-copy.img,if=none,format=raw,id=x0 \
    -device virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0

$(F)/fs.img: user .FORCE
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -rR -C $(F) -f Makefile

$(F)/fs-copy.img: $(F)/fs.img
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(CP) "$<" "$$tmp"; \
		mv -f "$$tmp" "$@"; \
		trap - 0 1 2 3 15

run: build/kernel $(F)/fs-copy.img
	$(QEMU_CMD) $(QEMUOPTS)

# 只启动已构建产物；宿主观测器使用此目标，避免把编译输出误判为 Guest 日志。
run-prebuilt:
	@test -f build/kernel || { echo "missing prebuilt kernel" >&2; exit 1; }
	@test -f $(F)/fs-copy.img || { echo "missing prebuilt filesystem image" >&2; exit 1; }
	$(QEMU_CMD) $(QEMUOPTS)

# 显式重启当前可写磁盘；普通 `run` 始终安装新构建的用户镜像，
# 防止代码或清单滞后。
run-persist: build/kernel
	@if [ ! -f "$(F)/fs-copy.img" ]; then $(MAKE) $(AGENTOS_SUBMAKE_JOBS) $(F)/fs-copy.img; fi
	$(QEMU_CMD) $(QEMUOPTS)

# QEMU's gdb stub command line changed in 0.11
QEMUGDB = $(shell if $(QEMU_CMD) -help | grep -q '^-gdb'; \
	then echo "-gdb tcp::15234"; \
	else echo "-s -p 15234"; fi)

debug: build/kernel .gdbinit
	@tmux new-session -d \
		$(QEMU_CMD) $(QEMUOPTS) -S $(QEMUGDB) && \
		tmux split-window -h $(call shell_quote,$(GDB_CMD) -ex 'target remote localhost:15234') && \
		tmux -2 attach-session -d

gdbserver: build/kernel
	$(QEMU_CMD) $(QEMUOPTS) -S $(QEMUGDB)

gdbclient:
	$(GDB_CMD) -ex "target remote localhost:15234"

CHAPTER ?= $(shell git rev-parse --abbrev-ref HEAD | grep -oP 'ch\K[0-9]' || echo 8)

user:
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -rR -C user -f Makefile CHAPTER=$(CHAPTER) BASE=$(BASE) \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		USER_EXTRA_CFLAGS='$(USER_EXTRA_CFLAGS)'

user-stack-check:
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -rR -C user -f Makefile user-stack-check TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN))

test: agentos-test

doctor:
	bash scripts/check-dependencies.sh

plain-platform-build:
	rm -f baseline_ucore/$(F)/fs.img baseline_ucore/$(F)/fs-copy.img
	$(MAKE) -C baseline_ucore/user clean
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) CHAPTER=platform
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore nfs/fs.img TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) CHAPTER=platform
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore build TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) LOG=warn INIT_PROC=rp_orch CHAPTER=platform

plain-platform-run:
	rm -f baseline_ucore/$(F)/fs.img baseline_ucore/$(F)/fs-copy.img
	$(MAKE) -C baseline_ucore/user clean
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) CHAPTER=platform
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore nfs/fs.img TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) CHAPTER=platform
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore run TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) QEMU=$(call shell_quote,$(QEMU)) PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) LOG=error INIT_PROC=rp_orch CHAPTER=platform

agentos-user:
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=agent

agentos-build:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -rR -C user -f Makefile clean
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=agent
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) nfs/fs.img TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=agent
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) build TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) LOG=warn INIT_PROC=agentfinal_ucore

agentos-test:
	@rm -f $(F)/fs.img $(F)/fs-copy.img
	@AGENTOS_BUILD_JOBS=$(call shell_quote,$(AGENTOS_BUILD_JOBS)) \
		HOST_CC=$(call shell_quote,$(HOST_CC)) \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		QEMU=$(call shell_quote,$(QEMU)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) \
		BASH_BIN=$(call shell_quote,$(BASH_BIN)) \
		$(call shell_quote,$(BASH_BIN)) scripts/run-agent-tests.sh

# Build the persistent console Guest once.  Runtime sockets and latest state
# live below the owner-only Host runtime directory, never in this workspace.
agentos-console-image: user/src/agentlive_ucore.c
	@rm -f $(F)/fs.img $(F)/fs-copy.img
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(U) -f Makefile clean
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(U) -f Makefile \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		CHAPTER=agent CH_TESTS=agentlive_ucore
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(F) -f Makefile
	@set -e; tmp="$(F)/fs-copy.img.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(CP) "$(F)/fs.img" "$$tmp"; \
		mv -f "$$tmp" "$(F)/fs-copy.img"; \
		trap - 0 1 2 3 15
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory build \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		LOG=warn INIT_PROC=agentlive_ucore CHAPTER=agent

agentos-console: agentos-console-image host_tools/agentos_console.py host_tools/agentos_relayd.py host_tools/agentos_cli.py host_tools/agentos_local_protocol.py $(AGENTOS_CONSOLE_REPLAY_DEP)
	@if test -n $(call shell_quote,$(AGENTOS_CONSOLE_API_KEY_FILE)) && \
		test -n $(call shell_quote,$(AGENTOS_CONSOLE_API_KEY_ENV)); then \
		echo "AGENTOS_CONSOLE_API_KEY_FILE and AGENTOS_CONSOLE_API_KEY_ENV are mutually exclusive" >&2; \
		exit 2; \
	fi
	@case $(call shell_quote,$(AGENTOS_CONSOLE_PROVIDER)) in \
		replay) ;; \
		openai|anthropic|deepseek) \
			test -n $(call shell_quote,$(AGENTOS_CONSOLE_MODEL)) || \
				{ echo "AGENTOS_CONSOLE_MODEL is required for a live provider" >&2; exit 2; } ;; \
		*) echo "invalid AGENTOS_CONSOLE_PROVIDER" >&2; exit 2 ;; \
	esac
	@$(PYTHON_CMD) -I -S -B host_tools/agentos_console.py run \
		$(AGENTOS_CONSOLE_PROVIDER_ARGS) \
		--qemu $(call shell_quote,$(QEMU)) \
		--kernel $(call shell_quote,$(BUILDDIR)/kernel) \
		--image $(call shell_quote,$(F)/fs-copy.img)

agentos-cli: host_tools/agentos_console.py host_tools/agentos_cli.py host_tools/agentos_local_protocol.py
	@$(PYTHON_CMD) -I -S -B host_tools/agentos_console.py cli --attach latest

agentos-observe: host_tools/agentos_console.py host_tools/agentos_observe.py host_tools/agentos_local_protocol.py
	@$(PYTHON_CMD) -I -S -B host_tools/agentos_console.py observe --attach latest

agentos-console-check: host_tools/test_agentos_console.py scripts/test-agent-live-loop.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_console.py
	@$(PYTHON_CMD) -I -S -B scripts/test-agent-live-loop.py

# Deterministic multi-turn acceptance: one QEMU boot, real Guest tools,
# Context controls, one denied side effect, a safe alternative, and close.
agentos-console-replay: agentos-console-image host_tools/agentos_console.py host_tools/agentos_relayd.py host_tools/agentos_cli.py host_tools/agentos_observe.py host_tools/agentos_local_protocol.py host_tools/validate_agentos_console_replay.py $(AGENTOS_CONSOLE_REPLAY_FILE) ci/agentos-interactive-script.txt
	@set -eu; \
		runtime=$$(mktemp -d /tmp/aoc.XXXXXX); \
		controller="$$runtime/controller.ndjson"; \
		controller_error="$$runtime/controller.stderr"; \
		observer="$$runtime/observer.ndjson"; \
		observer_error="$$runtime/observer.stderr"; \
		daemon_log="$$runtime/daemon.log"; \
		daemon_pid=; observer_pid=; \
		cleanup() { \
			status=$$?; \
			trap - 0 1 2 3 15; \
			if test -n "$$observer_pid" && kill -0 "$$observer_pid" 2>/dev/null; then kill "$$observer_pid" 2>/dev/null || true; fi; \
			if test -n "$$daemon_pid" && kill -0 "$$daemon_pid" 2>/dev/null; then kill "$$daemon_pid" 2>/dev/null || true; fi; \
			if test -n "$$observer_pid"; then wait "$$observer_pid" 2>/dev/null || true; fi; \
			if test -n "$$daemon_pid"; then wait "$$daemon_pid" 2>/dev/null || true; fi; \
			if test "$$status" -ne 0; then \
				printf '%s\n' '--- AgentOS console daemon ---' >&2; test ! -f "$$daemon_log" || cat "$$daemon_log" >&2; \
				printf '%s\n' '--- AgentOS controller ---' >&2; test ! -f "$$controller" || cat "$$controller" >&2; \
				printf '%s\n' '--- AgentOS controller stderr ---' >&2; test ! -f "$$controller_error" || cat "$$controller_error" >&2; \
				printf '%s\n' '--- AgentOS observer ---' >&2; test ! -f "$$observer" || cat "$$observer" >&2; \
				printf '%s\n' '--- AgentOS observer stderr ---' >&2; test ! -f "$$observer_error" || cat "$$observer_error" >&2; \
			fi; \
			runtime_user="$$runtime/agentos-$$(id -u)"; \
			rm -f "$$runtime_user"/control-*.sock "$$runtime_user"/telemetry-*.sock \
				"$$runtime_user"/latest.json "$$runtime_user"/daemon.lock 2>/dev/null || true; \
			rm -f "$$controller" "$$controller_error" "$$observer" "$$observer_error" "$$daemon_log"; \
			rmdir "$$runtime_user" 2>/dev/null || true; \
			rmdir "$$runtime" 2>/dev/null || true; \
			exit "$$status"; \
		}; \
		trap cleanup 0; \
		trap 'exit 130' 1 2 3 15; \
		$(PYTHON_CMD) -I -S -B host_tools/agentos_console.py daemon \
			--provider replay \
			--qemu $(call shell_quote,$(QEMU)) \
			--kernel $(call shell_quote,$(BUILDDIR)/kernel) \
			--image $(call shell_quote,$(F)/fs-copy.img) \
			--replay-file $(call shell_quote,$(AGENTOS_CONSOLE_REPLAY_FILE)) \
			--runtime-dir "$$runtime" --quiet > "$$daemon_log" 2>&1 & \
		daemon_pid=$$!; \
		state="$$runtime/agentos-$$(id -u)/latest.json"; \
		attempt=0; \
		while test ! -s "$$state" && test "$$attempt" -lt 3000; do \
			kill -0 "$$daemon_pid" 2>/dev/null || { printf '%s\n' 'AgentOS daemon exited before publishing state' >&2; exit 1; }; \
			attempt=$$((attempt + 1)); sleep 0.05; \
		done; \
		test -s "$$state" || { printf '%s\n' 'AgentOS daemon state timeout' >&2; exit 1; }; \
		$(PYTHON_CMD) -I -S -B host_tools/agentos_console.py observe \
			--attach latest --state-file "$$state" --json-events \
			--until-event session_closed > "$$observer" 2> "$$observer_error" & \
		observer_pid=$$!; \
		attempt=0; \
		while test ! -s "$$observer" && test "$$attempt" -lt 200; do \
			kill -0 "$$observer_pid" 2>/dev/null || { printf '%s\n' 'AgentOS observer exited before attaching' >&2; exit 1; }; \
			attempt=$$((attempt + 1)); sleep 0.05; \
		done; \
		test -s "$$observer" || { printf '%s\n' 'AgentOS observer attach timeout' >&2; exit 1; }; \
		$(PYTHON_CMD) -I -S -B host_tools/agentos_console.py cli \
			--attach latest --state-file "$$state" \
			--script ci/agentos-interactive-script.txt \
			--json-events --event-timeout 120 > "$$controller" 2> "$$controller_error"; \
		wait "$$observer_pid"; observer_pid=; \
		wait "$$daemon_pid"; daemon_pid=; \
		$(PYTHON_CMD) -I -S -B host_tools/validate_agentos_console_replay.py \
			--controller "$$controller" --observer "$$observer" \
			--fixture $(call shell_quote,$(AGENTOS_CONSOLE_REPLAY_FILE))

agentos-console-deepseek:
	+@$(MAKE) --no-print-directory agentos-console AGENTOS_CONSOLE_PROVIDER=deepseek

# Capability-driven multi-Agent Harness. The objective and optional policy
# document are the only task-specific inputs; the runtime has no demo workflow.
agentos-harness-image: user/src/agentharness_ucore.c user/include/exec_policy_manifest.h
	@rm -f $(F)/fs.img $(F)/fs-copy.img
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(U) -f Makefile clean
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(U) -f Makefile \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		CHAPTER=agent CH_TESTS=agentharness_ucore
	@set -e; binary="$(U)/build/bin/agentharness_ucore"; \
		test -f "$$binary" || { echo "missing Harness Guest binary: $$binary" >&2; exit 1; }; \
		size=$$(wc -c < "$$binary"); \
		test "$$size" -le "$(AGENTOS_HARNESS_MAX_BINARY)" || { \
			echo "agentharness_ucore exceeds uCore MAXFILE: $$size > $(AGENTOS_HARNESS_MAX_BINARY)" >&2; \
			exit 1; \
		}
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(F) -f Makefile
	@set -e; tmp="$(F)/fs-copy.img.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(CP) "$(F)/fs.img" "$$tmp"; \
		mv -f "$$tmp" "$(F)/fs-copy.img"; \
		trap - 0 1 2 3 15
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory build \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		LOG=warn INIT_PROC=agentharness_ucore CHAPTER=agent

agentos-harness-native-test: agentos-harness-image host_tools/test_agentos_native_task_channel.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_native_task_channel.py \
		--integration --qemu $(call shell_quote,$(QEMU)) \
		--kernel $(call shell_quote,$(BUILDDIR)/kernel) \
		--image $(call shell_quote,$(F)/fs-copy.img)

agentos-nexus-harness-integration: agentos-harness-image host_tools/test_agentos_nexus_harness_integration.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_nexus_harness_integration.py \
		--workspace $(call shell_quote,.) \
		--qemu $(call shell_quote,$(QEMU)) \
		--kernel $(call shell_quote,$(BUILDDIR)/kernel) \
		--image $(call shell_quote,$(F)/fs-copy.img)

agentos-nexus-harness: host_tools/agentos_nexus_multiagent.py host_tools/agentos_harness_progress.py host_tools/agentos_native_task_channel.py host_tools/agentos_nexus_dev.py host_tools/agentos_workspace.py
	@test -n $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_GOAL)) || \
		{ echo "AGENTOS_NEXUS_HARNESS_GOAL is required" >&2; exit 2; }
	@test -n $(call shell_quote,$(AGENTOS_NEXUS_API_KEY_FILE)) || \
		{ echo "AGENTOS_NEXUS_API_KEY_FILE is required" >&2; exit 2; }
	@test -f $(call shell_quote,$(AGENTOS_NEXUS_API_KEY_FILE)) || \
		{ echo "AGENTOS_NEXUS_API_KEY_FILE does not exist" >&2; exit 2; }
	+@$(MAKE) --no-print-directory agentos-harness-image 1>&2
	@$(PYTHON_CMD) -I -S -B host_tools/agentos_nexus_multiagent.py \
		--workspace $(call shell_quote,.) \
		--goal $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_GOAL)) \
		--api-key-file $(call shell_quote,$(AGENTOS_NEXUS_API_KEY_FILE)) \
		--endpoint $(call shell_quote,$(AGENTOS_NEXUS_ENDPOINT)) \
		--model $(call shell_quote,$(AGENTOS_NEXUS_MODEL)) \
		--timeout $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_TIMEOUT)) \
		--progress $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_PROGRESS)) \
		--status-interval $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_STATUS_INTERVAL)) $(if $(strip $(AGENTOS_NEXUS_HARNESS_CONFIG)),--config $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_CONFIG))) $(if $(strip $(AGENTOS_NEXUS_HARNESS_TRACE_FILE)),--trace-file $(call shell_quote,$(AGENTOS_NEXUS_HARNESS_TRACE_FILE))) \
		--qemu $(call shell_quote,$(QEMU)) \
		--kernel $(call shell_quote,$(BUILDDIR)/kernel) \
		--image $(call shell_quote,$(F)/fs-copy.img)

# Fixed-role Nexus targets are tombstones. All supported execution uses the
# generic Harness and its persistent native Task Channel Guest.
agentos-nexus-image agentos-nexus agentos-nexus-demo agentos-nexus-cli agentos-nexus-observe agentos-nexus-replay agentos-nexus-deepseek:
	@echo "fixed-role Nexus is retired; use make agentos-nexus-harness" >&2
	@exit 2

agentos-nexus-check: host_tools/test_agentos_nexus_dev.py host_tools/test_agentos_nexus_dev_replay.py host_tools/test_agentos_nexus_multiagent.py host_tools/test_agentos_harness_progress.py host_tools/test_agentos_native_task_channel.py host_tools/test_agentos_workspace.py host_tools/test_guest_llm_relay.py ci/agentos-nexus-dev-replay.jsonl
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_nexus_dev.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_nexus_dev_replay.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_nexus_multiagent.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_harness_progress.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_native_task_channel.py
	@$(PYTHON_CMD) -I -S -B host_tools/test_agentos_workspace.py
	@$(PYTHON_CMD) -B host_tools/test_guest_llm_relay.py

# The Guest owns the conversation, tool selection, Context, and kernel calls.
# The Host relay only carries framed bytes and provider HTTPS; replay uses the
# identical serial path without a network connection or API key.
agent-live-demo: host_tools/guest_llm_relay.py user/src/agentlive_ucore.c $(AGENT_LIVE_REPLAY_DEP)
	@if test -n $(call shell_quote,$(AGENT_LIVE_API_KEY_FILE)) && \
		test -n $(call shell_quote,$(AGENT_LIVE_API_KEY_ENV)); then \
		echo "AGENT_LIVE_API_KEY_FILE and AGENT_LIVE_API_KEY_ENV are mutually exclusive" >&2; \
		exit 2; \
	fi
	@case $(call shell_quote,$(AGENT_LIVE_PROVIDER)) in \
		replay) ;; \
		openai|anthropic|deepseek) \
			test -n $(call shell_quote,$(AGENT_LIVE_MODEL)) || \
				{ echo "AGENT_LIVE_MODEL is required for a live provider" >&2; exit 2; } ;; \
		*) echo "invalid AGENT_LIVE_PROVIDER" >&2; exit 2 ;; \
	esac
	@rm -f $(F)/fs.img $(F)/fs-copy.img
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(U) -f Makefile clean
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(U) -f Makefile \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		CHAPTER=agent CH_TESTS=agentlive_ucore
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory -rR -C $(F) -f Makefile
	@set -e; tmp="$(F)/fs-copy.img.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(CP) "$(F)/fs.img" "$$tmp"; \
		mv -f "$$tmp" "$(F)/fs-copy.img"; \
		trap - 0 1 2 3 15
	+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory build \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		LOG=warn INIT_PROC=agentlive_ucore CHAPTER=agent
	@$(PYTHON_CMD) -I -S -B host_tools/guest_llm_relay.py \
		--provider $(call shell_quote,$(AGENT_LIVE_PROVIDER)) \
		--qemu $(call shell_quote,$(QEMU)) \
		--kernel $(call shell_quote,$(BUILDDIR)/kernel) \
		--image $(call shell_quote,$(F)/fs-copy.img) \
		--goal $(call shell_quote,$(AGENT_LIVE_GOAL)) \
		$(AGENT_LIVE_REPLAY_ARGS) $(AGENT_LIVE_MODEL_ARGS) \
		$(AGENT_LIVE_ENDPOINT_ARGS) $(AGENT_LIVE_KEY_ARGS) \
		$(AGENT_LIVE_APPROVAL_ARGS) $(AGENT_LIVE_VERIFY_ARGS) \
		$(AGENT_LIVE_REPLAY_VERIFY_ARGS) $(AGENT_LIVE_DEEPSEEK_VERIFY_ARGS)

agent-live-demo-check: host_tools/test_guest_llm_relay.py scripts/test-agent-live-loop.py
	@$(PYTHON_CMD) -B host_tools/test_guest_llm_relay.py
	@$(PYTHON_CMD) -B scripts/test-agent-live-loop.py

# 四次隔离 Guest 启动，以 AB/BA 顺序比较同一综合工作流的遍历与索引路径；
# 不读取云端 API 或历史结果。
contest-demo:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		QEMU=$(call shell_quote,$(QEMU)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) \
		MAKE_TOOL=$(call shell_quote,$(MAKE)) \
		$(call shell_quote,$(BASH_BIN)) scripts/run-contest-demo.sh

contest-demo-check: scripts/run-contest-demo.sh host_tools/contest_demo.py host_tools/test_contest_demo.py
	@$(call shell_quote,$(BASH_BIN)) -n scripts/run-contest-demo.sh
	@$(PYTHON_CMD) host_tools/test_contest_demo.py

ch3-trace-test:
	AGENTOS_BUILD_JOBS=$(call shell_quote,$(AGENTOS_BUILD_JOBS)) \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		QEMU=$(call shell_quote,$(QEMU)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) \
		HOST_CC=$(call shell_quote,$(HOST_CC)) \
		$(call shell_quote,$(BASH_BIN)) scripts/run-ch3-trace-test.sh

fs-enospc-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-fs-enospc-tests.sh

fs-allocator-fault-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) /bin/bash --noprofile --norc -p scripts/run-fs-allocator-fault-tests.sh

fs-epoch-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-fs-epoch-tests.sh

proc-reap-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-proc-reap-tests.sh

syscall-fairness-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-syscall-fairness-tests.sh

file-resource-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-file-resource-tests.sh

thread-resource-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-thread-resource-tests.sh

physical-resource-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-physical-resource-tests.sh

workflow-teardown-race-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-workflow-teardown-race-tests.sh

virtio-disk-test:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-virtio-disk-tests.sh

agentos-platform-user:
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=platform_agentos

agentos-platform-build:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -rR -C user -f Makefile clean
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=platform_agentos
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) nfs/fs.img TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=platform_agentos
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) build TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) LOG=warn INIT_PROC=rp_agentos_orch

agentos-platform-run:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -rR -C user -f Makefile clean
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) user TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=platform_agentos
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) nfs/fs.img TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) CHAPTER=platform_agentos
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) run TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) QEMU=$(call shell_quote,$(QEMU)) LOG=error INIT_PROC=rp_agentos_orch CHAPTER=platform_agentos

dual-platform-run:
	TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-dual-platforms.sh

full-verify:
	AGENTOS_BUILD_JOBS=$(call shell_quote,$(AGENTOS_BUILD_JOBS)) \
		AGENTOS_TEST_JOBS=$(call shell_quote,$(AGENTOS_TEST_JOBS)) \
		AGENTOS_QEMU_JOBS=$(call shell_quote,$(AGENTOS_QEMU_JOBS)) \
		HOST_CC=$(call shell_quote,$(HOST_CC)) \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		QEMU=$(call shell_quote,$(QEMU)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) \
		BASH_BIN=$(call shell_quote,$(BASH_BIN)) \
		$(call shell_quote,$(BASH_BIN)) scripts/run-full-verification.sh

agentos-clean:
	$(MAKE) clean

plain-clean:
	$(MAKE) -C baseline_ucore clean

dual-clean: clean plain-clean

# 只预览或删除已忽略且列入白名单的构建产物。
clean-workspace-dry-run:
	git clean -ndX -- $(WORKSPACE_GENERATED_PATHS)

clean-workspace:
	git clean -fdX -- $(WORKSPACE_GENERATED_PATHS)
