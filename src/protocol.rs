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
    /// Removes a node and its subtree from the currently edited scene
    /// through the editor's undo/redo manager, without saving. The scene
    /// root itself is rejected. `project_path` follows the same rules as
    /// [`Request::RenameNode`].
    DeleteNode {
        node_path: String,
        project_path: String,
    },
    /// Persists the currently edited scene to the file path it already has,
    /// so edits made through the other mutating commands survive a reload.
    /// `project_path` follows the same rules as [`Request::RenameNode`].
    SaveScene {
        project_path: String,
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
}
