@tool
extends Node
## In-game capture helper behind the `GodotPipelineCapture` autoload.
##
## Forwards the game log to the editor over the debugger channel and answers
## viewport capture requests from the editor. Runs only while the game is
## debugged: `_ready` returns without registering anything when
## `EngineDebugger.is_active()` is false, so an exported or plain run neither
## forwards nor registers. Never writes a file; the PNG is only encoded to a
## buffer for the reply.
##
## Log forwarding: a `Logger` added with `OS.add_logger` receives `print`
## through `_log_message` and warnings, errors and runtime errors through
## `_log_error` with all eight engine fields (`function`, `file`, `line`,
## `code`, `rationale`, `editor_notify`, `error_type`, `script_backtraces`).
## The logger callbacks can arrive from any thread, so each entry is appended
## to a bounded queue under a `Mutex` and the queue is drained on the main
## loop in `_process`, where the entries are sent from — except a script
## runtime error seen on the main thread, which is sent at once with the
## entries already queued, so the stopping error reaches the editor even
## while the debugger holds the game. Appending never blocks:
## when the queue is full the entry is dropped and `_dropped` counts it. Every
## sent entry carries a `seq` number starting at 1 and increasing by one per
## game process, the timestamp in msec, and the current drop count, so the
## editor detects gaps from the sequence and reads the counter from any entry.
##
## Log payload (`godot_pipeline:log`), positional: `[seq, msec, dropped, kind,
## text_or_empty, error, function, file, line, code, rationale, editor_notify,
## error_type, backtraces]`. `kind` is `"message"` for a `_log_message` entry
## (only `text_or_empty` and `error` apply) or `"error"` for a `_log_error`
## entry (only the eight engine fields apply; the others are empty). Each
## `backtraces` element keeps the engine's Dictionary when it is one and is
## the element stringified otherwise.
##
## Capture: `register_message_capture("godot_pipeline", ...)` receives the
## request named `"capture"` (the prefix is matched by the registration, so
## the callback sees the short name) with data `[id, max_png_bytes]`. The
## request waits in a First In, First Out queue and is handled one at a time
## on the main loop: after two `frame_post_draw` signals the current viewport
## image is read and PNG-encoded, and while the encoded size exceeds
## `max_png_bytes` the image is halved in each dimension and re-encoded.
## `scale` counts the halvings (0 is the full frame). The reply
## (`godot_pipeline:capture_reply`) is `[id, ok, width, height, scale, png,
## size, error]`: on success `ok` is true with the PNG bytes as a
## `PackedByteArray` and its size; on failure `ok` is false with the reason
## in `error` and empty image fields (no image because the renderer is
## headless or dummy, an invalid request, or an encode that never fits).
##
## Input injection: the same registration also receives `"key"` with data
## `[id, keycode, pressed, hold_frames, timeout_ms]` and `"click"` with data
## `[id, x, y]`. Both wait in one First In, First Out queue and run one at a
## time on the main loop through `Input.parse_input_event`, so a second
## request waits for the active hold to finish. A key press (or release) is
## an `InputEventKey` carrying the physical keycode; a positive hold keeps
## the key down for that many physics frames and then releases it, and the
## reply waits for the release, while 0 leaves the key down until a
## `pressed:false` call. Before injecting anything for a hold the helper
## compares the wait against the time the hold needs at the game's own
## physics rate and refuses, naming the minimum wait, when the wait cannot
## cover it; nothing is injected on that refusal. A click is a left-button
## press and, one frame later, the release at the same viewport position. The
## replies are `godot_pipeline:key_reply` (`[id, ok, key, pressed,
## held_frames, released, error]`, with `released` telling whether the key is
## up again) and `godot_pipeline:click_reply` (`[id, ok, x, y, button,
## error]`). The helper runs with `PROCESS_MODE_ALWAYS`, so injection and
## the hold countdown keep working while the tree is paused; a paused game
## still polls no movement and its Buttons still do not press (Godot GUI
## semantics), which the caller checks with the log and a capture.

