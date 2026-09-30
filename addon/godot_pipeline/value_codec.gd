@tool
extends RefCounted
## Value conversion for the Godot Pipeline editor plugin.
##
## Pure, static conversions between a JSON-decoded request value and the Godot
## Variant a property expects (`coerce_value`), and between a Variant and the
## JSON-representable shape `set-property` accepts (`value_to_json`), so an
## inspected value can be fed back unchanged. The plugin preloads this script
## and calls the public functions; everything else here is a helper only those
## functions use.

## Converts a Variant into a JSON-representable value whose shape matches the
## `set-property` input rules for the types it supports, so an inspected value
## can be fed back through `set-property` unchanged. Non-finite floats become
## the strings "inf", "-inf", and "nan", keeping the reply valid JSON. Returns
## null for types `set-property` cannot address (Object/Resource references,
## Dictionary, untyped Array, Callable, Signal, RID, ...); the caller reports
## those as `supported: false`.
static func value_to_json(value: Variant) -> Variant:
	match typeof(value):
		TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return value
		TYPE_FLOAT:
			return _float_to_json(value)
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_VECTOR2:
			return [_float_to_json(value.x), _float_to_json(value.y)]
		TYPE_VECTOR3:
			return [_float_to_json(value.x), _float_to_json(value.y), _float_to_json(value.z)]
		TYPE_VECTOR2I:
			return [value.x, value.y]
		TYPE_VECTOR3I:
			return [value.x, value.y, value.z]
		TYPE_VECTOR4:
			return [_float_to_json(value.x), _float_to_json(value.y), _float_to_json(value.z), _float_to_json(value.w)]
		TYPE_VECTOR4I:
			return [value.x, value.y, value.z, value.w]
		TYPE_RECT2:
			return [
				_float_to_json(value.position.x),
				_float_to_json(value.position.y),
				_float_to_json(value.size.x),
				_float_to_json(value.size.y),
			]
		TYPE_RECT2I:
			return [value.position.x, value.position.y, value.size.x, value.size.y]
		TYPE_TRANSFORM2D:
			return [
				[_float_to_json(value.x.x), _float_to_json(value.x.y)],
				[_float_to_json(value.y.x), _float_to_json(value.y.y)],
				[_float_to_json(value.origin.x), _float_to_json(value.origin.y)],
			]
		TYPE_TRANSFORM3D:
			var basis: Basis = value.basis
			return [
				[_float_to_json(basis.x.x), _float_to_json(basis.x.y), _float_to_json(basis.x.z)],
				[_float_to_json(basis.y.x), _float_to_json(basis.y.y), _float_to_json(basis.y.z)],
				[_float_to_json(basis.z.x), _float_to_json(basis.z.y), _float_to_json(basis.z.z)],
				[_float_to_json(value.origin.x), _float_to_json(value.origin.y), _float_to_json(value.origin.z)],
			]
		TYPE_COLOR:
			return [_float_to_json(value.r), _float_to_json(value.g), _float_to_json(value.b), _float_to_json(value.a)]
		TYPE_PACKED_BYTE_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_INT64_ARRAY:
			var ints: Array = []
			for element in value:
				ints.append(element)
			return ints
		TYPE_PACKED_FLOAT32_ARRAY, TYPE_PACKED_FLOAT64_ARRAY:
			var floats: Array = []
			for element in value:
				floats.append(_float_to_json(element))
			return floats
		TYPE_PACKED_STRING_ARRAY:
			var strings: Array = []
			for element in value:
				strings.append(element)
			return strings
		TYPE_PACKED_VECTOR2_ARRAY:
			var vector2s: Array = []
			for element in value:
				vector2s.append([_float_to_json(element.x), _float_to_json(element.y)])
			return vector2s
		TYPE_PACKED_VECTOR3_ARRAY:
			var vector3s: Array = []
			for element in value:
				vector3s.append([_float_to_json(element.x), _float_to_json(element.y), _float_to_json(element.z)])
			return vector3s
		TYPE_PACKED_VECTOR4_ARRAY:
			var vector4s: Array = []
			for element in value:
				vector4s.append([_float_to_json(element.x), _float_to_json(element.y), _float_to_json(element.z), _float_to_json(element.w)])
			return vector4s
		TYPE_PACKED_COLOR_ARRAY:
			var colors: Array = []
			for element in value:
				colors.append([_float_to_json(element.r), _float_to_json(element.g), _float_to_json(element.b), _float_to_json(element.a)])
			return colors
		TYPE_ARRAY:
			if not value.is_typed():
				return null
			if not is_supported_array_element(value.get_typed_builtin()):
				return null
			var elements: Array = []
			for element in value:
				elements.append(value_to_json(element))
			return elements
	return null


