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
        speedToGo = 25.0
        print(laser_scan[0], laser_scan[18], laser_scan[54])
        
        if len(self.initialLaserPosLeft) == 0:
            for x in range(3, 9):
                self.initialLaserPosLeft.append(laser_scan[x])
        
        if len(self.initialLaserPosRight) == 0:
            for x in range(69, 63, -1):
                self.initialLaserPosRight.append(laser_scan[x])
        
        if laser_scan[18] < 7.0:
            steering = np.deg2rad(-75)
        elif laser_scan[54] < 7.0:
            steering = np.deg2rad(75)
        elif laser_scan[9] > 40.0:
            steering = np.deg2rad(90)
            speedToGo = 12.0
        elif laser_scan[63] > 40.0:
            steering = np.deg2rad(-90)
            speedToGo = 12.0
        elif laser_scan[18] and laser_scan[54] < 10.0:
            speedToGo = 17.0
        return speedToGo, steering
