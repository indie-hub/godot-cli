@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")

## A PNG that base64-encodes to about one mebibyte: 786432 source bytes
## become 1048576 encoded bytes, so this default keeps the reply around the
## 1 MiB line for a noisy full-viewport frame (which the helper downscales
## to fit, reporting the halvings in `scale`).
const DEFAULT_MAX_PNG_BYTES := 786432
## The largest capture the command accepts. Measured: a reply carrying about
## 2.9 MiB of base64 still passes the plugin and the CLI whole; the bound
## stays at eight mebibytes, the plugin's own request-size scale.
const MAX_MAX_PNG_BYTES := 8388608
const DEFAULT_TIMEOUT_MS := 5000
const MIN_TIMEOUT_MS := 100
const MAX_TIMEOUT_MS := 30000


## Captures the running game's viewport through the game helper. The command
## holds the connection until the helper answers or the wait passes (see the
## pending-reply members in `editor_plugin.gd`): it never blocks the editor
## thread, and the CLI waits for the declared `timeout_ms` plus two seconds.
## Read-only except for the capture request itself: no undo/redo action, no
## dirty flag, no file write, and the image travels in the reply only.
##
## `request` must carry string field `project_path`, checked first. Optional
## `max_png_bytes` (default 786432) bounds the PNG the helper encodes: the
## helper halves each dimension until the PNG fits, counting the halvings in
## `scale`. Optional `timeout_ms` (default 5000, 100 to 30000) bounds the
## wait. When no game is attached the command refuses at once and sends
## nothing. A helper answer with ok false becomes an error carrying the
## helper's text; a wait that passes without an answer becomes a timeout
## error naming the measured causes.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("capture_game requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	for key in request.keys():
		if not str(key) in ["command", "project_path", "max_png_bytes", "timeout_ms"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var cap := _validate_max_png_bytes(request.get("max_png_bytes", DEFAULT_MAX_PNG_BYTES))
	if cap.has("error"):
		return CommandSupport.error(cap["error"])

	var wait := _validate_timeout_ms(request.get("timeout_ms", DEFAULT_TIMEOUT_MS))
	if wait.has("error"):
		return CommandSupport.error(wait["error"])

	var debug = plugin._debug_plugin
	var id: String = debug.request_capture(int(cap["value"]))
	if id.is_empty():
		return CommandSupport.error("no game is attached: start the game before capturing it")
	var timeout_ms: int = wait["value"]
	var poll := func() -> Dictionary:
		return _poll_capture(debug, id)
	var cancel := func() -> void:
		debug.retire_capture(id)
	return {
		"_pending": {
			"poll": poll,
			"deadline_msec": timeout_ms,
			"timeout_message": "no capture reply arrived within %d ms: the game is likely stopped in the debugger, the helper is not loaded, or the game is busy" % timeout_ms,
			"on_cancel": cancel,
		}
	}


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help. `reply_wait_argument`
## tells the CLI to wait for `timeout_ms` plus two seconds.
static func interface() -> Dictionary:
	return {
		"name": "capture_game",
		"description": "Capture the running game's viewport as a PNG in the reply.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"reply_wait_argument": "timeout_ms",
		"arguments": [
		{"name": "max_png_bytes", "flag": "--max-png-bytes", "positional": null, "type": "integer", "required": false, "default": 786432, "help": "Largest PNG the helper encodes before halving each dimension; the plugin checks the 1 to 8388608 range."},
		{"name": "timeout_ms", "flag": "--timeout-ms", "positional": null, "type": "integer", "required": false, "default": 5000, "help": "Longest wait for the helper's answer in milliseconds; the plugin checks the 100 to 30000 range."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Reads the helper's stored reply for `id`: an empty Dictionary while it has
## not arrived (the connection stays held), the game capture once it has, or
## the helper's own error text when the helper reports failure.
static func _poll_capture(debug, id: String) -> Dictionary:
	var stored: Dictionary = debug.take_capture_reply(id)
	if stored.is_empty():
		return {}
	debug.retire_capture(id)
	if not bool(stored["ok"]):
		return CommandSupport.error(str(stored["error"]))
	var png: PackedByteArray = stored["png"]
	return CommandSupport.ok({
		"source": "game",
		"width": int(stored["width"]),
		"height": int(stored["height"]),
		"scale": int(stored["scale"]),
		"png_bytes": int(stored["size"]),
		"image_format": "png",
		"image_base64": Marshalls.raw_to_base64(png),
	})


## Validates `max_png_bytes` as an integer in 1..8388608, accepting an
## integral JSON number.
static func _validate_max_png_bytes(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 1 or int(value) > MAX_MAX_PNG_BYTES:
			return {"error": "max_png_bytes must be between 1 and %d" % MAX_MAX_PNG_BYTES}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "max_png_bytes must be an integer"}
		if number < 1.0 or number > float(MAX_MAX_PNG_BYTES):
			return {"error": "max_png_bytes must be between 1 and %d" % MAX_MAX_PNG_BYTES}
		return {"value": int(number)}
	return {"error": "max_png_bytes must be an integer"}


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
