#!/usr/bin/env python3
"""End-to-end acceptance run for Phase 4 Milestone A (assemble).

Drives the merged commands through the real godot-cli binary against one
fixture scene: assign a texture and a collision shape, attach a player script,
set two exported properties, undo and redo every edit, prove that rejected
requests leave no trace, save explicitly, and reload in a fresh editor.

Every step asserts from replies and from reads of the live editor, never from a
command's acknowledgement alone. The script prints one PASS or FAIL line per
step and exits non-zero on the first failed assertion.

  python3 milestone_a.py --godot <editor> --cli <godot-cli> --port N \
      --workdir <new dir> --out <transcript.json>

--workdir must not exist; the script creates it and never deletes anything.
"""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time

# A 1x1 transparent PNG, written as bytes so the fixture needs no image asset
# in the repository and no import step can go missing.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

PROJECT_GODOT = """; Engine configuration file.
config_version=5

[application]

config/name="godot-cli-acceptance-a"

[editor_plugins]

enabled=PackedStringArray("res://addons/godot_pipeline/plugin.cfg")
"""

PLAYER_GD = """extends Node2D

@export var speed: float = 100.0
@export var health: int = 3
"""

BROKEN_GD = "extends Node2D\nfunc f(:\n"
PLAYER_B_GD = "extends Node2D\n"
NODE3D_GD = "extends Node3D\n"
NOT_A_RESOURCE = "this file is plain text and not a resource\n"

MAIN_TSCN = """[gd_scene format=3]

[node name="Player" type="Node2D"]

[node name="Sprite" type="Sprite2D" parent="."]

[node name="Shape" type="CollisionShape2D" parent="."]
"""

# The scene's undo history lives behind the manager: the value eval receives is an
# EditorUndoRedoManager, which has no undo() of its own, so the UndoRedo is
# fetched by history id for the edited scene root.
#
# A persistent signal connection is not a stored property, so it would not show up
# in the property walk. Each node's own signals are therefore read through
# get_signal_connection_list and every CONNECT_PERSIST entry recorded as plain
# strings and a flag: eval encodes a Signal or Callable as an empty typed object,
# so the signal name, the target path and the method are resolved here instead.
SNAPSHOT = """extends RefCounted
func run(editor, undo_redo) -> Variant:
	var root = editor.get_edited_scene_root()
	var h = undo_redo.get_history_undo_redo(undo_redo.get_object_history_id(root))
	var nodes := []
	var stack = [root]
	while not stack.is_empty():
		var n = stack.pop_back()
		var rp = root.get_path_to(n)
		var key = "." if str(rp) == "" else "./" + str(rp)
		if key == "./.":
			key = "."
		var s = n.get_script()
		var props := []
		for p in n.get_property_list():
			if p["usage"] & PROPERTY_USAGE_STORAGE == 0:
				continue
			props.append([p["name"], var_to_str(n.get(p["name"]))])
		props.sort_custom(func(a, b): return a[0] < b[0])
		var conns := []
		for sig in n.get_signal_list():
			for c in n.get_signal_connection_list(sig["name"]):
				if c["flags"] & Object.CONNECT_PERSIST == 0:
					continue
				var target = c["callable"].get_object()
				var trp = root.get_path_to(target)
				conns.append([str(c["signal"].get_name()),
					"." if target == root or str(trp) == "" else "./" + str(trp),
					str(c["callable"].get_method()), c["flags"]])
		conns.sort_custom(func(a, b): return a[0] < b[0] if a[0] != b[0] else a[1] < b[1])
		nodes.append([key, n.get_class(), (s.resource_path if s != null else ""), n.get_groups(), props, conns])
		for c in n.get_children(true):
			stack.append(c)
	return {"history": h.get_history_count(), "unsaved": Array(editor.get_unsaved_scenes()), "nodes": nodes}
"""

# Presses the editor's Scene menu (title bar) item whose id is WANTED, at item
# slot SLOT. Two PopupMenus in the editor carry Undo and Redo items: this one at
# ids 13 and 14, and the Scene dock's line-editing menu at ids 5 and 6. Those
# four ids were measured on Godot 4.7.2 headless and were the only Undo and Redo
# items found. The menu is therefore chosen by item id and item text, never by
# position or by taking the first match.
PRESS_MENU = """extends RefCounted
func run(editor, undo_redo) -> Variant:
	var target = null
	var stack = [editor.get_base_control()]
	while not stack.is_empty():
		var n = stack.pop_back()
		if n is PopupMenu and n.item_count > SLOT and n.get_item_id(SLOT) == WANTED and n.get_item_text(SLOT) == LABEL:
			target = n
		for c in n.get_children(true):
			stack.append(c)
	if target == null:
		return "no PopupMenu carries id WANTED at slot SLOT"
	target.emit_signal("id_pressed", WANTED)
	return "pressed " + target.get_item_text(SLOT)
"""

