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