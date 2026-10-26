@tool
extends EditorDebuggerPlugin
## Editor side of the in-game capture helper.
##
## Captures the `godot_pipeline:*` traffic the game helper sends over the
## debugger channel. `_has_capture` matches the prefix only, and `_capture`
## returns true for it, which removes the editor's Unknown message warning.
## Nothing here writes a file.
##
## Log entries accumulate in a bounded ring buffer per run: a new session
## (a new game process) clears the buffer and restarts gap detection, while
## the drop totals keep counting. Each entry is a Dictionary with `seq` (the
## helper's per-process sequence id, starting at 1), `msec` (the helper's
## timestamp), `dropped` (the helper's drop count when the entry was queued),
## `kind` (`"message"` or `"error"`), and the payload fields the helper
## documents: a message entry carries `text` and `error`; an error entry
## carries all eight engine fields (`function`, `file`, `line`, `code`,
## `rationale`, `editor_notify`, `error_type`, `backtraces`). Dropped entries
## are detected three ways: a sequence gap between two received entries counts
## the missing ids, the helper's own drop counter arrives inside every entry,
## and entries evicted from the full ring count too.
##
## Capture replies are stored by request id for polling: `request_capture`
## sends the request to the active session and returns its id at once without
## blocking the editor thread, and `take_capture_reply` removes and returns
## the stored reply once it arrives. A reply is `{id, ok, width, height,
## scale, png, size, error}` with the PNG bytes as a `PackedByteArray`; an
## empty id or an empty Dictionary means no live session or no reply yet.

const PREFIX := "godot_pipeline"

## How many log entries the ring buffer holds at most; older entries leave
## first and count as evicted.
const MAX_ENTRIES := 4096

var _entries: Array = []
var _last_seq := 0
var _received := 0
var _seq_gaps := 0
var _helper_dropped := 0
var _evicted := 0
var _capture_replies := {}
var _next_capture := 1


## A new game process starts a new run: the buffer and the gap detection
## restart, while the drop totals keep counting across runs.
func _setup_session(_session_id: int) -> void:
	_entries = []
	_last_seq = 0


## The first session with a game still attached, or null. Sessions outlive
## their game (a tab stays after its game stops), so the stored id alone
## cannot tell a live game from a gone one.
func _live_session() -> EditorDebuggerSession:
	for index in range(_sessions_count()):
		var session := get_session(index)
		if session != null and session.is_active():
			return session
	return null


## How many sessions the editor holds for this plugin.
func _sessions_count() -> int:
	return get_sessions().size()


func _has_capture(prefix: String) -> bool:
	return prefix == PREFIX


## Records one helper message and claims it, so the editor stays silent.
## Anything outside the prefix is not ours.
func _capture(message: String, data: Array, _session_id: int) -> bool:
	if message == PREFIX + ":log":
		_record_log(data)
		return true
	if message == PREFIX + ":capture_reply":
		_record_capture_reply(data)
		return true
	return false


## Records one log entry in the ring, counting sequence gaps and the
## helper's drop counter. A short or mistyped payload still counts as
## received so the totals stay honest, with empty fields.
func _record_log(data: Array) -> void:
	_received += 1
	var entry := {
		"seq": int(data[0]) if data.size() > 0 else 0,
		"msec": int(data[1]) if data.size() > 1 else 0,
		"dropped": int(data[2]) if data.size() > 2 else 0,
		"kind": str(data[3]) if data.size() > 3 else "",
		"text": str(data[4]) if data.size() > 4 else "",
		"error": bool(data[5]) if data.size() > 5 else false,
		"function": str(data[6]) if data.size() > 6 else "",
		"file": str(data[7]) if data.size() > 7 else "",
		"line": int(data[8]) if data.size() > 8 else 0,
		"code": str(data[9]) if data.size() > 9 else "",
		"rationale": str(data[10]) if data.size() > 10 else "",
		"editor_notify": bool(data[11]) if data.size() > 11 else false,
		"error_type": int(data[12]) if data.size() > 12 else 0,
		"backtraces": data[13] if data.size() > 13 else [],
	}
	var seq: int = entry["seq"]
	if seq > 0 and _last_seq > 0 and seq <= _last_seq:
		# A new game process on a reused session: sequence ids restart at 1,
		# so this entry opens a new run. The buffer restarts with it, while
		# the drop totals keep counting across runs.
		_entries = []
		_last_seq = 0
	if seq > 0:
		if _last_seq > 0 and seq > _last_seq + 1:
			_seq_gaps += seq - _last_seq - 1
		if seq > _last_seq:
			_last_seq = seq
	_helper_dropped = int(entry["dropped"])
	_entries.append(entry)
	if _entries.size() > MAX_ENTRIES:
		_entries.pop_front()
		_evicted += 1


## Stores one capture reply by its request id for polling.
func _record_capture_reply(data: Array) -> void:
	_capture_replies[str(data[0]) if data.size() > 0 else "0"] = {
		"id": str(data[0]) if data.size() > 0 else "0",
		"ok": bool(data[1]) if data.size() > 1 else false,
		"width": int(data[2]) if data.size() > 2 else 0,
		"height": int(data[3]) if data.size() > 3 else 0,
		"scale": int(data[4]) if data.size() > 4 else 0,
		"png": data[5] if data.size() > 5 else PackedByteArray(),
		"size": int(data[6]) if data.size() > 6 else 0,
		"error": str(data[7]) if data.size() > 7 else "",
	}


## The log entries with a sequence number greater than `since`, oldest first.
func entries_since(since: int) -> Array:
	var kept: Array = []
	for entry in _entries:
		if int(entry["seq"]) > since:
			kept.append(entry)
	return kept


## Every dropped entry counted three ways: sequence gaps, the helper's own
## counter, and ring evictions.
func dropped_count() -> Dictionary:
	return {"gaps": _seq_gaps, "helper": _helper_dropped, "evicted": _evicted}


## The state of the session for a later command: whether a game is still
## attached, and the run totals.
func session_state() -> Dictionary:
	var session := _live_session()
	return {
		"active": session != null,
		"received": _received,
		"buffered": _entries.size(),
		"next_since": _last_seq,
	}


## Sends a capture request to the attached game and returns its id at once;
## the caller polls `take_capture_reply`. Returns an empty id when no game
## is attached, which is the no-game failure.
func request_capture(max_png_bytes: int) -> String:
	var session := _live_session()
	if session == null:
		return ""
	var id := "cap_%d" % _next_capture
	_next_capture += 1
	session.send_message(PREFIX + ":capture", [id, max_png_bytes])
	return id


## Removes and returns the stored reply for `id`, or an empty Dictionary
## when no reply arrived yet.
func take_capture_reply(id: String) -> Dictionary:
	if not _capture_replies.has(id):
		return {}
	var reply: Dictionary = _capture_replies[id]
	_capture_replies.erase(id)
	return reply
