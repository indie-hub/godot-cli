@tool
extends EditorPlugin
## Godot Pipeline bridge.
##
## Listens on a loopback-only TCP socket and answers a single JSON request
## per connection. Supported commands: `status`, `scene_tree`, `inspect_node`,
## `query_nodes`, `inspect_class`, `list_resources`, `inspect_resource`,
## `rename_node`, `create_node`, `set_property`, `delete_node`,
## `connect_signal`, `set_group`, `set_unique_name`, `instantiate_scene`,
## `save_scene`, `open_scene`, `console`, `set_parent`, `eval`, `attach_script`,
## `play`, `stop`, `game_log`, `capture_game`, `input_stage`, `input_read`, `input_apply` and `input_discard`. The read commands report the editor's status, the active
## edited scene's node tree, a node's class/child count/property values, the
## nodes matching a class, group, and name search, an engine class's ClassDB
## reflection (ancestors, properties, methods, signals), the resource files the
## editor file system holds, and the editor log messages captured since the
## plugin loaded; the editing commands change the active scene through the
## editor's undo/redo stack (one Undo/Redo step each) and never save it.
## `save_scene` persists the currently edited scene to the file path it already
## has, so edits made through the other commands survive a reload. `play` saves
## an unsaved edited scene before it launches the game, and `stop` ends a game
## that is running. `game_log` reports the running game's log entries held by
## the editor side of the log channel, and `capture_game` holds the connection
## open until the game answers a viewport capture or its wait passes (see the
## pending-reply members below). `open_scene` opens a scene by path, optionally saving a
## dirty edited scene first. Every editing
## command requires a `project_path` that matches the running editor's
## project, validates the whole request before it mutates anything, and
## rejects targets that would not persist when the scene is saved.
## `set_property` additionally rejects an int value that is not one of the
## declared values of an enum-hinted int property. `input_apply` writes the
## input actions `input_stage` left on the editor interface to
## `project.godot` with one project-settings save, and `input_discard` drops
## them without writing. `eval` runs submitted
## GDScript in the live editor: it is the one command that may break rule 3 (a
## direct scene edit from the snippet creates no undo entry) and rule 6 (the
## snippet can hold the main thread); its timeout is best effort and never
## interrupts the call, and it marks the scene unsaved only when the edited
## scene tree changed. The plugin also registers the `GodotPipelineCapture`
## game autoload on first start (guarded, see `_enter_tree`) and attaches the
## editor side of the in-game log and capture channel (`debug_plugin.gd`,
## added and removed with the plugin). The port is fixed, so only
## one Godot editor instance can host this plugin on a machine at a time.
## Requests and replies are single newline-terminated JSON objects. The plugin
## assembles a request across reads (up to an 8 MiB cap and a 5 second idle
## timeout), so a request may arrive in several TCP segments.
## The file is laid out as the socket server, request framing, `_reply`, and
## dispatch through a command-name table into one script per command under
## `commands/`; the pure value conversions between JSON and Godot Variants
## live in the preloaded `value_codec.gd`, and the reply constructors, the
## shared request guard, and the constants shared by more than one command
## live in `commands/command_support.gd`.

const HOST := "127.0.0.1"
const PORT := 47821

## A request larger than this (in bytes) is rejected without being applied.
## It bounds the buffered request while it is being assembled across reads.
const MAX_REQUEST_BYTES := 8388608
## How long a connection may send no new bytes before the plugin acts: a
## connection that has sent nothing is closed silently, and one that has sent
## an incomplete request is dropped with an error (or handled, if the buffered
## bytes already form a complete JSON object without the trailing newline).
const REQUEST_IDLE_TIMEOUT_MSEC := 5000

