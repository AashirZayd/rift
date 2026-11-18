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
- how M2 symbols map onto the §6.3 node vocabulary, which has no `constant` kind;
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

6. Two edges with the same `(source, kind, target)` merge into one edge:
   - `confidence`: the strongest.
   - `origin`: the origin of a strongest edge. On a tie, the one declared first in `EdgeOrigin`
     (`ast_static`, `name_resolution`, `heuristic`, `config_declared`, `plugin`).
   - `evidence_ids`: the sorted union.
   - `flags`, by meaning:
     - provenance flags (`star_import`, `dynamic`) record how the edge was found and are
       **unioned**;
     - restricting flags (`type_checking_only`, `conditional`) record when the edge holds and are
       **intersected**.

     A runtime import plus a `TYPE_CHECKING` import of the same module is a runtime dependency.
     A plain union would label it `type_checking_only`, and a later consumer that discounts such
     edges would then hide real impact. Where the restrictions differ (`type_checking_only` vs
     `conditional`), the merged edge carries neither: it over-states the dependency, which is the
     conservative direction.
7. The rule is commutative and associative, so the stored edge never depends on insertion order.

### Traversal (`rift.graph.traversal`)

8. `forward_reachable` / `reverse_reachable(starts, *, kinds=None, min_confidence=unknown,
   max_depth=None)` is a breadth-first search over a deque with a visited map. There is no
   recursion anywhere in `rift.graph`; a static test checks this.
   - `starts` may be one node ID or many. An unknown start raises `GraphError(unknown_node)`.
   - `kinds=None` means every kind; an empty set follows no edges.
   - An edge qualifies if `confidence >= min_confidence`.
   - `max_depth` must be a non-negative int; `0` returns only the starts.
9. The result `Reachability` holds:
   - the sorted starts and the sorted reached nodes. Starts are included at depth 0, so the reach
     set is reflexive;
   - a parent link per node.

   `path(n)` and `path_edges(n)` rebuild a path on demand, so memory stays linear. A 100k-node
   chain would need about 5·10⁹ path entries if every path were stored.
10. **Tie-breaking.** Starts are queued in sorted order, and each node's neighbours are expanded
    in sorted order. The first node to discover a neighbour becomes its parent, so each reached
    node gets the **lexicographically smallest shortest path**, compared as a sequence of node IDs
    from the start.
    - When several qualifying edges of different kinds join the same pair, the hop is explained
      by the strongest edge, then by the smaller kind value.
    - Hypothesis checks this against brute-force enumeration of every shortest path.
11. In a forward result, `path_edges(n)[i].source == path[i]`. In a reverse result,
    `path_edges(n)[i].target == path[i]`.

### Building the graph from analysis (`rift.graph.builder`)

12. `build_graph(results)` produces `GraphBuild(graph, unresolved)` and is deterministic in any
    input order: results are processed by `file_path`, and two results for one file raise
    `graph_conflict`. Nodes:
    - one `file:` node per result, with `location=None`;
    - module, class, function and method symbols become nodes of the same kind;
    - `test_function` / `test_method` symbols become `test:<path>::<Class>::<name>` nodes, i.e.
      pytest node IDs without parameters. A test-kind symbol whose qualname contains `<locals>`
      cannot be collected by pytest, so it stays a `py:` function or method node;
    - symbol nodes carry `attributes = {symbol_kind, visibility}`.
13. **Constants** have no `NodeKind` in v0.1, and adding one would change the protocol
    vocabulary. A constant folds into its module node, and an edge to `py:m:CONST` becomes an edge
    to `py:m`.
    - This is coarser but conservative: every dependant of the constant is still a dependant of
      its module.
    - To revisit when the vocabularies are finalized in M6.
14. **Edges** keep their kind, origin, confidence, flags and evidence IDs; only the endpoints
    renamed in 12–13 change.
    - An edge whose target is not a node is **never dropped**. It becomes an
      `UnresolvedReference` from the edge's source, with the target ID as `raw`:
      - `missing_module` when the target's module has no node at all (for example, its file was
        not analyzed);
      - `unknown` otherwise.
    - It is anchored at the source node's location, or at line 1 of the file for a `file:`
      source.
    - The analyzer's own unresolved references are carried over, with sources renamed the same
      way. All unresolved references are sorted by `unresolved_sort_key`.
15. The builder adds no edges of its own: no `tests` edges, and no file↔module or test↔module
    containment edges. Deciding which tests relate to a change is M4/M5 work.

## Alternatives considered

- **Union of all flags**, the literal reading of "merge flags". Rejected: it can mark a runtime
  dependency `type_checking_only` (6).
- **Keep parallel duplicate edges** instead of merging them. Rejected: §10 requires one edge per
  `(source, kind, target)`.
- **`add_edge` returning `False` for a dangling edge.** Rejected: callers could ignore it and
  drop the edge.
- **Store the full path per reached node.** Rejected: quadratic memory on deep chains.
- **Add `NodeKind.constant`.** Deferred: it changes the protocol vocabulary and needs approval.
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
  order, monotonic filters, brute-force path check;
- `tests/contract/test_determinism.py`: PYTHONHASHSEED 0 vs 4242.

Known limitations:
- Constants are folded into module nodes (13).
- Test node IDs carry no parametrization and are split at the first `::` (ADR-0005).
- The graph is mutable while it is being built. Consumers treat a finished graph as read-only by
  convention; nothing enforces it.
