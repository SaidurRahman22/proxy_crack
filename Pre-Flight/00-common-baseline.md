# 00 — Common Baseline

Applies to **every** mobile stack. Do this first.

---

## Hardware

- [x] **16 GB RAM minimum** (8 GB works but an emulator + IDE will thrash)
- [x] **SSD, 100 GB+ free** — SDKs, emulator images, Gradle caches and build output add up fast
- [x] CPU with virtualization support, **enabled in BIOS/UEFI** (Intel VT-x / AMD-V) — without it the Android emulator will not start

## Windows system flags

- [x] **Windows Hypervisor Platform** enabled — `Turn Windows features on or off` → tick it → reboot
  - Required for hardware-accelerated emulation. Intel HAXM is deprecated; don't use it.
- [x] **Developer Mode ON** — `Settings → System → For developers`
  - **Flutter on Windows requires this** for plugin symlink support. It will fail without it.
- [x] **Long path support** enabled — deep `node_modules` / Gradle paths break the 260-char limit:
  ```powershell
  # Run as Administrator
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
    -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
  git config --system core.longpaths true
  ```
- [ ] **Antivirus exclusions** added for your project folder, the Android SDK folder, `~/.gradle`, and `~/.pub-cache`
  - Real-time scanning on build output can triple build times. This is the single biggest easy win.
- [ ] Project path has **no spaces and no non-ASCII characters** — `D:\project\Proxy` is fine; `D:\My Projects\অ্যাপ` will break Gradle and CocoaPods

> macOS: enable Rosetta if on Apple Silicon and using x86 tooling. Linux: install `KVM` and add your user to the `kvm` group.

## Package manager

- [x] **winget** available (ships with Windows 11) — verify: `winget --version`
- [ ] *(optional)* **Chocolatey** as a fallback for packages winget lacks
- [ ] *(macOS)* **Homebrew** installed
- [ ] *(Linux)* `apt` / `dnf` up to date

## Terminal

- [x] **Windows Terminal** installed (ships with Win 11)
- [ ] **PowerShell 7** — `winget install Microsoft.PowerShell` (the built-in 5.1 lacks `&&`, `??`, ternary)
- [ ] Know how to open a terminal **as Administrator** — needed for env vars and some installers
- [ ] *(optional)* Git Bash available for POSIX scripts — ships with Git for Windows

## Git and version control

- [x] **Git installed** — 2.55.0 detected
- [x] Identity configured:
  ```bash
  git config --global user.name  "Your Name"
  git config --global user.email "you@example.com"
  ```
- [x] Line endings set — prevents mystery diffs across platforms:
  ```bash
  git config --global core.autocrlf input   # Windows: use "true" if unsure
  ```
- [x] **SSH key** generated and added to GitHub/GitLab:
  ```bash
  ssh-keygen -t ed25519 -C "you@example.com"
  ```
- [x] Default branch name set — `git config --global init.defaultBranch main`
- [ ] A **`.gitignore`** ready for your stack (gitignore.io / GitHub templates)
  - Never commit: `build/`, `.gradle/`, `node_modules/`, `*.keystore`, `*.jks`, `.env`, `google-services.json` with secrets, `ios/Pods/`

## Editor

- [x] **VS Code installed**
- [ ] Core extensions:
  - [ ] GitLens
  - [ ] EditorConfig for VS Code
  - [ ] Error Lens
  - [ ] *(stack-specific extensions are listed in each stack's file)*
- [ ] `.editorconfig` added to the project root
- [ ] *(Kotlin/Swift users)* note that VS Code is **not** sufficient — see files 03 and 04

## Runtimes already present

- [x] **Node.js v24.18.0** — needed for React Native, Capacitor, and most tooling
- [x] **Python 3.11.15** — used by some native build scripts
- [ ] *(optional)* a Node version manager — `fnm` or `nvm-windows` — if you will juggle Node versions

## Network

- [ ] Stable connection — first-time SDK downloads are **5–20 GB**
- [ ] If behind a corporate proxy/firewall, configure it for: `gradle.properties`, `npm config`, `git`, and the Android SDK manager
- [ ] Verify `dl.google.com`, `repo.maven.apache.org`, `pub.dev`, and `registry.npmjs.org` are reachable

---

**Next:** [01-android-sdk-jdk.md](01-android-sdk-jdk.md)
