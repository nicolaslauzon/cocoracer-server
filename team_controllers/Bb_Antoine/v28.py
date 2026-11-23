import numpy as np
import math as m
from cocoracer import Controller, TrackInfo

class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""
    left_turn_angle = 10.0
    right_turn_angle = -10.0
    previous_speed = 5

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""
        self.previous_speed = 5.

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
        points = laser_scan
        distance_from_left_wall = points[15] if np.isfinite(laser_scan[15]) else 10000
        distance_from_right_wall = points[53] if np.isfinite(laser_scan[53]) else 10000
        distance_from_front_wall = points[0] if np.isfinite(laser_scan[0]) else 10000
        distance_from_front_left_wall = points[6] if np.isfinite(laser_scan[8]) else 10000
        distance_from_front_right_wall = points[61] if np.isfinite(laser_scan[61]) else 10000
        distance_from_centerline = distance_from_right_wall - 10

        self.speed = speed * 1.01
        if self.is_near_left(distance_from_left_wall):
            print("Leaving left")
            alpha = self.calculate_alpha(distance_from_front_left_wall, distance_from_left_wall, 45)
            steering_angle = alpha * self.right_turn_angle
            self.speed = speed / 0.8
            print(alpha)
        else:
            print("Reaching Left")
            alpha = self.calculate_alpha(distance_from_front_left_wall, distance_from_left_wall, 45)
            steering_angle = alpha * self.left_turn_angle
            self.speed = speed / 0.8
            print(alpha)

        if speed == 0.:
            speed = self.previous_speed
        return 12, self.to_radian(steering_angle)

    def is_near_left(self, distance_from_left):
        print(distance_from_left)
        return distance_from_left < 5.0

    def to_radian(self, degree):
        return m.pi / 180 * degree

    def calculate_alpha(self, a, b, theta):
        return m.atan((a * m.cos(theta) - b) / a * m.sin(theta)) 