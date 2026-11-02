@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const SceneWrite := preload("scene_write.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "save_scene_as",
		"description": "Save a copy of the edited scene to a new path without changing tabs.",
		"needs_project_path": true,
		"edits_scene": true,
		"refresh_poll": false,
		"arguments": [
		{"name": "path", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "A res:// .tscn path for the copy."},
		{"name": "overwrite", "flag": "--overwrite", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Replace an existing closed scene file."},
		{"name": "dry_run", "flag": "--dry-run", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Validate the whole request and write nothing."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Writes a copy of the edited scene (including unsaved changes) to PATH with
## PackedScene.pack of the edited root and ResourceSaver.save. The edited tab
## keeps its path, name, dirty state, selection and undo history. Never calls
## EditorInterface.save_scene_as, which would rename the tab.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var path_value: Variant = request.get("path")
	var overwrite_value: Variant = request.get("overwrite", false)
	var dry_run_value: Variant = request.get("dry_run", false)
	if typeof(project_path_value) != TYPE_STRING or typeof(path_value) != TYPE_STRING:
		return CommandSupport.error("save_scene_as requires string fields: path, project_path")
	if typeof(overwrite_value) != TYPE_BOOL or typeof(dry_run_value) != TYPE_BOOL:
		return CommandSupport.error("save_scene_as requires boolean fields: overwrite, dry_run")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]
	for key in request.keys():
		if not str(key) in ["command", "project_path", "path", "overwrite", "dry_run"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var path: String = path_value
	var edited_path: String = scene_root.scene_file_path
	if edited_path != "" and path == edited_path:
		return CommandSupport.error("path is the edited scene's own path; use save-scene instead")
	var allow_overwrite: bool = overwrite_value
	var checked: Dictionary = SceneWrite.validate_target(plugin, path, allow_overwrite, edited_path)
	if checked.has("error"):
		return CommandSupport.error(checked["error"])
	var exists: bool = bool(checked.get("exists", false))

	var dry_run: bool = dry_run_value
	if dry_run:
		return CommandSupport.ok({
			"written": [],
			"replaced": exists,
			"dry_run": true,
			"undoable": false,
			"undo_scope": "none: the disk write has no undo in the editor",
			"path": path,
			"source": edited_path,
			"overwrote_open": false,
		})

	var packed := PackedScene.new()
	var pack_error := packed.pack(scene_root)
	if pack_error != OK:
		return CommandSupport.error("failed to pack the scene: %s" % error_string(pack_error))
	var save_error := ResourceSaver.save(packed, path)
	if save_error != OK:
		return CommandSupport.error("failed to save the scene: %s" % error_string(save_error))
	plugin.get_editor_interface().get_resource_filesystem().update_file(path)
	return CommandSupport.ok({
		"written": [path],
		"replaced": exists,
		"dry_run": false,
		"undoable": false,
		"undo_scope": "none: the disk write has no undo in the editor",
		"path": path,
		"source": edited_path,
		"overwrote_open": false,
	})
