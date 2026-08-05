.DEFAULT_GOAL := target

ARCH ?= riscv64
CHAPTER ?= 8
BASE ?= 0
TOOLPREFIX ?= $(shell if command -v $(ARCH)-linux-musl-gcc >/dev/null 2>&1; then echo $(ARCH)-linux-musl-; else echo riscv64-linux-gnu-; fi)
CC := $(TOOLPREFIX)gcc
OBJCOPY := $(TOOLPREFIX)objcopy
OBJDUMP := $(TOOLPREFIX)objdump
CP := cp
# Keep raw tool identities available to callers and quote only executable
# words at the recipe boundary.
shell_quote = '$(subst ','"'"',$(1))'
CC_CMD = $(call shell_quote,$(CC))
OBJCOPY_CMD = $(call shell_quote,$(OBJCOPY))
OBJDUMP_CMD = $(call shell_quote,$(OBJDUMP))

app_dir := src
build_dir := build
elf_dir := $(build_dir)/$(ARCH)
obj_dir := $(build_dir)/obj
bin_dir := $(build_dir)/bin
generated_dir := $(build_dir)/generated
out_dir := target
asm_dir := asm
arch_dir := lib/arch/riscv

CFLAGS := -std=gnu11 -march=rv64imac_zicsr_zifencei -mabi=lp64 -mcmodel=medany
CFLAGS += -fno-builtin -nostdinc -fno-stack-protector -ggdb -Wall -Os
CFLAGS += -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-unwind-tables
CFLAGS += -fno-pie -static -Iinclude -Ilib -I$(arch_dir) -I$(generated_dir)
LDFLAGS := -nostdlib -T $(arch_dir)/user.ld -Wl,-Ttext=0x1000 -Wl,--gc-sections

