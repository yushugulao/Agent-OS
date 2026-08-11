#ifndef EXEC_IMAGE_POLICY_H
#define EXEC_IMAGE_POLICY_H

/* 受保护 SYSTEM 可执行映像的共享生产者/消费者契约；身份信任与委派能力上限分别检查。 */
static inline int
exec_image_profile_valid(unsigned int profile)
{
	return profile == VFS_EXEC_PROFILE_NONE ||
	       profile == VFS_EXEC_PROFILE_WORKFLOW ||
	       profile == VFS_EXEC_PROFILE_CONTENT_READ ||
	       profile == VFS_EXEC_PROFILE_ARTIFACT_WRITE;
}

enum exec_image_policy_class {
	EXEC_IMAGE_INVALID = 0,
	EXEC_IMAGE_COMPAT,
	EXEC_IMAGE_WORKER,
	EXEC_IMAGE_TRUSTED_ENDPOINT,
	EXEC_IMAGE_TRUSTED_AGENT,
};

static inline enum exec_image_policy_class
exec_image_protected_classify(int regular_file, unsigned long long size,
			      unsigned int flags, unsigned int generation,
			      unsigned int role_mask,
			      unsigned int layout_version,
			      unsigned int rw_offset, unsigned int profile,
			      unsigned int page_size)
{
	unsigned int required = EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE;
	int bootstrap = (flags & EXEC_FLAG_BOOTSTRAP) != 0;
	int trusted = (flags & EXEC_FLAG_TRUSTED) != 0;

	if (!regular_file || page_size == 0 ||
	    !exec_image_profile_valid(profile) ||
	    (flags & ~EXEC_FLAG_KNOWN) != 0 ||
	    (flags & required) != required ||
	    generation != EXEC_MANIFEST_VERSION ||
	    (role_mask & ~EXEC_MANIFEST_ROLE_ALL) != 0 ||
	    layout_version != EXEC_LAYOUT_VERSION || rw_offset < page_size ||
	    (rw_offset % page_size) != 0 || size <= rw_offset)
		return EXEC_IMAGE_INVALID;
	if (!trusted)
		return !bootstrap && role_mask == 0 &&
		       profile != VFS_EXEC_PROFILE_NONE ?
			       EXEC_IMAGE_WORKER : EXEC_IMAGE_INVALID;
	if (profile == VFS_EXEC_PROFILE_NONE)
		return bootstrap ? EXEC_IMAGE_INVALID : EXEC_IMAGE_COMPAT;
	if (role_mask == 0)
		return bootstrap ? EXEC_IMAGE_INVALID :
				   EXEC_IMAGE_TRUSTED_ENDPOINT;
	return EXEC_IMAGE_TRUSTED_AGENT;
}

static inline int
exec_image_protected_shape_valid(int regular_file, unsigned long long size,
				 unsigned int flags, unsigned int generation,
				 unsigned int role_mask,
				 unsigned int layout_version,
				 unsigned int rw_offset, unsigned int profile,
				 unsigned int page_size)
{
	return exec_image_protected_classify(
		regular_file, size, flags, generation, role_mask, layout_version,
		rw_offset, profile, page_size) != EXEC_IMAGE_INVALID;
}

#endif
