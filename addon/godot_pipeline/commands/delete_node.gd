@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const SelfScript := preload("delete_node.gd")


## Deletes a node and its whole subtree from the currently edited scene
## through the editor's undo/redo stack, so the deletion shows up as one
## Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself) and `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to edit).
## The project-path check runs before anything else is resolved, and every
## other check runs before the undo action is created, so a rejected request
## never touches the scene or the undo history.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("delete_node requires string fields: node_path, project_path")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]
	if target == scene_root:
		return CommandSupport.error("the scene root cannot be deleted")

	# Deleting inside an instanced sub-scene only survives a save when every
	# instance between the target and scene_root has "Editable Children" on;
	# otherwise the removal silently reappears on reload. Reject those up
	# front rather than mutate the scene and lose the result.
	var owner_node: Node = target.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			return CommandSupport.error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the deletion would not be saved" % node_path
			)
		owner_node = owner_node.owner

	# Captured before the node leaves the tree, so the undo step can restore
	# it to its exact prior position and every node to its exact prior owner
	# (the scene root for local nodes, an instance root for a node inside an
	# editable instanced sub-scene).
	var parent := target.get_parent()
	var index := target.get_index()
	var owners: Array = []
	_collect_owners(target, owners)

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Delete Node")
	undo_redo.add_do_method(parent, "remove_child", target)
	undo_redo.add_undo_method(parent, "add_child", target, true)
	undo_redo.add_undo_method(parent, "move_child", target, index)
	undo_redo.add_undo_method(SelfScript, "_restore_owners", owners)
	undo_redo.add_undo_reference(target)
	undo_redo.commit_action()

	return CommandSupport.ok({
		"node_path": node_path,
		"name": str(target.name),
		"parent_path": str(scene_root.get_path_to(parent)),
		"index": index,
		"child_count": target.get_child_count(),
	})


## Records `node` and every descendant with its exact current owner, mirroring
## the engine's `Node::get_owned_by()` but keeping each node's own owner so the
## delete undo step restores the original owner whether it was the scene root
## or an instance root inside an editable instanced sub-scene. The undo step
## re-attaches these owners because `remove_child` detaches the subtree but the
## scene's saved state depends on which node owns each node.
static func _collect_owners(node: Node, entries: Array) -> void:
	entries.append([node, node.owner])
	for child in node.get_children():
		_collect_owners(child, entries)


static func _restore_owners(entries: Array) -> void:
	for entry in entries:
		if entry is Array and entry.size() == 2 and entry[0] is Node:
			entry[0].owner = entry[1]