@tool
extends RefCounted

## Shared staging format for the input commands: validation of action names,
## deadzones and wire event objects, plus encoding of stored InputEvent
## objects back to wire JSON. Used by the four input commands.
## The store itself lives as an editor meta value and never holds JSON.

const STAGING_KEY := "godot_pipeline_input_staging"
const MAX_EVENTS := 32
const MOUSE_BUTTON_MAX := 9
const JOY_BUTTON_MAX := 127
const JOY_AXIS_MAX := 9
## A keycode is the low KeyModifierMask.CODE_MASK bits of the engine key enum;
## a larger value carries modifier masks and would be stored as another key.
const KEY_CODE_MAX := 8388607
## Device numbers are stored in a 32-bit engine integer.
const DEVICE_MAX := 2147483647
## The engine's default deadzone for an action (core/input/input_map.h DEFAULT_DEADZONE).
const DEFAULT_DEADZONE := 0.2

## Prints a caller-supplied string escaped and printable for refusal messages:
## control characters below 0x20 and DEL become \uXXXX so the reply stays valid
## JSON; every other character passes through for the reply writer, which
## already escapes quotes and backslashes and passes UTF-8 through.
static func escape_text(text: String) -> String:
	var out := ""
	for index in text.length():
		var code := text.unicode_at(index)
		if code < 32 or code == 127:
			out += "\\u%04x" % code
		else:
			out += text.substr(index, 1)
	return out


## Returns a copy of the staging store. Missing or corrupt values read as empty.
static func read_store(plugin: EditorPlugin) -> Dictionary:
	var editor := plugin.get_editor_interface()
	if not editor.has_meta(STAGING_KEY):
		return {}
	var stored: Variant = editor.get_meta(STAGING_KEY)
	if typeof(stored) != TYPE_DICTIONARY:
		return {}
	return (stored as Dictionary).duplicate(true)


## Replaces the staging store.
static func write_store(plugin: EditorPlugin, store: Dictionary) -> void:
	plugin.get_editor_interface().set_meta(STAGING_KEY, store)


## Action names hold no path or setting syntax. U+FFFD is refused outright:
## the request parser replaces an invalid or NUL character with U+FFFD before
## validation sees it, so accepting it would stage a different name than asked.
static func is_valid_action_name(action_name: Variant) -> bool:
	if typeof(action_name) != TYPE_STRING:
		return false
	var text: String = action_name
	if text.is_empty():
		return false
	for index in text.length():
		var code := text.unicode_at(index)
		if code < 32 or code == 127 or code == 0xFFFD:
			return false
		var ch := text.substr(index, 1)
		if ch == "/" or ch == ":" or ch == "=" or ch == "\"" or ch == "\\":
			return false
	return true


static func action_error(command: String, action_name: Variant) -> String:
	return "%s refused action %s: action must be a non-empty string without '/', ':', '=', '\"', backslash, newline, control characters or U+FFFD. The request parser replaces an invalid or NUL character with U+FFFD, which this command refuses" % [command, escape_text(str(action_name))]


## Deadzone is a number in 0.0 to 1.0; null means the default.
static func validate_deadzone(value: Variant, command: String) -> Dictionary:
	if value == null:
		return {"value": DEFAULT_DEADZONE}
	if typeof(value) != TYPE_INT and typeof(value) != TYPE_FLOAT:
		return {"error": "%s refused deadzone %s: deadzone must be a number from 0.0 to 1.0" % [command, escape_text(str(value))]}
	var number := float(value)
	if number < 0.0 or number > 1.0:
		return {"error": "%s refused deadzone %s: deadzone must be a number from 0.0 to 1.0" % [command, escape_text(str(value))]}
	return {"value": number}


