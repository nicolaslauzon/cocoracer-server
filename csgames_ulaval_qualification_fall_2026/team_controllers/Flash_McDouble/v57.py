"""
my_car.py - racing controller for the "defi vroom vroom".

Strategy
--------
reset():
  * build a minimum-curvature racing line inside the track corridor
    (gradient descent on the lateral offset, kept WALL_MARGIN from walls)
  * build friction-circle speed profiles for several grip guesses
    (grip is unknown; we start optimistic and back off after wall crashes)
step() (40 Hz, must average < 20 ms):
  * pure pursuit on the racing line, wheelbase estimated online from yaw rate
  * laser scan is compared against the expected wall distance (ray cast on
    the wall polylines); any beam much shorter than the wall is another car
  * cars ahead on our path -> shift the line to pass on the roomier side,
    and limit speed (time-gap rule) until we are laterally clear
  * cars alongside -> never steer into them
  * crash detection (teleport / instant stop) -> lower grip guess if it was
    a wall crash, keep it if it was a car contact
Only numpy + stdlib are used.
"""

import math
import os

import numpy as np

from cocoracer import Controller, TrackInfo

DT = 1.0 / 40.0
TWO_PI = 2.0 * math.pi


def _wrap(a):
    return (a + math.pi) % TWO_PI - math.pi


