import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""
        self.initialLaserPosLeft = []
        self.initialLaserPosRight = []

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
        speedToGo = 19.0
        print(laser_scan[0], laser_scan[18], laser_scan[54])

        if laser_scan[18] < 7.0 and laser_scan[18] > 0.5:
            steering = np.deg2rad(-65)
        elif laser_scan[54] < 7.0 and laser_scan[54] > 0.5:
            steering = np.deg2rad(65)
        elif laser_scan[0] < 0.5:
            speedToGo = 10.0
        elif laser_scan[9] > 50.0:
            steering = np.deg2rad(85)
            speedToGo = 12.0
        elif laser_scan[63] > 50.0:
            steering = np.deg2rad(-85)
            speedToGo = 12.0
        elif laser_scan[18] and laser_scan[54] < 10.0:
            speedToGo = 17.0
        return speedToGo, steering
