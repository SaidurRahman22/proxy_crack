# Proxy Crack — Engine Decision (resolves P1)

**Status:** RESOLVED — **Xray-core**
**Date:** 2026-10-15
**Resolves:** `memory.md` pending decision **P1** (sing-box GPLv3 vs Xray-core MPL)
**Supersedes:** `architecture.md` §1 recommendation, and corrects two factual errors in it (see §11)
**Scope:** **Cross-platform.** Android is the foundation and ships first. Windows, Linux and macOS
desktop products are built on the same core afterwards. The engine is evaluated as a four-platform
choice, not an Android-only one.

> **Did the cross-platform scope change the recommendation? No — it strengthened it, decisively.**
> Nothing flipped. Three things got *worse* for sing-box and *better* for Xray:
> (1) GPLv3 exposure multiplies across four commercially distributed products instead of one;
> (2) desktop distribution is direct, which **removes** the one ambiguity (the app-store "further
> restrictions" argument) that made the GPLv3 analysis debatable — direct conveying of a GPLv3
> binary requires source publication with no room for argument at all;
> (3) Xray-core's TUN inbound covers Linux, Windows, macOS, Android **and** iOS from one codebase,
> and libXray ships build targets for every one of them.
> The only new finding that cuts the other way is **Wintun** (§1.6) — and it hits both engines
> equally, so it does not discriminate between them.

> **This is engineering analysis, not legal advice.** I am not a lawyer. The licence reasoning
> below is based on the primary licence texts and on how the code is actually linked, but the
> conclusions — particularly anything about derivative works, linking, and app-store terms — are
> contested areas where courts have said very little.
> **Have a solicitor with software-licensing experience confirm this before you take payment for
> any of the four products.** The cost of being wrong is the obligation to publish the entire
> source of a commercial product, which is not something you can unwind.

---

## 0. Decision at a glance

| | |
|---|---|
| **Engine** | **Xray-core** (XTLS/Xray-core), MPL-2.0 |
| **Binding layer** | **libXray** (XTLS/libXray), **MIT** — one wrapper, build targets for all platforms |
| **tun2socks** | Xray-core's **built-in `tun` inbound** (gVisor IP stack) — merged into `main` 2026-01-07, covering Linux, Windows, macOS, Android, iOS |
| **Chaining** | `streamSettings.sockopt.dialerProxy` — **not** `proxySettings`, which has been removed |
| **Strongest reason** | MPL-2.0 §3.3 explicitly permits a proprietary "Larger Work". GPLv3 §5(c) requires licensing **the entire shipped work** under GPLv3 — on each of four products, at every release. The business model is closed-source paid subscription, so GPLv3 is disqualifying and MPL is not. |
| **Second reason** | The one technical argument that previously favoured sing-box — "Xray has no tun2socks" — **is no longer true**, on any platform. |
| **Residual risks** | Xray's `tun` inbound is new (Jan 2026) and lightly documented. libXray's desktop targets produce a **CLI binary, not a shared library**, so the desktop integration shape differs from mobile (§3). Wintun's redistribution licence needs a legal read for the Windows product (§1.6). |

---

## 1. Licence analysis

### 1.1 First, get the linking question right

Both engines are Go. Both reach each platform the same two ways:

- **Mobile / in-process:** `gomobile bind -target=android` compiles the Go package tree into a
  native shared library (`libgojni.so`) plus Java/Kotlin stubs, packaged as an `.aar`. The Apple
  equivalent produces an `.xcframework`.
- **Desktop:** a native executable (or, with `-buildmode=c-shared`, a `.dll`/`.so`/`.dylib`).

This matters because people reach for the "dynamic linking is different" argument. For the mobile
case it does not help:

- The `.so` is **statically linked Go**. Every Go package in the build — all of sing-box or all of
  Xray-core — is compiled into one object. There is no separable engine library a user could swap.
- The `.aar` ships **inside your APK**. You are distributing (GPLv3 calls it "conveying") the
  engine's object code together with your code, as one installable artifact.
- Your Kotlin/Dart code calls the engine through generated JNI stubs: a function call across a JNI
  boundary **within one process**, not two programs talking at arm's length.

The FSF's position is that calling into a library in the same address space creates a combined
work. Whether a court would agree is genuinely untested — but **you do not want to be the test
case**, and the "separate process" escape hatch (§1.2.3) does not rescue the business model even
where it is strongest.

### 1.2 sing-box — GPL-3.0-or-later

**Verified:** The sing-box README's License section contains the GPL text pointing to "version 3
... or (at your option) any later version". Distro packagers agree: Arch lists `GPL-3.0-or-later`
plus `LicenseRef-sing-box-exception`; Alpine lists `GPL-3.0-or-later` with a
name-use-or-association addition.

**The additional clause:** the README adds that *"no derivative work may use the name or imply
association with this application without prior consent."* This is almost certainly a GPLv3 §7(e)
additional term — §7(e) permits *"Declining to grant rights under trademark law for use of some
trade names, trademarks, or service marks"*. So it is a **permitted** additional restriction, not a
licence incompatibility. Practical effect: you could not market any of the four products as
"powered by sing-box" without SagerNet's consent. A marketing footnote, not the problem.

#### 1.2.1 What GPLv3 actually requires of you

The operative clause is **§5(c)**, verbatim:

> "You must license the entire work, as a whole, under this License to anyone who comes into
> possession of a copy."

"The entire work, as a whole" is your APK, your `.exe` installer, your `.deb`/`.rpm`/AppImage, and
your `.app` bundle. Combined with §6 (conveying non-source forms), you must provide the
**Corresponding Source** — §1 defines it as *"all the source code needed to generate, install,
[and run the object code]"*. Across the four products that means:

- Every line of your shared Flutter/Dart UI
- Every line of the Kotlin `VpnService` layer, the Windows elevated helper, the Linux daemon, and
  the macOS system extension
- Your Gradle, CMake, MSIX/Inno, and notarisation build scripts
- Any proprietary logic you put client-side (chain builder, paywall gating, credential handling)

…all licensed **under GPLv3**, to every person who installs any of them, on request.

And **§10**, verbatim:

> "You may not impose any further restrictions on the exercise of the rights granted or affirmed
> under this License."

So you could not add an EULA forbidding redistribution, reverse engineering, or sharing — on any
platform.

#### 1.2.2 Why this kills the business model even though GPL permits selling

A common misreading: "GPL lets you charge money, so a paid app is fine." True but irrelevant. GPL
restricts **price** not at all; it restricts **exclusivity** totally. Under GPLv3:

- Any one subscriber may obtain your full source and **republish the product** — on F-Droid, on
  GitHub, as a free installer — legally, with your blessing baked into the licence.
- They may strip the paywall and the subscription check, because §10 forbids you restricting that.
- Your only remaining moat would be the **backend** (Django + the proxy credentials it hands out),
  which is not conveyed to users and therefore stays closed. But a forked client pointed at your
  API would be indistinguishable from a legitimate one, and your entitlement logic would be fully
  readable by anyone wanting to abuse it.

For a subscription product whose value *is* the client plus curated proxy credentials, this is not
a licence inconvenience. It is a different company.

#### 1.2.3 The "run it as a separate process" workaround — strongest on desktop, still a bad bet

The usual dodge is to ship the sing-box **binary** and run it as a child process, talking to it over
a local SOCKS port or its API. The argument is that two programs exchanging data at arm's length are
an "aggregate" under GPLv3, not a combined work.

**Cross-platform scope makes this argument genuinely stronger on desktop** — on Windows and Linux
the engine *is* naturally a separate process (that is how both engines ship there, and how libXray's
own desktop targets work). So be honest: on desktop, the aggregation argument is a real argument,
not a fig leaf.

It still fails as a strategy, for four reasons:

1. **It is contested.** The FSF treats pipe/socket communication as *usually* aggregation, but
   weighs "intimacy of communication" and whether the programs are designed to work only together.
   A bundled binary that is useless without your app, launched by your app, configured by your app,
   is not an obvious arm's-length relationship.
2. **You still convey sing-box.** You must still supply sing-box's Corresponding Source for the
   exact binary you shipped — easy in principle (upstream plus your build script), but an obligation
   you must discharge for every release of every product, forever.
3. **It does not work on mobile, which is the foundation.** The Android `VpnService` TUN file
   descriptor belongs to your process. Handing an fd to a forked child means fd inheritance or
   `SCM_RIGHTS` over a Unix socket. On iOS it is flatly impossible — NetworkExtension forbids
   spawning subprocesses. So you would need **two different licence postures and two different
   process architectures** for mobile vs desktop. That is the opposite of a shared core.
4. **It poisons the shared-core goal.** The entire point of the scope change is maximum code
   sharing across four platforms. A licence-driven process split that applies on two platforms and
   not the other two is exactly the kind of structural divergence that makes a shared core
   collapse into four codebases.

#### 1.2.4 The Google Play angle — weaker than usually claimed, and now largely moot

The famous GPL-vs-app-store conflict is the **Apple** one: Apple's developer agreement imposes a
device limit and a redistribution ban on end users, which are "further restrictions" barred by
GPL §10 (GPLv2 §6). That is why GPL projects like VLC and Pidgin fought with Apple.

**Google Play is materially different.** I read the Google Play Developer Distribution Agreement
directly:

- **No device limit.** §3.9: *"Users are allowed unlimited reinstalls of each Product distributed
  via Google Play without any additional fee."*
- **No DRM clause.** The agreement does not mention digital rights management at all.
- **No broad ban on users redistributing.** §5.3 only discusses *family group* sharing limits.
- **No open-source carve-out either.** The agreement never mentions open-source or free software.
  §5.3 says that where a separate EULA conflicts, *"this Agreement will supersede the EULA"*, and
  §16.1 calls it the *"entire legal agreement"*.

So the §10 objection that bites on Apple is **much weaker on Play**, and empirically GPLv3 apps
(Anki, OsmAnd) ship on Play without incident. Google has historically enforced *in the other
direction* — removing repackaged VLC clones precisely because they did not publish modifications.

**Keep this argument separate from the real one.** Do not let anyone tell you "GPL can't go on
Play" — it can. GPLv3 is disqualifying because §5(c) forces you to publish your entire source.
Conflating the two leads to the wrong conclusion if someone later "disproves" the Play angle.

*Uncertainty flagged:* I could not find an FSF or Software Freedom Conservancy analysis of the
current Play DDA. My reading is of the agreement text, not of authority.

#### 1.2.5 Desktop distribution — the ambiguity disappears and the obligation remains

This is the section the cross-platform scope adds, and it is the one that settles the matter.

**Windows and Linux: direct download, no store.** You host an installer. There is no intermediary
imposing terms, so the §10 "further restrictions" question — the only genuinely contested part of
the GPL/app-store debate — **never arises**. What remains is §6, undiluted. You convey object code;
you must provide Corresponding Source by one of §6's routes (typically §6(d): equivalent access to
the source from the same server as the binary). There is no ambiguity to exploit and no counsel who
would tell you otherwise.

> **This is the key cross-platform insight on licensing.** On Play, a GPLv3 defender can at least
> argue the edges. On a direct-download Windows installer, there are no edges. Expanding to desktop
> makes GPLv3 *more* clearly fatal, not less.

