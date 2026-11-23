import numpy as np
import math
from cocoracer import Controller, TrackInfo

class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""
    NUMBER_RAYS = 72
    left_turn_angle = 5.0
    right_turn_angle = -5.0

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
        distance_from_left_wall = laser_scan[17]
        distance_from_right_wall = laser_scan[53]
        distance_from_front_wall = laser_scan[0]
        distance_from_front_left_wall = laser_scan[8]
        distance_from_front_right_wall = laser_scan[61]
        steering_angle = 0.0
        
        if distance_from_front_wall < 1.5:
            speed = 0.5
            if max(distance_from_left_wall, distance_from_front_left_wall) > (distance_from_right_wall, distance_from_front_right_wall):
                steering_angle = self.left_turn_angle
            else:
                steering_angle = self.right_turn_angle
    
        closestObjectAhead = laser_scan[0]

        return 20.0, steering_angle