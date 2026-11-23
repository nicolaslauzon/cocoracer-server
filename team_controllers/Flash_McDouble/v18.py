import numpy as np
from random import randint;
import math
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


        print(f"X:{x}\ny:{y}\nyaw:{yaw}\nspeed:{speed}\nsteering_angle:{steering_angle}\nlaser_scan:{laser_scan}\n")
        return 15, self.get_nextStep_tangeant(x,y,laser_scan, yaw)




    def get_nextStep(self, x: float, y: float, laser_scan, yaw):
        """cette fonction retourne des trucs"""
        left_side = laser_scan[0:len(laser_scan)//4]
        right_side = laser_scan[len(laser_scan) // 4::-1]

        TOURNAGE = 0.5

        if laser_scan[0] > 2:
            return 0
        if np.mean(left_side) > np.mean(right_side):
            return -TOURNAGE
        else:
            return TOURNAGE

    def get_nextStep_tangeant(self, x: float, y: float, laser_scan, yaw):
        """cette fonction retourne des trucs"""
        left_side = laser_scan[len(laser_scan) // 4]
        right_side = laser_scan[len(laser_scan) * 0.75]

        TOURNAGE = 0.2

        if laser_scan[0] > 20:
            return 0
        if left_side > right_side:
            return TOURNAGE
        else:
            return -TOURNAGE




