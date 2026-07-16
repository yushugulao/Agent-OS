.PHONY: clean build user run debug test doctor kernel-stack-check plain-clean plain-platform-build plain-platform-run agentos-user agentos-build agentos-clean agentos-test agentos-platform-user agentos-platform-build agentos-platform-run fs-enospc-test proc-reap-test reader target-readiness dual-platform-run full-verify dual-clean .FORCE
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
PY = python3
PYTHON_BIN ?= $(PY)
GDB = $(TOOLPREFIX)gdb
CP = cp
BUILDDIR = build
C_SRCS = $(wildcard $K/*.c)
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
KSTACK_GUARD_SIZE_BYTES ?= 4096
KSTACK_FRAME_BUDGET ?= $(KSTACK_GUARD_SIZE_BYTES)
KSTACK_SAFETY_MARGIN ?= 4096
KERNELVEC_FRAME_SIZE_BYTES ?= 256
# swtch changes stacks; usertrapret's indirect jump is the stackless trampoline.
KSTACK_STACK_BOUNDARIES ?= swtch
KSTACK_INDIRECT_CALLERS ?= usertrapret
# printf can enter panic once; Sv39 freewalk visits at most three page-table levels.
KSTACK_RECURSION_BOUNDS ?= printf=2 freewalk=3
KSTACK_POLICY_ARGS = \
	$(foreach fn,$(KSTACK_STACK_BOUNDARIES),--stack-boundary $(fn)) \
	$(foreach fn,$(KSTACK_INDIRECT_CALLERS),--allow-indirect-from $(fn)) \
	$(foreach bound,$(KSTACK_RECURSION_BOUNDS),--recursion-bound $(bound))
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

KSTACK_BUILD_CONFIG = $(BUILDDIR)/.kernel-stack-config
$(KSTACK_BUILD_CONFIG): .FORCE
	@mkdir -p $(@D)
	@printf '%s\n' \
		'CC=$(CC)' \
		'CFLAGS=$(CFLAGS)' \
		'KSTACK_SIZE_BYTES=$(KSTACK_SIZE_BYTES)' \
		'KSTACK_GUARD_SIZE_BYTES=$(KSTACK_GUARD_SIZE_BYTES)' \
		'KSTACK_FRAME_BUDGET=$(KSTACK_FRAME_BUDGET)' \
		'KSTACK_SAFETY_MARGIN=$(KSTACK_SAFETY_MARGIN)' \
		'KERNELVEC_FRAME_SIZE_BYTES=$(KERNELVEC_FRAME_SIZE_BYTES)' \
		'KSTACK_STACK_BOUNDARIES=$(KSTACK_STACK_BOUNDARIES)' \
		'KSTACK_INDIRECT_CALLERS=$(KSTACK_INDIRECT_CALLERS)' \
		'KSTACK_RECURSION_BOUNDS=$(KSTACK_RECURSION_BOUNDS)' > $@.tmp
	@if ! test -r $@ || ! cmp -s $@.tmp $@; then mv $@.tmp $@; else rm $@.tmp; fi

# empty target
.FORCE:

LDFLAGS = -z max-page-size=4096

$(AS_OBJS): $(BUILDDIR)/$K/%.o : $K/%.S
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@

$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c  $(BUILDDIR)/$K/%.d
	@mkdir -p $(@D)
	@rm -f $(patsubst %.o,%.ci,$@)
	$(CC) $(CFLAGS) -c $< -o $@

$(HEADER_DEP): $(BUILDDIR)/$K/%.d : $K/%.c
	@mkdir -p $(@D)
	@set -e; rm -f $@; $(CC) -MM $< $(INCLUDEFLAGS) > $@.$$$$; \
        sed 's,\($*\)\.o[ :]*,\1.o $@ : ,g' < $@.$$$$ > $@; \
        rm -f $@.$$$$

$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)
-include $(HEADER_DEP)

INIT_PROC ?= usershell

build: build/kernel

build/kernel: $(OBJS) os/kernel.ld scripts/check-kernel-stack-usage.py $(KSTACK_BUILD_CONFIG) Makefile
	$(PY) scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) $(KSTACK_POLICY_ARGS)
	$(LD) $(LDFLAGS) -T os/kernel.ld -o $(BUILDDIR)/kernel $(OBJS)
	$(OBJDUMP) -S $(BUILDDIR)/kernel > $(BUILDDIR)/kernel.asm
	$(OBJDUMP) -t $(BUILDDIR)/kernel | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > $(BUILDDIR)/kernel.sym
	@echo 'Build kernel done'

kernel-stack-check: build/kernel
	@$(PY) scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) $(KSTACK_POLICY_ARGS)

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

$(F)/fs.img:
	make -C $(F)

$(F)/fs-copy.img: $(F)/fs.img
	@$(CP) $< $@

run: build/kernel $(F)/fs-copy.img
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
	make -C user CHAPTER=$(CHAPTER) BASE=$(BASE)

test: user run

doctor:
	bash scripts/check-dependencies.sh

plain-platform-build:
	rm -f baseline_ucore/$(F)/fs.img baseline_ucore/$(F)/fs-copy.img
	$(MAKE) -C baseline_ucore/user clean
	$(MAKE) -C baseline_ucore user nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform
	$(MAKE) -C baseline_ucore build TOOLPREFIX=$(TOOLPREFIX) LOG=warn INIT_PROC=rp_orch CHAPTER=platform

plain-platform-run:
	rm -f baseline_ucore/$(F)/fs.img baseline_ucore/$(F)/fs-copy.img
	$(MAKE) -C baseline_ucore/user clean
	$(MAKE) -C baseline_ucore user nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform
	$(MAKE) -C baseline_ucore run TOOLPREFIX=$(TOOLPREFIX) LOG=error INIT_PROC=rp_orch CHAPTER=platform

agentos-user:
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=agent

agentos-build:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -C user clean
	$(MAKE) user nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=agent
	$(MAKE) build TOOLPREFIX=$(TOOLPREFIX) LOG=warn INIT_PROC=agentfinal_ucore

agentos-test:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-agent-tests.sh

fs-enospc-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-fs-enospc-tests.sh

proc-reap-test:
	TOOLPREFIX=$(TOOLPREFIX) bash scripts/run-proc-reap-tests.sh

agentos-platform-user:
	$(MAKE) user TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos

agentos-platform-build:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -C user clean
	$(MAKE) user nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos
	$(MAKE) build TOOLPREFIX=$(TOOLPREFIX) LOG=warn INIT_PROC=rp_agentos_orch

agentos-platform-run:
	rm -f $(F)/fs.img $(F)/fs-copy.img
	$(MAKE) -C user clean
	$(MAKE) user nfs/fs.img TOOLPREFIX=$(TOOLPREFIX) CHAPTER=platform_agentos
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
