# Godot Pipeline

Godot Pipeline is a command-line tool plus a small plugin for the Godot editor. It lets a script or an AI agent read and edit the scene that is open in a running Godot editor.

The agent sends commands over a local socket. Every edit goes through the editor's own undo and redo history, so the agent works the way a person does in the editor, instead of editing scene files by hand.

## What it does and does not do

- It talks only to a plugin that you install in your own project. The connection is local, over 127.0.0.1.
- Scene edits go through the editor's undo and redo stack, so you can undo them in the editor. Saving (`save-scene`) and opening a scene (`open-scene`) are not undo steps.
- It writes a scene file when you run `save-scene`, when `play` saves an unsaved scene before it launches, and when the editor rewrites the open scene's file as a play starts (Godot writes its own form of that file even when nothing was unsaved). `play`'s `saved` list names only the scenes the command itself saved. It also writes one line the plugin manages: on the first start with the plugin enabled it registers the `GodotPipelineCapture` game autoload, and the `[autoload]` entry reaches `project.godot` with the next project-settings save. Disabling the plugin removes it again. See `docs/reference.md` for `play` and the "Game helper" section.
- It does not modify the Godot engine.

## Requirements

- Stock Godot 4.7.2. This is the version the project is tested on.
- A Rust toolchain that supports the 2024 edition (Cargo.toml sets `edition = "2024"`), to build the command-line tool.

## Install

Build the command-line tool from the repository:

    cargo build

Install the plugin into your project with one command. This needs no editor:

    target/debug/godot-cli install /path/to/project

`install` copies the plugin folder into `addons/godot_pipeline` and adds `res://addons/godot_pipeline/plugin.cfg` to the `[editor_plugins]` `enabled` list in `project.godot`, creating that section when it is absent and preserving the rest of the file. It copies no `.uid` files. Validation of the inputs runs first and a refused install leaves the project untouched, but a write failure partway (a read-only subfolder, a read-only project folder) can leave part of the addon copied and the plugin not enabled, and a rerun completes it. A symlinked `project.godot` is replaced by a regular file. Run it again on an installed project: it changes nothing and says so. `--overwrite` replaces only the files the source addon contains: it overwrites them in place, keeps every other file in `addons/godot_pipeline` (including `.uid` files and files you added) and deletes nothing. The reply lists the files it `created` and the files it `replaced`. `--addon-source <dir>` installs a different copy of the plugin. It refuses a folder that has no `project.godot`, a `project.godot` it cannot read, cannot write, or that is not UTF-8, and an `[editor_plugins]` `enabled` value it cannot edit in place; the error names the cause.

Then open the project in the editor and open the scene you want to work on. The plugin is already enabled; Project Settings > Plugins shows it. In the measured run, running `install` while an editor was open on the project still left the entry in `project.godot` after the editor exited.

When the editor starts with the plugin enabled, the plugin registers the `GodotPipelineCapture` game autoload (the in-game log and capture helper, `res://addons/godot_pipeline/scripts/helper.gd`). The `[autoload]` entry reaches `project.godot` with the next project-settings save; starting the game saves. A second start changes nothing. Disabling the plugin removes the entry again. While the plugin is toggled off, the command line has no server until the plugin is enabled again. Details and the exact lines are in `docs/reference.md` ("Game helper").

## Port

The plugin listens on port 47821 unless `GODOT_PIPELINE_PORT` is set when the editor starts:

    GODOT_PIPELINE_PORT=47900 godot --headless --path /path/to/project --editor

The value must be an integer from 1024 to 65535. Any other value makes the plugin print an error and not listen at all; it never falls back to 47821, because another editor may already hold that port. Pass the same number to the command-line tool with `--port`, for example `godot-cli --port 47900 call status`. Each editor needs its own port; two editors with different values run side by side.

## Quick start

This example was run against a headless Godot 4.7.2 editor with `res://world.tscn` open. The scene has a root `Node2D` named `World` with one child, `Background`.

Start the editor with the plugin installed and the scene open:

    godot --headless --path /path/to/project --editor res://world.tscn

`godot` stands for your Godot 4.7.2 executable; on macOS it is inside the application, for example `Godot.app/Contents/MacOS/Godot`. `--headless` runs the editor without a window. These examples use the default port; if your plugin uses another port, pass the same `--port N` to every command.

