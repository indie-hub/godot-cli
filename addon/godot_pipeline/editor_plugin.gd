@tool
extends EditorPlugin
## Godot Pipeline bridge.
##
## Listens on a loopback-only TCP socket and answers a single JSON request
## per connection. Supported commands: `status`, `scene_tree`, `rename_node`,
## `create_node`, `set_property`, `delete_node`, and `save_scene`. The read
## commands report the editor's status and the active edited scene's node
## tree; the editing commands change the active scene through the editor's
## undo/redo stack (one Undo/Redo step each) and never save it. `save_scene`
## persists the currently edited scene to the file path it already has, so
## edits made through the other commands survive a reload. Every editing
## command requires a `project_path` that matches the running editor's
## project, validates the whole request before it mutates anything, and
## rejects targets that would not persist when the scene is saved.
## `set_property` additionally rejects an int value that is not one of the
## declared values of an enum-hinted int property. The port is fixed, so only
## one Godot editor instance can host this plugin on a machine at a time.
## Requests and replies are single newline-terminated JSON objects. The plugin
## assembles a request across reads (up to an 8 MiB cap and a 5 second idle
## timeout), so a request may arrive in several TCP segments.

const HOST := "127.0.0.1"
const PORT := 47821

## A request larger than this (in bytes) is rejected without being applied.
## It bounds the buffered request while it is being assembled across reads.
const MAX_REQUEST_BYTES := 8388608
## How long a connection may send no new bytes before the plugin acts: a
## connection that has sent nothing is closed silently, and one that has sent
## an incomplete request is dropped with an error (or handled, if the buffered
## bytes already form a complete JSON object without the trailing newline).
const REQUEST_IDLE_TIMEOUT_MSEC := 5000

## Characters Godot strips from node names (see
## `String::validate_node_name()` in the engine). Rejecting them up front
## means the caller never has to guess whether a name was silently changed.
const INVALID_NAME_CHARACTERS := [".", ":", "@", "/", "\"", "%"]

var _server: TCPServer
var _connection: StreamPeerTCP

var _request_buffer := PackedByteArray()
var _request_scanned := 0
var _request_last_activity_msec := 0


func _enter_tree() -> void:
	_server = TCPServer.new()
	var error := _server.listen(PORT, HOST)
	if error != OK:
		push_error("Godot Pipeline: failed to listen on %s:%d (%s)" % [HOST, PORT, error_string(error)])
		_server = null
		return
	set_process(true)
	print("Godot Pipeline: listening on %s:%d" % [HOST, PORT])


func _exit_tree() -> void:
	set_process(false)
	_reset_connection()
	if _server != null:
		_server.stop()
		_server = null


func _process(_delta: float) -> void:
	if _server == null:
		return
	if _connection == null:
		if not _server.is_connection_available():
			return
		_connection = _server.take_connection()
		_request_buffer = PackedByteArray()
		_request_scanned = 0
		_request_last_activity_msec = Time.get_ticks_msec()
	_connection.poll()
	if _connection.get_status() != StreamPeerSocket.STATUS_CONNECTED:
		_reset_connection()
		return
	var available := _connection.get_available_bytes()
	if available > 0:
		var chunk := _connection.get_data(available)
		if chunk[0] != OK:
			_reset_connection()
			return
		_request_buffer.append_array(chunk[1])
		_request_last_activity_msec = Time.get_ticks_msec()
		if _request_buffer.size() > MAX_REQUEST_BYTES:
			_reply_error(
				"request exceeds the maximum size of %d bytes (received %d bytes without a terminating newline)" % [MAX_REQUEST_BYTES, _request_buffer.size()]
			)
			_reset_connection()
			return
		var newline_index := _scan_for_newline()
		if newline_index >= 0:
			_handle_complete_request(newline_index)
			_reset_connection()
			return
	if _request_buffer.is_empty():
		# The connection has sent nothing; close it once it has been silent
		# for the idle timeout, with no reply.
		if _request_last_activity_msec > 0 and Time.get_ticks_msec() - _request_last_activity_msec > REQUEST_IDLE_TIMEOUT_MSEC:
			_reset_connection()
	else:
		if Time.get_ticks_msec() - _request_last_activity_msec > REQUEST_IDLE_TIMEOUT_MSEC:
			_handle_idle_fallback()
			_reset_connection()


## Scans the request buffer for the first newline byte, starting from the
## offset where the previous scan stopped so old bytes are never re-examined.
## Returns the newline index, or -1 if there is none.
func _scan_for_newline() -> int:
	var index := _request_buffer.find(0x0A, _request_scanned)
	if index >= 0:
		_request_scanned = _request_buffer.size()
		return index
	_request_scanned = _request_buffer.size()
	return -1


## Handles a request whose terminating newline has been received. Only the
## bytes before the first newline belong to the request; anything after it is
## ignored, because the protocol is one request per connection.
func _handle_complete_request(newline_index: int) -> void:
	# Truncate in place (no copy of the request bytes) before decoding, since
	# the buffer is discarded right after the request is handled.
	_request_buffer.resize(newline_index)
	_handle_request(_request_buffer.get_string_from_utf8(), _request_buffer.size())


