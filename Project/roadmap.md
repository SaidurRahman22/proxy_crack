# Roadmap — beating proxychains

What the original actually does badly, and what Proxy Crack can do about it.
Ordered by leverage, not by ease.

**Written:** 2026-10-15

---

## First, be honest about what proxychains is good at

It is a ~2,000-line `LD_PRELOAD` shim that hooks libc's `connect()`. It is tiny, it has no
daemon, it starts instantly, and it has been stable for fifteen years. Any claim of "better"
has to beat it on **capability**, not on elegance — you will never beat it on footprint.

Where it is genuinely weak:

| Weakness | Why it matters |
|---|---|
| **No health awareness** | A dead hop is discovered by failing. No probe, no cache, no status. |
| **No failover** | One dead hop kills the chain. You edit the config file and retry. |
| **TCP only** | It hooks `connect()`. UDP, QUIC, VoIP, games do not work at all. |
| **Breaks on Go and static binaries** | `LD_PRELOAD` needs dynamic linking. Modern tooling is increasingly static. |
| **DNS is a hack** | `proxy_dns` maps lookups onto a fake `224.0.0.0/4` range. Leaks are common and hard to diagnose. |
| **Per-process** | You must prefix every command. Nothing covers the whole system. |
| **No observability** | No throughput, no per-hop latency, no success rate. |
| **Blind chain selection** | It picks from a list. It has no idea three hops are in the same datacenter. |

---

## The three that actually matter

### 1. Per-hop health checking and automatic failover ✅ **BUILT 2026-10-16**

Shipped. 628 tests. What exists now:
- `ProxyProbe` table + a real prober (SOCKS5, HTTP CONNECT, **and Shadowsocks AEAD**)
- Quality scoring where **reliability multiplies** and latency only scales within a 40% band,
  so a proxy dropping a third of its connections cannot climb back by being fast
- Wilson lower bound, so 1/1 does not outrank 198/200
- Flapping suppression: 3 consecutive failures to die, 1 success to revive, score stays depressed
- **Positional failover** — a hop can be a constraint (`"any premium SOCKS5 in NL, p95 < 200ms"`)
  resolved at connect time, never reusing a proxy within one chain
- `POST /api/chains/{id}/resolve/`, `probe_proxies`, `prune_probes`

Remaining within this item: mid-session failover without tearing down the TUN — that half is
engine-side (Phase 3), and the backend half (`/resolve/`) is done.

<details>
<summary>Original writeup</summary>

The single biggest functional gap in proxychains. Your schema already has `is_alive`,
`latency_ms`, `last_checked` — **nothing writes them** (noted as Phase 4).

Build:
- A **prober** that periodically opens a real connection through each proxy (not just a TCP
  handshake — do a full CONNECT to a known target and time it)
- **Success rate**, not just alive/dead. A proxy that answers but drops 30% of connections is
  worse than one 80 ms slower, and alive/dead cannot express that.
- **Positional fallback pools.** Let a chain slot be a *constraint* — "position 2 = any premium
  SOCKS5 in NL, p95 < 200 ms" — instead of a fixed proxy id. When a hop dies, the engine
  re-resolves the slot rather than dropping the tunnel.
- **Mid-session failover** without tearing down the TUN.

> proxychains cannot do any of this. This is the clearest "strictly better" feature you have.

</details>

### 2. Chain quality scoring — ASN and jurisdiction diversity ✅ **BUILT 2026-10-17**

Shipped. 15 finding codes, severity-ranked, score derived from findings. `unknown` deliberately
ranks above `high` — a hop nobody checked is worse than a known problem — and a coverage ceiling
makes that structural. Includes a candidate-analysis endpoint for vetting proxies before buying.

<details>
<summary>Original writeup</summary>

A 3-hop chain where all three hops sit in the same datacenter, on the same ASN, under the same
legal jurisdiction, provides **almost none** of the protection the user thinks they bought.
proxychains will happily build it and say nothing.

Build a score shown **before** connecting:
- **ASN diversity** — are hops on genuinely different networks? (Bulk proxy sellers often resell
  the same handful of ASNs.)
- **Jurisdiction diversity** — flag chains entirely inside one legal bloc, and flag
  Five/Nine/Fourteen-Eyes concentration.
- **Operator diversity** — if hop 1 and hop 3 are the same provider, that provider sees both
  ends. This is the single most important and least understood weakness of a naive chain.
- **Correlation warning** — same /24, same hosting company, same upstream transit.

This is the feature that makes the product *credible to people who know what chaining is for*,
and it is pure data work — no engine changes.

</details>

### 3. Kill switch and leak prevention 🔴 table stakes that proxychains fails

proxychains leaks on failure. If the chain breaks, traffic quietly goes direct.

- **Fail closed.** If the engine dies, the TUN stays up and drops packets. Never fall back to
  direct.
- **DNS strictly through the tunnel**, with DoH/DoT. This is proxychains' most notorious weak
  point; doing it properly is a real differentiator.
- **IPv6 handling** — either route it or black-hole it. Half-handled IPv6 is a classic leak.
- **Built-in leak test** — DNS, IP, WebRTC — run from inside the app so a user can *verify*
  rather than trust.
- **Always-on VPN** integration on Android.

---

## Strong second tier

### 4. Latency-budgeted chain composition
Instead of hand-picking three proxies, let the user state intent: *"3 hops, exit in NL, under
600 ms, maximum ASN diversity."* Compose it automatically from live health data, and show the
predicted latency before connecting. proxychains makes you hand-edit a config file and find out
by trying.

