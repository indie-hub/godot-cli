@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Reports the currently edited scene's node tree, root first. Read-only: no
## undo/redo action, no dirty flag, no file write.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var scene_root := plugin.get_editor_interface().get_edited_scene_root()
	if scene_root == null:
		return CommandSupport.error("no scene is currently being edited")
	else:
		return CommandSupport.ok(_describe_node(scene_root))


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "scene_tree",
		"description": "Show the node tree of the open scene.",
		"needs_project_path": false,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [],
	}


static func _describe_node(node: Node) -> Dictionary:
	var children: Array = []
	for child in node.get_children():
		children.append(_describe_node(child))
	return {
		"name": str(node.name),
		"type": node.get_class(),
		"children": children,
	}