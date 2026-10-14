//! Wire protocol shared with the `godot_pipeline` GDScript editor plugin.
//!
//! The protocol is one line of JSON in, one line of JSON out, over a plain
//! loopback TCP connection: the client writes a single request object and
//! reads back a single [`Response`] line before the plugin closes the
//! connection. Requests are built from the interface the plugin publishes, so
//! this module knows no command of its own.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// Fixed loopback port the editor plugin listens on.
pub const DEFAULT_PORT: u16 = 47821;
/// The plugin only ever binds the loopback interface.
pub const HOST: &str = "127.0.0.1";

const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const READ_TIMEOUT: Duration = Duration::from_secs(5);

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
/// one-request-per-connection contract. `max_reply_bytes` bounds what is read
/// back: the interface fetch passes its bound so a hostile or broken editor
/// cannot make the client allocate without limit, and a command passes `None`
/// because the plugin answers with whatever the command produced, which can be
/// tens of megabytes.
pub fn send_request(
    host: &str,
    port: u16,
    request: &serde_json::Value,
    max_reply_bytes: Option<u64>,
) -> Result<Response, ClientError> {
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
    let bytes_read = match max_reply_bytes {
        // One byte over the bound: a reply that exactly fills it still ends
        // in its newline, and a longer one does not, so one read decides.
        Some(limit) => reader
            .take(limit + 1)
            .read_line(&mut response_line)
            .map_err(ClientError::Io)?,
        None => reader
            .read_line(&mut response_line)
            .map_err(ClientError::Io)?,
    };
    if bytes_read == 0 {
        return Err(ClientError::Protocol(
            "connection closed before a response was received".to_string(),
        ));
    }
    // The bound is read one byte over, so a reply that exactly fills it ends
    // in its newline and a longer one does not. A reply that ends without a
    // newline is refused either way; the message says which of the two it was,
    // because the real plugin always terminates its replies.
    if let Some(limit) = max_reply_bytes
        && !response_line.ends_with('\n')
    {
        let message = if bytes_read as u64 > limit {
            format!("the reply is larger than {limit} bytes")
        } else {
            "the reply did not end with a newline".to_string()
        };
        return Err(ClientError::Protocol(message));
    }

    serde_json::from_str(response_line.trim_end())
        .map_err(|error| ClientError::Protocol(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    /// Runs one request against a real loopback socket that speaks the exact
    /// wire format (one JSON line in, one JSON line out), proving the framing
    /// round-trip rather than just the data types.
    #[test]
    fn send_request_round_trips_over_a_real_socket() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();
        let request = serde_json::json!({"command": "status"});
        let expected = request.clone();

        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept connection");
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut request_line = String::new();
            reader
                .read_line(&mut request_line)
                .expect("read request line");
            let received: serde_json::Value =
                serde_json::from_str(request_line.trim_end()).expect("parse request");
            assert_eq!(received, expected);

            let mut writer = stream;
            let mut response_line = serde_json::to_string(&Response::Ok {
                data: serde_json::json!({}),
            })
            .expect("serialize response");
            response_line.push('\n');
            writer
                .write_all(response_line.as_bytes())
                .expect("write response");
        });

        let response = send_request(HOST, port, &request, None).expect("send_request succeeds");
        assert_eq!(
            response,
            Response::Ok {
                data: serde_json::json!({}),
            }
        );

        server.join().expect("server thread does not panic");
    }

    #[test]
    fn send_request_reports_connection_refused_as_a_client_error() {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();
        drop(listener); // Free the port so the connection below is refused.

        let error = send_request(HOST, port, &serde_json::json!({}), None)
            .expect_err("connection should fail");
        assert!(matches!(error, ClientError::Connect(_)));
    }
}
