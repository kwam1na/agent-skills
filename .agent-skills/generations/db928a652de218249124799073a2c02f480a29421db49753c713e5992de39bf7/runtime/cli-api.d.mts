import * as _agent_delivery_harness_kernel from './kernel.mjs';
import { HarnessConfig, CompiledAdopterPolicyBinding, EnvSnapshot, CaptureCandidate, CapturedCandidate, ReviewActivationProjection, ArtifactsPort, WaiverPrompt, LiveProviderResult, ProviderRailInvocationResult, ExecutionContext, RunPreparationObservation, Blocker, ProviderRailSession, NonEmptyTuple, createBlocker, RunJournalEvaluation, RunJournalRequiredEntry, RunJournalExplanation, RunArtifactMetadata, RunEvent, ScopedCheckAttempt } from './kernel.mjs';

declare const EXIT_OK = 0;
declare const EXIT_POLICY = 1;
declare const EXIT_USAGE = 2;
declare const EXIT_INTERRUPTED = 130;
/**
 * Thrown when the operator interrupts the process (SIGINT), typically at an
 * interactive prompt. The boundary maps it to exit 130 — distinct from a policy
 * block, because an interrupted run reached no verdict.
 */
declare class CliInterruption extends Error {
    constructor(message?: string);
}
/**
 * What a command returns. `ok` is a pass; `blocked` is a policy failure carrying
 * the typed blockers to render; `usage` is an argument or invocation error.
 */
type CommandResult = {
    readonly kind: "ok";
    readonly summary?: string;
    readonly digest?: string;
    readonly preparation?: RunPreparationObservation;
    /** The command did not do its work, so no completion of it goes in the run's journal. */
    readonly unobserved?: true;
} | {
    readonly kind: "blocked";
    readonly blockers: readonly Blocker[];
} | {
    readonly kind: "usage";
    readonly message: string;
};
/** The repository wiring shared by every command in one invocation. */
interface RepoWiring {
    readonly rootDir: string;
    readonly workspaceId: string;
    readonly captureCandidate: CaptureCandidate;
    readonly projectActivation: (candidate: CapturedCandidate) => Promise<ReviewActivationProjection>;
    /** Storage options threaded to every kernel call so all share one namespace. */
    readonly storageOptions: {
        readonly storageNamespace: string;
    };
}
/** Everything a command may reach, none of it the ambient process. */
interface CommandContext {
    readonly rootDir: string;
    readonly config: HarnessConfig;
    /** Optional adopter binding; managed commands also load the persisted binding after registration. */
    readonly policyBinding?: CompiledAdopterPolicyBinding;
    readonly env: EnvSnapshot;
    readonly stdinIsTTY: boolean;
    readonly stdoutIsTTY: boolean;
    /** Positional and flag arguments after the command name. */
    readonly args: readonly string[];
    readonly readStdin?: () => Promise<string>;
    readonly signal?: AbortSignal;
    /**
     * Wires capture and the store from this repo, memoized. Lazy so `--help`
     * wires nothing and so a command owns how it renders a store that will not
     * resolve. Rejects with a `BlockedError` the boundary maps to exit 1.
     */
    wire(): Promise<RepoWiring>;
    readonly artifacts: ArtifactsPort;
    /** Present only when the run can ask a human; the boundary gates it on a TTY. */
    readonly promptForWaiver?: WaiverPrompt;
    readonly liveResults?: readonly LiveProviderResult[];
    /** Runs one configured provider through the neutral stdio rail, if it has a command. */
    readonly invokeProvider?: (input: {
        readonly providerId: string;
        readonly payload: Readonly<Record<string, unknown>>;
        readonly requiresEvidence: boolean;
    }) => Promise<ProviderRailInvocationResult | undefined>;
    /** Emits one line of operator-facing output to stdout. */
    readonly write: (text: string) => void;
    /** Emits one line of operator-facing output to stderr, leaving stdout as it is. */
    readonly writeError: (text: string) => void;
    /** Classifies the execution context from this invocation's env + TTY. */
    classifyContext(): ExecutionContext;
}
interface CommandDescriptor {
    readonly name: string;
    /** Opt in to progress from this command's fresh captures. Other descriptors
     * retain the boundary's eager capture; this flag grants no candidate reuse. */
    readonly capturesCandidate?: boolean;
    /** The blocker `source.id` this command stamps on failures it raises itself. */
    readonly sourceId: string;
    readonly summary: string;
    /**
     * What a lone `--help`/`-h` on this command is answered with, printed by the
     * boundary before anything is loaded. It is the command's own text — its
     * invocation form and, where the command has one, the sentence explaining
     * what its flags decide. Most commands build their own usage errors from it
     * too, but nothing enforces that: `emit-review-evidence`, `maintain`,
     * `managed` and `submit-evidence`'s missing-manifest arm phrase theirs from
     * separate literals, so this member is the help answer and not a
     * single-authority claim over every usage message.
     */
    readonly usage: string;
    run(context: CommandContext): Promise<CommandResult>;
}
/**
 * What a config-free command may reach. Deliberately much less than
 * {@link CommandContext}: no config, no wiring, no provider rail, no artifacts
 * port. A run event belongs to the repository, not to a configured gate, and
 * `emit` has to work in a repository that has no `harness.config.ts` at all.
 *
 * The ONE thing a config-free command may do with `harness.config.ts` is ask
 * whether the path exists (`lstat`, no follow) at a worktree root. It never
 * imports it, loads it, or parses it — which is why the config loader is not
 * reachable from here even as a seam.
 */
