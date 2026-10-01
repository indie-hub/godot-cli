#!/usr/bin/env python3
"""Golden replay harness for the Godot Pipeline editor plugin.

Runs a fixed request list against the plugin found in --plugin-dir (laid out
as it sits in addons/godot_pipeline/) inside a fresh throwaway Godot project
and records every (request, reply) pair as JSON lines. Two runs against the
same plugin bytes give byte-identical output; a refactored plugin that
replies byte-identically is behavior-preserving.

Port patch (the only edit ever made to the copied plugin): the single line
`const PORT := 47821` in the copied editor_plugin.gd is replaced with
`const PORT := <port>`. The repo checkout is never touched.

Normalisation (the only one): the throwaway project's absolute path is
replaced with `<PROJECT>` in both stored request and reply text, so fresh
temporary directories compare equal. Replies that carry it are the
`project path mismatch` errors. Nothing else is normalised, sorted or
rounded; reply text is stored verbatim otherwise.

Stdlib only. Never connects to 47821.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

GODOT = os.environ.get("GODOT", "godot")
PORT_LINE = "const PORT := 47821"
WRONG_PROJECT = "/no/replay-mismatch"

PROJECT_GODOT = """; Engine configuration file.
config_version=5
[application]
config/name="gp015golden"
; No run/main_scene: the editor must start session A with no edited scene.
[editor_plugins]
enabled=PackedStringArray("res://addons/godot_pipeline/plugin.cfg")
"""

PROPS_GD = """extends Node
@export var b: bool = true
@export var i: int = 42
@export var f: float = 1.5
@export var s: String = "hello"
@export var sn: StringName = &"sname"
@export var np: NodePath = ^"Ref"
@export var v2: Vector2 = Vector2(1, 2)
@export var v3: Vector3 = Vector3(1, 2, 3)
@export var v2i: Vector2i = Vector2i(3, 4)
@export var v3i: Vector3i = Vector3i(5, 6, 7)
@export var v4: Vector4 = Vector4(1, 2, 3, 4)
@export var v4i: Vector4i = Vector4i(5, 6, 7, 8)
@export var r2: Rect2 = Rect2(1, 2, 3, 4)
@export var r2i: Rect2i = Rect2i(5, 6, 7, 8)
@export var t2: Transform2D = Transform2D(Vector2(1, 0), Vector2(0, 1), Vector2(0, 0))
@export var t3: Transform3D = Transform3D(Basis(), Vector3(0, 0, 0))
@export var col: Color = Color(1, 0, 0, 1)
@export var pb: PackedByteArray = PackedByteArray([1, 2])
@export var pi32: PackedInt32Array = PackedInt32Array([1, 2])
@export var pi64: PackedInt64Array = PackedInt64Array([1, 2])
@export var pf32: PackedFloat32Array = PackedFloat32Array([1.0])
@export var pf64: PackedFloat64Array = PackedFloat64Array([2.0])
@export var ps: PackedStringArray = PackedStringArray(["a"])
@export var pv2: PackedVector2Array = PackedVector2Array([Vector2(0, 0)])
@export var pv3: PackedVector3Array = PackedVector3Array([Vector3(0, 0, 0)])
@export var pv4: PackedVector4Array = PackedVector4Array([Vector4(0, 0, 0, 0)])
@export var pc: PackedColorArray = PackedColorArray([Color(1, 1, 1, 1)])
@export var ta_int: Array[int] = [1]
@export var ta_str: Array[String] = ["a"]
@export var arr: Array = [1, 2]
@export var d: Dictionary = {"k": 1}
@export var res: Resource
@export var ta_obj: Array[Resource] = []
@export var ta_c: Array[Callable] = []
@export_enum("Alpha", "Beta") var mode: int = 0
@export var inf_f: float = INF
@export var ninf_f: float = -INF
@export var nan_f: float = NAN
"""

SCRIPT_CLASS_GD = """extends Node
class_name QryScriptClass
"""

SUB_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp015goldensub1"]
[node name="SubRoot" type="Node"]
[node name="Inner1" type="Node2D" parent="."]
[node name="Inner2" type="Node" parent="."]
"""

