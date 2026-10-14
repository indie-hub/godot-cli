@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const ValueCodec := preload("../value_codec.gd")


## Reads one resource file as a typed view without changing the scene, the
## undo/redo history, or any file, and without requiring an edited scene.
## Read-only: no Undo/Redo step, no dirty flag, no save. `request` must carry
## string fields `project_path` (the canonical, symlink-resolved absolute path
## of the project the caller intends to read) and `resource_path` (a `res://`
## path). The project-path check runs first.
##
## The command refuses, before any load, a resource whose dependency chain
## contains a `@tool` script (naming that script): `@tool` resource scripts run
## project code on load, so the chain is scanned from script source text while
## nothing is loaded. The chain is the transitive `get_dependencies` closure
## plus every `preload("...")`/`load("...")` and `extends` literal path in each
## reached script's source (a base reached only as a class name resolves
## through the project's global class list; a `uid://` literal resolves
## through the ResourceUID registry), because compiling a script compiles and
## runs what it reaches. The `extends` scan also reads a one-line
## `class_name Name extends Base` form and an inner `class Name extends ...`
## form. `@tool` is a line whose first non-blank text is the annotation; a
## mention in a comment or string is not. A path a script builds only at run
## time, or a `uid://` the editor does not know, cannot be seen by a source
## scan and stays out of scope. A script that merely `extends` a `@tool` base
## is refused, and the reply names the base script: the walk follows the
## extends edge and the base is a `@tool` script. A script that only extends a
## non-`@tool` ordinary base (whose own chain has no `@tool`) loads.
##
## The load uses `CACHE_MODE_IGNORE_DEEP` only, so a live cached object an open
## scene holds is never replaced. A file that exists but does not load replies
## an error. The reply mirrors `inspect_node`'s property entries; object values
## are shown compactly (external reference `{"path","type"}`, embedded
## sub-resource inline with a depth limit of 3 and a loop guard on its id),
## arrays over 10,000 elements are replaced by `{"type","size","truncated":true}`,
## and null properties are null.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var resource_path_value: Variant = request.get("resource_path")
	if typeof(project_path_value) != TYPE_STRING or typeof(resource_path_value) != TYPE_STRING:
		return CommandSupport.error("inspect_resource requires string fields: project_path, resource_path")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var resource_path: String = resource_path_value
	if not resource_path.begins_with("res://"):
		return CommandSupport.error("resource path must start with res://: %s" % resource_path)
	if ".." in resource_path.split("/"):
		return CommandSupport.error("resource path must not contain '..': %s" % resource_path)
	if resource_path.contains("uid://"):
		return CommandSupport.error("resource path must not contain uid://: %s" % resource_path)
	var extension := resource_path.get_extension().to_lower()
	if extension == "tscn" or extension == "scn":
		return CommandSupport.error("inspect_resource does not read scenes; use open_scene or scene_tree: %s" % resource_path)
	if extension == "gd":
		return CommandSupport.error("inspect_resource does not read scripts; read the file directly: %s" % resource_path)

	# A text .tres grows its load cost with its size (measured about 0.14 s
	# per MiB in 4.7.2), so a text file over 4 MiB is refused before it is
	# parsed, keeping the request bounded and the editor responsive. The
	# extension is case-insensitive, so an uppercase .TRES is capped too.
	if extension == "tres" and _file_size(resource_path) > 4194304:
		return CommandSupport.error("resource is a text .tres over 4 MiB and is refused: %s" % resource_path)

	if not ResourceLoader.exists(resource_path):
		return CommandSupport.error("resource not found: %s" % resource_path)

	var tool_script := _tool_script_dependency(resource_path)
	if tool_script != "":
		return CommandSupport.error("resource depends on a @tool script and is refused: %s" % tool_script)

	var loaded: Resource = ResourceLoader.load(resource_path, "", ResourceLoader.CACHE_MODE_IGNORE_DEEP) as Resource
	if loaded == null:
		return CommandSupport.error("resource failed to load: %s" % resource_path)

	return CommandSupport.ok({
		"path": resource_path,
		"type": loaded.get_class(),
		"properties": _properties(loaded, 0, {}),
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "inspect_resource",
		"description": "Show one resource file's class, typed properties, and embedded sub-resources.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "resource_path", "flag": "--resource-path", "positional": null, "type": "res_path", "required": true, "default": null, "help": "A res:// path that is not a scene or script file; a symlink that escapes the project is rejected before sending."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Returns the size of the text file at `path` in bytes, or -1 when it cannot
## be opened. `FileAccess.open` with READ keeps the editor on a plain file
## read, so the 4 MiB cap check runs no project code.
static func _file_size(path: String) -> int:
	var file: FileAccess = FileAccess.open(path, FileAccess.READ)
	return file.get_length() if file != null else -1


# A `@tool` annotation is a line whose first non-blank text is `@tool`; a
# mention inside a comment or string is not the annotation. Triple-quoted
# string blocks are stripped from the source before this test, so a line
# inside such a string does not count.
const TOOL_ANNOTATION_PATTERN := "(?m)^[ \\t]*@tool\\b"

# Literal `preload("...")` and `load("...")` paths in a script's source. Only
# quoted literals are visible to a source scan; a path a script builds at run
# time is not. Whitespace may span lines between the call, the parenthesis and
# the string, and the string needs no closing parenthesis after it, so
# `load("res://p.tres", "", 1)` and a multi-line `preload(` are both caught.
const LITERAL_LOAD_PATTERN := "(?:preload|load)\\s*\\(\\s*[\"']([^\"']+)[\"']"

# A triple-quoted string block, either delimiter; removed from the source text
# before the annotation test so a line inside one cannot look like an
# annotation. GDScript has no nested triple-quoted strings, so a scan from the
# first delimiter to the next is enough.
const TRIPLE_STRING_PATTERN := "\"\"\"[\\s\\S]*?\"\"\"|'''[\\s\\S]*?'''"

# A script `extends` its base as a string literal (path, relative to the
# script's folder) or as a bare class name. The match is anchored at the start
# of a statement: an optional leading annotation (`@icon("...")`) and an
# optional `class_name Identifier` or `class Identifier` (an inner class) may
# precede `extends` on the same line, but a comment or a string that merely
# contains the word `extends` does not match. Group 2 is a quoted base path,
# group 3 a bare class name; only one is set.
const EXTENDS_PATTERN := "(?:^|\\n)[ \\t]*(?:@[A-Za-z_]\\w*(?:\\([^)]*\\))?[ \\t]*)*(?:(?:class_name|class)[ \\t]+[A-Za-z_]\\w*[ \\t]*)?extends[ \\t]*(?:['\"]([^'\"]+)['\"]|([A-Za-z_]\\w*))"

# A literal resource path may be a `res://` path, a path relative to the
# script's folder, or a `uid://` id that Godot 4.4 and later accept in
# preload/load (and in `extends "uid://..."`). A `uid://` id is resolved
# through the engine's ResourceUID registry; an id the editor does not know
# adds nothing.
static func _literal_path(literal: String, dir: String) -> String:
	if literal.begins_with("res://"):
		return literal
	if literal.begins_with("uid://"):
		var id := ResourceUID.text_to_id(literal)
		if ResourceUID.has_id(id):
			return ResourceUID.get_id_path(id)
		return ""
	return dir.path_join(literal)


## Returns the path of the first `.gd` script in the transitive dependency
## chain of `path` whose source has a `@tool` annotation, or an empty string
## when there is none. Only `get_dependencies`, file reads, and a plain source
## scan are used, so no project code runs. The chain follows
## `get_dependencies` (direct only per call, with an optional `uid://...::::`
## prefix stripped) and, for every `.gd` script reached, the `res://`,
## relative and `uid://` paths its source names in `preload("...")`/
## `load("...")`, because a preloaded resource is compiled with its script and
## a `@tool` `_init` runs even when the outer resource later fails to load.
## The walk also follows the script's `extends` edge: `extends "path"` adds
## that path, a one-line `class_name Name extends ...` and an inner
## `class Name extends ...` add their base, and `extends ClassName` resolves
## the class name against the project's global class list and adds that
## script's path, so a base script's own preloads and base are walked in turn.
## Triple-quoted string blocks are removed from each source before the
## annotation test. A script that merely extends a `@tool` script without
## `@tool` itself is therefore refused and the reply names the base script:
## the walk reaches the base, whose source has the annotation.
static func _tool_script_dependency(path: String) -> String:
	var annotation_regex := RegEx.create_from_string(TOOL_ANNOTATION_PATTERN)
	var literal_regex := RegEx.create_from_string(LITERAL_LOAD_PATTERN)
	var triple_regex := RegEx.create_from_string(TRIPLE_STRING_PATTERN)
	var extends_regex := RegEx.create_from_string(EXTENDS_PATTERN)
	var visited := {}
	var pending: Array = [path]
	while not pending.is_empty():
		var current: String = pending.pop_back()
		if visited.has(current):
			continue
		visited[current] = true
		for dependency in ResourceLoader.get_dependencies(current):
			var dependency_path := CommandSupport.dependency_path(dependency)
			if not visited.has(dependency_path):
				pending.append(dependency_path)
		if current.get_extension().to_lower() != "gd":
			continue
		var source := FileAccess.get_file_as_string(current)
		# Remove triple-quoted string blocks so a line inside one cannot read
		# as an annotation; GDScript does not let them nest.
		source = triple_regex.sub(source, "", true)
		if annotation_regex.search(source) != null:
			return current
		var dir := current.get_base_dir()
		for match in literal_regex.search_all(source):
			var literal_path := _literal_path(match.get_string(1), dir)
			if literal_path != "" and not visited.has(literal_path):
				pending.append(literal_path)
		# Follow the extends edge: a string literal base, or a class-name base
		# resolved through the project's global class list (the engine "extends
		# ClassName" form). A class-name with no match is a built-in class and
		# adds nothing.
		for match in extends_regex.search_all(source):
			var base_path := _resolve_extended_path(match, dir)
			if base_path != "" and not visited.has(base_path):
				pending.append(base_path)
	return ""


## Resolves an `extends` match to a script path. A quoted base is a path,
## resolved through `_literal_path` (res://, relative, or uid://); a bare
## class name is looked up in the project's global class list, whose entries
## carry the `class` name and the script `path`. An empty result means a
## built-in class or an unknown uid (no script to walk).
static func _resolve_extended_path(match: RegExMatch, dir: String) -> String:
	var base := match.get_string(1)
	if base == "":
		base = match.get_string(2)
		if base == "":
			return ""
		for global_class in ProjectSettings.get_global_class_list():
			if global_class.get("class") == base:
				return global_class.get("path")
		return ""
	return _literal_path(base, dir)


## The property entries of `resource` exactly as `inspect_node` builds them
## (inspector or storage usage only), with the object-valued encoding of this
## command. `depth` is the current embedded sub-resource expansion depth; at
## depth 3 an embedded sub-resource is shown as `{"path","type"}` without
## expanding it. `seen` holds the `file::id` values already expanded in this
## reply, so a sub-resource cycle cannot recurse forever.
static func _properties(resource: Resource, depth: int, seen: Dictionary) -> Array:
	var properties: Array = []
	for info in resource.get_property_list():
		var usage: int = info["usage"]
		if usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE) == 0:
			continue
		var entry := {
			"name": info["name"],
			"type": type_string(info["type"]),
			"read_only": usage & PROPERTY_USAGE_READ_ONLY != 0,
		}
		var property_value: Variant = resource.get(info["name"])
		var encoded: Variant = _encode_value(property_value, depth, seen)
		# The shared codec returns null for types set-property cannot address
		# (Object beyond Resource, Dictionary, untyped Array, Callable, Signal,
		# ...) and for a genuinely null property; only the former is marked
		# unsupported, because a null property value is a valid value.
		if encoded == null and property_value != null:
			entry["value"] = null
			entry["supported"] = false
		else:
			entry["value"] = encoded
		properties.append(entry)
	return properties


## Encodes one property value for the reply: arrays over 10,000 elements are
## replaced by `{"type","size","truncated":true}`; an embedded sub-resource is
## expanded inline (depth-capped and loop-guarded); an external resource
## reference becomes `{"path","type"}`; everything else uses the shared value
## codec, which returns null for the types `set-property` cannot address
## (Object besides Resource, Dictionary, untyped Array, Callable, Signal, ...)
## and for a null property value.
static func _encode_value(value: Variant, depth: int, seen: Dictionary) -> Variant:
	if value == null:
		return null
	if typeof(value) == TYPE_ARRAY or typeof(value) in [
		TYPE_PACKED_BYTE_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_INT64_ARRAY,
		TYPE_PACKED_FLOAT32_ARRAY, TYPE_PACKED_FLOAT64_ARRAY, TYPE_PACKED_STRING_ARRAY,
		TYPE_PACKED_VECTOR2_ARRAY, TYPE_PACKED_VECTOR3_ARRAY, TYPE_PACKED_VECTOR4_ARRAY,
		TYPE_PACKED_COLOR_ARRAY,
	]:
		if value.size() > 10000:
			return {
				"type": type_string(typeof(value)),
				"size": value.size(),
				"truncated": true,
			}
	if typeof(value) == TYPE_OBJECT and value is Resource:
		var resource: Resource = value
		var resource_path: String = resource.resource_path
		if resource_path.contains("::"):
			if depth >= 3 or seen.has(resource_path):
				return {"path": resource_path, "type": resource.get_class()}
			var next_seen := seen.duplicate()
			next_seen[resource_path] = true
			return {
				"path": resource_path,
				"type": resource.get_class(),
				"properties": _properties(resource, depth + 1, next_seen),
			}
		return {"path": resource_path, "type": resource.get_class()}
	return ValueCodec.value_to_json(value)