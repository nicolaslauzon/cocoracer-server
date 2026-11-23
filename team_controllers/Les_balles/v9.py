import numpy as np

from cocoracer import Controller, TrackInfo, CenterlinePoint


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
        speed = 30.0
        if (laser_scan[1] > laser_scan[0]):
            return 30.0, 0.0
        elif (laser_scan[0] < laser_scan[1]):
            steering_angle = yaw + 25.0
            speed = speed - 1
        
        return speed, 0.0

        