# The Undo and Redo items of the Scene menu in the editor's title bar. The Scene
# dock carries its own Undo and Redo under different ids, so both the id and the
# slot are given and the menu is matched on both.
UNDO_SLOT, UNDO_ID, UNDO_LABEL = 16, 13, "Undo"
REDO_SLOT, REDO_ID, REDO_LABEL = 17, 14, "Redo"


def decode(value):
    """Decode eval's tagged value encoding into plain Python values.

    eval reports a GDScript value as {"type": ..., "args": [...]} with every
    scalar prefixed by its type letter, so a before/after comparison has to
    decode it or two readings of the same state would never compare equal. A
    typed Array arrives as a dictionary carrying its element type, and a value
    that is already plain is returned unchanged, so the function is safe to
    apply more than once.
    """
    if isinstance(value, dict):
        if value.get("type") == "Dictionary":
            args = value.get("args", [])
            return {decode(args[i]): decode(args[i + 1]) for i in range(0, len(args) - 1, 2)}
        if value.get("type") == "Array":
            return [decode(v) for v in value.get("args", [])]
        return value
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, str):
        for prefix in ("i:", "f:", "sn:", "s:"):
            if value.startswith(prefix):
                rest = value[len(prefix):]
                if prefix == "i:":
                    try:
                        return int(rest)
                    except ValueError:
                        return value
                if prefix == "f:":
                    try:
                        return float(rest)
                    except ValueError:
                        return value
                return rest
    return value


class Failed(Exception):
    """A step assertion that did not hold."""


# The step being run, so a failure names where it happened.
CURRENT = ["startup"]


