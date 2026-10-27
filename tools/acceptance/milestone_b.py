#!/usr/bin/env python3
"""End-to-end acceptance run for Phase 4 Milestone B (run and check).

Builds a fixture project, drives the merged play, game-log, capture-game
and stop commands through the real godot-cli binary: plays a scene whose
script prints a heartbeat line then fails with a deliberate runtime error,
retrieves the error from game-log while the game sits stopped in the
debugger, fixes the script with an ordinary file write, plays again, checks
the new run is clean and shows the fixed behaviour, captures the game
viewport and checks known pixels, then stops and proves nothing lingers.

Every step asserts from replies and from reads of the live editor and game
(game-log, status, decoded PNG pixels), never from a command's
acknowledgement alone. The script prints seven PASS lines, one per step, when every
step passes, and exits non-zero on the first failed assertion.

  python3 milestone_b.py --godot <editor> --cli <godot-cli> --port N \
      --workdir <new dir> --out <transcript.json>

--workdir must not exist; the script creates it and never deletes anything.
"""
import argparse
import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib

# Test-only hooks, read from the environment so the command line keeps only
# its five flags. Each value makes exactly one step fail; the empty default
# is the real acceptance run.
CONTROL = os.environ.get("MILESTONE_B_CONTROL", "")

HEARTBEAT = "MILESTONE_B_HEARTBEAT_1"
FIXED_LINE = "MILESTONE_B_FIXED_2"
ERROR_TEXT = "Nonexistent function 'do_something' in base 'Nil'."
SCRIPT_PATH = "res://player.gd"
ERROR_LINE = 7
ERROR_FUNC = "_ready"

PROJECT_GODOT = """; Engine configuration file.
config_version=5

[application]

config/name="godot-cli-acceptance-b"
run/main_scene="res://main.tscn"

[display]

window/size/viewport_width=1152
window/size/viewport_height=648

[rendering]

environment/defaults/default_clear_color=Color(0, 0, 1, 1)

[editor_plugins]

enabled=PackedStringArray("res://addons/godot_pipeline/plugin.cfg")
"""

# The defect is a runtime error, not a syntax error: the script parses and
# loads, then fails at the marked line when _ready runs on a null value. The
# heartbeat print is followed by one awaited frame so the helper forwards it
# before the error stops the game in the debugger.
BROKEN_GD = """extends Control

func _ready() -> void:
\tprint("MILESTONE_B_HEARTBEAT_1")
\tawait get_tree().process_frame
\tvar x = null
\tx.do_something()
"""

FIXED_GD = """extends Control

func _ready() -> void:
\tprint("MILESTONE_B_HEARTBEAT_1")
\tprint("MILESTONE_B_FIXED_2")
"""

# A red block at a known position on a blue background, plus a label, so a
# capture can be checked against two known pixels.
MAIN_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://player.gd" id="1"]

[node name="Main" type="Control"]
layout_mode = 3
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
grow_horizontal = 2
grow_vertical = 2
script = ExtResource("1")

[node name="Box" type="ColorRect" parent="."]
layout_mode = 1
offset_left = 100.0
offset_top = 80.0
offset_right = 300.0
offset_bottom = 230.0
color = COLOR_LINE

