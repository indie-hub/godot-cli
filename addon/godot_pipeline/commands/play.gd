@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")


## Plays the edited scene, a scene named by a res:// path, or the project's
## main scene, in the running editor.
##
## `request` must carry a string field `project_path`, an optional string field
## `scene_path` (the `res://` path of the scene to play) and an optional bool
## field `main` (default false). At most one of `scene_path` and `main` may be
## given; with neither, the edited scene is played.
##
## The whole request is validated before anything is launched, so a refused
## request starts no game and changes no file. Every target is checked before
## the launch: a path that is not `res://`, a missing file, a resource that is
## not a scene the editor can run, and an unset or missing main scene are all
## refused here, so the editor's own confirmation dialog for a missing main
## scene can never open.
##
## A play writes unsaved work: when the edited scene is dirty it is saved
## through the editor's own save path first (the same call `save_scene` uses),
## because the engine would otherwise save it or leave it dirty depending on
## the `run/auto_save/save_before_running` setting. That setting is read only
## to report it in the reply; the save does not depend on it. A failed save
## refuses the request and no game starts. An untitled edited scene cannot be
## saved, and a dirty scene other than the edited one cannot be saved without
## switching tabs, so both are refused rather than launched over.
##
## The game plays in its own process and this call does not wait for it: the
## reply carries the editor state measured immediately after the call, which
## is `playing: true` with the requested scene in Godot 4.7.2. That is the
## editor's own play state, not an acknowledgement from the game: it says a
## game process started, not that the game rendered a frame.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var scene_path_value: Variant = request.get("scene_path", null)
	var main_value: Variant = request.get("main", false)
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("play requires a string field: project_path")
	if scene_path_value != null and typeof(scene_path_value) != TYPE_STRING:
		return CommandSupport.error("play requires a string field: scene_path")
	if typeof(main_value) != TYPE_BOOL:
		return CommandSupport.error("play requires a boolean field: main")
	var play_main: bool = main_value
	if play_main and scene_path_value != null:
		return CommandSupport.error("play takes either a scene path or --main, not both")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(project_mismatch)

	var editor := plugin.get_editor_interface()
	var edited_root: Node = editor.get_edited_scene_root()
	var edited_path: String = "" if edited_root == null else edited_root.scene_file_path

	var requested_scene := ""
	if play_main:
		var configured_main: Variant = ProjectSettings.get_setting("application/run/main_scene")
		var main_path := "" if configured_main == null else str(configured_main)
		if main_path.is_empty():
			return CommandSupport.error("no main scene is configured: application/run/main_scene is empty in project settings")
		requested_scene = _resolved_path(main_path)
		var main_problem := _scene_problem(requested_scene, main_path, "--main")
		if main_problem != "":
			return CommandSupport.error(main_problem)
	elif scene_path_value != null:
		requested_scene = scene_path_value
		if not requested_scene.begins_with("res://"):
			return CommandSupport.error("play refused %s: a scene must be named by its res:// path" % requested_scene)
		var scene_problem := _scene_problem(requested_scene, requested_scene, "play")
		if scene_problem != "":
			return CommandSupport.error(scene_problem)
	else:
		if edited_root == null:
			return CommandSupport.error("no scene is currently being edited")
		if edited_path.is_empty():
			return CommandSupport.error("the edited scene has no file path yet; save it to a file before playing")
		requested_scene = edited_path

	# Save the edited scene before launching when it is dirty. The list is
	# checked first so a refusal caused by another dirty scene happens before
	# any file is written. `save_scene` returns OK whenever the scene has a
	# path, so a failed write is detected by the editor still listing the
	# scene as unsaved afterwards; that is the only signal the editor exposes.
	var unsaved := editor.get_unsaved_scenes()
	for path in unsaved:
		if str(path) != edited_path:
			return CommandSupport.error("another open scene has unsaved changes: %s; save it before playing" % str(path))
	var saved: Array = []
	if edited_root != null and unsaved.has(edited_path):
		editor.save_scene()
		if editor.get_unsaved_scenes().has(edited_path):
			return CommandSupport.error("failed to save the scene: %s is still unsaved, so the game was not started" % edited_path)
		saved.append(edited_path)

	var save_before_running: bool = editor.get_editor_settings().get_setting("run/auto_save/save_before_running")
	var was_playing: bool = editor.is_playing_scene()
	if play_main:
		editor.play_main_scene()
	elif scene_path_value != null:
		editor.play_custom_scene(requested_scene)
	else:
		editor.play_current_scene()

	return CommandSupport.ok({
		"requested_scene": requested_scene,
		"saved": saved,
		"restarted": was_playing,
		"playing": editor.is_playing_scene(),
		"scene": editor.get_playing_scene(),
		"save_before_running": save_before_running,
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "play",
		"description": "Play the edited scene, a named scene or the main scene.",
		"needs_project_path": true,
		"edits_scene": true,
		"refresh_poll": false,
		"arguments": [
		{"name": "scene_path", "flag": null, "positional": 0, "type": "string", "required": false, "default": null, "help": "res:// path of the scene to play; omit it to play the edited scene."},
		{"name": "main", "flag": "--main", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Play the project main scene instead of the edited scene."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## The project's configured main scene as a res:// path. A uid:// id is
## resolved the way the editor resolves it; an id the registry does not know
## resolves to an empty string, which the caller refuses as a missing scene.
static func _resolved_path(scene_path: String) -> String:
	if scene_path.begins_with("uid://"):
		return ResourceUID.uid_to_path(scene_path)
	return scene_path


## Returns the refusal message for a path that does not name a scene the
## editor can load, or an empty string when it does. `scene_path` is the path
## to load, `name` is how the caller referred to the scene and appears in the
## message. A missing file, a directory and a resource of another type all
## load as null here, so the refusal is decided before anything is launched
## and the editor's own error dialog never opens.
static func _scene_problem(scene_path: String, name: String, target: String) -> String:
	var packed := ResourceLoader.load(scene_path, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE) as PackedScene
	if packed == null:
		return "no such scene: %s (%s)" % [name, target]
	return ""

