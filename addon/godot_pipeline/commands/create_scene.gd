@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const SceneWrite := preload("scene_write.gd")


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "create_scene",
		"description": "Create a new scene file with an explicit root type without opening it.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "path", "flag": null, "positional": 0, "type": "string", "required": true, "default": null, "help": "A res:// .tscn path that must not exist."},
		{"name": "root_type", "flag": "--root-type", "positional": null, "type": "string", "required": true, "default": null, "help": "Built-in Node subclass for the scene root."},
		{"name": "root_name", "flag": "--root-name", "positional": null, "type": "string", "required": false, "default": null, "help": "Name of the scene root; default is the file stem."},
		{"name": "dry_run", "flag": "--dry-run", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Validate the whole request and write nothing."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## Creates a new `.tscn` file with one root node and never touches tabs,
## the edited scene, dirty state or undo history. Works with no scene open.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var path_value: Variant = request.get("path")
	var root_type_value: Variant = request.get("root_type")
	var root_name_value: Variant = request.get("root_name", null)
	var dry_run_value: Variant = request.get("dry_run", false)
	if typeof(project_path_value) != TYPE_STRING or typeof(path_value) != TYPE_STRING or typeof(root_type_value) != TYPE_STRING:
		return CommandSupport.error("create_scene requires string fields: path, root_type, project_path")
	if root_name_value != null and typeof(root_name_value) != TYPE_STRING:
		return CommandSupport.error("create_scene requires a string field: root_name")
	if typeof(dry_run_value) != TYPE_BOOL:
		return CommandSupport.error("create_scene requires a boolean field: dry_run")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)
	for key in request.keys():
		if not str(key) in ["command", "project_path", "path", "root_type", "root_name", "dry_run"]:
			return CommandSupport.error("unknown field: %s" % str(key))

	var requested_class: String = root_type_value
	if not ClassDB.class_exists(requested_class):
		return CommandSupport.error("unknown class: %s" % requested_class)
	for global_class in ProjectSettings.get_global_class_list():
		if global_class.get("class") == requested_class:
			return CommandSupport.error("class must be a built-in Node class, not a project script class: %s" % requested_class)
	if requested_class != "Node" and not ClassDB.is_parent_class(requested_class, "Node"):
		return CommandSupport.error("class is not a Node subclass: %s" % requested_class)
	if not ClassDB.can_instantiate(requested_class):
		return CommandSupport.error("class cannot be instantiated: %s" % requested_class)

	var path: String = path_value
	var root_name: String = path.get_file().get_basename()
	if root_name_value != null:
		root_name = root_name_value
	if root_name.is_empty():
		return CommandSupport.error("name must not be empty")
	for character in CommandSupport.INVALID_NAME_CHARACTERS:
		if root_name.contains(character):
			return CommandSupport.error("name must not contain any of: . : @ / \" %")
	if root_name != root_name.validate_node_name():
		return CommandSupport.error("name must not contain any of: . : @ / \" %")

	var editor := plugin.get_editor_interface()
	var edited_root := editor.get_edited_scene_root()
	var edited_path := ""
	if edited_root != null:
		edited_path = edited_root.scene_file_path
	var checked: Dictionary = SceneWrite.validate_target(plugin, path, false, edited_path)
	if checked.has("error"):
		return CommandSupport.error(checked["error"])

	var dry_run: bool = dry_run_value
	if dry_run:
		return CommandSupport.ok({
			"written": [],
			"replaced": false,
			"dry_run": true,
			"undoable": false,
			"undo_scope": "none: the disk write has no undo in the editor",
			"path": path,
			"root_type": requested_class,
			"root_name": root_name,
			"opened": false,
		})

	var new_root: Node = ClassDB.instantiate(requested_class)
	new_root.name = root_name
	var packed := PackedScene.new()
	var pack_error := packed.pack(new_root)
	if pack_error != OK:
		new_root.queue_free()
		return CommandSupport.error("failed to pack the scene: %s" % error_string(pack_error))
	var save_error := ResourceSaver.save(packed, path)
	new_root.queue_free()
	if save_error != OK:
		return CommandSupport.error("failed to save the scene: %s" % error_string(save_error))
	editor.get_resource_filesystem().update_file(path)
	return CommandSupport.ok({
		"written": [path],
		"replaced": false,
		"dry_run": false,
		"undoable": false,
		"undo_scope": "none: the disk write has no undo in the editor",
		"path": path,
		"root_type": requested_class,
		"root_name": root_name,
		"opened": false,
	})
