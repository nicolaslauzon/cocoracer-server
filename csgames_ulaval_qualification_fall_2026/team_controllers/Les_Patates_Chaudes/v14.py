import math

import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

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
        """Return (target_speed, target_steering_angle in radians). See cocoracer.py and wtf.md."""

        max_laser_index = 0

        for i in range(len(laser_scan[-32:32])):
            laser = laser_scan[i]
            if laser > laser_scan[max_laser_index]:
                max_laser_index = i


        heading = math.radians(laser_scan[max_laser_index] * 5)

        # avant = laser_scan[0]

        # avant_gauche = laser_scan[7]

        # avant_droite = laser_scan[63]

        # turn_angle = 0

        # if avant < 20:
        #     if avant_gauche < avant_droite:
        #         turn_angle = -0.2
        #     else:
        #         turn_angle = 0.2

        # if laser_scan[17] > 10:
        #     turn_angle = 0.01
        # elif laser_scan[17] < 10:
        #     turn_angle = 0.01

        return 20.0, heading
