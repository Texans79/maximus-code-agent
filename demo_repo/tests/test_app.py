import unittest
import pytest
from app import hello, power, add, subtract, multiply, divide

class TestApp(unittest.TestCase):
    def test_power(self):
        self.assertEqual(power(2, 3), 8)
        self.assertEqual(power(5, 0), 1)

class TestHello(unittest.TestCase):
    def test_hello(self):
        self.assertEqual(hello('World'), 'Hello, World!')

class TestAdd:
    def test_positive(self):
        assert add(2, 3) == 5

    def test_negative(self):
        assert add(-1, -2) == -3

    def test_zero(self):
        assert add(0, 0) == 0


class TestSubtract:
    def test_basic(self):
        assert subtract(10, 4) == 6

    def test_negative_result(self):
        assert subtract(3, 7) == -4


class TestMultiply:
    def test_basic(self):
        assert multiply(6, 7) == 42

    def test_zero(self):
        assert multiply(5, 0) == 0


class TestDivide:
    def test_basic(self):
        assert divide(15, 3) == 5.0

    def test_float(self):
        assert divide(10, 3) == pytest.approx(3.333, rel=1e-2)

    def test_zero_division(self):
        with pytest.raises(ValueError, match="Cannot divide by zero"):
            divide(1, 0)
