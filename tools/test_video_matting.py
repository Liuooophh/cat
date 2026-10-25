"""Offline matte regressions; run with tools/requirements-media.txt installed."""
import unittest

import numpy as np

from prepare_video_assets import remove_white


def studio_scene():
    image = np.full((816, 1088, 3), 253, dtype=np.uint8)
    image[120:795, 400:700] = (160, 157, 153)
    # A near-neutral white chest, touching the side of the silhouette. Lowering
    # the key threshold across the whole image would cut this fur away.
    image[350:520, 400:640] = (222, 222, 222)
    image[745:795, 630:700] = (120, 117, 113)
    image[752:787, 638:692] = (237, 237, 237)
    # This gray studio floor is attached to the actor, not a detached speck.
    image[795:810, 390:710] = (211, 211, 211)
    # The floor also rises beside the paw, above the old horizontal cutoff.
    # It must not become a white wing protruding from the cat's silhouette.
    image[740:757, 700:720] = (225, 224, 227)
    # A separate, colored action prop must survive the contact-floor cleanup.
    image[787:800, 735:750] = (145, 88, 34)
    return image


class ContactFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = studio_scene()
        cls.color, cls.alpha = remove_white(cls.scene)

    def test_gray_floor_attached_to_cat_is_transparent(self):
        self.assertEqual(float(self.alpha[802, 500]), 0.0)
        self.assertEqual(float(self.alpha[805, 700]), 0.0)

    def test_white_chest_and_enclosed_white_paw_remain_opaque(self):
        self.assertEqual(float(self.alpha[430, 410]), 1.0)
        self.assertEqual(float(self.alpha[770, 665]), 1.0)

    def test_gray_floor_beside_paw_has_no_horizontal_shelf(self):
        for y in (745, 750, 755):
            self.assertEqual(float(self.alpha[y, 712]), 0.0)

    def test_colored_food_prop_is_preserved(self):
        self.assertEqual(float(self.alpha[793, 742]), 1.0)

    def test_clear_background_has_no_white_color_residue(self):
        self.assertTrue(np.isfinite(self.alpha).all())
        self.assertTrue(((self.alpha >= 0) & (self.alpha <= 1)).all())
        self.assertTrue((self.color[self.alpha == 0] == 0).all())


if __name__ == "__main__":
    unittest.main()
