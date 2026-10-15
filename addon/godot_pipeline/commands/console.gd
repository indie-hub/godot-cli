@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")

## Levels the `level` filter accepts. `all` keeps every message.
const LEVELS := ["all", "info", "warning", "error", "script", "shader"]


## Receives the editor log stream through `OS.add_logger` and keeps the
## messages since the plugin loaded in a bounded buffer. The callbacks arrive
## from any thread, so every buffer access holds the mutex and does only the
## append or the snapshot inside it. Registration happens once in the
## plugin's `_enter_tree` and removal in `_exit_tree`, so a disable/enable
## cycle leaves exactly one logger.
class Capture extends Logger:
	const MAX_MESSAGES := 1000
	const MAX_TEXT_BYTES := 4096

	static var _mutex := Mutex.new()
	static var _messages: Array = []
	static var _next_seq := 1
	static var _dropped := 0

	## Plain stdout messages (`print`) arrive with `error` false and stderr
	## ones (`printerr`) with it true.
	func _log_message(message: String, error: bool) -> void:
		_append("error" if error else "info", message)

	## Errors carry their origin; only the file name is kept so the text
	## stays short. The level follows the engine's error type.
	func _log_error(_function: String, file: String, line: int, code: String, rationale: String, _editor_notify: bool, error_type: int, _script_backtraces: Array) -> void:
		var level := "error"
		if error_type == ERROR_TYPE_WARNING:
			level = "warning"
		elif error_type == ERROR_TYPE_SCRIPT:
			level = "script"
		elif error_type == ERROR_TYPE_SHADER:
			level = "shader"
		var detail := code if code != "" else rationale
		_append(level, "%s:%d: %s: %s" % [file.get_file(), line, _function, detail])

	## Appends one message with the next sequence number, cutting the text at
	## the byte cap and evicting the oldest entry past the message cap. The
	## number never restarts, not even after a clear. ASCII control characters
	## (editor progress lines carry ANSI escapes) are dropped, since the wire
	## reply is JSON and must not hold raw control bytes. A cut that lands
	## inside a multibyte character backs off to the character boundary, so
	## the stored text stays valid UTF-8 within the cap.
	static func _append(level: String, text: String) -> void:
		var bytes := _sanitize(text).to_utf8_buffer()
		var truncated := false
		if bytes.size() > MAX_TEXT_BYTES:
			# The bytes hold valid UTF-8, so the cut is on a character
			# boundary exactly when the first excluded byte starts a new
			# character; otherwise it continues the split one, so step back
			# to that character's first byte.
			var end := MAX_TEXT_BYTES
			while end > 0 and (bytes[end] & 0xC0) == 0x80:
				end -= 1
			bytes = bytes.slice(0, end)
			truncated = true
		_mutex.lock()
		_messages.append({
			"seq": _next_seq,
			"level": level,
			"text": bytes.get_string_from_utf8(),
			"truncated": truncated,
		})
		_next_seq += 1
		if _messages.size() > MAX_MESSAGES:
			_messages.pop_front()
			_dropped += 1
		_mutex.unlock()

	## Copies the buffer and its cursor state; empties the buffer when asked,
	## after the copy is taken, so the reply is built from the full content.
	static func read_and_maybe_clear(clear: bool) -> Dictionary:
		_mutex.lock()
		var snapshot := _messages.duplicate()
		var state := {"messages": snapshot, "dropped": _dropped, "next_since": _next_seq - 1}
		if clear:
			_messages = []
		_mutex.unlock()
		return state


	## Drops the ASCII control characters of `text`, except tab, newline and
	## carriage return, so the stored message survives the JSON reply.
	static func _sanitize(text: String) -> String:
		var clean := ""
		for index in text.length():
			var character := text[index]
			var code := character.unicode_at(0)
			if code >= 32 or character == "\t" or character == "\n" or character == "\r":
				clean += character
		return clean


## Returns the editor log messages captured since the plugin loaded, newest
## last. Read-only except for the optional clear: no undo/redo action, no
## dirty flag, no file write, and no edited scene is required.
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to read).
## Optional `tail` (default 100) keeps only the last N matching messages and
## must be an integer in 1..1000; optional `level` (default "all") keeps one
## of info, warning, error, script or shader; optional `since` (default 0)
## keeps messages with a sequence number greater than it; optional bool
## `clear` (default false) empties the buffer after the reply is built, while
## the sequence numbers continue. The project-path check runs before anything
## else, then every argument is validated, then the buffer is read, so a
## rejected request leaves the buffer untouched.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("console requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var tail := _validate_tail(request.get("tail", 100))
	if tail.has("error"):
		return CommandSupport.error(tail["error"])

	var level_value: Variant = request.get("level", "all")
	if typeof(level_value) != TYPE_STRING or not level_value in LEVELS:
		return CommandSupport.error("unknown level: %s" % str(level_value))
	var level: String = level_value

	var since := _validate_since(request.get("since", 0))
	if since.has("error"):
		return CommandSupport.error(since["error"])

	var clear_value: Variant = request.get("clear", false)
	if typeof(clear_value) != TYPE_BOOL:
		return CommandSupport.error("clear must be a boolean")

	var state := Capture.read_and_maybe_clear(bool(clear_value))
	var kept: Array = []
	for entry in state["messages"]:
		if int(entry["seq"]) > int(since["value"]) and (level == "all" or str(entry["level"]) == level):
			kept.append(entry)
	if kept.size() > int(tail["value"]):
		kept = kept.slice(kept.size() - int(tail["value"]))
	return CommandSupport.ok({
		"messages": kept,
		"count": kept.size(),
		"dropped": int(state["dropped"]),
		"next_since": int(state["next_since"]),
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "console",
		"description": "Show the editor log messages captured since the plugin loaded.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "tail", "flag": "--tail", "positional": null, "type": "integer", "required": false, "default": 100, "help": "Keep only the last N matching messages; the plugin checks the range."},
		{"name": "level", "flag": "--level", "positional": null, "type": "string", "required": false, "default": "all", "help": "Keep messages of this level: all, info, warning, error, script or shader."},
		{"name": "since", "flag": "--since", "positional": null, "type": "integer", "required": false, "default": 0, "help": "Keep messages with a sequence number greater than this cursor."},
		{"name": "clear", "flag": "--clear", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Empty the buffer after the reply is built."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Validates `tail` the way the shared limit check does, with messages naming
## this field: an integer in 1..1000, accepting an integral JSON number.
static func _validate_tail(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 1 or int(value) > 1000:
			return {"error": "tail must be between 1 and 1000"}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "tail must be an integer"}
		if number < 1.0 or number > 1000.0:
			return {"error": "tail must be between 1 and 1000"}
		return {"value": int(number)}
	return {"error": "tail must be an integer"}


## Validates `since` as a non-negative integer cursor, accepting an integral
## JSON number. There is no upper bound: a cursor past the newest message
## simply matches nothing.
static func _validate_since(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 0:
			return {"error": "since must not be negative"}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "since must be an integer"}
		if number < 0.0:
			return {"error": "since must not be negative"}
		return {"value": int(number)}
	return {"error": "since must be an integer"}
