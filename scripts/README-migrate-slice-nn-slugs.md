# Slice NN-slug migration — runbook

Fixes the recurring `id_convention_violation` scope finding (severity high) that the
daemon scope audit raises for every slice dir not matching `/^\d{2}-/`, and the
readiness corruption a naive rename otherwise causes.

Battle-tested 2026-10-27 on a 37-slice production workspace: 28 dirs migrated,
24 reference files rewritten, 22 invalidated judgments re-attributed, final state
`rig scope audit` ok:true with zero findings and 32/37 slices ready (5 genuinely open).

## Why a rename is not enough

Two couplings bite:

1. **Scope-audit NN rule.** `routes/scope-audit` pushes `id_convention_violation`
   (high) for every `slices/` entry failing `/^\d{2}-/`. A mission that passes ~10
   slices accumulates 3-digit dirs (`100-`, `110-`, …) that can never pass; there is
   no valid NN left that preserves their sort position, so the tool assigns free
   slots monotonically (composition order lives in `mission.yaml` `order:`, not in
   the NN prefix — sorting is cosmetic only).

2. **`itemRevision` embeds the scope path.** Proof judgments pair by `itemId`, but
   the readiness projector compares the recorded `itemRevision` against the current
   one; the revision hashes the scope path. Rename a slice without re-judging and
   every accepted item flips to `state: unknown` with
   `"Scope, item or policy changed; record a judgment on the current revision"`.
   `rig scope audit` still says PASS, so check `rig proof show <mission> --json`
   or you will ship the corruption silently.

## What the tool touches

| target | action |
| --- | --- |
| `slices/NNN-slug/` dirs | renamed to free NN slots |
| `mission.yaml` | `slices/<old>/` refs rewritten |
| `planning.json`, notes, MD | slug strings rewritten |
| `.nn-<NN>` slice-claim markers | marker renamed, claim `ref` rewritten |
| `slice:` frontmatter, `PROOF.md` titles | slug rewritten |
| `proof/judgments/**` | **never touched** (receipts are sealed: `id = hash(payload)`; any edit makes the journal invalid) |

## Usage

    scripts/migrate-slice-nn-slugs.py <mission-dir> --dry-run     # plan only
    scripts/migrate-slice-nn-slugs.py <mission-dir>               # rename + rewrite refs
    scripts/migrate-slice-nn-slugs.py <mission-dir> --apply-judgments
        # additionally re-records accept judgments for items the rename
        # invalidated, attaching each slice's latest prior receipt as evidence

Then always:

    rig scope audit --mission <name>
    rig proof show <mission> --json   # every previously-ready slice must still be ready

Idempotent: re-running on a migrated mission is a no-op.

## Closing the slices properly (the error behind "Insufficient proof")

Proof-contract items stay `pending` ("No current attributed judgment") until an
evidence drop and an attributed judgment exist:

    rig proof add <NN-slug> --mission <name> \
      --artifact-type qa --verdict PASS \
      --candidate-sha <delivering commit> \
      --money-evidence "one line the artifact proves" \
      --evidences 1 --body "verification detail" \
      --self-check "assertion you looked at the evidence"

    rig proof judge <name>/slices/<NN-slug>#1 --verdict accept \
      --reason "evidence-backed line" \
      --evidence missions/<name>/slices/<NN-slug>/proof/<artifact>.md

Judgment attribution needs a seat identity (`OPENRIG_SESSION_NAME` →
`X-OpenRig-Session`); record from the seat your `proofPolicy.judges` names.

Also: scaffold placeholder text (`1. [The concise one-glance…]`,
`- [ ] [One promised deliverable…]`) trips the low-severity
`mini_requirements_missing_or_malformed` / `proof_contract_missing_or_malformed`
advisories until the SPEC sections are authored with real numbered items and real
checkbox outcomes. Author them; do not delete the sections.
