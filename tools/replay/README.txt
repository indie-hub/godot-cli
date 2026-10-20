Golden replay harness for the Godot Pipeline editor plugin (1325 rows:
1-148 unchanged, 149-200 cover inspect-class and open-scene, 201-262 cover
connect-signal, 263-362 cover set-group, 363-413 cover set-unique-name,
414-477 cover the rename unique-flag fix, 478-530 cover the rename option C
cases, 531-619 cover the inherited-rename rejection, 620-630 cover the fresh
reloads of the accepted inherited-rename cases, 631-636 cover the
instantiate-scene no-scene guards, 637-719 cover instantiate-scene accepted and
rejected cases, 720-729 cover the fresh reloads of the accepted instances,
730-764 cover list-resources, 765-825 cover inspect-resource, 826-827
cover the published command interface (now twenty entries, with console,
set_parent and eval last), 828-842 cover console validation, 843-938 cover
set-parent, 939-945 cover the fresh reload of the accepted set-parent moves,
946-976 cover set-parent in a derived scene, 977-982 cover the fresh reload of
the derived-scene moves, 983-1051 cover set-parent against the inherited-node
matrix, 1052-1128 cover the base-inherited rename and delete rejections and
their fresh reloads, 1129-1222 cover eval, 1223-1310 cover the resource-assignment
session (external texture, shape and material, embedded shape, twenty-two
rejections, an `ordinary.txt` fixture with a file_contains check that proves
the non-resource-path rejection names an existing file, scene and property
state mirrored with two `inspect-node` rows bracketing the rejection block,
file_contains and a save_scene), 1311-1316 cover the resource reloads
(persistence of texture, embedded shape, embedded material, still shared
external shape, and a file_contains), 1317-1322 cover the resource override
on a base-inherited node and a file_contains on the derived scene, and
1323-1325 cover the resource guard against a node inside a non-editable
instance plus a position change on the instance root). The script identity
is read directly with `get_script()` through `eval` (six readings bracketing
the three script rejection rows in the build script) because the `script`
property is encoded as an unsupported null in `inspect-node` and mirrors alone
cannot show a script change.

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
  python3 compare.py BASELINE OTHER --sessions NAMES
  Compares only the rows of the named sessions. The expected rows come from
  replay.py's request builders, so no extra file is read; the counts match
  only when replay.py and the baseline belong to the same revision.
  A replay.py with an altered layout can make a matching wrong output pass,
  so the full run stays the final check. The comparison fails closed: it prints
  IDENTICAL and exits 0 only when OTHER holds exactly the named sessions' rows,
  equal to that slice of BASELINE, in the storage order. A wrong length, a
  missing row, an extra row, a row of another session or rows in another order
  all print a difference and exit 1. The session names are the same ones
  replay.py accepts, so lower case works here too. Without --sessions compare.py
  behaves exactly as above.

  REPLAYING SOME SESSIONS
  python3 replay.py --plugin-dir DIR --port PORT --out FILE --sessions NAMES
  NAMES is a comma-separated list of the session labels above, for example
  W,X or A,EVAL,B. Names are matched without regard to case and may be given in
  any order. The sessions still run in the launch order above, and their rows are
  still stored in the storage order above, so FILE holds only the named sessions'
  rows. An unknown name is refused before any editor starts, and the message
  lists the valid names. An empty value is refused. Without --sessions every
  session runs and FILE is the full 1325-row baseline.
  Every staging step and every fixture write still happens when sessions are
  filtered, so a selected session meets the same project files it meets in a full
  run. Only the editor launches of sessions that neither run nor were asked for
  are skipped, which is what makes a filtered run quick.

  PREREQUISITE SESSIONS
  Twenty-nine of the forty-two sessions reproduce on their own. The other
  thirteen read nodes, saved scene files or a message counter that an earlier
  session left behind, so they do not. --sessions replays those prerequisites
  automatically and prints their names. A prerequisite is replayed but stores no
  rows, so FILE still holds only the sessions you named.
    session  prerequisite    session  prerequisite
    F        D               AA       Z
    I        H               AC       AB
    L        K               AH       AG
    O        N               AK       AI and AJ
    Q        P               CONSOLE  A and Y
    RU       RS
    S        R
    V        U
  Each pair was measured by replaying it and comparing against the baseline.
  CONSOLE is the slowest case: it needs A and Y, so naming CONSOLE alone costs
  three editor launches instead of one. Without --sessions the whole run happens
  as before and this table is not consulted.

  Check a filtered run with one command. Run both from the repository root, so
  the script paths carry the tools/replay/ prefix:
    python3 tools/replay/replay.py --plugin-dir addon/godot_pipeline \
      --port 47920 --out /tmp/filtered.jsonl --sessions W,X
    python3 tools/replay/compare.py tools/replay/baseline.jsonl \
      /tmp/filtered.jsonl --sessions W,X
  The second command prints IDENTICAL (96 pairs) when the filtered run is correct.
  A filtered run writes only FILE; no other file is written beside it.

  A FILTERED RUN PROVES LESS THAN A FULL RUN. A filtered run exercises the named
  sessions and their measured prerequisites, and nothing else. It cannot see a
  leak between two sessions you did not name: such a change passes a filtered
  run and still fails the full run. Use --sessions to work on one command at a
  time. The full run stays mandatory at integration and for validators.

  Each run takes several minutes: one --import plus forty-two editor launches
  (in launch order A with no scene, E with no scene, G with no scene, J with no
  scene, T with no scene, W with no scene, X with no scene, Y with no scene, the
  console session with no scene, the eval session with no scene, C with no
  scene,
  B with res://main.tscn,
  D and F with res://connect.tscn, H and I with res://group.tscn, K and L with
  res://unique.tscn, N, O, P and Q with the rename_*.tscn fixtures, R with
  res://inherit_i3.tscn, S with res://inherit_i1.tscn, U and V with
  res://inst_base.tscn, Z and AA with res://sp_base.tscn, AB and AC with
  res://sp_dderived.tscn, AD with res://sp2_override.tscn, AE with
  res://sp2_plain.tscn, AF with res://sp2_twolevel.tscn, AG and AH with
  res://sp2_local.tscn, AI and AK with res://ib_rename.tscn, AJ with
  res://ib_delete.tscn, RS and RU with res://rs_main.tscn, RD with
  res://rs_derived.tscn, and RI with res://rs_inst.tscn), 1325 stored rows
  (1195 requests and 130 file checks).
  The plugin has twenty commands, a different count. Rows are stored A, B, C,
  E, D, F, G, H, I, J, K, L, N, O, P, Q, R, S, T, U, V, W, X, Y, CONSOLE,
  Z, AA, AB, AC, AD, AE, AF, AG, AH, AI, AJ, AK, EVAL, RS, RU, RD, RI: a
  fresh editor restores the previous session's open scenes, so the no-scene
  sessions (A, E, G, J, T, W, X, Y, the console session and the eval session,
  and C before it opens anything) must run before any scene session. The console
  session runs right after the interface and the eval session right after it;
  both are no-scene, so they run before the first scene session. Appending
  the new sessions keeps rows 1-1222 byte-identical; rows 1223-1325 carry
  the resource-assignment acceptance and rejections, the resource reload, the
  base-inherited override, and the non-editable-instance guard.

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
  Session E (requests 201-206, no edited scene, runs before session C):
  connect-signal with no edited scene, a wrong project, missing fields, a
  non-string source_path, and a non-bool deferred value; each is a structured
  error before any node is resolved.
  Session D (requests 207-256, connect.tscn open): status, a file sha256 of
  connect.tscn, seven accepted connects (both nodes under the root, target
  ".", source ".", --deferred, --one-shot, a source inside an editable
  instance, a target inside a non-editable instance), then eleven rejections
  each bracketed by identical scene_tree before/after snapshots (duplicate,
  missing source, missing target, unknown signal, unknown method, a target
  with no script, a source inside a non-editable instance, a nested instance
  where only the outer ancestor is editable, a duplicate inherited from a
  sub-scene, a wrong project, a non-bool deferred value), a second file sha256
  that must equal the first (connect-signal wrote nothing), a file-bytes check
  that no [connection] line exists yet, save_scene, and file-bytes checks that
  the persisted [connection ...] lines are present.
  Session F (requests 257-262, a fresh editor on the saved connect.tscn):
  four of the seven pairs accepted in session D are reconnected here and must
  be duplicates after the reload (Source.ping -> Target.on_ping; Source.ping
  -> "."; Source.ping -> Sub/SubTarget.sub_on_ping; Edit/SubSource.sub_ping
  -> Target.on_ping), which proves those connections survived the save and
  reload. The root-source, --deferred and --one-shot pairs are not rechecked
  in session F.
  Session G (requests 263-269, no edited scene, runs before session C):
  set-group with no edited scene, a wrong project (guard first), missing
  fields, a non-string node_path, a non-string group, and a non-bool remove;
  each is a structured error before any node is resolved.
  Session H (requests 270-346, group.tscn open): status, a file sha256 of
  group.tscn, nine accepted set-groups (add to a plain child, remove a
  persisted group, add to the scene root, add to an instance root with Editable
  Children off, add to an inner node with Editable Children on, a leading
  underscore, a name with spaces, a unicode name, and a fourth group that
  coexists with the others), then eleven rejections each bracketed by identical
  scene_tree and group-query before/after snapshots (missing node, empty group
  name, add of an existing local group, add of an inherited group, remove of a
  group the node does not have, remove of an inherited group, a node inside a
  non-editable instance, a node inside a nested instance with only some
  ancestors editable, a wrong project, and an add and a remove of a name that
  contains U+FFFD after the parser turns an escaped NUL into the replacement
  character), a second file sha256 that must equal the first (set-group wrote
  nothing), a file-bytes check that no accepted group is on disk yet,
  save_scene, and file-bytes checks that the persisted groups are present and
  the removed and rejected groups absent.
  Session I (requests 347-362, a fresh editor on the saved group.tscn): the
  group queries must list the persisted members (including the instance-root
  member saved with Editable Children off), "beta" must be gone, and adding a
  persisted group again must still be rejected as a duplicate.
  Session J (requests 363-368, no edited scene, runs before session C):
  set-unique-name with no edited scene, a wrong project (guard first), missing
  fields, a non-string node_path, and a non-bool remove; each is a structured
  error before any node is resolved.
  Session K (requests 369-404, unique.tscn open): status, a file sha256 of
  unique.tscn, four accepted edits (set the flag on a plain child, on an
  instance root with Editable Children off, and on an inner node with Editable
  Children on, and clear it on a node that already had it), then nine rejections
  each bracketed by identical inspect_node before/after snapshots (the scene
  root, a node in a non-editable instance, a node that is already unique, an
  inherited flag for an add and for a remove, a removal of a node that is not
  unique, a removal on a node inside a nested instance whose origin cannot be
  read in one step, a same-owner name collision, and a wrong project), a second
  file sha256 that must equal the first (set-unique-name wrote nothing), then
  save_scene and a file-bytes check that a flag line was written.
  Session L (requests 405-413, a fresh editor on the saved unique.tscn):
  inspect_node of seven nodes must show the flag true for the accepted adds and
  the untouched claimant, false for the unset and removed nodes, and setting a
  persisted flag again must be rejected as a no-op.
  Session N (requests 414-464, rename_r1.tscn then one rename_*.tscn scene per
  case): one editor opens each scene in turn and renames P/T (or Sub/P/T, Sub,
  .) to "N". Four rejections are each bracketed by identical scene_tree and
  inspect_node snapshots, with the scene file sha256 equal before and after
  (rename_r1: a unique claimant in the owner scope; rename_r6: a sibling N
  forces the applied name N2, which Q/N2 holds; rename_r7: a claimant in the
  instance scope; rename_r9: an instance-root node renamed onto a claimed
  name). Five non-bracketed renames reply ok and are saved: rename_r2 (target
  not unique), rename_r3 (unique, no clash), rename_r4 and rename_r5 (a
  sibling N forces the applied name N2, which no unique node holds), and
  rename_r10 (the scene root, which has no owner). rename_r8 (claimed only
  in the outer scope, so the unique-name check passes) is in this list too,
  but now replies with the inherited-rename rejection before the undo action,
  so its save_scene writes the untouched fixture.
  Session O (requests 465-477, a fresh editor): opens each saved accepted
  scene and inspects the applied node: P/N in rename_r2 (flag false) and
  rename_r3 (flag true), P/N2 in rename_r4 and rename_r5 (flag true), and the
  root renamed to N in rename_r10. The rejected rename_r8 scene is read at
  Sub/P/T (name T, flag true): the inherited rename was rejected, so nothing
  was applied and the file row and the inspected state are unchanged.
  Session P (requests 478-519, the option C rename scenes in one editor):
  status, then four rejections each bracketed by identical scene_tree and
  inspect_node snapshots with the file sha256 equal before and after, and three
  accepted renames each followed by save_scene. Rejected: Case 1 (Q/N holds the
  requested N), Case 2 with a unique same-stem claimant (Q/N2), zero-padded
  (a sibling N01 makes the engine apply N02, held by Q/N02), and the
  conservative example (a sibling N holds N, a unique Q/N7 exists, the engine
  would apply N2 but the check rejects). Accepted: Case 1 (applied N), Case 2
  with no same-stem claimant (applied N2), and zero-padded with a unique Q/K
  (applied N02).
  Session Q (requests 520-530, a fresh editor): opens the three accepted scenes
  and reads P/N, P/N2 and P/N02 with the flag true, and opens the two rejected
  scenes and reads P/Old with the flag true (the rejections left them
  unchanged).
  Session R (requests 531-619, inherit_i3.tscn then one inherit_i*.tscn scene
  per case): status, then nine rejections each bracketed by identical
  scene_tree and inspect_node snapshots with equal before/after file hashes
  (an inherited child A with Editable Children on and off, an inherited
  grandchild B, the nested instance root and the nested inner A, a rename back
  to the node's own name, the same rename twice in a row, and an inherited
  node that holds a unique name), then a wrong-project rename on an inherited
  target whose project guard replies first, then four accepted renames each
  followed by
  save_scene: two instance-root renames (Editable Children off and on), an
  outer-owned local node under an editable instance, and an outer-owned local
  node under a nested editable instance.
  Session S (requests 620-630, a fresh editor): opens each saved accepted scene
  and reads the renamed node (SubX in both instance-root scenes, the renamed
  local node under the editable instance, the renamed local node under the
  nested instance), and reopens the unique inherited scene to read name A with
  the flag true (the rejected rename changed nothing).
  Session T (requests 631-636, no edited scene, runs before session C):
  instantiate-scene with no edited scene, a wrong project, missing fields, and a
  non-string scene_path and parent_path; each is a structured error before any
  scene is resolved.
  Session U (requests 637-719, inst_base.tscn open): status, a file sha256 of
  inst_base.tscn, eight accepted instantiations (a plain scene, S under the
  root, S under the root again so a sibling collision applies R2, S under an
  instance root with Editable Children off and on, S under a local node owned
  by the root inside an editable instance, a scene that itself instances S, and
  a Control-root scene under a plain Node), then seventeen rejections each
  bracketed by identical scene_tree snapshots (an inner node of a non-editable
  instance and of an editable instance, a missing parent, four bad parent-path
  shapes, a scene path that is not res://, has "..", has the wrong extension is
  a .gd file, is missing, fails to parse, and has a missing dependency, a self
  cycle, a wrong project, and a wrong project with other invalid fields where
  the project guard answers first), a second file sha256 equal to the first, a
  file_not_contains that no added instance is on disk yet, save_scene, and
  file_contains checks that the accepted instances were written. It then opens
  inst_plain.tscn for the self-cycle rejection and inst_e.tscn for the
  transitive cycle (inst_t.tscn instances the edited scene) and the inheritance
  cycle (inst_derived_e.tscn inherits the edited scene), each bracketed by an
  equal before/after hash of its own fixture.
  Session V (requests 720-729, a fresh editor on the saved inst_base.tscn):
  status, scene_tree and query_nodes, then inspect_node of the applied instance
  nodes (R2, PlainRoot, SubOff/R, SubOn/LocalUnderOn/R, OfSubRoot, CtlRoot),
  and a file_contains that the R2 instance row is on disk. This proves the
  accepted instances survive a save and a fresh reload.
  Session W (requests 730-764, no edited scene, runs before session C): the
  list-resources matrix. status, then the full listing (limit 1000), limits
  1/2/1000, rejected limits 0/1001/1.5/"abc", type filters (GDScript,
  PackedScene, Resource, StandardMaterial3D, Node, Script), the unknown and
  project-script classes, an empty type, path_prefix matches (res://inst_ and
  the commands folder) and the not-res:// rejection, an empty prefix, cursor
  pages after res://inst_plain.tscn and a PackedScene cursor after
  res://sub.tscn, cursors that are absent or not res://, each wrong field type,
  a wrong project with other invalid fields (the project guard answers first),
  and a refresh false that returns the ordinary listing. The session waits,
  unrecorded, until the editor's file-system scan reports scanning false
  before its first recorded request, so a fresh editor's startup scan cannot
  change a reply.
  Session X (requests 765-825, no edited scene, runs right after session W):
  the inspect-resource matrix. It runs after W's file-listing rows are
  recorded, so the fixture files it needs are written into the throwaway
  project only at that point and never appear in the earlier rows' listings;
  the new `inspect_resource.gd` command script is staged into the copied
  plugin only for this session too, so rows 1-764 list the previous command
  set. status, then: accepted typed reads of a script-backed .tres with an
  embedded sub-resource (shown inline) and an external reference ({path,type}),
  its external dependency .tres, a generated binary .res (text-format .res
  does not load in 4.7.2, so the fixture is produced by ResourceSaver and its
  bytes are deterministic, measured byte-identical across independent project
  paths), a script-backed binary .res, a .gdshader, a .tres whose array
  exceeds 10,000 elements (replaced by {type,size,truncated:true}), an
  embedded sub-resource chain that reaches the reply depth limit of 3 (the
  fourth level shows as {path,type}) and a self-referencing embedded
  sub-resource (the loop guard shows the second visit as {path,type}); the
  refusals: a text .tres over 4 MiB and an uppercase .TRES over 4 MiB
  (refused before parse), a resource whose dependency chain contains a @tool
  script, reached on the top script, on an embedded sub-resource, on an
  external dependency, and through a script preload (including the malformed
  resource that would fail to load, which must still be refused before any
  project code runs), through a multi-line `preload(` literal, through a
  `ResourceLoader.load("...", "", 1)` literal with trailing arguments, and
  through the `extends` edge: an ordinary base reached by `extends "path"` or
  `extends ClassName` (its own preload of a @tool resource refuses the whole
  chain), including a two-level extends chain and a one-line
  `class_name Name extends Base` form and an inner `class Name extends ...`
  form (both walked), and through a `preload("uid://...")` literal whose uid
  resolves through the ResourceUID registry to a resource with a @tool script
  (both the well-formed and the malformed forms, accepted in the former scan,
  are now refused); a resource whose script merely extends a @tool script in
  the `class_name` form, or extends it by `extends "res://..."` path, is
  refused and the reply names the base script, an ordinary script that
  mentions @tool only in a comment (accepted), an ordinary script whose
  comment or string merely contains the word `extends` (accepted, adds no
  path), an ordinary script with an `@icon("...")` prefix on its extends line
  whose base is ordinary (accepted), an ordinary script with a @tool-looking
  line inside a triple-quoted string (accepted), a script whose base is a
  built-in class (accepted), an ordinary script that preloads a non-@tool
  resource (accepted), a scene .tscn/.scn and uppercase .TSCN/.SCN and a
  script .gd and uppercase .GD (kind refusals with pointers), a missing file,
  a corrupt .tres, an empty .tres, a text scene named .res, an unknown
  extension, a TextFile, a dangling symlink, and the project root directory
  (all "resource not found"/"failed to load" errors), the path-rule refusals
  (not res://, "..", uid://), wrong-project guards (with both valid and
  invalid paths), and the missing-field and bad-type rows. Each cold payload
  in the scan cases uses its own @tool script and resource files that no
  other row loads in the session, so a refused or failed-to-load request is
  never warmed by an earlier accepted one; the uid fixture's uid is fixed, so
  its registration is deterministic.

Session Y (requests 826-827, no edited scene, runs right after session X):
  the published command interface. Request 826 asks for the interface with no
  other field and gets protocol_version 1 with the twenty command entries,
  each handler's own name, description, flags and arguments, in the order of the
  plugin's COMMANDS table. Request 827 repeats it with a project_path that is
  not the project's: the interface request checks no project path, so the reply
  must equal row 826 byte for byte.

Session Z (requests 843-938, sp_base.tscn open, runs after session V): the
  set-parent matrix. status, a file sha256 of sp_base.tscn, a baseline
  scene_tree, then the accepted moves, each with its own mover node so no undo
  is needed between cases: a plain node appended under a new parent; a Node2D, a
  Node3D and a Control moved with the default keep_global_transform (the
  inspect_node of each transform node shows the local position the parent
  transform adjusted to); an instanced sub-scene root moved under a plain node;
  and a same-parent move to an explicit index. Then a raw request with
  keep_global_transform false, because the CLI's boolean flag can only send
  true. Every rejection is bracketed by identical scene_tree snapshots: missing
  fields, a non-string node_path, a non-bool keep_global_transform, the scene
  root as the moved node, a new parent equal to the node and a new parent inside
  the node's subtree, a same-parent no-op, a sibling name collision, a node
  inside a non-editable instance, a node inherited from an editable instance, a
  new parent inside a non-editable and an editable instance, an index above and
  below the range (and one fractional and one string), an unknown node, a wrong
  project with other invalid fields (the project guard answers first) and a
  wrong project with an out-of-range index, an empty node path and an empty,
  absolute, colon and ".." new parent path. The file sha256 before and after the
  rejections is equal, and no accepted move is on disk yet
  (file_not_contains); then Box and Uniq are moved under Target, save_scene
  runs, and file_contains proves the persisted connection (now
  Target/Box -> Target), the moved Box row, the MSub instance row and the unique
  name flag. The post-save bytes are not hashed: the save adds generated uids
  and unique_ids.

Session AA (requests 939-945, a fresh editor on the saved sp_base.tscn): status,
  scene_tree, inspect_node of the moved Uniq (the flag survives) and of the
  moved Node2D (its local position survives), a query_nodes for the moved
  group, a connect_signal on Target/Box -> Target that must be rejected as a
  duplicate (the connection survived the reload), and a file_contains for the
  persisted connection line.

Session AB (requests 946-976, sp_dderived.tscn open; it inherits
  sp_dbase.tscn, so BaseNode and BaseChild are owned by the edited scene root
  but are not stored by the edited scene): the set-parent derived-scene matrix.
  status, a file sha256 of sp_dderived.tscn, a baseline scene_tree, then six
  rejections bracketed by identical scene_tree snapshots: a wrong project path
  with a fractional index, a wrong project path with a non-bool
  keep_global_transform, a wrong project path with a non-string node_path and
  a wrong project path with a non-string new_parent_path (the project guard
  answers first in all four), and a move of BaseNode and of BaseNode/BaseChild
  (both inherited from the base scene, rejected with the base-scene message).
  The file sha256 before and after the rejections is equal and no move is on
  disk yet (file_not_contains). Then a local node (MoveMe) moves under a local
  parent and a local node (Local) moves under the inherited BaseNode (an
  inherited node is an eligible parent, as for create_node); save_scene writes
  both, file_contains proves the two rows, and file_not_contains proves the
  derived scene did not store a BaseNode row.

Session AC (requests 977-982, a fresh editor on the saved sp_dderived.tscn):
  status, scene_tree, inspect_node of the moved Holder/MoveMe and of
  BaseNode/Local, a query_nodes for the name BaseNode (exactly one node, no
  copy was written), and a file_contains for the persisted MoveMe row.

Session AD (requests 983-1006, sp2_override.tscn): the inherited-node
  matrix. sp2_base.tscn holds a three-level chain (BaseCold, ChildCold,
  NestedCold), a plain node and an instanced sub-scene; sp2_override.tscn
  inherits it and overrides a property on all three levels plus a script and a
  group on the plain node. All six moves are rejected, each bracketed by
  identical scene_tree snapshots. The file sha256 is equal before and after
  (cdaca1f1...) and the file holds no node under Target, so no local copy was
  written. SubCold/InnerCold is refused by the earlier sub-scene rule.
Session AE (requests 1007-1017, sp2_plain.tscn): the control that inherits with
  no override at all. Both moves are rejected the same way and the file is
  unchanged (1e35dc05...).
Session AF (requests 1018-1034, sp2_twolevel.tscn): a two-level base chain.
  sp2_mid.tscn inherits sp2_base.tscn and adds one node, so the intermediate
  base state holds only the instance row; the inherited node is found one level
  further down. All four moves are rejected and the file is unchanged
  (77056cca...).
Session AG (requests 1035-1043, sp2_local.tscn): a node the derived scene owns
  itself, added under an inherited parent, stays movable. The move succeeds,
  the file sha256 is unchanged until the explicit save_scene, and the saved
  file holds one copy under Target and none under BaseCold.
Session AH (requests 1044-1051, a fresh editor on the saved sp2_local.tscn):
  Target/AddedUnderInherited is present after the reload, and query_nodes
  returns exactly one node each for AddedUnderInherited, BaseCold, ChildCold
  and NestedCold, so nothing was duplicated.
Session AI (requests 1052-1084, ib_rename.tscn open; it inherits sp2_base.tscn,
  so its inherited nodes have the edited scene root as owner while Target is
  local): status, a file sha256 of ib_rename.tscn, a baseline scene_tree, then
  seven rejections each bracketed by identical scene_tree replies and with an
  equal before/after file hash: a rename of a base-inherited node with a
  property override (BaseCold), with a property and script override
  (BasePlain), of two inherited descendants (BaseCold/ChildCold and
  BaseCold/ChildCold/NestedCold), of the base scene's instanced sub-scene root
  (SubCold), and a wrong project path on an inherited target (the project guard
  answers first); the last rejection is a set_parent of BaseCold, which shows
  the rename-then-set-parent composition cannot start because the rename is
  refused. Then the local node Target is renamed to TargetX, its scene_tree is
  recorded, the file hash is still equal before the save, save_scene runs, and
  file_contains proves the persisted TargetX row and the inherited BaseCold
  override row; file_not_contains proves no renamed copy was written.
Session AJ (requests 1085-1113, ib_delete.tscn open, the same text as
  ib_rename.tscn): status, a file sha256, a baseline scene_tree, then six
  rejections each bracketed by identical scene_tree replies and with an equal
  before/after file hash: a deletion of BaseCold, BasePlain, BaseCold/ChildCold
  and SubCold (all base-inherited), a wrong project path on an inherited target
  (the project guard answers first), and a set_parent of BaseCold after the
  refused delete, which shows the delete-then-create-then-set-parent
  composition cannot start. Then the local node Target is deleted, its
  scene_tree is recorded, the file hash is still equal before the save,
  save_scene runs, and file_not_contains proves Target is gone while file_contains
  proves the inherited BaseCold override row is untouched.
Session AK (requests 1114-1128, a fresh editor on the scene sessions AI and AJ
  saved): status and scene_tree, then inspect_node and query_nodes prove the
  renamed local TargetX is present and BaseCold and ChildCold are exactly one
  node each (no copy was written), and a file_contains proves the persisted
  TargetX row; open_scene loads the saved ib_delete.tscn, then inspect_node
  reports Target as not found and query_nodes returns no Target and exactly one
  node each for BaseCold, ChildCold and NestedCold, so the allowed local delete
  persisted and the inherited chain was not duplicated.

Session CONSOLE (requests 828-842, no edited scene, runs right after session Y):
  the console command's validation. Every row is rejected before the buffer is
  read, except the last one: the project guard answering first on a wrong
  project with other invalid fields, the missing and non-string project path,
  the mistyped and out-of-range tail (abc, 0, 1001, 1.5), the unknown and
  non-string level, the mistyped and negative since, and the non-bool clear.
  The last row is a successful read with a cursor past every message, which
  returns no messages, only the cursor state. A successful read of real
  messages is not recorded: the editor's startup log text varies by run, so it
  cannot be byte-deterministic; the happy path (levels, tail, since, clear,
  eviction, truncation, single logger after disable/enable) is proved by
  isolated-editor probes instead.
Session EVAL (requests 1129-1222, starts with no edited scene, runs right after the console session):
  the eval command. The eval.gd script and its COMMANDS line are restored into
  the copied plugin just before this session, so session Y's interface carries
  eval as entry 20 and rows 1-825 (whose sessions never saw the file) stay
  byte-identical. The fixture res://eval_bad_preload.gd is written just before
  this session, not earlier: session W lists the project files, so a file added
  before it would change rows 730-764. Rows 1129-1180 run with no edited scene,
  so scene_changed is false and no request mutates stored state except the two
  editor-meta markers (the Object leak marker and the retained parent); rows
  1181-1182 open res://main.tscn and then edit it, so scene_changed is true, and
  rows 1183-1222 run with that scene still open and do not edit it, so
  scene_changed stays false there.
  The rows are: seven value
  returns (int, float, string, Vector2, Array, Dictionary, null, each shown as
  typed JSON through JSON.from_native), a print-output snippet, a compile error,
  a runtime error, a snippet that blocks past a small timeout (timeout 100,
  elapsed about 300 ms, so timeout_exceeded true), and five rejected requests
  (a wrong project path, a missing code field, a non-string code, a zero
  timeout, and a wrong project path paired with a non-string code where the
  project guard answers first), the refused encodings (an Object result as
  completed false, a 2000-deep Array refused past the tagged depth, and the
  oversized value), and the repair rows (an Array of exactly the 100 tagged
  levels that still returns exactly, rich and spaced output, an explicit null
  timeout refused, a warning that keeps its value, a push_error that fails, an
  _init error that fails, an Object instance held in editor meta and reported
  freed on the next call, three cases where an error raised while the reload
  initializes the submitted @tool script (a failing static initializer, a
  _static_init that calls push_error, and a preloaded @tool script with an
  error) each answered with completed false and value null and each followed by
  a clean call, the tagged depth boundary for an Array (100 levels returned, 101
  refused), a Dictionary (100 levels returned, 102 refused), a mixed shape
  (100 levels returned, 103 refused), and a Vector2-ended, a
  packed-array-ended, and a Transform3D-ended shape (each 100 levels returned,
  101 refused), a typed-Array[int]-ended shape (100 levels returned, 101
  refused) and a chain of typed Arrays (49 wrappers returned at 99 levels, 50
  refused at 101), a Node that another Node parents kept
  alive across two calls, a scene switch that stays clean, and a switch with an
  edit that reports the
  change), 21 display rows (each returning a value of a covered class, one run
  that does not complete, and one PackedByteArray whose readable form is over
  the cap while its encoding is inside it, which now stays ok with the value,
  display null and one added warning), and 19 parse rows. Each parse request
  returns str_to_var of the display text that the matching display row's reply
  actually carried. Each reply is normalised by
  normalize_eval_reply, so the run-dependent elapsed milliseconds and the
  anonymous gdscript:// source path are placeholders. Every recorded eval reply
  body parses as JSON, so an invalid reply fails the recording, and
  check_eval_display proves that every status-ok eval reply carries display (a
  string, or null when the run did not complete or the display was over the
  cap), that each display row carries its rt:true marker, and that each parse
  row's value equals the value the matching display row returned, with a null
  value's display required to be the canonical null text, because invalid text
  parses to null.

NORMALISATION
  The throwaway project's absolute path is replaced with <PROJECT> in stored
  requests and replies. Replies that carry it are the "project path mismatch"
  errors (10, 46, 54, 61, 70, 131, 141, 146, 158, 159, 203, 244, 265, 323,
  365, 400, 606, 633, 693, 696, 763, 818, 821, 829, 909, 912, 950, 953, 956,
  959, 1071, 1101, 1140, 1144). For the eval rows two run-dependent values are
  also replaced, by normalize_eval_reply: the elapsed milliseconds become the
  string "<ELAPSED>" and an anonymous gdscript://-<id>.gd source path (a
  compile or runtime error's file, which is a pointer that differs every run)
  becomes "<ANON>". Reply text is otherwise verbatim; no sorting, no rounding.
  Every recorded eval reply body parses as JSON.

DETERMINISM PROOF
  For the previous 1182-row baseline, the combined rows were recorded twice; the two full runs are byte-identical
  (compare.py IDENTICAL, 1182 pairs, and cmp clean between the two runs). Rows
  1-825 are byte-identical to the baseline that preceded this change: their
  sha256 is
  99f6ebef9215d318e32ee516db497e9c3a12488b19de1321e135da93991056e7. Rows
  826-827 change only because the interface now carries console, set_parent and
  eval as entries 18, 19 and 20. The three new command scripts and the console
  Logger wiring are staged out of the copied plugin while sessions A..W record
  (so session W's commands-folder listing keeps the pre-existing command set)
  and restored together before session Y; the set-parent fixtures are written
  only before the set-parent sessions and the eval fixture only before the eval
  session. A separate script maps every row of the three accepted baselines
  beyond row 827 to its combined row and finds every one byte-identical.
  The base-inherited rename and delete rows were recorded twice; the two full
  runs are byte-identical (compare.py IDENTICAL, 1113 pairs). Rows 1-1036 are
  byte-identical to the 1036-row baseline that preceded this change, whose
  sha256 is
  52754cd8c00d56a58762cd20403a26e8c9bd3913e4c12bf41fb66121205458d0; rows
  1-825 keep their sha256
  99f6ebef9215d318e32ee516db497e9c3a12488b19de1321e135da93991056e7. The new
  fixtures ib_rename.tscn and ib_delete.tscn are written only after session AH
  records, so the earlier listings (session W) never name them. Every rejection
  is bracketed by identical scene_tree replies, each session's file hash is
  equal before and after them, and the two accepted local edits are not hashed
  after their save (the save adds generated uids and unique_ids).
  The set-parent rows were recorded twice; the two full runs are byte-identical
  (compare.py IDENTICAL, cmp clean, 967 pairs). Rows 1-825 are byte-identical
  to the 825-row baseline that preceded this change; their sha256 is
  99f6ebef9215d318e32ee516db497e9c3a12488b19de1321e135da93991056e7, which is
  the sha256 of that whole file. Rows 826-827 (the published command interface)
  change only by the set_parent entry appended as the last entry of the COMMANDS
  table. The new command script is staged out of the copied plugin while
  sessions A..W record and restored only before session Y, and the new fixture
  files are written only after session Y, so the earlier commands-folder and
  file listings never change. Rows 828-923 are the set-parent session and rows
  931-961 are the derived-scene session; their replies carry no generated id,
  and the post-save file bytes (which do carry generated uids and unique_ids)
  are never hashed. Rows 924-930 and 962-967 are the fresh reloads of the saved
  scenes.
  The command-interface rows were recorded twice; the two full runs are
  byte-identical (compare.py IDENTICAL, cmp clean, 827 pairs). Rows 1-825 are
  byte-identical to the 825-row baseline that preceded this change: their
  sha256 is 99f6ebef9215d318e32ee516db497e9c3a12488b19de1321e135da93991056e7,
  which is the sha256 of that whole file. The commands request adds a branch to
  the plugin's dispatch and a static interface() to each handler, reads no
  project path and no scene, and no earlier row names it.
  The inspect-resource rows were recorded twice; the two full runs are
  byte-identical (cmp clean, 825 pairs). Rows 1-764 are byte-identical to the
  764-row baseline that preceded this change (git show HEAD): the new command
  script is staged out of the copied plugin while sessions A..W record, and
  the new fixture files are written only for session X, so the earlier
  commands-folder and file listings never change. Row 765 is a status request
  and rows 766-825 are the inspect-resource session; the generated binary .res
  fixture bytes are byte-identical across independent project paths (measured).
  The list-resources rows were recorded twice; the two full runs are
  byte-identical (compare.py IDENTICAL, cmp clean, 764 pairs). Rows 1-729 are
  byte-identical to the 729-row baseline that preceded this change (git show
  HEAD). Rows 730-764 are the list-resources session; its first recorded
  request follows an unrecorded wait until the editor reports scanning false.
  The instantiate-scene rows were recorded twice; the two full runs are
  byte-identical (compare.py IDENTICAL, cmp clean, 729 pairs). Rows 1-630 are
  byte-identical to the 630-row baseline that preceded this change (git show
  HEAD) and to the baseline on main. Rows 631-636 are the instantiate-scene
  no-scene guards, 637-719 the accepted and rejected cases, and 720-729 the
  fresh reloads of the accepted instances.
  The inherited-rename stretch was recorded twice and was byte-identical (630
  pairs). At that recording, rows 1-530 matched the then-current baseline
  except row 460, whose inherited inner rename now replies with the rejection;
  rows 1-362 matched the main baseline. Rows 363-530 were unchanged from the
  prior rename-flag baseline except row 460. Rows 531-619 are the
  inherited-rename cases, and rows 620-630 are the fresh reloads of the
  accepted ones; rows 1-413 also matched the set-unique-name baseline, whose
  whole file sha256 was
  0d3de504f4313386f4f58525c2fc139dfa2604781d5a5ad30b2efb9f692312e1. Rows
  1-362 remain byte-identical to the set-group baseline, whose whole file
  sha256 was
  63deb099fac0ebc04345af0e0ea5cf8e3d17bf5ffabae8a8e5abc71d779ae798. Rows
  1-262 are byte-identical to the connect-signal baseline, whose whole file
  sha256 was
  1b1cd260ce425d710525620e8fa4384e0c58b374665a4875ae813c6a3f6c4877. Rows
  1-200 are byte-identical to the 0.5.0 baseline, whose whole file sha256 was
  9251fb936b3a736a5a7d9823d66b0f27910072575fad599e331756199c7a6d87, and rows
  1-148 still have their own sha256
  17b67de4a11a1285355a173326b7e72b68a72dd2754b89a12400c3309a3edc1d.
  The display rows were recorded twice; the two full runs are byte-identical
  (compare.py IDENTICAL, 1222 pairs, and cmp clean). Rows 1-1128 are
  byte-identical to the 1182-row baseline that preceded this change: their
  sha256 is
  f1a712a5520f9bc63a2b20ce0cebf67b62ab43f3cb74db5feb822e95e0819ff1, which is
  the sha256 of the first 1128 lines including their trailing newlines (the
  method that also reproduces the pinned hash on the old baseline). Every old
  eval row (1129-1182) equals its base row after the display key is removed, and
  no other key changes (the 47 status-ok rows gain only display; the seven
  status-error rows are unchanged); rows 1183-1202 are unchanged from the
  pre-repair
  baseline, row 1203 changes only to the policy A shape (status ok, value kept,
  display null, one added warning), and the 19 parse rows are appended at
  positions 1204-1222. The new whole-file sha256 is
  e0e9879df1bab7e764de7af5bc2db1abe6043bb38227eacec627f7d8b16adf42.
  baseline.sha256 holds the sha256 of the whole baseline.jsonl: check with
  shasum -a 256 baseline.jsonl.
  The 1325-row baseline was recorded twice; the two full runs are byte-identical
  (compare.py IDENTICAL, 1325 pairs, and cmp clean between the two runs). Rows
  1-1222 stay byte-identical to the previous 1222-row baseline at
  af10539; the new 103 rows (RS, RU, RD, RI) are the resource-assignment
  acceptance and rejections, the resource reload, the base-inherited override
  and the non-editable-instance guard. The new whole-file sha256 is
  a2d1f45ade08a1ab978848250b65eaebec0e7c0877e9ce16b92fcc6312b5664c.
  A saved scene file is hashed only before a save. After save_scene the editor
  may write generated unique_id values into saved node declarations, and an
  override-only declaration can omit it. So the post-save bytes are not
  deterministic; the write is proven by the file_contains checks instead.

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
  connect-signal #201-262: no-scene #201-206 (status; no edited scene;
  wrong project; missing fields; non-string source_path; non-bool deferred);
  D #207-256: status #207, unchanged-file hash #208, accepted both-under-root
  #209, target "." #210, source "." #211, --deferred #212, --one-shot #213,
  source in an editable instance #214, target in a non-editable instance #215;
  rejections with identical scene_tree pairs (duplicate #216-218, missing
  source #219-221, missing target #222-224, unknown signal #225-227, unknown
  method #228-230, target without a script #231-233, source in a non-editable
  instance #234-236, nested instance where only the outer ancestor is editable
  #237-239, duplicate inherited from a sub-scene #240-242, wrong project
  #243-245, non-bool deferred #246-248); unchanged-file hash #249; no
  [connection] line yet #250; save_scene #251; persisted [connection] checks
  #252-256. F #257-262: status #257, duplicates after the fresh reload
  #258-261, scene_tree #262.
  set-group #263-362: no-scene #263-269 (status; no edited scene; wrong
  project first; missing fields; non-string node_path; non-string group;
  non-bool remove); H #270-346: status #270, unchanged-file hash #271,
  accepted add/remove #272-280, rejections with identical scene_tree and
  group-query pairs (missing node #281-285, empty group #286-290, add of an
  existing local group #291-295, add of an inherited group #296-300, remove of
  a group the node does not have #301-305, remove of an inherited group
  #306-310, node in a non-editable instance #311-315, node in a nested
  instance with only some ancestors editable #316-320, wrong project
  #321-325, U+FFFD add #326-330, U+FFFD remove #331-335), unchanged-file hash
  #336, no accepted group on disk yet #337, save_scene #338, persisted-group
  checks #339-346; I #347-362: status #347, persisted members #348-357,
  removed and rejected groups absent #358-360, scene_tree #361, duplicate add
  after the reload #362.
  set-unique-name #363-413: no-scene #363-368 (status; no edited scene; wrong
  project first; missing fields; non-string node_path; non-bool remove);
  K #369-404: status #369, unchanged-file hash #370, accepted add/remove
  #371-374, rejections with identical inspect_node pairs (scene root #375-377,
  non-editable instance #378-380, already unique #381-383, inherited add
  #384-386, remove not unique #387-389, inherited remove #390-392, nested
  origin #393-395, collision #396-398, wrong project #399-401), unchanged-file
  hash #402, save_scene #403, flag-line check #404; L #405-413: status #405,
  flag state after the reload #406-412, duplicate add #413.
  rename unique-flag #414-477: N #414-464: status #414, then per case an
  open_scene plus the rename. Rejections with identical scene_tree and
  inspect_node pairs and equal before/after file hashes (r1 #415-422, r6
  #423-430, r7 #431-438, r9 #439-446); accepted renames and save_scene (r2
  #447-449, r3 #450-452, r4 #453-455, r5 #456-458, r10
  #462-464); r8 #459-461 now replies with the inherited-rename rejection,
  so its save_scene writes the unchanged scene; O #465-477:
  status #465, then the applied node in each saved
  scene (r2 #466-467, r3 #468-469, r4 #470-471, r5 #472-473, r8 #474-475, r10
  #476-477).
  rename option C #478-530: P #478-519: status #478; rejected Case 1 #479-486,
  rejected Case 2 same-stem #487-494, rejected zero-padded #495-502, rejected
  conservative same-stem #503-510, each with identical scene_tree and
  inspect_node pairs and an equal before/after file hash; accepted Case 1
  #511-513, accepted Case 2 no same-stem claimant #514-516, accepted
  zero-padded with a unique K #517-519; Q #520-530: status #520, applied name
  and flag after the reload for rename_case1_accept #521-522,
  rename_stem_accept #523-524 and rename_pad_accept #525-526, and the
  unchanged rejected scenes rename_case1_reject #527-528 and
  rename_conservative #529-530.
  inherited-rename #531-630: R #531-619: status #531; rejected inherited child
  A editable on #532-539 and off #548-555, inherited grandchild B #540-547,
  nested instance root #556-563, nested inner A #564-571, rename to the node's
  own name #572-579, the same rename twice in a row #580-587 and #588-595,
  inherited unique node #596-603, each with identical scene_tree and
  inspect_node pairs and an equal before/after file hash; wrong-project rename
  on an inherited target #604-607 (project guard answers first); accepted
  instance-root rename editable off #608-610 and on #611-613, outer-owned
  local under an editable instance #614-616, outer-owned local under a nested
  editable instance #617-619; S #620-630: status #620, renamed instance root
  after reload editable off #621-622 and on #623-624, renamed local under the
  editable instance #625-626, renamed local under the nested instance
  #627-628, rejected unique inherited scene unchanged #629-630 (name A, flag
  true).
  instantiate-scene #631-729: T #631-636 (status; no edited scene; wrong
  project first; missing fields; non-string scene_path; non-string parent_path);
  U #637-719: status #637, unchanged-file hash #638, accepted plain #639, S
  applied R #640 and R2 after a sibling collision #641, S under an instance root
  with Editable Children off #642 and on #643, S under a local node in an
  editable instance #644, a scene that instances S #645, a Control root under a
  plain Node #646; rejections with identical scene_tree pairs (inner node of a
  non-editable instance #647-649, inner node of an editable instance #650-652,
  missing parent #653-655, empty parent #656-658, absolute parent #659-661,
  colon parent #662-664, ".." parent #665-667, not res:// #668-670, ".." scene
  #671-673, wrong extension #674-676, .gd scene #677-679, missing scene
  #680-682, unparsable scene #683-685, missing dependency #686-688, self cycle
  #689-691, wrong project #692-694, wrong project with invalid fields
  #695-697); unchanged-file hash #698; no instance on disk yet #699; save_scene
  #700; written instances #701-706; self cycle on inst_plain #707-712 (hash,
  open, scene_tree, reject, scene_tree, hash); transitive and inheritance
  cycles on inst_e #713-719 (hash, open, scene_tree, reject, reject, scene_tree,
  hash); V #720-729: status #720, scene_tree #721, query_nodes #722,
  inspect_node of R2 #723, PlainRoot #724, SubOff/R #725, SubOn/LocalUnderOn/R
  #726, OfSubRoot #727, CtlRoot #728, and the R2 instance row on disk #729.
  list-resources #730-764: W #730-763 (status #730; full listing #731; limits
  1/2/1000 #732-734; rejected limits 0/1001/1.5/"abc" #735-738; type GDScript
  #739, PackedScene #740, Resource #741, StandardMaterial3D #742, Node #743,
  Script #744, unknown class #745, project script class #746, empty type #747;
  path_prefix res://inst_ #748, the commands folder #749, not-res:// #750,
  empty #751; cursor page after res://inst_plain.tscn with limit 2 #752 and
  limit 1000 #753, PackedScene cursor after res://sub.tscn #754; cursor not in
  the tree #755-756; cursor not res:// #757; wrong field types path_prefix
  #758, type #759, cursor #760, refresh #761, project_path #762; a wrong
  project with other invalid fields where the project guard answers first
  #763), and refresh false with a full listing #764.
  command interface #826-827: Y #826 (the interface, no other field) and #827
  (the same request with a project_path that is not the project's, which must
  answer identically).
  console #828-842: Z #828 (status), the project guard answering first on a
  wrong project with other invalid fields #829, the missing and non-string
  project path #830-831, the mistyped and out-of-range tail #832-835, the
  unknown and non-string level #836-837, the mistyped and negative since
  #838-840, the non-bool clear #841, and a successful read with a cursor past
  every message #842.
  set-parent #843-945: Z #843-938 (status #843; unchanged-file hash #844;
  baseline scene_tree #845; accepted moves #846-852: MNode under Target, M2D
  under Holder2D, M3D under Holder3D, MCtl under HolderCtl, MSub under Target,
  MIndex/IB to index 0, and MNoKeep under HolderNoKeep with
  keep_global_transform false as a raw request; post-move scene_tree #853 and
  the transform inspect_node replies #854-856; rejections with identical
  scene_tree pairs: missing fields #857-859, non-string node_path #860-862,
  non-bool keep_global_transform #863-865, the scene root #866-868, a parent
  equal to the node #869-871, a parent inside the node's subtree #872-874, a
  same-parent no-op #875-877, a sibling name collision #878-880, a node inside
  a non-editable instance #881-883, an inherited node #884-886, a new parent
  inside a non-editable instance #887-889 and an editable one #890-892, an
  index above the range #893-895, below it #896-898, fractional #899-901 and a
  string #902-904, an unknown node #905-907, a wrong project with other invalid
  fields #908-910, a wrong project with an out-of-range index #911-913, an
  empty node path #914-916, and an empty #917-919, absolute #920-922, colon
  #923-925 and ".." #926-928 new parent path; unchanged-file hash #929; no
  moved Box on disk yet #930; Box and Uniq under Target #931-932; save_scene
  #933; the persisted connection, Box row, MSub instance row and unique flag
  #934-938). AA #939-945 (status #939, scene_tree #940, the moved Uniq #941,
  the moved Node2D #942, the moved group query #943, the duplicate connection
  #944, and the persisted connection line #945).
  set-parent derived scene #946-982: AB #946-976 (status #946; unchanged-file
  hash #947; baseline scene_tree #948; six rejections with identical scene_tree
  pairs: a wrong project path with a fractional index #949-951, with a non-bool
  keep_global_transform #952-954, with a non-string node_path #955-957 and with
  a non-string new_parent_path #958-960, and a move of the base-inherited
  BaseNode #961-963 and of BaseNode/BaseChild #964-966; unchanged-file hash
  #967; no MoveMe row on disk yet #968; MoveMe under Holder #969 and Local
  under the inherited BaseNode #970; scene_tree #971; unchanged-file hash #972;
  save_scene #973; the persisted MoveMe row #974, the persisted Local row #975
  and no BaseNode row in the derived scene #976). AC #977-982 (status #977,
  scene_tree #978, the moved Holder/MoveMe #979, the local node under the
  inherited BaseNode #980, one BaseNode by name #981, and the persisted MoveMe
  line #982).
  set-parent inherited-node matrix #983-1051: AD #983-1006 (status #983,
  unchanged-file hash #984, baseline scene_tree #985, six rejected moves each
  bracketed by identical scene_tree pairs #986-1003, unchanged-file hash #1004,
  no node under Target #1005-1006); AE #1007-1017 (the no-override control);
  AF #1018-1034 (the two-level base chain); AG #1035-1043 (a local node under
  an inherited parent moves, is saved, and one copy is written under Target);
  AH #1044-1051 (the fresh reload; each inherited node is a single node by
  name).
  base-inherited rename and delete #1052-1128: AI #1052-1084 (status #1052,
  unchanged-file hash #1053, baseline scene_tree #1054, seven rejections each
  bracketed by identical scene_tree pairs with an equal before/after file hash
  #1055-1075, unchanged-file hash #1076, no renamed copy #1077-1078, the local
  rename to TargetX #1079, scene_tree #1080, unchanged-file hash #1081,
  save_scene #1082, the persisted TargetX row #1083 and the inherited BaseCold
  row #1084). AJ #1085-1113 (status #1085, unchanged-file hash #1086, baseline
  scene_tree #1087, six rejections bracketed by identical scene_tree pairs with
  an equal before/after file hash #1088-1105, unchanged-file hash #1106, no
  Target copy #1107, the local delete of Target #1108, scene_tree #1109,
  unchanged-file hash #1110, save_scene #1111, Target gone #1112 and the
  inherited BaseCold row untouched #1113). AK #1114-1128 (status #1114,
  scene_tree #1115, the renamed TargetX #1116, query_nodes proving one TargetX
  #1117 and one each of BaseCold #1118 and ChildCold #1119, the persisted
  TargetX row #1120, open_scene of the saved ib_delete.tscn #1121, scene_tree
  #1122, Target not found #1123, no Target #1124, one each of BaseCold #1125,
  ChildCold #1126 and NestedCold #1127, and Target gone #1128).
  eval #1129-1222: EVAL (rows 1129-1180 start with no edited scene; rows 1181-1182 open
  res://main.tscn; rows 1183-1222 run with it open and do not edit it):
  value returns int #1129, float #1130, string #1131, Vector2 #1132, Array #1133,
  Dictionary #1134 and null #1135 (typed JSON, the int kept as i:); print output
  #1136; compile error #1137 and runtime error #1138 (completed false, the error
  captured with function, line, message and type, the anonymous source path
  normalised); a snippet blocking past a small timeout #1139 (timeout_exceeded
  true); and the rejected requests #1140-1144 (wrong project, missing code,
  non-string code, zero timeout, and a wrong project paired with a non-string
  code where the project guard answers first); refused encodings #1145 (Object
  as completed false with type error), #1146 (2000-deep Array refused past the
  tagged depth) and #1147 (oversized value); repair rows #1148 (an Array of
  exactly the 100 tagged levels, returned exact), #1149 (rich output), #1150
  (spaced output), #1151 (explicit null timeout refused), #1152 (warning keeps
  value) and #1153 (push_error fails), #1154 (_init error fails), #1155-1156
  (Object instance freed, checked via editor meta), #1157 (a failing @tool
  static initializer: completed false, value null, one error) and #1158 (a clean
  call with errors and output empty), #1159 (_static_init calling push_error)
  and #1160 (clean call), #1161 (a preloaded @tool script with an error) and #1162
  (clean call), #1163 (Array at exactly 100 levels returned exact) and #1164
  (Array at 101 levels refused), #1165 (Dictionary at exactly 100 levels returned
  exact) and #1166 (Dictionary at 102 levels refused), #1167 (mixed shape at
  exactly 100 levels returned exact) and #1168 (mixed shape at 103 levels
  refused), #1169 (Vector2-ended shape at exactly 100 levels returned exact)
  and #1170 (Vector2-ended shape at 101 levels refused), #1171
  (packed-array-ended shape at exactly 100 levels returned exact) and #1172
  (packed-array-ended shape at 101 levels refused), #1173 (Transform3D-ended
  shape at exactly 100 levels returned exact) and #1174 (Transform3D-ended
  shape at 101 levels refused), #1175 (typed-Array[int]-ended shape at exactly
  100 levels returned exact) and #1176 (typed-Array[int]-ended shape at 101
  levels refused), #1177 (chain of 49 typed Arrays returned at 99 levels) and
  #1178 (chain of 50 typed Arrays refused at 101 levels), #1179 (a Node
  parented to a Node the snippet retains, parent child count 1) and #1180 (the
  next call still finds that one child), #1181 (scene switch clean) and #1182
  (switch with edit reports the change); display rows #1183 int, #1184 float,
  #1185 NaN, #1186 inf, #1187 bool, #1188 null, #1189 a String with a quote,
  #1190 one with a newline, #1191 one with non-ASCII text, #1192 a StringName,
  #1193 a NodePath, #1194 a Vector2, #1195 a Color, #1196 an untyped Array,
  #1197 a typed Array[int], #1198 a Dictionary, #1199 a nested Dictionary at 100
  levels, #1200 a PackedByteArray and #1201 a warning run, each returning its
  value and printing rt:true from str_to_var of its readable form (is_nan for
  the NaN row); #1202 a run that does not complete (display null); #1203 a
  PackedByteArray whose readable form is over the cap while its encoding is
  inside it, which keeps its value with display null and one added warning; and
  #1204-1222 the parse rows, each returning str_to_var of the display text the
  matching display row's reply carried.
  Not covered, with reason:
  - "request exceeds the maximum size" (>8 MiB without newline): reaching
    it needs an 8 MB write, and the reported byte count varies with TCP
    segmentation, so it cannot be byte-deterministic; framing is also
    untouched by a file-split refactor.
  - "class must be a built-in ... script class" (create_node, query_nodes):
    unreachable in this editor: ClassDB rejects the script class first,
    observed as "unknown class: QryScriptClass" (#29, #74).
  - list-resources refresh: the scanning reply and the moment the scan
    finishes depend on timing, so the refresh case cannot be
    byte-deterministic. It is proved instead by an isolated-editor probe over
    the real socket and by the CLI's loopback fake-plugin tests.
  - "the open scene has no file path yet": no command opens an unsaved
    scene, so this branch cannot trigger from outside.
  - "failed to save the scene": needs an IO failure while saving.
  - genuine imported scenes (.gltf, .glb): the editor's import is not
    deterministic enough to record byte-identically (a hand-written
    triangle .gltf was measured: it loads but the editor refuses it).
  - an absolute path through /tmp (symlink to /private/tmp on macOS):
    depends on the temp directory, so it cannot be a fixed request.
  - an untitled dirty scene: making one needs editor-side scripting
    (close all scenes, then set the edited scene), not a socket request. The
    instantiate-scene cycle check compares the edited scene path, which such a
    scene does not have; the missing-dependency walk still runs.
  - a parent node with no owner that is not the scene root: a scene file
    declares an owner for every node, and an unowned child can only be made by
    editor-side scripting. The rejection is proved by the shared parent helper
    that create-node and instantiate-scene call.
  - remove of a session (runtime) group or an engine-internal group such as
    _root_canvas<digits>: the socket cannot create a runtime group, and the
    engine-internal name carries a generated object id that is not
    byte-stable. The rejection of both is proved in the isolated undo probe
    instead (it adds a runtime group with add_to_group(name, false)).
  - live `%` name resolution (`get_node("%Name")`): it is engine lookup
    behavior inside a running scene, and this harness records the saved flag
    state instead.

NOTES
  The fixture project deliberately has no run/main_scene: otherwise the
  editor would auto-open main.tscn and the no-scene sessions would have a
  scene. The port patch is one substitution of "const PORT := 47821" in the
  copied plugin only. The harness leaves no Godot process and deletes the
  throwaway project every run. Files here: replay.py, compare.py,
  baseline.jsonl, baseline.sha256, README.txt.
  Undo and redo are not proved by this harness: it talks over the socket only
  and cannot trigger the editor's Undo action. As for the earlier commands,
  one-action Undo/Redo proof lives in the Code4Me task result, from a separate
  isolated-editor probe that sends a real set-parent (and, for the earlier
  commands, connect-signal) request through the plugin and then triggers the
  editor's Undo/Redo through the safe route.
  The baseline is only valid while the plugin's replies for these 1291 rows
  (1162 requests and 129 file checks)
  are supposed to stay the same. A change that adds or alters a command's
  behavior on purpose needs a new baseline, recorded from the last trusted
  commit, and a fresh determinism check (record three times, compare byte for
  byte). Negative control: copy the plugin, change one character in one error
  message, run the harness on the copy; compare.py must report a difference
  only on the requests that carry that string.
  The baseline plugin can be recreated with
  git show d5b8e0c:addon/godot_pipeline (whole directory).
