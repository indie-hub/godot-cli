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


## Returns the plugin's exact project-path mismatch message when
## `project_path_value` does not match the running editor's project, or an
## empty string when it does. The first check every command runs (a mismatched
## caller can never cause a mutation or a read); shared by `guard_request` and
## commands that do not need an edited scene, like `inspect_class`.
static func project_path_mismatch(project_path_value: Variant) -> String:
	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		return "project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
	return ""


## Resolves the request fields every scene command checks in the same fixed
## order: the project path first (so a mismatched caller can never cause a
## mutation or a read), then the edited scene root, then the node path and its
## target when the command has one. Returns the resolved objects on the first
## success, or the exact error message the plugin sent before the split.
## The handlers keep their own per-command field-type checks, so the values
## passed here are already known to be strings. Commands without a node path
## omit `node_path_value`, which skips the node-path checks.
static func guard_request(plugin: EditorPlugin, project_path_value: Variant, node_path_value: Variant = null) -> Variant:
	var project_mismatch := project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return project_mismatch
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


## Resolves the `parent_path` of a command that creates a new persisted child
## in the edited scene: the parent-path shape checks, the resolution against
## the edited scene root, and the eligibility check that the parent is
## serialized by the edited scene. Returns the error message String on the
## first failure, or {"scene_root": ..., "parent_path": ..., "parent": ...} on
## success. Shared by `create_node` and `instantiate_scene`, which must produce
## the same messages in the same order.
##
## A new child only saves correctly when Godot serializes it as part of the
## edited scene: the scene root itself, or any node directly owned by it. A
## node inside an instanced sub-scene's own internal structure is owned by that
## instance's own root instead, so a child added under it can appear in the
## live tree but silently vanish on save. Reject those parents up front rather
## than mutate the scene and lose the result.
static func resolve_new_child_parent(plugin: EditorPlugin, parent_path_value: Variant) -> Variant:
	var scene_root := plugin.get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		return "no scene is currently being edited"

	var parent_path: String = parent_path_value
	if parent_path.is_empty():
		return "parent path must not be empty; use \".\" for the scene root"
	if parent_path.begins_with("/"):
		return "parent path must be relative to the scene root; absolute paths are rejected"
	if parent_path.contains(":"):
		return "parent path must not contain ':'"
	if parent_path != "." and ".." in parent_path.split("/"):
		return "parent path must not contain '..'"

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var parent: Node = scene_root if parent_path == "." else scene_root.get_node_or_null(NodePath(parent_path))
	if parent == null:
		return "parent node not found: %s" % parent_path

	if parent != scene_root and parent.owner != scene_root:
		return "parent is not eligible for a new persisted child: %s is not the scene root and is not owned by it (it is likely inside an instanced sub-scene's internal structure); only the scene root or a node it owns is supported" % parent_path

	return {"scene_root": scene_root, "parent_path": parent_path, "parent": parent}


## Returns the first node in the edited scene, other than `target`, that holds
## a unique flag (the `%` name) for `name` and has the same owner as `target`,
## or null when there is none. That node is what the engine refuses a second
## claimant for in one owner scope. A null owner (the scene root, or an unowned
## node) is not a usable scope and always returns null. The walk includes
## internal children, which take part in the engine's sibling naming and can
## hold owner-scoped unique names. Shared by `set_unique_name` (which tests the
## target's current name) and `rename_node`.
static func unique_claimant(target: Node, name: String, scene_root: Node) -> Node:
	return _first_claimant(target, scene_root, name, false)


## Reports whether renaming `target` to `name` would clear its unique flag
## because another unique node in the same owner scope already holds the name
## the engine would apply. Returns {} when the rename is allowed, or
## {"claimant": node, "sibling": bool} when it must be rejected. `sibling` is
## true when a sibling of `target` already holds `name`, so the engine numbers
## it and the test is the conservative stem-plus-digits superset.
static func unique_name_conflict(target: Node, name: String, scene_root: Node) -> Dictionary:
	if target.owner == null:
		return {}
	var sibling_holds := false
	var parent := target.get_parent()
	if parent != null:
		for child in parent.get_children(true):
			if child != target and str(child.name) == name:
				sibling_holds = true
				break
	if not sibling_holds:
		var claimant := unique_claimant(target, name, scene_root)
		if claimant != null:
			return {"claimant": claimant, "sibling": false}
		return {}
	var stem_claimant := _first_claimant(target, scene_root, _name_stem(name), true)
	if stem_claimant != null:
		return {"claimant": stem_claimant, "sibling": true}
	return {}


## The name without its trailing ASCII digits. The engine numbers the requested
## name when a sibling holds it; the width and size of that number are the
## engine's business, so only the stem is used here.
static func _name_stem(name: String) -> String:
	var position := name.length()
	while position > 0 and "0123456789".contains(name[position - 1]):
		position -= 1
	return name.substr(0, position)


## First node other than `target` in `target`'s owner scope with a unique flag
## whose name equals `name_or_stem`, or starts with it and continues with one
## or more ASCII digits when `stem_digits` is true. A null owner returns null.
static func _first_claimant(target: Node, scene_root: Node, name_or_stem: String, stem_digits: bool) -> Node:
	var owner: Node = target.owner
	if owner == null:
		return null
	var stack: Array = [scene_root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node != target and node.owner == owner and node.unique_name_in_owner:
			var node_name := str(node.name)
			if (stem_digits and _is_stem_digits(node_name, name_or_stem)) or (not stem_digits and node_name == name_or_stem):
				return node
		for child in node.get_children(true):
			stack.append(child)
	return null


## Whether `name` is `stem` followed by one or more ASCII digits. An empty stem
## matches any name that is only digits, which is the conservative superset for
## a requested name that is itself all digits.
static func _is_stem_digits(name: String, stem: String) -> bool:
	if not name.begins_with(stem):
		return false
	var rest := name.substr(stem.length())
	if rest.is_empty():
		return false
	for index in rest.length():
		if not "0123456789".contains(rest[index]):
			return false
	return true


## Strips the "uid://xxxx::::" prefix a `ResourceLoader.get_dependencies` string
## carries when the editor wrote a uid attribute on its `ext_resource` line, so
## the remaining res:// path can be checked with `ResourceLoader.exists` and
## compared with another path. Shared by `instantiate_scene` and `open_scene`.
static func dependency_path(dependency: String) -> String:
	if dependency.contains("::::"):
		return dependency.substr(dependency.rfind("::::") + 4)
	return dependency