[node name="Tag" type="Label" parent="."]
layout_mode = 1
offset_left = 100.0
offset_top = 240.0
offset_right = 300.0
offset_bottom = 270.0
text = "B-TAG"
"""

BOX_INSIDE = (200, 150)
BACKGROUND_AT = (600, 400)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class Failed(Exception):
    """A step assertion that did not hold."""


# The step being run, so a failure names where it happened.
CURRENT = ["startup"]


def die(message):
    raise Failed("%s: %s" % (CURRENT[0], message))


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(raw):
    """Decode a PNG into (width, height, pixel function) with stdlib only.

    The rows carry a filter byte that must be reversed; reading the filtered
    bytes as pixels returns black for solid colours.
    """
    pos = 8
    idat = b""
    width = height = None
    colour = None
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, bit, colour, _, _, _ = struct.unpack(">IIBBBBB", data)
            if bit != 8 or colour != 2:
                die("the capture is not 8-bit RGB: bit %s colour %s" % (bit, colour))
        elif kind == b"IDAT":
            idat += data
        pos += 12 + length
        if kind == b"IEND":
            break
    if width is None:
        die("the capture has no IHDR")
    channels = 3
    stride = width * channels
    flat = zlib.decompress(idat)
    if len(flat) != (stride + 1) * height:
        die("the capture decompresses to %d bytes, expected %d"
            % (len(flat), (stride + 1) * height))
    pixels = bytearray(width * height * channels)
    for y in range(height):
        filt = flat[y * (stride + 1)]
        row = flat[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        prev = pixels[(y - 1) * stride:y * stride] if y > 0 else bytearray(stride)
        cur = bytearray(stride)
        if filt == 0:
            cur[:] = row
        elif filt == 1:
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                cur[i] = (row[i] + a) & 0xFF
        elif filt == 2:
            for i in range(stride):
                cur[i] = (row[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                cur[i] = (row[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                cur[i] = (row[i] + paeth(a, b, c)) & 0xFF
        else:
            die("the capture uses filter %d" % filt)
        pixels[y * stride:(y + 1) * stride] = cur

    def pixel(x, y):
        off = y * stride + x * channels
        return tuple(pixels[off:off + channels])

    return width, height, pixel


class Run:
    def __init__(self, godot, cli, port, repo, workdir):
        self.godot = godot
        self.cli_bin = cli
        self.port = port
        self.repo = repo
        self.proj = os.path.join(workdir, "proj")
        self.proc = None
        self.transcript = []

    # ---- the editor, launched the way tools/replay/replay.py launches it ----
    def launch(self, scene=None):
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
            die("port %d is already in use" % self.port)
        except OSError:
            pass
        args = [self.godot, "--headless", "--path", self.proj, "--editor"]
        if scene:
            args.append(scene)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.wait_listener()
        if scene:
            end = time.time() + 90
            while time.time() < end:
                if self.status().get("scene_path") is not None:
                    break
                time.sleep(1)
            else:
                die("the edited scene never opened")
        self.wait_filesystem_ready()

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)
        self.proc = None

    def wait_listener(self, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
                return
            except OSError:
                time.sleep(0.5)
        die("the plugin listener on port %d never came up" % self.port)

    def send(self, payload, timeout=30):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            s.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            s.settimeout(timeout)
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
            return data.decode().rstrip("\n")
        finally:
            s.close()

    def wait_filesystem_ready(self, timeout=120):
        end = time.time() + timeout
        while True:
            reply = json.loads(self.send({"command": "list_resources",
                                          "project_path": self.proj, "limit": 1}, 25))
            if reply.get("status") == "ok" and reply["data"].get("scanning") is False:
                return
            if time.time() >= end:
                die("the editor file system never finished its scan")
            time.sleep(0.5)

    # ---- everything the scenario does goes through the real CLI binary ----
    def cli(self, args, timeout=120):
        # --port always comes first: without it the CLI tries its default port,
        # which is the port of a developer's live editor.
        proc = subprocess.run([self.cli_bin, "--port", str(self.port)] + args,
                              capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "").strip()
        try:
            parsed = json.loads(out) if out else None
        except json.JSONDecodeError:
            parsed = None
        return {"argv": args, "exit": proc.returncode, "stdout": out,
                "stderr": (proc.stderr or "").strip(), "json": parsed}

    def status(self):
        reply = self.cli(["call", "status", "--project-path", self.proj])
        if reply["exit"] != 0 or not isinstance(reply["json"], dict):
            die("status was refused: %s" % (reply["stderr"] or reply["stdout"][:200]))
        return reply["json"]

    def game_log(self):
        reply = self.cli(["call", "game-log", "--project-path", self.proj])
        if reply["exit"] != 0 or not isinstance(reply["json"], dict):
            die("game-log was refused: %s" % (reply["stderr"] or reply["stdout"][:200]))
        return reply["json"]

    def game_processes(self):
        """Pids of game processes started for this workdir, if any."""
        try:
            out = subprocess.run(["pgrep", "-f", self.proj],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
        pids = []
        for line in out.splitlines():
            try:
                pid = int(line.strip().split()[0])
            except ValueError:
                continue
            try:
                cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                     capture_output=True, text=True,
                                     timeout=10).stdout
            except Exception:
                continue
            if "--scene" in cmd and self.proj in cmd:
                pids.append(pid)
        return pids


def build_fixture(run):
    proj = run.proj
    if os.path.isdir(proj):
        die("the project directory already exists: %s" % proj)
    os.makedirs(os.path.join(proj, "addons"))
    with open(os.path.join(proj, "project.godot"), "w") as h:
        h.write(PROJECT_GODOT)
    shutil.copytree(os.path.join(run.repo, "addon", "godot_pipeline"),
                    os.path.join(proj, "addons", "godot_pipeline"),
                    ignore=shutil.ignore_patterns("*.uid"))
    plugin = os.path.join(proj, "addons", "godot_pipeline", "editor_plugin.gd")
    with open(plugin) as h:
        text = h.read()
    text = text.replace("const PORT := 47821", "const PORT := %d" % run.port)
    text = text.replace("const PORT = 47821", "const PORT = %d" % run.port)
    with open(plugin, "w") as h:
        h.write(text)
    if "47821" in text:
        die("the fixture plugin still names the live-editor port")
    first = BROKEN_GD if CONTROL != "no_error" else FIXED_GD
    with open(os.path.join(proj, "player.gd"), "w") as h:
        h.write(first)
    colour = "Color(0, 0, 1, 1)" if CONTROL == "wrong_colour" \
        else "Color(1, 0, 0, 1)"
    with open(os.path.join(proj, "main.tscn"), "w") as h:
        h.write(MAIN_TSCN.replace("COLOR_LINE", colour))
    # One import pass gives the scripts and the scene resource ids, so the
    # editor's first launch does not race its own startup scan.
    subprocess.run([run.godot, "--headless", "--path", proj, "--import"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)


def entries_by_kind(log, kind):
    return [e for e in log.get("entries", []) if e.get("kind") == kind]


def run_scenario(run):
    t = run.transcript

    # ---- S1 fixture and baseline: the edited scene, idle ----
    CURRENT[0] = "S1"
    got = run.status()
    if got.get("playing") is not False:
        die("status.playing is %r, expected false before play" % got.get("playing"))
    if got.get("scene_path") != "res://main.tscn":
        die("status.scene_path is %r, expected res://main.tscn" % got.get("scene_path"))
    t.append({"step": "S1", "kind": "baseline",
              "detail": {"playing": False, "scene_path": "res://main.tscn"}})

    # ---- S2 play: the game starts, then stops in the debugger ----
    CURRENT[0] = "S2"
    # Prime the capture autoload: the first play persists its project.godot
    # line, and a game started before that save reads no helper and forwards
    # nothing. One throwaway play and stop makes the measured play deliver.
    r = run.cli(["call", "play", "--project-path", run.proj])
    if r["exit"] != 0:
        die("the priming play was refused: %s" % r["stderr"])
    end = time.time() + 15
    while time.time() < end:
        log = run.game_log()
        if log.get("entries") or log.get("game_state") == "stopped_in_debugger":
            break
        time.sleep(0.5)
    r = run.cli(["call", "stop", "--project-path", run.proj])
    if r["exit"] != 0:
        die("the priming stop was refused: %s" % r["stderr"])
    # The registration reaches project.godot with a queued project-settings
    # save, so the line may land after the reply; wait for it.
    end = time.time() + 20
    while time.time() < end:
        with open(os.path.join(run.proj, "project.godot")) as h:
            if "GodotPipelineCapture" in h.read():
                break
        time.sleep(0.5)
    else:
        die("the priming play did not register the capture autoload")
    t.append({"step": "S2", "kind": "prime",
              "detail": {"autoload": "GodotPipelineCapture"}})
    r = run.cli(["call", "play", "--project-path", run.proj])
    if r["exit"] != 0:
        die("play was refused: %s" % r["stderr"])
    run_id_1 = None
    log_1 = None
    end = time.time() + 30
    while time.time() < end:
        status = run.status()
        log = run.game_log()
        hearts = [e for e in log.get("entries", [])
                  if e.get("kind") == "message" and HEARTBEAT in (e.get("text") or "")]
        errors = entries_by_kind(log, "error")
        # The stopping error is sent synchronously while the heartbeat print
        # still travels the helper queue, so the first poll that reports the
        # stop can hold the error without the heartbeat yet.
        if log.get("game_state") == "stopped_in_debugger" and hearts and errors:
            if status.get("playing") is not True:
                die("status.playing is %r while stopped in the debugger"
                    % status.get("playing"))
            run_id_1 = log.get("run_id")
            log_1 = log
            break
        time.sleep(0.5)
    else:
        die("game-log never reached stopped_in_debugger: %s"
            % json.dumps(log)[:400])
    hearts = [e for e in log_1["entries"]
              if e.get("kind") == "message" and HEARTBEAT in (e.get("text") or "")]
    if not hearts:
        die("the heartbeat print is missing from game-log")
    errors = entries_by_kind(log_1, "error")
    if len(errors) != 1:
        die("game-log holds %d error entries, expected exactly one" % len(errors))
    err = errors[0]
    if ERROR_TEXT not in (err.get("code") or ""):
        die("the error entry carries %r, expected %r"
            % (err.get("code"), ERROR_TEXT))
    for key, want in (("file", SCRIPT_PATH), ("line", ERROR_LINE),
                      ("function", ERROR_FUNC)):
        if err.get(key) != want:
            die("the error entry %s is %r, expected %r" % (key, err.get(key), want))
    for where in ("script_location", "engine_location"):
        loc = err.get(where) or {}
        if (loc.get("file"), loc.get("line"), loc.get("function")) != \
                (SCRIPT_PATH, ERROR_LINE, ERROR_FUNC):
            die("the %s is %r, expected the script line %d" % (where, loc, ERROR_LINE))
    traces = " ".join(err.get("backtraces") or [])
    if SCRIPT_PATH + ":" + str(ERROR_LINE) not in traces or ERROR_FUNC not in traces:
        die("the backtrace names %r, expected the script line" % traces[:200])
    t.append({"step": "S2", "kind": "runtime_error",
              "detail": {"game_state": "stopped_in_debugger",
                         "message": ERROR_TEXT, "file": SCRIPT_PATH,
                         "line": ERROR_LINE, "function": ERROR_FUNC}})

    # ---- S3 fix: overwrite the script with ordinary file tools ----
    CURRENT[0] = "S3"
    if CONTROL != "unfixed":
        with open(os.path.join(run.proj, "player.gd"), "w") as h:
            h.write(FIXED_GD)
    with open(os.path.join(run.proj, "project.godot")) as h:
        if "GodotPipelineCapture" not in h.read():
            die("the first play did not register the capture autoload")
    t.append({"step": "S3", "kind": "fix",
              "detail": {"method": "file_overwrite", "path": "res://player.gd"}})

    # ---- S4 rerun: the stopped game restarts, clean ----
    CURRENT[0] = "S4"
    r = run.cli(["call", "play", "--project-path", run.proj])
    if r["exit"] != 0:
        die("the rerun play was refused: %s" % r["stderr"])
    run_id_2 = None
    log_2 = None
    end = time.time() + 30
    while time.time() < end:
        log = run.game_log()
        if log.get("run_id") != run_id_1 and log.get("entries"):
            texts = " ".join(e.get("text") or "" for e in log["entries"])
            if FIXED_LINE in texts:
                run_id_2 = log.get("run_id")
                log_2 = log
                break
            # A rerun that still defects stops in the debugger instead of
            # printing the fixed line; take it so the assertions fail fast.
            if log.get("game_state") == "stopped_in_debugger" or \
                    entries_by_kind(log, "error"):
                run_id_2 = log.get("run_id")
                log_2 = log
                break
        time.sleep(0.5)
    else:
        die("no new game-log run started after the fix")
    if log_2.get("game_state") != "running":
        die("the new run game_state is %r, expected running" % log_2.get("game_state"))
    if entries_by_kind(log_2, "error"):
        die("the new run still carries an error entry")
    texts = " ".join(e.get("text") or "" for e in log_2["entries"])
    if HEARTBEAT not in texts or FIXED_LINE not in texts:
        die("the new run misses the fixed behaviour lines: %r" % texts[:200])
    t.append({"step": "S4", "kind": "rerun",
              "detail": {"new_run": True, "game_state": "running",
                         "fixed_line": FIXED_LINE}})

    # ---- S5 capture: a decoded PNG with known pixels ----
    CURRENT[0] = "S5"
    r = run.cli(["call", "capture-game", "--project-path", run.proj,
                 "--timeout-ms", "10000"], timeout=30)
    if r["exit"] != 0:
        die("capture-game was refused: %s" % r["stderr"])
    reply = r["json"] or {}
    if CONTROL == "bad_png":
        raw = b"BAD!" + base64.b64decode(reply.get("image_base64", ""))
    else:
        try:
            raw = base64.b64decode(reply.get("image_base64", ""))
        except Exception:
            die("the capture reply is not base64")
    if not raw.startswith(PNG_SIGNATURE):
        die("the capture is not a PNG")
    width, height, pixel = decode_png(raw)
    if (width, height) != (reply.get("width"), reply.get("height")):
        die("the PNG is %dx%d but the reply says %sx%s"
            % (width, height, reply.get("width"), reply.get("height")))
    scale = reply.get("scale", 0)
    box = pixel(BOX_INSIDE[0] >> scale, BOX_INSIDE[1] >> scale)
    if box != RED:
        die("the box pixel is %r, expected red" % (box,))
    back = pixel(BACKGROUND_AT[0] >> scale, BACKGROUND_AT[1] >> scale)
    if back != BLUE:
        die("the background pixel is %r, expected blue" % (back,))
    t.append({"step": "S5", "kind": "capture",
              "detail": {"width": width, "height": height, "scale": scale,
                         "png_bytes": reply.get("png_bytes"),
                         "box": "red", "background": "blue"}})

    # ---- S6 stop: nothing plays, the log stays, no game lingers ----
    CURRENT[0] = "S6"
    if CONTROL != "skip_stop":
        r = run.cli(["call", "stop", "--project-path", run.proj])
        if r["exit"] != 0:
            die("stop was refused: %s" % r["stderr"])
    if run.status().get("playing") is not False:
        die("status.playing is still true after stop")
    after = run.game_log()
    if after.get("game_state") != "no_game":
        die("game_state is %r after stop, expected no_game" % after.get("game_state"))
    if after.get("run_id") != run_id_2:
        die("the last run's entries are gone after stop")
    # The run id alone does not prove the entries stayed: an empty buffer
    # carries the same id. The last run's own lines must still be returned,
    # and its error entry must be absent.
    texts = " ".join(e.get("text") or "" for e in after.get("entries", []))
    if HEARTBEAT not in texts or FIXED_LINE not in texts:
        die("the last run's entries are gone after stop: %r" % texts[:200])
    if entries_by_kind(after, "error"):
        die("an error entry is present in the last run after stop")
    if run.game_processes():
        die("a game process for this workdir still runs after stop")
    t.append({"step": "S6", "kind": "stop",
              "detail": {"playing": False, "game_state": "no_game"}})

    # ---- S7 refusal: capture with no game attached ----
    CURRENT[0] = "S7"
    r = run.cli(["call", "capture-game", "--project-path", run.proj,
                 "--timeout-ms", "1000"], timeout=15)
    if r["exit"] == 0:
        die("capture-game with no game was accepted")
    message = r["stderr"] or r["stdout"]
    if "no game is attached: start the game before capturing it" not in message:
        die("capture-game refused without the documented text: %s" % message[:200])
    t.append({"step": "S7", "kind": "refusal",
              "detail": {"message": "no game is attached: start the game before capturing it"}})


STEPS = ["S1 fixture and baseline", "S2 play and runtime error",
         "S3 fix the script", "S4 rerun clean",
         "S5 capture the game", "S6 stop the game",
         "S7 capture refusal"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--godot", required=True)
    ap.add_argument("--cli", required=True)
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    args = ap.parse_args()

    if os.path.exists(args.workdir):
        die("--workdir already exists, refusing to reuse it: %s" % args.workdir)
    os.makedirs(args.workdir)

    run = Run(args.godot, args.cli, args.port, args.repo, args.workdir)
    started = time.time()
    status = 1
    try:
        build_fixture(run)
        run.launch("res://main.tscn")
        run_scenario(run)
        status = 0
    except Failed as exc:
        print("FAIL %s" % exc)
        run.transcript.append({"step": "FAIL", "kind": "assertion", "detail": str(exc)})
    except Exception as exc:  # a broken step must still leave a transcript
        print("FAIL %s: %s" % (type(exc).__name__, exc))
        run.transcript.append({"step": "FAIL", "kind": "error",
                               "detail": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        # A failed step must not leave a game window behind: stop the game
        # through the editor first, then fall back to the leftover pids.
        try:
            if run.proc is not None and run.proc.poll() is None:
                run.cli(["call", "stop", "--project-path", run.proj], timeout=15)
        except Exception:
            pass
        try:
            for pid in run.game_processes():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        except Exception:
            pass
        run.stop()
        with open(args.out, "w") as h:
            json.dump({"exit": status, "elapsed_sec": round(time.time() - started, 1),
                       "port": args.port, "workdir": args.workdir,
                       "steps": STEPS, "transcript": run.transcript}, h, indent=1)

    if status == 0:
        for name in STEPS:
            print("PASS %s" % name)
    return status


if __name__ == "__main__":
    sys.exit(main())
