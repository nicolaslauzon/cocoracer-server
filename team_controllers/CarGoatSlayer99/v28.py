import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""
        self.curPoint = 0
        self.centerLine = track_info.centerline
        self.nextPointPos = self.centerLine[15]
        self.LookAheadModifier = 1.5

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
        length = 0.0
        
        lookAhead = np.clip(self.LookAheadModifier * speedToGo, 5, 25)
        for point in self.centerLine:
            length = np.array((x, y)) - np.array((point.x, point.y))
            if 24.8 <= np.dot(length.T, length) <= 25.2:
                self.nextPointPos = point
                print(self.nextPointPos)
                break
        steering = np.arctan((2 * np.sin(np.arctan2(self.nextPointPos.x, self.nextPointPos.y)))/length)
        return speedToGo, steering
