@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


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
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("query_nodes requires a string field: project_path")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]

	var class_filter := _optional_string(request, "class")
	if class_filter != "":
		if not ClassDB.class_exists(class_filter):
			return CommandSupport.error("unknown class: %s" % class_filter)
		for global_class in ProjectSettings.get_global_class_list():
			if global_class.get("class") == class_filter:
				return CommandSupport.error("class must be a built-in class, not a project script class: %s" % class_filter)

	var limit := CommandSupport.validate_limit(request.get("limit", 100))
	if limit.has("error"):
		return CommandSupport.error(limit["error"])

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
	return CommandSupport.ok({"nodes": matches, "truncated": truncated})


## Reads an optional string filter from `request`; a missing or non-string
## value counts as no filter (an empty string).
static func _optional_string(request: Dictionary, key: String) -> String:
	var value: Variant = request.get(key)
	if typeof(value) != TYPE_STRING:
		return ""
	return value


## Appends every node at or below `node` that matches all the filters, in
## tree order, stopping once more than `limit` matches are collected. Returns
## true when the caller should stop searching (the cap is reached), so a large
## scene is not walked past the point where the result is already decided.
static func _collect_query_nodes(node: Node, scene_root: Node, class_filter: String, group_filter: String, name_filter: String, limit: int, matches: Array) -> bool:
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
static func _node_matches(node: Node, class_filter: String, group_filter: String, name_filter: String) -> bool:
	if class_filter != "" and not node.is_class(class_filter):
		return false
	if group_filter != "" and not node.is_in_group(group_filter):
		return false
	if name_filter != "" and not str(node.name).match(name_filter):
		return false
	return true