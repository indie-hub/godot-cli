@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const ValueCodec := preload("../value_codec.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "set_property",
		"description": "Set one property on a node.",
		"needs_project_path": true,
		"edits_scene": true,
		"refresh_poll": false,
		"arguments": [
		{"name": "node_path", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "Scene-relative path of the node."},
		{"name": "property", "flag": null, "positional": 1, "type": "string", "required": true, "default": null, "help": "Property name."},
		{"name": "value", "flag": null, "positional": 2, "type": "json", "required": true, "default": null, "help": "Parsed as JSON when it parses, otherwise sent as a plain string; the plugin coerces it to the declared type."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


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
##
## An Object property that declares a resource type takes two extra value
## forms. A plain `res://` path string assigns the loaded resource. An embedded
## `{"class": ..., "properties": {...}}` object builds a new instance, sets the
## named properties on it, and assigns it; because the instance is configured
## before it is assigned, the whole change is still one Undo/Redo step. Loading
## a resource whose script carries `@tool` runs that script's `_init` in the
## editor, as any Godot load does. An Object property without the resource hint,
## and any value that is neither a string nor an object, keep the ordinary
## coercion path and its existing refusal.
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

	# An Object property that declares a resource type accepts a resource the
	# caller names two ways: a plain `res://` path string, or an embedded object
	# `{"class": ..., "properties": {...}}` that becomes a new instance. Both are
	# one undo action. A value of any other type, and an Object property without
	# the resource hint, keep the existing coercion path below unchanged. The
	# node's own `script` property is one of those Object-with-resource-hint
	# properties, but script attachment belongs to the planned `attach-script`
	# command (parse check, base-class check, --replace rule), so it falls
	# through to today's exact Object refusal and `get_script()` is unchanged.
	if property_type == TYPE_OBJECT and property_info["hint"] == PROPERTY_HINT_RESOURCE_TYPE and property != "script":
		var value_kind: int = typeof(request["value"])
		if value_kind == TYPE_STRING or value_kind == TYPE_DICTIONARY:
			var assigned: Variant = _assign_resource(property, property_info, request["value"])
			if assigned is Dictionary:
				return assigned
			return _commit_property(plugin, target, property, property_type, old_value, assigned, node_path)

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

	return _commit_property(plugin, target, property, property_type, old_value, new_value, node_path)


## Creates the single undo action that writes `new_value` to `property` and
## answers with the same reply every accepted `set-property` produces. Every
## caller has finished validating before it gets here, so a rejected request
## never reaches an action.
static func _commit_property(plugin: EditorPlugin, target: Node, property: String, property_type: int, old_value: Variant, new_value: Variant, node_path: String) -> Dictionary:
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


## Resolves a resource-valued Object property's `value` into the Resource to
## assign. `value` is a plain `res://` path string (assign the loaded resource)
## or an embedded `{"class": ..., "properties": {...}}` object (build, configure,
## then assign). Every check runs here, before any undo action, so a rejected
## request changes nothing. Returns the Resource to assign, or an error
## Dictionary (a reply) to send back unchanged.
static func _assign_resource(property: String, property_info: Dictionary, value: Variant) -> Variant:
	if typeof(value) == TYPE_STRING:
		return _resource_from_path(property, property_info, value as String)
	return _embedded_resource(property, property_info, value as Dictionary)


## Loads the resource a caller named by `path` and checks it against the
## property's declared allow-list. The allow-list is the property's `hint_string`
## (a comma list), split and trimmed; the loaded class passes when it is one of
## the entries or inherits one. This is stricter than the engine's setter, which
## is deliberate: the inspector offers exactly these classes.
static func _resource_from_path(property: String, property_info: Dictionary, path: String) -> Variant:
	if not path.begins_with("res://"):
		return CommandSupport.error("cannot set %s (Object): resource path must start with res://: %s" % [property, path])
	if not ResourceLoader.exists(path):
		return CommandSupport.error("cannot set %s (Object): resource not found: %s" % [property, path])
	var loaded: Resource = ResourceLoader.load(path) as Resource
	if loaded == null:
		return CommandSupport.error("cannot set %s (Object): resource failed to load: %s" % [property, path])
	var allowed_error := _class_allowed(property, property_info, loaded.get_class())
	if allowed_error != "":
		return CommandSupport.error(allowed_error)
	return loaded


## Builds a new resource of the class the caller named, sets its `properties`
## with the shared value codec, and returns it for assignment. An embedded
## property may not itself be an Object: nesting resources is out of this slice,
## so an Object-valued embedded property is refused rather than silently
## mis-typed.
static func _embedded_resource(property: String, property_info: Dictionary, value: Dictionary) -> Variant:
	for key in value.keys():
		if key != "class" and key != "properties":
			return CommandSupport.error(
				"cannot set %s (Object): embedded resource accepts only the keys class and properties, not: %s" % [property, key]
			)
	if typeof(value.get("class")) != TYPE_STRING:
		return CommandSupport.error("cannot set %s (Object): embedded resource needs a string class" % property)
	var resource_class: String = value["class"]
	if typeof(value.get("properties", {})) != TYPE_DICTIONARY:
		return CommandSupport.error("cannot set %s (Object): embedded resource properties must be an object" % property)
	if not ClassDB.class_exists(resource_class):
		return CommandSupport.error("cannot set %s (Object): unknown embedded resource class: %s" % [property, resource_class])
	if not ClassDB.can_instantiate(resource_class):
		return CommandSupport.error("cannot set %s (Object): embedded resource class cannot be instantiated: %s" % [property, resource_class])
	if not (ClassDB.is_parent_class(resource_class, "Resource") or resource_class == "Resource"):
		return CommandSupport.error("cannot set %s (Object): embedded resource class is not a Resource: %s" % [property, resource_class])
	var allowed_error := _class_allowed(property, property_info, resource_class)
	if allowed_error != "":
		return CommandSupport.error(allowed_error)
	var instance: Resource = ClassDB.instantiate(resource_class) as Resource
	for embedded_property in (value.get("properties", {}) as Dictionary).keys():
		var set_error: Variant = _set_embedded_property(property, instance, embedded_property, value["properties"][embedded_property])
		if set_error != null:
			return set_error
	return instance


## Sets one embedded property on the fresh instance, or returns the error reply
## that refuses it. A property that is missing, not settable, an Object (nesting
## is out of this slice) or whose value the codec cannot coerce is refused; on
## any refusal the caller discards the whole instance, so nothing is assigned.
static func _set_embedded_property(property: String, instance: Resource, embedded_property: Variant, raw_value: Variant) -> Variant:
	var name_text := str(embedded_property)
	var info: Dictionary = {}
	for candidate in instance.get_property_list():
		if candidate["name"] == name_text:
			info = candidate
			break
	if info.is_empty():
		return CommandSupport.error(
			"cannot set %s (Object): unknown property on embedded %s: %s"
			% [property, instance.get_class(), name_text]
		)
	var usage: int = info["usage"]
	if usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE) == 0:
		return CommandSupport.error(
			"cannot set %s (Object): embedded property is not editable or saved: %s (%s)" % [property, name_text, instance.get_class()]
		)
	if usage & PROPERTY_USAGE_READ_ONLY:
		return CommandSupport.error(
			"cannot set %s (Object): embedded property is read-only: %s (%s)" % [property, name_text, instance.get_class()]
		)
	var embedded_type: int = info["type"]
	if embedded_type == TYPE_OBJECT:
		return CommandSupport.error(
			"cannot set %s (Object): embedded property %s (%s) is an Object; nested resources are not supported" % [property, name_text, instance.get_class()]
		)
	# `resource_local_to_scene` is intentionally not exposed: editing it would
	# silently change whether the embedded instance is local to the edited scene
	# or shared across instances, and the design does not own that choice. Refuse
	# it before the assignment so a rejected request changes nothing.
	if name_text == "resource_local_to_scene":
		return CommandSupport.error(
			"cannot set %s (Object): resource_local_to_scene is not exposed by set-property; it is set when the resource is created as part of an instance" % property
		)
	var old_embedded: Variant = instance.get(name_text)
	var coerced := ValueCodec.coerce_value(raw_value, embedded_type, TYPE_NIL, old_embedded)
	if coerced.has("error"):
		return CommandSupport.error(
			"cannot set %s (Object): cannot set embedded property %s (%s): %s"
			% [property, name_text, instance.get_class(), coerced["error"]]
		)
	instance.set(name_text, coerced["value"])
	return null


## Returns the exact refusal message when `candidate_class` is not one of the
## property's declared resource types and inherits none of them, or an empty
## string when it passes. `hint_string` is a comma list of accepted classes; a
## subclass counts (a `RectangleShape2D` passes a `Shape2D` property).
static func _class_allowed(property: String, property_info: Dictionary, candidate_class: String) -> String:
	for entry in (property_info["hint_string"] as String).split(",", false):
		var allowed := entry.strip_edges()
		if allowed == "":
			continue
		if candidate_class == allowed or ClassDB.is_parent_class(candidate_class, allowed):
			return ""
	return "cannot set %s (Object): resource class %s is not one of the declared types for this property (%s)" % [
		property, candidate_class, property_info["hint_string"],
	]