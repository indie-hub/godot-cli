@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Creates a new built-in `Node`-subclass child under a node in the currently
## edited scene through the editor's undo/redo stack, so the creation shows
## up as one Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `parent_path` (relative to the edited
## scene root, "." for the root itself), `class_name` (a built-in,
## instantiable `Node` subclass; project script classes are rejected),
## `name`, and `project_path` (the canonical, symlink-resolved absolute path
## of the project the caller intends to edit). The project-path check runs
## before anything else is resolved, so a mismatched caller can never cause a
## mutation. create-node keeps its own project, scene and path checks because
## its messages say "parent path" rather than "node path", so it does not use
## the shared guard.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var parent_path_value: Variant = request.get("parent_path")
	var class_name_value: Variant = request.get("class_name")
	var name_value: Variant = request.get("name")
	var project_path_value: Variant = request.get("project_path")
	if typeof(parent_path_value) != TYPE_STRING or typeof(class_name_value) != TYPE_STRING or typeof(name_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("create_node requires string fields: parent_path, class_name, name, project_path")

	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		return CommandSupport.error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)

	var scene_root := plugin.get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		return CommandSupport.error("no scene is currently being edited")

	var parent_path: String = parent_path_value
	if parent_path.is_empty():
		return CommandSupport.error("parent path must not be empty; use \".\" for the scene root")
	if parent_path.begins_with("/"):
		return CommandSupport.error("parent path must be relative to the scene root; absolute paths are rejected")
	if parent_path.contains(":"):
		return CommandSupport.error("parent path must not contain ':'")
	if parent_path != "." and ".." in parent_path.split("/"):
		return CommandSupport.error("parent path must not contain '..'")

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var parent: Node = scene_root if parent_path == "." else scene_root.get_node_or_null(NodePath(parent_path))
	if parent == null:
		return CommandSupport.error("parent node not found: %s" % parent_path)

	# A new child only saves correctly if Godot will actually serialize it as
	# part of the edited scene: the scene root itself, or any node directly
	# owned by it, both work; a node inside an instanced sub-scene's own
	# internal structure is owned by that instance's own root instead (not
	# by scene_root), and a new_node.owner = scene_root child added under it
	# can appear in the live tree but silently vanish on save. Reject those
	# parents up front rather than mutate the scene and lose the result.
	if parent != scene_root and parent.owner != scene_root:
		return CommandSupport.error(
			"parent is not eligible for a new persisted child: %s is not the scene root and is not owned by it (it is likely inside an instanced sub-scene's internal structure); only the scene root or a node it owns is supported" % parent_path
		)

	var requested_class: String = class_name_value
	if not ClassDB.class_exists(requested_class):
		return CommandSupport.error("unknown class: %s" % requested_class)
	for global_class in ProjectSettings.get_global_class_list():
		if global_class.get("class") == requested_class:
			return CommandSupport.error("class must be a built-in Node class, not a project script class: %s" % requested_class)
	if requested_class != "Node" and not ClassDB.is_parent_class(requested_class, "Node"):
		return CommandSupport.error("class is not a Node subclass: %s" % requested_class)
	if not ClassDB.can_instantiate(requested_class):
		return CommandSupport.error("class cannot be instantiated: %s" % requested_class)

	var name: String = name_value
	if name.is_empty():
		return CommandSupport.error("name must not be empty")
	for character in CommandSupport.INVALID_NAME_CHARACTERS:
		if name.contains(character):
			return CommandSupport.error("name must not contain any of: . : @ / \" %")

	var new_node: Node = ClassDB.instantiate(requested_class)
	new_node.name = name

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Create Node")
	# force_readable_name=true so a sibling-name collision uniquifies to
	# "Name2" the way the editor's own UI does, instead of add_child's
	# default internal "@ClassName@123" placeholder.
	undo_redo.add_do_method(parent, "add_child", new_node, true)
	undo_redo.add_do_method(new_node, "set_owner", scene_root)
	undo_redo.add_do_reference(new_node)
	undo_redo.add_undo_method(parent, "remove_child", new_node)
	undo_redo.commit_action()

	# Godot silently re-uniquifies a name that collides with a sibling, so
	# the requested name is not necessarily the name that ended up applied;
	# read it back rather than assume the request was honored verbatim.
	return CommandSupport.ok({
		"parent_path": parent_path,
		"class_name": requested_class,
		"requested_name": name,
		"name": str(new_node.name),
	})