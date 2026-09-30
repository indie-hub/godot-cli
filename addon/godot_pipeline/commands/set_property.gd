@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const ValueCodec := preload("../value_codec.gd")


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
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var property_value: Variant = request.get("property")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(property_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING or not request.has("value"):
		return CommandSupport.error("set_property requires string fields: node_path, property, project_path; and a value field")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
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
			return CommandSupport.error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the change would not be saved" % node_path
			)
		owner_node = owner_node.owner

	var property: String = property_value
	var property_info: Dictionary = {}
	for candidate in target.get_property_list():
		if candidate["name"] == property:
			property_info = candidate
			break
	if property_info.is_empty():
		return CommandSupport.error("unknown property on %s (%s): %s" % [node_path, target.get_class(), property])
	# Only inspector-visible or serialized properties are settable; this
	# excludes internal ones like `name` (use rename_node) and derived ones
	# like `global_position` that are not part of the scene's saved state.
	var usage: int = property_info["usage"]
	if usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE) == 0:
		return CommandSupport.error("property is not editable in the inspector or saved with the scene: %s" % property)
	if usage & PROPERTY_USAGE_READ_ONLY:
		return CommandSupport.error("property is read-only: %s" % property)

	var property_type: int = property_info["type"]
	var old_value: Variant = target.get(property)

	# A plain `Array` has no declared element type, so a JSON value could not
	# be coerced without guessing (Vector2 versus a 2-number array, int versus
	# float); only typed `Array[T]` and the Packed*Array types are accepted.
	var array_element_type: int = TYPE_NIL
	if property_type == TYPE_ARRAY:
		if typeof(old_value) != TYPE_ARRAY or not old_value.is_typed():
			return CommandSupport.error(
				"untyped Array property %s is not supported by set-property (an Array has no declared element type to coerce to)" % property
			)
		array_element_type = old_value.get_typed_builtin()
		if array_element_type == TYPE_OBJECT:
			return CommandSupport.error(
				"typed array of unsupported element type %s on %s (Object/Node/Resource element types are not supported)" % [old_value.get_typed_class_name(), property]
			)
		if not ValueCodec.is_supported_array_element(array_element_type):
			return CommandSupport.error(
				"typed array of unsupported element type %s on %s (%s is not a supported set-property element type)" % [type_string(array_element_type), property, type_string(array_element_type)]
			)

	var coerced := ValueCodec.coerce_value(request["value"], property_type, array_element_type, old_value)
	if coerced.has("error"):
		return CommandSupport.error("cannot set %s (%s): %s" % [property, type_string(property_type), coerced["error"]])
	var new_value: Variant = coerced["value"]

	# An enum-hinted int property only accepts the values declared in its
	# hint_string (see `ValueCodec.enum_value_list`); anything else is rejected
	# before any undo action is created.
	if property_type == TYPE_INT and property_info["hint"] == PROPERTY_HINT_ENUM:
		if not (int(new_value) in ValueCodec.enum_value_list(property_info["hint_string"])):
			return CommandSupport.error(
				"value %d is not one of the declared enum values for %s (%s)" % [new_value, property, property_info["hint_string"]]
			)

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Set %s" % property)
	undo_redo.add_do_property(target, property, new_value)
	undo_redo.add_undo_property(target, property, old_value)
	undo_redo.commit_action()

	# Setters may clamp or normalize (e.g. a ranged float), so read the value
	# back rather than echo the request. var_to_str keeps Vector2/Color/etc.
	# unambiguous, which plain JSON.stringify would not.
	return CommandSupport.ok({
		"node_path": node_path,
		"property": property,
		"type": type_string(property_type),
		"old_value": var_to_str(old_value),
		"value": var_to_str(target.get(property)),
	})