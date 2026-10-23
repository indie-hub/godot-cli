# Phase 4: complete a playable authoring workflow

Status: proposed roadmap, 16 October 2026. No commands are implemented by this document. Milestone A's acceptance script, `tools/acceptance/milestone_a.py`, passed its nine steps on 2026-10-23; what it measures, the stored scene data it compares and the limits of that snapshot verified so far, which are not an exhaustive list (node owner relations, the arguments bound to a persistent callable connection, and a sub-resource's identity beyond its rendered text), are recorded in [PLAN.md](../PLAN.md) under "Milestone A acceptance (assemble)".

The goal is to let a developer or agent assemble, run and check a small playable scene. Priorities follow the workflows that become possible, rather than the number of Unity commands matched.

Use [the Unity parity map](unity-parity.md) for individual mappings, built-command status, evidence and engine differences. Unity names below identify parity targets; they are not proposed Godot command syntax. New Godot commands keep the existing kebab-case convention. A mapping based on documentation and source still needs verification in the project's tested Godot version.

## Starting point

The current command set already covers scene and class inspection, node queries, resource discovery and inspection, opening scenes, node creation and deletion, properties, hierarchy changes, scene instancing, signals, groups, unique names, saving an existing scene, editor console output and editor-side `eval`.

Reuse those capabilities. Extend `set-property` where practical rather than introduce parallel property setters. Ordinary file tools can write script text; the missing integration is getting the editor to recognise and attach it reliably.

## Command roadmap

| Order | Capability | Unity parity targets or Godot extension | Minimum useful scope | Game-development outcome | Boundary or dependency |
| --- | --- | --- | --- | --- | --- |
| 1 | Resource assignment and editing | `set_component_properties`, `create_asset`, `get_material_properties`, `set_material_properties`; Godot resource assignment and uniqueness | Assign an existing resource to a node property; create and configure an embedded resource; inspect and edit its properties; make a shared resource unique before a local edit. Start with textures, collision shapes and materials. | Give a sprite its texture, a collision node its shape, and a mesh its material. | Current `set-property` does not accept Object/Resource references. Scene-local changes must use scene undo; shared or standalone resource-file writes need the external-write boundary described below. |
| 2 | Script attachment and editor refresh | `attach_script`, `reload_file`, `recompile`, `recompile_status`, `get_serialized_fields` | Inspect, attach and detach an existing GDScript; recognise external script changes; report parse/load failures; inspect and set supported exported properties. | Give a player or enemy behaviour and configure its speed or health. | Validate script compatibility before replacing an attachment. Tool scripts can execute during attachment. File reload must account for unsaved editor buffers. Do not build a script text editor into the CLI. |
| 3 | Play and stop | `editor_play`, `editor_stop`; consider `editor_pause` later | Run the current scene, a specified scene or the main scene; report running/stopped/failure state; stop the launched game. | Try a change without manually switching to the editor. | Define dirty-scene behaviour first: launching must not silently save or discard work. Waiting must not block the editor thread. |
| 4 | Runtime observation and capture | `console`, `get_console_logs`, `capture_game_view`, `capture_scene_view`, `screenshot`; Godot runtime inspection | First establish runtime error/log capture and a game screenshot. Then add runtime tree/property inspection. Reuse the existing console command where suitable. | Detect script errors, check the visible result, and diagnose unexpected runtime state. | Existing console capture is editor-side; runtime coverage must be demonstrated. Editor `eval` is not runtime access. Label editor viewport captures separately from game captures. |
| 5 | Scene creation and multi-scene work | `create_scene`, `list_open_scenes`, `set_active_scene`, `save_all`; Godot save-as | Create a scene with an explicit root type; save it to a new path; list and switch open scene tabs; explicitly save selected/all scenes. | Create reusable player, enemy, UI and level scenes from scratch. | New-file creation and save-as need an explicit disk-write contract. Preserve dirty scenes and reject accidental overwrites. The parity map's proposed scene-creation mechanism is not yet measured in the tested engine. |
| 6 | Selected project settings | Settings pairs in the next table | Input actions first, then named physics layers and gravity. Treat main-scene selection as an additional Godot capability. | Configure controls, collision categories and basic game startup. | Settings persistence changes `project.godot`; it is not scene undo and must not be hidden inside a scene edit. |
| 7 | Recovery and repeated authoring | Additional Godot proposals: undo/redo and node duplication; these are not named Unity targets in the parity map. | Undo/redo in the correct scene history; duplicate a node/subtree with correct ownership, names and resource-sharing behaviour. | Recover from a bad edit and build repeated level structures. | Existing scene commands already create undo entries. Exposing undo must preserve that history; duplication must state whether resources remain shared. |

