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
        """Return (target_speed, target_steering_angle). See cocoracer.py and wtf.md."""

        dist_g = laser_scan[10]
        dist_avant = laser_scan[0]
        dist_d = laser_scan[-10]

        if abs(dist_g - dist_d) < 4 and speed > 20 and dist_avant < 30:
            return 15, 0
        if abs(dist_g - dist_d) < 4 and dist_avant > 40 :
            return 30, 0
        elif dist_g < dist_d:
            return 15, -0.5
        else:
            return 15, 0.5
