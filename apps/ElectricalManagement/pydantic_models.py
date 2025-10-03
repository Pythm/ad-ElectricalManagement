#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, RootModel
from dataclasses import dataclass, field


class MaxUsage(BaseModel):
    max_kwh_usage_pr_hour: int = 0
    topUsage: List[float] = Field(default_factory=lambda: [0, 0, 0])


class TempConsumption(BaseModel):
    Consumption: float | None = None
    HeaterConsumption: float | None = None
    Counter: int | None = None


class IdleBlock(BaseModel):
    ConsumptionData: Dict[str, TempConsumption] | None = None


class PeakHour(BaseModel):
    start: datetime
    end: datetime
    duration: int | None = None


class HeaterBlock(BaseModel):
    heater: str | None = None
    consumptionSensor: str | None = None
    validConsumptionSensor: bool | None = None
    kWhconsumptionSensor: str | None = None
    max_continuous_hours: int | None = None
    on_for_minimum: int | None = None
    pricedrop: float | None = None
    pricedifference_increase: float | None = None
    vacation: Union[str, bool] = False
    automate: Union[str, bool] = False
    recipient: Optional[List[str]] = None
    indoor_sensor_temp: str | None = None
    window_temp: str | None = None
    window_offset: int | None = None
    target_indoor_input: str | None = None
    target_indoor_temp: int | None = None
    save_temp_offset: float | None = None
    save_temp: int | None = None
    away_temp: int | None = None
    rain_level: float | None = None
    anemometer_speed: float | None = None
    low_price_max_continuous_hours: int | None = None
    priceincrease: float | None = None
    windowsensors: List[str] | None = None
    getting_cold: int | None = None
    daytime_savings: List[Dict] | None = None
    temperatures: List[Dict] | None = None

    ConsumptionData: Dict[str, Dict[str, TempConsumption]] = Field(default_factory=dict)
    peak_hours: List[PeakHour] = Field(default_factory=list)
    power: float | None = None


class ChargerData(BaseModel):
    # Sensors:
    charger_sensor: str | None = None
    charger_switch: str | None = None
    charging_amps: str | None = None
    charger_power: str | None = None
    session_energy: str | None = None
    idle_current: Union[str, bool] = False
    guest: Union[str, bool] = False

    # Helpers
    ampereCharging: float = 0
    min_ampere: int = 6
    maxChargerAmpere: int = 0
    volts: int = 220
    phases: int = 1
    voltPhase: int = 220
    connected_car_name: str | None = None

    # Easee sensors
    max_charger_limit: Optional[str] = None
    reason_for_no_current: Optional[str] = None
    voltage: Optional[str] = None


class CarData(BaseModel):
    charger_sensor: str | None = None
    charge_limit: str | None = None
    battery_sensor: str | None = None
    asleep_sensor: str | None = None
    online_sensor: str | None = None
    location_tracker: str | None = None
    destination_location_tracker: str | None = None
    arrival_time: str | None = None
    software_update: str | None = None
    force_data_update: str | None = None
    polling_switch: str | None = None
    data_last_update_time: str | None = None
    battery_size: int = 100
    pref_charge_limit: int = 100
    priority: int = 3
    finish_by_hour: Union[str, int] = 7
    charge_now: Union[str, bool] = False
    charge_only_on_solar: Union[str, bool] = False
    departure: str | None = None
    battery_reg_counter: int = 0
    car_limit_max_ampere: float | None = None
    max_kWh_charged: float = 5
    old_charge_limit: float = 100
    kWh_remain_to_charge: float = -2


class ChargingQueueItem(BaseModel):
    vehicle_id: str
    kWhRemaining: float
    maxAmps: int
    voltPhase: int
    finish_by_hour: int
    priority: int
    estHourCharge: float
    name: str
    chargingStart: datetime | None = None
    estimateStop: datetime | None = None
    chargingStop: datetime | None = None
    price: float | None = None
    informedStart: datetime | None = None
    informedStop: datetime | None = None

    def to_dict(self) -> dict:
        return self.model_dump(
            by_alias=False,
            exclude_none=True
        )

@dataclass(order=True)
class WattSlot:
    start: datetime
    end: datetime
    available_Wh: float

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


class PersistenceData(BaseModel):
    max_usage: MaxUsage = Field(alias="MaxUsage", default_factory=MaxUsage)
    idle_usage: IdleBlock = Field(alias="IdleUsage", default_factory=IdleBlock)
    charger: Dict[str, ChargerData] = Field(alias="charger", default_factory=dict)
    car: Dict[str, CarData] = Field(alias="carName", default_factory=dict)
    heater: Dict[str, HeaterBlock] = Field(alias="heater", default_factory=dict)
    chargingQueue: List[ChargingQueueItem] = Field(alias="chargingQueue", default_factory=list)
    queueChargingList: List[Any] = Field(alias="queueChargingList", default_factory=list)
    solarChargingList: List[Any] = Field(alias="solarChargingList", default_factory=list)
    available_watt: List[WattSlot] = Field(alias="available_watt", default_factory=list)

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": False,
        "json_encoders": {   # <‑‑ tell pydantic how to serialise a WattSlot
            WattSlot: lambda ws: ws.__dict__,
        },
    }

    def has_initialized_consuming_objects(self) -> bool:
        """ Return  ``True`` if at least one collection is non empty. """
        return bool(self.car) or bool(self.charger) or bool(self.heater)

def _json_path(path: str) -> Path:
    return Path(path).expanduser()

def load_persistence(path: str) -> PersistenceData:
    """Load a JSON file into a typed PersistenceData instance."""
    try:
        return PersistenceData.parse_file(_json_path(path))
    except FileNotFoundError:
        persistence = PersistenceData()
        dump_persistence(path, persistence)
        return persistence

def dump_persistence(path: str, data: PersistenceData) -> None:
    """Write the PersistenceData back to JSON."""
    with open(_json_path(path), 'w') as f:
        f.write(data.model_dump_json(exclude_none=True, by_alias=True, indent=4))
