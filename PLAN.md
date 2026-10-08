# Godot Pipeline plan: parity with the Unity MCP bridge

Godot Pipeline is a Rust CLI plus a GDScript EditorPlugin for stock Godot. An agent drives a running Godot editor through typed commands instead of a person clicking. The Unity MCP bridge (`mcp-unity`) does the same for the Unity Editor with 100 tools. This plan compares the two, says what makes sense in Godot, and sets an order of work.

## Status

- Fourteen commands exist: `status`, `scene-tree`, `rename-node`, `create-node`, `set-property`, `delete-node`, `connect-signal`, `set-group`, `set-unique-name`, `save-scene`, `inspect-node`, `query-nodes`, `inspect-class`, `open-scene`. See `README.md`.
- `set-property` accepts 17 value types, ten Packed array types, and typed `Array[T]`. It rejects untyped arrays on purpose.
- The plugin assembles each request across editor ticks, with an 8 MiB cap and a 5 second idle timeout.
- Current version is 0.5.0. Phase 1 (read and navigate) is complete.
- The golden replay harness in `tools/replay/` covers all fourteen commands (413 baseline rows).
- The add-on is a prototype and is not ready for distribution.

## How this plan was made, and how far to trust it

- The Unity side is ground truth. All 100 tool descriptions and input schemas were read from the running Unity server's `tools/list`.
- The Godot claims were checked against the Godot 4.8-dev source (`doc/classes/*.xml` and C++). The editor that runs the plugin is Godot 4.7.2. Behavior in 4.7.2 is unverified unless an existing command already exercises it. Prove a new API in an isolated 4.7.2 editor before building on it.
- One room wrote the plan and a room from a different vendor reviewed it. The reviewer checked 33 of the 100 rows, 44 Godot claims, and 12 Godot-only capabilities, and found no wrong claim.
- A table row proposes scope. It is not a promise that a command exists or will work.

## Decisions made (2026-09-29)

| Question | Decision |
| --- | --- |
| Expose an MCP server? | Deferred until a host without a shell needs it. The CLI stays the interface. Claude Code and Codex both have a shell. |
| Governance gates on direct CLI calls | Deferred. Decide when the gates are designed. |
| `open-scene` with a dirty scene | Without `--save` the target opens and the dirty scene stays open as a background tab; with `--save` only the edited scene is saved first. There is no discard option. The reply lists the scenes that still have unsaved changes in `unsaved`. |
| External writes (project settings, resource files, scripts, imports, exports) | Not yet. Ship reads and scene edits first. Add external writes later, after governance gates exist. |
| Live game and debugger inspection | Deferred until play control and headless tests exist. |

Why external writes wait: scene edits go through the editor's undo stack, so Ctrl+Z reverses them. Writes to `project.godot`, `.tres` and `.res` files, scripts, imports, and exports go straight to disk with no undo, and one edit can affect many scenes (a shared material file, for example). Real safety for those needs gates such as "overwrite is off by default", and those do not exist yet.

## Rules every new command must keep

1. Check the project path first, before any other check.
2. Validate the whole request before changing anything. A rejected request changes neither the scene nor the undo history.
3. Every scene edit is one editor Undo/Redo action in the edited scene root's history.
4. Nothing saves unless `save-scene` runs.
5. Coerce values strictly from the declared type. Never guess from the JSON.
6. Never block the editor main thread. Poll across frames or hand long work to a child process.
7. Keep kebab-case command names. Keep the reply shape `{"status":"ok","data":...}` or `{"status":"error","message":...}`.
8. Godot persistence traps to respect: a node inside an instanced sub-scene is not saved unless Editable Children is enabled, and every node needs the right owner.
9. Prove each command in an isolated Godot 4.7.2 editor on an alternate port, never the live editor on 47821. Show: no mutation on a rejected request, no implicit save, correct Undo and Redo, and persistence after an explicit save and a fresh reload.

## Roadmap

**Phase 1: read and navigate.** A small release built on the current commands. No new value coercion and no file writing.

1. `inspect-node`: typed node and property read. Done in 0.5.0.
2. `query-nodes`: bounded scene search. Done in 0.5.0.
3. `open-scene`: open a scene by path. A dirty edited scene stays open as a background tab unless `--save` saves it first; the reply lists the scenes that still have unsaved changes. Done in 0.5.0.
4. `inspect-class`: ClassDB property, method, and signal discovery. Done in 0.5.0.

