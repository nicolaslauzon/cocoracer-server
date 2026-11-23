import numpy
import numpy as np
import scipy
from cocoracer import Controller, TrackInfo

class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""
    def reset(self, track_info: TrackInfo) -> None:
        self.trackInfo = track_info
        self.right = 1
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

        print(laser_scan[0])
        if laser_scan[0] < 10 and not laser_scan[0] <= self.old:
            self.right *= -1
            self.old = laser_scan[0]
            return (30, self.right * numpy.pi/2)
        elif laser_scan[0] < 10 and laser_scan[0] > self.old:
            self.old = laser_scan[0]
            return (30, self.right*numpy.pi/2)
        else :
            self.old = laser_scan[0]
            return 30,0

    def whereInTrack(self, positionX : float, positionY : float, trackPoints : tuple):
        for i in trackPoints[0:]:
            if numpy.fabs((i.x - positionX) + (i.y - positionY)) < 3:
                return i
        return trackPoints[0]