## Handles a connection that sent bytes but then stalled without a newline.
## A raw client that forgot the trailing newline still works: if the buffered
## bytes parse as a complete JSON request, handle it; otherwise reply with an
## incomplete-request error. Nothing is applied in the error case.
func _handle_idle_fallback() -> void:
	var text := _request_buffer.get_string_from_utf8()
	var parsed: Variant = JSON.parse_string(text.strip_edges())
	if typeof(parsed) == TYPE_DICTIONARY and parsed.has("command"):
		_handle_request(text, _request_buffer.size())
	else:
		_reply_error(
			"malformed request (incomplete: %d bytes received without a terminating newline)" % _request_buffer.size()
		)


## Drops the current connection and its partial request, so state never leaks
## between connections.
func _reset_connection() -> void:
	if _connection != null:
		_connection.disconnect_from_host()
		_connection = null
	_request_buffer = PackedByteArray()
	_request_scanned = 0
	_request_last_activity_msec = 0


func _handle_request(raw_text: String, raw_bytes: int) -> void:
	var parsed: Variant = JSON.parse_string(raw_text.strip_edges())
	if typeof(parsed) != TYPE_DICTIONARY or not parsed.has("command"):
		_reply_error("malformed request (received %d bytes)" % raw_bytes)
		return
	match parsed["command"]:
		"status":
			_reply_ok(_build_status())
		"scene_tree":
			var scene_root := get_editor_interface().get_edited_scene_root()
			if scene_root == null:
				_reply_error("no scene is currently being edited")
			else:
				_reply_ok(_describe_node(scene_root))
		"rename_node":
			_handle_rename_node(parsed)
		"create_node":
			_handle_create_node(parsed)
		"set_property":
			_handle_set_property(parsed)
		"delete_node":
			_handle_delete_node(parsed)
		"save_scene":
			_handle_save_scene(parsed)
		_:
			_reply_error("unknown command: %s" % str(parsed["command"]))


## Renames a node in the currently edited scene through the editor's
## undo/redo stack, so the rename shows up as one Undo/Redo step and marks
## the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself), `new_name`, and `project_path` (the
## canonical, symlink-resolved absolute path of the project the caller
## intends to edit). The project-path check runs before anything else is
## resolved, so a mismatched caller can never cause a mutation.
func _handle_rename_node(request: Dictionary) -> void:
	var node_path_value: Variant = request.get("node_path")
	var new_name_value: Variant = request.get("new_name")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(new_name_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		_reply_error("rename_node requires string fields: node_path, new_name, project_path")
		return

	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		_reply_error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)
		return

	var scene_root := get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		_reply_error("no scene is currently being edited")
		return

	var node_path: String = node_path_value
	if node_path.is_empty():
		_reply_error("node path must not be empty; use \".\" for the scene root")
		return
	if node_path.begins_with("/"):
		_reply_error("node path must be relative to the scene root; absolute paths are rejected")
		return
	if node_path.contains(":"):
		_reply_error("node path must not contain ':'")
		return
	if node_path != "." and ".." in node_path.split("/"):
		_reply_error("node path must not contain '..'")
		return

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var target: Node = scene_root if node_path == "." else scene_root.get_node_or_null(NodePath(node_path))
	if target == null:
		_reply_error("node not found: %s" % node_path)
		return

	var new_name: String = new_name_value
	if new_name.is_empty():
		_reply_error("new name must not be empty")
		return
	for character in INVALID_NAME_CHARACTERS:
		if new_name.contains(character):
			_reply_error("new name must not contain any of: . : @ / \" %")
			return

	var old_name := str(target.name)
	var undo_redo := get_undo_redo()
	undo_redo.create_action("Rename Node")
	undo_redo.add_do_property(target, "name", new_name)
	undo_redo.add_undo_property(target, "name", old_name)
	undo_redo.commit_action()

	# Godot silently re-uniquifies a name that collides with a sibling, so
	# the requested name is not necessarily the name that ended up applied;
	# read it back rather than assume the request was honored verbatim.
	_reply_ok({
		"node_path": node_path,
		"old_name": old_name,
		"requested_name": new_name,
		"name": str(target.name),
	})


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
## mutation.
func _handle_create_node(request: Dictionary) -> void:
	var parent_path_value: Variant = request.get("parent_path")
	var class_name_value: Variant = request.get("class_name")
	var name_value: Variant = request.get("name")
	var project_path_value: Variant = request.get("project_path")
	if typeof(parent_path_value) != TYPE_STRING or typeof(class_name_value) != TYPE_STRING or typeof(name_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		_reply_error("create_node requires string fields: parent_path, class_name, name, project_path")
		return

	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		_reply_error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)
		return

	var scene_root := get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		_reply_error("no scene is currently being edited")
		return

	var parent_path: String = parent_path_value
	if parent_path.is_empty():
		_reply_error("parent path must not be empty; use \".\" for the scene root")
		return
	if parent_path.begins_with("/"):
		_reply_error("parent path must be relative to the scene root; absolute paths are rejected")
		return
	if parent_path.contains(":"):
		_reply_error("parent path must not contain ':'")
		return
	if parent_path != "." and ".." in parent_path.split("/"):
		_reply_error("parent path must not contain '..'")
		return

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var parent: Node = scene_root if parent_path == "." else scene_root.get_node_or_null(NodePath(parent_path))
	if parent == null:
		_reply_error("parent node not found: %s" % parent_path)
		return

	# A new child only saves correctly if Godot will actually serialize it as
	# part of the edited scene: the scene root itself, or any node directly
	# owned by it, both work; a node inside an instanced sub-scene's own
	# internal structure is owned by that instance's own root instead (not
	# by scene_root), and a new_node.owner = scene_root child added under it
	# can appear in the live tree but silently vanish on save. Reject those
	# parents up front rather than mutate the scene and lose the result.
	if parent != scene_root and parent.owner != scene_root:
		_reply_error(
			"parent is not eligible for a new persisted child: %s is not the scene root and is not owned by it (it is likely inside an instanced sub-scene's internal structure); only the scene root or a node it owns is supported" % parent_path
		)
		return

	var requested_class: String = class_name_value
	if not ClassDB.class_exists(requested_class):
		_reply_error("unknown class: %s" % requested_class)
		return
	for global_class in ProjectSettings.get_global_class_list():
		if global_class.get("class") == requested_class:
			_reply_error("class must be a built-in Node class, not a project script class: %s" % requested_class)
			return
	if requested_class != "Node" and not ClassDB.is_parent_class(requested_class, "Node"):
		_reply_error("class is not a Node subclass: %s" % requested_class)
		return
	if not ClassDB.can_instantiate(requested_class):
		_reply_error("class cannot be instantiated: %s" % requested_class)
		return

	var name: String = name_value
	if name.is_empty():
		_reply_error("name must not be empty")
		return
	for character in INVALID_NAME_CHARACTERS:
		if name.contains(character):
			_reply_error("name must not contain any of: . : @ / \" %")
			return

	var new_node: Node = ClassDB.instantiate(requested_class)
	new_node.name = name

	var undo_redo := get_undo_redo()
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
	_reply_ok({
		"parent_path": parent_path,
		"class_name": requested_class,
		"requested_name": name,
		"name": str(new_node.name),
	})


