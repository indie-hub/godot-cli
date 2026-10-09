@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Lists the resource files the editor's file system view holds, as a bounded
## page, without changing the scene and without loading or reading any
## resource. Read-only: no undo/redo action, no dirty flag, no file write, and
## no edited scene is required.
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to read).
## Optional `path_prefix` keeps entries whose full path starts with it,
## optional `type` keeps entries whose engine class equals it or inherits it,
## optional `cursor` is the path of the last entry of the previous page, and
## `limit` is a JSON number defaulting to 100 that must be an integer in
## 1..1000. An optional boolean `refresh` asks the plugin to start a
## file-system scan and reply in the scanning state. The project-path check
## runs before anything else, then every other field is validated, then the
## editor file system is read.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("list_resources requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var path_prefix_value: Variant = request.get("path_prefix")
	if path_prefix_value != null and typeof(path_prefix_value) != TYPE_STRING:
		return CommandSupport.error("path_prefix must be a string or null")
	var path_prefix: String = "" if path_prefix_value == null else path_prefix_value
	if path_prefix != "" and not path_prefix.begins_with("res://"):
		return CommandSupport.error("path_prefix must start with res://: %s" % path_prefix)

	var type_value: Variant = request.get("type")
	if type_value != null and typeof(type_value) != TYPE_STRING:
		return CommandSupport.error("type must be a string or null")
	var type_filter: String = "" if type_value == null else type_value
	if type_filter != "":
		if not ClassDB.class_exists(type_filter):
			return CommandSupport.error("unknown class: %s" % type_filter)
		for global_class in ProjectSettings.get_global_class_list():
			if global_class.get("class") == type_filter:
				return CommandSupport.error("class must be a built-in class, not a project script class: %s" % type_filter)

	var limit := CommandSupport.validate_limit(request.get("limit", 100))
	if limit.has("error"):
		return CommandSupport.error(limit["error"])

	var cursor_value: Variant = request.get("cursor")
	if cursor_value != null and typeof(cursor_value) != TYPE_STRING:
		return CommandSupport.error("cursor must be a string or null")
	var cursor: String = "" if cursor_value == null else cursor_value
	if cursor != "" and not cursor.begins_with("res://"):
		return CommandSupport.error("cursor must start with res://: %s" % cursor)

	var refresh_value: Variant = request.get("refresh")
	if refresh_value != null and typeof(refresh_value) != TYPE_BOOL:
		return CommandSupport.error("refresh must be a boolean")
	var refresh: bool = false if refresh_value == null else refresh_value

	var efs := plugin.get_editor_interface().get_resource_filesystem()
	if efs.get_filesystem() == null:
		return CommandSupport.error("the editor file system is not ready")

	if refresh and not efs.is_scanning():
		efs.scan()
	if refresh or efs.is_scanning():
		# The scan runs asynchronously; the caller polls until a later reply
		# reports `scanning` false, so a listing taken mid-scan is never a
		# partial page.
		return CommandSupport.ok({
			"scanning": true,
			"resources": [],
			"truncated": false,
			"next_cursor": null,
		})

	var page_limit: int = int(limit["value"])
	var state := {
		"path_prefix": path_prefix,
		"type_filter": type_filter,
		"cursor": cursor,
		"cursor_found": cursor == "",
		"limit": page_limit,
		"resources": [],
	}
	_walk(efs.get_filesystem(), state)
	if not state["cursor_found"]:
		return CommandSupport.error("cursor not found: %s" % cursor)
	var resources: Array = state["resources"]
	var truncated: bool = resources.size() > page_limit
	var next_cursor: Variant = null
	if truncated:
		resources.resize(page_limit)
		next_cursor = resources[resources.size() - 1]["path"]
	return CommandSupport.ok({
		"scanning": false,
		"resources": resources,
		"truncated": truncated,
		"next_cursor": next_cursor,
	})


## Appends matching entries to `state.resources`, in walk order: a directory's
## files first (in the editor's own index order), then its subdirectories in
## index order. Entries at or before the cursor are skipped, so a page starts
## after the previous page's last entry. The walk stops once one entry more
## than the limit is collected, so a large project is not walked past the
## point where `truncated` is already decided.
static func _walk(directory: EditorFileSystemDirectory, state: Dictionary) -> void:
	for index in directory.get_file_count():
		var file_type := directory.get_file_type(index)
		if file_type == "TextFile":
			continue
		var path := directory.get_file_path(index)
		if not state["cursor_found"]:
			if path == state["cursor"]:
				state["cursor_found"] = true
			continue
		if state["resources"].size() > int(state["limit"]):
			return
		if not _matches(path, file_type, state["path_prefix"], state["type_filter"]):
			continue
		var entry: Dictionary = {"path": path, "type": file_type}
		if file_type == "GDScript":
			entry["script_class"] = directory.get_file_script_class_name(index)
			entry["extends"] = directory.get_file_script_class_extends(index)
		state["resources"].append(entry)
		if state["resources"].size() > int(state["limit"]):
			return
	for index in directory.get_subdir_count():
		_walk(directory.get_subdir(index), state)
		if state["resources"].size() > int(state["limit"]):
			return


## Whether an entry passes both filters: the path prefix (a plain
## `begins_with`) and the engine class (an exact match, or a subclass through
## `ClassDB.is_parent_class`). An empty filter matches every entry.
static func _matches(path: String, file_type: String, path_prefix: String, type_filter: String) -> bool:
	if path_prefix != "" and not path.begins_with(path_prefix):
		return false
	if type_filter != "" and file_type != type_filter and not ClassDB.is_parent_class(file_type, type_filter):
		return false
	return true
