@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "list_open_scenes",
		"description": "List the open scene tabs in tab order with dirty state.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Read-only list of the open tabs from get_open_scene_roots in tab order.
## Never uses get_open_scenes as the source: it goes stale after a save-as.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("list_open_scenes requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)
	for key in request.keys():
		if not str(key) in ["command", "project_path"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var editor := plugin.get_editor_interface()
	var edited_root := editor.get_edited_scene_root()
	var unsaved: Array = editor.get_unsaved_scenes()
	var scenes: Array = []
	for root in editor.get_open_scene_roots():
		if root == null:
			continue
		var path: String = root.scene_file_path
		scenes.append({
			"path": path,
			"edited": root == edited_root,
			"unsaved": unsaved.has(path),
		})
	var edited: Variant = null
	if edited_root != null:
		edited = edited_root.scene_file_path
	return CommandSupport.ok({"scenes": scenes, "edited": edited})