interface ConfigFreeCommandContext {
    readonly rootDir: string;
    readonly env: EnvSnapshot;
    /** Whether omitted payload input would read from an interactive terminal. */
    readonly stdinIsTTY?: boolean;
    /** Positional and flag arguments after the command name. */
    readonly args: readonly string[];
    /** The payload channel: everything on stdin, when a command reads one. */
    readStdin(): Promise<string>;
    /** Emits one line of operator-facing output to stdout. */
    readonly write: (text: string) => void;
    /**
     * The invocation's cancellation, for the one config-free command that does
     * not return on its own. `runs serve` holds a socket open until the operator
     * ends it; without a signal it serves forever, which is exactly right for a
     * terminal and useless for a caller that has to get its process back.
     */
    readonly signal?: AbortSignal;
}
interface ConfigFreeCommandDescriptor {
    readonly name: string;
    readonly sourceId: string;
    readonly summary: string;
    /** As {@link CommandDescriptor.usage}: one help contract across both classes. */
    readonly usage: string;
    /** The discriminator the boundary dispatches on, before any config load. */
    readonly configFree: true;
    run(context: ConfigFreeCommandContext): Promise<CommandResult>;
}
/** Either command class; the registry holds both. */
type AnyCommandDescriptor = CommandDescriptor | ConfigFreeCommandDescriptor;
declare function isConfigFreeCommand(descriptor: AnyCommandDescriptor): descriptor is ConfigFreeCommandDescriptor;
interface CliRuntime {
    readonly cwd: string;
    readonly env: EnvSnapshot;
    readonly stdinIsTTY: boolean;
    readonly stdoutIsTTY: boolean;
    readonly stdout: (text: string) => void;
    readonly stderr: (text: string) => void;
    /** Loads the consumer config. Defaults to importing `harness.config.ts`. */
    readonly loadConfig?: (rootDir: string) => Promise<HarnessConfig>;
    /** An embedding adopter may pass its already-compiled policy directly. */
    readonly policyBinding?: CompiledAdopterPolicyBinding;
    /** The interactive waiver prompt. Only ever offered under a TTY. */
    readonly promptForWaiver?: WaiverPrompt;
    /** The filesystem port. Defaults to one rooted in the system temp directory. */
    readonly artifacts?: ArtifactsPort;
    readonly liveResults?: readonly LiveProviderResult[];
    readonly signal?: AbortSignal;
    /** Reads the whole of stdin, for the one command whose payload arrives there. */
    readonly readStdin?: () => Promise<string>;
    /** Test/embedding seam. The ordinary runtime opens the provider's configured command. */
    readonly openProviderRail?: (input: {
        readonly providerId: string;
        readonly command: readonly [string, ...string[]];
        readonly cwd: string;
        readonly env: EnvSnapshot;
    }) => Promise<ProviderRailSession>;
}
declare function commandBlocker(input: {
    readonly code: string;
    readonly sourceId: string;
    readonly summary: string;
    readonly details?: string;
    readonly remediations: NonEmptyTuple<Parameters<typeof createBlocker>[0]["remediations"][number]>;
}): Blocker;
/**
 * The default config loader: import `harness.config.ts` from the repo root and
 * take its default export, which `defineHarnessConfig` has already validated. A
 * config that fails to load — absent, unparseable, or invalid — becomes one
 * typed blocker rather than an unhandled throw.
 */
