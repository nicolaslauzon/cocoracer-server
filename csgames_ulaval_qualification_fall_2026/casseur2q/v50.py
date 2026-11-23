import numpy as np
import math

from cocoracer import Controller, TrackInfo

LOOKAHEAD = 50

STRAIGHT_TRESHOLD = 0.2

MAX_SPEED = 22.0

MIN_SPEED = 3


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
        target_x= self.trackInfo.centerline[(delta_i + LOOKAHEAD) % len(self.trackInfo.centerline)].x
        target_y= self.trackInfo.centerline[(delta_i + LOOKAHEAD) % len(self.trackInfo.centerline)].y
        angle= math.atan2(target_y - y, target_x - x)
        steering_angle = angle - yaw
        steering_angle %= 2 * math.pi
        if steering_angle > math.pi:
            steering_angle -= 2 * math.pi
        print(angle, steering_angle, target_x, target_y)
        if math.fabs(steering_angle) < STRAIGHT_TRESHOLD:
            steering_angle = 0

        """Return (target_speed, target_steering_angle). See cocoracer.py and wtf.md."""
        angle_damping = max(MIN_SPEED, MAX_SPEED * (1 - (math.fabs(steering_angle) / 0.5)))
        if (laser_scan[0] < 10):
            if laser_scan[2] > 15:
                steering_angle += 0.17
            if laser_scan[-2] > 15:
                steering_angle -= 0.17
        straight_damping = angle_damping * (max(laser_scan[0], 40) / 40)
        return straight_damping, steering_angle
