import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""

    def convert_angle_to_index(self, angle: float, laser_scan: np.ndarray) -> int:
        """Convert an angle in radians to the corresponding index in the laser scan array."""
        self.angle_increment = 2 * np.pi / len(laser_scan)
        index = int(angle/ self.angle_increment) % len(laser_scan)

        return index

    def numpoint_cover(self, width, dist):
        angle = 1.5*np.arctan(width/(2*dist))
        num_points = int(np.ceil(angle / self.angle_increment))

        return num_points

    def cover_points(self, num_points, start_idx, cover_right, ranges):
        new_dist = ranges[start_idx]
        if cover_right:
            for i in range(num_points):
                next_idx = start_idx+1+i
                if next_idx >= len(ranges): break
                if ranges[next_idx] > new_dist:
                    ranges[next_idx] = new_dist
        else:
            for i in range(num_points):
                next_idx = start_idx-1-i
                if next_idx < 0: break
                if ranges[next_idx] > new_dist:
                    ranges[next_idx] = new_dist
        return ranges

    def extend_disparities(self, disparities, ranges, car_width, extra_pct):
        width_to_cover = 0.155 * (1+extra_pct/100)
        for index in disparities:
            first_idx = index-1
            points = ranges[first_idx:first_idx+2]
            close_idx = first_idx+np.argmin(points)
            far_idx = first_idx+np.argmax(points)
            close_dist = ranges[close_idx]
            num_points_to_cover = self.numpoint_cover(close_dist,
                    width_to_cover)
            cover_right = close_idx < far_idx
            ranges = self.cover_points(num_points_to_cover, close_idx,
                cover_right, ranges)
        return ranges

    def get_steering_angle(self, range_index, range_len):
        lidar_angle = (range_index - (range_len/2)) * self.angle_increment
        return lidar_angle

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
        disparity_treshold = 2

        laser_scan = laser_scan[self.convert_angle_to_index(-np.pi / 2, laser_scan):
                                self.convert_angle_to_index(np.pi / 2, laser_scan)
                                ]
        if laser_scan[0] == np.inf:
            laser_scan[0] = 10.0

        disparities = np.array([], dtype=np.uint8)

        for i in range(1, len(laser_scan)):
            if laser_scan[i] == np.inf:
                laser_scan[i] = 1000.0
            diff = laser_scan[i] - laser_scan[i-1]
            if diff > disparity_treshold:
                np.append(disparities, i)

        laser_scan = self.extend_disparities(disparities, laser_scan,
                0.5, 30)
        steering_angle = (self.get_steering_angle(laser_scan.argmax(),
                len(laser_scan)) - steering_angle)

        speed = 30
        
        a = (abs(steering_angle) % 0.5 * abs(steering_angle) / steering_angle)
        if steering_angle == 0:
            a = 0

        return speed, a