declare function importHarnessConfig(rootDir: string): Promise<HarnessConfig>;
/**
 * Wires capture and the evidence store from one `rootDir`. The store's
 * `workspaceId` is what the capture stamps onto the candidate, so the two can
 * never disagree — the coherence the admission adapter guards is guaranteed at
 * the source here.
 */
declare function wireRepo(rootDir: string, config: HarnessConfig): Promise<RepoWiring>;
/**
 * THE WRAPPED COMMANDS, NAMED ONE BY ONE.
 *
 * Exactly the candidate-facing loop plus its preflight. `managed` and
 * `maintain` are deliberately absent: they are host-facing and
 * installation-scoped, and a completion event for them would describe
 * something that is not a step of the delivery run the journal is about.
 * `emit` and `runs` are absent because a viewer that recorded its own
 * invocations would fill the journal it renders.
 *
 * An allowlist rather than a denylist: a command added to the registry is
 * unwrapped until someone decides it belongs here, which is the direction that
 * fails safe for a store nothing authoritative may read.
 */
declare const COMPLETION_WRAPPED_COMMANDS: readonly string[];
/**
 * Runs one CLI invocation to an exit code. Total: it maps every command result
 * and every throw to one of the four codes, and renders every failure through
 * the neutralizing blocker renderer. Never throws.
 */
declare function runCliBoundary(argv: readonly string[], commands: readonly AnyCommandDescriptor[], runtime: CliRuntime): Promise<number>;

/**
 * The gate time a journal can actually account for.
 *
 * `counted` is how many gate completions were journaled and `totalMs` their
 * sum; `unseen` says the journal carries none at all. That last one is the
 * honest answer to the thing this ticket was written for: a delivery whose gate
 * ran outside the CLI wrapper and emitted no `gate.reported` has a tail that
 * looks free, and a zero printed with no label is indistinguishable from a gate
 * that took no time. `unreadable` counts a gate completion whose `durationMs`
 * could not be read as a number, so a partial sum is never passed off as whole.
 *
 * EVERY journaled gate is summed, including one superseded by a re-run. The
 * headline outcome takes the last completion (a re-run supersedes its verdict),
 * but the time is time: a delivery that gated twice spent both.
 */
interface RunGateTime {
    readonly totalMs: number;
    readonly counted: number;
    readonly unreadable: number;
    readonly unseen: boolean;
}
/**
 * The run's three phases, in seconds, plus what they were spent on.
 *
 * The boundaries are the journal's own events, so the three spans tile the
 * run's whole span and sum back to `durationSeconds` within per-phase rounding:
 *
 * - implementation: the first event to the first round event;
 * - review: that first round event to the last `review.round.closed`, which
 *   deliberately INCLUDES the fix time between rounds — an operator asking what
 *   review cost is asking what the loop cost, not what the reviewers were busy;
 * - tail: the last closed round to the last event, which is `run.ended` on an
 *   ended run and whatever the run has reached on an open one.
 *
 * A run with no round event has no review and no tail: implementation is the
 * whole span, which is what a delivery that never opened a round actually did.
 */