## Wire command names mapped to the preloaded script that handles each one.
## The handler scripts live under `commands/` and expose one static entry
## `run(plugin, request)` that returns the reply dictionary.
const COMMANDS := {
	"status": preload("commands/status.gd"),
	"scene_tree": preload("commands/scene_tree.gd"),
	"rename_node": preload("commands/rename_node.gd"),
	"create_node": preload("commands/create_node.gd"),
	"set_property": preload("commands/set_property.gd"),
	"inspect_node": preload("commands/inspect_node.gd"),
	"query_nodes": preload("commands/query_nodes.gd"),
	"inspect_class": preload("commands/inspect_class.gd"),
	"list_resources": preload("commands/list_resources.gd"),
	"inspect_resource": preload("commands/inspect_resource.gd"),
	"delete_node": preload("commands/delete_node.gd"),
	"connect_signal": preload("commands/connect_signal.gd"),
	"set_group": preload("commands/set_group.gd"),
	"set_unique_name": preload("commands/set_unique_name.gd"),
	"instantiate_scene": preload("commands/instantiate_scene.gd"),
	"save_scene": preload("commands/save_scene.gd"),
	"open_scene": preload("commands/open_scene.gd"),
	"console": preload("commands/console.gd"),
	"set_parent": preload("commands/set_parent.gd"),
	"eval": preload("commands/eval.gd"),
	"attach_script": preload("commands/attach_script.gd"),
	"play": preload("commands/play.gd"),
	"stop": preload("commands/stop.gd"),
	"game_log": preload("commands/game_log.gd"),
	"capture_game": preload("commands/capture_game.gd"),
	"input_stage": preload("commands/input_stage.gd"),
	"input_read": preload("commands/input_read.gd"),
	"input_apply": preload("commands/input_apply.gd"),
	"input_discard": preload("commands/input_discard.gd"),
}

## Version of the command interface the plugin publishes. A client that does
## not know it must refuse the interface instead of guessing.
const PROTOCOL_VERSION := 1

## The editor log capture behind the `console` command. Created once when the
## plugin loads and removed when it unloads, so only one logger is ever
## registered no matter how often the plugin is disabled and enabled.
const ConsoleCommand := preload("commands/console.gd")

## The game autoload behind the in-game capture helper. The guarded add in
## `_enter_tree` writes exactly this line; the engine may later normalize the
## value to the `uid://` form of the same script on a toggle cycle.
const AUTOLOAD_NAME := "GodotPipelineCapture"
const AUTOLOAD_PATH := "res://addons/godot_pipeline/scripts/helper.gd"

## The editor side of the in-game log and capture channel. Created once when
## the plugin loads and removed when it unloads, like the console logger.
const DebugPlugin := preload("debug_plugin.gd")

var _server: TCPServer
var _connection: StreamPeerTCP
var _console_logger: Logger
var _debug_plugin: EditorDebuggerPlugin

## A held reply, when a command returns one. The handler's reply carries a
## `_pending` dictionary with the `poll` callable (called every frame from
## `_process`; it returns an empty Dictionary while the answer is not ready
## and the full reply once it is), the relative `deadline_msec`, the
## `timeout_message` answered when the deadline passes, and an optional
## `on_cancel` callable run once when the client goes away first or when the
## deadline passes (a command uses it to retire what the late reply would
## otherwise retain). While a
## reply is pending the connection stays open, the request idle timeout does
## not fire, and the plugin answers no other client: the server holds one
## connection at a time, so a second client waits at the TCP backlog until
## the pending reply is written (measured) or the deadline passes.
var _pending_poll := Callable()
var _pending_cancel := Callable()
var _pending_start_msec := 0
var _pending_wait_msec := 0
var _pending_timeout_message := ""

var _request_buffer := PackedByteArray()
var _request_scanned := 0
var _request_last_activity_msec := 0


func _enter_tree() -> void:
	_register_autoload_once()
	_server = TCPServer.new()
	var error := _server.listen(PORT, HOST)
	if error != OK:
		push_error("Godot Pipeline: failed to listen on %s:%d (%s)" % [HOST, PORT, error_string(error)])
		_server = null
		return
	set_process(true)
	print("Godot Pipeline: listening on %s:%d" % [HOST, PORT])
	if _console_logger == null:
		_console_logger = ConsoleCommand.Capture.new()
		OS.add_logger(_console_logger)
	if _debug_plugin == null:
		_debug_plugin = DebugPlugin.new()
		add_debugger_plugin(_debug_plugin)


func _exit_tree() -> void:
	set_process(false)
	if _console_logger != null:
		OS.remove_logger(_console_logger)
		_console_logger = null
	if _debug_plugin != null:
		remove_debugger_plugin(_debug_plugin)
		_debug_plugin = null
	_reset_connection()
	if _server != null:
		_server.stop()
		_server = null


