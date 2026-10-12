Golden replay harness for the Godot Pipeline editor plugin (764 rows:
1-148 unchanged, 149-200 cover inspect-class and open-scene, 201-262 cover
connect-signal, 263-362 cover set-group, 363-413 cover set-unique-name,
414-477 cover the rename unique-flag fix, 478-530 cover the rename option C
cases, 531-619 cover the inherited-rename rejection, 620-630 cover the fresh
reloads of the accepted inherited-rename cases, 631-636 cover the
instantiate-scene no-scene guards, 637-719 cover instantiate-scene accepted and
rejected cases, 720-729 cover the fresh reloads of the accepted instances, and
730-764 cover list-resources).

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

  Each run takes about two minutes: one --import plus twenty-two editor launches
  (in launch order A with no scene, E with no scene, G with no scene, J with no
  scene, T with no scene, W with no scene, C with no scene, B with res://main.tscn,
  D and F with res://connect.tscn, H and I with res://group.tscn, K and L with
  res://unique.tscn, N, O, P and Q with the rename_*.tscn fixtures, R with
  res://inherit_i3.tscn, S with res://inherit_i1.tscn, and U and V with
  res://inst_base.tscn), 764
  requests. The plugin has sixteen commands, a different count. Rows are
  stored A, B, C, E, D, F, G, H, I, J, K, L, N, O, P, Q, R, S, T, U, V, W: a fresh
  editor restores the previous session's open scenes, so the no-scene sessions
  (A, E, G, J, T, W, and C before it opens anything) must run before any scene
  session. Appending the new sessions keeps rows 1-729 byte-identical.

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

NORMALISATION
  The only one: the throwaway project's absolute path is replaced with
  <PROJECT> in stored requests and replies. Replies that carry it are the
  "project path mismatch" errors (10, 46, 54, 61, 70, 131, 141, 146, 158,
  159, 203, 244, 265, 323, 365, 400, 606, 633, 693, 696, 763). Reply text is
  otherwise verbatim; no sorting, no rounding.

DETERMINISM PROOF
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
  baseline.sha256 holds the sha256 of the whole baseline.jsonl: check with
  shasum -a 256 baseline.jsonl.
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
  isolated-editor probe that sends a real connect-signal request through the
  plugin and then triggers the editor's Undo/Redo through the safe route.
  The baseline is only valid while the plugin's replies for these 764 requests
  are supposed to stay the same. A change that adds or alters a command's
  behavior on purpose needs a new baseline, recorded from the last trusted
  commit, and a fresh determinism check (record three times, compare byte for
  byte). Negative control: copy the plugin, change one character in one error
  message, run the harness on the copy; compare.py must report a difference
  only on the requests that carry that string.
  The baseline plugin can be recreated with
  git show d5b8e0c:addon/godot_pipeline (whole directory).