class MyCar(Controller):
    # ---------------- physics guesses (grip adapts online) ----------------
    A_MAX0 = 8.0          # friction circle radius guess (m/s^2)
    A_ENGINE = 5.0        # forward acceleration guess (m/s^2)
    LAT_FRAC = 0.92       # share of grip used for cornering in the profile
    BRK_FRAC = 0.85       # share of remaining grip used for braking
    V_MAX = 30.0
    GRIP_SCALES = tuple(0.9 ** k for k in range(10))   # 1.0 ... 0.39
    GRIP_RECOVER_LAPS = 3  # clean laps before trying one notch more grip

    # ---------------- geometry ----------------
    WALL_MARGIN = 2.0     # racing line distance to the walls
    PASS_MARGIN = 1.6     # allowed wall distance while passing
    W_CLEAR = 3.0         # lateral clearance to an obstacle surface (m)
    CAR_DETECT_TOL = 1.0  # beam shorter than wall by this => car

    # ---------------- pursuit / speed ----------------
    WB0 = 2.5             # wheelbase guess (m), refined online
    LD_BASE = 3.0
    LD_GAIN = 0.33        # lookahead = LD_BASE + LD_GAIN * v
    LD_MAX = 15.0
    SPEED_LOOKAHEAD_T = 0.15
    DELTA_RATE = 5.0      # m/s lateral shift rate of the avoidance offset
    AVOID_HOLD = 1.2      # s to hold an avoidance offset after last sighting
    GAP_MIN = 3.5         # m, bumper gap we keep behind a car
    T_GAP = 0.45          # s, time gap rule: v <= (gap - GAP_MIN) / T_GAP

    # ============================ setup ============================
    def reset(self, track_info: TrackInfo) -> None:
        self._ok = False
        self._init_state()
        try:
            self._build(track_info)
            self._ok = True
        except Exception as e:  # never die in reset: fall back to reactive driving
            print("reset: track build failed, reactive fallback:", repr(e))

    def _init_state(self):
        self.frame = 0
        self.prev = None            # (x, y, yaw, speed)
        self.delta = 0.0            # lateral avoidance offset (m, +left)
        self.avoid_side = 0         # +1 passing left, -1 passing right, 0 none
        self.avoid_until = -1
        self.grip_idx = 0
        self.crashes = 0
        self.wb = self.WB0
        self.wb_samples = []
        self.lap = 0
        self.lap_start = 0
        self.lap_crash = 0
        self.clean_laps = 0
        self.last_ic = None
        self.near_car = 0           # frame of last close car sighting
        self.max_alat = 0.0
        self.step_ms = 0.0

    def _build(self, ti):
        pts = ti.centerline if ti is not None else ()
        if len(pts) >= 10:
            arr = np.array([[p.x, p.y, p.distance_wall_left, p.distance_wall_right]
                            for p in pts], dtype=float)
        else:  # try the csv next to this file
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "centerline.csv")
            arr = np.loadtxt(path, delimiter=",", skiprows=1)
        cx, cy, wl, wr = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
        # drop duplicated closing point
        if math.hypot(cx[0] - cx[-1], cy[0] - cy[-1]) < 1e-6:
            cx, cy, wl, wr = cx[:-1], cy[:-1], wl[:-1], wr[:-1]
        n = len(cx)
        self.n = n
        self.cx, self.cy, self.wl, self.wr = cx, cy, wl, wr
        tx = np.roll(cx, -1) - np.roll(cx, 1)
        ty = np.roll(cy, -1) - np.roll(cy, 1)
        tn = np.hypot(tx, ty)
        tx, ty = tx / tn, ty / tn
        self.nx, self.ny = -ty, tx                      # left normal
        seg = np.hypot(np.roll(cx, -1) - cx, np.roll(cy, -1) - cy)
        self.ds_nom = float(seg.mean())
        self.half_w = float(min(wl.min(), wr.min()))

        # wall polylines (for expected laser distances)
        self.lax, self.lay = cx + self.nx * wl, cy + self.ny * wl
        self.rax, self.ray = cx - self.nx * wr, cy - self.ny * wr

        # racing line
        lo = -(wr - self.WALL_MARGIN)
        hi = wl - self.WALL_MARGIN
        self.d_race = self._optimize_line(cx, cy, self.nx, self.ny, lo, hi)
        self.px = cx + self.d_race * self.nx
        self.py = cy + self.d_race * self.ny
        pseg = np.hypot(np.roll(self.px, -1) - self.px, np.roll(self.py, -1) - self.py)
        self.pseg = pseg
        ps = np.concatenate(([0.0], np.cumsum(pseg)[:-1]))
        self.ps = ps
        self.ptot = float(pseg.sum())
        self.ps2 = np.concatenate((ps, ps + self.ptot))
        self.pk = self._curvature(self.px, self.py)

        # speed profiles for every grip guess
        self.vprof = [self._speed_profile(self.A_MAX0 * g) for g in self.GRIP_SCALES]

        # laser beam subset: front +-140 deg (set lazily on first scan)
        self._beam_n = None

    # ---- minimum curvature line by projected gradient descent ----
    def _optimize_line(self, cx, cy, nx, ny, lo, hi):
        d = np.zeros(len(cx))
        for h, iters, eta in ((40, 250, 0.1), (20, 250, 0.1), (10, 250, 0.1), (5, 200, 0.1), (2, 100, 0.1)):
            for _ in range(iters):
                px = cx + d * nx
                py = cy + d * ny
                d2x = np.roll(px, h) - 2 * px + np.roll(px, -h)
                d2y = np.roll(py, h) - 2 * py + np.roll(py, -h)
                gx = np.roll(d2x, -h) - 2 * d2x + np.roll(d2x, h)
                gy = np.roll(d2y, -h) - 2 * d2y + np.roll(d2y, h)
                g = gx * nx + gy * ny
                d = np.clip(d - eta * g, lo, hi)
        # light smoothing to remove clipping kinks
        k = np.ones(7) / 7.0
        d = np.convolve(np.concatenate((d[-3:], d, d[:3])), k, mode="valid")
        return np.clip(d, lo, hi)

    @staticmethod
    def _curvature(px, py):
        tx = np.roll(px, -1) - np.roll(px, 1)
        ty = np.roll(py, -1) - np.roll(py, 1)
        ds = np.hypot(tx, ty)
        tx, ty = tx / ds, ty / ds
        dtx = np.roll(tx, -1) - np.roll(tx, 1)
        dty = np.roll(ty, -1) - np.roll(ty, 1)
        return (tx * dty - ty * dtx) / ds

    def _speed_profile(self, a_max):
        n = self.n
        k = np.abs(self.pk) + 1e-6
        seg = self.pseg
        v = np.minimum(np.sqrt(self.LAT_FRAC * a_max / k), self.V_MAX)
        a2 = a_max * a_max
        # backward pass (braking), twice around for periodicity
        for _ in range(2):
            for i in range(n - 1, -1, -1):
                j = (i + 1) % n
                vn = v[j]
                alat = vn * vn * k[j]
                abr = math.sqrt(max(a2 - alat * alat, 0.0)) * self.BRK_FRAC
                vb = math.sqrt(vn * vn + 2.0 * abr * seg[i])
                if vb < v[i]:
                    v[i] = vb
        # forward pass (acceleration)
        for _ in range(2):
            for i in range(n):
                j = (i + 1) % n
                vc = v[i]
                alat = vc * vc * k[i]
                aac = min(self.A_ENGINE, math.sqrt(max(a2 - alat * alat, 0.0)))
                vf = math.sqrt(vc * vc + 2.0 * aac * seg[i])
                if vf < v[j]:
                    v[j] = vf
        return v

    # ============================ step ============================
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        try:
            if not self._ok:
                return self._reactive(speed, laser_scan)
            return self._drive(x, y, yaw, speed, steering_angle, laser_scan)
        except Exception as e:
            print("step error:", repr(e))
            return self._reactive(speed, laser_scan)

    def _reactive(self, speed, scan):
        """Follow-the-gap fallback. Never used when the track was built."""
        n = len(scan)
        k = np.concatenate((np.arange(0, 13), np.arange(n - 12, n)))
        d = np.where(np.isfinite(scan[k]), scan[k], 200.0)
        ang = np.where(k > n // 2, k - n, k) * (TWO_PI / n)
        best = int(np.argmax(d - 0.5 * np.abs(ang) * 10))
        steer = float(np.clip(ang[best] * 0.8, -0.5, 0.5))
        front = d[0]
        vt = float(np.clip(front / 1.2, 4.0, 18.0))
        return vt, steer

    def _drive(self, x, y, yaw, speed, steer_now, scan):
        self.frame += 1
        n = self.n

        # ---- crash detection (teleport or instant stop) ----
        if self.prev is not None:
            pxp, pyp, pyaw, pv = self.prev
            jump = math.hypot(x - pxp, y - pyp)
            if jump > 2.5 or (pv - speed) > 8.0:
                self._on_crash()
            else:
                self._update_wheelbase(yaw, pyaw, speed, steer_now)
        self.prev = (x, y, yaw, speed)

        # ---- localisation ----
        dcx = self.cx - x
        dcy = self.cy - y
        ic = int(np.argmin(dcx * dcx + dcy * dcy))
        e_car = -(dcx[ic] * self.nx[ic] + dcy[ic] * self.ny[ic])   # +left of centerline
        dlx = self.px - x
        dly = self.py - y
        il = int(np.argmin(dlx * dlx + dly * dly))

        # lap bookkeeping
        if self.last_ic is not None and ic < self.last_ic - n // 2:
            self._on_lap()
        self.last_ic = ic

        # ---- other cars from the laser ----
        v_limit = self.V_MAX
        delta_target = 0.0
        block_left = block_right = False
        try:
            delta_target, v_limit, block_left, block_right = self._obstacles(
                x, y, yaw, speed, scan, ic, e_car)
        except Exception as e:
            print("obstacle error:", repr(e))

        if self.frame < self.avoid_until:
            # keep the offset we had (we may still be beside the car we passed)
            if delta_target == 0.0:
                delta_target = self.delta
        else:
            self.avoid_side = 0
        step = self.DELTA_RATE * DT
        change = max(-step, min(step, delta_target - self.delta))
        if (change > 0 and block_left) or (change < 0 and block_right):
            change = 0.0
        self.delta += change

        # ---- pure pursuit on the (shifted) racing line ----
        ld = min(self.LD_BASE + self.LD_GAIN * max(speed, 0.0), self.LD_MAX)
        j = int(np.searchsorted(self.ps2, self.ps[il] + ld)) % n
        off = self.d_race[j] + self.delta
        off = max(-(self.wr[j] - self.PASS_MARGIN), min(self.wl[j] - self.PASS_MARGIN, off))
        tx = self.cx[j] + off * self.nx[j]
        ty = self.cy[j] + off * self.ny[j]
        dx, dy = tx - x, ty - y
        ld_act = max(math.hypot(dx, dy), 1.0)
        alpha = _wrap(math.atan2(dy, dx) - yaw)
        steer = math.atan2(2.0 * self.wb * math.sin(alpha), ld_act)
        steer = max(-0.5, min(0.5, steer))

        # ---- speed ----
        vprof = self.vprof[self.grip_idx]
        ahead = int(max(speed, 0.0) * self.SPEED_LOOKAHEAD_T / self.ds_nom) + 1
        ia = (il + ahead) % n
        vt = min(vprof[il], vprof[ia])
        # off the racing line (after a crash, or while passing): be a bit careful
        err = abs(e_car - (self.d_race[ic] + self.delta))
        if err > 1.0:
            vt *= max(0.6, 1.0 - 0.05 * (err - 1.0))
        # heading error: don't floor it while pointing at a wall
        herr = abs(alpha)
        if herr > 0.35:
            vt *= max(0.4, 1.0 - (herr - 0.35))
        vt = min(vt, v_limit)
        # last resort: something right in front (wall after a bad reset, car)
        d0 = scan[0]
        if np.isfinite(d0) and speed > 3.0 and d0 < speed * 0.5:
            vt = min(vt, d0 / 0.5)
        vt = max(0.0, min(self.V_MAX, vt))

        # stats
        if self.prev is not None:
            pass
        return float(vt), float(steer)

    # ---------------- obstacle handling ----------------
    def _beams(self, nb):
        if self._beam_n != nb:
            self._beam_n = nb
            span = int(round(nb * 140.0 / 360.0))
            ks = np.concatenate((np.arange(0, span + 1), np.arange(nb - span, nb)))
            self._ks = ks
            rel = np.where(ks > nb // 2, ks - nb, ks) * (TWO_PI / nb)   # signed angle, +left
            self._krel = rel
            self._left_side = (rel > math.radians(40)) & (rel < math.radians(140))
            self._right_side = (rel < -math.radians(40)) & (rel > -math.radians(140))
            self._front = np.abs(rel) <= math.radians(100)
            self._win = np.arange(-60, 340)
        return self._ks, self._krel

    def _obstacles(self, x, y, yaw, speed, scan, ic, e_car):
        n = self.n
        ks, rel = self._beams(len(scan))
        meas = scan[ks]
        fin = np.isfinite(meas) & (meas < 60.0)
        if not fin.any():
            return 0.0, self.V_MAX, False, False
        ang = yaw + rel
        dxr, dyr = np.cos(ang), np.sin(ang)
        idx = (ic + self._win) % n

        # expected distance to the walls along each beam
        ax = np.concatenate((self.lax[idx], self.rax[idx]))
        ay = np.concatenate((self.lay[idx], self.ray[idx]))
        bx = np.concatenate((self.lax[(idx + 1) % n], self.rax[(idx + 1) % n]))
        by = np.concatenate((self.lay[(idx + 1) % n], self.ray[(idx + 1) % n]))
        ex, ey = bx - ax, by - ay
        amx, amy = ax - x, ay - y
        denom = dxr[:, None] * ey[None, :] - dyr[:, None] * ex[None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (amx * ey - amy * ex)[None, :] / denom
            u = (amx[None, :] * dyr[:, None] - amy[None, :] * dxr[:, None]) / denom
        valid = (np.abs(denom) > 1e-9) & (t > 0) & (u >= 0.0) & (u <= 1.0)
        t = np.where(valid, t, np.inf)
        expected = t.min(axis=1)

        car = fin & (meas < expected - self.CAR_DETECT_TOL)
        if not car.any():
            return 0.0, self.V_MAX, False, False

        block_left = bool((car & self._left_side & (meas < 4.0)).any())
        block_right = bool((car & self._right_side & (meas < 4.0)).any())
        if (car & (meas < 8.0)).any():
            self.near_car = self.frame

        # project the car hits into the track frame
        sel = car & self._front
        if not sel.any():
            return 0.0, self.V_MAX, block_left, block_right
        hx = x + meas[sel] * dxr[sel]
        hy = y + meas[sel] * dyr[sel]
        wcx, wcy = self.cx[idx], self.cy[idx]
        d2 = (hx[:, None] - wcx[None, :]) ** 2 + (hy[:, None] - wcy[None, :]) ** 2
        wi = np.argmin(d2, axis=1)
        gidx = idx[wi]
        s_rel = (wi - 60) * self.ds_nom
        e_obs = (hx - self.cx[gidx]) * self.nx[gidx] + (hy - self.cy[gidx]) * self.ny[gidx]

        horizon = min(60.0, max(15.0, 2.0 * speed + 10.0))
        ahead = (s_rel > 0.5) & (s_rel < horizon)
        if not ahead.any():
            return 0.0, self.V_MAX, block_left, block_right
        plan = self.d_race[gidx] + self.delta
        gap = e_obs - plan
        conflict = ahead & (np.abs(gap) < self.W_CLEAR)
        if not conflict.any():
            return 0.0, self.V_MAX, block_left, block_right

        s_near = s_rel[conflict].min()
        cluster = ahead & (np.abs(s_rel - s_near) < 5.0)
        emin, emax = e_obs[cluster].min(), e_obs[cluster].max()
        i_near = gidx[conflict][int(np.argmin(s_rel[conflict]))]
        lo = -(self.wr[i_near] - self.PASS_MARGIN)
        hi = self.wl[i_near] - self.PASS_MARGIN
        off_l = emax + self.W_CLEAR
        off_r = emin - self.W_CLEAR
        ok_l = off_l <= hi
        ok_r = off_r >= lo
        race = self.d_race[i_near]
        side = 0
        if ok_l and ok_r:
            if self.avoid_side != 0 and self.frame < self.avoid_until:
                side = self.avoid_side
            else:
                side = 1 if abs(off_l - race) <= abs(off_r - race) else -1
        elif ok_l:
            side = 1
        elif ok_r:
            side = -1

        delta_target = 0.0
        if side != 0:
            self.avoid_side = side
            self.avoid_until = self.frame + int(self.AVOID_HOLD / DT)
            delta_target = (off_l if side > 0 else off_r) - race

        # speed: time gap until we are laterally clear of the obstacle
        v_limit = self.V_MAX
        lat_clear = (e_car > emax + self.W_CLEAR - 0.6) or (e_car < emin - self.W_CLEAR + 0.6)
        if not lat_clear:
            v_limit = max(0.0, s_near - self.GAP_MIN) / self.T_GAP
            if side != 0:
                v_limit = max(v_limit, 4.0)
        return delta_target, v_limit, block_left, block_right

    # ---------------- adaptation ----------------
    def _update_wheelbase(self, yaw, pyaw, speed, steer):
        yr = _wrap(yaw - pyaw) / DT
        if speed > 4.0 and abs(steer) > 0.04 and abs(yr) > 0.05:
            alat = abs(speed * yr)
            self.max_alat = max(self.max_alat, alat)
            if alat < 0.6 * self.A_MAX0 * self.GRIP_SCALES[self.grip_idx]:
                wb = speed * math.tan(steer) / yr
                if 0.5 < wb < 10.0:
                    self.wb_samples.append(wb)
                    if len(self.wb_samples) > 60:
                        self.wb_samples.pop(0)
                    if len(self.wb_samples) >= 15:
                        self.wb = float(np.median(self.wb_samples))

    def _on_crash(self):
        self.crashes += 1
        self.lap_crash += 1
        self.clean_laps = 0
        car_contact = (self.frame - self.near_car) < 8
        if not car_contact and self.grip_idx < len(self.GRIP_SCALES) - 1:
            self.grip_idx += 1
        self.delta = 0.0
        self.avoid_side = 0
        self.prev = None
        print(f"crash #{self.crashes} ({'car' if car_contact else 'wall'}) "
              f"grip->{self.GRIP_SCALES[self.grip_idx]:.2f} wb={self.wb:.2f}")

    def _on_lap(self):
        t = (self.frame - self.lap_start) / 40.0
        self.lap += 1
        if self.lap_crash == 0:
            self.clean_laps += 1
            if self.clean_laps >= self.GRIP_RECOVER_LAPS and self.grip_idx > 0:
                self.grip_idx -= 1
                self.clean_laps = 0
        print(f"lap {self.lap} {t:.1f}s crashes={self.crashes} grip={self.GRIP_SCALES[self.grip_idx]:.2f} "
              f"wb={self.wb:.2f} max_alat={self.max_alat:.1f}")
        self.lap_start = self.frame
        self.lap_crash = 0