These are capability groups, not a requirement to add one command per Unity row. For example, Godot resource operations can cover several Unity material/component tasks.

## Settings priorities

Implement reads alongside writes so callers can inspect the current state and verify changes.

| Priority | Unity parity pair | Initial Godot scope | Reason | Limit |
| --- | --- | --- | --- | --- |
| First | `get_input_settings` / `set_input_settings` | List, create and remove input actions; read and edit key, mouse and controller bindings and deadzones. | Enables movement, jumping, interaction and menus. Include this in the first playable milestone. | Use Godot actions/events; Unity legacy axis sensitivity and gravity have no direct equivalent. Specify key and joypad event encoding. |
| Next | `get_tags_layers` / `set_tags_layers` | Read and name 2D and 3D physics layers. Reuse existing groups support for the tag-like part. | Makes player, enemy and environment collision categories understandable. | Layer names alone do not configure a node's collision layer/mask; use existing property editing for those values. Godot groups are not a global Unity tag registry. |
| Next | `get_physics_settings` / `set_physics_settings` | Read and change gravity separately for 2D and 3D. | Supports common platformer and physics-prototype tuning. | Validate before writing. Defer advanced solver controls until a real workflow needs them. |
| Later | `get_player_settings` / `set_player_settings` | Application identity and version, as mapped in the parity document. | Useful for packaging and release preparation. | Main-scene selection and window configuration are separate Godot additions, not the documented scope of this pair. |
| Deferred | Audio, graphics, quality and time settings pairs | Choose a concrete workflow before expanding these groups. | They follow the core authoring loop. | Avoid generic coverage of every project setting or invented Unity-equivalent fields. |

## Delivery and readiness

| Milestone | Scope | Evidence of completion |
| --- | --- | --- |
| A: assemble | Resource assignment plus script attachment on an existing fixture scene. | Assign a texture and collision shape, attach a player script, set an exported property, and verify persistence after explicit scene save. Rejected requests leave scene and undo history unchanged; supported scene edits undo and redo correctly. |
| B: run and check | Play/stop plus runtime errors and game capture. | Run that scene, deliberately trigger and retrieve a runtime error, fix it, rerun, capture the game, and stop it. Verify outcomes rather than just command acknowledgement. |
| C: playable controls | Input actions after the settings-write boundary is defined. | Configure bindings, persist them explicitly, reopen the project and demonstrate that input drives the player. Automated input simulation can follow; it is not assumed to exist. |
| D: reusable content | New scene/save-as and multi-scene work. | Create and save a reusable player scene, instance it in a level using the existing command, switch scene tabs, and preserve unrelated unsaved work. |

Milestones A–C are the first candidate for a focused public demo. Milestone D enables the stronger empty-project-to-playable-scene demonstration. More command count alone is not a readiness test.

## Disk writes and scope

[PLAN.md](../PLAN.md) currently defers external writes and says nothing saves except through `save-scene`. This roadmap does not override those rules. Before implementing project-settings persistence, standalone resource writes, new scene files or save-as, explicitly revise that contract and add the relevant gates: no overwrite by default, validation before mutation, clear affected paths and shared-resource effects, and honest reporting of whether a change is undoable. Keep scene-only edits within the existing undo/save model.

Defer broad animation, baking, navigation, package-management and specialist authoring coverage until the core loop works. Pick one specialist workflow next based on actual users. For build/export work, investigate reusing Godot's existing command-line functionality before creating a parallel system.

This is a prioritisation document, not an exhaustive parity audit or evidence that the proposed engine integrations work.
