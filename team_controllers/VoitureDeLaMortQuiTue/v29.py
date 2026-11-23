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
        MAX_SPD = 20
        LOW_SPD = 10
        spd = LOW_SPD
        angle = 0
        #calculer angle
        vision_droite = laser_scan[4]
        vision_droite_min = laser_scan[3]
        vision_droite_max = laser_scan[5]
        vision_gauche = laser_scan[69]
        vision_gauche_min = laser_scan[70]
        vision_gauche_max = laser_scan[68]
        vision_front = laser_scan[0]
        vision_front_RAGE_1 = laser_scan[1]
        vision_front_RAGE_2 = laser_scan[2]
        vision_front_RAGE_01 = laser_scan[71]
        vision_front_RAGE_02 = laser_scan[70]

        if np.isfinite(vision_gauche) and np.isfinite(vision_gauche_min) and np.isfinite(vision_gauche_max):
            if max(vision_gauche, vision_gauche_min, vision_gauche_max) < 25:
                angle = 0.5
  
        if np.isfinite(vision_droite) and np.isfinite(vision_droite_min) and np.isfinite(vision_droite_max):
            if max(vision_droite, vision_droite_min, vision_droite_max) < 25:
                angle = -0.5

        if np.isfinite(vision_front) and np.isfinite(vision_front_RAGE_1) and np.isfinite(vision_front_RAGE_01):
            if max(vision_front, vision_front_RAGE_1, vision_front_RAGE_01) < 26:
                spd = LOW_SPD
            elif math.abs(angle) < 0.2:
                spd = MAX_SPD
        else:
            spd = MAX_SPD

        self.tick += 1
        return spd, angle
