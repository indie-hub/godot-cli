//! Wire protocol shared with the `godot_pipeline` GDScript editor plugin.
//!
//! The protocol is one line of JSON in, one line of JSON out, over a plain
//! loopback TCP connection: the client writes a single [`Request`] line and
//! reads back a single [`Response`] line before the plugin closes the
//! connection. There is no discovery step and the port is fixed, so only one
//! Godot editor instance can host the plugin on a given machine at a time.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// Fixed loopback port the editor plugin listens on.
pub const DEFAULT_PORT: u16 = 47821;
/// The plugin only ever binds the loopback interface.
pub const HOST: &str = "127.0.0.1";

const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const READ_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
#[serde(tag = "command", rename_all = "snake_case")]
pub enum Request {
    Status,
    SceneTree,
    /// Renames a node within the currently edited scene through the
    /// editor's undo/redo manager. `project_path` must be the canonical
    /// (symlink-resolved) absolute path of the project the caller intends
    /// to edit; the plugin rejects the request if it does not match the
    /// running editor's project, before making any change.
    RenameNode {
        node_path: String,
        new_name: String,
        project_path: String,
    },
    /// Creates a new built-in `Node`-subclass child under a node in the
    /// currently edited scene through the editor's undo/redo manager.
    /// `project_path` must be the canonical (symlink-resolved) absolute path
    /// of the project the caller intends to edit; the plugin rejects the
    /// request if it does not match the running editor's project, before
    /// making any change.
    CreateNode {
        parent_path: String,
        class_name: String,
        name: String,
        project_path: String,
    },
    /// Sets a property on an existing node in the currently edited scene
    /// through the editor's undo/redo manager. `value` is untyped JSON; the
    /// plugin coerces it to the property's declared Variant type and rejects
    /// the request if it cannot. `project_path` follows the same rules as
    /// [`Request::RenameNode`].
    SetProperty {
        node_path: String,
        property: String,
        value: serde_json::Value,
        project_path: String,
    },
    /// Reports a node's class, child count, and the values of its inspector
    /// and storage properties without changing the scene. `project_path`
    /// follows the same rules as [`Request::RenameNode`].
    InspectNode {
        node_path: String,
        project_path: String,
    },
    /// Searches the edited scene's whole node tree for nodes matching every
    /// provided filter, in tree order, without changing the scene. `class`,
    /// `group`, and `name` are optional filters; `limit` is a JSON number the
    /// plugin validates (default 100, max 1000). `project_path` follows the
    /// same rules as [`Request::RenameNode`].
    QueryNodes {
        project_path: String,
        class: Option<String>,
        group: Option<String>,
        name: Option<String>,
        limit: serde_json::Value,
    },
    /// Reports an engine class's ClassDB reflection (ancestors, properties,
    /// methods, signals) without changing the scene and without requiring an
    /// edited scene. `class` must name an engine class. `project_path` follows
    /// the same rules as [`Request::RenameNode`].
    InspectClass {
        class: String,
        project_path: String,
    },
    /// Removes a node and its subtree from the currently edited scene
    /// through the editor's undo/redo manager, without saving. The scene
    /// root itself is rejected. `project_path` follows the same rules as
    /// [`Request::RenameNode`].
    DeleteNode {
        node_path: String,
        project_path: String,
    },
    /// Connects a signal from one node to a method on another node in the
    /// currently edited scene through the editor's undo/redo manager, as one
    /// Undo/Redo step, without saving. `deferred` and `one_shot` add the
    /// matching connect flags to `CONNECT_PERSIST`. `project_path` follows the
    /// same rules as [`Request::RenameNode`].
    ConnectSignal {
        source_path: String,
        signal: String,
        target_path: String,
        method: String,
        deferred: bool,
        one_shot: bool,
        project_path: String,
    },
    /// Adds or removes one persistent group on a node in the currently edited
    /// scene through the editor's undo/redo manager, as one Undo/Redo step,
    /// without saving. `remove` false (the default) adds the group; true
    /// removes it. A group inherited from a sub-scene and a runtime (session)
    /// group are rejected before any change, because neither survives a save
    /// or a reload. `project_path` follows the same rules as
    /// [`Request::RenameNode`].
    SetGroup {
        node_path: String,
        group: String,
        remove: bool,
        project_path: String,
    },
    /// Sets or clears `Node.unique_name_in_owner` (the `%` name) on a node in
    /// the currently edited scene through the editor's undo/redo manager, as
    /// one Undo/Redo step, without saving. `remove` false (the default) sets
    /// the flag on the node; true clears it. The scene root, a node inside a
    /// non-editable instance, and a request that would do nothing or collide
    /// with another unique name in the same owner scope are rejected before
    /// any change. `project_path` follows the same rules as
    /// [`Request::RenameNode`].
    SetUniqueName {
        node_path: String,
        remove: bool,
        project_path: String,
    },
    /// Persists the currently edited scene to the file path it already has,
    /// so edits made through the other mutating commands survive a reload.
    /// `project_path` follows the same rules as [`Request::RenameNode`].
    SaveScene {
        project_path: String,
    },
    /// Opens a scene in the running editor by path, optionally saving a dirty
    /// edited scene first (`save` defaults to false). `scene_path` may be a
    /// res://, relative, absolute-inside-project, "..", or uid:// path, as
    /// long as it resolves to a scene inside the project. `project_path`
    /// follows the same rules as [`Request::RenameNode`].
    OpenScene {
        project_path: String,
        scene_path: String,
        save: bool,
    },
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Clone)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Response {
    Ok { data: serde_json::Value },
    Error { message: String },
}

