import unittest
import numpy as np
from PIL import Image
from matrix_output import dimensions, fill_framebuffer


class MatrixMappingTests(unittest.TestCase):
    def image(self):
        pixels = np.zeros((64, 192, 3), dtype=np.uint8)
        for tile in range(3):
            pixels[:, tile*64:(tile+1)*64, tile] = 100
            pixels[0, tile*64] = (10, 20, 30)
        return Image.fromarray(pixels)

    def test_chain_left_to_right_rotates_only_right(self):
        image = self.image()
        fb = np.zeros((64, 192, 3), dtype=np.uint8)
        fill_framebuffer(fb, image, 1, 'chain', [0, 1, 2], [0, 0, 180])
        np.testing.assert_array_equal(fb[:, :128], np.asarray(image)[:, :128])
        self.assertEqual(tuple(fb[63, 191]), (10, 20, 30))
        self.assertEqual(tuple(fb[0, 128]), (0, 0, 100))

    def test_existing_parallel_mapping_is_preserved(self):
        fb = np.zeros((192, 64, 3), dtype=np.uint8)
        fill_framebuffer(fb, self.image(), .5, 'parallel', [2, 1, 0], [180, 0, 0])
        self.assertEqual(tuple(fb[63, 63]), (5, 10, 15))
        self.assertEqual(tuple(fb[0, 0]), (0, 0, 50))
        self.assertEqual(tuple(fb[65, 1]), (0, 50, 0))
        self.assertEqual(tuple(fb[129, 1]), (50, 0, 0))

    def test_chain_permutation(self):
        fb = np.zeros((64, 192, 3), dtype=np.uint8)
        fill_framebuffer(fb, self.image(), 1, 'chain', [2, 0, 1], [0, 0, 0])
        self.assertEqual(tuple(fb[1, 1]), (0, 0, 100))
        self.assertEqual(tuple(fb[1, 65]), (100, 0, 0))
        self.assertEqual(tuple(fb[1, 129]), (0, 100, 0))

    def test_geometry_and_invalid_layout(self):
        self.assertEqual(dimensions('chain'), (192, 64, 2))
        self.assertEqual(dimensions('parallel'), (64, 192, 6))
        with self.assertRaises(ValueError): dimensions('unknown')
        with self.assertRaises(ValueError):
            fill_framebuffer(np.zeros((192, 64, 3)), self.image(), 1, 'chain', [0, 1, 2], [0, 0, 0])


if __name__ == '__main__': unittest.main()
