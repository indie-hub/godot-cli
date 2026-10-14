//! Godot Pipeline CLI.
//!
//! Talks to the `godot_pipeline` GDScript editor plugin over a loopback TCP
//! socket and prints its JSON response. Every command, argument and type is
//! read from the interface the plugin publishes, so this file names no
//! command of its own. See `protocol` for the wire format and `README.md` for
//! how to install the plugin and the one-editor-per-port limitation.

mod protocol;

use protocol::{ClientError, DEFAULT_PORT, HOST, Response, send_request};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::collections::HashMap;
use std::process::ExitCode;
use std::thread;
use std::time::Duration;

/// The `list-resources --refresh` poll interval and overall timeout. The
/// plugin cannot reply after it has sent the scanning reply, so the CLI waits
/// here and repeats the request until the scan finishes.
const REFRESH_POLL_INTERVAL: Duration = Duration::from_millis(250);
const REFRESH_TIMEOUT: Duration = Duration::from_secs(60);

/// The interface version this build speaks. Any other version is refused
/// rather than guessed at.
const PROTOCOL_VERSION: u64 = 1;

/// The reply a plugin that predates the interface gives to the fetch: it has
/// no `commands` request, so it treats the name as unknown like any other.
const UNKNOWN_COMMANDS_REPLY: &str = "unknown command: commands";

/// The one argument every command that edits a scene carries. It is global on
/// the command line and canonicalized before the command request is sent.
const PROJECT_PATH: &str = "project_path";

/// What a reply to the interface fetch may weigh. A command reply is read
/// whole, because the plugin answers with whatever the command produced, so
/// the bound applies to the one reply that comes from outside the plugin's own
/// command handlers.
const MAX_INTERFACE_BYTES: u64 = 8_388_608;

/// The argument kinds this build can encode. A kind the plugin names that is
/// not one of them is refused with the interface, before any request is built.
const SUPPORTED_KINDS: [&str; 5] = ["string", "boolean", "json", "res_path", "integer"];

/// What the plugin publishes for one argument. Deserialized from the
/// interface, never written here.
#[derive(Deserialize)]
struct ArgumentInterface {
    name: String,
    flag: Option<String>,
    positional: Option<u64>,
    #[serde(rename = "type")]
    kind: String,
    required: bool,
    #[serde(default)]
    default: Value,
    help: String,
}

/// What the plugin publishes for one command.
#[derive(Deserialize)]
struct CommandInterface {
    name: String,
    description: String,
    #[serde(default)]
    needs_project_path: bool,
    #[serde(default)]
    refresh_poll: bool,
    #[serde(default)]
    arguments: Vec<ArgumentInterface>,
}

/// The whole interface, as the plugin's `commands` reply carries it.
#[derive(Deserialize)]
struct Interface {
    protocol_version: u64,
    commands: Vec<CommandInterface>,
}

impl ArgumentInterface {
    /// How the argument is named on the command line: its flag, or a
    /// placeholder for a positional.
    fn label(&self) -> String {
        match &self.flag {
            Some(flag) => flag.clone(),
            None => format!("<{}>", self.name),
        }
    }
}

impl CommandInterface {
    /// The command-line name, derived from the wire name the plugin uses.
    fn cli_name(&self) -> String {
        self.name.replace('_', "-")
    }
}

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

/// Reads the fixed part of the command line and runs what it asks for. The
/// fixed part is the port, the project path, the help and version flags and
/// the `call` and `commands` subcommands; everything after a subcommand
/// belongs to that subcommand.
fn run(args: &[String]) -> Result<(), String> {
    let mut port = DEFAULT_PORT;
    let mut project_path: Option<String> = None;
    let mut help = false;
    let mut version = false;
    let mut subcommand: Option<&str> = None;
    let mut rest: Vec<String> = Vec::new();

    let mut arguments = args.iter().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--port" => {
                let value = arguments.next().ok_or("--port requires a value")?;
                port = value
                    .parse::<u16>()
                    .map_err(|_| format!("invalid port: {value}"))?;
            }
            "--project-path" => {
                project_path = Some(
                    arguments
                        .next()
                        .ok_or("--project-path requires a value")?
                        .clone(),
                );
            }
            "--help" | "-h" => help = true,
            "--version" => version = true,
            other if subcommand.is_none() && matches!(other, "call" | "commands") => {
                subcommand = Some(other);
            }
            _ if subcommand.is_some() => rest.push(argument.clone()),
            other => return Err(format!("unknown argument: {other}")),
        }
    }

    if version {
        println!("godot-cli {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if help {
        let named = match subcommand {
            Some("call") => rest.first().map(String::as_str),
            _ => None,
        };
        return print_usage(port, named);
    }
    match subcommand {
        Some("call") => call_command(&rest, port, project_path.as_deref()),
        Some("commands") => list_commands(port, &rest),
        Some(_) => unreachable!("only call and commands are recognized above"),
        None => {
            print_usage(port, None)?;
            Err("missing command (expected 'call' or 'commands')".to_string())
        }
    }
}

/// Runs one plugin command: fetch its interface, build the request from it,
/// send it, print the reply. Every check runs before the request is built, so
/// a rejected invocation sends no command request.
fn call_command(args: &[String], port: u16, project_path: Option<&str>) -> Result<(), String> {
    let interface = parse_interface(&fetch_commands(port)?)?;
    let Some((name, arguments)) = args.split_first() else {
        return Err("missing command name after 'call'".to_string());
    };
    let command = find_command(&interface, name)?;
    let request = build_request(command, arguments, project_path)?;
    let response = match command.refresh_poll && request.get("refresh") == Some(&json!(true)) {
        true => poll_refresh(
            &request,
            // A command reply is read whole: only the interface fetch is bounded.
            |current| send(port, current, None),
            thread::sleep,
            REFRESH_TIMEOUT,
        )?,
        false => send(port, &request, None)?,
    };
    match response {
        Response::Ok { data } => {
            let pretty = serde_json::to_string_pretty(&data).map_err(|error| error.to_string())?;
            println!("{pretty}");
            Ok(())
        }
        Response::Error { message } => Err(format!("plugin reported an error: {message}")),
    }
}