interface RunPhases {
    readonly implementationSeconds: number;
    readonly reviewSeconds: number;
    readonly tailSeconds: number;
    /** Logical rounds the journal mentions, opened or closed. */
    readonly rounds: number;
    readonly gate: RunGateTime;
}
interface Readout {
    readonly status: RunJournalEvaluation["status"];
    readonly present: readonly RunJournalRequiredEntry[];
    readonly missing: readonly RunJournalRequiredEntry[];
    readonly violations: readonly string[];
    /**
     * One per violation above, in the same order. The readout evaluates unbound
     * to any record, so these explain the journal's own ordering only; the
     * record-bound explanations are `verify`'s.
     */
    readonly explanations?: readonly RunJournalExplanation[];
    /** The config-presence note, present on exactly the status that it explains. */
    readonly note?: string;
}
/** One command outcome and the role of whoever wrote it down. */
interface WrittenOutcome {
    readonly outcome: string;
    readonly writer: "cli" | "executor";
}
interface RunSummary {
    /**
     * The run's PRIMARY ticket, by the family's own rule: the first the journal
     * names. A run that read two — a dogfood item and the ordinary item it
     * delivered — is named here by the first, and every entry that bound no
     * ticket of its own belongs to it.
     */
    readonly ticket: string;
    /** A run with no `run.ended` is open, whatever else it holds. */
    readonly open: boolean;
    readonly startedAt: string;
    readonly lastAt: string;
    /**
     * The journal's own span, first event to last, in whole seconds.
     *
     * NOT "now minus the start". The viewer renders a file, and a file's span is
     * a property of the file: two operators refreshing the page a minute apart
     * must be told the same thing about the same run, and a run whose journal
     * ends is not still accruing duration. `at` is second-granularity, so this
     * is too.
     */
    readonly durationSeconds: number;
    /**
     * The same span, divided into the three phases a delivery is actually made
     * of. Derived from the events beside it, never carried by a journal: an
     * archive written before this member existed is projected with it, and one
     * carrying invented phases is re-derived over its own events.
     */
    readonly phases: RunPhases;
    readonly roundsOpened: number;
    readonly roundsClosed: number;
    readonly findings: {
        readonly P0: number;
        readonly P1: number;
        readonly P2: number;
        readonly P3: number;
    };
    /**
     * The gate outcome and who claimed it: the CLI-written `command.completed`
     * for `gate` when there is one, the executor's `gate.reported` otherwise.
     * The writer is the point of the label — an adopter whose gate is not a
     * product command has only the executor's word for it.
     */
    readonly gate?: WrittenOutcome;
    readonly record?: WrittenOutcome;
    /** `run.ended`'s result, when the run has ended. */
    readonly result?: string;
}

/** Bounded observational attachment transport; no path is followed by this parser. */

interface ArchivedAttachment {
    readonly metadata: RunArtifactMetadata;
    readonly availability: "retained" | "unavailable";
    readonly reason?: string;
    readonly code?: "missing" | "access_refused";
}
interface RunAttachments {
    readonly entries: readonly ArchivedAttachment[];
    readonly blobs: Readonly<Record<string, string>>;
}

declare function buildRunExport(input: {
    readonly runId: string;
    readonly events: readonly RunEvent[];
    readonly rootDir?: string;
    readonly refusedAppends?: readonly unknown[];
}): {
    progress?: {
        currentFindings: _agent_delivery_harness_kernel.RunReferencedObservation[];
        findingsCoverage: "unreported" | "reported";
        activities: _agent_delivery_harness_kernel.RunActivityObservation[];
        waits: _agent_delivery_harness_kernel.RunWaitObservation[];
        findings: _agent_delivery_harness_kernel.RunReferencedObservation[];
        reports: _agent_delivery_harness_kernel.RunReferencedObservation[];
        artifacts: _agent_delivery_harness_kernel.RunReferencedObservation[];
        finishSteps: _agent_delivery_harness_kernel.RunReferencedObservation[];
        asOf: string;
    } | undefined;
    spec: "delivery-run-export/2" | "delivery-run-export/1";
    labels: string;
    runId: string;
    events: readonly RunEvent[];
    summary: RunSummary;
    costs: {
        review: {
            coverage: string;
            unreportedEntries: number;
            totals: {
                unit: string;
                reportedBy: string;
                total: number | null;
            }[];
        };
        run: unknown;
    };
    readout: Readout;
    refusedAppends: readonly unknown[];
};
type DeliveryRunExport = ReturnType<typeof buildRunExport> & {
    readonly attachments?: RunAttachments;
};
type RunExportParseResult = {
    readonly ok: true;
    readonly value: DeliveryRunExport;
} | {
    readonly ok: false;
    readonly code: "run_export_invalid";
};
/** Validate observations and recompute totals; this never establishes admission. */
declare function parseRunExport(text: string): RunExportParseResult;

