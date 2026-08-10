.PHONY: clean build user user-stack-check run run-prebuilt run-persist debug test doctor kernel-stack-check kernel-budget-check kernel-budget-selftest host-contract-selftest evidence-capture-selftest stage-host-selftests stage-check local-host-selftests local-check agent-module-check agent-uapi-check printf-format-static-check printf-format-check plain-clean plain-platform-build plain-platform-run agentos-user agentos-build agentos-clean agentos-test contest-demo contest-demo-check agentos-platform-user agentos-platform-build agentos-platform-run ch3-trace-test fs-enospc-test fs-allocator-fault-test fs-epoch-test proc-reap-test syscall-fairness-test file-resource-test thread-resource-test physical-resource-test workflow-teardown-race-test virtio-disk-test target-readiness dual-platform-run full-verify evaluation-doctor evaluation-smoke evaluation-run evaluation-verify evaluation-kernel-cost evaluation-full-verify evaluation-dashboard evaluation-package evaluation-package-development evaluation-package-verify compatibility-overhead-selftest compatibility-overhead-run dual-clean clean-workspace-dry-run clean-workspace .FORCE
.DELETE_ON_ERROR:
unexport BASH_ENV ENV
all: build

K = os
U = user
F = nfs

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
	baseline_ucore/os/initproc.S results/latest .pytest_cache \
	host_tools/__pycache__ scripts/__pycache__

TOOLPREFIX ?= $(shell if command -v riscv64-unknown-elf-gcc >/dev/null 2>&1; then echo riscv64-unknown-elf-; else echo riscv64-linux-gnu-; fi)
ifeq ($(FUNCTIONAL_REVIEW_BUILD),1)
override FUNCTIONAL_REVIEW_FORBIDDEN_BUILD_VARS := \
	K U F BUILDDIR CC AS LD OBJCOPY OBJDUMP NM SIZE CFLAGS CPPFLAGS LDFLAGS \
	C_SRCS AS_SRCS C_OBJS AS_OBJS OBJS HEADER_DEP \
	FUNCTIONAL_REVIEW_PROFILE_CONTEXT \
	AGENT_CONTEXT_SYNC_TEST_PROFILE AGENT_OBSERVE_TEST_PROFILE \
	WAIT_ATOMIC_TEST_PROFILE FS_ALLOCATOR_FAULT_TEST_PROFILE \
	PHYSICAL_PAGE_TEST_HOOKS DURABILITY_POWERCUT_TEST_PROFILE \
	AGENT_METADATA_CRASH_PHASE AGENT_METADATA_EIO_PHASE \
	AGENT_METADATA_SELECT_FAULT_BANK AGENT_METADATA_BOOT_READ_FAULT
