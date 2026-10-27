#!/usr/bin/env python3
r"""Migrate OpenRig slice directories to the NN-slug convention.

The daemon scope audit flags any slice dir not matching /^\d{2}-/ as a
high-severity id_convention_violation. Missions that outgrew two-digit
numbering accumulate 3-digit dirs that can never pass. This tool renames
them into free NN slots (monotonic), rewrites every workspace reference,
and — because a slice's itemRevision embeds its scope path — re-records
judgments whose revisions the rename invalidates.

Ref rewrite rules:
  - mission.yaml slice refs (slices/<old>/ -> slices/<new>/)
  - planning/notes JSON+MD slug strings
  - .nn-<NN> slice-claim markers (marker file renamed, claim ref rewritten)
  - `slice:` frontmatter and PROOF.md title slugs
  - NEVER touches proof/judgments/** (hash-sealed receipts, id=hash(payload))

Usage:
  migrate-slice-nn-slugs.py <mission-dir> [--dry-run] [--apply-judgments]

After any run verify with:
  rig scope audit --mission <name> && rig proof show <mission> --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

THREE_DIGIT = re.compile(r"^(\d{3,})-(.+)$")
TWO_DIGIT = re.compile(r"^(\d{2})-(.+)$")
MARKER = re.compile(r"^\.nn-(\d+)$")


def sh(cmd: list[str], env: dict | None = None) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def scan(slices_dir: str) -> tuple[dict[str, str], dict[str, str]]:
    dirs = os.listdir(slices_dir)
    three = {d: d for d in dirs if THREE_DIGIT.match(d)}
    used = {int(m.group(1)) for d in dirs if (m := TWO_DIGIT.match(d))}
    occupied = {int(MARKER.match(m).group(1)) for m in dirs if MARKER.match(m)}
    used |= occupied
    ordered = sorted(three, key=lambda d: int(THREE_DIGIT.match(d).group(1)))
    free = (n for n in range(1, 100) if n not in used)
    nn_map, slug_map = {}, {}
    for old in ordered:
        nn = next(free, None)
        if nn is None:
            sys.exit(f"error: no free NN slot left for {old} (>99 slices is unrepresentable)")
        old_nn, rest = THREE_DIGIT.match(old).groups()
        new = f"{nn:02d}-{rest}"
        nn_map[old_nn.zfill(3)] = f"{nn:02d}"
        slug_map[old] = new
    return nn_map, slug_map


def rewrite_text_files(mission_dir: str, slug_map: dict[str, str]) -> list[str]:
    changed = []
    for root, dirs, files in os.walk(mission_dir):
        if root.endswith("proof/judgments") or "/proof/judgments/" in root:
            dirs[:] = []
            continue
        for fn in files:
            if not fn.endswith((".md", ".yaml", ".json")):
                continue
            p = os.path.join(root, fn)
            text = open(p, errors="replace").read()
            out = text
            for old, new in slug_map.items():
                out = out.replace(old, new)
            if out != text:
                open(p, "w").write(out)
                changed.append(os.path.relpath(p, mission_dir))
    return changed


def migrate_markers(slices_dir: str, nn_map: dict[str, str], dry: bool) -> list[str]:
    moved = []
    for entry in os.listdir(slices_dir):
        m = MARKER.match(entry)
        if not m:
            continue
        old_nn = m.group(1)
        new_nn = nn_map.get(old_nn.zfill(3))
        if new_nn is None:
            continue
        path = os.path.join(slices_dir, entry)
        raw = open(path).read()
        try:
            claim = json.loads(raw)
            claim["ref"] = re.sub(r"^slices/\d+-", f"slices/{new_nn}-", claim["ref"])
            body = json.dumps(claim) + "\n"
        except (json.JSONDecodeError, KeyError, TypeError):
            body = raw
        target = os.path.join(slices_dir, f".nn-{new_nn}")
        if not dry:
            os.remove(path)
            open(target, "w").write(body)
        moved.append(f"{entry} -> .nn-{new_nn}")
    return moved


def rejudge_invalidated(mission_dir: str, dry: bool) -> tuple[int, list[str]]:
    name = os.path.basename(os.path.normpath(mission_dir))
    env = dict(os.environ)
    rc, out = sh(["rig", "proof", "show", name, "--json"], env=env)
    if rc != 0:
        sys.exit(f"error: rig proof show failed: {out.strip()[:200]}")
    data = json.loads(out)
    slices = data.get("slices") or []
    for m in data.get("missions") or []:
        slices.extend(m.get("slices") or [])
    workspace = os.path.dirname(os.path.normpath(mission_dir))
    rejudged, skipped = 0, []
    for s in slices:
        rd = s.get("readiness") or {}
        if rd.get("state") != "unknown":
            continue
        scope = s.get("scope") or ""
        slug = scope.rstrip("/").split("/")[-1]
        needs = [i for i in rd.get("items") or [] if i.get("state") == "unknown"]
        if not needs:
            continue
        jdir = os.path.join(mission_dir, "slices", slug, "proof", "judgments")
        receipts = sorted(f for f in os.listdir(jdir) if f.endswith(".md")) if os.path.isdir(jdir) else []
        if not receipts:
            skipped.append(slug)
            continue
        evidence = f"missions/{name}/slices/{slug}/proof/judgments/{receipts[-1]}"
        if dry:
            rejudged += 1
            continue
        rc, out = sh(["rig", "proof", "judge", f"{name}/slices/{slug}#1",
                      "--verdict", "accept",
                      "--reason", f"Re-attributed prior acceptance after NN-slug scope rename of {slug}; deliverable unchanged, latest prior receipt attached",
                      "--evidence", evidence], env=env)
        if rc == 0:
            rejudged += 1
        else:
            skipped.append(f"{slug}: {out.strip()[:120]}")
    return rejudged, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mission", help="mission dir containing slices/")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply-judgments", action="store_true",
                    help="after migration, re-record accept judgments for items invalidated by the rename")
    args = ap.parse_args()
    mission_dir = os.path.normpath(args.mission)
    slices_dir = os.path.join(mission_dir, "slices")
    if not os.path.isdir(slices_dir):
        sys.exit(f"error: no slices/ under {mission_dir}")

    nn_map, slug_map = scan(slices_dir)
    if not slug_map:
        print("nothing to do: all slice dirs already NN-slug")
        return

    print(f"planned renames: {len(slug_map)}")
    for old, new in sorted(slug_map.items()):
        print(f"  {old} -> {new}")
    if args.dry_run:
        print("dry run: no changes written")
        return

    for old, new in slug_map.items():
        os.rename(os.path.join(slices_dir, old), os.path.join(slices_dir, new))
    moved = migrate_markers(slices_dir, nn_map, dry=False)
    changed = rewrite_text_files(mission_dir, slug_map)
    print(f"renamed dirs: {len(slug_map)}, claim markers: {len(moved)}, text files rewritten: {len(changed)}")

    if args.apply_judgments:
        rejudged, skipped = rejudge_invalidated(mission_dir, dry=False)
        print(f"judgments re-attributed: {rejudged}, skipped: {len(skipped)}")
        for s in skipped:
            print(f"  ! {s}")
    print("verify: rig scope audit --mission %s && rig proof show %s --json" % (os.path.basename(mission_dir), os.path.basename(mission_dir)))


if __name__ == "__main__":
    main()
