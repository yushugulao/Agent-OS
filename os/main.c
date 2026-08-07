#include "console.h"
#include "agent.h"
#include "bio.h"
#include "defs.h"
#include "fs_epoch.h"
#include "loader.h"
#include "plic.h"
#include "timer.h"
#include "trap.h"
#include "virtio.h"

void clean_bss()
{
	extern char s_bss[];
	extern char e_bss[];
	memset(s_bss, 0, e_bss - s_bss);
}

void main()
{
	clean_bss();
	printf("hello world!\n");
	proc_init();
	console_init();
	agentinit();
	kinit();
	kvm_init();
	if (kalloc_physical_policy_init(PHYSICAL_PAGE_SYSTEM_RESERVE,
					PHYSICAL_PAGE_ORDINARY_LIMIT) < 0)
		panic("physical page policy");
	trap_init();
	plicinit();
	virtio_disk_init();
	binit();
	fsinit();
	timer_init();
	agent_storage_init();
	fs_epoch_runtime_enable();
	load_init_app();
	infof("start scheduler!");
	/* 仅启动期使用的映像加载完成后，才开放运行时输入输出准入。 */
	bio_policy_start();
	virtio_disk_runtime_start();
	scheduler();
}
