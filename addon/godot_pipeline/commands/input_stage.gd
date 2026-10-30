@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const InputEvents := preload("input_events.gd")


## Stages one input action's whole entry, replacing any earlier staged entry
## for the same name. W3 staging only: writes no file and touches neither
## live ProjectSettings nor the editor's InputMap, and needs no edited scene.
## `request` must carry string fields `project_path` and `action`, an optional
## JSON array `events`, an optional JSON number `deadzone` and an optional
## boolean `remove`. `--remove` stages a removal and is refused together with
## `--events` or `--deadzone`; without `--remove`, `--events` is required and
## must hold 1 to 32 wire events. The field presence, type and combination
## checks run first, then the project-path mismatch, then the unknown-field
## check, the action-name check and the deadzone and event validation; nothing
## is mutated until every check has passed.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var action_value: Variant = request.get("action")
	if typeof(project_path_value) != TYPE_STRING or typeof(action_value) != TYPE_STRING:
		return CommandSupport.error("input_stage requires string fields: project_path, action")
	var remove_value: Variant = request.get("remove", false)
	if typeof(remove_value) != TYPE_BOOL:
		return CommandSupport.error("input_stage requires a boolean field: remove")
	var remove: bool = remove_value
	var events_value: Variant = request.get("events", null)
	var deadzone_value: Variant = request.get("deadzone", null)
	if remove and (events_value != null or deadzone_value != null):
		return CommandSupport.error("input_stage cannot combine --remove with --events or --deadzone")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(InputEvents.escape_text(project_mismatch))
	for key in request.keys():
		if not str(key) in ["command", "project_path", "action", "events", "deadzone", "remove"]:
			return CommandSupport.error("unknown field: %s" % InputEvents.escape_text(str(key)))

	var action_name: String = action_value
	if not InputEvents.is_valid_action_name(action_name):
		return CommandSupport.error(InputEvents.action_error("input_stage", action_value))

	if remove:
		var store := InputEvents.read_store(plugin)
		store[action_name] = {"deadzone": InputEvents.DEFAULT_DEADZONE, "events": [], "remove": true}
		InputEvents.write_store(plugin, store)
		var staged := InputEvents.encode_staged(InputEvents.DEFAULT_DEADZONE, [], true)
		return CommandSupport.ok({"action": action_name, "staged": staged, "staged_count": store.size()})

	if events_value == null:
		return CommandSupport.error("input_stage refused events: events must be a non-empty array of at most 32 event objects")
	var deadzone_result := InputEvents.validate_deadzone(deadzone_value, "input_stage")
	if deadzone_result.has("error"):
		return CommandSupport.error(deadzone_result["error"])
	var decoded := InputEvents.decode_events(events_value, "input_stage")
	if decoded.has("error"):
		return CommandSupport.error(decoded["error"])
	var deadzone: float = deadzone_result["value"]
	var objects: Array = decoded["value"]
	var current := InputEvents.read_store(plugin)
	current[action_name] = {"deadzone": deadzone, "events": objects, "remove": false}
	InputEvents.write_store(plugin, current)
	var reply_staged := InputEvents.encode_staged(deadzone, objects, false)
	return CommandSupport.ok({"action": action_name, "staged": reply_staged, "staged_count": current.size()})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "input_stage",
		"description": "Stage one input action's deadzone and events without writing any file.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "action", "flag": "--action", "positional": null, "type": "string", "required": true, "default": null, "help": "Action name without the input/ prefix."},
		{"name": "events", "flag": "--events", "positional": null, "type": "json", "required": false, "default": null, "help": "JSON array of 1 to 32 wire event objects; refused with --remove."},
		{"name": "deadzone", "flag": "--deadzone", "positional": null, "type": "json", "required": false, "default": null, "help": "Deadzone number from 0.0 to 1.0; default 0.2; refused with --remove."},
		{"name": "remove", "flag": "--remove", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Stage a removal entry instead of events."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}
