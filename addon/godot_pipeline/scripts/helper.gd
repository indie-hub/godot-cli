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
## loop in `_process`, where the entries are sent from. Appending never blocks:
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

	## Plain stdout messages (`print`) arrive with `error` false and stderr
	## ones (`printerr`) with it true.
	func _log_message(message: String, error: bool) -> void:
		_append(["message", message, error, "", "", 0, "", "", false, 0, []])

	## All eight engine fields arrive intact; only their packing into the
	## payload happens here.
	func _log_error(function: String, file: String, line: int, code: String, rationale: String, editor_notify: bool, error_type: int, script_backtraces: Array) -> void:
		var traces: Array = []
		if script_backtraces != null:
			for entry in script_backtraces:
				if typeof(entry) == TYPE_DICTIONARY:
					traces.append(entry)
				else:
					traces.append(str(entry))
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


var _logger: CaptureLogger = null
var _capture_queue: Array = []
var _capturing := false


func _ready() -> void:
	if not EngineDebugger.is_active():
		print("HELPER active=false")
		return
	print("HELPER active=true")
	_logger = CaptureLogger.new()
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


## Handles an editor request. The prefix matched the registration, so only
## the short name arrives: anything else is not ours.
func _on_capture_message(message: String, data: Array) -> bool:
	if message != "capture":
		return false
	if data.size() < 2 or typeof(data[0]) != TYPE_STRING or (typeof(data[1]) != TYPE_INT and typeof(data[1]) != TYPE_FLOAT):
		EngineDebugger.send_message(PREFIX + ":capture_reply", [str(data[0]) if data.size() > 0 else "0", false, 0, 0, 0, PackedByteArray(), 0, "invalid capture request"])
		return true
	_capture_queue.append([str(data[0]), int(data[1])])
	return true


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


func _exit_tree() -> void:
	if _logger != null:
		OS.remove_logger(_logger)
		_logger = null