const PREFIX := "godot_pipeline"

## How many log entries wait for the main loop at most; older behavior is to
## drop the overflow, never to stall the game.
const MAX_QUEUE := 4096


## Receives the game log stream and holds it in a bounded queue for the main
## loop. The callbacks arrive from any thread, so every queue access holds the
## mutex and does only the append or the swap inside it.
class CaptureLogger extends Logger:
	var _mutex := Mutex.new()
	var _pending: Array = []
	var _next_seq := 1
	var _dropped := 0
	## The thread `_ready` ran on. Only a script runtime error seen on this
	## thread flushes at once; every other entry keeps the queue path.
	var _main_thread := 0

	## Records which thread is the main loop thread. Called once from
	## `_ready`, which always runs there.
	func set_main_thread(id: int) -> void:
		_main_thread = id


	## Whether this call runs on the main loop thread.
	func is_main_thread() -> bool:
		return OS.get_thread_caller_id() == _main_thread

	## Plain stdout messages (`print`) arrive with `error` false and stderr
	## ones (`printerr`) with it true.
	func _log_message(message: String, error: bool) -> void:
		_append(["message", message, error, "", "", 0, "", "", false, 0, []])

	## A script runtime error on the main thread flushes at once: the entries
	## already queued go first, then this one, each with its sequence number
	## assigned under the mutex, so the order stays gap-free. Anything else
	## keeps the queue path the main loop drains.
	func _log_error(function: String, file: String, line: int, code: String, rationale: String, editor_notify: bool, error_type: int, script_backtraces: Array) -> void:
		var traces: Array = []
		if script_backtraces != null:
			for entry in script_backtraces:
				if typeof(entry) == TYPE_DICTIONARY:
					traces.append(entry)
				else:
					traces.append(str(entry))
		if error_type == 2 and is_main_thread():
			for entry in take_and_append(["error", "", false, function, file, line, code, rationale, editor_notify, error_type, traces]):
				EngineDebugger.send_message(PREFIX + ":log", entry)
			return
		_append(["error", "", false, function, file, line, code, rationale, editor_notify, error_type, traces])

	## Appends one entry with the next sequence number, timestamp and drop
	## count, or counts a drop when the queue is full. Never blocks.
	func _append(fields: Array) -> void:
		_mutex.lock()
		if _pending.size() >= MAX_QUEUE:
			_dropped += 1
			_mutex.unlock()
			return
		var seq := _next_seq
		_next_seq += 1
		_pending.append([seq, Time.get_ticks_msec(), _dropped] + fields)
		_mutex.unlock()

	## Takes the queued entries for the main loop, leaving an empty queue.
	func take_pending() -> Array:
		_mutex.lock()
		var entries := _pending
		_pending = []
		_mutex.unlock()
		return entries


	## Moves the queued entries plus one more into the caller's hands, with
	## the next sequence numbers assigned under the same lock, so the flushed
	## batch keeps the numbers monotonic and unique. The main loop sends them.
	func take_and_append(fields: Array) -> Array:
		_mutex.lock()
		var seq := _next_seq
		_next_seq += 1
		var entries := _pending
		_pending = []
		entries.append([seq, Time.get_ticks_msec(), _dropped] + fields)
		_mutex.unlock()
		return entries


var _logger: CaptureLogger = null
var _capture_queue: Array = []
var _capturing := false

## Injected input requests waiting for the main loop, each `[kind, payload]`
## with `kind` `"key"` or `"click"`. One runs at a time; a hold keeps the
## injector busy until its release, so a later request waits behind it.
var _inject_queue: Array = []
var _injecting := false

## Extra milliseconds the key reply needs beyond the hold itself, so a wait
## exactly as long as the hold still has room to answer.
const KEY_REPLY_MARGIN_MS := 1000


