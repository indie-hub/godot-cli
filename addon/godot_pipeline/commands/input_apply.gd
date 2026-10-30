@tool
extends RefCounted

const CommandSupport := preload("command_support.gd")
const InputEvents := preload("input_events.gd")

## The editor-interface metadata key that holds the staged input actions.
## `input-stage` writes it; this command, `input-read` and `input-discard` read
## it; this command removes it after a successful save. The store dies with the
## editor process, so a staged edit never survives a restart. It is a
## Dictionary of action name -> {"deadzone": float,
## "events": Array of InputEvent, "remove": bool}.
const STAGING_META := "godot_pipeline_input_staging"

## Every apply and dry run says this in its reply: one save writes the whole
## live project settings, so the file can change beyond the staged actions.
const SAVE_WARNING := "ProjectSettings.save() writes every live project setting, including settings changed in the editor that were not yet saved, so project.godot may contain more than the staged actions; a hand-written project.godot can be normalized on the first save"


## Writes the staged input actions to `project.godot` with one
## `ProjectSettings.save()`, or validates them without writing anything.
##
## `request` must carry string field `project_path` (the canonical,
## symlink-resolved absolute path of the project the caller intends to write)
## and may carry boolean field `dry_run` (default false). The field checks
## run first, then the project-path mismatch, then the unknown-field check,
## the empty-store and malformed-entry checks, and the dry-run-or-apply;
## nothing is written and the store is untouched until every check has passed.
##
## Each staged entry sets `input/<name>` to {"deadzone", "events"} from real
## InputEvent objects, or removes the setting when its `remove` flag is true
## (`ProjectSettings` has no clear function, so a removal is a null set). Every
## entry is validated before the first live value changes, so a malformed store
## is refused with nothing written and nothing staged lost.
##
## The save is a whole-file write with no undo, and it can also hold unrelated
## live settings the editor changed but did not yet save. A failed save
## restores the previous live value of every key this call set and keeps the
## staging store, because the file may then be partly written. A successful
## save clears the staging store. Nothing here touches the editor's InputMap,
## saves a scene, or starts or stops a game, so no undo step is created.
static func run(plugin: EditorPlugin, request: Dictionary) -> Dictionary:
	var project_path_value: Variant = request.get("project_path")
	var dry_run_value: Variant = request.get("dry_run", false)
	if typeof(project_path_value) != TYPE_STRING:
		return CommandSupport.error("input_apply requires a string field: project_path")
	if typeof(dry_run_value) != TYPE_BOOL:
		return CommandSupport.error("input_apply requires a boolean field: dry_run")

	var project_mismatch := CommandSupport.project_path_mismatch(project_path_value)
	if project_mismatch != "":
		return CommandSupport.error(InputEvents.escape_text(project_mismatch))
	for key in request.keys():
		if not str(key) in ["command", "project_path", "dry_run"]:
			return CommandSupport.error("unknown field: %s" % InputEvents.escape_text(str(key)))

	var editor := plugin.get_editor_interface()
	var staged: Variant = _staged(editor)
	if staged is String:
		return CommandSupport.error(staged)
	var store: Dictionary = staged
	if store.is_empty():
		return CommandSupport.error("nothing is staged: stage an action with input-stage before applying")
	var malformed := _malformed_entry(store)
	if malformed != "":
		return CommandSupport.error(malformed)

	var changed: Array = []
	for name_value in store:
		var entry: Dictionary = store[name_value]
		changed.append({
			"action": name_value,
			"change": "remove" if bool(entry.get("remove", false)) else "set",
		})

	var dry_run: bool = dry_run_value
	if not dry_run:
		var previous := {}
		for name_value in store:
			var key := "input/" + str(name_value)
			# A key that did not exist reads as null, and a null set removes it
			# again, so the same restore works for both cases.
			previous[key] = ProjectSettings.get_setting(key)
			var entry: Dictionary = store[name_value]
			if bool(entry.get("remove", false)):
				ProjectSettings.set_setting(key, null)
			else:
				ProjectSettings.set_setting(key, {"deadzone": entry["deadzone"], "events": entry["events"]})
		var error := ProjectSettings.save()
		if error != OK:
			for key in previous:
				ProjectSettings.set_setting(key, previous[key])
			return CommandSupport.error(
				"ProjectSettings.save() failed: %s; the live settings were restored to their previous values and the staging store is kept, but project.godot may be partly written" % error_string(error)
			)
		editor.remove_meta(STAGING_META)

	return CommandSupport.ok({
		"written": [] if dry_run else ["res://project.godot"],
		"changed": changed,
		"dry_run": dry_run,
		"staged_cleared": not dry_run,
		"undoable": false,
		"undo_scope": "none: the disk write has no undo in the editor",
		"warning": SAVE_WARNING,
	})


