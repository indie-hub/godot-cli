# Godot Pipeline plan

Godot Pipeline is a Rust CLI plus a GDScript EditorPlugin for stock Godot. An agent drives a running Godot editor through typed commands instead of a person clicking. The Unity command line (`unity command`, `unity list`) does the same for the Unity Editor. This plan says what the two tools do, what makes sense in Godot, and what is still open. It targets the same functionality as the Unity command line, not the same names.

## Status

- Read the command list with `godot-cli commands` (it needs a running editor; the plugin publishes the interface and the CLI reads it on every run).
- Read the version in `Cargo.toml`.
- Read the replay baseline in `tools/replay/baseline.jsonl`.
- Run `cargo test --locked` for the test count.
- Phase 1, read and navigate, is complete. Phase 2, Godot scene edits, is complete.
- Later commands (`list-resources`, `inspect-resource`, `console`, `set-parent` and `eval`) shipped after that; see `CHANGELOG.md`.
- `set-property` accepts 17 scalar types and ten `Packed*Array` types, plus a typed `Array[T]` of those types. It rejects untyped arrays, object and resource references, `Dictionary`, `Callable`, `Signal` and `RID`.
- The plugin assembles each request across editor ticks. It caps a request at 8 MiB (`MAX_REQUEST_BYTES` in `addon/godot_pipeline/editor_plugin.gd`) and drops an idle connection after 5 seconds (`REQUEST_IDLE_TIMEOUT_MSEC`).
- The add-on is a prototype and is not ready for distribution.

## Acceptance runs

The Milestone A (assemble) and Milestone B (run and check) acceptance narratives live in docs/history.md.

## How this plan was made, and how far to trust it

- The parity target is the functionality of the Unity command line. On 2026-10-13 the command list was read from a live Unity 6000.4.4f1 project: 142 commands, each with a name, tags, typed parameters and a JSON Schema. The reads changed nothing on the Unity side.
- Three rooms mapped the list in three chunks of 39, 50 and 53 commands. Each chunk was mapped by one room and then re-derived blind by a room from a different vendor.
- The rooms agreed on 106 commands. On 14 commands they differed only on how large the gap is: one room wrote `DIRECT`, the other wrote `ADAPT`, and both named the same mechanism. On 22 commands they differed on facts.
- The 22 contested commands were measured in a real Godot 4.7.2 editor. 10 of them were measured by two rooms each. 12 were measured by one room.
- `eval` was measured in a real Godot 4.7.2 editor by one room of one vendor. That room answered nine questions about running code and, in a second task, two questions about marking the scene unsaved and detecting a scene change. A second vendor re-ran the `eval` measurement. `eval_file` was measured by one vendor and has not been re-run.
- Every Godot class, method, signal and property in the map was checked by script against the Godot 4.8-dev engine documentation: 323 entries from the author rooms and 403 entries from the blind rooms, all resolved. The script proves that the API exists. It does not prove that the mapping is right.
- Evidence levels, with their counts from the table in `docs/unity-parity.md`: 11 rows are measured and replicated, 13 rows are measured by one vendor, and 118 rows are docs and source only. A `docs and source only` row is a reading of the Godot 4.8-dev documentation and the engine source. Nobody measured it in a 4.7.2 editor.
- The table under "What exists" lists commands that cover 30 of the 142 Unity commands, which are listed under "What exists".
- A row proposes scope. It is not a promise that a command exists or will work.
- An earlier version of this plan mapped a different Unity surface: an MCP server with 100 tools on port 3333. That surface is not the target. The earlier plan was wrong. Its table was removed, and it stays in git history.

## Decisions made

Decisions of 2026-09-29, kept with the same meaning:

| Question | Decision |
| --- | --- |
| Expose an MCP server? | Deferred until a host without a shell needs it. The CLI stays the interface. Every agent host in use today has a shell. |
| External writes (project settings, resource files, scripts, imports, exports) | Not built. Reads and scene edits ship first. The gates that must exist before the first write command are under "Disk writes and write gates". |
| Live game and debugger inspection | Deferred until play control and headless tests exist. |
| `open-scene` with a dirty scene | Without `--save` the target opens and the dirty scene stays open as a background tab; with `--save` only the edited scene is saved first. There is no discard option. The reply lists the scenes that still have unsaved changes in `unsaved`. |
| Governance gates on direct command-line calls | Not built. The Unity command line has no global switch. Its safety model is per call: `dry_run` on 40 commands and `confirm` on 29 commands. 18 of those 29 refuse the call without `confirm=true`, 7 need it only when the call would overwrite an existing file, and 4 accept it for parity and do not need it. Godot's gates are the per-command write classes under "Disk writes and write gates"; they are guardrails against an agent's accidents, not a security boundary. |

