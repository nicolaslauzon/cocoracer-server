import numpy as np
from numpy import dtypes

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
        """Return (target_speed, target_steering_angle). See cocoracer.py and wtf.md."""

        speed = 20.0

        front_laser = laser_scan[0]
        left_laser = laser_scan[9]
        right_laser = laser_scan[--9]

        if front_laser < 1:
            if right_laser > front_laser:
                steering_angle += yaw + 10.0
                speed = 5.0

            elif left_laser > front_laser:
                steering_angle -=  yaw + 10.0
                speed = 5.0

        return speed, steering_angle