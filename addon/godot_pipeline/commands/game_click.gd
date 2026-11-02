@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")

## Clicks the left mouse button in the running game through the game helper.
## The helper executes `Input.parse_input_event` with an `InputEventMouseButton`
## press and, one frame later, the release at the same viewport position (the
## space `capture_game` reports): the pair is what a Button answers, firing on
## release. The command holds the connection with a `timeout_ms` + reply wait
## exactly like `capture_game`. Read-only except for the injected events
## themselves: no undo/redo action, no dirty flag, no file write, and no hover
## is sent because none is needed. Only the left button is built; middle,
## right and other buttons are refused by the shape of the command (there is
## no button argument to name them with).
##
## `request` must carry string field `project_path`, checked first, and
## integer fields `x` and `y` (positional 0 and 1: viewport pixels, 0 or
## more). Optional `timeout_ms` (default 5000, 100 to 30000) bounds the wait.
## When no game is attached the command refuses at once and sends nothing. A
## helper answer with ok false becomes an error carrying the helper's text; a
## wait that passes without an answer becomes a timeout error naming the
## measured causes.
const DEFAULT_TIMEOUT_MS := 5000
const MIN_TIMEOUT_MS := 100
const MAX_TIMEOUT_MS := 30000


## Clicks at (`x`, `y`) in the running game, holding the connection until
## the helper answers or the wait passes.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("game_click requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	for key in request.keys():
		if not str(key) in ["command", "project_path", "x", "y", "timeout_ms"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var x := _validate_coordinate(request.get("x"), "x")
	if x.has("error"):
		return CommandSupport.error(x["error"])
	var y := _validate_coordinate(request.get("y"), "y")
	if y.has("error"):
		return CommandSupport.error(y["error"])

	var wait := _validate_timeout_ms(request.get("timeout_ms", DEFAULT_TIMEOUT_MS))
	if wait.has("error"):
		return CommandSupport.error(wait["error"])

	var debug = plugin._debug_plugin
	var id: String = debug.request_click(int(x["value"]), int(y["value"]))
	if id.is_empty():
		return CommandSupport.error("no game is attached: start the game before clicking it")
	var timeout_ms: int = wait["value"]
	var poll := func() -> Dictionary:
		return _poll_click(debug, id)
	var cancel := func() -> void:
		debug.retire_click(id)
	return {
		"_pending": {
			"poll": poll,
			"deadline_msec": timeout_ms,
			"timeout_message": "no click reply arrived within %d ms: the game is likely stopped in the debugger, the helper is not loaded, or the game is busy" % timeout_ms,
			"on_cancel": cancel,
		}
	}


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help. `reply_wait_argument`
## tells the CLI to wait for `timeout_ms` plus two seconds.
static func interface() -> Dictionary:
	return {
		"name": "game_click",
		"description": "Click the left mouse button at viewport coordinates in the running game through the game helper.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"reply_wait_argument": "timeout_ms",
		"arguments": [
		{"name": "x", "flag": null, "positional": 0, "type": "integer", "required": true, "default": null, "help": "Viewport x in pixels, the same space capture_game reports; the plugin checks 0 or more."},
		{"name": "y", "flag": null, "positional": 1, "type": "integer", "required": true, "default": null, "help": "Viewport y in pixels, the same space capture_game reports; the plugin checks 0 or more."},
		{"name": "timeout_ms", "flag": "--timeout-ms", "positional": null, "type": "integer", "required": false, "default": 5000, "help": "Longest wait for the helper's answer in milliseconds; the plugin checks the 100 to 30000 range."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Reads the helper's stored reply for `id`: an empty Dictionary while it has
## not arrived (the connection stays held), the click result once it has, or
## the helper's own error text when the helper reports failure.
static func _poll_click(debug, id: String) -> Dictionary:
	var stored: Dictionary = debug.take_click_reply(id)
	if stored.is_empty():
		return {}
	debug.retire_click(id)
	if not bool(stored["ok"]):
		return CommandSupport.error(str(stored["error"]))
	return CommandSupport.ok({
		"x": int(stored["x"]),
		"y": int(stored["y"]),
		"button": str(stored["button"]),
	})


## Validates one viewport coordinate as a non-negative integer, accepting an
## integral JSON number. There is no upper bound: the game window's size is
## the game's business, and a click outside any control only reaches `_input`.
static func _validate_coordinate(value: Variant, name: String) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 0:
			return {"error": "%s must not be negative" % name}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "%s must be an integer" % name}
		if number < 0.0:
			return {"error": "%s must not be negative" % name}
		return {"value": int(number)}
	return {"error": "%s must be an integer" % name}


## Validates `timeout_ms` as an integer in 100..30000, accepting an integral
## JSON number.
static func _validate_timeout_ms(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < MIN_TIMEOUT_MS or int(value) > MAX_TIMEOUT_MS:
			return {"error": "timeout_ms must be between 100 and 30000"}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "timeout_ms must be an integer"}
		if number < 100.0 or number > 30000.0:
			return {"error": "timeout_ms must be between 100 and 30000"}
		return {"value": int(number)}
	return {"error": "timeout_ms must be an integer"}