### Disk writes and write gates

The gates cover every named plugin command that changes a file on disk or a project setting except `eval`. They are guardrails against accidents by an agent that uses those commands, not a security boundary: `eval` refuses nobody, and in the measured editor the code it runs wrote a file outside the project. The plugin may write `project.godot` where this contract says so.

Every write by a covered named plugin command falls in exactly one class. `eval` and the editor's own activity are outside the gates and outside these classes.

- **W0, an edit to the edited scene.** It changes the scene in memory only, as one undoable editor action (rule 3); a WS save writes it.
- **WS, an ordinary save of an open scene to its own path.** Built: `save-scene` and `open-scene --save`; proposed and unbuilt: the named-scene save. It replaces that scene's file by design and needs no flag. `play` saves a dirty edited scene this way, under its rule-4 exception.
- **W1, a new file inside `res://`.** A command that creates a scene or a resource refuses unless the path is inside `res://`, has an allowed extension, has an existing parent folder, and does not exist when it validates. W1 has no overwrite flag.
- **W2, an overwrite or a delete of an existing file that is not a WS save and not W3.** Save-as onto an existing path, deleting a file and rewriting a resource file are examples. A command refuses unless given an explicit named flag (proposed and unbuilt: `--overwrite` and `--delete`). These writes have no undo, and one file can be used by many scenes; the reply lists the files it replaced or removed and the dependents it knows.
- **W3, project settings.** Staged, then applied explicitly, as below. The apply overwrites `project.godot` by contract and takes no W2 flag.

The reply fields are proposed and unbuilt: `written` (the paths it wrote), `affected` (the other files and scenes it found that reference what changed) with `affected_complete`, false whenever the command cannot establish that it found every dependent, `undoable` with `undo_scope` (what an undo would reverse), and `failed` and `uncertain` when a multi-file write is partial. `--dry-run` (proposed and unbuilt) validates the whole request and returns the intended paths and effects with `written` empty, and writes nothing. A validation failure never mutates. An I/O failure after some writes can leave a partial result: the contract promises no rollback.

**W3, input actions.** Edits are staged in plugin-owned data, never in live ProjectSettings and never in the editor's InputMap. In the tested fixtures a staged key reached `project.godot` while a plugin-registration save was pending, and the cause was not isolated. An explicit save there also wrote an unrelated changed setting, so a save can write other live changes, including settings the human changed in the editor; the 4.8-dev source skips hidden and unchanged-default properties. A staged key in live state is therefore not safe: the plugin does not control when the editor saves project settings. A read command (proposed and unbuilt) shows the staged edits. One apply command (proposed and unbuilt) writes them with `ProjectSettings.set_setting` and `ProjectSettings.save()`. Its reply states that the save can write other live changes, including settings the human changed in the editor, lists the keys it changed, and says that a hand-written `project.godot` can be normalised on the first save. Removal is `set_setting(name, null)` plus a save, because ProjectSettings has no `clear_setting`. Reading the applied state goes through ProjectSettings: in the tested editor a custom project action was absent from the editor's own InputMap after a fresh start, and the engine initialises the editor with default keys and the game with the project's (`main/main.cpp:2329-2335`, 4.8-dev source); a separately launched game reported the action present. A staged edit is discarded by an explicit discard command (proposed and unbuilt). The apply is `undoable: false` for the file on disk.

**No-overwrite limit.** A W1 command refuses when the target exists at the moment it validates and reports that refusal. It does not promise exclusivity, and it does not promise that the target is still absent when the bytes are written. In the sequenced test (existence check, an outside process creates the file, then the editor saves) both vendors saw the competing bytes lost. In 200 coordinated races one vendor saw both writers' bytes survive on different iterations. The enumerated 4.7.2 FileAccess and DirAccess method lists and flags contain no exclusive-create method or flag; the flag enum at `core/io/file_access.h:57-62` is 4.8-dev source. That is an API observation, not a statement about a GDExtension or an external process.

