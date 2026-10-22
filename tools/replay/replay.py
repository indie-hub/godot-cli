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

Normalisation (eval only, inside the harness): the throwaway project's absolute
path is replaced with `<PROJECT>` in both stored request and reply text, so fresh
temporary directories compare equal; eval replies additionally replace the
measured `elapsed_msec` with `"<ELAPSED>"` and the anonymous `gdscript://-<id>.gd`
source path with `<ANON>`, so stored rows are reproducible. Nothing else is
normalised, sorted or rounded; reply text is stored verbatim otherwise. Every
recorded eval reply body must parse as JSON, so an invalid reply fails the
recording instead of being blessed into the baseline. `check_eval_display`
also requires every status-ok eval reply to carry `display` (a string, or null
when the run did not complete or the display is over the cap), and it pairs
every display row with a parse request that returns `str_to_var` of the display
the reply carried and requires the parsed value to equal the value that row
returned.

Stdlib only. Never connects to 47821.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

GODOT = os.environ.get("GODOT", "godot")
PORT_LINE = "const PORT := 47821"
WRONG_PROJECT = "/no/replay-mismatch"

# Commands a later session adds, staged out of the copied plugin while the
# earlier sessions record and restored just before the session that needs
# them: (path relative to the plugin, COMMANDS line, anchor line to insert the
# COMMANDS line after). Session W's listing rows name the commands folder, so
# every command added after the 825-row baseline must be absent while A..W
# record. inspect-resource is restored before session X; console (which also
# wires a Logger into editor_plugin.gd), set_parent and eval are restored
# together before session Y.
STAGED_COMMANDS = [
    (
        os.path.join("commands", "inspect_resource.gd"),
        '\t"inspect_resource": preload("commands/inspect_resource.gd"),',
        '\t"list_resources": preload("commands/list_resources.gd"),',
    ),
    (
        os.path.join("commands", "set_parent.gd"),
        '\t"set_parent": preload("commands/set_parent.gd"),',
        '\t"console": preload("commands/console.gd"),',
    ),
    (
        os.path.join("commands", "eval.gd"),
        '\t"eval": preload("commands/eval.gd"),',
        '\t"set_parent": preload("commands/set_parent.gd"),',
    ),
    (
        os.path.join("commands", "attach_script.gd"),
        '\t"attach_script": preload("commands/attach_script.gd"),',
        '\t"eval": preload("commands/eval.gd"),',
    ),
]

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

# Connect-signal fixtures. Sessions A, B and C never reference these paths, so
# their rows cannot change. connect.tscn is a fresh scene per run: main()
# rewrites it, so session D always starts from the same bytes.
CONNECT_FIXTURE_GD = """extends Node

signal ping(value)
signal plain()


func on_ping(value):
	pass


func on_plain():
	pass
"""

CONNECT_NOMETHOD_GD = """extends Node
"""

CONNECT_SUB_GD = """extends Node

signal sub_ping(value)


func sub_on_ping(value):
	pass
"""

CONNECT_SUB_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp024connectsub1"]
[ext_resource type="Script" path="res://connect_sub.gd" id="1"]
[node name="SubRoot" type="Node"]
[node name="SubSource" type="Node" parent="."]
script = ExtResource("1")
[node name="SubTarget" type="Node" parent="."]
script = ExtResource("1")
[connection signal="sub_ping" from="SubSource" to="SubTarget" method="sub_on_ping"]
"""

CONNECT_MID_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp024connectmid1"]
[ext_resource type="PackedScene" path="res://connect_sub.tscn" id="1"]
[node name="MidRoot" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
"""

CONNECT_TSCN = """[gd_scene load_steps=6 format=3 uid="uid://gp024connect01"]
[ext_resource type="Script" path="res://connect_fixture.gd" id="1"]
[ext_resource type="Script" path="res://connect_nomethod.gd" id="2"]
[ext_resource type="PackedScene" path="res://connect_sub.tscn" id="3"]
[ext_resource type="PackedScene" path="res://connect_mid.tscn" id="4"]
[node name="ConnectRoot" type="Node"]
script = ExtResource("1")
[node name="Source" type="Node" parent="."]
script = ExtResource("1")
[node name="Target" type="Node" parent="."]
script = ExtResource("1")
[node name="Target2" type="Node" parent="."]
script = ExtResource("1")
[node name="Target3" type="Node" parent="."]
script = ExtResource("1")
[node name="NoMethod" type="Node" parent="."]
script = ExtResource("2")
[node name="Bare" type="Node" parent="."]
[node name="Sub" parent="." instance=ExtResource("3")]
[node name="Edit" parent="." instance=ExtResource("3")]
[editable path="Edit"]
[node name="Mid" parent="." instance=ExtResource("4")]
[editable path="Mid"]
"""

# Set-group fixtures. Sessions A-F never reference these paths, so their rows
# cannot change. group.tscn is written fresh by main() every run, so the
# set-group sessions always start from the same bytes.
GROUP_SUB_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp026groupsub1"]
[node name="SubRoot" type="Node" groups=["subroot"]]
[node name="Inner" type="Node" parent="." groups=["inner_g"]]
"""

GROUP_DEEP_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp026groupdeep1"]
[node name="DeepRoot" type="Node"]
[node name="DeepChild" type="Node" parent="."]
"""

GROUP_MID_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp026groupmid1"]
[ext_resource type="PackedScene" path="res://group_deep.tscn" id="1"]
[node name="MidRoot" type="Node"]
[node name="Deep" parent="." instance=ExtResource("1")]
"""

# Root carries no groups. Child3 starts with "beta" so an accepted removal has
# a persistent group to remove; Child2 starts with "alpha" so adding it is an
# add of an existing local group. Sub is an instance root (Editable Children
# off) whose source defines "subroot" and "inner_g"; Edit is the same scene
# with Editable Children on; Mid is an instance with only Mid editable, so
# Mid/Deep/DeepChild sits under a nested instance that is not editable.
GROUP_TSCN = """[gd_scene load_steps=3 format=3 uid="uid://gp026group01"]
[ext_resource type="PackedScene" path="res://group_sub.tscn" id="1"]
[ext_resource type="PackedScene" path="res://group_mid.tscn" id="2"]
[node name="GroupRoot" type="Node"]
[node name="Child" type="Node" parent="."]
[node name="Child2" type="Node" parent="." groups=["alpha"]]
[node name="Child3" type="Node" parent="." groups=["beta"]]
[node name="Ctl" type="Control" parent="."]
[node name="Sub" parent="." instance=ExtResource("1")]
[node name="Edit" parent="." instance=ExtResource("1")]
[editable path="Edit"]
[node name="Mid" parent="." instance=ExtResource("2")]
[editable path="Mid"]
"""

# Set-unique-name fixtures. Sessions A-I never reference these paths, so their
# rows cannot change. unique.tscn is written fresh by main() every run.
UNIQUE_SUB_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp028uniquesub1"]
[node name="SubRoot" type="Node"]
[node name="Inner" type="Node" parent="."]
[node name="Inherited" type="Node" parent="."]
unique_name_in_owner = true
"""

UNIQUE_DEEP_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp028uniquedeep1"]
[node name="DeepRoot" type="Node"]
[node name="DeepChild" type="Node" parent="."]
unique_name_in_owner = true
"""

UNIQUE_MID_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp028uniquemid1"]
[ext_resource type="PackedScene" path="res://unique_deep.tscn" id="1"]
[node name="MidRoot" type="Node"]
[node name="Deep" parent="." instance=ExtResource("1")]
"""

# Already holds a local flag, so an accepted removal has one to clear; P1/Dup
# holds a local flag that collides with P2/Dup; Sub is an instance root with
# Editable Children off; Edit is the same scene editable, so Edit/Inherited
# inherits its flag and Edit/Inner can take a local one; Mid/Deep is a nested
# instance with both ancestors editable, so Mid/Deep/DeepChild inherits a flag
# whose origin the packed route cannot read in one step.
UNIQUE_TSCN = """[gd_scene load_steps=3 format=3 uid="uid://gp028unique01"]
[ext_resource type="PackedScene" path="res://unique_sub.tscn" id="1"]
[ext_resource type="PackedScene" path="res://unique_mid.tscn" id="2"]
[node name="UniqueRoot" type="Node"]
[node name="Child" type="Node" parent="."]
[node name="Plain" type="Node" parent="."]
[node name="Already" type="Node" parent="."]
unique_name_in_owner = true
[node name="P1" type="Node" parent="."]
[node name="Dup" type="Node" parent="P1"]
unique_name_in_owner = true
[node name="P2" type="Node" parent="."]
[node name="Dup" type="Node" parent="P2"]
[node name="Sub" parent="." instance=ExtResource("1")]
[node name="Edit" parent="." instance=ExtResource("1")]
[editable path="Edit"]
[node name="Mid" parent="." instance=ExtResource("2")]
[editable path="Mid"]
[editable path="Mid/Deep"]
"""

