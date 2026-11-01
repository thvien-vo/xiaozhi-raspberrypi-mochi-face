"""Weather data service (currently mock; pending real API integration)."""

from __future__ import annotations

import json
from typing import Any

from src.logging import get_logger

logger = get_logger()


def get_weather_payload(args: dict[str, Any]) -> str:
    city = args.get("city", "Beijing")
    logger.info(f"[WeatherTool] Getting current weather for {city}")
    # TODO: Call a real weather API in production
    weather_data = {
        "city": city,
        "temperature": 25,
        "condition": "Sunny",
        "humidity": 45,
        "wind": "NE Level 3",
        "aqi": 52,
    }
    return json.dumps(weather_data, ensure_ascii=False)


def get_forecast_payload(args: dict[str, Any]) -> str:
    city = args.get("city", "Beijing")
    days = args.get("days", 3)
    logger.info(f"[WeatherTool] Getting {days}-day weather forecast for {city}")
    forecast = [
        {"date": "Today", "high": 28, "low": 18, "condition": "Sunny"},
        {"date": "Tomorrow", "high": 26, "low": 17, "condition": "Partly Cloudy"},
        {"date": "Day after tomorrow", "high": 24, "low": 15, "condition": "Light Rain"},
    ]
    return json.dumps({"city": city, "forecast": forecast[:days]}, ensure_ascii=False)
