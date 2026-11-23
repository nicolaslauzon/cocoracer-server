import numpy as np

from cocoracer import Controller, TrackInfo


class MyCar(Controller):
    """Your car: edit `step`, then push this file. Contract: cocoracer.py, wtf.md."""

    def reset(self, track_info: TrackInfo) -> None:
        """Called once before the first step. See cocoracer.py and wtf.md."""

    def convert_angle_to_index(self, angle: float, laser_scan: np.ndarray) -> int:
        """Convert an angle in radians to the corresponding index in the laser scan array."""
        self.angle_increment = 2 * np.pi / len(laser_scan)
        index = int((angle + np.pi) / self.angle_increment) % len(laser_scan)

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
        steering_angle = np.clip(lidar_angle, np.radians(-90), np.radians(90))
        return steering_angle

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

        laser_scan = np.append(laser_scan[0:int(len(laser_scan)/2)], laser_scan[int(len(laser_scan)/2): len(laser_scan)])
        angle_increment = 2 * np.pi / len(laser_scan)

        ranges = laser_scan

        # 1. FILTRER (Nettoyage et restriction du FOV)
        # Remplacer les infinis (pas de mur) par une grande distance arbitraire (ex: 10m)
        ranges = np.nan_to_num(ranges, posinf=10.0, neginf=0.0)

        # Créer un tableau des angles correspondants
        angles = 0 + np.arange(len(ranges)) * angle_increment

        # Masquer ce qui est derrière nous (on force à 0)
        masque_avant = (angles <= -np.pi/2) & (angles >= 3*np.pi/2)

        # On extrait uniquement les rayons devant nous pour le traitement
        idx_valides = np.where(masque_avant)[0]
        ranges_avant = ranges[idx_valides]

        # Copie de travail que l'on va "gonfler"
        ranges_etendus = np.copy(ranges_avant)

        # 2. DÉTECTER (Trouver les sauts de profondeur)
        for i in range(len(ranges_avant) - 1):
            r1 = ranges_avant[i]
            r2 = ranges_avant[i + 1]

            # Si la différence entre deux rayons voisins est plus grande que le seuil...
            if abs(r1 - r2) > 0.3:

                # 3. ÉTENDRE (La magie du Disparity Extender)
                # On détermine quel côté du saut est le plus proche de la voiture
                distance_proche = min(r1, r2)

                # Combien de rayons LiDAR représentent la largeur de notre voiture à cette distance ?
                # Formule de l'arc : Angle = Distance_Arc / Rayon
                angle_a_couvrir = (0.5 / 2.0) / (distance_proche + 1e-5)
                nb_indices = int(np.ceil(angle_a_couvrir / angle_increment))

                if r1 < r2:
                    # Le mur de gauche (r1) est plus proche. On étend la paroi vers la droite (indices >)
                    debut = i + 1
                    fin = min(i + 1 + nb_indices, len(ranges_avant))
                    # On n'écrase que si notre mur virtuel est plus proche que ce qu'il y avait déjà
                    ranges_etendus[debut:fin] = np.minimum(ranges_etendus[debut:fin], distance_proche)
                else:
                    # Le mur de droite (r2) est plus proche. On étend la paroi vers la gauche (indices <)
                    debut = max(0, i + 1 - nb_indices)
                    fin = i + 1
                    ranges_etendus[debut:fin] = np.minimum(ranges_etendus[debut:fin], distance_proche)

        # 4. VISER ET CONDUIRE
        # On cherche le point qui offre la plus longue ligne droite DANS LE MONDE ÉTENDU
        idx_cible_relatif = np.argmax(ranges_etendus)

        # On retrouve l'index d'origine dans le tableau complet
        idx_cible_absolu = idx_valides[idx_cible_relatif]
        angle_cible = angles[idx_cible_absolu]

        # Logique de vitesse adaptative simple
        vitesse = 5.0
        if abs(angle_cible) > 0.2:
            vitesse = 3.0
        if abs(angle_cible) > 0.5:
            vitesse = 1.5
        
        return vitesse, angle_cible