## Sets a property on an existing node in the currently edited scene through
## the editor's undo/redo stack, so the change shows up as one Undo/Redo step
## and marks the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself), `property`, and `project_path` (the
## canonical, symlink-resolved absolute path of the project the caller
## intends to edit), plus a JSON `value` that is coerced to the property's
## declared type (see `_coerce_value`). The project-path check runs before
## anything else is resolved, and every other check runs before the undo
## action is created, so a rejected request never touches the scene or the
## undo history.
func _handle_set_property(request: Dictionary) -> void:
	var node_path_value: Variant = request.get("node_path")
	var property_value: Variant = request.get("property")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(property_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING or not request.has("value"):
		_reply_error("set_property requires string fields: node_path, property, project_path; and a value field")
		return

	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		_reply_error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)
		return

	var scene_root := get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		_reply_error("no scene is currently being edited")
		return

	var node_path: String = node_path_value
	if node_path.is_empty():
		_reply_error("node path must not be empty; use \".\" for the scene root")
		return
	if node_path.begins_with("/"):
		_reply_error("node path must be relative to the scene root; absolute paths are rejected")
		return
	if node_path.contains(":"):
		_reply_error("node path must not contain ':'")
		return
	if node_path != "." and ".." in node_path.split("/"):
		_reply_error("node path must not contain '..'")
		return

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var target: Node = scene_root if node_path == "." else scene_root.get_node_or_null(NodePath(node_path))
	if target == null:
		_reply_error("node not found: %s" % node_path)
		return

	# A property change only survives a save if the edited scene serializes
	# it: local nodes always, nodes inside an instanced sub-scene only when
	# every instance between them and scene_root has "Editable Children" on
	# (mirrors Node::get_deepest_editable_node in the engine). Reject the rest
	# up front rather than apply a change that silently vanishes on save.
	var owner_node: Node = target.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			_reply_error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the change would not be saved" % node_path
			)
			return
		owner_node = owner_node.owner

	var property: String = property_value
	var property_info: Dictionary = {}
	for candidate in target.get_property_list():
		if candidate["name"] == property:
			property_info = candidate
			break
	if property_info.is_empty():
		_reply_error("unknown property on %s (%s): %s" % [node_path, target.get_class(), property])
		return
	# Only inspector-visible or serialized properties are settable; this
	# excludes internal ones like `name` (use rename_node) and derived ones
	# like `global_position` that are not part of the scene's saved state.
	var usage: int = property_info["usage"]
	if usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE) == 0:
		_reply_error("property is not editable in the inspector or saved with the scene: %s" % property)
		return
	if usage & PROPERTY_USAGE_READ_ONLY:
		_reply_error("property is read-only: %s" % property)
		return

	var property_type: int = property_info["type"]
	var old_value: Variant = target.get(property)

	# A plain `Array` has no declared element type, so a JSON value could not
	# be coerced without guessing (Vector2 versus a 2-number array, int versus
	# float); only typed `Array[T]` and the Packed*Array types are accepted.
	var array_element_type: int = TYPE_NIL
	if property_type == TYPE_ARRAY:
		if typeof(old_value) != TYPE_ARRAY or not old_value.is_typed():
			_reply_error(
				"untyped Array property %s is not supported by set-property (an Array has no declared element type to coerce to)" % property
			)
			return
		array_element_type = old_value.get_typed_builtin()
		if array_element_type == TYPE_OBJECT:
			_reply_error(
				"typed array of unsupported element type %s on %s (Object/Node/Resource element types are not supported)" % [old_value.get_typed_class_name(), property]
			)
			return
		if not _is_supported_array_element(array_element_type):
			_reply_error(
				"typed array of unsupported element type %s on %s (%s is not a supported set-property element type)" % [type_string(array_element_type), property, type_string(array_element_type)]
			)
			return

	var coerced := _coerce_value(request["value"], property_type, array_element_type, old_value)
	if coerced.has("error"):
		_reply_error("cannot set %s (%s): %s" % [property, type_string(property_type), coerced["error"]])
		return
	var new_value: Variant = coerced["value"]

	# An enum-hinted int property only accepts the values declared in its
	# hint_string (see `_enum_value_list`); anything else is rejected before
	# any undo action is created.
	if property_type == TYPE_INT and property_info["hint"] == PROPERTY_HINT_ENUM:
		if not (int(new_value) in _enum_value_list(property_info["hint_string"])):
			_reply_error(
				"value %d is not one of the declared enum values for %s (%s)" % [new_value, property, property_info["hint_string"]]
			)
			return

	var undo_redo := get_undo_redo()
	undo_redo.create_action("Set %s" % property)
	undo_redo.add_do_property(target, property, new_value)
	undo_redo.add_undo_property(target, property, old_value)
	undo_redo.commit_action()

	# Setters may clamp or normalize (e.g. a ranged float), so read the value
	# back rather than echo the request. var_to_str keeps Vector2/Color/etc.
	# unambiguous, which plain JSON.stringify would not.
	_reply_ok({
		"node_path": node_path,
		"property": property,
		"type": type_string(property_type),
		"old_value": var_to_str(old_value),
		"value": var_to_str(target.get(property)),
	})