def die(message):
    raise Failed("%s: %s" % (CURRENT[0], message))


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
    def launch(self, scene=None):
        args = [self.godot, "--headless", "--path", self.proj, "--editor"]
        if scene:
            args.append(scene)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.wait_listener()
        if scene:
            end = time.time() + 90
            while time.time() < end:
                if self.status_scene() is not None:
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

    def wait_listener(self, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
                return
            except OSError:
                time.sleep(0.5)
        die("the plugin listener on port %d never came up" % self.port)

    def status_scene(self):
        return json.loads(self.send({"command": "status"})).get("data", {}).get("scene_path")

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
    def cli(self, args):
        # --port always comes first: without it the CLI tries its default port,
        # which is the port of a developer's live editor.
        proc = subprocess.run([self.cli_bin, "--port", str(self.port)] + args,
                              capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "").strip()
        try:
            parsed = json.loads(out) if out else None
        except json.JSONDecodeError:
            parsed = None
        return {"argv": args, "exit": proc.returncode, "stdout": out,
                "stderr": (proc.stderr or "").strip(), "json": parsed}

    def eval(self, code):
        return self.cli(["call", "eval", code, "--project-path", self.proj])

    def read(self, reply, what):
        """The value of a read, or a FAIL. A read that did not succeed must never
        be mistaken for a state that legitimately holds no value."""
        if reply["exit"] != 0:
            die("the %s read was refused: %s" % (what, reply["stderr"] or reply["stdout"]))
        if reply["json"] is None:
            die("the %s read returned no JSON: %s" % (what, reply["stdout"][:200]))
        if reply["json"].get("errors"):
            die("the %s read reported errors: %s" % (what, reply["json"]["errors"]))
        if "value" not in reply["json"]:
            die("the %s read carried no value: %s" % (what, json.dumps(reply["json"])[:200]))
        value = decode(reply["json"]["value"])
        if value is None:
            die("the %s read decoded to no value: %s" % (what, json.dumps(reply["json"])[:200]))
        return value

    def snapshot(self):
        """Everything the scene stores, plus the history size and unsaved list."""
        return self.read(self.eval(SNAPSHOT), "scene snapshot")

    def press(self, slot, ident, label):
        code = (PRESS_MENU.replace("LABEL", '"%s"' % label)
                .replace("WANTED", str(ident)).replace("SLOT", str(slot)))
        return self.read(self.eval(code), "%s press" % label)

    def node_script(self, where):
        """The script path of one node read out of the complete snapshot."""
        for entry in self.snapshot()["nodes"]:
            if entry[0] == "./" + where if where != "." else entry[0] == ".":
                return entry[2] or None
        die("the node %s is not in the snapshot" % where)

    def set_property(self, node, prop, value):
        return self.cli(["call", "set-property", node, prop,
                         value if isinstance(value, str) else json.dumps(value),
                         "--project-path", self.proj])

    def attach(self, node, script=None, replace=False):
        args = ["call", "attach-script", node]
        if script is not None:
            args.append(script)
        if replace:
            args.append("--replace")
        return self.cli(args + ["--project-path", self.proj])

    def inspect(self, node):
        return self.cli(["call", "inspect-node", "--node-path", node,
                         "--project-path", self.proj])

    def shown(self, node, prop):
        """One property read through inspect-node, or a FAIL. The value is
        decoded, so it compares as a typed number or list and a value like 320
        can never pass a check written for 32. A failed read raises instead of
        returning None, which an empty value would otherwise look like."""
        reply = self.inspect(node)
        if reply["exit"] != 0:
            die("inspect-node on %s was refused: %s" % (node, reply["stderr"]))
        if reply["json"] is None or "properties" not in reply["json"]:
            die("inspect-node on %s carried no properties: %s"
                % (node, json.dumps(reply["json"])[:200]))
        for entry in reply["json"]["properties"]:
            if entry["name"] == prop:
                return decode(entry.get("value"))
        return None

    def note(self, step, kind, detail):
        self.transcript.append({"step": step, "kind": kind, "detail": detail})


def build_fixture(run):
    proj = run.proj
    if os.path.isdir(proj):
        die("the project directory already exists: %s" % proj)
    os.makedirs(os.path.join(proj, "addons"))
    with open(os.path.join(proj, "project.godot"), "w") as h:
        h.write(PROJECT_GODOT)
    shutil.copytree(os.path.join(run.repo, "addon", "godot_pipeline"),
                    os.path.join(proj, "addons", "godot_pipeline"),
                    ignore=shutil.ignore_patterns("*.uid"))
    plugin = os.path.join(proj, "addons", "godot_pipeline", "editor_plugin.gd")
    with open(plugin) as h:
        text = h.read()
    text = text.replace("const PORT := 47821", "const PORT := %d" % run.port)
    with open(plugin, "w") as h:
        h.write(text)
    for name, content in [("player.gd", PLAYER_GD), ("player_b.gd", PLAYER_B_GD),
                          ("broken.gd", BROKEN_GD),
                          ("only3d.gd", NODE3D_GD), ("notes.txt", NOT_A_RESOURCE),
                          ("main.tscn", MAIN_TSCN), ("icon.png", None)]:
        with open(os.path.join(proj, name), "wb") as h:
            h.write(PNG_1X1 if content is None else content.encode())
    # One import pass gives the scripts and the scene resource ids, so the
    # editor's first launch does not race its own startup scan.
    subprocess.run([run.godot, "--headless", "--path", proj, "--import"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)


def property_of(snapshot, path, name):
    """One stored property of one node in a snapshot, or None when absent."""
    for entry in snapshot["nodes"]:
        if entry[0] == path:
            for prop in entry[4]:
                if prop[0] == name:
                    return prop[1]
    return None


def texture_of(snapshot):
    """The stored texture as its var_to_str text, or None when unset."""
    value = property_of(snapshot, "./Sprite", "texture")
    # var_to_str renders a null value as the text "null".
    if value in (None, "null", "Null"):
        return None
    return value


def shape_size_of(snapshot):
    """The stored shape's size as var_to_str text, or None when unset."""
    return property_of(snapshot, "./Shape", "shape")


def script_of(snapshot, path="."):
    for entry in snapshot["nodes"]:
        if entry[0] == path:
            return entry[2] or None
    return None


def run_scenario(run):
    t = run.transcript

    # ---- S1 baseline: the whole scene, the history and the unsaved list ----
    CURRENT[0] = "S1"
    baseline = run.snapshot()
    t.append({"step": "S1", "kind": "baseline", "detail": baseline})
    if texture_of(baseline) is not None or script_of(baseline) is not None:
        die("the fixture scene is not empty: %s" % json.dumps(baseline)[:400])

    # Each accepted edit is recorded, so the undo walk can be compared against
    # the neighbour snapshot it must produce rather than only against the ends.
    states = [baseline]

    # ---- S2 assign a texture -----------------------------------------
    CURRENT[0] = "S2"
    before = baseline
    r = run.set_property("Sprite", "texture", "res://icon.png")
    if r["exit"] != 0:
        die("set-property texture was refused: %s" % r["stderr"])
    after = run.snapshot()
    if "res://icon.png" not in (texture_of(after) or ""):
        die("the stored texture is %r, not res://icon.png" % texture_of(after))
    if (run.shown("Sprite", "texture") or {}).get("path") != "res://icon.png":
        die("inspect-node does not show the texture as res://icon.png: %r"
            % run.shown("Sprite", "texture"))
    if after["history"] != before["history"] + 1:
        die("history went %s to %s, expected +1" % (before["history"], after["history"]))
    if after["unsaved"] == before["unsaved"]:
        die("did not mark the scene unsaved")
    states.append(after)
    t.append({"step": "S2", "kind": "assign_texture", "detail": r["json"]})

    # ---- S3 assign an embedded collision shape ------------------------
    CURRENT[0] = "S3"
    before = states[-1]
    r = run.set_property("Shape", "shape",
                         {"class": "RectangleShape2D", "properties": {"size": [32, 48]}})
    if r["exit"] != 0:
        die("set-property shape was refused: %s" % r["stderr"])
    after = run.snapshot()
    if shape_size_of(after) is None:
        die("the stored shape is unset, expected a RectangleShape2D sized 32 by 48")
    # The size is compared as two numbers from inspect-node, never as the digits
    # 32 and 48 in the snapshot text, so a 320 by 480 shape cannot pass.
    shown = run.shown("Shape", "shape") or {}
    if shown.get("type") != "RectangleShape2D":
        die("inspect-node shows the shape as %r, not a RectangleShape2D" % shown)
    size = [p for p in shown.get("properties", []) if p["name"] == "size"]
    if not size or size[0]["value"] != [32.0, 48.0]:
        die("inspect-node does not show the shape sized exactly 32 by 48: %r"
            % (size[0]["value"] if size else None))
    if after["history"] != before["history"] + 1:
        die("history went %s to %s, expected +1" % (before["history"], after["history"]))
    states.append(after)
    t.append({"step": "S3", "kind": "assign_shape", "detail": r["json"]})

    # ---- S4 attach the player script ----------------------------------
    CURRENT[0] = "S4"
    before = states[-1]
    r = run.attach(".", "res://player.gd")
    if r["exit"] != 0:
        die("attach-script was refused: %s" % r["stderr"])
    if r["json"].get("action") != "attach":
        die("reply action is %r, not attach" % r["json"].get("action"))
    if r["json"].get("tool_script") is not False:
        die("reply tool_script is %r, expected false" % r["json"].get("tool_script"))
    after = run.snapshot()
    if script_of(after) != "res://player.gd":
        die("the root node holds %r, not res://player.gd" % script_of(after))
    if after["history"] != before["history"] + 1:
        die("history went %s to %s, expected +1" % (before["history"], after["history"]))
    states.append(after)
    t.append({"step": "S4", "kind": "attach_script", "detail": r["json"]})

    # ---- S5 set the two exported properties ----------------------------
    CURRENT[0] = "S5"
    before = states[-1]
    # Each export is compared as a number read back through inspect-node, never
    # as the digits in the snapshot text, so 2505.0 cannot pass a check for 250.
    for prop, value, wanted in [("speed", "250", 250.0), ("health", "5", 5)]:
        r = run.set_property(".", prop, value)
        if r["exit"] != 0:
            die("set-property %s was refused: %s" % (prop, r["stderr"]))
        got = run.shown(".", prop)
        if got != wanted:
            die("inspect-node stores %s as %r, expected %r" % (prop, got, wanted))
        states.append(run.snapshot())
    after = states[-1]
    if after["history"] != before["history"] + 2:
        die("history went %s to %s, expected +2" % (before["history"], after["history"]))
    t.append({"step": "S5", "kind": "set_exports", "detail": states[-3:]})

    # ---- S6 undo every edit, then redo every edit ---------------------
    # Each single press is compared with the neighbour snapshot recorded while
    # the edits were made, so an undo that undoes the wrong step is caught.
    CURRENT[0] = "S6"
    presses = []
    for index in range(len(states) - 2, -1, -1):
        presses.append(("undo", index, run.press(UNDO_SLOT, UNDO_ID, UNDO_LABEL)))
        got = run.snapshot()
        if got["nodes"] != states[index]["nodes"]:
            die("undo %d left the scene at %s, expected the state before edit %d"
                % (len(states) - 2 - index, json.dumps(got["nodes"])[:300], index))
    if presses[-1][2] is None:
        die("the final undo press reported nothing")
    for index in range(1, len(states)):
        presses.append(("redo", index, run.press(REDO_SLOT, REDO_ID, REDO_LABEL)))
        got = run.snapshot()
        if got["nodes"] != states[index]["nodes"]:
            die("redo %d left the scene at %s, expected the state after edit %d"
                % (index, json.dumps(got["nodes"])[:300], index))
    t.append({"step": "S6", "kind": "undo_then_redo",
              "detail": {"presses": [p[0] for p in presses],
                         "states_recorded": len(states),
                         "undo_count": len(states) - 1}})

    # ---- S7 rejected requests leave no trace --------------------------
    # Each case asserts the substring the message must contain, so a refusal
    # that names the wrong property, node or script fails here.
    CURRENT[0] = "S7"
    rejections = [
        ("texture_missing_path",
         lambda: run.set_property("Sprite", "texture", "res://absent.png"),
         ["texture", "resource not found"]),
        ("texture_path_not_a_resource",
         lambda: run.set_property("Sprite", "texture", "res://notes.txt"),
         ["texture", "resource not found"]),
        ("texture_wrong_class",
         lambda: run.set_property("Sprite", "texture", "res://player.gd"),
         ["texture", "declared types"]),
        ("embedded_class_not_allowed",
         lambda: run.set_property("Shape", "shape",
                                  {"class": "StandardMaterial3D", "properties": {}}),
         ["shape", "declared types"]),
        ("embedded_unknown_property",
         lambda: run.set_property("Shape", "shape",
                                  {"class": "RectangleShape2D", "properties": {"nope": 1}}),
         ["shape", "unknown property", "nope"]),
        ("attach_syntax_error_script",
         lambda: run.attach("Sprite", "res://broken.gd"),
         ["broken.gd", "did not compile"]),
        ("attach_node3d_script_on_node2d",
         lambda: run.attach("Sprite", "res://only3d.gd"),
         ["only3d.gd", "Node3D", "Sprite"]),
        # Sprite must hold a script before the no-replace refusal can fire, and
        # the second script must be compatible with the node, otherwise the
        # base-class check would refuse first and hide the guard under test.
        ("seed_sprite_script", lambda: run.attach("Sprite", "res://player.gd"), None),
        ("attach_replace_without_flag",
         lambda: run.attach("Sprite", "res://player_b.gd"),
         ["node already has a script", "player.gd"]),
        ("set_speed_from_a_string", lambda: run.set_property(".", "speed", '"fast"'),
         ["speed", "expected a number"]),
    ]
    refusals = []
    for entry in rejections:
        name, send = entry[0], entry[1]
        wanted = entry[2] if len(entry) > 2 else None
        # The request is sent between the two reads, so the bracket covers it.
        before = run.snapshot()
        reply = send()
        after = run.snapshot()
        if wanted is None:
            # A seeding step, not a refusal: it must succeed, and its brackets
            # exist only to record what it changed.
            if reply["exit"] != 0:
                die("the seeding step %s was refused: %s" % (name, reply["stderr"]))
            refusals.append({"case": name, "seeded": True, "argv": reply["argv"],
                             "reply": reply["json"], "changed": before != after})
            continue
        if reply["exit"] == 0:
            die("%s was accepted but should have been refused: %s" % (name, reply["stdout"]))
        message = reply["stderr"] or reply["stdout"]
        absent = [w for w in wanted if w not in message]
        if absent:
            die("%s refused without naming %s: %s" % (name, absent, message))
        if before != after:
            die("%s left a trace: history %s to %s, unsaved %s to %s, nodes %s"
                % (name, before["history"], after["history"], before["unsaved"],
                   after["unsaved"], "equal" if before["nodes"] == after["nodes"] else "CHANGED"))
        refusals.append({"case": name, "argv": reply["argv"], "message": message,
                         "expected_substrings": wanted})
    t.append({"step": "S7", "kind": "refusals", "detail": refusals})

    # ---- S8 explicit save ---------------------------------------------
    CURRENT[0] = "S8"
    before = run.snapshot()
    r = run.cli(["call", "save-scene", "--project-path", run.proj])
    if r["exit"] != 0:
        die("save-scene was refused: %s" % r["stderr"])
    after = run.snapshot()
    if after["unsaved"] != []:
        die("the unsaved list is %s after save-scene, expected empty" % after["unsaved"])
    with open(os.path.join(run.proj, "main.tscn")) as h:
        saved = h.read()
    # Each numeric line is matched whole, so size = Vector2(320, 480) does not
    # satisfy a check for size = Vector2(32, 48).
    lines = [ln.strip() for ln in saved.splitlines()]
    needed = ["size = Vector2(32, 48)", "speed = 250.0", "health = 5"]
    missing = [n for n in needed if n not in lines]
    if missing:
        die("the saved scene is missing the exact lines %s\n%s" % (missing, saved))
    for token in ["[ext_resource", "icon.png", "RectangleShape2D", "sub_resource",
                  "res://player.gd"]:
        if token not in saved:
            die("the saved scene is missing %s\n%s" % (token, saved))
    t.append({"step": "S8", "kind": "save", "detail": {"reply": r["json"],
                                                       "before": before, "after": after,
                                                       "saved_lines": saved.split("\n")}})

    # ---- S9 persistence in a fresh editor -----------------------------
    CURRENT[0] = "S9"
    run.stop()
    run.launch("res://main.tscn")
    fresh = run.snapshot()
    if "res://icon.png" not in (texture_of(fresh) or ""):
        die("the fresh editor stores the texture as %r" % texture_of(fresh))
    if shape_size_of(fresh) is None:
        die("the fresh editor stores no shape, expected one sized 32 by 48")
    shown = run.shown("Shape", "shape") or {}
    size = [p for p in shown.get("properties", []) if p["name"] == "size"]
    if not size or size[0]["value"] != [32.0, 48.0]:
        die("the fresh editor stores the shape sized %r, expected exactly 32 by 48"
            % (size[0]["value"] if size else None))
    if script_of(fresh) != "res://player.gd":
        die("the fresh editor stores the root script as %r" % script_of(fresh))
    for prop, wanted in [("speed", 250.0), ("health", 5)]:
        got = run.shown(".", prop)
        if got != wanted:
            die("the fresh editor stores %s as %r, expected %r" % (prop, got, wanted))
    if fresh["history"] != 0:
        die("the fresh editor history is %s, expected 0" % fresh["history"])
    if fresh["unsaved"] != []:
        die("the fresh editor unsaved list is %s, expected empty" % fresh["unsaved"])
    t.append({"step": "S9", "kind": "fresh_editor", "detail": fresh})


STEPS = ["S1 baseline", "S2 assign a texture", "S3 assign a collision shape",
         "S4 attach the player script", "S5 set exported properties",
         "S6 undo and redo", "S7 rejected requests", "S8 explicit save",
         "S9 persistence"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--godot", required=True)
    ap.add_argument("--cli", required=True)
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    args = ap.parse_args()

    if os.path.exists(args.workdir):
        die("--workdir already exists, refusing to reuse it: %s" % args.workdir)
    os.makedirs(args.workdir)

    run = Run(args.godot, args.cli, args.port, args.repo, args.workdir)
    started = time.time()
    status = 1
    try:
        build_fixture(run)
        run.launch("res://main.tscn")
        run_scenario(run)
        status = 0
    except Failed as exc:
        print("FAIL %s" % exc)
        run.note("FAIL", "assertion", str(exc))
    except Exception as exc:  # a broken step must still leave a transcript
        print("FAIL %s: %s" % (type(exc).__name__, exc))
        run.note("FAIL", "error", "%s: %s" % (type(exc).__name__, exc))
    finally:
        run.stop()
        with open(args.out, "w") as h:
            json.dump({"exit": status, "elapsed_sec": round(time.time() - started, 1),
                       "port": args.port, "workdir": args.workdir,
                       "steps": STEPS, "transcript": run.transcript}, h, indent=1)

    if status == 0:
        for name in STEPS:
            print("PASS %s" % name)
    return status


if __name__ == "__main__":
    sys.exit(main())