**Named-scene save instead of `save_all` (proposed and unbuilt).** The command saves the open scene named by a `res://` path, only if it has unsaved changes, to its own path. It refuses an untitled or never-saved scene and never opens a save-as dialog. It never stops a running game and never saves the default environment. The engine's `EditorInterface.save_all_scenes` instead saves a dirty scene to its own path, queues a dirty scene that is untitled or whose folder is missing for a save-as dialog, stops a running game, and calls a default-environment save that writes only when a valid fallback environment has a resource-file path (`editor_node.cpp:2614-2617`, `2662-2687`, `3096-3103`, 4.8-dev source, read not run), so the command does not use it. Unmeasured: whether a plugin can save a background (non-edited) open tab to its own path without making it the edited scene, and what that does to the open tabs and to the running game. That measurement is required before the command is built.

Decisions of 2026-10-13:

| Question | Decision |
| --- | --- |
| What is the parity target? | The functionality of the Unity command line (`unity command`, `unity list`). Not the names and not the arguments. Names were the wrong target in the earlier version. |
| How are new commands named? | In kebab-case, after the Godot mechanism. A command is not renamed after a Unity command. The Unity names are snake_case, for example `create_gameobject`. |
| Is an `eval` command wanted? | Yes. The command runs a snippet and reads the result, the way `eval` and `eval_file` do in the Unity command line. The decisions of 2026-10-14 below fix the design. |
| Where does the capability map live? | In `docs/unity-parity.md` in this repository, and in Basic Memory under the note `godot-pipeline-unity-cli-capability-map-142-commands`. |

Decisions of 2026-10-14, made after the measurements:

| Question | Decision |
| --- | --- |
| Where does `eval` run? | In the live editor, as the Unity command runs. Not in a separate process. |
| Which language does `eval` run? | GDScript first. C# follows later. |
| Who may call `eval`? | Nobody is refused. The Unity `eval` and `eval_file` take only the code, or a file, and a timeout in milliseconds with a default of 5000. They carry no `confirm` and no `dry_run`. |
| Is the timeout enforced? | The timeout is best effort. A runaway loop on the main thread cannot be stopped from inside the editor. |
| Does `eval` undo its own edits? | No. `eval` is an exception to rule 3 and to rule 6 in the rules section above. The rules section itself stays as it is. |
| What does the reply report? | The reply carries `scene_changed`. The plugin marks the scene unsaved when the value is true. |

CLI decisions of 2026-10-14:

| Question | Decision |
| --- | --- |
| How does the CLI learn the commands? | The plugin describes its commands through one interface (name, arguments with type, flag, positional index, required, default, and help); the CLI collects the interface from the running editor on every run, with no cache. No editor means no command list. |
| How are plugin commands run? | Plugin commands run through `call`: `godot-cli call <command> [arguments]`. The CLI has its own fixed commands: `commands` (lists the plugin commands), `--help`, and `--version`. Top-level plugin commands were rejected. The subcommand is `call` (not `command`); the discovery subcommand is `commands` (not `list`). |
| Where is an invocation validated? | The CLI validates statically before sending. The plugin still validates the whole request. |
| Which argument types exist? | Five: `string`, `boolean`, `json`, `res_path`, and `integer`. An integer argument is checked by the CLI before sending (a JSON integer literal that fits 64 bits). |
| What is the binary called? | The binary is renamed from `godot-pipeline` to `godot-cli`. The add-on folder `godot_pipeline`, `plugin.cfg`, and the product name `Godot Pipeline` stay. |
| Is old usage kept? | Nothing was released, so no compatibility was kept: old top-level invocations no longer exist; there is no shim. |
| How many commands moved at once? | The 17 commands that existed then were migrated all at once to the interface; `console`, `set-parent` and `eval` were added afterwards on the same interface. No hand-written command is left beside it (no hybrid). |

Decisions of 2026-10-14 and 2026-10-15:

