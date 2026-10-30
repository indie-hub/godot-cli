@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const InputEvents := preload("input_events.gd")


## Reads staged and applied input actions without writing anything.
## `request` must carry string field `project_path` and an optional string
## field `action` filtering both maps to one name. Needs no edited scene and
## creates no undo step. The field checks run first, then the project-path
## mismatch, then the unknown-field check and the action-name check; the store
## and the settings are only read after every check has passed. Applied state
## is the `[input]` section of `project.godot` on disk: only actions the file
## names are reported, with their live values read from ProjectSettings; never
## from InputMap. A live-only setting the file does not name is not listed. A
## project.godot the loader refuses is an error naming the file and the engine
## reason; a file that loads without an `[input]` section reads an empty map.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("input_read requires a string field: project_path")
	var filter_value: Variant = request.get("action", null)
	if filter_value != null and typeof(filter_value) != TYPE_STRING:
		return CommandSupport.error("input_read requires a string field: action")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(InputEvents.escape_text(project_mismatch))
	for key in request.keys():
		if not str(key) in ["command", "project_path", "action"]:
			return CommandSupport.error("unknown field: %s" % InputEvents.escape_text(str(key)))
	if filter_value != null and not InputEvents.is_valid_action_name(filter_value):
		return CommandSupport.error(InputEvents.action_error("input_read", filter_value))

	var staged_out := {}
	var store := InputEvents.read_store(plugin)
	for action_name in store.keys():
		if filter_value != null and str(action_name) != str(filter_value):
			continue
		var entry: Variant = store[action_name]
		if typeof(entry) != TYPE_DICTIONARY:
			continue
		var stored_events: Array = []
		if (entry as Dictionary).has("events") and typeof((entry as Dictionary)["events"]) == TYPE_ARRAY:
			stored_events = (entry as Dictionary)["events"]
		var deadzone := InputEvents.DEFAULT_DEADZONE
		if (entry as Dictionary).has("deadzone"):
			var raw_dead: Variant = (entry as Dictionary)["deadzone"]
			if typeof(raw_dead) == TYPE_INT or typeof(raw_dead) == TYPE_FLOAT:
				deadzone = float(raw_dead)
		var is_remove := bool((entry as Dictionary).get("remove", false))
		if is_remove:
			staged_out[str(action_name)] = InputEvents.encode_staged(deadzone, [], true)
		else:
			staged_out[str(action_name)] = InputEvents.encode_staged(deadzone, stored_events, false)

	var applied_out := {}
	var unreadable_out := {}
	# Only actions the project file names are the project's custom actions.
	# Engine defaults the file never names stay out, so an unchanged built-in
	# action is never listed; a live-only setting the file does not name is
	# not listed either.
	var names: PackedStringArray = []
	var project_file := ConfigFile.new()
	var load_error := project_file.load("res://project.godot")
	if load_error != OK:
		return CommandSupport.error("input_read cannot read res://project.godot: %s" % error_string(load_error))
	if project_file.has_section("input"):
		names = project_file.get_section_keys("input")
	for short_name in names:
		if filter_value != null and short_name != str(filter_value):
			continue
		var value: Variant = ProjectSettings.get_setting("input/" + short_name)
		if typeof(value) != TYPE_DICTIONARY or not (value as Dictionary).has("events") or typeof((value as Dictionary)["events"]) != TYPE_ARRAY:
			unreadable_out[short_name] = "setting is not a Dictionary with an events array"
			continue
		var stored_dead := InputEvents.DEFAULT_DEADZONE
		var raw_deadzone: Variant = (value as Dictionary).get("deadzone", InputEvents.DEFAULT_DEADZONE)
		if typeof(raw_deadzone) == TYPE_INT or typeof(raw_deadzone) == TYPE_FLOAT:
			stored_dead = float(raw_deadzone)
		var encoded: Array = []
		for stored_event in (value as Dictionary)["events"]:
			encoded.append(InputEvents.encode_event(stored_event))
		applied_out[short_name] = {"deadzone": stored_dead, "events": encoded}

	return CommandSupport.ok({"staged": staged_out, "applied": applied_out, "applied_unreadable": unreadable_out})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "input_read",
		"description": "Show staged and applied input actions without writing any file.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "action", "flag": "--action", "positional": null, "type": "string", "required": false, "default": null, "help": "Filter staged and applied maps to one action name."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}
