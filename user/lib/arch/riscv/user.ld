OUTPUT_ARCH(riscv)
ENTRY(_start)

PHDRS {
    text PT_LOAD FLAGS(5);
    data PT_LOAD FLAGS(6);
}

SECTIONS {

    .text : {
        *(.text.entry)
        *(.text .text.*)
	*(.rodata .rodata.*)
	*(.srodata .srodata.*)
    } :text

    . = ALIGN(4096);

    .bss : {
        *(.bss .bss.*)
        *(.sbss .sbss.*)
    } :data

    .data : {
        *(.data .data.*)
        *(.sdata .sdata.*)
	/* Materialize trailing BSS in the flat binary consumed by uCore. */
	BYTE(0)
    } :data

    /DISCARD/ : { *(.eh_*) }
}