# The reviewed Agent runner needs both halves of one kernel-only atomicity
# profile.  Admit that exact command-line tuple and no independently injected
# profile: the context selector is inert unless both profiles, init image, and
# chapter are all the reviewed runner's exact values.
override FUNCTIONAL_REVIEW_PAIRED_PROFILE_REQUEST := $(strip $(FUNCTIONAL_REVIEW_PROFILE_CONTEXT)|$(AGENT_CONTEXT_SYNC_TEST_PROFILE)|$(WAIT_ATOMIC_TEST_PROFILE)|$(INIT_PROC)|$(CHAPTER)|$(MAKECMDGOALS)|$(USER_EXTRA_CFLAGS))
override FUNCTIONAL_REVIEW_PAIRED_PROFILE_ORIGINS := $(origin FUNCTIONAL_REVIEW_BUILD)|$(origin FUNCTIONAL_REVIEW_PROFILE_CONTEXT)|$(origin AGENT_CONTEXT_SYNC_TEST_PROFILE)|$(origin WAIT_ATOMIC_TEST_PROFILE)|$(origin INIT_PROC)|$(origin CHAPTER)
ifeq ($(FUNCTIONAL_REVIEW_PAIRED_PROFILE_REQUEST),agentfinal-context-sync-atomicity-v1|1|1|agentfinal_ucore|agent|build|)
ifeq ($(FUNCTIONAL_REVIEW_PAIRED_PROFILE_ORIGINS),command line|command line|command line|command line|command line|command line)
override FUNCTIONAL_REVIEW_FORBIDDEN_BUILD_VARS := $(filter-out FUNCTIONAL_REVIEW_PROFILE_CONTEXT AGENT_CONTEXT_SYNC_TEST_PROFILE WAIT_ATOMIC_TEST_PROFILE,$(FUNCTIONAL_REVIEW_FORBIDDEN_BUILD_VARS))
endif
endif
override FUNCTIONAL_REVIEW_EXTERNAL_BUILD_VARS := $(strip $(foreach variable,$(FUNCTIONAL_REVIEW_FORBIDDEN_BUILD_VARS),$(if $(filter command line environment environment\ override,$(origin $(variable))),$(variable))))
ifneq ($(FUNCTIONAL_REVIEW_EXTERNAL_BUILD_VARS),)
$(error FUNCTIONAL_REVIEW_BUILD rejects external build variables: $(FUNCTIONAL_REVIEW_EXTERNAL_BUILD_VARS))
endif
override FUNCTIONAL_REVIEW_USER_CFLAGS := $(USER_EXTRA_CFLAGS)
override FUNCTIONAL_REVIEW_FLAGS_STATUS := $(shell python3 -I -S -B scripts/validate-functional-review-flags.py '$(subst ','"'"',$(FUNCTIONAL_REVIEW_USER_CFLAGS))' '$(subst ','"'"',$(CHAPTER))' '$(subst ','"'"',$(CH_TESTS))' '$(subst ','"'"',$(INIT_PROC))' 2>/dev/null)
ifneq ($(FUNCTIONAL_REVIEW_FLAGS_STATUS),ok)
$(error FUNCTIONAL_REVIEW_BUILD rejects USER_EXTRA_CFLAGS or its build context)
endif
override USER_EXTRA_CFLAGS := $(FUNCTIONAL_REVIEW_USER_CFLAGS)
endif
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
AGENT_TEST_DURATION_PROFILE_ORIGIN := $(origin AGENT_TEST_DURATION_PROFILE)
AGENT_TEST_DURATION_PROFILE ?= none
FULL_VERIFY_AGENT_TEST_DURATION_PROFILE ?= $(if $(filter undefined,$(AGENT_TEST_DURATION_PROFILE_ORIGIN)),local-e3,$(AGENT_TEST_DURATION_PROFILE))
COMPAT_BENCH_CHALLENGE_HEX ?= 0000000000000001
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
# The live-query metadata catalog is deliberately memory-only.  Keep the
# superseded disk catalog pipeline available as reference source, but never
# compile it into a production kernel.
RETIRED_METADATA_C_SRCS := \
	$K/agent_metadata_journal.c \
	$K/agent_metadata_probe.c \
	$K/agent_metadata_recovery.c \
	$K/agent_metadata_recovery_test.c \
	$K/agent_metadata_scan.c \
	$K/agent_metadata_store.c \
	$K/agent_metadata_store_format.c \
	$K/agent_metadata_store_io.c \
	$K/agent_metadata_test.c
C_SRCS := $(filter-out $(RETIRED_METADATA_C_SRCS),$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $(RETIRED_METADATA_C_SRCS)
# Fence-sealed evidence is memory resident.  The former shared disk arena,
# recovery reader, and checkpoint-capacity reservation are not production
# dependencies of Agent admission or evidence queries.
RETIRED_OBSERVE_C_SRCS := \
	$K/agent_durable_section.c \
	$K/agent_observe_capacity.c \
	$K/agent_observe_recovery.c \
	$K/agent_observe_store.c
C_SRCS := $(filter-out $(RETIRED_OBSERVE_C_SRCS),$(C_SRCS))
INACTIVE_PROFILE_C_SRCS += $(RETIRED_OBSERVE_C_SRCS)
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

LDFLAGS = -m elf64lriscv -z max-page-size=4096 --gc-sections

$(AS_OBJS): $(BUILDDIR)/$K/%.o : $K/%.S
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c
	@mkdir -p $(@D)
	@rm -f $(patsubst %.o,%.ci,$@)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)
ifneq ($(FUNCTIONAL_REVIEW_BUILD),1)
-include $(HEADER_DEP)
endif

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

override KERNEL_BUDGET_CONFIG = ci/kernel-budgets.json
override KERNEL_BUDGET_BUILDDIR = build/ci-kernel-budget
override STRUCT_PROC_BUDGET_PROBE = $(KERNEL_BUDGET_BUILDDIR)/ci/struct-proc-size.o
override AGENT_CORE_BOUNDARY_PROBE = $(KERNEL_BUDGET_BUILDDIR)/ci/agent-core-boundary.o
override KERNEL_BUDGET_TOOLPREFIX = $(TOOLPREFIX)
override KERNEL_BUDGET_INIT_PROC = agentfinal_ucore
override KERNEL_BUDGET_LOG = warn
override KERNEL_BUDGET_CHAPTER = agent
override KERNEL_BUDGET_PYTHON = $(PYTHON_BIN)
override KERNEL_BUDGET_PYTHON_CMD = $(call shell_quote,$(KERNEL_BUDGET_PYTHON))
override KERNEL_BUDGET_TOOL_ARGS = \
	--cc $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)gcc) \
	--ld $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)ld) \
	--objcopy $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)objcopy) \
	--objdump $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)objdump) \
	--nm $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)nm) \
	--size $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)size)
