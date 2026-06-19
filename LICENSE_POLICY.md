# License Compliance Policy

This repository follows the contest requirement that source code must use at least one of GPL, Apache, BSD, or Mulan licenses, and that technical documents and defense materials must use CC BY-SA 4.0.

## Source Code

The submitted source code is licensed under GPL-3.0, as provided in [LICENSE](LICENSE).

This applies to:

- kernel source under `os/`;
- user programs and user libraries under `user/`;
- filesystem image tools under `nfs/`;
- executable helper scripts under `scripts/`;
- build files, CI workflow files, linker scripts, and other source-like project files.

The upstream uCore tutorial code and tutorial test structure used by this project are GPL-3.0 projects. Project-specific Agent-OS source code, test programs, build integration, and modifications are distributed as GPL-3.0 together with the rest of the submitted source tree.

The optional RustSBI bootloader binary under `bootloader/rustsbi-qemu.bin` is a third-party firmware dependency. RustSBI is licensed by its upstream project under MIT or Mulan PSL v2. It is recorded in [NOTICE](NOTICE). The verified default run path uses OpenSBI through QEMU; the bundled RustSBI binary is retained only as an optional bootloader path.

## Documentation And Defense Materials

Technical documents, verification notes, architecture descriptions, Markdown demo narratives, slide decks, and videos produced for this project are licensed under Creative Commons Attribution-ShareAlike 4.0 International, as provided in [DOCUMENTATION_LICENSE.md](DOCUMENTATION_LICENSE.md).

This applies to:

- `README.md`;
- all Markdown documents under `docs/`;
- `DOCUMENTATION_LICENSE.md`, `NOTICE`, and this policy document;
- future defense slide decks, speech notes, diagrams, and videos produced by the team for this project.

Executable scripts and test programs are source code, not documentation. They follow GPL-3.0 unless a third-party notice states otherwise.

## Rules For Future Additions

Do not add closed-source, private-use, non-open, noncommercial-only, no-derivatives, or otherwise incompatible materials to the submitted repository.

When adding third-party source code, documentation, figures, binaries, or generated assets:

1. Confirm that the license is compatible with this policy.
2. Preserve upstream copyright and license notices.
3. Record the source, purpose, and license in [NOTICE](NOTICE).
4. For defense slides or videos, include a visible CC BY-SA 4.0 notice and attribution page.

## Pre-Submission Review

Before packaging or uploading materials, review the repository against these points:

- `LICENSE` is present and states GPL-3.0 for source code.
- `DOCUMENTATION_LICENSE.md` is present and states CC BY-SA 4.0 for technical documents and defense materials.
- `NOTICE` records third-party code, test structure, firmware binaries, documentation, images, or generated assets used by the project.
- New source-like files follow GPL-3.0 unless a compatible third-party notice states otherwise.
- New documents, slides, diagrams, and videos include or reference the CC BY-SA 4.0 notice.
- No closed-source, private-use, non-open, noncommercial-only, no-derivatives, or otherwise incompatible material is included in the submitted repository.