/// Prints the plugin's command list, one line per command, or the interface
/// exactly as the plugin sent it.
fn list_commands(port: u16, args: &[String]) -> Result<(), String> {
    let mut json_output = false;
    for argument in args {
        match argument.as_str() {
            "--json" => json_output = true,
            other => return Err(format!("unknown argument for commands: {other}")),
        }
    }
    let data = fetch_commands(port)?;
    // Both output modes validate the interface first: a version this build
    // does not speak, or an argument kind it cannot encode, is refused whether
    // or not the data is printed verbatim.
    let interface = parse_interface(&data)?;
    if json_output {
        println!("{data}");
        return Ok(());
    }
    print_command_list(&interface);
    Ok(())
}

/// Asks the running editor what it can do. There is no cache: the list must
/// come from the editor that will run the command, so nothing about it is
/// kept across invocations.
fn fetch_commands(port: u16) -> Result<Value, String> {
    let request = json!({ "command": "commands" });
    match send(port, &request, Some(MAX_INTERFACE_BYTES))? {
        Response::Error { message } if message.starts_with(UNKNOWN_COMMANDS_REPLY) => {
            Err("the editor plugin is too old for this godot-cli; update the plugin".to_string())
        }
        Response::Error { message } => Err(format!("plugin reported an error: {message}")),
        Response::Ok { data } => Ok(data),
    }
}

fn send(port: u16, request: &Value, max_reply_bytes: Option<u64>) -> Result<Response, String> {
    send_request(HOST, port, request, max_reply_bytes)
        .map_err(|error| describe_client_error(&error, port))
}

/// Turns the plugin's `commands` reply into an interface, refusing a version
/// this build does not speak instead of guessing at its fields, and any
/// argument kind it cannot encode, so a request is never built from one.
fn parse_interface(data: &Value) -> Result<Interface, String> {
    let interface: Interface = serde_json::from_value(data.clone())
        .map_err(|error| format!("unexpected command interface from the plugin: {error}"))?;
    if interface.protocol_version != PROTOCOL_VERSION {
        return Err(format!(
            "unsupported protocol version: {} (this godot-cli knows {PROTOCOL_VERSION})",
            interface.protocol_version
        ));
    }
    for command in &interface.commands {
        for argument in &command.arguments {
            if !SUPPORTED_KINDS.contains(&argument.kind.as_str()) {
                return Err(format!(
                    "unknown argument type from the plugin for {}: {}",
                    argument.name, argument.kind
                ));
            }
        }
    }
    Ok(interface)
}

fn find_command<'a>(
    interface: &'a Interface,
    cli_name: &str,
) -> Result<&'a CommandInterface, String> {
    interface
        .commands
        .iter()
        .find(|command| command.cli_name() == cli_name)
        .ok_or_else(|| format!("unknown command: {cli_name}"))
}

/// Builds the wire request: the command's wire name plus one entry per
/// argument, in the interface's order.
fn build_request(
    command: &CommandInterface,
    args: &[String],
    project_path: Option<&str>,
) -> Result<Value, String> {
    let supplied = bind_arguments(command, args)?;
    // The interface says whether the command takes a project path, so the
    // global flag is canonicalized for exactly those commands.
    let canonical_project_path = match project_path {
        Some(path) if command.needs_project_path => Some(canonicalize_project_path(path)?),
        _ => None,
    };
    let mut request = Map::new();
    request.insert("command".to_string(), json!(command.name));
    for argument in &command.arguments {
        let value = match argument.name.as_str() {
            // The plugin compares the project path with its own canonical
            // path, so it is canonicalized before the command request is sent.
            // A command whose interface takes none ignores the global flag.
            PROJECT_PATH => match &canonical_project_path {
                Some(path) => json!(path),
                None if argument.required => return Err(missing_required(command, argument)),
                None => argument.default.clone(),
            },
            _ => match supplied.get(&argument.name) {
                Some(raw) => encode_value(argument, raw, canonical_project_path.as_deref())?,
                None if argument.required => return Err(missing_required(command, argument)),
                None => argument.default.clone(),
            },
        };
        request.insert(argument.name.clone(), value);
    }
    Ok(Value::Object(request))
}

/// Binds the call arguments to the interface: flags by the flag the
/// interface declares, positionals by the index it declares. A flag or an
/// extra positional the interface does not declare is refused here.
fn bind_arguments(
    command: &CommandInterface,
    args: &[String],
) -> Result<HashMap<String, String>, String> {
    let mut supplied = HashMap::new();
    let mut position = 0u64;
    let mut arguments = args.iter();
    while let Some(argument) = arguments.next() {
        if argument.starts_with("--") {
            let declared = command
                .arguments
                .iter()
                .find(|candidate| candidate.flag.as_deref() == Some(argument.as_str()));
            let Some(declared) = declared else {
                return Err(format!(
                    "unknown argument for {}: {argument}",
                    command.cli_name()
                ));
            };
            let value = if declared.kind == "boolean" {
                // A boolean takes no value: the flag's presence means true.
                "true".to_string()
            } else {
                arguments
                    .next()
                    .cloned()
                    .ok_or_else(|| format!("{} requires a value", declared.label()))?
            };
            supplied.insert(declared.name.clone(), value);
            continue;
        }
        let declared = command
            .arguments
            .iter()
            .find(|candidate| candidate.positional == Some(position));
        let Some(declared) = declared else {
            let expected = command
                .arguments
                .iter()
                .filter(|argument| argument.positional.is_some())
                .count();
            return Err(format!(
                "too many arguments for {}: expected {expected}",
                command.cli_name()
            ));
        };
        supplied.insert(declared.name.clone(), argument.clone());
        position += 1;
    }
    Ok(supplied)
}

/// Encodes one command-line value with the type the interface declares.
fn encode_value(
    argument: &ArgumentInterface,
    raw: &str,
    canonical_project_path: Option<&str>,
) -> Result<Value, String> {
    Ok(match argument.kind.as_str() {
        "string" => json!(raw),
        "boolean" => json!(true),
        "json" => parse_value_argument(raw),
        "integer" => json!(parse_integer(raw).ok_or_else(|| {
            format!(
                "invalid {} value: expected an integer, got {raw}",
                argument.label()
            )
        })?),
        // The plugin cannot resolve a symlink outside the project, so that
        // check stays here; a command with no project path has none to check.
        "res_path" => json!(match canonical_project_path {
            Some(path) => resolve_resource_path(path, raw)?,
            None => raw.to_string(),
        }),
        other => {
            return Err(format!(
                "unknown argument type from the plugin for {}: {other}",
                argument.name
            ));
        }
    })
}