| Question | Decision |
| --- | --- |
| What does `set-parent` keep? | Every node of the moved subtree keeps its exact owner. A node the edited scene saved before the move is still saved after it, and Undo restores owners exactly. |
| Is `keep_global_transform=false` reachable through the CLI? | Yes: `godot-cli call set-parent ... --keep-global-transform=false`. A boolean argument is written `--flag`, `--flag=true` or `--flag=false`. |
| What does `keep_global_transform` preserve? (implemented behaviour, not a recorded decision) | For a `Control` it preserves the global position only. A `Node2D` and a `Node3D` keep the full global transform. Every other node type ignores the flag. |
| Which edits of a base-inherited node are refused? | `rename-node`, `delete-node` and `set-parent` refuse a structural edit of a node inherited from a base scene. The plugin packs the edited scene and walks every level of `SceneState.get_base_scene_state()`. Property, script, group, signal and new-child edits of an inherited node stay allowed as overrides. |
| What does `eval` return as its value? | `eval` keeps the tagged `JSON.from_native` encoding as its only encoded value, and carries `display` beside it: the same value written as text with `var_to_str`, so a reader does not decode the tagged form by eye. `display` is null when the run did not complete, and also when the readable text is over the 1 MiB display cap; in the cap case the value and every other field are returned, `completed` stays true, and one added `errors` entry of type `warning` names the display size (a `PackedByteArray` of 225000 bytes encodes to 900035 bytes and displays 1125015 bytes). `completed` is false when the value cannot be encoded intact or is deeper than the limit. `errors` entries carry a type (`warning`, `error`, `script`, `shader`), and a warning leaves `completed` true with the value. |
| What is the `eval` depth limit? | `eval` has an explicit depth limit of 100 JSON levels, counted exactly: an untyped `Array` costs one level, a typed `Array` two, a `Dictionary` two, a tagged terminal value two, a `Callable`, an `RID` and a `Signal` one, and scalars none. Exactly 100 levels come back and 101 is refused. The real command line reads about 125 levels, so the margin is 25. |
| Are `Callable`, `RID` and `Object` results usable values? | They are documented as not returned as values. A `Callable` round-trips to an empty invalid `Callable`. An `RID` round-trips to a fresh `RID(0)`. An `Object` gives value null. |
| What does a scene-switching snippet guarantee? | A snippet that switches scenes is outside the dirty-marking guarantee. |
| Was `set-parent` accepted? | `set-parent` was accepted after repeated validation rounds. It was not independently validated as a final tree on its own. The combined tree that contains it passed an independent validation that re-ran a regression sample of its behaviour. |
| Was `eval` accepted? | `eval` was accepted after repeated validation rounds. Its depth count is exact: at most 100 JSON levels come back and 101 is refused. The combined tree that contains it passed an independent validation that re-ran a regression sample of its behaviour. |
| Should the inherited-node check move into the shared request guard? | No. The old refusal precedence stays: each structural command calls the shared helper after its own checks. A guard-level default that refuses first was rejected: a 94-request probe found 51 error-to-error precedence changes, and the check then ran for requests that used to fail cheaply. |
| Should docs/reference.md describe what editing a base scene does to a derived scene? | Yes. docs/reference.md describes that renaming or deleting a node in a base scene silently drops the override in a derived scene and detaches its local child; the plugin does not refuse the edit in the base scene. |

Queued follow-up tasks:

- `eval-readable-value`: done. `eval` carries `display` beside `value`, and the baseline holds 1222 rows.
- `harness-and-cost`: the `--sessions` filter is done. Measured: full replay 140.7 s, 298 bracket rows around 121 rejections, detector cost 52 ms at 5000 nodes with a base. Still open and unbuilt: bracket-row trim, table-driven matrix, skip-pack. Only the bracket-row trim changes baseline rows.

## Rules every new command must keep

