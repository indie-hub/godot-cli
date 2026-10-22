@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Attaches, replaces or detaches the GDScript of one node of the currently
## edited scene through the editor's undo/redo stack, so the change shows up as
## one Undo/Redo step and marks the scene dirty without saving it.
##
## `request` must carry string fields `node_path` (relative to the edited scene
## root) and `project_path` (the canonical, symlink-resolved absolute path of
## the project the caller intends to edit), an optional string field
## `script_path` (the `res://` path of a GDScript) and optional bool fields
## `detach` (default false) and `replace` (default false). Exactly one of
## `script_path` and `detach` must be given, and `replace` is meaningless
## together with `detach`.
##
## The engine validates nothing when a script is assigned: `set_script` accepts
## a script with a syntax error and a Node2D script on a Node3D, both silently.
## Every check therefore runs before the undo action is created, so a rejected
## request never touches the scene, the undo history or the unsaved flag.
##
## The script is loaded last, after the project, scene, node, instance and
## current-script checks, because loading it can run script code. A request
## refused by one of those early guards therefore never loads the script and
## never runs any of it. Once those guards have passed the script is loaded,
## and two different things can then run: a `@tool` script's static var
## initializers run when the script is loaded, even if a later check (the
## base-class check) refuses the request, while the instance `_init` of a
## `@tool` script runs only when the script is attached to a node in the tree.
##
## The script only survives a save when the edited scene serializes the node: a
## node inside an instanced sub-scene needs every instance between it and the
## scene root to have Editable Children on; the instance root itself is accepted
## with Editable Children off.
##
## A replace is refused unless `replace` is given, and attaching the script the
## node already holds is refused even then. Replacing a script resets the node's
## exported values to the new script's defaults; they are not captured and
## reapplied, so undo restores the old script with its own defaults rather than
## the values that were set before.
##
## This first slice does not re-read a script that was edited outside the
## editor. The script that gets attached is the copy the resource loader holds,
## so a file edited on disk after the script was loaded attaches as the cached
## copy, and a file edited to a syntax error still attaches. Only an editor
## reload of the script itself picks the change up.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var node_path_value: Variant = request.get("node_path")
	var project_path_value: Variant = request.get("project_path")
	var script_path_value: Variant = request.get("script_path", null)
	var detach_value: Variant = request.get("detach", false)
	var replace_value: Variant = request.get("replace", false)
	if typeof(node_path_value) != TYPE_STRING or typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("attach_script requires string fields: node_path, project_path")
	if script_path_value != null and typeof(script_path_value) != TYPE_STRING:
		return CommandSupport.error("attach_script requires a string field: script_path")
	if typeof(detach_value) != TYPE_BOOL or typeof(replace_value) != TYPE_BOOL:
		return CommandSupport.error("attach_script requires boolean fields: detach, replace")

	var detach: bool = detach_value
	var replace: bool = replace_value
	if detach and script_path_value != null:
		return CommandSupport.error("attach_script takes either script_path or --detach, not both")
	if not detach and script_path_value == null:
		return CommandSupport.error("attach_script requires one of: a script_path, or --detach")
	if detach and replace:
		return CommandSupport.error("attach_script cannot combine --detach with --replace")

	var guarded: Variant = CommandSupport.guard_request(plugin, project_path_value, node_path_value)
	if guarded is String:
		return CommandSupport.error(guarded)
	var scene_root: Node = guarded["scene_root"]
	var node_path: String = guarded["node_path"]
	var target: Node = guarded["target"]

	# The script only survives a save when the edited scene serializes its node,
	# so walk the owners and refuse a node whose edit the save would drop. The
	# instance root itself is owned by the edited scene root, so its owner walk
	# is empty and it is accepted with Editable Children off.
	var owner_node: Node = target.owner
	while owner_node != null and owner_node != scene_root:
		if not scene_root.is_editable_instance(owner_node):
			return CommandSupport.error(
				"node is not editable in this scene: %s is inside an instanced sub-scene without Editable Children enabled, so the script would not be saved" % node_path
			)
		owner_node = owner_node.owner

	# The current script is read from the node, never from disk, so these rules
	# cost nothing and refuse before any script is loaded.
	var current: Script = target.get_script()
	var current_path: Variant = null if current == null else str(current.resource_path)
	if detach:
		if current == null:
			return CommandSupport.error("node has no script to detach: %s" % node_path)
	elif current != null and not replace:
		return CommandSupport.error(
			"node already has a script: %s holds %s; pass --replace to attach a different one" % [node_path, str(current_path)]
		)

	var loaded: Variant = {}
	if not detach:
		var script_path: String = script_path_value
		# Attaching the script the node already holds is a no-op the engine
		# would apply silently, so it is refused even with --replace.
		if current != null and script_path == str(current_path):
			return CommandSupport.error(
				"node already has that script: %s holds %s" % [node_path, script_path]
			)
		loaded = _load_script(target, node_path, script_path)
		if loaded.has("error"):
			return CommandSupport.error(loaded["error"])
		# The engine normalizes a path inside the project, so `res://./a.gd`,
		# `res://sub/../a.gd` and a repeated slash all load the Script the node
		# already holds and are not caught by comparing path strings. The loaded
		# Script is the one that would be assigned, so compare it with the one
		# the node holds: that makes the refusal independent of how the caller
		# spelled the path. The Script is already cached, so this costs no load.
		if current != null and loaded["script"] == current:
			return CommandSupport.error(
				"attach_script refused %s for node %s: the node already holds this script (the path resolves to %s), so nothing was attached and the node is unchanged" % [script_path, node_path, current_path]
			)
	var script: Variant = loaded.get("script")
	var tool_script: bool = false
	if script != null:
		tool_script = script.is_tool()

	var undo_redo := plugin.get_undo_redo()
	undo_redo.create_action("Detach Script" if detach else "Replace Script" if replace else "Attach Script", UndoRedo.MERGE_DISABLE, scene_root)
	undo_redo.add_do_method(target, "set_script", script)
	undo_redo.add_undo_method(target, "set_script", current)
	undo_redo.commit_action()

	var attached_path: Variant = null if detach else str(script.resource_path)
	return CommandSupport.ok({
		"node_path": node_path,
		"action": "detach" if detach else "replace" if replace else "attach",
		"script": attached_path,
		"previous_script": current_path,
		"tool_script": tool_script,
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "attach_script",
		"description": "Attach, replace or detach a node's GDScript.",
		"needs_project_path": true,
		"edits_scene": true,
		"refresh_poll": false,
		"arguments": [
		{"name": "node_path", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "Scene-relative path of the node."},
		{"name": "script_path", "flag": null, "positional": 1, "type": "string", "required": false, "default": null, "help": "res:// path of the GDScript to attach; omit it when passing --detach."},
		{"name": "detach", "flag": "--detach", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Remove the node's script instead of attaching one."},
		{"name": "replace", "flag": "--replace", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Allow attaching a different script to a node that already has one."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Loads and checks the script at `script_path` for `target`, and returns
## {"script": script} on success or {"error": message} on the first failure.
## The project, scene, node, instance and current-script checks have all passed
## by the time this runs, so a request refused by one of them never reaches
## this function. The path-shape and existence checks below also refuse before
## `ResourceLoader.load` and load nothing. A refusal after that load has already
## loaded the script, which can run a `@tool` script's static var initializers;
## the instance `_init` runs only later, when the script is attached to a node in the tree.
##
## The checks are the ones the engine omits on assignment: a path that is not
## an attachable `res://` path, a path that names no file, a resource that is
## not a GDScript, an abstract script the engine's `set_script` refuses, a
## script that did not compile, and a base class the target node does not have.
##
## The script is checked through its own compiled state and is never reloaded:
## `reload()` recompiles the in-memory source, so it neither re-reads the file
## nor is safe on a script that already has an instance, and a pathless probe
## script would falsely refuse a registered `class_name` script and a
## relative-path base. An empty instance base type is what a GDScript that did
## not compile reports. A script that parses but is semantically wrong, and a
## script whose `_init` fails at run time, cannot be detected here at all.
static func _load_script(target: Node, node_path: String, script_path: String) -> Dictionary:
	# The engine loads `user://`, absolute and bare-relative paths, so the
	# `res://` prefix is enforced here. An embedded sub-resource path carries
	# `::` and names a resource inside another file, not a script of its own.
	if not script_path.begins_with("res://") or script_path.contains("::"):
		return {"error": "attach_script refused %s for node %s: a script must be named by its own res:// path, and an embedded sub-resource path (one with ::) names a script inside another file rather than a script file of its own; pass the res:// path of the .gd file to attach" % [script_path, node_path]}
	if not ResourceLoader.exists(script_path):
		return {"error": "attach_script refused %s for node %s: no resource was found at that path, so the file is missing, it is a directory, or its type is not one the editor imports; check the path and that the file has been imported, then retry" % [script_path, node_path]}
	var script: Variant = ResourceLoader.load(script_path)
	if script == null:
		return {"error": "attach_script refused %s for node %s: the path names a resource the editor cannot load; check the path and that the file has been imported, then retry" % [script_path, node_path]}
	if not (script is GDScript):
		return {"error": "attach_script refused %s for node %s: only a GDScript can be attached, but that path is a %s; pass the res:// path of a .gd file to attach" % [script_path, node_path, script.get_class()]}
	# An abstract script parses and reports a base type, but `set_script`
	# refuses it, so it is refused here rather than after the undo action.
	if script.is_abstract():
		return {"error": "attach_script refused %s for node %s: the script is declared @abstract, so it has no instance a node could run; attach a concrete subclass of it instead" % [script_path, node_path]}
	var base: String = script.get_instance_base_type()
	if base == "":
		return {"error": "attach_script refused %s for node %s: the script did not compile, so nothing was attached and node %s is unchanged. The usual causes are a syntax error, an analysis error (an undefined name or function, a type mismatch, a missing preload), or a base class that is not known yet, such as extends SomeClassName before the project file-system scan has registered it. Open the script in the editor or read the editor console, which shows the engine's own parse error, fix it, then retry; if the file was fixed on disk after the editor had already failed to load it, the cached broken copy stays until the editor reloads the file, because this command never re-reads it." % [script_path, node_path, node_path]}
	if not target.is_class(base):
		return {"error": "attach_script refused %s for node %s: the script extends %s and node %s is a %s, so the engine would refuse or silently misbehave on the assignment; attach it to a node of class %s or a subclass, or pass a script whose base class matches this node" % [script_path, node_path, base, node_path, target.get_class(), base]}
	return {"script": script}