## Decodes a whole events array into InputEvent objects.
static func decode_events(events: Variant, command: String) -> Dictionary:
	if typeof(events) != TYPE_ARRAY:
		return {"error": "%s refused events: events must be a non-empty array of at most 32 event objects" % command}
	var items: Array = events
	if items.is_empty() or items.size() > MAX_EVENTS:
		return {"error": "%s refused events: events must be a non-empty array of at most 32 event objects" % command}
	var decoded: Array = []
	for index in items.size():
		var one := decode_event(index, items[index], command)
		if one.has("error"):
			return one
		decoded.append(one["value"])
	return {"value": decoded}


static func _unknown_field(command: String, index: int, field: String, event_type: String) -> Dictionary:
	return {"error": "%s refused event %d: unknown field `%s` for type `%s`" % [command, index, escape_text(field), event_type]}


static func _missing_field(command: String, index: int, event_type: String, field: String) -> Dictionary:
	return {"error": "%s refused event %d: type `%s` requires field `%s`" % [command, index, event_type, field]}


# Godot JSON parses every number as float, so a JSON integer arrives as a
# whole float; fractional floats are refused where an integer is required.
static func _check_int(value: Variant) -> bool:
	if typeof(value) == TYPE_INT:
		return true
	if typeof(value) == TYPE_FLOAT:
		return float(value) == floorf(float(value))
	return false


# Compares as a float so a value too large for an int is refused instead of
# being narrowed into the range.
static func _check_at_most(value: Variant, maximum: int) -> bool:
	if not _check_int(value):
		return false
	return float(value) <= float(maximum)


static func _check_bool(value: Variant) -> bool:
	return typeof(value) == TYPE_BOOL


static func _check_modifiers(item: Dictionary, command: String, index: int) -> String:
	for field in ["shift", "ctrl", "alt", "meta"]:
		if item.has(field) and not _check_bool(item[field]):
			return "%s refused event %d: `%s` must be a boolean" % [command, index, field]
	return ""


## Decodes one wire event object.
static func decode_event(index: int, item: Variant, command: String) -> Dictionary:
	if typeof(item) != TYPE_DICTIONARY:
		return {"error": "%s refused event %d: event must be an object with a string field `type`" % [command, index]}
	var entry: Dictionary = item
	if not entry.has("type") or typeof(entry["type"]) != TYPE_STRING:
		return {"error": "%s refused event %d: event must be an object with a string field `type`" % [command, index]}
	var event_type: String = entry["type"]
	if event_type == "key":
		return _decode_key(index, entry, command)
	if event_type == "mouse_button":
		return _decode_mouse_button(index, entry, command)
	if event_type == "joypad_button":
		return _decode_joy_button(index, entry, command)
	if event_type == "joypad_motion":
		return _decode_joy_motion(index, entry, command)
	return {"error": "%s refused event %d: unknown event type `%s`; expected `key`, `mouse_button`, `joypad_button` or `joypad_motion`" % [command, index, escape_text(event_type)]}