## Converts a float into a JSON-representable number, replacing a non-finite
## value with the strings "inf", "-inf", or "nan" so the reply stays valid
## JSON regardless of how the engine's own JSON stringifier handles them.
static func _float_to_json(value: float) -> Variant:
	if not is_finite(value):
		if is_nan(value):
			return "nan"
		return "inf" if value > 0.0 else "-inf"
	return value


## Converts a JSON-decoded `value` to `property_type`. Returns
## `{"value": converted}` on success or `{"error": message}` otherwise; it
## never guesses across types (a string is never parsed as a number, and a
## number is never stringified). Godot's JSON parser yields every number as
## a float, so ints are accepted only when the float is integral.
##
## Accepted shapes: bool -> true/false; int -> integral number; float ->
## number; String/StringName/NodePath -> string; Vector2 -> [x, y];
## Vector3 -> [x, y, z]; Vector2i -> [x, y]; Vector3i -> [x, y, z];
## Vector4 -> [x, y, z, w]; Vector4i -> [x, y, z, w]; Rect2 -> [x, y, w, h]
## (position then size); Rect2i -> [x, y, w, h]; Transform2D ->
## [[xx, xy], [yx, yy], [ox, oy]] (x axis, y axis, origin);
## Transform3D -> [[bxx, bxy, bxz], [byx, byy, byz], [bzx, bzy, bzz],
## [ox, oy, oz]] (the three basis column vectors, then the origin); Color ->
## "#rrggbb[aa]", "rrggbb", a named color such as "red", or [r, g, b] /
## [r, g, b, a] in 0..1 floats. The int-vector and Rect2i components are
## signed 32-bit integers: a fractional or out-of-range component is rejected
## up front, because Godot stores it as int32 and would silently wrap.
##
## Arrays are accepted for the Packed*Array types and for typed `Array[T]`
## whose element type is one of the types above; `value` is a JSON array and
## every element is coerced by the same per-type rules (an element error names
## its index). PackedByteArray elements are integers 0..255, PackedInt32Array
## elements follow the int32 rule, and PackedInt64Array elements follow the
## int rule. Untyped `Array` properties are rejected by the caller, never
## coerced by guessing. `array_element_type` and `array_template` are only
## used for `TYPE_ARRAY`: the template supplies the typed array to fill.
static func coerce_value(value: Variant, property_type: int, array_element_type: int = TYPE_NIL, array_template: Variant = null) -> Dictionary:
	match property_type:
		TYPE_ARRAY:
			return _coerce_typed_array(value, array_element_type, array_template)
		TYPE_PACKED_BYTE_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_INT64_ARRAY, TYPE_PACKED_FLOAT32_ARRAY, TYPE_PACKED_FLOAT64_ARRAY, TYPE_PACKED_STRING_ARRAY, TYPE_PACKED_VECTOR2_ARRAY, TYPE_PACKED_VECTOR3_ARRAY, TYPE_PACKED_VECTOR4_ARRAY, TYPE_PACKED_COLOR_ARRAY:
			return _coerce_packed_array(value, property_type)
	if not is_supported_array_element(property_type):
		return {"error": "property type %s is not supported by set_property" % type_string(property_type)}
	var error_out := []
	var coerced: Variant = _coerce_element(value, property_type, TYPE_NIL, error_out)
	if error_out.is_empty():
		return {"value": coerced}
	return {"error": error_out[0]}


