@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const SceneWrite := preload("scene_write.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "save_open_scene",
		"description": "Save a named open scene to its own path without closing tabs.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "path", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "A res:// path of an open scene to save."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Saves the open scene named by PATH to its own path, only if it has unsaved
## changes. The tab switch is visible: the edited tab changes during the call
## and changes back. The edited tab must have a file path: with an untitled
## edited scene the named scene cannot be restored, so the call is refused
## before any tab change. Never stops a running game and never opens a dialog.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var path_value: Variant = request.get("path")
	if typeof(project_path_value) != TYPE_STRING or typeof(path_value) != TYPE_STRING:
		return CommandSupport.error("save_open_scene requires string fields: path, project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)
	for key in request.keys():
		if not str(key) in ["command", "project_path", "path"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var path: String = path_value
	if path.is_empty():
		return CommandSupport.error("untitled scenes cannot be saved by path; use save-scene-as instead")
	if not path.begins_with("res://"):
		return CommandSupport.error("path must start with res://: %s" % SceneWrite.escape_text(path))

	var editor := plugin.get_editor_interface()
	var edited_root := editor.get_edited_scene_root()
	var edited_path := ""
	if edited_root != null:
		edited_path = edited_root.scene_file_path
	if edited_root != null and edited_path.is_empty():
		return CommandSupport.error("edited scene is untitled; save it with save-scene-as first")

	var open_paths: Array = []
	for root in editor.get_open_scene_roots():
		if root != null:
			open_paths.append(root.scene_file_path)
	if not open_paths.has(path):
		return CommandSupport.error("scene is not open: %s" % SceneWrite.escape_text(path))

	var unsaved: Array = editor.get_unsaved_scenes()
	if not unsaved.has(path):
		var current_edited: Variant = null
		if edited_root != null:
			current_edited = edited_root.scene_file_path
		return CommandSupport.ok({
			"path": path,
			"saved": false,
			"edited": current_edited,
			"undoable": false,
			"undo_scope": "none: the disk write has no undo in the editor",
		})

	if edited_root != null and edited_path == path:
		var save_error := editor.save_scene()
		if save_error != OK:
			return CommandSupport.error("failed to save the scene: %s" % error_string(save_error))
		var after := editor.get_edited_scene_root()
		var after_path: Variant = null
		if after != null:
			after_path = after.scene_file_path
		return CommandSupport.ok({
			"path": path,
			"saved": true,
			"edited": after_path,
			"undoable": false,
			"undo_scope": "none: the disk write has no undo in the editor",
		})

	var remembered_path := edited_path
	var remembered_was_null := edited_root == null
	editor.open_scene_from_path(path)
	var switched := editor.get_edited_scene_root()
	if switched == null or switched.scene_file_path != path:
		if not remembered_was_null:
			editor.open_scene_from_path(remembered_path)
		return CommandSupport.error("scene did not open: %s" % SceneWrite.escape_text(path))
	var save_background_error := editor.save_scene()
	if save_background_error != OK:
		if not remembered_was_null:
			editor.open_scene_from_path(remembered_path)
		return CommandSupport.error("failed to save the scene: %s" % error_string(save_background_error))
	if not remembered_was_null:
		editor.open_scene_from_path(remembered_path)
		var restored := editor.get_edited_scene_root()
		var restored_path := ""
		if restored != null:
			restored_path = restored.scene_file_path
		if restored == null or restored_path != remembered_path:
			return CommandSupport.error("failed to restore the edited scene: %s" % SceneWrite.escape_text(remembered_path))
	var final_root := editor.get_edited_scene_root()
	var final_path: Variant = null
	if final_root != null:
		final_path = final_root.scene_file_path
	return CommandSupport.ok({
		"path": path,
		"saved": true,
		"edited": final_path,
		"undoable": false,
		"undo_scope": "none: the disk write has no undo in the editor",
	})
