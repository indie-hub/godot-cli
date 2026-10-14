# Godot Pipeline reference

This file is the detailed reference for the wire protocol and every command. README.md has the overview and the quick start.

## Layout

- `addon/godot_pipeline/` - the GDScript `EditorPlugin` source of truth:
  `editor_plugin.gd` holds the socket server, request framing, reply writing,
  and dispatch into one script per command under `commands/`;
  `commands/command_support.gd` holds the reply constructors, the shared
  request guard, and the shared constants, and `value_codec.gd` holds the
  JSON/Variant value conversions. Copy this folder into a Godot project's
  `addons/` directory and enable it under Project Settings > Plugins to use
  it.
- `src/protocol.rs` - the wire protocol and the synchronous TCP client.
- `src/main.rs` - the CLI: the fixed options, and the generic engine that
  builds every command's request from the interface the plugin publishes.
- `tests/schema_fixture.json` - the interface the plugin publishes, copied
  from its own reply so the engine is tested against it.

## Protocol

One TCP connection per request, on `127.0.0.1:47821` by default:

1. The client writes one line of JSON: `{"command":"status"}`,
   `{"command":"scene_tree"}`,
   `{"command":"rename_node","node_path":"...","new_name":"...","project_path":"..."}`,
   or
   `{"command":"create_node","parent_path":"...","class_name":"...","name":"...","project_path":"..."}`,
   or
   `{"command":"set_property","node_path":"...","property":"...","value":<json>,"project_path":"..."}`,
   or
   `{"command":"inspect_node","node_path":"...","project_path":"..."}`,
   or
   `{"command":"query_nodes","project_path":"...","class":<class or null>,"group":<group or null>,"name":<pattern or null>,"limit":<int>}`,
   or
   `{"command":"inspect_class","class":"...","project_path":"..."}`,
   or
   `{"command":"delete_node","node_path":"...","project_path":"..."}`,
   or
   `{"command":"connect_signal","source_path":"...","signal":"...","target_path":"...","method":"...","deferred":false,"one_shot":false,"project_path":"..."}`,
   or
   `{"command":"set_group","node_path":"...","group":"...","remove":false,"project_path":"..."}`,
   or
   `{"command":"set_unique_name","node_path":"...","remove":false,"project_path":"..."}`,
   or
   `{"command":"instantiate_scene","scene_path":"...","parent_path":"...","project_path":"..."}`,
   or
   `{"command":"save_scene","project_path":"..."}`,
   or
   `{"command":"open_scene","project_path":"...","scene_path":"...","save":false}`,
   or
   `{"command":"list_resources","project_path":"...","path_prefix":<prefix or null>,"type":<class or null>,"limit":<int>,"cursor":<path or null>,"refresh":false}`,
   or
   `{"command":"inspect_resource","project_path":"...","resource_path":"res://..."}`,
   then keeps the socket open for the reply.
2. The plugin writes one line of JSON back:
   `{"status":"ok","data":...}` or `{"status":"error","message":"..."}`.
3. Either side closes the connection after the exchange.

`inspect_node` reads a node in the currently edited scene and changes
nothing: no Undo/Redo step, no dirty flag, no save. `node_path` and
`project_path` follow the same rules as `rename_node`'s, and the reply is
`{"status":"ok","data":{"path":"...","name":"...","type":"...","child_count":...,"properties":[...]}}`
with `data.path` echoing the node path as given (`.` for the scene root),
`data.type` the node's class, and one entry per editor-visible property
(`get_property_list()` entries with inspector or storage usage, excluding
category and group headers, in engine order): `name`, the Variant type name
(`"Vector2"`, `"Array"`, ...), `value`, and `read_only`. For the value types
`set-property` accepts (bool, int, float, String, StringName, NodePath, the
vector and int-vector types, Rect2/Rect2i, Transform2D/Transform3D, Color,
the Packed*Array types, and typed `Array[T]` of those types), `value` uses
exactly the JSON shape `set-property` accepts as input, so an inspected
value can be fed back to `set-property` unchanged (Color as `[r,g,b,a]`,
Rect2/Rect2i as `[x,y,w,h]`, Transform2D as `[[xx,xy],[yx,yy],[ox,oy]]`,
Transform3D as the three basis axis vectors then the origin). For any other type
(Object/Resource references, Dictionary, untyped Array, Callable, Signal,
RID, ...) the entry has `value: null` and `supported: false`; supported
entries carry no `supported` key. A float holding `inf`, `-inf`, or `nan`
is emitted as the string `"inf"`, `"-inf"`, or `"nan"`, so the reply is
always valid JSON. Reads are allowed anywhere in the edited scene,
including inside an instanced sub-scene without Editable Children.

`query_nodes` searches the whole node tree of the currently edited scene
(root included) and changes nothing: no Undo/Redo step, no dirty flag, no
save. `project_path` follows the same rules as `rename_node`'s. The reply
is `{"status":"ok","data":{"nodes":[{"path":"...","name":"...","type":"..."},...],"truncated":false}}`;
nodes come in tree order (parent before children, siblings in order) with
`data.nodes[].path` relative to the scene root (`.` for the root), so a path
can be passed to `inspect_node` unchanged. The optional filters combine with
AND: `class` matches by `is_class` (subclasses count, so `Node2D` also
matches `Sprite2D`), `group` matches `is_in_group`, and `name` matches the
node name against the pattern as a case-sensitive Godot glob (`String.match`,
so `*` and `?` work). A `class` that is not an engine class
(`ClassDB.class_exists` fails) or that names a project `class_name` script
class is rejected with an error naming it; an unknown `group` is not an
error and simply matches nothing. `limit` defaults to 100 and must be an
integer in `1..1000`; when more nodes match than the limit, the reply holds
exactly the first `limit` matches and `data.truncated` is `true` (the search
stops early once the cap is reached). `project_path` is checked first, then
the edited scene, then the filters and limit, so a wrong project path is
reported even when a filter or limit is also invalid.