**Phase 2: Godot scene edits.** Build after Phase 1 discovery exists and each behavior is proved in a throwaway editor.

5. `connect-signal`: serialized signal wiring. Done on the current working tree (next release).
6. `set-group`: persistent node group membership. Done on the current working tree (next release).
7. `set-unique-name`: `%` name within owner scope. Done on the current working tree (next release).
8. `instantiate-scene`: add a PackedScene instance with correct ownership.

**Phase 3: resources and the project boundary.**

9. `list-resources`: paged editor resource inventory.
10. `inspect-resource`: typed resource read.
11. Then consider, in this order, and only behind explicit gates: resource creation and save, InputMap edits, project settings writes, play control, headless checks and export, and an MCP facade if a host needs one.

**Proposed, not yet approved:** a `commands` subcommand that prints every command with its argument schema as JSON. It gives an agent discovery without an MCP layer.

## Architecture notes

- **MCP facade (deferred).** If one is built, make it a Rust stdio adapter over the same commands, with the plugin staying the authority on the scene. Do not put an MCP HTTP server inside the GDScript plugin: it would add protocol and concurrency work to the editor thread. Do not expose 100 tools before the commands underneath exist. The Unity `tools/list` response is about 81 KB, a large context cost per session.
- **Batches.** Unity's `changes/apply` applies a ChangeSet in one undo step. In Godot the equivalent is one `EditorUndoRedoManager` action holding many operations. A batch must validate every step against the resulting state before it opens the action. Defer this until the small commands and a preflight model exist. Disk writes, imports, exports, and tests can never join a scene undo action.
- **Governance (deferred).** Unity gates deletes, overwrites, builds, and package imports behind flags that default off. Godot should keep operator grants in per-user `EditorSettings`, default off, and never in the shared `project.godot`. Gate before detailed argument validation.
- **Long work.** Run `--check-only`, `--script`, and `--export-release` as separate headless Godot processes started by the Rust side. Return an operation ID and poll. Keep editor-only work frame-driven. A client timeout must not be treated as cancellation.
- **Large requests and replies.** The plugin already handles requests up to 8 MiB. A request of several megabytes is handled in one tick and pauses the editor for roughly 0.2 to 0.5 seconds. The real fix, if it ever matters, is to validate and apply in slices across ticks. Bound future replies with pagination, log limits, and file references for large or binary data.
- **Errors.** Add optional stable `code` and `details` fields to error replies. Do not replace the existing shape.

## Coverage of the 100 Unity tools

15 DIRECT, 54 ADAPT, 4 GODOT-NATIVE, 17 SKIP, 10 DEFER.

- DIRECT: a close Godot equivalent.
- ADAPT: the same goal, but Godot's model differs.
- GODOT-NATIVE: Godot's own CLI or a built-in workflow already does it, so no command is needed.
- SKIP: Unity-specific, or product policy that does not belong in a bridge.
- DEFER: makes sense, but a prerequisite or public API is unresolved.

The largest adapted groups are GameObject and component to node and script, prefab to `PackedScene` and scene inheritance, `InputActionAsset` to `InputMap` and project settings, and AnimatorController to `AnimationTree`. The Unity package installer, `EventSystem`, `PlayerInput` component, and scaffold templates get no Godot command.

Run site is PLUGIN (inside the editor), CLI-only (Rust side, no editor call), or headless (a separate Godot process). Effort is S, M, or L. Priority is P0 to P3.

Source keys in the tables point to Godot 4.8-dev files: E = `EditorInterface`, U = `EditorUndoRedoManager`, N = `Node` and `Object`, F = `EditorFileSystem` and `EditorFileSystemDirectory`, R = `ResourceLoader`, `ResourceSaver`, `Resource`, `ResourceUID`, S = `ProjectSettings` and `EditorSettings`, C = `ClassDB`, I = `InputMap` and `Input`, A = the Animation classes, V = `Viewport`, `Image`, `Performance`, G = the command line flags in `main/main.cpp`, T = `TileMapLayer` and `TileSet`, H = `Theme`, `Control`, `BaseButton`, D = the language server, debug adapter, and `EditorDebuggerPlugin`, L = `LightmapGI`, O = `PackedScene` and `SceneTree`, Q = `Shader` and `VisualShader`, Z = `TranslationServer` and `Translation`, P = this repository.