## Coerces one array element (or one scalar `set-property` value) of
## `element_type` into the matching Variant without allocating a result
## Dictionary per element. Returns the coerced value; on failure it stores the
## error message in `error_out[0]` (an array the caller allocates once per
## request) and returns null. `packed_type` selects the byte/int32 element
## rules for the two packed types that need them; it is TYPE_NIL otherwise.
static func _coerce_element(value: Variant, element_type: int, packed_type: int, error_out: Array) -> Variant:
	if packed_type == TYPE_PACKED_BYTE_ARRAY:
		if not _is_number(value):
			error_out.append("expected an integer")
			return null
		var byte_value := float(value)
		if byte_value != floorf(byte_value) or byte_value < 0.0 or byte_value > 255.0:
			error_out.append("expected an integer in 0..255, got %s" % str(value))
			return null
		return int(byte_value)
	if packed_type == TYPE_PACKED_INT32_ARRAY:
		if not _is_number(value):
			error_out.append("expected an integer")
			return null
		var int32_value := float(value)
		if int32_value != floorf(int32_value) or int32_value < -2147483648.0 or int32_value > 2147483647.0:
			error_out.append("expected an integer in -2147483648..2147483647, got %s" % str(value))
			return null
		return int(int32_value)
	match element_type:
		TYPE_BOOL:
			if typeof(value) == TYPE_BOOL:
				return value
			error_out.append("expected true or false")
			return null
		TYPE_INT:
			if not _is_number(value):
				error_out.append("expected an integer")
				return null
			var number := float(value)
			# 2^53: beyond this a JSON float can no longer represent every
			# integer exactly, so the caller's value may already be lost.
			if number != floorf(number) or absf(number) > 9007199254740992.0:
				error_out.append("expected an integer, got %s" % str(value))
				return null
			return int(number)
		TYPE_FLOAT:
			if not _is_number(value):
				error_out.append("expected a number")
				return null
			return float(value)
		TYPE_STRING:
			if typeof(value) != TYPE_STRING:
				error_out.append("expected a string")
				return null
			return value
		TYPE_STRING_NAME:
			if typeof(value) != TYPE_STRING:
				error_out.append("expected a string")
				return null
			return StringName(value)
		TYPE_NODE_PATH:
			if typeof(value) != TYPE_STRING:
				error_out.append("expected a node path string")
				return null
			return NodePath(value)
		TYPE_VECTOR2:
			if not _is_number_array(value, 2):
				error_out.append("expected [x, y]")
				return null
			return Vector2(value[0], value[1])
		TYPE_VECTOR3:
			if not _is_number_array(value, 3):
				error_out.append("expected [x, y, z]")
				return null
			return Vector3(value[0], value[1], value[2])
		TYPE_VECTOR2I:
			var v2i := _coerce_int_vector(value, 2, "[x, y]", error_out)
			if error_out.is_empty():
				return Vector2i(v2i[0], v2i[1])
			return null
		TYPE_VECTOR3I:
			var v3i := _coerce_int_vector(value, 3, "[x, y, z]", error_out)
			if error_out.is_empty():
				return Vector3i(v3i[0], v3i[1], v3i[2])
			return null
		TYPE_VECTOR4:
			if not _is_number_array(value, 4):
				error_out.append("expected [x, y, z, w]")
				return null
			return Vector4(value[0], value[1], value[2], value[3])
		TYPE_VECTOR4I:
			var v4i := _coerce_int_vector(value, 4, "[x, y, z, w]", error_out)
			if error_out.is_empty():
				return Vector4i(v4i[0], v4i[1], v4i[2], v4i[3])
			return null
		TYPE_RECT2:
			if not _is_number_array(value, 4):
				error_out.append("expected [x, y, w, h]")
				return null
			return Rect2(value[0], value[1], value[2], value[3])
		TYPE_RECT2I:
			var ri := _coerce_int_vector(value, 4, "[x, y, w, h]", error_out)
			if error_out.is_empty():
				return Rect2i(ri[0], ri[1], ri[2], ri[3])
			return null
		TYPE_COLOR:
			if typeof(value) == TYPE_STRING:
				# Color.from_string falls back to its default for anything that
				# is neither a valid HTML color nor a named color, so parsing with
				# two different defaults and comparing detects invalid input.
				var parsed := Color.from_string(value, Color(0, 0, 0, 0))
				if parsed != Color.from_string(value, Color(1, 1, 1, 1)):
					error_out.append("expected an HTML color (\"#rrggbb\") or a named color, got \"%s\"" % value)
					return null
				return parsed
			if _is_number_array(value, 3):
				return Color(value[0], value[1], value[2])
			if _is_number_array(value, 4):
				return Color(value[0], value[1], value[2], value[3])
			error_out.append("expected an HTML/named color string, [r, g, b], or [r, g, b, a]")
			return null
		TYPE_TRANSFORM2D:
			var m2 := _number_matrix(value, 3, 2)
			if m2.has("error"):
				error_out.append("expected [[xx, xy], [yx, yy], [ox, oy]]: %s" % m2["error"])
				return null
			var t2: Array = m2["value"]
			return Transform2D(Vector2(t2[0][0], t2[0][1]), Vector2(t2[1][0], t2[1][1]), Vector2(t2[2][0], t2[2][1]))
		TYPE_TRANSFORM3D:
			var m3 := _number_matrix(value, 4, 3)
			if m3.has("error"):
				error_out.append("expected [[bxx, bxy, bxz], [byx, byy, byz], [bzx, bzy, bzz], [ox, oy, oz]]: %s" % m3["error"])
				return null
			var t3: Array = m3["value"]
			var basis := Basis(Vector3(t3[0][0], t3[0][1], t3[0][2]), Vector3(t3[1][0], t3[1][1], t3[1][2]), Vector3(t3[2][0], t3[2][1], t3[2][2]))
			return Transform3D(basis, Vector3(t3[3][0], t3[3][1], t3[3][2]))
	return null


