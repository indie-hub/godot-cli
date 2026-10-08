@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Renames a node in the currently edited scene through the editor's
## undo/redo stack, so the rename shows up as one Undo/Redo step and marks
## the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself), `new_name`, and `project_path` (the
## canonical, symlink-resolved absolute path of the project the caller
## intends to edit). The project-path check runs before anything else is
## resolved, so a mismatched caller can never cause a mutation.
##
## When the target holds a unique name and the name the engine would apply is
## already held by another unique node in the same owner scope, the engine
## keeps the requested name but silently clears the target's flag. That rename
## is rejected before the undo action, naming the claimant, so a rejected
## request never touches the live node. When no sibling holds the requested
## name the engine applies it exactly, and that exact name is tested. When a
## sibling holds it the engine numbers it, and the test then rejects a unique
## node whose name is the requested stem followed by digits: a conservative
## superset of the names the engine can apply. The unique-name check does not
## apply to a target without a unique flag or to one whose owner is null (the
## scene root). The ownership check below runs after it. It rejects, before
## the undo action, a target that is neither the scene root nor owned by the
## edited scene root: an instance-owned descendant of an instanced sub-scene,
## the root of a nested instance, or a node with no owner. The engine accepts
## the rename in the open editor, but the name is not kept after a save and a
## fresh reload. Instance roots owned by the edited scene, local nodes under an
## editable instance, and plain nodes are not rejected by this check.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var new_name_value: Variant = request.get("new_name")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(new_name_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("rename_node requires string fields: node_path, new_name, project_path")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

	var new_name: String = new_name_value
	if new_name.is_empty():
		return CommandSupport.error("new name must not be empty")
	for character in CommandSupport.INVALID_NAME_CHARACTERS:
		if new_name.contains(character):
			return CommandSupport.error("new name must not contain any of: . : @ / \" %")

	# The engine first uniquifies the requested name against the target's
	# siblings (internal children included), then checks the unique-name scope
	# (the node's owner). Reject a rename that would leave the applied name
	# held by another unique node in that scope, because the engine would keep
	# the name and clear the flag. A null owner is not a usable scope. Nothing
	# is assigned to the live node here: the check only reads the tree.
	if target.unique_name_in_owner and target.owner != null:
		var scene_root: Node = guarded["scene_root"]
		var conflict := CommandSupport.unique_name_conflict(target, new_name, scene_root)
		if not conflict.is_empty():
			var claimant: Node = conflict["claimant"]
			var message := "unique name collision: %s is already set on %s" % [str(claimant.name), str(scene_root.get_path_to(claimant))]
			if conflict["sibling"]:
				message += "; the requested name %s is held by a sibling and the engine would number it" % new_name
			return CommandSupport.error(message)

	# The engine accepts the rename in the open editor. For a target that is
	# neither the scene root nor owned by the edited scene root, the name is
	# not kept after a save and a fresh reload, so such targets are rejected
	# before the undo action and a rejected request changes nothing. The
	# targets are instance-owned descendants of an instanced sub-scene, the
	# roots of nested instances, and nodes with no owner. Instance roots owned
	# by the edited scene, local nodes under an editable instance, and plain
	# nodes are not rejected by this check.
	if target != guarded["scene_root"] and target.owner != guarded["scene_root"]:
		if target.owner == null:
			return CommandSupport.error(
				"node has no owner in the edited scene and its rename would not be saved: %s" % node_path
			)
		return CommandSupport.error(
			"node is inherited from a sub-scene and its rename would not be saved: %s" % node_path
		)

	var old_name := str(target.name)
	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Rename Node")
	undo_redo.add_do_property(target, "name", new_name)
	undo_redo.add_undo_property(target, "name", old_name)
	undo_redo.commit_action()

	# Godot silently re-uniquifies a name that collides with a sibling, so
	# the requested name is not necessarily the name that ended up applied;
	# read it back rather than assume the request was honored verbatim.
	return CommandSupport.ok({
		"node_path": node_path,
		"old_name": old_name,
		"requested_name": new_name,
		"name": str(target.name),
	})