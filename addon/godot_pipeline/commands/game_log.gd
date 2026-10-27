@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Returns the running game's log entries held by the editor side of the log
## channel, oldest first. Read-only: no undo/redo action, no dirty flag, no
## file write, and no edited scene is required.
##
## `request` must carry string field `project_path`. Optional `since`
## (default 0) keeps entries with a sequence number greater than it, and
## optional `limit` (default 500) keeps at most that many of the newest
## matching entries (1 to 4096). The project-path check runs first, then the
## unknown-field check, then every argument is validated, so a rejected
## request leaves everything unchanged. When no game has run yet the reply
## carries an empty entry list, not an error.
##
## The reply reports whether a game is attached now, the identity of the log
## run the entries belong to (it rises whenever a new game process restarts
## the sequence, so a client can tell a restart from new entries),
## `next_since` (the cursor to pass as `since` next time), the totals, and
## the three drop counters. A message entry carries `seq`, `msec`, `kind`,
## `text` and `error`; an error entry additionally carries the eight engine
## fields, the raw `backtraces`, the `script_location` parsed from them
## (`{file, line, function}` or null when no backtrace names a script
## frame) and the `engine_location` (`{file, line, function}` from the
## Logger fields, which name the engine for `push_warning`/`push_error`).
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("game_log requires a string field: project_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	for key in request.keys():
		if not str(key) in ["command", "project_path", "since", "limit"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var since := _validate_since(request.get("since", 0))
	if since.has("error"):
		return CommandSupport.error(since["error"])

	var limit := _validate_limit(request.get("limit", 500))
	if limit.has("error"):
		return CommandSupport.error(limit["error"])

	var debug = plugin._debug_plugin
	var state: Dictionary = debug.session_state()
	var drops: Dictionary = debug.dropped_count()
	var kept: Array = []
	for entry in debug.entries_since(int(since["value"])):
		kept.append(_present(entry))
	if kept.size() > int(limit["value"]):
		kept = kept.slice(kept.size() - int(limit["value"]))

	return CommandSupport.ok({
		"attached": bool(state["active"]),
		"game_state": debug.game_state(),
		"run_id": debug.run_identity(),
		"entries": kept,
		"next_since": int(state["next_since"]),
		"received": int(state["received"]),
		"buffered": int(state["buffered"]),
		"gaps": int(drops["gaps"]),
		"helper_dropped": int(drops["helper"]),
		"evicted": int(drops["evicted"]),
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "game_log",
		"description": "Show the running game's log entries forwarded by the game helper.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "since", "flag": "--since", "positional": null, "type": "integer", "required": false, "default": 0, "help": "Keep entries with a sequence number greater than this cursor."},
		{"name": "limit", "flag": "--limit", "positional": null, "type": "integer", "required": false, "default": 500, "help": "Keep at most this many of the newest matching entries; the plugin checks the 1 to 4096 range."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Presents one stored log entry for the reply. Backtrace strings stay verbatim
## in `backtraces`; `script_location` names the script frame parsed from them.
static func _present(entry: Dictionary) -> Dictionary:
	var shown := {
		"seq": int(entry["seq"]),
		"msec": int(entry["msec"]),
		"kind": str(entry["kind"]),
		"text": str(entry["text"]),
		"error": bool(entry["error"]),
	}
	if str(entry["kind"]) != "error":
		return shown
	shown["function"] = str(entry["function"])
	shown["file"] = str(entry["file"])
	shown["line"] = int(entry["line"])
	shown["code"] = str(entry["code"])
	shown["rationale"] = str(entry["rationale"])
	shown["editor_notify"] = bool(entry["editor_notify"])
	shown["error_type"] = int(entry["error_type"])
	var traces: Array = []
	for trace in entry["backtraces"]:
		traces.append(trace if typeof(trace) == TYPE_STRING else str(trace))
	shown["backtraces"] = traces
	shown["script_location"] = _script_location(traces)
	shown["engine_location"] = {
		"file": str(entry["file"]),
		"line": int(entry["line"]),
		"function": str(entry["function"]),
	}
	return shown


## Names the script frame of the game's own code inside the raw backtrace
## strings, or null when no backtrace names one. The first frame whose file
## is a project script is the game's own call site; engine frames name
## engine files. Measured in Godot 4.7.2: a frame reads
## `    [N] function (res://path.gd:LINE)` under the header line
## `GDScript backtrace (most recent call first):`.
static func _script_location(traces: Array) -> Variant:
	for trace in traces:
		var parsed := _parse_frame(str(trace))
		if not parsed.is_empty() and str(parsed["file"]).begins_with("res://"):
			return parsed
	return null


## Parses one backtrace string into `{file, line, function}`, or an empty
## Dictionary when no line of it is a script frame. The engine packs the
## whole stack, header line included, into one string, so every line is
## tried and the first script frame wins.
static func _parse_frame(trace: String) -> Dictionary:
	for raw_line in trace.split("\n"):
		var parsed := _parse_line(raw_line.strip_edges())
		if not parsed.is_empty():
			return parsed
	return {}


## Parses one `    [N] function (res://path.gd:LINE)` line, or an empty
## Dictionary for the header line and anything else.
static func _parse_line(line: String) -> Dictionary:
	if not line.begins_with("["):
		return {}
	var bracket := line.find("]")
	if bracket < 0:
		return {}
	var rest := line.substr(bracket + 1).strip_edges()
	var open := rest.rfind("(")
	var close := rest.rfind(")")
	if open < 0 or close < 0 or close <= open:
		return {}
	var function := rest.substr(0, open).strip_edges()
	var inner := rest.substr(open + 1, close - open - 1)
	var colon := inner.rfind(":")
	if colon < 0:
		return {}
	var file := inner.substr(0, colon).strip_edges()
	var line_number := inner.substr(colon + 1).strip_edges()
	if file.is_empty() or not line_number.is_valid_int():
		return {}
	return {"file": file, "line": int(line_number), "function": function}


## Validates `since` as a non-negative integer cursor, accepting an integral
## JSON number. There is no upper bound: a cursor past the newest entry
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


## Validates `limit` as an integer in 1..4096, accepting an integral JSON
## number. The game side holds at most 4096 entries, so a larger limit can
## never match more.
static func _validate_limit(value: Variant) -> Dictionary:
	if typeof(value) == TYPE_INT:
		if int(value) < 1 or int(value) > 4096:
			return {"error": "limit must be between 1 and 4096"}
		return {"value": int(value)}
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number != floorf(number):
			return {"error": "limit must be an integer"}
		if number < 1.0 or number > 4096.0:
			return {"error": "limit must be between 1 and 4096"}
		return {"value": int(number)}
	return {"error": "limit must be an integer"}