## Converts a JSON array of `size` numbers into an array of signed 32-bit
## integers, or reports the offending component through `error_out`. Returns
## the ints on success and an empty array on failure; the error message names
## `shape`. Godot stores these components as int32, so a fractional or
## out-of-range number would be silently truncated or wrapped.
static func _coerce_int_vector(value: Variant, size: int, shape: String, error_out: Array) -> Array:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != size:
		error_out.append("expected %s of integers in -2147483648..2147483647" % shape)
		return []
	var bad := []
	var ints: Array = []
	for element in value:
		if bad.is_empty():
			ints.append(_coerce_int32_component(element, bad))
	if not bad.is_empty():
		error_out.append("expected %s of integers in -2147483648..2147483647, got %s" % [shape, str(bad[0])])
		return []
	return ints


## Coerces a single signed 32-bit component, reporting the offending value
## through `bad_out` on failure. Returns 0 when it fails.
static func _coerce_int32_component(value: Variant, bad_out: Array) -> int:
	if not _is_number(value):
		bad_out.append(value)
		return 0
	var number := float(value)
	if number != floorf(number) or number < -2147483648.0 or number > 2147483647.0:
		bad_out.append(value)
		return 0
	return int(number)


static func _is_number(value: Variant) -> bool:
	return typeof(value) == TYPE_FLOAT or typeof(value) == TYPE_INT


## Returns the int values a PROPERTY_HINT_ENUM int property accepts, mirroring
## the engine's `EditorPropertyEnum::setup()`: an entry without ':' takes the
## running 0-based index, an entry with ':' sets the running value to the
## number after the colon, and each entry's value is then bumped by one. So
## "A,B,C" implies 0, 1, 2 and "A:5,B:10" implies 5, 10.
static func enum_value_list(hint_string: String) -> Array:
	var values: Array = []
	var current_value := 0
	for raw_option in hint_string.split(","):
		if raw_option.get_slice_count(":") != 1:
			current_value = int(raw_option.get_slice(":", 1))
		values.append(current_value)
		current_value += 1
	return values