static func _decode_key(index: int, entry: Dictionary, command: String) -> Dictionary:
	for key in entry.keys():
		if not ["type", "physical_keycode", "keycode", "key_label", "shift", "ctrl", "alt", "meta"].has(key):
			return _unknown_field(command, index, str(key), "key")
	var physical := 0
	var code := 0
	var label := 0
	if entry.has("physical_keycode"):
		if not _check_int(entry["physical_keycode"]) or int(entry["physical_keycode"]) < 0:
			return {"error": "%s refused event %d: `physical_keycode` must be an integer of at least 0" % [command, index]}
		if not _check_at_most(entry["physical_keycode"], KEY_CODE_MAX):
			return {"error": "%s refused event %d: `physical_keycode` must be an integer from 0 to %d" % [command, index, KEY_CODE_MAX]}
		physical = int(entry["physical_keycode"])
	if entry.has("keycode"):
		if not _check_int(entry["keycode"]) or int(entry["keycode"]) < 0:
			return {"error": "%s refused event %d: `keycode` must be an integer of at least 0" % [command, index]}
		if not _check_at_most(entry["keycode"], KEY_CODE_MAX):
			return {"error": "%s refused event %d: `keycode` must be an integer from 0 to %d" % [command, index, KEY_CODE_MAX]}
		code = int(entry["keycode"])
	if entry.has("key_label"):
		if not _check_int(entry["key_label"]) or int(entry["key_label"]) < 0:
			return {"error": "%s refused event %d: `key_label` must be an integer of at least 0" % [command, index]}
		if not _check_at_most(entry["key_label"], KEY_CODE_MAX):
			return {"error": "%s refused event %d: `key_label` must be an integer from 0 to %d" % [command, index, KEY_CODE_MAX]}
		label = int(entry["key_label"])
	if physical == 0 and code == 0:
		return {"error": "%s refused event %d: `key` requires at least one of `physical_keycode` or `keycode` to be non-zero" % [command, index]}
	var modifier_error := _check_modifiers(entry, command, index)
	if modifier_error != "":
		return {"error": modifier_error}
	var event := InputEventKey.new()
	event.physical_keycode = physical
	event.keycode = code
	event.key_label = label
	event.shift_pressed = bool(entry.get("shift", false))
	event.ctrl_pressed = bool(entry.get("ctrl", false))
	event.alt_pressed = bool(entry.get("alt", false))
	event.meta_pressed = bool(entry.get("meta", false))
	return {"value": event}


static func _decode_mouse_button(index: int, entry: Dictionary, command: String) -> Dictionary:
	for key in entry.keys():
		if not ["type", "button_index", "shift", "ctrl", "alt", "meta"].has(key):
			return _unknown_field(command, index, str(key), "mouse_button")
	if not entry.has("button_index"):
		return _missing_field(command, index, "mouse_button", "button_index")
	if not _check_int(entry["button_index"]):
		return {"error": "%s refused event %d: `button_index` must be an integer from 1 to %d" % [command, index, MOUSE_BUTTON_MAX]}
	var button := int(entry["button_index"])
	if button < 1 or button > MOUSE_BUTTON_MAX:
		return {"error": "%s refused event %d: `button_index` must be an integer from 1 to %d" % [command, index, MOUSE_BUTTON_MAX]}
	var modifier_error := _check_modifiers(entry, command, index)
	if modifier_error != "":
		return {"error": modifier_error}
	var event := InputEventMouseButton.new()
	event.button_index = button
	event.shift_pressed = bool(entry.get("shift", false))
	event.ctrl_pressed = bool(entry.get("ctrl", false))
	event.alt_pressed = bool(entry.get("alt", false))
	event.meta_pressed = bool(entry.get("meta", false))
	return {"value": event}


static func _decode_joy_button(index: int, entry: Dictionary, command: String) -> Dictionary:
	for key in entry.keys():
		if not ["type", "button_index", "device"].has(key):
			return _unknown_field(command, index, str(key), "joypad_button")
	if not entry.has("button_index"):
		return _missing_field(command, index, "joypad_button", "button_index")
	if not _check_int(entry["button_index"]):
		return {"error": "%s refused event %d: `button_index` must be an integer from 0 to %d" % [command, index, JOY_BUTTON_MAX]}
	var button := int(entry["button_index"])
	if button < 0 or button > JOY_BUTTON_MAX:
		return {"error": "%s refused event %d: `button_index` must be an integer from 0 to %d" % [command, index, JOY_BUTTON_MAX]}
	var device := -1
	if entry.has("device"):
		if not _check_int(entry["device"]) or int(entry["device"]) < -1:
			return {"error": "%s refused event %d: `device` must be an integer of at least -1" % [command, index]}
		if not _check_at_most(entry["device"], DEVICE_MAX):
			return {"error": "%s refused event %d: `device` must be an integer from -1 to %d" % [command, index, DEVICE_MAX]}
		device = int(entry["device"])
	var event := InputEventJoypadButton.new()
	event.button_index = button
	event.device = device
	return {"value": event}