func _handle_delete_node(request: Dictionary) -> void:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		_reply_error("delete_node requires string fields: node_path, project_path")
		return

	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		_reply_error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)
		return

	var scene_root := get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		_reply_error("no scene is currently being edited")
		return

	var node_path: String = node_path_value
	if node_path.is_empty():
		_reply_error("node path must not be empty; use \".\" for the scene root")
		return
	if node_path.begins_with("/"):
		_reply_error("node path must be relative to the scene root; absolute paths are rejected")
		return
	if node_path.contains(":"):
		_reply_error("node path must not contain ':'")
		return
	if node_path != "." and ".." in node_path.split("/"):
		_reply_error("node path must not contain '..'")
		return

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var target: Node = scene_root if node_path == "." else scene_root.get_node_or_null(NodePath(node_path))
	if target == null:
		_reply_error("node not found: %s" % node_path)
		return
	if target == scene_root:
		_reply_error("the scene root cannot be deleted")
		return

	# Deleting inside an instanced sub-scene only survives a save when every
	# instance between the target and scene_root has "Editable Children" on;
	# otherwise the removal silently reappears on reload. Reject those up
	# front rather than mutate the scene and lose the result.
	var owner_node: Node = target.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			_reply_error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the deletion would not be saved" % node_path
			)
			return
		owner_node = owner_node.owner

	# Captured before the node leaves the tree, so the undo step can restore
	# it to its exact prior position and every node to its exact prior owner
	# (the scene root for local nodes, an instance root for a node inside an
	# editable instanced sub-scene).
	var parent := target.get_parent()
	var index := target.get_index()
	var owners: Array = []
	_collect_owners(target, owners)

	var undo_redo := get_undo_redo()
	undo_redo.create_action("Delete Node")
	undo_redo.add_do_method(parent, "remove_child", target)
	undo_redo.add_undo_method(parent, "add_child", target, true)
	undo_redo.add_undo_method(parent, "move_child", target, index)
	undo_redo.add_undo_method(self, "_restore_owners", owners)
	undo_redo.add_undo_reference(target)
	undo_redo.commit_action()

	_reply_ok({
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
func _collect_owners(node: Node, entries: Array) -> void:
	entries.append([node, node.owner])
	for child in node.get_children():
		_collect_owners(child, entries)


func _restore_owners(entries: Array) -> void:
	for entry in entries:
		if entry is Array and entry.size() == 2 and entry[0] is Node:
			entry[0].owner = entry[1]


## Saves the currently edited scene to the file path it already has, so edits
## made through the other commands persist on disk. This is the one command
## that writes the scene file; it never prompts for a location (save-as and
## creating new files are out of scope).
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to save).
## The project-path check runs before anything else is resolved, so a
## mismatched caller can never cause a write. A missing scene, or an open
## scene with no file path yet (an unsaved new scene), is rejected without
## writing anything.
func _handle_save_scene(request: Dictionary) -> void:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		_reply_error("save_scene requires a string field: project_path")
		return

	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		_reply_error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)
		return

	var scene_root := get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		_reply_error("no scene is currently being edited")
		return

	var scene_path := scene_root.scene_file_path
	if scene_path.is_empty():
		_reply_error("the open scene has no file path yet; save it to a file before using save_scene")
		return

	# The editor's own save path (same as Ctrl+S) writes the scene to the
	# path it already has, clears the dirty flag, and handles the .uid
	# sidecar; it reports failure through its Error return value.
	var error := get_editor_interface().save_scene()
	if error != OK:
		_reply_error("failed to save the scene: %s" % error_string(error))
		return

	_reply_ok({"path": scene_path})


