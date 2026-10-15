@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")

## Largest encoded return value the reply carries. A bigger value is replaced
## by an error that names its size, so a runaway snippet cannot inflate the
## reply without bound (the code still ran; only its value is dropped).
const MAX_VALUE_BYTES := 1048576

## Deepest tagged nesting the reply carries, counted in JSON levels: an Array
## counts one and a Dictionary counts two (the outer object and its args
## array). The container under examination counts toward the total.
## JSON.from_native encodes a tagged terminal value (a Vector2, a packed array,
## a Transform3D) as an object plus an args array, and the walk does not count
## those two levels, so such a value can reach two levels past the limit. The
## real command line reads about 125 levels, so the reply stays readable. A
## deeper value is replaced by a completed-false reply that names the limit;
## the code still ran and the scene report is unchanged.
const MAX_TAGGED_DEPTH := 100

## Milliseconds a request may omit `timeout` before this value applies.
const DEFAULT_TIMEOUT_MSEC := 5000


## Captures the errors and the printed output the submitted code produces while
## it runs. `OS.add_logger` installs one logger for the duration of the call and
## removes it right after, including on failure, so only this snippet's output is
## seen. The engine reports a compile error at reload and a runtime error during
## the call through `_log_error`; `print()` reaches `_log_message` with
## `error` false. Control characters other than tab, newline and carriage
## return are dropped so the reply stays valid JSON, and a captured line keeps
## its spaces: only its trailing line break is removed.
class _EvalLogger:
	extends Logger

	var errors: Array = []
	var output: Array = []

	func _log_error(function: String, file: String, line: int, code: String, rationale: String, _editor_notify: bool, error_type: int, _script_backtraces) -> void:
		var message := code
		if rationale != "":
			message = "%s (%s)" % [code, rationale]
		var entry := {"file": _sanitize(file), "line": line, "function": _sanitize(function), "message": _sanitize(message)}
		errors.append({"entry": entry, "type": error_type})

	func _log_message(message: String, error: bool) -> void:
		if not error:
			output.append(_strip_break(_sanitize(message)))

	static func _sanitize(text: String) -> String:
		var out := ""
		for index in range(text.length()):
			var code := text.unicode_at(index)
			if code < 32 and code != 9 and code != 10 and code != 13:
				continue
			out += String.chr(code)
		return out

	static func _strip_break(line: String) -> String:
		var end := line.length()
		while end > 0 and (line.unicode_at(end - 1) == 10 or line.unicode_at(end - 1) == 13):
			end -= 1
		return line.left(end)


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "eval",
		"description": "Run a GDScript snippet in the live editor and return its value. A snippet that switches scenes is outside the dirty-marking guarantee; check status for unsaved scenes.",
		"needs_project_path": true,
		"edits_scene": true,
		"refresh_poll": false,
		"arguments": [
		{"name": "code", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "A complete GDScript source defining func run(editor, undo_redo)."},
		{"name": "timeout", "flag": "--timeout", "positional": null, "type": "integer", "required": false, "default": 5000, "help": "Best-effort timeout in milliseconds; the call is never interrupted."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Compiles `code` as GDScript, instantiates it, and calls its `run` entry on
## the main thread. `run` receives the editor interface and the editor undo
## manager. The reply reports the returned value as typed JSON, the captured
## errors and printed output, whether the run returned normally, the edited
## scene's fingerprint before and after (as `scene_changed`), the elapsed
## milliseconds, and whether that elapsed time exceeded `timeout`.
##
## This is the one command allowed to break rule 3 (a direct scene edit from
## the snippet creates no undo entry) and rule 6 (the snippet can hold the main
## thread). The timeout is best effort and never interrupts the call. A changed
## fingerprint inside the same scene root marks that scene unsaved; a snippet
## that merely opens another scene leaves the dirty state alone, and an edit
## that cannot be attributed to the current scene is not marked. Nothing is saved.
##
## `request` must carry string fields `code` and `project_path`, and may carry
## an integer `timeout` (default 5000, must be positive). An absent timeout
## gets the default; an explicit null is refused like any other invalid value.
## The project-path check runs first, before any other field is validated, so
## a mismatched caller is rejected before the code is compiled or run.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("eval requires a string field: project_path")
	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var code_value: Variant = request.get("code")
	if typeof(code_value) != TYPE_STRING:
		return CommandSupport.error("eval requires a string field: code")
	var timeout_msec := DEFAULT_TIMEOUT_MSEC
	if request.has("timeout"):
		timeout_msec = _resolve_timeout(request.get("timeout"))
	if timeout_msec == 0:
		return CommandSupport.error("timeout must be a positive integer in milliseconds")

	var editor := plugin.get_editor_interface()
	var undo_redo := plugin.get_undo_redo()

	var logger := _EvalLogger.new()
	var root_before := editor.get_edited_scene_root()
	var scene_before := _scene_fingerprint(root_before)
	var started_msec := Time.get_ticks_msec()

	OS.add_logger(logger)
	var compiled := _compile_and_run(editor, undo_redo, code_value, logger)
	var run_completed: bool = compiled["completed"]
	var run_value: Variant = compiled["value"]
	var errors_after_run := logger.errors.size()
	# A value nested past the tagged depth never reaches the encoder, so the
	# reply stays valid JSON the command line can read. Encoding itself stays
	# inside the logger window: the engine reports a value it cannot encode
	# (an Object, or nesting past its own recursion depth) as an error, and
	# that has to reach `errors` rather than only the editor's own log.
	var too_deep := false
	var value: Variant = null
	if run_completed:
		if _exceeds_depth(run_value, MAX_TAGGED_DEPTH):
			too_deep = true
		else:
			value = JSON.from_native(run_value)
	var encoding_failed := false
	if run_completed and not too_deep:
		for index in range(errors_after_run, logger.errors.size()):
			if int(logger.errors[index]["type"]) != 1:
				encoding_failed = true
				break
	OS.remove_logger(logger)

	var root_after := editor.get_edited_scene_root()
	var scene_changed: bool = _scene_fingerprint(root_after) != scene_before
	# Only a change inside the same scene root marks a scene unsaved. A mere
	# switch leaves a clean scene clean; an edit that cannot be attributed to
	# the current scene is not marked.
	var marked := false
	if scene_changed and root_after != null and root_after == root_before:
		editor.mark_scene_as_unsaved()
		marked = true

	var errors: Array = []
	for captured in logger.errors:
		var entry: Dictionary = (captured["entry"] as Dictionary).duplicate()
		entry["type"] = _type_name(int(captured["type"]))
		errors.append(entry)

	var timeout_exceeded: bool = Time.get_ticks_msec() - started_msec > timeout_msec
	var elapsed_msec := Time.get_ticks_msec() - started_msec
	if too_deep:
		errors.append({"file": "", "line": 0, "function": "eval", "message": "eval produced a value nested deeper than %d levels and it was not returned (the code ran)" % MAX_TAGGED_DEPTH, "type": "error"})
	var completed: bool = run_completed and not encoding_failed and not too_deep
	if completed:
		var value_size := JSON.stringify(value).to_utf8_buffer().size()
		if value_size > MAX_VALUE_BYTES:
			var marked_text := "; the edited scene changed and is marked unsaved" if marked else ""
			return CommandSupport.error(
				"eval produced a value larger than %d bytes (the code ran); the value is %d bytes and was not returned%s"
				% [MAX_VALUE_BYTES, value_size, marked_text]
			)
	return CommandSupport.ok({
		"value": value if completed else null,
		"completed": completed,
		"errors": errors,
		"output": logger.output,
		"scene_changed": scene_changed,
		"elapsed_msec": elapsed_msec,
		"timeout_exceeded": timeout_exceeded,
	})


## Compiles `code`, instantiates it, and calls `run`. Returns a dictionary with
## `completed` (true only when the snippet compiled and `run` returned without
## a non-warning script error, including an error while the reload initializes
## the script and an error in `_init`) and `value` (the raw returned Variant,
## meaningless unless `completed`). The logger must already be installed, so a
## compile error at reload, a runtime error during reload, construction, and the
## call are all captured. A warning never fails the run and never discards the
## value. A manually managed instance that the snippet left parentless and out of
## the tree is freed; a node in the tree, or one a retained parent still owns,
## stays alive.
static func _compile_and_run(editor: EditorInterface, undo_redo: EditorUndoRedoManager, code: String, logger: _EvalLogger) -> Dictionary:
	var script := GDScript.new()
	script.source_code = code
	# The count starts before the reload, because an error raised while reload
	# initializes a @tool script (a static initializer, `_static_init`, or a
	# preloaded @tool script) fails the run exactly like one raised during
	# construction or the call.
	var errors_before_run: int = logger.errors.size()
	if script.reload() != OK:
		return {"completed": false, "value": null}

	var instance: Variant = script.new()
	if instance == null:
		logger.errors.append({"entry": {"file": "", "line": 0, "function": "eval", "message": "the submitted code could not be instantiated"}, "type": 2})
		return {"completed": false, "value": null}
	if not instance.has_method("run"):
		_free_if_orphan(instance)
		logger.errors.append({"entry": {"file": "", "line": 0, "function": "eval", "message": "the submitted code does not define func run(editor, undo_redo)"}, "type": 2})
		return {"completed": false, "value": null}

	var result: Variant = instance.call("run", editor, undo_redo)
	var run_failed := false
	for index in range(errors_before_run, logger.errors.size()):
		if int(logger.errors[index]["type"]) != 1:
			run_failed = true
			break
	_free_if_orphan(instance)
	return {"completed": not run_failed, "value": result}


## Frees a submitted-script instance the snippet left behind, unless the
## engine frees it (RefCounted) or the snippet deliberately kept it (a Node
## inside the tree, or a Node parented to another Node, stays alive: a parent
## outside the tree that the snippet retains still owns its children).
static func _free_if_orphan(instance: Variant) -> void:
	if instance is RefCounted:
		return
	if instance is Node:
		var node := instance as Node
		if node.is_inside_tree() or node.get_parent() != null:
			return
	if instance is Object:
		(instance as Object).free()


## The severity name the `console` command uses for a logger error type:
## warning, error, script, or shader.
static func _type_name(error_type: int) -> String:
	match error_type:
		1:
			return "warning"
		2:
			return "script"
		3:
			return "shader"
	return "error"


## Whether `value` nests deeper than `limit`, counting the containers the way
## MAX_TAGGED_DEPTH describes. The walk stops at the first excess, so a runaway
## value costs only a bounded scan.
static func _exceeds_depth(value: Variant, limit: int) -> bool:
	var stack: Array = [[value, 0]]
	while not stack.is_empty():
		var frame: Array = stack.pop_back()
		var node: Variant = frame[0]
		var depth: int = int(frame[1])
		if node is Array:
			if depth + 1 > limit:
				return true
			for child in (node as Array):
				stack.append([child, depth + 1])
		elif node is Dictionary:
			if depth + 2 > limit:
				return true
			for key in (node as Dictionary).keys():
				stack.append([key, depth + 2])
				stack.append([(node as Dictionary)[key], depth + 2])
	return false


## The integer `timeout` a request carries: the value when it is a positive
## integer, or 0 when it is invalid (null, a non-integer, or an integer that
## is not positive). 0 is never a valid timeout, so it doubles as the
## rejection marker. Absence is handled by the caller, which applies the
## default without calling here.
static func _resolve_timeout(value: Variant) -> int:
	if typeof(value) == TYPE_INT:
		if value > 0:
			return int(value)
		return 0
	if typeof(value) == TYPE_FLOAT:
		var number := float(value)
		if number == floorf(number) and number > 0.0:
			return int(number)
	return 0


## A string that changes whenever the edited scene tree changes, built from each
## node's path, class, owner, script, groups, signal connections, and stored
## property values. An unchanged scene yields an identical string, so comparing
## the capture taken before and after the call detects every structural or
## stored-property edit. An absent scene root yields an empty string.
static func _scene_fingerprint(scene_root: Node) -> String:
	if scene_root == null:
		return ""
	var parts: Array[String] = []
	_fingerprint_node(scene_root, parts)
	return "\n".join(parts)


## Appends one record per node, in tree order (parent before children, siblings
## in order), to `parts`. Stored properties are read with their `var_to_str`
## form so a value change shows up in the record.
static func _fingerprint_node(node: Node, parts: Array[String]) -> void:
	var owner_path := "."
	if node.owner != null:
		owner_path = str(node.owner.get_path())
	var script_path := ""
	var script: Variant = node.get_script()
	if script != null:
		script_path = script.resource_path

	var groups: Array = []
	for group in node.get_groups():
		groups.append(str(group))
	groups.sort()

	var connections: Array = []
	for signal_info in node.get_signal_list():
		var signal_name: String = signal_info["name"]
		for connection in node.get_signal_connection_list(signal_name):
			connections.append("%s->%s" % [signal_name, str(connection["callable"])])
	connections.sort()

	var properties: Array = []
	for info in node.get_property_list():
		if int(info["usage"]) & PROPERTY_USAGE_STORAGE != 0:
			properties.append("%s=%s" % [info["name"], var_to_str(node.get(info["name"]))])

	parts.append("%s|%s|owner=%s|script=%s|groups=[%s]|connections=[%s]|properties=[%s]" % [
		str(node.get_path()),
		node.get_class(),
		owner_path,
		script_path,
		",".join(groups),
		";".join(connections),
		";".join(properties),
	])
	for child in node.get_children():
		_fingerprint_node(child, parts)