**Linux packaging makes it worse again.** Linux users expect `.deb`/`.rpm`/Flatpak/AUR. Distro
packaging norms and automated licence scanners (`reuse`, `licensecheck`, Debian's ftpmaster review)
will surface a GPLv3-linked proprietary binary almost immediately. The Linux ecosystem is where
licence violations are found fastest, by people who enjoy finding them.

**macOS splits in two:**
- **Developer ID (direct download):** same as Windows — §6 applies cleanly, no store terms.
- **Mac App Store:** this is the genuine Apple conflict. Apple removed VLC from the store over
  exactly this. A GPLv3 engine would close off the Mac App Store entirely. A real precedent: AdGuard
  VPN ships a Mac App Store build on NetworkExtension and a *separate* standalone build on utun —
  vendors routinely maintain two macOS builds for this class of reason.

**iOS (future):** App Store only, so the Apple conflict is total and unavoidable. If iOS is ever in
scope, GPLv3 is not merely bad business, it is a dead end.

#### 1.2.6 Exposure multiplies — count the obligations

Under GPLv3 you would owe Corresponding Source:

| Product | Conveyance | Obligation triggers |
|---|---|---|
| Android | Google Play (+ any sideload) | every release |
| Windows | direct installer download | every release |
| Linux | direct download + any distro packaging | every release, plus packager scrutiny |
| macOS | Developer ID download (MAS closed off entirely) | every release |

That is **four independent, perpetual publication obligations**, each attaching to a *different*
platform-integration layer — the Windows elevated helper, the Linux daemon, the macOS system
extension — which are precisely the components where your hard-won engineering lives. The
cross-platform scope does not add a new *kind* of risk; it multiplies the existing one by four and
spreads it across the parts of the codebase you would least want to publish.

#### 1.2.7 Anti-tivoisation (§6) — probably not triggered, worth a lawyer's eye

GPLv3 §6 requires "Installation Information" when GPL'd object code is conveyed **in or with a User
Product**. Shipping an app or an installer is conveying software, not conveying a consumer device,
so anti-tivoisation almost certainly does not apply on any of the four platforms. Google Play App
Signing is not a technical measure preventing modified installs — sideloading remains possible. I
rate this a non-issue, but it is worth one line in a legal memo.

### 1.3 Xray-core — MPL-2.0

**Verified:** `XTLS/Xray-core/LICENSE` is the standard **Mozilla Public License, Version 2.0**, with
**no project-specific additions, exceptions or extra clauses**. Exhibits A and B are the stock MPL
templates.

MPL-2.0 is **file-level ("weak") copyleft**. The relevant clauses, verbatim from mozilla.org:

- **§1.7 "Larger Work"** — *"means a work that combines Covered Software with other material, in a
  separate file or files, that is not Covered Software."*
- **§1.10 "Modifications"** — *"(a) any file in Source Code Form that results from an addition to,
  deletion from, or modification of the contents of Covered Software; or (b) any new file in Source
  Code Form that contains any Covered Software."*
- **§3.2(a)** — if you distribute Covered Software in Executable Form, *"such Covered Software must
  also be made available in Source Code Form ... and You must inform recipients of the Executable
  Form how they can obtain a copy of such Source Code Form by reasonable means in a timely manner,
  at a charge no more than the cost of distribution to the recipient"*.
- **§3.2(b)** — *"You may distribute such Executable Form under the terms of this License, or
  sublicense it under different terms, provided that the license for the Executable Form does not
  attempt to limit or alter the recipients' rights in the Source Code Form under this License."*
- **§3.3** — *"You may create and distribute a Larger Work under terms of Your choice, provided that
  You also comply with the requirements of this License for the Covered Software."*

#### What this means concretely, on all four platforms

| Your action | Obligation |
|---|---|
| Ship unmodified Xray-core in an APK, an `.exe`, a `.deb`, or a `.app` | **No source disclosure of your code.** Your Dart, Kotlin, C++, Swift and build scripts are "other material in separate files" = a Larger Work under §3.3. |
| Satisfy §3.2(a) for Xray itself | Tell users where to get Xray-core's source. In practice: one shared "Open source licences" screen — **the same Dart widget on all four platforms** — naming the **exact version/commit** you built, linking `github.com/XTLS/Xray-core`, and including the MPL text. Keep a copy of that exact source tarball yourself in case upstream disappears. |
| **Modify any `.go` file in Xray-core** | That file (and any new file containing Xray code) is a **Modification** under §1.10 and must be published under MPL-2.0. The rest of your product is untouched. |
| Apply a proprietary EULA | Permitted by §3.2(b), **provided** it does not purport to limit rights in Xray's Source Code Form. Your EULA must carve that out explicitly — one clause, reused across all four. |
| Sell subscriptions, on any platform, through any channel | No restriction whatsoever. MPL has no price, exclusivity, store, or anti-DRM clause. |

**There is no "further restrictions" clause in MPL-2.0 analogous to GPLv3 §10.** That is the single
structural difference that makes MPL safe here and GPLv3 not — and it holds identically for Play,
the Mac App Store, direct download, and Linux distro packaging.

**MPL and the Mac App Store:** MPL-2.0 imposes nothing that conflicts with Apple's terms. The
Mac App Store (and, later, iOS) stays open to you. That option has real commercial value and GPLv3
would destroy it.

#### 1.3.1 Practical compliance rule for the team

> **Treat `Xray-core/` as read-only.** If you need behaviour it does not have, add it in your own
> wrapper package, not by editing Xray's files. If you genuinely must patch Xray, do it in a public
> fork on GitHub and link to it from the licences screen — the obligation is then discharged by
> construction and costs you nothing, because the patch is engine plumbing, not product.

### 1.4 libXray — MIT

**Verified:** the libXray README states *"This repository is based on the MIT License."* It credits
MetaCubeX `age` (BSD 3-Clause) as a dependency.

MIT is attribution-only. You may fork it, modify it, keep your fork private, and ship it
closed-source on every platform. **This matters more than it looks** — §5.4 shows you will almost
certainly need to fork it to add a `VpnService.protect()` hook on Android, and §3 shows you may need
to extend its desktop targets. MIT makes both a non-event.

### 1.5 Wintun on Windows — a new licence item, and it is engine-neutral

This is the one finding the cross-platform scope adds to the licence analysis, and it needs a
lawyer's eye before the Windows product ships.

Xray-core's README has a "Bundled Third-Party Components Redistribution" section listing exactly one
component: **Wintun**.

- Copyright: *"Copyright (C) 2018-2021 WireGuard LLC. All Rights Reserved."*
- Redistribution licence: **Prebuilt Binaries License (PBL)**, not MPL
- Component: `wintun.dll`; source `https://www.wintun.net/`
- Included in: Windows x86, x86-64, and AArch64 builds
- Notes: *"Wintun is an optional runtime-loaded component only used for TUN inbound functionality on
  supported Windows platforms."*

The PBL grants a *"non-exclusive and non-transferable right to use Software for lawful purposes"*
and **prohibits redistribution without WireGuard LLC's prior written consent**, with one carve-out:
*"except insofar as the Software is distributed alongside other software that uses the Software only
via the Permitted API"* (i.e. `wintun.h`). It also forbids reverse engineering, modifying the binary,
removing copyright notices, and using WireGuard's name to promote derived products.

**Assessment:** Xray loads `wintun.dll` at runtime through the documented API, which appears to fall
squarely inside the carve-out. So bundling it is *probably* fine. But "probably" is not the standard
for a commercial product, and this is a restrictive proprietary licence, not an open one.

**Three things to note:**

1. **It does not discriminate between the engines.** sing-box's Windows TUN support uses Wintun too.
   This is a cost of doing Windows TUN at all, not a reason to prefer either engine.
2. **Add it to the lawyer list** (§1.8 item 6). If the carve-out reading is wrong, you need written
   consent from WireGuard LLC — obtainable, but with a lead time you want to discover early, not
   during a release.
3. **Mirror the binary.** wintun.net has gone down before, and downstream projects have had to cache
   it. Vendor the exact `wintun.dll` you ship into your own build artefacts with its licence file
   beside it.

**Avoiding Wintun entirely:** Windows has no supported userspace TUN alternative. WFP (Windows
Filtering Platform) is a *filtering* layer, not a TUN provider — Xray uses it for DNS leak blocking
(`autoSystemWfpBlockLeak`), not for packet capture. A WFP-only design means a different and much
harder architecture. **Plan on Wintun; budget the legal check.**

> **Action:** when you pin an Xray-core version, run a licence scan over the final `go.mod`
> dependency graph **for each platform's build** (build tags differ per OS, so the graphs differ).
> Do it once per engine version bump and keep the output. The usual landmines are GPL/AGPL
> transitive deps. I have **not** audited Xray's dependency tree — that is a build-time task.

### 1.6 Verdict

| Engine | Licence | Safe for four closed-source commercial products? |
|---|---|---|
| **sing-box** | GPL-3.0-or-later (+ §7(e) name clause) | **No.** §5(c) requires the entire shipped work under GPLv3 — on Play, on direct download, and on Linux packaging. Closes off the Mac App Store and iOS permanently. |
| **Xray-core** | MPL-2.0, unmodified | **Yes.** §3.3 explicitly contemplates a proprietary Larger Work. Obligation limited to Xray's own files. No store is closed off. |
| **libXray** | MIT | **Yes.** Attribution only. Fork freely, on every platform. |
| *Wintun (Windows only)* | *Prebuilt Binaries License* | *Probably yes via the Permitted-API carve-out — **needs legal confirmation**. Applies to either engine.* |

### 1.7 What to put in front of a lawyer

Give them this list, not this document:

1. Does bundling a `gomobile`-produced `.aar` (statically linked Go in `libgojni.so`) inside our APK
   create a GPLv3 "combined work" such that §5(c) reaches our Flutter and Kotlin code? *(We assume
   yes and are avoiding GPLv3 entirely — confirm we are not being over-cautious in a way that costs
   us, and confirm there is no GPL dependency hidden in the Xray graph on any platform.)*
2. Under MPL-2.0 §3.2(a), is an in-app "Open source licences" screen naming the exact Xray-core
   version and linking to GitHub sufficient "reasonable means in a timely manner"? Does the answer
   differ for a directly-downloaded desktop installer versus an app-store app? Do we need a written
   offer with a retention period?
3. Draft the EULA carve-out required by MPL §3.2(b) so our terms do not purport to limit rights in
   Xray's Source Code Form — one clause reused across four products.
4. Confirm nothing in the Google Play Developer Distribution Agreement §5.1 licence grant to Google,
   or in the Apple Developer Program Licence Agreement, conflicts with MPL §3.2(b).
5. Confirm our paid proxy provider's ToS permits redistribution of credentials via an app
   (`memory.md` P4 — unrelated to engine licensing, but it is the other contract that can kill the
   product, so ask once).
6. **Wintun:** does bundling `wintun.dll` in a commercial Windows installer fall inside the Prebuilt
   Binaries License carve-out for *"software that uses the Software only via the Permitted API"*, or
   do we need written consent from WireGuard LLC? **This one has a lead time — ask early.**

---

## 2. Capability comparison

### 2.1 Cross-platform core — how each engine is consumed per platform

Both engines are pure Go, so the *protocol core* genuinely is the same code on all four platforms.
The difference is in **how you consume it**, and here the two diverge in a way that matters.

| | **sing-box** | **Xray-core (via libXray)** |
|---|---|---|
| Android | `libbox` gomobile `.aar`, **in-process** | gomobile `.aar`, **in-process** (`build/main.py android`) |
| iOS / macOS | `libbox` via gomobile / xcframework, in-process | `LibXray.xcframework` via cgo, **in-process** (`build/main.py apple go`) — also a gomobile variant |
| Windows | `sing-box.exe` CLI, **separate process** | `bin/xray.exe` CLI, **separate process** (`build/main.py windows`) |
| Linux | `sing-box` CLI, **separate process** | `bin/xray` CLI, **separate process** (`build/main.py linux`) |
| One wrapper for all? | No — libbox (mobile) and the CLI (desktop) are different surfaces | **Yes — libXray is one repo with a target per platform**, and a single `Invoke(requestJSON) -> responseJSON` API shape |

**The important structural fact, verified from libXray's README:** the Linux and Windows targets
produce **`bin/xray` / `bin/xray.exe`** — a standalone "session Core" executable, **not** a shared
library. It is driven by the command line:

```
xray run -dns <IP:port> -interface <name> -config <xray.json> [-error-file <path>]
```

`-dns`, `-interface` and `-config` are **required**. `-error-file` needs a desktop Core built with
that support.

So your consumption model is:

- **Mobile and macOS:** engine **in-process**, called through `Invoke(json) -> json`.
- **Windows and Linux:** engine as a **child process**, configured by a JSON file on disk and a
  named interface, with no fd handoff.

This is not a flaw — it is the normal shape of desktop VPN software and it maps cleanly onto the
elevated-helper model you need anyway (§3.2, §3.3). But it **must** be reflected in the architecture
(§4), because an abstraction that assumes in-process everywhere will not survive Windows.

> **Note the licence synergy.** On Windows and Linux the engine is already a separate process, so
> the MPL §3.3 "Larger Work" analysis there is even cleaner than on Android — the engine is not even
> in your address space. MPL did not need this help, but it is worth knowing the posture is strong
> on every platform.

#### Is a desktop shared library possible?

Yes in principle: libXray exports C symbols —

```c
char* CGoInvoke(char* requestJSON);
void  CGoFree(char* value);
```

— and the README says *"`CGoInvoke` allocates its response"*, that every non-null response must be
released with `CGoFree`, and that callers **must not** use a platform allocator directly. Go supports
`-buildmode=c-shared` to emit a `.dll`/`.so`. Combined with `dart:ffi` (supported on Android, iOS,
Windows, macOS and Linux) this would give a uniform in-process model everywhere.

**But libXray does not document a `c-shared` desktop target**, and building one yourself takes you
off the supported path. More importantly, **it would not help**: on Windows and Linux the engine must
run elevated (§3.2, §3.3), and you do not want an elevated Flutter UI process. The child-process
model is the *correct* architecture on desktop regardless of what is technically possible.

**Recommendation: use the documented CLI target on Windows and Linux. Do not build a c-shared
desktop library.** Revisit only if a concrete requirement demands in-process desktop operation.

### 2.2 Per-platform feature gaps — call these out now

| Feature | Android | Windows | Linux | macOS |
|---|---|---|---|---|
| Xray `tun` inbound | yes (`tun_android.go`, fd via env) | yes (Wintun) | yes (primary target) | yes (`tun_darwin.go`, utun) |
| libXray `SetDNS` / `ResetDNS` | **yes** | **no — Android-only** | **no** | **no** |
| libXray `registerProcessFinder` (per-app routing) | **yes** | **no** | **no** | **no** |
| Automatic system-DNS redirection | n/a (`VpnService.addDnsServer`) | `autoSystemWfpBlockLeak` (WFP) | `autoSystemDnsToGateway` — **requires systemd-resolved** | **no documented mechanism** |
| Routing-table automation | n/a (VpnService) | `autoSystemRoutingTable` | `autoSystemRoutingTable` | *unverified* |
| Per-app split tunnelling | `VpnService.Builder` | no OS primitive — out of scope v1 | `cgroup`/netns, bespoke — out of scope v1 | per-app via NE rules (App Store tier) |

Three of these deserve emphasis:

1. **`SetDNS`, `ResetDNS` and `registerProcessFinder` are Android-only** (libXray README states this
   explicitly). Your `ProxyEngine` abstraction must treat them as optional capabilities, not core
   API. Do not let Android-only features leak into the shared interface.
2. **macOS has no documented DNS-leak automation in Xray.** On Windows there is WFP blocking; on
   Linux there is systemd-resolved integration; on macOS there is neither. For macOS you will set
   DNS through NetworkExtension's `NEDNSSettings` (App Store / sysex tier) or via
   `scutil`/`networksetup` from the privileged helper. **Budget bespoke macOS DNS work** (§8.5).
3. **The Linux DNS path requires systemd-resolved.** Xray's TUN README says `autoSystemDnsToGateway`
   is off by default with strict preconditions, and **refuses to start rather than leaking DNS** if
   they are unmet — good failure behaviour, but it means non-systemd distros (Alpine, Void, some
   Gentoo) need a fallback path or must be unsupported.

### 2.3 Chaining

**Both do real chaining. Xray's syntax is different from what `architecture.md` says.**

`architecture.md` §1 states Xray chains via `proxySettings`. **That is now wrong.** I checked
`infra/conf/xray.go` on `main`: the `OutboundDetourConfig` struct still declares a field with the
JSON tag `proxySettings`, but `Build()` now rejects it:

```go
errors.PrintRemovedFeatureError(`outbound "proxySettings"`, `"streamSettings.sockopt.dialerProxy"`)
```

The field survives **only to emit a clear error**. A config generator that emits `proxySettings` will
fail at engine start on every platform. The replacement is `streamSettings.sockopt.dialerProxy`,
documented as:

> *"An outbound identifier. When non-empty, the specified outbound is used to establish the
> connection."* … *"This is commonly used to configure chained proxies."*

Semantically this is the same primitive as sing-box's `detour`, pointing the other way in the config
file but producing the same proxychains behaviour. Each outbound names the outbound that dials it;
chain the tags and you have your hop sequence.

**Crucially for the shared core: the chaining config is identical on all four platforms.** The
`outbounds` array your Dart generator produces is byte-for-byte portable. Only the `inbounds` block
and the fd/interface plumbing differ (§4.3).

**One conflict to encode in the generator:** Xray docs warn *"Do not use this feature together with
`dialerProxy`, because that prevents `happyEyeballs` from taking effect."* So do not emit
`happyEyeballs` on any chained outbound.

*Uncertainty:* I did not find an official 3-hop example in the Xray docs. The per-hop `dialerProxy`
pattern in §7 follows directly from the field's documented semantics and is what community configs
do, but **validate a 3-hop chain on-device before Phase 4.**

### 2.4 Protocol coverage — both cover D4 exactly, on every platform

Your D4 set is SOCKS5 + HTTP + Shadowsocks. Both engines cover all three as outbounds, and because
the protocol core is pure Go with no platform-specific code paths, **coverage is identical on
Android, Windows, Linux and macOS.** No protocol is available on one platform and not another.

Xray specifics, from the official docs:

- **Shadowsocks** — fields `address`, `port`, `method`, `password`, `email`, `level`. Methods:
  `2022-blake3-aes-128-gcm`, `2022-blake3-aes-256-gcm`, `2022-blake3-chacha20-poly1305`,
  `aes-256-gcm`, `aes-128-gcm`, `chacha20-poly1305`, `xchacha20-poly1305`. For SS2022 the password is
  a base64 pre-shared key of 16 bytes (aes-128) or 32 bytes (the other two).
- **SOCKS** — fields `address`, `port`, `user`, `pass`, `level`, `email`. Docs note *"Only connections
  to Socks 5 servers are supported"* and *"The Socks protocol does not encrypt transmission and is not
  suitable for transmission over the public internet."*
- **HTTP** — fields `address`, `port`, `user`, `pass`, `level`, `email`, `headers`. Same no-encryption
  warning.

Full Xray outbound list: `blackhole`, `dns`, `freedom`, `http`, `loopback`, `shadowsocks`, `socks`,
`trojan`, `vless`, `vmess`, `hysteria`, `wireguard`.

> **Config-shape caveat — resolve this with a spike.** The current xtls.github.io pages show
> **flattened** settings (`settings.address`, `settings.port`, …). Older V2Ray-lineage configs use a
> nested array (`settings.servers[0].address`, with `users[]` for credentials). I saw both forms in
> circulation and could **not** definitively establish which the pinned version accepts.
> **Generate one config of each shape and feed them to the real engine before writing the
> generator.** A 20-minute test that prevents a week of confusing failures — and it only has to be
> done once, because the generator is shared.

### 2.5 UDP — the hazard nobody in the chain-builder UI will expect

This is the most important capability finding in the document, it is **not** in `architecture.md`,
and it applies identically on all four platforms.

| Hop protocol | Carries UDP? |
|---|---|
| Shadowsocks | **Yes** — docs: *"Shadowsocks forwards both TCP and UDP"* |
| SOCKS5 | **Yes** — I read `proxy/socks/client.go` on `main`: it sets `protocol.RequestCommandUDP` when `destination.Network == net.Network_UDP` and sets up `UDPWriter`/`UDPReader` over a dialed relay conn. (Whether a given commercial SOCKS5 server actually honours UDP ASSOCIATE is a separate matter — many do not.) |
| **HTTP** | **No.** Xray docs, verbatim: *"`http` can only proxy the TCP protocol; UDP-based protocols cannot pass through."* |

**Consequence:** a chain is only as UDP-capable as its weakest hop. The worked example in §7
(SS → SOCKS5 → HTTP) **cannot carry UDP at all**, because the exit hop is HTTP.

What breaks when UDP cannot traverse the chain:

- **QUIC / HTTP3** — a large and growing share of web traffic. These usually fall back to TCP after a
  timeout, which users perceive as "the app is broken and slow" rather than "QUIC is blocked".
  **This is worse on desktop**, where browsers are the dominant workload and Chrome/Edge/Firefox all
  prefer QUIC.
- **Plain UDP DNS** — must be forced to TCP (§8).
- Games, VoIP, WebRTC — simply fail. Again a bigger deal on desktop.

**Design requirement for the chain builder (shared Dart, all platforms):**

1. Compute a `supportsUdp` property per chain: `true` only if **every** hop supports UDP.
2. If `false`, both (a) blackhole UDP in routing so it fails fast and apps fall back to TCP
   immediately, **and** (b) show an explicit "This chain is TCP-only — video calls and some games
   will not work" warning. Silent blackholing generates support tickets; timeout-then-fallback
   generates worse ones.
3. Enforce the same rule server-side so a free-tier user cannot assemble a chain the UI would have
   warned about.

This is also an argument for **always making hop 1 Shadowsocks** (as `architecture.md` already
suggests for encryption reasons) and **preferring SOCKS5 over HTTP for the exit hop** wherever the
provider offers both.

### 2.6 DNS handling

Both engines have a full DNS module. Xray's, from the official docs:

- The `dns` object takes `hosts`, `servers`, `queryStrategy` (`UseIP` | `UseIPv4` | `UseIPv6` |
  `UseSystem`), `disableCache`, `clientIp`, `tag`, and more.
- `DnsServerObject.address` accepts plain IP, `tcp://`, `https://`, `h2c://`, plus `tcp+local://`,
  `https+local://`, `quic+local://`, `localhost`, and `fakedns`.
- **Routing behaviour is the key bit.** Non-local modes (plain IP, `tcp://`, `https://`) enter the
  routing system as internal requests carrying `inboundTag` = the `dns.tag` value, so **they can be
  routed through a proxy outbound**. `*+local://` modes **bypass routing** and dial via `freedom` —
  i.e. **straight out of the device**. `localhost` uses the OS resolver, which Xray does not control.

> `tcp+local://`, `https+local://` and `localhost` are **DNS leaks by design**. The shared generator
> must never emit them, on any platform. See §8.

Xray's TUN README is candid about the problem: because TUN traffic is IP-to-IP, hostname resolution
happens *before* packets reach the interface, so *"Router DNS queries can expose destinations unless
routed through Xray."*

sing-box's DNS module is comparable and arguably has a nicer rule engine plus mature FakeDNS. This is
a genuine point in sing-box's favour; it is not worth GPLv3 on four products.

### 2.7 Maintenance activity — both healthy, neither is a bus-factor concern

- **sing-box:** v1.14.3 stable on 2026-10-09; 1.15.0-alpha.12 on 2026-10-11; seven releases between
  16 Sep and 11 Oct 2026. 1.14.0 added OpenVPN client/server, OpenConnect, Snell, and an API service
  with dashboard.
- **Xray-core:** v26.10.10 on 2026-10-10, with v26.9.30, v26.9.9/8, v26.7.28 before it. CalVer.
- **libXray:** mirrors Xray-core releases, CalVer with SemVer mirror tags for Go consumers.

Neither project is at risk of abandonment. Maintenance is **not** a differentiator.

### 2.8 Android library maturity — **this is where sing-box genuinely wins**

Be honest about the trade you are making:

| | sing-box / libbox | Xray / libXray |
|---|---|---|
| Reference client | **SFA/SFI/SFM/SFW**, open source, show full platform wiring | v2rayNG and others exist but are **GPLv3** — do not read them for implementation patterns you intend to ship closed-source |
| Platform abstraction | `PlatformInterface` with `OpenInterface`, `AutoDetectInterfaceControl(fd)`, default-interface monitoring | `Invoke(requestJSON) string` — a single JSON-in/JSON-out entry point |
| API stability | reasonably stable across minor versions | README: *"This repository does not guarantee API stability, you need to adapt it yourself"* and *"only compatible with the latest release of Xray-core"* |
| TUN maturity | years in production on all platforms | **merged 2026-01-07**, lightly documented |

> **The GPLv3 reference clients are a real cost.** SFA/SFM would have been readable blueprints for
> the Android and macOS layers. With Xray you must build from the libXray README and Xray-core's own
> `proxy/tun/README.md` — both upstream-authored under permissive/MPL terms — and from Apple/Microsoft
> platform documentation. **Budget extra time in the first platform-layer implementation of each OS.**
> This is the price of the licence, and it is worth paying.

libXray's API surface is narrow but adequate and, importantly, **uniform**:

- `func Invoke(requestJSON string) string` — single entry point, `apiVersion: 3` (fixed).
- Methods include `runXray`, `stopXray`, `pingBatch`, `testXray`, `generateAgeKeyPair`.
- C exports `CGoInvoke` / `CGoFree`; every non-null response must be freed with `CGoFree`.
- `SetDNS` / `ResetDNS` and `registerProcessFinder` — **Android-only**.
- **`SetTunFd` has been removed.** The fd is passed via `xray.tun.fd` in the config root `env`.
- Hard constraint: *"Use only one Go runtime per process."* Do not load libXray alongside another
  independently built Go, cgo or gomobile library in the same process. **This binds on every
  platform** where the engine is in-process (Android, macOS).

### 2.9 Where sing-box would have been the better engineering choice

For the record, so nobody revisits this decision without the full picture:

- More mature TUN implementation on every platform, with years of production use.
- Open-source reference clients for Android, macOS, Windows and iOS you could read for architecture.
- A documented `protect()` hook instead of one you have to add yourself.
- `include_package` / `exclude_package` for per-app routing inside the engine.
- Better-documented DNS rule engine and FakeDNS.

**None of that survives contact with GPLv3 §5(c) across four commercially distributed products.**
Choose Xray.

---

## 3. Platform integration layer — TUN acquisition and privileges

This is the part that genuinely differs per OS, and it is where most of the per-platform engineering
cost lives. The engine core is portable; **this layer is not, and pretending otherwise is the main
way a shared-core project fails.**

### 3.1 Android — `VpnService`

| | |
|---|---|
| TUN mechanism | `VpnService.Builder.establish()` → `ParcelFileDescriptor` |
| Privilege model | **User consent dialog**, no elevation. `VpnService.prepare()` returns an Intent the user approves once. |
| Engine placement | **In-process**, gomobile `.aar` |
| fd handoff | `xray.tun.fd` in config root `env` |
| Helper process | none |

The cheapest privilege model of the four: one consent dialog, no admin, no driver, no signing
ceremony. This is a good reason Android is the foundation. Full detail in §5.

### 3.2 Windows — Wintun, and an elevated service

| | |
|---|---|
| TUN mechanism | **Wintun** (`wintun.dll`, loaded at runtime by Xray's `tun` inbound) |
| Privilege model | **Administrator at minimum; possibly LocalSystem** — see below |
| Engine placement | **Separate process** (`xray.exe`), run by an elevated helper service |
| fd handoff | none — the engine creates its own adapter; driven by `-interface <name>` and `-config` |
| Helper process | **required** — a Windows service running as LocalSystem |
| DNS leak control | `autoSystemWfpBlockLeak` (Windows Filtering Platform) |

**Wintun vs WFP — they are not alternatives.** Wintun is the virtual *adapter* (it gives you packets).
WFP is a *filtering* layer (it blocks packets). Xray uses Wintun for the TUN inbound and WFP only for
DNS leak blocking. There is no supported WFP-only design that gives you a TUN, so **Wintun is not
optional** on Windows.

**The privilege question is genuinely unsettled and you must test it.** The sources conflict:

- WireGuard's Jason Donenfeld originally wrote that *"you must be Local System, which is already the
  case if you're running inside a service"*, and suggested token-stealing for plain Administrator
  processes.
- He later clarified that *"the code to do automatic elevation is part of wintun.dll"*, and at least
  one developer reported Administrator being sufficient in practice.
- OpenVPN forum reports still show *"Wintun requires SYSTEM privileges and therefore should be used
  with interactive service"*, and OpenVPN's own recommendation is to let its GUI handle Wintun
  initialisation.
- Wintun's own README says to *"open the device as Local System"* after creating it.

> **Design for LocalSystem.** Even if Administrator turns out to be enough, you need a service
> anyway: a Flutter UI should never run elevated, and users will not accept a UAC prompt on every
> connect. **Verify the actual requirement on Windows 10 and 11 during the Windows spike**, but build
> the service regardless.

**Recommended Windows shape:**

```
ProxyCrack.exe (Flutter UI)          — normal user, no elevation, ever
      │  named pipe (ACL'd to the installing user's SID)
      ▼
ProxyCrackService.exe                — Windows service, LocalSystem, installed once by the installer
      │  spawns + supervises as a child process
      ▼
xray.exe  -interface ProxyCrack0 -config %PROGRAMDATA%\ProxyCrack\config.json -dns 127.0.0.1:5353
      │  loads at runtime
      ▼
wintun.dll                           — creates the virtual adapter
```

Notes:

- **Install the service at install time, with the one UAC prompt the installer already needs.**
  Never elevate at runtime.
- **ACL the named pipe** to the installing user's SID. An unauthenticated local IPC endpoint on a
  LocalSystem service that can reconfigure all network traffic is a privilege-escalation
  vulnerability, not a design detail. Validate every message; never let the UI pass a raw config
  path or raw command-line arguments through to the service.
- **Write the config to a directory only LocalSystem and Administrators can write.** If a normal
  user can rewrite `config.json`, they can redirect all system traffic. `%PROGRAMDATA%` with a
  tightened ACL, not `%APPDATA%`.
- **Code-sign the binaries.** Unsigned services installing network drivers trigger SmartScreen and
  most endpoint-protection products. Budget for an EV or OV code-signing certificate.
- **Antivirus false positives are near-certain** for a signed-but-new binary that installs a network
  adapter and proxies all traffic. Budget time for submissions to Microsoft and the major AV vendors,
  and expect this to recur on each release until reputation accrues.

### 3.3 Linux — TUN/TAP, `CAP_NET_ADMIN`, systemd

| | |
|---|---|
| TUN mechanism | `/dev/net/tun`, `ioctl(TUNSETIFF)` |
| Privilege model | **`CAP_NET_ADMIN`** — root is sufficient but not required |
| Engine placement | **Separate process** (`xray`), run by a systemd service |
| fd handoff | none — `-interface <name>` and `-config` |
| Helper process | **required** — a systemd **system** service |
| DNS leak control | `autoSystemDnsToGateway` — **requires systemd-resolved** |

**Confirmed:** `ioctl(TUNSETIFF)` requires `CAP_NET_ADMIN`. The best-practice shape is a systemd
**system** unit with a dedicated unprivileged user plus ambient capabilities:

```ini
[Service]
User=proxycrack
Group=proxycrack
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
ExecStart=/usr/lib/proxycrack/xray run -interface proxycrack0 -config /etc/proxycrack/config.json -dns 127.0.0.1:5353
```

**Do not use a systemd *user* unit.** `AmbientCapabilities` is documented as available *"only ... for
system services, or for services running in per-user instances of the service manager in which case
`PrivateUsers=` is implicitly enabled"* and requires unprivileged user namespaces. Developers hit
status-218 `CAPABILITIES` failures on user units. Use a system unit.

Two fallbacks for non-systemd distros:

- `setcap cap_net_admin=eip /usr/lib/proxycrack/xray` — simple, but the binary then carries
  network-admin power generally. Drop the capability after the TUN is set up if you control the code.
- Pre-create a persistent TUN owned by the service user:
  `ip tuntap add dev proxycrack0 mode tun user proxycrack`. Lets the daemon run fully unprivileged.

**The non-systemd DNS gap is real.** `autoSystemDnsToGateway` requires systemd-resolved and, per
Xray's TUN README, **refuses to start rather than leak DNS** when its preconditions are unmet. Good
failure behaviour, but it means Alpine, Void and some Gentoo configurations need either a bespoke
`/etc/resolv.conf` path or an explicit "unsupported" statement. **Decide which, and say so in the
docs.** Silently shipping a build that will not start is worse than not shipping it.

Also: the UI talks to the daemon over a **Unix domain socket** with filesystem permissions
(`0660`, group `proxycrack`), not a TCP port. The same injection cautions as Windows apply.

#### 3.3.1 LD_PRELOAD / proxychains-style mode — offer it, but as a clearly-labelled lesser mode

The classic `proxychains` approach replaces libc's socket calls via `LD_PRELOAD`. It is attractive
because it needs **no privileges at all** — no root, no capability, no TUN, no daemon.

**It is also fundamentally limited, and you must be honest about that:**

- **It only works on dynamically linked ELF binaries.** Statically linked programs never consult the
  dynamic loader.
- **It does not work on Go programs**, which bypass libc and make raw syscalls. In 2026 that excludes
  an enormous amount of modern tooling — Docker, kubectl, Terraform, gh, and much else.
- **It is ignored for setuid binaries**, by loader design, for obvious security reasons.
- It is per-command (`proxycrack-exec curl …`), not system-wide — which is the opposite of your D2
  "behave like proxychains for *all* apps" requirement.

**Recommendation:** ship it as a **secondary "per-command mode"** on Linux, clearly labelled, with
its limitations stated in the UI — *not* as a substitute for the TUN mode. It genuinely helps the
subset of Linux users who cannot or will not install a privileged daemon (locked-down corporate
machines, shared servers, containers), and it costs little because it reuses the same engine: the
shim just points at a local SOCKS5 inbound that `xray` already exposes.

> **Do not write the `LD_PRELOAD` shim yourself, and do not bundle `proxychains-ng` — it is GPLv2.**
> The cheap, licence-clean path is: run `xray` unprivileged with a plain `socks` **inbound** on
> localhost, then set `ALL_PROXY` / `HTTP_PROXY` / `HTTPS_PROXY` for the launched command. That
> covers most CLI tools (including Go ones, which honour the proxy env vars), needs no preloading at
> all, and has none of the limitations above. **Prefer the env-var approach; treat a real
> `LD_PRELOAD` shim as a later enhancement if users ask for it.**

### 3.4 macOS — the most expensive platform, by a wide margin

| | |
|---|---|
| TUN mechanism | NetworkExtension `NEPacketTunnelProvider`, **or** raw `utun` via `PF_SYSTEM` socket |
| Privilege model | NE: entitlement + notarisation. Raw utun: **root helper** |
| Engine placement | **In-process** inside the extension/helper (`LibXray.xcframework`) |
| fd handoff | NE gives a `packetFlow`; raw utun gives a socket fd |
| Helper process | **required** either way (sysex or `SMAppService` daemon) |
| DNS leak control | **no Xray automation** — use `NEDNSSettings` or `scutil` from the helper |

**Two paths, and they are not equivalent.**

**(a) NetworkExtension — Apple's supported path.** An Apple DTS engineer stated that while raw utun
is *"a supported API, DTS has stopped supporting ad hoc VPN clients built on it in favour of Network
Extension providers."* NE gives you per-app rules, `NEDNSSettings`, on-demand rules, and system VPN
UI integration.

Requirements and frictions:

- The **Network Extension entitlement**. Good news: reports from 2026 indicate the general NE
  entitlement **no longer requires Apple approval** for paid developers — a DTS engineer said there
  *"is no approval process"* for the content-filter entitlement and that this has been so *"for almost
  10 years now"*. Specific sub-entitlements (`app-push-provider`, HotspotHelper) still need a request.
  **Verify the exact entitlement your packet-tunnel provider needs against Apple's current docs —
  I could not retrieve Apple's live entitlement page, so treat "no approval needed" as likely but
  unconfirmed.**
- **Packaging differs by channel.** Per Apple DTS: for Developer ID (outside the Mac App Store) you
  *"must package your NE providers as a sysex"*; *"you can only use appex packaging when distributing
  on the App Store."* **You will need two macOS builds** if you want both channels — exactly the split
  AdGuard VPN ships.
- Apple Developer Program membership, code signing, **notarisation**, and a system extension approval
  prompt the user must accept in System Settings.

**(b) Raw `utun` + privileged helper.** A utun is created by connecting to a `PF_SYSTEM` socket, which
*"needs more privileges than an unprivileged user"* — so you need a root helper registered via
`SMAppService` (modern) or `SMJobBless` (legacy). This avoids the NE entitlement entirely, but Apple
has explicitly deprioritised support for it, you lose `NEDNSSettings` and per-app rules, and you are
on your own when macOS changes behaviour.

**Recommendation: NetworkExtension, Developer ID + sysex packaging for v1.** Revisit a Mac App Store
appex build only if store distribution becomes a commercial requirement.

**Known macOS operational hazards** (from Apple forum reports): utun interfaces are not always cleaned
up on disconnect — one developer saw two utuns created per connect with only one removed, and DNS
failures once roughly 95 had accumulated. **Test connect/disconnect cycling explicitly**, a few
hundred iterations, and assert the interface count returns to baseline.

> **`memory.md` §6 constraint stands and now binds harder: macOS cannot be built from this Windows
> machine.** You need Mac hardware (or a cloud Mac / CI runner) for building, signing, notarising and
> testing. **Schedule macOS last** and budget for the hardware plus a $99/yr Apple Developer Program
> membership before that phase starts, not during it.

### 3.5 Privilege-model summary

| | Android | Windows | Linux | macOS |
|---|---|---|---|---|
| Elevation needed | no (consent dialog) | **yes** — LocalSystem service | **yes** — `CAP_NET_ADMIN` | **yes** — sysex or root helper |
| Installed once or per-run | per-run consent (once) | once, at install | once, at install | once, user-approved in System Settings |
| Driver install | no | **yes — Wintun** | no | no |
| Signing ceremony | Play signing | **code-signing cert** | none (distro signing optional) | **Developer ID + notarisation** |
| Engine in-process? | yes | no | no | yes |
| Relative build cost | **1x (baseline)** | 2x | 1.5x | **4x** |

The cost column is the planning message: **Android first (it is cheapest and proves the core),
then Linux, then Windows, then macOS.** Do not start macOS until the shared core has been proven on
two platforms, because macOS adds hardware, entitlement and notarisation variables on top of
everything else.

---

## 4. Shared-core architecture

### 4.1 Where the boundaries go

Four layers. The rule is that **each layer only knows about the layer directly below it**, and
platform knowledge is confined to exactly one of them.

```
┌─────────────────────────────────────────────────────────────┐
│ L1  Flutter UI + domain            100% shared Dart         │
│     chain builder, paywall, proxy browser, settings         │
├─────────────────────────────────────────────────────────────┤
│ L2  ProxyEngine abstraction        100% shared Dart         │
│     abstract interface + config generator + capability model│
│     app/lib/core/routing/                                   │
├─────────────────────────────────────────────────────────────┤
│ L3  Platform transport             ~150 lines Dart per OS   │
│     how L2 reaches L4: MethodChannel vs named pipe vs       │
│     Unix socket vs XPC                                      │
├─────────────────────────────────────────────────────────────┤
│ L4  Privileged platform layer      per-OS native            │
│     TUN acquisition, elevation, engine lifecycle            │
│     Kotlin / C++ service / systemd daemon / Swift sysex     │
├─────────────────────────────────────────────────────────────┤
│ L5  Engine core                    100% shared Go           │
│     Xray-core + libXray — identical config on all platforms │
└─────────────────────────────────────────────────────────────┘
```

**The single most important boundary decision: L2 generates the complete engine config, including
the `outbounds`, `dns` and `routing` blocks, for every platform. L4 only ever edits the `inbounds`
block and the fd/interface plumbing.**

That one rule is what makes the core genuinely shared. The chain logic — the actual product — is
written once, in Dart, unit-tested without any device or VM, and is byte-identical on all four
platforms. Everything platform-specific is a small, well-defined mutation applied at the last moment.

Expected sharing, by volume:

| Layer | Shared | Per-platform |
|---|---|---|
| L1 UI + domain | ~100% | theming and window chrome only |
| L2 engine abstraction + config generator | ~100% | — |
| L3 transport | ~70% | ~150 lines of Dart each |
| L4 privileged layer | ~0% | **all of it** |
| L5 engine | 100% | build target only |

**Realistic overall: 75–85% of your own code is shared.** L4 is irreducible — it is OS API surface,
and no abstraction makes `VpnService`, a Windows service, a systemd unit and a macOS sysex the same
thing. Budget it as four separate pieces of work and do not let anyone promise otherwise.

### 4.2 What each platform implementation must satisfy

`app/lib/core/routing/` already has an abstract `ProxyEngine`. Here is the contract each platform
implementation must meet. **Keep it this small** — every method you add is four implementations.

```dart
abstract class ProxyEngine {
  /// Is the platform ready? Android: VpnService consent granted.
  /// Windows: service installed and running. Linux: daemon reachable.
  /// macOS: system extension approved and enabled.
  Future<EnginePrereqs> checkPrerequisites();

  /// Drive whatever one-time flow the platform needs. Android: consent dialog.
  /// Windows: start/repair the service. macOS: prompt for sysex approval.
  /// Linux: instruct the user to enable the unit. Idempotent.
  Future<bool> requestPrerequisites();

  /// Start the tunnel. [config] is the COMPLETE engine JSON from the shared
  /// generator; the implementation may only mutate `inbounds` and the
  /// fd/interface plumbing.
  Future<void> start(EngineConfig config);

  Future<void> stop();

  /// Hot-reload the chain without tearing the TUN down, if the platform can.
  /// Default implementation: stop() then start(). Override where cheaper.
  Future<void> reconfigure(EngineConfig config) async { await stop(); await start(config); }

  /// Authoritative state. MUST replay current state to every new listener.
  Stream<EngineState> get state;

  Stream<EngineStats> get stats;

  /// Capability probe. The UI reads this — it does NOT switch on platform.
  EngineCapabilities get capabilities;
}

class EngineCapabilities {
  final bool perAppRouting;       // Android: true. Windows/Linux: false. macOS: NE only.
  final bool killSwitch;          // Android: system lockdown. Desktop: ours to implement.
  final bool alwaysOn;            // Android + desktop service autostart.
  final bool processFinder;       // Android only (libXray registerProcessFinder).
  final bool systemDnsControl;    // Win: WFP. Linux: systemd-resolved only. macOS: NE. Android: n/a.
  final bool hotReload;
  final bool perCommandMode;      // Linux env-var/LD_PRELOAD mode only.
}
```

**Rules for this contract, in priority order:**

1. **The UI must never branch on `Platform.isWindows`.** It branches on `capabilities`. The day you
   write `if (Platform.isAndroid)` in a widget is the day the shared core starts dying. Enforce this
   in code review; a lint rule banning `dart:io` `Platform` outside `core/routing/` is cheap.
2. **Capabilities are declared, not inferred.** Each implementation states what it supports. Adding
   a fifth platform means writing one `EngineCapabilities` and one L4 layer, touching nothing else.
3. **No Android-only concept in the interface.** `VpnService`, `ParcelFileDescriptor`, consent
   Intents, `xray.tun.fd` — none appear above L4. The Android-only libXray calls (`SetDNS`,
   `ResetDNS`, `registerProcessFinder`) live entirely inside the Android L4.
4. **State is authoritative in L4 and replayed on subscribe.** A desktop daemon survives UI restarts;
   an Android service survives Activity recreation. In both cases the UI may attach to an
   already-running tunnel and must be told the truth immediately, not on the next change.
5. **`start()` takes the complete config.** L4 never constructs chain semantics. If you find L4 code
   building an `outbounds` array, the boundary has been breached — fix it immediately, because it
   will silently diverge across four platforms.

### 4.3 The one config difference across platforms

Everything in the generated config is identical except the `inbounds` block:

**Android / macOS (in-process, fd handoff):**

```json
"env": { "xray.tun.fd": "<runtime fd>" },
"inbounds": [ { "tag": "tun-in", "protocol": "tun", "port": 0,
                "settings": { "name": "", "mtu": 1500 } } ]
```

**Windows / Linux (child process, named interface):**

```json
"inbounds": [ { "tag": "tun-in", "protocol": "tun", "port": 0,
                "settings": { "name": "proxycrack0", "mtu": 1500,
                              "autoSystemRoutingTable": ["main"] } } ]
```

…with the interface name also passed as `-interface proxycrack0` on the command line.

**Implementation:** the shared generator emits the config with a `__TUN_INBOUND__` placeholder, and
each L4 substitutes its own block. One ~20-line method per platform. Unit-test the shared part once;
integration-test the substitution per platform.

### 4.4 Repository shape

```
D:\project\Proxy\
  app/                     Flutter — all four platforms, one project
    lib/
      core/routing/        L2 — abstract ProxyEngine, config generator, capabilities
        platform/          L3 — android.dart, windows.dart, linux.dart, macos.dart
      features/            L1 — shared UI
    android/               L4 — Kotlin VpnService
    windows/               L4 — C++ UI shell + named-pipe client
    linux/                 L4 — C++ UI shell + Unix-socket client
    macos/                 L4 — Swift host app
  native/                  L4 — the privileged components
    windows-service/       Windows service (LocalSystem)
    linux-daemon/          systemd unit + packaging
    macos-sysex/           NetworkExtension system extension
  engine/                  L5 — pinned engine artefacts, one per platform
    libXray.aar
    LibXray.xcframework/
    xray.exe  +  wintun.dll  (+ its licence file)
    xray
    BUILD.md               exact versions and build commands — MPL §3.2(a) evidence
  backend/                 unchanged
  Project/                 unchanged
```

**Keep the privileged components in `native/`, out of the Flutter tree.** They have their own build
systems, their own signing requirements, and their own release cadence. Flutter's platform folders
hold only the UI shell and the thin IPC client.

### 4.5 Build order

`architecture.md` §5 needs extending. Recommended sequence:

| Phase | Deliverable |
|---|---|
| 1 | Django models, admin, CSV upload, JWT auth, entitlement API *(unchanged)* |
| 2 | Flutter UI — auth, proxy browser, chain builder, paywall, **against `MockEngine`** |
| **2.5** | **Engine spike** — §10 items 1, 2, 4 on Android. Two days. **Gate on this.** |
| 3 | Android L4 + libXray `.aar`, single hop working |
| 4 | Chaining via `dialerProxy`, health checks, failover — **shared L2, benefits all platforms** |
| 5 | Play Billing, server-side receipt validation |
| 6 | Play VPN declaration, privacy policy, closed testing → **Android ships** |
| **7** | **Linux** — systemd daemon, `CAP_NET_ADMIN`, Unix socket. Cheapest second platform; proves the L2/L3/L4 boundary with a *separate-process* engine. |
| **8** | **Windows** — LocalSystem service, Wintun, named pipe, code signing, AV submissions |
| **9** | **macOS** — requires Mac hardware, Apple Developer Program, sysex, notarisation |

**Linux second, not Windows.** It is the cheapest way to prove that the separate-process engine model
and the L3/L4 boundary actually work, with no driver, no signing certificate, no antivirus politics
and no new hardware. Every bug it surfaces in the abstraction is a bug you would otherwise have found
on Windows while also fighting Wintun and SmartScreen. Windows is the bigger market; Linux is the
better *second* implementation.

---

## 5. Android integration plan (the foundation platform)

### 5.1 Obtaining the `.aar`

libXray builds with `gomobile`:

```bash
# min Android API level is 21
python3 build/main.py android

# use a local Xray-core checkout at ../Xray-core via a go.mod replace
python3 build/main.py android local

# skip snapshot/restore of go.mod and go.sum
python3 build/main.py android --no-snapshot
```

`LIBXRAY_GOMOBILE_VERSION` pins the gomobile module version (default resolves `latest` — **pin it**;
an unpinned toolchain is a reproducibility bug waiting to happen).

Prerequisites on this machine (status per `memory.md` §4):

| Need | Have |
|---|---|
| Go toolchain | **not yet installed** — add to `D:\dev\go` (no spaces, per D5) |
| `gomobile` + `gomobile init` | to install |
| `gcc`/`g++` on `PATH` | needed for the **Linux and Windows** engine targets |
| JDK 17 | yes — `D:\dev\jdk-17` |
| Android SDK + NDK | yes — `D:\dev\android-sdk`, NDK 28.2.13676358 |
| Python 3 | yes — 3.11.15 |
| `git` | yes |

> **Windows caveat.** The build driver is `build/main.py` and assumes a POSIX-ish environment. If it
> fights you, **do not burn days on it** — build engine artefacts in WSL2 or a Linux CI job and commit
> them. You need one set per engine version, not per code change. With four platforms this is
> emphatically a CI job: one pipeline producing `.aar`, `.xcframework`, `xray.exe` and `xray` from a
> single pinned commit, so all four products ship the same engine version.

**Pin and record.** Record in `engine/BUILD.md` and `memory.md`: the libXray version, the Xray-core
version it embeds, the Go version, the gomobile version, the Wintun version, and the exact build
command per target. **MPL §3.2(a) compliance depends on knowing precisely which Xray source
corresponds to each binary you shipped** — across four products, this bookkeeping is the compliance
artefact.

Android ABIs: `arm64-v8a` (essential), `armeabi-v7a` (older devices), `x86_64` (the `Pixel8_API36`
emulator — you need this to test at all). Drop `x86`.

### 5.2 Where it goes in the Flutter `android/` tree

```
app/
  android/
    app/
      libs/
        libXray.aar                 <- or referenced from ../../engine/
      src/main/kotlin/com/nenobot/proxy_crack/
        MainActivity.kt             <- registers channels, handles VpnService.prepare() result
        VpnConnectionService.kt     <- extends VpnService  (name kept from architecture.md §2)
        XrayBridge.kt               <- libXray Invoke() wrapper   (was SingBoxBridge.kt)
        VpnStateBus.kt              <- fan-out to the EventChannel
      src/main/AndroidManifest.xml
```

`android/app/build.gradle`:

```groovy
android {
    defaultConfig {
        minSdk 21            // libXray floor; raise to 24+ if you want fewer compat paths
        targetSdk 36         // MANDATORY — see §6.4
        ndk { abiFilters 'arm64-v8a', 'armeabi-v7a', 'x86_64' }
    }
    packagingOptions { jniLibs { useLegacyPackaging false } }
}
dependencies {
    implementation fileTree(dir: 'libs', include: ['*.aar'])
}
```

> Do **not** let the engine `.aar` leak into `lib/`. Every engine call goes through `core/routing/`
> (§4) — `architecture.md` §2 is right about this, and it is why this decision is only moderately
> expensive to reverse.

### 5.3 `VpnConnectionService.kt` responsibilities

The Kotlin service owns **everything Android**; the Go library owns **everything network**.

1. **Consent.** `VpnService.prepare(context)` returns an `Intent` if consent is needed, `null` if
   already granted. The Intent must be launched from an **Activity** (`MainActivity`); the result
   arrives in `onActivityResult`. The service cannot do this itself.
2. **Foreground + notification.** `startForeground()` with `FOREGROUND_SERVICE_TYPE_SPECIAL_USE`
   immediately in `onStartCommand()` — before any slow work. A non-dismissible notification is
   mandatory while connected (§6.5).
3. **Build the TUN.** `VpnService.Builder`:
   - `addAddress("10.0.0.2", 32)` and an IPv6 address if you route v6
   - `addRoute("0.0.0.0", 0)` (+ `::/0` if v6) — capture everything
   - `addDnsServer(...)` — point at an address the engine answers on (§8)
   - `setMtu(1500)` — but read §9.1 first
   - `setSession("Proxy Crack")`
   - `setBlocking(false)`
   - per-app rules: `addAllowedApplication` **or** `addDisallowedApplication`, never both
   - `establish()` → `ParcelFileDescriptor`, or `null` if consent was revoked (handle this)
4. **Hand the fd to the engine.** §5.4.
5. **Start the engine.** `XrayBridge.start(configJson)` → `Invoke()` with `runXray`.
6. **Keep the fd alive.** Hold the `ParcelFileDescriptor` reference for the whole session. If it is
   GC'd or closed, the tunnel dies silently.
7. **Network changes.** Register a `ConnectivityManager.NetworkCallback`; on default-network change
   call `setUnderlyingNetworks(arrayOf(newNetwork))` so Android attributes traffic and billing
   correctly, and restart the engine's dialers if needed.
8. **Teardown.** `stopXray` → close the `ParcelFileDescriptor` → `stopForeground` → `stopSelf`.
   **In that order.** Closing the fd first causes reads on a dead fd.
9. **Always-on / system-initiated start.** `onStartCommand` may be called by the system with no
   Intent of yours. Detect this (flag your own Intents; treat unflagged starts as system-initiated),
   load the last-good chain from persistent storage, and connect without any UI. See §9.6.
10. **Revocation.** Override `onRevoke()` — the user switched to another VPN. Tear down cleanly and
    push a state event so the UI does not sit on a stale "Connected".

### 5.4 Handing the TUN fd to the engine — and the loop problem

**The fd handoff.** Xray-core's `proxy/tun/README.md` is explicit: the app gets the fd from
`VpnService`, then sets the `xray.tun.fd` (or `XRAY_TUN_FD`) environment variable to the fd number
before starting Xray — *"can be done from Kotlin/Java or by exposing a Go function via gomobile
bindings."* libXray's README says the same and notes `SetTunFd` has been **removed**: write
`xray.tun.fd` into the Xray config's root `env` object before `runXray`.

```kotlin
val pfd: ParcelFileDescriptor = builder.establish() ?: error("VPN permission revoked")
val fd = pfd.fd                                   // Int
val config = configJson.withTunInbound(fd)        // L4 substitutes __TUN_INBOUND__ (§4.3)
XrayBridge.runXray(config)
```

Confirmed in source: `proxy/tun/` on `main` contains `tun_android.go` and `tun_fd.go`, and the commit
that added this defines `TunFdKey = "xray.tun.fd"`. The same env mechanism is documented for iOS,
where the fd comes from NetworkExtension — **so the macOS L4 will use the identical mechanism**, which
is a real code-sharing win between the two in-process platforms.

> *Uncertainty, flag it loudly:* I verified `infra/conf/tun.go`'s `TunConfig` has fields `name`,
> `desc`, `mtu`, `gateway`, `dns`, `userLevel`, `autoSystemRoutingTable`, `autoOutboundsInterface`,
> `autoSystemDnsToGateway`, `autoSystemWfpBlockLeak` — and **no `fd` field**, consistent with the fd
> arriving via `env`. But I could **not** verify the exact JSON shape of the root `env` object against
> Xray's config parser. **Spike this first.** If root `env` turns out not to exist in your pinned
> version, the fallback is a one-line addition to your libXray fork calling
> `os.Setenv("xray.tun.fd", ...)` before core start.

#### The routing loop — the single biggest technical risk in this plan

Xray's TUN README warns directly: *"Plainly enabling it in the config probably will result nothing, or
lock your router up in infinite network loop"*, because Xray's own uplink connections would be routed
back into the interface it is serving. **This hazard exists on all four platforms**; only the fix
differs. On desktop the engine runs under a different user (service account), which helps, but you
still need an explicit exclusion route for the hop-1 server address — Xray's README suggests exactly
that (*"static routes to uplink addresses"*).

On Android, your app's sockets — including the engine's connection to hop 1 — go through the TUN
unless something excludes them. Three options:

**(a) `addDisallowedApplication(BuildConfig.APPLICATION_ID)` — simplest, start here.**
Excludes your whole process from the VPN, so engine sockets and your Django API calls both bypass the
tunnel. This is what most Android proxy clients do. Cheap, no native work.
**Known failure mode:** under always-on VPN with *"Block connections without VPN"* (lockdown), the
Android VPN guide states apps *"that aren't in an allowed list, or are in a disallowed list"* lose
network access. If that applies to your own app, lockdown would cut the engine off and the tunnel
could never come up. **Test this explicitly on-device — it is the difference between shipping a kill
switch and not.**

**(b) `VpnService.protect(fd)` on each engine socket — correct, and available.**
This is the mechanism designed for exactly this problem and it survives lockdown. libXray does **not**
expose a protect hook. **Xray-core does.** I verified in `transport/internet/system_dialer.go` on
`main`:

```go
func RegisterDialerController(ctl func(network, address string, c syscall.RawConn) error) error
```

with the controller list applied via `c.Control(func(fd uintptr) { ... })` before connect. So:

> **Fork libXray (MIT — free to do, free to keep private) and add roughly 30 lines:** a
> gomobile-exported interface the Kotlin side implements, registered through
> `internet.RegisterDialerController`, whose callback does
> `c.Control { fd -> vpnService.protect(fd.toInt()) }`.
> **This is the MIT licence paying for itself.** Note you are modifying *libXray* (MIT, no obligation),
> not *Xray-core* (MPL, would trigger §3.2) — keep it that way. The same `RegisterDialerController`
> hook is available on desktop if you ever need source-address control there, so the fork pays off
> more than once.

**(c) `sockopt.interface` / `sockopt.mark`** — do not bother on Android. `SO_BINDTODEVICE` and
`SO_MARK` require `CAP_NET_ADMIN`, which an unprivileged Android app does not have. *(On Linux, where
the daemon **does** hold `CAP_NET_ADMIN`, `sockopt.mark` becomes a genuine option for loop avoidance —
a rare case where desktop has the easier path.)*

**Recommendation:** implement **(a)** for the Phase 2.5 spike to get a single hop working fast, then
implement **(b)** before Phase 4. Plan for (b); do not ship a kill switch without it.

### 5.5 Flutter to Kotlin (the Android L3)

Two channels. Commands go down a `MethodChannel`; state comes up an `EventChannel`. Do not poll state
over the MethodChannel — you will reinvent an event stream badly.

```
MethodChannel : com.nenobot.proxy_crack/vpn
  prepare()                  -> Bool      // true = consent already granted
  requestPermission()        -> Bool      // launches the consent Intent, completes on result
  start(configJson: String)  -> Bool
  stop()                     -> Bool
  status()                   -> String    // one-shot, for cold start / app resume
  stats()                    -> Map       // bytes up/down, uptime, per-hop latency

EventChannel  : com.nenobot.proxy_crack/vpn_events
  { "state": "idle|preparing|connecting|connected|reconnecting|error|revoked",
    "hop": 2, "hopTotal": 3,
    "bytesUp": 123456, "bytesDown": 654321,
    "error": null }
```

**Design this message shape once and reuse it verbatim on desktop**, where the same JSON travels over
a named pipe (Windows), a Unix socket (Linux) or XPC (macOS) instead of a platform channel. The L3
implementations differ in transport; the protocol does not. That is most of why L3 is ~70% shared.

Flow for the consent dance (the one place people get this wrong):

1. Dart calls `prepare()`.
2. Kotlin runs `VpnService.prepare(context)`. `null` means already consented — return `true`.
3. Non-null → Dart calls `requestPermission()`; `MainActivity` launches the Intent with
   `startActivityForResult`.
4. `MainActivity.onActivityResult` checks the request code and `RESULT_OK`, then completes the
   pending `MethodChannel.Result`.
5. Dart calls `start(configJson)`.

Keep the pending `Result` on the Activity, not the Service — the Activity can be recreated on rotation
mid-dialog, so guard against a stale or duplicate completion (completing a `Result` twice throws).

**`EventChannel` lifecycle:** the stream only exists while Dart is listening. The service must keep
authoritative state in `VpnStateBus` and replay the current state on `onListen`, or the UI shows
"idle" every time the user returns to a connected app from the background. **The same replay rule is
contract item 4 in §4.2 and applies to every platform.**

### 5.6 The Dart side

```
lib/core/routing/
  proxy_engine.dart          L2 — the abstract contract from §4.2
  engine_capabilities.dart   L2 — the capability model
  xray_config_builder.dart   L2 — ProxyChain -> Xray JSON (shared, platform-free)
  chain_capabilities.dart    L2 — supportsUdp, estimatedLatency, validate()
  mock_engine.dart           L2 — for UI work with no native dependency
  platform/
    android_engine.dart      L3 — MethodChannel/EventChannel
    windows_engine.dart      L3 — named pipe
    linux_engine.dart        L3 — Unix socket
    macos_engine.dart        L3 — XPC / MethodChannel
```

**Build the config JSON in Dart, not in any L4.** Three reasons, all stronger now that there are four
L4s: it is testable without any device or VM (pure unit tests over `ProxyChain → JSON`); the chain
model already lives in Dart; and when the Xray config shape shifts on a version bump (§2.4), you fix
it in **one** Dart file with tests around it, rather than in four native codebases with none.

---

## 6. AndroidManifest requirements

### 6.1 Permissions

```xml
<!-- Foreground service: base + the Android 14+ type-specific permission -->
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />

<!-- Notifications: runtime permission since Android 13 (API 33) -->
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />

<!-- Only if you support auto-connect at boot. See §9.6 before adding. -->
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
```

Note: `BIND_VPN_SERVICE` is **not** a `<uses-permission>`. It is an `android:permission` attribute on
the `<service>` element — it restricts who may bind to your service (only the system), rather than
granting your app anything. Getting this backwards is a common and confusing bug.

### 6.2 Service declaration

```xml
<service
    android:name=".VpnConnectionService"
    android:permission="android.permission.BIND_VPN_SERVICE"
    android:foregroundServiceType="specialUse"
    android:exported="true">

    <intent-filter>
        <action android:name="android.net.VpnService" />
    </intent-filter>

    <!-- Required when foregroundServiceType="specialUse".
         Reviewed by Google Play at submission - write it for a human reviewer. -->
    <property
        android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
        android:value="Maintains the user-initiated VPN tunnel and the multi-hop proxy chain carrying all device traffic. The service must stay alive for the entire duration of the connection; terminating it silently drops all network traffic for every app on the device." />

    <!-- Opt OUT of always-on. Omit this element entirely to SUPPORT always-on. -->
    <!-- <meta-data android:name="android.net.VpnService.SUPPORTS_ALWAYS_ON" android:value="false" /> -->
</service>
```

`android:exported="true"` with `android:permission="android.permission.BIND_VPN_SERVICE"` is the
correct and required combination: the system must be able to reach it, and the permission ensures only
the system can.

### 6.3 `specialUse` vs `systemExempted` — use `specialUse`

The Android 14 FGS-types page lists VPN apps under `systemExempted`, but narrowly: *"VPN apps
configured in Settings > Network & Internet > VPN"*. That describes the **always-on / system-configured**
case, not an app starting its own tunnel from its own UI. Declaring `systemExempted` when you do not
qualify causes `ForegroundServiceTypeNotAllowedException`.

Observed practice supports `specialUse`: OpenVPN for Android and TunnelForge both declare it.

> *Uncertainty:* I did not find Google documentation that settles this for a third-party VPN that is
> *also* registered as always-on. It is possible the correct behaviour is `specialUse` normally and
> `systemExempted` when system-started. **Test both paths on an Android 14+ device.** If you need both,
> you can pass different type constants to `startForeground()` at runtime as long as both are declared
> in the manifest.

Runtime call:

```kotlin
ServiceCompat.startForeground(
    this, NOTIFICATION_ID, notification,
    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
)
```

Failure modes: missing manifest type gives `MissingForegroundServiceTypeException`; missing matching
permission gives `SecurityException`. `FOREGROUND_SERVICE_SPECIAL_USE` is a normal permission, granted
at install, not revocable by the user.

### 6.4 Target SDK — act now, the deadline has passed

Google Play requires **new apps and updates to target Android 16 (API 36)** as of **31 August 2026**.
Today is 15 October 2026, so this is already in force (an extension to 1 November 2026 was available on
request). `memory.md` §4 records platform 36 and build-tools 36.0.0 installed — good. Set
`targetSdk 36` in `android/app/build.gradle` from the start, and validate FGS and notification
behaviour on an API 36 device. `Pixel8_API36` is already provisioned.

### 6.5 The persistent notification

Not optional. Requirements:

- Its own `NotificationChannel`, `IMPORTANCE_LOW` (no sound, no heads-up).
- `setOngoing(true)` so it is non-dismissible while connected.
- `setContentIntent(...)` opening the app's connection screen.
- An explicit **Disconnect** action — users expect to kill a VPN from the shade. Not providing one
  reads as malware.
- Shows the active chain and hop count, e.g. "Connected — 3 hops · NL → DE → US".
- Request `POST_NOTIFICATIONS` at runtime on API 33+. **The foreground service still runs if denied**,
  but the notification is hidden and the UX degrades badly — ask during onboarding, with a reason,
  before the first connect.

*Desktop equivalents:* a tray/menu-bar icon with the same state and a Disconnect action on Windows,
Linux and macOS. Not an OS requirement there, but the same user expectation — and the same shared
state model (§4.2) drives all four.

---

## 7. Config generation — worked 3-hop example

Chain: **Shadowsocks (hop 1) → SOCKS5 (hop 2) → HTTP (hop 3) → target**.

Reading order for `dialerProxy`: hop 3 is the outbound that carries app traffic; it dials through
hop 2; hop 2 dials through hop 1; hop 1 dials direct. The **last** hop in the user's chain is the
**first** outbound in the array (it is the default outbound).

**Everything below except the `inbounds` block and `env` is identical on all four platforms.**

```json
{
  "env": {
    "xray.tun.fd": "__RUNTIME_FD__"
  },

  "log": { "loglevel": "warning" },

  "inbounds": [
    {
      "tag": "tun-in",
      "protocol": "tun",
      "port": 0,
      "settings": {
        "name": "",
        "mtu": 1500
      }
    }
  ],

  "outbounds": [
    {
      "tag": "hop3-http",
      "protocol": "http",
      "settings": {
        "address": "203.0.113.30",
        "port": 8080,
        "user": "u3",
        "pass": "p3"
      },
      "streamSettings": {
        "sockopt": { "dialerProxy": "hop2-socks" }
      }
    },
    {
      "tag": "hop2-socks",
      "protocol": "socks",
      "settings": {
        "address": "198.51.100.20",
        "port": 1080,
        "user": "u2",
        "pass": "p2"
      },
      "streamSettings": {
        "sockopt": { "dialerProxy": "hop1-ss" }
      }
    },
    {
      "tag": "hop1-ss",
      "protocol": "shadowsocks",
      "settings": {
        "address": "192.0.2.10",
        "port": 8388,
        "method": "2022-blake3-aes-256-gcm",
        "password": "BASE64_32_BYTE_PSK=="
      },
      "streamSettings": {
        "sockopt": {}
      }
    },

    { "tag": "direct",  "protocol": "freedom"   },
    { "tag": "block",   "protocol": "blackhole" },
    { "tag": "dns-out", "protocol": "dns"       }
  ],

  "dns": {
    "tag": "dns-internal",
    "queryStrategy": "UseIP",
    "disableFallback": false,
    "hosts": {},
    "servers": [
      "tcp://1.1.1.1:53",
      "tcp://8.8.8.8:53"
    ]
  },

  "routing": {
    "domainStrategy": "AsIs",
    "rules": [
      {
        "type": "field",
        "inboundTag": ["tun-in"],
        "port": 53,
        "outboundTag": "dns-out"
      },
      {
        "type": "field",
        "inboundTag": ["dns-internal"],
        "outboundTag": "hop3-http"
      },
      {
        "type": "field",
        "inboundTag": ["tun-in"],
        "network": "udp",
        "outboundTag": "block"
      },
      {
        "type": "field",
        "inboundTag": ["tun-in"],
        "outboundTag": "hop3-http"
      }
    ]
  }
}
```

### 7.1 Notes the generator must encode

1. **`hop1` has no `dialerProxy`.** It dials direct. Emitting one on the first hop creates a cycle;
   Xray may or may not detect it, and a silent hang is worse than an error.
2. **The chain reverses.** User-facing order is hop1 → hop2 → hop3. Config order is hop3 (default
   outbound) → hop2 → hop1. Unit-test this inversion; it is the easiest thing in the whole generator
   to get backwards.
3. **Never emit `proxySettings`.** Removed feature; now produces a startup error pointing at
   `streamSettings.sockopt.dialerProxy`.
4. **Never emit `happyEyeballs` on a chained outbound** — it conflicts with `dialerProxy`.
5. **The UDP block rule is conditional.** Only when the chain contains an HTTP hop (i.e.
   `chain.supportsUdp == false`). For an all-SS/SOCKS5 chain, omit it so UDP flows. Blocking with
   `blackhole` makes QUIC fail fast and fall back to TCP rather than hanging for seconds.
6. **`"port": 0` on the `tun` inbound** — per Xray's TUN README, the inbound never listens on a port.
7. **`__RUNTIME_FD__` and the whole `inbounds` block are substituted by L4** (§4.3). Everything else
   is generated once in shared Dart.
8. **Credentials never touch disk — and this is harder on desktop.** On Android the config goes over
   a MethodChannel in memory. On Windows and Linux the engine reads a **config file**, so credentials
   necessarily hit the filesystem. Mitigate: write it to a directory readable only by the service
   account (`%PROGRAMDATA%\ProxyCrack` with a tightened ACL; `/etc/proxycrack` mode `0600` owned by
   `proxycrack`), delete on disconnect, and never persist it across reboots. For always-on reconnect
   (§9.6), persist the *chain identifiers* and re-fetch credentials from the backend — never the
   credentials themselves. **Add "config file permissions" to the desktop release checklist.**
9. **Config-shape caveat applies.** See §2.4 — validate flat vs `servers[]` form against the pinned
   engine version before trusting this example.
10. **Scrub logs.** `"loglevel": "warning"`. On `debug`, Xray logs destination hostnames — a privacy
    incident in a VPN product. On desktop, also make sure the log file is not world-readable.

---

## 8. DNS leak prevention

A proxy product that leaks DNS has failed at its core promise. Layers 1–4 are the shared design;
§8.5 covers what differs per platform.

### Layer 1 — tell the OS the DNS server is inside the tunnel

Android:

```kotlin
builder.addDnsServer("1.1.1.1")     // or any address the engine answers on
builder.addRoute("0.0.0.0", 0)      // ensures packets to it enter the TUN
```

Desktop equivalents are in §8.5. In all cases the point is the same: the system resolver must address
queries to something that routes into the TUN.

### Layer 2 — hijack port 53 in routing

```json
{ "type": "field", "inboundTag": ["tun-in"], "port": 53, "outboundTag": "dns-out" }
```

The `dns` outbound pulls those queries into Xray's built-in DNS module rather than forwarding them to
whatever address they were sent to. This catches apps that hard-code `8.8.8.8` and ignore the system
resolver — which is most of the apps that cause leaks. **Platform-independent, and the most valuable
single rule in the config.**

### Layer 3 — route the DNS module's own upstream queries through the chain

```json
"dns":     { "tag": "dns-internal", "servers": ["tcp://1.1.1.1:53"] },
"routing": { "rules": [ { "type": "field", "inboundTag": ["dns-internal"], "outboundTag": "hop3-http" } ] }
```

This is the layer people forget. Hijacking queries into the DNS module achieves nothing if the module
then resolves them from the device's physical interface.

Per Xray's DNS docs, non-local server modes (plain IP, `tcp://`, `https://`) enter the routing system
tagged with `dns.tag`, so this rule catches them.

> **Use `tcp://`, not a bare IP.** Two reasons. (1) If your chain contains an HTTP hop, UDP cannot
> traverse it at all (§2.5) — a bare-IP server would attempt UDP, fail, and you would be debugging
> "no internet" for a day. (2) TCP DNS through a chain is reliable where UDP relaying through SOCKS5
> is at the mercy of every intermediate server's UDP ASSOCIATE support.
> `https://` (DoH) also works and encrypts the query to the resolver, at the cost of more RTTs on an
> already-slow chain. **Start with `tcp://`; consider DoH as a user-facing option.**

### Layer 4 — forbid the leaky forms in the generator

**Never emit** `tcp+local://`, `https+local://`, `quic+local://`, or `localhost` as DNS server
addresses. Per the Xray docs these **bypass routing** and dial via `freedom` — straight out of the
physical interface. `localhost` defers to the OS resolver entirely. Put an assertion in
`xray_config_builder.dart` that rejects any `dns.servers` entry matching `+local://` or `localhost`,
and a unit test that proves it. **Written once, protects all four platforms.**

### 8.5 What differs per platform

| | Mechanism | Notes |
|---|---|---|
| **Android** | `VpnService.Builder.addDnsServer()` | Clean. Plus libXray `SetDNS`/`ResetDNS` for the Go resolver (below). |
| **Windows** | `autoSystemWfpBlockLeak` | WFP blocks DNS to anything but the tunnel. The most robust of the four. |
| **Linux** | `autoSystemDnsToGateway` | **Requires systemd-resolved.** Off by default; **refuses to start rather than leak** if preconditions are unmet. Non-systemd distros need a bespoke `/etc/resolv.conf` path or must be unsupported — decide and document. |
| **macOS** | **none in Xray** | Use `NEDNSSettings` via NetworkExtension, or `scutil`/`networksetup` from the privileged helper. **Bespoke work — budget for it.** |

**libXray's `SetDNS` / `ResetDNS` are Android-only.** Their purpose: Android may expose a loopback DNS
server while a VPN is active; call `SetDNS` with an `IP:port` endpoint **before** `runXray` and
`ResetDNS` **after** Xray stops. They change the **process-wide Go resolver** — affecting how the Go
runtime resolves names (e.g. your hop hostnames), not how proxied app traffic resolves.
**Use IP addresses rather than hostnames for proxy hops wherever possible**, and this concern largely
evaporates — on every platform, which is the point.

### 8.6 Verification — do not trust the config, test it

Before any release, **on every platform you ship**:

1. Connect, then load `dnsleaktest.com` / `browserleaks.com/dns`.
2. The resolvers shown must be the chain's exit, never the carrier's or the local router's.
3. Repeat on **Wi-Fi and cellular** (mobile) and on **Ethernet and Wi-Fi** (desktop) — different
   default resolvers, different failure modes. Carrier DNS interception on cellular is common.
4. Repeat with IPv6 enabled. **Unrouted IPv6 is a leak path**: if you route only `0.0.0.0/0` and the
   host has working IPv6, v6-capable destinations bypass the tunnel entirely. Either route `::/0` into
   the TUN as well, or block v6 — never leave it unhandled. **Desktop machines are far more likely to
   have working IPv6 than phones**, so this matters more on Windows/Linux/macOS.
5. On Windows, verify with the system DNS set to a static third-party resolver — this is where WFP
   blocking earns its keep.
6. On Linux, verify on at least one systemd and one non-systemd distro, or state that non-systemd is
   unsupported.

Make this a manual release-checklist item, per platform. **There is no unit test for a DNS leak.**

---

## 9. Known hazards

Unless noted, these apply on all four platforms.

### 9.1 MTU

The TUN MTU (set by `VpnService.Builder.setMtu()` on Android, by the `tun` inbound's `mtu` on desktop)
and Xray's own `mtu` must **match**. A mismatch produces packet loss that looks like a flaky network.

The compounding problem: every hop adds encapsulation overhead. Shadowsocks adds AEAD framing; SOCKS5
UDP adds a header; each TCP hop adds its own. With 1500 at the TUN and three hops, outermost packets
exceed the physical path MTU and get fragmented or dropped. Path MTU Discovery does not reliably work
through proxy chains, so you get a black hole: small packets fine, large transfers stall. Classic
symptom — the connection "works" but downloads hang.

**Start at 1500** (Xray's own README example). **If large transfers stall, drop to 1400 then 1280.**
1280 is the IPv6 minimum and the safe floor. **Make MTU a remotely-tunable value from the backend, per
platform**, so you can fix a user's problem without shipping a new build — which matters far more when
"shipping a build" means a notarisation cycle or an AV re-submission.

### 9.2 UDP ASSOCIATE

Covered in §2.5. Operational additions:

- Many commercial SOCKS5 providers **advertise** UDP and do not implement UDP ASSOCIATE, or implement
  it only from the same IP that opened the TCP control connection (breaks behind CGNAT). Your health
  checker should probe UDP separately from TCP and store `supports_udp` on the `Proxy` model.
- Xray's SOCKS outbound dials the UDP relay through the dialer chain
  (`dialer.Dial(ctx, udpRequest.Destination())`), so a UDP association requires **every** upstream hop
  to carry it. With HTTP anywhere in the chain, it cannot.
- The SOCKS5 UDP control connection must stay open for the association's lifetime. Chains make this
  fragile — any hop dropping it silently kills UDP while TCP keeps working.
- **Desktop amplifies the user impact**: browsers prefer QUIC, and desktop workloads include more
  VoIP, conferencing and gaming than mobile.

**Backend change:** add `supports_udp` (boolean, nullable, set by the health-checker) to the `Proxy`
model in `architecture.md` §3.

### 9.3 Half-open connections

TCP allows one direction to close while the other stays open (`shutdown(SHUT_WR)`). Proxy chains handle
this badly: a hop that treats a one-way FIN as full teardown truncates the response. Classic symptoms —
truncated HTTP responses, `git clone` failing near the end, large POSTs cut short.

This is the one hazard where **you are relying entirely on the engine**, which is a good argument for
not writing your own: Xray's gVisor-based stack handles half-open correctly, and this is precisely the
class of bug `architecture.md` §1 is right to say you should not take on. Your job is to **recognise**
it in bug reports, not fix it: if a user reports "large uploads fail but browsing works", suspect
half-open or MTU before suspecting the proxy. **More likely to surface on desktop**, where `git`,
`scp`, `rsync` and large uploads are everyday traffic.

### 9.4 Battery, Doze, and desktop sleep

**Android:**

- A foreground service with a visible notification is **exempt from Doze app-standby**. Keep the
  notification visible.
- Android 15 restricts which FGS types a `BOOT_COMPLETED` receiver may start, throwing
  `ForegroundServiceStartNotAllowedException`. **I could not confirm whether `specialUse` is among the
  restricted types.** Verify before building boot-time auto-connect (§9.6).
- The Android 15 `dataSync` six-hours-per-24 timeout is another reason not to use `dataSync` for a VPN.
- Aggressive OEM battery managers (Xiaomi, Huawei, Samsung, OnePlus) kill foreground services
  regardless of Android's rules. Add onboarding pointing users to
  `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` and OEM autostart settings. **Budget support load** —
  it is the single largest source of "the VPN randomly disconnects" reports for every VPN app.

**Desktop:**

- **Sleep/resume and hibernate are the desktop equivalent.** Network interfaces disappear and
  reappear; the TUN may survive while every upstream connection is dead. Register for power
  notifications (`WM_POWERBROADCAST` on Windows, `systemd-logind` sleep inhibitors/signals on Linux,
  `NSWorkspace` sleep/wake notifications on macOS) and **tear down and re-establish the chain on
  resume**. Do not assume connections survive.
- Laptops roam networks constantly (Wi-Fi ⇄ Ethernet ⇄ hotspot). The desktop daemon needs the same
  network-change monitoring the Android service does.
- **Chain cost:** three hops means three sets of keepalives and three times the setup work on every
  transition. Tune `tcpKeepAliveInterval` / `tcpKeepAliveIdle` in `sockopt` rather than leaving
  defaults; defaults assume a datacentre.

### 9.5 Per-app split tunnelling

**Android** — do this with `VpnService.Builder`, not in the engine. Xray-core has no Android
package-routing feature (sing-box's `include_package`/`exclude_package` is one of its genuine
advantages).

- `addAllowedApplication()` — **only** listed apps use the VPN. Empty list means all apps.
- `addDisallowedApplication()` — listed apps bypass; everything else uses the VPN.
- **One or the other, never both.**
- Must be set **before** `establish()`. Changing the list requires tearing down and re-establishing —
  i.e. a reconnect. The UI must say so.
- Apps must already be installed; `PackageManager.NameNotFoundException` otherwise. Wrap each call.
- **Warn the user** when they change these lists while lockdown mode is on: apps not in an allowed
  list, or in a disallowed list, **lose network access entirely** under lockdown.

For finer routing *inside* the tunnel, libXray exposes
`registerProcessFinder(finder, Build.VERSION.SDK_INT)` — `ConnectivityManager.getConnectionOwnerUid()`
on API 30+, falling back to parsing `/proc/net` below that. The fallback is slow and increasingly
restricted; **do not build a core feature on it.** Android-only, as noted in §2.2.

**Desktop** — there is no clean cross-platform primitive:

- **Windows:** no supported per-process VPN routing for third-party apps. WFP callouts could do it but
  require a kernel driver. **Out of scope for v1.**
- **Linux:** possible via cgroup v2 net_cls or network namespaces, but bespoke and fragile.
  **Out of scope for v1.** The per-command mode (§3.3.1) covers much of the real need more simply.
- **macOS:** NetworkExtension supports per-app rules, but principally in the managed/App Store tier.
  **Out of scope for v1.**

> **Set this expectation now:** per-app split tunnelling is an **Android-only feature in v1**. Surface
> it through `EngineCapabilities.perAppRouting` (§4.2) so the UI hides it elsewhere rather than
> offering something that cannot work.

### 9.6 Kill switch and always-on

**Android.** Two distinct things:

*Always-on VPN* (API 24+) — a *system* setting the user enables in Settings. The system starts your
service at boot and restarts it if it dies.

- Support it by **omitting** the `SUPPORTS_ALWAYS_ON` meta-data. Only add
  `<meta-data android:name="android.net.VpnService.SUPPORTS_ALWAYS_ON" android:value="false" />` to
  opt **out**.
- Your service will be started with no Intent of yours and **no UI**. It must reconstruct the last good
  chain from persistent storage and connect headlessly. **Persist chain identifiers, not credentials**
  (§7.1 note 8) — so a headless start needs a valid cached auth token to re-fetch them. Design the
  token refresh for this path, or always-on silently fails for anyone whose token expired overnight.
  **The same problem exists for desktop autostart**, so solve it once in shared code.
- While system-started, disable in-app UI that would disconnect.

*Kill switch* = Android's *"Block connections without VPN"* (lockdown). You do not implement it; you
must **survive** it:

1. If you used `addDisallowedApplication(self)` for loop avoidance (§5.4a), your engine may lose network
   under lockdown and never connect. **This is the strongest argument for implementing the `protect()`
   hook (§5.4b) before advertising a kill switch.**
2. `allowBypass()` must **not** be called — it lets apps escape the VPN, the opposite of a kill switch.

**Desktop — the kill switch is yours to build, and it is a genuine feature gap.** No desktop OS gives
you Android's lockdown toggle. You must implement it:

- **Windows:** WFP filters blocking all non-tunnel traffic. This is what every commercial Windows VPN
  does, and it is the right answer. Xray's `autoSystemWfpBlockLeak` covers DNS; a full kill switch
  needs broader WFP rules from your service. **Budget real work here.**
- **Linux:** `nftables`/`iptables` rules from the daemon — drop everything not bound for the TUN or the
  hop-1 server. Straightforward, but must be installed and removed atomically and survive a daemon
  crash (so: install on connect, remove on clean disconnect, and have the daemon restore on startup if
  it finds stale rules).
- **macOS:** NetworkExtension's `includeAllNetworks` / on-demand rules are the supported route.

In all three cases the critical property is **fail-closed on crash**: if the engine dies, traffic must
stop, not fall back to the physical interface. That requires rules that outlive the engine process and
are cleaned up by a supervising component — one more reason the privileged helper must supervise the
engine rather than the UI doing it.

Do **not** attempt an in-app kill switch by blackholing all outbounds. It only works while the process
is alive, which is exactly when it is not needed.

### 9.7 Chain latency and reliability

`architecture.md` §1 is right: 3 hops is routinely 400–900 ms and the chain is only as live as its
weakest hop. The engine decision does not change this.

Engine-specific: with `dialerProxy`, a failure at hop 1 surfaces as a generic dial failure at hop 3.
**You will not get per-hop error attribution for free.** Probe each hop independently from the backend
health-checker (and optionally from the client with `pingBatch`, which libXray exposes) so the UI can
say "hop 2 is down" rather than "connection failed". **Shared L2 logic — write it once.**

### 9.8 One Go runtime per process

libXray's README: *"Use only one Go runtime per process."* Go does not support loading multiple
independently built Go runtimes into one process.

Consequences:

- You cannot ship libbox and libXray in the same binary. No runtime A/B testing between engines, no
  gradual migration. **The `core/routing/` abstraction protects your Dart code, not your binaries.**
  Swapping engines is a build-time decision, on all four platforms.
- Any other gomobile/cgo Go library you add later is subject to the same constraint. Check first.
- **This binds only where the engine is in-process** (Android, macOS). On Windows and Linux the engine
  is a child process, so the constraint does not apply there — a small, genuine advantage of the
  desktop architecture.

### 9.9 Store listing name

Unrelated to the engine but it bites at the same phase: `memory.md` P2 is still open. "Proxy Crack"
reads as piracy tooling and is a realistic Play rejection risk for an app that already needs a VPN
declaration and manual review. **With four platforms, the name now also has to survive Microsoft's
SmartScreen reputation system, Apple notarisation, and antivirus heuristics** — all of which treat
"crack" as a strong negative signal. The `applicationId` `com.nenobot.proxy_crack` is permanent after
publish; the **display name is not**. Pick a neutral name before Phase 6, and use it consistently
across all four products and their signing certificates.

---

## 10. Open questions — verify empirically

Ranked by how much damage a wrong assumption causes. Items 1–4 are Android and gate Phase 3; items
9–13 gate the desktop phases.

| # | Question | How to settle it | Blocks |
|---|---|---|---|
| 1 | Does the root `env` object accept `xray.tun.fd` in the pinned Xray version? | Minimal config + `runXray` on the emulator; check logs | fd handoff — everything |
| 2 | Flat `settings.address` vs nested `settings.servers[]` for socks/http/ss outbounds? | Feed both shapes to the engine, see which parses | the shared config generator — **all platforms** |
| 3 | Does `addDisallowedApplication(self)` survive always-on + lockdown? | Enable lockdown on a real device, try to connect | Android kill-switch claim |
| 4 | Does a 3-hop `dialerProxy` chain actually work? | Three throwaway proxies, `curl` through the tunnel | Phase 4 — **all platforms** |
| 5 | `specialUse` vs `systemExempted` for a VPN that is *also* always-on | Run both paths on an API 36 device | Play submission |
| 6 | Is `specialUse` startable from a `BOOT_COMPLETED` receiver on Android 15+? | Reboot test on API 35/36 | Android boot auto-connect |
| 7 | Does Xray's `tun` inbound handle IPv6 correctly? | Dual-stack network, leak test | DNS/IP leak claims — **all platforms** |
| 8 | Any GPL/AGPL transitive dependency in Xray's `go.mod` graph, **per platform build**? | `go mod graph` + licence scan per target | licence conclusion |
| 9 | **Wintun:** Administrator sufficient, or is LocalSystem required? | Test on Windows 10 and 11, both privilege levels | Windows service design |
| 10 | **Wintun licence:** does the Permitted-API carve-out cover commercial bundling? | Lawyer; if unclear, write to WireGuard LLC | Windows shipping — **long lead time** |
| 11 | Does `xray run -interface <name>` create the interface itself, or expect it pre-created? | Run the Linux target manually as root and as a capability-holding user | Windows + Linux L4 |
| 12 | Does the NE packet-tunnel entitlement need Apple approval in 2026? | Apple's current entitlement docs + a test provisioning profile | macOS phase — **check before scheduling** |
| 13 | Linux non-systemd DNS: bespoke path, or declare unsupported? | Test on Alpine/Void | Linux shipping scope |

Items 1, 2 and 4 are a **single two-day spike** and should happen before any Kotlin is written. Items
8, 10 and 12 have external lead times — **start them now**, in parallel, because none of them is
blocked by engineering.

---

## 11. Required changes to `architecture.md` and `memory.md`

### 11.1 `architecture.md`

**(a) Reframe the document as cross-platform.** It is currently written as an Android-only app plus a
backend. Add a short "Platforms" section near the top stating Android first, then Linux, Windows,
macOS, all on one shared core, and point at §3 and §4 of this document for the per-OS layer.

**(b) §1 recommendation — reverse it.** "Recommendation: embed sing-box" becomes "Recommendation: embed
Xray-core via libXray". The comparison table's sing-box row is accurate; it just reaches the wrong
conclusion once the licence is weighed — and far more so across four products.

**(c) §1 — the Xray tun2socks objection is obsolete.** The implicit premise that only sing-box has
built-in tun2socks no longer holds. Xray-core merged a TUN inbound with a gVisor userspace IP stack on
**2026-01-07** (PR #5464), covering Linux, Windows, macOS, **Android** and iOS. Confirmed on `main`:
`proxy/tun/` contains `tun_android.go`, `tun_darwin.go`, `tun_windows.go`, `tun_linux.go`, `tun_fd.go`,
`stack.go` and a README. This removes the only technical argument that favoured sing-box, on every
platform.

**(d) §1 — the Xray chaining syntax is WRONG.** *"Alternative: Xray-core (chaining via
`proxySettings`…)"* is out of date. `proxySettings` is a **removed feature**, present only to emit
`errors.PrintRemovedFeatureError` pointing at `streamSettings.sockopt.dialerProxy`. Any config
generator written from the current `architecture.md` **will not start the engine, on any platform.**
Replace the §1 "Chain semantics" JSON with the `dialerProxy` form from §7 above, and note the chain
inversion (hop3 first).

**(e) §1 — add the UDP constraint.** New paragraph alongside "Latency compounds" and "Reliability
compounds": *"UDP capability compounds. An HTTP hop cannot carry UDP at all — Xray's docs state `http`
can only proxy TCP. Any chain containing an HTTP hop is TCP-only, which breaks QUIC, WebRTC, VoIP and
games — more visibly on desktop than on mobile. The chain builder must compute and surface this."*
Product-visible constraint, currently absent.

**(f) §2 — restructure as the layered architecture from §4.** Replace the single `app/` tree with the
L1–L5 model and the repository shape in §4.4. Rename `SingBoxBridge.kt` → `XrayBridge.kt`; add
`VpnStateBus.kt`; add the `native/` and `engine/` top-level directories.

**(g) §2 "Android requirements" — two corrections.** `FOREGROUND_SERVICE_SPECIAL_USE` also requires the
`PROPERTY_SPECIAL_USE_FGS_SUBTYPE` `<property>` element, which **Google Play reviews at submission**.
And `BIND_VPN_SERVICE` is an `android:permission` attribute on `<service>`, not a `<uses-permission>`.

**(h) §2 — add a new subsection for the desktop platform layers**, summarising §3 of this document:
Windows (Wintun + LocalSystem service + named pipe), Linux (`CAP_NET_ADMIN` + systemd system unit +
Unix socket + optional per-command mode), macOS (NetworkExtension sysex + Developer ID + notarisation,
Mac hardware required).

**(i) §3 Models — add two `Proxy` fields.** `supports_udp` (bool, nullable) and
`supports_udp_checked_at` (§9.2).

**(j) §4 Monetization — add desktop.** Google Play Billing is mandatory **for the Android app only**.
Desktop products sold outside an app store can take payment directly (Stripe/Paddle), which is
materially better margin — but creates a **cross-platform entitlement problem**: one subscription must
unlock all four clients. Design the backend entitlement API for this from the start (it already is,
being server-side — just make sure `Subscription` is not modelled as Play-specific). If you ever ship
to the Mac App Store, Apple's IAP rules apply there too.

**(k) §5 Build order — replace with the nine-phase sequence in §4.5**, including Phase 2.5 (engine
spike) and Linux-before-Windows.

**(l) §6 Open risks — rewrite one row, add six.**
- *sing-box GPLv3* → **resolved**, replaced by: *"MPL-2.0 compliance — treat `Xray-core/` as read-only;
  any edit to its `.go` files must be published (MPL §3.2). Ship an open-source licences screen naming
  the exact Xray version, on all four products."*
- Add: *"**Wintun Prebuilt Binaries License** — redistribution needs WireGuard LLC consent unless the
  Permitted-API carve-out applies. Legal check with a long lead time. Blocks Windows shipping."*
- Add: *"Xray `tun` inbound is new (merged Jan 2026) and lightly documented — validate per platform."*
- Add: *"No `protect()` hook in libXray. Fork it (MIT) and register `internet.RegisterDialerController`
  before claiming kill-switch support on Android."*
- Add: *"UDP cannot traverse an HTTP hop — product-visible constraint on chain composition."*
- Add: *"**Desktop kill switch must be built from scratch** (WFP / nftables / NE). No OS gives you
  Android's lockdown toggle. Must fail closed if the engine crashes."*
- Add: *"**macOS needs Mac hardware, Apple Developer Program, and notarisation.** Cannot be built from
  the current Windows machine. Schedule last; procure early."*
- Keep the iOS row but update it: *"iOS is now reachable in principle — libXray has an Apple target and
  Xray's tun supports the NE fd. Still needs Mac hardware and App Store review. MPL (unlike GPLv3)
  does not close off the App Store."*

### 11.2 `memory.md`

- Move **P1** out of "Pending decisions" and into §3 as **D8**: *"Engine: Xray-core (MPL-2.0) via
  libXray (MIT), not sing-box (GPLv3). Rationale: MPL §3.3 permits a proprietary Larger Work; GPLv3
  §5(c) would require publishing the entire source of all four products, and direct desktop
  distribution removes the only ambiguity that made the GPLv3 analysis arguable. The previous technical
  objection to Xray (no built-in tun2socks) was removed by PR #5464, merged 2026-01-07, which covers
  all five platforms. Reversible: moderate — `core/routing/` isolates Dart, but only one Go runtime may
  be loaded per process, so an engine swap is a build-time change."*
- Add **D9**: *"Cross-platform scope. Android is the foundation; Linux, Windows and macOS follow on a
  shared core. Target ~75–85% shared code; the privileged platform layer (L4) is irreducibly
  per-OS."*
- Add **D10**: *"Platform order: Android → Linux → Windows → macOS. Linux second because it is the
  cheapest way to prove the separate-process engine model and the L3/L4 boundary."*
- Add to §5 gotchas: *"Xray `proxySettings` is removed — chain with
  `streamSettings.sockopt.dialerProxy`. Config order is reversed: the last hop is the first outbound."*
- Add to §5 gotchas: *"`http` outbounds cannot carry UDP. Any chain with an HTTP hop is TCP-only."*
- Add to §5 gotchas: *"libXray desktop targets build `bin/xray` / `bin/xray.exe` — a CLI binary, **not**
  a shared library. Desktop runs the engine as a child process."*
- Add to §5 gotchas: *"libXray `SetDNS`, `ResetDNS` and `registerProcessFinder` are **Android-only**.
  Do not put them in the shared interface."*
- Add to §4 environment: Go toolchain + `gomobile` **not yet installed** (`D:\dev\go` per D5); `gcc`/
  `g++` on `PATH` needed for the Linux and Windows engine targets.
- Add to §6 constraints: *"Play requires `targetSdk 36` since 2026-08-31 — already in force."*
- Add to §6 constraints: *"One Go runtime per process — binds on Android and macOS (in-process), not on
  Windows/Linux (child process)."*
- Add to §6 constraints: *"Wintun is mandatory for Windows TUN and carries a restrictive Prebuilt
  Binaries License."*
- Update the existing iOS constraint: *"iOS is now technically reachable (libXray Apple target, NE fd
  via `xray.tun.fd`) and MPL does not close off the App Store — but still needs Mac hardware."*
- Add **P5** to pending: *"Wintun redistribution consent — confirm the Permitted-API carve-out or obtain
  written consent from WireGuard LLC. Long lead time; start before the Windows phase."*
- Add **P6** to pending: *"Desktop payment rail — direct (Stripe/Paddle) vs store IAP, and how one
  subscription unlocks four clients."*

---

## 12. Sources

Primary (read directly for this document):

- [XTLS/Xray-core LICENSE](https://raw.githubusercontent.com/XTLS/Xray-core/main/LICENSE) — MPL-2.0, no additions
- [Mozilla Public License 2.0](https://www.mozilla.org/en-US/MPL/2.0/) — §1.7, §1.10, §3.1, §3.2, §3.3
- [GNU GPLv3](https://www.gnu.org/licenses/gpl-3.0.en.html) — §1, §5(c), §7(e), §10
- [SagerNet/sing-box](https://github.com/SagerNet/sing-box) — GPLv3-or-later + name/association clause
- [SagerNet/sing-box releases](https://github.com/SagerNet/sing-box/releases) — 1.14.3, 2026-10-09
- [XTLS/libXray](https://github.com/XTLS/libXray) and its [README](https://raw.githubusercontent.com/XTLS/libXray/main/README.md) — MIT, all build targets, `xray.tun.fd`, `CGoInvoke`/`CGoFree`, Android-only APIs, one-Go-runtime rule
- [Xray-core README](https://raw.githubusercontent.com/XTLS/Xray-core/main/README.md) — Bundled Third-Party Components (Wintun), build targets
- [Xray-core `infra/conf/xray.go`](https://raw.githubusercontent.com/XTLS/Xray-core/main/infra/conf/xray.go) — `proxySettings` removal error
- [Xray-core `infra/conf/tun.go`](https://raw.githubusercontent.com/XTLS/Xray-core/main/infra/conf/tun.go) — `TunConfig` fields
- [Xray-core `proxy/tun/README.md`](https://raw.githubusercontent.com/XTLS/Xray-core/main/proxy/tun/README.md) — platforms, Android/iOS fd, DNS automation, routing-loop warning
- [Xray-core `proxy/tun/`](https://github.com/XTLS/Xray-core/tree/main/proxy/tun) — `tun_android.go`, `tun_darwin.go`, `tun_windows.go`, `tun_linux.go`, `tun_fd.go`
- [Xray-core `transport/internet/system_dialer.go`](https://raw.githubusercontent.com/XTLS/Xray-core/main/transport/internet/system_dialer.go) — `RegisterDialerController`
- [Xray-core `proxy/socks/client.go`](https://raw.githubusercontent.com/XTLS/Xray-core/main/proxy/socks/client.go) — SOCKS5 UDP support
- [Xray-core PR #5464](https://github.com/XTLS/Xray-core/pull/5464) — TUN inbound, merged 2026-01-07
- [Xray-core releases](https://github.com/XTLS/Xray-core/releases) — v26.10.10, 2026-10-10
- Xray docs: [outbound](https://xtls.github.io/en/config/outbound.html), [sockopt](https://xtls.github.io/en/config/transports/sockopt.html), [shadowsocks](https://xtls.github.io/en/config/outbounds/shadowsocks.html), [socks](https://xtls.github.io/en/config/outbounds/socks.html), [http](https://xtls.github.io/en/config/outbounds/http.html), [dns](https://xtls.github.io/en/config/dns.html)
- sing-box docs: [dial fields](https://sing-box.sagernet.org/configuration/shared/dial/), [tun inbound](https://sing-box.sagernet.org/configuration/inbound/tun/), [outbound](https://sing-box.sagernet.org/configuration/outbound/)
- [Wintun Prebuilt Binaries License](https://git.zx2c4.com/wireguard-nt/tree/prebuilt-binaries-license.txt) — redistribution terms and the Permitted-API carve-out
- [Wintun README](https://git.zx2c4.com/wintun/about/) and the [WireGuard mailing-list thread on Wintun privileges](https://lists.zx2c4.com/pipermail/wireguard/2021-April/006565.html)
- [Android: VPN developer guide](https://developer.android.com/develop/connectivity/vpn)
- [Android 14: foreground service types required](https://developer.android.com/about/versions/14/changes/fgs-types-required)
- [Google Play target API level requirements](https://developer.android.com/google/play/requirements/target-sdk)
- [Google Play Developer Distribution Agreement](https://play.google/developer-distribution-agreement.html)
- [Apple TN3134: Network Extension provider deployment](https://developer.apple.com/documentation/technotes/tn3134-network-extension-provider-deployment)
- Apple Developer Forums on [NE entitlement approval](https://developer.apple.com/forums/thread/816877) and [PacketTunnelProvider vs utun](https://developer.apple.com/forums/thread/708310)
- [Flutter: C interop with dart:ffi](https://docs.flutter.dev/platform-integration/c-interop)
- [Nebula: running as non-root](https://nebula.defined.net/docs/guides/running-nebula-as-non-root/) — `CAP_NET_ADMIN` + systemd `AmbientCapabilities`

Secondary / corroborating:

- Arch and Alpine package metadata for the sing-box licence exception
- [heiher/hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel) — the external tun2socks Xray clients used *before* the native TUN inbound existed; no longer needed
- Tor Project forum and LD_PRELOAD write-ups on proxychains limits with static and Go binaries

---

**Prepared by:** Mobile App Builder
**Resolves:** P1
**Next actions:**
1. The two-day Android engine spike (§10 items 1, 2, 4) before any Phase 3 Kotlin is written
2. **Start now, in parallel (external lead times):** the Wintun licence question (§10 item 10), the
   Xray dependency licence scan (§10 item 8), and the Apple NE entitlement check (§10 item 12)
