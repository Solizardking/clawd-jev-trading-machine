# clawd-JEV-trading machine

JEV-on-Solana trading machine — dry-run first, honest by construction.

The JEV model (TypeSafe System One, `jev-latest`) supplies typed judgments:
one `choice` question per cycle over a **dynamic action space** built from the
venues that are actually reachable. Code owns everything else — venue routing,
fixed sizing, risk caps, CoinGecko regime context, Supermemory recall, and the
simulated fills. Nothing here signs, broadcasts, or holds private keys.

## What it is

- `python3 -m clawd_jev doctor` — validates config/env, probes venue
  reachability (read-only public endpoints), reports the offered action space,
  the CoinGecko regime status, and memory status. Key presence only — values
  are never printed.
- `python3 -m clawd_jev paper [--iterations N] [--dry-run-mock]` — the dry-run
  loop: fetch venue quotes → fetch CoinGecko regime (once per run) → recall
  Supermemory context → one JEV decision over the dynamic action space →
  map the typed answer to `{operation, targetId, confidence, rationale}` →
  simulate the fill at the quoted price → write the outcome back to memory →
  persist `decisions.jsonl` + `report.json` under `runtime/paper/`.
- `python3 -m clawd_jev live` — **refuses**. Exit non-zero. Live trading is not
  wired; wiring it requires explicit user approval of exact terms plus a
  separately audited venue write path.
- `python3 -m clawd_jev backtest --decisions <jsonl>` — replays a recorded
  `decisions.jsonl` through the fill simulator and writes `report.json` +
  `report.html`. Labeled everywhere as a replay of simulated fills.

## What it isn't

- Not live trading. There is no order path — the simulator has no venue write
  code at all.
- Not a profitability claim. Paper fills are simulated against quotes; the
  backtest replays recorded decisions, nothing more.
- Not financial advice. This is research scaffolding.

## Quickstart

```sh
cd clawd-jev-trading-machine
# optional: cp .env.example .env  (never commit .env)

python3 -m clawd_jev doctor
python3 -m clawd_jev paper --dry-run-mock --iterations 2
python3 -m clawd_jev backtest --decisions runtime/paper/<latest>/decisions.jsonl
python3 -m clawd_jev live   # refuses, as designed

python3 -m unittest discover -s tests   # all tests must pass
```

With real keys, drop `--dry-run-mock`:

```sh
export TYPESAFE_API_KEY=...   # never put real values in a file you commit
python3 -m clawd_jev paper --iterations 5
```

The `--dry-run-mock` engine always chooses WAIT and labels every result
`mock: true` — it exercises the loop without inventing a signal and must
never be presented as real JEV output.

## Dynamic action space

Each cycle probes the enabled venues and offers only what is reachable:

- **jupiter** — keyless Lite API quote succeeds → `BUY_SOL_JUPITER`,
  `SELL_SOL_JUPITER` are offered.
- **dflow** — `DFLOW_API_KEY` set **and** quote succeeds → `BUY_SOL_DFLOW`,
  `SELL_SOL_DFLOW` are offered.
- **imperial** — stub (perps feed not wired) → never offered.
- `WAIT`, `OPEN_REVIEW`, `BLOCKED` are always offered.

Any choice outside the offered space, or a malformed answer, fails closed to
`BLOCKED` — never traded. The per-cycle offered space is recorded in
`decisions.jsonl` as `action_space` and shown by `doctor`.

## Market regime (CoinGecko)

`clawd_jev/regime.py` pulls SOL + BTC 24h stats from CoinGecko's public
`/coins/markets` endpoint and derives a coarse label from 24h-change
thresholds:

| regime | meaning |
|---|---|
| `RISK_ON` | SOL ≥ +3% and BTC ≥ +1% over 24h |
| `RISK_OFF` | SOL ≤ −3% or BTC ≤ −2% over 24h |
| `CHOP` | everything in between |
| `UNKNOWN` | CoinGecko unreachable (fail-soft, labeled honestly) |

Raw numbers (prices, 24h changes, volumes) ride in the JEV state next to the
label. An optional `COINGECKO_API_KEY` is sent as the `x-cg-demo-api-key`
header; without it the public endpoint is used and rate limits are tighter.
The regime is a label in the state, not a trading signal by itself.