override KERNEL_BUDGET_SUBMAKE_JOBS = $(if \
	$(filter -j% --jobs=% --jobserver-auth=% --jobserver-fds=%,$(MAKEFLAGS)),\
	$(filter -j% --jobs=% --jobserver-auth=% --jobserver-fds=%,$(MAKEFLAGS)),\
	-j$(AGENTOS_BUILD_JOBS))
override KERNEL_BUDGET_SUBMAKE = env \
	-u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES -u GNUMAKEFLAGS -u MAKEFILES \
	-u CFLAGS -u CPPFLAGS -u LDFLAGS -u ASFLAGS \
	$(MAKE) $(KERNEL_BUDGET_SUBMAKE_JOBS)
override KERNEL_BUDGET_MAKE_ARGS = \
	MAKEOVERRIDES= \
	FUNCTIONAL_REVIEW_BUILD= \
	BUILDDIR=$(call shell_quote,$(KERNEL_BUDGET_BUILDDIR)) \
	TOOLPREFIX=$(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)) \
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
	KSTACK_INDIRECT_CALLERS='usertrapret' \
	KSTACK_INDIRECT_CALL_EDGES='agent_task_deadline_completion=agent_task_bridge_expire agent_task_release_invoke=agent_task_bridge_resource_release agent_task_channel_reclaim=agent_task_bridge_cancel agent_task_channel_consume_one=agent_task_bridge_cancel agent_task_channel_consume_one=agent_task_bridge_validate agent_task_channel_consume_one=agent_task_bridge_submit agent_task_channel_resource=agent_task_bridge_resource_import' \
	KSTACK_RECURSION_BOUNDS='freewalk=3 uvm_prune_empty_walk=3' \
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
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(AGENT_CORE_BOUNDARY_PROBE): $(K)/agent_core.c $(wildcard $(K)/*.h) $(wildcard *_policy.h) $(KSTACK_BUILD_CONFIG)
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -fno-inline -fkeep-static-functions -c $< -o $@

agent-uapi-check: scripts/check-agent-uapi-layout.py scripts/probes/agent-uapi-layout.c ci/agent-uapi-layout.json $(K)/agent.h user/include/agent.h agent_execution_contract_abi.h agent_lifecycle_abi.h agent_provenance_abi.h agent_task_channel_abi.h agent_tool_abi.h agent_workflow_fence_abi.h agent_metadata_disk_abi.h agent_performance_abi.h agent_resource_abi.h
	@$(KERNEL_BUDGET_PYTHON_CMD) scripts/check-agent-uapi-layout.py \
		--root . --build-dir $(KERNEL_BUDGET_BUILDDIR)/ci \
		--cc $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)gcc) \
		--nm $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)nm)

agent-module-check: agent-uapi-check scripts/check-agent-module-boundaries.sh scripts/check-agent-live-query-fs.py scripts/check-workflow-fence.py scripts/check-kernel-budgets.py $(KERNEL_BUDGET_CONFIG)
	@bash scripts/check-agent-module-boundaries.sh
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/check-agent-live-query-fs.py
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/check-workflow-fence.py
	+@$(KERNEL_BUDGET_SUBMAKE) $(KERNEL_BUDGET_BUILDDIR)/kernel $(KERNEL_BUDGET_MAKE_ARGS)
	+@$(KERNEL_BUDGET_SUBMAKE) $(AGENT_CORE_BOUNDARY_PROBE) $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_PYTHON_CMD) scripts/check-kernel-budgets.py \
		--check agent-modules --config $(KERNEL_BUDGET_CONFIG) --root . \
		--kernel $(KERNEL_BUDGET_BUILDDIR)/kernel \
		--object-dir $(KERNEL_BUDGET_BUILDDIR)/os \
		--callgraph-dir $(KERNEL_BUDGET_BUILDDIR)/os \
		--stack-build-config $(KERNEL_BUDGET_BUILDDIR)/.kernel-stack-config \
		--agent-core-probe $(AGENT_CORE_BOUNDARY_PROBE) \
		$(KERNEL_BUDGET_TOOL_ARGS)

kernel-budget-check: agent-uapi-check scripts/check-agent-module-boundaries.sh scripts/check-agent-live-query-fs.py scripts/check-workflow-fence.py scripts/check-kernel-budgets.py $(KERNEL_BUDGET_CONFIG)
	@bash scripts/check-agent-module-boundaries.sh
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/check-agent-live-query-fs.py
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/check-workflow-fence.py
	+@$(KERNEL_BUDGET_SUBMAKE) $(KERNEL_BUDGET_BUILDDIR)/kernel $(KERNEL_BUDGET_MAKE_ARGS)
	+@$(KERNEL_BUDGET_SUBMAKE) $(STRUCT_PROC_BUDGET_PROBE) $(KERNEL_BUDGET_MAKE_ARGS)
	+@$(KERNEL_BUDGET_SUBMAKE) $(AGENT_CORE_BOUNDARY_PROBE) $(KERNEL_BUDGET_MAKE_ARGS)
	@$(KERNEL_BUDGET_PYTHON_CMD) scripts/check-kernel-budgets.py \
		--check kernel --config $(KERNEL_BUDGET_CONFIG) --root . \
		--kernel $(KERNEL_BUDGET_BUILDDIR)/kernel \
		--struct-probe $(STRUCT_PROC_BUDGET_PROBE) \
		--stack-build-config $(KERNEL_BUDGET_BUILDDIR)/.kernel-stack-config \
		$(KERNEL_BUDGET_TOOL_ARGS) \
		--callgraph-dir $(KERNEL_BUDGET_BUILDDIR)/os
	@$(KERNEL_BUDGET_PYTHON_CMD) scripts/check-kernel-budgets.py \
		--check agent-modules --config $(KERNEL_BUDGET_CONFIG) --root . \
		--kernel $(KERNEL_BUDGET_BUILDDIR)/kernel \
		--object-dir $(KERNEL_BUDGET_BUILDDIR)/os \
		--callgraph-dir $(KERNEL_BUDGET_BUILDDIR)/os \
		--stack-build-config $(KERNEL_BUDGET_BUILDDIR)/.kernel-stack-config \
		--agent-core-probe $(AGENT_CORE_BOUNDARY_PROBE) \
		$(KERNEL_BUDGET_TOOL_ARGS)

override KERNEL_BUDGET_STATIC_CHECKS := \
	scripts/check-agent-file-generation-index.py \
	scripts/check-agent-file-version-sparse.py \
	scripts/check-vfs-scope-registry.py \
	scripts/check-agent-live-query-fs.py \
	scripts/check-workflow-fence.py

override KERNEL_BUDGET_PYTHON_SELFTESTS := \
	scripts/test-check-kernel-budgets.py \
	scripts/test-agent-file-generation-index.py \
	scripts/test-agent-file-version-sparse.py \
	scripts/test-check-user-stack-usage.py \
	scripts/test-check-user-stack-contract.py \
	scripts/test-check-teardown-protocol.py \
	scripts/test-check-agent-uapi-layout.py \
	scripts/test-agent-execution-contract.py \
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
	scripts/test-agent-test-calibration.py \
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
	scripts/test-fs-allocator-evidence.py \
	scripts/test-fs-epoch-regression.py \
	scripts/test-mkfs-host-snapshot.py \
	scripts/test-printf-format-contract.py \
	scripts/test-rp-evidence-file-field.py \
	scripts/test-rp-state-append.py \
	scripts/test-sync-owner-wiring.py \
	scripts/test-validate-virtio-disk-log.py \
	scripts/check-sequential-read-batch.py \
	scripts/test-virtio-disk-wiring.py \
	scripts/test-parallel-qemu-regressions.py \
	scripts/test-parallel-test-runner.py \
	scripts/test-resource-jobs.py \
	scripts/test-bio-rate-controller.py

kernel-budget-selftest: $(KERNEL_BUDGET_PYTHON_SELFTESTS) $(KERNEL_BUDGET_STATIC_CHECKS) scripts/run-parallel-tests.py printf-format-static-check
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--python $(call shell_quote,$(KERNEL_BUDGET_PYTHON)) \
		$(KERNEL_BUDGET_PYTHON_SELFTESTS) $(KERNEL_BUDGET_STATIC_CHECKS)

agent-observe-disk-format-check: scripts/check-agent-observe-disk-format.py scripts/probes/agent-observe-disk-layout.c ci/agent-observe-disk-format.json
	@$(KERNEL_BUDGET_PYTHON_CMD) scripts/check-agent-observe-disk-format.py \
		--cc $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)gcc) \
		--objcopy $(call shell_quote,$(KERNEL_BUDGET_TOOLPREFIX)objcopy)

printf-format-static-check: scripts/check-printf-format-contract.py os/printf.c user/lib/stdio.c
	@$(PYTHON_CMD) scripts/check-printf-format-contract.py --root .

printf-format-check: printf-format-static-check scripts/test-printf-format-contract.py scripts/probes/kernel-printf-integer.c scripts/probes/user-printf-integer.c
	@HOST_CC=$(call shell_quote,$(HOST_CC)) $(PYTHON_CMD) scripts/test-printf-format-contract.py

override HOST_CONTRACT_TESTS := \
	scripts/test-mkfs-host-snapshot.py \
	scripts/test-agent-feature-guest-wiring.py \
	scripts/test-trap-callgraph-separation.py \
	host_tools/test_check_host_platform_alignment.py \
	host_tools/test_check_host_action_kind_alignment.py \
	host_tools/test_check_seeded_action_state.py \
	host_tools/test_check_host_surface_alignment.py \
	host_tools/test_check_host_test_alignment.py \
	host_tools/test_agent_task_transport.py \
	host_tools/test_mcp_a2a_gateway.py \
	host_tools/test_plain_ucore_action_runner.py \
	host_tools/test_research_state_manifest.py \
	host_tools/test_plain_ucore_fs_extract.py \
	host_tools/test_compare_dual_platform_state.py \
	host_tools/test_backend_evidence_contract.py \
	host_tools/test_reference_catalog_contract.py \
	host_tools/test_measured_experiments.py \
	host_tools/test_dual_measurement_source_contract.py \
	host_tools/test_evaluation_platform.py \
	host_tools/test_evaluation_campaign.py \
	host_tools/test_agenteval_measurement_source.py \
	host_tools/test_functional_acceptance_source.py \
	host_tools/test_scenario_timing_source.py \
	host_tools/test_evaluation_contract.py \
	host_tools/test_evaluation_kernel_build.py \
	host_tools/test_evaluation_kernel_cost.py \
	host_tools/test_evaluation_scenario.py \
	host_tools/test_task6_source_comparability.py \
	host_tools/test_evaluation_dashboard.py \
	host_tools/test_contest_demo.py \
	host_tools/test_full_verification_payload.py \
	host_tools/test_evaluation_bundle.py \
	host_tools/test_compatibility_overhead.py \
	host_tools/test_evidence_toolchain_attestation.py \
	host_tools/test_formal_python_runtime.py \
	host_tools/test_safe_host_paths.py

override LONG_HOST_SELFTESTS := \
	host_tools/test_evaluation_campaign.py \
	host_tools/test_evaluation_contract.py \
	host_tools/test_evaluation_kernel_build.py \
	host_tools/test_evaluation_scenario.py \
	host_tools/test_task6_source_comparability.py \
	host_tools/test_evaluation_dashboard.py \
	host_tools/test_full_verification_payload.py \
	host_tools/test_evaluation_bundle.py \
	scripts/test-check-kernel-budgets.py \
	scripts/test-check-teardown-protocol.py
override HOST_CONTRACT_FAST_TESTS := \
	$(filter-out $(LONG_HOST_SELFTESTS),$(HOST_CONTRACT_TESTS))
override HOST_CONTRACT_LONG_TESTS := \
	$(filter $(LONG_HOST_SELFTESTS),$(HOST_CONTRACT_TESTS))
override AGENTOS_LONG_TEST_JOBS := \
	$(if $(filter 1,$(AGENTOS_TEST_JOBS)),1,2)

host-contract-selftest: $(HOST_CONTRACT_TESTS) scripts/run-parallel-tests.py
	@$(PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--python $(call shell_quote,$(PYTHON_BIN)) \
		$(HOST_CONTRACT_FAST_TESTS)
	@$(PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_LONG_TEST_JOBS) \
		--timeout 1800 \
		--python $(call shell_quote,$(PYTHON_BIN)) \
		$(HOST_CONTRACT_LONG_TESTS)

override EVIDENCE_CAPTURE_TESTS := \
	host_tools/test_capture_final_evidence.py \
	host_tools/test_evidence_delivery_contract.py

evidence-capture-selftest: scripts/trusted-python-entry.py scripts/trusted-python-child.py host_tools/evaluation_source_gate.py host_tools/formal_python_runtime.py
evidence-capture-selftest: scripts/capture-final-evidence.py scripts/fs-allocator-evidence.py scripts/check-agent-live-query-fs.py scripts/check-workflow-fence.py scripts/test-workflow-credit-domain.py scripts/test-agent-evidence-ring.py scripts/test-agent-live-query-fs.py scripts/test-workflow-fence.py scripts/test-workflow-syscall-cut.py host_tools/evidence_toolchain_attestation.py host_tools/git_history_contract.py host_tools/measured_experiments.py host_tools/evidence_delivery_contract.py host_tools/dual_state_archive.py host_tools/safe_host_paths.py host_tools/dual_state_evidence_contract.py host_tools/evidence_semantic_common.py host_tools/evidence_semantic_dual.py host_tools/evidence_semantic_metadata.py host_tools/evidence_semantic_profiles.py host_tools/evidence_semantic_registry.py $(EVIDENCE_CAPTURE_TESTS)
	@$(PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--python $(call shell_quote,$(PYTHON_BIN)) \
		$(EVIDENCE_CAPTURE_TESTS)

override LOCAL_HOST_SELFTESTS := \
	$(HOST_CONTRACT_TESTS) \
	$(filter-out $(HOST_CONTRACT_TESTS),$(EVIDENCE_CAPTURE_TESTS)) \
	$(filter-out $(HOST_CONTRACT_TESTS) $(EVIDENCE_CAPTURE_TESTS),$(KERNEL_BUDGET_PYTHON_SELFTESTS) $(KERNEL_BUDGET_STATIC_CHECKS))

# 高成本证据变异套件仍是 local-check/full-verify 的必选项，
# 但普通开发检查点不必等待整包重放。
override STAGE_EXPENSIVE_HOST_SELFTESTS := \
	$(EVIDENCE_CAPTURE_TESTS) \
	scripts/test-fs-allocator-evidence.py \
	$(LONG_HOST_SELFTESTS)
override STAGE_HOST_SELFTESTS := \
	$(filter-out $(STAGE_EXPENSIVE_HOST_SELFTESTS),$(LOCAL_HOST_SELFTESTS))
override LOCAL_FAST_HOST_SELFTESTS := \
	$(filter-out $(LONG_HOST_SELFTESTS),$(LOCAL_HOST_SELFTESTS))
override LOCAL_LONG_HOST_SELFTESTS := \
	$(filter $(LONG_HOST_SELFTESTS),$(LOCAL_HOST_SELFTESTS))

stage-host-selftests: $(STAGE_HOST_SELFTESTS) scripts/run-parallel-tests.py
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--python $(call shell_quote,$(KERNEL_BUDGET_PYTHON)) \
		$(STAGE_HOST_SELFTESTS)

local-host-selftests: $(LOCAL_HOST_SELFTESTS) scripts/run-parallel-tests.py printf-format-static-check
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_TEST_JOBS) \
		--python $(call shell_quote,$(KERNEL_BUDGET_PYTHON)) \
		$(LOCAL_FAST_HOST_SELFTESTS)
	@$(KERNEL_BUDGET_PYTHON_CMD) -I -S -B scripts/run-parallel-tests.py \
		--jobs $(AGENTOS_LONG_TEST_JOBS) \
		--timeout 1800 \
		--python $(call shell_quote,$(KERNEL_BUDGET_PYTHON)) \
		$(LOCAL_LONG_HOST_SELFTESTS)

local-check:
	+@$(MAKE) --no-print-directory local-host-selftests
	+@$(MAKE) --no-print-directory kernel-budget-check
	+@$(MAKE) --no-print-directory user-stack-check

stage-check:
	+@$(MAKE) --no-print-directory stage-host-selftests
	+@$(MAKE) --no-print-directory kernel-budget-check
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
		COMPAT_BENCH_CHALLENGE_HEX=$(call shell_quote,$(COMPAT_BENCH_CHALLENGE_HEX)) \
		USER_EXTRA_CFLAGS='$(USER_EXTRA_CFLAGS)'

user-stack-check:
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -rR -C user -f Makefile user-stack-check TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN))

test:
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) user CHAPTER=$(CHAPTER) BASE=$(BASE)
	$(MAKE) $(AGENTOS_SUBMAKE_JOBS) run CHAPTER=$(CHAPTER) BASE=$(BASE)

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
	@set -eu; \
		duration_profile=$(call shell_quote,$(AGENT_TEST_DURATION_PROFILE)); \
		case "$$duration_profile" in local-e3|none) ;; \
			*) echo "agentos-test: AGENT_TEST_DURATION_PROFILE must be local-e3 or none" >&2; exit 2 ;; \
		esac; \
		if [ "$$duration_profile" = local-e3 ] || \
		   [ -n "$${AGENT_TEST_CASE:-}" ] || \
		   [ "$${AGENT_TEST_CALIBRATE:-0}" != 0 ] || \
		   [ "$${REQUIRE_FULL_SUITE:-0}" != 0 ] || \
		   [ -n "$${FINAL_EVIDENCE_STAGE:-}" ]; then \
			rm -f $(F)/fs.img $(F)/fs-copy.img; \
			AGENT_TEST_DURATION_PROFILE="$$duration_profile" \
				HOST_CC=$(call shell_quote,$(HOST_CC)) \
				TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
				$(call shell_quote,$(BASH_BIN)) scripts/run-agent-tests.sh; \
		else \
			output="build/agent-qemu-lanes-$$(date +%s)-$$$$"; \
			echo "[agentos-test] parallel lanes=$(AGENTOS_QEMU_JOBS) output=$$output"; \
			TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) \
				QEMU=$(call shell_quote,$(QEMU)) \
				PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) \
				BASH_BIN=$(call shell_quote,$(BASH_BIN)) \
				HOST_CC=$(call shell_quote,$(HOST_CC)) \
				$(PYTHON_CMD) -I -S -B scripts/run-parallel-qemu-regressions.py \
					--root . --output-dir "$$output" --suite agent \
					--jobs $(AGENTOS_QEMU_JOBS) \
					--build-jobs $(AGENTOS_BUILD_JOBS) \
					--bash $(call shell_quote,$(BASH_BIN)); \
			$(PYTHON_CMD) -I -S -B scripts/check-kernel-budgets.py \
				--check agent-test-timing-inventory \
				--config ci/kernel-budgets.json \
				--agent-test-timing-file "$$output/agent-suite-timings.log"; \
			echo "[agentos-test] results=$$output"; \
		fi

# 两次隔离 Guest 启动：先执行绑定挑战的任务 1-5 与路径/索引对照，
# 再执行短任务 6；不读取云端 API 或历史结果。
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

target-readiness:
	@$(call shell_quote,$(BASH_BIN)) scripts/check-target-readiness.sh
	+@$(MAKE) --no-print-directory stage-host-selftests

full-verify:
	AGENT_TEST_DURATION_PROFILE=$(call shell_quote,$(FULL_VERIFY_AGENT_TEST_DURATION_PROFILE)) \
		AGENTOS_BUILD_JOBS=$(call shell_quote,$(AGENTOS_BUILD_JOBS)) \
		AGENTOS_TEST_JOBS=$(call shell_quote,$(AGENTOS_TEST_JOBS)) \
		AGENTOS_QEMU_JOBS=$(call shell_quote,$(AGENTOS_QEMU_JOBS)) \
		HOST_CC=$(call shell_quote,$(HOST_CC)) \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) bash scripts/run-full-verification.sh

# 宿主评价契约纳入 local-check；QEMU 活动在本地执行。
evaluation-doctor:
	AGENT_TEST_DURATION_PROFILE=$(call shell_quote,$(AGENT_TEST_DURATION_PROFILE)) \
		HOST_CC=$(call shell_quote,$(HOST_CC)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) bash scripts/run-evaluation-suite.sh doctor

evaluation-smoke:
	AGENT_TEST_DURATION_PROFILE=$(call shell_quote,$(AGENT_TEST_DURATION_PROFILE)) \
		HOST_CC=$(call shell_quote,$(HOST_CC)) \
		PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) bash scripts/run-evaluation-suite.sh smoke

define evaluation_formal_exec
	$(PYTHON_CMD) -I -S scripts/trusted-python-entry.py \
		host_tools/evaluation_platform.py formal-exec --repo . \
		--toolprefix $(call shell_quote,$(TOOLPREFIX)) \
		--qemu $(call shell_quote,$(QEMU)) \
		--python-bin $(call shell_quote,$(PYTHON_BIN)) \
		--shell-bin $(call shell_quote,$(BASH_BIN)) \
		--host-cc $(call shell_quote,$(HOST_CC)) \
		--duration-profile $(call shell_quote,$(AGENT_TEST_DURATION_PROFILE)) \
		--script-relative scripts/run-evaluation-suite.sh --mode $(1)
endef

evaluation-run:
	$(call evaluation_formal_exec,run)

evaluation-verify:
	$(call evaluation_formal_exec,verify)

evaluation-kernel-cost:
	$(call evaluation_formal_exec,kernel-cost)

evaluation-full-verify:
	$(call evaluation_formal_exec,full-verify)

evaluation-dashboard:
	$(call evaluation_formal_exec,dashboard)

evaluation-package:
	$(call evaluation_formal_exec,package)

evaluation-package-development:
	@test -n "$(EVALUATION_RUN_DIR)" -a -n "$(EVALUATION_BUNDLE_DIR)" || { \
		echo "EVALUATION_RUN_DIR and EVALUATION_BUNDLE_DIR are required" >&2; exit 2; \
	}
	PYTHON_BIN=$(call shell_quote,$(PYTHON_BIN)) bash scripts/package-evaluation-evidence.sh create \
		"$(EVALUATION_RUN_DIR)" "$(EVALUATION_BUNDLE_DIR)" --development

evaluation-package-verify:
	$(call evaluation_formal_exec,verify-package)

compatibility-overhead-selftest:
	$(PYTHON_CMD) host_tools/test_compatibility_overhead.py

# 生产器绑定已采集的正式微型活动；逐指标兼容开销与 AgentOS 得分分开统计。
compatibility-overhead-run:
	@test -n "$(COMPATIBILITY_WORK_DIR)" || { \
		echo "COMPATIBILITY_WORK_DIR is required" >&2; exit 2; \
	}
	@test -n "$(COMPATIBILITY_MICRO_MANIFEST)" || { \
		echo "COMPATIBILITY_MICRO_MANIFEST is required" >&2; exit 2; \
	}
	$(PYTHON_CMD) -I -S scripts/trusted-python-entry.py \
		host_tools/compatibility_overhead.py run --repo . \
		--work-dir "$(COMPATIBILITY_WORK_DIR)" \
		--micro-manifest "$(COMPATIBILITY_MICRO_MANIFEST)" \
		--timeout "$(or $(COMPATIBILITY_TIMEOUT),600)"

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