func _ready() -> void:
	if not EngineDebugger.is_active():
		print("HELPER active=false")
		return
	print("HELPER active=true")
	# Injection stays usable while the tree is paused: the queue, the hold
	# countdown and the capture all keep running on the main loop.
	process_mode = Node.PROCESS_MODE_ALWAYS
	_logger = CaptureLogger.new()
	_logger.set_main_thread(OS.get_thread_caller_id())
	OS.add_logger(_logger)
	EngineDebugger.register_message_capture(PREFIX, _on_capture_message)


func _process(_delta: float) -> void:
	if _logger == null:
		return
	for entry in _logger.take_pending():
		EngineDebugger.send_message(PREFIX + ":log", entry)
	if not _capturing and not _capture_queue.is_empty():
		_capturing = true
		_answer_capture(_capture_queue.pop_front())
	if not _injecting and not _inject_queue.is_empty():
		_injecting = true
		var next: Array = _inject_queue.pop_front()
		if str(next[0]) == "key":
			_answer_key(next[1])
		else:
			_answer_click(next[1])


## Handles an editor request. The prefix matched the registration, so only
## the short name arrives: anything else is not ours. A mistyped injection
## is answered with an error at once, so the caller learns the refusal
## instead of waiting out its deadline.
func _on_capture_message(message: String, data: Array) -> bool:
	if message == "capture":
		if data.size() < 2 or typeof(data[0]) != TYPE_STRING or (typeof(data[1]) != TYPE_INT and typeof(data[1]) != TYPE_FLOAT):
			EngineDebugger.send_message(PREFIX + ":capture_reply", [str(data[0]) if data.size() > 0 else "0", false, 0, 0, 0, PackedByteArray(), 0, "invalid capture request"])
			return true
		_capture_queue.append([str(data[0]), int(data[1])])
		return true
	if message == "key":
		if data.size() < 5 or typeof(data[0]) != TYPE_STRING:
			EngineDebugger.send_message(PREFIX + ":key_reply", [_key_id(data), false, 0, false, 0, false, "invalid key request"])
			return true
		_inject_queue.append(["key", [str(data[0]), int(data[1]), bool(data[2]), int(data[3]), int(data[4])]])
		return true
	if message == "click":
		if data.size() < 3 or typeof(data[0]) != TYPE_STRING:
			EngineDebugger.send_message(PREFIX + ":click_reply", [_click_id(data), false, 0, 0, "left", "invalid click request"])
			return true
		_inject_queue.append(["click", [str(data[0]), int(data[1]), int(data[2])]])
		return true
	return false


## The request id a mistyped injection carried, or "0" when even that is
## missing.
func _key_id(data: Array) -> String:
	return str(data[0]) if data.size() > 0 and typeof(data[0]) == TYPE_STRING else "0"


## The request id a mistyped click carried, or "0" when even that is missing.
func _click_id(data: Array) -> String:
	return str(data[0]) if data.size() > 0 and typeof(data[0]) == TYPE_STRING else "0"


## Answers one capture request on the main loop: a headless renderer answers
## at once (its viewport never produces a frame), otherwise two frames settle
## the viewport, then the image is read, encoded, and halved until it fits.
func _answer_capture(request: Array) -> void:
	var id: String = request[0]
	var max_bytes: int = request[1]
	if DisplayServer.get_name() == "headless":
		_reply_capture(id, "no viewport image (headless renderer)")
		return
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var texture := get_viewport().get_texture()
	if texture == null:
		_reply_capture(id, "no viewport texture (headless or dummy renderer)")
		return
	var image := texture.get_image()
	if image == null:
		_reply_capture(id, "no viewport image (headless or dummy renderer)")
		return
	var width := image.get_width()
	var height := image.get_height()
	if width <= 0 or height <= 0:
		_reply_capture(id, "empty viewport image")
		return
	var scale := 0
	var png := image.save_png_to_buffer()
	while png.size() > max_bytes and (width > 1 or height > 1):
		width = maxi(1, width / 2)
		height = maxi(1, height / 2)
		image.resize(width, height, Image.INTERPOLATE_BILINEAR)
		scale += 1
		png = image.save_png_to_buffer()
	if png.size() > max_bytes:
		_reply_capture(id, "encoded frame never fits max_png_bytes")
		return
	EngineDebugger.send_message(PREFIX + ":capture_reply", [id, true, image.get_width(), image.get_height(), scale, png, png.size(), ""])
	_capturing = false


