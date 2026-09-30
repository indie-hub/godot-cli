# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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