## Converts a JSON-decoded `value` to `property_type`. Returns
## `{"value": converted}` on success or `{"error": message}` otherwise; it
## never guesses across types (a string is never parsed as a number, and a
## number is never stringified). Godot's JSON parser yields every number as
## a float, so ints are accepted only when the float is integral.
##
## Accepted shapes: bool -> true/false; int -> integral number; float ->
## number; String/StringName/NodePath -> string; Vector2 -> [x, y];
## Vector3 -> [x, y, z]; Vector2i -> [x, y]; Vector3i -> [x, y, z];
## Vector4 -> [x, y, z, w]; Vector4i -> [x, y, z, w]; Rect2 -> [x, y, w, h]
## (position then size); Rect2i -> [x, y, w, h]; Transform2D ->
## [[xx, xy], [yx, yy], [ox, oy]] (x axis, y axis, origin);
## Transform3D -> [[bxx, bxy, bxz], [byx, byy, byz], [bzx, bzy, bzz],
## [ox, oy, oz]] (the three basis column vectors, then the origin); Color ->
## "#rrggbb[aa]", "rrggbb", a named color such as "red", or [r, g, b] /
## [r, g, b, a] in 0..1 floats. The int-vector and Rect2i components are
## signed 32-bit integers: a fractional or out-of-range component is rejected
## up front, because Godot stores it as int32 and would silently wrap.
##
## Arrays are accepted for the Packed*Array types and for typed `Array[T]`
## whose element type is one of the types above; `value` is a JSON array and
## every element is coerced by the same per-type rules (an element error names
## its index). PackedByteArray elements are integers 0..255, PackedInt32Array
## elements follow the int32 rule, and PackedInt64Array elements follow the
## int rule. Untyped `Array` properties are rejected by the caller, never
## coerced by guessing. `array_element_type` and `array_template` are only
## used for `TYPE_ARRAY`: the template supplies the typed array to fill.
func _coerce_value(value: Variant, property_type: int, array_element_type: int = TYPE_NIL, array_template: Variant = null) -> Dictionary:
	match property_type:
		TYPE_ARRAY:
			return _coerce_typed_array(value, array_element_type, array_template)
		TYPE_PACKED_BYTE_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_INT64_ARRAY, TYPE_PACKED_FLOAT32_ARRAY, TYPE_PACKED_FLOAT64_ARRAY, TYPE_PACKED_STRING_ARRAY, TYPE_PACKED_VECTOR2_ARRAY, TYPE_PACKED_VECTOR3_ARRAY, TYPE_PACKED_VECTOR4_ARRAY, TYPE_PACKED_COLOR_ARRAY:
			return _coerce_packed_array(value, property_type)
	if not _is_supported_array_element(property_type):
		return {"error": "property type %s is not supported by set_property" % type_string(property_type)}
	var error_out := []
	var coerced := _coerce_element(value, property_type, TYPE_NIL, error_out)
	if error_out.is_empty():
		return {"value": coerced}
	return {"error": error_out[0]}