Read the editor status and the scene tree:

    $ cargo run -- call status
    {
      "editor": "Godot Editor",
      "playing": false,
      "scene_path": "res://world.tscn",
      "version": "4.7.2-stable (official)"
    }

    $ cargo run -- call scene-tree
    {
      "children": [
        {
          "children": [],
          "name": "Background",
          "type": "Node2D"
        }
      ],
      "name": "World",
      "type": "Node2D"
    }

Add a `Node2D` named `Player` under the root, move it, and save the scene:

    $ cargo run -- call create-node . Node2D Player --project-path /path/to/project
    {
      "class_name": "Node2D",
      "name": "Player",
      "parent_path": ".",
      "requested_name": "Player"
    }

    $ cargo run -- call set-property Player position '[120, 80]' --project-path /path/to/project
    {
      "node_path": "Player",
      "old_value": "Vector2(0, 0)",
      "property": "position",
      "type": "Vector2",
      "value": "Vector2(120, 80)"
    }

    $ cargo run -- call save-scene --project-path /path/to/project
    {
      "path": "res://world.tscn"
    }

The new node is in the scene and in the file now. You can undo the edits in the editor.

## Commands

| Group | Command | What it does |
| --- | --- | --- |
| read | `status` | Report the editor version, whether a scene is playing, and the open scene. |
| read | `scene-tree` | Show the node tree of the open scene. |
| read | `inspect-node` | Show a node's class, child count, and property values. |
| read | `query-nodes` | Find nodes in the open scene by class, group, or name. |
| read | `inspect-class` | Show an engine class's properties, methods, and signals. |
| read | `list-resources` | List the resource files the editor file system holds. |
| read | `inspect-resource` | Show one resource file's class, typed properties, and embedded sub-resources. |
| read | `open-scene` | Open a scene in the editor; with `--save`, save the current scene first. |
| read | `console` | Show the editor log messages captured since the plugin loaded. |
| edit | `rename-node` | Rename a node. Rejects a node inherited from a base scene, because the rename would not be saved. |
| edit | `create-node` | Add a new node under another node. |
| edit | `set-property` | Set one property on a node. |
| edit | `delete-node` | Remove a node and its subtree. Rejects a node inherited from a base scene, because the deletion would not be saved. |
| edit | `connect-signal` | Connect a signal on one node to a method on another node. |
| edit | `set-group` | Add or remove a persistent group on a node. |
| edit | `set-unique-name` | Set or clear a node's `%` unique name. |
| edit | `instantiate-scene` | Add an instance of a scene under a node. |
| edit | `set-parent` | Move a node under a new parent. `--keep-global-transform` (the default) keeps a Node2D/Node3D global transform and a Control's global position. The flag is written `--keep-global-transform`, `--keep-global-transform=true` or `--keep-global-transform=false`. |
| edit | `attach-script` | Attach, replace or detach a node's GDScript. Refuses a script that does not compile before the undo action. |
| edit | `save-scene` | Save the open scene to its existing file. |
| run | `play` | Play the edited scene, a scene named by a `res://` path, or the main scene. Saves an unsaved edited scene first; opens a game window unless the project sets a headless display driver. |
| run | `stop` | Stop the running game. Does nothing when no game runs. |
| run | `game-log` | Show the running game's log entries forwarded by the game helper, with the run identity, cursor, drop counters, and script and engine locations for errors. |
| run | `capture-game` | Capture the running game's viewport as a PNG in the reply. Holds the connection until the helper answers or the wait passes; writes no file. |
| run | `game-key` | Press, hold and release a physical key in the running game. |
| run | `game-click` | Click the left mouse button at viewport coordinates in the running game. |
| edit | `eval` | Run a GDScript snippet in the live editor and return its value; may break the one-undo-action and no-main-thread-blocking rules. |

Every edit command except `save-scene` goes through the editor's undo and redo stack and saves nothing. Run `save-scene` to write the scene to disk; `play` saves an unsaved edited scene before it launches the game. `open-scene` changes which scene is open and writes no file unless you pass `--save`.

## Status

Godot Pipeline is a prototype. The current version is 0.7.0. It is not ready for distribution. The read and navigate commands and the scene edit commands are complete.

## More

- `docs/reference.md`: the wire protocol and the details and limits of every command, and how to verify the project.
- `CHANGELOG.md`: what changed in each release.
- `PLAN.md`: the roadmap.
- `tools/replay/README.txt`: the regression test harness.
