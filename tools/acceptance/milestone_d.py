#!/usr/bin/env python3
"""End-to-end acceptance run for Phase 4 Milestone D (reusable content).

Drives the four scene commands through the real godot-cli binary against one
fixture project: creates a player scene, refuses to create it twice, adds a
body and saves it, creates a level, instances the player in the level, lists
and switches the open scene tabs, keeps an unrelated dirty tab dirty, copies
the edited scene with save-scene-as (refusals, --overwrite, --dry-run),
saves the dirty background tab by name, restarts the editor and reads the
level back.

Every step asserts from replies, from file bytes and from the live editor
(list-open-scenes, scene-tree, eval, the edited scene path), never from a
command's acknowledgement alone. The script prints one PASS line per step and
exits non-zero on the first failed assertion.

Ordering: `other.tscn` is opened and dirtied before step 1 and stays dirty,
untouched and never saved until step 8. Steps 6 and 7 need the unrelated scene
edited (step 6) and then the player edited (step 7), so step 7 starts by
opening the player again: that makes the player the copy source and leaves
`other.tscn` as the dirty background tab that step 8 saves by name. Step 8 is
the first and only write of `other.tscn`. Step 8 also opens `sentinel.tscn` and
dirties it, so the named save has to leave a second, uninvolved dirty tab alone.

The transcript holds the raw reply of every tab check and scene-tree read, the
raw text of every refusal and a sha256 for every file an assertion compares.
The two scenes the script writes itself are hashed raw, so any byte change
fails a preservation check. Files the editor writes are hashed with the
engine-assigned uid and unique_id values masked, because those values change
between runs and two runs must compare byte-equal.

  python3 milestone_d.py --godot <editor> --cli <godot-cli> --port N \
      --workdir <new dir> --out <transcript.json>

Controls, each invoked as a separate run and each expected to report FAILED:
  --negative NAME            run one deliberately broken variant (see CONTROLS)
  --self-test                check the pure helpers and the contract table
  --normalise <transcript>   print the transcript with the workdir, the port
                             and the elapsed time removed, for the two-run
                             byte-equality check

--workdir must not exist; the script creates it and never deletes anything.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

# Every command name, flag and reply field the run uses, in one place: a later
# adjustment to the shared contract is a one-line change here.
CONTRACT = {
    "create_scene": {
        "cli": "create-scene",
        "write_class": "W1",
        "flags": {"root_type": "--root-type", "root_name": "--root-name",
                  "dry_run": "--dry-run"},
        "reply": ["written", "replaced", "dry_run", "undoable", "undo_scope",
                  "path", "root_type", "root_name", "opened"],
    },
    "save_scene_as": {
        "cli": "save-scene-as",
        "write_class": "W2 only with --overwrite",
        "flags": {"overwrite": "--overwrite", "dry_run": "--dry-run"},
        "reply": ["written", "replaced", "dry_run", "undoable", "undo_scope",
                  "path", "source", "overwrote_open"],
    },
    "list_open_scenes": {
        "cli": "list-open-scenes",
        "write_class": "none",
        "flags": {},
        "reply": ["scenes", "edited"],
    },
    "save_open_scene": {
        "cli": "save-open-scene",
        "write_class": "WS",
        "flags": {},
        "reply": ["path", "saved", "edited", "undoable", "undo_scope"],
    },
}

# A disk write has no undo in the editor: every writing command states it in
# the same words.
UNDO_SCOPE = "none: the disk write has no undo in the editor"

PROJECT_GODOT = """; Engine configuration file.
config_version=5

[application]

config/name="godot-cli-acceptance-d"

[editor_plugins]

enabled=PackedStringArray("res://addons/godot_pipeline/plugin.cfg")
"""

# The unrelated scene: a Node2D root, no edit yet. It is opened before the
# first step and dirtied there, and is not written until step 8.
OTHER_TSCN = """[gd_scene format=3]

[node name="Other" type="Node2D"]
"""

# The scenes the script writes itself (OTHER_TSCN, SENTINEL_TSCN) are hashed
# raw: their bytes are fixed here, so a rewrite that changes only an
# engine-assigned uid or unique_id still fails a preservation check. Files the
# editor writes (the player, the level, the copy) are hashed with those ids
# masked, because they differ per run. other.tscn joins the masked group once
# the editor saves it in step 8.
PLAYER = "res://player.tscn"
LEVEL = "res://level.tscn"
OTHER = "res://other.tscn"
SENTINEL = "res://sentinel.tscn"
COPY = "res://player_copy.tscn"
DRY = "res://dry.tscn"
DRY_NEW = "res://dry_new.tscn"
PLAYER_ROOT = "player"
LEVEL_ROOT = "level"
OTHER_ROTATION = "1.5"
SENTINEL_ROTATION = "2.5"

# The copy is seeded with this before --overwrite: an overwrite that skips the
# write would leave it in place and change nothing.
STALE_TSCN = """[gd_scene format=3]

[node name="Stale" type="Node2D"]
"""

# A second uninvolved scene, dirtied in memory and never saved.
SENTINEL_TSCN = """[gd_scene format=3]

[node name="Sentinel" type="Node2D"]
"""

# The edit the unrelated tab carries: the root's rotation. var_to_str keeps the
# read-back a string, so no value decoding is involved in the comparison.
READ_ROTATION = """extends RefCounted
func run(editor, undo_redo) -> Variant:
	return var_to_str(editor.get_edited_scene_root().rotation)
