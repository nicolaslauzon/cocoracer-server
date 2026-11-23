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


        # speed = max(laser_scan[0]/2,20)
        # if laser_scan[10] < laser_scan[-10]:
        #     return speed, -0.5 * speed/10
        # else :
        #     return speed, 0.5 * speed/10

        # DRIFT
        # if abs(laser_scan[10] - laser_scan[-10]) < 3 and laser_scan[0] > 40:
        #     return laser_scan[0]/2,0
        # if laser_scan[10] < laser_scan[-10]:
        #     return 13, -0.5
        # else :
        #     return 13, 0.5

        # SAFE
        # if abs(laser_scan[10] - laser_scan[-10]) < 3 and laser_scan[0] > 40:
        #     return laser_scan[0]/2,0
        # elif abs(laser_scan[10] - laser_scan[-10]) < 3:
        #     return laser_scan[0]/3,0
        # if laser_scan[10] < laser_scan[-10]:
        #     return 13, -0.5
        # else :
        #     return 13, 0.5

        if abs(laser_scan[10] - laser_scan[-10]) < 3 and laser_scan[0] > 40:
            print("full speed")
            return laser_scan[0]/2, 0
        elif abs(laser_scan[10] - laser_scan[-10]) < 3 and laser_scan[0] < 5:
            print("full speed + esquive")
            return laser_scan[0]/2, 0.1
        elif abs(laser_scan[10] - laser_scan[-10]) < 3:
            print("tout droit")
            return laser_scan[0]/2.5, 0
        if laser_scan[10] < laser_scan[-10]:
            print("droite")
            return 15, (-0.5 if speed < 18 else -0.5/(speed/4))
        else :
            print("gauche")
            return 15, (0.5 if speed < 18 else 0.5/(speed/4))


        # print(laser_scan[0])
        # if laser_scan[0] < 20 and not laser_scan[0] <= self.old:
        #     self.right *= -1
        #     self.old = laser_scan[0]
        #     return (10, self.right * 0.5)
        # elif laser_scan[0] < 20 and laser_scan[0] > self.old:
        #     self.old = laser_scan[0]
        #     return (10, self.right*0.5)
        # else :
        #     self.old = laser_scan[0]
        #     return 10,0

    def whereInTrack(self, positionX : float, positionY : float, trackPoints : tuple):
        for i in trackPoints[0:]:
            if numpy.fabs((i.x - positionX) + (i.y - positionY)) < 3:
                return i
        return trackPoints[0]