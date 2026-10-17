# Engine binary — fetched, not committed

`libXray.aar` is **56 MB** and is deliberately **gitignored**. Committing it would bloat the
repository permanently, and git stores every version forever.

## Fetch it

```bash
# from the repo root
python tools/fetch_engine.py
```

Or by hand: download `libxray-android.zip` from the latest
[libXray release](https://github.com/XTLS/libXray/releases), unzip, and put `libXray.aar` here.

## What this is

[libXray](https://github.com/XTLS/libXray) (**MIT**) — a gomobile wrapper around
[Xray-core](https://github.com/XTLS/Xray-core) (**MPL-2.0**). Decision **D9** in
`Project/memory.md`; the full licence analysis is in `Project/engine-decision.md`.

## API surface (v26.10.10)

```java
LibXray.invoke(String requestJSON)                      // everything goes through this
LibXray.registerDialerController(DialerController c)    // VpnService.protect() hook
LibXray.registerListenerController(DialerController c)
LibXray.registerProcessFinder(ProcessFinder f, long sdk)// per-app routing (Android only)
LibXray.setDNS(DialerController c, String server)       // Android only
LibXray.resetDNS()                                      // Android only
```

```java
interface DialerController { boolean protectFd(long fd); }
interface ProcessFinder   { long findProcessByConnection(String network, String srcIP,
                                                         long srcPort, String destIP,
                                                         long destPort); }
```

> **Correction to `engine-decision.md`:** that document states libXray exposes no
> `VpnService.protect()` hook and recommends forking it to add one (~30 lines). That is **not
> true of v26.10.10** — `registerDialerController` with `DialerController.protectFd(long)` is
> exactly that hook, and it ships in the release. **No fork is required.**
>
> Without it the engine's own sockets would loop back into the TUN; the suggested workaround
> (`addDisallowedApplication(self)`) was itself suspected of breaking under always-on lockdown,
> i.e. breaking the kill switch.
