
## In-app checks

The repository runs the app for checks through adapters in `{{config}}` (`inApp`). Port {{port}} and whatever
sign-in the app uses are the `{{lease}}` lease: use them only through `app-check`, which takes and releases the lease
for you. Never start your own server on that port.

1. `{{flow}} build <key>` builds from a clean HEAD and stamps the build with that commit. `app-check` refuses a build
   that isn't from HEAD.
2. For each check the plan asks of your story:

   `{{flow}} app-check <key> <name> --mode hold|fail|loaded --path <app path> [--hold <pattern>]... [--fail <pattern>]... [--for <selector>] [--wait <ms>] [--click <selector>]... [--eval <js>]... [--after-eval <js>]... [--after-for <selector>] [--baseline] [-- <adapter arguments>]`

   It waits for the lease, serves your build, runs the check, stops the server, releases the lease, and records the
   result in `{{work}}/review/<key>/app-checks/<name>.json`, and logs every run. It prints PASS or FAIL against these rules:
   - the app was ready, and the served build carries your HEAD's stamp;
   - no page errors, and no console error matching the repository's pattern. The only excuse is the same error in a
     same-named `--baseline` run that was a plain `loaded` load of the same path; a hold or fail run never excuses one;
   - at least one `--eval`, and every eval and after-eval returned exactly `true` (write `!!el && el.disabled === true`,
     not `el.disabled`);
   - `hold`: only `--hold`, each a pattern your story pins and each holding a request before the evals, at least one
     `--after-eval`, every eval run while the requests were held, and no `[role=alert]` appearing at any point
     (a request matching after the release, such as a re-fetch, is noted but not held);
   - `fail`: only `--fail`, each a pattern your story pins and each failing a request with an error status before the
     evals;
   - `loaded`: no `--hold` or `--fail`.
3. The hand-off needs the checks in your story's row of the table above, each passing and current (run at your head,
   or no shipped non-test file changed since). The check spec belongs to `app-check`'s own flags: anything after `--`
   goes to the adapter as is, and spec flags there are refused.
   To look things up in the app (record ids, say) without making evidence, add `--probe` to a plain `loaded` run (no
   `--hold` or `--fail`): it prints the eval values, records nothing in `app-checks/`, and needs no passing rule.
4. If the check reports the app wasn't ready because you're signed out, ask the lead; don't sign in yourself.
5. When a check can't pass for a reason outside your story (missing data, say), hand off with
   `--blocked '<check name or requirement label>=<reason>'`. The lead decides each one; nothing blocked is accepted
   without that.

**Video evidence.** A story whose row above lists `video` also needs a before/after clip at your final head:
`{{flow}} app-video <key> <name> --mode hold|fail --path <route> --hold|--fail '<pattern>' [--click ...] [--caption ...]`.
It records the same steps on the baseline build (before) and on your build (after) under the lease, and stitches them
side by side. Record the state your story changes: the loading state with `--mode hold`, the failure with `--mode fail`.
The hand-off is refused without a current clip, and the lead gathers every clip into the delivery's evidence page.