`inspect_class` reports an engine class's ClassDB reflection and changes
nothing: no Undo/Redo step, no dirty flag, no save, and no edited scene is
required. `class` must be an engine class (`ClassDB.class_exists`); a project
`class_name` script class, an empty name, or a wrong-case name is rejected
with an error naming it. `project_path` follows the same rules as
`rename_node`'s and is checked before the class, so a wrong project path is
reported even when the class is also invalid. The reply is
`{"status":"ok","data":{"class":"...","ancestors":["..."],"can_instantiate":...,"is_node":...,"properties":[...],"methods":[...],"signals":[...]}}`;
`data.ancestors` lists the nearest parent first and ends with `"Object"`
(empty for `Object` itself), `data.is_node` is true for `Node` and every
subclass, and the three lists are the class's declared members in engine
order (no inherited members). Each property entry has `name`, the Variant
type name, and `read_only`; each method entry has `name`, its `args` (name
and type), `return` (type name), and `flags` (the engine's raw integer, so
`_process` reports 8 for VIRTUAL); each signal entry has `name` and its
`args`. Object-typed members use their class name (`InputEvent`), an untyped
Variant argument or property renders `"Variant"`, and a method with no return
value renders `"void"`.

`rename_node` renames a node in the currently edited scene through the
editor's `EditorUndoRedoManager`, so the change is a single Undo/Redo step
and marks the scene dirty without saving it. `node_path` is relative to the
edited scene's root ("." selects the root itself); absolute paths and `..`
components are rejected. `project_path` must be the canonical
(symlink-resolved) absolute path of the project the caller intends to edit;
the plugin rejects the request before touching the scene if it does not
match the running editor's own project. `new_name` is rejected up front if
it is empty or contains a character Godot reserves for node names
(`. : @ / " %`); the reply's `data.name` is the name actually applied, which
can differ from `data.requested_name` if it collided with a sibling and
Godot uniquified it. When the target holds a unique name
(`unique_name_in_owner`) and the rename would make the engine clear that flag,
the rename is rejected before the undo action, naming the claimant. The check
only reads the scene; it never renames the live node, so a rejected request
runs no callback and changes nothing; an accepted request runs the engine's
own rename callbacks. When no sibling of the target holds the requested name
the engine applies it exactly, and that exact name is tested.
When a sibling holds it the engine numbers it, and the check then rejects a
unique node in the same owner scope whose name is the requested name without
its trailing digits followed by digits (for example a requested `N01` next to a
sibling `N01` is tested against every `N` followed by digits). That second
case is deliberately conservative: it can reject a rename the engine would
allow (a sibling holds `N` and a unique `N7` exists, while the engine would
apply `N2`). The unique-name check does not apply to a target without a
unique flag or to one whose owner is null (the scene root). A second check
then rejects a target that is neither the scene root nor owned by the edited
scene root: the engine accepts its rename in the open editor, but the name is
not kept after a save and a fresh reload, so such a rename would not persist.
The rejected targets are instance-owned descendants of an instanced
sub-scene, the roots of nested instances, and nodes with no owner. Instance
roots owned by the edited scene, local nodes under an editable instance, and
plain nodes rename and persist. The unique-name check runs first: an
inherited unique node renamed onto a claimed name gets the unique-name
collision message. When the edited scene itself derives from a base scene,
renaming a base-scene child (whose owner is the edited root) is accepted and,
after a save and reload, shows the new name as a new local node and the
original child again. That behavior is outside this check and unchanged.
The check reads the scene as it is before the rename. A script, a setter or a
renamed-signal observer can change the node's name during the real rename to
a different name that another unique node in the same owner scope holds. The
engine then clears the flag of the renamed node and the reply is still ok.
No check made before the rename can detect this. The reply reports the
applied name in `data.name` but not the flag; `inspect-node` reads the flag.

`create_node` creates a new child under a node in the currently edited
scene, also through the editor's `EditorUndoRedoManager` as a single
Undo/Redo step, without saving the scene. `parent_path` follows the same
rules as `rename_node`'s `node_path`. `class_name` must be a built-in,
instantiable `Node` subclass (`ClassDB.class_exists`, `ClassDB.can_instantiate`,
and a `Node`-ancestry check all have to pass); project script classes
registered with `class_name` in GDScript are rejected even if the name
resolves. `project_path` and `name` are validated the same way as
`rename_node`'s `project_path` and `new_name`. The new node is owned by the
edited scene root (so it will be included the next time the scene is
saved); the reply's `data.name` is the name actually applied, which can
differ from `data.requested_name` if it collided with a sibling.

`parent_path` must resolve to the scene root itself or to a node owned by
it. A node inside an instanced sub-scene's own internal structure is owned
by that instance's root instead, so a node created under it would appear in
the live tree but silently disappear the next time the scene is saved;
`create_node` rejects such a parent up front rather than allow that loss.
Adding a child directly under an instance's own root node is fine (the
instance root itself is owned by the edited scene, like any other local
node).

`set_property` sets one property on an existing node in the currently
edited scene, also through the editor's `EditorUndoRedoManager` as a single
Undo/Redo step, without saving the scene. `node_path` and `project_path`
follow the same rules as `rename_node`'s. `property` must appear in the
node's property list with inspector (`PROPERTY_USAGE_EDITOR`) or storage
(`PROPERTY_USAGE_STORAGE`) usage and must not be read-only; internal
properties such as `name` (use `rename_node`) and derived ones such as
`global_position` are rejected. `value` is raw JSON, coerced strictly to the
property's declared type with no cross-type guessing:

| Declared type | Accepted `value` |
| --- | --- |
| `bool` | `true` / `false` |
| `int` | an integral number (at most 2^53 in magnitude) |
| `float` | any number |
| `String`, `StringName`, `NodePath` | a string |
| `Vector2` | `[x, y]` |
| `Vector3` | `[x, y, z]` |
| `Vector2i` | `[x, y]`, each an integral int32 |
| `Vector3i` | `[x, y, z]`, each an integral int32 |
| `Vector4` | `[x, y, z, w]` |
| `Vector4i` | `[x, y, z, w]`, each an integral int32 |
| `Rect2` | `[x, y, w, h]` (position then size) |
| `Rect2i` | `[x, y, w, h]`, each an integral int32 |
| `Transform2D` | `[[xx, xy], [yx, yy], [ox, oy]]` (x axis, y axis, origin) |
| `Transform3D` | `[[bxx, bxy, bxz], [byx, byy, byz], [bzx, bzy, bzz], [ox, oy, oz]]` (the three basis column vectors, then the origin) |
| `Color` | an HTML color (`"#rrggbb"`, `"#rrggbbaa"`, with or without `#`), a named color (`"red"`), `[r, g, b]`, or `[r, g, b, a]` (0..1 floats) |
| `PackedByteArray` | a JSON array of integers `0..255` |
| `PackedInt32Array` | a JSON array of integral int32 numbers |
| `PackedInt64Array` | a JSON array of integral numbers (at most 2^53 in magnitude) |
| `PackedFloat32Array`, `PackedFloat64Array` | a JSON array of numbers |
| `PackedStringArray` | a JSON array of strings |
| `PackedVector2Array` | a JSON array of `[x, y]` elements |
| `PackedVector3Array` | a JSON array of `[x, y, z]` elements |
| `PackedVector4Array` | a JSON array of `[x, y, z, w]` elements |
| `PackedColorArray` | a JSON array of Color elements (as for `Color` above) |
| typed `Array[T]` | a JSON array of elements of T, where T is any of the scalar, vector, or color types above |

The int-vector and `Rect2i` components are stored by Godot as signed 32-bit
integers, so a fractional or out-of-range component (outside
`-2147483648..2147483647`) is rejected up front rather than silently wrapped.
The `Transform2D` and `Transform3D` arrays use the constructor order: axis
vectors first, then the origin. Any other declared type is rejected as
unsupported.

Array properties are coerced element by element with the same per-type rules;
a bad element is rejected with its index (`element 3: expected [x, y]`), and
the result is a correctly typed Godot array. An **untyped `Array`** property is
rejected, never guessed, because it has no declared element type (a `Vector2`
element would be indistinguishable from a 2-number array). A typed `Array[T]`
is also rejected when `T` is not one of the supported types (for example
`Object`, `Node`, `Resource`, `Dictionary`, a nested `Array`, or `Variant`).
An empty array `[]` is valid and sets an empty array of the right type. For an int property
declared with `PROPERTY_HINT_ENUM`, the value must be one of the declared
enum values: `"A,B,C"` implies `0,1,2`, and `"A:5,B:10"` gives explicit
values (`5,10`); an out-of-enum int is rejected before any undo action.
A target inside an instanced sub-scene is only accepted when every instance
between it and the edited scene root has Editable Children enabled;
otherwise the change would not be saved with the scene, so it is rejected
up front. All validation (project path, node, property, value) runs before
the undo action is created, so a rejected request leaves the scene and undo
history untouched. The reply's `data.old_value` and `data.value` are
`var_to_str` renderings of the property before and after the change;
`data.value` is read back from the node, so it reflects any clamping a
setter applied.

`delete_node` removes a node and its entire subtree from the currently
edited scene, also through the editor's `EditorUndoRedoManager` as a single
Undo/Redo step, without saving the scene. `node_path` and `project_path`
follow the same rules as `rename_node`'s, except that the scene root itself
(`"."`) is rejected. Undo re-adds the node at its original parent and child
index and restores the scene root's ownership over the node and every
descendant it owned, so the removed subtree returns exactly as it was and
persists after a save and reload; Redo removes it again. As with
`set_property`, a node inside an instanced sub-scene is only deleted when
every instance between it and the edited scene root has Editable Children
enabled; otherwise it is rejected up front, because the deletion would not
be saved. The reply's `data.name` is the removed node's name and
`data.parent_path` is its parent's path relative to the scene root.

`connect_signal` connects a signal on one node of the currently edited scene
to a method on another node, through the editor's `EditorUndoRedoManager` as a
single Undo/Redo step, without saving the scene. `source_path` and
`target_path` follow the same rules as `rename_node`'s `node_path` ("." for
the scene root); `signal` must be a signal the source node has
(`Object.has_signal`); `method` must be a method the target node has
(`Object.has_method`), because the engine itself never checks the target
method. The optional bools `deferred` and `one_shot` (default false) add
`CONNECT_DEFERRED` and `CONNECT_ONE_SHOT` to the `CONNECT_PERSIST` flag the
connection is always made with. The request is rejected, before any undo
action is created, when the source or target node is missing, the signal is
unknown, the method is unknown, or the connection already exists (including a
connection a sub-scene already defines, which appears with `CONNECT_INHERITED`
set). The source node must be serializable by the edited scene: a source
inside an instanced sub-scene is only accepted when every instance between it
and the edited scene root has Editable Children enabled, because the engine
silently drops the connection on save otherwise; a target inside a
non-editable instance is allowed, because the connection is stored on the
source. The success reply is
`{"status":"ok","data":{"source_path":"...","signal":"...","target_path":"...","method":"...","flags":<int>}}`,
with `data.flags` the flags the engine stored (`2` for a plain persistent
connection, `3` with `--deferred`, `6` with `--one-shot`). An argument-count
mismatch between the signal and the method is not checked: it connects and the
engine reports the mismatch only when the signal is emitted.