## Replies `ok` false for a capture that produced no image. The `_capturing`
## flag resets here too, so a failure never blocks the queued requests.
func _reply_capture(id: String, error: String) -> void:
	EngineDebugger.send_message(PREFIX + ":capture_reply", [id, false, 0, 0, 0, PackedByteArray(), 0, error])
	_capturing = false


## Presses or releases one physical key on the main loop. A positive hold is
## timed in physics frames first: when the caller's wait cannot cover the
## hold at this game's own physics rate the helper refuses before injecting
## anything, naming the minimum wait. Otherwise the press goes out at once
## and a hold releases itself after its frames; the release of a hold is
## driven here in the game, so it still happens when the caller already gave
## up waiting, as long as the game keeps running.
func _answer_key(request: Array) -> void:
	var id: String = request[0]
	var keycode: int = request[1]
	var pressed: bool = request[2]
	var hold_frames: int = request[3]
	var timeout_ms: int = request[4]
	if hold_frames > 0 and pressed:
		var tps := Engine.physics_ticks_per_second
		var need := int(ceil(hold_frames * 1000.0 / maxf(1.0, float(tps)))) + KEY_REPLY_MARGIN_MS
		if timeout_ms < need:
			_reply_key(id, "game_key refused hold_frames %d with timeout_ms %d: need at least %d ms at %d physics frames per second" % [hold_frames, timeout_ms, need, tps])
			return
	var event := InputEventKey.new()
	event.physical_keycode = keycode
	event.pressed = pressed
	Input.parse_input_event(event)
	if not (hold_frames > 0 and pressed):
		EngineDebugger.send_message(PREFIX + ":key_reply", [id, true, keycode, pressed, 0, not pressed, ""])
		_injecting = false
		return
	for _i in hold_frames:
		await get_tree().physics_frame
	var release := InputEventKey.new()
	release.physical_keycode = keycode
	release.pressed = false
	Input.parse_input_event(release)
	EngineDebugger.send_message(PREFIX + ":key_reply", [id, true, keycode, true, hold_frames, true, ""])
	_injecting = false


## Clicks the left button at one viewport position: the press, one frame,
## then the release. The pair is what a Button answers.
func _answer_click(request: Array) -> void:
	var id: String = request[0]
	var point := Vector2(request[1], request[2])
	var press := InputEventMouseButton.new()
	press.button_index = MOUSE_BUTTON_LEFT
	press.position = point
	press.global_position = point
	press.pressed = true
	Input.parse_input_event(press)
	await get_tree().process_frame
	var release := InputEventMouseButton.new()
	release.button_index = MOUSE_BUTTON_LEFT
	release.position = point
	release.global_position = point
	release.pressed = false
	Input.parse_input_event(release)
	EngineDebugger.send_message(PREFIX + ":click_reply", [id, true, request[1], request[2], "left", ""])
	_injecting = false


## Replies `ok` false for an injection the helper refused. The injector flag
## resets here too, so a refusal never blocks the queued requests.
func _reply_key(id: String, error: String) -> void:
	EngineDebugger.send_message(PREFIX + ":key_reply", [id, false, 0, false, 0, false, error])
	_injecting = false


func _exit_tree() -> void:
	if _logger != null:
		OS.remove_logger(_logger)
		_logger = null
