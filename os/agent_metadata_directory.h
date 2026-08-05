#ifndef AGENT_METADATA_DIRECTORY_H
#define AGENT_METADATA_DIRECTORY_H

struct inode;
void agent_fs_note_create(struct inode *, char *);
void agent_fs_note_write(struct inode *);
void agent_fs_note_truncate(struct inode *);
void agent_fs_note_delete(struct inode *);

#endif