`set_group` adds or removes one persistent group on a node in the currently
edited scene, through the editor's `EditorUndoRedoManager` as a single
Undo/Redo step, without saving the scene. `node_path` and `project_path`
follow the same rules as `rename_node`'s. By default the group is added;
`remove` (the CLI's `--remove`) removes it instead. `group` must not be empty
(the engine itself refuses an empty group name). A group only survives a save
when the edited scene serializes the node: a node inside an instanced
sub-scene is only accepted when every instance between it and the edited scene
root has Editable Children enabled; the instance root itself is accepted with
Editable Children off, because it is owned by the edited scene root. A removal
is accepted only for a group the node holds persistently and locally, read
from a packed copy of the edited scene (the node's own row lists its local
persistent groups, and the owning instance's source scene lists the groups it
inherits). A group inherited from a sub-scene is rejected because the engine
brings it back after a reload. A group present only at runtime
(`add_to_group(name, false)`) or an engine-internal session group (for example
`_root_canvas...` on a `Control`) is rejected because it is not a persistent
local group of the node. A removal on a node inside a nested instance whose
origin cannot be read in one step is also rejected. Adding a
group the node is already in (locally or by inheritance) is rejected, because
the no-op action would still mark the scene dirty and add an Undo step. Every
check runs before the undo action is created, so a rejected request leaves the
scene, the dirty flag, the undo history, and the file untouched. The success
reply is
`{"status":"ok","data":{"node_path":"...","group":"...","action":"add"}}`
(`"action":"remove"` for a removal). Group names are used verbatim, with one
exception: a name that contains U+FFFD is rejected, because Godot's JSON
parser turns an escaped NUL (`\u0000`) into U+FFFD and the plugin cannot tell
them apart; a user group may start with an underscore.

`set_unique_name` sets or clears `Node.unique_name_in_owner` (the `%` name) on
a node in the currently edited scene, through the editor's
`EditorUndoRedoManager` as a single Undo/Redo step, without saving the scene.
`node_path` and `project_path` follow the same rules as `rename_node`'s. By
default the flag is set; `remove` (the CLI's `--remove`) clears it instead. A
unique name is scoped to the node's owner, so the scene root is rejected (it
has no owner and `%Root` never resolves). The flag only survives a save when
the edited scene serializes the node: a node inside an instanced sub-scene is
only accepted when every instance between it and the edited scene root has
Editable Children enabled; the instance root itself is accepted with Editable
Children off. A removal is accepted only for a flag the node holds locally and
persistently: removing a flag inherited from a sub-scene is rejected, and a
removal on a node inside a nested instance is rejected because the origin
cannot be read in one step. Setting the flag is rejected when the node already
has a unique name, and when another node in the same owner scope already
claims the same name (the engine refuses the second claim and that refusal
would still dirty the scene); the collision error names the claiming node. The
success reply is
`{"status":"ok","data":{"node_path":"...","name":"...","action":"add"}}`
(`"action":"remove"` for a removal; `name` is the node's name). Every check
runs before the undo action is created, so a rejected request leaves the
scene, the dirty flag, the undo history, and the file untouched.

`instantiate_scene` adds an instance of a `PackedScene` under a node of the
currently edited scene, through the editor's `EditorUndoRedoManager` as a
single Undo/Redo step, without saving the scene. `scene_path` must be a
`res://` path to a `.tscn` or `.scn` file; `parent_path` and `project_path`
follow the same rules as `create_node`'s. `project_path` is checked first, so a
mismatched caller can never cause a change. No `name` argument is taken: the
instance keeps the scene root's name, made unique by the engine when a sibling
collides, and the reply's `data.name` is the name actually applied. The reply
is
`{"status":"ok","data":{"node_path":"...","name":"...","scene_path":"..."}}`,
with `node_path` the instance's path relative to the edited scene root.
Instantiating a scene runs its `@tool` scripts' `_init` and `_enter_tree` in the
editor, so a rejected request must not instantiate anything; every check below
runs before the instance is created, and the only failure after instantiation
is a null result, which needs no cleanup. A rejected request leaves the scene,
the dirty flag, the undo history, and the file untouched, and nothing is saved
until `save_scene` runs. The editor selection is left unchanged. The scene
file is read from disk with the resource cache bypassed, so a scene file
changed on disk after the editor loaded it is the version that is
instantiated. Both accepted extensions were measured: the replay baseline
covers `.tscn`, and a probe outside the repository created a `.scn` with
`ResourceSaver.save`, instantiated it into a scene, saved that scene and found
the instance again after a fresh editor reload.

The scene path is rejected, in this order, when it is not a string, does not
start with `res://`, has a `..` path segment, does not end with `.tscn` or
`.scn`, does not exist, does not load as a `PackedScene`, or cannot be
instantiated. It is then rejected when the scene depends, directly or through
nested instances or scene inheritance, on a resource that does not exist,
because the saved instance would reference a broken file. When the edited
scene has a file path, the same walk rejects a scene whose dependency closure
contains the edited scene path, or the edited scene path itself: the instance
would make the edited scene contain itself, directly, through a nested
instance, or through scene inheritance. The walk continues only into `.tscn`
and `.scn` dependencies and guards against repeated files with a visited set.
The walk reads every scene file in the closure, so its cost grows with the
number of nested scene files.

`parent_path` must resolve to the scene root or to a node owned by it, exactly
as `create_node` requires. An instance added under a node inside an instanced
sub-scene's own internal structure would appear in the live tree but silently
disappear on save, so such a parent is rejected up front; this includes an
inner node of an instance whose Editable Children is on. The instance root
itself is accepted, and a local node owned by the edited scene root under an
editable instance is accepted, but adding under an inner node of any instance
is not supported. That last rejection is conservative: the persistence of an
instance added under an editable inner node is not proven. A parent node with
no owner is rejected for the same reason.

`save_scene` persists the currently edited scene to the file path it already
has, using the editor's own save path (the same one Ctrl+S uses), so every
edit made through `rename_node`, `create_node`, `set_property`,
`delete_node`, and `connect_signal` is written to disk and survives a
reload. It is the one
command that writes the scene file; it never prompts for a location, and
save-as or creating new files are out of scope. `project_path` follows the
same rules as `rename_node`'s and is checked before anything else, so a
mismatched caller can never cause a write. If no scene is open, or the open
scene has no file path yet (an unsaved new scene), the request is rejected
with a clear error and nothing is written. If Godot's save fails (a non-OK
`Error`), the reply is an `error` that names the Godot error; on success the
reply is `{"status":"ok","data":{"path":"res://..."}}` with `data.path` set
to the saved scene path.

`open_scene` opens a scene in the running editor by path and changes nothing
else: no Undo/Redo step, no file write unless the request asks to save.
`project_path` follows the same rules as `rename_node`'s and is checked
first. `scene_path` may be a `res://` path, a path relative to the project
root, an absolute path inside the project, a `..`-containing path, or a
`uid://` id. Before anything is saved or opened, the plugin resolves the path
and rejects imported scenes and anything that does not load as a `PackedScene`
(a missing file, a directory, a non-scene resource, or a scene with a missing
dependency). Without `save` a dirty edited scene stays open as a background
tab with its edits intact; with `save` it is saved first, and a failed save
is an `error` that opens nothing. There is no discard option. On success the
reply is `{"status":"ok","data":{"path":"res://...","saved":false,"unsaved":[]}}`,
with `data.unsaved` the scenes that still have unsaved changes after the call
(an untitled dirty scene appears as an empty string, e.g. `[""]`); when
the editor refuses the open, the reply is an `error` naming the requested
scene, and a save that already happened stays saved.

`list_resources` reports the resource files the editor's file system view
holds, as a bounded page, and changes nothing: no Undo/Redo step, no dirty
flag, no file write, and no edited scene is required. `project_path` follows
the same rules as `rename_node`'s and is checked first. Optional `path_prefix`
keeps entries whose full path starts with it (an empty string counts as
absent), optional `type` keeps entries whose engine class equals it or
inherits it (`ClassDB.is_parent_class`), optional `cursor` is the path of the
last entry of the previous page, `limit` is an integer in 1..1000 (default
100), and `refresh` (default false) starts a file-system scan. The reply is
`{"status":"ok","data":{"scanning":false,"resources":[{"path":"res://...","type":"..."},...],"truncated":false,"next_cursor":null}}`;
a `GDScript` entry also carries `script_class` and `extends` (either may be
empty). Entries are in the editor's own walk order (a directory's files before
its subdirectories, natural case-insensitive order inside a directory);
`TextFile` entries (`.txt`, `.cfg`, and other text files) are skipped. The page
holds at most `limit` entries; `truncated` is true when more matched, and
`next_cursor` is then the last returned path, to pass back as `cursor`. A
`cursor` that is not in the tree is an error (`cursor not found: <path>`); a
cursor entry deleted between pages gives that error, and the caller restarts
without a cursor. `type` must be an engine class (`unknown class: <name>`
otherwise); a project script class is rejected the same way `query_nodes`
rejects it. With `refresh` true the plugin starts a scan and replies
`{"scanning":true,"resources":[],"truncated":false,"next_cursor":null}`; a
request made while a scan is running replies the same scanning shape, so a
listing is not a partial page. The CLI's `--refresh` sends the refresh request,
then repeats the request every 250 ms until the scan finishes or 60 seconds
pass, and fails with a timeout error if it does not finish. Limits: the tree
lists loadable resources and omits unknown extensions, `.uid` and `.import`
sidecars, any folder holding a `.gdignore`, and dot folders (`.godot`, `.git`,
...); `ResourceLoader` can still open a file there, but this command does not
list it. `get_file_type` reports the base class, so an instance of a custom
resource class appears as `type: "Resource"`, not as that class. The tree is a
cached view: a file changed or added on disk after the editor's last scan does
not appear until a scan runs, which `refresh` starts. Only headless editors
were measured; the behavior of a windowed editor was not tested.
An entry's `type` can be an empty string: the editor reports no type for a
dangling symlink and for a text file named `.scn`. Such an entry is still
listed, and a `type` filter such as `Resource` does not match it.

