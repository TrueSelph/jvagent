# Offline migration cannot enter global graph mutation

Parent `9747a1d084f630dd225e04af93e13867b8175c55` introduced the offline planner.
CLI integration review then found that global purge/update handling ran before
subcommand dispatch. A migration invocation combined with `--purge --yes` could
therefore purge before the offline handler ran. This confirmed gap is closed
before claiming the CLI's offline guarantee.

[`main.py:174`](../../jvagent/cli/main.py#L174) now identifies the selected
subcommand while skipping known flag values, and rejects purge/update/source/
merge flags before their global processing. The existing value-flag set is
shared with app-root parsing. Other commands retain their existing global flags.
No execution engine, model policy, storage adapter or dependency changes here.

## Evidence

- [101 CLI tests on Python 3.11](2026-11-05-native-migration-flags-311.txt) and
  [Python 3.12](2026-11-05-native-migration-flags-312.txt) pass, including eight
  mutation-flag/order cases and existing parser/dispatch coverage.
- [Isolated guard removal](2026-11-05-native-migration-flags-mutation.txt) causes
  six regressions to fail. Two cases remain refused by older general validation;
  they are not counted as evidence that the new guard was reached. Mutation
  testing patches only the test process; purge/bootstrap/model entrypoints are
  mocked, and repository/runtime files are unchanged.
- [Four real CLI invocations](2026-11-05-native-migration-flags-cli.json) against
  the disposable migration graph, with explicit development mode and confirmed
  isolated DSN, all return exit 2 with the specific guard message. Purge flags
  before and after the command, source update and merge update are covered.
  All 15 graph nodes have an identical before/after digest; source YAML is
  byte-identical. No destructive operation or model request occurred.
- After those refusals a new actual GPT-4.1/MCP browser interaction succeeds:
  [visible result/tool/usage](2026-11-05-native-migration-flags-browser.json) and
  [fresh SQL receipt](2026-11-05-native-migration-flags-final-smoke.json).
  REHEARSAL-47 returns Cedar/14:30. Two provider requests, 747 reported tokens,
  USD 0.001764 estimated; SDK status delivered, closed/emitted Interaction and
  spending with no remaining reserves. HTTP resource 4.3073s; persisted duration
  4.193633s. The native execution source is unchanged from the parent rehearsal;
  the new main.py guard is qualified through separate CLI subprocesses. The
  long-running backend observation correctly retains its earlier CLI main hash.

The parent 133-case slices and 367-case real-PG native suites retain their
original evidence; they are not claimed as newly rerun for this CLI-only fix.
Final clean all-files/commit hooks, normal full pre-push and exact-head remote
CI remain separate publication gates. This closes the global-flag integration
gap only. Historical migration, reference-profile blockers, default cutover,
legacy removal and the other release gates remain open.
