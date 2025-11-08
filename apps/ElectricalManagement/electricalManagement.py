""" ElectricalManagement.

    @Pythm / https://github.com/Pythm
"""

from __future__ import annotations
from appdaemon import adbase as ad

import math
import json
import csv

import bisect
import pytz
from datetime import timedelta
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Tuple, Iterable, Optional
from pydantic import BaseModel, Field

from pydantic_models import (
    PersistenceData,
    load_persistence,
    dump_persistence,
    ChargerData,
    CarData,
    HeaterBlock,
    IdleBlock,
    MaxUsage,
    TempConsumption,
    ChargingQueueItem,
    WattSlot,
    Decision,
    PeakHour
)
from utils import (
    cancel_timer_handler,
    cancel_listen_handler,
    get_next_runtime_aware,
    get_consumption_for_outside_temp,
    closest_value,
    closest_temp_in_dict,
    diff_ok,
    floor_even
)
from registry import Registry
from scheduler import Scheduler
from electrical_cars import Car, Tesla_car
from electrical_chargers import Charger, Tesla_charger, Easee
from electrical_heater import Heater, Climate, On_off_switch

__version__ = "1.0.0_beta"

MAX_TEMP_DIFFERENCE = 5
MAX_CONSUMPTION_RATIO_DIFFERENCE = 3

# Translations from json for 'MODE_CHANGE' events
FIRE_TRANSLATE:str = 'fire'
FALSE_ALARM_TRANSLATE:str = 'false-alarm'

UNAVAIL = ('unavailable', 'unknown')