declare const prepareCommand: CommandDescriptor;

declare const admitCommand: CommandDescriptor;

declare const reviewContextCommand: CommandDescriptor;

declare const emitReviewEvidenceCommand: CommandDescriptor;

declare const submitEvidenceCommand: CommandDescriptor;

/**
 * `gate` — evaluate the delivery gate and, under a TTY, offer a scoped waiver.
 *
 * The command classifies the execution context from this invocation's env and
 * TTY, then runs the admission adapter. The waiver prompt is the one piece of
 * interactive I/O the CLI owns: it is handed to admission only when the boundary
 * saw a real TTY (the boundary already gated `context.promptForWaiver` on that),
 * so a non-interactive run can never be prompted — it blocks. Admission itself
 * only ever offers a waiver to a `human` context, all-or-nothing over waivable
 * findings; the CLI adds no waiver logic of its own.
 */

declare const gateCommand: CommandDescriptor;

declare const recordCommand: CommandDescriptor;

declare const verifyCommand: CommandDescriptor;

declare const checkCommand: CommandDescriptor;

declare const managedCommand: CommandDescriptor;

declare const maintainCommand: CommandDescriptor;

declare const emitCommand: ConfigFreeCommandDescriptor;

declare const runsCommand: ConfigFreeCommandDescriptor;

declare const saveContextCommand: CommandDescriptor;

declare const resumeCommand: CommandDescriptor;

interface ScopedCheckObservations {
    readonly version: "scoped-check-observations/1";
    readonly providers: readonly {
        readonly providerId: string;
        readonly attempts: readonly (ScopedCheckAttempt & {
            readonly durationMs?: number;
        })[];
    }[];
}
/**
 * Read retained attempts for the supplied scoped providers, in configuration
 * order and ascending generation order. This creates no directories or evidence.
 * Providers removed from this supplied configuration are not inventoried.
 * Absent history is empty; corrupt selected history throws. Durations are reported only
 * when recorded by the executor. These observations make no statement about
 * reuse, current applicability or admission; use the gate/verify for those.
 */
declare function readScopedCheckObservations(input: {
    readonly rootDir: string;
    readonly config: Pick<HarnessConfig, "gateId" | "storageNamespace" | "providers">;
}): Promise<ScopedCheckObservations>;

