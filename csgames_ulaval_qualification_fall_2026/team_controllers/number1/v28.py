import numpy as np

from cocoracer import Controller, TrackInfo

ANGLE_INCREMENT = np.deg2rad(5)

class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    last_steering = 0

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""

    def calcule_distance(self, theta_rad, laser_scan):
        # https://f1tenth-coursekit.readthedocs.io/en/latest/assignments/labs/lab3.html
        index = int(theta_rad / ANGLE_INCREMENT)
        return laser_scan[index] if 0 <= index < len(laser_scan) else math.inf

    def valid_lidar(self, distance):
        return not np.isinf(distance) and not np.isnan(distance)

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

        theta = 60
        distance_du_mur = 10
        dt = 1 / 40

        angle_1 = np.deg2rad(30)
        distance_1 = self.calcule_distance(angle_1, laser_scan)
        if not self.valid_lidar(distance_1):
            return
        print("dist1:", distance_1)

        angle_2 = np.deg2rad(30 + theta)
        distance_2 = self.calcule_distance(angle_2, laser_scan)
        if not self.valid_lidar(distance_2):
            return
        print("dist2:", distance_2)

        # https://f1tenth-coursekit.readthedocs.io/en/latest/assignments/labs/lab3.html
        alpha = np.atan2(
            distance_1 * np.cos(np.deg2rad(theta)) - distance_2,
            distance_1 * np.sin(np.deg2rad(theta))
        )
        distance_effective = distance_2 * np.cos(alpha)
        erreur_relative = distance_du_mur - distance_effective

        if (erreur_relative > 4): erreur_relative = 4

        print("dist eff", distance_effective)

        KP = 0.3
        KD = 0.05

        terme_p = KP * erreur_relative

        de = erreur_relative + self.last_steering

        terme_d = KD * de / dt

        target_steering_angle = terme_p + terme_d

        if abs(target_steering_angle) > 0.5:
            target_speed = 4
        elif abs(target_steering_angle) > 0.3:
            target_speed = 8
        else:
            target_speed = 10

        print("Input speed: ", speed)

        print("Input steering", steering_angle)

        print("Result speed: ", target_speed)
              
        print("Result steering", -target_steering_angle)

        self.last_steering = -target_steering_angle

        return target_speed, -target_steering_angle
