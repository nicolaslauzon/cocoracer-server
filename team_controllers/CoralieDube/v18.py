import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""

    def angle_to_distance(
        self,
        theta_rad: float,
        lidar_array: list[float],
        angle_min: float,
        angle_increment: float,
    ) -> float:
        index = int((theta_rad - angle_min) / angle_increment)
        distance = lidar_array[index]

        return distance

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

        lidar_range_array = laser_scan  # type: ignore
        angle_min = -np.pi
        angle_increment = 2 * np.pi / len(laser_scan)

        # 1. TODO: Récupérer les distances LiDAR à deux angles de détection du mur
        theta = np.radians(60)
        theta_b = -np.pi / 2.0
        theta_a = theta_b + theta

        # 2. TODO: Calculer la distance effective au mur et l'erreur relative à la distance désirée
        a = self.angle_to_distance(theta_a, lidar_range_array, angle_min, angle_increment)
        b = self.angle_to_distance(theta_b, lidar_range_array, angle_min, angle_increment)

        # 3. TODO: Calcul de PID
        div = a * np.sin(theta)
        alpha = np.atan((a * np.cos(theta) - b) / div if div != 0 else 0.1)
        D_t = b * np.cos(alpha)
        D_tp1 = D_t + 0.6 * np.sin(alpha)

        error = 0.8 - D_tp1

        steering = error * 1.8

        # 5. TODO: Ajuster la vitesse en fonction de l'angle de braquage (steering) pour éviter les collisions
        min_throttle = 20
        max_throttle = 40

        throttle = max_throttle * np.exp(-2 * abs(steering))
        throttle = max(throttle, min_throttle)
        
        return speed, steering_angle