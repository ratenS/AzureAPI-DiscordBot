from typing import Any, Dict

import httpx


class SafetyViolation(Exception):
    """Raised when content safety blocks a request or response."""


class SafetyService:
    def __init__(self, endpoint: str, api_key: str, block_severity: int) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._block_severity = block_severity

    async def analyze_text(self, text: str) -> Dict[str, Any]:
        url = f"{self._endpoint}/contentsafety/text:analyze?api-version=2024-09-01"
        headers = {"Ocp-Apim-Subscription-Key": self._api_key, "Content-Type": "application/json"}
        payload = {"text": text}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        categories = data.get("categoriesAnalysis", [])
        if any(category.get("severity", 0) >= self._block_severity for category in categories):
            raise SafetyViolation("Azure Content Safety blocked the content")

        return data
