from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def quit_bot_browser_hint() -> str:
    """How the bot quits the manual-login browser on this OS."""
    if is_macos():
        return "the bot will quit that Chrome for you when you press Enter"
    if is_windows():
        return "the bot will quit that Edge/Chrome for you when you press Enter"
    return "the bot will quit that browser when you press Enter"


def profile_still_locked_hint() -> str:
    if is_macos():
        return (
            "Quit the bot browser with Cmd+Q, or run:\n"
            "  pkill -f 'user-data-dir=.*/data/sessions/target'"
        )
    if is_windows():
        return (
            "Fully quit the bot Chrome/Edge window that used the PokeBot profile "
            "(Task Manager → end any leftover msedge/chrome still on that profile)."
        )
    return "Fully quit the bot browser using the PokeBot profile directory."


def browser_ua_platform() -> tuple[str, str]:
    """Return (user-agent OS token, sec-ch-ua-platform value) for this host."""
    if is_windows():
        return (
            "Windows NT 10.0; Win64; x64",
            '"Windows"',
        )
    if is_macos():
        return (
            "Macintosh; Intel Mac OS X 10_15_7",
            '"macOS"',
        )
    return (
        "X11; Linux x86_64",
        '"Linux"',
    )


def profile_singleton_paths(profile: Path) -> tuple[Path, ...]:
    """Chrome/Edge singleton marker paths under a user-data-dir."""
    return tuple(profile / name for name in (
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
    ))


def profile_singleton_present(profile: Path) -> bool:
    """True if Chrome left singleton markers (including broken symlinks).

    On macOS, ``SingletonLock`` is often a symlink to ``Host-pid`` that does
    **not** resolve as a real file, so ``Path.exists()`` is False even while
    Chrome still owns the profile. Use ``is_symlink()`` as well.
    """
    for path in profile_singleton_paths(profile):
        if path.is_symlink() or path.exists():
            return True
    return False


def clear_profile_singleton(profile: Path) -> None:
    """Remove stale singleton lock/socket/cookie markers (incl. broken symlinks)."""
    for path in profile_singleton_paths(profile):
        if path.is_symlink() or path.exists():
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)


