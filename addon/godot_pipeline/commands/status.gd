@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Reports the editor's status and the path of the currently edited scene.
## Read-only: no undo/redo action, no dirty flag, no file write.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var scene_root := plugin.get_editor_interface().get_edited_scene_root()
	return CommandSupport.ok({
		"editor": "Godot Editor",
		"version": Engine.get_version_info()["string"],
		"playing": plugin.get_editor_interface().is_playing_scene(),
		"scene_path": scene_root.scene_file_path if scene_root != null else null,
	})