static func _is_number_array(value: Variant, size: int) -> bool:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != size:
		return false
	for element in value:
		if not _is_number(element):
			return false
	return true


## Validates `value` as a `rows` x `cols` JSON array of numbers (an array of
## `rows` arrays, each of `cols` numbers). Returns `{"value": rows}` or
## `{"error": message}`. Used by the transform types, whose wire shape nests
## the axis and origin vectors.
static func _number_matrix(value: Variant, rows: int, cols: int) -> Dictionary:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != rows:
		return {"error": "expected %d rows" % rows}
	for row in value:
		if not _is_number_array(row, cols):
			return {"error": "expected each row to be %d numbers" % cols}
	return {"value": value}


## Coerces a JSON array `value` into a typed `Array[T]` where T is
## `element_type`, filling a duplicate of the current `template` (already a
## typed array of T) so the result keeps the property's exact typed array type
## and is assignable back to it. The original array is never mutated. A bad
## element is rejected with its index in the message.
static func _coerce_typed_array(value: Variant, element_type: int, template: Variant) -> Dictionary:
	if typeof(value) != TYPE_ARRAY:
		return {"error": "expected a JSON array"}
	if typeof(template) != TYPE_ARRAY or not template.is_typed():
		return {"error": "the property has no typed array value to build from"}
	var result: Array = template.duplicate()
	result.resize(0)
	var message := _coerce_array_elements(value, element_type, TYPE_NIL, result)
	if not message.is_empty():
		return {"error": message}
	return {"value": result}


## Coerces a JSON array `value` into the Packed*Array type `packed_type`,
## applying each element type's own rule (see `_packed_element_type` and the
## byte/int32 special cases). A bad element is rejected with its index.
static func _coerce_packed_array(value: Variant, packed_type: int) -> Dictionary:
	var element_type := _packed_element_type(packed_type)
	if typeof(value) != TYPE_ARRAY:
		return {"error": "expected a JSON array"}
	var result: Variant
	match packed_type:
		TYPE_PACKED_BYTE_ARRAY:
			result = PackedByteArray()
		TYPE_PACKED_INT32_ARRAY:
			result = PackedInt32Array()
		TYPE_PACKED_INT64_ARRAY:
			result = PackedInt64Array()
		TYPE_PACKED_FLOAT32_ARRAY:
			result = PackedFloat32Array()
		TYPE_PACKED_FLOAT64_ARRAY:
			result = PackedFloat64Array()
		TYPE_PACKED_STRING_ARRAY:
			result = PackedStringArray()
		TYPE_PACKED_VECTOR2_ARRAY:
			result = PackedVector2Array()
		TYPE_PACKED_VECTOR3_ARRAY:
			result = PackedVector3Array()
		TYPE_PACKED_VECTOR4_ARRAY:
			result = PackedVector4Array()
		TYPE_PACKED_COLOR_ARRAY:
			result = PackedColorArray()
		_:
			return {"error": "packed array type %s is not supported" % type_string(packed_type)}
	var message := _coerce_array_elements(value, element_type, packed_type, result)
	if not message.is_empty():
		return {"error": message}
	return {"value": result}