def kill_browsers_using_profile(profile: Path) -> None:
    """Kill only browser processes whose command line uses this user-data-dir.

    Safe for daily Chrome/Edge: those use a different profile path.
    """
    marker = str(profile.resolve())
    if is_macos() or sys.platform.startswith("linux"):
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={marker}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if is_windows():
        # Match the bot profile only; do not kill the user's daily browser.
        ps = (
            "$m = '"
            + marker.replace("'", "''")
            + "'; "
            + "Get-CimInstance Win32_Process | "
            + "Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $m + '*') } | "
            + "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def open_url_in_system_chrome(url: str) -> None:
    """Open a URL in the user's everyday Chrome (default profile — not PokeBot)."""
    import os
    import shutil

    if is_macos():
        chrome = Path("/Applications/Google Chrome.app")
        if chrome.exists():
            subprocess.Popen(
                ["open", "-a", "Google Chrome", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        # Fall back to whatever `open` chooses for http(s).
        subprocess.Popen(
            ["open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    if is_windows():
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        candidates = [
            pf / "Google/Chrome/Application/chrome.exe",
            pf86 / "Google/Chrome/Application/chrome.exe",
            local / "Google/Chrome/Application/chrome.exe",
        ]
        exe = next((p for p in candidates if p.exists()), None)
        if exe is not None:
            subprocess.Popen(
                [str(exe), url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        os.startfile(url)  # type: ignore[attr-defined]
        return

    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which(
        "chromium-browser"
    )
    if chrome:
        subprocess.Popen(
            [chrome, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    import webbrowser

    webbrowser.open(url)


_ATC_CLICK_JS = r"""
(() => {
  const tcin = __TCIN__;
  const wantQty = __WANT_QTY__;
  const href = location.href || '';
  if (!/target\.com\/p\//i.test(href)) {
    return JSON.stringify({
      clicked: false,
      reason: 'wrong_page',
      href,
      title: (document.title || '').slice(0, 80),
    });
  }
  if (tcin && !href.includes(tcin)) {
    return JSON.stringify({
      clicked: false,
      reason: 'wrong_pdp_tcin',
      href,
      title: (document.title || '').slice(0, 80),
    });
  }

  const labelOf = (el) =>
    ((el.innerText || el.getAttribute('aria-label') || el.getAttribute('data-test') || '')
      .replace(/\s+/g, ' ')
      .trim());

  const isInactive = (el) => {
    if (!el) return true;
    if (el.disabled) return true;
    if (el.getAttribute('aria-disabled') === 'true') return true;
    if (el.getAttribute('aria-busy') === 'true') return true;
    const cls = (el.className || '').toString().toLowerCase();
    if (/\bdisabled\b|\bunavailable\b|\bsold.?out\b/.test(cls)) return true;
    const t = labelOf(el).toLowerCase();
    if (/sold out|not available|notify me|out of stock|unavailable/.test(t)) return true;
    return false;
  };

  const tryClick = (el) => {
    if (!el || isInactive(el)) return false;
    el.scrollIntoView({block:'center'});
    el.click();
    return true;
  };

  // Prefer Qty=2 when the PDP offers it; otherwise leave current qty (usually 1).
  // Prior run: attribute selectors found 0 triggers on Target PDP — use text/nearby scan.
  let qtySelected = null;
  let qtyDetail = 'unchanged';
  const qtyProbe = {
    selects: 0,
    triggers: 0,
    options: [],
    menu_scan: [],
    method: null,
    candidates: [],
    near_ship: 0,
  };

  const isQtyLabel = (t) => {
    const s = (t || '').replace(/\s+/g, ' ').trim();
    return /^Qty(\s*\d+)?$/i.test(s) || /^Qty\s+\d+/i.test(s) || /^Quantity\s*\d*$/i.test(s);
  };

  const collectQtyCandidates = () => {
    const out = [];
    const nodes = Array.from(
      document.querySelectorAll(
        'button, select, input, [role="combobox"], [role="button"], [aria-haspopup], label'
      )
    );
    for (const el of nodes) {
      const dt = (el.getAttribute('data-test') || '').slice(0, 48);
      const al = (el.getAttribute('aria-label') || '').slice(0, 48);
      const t = labelOf(el).slice(0, 48);
      const blob = (dt + ' ' + al + ' ' + t).toLowerCase();
      if (!(isQtyLabel(t) || isQtyLabel(al) || /qty|quantity/.test(blob))) continue;
      if (t.length > 48) continue;
      out.push({
        tag: el.tagName,
        dt,
        al,
        text: t,
        role: (el.getAttribute('role') || '').slice(0, 20),
      });
      if (out.length >= 12) break;
    }
    return out;
  };

  const fireChange = (el) => {
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
  };

  const pointerClick = (el) => {
    if (!el || isInactive(el)) return false;
    el.scrollIntoView({block: 'center'});
    const opts = {bubbles: true, cancelable: true, view: window};
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    el.dispatchEvent(new PointerEvent('pointerup', opts));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.dispatchEvent(new MouseEvent('click', opts));
    // Native click — React/NDS handlers often bind here, not only to dispatched events.
    try { el.click(); } catch (e) {}
    return true;
  };

  const scanOpenMenu = () => {
    // Target NDS qty menu often renders plain digit nodes (not always role=option).
    const hits = [];
    const nodes = document.querySelectorAll(
      '[role="option"], [role="listbox"] *, [role="menu"] *, [id*="listbox"] *, [id*="option"] *, li, button, div, span'
    );
    for (const el of nodes) {
      if (el.children && el.children.length > 3) continue;
      const t = labelOf(el).slice(0, 24);
      if (!t || t.length > 12) continue;
      if (!/^(\d{1,2}|Qty\s*\d{1,2})$/i.test(t)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      hits.push({
        tag: el.tagName,
        text: t,
        role: (el.getAttribute('role') || '').slice(0, 16),
        dt: (el.getAttribute('data-test') || '').slice(0, 32),
      });
      if (hits.length >= 16) break;
    }
    return hits;
  };

  const pickOption = (nStr, trig) => {
    const until = Date.now() + 1200;
    let opts = [];
    let menuScan = [];
    while (Date.now() < until) {
      opts = Array.from(
        document.querySelectorAll(
          '[role="option"], [role="menuitem"], [role="listbox"] li, [role="listbox"] [role="option"], ul[role="listbox"] li, li[data-value], [id*="option-"], [id*="listbox"] [id*="option"]'
        )
      );
      menuScan = scanOpenMenu();
      if (
        opts.length ||
        menuScan.some(
          (h) => h.text === nStr || new RegExp('^Qty\\s*' + nStr + '$', 'i').test(h.text)
        )
      ) {
        break;
      }
    }
    qtyProbe.options = (opts.length
      ? opts.map((o) => labelOf(o).slice(0, 24))
      : menuScan.map((h) => h.text)
    ).slice(0, 12);
    qtyProbe.menu_scan = menuScan.slice(0, 12);

    const matchesWant = (t, val) =>
      val === nStr ||
      t === nStr ||
      t === ('Qty ' + nStr) ||
      new RegExp('^(Qty\\s*)?' + nStr + '\\s*$', 'i').test(t);

    // Prefer LI / role=option — clicking inner DIV often does not update NDS state.
    const ranked = [];
    for (const o of opts) {
      const t = labelOf(o);
      const val = (o.getAttribute('data-value') || o.getAttribute('value') || '').trim();
      if (!matchesWant(t, val)) continue;
      ranked.push(o);
    }
    for (const h of menuScan) {
      if (!matchesWant(h.text, '')) continue;
      const el = Array.from(document.querySelectorAll('li, [role="option"], button, div, span')).find(
        (node) => {
          if (labelOf(node) !== h.text) return false;
          const r = node.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        }
      );
      if (el && !ranked.includes(el)) ranked.push(el);
    }
    ranked.sort((a, b) => {
      const score = (el) => {
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (role === 'option' || role === 'menuitem') return 0;
        if (el.tagName === 'LI') return 1;
        if (el.tagName === 'BUTTON') return 2;
        return 3;
      };
      return score(a) - score(b);
    });

    for (const o of ranked) {
      if (!(pointerClick(o) || tryClick(o))) continue;
      // Require the Qty trigger to reflect the new value before ATC.
      const confirmUntil = Date.now() + 1000;
      while (Date.now() < confirmUntil) {
        const trigText = trig ? labelOf(trig) : '';
        qtyProbe.trigger_after = trigText.slice(0, 24);
        if (new RegExp('Qty\\s*' + nStr + '\\b', 'i').test(trigText)) {
          return trigText || nStr;
        }
        // Re-query trigger in case React replaced the node
        const live = Array.from(document.querySelectorAll('button')).find((b) =>
          isQtyLabel(labelOf(b))
        );
        if (live) {
          const lt = labelOf(live);
          qtyProbe.trigger_after = lt.slice(0, 24);
          if (new RegExp('Qty\\s*' + nStr + '\\b', 'i').test(lt)) {
            return lt;
          }
        }
      }
    }
    return null;
  };

  const selectQty = (n) => {
    const nStr = String(n);
    qtyProbe.candidates = collectQtyCandidates();

    // 1) Native <select>
    const selects = Array.from(document.querySelectorAll('select'));
    qtyProbe.selects = selects.length;
    for (const sel of selects) {
      const blob = (
        (sel.getAttribute('data-test') || '') +
        ' ' +
        (sel.getAttribute('aria-label') || '') +
        ' ' +
        (sel.id || '') +
        ' ' +
        (sel.name || '')
      ).toLowerCase();
      const nearQty = /qty|quantity/.test(blob);
      const opt = Array.from(sel.options || []).find((o) => {
        const tv = String(o.value || '').trim();
        const tt = (o.textContent || '').replace(/\s+/g, ' ').trim();
        return tv === nStr || tt === nStr || tt === ('Qty ' + nStr);
      });
      if (!opt || opt.disabled) continue;
      if (!nearQty && selects.length > 3) continue;
      sel.focus();
      sel.value = opt.value;
      fireChange(sel);
      if (sel.value === opt.value || sel.value === nStr) {
        qtySelected = n;
        qtyDetail = 'select:' + (sel.getAttribute('data-test') || sel.id || sel.name || 'qty');
        qtyProbe.method = qtyDetail;
        qtyProbe.trigger_after = 'select:' + sel.value;
        return true;
      }
    }

    // 2) "Qty 1" button near fulfillment — open menu, pick 2, confirm label → Qty 2
    const triggers = Array.from(
      document.querySelectorAll('button, [role="combobox"], [role="button"], [aria-haspopup="listbox"]')
    ).filter((el) => {
      const t = labelOf(el);
      const al = el.getAttribute('aria-label') || '';
      const dt = el.getAttribute('data-test') || '';
      return isQtyLabel(t) || isQtyLabel(al) || /qty|quantity/i.test(dt + ' ' + al);
    });
    qtyProbe.triggers = triggers.length;

    const ship =
      document.querySelector('[data-test="shippingButton"]') ||
      document.querySelector('[data-test="shipItButton"]');
    let nearRoot = ship;
    for (let i = 0; i < 8 && nearRoot; i++) nearRoot = nearRoot.parentElement;
    const nearBtns = nearRoot
      ? Array.from(nearRoot.querySelectorAll('button, [role="combobox"], select')).filter((el) => {
          const t = labelOf(el);
          return isQtyLabel(t) || /^\d+$/.test(t.trim()) || /qty|quantity/i.test(el.getAttribute('data-test') || '');
        })
      : [];
    qtyProbe.near_ship = nearBtns.length;
    const ordered = [...triggers];
    for (const el of nearBtns) {
      if (!ordered.includes(el)) ordered.push(el);
    }

    for (const trig of ordered) {
      if (trig.tagName === 'SELECT') continue;
      if (isInactive(trig)) continue;
      pointerClick(trig) || tryClick(trig);
      let picked = pickOption(nStr, trig);
      if (!picked) {
        // Keyboard: open listbox and move to option N
        try {
          trig.focus();
          trig.dispatchEvent(new KeyboardEvent('keydown', {key: ' ', code: 'Space', bubbles: true}));
          const active = document.activeElement || trig;
          for (let i = 1; i < n; i++) {
            active.dispatchEvent(
              new KeyboardEvent('keydown', {key: 'ArrowDown', code: 'ArrowDown', bubbles: true})
            );
          }
          active.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
          const confirmUntil = Date.now() + 800;
          while (Date.now() < confirmUntil) {
            const live = Array.from(document.querySelectorAll('button')).find((b) =>
              isQtyLabel(labelOf(b))
            );
            const lt = labelOf(live || trig);
            qtyProbe.trigger_after = lt.slice(0, 24);
            if (new RegExp('Qty\\s*' + nStr + '\\b', 'i').test(lt)) {
              picked = lt;
              break;
            }
          }
        } catch (e) {}
      }
      if (picked) {
        qtySelected = n;
        qtyDetail = 'menu:' + String(picked).slice(0, 40);
        qtyProbe.method = qtyDetail;
        return true;
      }
      try {
        document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      } catch (e) {}
    }

    // 3) +/- stepper if present — confirm trigger/value afterward
    const inc = Array.from(document.querySelectorAll('button')).find((b) => {
      const al = (b.getAttribute('aria-label') || '').toLowerCase();
      const dt = (b.getAttribute('data-test') || '').toLowerCase();
      return (
        (/increase/.test(al) && /(qty|quantity)/.test(al)) ||
        (/increase/.test(dt) && /(qty|quantity)/.test(dt)) ||
        dt === 'quantity-increase-button' ||
        dt === 'qtySpinnerIncrease'
      );
    });
    if (inc && !isInactive(inc)) {
      for (let i = 1; i < n; i++) {
        if (!tryClick(inc)) break;
      }
      const live = Array.from(document.querySelectorAll('button')).find((b) =>
        isQtyLabel(labelOf(b))
      );
      const lt = live ? labelOf(live) : '';
      qtyProbe.trigger_after = lt.slice(0, 24);
      if (new RegExp('Qty\\s*' + nStr + '\\b', 'i').test(lt)) {
        qtySelected = n;
        qtyDetail = 'stepper:increase';
        qtyProbe.method = qtyDetail;
        return true;
      }
    }
    return false;
  };

  // Wait for Qty control, set to 2, only ATC after trigger shows Qty 2 (or unavailable).
  if (wantQty >= 2) {
    const waitUntil = Date.now() + 2000;
    while (Date.now() < waitUntil) {
      const cands = collectQtyCandidates();
      if (cands.length) break;
    }
    if (!selectQty(wantQty)) {
      qtyDetail = 'qty_' + wantQty + '_unavailable';
      qtyProbe.method = qtyDetail;
      qtyProbe.candidates = collectQtyCandidates();
      if (!qtyProbe.menu_scan || !qtyProbe.menu_scan.length) {
        qtyProbe.menu_scan = scanOpenMenu();
      }
    }
  }

  // Prefer fulfillment ATC controls only — never generic "Add to cart" elsewhere
  // on the page (recommendations / recently viewed can ATC the wrong TCIN).
  const sels = [
    '[data-test="shippingButton"]',
    '[data-test="shipItButton"]',
    '[data-test="orderPickupButton"]',
    'button[data-test="shippingButton"]',
    '[data-test="fulfillment-cell-shipping"] button',
  ];

  const seen = [];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    const text = labelOf(el).slice(0, 60);
    const inactive = isInactive(el);
    seen.push({sel: s, text, inactive});
    if (!inactive && tryClick(el)) {
      return JSON.stringify({
        clicked: true,
        sel: s,
        text,
        href,
        tcin,
        want_qty: wantQty,
        qty_selected: qtySelected,
        qty_detail: qtyDetail,
        qty_probe: qtyProbe,
      });
    }
  }

  const inactive = seen.filter((x) => x.inactive);
  const pageText = ((document.body && document.body.innerText) || '').slice(0, 2500);
  const oosPage = /sold out|this item.*(not available|unavailable)|out of stock/i.test(pageText);

  return JSON.stringify({
    clicked: false,
    href,
    title: (document.title || '').slice(0, 80),
    tcin,
    want_qty: wantQty,
    qty_selected: qtySelected,
    qty_detail: qtyDetail,
    qty_probe: qtyProbe,
    buttons: seen.slice(0, 8),
    inactive_buttons: inactive.slice(0, 8),
    reason: inactive.length
      ? 'atc_inactive'
      : (oosPage ? 'oos_page' : 'no_atc_button'),
  });
})()
"""


def click_target_atc_in_system_chrome(
    *, tcin: str | None = None, quantity: int = 2
) -> tuple[bool, str]:
    """Click Target Add to cart in everyday Chrome via AppleScript (macOS).

    Does **not** use the bot profile or CDP. Requires Chrome once:
    View → Developer → Allow JavaScript from Apple Events.

    Selects qty=2 when the PDP quantity UI offers it, then clicks fulfillment ATC.
    Only clicks on a PDP tab (``/p/``); refuses ``/cart`` and mismatched TCINs.
    Returns ``(ok, detail)``. Detail explains inactive/OOS when click is impossible.
    """
    import json
    import tempfile

    if not is_macos():
        return False, "auto-click only on macOS — click Add to cart manually"

    want_qty = max(1, int(quantity or 2))
    js = (
        _ATC_CLICK_JS.replace("__TCIN__", json.dumps(str(tcin or "")))
        .replace("__WANT_QTY__", str(want_qty))
    )
    js_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(js)
            js_path = fh.name

        script = f'''
set jsPath to "{js_path}"
set jsCode to read POSIX file jsPath as «class utf8»
set pdpUrl to "https://www.target.com/p/-/A-{tcin or ""}"
tell application "Google Chrome"
  activate
  if (count of windows) = 0 then
    make new window
  end if
  -- Force the front tab onto the PDP so we never click /cart or another product.
  set URL of active tab of front window to pdpUrl
  delay 5
  try
    set r to execute active tab of front window javascript jsCode
    return r
  on error errMsg number errNum
    return "applescript_error:" & errNum & ":" & errMsg
  end try
end tell
'''
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except Exception as exc:
        return False, f"osascript failed: {exc}"
    finally:
        if js_path:
            with contextlib.suppress(OSError):
                Path(js_path).unlink(missing_ok=True)

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        return False, err or f"osascript exit {proc.returncode}"

    if out.startswith("applescript_error:"):
        low = out.lower()
        if "javascript through applescript is turned off" in low or "allow javascript" in low:
            return False, (
                "Chrome blocks AppleScript JS — enable once: "
                "View → Developer → Allow JavaScript from Apple Events, then retry. "
                "Or click Add to cart manually while the bot polls."
            )
        return False, out[:240]

    if out.startswith("no_windows") or out.startswith("wrong_tab:"):
        return False, out[:200]

    try:
        data = json.loads(out)
    except Exception:
        return False, f"unexpected AppleScript result: {out[:200]}"

    if data.get("clicked"):
        detail = f"clicked {data.get('sel')} {data.get('text')!r}"
        if data.get("qty_selected"):
            detail += f" qty={data.get('qty_selected')} ({data.get('qty_detail')})"
        elif data.get("qty_detail"):
            detail += f" qty={data.get('qty_detail')}"
        href = str(data.get("href") or "")
        if tcin and tcin not in href:
            detail += f" (tab={href[:80]})"
        return True, detail

    reason = str(data.get("reason") or "no_atc_button")
    inactive = data.get("inactive_buttons") or []
    if reason == "wrong_page" or reason == "wrong_pdp_tcin":
        return False, f"{reason}: {str(data.get('href') or '')[:120]}"
    if reason == "atc_inactive" and inactive:
        sample = inactive[0]
        return False, (
            f"ATC inactive/OOS: {sample.get('text')!r} "
            f"(sel={sample.get('sel')}) — button present but not clickable"
        )
    if reason == "oos_page":
        return False, "ATC unavailable — page looks sold out / not available"
    return False, f"no active ATC button on {str(data.get('href') or '')[:100]}"


def dismiss_target_added_to_cart_drawer() -> tuple[bool, str]:
    """Close Target's 'Added to cart' flyout so the window doesn't look stuck."""
    if not is_macos():
        return False, "dismiss only on macOS"

    js = r"""
(() => {
  const tryClick = (el) => {
    if (!el) return false;
    el.click();
    return true;
  };
  // Close (X) on the added-to-cart drawer / modal.
  const closeSels = [
    '[data-test="closeButton"]',
    'button[aria-label="close"]',
    'button[aria-label="Close"]',
    '[data-test="modal-close"]',
  ];
  for (const s of closeSels) {
    const el = document.querySelector(s);
    if (tryClick(el)) return JSON.stringify({dismissed: true, sel: s});
  }
  // "Continue shopping" also dismisses the flyout.
  for (const b of Array.from(document.querySelectorAll('button'))) {
    const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || ''))
      .replace(/\s+/g, ' ').trim();
    if (/^continue shopping$/i.test(t) && tryClick(b)) {
      return JSON.stringify({dismissed: true, sel: 'continue-shopping'});
    }
  }
  const text = (document.body && document.body.innerText || '').slice(0, 500);
  return JSON.stringify({
    dismissed: false,
    hasDrawer: /added to cart/i.test(text),
  });
})()
"""
    import json
    import tempfile

    js_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(js)
            js_path = fh.name
        script = f'''
set jsPath to "{js_path}"
set jsCode to read POSIX file jsPath as «class utf8»
tell application "Google Chrome"
  if (count of windows) = 0 then return "no_windows"
  try
    set r to execute active tab of front window javascript jsCode
    return r
  on error errMsg number errNum
    return "applescript_error:" & errNum & ":" & errMsg
  end try
end tell
'''
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return False, f"osascript failed: {exc}"
    finally:
        if js_path:
            with contextlib.suppress(OSError):
                Path(js_path).unlink(missing_ok=True)

    out = (proc.stdout or "").strip()
    if not out:
        return False, (proc.stderr or "empty").strip()[:200]
    if out.startswith("applescript_error:") or out.startswith("no_windows"):
        return False, out[:200]
    try:
        data = json.loads(out)
    except Exception:
        return False, out[:200]
    if data.get("dismissed"):
        return True, f"dismissed via {data.get('sel')}"
    return False, "drawer not found or already closed"

