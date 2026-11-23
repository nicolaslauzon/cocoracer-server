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
        print(laser_scan)
        spd = 30
        angle = 0
        #calculer angle
        vision_droite = laser_scan[3]
        vision_gauche = laser_scan[70]

        if np.isfinite(vision_gauche):
            if vision_gauche < 5:
                angle = 15
        if np.isfinite(vision_droite):
            if vision_droite < 5:
                angle = -15

        print(vision_gauche, vision_droite)

        self.tick += 1
        return spd, angle