"""

# Deliberately broken variants. Each one is a single injected mutation; a run
# with it must report FAILED, which shows the assertion it defeats is not
# vacuous.
CONTROLS = {
    "missing_instance": "step 4 skips instantiate-scene and its file check, so "
                        "the restarted level cannot show the instance (step 9)",
    "removed_player": "player.tscn is deleted after step 8, so the restarted "
                      "editor cannot show the level and the restart check fails (step 9)",
    "unrelated_written": "other.tscn is written behind the editor's back after "
                         "step 7, so the unchanged-until-step-8 check fails (step 7)",
    "overwrite_unchanged": "step 7 expects --overwrite to leave the stale copy's "
                           "bytes, so a correct overwrite fails the assertion (step 7)",
    "sentinel_written": "sentinel.tscn is written behind the editor's back after "
                        "the background save, so its unchanged-file check fails (step 8)",
    "weakened_refusal": "step 2 expects the refusal to leave out the path, so a "
                        "correct refusal fails the assertion (step 2)",
    "tab_order": "step 5 expects the tab list in reverse order, so a correct "
                 "tab list fails the assertion (step 5)",
    "copy_moved_tab": "step 7 expects the edited tab to move to the copy, which "
                      "is the engine rename the contract forbids (step 7)",
    "port_not_patched": "the fixture keeps the live-editor port, so the fixture "
                        "guard fails before anything runs",
}


class Failed(Exception):
    """A step assertion that did not hold."""


# The step being run, so a failure names where it happened.
CURRENT = ["startup"]


def die(message):
    raise Failed("%s: %s" % (CURRENT[0], message))


def normalise(doc):
    """The transcript without the values that differ between two runs.

    The workdir, the port and the elapsed time are the only permitted
    differences: every step detail is a fixed value, so two runs on fresh
    workdirs and ports compare byte-equal once these fields are removed.
    """
    out = dict(doc)
    for key in ("workdir", "port", "elapsed_sec"):
        out.pop(key, None)
    return out


def root_node_name(text):
    """The root node's name from a .tscn text, or None."""
    match = re.search(r'^\[node name="([^"]+)" type=', text, re.MULTILINE)
    return match.group(1) if match else None


def has_node_line(text, name, node_type, parent=None):
    """Whether a .tscn text stores a node line with this name and type.

    The editor's saved form carries extra attributes on the same line (a
    unique_id, an instance reference), so the line is matched up to the end of
    the bracket rather than compared whole.
    """
    pattern = r'^\[node name="%s" type="%s"' % (re.escape(name), re.escape(node_type))
    if parent is not None:
        pattern += r' parent="%s"' % re.escape(parent)
    pattern += r'( [^\]]*)?\]$'
    return re.search(pattern, text, re.MULTILINE) is not None


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def without_uid(text):
    """The .tscn text without the uid the engine assigns to a resource path."""
    return re.sub(r' uid="[^"]*"', "", text)


def comparable(text):
    """The .tscn text without the engine-assigned ids: the resource uid and the
    node unique_id values, which the editor writes afresh but which say nothing
    about the scene content the assertions compare."""
    return re.sub(r' (?:uid="[^"]*"|unique_id=\d+)', "", text)


def decode(value):
    """eval tags every scalar with its type letter; the two reads here return
    strings, which arrive as "s:<text>"."""
    if isinstance(value, str) and value.startswith("s:"):
        return value[len("s:"):]
    return value


