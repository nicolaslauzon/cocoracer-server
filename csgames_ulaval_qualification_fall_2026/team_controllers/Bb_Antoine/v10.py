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
        points = laser_scan
        distance_from_left_wall = points[17] if np.isfinite(laser_scan[17]) else 10000
        print(distance_from_left_wall)
        distance_from_right_wall = points[53] if np.isfinite(laser_scan[53]) else 10000
        distance_from_front_wall = points[0] if np.isfinite(laser_scan[0]) else 10000
        distance_from_front_left_wall = points[8] if np.isfinite(laser_scan[8]) else 10000
        distance_from_front_right_wall = points[61] if np.isfinite(laser_scan[61]) else 10000
        distance_from_centerline = distance_from_right_wall - 10
        steering_angle = 0.0
        car_speed = 5.0 ## + speed
        if distance_from_front_wall < 3.0:
            if distance_from_front_left_wall > distance_from_front_right_wall:
                steering_angle = self.right_turn_angle
            else:
                steering_angle = self.left_turn_angle if steering_angle != 0 else 0
        return 20.0, self.to_radian(steering_angle)

    def is_near_left(self, distance_from_left):
        print(distance_from_left)
        return distance_from_left < 1.0

    def is_near_right(self, distance_from_right):
        print(distance_from_right)
        return distance_from_right > 1.0
        

    def to_radian(self, degree):
        return math.pi / 180 * degree
    
    def is_near_left(self, distance_from_left):
        print(distance_from_left)
        return distance_from_left < 1.0

    def is_near_right(self, distance_from_right):
        print(distance_from_right)
        return distance_from_right > 1.0

    def test(self, distance_ahead):
        
        
        return