class ElectricalUsage(ad.ADBase):
    """ Main class of ElectricalManagement

        @Pythm / https://github.com/Pythm
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ElectricalUsage, cls).__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ElectricalUsage()
        return cls._instance

    def initialize(self):
        self._setup_api_and_translations()
        self._init_collections()
        self._setup_notify_app()
        self._setup_electricity_price()
        
        self._validate_current_consumption_sensor()
        self._validate_accumulated_consumption_current_hour()
        self._setup_power_production_sensors()

        self.json_path = self.args.get('json_path')
        if not self.json_path:
            self.ADapi.log(
                "Path to store json not provided. "
                "Please input a valid path with configuration 'json_path' to use persistency.",
                level = 'WARNING'
            )

        self._load_persistent_data()

        self.charging_scheduler = Scheduler(
            api = self.ADapi,
            stopAtPriceIncrease = self.args.get('stopAtPriceIncrease', 0.3),
            startBeforePrice = self.args.get('startBeforePrice', 0.01),
            infotext = self.args.get('infotext', None),
            namespace = self.HASS_namespace,
            electricalPriceApp = self.electricalPriceApp,
            notify_app = self.notify_app,
            recipients = self.recipients,
            chargingQueue = self._persistence.chargingQueue,
            available_watt = self._persistence.available_watt,
        )

        self.away_state = self._get_vacation_state()
        self.automate = self.args.get('automate', True) # Default Automate switch for all heaters
        self._setup_weather_sensors()

        # --------------------------------------------------------------------------- #
        # Setup cars and chargers
        # --------------------------------------------------------------------------- #

        CAR_SPECS: List[Tuple[str, str, str]] = [
            ("charger_sensor",           "binary_sensor", "_charger"),
            ("charge_limit",             "number",        "_charge_limit"),
            ("asleep_sensor",            "binary_sensor", "_asleep"),
            ("online_sensor",            "binary_sensor", "_online"),
            ("battery_sensor",           "sensor",        "_battery"),
            ("location_tracker",         "device_tracker","_location_tracker"),
            ("destination_location_tracker", "device_tracker","_destination_location_tracker"),
            ("arrival_time",             "sensor",        "_arrival_time"),
            ("software_update",          "update",        "_software_update"),
            ("force_data_update",        "button",        "_force_data_update"),
            ("polling_switch",           "switch",        "_polling"),
            ("data_last_update_time",    "sensor",        "_data_last_update_time"),
        ]

        CHARGER_SPECS: List[Tuple[str, str, str]] = [
            ("charger_switch",           "switch",        "_charger"),
            ("charging_amps",            "number",        "_charging_amps"),
            ("charger_power",            "sensor",        "_charger_power"),
            ("session_energy",           "sensor",        "_energy_added"),
        ]

        EASEE_SPECS: List[Tuple[str, str, str]] = [
            ("charger_sensor",          "sensor",   "_status"),
            ("reason_for_no_current",   "sensor",   "_reason_for_no_current"),
            ("charging_amps",           "sensor",   "_current"),
            ("charger_power",           "sensor",   "_power"),
            ("session_energy",          "sensor",   "_energy_added"),
            ("voltage",                 "sensor",   "_voltage"),
            ("max_charger_limit",       "sensor",   "_max_charger_limit"),
            ("idle_current",            "sensor",   "_idle_current"),
        ]

        def merge_config_with_persistent(
            cfg: dict,
            name: str,
            specs: List[tuple[str, str, str]],
            persistent_data,
        ) -> None:

            namespace = cfg.get("namespace", self.HASS_namespace)

            for key, domain, suffix in specs:
                value = str(f"{domain}.{name}{suffix}")

                if key in cfg and cfg[key] is not None:
                    setattr(persistent_data, key, cfg[key])
                    continue

                elif self.ADapi.entity_exists(f"{domain}.{name}{suffix}", namespace = namespace):
                    cfg[key] = str(f"{domain}.{name}{suffix}")
                    setattr(persistent_data, key, cfg[key])
                else:
                    self.ADapi.log(
                        f"Could not automatically find {key} when setting up {name} in "
                        f"{namespace} namespace. Please update your configuration with the missing sensor.",
                        level = 'INFO'
                    )

        def _update_persistence_from_cfg(cfg: dict, persistent_data) -> None:
            if persistent_data:
                common_keys = [
                    'battery_size', 'pref_charge_limit', 'priority',
                    'finish_by_hour', 'charge_now', 'charge_only_on_solar',
                    'departure'
                ]
                for key in common_keys:
                    value = getattr(persistent_data, key, None)
                    if key in cfg and cfg[key] is not None:
                        if value != cfg[key]:
                            setattr(persistent_data, key, cfg[key])
                        continue
        
        for cfg in self.args.get('tesla', []):
            namespace = cfg.get('namespace', self.HASS_namespace)

            carName = cfg.get('charger') or cfg.get('car')
            if 'charger_sensor' in cfg and not carName:
                sensor_id = cfg['charger_sensor']
                carName = sensor_id.replace('binary_sensor.', '').replace('_charger', '')

            persisted_car = self._persistence.car.get(carName)
            if not persisted_car:
                defaults: dict[str, Any] = {
                    'charger_sensor':              cfg.get('charger_sensor', None),
                    'charge_limit':                cfg.get('charge_limit', None),
                    'battery_sensor':              cfg.get('battery_sensor', None),
                    'asleep_sensor':               cfg.get('asleep_sensor', None),
                    'online_sensor':               cfg.get('online_sensor', None),
                    'location_tracker':            cfg.get('location_tracker', None),
                    'destination_location_tracker':cfg.get('destination_location_tracker', None),
                    'arrival_time':                cfg.get('arrival_time', None),
                    'software_update':             cfg.get('software_update', None),
                    'force_data_update':           cfg.get('force_data_update', None),
                    'polling_switch':              cfg.get('polling_switch', None),
                    'data_last_update_time':       cfg.get('data_last_update_time', None),
                    'battery_size':                cfg.get('battery_size', 100),
                    'pref_charge_limit':           cfg.get('pref_charge_limit', 90),
                    'priority':                    cfg.get('priority', 3),
                    'finish_by_hour':              cfg.get('finish_by_hour', 7),
                    'charge_now':                  cfg.get('charge_now', False),
                    'charge_only_on_solar':        cfg.get('charge_only_on_solar', False),
                    'departure':                   cfg.get('departure', None),
                    'battery_reg_counter':  0,
                    'car_limit_max_ampere': None,
                    'max_kWh_charged':      5,
                    'current_charge_limit': 100,
                    'old_charge_limit':     100,
                    'kWh_remain_to_charge': -2,
                    'connected_charger_id': None,
                }
                cfg.update({k: v for k, v in defaults.items() if k not in cfg})
                self._persistence.car[carName] = CarData(**cfg)

            merge_config_with_persistent(cfg = cfg,
                                         name = carName,
                                         specs = CAR_SPECS,
                                         persistent_data = self._persistence.car[carName])

            _update_persistence_from_cfg(cfg = cfg,
                                         persistent_data = self._persistence.car[carName])

            tesla_car = Tesla_car(
                api = self.ADapi,
                namespace = namespace,
                carName = carName,
                car_data = self._persistence.car[carName],
                charging_scheduler = self.charging_scheduler,
            )
            self.cars[tesla_car.vehicle_id] = tesla_car

            persisted_charger = self._persistence.charger.get(carName)

            if not persisted_charger:
                defaults: dict[str, Any] = {
                    'charger_sensor':        cfg.get('charger_sensor'),
                    'charger_switch':        cfg.get('charger_switch'),
                    'charging_amps':         cfg.get('charging_amps'),
                    'charger_power':         cfg.get('charger_power'),
                    'session_energy':        cfg.get('session_energy'),
                    'idle_current':          cfg.get('idle_current', False),
                    'guest':                 cfg.get('guest', False),
                    'ampereCharging':        0.0,
                    'min_ampere':            6,
                    'maxChargerAmpere':      0,
                    'volts':                 220,
                    'phases':                1,
                    'voltPhase':             220,
                }
                cfg.update({k: v for k, v in defaults.items() if k not in cfg})
                self._persistence.charger[carName] = ChargerData(**cfg)

            merge_config_with_persistent(cfg = cfg,
                                         name = carName,
                                         specs = CHARGER_SPECS,
                                         persistent_data = self._persistence.charger.get(carName))

            tesla_charger = Tesla_charger(
                api = self.ADapi,
                Car = tesla_car,
                namespace = namespace,
                charger = carName,
                charger_data = self._persistence.charger[carName],
                charging_scheduler = self.charging_scheduler,
                notify_app = self.notify_app,
                recipients = self.recipients,
            )
            self.chargers[tesla_charger.charger_id] = tesla_charger

        for cfg in self.args.get('cars', []):
            namespace = cfg.get("namespace", self.HASS_namespace)
            if not 'carName' in cfg:
                self.ADapi.log(f"Skipping car entry {cfg} – no carName given", level='WARNING')
                continue

            persisted_car = self._persistence.car.get(cfg['carName'])
            if not persisted_car:
                defaults: dict[str, Any] = {
                    'charger_sensor':              cfg.get('charger_sensor', None),
                    'charge_limit':                cfg.get('charge_limit', None),
                    'battery_sensor':              cfg.get('battery_sensor', None),
                    'asleep_sensor':               cfg.get('asleep_sensor', None),
                    'online_sensor':               cfg.get('online_sensor', None),
                    'location_tracker':            cfg.get('location_tracker', None),
                    'destination_location_tracker':cfg.get('destination_location_tracker', None),
                    'arrival_time':                cfg.get('arrival_time', None),
                    'software_update':             cfg.get('software_update', None),
                    'force_data_update':           cfg.get('force_data_update', None),
                    'polling_switch':              cfg.get('polling_switch', None),
                    'data_last_update_time':       cfg.get('data_last_update_time', None),
                    'battery_size':                cfg.get('battery_size', 100),
                    'pref_charge_limit':           cfg.get('pref_charge_limit', 90),
                    'priority':                    cfg.get('priority', 3),
                    'finish_by_hour':              cfg.get('finish_by_hour', 7),
                    'charge_now':                  cfg.get('charge_now', False),
                    'charge_only_on_solar':        cfg.get('charge_only_on_solar', False),
                    'departure':                   cfg.get('departure', None),
                    'battery_reg_counter':  0,
                    'car_limit_max_ampere': None,
                    'max_kWh_charged':      5,
                    'current_charge_limit': 100,
                    'old_charge_limit':     100,
                    'kWh_remain_to_charge': -2,
                    'connected_charger_id': None,
                }
                cfg.update({k: v for k, v in defaults.items() if k not in cfg})
                self._persistence.car[cfg['carName']] = CarData(**cfg)

            car = Car(
                api = self.ADapi,
                namespace = namespace,
                carName = cfg['carName'],
                vehicle_id = cfg['carName'],
                car_data = self._persistence.car[cfg['carName']],
                charging_scheduler = self.charging_scheduler,
            )
            self.cars[cfg['carName']] = car


        for cfg in self.args.get('easee', []):
            namespace = cfg.get('namespace', self.HASS_namespace)
            charger = cfg.get('charger')
            if 'charger_status' in cfg and not charger:
                sensor_id = cfg['charger_status']
                charger = sensor_id.replace('sensor.', '').replace('_status', '')
            if 'current' in cfg and not 'charging_amps' in cfg:
                cfg['charging_amps'] = cfg['current']
            if 'status' in cfg and not 'charger_sensor' in cfg:
                cfg['charger_sensor'] = cfg['status']

            persisted_charger = self._persistence.charger.get(charger)
            if not persisted_charger:
                defaults: dict[str, Any] = {
                    'charger_sensor':        cfg.get('charger_sensor'),
                    'charger_switch':        cfg.get('charger_switch'),
                    'charging_amps':         cfg.get('charging_amps'),
                    'charger_power':         cfg.get('charger_power'),
                    'session_energy':        cfg.get('session_energy'),
                    'idle_current':          cfg.get('idle_current', False),
                    'guest':                 cfg.get('guest', False),
                    'ampereCharging':        0.0,
                    'min_ampere':            6,
                    'maxChargerAmpere':      0,
                    'volts':                 220,
                    'phases':                1,
                    'voltPhase':             220,
                    'max_charger_limit':     cfg.get('max_charger_limit'),
                    'reason_for_no_current': cfg.get('reason_for_no_current', None),
                    'voltage':               cfg.get('voltage', None),
                }
                cfg.update({k: v for k, v in defaults.items() if k not in cfg})
                self._persistence.charger[charger] = ChargerData(**cfg)

            merge_config_with_persistent(cfg = cfg,
                                         name = charger,
                                         specs = EASEE_SPECS,
                                         persistent_data = self._persistence.charger.get(charger))

            easee = Easee(
                api = self.ADapi,
                cars = self.all_cars(),
                namespace = namespace,
                charger = charger,
                charger_data = self._persistence.charger[charger],
                charging_scheduler = self.charging_scheduler,
                notify_app = self.notify_app,
                recipients = self.recipients,
            )
            self.chargers[easee.charger_id] = easee

        for car in self.all_cars():
            if car.car_data.connected_charger_id:
                charger = Registry.get_charger(car.car_data.connected_charger_id)
                if charger is not None:
                    Registry.set_link(car, charger)
            else:
                self._connect_car_and_charger(car)

        # --------------------------------------------------------------------------- #
        # Setup heaters and switches
        # --------------------------------------------------------------------------- #
        def _merge_heater_cfg(heater_cfg: dict, persisted_heater) -> bool:
            value_changed = False
            if persisted_heater:
                common_keys = [
                    'consumptionSensor', 'validConsumptionSensor', 'kWhconsumptionSensor',
                    'max_continuous_hours', 'on_for_minimum', 'pricedrop',
                    'pricedifference_increase', 'vacation', 'automate', 'recipient'
                ]
                for key in common_keys:
                    value = getattr(persisted_heater, key, None)
                    if key in heater_cfg and heater_cfg[key] is not None:
                        if value != heater_cfg[key]:
                            setattr(persisted_heater, key, heater_cfg[key])
                            value_changed = True
                        continue

            return value_changed

        def _merge_climate_cfg(heater_cfg: dict, persisted_heater) -> bool:
            value_changed = False
            if persisted_heater:
                climate_keys = [
                    'indoor_sensor_temp', 'target_indoor_input','target_indoor_temp', 'window_temp',
                    'window_offset', 'save_temp_offset', 'save_temp', 'vacation_temp',
                    'rain_level', 'anemometer_speed', 'getting_cold', 'priceincrease', 'windowsensors',
                    'daytime_savings', 'temperatures'
                ]
                for key in climate_keys:
                    value = getattr(persisted_heater, key, None)
                    if key in heater_cfg and heater_cfg[key] is not None:
                        if value != heater_cfg[key]:
                            setattr(persisted_heater, key, heater_cfg[key])
                            value_changed = True
                        continue

            return value_changed

        def _ensure_sensor(
            heater_name: str, namespace: str, suffixes: List[str]
        ) -> Optional[str]:
            """
            Return the first existing sensor id that matches one of the supplied suffixes.
            If no sensor exists, return ``None``. """

            for suffix in suffixes:
                candidate = f"sensor.{heater_name}{suffix}"
                if self.ADapi.entity_exists(candidate, namespace=namespace):
                    return candidate

            return None

        def _add_heater_missing(
            heater_cfg: dict, heater_name: str, namespace: str, is_switch: bool
        ) -> Tuple[bool, float]:

            consumption_sensor = heater_cfg.get("consumptionSensor")
            if not consumption_sensor:
                sensor_id = _ensure_sensor(
                    heater_name, namespace, ["_electric_consumption_w", "_electric_consumed_w"]
                )
                if sensor_id is None:
                    normal_power = heater_cfg.get("power", 300 if not is_switch else 1000)
                else:
                    normal_power = 0.0
                heater_cfg["consumptionSensor"] = sensor_id
                valid_consumption_sensor = sensor_id is not None
            else:
                valid_consumption_sensor = True
                normal_power = 0.0

            kwh_sensor = heater_cfg.get("kWhconsumptionSensor")
            if not kwh_sensor:
                sensor_id = _ensure_sensor(
                    heater_name, namespace, ["_electric_consumption_kwh", "_electric_consumed_kwh"]
                )
                heater_cfg["kWhconsumptionSensor"] = sensor_id

            return valid_consumption_sensor, normal_power


        for heater_cfg in self.args.get('climate', []):
            namespace = heater_cfg.get('namespace', self.HASS_namespace)
            heater_entity: str | None = heater_cfg.get('heater')
            print_save_hours = False
            if not heater_entity:
                self.ADapi.log(f"Skipping heater entry {heater_cfg}  no heater given",
                            level='WARNING')
                continue
            heater_name = heater_entity.replace('climate.', '')
            if 'options' in heater_cfg and 'print_save_hours' in heater_cfg['options']:
                print_save_hours = True

            persisted_heater = self._persistence.heater.get(heater_entity)
            if not persisted_heater:
                normal_power = 0.0
                validConsumptionSensor, normal_power = _add_heater_missing(heater_cfg, heater_name, namespace, is_switch=False)
                defaults: dict[str, Any] = {
                    'consumptionSensor':              heater_cfg['consumptionSensor'],
                    'validConsumptionSensor':         validConsumptionSensor,
                    'normal_power':                   normal_power,
                    'kWhconsumptionSensor':           heater_cfg['kWhconsumptionSensor'],
                    'max_continuous_hours':           heater_cfg.get('max_continuous_hours',2),
                    'on_for_minimum':                 heater_cfg.get('on_for_minimum',6),
                    'pricedrop':                      heater_cfg.get('pricedrop',1),
                    'pricedifference_increase':       heater_cfg.get('pricedifference_increase',1.07),
                    'vacation':                       heater_cfg.get('vacation',self.away_state),
                    'automate':                       heater_cfg.get('automate',self.automate),
                    'recipient':                      heater_cfg.get('recipient',self.recipients),
                    'indoor_sensor_temp':             heater_cfg.get('indoor_sensor_temp',None),
                    'target_indoor_input':            heater_cfg.get('target_indoor_input',None),
                    'target_indoor_temp':             heater_cfg.get('target_indoor_temp',23),
                    'window_temp':                    heater_cfg.get('window_temp',None),
                    'window_offset':                  heater_cfg.get('window_offset',-3),
                    'save_temp_offset':               heater_cfg.get('save_temp_offset',None),
                    'save_temp':                      heater_cfg.get('save_temp',None),
                    'vacation_temp':                  heater_cfg.get('vacation_temp',None),
                    'rain_level':                     heater_cfg.get('rain_level',self.rain_level),
                    'anemometer_speed':               heater_cfg.get('anemometer_speed',self.anemometer_speed),
                    'getting_cold':                   heater_cfg.get('getting_cold',18),
                    'priceincrease':                  heater_cfg.get('priceincrease',1),
                    'windowsensors':                  heater_cfg.get('windowsensors',[]),
                    'daytime_savings':                heater_cfg.get('daytime_savings',[]),
                    'temperatures':                   heater_cfg.get('temperatures',[]),
                    'ConsumptionData':                {},
                    'prev_consumption':               0,
                    'time_to_save':                   [],
                }
                for k, v in defaults.items():
                    if k not in heater_cfg:
                        heater_cfg[k] = v

                self._persistence.heater[heater_entity] = HeaterBlock(**heater_cfg)
                print_save_hours = True

            value_changed = _merge_heater_cfg(heater_cfg, persisted_heater)
            value_changed = _merge_climate_cfg(heater_cfg, persisted_heater)
            if value_changed:
                print_save_hours = True

            climate = Climate(
                api = self.ADapi,
                namespace = namespace,
                heater = heater_entity,
                heater_data = self._persistence.heater[heater_entity],
                electricalPriceApp = self.electricalPriceApp,
                charging_scheduler = self.charging_scheduler,
                notify_app = self.notify_app,
                print_save_hours = print_save_hours,
            )
            self.heaters.append(climate)


        for switch_cfg in self.args.get('heater_switches', []):
            namespace = switch_cfg.get('namespace', self.HASS_namespace)
            heater_entity: str | None = switch_cfg.get('switch')
            print_save_hours = False
            if not heater_entity:
                self.ADapi.log(f"No switch found for heater switch {switch_cfg}", level='WARNING')
                continue
            heater_name = heater_entity.replace('switch.', '')
            if 'options' in heater_cfg and 'print_save_hours' in heater_cfg['options']:
                print_save_hours = True

            persisted_heater = self._persistence.heater.get(heater_entity)
            if not persisted_heater:
                validConsumptionSensor, normal_power = _add_heater_missing(switch_cfg, heater_name, namespace, is_switch=True)
                defaults: dict[str, Any] = {
                    'consumptionSensor':              heater_cfg['consumptionSensor'],
                    'validConsumptionSensor':         validConsumptionSensor,
                    'normal_power':                   normal_power,
                    'kWhconsumptionSensor':           heater_cfg['kWhconsumptionSensor'],
                    'max_continuous_hours':           heater_cfg.get('max_continuous_hours',2),
                    'on_for_minimum':                 heater_cfg.get('on_for_minimum',6),
                    'pricedrop':                      heater_cfg.get('pricedrop',1),
                    'pricedifference_increase':       heater_cfg.get('pricedifference_increase',1.07),
                    'vacation':                       heater_cfg.get('vacation',self.away_state),
                    'automate':                       heater_cfg.get('automate',self.automate),
                    'recipient':                      heater_cfg.get('recipient',self.recipients),
                    'daytime_savings':                heater_cfg.get('daytime_savings',[]),
                    'ConsumptionData':                {},
                    'prev_consumption':               0,
                    'time_to_save':                   [],
                }
                for k, v in defaults.items():
                    if k not in switch_cfg:
                        switch_cfg[k] = v

                self._persistence.heater[heater_entity] = HeaterBlock(**switch_cfg)
                print_save_hours = True

            value_changed = _merge_heater_cfg(switch_cfg, persisted_heater)
            if value_changed:
                print_save_hours = True

            switch = On_off_switch(
                api = self.ADapi,
                namespace = namespace,
                heater = heater_entity,
                heater_data = self._persistence.heater[heater_entity],
                electricalPriceApp = self.electricalPriceApp,
                charging_scheduler = self.charging_scheduler,
                notify_app = self.notify_app,
                print_save_hours = print_save_hours,
            )
            self.heaters.append(switch)
        
        self._refresh_heaters()
        self.ADapi.run_in(self._create_runners, 60)
        self.ADapi.run_in(self._get_new_prices, 60)


    def _setup_api_and_translations(self):
        self.ADapi = self.get_ad_api()
        self.HASS_namespace = self.args.get('main_namespace', 'default')
        language = self.args.get('lightwand_language', 'en')
        language_file = self.args.get('language_file', '/conf/apps/Lightwand/translations.json')
        event_listen_str: str = 'MODE_CHANGE'

        try:
            with open(language_file) as lang:
                translations = json.load(lang)
            event_listen_str = translations[language]['MODE_CHANGE']
            global FIRE_TRANSLATE
            FIRE_TRANSLATE = translations[language]['fire']
            global FALSE_ALARM_TRANSLATE
            FALSE_ALARM_TRANSLATE = translations[language]['false-alarm']
        except FileNotFoundError:
            self.ADapi.log("Translation file not found. Will use default mode names", level = 'DEBUG')

        self.ADapi.listen_event(self.mode_event, event_listen_str, namespace = self.HASS_namespace)
        self.ADapi.listen_event(self._notify_event, "mobile_app_notification_action", namespace=self.HASS_namespace)

    def _init_collections(self):
        self.chargers: dict[str, Charger] = {}
        self.cars: dict[str, Car] = {}
        self.appliances: list = []
        self.heaters: list = []

        self.heatersRedusedConsumption:list = []
        self.lastTimeHeaterWasReduced = self.ADapi.datetime(aware = True) - timedelta(minutes = 5)

        self.notify_overconsumption: bool = 'notify_overconsumption' in self.args.get('options')
        self.pause_charging: bool = 'pause_charging' in self.args.get('options')

        self.buffer = self.args.get('buffer', 0.4) + 0.02
        self.max_kwh_goal = self.args.get('max_kwh_goal', 15)

        # Variables for different calculations
        self.accumulated_unavailable:int = 0
        self.last_accumulated_kWh:float = 0
        self.accumulated_kWh_wasUnavailable:bool = False
        self.solar_producing_change_to_zero:bool = False
        self.notify_about_overconsumption:bool = False
        self.totalWattAllHeaters:float = 0
        self.houseIsOnFire:bool = False
        self.find_next_charger_counter:int = 0

        self.checkIdleConsumption_Handler = None

    def _setup_notify_app(self):
        name_of_notify_app = self.args.get('notify_app', None)
        self.recipients = self.args.get('notify_receiver', [])
        if name_of_notify_app is not None:
            self.notify_app = self.ADapi.get_app(name_of_notify_app)
        else:
            self.notify_app = Notify_Mobiles(self.ADapi, self.HASS_namespace)

    def _setup_electricity_price(self):
        if 'electricalPriceApp' in self.args:
            self.electricalPriceApp = self.ADapi.get_app(self.args['electricalPriceApp'])
        else:
            raise Exception(
                "\nFrom version 1.0.0 the electrical price calculations have been moved to it's own repository.\n"
                "This can be found here: https://github.com/Pythm/ElectricalPriceCalc \n"
                "Please add the app and configure with 'electricalPriceApp'. Check out readme for more info.\n"
                "Aborting Electrical Usage setup."
            )

    def _validate_current_consumption_sensor(self):
        self.current_consumption_sensor = self.args.get('power_consumption', None) # In Watt
        if not self.current_consumption_sensor:
            self.ADapi.log(
                "'power_consumption' sensor not provided in configuration. Aborting Electrical Usage setup."
                "Please provide a watt power consumption sensor to use this function"
                "Set up Tibber Pulse or equivalent and configure a watt power consumption sensor.\n"
                "ElectricalUsage will not adjust electricity consumption",
                level='INFO'
            )
        try:
            self.current_consumption = float(self.ADapi.get_state(self.current_consumption_sensor))
        except (ValueError, TypeError) as ve:
            if self.ADapi.get_state(self.current_consumption_sensor) in UNAVAIL:
                self.ADapi.log(f"Current consumption is unavailable at startup", level = 'DEBUG')
            else:
                self.ADapi.log(
                    "power_consumption sensor is not a number on app initialization. ",
                    level='INFO'
                )
            self.ADapi.log(ve, level = 'DEBUG')

    def _validate_accumulated_consumption_current_hour(self):
        self.accumulated_consumption_current_hour = self.args.get('accumulated_consumption_current_hour', None)
        if self.accumulated_consumption_current_hour is None:
            self.ADapi.log(
                "'accumulated_consumption_current_hour' not provided in configuration. "
                "Set up Tibber Pulse or equivalent and configure a kWh consumption for current hour.\n"
                "ElectricalUsage will not adjust electricity consumption",
                level='INFO'
            )
            return

        attr_last_updated = self.ADapi.get_state(
            entity_id = self.accumulated_consumption_current_hour,
            attribute = "last_updated"
        )
        if not attr_last_updated:
            self.ADapi.log(
                f"{self.ADapi.get_state(self.accumulated_consumption_current_hour)} has no 'last_updated' attribute. Function might fail",
                level='INFO'
            )

    def _setup_power_production_sensors(self):
        self.current_production_sensor = self.args.get('power_production', None)  # Watt
        self.accumulated_production_current_hour = self.args.get('accumulated_production_current_hour', None)  # kWh

    def _load_persistent_data(self):
        self._persistence: PersistenceData = load_persistence(self.json_path)

        if self._persistence.max_usage.max_kwh_usage_pr_hour == 0:
            self._persistence.max_usage.max_kwh_usage_pr_hour = self.max_kwh_goal

    def _get_vacation_state(self):
        away_state = self.args.get('away_state') or self.args.get('vacation')
        if not away_state and self.ADapi.entity_exists('input_boolean.vacation', namespace = self.HASS_namespace):
            away_state = 'input_boolean.vacation'

        # Set up listener for state changes
        if away_state:
            self.ADapi.listen_state(self._awayStateListen_Main, away_state,
                namespace=self.HASS_namespace)
            return self.ADapi.get_state(away_state, namespace = self.HASS_namespace)  == 'on'

        return False

    def _setup_weather_sensors(self):
        self.out_temp:float = 10
        self.ADapi.listen_event(self.weather_event, 'WEATHER_CHANGE', namespace=self.HASS_namespace)

    def _create_runners(self, kwargs):
        """ Schedule check for charging, electricity usage and electricity price. """

        now = self.ADapi.datetime(aware = True)
        
        if self.current_consumption_sensor is not None and self.accumulated_consumption_current_hour is not None:
            runtime = get_next_runtime_aware(startTime = now, offset_seconds = 0, delta_in_seconds = 60)
            self.ADapi.run_every(self.checkElectricalUsage, runtime, 60)
        else:
            self.available_Wh = 10000 # Set a trick fixed value since sensors are missing.
            runtime = get_next_runtime_aware(startTime = now, offset_seconds = 0, delta_in_seconds = 600)
            self.ADapi.run_every(self.checkChargingQueue, runtime, 600)

        self.ADapi.run_daily(self.dump_persistence_file, "14:30:00")
        self.ADapi.run_daily(self._get_new_prices, "00:03:00")
        self.ADapi.run_daily(self._get_new_prices, "13:01:00")


        item = self.electricalPriceApp.elpricestoday[0]
        duration = (item.end - item.start).total_seconds()
        runtime_switch = get_next_runtime_aware(startTime = now, offset_seconds = 1, delta_in_seconds = duration)
        interval = min(duration, 900)
        runtime_climate = get_next_runtime_aware(startTime = now, offset_seconds = 1, delta_in_seconds = interval)

        for heater in self.heaters:
            if isinstance(heater, Climate):
                self.ADapi.run_every(heater.heater_setNewValues, runtime_climate, interval)
            else:
                self.ADapi.run_every(heater.heater_setNewValues, runtime_switch, duration)

    # Finished initialization.

    def terminate(self) -> None:
        """ Writes charger and car data to persisten storage before terminating app """

        if hasattr(self, "_persistence"):
            dump_persistence(self.json_path, self._persistence)

    def dump_persistence_file(self, kwargs) -> None:
        """ Writes charger and car data to persisten storage daily """

        if hasattr(self, "_persistence"):
            dump_persistence(self.json_path, self._persistence)

    def all_cars(self) -> Iterable[Car]:
        """ Returns iterable car list """

        return self.cars.values()

    def all_cars_connected(self) -> Iterable[Car]:
        """ Yield only cars that are actually connected and have a charger """

        return (
            car
            for car in self.cars.values()
            if car.isConnected() and car.connected_charger is not None
    )

    def all_chargers(self) -> Iterable[Charger]:
        """ Returns iterable charger list """

        return self.chargers.values()

    def _connect_car_and_charger(self, car) -> None:
        """ Finds charger that car is connected to """

        if car.isConnected():
            ChargingState = car.getCarChargerState()
            if ChargingState == 'NoPower':
                for charger in self.all_chargers():
                    if (
                        charger.connected_vehicle is None
                        and charger.getChargingState() in ('Stopped', 'awaiting_start')
                    ):
                        charger.findCarConnectedToCharger()

            elif ChargingState != 'Disconnected':
                Registry.set_link(car, car.onboard_charger)


    def _get_new_prices(self, kwargs) -> None:
        """ Fetches new prices and finds charge time """

        if (
            not self.electricalPriceApp.tomorrow_valid
            and self.ADapi.now_is_between('12:30:00', '15:30:00')
        ):
            self.ADapi.run_in(self._get_new_prices, 600)
            return # Wait until prices tomorrow is valid

        for heater in self.heaters:
            if (
                self.electricalPriceApp.tomorrow_valid # if tomorrows prices are found
                or self.ADapi.now_is_between('00:05:00', '12:50:00') # Before tomorrow prices are expected
            ):
                self.ADapi.run_in(heater.heater_getNewPrices, delay = 20, random_start = 1, random_end = 2)

        if self.electricalPriceApp.tomorrow_valid:
            for car in self.all_cars_connected():
                self.ADapi.run_in(car.findNewChargeTimeAt, 140)

        self.ADapi.run_in(self.calculateIdleConsumption, 120)
        self.ADapi.run_in(self._run_find_consumption_after_turned_back_on, 620)

        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkIdleConsumption_Handler, name = "log"):
            self.checkIdleConsumption_Handler = None

        if (
            not self.away_state
            and self.ADapi.now_is_between('00:00:00', '03:30:00')
            and not self._persistence.queueChargingList
        ):
            self.checkIdleConsumption_Handler = self.ADapi.run_at(self.logIdleConsumption, "04:30:01")

    def checkChargingQueue(self, kwargs) -> None:
        """ Handels charging start and stop when no consumption sensors is configured """

        now = self.ADapi.datetime(aware = True)
        minute = now.minute

        if minute == 0:
            self._check_charging_this_hour()

        self._check_queue_charging_list(charging_list = self._persistence.queueChargingList,
                                        check_if_charging_time = True,
                                        available_Wh = self.available_Wh)

    def checkElectricalUsage(self, kwargs) -> None:
        """ Calculate and ajust consumption to stay within kWh limit.
            Start and stops charging when time to charge """

        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        remaining_minute = 60 - minute

        self._get_current_consumption()
        self._get_accumulated_kWh()

        if minute == 0:
            self._reset_hourly(now)
            return
        elif minute == 59 and self.accumulated_kWh > self._persistence.max_usage.max_kwh_usage_pr_hour -1:
            if not self.charging_scheduler.isChargingTime() and not self._persistence.queueChargingList:
                if now.hour not in self._persistence.high_consumption.high_consumption_hours:
                    self._persistence.high_consumption.high_consumption_hours.append(now.hour)

        self.current_production = self._get_sensor_value(self.current_production_sensor)
        self.production_kWh = self._get_sensor_value(self.accumulated_production_current_hour)

        self.max_target_kWh_buffer = self._calc_max_target_kWh_buffer(now)
        self.projected_kWh_usage = self._calc_projected_kWh_usage(now)
        self.available_Wh = self._calc_available_Wh(now)

        if now.hour in self._persistence.high_consumption.high_consumption_hours:
            if minute < 50:
                self.available_Wh -= remaining_minute * 50
                self.max_target_kWh_buffer -= 1 / remaining_minute

        self._dispatch_decision()

    def _cond_over_target(self) -> bool:
        return (
            self.projected_kWh_usage + self.accumulated_kWh >
            self._persistence.max_usage.max_kwh_usage_pr_hour - self.buffer
            or self.max_target_kWh_buffer < 0
        )

    def _cond_heaters_reduced(self) -> bool:
        return self.heatersRedusedConsumption

    def _cond_prod_gt_cons(self) -> bool:
        return self.accumulated_kWh <= self.production_kWh and self.projected_kWh_usage < 0

    def _cond_cons_gt_prod_solar_off(self) -> bool:
        return (
            (self.accumulated_kWh > self.production_kWh or self.projected_kWh_usage > 0)
            and self.solar_producing_change_to_zero
        )

    def _cond_under_target(self) -> bool:
        return (
            self.projected_kWh_usage + self.accumulated_kWh <
            self._persistence.max_usage.max_kwh_usage_pr_hour - self.buffer
            and self.max_target_kWh_buffer > 0
            and not self.houseIsOnFire
        )

    def _dispatch_decision(self) -> None:
        for dec in self._build_decision_table():
            if dec.predicate():
                dec.action()
                break

    def _build_decision_table(self) -> list[Decision]:
        return [
            Decision("over_target",            self._cond_over_target,            self._act_over_target),
            Decision("heaters_reduced",        self._cond_heaters_reduced,        self._act_heaters_reduced),
            Decision("prod_gt_cons",           self._cond_prod_gt_cons,           self._act_prod_gt_cons),
            Decision("cons_gt_prod_solar_off", self._cond_cons_gt_prod_solar_off, self._act_cons_gt_prod_solar_off),
            Decision("under_target",           self._cond_under_target,           self._act_under_target),
        ]


    def _act_over_target(self) -> None:
        """ Current consuption is on it's way to go over max kWh usage pr hour. Redusing electricity usage """

        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        remaining_minute = 60 - minute
        reduce_Wh:float = 0.0

        if (
            self.available_Wh > -800
            and remaining_minute > 15
            and not self.heatersRedusedConsumption
        ):
            return

        if self._update_ChargingQueue(charging_list = self._persistence.queueChargingList):
            reduce_Wh, self.available_Wh = self._get_heaters_reduced_previous_consumption(avail = self.available_Wh)

            if reduce_Wh + self.available_Wh < 0:
                reduce_Wh, self.available_Wh = self._reduce_charging_ampere(reduce_Wh = reduce_Wh,
                                                                            available_Wh = self.available_Wh,
                                                                            charging_list = self._persistence.queueChargingList)

        if reduce_Wh + self.available_Wh > 0:
            return

        if minute > 7 or not self._persistence.queueChargingList:
            self._reduce_heating()

        if (
            (self._persistence.max_usage.max_kwh_usage_pr_hour
            + (self.max_target_kWh_buffer * (60 / remaining_minute)))*1000
            - self.current_consumption
            < -100
            and now - self.lastTimeHeaterWasReduced > timedelta(minutes = 3)
            and remaining_minute <= 40
            and self.available_Wh < -200
        ):
            if self.pause_charging:
                if self._stop_chargers_due_to_overconsumption():
                    return

            if self.notify_overconsumption:
                self._notify_overconsumption()

    def _act_heaters_reduced(self) -> None:
        """ Reduce charging speed to turn heaters back on """

        self.notify_about_overconsumption = False

        reduce_Wh, self.available_Wh = self._get_heaters_reduced_previous_consumption(avail = self.available_Wh)
        
        if (
            self._update_ChargingQueue(charging_list = self._persistence.queueChargingList)
            and reduce_Wh + self.available_Wh < 0
        ):
            reduce_Wh, self.available_Wh = self._reduce_charging_ampere(reduce_Wh = reduce_Wh,
                                                                        available_Wh = self.available_Wh,
                                                                        charging_list = self._persistence.queueChargingList)

    def _act_prod_gt_cons(self) -> None:
        """ Production is higher than consumption """

        # TODO: Not tested with actual data.
        self.notify_about_overconsumption = False
        self.solar_producing_change_to_zero = True

        overproduction_Wh:float = self.current_production - self.current_consumption
        # Check if any heater is reduced
        if self.heatersRedusedConsumption:
            reduce_Wh, overproduction_Wh = self._get_heaters_reduced_previous_consumption(avail = overproduction_Wh)
        for heater in self.heaters:
            if heater.isSaveState:
                heater.removeSaveState()
                overproduction_Wh -= heater.heater_data.prev_consumption
                if overproduction_Wh > -100:
                    return

        if self._persistence.queueChargingList:
            success = self._check_queue_charging_list(charging_list = self._persistence.queueChargingList,
                                            check_if_charging_time = True,
                                            available_Wh = self.available_Wh + overproduction_Wh)
            if success:
                return
        else:
            success = self._check_queue_charging_list(charging_list = self._persistence.solarChargingList,
                                            check_if_charging_time = False,
                                            available_Wh = overproduction_Wh)
            if success:
                return

        if len(self.charging_scheduler.chargingQueue) == 0:
            # Check if any car has charging limit below preferred limit
            for car in self.all_cars_connected():
                if car.car_data.pref_charge_limit > car.car_data.current_charge_limit:
                    car.changeChargeLimit(car.car_data.pref_charge_limit)
                    self._persistence.solarChargingList.append(car.vehicle_id)
                    car.charging_on_solar = True
                    return

        # Set spend in heaters
        for heater in self.heaters:
            heater.heater_data.prev_consumption, valid_consumption = heater.get_heater_consumption()
            if (
                heater.heater_data.prev_consumption < 100
                and not heater.increase_now
                and heater.heater_data.normal_power < overproduction_Wh
            ):
                heater.setIncreaseState()
                overproduction_Wh -= heater.heater_data.normal_power
            if overproduction_Wh < 100:
                return

    def _act_cons_gt_prod_solar_off(self) -> None:
        """ Consumption is higher than production """

        # TODO: Not tested with actual data.
        self.notify_about_overconsumption = False

        overproduction_Wh:float = self.current_production - self.current_consumption

        # Remove spend in heaters
        for heater in self.heaters:
            if overproduction_Wh > 0:
                return
            if heater.increase_now:
                heater.setPreviousState()
                overproduction_Wh += heater.heater_data.normal_power

        overproduction_Wh, available_Wh = self._reduce_charging_ampere(reduce_Wh = overproduction_Wh,
                                                         available_Wh = 0,
                                                         charging_list = self._persistence.solarChargingList)
        if overproduction_Wh > -300:
            # production is to low -> stop and reset.
            to_remove = set()
            for queue_id in reversed(self._persistence.solarChargingList):
                car = Registry.get_car(queue_id)
                if car is None or car.connected_charger is None:
                    continue

                if car.connected_charger.getChargingState() == "Charging":
                    overproduction_Wh += (
                        car.connected_charger.charger_data.ampereCharging * car.connected_charger.charger_data.voltPhase
                    )
                    car.charging_on_solar = False
                    car.changeChargeLimit(car.car_data.old_charge_limit)
                    car.stopCharging()
                    to_remove.add(queue_id)

                if overproduction_Wh > 0:
                    break

            self._persistence.solarChargingList = [
                qid for qid in self._persistence.solarChargingList
                if qid not in to_remove
            ]

        if not self._persistence.solarChargingList:
            self.solar_producing_change_to_zero = False

    def _act_under_target(self) -> None:
        """ Consumption is below max target """
        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        remaining_minute = 60 - minute

        # Increase charging speed or add another charger if time to charge
        self.notify_about_overconsumption = False
        if (
            (remaining_minute > 9 and self.available_Wh < 800)
            or self.max_target_kWh_buffer < 0.1
            or now - self.lastTimeHeaterWasReduced < timedelta(minutes = 10)
        ):
            return
        self._check_queue_charging_list(charging_list = self._persistence.queueChargingList,
                                        check_if_charging_time = True,
                                        available_Wh = self.available_Wh)

    def _check_queue_charging_list(self, charging_list, check_if_charging_time, available_Wh) -> bool:
        """ Updates queueChargingList and increases chargingspeed """

        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        remaining_minute = 60 - minute

        next_vehicle_id = False
        if self._update_ChargingQueue():
            to_remove = set()
            for queue_id in self._persistence.queueChargingList:
                car = self.get_car(queue_id)
                if car is not None:
                    if car.connectedCharger is not None:
                        ChargingState = car.getCarChargerState()
                        if ChargingState in ('Complete', 'Disconnected'):
                            to_remove.add(queue_id)
                            CHARGE_SCHEDULER.removeFromCharging(car.vehicle_id)
                            car.connectedCharger._CleanUpWhenChargingStopped()
                            if (
                                len(self._persistence.queueChargingList) <= 1 and
                                not self.away_state and
                                self.ADapi.now_is_between('01:00:00', '05:00:00')
                            ):
                                if CHARGE_SCHEDULER.findNextChargerToStart() is None:
                                    if self.ADapi.now_is_between('01:00:00', '04:00:00'):
                                        self.checkIdleConsumption_Handler = self.ADapi.run_at(self.logIdleConsumption, "04:30:01")
                                    else:
                                        self.ADapi.run_in(self.logIdleConsumption, 30)
                                elif self._should_start_next_charging(vehicle_id = car.vehicle_id):
                                    next_vehicle_id = True

                        elif ChargingState in ('Stopped', 'awaiting_start'):
                            
                            if CHARGE_SCHEDULER.isChargingTime(vehicle_id = car.vehicle_id) and self.available_Wh > 1300:
                                self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                                        remaining_minute = remaining_minute)
                                                                        
                                return
                            elif not car.dontStopMeNow():
                                to_remove.add(queue_id)
                                CHARGE_SCHEDULER.removeFromCharging(car.vehicle_id)

                        elif ChargingState == 'Charging':
                            if (len(CHARGE_SCHEDULER.chargingQueue) > len(self._persistence.queueChargingList) and
                                self._should_start_next_charging(vehicle_id = car.vehicle_id)
                            ):
                                next_vehicle_id = True
                            else:
                                next_vehicle_id = False

                                if not car.isChargingAtMaxAmps():
                                    AmpereToIncrease = math.floor(self.available_Wh / car.connectedCharger.charger_data.voltPhase)
                                    car.connectedCharger.changeChargingAmps(charging_amp_change = AmpereToIncrease)

                        elif ChargingState is None:
                            car.wakeMeUp()
                            self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                                    remaining_minute = remaining_minute)
                            return
                        elif (
                            car.connectedCharger is not car.onboardCharger
                            and ChargingState == 'NoPower'
                        ):
                            if car.connectedCharger.getChargingState() != 'Charging':
                                self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                                        remaining_minute = remaining_minute)
                                return
                        else:
                            if (
                                charger.connected_vehicle is None
                                and charger.getChargingState() in ('Stopped', 'awaiting_start')
                            ):
                                for charger in self.chargers:
                                    if (
                                        charger.Car is None
                                        and charger.getChargingState() in ('Stopped', 'awaiting_start')
                                    ):
                                        car.connectedCharger = None
                                        charger.findCarConnectedToCharger()

                    else:
                        if not car.isConnected():
                            to_remove.add(queue_id)
                            CHARGE_SCHEDULER.removeFromCharging(car.vehicle_id)
                            car._handleChargeCompletion()
                        else:
                            car.connectedCharger = car.onboardCharger
            self._persistence.queueChargingList = [
                qid for qid in self._persistence.queueChargingList
                if qid not in to_remove
            ]

        self.find_next_charger_counter += 1
        if next_vehicle_id or self.find_next_charger_counter > 5 and not charging_list:
            self._update_ChargingQueue(charging_list = charging_list)
            self.find_next_charger_counter = 0
            if (
                next_vehicle_id or
                (available_Wh > 1600 and
                self.charging_scheduler.isChargingTime())
            ):
                next_vehicle_to_start = CHARGE_SCHEDULER.findNextChargerToStart()

                if next_vehicle_to_start is not None:
                    if self._checkIfPossibleToStartCharging():
                        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkIdleConsumption_Handler, name = "log"):
                            self.checkIdleConsumption_Handler = None

                        car = self.get_car(next_vehicle_to_start)
                        if car is not None:
                            if car.connectedCharger is not None:
                                if car.vehicle_id not in self._persistence.queueChargingList:
                                    self._persistence.queueChargingList.append(car.vehicle_id)
                                    self._persistence.queueChargingList = CHARGE_SCHEDULER.sort_charging_queue_by_priority(
                                                                                        self._persistence.queueChargingList)
                                    CHARGE_SCHEDULER.markAsCharging(car.vehicle_id)
                                    self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                                            remaining_minute = remaining_minute)
                                    
                                    return
                            else:
                                if not car.isConnected():
                                    car._handleChargeCompletion()
                                elif car.getCarChargerState() == 'NoPower':
                                    for charger in self.chargers:
                                        if (
                                            charger.Car is None
                                            and charger.getChargingState() in ('Stopped', 'awaiting_start')
                                        ):
                                            charger.findCarConnectedToCharger()
                                            return


    # get data for consumption calculation
    def _get_current_consumption(self) -> None:
        try:
            self.current_consumption = float(self.ADapi.get_state(self.current_consumption_sensor))
        except (TypeError, ValueError):
            self.current_consumption, heater_consumption = self.get_idle_and_heater_consumption()
            if self.current_consumption is None:
                self.current_consumption = 2000.0

            for heater in self.heaters:
                if heater.heater_data.validConsumptionSensor:
                    try:
                        self.current_consumption += float(self.ADapi.get_state(heater.heater_data.consumptionSensor,
                            namespace = heater.namespace))
                    except (TypeError, ValueError):
                        self.current_consumption += heater_consumption / len(self.heaters)
            for car in self.all_cars_connected():
                if car.getCarChargerState() == 'Charging':
                    try:
                        self.current_consumption += car.connected_charger.charger_data.ampereCharging * car.connected_charger.charger_data.voltPhase
                    except (TypeError, ValueError):
                        self.ADapi.log(
                            f"Not able to get charging info when current consumption is unavailable from {type(car.connected_charger).__name__}",
                            level = 'WARNING'
                        )

    def _get_accumulated_kWh(self) -> None:
        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        try:
            self.accumulated_kWh = float(self.ADapi.get_state(self.accumulated_consumption_current_hour))
        except (TypeError, ValueError):
            if self.accumulated_unavailable > 9:
                # Will try to reload Home Assistant integration if the sensor is unavailable for 10 minutes. 
                self.accumulated_unavailable = 0
                self.ADapi.create_task(self._reload_accumulated_consumption_sensor())
            else:
                self.accumulated_unavailable += 1

            self.accumulated_kWh = float(self.last_accumulated_kWh + (self.current_consumption/60000))
            self.last_accumulated_kWh = self.accumulated_kWh
            self.accumulated_kWh_wasUnavailable = True
        else:
            if self.accumulated_kWh_wasUnavailable:
                self.accumulated_kWh_wasUnavailable = False
                # Print out estimate during unavailable vs actual if below actual.
                if self.last_accumulated_kWh + (self.current_consumption/60000) < self.accumulated_kWh:
                    self.ADapi.log(
                        f"Accumulated kWh was unavailable. Estimated: {round(self.last_accumulated_kWh + (self.current_consumption/60000),2)}. "
                        f"Actual: {self.accumulated_kWh}",
                        level = 'INFO'
                    )
            self.last_accumulated_kWh = self.accumulated_kWh
            attr_last_updated = self.ADapi.get_state(entity_id = self.accumulated_consumption_current_hour,
                attribute = "last_updated"
            )
            if attr_last_updated:
                last_update = self.ADapi.convert_utc(attr_last_updated)
                stale_time = now - last_update
                if stale_time > timedelta(minutes = 3):
                    self.ADapi.create_task(self._reload_accumulated_consumption_sensor())

                    if minute < 2:
                        self.last_accumulated_kWh = self.accumulated_kWh = 1
                    else:
                        add_consumption = round(self.current_consumption/60000 ,2)
                        self.accumulated_kWh += add_consumption
                        self.last_accumulated_kWh += add_consumption

    async def _reload_accumulated_consumption_sensor(self) -> None:
        await self.ADapi.call_service('homeassistant/reload_config_entry',
            entity_id = self.accumulated_consumption_current_hour
        )

    def _get_sensor_value(self, sensor_id: str | None) -> float:
        if not sensor_id:
            return 0.0
        value = self.ADapi.get_state(sensor_id)
        return 0.0 if value in UNAVAIL else float(value)

    def _calc_max_target_kWh_buffer(self, now) -> float:
        minute_ratio = now.minute / 60.0
        target = (
            self._persistence.max_usage.max_kwh_usage_pr_hour - self.buffer
        ) * minute_ratio
        return target - (self.accumulated_kWh - self.production_kWh)

    def _calc_projected_kWh_usage(self, now) -> float:
        remaining_minute = 60 - now.minute
        return  ((self.current_consumption - self.current_production) / 60000.0) * remaining_minute

    def _calc_available_Wh(self, now) -> float:
        remaining_minute = 60 - now.minute
        return (
                self._persistence.max_usage.max_kwh_usage_pr_hour
                - self.buffer
                + (self.max_target_kWh_buffer * (60 / remaining_minute))
                ) * 1000 - self.current_consumption


    # Manage charging consumption

    def _find_next_charger_to_start(self, queue_list:list, check_if_charging_time:bool) -> bool:

        next_vehicle_to_start = self.charging_scheduler.findNextChargerToStart(check_if_charging_time)

        if next_vehicle_to_start is None:
            return False

        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        remaining_minute = 60 - minute

        if self._checkIfPossibleToStartCharging():
            car = Registry.get_car(next_vehicle_to_start)
            if car is None:
                return
            if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkIdleConsumption_Handler, name = "log"):
                self.checkIdleConsumption_Handler = None
            if car.connected_charger is not None:
                if car.vehicle_id not in queue_list:
                    queue_list.append(car.vehicle_id)
                    queue_list = self.charging_scheduler.sort_charging_queue_by_priority(
                                                                        queue_list)
                    self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                          remaining_minute = remaining_minute)
                    return True
            else:
                self._connect_car_and_charger(car)
        return False

    def _checkIfPossibleToStartCharging(self) -> bool:
        softwareUpdates = False
        for car in self.all_cars_connected():
            if car.SoftwareUpdates():
                softwareUpdates = True
        # Stop other chargers if a car is updating software. Might not be able to adjust chargespeed when updating.
        if softwareUpdates:
            for car in self.all_cars_connected():
                if (
                    not car.dontStopMeNow()
                    and car.getCarChargerState() == 'Charging'
                ):
                    car.stopCharging()
            return False
        return True

    def _start_charging_from_chargeQueue(self,
                                         vehicle_id:str = None, 
                                         remaining_minute:int = 1) -> None:
        if remaining_minute > 3:
            car = Registry.get_car(vehicle_id)
            if car is not None:
                car.startCharging()
                #AmpereToCharge = math.floor(self.available_Wh / car.connected_charger.charger_data.voltPhase)
                #car.connected_charger.setChargingAmps(charging_amp_set = AmpereToCharge)
                self.charging_scheduler.markAsCharging(car.vehicle_id)


    def _should_start_next_charging(self, vehicle_id:str = None) -> bool:
        """ Check if next car should also start charging """

        now = self.ADapi.datetime(aware = True)
        minute = now.minute
        remaining_minute = 60 - minute

        car = Registry.get_car(vehicle_id)
        if car is not None:
            if car.isChargingAtMaxAmps():
                return True
            if minute > 15 and remaining_minute > 12:
                amp = car.connected_charger.charger_data.ampereCharging
                threshold = max(car.getCarMaxAmps() - 12, 12)
                return amp > threshold
        return False

    def _update_ChargingQueue(self, charging_list) -> bool:
        """Add eligible cars to the charging queue and return whether the queue is non-empty """

        added = False
        for car in self.all_cars_connected():
            if (
                car.vehicle_id not in charging_list and
                (car.getCarChargerState() == 'Charging' or car.connected_charger.getChargingState() == 'Charging')
            ):
                charging_list.append(car.vehicle_id)
                added = True
                self.charging_scheduler.markAsCharging(car.vehicle_id)

        if added:
            charging_list = self.charging_scheduler.sort_charging_queue_by_priority(
                                                                    charging_list)
        return charging_list

    def _check_charging_this_hour(self):
        for car in self.all_cars_connected():
            if (
                car.getCarChargerState() == 'Charging'
                and not self.solar_producing_change_to_zero
                and not car.dontStopMeNow()
            ):
                if not self.charging_scheduler.isChargingTime(vehicle_id = car.vehicle_id):
                    car.kWhRemaining()
                    if self.charging_scheduler.isPastChargingTime(vehicle_id = car.vehicle_id):
                        if car.car_data.priority == 1 or car.car_data.priority == 2:
                            continue # Finishing charging on priority cars.
                        car.stopCharging()
                        if car.car_data.kWh_remain_to_charge > 1:
                            self.ADapi.log(
                                f"Was not able to finish charging {car.carName} with {round(car.car_data.kWh_remain_to_charge,2)} kWh remaining before prices increased. "
                                f"Consider adjusting startBeforePrice {self.charging_scheduler.startBeforePrice} and "
                                f"stopAtPriceIncrease {self.charging_scheduler.stopAtPriceIncrease} in configuration.",
                                level = 'INFO'
                            )
                            data = {
                                'tag' : 'charging' + str(car.carName),
                                'actions' : [{ 'action' : 'find_new_chargetime'+str(car.carName), 'title' : f'Find new chargetime for {car.carName}' }]
                                }
                            self.notify_app.send_notification(
                                message = f"Was not able to finish with {round(car.car_data.kWh_remain_to_charge,2)} kWh remaining before prices increased.",
                                message_title = f"🚘Charging {car.carName}",
                                message_recipient = self.recipients,
                                also_if_not_home = False,
                                data = data
                            )
                    else:
                        car.findNewChargeTime()

    def _reduce_charging_ampere(self, reduce_Wh, available_Wh, charging_list) -> float:
        """ Reduces charging to stay within max kWh """

        for queue_id in reversed(charging_list):
            car = Registry.get_car(queue_id)
            if car is None or car.connected_charger is None:
                continue

            ampere_charging = car.connected_charger.charger_data.ampereCharging
            if ampere_charging == 0:
                ampere_charging = car.connected_charger.update_ampere_charging_from_sensor()

            charger_min_ampere = car.connected_charger.charger_data.min_ampere
            charger_voltPhase = car.connected_charger.charger_data.voltPhase

            if ampere_charging > charger_min_ampere:
                AmpereToReduce = math.floor(reduce_Wh + available_Wh / charger_voltPhase)
                if (ampere_charging + AmpereToReduce) < charger_min_ampere:
                    car.connected_charger.setChargingAmps(charging_amp_set = charger_min_ampere)
                    available_Wh -= (ampere_charging  - charger_min_ampere) * charger_voltPhase
                    reduce_Wh -= (ampere_charging  - charger_min_ampere) * charger_voltPhase
                else:
                    car.connected_charger.changeChargingAmps(charging_amp_change = AmpereToReduce)
                    available_Wh -= AmpereToReduce * charger_voltPhase
                    reduce_Wh -= AmpereToReduce * charger_voltPhase

            if reduce_Wh + available_Wh > 0:
                return reduce_Wh, available_Wh
        return reduce_Wh, available_Wh

    def _increase_charging_ampere(self, car, increase_Wh: float) -> None:
        """ Increase charging speed """

        AmpereToIncrease = math.floor(increase_Wh / car.connected_charger.charger_data.voltPhase)
        car.connected_charger.changeChargingAmps(charging_amp_change = AmpereToIncrease)

    def _stop_chargers_due_to_overconsumption(self) -> bool:
        for queue_id in reversed(self._persistence.queueChargingList):
            car = Registry.get_car(queue_id)
            if car is None or car.connected_charger is None:
                continue

            if car.connected_charger.getChargingState() == "Charging":
                self.available_Wh += (
                    car.connected_charger.charger_data.ampereCharging * car.connected_charger.charger_data.voltPhase
                )
                car.stopCharging(force_stop = True)
                if self.available_Wh > -100:
                    return True
        return False

    # Manage heaters consumption

    def _reduce_heating(self) -> None:
        now = self.ADapi.datetime(aware = True)
        for heater in self.heaters:
            if heater not in self.heatersRedusedConsumption:
                heater_consumption_now, valid_consumption = heater.get_heater_consumption()

                if heater_consumption_now > 100:
                    self.heatersRedusedConsumption.append(heater)
                    heater.last_reduced_state = now
                    heater.heater_data.prev_consumption = heater_consumption_now
                    heater.setSaveState()
                    if (
                        self.ADapi.get_state(heater.heater,
                            attribute = 'hvac_action',
                            namespace = heater.namespace
                        ) == 'heating'
                        or valid_consumption
                    ):
                        self.available_Wh += heater_consumption_now
                    if heater_consumption_now > heater.heater_data.normal_power:
                        heater.heater_data.normal_power = heater_consumption_now
            else:
                heater.last_reduced_state = now
            if self.available_Wh > -100:
                return

    def _get_heaters_reduced_previous_consumption(self, avail:float = 0) -> float:
        """ Function that finds the value of power consumption when heating for items that are turned down
            and turns the heating back on if there is enough available watt,
            or return how many watt to reduce charing to turn heating back on """

        reduce_Wh: float = 0
        to_remove = set()
        now = self.ADapi.datetime(aware = True)
        for heater in reversed(self.heatersRedusedConsumption):
            if heater.heater_data.prev_consumption + 600 < avail:
                heater.setPreviousState()
                avail -= heater.heater_data.prev_consumption
                to_remove.add(heater)
                self.lastTimeHeaterWasReduced = now
            elif heater.heater_data.prev_consumption > avail:
                reduce_Wh -= heater.heater_data.prev_consumption
        self.heatersRedusedConsumption = [
            qid for qid in self.heatersRedusedConsumption
            if qid not in to_remove
        ]
        return reduce_Wh, avail


    def get_idle_and_heater_consumption(self) -> Tuple[float | None, float | None]:
        data = self._persistence.idle_usage.ConsumptionData
        tmp  = get_consumption_for_outside_temp(data, self.out_temp)
        if tmp is None:
            return None, None
        try:
            idle  = float(tmp.Consumption)
            heater= float(tmp.HeaterConsumption)
        except Exception:
            return None, None
        return idle, heater

    def _run_find_consumption_after_turned_back_on(self, kwargs):
        now = self.ADapi.datetime(aware = True)
        tomorrow_start = (now + timedelta(days = 1)).replace(
            hour = 0, minute = 0, second = 0, microsecond = 0
        )
        for heater in self.heaters:
            for item in heater.heater_data.time_to_save:
                if now < item.end <= tomorrow_start:
                    self.ADapi.run_at(self.findConsumptionAfterTurnedBackOn, item.end, heater = heater, time_to_save_item = item)

    def findConsumptionAfterTurnedBackOn(self, **kwargs) -> None:
        """ Functions to register consumption based on outside temperature after turned back on,
            to better be able to calculate chargingtime based on max kW pr hour usage """

        heater = kwargs['heater']
        time_to_save_item = kwargs['time_to_save_item']
        hoursOffInt = 0
        now_notAware = self.ADapi.datetime()

        if not heater.away_state:
            for daytime in heater.heater_data.daytime_savings:
                if 'start' in daytime and 'stop' in daytime:
                    if not 'presence' in daytime:
                        if (start := self.ADapi.parse_datetime(daytime['start'])) <= now_notAware < (end := self.ADapi.parse_datetime(daytime['stop'])):

                            off_hours = self.ADapi.parse_datetime(daytime['stop']) - self.ADapi.parse_datetime(daytime['start'])
                            hoursOffInt = off_hours.seconds//3600
                            break
            if hoursOffInt == 0:
                try:
                    hoursOffInt = time_to_save_item.duration.seconds//3600
                except (ValueError, TypeError) as e:
                    self.ADapi.log(f"Could not convert {time_to_save_item.duration} to a duration: {e}", level = 'DEBUG')
                    return
            if hoursOffInt > 0:
                runtime = time_to_save_item.end + timedelta(minutes = 3)
                self.ADapi.run_at(heater.findConsumptionAfterTurnedOn, runtime, hoursOffInt = hoursOffInt)


    def _reset_hourly(self, now) -> None:
        self.last_accumulated_kWh = 0
        self.find_next_charger_counter = 0
        if now.hour == 0 and now.day == 1:
            self._persistence.max_usage.max_kwh_usage_pr_hour = self.max_kwh_goal

        elif self.accumulated_kWh > self._persistence.max_usage.topUsage[0]:
            self.logHighUsage()
        self._check_charging_this_hour()


    # Functions to calculate and store consumption

    def calculateIdleConsumption(self, kwargs: dict) -> None:
        """Build the per_hour available_wh schedule """

        persistence = self._persistence

        now = self.ADapi.datetime(aware = True)
        save_end_hour = now.replace(minute = 0, second = 0, microsecond = 0)
        duration_hours = 1

        slots: List[WattSlot] = []
        for item in self.electricalPriceApp.elpricestoday:
            duration_hours = (item.end - item.start).total_seconds() / 3600.0
            base_wh = persistence.max_usage.max_kwh_usage_pr_hour * 1_000 * duration_hours
            slots.append(WattSlot(start=item.start, end=item.end, available_Wh=base_wh))

        reduce_avg_heater_watt = 1.0
        reduce_avg_idle_watt   = 1.0
        idle_block = persistence.idle_usage
        if idle_block and idle_block.ConsumptionData:
            idle_consumption = get_consumption_for_outside_temp(idle_block.ConsumptionData, self.out_temp)
            if idle_consumption:
                reduce_avg_heater_watt = float(idle_consumption.HeaterConsumption or 0)
                reduce_avg_idle_watt   = float(idle_consumption.Consumption or 0)
                idle_val = (reduce_avg_heater_watt + reduce_avg_idle_watt) * duration_hours
                for s in slots:
                    s.available_Wh -= idle_val

        total_power = self.totalWattAllHeaters or 1.0
        for heater_id, heater_block in persistence.heater.items():
            if not heater_block or not heater_block.ConsumptionData:
                continue

            matching_heater = next((h for h in self.heaters if h.heater == heater_id), None)
            if matching_heater is None:
                continue

            for item in matching_heater.heater_data.time_to_save:
                end_time: Optional[now] = item.end
                if end_time and end_time.date() == now.date():
                    save_end_hour = end_time

                duration: timedelta | None = item.duration
                if not duration:
                    continue

                off_minutes = int(duration.total_seconds() // 60)
                off_hours   = off_minutes // 60
                if off_hours == 0:
                    continue

                nested = heater_block.ConsumptionData.get(off_minutes)
                if not nested:
                    available_keys = [int(k) for k in heater_block.ConsumptionData.keys()]
                    closest = closest_value(data = available_keys, target = off_minutes)
                    if closest is None:
                        continue
                    nested = heater_block.ConsumptionData[closest]

                temp_consumption = get_consumption_for_outside_temp(nested, self.out_temp)
                if temp_consumption is None:
                    continue

                try:
                    expected_kwh = float(temp_consumption.Consumption or 0) * 1000
                except Exception:
                    temp_keys = [int(k) for k in nested.keys()]
                    closest_temp = closest_value(data = temp_keys, target = self.out_temp)
                    if closest_temp is None:
                        continue
                    expected_kwh = float(nested[closest_temp].Consumption or 0) * 1000

                heater_watt = heater_block.normal_power or 0.0
                pct = heater_watt / total_power
                heater_watt -= reduce_avg_heater_watt * pct
                heater_consumption = heater_watt * duration_hours

                idx = bisect.bisect_left([s.start for s in slots], end_time)
                remaining = expected_kwh
                for s in slots[idx:]:
                    if remaining <= 0:
                        break
                    usable = min(s.available_Wh, heater_consumption)
                    if remaining > heater_consumption:
                        if s.available_Wh < heater_consumption:
                            remaining -= s.available_Wh
                            s.available_Wh = 0.0
                        else:
                            remaining -= heater_consumption
                            s.available_Wh -= heater_consumption
                    else:
                        s.available_Wh -= remaining
                        remaining = 0.0
                        break

        self.charging_scheduler.save_endHour   = save_end_hour
        persistence.available_watt = slots


    def logIdleConsumption(self, kwargs) -> None:
        """ Calculate the new idle & heater consumption values for the *current* outside temperature """

        try:
            self.current_consumption = float(self.ADapi.get_state(self.current_consumption_sensor))
        except ValueError as ve:
            self.ADapi.log(f"Current consumption is unavailable - skipping idle log", level = 'DEBUG')
            return

        heater_consumption: float = 0.0
        for heater in self.heaters:
            if heater.heater_data.validConsumptionSensor and heater._consumption_stops_register_usage_handler is None:
                try:
                    heater_consumption += float(
                        self.ADapi.get_state(heater.heater_data.consumptionSensor, namespace = heater.namespace)
                    )
                except (ValueError, TypeError):
                    pass
                else:
                    if heater_consumption > heater.heater_data.normal_power:
                        heater.heater_data.normal_power = heater_consumption

        idle_consumption = self.current_consumption - heater_consumption
        if idle_consumption <= 0:
            self.ADapi.log(f"idle_consumption={idle_consumption} - aborting logging Idle Consumption", level = 'DEBUG')
            return

        out_temp_even = floor_even(self.out_temp)
        consumption_dict = self._persistence.idle_usage.ConsumptionData

        if out_temp_even in consumption_dict:
            old = consumption_dict[out_temp_even]

            new_counter = old.Counter + 1
            new_consumption = round(
                ((old.Consumption or 0) * old.Counter + idle_consumption) / new_counter, 2
            )
            new_heater = round(
                ((old.HeaterConsumption or 0) * old.Counter + heater_consumption) / new_counter, 2
            )

            result_diff:bool = diff_ok(old.Consumption, idle_consumption, MAX_CONSUMPTION_RATIO_DIFFERENCE)

            if (
                result_diff and
                heater_consumption <= self.totalWattAllHeaters
                or old.Counter < 2
            ):

                if new_counter > 100:
                    new_counter = 10
                elif not result_diff:
                    new_counter = 1
                new_entry = TempConsumption(
                    Consumption = new_consumption,
                    HeaterConsumption = new_heater,
                    Counter = new_counter
                )
                consumption_dict[out_temp_even] = new_entry
            else:
                self.ADapi.log(
                    f"Discarded idle sample at {out_temp_even} degrees - too different from existing data",
                    level = 'DEBUG'
                )
                return

        else:
            nearest_key = closest_temp_in_dict(out_temp_even, consumption_dict)

            if nearest_key is None:
                new_entry = TempConsumption(
                    Consumption = idle_consumption,
                    HeaterConsumption = heater_consumption,
                    Counter = 1
                )
                consumption_dict[out_temp_even] = new_entry

            else:
                nearest = consumption_dict[nearest_key]
                temp_diff = abs(int(out_temp_even) - int(nearest_key))

                new_consumption = round(idle_consumption, 2)
                new_heater = round(heater_consumption, 2)

                if temp_diff <= MAX_TEMP_DIFFERENCE and nearest.Counter > 2:
                    if not diff_ok(nearest.Consumption, new_consumption, MAX_CONSUMPTION_RATIO_DIFFERENCE):
                        self.ADapi.log(
                            f"Discarded idle sample at {out_temp_even} degrees "
                            f"closest data at {nearest_key} degrees is too far or too different",
                            level = 'DEBUG'
                        )
                        return
                new_entry = TempConsumption(
                    Consumption = new_consumption,
                    HeaterConsumption = new_heater,
                    Counter = 1
                )
                consumption_dict[out_temp_even] = new_entry

    def logHighUsage(self) -> None:
        """ Updates top three max kWh usage pr hour """

        newTotal = 0.0
        max_kwh_usage_top = self._persistence.max_usage.topUsage
        newTopUsage:float = 0

        try:
            newTopUsage = float(self.ADapi.get_state(self.accumulated_consumption_current_hour))
            if newTopUsage > self._persistence.max_usage.topUsage[0]:
                max_kwh_usage_top[0] = newTopUsage
                self._persistence.max_usage.topUsage = sorted(max_kwh_usage_top)
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"Not able to set new Top Hour Usage. Accumulated consumption is {self.ADapi.get_state(self.accumulated_consumption_current_hour)} "
                f"ValueError: {ve}",
                level = 'WARNING'
            )

        for num in self._persistence.max_usage.topUsage:
            newTotal += num
        avg_top_usage = newTotal / 3

        if avg_top_usage > self._persistence.max_usage.max_kwh_usage_pr_hour:
            self._persistence.max_usage.max_kwh_usage_pr_hour += 5
            self.ADapi.log(
                f"Avg consumption during one hour is now {round(avg_top_usage, 3)} kWh and surpassed max kWh set. "
                f"New max kWh usage during one hour set to {self._persistence.max_usage.max_kwh_usage_pr_hour}. "
                "If this is not expected try to increase buffer.",
                level = 'WARNING'
            )
        elif (
            avg_top_usage > self._persistence.max_usage.max_kwh_usage_pr_hour - self.buffer
            and newTopUsage != 0   
        ):
            self.ADapi.log(
                f"Consumption last hour: {round(newTopUsage, 3)}. "
                f"Avg top 3 hours: {round(avg_top_usage, 3)}",
                level = 'INFO'
            )


    # Weather sensors

    def weather_event(self, event_name, data, **kwargs) -> None:
        """ Listens for weather change from the weather app """

        self.out_temp = float(data['temp'])

    def _refresh_heaters(self) -> None:
        """Remove orphan heater blocks and recompute the total wattage."""
        persistence = self._persistence
        heaters_to_remove = []

        total_power = 0.0
        active_names = {h.heater for h in self.heaters}

        for heater_name, heater_block in list(persistence.heater.items()):
            if heater_block.normal_power:
                total_power += heater_block.normal_power

            if heater_name not in active_names:
                heaters_to_remove.append(heater_name)

        if heaters_to_remove:
            for key in heaters_to_remove:
                del persistence.consumption[key]

        self.totalWattAllHeaters = total_power


    def mode_event(self, event_name, data, **kwargs) -> None:
        """ Listens to same mode event that I have used in Lightwand: https://github.com/Pythm/ad-Lightwand
            If mode name equals 'fire' it will turn off all charging and heating.
            To call from another app use: self.fire_event('MODE_CHANGE', mode = 'fire')
            Set back to normal with mode 'false-alarm' """

        if data['mode'] == FIRE_TRANSLATE:
            self.houseIsOnFire = True
            for car in self.all_cars_connected():
                if car.getCarChargerState() == 'Charging':
                    car.stopCharging(force_stop = True)
            
            for charger in self.all_chargers():
                charger.doNotStartMe = True

            for heater in self.heaters:
                heater.turn_off_heater()


        elif data['mode'] == FALSE_ALARM_TRANSLATE:
            # Fire alarm stopped
            self.houseIsOnFire = False
            for heater in self.heaters:
                heater.turn_on_heater()
            
            for charger in self.all_chargers():
                charger.doNotStartMe = False

            for car in self.all_cars_connected():
                if car.kWhRemaining() > 0:
                    car.findNewChargeTime()

    def _notify_overconsumption(self) -> None:
        if self.notify_about_overconsumption:
            self.notify_about_overconsumption = False
            self.notify_app.send_notification(
                message=(
                    f"Turn down consumption. It's about to go over max usage "
                    f"with {round(-self.available_Wh, 0)} Wh remaining to reduce"
                ),
                message_title="⚡High electricity usage",
                message_recipient=self.recipients,
                also_if_not_home=False,
                data={"tag": "overconsumption"},
            )
        else:
            self.notify_about_overconsumption = True

    def _notify_event(self, event_name, data, **kwargs) -> None:
        for car in self.all_cars():
            if data['action'] == 'find_new_chargetime'+str(car.carName):
                car.kWhRemaining()
                car.findNewChargeTime()
                return
        
        for charger in self.all_chargers():
            if data['action'] == 'kWhremaining'+str(charger.charger):
                try:
                    charger.connected_vehicle.car_data.kWh_remain_to_charge = float(data['reply_text'])
                except (ValueError, TypeError):
                    charger.kWhRemaining()
                    self.ADapi.log(
                        f"User input {data['reply_text']} on setting kWh remaining for Guest car. Not valid number. "
                        f"Using {charger.connected_vehicle.car_data.kWh_remain_to_charge} to calculate charge time",
                        level = 'INFO'
                    )
                charger.connected_vehicle.findNewChargeTime()
                return

            if data['action'] == 'chargeNow'+str(charger.charger):
                charger.connected_vehicle.charge_now = True
                charger.startCharging()
                return

    def _awayStateListen_Main(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state. """
        self.away_state = new == 'on'

    async def _reload_accumulated_consumption_sensor(self) -> None:
        await self.ADapi.call_service('homeassistant/reload_config_entry',
            entity_id = self.accumulated_consumption_current_hour
        )

class Scheduler:
    """ Class for calculating and schedule charge times. """

    def __init__(self, api,
        stopAtPriceIncrease:float,
        startBeforePrice:float,
        infotext,
        namespace:str,
        chargingQueue: Optional[list[ChargingQueueItem]] = None,
        available_watt: Optional[List[WattSlot]] = None,
    ):
        self.ADapi = api
        self.namespace = namespace
        self.stopAtPriceIncrease = stopAtPriceIncrease
        self.startBeforePrice = startBeforePrice
        self.infotext = infotext

        self.chargingQueue: list[ChargingQueueItem] = chargingQueue
        self.available_watt: List[WattSlot] = available_watt

        self.simultaneousChargeComplete: list[str] = []
        self.currentlyCharging: set[str] = set()
        self.informHandler = None

        # helper values
        now = self.ADapi.datetime(aware=True)
        self.save_endHour = now.replace(minute=0, second=0, microsecond=0)

    def _calculate_expected_chargetime(
        self,
        kWhRemaining: float = 2,
        totalW_AllChargers: float = 3600,
        start_time: Optional[self.ADapi.datetime(aware=True)] = None,
    ) -> float:
        """ Estimate the *number of hours* it will take to finish a charge. """

        if start_time is None:
            start_time = self.ADapi.datetime(aware=True)

        if start_time > self.save_endHour:
            self.save_endHour = get_next_runtime_aware(
                startTime = start_time, offset_seconds=0, delta_in_seconds=60 * 15
            )

        idx_start = bisect.bisect_left([s.start for s in self.available_watt], self.save_endHour)

        wh_remaining = kWhRemaining * 1_000
        hours_to_charge = 0.0

        for slot in self.available_watt[idx_start:]:
            if wh_remaining <= 0:
                break

            usable_wh = min(
                slot.available_Wh,
                totalW_AllChargers * slot.duration_hours,
            )

            if wh_remaining <= usable_wh:
                hours_to_charge += slot.duration_hours
                wh_remaining = 0
            else:
                wh_remaining -= usable_wh
                hours_to_charge += slot.duration_hours

        if wh_remaining > 0 and self.available_watt:
            last = self.available_watt[-1]
            extra = (wh_remaining / last.available_Wh) * last.duration_hours
            hours_to_charge += extra

        return hours_to_charge

    def _entry_for(self, vehicle_id: str) -> Optional["ChargingQueueItem"]:
        """ Return the first queue item that belongs to *vehicle_id* or ``None``. """

        return next((c for c in self.chargingQueue if c.vehicle_id == vehicle_id), None)

    def _vehicle_priority_map(self) -> dict[str, int]:
        """ A lookup table that maps every vehicle_id that is currently in the *charging* queue to its `priority` value. """
        return {item.vehicle_id: item.priority for item in self.chargingQueue}

    def sort_charging_queue_by_priority(
        self,
        vehicle_ids: List[str],
        *,
        reverse: bool = False
    ) -> List[str]:
        """ Return a **new list** containing the same vehicle_ids but sorted
        according to the priority stored in `self._persistence.chargingQueue`."""
        priority_map = self._vehicle_priority_map()

        def priority_of(vid: str) -> int:
            return priority_map.get(vid, 5)

        return sorted(vehicle_ids, key=priority_of, reverse=reverse)

    def getChargingTime(self, vehicle_id: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        """ Return ``(charging_start, charging_stop)`` for *vehicle_id* if the
        queue item has both timestamps set, otherwise ``(None, None)``. """

        entry = self._entry_for(vehicle_id)
        if entry and entry.chargingStart and entry.chargingStop:
            return entry.chargingStart, entry.chargingStop
        return None, None

    def isChargingTime(self, vehicle_id: Optional[str] = None) -> bool:
        """ Return ``True`` if *now* lies between a chargingStart/Stop pair for the
        supplied vehicle (or for any vehicle when *vehicle_id* is ``None``). """

        if not self.chargingQueue:
            return False

        now = self.ADapi.datetime(aware=True)

        for entry in self.chargingQueue:
            if vehicle_id is not None and entry.vehicle_id != vehicle_id:
                continue

            if entry.chargingStart and entry.chargingStop and entry.chargingStart <= now < entry.chargingStop:
                return True

        if (
            self.ADapi.now_is_between("09:00:00", "14:00:00")
            and not ELECTRICITYPRICE.tomorrow_valid
        ):
            return self._is_charging_price(vehicle_id = vehicle_id)

        return False

    def _is_charging_price(self, vehicle_id: Optional[str] = None) -> bool:
        max_price = 0.0
        for entry in self.chargingQueue:
            if entry.price is not None and entry.price > max_price:
                max_price = entry.price

        if max_price == 0:
            max_price = self._update_prices_for_future_hours()
            if max_price == -1:
                return False
        return ELECTRICITYPRICE.electricity_price_now() <= max_price


    def _update_prices_for_future_hours(self) -> float:
        """ When tomorrow's price data is not yet available """
        now = self.ADapi.datetime(aware=True)
        if all(c.price is not None for c in self.chargingQueue):
            return -1

        kWh_to_charge = sum(c.kWhRemaining for c in self.chargingQueue if c.kWhRemaining is not None)
        total_power = sum(c.maxAmps * c.voltPhase for c in self.chargingQueue if c.maxAmps and c.voltPhase)

        if not any(c.estHourCharge for c in self.chargingQueue):
            est_hours = self._calculate_expected_chargetime(
                kWhRemaining = kWh_to_charge,
                totalW_AllChargers = total_power
            )
        else:
            est_hours = sum(c.estHourCharge for c in self.chargingQueue if c.estHourCharge)

        price = ELECTRICITYPRICE.get_lowest_prices(
            checkitem = now,
            hours = est_hours,
            min_change = 0.1
        )

        for c in self.chargingQueue:
            c.price = price
        return price

    def getVehiclePrice(self, vehicle_id: Optional[str] = None) -> float:
        """
        Return the price for a specific vehicle if it is present in the queue.
        Otherwise return the *highest* price seen across all entries """

        highest_price = 0.0
        for entry in self.chargingQueue:
            if vehicle_id is not None and entry.vehicle_id == vehicle_id:
                return entry.price or 0.0
            if entry.price is not None and entry.price > highest_price:
                highest_price = entry.price
        return highest_price

    def isPastChargingTime(self, vehicle_id: Optional[str] = None) -> bool:
        """ Return ``True`` when the charging stop time for *vehicle_id* has
        already passed. """
        now = self.ADapi.datetime(aware=True)
        entry = self._entry_for(vehicle_id) if vehicle_id else None
        if not entry or entry.chargingStop is None:
            return False
        return now > entry.chargingStop

    def charging_scheduled_with_updated_data(
        self,
        vehicle_id: str,
        kWhRemaining: float,
        finish_by_hour: int,
    ) -> bool:
        """ Return ``True`` if a matching queue entry exists **and** the
        scheduled charging has not yet finished. """
        now = self.ADapi.datetime(aware=True)
        entry = self._entry_for(vehicle_id)
        if not entry:
            return False

        if (
            entry.kWhRemaining == kWhRemaining
            and entry.finish_by_hour == finish_by_hour
            and entry.chargingStart
            and entry.chargingStop
        ):
            return now < entry.chargingStop

        return False

    def markAsCharging(self, vehicle_id):
        if vehicle_id in self.currentlyCharging:
            return
        self.currentlyCharging.add(vehicle_id)

    def removeFromCharging(self, vehicle_id):
        if vehicle_id in self.currentlyCharging:
            self.currentlyCharging.discard(vehicle_id)

    def isCurrentlyCharging(self, vehicle_id):
        return vehicle_id in self.currentlyCharging

    def findNextChargerToStart(self) -> Optional[str]:
        """Return the *vehicle_id* of the next charging job that is ready to start. """

        for priority in range(1, 6):
            for entry in self.chargingQueue:
                if entry.priority == priority or priority == 5:
                    if not self.isCurrentlyCharging(entry.vehicle_id) \
                    and self.isChargingTime(entry.vehicle_id):
                        return entry.vehicle_id
        return None

    def removeFromQueue(self, vehicle_id: str) -> None:
        """ Remove the first queue entry that matches *vehicle_id*. """
        for idx, entry in enumerate(self.chargingQueue):
            if entry.vehicle_id == vehicle_id:
                del self.chargingQueue[idx]
                break

    def queueForCharging(
        self,
        vehicle_id: str,
        kWhRemaining: float,
        maxAmps: int,
        voltPhase: int,
        finish_by_hour: int,
        priority: int,
        name: str,
    ) -> bool:
        """
        Enqueue a new charging job (or replace an existing one). """
        self.removeFromQueue(vehicle_id)

        if kWhRemaining <= 0:
            return False

        est_hour_charge = self._calculate_expected_chargetime(
            kWhRemaining=kWhRemaining,
            totalW_AllChargers=maxAmps * voltPhase,
        )

        new_item = ChargingQueueItem(
            vehicle_id=vehicle_id,
            kWhRemaining=kWhRemaining,
            maxAmps=maxAmps,
            voltPhase=voltPhase,
            finish_by_hour=finish_by_hour,
            priority=priority,
            estHourCharge=est_hour_charge,
            name=name,
        )
        self.chargingQueue.append(new_item)

        if self.ADapi.now_is_between("09:00:00", "14:00:00") and not ELECTRICITYPRICE.tomorrow_valid:
            return self.isChargingTime(vehicle_id=vehicle_id)

        self.process_charging_queue()
        return self.isChargingTime(vehicle_id=vehicle_id)

    def process_charging_queue(self) -> None:
        """
        Resolve the whole queue, scheduling charging windows, detecting
        simultaneous sessions and finally computing the “best” price block
        for each job.
        """
        self.chargingQueue.sort(key=lambda c: c.finish_by_hour)

        simultaneous_charge: List[str] = []
        self.simultaneousChargeComplete = []

        for i, current_car in enumerate(self.chargingQueue):
            (
                current_car.chargingStart,
                current_car.estimateStop,
                current_car.chargingStop,
                current_car.price,
            ) = ELECTRICITYPRICE.get_Continuous_Cheapest_Time(
                hoursTotal=current_car.estHourCharge,
                calculateBeforeNextDayPrices=False,
                finishByHour=current_car.finish_by_hour,
                startBeforePrice=self.startBeforePrice,
                stopAtPriceIncrease=self.stopAtPriceIncrease,
            )

            has_overlap = False
            for overlapping_id in simultaneous_charge:
                idx = next(
                    (j for j, c in enumerate(self.chargingQueue) if c.vehicle_id == overlapping_id),
                    None,
                )
                if idx is not None and self.chargingQueue[idx].chargingStop > current_car.chargingStart:
                    has_overlap = True
                    break

            if not has_overlap:
                for j in range(i - 1, -1, -1):
                    prev = self.chargingQueue[j]
                    if prev.chargingStop is not None and current_car.chargingStart < prev.chargingStop:
                        simultaneous_charge.append(prev.vehicle_id)
                simultaneous_charge.append(current_car.vehicle_id)
            else:
                simultaneous_charge.append(current_car.vehicle_id)

            next_index = i + 1
            if next_index < len(self.chargingQueue):
                next_car = self.chargingQueue[next_index]
                if next_car.chargingStart is not None and current_car.chargingStop is not None:
                    if next_car.chargingStart >= current_car.chargingStop:
                        if simultaneous_charge:
                            self.calcSimultaneousCharge(simultaneous_charge)
                            self.simultaneousChargeComplete.extend(simultaneous_charge)
                            simultaneous_charge = []

        if simultaneous_charge:
            self.calcSimultaneousCharge(simultaneous_charge)
            self.simultaneousChargeComplete.extend(simultaneous_charge)

    def calcSimultaneousCharge(self, simultaneous_charge: List[str]) -> None:
        """
        Re-calculate the charging window for a group of vehicles that must run
        at the same time.  The function updates the queue in place.
        """
        finish_by_hour = 0
        kWh_to_charge = 0.0
        total_w_all_chargers = 0.0
        start_time = self.ADapi.datetime(aware=True)

        for c in self.chargingQueue:
            if c.vehicle_id in simultaneous_charge:
                kWh_to_charge += c.kWhRemaining
                total_w_all_chargers += c.maxAmps * c.voltPhase

                if c.finish_by_hour > finish_by_hour:
                    if finish_by_hour == 0:
                        finish_by_hour = c.finish_by_hour
                    else:
                        finish_by_hour += c.estHourCharge

                if c.chargingStart is not None:
                    start_time = c.chargingStart

        hours_to_charge = self._calculate_expected_chargetime(
            kWhRemaining=kWh_to_charge,
            totalW_AllChargers=total_w_all_chargers,
            start_time=start_time,
        )

        (
            charging_at,
            estimate_stop,
            charging_stop,
            price,
        ) = ELECTRICITYPRICE.get_Continuous_Cheapest_Time(
            hoursTotal=hours_to_charge,
            calculateBeforeNextDayPrices=False,
            finishByHour=finish_by_hour,
            startBeforePrice=self.startBeforePrice,
            stopAtPriceIncrease=self.stopAtPriceIncrease,
        )

        if estimate_stop is not None:
            for c in self.chargingQueue:
                if c.vehicle_id in simultaneous_charge:
                    c.chargingStart = charging_at
                    c.estimateStop = estimate_stop
                    c.chargingStop = charging_stop
                    c.price = price

    def notifyChargeTime(self, kwargs) -> None:
        """ Sends notifications and updates infotext with charging times and prices. """

        def _fmt(dt: datetime | None) -> str:
            """Return a human readable string without the TZ component."""
            return "" if dt is None else dt.strftime("%Y-%m-%d %H:%M")

        lowest_price: float | None = None
        times_set = False
        send_new_info = False
        info_text = ""
        info_text_simultaneous_car = "Charge "
        info_text_simultaneous_time = ""

        sorted_queue: List[QueueItem] = sorted(
            self.chargingQueue, key=lambda c: c.finish_by_hour
        )

        for car in sorted_queue:
            if self.charging_scheduled_with_updated_data(
                vehicle_id=car.vehicle_id,
                kWhRemaining=car.kWhRemaining,
                finish_by_hour=car.finish_by_hour,
            ):
                already_informed = (
                    car.informedStart is not None
                    and car.informedStop is not None
                    and car.chargingStart is not None
                )

                if already_informed:
                    if (
                        car.informedStart != car.chargingStart
                        or car.informedStop != car.estimateStop
                    ):
                        send_new_info = True
                else:
                    send_new_info = True

                if car.chargingStart is not None:
                    car.informedStart = car.chargingStart
                    car.informedStop = car.estimateStop

                    timestr_start = _fmt(car.chargingStart)
                    timestr_eta_stop = _fmt(car.estimateStop)
                    timestr_stop = _fmt(car.chargingStop)

                    if car.vehicle_id in self.simultaneousChargeComplete:
                        info_text_simultaneous_car += f"{car.name} & "
                        info_text_simultaneous_time = (
                            f" at {timestr_start}. Finish est at {timestr_eta_stop}. "
                            f"Stop no later than {timestr_stop}. "
                        )
                    else:
                        info_text += (
                            f"Start {car.name} at {timestr_start}. "
                            f"Finish est at {timestr_eta_stop} with {car.estHourCharge} hours to charge. "
                            f"Stop no later than {timestr_stop}. "
                        )
                    times_set = True

            if car.price is not None:
                if lowest_price is None or car.price < lowest_price:
                    lowest_price = car.price

        if info_text_simultaneous_car.endswith(" & "):
            info_text_simultaneous_car = info_text_simultaneous_car[:-3]

        info_text += info_text_simultaneous_car + info_text_simultaneous_time

        if not times_set and lowest_price is not None:
            price_msg = (
                f"Charge if price is lower than {ELECTRICITYPRICE.currency} "
                f"{round(lowest_price - ELECTRICITYPRICE.current_daytax, 3)} (day) or "
                f"{ELECTRICITYPRICE.currency} {round(lowest_price - ELECTRICITYPRICE.current_nighttax, 3)} (night/weekend)"
            )
            info_text = price_msg

        if self.infotext not in (None, "Charge "):
            info_text.strip()
            self.ADapi.call_service(
                "input_text/set_value",
                value=info_text,
                entity_id=self.infotext,
                namespace=self.namespace,
            )

            if send_new_info:
                data = {"tag": "chargequeue"}
                NOTIFY_APP.send_notification(
                    message=info_text,
                    message_title="🔋 Charge Queue",
                    message_recipient=RECIPIENTS,
                    also_if_not_home=True,
                    data=data,
                )


class Charger:
    """ Charger parent class
    Set variables in childclass before init:
        self.charger_id:str # Unik ID to identify chargers
        self.volts:int # 220/266/400v
        self.phases:int # 1 phase or 3 phase
        self._cars:list # list of cars that can connect to charger

    Set variables in childclass after init for connected chargers if needed:
        self.guestCharging:bool # Defaults to False
    Set variables in childclass after init for onboard chargers if needed:
        self.Car
        self.Car.onboardCharger
    
    Change default values if needed:
    self.charger_data.min_ampere = 6
    
    Functions to implement in child class:
        def setmaxChargingAmps(self) -> None:
        def getChargingState(self) -> str:
    """
    def __init__(self, api,
        namespace:str,
        charger:str,
        charger_data,
    ):

        self.ADapi = api
        self.Car = None
        self.namespace = namespace
        self.charger = charger
        self.charger_data = charger_data

        # Helpers
        self.checkCharging_handler = None
        self.doNotStartMe:bool = False
        self.pct_start_charge:float = 100

        # Switch to allow guest to charge
        if isinstance(charger_data.guest, str):
            self.guestCharging = api.get_state(charger_data.guest, namespace = namespace) == 'on'
            api.listen_state(self.guestChargingListen, charger_data.guest,
                namespace = namespace
            )
        else:
            self.guestCharging = False

        # Switch to allow current when preheating
        if isinstance(charger_data.idle_current, str):
            self.idle_current = api.get_state(charger_data.idle_current, namespace = namespace) == 'on'
            api.listen_state(self.idle_currentListen, charger_data.idle_current,
                namespace = namespace
            )
        else:
            self.idle_current = False

        if self.charger_data.charging_amps is not None:
            api.listen_state(self.updateAmpereCharging, self.charger_data.charging_amps,
                namespace = namespace
            )

        """ End initialization Charger Class
        """

    def findCarConnectedToCharger(self) -> bool:
        """ A check to see if a car is connected to the charger. """
        if self.getChargingState() not in ('Disconnected', 'Complete', 'NoPower'):
            for car in self._cars:
                if car._polling_of_data() and car.isConnected():
                    if car.connectedCharger is None or car.getCarChargerState() == 'NoPower':
                        if self.compareChargingState(
                            car_status = car.getCarChargerState()
                        ):
                            car.connectedCharger = self
                            self.Car = car
                            self.kWhRemaining()
                            self.Car.findNewChargeTime()
                            return True

            if self.Car is None:
                self.ADapi.run_in(self._recheck_findCarConnectedToCharger, 120)
        return False

        # Functions to react to charger sensors
    def _recheck_findCarConnectedToCharger(self, kwargs) -> None:
        self.findCarConnectedToCharger()

    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge from car battery sensor/size and charge limit.
            If those are not available it uses session energy to estimate how much is needed to charge.
        """
        chargingState = self.getChargingState()
        if chargingState in ('Complete', 'Disconnected'):
            if self.guestCharging:
                self.Car.car_data.kWh_remain_to_charge = -1
            return -1

        if self.Car is not None:
            kWhRemain:float = self.Car.kWhRemaining()
            if kWhRemain > -2:
                return kWhRemain

            if self.charger_data.session_energy:
                if self.guestCharging:
                    kWh_remain = self.Car.car_data.kWh_remain_to_charge - (float(self.ADapi.get_state(self.charger_data.session_energy, namespace = self.namespace)))
                    if kWh_remain > 2:
                        return kWh_remain
                    else:
                        return 10

                self.Car.car_data.kWh_remain_to_charge = self.Car.car_data.max_kWh_charged - float(self.ADapi.get_state(self.charger_data.session_energy,
                    namespace = self.namespace)
                )
                return self.Car.car_data.kWh_remain_to_charge
        
        return -1

    def compareChargingState(self, car_status:str) -> bool:
        """ Returns True if car and charger match charging state.
        """
        charger_status = self.getChargingState()
        return car_status == charger_status

    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Valid returns: 'Complete' / None / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        if self.charger_data.charger_sensor is not None:
            if self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace) == 'on':
                # Connected
                if self.charger_data.charger_switch is not None:
                    if self.ADapi.get_state(self.charger_data.charger_switch, namespace = self.namespace) == 'on':
                        return 'Charging'
                    elif self.kWhRemaining() > 0:
                        return 'Stopped'
                    else:
                        return "Complete"
                return 'Stopped'
            return 'Disconnected'
        return None

    def getChargerPower(self) -> float:
        """ Returns charger power in kWh.
        """
        pwr = self.ADapi.get_state(self.charger_data.charger_power,
            namespace = self.namespace
        )
        try:
            pwr = float(pwr)
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {pwr} Error: {ve}",
                level = 'DEBUG'
            )
            pwr = 0
        return pwr

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors
        """
        self.charger_data.maxChargerAmpere = 32
        self.ADapi.log(
            f"Setting maxChargerAmpere to 32. Needs to set value in child class of charger.",
            level = 'WARNING'
        )

        return True

    def getmaxChargingAmps(self) -> int:
        """ Returns the maximum ampere the car/charger can get/deliver.
        """
        if self.charger_data.maxChargerAmpere == 0:
            return 32
        
        return self.charger_data.maxChargerAmpere

    def updateAmpereCharging(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates the charging ampere value in self.ampereCharging from charging_amps sensor.
        """
        try:
            newAmp = math.ceil(float(new))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get ampere charging. New is {new}. Error {ve}",
                level = 'DEBUG'
            )
            return

        if newAmp >= 0:
            self.charger_data.ampereCharging = newAmp

    def changeChargingAmps(self, charging_amp_change:int = 0) -> None:
        """ Function to change ampere charging +/-
        """
        if charging_amp_change != 0:
            new_charging_amp = self.charger_data.ampereCharging + charging_amp_change
            self.setChargingAmps(charging_amp_set = new_charging_amp)

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        max_available_amps = self.getmaxChargingAmps()
        if charging_amp_set < self.charger_data.min_ampere:
            charging_amp_set = self.charger_data.min_ampere
        elif charging_amp_set > max_available_amps:
            charging_amp_set = max_available_amps
            if self.Car.onboardCharger is not None:
               if self.Car.connectedCharger is not self.Car.onboardCharger:
                    self.Car.onboardCharger.setChargingAmps(charging_amp_set = self.Car.onboardCharger.getmaxChargingAmps())

        stack = inspect.stack() # Check if called from child
        if stack[1].function != 'setChargingAmps':
            self.charger_data.ampereCharging = charging_amp_set
            self.ADapi.call_service('number/set_value',
                value = self.charger_data.ampereCharging,
                entity_id = self.charger_data.charging_amps,
                namespace = self.namespace
            )
        return charging_amp_set

    def Charger_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected
        """
        self.ADapi.log(
            f"Charger_ChargeCableConnected not implemented in parent class for {self.charger}",
            level = 'WARNING'
        )

    def startCharging(self) -> bool:
        """ Starts charger.
            Parent class returns boolen to child if ready to start charging.
        """
        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger):
            self.checkCharging_handler = None
        if self.doNotStartMe:
            return False
        self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

        # Calculations for battery size:
        if (
            self.Car is not None
            and self.charger_data.session_energy is not None
        ):
            if (
                float(self.ADapi.get_state(self.charger_data.session_energy, namespace = self.namespace)) < 4
                and self.Car.car_data.battery_sensor is not None
            ):
                self.pct_start_charge = float(self.ADapi.get_state(self.Car.car_data.battery_sensor, namespace = self.namespace))

        CHARGE_SCHEDULER.markAsCharging(self.Car.vehicle_id)
        stack = inspect.stack() # Check if called from child
        if stack[1].function == 'startCharging':
            return True
        else:
            self.ADapi.call_service('switch/turn_on',
                entity_id = self.charger_data.charger_switch,
                namespace = self.namespace,
            )
        return False

    def stopCharging(self, force_stop:bool = False) -> bool:
        """ Stops charger.
            Parent class returns boolen to child if able to stop charging.
        """
        if self.Car is not None:
            if self.Car.dontStopMeNow() and not force_stop:
                return False
        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        if self.getChargingState() in ('Charging', 'Starting'):
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

            stack = inspect.stack() # Check if called from child
            if stack[1].function != 'stopCharging':
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.charger_data.charger_switch,
                    namespace = self.namespace,
                )
        return True

    def checkIfChargingStarted(self, kwargs) -> bool:
        """ Check if charger was able to start.
        """
        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        if not self.getChargingState() in ('Charging', 'Complete', 'Disconnected'):
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

            stack = inspect.stack() # Check if called from child
            if stack[1].function in ('startCharging', 'checkIfChargingStarted'):
                return False
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.charger_data.charger_switch,
                    namespace = self.namespace,
                )
        return True

    def checkIfChargingStopped(self, kwargs) -> bool:
        """ Check if charger was able to stop.
        """
        if self.Car is not None:
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
            if self.Car.dontStopMeNow():
                return True
            if self.getChargingState() == 'Charging':
                self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

                stack = inspect.stack() # Check if called from child
                if stack[1].function in ('stopCharging', 'checkIfChargingStopped'):
                    return False
                else:
                    self.ADapi.call_service('switch/turn_off',
                        entity_id = self.charger_data.charger_switch,
                        namespace = self.namespace,
                    )

        return True

    def ChargingStarted(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger started charging. Check if controlling car and if chargetime has been set up
        """
        # Calculations for battery size. Also calculate if charging elsewhere.
        if (
            self.Car is not None
            and self.charger_data.session_energy is not None
            and self.pct_start_charge == 100
        ):
            if (
                float(self.ADapi.get_state(self.charger_data.session_energy, namespace = self.namespace)) < 4
                and self.Car.car_data.battery_sensor is not None
            ):
                self.pct_start_charge = float(self.ADapi.get_state(self.Car.car_data.battery_sensor, namespace = self.namespace))

            if  (
                self.Car.isConnected() and
                self.kWhRemaining() > 0
            ):
                # Update volts and phases on charging started
                self.setVolts()
                self.setPhases()
                self.setVoltPhase(
                    volts = self.charger_data.volts,
                    phases = self.charger_data.phases
                )

                if not CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id):
                    self.Car.findNewChargeTime()

        if self.Car.connectedCharger is None and self.Car.isConnected():
            if not self.findCarConnectedToCharger():
                return

    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger stopped charging.
        """
        self._CleanUpWhenChargingStopped()

    def _updateMaxkWhCharged(self, session: float):
        if self.Car.car_data.max_kWh_charged < session:
            self.Car.car_data.max_kWh_charged = session

    def _calculateBatterySize(self, session: float):
        battery_sensor = getattr(self.Car.car_data, 'battery_sensor', None)
        battery_reg_counter = getattr(self.Car.car_data, 'battery_reg_counter', 0)

        if battery_sensor is not None and self.pct_start_charge < 90:
            pctCharged = float(self.ADapi.get_state(battery_sensor, namespace=self.namespace)) - self.pct_start_charge

            if pctCharged > 35:
                self._updateBatterySize(session, pctCharged, battery_reg_counter)
            elif pctCharged > 10 and self.Car.car_data.battery_size == 100 and battery_reg_counter == 0:
                self.Car.car_data.battery_size = (session / pctCharged)*100

    def _updateBatterySize(self, session: float, pctCharged: float, battery_reg_counter: int):
        if battery_reg_counter == 0:
            avg = round((session / pctCharged) * 100, 2)
        else:
            avg = round(
                ((self.Car.car_data.battery_size * battery_reg_counter) + (session / pctCharged) * 100)
                / (battery_reg_counter + 1),
                2
            )

        self.Car.car_data.battery_reg_counter += 1

        if self.Car.car_data.battery_reg_counter > 100:
            self.Car.car_data.battery_reg_counter = 10

        self.ADapi.log(
            f"pct Charged for {self.Car.carName} is {pctCharged}. kWh: {round(session,2)}. Est battery size: {round((session / pctCharged)*100,2)}"
            f"Old calc: {self.Car.car_data.battery_size}. counter: {self.Car.car_data.battery_reg_counter}. New avg: {avg}"
        )

        self.Car.car_data.battery_size = avg

    def _CleanUpWhenChargingStopped(self) -> None:
        """ Charger stopped charging. """
        if self.Car is not None:
            if self.Car.connectedCharger is self:
                if self.getChargingState() in ('Complete', 'Disconnected'):
                    self.Car._handleChargeCompletion()
                    if self.charger_data.session_energy:
                        session = float(self.ADapi.get_state(self.charger_data.session_energy, namespace=self.namespace))
                        self._updateMaxkWhCharged(session)
                        self._calculateBatterySize(session)

                self.pct_start_charge = 100
                self.charger_data.ampereCharging = 0

    def setVoltPhase(self, volts, phases) -> None:
        """ Helper for calculations on chargespeed.
            VoltPhase is a make up name and simplification to calculate chargetime based on remaining kwh to charge
            230v 1 phase,
            266v is 3 phase on 230v without neutral (supported by tesla among others)
            687v is 3 phase on 400v with neutral.
        """
        if (
            phases > 1
            and self.charger_data.volts > 200
            and self.charger_data.volts < 250
        ):
            self.charger_data.voltPhase = 266

        elif (
            phases == 3
            and self.charger_data.volts > 300
        ):
            self.charger_data.voltPhase = 687

        elif (
            phases == 1
            and self.charger_data.volts > 200
            and self.charger_data.volts < 250
        ):
            self.charger_data.voltPhase = volts


    def idle_currentListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens for changes to idle_current switch. """
        if new == 'on':
            self.idle_current = True
        elif new == 'off':
            self.idle_current = False

    def notify_charge_now_or_kWhRemain(self, carName):
        """ Sends notification to ask to charge car Now or input kWh remaining. """
        data = {
            'tag' : carName,
            'actions' : [{ 'action' : 'chargeNow'+str(self.charger), 'title' : f'Charge {carName} Now' },
                         { 'action' : 'kWhremaining'+str(self.charger),
                           'title' : 'Input expected kWh to charge',
                           "behavior": "textInput"
                           } ]
            }
        NOTIFY_APP.send_notification(
                    message = f"Car connected. Select options.",
                    message_title = f"{self.charger}",
                    message_recipient = RECIPIENTS,
                    also_if_not_home = True,
                    data = data
                )

    def guestChargingListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Disables logging and schedule if guest is using charger.
        """
        self.guestCharging = new == 'on'
        if (
            new == 'on'
            and old == 'off'
        ):
            self._addGuestCar()
            self.notify_charge_now_or_kWhRemain(self.Car.carName)

        elif (
            new == 'off'
            and old == 'on'
        ):
            if self.Car is not None:
                if self.Car.carName == 'guestCar':
                    self.stopCharging()
                    self.Car._handleChargeCompletion()
                    self.remove_car_from_list(self.Car.carName)
                    self.Car = None
                elif (
                    self.Car.isConnected()
                    and self.kWhRemaining() > 0
                ):
                    self.Car.findNewChargeTime()
            else:
                self.stopCharging()

    def _addGuestCar(self):
        """
        Create a “dumb” guest car.
        """
        guest_car_cfg = CarData()

        guest_car = Car(
            api=self.ADapi,
            namespace=self.namespace,
            carName='guestCar',
            vehicle_id = 'guestCar',
            car_data=guest_car_cfg
        )

        self.add_car_to_list(guest_car)
        self.Car = guest_car
        self.Car.connectedCharger = self
        self.Car.car_data.kWh_remain_to_charge = 10

    def add_car_to_list(self, car_instance):
        """Method to add a car to the list."""
        self._cars.append(car_instance)

    def remove_car_from_list(self, carName):
        """Method to remove a car from the list."""
        self._cars = [car for car in self._cars if car.carName != carName]

class Car:
    """ Car parent class
    Set variables in childclass before init:
        self.vehicle_id:str # Unik ID to separate chargers. CarName will be used if not set

    Set variables in childclass after init if needed:
        self.guestCharging:bool # Defaults to False
        self.Car # Car to charge
    """
    def __init__(self, api,
        namespace:str,
        carName:str, # Name of car
        vehicle_id:str, # ID of car
        car_data,
    ):

        self.ADapi = api
        self.namespace = namespace
        self.car_data = car_data

        self.vehicle_id = vehicle_id
        self.carName = carName

        # Set up when car should be finished charging
        if isinstance(car_data.finish_by_hour, int):
            self.finish_by_hour = int(car_data.finish_by_hour)
        else:
            self.finish_by_hour = math.ceil(float(self.ADapi.get_state(car_data.finish_by_hour,
                namespace = self.namespace))
            )
            self.ADapi.listen_state(self._finishByHourListen, car_data.finish_by_hour,
                namespace = self.namespace
            )

        # Switch to start charging now
        if isinstance(car_data.charge_now, str):
            self.charge_now_HA_switch:str = car_data.charge_now
            self.charge_now = self.ADapi.get_state(car_data.charge_now, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._chargeNowListen, car_data.charge_now,
                namespace = self.namespace
            )
        else:
            self.charge_now:bool = car_data.charge_now

        # Switch to charge only on solar
        if isinstance(car_data.charge_only_on_solar, str):
            self.charge_only_on_solar = self.ADapi.get_state(car_data.charge_only_on_solar, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._charge_only_on_solar_Listen, car_data.charge_only_on_solar,
                namespace = self.namespace
            )
        else:
            self.charge_only_on_solar:bool = car_data.charge_only_on_solar

        # Helper Variables:
        self.charging_on_solar:bool = False

        # Charger objects:
        self.connectedCharger:object = None
        self.onboardCharger:object = None

        if self.car_data.charge_limit is not None:
            self.car_data.kWh_remain_to_charge:float = self.kWhRemaining()
            self.ADapi.listen_state(self.ChargeLimitChanged, self.car_data.charge_limit,
                namespace = self.namespace
            )
        else:
            self.car_data.kWh_remain_to_charge:float = -2

        # Set up listeners
        if self.car_data.charger_sensor is not None:
            #self.ADapi.listen_state(self.car_Car_ChargeCableConnected, self.car_data.charger_sensor,
            #    namespace = self.namespace,
            #    new = 'on'
            #)
            self.ADapi.listen_state(self.car_ChargeCableDisconnected, self.car_data.charger_sensor,
                namespace = self.namespace,
                new = 'off'
            )

        self.find_Chargetime_Whenhome_handler = None

        """ TODO Departure / Maxrange handling: To be re-written before implementation
            Set a departure time in a HA datetime sensor for when car will be finished charging to 100%,
            to have a optimal battery when departing.
        """
        self.max_range_handler = None
        self.start_charging_max = None

        """ Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, namespace = self.namespace, duration = 5 )
        """

        """ End initialization Car Class
        """

        # Functions on when to charge Car
    def _finishByHourListen(self, entity, attribute, old, new, kwargs) -> None:
        self.finish_by_hour = math.ceil(float(new))
        if self.kWhRemaining() > 0:
            self.findNewChargeTime()

    def _chargeNowListen(self, entity, attribute, old, new, kwargs) -> None:
        self.charge_now = new == 'on'
        if (
            new == 'on'
            and old == 'off'
            and self.connectedCharger is not None
        ):
            self.startCharging()
        elif (
            new == 'off'
            and old == 'on'
            and self.kWhRemaining() > 0
        ):
            self.findNewChargeTime()

    def turnOff_Charge_now(self) -> None:
        """ Turns smart charging on again. """
        if isinstance(self.charge_now, str):
            self.ADapi.call_service('input_boolean/turn_off',
                entity_id = self.charge_now_HA_switch,
                namespace = self.namespace,
            )
        self.charge_now = False

    def _charge_only_on_solar_Listen(self, entity, attribute, old, new, kwargs) -> None:
        self.charge_only_on_solar = new == 'on'
        if new == 'on':
            self._handleChargeCompletion()
        elif new == 'off':
            if self.kWhRemaining() > 0:
                self.findNewChargeTime()

        # Functions for charge times
    def findNewChargeTimeAt(self, kwargs) -> None:
        """ Function to run when initialized and when new prices arrive. """
        self.findNewChargeTime()

    def _find_Chargetime_Whenhome(self, entity, attribute, old, new, kwargs) -> None:
        self.findNewChargeTime()
        if cancel_listen_handler(ADapi = self.ADapi, handler = self.find_Chargetime_Whenhome_handler, name = self.carName):
            self.find_Chargetime_Whenhome_handler = None

    def findNewChargeTime(self) -> None:
        """ Find new chargetime for car. """
        now = self.ADapi.datetime(aware=True)
        if self.dontStopMeNow():
            return
        if self.charging_scheduled_with_updated_data():
            return
        startcharge = False
        charger_state = self.getCarChargerState()
        if self.connectedCharger is None:
            if charger_state != 'NoPower':
                self.connectedCharger = self.onboardCharger
            else:
                return
        if (
            self.isConnected() and charger_state not in ('Disconnected', 'Complete') or
            self.connectedCharger.getChargingState() not in ('Disconnected', 'Complete')
        ):
            if (
                not self.charging_on_solar
                and not self.charge_only_on_solar
            ):
                cancel_timer_handler(ADapi = self.ADapi, handler = CHARGE_SCHEDULER.informHandler, name = self.carName)
                startcharge = CHARGE_SCHEDULER.queueForCharging(
                    vehicle_id = self.vehicle_id,
                    kWhRemaining = self.car_data.kWh_remain_to_charge,
                    maxAmps = self.getCarMaxAmps(),
                    voltPhase = self.connectedCharger.charger_data.voltPhase,
                    finish_by_hour = self.finish_by_hour,
                    priority = self.car_data.priority,
                    name = self.carName
                )
                CHARGE_SCHEDULER.informHandler = self.ADapi.run_in(CHARGE_SCHEDULER.notifyChargeTime, 3)

                if (
                    charger_state == 'Charging'
                    and not startcharge
                ):
                    start, stop = CHARGE_SCHEDULER.getChargingTime(vehicle_id = self.vehicle_id)
                    match start:
                        case None:
                            if not CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.vehicle_id):
                                self.stopCharging()
                        case _ if start - timedelta(minutes=12) > now:
                            self.stopCharging()
                elif (
                    charger_state in ('NoPower', 'Stopped')
                    and startcharge
                ):
                    self.startCharging()

        elif self.getLocation() != 'home':
            self.find_Chargetime_Whenhome_handler = self.ADapi.listen_state(self._find_Chargetime_Whenhome, self.car_data.location_tracker,
                namespace = self.namespace,
                new = 'home',
                oneshot = True
            )

    def removeFromQueue(self) -> None:
        """ Removes car from chargequeue
        """
        CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)

    def _handleChargeCompletion(self):
        self.turnOff_Charge_now()
        self.removeFromQueue()
        self.car_data.kWh_remain_to_charge = -1

    def charging_scheduled_with_updated_data(self) -> bool:
        """ returns if car has charging scheduled
        """
        return CHARGE_SCHEDULER.charging_scheduled_with_updated_data(vehicle_id = self.vehicle_id,
                                                     kWhRemaining = self.car_data.kWh_remain_to_charge,
                                                     finish_by_hour = self.finish_by_hour)

        # Functions to react to car sensors
    def car_Car_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable connected for car.
        """
        pass

    def car_ChargeCableDisconnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable disconnected for car.
        """
        if self.connectedCharger is not None:
            if self.connectedCharger.getChargingState() == 'Disconnected':
                if self.connectedCharger.Car.onboardCharger is self.connectedCharger:
                    self.connectedCharger._CleanUpWhenChargingStopped()

            if self.max_range_handler is not None:
                # TODO: Program charging to max at departure time.
                # @HERE: Call a function that will cancel handler when car is disconnected
                #self.ADapi.run_in(self.resetMaxRangeCharging, 1)
                self.ADapi.log(f"{self.charger} Has a max_range_handler. Not Programmed yet", level = 'DEBUG')

    def isConnected(self) -> bool:
        """ Returns True if charge cable is connected.
        """
        if self.getLocation() == 'home':
            if self.car_data.charger_sensor is not None:
                if self.ADapi.get_state(self.car_data.charger_sensor, namespace = self.namespace) == 'on':
                    return True
            if self.connectedCharger is not None:
                return self.connectedCharger.getChargingState() not in ['Disconnected']
            return True
        return False

    def asleep(self) -> bool:
        """ Returns True if car is sleeping.
        """
        if self.car_data.asleep_sensor and self._polling_of_data():
            return self.ADapi.get_state(self.car_data.asleep_sensor, namespace = self.namespace) == 'on'
        return False

    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        pass

    def isOnline(self) -> bool:
        """ Returns True if car in online.
        """
        if self.car_data.online_sensor:
            return self.ADapi.get_state(self.car_data.online_sensor, namespace = self.namespace) == 'on'
        return True

    def getLocation(self) -> str:
        """ Returns location of the vehicle based on sones from Home Assistant.
        """
        if self.car_data.location_tracker:
            return self.ADapi.get_state(self.car_data.location_tracker, namespace = self.namespace)
        return 'home'

    def SoftwareUpdates(self) -> bool:
        """ Return True if car is updating software.
        """
        return False

    def forceAPIupdate(self) -> None:
        """ Function to force a new API pull on the vehicle.
        """
        pass

    def _polling_of_data(self) -> bool:
        """ Polling of data is a switch that disables communication with the car when switched off.
            TODO: Implement checks to not control/wake car if this is off.
        """
        if self.car_data.polling_switch:
            return self.ADapi.get_state(self.car_data.polling_switch, namespace = self.namespace) == 'on'
        return True

    def recentlyUpdated(self) -> bool:
        """ Returns True if car data is updated within the last 12 minutes.
        """
        if self.car_data.data_last_update_time:
            last_update = self.ADapi.convert_utc(self.ADapi.get_state(self.car_data.data_last_update_time,
                namespace = self.namespace)
            )
            now = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time < timedelta(minutes = 12):
                return False
        return True

    def dontStopMeNow(self) -> bool:
        """ Returns true if charger should not or can not be stopped.
        """
        if (
            self.charge_now
            or self.charging_on_solar
            or self.SoftwareUpdates()
        ):
            return True
        return False

    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge car from battery sensor/size and charge limit.
        """
        if self.car_data.charge_limit:
            battery_pct = self.car_battery_soc()
            limit_pct = self.ADapi.get_state(self.car_data.charge_limit, namespace = self.namespace)
            try:
                battery_pct = float(battery_pct)
                limit_pct = float(limit_pct)
            except (ValueError, TypeError) as ve:
                try:
                    kWhRemain = float(self.car_data.kWh_remain_to_charge)
                except Exception:
                    kWhRemain = -1
                    self.car_data.kWh_remain_to_charge = -1
                    if self.getLocation() in ('home', 'unknown'):
                        self.wakeMeUp() # Wake up car to get proper value.
                else:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.car_data.kWh_remain_to_charge}. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                return kWhRemain
            except Exception as e:
                self.ADapi.log(
                    f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                    f"Return existing value: {self.car_data.kWh_remain_to_charge}. Exception: {e}",
                    level = 'WARNING'
                )
                return self.car_data.kWh_remain_to_charge

            if battery_pct < limit_pct:
                percentRemainToCharge = limit_pct - battery_pct
                self.car_data.kWh_remain_to_charge = (percentRemainToCharge / 100) * self.car_data.battery_size
            else:
                self.car_data.kWh_remain_to_charge = -1
            return self.car_data.kWh_remain_to_charge
        return -2

    def car_battery_soc(self) -> int:
        """ Returns battery State of charge.
        """
        SOC = -1
        if self.car_data.battery_sensor:
            try:
                SOC = float(self.ADapi.get_state(self.car_data.battery_sensor, namespace = self.namespace))
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} Not able to get SOC. Trying alternative calculations. ValueError: {ve}",
                    level = 'DEBUG'
                )
            except Exception as e:
                self.ADapi.log(
                    f"{self.carName} Not able to get SOC. Trying alternative calculations. Exception: {e}",
                    level = 'WARNING'
                )
        if SOC == -1:
            try:
                kWhRemain = float(self.car_data.kWh_remain_to_charge)
            except Exception:
                kWhRemain = -1
            if kWhRemain == -1:
                SOC = 100
            else: # TODO: Find a way to calculate
                SOC = 10
        return SOC

    def changeChargeLimit(self, chargeLimit:int = 100 ) -> None:
        """ Change charge limit.
        """
        self.car_data.old_charge_limit = self.ADapi.get_state(self.car_data.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.car_data.charge_limit,
            namespace = self.namespace
        )

    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge limit changed.
        """
        if self.connectedCharger is not None:
            try:
                new = int(new)
                self.car_data.old_charge_limit = int(old)
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            try:
                battery_state = float(self.ADapi.get_state(self.car_data.battery_sensor,
                    namespace = self.namespace)
                )
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} battery state error {battery_state} when setting new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            if battery_state > float(new):
                self.connectedCharger._CleanUpWhenChargingStopped()

            elif self.kWhRemaining() > 0:
                self.findNewChargeTime()

    def isChargingAtMaxAmps(self) -> bool:
        """ Returns True if the charging speed is at maximum.
        """
        if self.car_data.car_limit_max_ampere is None:
            return self.connectedCharger.getmaxChargingAmps() <= self.connectedCharger.charger_data.ampereCharging
        return self.car_data.car_limit_max_ampere <= self.connectedCharger.charger_data.ampereCharging

    def getCarMaxAmps(self) -> int:
        if self.car_data.car_limit_max_ampere is None:
            return self.connectedCharger.getmaxChargingAmps()
        return self.car_data.car_limit_max_ampere

    def getCarChargerState(self) -> str:
        """ Returns the charging state of the car.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        if self.car_data.charger_sensor is not None:
            try:
                state = self.ADapi.get_state(self.car_data.charger_sensor,
                    namespace = self.namespace,
                    attribute = 'charging_state'
                )
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.charger} Could not get attribute = 'charging_state' from: "
                    f"{self.ADapi.get_state(self.car_data.charger_sensor, namespace = self.namespace)} "
                    f"Error: {ve}",
                    level = 'DEBUG'
                )
            else:
                if state == 'Starting':
                    state = 'Charging'
                return state
        
        if self.connectedCharger is not None:
            return self.connectedCharger.getChargingState()
        return None

    def startCharging(self) -> None:
        """ Starts controlling charger.
        """
        if (
            self.getCarChargerState() == 'Stopped'
            or self.connectedCharger.getChargingState() in {'awaiting_start', 'Stopped'}
        ):
            self.connectedCharger.startCharging()
        elif self.getCarChargerState() == 'Complete':
            self.connectedCharger._CleanUpWhenChargingStopped()

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops controlling charger.
        """
        if self.connectedCharger.getChargingState() in ('Charging', 'Starting'):
            self.connectedCharger.stopCharging(force_stop = force_stop)

