# ADR-0009: Dependency graph: storage, edge merging, traversal and the graph builder

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M3
- **Spec refs:** §2.2, §2.3, §6.3, §7, §10

## Context

§10 asks for a custom in-memory directed multigraph with bidirectional adjacency, iterative BFS
returning sorted results and shortest explanation paths, a duplicate-edge rule, and
deterministic serialization. It leaves open:
- how flags merge;
- which shortest path is reported when several exist;
- how M2 symbols map onto the §6.3 node vocabulary (which first had no `constant` kind);
- what happens to an edge whose target is not a node.

## Decision

### Storage (`rift.graph.multigraph`)

1. `DependencyGraph` keeps `node -> kind -> neighbour -> edge` in both directions, so there is
   at most one edge per `(source, kind, target)`. It imports only `rift.core`; the §4.1 layers
   contract enforces this.
2. `add_node` is idempotent for an equal node. A different node under the same ID raises
   `GraphError(graph_conflict)`.
3. `add_edge` raises `GraphError(unknown_node)` if either endpoint is missing. The caller records
   the edge as unresolved (the builder does, see 13). A raise cannot be silently ignored the way a
   `False` return can.
4. Every query returns sorted values, whatever the insertion order:
   - `nodes()` by ID;
   - `edges()` by `edge_sort_key`;
   - `successors` by `(target, kind)`, `predecessors` by `(source, kind)`.
5. `to_dict()` produces `{"format": "rift.graph", "version": 1, "nodes": [...], "edges": [...]}`,
   with nodes and edges in their sorted orders and encoded by the core codecs.
   - `from_dict()` is strict. It rejects unknown or missing keys, another format or version,
     duplicate nodes, duplicate `(source, kind, target)` edges and dangling edges, raising
     `SerializationError`.
   - This document is internal (it feeds the M5 cache), not part of the Change Proof protocol.

### Duplicate edges (`rift.graph.merge`)

6. Occurrences of one `(source, kind, target)` are grouped by their **restricting flags**
   (`type_checking_only`, `conditional`), which record *when* the edge holds.
   - The group with the **fewest restrictions** wins. A runtime import subsumes a
     `TYPE_CHECKING` import of the same module. With equally many restrictions, the sorted flag
     names decide: `conditional` beats `type_checking_only`, since a runtime edge is the broader
     dependency.
   - Inside the winning group:
     - `confidence` is the strongest;
     - `origin` comes from a strongest edge; on a tie, the one declared first in `EdgeOrigin`
       wins (`ast_static`, `name_resolution`, `heuristic`, `config_declared`, `plugin`);
     - provenance flags (`star_import`, `dynamic`: *how* the edge was found) are unioned;
     - `evidence_ids` are the sorted union.
   - Occurrences in losing groups are subsumed: their confidence, provenance and evidence
     describe another context.

   This never hides a runtime dependency behind `type_checking_only`, and never stores a
   confidence that no occurrence in the stored context has. Example: a `certain`
   `TYPE_CHECKING` import plus a `likely` `import_module("x")` is stored as `likely [dynamic]`,
   not `certain`. §10's "keep the strongest confidence" holds within a restriction context.
7. The rule is a lexicographic choice followed by a commutative, associative merge, so the stored
   edge never depends on insertion order.

### Traversal (`rift.graph.traversal`)

8. `forward_reachable` / `reverse_reachable(starts, *, kinds=None, min_confidence=unknown,
   max_depth=None)` is a level-by-level breadth-first search with a visited map, and every node
   is expanded at most once. There is no recursion anywhere in `rift.graph`. A static test checks
   for direct recursion, and the deep-chain tests run under a tightened recursion limit.
   - `starts` may be one node ID or many. An unknown start raises `GraphError(unknown_node)`.
   - `kinds=None` means every kind; an empty set follows no edges.
   - An edge qualifies if `confidence >= min_confidence`.
   - `max_depth` must be a non-negative int; `0` returns only the starts.
   - One validator serves the traversals and `successors`/`predecessors`. It rejects malformed
     filters with `GraphError(invalid_argument)`: a bare string or `EdgeKind` as `kinds` (it would
     iterate as characters), non-`EdgeKind` members, a non-`Confidence` floor, and a non-int,
     bool or negative depth. Nothing silently returns an empty result.
9. The result `Reachability` holds:
   - the sorted starts and the sorted reached nodes. Starts are included at depth 0, so the reach
     set is reflexive;
   - a parent link per node.

   `path(n)` and `path_edges(n)` rebuild a path on demand, so memory stays linear. A 100k-node
   chain would need about 5·10⁹ path entries if every path were stored.
10. **Which shortest path explains a node.** Among all shortest qualifying paths from any start:
    1. the path with the **fewest weak edges** wins. Paths are compared by
       `(number of unknown edges, number of possible edges, number of likely edges)`, so a path
       whose weakest link is stronger always wins, and explanations prefer confirmed evidence;
    2. a remaining tie goes to the **lexicographically smallest** path, compared as a sequence of
       node IDs from the start.

    Each hop is explained by the strongest qualifying edge between its two nodes, with ties going
    to the smaller kind value. The cost is additive, so the per-level BFS computes this exactly.
    Each node keeps its best cost and the lexicographic rank of its path within its level, and
    the next level is ranked by `(parent rank, node ID)`.

    A pure "maximize the weakest link, then lexicographic" rule was rejected: it has no optimal
    substructure, so a BFS cannot compute it exactly. Hypothesis checks the implemented rule
    against brute-force enumeration of every shortest path, including under `max_depth`.
