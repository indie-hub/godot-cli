@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Stops the game the editor is playing, if any. Stopping with nothing playing
## is a no-op that still replies ok, so a client can call it unconditionally.
##
## `request` must carry a string field `project_path`. The project-path check
## runs before anything else, so a mismatched caller can never stop the game.
## The reply reports the state measured immediately before and after the call.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("stop requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var editor := plugin.get_editor_interface()
	var was_playing: bool = editor.is_playing_scene()
	if was_playing:
		editor.stop_playing_scene()

	return CommandSupport.ok({
		"was_playing": was_playing,
		"playing": editor.is_playing_scene(),
		"scene": editor.get_playing_scene(),
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "stop",
		"description": "Stop the running game; a no-op when nothing plays.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}
