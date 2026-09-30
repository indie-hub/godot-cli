# Godot Pipeline v0

A bridge between a terminal and a running Godot editor: a small Rust CLI
talks over a loopback TCP socket to a stock-Godot GDScript `EditorPlugin`,
which reports the editor's status, the node tree of the scene currently
being edited, the properties of a node in that scene, and the nodes matching
a class, group, and name search, and can rename,
create, set properties on, and delete nodes through the editor's own
undo/redo stack, then save the edited scene to disk.

This is a prototype (v0). It does not modify the Godot engine checkout and
does not touch any project files beyond a temporary plugin install used for
testing, the scene edits a caller explicitly requests via `rename-node`,
`create-node`, `set-property`, or `delete-node`, or the scene file written
by `save-scene`.

## Layout

- `addon/godot_pipeline/` — the GDScript `EditorPlugin` source of truth.
  Copy this folder into a Godot project's `addons/` directory and enable it
  under Project Settings > Plugins to use it.
- `src/protocol.rs` — the wire protocol and the synchronous TCP client.
- `src/main.rs` — the CLI commands.

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
   `{"command":"delete_node","node_path":"...","project_path":"..."}`,
   or
   `{"command":"save_scene","project_path":"..."}`,
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
Godot uniquified it.

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

`save_scene` persists the currently edited scene to the file path it already
has, using the editor's own save path (the same one Ctrl+S uses), so every
edit made through `rename_node`, `create_node`, `set_property`, and
`delete_node` is written to disk and survives a reload. It is the one
command that writes the scene file; it never prompts for a location, and
save-as or creating new files are out of scope. `project_path` follows the
same rules as `rename_node`'s and is checked before anything else, so a
mismatched caller can never cause a write. If no scene is open, or the open
scene has no file path yet (an unsaved new scene), the request is rejected
with a clear error and nothing is written. If Godot's save fails (a non-OK
`Error`), the reply is an `error` that names the Godot error; on success the
reply is `{"status":"ok","data":{"path":"res://..."}}` with `data.path` set
to the saved scene path.

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

## CLI usage

```sh
cargo run -- status
cargo run -- scene-tree
cargo run -- status --port 47821
cargo run -- rename-node Child/Deep NewName --project-path /path/to/project
cargo run -- create-node Child/Deep Label NewLabel --project-path /path/to/project
cargo run -- set-property Child/Deep position '[10, 20]' --project-path /path/to/project
cargo run -- set-property Child/Deep modulate '#ff8800' --project-path /path/to/project
cargo run -- inspect-node --node-path Child/Deep --project-path /path/to/project
cargo run -- query-nodes --class Node2D --project-path /path/to/project
cargo run -- query-nodes --group enemies --name 'Leaf*' --limit 50 --project-path /path/to/project
cargo run -- delete-node Child/Deep --project-path /path/to/project
cargo run -- save-scene --project-path /path/to/project
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
`delete-node <scene-relative-path> --project-path <dir>` removes that node
and its subtree from the scene (see Protocol above), canonicalizing
`--project-path` the same way as the other mutating commands. If the plugin is not running
(editor closed, or plugin disabled), the CLI exits with a nonzero status and
a message explaining that the plugin could not be reached.
`save-scene --project-path <dir>` persists the currently edited scene to the
file path it already has (see Protocol above), canonicalizing `--project-path`
the same way as the other mutating commands. The reply's `data.path` is the
saved scene's `res://` path.

## Verification

```sh
cargo fmt --check
cargo test
cargo clippy -- -D warnings
```

`cargo test` includes protocol tests that open a real loopback socket and
round-trip `Request`/`Response` pairs through the exact wire format, not
just the Rust data types, plus tests pinning the `rename_node`, `create_node`, `set_property`, `inspect_node`, `query_nodes`, `delete_node`, and `save_scene` wire
shapes and the CLI's `set-property` value parsing.

Live verification against a running Godot editor (status, scene tree,
rename/undo/redo/save, project-path rejection, and the disabled-plugin
failure case) is tracked in the Code4Me task result, not in this repository,
since it depends on an external editor process and a separate test project.
