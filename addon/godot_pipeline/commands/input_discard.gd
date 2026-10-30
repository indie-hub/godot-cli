@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const InputEvents := preload("input_events.gd")

## The editor-interface metadata key that holds the staged input actions.
## `input-stage` writes it, `input-apply` and this command read it, and the
## store dies with the editor process, so a staged edit never survives a
## restart. It is a Dictionary of action name -> {"deadzone": float,
## "events": Array of InputEvent, "remove": bool}.
const STAGING_META := "godot_pipeline_input_staging"


## Removes staged input actions without writing any file and without touching
## live `ProjectSettings` or the editor's InputMap.
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to change)
## and may carry string field `action`. Without `action` every staged entry is
## discarded and the store is removed; with it, only that entry. The
## project_path string check runs first, then the project-path mismatch, then
## the unknown-field check and the action checks; the store is untouched until
## every check has passed. An action name the store does not hold is refused
## with the store unchanged. Discarding when nothing is staged is not
## an error: the reply is an empty `discarded` list.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("input_discard requires a string field: project_path")
	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(InputEvents.escape_text(project_mismatch))
	for key in request.keys():
		if not str(key) in ["command", "project_path", "action"]:
			return CommandSupport.error("unknown field: %s" % InputEvents.escape_text(str(key)))

	var action_value: Variant = request.get("action", null)
	if action_value != null and typeof(action_value) != TYPE_STRING:
		return CommandSupport.error("input_discard requires a string field: action")
	if action_value != null and (action_value as String).find(String.chr(0xFFFD)) >= 0:
		return CommandSupport.error(InputEvents.action_error("input_discard", action_value))

	var editor := plugin.get_editor_interface()
	if not editor.has_meta(STAGING_META):
		if action_value != null:
			return CommandSupport.error("no staged entry for action: %s" % InputEvents.escape_text(str(action_value)))
		return CommandSupport.ok({"discarded": [], "staged_count": 0})
	var stored: Variant = editor.get_meta(STAGING_META)
	if typeof(stored) != TYPE_DICTIONARY:
		return CommandSupport.error("the staging store is not a dictionary; nothing was discarded")
	var store: Dictionary = stored

	if action_value == null:
		var names: Array = store.keys()
		editor.remove_meta(STAGING_META)
		return CommandSupport.ok({"discarded": names, "staged_count": 0})

	var action: String = action_value
	if not store.has(action):
		return CommandSupport.error("no staged entry for action: %s" % InputEvents.escape_text(action))
	# The metadata holds this Dictionary itself, so the erase is the update.
	store.erase(action)
	return CommandSupport.ok({"discarded": [action], "staged_count": store.size()})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "input_discard",
		"description": "Discard staged input actions without writing.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "action", "flag": "--action", "positional": null, "type": "string", "required": false, "default": null, "help": "One staged action to discard; omit it to discard every staged action."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}
