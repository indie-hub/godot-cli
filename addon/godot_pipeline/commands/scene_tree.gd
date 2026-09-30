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


static func _describe_node(node: Node) -> Dictionary:
	var children: Array = []
	for child in node.get_children():
		children.append(_describe_node(child))
	return {
		"name": str(node.name),
		"type": node.get_class(),
		"children": children,
	}