class Tesla_charger(Charger):
    """ Tesla
        Child class of Charger. Uses Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS.
    
        Selection of possible commands to API
            self.ADapi.call_service('tesla_custom/api', command = 'STOP_CHARGE', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_LIMIT', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'percent': '70'} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_MAX', parameters = { 'path_vars': {'vehicle_id': self.charger_id}} )  #?
            self.ADapi.call_service('tesla_custom/api', command = 'CHARGING_AMPS', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': '25'} )

    """

    def __init__(self, api,
        Car,
        namespace:str,
        charger:str,
        charger_data,
    ):

        self.charger_id = api.get_state(Car.car_data.online_sensor,
            namespace = Car.namespace,
            attribute = 'id'
        )

        self._cars:list = [Car]

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_data = charger_data,
        )

        self.Car = Car
        self.Car.onboardCharger = self

        self.charger_data.min_ampere = 5
        self.noPowerDetected_handler = None

        self.ADapi.listen_state(self.ChargingStarted, self.charger_data.charger_switch,
            namespace = self.namespace,
            new = 'on',
            duration = 10
        )
        self.ADapi.listen_state(self.ChargingStopped, self.charger_data.charger_switch,
            namespace = self.namespace,
            new = 'off'
        )
        self.ADapi.listen_state(self.Charger_ChargeCableConnected, self.charger_data.charger_sensor,
            namespace = self.namespace
        )

        self.ADapi.listen_state(self.MaxAmpereChanged, self.charger_data.charging_amps,
            namespace = self.namespace,
            attribute = 'max',
            duration = 30
        )
        """ End initialization Tesla Charger Class
        """

    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        try:
            state = self.ADapi.get_state(self.charger_data.charger_sensor,
                namespace = self.namespace,
                attribute = 'charging_state'
            )
            if state == 'Starting':
                state = 'Charging'
        except (ValueError, TypeError) as ve:
            return None
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get attribute = 'charging_state' from: "
                f"{self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)} "
                f"Exception: {e}",
                level = 'WARNING'
            )
            return None
        # Set as connected charger if restarted after cable connected.
        if (
            state == 'Stopped'
            and self.Car.connectedCharger is None
        ):
            self.Car.connectedCharger = self

        return state

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors
        """
        if (
            self.Car.isConnected()
            and self.getChargingState() not in ('Disconnected', 'Complete')
        ):
            if self.Car.connectedCharger is self:
                try:
                    maxAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                        namespace = self.namespace,
                        attribute = 'max'))
                    )
                    self.charger_data.maxChargerAmpere = maxAmpere

                except (ValueError, TypeError) as ve:
                    self.ADapi.log(
                        f"{self.charger} Could not get maxChargingAmps. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                    return False

            # Update Voltphase calculations
            try:
                self.charger_data.volts = math.ceil(float(self.ADapi.get_state(self.charger_data.charger_power,
                    namespace = self.namespace,
                    attribute = 'charger_volts'
                )))
            except (ValueError, TypeError):
                pass
            try:
                self.charger_data.phases = int(self.ADapi.get_state(self.charger_data.charger_power,
                    namespace = self.namespace,
                    attribute = 'charger_phases'
                ))
            except (ValueError, TypeError):
                pass
            return True
        return False

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        self.charger_data.ampereCharging = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('tesla_custom/api',
            namespace = self.namespace,
            command = 'CHARGING_AMPS',
            parameters = {'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': self.charger_data.ampereCharging}
        )

    def MaxAmpereChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Detects if smart charger (Easee) increases ampere available to charge and updates internal charger to follow.
        """
        try:
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                namespace = self.namespace))
            )
            if float(new) > chargingAmpere:
                if (
                    self.Car.connectedCharger is not self
                    and self.Car.connectedCharger is not None
                ):
                    self.setChargingAmps(charging_amp_set = self.getmaxChargingAmps())

        except (ValueError, TypeError):
            pass
        else:
            if float(new) > self.charger_data.maxChargerAmpere:
                self.charger_data.maxChargerAmpere = new

    def Charger_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected.
        """
        if cancel_listen_handler(ADapi = self.ADapi, handler = self.noPowerDetected_handler, name = self.charger):
            self.noPowerDetected_handler = None

        if self.Car is not None:
            if (
                (self.Car.connectedCharger is None
                or self.Car.connectedCharger is self)
                and self.Car.isConnected()
                and new == 'on'
                and self.kWhRemaining() > 0
            ):
                if self.getChargingState() != 'NoPower':
                    if self.Car.connectedCharger is None:
                        if not self.findCarConnectedToCharger():
                            return
                    # Listen for changes made from other connected chargers
                    self.noPowerDetected_handler = self.ADapi.listen_state(self.noPowerDetected, self.charger_data.charger_sensor,
                        namespace = self.namespace,
                        attribute = 'charging_state',
                        new = 'NoPower'
                    )

                    self.Car.findNewChargeTime()

                elif self.getChargingState() == 'NoPower':
                    self.setChargingAmps(charging_amp_set = self.getmaxChargingAmps())

    def noPowerDetected(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts when chargecable is connected but no power is given.
            This indicates that a smart connected charger has cut the power.
        """
        if self.Car.connectedCharger is self:
            self.Car.connectedCharger = None

    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger stopped charging.
        """
        self._CleanUpWhenChargingStopped()
        if self.Car.connectedCharger is self:
            self.setChargingAmps(charging_amp_set = self.charger_data.min_ampere) # Set to minimum amp for preheat.

    def startCharging(self) -> None:
        """ Starts charger.
        """
        if super().startCharging():
            self.ADapi.create_task(self.start_Tesla_charging())

    async def start_Tesla_charging(self):
        if self.Car is not None:
            try:
                await self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                await self.Car._force_API_update()
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception: {e}", level = 'WARNING')

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops charger.
        """
        if super().stopCharging(force_stop = force_stop):
            self.ADapi.create_task(self.stop_Tesla_charging())

    async def stop_Tesla_charging(self):
        if self.Car is not None:
            try:
                await self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                await self.Car._force_API_update()
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging: {e}", level = 'WARNING')

    def checkIfChargingStarted(self, kwargs) -> None:
        """ Check if charger was able to start.
        """
        if (
            self.getChargingState() == 'NoPower'
            and self.Car.connectedCharger is self
        ):
            self.Car.connectedCharger = None

        elif not super().checkIfChargingStarted(0):
            self.ADapi.create_task(self.start_Tesla_charging())

    def checkIfChargingStopped(self, kwargs) -> None:
        """ Check if charger was able to stop.
        """
        if not super().checkIfChargingStopped(0):
            self.ADapi.create_task(self.stop_Tesla_charging())

    def setVolts(self):
        if self.Car.isConnected():
            try:
                volt = math.ceil(float(self.ADapi.get_state(self.charger_data.charger_power,
                namespace = self.namespace,
                attribute = 'charger_volts'))
            )
            except (ValueError, TypeError):
                pass
            else:
                if volt > 0:
                    self.charger_data.volts = volt

    def setPhases(self):
        if self.Car.isConnected():
            try:
                phase = int(self.ADapi.get_state(self.charger_data.charger_power,
                namespace = self.namespace,
                attribute = 'charger_phases')
            )
            except (ValueError, TypeError):
                pass
            else:
                if phase > 0:
                    self.charger_data.phases = phase


