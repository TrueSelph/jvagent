# Callback HTTP transport qualification

## Confirmed defects and changes

The callback helper passed extensions to AsyncClient.send(), which does not accept
that argument. Real HTTPX/MockTransport regressions fail before a request reaches
the transport. Extensions now belong to build_request(); send explicitly refuses
redirects. The validated IP remains the connection target, with original Host and
HTTPS server name. This follows the public [HTTPX request API](https://www.python-httpx.org/api/)
and [HTTPCore server-name extension](https://www.encode.io/httpcore/extensions/).
Source: `jvagent/core/callback.py:143`.

DNS validation runs once through asyncio.to_thread rather than blocking the event
loop. Existing address/scheme checks remain; IP annotations use public concrete
types and non-string resolver entries are skipped. A heartbeat regression fails
when synchronous lookup is restored. This provides event-loop cooperation, not
a hard resolver-worker deadline or bounded callback queue.

Error logs previously printed raw exceptions, which can contain URL credentials,
path/query secrets or response text. Module errors now report exception type;
target formatting removes userinfo/path/query. A task-local ContextVar filter
also prevents HTTPX's own request summaries from exporting callback URLs. Its
finally-reset handles success/error/cancellation, and concurrent unrelated request
logs remain visible. Existing callable logger filters remain compatible; that
import case failed before the guarded name lookup. Sources:
`jvagent/core/callback.py:14`, `:185`, `:279`, `:363`.

There is no new harness runtime, scheduler, retry loop, persistent schema or
dependency. The helper remains a JV transport service. Production task callback
ownership, durable delivery acknowledgement and recovery are separate gates.

## Real model and HTTPS browser cohort

[Machine evidence](2026-11-06-native-callback-transport-smoke.json) and
[browser observations](2026-11-06-native-callback-transport-browser.json) bind
parent/source epochs, requests, replies/tools/statistics, effect records, graph
readback, durations and usage. A disposable PostgreSQL app uses backend 8170 and
source jvchat 3140 with actual OpenAI GPT-4.1. A temporary app-local Action exposes
one fixed-payload diagnostic through the existing NativeMutationTool contract,
named host permission and official SDK effects. Its sanitized definition is in
the machine record; it is not added to the shipped Action library.

The diagnostic posts only a fixed synthetic marker to [HTTPBin's echo endpoint](https://httpbin.org/).
It accepts no model-selected recipient, URL or payload. Default HTTPX TLS
verification remains enabled; runtime functions are not patched. Observational
profiling records successful responses, pinned IP, Host, SNI and exact echoed
marker without exposing credentials or forwarding application/customer data.

1. An actual model tool call receives HTTP 200 and the matching marker. The SDK
   durably completes its declared callback-probe mutation; jvchat displays the
   tool, acknowledgement and usage.
2. An operator denies the named tool, then cold-restarts the backend. All prior
   Interaction/Conversation records and spending remain exact. A history-only
   question correctly recalls the acknowledgement without another POST/effect.
3. A new send request attempts the tool but host policy refuses it. The UI reports
   write denial; the run records mutation_access_denied and a terminal SDK failure
   without the domain mutation annotation. No second POST occurs.
4. After the callable-filter compatibility correction and permission restoration,
   a final-source process/new conversation performs a second explicit diagnostic.
   It again returns a matching 200 acknowledgement. Earlier records remain exact.
   Reload restores reply/tool/usage without any new records, spending or POST.

All four scenario expectations pass, including the expected denial. Numeric
totals and terminal-effect reconciliation are in the machine record. These are
functional observations, not a representative success-rate/latency comparison.
Model dollar amounts are SDK-priced estimates, not provider-billed amounts.

Six model requests report 2,745 tokens and USD 0.006096 estimated cost, zero
unknown usage. All three tool effects have exactly one terminal event: two
completed POSTs and one failed host denial. All four scenario expectations pass;
three runs deliver and the denied run fails as expected. Raw comparisons prove
unchanged restart/reload records and no changes to earlier turns after the final
source probe. No domain Task or production callback configuration is created.

## Checks and open gates

Twenty new transport cases plus twelve existing callback/SSRF cases pass. The
initial fourteen-case run had twelve failures/two passes; the added heartbeat,
log-scope and callable-filter cases exercise the subsequent corrections.
Mutation checks fail when DNS cooperation or log redaction is removed.
Final combined native/host/real-PostgreSQL/Docker/clock/callback lanes pass 784 on
each Python 3.11/3.12. Earlier 783-case lanes precede the callable-filter correction.
Clean all-files hooks and the normal full pre-push
suite remain mandatory publication gates.

The unmanaged mypy command now leaves one pre-existing handoff_action.py error;
the pinned repository hook remains authoritative. No handoff source is changed.

The live transport profile is IPv4 HTTPS with fixed synthetic echo data. HTTP,
IPv6, redirect refusal and secret-bearing errors have client/MockTransport
evidence, not live endpoint qualification. Userinfo authentication/IDNA endpoints,
all httpcore DEBUG transport logging, hard DNS worker deadlines, callback queue
capacity, background task scheduling/acknowledgement/idempotency, uncertain writes,
crash/recovery and customer-specific endpoints remain unqualified. No automatic
replay or repair is inferred from a successful echo. The nine reference migration
blockers, retained-state cutover, legacy removal and enterprise gates remain open.