### 5. Per-app split tunnelling
Android `VpnService` supports per-app rules natively. "Browser through chain A, banking app
direct, everything else through chain B." proxychains' equivalent is prefixing each command —
and it cannot do it for apps you do not launch from a shell.

### 6. UDP support — with honest limits
SOCKS5 (`UDP ASSOCIATE`) and Shadowsocks both carry UDP; proxychains carries none. This is a
genuine win **except** that an HTTP hop breaks it (see `architecture.md` §1). So:
- Mark each proxy `supports_udp`
- Compute `chain.supportsUdp`
- **Warn in the chain builder** when adding an HTTP hop silently kills QUIC, VoIP and games
- Black-hole UDP explicitly rather than letting it hang

### 7. Chain presets by purpose
- *Max anonymity* — 3 hops, forced ASN + jurisdiction diversity, no HTTP hop
- *Low latency* — 1 hop, nearest healthy, UDP-capable
- *Streaming* — exit in a chosen country, bandwidth-biased
- *Paranoid* — 4 hops, different operators, DoH, kill switch hard-on

Presets are how non-experts get a sane chain. proxychains assumes you already know.

### 8. Observability the original has none of
Per-hop latency breakdown (which hop is slow), live throughput, success rate over time, session
history. When a chain is slow, proxychains gives you nothing; you should name the hop.

---

## R9 — Provider sync (Webshare and friends) 🔴 not built, and the gap is bigger than it looks

Today proxies enter the database **one at a time, by hand or by CSV**. Nothing talks to the
provider. That has three consequences the operator will hit immediately:

1. **You load a fraction of what you pay for.** A Webshare free plan is 10 IPs; one is loaded.
2. **Rotation silently breaks the product.** Providers replace IPs. When Webshare swaps an
   address, this database keeps the dead one and the health checker marks it dead — correct, but
   the replacement never arrives. Inventory decays with no way back up.
3. **Bandwidth is invisible.** The free plan is 1 GB. Nothing tracks consumption against the
   provider's own meter, so the first sign of exhaustion is everything failing at once.

### What to build

- A `ProviderSync` abstraction — same pluggable shape as `Prober` and `IpIntel`, with a fake for
  tests, because these all hit a third party we cannot reach from CI.
- A **Webshare adapter**: `GET https://proxy.webshare.io/api/v2/proxy/list/` with
  `Authorization: Token <key>`, paginated. Returns address, port, username, password, country
  code and validity — i.e. everything `Proxy` needs, including the `country` that is blank today
  (**P22**).
- Store the provider API key **the same way proxy credentials are stored**: encrypted at rest,
  never in a committed file, fail-loud if the key is missing. It is as sensitive as the proxies
  it buys.
- **Reconcile, do not blind-import.** A sync must add new endpoints, mark vanished ones
  `is_enabled=False` rather than deleting them (deleting loses probe history and breaks saved
  chains via `PROTECT`), and **never silently blank credentials** — the exact bug already fixed
  once in the CSV importer. Reuse that rule.
- Pull the provider's **bandwidth counter** where exposed, and surface it next to `UsageLog` so
  the operator sees both their own accounting and the provider's.
- Schedule it the same way as `probe_proxies`: a management command a timer calls. No queue.

### Why it is also a credibility feature

Sync records **which provider sold each endpoint**, which feeds `Proxy.provider` — and that is
the field R2's operator-diversity check depends on. Without it an operator types vendor labels
by hand and the diversity score is only as good as their typing.

## Quick wins

| | Effort | Value |
|---|---|---|
| `supports_udp` field + chain validation | S | prevents a confusing class of bug |
| Chain latency prediction from `latency_ms` | S | already have the data |
| "All hops same ASN" warning | S | high credibility, pure data |
| Config export → actual `proxychains.conf` | S | lets power users bridge to the tool they know |
| Import from `proxychains.conf` | S | removes migration friction entirely |
| Per-hop latency in the connection screen | S | makes slowness diagnosable |

---

## Deliberately NOT recommended

- **Writing your own TUN/IP stack.** Months of subtle bugs. Use Xray (D9).
- **Supporting every exotic protocol.** SOCKS5 + HTTP + Shadowsocks covers what commercial
  providers actually sell.
- **Claiming anonymity guarantees.** Chaining through commercial proxies you purchased, under
  one account, with one payment trail, is *not* Tor. Say what it is: traffic routing with
  layered egress. Overclaiming here is both a legal and a reputational risk.
- **A "free unlimited" tier.** The credentials are your cost base. Free must be strictly
  bounded or the unit economics invert.

---

## Where this leaves you versus proxychains

| | proxychains | Proxy Crack (if the above ships) |
|---|---|---|
| Scope | one process | whole device, or per-app |
| Works with Go/static binaries | ❌ | ✅ (TUN, not LD_PRELOAD) |
| Health awareness | ❌ | ✅ probed, scored |
| Failover | ❌ | ✅ positional pools |
| UDP | ❌ | ✅ (non-HTTP chains) |
| DNS | leaky hack | ✅ DoH through tunnel |
| Kill switch | ❌ | ✅ fail closed |
| Chain quality feedback | ❌ | ✅ ASN/jurisdiction scoring |
| Observability | ❌ | ✅ per-hop |
| Cross-platform | Linux only | Android → Linux → Windows → macOS |
| Footprint / startup | **wins** | heavier |
| No daemon required | **wins** | needs a service |

**Honest summary:** you beat it on capability almost everywhere, and you will never beat it on
being a 2,000-line shim with no daemon. That is a fine trade — but it means the Linux build
should *also* offer the lightweight per-process mode (see `engine-decision.md`), because for
Linux power users that mode is genuinely the right tool and shipping it buys credibility with
exactly the audience that knows what proxychains is.
