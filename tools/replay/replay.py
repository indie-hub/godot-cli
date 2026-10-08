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
import hashlib
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


def run_session(port, proj, scene, requests, expected_scene):
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
        # Run order is A, E, G, J, C, B, D, F, H, I, K, L: a fresh editor
        # restores the previous session's open scenes, so the no-scene sessions
        # (A, E, G, J, and C before it opens anything) must run before the scene
        # sessions. Rows are stored A, B, C, E, D, F, G, H, I, J, K, L, so the
        # first 362 rows stay byte-identical to the set-group baseline and the
        # new set-unique-name rows are appended.
        rows_a = run_session(a.port, proj, None, REQ_A, None)
        rows_e = run_session(a.port, proj, None, build_e(), None)
        rows_g = run_session(a.port, proj, None, build_g(), None)
        rows_j = run_session(a.port, proj, None, build_j(), None)
        rows_c = run_session(a.port, proj, None, build_c(), None)
        rows_b = run_session(a.port, proj, "res://main.tscn", build_b(), "res://main.tscn")
        rows_d = run_session(a.port, proj, "res://connect.tscn", build_d(), "res://connect.tscn")
        rows_f = run_session(a.port, proj, "res://connect.tscn", build_f(), "res://connect.tscn")
        rows_h = run_session(a.port, proj, "res://group.tscn", build_h(), "res://group.tscn")
        rows_i = run_session(a.port, proj, "res://group.tscn", build_i(), "res://group.tscn")
        rows_k = run_session(a.port, proj, "res://unique.tscn", build_k(), "res://unique.tscn")
        rows_l = run_session(a.port, proj, "res://unique.tscn", build_l(), "res://unique.tscn")
        rows_n = run_session(a.port, proj, "res://rename_r1.tscn", build_n(), "res://rename_r1.tscn")
        rows_o = run_session(a.port, proj, "res://rename_r2.tscn", build_o(), "res://rename_r2.tscn")
        rows_p = run_session(a.port, proj, "res://rename_case1_reject.tscn", build_p(), "res://rename_case1_reject.tscn")
        rows_q = run_session(a.port, proj, "res://rename_case1_accept.tscn", build_q(), "res://rename_case1_accept.tscn")
        rows_r = run_session(a.port, proj, "res://inherit_i3.tscn", build_r(), "res://inherit_i3.tscn")
        rows_s = run_session(a.port, proj, "res://inherit_i1.tscn", build_s(), "res://inherit_i1.tscn")
        rows = (rows_a + rows_b + rows_c + rows_e + rows_d + rows_f + rows_g
                + rows_h + rows_i + rows_j + rows_k + rows_l + rows_n + rows_o
                + rows_p + rows_q + rows_r + rows_s)
        with open(a.out, "w") as f:
            for req, rep in rows:
                f.write(json.dumps({"request": req, "reply": rep}, separators=(",", ":")) + "\n")
        print("wrote %d pairs to %s" % (len(rows), a.out))
    finally:
        shutil.rmtree(proj, ignore_errors=True)


if __name__ == "__main__":
    main()