class Run:
    def __init__(self, godot, cli, port, repo, workdir):
        self.godot = godot
        self.cli_bin = cli
        self.port = port
        self.repo = repo
        self.proj = os.path.join(workdir, "proj")
        self.proc = None
        self.transcript = []

    # ---- the editor, launched the way tools/replay/replay.py launches it ----
    def launch(self, scene):
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
            die("port %d is already in use" % self.port)
        except OSError:
            pass
        args = [self.godot, "--headless", "--path", self.proj, "--editor", scene]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.wait_listener()
        end = time.time() + 90
        while time.time() < end:
            if self.status().get("scene_path") is not None:
                break
            time.sleep(1)
        else:
            die("the edited scene never opened")
        self.wait_filesystem_ready()

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)
        self.proc = None

    def wait_listener(self, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
                return
            except OSError:
                time.sleep(0.5)
        die("the plugin listener on port %d never came up" % self.port)

    def send(self, payload, timeout=30):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            s.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            s.settimeout(timeout)
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
            return data.decode().rstrip("\n")
        finally:
            s.close()

    def wait_filesystem_ready(self, timeout=120):
        end = time.time() + timeout
        while True:
            reply = json.loads(self.send({"command": "list_resources",
                                          "project_path": self.proj, "limit": 1}, 25))
            if reply.get("status") == "ok" and reply["data"].get("scanning") is False:
                return
            if time.time() >= end:
                die("the editor file system never finished its scan")
            time.sleep(0.5)

    # ---- everything the scenario does goes through the real CLI binary ----
    def cli(self, args, timeout=120):
        # --port always comes first: without it the CLI tries its default port,
        # which is the port of a developer's live editor.
        proc = subprocess.run([self.cli_bin, "--port", str(self.port)] + args,
                              capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "").strip()
        try:
            parsed = json.loads(out) if out else None
        except json.JSONDecodeError:
            parsed = None
        return {"argv": args, "exit": proc.returncode, "stdout": out,
                "stderr": (proc.stderr or "").strip(), "json": parsed}

    def command(self, name, *positional, **flags):
        """A call of one contract command, with every name taken from CONTRACT."""
        spec = CONTRACT[name]
        args = ["call", spec["cli"]] + [str(value) for value in positional]
        for key, value in flags.items():
            flag = spec["flags"][key]
            if value is True:
                args.append(flag)
            elif value is not False and value is not None:
                args += [flag, str(value)]
        args += ["--project-path", self.proj]
        return self.cli(args)

    def status(self):
        return self.data_of(self.cli(["call", "status", "--project-path", self.proj]),
                            "status")

    def eval(self, code):
        return self.cli(["call", "eval", code, "--project-path", self.proj])

    def data_of(self, reply, what):
        """The reply data of a command that must succeed."""
        if reply["exit"] != 0:
            die("the %s call was refused: %s"
                % (what, reply["stderr"] or reply["stdout"][:300]))
        if not isinstance(reply["json"], dict):
            die("the %s call returned no JSON object: %s"
                % (what, reply["stdout"][:300]))
        return reply["json"]

    def checked(self, name, *positional, **flags):
        """A contract command that must succeed and carry every stated field."""
        reply = self.command(name, *positional, **flags)
        data = self.data_of(reply, CONTRACT[name]["cli"])
        for key in CONTRACT[name]["reply"]:
            if key not in data:
                die("the %s reply carries no %r: %s"
                    % (CONTRACT[name]["cli"], key, json.dumps(data)[:300]))
        return data

    def refused(self, reply, what, expected):
        """A command that must fail, with text the contract promises, and no
        change: the callers compare the files themselves."""
        if reply["exit"] == 0:
            die("%s was accepted: %s" % (what, reply["stdout"][:300]))
        message = reply["stderr"] or reply["stdout"]
        self.note(CURRENT[0], "refusal", {"what": what, "expected": expected,
                                         "message": message})
        for text in expected:
            if text not in message:
                die("%s was refused without %r: %s" % (what, text, message[:300]))
        return message

    def value_of(self, reply, what):
        """The value of an eval read; a failed read is never a value."""
        if reply["exit"] != 0:
            die("the %s read was refused: %s"
                % (what, reply["stderr"] or reply["stdout"][:300]))
        data = reply["json"]
        if not isinstance(data, dict) or "value" not in data:
            die("the %s read carried no value: %s"
                % (what, json.dumps(data)[:300] if data is not None else "no JSON"))
        return decode(data["value"])

    # ---- files and editor state ----
    def path_of(self, res_path):
        return os.path.join(self.proj, res_path[len("res://"):])

    def text_of(self, res_path):
        with open(self.path_of(res_path), encoding="utf-8") as handle:
            text = handle.read()
        self.note(CURRENT[0], "sha256",
                  {"path": res_path, "raw": False,
                   "sha256": sha256_of(comparable(text).encode())})
        return text

    def digest_of(self, res_path, raw=False):
        with open(self.path_of(res_path), encoding="utf-8") as handle:
            text = handle.read()
        digest = sha256_of(text.encode() if raw else comparable(text).encode())
        self.note(CURRENT[0], "sha256", {"path": res_path, "sha256": digest,
                                         "raw": raw})
        return digest

    def tabs(self):
        """The open tabs in order, with the edited and unsaved flags, plus the
        edited scene path."""
        data = self.checked("list_open_scenes")
        self.note(CURRENT[0], "tabs", data)
        scenes = data["scenes"]
        if not isinstance(scenes, list):
            die("the list-open-scenes reply holds %r, not a list" % (scenes,))
        rows = []
        for entry in scenes:
            if not isinstance(entry, dict):
                die("a list-open-scenes entry is %r, not an object" % (entry,))
            for key in ("path", "edited", "unsaved"):
                if key not in entry:
                    die("a list-open-scenes entry carries no %r: %s" % (key, entry))
            rows.append((entry["path"], bool(entry["edited"]),
                         bool(entry["unsaved"])))
        return rows, data["edited"]

    def expect(self, condition, message):
        if not condition:
            die(message)

    def note(self, step, kind, detail):
        self.transcript.append({"step": step, "kind": kind, "detail": detail})


def build_fixture(run, negative):
    proj = run.proj
    if os.path.isdir(proj):
        die("the project directory already exists: %s" % proj)
    os.makedirs(os.path.join(proj, "addons"))
    with open(os.path.join(proj, "project.godot"), "w") as handle:
        handle.write(PROJECT_GODOT)
    shutil.copytree(os.path.join(run.repo, "addon", "godot_pipeline"),
                    os.path.join(proj, "addons", "godot_pipeline"),
                    ignore=shutil.ignore_patterns("*.uid"))
    plugin = os.path.join(proj, "addons", "godot_pipeline", "editor_plugin.gd")
    with open(plugin) as handle:
        text = handle.read()
    text = text.replace("const PORT := 47821", "const PORT := %d" % run.port)
    text = text.replace("const PORT = 47821", "const PORT = %d" % run.port)
    if negative == "port_not_patched":
        text = text.replace("const PORT := %d" % run.port, "const PORT := 47821")
    with open(plugin, "w") as handle:
        handle.write(text)
    if "47821" in text:
        die("the fixture plugin still names the live-editor port")
    with open(os.path.join(proj, "other.tscn"), "w") as handle:
        handle.write(OTHER_TSCN)
    with open(os.path.join(proj, "sentinel.tscn"), "w") as handle:
        handle.write(SENTINEL_TSCN)
    # One import pass gives the scene resource ids, so the editor's first
    # launch does not race its own startup scan.
    subprocess.run([run.godot, "--headless", "--path", proj, "--import"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)


def run_scenario(run, negative):
    # ---- S0 fixture: the unrelated scene opened and dirtied, nothing else ----
    CURRENT[0] = "S0"
    root = run.data_of(run.cli(["call", "set-property", ".", "rotation",
                                OTHER_ROTATION, "--project-path", run.proj]),
                       "set-property")
    del root
    rows, edited = run.tabs()
    run.expect(rows == [(OTHER, True, True)],
               "the baseline tabs are %r, expected the single dirty other.tscn" % (rows,))
    run.expect(edited == OTHER, "the baseline edited scene is %r" % (edited,))
    other_before = run.digest_of(OTHER, raw=True)
    run.note("S0", "baseline", {"tabs": [OTHER], "edited": OTHER, "unsaved": [OTHER]})

    # ---- S1 create a player scene file: no tab, no state change ----
    CURRENT[0] = "S1"
    # The same call as a dry run first: W1 validates and writes nothing.
    dry_new = run.checked("create_scene", DRY_NEW, root_type="Node2D", dry_run=True)
    run.expect(dry_new["written"] == [],
               "create-scene --dry-run wrote %r" % (dry_new["written"],))
    run.expect(dry_new["dry_run"] is True,
               "create-scene --dry-run dry_run is %r" % (dry_new["dry_run"],))
    run.expect(not os.path.exists(run.path_of(DRY_NEW)),
               "create-scene --dry-run created %s" % DRY_NEW)
    created = run.checked("create_scene", PLAYER, root_type="Node2D")
    run.expect(created["written"] == [PLAYER],
               "create-scene wrote %r" % (created["written"],))
    for key, want in (("replaced", False), ("dry_run", False), ("undoable", False),
                      ("opened", False), ("path", PLAYER), ("root_type", "Node2D"),
                      ("root_name", PLAYER_ROOT)):
        run.expect(created[key] == want,
                   "create-scene %s is %r, expected %r" % (key, created[key], want))
    run.expect(created["undo_scope"] == UNDO_SCOPE,
               "create-scene undo_scope is %r" % (created["undo_scope"],))
    text = run.text_of(PLAYER)
    run.expect(root_node_name(text) == PLAYER_ROOT,
               "the player root is named %r, expected the file stem %r"
               % (root_node_name(text), PLAYER_ROOT))
    run.expect(text.splitlines()[0].startswith("[gd_scene format=3"),
               "the player header is %r" % (text.splitlines()[0],))
    run.expect(has_node_line(text, PLAYER_ROOT, "Node2D"),
               "the player file has no %r Node2D root node line: %s"
               % (PLAYER_ROOT, text[:400]))
    rows_after, edited_after = run.tabs()
    run.expect(rows_after == rows,
               "creating the player changed the tabs to %r" % (rows_after,))
    run.expect(edited_after == OTHER,
               "creating the player changed the edited scene to %r" % (edited_after,))
    run.expect(rows_after[0][2] is True,
               "the unrelated tab is no longer unsaved after create-scene")
    # The command must leave the file visible to the editor's file system: a
    # later instancing or listing call reads it from there.
    seen = False
    end = time.time() + 15
    while time.time() < end:
        data = run.data_of(run.cli(["call", "list-resources", "--path-prefix", PLAYER,
                                    "--limit", "5", "--project-path", run.proj]),
                           "list-resources")
        if any(entry.get("path") == PLAYER for entry in data.get("resources", [])):
            seen = True
            break
        time.sleep(0.5)
    run.expect(seen, "the editor file system does not list %s after create-scene" % PLAYER)
    run.note("S1", "create_scene", {"path": PLAYER, "root_type": "Node2D",
                                    "root_name": PLAYER_ROOT, "opened": False,
                                    "tabs_unchanged": True})

    # ---- S2 creating the same file again is refused, the bytes stay ----
    CURRENT[0] = "S2"
    player_digest = run.digest_of(PLAYER)
    again = run.command("create_scene", PLAYER, root_type="Node2D")
    if negative == "weakened_refusal":
        # The deliberately weak assertion: the refusal does not name the path.
        message = run.refused(again, "create-scene on an existing path", [])
        run.expect(PLAYER not in message,
                   "the refusal names the path: %s" % message[:300])
    else:
        run.refused(again, "create-scene on an existing path", [PLAYER, "exist"])
    run.expect(run.digest_of(PLAYER) == player_digest,
               "the refused create-scene changed the player bytes")
    rows_now, _ = run.tabs()
    run.expect(rows_now == rows, "the refused create-scene changed the tabs to %r"
               % (rows_now,))
    run.note("S2", "refusal", {"path": PLAYER, "mentions_path": True,
                               "mentions_exists": True, "bytes_unchanged": True})

    # ---- S3 open the player, add the body, save it ----
    CURRENT[0] = "S3"
    opened = run.data_of(run.cli(["call", "open-scene", "--scene-path", PLAYER,
                                  "--project-path", run.proj]), "open-scene")
    run.expect(opened.get("path") == PLAYER,
               "open-scene reports %r" % (opened.get("path"),))
    run.data_of(run.cli(["call", "create-node", ".", "Sprite2D", "Body",
                         "--project-path", run.proj]), "create-node")
    saved = run.data_of(run.cli(["call", "save-scene",
                                 "--project-path", run.proj]), "save-scene")
    run.expect(saved.get("path") == PLAYER, "save-scene reports %r"
               % (saved.get("path"),))
    text = run.text_of(PLAYER)
    run.expect(has_node_line(text, "Body", "Sprite2D", "."),
               "the saved player has no Body Sprite2D: %s" % text[:400])
    rows_now, edited_now = run.tabs()
    run.expect(edited_now == PLAYER, "the edited scene is %r" % (edited_now,))
    run.expect([row[2] for row in rows_now if row[0] == PLAYER] == [False],
               "the player is still unsaved after save-scene: %r" % (rows_now,))
    player_saved = run.digest_of(PLAYER)
    run.note("S3", "player_saved", {"path": PLAYER, "body": True, "unsaved": False})

    # ---- S4 a level scene, then the player instanced in it and saved ----
    CURRENT[0] = "S4"
    run.checked("create_scene", LEVEL, root_type="Node2D")
    run.data_of(run.cli(["call", "open-scene", "--scene-path", LEVEL,
                         "--project-path", run.proj]), "open-scene")
    if negative != "missing_instance":
        run.data_of(run.cli(["call", "instantiate-scene",
                             "--scene-path", PLAYER, "--parent-path", ".",
                             "--project-path", run.proj]), "instantiate-scene")
    run.data_of(run.cli(["call", "save-scene",
                         "--project-path", run.proj]), "save-scene")
    text = run.text_of(LEVEL)
    if negative != "missing_instance":
        run.expect('[ext_resource type="PackedScene"' in text,
                   "the level has no PackedScene ext_resource: %s" % text[:400])
        run.expect('path="%s"' % PLAYER in text,
                   "the level does not reference %s: %s" % (PLAYER, text[:400]))
        run.expect("instance=ExtResource(" in text,
                   "the level stores no instance: %s" % text[:400])
    run.note("S4", "level_saved", {"path": LEVEL, "instance": PLAYER})

    # ---- S5 the open tabs in order, with the edited and unsaved flags ----
    CURRENT[0] = "S5"
    rows_now, edited_now = run.tabs()
    expected = [(OTHER, False, True), (PLAYER, False, False), (LEVEL, True, False)]
    if negative == "tab_order":
        expected = list(reversed(expected))
    run.expect(rows_now == expected,
               "the tabs are %r, expected %r" % (rows_now, expected))
    run.expect(edited_now == LEVEL, "the edited scene is %r, expected %s"
               % (edited_now, LEVEL))
    run.note("S5", "tabs", {"order": [OTHER, PLAYER, LEVEL], "edited": LEVEL,
                            "unsaved": [OTHER]})

    # ---- S6 switch to the unrelated tab: its unsaved edit survives ----
    CURRENT[0] = "S6"
    run.data_of(run.cli(["call", "open-scene", "--scene-path", OTHER,
                         "--project-path", run.proj]), "open-scene")
    rows_now, edited_now = run.tabs()
    run.expect(edited_now == OTHER, "the edited scene is %r after the switch"
               % (edited_now,))
    run.expect([row[2] for row in rows_now if row[0] == OTHER] == [True],
               "the unrelated scene is no longer unsaved after the switch: %r"
               % (rows_now,))
    rotation = run.value_of(run.eval(READ_ROTATION), "rotation")
    run.expect(rotation == OTHER_ROTATION,
               "the unrelated scene's edit is %r, expected %s"
               % (rotation, OTHER_ROTATION))
    run.expect(run.digest_of(OTHER, raw=True) == other_before,
               "the unrelated scene's file changed while it was only switched to")
    run.note("S6", "tab_switch", {"edited": OTHER, "edit": OTHER_ROTATION,
                                  "unsaved": True, "bytes_unchanged": True})

    # ---- S7 save a copy of the edited scene; every refusal and flag ----
    CURRENT[0] = "S7"
    # The copy source is the player, so open it again: the unrelated scene
    # stays open as the dirty background tab that step 8 saves by name.
    run.data_of(run.cli(["call", "open-scene", "--scene-path", PLAYER,
                         "--project-path", run.proj]), "open-scene")
    run.expect(run.tabs()[1] == PLAYER, "the edited scene is %r before the copy"
               % (run.tabs()[1],))
    copied = run.checked("save_scene_as", COPY)
    run.expect(copied["written"] == [COPY], "save-scene-as wrote %r"
               % (copied["written"],))
    for key, want in (("replaced", False), ("dry_run", False), ("undoable", False),
                      ("path", COPY), ("source", PLAYER), ("overwrote_open", False)):
        run.expect(copied[key] == want,
                   "save-scene-as %s is %r, expected %r" % (key, copied[key], want))
    run.expect(copied["undo_scope"] == UNDO_SCOPE,
               "save-scene-as undo_scope is %r" % (copied["undo_scope"],))
    text = run.text_of(COPY)
    run.expect(root_node_name(text) == PLAYER_ROOT,
               "the copy root is named %r" % (root_node_name(text),))
    run.expect(has_node_line(text, "Body", "Sprite2D", "."),
               "the copy has no Body Sprite2D: %s" % text[:400])
    run.expect(run.digest_of(PLAYER) == player_saved,
               "the copy changed the player's own file")
    rows_now, edited_now = run.tabs()
    expected_edited = PLAYER
    if negative == "copy_moved_tab":
        expected_edited = COPY
    run.expect(edited_now == expected_edited,
               "the edited tab is %r, expected %r" % (edited_now, expected_edited))
    run.expect([row[2] for row in rows_now if row[0] == PLAYER] == [False],
               "copying made the player unsaved: %r" % (rows_now,))
    copy_digest = run.digest_of(COPY)

    # An existing target needs its named flag.
    run.refused(run.command("save_scene_as", COPY),
                "save-scene-as onto an existing path", [COPY, "exist"])
    run.expect(run.digest_of(COPY) == copy_digest,
               "the refused save-scene-as changed the existing file")
    # A target that already holds the source bytes cannot show whether the
    # overwrite wrote anything: seed it with an unrelated scene instead.
    with open(run.path_of(COPY), "w") as handle:
        handle.write(STALE_TSCN)
    run.expect(root_node_name(run.text_of(COPY)) == "Stale",
               "the seeded copy is not the stale scene")
    stale_digest = run.digest_of(COPY)
    # The flag on a path that is not open replaces the file.
    overwritten = run.checked("save_scene_as", COPY, overwrite=True)
    run.expect(overwritten["replaced"] is True,
               "save-scene-as --overwrite replaced is %r" % (overwritten["replaced"],))
    run.expect(overwritten["written"] == [COPY],
               "save-scene-as --overwrite wrote %r" % (overwritten["written"],))
    text = run.text_of(COPY)
    run.expect(root_node_name(text) == PLAYER_ROOT,
               "the overwritten copy root is %r" % (root_node_name(text),))
    run.expect(has_node_line(text, "Body", "Sprite2D", "."),
               "the overwritten copy lost its Body Sprite2D")
    run.expect(without_uid(text) == without_uid(run.text_of(PLAYER)),
               "the overwritten copy does not hold the saved player scene: %s"
               % text[:400])
    if negative == "overwrite_unchanged":
        # The deliberately wrong expectation: the overwrite leaves the bytes.
        run.expect(run.digest_of(COPY) == stale_digest,
                   "save-scene-as --overwrite replaced the stale bytes of %s" % COPY)
    run.expect(run.digest_of(COPY) != stale_digest,
               "save-scene-as --overwrite left the stale copy's bytes in place")
    # The flag on the path of an open tab is refused.
    run.refused(run.command("save_scene_as", OTHER, overwrite=True),
                "save-scene-as --overwrite onto an open scene", [OTHER])
    run.expect(run.digest_of(OTHER, raw=True) == other_before,
               "the refused overwrite changed the open unrelated scene's file")
    # The edited scene's own path is refused too, with or without the flag.
    player_digest = run.digest_of(PLAYER)
    run.refused(run.command("save_scene_as", PLAYER, overwrite=True),
                "save-scene-as onto the edited scene's own path", ["own path", "save-scene"])
    run.expect(run.digest_of(PLAYER) == player_digest,
               "the refused own-path copy changed the player's file")
    # --dry-run validates and writes nothing.
    dry = run.checked("save_scene_as", DRY, dry_run=True)
    run.expect(dry["written"] == [], "save-scene-as --dry-run wrote %r"
               % (dry["written"],))
    run.expect(dry["dry_run"] is True, "save-scene-as --dry-run dry_run is %r"
               % (dry["dry_run"],))
    run.expect(not os.path.exists(run.path_of(DRY)),
               "save-scene-as --dry-run created %s" % DRY)
    if negative == "unrelated_written":
        # A command that saves the background tab too early would do this: the
        # bytes differ from the opened file, so only an early write explains it.
        with open(run.path_of(OTHER), "w") as handle:
            handle.write(OTHER_TSCN.replace(
                '[node name="Other" type="Node2D"]',
                '[node name="Other" type="Node2D"]\nrotation = %s'
                % OTHER_ROTATION))
    run.expect(run.digest_of(OTHER, raw=True) == other_before,
               "the unrelated scene's file changed before its own save")
    run.note("S7", "copy", {"copy": COPY, "source": PLAYER, "edited_kept": PLAYER,
                            "refused_existing": True, "overwrite_replaced_stale": True,
                            "overwrite_open_refused": True, "dry_run_empty": True})

    # ---- S8 save the dirty background tab by name ----
    CURRENT[0] = "S8"
    # A second tab, dirty only in memory, so a background save that saves or
    # discards another tab's work is caught.
    run.data_of(run.cli(["call", "open-scene", "--scene-path", SENTINEL,
                         "--project-path", run.proj]), "open-scene")
    run.data_of(run.cli(["call", "set-property", ".", "rotation",
                         SENTINEL_ROTATION, "--project-path", run.proj]),
                "set-property")
    run.data_of(run.cli(["call", "open-scene", "--scene-path", PLAYER,
                         "--project-path", run.proj]), "open-scene")
    sentinel_before = run.digest_of(SENTINEL, raw=True)
    rows_now, _ = run.tabs()
    run.expect([row[2] for row in rows_now if row[0] == SENTINEL] == [True],
               "the sentinel scene is not unsaved before the background save: %r"
               % (rows_now,))
    saved_tab = run.checked("save_open_scene", OTHER)
    for key, want in (("path", OTHER), ("saved", True), ("edited", PLAYER),
                      ("undoable", False)):
        run.expect(saved_tab[key] == want,
                   "save-open-scene %s is %r, expected %r"
                   % (key, saved_tab[key], want))
    run.expect(saved_tab["undo_scope"] == UNDO_SCOPE,
               "save-open-scene undo_scope is %r" % (saved_tab["undo_scope"],))
    text = run.text_of(OTHER)
    run.expect("rotation = %s" % OTHER_ROTATION in text,
               "the saved unrelated scene has no rotation %s: %s"
               % (OTHER_ROTATION, text[:400]))
    run.expect(sha256_of(text.encode()) != other_before,
               "saving the background tab wrote nothing")
    run.expect(run.digest_of(PLAYER) == player_digest,
               "saving the background tab changed the edited scene's file")
    rows_now, edited_now = run.tabs()
    run.expect(edited_now == PLAYER, "the edited scene is %r after the background save"
               % (edited_now,))
    run.expect([row[2] for row in rows_now if row[0] == OTHER] == [False],
               "the background tab is still unsaved: %r" % (rows_now,))
    other_saved = run.digest_of(OTHER)
    # A clean tab is not written again.
    clean = run.checked("save_open_scene", OTHER)
    run.expect(clean["saved"] is False, "save-open-scene on a clean tab saved %r"
               % (clean["saved"],))
    run.expect(run.digest_of(OTHER) == other_saved,
               "save-open-scene on a clean tab rewrote the file")
    if negative == "sentinel_written":
        # A command that saved more than the named tab would do this.
        with open(run.path_of(SENTINEL), "w") as handle:
            handle.write(SENTINEL_TSCN.replace(
                '[node name="Sentinel" type="Node2D"]',
                '[node name="Sentinel" type="Node2D"]\nrotation = %s'
                % SENTINEL_ROTATION))
    run.expect(run.digest_of(SENTINEL, raw=True) == sentinel_before,
               "the background save wrote the sentinel scene's file")
    rows_now, _ = run.tabs()
    run.expect([row[2] for row in rows_now if row[0] == SENTINEL] == [True],
               "the sentinel tab is no longer unsaved after the background save: %r"
               % (rows_now,))
    run.data_of(run.cli(["call", "open-scene", "--scene-path", SENTINEL,
                         "--project-path", run.proj]), "open-scene")
    edit = run.value_of(run.eval(READ_ROTATION), "sentinel rotation")
    run.expect(edit == SENTINEL_ROTATION,
               "the sentinel scene's edit is %r, expected %s"
               % (edit, SENTINEL_ROTATION))
    run.expect(run.digest_of(SENTINEL, raw=True) == sentinel_before,
               "the sentinel scene's file changed when it was switched to")
    run.note("S8", "background_save", {"path": OTHER, "saved": True,
                                       "edited_kept": PLAYER,
                                       "clean_call_saved": False,
                                       "sentinel_kept": SENTINEL})

    # ---- S9 restart and read the level back ----
    CURRENT[0] = "S9"
    if negative == "removed_player":
        os.remove(run.path_of(PLAYER))
    run.stop()
    run.launch(LEVEL)
    got = run.status()
    run.expect(got.get("scene_path") == LEVEL, "the restarted editor shows %r, "
               "expected %s" % (got.get("scene_path"), LEVEL))
    tree = run.data_of(run.cli(["call", "scene-tree"]), "scene-tree")
    run.note("S9", "scene_tree", tree)
    run.expect(tree.get("name") == LEVEL_ROOT and tree.get("type") == "Node2D",
               "the level root is %r/%r" % (tree.get("name"), tree.get("type")))
    children = tree.get("children") or []
    player_root = root_node_name(run.text_of(PLAYER)) if os.path.exists(
        run.path_of(PLAYER)) else PLAYER_ROOT
    instance = [child for child in children if child.get("name") == player_root]
    run.expect(len(instance) == 1,
               "the level has no %r instance child: %r" % (player_root, children))
    run.expect(instance[0].get("type") == "Node2D",
               "the instance is %r" % (instance[0].get("type"),))
    bodies = [child for child in instance[0].get("children") or []
              if child.get("name") == "Body"]
    run.expect(len(bodies) == 1,
               "the instance has no Body child: %r" % (instance[0].get("children"),))
    run.expect(bodies[0].get("type") == "Sprite2D",
               "the Body child is %r" % (bodies[0].get("type"),))
    text = run.text_of(LEVEL)
    run.expect('path="%s"' % PLAYER in text,
               "the level file does not reference %s: %s" % (PLAYER, text[:400]))
    run.expect("instance=ExtResource(" in text,
               "the level file stores no instance: %s" % text[:400])
    run.expect("rotation = %s" % OTHER_ROTATION in run.text_of(OTHER),
               "the unrelated scene's saved edit is not on disk after the restart")
    run.note("S9", "restart_readback", {"scene": LEVEL, "instance": player_root,
                                        "body": "Body", "bytes": "instance"})


STEPS = ["S0 fixture and dirty unrelated tab", "S1 create a player scene",
         "S2 create is refused on an existing path", "S3 save the player with a body",
         "S4 instance the player in a level", "S5 list the open tabs",
         "S6 switch to the unrelated tab", "S7 copy the edited scene",
         "S8 save the dirty background tab", "S9 restart and read the level"]


def self_test():
    """The pure checks: the contract table, the transcript normalisation and
    the assertion helpers."""
    names = sorted(CONTRACT)
    if names != sorted(["create_scene", "save_scene_as", "list_open_scenes",
                        "save_open_scene"]):
        die("the contract table holds %r" % (names,))
    for name, spec in CONTRACT.items():
        if spec["cli"] != spec["cli"].lower():
            die("the %s cli name is not lowercase" % name)
        if "_" in spec["cli"]:
            die("the %s cli name is not kebab-case: %s" % (name, spec["cli"]))
        if not spec["reply"]:
            die("the %s reply field list is empty" % name)
        if len(set(spec["reply"])) != len(spec["reply"]):
            die("the %s reply field list repeats a field" % name)
        for flag in spec["flags"].values():
            if not flag.startswith("--"):
                die("the %s flag %r is not a long flag" % (name, flag))
    if CONTRACT["create_scene"]["flags"]["root_type"] != "--root-type":
        die("create-scene root type flag is not --root-type")
    if "overwrite" in CONTRACT["create_scene"]["flags"]:
        die("create-scene carries an overwrite flag; W1 has none")
    if CONTRACT["save_scene_as"]["flags"].get("overwrite") != "--overwrite":
        die("save-scene-as has no --overwrite")
    # The two runs' documents differ only in the workdir, the port and the
    # elapsed time: normalisation removes exactly those.
    first = {"exit": 0, "elapsed_sec": 1.0, "port": 47950, "workdir": "/a",
             "steps": STEPS, "transcript": [{"step": "S1", "kind": "create_scene",
                                             "detail": {"path": PLAYER}}]}
    second = {"exit": 0, "elapsed_sec": 9.5, "port": 47951, "workdir": "/b",
              "steps": STEPS, "transcript": [{"step": "S1", "kind": "create_scene",
                                              "detail": {"path": PLAYER}}]}
    if json.dumps(normalise(first), sort_keys=True) != \
            json.dumps(normalise(second), sort_keys=True):
        die("normalisation does not remove the workdir, the port and the time")
    third = json.loads(json.dumps(second))
    third["transcript"][0]["detail"]["path"] = LEVEL
    if json.dumps(normalise(first), sort_keys=True) == \
            json.dumps(normalise(third), sort_keys=True):
        die("normalisation hides a difference in a step")
    # The assertion helpers accept a good reply and reject a bad one.
    run = Run("godot", "cli", 1, "/repo", "/work")
    data = run.data_of({"exit": 0, "json": {"path": PLAYER}, "stdout": "",
                        "stderr": ""}, "sample")
    if data["path"] != PLAYER:
        die("data_of lost the reply data")
    for bad in ({"exit": 1, "json": None, "stdout": "boom", "stderr": ""},
                {"exit": 0, "json": None, "stdout": "not json", "stderr": ""}):
        try:
            run.data_of(bad, "sample")
        except Failed:
            pass
        else:
            die("data_of accepted a failed or empty reply")
    run.refused({"exit": 1, "json": None, "stdout": "",
                 "stderr": "the target already exists: %s" % PLAYER},
                "sample", [PLAYER, "exist"])
    for bad in ({"exit": 0, "json": {"written": []}, "stdout": "", "stderr": ""},
                {"exit": 1, "json": None, "stdout": "", "stderr": "nope"}):
        try:
            run.refused(bad, "sample", [PLAYER])
        except Failed:
            pass
        else:
            die("refused accepted an accepted call or a wrong text")
    if run.value_of({"exit": 0, "json": {"value": OTHER_ROTATION}, "stdout": "",
                     "stderr": ""}, "sample") != OTHER_ROTATION:
        die("value_of lost the value")
    try:
        run.value_of({"exit": 0, "json": {"errors": []}, "stdout": "", "stderr": ""},
                     "sample")
    except Failed:
        pass
    else:
        die("value_of accepted a read with no value")
    if root_node_name(OTHER_TSCN) != "Other":
        die("root_node_name misses the root name")
    if root_node_name("[gd_scene format=3]\n") is not None:
        die("root_node_name invents a name")
    # The editor's saved form carries a unique_id on the node line.
    saved = ('[gd_scene format=3 uid="uid://x"]\n\n'
             '[node name="player" type="Node2D" unique_id=349108945]\n\n'
             '[node name="Body" type="Sprite2D" parent="." unique_id=966230003]\n')
    if not has_node_line(saved, "player", "Node2D"):
        die("has_node_line misses the saved root line")
    if not has_node_line(saved, "Body", "Sprite2D", "."):
        die("has_node_line misses the saved child line")
    if has_node_line(saved, "Body", "Node2D", "."):
        die("has_node_line ignores the node type")
    if has_node_line(saved, "Body", "Sprite2D", "Root"):
        die("has_node_line ignores the parent")
    if without_uid(saved) != ('[gd_scene format=3]\n\n'
                              '[node name="player" type="Node2D" unique_id=349108945]\n\n'
                              '[node name="Body" type="Sprite2D" parent="." unique_id=966230003]\n'):
        die("without_uid did not remove exactly the resource uid")
    if comparable(saved) != ('[gd_scene format=3]\n\n'
                             '[node name="player" type="Node2D"]\n\n'
                             '[node name="Body" type="Sprite2D" parent="."]\n'):
        die("comparable did not remove exactly the engine-assigned ids")
    if decode("s:1.5") != OTHER_ROTATION or decode("1.5") != OTHER_ROTATION:
        die("decode does not strip the eval string tag")
    if sha256_of(b"") != hashlib.sha256(b"").hexdigest():
        die("sha256_of is not the sha256 of the bytes")
    # The preservation checks rest on this difference: a rewritten engine id
    # changes the raw digest and leaves the masked one alone.
    plain = '[gd_scene format=3]\n\n[node name="Sentinel" type="Node2D"]\n'
    ids = ('[gd_scene format=3 uid="uid://x"]\n\n'
           '[node name="Sentinel" type="Node2D" unique_id=7]\n')
    if sha256_of(plain.encode()) == sha256_of(ids.encode()):
        die("the raw digest hides a rewritten engine id")
    if sha256_of(comparable(plain).encode()) != sha256_of(comparable(ids).encode()):
        die("the masked digest does not hide the engine ids")
    # Every control is a distinct injected mutation.
    if len(CONTROLS) < 6:
        die("there are fewer than six negative controls")
    print("PASS self-test")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--godot")
    ap.add_argument("--cli")
    ap.add_argument("--port", type=int)
    ap.add_argument("--workdir")
    ap.add_argument("--out")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    ap.add_argument("--negative", default="",
                    help="run one deliberately broken variant: "
                         + ", ".join(sorted(CONTROLS)))
    ap.add_argument("--self-test", action="store_true",
                    help="check the pure helpers and the contract table")
    ap.add_argument("--normalise", help="print a transcript without the workdir, "
                                        "the port and the elapsed time")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.normalise:
        with open(args.normalise) as handle:
            print(json.dumps(normalise(json.load(handle)), sort_keys=True, indent=1))
        return 0
    if not (args.godot and args.cli and args.port and args.workdir and args.out):
        ap.error("--godot, --cli, --port, --workdir and --out are required")
    if args.negative and args.negative not in CONTROLS:
        ap.error("--negative takes one of: " + ", ".join(sorted(CONTROLS)))
    # The plugin compares the project path it holds against the one a command
    # carries, so a relative workdir would never match and every call would be
    # refused.
    args.workdir = os.path.abspath(args.workdir)
    if os.path.exists(args.workdir):
        die("--workdir already exists, refusing to reuse it: %s" % args.workdir)
    os.makedirs(args.workdir)

    run = Run(args.godot, args.cli, args.port, args.repo, args.workdir)
    started = time.time()
    status = 1
    try:
        build_fixture(run, args.negative)
        run.launch(OTHER)
        run_scenario(run, args.negative)
        status = 0
    except Failed as exc:
        print("FAIL %s" % exc)
        run.transcript.append({"step": "FAIL", "kind": "assertion",
                               "detail": str(exc)})
    except Exception as exc:  # a broken step must still leave a transcript
        print("FAIL %s: %s" % (type(exc).__name__, exc))
        run.transcript.append({"step": "FAIL", "kind": "error",
                               "detail": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        run.stop()
        with open(args.out, "w") as handle:
            json.dump({"exit": status,
                       "elapsed_sec": round(time.time() - started, 1),
                       "port": args.port, "workdir": args.workdir,
                       "negative": args.negative, "steps": STEPS,
                       "transcript": run.transcript}, handle, indent=1,
                      sort_keys=True)

    if status == 0:
        for name in STEPS:
            print("PASS %s" % name)
    return status


if __name__ == "__main__":
    sys.exit(main())
