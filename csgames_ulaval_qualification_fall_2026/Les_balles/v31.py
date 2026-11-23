import numpy as np

from cocoracer import Controller, TrackInfo, CenterlinePoint


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
        sorted_laser_scan = np.sort(laser_scan)
        longest = sorted_laser_scan[-1]
        front = laser_scan[0]
        speed = 25.0

        left_laser = laser_scan[1]
        right_laser = laser_scan[-1]

        steer = self.angle(front, longest)

        if (left_laser > front):
            steering_angle += yaw + steer
            self.slow(speed)
        elif (right_laser > front):
            steering_angle -= yaw + steer
            self.slow(speed)
        
        return speed, steering_angle
        
    
    def slow(slef, speed):
        speed -= 2.0

    def accelerate(self, speed):
        speed += 2.0

    def steering(self, front, longest):
        norm_front = np.linalg.norm(front)
        norm_longest = np.linalg.norm(longest)

        dot = np.dot(front, longest)
        mag = norm_front * norm_longest

        cos = np.clip(dot/mag, -1.0, 1.0)
        rad = np.arccos(cos)
        deg = np.degrees(rad)

        return deg

    def angle(self, front, longest):
        angle = 0
        if (front < longest):
            angle = self.steering(front, longest)
            front = longest
            
        return angle
        
