/* Race player: a recorded replay, or the live race.
 *
 * Replay:  const player = ReplayPlayer.mount(element, "/api/races/3/replay");
 *
 * It fetches the replay JSON (the race worker's `Recorder`: the live
 * view's static message, the grid-ordered names, the sample rate, and
 * frames of [x, y, yaw, speed, laps, status, crashes, distance,
 * finish_time] per car) and plays it on a canvas, interpolating between
 * frames. Each car's distance and finish time are the engine's own, so the
 * order shown is the classification's. The second argument may also
 * be an already-parsed replay object. The returned handle has `ready` (a
 * promise that settles once the replay is loaded), `time`, `duration`,
 * and play(), pause(), seek(t), setSpeed(s) and destroy(). The replay
 * starts playing as soon as it is loaded.
 *
 * Controls: one play/pause button (space toggles it too), a speed control
 * "−  1×  +" from 0.25× to 2× in steps of 0.25, and a progress bar to click
 * or drag to seek.
 *
 * Cars carry no names on the track: the standings name them, each row's
 * colour mark matching its car. Clicking a row or a car on the track
 * circles that car, or uncircles it; any number of cars can be circled,
 * and a circled car is drawn over the others.
 *
 * A third argument, `{log, me}`, adds a car's log in the stage's top right
 * corner, over the track: `log`
 * is the URL of what the car printed (`{lines: [[t, text], ...],
 * truncated}`), `me` the car's name, circled from the start. The log
 * shows the lines printed up to the replay's time, scrolls with it while
 * scrolled to its end, seeks the replay to a line clicked, cuts long lines
 * or wraps them (a checkbox), and downloads as text.
 *
 * Live:    const live = ReplayPlayer.live(element, "ws://host:8765/ws", {me});
 *
 * It connects to cocoracer's live view WebSocket (cocoracer/web/
 * protocol.py: one `static` message, then a `dynamic` one about every
 * 150 ms) and draws the race the same way, a little behind the stream so
 * it can interpolate between messages. An F1-style standings tower sits on
 * the left; there are no other controls, but a tower row or a car clicked
 * circles that car as on a replay. `me`, optional, is the viewer's team,
 * circled from the start. The circled cars are kept by name for the life
 * of the page, so a car circled in one race is circled in the next, and
 * one uncircled stays so; a circled team absent from a race is simply not
 * drawn. Over the track, F1 start lights: dark
 * while the field waits on the grid for its start, three red lamps coming
 * on through the countdown, all out at the release (visual only, they
 * follow the race's countdown). With no race to connect to, it shows a
 * waiting message. It tries the socket only while a race runs, as the
 * header's status (views.py, `_STATUS_SCRIPT`) says through its
 * `cocoracer:status` events and its `data-running` at load: at once when
 * one starts, then every 500 ms until the race's socket is up. When the
 * socket closes after the race's finish it goes back to waiting, and
 * tries nothing until the status says the next race runs. On a page
 * without that status it retries on a blind 2 s timer instead. The
 * handle has `connected` and destroy().
 *
 * The drawing follows cocoracer's live view (cocoracer/web/index.html):
 * same road, start line, identity palette and status alphas, so a replay
 * reads as the same game as the projector; only it draws no names, which
 * cover the other cars in a crowded race. Vanilla JS, no build step.
 */
