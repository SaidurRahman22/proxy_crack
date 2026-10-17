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

## Invoke shape (verified against `invoke_model.go`, not docs)

```json
{"apiVersion":3,"method":"runXray","payload":{"xrayJson":"<the whole config>"}}
```

returns

```json
{"success":true,"data":{},"error":""}
```

Methods are lowerCamelCase: `runXray`, `stopXray`, `getXrayState`, `testXray`,
`xrayVersion`, `pingBatch`, `getFreePorts`, `countGeoData`,
`convertShareLinksToXrayJson`, `convertXrayJsonToShareLinks`,
`generateAgeKeyPair`, `decodeSubscriptionText`. `payload` is a JSON **object**,
not a base64 string.

`testXray` builds an instance and closes it without starting — the way to check
a generated config against the real parser with no TUN. It refuses to run while
an instance is live.

## Config gotchas found by running it on a device

These cost a build cycle each, and none of them produce an obvious error.

1. **`tun.settings.name` must NOT be empty on Android.** `engine-decision.md`
   §4.3 and §7 both show `"name": ""`. Empty sends `TunConfig.Build()` into
   `GetAvailableTunName()` → `net.Interfaces()` → a `netlink_route_socket`,
   which SELinux denies to `untrusted_app`:
   *"fail to get system interface information: route ip+net: netlinkrib:
   permission denied"*. It fails **after** the TUN is already established, so
   it reads as an engine fault. Any non-empty string works; the value is
   ignored on Android because `tun_android.go` takes the fd from the
   environment.
2. **`dns` outbound `rules[].qType` is a PortList, so it takes a string.**
   `[1, 28]` fails with *"cannot unmarshal array into Go value of type
   uint32"*. Write `"1,28"`.
3. **`registerDialerController` appends to a global slice** —
   `transport/internet/system_dialer.go` does `Controllers = append(...)` and
   nothing removes entries. Register once per process, not once per connect,
   or every dial walks a list that grows with every reconnect.
4. **Root `env` does work**: `Config.Env` is a `map[string]string` applied with
   `os.Setenv` in `Config.Build()`, and `platform.TunFdKey` is `"xray.tun.fd"`.
   Both `xray.tun.fd` and `XRAY_TUN_FD` are read.
5. **The flat outbound form is correct** for `shadowsocks`, `socks` and `http`
   (`settings.address` / `.port` / `.user` / `.pass`), confirmed in
   `infra/conf/{socks,http,shadowsocks}.go`: when `address` is set the parser
   expands it into `servers[0]` itself. A `user` key is only emitted when there
   is a username, because an empty one makes Xray offer username/password auth
   with an empty username.
