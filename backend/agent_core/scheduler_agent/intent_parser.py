"""Intent parser - extracts stock codes and research dimensions from user request"""
import re
from typing import Any, Dict, List


class IntentParser:
    COMPANY_MAP = {
        "完美世界": "002624",
        "贵州茅台": "600519",
        "五粮液": "000858",
        "宁德时代": "300750",
        "比亚迪": "002594",
        "中国平安": "601318",
        "招商银行": "600036",
        "格力电器": "000651",
        "美的集团": "000333",
        "恒瑞医药": "600276",
        "药明康德": "603259",
        "隆基绿能": "601012",
        "海康威视": "002415",
        "京东方A": "000725",
        "中兴通讯": "000063",
    }

    def parse(self, request_text: str) -> Dict[str, Any]:
        dims = self._extract_dimensions(request_text)
        stocks = self._extract_stocks(request_text)
        stock_codes = self._extract_stock_codes(request_text)
        return {
            "original_request": request_text,
            "dimensions": dims,
            "scenario": "financial_research",
            "output_format": "deep_report",
            "time_range": "latest",
            "entities": {"stocks": stocks, "stock_codes": stock_codes},
            "stock_codes": stock_codes,
        }

    def _extract_dimensions(self, text: str) -> List[str]:
        patterns = {"market_size": ["规模", "市场", "行业"],
                     "financial": ["财务", "营收", "利润", "财报", "季报"],
                     "competitor": ["竞争", "竞品", "格局"],
                     "risk": ["风险", "预警"],
                     "valuation": ["估值", "预测", "目标价"]}
        return [d for d, kws in patterns.items() if any(kw in text for kw in kws)] or ["market_size", "financial", "competitor"]

    def _extract_stocks(self, text: str) -> List[str]:
        codes = re.findall(r"\d{6}", text)
        names = [name for name in self.COMPANY_MAP if name in text]
        return list(set(codes + names))

    def _extract_stock_codes(self, text: str) -> List[str]:
        codes = re.findall(r"\d{6}", text)
        for name, code in self.COMPANY_MAP.items():
            if name in text and code not in codes:
                codes.append(code)
        tickers = re.findall(r"\b([A-Z]{1,5})\b", text)
        for t in tickers:
            if len(t) >= 2 and t not in codes and t.upper() == t:
                codes.append(t)
        return codes
