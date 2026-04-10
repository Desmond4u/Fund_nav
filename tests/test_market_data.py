import sys
import unittest
from unittest.mock import Mock, patch

sys.modules.setdefault("pandas", Mock())

from src.data_fetcher import get_realtime_stock_prices
from src.valuation import estimate_nav_change


class RealtimePriceParsingTests(unittest.TestCase):
    @patch("src.data_fetcher.requests.get")
    def test_a_share_pct_change_is_calculated_from_pre_close_and_current_price(self, mock_get):
        response = Mock()
        response.content = (
            'var hq_str_sh600519="贵州茅台,1466.99,1455.02,1470.00,1476.21,1450.00,0,0,0,0,0,0,2026-04-10,15:00:00,00";'
        ).encode("gbk")
        mock_get.return_value = response

        prices = get_realtime_stock_prices(["sh600519"])

        self.assertIn("sh600519", prices)
        expected_pct_change = ((1470.00 - 1455.02) / 1455.02) * 100
        self.assertAlmostEqual(prices["sh600519"]["pct_change"], expected_pct_change, places=6)
        self.assertAlmostEqual(prices["sh600519"]["change"], expected_pct_change, places=6)

    def test_estimate_nav_change_prefers_explicit_pct_change_field(self):
        holdings = [{"code": "600519", "fetch_code": "sh600519", "weight": 10.0, "name": "贵州茅台"}]
        prices = {
            "sh600519": {
                "name": "贵州茅台",
                "price": 1470.00,
                "pct_change": 1.25,
                "change": 1476.21,
            }
        }

        result = estimate_nav_change(holdings, prices)

        self.assertAlmostEqual(result["estimated_change"], 1.25, places=6)
        self.assertEqual(result["details"][0]["change"], 1.25)


if __name__ == "__main__":
    unittest.main()
