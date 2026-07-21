#include <assert.h>
#include <stdio.h>

#define FSSIZE 16384U
#define NINODE 2048U
#include "../fs_storage_policy.h"

int main(void)
{
	unsigned int system_blocks = fs_policy_system_reserve(
		0, 16060, FS_SYSTEM_BLOCK_MIN_RESERVE);
	unsigned int system_inodes = fs_policy_system_reserve(
		0, 2047, FS_SYSTEM_INODE_MIN_RESERVE);
	unsigned int workflow_blocks;
	unsigned int workflow_inodes;
	unsigned int checksum;

	assert(system_blocks == FS_SYSTEM_BLOCK_MIN_RESERVE);
	assert(system_inodes == 64);
	workflow_blocks = fs_policy_workflow_guarantee(
		FS_WORKFLOW_BLOCK_RESERVE, 16060, system_blocks, 6890,
		FS_WORKFLOW_BLOCK_MIN_PER_SCOPE);
	workflow_inodes = fs_policy_workflow_guarantee(
		FS_WORKFLOW_INODE_RESERVE, 2047, system_inodes, 1892,
		FS_WORKFLOW_INODE_MIN_PER_SCOPE);
	assert(workflow_blocks == 1195);
	assert(workflow_inodes == 342);
	checksum = fs_policy_contract_checksum(
		FS_STORAGE_POLICY_VERSION, FS_WORKFLOW_SCOPE_SLOTS,
		workflow_blocks, workflow_inodes, system_blocks, system_inodes);
	assert(fs_policy_contract_geometry_valid(
		16060, 2047, FS_STORAGE_POLICY_VERSION,
		FS_WORKFLOW_SCOPE_SLOTS, workflow_blocks, workflow_inodes,
		system_blocks, system_inodes, checksum));
	assert(!fs_policy_contract_geometry_valid(
		16060, 2047, FS_STORAGE_POLICY_VERSION,
		FS_WORKFLOW_SCOPE_SLOTS, workflow_blocks, workflow_inodes,
		system_blocks, system_inodes, checksum ^ 1U));
	assert(fs_policy_contract_initially_funded(
		6890, 1892, workflow_blocks, workflow_inodes,
		system_blocks, system_inodes));

	// G is persisted by mkfs. After SYSTEM legitimately spends its reserve,
	// reboot validates the same G and reconstructs a zero remaining credit.
	assert(fs_policy_contract_runtime_funded(
		FS_WORKFLOW_SCOPE_SLOTS * workflow_blocks,
		FS_WORKFLOW_SCOPE_SLOTS * workflow_inodes,
		workflow_blocks, workflow_inodes));
	assert(!fs_policy_contract_initially_funded(
		FS_WORKFLOW_SCOPE_SLOTS * workflow_blocks,
		FS_WORKFLOW_SCOPE_SLOTS * workflow_inodes,
		workflow_blocks, workflow_inodes, system_blocks, system_inodes));
	assert(fs_policy_system_remaining(
		FS_WORKFLOW_SCOPE_SLOTS * workflow_blocks, workflow_blocks,
		system_blocks) == 0);
	assert(fs_policy_system_remaining(
		FS_WORKFLOW_SCOPE_SLOTS * workflow_inodes, workflow_inodes,
		system_inodes) == 0);
	assert(!fs_policy_contract_runtime_funded(
		FS_WORKFLOW_SCOPE_SLOTS * workflow_blocks - 1,
		FS_WORKFLOW_SCOPE_SLOTS * workflow_inodes,
		workflow_blocks, workflow_inodes));

	puts("test_fs_storage_policy: passed");
	return 0;
}