MAIN_TSCN = """[gd_scene load_steps=3 format=3 uid="uid://gp015goldenmain1"]
[ext_resource type="Script" path="res://props.gd" id="1"]
[ext_resource type="PackedScene" path="res://sub.tscn" id="2"]
[node name="Main" type="Node"]
[node name="N2D" type="Node2D" parent="." groups=["heroes"]]
[node name="GrandChild" type="Node2D" parent="N2D"]
[node name="Sprite" type="Sprite2D" parent="." groups=["heroes"]]
[node name="N3D" type="Node3D" parent="."]
[node name="Ctrl" type="Control" parent="." groups=["targets"]]
[node name="LeafA" type="Node" parent="." groups=["targets"]]
[node name="LeafB" type="Node" parent="."]
[node name="leaf" type="Node" parent="."]
[node name="Ref" type="Node" parent="."]
[node name="Extra1" type="Node" parent="."]
[node name="Extra2" type="Node" parent="."]
[node name="Props" type="Node" parent="."]
script = ExtResource("1")
[node name="Sub" parent="." instance=ExtResource("2")]
[node name="Edit" parent="." instance=ExtResource("2")]
[editable path="Edit"]
"""

# Session C fixtures. Only session C uses them; sessions A and B never
# reference these paths, so their rows cannot change. Uids are fixed text in
# the headers, exactly like SUB_TSCN and MAIN_TSCN above.
C_A_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp021replaya01"]
[node name="C_A" type="Node"]
"""

C_B_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp021replayb01"]
[node name="C_B" type="Node"]
"""

C_BROKEN_SCRIPT_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp021replayc01"]
[ext_resource type="Script" path="res://c_missing.gd" id="1"]
[node name="C_Broken" type="Node"]
script = ExtResource("1")
"""

C_BROKEN_INSTANCE_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp021replayc02"]
[ext_resource type="PackedScene" path="res://c_missing.tscn" id="1"]
[node name="C_Broken" type="Node"]
[node name="Kid" parent="." instance=ExtResource("1")]
"""

C_BROKEN_SUBRES_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp021replayc03"]
[ext_resource type="Texture2D" path="res://c_missing.png" id="1"]
[node name="C_Broken" type="Sprite2D"]
texture = ExtResource("1")
"""

C_CHILD_BROKEN_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp021replayc04"]
[ext_resource type="Script" path="res://c_missing_child.gd" id="1"]
[node name="C_Child" type="Node"]
script = ExtResource("1")
"""

C_PARENT_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp021replayc05"]
[ext_resource type="PackedScene" path="res://c_child_broken.tscn" id="1"]
[node name="C_Parent" type="Node"]
[node name="Kid" parent="." instance=ExtResource("1")]
"""

C_STALE_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp021replayc06"]
[ext_resource type="Script" path="uid://gp021stale0000" id="1"]
[node name="C_Broken" type="Node"]
script = ExtResource("1")
"""

C_NOTE_TXT = """session C plain text, not a scene
"""

C_SCRIPT_GD = """extends Node
"""

C_DATA_TRES = """[gd_resource type="StandardMaterial3D" format=3]
"""

C_OLD_SCN = """[gd_scene load_steps=1 format=3]
[node name="C_Old" type="Node"]
"""

