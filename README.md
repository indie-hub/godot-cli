# Godot Pipeline

Godot Pipeline is a command-line tool plus a small plugin for the Godot editor. It lets a script or an AI agent read and edit the scene that is open in a running Godot editor.

The agent sends commands over a local socket. Every edit goes through the editor's own undo and redo history, so the agent works the way a person does in the editor, instead of editing scene files by hand.

## What it does and does not do

- It talks only to a plugin that you install in your own project. The connection is local, over 127.0.0.1.
- Scene edits go through the editor's undo and redo stack, so you can undo them in the editor. Saving (`save-scene`) and opening a scene (`open-scene`) are not undo steps.
- It writes nothing to disk unless you run `save-scene`.
- It does not modify the Godot engine.

## Requirements

- Stock Godot 4.7.2. This is the version the project is tested on.
- A Rust toolchain that supports the 2024 edition (Cargo.toml sets `edition = "2024"`), to build the command-line tool.

## Install

1. Copy the folder `addon/godot_pipeline` into the `addons` folder of your project.
2. In the Godot editor, open Project Settings > Plugins and enable Godot Pipeline.
3. Open the scene you want to work on.

Then build the command-line tool from the repository:

    cargo build

The plugin listens on port 47821. The port is fixed in the plugin source (`const PORT` in `addon/godot_pipeline/editor_plugin.gd`). The command-line tool uses 47821 unless you pass `--port`. Each editor needs its own port. To run a second editor, give it its own copy of the plugin with that line changed to a free port, and pass the same number to the command-line tool with `--port`.

## Quick start

This example was run against a headless Godot 4.7.2 editor with `res://world.tscn` open. The scene has a root `Node2D` named `World` with one child, `Background`.

Start the editor with the plugin installed and the scene open:

    godot --headless --path /path/to/project --editor res://world.tscn

`godot` stands for your Godot 4.7.2 executable; on macOS it is inside the application, for example `Godot.app/Contents/MacOS/Godot`. `--headless` runs the editor without a window. These examples use the default port; if your plugin uses another port, pass the same `--port N` to every command.

Read the editor status and the scene tree:

    $ cargo run -- status
    {
      "editor": "Godot Editor",
      "playing": false,
      "scene_path": "res://world.tscn",
      "version": "4.7.2-stable (official)"
    }

    $ cargo run -- scene-tree
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

    $ cargo run -- create-node . Node2D Player --project-path /path/to/project
    {
      "class_name": "Node2D",
      "name": "Player",
      "parent_path": ".",
      "requested_name": "Player"
    }

    $ cargo run -- set-property Player position '[120, 80]' --project-path /path/to/project
    {
      "node_path": "Player",
      "old_value": "Vector2(0, 0)",
      "property": "position",
      "type": "Vector2",
      "value": "Vector2(120, 80)"
    }

    $ cargo run -- save-scene --project-path /path/to/project
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
| read | `open-scene` | Open a scene in the editor; with `--save`, save the current scene first. |
| edit | `rename-node` | Rename a node. |
| edit | `create-node` | Add a new node under another node. |
| edit | `set-property` | Set one property on a node. |
| edit | `delete-node` | Remove a node and its subtree. |
| edit | `connect-signal` | Connect a signal on one node to a method on another node. |
| edit | `set-group` | Add or remove a persistent group on a node. |
| edit | `set-unique-name` | Set or clear a node's `%` unique name. |
| edit | `instantiate-scene` | Add an instance of a scene under a node. |
| edit | `save-scene` | Save the open scene to its existing file. |

Every edit command except `save-scene` goes through the editor's undo and redo stack and saves nothing. Run `save-scene` to write the scene to disk. `open-scene` changes which scene is open and writes no file unless you pass `--save`.

## Status

Godot Pipeline is a prototype. The current version is 0.6.0. It is not ready for distribution. The read and navigate commands and the scene edit commands are complete.

## More

- `docs/reference.md`: the wire protocol and the details and limits of every command, and how to verify the project.
- `CHANGELOG.md`: what changed in each release.
- `PLAN.md`: the roadmap.
- `tools/replay/README.txt`: the regression test harness.
