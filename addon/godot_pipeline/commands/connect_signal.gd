@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Connects a signal on one node of the currently edited scene to a method on
## another node through the editor's undo/redo stack, so the connection shows
## up as one Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `source_path`, `signal`, `target_path`,
## `method`, and `project_path` (the canonical, symlink-resolved absolute path
## of the project the caller intends to edit), plus optional bool fields
## `deferred` and `one_shot` (default false). The project-path check runs
## before anything else is resolved, and every other check runs before the
## undo action is created, so a rejected request never touches the scene or
## the undo history. The engine never checks the target method or its
## argument count, so this command rejects an unknown method up front; an
## argument-count mismatch still connects and fails only when the signal is
## emitted.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var source_path_value: Variant = request.get("source_path")
	var signal_value: Variant = request.get("signal")
	var target_path_value: Variant = request.get("target_path")
	var method_value: Variant = request.get("method")
	var project_path_value: Variant = request.get("project_path")
	var deferred_value: Variant = request.get("deferred", false)
	var one_shot_value: Variant = request.get("one_shot", false)
	if typeof(source_path_value) != TYPE_STRING or typeof(signal_value) != TYPE_STRING or typeof(target_path_value) != TYPE_STRING or typeof(method_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("connect_signal requires string fields: source_path, signal, target_path, method, project_path")
	if typeof(deferred_value) != TYPE_BOOL or typeof(one_shot_value) != TYPE_BOOL:
		return CommandSupport.error("connect_signal requires boolean fields: deferred, one_shot")

	var source_guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, source_path_value)
	if source_guarded is String:
		return CommandSupport.error(source_guarded)
	var scene_root: Node = source_guarded["scene_root"]
	var source_path: String = source_guarded["node_path"]
	var source: Node = source_guarded["target"]

	var target_guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, target_path_value)
	if target_guarded is String:
		return CommandSupport.error(target_guarded)
	var target_path: String = target_guarded["node_path"]
	var target: Node = target_guarded["target"]

	var signal_name: String = signal_value
	if not source.has_signal(signal_name):
		return CommandSupport.error("unknown signal on %s (%s): %s" % [source_path, source.get_class(), signal_name])

	var method: String = method_value
	if not target.has_method(method):
		return CommandSupport.error("unknown method on %s (%s): %s" % [target_path, target.get_class(), method])

	var callable := Callable(target, method)
	# A connection that only a sub-scene defines is already present on the
	# instance, so connecting the same pair again is a duplicate too.
	if source.is_connected(signal_name, callable):
		return CommandSupport.error("already connected: signal %s on %s is already connected to method %s on %s" % [signal_name, source_path, method, target_path])

	# A connection only survives a save when the edited scene serializes its
	# source node: a source inside an instanced sub-scene needs every instance
	# between it and scene_root to have "Editable Children" on (mirrors
	# Node::get_deepest_editable_node in the engine). Reject the rest up front
	# rather than apply a connection the engine silently drops on save. A
	# target inside a non-editable instance is fine, because the connection is
	# stored on the source.
	var owner_node: Node = source.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			return CommandSupport.error(
				"source node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the connection would not be saved" % source_path
			)
		owner_node = owner_node.owner

	var flags: int = CONNECT_PERSIST
	if deferred_value:
		flags |= CONNECT_DEFERRED
	if one_shot_value:
		flags |= CONNECT_ONE_SHOT

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Connect Signal", UndoRedo.MERGE_DISABLE, scene_root)
	undo_redo.add_do_method(source, "connect", signal_name, callable, flags)
	undo_redo.add_undo_method(source, "disconnect", signal_name, callable)
	undo_redo.commit_action()

	# The engine stores the connection's flags; read them back rather than
	# echo the requested ones, so the reply reflects what was applied.
	var applied_flags := flags
	for connection in source.get_signal_connection_list(signal_name):
		if connection["callable"] == callable:
			applied_flags = connection["flags"]
			break

	return CommandSupport.ok({
		"source_path": source_path,
		"signal": signal_name,
		"target_path": target_path,
		"method": method,
		"flags": applied_flags,
	})
