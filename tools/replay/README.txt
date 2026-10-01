Golden replay harness for the Godot Pipeline editor plugin (200 rows:
1-148 unchanged, 149-200 cover inspect-class and open-scene).

HOW TO RUN
  GODOT=/path/to/Godot python3 replay.py --plugin-dir DIR --port PORT --out FILE
  GODOT defaults to "godot" on PATH; use Godot 4.7.2 (the baseline was recorded
  with it). From the repository root, DIR is addon/godot_pipeline.
  DIR holds the plugin as it sits in addons/godot_pipeline/ (editor_plugin.gd,
  plugin.cfg, plus any extra .gd files a refactor adds; the whole directory is
  copied, subdirectories included, *.uid skipped at every level). Default port 47902. Never use 47821.
  python3 compare.py BASELINE OTHER
  Prints the first differing request with both replies, or IDENTICAL.
  Exit 0 identical, 1 different.

  Each run takes about half a minute: one --import plus three editor launches
  (session A with no scene, session C with no scene, session B with
  res://main.tscn), 200 requests. The run order is A, C, B but rows are stored
  A, B, C: a fresh editor restores the previous session's open scenes, so the
  second no-scene session must run before the scene session.

WHAT IT DOES
  Session A (requests 1-23, no edited scene): status ok with scene_path null;
  scene-tree/save/rename/create/set/inspect/query/delete each report
  "no scene is currently being edited"; query with wrong project plus bad
  limit reports the project mismatch (guard first); three malformed lines;
  unknown command; missing-fields object per command; one partial line with
  no newline (incomplete-request error after the 5s idle timeout); one
  complete JSON line with no newline (handled normally).
  Session B (requests 24-148, scene open): status, scene-tree, query matrix
  (no filter, class with subclass matches, unknown class, script class_name,
  groups incl. unknown group, globs incl. case-sensitivity, AND combos,
  limits 3/19/1000 with truncation flags, bad limits 0/-1/1001/1.5/"abc",
  wrong project plus bad limit), inspect matrix (root, nested, Node2D,
  Node3D, Control, script-export node incl. inf/-inf/nan strings and
  unsupported-type nulls, instanced paths, wrong project, five bad paths),
  rename matrix (valid, wrong project, unknown node, bad chars, empty name,
  four bad paths, sibling collision -> LeafA2), create matrix (eight error
  kinds incl. instanced parent, two valid), set matrix (17 value types, ten
  Packed arrays, two typed arrays, untyped/Dictionary/Resource/typed-array
  rejections, three malformed shapes, enum range, read-only layout_mode,
  unknown and non-editable properties, wrong project, non-editable and
  editable instance nodes), delete matrix (root/instance/unknown/bad-path/
  wrong-project rejections, valid leaf, subtree and editable-instance
  deletes), save valid plus wrong project, then a post-mutation query
  (17 nodes) and inspect of the renamed node. Mutations run in fixed order;
  later requests see earlier state by design.
  Session C (requests 149-200, new editor, no edited scene, extra c_* fixtures
  that sessions A and B never reference): inspect-class with no scene
  (149-158: Node, Control with its large reply, Object with empty ancestors,
  RefCounted, unknown class, empty class, the project's QryScriptClass which
  is not in ClassDB, non-string class, missing class field, wrong project plus
  bad class where the project guard answers first); open-scene with no scene
  (159-176: wrong project plus bad path, missing fields, non-string fields,
  non-bool save, empty path, missing file, res:// directory, .txt/.gd/.tres
  files, text .scn, broken script/instance/sub-resource references, nested
  broken instance, stale uid); valid opens (177-185: first open with
  saved:false and unsaved [], status, relative path, .. path, canonical
  absolute path, already-edited no-op, open without the save key, save on a
  clean scene, uid:// open); dirty phase (186-195: create_node dirt, open
  without save lists the dirty scene in unsaved, status, dirt the second
  scene, open with save which saves only the edited scene, status, open
  without save, dirt again, open without save with a two-entry unsaved list,
  status); broken-scene rejects on the dirty scene: 196 without save
  (save:false), 197 status, 198 with save (save:true), 199 status, every
  reject a structured error and every status showing the edited scene
  unchanged; inspect-class Node2D with a scene open (200).

NORMALISATION
  The only one: the throwaway project's absolute path is replaced with
  <PROJECT> in stored requests and replies. Replies that carry it are the
  "project path mismatch" errors (10, 46, 54, 61, 70, 131, 141, 146, 158,
  159). Reply text is otherwise verbatim; no sorting, no rounding.

DETERMINISM PROOF
  baseline.jsonl was recorded three times; all three are byte-identical
  (compare.py IDENTICAL, cmp clean). Rows 1-148 are byte-identical to the
  previous baseline (cmp clean on the first 148 lines; their sha256 is still
  17b67de4a11a1285355a173326b7e72b68a72dd2754b89a12400c3309a3edc1d).
  baseline.sha256 holds the sha256 of the whole baseline.jsonl: check with
  shasum -a 256 baseline.jsonl.

NEGATIVE CONTROL
  Two scratch copies of the plugin, each with one error string changed by one
  character, each replayed and compared against the baseline: an inspect_class
  change ("unknown class" -> "unknown clas") differs on exactly requests 153,
  154, 155 (the unknown-class rows) and nowhere else; an open_scene change
  ("no such scene" -> "no such scen") differs on exactly requests 166, 167,
  168, 169, 170, 171 (the missing/dir/type rows) and nowhere else.

COVERAGE (request numbers above)
  Covered: incomplete-request #22; malformed #11-13; unknown command #14;
  no-scene #2-9; missing fields #15-21; project mismatch #10,46,54,61,70,
  131,141,146,158,159; rename paths #65-68, not found #62, empty name #64, bad
  chars #63; create parent paths #78-81, parent not found #75, instanced
  parent #82, unknown class #71, non-Node class #72, cannot instantiate
  #73, empty name #77, bad chars #76; set paths #126-129, not found #125,
  non-editable instance #132, untyped Array #114, typed Object array #117,
  typed unsupported array #118, malformed values #119-121, enum range
  #122, read-only #123, unknown property #124, non-editable (name) #130;
  inspect paths #55-58, not found #59; query unknown class #28, limit
  range #41-43, limit integer #44-45; delete paths #137-140, not found
  #136, root #134, non-editable instance #135; inspect-class no-scene
  #149-158 (valid incl. large Control reply #150 and empty ancestors #151,
  unknown #153-155, field errors #156-157, guard first #158); open-scene
  no-scene guards #159-164, missing/dir/type rejects #165-171, broken scenes
  #172-176; valid opens #177,179-185 (relative, .., absolute, no-op,
  omitted save key, save-on-clean, uid); dirty opens #187,190,192,194
  (unsaved lists with one entry #187,190,192 and two entries #194);
  dirty rejects #196,198; inspect-class with scene #200.
  Not covered, with reason:
  - "request exceeds the maximum size" (>8 MiB without newline): reaching
    it needs an 8 MB write, and the reported byte count varies with TCP
    segmentation, so it cannot be byte-deterministic; framing is also
    untouched by a file-split refactor.
  - "class must be a built-in ... script class" (create_node, query_nodes):
    unreachable in this editor: ClassDB rejects the script class first,
    observed as "unknown class: QryScriptClass" (#29, #74).
  - "the open scene has no file path yet": no command opens an unsaved
    scene, so this branch cannot trigger from outside.
  - "failed to save the scene": needs an IO failure while saving.
  - genuine imported scenes (.gltf, .glb): the editor's import is not
    deterministic enough to record byte-identically (a hand-written
    triangle .gltf was measured: it loads but the editor refuses it).
  - an absolute path through /tmp (symlink to /private/tmp on macOS):
    depends on the temp directory, so it cannot be a fixed request.
  - an untitled dirty scene: making one needs editor-side scripting
    (close all scenes, then set the edited scene), not a socket request.

NOTES
  The fixture project deliberately has no run/main_scene: otherwise the
  editor auto-opens main.tscn and sessions A and C would have a scene. The
  port patch is one substitution of "const PORT := 47821" in the copied plugin
  only. The harness leaves no Godot process and deletes the throwaway
  project every run. Files here: replay.py, compare.py, baseline.jsonl,
  baseline.sha256, README.txt.
  The baseline is only valid while the plugin's replies for these 200 requests
  are supposed to stay the same. A change that adds or alters a command's
  behavior on purpose needs a new baseline, recorded from the last trusted
  commit, and a fresh determinism check (record three times, compare byte for
  byte). Negative control: copy the plugin, change one character in one error
  message, run the harness on the copy; compare.py must report a difference
  only on the requests that carry that string.
  The baseline plugin can be recreated with
  git show d5b8e0c:addon/godot_pipeline (whole directory).
