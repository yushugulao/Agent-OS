.DEFAULT_GOAL = fs.img

U := ../user
USER_BIN_DIR := target/bin
USER_BINS := $(wildcard $(U)/$(USER_BIN_DIR)/*)

FS_FUSE := fs

.PHONY: .FORCE clean

$(FS_FUSE): fs.c fs.h types.h

fs.img: .FORCE $(FS_FUSE) $(USER_BINS)
	@set -e; tmp="$@.$$$$.tmp"; \
		trap 'rm -f "$$tmp"' 0 1 2 3 15; \
		./$(FS_FUSE) "$$tmp" $(USER_BINS); \
		mv -f "$$tmp" $@; \
		trap - 0 1 2 3 15

.FORCE:

clean:
	rm -f *.img $(FS_FUSE)
