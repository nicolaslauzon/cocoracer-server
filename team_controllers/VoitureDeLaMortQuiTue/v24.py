import numpy as np
import math

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    tick = 0

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""

    def step(
        self,
        x: float,
        y: float,
        yaw: float,
        speed: float,
        steering_angle: float,
        laser_scan: np.ndarray,
    ) -> tuple[float, float]:
        """Return (target_speed, target_steering_angle). See cocoracer.py and wtf.md."""
        MAX_SPD = 18
        LOW_SPD = 10
        spd = LOW_SPD
        angle = 0
        #calculer angle
        vision_droite = laser_scan[4]
        vision_gauche = laser_scan[69]
        vision_front = laser_scan[0]
        vision_front_RAGE_1 = laser_scan[1]
        vision_front_RAGE_2 = laser_scan[2]
        vision_front_RAGE_01 = laser_scan[71]
        vision_front_RAGE_02 = laser_scan[70]

        if np.isfinite(vision_gauche):
            if vision_gauche < 25:
                angle = 0.5
  
        if np.isfinite(vision_droite):
            if vision_droite < 25:
                angle = -0.5

        if np.isfinite(vision_front) and np.isfinite(vision_front_RAGE_1) and np.isfinite(vision_front_RAGE_2) and np.isfinite(vision_front_RAGE_01) and np.isfinite(vision_front_RAGE_02):
            if max(vision_front, vision_front_RAGE_1, vision_front_RAGE_2, vision_front_RAGE_01, vision_front_RAGE_02) < 22:
                spd = LOW_SPD
            else:
                spd = MAX_SPD
        else:
            spd = MAX_SPD

        self.tick += 1
        return spd, angle
