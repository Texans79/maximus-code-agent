import unittest
from converter import UnitConverter

class TestUnitConverter(unittest.TestCase):
    def setUp(self):
        self.converter = UnitConverter()

    def test_temperature_conversion(self):
        # Celsius to Fahrenheit
        self.assertAlmostEqual(self.converter.convert(0, 'celsius', 'fahrenheit'), 32)
        self.assertAlmostEqual(self.converter.convert(100, 'celsius', 'fahrenheit'), 212)
        # Celsius to Kelvin
        self.assertAlmostEqual(self.converter.convert(0, 'celsius', 'kelvin'), 273.15)
        self.assertAlmostEqual(self.converter.convert(100, 'celsius', 'kelvin'), 373.15)
        # Fahrenheit to Celsius
        self.assertAlmostEqual(self.converter.convert(32, 'fahrenheit', 'celsius'), 0)
        self.assertAlmostEqual(self.converter.convert(212, 'fahrenheit', 'celsius'), 100)
        # Fahrenheit to Kelvin
        self.assertAlmostEqual(self.converter.convert(32, 'fahrenheit', 'kelvin'), 273.15)
        self.assertAlmostEqual(self.converter.convert(212, 'fahrenheit', 'kelvin'), 373.15)
        # Kelvin to Celsius
        self.assertAlmostEqual(self.converter.convert(273.15, 'kelvin', 'celsius'), 0)
        self.assertAlmostEqual(self.converter.convert(373.15, 'kelvin', 'celsius'), 100)
        # Kelvin to Fahrenheit
        self.assertAlmostEqual(self.converter.convert(273.15, 'kelvin', 'fahrenheit'), 32)
        self.assertAlmostEqual(self.converter.convert(373.15, 'kelvin', 'fahrenheit'), 212)

    def test_distance_conversion(self):
        # Miles to Kilometers
        self.assertAlmostEqual(self.converter.convert(1, 'miles', 'km'), 1.60934)
        self.assertAlmostEqual(self.converter.convert(5, 'miles', 'km'), 8.0467)
        # Miles to Meters
        self.assertAlmostEqual(self.converter.convert(1, 'miles', 'meters'), 1609.34)
        self.assertAlmostEqual(self.converter.convert(5, 'miles', 'meters'), 8046.7)
        # Miles to Feet
        self.assertAlmostEqual(self.converter.convert(1, 'miles', 'feet'), 5280)
        self.assertAlmostEqual(self.converter.convert(5, 'miles', 'feet'), 26400)
        # Kilometers to Miles
        self.assertAlmostEqual(self.converter.convert(1, 'km', 'miles'), 0.621371, places=5)
        self.assertAlmostEqual(self.converter.convert(5, 'km', 'miles'), 3.10686, places=4)
        # Kilometers to Meters
        self.assertAlmostEqual(self.converter.convert(1, 'km', 'meters'), 1000)
        self.assertAlmostEqual(self.converter.convert(5, 'km', 'meters'), 5000)
        # Kilometers to Feet
        self.assertAlmostEqual(self.converter.convert(1, 'km', 'feet'), 3280.84)
        self.assertAlmostEqual(self.converter.convert(5, 'km', 'feet'), 16404.2)
        # Meters to Miles
        self.assertAlmostEqual(self.converter.convert(1000, 'meters', 'miles'), 0.621371, places=4)
        self.assertAlmostEqual(self.converter.convert(5000, 'meters', 'miles'), 3.10686, places=4)
        # Meters to Kilometers
        self.assertAlmostEqual(self.converter.convert(1000, 'meters', 'km'), 1)
        self.assertAlmostEqual(self.converter.convert(5000, 'meters', 'km'), 5)
        # Meters to Feet
        self.assertAlmostEqual(self.converter.convert(1, 'meters', 'feet'), 3.28084)
        self.assertAlmostEqual(self.converter.convert(5, 'meters', 'feet'), 16.4042)
        # Feet to Miles
        self.assertAlmostEqual(self.converter.convert(5280, 'feet', 'miles'), 1)
        self.assertAlmostEqual(self.converter.convert(26400, 'feet', 'miles'), 5)
        # Feet to Kilometers
        self.assertAlmostEqual(self.converter.convert(3280.84, 'feet', 'km'), 1)
        self.assertAlmostEqual(self.converter.convert(16404.2, 'feet', 'km'), 5)
        # Feet to Meters
        self.assertAlmostEqual(self.converter.convert(3.28084, 'feet', 'meters'), 1)
        self.assertAlmostEqual(self.converter.convert(16.4042, 'feet', 'meters'), 5)

    def test_weight_conversion(self):
        # Pounds to Kilograms
        self.assertAlmostEqual(self.converter.convert(1, 'pounds', 'kg'), 0.453592)
        self.assertAlmostEqual(self.converter.convert(5, 'pounds', 'kg'), 2.26796)
        # Pounds to Grams
        self.assertAlmostEqual(self.converter.convert(1, 'pounds', 'grams'), 453.592)
        self.assertAlmostEqual(self.converter.convert(5, 'pounds', 'grams'), 2267.96)
        # Pounds to Ounces
        self.assertAlmostEqual(self.converter.convert(1, 'pounds', 'ounces'), 16)
        self.assertAlmostEqual(self.converter.convert(5, 'pounds', 'ounces'), 80)
        # Kilograms to Pounds
        self.assertAlmostEqual(self.converter.convert(1, 'kg', 'pounds'), 2.20462, places=5)
        self.assertAlmostEqual(self.converter.convert(5, 'kg', 'pounds'), 11.0231, places=4)
        # Kilograms to Grams
        self.assertAlmostEqual(self.converter.convert(1, 'kg', 'grams'), 1000)
        self.assertAlmostEqual(self.converter.convert(5, 'kg', 'grams'), 5000)
        # Kilograms to Ounces
        self.assertAlmostEqual(self.converter.convert(1, 'kg', 'ounces'), 35.274)
        self.assertAlmostEqual(self.converter.convert(5, 'kg', 'ounces'), 176.37)
        # Grams to Pounds
        self.assertAlmostEqual(self.converter.convert(453.592, 'grams', 'pounds'), 1)
        self.assertAlmostEqual(self.converter.convert(2267.96, 'grams', 'pounds'), 5)
        # Grams to Kilograms
        self.assertAlmostEqual(self.converter.convert(1000, 'grams', 'kg'), 1)
        self.assertAlmostEqual(self.converter.convert(5000, 'grams', 'kg'), 5)
        # Grams to Ounces
        self.assertAlmostEqual(self.converter.convert(28.3495, 'grams', 'ounces'), 1)
        self.assertAlmostEqual(self.converter.convert(141.7475, 'grams', 'ounces'), 5)
        # Ounces to Pounds
        self.assertAlmostEqual(self.converter.convert(16, 'ounces', 'pounds'), 1)
        self.assertAlmostEqual(self.converter.convert(80, 'ounces', 'pounds'), 5)
        # Ounces to Kilograms
        self.assertAlmostEqual(self.converter.convert(35.274, 'ounces', 'kg'), 1)
        self.assertAlmostEqual(self.converter.convert(176.37, 'ounces', 'kg'), 5)
        # Ounces to Grams
        self.assertAlmostEqual(self.converter.convert(1, 'ounces', 'grams'), 28.3495)
        self.assertAlmostEqual(self.converter.convert(5, 'ounces', 'grams'), 141.7475)

    def test_round_trip_accuracy(self):
        # Temperature
        self.assertAlmostEqual(self.converter.convert(self.converter.convert(0, 'celsius', 'fahrenheit'), 'fahrenheit', 'celsius'), 0)
        self.assertAlmostEqual(self.converter.convert(self.converter.convert(100, 'celsius', 'kelvin'), 'kelvin', 'celsius'), 100)
        # Distance
        self.assertAlmostEqual(self.converter.convert(self.converter.convert(1, 'miles', 'km'), 'km', 'miles'), 1)
        self.assertAlmostEqual(self.converter.convert(self.converter.convert(1000, 'meters', 'feet'), 'feet', 'meters'), 1000)
        # Weight
        self.assertAlmostEqual(self.converter.convert(self.converter.convert(1, 'pounds', 'kg'), 'kg', 'pounds'), 1)
        self.assertAlmostEqual(self.converter.convert(self.converter.convert(1000, 'grams', 'ounces'), 'ounces', 'grams'), 1000)

    def test_edge_cases(self):
        # Absolute Zero in Celsius and Kelvin
        self.assertAlmostEqual(self.converter.convert(-273.15, 'celsius', 'kelvin'), 0)
        self.assertAlmostEqual(self.converter.convert(0, 'kelvin', 'celsius'), -273.15)

    def test_incompatible_units(self):
        with self.assertRaises(ValueError):
            self.converter.convert(1, 'celsius', 'miles')
        with self.assertRaises(ValueError):
            self.converter.convert(1, 'miles', 'celsius')
        with self.assertRaises(ValueError):
            self.converter.convert(1, 'pounds', 'miles')

if __name__ == '__main__':
    unittest.main()