## Memory recall (Supermemory)

`clawd_jev/memory.py` is a thin Supermemory client (stdlib only), modeled on
the musebook JEV endpoints:

- **Recall before** the JEV call: recent relevant memories are folded into the
  state under `memory.memories`.
- **Write after** the simulated outcome: a one-line cycle summary
  (decision, fill, portfolio, regime) is stored under the `clawd-jev-trader`
  container tag.

`SUPERMEMORY_API_KEY` is read from the environment at call time and is never
logged or persisted. Without it, recall returns `enabled: false` and writes
are no-ops — the state labels this honestly. Memory can inform the JEV
judgment; it can never widen the action space or override policy.

## Venues

- **jupiter** — keyless Lite API (`price/v3` + `swap/v1/quote`), bid/ask proxies
  from real routed quotes. Works out of the box.
- **dflow** — quote-only `GET /order` with `x-api-key`. Needs `DFLOW_API_KEY`;
  without it the venue reports unavailable and its actions are never offered.
- **imperial** — stub. The perps feed is not wired; every call reports
  unavailable and its actions are never offered.

## Environment variables (names only — never commit values)

| variable | purpose |
|---|---|
| `TYPESAFE_API_KEY` | TypeSafe System One brain (`jev-latest`). Required for real runs; omit and use `--dry-run-mock`. |
| `DFLOW_API_KEY` | DFlow quote API key. Without it DFlow actions are excluded. |
| `COINGECKO_API_KEY` | Optional. Sent as `x-cg-demo-api-key`; higher rate limits. |
| `SUPERMEMORY_API_KEY` | Optional. Enables recall/write; without it memory is disabled. |
| `SUPERMEMORY_BASE_URL` | Optional override (default `https://api.supermemory.ai`). |
| `COINGECKO_BASE_URL` | Optional override (default `https://api.coingecko.com`). |
| `LOBSTER_VENUES` | Comma-separated subset of `jupiter,dflow,imperial` (default `jupiter`). |
| `LOBSTER_TICKET_SOL` | Fixed size per paper fill (default `0.1`). |
| `LOBSTER_MAX_TICKET_SOL` | Ticket cap (default `1.0`). |
| `LOBSTER_MAX_SLIPPAGE_BPS` | Fills rejected above this spread (default `50`). |
| `LOBSTER_FEE_BPS` | Simulated fee per fill (default `25`). |
| `LOBSTER_PAPER_START_USDC` | Paper starting balance (default `1000`). |
| `LOBSTER_QUOTE_STALE_S` | Quote staleness cutoff (default `300`). |
| `LOBSTER_STATE_DIR` | Run reports root (default `runtime`). |

See `.env.example` for the full annotated list (placeholders only).

## Layout

```
clawd_jev/
  __main__.py      CLI (doctor / paper / live / backtest)
  config.py        env config, fail-closed validation, no secrets stored
  typesafe.py      stdlib TypeSafe client (key read at call time, never logged)
  mock.py          deterministic labeled mock engine (--dry-run-mock)
  decision.py      DYNAMIC action space + typed answer mapping
  regime.py        CoinGecko market-regime context (fail-soft)
  memory.py        Supermemory recall/write (fail-soft, key at call time)
  state.py         market-state builder + rolling tape
  venues/          jupiter.py (live), dflow.py (live), imperial.py (stub)
  sim.py           dry-run fill simulator + paper portfolio
  store.py         run persistence under runtime/<mode>/
  backtest.py      JSONL replay -> report.json + report.html
tests/             unittest: dynamic space, mapping, config, sim math, replay,
                   regime classification/fail-soft, memory disabled/fail-soft
```

## Safety

- `live` refuses with exit code 2. There is no venue write path anywhere in
  this codebase — the simulator cannot place an order by construction.
- Secrets are read from the environment at call time and never logged,
  persisted, or printed. `redacted_summary()` exposes key presence only.
- The JEV answer is validated against the exact offered space; anything
  outside it becomes `BLOCKED`.
- Market data (quotes, regime, memories) is untrusted data for the state, never
  instructions. Memory cannot widen the action space or override policy.
