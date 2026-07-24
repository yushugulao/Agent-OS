#!/usr/bin/env python3
"""Unit tests for fully drained specialized-kernel log contracts."""

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("validate-kernel-test-log.py")
SPEC = importlib.util.spec_from_file_location("kernel_log_validator", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validator)


class KernelLogValidatorTests(unittest.TestCase):
    def test_thread_contract_checks_order_uniqueness_and_counts(self):
        lines = [
            (
                marker
                if marker != "threadresource_ucore: domain_fairness=1"
                else marker + " hog=575 victim=512 bound=576"
            )
            for marker in validator.THREAD_MARKERS
        ]
        self.assertIn("hog=575", validator.validate_thread("\n".join(lines)))

        with self.assertRaisesRegex(
            validator.ValidationError, "not unique"
        ):
            validator.validate_thread("\n".join(lines + [lines[0]]))
        with self.assertRaisesRegex(
            validator.ValidationError, "fairness mismatch"
        ):
            validator.validate_thread(
                "\n".join(lines).replace("hog=575", "hog=577")
            )

    def test_file_contract_checks_all_seven_markers(self):
        text = "\n".join(validator.FILE_MARKERS)
        self.assertIn("positions=", validator.validate_file(text))
        with self.assertRaisesRegex(validator.ValidationError, "missing"):
            validator.validate_file(text.replace(validator.FILE_MARKERS[2], ""))

    def test_syscall_contract_checks_phase_and_short_marker_order(self):
        lines = []
        for name, begin, peer, end in validator.SYSCALL_PHASES:
            lines.extend((begin, peer))
            if name == "inode":
                lines.append("SYSCALLFAIR_INODE_SHORT")
            lines.append(end)
        lines.append("syscallfair_ucore: parent passed")
        text = "\n".join(lines)
        self.assertIn("trunc=", validator.validate_syscall(text))

        with self.assertRaisesRegex(
            validator.ValidationError, "completion marker mismatch"
        ):
            validator.validate_syscall(
                text + "\nsyscallfair_ucore: parent passed"
            )

    def test_fs_domain_and_reserve_contracts_preserve_boundaries(self):
        domain = list(validator.FS_QUOTA_MARKERS)
        domain.append("fsquota_ucore: quota_reuse=1")
        domain_text = "\n".join(domain)
        domain_text = domain_text.replace(
            "fsquota_ucore: public_version_churn=1",
            "fsquota_ucore: public_version_churn=1 cycles=513",
        ).replace(
            "fsquota_ucore: public_domain_limited=1",
            "fsquota_ucore: public_domain_limited=1 blocks=16 inodes=8",
        )
        domain_text += "\nfsquota_ucore: parent passed"
        self.assertIn(
            "domain blocks=16",
            validator.validate_fs(
                domain_text,
                "domain",
                "fsquota_ucore: parent passed",
            ),
        )

        reserve_text = "\n".join(validator.FS_QUOTA_MARKERS)
        reserve_text = reserve_text.replace(
            "fsquota_ucore: public_version_churn=1",
            "fsquota_ucore: public_version_churn=1 cycles=700",
        ).replace(
            "fsquota_ucore: public_domain_limited=1",
            "fsquota_ucore: public_domain_limited=1 blocks=64 inodes=16",
        )
        reserve_text += "\nfsquota_ucore: parent passed"
        self.assertIn(
            "reserve blocks=64",
            validator.validate_fs(
                reserve_text,
                "reserve",
                "fsquota_ucore: parent passed",
            ),
        )

    def test_fs_persistent_profiles_preserve_exact_values_and_order(self):
        seed = "\n".join(
            (
                "fspquota_ucore: sponsored_object_charged=1 blocks=14",
                (
                    "fspquota_ucore: durable_fixture=1 blocks=18 "
                    "inodes=8 owner_exited=1"
                ),
            )
        )
        self.assertEqual(
            validator.validate_fs(
                seed,
                "persistent-seed",
                "fspquota_ucore: durable_fixture=1",
            ),
            "persistent-seed",
        )

        marker = "fspquota_ucore: parent passed"
        verify = "\n".join(validator.FS_PERSISTENT_MARKERS + (marker,))
        self.assertEqual(
            validator.validate_fs(verify, "persistent-verify", marker),
            "persistent-verify",
        )
        with self.assertRaisesRegex(validator.ValidationError, "out of order"):
            validator.validate_fs(
                "\n".join(
                    (
                        validator.FS_PERSISTENT_MARKERS[1],
                        validator.FS_PERSISTENT_MARKERS[0],
                        *validator.FS_PERSISTENT_MARKERS[2:],
                        marker,
                    )
                ),
                "persistent-verify",
                marker,
            )


if __name__ == "__main__":
    unittest.main()