LIB_C := $(wildcard lib/*.c)
LIB_OBJS := $(patsubst %.c,$(obj_dir)/%.o,$(LIB_C))
CRT_OBJ := $(obj_dir)/$(arch_dir)/crt.o
SRCS := $(wildcard $(app_dir)/*.c)
APPS := $(patsubst $(app_dir)/%.c,%,$(SRCS))
APPS += compatbench tradperf tradexec
GENERATED_HEADERS := $(generated_dir)/rp_host_action_seed.h
COMPAT_BENCH_HEADER := $(generated_dir)/compatbench_seed.h
COMPAT_BENCH_SOURCE := ../../evaluation_guest/compatbench.c
COMPAT_BENCH_CHALLENGE_HEX ?= 0000000000000001
TRADPERF_HEADER := $(generated_dir)/traditional_perf_seed.h
TRADPERF_SOURCE := ../../evaluation_guest/traditional_perf.c
TRADEXEC_SOURCE := ../../evaluation_guest/traditional_execprobe.c
TRADPERF_RUN_NONCE ?= 1
TRADPERF_SAMPLE_ID ?= 1
TRADPERF_ORDER_SLOT ?= 1

CH2_BASE_TESTS := ch2b_
CH3_BASE_TESTS := ch3b_ $(CH2_BASE_TESTS)
CH4_BASE_TESTS := $(CH2_BASE_TESTS) ch3b_yield
CH5_BASE_TESTS := ch5b_ $(CH3_BASE_TESTS) usershell
CH6_BASE_TESTS := ch6b_ $(CH5_BASE_TESTS)
CH7_BASE_TESTS := ch7b_ $(CH6_BASE_TESTS)
CH8_BASE_TESTS := ch8b_ $(CH7_BASE_TESTS)
SAFETY_TESTS := usersafety_ucore syscallfair_ucore
FS_ENOSPC_TESTS := fsenospc_ucore fspquota_ucore
PROC_REAP_TESTS := procreap_ucore
FILE_RESOURCE_TESTS := fileresource_ucore
COMPATIBILITY_TESTS := compatbench
TRADITIONAL_PERF_TESTS := tradperf tradexec
PLATFORM_PLAIN_TESTS := usershell rp_plain
PLATFORM_TESTS := rp_orch rp_catalog rp_state_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer rp_repair rp_auditor rp_query rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_execobs rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner rp_workbench rp_agent_collab rp_package rp_calculation rp_realtask rp_analysisres rp_campaign rp_delta rp_release rp_dossier rp_service_surface rp_startup_doctor rp_notebook_export rp_backend rp_consistency rp_metrics rp_ui_export rp_web_export rp_revdash rp_modelreg rp_sysreview rp_expsched rp_traincomp rp_publication rp_runbooks rp_projectrel rp_studyproto rp_stdesign rp_opsboard rp_reviewboard rp_controlplane rp_integrityplane rp_coherenceplane rp_mature rp_prov_view rp_prov_query rp_reldossier rp_decsupport rp_usable rp_usableproject rp_compare_plain rp_test_suite
PLATFORM_SEEDED_TESTS := rp_seed_orch rp_catalog rp_state_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer rp_repair rp_auditor rp_query rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_execobs rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner rp_workbench rp_agent_collab rp_package rp_calculation rp_realtask rp_analysisres rp_campaign rp_delta rp_release rp_dossier rp_service_surface rp_startup_doctor rp_notebook_export rp_backend rp_consistency rp_metrics rp_ui_export rp_web_export rp_revdash rp_modelreg rp_sysreview rp_expsched rp_traincomp rp_publication rp_runbooks rp_projectrel rp_studyproto rp_stdesign rp_opsboard rp_reviewboard rp_controlplane rp_integrityplane rp_coherenceplane rp_mature rp_prov_view rp_prov_query rp_reldossier rp_decsupport rp_usable rp_usableproject rp_compare_plain rp_test_suite

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
else ifeq ($(CHAPTER), safety)
CH_TESTS := $(SAFETY_TESTS)
else ifeq ($(CHAPTER), fs_enospc)
CH_TESTS := $(FS_ENOSPC_TESTS)
else ifeq ($(CHAPTER), proc_reap)
CH_TESTS := $(PROC_REAP_TESTS)
else ifeq ($(CHAPTER), file_resource)
CH_TESTS := $(FILE_RESOURCE_TESTS)
else ifeq ($(CHAPTER), compat_eval)
CH_TESTS := $(COMPATIBILITY_TESTS)
else ifeq ($(CHAPTER), traditional_perf)
CH_TESTS := $(TRADITIONAL_PERF_TESTS)
else ifeq ($(CHAPTER), platform)
CH_TESTS := $(PLATFORM_TESTS)
else ifeq ($(CHAPTER), platform_seeded)
CH_TESTS := $(PLATFORM_SEEDED_TESTS)
else ifeq ($(CHAPTER), platform_plain)
CH_TESTS := $(PLATFORM_PLAIN_TESTS)
else
CH_TESTS := $(PLATFORM_TESTS)
endif

SELECTED_APPS = $(foreach t,$(CH_TESTS),$(filter $(t)%,$(APPS)))

.PHONY: binary target clean .FORCE

.FORCE:

$(GENERATED_HEADERS):
	@mkdir -p $(@D)
	@printf '%s\n' '#ifndef __RP_HOST_ACTION_SEED_H__' '#define __RP_HOST_ACTION_SEED_H__' '#define RP_HOST_ACTION_SEED ""' '#define RP_HOST_ACTION_BOOTSTRAP_SEED ""' '#endif' > $@

$(COMPAT_BENCH_HEADER): .FORCE
	@mkdir -p $(@D)
	@case '$(COMPAT_BENCH_CHALLENGE_HEX)' in \
		????????????????) ;; \
		*) echo 'COMPAT_BENCH_CHALLENGE_HEX must contain 16 lowercase hex digits' >&2; exit 2 ;; \
	esac
	@case '$(COMPAT_BENCH_CHALLENGE_HEX)' in \
		*[!0-9a-f]*) echo 'COMPAT_BENCH_CHALLENGE_HEX must contain 16 lowercase hex digits' >&2; exit 2 ;; \
	esac
	@printf '%s\n' \
		'#ifndef __COMPATBENCH_SEED_H__' \
		'#define __COMPATBENCH_SEED_H__' \
		'#define COMPATBENCH_CHALLENGE 0x$(COMPAT_BENCH_CHALLENGE_HEX)ULL' \
		'#endif' > $@.tmp
	@if ! test -r $@ || ! cmp -s $@.tmp $@; then mv $@.tmp $@; else rm $@.tmp; fi

$(TRADPERF_HEADER): .FORCE
	@mkdir -p $(@D)
	@case '$(TRADPERF_RUN_NONCE)' in \
		''|0|*[!0-9]*) echo 'TRADPERF_RUN_NONCE must be a nonzero decimal uint64' >&2; exit 2 ;; \
	esac
	@case '$(TRADPERF_SAMPLE_ID)' in \
		1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26|27|28|29|30|31|32) ;; \
		*) echo 'TRADPERF_SAMPLE_ID must be in 1..32' >&2; exit 2 ;; \
	esac
	@case '$(TRADPERF_ORDER_SLOT)' in \
		1|2) ;; \
		*) echo 'TRADPERF_ORDER_SLOT must be 1 or 2' >&2; exit 2 ;; \
	esac
	@printf '%s\n' \
		'#ifndef __TRADITIONAL_PERF_SEED_H__' \
		'#define __TRADITIONAL_PERF_SEED_H__' \
		'#define TRADPERF_RUN_NONCE $(TRADPERF_RUN_NONCE)ULL' \
		'#define TRADPERF_SAMPLE_ID $(TRADPERF_SAMPLE_ID)U' \
		'#define TRADPERF_TARGET_AGENTOS 0' \
		'#define TRADPERF_ORDER_SLOT $(TRADPERF_ORDER_SLOT)U' \
		'#endif' > $@.tmp
	@if ! test -r $@ || ! cmp -s $@.tmp $@; then mv $@.tmp $@; else rm $@.tmp; fi

$(obj_dir)/%.o: %.c $(GENERATED_HEADERS)
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(CRT_OBJ): $(arch_dir)/crt.S
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(elf_dir)/%: $(app_dir)/%.c $(LIB_OBJS) $(CRT_OBJ)
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC_CMD) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) $< -o $@
	$(OBJDUMP_CMD) -d -S $@ > $(asm_dir)/$*.asm
	$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/$*

$(elf_dir)/compatbench: $(COMPAT_BENCH_SOURCE) $(LIB_OBJS) $(CRT_OBJ) \
		$(GENERATED_HEADERS) $(COMPAT_BENCH_HEADER) $(arch_dir)/user.ld
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC_CMD) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) \
		$(COMPAT_BENCH_SOURCE) -o $@
	$(OBJDUMP_CMD) -d -S $@ > $(asm_dir)/compatbench.asm
	$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/compatbench

$(elf_dir)/tradperf: $(TRADPERF_SOURCE) $(LIB_OBJS) $(CRT_OBJ) \
		$(GENERATED_HEADERS) $(TRADPERF_HEADER) $(arch_dir)/user.ld
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC_CMD) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) \
		$(TRADPERF_SOURCE) -o $@
	$(OBJDUMP_CMD) -d -S $@ > $(asm_dir)/tradperf.asm
	$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/tradperf

$(elf_dir)/tradexec: $(TRADEXEC_SOURCE) $(LIB_OBJS) $(CRT_OBJ) \
		$(GENERATED_HEADERS) $(TRADPERF_HEADER) $(arch_dir)/user.ld
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC_CMD) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) \
		$(TRADEXEC_SOURCE) -o $@
	$(OBJDUMP_CMD) -d -S $@ > $(asm_dir)/tradexec.asm
	$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/tradexec

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
