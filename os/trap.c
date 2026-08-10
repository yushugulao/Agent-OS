#include "trap.h"
#include "agent.h"
#include "bio.h"
#include "console.h"
#include "defs.h"
#include "loader.h"
#include "kernel_work.h"
#include "plic.h"
#include "syscall.h"
#include "timer.h"
#include "virtio.h"
#include "proc.h"

extern char trampoline[], uservec[];
extern char userret[], kernelvec[];

void kerneltrap();

void set_kerneltrap()
{
	w_stvec((uint64)kernelvec & ~0x3); // DIRECT
}

void trap_init()
{
	set_kerneltrap();
	w_sie(r_sie() | SIE_SEIE | SIE_STIE | SIE_SSIE);
}

static void
unknown_user_trap(void)
{
	errorf("unknown trap: %p, stval = %p", r_scause(), r_stval());
	exit(-1);
}

static int
devintr(uint64 cause)
{
	int irq;
	switch (cause) {
	case SupervisorTimer:
		set_next_timer();
		console_input_tick();
		virtio_disk_tick();
		bio_policy_tick();
		agent_tick();
		// 用户态仅在存在可运行竞争者时让出处理器。
		if ((r_sstatus() & SSTATUS_SPP) == 0) {
			if (scheduler_has_runnable_peer())
				yield();
		} else {
			kernel_work_request_resched();
		}
		return 1;
	case SupervisorExternal:
		irq = plic_claim();
		if (irq == UART0_IRQ) {
		} else if (irq == VIRTIO0_IRQ) {
			virtio_disk_intr();
		} else if (irq) {
			infof("unexpected interrupt irq=%d\n", irq);
		}
		if (irq)
			plic_complete(irq);
		return 1;
	default:
		return 0;
	}
}

void usertrap()
{
	set_kerneltrap();
	struct trapframe *trapframe = curr_thread()->trapframe;
	tracef("trap from user epc = %p", trapframe->epc);
	if ((r_sstatus() & SSTATUS_SPP) != 0)
		panic("usertrap: not from user mode");

	uint64 cause = r_scause();
	if (cause & (1ULL << 63)) {
		if (!devintr(cause & 0xff))
			unknown_user_trap();
		/*
		 * 时钟驱动的维护输入输出必须借用真实的已调度线程。每次用户态时钟
		 * 中断最多执行一个检查点，使计算密集进程也能推进后台任务，同时
		 * 避免在调度器空闲栈上发起输入输出。
		 */
		kernel_work_begin_background();
		agent_background_checkpoint();
		kernel_work_end_background();
	} else {
		switch (cause) {
		case UserEnvCall:
			trapframe->epc += 4;
			syscall();
			break;
		case StorePageFault:
			if (uvm_cow_fault(curr_proc()->pagetable, r_stval()) == 0)
				break;
		/* 继续执行后续分支。 */
		case StoreMisaligned:
		case InstructionMisaligned:
		case InstructionPageFault:
		case LoadMisaligned:
		case LoadPageFault:
			errorf("%d in application, bad addr = %p, bad instruction = %p, "
			       "core dumped.",
			       cause, r_stval(), trapframe->epc);
			exit(-2);
			break;
		case IllegalInstruction:
			errorf("IllegalInstruction in application, core dumped.");
			exit(-3);
			break;
		default:
			unknown_user_trap();
			break;
		}
	}
	usertrapret();
}

void usertrapret()
{
	if (proc_thread_exit_requested())
		exit(curr_proc()->exit_code);
	if (agent_task_deadline_due_current()) {
		kernel_work_begin_background();
		if (agent_task_deadline_checkpoint() < 0)
			agent_background_request();
		kernel_work_end_background();
	}
	kernel_stack_check(curr_thread());
	w_stvec(((uint64)TRAMPOLINE + (uservec - trampoline)) & ~0x3);
	struct trapframe *trapframe = curr_thread()->trapframe;
	trapframe->kernel_satp = r_satp(); // kernel page table
	trapframe->kernel_sp =
		curr_thread()->kstack + KSTACK_SIZE; // process's kernel stack
	trapframe->kernel_trap = (uint64)usertrap;
	trapframe->kernel_hartid = r_tp(); // unuesd

	w_sepc(trapframe->epc);

	uint64 x = r_sstatus();
	x &= ~SSTATUS_SPP; // clear SPP to 0 for user mode
	x |= SSTATUS_SPIE; // enable interrupts in user mode
	w_sstatus(x);

	uint64 satp = MAKE_SATP(curr_proc()->pagetable);
	uint64 fn = TRAMPOLINE + (userret - trampoline);
	uint64 trapframe_va = get_thread_trapframe_va(curr_thread()->tid);
	debugf("return to user @ %p, sp @ %p", trapframe->epc, trapframe->sp);
	((void (*)(uint64, uint64))fn)(trapframe_va, satp);
}

void kerneltrap()
{
	uint64 sepc = r_sepc();
	uint64 sstatus = r_sstatus();
	uint64 scause = r_scause();

	debugf("kernel trap: epc = %p, cause = %d", sepc, scause);

	if ((sstatus & SSTATUS_SPP) == 0)
		panic("kerneltrap: not from supervisor mode");

	if (scause & (1ULL << 63)) {
		if (!devintr(scause & 0xff)) {
			errorf("unknown trap: %p, stval = %p", scause,
			       r_stval());
			panic("unknown supervisor trap");
		}
	} else {
		errorf("invalid trap from kernel: %p, stval = %p sepc = %p\n",
		       scause, r_stval(), sepc);
		/*
		 * 进程拆除可能在关闭文件和结算输入输出时休眠。任意监管态故障可能仍
		 * 持有缓冲区或内核锁，不能安全进入该状态机。
		 */
		panic("invalid supervisor trap");
	}
	w_sepc(sepc);
	w_sstatus(sstatus);
}
