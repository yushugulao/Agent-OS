.PHONY: clean build user run run-prebuilt run-persist debug test kernel-stack-check .FORCE
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
PYTHON_BIN ?= python3
PY = $(PYTHON_BIN)
GDB = $(TOOLPREFIX)gdb
# Preserve raw tool identities for configuration and recursive-make
# propagation.  Quote complete executable words only when the shell sees them.
shell_quote = '$(subst ','"'"',$(1))'
CC_CMD = $(call shell_quote,$(CC))
AS_CMD = $(call shell_quote,$(AS))
LD_CMD = $(call shell_quote,$(LD))
OBJCOPY_CMD = $(call shell_quote,$(OBJCOPY))
OBJDUMP_CMD = $(call shell_quote,$(OBJDUMP))
PYTHON_CMD = $(call shell_quote,$(PY))
GDB_CMD = $(call shell_quote,$(GDB))
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
$(K)/initproc.S: ../scripts/initproc.py .FORCE
	@$(PYTHON_CMD) ../scripts/initproc.py $(INIT_PROC)

CFLAGS = -Wall -Werror -O -fno-omit-frame-pointer -ggdb
CFLAGS += -MD
CFLAGS += -march=rv64imac_zicsr_zifencei -mabi=lp64
CFLAGS += -mcmodel=medany
CFLAGS += -ffreestanding -fno-common -nostdlib -mno-relax
CFLAGS += -I$K
CFLAGS += $(shell $(CC_CMD) -fno-stack-protector -E -x c /dev/null >/dev/null 2>&1 && echo -fno-stack-protector)

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

ifneq ($(FS_PUBLIC_BLOCK_WATERMARK),)
CFLAGS += -DFS_PUBLIC_BLOCK_WATERMARK=$(FS_PUBLIC_BLOCK_WATERMARK)
endif
ifneq ($(FS_PUBLIC_INODE_WATERMARK),)
CFLAGS += -DFS_PUBLIC_INODE_WATERMARK=$(FS_PUBLIC_INODE_WATERMARK)
endif
ifneq ($(FS_DOMAIN_BLOCK_LIMIT),)
CFLAGS += -DFS_DOMAIN_BLOCK_LIMIT=$(FS_DOMAIN_BLOCK_LIMIT)
endif
ifneq ($(FS_DOMAIN_INODE_LIMIT),)
CFLAGS += -DFS_DOMAIN_INODE_LIMIT=$(FS_DOMAIN_INODE_LIMIT)
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

KSTACK_BUILD_CONFIG = $(BUILDDIR)/.kernel-stack-config
$(KSTACK_BUILD_CONFIG): .FORCE
	@mkdir -p $(@D)
	@printf '%s\n' \
		'CC=$(CC)' \
		'CFLAGS=$(CFLAGS)' \
		'KSTACK_SIZE_BYTES=$(KSTACK_SIZE_BYTES)' \
		'KSTACK_BOOT_SIZE_BYTES=$(KSTACK_BOOT_SIZE_BYTES)' \
		'KSTACK_BOOT_ROOT=$(KSTACK_BOOT_ROOT)' \
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

LDFLAGS = -m elf64lriscv -z max-page-size=4096

$(AS_OBJS): $(BUILDDIR)/$K/%.o : $K/%.S
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c  $(BUILDDIR)/$K/%.d
	@mkdir -p $(@D)
	@rm -f $(patsubst %.o,%.ci,$@)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(HEADER_DEP): $(BUILDDIR)/$K/%.d : $K/%.c
	@mkdir -p $(@D)
	@set -e; rm -f $@; $(CC_CMD) -MM $< $(INCLUDEFLAGS) > $@.$$$$; \
        sed 's,\($*\)\.o[ :]*,\1.o $@ : ,g' < $@.$$$$ > $@; \
        rm -f $@.$$$$

$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)
-include $(HEADER_DEP)

INIT_PROC ?= usershell

build: build/kernel

build/kernel: $(OBJS) os/kernel.ld ../scripts/check-kernel-stack-usage.py $(KSTACK_BUILD_CONFIG) Makefile
	$(PYTHON_CMD) ../scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--boot-stack-size $(KSTACK_BOOT_SIZE_BYTES) \
		--boot-root $(KSTACK_BOOT_ROOT) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) $(KSTACK_POLICY_ARGS)
	$(LD_CMD) $(LDFLAGS) -T os/kernel.ld -o $(BUILDDIR)/kernel $(OBJS)
	$(OBJDUMP_CMD) -S $(BUILDDIR)/kernel > $(BUILDDIR)/kernel.asm
	$(OBJDUMP_CMD) -t $(BUILDDIR)/kernel | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > $(BUILDDIR)/kernel.sym
	@echo 'Build kernel done'

kernel-stack-check: build/kernel
	@$(PYTHON_CMD) ../scripts/check-kernel-stack-usage.py \
		--callgraph-dir $(BUILDDIR)/$(K) --source-dir $(K) \
		--stack-size $(KSTACK_SIZE_BYTES) --guard-size $(KSTACK_GUARD_SIZE_BYTES) \
		--boot-stack-size $(KSTACK_BOOT_SIZE_BYTES) \
		--boot-root $(KSTACK_BOOT_ROOT) \
		--safety-margin $(KSTACK_SAFETY_MARGIN) \
		--interrupt-entry $(KERNELVEC_FRAME_SIZE_BYTES) $(KSTACK_POLICY_ARGS)

clean:
	make -C $(U) clean
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
	make -C $(F)

$(F)/fs-copy.img: $(F)/fs.img
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		$(CP) "$<" "$$tmp"; \
		mv -f "$$tmp" "$@"; \
		trap - 0 1 2 3 15

run: build/kernel $(F)/fs-copy.img
	$(QEMU_CMD) $(QEMUOPTS)

# Start only already-built artifacts. Host-side observers use this target so
# compiler output can never be interpreted as guest runtime output.
run-prebuilt:
	@test -f build/kernel || { echo "missing prebuilt kernel" >&2; exit 1; }
	@test -f $(F)/fs-copy.img || { echo "missing prebuilt filesystem image" >&2; exit 1; }
	$(QEMU_CMD) $(QEMUOPTS)

# Reboot the current writable disk explicitly.  Normal `run` always installs
# the freshly built userspace image so code updates cannot go stale.
run-persist: build/kernel
	@if [ ! -f "$(F)/fs-copy.img" ]; then $(MAKE) $(F)/fs-copy.img; fi
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
	$(MAKE) -C user CHAPTER=$(CHAPTER) BASE=$(BASE) \
		TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX))

test: user run
