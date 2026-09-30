@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Saves the currently edited scene to the file path it already has, so edits
## made through the other commands persist on disk. This is the one command
## that writes the scene file; it never prompts for a location (save-as and
## creating new files are out of scope).
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to save).
## The project-path check runs before anything else is resolved, so a
## mismatched caller can never cause a write. A missing scene, or an open
## scene with no file path yet (an unsaved new scene), is rejected without
## writing anything.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("save_scene requires a string field: project_path")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]

	var scene_path := scene_root.scene_file_path
	if scene_path.is_empty():
		return CommandSupport.error("the open scene has no file path yet; save it to a file before using save_scene")

	# The editor's own save path (same as Ctrl+S) writes the scene to the
	# path it already has, clears the dirty flag, and handles the .uid
	# sidecar; it reports failure through its Error return value.
	var error := plugin.get_editor_interface().save_scene()
	if error != OK:
		return CommandSupport.error("failed to save the scene: %s" % error_string(error))

	return CommandSupport.ok({"path": scene_path})