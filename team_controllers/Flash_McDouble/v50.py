import numpy as np
from cocoracer import Controller, TrackInfo


class MyCar(Controller):

    def reset(self, track_info: TrackInfo) -> None:
        self.previous_steering = 0.0

    def step(
        self,
        x: float,
        y: float,
        yaw: float,
        speed: float,
        steering_angle: float,
        laser_scan: np.ndarray,
    ) -> tuple[float, float]:

        scan = self.clean_scan(laser_scan)

        target_steering = self.get_steering(
            scan,
            steering_angle,
            speed
        )

        target_speed = self.get_speed(
            scan,
            target_steering,
            speed
        )

        return target_speed, target_steering


    # ============================================================
    # NETTOYAGE DU LIDAR
    # ============================================================

    def clean_scan(self, laser_scan):
        scan = np.array(laser_scan, dtype=float)

        scan = np.nan_to_num(
            scan,
            nan=100.0,
            posinf=100.0,
            neginf=0.0
        )

        scan = np.clip(scan, 0.0, 100.0)

        # petit filtre médian maison
        if len(scan) >= 5:
            filtered = scan.copy()

            for i in range(2, len(scan) - 2):
                filtered[i] = np.median(scan[i - 2:i + 3])

            scan = filtered

        return scan


    # ============================================================
    # UTILITAIRES
    # ============================================================

    def sector_mean(self, scan, start, end):
        """
        start/end sont entre 0 et 1.
        Exemple:
        0.0 = avant
        0.25 = côté
        """

        n = len(scan)

        a = int(start * n)
        b = int(end * n)

        values = scan[a:b]

        if len(values) == 0:
            return 100.0

        return float(np.mean(values))


    def sector_min(self, scan, start, end):
        n = len(scan)

        a = int(start * n)
        b = int(end * n)

        values = scan[a:b]

        if len(values) == 0:
            return 100.0

        return float(np.min(values))


    # ============================================================
    # DIRECTION
    # ============================================================

    def get_steering(self, scan, current_steering, speed):

        n = len(scan)

        # --------------------------------------------------------
        # DISTANCES IMPORTANTES
        # --------------------------------------------------------

        front = scan[0]

        # zones avant-gauche et avant-droite
        left_front = np.mean(scan[1:max(2, n // 8)])
        right_front = np.mean(scan[-max(2, n // 8):])

        left_medium = np.mean(
            scan[n // 8:n // 4]
        )

        right_medium = np.mean(
            scan[3 * n // 4:7 * n // 8]
        )

        # --------------------------------------------------------
        # ÉVITEMENT D'URGENCE
        # --------------------------------------------------------

        danger_distance = max(
            2.5,
            speed * 0.20
        )

        if front < danger_distance:

            if left_front > right_front:
                target = 0.85
            else:
                target = -0.85

            return self.smooth_steering(target)


        # --------------------------------------------------------
        # TROUVER LA MEILLEURE DIRECTION
        # --------------------------------------------------------

        #
        # On regarde surtout la moitié avant du LiDAR.
        #
        # Les indices positifs juste après 0 = gauche
        # Les indices vers la fin = droite
        #

        max_angle_index = max(3, n // 4)

        best_score = -1
        best_direction = 0.0

        # candidats à gauche
        for i in range(1, max_angle_index):

            direction = i / max_angle_index

            window_start = max(0, i - 2)
            window_end = min(n, i + 3)

            clearance = np.mean(
                scan[window_start:window_end]
            )

            #
            # pénalité pour éviter de tourner inutilement
            #
            turning_penalty = abs(direction) * 3.0

            score = clearance - turning_penalty

            if score > best_score:
                best_score = score
                best_direction = direction


        # candidats à droite
        for offset in range(1, max_angle_index):

            i = n - offset

            direction = -(offset / max_angle_index)

            window = []

            for j in range(-2, 3):
                window.append(
                    scan[(i + j) % n]
                )

            clearance = np.mean(window)

            turning_penalty = abs(direction) * 3.0

            score = clearance - turning_penalty

            if score > best_score:
                best_score = score
                best_direction = direction


        # --------------------------------------------------------
        # BIAIS BASÉ SUR LES MURS
        # --------------------------------------------------------

        wall_difference = left_medium - right_medium

        wall_correction = np.clip(
            wall_difference * 0.025,
            -0.25,
            0.25
        )

        target = (
            best_direction * 0.75
            + wall_correction
        )


        # --------------------------------------------------------
        # LIGNE DROITE
        # --------------------------------------------------------

        if (
            front > 45
            and left_front > 15
            and right_front > 15
        ):
            target *= 0.25


        target = np.clip(
            target,
            -0.9,
            0.9
        )

        return self.smooth_steering(target)


    # ============================================================
    # LISSAGE
    # ============================================================

    def smooth_steering(self, target):

        alpha = 0.45

        steering = (
            alpha * target
            + (1 - alpha) * self.previous_steering
        )

        self.previous_steering = steering

        return float(
            np.clip(
                steering,
                -0.9,
                0.9
            )
        )


    # ============================================================
    # VITESSE
    # ============================================================

    def get_speed(self, scan, steering, current_speed):

        n = len(scan)

        front = scan[0]

        front_left = np.mean(
            scan[1:max(2, n // 12)]
        )

        front_right = np.mean(
            scan[-max(2, n // 12):]
        )

        front_clearance = min(
            front,
            front_left,
            front_right
        )


        # --------------------------------------------------------
        # DANGER IMMÉDIAT
        # --------------------------------------------------------

        if front_clearance < 3:
            return 4.0

        if front_clearance < 6:
            return 7.0

        if front_clearance < 10:
            return 10.0


        # --------------------------------------------------------
        # VITESSE BASÉE SUR LE VIRAGE
        # --------------------------------------------------------

        turn = abs(steering)

        if turn > 0.75:
            speed = 9

        elif turn > 0.55:
            speed = 12

        elif turn > 0.35:
            speed = 16

        elif turn > 0.18:
            speed = 21

        else:
            speed = 30


        # --------------------------------------------------------
        # BOOST LIGNE DROITE
        # --------------------------------------------------------

        if (
            front > 70
            and turn < 0.08
        ):
            speed = 35

        elif (
            front > 50
            and turn < 0.12
        ):
            speed = 32


        # --------------------------------------------------------
        # ANTICIPATION DU MUR
        # --------------------------------------------------------

        safe_speed = (
            7.0
            + front_clearance * 0.55
        )

        speed = min(
            speed,
            safe_speed
        )

        return float(
            np.clip(
                speed,
                4,
                35
            )
        )