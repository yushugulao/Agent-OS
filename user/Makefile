.DEFAULT_GOAL := target

ARCH ?= riscv64
CHAPTER ?= 8
BASE ?= 0
TOOLPREFIX ?= $(shell if command -v $(ARCH)-linux-musl-gcc >/dev/null 2>&1; then echo $(ARCH)-linux-musl-; else echo riscv64-linux-gnu-; fi)
ifeq ($(FUNCTIONAL_REVIEW_BUILD),1)
override FUNCTIONAL_REVIEW_FORBIDDEN_USER_VARS := \
	ARCH CC OBJCOPY OBJDUMP COMMON_CFLAGS CFLAGS CPPFLAGS LDFLAGS LIB_C LIB_OBJS CRT_OBJ \
	app_dir build_dir elf_dir obj_dir bin_dir generated_dir out_dir asm_dir arch_dir \
	SRCS APPS SELECTED_APPS RETIRED_GUEST_APPS RETIRED_GUEST_SRCS
override FUNCTIONAL_REVIEW_EXTERNAL_USER_VARS := $(strip $(foreach variable,$(FUNCTIONAL_REVIEW_FORBIDDEN_USER_VARS),$(if $(filter command line environment override,$(origin $(variable))),$(variable))))
ifneq ($(FUNCTIONAL_REVIEW_EXTERNAL_USER_VARS),)
$(error FUNCTIONAL_REVIEW_BUILD rejects external user variables: $(FUNCTIONAL_REVIEW_EXTERNAL_USER_VARS))
endif
override FUNCTIONAL_REVIEW_USER_CFLAGS := $(USER_EXTRA_CFLAGS)
override FUNCTIONAL_REVIEW_FLAGS_STATUS := $(shell python3 -I -S -B ../scripts/validate-functional-review-flags.py '$(subst ','"'"',$(FUNCTIONAL_REVIEW_USER_CFLAGS))' '$(subst ','"'"',$(CHAPTER))' '$(subst ','"'"',$(CH_TESTS))' '$(subst ','"'"',$(INIT_PROC))' 2>/dev/null)
ifneq ($(FUNCTIONAL_REVIEW_FLAGS_STATUS),ok)
$(error FUNCTIONAL_REVIEW_BUILD rejects USER_EXTRA_CFLAGS or its build context)
endif
override USER_EXTRA_CFLAGS := $(FUNCTIONAL_REVIEW_USER_CFLAGS)
endif
CC := $(TOOLPREFIX)gcc
OBJCOPY := $(TOOLPREFIX)objcopy
OBJDUMP := $(TOOLPREFIX)objdump
CP := cp
PYTHON_BIN ?= python3
# 上方保留构建契约所需的原始身份；工具后缀拼入 TOOLPREFIX 后，
# 只在 shell 边界整体引用。
shell_quote = '$(subst ','"'"',$(1))'
CC_CMD = $(call shell_quote,$(CC))
OBJCOPY_CMD = $(call shell_quote,$(OBJCOPY))
OBJDUMP_CMD = $(call shell_quote,$(OBJDUMP))
PYTHON_CMD = $(call shell_quote,$(PYTHON_BIN))

app_dir := src
build_dir := build
elf_dir := $(build_dir)/$(ARCH)
obj_dir := $(build_dir)/obj
bin_dir := $(build_dir)/bin
generated_dir := $(build_dir)/generated
out_dir := target
asm_dir := asm
arch_dir := lib/arch/riscv

COMMON_CFLAGS := -std=gnu11 -march=rv64imac_zicsr_zifencei -mabi=lp64 -mcmodel=medany
COMMON_CFLAGS += -fno-builtin -nostdinc -fno-stack-protector -ggdb -Wall -Os
COMMON_CFLAGS += -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-unwind-tables
COMMON_CFLAGS += -fno-pie -static
CFLAGS := $(COMMON_CFLAGS) -Iinclude -Ilib -I$(arch_dir) -I$(generated_dir)
CFLAGS += $(USER_EXTRA_CFLAGS)
LDFLAGS := -nostdlib -T $(arch_dir)/user.ld -Wl,-Ttext=0x1000 -Wl,--gc-sections

LIB_C := $(wildcard lib/*.c)
LIB_OBJS := $(patsubst %.c,$(obj_dir)/%.o,$(LIB_C))
CRT_OBJ := $(obj_dir)/$(arch_dir)/crt.o
RETIRED_GUEST_APPS := agentmetacrash_ucore agentmetarecover_ucore \
	agentmetaeio_ucore agentmetalarge_ucore agentmetatransient_ucore \
	agentobsreboot_ucore
RETIRED_GUEST_SRCS := $(addprefix user/$(app_dir)/,$(addsuffix .c,$(RETIRED_GUEST_APPS)))
SRCS := $(wildcard $(app_dir)/*.c)
APPS := $(filter-out $(RETIRED_GUEST_APPS),$(patsubst $(app_dir)/%.c,%,$(SRCS)))
APPS += compatbench
GENERATED_HEADERS := $(generated_dir)/rp_host_action_seed.h
EVALUATION_HEADER := $(generated_dir)/agenteval_seed.h
COMPAT_BENCH_HEADER := $(generated_dir)/compatbench_seed.h
COMPAT_BENCH_REPO_SOURCE := evaluation_guest/compatbench.c
COMPAT_BENCH_SOURCE := ../$(COMPAT_BENCH_REPO_SOURCE)
AGENT_EVAL_CHALLENGE_HEX ?= 0000000000000001
COMPAT_BENCH_CHALLENGE_HEX ?= 0000000000000001
USER_BUILD_CONFIG := $(build_dir)/.user-build-config
USER_STACK_CONTRACT := ../user_stack_policy.h
USER_STACK_CONTRACT_WRAPPER := include/user_stack_policy.h
# 按运行器最大准入容量编译拆除测试，以最大受支持配置测量数组栈帧。
override USER_STACK_WORKFLOW_DOMAIN_FILE_CAP := 14
override USER_STACK_WORKFLOW_GLOBAL_RESERVED_CAP := 64
# 此辅助函数通过函数指针调用四个固定子入口之一；须建模全部目标，
# 不能把间接边视作无栈调用。
USER_STACK_INDIRECT_CALL_EDGES := \
	user/src/fspquota_ucore.c:run_child=user/src/fspquota_ucore.c:leave_crash_orphan \
	user/src/fspquota_ucore.c:run_child=user/src/fspquota_ucore.c:seed_or_verify \
	user/src/fspquota_ucore.c:run_child=user/src/fspquota_ucore.c:verify_relaunch_and_cleanup \
	user/src/fspquota_ucore.c:run_child=user/src/fspquota_ucore.c:verify_cleanup_reuse
USER_STACK_CHECKER := ../scripts/check-user-stack-usage.py
USER_STACK_CONTRACT_CHECKER := ../scripts/check-user-stack-contract.py
WORKER_BATCH_CHECKER := ../scripts/check-rp-worker-batches.py
WORKER_BATCH_SELFTEST := ../scripts/test-rp-worker-batches.py
STACK_USAGE_DIR ?= $(build_dir)/stack-usage

CH2_BASE_TESTS := ch2b_
CH3_BASE_TESTS := ch3b_ $(CH2_BASE_TESTS)
CH4_BASE_TESTS := $(CH2_BASE_TESTS) ch3b_yield
CH5_BASE_TESTS := ch5b_ $(CH3_BASE_TESTS) usershell
CH6_BASE_TESTS := ch6b_ $(CH5_BASE_TESTS)
CH7_BASE_TESTS := ch7b_ $(CH6_BASE_TESTS)
CH8_BASE_TESTS := ch8b_ $(CH7_BASE_TESTS)
SAFETY_TESTS := usersafety_ucore syscallfair_ucore blocking_semantics_ucore
FS_ENOSPC_TESTS := fsenospc_ucore fsquota_ucore fspquota_ucore
PROC_REAP_TESTS := procreap_ucore procreap_agent_ucore
FILE_RESOURCE_TESTS := fileresource_ucore
THREAD_RESOURCE_TESTS := threadresource_ucore
PHYSICAL_RESOURCE_TESTS := physicalresource_ucore
WORKFLOW_TEARDOWN_TESTS := workflow_teardown_race_ucore
VIRTIO_DISK_TESTS := virtiodisk_ucore
EVALUATION_TESTS := agenteval_ucore
COMPATIBILITY_TESTS := compatbench
AGENT_TESTS := agentfinal_ucore agentfs_ucore agentscan_ucore agentloop_ucore agentsched_ucore agentconflict_ucore agentllm_ucore agentbench_ucore ch8_cow_ucore labdemo_ucore labdemo_execprobe_ucore agentsecurity_ucore agenttoolabi_ucore agentscope_ucore agenttrust_ucore agentvfs_ucore agentvfs_probe iobudget_ucore $(SAFETY_TESTS)
PLATFORM_TESTS := rp_orch rp_catalog rp_state_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer rp_repair rp_auditor rp_query rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_execobs rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner rp_workbench rp_agent_collab rp_package rp_calculation rp_realtask rp_analysisres rp_campaign rp_delta rp_release rp_dossier rp_service_surface rp_startup_doctor rp_notebook_export rp_backend rp_consistency rp_metrics rp_ui_export rp_web_export rp_revdash rp_modelreg rp_sysreview rp_expsched rp_traincomp rp_publication rp_runbooks rp_projectrel rp_studyproto rp_stdesign rp_opsboard rp_reviewboard rp_controlplane rp_integrityplane rp_coherenceplane rp_mature rp_prov_view rp_prov_query rp_reldossier rp_decsupport rp_usable rp_usableproject rp_compare_plain rp_test_suite
PLATFORM_SEEDED_TESTS := rp_seed_orch rp_catalog rp_state_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer rp_repair rp_auditor rp_query rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_execobs rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner rp_workbench rp_agent_collab rp_package rp_calculation rp_realtask rp_analysisres rp_campaign rp_delta rp_release rp_dossier rp_service_surface rp_startup_doctor rp_notebook_export rp_backend rp_consistency rp_metrics rp_ui_export rp_web_export rp_revdash rp_modelreg rp_sysreview rp_expsched rp_traincomp rp_publication rp_runbooks rp_projectrel rp_studyproto rp_stdesign rp_opsboard rp_reviewboard rp_controlplane rp_integrityplane rp_coherenceplane rp_mature rp_prov_view rp_prov_query rp_reldossier rp_decsupport rp_usable rp_usableproject rp_compare_plain rp_test_suite
WORKER_BATCH_APPS := rp_wbatch0 rp_wbatch1 rp_wbatch2
WORKER_BATCH_0_PROGRAMS := rp_catalog rp_state_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner rp_calculation rp_analysisres rp_campaign rp_delta rp_release rp_dossier rp_startup_doctor rp_notebook_export
WORKER_BATCH_1_PROGRAMS := rp_consistency rp_metrics rp_ui_export rp_web_export rp_revdash rp_modelreg rp_sysreview rp_expsched rp_traincomp
WORKER_BATCH_2_PROGRAMS := rp_publication rp_runbooks rp_projectrel rp_studyproto rp_stdesign rp_opsboard rp_reviewboard rp_controlplane rp_integrityplane rp_coherenceplane rp_mature rp_prov_view rp_prov_query rp_reldossier rp_decsupport rp_usable rp_usableproject
WORKER_BATCH_DIRECT_PROGRAMS := rp_compare_plain rp_test_suite
WORKER_BATCH_PROGRAMS := $(WORKER_BATCH_0_PROGRAMS) $(WORKER_BATCH_1_PROGRAMS) $(WORKER_BATCH_2_PROGRAMS)
WORKER_BATCH_0_OBJS := $(addprefix $(obj_dir)/worker_batch/,$(addsuffix .o,$(WORKER_BATCH_0_PROGRAMS)))
WORKER_BATCH_1_OBJS := $(addprefix $(obj_dir)/worker_batch/,$(addsuffix .o,$(WORKER_BATCH_1_PROGRAMS)))
WORKER_BATCH_2_OBJS := $(addprefix $(obj_dir)/worker_batch/,$(addsuffix .o,$(WORKER_BATCH_2_PROGRAMS)))
WORKER_BATCH_ENTRY_OBJS := $(addprefix $(obj_dir)/worker_batch/,$(addsuffix .o,$(WORKER_BATCH_PROGRAMS)))
# MAXFILE (274432) minus a fixed 16 KiB future-integration reserve.
WORKER_BATCH_FLAT_MAX := 258048

CH2_TESTS := $(CH2_BASE_TESTS)
CH3_TESTS := $(CH3_BASE_TESTS) ch3_
CH4_TESTS := $(CH4_BASE_TESTS) ch4_ ch3b_sleep ch3_
CH5_TESTS_BASE := ch5b_ $(CH4_TESTS) ch5_ ch5t_ usershell
CH6_TESTS := $(CH6_BASE_TESTS) ch6_ ch5_ ch4_
CH7_TESTS := $(CH7_BASE_TESTS) ch7_
CH8_TESTS := $(CH8_BASE_TESTS) ch8_ $(AGENT_TESTS)

ifeq ($(BASE), 1)
CH2_TESTS := $(CH2_BASE_TESTS)
CH3_TESTS := $(CH3_BASE_TESTS)
CH4_TESTS := $(CH4_BASE_TESTS)
CH5_TESTS_BASE := $(CH5_BASE_TESTS)
CH6_TESTS := $(CH6_BASE_TESTS)
CH7_TESTS := $(CH7_BASE_TESTS)
CH8_TESTS := $(CH8_BASE_TESTS) $(AGENT_TESTS)
endif

ifneq ($(filter $(CHAPTER),metadata_recovery observe_recovery),)
$(error CHAPTER=$(CHAPTER) is retired; use the live-query and workflow-fence Agent tests)
endif

ifeq ($(CHAPTER), 2)
CH_TESTS := $(CH2_TESTS)
else ifeq ($(CHAPTER), 3)
CH_TESTS := $(CH3_TESTS)
else ifeq ($(CHAPTER), 4)
CH_TESTS := $(CH4_TESTS)
else ifeq ($(CHAPTER), 5)
CH_TESTS := $(CH5_TESTS_BASE)
else ifeq ($(CHAPTER), 6)
CH_TESTS := $(CH6_TESTS)
else ifeq ($(CHAPTER), 7)
CH_TESTS := $(CH7_TESTS)
else ifeq ($(CHAPTER), 8)
CH_TESTS := $(CH8_TESTS)
else ifeq ($(CHAPTER), agent)
CH_TESTS := usershell $(AGENT_TESTS)
else ifeq ($(CHAPTER), agent_eval)
CH_TESTS := $(EVALUATION_TESTS)
else ifeq ($(CHAPTER), compat_eval)
CH_TESTS := $(COMPATIBILITY_TESTS)
else ifeq ($(CHAPTER), safety)
CH_TESTS := $(SAFETY_TESTS)
else ifeq ($(CHAPTER), fs_enospc)
CH_TESTS := $(FS_ENOSPC_TESTS)
else ifeq ($(CHAPTER), proc_reap)
CH_TESTS := $(PROC_REAP_TESTS)
else ifeq ($(CHAPTER), file_resource)
CH_TESTS := $(FILE_RESOURCE_TESTS)
else ifeq ($(CHAPTER), thread_resource)
CH_TESTS := $(THREAD_RESOURCE_TESTS)
else ifeq ($(CHAPTER), physical_resource)
CH_TESTS := $(PHYSICAL_RESOURCE_TESTS)
else ifeq ($(CHAPTER), workflow_teardown)
CH_TESTS := $(WORKFLOW_TEARDOWN_TESTS)
else ifeq ($(CHAPTER), virtio_disk)
CH_TESTS := $(VIRTIO_DISK_TESTS)
else ifeq ($(CHAPTER), platform)
CH_TESTS := $(PLATFORM_TESTS)
else ifeq ($(CHAPTER), platform_seeded)
CH_TESTS := $(PLATFORM_SEEDED_TESTS)
else ifeq ($(CHAPTER), platform_agentos)
CH_TESTS := rp_agentos_orch rp_resource_probe $(PLATFORM_TESTS) $(WORKER_BATCH_APPS)
else
CH_TESTS := usershell $(AGENT_TESTS)
endif

SELECTED_APPS = $(foreach t,$(CH_TESTS),$(filter $(t)%,$(APPS)))
STACK_USAGE_ALL_LIBRARY_SRCS := $(addprefix user/,$(sort $(LIB_C)))
STACK_USAGE_DATA_ONLY_LIBRARY_SRCS := user/lib/research_platform_state.c
STACK_USAGE_FUNCTION_LIBRARY_SRCS := $(filter-out $(STACK_USAGE_DATA_ONLY_LIBRARY_SRCS),$(STACK_USAGE_ALL_LIBRARY_SRCS))
STACK_USAGE_SUPPORT_SRCS := $(addprefix user/src/,$(addsuffix .c,$(WORKER_BATCH_PROGRAMS)))
STACK_USAGE_APPLICATION_SRCS := \
	$(filter-out $(STACK_USAGE_SUPPORT_SRCS) $(RETIRED_GUEST_SRCS),$(addprefix user/,$(sort $(SRCS)))) \
	$(COMPAT_BENCH_REPO_SOURCE)
STACK_USAGE_SRCS := $(STACK_USAGE_FUNCTION_LIBRARY_SRCS) $(STACK_USAGE_SUPPORT_SRCS) $(STACK_USAGE_APPLICATION_SRCS)
STACK_USAGE_OBJS = $(patsubst %.c,$(STACK_USAGE_DIR)/%.o,$(STACK_USAGE_SRCS))
STACK_USAGE_DATA_ONLY_OBJS = $(patsubst %.c,$(STACK_USAGE_DIR)/data-only/%.o,$(STACK_USAGE_DATA_ONLY_LIBRARY_SRCS))
WORKER_BATCH_STACK_USAGE_OBJS = $(addprefix $(STACK_USAGE_DIR)/user/src/,$(addsuffix .o,$(WORKER_BATCH_PROGRAMS)))
STACK_USAGE_UNIT_ARGS = \
	$(foreach src,$(STACK_USAGE_FUNCTION_LIBRARY_SRCS),--library-unit=$(src)) \
	$(foreach src,$(STACK_USAGE_SUPPORT_SRCS),--library-unit=$(src)) \
	$(foreach src,$(STACK_USAGE_APPLICATION_SRCS),--application-unit=$(src))
STACK_USAGE_POLICY_ARGS = \
	$(foreach edge,$(USER_STACK_INDIRECT_CALL_EDGES),--indirect-call-edge=$(edge))
STACK_USAGE_CFLAGS := $(COMMON_CFLAGS) \
	-Iuser/include -Iuser/lib -Iuser/$(arch_dir) -I$(abspath $(generated_dir))
STACK_USAGE_CFLAGS += $(USER_EXTRA_CFLAGS)

.PHONY: binary target clean stack-usage-build user-stack-check worker-batch-check worker-batch-selftest .FORCE

$(GENERATED_HEADERS):
	@mkdir -p $(@D)
	@printf '%s\n' '#ifndef __RP_HOST_ACTION_SEED_H__' '#define __RP_HOST_ACTION_SEED_H__' '#define RP_HOST_ACTION_SEED ""' '#define RP_HOST_ACTION_BOOTSTRAP_SEED ""' '#endif' > $@

$(EVALUATION_HEADER): .FORCE
	@mkdir -p $(@D)
	@printf '%s\n' \
		'#ifndef __AGENTEVAL_SEED_H__' \
		'#define __AGENTEVAL_SEED_H__' \
		'#define AGENTEVAL_CHALLENGE 0x$(AGENT_EVAL_CHALLENGE_HEX)ULL' \
		'#endif' > $@.tmp
	@if ! test -r $@ || ! cmp -s $@.tmp $@; then mv $@.tmp $@; else rm $@.tmp; fi

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

$(USER_BUILD_CONFIG): .FORCE
	@mkdir -p $(@D)
	@printf '%s\n' \
		'CC=$(CC)' \
		'CFLAGS=$(CFLAGS)' \
		'LDFLAGS=$(LDFLAGS)' \
		'CHAPTER=$(CHAPTER)' \
		'BASE=$(BASE)' \
		'USER_EXTRA_CFLAGS=$(USER_EXTRA_CFLAGS)' > $@.tmp
	@if ! test -r $@ || ! cmp -s $@.tmp $@; then mv $@.tmp $@; else rm $@.tmp; fi

.FORCE:

$(obj_dir)/%.o: %.c $(GENERATED_HEADERS)
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(obj_dir)/worker_batch/%.o: $(app_dir)/%.c $(GENERATED_HEADERS)
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -Dmain=$*_worker_entry -c $< -o $@

$(STACK_USAGE_DIR)/user/%.o: %.c $(GENERATED_HEADERS)
	@mkdir -p $(@D)
	@cd .. && $(CC_CMD) $(STACK_USAGE_CFLAGS) $(STACK_USAGE_PROFILE_CFLAGS) \
		-fstack-usage -fcallgraph-info=su -c user/$< \
		-o $(call shell_quote,$(abspath $@))

$(STACK_USAGE_DIR)/data-only/user/%.o: %.c $(GENERATED_HEADERS)
	@mkdir -p $(@D)
	@cd .. && $(CC_CMD) $(STACK_USAGE_CFLAGS) -c user/$< \
		-o $(call shell_quote,$(abspath $@))
	@if $(OBJDUMP_CMD) -t $(call shell_quote,$(abspath $@)) | \
		grep -Eq '[[:space:]]F[[:space:]]'; then \
		echo 'data-only stack unit defines a function: user/$<' >&2; \
		exit 1; \
	fi

# Profile the support entries under their real combined-image symbol names so
# the direct dispatcher switch edges close in the stack call graph.
$(WORKER_BATCH_STACK_USAGE_OBJS): STACK_USAGE_PROFILE_CFLAGS = \
	-Dmain=$(notdir $*)_worker_entry

$(STACK_USAGE_DIR)/evaluation_guest/compatbench.o: \
		$(COMPAT_BENCH_SOURCE) $(GENERATED_HEADERS) $(COMPAT_BENCH_HEADER)
	@mkdir -p $(@D)
	@cd .. && $(CC_CMD) $(STACK_USAGE_CFLAGS) $(STACK_USAGE_PROFILE_CFLAGS) \
		-fstack-usage -fcallgraph-info=su -c $(COMPAT_BENCH_REPO_SOURCE) \
		-o $(call shell_quote,$(abspath $@))

$(STACK_USAGE_DIR)/user/src/workflow_teardown_race_ucore.o: \
	STACK_USAGE_PROFILE_CFLAGS := \
	-DWORKFLOW_TEARDOWN_DOMAIN_FILE_CAP=$(USER_STACK_WORKFLOW_DOMAIN_FILE_CAP) \
	-DWORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP=$(USER_STACK_WORKFLOW_GLOBAL_RESERVED_CAP)

# 用 QEMU 运行器相同的超集配置测量条件测试代码，避免默认构建漏审调用路径。
$(STACK_USAGE_DIR)/user/src/agentfinal_ucore.o: STACK_USAGE_PROFILE_CFLAGS := \
	-DAGENT_CONTEXT_SYNC_TEST_PROFILE -DWAIT_ATOMIC_TEST_PROFILE
$(STACK_USAGE_DIR)/user/lib/syscall.o: STACK_USAGE_PROFILE_CFLAGS := \
	-DWAIT_ATOMIC_TEST_PROFILE
$(STACK_USAGE_DIR)/user/src/agenteval_ucore.o: $(EVALUATION_HEADER)

$(CRT_OBJ): $(arch_dir)/crt.S
	@mkdir -p $(@D)
	$(CC_CMD) $(CFLAGS) -c $< -o $@

$(elf_dir)/%: $(app_dir)/%.c $(LIB_OBJS) $(CRT_OBJ) $(GENERATED_HEADERS) $(arch_dir)/user.ld
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC_CMD) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) $< -o $@
	$(OBJDUMP_CMD) -d -S $@ > $(asm_dir)/$*.asm
	$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/$*

define RP_WORKER_BATCH_LINK_RULE
$(elf_dir)/rp_wbatch$(1): $(app_dir)/rp_wbatch$(1).c $$(LIB_OBJS) $$(CRT_OBJ) \
		$$(GENERATED_HEADERS) include/rp_program_manifest.h \
		include/rp_worker_batch.h $$(WORKER_BATCH_$(1)_OBJS) $$(arch_dir)/user.ld
	@mkdir -p $$(elf_dir) $$(bin_dir) $$(asm_dir)
	$$(CC_CMD) $$(CFLAGS) $$(LDFLAGS) $$(CRT_OBJ) $$(LIB_OBJS) \
		$$(WORKER_BATCH_$(1)_OBJS) $$(app_dir)/rp_wbatch$(1).c -o $$@
	$$(OBJDUMP_CMD) -d -S $$@ > $$(asm_dir)/rp_wbatch$(1).asm
	$$(OBJCOPY_CMD) -O binary $$@ $$(bin_dir)/rp_wbatch$(1)
	@size=`wc -c < $$(bin_dir)/rp_wbatch$(1)`; \
		if test "$$$$size" -gt $$(WORKER_BATCH_FLAT_MAX); then \
			echo "rp_wbatch$(1) exceeds reserved MAXFILE limit: $$$$size > $$(WORKER_BATCH_FLAT_MAX)" >&2; \
			exit 1; \
		fi
endef

$(foreach group,0 1 2,$(eval $(call RP_WORKER_BATCH_LINK_RULE,$(group))))

$(elf_dir)/agenteval_ucore: $(EVALUATION_HEADER)

$(elf_dir)/compatbench: $(COMPAT_BENCH_SOURCE) $(LIB_OBJS) $(CRT_OBJ) \
		$(GENERATED_HEADERS) $(COMPAT_BENCH_HEADER) $(arch_dir)/user.ld
	@mkdir -p $(elf_dir) $(bin_dir) $(asm_dir)
	$(CC_CMD) $(CFLAGS) $(LDFLAGS) $(CRT_OBJ) $(LIB_OBJS) \
		$(COMPAT_BENCH_SOURCE) -o $@
	$(OBJDUMP_CMD) -d -S $@ > $(asm_dir)/compatbench.asm
	$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/compatbench

$(LIB_OBJS) $(CRT_OBJ) $(addprefix $(elf_dir)/,$(SELECTED_APPS)): \
	$(USER_BUILD_CONFIG) $(USER_STACK_CONTRACT) $(USER_STACK_CONTRACT_WRAPPER)
$(WORKER_BATCH_ENTRY_OBJS): \
	$(USER_BUILD_CONFIG) $(USER_STACK_CONTRACT) $(USER_STACK_CONTRACT_WRAPPER)
$(STACK_USAGE_OBJS): $(USER_STACK_CONTRACT) $(USER_STACK_CONTRACT_WRAPPER)

worker-batch-check:
	@$(PYTHON_CMD) $(WORKER_BATCH_CHECKER) --root ..

worker-batch-selftest:
	@$(PYTHON_CMD) $(WORKER_BATCH_SELFTEST)

binary: worker-batch-check $(addprefix $(elf_dir)/,$(SELECTED_APPS))

stack-usage-build: $(STACK_USAGE_OBJS) $(STACK_USAGE_DATA_ONLY_OBJS)

user-stack-check:
	@set -eu; \
		$(PYTHON_CMD) $(USER_STACK_CONTRACT_CHECKER) --root ..; \
		scratch="$$(mktemp -d "$${TMPDIR:-/tmp}/agentos-user-stack.XXXXXX")"; \
		trap 'rm -rf "$$scratch"' EXIT HUP INT TERM; \
		$(MAKE) --no-print-directory -s stack-usage-build \
			build_dir="$$scratch/build" \
			STACK_USAGE_DIR="$$scratch/usage"; \
		$(PYTHON_CMD) $(USER_STACK_CHECKER) \
			--usage-dir "$$scratch/usage" --source-dir .. \
			--contract-header $(USER_STACK_CONTRACT) \
			$(STACK_USAGE_UNIT_ARGS) $(STACK_USAGE_POLICY_ARGS)

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