## The interface a client reads for this command: its name, what it does,
## and its arguments with type, default and help.
static func interface() -> Dictionary:
	return {
		"name": "input_apply",
		"description": "Write the staged input actions to project.godot.",
		"needs_project_path": true,
		"edits_scene": false,
		"refresh_poll": false,
		"arguments": [
		{"name": "dry_run", "flag": "--dry-run", "positional": null, "type": "boolean", "required": false, "default": false, "help": "Validate the staged actions and write nothing."},
		{"name": "project_path", "flag": "--project-path", "positional": null, "type": "string", "required": true, "default": null, "help": "Absolute path of the target project; canonicalized before sending."},
		],
	}


## The staged store, or the refusal message when the metadata does not hold a
## Dictionary. An absent store reads as an empty one.
static func _staged(editor: EditorInterface) -> Variant:
	if not editor.has_meta(STAGING_META):
		return {}
	var value: Variant = editor.get_meta(STAGING_META)
	if typeof(value) != TYPE_DICTIONARY:
		return "the staging store is not a dictionary; nothing was applied"
	return value


## The refusal message for the first staged entry that is not well formed, or
## an empty string when every entry is. Validation runs before any live value
## changes, so a store a caller corrupted or an unfinished writer left refuses
## the whole apply instead of writing part of it.
static func _malformed_entry(store: Dictionary) -> String:
	for name_value in store:
		if not InputEvents.is_valid_action_name(name_value):
			return "staged action name is invalid: %s; nothing was applied" % InputEvents.escape_text(str(name_value))
		var name: String = name_value
		var entry_value: Variant = store[name_value]
		if typeof(entry_value) != TYPE_DICTIONARY:
			return "staged entry is malformed for action %s: it is not a dictionary; nothing was applied" % InputEvents.escape_text(name)
		var entry: Dictionary = entry_value
		var deadzone: Variant = entry.get("deadzone")
		if typeof(deadzone) != TYPE_INT and typeof(deadzone) != TYPE_FLOAT:
			return "staged entry is malformed for action %s: deadzone must be a number from 0.0 to 1.0; nothing was applied" % InputEvents.escape_text(name)
		if float(deadzone) < 0.0 or float(deadzone) > 1.0:
			return "staged entry is malformed for action %s: deadzone must be a number from 0.0 to 1.0; nothing was applied" % InputEvents.escape_text(name)
		var remove: Variant = entry.get("remove", false)
		if typeof(remove) != TYPE_BOOL:
			return "staged entry is malformed for action %s: remove must be a boolean; nothing was applied" % InputEvents.escape_text(name)
		var events: Variant = entry.get("events")
		if typeof(events) != TYPE_ARRAY:
			return "staged entry is malformed for action %s: events must be an array of InputEvent objects; nothing was applied" % InputEvents.escape_text(name)
		if bool(remove) and not (events as Array).is_empty():
			return "staged entry is malformed for action %s: a removal entry must have no events; nothing was applied" % InputEvents.escape_text(name)
		for event in (events as Array):
			if not (event is InputEvent):
				return "staged entry is malformed for action %s: events must be an array of InputEvent objects; nothing was applied" % InputEvents.escape_text(name)
	return ""
