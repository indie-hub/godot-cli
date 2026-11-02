@tool
extends RefCounted

## Shared target check for the scene-file writers: create-scene and
## save-scene-as use exactly this helper so both refuse the same paths in the
## same order with one distinct text each. A validation failure mutates
## nothing; the caller writes only after this returns a path.


## Prints a caller-supplied path escaped and printable for refusal messages:
## control characters below 0x20 and DEL become \uXXXX so the reply stays
## valid JSON; every other character passes through.
static func escape_text(text: String) -> String:
	var out := ""
	for index in text.length():
		var code := text.unicode_at(index)
		if code < 32 or code == 127:
			out += "\\u%04x" % code
		else:
			out += text.substr(index, 1)
	return out


## Returns true when the path holds a control character.
static func _has_control(text: String) -> bool:
	for index in text.length():
		var code := text.unicode_at(index)
		if code < 32 or code == 127:
			return true
	return false


## Validates a new or overwrite scene target in the fixed order. `edited_path`
## is the edited scene's path or "" when none; `allow_overwrite` is true only
## for save-scene-as with --overwrite. Returns {"path": String, "exists": bool}
## on success or {"error": String} on the first failure.
## Paths compare by normalized identity ignoring case: `res://./x.tscn` and
## `res://a//x.tscn` collapse to the stored tab path, and `res://A.tscn`
## matches `res://a.tscn`, so an alias of the edited or an open scene is
## refused with the same text. Any symlink component under res:// refuses.
static func validate_target(plugin: EditorPlugin, path_value: Variant, allow_overwrite: bool, edited_path: String) -> Dictionary:
	if typeof(path_value) != TYPE_STRING:
		return {"error": "path must be a string"}
	var path: String = path_value
	if not path.begins_with("res://"):
		return {"error": "path must start with res://: %s" % escape_text(path)}
	if ".." in path.split("/"):
		return {"error": "path must not contain '..': %s" % escape_text(path)}
	if _has_control(path):
		return {"error": "path must not contain control characters: %s" % escape_text(path)}
	if not path.ends_with(".tscn"):
		return {"error": "path must end with .tscn: %s" % escape_text(path)}
	var normalized := ProjectSettings.localize_path(path).simplify_path()
	var parent := normalized.get_base_dir()
	if parent.is_empty():
		return {"error": "parent folder does not exist: %s" % escape_text(path)}
	if not DirAccess.dir_exists_absolute(ProjectSettings.globalize_path(parent)):
		return {"error": "parent folder does not exist: %s" % escape_text(path)}
	if DirAccess.dir_exists_absolute(ProjectSettings.globalize_path(normalized)):
		return {"error": "target is a directory: %s" % escape_text(path)}
	var probe := DirAccess.open("res://")
	if probe != null:
		var rel := normalized.trim_prefix("res://")
		var parts := rel.split("/")
		var prefix := "res://"
		for i in parts.size():
			if parts[i].is_empty():
				continue
			prefix = prefix.trim_suffix("/") + "/" + parts[i]
			var global_prefix := ProjectSettings.globalize_path(prefix)
			if probe.is_link(prefix) or probe.is_link(global_prefix):
				return {"error": "target is a symlink: %s" % escape_text(path)}
	var exists := FileAccess.file_exists(normalized)
	if not exists:
		return {"path": path, "exists": false}
	if not allow_overwrite:
		return {"error": "target already exists: %s" % escape_text(path)}
	# Overwrite path below: the file exists and --overwrite was given.
	if edited_path != "" and normalized.to_lower() == ProjectSettings.localize_path(edited_path).simplify_path().to_lower():
		return {"error": "path is the edited scene's own path; use save-scene instead"}
	var editor := plugin.get_editor_interface()
	for root in editor.get_open_scene_roots():
		if root != null and ProjectSettings.localize_path(root.scene_file_path).simplify_path().to_lower() == normalized.to_lower():
			return {"error": "target is already open: %s" % escape_text(path)}
	for open_path in editor.get_open_scenes():
		if ProjectSettings.localize_path(str(open_path)).simplify_path().to_lower() == normalized.to_lower():
			return {"error": "target is already open: %s" % escape_text(path)}
	var packed := ResourceLoader.load(normalized, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE) as PackedScene
	if packed == null:
		return {"error": "target is not a scene file: %s" % escape_text(path)}
	return {"path": path, "exists": true}
