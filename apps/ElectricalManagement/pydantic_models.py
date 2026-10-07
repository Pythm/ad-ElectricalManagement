#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
from datetime import datetime, time, timedelta
import os
import time as time_module
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, ValidationError, conlist, conint, field_validator

# WattSlot/Decision live in utils.py; re-exported here so existing
# `from pydantic_models import WattSlot, Decision` keeps working.
from utils import WattSlot, Decision  # noqa: F401

LogFunc = Callable[[str, str], None]   # (message, level)

# Top-level `options:` values the main app understands. Anything else is logged as a WARNING.
KNOWN_OPTIONS = frozenset({
    'notify_overconsumption_also_when_away',
    'notify_overconsumption',
    'pause_charging',
})


def _none_to_list(value: Any) -> Any:
    return [] if value is None else value


class ElectricalManagementConfig(BaseModel):
    """ Validated view of the top-level apps.yaml arguments read by ElectricalUsage.

        Defaults are identical to the previous ``self.args.get(...)`` calls. Unknown keys
        (``module``, ``class``, ``dependencies``, ...) are ignored. The per-device lists
        (tesla/audi/cars/easee/climate/heater_switches) are only checked to be lists of
        dicts; their contents are still read from ``self.args`` and passed through untouched.
    """
    model_config = ConfigDict(extra='ignore')

    electricalPriceApp: str
    main_namespace: str = 'default'
    notify_app: str | None = None
    notify_receiver: List[str] = Field(default_factory=list)
    home_name: str = 'home'
    json_path: str | None = None

    power_consumption: str | None = None
    accumulated_consumption_current_hour: str | None = None
    power_production: str | None = None
    accumulated_production_current_hour: str | None = None

    max_kwh_goal: float = 15
    buffer: float = 0.4
    options: List[str] = Field(default_factory=list)
    automate: Union[bool, str] = True
    away_state: str | None = None
    vacation: str | None = None
    infotext: str | None = None
    stopAtPriceIncrease: float = 0.3
    startBeforePrice: float = 0.01

    tesla: List[Dict[str, Any]] = Field(default_factory=list)
    audi: List[Dict[str, Any]] = Field(default_factory=list)
    cars: List[Dict[str, Any]] = Field(default_factory=list)
    easee: List[Dict[str, Any]] = Field(default_factory=list)
    climate: List[Dict[str, Any]] = Field(default_factory=list)
    heater_switches: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator('notify_receiver', mode='before')
    @classmethod
    def _receiver_to_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator('options', 'tesla', 'audi', 'cars', 'easee', 'climate', 'heater_switches', mode='before')
    @classmethod
    def _empty_list_when_missing(cls, value: Any) -> Any:
        return _none_to_list(value)

    def unknown_options(self) -> List[str]:
        """ Options given in the config that the app does not know. Never an error. """
        return [opt for opt in self.options if opt not in KNOWN_OPTIONS]


class MaxUsage(BaseModel):
    # float since 1.0.6 (was int); existing files with ints load fine.
    max_kwh_usage_pr_hour: float = 0
    topUsage: List[float] = Field(default_factory=lambda: [0, 0, 0])
    calculated_difference_on_idle: float = 1.1

class HighConsumptionHour(BaseModel):
    high_consumption_hours: conlist(
        conint(ge=6, le=22)
    ) = Field(default_factory=list)

class TempConsumption(BaseModel):
    Consumption: float | None = None
    HeaterConsumption: float | None = None
    Counter: int | None = None


class IdleBlock(BaseModel):
    ConsumptionData: Dict[int, TempConsumption] = Field(default_factory=dict)


class PeakHour(BaseModel):
    start: datetime
    end: datetime
    duration: timedelta

class WeatherData(BaseModel):
    out_temp: float = 10.0

