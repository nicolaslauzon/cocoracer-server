import numpy as np
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



        print ("souki")
        left  = laser_scan(5)
        right = laser_scan(-5)
        center = laser_scan(0)

        angle = 0

        if left > right:
            tp = left
            angle += math.atan(math.sin(1.04)/tp)
        else :
            tp = right
            angle -= math.atan(2*math.sin(1.04)/tp)






        return 20.0, angle
