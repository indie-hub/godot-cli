@tool
extends RefCounted

## Shared helpers for the per-command scripts in this folder: the reply
## constructors, the shared request guard, and the constants more than one
## command uses. A helper used by exactly one command lives in that command's
## own file instead.

## Characters Godot strips from node names (see
## `String::validate_node_name()` in the engine). Rejecting them up front
## means the caller never has to guess whether a name was silently changed.
## Shared by `rename_node` and `create_node`.
const INVALID_NAME_CHARACTERS := [".", ":", "@", "/", "\"", "%"]


## Builds a success reply dictionary, exactly as the plugin's `_reply_ok`
## did before the split.
static func ok(data: Variant) -> Dictionary:
	return {"status": "ok", "data": data}


## Builds an error reply dictionary, exactly as the plugin's `_reply_error`
## did before the split.
static func error(message: String) -> Dictionary:
	return {"status": "error", "message": message}


## Resolves the request fields every scene command checks in the same fixed
## order: the project path first (so a mismatched caller can never cause a
## mutation or a read), then the edited scene root, then the node path and its
## target when the command has one. Returns the resolved objects on the first
## success, or the exact error message the plugin sent before the split.
## The handlers keep their own per-command field-type checks, so the values
## passed here are already known to be strings. Commands without a node path
## omit `node_path_value`, which skips the node-path checks.
static func guard_request(plugin: EditorPlugin, project_path_value: Variant, node_path_value: Variant = null) -> Variant:
	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		return "project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
	var scene_root := plugin.get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		return "no scene is currently being edited"
	if node_path_value == null:
		return {"scene_root": scene_root}
	var node_path: String = node_path_value
	if node_path.is_empty():
		return "node path must not be empty; use \".\" for the scene root"
	if node_path.begins_with("/"):
		return "node path must be relative to the scene root; absolute paths are rejected"
	if node_path.contains(":"):
		return "node path must not contain ':'"
	if node_path != "." and ".." in node_path.split("/"):
		return "node path must not contain '..'"

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var target: Node = scene_root if node_path == "." else scene_root.get_node_or_null(NodePath(node_path))
	if target == null:
		return "node not found: %s" % node_path
	return {"scene_root": scene_root, "node_path": node_path, "target": target}