1. Check the project path first, before any other check.
2. Validate the whole request before changing anything. A rejected request changes neither the scene nor the undo history.
3. Every scene edit is one editor Undo/Redo action in the edited scene root's history. A write to another file is not a scene edit and gets no scene undo.
4. A named plugin command writes a file or a setting only as its class under "Disk writes and write gates" allows: an ordinary save of an open scene to its own path is WS and needs no flag; a new file in `res://` is W1; an overwrite or a delete that is neither a WS save nor W3 is W2 and needs its named flag; project settings are W3, staged and applied explicitly. The built saves are `save-scene` and `open-scene --save`. `eval` and the editor's own activity are outside these gates. Three exceptions: `play` saves an unsaved edited scene before it launches the game; the editor itself rewrites the edited scene's file in its own saved form when a play starts and that file has not been saved in that form before, even though nothing was unsaved; and the plugin registers its `GodotPipelineCapture` game autoload with a guarded `add_autoload_singleton` on editor start, whose entry and whose removal on disable reach `project.godot` on disk with the next project-settings save (starting the game saves) — see `docs/reference.md` for `play` and the "Game helper" lines and the measured timing.
5. Coerce values strictly from the declared type. Never guess from the JSON.
6. Never block the editor main thread. Poll across frames or hand long work to a child process. A handler that cannot answer yet returns a pending reply instead: the plugin keeps the connection open, polls the handler's `poll` callable every frame from `_process`, answers when it completes or errors when its declared wait passes, and never blocks. While a reply is pending the request idle timeout does not fire, a disconnect cancels it silently, and the plugin answers no other client until it resolves.
7. Keep kebab-case command names. Keep the reply shape `{"status":"ok","data":...}` or `{"status":"error","message":...}`.
8. Godot persistence traps to respect: a node inside an instanced sub-scene is not saved unless Editable Children is enabled, and every node needs the right owner.
9. Prove each command in an isolated Godot 4.7.2 editor on an alternate port, never the live editor on 47821. Show: no mutation on a rejected request, no implicit save, correct Undo and Redo, and persistence after an explicit save and a fresh reload.

## The eval command

`eval` is built. `eval_file` is not built. This section says what the two Unity commands do, and what was measured in a Godot 4.7.2 editor by rooms of two vendors.

**What the Unity commands take.** Unity compiles C# with Roslyn. `eval` takes the source. `eval_file` takes a file. Both take a timeout in milliseconds, with a default of 5000. Both run in the live editor, need the main thread, and carry no `confirm` and no `dry_run`.

**How the two commands work.** The plugin reads the source, or reads the file as UTF-8 text with `FileAccess`. It then compiles the source, creates an instance, and calls a fixed entry function. That function receives the editor interface and the editor undo manager. The plugin returns the value as typed JSON, together with the captured errors and the captured output. Before and after the call the plugin fingerprints the edited scene tree. If the two fingerprints differ, the reply sets `scene_changed` to true and the plugin marks the scene unsaved. `eval_file` follows the same steps after the read. Which file paths it may read is not decided.

**What Godot can do.** A plugin can compile GDScript from a string at run time. It builds a `GDScript`, sets `source_code`, reloads it, creates an instance, and calls a fixed entry function. The script runs with or without the `@tool` annotation and reaches the real edited scene. The entry function receives the editor interface and the editor undo manager.

**Compiling is already an execution boundary.** Reloading the source runs the initializer of any `@tool` resource that the source preloads, before the entry function is called. A path check made before that point cannot see what the code goes on to instantiate.

**The timeout is best effort.** An endless loop on the main thread cannot be stopped from inside the editor. A `Thread` stays responsive but cannot be force-stopped. Only a child process killed with `OS.kill` gives a hard deadline. In five measured runs a child process started in a mean of 418.9104 ms and was gone a mean of 6.7484 ms after the kill. So the live timeout is best effort.

**Errors and the reply.** A `Logger` added with `OS.add_logger` captures the file, the line, the function and the message of a compile or runtime error. A null return alone is ambiguous. `JSON.from_native` and `JSON.to_native` keep the types when objects are off. A `Callable` result round-trips to an empty invalid `Callable`, not to null. Plain JSON turns an integer into a float. `var_to_str` is not a stable machine reply. A 1 MiB byte array expands above a 1 MiB reply cap.

**The code has the reach of the editor process.** In the measured editor the compiled code wrote a file outside the project, read a file, and started a program. The `Expression` class is not a read-only boundary. It ignores text after a valid expression, and `const_calls_only` did not stop a script mutation.

**C# has no working route yet.** Assigning `source_code` on a `CSharpScript` and reloading it did not compile or run C# in the tested 4.7.2 mono editor. External compilers were not tested. GDScript is the first language and C# follows later.

**The code is not confined.** Nobody is refused: there is no caller guard, as in the Unity command. There is no dry run either.