#[derive(Debug)]
pub enum ClientError {
    Connect(std::io::Error),
    Io(std::io::Error),
    Protocol(String),
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ClientError::Connect(source) => write!(f, "could not connect: {source}"),
            ClientError::Io(source) => write!(f, "network error: {source}"),
            ClientError::Protocol(message) => write!(f, "protocol error: {message}"),
        }
    }
}

impl std::error::Error for ClientError {}

/// Sends `request` to `host:port` and returns the plugin's single-line
/// response. Opens a fresh connection per call, matching the plugin's
/// one-request-per-connection contract.
pub fn send_request(host: &str, port: u16, request: &Request) -> Result<Response, ClientError> {
    let address = (host, port)
        .to_socket_addrs()
        .map_err(ClientError::Connect)?
        .next()
        .ok_or_else(|| {
            ClientError::Connect(std::io::Error::other(format!(
                "{host}:{port} did not resolve to an address"
            )))
        })?;

    let mut stream =
        TcpStream::connect_timeout(&address, CONNECT_TIMEOUT).map_err(ClientError::Connect)?;
    stream
        .set_read_timeout(Some(READ_TIMEOUT))
        .map_err(ClientError::Io)?;

    let mut line =
        serde_json::to_string(request).map_err(|error| ClientError::Protocol(error.to_string()))?;
    line.push('\n');
    stream.write_all(line.as_bytes()).map_err(ClientError::Io)?;
    stream.flush().map_err(ClientError::Io)?;

    let mut reader = BufReader::new(stream);
    let mut response_line = String::new();
    let bytes_read = reader
        .read_line(&mut response_line)
        .map_err(ClientError::Io)?;
    if bytes_read == 0 {
        return Err(ClientError::Protocol(
            "connection closed before a response was received".to_string(),
        ));
    }

    serde_json::from_str(response_line.trim_end())
        .map_err(|error| ClientError::Protocol(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    /// Runs the request against a real loopback socket that speaks the exact
    /// wire format (one JSON line in, one JSON line out), proving the framing
    /// and serialization round-trip rather than just the data types.
    #[test]
    fn send_request_round_trips_over_a_real_socket() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let request: Request =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(request, Request::Status);

            let response = Response::Ok {
                data: serde_json::json!({ "editor": "Godot Editor", "playing": false }),
            };
            let mut writer = stream;
            let mut response_line = serde_json::to_string(&response).expect("serialize response");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write response");
        });

        let response = send_request(HOST, port, &Request::Status).expect("send_request succeeds");
        assert_eq!(
            response,
            Response::Ok {
                data: serde_json::json!({ "editor": "Godot Editor", "playing": false }),
            }
        );

        server.join().expect("server thread does not panic");
    }

    #[test]
    fn send_request_reports_connection_refused_as_a_client_error() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();
        drop(listener); // Free the port so the connection below is refused.

        let error = send_request(HOST, port, &Request::Status).expect_err("connection should fail");
        assert!(matches!(error, ClientError::Connect(_)));
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"rename_node"` plus the three snake_case fields); a
    /// silent rename in `#[serde(...)]` here would desync the two sides
    /// without either one failing to compile.
    #[test]
    fn rename_node_request_serializes_to_the_documented_wire_shape() {
        let request = Request::RenameNode {
            node_path: "Child/Deep".to_string(),
            new_name: "Renamed".to_string(),
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"rename_node","node_path":"Child/Deep","new_name":"Renamed","project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"create_node"` plus the four snake_case fields); a silent
    /// rename in `#[serde(...)]` here would desync the two sides without
    /// either one failing to compile.
    #[test]
    fn create_node_request_serializes_to_the_documented_wire_shape() {
        let request = Request::CreateNode {
            parent_path: "Child/Deep".to_string(),
            class_name: "Node2D".to_string(),
            name: "NewNode".to_string(),
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"create_node","parent_path":"Child/Deep","class_name":"Node2D","name":"NewNode","project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"set_property"` plus the four snake_case fields, with
    /// `value` passed through as raw JSON rather than a string).
    #[test]
    fn set_property_request_serializes_to_the_documented_wire_shape() {
        let request = Request::SetProperty {
            node_path: "Child/Deep".to_string(),
            property: "position".to_string(),
            value: serde_json::json!([1.5, -2]),
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"set_property","node_path":"Child/Deep","property":"position","value":[1.5,-2],"project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"inspect_node"` plus the two snake_case fields); a silent
    /// rename in `#[serde(...)]` here would desync the two sides without
    /// either one failing to compile.
    #[test]
    fn inspect_node_request_serializes_to_the_documented_wire_shape() {
        let request = Request::InspectNode {
            node_path: "Child/Deep".to_string(),
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"inspect_node","node_path":"Child/Deep","project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"query_nodes"` plus the filter, limit, and project_path
    /// fields); a silent rename in `#[serde(...)]` here would desync the two
    /// sides without either one failing to compile.
    #[test]
    fn query_nodes_request_serializes_to_the_documented_wire_shape() {
        let request = Request::QueryNodes {
            project_path: "/tmp/project".to_string(),
            class: Some("Node2D".to_string()),
            group: Some("enemies".to_string()),
            name: Some("Leaf*".to_string()),
            limit: serde_json::json!(50),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"query_nodes","project_path":"/tmp/project","class":"Node2D","group":"enemies","name":"Leaf*","limit":50}"#
        );
    }

    /// Pins the no-filter wire shape: absent filters serialize as null and the
    /// CLI's default limit is 100, which the plugin treats the same as a typed
    /// request.
    #[test]
    fn query_nodes_without_filters_serializes_null_filters_and_default_limit() {
        let request = Request::QueryNodes {
            project_path: "/tmp/project".to_string(),
            class: None,
            group: None,
            name: None,
            limit: serde_json::json!(100),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"query_nodes","project_path":"/tmp/project","class":null,"group":null,"name":null,"limit":100}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"inspect_class"` plus the two snake_case fields); a silent
    /// rename in `#[serde(...)]` here would desync the two sides without
    /// either one failing to compile.
    #[test]
    fn inspect_class_request_serializes_to_the_documented_wire_shape() {
        let request = Request::InspectClass {
            class: "Node".to_string(),
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"inspect_class","class":"Node","project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"delete_node"` plus the two snake_case fields); a silent
    /// rename in `#[serde(...)]` here would desync the two sides without
    /// either one failing to compile.
    #[test]
    fn delete_node_request_serializes_to_the_documented_wire_shape() {
        let request = Request::DeleteNode {
            node_path: "Child/Deep".to_string(),
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"delete_node","node_path":"Child/Deep","project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"connect_signal"` plus the seven snake_case fields, with
    /// `deferred` and `one_shot` as JSON bools).
    #[test]
    fn connect_signal_request_serializes_to_the_documented_wire_shape() {
        let request = Request::ConnectSignal {
            source_path: "Child/Source".to_string(),
            signal: "ping".to_string(),
            target_path: "Child/Target".to_string(),
            method: "on_ping".to_string(),
            deferred: false,
            one_shot: false,
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"connect_signal","source_path":"Child/Source","signal":"ping","target_path":"Child/Target","method":"on_ping","deferred":false,"one_shot":false,"project_path":"/tmp/project"}"#
        );
    }

    /// Pins that `--deferred` and `--one-shot` reach the wire as real JSON
    /// bools, not null or absent.
    #[test]
    fn connect_signal_with_flags_serializes_them_as_true() {
        let request = Request::ConnectSignal {
            source_path: "A".to_string(),
            signal: "ping".to_string(),
            target_path: "B".to_string(),
            method: "on_ping".to_string(),
            deferred: true,
            one_shot: true,
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"connect_signal","source_path":"A","signal":"ping","target_path":"B","method":"on_ping","deferred":true,"one_shot":true,"project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"set_group"` plus the four snake_case fields, with `remove`
    /// as a JSON bool).
    #[test]
    fn set_group_request_serializes_to_the_documented_wire_shape() {
        let request = Request::SetGroup {
            node_path: "Child/Deep".to_string(),
            group: "enemies".to_string(),
            remove: false,
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"set_group","node_path":"Child/Deep","group":"enemies","remove":false,"project_path":"/tmp/project"}"#
        );
    }

    /// Pins that `--remove` reaches the wire as the real JSON bool true, not
    /// null or absent.
    #[test]
    fn set_group_with_remove_serializes_remove_as_true() {
        let request = Request::SetGroup {
            node_path: ".".to_string(),
            group: "heroes".to_string(),
            remove: true,
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"set_group","node_path":".","group":"heroes","remove":true,"project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"set_unique_name"` plus the three snake_case fields, with
    /// `remove` as a JSON bool).
    #[test]
    fn set_unique_name_request_serializes_to_the_documented_wire_shape() {
        let request = Request::SetUniqueName {
            node_path: "Child/Deep".to_string(),
            remove: false,
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"set_unique_name","node_path":"Child/Deep","remove":false,"project_path":"/tmp/project"}"#
        );
    }

    /// Pins that `--remove` reaches the wire as the real JSON bool true, not
    /// null or absent.
    #[test]
    fn set_unique_name_with_remove_serializes_remove_as_true() {
        let request = Request::SetUniqueName {
            node_path: "Child".to_string(),
            remove: true,
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"set_unique_name","node_path":"Child","remove":true,"project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"save_scene"` plus the one snake_case field); a silent
    /// rename in `#[serde(...)]` here would desync the two sides without
    /// either one failing to compile.
    #[test]
    fn save_scene_request_serializes_to_the_documented_wire_shape() {
        let request = Request::SaveScene {
            project_path: "/tmp/project".to_string(),
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"save_scene","project_path":"/tmp/project"}"#
        );
    }

    /// Pins the exact wire shape the GDScript plugin matches on
    /// (`"command":"open_scene"` plus the three snake_case fields, with
    /// `save` passed through as a JSON bool).
    #[test]
    fn open_scene_request_serializes_to_the_documented_wire_shape() {
        let request = Request::OpenScene {
            project_path: "/tmp/project".to_string(),
            scene_path: "scenes/S1.tscn".to_string(),
            save: true,
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"open_scene","project_path":"/tmp/project","scene_path":"scenes/S1.tscn","save":true}"#
        );
    }

    /// Pins the default wire shape when `--save` is not given: `save` is a
    /// real JSON bool false, not null or absent, so the plugin sees a typed
    /// field.
    #[test]
    fn open_scene_without_save_serializes_save_as_false() {
        let request = Request::OpenScene {
            project_path: "/tmp/project".to_string(),
            scene_path: "scenes/S1.tscn".to_string(),
            save: false,
        };

        let json = serde_json::to_string(&request).expect("serialize request");

        assert_eq!(
            json,
            r#"{"command":"open_scene","project_path":"/tmp/project","scene_path":"scenes/S1.tscn","save":false}"#
        );
    }
}