fn missing_required(command: &CommandInterface, argument: &ArgumentInterface) -> String {
    match argument.positional {
        Some(position) => format!(
            "missing positional argument {} ({}) for {}",
            position + 1,
            argument.name,
            command.cli_name()
        ),
        None => format!(
            "missing required argument for {}: {}",
            command.cli_name(),
            argument.label()
        ),
    }
}

/// Parses a JSON integer literal: an optional minus sign, then decimal digits
/// with no leading zero, and a value that fits in 64 bits. `abc`, `+5`, `1e2`,
/// `1.5` and `007` are refused here, before the plugin is asked.
fn parse_integer(raw: &str) -> Option<i64> {
    let digits = raw.strip_prefix('-').unwrap_or(raw);
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    if digits.len() > 1 && digits.starts_with('0') {
        return None;
    }
    raw.parse::<i64>().ok()
}

/// Prints the fixed usage, the plugin's command list when an editor answers,
/// or one plain line saying the list needs an editor. With a command name,
/// prints that command's usage from its interface instead.
fn print_usage(port: u16, command: Option<&str>) -> Result<(), String> {
    let Some(command) = command else {
        println!("usage: godot-cli [--port PORT] [--project-path <dir>] <call|commands> ...");
        println!(
            "       godot-cli call <command> [arguments] [--port PORT] [--project-path <dir>]"
        );
        println!("       godot-cli commands [--json] [--port PORT]");
        println!("       godot-cli --help | --version");
        println!(
            "  --project-path  the project the command targets; required when the command needs it"
        );
        println!("  --port          override the default port ({DEFAULT_PORT})");
        println!("  call            run one plugin command (needs a running editor)");
        println!("  commands        print the plugin command list (needs a running editor)");
        match fetch_commands(port) {
            Ok(data) => print_command_list(&parse_interface(&data)?),
            Err(_) => println!(
                "  plugin commands: need a running editor (start it and retry 'godot-cli commands')"
            ),
        }
        return Ok(());
    };
    let interface = parse_interface(&fetch_commands(port)?)?;
    print_command_usage(find_command(&interface, command)?);
    Ok(())
}

fn print_command_list(interface: &Interface) {
    for command in &interface.commands {
        println!("{} - {}", command.cli_name(), command.description);
    }
}

