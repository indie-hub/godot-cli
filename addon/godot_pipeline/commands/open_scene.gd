@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Opens a scene in the running editor by path, optionally saving a dirty
## edited scene first. The reply names the scene that is actually edited
## after the call, so a scene the editor silently refuses to open is an error.
## A success reply also lists the scenes that still have unsaved changes in
## `unsaved`; an untitled dirty scene shows up there as an empty string,
## e.g. `[""]`.
##
## `request` must carry string fields `project_path` and `scene_path`, and an
## optional bool `save` (default false). The project-path check runs before
## anything else is resolved. The pre-check rejects imported scenes and paths
## that do not load as a `PackedScene` before anything is saved, so rejected
## paths never reach the editor's blocking dialogs. Without `save` a dirty
## edited scene stays open as a background tab. If the editor still refuses
## after a save, the save stays and the reply is an error.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var scene_path_value: Variant = request.get("scene_path")
	var save_value: Variant = request.get("save", false)
	if typeof(project_path_value) != TYPE_STRING or typeof(scene_path_value) != TYPE_STRING or typeof(save_value) != TYPE_BOOL:
		return CommandSupport.error("open_scene requires string fields: project_path, scene_path and a bool field: save")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var scene_path: String = scene_path_value
	if scene_path.is_empty():
		return CommandSupport.error("scene path must not be empty")
	# The editor accepts res://, relative, absolute-inside-project, ".." and
	# uid:// paths. Anything whose resolved path is not a res:// path in this
	# project (an absolute path through a symlink, for example) is refused by
	# the editor, so it is rejected here, before anything is saved.
	var resolved := _resolved_path(scene_path)
	if not resolved.begins_with("res://"):
		return CommandSupport.error("scene did not open: %s" % scene_path)
	# Loading as a PackedScene rejects anything that resolves to res:// but the
	# editor would refuse with a blocking dialog. Broken scenes that still load
	# are caught by the dependency walk below.
	var packed := ResourceLoader.load(resolved, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE) as PackedScene
	# Imported scenes (.gltf and friends) load as a PackedScene but the editor
	# refuses to open them; only they carry an .import sidecar, so it tells
	# them apart from native scenes before anything is saved.
	if packed == null or not packed.can_instantiate() or FileAccess.file_exists(resolved + ".import"):
		return CommandSupport.error("no such scene: %s" % scene_path)
	if not _dependencies_exist(resolved):
		return CommandSupport.error("scene did not open: %s" % scene_path)

	var editor := plugin.get_editor_interface()
	var edited_root := editor.get_edited_scene_root()
	var saved := false
	if edited_root != null:
		# A scene is dirty when the edited root's own file path is in the
		# editor's unsaved list. Without the save option the dirty scene is
		# left alone and stays open as a background tab; with it, the dirty
		# scene is saved before the open, and a failed save is an error.
		var edited_path: String = edited_root.scene_file_path
		if save_value and editor.get_unsaved_scenes().has(edited_path):
			var error := editor.save_scene()
			if error != OK:
				return CommandSupport.error("failed to save the scene: %s" % error_string(error))
			saved = true

	editor.open_scene_from_path(scene_path)

	# open_scene_from_path returns void and can still refuse (for example
	# while the editor is already switching scenes), so the success reply is
	# decided by comparing the edited scene against the requested scene's
	# resolved res:// path, in the same call. The edited root updates
	# synchronously in Godot 4.7.2, so no frame wait is needed.
	var edited_after := editor.get_edited_scene_root()
	if edited_after == null or edited_after.scene_file_path != resolved:
		return CommandSupport.error("scene did not open: %s" % scene_path)

	return CommandSupport.ok({"path": edited_after.scene_file_path, "saved": saved, "unsaved": editor.get_unsaved_scenes()})


## Resolves `scene_path` to the normalized res:// path the editor stores in
## the edited root's `scene_file_path`: `ProjectSettings.localize_path` for
## res://, relative, absolute-inside-project and ".." paths, and
## `ResourceUID.uid_to_path` for a uid:// path, which localize_path leaves
## unchanged. Measured in Godot 4.7.2 to match the editor's own resolution.
static func _resolved_path(scene_path: String) -> String:
	if scene_path.begins_with("uid://"):
		return ResourceUID.uid_to_path(scene_path)
	return ProjectSettings.localize_path(scene_path)


## Returns false when the scene at `path` or any scene it instantiates,
## directly or through nested instances, references a resource file that does
## not exist. The editor refuses to open a scene whose dependency graph is
## broken (a missing script, a missing instanced scene), so this catches what
## loading cannot. Dependencies of an instanced scene come back as
## "uid://xxxx::::res://path"; the path part is the real file and only it is
## checked, since exists() is false for the combined string even when valid.
static func _dependencies_exist(path: String) -> bool:
	var visited := {}
	var pending: Array = [path]
	while not pending.is_empty():
		var current: String = pending.pop_back()
		if visited.has(current):
			continue
		visited[current] = true
		for dependency in ResourceLoader.get_dependencies(current):
			var dependency_path := _dependency_path(dependency)
			if not ResourceLoader.exists(dependency_path):
				return false
			if dependency_path.ends_with(".tscn") or dependency_path.ends_with(".scn"):
				pending.append(dependency_path)
	return true


## Strips the "uid://xxxx::::" prefix from an instanced-scene dependency, so
## the remaining res:// path can be checked with `ResourceLoader.exists`.
static func _dependency_path(dependency: String) -> String:
	if dependency.contains("::::"):
		return dependency.substr(dependency.rfind("::::") + 4)
	return dependency