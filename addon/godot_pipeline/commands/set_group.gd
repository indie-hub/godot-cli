@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Adds or removes one persistent group on one node of the currently edited
## scene through the editor's undo/redo stack, so the change shows up as one
## Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself), `group`, and `project_path` (the
## canonical, symlink-resolved absolute path of the project the caller
## intends to edit), plus an optional bool field `remove` (default false; true
## removes the group). The project-path check runs before anything else is
## resolved, and every other check runs before the undo action is created, so
## a rejected request never touches the scene or the undo history.
##
## A group only survives a save when the edited scene serializes the node: a
## node inside an instanced sub-scene needs every instance between it and the
## scene root to have Editable Children on (mirrors
## `Node::get_deepest_editable_node` in the engine); the instance root itself
## is owned by the edited scene root, so it is accepted with Editable Children
## off. A removal is accepted only for a group the node holds persistently and
## locally: a group inherited from a sub-scene comes back on a reload and a
## session (runtime) group was never saved, so both are rejected. The origin is
## read from a packed copy of the edited scene (persistent local groups in the
## node's own row, inherited groups in the owning instance's source scene)
## because `get_groups()` itself carries no origin or persistence flag.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var group_value: Variant = request.get("group")
	var project_path_value: Variant = request.get("project_path")
	var remove_value: Variant = request.get("remove", false)
	if typeof(node_path_value) != TYPE_STRING or typeof(group_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("set_group requires string fields: node_path, group, project_path")
	if typeof(remove_value) != TYPE_BOOL:
		return CommandSupport.error("set_group requires a boolean field: remove")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

	var group: String = group_value
	if group.is_empty():
		return CommandSupport.error("group must not be empty")
	# Godot's JSON parser replaces an escaped NUL (\u0000) with U+FFFD, so a
	# legitimate U+FFFD and a NUL cannot be told apart once the request is
	# parsed. Reject the replacement character rather than act on the wrong
	# name.
	if group.contains("\uFFFD"):
		return CommandSupport.error("group name must not contain U+FFFD (Godot's JSON parser turns an escaped NUL into U+FFFD, so the name cannot be told apart)")

	# A group only survives a save when the edited scene serializes its node:
	# a node inside an instanced sub-scene needs every instance between it and
	# scene_root to have Editable Children on. The instance root itself is
	# owned by the edited scene root, so its owner walk is empty and it is
	# accepted with Editable Children off. Reject the rest up front rather than
	# apply a group the engine silently drops on save.
	var owner_node: Node = target.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			return CommandSupport.error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the group would not be saved" % node_path
			)
		owner_node = owner_node.owner

	var classification := _classify(scene_root, target, group)
	if classification.has("error"):
		return CommandSupport.error(classification["error"])
	var local: bool = classification["local"]
	var inherited: bool = classification["inherited"]
	var nested: bool = classification["nested"]

	var remove: bool = remove_value
	var is_member: bool = target.is_in_group(group)
	if not remove:
		if is_member:
			if inherited:
				return CommandSupport.error("group is inherited from a sub-scene: %s is already in group %s" % [node_path, group])
			return CommandSupport.error("already in group: %s is already a member of group %s" % [node_path, group])
	else:
		if not is_member:
			return CommandSupport.error("node is not in group: %s is not a member of group %s" % [node_path, group])
		if not local:
			if nested:
				return CommandSupport.error("cannot remove a group from a node inside a nested instance: %s" % node_path)
			if inherited:
				return CommandSupport.error("group is inherited from a sub-scene and cannot be removed: %s on %s" % [group, node_path])
			return CommandSupport.error("not a persistent group of this node: %s on %s" % [group, node_path])

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Add Group" if not remove else "Remove Group", UndoRedo.MERGE_DISABLE, scene_root)
	if remove:
		undo_redo.add_do_method(target, "remove_from_group", group)
		undo_redo.add_undo_method(target, "add_to_group", group, true)
	else:
		undo_redo.add_do_method(target, "add_to_group", group, true)
		undo_redo.add_undo_method(target, "remove_from_group", group)
	undo_redo.commit_action()

	return CommandSupport.ok({
		"node_path": node_path,
		"group": group,
		"action": "remove" if remove else "add",
	})


## Reports how `group` relates to `target` in a packed copy of the edited
## scene: `local` when the node's own row lists it (a persistent local
## membership), `inherited` when the owning instance's source scene lists it,
## and `nested` when the node sits under more than one instance level so the
## source scene cannot be read in one step. A pack failure is returned as an
## `error` entry.
static func _classify(scene_root: Node, target: Node, group: String) -> Dictionary:
	var packed := PackedScene.new()
	var error := packed.pack(scene_root)
	if error != OK:
		return {"error": "could not read the scene's groups (pack failed: %s)" % error_string(error)}
	var state := packed.get_state()
	# The canonical path relative to the edited scene root, not the request
	# string, so a request spelling such as "./Child" still matches the packed
	# state's "./Child" row.
	var target_path := "." if target == scene_root else "./" + str(scene_root.get_path_to(target))

	var local := false
	var target_index := _index_for_path(state, target_path)
	if target_index >= 0:
		for candidate in state.get_node_groups(target_index):
			if str(candidate) == group:
				local = true
				break

	var inherited := false
	var nested := false
	var source := _source_groups(state, target_path)
	if source["resolved"]:
		for candidate in source["groups"]:
			if str(candidate) == group:
				inherited = true
				break
	else:
		nested = true

	return {"local": local, "inherited": inherited, "nested": nested}


## Returns the index of the node with the given packed path (".", "./Child",
## "./Sub/Inner") in `state`, or -1 when the path is not listed.
static func _index_for_path(state: SceneState, packed_path: String) -> int:
	for i in state.get_node_count():
		if str(state.get_node_path(i)) == packed_path:
			return i
	return -1


## Reads the groups a node inherits from its instanced source scene. Returns
## {"resolved": true, "groups": [...]} when the source row is found, and
## {"resolved": false, "groups": []} when the node lies under more than one
## instance level, where the source row is not reachable in one step.
static func _source_groups(state: SceneState, target_path: String) -> Dictionary:
	if target_path == ".":
		return {"resolved": true, "groups": []}

	# Every ancestor row (and the node itself) that the edited scene records as
	# an instance root. More than one means a nested instance.
	var instance_indices: Array = []
	var prefix := ""
	for part in target_path.substr(2).split("/"):
		prefix = part if prefix.is_empty() else prefix + "/" + part
		var index := _index_for_path(state, "./" + prefix)
		if index >= 0 and state.get_node_instance(index) != null:
			instance_indices.append(index)
	if instance_indices.is_empty():
		return {"resolved": true, "groups": []}
	if instance_indices.size() > 1:
		return {"resolved": false, "groups": []}

	var source: PackedScene = state.get_node_instance(instance_indices[0])
	if source == null:
		return {"resolved": true, "groups": []}
	var source_state := source.get_state()
	var instance_path := str(state.get_node_path(instance_indices[0]))
	var inner := "" if target_path == instance_path else target_path.substr(instance_path.length() + 1)
	var source_path := "." if inner.is_empty() else "./" + inner
	var source_index := _index_for_path(source_state, source_path)
	if source_index < 0:
		return {"resolved": true, "groups": []}

	var groups: Array = []
	for candidate in source_state.get_node_groups(source_index):
		groups.append(str(candidate))
	return {"resolved": true, "groups": groups}
