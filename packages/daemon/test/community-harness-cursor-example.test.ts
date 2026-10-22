import { afterEach, describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { loadCommunityHarnessPreset } from "../src/domain/community-harness-presets.js";
import { newLaunchAttempt, readLaunchResult } from "../src/domain/community-harness-launch-result.js";

// The documented Cursor example, run against a stand-in `cursor-agent` (no Cursor, no model): its record is one OpenRig reads.
const EXAMPLE = resolve(__dirname, "../../../docs/reference/community-harness-examples/cursor");
const dirs: string[] = [];
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });

function run(env: Record<string, string>) {
  const dir = mkdtempSync(join(tmpdir(), "cursor-example-")); dirs.push(dir);
  const fake = join(dir, "cursor-agent");
  // create-chat prints an id and keeps running, as Cursor's does; any other call records its arguments and exits.
  writeFileSync(fake, `#!/bin/sh\nif [ "$1" = create-chat ]; then echo chat-abc; exec sleep 30; fi\necho "$@ CFG=$CURSOR_CONFIG_DIR" > "${dir}/started"\n`);
  chmodSync(fake, 0o755);
  const root = join(dir, "results");
  const attempt = newLaunchAttempt("dev-cursor@r", root);
  execFileSync(join(EXAMPLE, "openrig-cursor-launch"), [], { env: { PATH: process.env["PATH"]!, OPENRIG_CURSOR_AGENT: fake, OPENRIG_HOME: join(dir, "home"),
    OPENRIG_NODE_ID: "node-1", OPENRIG_LAUNCH_RESULT: attempt.file, OPENRIG_LAUNCH_ATTEMPT: attempt.attempt, ...env }, timeout: 20_000 });
  return { read: readLaunchResult(attempt, "cursor", root), started: readFileSync(join(dir, "started"), "utf8"), dir };
}

describe("the documented Cursor launcher and preset", () => {
  it("reports the chat create-chat made, then reopens it in the seat's own Cursor config folder", () => {
    const { read, started, dir } = run({});
    expect(read).toEqual({ ok: true, nativeRef: "chat-abc", producer: "openrig-cursor-launch 1" });
    expect(started.trim()).toBe(`--resume chat-abc --trust CFG=${join(dir, "home", "state", "cursor", "node-1")}`);
  });

  it("reopens the chat it's asked to resume, and reports that id", () => {
    const { read, started } = run({ OPENRIG_RESUME_REF: "chat-777" });
    expect(read).toMatchObject({ ok: true, nativeRef: "chat-777" });
    expect(started).toMatch(/^--resume chat-777 --trust /);
  });

  it("is a preset this OpenRig loads, declaring launch_result and not resume", () => {
    expect(loadCommunityHarnessPreset("cursor", EXAMPLE)).toMatchObject({ ok: true, preset: { command: "openrig-cursor-launch", launchResult: true, resume: false } });
  });
});