static func _decode_joy_motion(index: int, entry: Dictionary, command: String) -> Dictionary:
	for key in entry.keys():
		if not ["type", "axis", "axis_value", "device"].has(key):
			return _unknown_field(command, index, str(key), "joypad_motion")
	if not entry.has("axis"):
		return _missing_field(command, index, "joypad_motion", "axis")
	if not entry.has("axis_value"):
		return _missing_field(command, index, "joypad_motion", "axis_value")
	if not _check_int(entry["axis"]):
		return {"error": "%s refused event %d: `axis` must be an integer from 0 to %d" % [command, index, JOY_AXIS_MAX]}
	var axis := int(entry["axis"])
	if axis < 0 or axis > JOY_AXIS_MAX:
		return {"error": "%s refused event %d: `axis` must be an integer from 0 to %d" % [command, index, JOY_AXIS_MAX]}
	var raw: Variant = entry["axis_value"]
	if typeof(raw) != TYPE_INT and typeof(raw) != TYPE_FLOAT:
		return {"error": "%s refused event %d: `axis_value` must be -1.0 or 1.0" % [command, index]}
	var strength := float(raw)
	if strength != -1.0 and strength != 1.0:
		return {"error": "%s refused event %d: `axis_value` must be -1.0 or 1.0" % [command, index]}
	var device := -1
	if entry.has("device"):
		if not _check_int(entry["device"]) or int(entry["device"]) < -1:
			return {"error": "%s refused event %d: `device` must be an integer of at least -1" % [command, index]}
		if not _check_at_most(entry["device"], DEVICE_MAX):
			return {"error": "%s refused event %d: `device` must be an integer from -1 to %d" % [command, index, DEVICE_MAX]}
		device = int(entry["device"])
	var event := InputEventJoypadMotion.new()
	event.axis = axis
	event.axis_value = strength
	event.device = device
	return {"value": event}


## Encodes one stored event object to wire JSON. Foreign objects become `other`.
static func encode_event(event: Variant) -> Dictionary:
	if event is InputEventKey:
		var key_event: InputEventKey = event
		return {
			"type": "key",
			"physical_keycode": int(key_event.physical_keycode),
			"keycode": int(key_event.keycode),
			"key_label": int(key_event.key_label),
			"shift": key_event.shift_pressed,
			"ctrl": key_event.ctrl_pressed,
			"alt": key_event.alt_pressed,
			"meta": key_event.meta_pressed,
			"device": key_event.device,
		}
	if event is InputEventMouseButton:
		var mouse_event: InputEventMouseButton = event
		return {
			"type": "mouse_button",
			"button_index": int(mouse_event.button_index),
			"shift": mouse_event.shift_pressed,
			"ctrl": mouse_event.ctrl_pressed,
			"alt": mouse_event.alt_pressed,
			"meta": mouse_event.meta_pressed,
			"device": mouse_event.device,
		}
	if event is InputEventJoypadButton:
		var joy_button: InputEventJoypadButton = event
		return {
			"type": "joypad_button",
			"button_index": int(joy_button.button_index),
			"device": joy_button.device,
		}
	if event is InputEventJoypadMotion:
		var joy_motion: InputEventJoypadMotion = event
		return {
			"type": "joypad_motion",
			"axis": int(joy_motion.axis),
			"axis_value": float(joy_motion.axis_value),
			"device": joy_motion.device,
		}
	if event is Object:
		return {"type": "other", "class": (event as Object).get_class()}
	return {"type": "other", "class": type_string(typeof(event))}


## Encodes an array of stored event objects.
static func encode_events(events: Array) -> Array:
	var out: Array = []
	for event in events:
		out.append(encode_event(event))
	return out


## Encodes a staged entry for replies.
static func encode_staged(deadzone: float, events: Array, remove: bool) -> Dictionary:
	return {"deadzone": float(deadzone), "events": encode_events(events), "remove": remove}
