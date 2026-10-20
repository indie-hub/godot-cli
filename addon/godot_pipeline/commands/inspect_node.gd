@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const ValueCodec := preload("../value_codec.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "inspect_node",
		"description": "Show a node's class, child count, and property values.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "node_path", "flag": "--node-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Scene-relative path of the node; '.' targets the scene root."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


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
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("inspect_node requires string fields: node_path, project_path")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
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
		var property_value: Variant = target.get(info["name"])
		# A Resource-valued Object property with the resource hint is encoded
		# exactly as `inspect_resource` encodes the same reference (external
		# `{"path","type"}`, an embedded sub-resource expanded inline), through
		# the shared encoder. A null value and every other Object property
		# keep the old `value_to_json` path so their `value: null, supported:
		# false` reply stays byte-identical with the existing baseline. The
		# `script` property is the one Object property baseline encodes from a
		# non-null Resource, and the design's intent is to show resources the
		# caller assigns at runtime, not the node's own script, so it stays on
		# the old path.
		var json_value: Variant = property_value
		if typeof(property_value) == TYPE_OBJECT and info["hint"] == PROPERTY_HINT_RESOURCE_TYPE and info["name"] != "script":
			json_value = CommandSupport.encode_value(property_value, 0, {})
		else:
			json_value = ValueCodec.value_to_json(property_value)
		if json_value == null:
			entry["value"] = null
			entry["supported"] = false
		else:
			entry["value"] = json_value
		properties.append(entry)

	return CommandSupport.ok({
		"path": node_path,
		"name": str(target.name),
		"type": target.get_class(),
		"child_count": target.get_child_count(),
		"properties": properties,
	})