# Rename/unique-flag fixtures, one scene per Gate 1 case. Sessions A-L never
# reference these paths, so their rows cannot change. Each scene holds a
# unique target named T under P and, in the colliding cases, a unique claimant
# named N under Q; the requested new name is always "N".
RENAME_SUB_TSCN = """[gd_scene format=3 uid="uid://gp029renamesub1"]
[node name="SubRoot" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_SUB_PLAIN_TSCN = """[gd_scene format=3 uid="uid://gp029renamesub2"]
[node name="SubRoot" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
"""

RENAME_R1_TSCN = """[gd_scene format=3 uid="uid://gp029renamer1"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_R2_TSCN = """[gd_scene format=3 uid="uid://gp029renamer2"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_R3_TSCN = """[gd_scene format=3 uid="uid://gp029renamer3"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
"""

RENAME_R4_TSCN = """[gd_scene format=3 uid="uid://gp029renamer4"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N" type="Node" parent="P"]
unique_name_in_owner = true
"""

RENAME_R5_TSCN = """[gd_scene format=3 uid="uid://gp029renamer5"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_R6_TSCN = """[gd_scene format=3 uid="uid://gp029renamer6"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="T" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="N2" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_R7_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp029renamer7"]
[ext_resource type="PackedScene" path="res://rename_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

RENAME_R8_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp029renamer8"]
[ext_resource type="PackedScene" path="res://rename_sub_plain.tscn" id="1"]
[node name="Root" type="Node"]
[node name="N" type="Node" parent="."]
unique_name_in_owner = true
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

RENAME_R9_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp029renamer9"]
[ext_resource type="PackedScene" path="res://rename_sub_plain.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
unique_name_in_owner = true
[node name="Sub" parent="." instance=ExtResource("1")]
unique_name_in_owner = true
"""

RENAME_R10_TSCN = """[gd_scene format=3 uid="uid://gp029renamer10"]
[node name="Root" type="Node"]
"""

# Option C fixtures, one scene per check. Case 1 (no sibling holds the
# requested name) tests the exact name; Case 2 (a sibling holds it) tests the
# stem plus digits, a conservative superset of the names the engine can apply.
RENAME_CASE1_REJECT_TSCN = """[gd_scene format=3 uid="uid://gp029c1reject"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
[node name="Q" type="Node" parent="."]
[node name="N" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_CASE1_ACCEPT_TSCN = """[gd_scene format=3 uid="uid://gp029c1accept"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
"""

RENAME_STEM_REJECT_TSCN = """[gd_scene format=3 uid="uid://gp029stemreject"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="N2" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_STEM_ACCEPT_TSCN = """[gd_scene format=3 uid="uid://gp029stemaccept"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N" type="Node" parent="P"]
"""

RENAME_PAD_REJECT_TSCN = """[gd_scene format=3 uid="uid://gp029padreject"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N01" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="N02" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_PAD_ACCEPT_TSCN = """[gd_scene format=3 uid="uid://gp029padaccept"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N01" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="K" type="Node" parent="Q"]
unique_name_in_owner = true
"""

RENAME_CONSERVATIVE_TSCN = """[gd_scene format=3 uid="uid://gp029conservative"]
[node name="Root" type="Node"]
[node name="P" type="Node" parent="."]
[node name="Old" type="Node" parent="P"]
unique_name_in_owner = true
[node name="N" type="Node" parent="P"]
[node name="Q" type="Node" parent="."]
[node name="N7" type="Node" parent="Q"]
unique_name_in_owner = true
"""

# Inherited-rename fixtures. One sub-scene S with root R, child A and
# grandchild B; a unique variant for i9; a sub-scene T that instances S for the
# nested case. Each outer scene is a separate file so a saved accepted rename
# never changes a later case's fixture. Sessions A-Q never reference them.
INHERIT_SUB_TSCN = """[gd_scene format=3 uid="uid://gp030ihsub1"]
[node name="R" type="Node"]
[node name="A" type="Node" parent="."]
[node name="B" type="Node" parent="A"]
"""

INHERIT_SUB_U_TSCN = """[gd_scene format=3 uid="uid://gp030ihsub2"]
[node name="R" type="Node"]
[node name="A" type="Node" parent="."]
unique_name_in_owner = true
[node name="B" type="Node" parent="A"]
"""

INHERIT_T_TSCN = """[gd_scene format=3 uid="uid://gp030iht1"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="TRoot" type="Node"]
[node name="S" parent="." instance=ExtResource("1")]
"""

# i1/i6: outer scene with the S instance, Editable Children off.
# (The per-case files below repeat the same shapes with per-case uids.)

# One editable-instance outer scene per replay case, each with its own uid so
# a saved accepted rename never changes a later case's fixture bytes.
INHERIT_I1_TSCN = """[gd_scene format=3 uid="uid://gp030ihi01"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
"""

INHERIT_I2_TSCN = """[gd_scene format=3 uid="uid://gp030ihi02"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

INHERIT_I3_TSCN = """[gd_scene format=3 uid="uid://gp030ihi03"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

INHERIT_I4_TSCN = """[gd_scene format=3 uid="uid://gp030ihi04"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

INHERIT_I5_TSCN = """[gd_scene format=3 uid="uid://gp030ihi05"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
[node name="L" type="Node" parent="Sub/A"]
owner="."
"""

INHERIT_I6_TSCN = """[gd_scene format=3 uid="uid://gp030ihi06"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
"""

INHERIT_I7_TSCN = """[gd_scene format=3 uid="uid://gp030ihi07"]
[ext_resource type="PackedScene" path="res://inherit_t.tscn" id="1"]
[node name="Root" type="Node"]
[node name="TInst" parent="." instance=ExtResource("1")]
[editable path="TInst"]
[editable path="TInst/S"]
[node name="L2" type="Node" parent="TInst/S/A"]
owner="."
"""

INHERIT_I8_TSCN = """[gd_scene format=3 uid="uid://gp030ihi08"]
[ext_resource type="PackedScene" path="res://inherit_sub.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

INHERIT_I9_TSCN = """[gd_scene format=3 uid="uid://gp030ihi09"]
[ext_resource type="PackedScene" path="res://inherit_sub_u.tscn" id="1"]
[node name="Root" type="Node"]
[node name="Sub" parent="." instance=ExtResource("1")]
[editable path="Sub"]
"""

# Instantiate-scene fixtures. Sessions A-S never reference these paths, so
# their rows cannot change. inst_base.tscn is written fresh by main() every
# run. inst_sub.tscn is S (root R, child A, grandchild B, one unique node);
# inst_of_sub.tscn contains an instance of S; inst_e.tscn is the edited scene
# for the cycle cases (inst_t.tscn instances it, inst_derived_e.tscn inherits
# from it); inst_missing_dep.tscn instances a file that does not exist.
INST_PLAIN_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp031instplain1"]
[node name="PlainRoot" type="Node"]
[node name="Kid" type="Node" parent="."]
"""

INST_SUB_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp031instsub1"]
[node name="R" type="Node"]
[node name="A" type="Node2D" parent="."]
[node name="B" type="Node" parent="A"]
[node name="U" type="Node" parent="."]
unique_name_in_owner = true
"""

INST_CONTROL_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp031instctl1"]
[node name="CtlRoot" type="Control"]
[node name="Btn" type="Button" parent="."]
"""

INST_OF_SUB_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp031instofsub1"]
[ext_resource type="PackedScene" path="res://inst_sub.tscn" id="1"]
[node name="OfSubRoot" type="Node"]
[node name="Inner" parent="." instance=ExtResource("1")]
"""

INST_E_TSCN = """[gd_scene load_steps=1 format=3 uid="uid://gp031inste1"]
[node name="ERoot" type="Node"]
[node name="X" type="Node" parent="."]
"""

# The ext_resource lines carry the target's uid, the way the editor writes
# them when a scene is saved, so ResourceLoader.get_dependencies returns the
# "uid://...::::res://..." form and the cycle walk must strip it.
INST_T_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp031instt1"]
[ext_resource type="PackedScene" uid="uid://gp031inste1" path="res://inst_e.tscn" id="1"]
[node name="TRoot" type="Node"]
[node name="Kid" parent="." instance=ExtResource("1")]
"""

INST_DERIVED_E_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp031instde1"]
[ext_resource type="PackedScene" uid="uid://gp031inste1" path="res://inst_e.tscn" id="1_base"]
[node name="ERoot" instance=ExtResource("1_base")]
"""

INST_MISSING_DEP_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp031instmd1"]
[ext_resource type="PackedScene" path="res://inst_absent.tscn" id="1"]
[node name="MissingRoot" type="Node"]
[node name="Kid" parent="." instance=ExtResource("1")]
"""

INST_BROKEN_TSCN = """this is not a valid scene at all
"""

INST_NOTE_TXT = """session fixture plain text, not a scene
"""

INST_SCRIPT_GD = """extends Node
"""

INST_BASE_TSCN = """[gd_scene load_steps=3 format=3 uid="uid://gp031instbase1"]
[ext_resource type="PackedScene" path="res://inst_sub.tscn" id="1"]
[ext_resource type="PackedScene" path="res://inst_of_sub.tscn" id="2"]
[node name="InstRoot" type="Node"]
[node name="Plain" type="Node" parent="."]
[node name="SubOff" parent="." instance=ExtResource("1")]
[node name="SubOn" parent="." instance=ExtResource("1")]
[editable path="SubOn"]
[node name="LocalUnderOn" type="Node" parent="SubOn"]
owner="."
[node name="OfSub" parent="." instance=ExtResource("2")]
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


def cs(source, signal, target, method, project="<PROJ>", deferred=False, one_shot=False):
    d = {"command": "connect_signal", "source_path": source, "signal": signal,
         "target_path": target, "method": method, "deferred": deferred,
         "one_shot": one_shot, "project_path": project}
    return ("line", json.dumps(d, separators=(",", ":")))


def build_e():
    # Session E: connect-signal rejections that need no edited scene. It runs
    # before session C, while the editor still has no scene open.
    return [
        ("line", '{"command":"status"}'),
        cs("Source", "ping", "Target", "on_ping"),
        cs("Source", "ping", "Target", "on_ping", project="/no/replay-mismatch"),
        ("line", '{"command":"connect_signal"}'),
        ("line", '{"command":"connect_signal","source_path":7,"signal":"ping","target_path":"Target","method":"on_ping","project_path":"<PROJ>"}'),
        ("line", '{"command":"connect_signal","source_path":"Source","signal":"ping","target_path":"Target","method":"on_ping","deferred":"yes","one_shot":false,"project_path":"<PROJ>"}'),
    ]


def build_d():
    # Session D: connect-signal against connect.tscn. Accepted requests run
    # first; every rejection is bracketed by scene_tree snapshots that must be
    # identical, so a rejection that changed the scene would show up.
    accepted = [
        cs("Source", "ping", "Target", "on_ping"),
        cs("Source", "ping", ".", "on_ping"),
        cs(".", "ping", "Target2", "on_ping"),
        cs("Source", "plain", "Target3", "on_plain", deferred=True),
        cs("Source", "ping", "Target3", "on_ping", one_shot=True),
        cs("Edit/SubSource", "sub_ping", "Target", "on_ping"),
        cs("Source", "ping", "Sub/SubTarget", "sub_on_ping"),
    ]
    rejections = [
        cs("Source", "ping", "Target", "on_ping"),
        cs("NoSource", "ping", "Target", "on_ping"),
        cs("Source", "ping", "NoTarget", "on_ping"),
        cs("Source", "nope", "Target", "on_ping"),
        cs("Source", "ping", "Target", "nope"),
        cs("Source", "ping", "Bare", "on_ping"),
        cs("Sub/SubSource", "sub_ping", "Target", "on_ping"),
        cs("Mid/Sub/SubSource", "sub_ping", "Target", "on_ping"),
        cs("Sub/SubSource", "sub_ping", "Sub/SubTarget", "sub_on_ping"),
        cs("Source", "ping", "Target2", "on_ping", project="/no/replay-mismatch"),
        ("line", '{"command":"connect_signal","source_path":"Source","signal":"ping","target_path":"Target2","method":"on_ping","deferred":1,"one_shot":false,"project_path":"<PROJ>"}'),
    ]
    out = [("line", '{"command":"status"}'), ("file_sha256", "connect.tscn")]
    out.extend(accepted)
    for rejection in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(rejection)
        out.append(("line", '{"command":"scene_tree"}'))
    # Nothing is saved implicitly: the file bytes are unchanged since the
    # session opened, and no connection line exists yet.
    out.append(("file_sha256", "connect.tscn"))
    out.append(("file_not_contains", "connect.tscn|[connection"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'connect.tscn|[connection signal="ping" from="Source" to="Target" method="on_ping"]'))
    out.append(("file_contains", 'connect.tscn|[connection signal="plain" from="Source" to="Target3" method="on_plain" flags=3]'))
    out.append(("file_contains", 'connect.tscn|[connection signal="ping" from="Source" to="Target3" method="on_ping" flags=6]'))
    out.append(("file_contains", 'connect.tscn|[connection signal="sub_ping" from="Edit/SubSource" to="Target" method="on_ping"]'))
    out.append(("file_contains", 'connect.tscn|[connection signal="ping" from="Source" to="Sub/SubTarget" method="sub_on_ping"]'))
    return out


def build_f():
    # Fresh editor process on the saved connect.tscn: four of the seven pairs
    # accepted in session D are reconnected here and must be duplicates, which
    # proves those connections survived the save and reload: Source.ping ->
    # Target.on_ping; Source.ping -> "."; Source.ping ->
    # Sub/SubTarget.sub_on_ping; Edit/SubSource.sub_ping -> Target.on_ping.
    # The root-source, --deferred and --one-shot pairs are not rechecked.
    return [
        ("line", '{"command":"status"}'),
        cs("Source", "ping", "Target", "on_ping"),
        cs("Source", "ping", ".", "on_ping"),
        cs("Source", "ping", "Sub/SubTarget", "sub_on_ping"),
        cs("Edit/SubSource", "sub_ping", "Target", "on_ping"),
        ("line", '{"command":"scene_tree"}'),
    ]


def sg(node, group, remove=False, project="<PROJ>"):
    d = {"command": "set_group", "node_path": node, "group": group,
         "remove": remove, "project_path": project}
    return ("line", json.dumps(d, separators=(",", ":")))


def qg(group):
    return ("line", json.dumps({"command": "query_nodes", "project_path": "<PROJ>",
                                "class": None, "group": group, "name": None,
                                "limit": 100}, separators=(",", ":")))


def build_g():
    # Session G: set-group rejections that need no edited scene. It runs before
    # session C, while the editor still has no scene open.
    return [
        ("line", '{"command":"status"}'),
        sg("Child", "x"),
        sg("Child", "x", project="/no/replay-mismatch"),
        ("line", '{"command":"set_group"}'),
        ("line", '{"command":"set_group","node_path":7,"group":"x","remove":false,"project_path":"<PROJ>"}'),
        ("line", '{"command":"set_group","node_path":"Child","group":7,"remove":false,"project_path":"<PROJ>"}'),
        ("line", '{"command":"set_group","node_path":"Child","group":"x","remove":"yes","project_path":"<PROJ>"}'),
    ]


def _bracket(out, group, request):
    # scene_tree plus a group query before and after a rejection. scene_tree
    # alone carries no groups, so the query is what proves the membership did
    # not change; an empty group name means "no group filter" and lists every
    # node in the scene.
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(qg(group))
    out.append(request)
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(qg(group))


def build_h():
    # Session H: set-group against group.tscn. Accepted requests run first,
    # then each rejection is bracketed by identical scene_tree and group-query
    # snapshots. Nothing is saved until save_scene.
    accepted = [
        sg("Child", "plain_add"),
        sg("Child3", "beta", remove=True),
        sg(".", "root_add"),
        sg("Sub", "sub_add"),
        sg("Edit/Inner", "edit_add"),
        sg("Child3", "_under"),
        sg("Child3", "with space"),
        sg("Child3", "\u00fcn\u00efc\u00f6d\u00e9"),
        sg("Child3", "coexist"),
    ]
    rejections = [
        ("x", sg("NoSuch", "x")),
        ("", sg("Child", "")),
        ("alpha", sg("Child2", "alpha")),
        ("subroot", sg("Sub", "subroot")),
        ("nope", sg("Child", "nope", remove=True)),
        ("subroot", sg("Sub", "subroot", remove=True)),
        ("x", sg("Sub/Inner", "x")),
        ("x", sg("Mid/Deep/DeepChild", "x")),
        ("x", sg("Child", "x", project="/no/replay-mismatch")),
        # An escaped NUL in the wire JSON is parsed to U+FFFD, so these two
        # requests carry the replacement character after parsing; the command
        # must reject them for an add and a remove.
        ("x\u0000y", sg("Child", "x\u0000y")),
        ("x\u0000y", sg("Child", "x\u0000y", remove=True)),
    ]
    out = [("line", '{"command":"status"}'), ("file_sha256", "group.tscn")]
    out.extend(accepted)
    for group, request in rejections:
        _bracket(out, group, request)
    # No implicit save: the file is unchanged since the session opened, and no
    # accepted group has been written yet.
    out.append(("file_sha256", "group.tscn"))
    out.append(("file_not_contains", 'group.tscn|groups=["plain_add"]'))
    # The post-save file is not hashed: the save adds a generated unique_id to
    # every node line, so its bytes are not deterministic. The write itself is
    # proven by the file_not_contains before the save and the file_contains
    # checks after it.
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'group.tscn|groups=["root_add"]'))
    out.append(("file_contains", 'group.tscn|groups=["plain_add"]'))
    out.append(("file_contains", 'group.tscn|groups=["sub_add"] instance=ExtResource("1")'))
    out.append(("file_contains", 'group.tscn|groups=["edit_add"]'))
    out.append(("file_contains", 'group.tscn|groups=["_under", "coexist", "with space", "\u00fcn\u00efc\u00f6d\u00e9"]'))
    out.append(("file_contains", 'group.tscn|groups=["alpha"]'))
    out.append(("file_not_contains", 'group.tscn|groups=["beta"]'))
    out.append(("file_not_contains", 'group.tscn|groups=["nope"]'))
    return out


def build_i():
    # Session I: a fresh editor on the saved group.tscn. The group queries must
    # list the persisted members, "beta" must be gone, and adding a persisted
    # group again must still be rejected as a duplicate.
    out = [("line", '{"command":"status"}')]
    for group in ["plain_add", "root_add", "sub_add", "edit_add", "_under",
                  "coexist", "with space", "\u00fcn\u00efc\u00f6d\u00e9", "alpha", "subroot"]:
        out.append(qg(group))
    for group in ["beta", "nope", "x"]:
        out.append(qg(group))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(sg("Child", "plain_add"))
    return out


def un(node, remove=False, project="<PROJ>"):
    d = {"command": "set_unique_name", "node_path": node, "remove": remove,
         "project_path": project}
    return ("line", json.dumps(d, separators=(",", ":")))


def ins(node, project="<PROJ>"):
    return ("line", json.dumps({"command": "inspect_node", "node_path": node,
                                "project_path": project}, separators=(",", ":")))


def _bracket_u(out, node, request):
    # inspect_node of the target before and after a rejection. It reports the
    # unique_name_in_owner property value, so a changed flag would show; the
    # two replies must be identical.
    out.append(ins(node))
    out.append(request)
    out.append(ins(node))


def build_j():
    # Session J: set-unique-name rejections that need no edited scene. It runs
    # before session C, while the editor still has no scene open.
    return [
        ("line", '{"command":"status"}'),
        un("Child"),
        un("Child", project="/no/replay-mismatch"),
        ("line", '{"command":"set_unique_name"}'),
        ("line", '{"command":"set_unique_name","node_path":7,"remove":false,"project_path":"<PROJ>"}'),
        ("line", '{"command":"set_unique_name","node_path":"Child","remove":"yes","project_path":"<PROJ>"}'),
    ]


def build_k():
    # Session K: set-unique-name against unique.tscn. Accepted requests run
    # first, then each rejection is bracketed by identical inspect_node
    # snapshots. Nothing is saved until save_scene.
    accepted = [
        un("Child"),
        un("Sub"),
        un("Edit/Inner"),
        un("Already", remove=True),
    ]
    rejections = [
        (".", un(".")),
        ("Sub/Inner", un("Sub/Inner")),
        ("P1/Dup", un("P1/Dup")),
        ("Edit/Inherited", un("Edit/Inherited")),
        ("Plain", un("Plain", remove=True)),
        ("Edit/Inherited", un("Edit/Inherited", remove=True)),
        ("Mid/Deep/DeepChild", un("Mid/Deep/DeepChild", remove=True)),
        ("P2/Dup", un("P2/Dup")),
        ("Plain", un("Plain", project="/no/replay-mismatch")),
    ]
    out = [("line", '{"command":"status"}'), ("file_sha256", "unique.tscn")]
    out.extend(accepted)
    for node, request in rejections:
        _bracket_u(out, node, request)
    # No implicit save: the file is unchanged since the session opened.
    out.append(("file_sha256", "unique.tscn"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    # The save writes at least one flag line. The per-node state is read back
    # by session L, because a node line carries a generated unique_id.
    out.append(("file_contains", 'unique.tscn|unique_name_in_owner = true'))
    return out


def build_l():
    # Session L: a fresh editor on the saved unique.tscn. inspect_node reports
    # unique_name_in_owner for each node: Child, Sub and Edit/Inner must be
    # true; Plain and P2/Dup must be false; Already must be false after the
    # accepted removal; P1/Dup stays true. Re-adding a persisted flag must
    # still be rejected as a no-op.
    out = [("line", '{"command":"status"}')]
    for node in ["Child", "Plain", "Already", "Sub", "Edit/Inner", "P1/Dup", "P2/Dup"]:
        out.append(ins(node))
    out.append(un("Child"))
    return out


def rn(node, new_name, project="<PROJ>"):
    d = {"command": "rename_node", "node_path": node, "new_name": new_name,
         "project_path": project}
    return ("line", json.dumps(d, separators=(",", ":")))


def _bracket_r(out, node, request):
    # scene_tree plus inspect_node of the target before and after a rejected
    # rename; the two snapshots must be identical.
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(ins(node))
    out.append(request)
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(ins(node))


def build_n():
    # Session N: one editor, one scene per Gate 1 case. Each rejection is
    # bracketed by identical scene_tree and inspect_node snapshots and must
    # leave the file unchanged; each accepted rename is followed by save_scene.
    out = [("line", '{"command":"status"}')]
    rejections = [
        ("r1", "P/T", rn("P/T", "N")),          # unique claimant in the same owner scope
        ("r6", "P/T", rn("P/T", "N")),          # sibling N forces N2, which Q/N2 holds
        ("r7", "Sub/P/T", rn("Sub/P/T", "N")),  # claimant in the instance scope
        ("r9", "Sub", rn("Sub", "N")),          # instance-root node renamed onto a claimed name
    ]
    for case, node, request in rejections:
        out.append(("file_sha256", "rename_%s.tscn" % case))
        out.append(os_("res://rename_%s.tscn" % case))
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(ins(node))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(ins(node))
        out.append(("file_sha256", "rename_%s.tscn" % case))
    accepted = [
        ("r2", "P/T"),       # target not unique: never rejected
        ("r3", "P/T"),       # unique, no sibling clash, no claimant
        ("r4", "P/T"),       # unique sibling N: the engine applies N2
        ("r5", "P/T"),       # non-unique sibling N: the engine applies N2
        ("r8", "Sub/P/T"),   # claimant only in the outer scope
        ("r10", "."),        # the scene root, which has no owner
    ]
    for case, node in accepted:
        out.append(os_("res://rename_%s.tscn" % case))
        out.append(rn(node, "N"))
        out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    return out


def build_o():
    # Session O: a fresh editor on each saved accepted scene, to read back the
    # applied name and the flag. The rejected scenes are covered by the
    # identical snapshots in session N. The inner rename in r8 is read at its
    # original path: the engine did not persist that rename for this action.
    out = [("line", '{"command":"status"}')]
    for case, node in [("r2", "P/N"), ("r3", "P/N"), ("r4", "P/N2"),
                       ("r5", "P/N2"), ("r8", "Sub/P/T"), ("r10", ".")]:
        out.append(os_("res://rename_%s.tscn" % case))
        out.append(ins(node))
    return out


def build_p():
    # Session P: the option C rename cases. Rejections are bracketed by
    # identical scene_tree and inspect_node snapshots with an equal before and
    # after file hash; accepted renames are saved. Case 1 tests the exact
    # requested name; Case 2 tests the stem plus digits (conservative), so the
    # stem-reject and conservative scenes reject although the engine would
    # apply N2, and the pad-accept scene accepts because no unique node is the
    # stem plus digits.
    out = [("line", '{"command":"status"}')]
    rejections = [
        ("rename_case1_reject.tscn", "P/Old", "N"),
        ("rename_stem_reject.tscn", "P/Old", "N"),
        ("rename_pad_reject.tscn", "P/Old", "N01"),
        ("rename_conservative.tscn", "P/Old", "N"),
    ]
    for scene, node, requested in rejections:
        out.append(("file_sha256", scene))
        out.append(os_("res://%s" % scene))
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(ins(node))
        out.append(rn(node, requested))
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(ins(node))
        out.append(("file_sha256", scene))
    accepted = [
        ("rename_case1_accept.tscn", "P/Old", "N"),
        ("rename_stem_accept.tscn", "P/Old", "N"),
        ("rename_pad_accept.tscn", "P/Old", "N01"),
    ]
    for scene, node, requested in accepted:
        out.append(os_("res://%s" % scene))
        out.append(rn(node, requested))
        out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    return out


def build_q():
    # Session Q: a fresh editor on the saved accepted scenes and the rejected
    # ones. The accepted renames applied N, N2 and N02 and kept the flag; the
    # rejected scenes are unchanged.
    out = [("line", '{"command":"status"}')]
    for scene, node in [("rename_case1_accept.tscn", "P/N"),
                        ("rename_stem_accept.tscn", "P/N2"),
                        ("rename_pad_accept.tscn", "P/N02"),
                        ("rename_case1_reject.tscn", "P/Old"),
                        ("rename_conservative.tscn", "P/Old")]:
        out.append(os_("res://%s" % scene))
        out.append(ins(node))
    return out


def build_r():
    # Session R: the inherited-rename cases, one scene per case. Rejections
    # (an inherited node inside an editable or non-editable
    # instance) are bracketed by identical scene_tree and inspect_node
    # snapshots with an equal before and after file hash; the accepted outer
    # renames (instance root, outer-owned local node) are saved.
    out = [("line", '{"command":"status"}')]
    rejections = [
        ("inherit_i3.tscn", "Sub/A", "ANew"),        # inherited child, editable on
        ("inherit_i4.tscn", "Sub/A/B", "BNew"),      # inherited grandchild, editable on
        ("inherit_i6.tscn", "Sub/A", "AOff"),        # inherited child, editable off
        ("inherit_i7.tscn", "TInst/S", "SNest"),     # nested instance root
        ("inherit_i7.tscn", "TInst/S/A", "ANest"),   # nested inner A
        ("inherit_i8.tscn", "Sub/A", "A"),           # rename back to its own name
        ("inherit_i8.tscn", "Sub/A", "X1"),          # twice in a row, first
        ("inherit_i8.tscn", "Sub/A", "X2"),          # twice in a row, second
        ("inherit_i9.tscn", "Sub/A", "AZed"),        # inherited unique node
    ]
    for scene, node, requested in rejections:
        out.append(("file_sha256", scene))
        out.append(os_("res://%s" % scene))
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(ins(node))
        out.append(rn(node, requested))
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(ins(node))
        out.append(("file_sha256", scene))
    # Wrong project on an inherited target: the project guard answers first.
    out.append(("file_sha256", "inherit_i3.tscn"))
    out.append(os_("res://inherit_i3.tscn"))
    out.append(rn("Sub/A", "XWrong", project="/no/replay-mismatch"))
    out.append(("file_sha256", "inherit_i3.tscn"))
    accepted = [
        ("inherit_i1.tscn", "Sub", "SubX"),          # instance root, editable off
        ("inherit_i2.tscn", "Sub", "SubX"),          # instance root, editable on
        ("inherit_i5.tscn", "Sub/A/L", "LNew"),      # outer-owned local node
        ("inherit_i7.tscn", "TInst/S/A/L2", "L2Nest"),  # outer-owned local in nested
    ]
    for scene, node, requested in accepted:
        out.append(os_("res://%s" % scene))
        out.append(rn(node, requested))
        out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    return out


def build_s():
    # Session S: a fresh editor on each saved accepted scene, to read back the
    # applied name after reload. The rejected scenes are unchanged (covered by
    # the identical snapshots in session R); the unique inherited node in i9 is
    # read to confirm its name and flag are untouched.
    out = [("line", '{"command":"status"}')]
    for scene, node in [("inherit_i1.tscn", "SubX"),
                        ("inherit_i2.tscn", "SubX"),
                        ("inherit_i5.tscn", "Sub/A/LNew"),
                        ("inherit_i7.tscn", "TInst/S/A/L2Nest"),
                        ("inherit_i9.tscn", "Sub/A")]:
        out.append(os_("res://%s" % scene))
        out.append(ins(node))
    return out


def isc(scene, parent, project="<PROJ>"):
    d = {"command": "instantiate_scene", "scene_path": scene,
         "parent_path": parent, "project_path": project}
    return ("line", json.dumps(d, separators=(",", ":")))


def build_t():
    # Session T: instantiate-scene rejections that need no edited scene. It runs
    # before session C, while the editor still has no scene open.
    return [
        ("line", '{"command":"status"}'),
        isc("res://inst_plain.tscn", "."),
        isc("res://inst_plain.tscn", ".", project="/no/replay-mismatch"),
        ("line", '{"command":"instantiate_scene"}'),
        ("line", '{"command":"instantiate_scene","scene_path":7,"parent_path":".","project_path":"<PROJ>"}'),
        ("line", '{"command":"instantiate_scene","scene_path":"res://inst_plain.tscn","parent_path":7,"project_path":"<PROJ>"}'),
    ]


def build_u():
    # Session U: instantiate-scene against inst_base.tscn. Accepted requests run
    # first, then each rejection is bracketed by identical scene_tree snapshots.
    # Nothing is saved until save_scene, so the file hash before and after the
    # rejections is equal. The cycle cases need a different edited scene, so
    # they run after the save, each bracketed by an equal before/after hash of
    # its own fixture.
    accepted = [
        isc("res://inst_plain.tscn", "."),
        isc("res://inst_sub.tscn", "."),
        isc("res://inst_sub.tscn", "."),                  # sibling collision -> R2
        isc("res://inst_sub.tscn", "SubOff"),             # instance root, Editable Children off
        isc("res://inst_sub.tscn", "SubOn"),              # instance root, Editable Children on
        isc("res://inst_sub.tscn", "SubOn/LocalUnderOn"),  # local node under an editable instance
        isc("res://inst_of_sub.tscn", "."),               # a scene that itself instances S
        isc("res://inst_control.tscn", "."),              # a Control root under a plain Node
    ]
    rejections = [
        isc("res://inst_sub.tscn", "SubOff/A"),           # inner node of a non-editable instance
        isc("res://inst_sub.tscn", "SubOn/A"),            # inner node of an editable instance
        isc("res://inst_sub.tscn", "NoSuch"),
        isc("res://inst_sub.tscn", ""),
        isc("res://inst_sub.tscn", "/InstRoot"),
        isc("res://inst_sub.tscn", "A:B"),
        isc("res://inst_sub.tscn", "../Plain"),
        isc("plain.tscn", "."),                           # not res://
        isc("res://x/../inst_plain.tscn", "."),           # ..
        isc("res://inst_note.txt", "."),                  # wrong extension
        isc("res://inst_script.gd", "."),                 # a .gd file
        isc("res://inst_absent.tscn", "."),               # missing file
        isc("res://inst_broken.tscn", "."),               # a file that fails to parse
        isc("res://inst_missing_dep.tscn", "."),          # a scene with a missing dependency
        isc("res://inst_base.tscn", "."),                 # self cycle
        isc("res://inst_plain.tscn", ".", project="/no/replay-mismatch"),
        isc("plain.tscn", "NoSuch", project="/no/replay-mismatch"),  # project guard first
    ]
    out = [("line", '{"command":"status"}'), ("file_sha256", "inst_base.tscn")]
    out.extend(accepted)
    for request in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
    # No implicit save: the file is unchanged since the session opened.
    out.append(("file_sha256", "inst_base.tscn"))
    out.append(("file_not_contains", 'inst_base.tscn|name="R2"'))
    # The post-save file is not hashed: the save adds a generated unique_id and
    # ext_resource id suffixes, so its bytes are not deterministic. The write is
    # proven by the file_not_contains before the save and the checks after it.
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'inst_base.tscn|name="R2" parent="."'))
    out.append(("file_contains", 'inst_base.tscn|name="PlainRoot" parent="."'))
    out.append(("file_contains", 'inst_base.tscn|name="R" parent="SubOff"'))
    out.append(("file_contains", 'inst_base.tscn|name="R" parent="SubOn/LocalUnderOn"'))
    out.append(("file_contains", 'inst_base.tscn|name="CtlRoot" parent="."'))
    out.append(("file_contains", 'inst_base.tscn|instance=ExtResource'))
    # Self cycle on a scene without instances.
    out.append(("file_sha256", "inst_plain.tscn"))
    out.append(os_("res://inst_plain.tscn"))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(isc("res://inst_plain.tscn", "."))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "inst_plain.tscn"))
    # Transitive cycle (inst_t instances the edited inst_e) and an inheritance
    # cycle (inst_derived_e inherits the edited inst_e).
    out.append(("file_sha256", "inst_e.tscn"))
    out.append(os_("res://inst_e.tscn"))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(isc("res://inst_t.tscn", "."))
    out.append(isc("res://inst_derived_e.tscn", "."))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "inst_e.tscn"))
    return out


def build_v():
    # Session V: a fresh editor on the saved inst_base.tscn. The accepted
    # instances must be present with their applied names after the reload, and
    # the saved file must carry an instance row for one of them.
    out = [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        q(),
        ins("R2"),
        ins("PlainRoot"),
        ins("SubOff/R"),
        ins("SubOn/LocalUnderOn/R"),
        ins("OfSubRoot"),
        ins("CtlRoot"),
        ("file_contains", 'inst_base.tscn|name="R2" parent="."'),
    ]
    return out


def lr(**kw):
    d = {"command": "list_resources", "project_path": "<PROJ>"}
    d.update(kw)
    return ("line", json.dumps(d, separators=(",", ":")))


def build_w():
    # Session W: list-resources (read-only, no edited scene). Runs before
    # session C like the other no-scene sessions. The listing covers the
    # fixture project and the copied plugin, both deterministic. The refresh
    # cases are not recorded: their reply depends on scan timing.
    return [
        ("line", '{"command":"status"}'),
        lr(limit=1000),
        lr(limit=1), lr(limit=2), lr(limit=1000),
        lr(limit=0), lr(limit=1001), lr(limit=1.5),
        ("line", '{"command":"list_resources","project_path":"<PROJ>","limit":"abc"}'),
        lr(type="GDScript", limit=1000),
        lr(type="PackedScene", limit=1000),
        lr(type="Resource", limit=1000),
        lr(type="StandardMaterial3D", limit=1000),
        lr(type="Node", limit=1000),
        lr(type="Script", limit=1000),
        lr(type="NoSuchClass999"),
        lr(type="QryScriptClass"),
        lr(type=""),
        lr(path_prefix="res://inst_", limit=1000),
        lr(path_prefix="res://addons/godot_pipeline/commands/", limit=1000),
        lr(path_prefix="not-res://"),
        lr(path_prefix="", limit=1000),
        lr(cursor="res://inst_plain.tscn", limit=2),
        lr(cursor="res://inst_plain.tscn", limit=1000),
        lr(cursor="res://sub.tscn", type="PackedScene", limit=1000),
        lr(cursor="res://inst_absent.tscn"),
        lr(cursor="res://no/such.tres"),
        lr(cursor="not-res://"),
        lr(path_prefix=7), lr(type=7), lr(cursor=7), lr(refresh="yes"),
        ("line", '{"command":"list_resources","project_path":7}'),
        ("line", '{"command":"list_resources","project_path":"/no/replay-mismatch","limit":0,"type":"NoSuchClass999","cursor":"nope","path_prefix":"nope","refresh":false}'),
        lr(refresh=False, limit=1000),
    ]


# Session X fixtures (inspect-resource). Each text is deterministic; a binary
# .res must be generated by ResourceSaver because a text-format .res does not
# load in 4.7.2 (measured). The generated bytes are recorded into the project
# by write_inspect_fixtures().

INSP_DATA_GD = """extends Resource
@export var name2: String = "rock"
@export var cost: int = 7
@export var scale: Vector3 = Vector3(1, 2, 3)
@export var tint: Color = Color(1, 0.5, 0, 1)
@export var sub: Resource
@export var other: Resource
@export var nothing: Resource
"""

INSP_OTHER_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_data.gd" id="1"]
[resource]
script = ExtResource("1")
name2 = "other-file"
"""

# Holds a script-backed resource with an embedded sub-resource (shown inline
# by inspect-resource) and an external reference (shown as {path,type}).
INSP_DATA_TRES = """[gd_resource type="Resource" load_steps=4 format=3]
[ext_resource type="Script" path="res://insp_data.gd" id="1"]
[ext_resource type="Resource" path="res://insp_other.tres" id="2"]
[sub_resource type="Resource" id="sub_inline"]
script = ExtResource("1")
name2 = "inline"
cost = 99
[resource]
script = ExtResource("1")
name2 = "rock"
cost = 7
scale = Vector3(1, 2, 3)
tint = Color(1, 0.5, 0, 1)
tags = PackedStringArray("a", "b")
sub = SubResource("sub_inline")
other = ExtResource("2")
"""

# A @tool resource script whose _init prints a marker line. The refusal path
# must never load it, so no marker appears (the marker assertion is a separate
# probe over the real editor output; the replay only records the refusal).
INSP_TOOL_GD = """@tool
extends Resource


func _init() -> void:
	print("MARKER_INSP_TOOL")
"""

INSP_TOOL_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_tool.gd" id="1"]
[resource]
script = ExtResource("1")
"""

# A script that extends the @tool script without being @tool itself. The walk
# follows the extends edge and finds the base's @tool annotation, so the
# resource is refused and the reply names the base script.
INSP_EXT_GD = """extends "res://insp_tool.gd"


func extra() -> void:
	pass
"""

INSP_EXT_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_ext.gd" id="1"]
[resource]
script = ExtResource("1")
"""

# The class_name inheritance form of the same extends case: a child script
# refers to the @tool base by class name, not by path; refused and the base
# script named, like the path form.
INSP_BASE_GD = """@tool
class_name InspToolBase
extends Resource


func base_marker() -> void:
	pass
"""

INSP_CLASS_EXT_GD = """extends InspToolBase
"""

INSP_CLASS_EXT_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_class_ext.gd" id="1"]
[resource]
script = ExtResource("1")
"""

# An ordinary script that merely mentions @tool in a comment: not an
# annotation, so it is accepted.
INSP_COMMENT_GD = """extends Resource
# @tool is deliberately absent here
@export var n: int = 5
"""

INSP_COMMENT_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_comment.gd" id="1"]
[resource]
script = ExtResource("1")
n = 19
"""

# A @tool script reached through an embedded sub-resource: the walk must name
# and refuse it before load.
INSP_SUBTOOL_TRES = """[gd_resource type="Resource" load_steps=3 format=3]
[ext_resource type="Script" path="res://insp_tool.gd" id="1"]
[sub_resource type="Resource" id="sub_1"]
script = ExtResource("1")
[resource]
metadata/sub = SubResource("sub_1")
"""

# A @tool script reached through an external dependency: same refusal path.
INSP_EXTERNAL_TOOL_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Resource" path="res://insp_tool.tres" id="1"]
[resource]
metadata/external = ExtResource("1")
"""

# An ordinary script that preloads a @tool resource. Compiling the script
# compiles the preloaded resource and runs its @tool _init, so inspect-resource
# must refuse the whole chain before load.
INSP_PRELOAD_PAYLOAD_GD = """@tool
extends Resource


func _init() -> void:
	print("MARKER_INSP_PRELOAD")
"""

INSP_PRELOAD_PAYLOAD_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_preload_payload.gd" id="1"]
[resource]
script = ExtResource("1")
"""

INSP_PRELOADER_GD = """extends Resource
const P = preload("res://insp_preload_payload.tres")
@export var n: int = 1
"""

INSP_PRELOADER_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_preloader.gd" id="1"]
[resource]
script = ExtResource("1")
n = 7
"""

# A malformed resource attaching the preloader script: even the failed-to-load
# path must not run the preloaded @tool code (the validator's f_fail2 case).
INSP_FAIL_PRELOADER_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_preloader.gd" id="1"]
[resource]
script = ExtResource("1")
n = )bad syntax
"""

# An ordinary script that preloads a non-@tool resource: accepted.
INSP_SAFE_PRELOADER_GD = """extends Resource
const P = preload("res://insp_data.gd")
@export var n: int = 1
"""

INSP_SAFE_PRELOADER_TRES = """[gd_resource type="Resource" load_steps=2 format=3]
[ext_resource type="Script" path="res://insp_safe_preloader.gd" id="1"]
[resource]
script = ExtResource("1")
n = 3
"""

# An embedded sub-resource chain long enough to hit the reply depth limit of
# 3: s1, s2 and s3 expand inline, s4 is shown as {"path","type"}. Sub-resources
# are declared last-to-first so every `SubResource(...)` reference points at an
# already-declared id (Godot rejects a forward reference).
INSP_CHAIN_GD = """extends Resource
@export var a: Resource
"""

INSP_CHAIN_TRES = """[gd_resource type="Resource" load_steps=6 format=3]
[ext_resource type="Script" path="res://insp_chain.gd" id="1"]
[sub_resource type="Resource" id="s4"]
script = ExtResource("1")
[sub_resource type="Resource" id="s3"]
script = ExtResource("1")
a = SubResource("s4")
[sub_resource type="Resource" id="s2"]
script = ExtResource("1")
a = SubResource("s3")
[sub_resource type="Resource" id="s1"]
script = ExtResource("1")
a = SubResource("s2")
[resource]
script = ExtResource("1")
a = SubResource("s1")
"""

# A self-referencing embedded sub-resource: the loop guard shows the second
# visit as {"path","type"} instead of recursing forever.
INSP_LOOP_GD = """extends Resource
@export var a: Resource
"""

INSP_LOOP_TRES = """[gd_resource type="Resource" load_steps=3 format=3]
[ext_resource type="Script" path="res://insp_loop.gd" id="1"]
[sub_resource type="Resource" id="cycle"]
script = ExtResource("1")
a = SubResource("cycle")
[resource]
script = ExtResource("1")
a = SubResource("cycle")
"""

# Uppercase variants of the refused kinds and the size cap; the extension
# comparison is case-insensitive.
INSP_UPPER_TSCN = """[gd_scene format=3]
[node name="Root" type="Node"]
"""

INSP_UPPER_GD = """extends Resource
"""

def insp_upper_tres_text():
    return '[gd_resource type="Resource" format=3]\n[resource]\n#' + "x" * (5 * 1024 * 1024) + "\n"

# A .scn scene.
INSP_UPPER_SCN = """[gd_scene format=3]
[node name="Root" type="Node"]
"""

INSP_SHADER = """shader_type canvas_item;
"""

INSP_CORRUPT_TRES = "this is not a valid resource file at all\n"

INSP_EMPTY_TRES = ""

# A text .tscn named .res: exists() is true, load() returns null.
INSP_FAKE_RES = """[gd_scene format=3]
[node name="Root" type="Node"]
"""

INSP_UNKNOWN_XYZ = "not a recognized extension\n"

INSP_NOTE_TXT = "plain text, not a resource\n"

# A deterministic text .tres fixture holding a 10001-element PackedFloat32Array
# (just over the 10000-element reply cap) so the reply shows the truncated
# marker. Built at write time so the literal is not huge in this file.
def insp_bigarr_text():
    floats = ",".join("0.0" for _ in range(10001))
    return (
        '[gd_resource type="Resource" load_steps=2 format=3]\n'
        '[ext_resource type="Script" path="res://insp_bigarr.gd" id="1"]\n'
        "[resource]\nscript = ExtResource(\"1\")\ndata = PackedFloat32Array(%s)\n" % floats
    )

INSP_BIGARR_GD = """extends Resource
@export var data: PackedFloat32Array = PackedFloat32Array()
"""

# A text .tres over 4 MiB (fixed cap, no flag): refused before it is parsed.
def insp_huge_tres_text():
    floats = ",".join("0.5" for _ in range(1200000))
    return (
        '[gd_resource type="Resource" format=3]\n[resource]\ndata = PackedFloat32Array(%s)\n' % floats
    )


def insp_scripted(script, extra=""):
    return (
        '[gd_resource type="Resource" load_steps=2 format=3]\n'
        '[ext_resource type="Script" path="res://%s" id="1"]\n[resource]\nscript = ExtResource("1")\n%s' % (script, extra)
    )

def insp_payload(name):
    # A @tool payload script that prints a distinctive line and writes a file,
    # plus the resource that attaches it. Each payload name is used by exactly
    # one fixture family, so a refused or failed-to-load request is COLD: no
    # other request in the session has loaded it.
    gd = (
        '@tool\nextends Resource\nfunc _init():\n\tprint("INSP_MARKER_%s")\n\tvar f = FileAccess.open("res://insp_evidence_%s.txt",FileAccess.WRITE)\n\tf.store_string("project code ran")\n'
        % (name, name)
    )
    pair = {
        "insp_%s_payload.gd" % name: gd,
        "insp_%s_payload.tres" % name: insp_scripted("insp_%s_payload.gd" % name),
    }
    return "res://insp_%s_payload.tres" % name, pair

# G1: a preload literal split across lines; the source scan must see it.
INSP_NEWLINE_GD = """extends Resource
const P = preload(
	"res://insp_newline_payload.tres"
)
@export var n: int = 1
"""

INSP_NEWLINE_TRES = insp_scripted("insp_newline.gd", 'n = 5\n')

# G1: a ResourceLoader.load with extra arguments after the path; the scan must
# see the literal path even though it has no closing parenthesis right after.
INSP_EXTRAARGS_GD = """extends Resource
static var x = ResourceLoader.load("res://insp_extraargs_payload.tres", "", 1)
@export var n: int = 1
"""

INSP_EXTRAARGS_TRES = insp_scripted("insp_extraargs.gd", 'n = 5\n')

# G2: an ordinary base whose own source preloads a @tool resource; a child that
# only extends it must be refused through the extends edge (path form).
INSP_INHERIT_BASE_GD = """extends Resource
const P = preload("res://insp_inherit_path_payload.tres")
"""

INSP_INHERIT_PATH_GD = """extends "res://insp_inherit_base.gd"
@export var n: int = 1
"""

INSP_INHERIT_PATH_TRES = insp_scripted("insp_inherit_path.gd", 'n = 5\n')

# G2: the same chain through a class_name base (the engine "extends ClassName"
# form; the base is registered in the project's global class list).
INSP_INHERIT_CLASS_BASE_GD = """class_name InspInheritBase
extends Resource
const P = preload("res://insp_inherit_class_payload.tres")
"""

INSP_INHERIT_CLASS_GD = """extends InspInheritBase
@export var n: int = 1
"""

INSP_INHERIT_CLASS_TRES = insp_scripted("insp_inherit_class.gd", 'n = 5\n')

# G2: a two-level extends chain (child -> middle -> base-with-preload).
INSP_INHERIT_MIDDLE_GD = """extends "res://insp_inherit_two_base.gd"
"""

INSP_INHERIT_TWO_GD = """extends "res://insp_inherit_middle.gd"
@export var n: int = 1
"""

INSP_INHERIT_TWO_TRES = insp_scripted("insp_inherit_two.gd", 'n = 5\n')

INSP_INHERIT_TWO_BASE_GD = """extends Resource
const P = preload("res://insp_twostep_payload.tres")
"""

# G3: a triple-quoted string whose body has a line that starts with @tool; that
# line is not an annotation, so the script is accepted.
INSP_MLSTRING_GD = (
    "extends Resource\n"
    'var note = """\n'
    "@tool\n"
    '"""\n'
    "@export var n: int = 1\n"
)

INSP_MLSTRING_TRES = insp_scripted("insp_mlstring.gd", 'n = 5\n')

# G3: a script whose base is a built-in class (extends Resource with no script
# reachable): accepted, no tool code anywhere.
INSP_BUILTIN_GD = """extends Resource
@export var n: int = 3
"""

INSP_BUILTIN_TRES = insp_scripted("insp_builtin.gd", 'n = 9\n')

# H1: a one-line `class_name Name extends Base` whose base preloads a @tool
# resource; the walk must follow the mid-line extends. Each form (accepted and
# malformed failed-load) uses its own cold payload.
INSP_OLINE_BASE_GD = """class_name InspOneLineBase
extends Resource
const P = preload("res://insp_oline_payload.tres")
"""

INSP_OLINE_GD = """class_name InspOneLine extends InspOneLineBase
"""

INSP_OLINE_TRES = insp_scripted("insp_oline.gd", "n = 5\n")
INSP_OLINE_BAD_TRES = insp_scripted("insp_oline_bad.gd", "invalid = )bad syntax\n")
INSP_OLINE_BAD_GD = """class_name InspOneLineBad extends InspOneLineBase
"""

# H1: an `@icon("...")` prefix on the same line as extends, with an ordinary
# base (no @tool anywhere): accepted, and the annotation line must not break
# the scan.
INSP_ICON_GD = """@icon("res://insp_icon.png") extends Resource
@export var n: int = 1
"""

INSP_ICON_TRES = insp_scripted("insp_icon.gd", "n = 5\n")
INSP_ICON_PNG = b"\x89PNG\r\n\x1a\n"

# H1: an inner `class Inner extends "res://base.gd":` whose base preloads a
# @tool resource; the walk follows the inner-class base.
INSP_INNER_BASE_GD = """extends Resource
const P = preload("res://insp_inner_payload.tres")
"""

INSP_INNER_GD = """extends Resource
class Inner extends "res://insp_inner_base.gd":
	pass
"""

INSP_INNER_TRES = insp_scripted("insp_inner.gd")

# H1: an ordinary script whose comment or string merely contains the word
# `extends`; must not add a path and must not be refused.
INSP_HCOMMENT_GD = """extends Resource
# extends "res://insp_comment_payload.tres"
var s = "extends"
@export var n: int = 1
"""

INSP_HCOMMENT_TRES = insp_scripted("insp_hcomment.gd", "n = 3\n")

# H2: a `preload("uid://...")` literal that resolves to a resource carrying a
# @tool script. The uid is fixed so the fixture is deterministic.
INSP_UID = "uid://c8v4p3test01"
INSP_UID_TOOL_GD = """@tool
extends Resource


func _init() -> void:
	print("INSP_MARKER_UIDTOOL")
"""

INSP_UID_TOOL_TRES = (
    '[gd_resource type="Resource" load_steps=2 format=3 uid="%s"]\n'
    '[ext_resource type="Script" path="res://insp_uid_tool.gd" id="1"]\n'
    "[resource]\nscript = ExtResource(\"1\")\n" % INSP_UID
)
INSP_UID_MID_GD = """extends Resource
const P = preload("%s")
@export var n: int = 1
""" % INSP_UID
INSP_UID_GOOD_TRES = insp_scripted("insp_uid_mid.gd", "n = 4\n")
INSP_UID_BAD_GD = """extends Resource
const P = preload("%s")
@export var n: int = 1
""" % INSP_UID
INSP_UID_BAD_TRES = insp_scripted("insp_uid_bad.gd", "invalid = )bad syntax\n")


# Writes every inspect-resource fixture into the throwaway project. This runs
# only right before session X, AFTER session W has recorded its file listing,
# so the new files never appear in the earlier rows' listing replies. The
# binary .res files are produced by a small generator script run in this
# project, so their bytes are deterministic across runs (measured byte-identical
# across separate project paths).
def write_inspect_fixtures(proj):
    files = {
        "insp_data.gd": INSP_DATA_GD,
        "insp_other.tres": INSP_OTHER_TRES,
        "insp_data.tres": INSP_DATA_TRES,
        "insp_tool.gd": INSP_TOOL_GD,
        "insp_tool.tres": INSP_TOOL_TRES,
        "insp_ext.gd": INSP_EXT_GD,
        "insp_ext.tres": INSP_EXT_TRES,
        "insp_base.gd": INSP_BASE_GD,
        "insp_class_ext.gd": INSP_CLASS_EXT_GD,
        "insp_class_ext.tres": INSP_CLASS_EXT_TRES,
        "insp_comment.gd": INSP_COMMENT_GD,
        "insp_comment.tres": INSP_COMMENT_TRES,
        "insp_subtool.tres": INSP_SUBTOOL_TRES,
        "insp_external_tool.tres": INSP_EXTERNAL_TOOL_TRES,
        "insp_preload_payload.gd": INSP_PRELOAD_PAYLOAD_GD,
        "insp_preload_payload.tres": INSP_PRELOAD_PAYLOAD_TRES,
        "insp_preloader.gd": INSP_PRELOADER_GD,
        "insp_preloader.tres": INSP_PRELOADER_TRES,
        "insp_fail_preloader.tres": INSP_FAIL_PRELOADER_TRES,
        "insp_safe_preloader.gd": INSP_SAFE_PRELOADER_GD,
        "insp_safe_preloader.tres": INSP_SAFE_PRELOADER_TRES,
        "insp_chain.gd": INSP_CHAIN_GD,
        "insp_chain.tres": INSP_CHAIN_TRES,
        "insp_loop.gd": INSP_LOOP_GD,
        "insp_loop.tres": INSP_LOOP_TRES,
        "insp_upper.TSCN": INSP_UPPER_TSCN,
        "insp_upper.GD": INSP_UPPER_GD,
        "insp_upper.TRES": insp_upper_tres_text(),
        "insp_upper.SCN": INSP_UPPER_SCN,
        "insp_shader.gdshader": INSP_SHADER,
        "insp_corrupt.tres": INSP_CORRUPT_TRES,
        "insp_empty.tres": INSP_EMPTY_TRES,
        "insp_fake.res": INSP_FAKE_RES,
        "insp_unknown.xyz": INSP_UNKNOWN_XYZ,
        "insp_note.txt": INSP_NOTE_TXT,
        "insp_bigarr.gd": INSP_BIGARR_GD,
        "insp_bigarr.tres": insp_bigarr_text(),
        "insp_huge.tres": insp_huge_tres_text(),
        "gen_insp_res.gd": (
            "extends SceneTree\n\n\nfunc _init() -> void:\n"
            "\tvar m: StandardMaterial3D = StandardMaterial3D.new()\n"
            "\tm.albedo_color = Color(0.1, 0.2, 0.3, 1)\n"
            "\tResourceSaver.save(m, \"res://insp_mat.res\")\n"
            "\tvar res: Resource = load(\"res://insp_data.gd\").new()\n"
            "\tres.set(\"name2\", \"from-res\")\n"
            "\tResourceSaver.save(res, \"res://insp_scripted.res\")\n"
            "\tquit()\n"
        ),
    }
    # Cold payloads for the scan cases (G1/G2): each is used by exactly one
    # fixture family so a refused or failed-to-load request never hits a
    # payload a previous request already loaded. The builder returns the .tres
    # path and the file pair to add.
    for payload_path, pair in [
        insp_payload("newline"),
        insp_payload("extraargs"),
        insp_payload("inherit_path"),
        insp_payload("inherit_class"),
        insp_payload("twostep"),
        insp_payload("oline"),
        insp_payload("inner"),
        insp_payload("uid"),
    ]:
        files.update(pair)
    files.update({
        "insp_newline.gd": INSP_NEWLINE_GD,
        "insp_newline.tres": INSP_NEWLINE_TRES,
        "insp_newline_bad.gd": INSP_NEWLINE_GD,
        "insp_newline_bad.tres": insp_scripted("insp_newline_bad.gd", "invalid = )bad syntax\n"),
        "insp_extraargs.gd": INSP_EXTRAARGS_GD,
        "insp_extraargs.tres": INSP_EXTRAARGS_TRES,
        "insp_extraargs_bad.gd": INSP_EXTRAARGS_GD,
        "insp_extraargs_bad.tres": insp_scripted("insp_extraargs_bad.gd", "invalid = )bad syntax\n"),
        "insp_inherit_base.gd": INSP_INHERIT_BASE_GD,
        "insp_inherit_path.gd": INSP_INHERIT_PATH_GD,
        "insp_inherit_path.tres": INSP_INHERIT_PATH_TRES,
        "insp_inherit_path_bad.gd": INSP_INHERIT_PATH_GD,
        "insp_inherit_path_bad.tres": insp_scripted("insp_inherit_path_bad.gd", "invalid = )bad syntax\n"),
        "insp_inherit_class_base.gd": INSP_INHERIT_CLASS_BASE_GD,
        "insp_inherit_class.gd": INSP_INHERIT_CLASS_GD,
        "insp_inherit_class.tres": INSP_INHERIT_CLASS_TRES,
        "insp_inherit_class_bad.gd": INSP_INHERIT_CLASS_GD,
        "insp_inherit_class_bad.tres": insp_scripted("insp_inherit_class_bad.gd", "invalid = )bad syntax\n"),
        "insp_inherit_middle.gd": INSP_INHERIT_MIDDLE_GD,
        "insp_inherit_two.gd": INSP_INHERIT_TWO_GD,
        "insp_inherit_two.tres": INSP_INHERIT_TWO_TRES,
        "insp_inherit_two_bad.gd": INSP_INHERIT_TWO_GD,
        "insp_inherit_two_bad.tres": insp_scripted("insp_inherit_two_bad.gd", "invalid = )bad syntax\n"),
        "insp_inherit_two_base.gd": INSP_INHERIT_TWO_BASE_GD,
        "insp_mlstring.gd": INSP_MLSTRING_GD,
        "insp_mlstring.tres": INSP_MLSTRING_TRES,
        "insp_builtin.gd": INSP_BUILTIN_GD,
        "insp_builtin.tres": INSP_BUILTIN_TRES,
        "insp_oline_base.gd": INSP_OLINE_BASE_GD,
        "insp_oline.gd": INSP_OLINE_GD,
        "insp_oline.tres": INSP_OLINE_TRES,
        "insp_oline_bad.gd": INSP_OLINE_BAD_GD,
        "insp_oline_bad.tres": INSP_OLINE_BAD_TRES,
        "insp_icon.gd": INSP_ICON_GD,
        "insp_icon.tres": INSP_ICON_TRES,
        "insp_icon.png": INSP_ICON_PNG,
        "insp_inner_base.gd": INSP_INNER_BASE_GD,
        "insp_inner.gd": INSP_INNER_GD,
        "insp_inner.tres": INSP_INNER_TRES,
        "insp_hcomment.gd": INSP_HCOMMENT_GD,
        "insp_hcomment.tres": INSP_HCOMMENT_TRES,
        "insp_uid_tool.gd": INSP_UID_TOOL_GD,
        "insp_uid_tool.tres": INSP_UID_TOOL_TRES,
        "insp_uid_mid.gd": INSP_UID_MID_GD,
        "insp_uid_good.tres": INSP_UID_GOOD_TRES,
        "insp_uid_bad.gd": INSP_UID_BAD_GD,
        "insp_uid_bad.tres": INSP_UID_BAD_TRES,
    })
    for name, text in files.items():
        with open(os.path.join(proj, name), "wb" if isinstance(text, bytes) else "w") as f:
            f.write(text)
    # A dangling symlink: `ResourceLoader.exists` is false for it, so it is
    # refused like a missing file. The target is a fixed name that no fixture
    # creates. The session records the refusal; the symlink itself is a file
    # written only for session X, so earlier listing rows never see it.
    try:
        os.symlink("insp_never_exists.tres", os.path.join(proj, "insp_dangle.tres"))
    except OSError:
        raise SystemExit("dangling symlink fixture could not be created")
    r = subprocess.run([GODOT, "--headless", "--path", proj, "--script",
                        os.path.join(proj, "gen_insp_res.gd")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120)
    if r.returncode != 0:
        raise SystemExit("fixture .res generation failed")


def ir(resource, project="<PROJ>"):
    d = {"command": "inspect_resource", "project_path": project,
         "resource_path": resource}
    return ("line", json.dumps(d, separators=(",", ":")))


def build_x():
    # Session X: inspect-resource (read-only, no edited scene). Runs right
    # after W, so its fixture files are written just before it and no earlier
    # session sees them; its rows are stored after W, before Y. Covers every refusal and
    # every accepted case with a row: typed reads (.tres/.res/.gdshader), the
    # object-valued encoding (inline embedded sub-resource, depth limit,
    # loop guard, {path,type} external reference, null), the 4 MiB .tres cap
    # and the 10000-element array truncation, case-insensitive extension
    # guards (.TSCN/.SCN/.GD/.TRES), the not-res:// path rule, the TextFile
    # kind, scene (.tscn/.scn) and script (.gd) refusals, @tool refusal on the
    # top script, on an embedded sub-resource, on an external dependency and
    # through a script preload (including the malformed failed-to-load case),
    # the comment false positive, the extends-a-@tool refusal in both the
    # path and class_name inheritance forms, and the safe preload case.
    return [
        ("line", '{"command":"status"}'),
        ir("res://insp_data.tres"),
        ir("res://insp_other.tres"),
        ir("res://insp_mat.res"),
        ir("res://insp_scripted.res"),
        ir("res://insp_shader.gdshader"),
        ir("res://insp_bigarr.tres"),
        ir("res://insp_huge.tres"),
        ir("res://insp_chain.tres"),
        ir("res://insp_loop.tres"),
        ir("res://insp_upper.TRES"),
        ir("res://insp_tool.tres"),
        ir("res://insp_subtool.tres"),
        ir("res://insp_external_tool.tres"),
        ir("res://insp_preloader.tres"),
        ir("res://insp_fail_preloader.tres"),
        ir("res://insp_safe_preloader.tres"),
        # G1 scan breadth: multi-line preload and extra-argument load; each has
        # its own cold payload, so the accepted-form row and the malformed
        # failed-form row are both independent of every other row.
        ir("res://insp_newline.tres"),
        ir("res://insp_newline_bad.tres"),
        ir("res://insp_extraargs.tres"),
        ir("res://insp_extraargs_bad.tres"),
        # G2 extends edge: a base reachable only through extends preloads a
        # @tool resource (path form, class_name form, two-level chain), each
        # refused with its own cold payload, plus the malformed forms.
        ir("res://insp_inherit_path.tres"),
        ir("res://insp_inherit_path_bad.tres"),
        ir("res://insp_inherit_class.tres"),
        ir("res://insp_inherit_class_bad.tres"),
        ir("res://insp_inherit_two.tres"),
        ir("res://insp_inherit_two_bad.tres"),
        # G3: a triple-quoted string line that starts with @tool is accepted;
        # a base that is a built-in class is accepted too.
        ir("res://insp_mlstring.tres"),
        ir("res://insp_builtin.tres"),
        # H1: one-line `class_name Name extends Base` and inner-class extends,
        # each with its own cold tool payload (accepted and malformed forms);
        # an @icon-prefixed extends with an ordinary base is accepted; a
        # comment/string that merely contains `extends` does not add a path.
        ir("res://insp_oline.tres"),
        ir("res://insp_oline_bad.tres"),
        ir("res://insp_inner.tres"),
        ir("res://insp_icon.tres"),
        ir("res://insp_hcomment.tres"),
        # H2: preload("uid://...") resolves through the ResourceUID registry;
        # the uid belongs to a resource with a @tool script, so the accepted
        # and the malformed forms are both refused naming that script.
        ir("res://insp_uid_good.tres"),
        ir("res://insp_uid_bad.tres"),
        ir("res://insp_ext.tres"),
        ir("res://insp_class_ext.tres"),
        ir("res://insp_comment.tres"),
        ir("res://insp_upper.TSCN"),
        ir("res://insp_upper.SCN"),
        ir("res://insp_upper.GD"),
        ir("res://sub.tscn"),
        ir("res://props.gd"),
        ir("res://insp_missing.tres"),
        ir("res://insp_corrupt.tres"),
        ir("res://insp_empty.tres"),
        ir("res://insp_fake.res"),
        ir("res://insp_unknown.xyz"),
        ir("res://insp_note.txt"),
        ir("res://insp_dangle.tres"),
        ir("res://"),
        ir("sub.tscn"),
        ir("sub.tscn", project="/no/replay-mismatch"),
        ir("res://../outside.tres"),
        ir("res://uid://junk"),
        ir("insp_data.tres", project="/no/replay-mismatch"),
        ("line", '{"command":"inspect_resource"}'),
        ("line", '{"command":"inspect_resource","project_path":"<PROJ>"}'),
        ("line", '{"command":"inspect_resource","resource_path":"res://insp_data.tres"}'),
        ("line", '{"command":"inspect_resource","project_path":"<PROJ>","resource_path":7}'),
    ]


def build_y():
    # Session Y: the published command interface. Runs right after X, with no
    # edited scene, so the reply carries no state of its own. The second request
    # carries a project_path that is not the project's: the interface request
    # checks no project path, so both requests must answer with the same
    # interface.
    return [
        ("line", '{"command":"commands"}'),
        ("line", '{"command":"commands","project_path":"/no/replay-mismatch"}'),
    ]


# Set-parent fixtures (sessions Z and AA). Sessions A-Y never reference these
# paths, and the files are written only after session Y records, so no earlier
# listing row (session W) or interface row (session Y) sees them. sp_base.tscn
# is written fresh by main() every run; session Z saves it, so session AA opens
# that saved file. Each accepted case has its own mover node, so the moves do
# not need an undo to keep the later paths stable.
SP_FIXTURE_GD = """extends Node
signal ping(value)


func on_ping(value):
	pass
"""

SP_SUB_TSCN = """[gd_scene format=3 uid="uid://gp054spsub1"]
[node name="SubRoot" type="Node"]
[node name="Inner" type="Node" parent="."]
"""

SP_BASE_TSCN = """[gd_scene load_steps=3 format=3 uid="uid://gp054spbase1"]
[ext_resource type="Script" path="res://sp_fixture.gd" id="1"]
[ext_resource type="PackedScene" path="res://sp_sub.tscn" id="2"]
[node name="SpRoot" type="Node"]
[node name="Box" type="Node" parent="." groups=["boxg"]]
script = ExtResource("1")
[node name="KidA" type="Node" parent="Box"]
[node name="KidB" type="Node" parent="Box"]
[node name="Target" type="Node" parent="." groups=["tg"]]
script = ExtResource("1")
[node name="Clash" type="Node" parent="Target"]
[node name="KidA" type="Node" parent="Target"]
[node name="MNode" type="Node" parent="."]
[node name="MIndex" type="Node" parent="."]
[node name="IA" type="Node" parent="MIndex"]
[node name="IB" type="Node" parent="MIndex"]
[node name="Holder2D" type="Node2D" parent="."]
position = Vector2(10, 10)
[node name="M2D" type="Node2D" parent="."]
position = Vector2(100, 50)
[node name="Holder3D" type="Node3D" parent="."]
position = Vector3(5, 5, 5)
[node name="M3D" type="Node3D" parent="."]
position = Vector3(30, 40, 50)
[node name="HolderCtl" type="Node2D" parent="."]
position = Vector2(2, 3)
[node name="MCtl" type="Control" parent="."]
position = Vector2(7, 8)
[node name="HolderNoKeep" type="Node2D" parent="."]
position = Vector2(1, 1)
[node name="MNoKeep" type="Node2D" parent="."]
position = Vector2(3, 4)
[node name="Uniq" type="Node" parent="."]
unique_name_in_owner = true
[node name="MSub" parent="." instance=ExtResource("2")]
[node name="Edit" parent="." instance=ExtResource("2")]
[editable path="Edit"]
[node name="LocalUnderEdit" type="Node" parent="Edit"]
owner="."
[connection signal="ping" from="Box" to="Target" method="on_ping"]
"""

# Derived-scene fixtures (sessions AB and AC). sp_dbase.tscn is the base scene;
# sp_dderived.tscn inherits it, so BaseNode and BaseChild have the edited scene
# root as their owner but are not stored by the edited scene, while Local,
# LocalKid, MoveMe and Holder are. Session AB rejects a move of an inherited
# node and accepts a move of a local node, and session AC reloads the result.
SP_DBASE_TSCN = """[gd_scene format=3 uid="uid://gp054dbase1"]
[node name="BaseRoot" type="Node"]
[node name="BaseNode" type="Node" parent="."]
[node name="BaseChild" type="Node" parent="BaseNode"]
[node name="BasePlain" type="Node" parent="."]
"""

SP_DDERIVED_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://gp054dderiv1"]
[ext_resource type="PackedScene" path="res://sp_dbase.tscn" id="1_base"]
[node name="DerivedRoot" instance=ExtResource("1_base")]
[node name="Local" type="Node" parent="."]
[node name="LocalKid" type="Node" parent="Local"]
[node name="MoveMe" type="Node" parent="."]
[node name="Holder" type="Node" parent="."]
"""


def write_set_parent_fixtures(proj):
    for name, text in [
        ("sp_fixture.gd", SP_FIXTURE_GD),
        ("sp_sub.tscn", SP_SUB_TSCN),
        ("sp_base.tscn", SP_BASE_TSCN),
        ("sp_dbase.tscn", SP_DBASE_TSCN),
        ("sp_dderived.tscn", SP_DDERIVED_TSCN),
    ]:
        with open(os.path.join(proj, name), "w") as f:
            f.write(text)
    write_sp2_fixtures(proj)


def sp(node, parent, index=None, keep=True, project="<PROJ>"):
    d = {"command": "set_parent", "node_path": node, "new_parent_path": parent,
         "index": index, "keep_global_transform": keep, "project_path": project}
    return ("line", json.dumps(d, separators=(",", ":")))


def build_z():
    # Session Z: set-parent against sp_base.tscn. Accepted moves run first
    # (each case has its own mover node), then every rejection is bracketed by
    # identical scene_tree snapshots. Nothing is saved until save_scene; the
    # file hash before and after the rejections is equal. The keep_global_
    # transform=false case is a raw request, because the CLI's boolean flag can
    # only ever send true.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "sp_base.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    accepted = [
        sp("MNode", "Target"),                     # plain node, append
        sp("M2D", "Holder2D"),                     # Node2D, keep global (default)
        sp("M3D", "Holder3D"),                     # Node3D, keep global
        sp("MCtl", "HolderCtl"),                   # Control, keep global
        sp("MSub", "Target"),                      # instanced sub-scene root
        sp("MIndex/IB", "MIndex", index=0),        # explicit index, same parent
        ("line", '{"command":"set_parent","node_path":"MNoKeep","new_parent_path":"HolderNoKeep","index":null,"keep_global_transform":false,"project_path":"<PROJ>"}'),
    ]
    out.extend(accepted)
    out.append(("line", '{"command":"scene_tree"}'))
    for node in ["Holder2D/M2D", "Holder3D/M3D", "HolderNoKeep/MNoKeep"]:
        out.append(ins(node))
    rejections = [
        ("line", '{"command":"set_parent"}'),
        ("line", '{"command":"set_parent","node_path":7,"new_parent_path":"Target","project_path":"<PROJ>"}'),
        sp("Box", "Target", keep="yes"),
        sp(".", "Target"),
        sp("Box", "Box"),
        sp("Box", "Box/KidA"),
        sp("Box/KidA", "Box", index=0),
        sp("Box/KidA", "Target"),
        sp("Sub/Inner", "Target"),
        sp("Edit/Inner", "Target"),
        sp("Box", "Sub/Inner"),
        sp("Box", "Edit/Inner"),
        sp("Box", "Target", index=99),
        sp("Box", "Target", index=-1),
        sp("Box", "Target", index=1.5),
        ("line", '{"command":"set_parent","node_path":"Box","new_parent_path":"Target","index":"2","project_path":"<PROJ>"}'),
        sp("NoSuch", "Target"),
        ("line", '{"command":"set_parent","node_path":"NoSuch","new_parent_path":"NoSuch2","project_path":"/no/replay-mismatch"}'),
        ("line", '{"command":"set_parent","node_path":"Box","new_parent_path":"Target","index":99,"project_path":"/no/replay-mismatch"}'),
        sp("", "Target"),
        sp("Box", ""),
        sp("Box", "/Target"),
        sp("Box", "A:B"),
        sp("Box", "../Target"),
    ]
    for request in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
    # No implicit save: the file is unchanged since the session opened, and Box
    # is not under Target yet.
    out.append(("file_sha256", "sp_base.tscn"))
    out.append(("file_not_contains", 'sp_base.tscn|name="Box" type="Node" parent="Target"'))
    # Persistence: move a subtree with a group, a signal connection and a
    # unique name, and an instance root, then save. The post-save bytes are not
    # hashed (the save adds generated uids and unique_ids), so the write is
    # proven by the file_contains checks; session AA reads the saved state back.
    out.append(sp("Box", "Target"))
    out.append(sp("Uniq", "Target"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'sp_base.tscn|[connection signal="ping" from="Target/Box" to="Target" method="on_ping"]'))
    out.append(("file_contains", 'sp_base.tscn|name="Box" type="Node" parent="Target"'))
    out.append(("file_contains", 'sp_base.tscn|name="MSub" parent="Target"'))
    out.append(("file_not_contains", 'sp_base.tscn|name="MSub" parent="Target" type='))
    out.append(("file_contains", 'sp_base.tscn|unique_name_in_owner = true'))
    return out


def build_aa():
    # Session AA: a fresh editor on the sp_base.tscn session Z saved. The moved
    # subtree, its group, its signal connection and its unique name must all be
    # present under the new parent after the reload; re-connecting the moved
    # connection must be rejected as a duplicate.
    return [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        ins("Target/Uniq"),
        ins("Holder2D/M2D"),
        q(**{"group": "boxg"}),
        cs("Target/Box", "ping", "Target", "on_ping"),
        ("file_contains", 'sp_base.tscn|[connection signal="ping" from="Target/Box" to="Target" method="on_ping"]'),
    ]


def build_ab():
    # Session AB: set-parent in a derived scene (sp_dderived.tscn inherits
    # sp_dbase.tscn). Rejections are bracketed by identical scene_tree
    # snapshots, and the file hash is equal before and after them: a wrong
    # project path answers first even when another field is malformed (a
    # fractional index, a non-bool keep_global_transform, a non-string
    # node_path, a non-string new_parent_path), and a node inherited from the
    # base scene and a node inside it are rejected. Then a local node moves, and
    # a local node moves under an inherited parent; save_scene writes both, and
    # the inherited nodes are not stored by the derived scene.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "sp_dderived.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    rejections = [
        ("line", '{"command":"set_parent","node_path":"MoveMe","new_parent_path":"Holder","index":1.5,"project_path":"/no/replay-mismatch"}'),
        ("line", '{"command":"set_parent","node_path":"MoveMe","new_parent_path":"Holder","keep_global_transform":"yes","project_path":"/no/replay-mismatch"}'),
        ("line", '{"command":"set_parent","node_path":7,"new_parent_path":"Holder","project_path":"/no/replay-mismatch"}'),
        ("line", '{"command":"set_parent","node_path":"MoveMe","new_parent_path":7,"project_path":"/no/replay-mismatch"}'),
        sp("BaseNode", "Local"),
        sp("BaseNode/BaseChild", "Local"),
    ]
    for request in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
    # No implicit save, and no rejection changed the scene.
    out.append(("file_sha256", "sp_dderived.tscn"))
    out.append(("file_not_contains", 'sp_dderived.tscn|name="MoveMe" type="Node" parent="Holder"'))
    out.append(sp("MoveMe", "Holder"))
    out.append(sp("Local", "BaseNode"))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "sp_dderived.tscn"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'sp_dderived.tscn|name="MoveMe" type="Node" parent="Holder"'))
    out.append(("file_contains", 'sp_dderived.tscn|name="Local" type="Node" parent="BaseNode"'))
    out.append(("file_not_contains", 'sp_dderived.tscn|name="BaseNode"'))
    return out


def build_ac():
    # Session AC: a fresh editor on the sp_dderived.tscn session AB saved. The
    # local node moved under Holder is present, the local node under the
    # inherited parent is present, and the inherited node is still a single
    # node (no copy was written).
    return [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        ins("Holder/MoveMe"),
        ins("BaseNode/Local"),
        q(**{"name": "BaseNode"}),
        ("file_contains", 'sp_dderived.tscn|name="MoveMe" type="Node" parent="Holder"'),
    ]


# Inherited-node matrix fixtures (session AD onwards). sp2_base.tscn holds a
# three-level chain, a plain node and an instanced sub-scene; every derived
# scene below inherits it. sp2_override.tscn overrides a property on all three
# levels of the chain plus a script and a group on the plain node, so one editor
# session covers five inherited cases. sp2_plain.tscn inherits with no override
# at all and is the control. sp2_twolevel.tscn inherits a scene that itself
# inherits, where the intermediate base holds only the instance row.
# sp2_local.tscn adds a node of its own under an inherited parent, which must
# stay movable.
SP2_SUB_GD = """extends Node2D
"""

SP2_SUB_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://sp2_sub.gd" id="1_s"]

[node name="SubCold" type="Node2D"]
script = ExtResource("1_s")

[node name="InnerCold" type="Node2D" parent="."]
"""

SP2_BASE_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://sp2_sub.tscn" id="1_s"]

[node name="Sp2Base" type="Node2D"]

[node name="BaseCold" type="Node2D" parent="."]

[node name="ChildCold" type="Node2D" parent="BaseCold"]

[node name="NestedCold" type="Node2D" parent="BaseCold/ChildCold"]

[node name="BasePlain" type="Node2D" parent="."]

[node name="SubCold" parent="." instance=ExtResource("1_s")]
"""

SP2_OVERRIDE_TSCN = """[gd_scene load_steps=3 format=3]

[ext_resource type="PackedScene" path="res://sp2_base.tscn" id="1_b"]
[ext_resource type="Script" path="res://sp2_sub.gd" id="2_s"]

[node name="Sp2Override" instance=ExtResource("1_b")]

[node name="BaseCold" parent="." index="0"]
position = Vector2(11, 12)

[node name="ChildCold" parent="BaseCold" index="0"]
position = Vector2(21, 22)

[node name="NestedCold" parent="BaseCold/ChildCold" index="0"]
position = Vector2(31, 32)

[node name="BasePlain" parent="." index="3"]
position = Vector2(41, 42)
script = ExtResource("2_s")
groups = ["sp2group"]

[node name="Target" type="Node2D" parent="."]
"""

SP2_PLAIN_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://sp2_base.tscn" id="1_b"]

[node name="Sp2Plain" instance=ExtResource("1_b")]

[node name="Target" type="Node2D" parent="."]
"""

SP2_MID_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://sp2_base.tscn" id="1_b"]

[node name="Sp2Mid" instance=ExtResource("1_b")]

[node name="MidLocal" type="Node2D" parent="."]
"""

SP2_TWOLEVEL_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://sp2_mid.tscn" id="1_m"]

[node name="Sp2Two" instance=ExtResource("1_m")]

[node name="BasePlain" parent="." index="3"]
position = Vector2(51, 52)

[node name="Target" type="Node2D" parent="."]
"""

SP2_LOCAL_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://sp2_base.tscn" id="1_b"]

[node name="Sp2Local" instance=ExtResource("1_b")]

[node name="AddedUnderInherited" type="Node2D" parent="BaseCold"]
owner = "."

[node name="Target" type="Node2D" parent="."]
"""


def write_sp2_fixtures(proj):
    for name, text in [
        ("sp2_sub.gd", SP2_SUB_GD),
        ("sp2_sub.tscn", SP2_SUB_TSCN),
        ("sp2_base.tscn", SP2_BASE_TSCN),
        ("sp2_override.tscn", SP2_OVERRIDE_TSCN),
        ("sp2_plain.tscn", SP2_PLAIN_TSCN),
        ("sp2_mid.tscn", SP2_MID_TSCN),
        ("sp2_twolevel.tscn", SP2_TWOLEVEL_TSCN),
        ("sp2_local.tscn", SP2_LOCAL_TSCN),
    ]:
        with open(os.path.join(proj, name), "w") as f:
            f.write(text)


def build_ad():
    # Session AD: five inherited nodes that carry a local override (property on
    # three levels of the chain, script and group on the plain node). Every one
    # is rejected, the scene and the file are unchanged by the rejections, and
    # no local copy is written under Target.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "sp2_override.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    for node in ["BaseCold", "BaseCold/ChildCold", "BaseCold/ChildCold/NestedCold",
                 "BasePlain", "SubCold", "SubCold/InnerCold"]:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(sp(node, "Target"))
        out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "sp2_override.tscn"))
    out.append(("file_not_contains", 'sp2_override.tscn|parent="Target"'))
    out.append(("file_not_contains", 'sp2_override.tscn|name="BaseCold" type="Node2D" parent="Target"'))
    return out


def build_ae():
    # Session AE: the control that inherits with no override at all, and the
    # two-level base chain where the intermediate base holds only the instance
    # row. Both are rejected the same way.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "sp2_plain.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    for node in ["BaseCold", "BasePlain"]:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(sp(node, "Target"))
        out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "sp2_plain.tscn"))
    out.append(("file_not_contains", 'sp2_plain.tscn|parent="Target"'))
    return out


def build_af():
    # Session AF: the two-level base chain. BasePlain carries an override at the
    # top, BaseCold carries none, and MidLocal belongs to the intermediate base.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "sp2_twolevel.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    for node in ["BasePlain", "BaseCold", "BaseCold/ChildCold", "MidLocal"]:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(sp(node, "Target"))
        out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "sp2_twolevel.tscn"))
    out.append(("file_not_contains", 'sp2_twolevel.tscn|parent="Target"'))
    return out


def build_ag():
    # Session AG: a node the derived scene owns itself, added under an inherited
    # parent, stays movable. It is saved and the file must hold exactly one copy
    # under Target and none beside the inherited parent.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "sp2_local.tscn"),
        ("line", '{"command":"scene_tree"}'),
        sp("BaseCold/AddedUnderInherited", "Target"),
        ("line", '{"command":"scene_tree"}'),
        ("file_sha256", "sp2_local.tscn"),
        ("line", '{"command":"save_scene","project_path":"<PROJ>"}'),
        ("file_contains", 'sp2_local.tscn|name="AddedUnderInherited" type="Node2D" parent="Target"'),
        ("file_not_contains", 'sp2_local.tscn|name="AddedUnderInherited" type="Node2D" parent="BaseCold"'),
    ]
    return out


def build_ah():
    # Session AH: a fresh editor on the scene session AG saved. The node added
    # under the inherited parent was moved under Target and persisted, and the
    # inherited chain is still a single node each.
    return [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        ins("Target/AddedUnderInherited"),
        q(**{"name": "AddedUnderInherited"}),
        q(**{"name": "BaseCold"}),
        q(**{"name": "ChildCold"}),
        q(**{"name": "NestedCold"}),
        ("file_contains", 'sp2_local.tscn|name="AddedUnderInherited" type="Node2D" parent="Target"'),
    ]


# Base-inherited rename and delete fixtures (sessions AI to AK). Both scenes
# inherit sp2_base.tscn, so BaseCold, ChildCold, NestedCold, BasePlain and
# SubCold are inherited nodes that the edited scene root owns while Target is a
# local node. ib_rename.tscn and ib_delete.tscn hold the same text so the
# rename session and the delete session each hash a fresh file before their one
# accepted save.
IB_DERIVED_TSCN = """[gd_scene load_steps=3 format=3]

[ext_resource type="PackedScene" path="res://sp2_base.tscn" id="1_b"]
[ext_resource type="Script" path="res://sp2_sub.gd" id="2_s"]

[node name="IbRoot" instance=ExtResource("1_b")]

[node name="BaseCold" parent="." index="0"]
position = Vector2(11, 12)

[node name="BasePlain" parent="." index="1"]
position = Vector2(41, 42)
script = ExtResource("2_s")

[node name="Target" type="Node2D" parent="."]
"""


def write_ib_fixtures(proj):
    for name in ("ib_rename.tscn", "ib_delete.tscn"):
        with open(os.path.join(proj, name), "w") as f:
            f.write(IB_DERIVED_TSCN)


def dele(node, project="<PROJ>"):
    return ("line", json.dumps({"command": "delete_node", "node_path": node,
                                "project_path": project}, separators=(",", ":")))


def build_ai():
    # Session AI: rename-node against ib_rename.tscn, a derived scene. Every
    # inherited target is rejected before the undo action: an inherited node
    # with a property override, an inherited node with a property and script
    # override, two inherited descendants, the root of the base scene's
    # instanced sub-scene, and a wrong project path (the project guard answers
    # first). The rejected set-parent of the same inherited node shows that the
    # rename-then-set-parent composition is unreachable. Each rejection is
    # bracketed by identical scene_tree snapshots and the file hash is equal
    # before and after them. Then the one local node is renamed and saved.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "ib_rename.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    rejections = [
        rn("BaseCold", "BaseColdX"),
        rn("BasePlain", "BasePlainX"),
        rn("BaseCold/ChildCold", "ChildColdX"),
        rn("BaseCold/ChildCold/NestedCold", "NestedX"),
        rn("SubCold", "SubColdX"),
        rn("BaseCold", "BaseColdX", project="/no/replay-mismatch"),
        sp("BaseCold", "Target"),
    ]
    for request in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "ib_rename.tscn"))
    out.append(("file_not_contains", 'ib_rename.tscn|name="BaseColdX"'))
    out.append(("file_not_contains", 'ib_rename.tscn|name="ChildColdX"'))
    out.append(rn("Target", "TargetX"))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "ib_rename.tscn"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'ib_rename.tscn|name="TargetX" type="Node2D" parent="."'))
    out.append(("file_contains", 'ib_rename.tscn|name="BaseCold" parent="." index="0"'))
    return out


def build_aj():
    # Session AJ: delete-node against ib_delete.tscn. Every inherited target is
    # rejected before the undo action: an inherited node with an override, an
    # inherited descendant, the base scene's instanced sub-scene root, and a
    # wrong project path (the project guard answers first). The refused delete
    # followed by a refused set-parent of the same node shows that the
    # delete-then-create-then-set-parent composition cannot start. Each
    # rejection is bracketed by identical scene_tree snapshots and the file hash
    # is equal before and after them. Then the one local node is deleted and
    # saved.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "ib_delete.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    rejections = [
        dele("BaseCold"),
        dele("BasePlain"),
        dele("BaseCold/ChildCold"),
        dele("SubCold"),
        dele("BaseCold", project="/no/replay-mismatch"),
        sp("BaseCold", "Target"),
    ]
    for request in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "ib_delete.tscn"))
    out.append(("file_not_contains", 'ib_delete.tscn|name="Target" type="Node2D" parent="Holder"'))
    out.append(dele("Target"))
    out.append(("line", '{"command":"scene_tree"}'))
    out.append(("file_sha256", "ib_delete.tscn"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_not_contains", 'ib_delete.tscn|name="Target"'))
    out.append(("file_contains", 'ib_delete.tscn|name="BaseCold" parent="." index="0"'))
    return out


def build_ak():
    # Session AK: a fresh editor on the two scenes sessions AI and AJ saved. The
    # renamed local node is present and the inherited nodes are each a single
    # node (no copy was written); the deleted local node is gone and the
    # inherited nodes are still a single node each.
    return [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        ins("TargetX"),
        q(**{"name": "TargetX"}),
        q(**{"name": "BaseCold"}),
        q(**{"name": "ChildCold"}),
        ("file_contains", 'ib_rename.tscn|name="TargetX" type="Node2D" parent="."'),
        os_("res://ib_delete.tscn"),
        ("line", '{"command":"scene_tree"}'),
        ins("Target"),
        q(**{"name": "Target"}),
        q(**{"name": "BaseCold"}),
        q(**{"name": "ChildCold"}),
        q(**{"name": "NestedCold"}),
        ("file_not_contains", 'ib_delete.tscn|name="Target"'),
    ]


def build_console():
    # Session Z: the console command's validation. Runs right after Y, with no
    # edited scene. Every row is rejected before the buffer is read, so no
    # reply carries ambient engine log text: the project guard answers first
    # even when other fields are invalid, then each mistyped or out-of-range
    # argument is refused with its own message. The last row is a successful
    # read with a cursor past every message: it returns no messages, only the
    # cursor state. A successful read of real messages is not recorded: the
    # editor's startup log text varies by run, so it cannot be
    # byte-deterministic; the happy path is proved by isolated-editor probes
    # instead.
    return [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"console","project_path":"/no/replay-mismatch","tail":"abc","level":"nope","since":-1,"clear":"yes"}'),
        ("line", '{"command":"console"}'),
        ("line", '{"command":"console","project_path":7}'),
        ("line", '{"command":"console","project_path":"<PROJ>","tail":"abc"}'),
        ("line", '{"command":"console","project_path":"<PROJ>","tail":0}'),
        ("line", '{"command":"console","project_path":"<PROJ>","tail":1001}'),
        ("line", '{"command":"console","project_path":"<PROJ>","tail":1.5}'),
        ("line", '{"command":"console","project_path":"<PROJ>","level":"verbose"}'),
        ("line", '{"command":"console","project_path":"<PROJ>","level":5}'),
        ("line", '{"command":"console","project_path":"<PROJ>","since":"5"}'),
        ("line", '{"command":"console","project_path":"<PROJ>","since":-1}'),
        ("line", '{"command":"console","project_path":"<PROJ>","since":0.5}'),
        ("line", '{"command":"console","project_path":"<PROJ>","clear":"yes"}'),
        ("line", '{"command":"console","project_path":"<PROJ>","since":999999999}'),
    ]


# A preloaded @tool script that raises while it initializes: the submitted
# script preloads it, so the error is reported during the reload, not during
# the call.
EVAL_BAD_PRELOAD_GD = """@tool
extends Resource
static var bad = _boom()
static func _boom():
\tvar n = null
\treturn n.missing_member
"""


def normalize_eval_reply(reply):
    # An eval reply carries two measured, run-dependent values that cannot be
    # byte-deterministic: the elapsed milliseconds (a wall-clock duration) and
    # the anonymous `gdscript://-<id>.gd` source path the engine reports for a
    # compile or runtime error (a pointer that differs every run). Both are
    # replaced by fixed placeholders so a stored row is reproducible.
    reply = re.sub(r'"elapsed_msec":\d+', '"elapsed_msec":"<ELAPSED>"', reply)
    reply = re.sub(r'gdscript://-\d+\.gd', '<ANON>', reply)
    return reply


def check_eval_display(rows_eval):
    # Every status-ok eval reply carries `display`: the readable form of the
    # returned value as a string, or null when the run did not complete or the
    # display is over the cap. A display row prints `rt:` and returns its value;
    # after all display rows the session sends one parse request each, and each
    # parse request returns str_to_var of the display text the matching reply
    # actually carried. This pairs every display row with its parse reply and
    # requires the parsed value to equal the value that row returned, so the
    # check consumes the emitted display text itself rather than a text computed
    # beside it.
    display_values = []
    parse_rows = []
    for request, reply in rows_eval:
        if '"command":"eval"' not in request:
            continue
        parsed = json.loads(reply)
        if parsed.get("status") != "ok":
            continue
        data = parsed["data"]
        if "display" not in data:
            raise RuntimeError("eval ok reply carries no display: %s" % reply)
        if data["completed"]:
            if data["display"] is not None and not isinstance(data["display"], str):
                raise RuntimeError("completed eval reply with a non-string, non-null display: %s" % reply)
        elif data["display"] is not None:
            raise RuntimeError("not-completed eval reply with a non-null display: %s" % reply)
        code = json.loads(request).get("code")
        if isinstance(code, str):
            if 'print("rt:"' in code:
                # A display row: it must carry its `rt:true` marker. The parse
                # request appended after it is the proof that consumes the
                # returned display. Invalid text parses to null, so a null value
                # also requires its readable form to be the canonical `null`
                # text, which str_to_var reads back to null.
                if data["output"] != ["rt:true"]:
                    raise RuntimeError("eval display row without its rt:true marker: %s" % reply)
                if data["value"] is None and data["display"] != "null":
                    raise RuntimeError("eval null display row emitted %r instead of null: %s" % (data["display"], reply))
                display_values.append(data["value"])
            elif "str_to_var(" in code:
                parse_rows.append((request, data["value"]))
    if len(display_values) != 19:
        raise RuntimeError("expected 19 display rows, saw %d" % len(display_values))
    if len(parse_rows) != len(display_values):
        raise RuntimeError("expected %d parse rows, saw %d" % (len(display_values), len(parse_rows)))
    for index, (request, value) in enumerate(parse_rows):
        if value != display_values[index]:
            raise RuntimeError("the returned display does not read back to the returned value: %s" % request)


def build_eval():
    # Session Z: the eval command. Runs right after Y with no edited scene, so
    # scene_changed is always false and no request mutates stored state. The
    # snippets return each value type, print, fail to compile, fail at run time,
    # and block past a small timeout; the rejected requests prove the project
    # guard answers first and that a bad timeout or a bad code field is refused.
    # Replies are normalised by normalize_eval_reply (elapsed and the anonymous
    # source path), so the stored rows are byte-deterministic.
    def ev(code, extra=None):
        req = {"command": "eval", "project_path": "<PROJ>", "code": code}
        if extra:
            req.update(extra)
        return ("line", json.dumps(req, separators=(",", ":")))
    h = "extends RefCounted\nfunc run(editor, undo_redo) -> Variant:\n\t"

    display_rows = []

    def drow(setup, nan=False):
        # `setup` declares v, the value the row returns. The snippet prints `rt:`
        # plus whether str_to_var of var_to_str of v equals v. A parse request
        # appended after the row reads back the display text the reply carries.
        check = "is_nan(str_to_var(var_to_str(v)))" if nan else "str_to_var(var_to_str(v)) == v"
        request = ev(h + "%s\n\tprint(\"rt:\" + str(%s))\n\treturn v" % (setup, check))
        display_rows.append(request)
        return request

    rows = [
        ev(h + "return 42"),
        ev(h + "return 3.5"),
        ev(h + "return \"hi\""),
        ev(h + "return Vector2(1, 2)"),
        ev(h + "return [1, \"a\", 2.5]"),
        ev(h + "return {\"k\": 1, \"v\": 2}"),
        ev(h + "return null"),
        ev(h + "print(\"line one\")\n\tprint(\"line two\")\n\treturn 1"),
        ev(h + "return x oops"),
        ev(h + "var n = null\n\treturn n.property"),
        ev(h + "var t0 = Time.get_ticks_msec()\n\twhile Time.get_ticks_msec() - t0 < 300:\n\t\tpass\n\treturn 1", {"timeout": 100}),
        ("line", '{"command":"eval","project_path":"/no/replay-mismatch","code":"extends RefCounted\\nfunc run(editor, undo_redo) -> Variant:\\n\\treturn 1"}'),
        ("line", '{"command":"eval","project_path":"<PROJ>"}'),
        ("line", '{"command":"eval","project_path":"<PROJ>","code":7}'),
        ("line", '{"command":"eval","project_path":"<PROJ>","timeout":0,"code":"extends RefCounted\\nfunc run(editor, undo_redo) -> Variant:\\n\\treturn 1"}'),
        ("line", '{"command":"eval","project_path":"/no/replay-mismatch","code":7}'),
        # A value the engine refuses to encode is named in errors instead of a
        # bare null, a container past the tagged depth says so, and an
        # oversized value is refused by name.
        ev(h + "var n = Node.new()\n\treturn n"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(2000):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\treturn a"),
        ev(h + 'return "x".repeat(1200000)'),
        # Repair rows: deep value at exactly the tagged limit still returns
        # exactly; rich and spaced output keep valid JSON and spaces; an
        # explicit null timeout is refused; a warning keeps its value while
        # push_error fails; an _init error fails; an Object script instance is
        # freed (held in editor meta so the requests stay deterministic); a
        # scene switch stays clean and a switch plus edit reports the change.
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(99):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\treturn a"),
        ev(h + 'print_rich("[b]bold[/b]")\n\treturn null'),
        ev(h + 'print("  spaced  ")\n\treturn 1'),
        ("line", '{"command":"eval","project_path":"<PROJ>","timeout":null,"code":"extends RefCounted\\nfunc run(editor, undo_redo) -> Variant:\\n\\treturn 1"}'),
        ev(h + 'push_warning("replay warning")\n\treturn 42'),
        ev(h + 'push_error("replay error")\n\treturn 42'),
        ("line", json.dumps({"command": "eval", "project_path": "<PROJ>", "code": 'extends RefCounted\nfunc _init():\n\tvar n = null\n\tvar _v = n.property\nfunc run(editor, undo_redo):\n\treturn 42'}, separators=(",", ":"))),
        ("line", json.dumps({"command": "eval", "project_path": "<PROJ>", "code": 'extends Object\nfunc run(editor, undo_redo):\n\teditor.get_base_control().set_meta("replay_leak", get_instance_id())\n\treturn null'}, separators=(",", ":"))),
        ev(h + 'return is_instance_id_valid(editor.get_base_control().get_meta("replay_leak"))'),
        # An error raised while the reload initializes the submitted @tool
        # script fails the run: a failing static initializer, a _static_init
        # that calls push_error, and a preloaded @tool script with an error.
        # Each is followed by a clean call, which must show that the logger
        # window closed and the next snippet is unaffected.
        ("line", json.dumps({"command": "eval", "project_path": "<PROJ>", "code": '@tool\nextends RefCounted\nstatic var bad = _boom()\nstatic func _boom():\n\tvar n = null\n\treturn n.missing_member\nfunc run(editor, undo_redo):\n\treturn 42'}, separators=(",", ":"))),
        ev(h + "return 9"),
        ("line", json.dumps({"command": "eval", "project_path": "<PROJ>", "code": '@tool\nextends RefCounted\nstatic func _static_init() -> void:\n\tpush_error("replay static init error")\nfunc run(editor, undo_redo):\n\treturn 42'}, separators=(",", ":"))),
        ev(h + "return 9"),
        ("line", json.dumps({"command": "eval", "project_path": "<PROJ>", "code": '@tool\nextends RefCounted\nconst bad = preload("res://eval_bad_preload.gd")\nfunc run(editor, undo_redo):\n\treturn 42'}, separators=(",", ":"))),
        ev(h + "return 9"),
        # The tagged depth limit counts the container being examined, so
        # exactly 100 levels comes back and one level more is refused. An
        # Array costs one level and a Dictionary two, so the three shapes
        # reach the same boundary at different wrapper counts.
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(99):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(100):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\treturn a"),
        ev(h + 'var d = {"k": 1}\n\tfor i in range(49):\n\t\td = {"k": d}\n\treturn d'),
        ev(h + 'var d = {"k": 1}\n\tfor i in range(50):\n\t\td = {"k": d}\n\treturn d'),
        ev(h + "var v = 1\n\tfor i in range(33):\n\t\tv = {\"k\": [v]}\n\treturn [v]"),
        ev(h + "var v = 1\n\tfor i in range(34):\n\t\tv = {\"k\": [v]}\n\treturn [v]"),
        # A tagged terminal value (a Vector2, a packed array, a Transform3D)
        # costs two levels, so the boundary sits one wrapper lower than for a
        # scalar-ended Array: 98 nested Arrays plus the terminal come back as
        # exactly 100 levels, and 99 plus the terminal are refused.
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(97):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tc.append(Vector2(1, 2))\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(98):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tc.append(Vector2(1, 2))\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(97):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tc.append(PackedInt32Array([1, 2]))\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(98):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tc.append(PackedInt32Array([1, 2]))\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(97):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tc.append(Transform3D(Basis(), Vector3(0, 0, 0)))\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(98):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tc.append(Transform3D(Basis(), Vector3(0, 0, 0)))\n\treturn a"),
        # A typed Array costs two levels while an untyped one costs one, so a
        # typed Array[int] terminal sits one wrapper lower than a scalar, and a
        # chain of typed Arrays steps two levels per wrapper (an exact 100 is
        # unreachable there, so the pair below pins 99 against 101).
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(97):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tvar t: Array[int] = [1]\n\tc.append(t)\n\treturn a"),
        ev(h + "var a = []\n\tvar c = a\n\tfor i in range(98):\n\t\tvar b = []\n\t\tc.append(b)\n\t\tc = b\n\tvar t: Array[int] = [1]\n\tc.append(t)\n\treturn a"),
        ev(h + "var v: Variant = []\n\tfor i in range(49):\n\t\tv = Array([v], TYPE_ARRAY, &\"\", null)\n\treturn v"),
        ev(h + "var v: Variant = []\n\tfor i in range(50):\n\t\tv = Array([v], TYPE_ARRAY, &\"\", null)\n\treturn v"),
        # A Node that another Node parents stays alive even when neither is in
        # the tree, so the retained parent keeps the child across two calls.
        ("line", json.dumps({"command": "eval", "project_path": "<PROJ>", "code": 'extends Node\nfunc run(editor, undo_redo):\n\tvar parent = Node.new()\n\tparent.name = "ReplayParent"\n\tparent.add_child(self)\n\teditor.get_base_control().set_meta("replay_parent", parent)\n\treturn parent.get_child_count()'}, separators=(",", ":"))),
        ev(h + 'var p = editor.get_base_control().get_meta("replay_parent", null)\n\treturn p.get_child_count() if p != null else -1'),
        # The only rows of this session that open a scene, so every row above
        # runs with no edited scene.
        ev(h + 'editor.open_scene_from_path("res://main.tscn")\n\treturn null'),
        ev(h + 'editor.open_scene_from_path("res://main.tscn")\n\teditor.get_edited_scene_root().get_node("N2D").position = Vector2(7, 8)\n\treturn null'),
        # The display rows. Each returns one value of a covered class and prints
        # `rt:` plus whether str_to_var of its readable form equals the value it
        # returned; a parse request appended after them reads back the display
        # text each reply carries. A NaN row uses is_nan, because NaN is never
        # equal to itself. They run after the two scene rows, with that scene
        # still open and unchanged, so scene_changed is false.
        drow("var v = 42"),
        drow("var v = 3.5"),
        drow("var v = NAN", nan=True),
        drow("var v = INF"),
        drow("var v = true"),
        drow("var v = null"),
        drow("var v = " + json.dumps('a"b')),
        drow("var v = " + json.dumps("line1\nline2")),
        drow("var v = " + json.dumps("héllo → 世界")),
        drow('var v = &"sn_hello"'),
        drow('var v = NodePath("a/b")'),
        drow("var v = Vector2(1, 2)"),
        drow("var v = Color(1, 0.5, 0.25, 1)"),
        drow('var v = [1, "a", 2.5]'),
        drow("var v: Array[int] = [1, 2]"),
        drow('var v = {"k": 1, "v": 2}'),
        drow('var v = {"k": 1}\n\tfor i in range(49):\n\t\tv = {"k": v}'),
        drow("var v = PackedByteArray([1, 2, 3])"),
        drow('push_warning("replay display warning")\n\tvar v = 42'),
        # A run that does not complete carries display null, exactly as value.
        ev(h + "var n = null\n\treturn n.property"),
        # A value that fits the encoded cap but whose readable form does not: a
        # PackedByteArray of 225000 bytes encodes to 900035 bytes, inside the
        # 1048576-byte cap, but var_to_str writes it with ", " between elements,
        # 1125015 bytes, past the cap. The display alone is dropped and one
        # warning entry names its size; the value and the other fields return.
        ev(h + "var p = PackedByteArray()\n\tp.resize(225000)\n\tp.fill(255)\n\treturn p"),
    ]
    # One parse request per display row, appended at the end: each returns
    # str_to_var of the display text the reply at that row actually carried.
    for request in display_rows:
        rows.append(("eval_parse", str(rows.index(request))))
    return rows


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


def wait_filesystem_ready(port, proj, timeout=120):
    # Polls list-resources until the editor's file system reports a finished
    # scan. This wait is not recorded; it keeps a fresh-editor session
    # deterministic when the editor's startup scan is still running.
    deadline = time.time() + timeout
    while True:
        reply = json.loads(send_one(port, json.dumps(
            {"command": "list_resources", "project_path": proj, "limit": 1},
            separators=(",", ":")), True, 25))
        if reply.get("status") == "ok" and reply["data"].get("scanning") is False:
            return
        if time.time() >= deadline:
            raise RuntimeError("editor file system never became ready")
        time.sleep(0.5)


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


def run_session(port, proj, scene, requests, expected_scene, ready_filesystem=False):
    proc = launch(proj, scene)
    try:
        wait_listener(port)
        if expected_scene:
            end = time.time() + 90
            while time.time() < end:
                if status_scene(port) == expected_scene:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("edited scene never opened")
        else:
            time.sleep(3)
            if status_scene(port) is not None:
                raise RuntimeError("expected no edited scene, one is open")
        if ready_filesystem:
            wait_filesystem_ready(port, proj)
        rows = []
        for kind, text in requests:
            if kind == "file_sha256":
                with open(os.path.join(proj, text), "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()
                rows.append(("CHECK file_sha256 %s" % text, digest))
                continue
            if kind in ("file_contains", "file_not_contains"):
                rel, _, needle = text.partition("|")
                with open(os.path.join(proj, rel)) as f:
                    content = f.read()
                present = needle in content
                if kind == "file_contains" and not present:
                    raise RuntimeError("expected %r in %s" % (needle, rel))
                if kind == "file_not_contains" and present:
                    raise RuntimeError("did not expect %r in %s" % (needle, rel))
                rows.append(("CHECK %s %s" % (kind, text), "ok"))
                continue
            if kind == "eval_parse":
                display = json.loads(rows[int(text)][1])["data"]["display"]
                code = ("extends RefCounted\nfunc run(editor, undo_redo) -> Variant:\n\t"
                        "return str_to_var(%s)" % json.dumps(display))
                wire = json.dumps({"command": "eval", "project_path": proj, "code": code},
                                  separators=(",", ":"))
                newline = True
            else:
                wire = text.replace("<PROJ>", proj)
                newline = kind == "line"
            reply = send_one(port, wire, newline, 25)
            if '"command":"eval"' in wire:
                reply = normalize_eval_reply(reply)
                json.loads(reply)
            rows.append((wire.replace(proj, "<PROJECT>"),
                         reply.replace(proj, "<PROJECT>")))
        return rows
    finally:
        stop(proc)


# A minimal valid 2x2 RGBA PNG (a solid red square), built so the bytes are
# exact and need no external file.
def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _make_png(width=2, height=2):
    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes([255, 0, 0, 255] * width)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(raw, 9)) + _png_chunk(b"IEND", b"")


RS_EXPORT_GD = """@tool
extends Node2D
@export var target_node: Node
"""

RS_SHAPE_TRES = """[gd_resource type="RectangleShape2D" format=3]

[resource]
size = Vector2(10, 20)
"""

RS_MAT_TRES = """[gd_resource type="CanvasItemMaterial" format=3]

[resource]
blend_mode = 1
"""

RS_MAIN_TSCN = """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://rs_export.gd" id="1"]
[ext_resource type="RectangleShape2D" path="res://rs_shape.tres" id="2"]

[node name="Main" type="Node2D"]

[node name="Spr" type="Sprite2D" parent="."]

[node name="Col" type="CollisionShape2D" parent="."]

[node name="Mesh" type="MeshInstance3D" parent="."]

[node name="SprM" type="Sprite2D" parent="."]

[node name="ShapeA" type="CollisionShape2D" parent="."]
shape = ExtResource("2")

[node name="ShapeB" type="CollisionShape2D" parent="."]
shape = ExtResource("2")

[node name="Hold" type="Node2D" parent="."]
script = ExtResource("1")
"""

RS_BASE_TSCN = """[gd_scene format=3]

[node name="RBase" type="Node2D"]

[node name="BaseSpr" type="Sprite2D" parent="."]
"""

RS_DERIVED_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://rs_base.tscn" id="1"]

[node name="RBase" instance=ExtResource("1")]
"""

RS_SUB_TSCN = """[gd_scene format=3]

[node name="RSub" type="Node2D"]

[node name="SubSpr" type="Sprite2D" parent="."]
"""

RS_INST_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://rs_sub.tscn" id="1"]

[node name="RRoot" type="Node2D"]

[node name="Inst" parent="." instance=ExtResource("1")]
"""


def write_rs_fixtures(proj):
    # Written only after every existing session, so session W's resource listing
    # never names these files and rows 1-1222 stay byte-identical. The PNG is a
    # new imported asset: the first editor launch that scans it imports it, the
    # same way a freshly added texture is imported.
    with open(os.path.join(proj, "rs_tex.png"), "wb") as f:
        f.write(_make_png())
    # A plain-text file in the project so the non-resource-path rejection names
    # an existing file, not a missing one. Written here (after every earlier
    # session) so session W's listing never names it.
    with open(os.path.join(proj, "ordinary.txt"), "w") as f:
        f.write("gp-073 ordinary.txt: plain text, not a Resource.\n")
    for name, text in [
        ("rs_export.gd", RS_EXPORT_GD),
        ("rs_shape.tres", RS_SHAPE_TRES),
        ("rs_mat.tres", RS_MAT_TRES),
        ("rs_main.tscn", RS_MAIN_TSCN),
        ("rs_base.tscn", RS_BASE_TSCN),
        ("rs_derived.tscn", RS_DERIVED_TSCN),
        ("rs_sub.tscn", RS_SUB_TSCN),
        ("rs_inst.tscn", RS_INST_TSCN),
    ]:
        with open(os.path.join(proj, name), "w") as f:
            f.write(text)


def rset(node, prop, value_json):
    # A set_property whose value is written as a literal JSON fragment, so an
    # embedded object and a plain res:// string are both sent exactly.
    return ("line", '{"command":"set_property","node_path":"%s","property":"%s","value":%s,"project_path":"<PROJ>"}'
            % (node, prop, value_json))


def build_rs():
    # Session RS: resource assignment on rs_main.tscn. Accepted external and
    # embedded assignments run first, each followed by an inspect read; every
    # rejection is bracketed by identical scene_tree snapshots, and the file hash
    # is unchanged until the explicit save_scene at the end.
    out = [
        ("line", '{"command":"status"}'),
        ("file_sha256", "rs_main.tscn"),
        ("line", '{"command":"scene_tree"}'),
    ]
    # Accepted: an external texture, an external shape, an external material
    # (both allow-list entries), and an embedded shape configured before assign.
    out.append(rset("Spr", "texture", '"res://rs_tex.png"'))
    out.append(ins("Spr"))
    out.append(("line", '{"command":"inspect_resource","resource_path":"res://rs_tex.png","project_path":"<PROJ>"}'))
    out.append(rset("Col", "shape", '"res://rs_shape.tres"'))
    out.append(rset("SprM", "material", '"res://rs_mat.tres"'))
    out.append(rset("ShapeA", "shape", '{"class":"RectangleShape2D","properties":{"size":[30,40]}}'))
    out.append(ins("ShapeA"))
    out.append(ins("ShapeB"))
    # An `inspect-node` of `.` taken before the rejection block records the
    # `script` entry on the scene root (still `value: null, supported: false`).
    out.append(ins("."))
    # The non-resource-path rejection names res://ordinary.txt; prove the
    # fixture is on disk before the request runs (the file is written by
    # write_rs_fixtures in this session's staging, so any earlier session's
    # rows never see it).
    out.append(("file_contains", 'ordinary.txt|gp-073 ordinary.txt: plain text, not a Resource.'))
    rejections = [
        rset("Spr", "texture", '"res://rs_missing.png"'),
        rset("Spr", "texture", '"res://rs_shape.tres"'),
        rset("Spr", "texture", '"notres://x"'),
        rset("Spr", "texture", '"res://ordinary.txt"'),
        rset("Col", "shape", '{"class":"NoSuchClass"}'),
        rset("Col", "shape", '{"class":"Shape2D"}'),
        rset("Col", "shape", '{"class":"Node2D"}'),
        rset("SprM", "material", '{"class":"StandardMaterial3D"}'),
        rset("Col", "shape", '{"class":"RectangleShape2D","bogus":1}'),
        rset("Col", "shape", '{"properties":{}}'),
        rset("Col", "shape", '{"class":5}'),
        rset("Col", "shape", '{"class":"RectangleShape2D","properties":5}'),
        rset("Col", "shape", '{"class":"RectangleShape2D","properties":{"nosuch":1}}'),
        rset("Col", "shape", '{"class":"RectangleShape2D","properties":{"resource_local_to_scene":true}}'),
        rset("Col", "shape", '{"class":"RectangleShape2D","properties":{"size":"nope"}}'),
        rset("Mesh", "material_override", '{"class":"StandardMaterial3D","properties":{"albedo_texture":"res://rs_tex.png"}}'),
        rset("Hold", "target_node", '"res://rs_tex.png"'),
        rset("Spr", "texture", "null"),
        rset("Spr", "texture", "42"),
        # Script attachment bypasses (F1): the node's own `script` property is
        # an Object property with PROPERTY_HINT_RESOURCE_TYPE, but script
        # attachment belongs to the planned `attach-script` command. The three
        # value shapes (a plain res:// path string, a syntax-error path, and an
        # embedded GDScript) all keep today's exact Object refusal and leave
        # `get_script()` unchanged.
        rset(".", "script", '"res://rs_export.gd"'),
        rset(".", "script", '"res://rs_missing.gd"'),
        rset(".", "script", '{"class":"GDScript"}'),
    ]
    for request in rejections:
        out.append(("line", '{"command":"scene_tree"}'))
        out.append(request)
        out.append(("line", '{"command":"scene_tree"}'))
    # (No additional file check needed for ordinary.txt; the existence check
    # above runs before the rejection loop.)
    # The three script rejections must leave `get_script()` unchanged. The
    # mirror `inspect-node` rows here (taken after the rejection block ran)
    # must equal the `ins(".")` taken before: the `script` entry on `.`
    # stays `value: null, supported: false`. Hold.script is also encoded as
    # unsupported null; script identity is checked by the separate direct
    # get_script() probe.
    out.append(ins("."))
    out.append(ins("Hold"))
    # No implicit save yet: the file is unchanged and holds no texture/shape rows.
    out.append(("file_sha256", "rs_main.tscn"))
    out.append(("file_not_contains", 'rs_main.tscn|texture = '))
    out.append(("file_not_contains", 'rs_main.tscn|SubResource('))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'rs_main.tscn|texture = ExtResource('))
    out.append(("file_contains", 'rs_main.tscn|shape = SubResource('))
    out.append(("file_contains", 'rs_main.tscn|material = ExtResource('))
    return out


def build_ru():
    # Session RU: a fresh editor on the rs_main.tscn that session RS saved. The
    # assigned texture, the embedded shape and material, and the still-shared
    # external shape must all survive the reload, and an untouched Object
    # property stays null.
    return [
        ("line", '{"command":"status"}'),
        ins("Spr"),
        ins("ShapeA"),
        ins("SprM"),
        ins("ShapeB"),
        ("file_contains", 'rs_main.tscn|texture = ExtResource('),
    ]


def build_rd():
    # Session RD: assign a resource to a node inherited from a base scene. The
    # assignment is accepted and the derived scene saves an override row.
    return [
        ("line", '{"command":"status"}'),
        ("line", '{"command":"scene_tree"}'),
        rset("BaseSpr", "texture", '"res://rs_tex.png"'),
        ("line", '{"command":"save_scene","project_path":"<PROJ>"}'),
        ("file_contains", 'rs_derived.tscn|name="BaseSpr" parent="." index="0"'),
        ("file_contains", 'rs_derived.tscn|texture = ExtResource('),
    ]


def build_ri():
    # Session RI: assign a resource to a node inside an instanced sub-scene
    # without Editable Children. The existing instance guard answers before any
    # resource is loaded; the instance root itself still accepts a change.
    return [
        ("line", '{"command":"status"}'),
        rset("Inst/SubSpr", "texture", '"res://rs_tex.png"'),
        rset("Inst", "position", "[1,2]"),
    ]


AS_MAIN_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://as_sub.tscn" id="1"]

[node name="ASMain" type="Node2D"]

[node name="A" type="Node2D" parent="."]
[node name="B" type="Node2D" parent="."]
[node name="C" type="Node3D" parent="."]
[node name="Plain" type="Node" parent="."]
[node name="T1" type="Node2D" parent="."]
[node name="T2" type="Node2D" parent="."]
[node name="T3" type="Node2D" parent="."]
[node name="R1" type="Node2D" parent="."]
[node name="R2" type="Node2D" parent="."]
[node name="R3" type="Node2D" parent="."]
[node name="R4" type="Node2D" parent="."]
[node name="R5" type="Node2D" parent="."]
[node name="R6" type="Node2D" parent="."]
[node name="R7" type="Node2D" parent="."]
[node name="R8" type="Node2D" parent="."]
[node name="R9" type="Node2D" parent="."]
[node name="R10" type="Node2D" parent="."]
[node name="R11" type="Node2D" parent="."]
[node name="R12" type="Node2D" parent="."]
[node name="R13" type="Node2D" parent="."]
[node name="R14" type="Node2D" parent="."]
[node name="R15" type="Node2D" parent="."]
[node name="Inst" parent="." instance=ExtResource("1")]
"""

AS_SUB_TSCN = """[gd_scene format=3]

[node name="SubRoot" type="Node2D"]

[node name="Inner" type="Node2D" parent="."]

[node name="InnerDeep" type="Node2D" parent="Inner"]
"""

AS_BASE_TSCN = """[gd_scene format=3]

[node name="BaseRoot" type="Node2D"]

[node name="BaseChild" type="Node2D" parent="."]
"""

AS_DERIVED_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://as_base.tscn" id="1"]

[node name="BaseRoot" instance=ExtResource("1")]
"""

AS_GOOD2D_GD = """extends Node2D
"""

AS_TOOL2D_GD = """@tool
extends Node2D

func _init() -> void:
\tprint("AS_TOOL_INIT")
"""

AS_EXPORT2D_GD = """extends Node2D
@export var label := "e2"
"""

AS_EXPORT2D_NEW_GD = """extends Node2D
@export var label := "e2n"
"""

AS_EXPORT_GD = """extends Node2D
@export var speed := 1.0
@export var label := "base"
"""

AS_CUSTOM_BASE_GD = """class_name CustomBase2D
extends Node2D
"""

AS_RELBASE_BASE_GD = """extends Node2D
"""

AS_RELBASE_GD = """extends "as_relbase_base.gd"
"""

AS_STALE_GD = """extends Node2D
var s := 1
"""

AS_SYNTAX_BROKEN_GD = """extends Node2D
func f(:
"""

AS_ANALYSIS_ERROR_GD = """extends Node2D
func f() -> void:
\tundefined_thing.call()
"""

AS_NOT_YET_SCANNED_GD = """extends SomeClassName
"""

AS_ABSTRACT_GD = """@abstract
extends Node
"""

AS_NOTASCRIPT_TRES = """[gd_resource type="Resource" format=3]

[resource]
"""

AS_DUMMY_CS = """using Godot;
public partial class AsDummy : Node2D {
}
"""

# One bracket row reads the per-node script map, the scene history size and
# has_undo in a single eval, the same three facts probe.md bracketed every
# refusal with. The scene's UndoRedo comes from the manager, which has no
# history count of its own.
AS_BRACKET_CODE = ("extends RefCounted\nfunc run(editor, undo_redo):\n"
    "\tvar root = editor.get_edited_scene_root()\n"
    "\tvar h = undo_redo.get_history_undo_redo(undo_redo.get_object_history_id(root))\n"
    "\tvar scripts = {}\n"
    "\tvar stack = [root]\n"
    "\twhile not stack.is_empty():\n"
    "\t\tvar n = stack.pop_back()\n"
    "\t\tvar rp = root.get_path_to(n)\n"
    "\t\tvar key = \".\" if str(rp) == \"\" else str(rp)\n"
    "\t\tvar s = n.get_script()\n"
    "\t\tscripts[key] = (str(s.resource_path) if s != null else null)\n"
    "\t\tfor c in n.get_children():\n"
    "\t\t\tstack.push_back(c)\n"
    "\treturn {\"history\": h.get_history_count(), \"has_undo\": h.has_undo(), \"scripts\": scripts}\n")

# Rewrites as_stale.gd to a syntax error mid-session, so the next attach
# proves the cached copy is used (the no-re-read decision).
AS_REWRITE_CODE = ("extends RefCounted\nfunc run(editor, undo_redo):\n"
    "\tvar w = FileAccess.open(\"res://as_stale.gd\", FileAccess.WRITE)\n"
    "\tw.store_string(\"extends Node2D\\nfunc f(:\\n\")\n"
    "\tw.close()\n"
    "\treturn \"rewrote\"\n")

AS_GETSCRIPT_CODE = ("extends RefCounted\nfunc run(editor, undo_redo):\n"
    "\tvar n = editor.get_edited_scene_root().get_node(\"BaseChild\")\n"
    "\tvar s = n.get_script()\n"
    "\treturn (str(s.resource_path) if s != null else null)\n")


def as_eval(code):
    return ("line", json.dumps({"command": "eval", "project_path": "<PROJ>",
                                "code": code}, separators=(",", ":")))


def as_attach(node, script=None, detach=False, replace=False,
              project="<PROJ>"):
    d = {"command": "attach_script", "node_path": node,
         "project_path": project}
    if script is not None:
        d["script_path"] = script
    if detach:
        d["detach"] = True
    if replace:
        d["replace"] = True
    return ("line", json.dumps(d, separators=(",", ":")))


def as_bracket(out, request):
    out.append(as_eval(AS_BRACKET_CODE))
    out.append(request)
    out.append(as_eval(AS_BRACKET_CODE))


def write_as_fixtures(proj):
    # Written only after every existing session, so session W's listings never
    # name these files and rows 1-1325 stay byte-identical.
    with open(os.path.join(proj, "as_pic.png"), "wb") as f:
        f.write(_make_png())
    for name, text in [
        ("as_good2d.gd", AS_GOOD2D_GD),
        ("as_tool2d.gd", AS_TOOL2D_GD),
        ("as_export2d.gd", AS_EXPORT2D_GD),
        ("as_export2d_new.gd", AS_EXPORT2D_NEW_GD),
        ("as_export.gd", AS_EXPORT_GD),
        ("as_custom_base.gd", AS_CUSTOM_BASE_GD),
        ("as_relbase_base.gd", AS_RELBASE_BASE_GD),
        ("as_relbase.gd", AS_RELBASE_GD),
        ("as_stale.gd", AS_STALE_GD),
        ("as_syntax_broken.gd", AS_SYNTAX_BROKEN_GD),
        ("as_analysis_error.gd", AS_ANALYSIS_ERROR_GD),
        ("as_not_yet_scanned.gd", AS_NOT_YET_SCANNED_GD),
        ("as_abstract.gd", AS_ABSTRACT_GD),
        ("as_notascript.tres", AS_NOTASCRIPT_TRES),
        ("as_dummy.cs", AS_DUMMY_CS),
        ("as_sub.tscn", AS_SUB_TSCN),
        ("as_base.tscn", AS_BASE_TSCN),
        ("as_derived.tscn", AS_DERIVED_TSCN),
        ("as_main.tscn", AS_MAIN_TSCN),
    ]:
        with open(os.path.join(proj, name), "w") as f:
            f.write(text)


def build_as():
    # Session AS: the attach-script matrix on as_main.tscn, then one attach on
    # the base-inherited node of as_derived.tscn with a property set and a
    # save. Every refusal and every accepted request is bracketed by the
    # script-map, history-size and has-undo eval, so a refusal that changed
    # anything would show and each accepted request shows exactly +1.
    out = [
        ("line", '{"command":"status"}'),
    ]
    as_bracket(out, as_attach("R1", "res://as_missing.gd"))
    as_bracket(out, as_attach("R6", "user://as_good2d.gd"))
    as_bracket(out, as_attach("R9", "res://as_main.tscn::GDScript_abc"))
    as_bracket(out, as_attach("R8", "res://as_good2d.gd "))
    as_bracket(out, as_attach("R3", "res://as_main.tscn"))
    as_bracket(out, as_attach("R3", "res://as_pic.png"))
    as_bracket(out, as_attach("R3", "res://as_dummy.cs"))
    as_bracket(out, as_attach("R3", "res://as_notascript.tres"))
    as_bracket(out, as_attach("R5", "res://as_abstract.gd"))
    as_bracket(out, as_attach("T1", "res://as_syntax_broken.gd"))
    as_bracket(out, as_attach("T2", "res://as_analysis_error.gd"))
    as_bracket(out, as_attach("T3", "res://as_not_yet_scanned.gd"))
    as_bracket(out, as_attach("C", "res://as_good2d.gd"))
    as_bracket(out, as_attach("Plain", "res://as_good2d.gd"))
    as_bracket(out, as_attach("Inst/Inner/InnerDeep", "res://as_good2d.gd"))
    as_bracket(out, as_attach("R14", "res://as_good2d.gd",
                              project="/private/tmp/other"))
    as_bracket(out, ("line", '{"command":"attach_script","node_path":"R15","project_path":"<PROJ>","script_path":5}'))
    as_bracket(out, as_attach("A", "res://as_good2d.gd"))
    as_bracket(out, as_attach("A", "res://as_export2d.gd"))
    as_bracket(out, as_attach("A", "res://as_good2d.gd", replace=True))
    # Alias paths that resolve to the script the node already holds. The
    # build under test does not resolve them yet, so these attach today;
    # the accepted repair refuses them, and the rows are re-recorded then.
    as_bracket(out, as_attach("A", "res://./as_good2d.gd", replace=True))
    as_bracket(out, as_attach("A", "res://T1/../as_good2d.gd", replace=True))
    # An alias without --replace never reaches the alias rule: the earlier
    # current-script refusal answers first.
    as_bracket(out, as_attach("A", "res://./as_good2d.gd"))
    as_bracket(out, as_attach("A", "res://as_export2d.gd", detach=True))
    as_bracket(out, as_attach("A", detach=True, replace=True))
    as_bracket(out, as_attach("A"))
    as_bracket(out, as_attach("R15", detach=True))
    as_bracket(out, as_attach("B", "res://as_tool2d.gd"))
    as_bracket(out, as_attach("B", "res://as_export2d.gd", replace=True))
    as_bracket(out, as_attach("B", "res://as_export2d_new.gd", replace=True))
    as_bracket(out, as_attach("R6", "res://as_good2d.gd"))
    as_bracket(out, as_attach("R6", detach=True))
    as_bracket(out, as_attach("A", "res://as_tool2d.gd", replace=True))
    as_bracket(out, as_attach("B", "res://as_tool2d.gd", replace=True))
    as_bracket(out, as_attach("R10", "res://as_custom_base.gd"))
    as_bracket(out, as_attach("R11", "res://as_relbase.gd"))
    as_bracket(out, as_attach("T1", "res://as_stale.gd"))
    out.append(as_eval(AS_REWRITE_CODE))
    as_bracket(out, as_attach(".", "res://as_stale.gd"))
    as_bracket(out, as_attach("Inst", "res://as_good2d.gd"))
    out.append(("line", '{"command":"open_scene","project_path":"<PROJ>","scene_path":"res://as_derived.tscn","save":false}'))
    as_bracket(out, as_attach("BaseChild", "res://as_export.gd"))
    out.append(rset("BaseChild", "speed", "42.0"))
    out.append(("line", '{"command":"save_scene","project_path":"<PROJ>"}'))
    out.append(("file_contains", 'as_derived.tscn|name="BaseChild"'))
    out.append(("file_contains", 'as_derived.tscn|script = ExtResource('))
    return out


def build_asr():
    # Session ASR: a fresh editor on the as_derived.tscn that session AS saved.
    # The attached script and the changed exported value must have survived.
    return [
        ("line", '{"command":"status"}'),
        as_eval(AS_BRACKET_CODE),
        ins("BaseChild"),
        as_eval(AS_GETSCRIPT_CODE),
        ("file_contains", 'as_derived.tscn|name="BaseChild"'),
        ("file_contains", 'as_derived.tscn|speed = 42'),
    ]


# The order rows are stored in, which is not the order the sessions run in.
# Each session writes its rows at its own place here, so a session that
# --sessions left out leaves a gap instead of shifting the rows after it.
SESSION_ORDER = ("A", "B", "C", "E", "D", "F", "G", "H", "I", "J", "K", "L",
                 "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y",
                 "CONSOLE", "Z", "AA", "AB", "AC", "AD", "AE", "AF", "AG",
                 "AH", "AI", "AJ", "AK", "EVAL", "RS", "RU", "RD", "RI",
                 "AS", "ASR")

# The request list of each session, in storage order. A session records exactly
# one row per request, so the length of these lists is the stored row layout.
# The builders build plain lists and touch no file, so this is safe to evaluate
# once at import. compare.py reads the layout from here, so the row count of a
# session is never written down twice.
SESSION_REQUESTS = (
    ("A", REQ_A), ("B", build_b()), ("C", build_c()), ("E", build_e()),
    ("D", build_d()), ("F", build_f()), ("G", build_g()), ("H", build_h()),
    ("I", build_i()), ("J", build_j()), ("K", build_k()), ("L", build_l()),
    ("N", build_n()), ("O", build_o()), ("P", build_p()), ("Q", build_q()),
    ("R", build_r()), ("S", build_s()), ("T", build_t()), ("U", build_u()),
    ("V", build_v()), ("W", build_w()), ("X", build_x()), ("Y", build_y()),
    ("CONSOLE", build_console()), ("Z", build_z()), ("AA", build_aa()),
    ("AB", build_ab()), ("AC", build_ac()), ("AD", build_ad()),
    ("AE", build_ae()), ("AF", build_af()), ("AG", build_ag()),
    ("AH", build_ah()), ("AI", build_ai()), ("AJ", build_aj()),
    ("AK", build_ak()), ("EVAL", build_eval()), ("RS", build_rs()),
    ("RU", build_ru()), ("RD", build_rd()), ("RI", build_ri()),
    ("AS", build_as()), ("ASR", build_asr()),
)


def stored_layout():
    """(name, first baseline row, row count) in storage order, 1-based. A
    filtered run writes these for the sessions it stored, so compare.py can find
    those rows in the baseline without being told where they are."""
    out = []
    start = 1
    for name, requests in SESSION_REQUESTS:
        count = len(requests)
        out.append((name, start, count))
        start += count
    return out


# Measured by replaying each of these together with the session it reads state
# from and comparing the result against the baseline: these thirteen sessions
# do not reproduce on their own, because their rows read nodes, saved files or
# a message counter that an earlier session left behind. The sessions named
# here are replayed first. A session that reproduces alone is not listed.
# None of the listed prerequisites needs one of its own, so one level is
# enough.
SESSION_PREREQUISITES = {
    "AA": ("Z",),
    "AC": ("AB",),
    "AH": ("AG",),
    "AK": ("AI", "AJ"),
    "ASR": ("AS",),
    "CONSOLE": ("A", "Y"),
    "F": ("D",),
    "I": ("H",),
    "L": ("K",),
    "O": ("N",),
    "Q": ("P",),
    "RU": ("RS",),
    "S": ("R",),
    "V": ("U",),
}


def with_prerequisites(wanted):
    """The wanted sessions plus every session their rows depend on, and those
    prerequisites' own. A prerequisite is replayed but stores no rows, so the
    output still holds only the sessions the caller asked for."""
    running = set(wanted)
    pending = list(wanted)
    while pending:
        for need in SESSION_PREREQUISITES.get(pending.pop(), ()):
            if need not in running:
                running.add(need)
                pending.append(need)
    return running


def parse_sessions(value):
    """Session names to replay, or None for all of them. Names are matched
    without regard to case and may be given in any order; the run order stays
    the order of the calls in main."""
    if value is None:
        return None
    canonical = {name.lower(): name for name in SESSION_ORDER}
    chosen = set()
    unknown = []
    for part in value.split(","):
        name = canonical.get(part.strip().lower())
        if name is None:
            if part.strip():
                unknown.append(part.strip())
        else:
            chosen.add(name)
    if unknown:
        raise SystemExit("unknown session name in --sessions: %s; valid names: %s"
                         % (", ".join(unknown), ", ".join(SESSION_ORDER)))
    if not chosen:
        raise SystemExit("--sessions names no known session; valid names: %s"
                         % ", ".join(SESSION_ORDER))
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-dir", required=True)
    ap.add_argument("--port", type=int, default=47902)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sessions", help="comma-separated session names to replay "
                    "instead of all of them")
    a = ap.parse_args()
    if a.port == 47821:
        raise SystemExit("port 47821 is Bruno's live editor; refusing")
    gd = os.path.join(a.plugin_dir, "editor_plugin.gd")
    if not os.path.isfile(gd):
        raise SystemExit("no editor_plugin.gd in --plugin-dir")
    # Refused here so that a bad name stops the run before an editor starts.
    selected = parse_sessions(a.sessions)
    if selected is None:
        wanted, running = SESSION_ORDER, SESSION_ORDER
    else:
        wanted = selected
        running = with_prerequisites(selected)
        added = [n for n in SESSION_ORDER if n in running and n not in wanted]
        if added:
            print("prerequisite sessions added, their rows are not stored: %s"
                  % " ".join(added))
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
            ("connect_fixture.gd", CONNECT_FIXTURE_GD),
            ("connect_nomethod.gd", CONNECT_NOMETHOD_GD),
            ("connect_sub.gd", CONNECT_SUB_GD),
            ("connect_sub.tscn", CONNECT_SUB_TSCN),
            ("connect_mid.tscn", CONNECT_MID_TSCN),
            ("connect.tscn", CONNECT_TSCN),
            ("group_sub.tscn", GROUP_SUB_TSCN),
            ("group_deep.tscn", GROUP_DEEP_TSCN),
            ("group_mid.tscn", GROUP_MID_TSCN),
            ("group.tscn", GROUP_TSCN),
            ("unique_sub.tscn", UNIQUE_SUB_TSCN),
            ("unique_deep.tscn", UNIQUE_DEEP_TSCN),
            ("unique_mid.tscn", UNIQUE_MID_TSCN),
            ("unique.tscn", UNIQUE_TSCN),
            ("rename_sub.tscn", RENAME_SUB_TSCN),
            ("rename_sub_plain.tscn", RENAME_SUB_PLAIN_TSCN),
            ("rename_r1.tscn", RENAME_R1_TSCN),
            ("rename_r2.tscn", RENAME_R2_TSCN),
            ("rename_r3.tscn", RENAME_R3_TSCN),
            ("rename_r4.tscn", RENAME_R4_TSCN),
            ("rename_r5.tscn", RENAME_R5_TSCN),
            ("rename_r6.tscn", RENAME_R6_TSCN),
            ("rename_r7.tscn", RENAME_R7_TSCN),
            ("rename_r8.tscn", RENAME_R8_TSCN),
            ("rename_r9.tscn", RENAME_R9_TSCN),
            ("rename_r10.tscn", RENAME_R10_TSCN),
            ("rename_case1_reject.tscn", RENAME_CASE1_REJECT_TSCN),
            ("rename_case1_accept.tscn", RENAME_CASE1_ACCEPT_TSCN),
            ("rename_stem_reject.tscn", RENAME_STEM_REJECT_TSCN),
            ("rename_stem_accept.tscn", RENAME_STEM_ACCEPT_TSCN),
            ("rename_pad_reject.tscn", RENAME_PAD_REJECT_TSCN),
            ("rename_pad_accept.tscn", RENAME_PAD_ACCEPT_TSCN),
            ("rename_conservative.tscn", RENAME_CONSERVATIVE_TSCN),
            ("inherit_sub.tscn", INHERIT_SUB_TSCN),
            ("inherit_sub_u.tscn", INHERIT_SUB_U_TSCN),
            ("inherit_t.tscn", INHERIT_T_TSCN),
            ("inherit_i1.tscn", INHERIT_I1_TSCN),
            ("inherit_i2.tscn", INHERIT_I2_TSCN),
            ("inherit_i3.tscn", INHERIT_I3_TSCN),
            ("inherit_i4.tscn", INHERIT_I4_TSCN),
            ("inherit_i5.tscn", INHERIT_I5_TSCN),
            ("inherit_i6.tscn", INHERIT_I6_TSCN),
            ("inherit_i7.tscn", INHERIT_I7_TSCN),
            ("inherit_i8.tscn", INHERIT_I8_TSCN),
            ("inherit_i9.tscn", INHERIT_I9_TSCN),
            ("inst_plain.tscn", INST_PLAIN_TSCN),
            ("inst_sub.tscn", INST_SUB_TSCN),
            ("inst_control.tscn", INST_CONTROL_TSCN),
            ("inst_of_sub.tscn", INST_OF_SUB_TSCN),
            ("inst_e.tscn", INST_E_TSCN),
            ("inst_t.tscn", INST_T_TSCN),
            ("inst_derived_e.tscn", INST_DERIVED_E_TSCN),
            ("inst_missing_dep.tscn", INST_MISSING_DEP_TSCN),
            ("inst_broken.tscn", INST_BROKEN_TSCN),
            ("inst_note.txt", INST_NOTE_TXT),
            ("inst_script.gd", INST_SCRIPT_GD),
            ("inst_base.tscn", INST_BASE_TSCN),
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
        # The commands a later session adds are staged out of the copied
        # plugin while sessions A..W record, because session W's listing rows
        # name the commands folder; console, set_parent and eval are restored
        # together before session Y, so the earlier rows stay byte-identical to
        # the previous baseline and the interface rows carry all three new
        # entries. The complete port-patched source is kept so they can be
        # restored in one write.
        unstaged_plugin = patched
        staged = []
        for rel, line, anchor in STAGED_COMMANDS:
            abs_path = os.path.join(dst, rel)
            if os.path.exists(abs_path):
                os.remove(abs_path)
                if patched.count(line) != 1:
                    raise SystemExit("%s COMMANDS line not unique" % rel)
                patched = patched.replace(line + "\n", "")
                staged.append((rel, line, anchor))
        # console also wires a Logger into editor_plugin.gd (a const preload
        # plus one block in _enter_tree and one in _exit_tree), so all four
        # snippets are removed together; otherwise the copied plugin fails to
        # parse and no session records.
        console_rel = os.path.join("commands", "console.gd")
        console_abs = os.path.join(dst, console_rel)
        console_cmd_line = '\t"console": preload("commands/console.gd"),'
        console_const_line = 'const ConsoleCommand := preload("commands/console.gd")'
        console_enter_block = ('\tif _console_logger == null:\n'
                               '\t\t_console_logger = ConsoleCommand.Capture.new()\n'
                               '\t\tOS.add_logger(_console_logger)\n')
        console_exit_block = ('\tif _console_logger != null:\n'
                              '\t\tOS.remove_logger(_console_logger)\n'
                              '\t\t_console_logger = null\n')
        console_staged = os.path.exists(console_abs)
        if console_staged:
            os.remove(console_abs)
            for snippet, what in [(console_cmd_line + "\n", "console COMMANDS line"),
                                  (console_const_line + "\n", "console const line"),
                                  (console_enter_block, "console _enter_tree block"),
                                  (console_exit_block, "console _exit_tree block")]:
                if patched.count(snippet) != 1:
                    raise SystemExit("%s not unique" % what)
                patched = patched.replace(snippet, "", 1)
        with open(target, "w") as f:
            f.write(patched)
        r = subprocess.run([GODOT, "--headless", "--path", proj, "--import"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=180)
        if r.returncode != 0:
            raise SystemExit("godot --import failed")
        rows = []
        blocks = {}

        def play(name, scene, requests, expected_scene, ready_filesystem=False):
            """Run one session, or skip it when --sessions left it out. Every
            staging step and fixture write still happens, so a selected
            session meets the same files it meets in a full run; only the
            editor launch and the requests of a session that neither runs nor
            was asked for are skipped."""
            if name not in running:
                blocks[name] = []
                return
            got = run_session(a.port, proj, scene, requests,
                              expected_scene, ready_filesystem)
            # A prerequisite runs to rebuild the state its dependant reads, but
            # only a session the caller named stores rows.
            blocks[name] = got if name in wanted else []
        # Run order is A, E, G, J, T, W, X, Y, CONSOLE, EVAL, C, B, D, F, H, I,
        # K, L, N, O, P, Q, R, S, U, V, then the set-parent sessions Z, AA, AB,
        # AC, AD-AG, AH, AI, AJ, AK: a fresh editor restores the previous
        # session's open scenes, so the no-scene sessions (A, E, G, J, T, W, X,
        # Y, the console session and the eval session, and C before it opens
        # anything) must run before the scene sessions. Rows are stored A, B, C,
        # E, D, F, G, H, I, J, K, L, N, O, P, Q, R, S, then the T, U and V rows,
        # then the W rows, then the X rows, then the Y rows, then the console
        # rows, then the set-parent rows, then the eval rows, so the first 825
        # rows stay byte-identical to the previous baseline; rows 826 and 827
        # (the interface) publish all three new commands, and the new rows are
        # appended after them.
        play("A", None, REQ_A, None)
        play("E", None, build_e(), None)
        play("G", None, build_g(), None)
        play("J", None, build_j(), None)
        play("T", None, build_t(), None)
        play("W", None, build_w(), None, ready_filesystem=True)
        # Restore the staged commands into the copied plugin one at a time and
        # write each session's fixtures only when its command is present, so no
        # earlier session's rows see them (session W's listing rows keep the
        # pre-existing command set). The generated .res bytes of session X are
        # deterministic (measured byte-identical across project paths).
        def restore_staged(rel):
            entry = next((item for item in staged if item[0] == rel), None)
            if entry is None:
                return
            _, line, anchor = entry
            shutil.copy2(os.path.join(a.plugin_dir, rel), os.path.join(dst, rel))
            with open(target) as f:
                current_plugin = f.read()
            if current_plugin.count(line) != 0:
                raise SystemExit("%s COMMANDS line already present" % rel)
            if current_plugin.count(anchor) != 1:
                raise SystemExit("%s anchor not unique" % anchor)
            current_plugin = current_plugin.replace(anchor, anchor + "\n" + line, 1)
            with open(target, "w") as f:
                f.write(current_plugin)

        restore_staged(os.path.join("commands", "inspect_resource.gd"))
        write_inspect_fixtures(proj)
        play("X", None, build_x(), None)
        # console, set_parent and eval are restored together before session Y,
        # so the published interface row carries all three as its last entries.
        # The complete source already holds the inspect-resource line the
        # incremental restore put back, so it is written directly; the three
        # command files are copied back too. The set-parent fixtures are written
        # now as well, so the earlier sessions never listed them.
        for rel in ("console.gd", "set_parent.gd", "eval.gd",
                    "attach_script.gd"):
            shutil.copy2(os.path.join(a.plugin_dir, "commands", rel),
                         os.path.join(dst, "commands", rel))
        with open(target, "w") as f:
            f.write(unstaged_plugin)
        play("Y", None, build_y(), None)
        # The console session and the eval session run right after the
        # interface with no edited scene (the eval session opens main.tscn in
        # its last two rows), so they run before the first scene session. The
        # eval fixture is written only now and the set-parent fixtures are
        # written only before the set-parent sessions: session W lists the
        # project files, so a file added earlier would change its rows, and no
        # session after Y lists resources.
        play("CONSOLE", None, build_console(), None)
        with open(os.path.join(proj, "eval_bad_preload.gd"), "w") as f:
            f.write(EVAL_BAD_PRELOAD_GD)
        play("EVAL", None, build_eval(), None)
        if "EVAL" in wanted:
            check_eval_display(blocks["EVAL"])
        play("C", None, build_c(), None)
        play("B", "res://main.tscn", build_b(), "res://main.tscn")
        play("D", "res://connect.tscn", build_d(), "res://connect.tscn")
        play("F", "res://connect.tscn", build_f(), "res://connect.tscn")
        play("H", "res://group.tscn", build_h(), "res://group.tscn")
        play("I", "res://group.tscn", build_i(), "res://group.tscn")
        play("K", "res://unique.tscn", build_k(), "res://unique.tscn")
        play("L", "res://unique.tscn", build_l(), "res://unique.tscn")
        play("N", "res://rename_r1.tscn", build_n(), "res://rename_r1.tscn")
        play("O", "res://rename_r2.tscn", build_o(), "res://rename_r2.tscn")
        play("P", "res://rename_case1_reject.tscn", build_p(), "res://rename_case1_reject.tscn")
        play("Q", "res://rename_case1_accept.tscn", build_q(), "res://rename_case1_accept.tscn")
        play("R", "res://inherit_i3.tscn", build_r(), "res://inherit_i3.tscn")
        play("S", "res://inherit_i1.tscn", build_s(), "res://inherit_i1.tscn")
        play("U", "res://inst_base.tscn", build_u(), "res://inst_base.tscn")
        play("V", "res://inst_base.tscn", build_v(), "res://inst_base.tscn")
        write_set_parent_fixtures(proj)
        play("Z", "res://sp_base.tscn", build_z(), "res://sp_base.tscn")
        play("AA", "res://sp_base.tscn", build_aa(), "res://sp_base.tscn")
        play("AB", "res://sp_dderived.tscn", build_ab(), "res://sp_dderived.tscn")
        play("AC", "res://sp_dderived.tscn", build_ac(), "res://sp_dderived.tscn")
        write_sp2_fixtures(proj)
        play("AD", "res://sp2_override.tscn", build_ad(), "res://sp2_override.tscn")
        play("AE", "res://sp2_plain.tscn", build_ae(), "res://sp2_plain.tscn")
        play("AF", "res://sp2_twolevel.tscn", build_af(), "res://sp2_twolevel.tscn")
        play("AG", "res://sp2_local.tscn", build_ag(), "res://sp2_local.tscn")
        play("AH", "res://sp2_local.tscn", build_ah(), "res://sp2_local.tscn")
        # The base-inherited rename and delete fixtures are written only now, so
        # the earlier listings (session W) never name them.
        write_ib_fixtures(proj)
        play("AI", "res://ib_rename.tscn", build_ai(), "res://ib_rename.tscn")
        play("AJ", "res://ib_delete.tscn", build_aj(), "res://ib_delete.tscn")
        play("AK", "res://ib_rename.tscn", build_ak(), "res://ib_rename.tscn")
        # The resource-assignment sessions run after every existing session, so
        # their fixtures are written here and their rows are appended at the end
        # of the baseline. RS saves rs_main.tscn; RU reloads it; RD and RI
        # touch the derived and instanced scenes (independent of RS, but run
        # after it so the editor's startup scan is known to have run).
        write_rs_fixtures(proj)
        play("RS", "res://rs_main.tscn", build_rs(), "res://rs_main.tscn")
        play("RU", "res://rs_main.tscn", build_ru(), "res://rs_main.tscn")
        play("RD", "res://rs_derived.tscn", build_rd(), "res://rs_derived.tscn")
        play("RI", "res://rs_inst.tscn", build_ri(), "res://rs_inst.tscn")
        # The attach-script sessions run last, so their fixtures are written
        # here and their rows are appended at the end of the baseline. AS
        # records the refusal and acceptance matrix on as_main.tscn, then one
        # attach on the base-inherited node of as_derived.tscn with a property
        # set and a save; ASR is a fresh editor on that saved scene.
        write_as_fixtures(proj)
        play("AS", "res://as_main.tscn", build_as(), "res://as_main.tscn",
             ready_filesystem=True)
        play("ASR", "res://as_derived.tscn", build_asr(),
             "res://as_derived.tscn")
        rows = [row for name in SESSION_ORDER for row in blocks[name]]
        with open(a.out, "w") as f:
            for req, rep in rows:
                f.write(json.dumps({"request": req, "reply": rep}, separators=(",", ":")) + "\n")
        print("wrote %d pairs to %s" % (len(rows), a.out))
        if selected is not None:
            print("sessions: %s" % " ".join(n for n in SESSION_ORDER if blocks[n]))
    finally:
        shutil.rmtree(proj, ignore_errors=True)


if __name__ == "__main__":
    main()
