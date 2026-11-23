import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
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

        i = int(len(laser_scan) / 4)

        steering_angle = 0
        if laser_scan[i] > laser_scan[3 * i]:
            steering_angle = (laser_scan[i] - 10) / 20 * 0.5
        else:
            steering_angle = -(laser_scan[i] - 10) / 20 * 0.5
        
        return 10, steering_angle
