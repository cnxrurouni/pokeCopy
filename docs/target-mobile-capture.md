# Phase 0 — Target mobile API capture

Goal: capture the Target **app** ATC → checkout HTTP chain so we can name host, path, and required headers.

## Critical Mac limitation

The **iOS Simulator** (Xcode) **cannot** install the App Store Target app.  
If your “emulator” is an iPhone simulator, use one of these instead:

| Path | Works for Target app? | Notes |
|------|----------------------|--------|
| Android Studio AVD (emulator) | Yes | Best on MacBook without a phone |
| Physical Android + mitmproxy | Yes | Easiest MITM if you have a phone |
| Physical iPhone + Proxyman | Yes | App Store Target; trust Proxyman cert |
| iOS Simulator | **No** | No App Store apps |

## Recommended path on this Mac: Android emulator + mitmproxy

### 1. Install Android Studio

```bash
brew install --cask android-studio
```

Open Android Studio → More Actions → Virtual Device Manager → Create Device  
(Pixel 6 / Pixel 7, system image **Google APIs** or Google Play, x86_64/arm64 matching your Mac).

Cold-boot the AVD once from Device Manager.

### 2. mitmproxy (already usable via Homebrew)

```bash
brew install mitmproxy   # if needed
mitmweb --listen-host 0.0.0.0 --listen-port 8080
```

Leave this running. UI: http://127.0.0.1:8081  
CA cert install page: http://mitm.it

### 3. Point the emulator at the proxy

With the AVD running:

```bash
# SDK platform-tools on PATH after Android Studio install, often:
# export PATH="$HOME/Library/Android/sdk/platform-tools:$HOME/Library/Android/sdk/emulator:$PATH"

adb devices
adb shell settings put global http_proxy "$(ipconfig getifaddr en0):8080"
```

If `en0` is wrong, use the Mac’s LAN IP from System Settings → Network.

### 4. Install mitm CA on the emulator

1. Emulator browser → http://mitm.it → download **Android** cert  
2. Settings → Security → Encryption & credentials → Install a certificate → CA certificate  
3. Name it `mitmproxy`

Android 7+ apps ignore user CAs unless they allow them. Target **may SSL-pin**. If you see CONNECT tunnels with no decrypted HTTPS:

- Try an AVD with **rootable** system image (Google APIs, not Play) and Magisk/Frida unpinning later, **or**
- Switch to a physical device + Proxyman/HTTP Toolkit which sometimes handle pinning better, **or**
- Capture on a cheap in-stock flow once pinning is solved — without decrypt we cannot proceed to Phase 1.

### 5. Install Target and buy-flow capture

1. Play Store on the AVD → install **Target**  
2. Sign in with the **same account** as `data/sessions/target-auth.json`  
3. Clear cart  
4. Open an **in-stock** PDP (cheap SKU is fine)  
5. Set qty **2** if the UI allows → Add to cart  
6. Open cart → proceed to checkout **start** (stop before placing a real order unless you intend to)

### 6. Export from mitmweb

In mitmweb, filter:

```
~d target.com | ~d targetimg.com | ~d api.target.com
```

Export flows that look like cart / checkout / orders (HAR or mitm `flows` file).

Save into the repo (gitignored under `/data/*`):

```text
data/captures/target-mobile/
  README.txt          # what device / date / SKU you used
  atc.har             # or full.har
  checkout.har        # optional split
```

Redact before sharing: cookies, `Authorization`, refresh tokens, payment fields. Keep header **names** and URL shapes.

### 7. Success criteria (paste back into chat)

From the ATC request, we need:

- Full URL (host + path + query)
- Method
- Notable headers: `x-application-name`, `User-Agent`, API `key`, any `x-api-*` / device ids
- JSON body keys (especially `channel_id`, `tcin`, `quantity`)
- HTTP status on success

Example answer format:

```text
ATC URL: …
x-application-name: …
channel_id: …
User-Agent: …
status: …
```

## Alternate: physical iPhone + Proxyman

1. `brew install --cask proxyman`  
2. iPhone and Mac on same Wi‑Fi; Proxyman → Certificate → Install on iOS  
3. Install Target from App Store → same login → same ATC/checkout flow  
4. Export HAR → `data/captures/target-mobile/`

## After capture

Tell the agent the ATC URL + headers (or point at the HAR path). Phase 1 sanitizes into `config/reseller.capture.target.mobile.json`.

## Running the mobile channel (separate from desktop)

Desktop web path is unchanged (`checkout_channel: web`). Mobile is opt-in and uses a
**separate auth sidecar** (`data/sessions/target-auth-mobile.json`), not Chrome cookies.

```bash
# Import registered iOS tokens from a Proxyman login HAR
python -m pokebot login target-mobile \
  --from-har data/captures/target-mobile/full.har

python -m pokebot doctor   # checks web + mobile sidecars

# Mobile app API preflight (no browser-assist, no purchase)
python -m pokebot reseller target \
  --url "https://www.target.com/p/-/A-TCIN" \
  --mobile --preflight

# Live RestockR → mobile HTTP checkout
python -m pokebot reseller run --mobile

# Web HTTP ATC control (disable browser-assist for A/B)
python -m pokebot reseller target \
  --url "https://www.target.com/p/-/A-TCIN" \
  --channel web --http-atc --preflight
```

Or set `reseller.checkout_channel: mobile` in `config/reseller.yaml`.

The login HAR must include `POST gsp.target.com/gsp/oauth_tokens/v2/tokens` with
`grant_type=authorization_code` and a registered JWT (`sut=R`, `cli=ecom-ios-*`).

### Spam windows (config/reseller.yaml)

| Phase | Default | Stop when |
|------|---------|-----------|
| ATC | **5 min** (`atc_spam_timeout_seconds: 300`) | success, stock/limit error, or timeout |
| Checkout / place_order | **20 min** (`checkout_spam_timeout_seconds: 1200`) | order placed, **cart GET 200 shows TCIN gone**, or timeout |

Cart GET timeouts/429s do **not** count as empty — only a successful cart read with the TCIN missing.

## Cleanup (Android emulator)

```bash
adb shell settings put global http_proxy :0
```
