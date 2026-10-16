@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const SelfScript := preload("set_parent.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "set_parent",
		"description": "Move a node under a new parent as one Undo/Redo step.",
		"needs_project_path": true,
		"edits_scene": true,
		"refresh_poll": false,
		"arguments": [
		{"name": "node_path", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "Scene-relative path of the node to move; the scene root is rejected."},
		{"name": "new_parent_path", "flag": null, "positional": 1, "type": "string", "required": true, "default": null, "help": "Scene-relative path of the new parent; '.' targets the scene root."},
		{"name": "index", "flag": "--index", "positional": null, "type": "integer", "required": false, "default": null, "help": "Position among the new parent's children, an integer; default appends."},
		{"name": "keep_global_transform", "flag": "--keep-global-transform", "positional": null, "type": "boolean", "required": false, "default": true, "help": "Keep the full global transform of a Node2D or Node3D and the global position only of a Control; other nodes ignore it."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Moves one node of the currently edited scene under a new parent through the
## editor's undo/redo stack, so the move shows up as one Undo/Redo step and
## marks the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself; the root cannot be moved) and
## `new_parent_path` (relative to the edited scene root, "." for the root
## itself), and `project_path` (the canonical, symlink-resolved absolute path
## of the project the caller intends to edit). The optional `index` positions
## the node among the new parent's children (an integer; the default appends).
## The optional bool `keep_global_transform` (default true) preserves the full
## global transform of a Node2D and a Node3D, and the global position only of a
## Control; other node types ignore it. The project path is checked first, and
## every other check runs before the undo action is created, so a rejected
## request never touches the scene, the dirty flag, the undo history, or the
## file.
##
## The new parent follows the `create_node` rule: it must be the scene root or
## a node the edited scene root owns, so the moved node is serialized by the
## edited scene. An inherited node is owned by the edited scene root, so it is
## an eligible new parent, exactly as it is for `create_node`. The moved node
## must be one the edited scene stores: the scene root is rejected, a node with
## no owner is rejected, a node inherited from a sub-scene is rejected, and a
## node inherited from the scene's base scene is rejected, with or without a
## local override, because none of their moves is kept after a save and a fresh
## reload. An instanced sub-scene's own root node, and a local node the edited
## scene owns, are stored by the scene and can be moved.
##
## A move that would rename the node is rejected rather than applied: when the
## new parent already holds a child with the node's name, the engine's
## re-parent call would replace the name with an internal placeholder.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var new_parent_path_value: Variant = request.get("new_parent_path")
	var index_value: Variant = request.get("index")
	var keep_value: Variant = request.get("keep_global_transform", true)
	var project_path_value: Variant = request.get("project_path")

	# The project path is checked before the other fields, so a mismatched
	# caller gets the project error even when another field is malformed. A
	# non-string project_path cannot be compared and falls through to the
	# field-type check below.
	if typeof(project_path_value) == TYPE_STRING:
		var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
		if project_mismatch != "":
			return CommandSupport.error(project_mismatch)

	if typeof(node_path_value) != TYPE_STRING or typeof(new_parent_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("set_parent requires string fields: node_path, new_parent_path, project_path")
	# Godot's JSON parser returns every number as a float, so an integer index
	# arrives as float too; it is accepted only when it is a whole number.
	if index_value != null and not _is_whole_number(index_value):
		return CommandSupport.error("index must be an integer")
	if typeof(keep_value) != TYPE_BOOL:
		return CommandSupport.error("set_parent requires a boolean field: keep_global_transform")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

	var parent_guarded: Variant = CommandSupport.resolve_new_child_parent(plugin, new_parent_path_value)
	if parent_guarded is String:
		return CommandSupport.error(parent_guarded)
	var new_parent: Node = parent_guarded["parent"]
	var new_parent_path: String = parent_guarded["parent_path"]

	if target == scene_root:
		return CommandSupport.error("the scene root cannot be moved")

	# The moved node must be serialized by the edited scene. A node with no
	# owner, or one whose owner is an instance root rather than the edited
	# scene root, belongs to a sub-scene: the engine accepts the move in the
	# open editor, but the node returns to the sub-scene after a save and a
	# fresh reload. Instance roots are owned by the edited scene root and are
	# not rejected by this check. The non-editable walk names the
	# persistence failure the way delete_node and rename_node do.
	if target.owner == null:
		return CommandSupport.error("node has no owner in the edited scene and its move would not be saved: %s" % node_path)
	if target.owner != scene_root:
		var owner_node: Node = target.owner
		while owner_node != null and owner_node != scene_root:
			if not scene_root.is_editable_instance(owner_node):
				return CommandSupport.error(
					"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the move would not be saved" % node_path
				)
			owner_node = owner_node.owner
		return CommandSupport.error(
			"node is inherited from a sub-scene and its move would not be saved: %s" % node_path
		)

	# A node that comes from the scene's base scene cannot be moved, whether or
	# not the edited scene overrides it: its owner is the edited scene root, so
	# the checks above let it through, and moving it writes a local copy beside
	# the inherited node, so a save and a fresh reload show the subtree twice.
	# The shared helper builds the refusal.
	var refusal := CommandSupport.inherited_edit_refusal(scene_root, target, node_path, "move")
	if refusal != "":
		return CommandSupport.error(refusal)

	if new_parent == target or target.is_ancestor_of(new_parent):
		return CommandSupport.error("the new parent is inside the moved node's own subtree: %s" % new_parent_path)

	var old_parent: Node = target.get_parent()
	var old_index: int = target.get_index()
	var same_parent: bool = old_parent == new_parent
	# The index is the node's final position among the new parent's children
	# (internal children excluded, as get_child_count and get_index count
	# them). Moving within one parent frees the node's own slot, so one fewer
	# position exists. The default appends.
	var max_index: int = new_parent.get_child_count() - (1 if same_parent else 0)
	var final_index: int = max_index
	if index_value != null:
		final_index = int(index_value)
		if final_index < 0 or final_index > max_index:
			return CommandSupport.error("index %d is out of range for the new parent (0..%d)" % [final_index, max_index])
	if same_parent and final_index == old_index:
		return CommandSupport.error("no-op: %s is already at index %d under %s" % [node_path, old_index, new_parent_path])

	# The engine's re-parent call adds the node without a readable name, so a
	# name the new parent already holds would be replaced by an internal
	# placeholder. Reject the collision instead, so the applied name always
	# equals the node's requested name. This is the sibling-name rule
	# create_node and rename_node rely on, applied to the new parent (every
	# child, internal ones included, as the engine's naming scan counts them).
	for child in new_parent.get_children(true):
		if child != target and str(child.name) == str(target.name):
			return CommandSupport.error(
				"name collision: %s already has a child named %s; the move would rename the node" % [new_parent_path, str(target.name)]
			)

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Set Parent", UndoRedo.MERGE_DISABLE, scene_root)
	undo_redo.add_do_method(SelfScript, "_move", target, new_parent, final_index, keep_value)
	undo_redo.add_undo_method(SelfScript, "_move", target, old_parent, old_index, keep_value)
	undo_redo.commit_action()

	return CommandSupport.ok({
		"node_path": node_path,
		"new_parent_path": new_parent_path,
		"index": final_index,
		"name": str(target.name),
	})


## Re-parents `node` under `parent` and places it at `index`. The engine's own
## re-parent call is a no-op when the parent is unchanged and appends the node
## when it changes, so the position is set afterwards in both cases. The move
## changes only the position: the engine call keeps every node of the moved
## subtree at exactly the owner it already had, so a node the edited scene saved
## before the move is still saved after it, and a node it did not save stays
## unsaved. The undo step calls this with the old parent and index, so Undo
## restores the owners and the transform exactly.
static func _move(node: Node, parent: Node, index: int, keep_global_transform: bool) -> void:
	if node.get_parent() != parent:
		node.reparent(parent, keep_global_transform)
	parent.move_child(node, index)


## Whether `value` is a whole number. Godot's JSON parser returns every number
## as a float, so both an int and an integral float are accepted; a fractional
## float, a string and every other type are not.
static func _is_whole_number(value: Variant) -> bool:
	if typeof(value) == TYPE_INT:
		return true
	if typeof(value) == TYPE_FLOAT:
		return float(value) == floorf(float(value))
	return false
