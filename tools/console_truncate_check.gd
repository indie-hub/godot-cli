extends SceneTree

## Deterministic regression check for the console message byte cap: a cut that
## lands inside a multibyte UTF-8 character must back off to the character
## boundary, so the stored text stays valid UTF-8 of at most 4096 bytes.
## Run from the repository root with the 4.7.2 binary:
## Godot --headless --script tools/console_truncate_check.gd
## Exits 0 when every case passes, 1 otherwise. Cases A and C fail on the
## naive cut (which decodes the split character as U+FFFD and overshoots the
## cap); case B pins the exact-boundary cut, which the naive code also keeps.
func _init() -> void:
	var handler_path: String = get_script().resource_path.get_base_dir().path_join("../addon/godot_pipeline/commands/console.gd").simplify_path()
	var console_script: GDScript = load(handler_path)
	if console_script == null:
		print("FAIL: cannot load ", handler_path)
		quit(1)
		return
	var capture = console_script.Capture
	var failed := 0
	if not _check(capture, "A split euro", "B".repeat(4095) + "€", "B".repeat(4095), true):
		failed += 1
	if not _check(capture, "B exact boundary", "B".repeat(4093) + "€" + "X", "B".repeat(4093) + "€", true):
		failed += 1
	if not _check(capture, "C split emoji", "B".repeat(4094) + "😀", "B".repeat(4094), true):
		failed += 1
	if not _check(capture, "D short text", "ok", "ok", false):
		failed += 1
	if failed == 0:
		print("PASS: 4/4 console truncate cases")
		quit(0)
	else:
		print("FAIL: %d/4 console truncate cases failed" % failed)
		quit(1)


## Appends `text` through the handler's own capture, then compares the stored
## entry against the expected text and flag. Each case clears the buffer, so
## cases never see each other.
func _check(capture: GDScript, name: String, text: String, expected_text: String, expected_truncated: bool) -> bool:
	capture._append("error", text)
	var state: Dictionary = capture.read_and_maybe_clear(true)
	var messages: Array = state["messages"]
	if messages.size() != 1:
		print("FAIL %s: stored %d messages, want 1" % [name, messages.size()])
		return false
	var entry: Dictionary = messages[0]
	var stored: String = entry["text"]
	var truncated: bool = entry["truncated"]
	var stored_bytes := stored.to_utf8_buffer().size()
	if stored != expected_text or truncated != expected_truncated or stored_bytes > 4096:
		print("FAIL %s: bytes=%d truncated=%s text-mismatch=%s" % [name, stored_bytes, truncated, stored != expected_text])
		return false
	print("PASS %s: bytes=%d truncated=%s" % [name, stored_bytes, truncated])
	return true