declare const phases: readonly ["snapshot-setup", "pre-command-verification", "command", "post-command-verification", "output-capture", "complete"];
declare const failureCodes: readonly ["check_snapshot_interrupted", "check_snapshot_timeout", "check_snapshot_unavailable", "check_snapshot_escape", "check_snapshot_drift", "check_snapshot_cleanup_failed", "check_dependency_source_overlap", "check_dependency_failed", "check_command_failed", "check_output_missing", "check_attempt_superseded", "check_artifact_unavailable", "check_evidence_rejected"];
declare const executionCodes: readonly ["ENOENT", "EACCES", "EPERM", "ABORT_ERR", "ERR_CHILD_PROCESS_STDIO_MAXBUFFER", "SIGKILL", "SIGTERM", "execution_failed"];
type ScopedDiagnosticPhase = typeof phases[number];
type ScopedDiagnosticFailureCode = typeof failureCodes[number];
type ScopedDiagnosticExecutionCode = typeof executionCodes[number];
type ScopedDiagnosticCommand = {
    readonly exitCode: number | null;
    readonly outputTail: string;
    readonly truncated: boolean;
} | {
    readonly unavailable: "not-started" | "not-completed";
};
interface ScopedDependencyDiagnostic {
    readonly durationMs: number;
    readonly executionErrorCode?: ScopedDiagnosticExecutionCode;
    readonly command: ScopedDiagnosticCommand;
}
interface RecordedScopedAttemptDiagnostic {
    readonly availability: "available";
    readonly phase: ScopedDiagnosticPhase;
    readonly failure: {
        readonly code: ScopedDiagnosticFailureCode;
        readonly executionErrorCode?: ScopedDiagnosticExecutionCode;
    } | {
        readonly unavailable: "not-failed" | "unclassified";
    };
    readonly command: ScopedDiagnosticCommand;
    /** Present only when this attempt actually ran dependency setup; never borrowed from a reused snapshot. */
    readonly dependency?: ScopedDependencyDiagnostic;
}
type ScopedAttemptDiagnostic = RecordedScopedAttemptDiagnostic | {
    readonly availability: "unavailable";
    readonly reason: "legacy" | "running";
};
interface ScopedCheckDiagnostics {
    readonly version: "scoped-check-diagnostics/1";
    readonly providers: readonly {
        readonly providerId: string;
        readonly attempts: readonly (ScopedCheckAttempt & {
            readonly durationMs?: number;
            readonly diagnostic: ScopedAttemptDiagnostic;
        })[];
    }[];
    readonly unavailableAttemptIds: readonly string[];
}
/** Read at most 100 explicit attempts. No logs from legacy payloads, no writes,
 * and no claim of applicability/admission. Unselected provider history is ignored. */
declare function readScopedCheckDiagnostics(input: {
    readonly rootDir: string;
    readonly config: Pick<HarnessConfig, "gateId" | "storageNamespace" | "providers">;
    readonly attemptIds: readonly string[];
}): Promise<ScopedCheckDiagnostics>;

declare const PACKAGE_NAME = "@agent-delivery-harness/cli";

/**
 * The command registry. The order here is the order `--help` lists them, and it
 * follows the loop an operator walks: prepare, review, submit, gate, record,
 * verify — with `check` as the standalone preflight and `managed` as the
 * host-facing managed-delivery checkpoint surface and `maintain` as the
 * installation-scoped maintenance lane.
 *
 * `emit` and `runs` come last because they are a different class: config-free
 * commands, dispatched before `harness.config.ts` is loaded, that read and
 * write the run store rather than anything a delivery decision depends on.
 */
declare const COMMANDS: readonly AnyCommandDescriptor[];

/** Runs the CLI against a runtime and returns the process exit code. */
declare function runCli(argv: readonly string[], runtime: CliRuntime): Promise<number>;

export { COMMANDS, COMPLETION_WRAPPED_COMMANDS, CliInterruption, EXIT_INTERRUPTED, EXIT_OK, EXIT_POLICY, EXIT_USAGE, PACKAGE_NAME, admitCommand, buildRunExport, checkCommand, commandBlocker, emitCommand, emitReviewEvidenceCommand, gateCommand, importHarnessConfig, isConfigFreeCommand, maintainCommand, managedCommand, parseRunExport, prepareCommand, readScopedCheckDiagnostics, readScopedCheckObservations, recordCommand, resumeCommand, reviewContextCommand, runCli, runCliBoundary, runsCommand, saveContextCommand, submitEvidenceCommand, verifyCommand, wireRepo };
export type { AnyCommandDescriptor, CliRuntime, CommandContext, CommandDescriptor, CommandResult, ConfigFreeCommandContext, ConfigFreeCommandDescriptor, DeliveryRunExport, RecordedScopedAttemptDiagnostic, RepoWiring, RunExportParseResult, ScopedAttemptDiagnostic, ScopedCheckDiagnostics, ScopedCheckObservations, ScopedDependencyDiagnostic, ScopedDiagnosticCommand, ScopedDiagnosticExecutionCode, ScopedDiagnosticFailureCode, ScopedDiagnosticPhase };
