# 08 — Accounts, Signing, and Store Readiness

Set these up **early**. Account verification can take days, and signing mistakes are expensive to undo.

---

## Developer accounts

- [ ] **Google Play Console** — **$25 one-time**
  - Identity verification required; personal accounts may need a D-U-N-S-style check and can take days
  - New personal developer accounts may face a closed-testing requirement before production release — check the current policy
- [ ] **Apple Developer Program** — **$99/year**
  - Individual or Organization (Organization requires a D-U-N-S number and takes longer)
- [ ] *(if applicable)* Huawei AppGallery, Amazon Appstore, Samsung Galaxy Store

## Android signing — ⚠️ the one thing you must not lose

- [ ] Generate an upload keystore:
  ```bash
  keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
  ```
- [ ] **Back up the `.jks` file and its passwords** somewhere safe and redundant (password manager + offline copy)
- [ ] **`.jks` is in `.gitignore`** — never commit it
- [ ] Passwords stored outside version control — `key.properties` (gitignored) or CI secrets
- [ ] **Enroll in Play App Signing** — Google holds the real app signing key, so losing your *upload* key becomes recoverable. Without it, a lost key means you can never update the app again.
- [ ] Release build configured to sign — `signingConfigs` in `app/build.gradle(.kts)`
- [ ] Build an **AAB** (Android App Bundle) for Play; APK only for direct distribution

## iOS signing

- [ ] Bundle ID registered in the Apple Developer portal
- [ ] Development + Distribution certificates created
- [ ] Provisioning profiles: Development, App Store
- [ ] *(team/CI)* **fastlane match** configured so signing assets live in a private repo
- [ ] App record created in **App Store Connect**

## Identifiers — permanent, choose carefully

- [ ] **Android `applicationId`** — e.g. `com.yourcompany.yourapp`
- [ ] **iOS Bundle Identifier** — usually the same string
- [ ] Both are **immutable after publishing**. Changing one means a new app listing and losing all installs, reviews, and rankings.
- [ ] Matching domain / brand name checked for availability

## Store listing assets

- [ ] **App icon** — 512×512 (Play), 1024×1024 (App Store), no alpha channel for iOS
- [ ] **Feature graphic** — 1024×500 (Play)
- [ ] **Screenshots** — multiple device sizes, both stores have specific required dimensions
- [ ] **App name** (30 chars) and **short description** (80 chars, Play)
- [ ] **Full description** (4000 chars)
- [ ] **Promo video** (optional)
- [ ] Localized copies if you target multiple markets

## Legal and compliance

- [ ] **Privacy Policy URL** — **mandatory on both stores**, even for a trivial app
- [ ] **Terms of Service** if you have accounts or payments
- [ ] **Play Data Safety form** completed accurately
- [ ] **Apple App Privacy "nutrition labels"** completed
- [ ] **Content / age rating** questionnaire done on both stores
- [ ] **Account deletion flow** — both stores require an in-app way to delete an account if you let users create one
- [ ] Export compliance (encryption) declaration for iOS
- [ ] GDPR / CCPA considerations if you collect personal data
- [ ] Third-party licenses page if you bundle OSS

## Release channels

- [ ] **Play**: internal → closed → open testing → production tracks understood
- [ ] **Apple**: **TestFlight** internal (up to 100 testers, no review) and external (needs review)
- [ ] Decide on **staged/phased rollout** — ship to 5% first, not 100%
- [ ] Plan for review time — Apple is typically a day or two, Play can vary

---

**Next:** [09-ci-testing-monitoring.md](09-ci-testing-monitoring.md)