## Unity tool mapping

| Unity tool | What it does | Disposition | Godot mechanism | Run site | Effort | Priority | Risk or Godot trap |
|---|---|---|---|---|---|---|---|
| unity/console/tail | Filter recent editor log entries. | ADAPT | Editor log access UNVERIFIED; bounded process output [G] | CLI-only | M | P1 | Editor history may not be exposed as a public API. |
| unity/editor/state | Read editor state. | DIRECT | `EditorInterface` scene and play getters [E] | PLUGIN | S | P0 | Distinguish edited from playing scene. |
| unity/health | Read a lightweight health snapshot. | DIRECT | Existing `status` [P] plus `EditorFileSystem.is_scanning` [F] | PLUGIN | S | P0 | Do not imply compilation state. |
| unity/screenshot/capture | Capture game or scene view PNG. | DEFER | `Viewport.get_texture`, `Image.save_png` [V]; editor view capture UNVERIFIED | PLUGIN | L | P2 | Focus, renderer, and large binary reply. |
| unity/project/info | Read project identity and paths. | DIRECT | Existing `status` [P], `ProjectSettings` [S] | PLUGIN | S | P0 | Keep project-path guard. |
| unity/project/ensure_agent_workspace | Create a standard agent folder. | SKIP | Plain project file operation; no Godot-specific API needed | CLI-only | S | P3 | Fixed folder convention is a Unity bridge choice. |
| unity/play/state | Read play state. | DIRECT | `EditorInterface.is_playing_scene` [E] | PLUGIN | S | P1 | Editor and remote trees differ. |
| unity/play/enter | Start play mode. | ADAPT | `EditorInterface.play_current_scene` or `play_main_scene` [E] | PLUGIN | S | P1 | Select current versus main explicitly. |
| unity/play/exit | Stop play mode. | DIRECT | `EditorInterface.stop_playing_scene` [E] | PLUGIN | S | P1 | Stop must report final output. |
| unity/play/run_for_seconds | Run play for a bounded interval. | ADAPT | `EditorInterface.play_current_scene`, `stop_playing_scene` [E] | PLUGIN | M | P2 | Use frame timer; do not block editor. |
| unity/play/inject_input | Queue synthetic runtime input events. | DEFER | `Input.parse_input_event` [I] in target runtime; bridge UNVERIFIED | PLUGIN | L | P3 | Editor process is not game process. |
| unity/tests/run | Run filtered edit/play tests and report progress. | ADAPT | `godot --headless --path --script` [G] with project test script | headless | M | P1 | No built-in Unity Test Framework equivalent. |
| unity/assets/guid_to_path | Resolve an asset GUID to a path. | ADAPT | `ResourceUID` [R] for Godot UIDs | PLUGIN | S | P2 | Not every file has a UID. |
| unity/assets/path_to_guid | Resolve a path to an asset GUID. | ADAPT | `ResourceUID` [R] for resource UIDs | PLUGIN | S | P2 | UID assignment is not a read-only lookup. |
| unity/assets/find | Search assets by filters. | ADAPT | `EditorFileSystem` tree [F], `ResourceLoader` [R] | PLUGIN | M | P1 | Unity asset labels have no Godot counterpart. |
| unity/assets/get_info | Read asset metadata. | ADAPT | `EditorFileSystemDirectory` [F], `ResourceLoader` [R] | PLUGIN | M | P1 | Imported and source file differ. |
| unity/assets/list_folder | List folder assets. | DIRECT | `EditorFileSystemDirectory` [F] | PLUGIN | S | P1 | Page large folders. |
| unity/assets/get_dependencies | Read resource dependencies. | DIRECT | `ResourceLoader.get_dependencies` [R] | PLUGIN | S | P2 | Imported resource paths can differ. |
| unity/assets/wait_for_import | Wait for import to settle. | ADAPT | `EditorFileSystem.is_scanning` [F] | PLUGIN | M | P2 | Scan completion may not imply all imports settled; UNVERIFIED. |
| unity/assets/import_package | Import a Unity package. | SKIP | `.unitypackage` has no Godot meaning | CLI-only | S | P3 | Do not unpack unknown archives into project. |
| unity/assets/refresh | Trigger asset discovery. | DIRECT | `EditorFileSystem.scan` [F] | PLUGIN | S | P1 | Can trigger expensive project scan. |
| unity/assets/probe | Compare file and asset database state. | ADAPT | `FileAccess` and `DirAccess`; `EditorFileSystem` [F] | PLUGIN | M | P2 | Correct disk-vs-import timing needed. |
| unity/material/create | Create a material asset. | ADAPT | `ResourceSaver.save` with `Material` [R] | PLUGIN | M | P2 | Disk mutation is not scene undo. |
| unity/material/get_info | Inspect material properties. | ADAPT | `ResourceLoader.load`, `Object.get_property_list` [R,N] | PLUGIN | S | P2 | Shader parameters need type discovery. |
| unity/material/set_properties | Set material fields. | ADAPT | `Resource` property edit plus `ResourceSaver.save` [R,N] | PLUGIN | M | P2 | Shared resource affects multiple nodes. |
| unity/material/create_and_assign | Create material and assign renderer slot. | DEFER | `ResourceSaver.save` [R] plus node property undo [U,N] | PLUGIN | L | P3 | Scene and disk cannot be one undo transaction. |
| unity/material/ensure_palette | Create several color materials. | SKIP | Repeated material creation; no core command | CLI-only | S | P3 | Template policy belongs outside bridge. |
| unity/prefab/create | Save scene object as reusable asset. | ADAPT | `PackedScene.pack`, `ResourceSaver.save` [O,R] | PLUGIN | M | P2 | Ownership and nested instances control persistence. |
| unity/prefab/create_variant | Create prefab variant. | ADAPT | Inherited `PackedScene`; creation API UNVERIFIED [O] | PLUGIN | L | P3 | Inheritance differs from Unity variant overrides. |
| unity/prefab/instantiate | Add reusable asset to scene. | ADAPT | `PackedScene.instantiate`, `Node.add_child`, `Node.owner` [O,N] | PLUGIN | M | P1 | Editable Children and owner rules. |
| unity/prefab/get_source | Find instance source asset. | ADAPT | `Node.scene_file_path` [N] | PLUGIN | S | P1 | Child nodes may not own source path. |
| unity/prefab/apply | Apply instance edits to source. | DEFER | `PackedScene` and resource save [O,R]; override API UNVERIFIED | PLUGIN | L | P3 | Source edit can affect all instances. |
| unity/prefab/revert | Revert instance overrides. | DEFER | `EditorInterface.reload_scene_from_path` [E] is whole-scene; per-instance revert UNVERIFIED | PLUGIN | L | P3 | Do not discard unrelated dirty edits. |
| unity/prefab/unpack | Detach instance to ordinary nodes. | DEFER | `Node.scene_file_path`, ownership [N]; safe unpack API UNVERIFIED | PLUGIN | L | P3 | Preserve descendants and inherited overrides. |
| unity/scene/get_active | Read edited scene identity. | DIRECT | `EditorInterface.get_edited_scene_root` [E] | PLUGIN | S | P0 | Root may be absent. |
| unity/scene/open | Open scene by path. | DIRECT | `EditorInterface.open_scene_from_path` [E] | PLUGIN | S | P0 | Without `--save` a dirty scene stays open as a background tab; `--save` saves only the edited scene first. |
| unity/scene/save | Save active scene. | DIRECT | Existing `save-scene`, `EditorInterface.save_scene` [P,E] | PLUGIN | S | P0 | Existing path only; no implicit save. |
| unity/scene/list_in_build_settings | List build scenes. | ADAPT | `ProjectSettings` main scene [S]; all scene files via [F] | PLUGIN | S | P2 | Godot has no Unity build scene list. |
| unity/scene/create | Create empty scene file. | ADAPT | `PackedScene.pack`, `ResourceSaver.save`, `EditorInterface.open_scene_from_path` [O,R,E] | PLUGIN | M | P1 | New file is disk mutation; overwrite gate. |
| unity/renderpipeline/info | Read active rendering pipeline. | ADAPT | `ProjectSettings.get_setting` [S] | PLUGIN | S | P2 | Godot renderer concepts differ. |
| unity/renderer/get_materials | Read assigned renderer materials. | ADAPT | `Node` property reflection [N] | PLUGIN | M | P2 | Surface override and mesh material differ. |
| unity/renderer/set_material | Assign one material slot. | ADAPT | `ResourceLoader.load` [R], `EditorUndoRedoManager` [U] | PLUGIN | M | P2 | Instance/shared resource effects. |
| unity/scripts/read | Read script lines. | GODOT-NATIVE | Filesystem read (`FileAccess`) | CLI-only | S | P2 | Restrict to project path. |
| unity/scripts/patch | Apply unified diff to script. | GODOT-NATIVE | Agent host patch tool; editor scan [F] after write | CLI-only | S | P3 | Script patch is disk change, no scene undo. |
| unity/scripts/create | Create script template. | GODOT-NATIVE | Agent host file write; editor scan [F] | CLI-only | S | P3 | `class_name` registration after scan. |
| unity/ui/ensure_event_system | Ensure Unity EventSystem object. | SKIP | Godot `Control` input is built in [H] | PLUGIN | S | P3 | No EventSystem node required. |
| unity/ui/ensure_canvas | Ensure Unity Canvas object. | ADAPT | `Control` root or `CanvasLayer` | PLUGIN | S | P2 | No mandatory Canvas object. |
| unity/ui/create | Create UI element. | ADAPT | Existing `create-node` with `Control` subclasses [P,H] | PLUGIN | M | P1 | Parent must be suitable; owner persists. |
| unity/ui/set_props | Set UI element properties. | DIRECT | Existing `set-property` [P] and `Control` [H] | PLUGIN | M | P1 | Some values are resources not yet supported. |
| unity/ui/get_props | Read UI properties. | DIRECT | `Object.get_property_list` [N], `Control` [H] | PLUGIN | S | P1 | Return typed values without guessing. |
| unity/ui/layout/apply_preset | Apply anchor preset. | ADAPT | `Control.set_anchors_preset` [H] | PLUGIN | M | P2 | Preset may move offsets; undo all fields. |
| unity/ui/layout/add_layout_group | Add layout group. | ADAPT | `Container` nodes | PLUGIN | M | P2 | Containers are nodes, not components. |
| unity/ui/layout/add_content_size_fitter | Add ContentSizeFitter. | ADAPT | `Control` size flags and minimum size [H] | PLUGIN | M | P2 | No one-to-one fitter. |
| unity/ui/wire_button_onclick | Connect button handler. | ADAPT | `Object.connect`, `BaseButton.pressed` [N,H] | PLUGIN | M | P1 | Serialized connection and method signature. |
| unity/ui/prefab/create_from_root | Save UI root as prefab. | ADAPT | `PackedScene.pack`, `ResourceSaver.save` [O,R] | PLUGIN | M | P2 | Node ownership before pack. |
| unity/scaffold/main_menu | Generate menu and handlers. | SKIP | Compose generic scene/UI commands | CLI-only | M | P3 | Product-specific scaffold is not bridge parity. |
| unity/scaffold/vertical_slice | Generate sample game slice. | SKIP | Template outside core bridge | CLI-only | M | P3 | Fixed game convention has no universal mapping. |
| unity/scaffold/verify_vertical_slice | Run fixed sample play test. | SKIP | Project-specific test script via [G] | headless | M | P3 | Fixed Unity fixture has no Godot target. |
| unity/audio/clip/import | Copy/import audio clip. | ADAPT | File copy; editor scan [F], resource load [R] | CLI-only | M | P2 | Overwrite gate and import delay. |
| unity/audio/source/add | Add and configure AudioSource. | ADAPT | `AudioStreamPlayer` node and undo [U] | PLUGIN | M | P2 | 2D/3D player types differ. |
| unity/project_settings/get | Read project settings. | DIRECT | `ProjectSettings.get_setting` [S] | PLUGIN | S | P1 | Return registered/default state. |
| unity/project_settings/set | Write project settings. | ADAPT | `ProjectSettings.set_setting`, `save` [S] | PLUGIN | M | P2 | Disk mutation, not scene undo; allowlist keys. |
| unity/lighting/bake | Start asynchronous lightmap bake. | DEFER | `LightmapGI` [L]; public bake entry UNVERIFIED | PLUGIN | L | P3 | Renderer support and editor responsiveness. |
| unity/lighting/clear | Clear baked lightmaps. | DEFER | `LightmapGI` [L]; public clear entry UNVERIFIED | PLUGIN | L | P3 | Must not delete external data blindly. |
| unity/profiler/capture | Sample selected runtime metrics. | ADAPT | `Performance.get_monitor` [V] | PLUGIN | M | P2 | Different metrics; timing and remote game process. |
| unity/animation/clip_create | Create empty animation clip asset. | ADAPT | `Animation`, `AnimationLibrary`, `ResourceSaver.save` [A,R] | PLUGIN | M | P2 | Library/animation ownership. |
| unity/animator/controller_create | Create animator controller. | ADAPT | `AnimationTree` and state machine [A] | PLUGIN | L | P3 | No direct AnimatorController asset. |
| unity/animator/add_parameter | Add controller parameter. | ADAPT | `AnimationTree` state machine parameter model [A] | PLUGIN | L | P3 | Parameter semantics differ. |
| unity/animator/add_state | Add controller state. | ADAPT | `AnimationNodeStateMachine.add_node` [A] | PLUGIN | L | P3 | AnimationTree resource mutation. |
| unity/animator/add_transition | Add state transition. | ADAPT | `AnimationNodeStateMachine.add_transition` [A] | PLUGIN | L | P3 | Conditions need Godot-specific model. |
| unity/build/player | Build player artifact. | GODOT-NATIVE | `godot --headless --path --export-release` [G] | headless | M | P1 | Export preset and output gate; separate process. |
| unity/input/ensure_package | Install Unity Input System package. | SKIP | `InputMap` is built in [I] | PLUGIN | S | P3 | No package to install. |
| unity/input/ensure_active | Select legacy/new input mode. | SKIP | `InputMap` is built in [I] | PLUGIN | S | P3 | No matching mode switch. |
| unity/input/actions/create | Create InputActionAsset. | ADAPT | `InputMap` and `ProjectSettings` [I,S] | PLUGIN | M | P2 | Actions live in project settings. |
| unity/input/actions/add_map | Add action map. | SKIP | `InputMap` action namespace [I] | PLUGIN | S | P3 | Godot has no action-map object. |
| unity/input/actions/add_action | Add named action. | ADAPT | `InputMap.add_action` [I] and project persistence [S] | PLUGIN | M | P2 | Runtime map change alone may not save. |
| unity/input/actions/add_binding | Bind input event to action. | ADAPT | `InputMap.action_add_event` [I] and project persistence [S] | PLUGIN | M | P2 | Event serialization and dead zones. |
| unity/input/actions/generate_csharp | Generate input wrapper. | SKIP | No required wrapper for `InputMap` [I] | CLI-only | S | P3 | Optional C# tooling belongs outside bridge. |
| unity/input/actions/list | List actions and bindings. | DIRECT | `InputMap.get_actions`, `action_get_events` [I] | PLUGIN | S | P1 | Built-ins versus project actions. |
| unity/input/playerinput/add | Add PlayerInput component. | SKIP | Game scripts read `InputMap` actions [I] | PLUGIN | S | P3 | No PlayerInput node. |
| unity/input/playerinput/set_actions | Reconfigure PlayerInput component. | SKIP | Game scripts and `InputMap` [I] | PLUGIN | S | P3 | No equivalent component fields. |
| unity/input/playerinput/get | Read PlayerInput component. | SKIP | `InputMap` query [I] | PLUGIN | S | P3 | No equivalent component. |
| unity/input/scaffold_default_3d | Generate sample 3D input asset. | SKIP | Template outside core bridge | CLI-only | M | P3 | Sample convention, not generic capability. |
| unity/templates/list | List Unity scaffold templates. | SKIP | Agent host templates | CLI-only | S | P3 | No built-in Godot template registry chosen. |
| unity/templates/instantiate | Fill Unity scaffold template. | SKIP | Agent host template action | CLI-only | M | P3 | Avoid a second templating language. |
| unity/compile/errors | Read compiler diagnostics. | ADAPT | `godot --headless --check-only --script` [G] | headless | M | P1 | Checks selected script, not whole project. |
| unity/compile/last_result | Read last compile result. | ADAPT | Cache last headless check result in Rust; [G] | CLI-only | S | P2 | Cache is facade state, not editor state. |
| unity/compile/wait | Wait for compilation. | ADAPT | Poll headless check operation [G] | CLI-only | M | P2 | No Unity-like global compile cycle. |
| unity/scene/query | Filter scene objects. | ADAPT | `Node` tree/groups and `ClassDB` [N,C] | PLUGIN | M | P0 | Tag/layer/component filters differ. |
| unity/scene/summary | Return structural scene snapshot. | ADAPT | Existing `scene-tree` plus typed summary [P,N] | PLUGIN | M | P1 | Bound depth and output size. |
| unity/gameobject/get | Inspect one scene object. | ADAPT | `Node.get_path`, `Object.get_property_list` [N] | PLUGIN | S | P0 | Node path changes on rename. |
| unity/component/list | List components on object. | ADAPT | Node class/script/property reflection [N,C] | PLUGIN | S | P1 | Godot behavior is nodes and scripts. |
| unity/component/get_fields | Read named component fields. | ADAPT | `Object.get_property_list`, `Object.get` [N] | PLUGIN | S | P0 | Resource/object values need typed encoding. |
| unity/gameobject/create | Create scene object. | ADAPT | Existing `create-node` [P] | PLUGIN | S | P0 | Select built-in class; owner and instance guard. |
| unity/gameobject/destroy | Delete scene object. | ADAPT | Existing `delete-node` [P] | PLUGIN | S | P0 | Deletion governance; preserve subtree owners. |
| unity/gameobject/set_transform | Set transform channels. | ADAPT | Existing `set-property` [P] and undo [U] | PLUGIN | M | P1 | World/local conversion; omitted channels unchanged. |
| unity/component/add | Add component. | ADAPT | Existing `create-node` or attach script UNVERIFIED [P,N] | PLUGIN | M | P1 | No general component stack. |
| unity/component/set_field | Set one component field. | ADAPT | Existing `set-property` [P] | PLUGIN | S | P0 | Strict value coercion; no object refs yet. |
| unity/changes/preview | Validate a scene ChangeSet. | DEFER | Preflight state model plus [U,N] | PLUGIN | L | P2 | Must predict dependent steps without mutation. |
| unity/changes/apply | Apply previewed batch atomically. | DEFER | One `EditorUndoRedoManager` action [U] | PLUGIN | L | P2 | Scene-only atomicity; no disk operations. |