## Registers the game autoload exactly once: only when no autoload holds the
## name (a colliding entry is left untouched), and never when the helper
## script file itself is missing (one editor error, no registration). The
## entry takes effect live at once and reaches `project.godot` on disk with
## the next project-settings save.
func _register_autoload_once() -> void:
	var key := "autoload/" + AUTOLOAD_NAME
	if ProjectSettings.has_setting(key):
		return
	if not ResourceLoader.exists(AUTOLOAD_PATH):
		push_error("Godot Pipeline: capture helper missing, autoload not registered: %s" % AUTOLOAD_PATH)
		return
	add_autoload_singleton(AUTOLOAD_NAME, AUTOLOAD_PATH)


## Removes the game autoload, but only the entry this plugin wrote: the
## `res://` value of the guarded add, or the `uid://` value the engine
## normalizes it to on a toggle cycle. A colliding entry keeps its value.
func _disable_plugin() -> void:
	var key := "autoload/" + AUTOLOAD_NAME
	if ProjectSettings.has_setting(key) and _is_own_autoload(str(ProjectSettings.get_setting(key))):
		remove_autoload_singleton(AUTOLOAD_NAME)


## Whether an autoload value is the one the guarded add wrote, in either of
## its two on-disk forms.
static func _is_own_autoload(value: String) -> bool:
	if value == "*" + AUTOLOAD_PATH:
		return true
	var uid := ResourceLoader.get_resource_uid(AUTOLOAD_PATH)
	if uid < 0:
		return false
	return value == "*" + ResourceUID.id_to_text(uid)


func _process(_delta: float) -> void:
	if _server == null:
		return
	if _connection == null:
		if not _server.is_connection_available():
			return
		_connection = _server.take_connection()
		_request_buffer = PackedByteArray()
		_request_scanned = 0
		_request_last_activity_msec = Time.get_ticks_msec()
	if _pending_poll.is_valid():
		_poll_pending()
		return
	_connection.poll()
	if _connection.get_status() != StreamPeerSocket.STATUS_CONNECTED:
		_reset_connection()
		return
	var available := _connection.get_available_bytes()
	if available > 0:
		var chunk := _connection.get_data(available)
		if chunk[0] != OK:
			_reset_connection()
			return
		_request_buffer.append_array(chunk[1])
		_request_last_activity_msec = Time.get_ticks_msec()
		if _request_buffer.size() > MAX_REQUEST_BYTES:
			_reply_error(
				"request exceeds the maximum size of %d bytes (received %d bytes without a terminating newline)" % [MAX_REQUEST_BYTES, _request_buffer.size()]
			)
			_reset_connection()
			return
		var newline_index := _scan_for_newline()
		if newline_index >= 0:
			_handle_complete_request(newline_index)
			if not _pending_poll.is_valid():
				_reset_connection()
			return
	if _request_buffer.is_empty():
		# The connection has sent nothing; close it once it has been silent
		# for the idle timeout, with no reply.
		if _request_last_activity_msec > 0 and Time.get_ticks_msec() - _request_last_activity_msec > REQUEST_IDLE_TIMEOUT_MSEC:
			_reset_connection()
	else:
		if Time.get_ticks_msec() - _request_last_activity_msec > REQUEST_IDLE_TIMEOUT_MSEC:
			_handle_idle_fallback()
			if not _pending_poll.is_valid():
				_reset_connection()


## Scans the request buffer for the first newline byte, starting from the
## offset where the previous scan stopped so old bytes are never re-examined.
## Returns the newline index, or -1 if there is none.
func _scan_for_newline() -> int:
	var index := _request_buffer.find(0x0A, _request_scanned)
	if index >= 0:
		_request_scanned = _request_buffer.size()
		return index
	_request_scanned = _request_buffer.size()
	return -1


## Handles a request whose terminating newline has been received. Only the
## bytes before the first newline belong to the request; anything after it is
## ignored, because the protocol is one request per connection.
func _handle_complete_request(newline_index: int) -> void:
	# Truncate in place (no copy of the request bytes) before decoding, since
	# the buffer is discarded right after the request is handled.
	_request_buffer.resize(newline_index)
	_handle_request(_request_buffer.get_string_from_utf8(), _request_buffer.size())