## Coerces one array element (or one scalar `set-property` value) of
## `element_type` into the matching Variant without allocating a result
## Dictionary per element. Returns the coerced value; on failure it stores the
## error message in `error_out[0]` (an array the caller allocates once per
## request) and returns null. `packed_type` selects the byte/int32 element
## rules for the two packed types that need them; it is TYPE_NIL otherwise.
func _coerce_element(value: Variant, element_type: int, packed_type: int, error_out: Array) -> Variant:
	if packed_type == TYPE_PACKED_BYTE_ARRAY:
		if not _is_number(value):
			error_out.append("expected an integer")
			return null
		var byte_value := float(value)
		if byte_value != floorf(byte_value) or byte_value < 0.0 or byte_value > 255.0:
			error_out.append("expected an integer in 0..255, got %s" % str(value))
			return null
		return int(byte_value)
	if packed_type == TYPE_PACKED_INT32_ARRAY:
		if not _is_number(value):
			error_out.append("expected an integer")
			return null
		var int32_value := float(value)
		if int32_value != floorf(int32_value) or int32_value < -2147483648.0 or int32_value > 2147483647.0:
			error_out.append("expected an integer in -2147483648..2147483647, got %s" % str(value))
			return null
		return int(int32_value)
	match element_type:
		TYPE_BOOL:
			if typeof(value) == TYPE_BOOL:
				return value
			error_out.append("expected true or false")
			return null
		TYPE_INT:
			if not _is_number(value):
				error_out.append("expected an integer")
				return null
			var number := float(value)
			# 2^53: beyond this a JSON float can no longer represent every
			# integer exactly, so the caller's value may already be lost.
			if number != floorf(number) or absf(number) > 9007199254740992.0:
				error_out.append("expected an integer, got %s" % str(value))
				return null
			return int(number)
		TYPE_FLOAT:
			if not _is_number(value):
				error_out.append("expected a number")
				return null
			return float(value)
		TYPE_STRING:
			if typeof(value) != TYPE_STRING:
				error_out.append("expected a string")
				return null
			return value
		TYPE_STRING_NAME:
			if typeof(value) != TYPE_STRING:
				error_out.append("expected a string")
				return null
			return StringName(value)
		TYPE_NODE_PATH:
			if typeof(value) != TYPE_STRING:
				error_out.append("expected a node path string")
				return null
			return NodePath(value)
		TYPE_VECTOR2:
			if not _is_number_array(value, 2):
				error_out.append("expected [x, y]")
				return null
			return Vector2(value[0], value[1])
		TYPE_VECTOR3:
			if not _is_number_array(value, 3):
				error_out.append("expected [x, y, z]")
				return null
			return Vector3(value[0], value[1], value[2])
		TYPE_VECTOR2I:
			var v2i := _coerce_int_vector(value, 2, "[x, y]", error_out)
			if error_out.is_empty():
				return Vector2i(v2i[0], v2i[1])
			return null
		TYPE_VECTOR3I:
			var v3i := _coerce_int_vector(value, 3, "[x, y, z]", error_out)
			if error_out.is_empty():
				return Vector3i(v3i[0], v3i[1], v3i[2])
			return null
		TYPE_VECTOR4:
			if not _is_number_array(value, 4):
				error_out.append("expected [x, y, z, w]")
				return null
			return Vector4(value[0], value[1], value[2], value[3])
		TYPE_VECTOR4I:
			var v4i := _coerce_int_vector(value, 4, "[x, y, z, w]", error_out)
			if error_out.is_empty():
				return Vector4i(v4i[0], v4i[1], v4i[2], v4i[3])
			return null
		TYPE_RECT2:
			if not _is_number_array(value, 4):
				error_out.append("expected [x, y, w, h]")
				return null
			return Rect2(value[0], value[1], value[2], value[3])
		TYPE_RECT2I:
			var ri := _coerce_int_vector(value, 4, "[x, y, w, h]", error_out)
			if error_out.is_empty():
				return Rect2i(ri[0], ri[1], ri[2], ri[3])
			return null
		TYPE_COLOR:
			if typeof(value) == TYPE_STRING:
				# Color.from_string falls back to its default for anything that
				# is neither a valid HTML color nor a named color, so parsing with
				# two different defaults and comparing detects invalid input.
				var parsed := Color.from_string(value, Color(0, 0, 0, 0))
				if parsed != Color.from_string(value, Color(1, 1, 1, 1)):
					error_out.append("expected an HTML color (\"#rrggbb\") or a named color, got \"%s\"" % value)
					return null
				return parsed
			if _is_number_array(value, 3):
				return Color(value[0], value[1], value[2])
			if _is_number_array(value, 4):
				return Color(value[0], value[1], value[2], value[3])
			error_out.append("expected an HTML/named color string, [r, g, b], or [r, g, b, a]")
			return null
		TYPE_TRANSFORM2D:
			var m2 := _number_matrix(value, 3, 2)
			if m2.has("error"):
				error_out.append("expected [[xx, xy], [yx, yy], [ox, oy]]: %s" % m2["error"])
				return null
			var t2: Array = m2["value"]
			return Transform2D(Vector2(t2[0][0], t2[0][1]), Vector2(t2[1][0], t2[1][1]), Vector2(t2[2][0], t2[2][1]))
		TYPE_TRANSFORM3D:
			var m3 := _number_matrix(value, 4, 3)
			if m3.has("error"):
				error_out.append("expected [[bxx, bxy, bxz], [byx, byy, byz], [bzx, bzy, bzz], [ox, oy, oz]]: %s" % m3["error"])
				return null
			var t3: Array = m3["value"]
			var basis := Basis(Vector3(t3[0][0], t3[0][1], t3[0][2]), Vector3(t3[1][0], t3[1][1], t3[1][2]), Vector3(t3[2][0], t3[2][1], t3[2][2]))
			return Transform3D(basis, Vector3(t3[3][0], t3[3][1], t3[3][2]))
	return null


## Converts a JSON array of `size` numbers into an array of signed 32-bit
## integers, or reports the offending component through `error_out`. Returns
## the ints on success and an empty array on failure; the error message names
## `shape`. Godot stores these components as int32, so a fractional or
## out-of-range number would be silently truncated or wrapped.
func _coerce_int_vector(value: Variant, size: int, shape: String, error_out: Array) -> Array:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != size:
		error_out.append("expected %s of integers in -2147483648..2147483647" % shape)
		return []
	var bad := []
	var ints: Array = []
	for element in value:
		if bad.is_empty():
			ints.append(_coerce_int32_component(element, bad))
	if not bad.is_empty():
		error_out.append("expected %s of integers in -2147483648..2147483647, got %s" % [shape, str(bad[0])])
		return []
	return ints


