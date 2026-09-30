@tool
extends EditorPlugin
## Godot Pipeline bridge.
##
## Listens on a loopback-only TCP socket and answers a single JSON request
## per connection. Supported commands: `status`, `scene_tree`, `inspect_node`,
## `query_nodes`, `rename_node`, `create_node`, `set_property`, `delete_node`,
## and `save_scene`. The read commands report the editor's status, the active
## edited scene's node tree, a node's class/child count/property values, and
## the nodes matching a class, group, and name search; the editing commands
## change the active scene through the editor's undo/redo stack (one Undo/Redo
## step each) and never save it. `save_scene`
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
## The file is laid out as the socket server, the request dispatch and command
## handlers, and the shared request guard `_guard_request`; the pure value
## conversions between JSON and Godot Variants live in the preloaded
## `value_codec.gd`.

const ValueCodec := preload("value_codec.gd")

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
		"inspect_node":
			_handle_inspect_node(parsed)
		"query_nodes":
			_handle_query_nodes(parsed)
		"delete_node":
			_handle_delete_node(parsed)
		"save_scene":
			_handle_save_scene(parsed)
		_:
			_reply_error("unknown command: %s" % str(parsed["command"]))


## Resolves the request fields every scene command checks in the same fixed
## order: the project path first (so a mismatched caller can never cause a
## mutation or a read), then the edited scene root, then the node path and its
## target when the command has one. Replies with the exact error message and
## returns an empty Dictionary on the first failure; otherwise returns the
## resolved objects. The handlers keep their own per-command field-type
## checks, so the values passed here are already known to be strings. Commands
## without a node path omit `node_path_value`, which skips the node-path checks.
func _guard_request(project_path_value: Variant, node_path_value: Variant = null) -> Dictionary:
	var current_project_path := ProjectSettings.globalize_path("res://").rstrip("/")
	var requested_project_path: String = (project_path_value as String).rstrip("/")
	if requested_project_path != current_project_path:
		_reply_error(
			"project path mismatch: this editor has %s open, not %s" % [current_project_path, requested_project_path]
		)
		return {}
	var scene_root := get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		_reply_error("no scene is currently being edited")
		return {}
	if node_path_value == null:
		return {"scene_root": scene_root}
	var node_path: String = node_path_value
	if node_path.is_empty():
		_reply_error("node path must not be empty; use \".\" for the scene root")
		return {}
	if node_path.begins_with("/"):
		_reply_error("node path must be relative to the scene root; absolute paths are rejected")
		return {}
	if node_path.contains(":"):
		_reply_error("node path must not contain ':'")
		return {}
	if node_path != "." and ".." in node_path.split("/"):
		_reply_error("node path must not contain '..'")
		return {}

	# Resolving relative to scene_root (rather than any absolute NodePath)
	# confines the target to the edited scene's own node tree, which is the
	# only part of the running editor this command may touch.
	var target: Node = scene_root if node_path == "." else scene_root.get_node_or_null(NodePath(node_path))
	if target == null:
		_reply_error("node not found: %s" % node_path)
		return {}
	return {"scene_root": scene_root, "node_path": node_path, "target": target}


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

	var guarded := _guard_request(project_path_value, node_path_value)
	if guarded.is_empty():
		return
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

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
## declared type (see `ValueCodec.coerce_value`). The project-path check runs before
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

	var guarded := _guard_request(project_path_value, node_path_value)
	if guarded.is_empty():
		return
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

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
		if not ValueCodec.is_supported_array_element(array_element_type):
			_reply_error(
				"typed array of unsupported element type %s on %s (%s is not a supported set-property element type)" % [type_string(array_element_type), property, type_string(array_element_type)]
			)
			return

	var coerced := ValueCodec.coerce_value(request["value"], property_type, array_element_type, old_value)
	if coerced.has("error"):
		_reply_error("cannot set %s (%s): %s" % [property, type_string(property_type), coerced["error"]])
		return
	var new_value: Variant = coerced["value"]

	# An enum-hinted int property only accepts the values declared in its
	# hint_string (see `ValueCodec.enum_value_list`); anything else is rejected
	# before any undo action is created.
	if property_type == TYPE_INT and property_info["hint"] == PROPERTY_HINT_ENUM:
		if not (int(new_value) in ValueCodec.enum_value_list(property_info["hint_string"])):
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


## Reports a node's class, child count, and the current values of its
## inspector and storage properties without changing the scene: no undo/redo
## action is created, no dirty flag is set, and no file is written. Reads are
## safe inside an instanced sub-scene regardless of Editable Children, so no
## ownership guard applies.
##
## `request` must carry string fields `node_path` (relative to the edited
## scene root, "." for the root itself) and `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to read).
## The project-path check runs before anything else is resolved, so a
## mismatched caller is rejected before any property is read.
func _handle_inspect_node(request: Dictionary) -> void:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		_reply_error("inspect_node requires string fields: node_path, project_path")
		return

	var guarded := _guard_request(project_path_value, node_path_value)
	if guarded.is_empty():
		return
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

	var properties: Array = []
	for info in target.get_property_list():
		# Only inspector-visible or serialized properties are reported, the
		# same visibility `set-property` addresses (read-only ones included,
		# marked by `read_only`). Category and group header entries carry no
		# EDITOR or STORAGE usage bits, so the check below excludes them too.
		var usage: int = info["usage"]
		if usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE) == 0:
			continue
		var entry := {
			"name": info["name"],
			"type": type_string(info["type"]),
			"read_only": usage & PROPERTY_USAGE_READ_ONLY != 0,
		}
		var json_value: Variant = ValueCodec.value_to_json(target.get(info["name"]))
		if json_value == null:
			entry["value"] = null
			entry["supported"] = false
		else:
			entry["value"] = json_value
		properties.append(entry)

	_reply_ok({
		"path": node_path,
		"name": str(target.name),
		"type": target.get_class(),
		"child_count": target.get_child_count(),
		"properties": properties,
	})