/// Prints one command's usage from its interface: the positional placeholders
/// and flags in interface order, the description, and one line per argument.
fn print_command_usage(command: &CommandInterface) {
    let mut usage = format!("usage: godot-cli call {}", command.cli_name());
    for argument in command.arguments.iter().filter(|a| a.positional.is_some()) {
        usage.push_str(&format!(" <{}>", argument.name));
    }
    for argument in &command.arguments {
        let Some(flag) = &argument.flag else { continue };
        usage.push_str(&format!(" {flag}"));
        if argument.kind != "boolean" {
            usage.push_str(" <value>");
        }
    }
    println!("{usage}");
    println!("  {}", command.description);
    let width = command
        .arguments
        .iter()
        .map(|argument| argument.label().len())
        .max()
        .unwrap_or_default();
    for argument in &command.arguments {
        println!(
            "  {:width$}  {}",
            argument.label(),
            argument.help,
            width = width
        );
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

/// Sends `request` (whose `refresh` is true), then repeats it with `refresh`
/// false every `REFRESH_POLL_INTERVAL` until the plugin reports a finished
/// scan or an error, and returns the final response. Fails with the timeout
/// message once `timeout` passes first. `sleep` is a parameter so the wait is
/// testable without real time.
fn poll_refresh<F, S>(
    request: &Value,
    mut send: F,
    mut sleep: S,
    timeout: Duration,
) -> Result<Response, String>
where
    F: FnMut(&Value) -> Result<Response, String>,
    S: FnMut(Duration),
{
    let mut response = send(request)?;
    if refresh_complete(&response) {
        return Ok(response);
    }
    let mut poll = request.clone();
    if let Some(refresh) = poll.get_mut("refresh") {
        *refresh = json!(false);
    }
    let mut waited = Duration::ZERO;
    loop {
        if waited >= timeout {
            return Err(format!(
                "the --refresh scan did not finish within {} seconds; the editor may still be scanning",
                timeout.as_secs()
            ));
        }
        sleep(REFRESH_POLL_INTERVAL);
        waited += REFRESH_POLL_INTERVAL;
        response = send(&poll)?;
        if refresh_complete(&response) {
            return Ok(response);
        }
    }
}

/// Whether a reply ends the `--refresh` wait: an error reply, or an ok reply
/// whose `scanning` flag is false.
fn refresh_complete(response: &Response) -> bool {
    match response {
        Response::Error { .. } => true,
        Response::Ok { data } => data.get("scanning") == Some(&Value::Bool(false)),
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

/// Validates a `res://` resource path against the canonical project path. A
/// path that does not start with `res://` is returned unchanged for the plugin
/// to reject with its own message. A `..` segment or a `uid://` is likewise
/// left for the plugin, which rejects them with its own message; the CLI's job
/// here is the symlink resolution the plugin cannot do: a symlinked file or a
/// symlinked directory that resolves outside the project is rejected up front,
/// because the plugin would either fail to load it or, for a directory
/// symlink, misreport it. The literal file is resolved through the deepest
/// existing ancestor, so a missing file (whose ancestor is inside the project)
/// still reaches the plugin and gets its `resource not found` reply, while a
/// symlink that escapes the project at any level is rejected here. Returns the
/// original path unchanged when it is inside the project.
fn resolve_resource_path(
    canonical_project_path: &str,
    resource_path: &str,
) -> Result<String, String> {
    let Some(relative) = resource_path.strip_prefix("res://") else {
        return Ok(resource_path.to_string());
    };
    let project_root = std::path::Path::new(canonical_project_path);
    let mut candidate = project_root.join(relative);
    let resolved = loop {
        match std::fs::canonicalize(&candidate) {
            Ok(resolved) => break resolved,
            Err(_) => {
                let Some(parent) = candidate.parent() else {
                    return Err(format!(
                        "resource path resolves outside the project: {resource_path}"
                    ));
                };
                candidate = parent.to_path_buf();
            }
        }
    };
    // `Path::starts_with` compares whole path components, so a symlink
    // resolved to a sibling such as `/a/b2` is not inside `/a/b`, and a
    // non-ASCII component never crosses a char boundary the way a byte slice
    // would.
    if !resolved.starts_with(project_root) {
        return Err(format!(
            "resource path resolves outside the project: {resource_path}"
        ));
    }
    Ok(resource_path.to_string())
}

/// Interprets a value argument as JSON when it parses (so `true`, `3`, `1.5`,
/// `[1, 2]`, and `"42"` keep their JSON types) and as a plain string
/// otherwise (so `Hello` or `#ff8800` need no shell-level quoting). The
/// plugin, not the CLI, coerces the result to the property's declared type.
fn parse_value_argument(raw: &str) -> Value {
    serde_json::from_str(raw).unwrap_or_else(|_| Value::String(raw.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::thread;
    use std::time::Duration;

    /// The interface the plugin publishes, copied from its own reply shape.
    const INTERFACE: &str = include_str!("../tests/schema_fixture.json");

    /// The request every valid invocation must reach the plugin, recorded
    /// from the binary that hard-coded the commands. Each row is the
    /// invocation's argv and the request the plugin saw, compared as parsed
    /// JSON because key order is not part of the contract.
    const MATRIX: &[(&str, &[&str], &str)] = &[
        ("m01_status", &["call", "status"], r#"{"command":"status"}"#),
        (
            "m02_scene_tree",
            &["call", "scene-tree"],
            r#"{"command":"scene_tree"}"#,
        ),
        (
            "m03_rename_node",
            &[
                "call",
                "rename-node",
                "Player",
                "Hero",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"rename_node","node_path":"Player","new_name":"Hero","project_path":"/private/tmp"}"#,
        ),
        (
            "m04_create_node",
            &[
                "call",
                "create-node",
                ".",
                "Node2D",
                "Player",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"create_node","parent_path":".","class_name":"Node2D","name":"Player","project_path":"/private/tmp"}"#,
        ),
        (
            "m05_set_property_array",
            &[
                "call",
                "set-property",
                "Player",
                "position",
                "[120, 80]",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_property","node_path":"Player","property":"position","value":[120,80],"project_path":"/private/tmp"}"#,
        ),
        (
            "m06_set_property_string",
            &[
                "call",
                "set-property",
                "Player",
                "name",
                "Hello",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_property","node_path":"Player","property":"name","value":"Hello","project_path":"/private/tmp"}"#,
        ),
        (
            "m07_set_property_quoted_number",
            &[
                "call",
                "set-property",
                "Player",
                "name",
                "\"42\"",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_property","node_path":"Player","property":"name","value":"42","project_path":"/private/tmp"}"#,
        ),
        (
            "m08_set_property_int",
            &[
                "call",
                "set-property",
                "Player",
                "z_index",
                "3",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_property","node_path":"Player","property":"z_index","value":3,"project_path":"/private/tmp"}"#,
        ),
        (
            "m09_set_property_bool",
            &[
                "call",
                "set-property",
                "Player",
                "visible",
                "true",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_property","node_path":"Player","property":"visible","value":true,"project_path":"/private/tmp"}"#,
        ),
        (
            "m10_set_property_hash",
            &[
                "call",
                "set-property",
                "Player",
                "color",
                "#ff8800",
                "--project-path",
                "/tmp",
            ],
            r##"{"command":"set_property","node_path":"Player","property":"color","value":"#ff8800","project_path":"/private/tmp"}"##,
        ),
        (
            "m11_inspect_node",
            &[
                "call",
                "inspect-node",
                "--node-path",
                "Player",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"inspect_node","node_path":"Player","project_path":"/private/tmp"}"#,
        ),
        (
            "m12_query_nodes_default",
            &["call", "query-nodes", "--project-path", "/tmp"],
            r#"{"command":"query_nodes","project_path":"/private/tmp","class":null,"group":null,"name":null,"limit":100}"#,
        ),
        (
            "m13_query_nodes_filters",
            &[
                "call",
                "query-nodes",
                "--class",
                "Node2D",
                "--group",
                "enemies",
                "--name",
                "B*",
                "--limit",
                "50",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"query_nodes","project_path":"/private/tmp","class":"Node2D","group":"enemies","name":"B*","limit":50}"#,
        ),
        (
            "m15_inspect_class",
            &[
                "call",
                "inspect-class",
                "--class",
                "Node2D",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"inspect_class","class":"Node2D","project_path":"/private/tmp"}"#,
        ),
        (
            "m16_delete_node",
            &["call", "delete-node", "Player", "--project-path", "/tmp"],
            r#"{"command":"delete_node","node_path":"Player","project_path":"/private/tmp"}"#,
        ),
        (
            "m17_connect_signal",
            &[
                "call",
                "connect-signal",
                "Player",
                "pressed",
                "UI",
                "on_pressed",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"connect_signal","source_path":"Player","signal":"pressed","target_path":"UI","method":"on_pressed","deferred":false,"one_shot":false,"project_path":"/private/tmp"}"#,
        ),
        (
            "m18_connect_signal_flags",
            &[
                "call",
                "connect-signal",
                "Player",
                "pressed",
                "UI",
                "on_pressed",
                "--deferred",
                "--one-shot",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"connect_signal","source_path":"Player","signal":"pressed","target_path":"UI","method":"on_pressed","deferred":true,"one_shot":true,"project_path":"/private/tmp"}"#,
        ),
        (
            "m19_set_group_add",
            &[
                "call",
                "set-group",
                "Player",
                "enemies",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_group","node_path":"Player","group":"enemies","remove":false,"project_path":"/private/tmp"}"#,
        ),
        (
            "m20_set_group_remove",
            &[
                "call",
                "set-group",
                "Player",
                "enemies",
                "--remove",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_group","node_path":"Player","group":"enemies","remove":true,"project_path":"/private/tmp"}"#,
        ),
        (
            "m21_set_unique_name_add",
            &[
                "call",
                "set-unique-name",
                "Player",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_unique_name","node_path":"Player","remove":false,"project_path":"/private/tmp"}"#,
        ),
        (
            "m22_set_unique_name_remove",
            &[
                "call",
                "set-unique-name",
                "Player",
                "--remove",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"set_unique_name","node_path":"Player","remove":true,"project_path":"/private/tmp"}"#,
        ),
        (
            "m23_instantiate_scene",
            &[
                "call",
                "instantiate-scene",
                "--scene-path",
                "res://bullet.tscn",
                "--parent-path",
                ".",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"instantiate_scene","scene_path":"res://bullet.tscn","parent_path":".","project_path":"/private/tmp"}"#,
        ),
        (
            "m24_save_scene",
            &["call", "save-scene", "--project-path", "/tmp"],
            r#"{"command":"save_scene","project_path":"/private/tmp"}"#,
        ),
        (
            "m25_open_scene",
            &[
                "call",
                "open-scene",
                "--scene-path",
                "res://world.tscn",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"open_scene","project_path":"/private/tmp","scene_path":"res://world.tscn","save":false}"#,
        ),
        (
            "m26_open_scene_save",
            &[
                "call",
                "open-scene",
                "--scene-path",
                "res://world.tscn",
                "--save",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"open_scene","project_path":"/private/tmp","scene_path":"res://world.tscn","save":true}"#,
        ),
        (
            "m27_list_resources_default",
            &["call", "list-resources", "--project-path", "/tmp"],
            r#"{"command":"list_resources","project_path":"/private/tmp","path_prefix":null,"type":null,"limit":100,"cursor":null,"refresh":false}"#,
        ),
        (
            "m28_list_resources_filters",
            &[
                "call",
                "list-resources",
                "--path-prefix",
                "res://scenes/",
                "--type",
                "StandardMaterial3D",
                "--limit",
                "20",
                "--cursor",
                "res://scenes/a.tscn",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"list_resources","project_path":"/private/tmp","path_prefix":"res://scenes/","type":"StandardMaterial3D","limit":20,"cursor":"res://scenes/a.tscn","refresh":false}"#,
        ),
        (
            "m30_inspect_resource",
            &[
                "call",
                "inspect-resource",
                "--resource-path",
                "res://mat.tres",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"inspect_resource","project_path":"/private/tmp","resource_path":"res://mat.tres"}"#,
        ),
        (
            "m31_inspect_resource_uid",
            &[
                "call",
                "inspect-resource",
                "--resource-path",
                "uid://abc",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"inspect_resource","project_path":"/private/tmp","resource_path":"uid://abc"}"#,
        ),
        (
            "m32_inspect_resource_non_res",
            &[
                "call",
                "inspect-resource",
                "--resource-path",
                "/etc/hosts",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"inspect_resource","project_path":"/private/tmp","resource_path":"/etc/hosts"}"#,
        ),
        (
            "m33_inspect_resource_missing",
            &[
                "call",
                "inspect-resource",
                "--resource-path",
                "res://link.tres",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"inspect_resource","project_path":"/private/tmp","resource_path":"res://link.tres"}"#,
        ),
        (
            "m34_global_order_port_first",
            &["--port", "47999", "call", "status"],
            r#"{"command":"status"}"#,
        ),
        (
            "m35_status_ignores_project_path",
            &["--project-path", "/tmp", "call", "status"],
            r#"{"command":"status"}"#,
        ),
        (
            "m36_status_no_project_path",
            &["call", "status"],
            r#"{"command":"status"}"#,
        ),
        (
            "m37_list_resources_refresh",
            &[
                "call",
                "list-resources",
                "--refresh",
                "--project-path",
                "/tmp",
            ],
            r#"{"command":"list_resources","project_path":"/private/tmp","path_prefix":null,"type":null,"limit":100,"cursor":null,"refresh":true}"#,
        ),
    ];

    /// Invocations the interface types as integer: the value is refused
    /// before any request is sent.
    const REJECTED: &[&[&str]] = &[
        &[
            "call",
            "query-nodes",
            "--limit",
            "abc",
            "--project-path",
            "/tmp",
        ],
        &[
            "call",
            "list-resources",
            "--limit",
            "abc",
            "--project-path",
            "/tmp",
        ],
    ];

    fn interface_reply() -> Response {
        Response::Ok {
            data: serde_json::from_str(INTERFACE).expect("the interface fixture parses"),
        }
    }

    /// Accepts one connection, or gives up after `seconds`. A refusal can
    /// happen before the CLI connects at all, and a listener that waits
    /// forever would keep the test thread waiting with it.
    fn accept_within(listener: &TcpListener, seconds: u64) -> Option<std::net::TcpStream> {
        listener.set_nonblocking(true).expect("set nonblocking");
        let deadline = std::time::Instant::now() + Duration::from_secs(seconds);
        while std::time::Instant::now() < deadline {
            match listener.accept() {
                Ok((stream, _)) => {
                    stream.set_nonblocking(false).expect("blocking stream");
                    return Some(stream);
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(20));
                }
                Err(_) => return None,
            }
        }
        None
    }

    /// Serves one reply per connection on an ephemeral loopback port and
    /// records every request line it read, so a run can be checked against
    /// exactly what the plugin saw. The thread ends after the last reply, so
    /// the caller can join it.
    fn fake_plugin(replies: Vec<Response>) -> (u16, thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();
        let handle = thread::spawn(move || {
            let mut lines = Vec::new();
            for reply in replies {
                let Some(stream) = accept_within(&listener, 10) else {
                    break;
                };
                let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
                let mut line = String::new();
                reader.read_line(&mut line).expect("read request line");
                lines.push(line.trim_end().to_string());
                let mut writer = stream;
                let mut reply_line = serde_json::to_string(&reply).expect("serialize reply");
                reply_line.push('\n');
                writer
                    .write_all(reply_line.as_bytes())
                    .expect("write reply");
            }
            lines
        });
        (port, handle)
    }

    /// Serves one connection only, writing `reply` verbatim or nothing at all
    /// when it is `None`, which is how an editor that stops mid-reply looks.
    /// Records the request line it read, so the caller can check what the CLI
    /// sent before the failure.
    fn raw_plugin(reply: Option<&str>) -> (u16, thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();
        let reply = reply.map(str::to_string);
        let handle = thread::spawn(move || {
            let mut lines = Vec::new();
            let Some(stream) = accept_within(&listener, 10) else {
                return lines;
            };
            let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
            let mut line = String::new();
            reader.read_line(&mut line).expect("read request line");
            lines.push(line.trim_end().to_string());
            if let Some(reply) = reply {
                let mut writer = stream;
                // A reply over the client's bound is never read whole, so the
                // write is allowed to time out rather than block the test.
                writer
                    .set_write_timeout(Some(Duration::from_secs(2)))
                    .expect("set write timeout");
                let _ = writer.write_all(reply.as_bytes());
            }
            lines
        });
        (port, handle)
    }

    /// Runs one invocation against a fake plugin that answers only the
    /// interface fetch, and returns the refusal together with the request
    /// lines the plugin saw.
    fn refused(argv: &[&str]) -> (String, Vec<String>) {
        let (port, server) = fake_plugin(vec![interface_reply()]);
        let error = run(&with_port(port, argv)).expect_err("the invocation is refused");
        let lines = server.join().expect("server thread does not panic");
        (error, lines)
    }

    /// Asserts that a refusal sent nothing but the interface fetch.
    fn fetch_only(lines: &[String]) {
        assert_eq!(
            lines,
            vec![r#"{"command":"commands"}"#.to_string()],
            "only the interface fetch reached the plugin"
        );
    }

    /// The recorded argv with `--port` aimed at a loopback port. A recorded
    /// row may carry its own `--port`; the last one on the line wins, so the
    /// ephemeral port goes last in that case.
    fn with_port(port: u16, argv: &[&str]) -> Vec<String> {
        let mut args: Vec<String> = vec!["godot-cli".to_string()];
        args.extend(argv.iter().map(|argument| (*argument).to_string()));
        if args.iter().any(|argument| argument == "--port") {
            args.push("--port".to_string());
            args.push(port.to_string());
        } else {
            args.insert(1, "--port".to_string());
            args.insert(2, port.to_string());
        }
        args
    }

    /// The recorded request with this machine's canonical temp path put back
    /// where the capture had it.
    fn expected(request: &str) -> Value {
        let temp = canonicalize_project_path("/tmp").expect("/tmp exists");
        serde_json::from_str(&request.replace("/private/tmp", &temp))
            .expect("the recorded request parses")
    }

    /// A port on the loopback interface where nothing listens.
    fn closed_port() -> u16 {
        let listener = TcpListener::bind((HOST, 0)).expect("bind ephemeral port");
        let port = listener.local_addr().expect("local addr").port();
        drop(listener);
        port
    }

    #[test]
    fn every_recorded_invocation_sends_the_recorded_request() {
        for (id, argv, request) in MATRIX {
            let (port, server) = fake_plugin(vec![
                interface_reply(),
                Response::Ok {
                    data: json!({ "scanning": false }),
                },
            ]);
            run(&with_port(port, argv)).unwrap_or_else(|error| panic!("{id}: {error}"));
            let lines = server.join().expect("server thread does not panic");
            assert_eq!(lines.len(), 2, "{id}: one fetch and one request");
            let sent: Value = serde_json::from_str(&lines[1]).expect("parse request");
            assert_eq!(sent, expected(request), "{id}");
        }
    }

    #[test]
    fn a_non_integer_value_is_refused_before_any_request_is_sent() {
        for argv in REJECTED {
            let (port, server) = fake_plugin(vec![interface_reply()]);
            let error = run(&with_port(port, argv)).expect_err("the value is refused");
            assert!(error.contains("--limit"), "{error}");
            assert!(error.contains("integer"), "{error}");
            let lines = server.join().expect("server thread does not panic");
            assert_eq!(
                lines,
                vec![r#"{"command":"commands"}"#.to_string()],
                "only the fetch reached the plugin"
            );
        }
    }

    #[test]
    fn integer_arguments_take_json_integer_literals_only() {
        assert_eq!(parse_integer("0"), Some(0));
        assert_eq!(parse_integer("-3"), Some(-3));
        assert_eq!(parse_integer("100"), Some(100));
        for raw in [
            "",
            "abc",
            "+5",
            "1e2",
            "1.5",
            "007",
            "-",
            "99999999999999999999",
        ] {
            assert_eq!(parse_integer(raw), None, "{raw} must be refused");
        }
    }

    #[test]
    fn an_editor_without_the_interface_is_named_as_too_old() {
        let (port, server) = fake_plugin(vec![Response::Error {
            message: UNKNOWN_COMMANDS_REPLY.to_string(),
        }]);
        let error = run(&with_port(port, &["call", "status"])).expect_err("the old plugin fails");
        assert_eq!(
            error,
            "the editor plugin is too old for this godot-cli; update the plugin"
        );
        server.join().expect("server thread does not panic");
    }

    #[test]
    fn an_unknown_protocol_version_is_refused() {
        let (port, server) = fake_plugin(vec![Response::Ok {
            data: json!({ "protocol_version": 2, "commands": [] }),
        }]);
        let error = run(&with_port(port, &["call", "status"])).expect_err("the version is refused");
        assert_eq!(
            error,
            "unsupported protocol version: 2 (this godot-cli knows 1)"
        );
        server.join().expect("server thread does not panic");
    }

    #[test]
    fn help_and_version_need_no_editor_and_a_call_does() {
        let port = closed_port();
        run(&with_port(port, &["--version"])).expect("--version needs no editor");
        run(&with_port(port, &["--help"])).expect("--help needs no editor");
        assert!(
            run(&with_port(port, &["call", "status", "--help"])).is_err(),
            "a command's own help needs the editor that describes it"
        );
        assert!(
            run(&with_port(port, &["commands"])).is_err(),
            "the command list needs an editor"
        );
        assert!(
            run(&with_port(port, &["call", "status"])).is_err(),
            "a call needs an editor"
        );
    }

    #[test]
    fn a_command_help_comes_from_the_interface() {
        let (port, server) = fake_plugin(vec![interface_reply()]);
        run(&with_port(port, &["call", "status", "--help"])).expect("the interface describes it");
        server.join().expect("server thread does not panic");
    }

    #[test]
    fn a_refresh_call_polls_until_the_scan_has_finished() {
        let (port, server) = fake_plugin(vec![
            interface_reply(),
            Response::Ok {
                data: json!({ "scanning": true }),
            },
            Response::Ok {
                data: json!({ "scanning": false }),
            },
        ]);
        run(&with_port(
            port,
            &[
                "call",
                "list-resources",
                "--refresh",
                "--project-path",
                "/tmp",
            ],
        ))
        .expect("the poll finishes");
        let lines = server.join().expect("server thread does not panic");
        assert_eq!(lines.len(), 3, "one fetch and two polls");
        let first: Value = serde_json::from_str(&lines[1]).expect("parse request");
        let second: Value = serde_json::from_str(&lines[2]).expect("parse request");
        assert_eq!(first["refresh"], json!(true));
        assert_eq!(second["refresh"], json!(false));
    }

    #[test]
    fn a_resource_path_that_resolves_outside_the_project_is_refused() {
        let base = std::env::temp_dir();
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock before epoch")
            .as_nanos();
        let dir = base.join(format!("cli_res_path_{unique}"));
        std::fs::create_dir_all(&dir).expect("create temp dir");
        // The outside target is a sibling of the project, not inside it, so
        // the symlink genuinely escapes.
        let outside = base.join(format!("cli_res_path_outside_{unique}"));
        std::fs::create_dir_all(&outside).expect("create outside dir");
        std::fs::write(outside.join("a.tres"), "not a real resource").expect("write outside file");
        std::os::unix::fs::symlink(&outside.join("a.tres"), dir.join("linked.tres"))
            .expect("create symlink");
        let (port, server) = fake_plugin(vec![interface_reply()]);

        let args = with_port(
            port,
            &[
                "call",
                "inspect-resource",
                "--resource-path",
                "res://linked.tres",
                "--project-path",
                dir.to_str().expect("temp dir is UTF-8"),
            ],
        );
        let error = run(&args).expect_err("the escaping symlink must be refused");
        assert!(
            error.contains("resource path resolves outside the project"),
            "{error}"
        );
        let lines = server.join().expect("server thread does not panic");
        assert_eq!(
            lines,
            vec![r#"{"command":"commands"}"#.to_string()],
            "only the fetch reached the plugin"
        );
    }

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

    #[test]
    fn the_refresh_wait_gives_up_after_its_timeout() {
        let send = |_: &Value| {
            Ok(Response::Ok {
                data: json!({ "scanning": true }),
            })
        };
        let sleep = |_: Duration| {};
        let error = poll_refresh(
            &json!({ "command": "list_resources", "refresh": true }),
            send,
            sleep,
            REFRESH_TIMEOUT,
        )
        .expect_err("the wait ends");
        assert!(error.contains("did not finish"), "{error}");
    }

    #[test]
    fn a_port_that_is_not_one_is_refused_before_anything_is_sent() {
        let ports: Vec<(&[&str], &str)> = vec![
            (&["--port", "x"], "invalid port: x"),
            (&["--port", "70000"], "invalid port: 70000"),
            (&["--port"], "--port requires a value"),
            (&["--port", "-1"], "invalid port: -1"),
        ];
        for (argv, expected) in ports {
            let mut args = vec!["godot-cli".to_string()];
            args.extend(argv.iter().map(|argument| (*argument).to_string()));
            let error = run(&args).expect_err("the port is refused");
            assert_eq!(error, expected);
        }
    }

    #[test]
    fn a_call_refuses_what_the_interface_does_not_declare() {
        let refusals: Vec<(&[&str], &str)> = vec![
            (
                &["call", "rename-node", "Player"],
                "missing positional argument 2 (new_name) for rename-node",
            ),
            (
                &["call", "rename-node", "Player", "Hero"],
                "missing required argument for rename-node: --project-path",
            ),
            (
                &[
                    "call",
                    "rename-node",
                    "Player",
                    "Hero",
                    "--nope",
                    "x",
                    "--project-path",
                    "/tmp",
                ],
                "unknown argument for rename-node: --nope",
            ),
            (
                &[
                    "call",
                    "rename-node",
                    "Player",
                    "Hero",
                    "Extra",
                    "--project-path",
                    "/tmp",
                ],
                "too many arguments for rename-node: expected 2",
            ),
            (
                &["call", "inspect-node", "--node-path"],
                "--node-path requires a value",
            ),
            (&["call", "nope"], "unknown command: nope"),
            (&["call"], "missing command name after 'call'"),
        ];
        for (argv, expected) in refusals {
            let (error, lines) = refused(argv);
            assert_eq!(error, expected, "for {argv:?}");
            fetch_only(&lines);
        }
        // The global --project-path is read before anything is fetched, so
        // its missing value is refused with nothing sent at all.
        let (error, lines) = refused(&["call", "rename-node", "Player", "Hero", "--project-path"]);
        assert_eq!(error, "--project-path requires a value");
        assert!(lines.is_empty(), "nothing reached the plugin");
    }

    #[test]
    fn a_project_path_that_does_not_exist_is_refused() {
        let (error, lines) = refused(&["call", "save-scene", "--project-path", "/no/such/project"]);
        assert!(
            error.starts_with("invalid --project-path /no/such/project:"),
            "{error}"
        );
        fetch_only(&lines);
    }

    #[test]
    fn a_refresh_wait_stops_on_an_error_reply() {
        let (port, server) = fake_plugin(vec![
            interface_reply(),
            Response::Error {
                message: "no scene is currently being edited".to_string(),
            },
        ]);
        let error = run(&with_port(
            port,
            &[
                "call",
                "list-resources",
                "--refresh",
                "--project-path",
                "/tmp",
            ],
        ))
        .expect_err("an error reply ends the wait");
        assert!(
            error.contains("plugin reported an error: no scene is currently being edited"),
            "{error}"
        );
        let lines = server.join().expect("server thread does not panic");
        assert_eq!(
            lines.len(),
            2,
            "the error reply ends the wait, with no poll after it"
        );
    }

    /// A symlink whose target crosses the project name at a multi-byte
    /// character is refused with a normal error, not a panic: the containment
    /// check compares whole path components, so it never slices a UTF-8 string
    /// by byte index.
    #[test]
    fn a_resource_path_with_a_non_ascii_symlink_is_refused_without_panicking() {
        let base = std::env::temp_dir();
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock before epoch")
            .as_nanos();
        let project_name = format!("cli_res_path_unicode_{unique}");
        let dir = base.join(&project_name);
        std::fs::create_dir_all(&dir).expect("create temp dir");
        let outside = base.join(format!(
            "{}é-onwards",
            &project_name[..project_name.len() - 1]
        ));
        std::fs::create_dir_all(&outside).expect("create outside dir");
        std::fs::write(outside.join("a.tres"), "not a real resource").expect("write outside file");
        std::os::unix::fs::symlink(&outside.join("a.tres"), dir.join("escape.tres"))
            .expect("create symlink");
        let (port, server) = fake_plugin(vec![interface_reply()]);

        let error = run(&with_port(
            port,
            &[
                "call",
                "inspect-resource",
                "--resource-path",
                "res://escape.tres",
                "--project-path",
                dir.to_str().expect("temp dir is UTF-8"),
            ],
        ))
        .expect_err("the escaping symlink must be refused");
        assert!(
            error.contains("resource path resolves outside the project"),
            "{error}"
        );
        fetch_only(&server.join().expect("server thread does not panic"));
    }

    #[test]
    fn a_transport_failure_names_what_went_wrong() {
        // Nothing listens: the connect branch names the host, the port and the
        // plugin.
        let error = run(&with_port(closed_port(), &["call", "status"])).expect_err("no editor");
        assert!(
            error.starts_with("could not connect to the Godot Pipeline plugin on 127.0.0.1:"),
            "{error}"
        );
        assert!(error.contains("godot_pipeline"), "{error}");

        // The editor closes without a reply.
        let (port, server) = raw_plugin(None);
        let error = run(&with_port(port, &["call", "status"])).expect_err("no reply");
        assert!(
            error.starts_with("unexpected response from the plugin:"),
            "{error}"
        );
        assert!(
            error.contains("connection closed before a response was received"),
            "{error}"
        );
        fetch_only(&server.join().expect("server thread does not panic"));

        // The reply is not the wire format.
        let (port, server) = raw_plugin(Some("not json\n"));
        let error = run(&with_port(port, &["call", "status"])).expect_err("no usable reply");
        assert!(
            error.starts_with("unexpected response from the plugin:"),
            "{error}"
        );
        fetch_only(&server.join().expect("server thread does not panic"));
    }

    #[test]
    fn an_interface_reply_over_the_bound_is_refused_and_a_smaller_one_is_not() {
        // Valid JSON one byte over the bound, so only the bound can refuse it.
        let padding = "x".repeat(MAX_INTERFACE_BYTES as usize);
        let data = json!({ "protocol_version": 1, "commands": [], "padding": padding });
        let mut reply = serde_json::to_string(&Response::Ok { data }).expect("serialize reply");
        reply.push('\n');
        assert!(reply.len() as u64 > MAX_INTERFACE_BYTES);
        let (port, server) = raw_plugin(Some(&reply));
        let error = run(&with_port(port, &["call", "status"])).expect_err("the bound refuses it");
        assert!(
            error.contains(&format!("larger than {MAX_INTERFACE_BYTES} bytes")),
            "{error}"
        );
        fetch_only(&server.join().expect("server thread does not panic"));

        // The real interface is well under the bound and is read.
        let (port, server) = fake_plugin(vec![
            interface_reply(),
            Response::Ok {
                data: json!({ "scanning": false }),
            },
        ]);
        run(&with_port(port, &["call", "status"])).expect("a small interface is read");
        assert_eq!(
            server.join().expect("server thread does not panic").len(),
            2
        );
    }

    #[test]
    fn an_io_failure_is_named_as_a_network_error() {
        let error = describe_client_error(
            &ClientError::Io(std::io::Error::other("connection reset by peer")),
            47821,
        );
        assert_eq!(
            error,
            "network error talking to the plugin: connection reset by peer"
        );
    }

    #[test]
    fn an_interface_reply_without_a_trailing_newline_is_named_as_such() {
        // The real plugin always ends a reply with its newline, so a missing
        // one is the editor's own doing and the message must not blame the
        // size of a reply that is nowhere near the bound.
        let data = serde_json::from_str(INTERFACE).expect("the interface fixture parses");
        let reply = serde_json::to_string(&Response::Ok { data }).expect("serialize reply");
        let (port, server) = raw_plugin(Some(&reply));
        let error = run(&with_port(port, &["call", "status"])).expect_err("the reply is refused");
        assert!(
            error.contains("the reply did not end with a newline"),
            "{error}"
        );
        assert!(!error.contains("larger than"), "{error}");
        fetch_only(&server.join().expect("server thread does not panic"));
    }

    #[test]
    fn a_command_reply_larger_than_the_interface_bound_is_still_read() {
        // A node with a very large array answers with megabytes; only the
        // interface fetch is bounded, so this must not be refused.
        let payload = "x".repeat(9 * 1024 * 1024);
        let (port, server) = fake_plugin(vec![
            interface_reply(),
            Response::Ok {
                data: json!({ "blob": payload }),
            },
        ]);
        run(&with_port(port, &["call", "status"])).expect("a large command reply is read");
        assert_eq!(
            server.join().expect("server thread does not panic").len(),
            2
        );
    }

    #[test]
    fn an_argument_kind_this_build_cannot_encode_is_refused_even_when_defaulted() {
        let mut data: Value =
            serde_json::from_str(INTERFACE).expect("the interface fixture parses");
        data["commands"][0]["arguments"] = json!([{
            "name": "future",
            "flag": null,
            "positional": null,
            "type": "unsupported",
            "required": false,
            "default": null,
            "help": "named by a newer plugin"
        }]);
        let (port, server) = fake_plugin(vec![Response::Ok { data }]);
        let error = run(&with_port(port, &["call", "status"])).expect_err("the kind is refused");
        assert_eq!(
            error,
            "unknown argument type from the plugin for future: unsupported"
        );
        fetch_only(&server.join().expect("server thread does not panic"));
    }

    #[test]
    fn the_command_list_refuses_an_unsupported_version_in_both_output_modes() {
        let modes: Vec<&[&str]> = vec![&["commands"], &["commands", "--json"]];
        for argv in modes {
            let (port, server) = fake_plugin(vec![Response::Ok {
                data: json!({ "protocol_version": 2, "commands": [] }),
            }]);
            let error = run(&with_port(port, &argv)).expect_err("the version is refused");
            assert_eq!(
                error, "unsupported protocol version: 2 (this godot-cli knows 1)",
                "for {argv:?}"
            );
            assert_eq!(
                server.join().expect("server thread does not panic").len(),
                1
            );
        }
    }
}
