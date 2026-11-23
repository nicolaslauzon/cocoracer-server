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


        print("LAMINE")


        front = sum(laser_scan[0:3] + laser_scan[-2:])/ 5

        left = sum(laser_scan[3:8]) / 5

        right = sum(laser_scan[-7:-2]) / 5

        #front = laser_scan[0]
        #left = laser_scan[5]
        #right = laser_scan[-5]

        turn = 0

        if left < right:
            turn -= 1 * (left * 10 /right)
        else:
            turn += 1 * (right * 10 /left)


        speed = 15 * (1 - 1 / front)
        if front <= 6:
            turn += 2


        turn = turn * 3.1415/180

        print("Turning deg:", turn)
        if 1 < left / right < 1.05:
            speed += 7

        print("Turning rad:", turn)
        print("Speed:", speed)
        print("Front:", front)
        print("Left:", left)
        print("Right:", right)




        return speed, turn


