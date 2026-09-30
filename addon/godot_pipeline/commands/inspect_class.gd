@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Reports ClassDB reflection for an engine class: its ancestors, whether it
## can be instantiated, whether it is a Node subclass, and its declared
## properties, methods and signals. Read-only: no undo/redo action, no dirty
## flag, no file write, and no edited scene is required.
##
## `request` must carry string fields `class` (an engine class name; project
## script classes are not in ClassDB and are rejected) and `project_path` (the
## canonical, symlink-resolved absolute path of the project the caller intends
## to read). The project-path check runs before anything else is resolved,
## then the class existence check.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var class_value: Variant = request.get("class")
	var project_path_value: Variant = request.get("project_path")
	if typeof(class_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("inspect_class requires string fields: class, project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var requested_class: String = class_value
	if not ClassDB.class_exists(requested_class):
		return CommandSupport.error("unknown class: %s" % requested_class)

	var properties: Array = []
	for info in ClassDB.class_get_property_list(requested_class, true):
		# The same editor-visible filter inspect-node uses; it also drops
		# category and group header entries, which carry no EDITOR/STORAGE bit.
		var usage: int = info["usage"]
		if usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE) == 0:
			continue
		properties.append({
			"name": info["name"],
			"type": _type_name(info),
			"read_only": usage & PROPERTY_USAGE_READ_ONLY != 0,
		})

	var methods: Array = []
	for info in ClassDB.class_get_method_list(requested_class, true):
		var args: Array = []
		for arg in info["args"]:
			args.append({"name": arg["name"], "type": _type_name(arg)})
		methods.append({
			"name": info["name"],
			"args": args,
			"return": _type_name(info["return"]),
			"flags": info["flags"],
		})

	var signals: Array = []
	for info in ClassDB.class_get_signal_list(requested_class, true):
		var args: Array = []
		for arg in info["args"]:
			args.append({"name": arg["name"], "type": _type_name(arg)})
		signals.append({"name": info["name"], "args": args})

	var ancestors: Array = []
	var parent := ClassDB.get_parent_class(requested_class)
	while parent != "":
		ancestors.append(parent)
		parent = ClassDB.get_parent_class(parent)

	return CommandSupport.ok({
		"class": requested_class,
		"ancestors": ancestors,
		"can_instantiate": ClassDB.can_instantiate(requested_class),
		"is_node": ClassDB.is_parent_class(requested_class, "Node"),
		"properties": properties,
		"methods": methods,
		"signals": signals,
	})


## Renders an entry's Variant type as a stable type name: `type_string` for
## ordinary types, the class name for an Object-typed entry that names one
## (e.g. "InputEvent"), "Variant" for a NIL entry declared as an untyped
## Variant, and "void" for a NIL entry with no value (a method return).
static func _type_name(info: Dictionary) -> String:
	var type: int = int(info["type"])
	if type == TYPE_OBJECT and info.get("class_name", "") != "":
		return str(info["class_name"])
	if type == TYPE_NIL:
		if int(info.get("usage", 0)) & PROPERTY_USAGE_NIL_IS_VARIANT != 0:
			return "Variant"
		return "void"
	return type_string(type)