**The two exceptions to the rules.** Rule 3 requires one Undo/Redo action per scene edit. Rule 6 requires that the main thread never blocks. `eval` is an exception to both. Direct scene edits from the submitted code create no undo entry and do not mark the scene unsaved. One `EditorUndoRedoManager` action does. The code can hold the main thread for as long as it runs.

**What `scene_changed` can see.** The plugin fingerprints the live scene tree before and after the call. The fingerprint uses path, class, owner, script, groups, connections and stored properties. It found all ten tested edits: add with an owner, add without an owner, remove, rename, a property change, a reorder, a property change inside an instanced sub-scene, a signal connection, a group add, and a script attach. Repeated captures of an unchanged scene gave identical results. On a scene of 203 nodes the fingerprint took a mean of 20.5732 ms over five runs. A second vendor re-ran the fingerprint on a 203-node fixture and measured 20.9 to 25.0 ms.

**What `scene_changed` cannot see.** The fingerprint misses an edit that the code undoes before the call returns, state that is not stored, other open scenes, and a deep change inside a referenced resource. A false value does not prove that the code had no side effects. Marking the scene unsaved does not make ownerless nodes, or non-persistent signal connections and groups, saveable.

**What `scene_changed` replaced.** Comparing `PackedScene.pack` output was tried first. It missed the child without an owner, a property change inside an instanced sub-scene, a default signal connection, and a default group add. Different output paths gave false positives. It is not the detector.

**Marking the scene unsaved.** `EditorInterface.mark_scene_as_unsaved` works from a plugin on a saved scene and on a never-saved scene. It does not change the undo history, and it is idempotent. It marks even an unchanged scene dirty, so the plugin calls it only when a change was detected. A later `save_scene` writes a direct edit of an owned node to disk. A never-saved scene needs `save_scene_as` first.

**Evidence level.** `docs/unity-parity.md` shows `eval` at the class `ADAPT` and the evidence level measured and replicated, and `eval_file` at the class `ADAPT` and the evidence level measured, one vendor.

## What exists

The table names 20 of the built commands and the Unity commands each covers. The Unity command names are the `unity_command` values in [the parity map](docs/unity-parity.md).

| Godot Pipeline command | Unity commands it covers |
| --- | --- |
| `status` | `editor_status` |
| `scene-tree` | `get_scene_hierarchy` |
| `inspect-node` | `get_component_properties`, `get_serialized_fields` |
| `query-nodes` | `find_gameobjects` |
| `inspect-class` | none. Godot-only. |
| `list-resources` | `list_shaders`, `find_assets` |
| `inspect-resource` | `get_material_properties` |
| `open-scene` | `open_scene`, `set_active_scene` |
| `rename-node` | `rename_gameobject` |
| `create-node` | `add_component`, `create_gameobject`, `create_gameobjects` |
| `set-property` | `set_active`, `set_component_properties`, `set_layer`, `set_transform`, `set_serialized_field` |
| `delete-node` | `delete_gameobject`, `remove_component` |
| `connect-signal` | none. Godot-only. |
| `set-group` | `set_tag`, `set_tags_layers` |
| `set-unique-name` | none. Godot-only. |
| `instantiate-scene` | `instantiate_prefab` |
| `save-scene` | `save_scene` |
| `console` | `console`, `get_console_logs`, `clear_console` |
| `set-parent` | `set_parent` |
| `eval` | `eval` (`eval_file` is not covered) |

22 of the 25 built commands cover 34 of the 142 Unity command rows. Three built commands, `inspect-class`, `connect-signal` and `set-unique-name`, cover no Unity command, because the Unity command list has nothing like them. They are the Godot-only capabilities marked as built below.

## The parity map in numbers

`docs/unity-parity.md` holds the full map, one row per Unity command, with the class, the evidence level, the Godot mechanism and the gaps. Every number below was recomputed from the rows of that table.

`docs/unity-parity.md` is the detail page. Its rows are written and edited by hand. The consolidated capability map lives in a session scratchpad that this repository does not keep, so there is no generator and no data file to regenerate the page from.

### By class

| Class | Rows |
| --- | --- |
| `DIRECT` | 9 |
| `ADAPT` | 117 |
| `GODOT_NATIVE` | 1 |
| `UNITY_ONLY` | 15 |
| total | 142 |