## Godot-only capabilities

These are Godot concepts, not renamed Unity tools. An agent gets typed discovery and small edits where a public API exists. Unless marked native or deferred, each edit needs the same checks and Undo/Redo as the existing scene commands.

| Capability | What it gives an agent | Checked API or source | Effort | Priority | Decision and trap |
|---|---|---|---|---|---|
| Signals and connections | Discover signals; connect and disconnect named callables | `ClassDB.class_get_signal_list` [C], `Object.connect`, `disconnect`, `get_signal_connection_list` [N] | M | P1 | `connect-signal` is built (Undo, flags and persistence confirmed in 4.7.2); `list-signals` and `disconnect-signal` are still to build. |
| Node groups | Query and change semantic sets | `Node.get_groups`, `add_to_group`, `remove_from_group` [N] | S | P1 | `set-group` is built (add and remove as one Undo action; inherited and runtime memberships rejected); `list-groups` is still to build. |
| Unique-name `%` nodes | Stable in-scene references across path changes | `Node.unique_name_in_owner` [N] | S | P1 | `set-unique-name` is built (add and remove as one Undo action; the scene root, inherited and nested-origin removals, and same-owner collisions are rejected; uniqueness scope is the owner). |
| Autoload singletons | Discover and configure global scenes and scripts | `ProjectSettings` [S] | M | P2 | Project setting mutation; exact autoload key format UNVERIFIED. |
| InputMap | Discover project actions and binding events | `InputMap.get_actions`, `action_get_events`, `add_action`, `action_add_event` [I] | M | P2 | Runtime map and saved project settings are distinct. |
| Scene inheritance and instancing | Add reusable scenes and inspect local overrides | `PackedScene.instantiate`, `Node.owner`, `Node.scene_file_path` [O,N] | L | P1 | Editable Children must cover nested instance ancestors before editing and saving. |
| `.tres` and `.res` Resources | Inspect, create, and save typed data assets | `ResourceLoader.load`, `ResourceSaver.save`, `Resource` [R] | M | P2 | `.res` is binary; shared external resource edits are disk changes, not scene undo. |
| Custom Resource scripts | Discover script-defined data types | `Resource`, `ClassDB` and script property reflection [R,C,N] | M | P2 | Script-exported properties need the current strict coercion model. |
| `class_name` script classes | Resolve named user classes for creation and discovery | `ProjectSettings.get_global_class_list` [S] | S | P1 | Refresh after script import; constructor safety must be checked. |
| ClassDB reflection | Discover constructible classes, properties, methods, and signals | `ClassDB.class_exists`, `can_instantiate`, `class_get_property_list`, `class_get_signal_list` [C] | S | P0 | Build `inspect-class`; avoid guessing property types from JSON. |
| Theme editing | Edit UI colors, fonts and styleboxes | `Theme.set_color`, `set_font`, `set_stylebox` [H] | M | P2 | Resource sharing and save boundary. |
| TileSet and TileMapLayer | Author tiles and cells | `TileSet.add_source`, `TileMapLayer.set_cell` [T] | L | P3 | Huge structured edits and resource ownership; batch later. |
| AnimationPlayer and AnimationTree | Author tracks, libraries, state graphs | `Animation`, `AnimationLibrary`, `AnimationMixer`, `AnimationNodeStateMachine` [A] | L | P2 | Current 4.8-dev class split may differ in 4.7.2; check live before committing surface. |
| Shaders and VisualShader | Inspect shader source and graph resources | `Shader.code`, `VisualShader.add_node` [Q] | L | P3 | Text source can use normal file tools; graph editing needs typed node schemas. |
| Translations | Discover locales and translation resources | `TranslationServer`, `Translation` [Z] | M | P2 | Project locale files versus runtime locale state. |
| Physics and navigation layer names | Read and set named bit layers | `ProjectSettings` [S]; exact property keys UNVERIFIED | S | P2 | Keep names separate from node bit masks. |
| GDScript language server | Reuse editor language diagnostics and symbols | `modules/gdscript/language_server/gdscript_language_server.cpp` [D] | S | P1 | GODOT-NATIVE: the agent host connects to the LSP; do not duplicate the parser. Host setup and 4.7.2 behavior UNVERIFIED. |
| Debug adapter | Inspect breakpoints and runtime stack | `editor/debugger/debug_adapter/debug_adapter_server.cpp` [D] | M | P2 | GODOT-NATIVE: use a DAP client; do not claim the remote scene tree is covered by DAP. |
| Headless checks, scripts and export | Parse scripts, run project scripts, create export artifacts | `--headless`, `--path`, `--check-only`, `--script`, `--export-release`, `--export-debug` [G] | S/M | P1 | GODOT-NATIVE: the Rust side launches a child process. `--check-only` is for a script, not a whole-project compile. |
| Run current scene and read output | Observe runtime behavior from the edited scene | `EditorInterface.play_current_scene`, `stop_playing_scene` [E] | M | P1 | Capturing child output through a public editor API is UNVERIFIED; a headless process is the alternate route. |
| Remote scene tree and debugger | Inspect live nodes and debug state | `EditorDebuggerPlugin` [D]; remote-tree access API UNVERIFIED | L | P2 | DEFER until live editor proof; do not infer a public remote tree API from the UI. |
| Editor settings | Read operator preferences, gate risky actions | `EditorInterface.get_editor_settings`, `EditorSettings.get_setting`, `set_setting` [E,S] | S | P1 | Gate values are local user state, not project data. |
| GDExtension | Discover native extension configuration | `core/extension/gdextension.cpp`, `GDExtension` class | L | P3 | Native binary loading and build have security and platform constraints; no general agent install command. |

Checked and dropped: wrappers around the language server and debug adapter (use those protocols directly), a general `bake-lightmaps` command (the public `LightmapGI` docs list no bake or clear method), direct editing of `.res` bytes (use typed resource load and save), and a generic remote scene-tree mutation command (no public API verified).

## Mappings that still need proof

These need source or live 4.7.2 proof before anyone claims parity: `unity/console/tail` (editor log API), `unity/screenshot/capture` (editor viewport capture), `unity/play/inject_input` (separate game process), `unity/assets/wait_for_import` (what scan completion guarantees), the four `unity/prefab` variant, apply, revert, and unpack tools, `unity/lighting/bake` and `clear`, and `unity/compile/errors` (coverage of `--check-only`). The `unity/input/actions/*` tools also need a decision on how project-persistent InputMap edits are stored. Exact physics and navigation layer setting keys and the autoload key format are unverified.

## Open questions

- Which of the Phase 1 to 3 commands does Bruno want after `inspect-node`, if the order should change?
- Should the `commands` discovery subcommand be built? It is proposed, not approved.
- Whether a discard option should join `open-scene`'s save option.
- When governance gates are designed: do they cover direct CLI calls as well as any MCP calls, and who may enable them?
