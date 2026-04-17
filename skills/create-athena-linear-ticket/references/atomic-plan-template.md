# Atomic Plan Template

## Goal
Single sentence describing the desired end state.

## Task Checklist
- [ ] Task 1: concrete outcome with code area.
- [ ] Task 2: concrete outcome with code area.
- [ ] Task 3: concrete outcome with code area.

## Atomicity Checks
1. Can each task be implemented and merged independently?
2. Can each task be validated with targeted tests?
3. Is any task too broad and should be split?

## Dependency Map
1. Task A -> blocked by Task B (only when required).
2. All other tasks should be marked parallelizable.

## Labels Hints
1. Map tasks to package labels from touched paths.
2. Add domain labels (checkout, security, payments, etc.) where applicable.
