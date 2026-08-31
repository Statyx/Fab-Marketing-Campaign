#!/usr/bin/env python
"""Record the Customer 360 Cockpit tour: a screen capture of the real app, driven by Playwright.

Why this file exists at all
---------------------------
The previous teaser (``marketing/teaser-c360.mp4``) was produced outside this repository.
Nothing in git says how, so it cannot be re-cut when a figure or a label changes -- and the
labels have changed repeatedly. A binary with no owner is exactly the drift this project
guards against elsewhere ("one Fabric item has ONE owning generator"). This is the missing
owner for the video.

Why a capture is possible now and was not before
------------------------------------------------
The old teaser had to be typographic: a suggested question took 61.5 s on average and 141 s at
worst, which is unfilmable. Freezing the recorded answers cut that to ~5 s, so the app can now
be filmed actually answering. The video shows instead of asserting.

Why two phases
--------------
The app signs in with MSAL configured for ``sessionStorage`` (``app-v2/src/services/msal.ts``).
Playwright's ``storage_state`` carries cookies and localStorage only, so a saved profile does
NOT replay the session. A recording started cold would therefore film the Microsoft account
picker -- i.e. the operator's real e-mail address -- into a public marketing video.

``--login`` takes the sign-in in a separate, *unrecorded* browser and dumps sessionStorage.
``--record`` restores it into a fresh recorded context before the first navigation, so the
account picker is never on camera. MSAL tokens last about an hour: run ``--record`` promptly.

Why the header identity is masked
---------------------------------
``AppShell.tsx`` renders ``{user.name}`` in the header on every persona page. That is a real
person's display name, and the hosting URL is public. ``_PREFLIGHT_JS`` rewrites it to a
neutral label for the duration of the capture.

Why a drawn cursor
------------------
Playwright's video does not composite the OS pointer. Without a synthetic one the tour reads as
a series of teleporting clicks. ``_PREFLIGHT_JS`` draws one and follows real mousemove events,
which ``page.mouse.move(..., steps=N)`` emits once per interpolation step.

Usage
-----
    python marketing/record_tour.py --login     # sign in once, off camera
    python marketing/record_tour.py --record    # drive + film the tour
    python marketing/record_tour.py --encode    # webm -> mp4
    python marketing/record_tour.py --all       # login, then record, then encode

Verify the artefact, never the exit code: ``--record`` prints the produced file and its
duration, and ``--contact-sheet`` extracts frames so the result can actually be looked at
before anyone proposes committing it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
HERE = ROOT / "marketing"

AUTH_FILE = HERE / "_auth.json"
CAPTURE_DIR = HERE / "_capture"
OUT_MP4 = HERE / "tour-c360.mp4"
SHEET_DIR = HERE / "_frames"

# 1080p, and deliberately not tied to the sign-in window: `--login` runs headed and has to fit
# on a real screen, but `--record` is headless, so nothing forces the film to be smaller than
# the delivery format. Keeping them equal cost the first two cuts a native-resolution master.
VIDEO_W, VIDEO_H = 1920, 1080

# The tour, as beats. Each is (path-or-None, seconds, note). `None` means "already there".
# Order is a narrative, not the registry order: portfolio -> who is leaving -> what it costs
# -> why, answered by the assistant. Ending on the answer is the point; ending on a chart is
# an anticlimax.
PERSONA_ORDER = ["direction", "retention", "commerce", "marketing"]

# Marketing's first starter, which `pickVaried` picks deterministically (FAMILY_ORDER puts
# 'model' first, and this is the first 'model' suggestion in the registry). Clicking a
# suggestion the app itself offers is more honest than typing a question chosen for the camera,
# and this one lands exactly on the panel above it -- e-mail pressure per campaign.
ASK_HINT = "Quelle campagne envoie le plus"

# The disclosure button under a rendered answer (AgentPage.tsx). Its appearance is the signal
# that the assistant has finished: it does not exist while the replay placeholder is on screen.
# Anchoring on it lets the film wait for the answer instead of for a duration.
ANSWER_MARK = "Source et requête"


def app_url() -> str:
    """Read the hosting URL from ``rayfin.yml`` rather than pasting it here.

    A pasted URL survives a redeploy into a new workspace and then points at the old app --
    the same failure mode as a pasted Fabric endpoint. ``allowedRedirectUris`` is written by
    Rayfin on first deploy and is the closest thing to a source of truth that is tracked.
    """
    import yaml

    cfg = yaml.safe_load((ROOT / "app-v2" / "rayfin" / "rayfin.yml").read_text(encoding="utf-8"))
    uris: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.startswith("https://") and "webapp." in node:
            uris.append(node.rstrip("/"))

    walk(cfg)
    if not uris:
        sys.exit("no hosting URL found in app-v2/rayfin/rayfin.yml -- pass --url explicitly")
    return uris[0]


# Runs before any page script, on every document. Three jobs, all cosmetic-but-load-bearing:
# draw a cursor, keep the restored session, and take the operator's name off camera.
_PREFLIGHT_JS = """
(() => {
  // 1. Session restore. `sessionStorage` is per-origin and per-tab, so it must be replanted
  //    on every document -- but never over a fresher value MSAL has just written.
  try {
    const seed = __SESSION__;
    for (const [k, v] of Object.entries(seed)) {
      if (sessionStorage.getItem(k) === null) sessionStorage.setItem(k, v);
    }
  } catch (e) { /* a restore failure must not stop the page from loading */ }

  const paint = () => {
    // 2. Synthetic cursor. Playwright's recorder does not composite the OS pointer.
    if (!document.getElementById('__tour_cursor')) {
      const c = document.createElement('div');
      c.id = '__tour_cursor';
      c.style.cssText = [
        'position:fixed', 'left:-50px', 'top:-50px', 'width:22px', 'height:22px',
        'border-radius:50%', 'pointer-events:none', 'z-index:2147483647',
        'background:rgba(255,255,255,0.85)',
        'box-shadow:0 0 0 2px rgba(15,23,42,0.55), 0 2px 10px rgba(0,0,0,0.45)',
        'transition:transform 90ms ease-out', 'transform:translate(-50%,-50%)',
      ].join(';');
      document.body.appendChild(c);
      document.addEventListener('mousemove', (e) => {
        c.style.left = e.clientX + 'px';
        c.style.top = e.clientY + 'px';
      }, true);
      document.addEventListener('mousedown', () => {
        c.style.transform = 'translate(-50%,-50%) scale(0.6)';
      }, true);
      document.addEventListener('mouseup', () => {
        c.style.transform = 'translate(-50%,-50%) scale(1)';
      }, true);
    }

    // 3. Identity masking. AppShell renders the signed-in display name next to "Se deconnecter".
    //    The hosting URL is public and so is this video; the name is neither.
    for (const b of document.querySelectorAll('header button')) {
      if (b.textContent && b.textContent.trim().startsWith('Se d')) {
        const holder = b.parentElement;
        const span = holder && holder.querySelector('span');
        if (span && span.textContent !== 'Demo') span.textContent = 'Demo';
      }
    }
  };

  if (document.body) paint();
  document.addEventListener('DOMContentLoaded', paint);
  setInterval(paint, 400);  // React re-renders the header; repaint rather than fight it
})();
"""


def preflight(session: dict[str, str]) -> str:
    return _PREFLIGHT_JS.replace("__SESSION__", json.dumps(session))


# --------------------------------------------------------------------------------------
# phase 1 -- sign in, off camera
# --------------------------------------------------------------------------------------
def cmd_login(url: str) -> None:
    from playwright.sync_api import sync_playwright

    print(f"opening {url} -- sign in with your Microsoft account, then come back here")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--window-size=1440,900"])
        ctx = browser.new_context(viewport={"width": 1360, "height": 860})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        # Wait for the app to be *authenticated*, not merely loaded: the persona nav only
        # renders past the guard. Generous timeout -- a human is typing a password and may
        # be doing MFA on a phone.
        print("waiting for sign-in to complete (up to 5 min)...")
        page.wait_for_selector('a[href="/agent/direction"]', timeout=300_000)

        # Grab the session the instant it exists, and persist before doing anything else.
        #
        # The window can vanish under us: `main.tsx` broadcasts and closes on an auth hash, and
        # a first attempt lost a good sign-in on the *next* statement -- sessionStorage had been
        # read, `storage_state()` then threw TargetClosedError and nothing was written. So the
        # order is: read, write, and only then reach for the optional extras, each guarded.
        session = page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))")
        payload: dict = {"session": session, "state": None}
        AUTH_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        # Cookies and localStorage are belt-and-braces only: MSAL is configured for
        # sessionStorage, so the replay does not depend on them. Losing them is not a failure.
        try:
            payload["state"] = ctx.storage_state()
            AUTH_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 -- any browser-teardown error is equivalent here
            print(f"note: cookies not captured ({type(exc).__name__}) -- sessionStorage is enough")

        keys = len(session)
        has_msal = any("msal" in k.lower() or "authority" in k.lower() for k in session)
        try:
            browser.close()
        except Exception:  # noqa: BLE001 -- already gone is the expected case here
            pass

    if not has_msal:
        sys.exit(
            f"captured {keys} keys but none look like MSAL -- the sign-in did not complete.\n"
            "re-run --login and leave the window open until it closes on its own."
        )
    print(f"captured {keys} sessionStorage keys -> {AUTH_FILE.name} (gitignored)")
    print("run --record within the hour: MSAL tokens expire.")


# --------------------------------------------------------------------------------------
# phase 2 -- drive and film
# --------------------------------------------------------------------------------------
def _glide(page, x: float, y: float) -> None:
    """Move in interpolated steps so the drawn cursor travels instead of teleporting."""
    page.mouse.move(x, y, steps=28)


def _glide_click(page, locator, settle_ms: int = 550) -> None:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if box is None:
        raise RuntimeError("element has no box -- it is not visible")
    _glide(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(settle_ms)
    locator.click()


def _slow_scroll(page, total: int, steps: int = 10, pause: int = 50) -> None:
    """Wheel in small increments: one big jump is a cut, not a scroll."""
    per = total / steps
    for _ in range(steps):
        page.mouse.wheel(0, per)
        page.wait_for_timeout(pause)


def _scroll_top(page) -> None:
    """Return to the top of the page.

    Not `_slow_scroll(-n)`: a persona whose panel is a long table hits the bottom of the
    document, so the wheel down and the wheel up are not symmetric and the page lands
    mid-table. That is how the first cut asked about e-mail pressure while the campaign that
    causes it was scrolled off the top of the screen.
    """
    page.evaluate("() => window.scrollTo({ top: 0, behavior: 'smooth' })")
    # 1500, not 900: a smooth scroll from the bottom of a long page takes longer than the eye
    # expects, and the first cut caught this mid-flight -- the closing frame showed the table
    # header instead of the outlier row it was supposed to land on.
    page.wait_for_timeout(1500)


def _wait_for_panels(page, timeout: int = 45_000) -> None:
    """Block until the panels have actually rendered -- not until the network merely goes quiet.

    `wait_for_load_state("networkidle")` is not enough on its own. On a client-side route change
    there is often nothing in flight at the moment it is called, because the panels have not
    mounted yet and so have not issued their DAX queries: it returns immediately and the pose that
    follows is spent filming `QueryState`'s spinner.

    That is exactly what the second cut did, and the contact sheet shows both halves of it: the
    second frame of the film was "Chargement des donnees..." twice over, and the two personas that
    issue the most queries (Direction, 8 KPIs; Commerce, 12 segments) held about twice as long as
    the other two -- the whole surplus being spinner, not figures.

    Gate on the spinner's own class instead, so the hold that follows is the same amount of
    *readable* screen for every persona whatever its query count. `.animate-spin` rather than the
    French loading string: the anchor should not move if the wording is ever translated.
    """
    # The settle is the point: it lets React mount the panel so the spinner exists to be waited
    # for. Without it this races and returns on a screen that has not started loading yet.
    page.wait_for_timeout(400)
    page.wait_for_function("() => !document.querySelector('.animate-spin')", timeout=timeout)
    page.wait_for_timeout(400)  # let the numbers paint before the pose starts


# At 1080p every persona fits on one screen -- KPI row, chart and the whole suggestion rail --
# so there is nothing left to scroll to. Verified frame by frame: Direction, Retention and
# Commerce render complete, and Marketing's table shows from the outlier down to the footer.
# The scrolling that these values used to carry was a habit inherited from the 900px cut; at
# this size it only moved readable content off screen. Kept as a dial, not deleted: a persona
# that grows a third panel will need it back.
SCROLL_BY = {"direction": 0, "retention": 0, "commerce": 0, "marketing": 0}


def cmd_record(url: str, ask: bool) -> None:
    from playwright.sync_api import sync_playwright

    if not AUTH_FILE.exists():
        sys.exit(f"{AUTH_FILE.name} missing -- run --login first")

    saved = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    for stale in CAPTURE_DIR.glob("*.webm"):
        stale.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Recording starts the instant the context exists, so the cold load is always filmed.
        # It cannot be avoided -- but it can be measured, and trimmed off exactly rather than
        # by a guessed constant.
        t0 = time.monotonic()
        ctx = browser.new_context(
            viewport={"width": VIDEO_W, "height": VIDEO_H},
            storage_state=saved["state"],
            record_video_dir=str(CAPTURE_DIR),
            record_video_size={"width": VIDEO_W, "height": VIDEO_H},
            locale="fr-FR",
            device_scale_factor=1,
        )
        ctx.add_init_script(preflight(saved["session"]))
        page = ctx.new_page()

        # --- beat 0: the front page, four personas -------------------------------------
        page.goto(url, wait_until="networkidle")
        try:
            page.wait_for_selector('a[href="/agent/direction"]', timeout=45_000)
        except Exception:
            page.screenshot(path=str(CAPTURE_DIR / "auth-failure.png"))
            browser.close()
            sys.exit(
                "the app never reached its signed-in state -- the saved session is stale.\n"
                f"see {CAPTURE_DIR / 'auth-failure.png'} and re-run --login"
            )
        # The persona cards exist before the cover's own KPI strip has loaded, so this selector
        # proves we are signed in, not that there is anything worth filming yet. Wait for the
        # figures too, and only then start the clock: `ready` is what --encode trims, so measuring
        # it here is what guarantees the film opens on a complete cover rather than on a spinner.
        _wait_for_panels(page)
        ready = time.monotonic() - t0
        _glide(page, VIDEO_W * 0.5, VIDEO_H * 0.45)
        page.wait_for_timeout(2200)

        # Enter through a card, not a URL: the audience should see the app being used.
        _glide_click(page, page.locator(f'a[href="/agent/{PERSONA_ORDER[0]}"]').first)
        page.wait_for_timeout(300)

        # --- beats 1..4: one persona at a time, charts first ----------------------------
        for i, key in enumerate(PERSONA_ORDER):
            page.wait_for_load_state("networkidle")
            # The panels are DAX round-trips. Wait for them to be on screen -- not for the network
            # to fall quiet, which happens before they have even asked (see _wait_for_panels).
            _wait_for_panels(page)

            # Read the persona top-down: KPI row, then the chart underneath, then back up.
            _glide(page, VIDEO_W * 0.35, VIDEO_H * 0.38)
            # Longer than it looks it needs: with the scroll beats gone this hold is the only
            # time the viewer gets on the persona, and a full 1080p screen of figures does not
            # read in under three seconds.
            page.wait_for_timeout(1800)
            by = SCROLL_BY.get(key, 440)
            if by:
                _slow_scroll(page, by)
                page.wait_for_timeout(1400)
                _glide(page, VIDEO_W * 0.30, VIDEO_H * 0.55)
                page.wait_for_timeout(700)
                _scroll_top(page)

            last = i == len(PERSONA_ORDER) - 1
            if not last:
                _glide_click(page, page.locator(f'a[href="/agent/{PERSONA_ORDER[i+1]}"]').first)
                page.wait_for_timeout(250)

        # --- beat 5: ask, and let the assistant answer ----------------------------------
        if ask:
            starter = page.locator(f'button:has-text("{ASK_HINT}")').first
            if starter.count() == 0:
                # Never invent a question for the camera: if the deterministic starter is not
                # on screen the registry changed, and the tour ends on the chart instead.
                print(f"WARNING: starter matching {ASK_HINT!r} not found -- skipping the question")
            else:
                _glide_click(page, starter)
                # The replay announces itself ("Réponse enregistrée — restitution…") for a
                # duration drawn at random between 3 and 8 s, then reveals. Waiting a fixed
                # 10.2 s was sized for the worst draw and spent the surplus on a static card:
                # two consecutive frames of the contact sheet were the same placeholder, which
                # is the single longest dead beat in the film. Wait for the answer to exist,
                # then hold -- the pose is then the same length whatever the draw.
                try:
                    page.wait_for_selector(f'text={ANSWER_MARK}', timeout=40_000)
                except Exception:
                    print(f"WARNING: {ANSWER_MARK!r} never appeared -- holding a fixed 10s instead")
                    page.wait_for_timeout(10_000)
                page.wait_for_timeout(3200)
                # The chat pins itself to its newest message (AgentPage scrolls `bottom` into
                # view), which drags the window past the campaign table -- so the answer names
                # a campaign the viewer cannot see. Close by going back up: the assistant says
                # which campaign, then the row it is talking about is on screen. Do not "fix"
                # this in the app; auto-scrolling to a new message is correct chat behaviour.
                _scroll_top(page)
                page.wait_for_timeout(2400)

        page.wait_for_timeout(800)
        video = page.video
        path = video.path() if video else None
        ctx.close()  # flush: the webm is only finalised on context close
        browser.close()

    produced = sorted(CAPTURE_DIR.glob("*.webm"))
    if not produced:
        sys.exit("playwright produced no video")
    raw = pathlib.Path(path) if path and pathlib.Path(path).exists() else produced[-1]
    (CAPTURE_DIR / "timing.json").write_text(
        json.dumps({"ready_seconds": round(ready, 2)}), encoding="utf-8"
    )
    print(f"raw capture: {raw}  ({raw.stat().st_size / 1e6:.1f} MB)")
    print(f"cold load measured at {ready:.1f}s -- --encode trims it off")


# --------------------------------------------------------------------------------------
# phase 3 -- encode and inspect
# --------------------------------------------------------------------------------------
def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def cmd_encode() -> None:
    raws = sorted(CAPTURE_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not raws:
        sys.exit("no capture found -- run --record first")
    raw = raws[-1]

    # Drop the cold load. `--record` measured how long the app took to reach its signed-in
    # state; everything before that is a white screen. Trimming a measured value rather than
    # a guessed constant means a slower day produces a shorter trim, not a clipped first beat.
    trim = 0.0
    timing = CAPTURE_DIR / "timing.json"
    if timing.exists():
        trim = max(0.0, json.loads(timing.read_text(encoding="utf-8"))["ready_seconds"] - 0.4)

    subprocess.run(
        [
            _ffmpeg(), "-y", "-ss", f"{trim:.2f}", "-i", str(raw),
            "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-pix_fmt", "yuv420p",
            # Even dimensions, and a constant frame rate: Playwright's webm is variable and
            # some players stutter on it.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=25",
            "-movflags", "+faststart",
            "-an",
            # Do not carry the source filename / encoder chatter into a public file.
            "-map_metadata", "-1",
            str(OUT_MP4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    size = OUT_MP4.stat().st_size / 1e6
    print(f"encoded -> {OUT_MP4.relative_to(ROOT)}  ({size:.1f} MB)")


def cmd_contact_sheet(count: int) -> None:
    """Extract frames so the result is looked at, not assumed.

    The exit code of an encoder says nothing about what is on screen. The previous teaser was
    only ever cleared by pulling frames and reading them.
    """
    if not OUT_MP4.exists():
        sys.exit("no mp4 -- run --encode first")
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    for stale in SHEET_DIR.glob("*.png"):
        stale.unlink()
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(OUT_MP4),
         "-vf", f"fps=1/{max(1, count)}", str(SHEET_DIR / "f%03d.png")],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    frames = sorted(SHEET_DIR.glob("*.png"))
    print(f"{len(frames)} frames -> {SHEET_DIR.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--login", action="store_true", help="sign in once, off camera")
    ap.add_argument("--record", action="store_true", help="drive and film the tour")
    ap.add_argument("--encode", action="store_true", help="convert the capture to mp4")
    ap.add_argument("--contact-sheet", type=int, metavar="EVERY_N_SECONDS", default=0,
                    help="extract frames from the mp4 so it can be reviewed")
    ap.add_argument("--all", action="store_true", help="login, record, encode")
    ap.add_argument("--no-ask", action="store_true", help="tour the charts only, ask nothing")
    ap.add_argument("--url", default=None, help="override the hosting URL")
    args = ap.parse_args()

    url = args.url or app_url()

    if not any([args.login, args.record, args.encode, args.contact_sheet, args.all]):
        ap.error("pick a phase: --login, --record, --encode, --contact-sheet or --all")

    if args.login or args.all:
        cmd_login(url)
    if args.record or args.all:
        cmd_record(url, ask=not args.no_ask)
    if args.encode or args.all:
        cmd_encode()
    if args.contact_sheet:
        cmd_contact_sheet(args.contact_sheet)


if __name__ == "__main__":
    main()
