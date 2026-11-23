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
        return self.getSpeed(laser_scan, steering_angle), self.get_nextStep_tangeant(x,y,laser_scan, yaw)




    def getSpeed(self, laser_scan, steering_angle: float) -> float:
        """ 8=========D bite """
        TOLERANCE = np.deg2rad(5)
        if abs(steering_angle) < TOLERANCE and laser_scan[0] > 70:
            return 30

        if laser_scan[0] < 30:
            return 14
        else:
            return 20

    def get_nextStep_tangeant(self, x: float, y: float, laser_scan, yaw):
        """cette fonction retourne des trucs"""
        left_side = laser_scan[len(laser_scan) // 6]
        right_side = laser_scan[(int(len(laser_scan) * 0.75))]
        TOURNAGE = 0.7


        # tournage d'urgence
        if laser_scan[len(laser_scan) // 4] < 2:
            return -0.2
        if laser_scan[int(len(laser_scan) * 0.75)] < 2:
            return 0.2

        # pas de probleme
        if laser_scan[0] > 30:
            return 0



        # tournée normal
        if left_side > right_side:
            return TOURNAGE
        else:
            return -TOURNAGE


    def get_nextStep_tangeant_backup(self, x: float, y: float, laser_scan, yaw):
        """cette fonction retourne des trucs"""
        left_side = laser_scan[len(laser_scan) // 7]
        right_side = laser_scan[(int(len(laser_scan) * 0.85))]
        TOURNAGE = 0.7

        if np.mean(laser_scan[0:len(laser_scan) // 4]) < 2:
            return -0.2
        if np.mean(laser_scan[int(len(laser_scan) * 0.75)]) < 2:
            return 0.2

        if laser_scan[0] > 30:
            return 0


        if left_side > right_side:
            return TOURNAGE
        else:
            return -TOURNAGE