## Handles a connection that sent bytes but then stalled without a newline.
## A raw client that forgot the trailing newline still works: if the buffered
## bytes parse as a complete JSON request, handle it; otherwise reply with an
## incomplete-request error. Nothing is applied in the error case.
func _handle_idle_fallback() -> void:
	var text := _request_buffer.get_string_from_utf8()
	var parsed: Variant = JSON.parse_string(text.strip_edges())
	if typeof(parsed) == TYPE_DICTIONARY and parsed.has("command"):
		_handle_request(text, _request_buffer.size())
	else:
		_reply_error(
			"malformed request (incomplete: %d bytes received without a terminating newline)" % _request_buffer.size()
		)


## Drops the current connection and its partial request, so state never leaks
## between connections.
func _reset_connection() -> void:
	_clear_pending()
	if _connection != null:
		_connection.disconnect_from_host()
		_connection = null
	_request_buffer = PackedByteArray()
	_request_scanned = 0
	_request_last_activity_msec = 0


## Forgets a held reply without answering it. The connection handling is the
## caller's business.
func _clear_pending() -> void:
	_pending_poll = Callable()
	_pending_cancel = Callable()
	_pending_start_msec = 0
	_pending_wait_msec = 0
	_pending_timeout_message = ""


## Advances a held reply by one frame: answers and closes when the poll
## reports the reply, errors and closes when the deadline passes, and drops
## everything silently when the client went away. Never blocks.
func _poll_pending() -> void:
	_connection.poll()
	if _connection.get_status() != StreamPeerSocket.STATUS_CONNECTED:
		if _pending_cancel.is_valid():
			_pending_cancel.call()
		_reset_connection()
		return
	var done: Dictionary = _pending_poll.call()
	if not done.is_empty():
		_clear_pending()
		_reply(done)
		_reset_connection()
		return
	if Time.get_ticks_msec() - _pending_start_msec >= _pending_wait_msec:
		var message := _pending_timeout_message
		if _pending_cancel.is_valid():
			_pending_cancel.call()
		_clear_pending()
		_reply_error(message)
		_reset_connection()


## Dispatches a parsed request to the command's script. Malformed requests
## and unknown commands reply exactly as before the per-command split: the
## `command` value is matched against the wire-name table, and a non-string
## value (or one that names no command) is reported as unknown.
func _handle_request(raw_text: String, raw_bytes: int) -> void:
	var parsed: Variant = JSON.parse_string(raw_text.strip_edges())
	if typeof(parsed) != TYPE_DICTIONARY or not parsed.has("command"):
		_reply_error("malformed request (received %d bytes)" % raw_bytes)
		return
	var command: Variant = parsed["command"]
	if typeof(command) == TYPE_STRING and command == "commands":
		# Discovery reads no scene and checks no project path: every handler
		# supplies its own entry, in the order of the `COMMANDS` table.
		var interfaces: Array = []
		for handler in COMMANDS.values():
			interfaces.append(handler.interface())
		_reply({"status": "ok", "data": {"protocol_version": PROTOCOL_VERSION, "commands": interfaces}})
		return
	var handler: Variant = COMMANDS.get(command)
	if handler == null:
		_reply_error("unknown command: %s" % str(command))
		return
	var result: Dictionary = handler.run(self, parsed)
	# A handler that cannot answer yet returns a reply carrying `_pending`
	# instead (see the member comment above): keep the connection open and
	# let `_process` answer from there. Anything else is answered at once.
	if result.has("_pending"):
		var pending: Dictionary = result["_pending"]
		_pending_poll = pending["poll"]
		_pending_cancel = pending.get("on_cancel", Callable())
		_pending_start_msec = Time.get_ticks_msec()
		_pending_wait_msec = int(pending["deadline_msec"])
		_pending_timeout_message = str(pending["timeout_message"])
		return
	_reply(result)


func _reply_error(message: String) -> void:
	_reply({"status": "error", "message": message})


func _reply(payload: Dictionary) -> void:
	if _connection == null:
		return
	# put_utf8_string() prepends a 32-bit length header; this protocol is
	# plain newline-delimited JSON, so write raw bytes instead.
	var text := JSON.stringify(payload) + "\n"
	_connection.put_data(text.to_utf8_buffer())