## Coerces each element of the JSON array `value` into `result` (a typed
## `Array` or a Packed*Array) and returns the error message for the first bad
## element, or an empty string on success. The scalar element rules are
## inlined here so a large numeric or string array is coerced without a
## function call, a `match`, or a Dictionary per element; the compound element
## types (vectors, transforms, colors) fall back to `_coerce_element`, which
## is fine because those arrays are small in practice. `packed_type` selects
## the byte/int32 rules for the two packed types that need them.
static func _coerce_array_elements(value: Array, element_type: int, packed_type: int, result: Variant) -> String:
	if packed_type == TYPE_PACKED_BYTE_ARRAY:
		for i in range(value.size()):
			var element: Variant = value[i]
			if not _is_number(element):
				return "element %d: expected an integer" % i
			var byte_value := float(element)
			if byte_value != floorf(byte_value) or byte_value < 0.0 or byte_value > 255.0:
				return "element %d: expected an integer in 0..255, got %s" % [i, str(element)]
			result.append(int(byte_value))
		return ""
	if packed_type == TYPE_PACKED_INT32_ARRAY:
		for i in range(value.size()):
			var element: Variant = value[i]
			if not _is_number(element):
				return "element %d: expected an integer" % i
			var int32_value := float(element)
			if int32_value != floorf(int32_value) or int32_value < -2147483648.0 or int32_value > 2147483647.0:
				return "element %d: expected an integer in -2147483648..2147483647, got %s" % [i, str(element)]
			result.append(int(int32_value))
		return ""
	match element_type:
		TYPE_BOOL:
			for i in range(value.size()):
				var element: Variant = value[i]
				if typeof(element) != TYPE_BOOL:
					return "element %d: expected true or false" % i
				result.append(element)
		TYPE_INT:
			for i in range(value.size()):
				var element: Variant = value[i]
				if not _is_number(element):
					return "element %d: expected an integer" % i
				var number := float(element)
				# 2^53: beyond this a JSON float can no longer represent every
				# integer exactly, so the caller's value may already be lost.
				if number != floorf(number) or absf(number) > 9007199254740992.0:
					return "element %d: expected an integer, got %s" % [i, str(element)]
				result.append(int(number))
		TYPE_FLOAT:
			for i in range(value.size()):
				var element: Variant = value[i]
				if typeof(element) != TYPE_FLOAT and typeof(element) != TYPE_INT:
					return "element %d: expected a number" % i
				result.append(float(element))
		TYPE_STRING:
			for i in range(value.size()):
				var element: Variant = value[i]
				if typeof(element) != TYPE_STRING:
					return "element %d: expected a string" % i
				result.append(element)
		_:
			var error_out := []
			for i in range(value.size()):
				var coerced: Variant = _coerce_element(value[i], element_type, packed_type, error_out)
				if not error_out.is_empty():
					return "element %d: %s" % [i, error_out[0]]
				result.append(coerced)
	return ""


## Maps each Packed*Array property type to its element Variant type, so the
## element coercion rules in `_coerce_element` can be reused. PackedByteArray
## and PackedInt32Array elements are NOT mapped here: `_coerce_element` gives
## them their own range rules.
static func _packed_element_type(packed_type: int) -> int:
	match packed_type:
		TYPE_PACKED_BYTE_ARRAY:
			return TYPE_INT
		TYPE_PACKED_INT32_ARRAY:
			return TYPE_INT
		TYPE_PACKED_INT64_ARRAY:
			return TYPE_INT
		TYPE_PACKED_FLOAT32_ARRAY:
			return TYPE_FLOAT
		TYPE_PACKED_FLOAT64_ARRAY:
			return TYPE_FLOAT
		TYPE_PACKED_STRING_ARRAY:
			return TYPE_STRING
		TYPE_PACKED_VECTOR2_ARRAY:
			return TYPE_VECTOR2
		TYPE_PACKED_VECTOR3_ARRAY:
			return TYPE_VECTOR3
		TYPE_PACKED_VECTOR4_ARRAY:
			return TYPE_VECTOR4
		TYPE_PACKED_COLOR_ARRAY:
			return TYPE_COLOR
	return TYPE_NIL


## The element types a typed `Array[T]` may use: exactly the scalar, vector,
## and color types `set-property` already supports. Everything else (Object,
## Node, Resource, Dictionary, nested Array, Variant) is rejected by the
## caller before any coercion.
static func is_supported_array_element(element_type: int) -> bool:
	return element_type in [
		TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING, TYPE_STRING_NAME, TYPE_NODE_PATH,
		TYPE_VECTOR2, TYPE_VECTOR3, TYPE_VECTOR4, TYPE_VECTOR2I, TYPE_VECTOR3I, TYPE_VECTOR4I,
		TYPE_RECT2, TYPE_RECT2I, TYPE_TRANSFORM2D, TYPE_TRANSFORM3D, TYPE_COLOR,
	]