`DIRECT` means a Godot equivalent with the same shape. `ADAPT` means the same goal with a different Godot model. `GODOT_NATIVE` means the engine command line does it already. `UNITY_ONLY` means Godot has no such subsystem.

### By evidence level

| Evidence level | Rows |
| --- | --- |
| measured and replicated | 11 |
| measured, one vendor | 13 |
| docs and source only | 118 |
| total | 142 |

### By Unity group

| Group | Commands | Class split |
| --- | --- | --- |
| baking | 17 | 11 `ADAPT`, 6 `UNITY_ONLY` |
| settings | 16 | 16 `ADAPT` |
| animation | 14 | 14 `ADAPT` |
| gameobjects | 14 | 12 `ADAPT`, 2 `DIRECT` |
| assets | 12 | 8 `ADAPT`, 4 `DIRECT` |
| scripts | 10 | 10 `ADAPT` |
| scenes | 9 | 8 `ADAPT`, 1 `DIRECT` |
| build | 8 | 5 `ADAPT`, 1 `GODOT_NATIVE`, 2 `UNITY_ONLY` |
| editor | 7 | 2 `DIRECT`, 4 `ADAPT`, 1 `UNITY_ONLY` |
| prefabs | 7 | 7 `ADAPT` |
| observability | 6 | 4 `ADAPT`, 2 `UNITY_ONLY` |
| packages | 6 | 5 `ADAPT`, 1 `UNITY_ONLY` |
| materials | 4 | 4 `ADAPT` |
| tests | 4 | 1 `ADAPT`, 3 `UNITY_ONLY` |
| navigation | 3 | 3 `ADAPT` |
| capture | 3 | 3 `ADAPT` |
| authoring | 2 | 2 `ADAPT` |

The 17 groups add up to 142.

### The 9 `DIRECT` commands

`create_folder`, `delete_asset`, `delete_gameobject`, `editor_focus`, `editor_status`, `get_scene_hierarchy`, `read_text_file`, `rename_gameobject`, `write_text_file`.

Four of them are covered by a built command. `delete_gameobject` is covered by `delete-node`, `editor_status` by `status`, `get_scene_hierarchy` by `scene-tree`, and `rename_gameobject` by `rename-node`.

### Near-direct rows

14 rows are near-direct. The two rooms named the same Godot mechanism but disagreed on the size of the gap, so the map keeps the stricter class `ADAPT` and marks the row near-direct. The rows are the 14 contested on strictness. `docs/unity-parity.md` shows them as `ADAPT (near-direct)`.

### The order of work

A proposed sequence is in [Phase 4: complete a playable authoring workflow](docs/phase-4.md). It prioritises resources, script attachment, play/stop and observation, then scene creation and selected settings. It does not override this plan's command rules or deferred external-write boundary. Use `docs/unity-parity.md` to read one command at a time.

## Godot-only capabilities

These are Godot concepts with no counterpart in the Unity command list. They are not renamed Unity commands. The rooms reported 28 such capabilities, which merge into the 17 items below, because several rooms described the same capability more than once.

| Capability | State |
| --- | --- |
| Persistent signal connections with flags (persist, deferred, one-shot) | built: `connect-signal` |
| Node groups as persistent multi-membership sets | built: `set-group` |
| Owner-scoped unique names (`%Name`) | built: `set-unique-name` |
| Class reflection (properties, methods, signals) without constructing objects | built: `inspect-class` |
| Editor file-system inventory with UID identity and scan control | built: `list-resources` |
| Multi-scene tab model with per-scene dirty tracking | partly built: `open-scene` reports unsaved scenes |
| Scene inheritance, Editable Children and placeholders for instances | `instantiate-scene` exists; inheritance and Editable Children are not built |
| Feature-tag-specific project setting overrides | not built (`ProjectSettings.get_setting_with_override`) |
| Mount a `.pck` or `.zip` resource pack into `res://` at run time | not built (`ProjectSettings.load_resource_pack`) |
| Named animation libraries on a player and `AnimationTree` blend graphs | not built |
| Navigation: push-based bake completion signal, runtime server inspection and debug drawing | not built |
| Export pack, zip and patch outputs from a preset | not built; the engine command line has `--export-pack` and `--export-patch` |
| Per-viewport render statistics paired with captures | not built |
| Register named commands in the editor command palette | not built |
| Editor preferences as readable and writable settings | not built (`EditorSettings`) |
| Resource and mesh previews generated by the editor | not built |
| Open a resource, script or object in the inspector or script editor | not built |