class Tesla_car(Car):

    def __init__(self, api,
        namespace,
        carName,
        car_data,
    ):

        self.vehicle_id = api.get_state(car_data.online_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        super().__init__(
            api=api,
            namespace=namespace,
            carName=carName,
            vehicle_id=self.vehicle_id,
            car_data=car_data,
        )

        if self.car_data.destination_location_tracker:
           self.ADapi.listen_state(self.destination_updated, self.car_data.destination_location_tracker,
            namespace = self.namespace
        )

        """ End initialization Tesla Car Class
        """

    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        if self._polling_of_data():
            if self.ADapi.get_state(self.car_data.charger_sensor, namespace = self.namespace) not in ('Complete', 'Disconnected'):
                if (
                    not self.recentlyUpdated()
                    and self.asleep()
                ):
                    self.ADapi.call_service('tesla_custom/api',
                        namespace = self.namespace,
                        command = 'WAKE_UP',
                        parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep' : True}
                    )
                self.forceAPIupdate()

    def SoftwareUpdates(self) -> bool:
        """ Return True if car is updating software.
        """
        if self.ADapi.get_state(self.car_data.software_update, namespace = self.namespace) not in UNAVAIL:
            if self.ADapi.get_state(self.car_data.software_update, namespace = self.namespace, attribute = 'in_progress') != False:
                return True
        return False

    def forceAPIupdate(self) -> None:
        """ Function to force a new API pull on the vehicle.
        """
        if self._polling_of_data():
            self.ADapi.create_task(self._force_API_update())

    async def _force_API_update(self):
        await self.ADapi.call_service('button/press',
            namespace = self.namespace,
            entity_id = self.car_data.force_data_update
        )

    def changeChargeLimit(self, chargeLimit:int = 90 ) -> None:
        """ Change charge limit.
        """
        self.car_data.old_charge_limit = self.ADapi.get_state(self.car_data.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.car_data.charge_limit,
            namespace = self.namespace
        )

    ###------------------------- Destination ------------------------- ###

    def destination_updated(self, entity, attribute, old, new, kwargs) -> None:
        """ Get arrival time if destination == 'home'
            and use estimated battery on arrival to calculate chargetime
        """
        if new == 'home':
            energy_at_arrival= self.ADapi.get_state(self.car_data.arrival_time,
                namespace = self.namespace,
                attribute='Energy at arrival'
            )
            if energy_at_arrival > 0:
                self.car_data.kWh_remain_to_charge = self.car_data.old_charge_limit - energy_at_arrival

                # f"Timedelta: {self.ADapi.datetime(aware=True) - self.ADapi.convert_utc(self.ADapi.get_state(self.car_data.arrival_time, namespace = self.namespace)) + timedelta(minutes=self.ADapi.get_tz_offset())} "

    ###------------------------- Destination ------------------------- ###

class Easee(Charger):
    """ Easee
        Child class of Charger. Uses Easee EV charger component for Home Assistant. https://github.com/nordicopen/easee_hass 
        Easiest installation is via HACS.
    """
    def __init__(self, api,
        cars:list,
        namespace:str,
        charger:str,
        charger_data,
    ):

        self.charger_id:str = api.get_state(charger_data.charger_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        self._cars:list = cars

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_data = charger_data,
        )

        # Minumum ampere if locked to 3 phase
        if self.charger_data.phases == 3:
            self.charger_data.min_ampere = 11

        api.listen_state(self.statusChange, self.charger_data.charger_sensor, namespace = namespace)

        if (
            api.get_state(self.charger_data.charger_sensor, namespace = self.namespace) != 'disconnected'
            and self.Car is None
        ):
            self.findCarConnectedToCharger()


        elif type(Car).__name__ == 'Car':
            api.listen_state(self.reasonChange, reason_for_no_current, namespace = namespace)

        """ End initialization Easee Charger Class
        """

    def compareChargingState(self, car_status:str) -> bool:
        """ Returns True if car and charger match charging state.
        """
        charger_status = self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)
        if charger_status == 'charging':
            return car_status == 'Charging'
        elif charger_status == 'completed':
            return car_status == 'Complete'
        elif charger_status == 'awaiting_start':
            return car_status == 'NoPower'
        elif charger_status == 'disconnected':
            return car_status == 'Disconnected'

        return False

    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        status = self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)
        if status == 'charging':
            return 'Charging'
        elif status == 'completed':
            return 'Complete'
        elif status == 'awaiting_start':
            return 'awaiting_start'
        elif status == 'disconnected':
            if self.Car is not None:
                return 'awaiting_start'
            return 'Disconnected'
        elif not status == 'ready_to_charge':
            self.ADapi.log(f"Status: {status} for {self.charger} is not defined", level = 'WARNING')
        return status

    def statusChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to changes in state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
        """
        if old == 'disconnected':
            if self.Car is None:
                if self.findCarConnectedToCharger():
                    if self.Car is not None:
                        self.kWhRemaining()
                        self.Car.findNewChargeTime()
            return

        elif (
            new != 'disconnected'
            and old == 'completed'
        ):
            if self.Car is not None:
                if (
                    self.kWhRemaining() > 2
                    and not self.Car.charging_scheduled_with_updated_data()
                ):
                    self.Car.findNewChargeTime()

                if (
                    CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id)
                    or self.idle_current # Preheating
                ):
                    return

            self.stopCharging()

        elif (
            new == 'charging'
            or new == 'ready_to_charge'
        ):
            if self.Car is None:
                if not self.findCarConnectedToCharger():
                    self.stopCharging()
                    return
            if self.Car is not None:
                if (
                    not self.Car.charging_scheduled_with_updated_data()
                ):
                    self.kWhRemaining() # Update kWh remaining to charge
                    self.Car.findNewChargeTime()

                elif not CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id):
                    self.stopCharging()

        elif new == 'completed':
            if self.Car is not None:
                self._CleanUpWhenChargingStopped()

        elif new == 'disconnected':
            self.ADapi.run_in(self._check_if_still_disconnected, 720)

        elif new == 'awaiting_start':
            self._CleanUpWhenChargingStopped()
            if self.Car is None:
                self.findCarConnectedToCharger()

    def _check_if_still_disconnected(self, kwargs) -> None:
        if self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace) == 'disconnected':
            if self.Car is not None:
                self._CleanUpWhenChargingStopped()
                self.Car.connectedCharger = None
                self.Car = None
        elif self.Car is not None: # Check if new car is connected.
            if self.Car.getCarChargerState() == 'Disconnected':
                self._CleanUpWhenChargingStopped()
                self.Car.connectedCharger = None
                self.Car = None
                self.findCarConnectedToCharger()
        elif self.Car is None: # New car connected.
            self.findCarConnectedToCharger()


    def reasonChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to reasonChange in Easee charger.
            Easee reason can be:
            'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' /
            'limited_by_local_adjustment' / 'limited_by_car' / 'car_not_charging' /  from reason_for_no_current
        """
        if (
            new == 'limited_by_car'
        ):
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                namespace = self.namespace))
            )
            if (
                self.Car.car_data.car_limit_max_ampere != chargingAmpere
                and chargingAmpere >= 6
            ):
                self.Car.car_data.car_limit_max_ampere = chargingAmpere

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors
        """
        try:
            self.charger_data.maxChargerAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.max_charger_limit,
                namespace = self.namespace))
            )
        except (ValueError, TypeError):
            return False

        return True

    def setVolts(self):
        try:
            self.charger_data.volts = math.ceil(float(self.ADapi.get_state(self.charger_data.voltage,
                namespace = self.namespace))
            )
        except (ValueError, TypeError):
            return

    def setPhases(self):
        try:
            self.charger_data.phases = int(self.ADapi.get_state(self.charger_data.charger_sensor,
            namespace = self.namespace,
            attribute = 'config_phaseMode')
        )
        except (ValueError, TypeError):
            self.charger_data.phases = 1

    def setChargingAmps(self, charging_amp_set:int = 16) -> None:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        if (
            self.charger_data.ampereCharging != charging_amp_set
            and self.charger_data.ampereCharging != charging_amp_set -1
        ):
            self.ADapi.call_service('easee/set_charger_dynamic_limit',
                namespace = self.namespace,
                current = charging_amp_set,
                charger_id = self.charger_id
            )

    def startCharging(self) -> None:
        """ Starts charger.
        """
        if super().startCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'resume',
                    charger_id = self.charger_id
                )
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception {e}", level = 'WARNING')

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops charger.
        """
        if super().stopCharging(force_stop = force_stop):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'pause',
                    charger_id = self.charger_id
                )
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging. Exception: {e}", level = 'WARNING')

    def checkIfChargingStarted(self, kwargs) -> None:
        """ Check if charger was able to start.
        """
        if not super().checkIfChargingStarted(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'resume',
                    charger_id = self.charger_id
                    )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )

    def checkIfChargingStopped(self, kwargs) -> None:
        """ Check if charger was able to stop.
        """
        if not super().checkIfChargingStopped(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'pause',
                    charger_id = self.charger_id
                    )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )

