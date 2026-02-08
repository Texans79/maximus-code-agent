class UnitConverter:
    def __init__(self):
        self.temperature = {
            'celsius': {'fahrenheit': lambda x: x * 9/5 + 32, 'kelvin': lambda x: x + 273.15},
            'fahrenheit': {'celsius': lambda x: (x - 32) * 5/9, 'kelvin': lambda x: (x - 32) * 5/9 + 273.15},
            'kelvin': {'celsius': lambda x: x - 273.15, 'fahrenheit': lambda x: (x - 273.15) * 9/5 + 32}
        }
        self.distance = {
            'miles': {'km': lambda x: x * 1.60934, 'meters': lambda x: x * 1609.34, 'feet': lambda x: x * 5280},
            'km': {'miles': lambda x: x / 1.60934, 'meters': lambda x: x * 1000, 'feet': lambda x: x * 3280.84},
            'meters': {'miles': lambda x: x / 1609.34, 'km': lambda x: x / 1000, 'feet': lambda x: x * 3.28084},
            'feet': {'miles': lambda x: x / 5280, 'km': lambda x: x / 3280.84, 'meters': lambda x: x / 3.28084}
        }
        self.weight = {
            'pounds': {'kg': lambda x: x * 0.453592, 'grams': lambda x: x * 453.592, 'ounces': lambda x: x * 16},
            'kg': {'pounds': lambda x: x / 0.453592, 'grams': lambda x: x * 1000, 'ounces': lambda x: x * 35.274},
            'grams': {'pounds': lambda x: x / 453.592, 'kg': lambda x: x / 1000, 'ounces': lambda x: x / 28.3495},
            'ounces': {'pounds': lambda x: x / 16, 'kg': lambda x: x / 35.274, 'grams': lambda x: x * 28.3495}
        }

    def convert(self, value, from_unit, to_unit):
        if from_unit in self.temperature and to_unit in self.temperature[from_unit]:
            return self.temperature[from_unit][to_unit](value)
        elif from_unit in self.distance and to_unit in self.distance[from_unit]:
            return self.distance[from_unit][to_unit](value)
        elif from_unit in self.weight and to_unit in self.weight[from_unit]:
            return self.weight[from_unit][to_unit](value)
        else:
            raise ValueError(f'Incompatible units: {from_unit} to {to_unit}')