# Each entry is (kind, text) where kind is "line" (newline-terminated, one
# reply expected at once) or "idle" (sent without newline; the reply arrives
# after the plugin's 5s idle timeout). "<PROJ>" is replaced with the
# throwaway project's real path before sending.
REQ_A = [
    ("line", '{"command":"status"}'),
    ("line", '{"command":"scene_tree"}'),
    ("line", '{"command":"save_scene","project_path":"<PROJ>"}'),
    ("line", '{"command":"rename_node","node_path":"LeafA","new_name":"X","project_path":"<PROJ>"}'),
    ("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"X","project_path":"<PROJ>"}'),
    ("line", '{"command":"set_property","node_path":".","property":"process_mode","value":1,"project_path":"<PROJ>"}'),
    ("line", '{"command":"inspect_node","node_path":".","project_path":"<PROJ>"}'),
    ("line", '{"command":"query_nodes","project_path":"<PROJ>","class":null,"group":null,"name":null,"limit":100}'),
    ("line", '{"command":"delete_node","node_path":"LeafA","project_path":"<PROJ>"}'),
    ("line", '{"command":"query_nodes","project_path":"/no/replay-mismatch","class":null,"group":null,"name":null,"limit":0}'),
    ("line", 'not json at all'),
    ("line", '[1,2,3]'),
    ("line", '{"foo":1}'),
    ("line", '{"command":"nope"}'),
    ("line", '{"command":"rename_node"}'),
    ("line", '{"command":"create_node"}'),
    ("line", '{"command":"set_property"}'),
    ("line", '{"command":"inspect_node"}'),
    ("line", '{"command":"query_nodes"}'),
    ("line", '{"command":"delete_node"}'),
    ("line", '{"command":"save_scene"}'),
    ("idle", '{"command":"status"'),
    ("idle", '{"command":"status"}'),
]

SET_VALID = [
    ("b", True), ("i", 43), ("f", 2.75), ("s", "rt-xyz"), ("sn", "rsn"),
    ("np", "RefRenamed"), ("v2", [11, 22]), ("v3", [13, 14, 15]),
    ("v2i", [16, 17]), ("v3i", [18, 19, 20]), ("v4", [21, 22, 23, 24]),
    ("v4i", [25, 26, 27, 28]), ("r2", [31, 32, 33, 34]),
    ("r2i", [35, 36, 37, 38]), ("t2", [[41, 42], [43, 44], [45, 46]]),
    ("t3", [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]),
    ("col", [0.11, 0.22, 0.33, 0.44]), ("pb", [1, 2, 255]),
    ("pi32", [-2147483648, 0, 2147483647]), ("pi64", [-5, 0, 9007199254740991]),
    ("pf32", [1.5, 2.5]), ("pf64", [3.5, 4.5]), ("ps", ["a", "b"]),
    ("pv2", [[1, 2], [3, 4]]), ("pv3", [[1, 2, 3]]), ("pv4", [[1, 2, 3, 4]]),
    ("pc", [[1, 0, 0, 1]]), ("ta_int", [7, 8, 9]), ("ta_str", ["x", "y"]),
]


def q(**kw):
    d = {"command": "query_nodes", "project_path": "<PROJ>",
         "class": None, "group": None, "name": None, "limit": 100}
    d.update(kw)
    return ("line", json.dumps(d, separators=(",", ":")))


def build_b():
    out = [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        q(), q(**{"class": "Node2D"}),
        q(**{"class": "NoSuchClass123"}), q(**{"class": "QryScriptClass"}),
        q(**{"group": "heroes"}), q(**{"group": "targets"}),
        q(**{"group": "nosuchgroup"}),
        q(**{"name": "Leaf*"}), q(**{"name": "?ef"}), q(**{"name": "leaf"}),
        q(**{"class": "Node", "group": "heroes", "name": "N*"}),
        q(**{"group": "targets", "name": "Leaf*"}),
        q(**{"limit": 3}), q(**{"limit": 19}), q(**{"limit": 1000}),
        q(**{"limit": 0}), q(**{"limit": -1}), q(**{"limit": 1001}),
        q(**{"limit": 1.5}), ("line", '{"command":"query_nodes","project_path":"<PROJ>","class":null,"group":null,"name":null,"limit":"abc"}'),
        ("line", '{"command":"query_nodes","project_path":"/no/replay-mismatch","class":null,"group":null,"name":null,"limit":0}'),
    ]
    for p in [".", "N2D", "N3D", "Ctrl", "Props", "Sub/Inner1", "Edit/Inner1"]:
        out.append(("line", json.dumps({"command": "inspect_node", "node_path": p, "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"inspect_node","node_path":".","project_path":"/no/replay-mismatch"}'))
    for p in ["", "/Main", "A:B", "../N2D", "NoSuch"]:
        out.append(("line", json.dumps({"command": "inspect_node", "node_path": p, "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"rename_node","node_path":"Ref","new_name":"RefRenamed","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"rename_node","node_path":"RefRenamed","new_name":"X","project_path":"/no/replay-mismatch"}'))
    out.append(("line", '{"command":"rename_node","node_path":"NoSuch","new_name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"rename_node","node_path":"RefRenamed","new_name":"Bad/Name","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"rename_node","node_path":"RefRenamed","new_name":"","project_path":"<PROJ>"}'))
    for p in ["", "/Main", "A:B", "../N2D"]:
        out.append(("line", json.dumps({"command": "rename_node", "node_path": p, "new_name": "X", "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"rename_node","node_path":"leaf","new_name":"LeafA","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"X","project_path":"/no/replay-mismatch"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"NoSuch123","name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"Resource","name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"CanvasItem","name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"QryScriptClass","name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":"Nope","class_name":"Node","name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"Bad/Name","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"","project_path":"<PROJ>"}'))
    for p in ["", "/Main", "A:B", "../N2D"]:
        out.append(("line", json.dumps({"command": "create_node", "parent_path": p, "class_name": "Node", "name": "X", "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"create_node","parent_path":"Sub/Inner1","class_name":"Node","name":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"NewKid","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"create_node","parent_path":".","class_name":"Control","name":"Ctl2","project_path":"<PROJ>"}'))
    for prop, val in SET_VALID:
        out.append(("line", json.dumps({"command": "set_property", "node_path": "Props", "property": prop, "value": val, "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"arr","value":[1],"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"d","value":{"a":1},"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"res","value":null,"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"ta_obj","value":[],"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"ta_c","value":[],"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"b","value":"yes","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"v2i","value":[1.5,2],"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"N2D","property":"modulate","value":"notacolor","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"mode","value":5,"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Ctl2","property":"layout_mode","value":1,"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"nope","value":1,"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"NoSuch","property":"i","value":1,"project_path":"<PROJ>"}'))
    for p in ["", "/Main", "A:B", "../N2D"]:
        out.append(("line", json.dumps({"command": "set_property", "node_path": p, "property": "i", "value": 1, "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"name","value":"X","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Props","property":"i","value":1,"project_path":"/no/replay-mismatch"}'))
    out.append(("line", '{"command":"set_property","node_path":"Sub/Inner1","property":"position","value":[1,2],"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"set_property","node_path":"Edit/Inner1","property":"position","value":[3,4],"project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"delete_node","node_path":".","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"delete_node","node_path":"Sub/Inner1","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"delete_node","node_path":"NoSuch","project_path":"<PROJ>"}'))
    for p in ["", "/Main", "A:B", "../N2D"]:
        out.append(("line", json.dumps({"command": "delete_node", "node_path": p, "project_path": "<PROJ>"}, separators=(",", ":"))))
    out.append(("line", '{"command":"delete_node","node_path":"Extra1","project_path":"/no/replay-mismatch"}'))
    out.append(("line", '{"command":"delete_node","node_path":"Extra1","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"delete_node","node_path":"N2D","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"delete_node","node_path":"Edit/Inner2","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("line", '{"command":"save_scene","project_path":"/no/replay-mismatch"}'))
    out.append(q())
    out.append(("line", '{"command":"inspect_node","node_path":"RefRenamed","project_path":"<PROJ>"}'))
    return out


def ic(clazz, project="<PROJ>"):
    return ("line", json.dumps({"command": "inspect_class", "class": clazz,
                                "project_path": project}, separators=(",", ":")))


def os_(scene, save=False, project="<PROJ>", no_save_key=False):
    d = {"command": "open_scene", "project_path": project, "scene_path": scene}
    if not no_save_key:
        d["save"] = save
    return ("line", json.dumps(d, separators=(",", ":")))


def build_c():
    # Session C: inspect-class (read-only, no scene needed) then open-scene
    # (stateful). Runs in a fresh no-scene editor like session A; the dirty
    # phase dirties scenes with create_node and never saves except once.
    out = [
        ic("Node"),
        ic("Control"),
        ic("Object"),
        ic("RefCounted"),
        ic("NoSuchClass999"),
        ic(""),
        ic("QryScriptClass"),
        ic(7),
        ("line", '{"command":"inspect_class","project_path":"<PROJ>"}'),
        ic("Nope", "/no/replay-mismatch"),
        os_("res://c_missing.tscn", project="/no/replay-mismatch"),
        ("line", '{"command":"open_scene","project_path":"<PROJ>"}'),
        ("line", '{"command":"open_scene","scene_path":"res://c_a.tscn"}'),
        os_("res://c_a.tscn", project=7),
        ("line", '{"command":"open_scene","project_path":"<PROJ>","scene_path":7,"save":false}'),
        os_("res://c_a.tscn", save="yes"),
        os_(""),
        os_("res://c_missing.tscn"),
        os_("res://"),
        os_("res://c_note.txt"),
        os_("res://c_script.gd"),
        os_("res://c_data.tres"),
        os_("res://c_old.scn"),
        os_("res://c_broken_script.tscn"),
        os_("res://c_broken_instance.tscn"),
        os_("res://c_broken_subres.tscn"),
        os_("res://c_parent.tscn"),
        os_("res://c_stale.tscn"),
        os_("res://c_a.tscn"),
        ("line", '{"command":"status"}'),
        os_("c_b.tscn"),
        os_("res://addons/../c_a.tscn"),
        os_("<PROJ>/c_b.tscn"),
        os_("res://c_b.tscn"),
        ("line", '{"command":"open_scene","project_path":"<PROJ>","scene_path":"res://c_a.tscn"}'),
        os_("res://c_b.tscn", save=True),
        os_("uid://gp021replaya01"),
        ("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"DirtKid","project_path":"<PROJ>"}'),
        os_("res://c_b.tscn"),
        ("line", '{"command":"status"}'),
        ("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"DirtKid","project_path":"<PROJ>"}'),
        os_("res://c_a.tscn", save=True),
        ("line", '{"command":"status"}'),
        os_("res://c_b.tscn"),
        ("line", '{"command":"create_node","parent_path":".","class_name":"Node","name":"DirtKid","project_path":"<PROJ>"}'),
        os_("res://c_a.tscn"),
        ("line", '{"command":"status"}'),
        os_("res://c_broken_script.tscn"),
        ("line", '{"command":"status"}'),
        os_("res://c_broken_script.tscn", save=True),
        ("line", '{"command":"status"}'),
        ic("Node2D"),
    ]
    return out


def send_one(port, text, newline, timeout):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(text.encode() + (b"\n" if newline else b""))
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


def wait_listener(port, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("listener on %d never came up" % port)


def status_scene(port):
    reply = send_one(port, '{"command":"status"}', True, 15)
    return json.loads(reply).get("data", {}).get("scene_path")


def launch(proj, scene):
    args = [GODOT, "--headless", "--path", proj, "--editor"]
    if scene:
        args.append(scene)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
    if proc.poll() is None:
        raise RuntimeError("editor process would not die")


def run_session(port, proj, scene, requests, expect_scene):
    proc = launch(proj, scene)
    try:
        wait_listener(port)
        if expect_scene:
            end = time.time() + 90
            while time.time() < end:
                if status_scene(port) == "res://main.tscn":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("edited scene never opened")
        else:
            time.sleep(3)
            if status_scene(port) is not None:
                raise RuntimeError("expected no edited scene, one is open")
        rows = []
        for kind, text in requests:
            wire = text.replace("<PROJ>", proj)
            reply = send_one(port, wire, kind == "line", 25)
            rows.append((wire.replace(proj, "<PROJECT>"),
                         reply.replace(proj, "<PROJECT>")))
        return rows
    finally:
        stop(proc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-dir", required=True)
    ap.add_argument("--port", type=int, default=47902)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.port == 47821:
        raise SystemExit("port 47821 is Bruno's live editor; refusing")
    gd = os.path.join(a.plugin_dir, "editor_plugin.gd")
    if not os.path.isfile(gd):
        raise SystemExit("no editor_plugin.gd in --plugin-dir")
    proj = os.path.realpath(tempfile.mkdtemp(prefix="gp015golden-"))
    try:
        with open(os.path.join(proj, "project.godot"), "w") as f:
            f.write(PROJECT_GODOT)
        with open(os.path.join(proj, "props.gd"), "w") as f:
            f.write(PROPS_GD)
        with open(os.path.join(proj, "my_class.gd"), "w") as f:
            f.write(SCRIPT_CLASS_GD)
        with open(os.path.join(proj, "sub.tscn"), "w") as f:
            f.write(SUB_TSCN)
        with open(os.path.join(proj, "main.tscn"), "w") as f:
            f.write(MAIN_TSCN)
        # Session C fixtures. Sessions A and B never reference them.
        for name, text in [
            ("c_a.tscn", C_A_TSCN),
            ("c_b.tscn", C_B_TSCN),
            ("c_broken_script.tscn", C_BROKEN_SCRIPT_TSCN),
            ("c_broken_instance.tscn", C_BROKEN_INSTANCE_TSCN),
            ("c_broken_subres.tscn", C_BROKEN_SUBRES_TSCN),
            ("c_child_broken.tscn", C_CHILD_BROKEN_TSCN),
            ("c_parent.tscn", C_PARENT_TSCN),
            ("c_stale.tscn", C_STALE_TSCN),
            ("c_note.txt", C_NOTE_TXT),
            ("c_script.gd", C_SCRIPT_GD),
            ("c_data.tres", C_DATA_TRES),
            ("c_old.scn", C_OLD_SCN),
        ]:
            with open(os.path.join(proj, name), "w") as f:
                f.write(text)
        dst = os.path.join(proj, "addons", "godot_pipeline")
        shutil.copytree(a.plugin_dir, dst, ignore=shutil.ignore_patterns("*.uid"))
        target = os.path.join(dst, "editor_plugin.gd")
        with open(target) as f:
            patched = f.read().replace(PORT_LINE, "const PORT := %d" % a.port)
        if patched.count("const PORT := %d" % a.port) != 1:
            raise SystemExit("port substitution did not hit exactly once")
        with open(target, "w") as f:
            f.write(patched)
        r = subprocess.run([GODOT, "--headless", "--path", proj, "--import"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=180)
        if r.returncode != 0:
            raise SystemExit("godot --import failed")
        rows = []
        # Run order is A, C, B: a fresh editor restores the previous session's
        # open scenes, so session C (no scene, like A) must run before session
        # B opens main.tscn. Rows are still stored A, B, C.
        rows_a = run_session(a.port, proj, None, REQ_A, False)
        rows_c = run_session(a.port, proj, None, build_c(), False)
        rows_b = run_session(a.port, proj, "res://main.tscn", build_b(), True)
        rows = rows_a + rows_b + rows_c
        with open(a.out, "w") as f:
            for req, rep in rows:
                f.write(json.dumps({"request": req, "reply": rep}, separators=(",", ":")) + "\n")
        print("wrote %d pairs to %s" % (len(rows), a.out))
    finally:
        shutil.rmtree(proj, ignore_errors=True)


if __name__ == "__main__":
    main()
