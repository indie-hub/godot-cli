@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Adds an instance of a `PackedScene` under a node of the currently edited
## scene through the editor's undo/redo stack, so the instance shows up as one
## Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `scene_path` (the scene to instantiate, a
## `res://` `.tscn` or `.scn` path), `parent_path` (relative to the edited
## scene root, "." for the root itself), and `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to edit).
## The project-path check runs before anything else is resolved, and every
## other check runs before the scene is instantiated, so a rejected request
## never runs a script inside the scene and never touches the scene or the undo
## history.
##
## `parent_path` must resolve to the scene root or a node owned by it, exactly
## as `create_node` requires: an instance added under an inner node of any
## instance, or under a node with no owner, would not persist on save and is
## rejected. The scene path is rejected up front when it is not a `res://`
## `.tscn`/`.scn` file, does not exist, does not load as a `PackedScene`, cannot
## be instantiated, depends on a missing resource, or would make the edited
## scene contain itself (directly, through a nested instance, or through scene
## inheritance). No `name` argument is taken: the instance keeps the scene
## root's name, made unique by the engine when a sibling collides; the applied
## name is read back in the reply.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var scene_path_value: Variant = request.get("scene_path")
	var parent_path_value: Variant = request.get("parent_path")
	var project_path_value: Variant = request.get("project_path")
	if typeof(scene_path_value) != TYPE_STRING or typeof(parent_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("instantiate_scene requires string fields: scene_path, parent_path, project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var parent_guarded: Variant = CommandSupport.resolve_new_child_parent(plugin, parent_path_value)
	if parent_guarded is String:
		return CommandSupport.error(parent_guarded)
	var scene_root: Node = parent_guarded["scene_root"]
	var parent: Node = parent_guarded["parent"]

	var scene_path: String = scene_path_value
	if not scene_path.begins_with("res://"):
		return CommandSupport.error("scene path must start with res://: %s" % scene_path)
	if ".." in scene_path.split("/"):
		return CommandSupport.error("scene path must not contain '..': %s" % scene_path)
	if not scene_path.ends_with(".tscn") and not scene_path.ends_with(".scn"):
		return CommandSupport.error("scene path must end with .tscn or .scn: %s" % scene_path)
	if not ResourceLoader.exists(scene_path):
		return CommandSupport.error("scene not found: %s" % scene_path)
	# The resource cache is bypassed so a scene file written on disk after the
	# editor loaded it is the file that is instantiated. A path that resolves to
	# a res:// file the editor cannot read as a PackedScene is rejected here,
	# before anything is instantiated.
	var packed: PackedScene = ResourceLoader.load(scene_path, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE) as PackedScene
	if packed == null:
		return CommandSupport.error("scene is not a PackedScene: %s" % scene_path)
	if not packed.can_instantiate():
		return CommandSupport.error("scene cannot be instantiated: %s" % scene_path)
	var unsafe := _unsafe_scene_dependency(scene_path, str(scene_root.scene_file_path))
	if unsafe != "":
		return CommandSupport.error(unsafe)

	# Every check passed. The instance is created with the editor's own variant
	# so the packed scene keeps its editing state. A null result is the only
	# failure after this point and needs no cleanup.
	var instance: Node = packed.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	if instance == null:
		return CommandSupport.error("scene could not be instantiated: %s" % scene_path)

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Instantiate Scene", UndoRedo.MERGE_DISABLE, scene_root)
	# force_readable_name=true so a sibling-name collision uniquifies to
	# "Name2" the way the editor's own UI does. Only the instance root is
	# owned by the edited scene; its descendants stay owned by the instance
	# root, so an owner on a descendant would write a local override.
	undo_redo.add_do_method(parent, "add_child", instance, true)
	undo_redo.add_do_method(instance, "set_owner", scene_root)
	undo_redo.add_do_reference(instance)
	undo_redo.add_undo_method(parent, "remove_child", instance)
	undo_redo.commit_action()

	# Godot silently re-uniquifies a name that collides with a sibling, so the
	# scene root's name is not necessarily the name that ended up applied.
	return CommandSupport.ok({
		"node_path": str(scene_root.get_path_to(instance)),
		"name": str(instance.name),
		"scene_path": scene_path,
	})


## Walks the scene files `path` instantiates, directly or through nested
## instances or scene inheritance, and returns an empty string when the walk is
## safe or a short error message when it is not. A dependency that does not
## exist is rejected, because the saved instance would reference a broken
## scene. When `edited_path` is not empty, `path` itself or any dependency
## equal to it is rejected, because the edited scene would contain itself. A
## visited set guards the walk against cycles in the dependency graph.
static func _unsafe_scene_dependency(path: String, edited_path: String) -> String:
	var visited := {}
	var pending: Array = [path]
	while not pending.is_empty():
		var current: String = pending.pop_back()
		if visited.has(current):
			continue
		visited[current] = true
		if not edited_path.is_empty() and current == edited_path:
			return "scene would contain the edited scene, directly or through an instance or inheritance: %s" % path
		for dependency in ResourceLoader.get_dependencies(current):
			var dependency_path := CommandSupport.dependency_path(dependency)
			if not ResourceLoader.exists(dependency_path):
				return "scene depends on a missing resource: %s" % dependency_path
			if dependency_path.ends_with(".tscn") or dependency_path.ends_with(".scn"):
				pending.append(dependency_path)
	return ""
