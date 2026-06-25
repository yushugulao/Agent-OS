.DEFAULT_GOAL := target

ARCH ?= riscv64
CHAPTER ?= 8
BASE ?= 0
TOOLPREFIX ?= $(shell if command -v $(ARCH)-linux-musl-gcc >/dev/null 2>&1; then echo $(ARCH)-linux-musl-; else echo riscv64-linux-gnu-; fi)
CC := $(TOOLPREFIX)gcc
OBJCOPY := $(TOOLPREFIX)objcopy
OBJDUMP := $(TOOLPREFIX)objdump
CP := cp

app_dir := src
build_dir := build
elf_dir := $(build_dir)/$(ARCH)
obj_dir := $(build_dir)/obj
bin_dir := $(build_dir)/bin
out_dir := target
asm_dir := asm
arch_dir := lib/arch/riscv

CFLAGS := -std=gnu11 -march=rv64imac_zicsr_zifencei -mabi=lp64 -mcmodel=medany
CFLAGS += -fno-builtin -nostdinc -fno-stack-protector -ggdb -Wall -Os
CFLAGS += -fno-pie -static -Iinclude -Ilib -I$(arch_dir)
LDFLAGS := -nostdlib -T $(arch_dir)/user.ld -Wl,-Ttext=0x1000

LIB_C := $(wildcard lib/*.c)
LIB_OBJS := $(patsubst %.c,$(obj_dir)/%.o,$(LIB_C))
CRT_OBJ := $(obj_dir)/$(arch_dir)/crt.o
SRCS := $(wildcard $(app_dir)/*.c)
APPS := $(patsubst $(app_dir)/%.c,%,$(SRCS))

CH2_BASE_TESTS := ch2b_
CH3_BASE_TESTS := ch3b_ $(CH2_BASE_TESTS)
CH4_BASE_TESTS := $(CH2_BASE_TESTS) ch3b_yield
CH5_BASE_TESTS := ch5b_ $(CH3_BASE_TESTS) usershell
CH6_BASE_TESTS := ch6b_ $(CH5_BASE_TESTS)
CH7_BASE_TESTS := ch7b_ $(CH6_BASE_TESTS)
CH8_BASE_TESTS := ch8b_ $(CH7_BASE_TESTS)
PLATFORM_PLAIN_TESTS := usershell rp_plain
PLATFORM_TESTS := rp_orch rp_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer rp_repair rp_auditor rp_query rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_execobs rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner rp_workbench rp_agent_collab rp_package rp_delta rp_release rp_dossier rp_service_surface rp_backend rp_consistency rp_metrics rp_ui_export rp_web_export rp_test_suite rp_compare_plain

CH2_TESTS := $(CH2_BASE_TESTS)
CH3_TESTS := $(CH3_BASE_TESTS) ch3_
CH4_TESTS := $(CH4_BASE_TESTS) ch4_ ch3b_sleep ch3_
CH5_TESTS_BASE := ch5b_ $(CH4_TESTS) ch5_ ch5t_ usershell
CH5_SCHED_TEST := ch5t_
CH6_TESTS := $(CH6_BASE_TESTS) ch6_ ch5_ ch4_
CH7_TESTS := $(CH7_BASE_TESTS) ch7_
CH8_TESTS := $(CH8_BASE_TESTS) ch8_

ifeq ($(BASE), 1)
CH2_TESTS := $(CH2_BASE_TESTS)
CH3_TESTS := $(CH3_BASE_TESTS)
CH4_TESTS := $(CH4_BASE_TESTS)
CH5_TESTS_BASE := $(CH5_BASE_TESTS)
CH6_TESTS := $(CH6_BASE_TESTS)
CH7_TESTS := $(CH7_BASE_TESTS)
CH8_TESTS := $(CH8_BASE_TESTS)
endif

ifeq ($(CHAPTER), 2)
CH_TESTS := $(CH2_TESTS)
else ifeq ($(CHAPTER), 2_bad)
CH_TESTS := __ch2_bad_
else ifeq ($(CHAPTER), 3)
CH_TESTS := $(CH3_TESTS)
else ifeq ($(CHAPTER), 4)
CH_TESTS := $(CH4_TESTS)
else ifeq ($(CHAPTER), 4_3)
CH_TESTS := $(CH4_BASE_TESTS) ch3b_sleep
else ifeq ($(CHAPTER), 4_only)
CH_TESTS := ch4_ ch4b_
else ifeq ($(CHAPTER), 5)
CH_TESTS := $(CH5_TESTS_BASE)
else ifeq ($(CHAPTER), 5_only)
CH_TESTS := ch5_ ch5b_ ch5t_
else ifeq ($(CHAPTER), 5t)
CH_TESTS := $(CH5_SCHED_TEST)
else ifeq ($(CHAPTER), 6)
CH_TESTS := $(CH6_TESTS)
else ifeq ($(CHAPTER), 6_only)
CH_TESTS := ch6_ ch6b_
else ifeq ($(CHAPTER), 7)
CH_TESTS := $(CH7_TESTS)
else ifeq ($(CHAPTER), 7_only)
CH_TESTS := ch7_ ch7b_
else ifeq ($(CHAPTER), 8)
CH_TESTS := $(CH8_TESTS)
else ifeq ($(CHAPTER), 8_only)
CH_TESTS := ch8_
else ifeq ($(CHAPTER), platform)
CH_TESTS := $(PLATFORM_TESTS)
else ifeq ($(CHAPTER), platform_plain)
CH_TESTS := $(PLATFORM_PLAIN_TESTS)
else
CH_TESTS := $(PLATFORM_TESTS)
endif

SELECTED_APPS = $(foreach t,$(CH_TESTS),$(filter $(t)%,$(APPS)))

$(obj_dir)/%.o: %.c
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@

$(CRT_OBJ): $(arch_dir)/crt.S
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@

$(elf_dir)/%: $(app_dir)/%.c $(LIB_OBJS) $(CRT_OBJ)
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) $< -o $@
	$(OBJDUMP) -d -S $@ > $(asm_dir)/$*.asm
	$(OBJCOPY) -O binary $@ $(bin_dir)/$*

binary: $(addprefix $(elf_dir)/,$(SELECTED_APPS))

target: binary
	@rm -rf $(out_dir)
	@mkdir -p $(out_dir)/bin $(out_dir)/elf
	@echo tests=$(CH_TESTS)
	@for t in $(CH_TESTS); do \
		for f in $(bin_dir)/$$t*; do [ -e "$$f" ] && cp "$$f" $(out_dir)/bin/; done; \
		for f in $(elf_dir)/$$t*; do [ -e "$$f" ] && cp "$$f" $(out_dir)/elf/; done; \
	done

clean:
	rm -rf $(asm_dir) $(build_dir) $(out_dir)