`inspect_resource` reads one resource file as a typed view and changes
nothing: no Undo/Redo step, no dirty flag, no file write, no save, and no
edited scene is required. `project_path` follows the same rules as
`rename_node`'s and is checked first. `resource_path` must be a `res://`
path; the plugin rejects it up front when it does not start with `res://`,
contains a `..` path segment, or contains `uid://`. Scene files (`.tscn`,
`.scn`) and script files (`.gd`) are refused with a pointer to the commands
that read them (`open_scene`/`scene_tree`, or reading the file directly), and
a text `.tres` over 4 MiB is refused before it is parsed, so a huge text load
never runs and never stalls the editor. The extension comparison is
case-insensitive, so `.TSCN`, `.SCN`, `.GD` and `.TRES` are refused the same
way as their lowercase forms. A path that does not exist
(`ResourceLoader.exists` false: a TextFile, an unknown extension, a
directory, a missing file, a dangling symlink) replies `resource not found`.
The reply is `{"status":"ok","data":{"path":"...","type":"...","properties":[...]}}`
with `data.path` echoing the requested path and one property entry per
inspector/storage property in engine order, matching `inspect_node`'s shape:
`name`, the Variant type name, `read_only`, and `value` in the JSON shape
`set-property` accepts (via the shared value codec). An object-valued
property is shown compactly: an external resource reference as
`{"path","type"}` without expanding it, and an embedded sub-resource (a
`resource_path` of the form `file::id`) inline as `{"path","type","properties"}`
with a depth limit of 3 and a loop guard on the sub-resource ids, so a
self-referencing fixture cannot loop and a chain stops at the limit with the
deepest sub-resource shown as `{"path","type"}`. A null property value is
null. For the value types the codec cannot address (Object beyond Resource,
Dictionary, untyped Array, Callable, Signal, ...) the entry has `value: null`
and `supported: false`. An array property with more than 10,000 elements is
replaced by `{"type","size","truncated":true}` so the reply stays well below
the size that breaks the real CLI.

