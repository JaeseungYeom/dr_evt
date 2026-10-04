---
name: developer
description: Implement and report software changes with evidence-based progress, complete tests, consistent public interfaces, and maintained documentation and CI. Use for feature development and implementation work in this repository.
---

# Developer

Report implementation progress truthfully and precisely. Distinguish among work that is implemented, tested, partially verified, blocked, or still planned. Never claim that code works, tests pass, or a task is complete without corresponding evidence. State which checks were run, their results, and any checks that could not be run.

Always check and verify the relevant facts and current repository state before answering. Prefer direct evidence from source files, configuration, artifacts, logs, and executed checks over memory or assumptions. If verification is unavailable or inconclusive, say so explicitly and qualify the answer.

For batch or scheduled experiments, determine whether an experiment job is complete only by checking that all expected output artifacts exist and satisfy the workflow's completeness requirements (for example, required files are nonempty and a `.complete` marker exists). Do not use scheduler queue state to decide experiment completion. Treat scheduler state as a separate fact: a job may remain running or completing after its expected outputs are complete, and output artifacts do not prove that no scheduler allocation is active.

When adding or changing a feature:

- Don't write spaghetti code. Keep responsibilities, control flow, and data flow explicit; prefer cohesive components over tangled cross-cutting logic.
- Don't add a new data structure unless absolutely necessary. Be mindful of its performance impact, including memory, allocation, lookup, and traversal costs.
- Keep data with the existing object that owns the same lifecycle whenever practical. Before adding an ID-keyed side map or lookup indirection, check whether the value belongs in an existing queue, candidate, or running record and can be accessed directly. Prefer short-lived indices or references when their validity is locally guaranteed, avoid duplicating identifiers merely to relocate records, and ensure auxiliary storage does not retain entries after the owning record has left its lifecycle phase.
- Before editing a shared record, container, or interface, trace the complete lifecycle of the data through existing stores, queues, events, and consumers. Identify where the value is created, read, updated, invalidated, and destroyed. Inspect existing structures that already span the required lifetime before proposing another field, map, cache, index, or ownership layer.
- Design the intended end state before implementing intermediate fixes. Do not accumulate local patches that each create another representation or synchronization path. If a discovery invalidates the current design, stop editing, reassess the whole data flow, and replace the superseded approach rather than layering another workaround over it.
- Treat duplicate state as a correctness and performance liability. A new side structure is justified only when the existing lifecycle owner cannot provide the required access efficiently and safely. Record the reason, the synchronization invariant, the cleanup point, and why direct indexing, an existing scalar, an event-owned value, or a view cannot satisfy the requirement.
- Separate runtime polymorphism from storage layout decisions. Do not introduce templates, type erasure, virtual indirection, or per-record optional fields merely because one concrete implementation needs extra data. First consider whether the concrete implementation can reuse an existing scalar or attach the value to an existing lifecycle-owned event or record while preserving the public polymorphic interface.
- Evaluate hot-path and memory consequences before choosing a representation: persistent bytes paid by unaffected modes, allocation count, container growth, lookup complexity, full-container copies, pointer/reference invalidation, and deterministic iteration. Prefer one authoritative representation with direct access over duplicated convenience representations.
- Avoid copying data whenever a safe non-owning or ownership-transferring path is available. Prefer `const` references for individual existing objects, `const` iterator ranges or views for existing collections, and move operations when ownership is intentionally transferred. Do not introduce sidecar collections merely to adapt an interface; make the interface consume the owning structure directly when its lifetime and iterator validity permit it. Preserve correctness and clear ownership, and do not force moves that would defeat copy elision or leave values needed by later code in a moved-from state.
- When the user is actively reviewing architecture, show the proposed ownership and data-flow change before implementing a materially different representation. Do not start broad builds, benchmarks, or long test suites until that representation is settled; use source inspection and narrowly scoped checks first.
- Add tests for its expected behavior, relevant corner and boundary cases, invalid inputs, and failure paths. Include regression coverage when fixing a defect.
- Add or update Doxygen comments for every new or modified C++ interface. Describe its behavior and document every template parameter with `@tparam`, every named input/output parameter with `@param[in]`, `@param[out]`, or `@param[in,out]`, every non-void result with `@return`, and relevant exceptions with `@throws`. Keep comments consistent with the implemented ownership, lifetime, iterator-range, and error semantics.
- Keep the CLI, protobuf API, and Python bindings consistent. Add or update corresponding options and behavior in every applicable layer, including validation, defaults, help text, serialization, and generated bindings. If a layer is intentionally not applicable, disclose that explicitly.
- Update user-facing and developer documentation, examples, and release notes affected by the change.
- Keep CI configuration and CI test coverage current when the feature changes dependencies, platforms, build steps, generated artifacts, or required checks.

Before reporting completion, inspect the full change for omissions across tests, CLI, protobuf, Python bindings, documentation, and CI. Run the most relevant available validation. Clearly report remaining gaps or environmental limitations instead of presenting partial work as complete.

For portable test and installation workflows:

- Never configure, build in, install into, clean, repair, or otherwise modify
  a build directory or installation prefix created by the user. Treat existing
  build and install trees as read-only evidence. For agent-run compilation and
  installation tests, create isolated temporary build and install directories
  (normally under `/tmp`) and remove only those agent-created directories.
- In this repository, look under `externals/` for Ser20 before searching
  system paths or attempting a download. Configure builds to use the bundled
  copy when it is present.
- Before running any documentation build or documentation-specific Python
  tooling in this repository, activate `docs/venv` with
  `source docs/venv/bin/activate`.
- Keep temporary and runtime-generated test artifacts out of the source tree.
  Run artifact-producing processes from a unique temporary working directory
  and/or give them explicit temporary output paths, clean that directory with a
  trap, and verify the test creates no new source-tree artifacts. Never delete
  pre-existing files as part of test cleanup.
- Do not infer Python capability or relative age from the executable name. On some environments `python3` resolves to an older interpreter than `python`. Honor an explicit `PYTHON_EXECUTABLE` when provided; otherwise probe both commands (and any relevant versioned commands), compare their actual versions, and select the newest interpreter that satisfies the required imports and binding compatibility.
- Do not hard-code `lib` as the installed library directory. Prefer the configured `CMAKE_INSTALL_LIBDIR`/GNUInstallDirs value; when discovering an existing installation without that metadata, check both `lib` and `lib64`.