class HeaterBlock(BaseModel):
    heater: str | None = None
    consumptionSensor: str | None = None
    validConsumptionSensor: bool | None = None
    normal_power: float = 0.0
    kWhconsumptionSensor: str | None = None
    max_continuous_hours: int | None = None
    on_for_minimum: int | None = None
    pricedrop: float | None = None
    pricedifference_increase: float | None = None
    vacation: Union[str, bool] = False
    automate: Union[str, bool] = False
    recipient: Optional[List[str]] = None
    indoor_sensor_temp: Optional[str] = None
    target_indoor_input: Optional[str] = None
    target_indoor_temp: Optional[float] = None
    target_heater_input: Optional[str] = None
    target_heater_temp: Optional[float] = None
    window_temp: Optional[str] = None
    window_offset: Optional[float] = None
    save_temp_offset: Optional[float] = None
    save_temp: Optional[float] = None
    vacation_temp: Optional[float] = None
    vacation_keep_off: bool = False
    rain_level: Optional[float] = None
    anemometer_speed: Optional[float] = None
    getting_cold: Optional[float] = 18
    priceincrease: Optional[float] = 1
    windowsensors: Optional[List[str]] = None
    daytime_savings: Optional[List[Dict[str, Any]]] = None
    temperatures: Optional[List[Dict[str, Any]]] = None
    turn_off_after: time | None = None
    turn_off_before: time | None = None
    notify_when_finished: bool = False
    start_threshold: float = 100
    stop_threshold: float = 15
    start_duration: int = 30
    stop_duration: int = 30
    turn_back_on_after: int = 60

    ConsumptionData: Dict[int, Dict[int, TempConsumption]] = Field(default_factory=dict)
    prev_consumption: float = 0.0
    time_to_save: List[PeakHour] = Field(default_factory=list)

    def sort_temperatures(self) -> None:
        """Sort the `temperatures` list by the `out` key. """
        if not self.temperatures:
            return
        self.temperatures.sort(key=lambda d: d.get('out', float('-inf')))

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
    battery_size: float = 100
    pref_charge_limit: float = 100
    charge_below_price:float = 0
    priority: int = 3
    finishByHour: Union[str, int] = 7
    charge_now: Union[str, bool] = False
    charge_only_on_solar: Union[str, bool] = False
    departure: str | None = None
    battery_reg_counter: int = 0
    car_limit_max_ampere: float | None = None
    max_kWh_charged: float = 5
    current_charge_limit: float = 100
    old_charge_limit: float = 100
    kWh_remain_to_charge: float = -2
    connected_charger_id: str | None = None

class ChargingQueueItem(BaseModel):
    vehicle_id: str
    kWhRemaining: float
    maxAmps: int
    voltPhase: int
    finish_by_hour: int
    priority: int
    estHourCharge: float
    name: str
    charge_below: float = 0
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


class PersistenceData(BaseModel):
    max_usage: MaxUsage = Field(alias="MaxUsage", default_factory=MaxUsage)
    high_consumption: HighConsumptionHour = Field(alias="HighConsumptionHour", default_factory=HighConsumptionHour)
    idle_usage: IdleBlock = Field(alias="IdleUsage", default_factory=IdleBlock)
    charger: Dict[str, ChargerData] = Field(alias="charger", default_factory=dict)
    car: Dict[str, CarData] = Field(alias="carName", default_factory=dict)
    heater: Dict[str, HeaterBlock] = Field(alias="heater", default_factory=dict)
    chargingQueue: List[ChargingQueueItem] = Field(alias="chargingQueue", default_factory=list)
    queueChargingList: List[Any] = Field(alias="queueChargingList", default_factory=list)
    solarChargingList: List[Any] = Field(alias="solarChargingList", default_factory=list)
    # WattSlot is a plain dataclass; pydantic 2 serialises it field-by-field, which is
    # the same {"start", "end", "available_Wh"} shape the old json_encoders lambda produced.
    available_watt: List[WattSlot] = Field(alias="available_watt", default_factory=list)
    weather: WeatherData = Field(default_factory=WeatherData)

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": False,
    }

    def has_initialized_consuming_objects(self) -> bool:
        """ Return  ``True`` if at least one collection is non empty. """
        return bool(self.car) or bool(self.charger) or bool(self.heater)

def _json_path(path: str) -> Path:
    return Path(path).expanduser()

def _log(log: LogFunc | None, message: str, level: str) -> None:
    if log is not None:
        log(message, level)

def load_persistence(path: str, log: LogFunc | None = None) -> PersistenceData:
    """Load a JSON file into a typed PersistenceData instance.

    Missing file: a fresh empty file is written. Unreadable/invalid file: it is moved aside
    to ``<name>.corrupt-<epoch>.json`` (so nothing is lost), the error is logged and the app
    starts with empty PersistenceData.
    """
    file_path = _json_path(path)
    try:
        raw = file_path.read_text()
    except FileNotFoundError:
        persistence = PersistenceData()
        dump_persistence(path, persistence)
        return persistence

    try:
        # ValidationError and json.JSONDecodeError are both ValueError subclasses.
        return PersistenceData.model_validate_json(raw)
    except (ValidationError, ValueError) as e:
        corrupt_path = file_path.with_name(f"{file_path.stem}.corrupt-{int(time_module.time())}.json")
        try:
            os.replace(file_path, corrupt_path)
        except OSError as rename_error:
            _log(log, f"Could not move corrupt persistence file {file_path} aside: {rename_error}", 'ERROR')
        _log(
            log,
            f"Persistence file {file_path} could not be read and was moved to {corrupt_path}. "
            f"Starting with empty data. Error: {e}",
            'ERROR'
        )
        persistence = PersistenceData()
        dump_persistence(path, persistence)
        return persistence

def dump_persistence(path: str, data: PersistenceData) -> None:
    """Write the PersistenceData back to JSON atomically (tmp file + os.replace)."""
    file_path = _json_path(path)
    tmp_path = file_path.with_name(file_path.name + '.tmp')
    with open(tmp_path, 'w') as f:
        f.write(data.model_dump_json(exclude_none=True, by_alias=True, indent=4))
    os.replace(tmp_path, file_path)
