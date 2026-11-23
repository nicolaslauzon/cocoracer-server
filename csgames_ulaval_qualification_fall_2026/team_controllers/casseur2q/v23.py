import numpy as np
import math

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        self.trackInfo = track_info
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
        best_delta = 9999999999999
        delta_i = -1
        for i, line in enumerate(self.trackInfo.centerline):
            dx = line.x - x
            dy = line.y - y
            delta = dx**2 + dy**2
            if delta < best_delta:
                delta_i = i
                best_delta = delta
        target_x= self.trackInfo.centerline[(delta_i + 5) % len(self.trackInfo.centerline)].x
        target_y= self.trackInfo.centerline[(delta_i + 5) % len(self.trackInfo.centerline)].y
        angle= math.atan2(y - target_y, x - target_x)
        steering_angle = angle - yaw
        steering_angle %= 2 * math.pi
        if steering_angle > math.pi:
            steering_angle -= 2 * math.pi
        print(angle, steering_angle, target_x, target_y)
        if math.fabs(steering_angle) < 0.2:
            steering_angle = 0

        """Return (target_speed, target_steering_angle). See cocoracer.py and wtf.md."""
        return max(10, 20.0 * (1 - (math.fabs(steering_angle) / 0.5))), steering_angle
