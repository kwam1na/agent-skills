# Atomic Plan Template

## Goal
Single sentence describing the desired end state.

## Task Checklist
- [ ] Task 1: concrete outcome with code area. Posture: `test-first` / `characterization-first` / `sensor-only`.
- [ ] Task 2: concrete outcome with code area. Posture: `test-first` / `characterization-first` / `sensor-only`.
- [ ] Task 3: concrete outcome with code area. Posture: `test-first` / `characterization-first` / `sensor-only`.

## Expected Sensors
1. Targeted tests or characterization tests.
2. Broader suite, typecheck, build, lint, harness/review command, runtime scenario, or CI-equivalent check.
3. Documented repair command for deterministic drift, if known.

## Compounding Opportunities
1. Reusable learning, missing repo sensor, reviewer gap, or skill update likely to emerge.
2. `None expected` when the work is unlikely to teach the system anything durable.

## Atomicity Checks
1. Can each task be implemented and merged independently?
2. Can each task be validated with targeted tests?
3. Is any task too broad and should be split?

## Dependency Map
1. Task A -> blocked by Task B (only when required).
2. All other tasks should be marked parallelizable.

## Labels Hints
1. Map tasks to labels from the touched code areas when the project already uses them.
2. Add domain labels only when they are explicit or clearly implied by the work.
