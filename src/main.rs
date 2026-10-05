//! Godot Pipeline CLI.
//!
//! Talks to the `godot_pipeline` GDScript editor plugin over a loopback TCP
//! socket and prints its JSON response. See `protocol` for the wire format
//! and `README.md` for how to install the plugin and the one-editor-per-port
//! limitation.

mod protocol;

use protocol::{ClientError, DEFAULT_PORT, HOST, Request, Response, send_request};
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("error: {message}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: &[String]) -> Result<(), String> {
    let mut command: Option<String> = None;
    let mut port: u16 = DEFAULT_PORT;
    let mut node_path: Option<String> = None;
    let mut new_name: Option<String> = None;
    let mut project_path: Option<String> = None;
    let mut create_parent_path: Option<String> = None;
    let mut create_class_name: Option<String> = None;
    let mut create_node_name: Option<String> = None;
    let mut set_node_path: Option<String> = None;
    let mut set_property_name: Option<String> = None;
    let mut set_value: Option<String> = None;
    let mut inspect_node_path: Option<String> = None;
    let mut query_class: Option<String> = None;
    let mut query_group: Option<String> = None;
    let mut query_name: Option<String> = None;
    let mut query_limit: Option<String> = None;
    let mut delete_node_path: Option<String> = None;
    let mut connect_source_path: Option<String> = None;
    let mut connect_signal: Option<String> = None;
    let mut connect_target_path: Option<String> = None;
    let mut connect_method: Option<String> = None;
    let mut connect_deferred: bool = false;
    let mut connect_one_shot: bool = false;
    let mut scene_path: Option<String> = None;
    let mut save: bool = false;

    let mut arguments = args.iter().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "status" | "scene-tree" => command = Some(argument.clone()),
            "rename-node" => {
                command = Some(argument.clone());
                node_path = Some(
                    arguments
                        .next()
                        .ok_or_else(|| {
                            "rename-node requires <scene-relative-path> <new-name>".to_string()
                        })?
                        .clone(),
                );
                new_name = Some(
                    arguments
                        .next()
                        .ok_or_else(|| {
                            "rename-node requires <scene-relative-path> <new-name>".to_string()
                        })?
                        .clone(),
                );
            }
            "create-node" => {
                command = Some(argument.clone());
                create_parent_path = Some(
                    arguments
                        .next()
                        .ok_or_else(|| {
                            "create-node requires <parent-scene-relative-path> <class> <name>"
                                .to_string()
                        })?
                        .clone(),
                );
                create_class_name = Some(
                    arguments
                        .next()
                        .ok_or_else(|| {
                            "create-node requires <parent-scene-relative-path> <class> <name>"
                                .to_string()
                        })?
                        .clone(),
                );
                create_node_name = Some(
                    arguments
                        .next()
                        .ok_or_else(|| {
                            "create-node requires <parent-scene-relative-path> <class> <name>"
                                .to_string()
                        })?
                        .clone(),
                );
            }
            "set-property" => {
                command = Some(argument.clone());
                let mut next_operand = || {
                    arguments.next().cloned().ok_or_else(|| {
                        "set-property requires <scene-relative-path> <property> <value>".to_string()
                    })
                };
                set_node_path = Some(next_operand()?);
                set_property_name = Some(next_operand()?);
                set_value = Some(next_operand()?);
            }
            "inspect-node" => command = Some(argument.clone()),
            "query-nodes" => command = Some(argument.clone()),
            "inspect-class" => command = Some(argument.clone()),
            "delete-node" => {
                command = Some(argument.clone());
                delete_node_path = Some(
                    arguments
                        .next()
                        .ok_or_else(|| "delete-node requires <scene-relative-path>".to_string())?
                        .clone(),
                );
            }
            "save-scene" => command = Some(argument.clone()),
            "open-scene" => command = Some(argument.clone()),
            "connect-signal" => {
                command = Some(argument.clone());
                let mut next_operand = || {
                    arguments.next().cloned().ok_or_else(|| {
                        "connect-signal requires <source-scene-relative-path> <signal> <target-scene-relative-path> <method>".to_string()
                    })
                };
                connect_source_path = Some(next_operand()?);
                connect_signal = Some(next_operand()?);
                connect_target_path = Some(next_operand()?);
                connect_method = Some(next_operand()?);
            }
            "--port" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--port requires a value".to_string())?;
                port = value
                    .parse::<u16>()
                    .map_err(|_| format!("invalid port: {value}"))?;
            }
            "--project-path" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--project-path requires a value".to_string())?;
                project_path = Some(value.clone());
            }
            "--node-path" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--node-path requires a value".to_string())?;
                inspect_node_path = Some(value.clone());
            }
            "--class" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--class requires a value".to_string())?;
                query_class = Some(value.clone());
            }
            "--scene-path" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--scene-path requires a value".to_string())?;
                scene_path = Some(value.clone());
            }
            "--save" => save = true,
            "--deferred" => connect_deferred = true,
            "--one-shot" => connect_one_shot = true,
            "--group" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--group requires a value".to_string())?;
                query_group = Some(value.clone());
            }
            "--name" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--name requires a value".to_string())?;
                query_name = Some(value.clone());
            }
            "--limit" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| "--limit requires a value".to_string())?;
                query_limit = Some(value.clone());
            }
            "--help" | "-h" => {
                print_usage();
                return Ok(());
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }

    let Some(command) = command else {
        print_usage();
        return Err(
            "missing command (expected 'status', 'scene-tree', 'rename-node', 'create-node', 'set-property', 'inspect-node', 'query-nodes', 'inspect-class', 'delete-node', 'connect-signal', 'save-scene', or 'open-scene')"
                .to_string(),
        );
    };

    let request = match command.as_str() {
        "status" => Request::Status,
        "scene-tree" => Request::SceneTree,
        "rename-node" => {
            let node_path = node_path.expect("parsed together with the rename-node command");
            let new_name = new_name.expect("parsed together with the rename-node command");
            let project_path = project_path
                .ok_or_else(|| "rename-node requires --project-path <dir>".to_string())?;
            Request::RenameNode {
                node_path,
                new_name,
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "create-node" => {
            let parent_path =
                create_parent_path.expect("parsed together with the create-node command");
            let class_name =
                create_class_name.expect("parsed together with the create-node command");
            let name = create_node_name.expect("parsed together with the create-node command");
            let project_path = project_path
                .ok_or_else(|| "create-node requires --project-path <dir>".to_string())?;
            Request::CreateNode {
                parent_path,
                class_name,
                name,
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "set-property" => {
            let node_path = set_node_path.expect("parsed together with the set-property command");
            let property =
                set_property_name.expect("parsed together with the set-property command");
            let value = set_value.expect("parsed together with the set-property command");
            let project_path = project_path
                .ok_or_else(|| "set-property requires --project-path <dir>".to_string())?;
            Request::SetProperty {
                node_path,
                property,
                value: parse_value_argument(&value),
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "inspect-node" => {
            let node_path = inspect_node_path
                .ok_or_else(|| "inspect-node requires --node-path <path>".to_string())?;
            let project_path = project_path
                .ok_or_else(|| "inspect-node requires --project-path <dir>".to_string())?;
            Request::InspectNode {
                node_path,
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "query-nodes" => {
            let project_path = project_path
                .ok_or_else(|| "query-nodes requires --project-path <dir>".to_string())?;
            // The limit is forwarded as raw JSON (a number, or a plain string
            // when it does not parse) so an invalid limit still reaches the
            // plugin, which validates it after the project and scene guards.
            let limit = query_limit
                .as_deref()
                .map(parse_value_argument)
                .unwrap_or_else(|| serde_json::json!(100));
            Request::QueryNodes {
                project_path: canonicalize_project_path(&project_path)?,
                class: query_class,
                group: query_group,
                name: query_name,
                limit,
            }
        }
        "inspect-class" => {
            let class =
                query_class.ok_or_else(|| "inspect-class requires --class <class>".to_string())?;
            let project_path = project_path
                .ok_or_else(|| "inspect-class requires --project-path <dir>".to_string())?;
            Request::InspectClass {
                class,
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "delete-node" => {
            let node_path = delete_node_path.expect("parsed together with the delete-node command");
            let project_path = project_path
                .ok_or_else(|| "delete-node requires --project-path <dir>".to_string())?;
            Request::DeleteNode {
                node_path,
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "save-scene" => {
            let project_path = project_path
                .ok_or_else(|| "save-scene requires --project-path <dir>".to_string())?;
            Request::SaveScene {
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "connect-signal" => {
            let source_path =
                connect_source_path.expect("parsed together with the connect-signal command");
            let signal = connect_signal.expect("parsed together with the connect-signal command");
            let target_path =
                connect_target_path.expect("parsed together with the connect-signal command");
            let method = connect_method.expect("parsed together with the connect-signal command");
            let project_path = project_path
                .ok_or_else(|| "connect-signal requires --project-path <dir>".to_string())?;
            Request::ConnectSignal {
                source_path,
                signal,
                target_path,
                method,
                deferred: connect_deferred,
                one_shot: connect_one_shot,
                project_path: canonicalize_project_path(&project_path)?,
            }
        }
        "open-scene" => {
            let scene_path =
                scene_path.ok_or_else(|| "open-scene requires --scene-path <path>".to_string())?;
            let project_path = project_path
                .ok_or_else(|| "open-scene requires --project-path <dir>".to_string())?;
            Request::OpenScene {
                project_path: canonicalize_project_path(&project_path)?,
                scene_path,
                save,
            }
        }
        _ => unreachable!("argument parsing only assigns known commands"),
    };

    let response =
        send_request(HOST, port, &request).map_err(|error| describe_client_error(&error, port))?;

    match response {
        Response::Ok { data } => {
            let pretty = serde_json::to_string_pretty(&data).map_err(|error| error.to_string())?;
            println!("{pretty}");
            Ok(())
        }
        Response::Error { message } => Err(format!("plugin reported an error: {message}")),
    }
}

fn describe_client_error(error: &ClientError, port: u16) -> String {
    match error {
        ClientError::Connect(source) => format!(
            "could not connect to the Godot Pipeline plugin on {HOST}:{port} ({source}). \
             Is the Godot editor open with the godot_pipeline plugin enabled?"
        ),
        ClientError::Io(source) => format!("network error talking to the plugin: {source}"),
        ClientError::Protocol(message) => format!("unexpected response from the plugin: {message}"),
    }
}

/// Resolves `project_path` to an absolute, symlink-free path so it can be
/// compared byte-for-byte against the path the running editor reports for
/// its own project (`ProjectSettings.globalize_path("res://")`). On macOS
/// `/tmp` and `/var` are symlinks into `/private`, so a caller-supplied path
/// under either would otherwise never match the editor's canonical one.
fn canonicalize_project_path(project_path: &str) -> Result<String, String> {
    let canonical = std::fs::canonicalize(project_path)
        .map_err(|error| format!("invalid --project-path {project_path}: {error}"))?;
    canonical
        .to_str()
        .map(|path| path.trim_end_matches('/').to_string())
        .ok_or_else(|| format!("--project-path {project_path} is not valid UTF-8"))
}

/// Interprets a `set-property` value argument as JSON when it parses (so
/// `true`, `3`, `1.5`, `[1, 2]`, and `"42"` keep their JSON types) and as a
/// plain string otherwise (so `Hello` or `#ff8800` need no shell-level
/// quoting). The plugin, not the CLI, coerces the result to the property's
/// declared type.
fn parse_value_argument(raw: &str) -> serde_json::Value {
    serde_json::from_str(raw).unwrap_or_else(|_| serde_json::Value::String(raw.to_string()))
}

fn print_usage() {
    eprintln!("usage: godot-pipeline <status|scene-tree> [--port PORT]");
    eprintln!(
        "       godot-pipeline rename-node <scene-relative-path> <new-name> --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline create-node <parent-scene-relative-path> <class> <name> --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline set-property <scene-relative-path> <property> <value> --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline inspect-node --node-path <scene-relative-path> --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline query-nodes [--class <class>] [--group <group>] [--name <pattern>] [--limit <n>] --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline inspect-class --class <class> --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline delete-node <scene-relative-path> --project-path <dir> [--port PORT]"
    );
    eprintln!(
        "       godot-pipeline connect-signal <source-scene-relative-path> <signal> <target-scene-relative-path> <method> --project-path <dir> [--deferred] [--one-shot] [--port PORT]"
    );
    eprintln!("       godot-pipeline save-scene --project-path <dir> [--port PORT]");
    eprintln!(
        "       godot-pipeline open-scene --scene-path <path> --project-path <dir> [--save] [--port PORT]"
    );
    eprintln!("  status      report the editor's connection state and edited scene path");
    eprintln!("  scene-tree  report the node tree of the currently edited scene");
    eprintln!("  rename-node rename a node in the edited scene through the editor's undo/redo");
    eprintln!("              stack; '.' targets the scene root");
    eprintln!("  create-node create a built-in Node-class child under a node in the edited");
    eprintln!(
        "              scene through the editor's undo/redo stack; '.' targets the scene root"
    );
    eprintln!("  set-property set a property on a node in the edited scene through the editor's");
    eprintln!("              undo/redo stack; <value> is parsed as JSON if it can be (true, 3,");
    eprintln!("              1.5, [1, 2], \"42\"), otherwise sent as a plain string");
    eprintln!(
        "  inspect-node report a node's class, child count, and the values of its editor-visible"
    );
    eprintln!("              properties without changing the scene; '.' targets the scene root");
    eprintln!("  query-nodes search the edited scene for nodes matching every given filter");
    eprintln!(
        "              (class, group, glob name), in tree order, up to --limit (default 100,"
    );
    eprintln!("              max 1000) results; the reply's `truncated` is true when more matched");
    eprintln!(
        "  inspect-class report an engine class's ClassDB reflection: its ancestors, whether"
    );
    eprintln!(
        "              it can be instantiated, whether it is a Node subclass, and its declared"
    );
    eprintln!("              properties, methods, and signals; no edited scene is required");
    eprintln!("  delete-node remove a node and its subtree from the edited scene through the");
    eprintln!("              editor's undo/redo stack; the scene root ('.') is rejected");
    eprintln!("  connect-signal connect <signal> on a source node to <method> on a target node");
    eprintln!("              through the editor's undo/redo stack; --deferred and --one-shot");
    eprintln!("              add the matching connect flags; nothing saves until save-scene");
    eprintln!("              The success reply's data names source_path, signal, target_path,");
    eprintln!("              method, and flags (the engine's connection flags integer).");
    eprintln!("  save-scene  persist the currently edited scene to the file path it already has;");
    eprintln!("              rejected if no scene is open or the open scene has no file path");
    eprintln!(
        "  open-scene  open a scene by path in the running editor, optionally saving a dirty"
    );
    eprintln!("              edited scene first with --save; without --save a dirty edited");
    eprintln!("              scene stays open as a background tab");
    eprintln!("              A success reply lists the scenes that still have unsaved");
    eprintln!("              changes in `unsaved`; an untitled scene appears as `[\"\"]`.");
    eprintln!("  --project-path  the project the command targets; required for rename-node,");
    eprintln!(
        "                  create-node, set-property, inspect-node, query-nodes, inspect-class, delete-node, connect-signal, save-scene, and open-scene, rejected"
    );
    eprintln!("                  by the plugin if it does not match the open project");
    eprintln!("  --port      override the default port ({DEFAULT_PORT})");
}

#[cfg(test)]
mod tests {
    use super::{parse_value_argument, run};
    use crate::protocol::{HOST, Request, Response};
    use serde_json::json;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn parse_value_argument_keeps_json_types_and_falls_back_to_string() {
        assert_eq!(parse_value_argument("true"), json!(true));
        assert_eq!(parse_value_argument("-3"), json!(-3));
        assert_eq!(parse_value_argument("1.5"), json!(1.5));
        assert_eq!(parse_value_argument("[1, 2.5]"), json!([1, 2.5]));
        assert_eq!(parse_value_argument("\"42\""), json!("42"));
        assert_eq!(parse_value_argument("Hello world"), json!("Hello world"));
        assert_eq!(parse_value_argument("#ff8800"), json!("#ff8800"));
        assert_eq!(parse_value_argument(""), json!(""));
    }

    /// Pins the CLI's `save-scene` argument parsing: `--project-path` is
    /// canonicalized the same way as the other mutating commands, and the
    /// resulting request is sent to the configured `--port`. Runs `run`
    /// against a real loopback socket that answers with an ok reply, then
    /// asserts the received wire request, so parsing and transport are both
    /// exercised rather than just the data types.
    #[test]
    fn save_scene_cli_sends_a_canonicalized_project_path() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let project_dir = std::env::temp_dir();
        let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
        let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(
                request,
                Request::SaveScene {
                    project_path: canonical_arg,
                }
            );

            let mut writer = stream;
            let mut response_line =
                serde_json::to_string(&Response::Ok { data: json!({}) }).expect("serialize reply");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write reply");
        });

        let args = vec![
            "godot-pipeline".to_string(),
            "save-scene".to_string(),
            "--project-path".to_string(),
            project_dir.to_str().expect("temp dir is UTF-8").to_string(),
            "--port".to_string(),
            port.to_string(),
        ];
        run(&args).expect("run succeeds");

        server.join().expect("server thread does not panic");
    }

    /// Pins the CLI's `inspect-node` argument parsing: `--node-path` is passed
    /// through as given and `--project-path` is canonicalized the same way as
    /// the other commands, then the request is sent to the configured `--port`.
    /// Runs `run` against a real loopback socket that answers with an ok reply,
    /// then asserts the received wire request, so parsing and transport are
    /// both exercised rather than just the data types.
    #[test]
    fn inspect_node_cli_sends_a_canonicalized_project_path() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let project_dir = std::env::temp_dir();
        let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
        let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(
                request,
                Request::InspectNode {
                    node_path: "Child/Deep".to_string(),
                    project_path: canonical_arg,
                }
            );

            let mut writer = stream;
            let mut response_line =
                serde_json::to_string(&Response::Ok { data: json!({}) }).expect("serialize reply");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write reply");
        });

        let args = vec![
            "godot-pipeline".to_string(),
            "inspect-node".to_string(),
            "--node-path".to_string(),
            "Child/Deep".to_string(),
            "--project-path".to_string(),
            project_dir.to_str().expect("temp dir is UTF-8").to_string(),
            "--port".to_string(),
            port.to_string(),
        ];
        run(&args).expect("run succeeds");

        server.join().expect("server thread does not panic");
    }

    /// Pins the CLI's `query-nodes` argument parsing: the filters and limit are
    /// forwarded as given and `--project-path` is canonicalized the same way as
    /// the other commands, then the request is sent to the configured `--port`.
    /// Runs `run` against a real loopback socket that answers with an ok reply,
    /// then asserts the received wire request, so parsing and transport are
    /// both exercised rather than just the data types.
    #[test]
    fn query_nodes_cli_forwards_filters_limit_and_canonicalized_project_path() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let project_dir = std::env::temp_dir();
        let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
        let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(
                request,
                Request::QueryNodes {
                    project_path: canonical_arg,
                    class: Some("Node2D".to_string()),
                    group: Some("enemies".to_string()),
                    name: Some("Leaf*".to_string()),
                    limit: serde_json::json!(50),
                }
            );

            let mut writer = stream;
            let mut response_line =
                serde_json::to_string(&Response::Ok { data: json!({}) }).expect("serialize reply");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write reply");
        });

        let args = vec![
            "godot-pipeline".to_string(),
            "query-nodes".to_string(),
            "--class".to_string(),
            "Node2D".to_string(),
            "--group".to_string(),
            "enemies".to_string(),
            "--name".to_string(),
            "Leaf*".to_string(),
            "--limit".to_string(),
            "50".to_string(),
            "--project-path".to_string(),
            project_dir.to_str().expect("temp dir is UTF-8").to_string(),
            "--port".to_string(),
            port.to_string(),
        ];
        run(&args).expect("run succeeds");

        server.join().expect("server thread does not panic");
    }

    /// Pins the CLI's `query-nodes` default limit of 100 and null filters when
    /// no filter or limit flags are given.
    #[test]
    fn query_nodes_without_flags_uses_default_limit_and_null_filters() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let project_dir = std::env::temp_dir();
        let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
        let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(
                request,
                Request::QueryNodes {
                    project_path: canonical_arg,
                    class: None,
                    group: None,
                    name: None,
                    limit: serde_json::json!(100),
                }
            );

            let mut writer = stream;
            let mut response_line =
                serde_json::to_string(&Response::Ok { data: json!({}) }).expect("serialize reply");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write reply");
        });

        let args = vec![
            "godot-pipeline".to_string(),
            "query-nodes".to_string(),
            "--project-path".to_string(),
            project_dir.to_str().expect("temp dir is UTF-8").to_string(),
            "--port".to_string(),
            port.to_string(),
        ];
        run(&args).expect("run succeeds");

        server.join().expect("server thread does not panic");
    }

    /// Pins the CLI's `query-nodes` forwarding of a non-integer limit: `1.5`
    /// is sent as the JSON number 1.5 (not rejected up front), so the plugin's
    /// project guard can still run first and the limit validation happens on
    /// the plugin side.
    #[test]
    fn query_nodes_forwards_a_non_integer_limit_to_the_plugin() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let project_dir = std::env::temp_dir();
        let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
        let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(
                request,
                Request::QueryNodes {
                    project_path: canonical_arg,
                    class: None,
                    group: None,
                    name: None,
                    limit: serde_json::json!(1.5),
                }
            );

            let mut writer = stream;
            let mut response_line =
                serde_json::to_string(&Response::Ok { data: json!({}) }).expect("serialize reply");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write reply");
        });

        let args = vec![
            "godot-pipeline".to_string(),
            "query-nodes".to_string(),
            "--limit".to_string(),
            "1.5".to_string(),
            "--project-path".to_string(),
            project_dir.to_str().expect("temp dir is UTF-8").to_string(),
            "--port".to_string(),
            port.to_string(),
        ];
        run(&args).expect("run succeeds");

        server.join().expect("server thread does not panic");
    }

    /// Pins the CLI's rejection of `query-nodes` without `--project-path`:
    /// it must fail before any request is sent.
    #[test]
    fn query_nodes_without_project_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "query-nodes".to_string(),
            "--class".to_string(),
            "Node2D".to_string(),
        ];
        let error = run(&args).expect_err("query-nodes without --project-path must fail");
        assert!(
            error.contains("query-nodes requires --project-path"),
            "{error}"
        );
    }

    /// Pins the CLI's rejection of `inspect-node` without `--project-path`:
    /// it must fail before any request is sent.
    #[test]
    fn inspect_node_without_project_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "inspect-node".to_string(),
            "--node-path".to_string(),
            "Child".to_string(),
        ];
        let error = run(&args).expect_err("inspect-node without --project-path must fail");
        assert!(
            error.contains("inspect-node requires --project-path"),
            "{error}"
        );
    }

    /// Pins the CLI's rejection of `inspect-node` without `--node-path`:
    /// it must fail before any request is sent.
    #[test]
    fn inspect_node_without_node_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "inspect-node".to_string(),
            "--project-path".to_string(),
            "/tmp".to_string(),
        ];
        let error = run(&args).expect_err("inspect-node without --node-path must fail");
        assert!(
            error.contains("inspect-node requires --node-path"),
            "{error}"
        );
    }

    /// Pins the CLI's rejection of `save-scene` without `--project-path`:
    /// it must fail before any request is sent.
    #[test]
    fn save_scene_without_project_path_is_rejected() {
        let args = vec!["godot-pipeline".to_string(), "save-scene".to_string()];
        let error = run(&args).expect_err("save-scene without --project-path must fail");
        assert!(
            error.contains("save-scene requires --project-path"),
            "{error}"
        );
    }

    /// Pins the CLI's `inspect-class` argument parsing: `--class` is passed
    /// through as given and `--project-path` is canonicalized the same way as
    /// the other commands, then the request is sent to the configured `--port`.
    /// Runs `run` against a real loopback socket that answers with an ok reply,
    /// then asserts the received wire request, so parsing and transport are
    /// both exercised rather than just the data types.
    #[test]
    fn inspect_class_cli_sends_canonicalized_project_path_and_class() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let project_dir = std::env::temp_dir();
        let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
        let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(
                request,
                Request::InspectClass {
                    class: "Node".to_string(),
                    project_path: canonical_arg,
                }
            );

            let mut writer = stream;
            let mut response_line =
                serde_json::to_string(&Response::Ok { data: json!({}) }).expect("serialize reply");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write reply");
        });

        let args = vec![
            "godot-pipeline".to_string(),
            "inspect-class".to_string(),
            "--class".to_string(),
            "Node".to_string(),
            "--project-path".to_string(),
            project_dir.to_str().expect("temp dir is UTF-8").to_string(),
            "--port".to_string(),
            port.to_string(),
        ];
        run(&args).expect("run succeeds");

        server.join().expect("server thread does not panic");
    }

    /// Pins the CLI's rejection of `inspect-class` without `--class`: it must
    /// fail before any request is sent.
    #[test]
    fn inspect_class_without_class_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "inspect-class".to_string(),
            "--project-path".to_string(),
            "/tmp".to_string(),
        ];
        let error = run(&args).expect_err("inspect-class without --class must fail");
        assert!(error.contains("inspect-class requires --class"), "{error}");
    }

    /// Pins the CLI's rejection of `inspect-class` without `--project-path`:
    /// it must fail before any request is sent.
    #[test]
    fn inspect_class_without_project_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "inspect-class".to_string(),
            "--class".to_string(),
            "Node".to_string(),
        ];
        let error = run(&args).expect_err("inspect-class without --project-path must fail");
        assert!(
            error.contains("inspect-class requires --project-path"),
            "{error}"
        );
    }

    /// Pins the CLI's `open-scene` argument parsing: `--scene-path` is passed
    /// through as given, `--save` sets the bool (absent means false), and
    /// `--project-path` is canonicalized the same way as the other commands.
    /// Runs both cases against a real loopback socket that answers with an ok
    /// reply, then asserts the received wire request, so parsing and transport
    /// are both exercised rather than just the data types.
    #[test]
    fn open_scene_cli_sends_canonicalized_project_path_scene_path_and_save_flag() {
        for with_save in [true, false] {
            let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
            let port = listener.local_addr().expect("local addr").port();

            let project_dir = std::env::temp_dir();
            let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
            let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

            let server = thread::spawn(move || {
                let (stream, _) = listener.accept().expect("accept connection");
                let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
                let mut request_line = String::new();
                reader
                    .read_line(&mut request_line)
                    .expect("read request line");
                let request: Request =
                    serde_json::from_str(request_line.trim_end()).expect("parse request");
                assert_eq!(
                    request,
                    Request::OpenScene {
                        project_path: canonical_arg,
                        scene_path: "scenes/S1.tscn".to_string(),
                        save: with_save,
                    }
                );

                let mut writer = stream;
                let mut response_line = serde_json::to_string(&Response::Ok { data: json!({}) })
                    .expect("serialize reply");
                response_line.push('\n');
                writer
                    .write_all(response_line.as_bytes())
                    .expect("write reply");
            });

            let mut args = vec![
                "godot-pipeline".to_string(),
                "open-scene".to_string(),
                "--scene-path".to_string(),
                "scenes/S1.tscn".to_string(),
            ];
            if with_save {
                args.push("--save".to_string());
            }
            args.extend(
                [
                    "--project-path".to_string(),
                    project_dir.to_str().expect("temp dir is UTF-8").to_string(),
                    "--port".to_string(),
                    port.to_string(),
                ]
                .into_iter(),
            );
            run(&args).expect("run succeeds");

            server.join().expect("server thread does not panic");
        }
    }

    /// Pins the CLI's rejection of `open-scene` without `--scene-path`: it
    /// must fail before any request is sent.
    #[test]
    fn open_scene_without_scene_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "open-scene".to_string(),
            "--project-path".to_string(),
            "/tmp".to_string(),
        ];
        let error = run(&args).expect_err("open-scene without --scene-path must fail");
        assert!(
            error.contains("open-scene requires --scene-path"),
            "{error}"
        );
    }

    /// Pins the CLI's rejection of `open-scene` without `--project-path`: it
    /// must fail before any request is sent.
    #[test]
    fn open_scene_without_project_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "open-scene".to_string(),
            "--scene-path".to_string(),
            "scenes/S1.tscn".to_string(),
        ];
        let error = run(&args).expect_err("open-scene without --project-path must fail");
        assert!(
            error.contains("open-scene requires --project-path"),
            "{error}"
        );
    }

    /// Pins the CLI's `connect-signal` argument parsing: the four operands are
    /// passed through as given, `--deferred` and `--one-shot` set their bools
    /// (absent means false), and `--project-path` is canonicalized the same way
    /// as the other commands. Each flag combination runs against a real
    /// loopback socket that answers with an ok reply, then asserts the received
    /// wire request.
    #[test]
    fn connect_signal_cli_sends_operands_flags_and_canonicalized_project_path() {
        for (deferred, one_shot) in [(false, false), (true, false), (false, true), (true, true)] {
            let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
            let port = listener.local_addr().expect("local addr").port();

            let project_dir = std::env::temp_dir();
            let canonical = std::fs::canonicalize(&project_dir).expect("canonicalize temp dir");
            let canonical_arg = canonical.to_str().expect("temp dir is UTF-8").to_string();

            let server = thread::spawn(move || {
                let (stream, _) = listener.accept().expect("accept connection");
                let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
                let mut request_line = String::new();
                reader
                    .read_line(&mut request_line)
                    .expect("read request line");
                let request: Request =
                    serde_json::from_str(request_line.trim_end()).expect("parse request");
                assert_eq!(
                    request,
                    Request::ConnectSignal {
                        source_path: "Child/Source".to_string(),
                        signal: "ping".to_string(),
                        target_path: "Child/Target".to_string(),
                        method: "on_ping".to_string(),
                        deferred,
                        one_shot,
                        project_path: canonical_arg,
                    }
                );

                let mut writer = stream;
                let mut response_line = serde_json::to_string(&Response::Ok { data: json!({}) })
                    .expect("serialize reply");
                response_line.push('\n');
                writer
                    .write_all(response_line.as_bytes())
                    .expect("write reply");
            });

            let mut args = vec![
                "godot-pipeline".to_string(),
                "connect-signal".to_string(),
                "Child/Source".to_string(),
                "ping".to_string(),
                "Child/Target".to_string(),
                "on_ping".to_string(),
            ];
            if deferred {
                args.push("--deferred".to_string());
            }
            if one_shot {
                args.push("--one-shot".to_string());
            }
            args.extend(
                [
                    "--project-path".to_string(),
                    project_dir.to_str().expect("temp dir is UTF-8").to_string(),
                    "--port".to_string(),
                    port.to_string(),
                ]
                .into_iter(),
            );
            run(&args).expect("run succeeds");

            server.join().expect("server thread does not panic");
        }
    }

    /// Pins the CLI's rejection of `connect-signal` without `--project-path`:
    /// it must fail before any request is sent.
    #[test]
    fn connect_signal_without_project_path_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "connect-signal".to_string(),
            "Child/Source".to_string(),
            "ping".to_string(),
            "Child/Target".to_string(),
            "on_ping".to_string(),
        ];
        let error = run(&args).expect_err("connect-signal without --project-path must fail");
        assert!(
            error.contains("connect-signal requires --project-path"),
            "{error}"
        );
    }

    /// Pins the CLI's rejection of `connect-signal` with a missing operand:
    /// it must fail before any request is sent.
    #[test]
    fn connect_signal_without_operands_is_rejected() {
        let args = vec![
            "godot-pipeline".to_string(),
            "connect-signal".to_string(),
            "Child/Source".to_string(),
            "ping".to_string(),
        ];
        let error = run(&args).expect_err("connect-signal without all operands must fail");
        assert!(
            error.contains("connect-signal requires <source-scene-relative-path>"),
            "{error}"
        );
    }
}