(function () {
  "use strict";

  // Identity palette by grid slot, the live view's exact list.
  const PALETTE = [
    "#ff4fd8", "#3f7bff", "#ff3f3f", "#35d13f", "#ffe13f", "#ff8c1a",
    "#a64dff", "#00e5d0", "#ff9ecb", "#7fd0ff", "#c0f24a", "#ff6a3d",
    "#8f9bff", "#4fbf8f", "#ffd28a", "#e050a0"
  ];
  const ALPHAS = { ghost: 0.4, paused: 0.6, dnf: 0.45 };
  // The same strings as team_views.py's `_STATUSES`.
  const STATUS_LABELS = {
    racing: "en course",
    paused: "pause",
    ghost: "ghost",
    finished: "terminé",
    done: "terminé",
    dnf: "DNF"
  };
  // Short tags for the live tower, where a full label does not fit.
  const STATUS_TAGS = { paused: "PAUSE", ghost: "GHOST", dnf: "DNF" };
  const SPEED_MIN = 0.25;
  const SPEED_MAX = 2;
  const SPEED_STEP = 0.25;
  // A car moving more than this between two samples was reset; it is
  // drawn at the earlier position rather than slid across the map.
  const JUMP_DIST = 20;
  // Live mode: how far behind the newest message the picture runs, so
  // there is always a later message to interpolate towards.
  const LIVE_DELAY = 0.2;
  // How often the player retries a race's socket while the status says the
  // race runs: the socket comes up a second or two after the race starts.
  const LIVE_RETRY_MS = 500;
  // On a page without the header's status, which says when a race runs, the
  // player retries the socket on this blind timer instead.
  const LIVE_FALLBACK_MS = 2000;
  const LIVE_BUFFER = 40;
  // The start lights in the gantry. They come on one by one, a quarter of
  // the countdown apart, the last a quarter before the release, and all go
  // out at the release. Visual only: they follow the race's own countdown,
  // whose length is read off the messages (the full value while the field
  // waits, or at the countdown's first tick).
  const START_LIGHTS = 3;

  function hslToHex(h, s, l) {
    const f = n => {
      const k = (n + h / 30) % 12;
      const c = l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1));
      return Math.round(255 * c).toString(16).padStart(2, "0");
    };
    return "#" + f(0) + f(8) + f(4);
  }

  function identityColor(index, name) {
    if (index < PALETTE.length) return PALETTE[index];
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return hslToHex(h % 360, 0.9, 0.55);
  }

  function wrapAngle(a) {
    return Math.atan2(Math.sin(a), Math.cos(a));
  }

  function fmtClock(s) {
    const t = Math.max(0, s);
    const m = Math.floor(t / 60);
    const r = t - m * 60;
    return String(m).padStart(2, "0") + ":" + r.toFixed(1).padStart(4, "0");
  }

  // "1:23,4": a race time with the French decimal comma.
  function fmtRaceTime(s) {
    const m = Math.floor(s / 60);
    const r = s - m * 60;
    const sec = r.toFixed(1).replace(".", ",");
    return m > 0 ? m + ":" + sec.padStart(4, "0") : sec;
  }

  // A time gap: "4,2 s" under a minute, "1:04,2" beyond.
  function fmtGap(s) {
    return s < 60 ? fmtRaceTime(s) + " s" : fmtRaceTime(s);
  }

  // "1,25×": French decimal comma, no trailing zeros.
  function fmtSpeed(v) {
    return String(Number(v.toFixed(2))).replace(".", ",") + "×";
  }

  function clampSpeed(v) {
    return Math.min(SPEED_MAX, Math.max(SPEED_MIN, v));
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // --- Track geometry -------------------------------------------------------

  function geometry(st) {
    const length = st.track_length;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const key of ["centerline", "left_wall", "right_wall"]) {
      for (const p of st[key]) {
        minx = Math.min(minx, p[0]); maxx = Math.max(maxx, p[0]);
        miny = Math.min(miny, p[1]); maxy = Math.max(maxy, p[1]);
      }
    }
    const pad = Math.max(10, length * 0.02);
    return {
      length,
      bounds: { minx: minx - pad, miny: miny - pad, maxx: maxx + pad, maxy: maxy + pad }
    };
  }

  // A car state between two samples. `a` and `b` are
  // {x, y, yaw, speed, progress}, progress being the engine's distance in
  // metres; discrete fields come from `a`.
  function lerpCar(a, b, k) {
    const kk = Math.hypot(b.x - a.x, b.y - a.y) > JUMP_DIST ? 0 : k;
    return {
      x: a.x + (b.x - a.x) * kk,
      y: a.y + (b.y - a.y) * kk,
      yaw: a.yaw + wrapAngle(b.yaw - a.yaw) * kk,
      speed: a.speed + (b.speed - a.speed) * kk,
      progress: a.progress + (b.progress - a.progress) * kk
    };
  }

  // The race at sim time t, between two samples {t, phase, countdown,
  // cars} with `a` the earlier. Discrete fields (laps, status, crashes,
  // finish time) hold `a`'s values.
  function between(a, b, t) {
    const span = b.t - a.t;
    const k = span > 0 ? Math.min(1, Math.max(0, (t - a.t) / span)) : 0;
    const byId = new Map(b.cars.map(c => [c.index, c]));
    const cars = a.cars.map(ca => {
      const cb = byId.get(ca.index) || ca;
      return Object.assign({}, ca, lerpCar(ca, cb, k));
    });
    return { phase: a.phase, countdown: Math.max(0, a.countdown - (t - a.t)), cars };
  }

  // --- The scene: track and cars on one canvas -----------------------------

  function createScene(root, stage, canvas) {
    const ctx = canvas.getContext("2d");
    let st = null;
    let geo = null;
    let view = null;
    let trackLayer = null;
    let carsPx = [];

    // Palette read off the page, so the canvas matches the CSS.
    function palette() {
      const cs = getComputedStyle(root);
      const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
      return {
        asphalt: v("--rp-asphalt", "#121310"),
        kerb: v("--rp-kerb", "#1a1b17"),
        line: v("--rp-line", "#2c2d27"),
        ink: v("--rp-ink", "#ece8dc"),
        muted: v("--rp-muted", "#8b887c"),
        mono: v("--rp-mono", "monospace")
      };
    }
    let colors = palette();

    function setTrack(staticMsg) {
      st = staticMsg;
      geo = geometry(st);
      const b = geo.bounds;
      const aspect = (b.maxx - b.minx) / (b.maxy - b.miny);
      stage.style.aspectRatio = String(Math.min(2.6, Math.max(1, aspect)));
      return geo;
    }

    function fit() {
      if (!st) return false;
      const b = geo.bounds;
      const rect = stage.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      const w = rect.width;
      const h = rect.height;
      const scale = Math.min(w / (b.maxx - b.minx), h / (b.maxy - b.miny));
      const ox = (w - (b.maxx - b.minx) * scale) / 2;
      const oy = (h - (b.maxy - b.miny) * scale) / 2;
      view = {
        dpr,
        scale,
        toPx(x, y) { return [ox + (x - b.minx) * scale, oy + (b.maxy - y) * scale]; }
      };
      colors = palette();
      prerenderTrack();
      return true;
    }

    function tracePolyline(t, points) {
      points.forEach((p, i) => {
        const [sx, sy] = view.toPx(p[0], p[1]);
        if (i === 0) t.moveTo(sx, sy); else t.lineTo(sx, sy);
      });
      t.closePath();
    }

    function prerenderTrack() {
      trackLayer = document.createElement("canvas");
      trackLayer.width = canvas.width;
      trackLayer.height = canvas.height;
      const t = trackLayer.getContext("2d");
      t.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
      // Road: the area between the two walls.
      t.beginPath();
      tracePolyline(t, st.left_wall);
      tracePolyline(t, st.right_wall);
      t.fillStyle = colors.line;
      t.fill("evenodd");
      t.strokeStyle = colors.muted;
      t.lineWidth = 1;
      t.beginPath();
      tracePolyline(t, st.left_wall);
      t.stroke();
      t.beginPath();
      tracePolyline(t, st.right_wall);
      t.stroke();
      // Dashed centerline.
      t.strokeStyle = "rgba(236, 232, 220, 0.3)";
      t.lineWidth = Math.max(1, 0.06 * view.scale);
      t.setLineDash([Math.max(2, 0.8 * view.scale), Math.max(2, 0.8 * view.scale)]);
      t.beginPath();
      tracePolyline(t, st.centerline);
      t.stroke();
      t.setLineDash([]);
      // Start line.
      const sl = st.start_line;
      const [ax, ay] = view.toPx(sl.left[0], sl.left[1]);
      const [bx, by] = view.toPx(sl.right[0], sl.right[1]);
      t.strokeStyle = colors.ink;
      t.lineWidth = Math.max(2, 0.1 * view.scale);
      t.beginPath();
      t.moveTo(ax, ay);
      t.lineTo(bx, by);
      t.stroke();
    }

    // A car, with no name: the standings name it. A circled car (`on`)
    // gets a ring at full opacity whatever its status: ink over a dark
    // halo, so it reads over the road, the walls and the cars packed
    // around it, and from the back of the room on the projector.
    function drawCar(car, color, on) {
      const vehicle = st.vehicle;
      const [cx, cy] = view.toPx(car.x, car.y);
      const len = Math.max(12, vehicle.length * view.scale);
      const wid = Math.max(6, vehicle.width * view.scale);
      const dnf = car.status === "dnf";
      if (on) {
        const r = len * 0.9 + 4;
        ctx.save();
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.strokeStyle = colors.asphalt;
        ctx.lineWidth = 6;
        ctx.stroke();
        ctx.strokeStyle = colors.ink;
        ctx.lineWidth = 3;
        ctx.stroke();
        ctx.restore();
      }
      ctx.save();
      ctx.globalAlpha = ALPHAS[car.status] ?? 1;
      ctx.translate(cx, cy);
      ctx.rotate(-car.yaw);
      ctx.fillStyle = dnf ? colors.muted : color;
      ctx.fillRect(-len / 2, -wid / 2, len, wid);
      // Dark nose band, so the heading reads at a glance.
      ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
      ctx.fillRect(len / 2 - len * 0.22, -wid / 2, len * 0.22, wid);
      ctx.restore();
      carsPx.push({ index: car.index, x: cx, y: cy, r: len });
    }

    // Cars are {index, name, x, y, yaw, status}; `carColors[index]` is
    // each one's identity colour, and `circled(car)` says whether it is
    // circled. The circled cars are drawn last, over the others.
    function render(cars, carColors, circled) {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      carsPx = [];
      if (!view) return;
      if (trackLayer) ctx.drawImage(trackLayer, 0, 0);
      ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
      const on = cars.filter(circled);
      for (const car of cars) {
        if (!on.includes(car)) drawCar(car, carColors[car.index], false);
      }
      for (const car of on) drawCar(car, carColors[car.index], true);
    }

    function clear() {
      st = null;
      geo = null;
      view = null;
      trackLayer = null;
      carsPx = [];
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    // The car under a click, or null.
    function hit(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      let best = null;
      let bestD = Infinity;
      for (const c of carsPx) {
        const d = Math.hypot(c.x - x, c.y - y);
        if (d < Math.max(14, c.r) && d < bestD) { bestD = d; best = c.index; }
      }
      return best;
    }

    return { setTrack, fit, render, clear, hit };
  }

  function observeResize(stage, onResize) {
    if (typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(() => onResize());
      ro.observe(stage);
      return () => ro.disconnect();
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }

  // --- Replay preprocessing -------------------------------------------------

  // A replay row's column indices, named as `race_worker.Recorder.COLUMNS`
  // names them (a server test holds the two together).
  const COL = {
    x: 0,
    y: 1,
    yaw: 2,
    speed: 3,
    laps: 4,
    status: 5,
    crashes: 6,
    distance: 7,
    finish_time: 8
  };

  // The replay's frames as the samples `between` reads, the live
  // player's shape.
  function prepare(replay) {
    const frames = replay.frames.map(f => ({
      t: f.t,
      phase: f.phase,
      countdown: f.countdown,
      cars: f.cars.map((car, c) => ({
        index: c,
        name: replay.names[c],
        x: car[COL.x],
        y: car[COL.y],
        yaw: car[COL.yaw],
        speed: car[COL.speed],
        laps: car[COL.laps],
        status: car[COL.status],
        crashes: car[COL.crashes],
        progress: car[COL.distance],
        finishTime: car[COL.finish_time]
      }))
    }));
    return {
      replay,
      frames,
      times: frames.map(f => f.t),
      colors: replay.names.map((name, i) => identityColor(i, name))
    };
  }

  // --- A car's log ---------------------------------------------------------

  // What one car printed, under the replay. Lines past the replay's time
  // are hidden, so the log reads as the car's console at that moment.
  // `onSeek(t)` is called with a clicked line's time.
  function createLog(onSeek) {
    const box = el("div", "rp-log");
    const head = el("div", "rp-log-head");
    const title = el("h3", null, "Journal");
    const note = el("span", "rp-log-note");
    const download = el("button", "rp-log-download", "Télécharger");
    download.type = "button";
    download.disabled = true;
    // Long lines are cut with "…" unless this is ticked; the choice is
    // remembered in this browser.
    const wrapBox = el("label", "rp-log-wrap-toggle");
    const wrap = document.createElement("input");
    wrap.type = "checkbox";
    wrapBox.append(wrap, document.createTextNode("Wrap"));
    head.append(title, note, wrapBox, download);
    const list = el("ol", "rp-log-lines");
    const empty = el("p", "rp-log-empty", "Chargement du journal…");
    box.append(head, list, empty);

    const WRAP_KEY = "rp-log-wrap";
    try { wrap.checked = localStorage.getItem(WRAP_KEY) === "1"; } catch (e) {}
    box.classList.toggle("rp-log-wrapped", wrap.checked);
    wrap.addEventListener("change", () => {
      const following =
        list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
      box.classList.toggle("rp-log-wrapped", wrap.checked);
      if (following) list.scrollTop = list.scrollHeight;
      try { localStorage.setItem(WRAP_KEY, wrap.checked ? "1" : "0"); } catch (e) {}
    });

    let lines = [];
    let items = [];
    let shown = 0;
    let t0 = 0;

    // How many lines were printed at or before sim time t.
    function upTo(t) {
      let lo = 0;
      let hi = lines.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (lines[mid][0] <= t + 1e-6) lo = mid + 1; else hi = mid;
      }
      return lo;
    }

    function showEmpty() {
      list.hidden = shown === 0;
      empty.hidden = shown > 0;
      if (!lines.length) {
        empty.textContent = "Tu peux utiliser des print() pour debug!";
      } else {
        empty.textContent = "Rien d'affiché à ce moment de la course.";
      }
    }

    function load(log, start) {
      t0 = start;
      lines = log && Array.isArray(log.lines) ? log.lines : [];
      list.textContent = "";
      items = lines.map(([t, text], i) => {
        const li = el("li", "rp-log-line");
        const body = el("span", "rp-log-text", text);
        body.title = text;
        li.append(el("span", "rp-log-t", fmtClock(t - t0)), body);
        li.dataset.i = String(i);
        li.hidden = true;
        list.append(li);
        return li;
      });
      shown = 0;
      if (log && log.truncated) {
        note.textContent = "Tronqué : ta voiture a dépassé ce qu'elle " +
          "peut afficher en une course, la suite est perdue.";
      }
      download.disabled = !lines.length;
      showEmpty();
    }

    function fail(text) {
      empty.hidden = false;
      empty.classList.add("rp-log-error");
      empty.textContent = text;
    }

    function update(t) {
      const n = upTo(t);
      if (n === shown) return;
      const following =
        list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
      for (let i = Math.min(n, shown); i < Math.max(n, shown); i++) {
        items[i].hidden = i >= n;
      }
      if (shown > 0) items[shown - 1].classList.remove("rp-log-now");
      if (n > 0) items[n - 1].classList.add("rp-log-now");
      shown = n;
      showEmpty();
      if (following) list.scrollTop = list.scrollHeight;
    }

    list.addEventListener("click", ev => {
      const li = ev.target.closest(".rp-log-line");
      if (li) onSeek(lines[Number(li.dataset.i)][0]);
    });

    download.addEventListener("click", () => {
      const text = lines.map(([t, line]) => fmtClock(t - t0) + "  " + line)
        .join("\n") + "\n";
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
      const a = el("a");
      a.href = url;
      a.download = "journal.txt";
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    });

    return { el: box, load, fail, update };
  }

  // --- The replay player ----------------------------------------------------

  function mount(root, source, options) {
    const opts = options || {};
    root.classList.add("rp");
    root.textContent = "";

    const stage = el("div", "rp-stage");
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-label", "Reprise de la course");
    const overlay = el("div", "rp-overlay");
    overlay.hidden = true;
    const message = el("div", "rp-message", "Chargement de la reprise…");
    stage.append(canvas, overlay, message);

    const panel = el("div", "rp-panel");
    panel.append(el("h3", null, "Classement"));
    const order = el("ol", "rp-order");
    panel.append(order);

    const bar = el("div", "rp-bar");
    const playBtn = el("button", "rp-play", "▶ Lecture");
    playBtn.title = "Lecture / pause (espace)";
    const speedBox = el("div", "rp-speed");
    speedBox.setAttribute("role", "group");
    speedBox.setAttribute("aria-label", "Vitesse de lecture");
    const slowerBtn = el("button", null, "−");
    slowerBtn.title = "Plus lent";
    slowerBtn.setAttribute("aria-label", "Plus lent");
    const speedLabel = el("span", "rp-speed-value", fmtSpeed(1));
    speedLabel.setAttribute("aria-live", "polite");
    const fasterBtn = el("button", null, "+");
    fasterBtn.title = "Plus rapide";
    fasterBtn.setAttribute("aria-label", "Plus rapide");
    speedBox.append(slowerBtn, speedLabel, fasterBtn);
    for (const b of [playBtn, slowerBtn, fasterBtn]) b.type = "button";
    // Progress bar: a plain element, so a click anywhere on it seeks there.
    const progress = el("div", "rp-progress");
    progress.setAttribute("role", "slider");
    progress.setAttribute("aria-label", "Position dans la course");
    const fill = el("div", "rp-progress-fill");
    progress.append(fill);
    const clock = el("span", "rp-clock");
    bar.append(playBtn, progress, clock, speedBox);
    for (const b of bar.querySelectorAll("button")) b.disabled = true;

    root.append(stage, panel, bar);

    // `seek` is hoisted; the log only calls it once the replay is loaded.
    const log = opts.log ? createLog(t => seek(t)) : null;
    if (log) stage.insertBefore(log.el, overlay);

    const scene = createScene(root, stage, canvas);
    let data = null;
    let time = 0;
    let t0 = 0;
    let t1 = 0;
    let speed = 1;
    let playing = false;
    let lastTick = null;
    let raf = null;
    // The circled cars, by grid index.
    const circled = new Set();
    let rows = [];
    let rowOrder = "";
    let destroyed = false;

    // --- sampling ---

    function frameIndex(t) {
      const times = data.times;
      let lo = 0;
      let hi = times.length - 1;
      if (t <= times[0]) return 0;
      if (t >= times[hi]) return hi;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= t) lo = mid; else hi = mid;
      }
      return lo;
    }

    function sample(t) {
      const i = frameIndex(t);
      return between(data.frames[i], data.frames[Math.min(i + 1, data.frames.length - 1)], t);
    }

    // --- drawing ---

    function fit() {
      if (scene.fit()) draw();
    }

    function draw() {
      if (!data) return;
      const state = sample(time);
      scene.render(state.cars, data.colors, car => circled.has(car.index));

      if (state.phase === "countdown" && state.countdown > 0) {
        overlay.hidden = false;
        overlay.textContent = String(Math.ceil(state.countdown - 1e-6));
      } else if (time >= t1 && state.phase === "finished") {
        overlay.hidden = false;
        overlay.textContent = "Terminé";
      } else {
        overlay.hidden = true;
      }
      updatePanel(state);
      updateBar();
      if (log) log.update(time);
    }

    // --- side panel ---

    function buildRows() {
      order.textContent = "";
      rows = data.replay.names.map((name, i) => {
        const li = el("li", "rp-row");
        const pos = el("span", "rp-pos");
        const dot = el("span", "rp-dot");
        dot.style.background = data.colors[i];
        const nm = el("span", "rp-name", name);
        nm.title = name;
        const status = el("span", "rp-status");
        const meta = el("span", "rp-meta");
        li.append(pos, dot, nm, status, meta);
        li.addEventListener("click", () => toggle(i));
        return { li, pos, status, meta, text: "", label: "" };
      });
    }

    function updatePanel(state) {
      // The tower's order (`rankLive`): finishers by finish time, then the
      // rest by distance, DNF last while the race runs.
      const ranked = rankLive(state.cars);
      const key = ranked.map(c => c.index).join(",");
      if (key !== rowOrder) {
        rowOrder = key;
        for (const car of ranked) order.append(rows[car.index].li);
      }
      ranked.forEach((car, place) => {
        const row = rows[car.index];
        const text =
          (car.laps === 1 ? "1 tour" : car.laps + " tours") + " · " +
          (car.crashes === 1 ? "1 accrochage" : car.crashes + " accrochages");
        if (row.text !== text) { row.text = text; row.meta.textContent = text; }
        const label = STATUS_LABELS[car.status] ?? car.status;
        if (row.label !== label) { row.label = label; row.status.textContent = label; }
        const p = String(place + 1);
        if (row.pos.textContent !== p) row.pos.textContent = p;
        row.li.classList.toggle("rp-on", circled.has(car.index));
        row.li.classList.toggle("rp-dnf", car.status === "dnf");
        row.li.classList.toggle("rp-paused", car.status === "paused");
        row.li.classList.toggle("rp-ghost", car.status === "ghost");
      });
    }

    function toggle(i) {
      if (!circled.delete(i)) circled.add(i);
      draw();
    }

    // --- controls ---

    function updateBar() {
      playBtn.textContent = playing ? "❚❚ Pause" : "▶ Lecture";
      const span = t1 - t0;
      const frac = span > 0 ? (time - t0) / span : 0;
      fill.style.width = (frac * 100).toFixed(3) + "%";
      progress.setAttribute("aria-valuetext", fmtClock(time - t0) + " / " + fmtClock(span));
      clock.innerHTML = "";
      const cur = el("strong", null, fmtClock(time - t0));
      clock.append(cur, document.createTextNode(" / " + fmtClock(span)));
      speedLabel.textContent = fmtSpeed(speed);
      slowerBtn.disabled = speed <= SPEED_MIN;
      fasterBtn.disabled = speed >= SPEED_MAX;
    }

    function seek(t) {
      time = Math.min(t1, Math.max(t0, t));
      draw();
    }

    function loop(now) {
      raf = null;
      if (!playing || destroyed) return;
      if (lastTick !== null) time += ((now - lastTick) / 1000) * speed;
      lastTick = now;
      if (time >= t1) {
        time = t1;
        playing = false;
      }
      draw();
      if (playing) raf = requestAnimationFrame(loop);
    }

    function play() {
      if (!data || playing) return;
      if (time >= t1) time = t0;
      playing = true;
      lastTick = null;
      raf = requestAnimationFrame(loop);
      updateBar();
    }

    function pause() {
      playing = false;
      if (raf !== null) cancelAnimationFrame(raf);
      raf = null;
      if (data) updateBar();
    }

    // Any value in range is kept as given; the buttons move in 0.25 steps.
    function setSpeed(s) {
      const v = Number(s);
      if (Number.isFinite(v)) speed = clampSpeed(v);
      if (data) updateBar();
    }

    // Snap to the step grid first, so a speed set off-grid by setSpeed
    // still lands on a clean value.
    function stepSpeed(dir) {
      const snapped = dir > 0
        ? Math.floor(speed / SPEED_STEP + 1e-9) * SPEED_STEP
        : Math.ceil(speed / SPEED_STEP - 1e-9) * SPEED_STEP;
      setSpeed(snapped + dir * SPEED_STEP);
    }

    playBtn.addEventListener("click", () => (playing ? pause() : play()));
    slowerBtn.addEventListener("click", () => stepSpeed(-1));
    fasterBtn.addEventListener("click", () => stepSpeed(1));

    function seekToPointer(ev) {
      const rect = progress.getBoundingClientRect();
      const frac = rect.width > 0
        ? Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width)) : 0;
      seek(t0 + frac * (t1 - t0));
    }
    let dragging = false;
    progress.addEventListener("pointerdown", ev => {
      if (!data || ev.button !== 0) return;
      dragging = true;
      progress.setPointerCapture(ev.pointerId);
      progress.classList.add("rp-dragging");
      seekToPointer(ev);
    });
    progress.addEventListener("pointermove", ev => { if (dragging) seekToPointer(ev); });
    const endDrag = () => { dragging = false; progress.classList.remove("rp-dragging"); };
    progress.addEventListener("pointerup", endDrag);
    progress.addEventListener("pointercancel", endDrag);

    // A car clicked is toggled; empty track does nothing.
    canvas.addEventListener("click", ev => {
      const best = scene.hit(ev.clientX, ev.clientY);
      if (best !== null) toggle(best);
    });

    function onKey(ev) {
      if (!data || ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const target = ev.target;
      const typing = target && (target.isContentEditable ||
        target.tagName === "TEXTAREA" || target.tagName === "SELECT" ||
        target.tagName === "INPUT");
      if (typing) return;
      if (ev.key === " " || ev.code === "Space") {
        ev.preventDefault();
        playing ? pause() : play();
      }
    }
    // Space on a focused button fires click on keyup; stop that, since
    // keydown already toggled playback.
    function onKeyUp(ev) {
      if ((ev.key === " " || ev.code === "Space") && ev.target &&
          ev.target.tagName === "BUTTON" && root.contains(ev.target)) {
        ev.preventDefault();
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("keyup", onKeyUp);

    const stopResize = observeResize(stage, fit);

    function fail(text) {
      root.classList.add("rp-failed");
      message.hidden = false;
      message.classList.add("rp-error");
      message.textContent = text;
    }

    const load = typeof source === "string"
      ? fetch(source, { credentials: "same-origin" }).then(res => {
          if (res.status === 404) throw new Error("Aucune reprise pour cette course.");
          if (!res.ok) throw new Error("Impossible de charger la reprise (" + res.status + ").");
          return res.json();
        })
      : Promise.resolve(source);

    const logLoad = log
      ? fetch(opts.log, { credentials: "same-origin", cache: "no-store" })
          .then(res => {
            if (!res.ok) throw new Error(String(res.status));
            return res.json();
          })
      : null;

    const ready = load.then(replay => {
      if (destroyed) return;
      if (!replay || !replay.frames || !replay.frames.length) {
        throw new Error("La reprise est vide.");
      }
      scene.setTrack(replay.static);
      data = prepare(replay);
      t0 = data.times[0];
      t1 = data.times[data.times.length - 1];
      time = t0;
      buildRows();
      const mine = replay.names.indexOf(opts.me);
      if (mine >= 0) circled.add(mine);
      message.hidden = true;
      for (const b of bar.querySelectorAll("button")) b.disabled = false;
      if (logLoad) {
        logLoad.then(entries => {
          if (destroyed) return;
          log.load(entries, t0);
          log.update(time);
        }).catch(() => log.fail("Impossible de charger le journal."));
      }
      fit();
      play();
    }).catch(err => {
      fail(err && err.message ? err.message : "Impossible de charger la reprise.");
      throw err;
    });
    ready.catch(() => {});

    return {
      ready,
      play,
      pause,
      seek,
      setSpeed,
      get time() { return time; },
      get duration() { return t1 - t0; },
      destroy() {
        destroyed = true;
        pause();
        document.removeEventListener("keydown", onKey);
        document.removeEventListener("keyup", onKeyUp);
        stopResize();
        root.textContent = "";
        root.classList.remove("rp");
      }
    };
  }

  // --- The live player ------------------------------------------------------

  // Once any car is "done" the race is over, and the server's classification
  // ranks done and DNF cars together by distance: a DNF that went far sits
  // above a done car that did not.
  function raceOver(cars) {
    return cars.some(c => c.status === "done");
  }

  // Tower order: finishers by finish time, then the rest by distance
  // covered, then DNF while the race runs (with the rest once it is over).
  // Over, it is the classification's order, from the engine's own finish
  // times and distances.
  function rankLive(cars) {
    const out = raceOver(cars);
    const group = c => (c.status === "finished" && c.finishTime !== null ? 0
      : c.status === "dnf" && !out ? 2 : 1);
    return cars.slice().sort((a, b) =>
      (group(a) - group(b)) ||
      (group(a) === 0 ? a.finishTime - b.finishTime : 0) ||
      (b.progress - a.progress) || (a.index - b.index));
  }

  // The tower's right column: the leader's lap, then each car's gap to it.
  function gapText(car, leader, place, length) {
    if (car.status === "dnf") return "";
    if (car.status === "finished" && car.finishTime !== null) {
      return place === 0 || leader.finishTime === null
        ? fmtRaceTime(car.finishTime)
        : "+" + fmtGap(car.finishTime - leader.finishTime);
    }
    // A car still out when the race ended (status "done") has no next lap.
    if (place === 0) return car.status === "done" ? "" : "Tour " + (car.laps + 1);
    if (leader.status === "finished") {
      return car.laps === 1 ? "1 tour" : car.laps + " tours";
    }
    const behind = leader.progress - car.progress;
    if (behind >= length) {
      const n = Math.floor(behind / length);
      return "+" + n + (n === 1 ? " tour" : " tours");
    }
    return "+" + Math.max(0, Math.round(behind)) + " m";
  }

  function live(root, wsUrl, options) {
    const opts = options || {};
    root.classList.add("rp", "rp-live", "rp-idle");
    root.textContent = "";

    const panel = el("div", "rp-panel rp-tower");
    const order = el("ol", "rp-order");
    panel.append(order);

    const stage = el("div", "rp-stage");
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-label", "Course en direct");
    const message = el("div", "rp-message", "En attente de la prochaine course");
    const lights = el("div", "rp-lights");
    lights.hidden = true;
    lights.setAttribute("aria-hidden", "true");
    const gantry = el("div", "rp-gantry");
    const lamps = [];
    for (let i = 0; i < START_LIGHTS; i++) {
      const lamp = el("span", "rp-lamp");
      lamps.push(lamp);
      gantry.append(lamp);
    }
    const lightsNote = el("div", "rp-lights-note", "En attente du départ");
    lights.append(gantry, lightsNote);
    stage.append(canvas, lights, message);

    root.append(panel, stage);

    const scene = createScene(root, stage, canvas);
    let socket = null;
    let retry = null;
    let raf = null;
    let destroyed = false;
    let geo = null;
    let colors = [];
    let names = [];
    let buffer = [];
    let clock = null;
    let lastNow = null;
    let rows = new Map();
    let rowOrder = "";
    let countdownTotal = 0;
    // The circled cars, by team name, since grid ids change each race;
    // kept for the life of the page, across races.
    const circled = new Set(opts.me ? [opts.me] : []);
    // The race running, as the header's status last said, or null between
    // races. Without a status on the page, nothing says it, so the player
    // behaves as if a race always ran and retries on the blind timer.
    const status = document.getElementById("status");
    const retryMs = status ? LIVE_RETRY_MS : LIVE_FALLBACK_MS;
    let running = status ? status.dataset.running || null : "unknown";
    // The race whose socket closed after its finish: not tried again until
    // the status says no race runs, or another race does.
    let spent = null;
    // The phase of the newest message on the socket, null before any.
    let lastPhase = null;

    function reset() {
      geo = null;
      colors = [];
      names = [];
      buffer = [];
      clock = null;
      lastNow = null;
      rows = new Map();
      rowOrder = "";
      order.textContent = "";
      lights.hidden = true;
      countdownTotal = 0;
      scene.clear();
    }

    // The gantry: dark while the field waits for its start, lit one lamp
    // at a time through the countdown, gone at the release.
    function showLights(state) {
      let lit = -1;
      if (state.phase === "waiting") {
        lit = 0;
      } else if (state.phase === "countdown" && countdownTotal > 0) {
        const elapsed = countdownTotal - state.countdown;
        lit = Math.min(START_LIGHTS,
          Math.floor(elapsed / (countdownTotal / (START_LIGHTS + 1)) + 1e-6));
      }
      lights.hidden = lit < 0;
      lightsNote.hidden = state.phase !== "waiting";
      lamps.forEach((lamp, i) => lamp.classList.toggle("rp-on", i < lit));
    }

    function waiting() {
      reset();
      // Back to the idle frame's shape, not the last track's.
      stage.style.aspectRatio = "";
      root.classList.add("rp-idle");
      message.hidden = false;
      if (raf !== null) cancelAnimationFrame(raf);
      raf = null;
    }

    function onStatic(msg) {
      reset();
      geo = scene.setTrack(msg);
      root.classList.remove("rp-idle");
      message.hidden = true;
      scene.fit();
    }

    // A dynamic message, reduced to what the renderer samples. Cars are
    // keyed by the protocol's grid id, which is also the palette slot.
    function onDynamic(msg) {
      if (!geo) return;
      const cars = msg.vehicles.map(v => {
        if (colors[v.id] === undefined) colors[v.id] = identityColor(v.id, v.name);
        names[v.id] = v.name;
        return {
          index: v.id,
          name: v.name,
          x: v.x,
          y: v.y,
          yaw: v.yaw,
          speed: v.speed,
          laps: v.laps,
          status: v.status,
          crashes: v.crashes,
          finishTime: v.finish_time,
          progress: v.distance
        };
      });
      // Whole seconds, so a viewer joining mid-countdown still gets the
      // configured length rather than what was left of it.
      if (msg.phase === "waiting" || msg.phase === "countdown") {
        countdownTotal = Math.max(countdownTotal, Math.ceil(msg.countdown - 1e-6));
      }
      lastPhase = msg.phase;
      buffer.push({ t: msg.time, phase: msg.phase, countdown: msg.countdown, cars });
      if (buffer.length > LIVE_BUFFER) buffer.shift();
      if (raf === null) raf = requestAnimationFrame(loop);
    }

    // The picture's own clock runs at wall-clock pace a little behind the
    // newest message, and is nudged towards that target so network
    // jitter does not show as stutter.
    function advanceClock(now) {
      const newest = buffer[buffer.length - 1].t;
      const target = newest - LIVE_DELAY;
      if (clock === null || Math.abs(clock - target) > 1) {
        clock = target;
      } else {
        clock += (now - lastNow) / 1000;
        clock += (target - clock) * 0.05;
      }
      lastNow = now;
      clock = Math.min(clock, newest);
    }

    function sample(t) {
      let j = buffer.findIndex(m => m.t > t);
      if (j === -1) j = buffer.length - 1;
      return between(buffer[Math.max(0, j - 1)], buffer[j], t);
    }

    function loop(now) {
      raf = null;
      if (destroyed || !geo || !buffer.length) return;
      if (lastNow === null) lastNow = now;
      advanceClock(now);
      const state = sample(clock);
      scene.render(state.cars, colors, car => circled.has(car.name));
      showLights(state);
      updateTower(state.cars);
      raf = requestAnimationFrame(loop);
    }

    function row(car) {
      let r = rows.get(car.index);
      if (r) return r;
      const li = el("li", "rp-trow");
      const pos = el("span", "rp-pos");
      const stripe = el("span", "rp-stripe");
      stripe.style.background = colors[car.index];
      const nm = el("span", "rp-name", car.name);
      nm.title = car.name;
      const tag = el("span", "rp-tag");
      const gap = el("span", "rp-gap");
      li.append(pos, stripe, nm, tag, gap);
      li.addEventListener("click", () => toggle(car.name));
      r = { li, pos, tag, gap };
      rows.set(car.index, r);
      return r;
    }

    function setText(node, text) {
      if (node.textContent !== text) node.textContent = text;
    }

    function updateTower(cars) {
      const ranked = rankLive(cars);
      const key = ranked.map(c => c.index).join(",");
      if (key !== rowOrder) {
        rowOrder = key;
        for (const car of ranked) order.append(row(car).li);
      }
      const leader = ranked[0];
      ranked.forEach((car, place) => {
        const r = row(car);
        setText(r.pos, String(place + 1));
        setText(r.tag, STATUS_TAGS[car.status] ?? "");
        setText(r.gap, gapText(car, leader, place, geo.length));
        r.li.classList.toggle("rp-dnf", car.status === "dnf");
        r.li.classList.toggle("rp-ghost", car.status === "ghost");
        r.li.classList.toggle("rp-paused", car.status === "paused");
        r.li.classList.toggle("rp-finished",
          car.status === "finished" || car.status === "done");
        r.li.classList.toggle("rp-on", circled.has(car.name));
      });
    }

    // The animation loop redraws the track and the tower on its next
    // frame; the row's mark is set now, so a click shows at once.
    function toggle(name) {
      if (!circled.delete(name)) circled.add(name);
      for (const [index, r] of rows) {
        if (names[index] === name) r.li.classList.toggle("rp-on", circled.has(name));
      }
    }

    function wanted() {
      return !destroyed && running !== null && running !== spent;
    }

    function schedule() {
      if (retry === null && socket === null && wanted()) {
        retry = setTimeout(connect, retryMs);
      }
    }

    function connect() {
      retry = null;
      if (socket !== null || !wanted()) return;
      let ws;
      try {
        ws = new WebSocket(wsUrl);
      } catch (err) {
        schedule();
        return;
      }
      socket = ws;
      ws.onmessage = ev => {
        if (socket !== ws) return;
        let msg;
        try { msg = JSON.parse(ev.data); } catch (err) { return; }
        if (msg.type === "static") onStatic(msg);
        else if (msg.type === "dynamic") onDynamic(msg);
      };
      ws.onclose = () => {
        if (socket !== ws) return;
        socket = null;
        if (destroyed) return;
        // A socket that closes after the finish is the race ending; one
        // that never opened, or dropped mid-race, is tried again.
        if (status && lastPhase === "finished") spent = running;
        lastPhase = null;
        waiting();
        schedule();
      };
    }

    // The header's status, after each poll: try the socket at once when a
    // race runs, and stop trying when none does.
    function onStatus(ev) {
      running = ev.detail.running || null;
      if (running === null) spent = null;
      if (!wanted()) {
        if (retry !== null) clearTimeout(retry);
        retry = null;
      } else if (retry === null) {
        connect();
      }
    }

    // A car clicked is toggled; empty track does nothing.
    canvas.addEventListener("click", ev => {
      const best = scene.hit(ev.clientX, ev.clientY);
      if (best !== null && names[best] !== undefined) toggle(names[best]);
    });

    const stopResize = observeResize(stage, () => scene.fit());
    if (status) document.addEventListener("cocoracer:status", onStatus);
    connect();

    return {
      get connected() { return geo !== null; },
      destroy() {
        destroyed = true;
        if (status) document.removeEventListener("cocoracer:status", onStatus);
        if (retry !== null) clearTimeout(retry);
        retry = null;
        if (raf !== null) cancelAnimationFrame(raf);
        raf = null;
        if (socket) {
          const ws = socket;
          socket = null;
          ws.onmessage = null;
          ws.onclose = null;
          ws.close();
        }
        stopResize();
        root.textContent = "";
        root.classList.remove("rp", "rp-live", "rp-idle");
      }
    };
  }

  window.ReplayPlayer = { mount, live };
})();
