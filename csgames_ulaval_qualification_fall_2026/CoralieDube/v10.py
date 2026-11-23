import math

import numpy as np

from cocoracer import Controller, TrackInfo

def distancePoints(xs, ys, x, y):
    difx = xs - x
    dify = ys - y
    return (difx.multiply(difx) + dify.multiply(dify)).argmin()


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
        """Return (target_speed, target_steerigzng_angle). See cocoracer.py and wtf.md."""

        minIdx = distancePoints(TrackInfo.centerline.x, TrackInfo.centerline.y, x, y)
        idx = minIdx[0]

        deltaX = x - centerLineX[idx]
        deltaY = y - centerLineY[idx]

        return 20.0,math.atan2(deltaY, deltaX)