## Searches the edited scene's whole node tree for nodes matching every
## provided filter, in tree order (parent before children, siblings in
## order), root included, and reports up to `limit` matches. Reads only: no
## undo/redo action, no dirty flag, no file write. Nodes inside an instanced
## sub-scene are included, so no Editable Children guard applies.
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to read).
## Optional string filters `class`, `group`, and `name` combine with AND;
## `class` must name an engine class (subclasses match), `group` is any group
## name, and `name` is a case-sensitive glob (`*` and `?`) matched with
## `String.match`. `limit` is a JSON number defaulting to 100; it must be an
## integer in 1..1000. The project-path check runs before anything else, then
## the edited-scene check, then the filter and limit validation.
func _handle_query_nodes(request: Dictionary) -> void:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		_reply_error("query_nodes requires a string field: project_path")
		return

	var guarded := _guard_request(project_path_value)
	if guarded.is_empty():
		return
	var scene_root: Node = guarded["scene_root"]

	var class_filter := _optional_string(request, "class")
	if class_filter != "":
		if not ClassDB.class_exists(class_filter):
			_reply_error("unknown class: %s" % class_filter)
			return
		for global_class in ProjectSettings.get_global_class_list():
			if global_class.get("class") == class_filter:
				_reply_error("class must be a built-in class, not a project script class: %s" % class_filter)
				return

	var limit := _validate_limit(request.get("limit", 100))
	if limit.has("error"):
		_reply_error(limit["error"])
		return

	var matches: Array = []
	_collect_query_nodes(
		scene_root,
		scene_root,
		class_filter,
		_optional_string(request, "group"),
		_optional_string(request, "name"),
		limit["value"],
		matches
	)
	var truncated: bool = matches.size() > int(limit["value"])
	if truncated:
		matches.resize(limit["value"])
	_reply_ok({"nodes": matches, "truncated": truncated})


## Reads an optional string filter from `request`; a missing or non-string
## value counts as no filter (an empty string).
func _optional_string(request: Dictionary, key: String) -> String:
	var value: Variant = request.get(key)
	if typeof(value) != TYPE_STRING:
		return ""
	return value


## Validates the request's `limit` (a JSON number, defaulting to 100) as an
## integer in 1..1000. Returns `{"value": int}` on success or
## `{"error": message}` otherwise; a fractional or non-numeric value is not an
## integer, and an integer outside the range is rejected up front.
func _validate_limit(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 1 or int(value) > 1000:
			return {"error": "limit must be between 1 and 1000"}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "limit must be an integer"}
		if number < 1.0 or number > 1000.0:
			return {"error": "limit must be between 1 and 1000"}
		return {"value": int(number)}
	return {"error": "limit must be an integer"}


## Appends every node at or below `node` that matches all the filters, in
## tree order, stopping once more than `limit` matches are collected. Returns
## true when the caller should stop searching (the cap is reached), so a large
## scene is not walked past the point where the result is already decided.
func _collect_query_nodes(node: Node, scene_root: Node, class_filter: String, group_filter: String, name_filter: String, limit: int, matches: Array) -> bool:
	if matches.size() <= limit and _node_matches(node, class_filter, group_filter, name_filter):
		var path := "." if node == scene_root else str(scene_root.get_path_to(node))
		var entry: Dictionary = {}
		entry["path"] = path
		entry["name"] = str(node.name)
		entry["type"] = node.get_class()
		matches.append(entry)
	if matches.size() > limit:
		return true
	for child in node.get_children():
		if _collect_query_nodes(child, scene_root, class_filter, group_filter, name_filter, limit, matches):
			return true
	return false


## Whether `node` matches every non-empty filter: the class (via is_class, so
## subclasses match), the group, and the name glob (`String.match`, which is
## case-sensitive by default).
func _node_matches(node: Node, class_filter: String, group_filter: String, name_filter: String) -> bool:
	if class_filter != "" and not node.is_class(class_filter):
		return false
	if group_filter != "" and not node.is_in_group(group_filter):
		return false
	if name_filter != "" and not str(node.name).match(name_filter):
		return false
	return true


func _handle_delete_node(request: Dictionary) -> void:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		_reply_error("delete_node requires string fields: node_path, project_path")
		return

	var guarded := _guard_request(project_path_value, node_path_value)
	if guarded.is_empty():
		return
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]
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

	var guarded := _guard_request(project_path_value)
	if guarded.is_empty():
		return
	var scene_root: Node = guarded["scene_root"]

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