11. In a forward result, `path_edges(n)[i].source == path[i]`. In a reverse result,
    `path_edges(n)[i].target == path[i]`.

### Building the graph from analysis (`rift.graph.builder`)

12. `build_graph(results)` produces `GraphBuild(graph, unresolved, dangling)` and is
    deterministic in any input order: results are processed by `file_path`, and two results for
    one file raise `graph_conflict`. Nodes:
    - one `file:` node per result, with `location=None`;
    - module, class, function and method symbols become nodes of the same kind;
    - `test_function` / `test_method` symbols become `test:<path>::<Class>::<name>` nodes, i.e.
      pytest node IDs without parameters, **only if pytest can collect them**: every enclosing
      class must be a `Test*` class, and there must be no `<locals>`. Otherwise the symbol stays a
      `py:` function or method node. (The analyzer marks a method as a test from its innermost
      class only.)
    - symbol nodes carry `attributes = {symbol_kind, visibility}`.
13. **Constants** are `constant` nodes (`py:<module>:<NAME>`, label = the name), so a change
    to a constant's line has a node of its own. Their users reach them through the analyzer's
    `references` edges: from the function that reads them, or from the module for a module-level
    use (`B = A + 1` gives `py:m -> py:m:A`). The user approved adding `NodeKind.constant` after
    M3, as a pre-protocol vocabulary change (SPEC §6.3; the protocol is first published in M6).

    The first version had no such kind and folded constants into their module node.
14. **Edges** keep their kind, origin, confidence, flags and evidence IDs; only the endpoints
    renamed in 12–13 change. An edge whose target is not a node is **never dropped**:
    - it is kept whole, unmerged, in `GraphBuild.dangling` (sorted by `edge_sort_key`), so a
      `TYPE_CHECKING` miss stays distinguishable from a runtime one;
    - it becomes an `UnresolvedReference` from the edge's source, with `raw` =
      `"<kind> <target ID>"`:
      - `missing_module` when the target's module has no node at all (for example, its file was
        not analyzed);
      - `unknown` otherwise.

      It is anchored at the source node's location, or at line 1 of the file for a `file:`
      source.
    - The analyzer's own unresolved references are carried over, with sources renamed the same
      way. All unresolved references are sorted by `unresolved_sort_key`.
15. The builder adds no edges of its own: no `tests` edges, and no file↔module or test↔module
    containment edges. Deciding which tests relate to a change is M4/M5 work.

### Amendment after the fresh-context review (same milestone)

The first version was reviewed before acceptance. Changes:
- **6** replaced "intersect restricting flags, keep the strongest confidence". That rule could
  store `certain` for a dependency whose only runtime evidence was `likely` (SPEC §2.2).
- **8** added argument validation. Malformed filters used to return silently empty results.
- **10** made explanations prefer strong edges. They used to follow node IDs only, even through
  a `possible` edge when a `certain` path of the same length existed.
- **12** requires every enclosing class to be a `Test*` class. Before, `Helper::TestInner::test_x`
  produced a test ID pytest cannot run.
- **13** dropped folded self-loops (moot since constants became nodes; see the next section).
- **14** added `dangling` and the kind in `raw`. Several dangling edges used to collapse into one
  record.

## Alternatives considered

- **Union of all flags**, the literal reading of "merge flags". Rejected: it can mark a runtime
  dependency `type_checking_only` (6).
- **Intersect restricting flags and keep the strongest confidence** (the first version).
  Rejected: it inflates confidence across contexts (6).
- **Keep parallel duplicate edges** instead of merging them. Rejected: §10 requires one edge per
  `(source, kind, target)`.
- **`add_edge` returning `False` for a dangling edge.** Rejected: callers could ignore it and
  drop the edge.
- **Store the full path per reached node.** Rejected: quadratic memory on deep chains.
- **Fold constants into their module node** (the first version). Superseded once
  `NodeKind.constant` was approved: folding is coarser and loses the constant's own span.
- **Drop edges to constants, or record them as unresolved.** Rejected: they are resolved, so
  either choice would misreport them.

## Consequences

Pinned by:
- `tests/unit/graph/test_merge.py`: the merge rule;
- `tests/unit/graph/test_multigraph.py`: API, filters, ties, strict decoding;
- `tests/unit/graph/test_builder.py`: real analyzer output;
- `tests/unit/graph/test_scale.py`: a 100k-node chain and a cycle-heavy graph under a tightened
  recursion limit, plus the static no-recursion guard;
- `tests/property/test_graph_properties.py`: transitivity, symmetry, round-trip, insertion
  order, the batch merge rule, monotonic filters, and the brute-force path check. Mutations of
  the cost, the cross-level ranking and the kind tie-break were each confirmed to fail it;
- `tests/contract/test_determinism.py`: PYTHONHASHSEED 0 vs 4242.

Known limitations:
- Test node IDs carry no parametrization and are split at the first `::` (ADR-0005).
- Merging keeps only the least-restricted context. A `certain` `TYPE_CHECKING` occurrence next
  to a `likely` runtime one is not visible as `certain` anywhere in the graph (6).
- The graph is mutable while it is being built. Consumers treat a finished graph as read-only by
  convention; nothing enforces it.
