# 06 — Other Mobile Stacks

For completeness — the remaining well-known routes to a mobile app.

---

## .NET MAUI (C#)

Microsoft's cross-platform UI framework; successor to Xamarin.Forms.

- [ ] **.NET SDK** installed — `winget install Microsoft.DotNet.SDK.9`
- [ ] MAUI workloads:
  ```bash
  dotnet workload install maui
  dotnet workload list
  ```
- [ ] **Android SDK + JDK 17** — see [01-android-sdk-jdk.md](01-android-sdk-jdk.md)
- [ ] VS Code extensions: **C# Dev Kit** and **.NET MAUI**
  - *(or use full **Visual Studio 2022+** with the ".NET Multi-platform App UI development" workload — a notably better MAUI experience than VS Code)*
- [ ] Verify: `dotnet new maui -n MyApp && dotnet build`
- [ ] iOS: requires a networked Mac ("Pair to Mac") or a macOS build agent
- [ ] Decide: **XAML** or **C# Markup**; consider the **.NET MAUI Community Toolkit**

## Capacitor / Ionic (web technologies)

Wrap a web app (React/Vue/Angular/vanilla) in a native shell.

- [x] Node.js installed
- [ ] `npm i -g @ionic/cli` *(Ionic only — Capacitor alone needs no global CLI)*
- [ ] Create: `npm init @capacitor/app` or `ionic start`
- [ ] Add platforms: `npx cap add android` / `npx cap add ios`
- [ ] **Android Studio** required to build and open the Android project
- [ ] **Xcode on a Mac** required for iOS
- [ ] Understand the tradeoff: fastest path if you already have a web app; a WebView will never feel fully native
- [ ] Check plugin coverage for your native needs before committing

## NativeScript

JS/TS with direct native API access, no WebView.

- [ ] `npm i -g nativescript`
- [ ] Run `ns doctor` and clear every issue
- [ ] Android SDK + JDK 17 required
- [ ] Angular, Vue, Svelte, or plain TS flavors available
- [ ] Note: a smaller ecosystem and community than Flutter or React Native — check that your required integrations exist

## Unity (games and 3D)

- [ ] **Unity Hub** installed
- [ ] A Unity Editor **LTS** version
- [ ] Modules added during install: **Android Build Support** (includes its own OpenJDK + SDK + NDK) and **iOS Build Support**
- [ ] ~30 GB free disk
- [ ] iOS still exports an Xcode project — a Mac is required for the final build
- [ ] Only choose this if you're building a game or a heavy 3D/AR experience

## Progressive Web App (PWA)

Not a native app, but worth considering before committing to one.

- [ ] HTTPS hosting
- [ ] Web app manifest + service worker
- [ ] No store account, no review, no install toolchain
- [ ] Limits to accept: no App Store presence, restricted iOS capabilities (push is limited), no deep hardware access
- [ ] Good fit for content, dashboards, and internal tools; poor fit for anything hardware-heavy

---

## Quick comparison

| Stack | Language | Maturity | Native feel | Windows → iOS |
|---|---|---|---|---|
| Flutter | Dart | high | excellent | ❌ |
| Kotlin / Swift native | Kotlin / Swift | highest | perfect | ❌ |
| React Native / Expo | TypeScript | high | very good | ✅ via EAS |
| .NET MAUI | C# | medium | good | ❌ |
| Capacitor / Ionic | TypeScript | high | fair | ❌ |
| NativeScript | TypeScript | medium | good | ❌ |
| Unity | C# | high | n/a (games) | ❌ |

---

**Next:** [07-devices-emulators.md](07-devices-emulators.md)