## Coerces a single signed 32-bit component, reporting the offending value
## through `bad_out` on failure. Returns 0 when it fails.
func _coerce_int32_component(value: Variant, bad_out: Array) -> int:
	if not _is_number(value):
		bad_out.append(value)
		return 0
	var number := float(value)
	if number != floorf(number) or number < -2147483648.0 or number > 2147483647.0:
		bad_out.append(value)
		return 0
	return int(number)


func _is_number(value: Variant) -> bool:
	return typeof(value) == TYPE_FLOAT or typeof(value) == TYPE_INT


## Returns the int values a PROPERTY_HINT_ENUM int property accepts, mirroring
## the engine's `EditorPropertyEnum::setup()`: an entry without ':' takes the
## running 0-based index, an entry with ':' sets the running value to the
## number after the colon, and each entry's value is then bumped by one. So
## "A,B,C" implies 0, 1, 2 and "A:5,B:10" implies 5, 10.
func _enum_value_list(hint_string: String) -> Array:
	var values: Array = []
	var current_value := 0
	for raw_option in hint_string.split(","):
		if raw_option.get_slice_count(":") != 1:
			current_value = int(raw_option.get_slice(":", 1))
		values.append(current_value)
		current_value += 1
	return values


func _is_number_array(value: Variant, size: int) -> bool:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != size:
		return false
	for element in value:
		if not _is_number(element):
			return false
	return true


## Validates `value` as a `rows` x `cols` JSON array of numbers (an array of
## `rows` arrays, each of `cols` numbers). Returns `{"value": rows}` or
## `{"error": message}`. Used by the transform types, whose wire shape nests
## the axis and origin vectors.
func _number_matrix(value: Variant, rows: int, cols: int) -> Dictionary:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != rows:
		return {"error": "expected %d rows" % rows}
	for row in value:
		if not _is_number_array(row, cols):
			return {"error": "expected each row to be %d numbers" % cols}
	return {"value": value}


## Coerces a JSON array `value` into a typed `Array[T]` where T is
## `element_type`, filling a duplicate of the current `template` (already a
## typed array of T) so the result keeps the property's exact typed array type
## and is assignable back to it. The original array is never mutated. A bad
## element is rejected with its index in the message.
func _coerce_typed_array(value: Variant, element_type: int, template: Variant) -> Dictionary:
	if typeof(value) != TYPE_ARRAY:
		return {"error": "expected a JSON array"}
	if typeof(template) != TYPE_ARRAY or not template.is_typed():
		return {"error": "the property has no typed array value to build from"}
	var result: Array = template.duplicate()
	result.resize(0)
	var message := _coerce_array_elements(value, element_type, TYPE_NIL, result)
	if not message.is_empty():
		return {"error": message}
	return {"value": result}


## Coerces a JSON array `value` into the Packed*Array type `packed_type`,
## applying each element type's own rule (see `_packed_element_type` and the
## byte/int32 special cases). A bad element is rejected with its index.
func _coerce_packed_array(value: Variant, packed_type: int) -> Dictionary:
	var element_type := _packed_element_type(packed_type)
	if typeof(value) != TYPE_ARRAY:
		return {"error": "expected a JSON array"}
	var result: Variant
	match packed_type:
		TYPE_PACKED_BYTE_ARRAY:
			result = PackedByteArray()
		TYPE_PACKED_INT32_ARRAY:
			result = PackedInt32Array()
		TYPE_PACKED_INT64_ARRAY:
			result = PackedInt64Array()
		TYPE_PACKED_FLOAT32_ARRAY:
			result = PackedFloat32Array()
		TYPE_PACKED_FLOAT64_ARRAY:
			result = PackedFloat64Array()
		TYPE_PACKED_STRING_ARRAY:
			result = PackedStringArray()
		TYPE_PACKED_VECTOR2_ARRAY:
			result = PackedVector2Array()
		TYPE_PACKED_VECTOR3_ARRAY:
			result = PackedVector3Array()
		TYPE_PACKED_VECTOR4_ARRAY:
			result = PackedVector4Array()
		TYPE_PACKED_COLOR_ARRAY:
			result = PackedColorArray()
		_:
			return {"error": "packed array type %s is not supported" % type_string(packed_type)}
	var message := _coerce_array_elements(value, element_type, packed_type, result)
	if not message.is_empty():
		return {"error": message}
	return {"value": result}


