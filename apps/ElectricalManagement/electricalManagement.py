""" ElectricalManagement.

    @Pythm / https://github.com/Pythm
"""

from __future__ import annotations
from appdaemon import adbase as ad

import math
import json
import importlib.util
#import csv

import bisect
#import pytz
from datetime import timedelta
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass #, field, asdict
from typing import Any, Dict, List, Tuple, Iterable, Optional
#from pydantic import BaseModel, Field

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
    floor_even,
    ModeTranslations
)
from registry import Registry
from scheduler import Scheduler
from electrical_cars import Car, Tesla_car
from electrical_chargers import Charger, Tesla_charger, Easee
from electrical_heater import Heater, Climate, On_off_switch

__version__ = "1.0.0_beta"

MAX_TEMP_DIFFERENCE = 5
MAX_CONSUMPTION_RATIO_DIFFERENCE = 3

UNAVAIL = ('unavailable', 'unknown')
translations = None

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

        self.json_path = self.args.get('json_path', None) 
        if self.json_path is None:
            self.json_path:str = f"{self.AD.config_dir}/persistent/electricity/"
            if not os.path.exists(self.json_path):
                os.makedirs(self.json_path)
            self.json_path += 'electricalmanagement.json'

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
                self.ADapi.log(f"Skipping car entry {cfg} - no carName given", level='WARNING')
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
                self.ADapi.log(f"Skipping heater entry {heater_cfg} no heater given", level='WARNING')
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

        self.ADapi.listen_event(self._notify_event, "mobile_app_notification_action", namespace=self.HASS_namespace)

        global translations
        spec = importlib.util.find_spec('translations_lightmodes')
        if spec is not None:
            from translations_lightmodes import translations
            self.ADapi.listen_event(self.mode_event, translations.MODE_CHANGE, namespace = self.HASS_namespace)
        else:
            translations = ModeTranslations()
            self.ADapi.listen_event(self.mode_event, "MODE_CHANGE", namespace = self.HASS_namespace)

    def _init_collections(self):
        self.chargers: dict[str, Charger] = {}
        self.cars: dict[str, Car] = {}
        self.appliances: list = []
        self.heaters: list = []

        self.heatersRedusedConsumption:list = []
        self.lastTimeHeaterWasReduced = self.ADapi.datetime(aware = True) - timedelta(minutes = 5)

        self.notify_overconsumption: bool = 'notify_overconsumption' in self.args.get('options')
        self.pause_charging: bool = 'pause_charging' in self.args.get('options')

        self.buffer = self.args.get('buffer', 0.4) + 0.01
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
        
        self.home_name = self.args.get('home_name', 'home')

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
        if self.current_consumption_sensor is not None:
            try:
                self.current_consumption = float(self.ADapi.get_state(self.current_consumption_sensor))
            except (ValueError, TypeError) as ve:
                if self.ADapi.get_state(self.current_consumption_sensor) in UNAVAIL:
                    pass
                else:
                    self.ADapi.log(
                        "power_consumption sensor is not a number on electrical management initialization. ",
                        level='INFO'
                    )
                self.ADapi.log(ve, level = 'DEBUG')

    def _validate_accumulated_consumption_current_hour(self):
        self.accumulated_consumption_current_hour = self.args.get('accumulated_consumption_current_hour', None)
        if self.accumulated_consumption_current_hour is not None:

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

        self.current_production = self._get_sensor_value(self.current_production_sensor)
        self.production_kWh = self._get_sensor_value(self.accumulated_production_current_hour)

        self.max_target_kWh_buffer = self._calc_max_target_kWh_buffer(now)
        self.projected_kWh_usage = self._calc_projected_kWh_usage(now)
        self.available_Wh = self._calc_available_Wh(now)

        if now.hour in self._persistence.high_consumption.high_consumption_hours:
            sub_wh = remaining_minute * 10 * self._persistence.max_usage.max_kwh_usage_pr_hour
            self.available_Wh -= sub_wh
            self.max_target_kWh_buffer -= (sub_wh / 10000)

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

            if not self.charging_scheduler.isChargingTime() and remaining_minute <= 15:
                if now.hour not in self._persistence.high_consumption.high_consumption_hours:
                    self._persistence.high_consumption.high_consumption_hours.append(now.hour)

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
        to_remove = set()
        for queue_id in charging_list:
            car = Registry.get_car(queue_id)
            if car is None:
                continue

            if car.connected_charger is not None:
                ChargingState = car.getCarChargerState()
                if ChargingState in ('Complete', 'Disconnected'):
                    to_remove.add(queue_id)
                    self.charging_scheduler.removeFromCharging(car.vehicle_id)
                    car.connected_charger._CleanUpWhenChargingStopped()
                    if (
                        len(self.charging_scheduler.chargingQueue) == 0 and
                        not self.away_state and
                        self.ADapi.now_is_between('01:00:00', '05:00:00')
                    ):
                        if self.charging_scheduler.findNextChargerToStart(check_if_charging_time = check_if_charging_time) is None:
                            if self.ADapi.now_is_between('01:00:00', '04:00:00'):
                                self.checkIdleConsumption_Handler = self.ADapi.run_at(self.logIdleConsumption, "04:30:01")
                            else:
                                self.ADapi.run_in(self.logIdleConsumption, 30)
                        elif self._should_start_next_charging(vehicle_id = car.vehicle_id):
                            next_vehicle_id = True

                elif ChargingState in ('Stopped', 'awaiting_start'):
                    
                    if (
                        self.charging_scheduler.isChargingTime(vehicle_id = car.vehicle_id) and 
                        available_Wh > 1300 or
                        not check_if_charging_time
                    ):
                        self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                              remaining_minute = remaining_minute)
                                                                
                        return True
                    elif not car.dontStopMeNow():
                        to_remove.add(queue_id)
                        self.charging_scheduler.removeFromCharging(car.vehicle_id)

                elif ChargingState == 'Charging':
                    if not check_if_charging_time:
                        car.charging_on_solar = True
                    if (len(self.charging_scheduler.chargingQueue) > len(charging_list) and
                        self._should_start_next_charging(vehicle_id = car.vehicle_id)
                    ):
                        next_vehicle_id = True
                    else:
                        next_vehicle_id = False

                        if not car.isChargingAtMaxAmps():
                            self._increase_charging_ampere(car, available_Wh)
                            return True

                elif ChargingState is None:
                    car.wakeMeUp()
                    self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                          remaining_minute = remaining_minute)
                    return True

                elif (
                    car.connected_charger is not car.onboard_charger
                    and ChargingState == 'NoPower'
                ):
                    if car.connected_charger.getChargingState() != 'Charging':
                        self._start_charging_from_chargeQueue(vehicle_id = car.vehicle_id,
                                                              remaining_minute = remaining_minute)
                        return True

                else:
                    if (
                        car.connected_charger is car.onboard_charger
                        and ChargingState == 'NoPower'
                    ):
                        car.wakeMeUp()
                        for charger in self.all_chargers():
                            if (
                                charger.connected_vehicle is None
                                and charger.getChargingState() in ('Stopped', 'awaiting_start')
                            ):
                                self.ADapi.log(f"-> Found {charger.charger} with state {charger.getChargingState()}. Will try to match with car {car.carName}") ###
                                Registry.unlink(car)
                                charger.findCarConnectedToCharger()

            elif not car.isConnected():
                to_remove.add(queue_id)
                self.charging_scheduler.removeFromCharging(car.vehicle_id)
                self.ADapi.log(f"Removing {car.carName} from chargequeue. is not connected. Chargestate not Disconnetcted? {car.getCarChargerState()}") ###
                car._handleChargeCompletion()

            else:
                Registry.set_link(car, car.onboard_charger)

        charging_list[:] = [
            qid for qid in charging_list
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
                return self._find_next_charger_to_start(queue_list = charging_list,
                                                        check_if_charging_time = check_if_charging_time)
        return False


    # Functions for consumption calculation

    def _get_current_consumption(self) -> None:
        try:
            self.current_consumption = float(self.ADapi.get_state(self.current_consumption_sensor))
        except (TypeError, ValueError):
            self.current_consumption, heater_consumption = self.get_idle_and_heater_consumption()
            if self.current_consumption is None:
                self.current_consumption = 2000.0
            self.current_consumption *= self._persistence.max_usage.calculated_difference_on_idle

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
            if self.accumulated_unavailable > 15:
                # Will try to reload Home Assistant integration if the sensor is unavailable for 15 minutes. 
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

                if self.last_accumulated_kWh + (self.current_consumption/60000) < self.accumulated_kWh:
                    self.ADapi.log(
                        f"Accumulated kWh was unavailable. Estimated: {round(self.last_accumulated_kWh + (self.current_consumption/60000),2)}. "
                        f"Actual: {self.accumulated_kWh}",
                        level = 'INFO'
                    ) ###
                    error_ratio = self.accumulated_kWh / (self.last_accumulated_kWh + (self.current_consumption/60000))
                    self._persistence.max_usage.calculated_difference_on_idle *= error_ratio
                    self._persistence.max_usage.calculated_difference_on_idle *= 1.1
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
                threshold = max(car.getCarMaxAmps() - 12, 16)
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
                    return
            if hoursOffInt > 0:
                runtime = time_to_save_item.end + timedelta(minutes = 3)
                self.ADapi.run_at(heater.findConsumptionAfterTurnedOn, runtime, hoursOffInt = hoursOffInt)


    def _reset_hourly(self, now) -> None:
        self.last_accumulated_kWh = 0
        self.find_next_charger_counter = 0
        if now.hour == 0 and now.day == 1:
            self._persistence.max_usage.max_kwh_usage_pr_hour = self.max_kwh_goal
            self._persistence.max_usage.topUsage = [0, 0, 0]

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
            self.ADapi.log(f"idle_consumption = {idle_consumption} - aborting logging Idle Consumption") ###
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
                    f"Discarded idle sample at {out_temp_even} degrees - too different from existing data"
                ) ###
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
                            f"closest data at {nearest_key} degrees is too far or too different"
                        ) ###
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

        if data['mode'] == translations.fire:
            self.houseIsOnFire = True
            for car in self.all_cars_connected():
                if car.getCarChargerState() == 'Charging':
                    car.stopCharging(force_stop = True)
            
            for charger in self.all_chargers():
                charger.doNotStartMe = True

            for heater in self.heaters:
                heater.turn_off_heater()


        elif data['mode'] == translations.false_alarm:
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
                    f"Turn down consumption at {self.home_name}. It's about to go over max usage "
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
        """ Listen for changes in vacation switch """

        self.away_state = new == 'on'


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