A resource whose dependency chain contains a `@tool` script is refused,
naming that script: `@tool` resource scripts run project code on load, so the
command scans the chain before anything is loaded and every refusal runs no
project code. A `@tool` annotation is a line whose first non-blank text is
`@tool`; a script that merely mentions `@tool` in a comment or string is
accepted, and a line inside a triple-quoted string is not treated as an
annotation. The chain is the transitive `ResourceLoader.get_dependencies`
closure (direct only per call, with an optional `uid://...::::` prefix
stripped) plus, for every `.gd` script reached, the `res://`, relative and
`uid://` paths its source text names in `preload("...")` and `load("...")`
literals (whitespace may span lines around the call and the path, trailing
arguments after the path are allowed, and a `uid://` literal is resolved
through the engine's `ResourceUID` registry): compiling a script compiles a
preloaded resource, so a `@tool` on a preloaded resource runs even when the
outer resource later fails to load. The walk also follows each script's
`extends` edge: `extends "path"` adds that path, a one-line
`class_name Name extends ...` and an inner `class Name extends ...` add their
base, and `extends ClassName` resolves the class name against the project's
global class list and adds that script's path, so a base script's own preloads
and base are walked in turn. A script that merely `extends` a `@tool` base
without `@tool` itself is therefore refused and the reply names the base
script, in both the path and the `class_name` forms. The scan cannot see a
path a script builds at run time, a `uid://` the editor does not know, or a
language construct the scan does not read; such a `@tool` path is not refused,
and these limits are documented.
The load uses `ResourceLoader.CACHE_MODE_IGNORE_DEEP` only: a fresh copy of
the top resource and its dependencies, never a replacement of a live cached
object that an open scene may hold. A file that exists but does not load (a
corrupt, an empty, or a text-scene-named-`.res` file) replies
`resource failed to load`.

There is no length prefix beyond the newline: a request is one
newline-terminated JSON object and a reply is one newline-terminated JSON
object. The plugin buffers the bytes of the request in its own state and
assembles them across reads, so a request that arrives in several TCP
segments is handled whole, regardless of timing; the whole request is parsed
only once its terminating newline has been received. Bytes after the first
newline are ignored, because the protocol is one request per connection. A
request that exceeds the 8 MiB cap (8388608 bytes) without a newline is
rejected with an error stating the cap and the received size, and nothing is
applied. A connection that sends nothing is closed after the 5 second idle
timeout with no reply; a connection that sends only part of a request and
then stalls is dropped after the same timeout with an incomplete-request
error. A raw client that omits the trailing newline still works, but only
after the idle timeout elapses, when the buffered bytes are parsed as a
complete JSON object. These limits were measured with a raw socket harness;
an oversized request never applies partially and never hangs the editor.
The plugin validates and applies a request in a single editor tick, so a
request of several megabytes pauses the editor while it runs. Measured with
`set-property` arrays of about 4 MB and about 7.65 MB, the longest tick was
roughly 0.2 to 0.5 seconds. A request of a few kilobytes is unaffected.

The port is fixed and there is no discovery or negotiation step, so only one
Godot editor instance can host the plugin on a given machine at a time.
Override the CLI's port with `--port` if the plugin is configured to listen
elsewhere.

### Command interface

The plugin publishes the interface of its commands and the CLI reads it.
The client sends `{"command":"commands"}` with no other field. The plugin
answers that request without checking a project path and without needing an
open scene.

The reply is `{"status":"ok","data":{"protocol_version":1,"commands":[...]}}`
with 17 entries in the order of the `COMMANDS` table. `protocol_version` is
the integer 1.

One command entry has `name` (the wire name sent as `command`, with `_`
shown as `-` on the command line), `description` (the one-line summary shown
by `commands` and per-command help), `needs_project_path` (true means the
CLI requires `--project-path`, canonicalizes it, and sends `project_path`;
false means the CLI accepts the flag but ignores it and sends no
`project_path`), `edits_scene` (true means the command changes the edited
scene through the undo stack), `refresh_poll` (true only for
`list_resources`, which drives the `--refresh` wait), and `arguments` (the
argument list in command-line order). One argument entry has `name` (the
wire field name), `flag` (the command-line flag such as `--limit`, or null
for a positional-only argument), `positional` (the positional index, or null
for a flag-only argument), `type` (one of the five types below), `required`
(true means a missing value is refused before anything is sent), `default`
(the value sent when an absent optional argument is left out), and `help`
(the one-line text shown in per-command help).

The five types are `string`, `boolean`, `json`, `res_path`, and `integer`.
A `string` value is sent unchanged; every content check is done by the
plugin. A `boolean` flag takes no value: presence sends true and absence
sends the default. A `json` value is sent as parsed JSON when the text parses
as JSON and as a plain string otherwise; the plugin coerces the result
strictly to the declared property type. A `res_path` value is a string plus
one CLI check: a symlink that resolves outside the project is refused before
anything is sent; the `res://` prefix, `..` segments, `uid://` values, and
existence are checked by the plugin. An `integer` value must match
`-?(0|[1-9][0-9]*)` and fit in 64 bits: `0`, `-3`, and `100` pass, while the
empty string, `abc`, `+5`, `1e2`, `1.5`, `007`, `-`, and a value beyond the
64-bit range are refused by the CLI with
`error: invalid --limit value: expected an integer, got abc` before anything
is sent; the numeric range (for example `1..1000` for a limit) is checked by
the plugin after the project path.

A client must refuse an interface it does not know instead of guessing. When
`protocol_version` is not 1, both `call` and `commands` (including
`commands --json`) print
`error: unsupported protocol version: 2 (this godot-cli knows 1)` and exit 1.
When the plugin predates the interface it answers the fetch with
`{"status":"error","message":"unknown command: commands"}`; the CLI then
prints `error: the editor plugin is too old for this godot-cli; update the
plugin` and exits 1.

The interface reply is bounded at 8388608 bytes (8 MiB): a larger reply is
refused with
`error: unexpected response from the plugin: the reply is larger than 8388608 bytes`
and exit 1.
Command replies have no bound and are read whole, including replies of many
megabytes.

One `call` opens one connection for the interface fetch, then a fresh
connection for the command request. A rejected invocation sends only the
fetch; no command request is sent. `list-resources --refresh` sends the
command request with `refresh` true on its own connection, then repeats the
same request with `refresh` false on fresh connections every 250 ms until
the reply reports the scan as finished or 60 seconds pass.

Each plugin command declares itself where it is implemented: every handler
script under `commands/` exposes a static `interface()` beside its static
`run(plugin, request)`. The `COMMANDS` table in `editor_plugin.gd` maps each
wire name to its handler in one line per command. Discovery walks the table
values in order and returns each `interface()` result.

The first entry, which takes no arguments:

```
{
 "name": "status",
 "description": "Report the editor version, whether a scene is playing, and the open scene.",
 "needs_project_path": false,
 "edits_scene": false,
 "refresh_poll": false,
 "arguments": []
}
```

One entry with arguments:

```
{
 "name": "rename_node",
 "description": "Rename a node.",
 "needs_project_path": true,
 "edits_scene": true,
 "refresh_poll": false,
 "arguments": [
  {
   "name": "node_path",
   "flag": null,
   "positional": 0,
   "type": "string",
   "required": true,
   "default": null,
   "help": "Scene-relative path of the node; '.' targets the scene root."
  },
  {
   "name": "new_name",
   "flag": null,
   "positional": 1,
   "type": "string",
   "required": true,
   "default": null,
   "help": "The new node name."
  },
  {
   "name": "project_path",
   "flag": "--project-path",
   "positional": null,
   "type": "string",
   "required": true,
   "default": null,
   "help": "Absolute path of the target project; canonicalized before sending."
  }
 ]
}
```

Both examples are the parsed entries of `tests/schema_fixture.json`.

## CLI usage

```sh
cargo run -- commands
cargo run -- commands --json
cargo run -- --help
cargo run -- --version
cargo run -- call status
cargo run -- call scene-tree
cargo run -- call status --port 47821
cargo run -- call rename-node Child/Deep NewName --project-path /path/to/project
cargo run -- call create-node Child/Deep Label NewLabel --project-path /path/to/project
cargo run -- call set-property Child/Deep position '[10, 20]' --project-path /path/to/project
cargo run -- call set-property Child/Deep modulate '#ff8800' --project-path /path/to/project
cargo run -- call inspect-node --node-path Child/Deep --project-path /path/to/project
cargo run -- call query-nodes --class Node2D --project-path /path/to/project
cargo run -- call query-nodes --group enemies --name 'Leaf*' --limit 50 --project-path /path/to/project
cargo run -- call inspect-class --class Node --project-path /path/to/project
cargo run -- call delete-node Child/Deep --project-path /path/to/project
cargo run -- call connect-signal Source ping Target on_ping --project-path /path/to/project
cargo run -- call connect-signal Source ping Target on_ping --deferred --project-path /path/to/project
cargo run -- call set-group Child/Deep enemies --project-path /path/to/project
cargo run -- call set-group Child/Deep enemies --remove --project-path /path/to/project
cargo run -- call set-unique-name Child/Deep --project-path /path/to/project
cargo run -- call set-unique-name Child/Deep --remove --project-path /path/to/project
cargo run -- call instantiate-scene --scene-path res://scenes/S1.tscn --parent-path . --project-path /path/to/project
cargo run -- call instantiate-scene --scene-path res://scenes/S1.tscn --parent-path Child/Deep --project-path /path/to/project
cargo run -- call save-scene --project-path /path/to/project
cargo run -- call open-scene --scene-path scenes/S1.tscn --project-path /path/to/project
cargo run -- call open-scene --scene-path res://scenes/S1.tscn --save --project-path /path/to/project
cargo run -- call list-resources --project-path /path/to/project
cargo run -- call list-resources --path-prefix res://scenes/ --type Texture2D --limit 50 --project-path /path/to/project
cargo run -- call list-resources --cursor res://scenes/a.png --project-path /path/to/project
cargo run -- call list-resources --refresh --project-path /path/to/project
cargo run -- call inspect-resource --resource-path res://scenes/a.tres --project-path /path/to/project
```

`status` reports the editor version, whether a scene is playing, and the
path of the scene currently open for editing. `scene-tree` reports the node
tree (name, class, children) of that scene, or a clear error if no scene is
open. `rename-node <scene-relative-path> <new-name> --project-path <dir>`
renames a node in that scene (see Protocol above); the CLI canonicalizes
`--project-path` itself before sending it, so a symlinked path (e.g. `/tmp`
on macOS) still matches the editor's own canonical project path.
`create-node <parent-scene-relative-path> <class> <name> --project-path <dir>`
creates a new built-in-class node under that parent (see Protocol above),
canonicalizing `--project-path` the same way.
`set-property <scene-relative-path> <property> <value> --project-path <dir>`
sets a property on that node (see Protocol above). `<value>` is sent as JSON
if it parses as JSON (`true`, `3`, `1.5`, `[1, 2]`, `"42"`), otherwise as a
plain string (`Hello`, `#ff8800`), so a String property whose value looks
like a number or a bool needs explicit JSON quotes, e.g. `'"42"'`.
`inspect-node --node-path <scene-relative-path> --project-path <dir>` reads
a node and its editor-visible properties without changing the scene (see
Protocol above), canonicalizing `--project-path` the same way as the other
commands.
`query-nodes [--class <class>] [--group <group>] [--name <pattern>] [--limit <n>] --project-path <dir>`
searches the edited scene's node tree for matching nodes without changing
the scene (see Protocol above), canonicalizing `--project-path` the same
way as the other commands; the filters are optional and combine with AND,
and `--limit` (default 100, max 1000) bounds the number of returned nodes.
`inspect-class --class <class> --project-path <dir>` reports an engine
class's ClassDB reflection (ancestors, instantiability, Node ancestry, and
declared properties, methods, and signals) without changing the scene and
without requiring an edited scene (see Protocol above), canonicalizing
`--project-path` the same way as the other commands.
`delete-node <scene-relative-path> --project-path <dir>` removes that node
and its subtree from the scene (see Protocol above), canonicalizing
`--project-path` the same way as the other mutating commands. If the plugin is not running
(editor closed, or plugin disabled), the CLI exits with a nonzero status and
a message explaining that the plugin could not be reached.
`connect-signal <source-scene-relative-path> <signal> <target-scene-relative-path> <method> --project-path <dir> [--deferred] [--one-shot]`
connects the signal on the source node to the method on the target node (see
Protocol above), canonicalizing `--project-path` the same way as the other
mutating commands. `--deferred` and `--one-shot` add the matching connect
flags to `CONNECT_PERSIST`. Nothing is saved until `save-scene` runs.
`set-group <scene-relative-path> <group> --project-path <dir> [--remove]` adds
the group to that node, or removes it with `--remove`, through the editor's
undo/redo stack (see Protocol above), canonicalizing `--project-path` the same
way as the other mutating commands. Nothing is saved until `save-scene` runs.
`set-unique-name <scene-relative-path> --project-path <dir> [--remove]` sets
the `%` unique name of that node, or clears it with `--remove`, through the
editor's undo/redo stack (see Protocol above), canonicalizing `--project-path`
the same way as the other mutating commands. Nothing is saved until
`save-scene` runs.
`instantiate-scene --scene-path <path> --parent-path <scene-relative-path> --project-path <dir>`
adds an instance of the scene at `<path>` under the parent node through the
editor's undo/redo stack (see Protocol above), canonicalizing `--project-path`
the same way as the other mutating commands. `<parent-path>` must be the scene
root (`.`) or a node owned by it. Nothing is saved until `save-scene` runs.
`save-scene --project-path <dir>` persists the currently edited scene to the
file path it already has (see Protocol above), canonicalizing `--project-path`
the same way as the other mutating commands. The reply's `data.path` is the
saved scene's `res://` path.
`open-scene --scene-path <path> --project-path <dir> [--save]` opens the
scene at `<path>` in the running editor (see Protocol above); `<path>` may be
a `res://`, relative, absolute-inside-project, `..`, or `uid://` path the
editor accepts. Imported scenes (`.gltf` and friends) are rejected before
anything is saved. Without `--save` a dirty edited scene stays open as a
background tab; with `--save` it is saved before the open. The success reply
lists the scenes that still have unsaved changes in `data.unsaved`.
`--project-path` is canonicalized the same way as the other commands.
`list-resources [--path-prefix <res://dir/>] [--type <class>] [--limit <n>] [--cursor <path>] [--refresh] --project-path <dir>`
lists the resource files the editor file system holds, as a bounded page,
without loading them (see Protocol above), canonicalizing `--project-path`
the same way as the other commands. `--limit` defaults to 100: the CLI
refuses a value outside the integer grammar before anything is sent, and the
plugin validates the 1..1000 range after the project check.
With `--refresh` the CLI starts a scan and waits until it finishes (up to
60 seconds) before printing the listing.
`inspect-resource --resource-path <res://path> --project-path <dir>` reads a
resource file's class and typed property values (see Protocol above),
canonicalizing `--project-path` the same way as the other commands and
resolving symlinks first: a `res://` path whose symlink resolves outside the
project is rejected locally (`resource path resolves outside the project`)
instead of being sent, because the plugin cannot detect a symlink escape.

## Verification

```sh
cargo fmt --check
cargo test
cargo clippy -- -D warnings
```

`cargo test` includes a protocol test that opens a real loopback socket and
round-trips a request and a reply through the exact wire format, not just the
Rust data types, plus the engine tests: the recorded invocations of every
command, run against a fake plugin that publishes the interface in
`tests/schema_fixture.json`, each compared with the request that must reach
the plugin as parsed JSON; the integer grammar; the protocol-version and
too-old-plugin messages; the help and version output; the `list-resources`
`--refresh` wait loop; the `inspect-resource` symlink containment; and the
`set-property` value parsing.

Live verification against a running Godot editor (status, scene tree,
rename/undo/redo/save, project-path rejection, and the disabled-plugin
failure case) is tracked in the Code4Me task result, not in this repository,
since it depends on an external editor process and a separate test project.