class Heater:
    """ Heater
        Parent class for on_off_switch and electrical heaters
        Sets up times to save/spend based on electricity price
    """
    def __init__(self,
        api,
        namespace,
        heater,
        heater_data
    ):
        self.ADapi = api
        self.namespace = namespace
        self.heater = heater
        self.heater_data = heater_data

        # Vacation setup
        if self.heater_data.vacation is not None and self.ADapi.entity_exists(self.heater_data.vacation, namespace = self.namespace):
            self.away_state = self.ADapi.get_state(self.heater_data.vacation, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._awayStateListen_Heater, self.heater_data.vacation,
                namespace = self.namespace
            )
        else:
            self.away_state = False

        # Automate setup
        if isinstance(self.heater_data.automate, str):
            self.automate = self.ADapi.get_state(self.heater_data.automate, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self.automateStateListen, self.heater_data.automate,
                namespace = self.namespace
            )
        elif isinstance(self.heater_data.automate, bool):
            self.automate = self.heater_data.automate

        # Consumption data
        self.reset_continuous_hours:bool = False
        self.time_to_spend:list = []
        self.kWh_consumption_when_turned_on:float = 0.0
        self.isSaveState:bool = False
        self.isOverconsumption:bool = False
        self.increase_now:bool = False
        self.last_reduced_state = self.ADapi.datetime(aware=True) - timedelta(minutes=20)

        # Handlers
        self._consumption_stops_register_usage_handler = None
        self.checkConsumption_handler = None

        # Helpers used on vacation
        self.HeatAt = None
        self.EndAt = None
        self.price:float = 0

        # Finding data if not set to persistent
        if self.heater_data.normal_power < 30:
            self.ADapi.listen_state(self._set_normal_power, self.heater_data.consumptionSensor,
                constrain_state=lambda x: float(x) > 30,
                oneshot = True,
                namespace = self.namespace
            )
    
    def _set_normal_power(self, entity, attribute, old, new, kwargs) -> None:
        if float(new) < 30:
            self.heater_data.normal_power = float(new)

    def _awayStateListen_Heater(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state
        """
        self.away_state = new == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def automateStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes to automate switch and requests heater to set new state if automation is turned back on
        """
        self.automate = new == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on ELECTRICITYPRICE.find_times_to_save()
            Will also find cheapest times to heat hotwater boilers and other on/off switches when on vacation.
        """
        now = self.ADapi.datetime(aware=True)
        self.heater_data.time_to_save = ELECTRICITYPRICE.find_times_to_save(
            pricedrop = self.heater_data.pricedrop,
            max_continuous_hours = self.heater_data.max_continuous_hours,
            on_for_minimum = self.heater_data.on_for_minimum,
            pricedifference_increase = self.heater_data.pricedifference_increase,
            reset_continuous_hours = self.reset_continuous_hours,
            previous_save_hours = self.heater_data.time_to_save
        )
        if (
            self.away_state
            and (self.HeatAt is None
            or ELECTRICITYPRICE.tomorrow_valid)
        ):
            self.HeatAt, est_end, self.EndAt, self.price = ELECTRICITYPRICE.get_Continuous_Cheapest_Time(
                hoursTotal = 2,
                calculateBeforeNextDayPrices = not ELECTRICITYPRICE.tomorrow_valid,
		        finishByHour = 24,
                startBeforePrice = 0.02, 
                stopAtPriceIncrease = 0.01
            )
            if self.HeatAt is not None:
                if self.HeatAt > now:
                    self.ADapi.run_at(self.heater_setNewValues, self.HeatAt)
            if self.EndAt is not None:
                if self.EndAt > now:
                    self.ADapi.run_at(self.heater_setNewValues, self.EndAt)

        elif not self.away_state:
            self.HeatAt = None
            self.EndAt = None

        if self.heater_data.time_to_save:
            for item in self.heater_data.time_to_save:
                if item.end > now:
                    self.ADapi.run_at(self.heater_setNewValues, item.end)
                    if item.start > now:
                        self.ADapi.run_at(self.heater_setNewValues, item.start)

        self.ADapi.run_in(self.heater_setNewValues, 5)

        """Logging purposes to check what hours heater turns off/down to check if behaving as expected"""
        #if self.heater_data.time_to_save:
        #    self.ADapi.log(f"{self.heater} save hours:{ELECTRICITYPRICE.print_peaks(self.heater_data.time_to_save)}")

    def heater_setNewValues(self, kwargs) -> None:
        """ Turns heater on or off based on this hours electricity price.
        """
        isOn:bool = self.ADapi.get_state(self.heater, namespace = self.namespace) == 'on'
        now = self.ADapi.datetime(aware=True)

        if (
            self.isOverconsumption
            and isOn
        ):
            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )
            self.isSaveState = True
            return
        if self.increase_now:
            if not isOn:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
            self.isSaveState = False
            return
        if (
            self._is_time_within_any_save_range()
            and self.automate
            and not CHARGE_SCHEDULER.isChargingTime()
        ):
            if isOn:
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
            self.isSaveState = True
            return
        elif not isOn:
            if (
                self.HeatAt is not None
                and self.away_state
            ):
                if (
                    (start := self.HeatAt) <= now < (end := self.EndAt)
                    or ELECTRICITYPRICE.electricity_price_now() <= self.price + (self.heater_data.pricedrop/2)
                ):
                    self.ADapi.call_service('switch/turn_on',
                        entity_id = self.heater,
                        namespace = self.namespace
                    )
                self.isSaveState = False
                return
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
                self.isSaveState = False
                return
        elif(
            isOn
            and self.HeatAt is not None
            and self.away_state
        ):
            if (
                (start := self.HeatAt) <= now < (end := self.EndAt)
                or ELECTRICITYPRICE.electricity_price_now() <= self.price + (self.heater_data.pricedrop/2)
            ):
                return
            if self.heater_data.validConsumptionSensor:
                if float(self.ADapi.get_state(self.heater_data.consumptionSensor, namespace = self.namespace)) > 20:
                    self.ADapi.listen_state(self.turnOffHeaterAfterConsumption, self.heater_data.consumptionSensor,
                        namespace = self.namespace,
                        constrain_state=lambda x: float(x) < 20
                    )
                    return
            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )

    def turnOffHeaterAfterConsumption(self, entity, attribute, old, new, kwargs) -> None:
        """ Turns off heater after consumption is below 20W
        """
        self.ADapi.call_service('switch/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )

    def turn_on_heater(self) -> None:
        """ Turns heater back to normal operation after fire.
        """
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def turn_off_heater(self) -> None:
        """ Turns heater off.
        """
        self.ADapi.call_service('switch/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )

        # Functions called from electrical
    def setPreviousState(self) -> None:
        """ Set heater to previous state after overconsumption.
        """
        self.isOverconsumption = False
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def removeSaveState(self) -> None:
        """ Set heater to normal state.
        """
        self.isSaveState = False
        self.isOverconsumption = False
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def setIncreaseState(self) -> None:
        """ Set heater to increase temperature when electricity production is higher that consumption.
        """
        self.increase_now = True
        self.isSaveState = False
        self.ADapi.run_in(self.heater_setNewValues, 1)

    def get_heater_consumption(self) -> Tuple[float, bool]:
        if self.heater_data.validConsumptionSensor:
            consumption_now = self.ADapi.get_state(self.heater_data.consumptionSensor, namespace = self.namespace)
            if consumption_now not in UNAVAIL:
                try:
                    return float(consumption_now), True
                except (TypeError, ValueError):
                    pass
        return self.heater_data.normal_power, False

    def get_heater_kWh_consumption(self) -> float:
        try:
            consumption = float(self.ADapi.get_state(self.heater_data.kWhconsumptionSensor, namespace = self.namespace))
        except (TypeError, AttributeError) as ve:
            self.ADapi.log(
                f"Could not get kWh consumption for {self.heater} {consumption} Error: {ve}",
                level = 'DEBUG'
            )
            return None
        else:
            return consumption

        # Functions to calculate and log consumption to persistent storage
    def findConsumptionAfterTurnedOn(self, **kwargs) -> None:
        """ Starts to listen for how much heater consumes after it has been in save mode.
        """
        hoursOffInt = kwargs['hoursOffInt']
        if self.heater_data.kWhconsumptionSensor is None or not self.heater_data.validConsumptionSensor:
            return

        if (
            (self.ADapi.get_state(self.heater, namespace = self.namespace) != 'off' or
            self.isOverconsumption)
            and not self.away_state
            and self.automate
        ):
            kWh_consumption = self.get_heater_kWh_consumption()
            if kWh_consumption is None:
                return
            self.kWh_consumption_when_turned_on = kWh_consumption

            self._consumption_stops_register_usage_handler = self.ADapi.listen_state(self._consumption_stops_register_usage, self.heater_data.consumptionSensor,
                namespace = self.namespace,
                constrain_state=lambda x: float(x) < 20,
                hoursOffInt = hoursOffInt,
                oneshot = True
            )
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater)
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 1200, hoursOffInt = hoursOffInt)

    def checkIfConsumption(self, kwargs) -> None:
        """ Checks if there is consumption after 'findConsumptionAfterTurnedOn' starts listening.
            If there is no consumption it will cancel the timer.
        """
        if not self.heater_data.validConsumptionSensor:
            self.ADapi.log(f"Consumption sensor for {self.heater} not Valid. Should not see this anymore...")
            if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater):
               self.checkConsumption_handler = None
            return

        if self.isOverconsumption:
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater)
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600, hoursOffInt = kwargs.get("hoursOffInt"))
            return

        wattconsumption, valid_consumption = self.get_heater_consumption()
        if valid_consumption:
            if wattconsumption < 30:
                if cancel_listen_handler(ADapi = self.ADapi, handler = self._consumption_stops_register_usage_handler, name = self.heater):
                    self._consumption_stops_register_usage_handler = None
                    self.registerConsumption(off_for_hours = int(kwargs.get("hoursOffInt")))
            elif self._consumption_stops_register_usage_handler is None:
                self._consumption_stops_register_usage_handler = self.ADapi.listen_state(self._consumption_stops_register_usage, self.heater_data.consumptionSensor,
                    namespace = self.namespace,
                    constrain_state=lambda x: float(x) < 20,
                    hoursOffInt = kwargs.get("hoursOffInt"),
                    oneshot = True
                )

    def _consumption_stops_register_usage(self, entity, attribute, old, new, **kwargs) -> None:
        """ Registers consumption to persistent storage after heater has been off.
        """
        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater):
            self.checkConsumption_handler = None

        if self.isOverconsumption:
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600, hoursOffInt = kwargs.get("hoursOffInt"))
            return

        if cancel_listen_handler(ADapi = self.ADapi, handler = self._consumption_stops_register_usage_handler, name = self.heater):
           self._consumption_stops_register_usage_handler = None

        try:
            if self.heater_data.normal_power < float(old):
                self.heater_data.normal_power = float(old)
        except (TypeError, ValueError):
            pass
        self.registerConsumption(off_for_hours = int(kwargs.get("hoursOffInt")))

    def registerConsumption(self, off_for_hours:int = 0) -> None:
        consumption:float = 0
        try:
            consumption = float(self.ADapi.get_state(self.heater_data.kWhconsumptionSensor, namespace = self.namespace))
            consumption -= self.kWh_consumption_when_turned_on
        except (TypeError, AttributeError) as ve:
            self.ADapi.log(
                f"Could not get consumption for {self.heater} to register data. {consumption} Error: {ve}",
                level = 'DEBUG'
            )
            return
        if consumption == 0:
            consumption = 0.01 # Avoid multiplications by 0.
        if consumption < 0:
            return
        
        if self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off':
            return

        out_temp_str = str(floor_even(OUT_TEMP))

        if off_for_hours not in self.heater_data.ConsumptionData:
            self.heater_data.ConsumptionData[off_for_hours] = {}

        inner_dict: Dict[str, TempConsumption] = self.heater_data.ConsumptionData[
            off_for_hours
        ]

        if out_temp_str not in inner_dict:
            inner_dict[out_temp_str] = TempConsumption(
                Consumption=round(consumption, 2),
                Counter=1,
            )
        else:
            existing: TempConsumption = inner_dict[out_temp_str]
            counter = (existing.Counter or 0) + 1
            avg_consumption = round(
                (
                    (existing.Consumption or 0) * (existing.Counter or 0)
                    + consumption
                )
                / counter,
                2,
            )
            if counter > 100:
                counter = 10
            existing.Consumption = avg_consumption
            existing.Counter = counter


        # Helper functions for windows
    def windowOpened(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to windows opened.
        """
        if self.numWindowsOpened() != 0:
            self.windows_is_open = True
            self.notify_on_window_closed = True
            self.ADapi.run_in(self.heater_setNewValues, 1)

    def windowClosed(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to windows closed and checks if other windows are opened.
        """
        if self.numWindowsOpened() == 0:
            self.windows_is_open = False
            self.notify_on_window_open = True
            self.ADapi.run_in(self.heater_setNewValues, 1)

    def numWindowsOpened(self) -> int:
        """ Returns number of windows opened.
        """
        opened = 0
        for window in self.heater_data.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

    def _is_time_within_any_save_range(self):
        now = self.ADapi.datetime(aware=True)
        for range_item in self.heater_data.time_to_save:
            if (start := range_item.start) <= now < (end := range_item.end):
                return True
        return False

    def _is_time_within_any_spend_range(self):
        now = self.ADapi.datetime(aware=True)
        for range_item in self.time_to_spend:
            if (start := range_item.start) <= now < (end := range_item.end):
                return True
        return False

class Climate(Heater):
    """ Child class of Heater
        For controlling electrical heaters to heat off peak hours.
    """
    def __init__(self,
        api,
        namespace,
        heater,
        heater_data
    ):

        # Sensors
        if heater_data.target_indoor_input is not None:
            api.listen_state(self.updateTarget, heater_data.target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(heater_data.target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = heater_data.target_indoor_temp

        super().__init__(
            api = api,
            namespace = namespace,
            heater = heater,
            heater_data = heater_data
        )
        self.reset_continuous_hours = True

        self.windows_is_open:bool = False
        self.notify_on_window_open:bool = True
        self.notify_on_window_closed:bool = False
        for window in self.heater_data.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                self.windows_is_open = True

            self.ADapi.listen_state(self.windowOpened, window,
                new = 'on',
                duration = 120,
                namespace = self.namespace
            )
            self.ADapi.listen_state(self.windowClosed, window,
                new = 'off',
                namespace = self.namespace
            )

        try:
            self.min_temp = self.ADapi.get_state(self.heater,
                namespace = self.namespace,
                attribute = 'min_temp'
            )
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.heater} Attribute = 'min_temp' is not found in: "
                f"{self.ADapi.get_state(self.heater, namespace = self.namespace, attribute = 'all')} "
                f"ValueError: {ve}",
                level = 'DEBUG'
            )
            self.min_temp = 5

        now = self.ADapi.datetime(aware=True)
        runtime = get_next_runtime_aware(startTime = now, offset_seconds=10, delta_in_seconds=60*15)
        self.ADapi.run_every(self.heater_setNewValues, runtime, 60*15)

        # Get new prices to save and in addition to turn up heat for heaters before expensive hours
    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on ELECTRICITYPRICE.find_times_to_spend()
        """
        super().heater_getNewPrices(0)
        self.time_to_spend = ELECTRICITYPRICE.find_times_to_spend(
            priceincrease = self.heater_data.priceincrease
        )

        """Logging purposes to check what hours heating will be turned up"""
        #if self.time_to_spend:
        #    self.ADapi.log(f"{self.heater} Extra heating at: {ELECTRICITYPRICE.print_peaks(self.time_to_spend)}", level = 'INFO')

    def _awayStateListen_Heater(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state
        """
        self.away_state = new == 'on'
        if (
            self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
            and new == 'off'
        ):
            try:
                self.ADapi.call_service('climate/set_hvac_mode',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    hvac_mode = 'heat'
                )
            except Exception as e:
                self.ADapi.log(f"Not able to set hvac_mode to heat for {self.heater}. Exception: {e}", level = 'INFO')
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def find_target_temperatures(self) -> int:
        """ Helper function to find correct dictionary element in temperatures
        """
        target_num = 0
        for target_num, target_temp in enumerate(self.heater_data.temperatures):
            if target_temp['out'] >= OUT_TEMP:
                if target_num != 0:
                    target_num -= 1
                return target_num

        return target_num

    def turn_on_heater(self) -> None:
        """ Turns climate on after fire alarm.
        """
        self.ADapi.call_service('climate/turn_on',
            entity_id = self.heater,
            namespace = self.namespace
        )
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def turn_off_heater(self) -> None:
        """ Turns climate off.
        """
        self.ADapi.call_service('climate/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )

        # Functions to set temperature
    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        if self.ADapi.get_state(self.heater, namespace = self.namespace) == 'heat':
            target_num = self.find_target_temperatures()
            target_temp = self.heater_data.temperatures[target_num]
            if self.heater_data.save_temp is not None:
                save_temp = self.heater_data.save_temp + target_temp['offset']
            elif 'save' in target_temp:
                save_temp = target_temp['save']
            else:
                save_temp = 10
            try:
                if float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='temperature')) > save_temp:
                    self.ADapi.call_service('climate/set_temperature',
                        namespace = self.namespace,
                        entity_id = self.heater,
                        temperature = save_temp
                    )
            except (TypeError, AttributeError) as ve:
                self.ADapi.call_service('climate/set_temperature',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    temperature = 10
                )
                self.ADapi.log(f"Error when trying to set temperature to {self.heater}: {ve}", level = 'DEBUG')

    def heater_setNewValues(self, kwargs) -> None:
        """ Adjusts temperature based on weather and time to save/spend
        """
        if (
            self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
            or self.isOverconsumption
        ):
            return
        self.isSaveState =  False
        target_num = self.find_target_temperatures()
        target_temp = self.heater_data.temperatures[target_num]

        try:
            heater_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='temperature'))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}: {ve}",
                level = 'DEBUG'
            )
            heater_temp = self.target_indoor_temp
        except Exception as e:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}. Exception: {e}",
                level = 'INFO'
            )
            heater_temp = self.target_indoor_temp

        in_temp:float = -50
        if self.heater_data.indoor_sensor_temp is not None:
            try:
                in_temp = float(self.ADapi.get_state(self.heater_data.indoor_sensor_temp, namespace = self.namespace))
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(
                    f"Not able to get new inside temperature from {self.heater_data.indoor_sensor_temp}. Error: {e}",
                    level = 'DEBUG'
                )
        if in_temp == -50:
            try:
                in_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='current_temperature'))
                self.ADapi.log(
                    f"{self.heater} Not able to get new inside temperature from {self.heater_data.indoor_sensor_temp}. "
                    f"Getting in temp from heater. It is: {in_temp}",
                    level = 'DEBUG'
                )
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline. Error: {te}", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(f"Not able to get new inside temperature from {self.heater}. {e}", level = 'WARNING')

        # Set Target temperatures
        if 'offset' in target_temp:
            new_temperature = self.target_indoor_temp + target_temp['offset']
        elif 'normal' in target_temp:
            new_temperature = target_temp['normal']
        else:
            new_temperature = self.target_indoor_temp

        if self.heater_data.vacation_temp is not None:
            vacation_temp = self.heater_data.vacation_temp + target_temp['offset']
        elif 'away' in target_temp:
            vacation_temp = target_temp['away']
        else:
            vacation_temp = 5

        # Adjust temperature based on weather
        if RAIN_AMOUNT >= self.heater_data.rain_level:
            new_temperature += 1
        elif WIND_AMOUNT >= self.heater_data.anemometer_speed:
            new_temperature += 1
        
        adjust = 0
        if self.heater_data.window_temp is not None:
            try:
                window_temp = float(self.ADapi.get_state(self.heater_data.window_temp, namespace = self.namespace))
            except (TypeError, AttributeError):
                window_temp = self.target_indoor_temp + self.heater_data.window_offset
                self.ADapi.log(f"{self.heater_data.window_temp} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                window_temp = self.target_indoor_temp + self.heater_data.window_offset
                self.ADapi.log(f"Not able to get temperature from {self.heater_data.window_temp}. {e}", level = 'DEBUG')
            if window_temp > self.target_indoor_temp + self.heater_data.window_offset:
                adjust = math.floor(float(window_temp - (self.target_indoor_temp + self.heater_data.window_offset)))

        if in_temp > self.target_indoor_temp:
            adjust += math.floor(float(in_temp - self.target_indoor_temp))
        
        new_temperature -= adjust

        if new_temperature < vacation_temp:
            new_temperature = vacation_temp

        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and in_temp >= self.target_indoor_temp + 10
            and OUT_TEMP > self.heater_data.getting_cold
        ):
            NOTIFY_APP.send_notification(
                message = f"No Window near {self.heater} is open and it is getting hot inside! {in_temp}°",
                message_title = f"Window closed",
                message_recipient = self.heater_data.recipients,
                also_if_not_home = False
            )
            self.notify_on_window_closed = False
        
        if self.windows_is_open:
            new_temperature = vacation_temp
            if (
                self.notify_on_window_open
                and OUT_TEMP < self.heater_data.getting_cold
                and in_temp < self.heater_data.getting_cold
            ):
                NOTIFY_APP.send_notification(
                    message = f"Window near {self.heater} is open and inside temperature is {in_temp}°",
                    message_title = "Window open",
                    message_recipient = self.heater_data.recipients,
                    also_if_not_home = False
                )
                self.notify_on_window_open = False

        # Holliday temperature
        elif self.away_state:
            new_temperature = vacation_temp

        # Peak and savings temperature
        if (
            self._is_time_within_any_save_range()
            and self.automate
        ):
            new_temperature = self.getSaveTemp(new_temperature, target_temp)
            self.isSaveState = True
        
        # Daytime Savings
        else:
            doDaytimeSaving = False
            for daytime in self.heater_data.daytime_savings:
                if (
                    'start' in daytime
                    and 'stop' in daytime
                ):
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeSaving = True
                        if 'presence' in daytime:
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                    doDaytimeSaving = False

                elif 'presence' in daytime:
                    doDaytimeSaving = True
                    for presence in daytime['presence']:
                        if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                            doDaytimeSaving = False

            if doDaytimeSaving:
                new_temperature = self.getSaveTemp(new_temperature, target_temp)
                self.isSaveState = True

        # Low price for electricity or solar power
        if (
            self.increase_now
            or self._is_time_within_any_spend_range()
        ):
            new_temperature += 1

        # Avoid setting lower temp than climate minimum
        if new_temperature < self.min_temp:
            new_temperature = self.min_temp

        # Setting new temperature
        try:
            if heater_temp != new_temperature:
                self.ADapi.call_service('climate/set_temperature',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    temperature = new_temperature
                )
        except (TypeError, AttributeError):
            self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')

    def getSaveTemp(self, new_temperature:float, target_temp:dict) -> float:
        """ Returns save temperature
        """
        if self.heater_data.save_temp_offset is not None:
            new_temperature += self.heater_data.save_temp_offset
        elif self.heater_data.save_temp is not None:
            if new_temperature > self.heater_data.save_temp + target_temp['offset']:
                new_temperature = self.heater_data.save_temp + target_temp['offset']
        elif 'save' in target_temp:
            if new_temperature > target_temp['save']:
                new_temperature = target_temp['save']
        else:
            new_temperature = 10

        return new_temperature

    def updateTarget(self, entity, attribute, old, new, kwargs):
        """ Reacts to target temperature for room beening updated.
        """
        self.target_indoor_temp = float(new)
        self.ADapi.run_in(self.heater_setNewValues, 5)

class On_off_switch(Heater):
    """ Child class of Heater
        Heating of on_off_switch off peak hours
        Turns on/off a switch depending og given input and electricity price
    """
    def __init__(self,
        api,
        heater,
        namespace,
        heater_data
    ):

        super().__init__(
            api = api,
            namespace = namespace,
            heater = heater,
            heater_data=heater_data,
        )

class Notify_Mobiles:
    """ Class to send notification with 'notify' HA integration
    """
    def __init__(self, api,
        namespace:str
    ) -> None:
        self.ADapi = api
        self.namespace = namespace


    def send_notification(self, **kwargs) -> None:
        """ Sends notification to recipients via Home Assistant notification.
        """
        message:str = kwargs['message']
        message_title:str = kwargs.get('message_title', 'Home Assistant')
        message_recipient:str = kwargs.get('message_recipient', True)
        also_if_not_home:bool = kwargs.get('also_if_not_home', False)
        data:dict = kwargs.get('data', {'clickAction' : 'noAction'})

        for re in message_recipient:
            self.ADapi.call_service(f'notify/{re}',
                title = message_title,
                message = message,
                data = data,
                namespace = self.namespace
            )