## Coerces each element of the JSON array `value` into `result` (a typed
## `Array` or a Packed*Array) and returns the error message for the first bad
## element, or an empty string on success. The scalar element rules are
## inlined here so a large numeric or string array is coerced without a
## function call, a `match`, or a Dictionary per element; the compound element
## types (vectors, transforms, colors) fall back to `_coerce_element`, which
## is fine because those arrays are small in practice. `packed_type` selects
## the byte/int32 rules for the two packed types that need them.
func _coerce_array_elements(value: Array, element_type: int, packed_type: int, result: Variant) -> String:
	if packed_type == TYPE_PACKED_BYTE_ARRAY:
		for i in range(value.size()):
			var element: Variant = value[i]
			if not _is_number(element):
				return "element %d: expected an integer" % i
			var byte_value := float(element)
			if byte_value != floorf(byte_value) or byte_value < 0.0 or byte_value > 255.0:
				return "element %d: expected an integer in 0..255, got %s" % [i, str(element)]
			result.append(int(byte_value))
		return ""
	if packed_type == TYPE_PACKED_INT32_ARRAY:
		for i in range(value.size()):
			var element: Variant = value[i]
			if not _is_number(element):
				return "element %d: expected an integer" % i
			var int32_value := float(element)
			if int32_value != floorf(int32_value) or int32_value < -2147483648.0 or int32_value > 2147483647.0:
				return "element %d: expected an integer in -2147483648..2147483647, got %s" % [i, str(element)]
			result.append(int(int32_value))
		return ""
	match element_type:
		TYPE_BOOL:
			for i in range(value.size()):
				var element: Variant = value[i]
				if typeof(element) != TYPE_BOOL:
					return "element %d: expected true or false" % i
				result.append(element)
		TYPE_INT:
			for i in range(value.size()):
				var element: Variant = value[i]
				if not _is_number(element):
					return "element %d: expected an integer" % i
				var number := float(element)
				# 2^53: beyond this a JSON float can no longer represent every
				# integer exactly, so the caller's value may already be lost.
				if number != floorf(number) or absf(number) > 9007199254740992.0:
					return "element %d: expected an integer, got %s" % [i, str(element)]
				result.append(int(number))
		TYPE_FLOAT:
			for i in range(value.size()):
				var element: Variant = value[i]
				if typeof(element) != TYPE_FLOAT and typeof(element) != TYPE_INT:
					return "element %d: expected a number" % i
				result.append(float(element))
		TYPE_STRING:
			for i in range(value.size()):
				var element: Variant = value[i]
				if typeof(element) != TYPE_STRING:
					return "element %d: expected a string" % i
				result.append(element)
		_:
			var error_out := []
			for i in range(value.size()):
				var coerced := _coerce_element(value[i], element_type, packed_type, error_out)
				if not error_out.is_empty():
					return "element %d: %s" % [i, error_out[0]]
				result.append(coerced)
	return ""


## Maps each Packed*Array property type to its element Variant type, so the
## element coercion rules in `_coerce_element` can be reused. PackedByteArray
## and PackedInt32Array elements are NOT mapped here: `_coerce_element` gives
## them their own range rules.
func _packed_element_type(packed_type: int) -> int:
	match packed_type:
		TYPE_PACKED_BYTE_ARRAY:
			return TYPE_INT
		TYPE_PACKED_INT32_ARRAY:
			return TYPE_INT
		TYPE_PACKED_INT64_ARRAY:
			return TYPE_INT
		TYPE_PACKED_FLOAT32_ARRAY:
			return TYPE_FLOAT
		TYPE_PACKED_FLOAT64_ARRAY:
			return TYPE_FLOAT
		TYPE_PACKED_STRING_ARRAY:
			return TYPE_STRING
		TYPE_PACKED_VECTOR2_ARRAY:
			return TYPE_VECTOR2
		TYPE_PACKED_VECTOR3_ARRAY:
			return TYPE_VECTOR3
		TYPE_PACKED_VECTOR4_ARRAY:
			return TYPE_VECTOR4
		TYPE_PACKED_COLOR_ARRAY:
			return TYPE_COLOR
	return TYPE_NIL


## The element types a typed `Array[T]` may use: exactly the scalar, vector,
## and color types `set-property` already supports. Everything else (Object,
## Node, Resource, Dictionary, nested Array, Variant) is rejected by the
## caller before any coercion.
func _is_supported_array_element(element_type: int) -> bool:
	return element_type in [
		TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING, TYPE_STRING_NAME, TYPE_NODE_PATH,
		TYPE_VECTOR2, TYPE_VECTOR3, TYPE_VECTOR4, TYPE_VECTOR2I, TYPE_VECTOR3I, TYPE_VECTOR4I,
		TYPE_RECT2, TYPE_RECT2I, TYPE_TRANSFORM2D, TYPE_TRANSFORM3D, TYPE_COLOR,
	]


func _build_status() -> Dictionary:
	var scene_root := get_editor_interface().get_edited_scene_root()
	return {
		"editor": "Godot Editor",
		"version": Engine.get_version_info()["string"],
		"playing": get_editor_interface().is_playing_scene(),
		"scene_path": scene_root.scene_file_path if scene_root != null else null,
	}


func _describe_node(node: Node) -> Dictionary:
	var children: Array = []
	for child in node.get_children():
		children.append(_describe_node(child))
	return {
		"name": str(node.name),
		"type": node.get_class(),
		"children": children,
	}


func _reply_ok(data: Variant) -> void:
	_reply({"status": "ok", "data": data})


func _reply_error(message: String) -> void:
	_reply({"status": "error", "message": message})


func _reply(payload: Dictionary) -> void:
	if _connection == null:
		return
	# put_utf8_string() prepends a 32-bit length header; this protocol is
	# plain newline-delimited JSON, so write raw bytes instead.
	var text := JSON.stringify(payload) + "\n"
	_connection.put_data(text.to_utf8_buffer())
