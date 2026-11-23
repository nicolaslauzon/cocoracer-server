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
            return 10
        else:
            return 20

    def get_nextStep_tangeant(self, x: float, y: float, laser_scan, yaw):
        """cette fonction retourne des trucs"""
        left_side = laser_scan[1:len(laser_scan) // 7]
        right_side = laser_scan[(int(len(laser_scan) * 0.85)):-1]

        #left_side, right_side = self.fonction_penis(laser_scan)

        left_side, right_side = self.sanitaze_view(left_side),self.sanitaze_view(right_side)

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

    def fonction_penis(self, laser) -> tuple[float, float]:
        """ 8==============D """
        left_laser = laser[1:len(laser) // 7]
        left_verif = [elt for elt in left_laser if not np.isinf(elt)]

        right_laser = laser[int(len(laser) * 6 / 7):-1]
        right_verif = [elt for elt in right_laser if not np.isinf(elt)]

        left_side = np.mean(left_verif)
        right_side = np.mean(right_verif)

        return left_side, right_side



    def sanitaze_view(self, left_view):
        tolerance = 5
        moyenne = np.mean(left_view)
        return np.mean(np.array(list(filter(lambda e: abs(abs(e)-moyenne) < tolerance, left_view))))


    def get_nextStep_tangeant_backup(self, x: float, y: float, laser_scan, yaw):
        """cette fonction retourne des trucs"""
        left_side = laser_scan[len(laser_scan) // 7]
        right_side = laser_scan[(int(len(laser_scan) * 0.85))]
        TOURNAGE = 0.7
        TOLERANCE = 15

        meang, meand = self.fonction_penis(laser_scan)
        meanTotal = (meang + meand) / 2

        if np.mean(laser_scan[0:len(laser_scan) // 4]) < 2:
            return -0.2
        if np.mean(laser_scan[int(len(laser_scan) * 0.75)]) < 2:
            return 0.2

        if laser_scan[0] > 30:
            return 0

        #if abs(meanTotal - laser_scan[0]) < TOLERANCE:
           # if left_side > right_side:
    ##           return TOURNAGE
        #    else:
         #       return -TOURNAGE