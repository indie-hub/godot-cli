# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.6.0] - 2026-10-09

### Added

- `connect-signal` command to connect a signal on one node of the currently edited scene to a method on another node through the editor's `EditorUndoRedoManager` as a single Undo/Redo step, without saving the scene. The optional `--deferred` and `--one-shot` flags add `CONNECT_DEFERRED` and `CONNECT_ONE_SHOT` to the `CONNECT_PERSIST` flag the connection is always made with. The request is rejected before any change when a node, the signal, or the method is missing, when the connection already exists (including one a sub-scene defines), or when the source node is inside a non-editable instanced sub-scene (where the engine would silently drop it on save); a target inside a non-editable instance is accepted. An argument-count mismatch between the signal and the method is not checked and fails only when the signal is emitted.
- `set-group` command to add or remove one persistent group on a node of the currently edited scene through the editor's `EditorUndoRedoManager` as a single Undo/Redo step, without saving the scene. By default the group is added; the CLI's `--remove` flag removes it instead. The request is rejected before any change when the node is missing, the group name is empty, the group name contains U+FFFD (Godot's JSON parser turns an escaped NUL into U+FFFD, so the name cannot be told apart), an add targets a group the node is already in (locally or inherited), a removal targets a group that is not a persistent local group (an inherited group, a runtime group, or an engine-internal session group), a removal targets a node inside a nested instance whose origin cannot be read in one step, or the node is inside a non-editable instanced sub-scene (where the engine would silently drop the group on save); the instance root itself is accepted with Editable Children off.
- `set-unique-name` command to set or clear `Node.unique_name_in_owner` (the `%` name) on a node of the currently edited scene through the editor's `EditorUndoRedoManager` as a single Undo/Redo step, without saving the scene. By default the flag is set; the CLI's `--remove` flag clears it instead. The request is rejected before any change when the node is the scene root (`%Root` never resolves), the node is inside a non-editable instanced sub-scene (where the engine would silently drop the flag on save), an add targets a node that is already unique or would collide with another unique name in the same owner scope (the error names the claiming node), a removal targets a node that is not unique or whose flag is inherited from a sub-scene, or a removal targets a node inside a nested instance whose flag origin cannot be read in one step; the instance root itself is accepted with Editable Children off.
- `instantiate-scene` command to add an instance of a `PackedScene` under a node of the currently edited scene through the editor's `EditorUndoRedoManager` as a single Undo/Redo step, without saving the scene. `--scene-path` must be a `res://` `.tscn` or `.scn` file and `--parent-path` must be the scene root (`.`) or a node owned by it. No name is taken: the instance keeps the scene root's name, made unique by the engine on a sibling collision, and the reply reports the applied name. The request is rejected before any change when the scene path is not a string, does not start with `res://`, has a `..` segment, does not end with `.tscn` or `.scn`, does not exist, does not load as a `PackedScene`, or cannot be instantiated; when the scene depends on a missing resource; when the edited scene appears in the scene's dependency closure (a self, transitive or inheritance cycle); or when the parent is inside an instance's own internal structure or has no owner. Instantiating runs the scene's `@tool` scripts, so a rejected request instantiates nothing.

### Fixed

- `rename-node` rejects, before the undo action, a rename that would clear the target's unique name (`unique_name_in_owner`) because another unique node in the same owner scope holds the name the engine would apply. The check only reads the scene and never renames the live node, so a rejected request runs no callback. When no sibling of the target holds the requested name the exact name is tested; when a sibling holds it the check rejects a unique node whose name is the requested name without its trailing digits followed by digits, a conservative superset of the names the engine can apply (it can reject a rename the engine would allow). The claimant scan includes internal children and ignores a null owner (the scene root). A target without a unique flag is unaffected. A script or observer that renames the node during the real rename can still cause a collision this check cannot see; the README describes it.
- `rename-node` also rejects, before the undo action, a rename of a target that is neither the scene root nor owned by the edited scene root: the engine accepts its rename in the open editor, but the name is not kept after a save and a fresh reload, so such a rename would not persist. The rejected targets are instance-owned descendants of an instanced sub-scene, the roots of nested instances, and nodes with no owner. Instance roots owned by the edited scene, local nodes under an editable instance, and plain nodes are not affected by this check and rename and persist. The unique-name check described above runs first.

## [0.5.0] - 2026-10-01

### Added

- `open-scene` command to open a scene in the running editor by path, optionally saving a dirty edited scene first with `--save`; without `--save` a dirty edited scene stays open as a background tab. Only native scenes are opened; imported scenes are rejected before anything is saved. The reply names the scene actually edited after the open, so a scene the editor silently refuses to open is reported as an error, and lists the scenes that still have unsaved changes in `unsaved` (an untitled dirty scene appears as an empty string, e.g. `[""]`).
- `inspect-class` command to report an engine class's ClassDB reflection (ancestors, instantiability, Node ancestry, and declared properties, methods, and signals) without changing the scene and without requiring an edited scene.
- `query-nodes` command to search the edited scene's node tree for nodes matching a class, group, and glob name filter, in tree order, up to a bounded limit, without changing the scene.
- `inspect-node` command to read a node's class, child count, and the values of its editor-visible properties without changing the scene, including the JSON shape needed to feed a supported value back through `set-property`.

### Fixed

- Requests that arrive in several TCP segments are no longer cut short; the plugin buffers and assembles each request until its terminating newline.

### Changed

- A request that exceeds the 8 MiB cap without a terminating newline is rejected with an error naming the cap and the received size.
- A connection that sends only part of a request and then stalls is dropped after the 5 second idle timeout, and a request that omits the trailing newline is accepted after that timeout.
- A request of several megabytes is now handled whole, and it pauses the editor for roughly 0.2 to 0.5 seconds while it is validated and applied.

## [0.4.0] - 2026-09-29

### Added

- `set-property` now accepts all Packed*Array types and typed `Array[T]` properties, coercing each element with the same per-type rules and naming the element index in errors.
- Untyped `Array` properties and typed arrays of unsupported element types (Object, Node, Resource, Dictionary, nested Array, Variant) are rejected before any change.

### Changed

- The plugin's malformed-request error now reports how many bytes were received, so a request that exceeds the single-pass socket read is recognizable as too large.

## [0.3.0] - 2026-09-29

### Added

- `set-property` now accepts Vector2i, Vector3i, Vector4, Vector4i, Rect2, Rect2i, Transform2D, and Transform3D, all as plain JSON arrays.
- Int-vector and Rect2i components must be integral signed 32-bit integers; fractional or out-of-range components are rejected before any change.

## [0.2.0] - 2026-09-29

### Added

- `rename-node` command to rename a node in the currently edited scene via a single Undo/Redo step.
- `create-node` command to instantiate a built-in Node subclass as a child of a scene node via a single Undo/Redo step.
- `set-property` command to set a named property on a node in the currently edited scene, with strict type coercion for bool, int, float, String, StringName, NodePath, Vector2, Vector3, and Color.
- `delete-node` command to remove a node and its subtree from the currently edited scene via a single Undo/Redo step, with owner restoration on Undo.
- `save-scene` command to persist the currently edited scene to disk through EditorInterface.

### Changed

- `set-property` now rejects an int value for a PROPERTY_HINT_ENUM property unless it matches one of the declared enum values.

### Fixed

- `create-node` now rejects parents not owned by the edited scene root, preventing nodes that would silently vanish on save.
- Added `.uid` files to `.gitignore` to exclude Godot-generated sidecar files from the addon source.

## [0.1.0] - 2026-09-25

### Added

- Initial release of the Godot Pipeline CLI and editor plugin.
- `status` command to report the editor version, playing state, and active scene path.
- `scene-tree` command to report the node tree of the currently edited scene.
- GDScript EditorPlugin that communicates with the CLI over a loopback TCP socket.
