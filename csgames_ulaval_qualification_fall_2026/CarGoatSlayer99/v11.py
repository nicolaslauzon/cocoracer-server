import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""
        self.initialLaserPos = []

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
        steering = 0.0
        speedToGo = 25.0
        print(laser_scan[9], laser_scan[54])
        if laser_scan[9] > 40.0:
            steering = np.deg2rad(90)
            speedToGo = 12.0
        elif laser_scan[63] > 40.0:
            steering = np.deg2rad(-90)
            speedToGo = 12.0
        return speedToGo, steering
