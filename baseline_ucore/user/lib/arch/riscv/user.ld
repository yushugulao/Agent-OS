OUTPUT_ARCH(riscv)
ENTRY(_start)

SECTIONS {

    .text : { 
        *(.text.entry)
        *(.text .text.*)
    }

    .bss : { 
        *(.bss .bss.*)
        *(.sbss .sbss.*)
    }

    .data : { 
        *(.data .data.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        *(.sdata .sdata.*)
    }
    
    /DISCARD/ : { *(.eh_*) }
}
