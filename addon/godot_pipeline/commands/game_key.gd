@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const InputEvents := preload("input_events.gd")

## Press, hold and release a physical key in the running game through the
## game helper. The helper executes `Input.parse_input_event` with an
## `InputEventKey` carrying the physical keycode on the main loop (the parsed
## path reaches `_input`, `_unhandled_input` and, one frame later, the action
## state, so both per-key game code and `Input.get_vector` polling see it).
## The command holds the connection with a `timeout_ms` + reply wait exactly
## like `capture_game`. Read-only except for the injected event itself: no
## undo/redo action, no dirty flag, no file write.
##
## `request` must carry string field `project_path`, checked first, and
## integer field `key` (positional 0: the physical keycode, e.g. 68 for D).
## The keycode reuses the input commands' key validation (`input_events.gd`),
## so the bounds never drift apart: 1 to 8388607. Optional `pressed`
## (default true) releases the key instead of pressing it. Optional
## `hold_frames` (default 0, 0 to 3600) keeps the key down for that many
## physics frames and then releases it; 0 leaves the key down until a
## `pressed:false` call, like a held finger. A positive hold is refused
## together with `pressed:false`, since a hold releases itself. Optional
## `timeout_ms` (default 5000, 100 to 30000) bounds the wait. The helper
## knows the game's physics rate, so before injecting anything it refuses a
## hold the wait cannot cover, naming the minimum timeout; nothing is
## injected on that refusal. When no game is attached the command refuses at
## once and sends nothing. A helper answer with ok false becomes an error
## carrying the helper's text; a wait that passes without an answer becomes a
## timeout error naming the measured causes. The release of a hold is driven
## inside the game, so a client timeout or a lost connection still leaves
## the key released as long as the game keeps running.
const DEFAULT_TIMEOUT_MS := 5000
const MIN_TIMEOUT_MS := 100
const MAX_TIMEOUT_MS := 30000
const MAX_HOLD_FRAMES := 3600


## Presses or releases the key in the running game, holding the connection
## until the helper answers or the wait passes.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("game_key requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	for key in request.keys():
		if not str(key) in ["command", "project_path", "key", "pressed", "hold_frames", "timeout_ms"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var key_value: Variant = request.get("key")
	if key_value == null or (typeof(key_value) != TYPE_INT and typeof(key_value) != TYPE_FLOAT):
		return CommandSupport.error("game_key requires an integer field: key")

	# The keycode bounds are the input commands' own: the same check
	# `input_stage` applies to a staged key event, so a code the game can
	# bind is a code this command can press. KEY_NONE (0) is refused because
	# pressing no key injects nothing observable.
	var checked := InputEvents.decode_event(0, {"type": "key", "physical_keycode": key_value}, "game_key")
	if checked.has("error"):
		return CommandSupport.error(checked["error"])

	var pressed_value: Variant = request.get("pressed", true)
	if typeof(pressed_value) != TYPE_BOOL:
		return CommandSupport.error("game_key requires a boolean field: pressed")

	var hold := _validate_hold_frames(request.get("hold_frames", 0))
	if hold.has("error"):
		return CommandSupport.error(hold["error"])
	if not bool(pressed_value) and int(hold["value"]) > 0:
		return CommandSupport.error("game_key cannot combine hold_frames with pressed false: a hold releases itself")

	var wait := _validate_timeout_ms(request.get("timeout_ms", DEFAULT_TIMEOUT_MS))
	if wait.has("error"):
		return CommandSupport.error(wait["error"])

	var debug = plugin._debug_plugin
	var id: String = debug.request_key(int(key_value), bool(pressed_value), int(hold["value"]), int(wait["value"]))
	if id.is_empty():
		return CommandSupport.error("no game is attached: start the game before pressing it")
	var timeout_ms: int = wait["value"]
	var poll := func() -> Dictionary:
		return _poll_key(debug, id)
	var cancel := func() -> void:
		debug.retire_key(id)
	return {
		"_pending": {
			"poll": poll,
			"deadline_msec": timeout_ms,
			"timeout_message": "no key reply arrived within %d ms: the game is likely stopped in the debugger, the helper is not loaded, or the game is busy" % timeout_ms,
			"on_cancel": cancel,
		}
	}


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help. `reply_wait_argument`
## tells the CLI to wait for `timeout_ms` plus two seconds.
static func interface() -> Dictionary:
	return {
		"name": "game_key",
		"description": "Press, hold and release a physical key in the running game through the game helper.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"reply_wait_argument": "timeout_ms",
		"arguments": [
		{"name": "key", "flag": null, "positional": 0, "type": "integer", "required": true, "default": null, "help": "Physical keycode to press, e.g. 68 for D; the plugin checks the 1 to 8388607 range shared with the input commands."},
		{"name": "pressed", "flag": "--pressed", "positional": null, "type": "boolean", "required": false, "default": true, "help": "Press the key when true, release it when false; refused with a positive hold_frames."},
		{"name": "hold_frames", "flag": "--hold-frames", "positional": null, "type": "integer", "required": false, "default": 0, "help": "Physics frames to hold the key before the game releases it; 0 leaves the key down until a pressed:false call; the plugin checks the 0 to 3600 range."},
		{"name": "timeout_ms", "flag": "--timeout-ms", "positional": null, "type": "integer", "required": false, "default": 5000, "help": "Longest wait for the helper's answer in milliseconds; the plugin checks the 100 to 30000 range."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Reads the helper's stored reply for `id`: an empty Dictionary while it has
## not arrived (the connection stays held), the key result once it has, or
## the helper's own error text when the helper refused the hold.
static func _poll_key(debug, id: String) -> Dictionary:
	var stored: Dictionary = debug.take_key_reply(id)
	if stored.is_empty():
		return {}
	debug.retire_key(id)
	if not bool(stored["ok"]):
		return CommandSupport.error(str(stored["error"]))
	return CommandSupport.ok({
		"key": int(stored["key"]),
		"pressed": bool(stored["pressed"]),
		"held_frames": int(stored["held_frames"]),
		"released": bool(stored["released"]),
	})


## Validates `hold_frames` as an integer in 0..3600, accepting an integral
## JSON number. 0 means no automatic release.
static func _validate_hold_frames(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 0 or int(value) > MAX_HOLD_FRAMES:
			return {"error": "hold_frames must be between 0 and %d" % MAX_HOLD_FRAMES}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "hold_frames must be an integer"}
		if number < 0.0 or number > float(MAX_HOLD_FRAMES):
			return {"error": "hold_frames must be between 0 and %d" % MAX_HOLD_FRAMES}
		return {"value": int(number)}
	return {"error": "hold_frames must be an integer"}


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