## Architecture notes

- **Command interface.** The plugin publishes the interface of its commands and the CLI reads it on every run; see [the command interface](docs/reference.md#command-interface).
- **MCP facade (deferred).** If one is built, make it a Rust stdio adapter over the same commands, with the plugin staying the authority on the scene. Do not put an MCP HTTP server inside the GDScript plugin: it would add protocol and concurrency work to the editor thread. Do not expose a command before the command underneath it exists. The earlier plan listed a facade tool list as the target of this parity work; that surface is not the target.
- **Batches.** Unity's `changes/apply` applies a ChangeSet in one undo step. The Godot equivalent is one `EditorUndoRedoManager` action that holds many operations. A batch must validate every step against the resulting state before it opens the action. Defer this until the small commands and a preflight model exist. Disk writes, imports, exports and tests can never join a scene undo action.
- **Governance (deferred).** The Unity command line has no global switch. Its safety is per call: `dry_run` on 40 commands and `confirm` on 29 commands, and 18 of those 29 refuse the call without `confirm=true`. That model fits Godot's rule that a request is validated whole before anything changes. Godot's gates are the per-command write classes under "Disk writes and write gates", checked by the plugin; the earlier sketch put the grants in per-user `EditorSettings` and came from the wrong reference.
- **Long work.** A child Godot process started with `OS.execute_with_pipe` can be polled, its output can be read, and it can be killed with `OS.kill`. The engine's own flags `--headless`, `--script` and `--export-release` run outside the editor. An endless loop inside the editor process cannot be interrupted: the editor stops replying. Run long work in a child process, return an operation ID, and poll from the Rust side. A client timeout is not a cancellation.
- **Editor-to-game debugger channel.** The editor talks to the running game over an `EditorDebuggerSession`. Messages such as `scene:suspend_changed` arrive on that channel, and `EngineDebugger` can capture data from the game side. Both were measured in 4.7.2. The game project still needs a small capture script to send images.
- **Package search.** A plugin can search the official asset store with `HTTPRequest`. The Unity command line covers the same need with `package_search` and the other package commands. Query grammar, paging and offline cache differ, and there is no offline cache.
- **Errors.** Add optional stable `code` and `details` fields to error replies. Do not replace the existing shape.

## Open measurements

A row needs a measurement when at least one room wrote `needs_measurement` in the consolidated map and the row has no finished measurement. 21 rows are in that state.

- `add_scene_to_build`, `remove_scene_from_build`
- `add_animator_layer`, `add_animator_parameter`
- `copy_asset`, `import_asset`, `move_asset`, `rename_asset`, `set_import_settings`
- `create_prefab_variant`, `revert_prefab_overrides`, `save_prefab_contents`, `unpack_prefab`
- `recompile`, `recompile_status`, `reload_file`, `reload_file_override`
- `get_build_settings`, `set_build_settings`, `list_build_profiles`, `list_build_targets`

That is 21 Unity commands. The other 97 rows are docs and source only, and neither room marked them as needing a measurement.

- A second vendor re-ran the `eval` measurement. `eval` is now measured and replicated. `eval_file` still has a measurement from one vendor.

## Open questions

- The order of work. Nothing in this plan fixes it.
- Which file paths may `eval_file` read? Nothing is decided. The measured code could read and write outside the project, and no caller is refused.
- Should the exceptions to rule 3 and rule 6 be written into the rules section itself, instead of being stated in the `eval` section?
- How would a C# `eval` be compiled? No working route was found in the tested editor, and external compilers were not tested.
- Governance gates: the write classes decide by command on the command line. Whether a later interface repeats them is open; nothing else is built.
- External writes: when do project settings, resource files, scripts, imports and exports start? No external-write command is built, and [Phase 4](docs/phase-4.md) lists the measurements each one needs first.
- The `commands` discovery subcommand, which prints every command with its argument schema as JSON, exists: the plugin publishes the schema and `godot-cli` reads it from the running editor, with no cache.
- How much measurement does a `docs and source only` row need before it is built?
- The 15 `UNITY_ONLY` rows: is a Godot-flavored alternative wanted for each, or is the gap accepted?
