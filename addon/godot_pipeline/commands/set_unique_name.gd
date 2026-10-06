@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Sets or clears `Node.unique_name_in_owner` (the `%` name) on one node of the
## currently edited scene through the editor's undo/redo stack, so the change
## shows up as one Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root) and `project_path` (the canonical, symlink-resolved absolute
## path of the project the caller intends to edit), plus an optional bool field
## `remove` (default false; true clears the flag). The project-path check runs
## before anything else is resolved, and every other check runs before the undo
## action is created, so a rejected request never touches the scene or the undo
## history.
##
## A unique name is scoped to the node's owner, so the scene root is rejected
## (it has no owner and `%Root` never resolves). The flag only survives a save
## when the edited scene serializes the node: a node inside an instanced
## sub-scene needs every instance between it and the scene root to have
## Editable Children on; the instance root itself is accepted with Editable
## Children off. A removal is accepted only for a flag the node holds locally
## and persistently: a flag inherited from a sub-scene is rejected, and a
## removal on a node inside a nested instance is rejected because the origin
## cannot be read in one step. An add is rejected when the node already has the
## flag, and when another node in the same owner scope already claims the same
## name, because the engine refuses the second claim (and that refusal would
## still dirty the scene). The claimant is named in the error.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	var remove_value: Variant = request.get("remove", false)
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("set_unique_name requires string fields: node_path, project_path")
	if typeof(remove_value) != TYPE_BOOL:
		return CommandSupport.error("set_unique_name requires a boolean field: remove")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

	if target == scene_root:
		return CommandSupport.error("the scene root cannot have a unique name: %Root never resolves")

	# The flag only survives a save when the edited scene serializes its node: a
	# node inside an instanced sub-scene needs every instance between it and
	# scene_root to have Editable Children on. The instance root itself is owned
	# by the edited scene root, so its owner walk is empty and it is accepted
	# with Editable Children off.
	var owner_node: Node = target.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			return CommandSupport.error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the unique name would not be saved" % node_path
			)
		owner_node = owner_node.owner

	var origin := _origin(scene_root, target)
	if origin.has("error"):
		return CommandSupport.error(origin["error"])
	var local: bool = origin["local"]
	var inherited: bool = origin["inherited"]
	var nested: bool = origin["nested"]

	var remove: bool = remove_value
	var is_unique: bool = target.unique_name_in_owner
	if not remove:
		if is_unique:
			if inherited and not local:
				return CommandSupport.error("unique name is inherited from a sub-scene: %s already has a unique name" % node_path)
			return CommandSupport.error("node already has a unique name: %s" % node_path)
		var claimant := _collision(scene_root, target)
		if claimant != null:
			return CommandSupport.error(
				"unique name collision: %s is already set on %s" % [str(target.name), str(scene_root.get_path_to(claimant))]
			)
	else:
		if not is_unique:
			return CommandSupport.error("node does not have a unique name: %s" % node_path)
		if not local:
			if nested:
				return CommandSupport.error("cannot determine the origin of the unique name on a node inside a nested instance: %s" % node_path)
			if inherited:
				return CommandSupport.error("unique name is inherited from a sub-scene and cannot be removed: %s" % node_path)
			return CommandSupport.error("node does not have a persistent local unique name: %s" % node_path)

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Set Unique Name" if not remove else "Clear Unique Name", UndoRedo.MERGE_DISABLE, scene_root)
	undo_redo.add_do_property(target, "unique_name_in_owner", not remove)
	undo_redo.add_undo_property(target, "unique_name_in_owner", remove)
	undo_redo.commit_action()

	return CommandSupport.ok({
		"node_path": node_path,
		"name": str(target.name),
		"action": "remove" if remove else "add",
	})


## Returns the first node in the edited scene tree, other than `target`, that
## has the same owner and name as `target` and already holds a unique flag.
## That is the claimant the engine would refuse a second one for. Null when
## there is none.
static func _collision(scene_root: Node, target: Node) -> Node:
	var owner: Node = target.owner
	var name := str(target.name)
	var stack: Array = [scene_root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node != target and node.owner == owner and str(node.name) == name and node.unique_name_in_owner:
			return node
		for child in node.get_children():
			stack.append(child)
	return null


## Reports how the unique flag relates to `target` in a packed copy of the
## edited scene: `local` when the node's own row stores it, `inherited` when
## the owning instance's source scene stores it, and `nested` when the node
## sits under more than one instance level so the source scene cannot be read
## in one step. A pack failure is returned as an `error` entry.
static func _origin(scene_root: Node, target: Node) -> Dictionary:
	var packed := PackedScene.new()
	var error := packed.pack(scene_root)
	if error != OK:
		return {"error": "could not read the scene's unique names (pack failed: %s)" % error_string(error)}
	var state := packed.get_state()
	var target_path := "." if target == scene_root else "./" + str(scene_root.get_path_to(target))

	var local := _stored_unique(state, target_path)
	var inherited := false
	# The packed root does not always list an intermediate instance root, so
	# count the instance levels from the node's owner chain instead.
	var nested := _instance_levels(scene_root, target) > 1
	if not nested:
		var source := _source_state(state, target_path)
		if source["resolved"]:
			var source_state: SceneState = source["state"]
			if source_state != null:
				inherited = _stored_unique(source_state, source["path"])
		else:
			nested = true

	return {"local": local, "inherited": inherited, "nested": nested}


## Number of instance roots between `target` and the edited scene root, the
## node itself included. An instance root carries the path of its source scene
## in `scene_file_path`; a node the scene owns directly carries an empty one.
static func _instance_levels(scene_root: Node, target: Node) -> int:
	var count := 0
	var node: Node = target
	while node != null and node != scene_root:
		if node.scene_file_path != "":
			count += 1
		node = node.owner
	return count


## Whether `state`'s row at the packed path stores unique_name_in_owner = true.
## SceneState.get_node_property_value takes the property's integer index, not
## its name, so the row's properties are scanned for the name first.
static func _stored_unique(state: SceneState, packed_path: String) -> bool:
	var index := _index_for_path(state, packed_path)
	if index < 0:
		return false
	for property_index in state.get_node_property_count(index):
		if str(state.get_node_property_name(index, property_index)) == "unique_name_in_owner":
			var value: Variant = state.get_node_property_value(index, property_index)
			return value == true
	return false


## Returns the index of the node with the given packed path (".", "./Child")
## in `state`, or -1 when the path is not listed.
static func _index_for_path(state: SceneState, packed_path: String) -> int:
	for i in state.get_node_count():
		if str(state.get_node_path(i)) == packed_path:
			return i
	return -1


## Reads the state and inner path of the source scene a node inherits from.
## Returns {"resolved": true, "state": null, "path": ""} when the node is not
## inside an instance, and {"resolved": false, ...} when the node lies under
## more than one instance level, where the source row is not reachable in one
## step.
static func _source_state(state: SceneState, target_path: String) -> Dictionary:
	if target_path == ".":
		return {"resolved": true, "state": null, "path": ""}

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
		return {"resolved": true, "state": null, "path": ""}
	if instance_indices.size() > 1:
		return {"resolved": false, "state": null, "path": ""}

	var source: PackedScene = state.get_node_instance(instance_indices[0])
	if source == null:
		return {"resolved": true, "state": null, "path": ""}
	var instance_path := str(state.get_node_path(instance_indices[0]))
	var inner := "" if target_path == instance_path else target_path.substr(instance_path.length() + 1)
	var source_path := "." if inner.is_empty() else "./" + inner
	return {"resolved": true, "state": source.get_state(), "path": source_path}
