import numpy
import numpy as np
import scipy
from cocoracer import Controller, TrackInfo

class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""
    def reset(self, track_info: TrackInfo) -> None:
        self.trackInfo = track_info
        self.right = 1
        self.old = 10
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

        # SAFE
        # if abs(laser_scan[10] - laser_scan[-10]) < 3 and laser_scan[0] > 40:
        #     print("full speed")
        #     return laser_scan[0]/2, 0
        # elif abs(laser_scan[10] - laser_scan[-10]) < 3:
        #     print("tout droit")
        #     return laser_scan[0]/2.5, 0
        # if laser_scan[10] < laser_scan[-10]:
        #     print("droite")
        #     return 15, (-0.5 if speed < 18 else -0.5/(speed/4))
        # else :
        #     print("gauche")
        #     return 15, (0.5 if speed < 18 else 0.5/(speed/4))

        
        if abs(laser_scan[10] - laser_scan[-10]) < 3 and laser_scan[0] > 40:
            print("full speed")
            return laser_scan[0]/2, 0
        elif abs(laser_scan[10] - laser_scan[-10]) < 3:
            print("tout droit")
            return laser_scan[0]/2.5, 0
        if laser_scan[10] < laser_scan[-10]:
            print("droite")
            return 15, (-0.5 if speed < 18 else -0.5/(speed/4))
        else :
            print("gauche")
            return 15, (0.5 if speed < 18 else 0.5/(speed/4))
