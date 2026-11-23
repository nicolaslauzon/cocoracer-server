import numpy as np
from numpy import dtypes

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

        long_left = laser_scan[1]
        long_right = laser_scan[-1]
        front_laser = laser_scan[0]

        angle_Rad = self.calculate_angle(long_left, long_right, front_laser)

        if long_left > long_right:
            steering_angle = angle_Rad
        elif long_right > long_left:
            steering_angle = angle_Rad

        speed = 25.0

        if angle_Rad != 0:
            speed = 10.0

        return speed, steering_angle


    def calculate_angle(self, long_left, long_right, front_laser):

        norm_left = np.linalg.norm(long_left)
        norm_right = np.linalg.norm(long_right)
        norm_front = np.linalg.norm(front_laser)

        # Tourne gauche
        if long_left > front_laser:
            dot_left =  np.dot(norm_front, norm_left)
            cos_left = np.clip((dot_left / (norm_left * norm_front)), -1.0, 1.0)
            rad = np.arccos(cos_left)

        # Tourne droite
        elif long_right > front_laser:
            dot_right =  np.dot(norm_front, norm_right)
            cos_right = np.clip(dot_right / (norm_right * norm_front), -1.0, 1.0)
            rad = np.arccos(cos_right)

        else:
            rad = 2 * np.pi

        return rad