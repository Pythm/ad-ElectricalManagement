""" ElectricalManagement.

    @Pythm / https://github.com/Pythm
"""

__version__ = "0.3.0"

from appdaemon import adbase as ad
import datetime
import math
import json
import csv
import inspect
import bisect
import pytz
from collections import defaultdict
from typing import Dict, List, Tuple

RECIPIENTS:list = []
NOTIFY_APP = None
OUT_TEMP:float = 10.0
RAIN_AMOUNT:float = 0.0
WIND_AMOUNT:float = 0.0

# Translations from json for 'MODE_CHANGE' events
FIRE_TRANSLATE:str = 'fire'
FALSE_ALARM_TRANSLATE:str = 'false-alarm'

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

        # Set up notification app
        self._setup_notify_app()

        # Set up electricity price class
        self._setup_electricity_price()

        # Consumption sensors setup
        try:
            self._validate_current_consumption_sensor()
        except Exception as e:
            raise e

        # Accumulated consumption current hour sensor setup
        self._setup_accumulated_consumption_current_hour()

        # Electricity power production sensors setup
        self._setup_power_production_sensors()

        # Setting buffer for kWh usage and max kWh goal
        self.buffer:float = self.args.get('buffer', 0.4) + 0.02
        self.max_kwh_goal: int = self.args.get('max_kwh_goal', 15)

        # Set up charge scheduler
        global CHARGE_SCHEDULER
        CHARGE_SCHEDULER = Scheduler(
            api=self.ADapi,
            stopAtPriceIncrease=self.args.get('stopAtPriceIncrease', 0.3),
            startBeforePrice=self.args.get('startBeforePrice', 0.01),
            infotext=self.args.get('infotext', None),
            namespace=self.HASS_namespace
        )

        # Establish and recall persistent data using JSON

        self.json_path = self.args.get('json_path', None)
        if not self.json_path:
            raise Exception("Path to store json not provided. Please input a valid path with configuration 'json_path'")

        self._load_persistent_data()

        # Generate availableWatt list
        self._generate_available_watt_list()

        # Default vacation state for saving purposes when away from home
        self.away_state = self._get_vacation_state()
        self.automate = self.args.get('automate', None)

        # Weather sensors setup
        self._setup_weather_sensors()

        # Set up chargers, cars, and heaters
        self.notify_overconsumption: bool = 'notify_overconsumption' in self.args.get('options', [])
        self.pause_charging: bool = 'pause_charging' in self.args.get('options', [])

        self.heatersRedusedConsumption:list = [] # Heaters currently turned off/down due to overconsumption
        self.lastTimeHeaterWasReduced = datetime.datetime.now() - datetime.timedelta(minutes = 5)


        CAR_SPECS: List[Tuple[str, str, str]] = [
            ("charger_sensor",           "binary_sensor", "_charger"),
            ("charger_switch",           "switch",        "_charger"),
            ("charging_amps",            "number",        "_charging_amps"),
            ("charger_power",            "sensor",        "_charger_power"),
            ("charge_limit",             "number",        "_charge_limit"),
            ("session_energy",           "sensor",        "_energy_added"),
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

        EASEE_SPECS: List[Tuple[str, str, str]] = [
            ("charger_sensor",          "sensor",   "_status"),
            ("reason_for_no_current",   "sensor",   "_reason_for_no_current"),
            ("charging_amps",           "sensor",   "_current"),
            ("charger_power",           "sensor",   "_power"),
            ("session_energy",          "sensor",   "_energy_added"),
            ("voltage",                 "sensor",   "_voltage"),
            ("max_charger_limit",       "sensor",   "_max_charger_limit"),
            ("idle_current",            "sensor",   "_idle_current")
        ]

        HEATER_SPECS: List[Tuple[str, str, str]] = [
            ("heater",                "climate", ""),
            ("consumptionSensor",     "sensor",   "_electric_consumption_w"),
            ("kWhconsumptionSensor",  "sensor",   "_electric_consumption_kwh"),
        ]


        missing_sensors: Dict[str, Dict[str, None]] = defaultdict(dict)

        pending_teslas: List[dict] = []
        pending_other_cars: List[dict] = []
        pending_easees: List[dict] = []
        pending_heaters: List[dict] = []
        pending_switches: List[dict] = []

        def _find_missing_sensors(namespace: str) -> None:
            """Populate missing_sensors[namespace] with real entity IDs."""
            state_map = self.ADapi.get_state(namespace=namespace)
            for entity_id in state_map.keys():
                if entity_id in missing_sensors[namespace]:
                    missing_sensors[namespace][entity_id] = entity_id

        teslas = self.args.get('tesla', [])
        for t in teslas:
            namespace = t.get('namespace', self.HASS_namespace)

            car = t.get('charger') or t.get('car')
            if 'charger_sensor' in t and not car:
                sensor_id = t['charger_sensor']
                car = sensor_id.replace('binary_sensor.', '').replace('_charger', '')

            for cfg_key, domain, suffix in CAR_SPECS:
                # The first element (charger_sensor) is handled specially below
                if cfg_key == 'charger_sensor':
                    if not t.get(cfg_key):
                        missing_sensors[namespace][f"{domain}.{car}{suffix}"] = None
                    continue

                if not t.get(cfg_key):
                    missing_sensors[namespace][f"{domain}.{car}{suffix}"] = None

            pending_teslas.append({
                'namespace': namespace,
                'car': car,
                **t,
            })

        other_cars = self.args.get('cars', [])
        for c in other_cars:
            namespace = c.get('namespace', self.HASS_namespace)
            car_name = c.get('carName', 'automobile')

            pending_other_cars.append({
                'namespace': namespace,
                'car': car_name,
                **c,
            })

        easees = self.args.get('easee', [])
        for e in easees:
            namespace = e.get('namespace', self.HASS_namespace)
            charger = e.get('charger')
            if 'charger_status' in e and not charger:
                sensor_id = e['charger_status']
                charger = sensor_id.replace('sensor.', '').replace('_status', '')

            # Add potential missing sensors to missing_sensors dict
            for cfg_key, domain, suffix in EASEE_SPECS:
                # The “charger_sensor” key is special: it is already resolved
                if cfg_key == 'charger_sensor':
                    if not e.get(cfg_key):
                        missing_sensors[namespace][f"{domain}.{charger}{suffix}"] = None
                    continue
                if not e.get(cfg_key):
                    missing_sensors[namespace][f"{domain}.{charger}{suffix}"] = None

            pending_easees.append({
                'namespace': namespace,
                'charger': charger,
                **e,
            })

        heaters = self.args.get('climate', {})
        for h in heaters:
            namespace = h.get('namespace', self.HASS_namespace)
            pending_heaters.append({
                'namespace': namespace,
                **h,
            })

        heater_switches = self.args.get('heater_switches', {})
        for hs in heater_switches:
            namespace = hs.get('namespace', self.HASS_namespace)
            pending_switches.append({
                'namespace': namespace,
                **hs,
            })

        for ns in missing_sensors.keys():
            _find_missing_sensors(ns)


        def _resolved(namespace: str, entity_id: str) -> str | None:
            """Return the real entity ID or None if it was never found."""
            return missing_sensors[namespace].get(entity_id)

        for car_def in pending_teslas:
            ns = car_def['namespace']
            car_name = car_def['car']

            def resolve(cfg_key: str) -> str | None:
                if cfg_key in car_def:
                    return car_def[cfg_key]
                domain, suffix = next((d, s)
                                    for k, d, s in CAR_SPECS if k == cfg_key)
                entity_id = f"{domain}.{car_name}{suffix}"
                return _resolved(ns, entity_id)

            teslaCar = Tesla_car(
                api=self.ADapi,
                namespace=ns,
                carName=car_name,
                charger_sensor=resolve('charger_sensor'),
                charge_limit=resolve('charge_limit'),
                battery_sensor=resolve('battery_sensor'),
                asleep_sensor=resolve('asleep_sensor'),
                online_sensor=resolve('online_sensor'),
                location_tracker=resolve('location_tracker'),
                destination_location_tracker=resolve('destination_location_tracker'),
                arrival_time=resolve('arrival_time'),
                software_update=resolve('software_update'),
                force_data_update=resolve('force_data_update'),
                polling_switch=resolve('polling_switch'),
                data_last_update_time=resolve('data_last_update_time'),
                battery_size=car_def.get('battery_size', 100),
                pref_charge_limit=car_def.get('pref_charge_limit', 90),
                priority=car_def.get('priority', 3),
                finishByHour=car_def.get('finishByHour', 7),
                charge_now=car_def.get('charge_now', False),
                charge_only_on_solar=car_def.get('charge_only_on_solar', False),
                departure=car_def.get('departure', None),
                json_path=self.json_path
            )
            self.cars.append(teslaCar)

            teslaCharger = Tesla_charger(
                api=self.ADapi,
                Car=teslaCar,
                namespace=ns,
                charger=car_name,
                charger_sensor=resolve('charger_sensor'),
                charger_switch=resolve('charger_switch'),
                charging_amps=resolve('charging_amps'),
                charger_power=resolve('charger_power'),
                session_energy=resolve('session_energy'),
                json_path=self.json_path
            )
            self.chargers.append(teslaCharger)

        for c in pending_other_cars:
            namespace = c['namespace']
            car_name = c['car']
            automobile = Car(
                api=self.ADapi,
                namespace=namespace,
                carName=car_name,
                charger_sensor=c.get('charger_sensor'),
                charge_limit=c.get('charge_limit'),
                battery_sensor=c.get('battery_sensor'),
                asleep_sensor=c.get('asleep_sensor'),
                online_sensor=c.get('online_sensor'),
                location_tracker=c.get('location_tracker'),
                software_update=c.get('software_update'),
                force_data_update=c.get('force_data_update'),
                polling_switch=c.get('polling_switch'),
                data_last_update_time=c.get('data_last_update_time'),
                battery_size=c.get('battery_size', 100),
                pref_charge_limit=c.get('pref_charge_limit', 100),
                priority=c.get('priority', 3),
                finishByHour=c.get('finishByHour', 7),
                charge_now=c.get('charge_now', False),
                charge_only_on_solar=c.get('charge_only_on_solar', False),
                departure=c.get('departure', None),
                json_path=self.json_path
            )
            self.cars.append(automobile)

        for e in pending_easees:
            ns = e['namespace']
            charger_name = e['charger']

            def resolve(attr: str) -> str | None:
                if attr in e:
                    return e[attr]
                return _resolved(ns, f"sensor.{charger_name}_{attr}")

            easeeCharger = Easee(
                api=self.ADapi,
                cars=self.cars,
                namespace=ns,
                charger=charger_name,
                charger_sensor=resolve('status'),
                reason_for_no_current=resolve('reason_for_no_current'),
                charging_amps=resolve('current'),
                charger_power=resolve('power'),
                session_energy=resolve('session_energy'),
                voltage=resolve('voltage'),
                max_charger_limit=resolve('max_charger_limit'),
                idle_current=resolve('idle_current'),
                guest=e.get('guest', False),
                json_path=self.json_path
            )
            self.chargers.append(easeeCharger)


        # Set up hot water boilers and electrical heaters


        heaters = self.args.get('climate', {})
        for heater in heaters:
            validConsumptionSensor: bool = True
            namespace = heater.get('namespace',self.HASS_namespace)
            if 'name' in heater:
                log_indoor_sens:bool = True
                sensor_states = self.ADapi.get_state(namespace = namespace)
                for sensor_id, sensor_states in sensor_states.items():
                    if 'climate.' + heater['name'] in sensor_id:
                        if not 'heater' in heater:
                            heater['heater'] = sensor_id
                            self.ADapi.log(f"Added {heater['heater']} to ElectricalManagement based on name: {heater['name']}", level = 'INFO')
                    if (
                        'sensor.' + heater['name'] + '_electric_consumption_w' in sensor_id
                        or 'sensor.' + heater['name'] + '_electric_consumed_w' in sensor_id
                    ):
                        if not 'consumptionSensor' in heater:
                            heater['consumptionSensor'] = sensor_id
                    if (
                        'sensor.' + heater['name'] + '_electric_consumption_kwh' in sensor_id
                        or 'sensor.' + heater['name'] + '_electric_consumed_kwh' in sensor_id
                    ):
                        if not 'kWhconsumptionSensor' in heater:
                            heater['kWhconsumptionSensor'] = sensor_id
                    if 'sensor.' + heater['name'] + '_air_temperature' in sensor_id:
                        if (
                            not 'indoor_sensor_temp' in heater
                            and log_indoor_sens
                        ):
                            indoor_sensor_temp_found = sensor_id
                            self.ADapi.log(
                                f"No external indoor temperature sensor for {heater['name']} is configured. "
                                f"Automation will not check if it is hot inside. Found sensor {indoor_sensor_temp_found}. "
                                "This can be configured with 'indoor_sensor_temp' if applicable.",
                                level = 'INFO'
                            )
                            log_indoor_sens = False

            if not 'heater' in heater:
                self.ADapi.log(
                    f"'heater' not found or configured in {heater} climate configuration. Climate control setup aborted",
                    level = 'WARNING'
                )
                continue

            if not 'consumptionSensor' in heater:
                validConsumptionSensor = False
                heatername = (str(heater['heater'])).split('.')
                heater['consumptionSensor'] = 'input_number.' + heatername[1] + '_power'
                if not self.ADapi.entity_exists(heater['consumptionSensor'], namespace = namespace):
                    powercapability = heater.get('power', 300)
                    self.ADapi.call_service("state/set",
                        entity_id = heater['consumptionSensor'],
                        attributes = {'friendly_name' : str(heatername[1]) + ' Power'},
                        state = powercapability,
                        namespace = namespace
                    )

                self.ADapi.log(
                    f"'consumptionSensor' not found or configured. Climate electricity control not optimal. "
                    f"Using {heater['consumptionSensor']} as state with power: "
                    f"{self.ADapi.get_state(heater['consumptionSensor'], namespace = namespace)}",
                    level = 'WARNING'
                )
                if not 'power' in heater:
                    self.ADapi.log(f"Set electrical consumption with 'power' in args for heater.", level = 'INFO')

            if not 'kWhconsumptionSensor' in heater:
                heater['kWhconsumptionSensor'] = 'input_number.zero'
                if not self.ADapi.entity_exists(heater['kWhconsumptionSensor'], namespace = namespace):
                    self.ADapi.call_service("state/set",
                        entity_id = heater['kWhconsumptionSensor'],
                        attributes = {'friendly_name' : 'Zero consumption helper'},
                        state = 0,
                        namespace = namespace
                    )

                self.ADapi.log(
                    "'kWhconsumptionSensor' not found or configured. Climate electricity logging not available. "
                    "Using input_number.zero as state",
                    level = 'WARNING'
                )


            climate = Climate(api = self.ADapi,
                heater = heater['heater'],
                consumptionSensor = heater['consumptionSensor'],
                validConsumptionSensor = validConsumptionSensor,
                kWhconsumptionSensor = heater['kWhconsumptionSensor'],
                max_continuous_hours = heater.get('max_continuous_hours', 2),
                on_for_minimum = heater.get('on_for_minimum', 6),
                pricedrop = heater.get('pricedrop', 1),
                pricedifference_increase = heater.get('pricedifference_increase', 1.07),
                namespace = heater.get('namespace', self.HASS_namespace),
                away = heater.get('vacation', self.away_state),
                automate = heater.get('automate', self.automate),
                recipient = heater.get('recipient', None),
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None),
                window_temp = heater.get('window_temp', None),
                window_offset = heater.get('window_offset', -3),
                target_indoor_input = heater.get('target_indoor_input', None),
                target_indoor_temp = heater.get('target_indoor_temp', 23),
                save_temp_offset = heater.get('save_temp_offset', None),
                save_temp = heater.get('save_temp', None),
                away_temp = heater.get('away_temp', None),
                rain_level = heater.get('rain_level', self.rain_level),
                anemometer_speed = heater.get('anemometer_speed', self.anemometer_speed),
                low_price_max_continuous_hours = heater.get('low_price_max_continuous_hours', 2),
                priceincrease = heater.get('priceincrease', 1),
                windowsensors = heater.get('windowsensors', []),
                getting_cold = heater.get('getting_cold', 18),
                daytime_savings = heater.get('daytime_savings', []),
                temperatures = heater.get('temperatures', []),
                json_path=self.json_path
            )
            self.heaters.append(climate)


        heater_switches = self.args.get('heater_switches', {})
        for heater_switch in heater_switches:
            validConsumptionSensor:bool = True
            namespace = heater_switch.get('namespace',self.HASS_namespace)
            if 'name' in heater_switch:
                sensor_states = self.ADapi.get_state(namespace = namespace)
                for sensor_id, sensor_states in sensor_states.items():
                    if 'switch.' + heater_switch['name'] in sensor_id:
                        if not 'switch' in heater_switch:
                            heater_switch['switch'] = sensor_id
                    if (
                        'sensor.' + heater_switch['name'] + '_electric_consumption_w' in sensor_id
                        or 'sensor.' + heater_switch['name'] + '_electric_consumed_w' in sensor_id
                    ):
                        if not 'consumptionSensor' in heater_switch:
                            heater_switch['consumptionSensor'] = sensor_id
                    if (
                        'sensor.' + heater_switch['name'] + '_electric_consumption_kwh' in sensor_id
                        or 'sensor.' + heater_switch['name'] + '_electric_consumed_kwh' in sensor_id
                    ):
                        if not 'kWhconsumptionSensor' in heater_switch:
                            heater_switch['kWhconsumptionSensor'] = sensor_id

            if not 'switch' in heater_switch:
                self.ADapi.log(
                    "'switch' not found or configured in on_off_switch configuration. "
                    "on_off_switch control setup aborted",
                    level = 'WARNING'
                )
                continue

            if not 'consumptionSensor' in heater_switch:
                validConsumptionSensor = False
                heatername = (str(heater_switch['switch'])).split('.')
                heater_switch['consumptionSensor'] = 'input_number.' + heatername[1] + '_power'
                if not self.ADapi.entity_exists(heater_switch['consumptionSensor'], namespace = namespace):
                    powercapability = heater_switch.get('power', 1000)
                    self.ADapi.call_service("state/set",
                        entity_id = heater['consumptionSensor'],
                        attributes = {'friendly_name' : str(heatername[1]) + ' Power'},
                        state = powercapability,
                        namespace = namespace
                    )

                self.ADapi.log(
                    f"'consumptionSensor' not found or configured. on_off_switch electricity control not optimal. "
                    f"Using {heater_switch['consumptionSensor']} as state with power: "
                    f"{self.ADapi.get_state(heater_switch['consumptionSensor'], namespace = namespace)}",
                    level = 'WARNING'
                )
                if not 'power' in heater_switch:
                    self.ADapi.log(f"Set electrical consumption with 'power' in args for on_off_switch.", level = 'INFO')

            if not 'kWhconsumptionSensor' in heater_switch:
                heater_switch['kWhconsumptionSensor'] = 'input_number.zero'
                if not self.ADapi.entity_exists(heater_switch['kWhconsumptionSensor'], namespace = namespace):
                    self.ADapi.call_service("state/set",
                        entity_id = heater['kWhconsumptionSensor'],
                        attributes = {'friendly_name' : 'Zero consumption helper'},
                        state = 0,
                        namespace = namespace
                    )
                self.ADapi.log(
                    "'kWhconsumptionSensor' not found or configured. on_off_switch electricity logging not available. "
                    "Using input_number.zero as state",
                    level = 'WARNING'
                )

            on_off_switch = On_off_switch(api = self.ADapi,
                heater = heater_switch['switch'],
                consumptionSensor = heater_switch['consumptionSensor'],
                validConsumptionSensor = validConsumptionSensor,
                kWhconsumptionSensor = heater_switch['kWhconsumptionSensor'],
                max_continuous_hours = heater_switch.get('max_continuous_hours',8),
                on_for_minimum = heater_switch.get('on_for_minimum',6),
                pricedrop = heater_switch.get('pricedrop',0.3),
                pricedifference_increase = heater_switch.get('pricedifference_increase', 1.07),
                namespace = heater_switch.get('namespace', self.HASS_namespace),
                away = heater_switch.get('vacation', self.away_state),
                automate = heater_switch.get('automate', self.automate),
                recipient = heater_switch.get('recipient', None),
                json_path=self.json_path
            )
            self.heaters.append(on_off_switch)



        # Variables for different calculations
        self.accumulated_unavailable:int = 0
        self.last_accumulated_kWh:float = 0
        self.accumulated_kWh_wasUnavailable:bool = False
        self.SolarProducing_ChangeToZero:bool = False
        self.notify_about_overconsumption:bool = False
        self.totalWattAllHeaters:float = 0
        self.houseIsOnFire:bool = False

        self.checkIdleConsumption_Handler = None

        # Schedule regular checks and event listeners
        runtime = get_next_runtime(offset_seconds=0, delta_in_seconds=60)
        self.ADapi.run_every(self.checkElectricalUsage, runtime, 60)
        self.ADapi.run_daily(self._get_new_prices, "00:03:00")
        self.ADapi.run_daily(self._get_new_prices, "13:01:00")
        self.ADapi.run_in(self._get_new_prices, 60)
        self.ADapi.listen_event(self._notify_event, "mobile_app_notification_action", namespace=self.HASS_namespace)

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
            self.ADapi.log("Translation file not found", level='DEBUG')
        
        self.ADapi.listen_event(self.mode_event, event_listen_str, namespace = self.HASS_namespace)

    def _init_collections(self):
        self.chargers: list = []
        self.cars: list = []
        self.appliances: list = []
        self.heaters: list = []

        self.queueChargingList: list = [] # Cars currently charging.
        self.solarChargingList: list = [] # Cars currently charging on solar only.

    def _setup_notify_app(self):
        global RECIPIENTS
        global NOTIFY_APP
        name_of_notify_app = self.args.get('notify_app', None)
        RECIPIENTS = self.args.get('notify_receiver', [])
        if name_of_notify_app is not None:
            NOTIFY_APP = self.ADapi.get_app(name_of_notify_app)
        else:
            NOTIFY_APP = Notify_Mobiles(self.ADapi, self.HASS_namespace)

    def _setup_electricity_price(self):
        global ELECTRICITYPRICE
        if 'electricalPriceApp' in self.args:
            ELECTRICITYPRICE = self.ADapi.get_app(self.args['electricalPriceApp'])
        else:
            raise Exception(
                "\nFrom version 0.3.0 the electrical price calculations have been moved to own repository.\n"
                "This can be found here: https://github.com/Pythm/ElectricalPriceCalc \n"
                "Please add the app and configure with 'electricalPriceApp'. Check out readme for more info.\n"
                "Aborting Electrical Usage setup."
            )

    def _validate_current_consumption_sensor(self):
        self.current_consumption = self.args.get('power_consumption', None) # In Watt
        if not self.current_consumption:
            raise Exception(
                "power_consumption sensor not provided in configuration. Aborting Electrical Usage setup."
                "Please provide a watt power consumption sensor to use this function"
            )
        try:
            float(self.ADapi.get_state(self.current_consumption))
        except ValueError as ve:
            if self.ADapi.get_state(self.current_consumption) == 'unavailable':
                self.ADapi.log(f"Current consumption is unavailable at startup", level='DEBUG')
            else:
                raise Exception()
        except Exception as e:
            self.ADapi.log(
                f"power_consumption sensor is not a number. Please provide a watt power consumption sensor for this function",
                level='WARNING'
            )
            self.ADapi.log(
                "If power_consumption should be a number and this error occurs after a restart, "
                "your sensor has probably not started sending data.",
                level='INFO'
            )
            self.ADapi.log(e, level='DEBUG')

    def _setup_accumulated_consumption_current_hour(self):
        if 'accumulated_consumption_current_hour' in self.args:
            self.accumulated_consumption_current_hour = self.args['accumulated_consumption_current_hour']
        else:
            sensor_states = self.ADapi.get_state(namespace=self.HASS_namespace)
            for sensor_id in sensor_states.keys():
                if 'accumulated_consumption_current_hour' in sensor_id:
                    self.accumulated_consumption_current_hour = sensor_id
                    break

        if not self.accumulated_consumption_current_hour:
            raise Exception(
                "accumulated_consumption_current_hour not found. "
                "Please install Tibber Pulse or input equivalent to provide kWh consumption current hour."
            )
            self.ADapi.log(
                "Check out https://tibber.com/ to learn more. "
                "If you are interested in switching to Tibber, you can use my invite link to get a startup bonus: "
                "https://invite.tibber.com/fydzcu9t"
                " or contact me for an invite code.",
                level='INFO'
            )

        attr_last_updated = self.ADapi.get_state(
            entity_id=self.accumulated_consumption_current_hour,
            attribute="last_updated"
        )
        if not attr_last_updated:
            self.ADapi.log(
                f"{self.ADapi.get_state(self.accumulated_consumption_current_hour)} has no 'last_updated' attribute. Function might fail",
                level='INFO'
            )

    def _setup_power_production_sensors(self):
        self.current_production = self.args.get('power_production', None)  # Watt
        self.accumulated_production_current_hour = self.args.get('accumulated_production_current_hour', None)  # kWh

    def _load_persistent_data(self):
        try:
            with open(self.json_path, 'r') as json_read:
                ElectricityData = json.load(json_read, object_hook=json_deserialize)
                if 'chargingQueue' in ElectricityData:
                    CHARGE_SCHEDULER.chargingQueue = ElectricityData['chargingQueue']
                if 'queueChargingList' in ElectricityData:
                    self.queueChargingList = ElectricityData['queueChargingList']
                if 'solarChargingList' in ElectricityData:
                    self.solarChargingList = ElectricityData['solarChargingList']
        except FileNotFoundError:
            ElectricityData = {"MaxUsage": {"max_kwh_usage_pr_hour": self.max_kwh_goal, "topUsage": [0, 0, 0]},
                               "charger": {},
                               "car": {},
                               "consumption": {"idleConsumption": {"ConsumptionData": {}}}}
            with open(self.json_path, 'w') as json_write:
                json.dump(ElectricityData, json_write, default=json_serial, indent=4)
            self.ADapi.log(
                f"Json file created at {self.json_path}",
                level='INFO'
            )
        self.max_kwh_usage_pr_hour: int = ElectricityData['MaxUsage']['max_kwh_usage_pr_hour']
        self.top_usage_hour: float = ElectricityData['MaxUsage']['topUsage'][0]

    def _generate_available_watt_list(self):
        self.timedelta_for_dictionaries:float = 0
        available_Wh = (self.max_kwh_usage_pr_hour - self.buffer) * 1000
        availableWatt = []
        for item in ELECTRICITYPRICE.elpricestoday:
            if self.timedelta_for_dictionaries == 0:
                duration = item['end'] - item['start']
                self.timedelta_for_dictionaries = duration.total_seconds() / 3600.0
                available_Wh *= self.timedelta_for_dictionaries

            item_dict = {
                'start': item['start'],
                'end': item['end'],
                'available_Wh': available_Wh
            }
            availableWatt.append(item_dict)

        CHARGE_SCHEDULER.availableWatt = availableWatt
        CHARGE_SCHEDULER.timedelta_for_dictionaries = self.timedelta_for_dictionaries

    def _get_vacation_state(self):
        away_state = self.args.get('away_state') or self.args.get('vacation')
        if not away_state and self.ADapi.entity_exists('input_boolean.vacation', namespace=self.HASS_namespace):
            away_state = 'input_boolean.vacation'

        # Set up listener for state changes
        if away_state:
            self.ADapi.listen_state(self._awayStateListen_Main, away_state,
                namespace=self.HASS_namespace)
            return self.ADapi.get_state(away_state, namespace = self.HASS_namespace)  == 'on'

        return False

    def _setup_weather_sensors(self):
        self.out_temp_last_update = self.ADapi.datetime(aware=True) - datetime.timedelta(minutes=20)
        self.rain_last_update = self.ADapi.datetime(aware=True) - datetime.timedelta(minutes=20)
        self.wind_last_update = self.ADapi.datetime(aware=True) - datetime.timedelta(minutes=20)

        global OUT_TEMP
        if (outside_temperature := self.args.get('outside_temperature')):
            self.ADapi.listen_state(self._outsideTemperatureUpdated, outside_temperature)
            try:
                OUT_TEMP = float(self.ADapi.get_state(outside_temperature))
            except (ValueError, TypeError):
                self.ADapi.log(f"Outside temperature is not valid.", level='DEBUG')

        # Setup Rain sensor
        self.rain_level: float = self.args.get('rain_level',3)

        global RAIN_AMOUNT
        if (rain_sensor := self.args.get('rain_sensor')):
            self.ADapi.listen_state(self._rainSensorUpdated, rain_sensor)
            try:
                RAIN_AMOUNT = float(self.ADapi.get_state(rain_sensor))
            except ValueError as ve:
                RAIN_AMOUNT = 0.0
                self.ADapi.log(f"Rain sensor not valid. {ve}", level='DEBUG')

        # Setup Wind sensor
        self.anemometer_speed:float = self.args.get('anemometer_speed',40)

        global WIND_AMOUNT
        if (anemometer := self.args.get('anemometer')):
            self.ADapi.listen_state(self._anemometerUpdated, anemometer)
            try:
                WIND_AMOUNT = float(self.ADapi.get_state(anemometer))
            except ValueError as ve:
                WIND_AMOUNT = 0.0
                self.ADapi.log(f"Anemometer sensor not valid. {ve}", level='DEBUG')

        self.ADapi.listen_event(self.weather_event, 'WEATHER_CHANGE', namespace=self.HASS_namespace)

    def _process_heater(self, heater: dict) -> None:
        """Create a Climate object after resolving / creating all required sensors."""
        namespace = heater.get('namespace', self.HASS_namespace)
        valid_consumption = True
        sensor_states = self.ADapi.get_state(namespace=namespace)

        if 'name' in heater and not heater.get('heater'):
            for sensor_id, _ in sensor_states.items():
                if sensor_id.startswith(f"climate.{heater['name']}"):
                    heater['heater'] = sensor_id
                    self.ADapi.log(
                        f"Added {heater['heater']} to ElectricalManagement based on name: {heater['name']}",
                        level='INFO',
                        namespace=namespace,
                    )
                    break

        log_indoor_sens = True
        for sensor_id, _ in sensor_states.items():
            # power consumption (W)
            if (sensor_id.endswith('_electric_consumption_w') or
                sensor_id.endswith('_electric_consumed_w')):
                if not heater.get('consumptionSensor'):
                    heater['consumptionSensor'] = sensor_id

            # power consumption (kWh)
            if (sensor_id.endswith('_electric_consumption_kwh') or
                sensor_id.endswith('_electric_consumed_kwh')):
                if not heater.get('kWhconsumptionSensor'):
                    heater['kWhconsumptionSensor'] = sensor_id

            # indoor temperature sensor
            if sensor_id.endswith('_air_temperature'):
                if not heater.get('indoor_sensor_temp') and log_indoor_sens:
                    heater['indoor_sensor_temp'] = sensor_id
                    self.ADapi.log(
                        (
                            f"No external indoor temperature sensor for {heater['name']} "
                            f"is configured. Automation will not check if it is hot inside. "
                            f"Found sensor {sensor_id}. "
                            f"This can be configured with 'indoor_sensor_temp' if applicable."
                        ),
                        level='INFO',
                        namespace=namespace,
                    )
                    log_indoor_sens = False

        if not heater.get('heater'):
            self.ADapi.log(
                f"'heater' not found or configured in {heater} climate configuration. Climate control setup aborted",
                level='WARNING',
                namespace=namespace,
            )
            return

        if not heater.get('consumptionSensor'):
            valid_consumption = False
            heater_name = heater['heater'].split('.')[1]
            fallback = f"input_number.{heater_name}_power"
            heater['consumptionSensor'] = fallback

            if not self.ADapi.entity_exists(fallback, namespace=namespace):
                power_cap = heater.get('power', 300)
                self.ADapi.call_service(
                    "state/set",
                    entity_id=fallback,
                    attributes={'friendly_name': f"{heater_name} Power"},
                    state=power_cap,
                    namespace=namespace,
                )

            self.ADapi.log(
                (
                    f"'consumptionSensor' not found or configured. "
                    f"Climate electricity control not optimal. "
                    f"Using {fallback} as state with power: "
                    f"{self.ADapi.get_state(fallback, namespace=namespace)}"
                ),
                level='WARNING',
                namespace=namespace,
            )
            if 'power' not in heater:
                self.ADapi.log(
                    f"Set electrical consumption with 'power' in args for heater {heater_name}.",
                    level='INFO',
                    namespace=namespace,
                )

        if not heater.get('kWhconsumptionSensor'):
            fallback_kwh = "input_number.zero"
            heater['kWhconsumptionSensor'] = fallback_kwh

            if not self.ADapi.entity_exists(fallback_kwh, namespace=namespace):
                self.ADapi.call_service(
                    "state/set",
                    entity_id=fallback_kwh,
                    attributes={'friendly_name': 'Zero consumption helper'},
                    state=0,
                    namespace=namespace,
                )

            self.ADapi.log(
                (
                    "'kWhconsumptionSensor' not found or configured. "
                    "Climate electricity logging not available. "
                    "Using input_number.zero as state"
                ),
                level='WARNING',
                namespace=namespace,
            )

        climate = Climate(
            api=self.ADapi,
            heater=heater['heater'],
            consumptionSensor=heater['consumptionSensor'],
            validConsumptionSensor=valid_consumption,
            kWhconsumptionSensor=heater['kWhconsumptionSensor'],
            max_continuous_hours=heater.get('max_continuous_hours', 2),
            on_for_minimum=heater.get('on_for_minimum', 6),
            pricedrop=heater.get('pricedrop', 1),
            pricedifference_increase=heater.get('pricedifference_increase', 1.07),
            namespace=heater.get('namespace', self.HASS_namespace),
            away=heater.get('vacation', away_state),
            automate=heater.get('automate', self.automate),
            recipient=heater.get('recipient', None),
            indoor_sensor_temp=heater.get('indoor_sensor_temp', None),
            window_temp=heater.get('window_temp', None),
            window_offset=heater.get('window_offset', -3),
            target_indoor_input=heater.get('target_indoor_input', None),
            target_indoor_temp=heater.get('target_indoor_temp', 23),
            save_temp_offset=heater.get('save_temp_offset', None),
            save_temp=heater.get('save_temp', None),
            away_temp=heater.get('away_temp', None),
            rain_level=heater.get('rain_level', self.rain_level),
            anemometer_speed=heater.get('anemometer_speed', self.anemometer_speed),
            low_price_max_continuous_hours=heater.get('low_price_max_continuous_hours', 2),
            priceincrease=heater.get('priceincrease', 1),
            windowsensors=heater.get('windowsensors', []),
            getting_cold=heater.get('getting_cold', 18),
            daytime_savings=heater.get('daytime_savings', []),
            temperatures=heater.get('temperatures', []),
        )
        self.heaters.append(climate)

    def _process_heater_switch(self, heater_switch: dict) -> None:
        """Create an On_off_switch object after resolving / creating all required sensors."""
        namespace = heater_switch.get('namespace', self.HASS_namespace)
        valid_consumption = True

        if 'name' in heater_switch and not heater_switch.get('switch'):
            sensor_states = self.ADapi.get_state(namespace=namespace)
            for sensor_id, _ in sensor_states.items():
                if sensor_id.startswith(f"switch.{heater_switch['name']}"):
                    heater_switch['switch'] = sensor_id
                    self.ADapi.log(
                        f"Added {heater_switch['switch']} to on_off_switch based on name: {heater_switch['name']}",
                        level='INFO',
                        namespace=namespace,
                    )
                    break

        sensor_states = self.ADapi.get_state(namespace=namespace)
        for sensor_id, _ in sensor_states.items():
            # power consumption (W)
            if (sensor_id.endswith('_electric_consumption_w') or
                sensor_id.endswith('_electric_consumed_w')):
                if not heater_switch.get('consumptionSensor'):
                    heater_switch['consumptionSensor'] = sensor_id

            # power consumption (kWh)
            if (sensor_id.endswith('_electric_consumption_kwh') or
                sensor_id.endswith('_electric_consumed_kwh')):
                if not heater_switch.get('kWhconsumptionSensor'):
                    heater_switch['kWhconsumptionSensor'] = sensor_id

        if not heater_switch.get('switch'):
            self.ADapi.log(
                "'switch' not found or configured in on_off_switch configuration. "
                "on_off_switch control setup aborted",
                level='WARNING',
                namespace=namespace,
            )
            return

        if not heater_switch.get('consumptionSensor'):
            valid_consumption = False
            switch_name = heater_switch['switch'].split('.')[1]
            fallback = f"input_number.{switch_name}_power"
            heater_switch['consumptionSensor'] = fallback

            if not self.ADapi.entity_exists(fallback, namespace=namespace):
                power_cap = heater_switch.get('power', 1000)
                self.ADapi.call_service(
                    "state/set",
                    entity_id=fallback,
                    attributes={'friendly_name': f"{switch_name} Power"},
                    state=power_cap,
                    namespace=namespace,
                )

            self.ADapi.log(
                (
                    f"'consumptionSensor' not found or configured. "
                    f"on_off_switch electricity control not optimal. "
                    f"Using {fallback} as state with power: "
                    f"{self.ADapi.get_state(fallback, namespace=namespace)}"
                ),
                level='WARNING',
                namespace=namespace,
            )
            if 'power' not in heater_switch:
                self.ADapi.log(
                    "Set electrical consumption with 'power' in args for on_off_switch.",
                    level='INFO',
                    namespace=namespace,
                )

        if not heater_switch.get('kWhconsumptionSensor'):
            fallback_kwh = "input_number.zero"
            heater_switch['kWhconsumptionSensor'] = fallback_kwh

            if not self.ADapi.entity_exists(fallback_kwh, namespace=namespace):
                self.ADapi.call_service(
                    "state/set",
                    entity_id=fallback_kwh,
                    attributes={'friendly_name': 'Zero consumption helper'},
                    state=0,
                    namespace=namespace,
                )

            self.ADapi.log(
                (
                    "'kWhconsumptionSensor' not found or configured. "
                    "on_off_switch electricity logging not available. "
                    "Using input_number.zero as state"
                ),
                level='WARNING',
                namespace=namespace,
            )

        on_off_switch = On_off_switch(
            api=self.ADapi,
            heater=heater_switch['switch'],
            consumptionSensor=heater_switch['consumptionSensor'],
            validConsumptionSensor=valid_consumption,
            kWhconsumptionSensor=heater_switch['kWhconsumptionSensor'],
            max_continuous_hours=heater_switch.get('max_continuous_hours', 8),
            on_for_minimum=heater_switch.get('on_for_minimum', 6),
            pricedrop=heater_switch.get('pricedrop', 0.3),
            pricedifference_increase=heater_switch.get('pricedifference_increase', 1.07),
            namespace=heater_switch.get('namespace', self.HASS_namespace),
            away=heater_switch.get('vacation', away_state),
            automate=heater_switch.get('automate', self.automate),
            recipient=heater_switch.get('recipient', None),
        )
        self.heaters.append(on_off_switch)


    # Finished initialization...


    def terminate(self) -> None:
        """ Writes charger and car data to persisten storage before terminating app.
        """
        try:
            with open(self.json_path, 'r') as json_read:
                ElectricityData = json.load(json_read)
            for charger in self.chargers:
                if charger.charger_id in ElectricityData['charger']:
                    car_connected = None
                    if charger.Car is not None:
                        car_connected = charger.Car.carName

                    ElectricityData['charger'][charger.charger_id].update({
                        "voltPhase" : charger.voltPhase,
                        "MaxAmp" : charger.maxChargerAmpere,
                        "ConnectedCar" : car_connected
                    })

            for car in self.cars:
                if car.vehicle_id in ElectricityData['car']:

                    ElectricityData['car'][car.vehicle_id].update({
                        "CarLimitAmpere" : car.car_limit_max_charging,
                        "MaxkWhCharged" : car.maxkWhCharged,
                        "batterysize" : car.battery_size,
                        "Counter" : car.battery_reg_counter
                    })

            completeQueue:list = []
            if CHARGE_SCHEDULER.chargingQueue:
                completeQueue = CHARGE_SCHEDULER.chargingQueue

            for heater in self.heaters:
                if heater.heater in ElectricityData['consumption']:
                    ElectricityData['consumption'][heater.heater].update({'peak_hours' : heater.time_to_save})

            ElectricityData['chargingQueue'] = completeQueue
            ElectricityData['queueChargingList'] = self.queueChargingList
            ElectricityData['solarChargingList'] = self.solarChargingList

            with open(self.json_path, 'w') as json_write:
                try:
                    json.dump(ElectricityData, json_write, default=json_serial, indent = 4)
                except Exception as e:
                    self.ADapi.log(f"Error occurred while writing to JSON on Terminate: {e}")

        except FileNotFoundError:
            self.ADapi.log(f"FileNotFound when ElectricityManagement Terminated", level = 'INFO')

    def _get_new_prices(self, kwargs) -> None:
        if (
            not ELECTRICITYPRICE.tomorrow_valid
            and self.ADapi.now_is_between('12:30:00', '15:30:00')
        ):
            self.ADapi.run_in(self._get_new_prices, 600)
            return # Wait until prices tomorrow is valid

        for heater in self.heaters:
            if (
                len(heater.time_to_save) == 0 # if not run before (Empty list)
                or ELECTRICITYPRICE.tomorrow_valid # if tomorrow price is found
                or self.ADapi.now_is_between('00:00:00', '12:00:00') # Before tomorrow price is expected
            ):
                self.ADapi.run_in(heater.heater_getNewPrices, delay = 20, random_start = 1, random_end = 2)

        if ELECTRICITYPRICE.tomorrow_valid:
            for c in self.cars:
                if c.isConnected():
                    self.ADapi.run_in(c.findNewChargeTimeAt, 40)

        self.ADapi.run_in(self.calculateIdleConsumption, 120)
        self.ADapi.run_in(self._run_find_consumption_after_turned_back_on, 600)
            
        if self.checkIdleConsumption_Handler is not None:
            if self.ADapi.timer_running(self.checkIdleConsumption_Handler):
                try:
                    self.ADapi.cancel_timer(self.checkIdleConsumption_Handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Was not able to stop existing handler to log consumption. {e}",
                        level = "DEBUG"
                    )
            self.checkIdleConsumption_Handler = None

        if (
            not self.away_state
            and self.ADapi.now_is_between('00:00:00', '03:30:00')
            and not self.queueChargingList
        ):
            self.checkIdleConsumption_Handler = self.ADapi.run_at(self.logIdleConsumption, "04:30:00")

    def _run_find_consumption_after_turned_back_on(self, kwargs):
        for heater in self.heaters:
            for item in heater.time_to_save:
                self.ADapi.run_at(self.findConsumptionAfterTurnedBackOn, item['end'], heater = heater, time_to_save_item = item)

    def checkElectricalUsage(self, kwargs) -> None:
        """ Calculate and ajust consumption to stay within kWh limit.
            Start charging when time to charge.
        """
        accumulated_kWh = self.ADapi.get_state(self.accumulated_consumption_current_hour)
        current_consumption = self.ADapi.get_state(self.current_consumption)

        runtime = datetime.datetime.now()
        remaining_minute:int = 60 - int(runtime.minute)

            # Check if consumption sensors is valid
        if current_consumption in ['unavailable','unknown']:
            current_consumption:float = 0.0
            with open(self.json_path, 'r') as json_read:
                ElectricityData = json.load(json_read)

            if ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                multiply_with_to_be_safe:float = 2
                    # Find closest temp registered with data
                try:
                    closest_temp = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
                except Exception:
                    temp_diff:int = 100
                    closest_temp:int
                    for temps in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                        if OUT_TEMP > float(temps):
                            if temp_diff < OUT_TEMP - float(temps):
                                continue
                            temp_diff = OUT_TEMP - float(temps)
                            closest_temp = temps
                        else:
                            if temp_diff < float(temps) - OUT_TEMP:
                                continue
                            temp_diff = float(temps) - OUT_TEMP
                            closest_temp = temps

                    out_temp_str = str(closest_temp)

                current_consumption = (
                    float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['Consumption'])
                    * multiply_with_to_be_safe
                )
            for heater in self.heaters:
                if heater.validConsumptionSensor:
                    try:
                        current_consumption += float(self.ADapi.get_state(heater.consumptionSensor,
                            namespace = heater.namespace))
                    except Exception:
                        pass
            for c in self.cars:
                if (
                    c.isConnected()
                    and c.getCarChargerState() == 'Charging'
                    and c.connectedCharger is not None
                ):
                    try:
                        current_consumption += c.connectedCharger.ampereCharging * c.connectedCharger.voltPhase
                    except Exception:
                        self.ADapi.log(
                            f"Not able to get charging info when current consumption is unavailable from {type(c.connectedCharger).__name__}",
                            level = 'WARNING'
                        )          
        else:
            current_consumption = float(current_consumption)
        try:
            accumulated_kWh = float(accumulated_kWh)
        except Exception as e:
            if self.accumulated_unavailable > 5:
                # Will try to reload Home Assistant integration every sixth minute the sensor is unavailable. 
                self.accumulated_unavailable = 0
                self.ADapi.create_task(self._reload_accumulated_consumption_sensor())
            else:
                self.accumulated_unavailable += 1
            try:
                accumulated_kWh = self.last_accumulated_kWh
            except Exception as e:
                accumulated_kWh = round(float(runtime.minute/60) * (self.max_kwh_usage_pr_hour - self.buffer),2)
                self.ADapi.log(f"Failed to get last accumulated kwh. Exception: {e}", level = 'WARNING')

            accumulated_kWh = round(self.last_accumulated_kWh + (current_consumption/60000),2)
            self.last_accumulated_kWh = accumulated_kWh
            self.accumulated_kWh_wasUnavailable = True
        else:
            if self.accumulated_kWh_wasUnavailable:
                # Log estimated during unavailable vs actual
                self.accumulated_kWh_wasUnavailable = False
                if self.last_accumulated_kWh + (current_consumption/60000) < accumulated_kWh:
                    self.ADapi.log(
                        f"Accumulated kWh was unavailable. Estimated: {round(self.last_accumulated_kWh + (current_consumption/60000),2)}. "
                        f"Actual: {accumulated_kWh}",
                        level = 'INFO'
                    )
            self.last_accumulated_kWh = accumulated_kWh
            attr_last_updated = self.ADapi.get_state(entity_id = self.accumulated_consumption_current_hour,
                attribute = "last_updated"
            )
            if not attr_last_updated:
                last_update: datetime = self.ADapi.datetime(aware=True)
            else:
                last_update = self.ADapi.convert_utc(attr_last_updated)

            now: datetime = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time > datetime.timedelta(minutes = 2): # Stale for more than two minutes. Reload integration
                self.ADapi.call_service('homeassistant/reload_config_entry',
                    entity_id = self.accumulated_consumption_current_hour
                )
                if runtime.minute < 2:
                    accumulated_kWh = 1
                    self.last_accumulated_kWh = 1
                else:
                    accumulated_kWh += 0.5
                    self.last_accumulated_kWh += 0.5
                return

            # Check if production sensors exists and valid
        if self.current_production:
            current_production = self.ADapi.get_state(self.current_production)
            if current_production in ['unavailable', 'unknown']:
                current_production = 0
        else:
            current_production = 0
        if self.accumulated_production_current_hour:
            production_kWh = self.ADapi.get_state(self.accumulated_production_current_hour)
            if production_kWh in ['unavailable', 'unknown']:
                production_kWh = 0
        else:
            production_kWh = 0

            # Calculations used to adjust consumption
        max_target_kWh_buffer:float = round(
            ((self.max_kwh_usage_pr_hour
            - self.buffer) * (runtime.minute/60))
            - (accumulated_kWh - production_kWh),2
        )
        projected_kWh_usage:float = round(
            ((current_consumption - current_production) /60000)
            * remaining_minute,2
        )
        available_Wh:float = round(
            (self.max_kwh_usage_pr_hour
            - self.buffer
            + (max_target_kWh_buffer * (60 / remaining_minute)))*1000
            - current_consumption,2
        )

        if runtime.minute == 0:
            # Resets and logs every hour
            self.last_accumulated_kWh = 0
            if (
                datetime.datetime.now().hour == 0
                and datetime.datetime.now().day == 1
            ):
                self.resetHighUsage()

            elif accumulated_kWh > self.top_usage_hour:
                self.logHighUsage()

            for c in self.cars:
                if (
                    c.isConnected()
                    and c.getCarChargerState() == 'Charging'
                    and not self.SolarProducing_ChangeToZero
                    and not c.dontStopMeNow()
                ):
                    if CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = c.vehicle_id):
                        if c.priority == 1 or c.priority == 2:
                            pass
                        else:
                            c.stopCharging()
                            self.ADapi.log(
                                f"Was not able to finish charging {c.carName} with {c.kWhRemaining()} kWh remaining before prices increased."
                                f"Consider adjusting startBeforePrice {CHARGE_SCHEDULER.startBeforePrice} and "
                                f"stopAtPriceIncrease {CHARGE_SCHEDULER.stopAtPriceIncrease} in configuration",
                                level = 'INFO'
                            )
                            data = {
                                'tag' : 'charging' + str(c.carName),
                                'actions' : [{ 'action' : 'find_new_chargetime'+str(c.carName), 'title' : f'Find new chargetime for {c.carName}' }]
                                }
                            NOTIFY_APP.send_notification(
                                message = f"Was not able to finish with {round(c.kWhRemaining(),2)} kWh remaining before prices increased.",
                                message_title = f"🚘Charging {c.carName}",
                                message_recipient = RECIPIENTS,
                                also_if_not_home = False,
                                data = data
                            )
                    elif not CHARGE_SCHEDULER.isChargingTime(vehicle_id = c.vehicle_id):
                        c.kWhRemaining()
                        c.findNewChargeTime()

            """ Change consumption if above target or below production: """
        elif (
            projected_kWh_usage + accumulated_kWh > self.max_kwh_usage_pr_hour - self.buffer
            or max_target_kWh_buffer < 0
        ):
            # Current consuption is on it´s way to go over max kWh usage pr hour. Redusing usage
            if (
                available_Wh > -800
                and remaining_minute > 15
                and not self.heatersRedusedConsumption
            ):
                return

            if self._updateChargingQueue():
                reduce_Wh, available_Wh = self.getHeatersReducedPreviousConsumption(available_Wh)

                if  reduce_Wh + available_Wh < 0 :
                    available_Wh = self.reduceChargingAmpere(available_Wh, reduce_Wh)
            if (
                runtime.minute > 7
                or not self.queueChargingList
            ):
                for heater in self.heaters:
                    if available_Wh < -100:
                        try:
                            heater.prev_consumption = float(self.ADapi.get_state(heater.consumptionSensor,
                                namespace = heater.namespace)
                            )
                        except ValueError:
                            pass
                        else:
                            if (
                                heater.prev_consumption > 100
                                and heater not in self.heatersRedusedConsumption
                            ):
                                self.heatersRedusedConsumption.append(heater)
                                heater.setSaveState()
                                if (
                                    self.ADapi.get_state(heater.heater,
                                        attribute = 'hvac_action',
                                        namespace = heater.namespace
                                    ) == 'heating'
                                    or heater.validConsumptionSensor
                                ):
                                    available_Wh += heater.prev_consumption
                    else:
                        return

            if (
                (self.max_kwh_usage_pr_hour + (max_target_kWh_buffer * (60 / remaining_minute)))*1000 - current_consumption < -100
                and datetime.datetime.now() - self.lastTimeHeaterWasReduced > datetime.timedelta(minutes = 3)
                and remaining_minute <= 40
            ):
                if self.pause_charging:
                    for queue_id in  reversed(self.queueChargingList):
                        for c in self.chargers:
                            if c.Car is not None:
                                if (
                                    c.Car.connectedCharger is c
                                    and c.Car.vehicle_id == queue_id
                                ):
                                    if c.getChargingState() == 'Charging':
                                        available_Wh += c.ampereCharging * c.voltPhase
                                        c.stopCharging(force_stop = True)
                                        if available_Wh > -100:
                                            return
                                    break
                if self.notify_overconsumption:
                    if self.notify_about_overconsumption:
                        self.notify_about_overconsumption = False
                        data = {
                            'tag' : 'overconsumption'
                            }

                        NOTIFY_APP.send_notification(
                            message = f"Turn down consumption. Is's about to go over max usage with {round(-available_Wh,0)} "
                            "remaining to reduce",
                            message_title = "⚡High electricity usage",
                            message_recipient = RECIPIENTS,
                            also_if_not_home = False,
                            data = data
                        )
                    else:
                        self.notify_about_overconsumption = True

        elif self.heatersRedusedConsumption:
            # Reduce charging speed to turn heaters back on
            self.notify_about_overconsumption = False

            reduce_Wh, available_Wh = self.getHeatersReducedPreviousConsumption(available_Wh)
            
            if (
                self._updateChargingQueue()
                and reduce_Wh + available_Wh < 0
            ):
                available_Wh = self.reduceChargingAmpere(available_Wh, reduce_Wh)

        elif (
            accumulated_kWh <= production_kWh
            and projected_kWh_usage < 0
        ):
            # Production is higher than consumption
            # TODO: Not tested properly

            self.notify_about_overconsumption = False
            self.SolarProducing_ChangeToZero = True
            overproduction_Wh:float = round(current_production - current_consumption , 2)

            # Check if any heater is reduced
            if self.heatersRedusedConsumption:
                for heater in reversed(self.heatersRedusedConsumption):
                    if heater.prev_consumption < overproduction_Wh:
                        heater.setPreviousState()
                        overproduction_Wh -= heater.prev_consumption
                        self.heatersRedusedConsumption.remove(heater)

            # TODO: If chargetime: Calculate if production is enough to charge wanted amount

            if not self.solarChargingList :
                # Check if any is charging, or is not finished
                for c in self.cars:
                    if (
                        c.isConnected()
                        and c.connectedCharger is not None
                    ):
                        if c.getCarChargerState() == 'Charging':
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.vehicle_id)
                        elif (
                            (c.getCarChargerState() == 'Stopped'
                            or c.getCarChargerState() == 'awaiting_start')
                            and c.car_battery_soc() < c.pref_charge_limit
                            and overproduction_Wh > 1600
                        ):
                            c.startCharging()
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.vehicle_id)
                            AmpereToCharge = math.ceil(overproduction_Wh / c.voltPhase)
                            c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return
                    elif c.isConnected():
                        c.connectedCharger = c.onboardCharger

                # Check if any is below prefered charging limit
                for c in self.cars:
                    if (
                        c.isConnected()
                        and c.connectedCharger is not None
                    ):
                        if c.getCarChargerState() == 'Charging':
                            self.solarChargingList.append(c.vehicle_id)
                            c.charging_on_solar = True
                        elif (
                            c.pref_charge_limit > c.oldChargeLimit
                        ):
                            c.charging_on_solar = True
                            c.changeChargeLimit(c.pref_charge_limit)
                            c.startCharging()
                            self.solarChargingList.append(c.vehicle_id)
                            AmpereToCharge = math.ceil(overproduction_Wh / c.voltPhase)
                            c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return

            else :
                for queue_id in self.solarChargingList:
                    for c in self.cars:
                        ChargingState = c.getCarChargerState()
                        if (
                            c.vehicle_id == queue_id
                            and c.connectedCharger is not None
                        ):
                            if ChargingState == 'Charging':
                                AmpereToIncrease = math.ceil(overproduction_Wh / c.voltPhase)
                                c.connectedCharger.changeChargingAmps(charging_amp_change = AmpereToIncrease)
                                return
                            elif (
                                ChargingState == 'Complete'
                                and c.car_battery_soc() >= c.pref_charge_limit
                            ):
                                c.charging_on_solar = False
                                c.changeChargeLimit(c.oldChargeLimit)
                                try:
                                    self.solarChargingList.remove(queue_id)
                                except Exception as e:
                                    self.ADapi.log(f"{c.carName} was not in solarChargingList. Exception: {e}", level = 'DEBUG')
                            elif ChargingState == 'Complete':
                                c.charging_on_solar = False
                                try:
                                    self.solarChargingList.remove(queue_id)
                                except Exception as e:
                                    self.ADapi.log(f"{c.carName} was not in solarChargingList. Exception: {e}", level = 'DEBUG')
                return
            # Set spend in heaters
            for heater in self.heaters:
                if (
                    float(self.ADapi.get_state(heater.consumptionSensor, namespace = heater.namespace)) < 100
                    and not heater.increase_now
                    and heater.normal_power < overproduction_Wh
                ):
                    heater.setIncreaseState()
                    overproduction_Wh -= heater.normal_power
        elif (
            (accumulated_kWh > production_kWh
            or projected_kWh_usage > 0)
            and self.SolarProducing_ChangeToZero
        ):
            # Consumption is higher than production
            # TODO: Not tested properly

            self.notify_about_overconsumption = False
            overproduction_Wh:float = round(current_production - current_consumption , 2)

            # Remove spend in heaters
            for heater in self.heaters:
                if overproduction_Wh > 0:
                    return
                if heater.increase_now:
                    heater.setPreviousState()
                    overproduction_Wh += heater.normal_power

            # Reduce any chargers/batteries
            for queue_id in reversed(self.solarChargingList):
                for c in self.chargers:
                    if c.Car is not None:
                        if (
                            c.Car.connectedCharger is c
                            and c.Car.vehicle_id == queue_id
                        ):
                            if c.ampereCharging == 0:
                                c.ampereCharging = math.floor(float(self.ADapi.get_state(c.charging_amps,
                                    namespace = c.namespace))
                                )
                            if c.ampereCharging > c.min_ampere:
                                AmpereToReduce = math.floor(overproduction_Wh / c.voltPhase)
                                if (c.ampereCharging + AmpereToReduce) < c.min_ampere:
                                    c.setChargingAmps(charging_amp_set = c.min_ampere)
                                    overproduction_Wh += (c.ampereCharging - c.min_ampere) * c.voltPhase
                                    # TODO: Check if remaining available is lower than production and stop charing.
                                else:
                                    c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                    overproduction_Wh += AmpereToReduce * c.voltPhase
                                    break
            if current_production < 1000:
                # TODO: Find proper idle consumption... 
                # If production is low -> stop and reset.
                self.SolarProducing_ChangeToZero = False
                for queue_id in reversed(self.solarChargingList):
                    for c in self.cars:
                        if c.vehicle_id == queue_id:
                            c.charging_on_solar = False
                            c.changeChargeLimit(c.oldChargeLimit)
                            try:
                                self.solarChargingList.remove(queue_id)
                            except Exception as e:
                                self.ADapi.log(f"{c.carName} was not in solarChargingList. Exception: {e}", level = 'DEBUG')

        elif (
            projected_kWh_usage + accumulated_kWh < self.max_kwh_usage_pr_hour - self.buffer
            and max_target_kWh_buffer > 0
            and not self.houseIsOnFire
        ):
            # Increase charging speed or add another charger if time to charge
            self.notify_about_overconsumption = False

            if (
                (remaining_minute > 9 and available_Wh < 800)
                or max_target_kWh_buffer < 0.1
                or datetime.datetime.now() - self.lastTimeHeaterWasReduced < datetime.timedelta(minutes = 4)
            ):
                return

            next_vehicle_id = False
            if self._updateChargingQueue():
                for queue_id in self.queueChargingList:
                    for c in self.cars:
                        if (
                            c.vehicle_id == queue_id
                            and c.connectedCharger is not None
                        ):
                            ChargingState = c.getCarChargerState()
                            if ChargingState in ['Complete', 'Disconnected']:
                                try:
                                    self.queueChargingList.remove(queue_id)
                                except Exception as e:
                                    self.ADapi.log(
                                        f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                        level = 'DEBUG'
                                    )
                                c.connectedCharger._CleanUpWhenChargingStopped()
                                if (
                                    not self.queueChargingList
                                    and self.ADapi.now_is_between('01:00:00', '05:00:00')
                                    and not self.away_state
                                ):
                                    if CHARGE_SCHEDULER.findNextChargerToStart() is None:
                                        if self.ADapi.now_is_between('01:00:00', '04:00:00'):
                                            self.checkIdleConsumption_Handler = self.ADapi.run_at(self.logIdleConsumption, "04:30:00")
                                        else:
                                            self.ADapi.run_in(self.logIdleConsumption, 30)
                                        return

                            elif ChargingState in ['Stopped', 'awaiting_start']:
                                if (
                                    not CHARGE_SCHEDULER.isChargingTime(vehicle_id = c.vehicle_id)
                                    and not c.dontStopMeNow()
                                ):
                                    try:
                                        self.queueChargingList.remove(queue_id)
                                    except Exception as e:
                                        self.ADapi.log(
                                            f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                            level = 'DEBUG'
                                        )

                            elif ChargingState == 'Charging':
                                if (len(CHARGE_SCHEDULER.chargingQueue) > len(self.queueChargingList)
                                    and (c.isChargingAtMaxAmps()
                                    or c.connectedCharger.ampereCharging > 25)):
                                    if (
                                        runtime.minute > 15
                                        and remaining_minute > 12
                                    ):
                                        next_vehicle_id = True
                                else:
                                    next_vehicle_id = False

                                if not c.isChargingAtMaxAmps():
                                    AmpereToIncrease = math.floor(available_Wh / c.connectedCharger.voltPhase)
                                    c.connectedCharger.changeChargingAmps(charging_amp_change = AmpereToIncrease)

                            elif ChargingState is None:
                                c.wakeMeUp()
                                c.startCharging()
                                self.ADapi.log(f"Waking up {c.carName} from chargequeue. Chargestate is None") ###
                            elif (
                                c.connectedCharger is not c.onboardCharger
                                and ChargingState == 'NoPower'
                            ):
                                c.startCharging()
                                self.ADapi.log(f"Trying to start {c.carName} from chargequeue. Chargestate is NoPower and not onboard charger") ###
                            else:
                                if (
                                    c.connectedCharger is c.onboardCharger
                                    and ChargingState == 'NoPower'
                                ):
                                    self.ADapi.log(f"{c.carName} from chargequeue has Chargestate is NoPower and is connected to onboard charger") ###
                                    c.connectedCharger = None
                                    for charger in self.chargers:
                                        if (
                                            charger.Car is None
                                            and charger.getChargingState() != 'Disconnected'
                                        ):
                                            charger.findCarConnectedToCharger()
                                            return

                        elif c.vehicle_id == queue_id:
                            if not c.isConnected():
                                try:
                                    self.queueChargingList.remove(queue_id)
                                except Exception as e:
                                    self.ADapi.log(
                                        f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                        level = 'DEBUG'
                                    )
                                self.ADapi.log(f"Removing {c.carName} from chargequeue. is not connected. Chargestate not Disconnetcted? {c.getCarChargerState()}") ###
                                c._handleChargeCompletion()
                            else:
                                c.connectedCharger = c.onboardCharger

            if not self.queueChargingList or next_vehicle_id:
                if (
                    CHARGE_SCHEDULER.isChargingTime()
                    and available_Wh > 1600
                    and remaining_minute > 9
                    and runtime.minute > 3
                ):
                    next_vehicle_to_start = CHARGE_SCHEDULER.findNextChargerToStart()

                    if next_vehicle_to_start is not None:
                        if self._checkIfPossibleToStartCharging():
                            if self.checkIdleConsumption_Handler is not None:
                                if self.ADapi.timer_running(self.checkIdleConsumption_Handler):
                                    try:
                                        self.ADapi.cancel_timer(self.checkIdleConsumption_Handler)
                                    except Exception as e:
                                        self.ADapi.log(
                                            f"Was not able to stop existing handler to log consumption. {e}",
                                            level = "DEBUG"
                                        )
                                self.checkIdleConsumption_Handler = None

                            for c in self.cars:
                                if (
                                    c.vehicle_id == next_vehicle_to_start
                                    and c.connectedCharger is not None
                                ):
                                    if c.vehicle_id not in self.queueChargingList:
                                        self.queueChargingList.append(c.vehicle_id)
                                        self.ADapi.log(f"Starting to charge {c.carName} from queueChargingList") ###
                                        c.startCharging()
                                        AmpereToCharge = math.floor(available_Wh / c.connectedCharger.voltPhase)
                                        c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                                        return
                                elif c.vehicle_id == next_vehicle_to_start:
                                    if not c.isConnected():
                                        self.ADapi.log(f"{c.carName} Next to charge from queueChargingList but not connected?") ###
                                        c._handleChargeCompletion()
                                    elif c.getCarChargerState() == 'NoPower':
                                        for charger in self.chargers:
                                            if (
                                                charger.Car is None
                                                and charger.getChargingState() != 'Disconnected'
                                            ):
                                                charger.findCarConnectedToCharger()
                                                return

                                    c.connectedCharger = c.onboardCharger

    def reduceChargingAmpere(self, available_Wh: float, reduce_Wh: float) -> float:
        """ Reduces charging to stay within max kWh. """
        reduce_Wh += available_Wh

        for queue_id in reversed(self.queueChargingList):
            for c in self.chargers:
                if c.Car is not None:
                    if (
                        c.Car.connectedCharger is c
                        and c.Car.vehicle_id == queue_id
                        and reduce_Wh < 0
                    ):

                        if c.ampereCharging == 0:
                            c.ampereCharging = math.ceil(float(self.ADapi.get_state(c.charging_amps,
                                namespace = c.namespace))
                            )

                        if c.ampereCharging > c.min_ampere:
                            AmpereToReduce = math.floor(reduce_Wh / c.voltPhase)
                            if (c.ampereCharging + AmpereToReduce) < c.min_ampere:
                                c.setChargingAmps(charging_amp_set = c.min_ampere)
                                available_Wh -= (c.ampereCharging  - c.min_ampere) * c.voltPhase
                                reduce_Wh -= (c.ampereCharging  - c.min_ampere) * c.voltPhase
                            else:
                                c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                available_Wh -= AmpereToReduce * c.voltPhase
                                reduce_Wh -= AmpereToReduce * c.voltPhase
                                break
                        else:
                            c.ampereCharging = math.ceil(float(self.ADapi.get_state(c.charging_amps,
                                namespace = c.namespace))
                            )
                        
        return available_Wh

    def _checkIfPossibleToStartCharging(self) -> bool:
        softwareUpdates = False
        for c in self.cars:
            if c.isConnected():
                if c.SoftwareUpdates():
                    softwareUpdates = True
        # Stop other chargers if a car is updating software. Not able to adjust chargespeed when updating.
        if softwareUpdates:
            for c in self.cars:
                if (
                    c.isConnected()
                    and not c.dontStopMeNow()
                    and c.getCarChargerState() == 'Charging'
                ):
                    c.stopCharging(force_stop = True)
            return False
        return True

    def _updateChargingQueue(self) -> bool:
        for c in self.cars:
            if (
                c.isConnected()
                and c.getCarChargerState() == 'Charging'
                and c.vehicle_id not in self.queueChargingList
                and not self.SolarProducing_ChangeToZero
            ):
                self.queueChargingList.append(c.vehicle_id)
        return self.queueChargingList

    def getHeatersReducedPreviousConsumption(self, available_Wh:float) -> (float, float):
        """ Function that finds the value of power consumption when heating for items that are turned down
            and turns the heating back on if there is enough available watt,
            or return how many watt to reduce charing to turn heating back on.
        """
        reduce_Wh: float = 0

        for heater in reversed(self.heatersRedusedConsumption):
            if heater.prev_consumption + 600 < available_Wh:
                heater.setPreviousState()
                available_Wh -= heater.prev_consumption
                self.heatersRedusedConsumption.remove(heater)
                self.lastTimeHeaterWasReduced = datetime.datetime.now()
            elif heater.prev_consumption > available_Wh:
                reduce_Wh -= heater.prev_consumption
        return reduce_Wh, available_Wh

    def findConsumptionAfterTurnedBackOn(self, **kwargs) -> None:
        """ Functions to register consumption based on outside temperature after turned back on,
            to better be able to calculate chargingtime based on max kW pr hour usage
        """
        heater = kwargs['heater']
        time_to_save_item = kwargs['time_to_save_item']
        hoursOffInt = 0

        if not heater.away_state:
            for daytime in heater.daytime_savings:
                if 'start' in daytime and 'stop' in daytime:
                    if not 'presence' in daytime:
                        current_time = self.ADapi.datetime()
                        if (start := self.ADapi.parse_datetime(daytime['start'])) <= current_time < (end := self.ADapi.parse_datetime(daytime['stop'])):

                            off_hours = self.ADapi.parse_datetime(daytime['stop']) - self.ADapi.parse_datetime(daytime['start'])
                            if off_hours < datetime.timedelta(minutes = 0):
                                self.ADapi.log(f"Off_hours in findConsumptionAfterTurnedBackOn was {off_hours} before adding a day") ###
                                off_hours += datetime.timedelta(days = 1)
                            hoursOffInt = off_hours.seconds//3600
                            break
            if hoursOffInt == 0:
                try:
                    hoursOffInt = time_to_save_item['duration'].seconds//3600
                except (ValueError, TypeError) as e:
                    self.ADapi.log(f"Could not convert {time_to_save_item['duration']} to a duration: {e}", level = 'DEBUG')
                    return
            if (
                time_to_save_item['end'] > self.ADapi.datetime(aware=True)
                and hoursOffInt > 0
            ):
                self.ADapi.run_at(heater.findConsumptionAfterTurnedOn, time_to_save_item['end'], hoursOffInt = hoursOffInt)
                self.ADapi.log(f"Check consuption after: {time_to_save_item}") ###

    def check_if_heaterName_is_in_heaters(self, heater_name:str) -> bool:
        """ Function to find heater configuration by its name. """
        for heater in self.heaters:
            if heater_name == heater.heater:
                return True
        return False

    def calculateIdleConsumption(self, kwargs) -> None:
        """ Calculates expected available watts for each hour to calculate chargetime based on outside temperature and how many hours heaters has been off.
            The 'idleConsumption' consists of two watt measurements.
            One is all not registered from charging and heating.
            The other is all heaters combined as a average on outside temperature.
        """
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)

        if self.totalWattAllHeaters == 0:
            heaters_to_remove = []
            for heaterName in ElectricityData['consumption']:
                if heaterName != 'idleConsumption':
                    if 'power' in ElectricityData['consumption'][heaterName]:
                        self.totalWattAllHeaters += ElectricityData['consumption'][heaterName]['power']
                    if not self.check_if_heaterName_is_in_heaters(heater_name = heaterName):
                        heaters_to_remove.append(heaterName)
            if heaters_to_remove:
                # Remove old/uncofigured heaters from your JSON data
                updatedListWithCurrentHeaters:dict = {}
                for entry in ElectricityData['consumption']:
                    if entry not in heaters_to_remove:
                        updatedListWithCurrentHeaters.update({entry : ElectricityData['consumption'][entry]})
                ElectricityData['consumption'] = updatedListWithCurrentHeaters
                with open(self.json_path, 'w') as json_write:
                    json.dump(ElectricityData, json_write, default=json_serial, indent = 4)

        available_Wh_toCharge:list = []
        save_endHour = self.ADapi.datetime(aware=True).replace(minute = 0, second = 0, microsecond = 0)
        for item in ELECTRICITYPRICE.elpricestoday:
            item_dict: dict = {}
            available_Wh = (self.max_kwh_usage_pr_hour * 1000) * self.timedelta_for_dictionaries

            item_dict.update({
                'start': item['start'],
                'end': item['end'],
                'available_Wh': available_Wh
            })
            available_Wh_toCharge.append(item_dict)
        reduceAvgHeaterwatt:float = 1
        reduceAvgIdlewatt:float = 1

        if ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
            out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
            try:
                closest_temp = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
            except Exception:
                closest_temp = find_closest_temp_in_data(data = ElectricityData['consumption']['idleConsumption']['ConsumptionData'])
                if closest_temp is not None:
                    out_temp_str = str(closest_temp)
                else:
                    return

            reduceAvgHeaterwatt = float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['HeaterConsumption'])
            reduceAvgIdlewatt = float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['Consumption'])

            for item in available_Wh_toCharge:
                idle_consumption = (reduceAvgHeaterwatt + reduceAvgIdlewatt) * self.timedelta_for_dictionaries
                item['available_Wh'] -= idle_consumption

        for heaterName in ElectricityData['consumption']:
            if heaterName != 'idleConsumption':
                if ElectricityData['consumption'][heaterName]['ConsumptionData']:
                    for heater in self.heaters:
                        if heaterName == heater.heater:
                            for item in heater.time_to_save:
                                if 'end' in item:
                                    if item['end'].date() == self.ADapi.datetime(aware=True).date():
                                        save_endHour = item['end']
                                if 'duration' in item:
                                    duration_off = item['duration']
                                    off_for_int = math.floor((duration_off.days * 24 * 60 + duration_off.seconds // 60) / 60)
                                    if off_for_int > 0:
                                        off_for = str(off_for_int)
                                            # Find closest time registered with data
                                        if off_for in ElectricityData['consumption'][heaterName]['ConsumptionData']:
                                            off_for_data = ElectricityData['consumption'][heaterName]['ConsumptionData'][off_for]
                                        else:
                                            closest_time = find_closest_time_in_data(off_for_int = off_for_int, data = ElectricityData['consumption'][heaterName]['ConsumptionData'])
                                            if closest_time is not None:
                                                off_for = closest_time
                                                off_for_data = ElectricityData['consumption'][heaterName]['ConsumptionData'][off_for]
                                            else:
                                                break

                                        out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                                            # Find closest temp registered with data
                                        try:
                                            expectedHeaterConsumption = round(float(off_for_data[out_temp_str]['Consumption']) * 1000, 2)
                                        except Exception:
                                            closest_temp = find_closest_temp_in_data(data = off_for_data)
                                            if closest_temp is not None:
                                                out_temp_str = str(closest_temp)
                                                expectedHeaterConsumption = round(float(off_for_data[out_temp_str]['Consumption']) * 1000, 2)
                                            else:
                                                break

                                        heaterWatt = ElectricityData['consumption'][heaterName]['power']
                                        # Remove part of the calculated Idle consumption:
                                        pctHeaterWatt = heaterWatt / self.totalWattAllHeaters
                                        heaterWatt -= (reduceAvgHeaterwatt * pctHeaterWatt)
                                        heater_consumption = heaterWatt * self.timedelta_for_dictionaries
                                        #heater_consumption *= 0.9 # Enable Optimistic setting?

                                        start_times = [times_item['start'] for times_item in available_Wh_toCharge]
                                        index_start = bisect.bisect_left(start_times, item['end'])

                                        for Wh_item in available_Wh_toCharge[index_start:]:
                                            if expectedHeaterConsumption > heater_consumption:
                                                if Wh_item['available_Wh'] < heater_consumption:
                                                    expectedHeaterConsumption -= Wh_item['available_Wh']
                                                    Wh_item['available_Wh'] = 0
                                                else:
                                                    expectedHeaterConsumption -= heater_consumption
                                                    Wh_item['available_Wh'] -= heater_consumption

                                            elif expectedHeaterConsumption > 0:
                                                Wh_item['available_Wh'] -= expectedHeaterConsumption
                                                expectedHeaterConsumption = 0
                                                break

        CHARGE_SCHEDULER.availableWatt = available_Wh_toCharge
        CHARGE_SCHEDULER.save_endHour = save_endHour

    def logIdleConsumption(self, kwargs) -> None:
        """ Calculates average idle consumption and heater consumption and writes to persistent storage based on outside temperature. """
        try:
            current_consumption = float(self.ADapi.get_state(self.current_consumption))
        except ValueError as ve:
            if self.ADapi.get_state(self.current_consumption) == 'unavailable':
                self.ADapi.log(f"Current consumption is unavailable at logIdleConsumption", level = 'DEBUG')
            else:
                self.ADapi.log(ve, level = 'DEBUG')
            return

        heater_consumption:float = 0.0
        for heater in self.heaters:
            if heater.validConsumptionSensor:
                try:
                    heater_consumption += float(self.ADapi.get_state(heater.consumptionSensor,
                        namespace = heater.namespace)
                    )
                except ValueError:
                    pass

        idle_consumption = current_consumption - heater_consumption
        avgConsumption = idle_consumption
        avgHeaterConsumption = heater_consumption
        if idle_consumption <= 0:
            self.ADapi.log(f"idle_consumption is {idle_consumption} Aborting logIdleConsumption") ###
            return

        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)

        out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
        consumption_compare = 0
        consumptionData = {}
        counter = 1

        if ElectricityData['consumption']['idleConsumption']['ConsumptionData']:

            if not out_temp_str in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                out_temp_str_compare = find_closest_temp_in_data(data = ElectricityData['consumption']['idleConsumption']['ConsumptionData'])
            else:
                out_temp_str_compare = out_temp_str
                consumptionData = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
                counter += consumptionData['Counter']
                avgConsumption = round(((consumptionData['Consumption'] * consumptionData['Counter']) + idle_consumption) / counter,2)
                avgHeaterConsumption = round(((consumptionData['HeaterConsumption'] * consumptionData['Counter']) + heater_consumption) / counter,2)
                if counter > 100:
                    counter = 10 # Value old data less.

            if out_temp_str_compare is not None:
                try:
                    consumption_compare = round(float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str_compare]['Consumption']) * 1000, 2)
                except Exception:
                    pass

                if (
                    idle_consumption > consumption_compare + 1000
                    and idle_consumption < consumption_compare - 1000
                ): # Avoid setting high consumption
                    if consumption_compare != 0:
                        return

        newData = {"Consumption" : avgConsumption, "HeaterConsumption" : avgHeaterConsumption, "Counter" : counter}
        ElectricityData['consumption']['idleConsumption']['ConsumptionData'].update({out_temp_str : newData})

        with open(self.json_path, 'w') as json_write:
            json.dump(ElectricityData, json_write, default=json_serial, indent = 4)

    def logHighUsage(self) -> None:
        """ Writes top three max kWh usage pr hour to persistent storage. """
        newTotal = 0.0
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)
        max_kwh_usage_top = ElectricityData['MaxUsage']['topUsage']
        newTopUsage:float = 0

        try:
            newTopUsage = float(self.ADapi.get_state(self.accumulated_consumption_current_hour))
            if newTopUsage > max_kwh_usage_top[0]:
                max_kwh_usage_top[0] = newTopUsage
                ElectricityData['MaxUsage']['topUsage'] = sorted(max_kwh_usage_top)
            self.top_usage_hour = ElectricityData['MaxUsage']['topUsage'][0]
        except ValueError as ve:
            self.ADapi.log(
                f"Not able to set new Top Hour Usage. Accumulated consumption is {self.ADapi.get_state(self.accumulated_consumption_current_hour)} "
                f"ValueError: {ve}",
                level = 'WARNING'
            )
        except Exception as e:
            self.ADapi.log(f"Not able to set new Top Hour Usage. Exception: {e}", level = 'WARNING')

        for num in ElectricityData['MaxUsage']['topUsage']:
            newTotal += num
        avg_top_usage = newTotal / 3

        if avg_top_usage > self.max_kwh_usage_pr_hour:
            self.max_kwh_usage_pr_hour += 5
            ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour 
            self.ADapi.log(
                f"Avg consumption during one hour is now {round(avg_top_usage, 3)} kWh and surpassed max kWh set. "
                f"New max kWh usage during one hour set to {self.max_kwh_usage_pr_hour}. "
                "If this is not expected try to increase buffer.",
                level = 'WARNING'
            )
        elif (
            avg_top_usage > self.max_kwh_usage_pr_hour - self.buffer
            and newTopUsage != 0   
        ):
            self.ADapi.log(
                f"Consumption last hour: {round(newTopUsage, 3)}. "
                f"Avg top 3 hours: {round(avg_top_usage, 3)}",
                level = 'INFO'
            )

        with open(self.json_path, 'w') as json_write:
            json.dump(ElectricityData, json_write, default=json_serial, indent = 4)

    def resetHighUsage(self) -> None:
        """ Resets max usage pr hour for new month. """
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)
        self.max_kwh_usage_pr_hour = self.max_kwh_goal
        ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour
        ElectricityData['MaxUsage']['topUsage'] = [0,0,float(self.ADapi.get_state(self.accumulated_consumption_current_hour))]

        with open(self.json_path, 'w') as json_write:
            json.dump(ElectricityData, json_write, default=json_serial, indent = 4)

        # Set proper value when weather sensors is updated
    def weather_event(self, event_name, data, kwargs) -> None:
        """ Listens for weather change from the weather app. """
        global OUT_TEMP
        global RAIN_AMOUNT
        global WIND_AMOUNT

        if self.ADapi.datetime(aware=True) - self.out_temp_last_update > datetime.timedelta(minutes = 20):
            OUT_TEMP = data['temp']
        if self.ADapi.datetime(aware=True) - self.rain_last_update > datetime.timedelta(minutes = 20):
            RAIN_AMOUNT = data['rain']
        if self.ADapi.datetime(aware=True) - self.wind_last_update > datetime.timedelta(minutes = 20):
            WIND_AMOUNT = data['wind']

    def _outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except (ValueError, TypeError):
            pass
        else:
            self.out_temp_last_update = self.ADapi.datetime(aware=True)

    def _rainSensorUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global RAIN_AMOUNT
        try:
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            RAIN_AMOUNT = 0.0
            self.ADapi.log(f"Not able to set new rain amount: {new}. {ve}", level = 'DEBUG')
        else:
            self.rain_last_update = self.ADapi.datetime(aware=True)
        
    def _anemometerUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global WIND_AMOUNT
        try:
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            WIND_AMOUNT = 0.0
            self.ADapi.log(f"Not able to set new wind amount: {new}. {ve}", level = 'DEBUG')
        else:
            self.wind_last_update = self.ADapi.datetime(aware=True)

    def mode_event(self, event_name, data, kwargs) -> None:
        """ Listens to same mode event that I have used in Lightwand: https://github.com/Pythm/ad-Lightwand
            If mode name equals 'fire' it will turn off all charging and heating.
            To call from another app use: self.fire_event('MODE_CHANGE', mode = 'fire')
            Set back to normal with mode 'false-alarm'.
        """
        if data['mode'] == FIRE_TRANSLATE:
            self.houseIsOnFire = True
            for c in self.cars:
                if (
                    c.isConnected()
                    and c.getCarChargerState() == 'Charging'
                ):
                    c.stopCharging(force_stop = True)
            
            for charger in self.chargers:
                charger.doNotStartMe = True

            for heater in self.heaters:
                heater.turn_off_heater()


        elif data['mode'] == FALSE_ALARM_TRANSLATE:
            # Fire alarm stopped
            self.houseIsOnFire = False
            for heater in self.heaters:
                heater.turn_on_heater()
            
            for charger in self.chargers:
                charger.doNotStartMe = False

            if any(c.kWhRemaining() > 0 for c in self.cars):
                c.findNewChargeTime()

    def _notify_event(self, event_name, data, kwargs) -> None:
        if any(data['action'] == 'find_new_chargetime'+str(c.carName) for c in self.cars):
            c.kWhRemaining()
            c.findNewChargeTime()
            return
        
        if any(data['action'] == 'kWhremaining'+str(c.charger) for c in self.chargers):
            try:
                c.Car.kWhRemainToCharge = float(data['reply_text'])
            except (ValueError, TypeError):
                c.kWhRemaining()
                self.ADapi.log(
                    f"User input {data['reply_text']} on setting kWh remaining for Guest car. Not valid number. Using {c.Car.kWhRemainToCharge} to calculate charge time",
                    level = 'INFO'
                )
            c.Car.findNewChargeTime()
            return

        if any(data['action'] == 'chargeNow'+str(c.charger) for c in self.chargers):
            c.Car.charge_now = True
            c.startCharging()

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
        namespace:str
    ):
        self.ADapi = api
        self.namespace = namespace
        self.stopAtPriceIncrease = stopAtPriceIncrease
        self.startBeforePrice = startBeforePrice

        # Helpers
        self.chargingQueue:list = []
        self.simultaneousChargeComplete:list = []
        self.currentlyCharging = set()
        self.informHandler = None
        self.infotext = infotext
       
        # Is updated from main class when turning off/down to save on electricity price
        self.availableWatt:list = []
        self.timedelta_for_dictionaries:float = 0
        self.save_endHour:datetime = self.ADapi.datetime(aware=True).replace(minute = 0, second = 0, microsecond = 0)

    def _calculate_expected_chargetime(self, kWhRemaining:float = 2, totalW_AllChargers:float = 3600, startTime = None) -> int:
        hoursToCharge = 0
        WhRemaining = kWhRemaining * 1000
        start_times = [item['start'] for item in self.availableWatt]
        if startTime is None:
            startTime = self.ADapi.datetime(aware=True)
        if startTime > self.save_endHour:
            self.save_endHour = self.get_next_time_aware(startTime = startTime, offset_seconds = 00, delta_in_seconds = 60*15)
        index_start = bisect.bisect_left(start_times, self.save_endHour)
        available_Wh = 2000

        for item in self.availableWatt[index_start:]:
            if WhRemaining <= 0:
                break
            available_Wh = item['available_Wh']

            if WhRemaining <= available_Wh:
                hoursToCharge += self.timedelta_for_dictionaries
                WhRemaining = 0
                break
            else:
                WhRemaining -= available_Wh
                hoursToCharge += self.timedelta_for_dictionaries
        
        if WhRemaining > available_Wh:
            duration = (self.availableWatt[-1]['end'] - self.availableWatt[-1]['start']).total_seconds() / 3600.0
            hoursToCharge += (WhRemaining / available_Wh) * self.timedelta_for_dictionaries

        return hoursToCharge

    def get_next_time_aware(self, startTime, offset_seconds, delta_in_seconds):
        next_minute_mark = ((startTime.minute * 60 + startTime.second) // delta_in_seconds + 1) * delta_in_seconds
        next_runtime = startTime.replace(minute=0, second=offset_seconds % 60, microsecond=0)
        next_runtime += datetime.timedelta(seconds=next_minute_mark)

        return next_runtime

    def getCharingTime(self, vehicle_id:str) -> (datetime, datetime):
        """ Helpers used to return data. Returns charging start and stop time for vehicle_id
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                if 'chargingStart' in c and 'chargingStop' in c:
                    return c['chargingStart'], c['chargingStop']
        return None, None

    def isChargingTime(self, vehicle_id:str = None) -> bool:
        """ Helpers used to return data. Returns True if it it chargingtime
        """
        if not self.chargingQueue:
            return False

        price:float = 0
        for c in self.chargingQueue:
            if (
                vehicle_id is None
                or vehicle_id == c['vehicle_id']
            ):
                if 'chargingStart' in c and 'chargingStop' in c:
                    if c['chargingStart'] is not None:
                        if (
                            self.ADapi.datetime(aware=True) >= c['chargingStart']
                            and self.ADapi.datetime(aware=True) < c['chargingStop']
                        ):
                            return True

                    if c['price'] is not None:
                        if c['price'] > price:
                            price = c['price']

        if (
            self.ADapi.now_is_between('09:00:00', '14:00:00')
            and not ELECTRICITYPRICE.tomorrow_valid
        ):
            # Finds low price during day awaiting tomorrows prices
            # TODO: A better logic to charge up if price is lower than usual before tomorrow prices is available from Nordpool.

            calculatePrice:bool = False
            for c in self.chargingQueue:
                if c['price'] is None and price == 0:
                    calculatePrice = True
                elif c['price'] is not None:
                    if c['price'] > price:
                        price = c['price']
                        calculatePrice = False

            if calculatePrice:
                kWhToCharge = 0
                totalW_AllChargers = 0
                hoursToCharge = 0
                for c in self.chargingQueue:
                    kWhToCharge += c['kWhRemaining']
                    totalW_AllChargers += c['maxAmps'] * c['voltPhase']
                    if 'estHourCharge' in c:
                       hoursToCharge += c['estHourCharge']
                if hoursToCharge == 0:
                    hoursToCharge = self._calculate_expected_chargetime(kWhRemaining = kWhToCharge, totalW_AllChargers = totalW_AllChargers)
                price = ELECTRICITYPRICE.get_lowest_prices(checkitem = datetime.datetime.now().hour, hours = hoursToCharge, min_change = 0.1)

            for c in self.chargingQueue:
                c['price'] = price

        if price > 0:
            try:
                return ELECTRICITYPRICE.electricity_price_now() <= price
            except TypeError:
                return False
        return False

    def getVehiclePrice(self, vehicle_id:str = None) -> float:
        """ Returns a float with price to charge car
        """
        price:float = 0
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                if c['price'] is not None:
                    return c['price']
            elif c['price'] is not None:
                if c['price'] > price:
                    price = c['price']
        return price

    def isPastChargingTime(self, vehicle_id:str = None) -> bool:
        """ Helpers used to return data. Returns True if it is past chargingtime.
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                if 'chargingStop' in c:
                    if c['chargingStop'] is None:
                        return True
                    return self.ADapi.datetime(aware=True) > c['chargingStop']
        return True

    def hasChargingScheduled(self, vehicle_id:str, kWhRemaining:float, finishByHour:int) -> bool:
        """ Helpers used to return data. Returns True if vehicle_id has charging scheduled.
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                if (
                    c['kWhRemaining'] == kWhRemaining
                    and c['finishByHour'] == finishByHour
                ):
                    if 'chargingStart' in c:
                        if (
                            c['chargingStart'] is not None
                            and c['chargingStop'] is not None
                        ):
                            if self.ADapi.datetime(aware=True) < c['chargingStop']:
                                return True
                break
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

    def findNextChargerToStart(self) -> str:
        """ Helpers used to return data. Returns next vehicle_id that has charging scheduled.
        """
        pri = 1
        while pri <= 5:
            for c in self.chargingQueue:
                if (c['priority'] == pri or pri == 5) and not self.isCurrentlyCharging(c['vehicle_id']):
                    if self.isChargingTime(vehicle_id=c['vehicle_id']):
                        return c['vehicle_id']
            pri += 1
        return None

    def removeFromQueue(self, vehicle_id:str) -> None:
        """ Removes a charger from queue after finished charging or disconnected.
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                self.chargingQueue.remove(c)


    def queueForCharging(self,
        vehicle_id:str,
        kWhRemaining:float,
        maxAmps:int,
        voltPhase:int,
        finishByHour:int,
        priority:int,
        name:str # Name only for notification
    ) -> bool:
        """ Adds charger to queue and sets charging time, Returns True if it is charging time.
        """
        self.removeFromQueue(vehicle_id = vehicle_id)

        if kWhRemaining <= 0:
            return False

        estHourCharge = self._calculate_expected_chargetime(
            kWhRemaining = kWhRemaining,
            totalW_AllChargers = maxAmps * voltPhase
        )

        self.chargingQueue.append({'vehicle_id' : vehicle_id,
            'kWhRemaining' : kWhRemaining,
            'maxAmps' : maxAmps,
            'voltPhase' : voltPhase,
            'finishByHour' : finishByHour,
            'priority' : priority,
            'estHourCharge' : estHourCharge,
            'name' : name,
            'chargingStart' : None,
            'estimateStop' : None,
            'chargingStop' : None,
            'price' : None
            })

        if (
            self.ADapi.now_is_between('09:00:00', '14:00:00')
            and not ELECTRICITYPRICE.tomorrow_valid
        ):
            return self.isChargingTime(vehicle_id = vehicle_id)

        self.process_charging_queue()

        return self.isChargingTime(vehicle_id = vehicle_id)

    def process_charging_queue(self):
        # Ensure the chargingQueue is sorted by finishByHour
        self.chargingQueue.sort(key=lambda c: c['finishByHour'])

        simultaneousCharge = []
        self.simultaneousChargeComplete = []

        for i in range(len(self.chargingQueue)):
            current_car = self.chargingQueue[i]

            current_car['chargingStart'], current_car['estimateStop'], current_car['chargingStop'], current_car['price'] = ELECTRICITYPRICE.get_Continuous_Cheapest_Time(
                hoursTotal=current_car['estHourCharge'],
                calculateBeforeNextDayPrices=False,
                finishByHour=current_car['finishByHour'],
                startBeforePrice=self.startBeforePrice,
                stopAtPriceIncrease=self.stopAtPriceIncrease
            )

            if current_car['chargingStart'] is not None:
                has_overlap = False

                for overlapping_id in simultaneousCharge:
                    overlap_car_index = next((j for j, c in enumerate(self.chargingQueue) if c['vehicle_id'] == overlapping_id), None)
                    if overlap_car_index is not None and self.chargingQueue[overlap_car_index]['chargingStop'] > current_car['chargingStart']:
                        has_overlap = True
                        break

                if not has_overlap:
                    for j in range(i-1, -1, -1):
                        previous_car = self.chargingQueue[j]
                        if (previous_car['chargingStop'] is not None and
                            current_car['chargingStart'] < previous_car['chargingStop']):

                            simultaneousCharge.append(previous_car['vehicle_id'])

                    simultaneousCharge.append(current_car['vehicle_id'])
                else:
                    simultaneousCharge.append(current_car['vehicle_id'])


            next_index = i + 1
            if next_index < len(self.chargingQueue):
                if (
                    self.chargingQueue[next_index]['chargingStart'] is not None
                    and current_car['chargingStop'] is not None
                ):
                    if self.chargingQueue[next_index]['chargingStart'] >= current_car['chargingStop']:
                        if simultaneousCharge:
                            self.calcSimultaneousCharge(simultaneousCharge=simultaneousCharge)
                            self.simultaneousChargeComplete.extend(simultaneousCharge)
                            simultaneousCharge = []

        if simultaneousCharge:
            self.calcSimultaneousCharge(simultaneousCharge=simultaneousCharge)
            self.simultaneousChargeComplete.extend(simultaneousCharge)
            simultaneousCharge = []

    def calcSimultaneousCharge(self, simultaneousCharge:list):
        """ Calculates charging time for vehicles that has the same charging time.
        """
        finishByHour:int = 0
        kWhToCharge:float = 0.0
        totalW_AllChargers:float = 0.0
        startTime:datetime = self.ADapi.datetime(aware=True)
        for c in self.chargingQueue:
            if c['vehicle_id'] in simultaneousCharge:
                kWhToCharge += c['kWhRemaining']
                totalW_AllChargers += c['maxAmps'] * c['voltPhase']
                if c['finishByHour'] > finishByHour:
                    if finishByHour == 0:
                        finishByHour = c['finishByHour']
                    else:
                        finishByHour += c['estHourCharge']
                if 'chargingStart' in c:
                    startTime = c['chargingStart']

        hoursToCharge = self._calculate_expected_chargetime(
            kWhRemaining = kWhToCharge,
            totalW_AllChargers = totalW_AllChargers,
            startTime = startTime
        )
        ChargingAt, estimateStop, ChargingStop, price = ELECTRICITYPRICE.get_Continuous_Cheapest_Time(
            hoursTotal = hoursToCharge,
            calculateBeforeNextDayPrices = False,
            finishByHour = finishByHour,
            startBeforePrice = self.startBeforePrice, 
            stopAtPriceIncrease = self.stopAtPriceIncrease
        )
        if estimateStop is not None:
            for c in self.chargingQueue:
                if c['vehicle_id'] in simultaneousCharge:
                    c['chargingStart'] = ChargingAt
                    c['estimateStop'] = estimateStop
                    c['chargingStop'] = ChargingStop
                    c['price'] = price

    def notifyChargeTime(self, kwargs):
        """Sends notifications and updates infotext with charging times and prices."""
        price = None
        times_set = False
        info_text = ""
        info_text_simultaneous_car = "Charge "
        info_text_simultaneous_time = ""
        send_new_info = False

        sorted_queue = sorted(self.chargingQueue, key=lambda c: c['finishByHour'])

        for car in sorted_queue:
            if self.hasChargingScheduled(
                vehicle_id=car['vehicle_id'],
                kWhRemaining=car['kWhRemaining'],
                finishByHour=car['finishByHour']
            ):
                if all(key in car for key in ['informedStart', 'informedStop', 'chargingStart']):
                    if (car['informedStart'] != car['chargingStart'] or
                        car['informedStop'] != car['estimateStop']):
                        send_new_info = True
                else:
                    send_new_info = True

                if 'chargingStart' in car and car['chargingStart'] is not None:
                    car['informedStart'] = car['chargingStart']
                    car['informedStop'] = car['estimateStop']

                    timestrStart = str(car['chargingStart'])
                    timestrStart = timestrStart[:-9]
                    timestrEtaStop = str(car['estimateStop'])
                    timestrEtaStop = timestrEtaStop[:-9]
                    timestrStop = str(car['chargingStop'])
                    timestrStop = timestrStop[:-9]
                    if car['vehicle_id'] in self.simultaneousChargeComplete:
                        info_text_simultaneous_car += f"{car['name']} & "
                        info_text_simultaneous_time = f"at {timestrStart}. Finish est at {timestrEtaStop}. Stop no later than {timestrStop}. "
                    else:
                        info_text += (f"Start {car['name']} at {timestrStart}. "
                                    f"Finish est at {timestrEtaStop}. "
                                    f"Stop no later than {timestrStop}. ")

                    times_set = True

            if car['price'] is not None:
                if price is None or price < car['price']:
                    price = car['price']

        if info_text_simultaneous_car.endswith(" & "):
            info_text_simultaneous_car = info_text_simultaneous_car[:-2]
        info_text += info_text_simultaneous_car + info_text_simultaneous_time

        if not times_set and price is not None:
            info_text = (
                f"Charge if price is lower than {ELECTRICITYPRICE.currency} "
                f"{round(price - ELECTRICITYPRICE.current_daytax, 3)} (day) or "
                f"{ELECTRICITYPRICE.currency} {round(price - ELECTRICITYPRICE.current_nighttax, 3)} (night/weekend)"
            )
            send_new_info = True

        if self.infotext is not None:
            self.ADapi.call_service(
                'input_text/set_value',
                value=info_text,
                entity_id=self.infotext,
                namespace=self.namespace
            )

        if send_new_info and info_text.strip():
            data = {'tag': 'chargequeue'}
            NOTIFY_APP.send_notification(
                message=info_text,
                message_title="🔋 Charge Queue",
                message_recipient=RECIPIENTS,
                also_if_not_home=True,
                data=data
            )


class Charger:
    """ Charger parent class
    Set variables in childclass before init:
        self.charger_id:str # Unik ID to identify chargers
        self.volts:int # 220/266/400v
        self.phases:int # 1 phase or 3 phase
        self.cars:list # list of cars that can connect to charger

    Set variables in childclass after init for connected chargers if needed:
        self.guestCharging:bool # Defaults to False
    Set variables in childclass after init for onboard chargers if needed:
        self.Car
        self.Car.onboardCharger
    
    Change default values if needed:
    self.min_ampere = 6
    
    Functions to implement in child class:
        def setmaxChargingAmps(self) -> None:
        def getChargingState(self) -> str:
    """
    def __init__(self, api,
        namespace:str,
        charger:str, # Name of your charger.
        charger_sensor:str, # Cable Connected or Disconnected
        charger_switch:str, # Charging or not
        charging_amps:str, # Ampere charging
        charger_power:str, # Charger power
        session_energy:str, # Charged this session in kWh
        json_path:str
    ):

        self.ADapi = api
        self.Car = None
        self.namespace = namespace
        self.json_path = json_path

        # Sensors
        self.charger = charger
        self.charger_sensor = charger_sensor
        self.charger_switch = charger_switch
        self.charging_amps = charging_amps
        self.charger_power = charger_power
        self.session_energy = session_energy

        # Helpers
        self.guestCharging:bool = False
        self.ampereCharging:int = 0
        self.min_ampere:int = 6
        self.maxChargerAmpere:int = 0
        self.voltPhase:int = 220
        self.checkCharging_handler = None
        self.doNotStartMe:bool = False
        self.pct_start_charge:float = 100

        # Check that charger exists and get data from persistent json file if so.
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.charger_id in ElectricityData['charger']:
            self.ADapi.run_in(self._create_charger_in_persistent, 600)
        else:
            if 'voltPhase' in ElectricityData['charger'][self.charger_id]:
                self.voltPhase = int(ElectricityData['charger'][self.charger_id]['voltPhase'])
            else:
                self.setVoltPhase(
                    volts = self.volts,
                    phases = self.phases
                )
            if 'MaxAmp' in ElectricityData['charger'][self.charger_id]:
                self.maxChargerAmpere = int(ElectricityData['charger'][self.charger_id]['MaxAmp'])
            if 'ConnectedCar' in ElectricityData['charger'][self.charger_id]:
                if ElectricityData['charger'][self.charger_id]['ConnectedCar'] is not None:
                    for car in self.cars:
                        if car.carName == ElectricityData['charger'][self.charger_id]['ConnectedCar']:
                            if car.isConnected():
                                car.connectedCharger = self
                                self.Car = car
                            break

        if charging_amps is not None:
            api.listen_state(self.updateAmpereCharging, charging_amps,
                namespace = namespace
            )

        """ End initialization Charger Class
        """

    def _create_charger_in_persistent(self, kwargs) -> None:
        self.setmaxChargingAmps()
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.charger_id in ElectricityData['charger']:
            ElectricityData['charger'].update(
                {self.charger_id : {
                    "voltPhase" : self.voltPhase,
                    "MaxAmp" : self.maxChargerAmpere
                }}
            )
            with open(self.json_path, 'w') as json_write:
                json.dump(ElectricityData, json_write, default=json_serial, indent = 4)

    def findCarConnectedToCharger(self) -> bool:
        """ A check to see if a car is connected to the charger. """
        if self.getChargingState() not in ['Disconnected', 'Complete', 'NoPower']:
            for car in self.cars:

                if car._polling_of_data():
                    if (
                        car.isConnected()
                        and car.connectedCharger is None
                    ):
                        if self.compareChargingState(
                            car_status = car.getCarChargerState()
                        ):
                            car.connectedCharger = self
                            self.Car = car
                            self.kWhRemaining()
                            self.ADapi.log(f"Connected {self.Car.carName} to {self.charger} in findCarConnectedToCharger") ###
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
        if chargingState in ['Complete', 'Disconnected']:
            if self.guestCharging:
                self.Car.kWhRemainToCharge = -1
            return -1

        if self.Car is not None:
            kWhRemain:float = self.Car.kWhRemaining()
            if kWhRemain > -2:
                return kWhRemain

            if self.session_energy:
                if self.guestCharging:
                    kWh_remain = self.Car.kWhRemainToCharge - (float(self.ADapi.get_state(self.session_energy, namespace = self.namespace)))
                    self.ADapi.log(
                        f"Guest charging when trying to calculate kWh Remaining: {kWh_remain}"
                    ) ###
                    if kWh_remain > 2:
                        return kWh_remain
                    else:
                        return 10

                self.Car.kWhRemainToCharge = self.Car.maxkWhCharged - float(self.ADapi.get_state(self.session_energy,
                    namespace = self.namespace)
                )
                return self.Car.kWhRemainToCharge
        
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
        if self.charger_sensor is not None:
            if self.ADapi.get_state(self.charger_sensor, namespace = self.namespace) == 'on':
                # Connected
                if self.charger_switch is not None:
                    if self.ADapi.get_state(self.charger_switch, namespace = self.namespace) == 'on':
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
        pwr = self.ADapi.get_state(self.charger_power,
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
        self.maxChargerAmpere = 32
        self.ADapi.log(
            f"Setting maxChargerAmpere to 32. Needs to set value in child class of charger.",
            level = 'WARNING'
        )
        # Update Voltphase calculations
        self.setVoltPhase(
            volts = self.volts,
            phases = self.phases
        )
        return True

    def getmaxChargingAmps(self) -> int:
        """ Returns the maximum ampere the car/charger can get/deliver.
        """
        if self.maxChargerAmpere == 0:
            return 32
        
        return self.maxChargerAmpere

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
            self.ampereCharging = newAmp

    def changeChargingAmps(self, charging_amp_change:int = 0) -> None:
        """ Function to change ampere charging +/-
        """
        if charging_amp_change != 0:
            new_charging_amp = self.ampereCharging + charging_amp_change
            self.setChargingAmps(charging_amp_set = new_charging_amp)

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        max_available_amps = self.getmaxChargingAmps()
        if charging_amp_set < self.min_ampere:
            charging_amp_set = self.min_ampere
        elif charging_amp_set > max_available_amps:
            charging_amp_set = max_available_amps
            if self.Car.onboardCharger is not None:
               if self.Car.connectedCharger is not self.Car.onboardCharger:
                    self.Car.onboardCharger.setChargingAmps(charging_amp_set = self.Car.onboardCharger.getmaxChargingAmps())

        stack = inspect.stack() # Check if called from child
        if stack[1].function != 'setChargingAmps':
            self.ampereCharging = charging_amp_set
            self.ADapi.call_service('number/set_value',
                value = self.ampereCharging,
                entity_id = self.charging_amps,
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
        if self.checkCharging_handler is not None:
            if self.ADapi.timer_running(self.checkCharging_handler):
                try:
                    self.ADapi.cancel_timer(self.checkCharging_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                        level = 'DEBUG'
                    )
            self.checkCharging_handler = None
        if self.doNotStartMe:
            self.ADapi.log(f"Charging not allowed for {self.charger} by doNotStartMe. existing handler: {self.checkCharging_handler}") ###
            return False
        self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

        # Calculations for battery size:
        if (
            self.Car is not None
            and self.session_energy is not None
        ):
            if (
                float(self.ADapi.get_state(self.session_energy, namespace = self.namespace)) < 4
                and self.Car.battery_sensor is not None
            ):
                self.pct_start_charge = float(self.ADapi.get_state(self.Car.battery_sensor, namespace = self.namespace))

        CHARGE_SCHEDULER.markAsCharging(self.Car.vehicle_id)
        stack = inspect.stack() # Check if called from child
        if stack[1].function == 'startCharging':
            start, stop = CHARGE_SCHEDULER.getCharingTime(vehicle_id = self.Car.vehicle_id) ###
            self.ADapi.log(
                f"Starting to charge {self.Car.carName}. with connected charger: {self.charger} Chargestart: {start} Stop: {stop}. "
                f"Price: {CHARGE_SCHEDULER.getVehiclePrice(vehicle_id = self.Car.vehicle_id)}"
                ) ### TODO: Check for wrong start time...
            return True
        else:
            self.ADapi.call_service('switch/turn_on',
                entity_id = self.charger_switch,
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
        if self.getChargingState() in ['Charging', 'Starting']:
            if self.checkCharging_handler is not None:
                if self.ADapi.timer_running(self.checkCharging_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkCharging_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                            level = 'DEBUG'
                        )

            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

            stack = inspect.stack() # Check if called from child
            if stack[1].function != 'stopCharging':
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.charger_switch,
                    namespace = self.namespace,
                )
        return True

    def checkIfChargingStarted(self, kwargs) -> bool:
        """ Check if charger was able to start.
        """
        if not self.getChargingState() in ['Charging', 'Complete', 'Disconnected']:
            if self.ADapi.timer_running(self.checkCharging_handler):
                try:
                    self.ADapi.cancel_timer(self.checkCharging_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                        level = 'DEBUG'
                    )
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

            stack = inspect.stack() # Check if called from child
            if stack[1].function in ['startCharging', 'checkIfChargingStarted']:
                return False
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.charger_switch,
                    namespace = self.namespace,
                )
        return True

    def checkIfChargingStopped(self, kwargs) -> bool:
        """ Check if charger was able to stop.
        """
        if self.Car is not None:
            if self.Car.dontStopMeNow():
                return True
            if self.getChargingState() == 'Charging':
                if self.ADapi.timer_running(self.checkCharging_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkCharging_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                            level = 'DEBUG'
                        )

                self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

                stack = inspect.stack() # Check if called from child
                if stack[1].function in ['stopCharging', 'checkIfChargingStopped']:
                    return False
                else:
                    self.ADapi.call_service('switch/turn_off',
                        entity_id = self.charger_switch,
                        namespace = self.namespace,
                    )

        return True

    def ChargingStarted(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger started charging. Check if controlling car and if chargetime has been set up
        """
        if self.Car.connectedCharger is None:
            self.ADapi.log(f"Need to find connected charger for {self.Car.carName} in ChargingStarted for {self.charger}. Add logic maybe?") ###
            if not self.findCarConnectedToCharger():
                return

        #if self.Car.connectedCharger is self:
        #    if not self.Car.hasChargingScheduled():
        #        if self.kWhRemaining() > 0:
        #            self.Car.findNewChargeTime()

    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger stopped charging.
        """
        self._CleanUpWhenChargingStopped()

    def _updateMaxkWhCharged(self, session: float):
        if self.Car.maxkWhCharged < session:
            self.Car.maxkWhCharged = session

    def _calculateBatterySize(self, session: float):
        battery_sensor = getattr(self.Car, 'battery_sensor', None)
        battery_reg_counter = getattr(self.Car, 'battery_reg_counter', 0)

        if battery_sensor is not None and self.pct_start_charge < 90:
            pctCharged = float(self.ADapi.get_state(battery_sensor, namespace=self.namespace)) - self.pct_start_charge

            if pctCharged > 35:
                self._updateBatterySize(session, pctCharged, battery_reg_counter)
            elif pctCharged > 10 and self.Car.battery_size == 100 and battery_reg_counter == 0:
                self.Car.battery_size = (session / pctCharged)*100

    def _updateBatterySize(self, session: float, pctCharged: float, battery_reg_counter: int):
        if battery_reg_counter == 0:
            avg = round((session / pctCharged) * 100, 2)
        else:
            avg = round(
                ((self.Car.battery_size * battery_reg_counter) + (session / pctCharged) * 100)
                / (battery_reg_counter + 1),
                2
            )

        self.Car.battery_reg_counter += 1

        if self.Car.battery_reg_counter > 100:
            self.Car.battery_reg_counter = 10

        self.ADapi.log(
            f"pct Charged for {self.Car.carName} is {pctCharged}. kWh: {round(session,2)}. Est battery size: {round((session / pctCharged)*100,2)}"
            f"Old calc: {self.Car.battery_size}. counter: {self.Car.battery_reg_counter}. New avg: {avg}"
        )

        self.Car.battery_size = avg

    def _CleanUpWhenChargingStopped(self) -> None:
        """ Charger stopped charging. """
        if self.Car is not None:
            if self.Car.connectedCharger is self:
                if (
                    self.kWhRemaining() <= 2
                    or CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = self.Car.vehicle_id)
                ):
                    if self.getChargingState() in ['Complete', 'Disconnected']:
                        self.Car._handleChargeCompletion()
                    if self.session_energy:
                        session = float(self.ADapi.get_state(self.session_energy, namespace=self.namespace))
                        self._updateMaxkWhCharged(session)
                        self._calculateBatterySize(session)

                self.pct_start_charge = 100
                self.ampereCharging = 0

    def setVoltPhase(self, volts, phases) -> None:
        """ Helper for calculations on chargespeed.
            VoltPhase is a make up name and simplification to calculate chargetime based on remaining kwh to charge
            230v 1 phase,
            266v is 3 phase on 230v without neutral (supported by tesla among others)
            687v is 3 phase on 400v with neutral.
        """
        if (
            phases > 1
            and volts > 200
            and volts < 250
        ):
            self.voltPhase = 266

        elif (
            phases == 3
            and volts > 300
        ):
            self.voltPhase = 687

        elif (
            phases == 1
            and volts > 200
            and volts < 250
        ):
            self.voltPhase = volts


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
                    self.remove_car_from_list(self.Car.vehicle_id)
                    self.Car = None
                elif (
                    self.Car.isConnected()
                    and self.kWhRemaining() > 0
                ):
                    self.Car.findNewChargeTime()
            else:
                self.stopCharging()

    def _addGuestCar(self):
        """ Creates a guest car and starts charging.
        """
        guestCar = Car(api = self.ADapi,
            namespace = self.namespace,
            carName = 'guestCar',
            charger_sensor = None,
            charge_limit = None,
            battery_sensor = None,
            asleep_sensor = None,
            online_sensor = None,
            location_tracker = None,
            software_update = None,
            force_data_update = None,
            polling_switch = None,
            data_last_update_time = None,
            battery_size = 100,
            pref_charge_limit = 100,
            priority = 1,
            finishByHour = 7,
            charge_now = False,
            charge_only_on_solar = False,
            departure = None
        )
        self.add_car_to_list(guestCar)

        self.Car = guestCar
        self.Car.connectedCharger = self
        self.Car.kWhRemainToCharge = 10


    def add_car_to_list(self, car_instance):
        """Method to add a car to the list."""
        self.cars.append(car_instance)

    def remove_car_from_list(self, vehicle_id):
        """Method to remove a car from the list."""
        self.cars = [car for car in self.cars if car.vehicle_id != vehicle_id]

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
        charger_sensor:str, # Sensor chargecable connected
        charge_limit:str, # SOC limit sensor in %
        battery_sensor:str, # SOC in %
        asleep_sensor:str, # If car is sleeping
        online_sensor:str, # If car is online
        location_tracker:str, # Location of car
        software_update:str, # If cars updates software it probably can`t change charge speed or stop charging
        force_data_update:str, # Force Home Assistant Integration to pull new data
        polling_switch:str, # Turn off Home Assistant Integration pulling data from car
        data_last_update_time:str, # Last time Home Assistant pulled data
        battery_size:int, # Size of battery in kWh
        pref_charge_limit:int, # Preferred chargelimit in %
        priority:int, # Priority in chargecue
        finishByHour:str, # HA input_number for when car should be finished charging
        charge_now:str, # HA input_boolean to bypass smartcharging if true
        charge_only_on_solar:str, # HA input_boolean to charge only on solar
        departure:str, # HA input_datetime for when to have car finished charging to 100%. Not implemented yet
        json_path
    ):

        self.ADapi = api
        self.namespace = namespace
        self.json_path = json_path

        if not hasattr(self, 'vehicle_id'):
            self.vehicle_id = carName
        self.carName = carName

        # Sensors
        self.car_charger_sensor = charger_sensor
        self.charge_limit = charge_limit
        self.battery_sensor = battery_sensor

        self.asleep_sensor = asleep_sensor
        self.online_sensor = online_sensor
        self.location_tracker = location_tracker
        self.software_update = software_update
        self.force_data_update = force_data_update
        self.polling_switch = polling_switch
        self.data_last_update_time = data_last_update_time

        # Car variables
        self.battery_size:float = battery_size
        self.battery_reg_counter = 0
        self.pref_charge_limit:int = pref_charge_limit

        self.priority:int = priority

        # Set up when car should be finished charging
        try:
            self.finishByHour = int(finishByHour)
        except ValueError:
            self.finishByHour = math.ceil(float(self.ADapi.get_state(finishByHour,
                namespace = self.namespace))
            )
            self.ADapi.listen_state(self._finishByHourListen, finishByHour,
                namespace = self.namespace
            )

        # Switch to start charging now
        
        if not charge_now:
            self.charge_now:bool = False
        elif isinstance(charge_now, str):
            self.charge_now_HA_switch:str = charge_now
            self.charge_now = self.ADapi.get_state(charge_now, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._chargeNowListen, charge_now,
                namespace = self.namespace
            )
        elif charge_now:
            self.charge_now:bool = True

        # Switch to charge only on solar
        if not charge_only_on_solar:
            self.charge_only_on_solar:bool = False
        else:
            self.charge_only_on_solar = self.ADapi.get_state(charge_only_on_solar, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._charge_only_on_solar_Listen, charge_only_on_solar,
                namespace = self.namespace
            )

        # Helper Variables:
        self.charging_on_solar:bool = False
        self.car_limit_max_charging:int = None
        self.maxkWhCharged:float = 5 # Max kWh car has charged
        self.connectedCharger:object = None
        self.onboardCharger:object = None
        self.oldChargeLimit:int = 100

        # Check that car exists or get data from persistent json file
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read)
        
        # NEW ENTRY IN PERSISTENT: 'car'
        if not 'car' in ElectricityData:
            ElectricityData.update(
                {'car' : {}}
            )
        if not self.vehicle_id in ElectricityData['car']:
            ElectricityData['car'].update(
                {self.vehicle_id : {
                    "MaxkWhCharged" : 5
                }}
            )
            with open(self.json_path, 'w') as json_write:
                json.dump(ElectricityData, json_write, default=json_serial, indent = 4)
        else:
            if 'CarLimitAmpere' in ElectricityData['car'][self.vehicle_id]:
                try:
                    self.car_limit_max_charging = math.ceil(float(ElectricityData['car'][self.vehicle_id]['CarLimitAmpere']))
                except TypeError:
                    pass
            if 'MaxkWhCharged' in ElectricityData['car'][self.vehicle_id]:
                self.maxkWhCharged = float(ElectricityData['car'][self.vehicle_id]['MaxkWhCharged'])
            if 'batterysize' in ElectricityData['car'][self.vehicle_id]:
                self.battery_size = float(ElectricityData['car'][self.vehicle_id]['batterysize'])
                self.battery_reg_counter = int(ElectricityData['car'][self.vehicle_id]['Counter'])


        if self.charge_limit is not None:
            self.kWhRemainToCharge:float = self.kWhRemaining()
            self.ADapi.listen_state(self.ChargeLimitChanged, self.charge_limit,
                namespace = self.namespace
            )
            self.oldChargeLimit = self.ADapi.get_state(self.charge_limit,
                namespace = self.namespace
            )
        else:
            self.kWhRemainToCharge:float = -2

        # Set up listeners
        if self.car_charger_sensor is not None:
            #self.ADapi.listen_state(self.car_Car_ChargeCableConnected, self.car_charger_sensor,
            #    namespace = self.namespace,
            #    new = 'on'
            #)
            self.ADapi.listen_state(self.car_ChargeCableDisconnected, self.car_charger_sensor,
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
        if departure is not None:
            self.departure = departure

        """ Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, namespace = self.namespace, duration = 5 )
        """

        """ End initialization Car Class
        """

        # Functions on when to charge Car
    def _finishByHourListen(self, entity, attribute, old, new, kwargs) -> None:
        self.finishByHour = math.ceil(float(new))
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
        if self.find_Chargetime_Whenhome_handler is not None:
            try:
                self.ADapi.cancel_listen_state(self.find_Chargetime_Whenhome_handler)
            except Exception as exc:
                self.ADapi.log(
                    f"Could not stop hander listening for NoPower {self.find_Chargetime_Whenhome_handler}. Exception: {exc}",
                    level = 'DEBUG'
                )
            self.find_Chargetime_Whenhome_handler = None

    def findNewChargeTime(self) -> None:
        """ Find new chargetime for car. """
        if self.dontStopMeNow():
            return
        if self.hasChargingScheduled():
            return
        startcharge = False
        charger_state = self.getCarChargerState()
        if (
            self.isConnected()
            and charger_state not in ['Disconnected', 'Complete']
        ):
            if self.connectedCharger is None:
                if charger_state != 'NoPower':
                    self.connectedCharger = self.onboardCharger
            if (
                not self.charging_on_solar
                and not self.charge_only_on_solar
            ):
                if CHARGE_SCHEDULER.informHandler is not None:
                    if self.ADapi.timer_running(CHARGE_SCHEDULER.informHandler):
                        try:
                            self.ADapi.cancel_timer(CHARGE_SCHEDULER.informHandler)
                        except Exception as e:
                            self.ADapi.log(
                                f"Not possible to stop timer to run sum and inform chargetime. Exception: {e}",
                                level = 'DEBUG'
                            )
                startcharge = CHARGE_SCHEDULER.queueForCharging(
                    vehicle_id = self.vehicle_id,
                    kWhRemaining = self.kWhRemainToCharge,
                    maxAmps = self.getCarMaxAmps(),
                    voltPhase = self.connectedCharger.voltPhase,
                    finishByHour = self.finishByHour,
                    priority = self.priority,
                    name = self.carName
                )
                CHARGE_SCHEDULER.informHandler = self.ADapi.run_in(CHARGE_SCHEDULER.notifyChargeTime, 3)

                if (
                    charger_state == 'Charging'
                    and not startcharge
                ):
                    start, stop = CHARGE_SCHEDULER.getCharingTime(vehicle_id = self.vehicle_id)
                    match start:
                        case None:
                            if not CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.vehicle_id):
                                self.stopCharging()
                        case _ if start - datetime.timedelta(minutes=12) > self.ADapi.datetime(aware=True):
                            self.stopCharging()
                elif (
                    charger_state in ['NoPower', 'Stopped']
                    and startcharge
                ):
                    self.startCharging()

        elif self.getLocation() != 'home':
            self.ADapi.log(f"{self.carName} is not home when finding chargetime. If at home, find new logic calling findNewChargeTime from Easee..") ###
            self.find_Chargetime_Whenhome_handler = self.ADapi.listen_state(self._find_Chargetime_Whenhome, self.location_tracker,
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
        self.kWhRemainToCharge = -1

    def hasChargingScheduled(self) -> bool:
        """ returns if car has charging scheduled
        """
        return CHARGE_SCHEDULER.hasChargingScheduled(vehicle_id = self.vehicle_id,
                                                     kWhRemaining = self.kWhRemainToCharge,
                                                     finishByHour = self.finishByHour)

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
            if self.car_charger_sensor is not None:
                return self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace) == 'on'
            return True
        return False

    def asleep(self) -> bool:
        """ Returns True if car is sleeping.
        """
        if self.asleep_sensor and self._polling_of_data():
            return self.ADapi.get_state(self.asleep_sensor, namespace = self.namespace) == 'on'
        return False

    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        pass

    def isOnline(self) -> bool:
        """ Returns True if car in online.
        """
        if self.online_sensor:
            return self.ADapi.get_state(self.online_sensor, namespace = self.namespace) == 'on'
        return True

    def getLocation(self) -> str:
        """ Returns location of the vehicle based on sones from Home Assistant.
        """
        if self.location_tracker:
            return self.ADapi.get_state(self.location_tracker, namespace = self.namespace)
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
        if self.polling_switch:
            return self.ADapi.get_state(self.polling_switch, namespace = self.namespace) == 'on'
        return True

    def recentlyUpdated(self) -> bool:
        """ Returns True if car data is updated within the last 12 minutes.
        """
        if self.data_last_update_time:
            last_update = self.ADapi.convert_utc(self.ADapi.get_state(self.data_last_update_time,
                namespace = self.namespace)
            )
            now: datetime = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time < datetime.timedelta(minutes = 12):
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
        if self.charge_limit:
            battery_pct = self.car_battery_soc()
            limit_pct = self.ADapi.get_state(self.charge_limit, namespace = self.namespace)
            try:
                battery_pct = float(battery_pct)
                limit_pct = float(limit_pct)
            except (ValueError, TypeError) as ve:
                try:
                    kWhRemain = float(self.kWhRemainToCharge)
                except Exception:
                    kWhRemain = -1
                    self.kWhRemainToCharge = -1
                    if self.getLocation() in ['home', 'unknown']:
                        self.wakeMeUp() # Wake up car to get proper value.
                else:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.kWhRemainToCharge}. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                return self.kWhRemainToCharge
            except Exception as e:
                self.ADapi.log(
                    f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                    f"Return existing value: {self.kWhRemainToCharge}. Exception: {e}",
                    level = 'WARNING'
                )
                return self.kWhRemainToCharge

            if battery_pct < limit_pct:
                percentRemainToCharge = limit_pct - battery_pct
                self.kWhRemainToCharge = (percentRemainToCharge / 100) * self.battery_size
            else:
                self.kWhRemainToCharge = -1
        return -2

    def car_battery_soc(self) -> int:
        """ Returns battery State of charge.
        """
        SOC = -1
        if self.battery_sensor:
            try:
                SOC = float(self.ADapi.get_state(self.battery_sensor, namespace = self.namespace))
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
                kWhRemain = float(self.kWhRemainToCharge)
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
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.charge_limit,
            namespace = self.namespace
        )

    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge limit changed.
        """
        if self.connectedCharger is not None:
            try:
                new = int(new)
                self.oldChargeLimit = int(old)
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            try:
                battery_state = float(self.ADapi.get_state(self.battery_sensor,
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
        if self.car_limit_max_charging is None:
            return self.connectedCharger.getmaxChargingAmps() <= self.connectedCharger.ampereCharging
        return self.car_limit_max_charging <= self.connectedCharger.ampereCharging

    def getCarMaxAmps(self) -> int:
        if self.car_limit_max_charging is None:
            return self.connectedCharger.getmaxChargingAmps()
        return self.car_limit_max_charging

    def getCarChargerState(self) -> str:
        """ Returns the charging state of the car.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        if self.car_charger_sensor is not None:
            try:
                state = self.ADapi.get_state(self.car_charger_sensor,
                    namespace = self.namespace,
                    attribute = 'charging_state'
                )
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.charger} Could not get attribute = 'charging_state' from: "
                    f"{self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace)} "
                    f"Error: {ve}",
                    level = 'DEBUG'
                )
            else:
                if state == 'Starting':
                    state = 'Charging'
                return state
        
        if self.connectedCharger is not None:
            return self.connectedCharger.getChargingState()

    def startCharging(self) -> None:
        """ Starts controlling charger.
        """
        if (
            self.getCarChargerState() == 'Stopped'
            or self.connectedCharger.getChargingState() == 'awaiting_start'
        ):
            self.connectedCharger.startCharging()
        elif self.getCarChargerState() == 'Complete':
            self.connectedCharger._CleanUpWhenChargingStopped()

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops controlling charger.
        """
        if self.connectedCharger.getChargingState() in ['Charging', 'Starting']:
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
        charger:str, # Name of your Tesla
        charger_sensor:str, # Binary_sensor with attributes with status
        charger_switch:str, # Charging or not
        charging_amps:str, # Ampere charging
        charger_power:str, # Charger power. Contains volts and phases
        session_energy:str, # Charged this session in kWh
        json_path
    ):

        self.charger_id = api.get_state(Car.online_sensor,
            namespace = Car.namespace,
            attribute = 'id'
        )
        self.volts:int = 220
        self.phases:int = 1

        if Car.isConnected():
            try:
                self.volts = math.ceil(float(api.get_state(charger_power,
                namespace = namespace,
                attribute = 'charger_volts'))
            )
            except (ValueError, TypeError):
                self.volts = 220
            except Exception as e:
                api.log(
                    f"Error trying to get voltage: "
                    f"{api.get_state(charger_power, namespace = namespace, attribute = 'charger_volts')}. "
                    f"Exception: {e}", level = 'WARNING'
                )

            try:
                self.phases = int(api.get_state(charger_power,
                namespace = namespace,
                attribute = 'charger_phases')
            )
            except (ValueError, TypeError):
                self.phases = 1
            except Exception as e:
                api.log(f"Error trying to get phases: "
                    f"{(api.get_state(charger_power, namespace = namespace, attribute = 'charger_phases'))}. "
                    f"Exception: {e}", level = 'WARNING'
                )

        # Onboard charger can only be linked to one car.
        self.cars:list = [Car]

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_sensor = charger_sensor,
            charger_switch = charger_switch,
            charging_amps = charging_amps,
            charger_power = charger_power,
            session_energy = session_energy,
            json_path = json_path
        )

        self.Car = Car
        self.Car.onboardCharger = self

        self.min_ampere = 5
        self.noPowerDetected_handler = None

        self.ADapi.listen_state(self.ChargingStarted, self.charger_switch,
            namespace = self.namespace,
            new = 'on',
            duration = 30 ### TODO Testing only. Remove listen state if possible.
        )
        self.ADapi.listen_state(self.ChargingStopped, self.charger_switch,
            namespace = self.namespace,
            new = 'off'
        )
        self.ADapi.listen_state(self.Charger_ChargeCableConnected, self.charger_sensor,
            namespace = self.namespace
        )

        self.ADapi.listen_state(self.MaxAmpereChanged, self.charging_amps,
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
            state = self.ADapi.get_state(self.charger_sensor,
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
                f"{self.ADapi.get_state(self.charger_sensor, namespace = self.namespace)} "
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
            and self.getChargingState() not in ['Disconnected', 'Complete']
        ):
            if self.Car.connectedCharger is self:
                try:
                    maxAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps,
                        namespace = self.namespace,
                        attribute = 'max'))
                    )
                    self.ADapi.log(f"maxAmpere {maxAmpere} updated for {self.charger}. Was {self.maxChargerAmpere}") ###
                    self.maxChargerAmpere = maxAmpere

                except (ValueError, TypeError) as ve:
                    self.ADapi.log(
                        f"{self.charger} Could not get maxChargingAmps. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                    return False

            # Update Voltphase calculations
            try:
                self.volts = math.ceil(float(self.ADapi.get_state(self.charger_power,
                    namespace = self.namespace,
                    attribute = 'charger_volts'
                )))
            except (ValueError, TypeError):
                pass
            try:
                self.phases = int(self.ADapi.get_state(self.charger_power,
                    namespace = self.namespace,
                    attribute = 'charger_phases'
                ))
            except (ValueError, TypeError):
                pass
            self.setVoltPhase(
                volts = self.volts,
                phases = self.phases
            )
            return True
        return False

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        self.ampereCharging = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('tesla_custom/api',
            namespace = self.namespace,
            command = 'CHARGING_AMPS',
            parameters = {'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': self.ampereCharging}
        )

    def MaxAmpereChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Detects if smart charger (Easee) increases ampere available to charge and updates internal charger to follow.
        """
        try:
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps,
                namespace = self.namespace))
            )
            if float(new) > chargingAmpere:
                if (
                    self.Car.connectedCharger is not self
                    and self.Car.connectedCharger is not None
                ):
                    self.ADapi.log(f"Max ampere for {self.charger} is {new} while connected to {self.Car.connectedCharger.charger}. Is charging with {chargingAmpere} and not following max. Updating") ###
                    self.setChargingAmps(charging_amp_set = self.getmaxChargingAmps())

        except (ValueError, TypeError):
            pass
        else:
            if float(new) > self.maxChargerAmpere:
                self.maxChargerAmpere = new

    def Charger_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected.
        """
        if self.noPowerDetected_handler is not None:
            try:
                self.ADapi.cancel_listen_state(self.noPowerDetected_handler)
            except Exception as exc:
                self.ADapi.log(
                    f"Could not stop hander listening for NoPower {self.noPowerDetected_handler}. Exception: {exc}",
                    level = 'DEBUG'
                )
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
                    self.noPowerDetected_handler = self.ADapi.listen_state(self.noPowerDetected, self.charger_sensor,
                        namespace = self.namespace,
                        attribute = 'charging_state',
                        new = 'NoPower'
                    )

                    # Find chargetime
                    if self.ADapi.get_state(self.charger_switch, namespace = self.namespace) == 'on':
                        self.ADapi.log(f"Charger cable connected and charger switch is on for {self.charger}. TODO: Check if calculations are handled correctly.") ###
                    #    return # Calculations will be handeled by ChargingStarted
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
            self.setChargingAmps(charging_amp_set = self.min_ampere) # Set to minimum amp for preheat.

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
        else:
            self.ADapi.log(f"Car was None when trying to start charging?") ###

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops charger.
        """
        if super().stopCharging(force_stop = force_stop):
            self.ADapi.log(f"Stops onboard charger from {self.charger}") ###
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
        else:
            self.ADapi.log(f"Car was None when trying to stop charging?") ###

    def checkIfChargingStarted(self, kwargs) -> None:
        """ Check if charger was able to start.
        """
        if (
            self.getChargingState() == 'NoPower'
            and self.Car.connectedCharger is self
        ):
            self.ADapi.log(f"Charing had not started for {self.charger}. Asleep? {self.Car.asleep()}. NoPower detected. Disconnecting from self") ###
            self.Car.connectedCharger = None

        elif not super().checkIfChargingStarted(0):
            self.ADapi.create_task(self.start_Tesla_charging())

    def checkIfChargingStopped(self, kwargs) -> None:
        """ Check if charger was able to stop.
        """
        if not super().checkIfChargingStopped(0):
            self.ADapi.create_task(self.stop_Tesla_charging())

class Tesla_car(Car):

    def __init__(self, api,
        namespace,
        carName, # Unique name of charger/car
        charger_sensor, # Sensor chargecable connected
        charge_limit, # SOC limit sensor
        battery_sensor, # SOC (State Of Charge)
        asleep_sensor, # If car is sleeping
        online_sensor, # If car is online
        location_tracker, # Location of car/charger
        destination_location_tracker, # Destination of car
        arrival_time, # Sensor with Arrival time, estimated energy at arrival and destination.
        software_update, # If Tesla updates software it can`t change or stop charging
        force_data_update, # Button to force car to send update to HA
        polling_switch,
        data_last_update_time,
        battery_size:int, # User input size of battery. Used to calculate amount of time to charge
        pref_charge_limit:int, # User input if prefered SOC limit is other than 90%
        priority:int, # Priority. See full description in Readme
        finishByHour:str, # HA input_number for when car should be finished charging
        charge_now:str, # HA input_boolean to bypass smartcharging if true
        charge_only_on_solar:str, # HA input_boolean to charge only on solar
        departure:str, # HA input_datetime for when to have car finished charging to 100%. Not implemented yet
        json_path
    ):

        self.vehicle_id = api.get_state(online_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        super().__init__(
            api = api,
            namespace = namespace,
            carName = carName,
            charger_sensor = charger_sensor,
            charge_limit = charge_limit,
            battery_sensor = battery_sensor,
            asleep_sensor = asleep_sensor,
            online_sensor = online_sensor,
            location_tracker = location_tracker,
            software_update = software_update,
            force_data_update = force_data_update,
            polling_switch = polling_switch,
            data_last_update_time = data_last_update_time,
            battery_size = battery_size,
            pref_charge_limit = pref_charge_limit,
            priority = priority,
            finishByHour = finishByHour,
            charge_now = charge_now,
            charge_only_on_solar = charge_only_on_solar,
            departure = departure,
            json_path = json_path
        )

        self.arrival_time = arrival_time
        if destination_location_tracker:
           self.ADapi.listen_state(self.destination_updated, destination_location_tracker,
            namespace = namespace
        )

        """ End initialization Tesla Car Class
        """

    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        if self._polling_of_data():
            if self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace) not in ['Complete', 'Disconnected']:
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
        if (
            self.ADapi.get_state(self.software_update, namespace = self.namespace) != 'unknown'
            and self.ADapi.get_state(self.software_update, namespace = self.namespace) != 'unavailable'
        ):
            if self.ADapi.get_state(self.software_update, namespace = self.namespace, attribute = 'in_progress') != False:
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
            entity_id = self.force_data_update
        )

    def changeChargeLimit(self, chargeLimit:int = 90 ) -> None:
        """ Change charge limit.
        """
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.charge_limit,
            namespace = self.namespace
        )

    ###------------------------- Destination testing ------------------------- ###

    def destination_updated(self, entity, attribute, old, new, kwargs) -> None:
        """ Get arrival time if destination == 'home'
            and use estimated battery on arrival to calculate chargetime
        """
        if new == 'home':
            energy_at_arrival= self.ADapi.get_state(self.arrival_time,
                namespace = self.namespace,
                attribute='Energy at arrival'
            )
            if energy_at_arrival > 0:
                self.kWhRemainToCharge = self.oldChargeLimit - energy_at_arrival
                self.ADapi.log(
                    f"Arrival: {self.ADapi.convert_utc(self.ADapi.get_state(self.arrival_time, namespace = self.namespace)) + datetime.timedelta(minutes=self.ADapi.get_tz_offset())} "
                    f"Destination UTC: {self.ADapi.convert_utc(self.ADapi.get_state(self.arrival_time, namespace = self.namespace))} "
                    f"Timedelta: {self.ADapi.convert_utc(self.ADapi.get_state(self.arrival_time, namespace = self.namespace)) - self.ADapi.datetime(aware=True)} "
                    f"Energy at Arrival: {energy_at_arrival}. To charge: {self.kWhRemainToCharge}"
                ) 
                # f"Timedelta: {self.ADapi.datetime(aware=True) - self.ADapi.convert_utc(self.ADapi.get_state(self.arrival_time, namespace = self.namespace)) + datetime.timedelta(minutes=self.ADapi.get_tz_offset())} "

    ###------------------------- Destination testing ------------------------- ###

class Easee(Charger):
    """ Easee
        Child class of Charger. Uses Easee EV charger component for Home Assistant. https://github.com/nordicopen/easee_hass 
        Easiest installation is via HACS.
    """
    def __init__(self, api,
        cars:list,
        namespace:str,
        charger:str, # Name of your Easee
        charger_sensor:str, # sensor.charger_status
        reason_for_no_current:str, # No charger_switch in Easee integration
        charging_amps:str, # Sensor with current
        charger_power:str, # Charger power in kW
        session_energy:str, # Charged this session. In kWh
        voltage:str, # Voltage sensor
        max_charger_limit:str, # Max available ampere in charger
        idle_current:str, # Allow Idle current for preheating
        guest:str, # HA input_boolean for when a guest car borrows charger.
        json_path
    ):

        self.charger_id:str = api.get_state(charger_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        self.voltage = voltage
        try:
            self.volts = math.ceil(float(api.get_state(voltage,
                namespace = namespace))
            )
        except ValueError:
            self.volts = 220
        except Exception as e:
            api.log(f"Error trying to get voltage for {charger} from {voltage}. Exception: {e}",
            level = 'WARNING'
        )

        try:
            self.phases = int(api.get_state(charger_sensor,
            namespace = namespace,
            attribute = 'config_phaseMode')
        )
        except (ValueError, TypeError):
            self.phases = 1
        except Exception as e:
            api.log(f"Error trying to get phases for {charger} from {charger_sensor}. Exception: {e}",
                level = 'WARNING'
            )

        self.max_charger_limit = max_charger_limit

        self.cars:list = cars

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_sensor = charger_sensor,
            charger_switch = None,
            charging_amps = charging_amps,
            charger_power = charger_power,
            session_energy = session_energy,
            json_path = json_path
        )

        # Minumum ampere if locked to 3 phase
        if self.phases == 3:
            self.min_ampere = 11

        # Switch to allow guest to charge
        if not guest:
            self.guestCharging = False
        else:
            self.guestCharging = api.get_state(guest, namespace = namespace) == 'on'
            api.listen_state(self.guestChargingListen, guest,
                namespace = namespace
            )

        # Switch to allow current when preheating
        if not idle_current:
            self.idle_current = False
        else:
            self.idle_current = api.get_state(idle_current, namespace = namespace) == 'on'
            api.listen_state(self.idle_currentListen, idle_current,
                namespace = namespace
            )

        api.listen_state(self.statusChange, charger_sensor, namespace = namespace)

        if (
            api.get_state(self.charger_sensor, namespace = self.namespace) != 'disconnected'
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
        charger_status = self.ADapi.get_state(self.charger_sensor, namespace = self.namespace)
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
        status = self.ADapi.get_state(self.charger_sensor, namespace = self.namespace)
        if status == 'charging':
            return 'Charging'
        elif status == 'completed':
            return 'Complete'
        elif status == 'awaiting_start':
            return 'awaiting_start'
        elif status == 'disconnected':
            return 'Disconnected'
        elif not status == 'ready_to_charge':
            self.ADapi.log(f"Status: {status} for {self.charger} is not defined", level = 'WARNING')
        return status

    def statusChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to changes in state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
        """
        self.setPhases()

        if old == 'disconnected':
            if self.Car is None:
                self.ADapi.log(f"{self.charger} was disconnected. Car is None") ###
                if self.findCarConnectedToCharger():
                    if self.Car is not None:
                        self.ADapi.log(f"{self.Car.carName} connected to {self.charger} in StatusChange. New status: {new}") ###
                        self.kWhRemaining() # Update kWh remaining to charge
                        self.Car.findNewChargeTime()
                        return
                    else:
                        self.ADapi.log(f"No car connected when cable conneted to {self.charger} in StatusChange. New status: {new}") ###
            else:
                self.ADapi.log(f"{self.charger} was disconnected. Car: is {self.Car.carName}") ###
            return

        elif (
            new != 'disconnected'
            and old == 'completed'
        ):
            if self.Car is not None:
                if (
                    self.kWhRemaining() > 2
                    and not self.Car.hasChargingScheduled()
                ):
                    self.Car.findNewChargeTime()
                    self.ADapi.log(
                        f"Tried to find new chargetime when car was complete and new is charging for {self.Car.carName}. "
                        f"Chargetime:{CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id)}"
                    ) ###

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
                    not self.Car.hasChargingScheduled()
                ):
                    self.kWhRemaining() # Update kWh remaining to charge
                    self.Car.findNewChargeTime()

                elif not CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id):
                    self.stopCharging()

        elif new == 'completed':
            if self.Car is not None:
                self._CleanUpWhenChargingStopped()

        elif new == 'disconnected':
            if self.Car is None: ###
                self.ADapi.log(f"{self.charger} disconnected with no car connected. Should not see this unless Guest charging") ###
            self.ADapi.run_in(self._check_if_still_disconnected, 720)

        elif new == 'awaiting_start':
            self._CleanUpWhenChargingStopped()
            if self.Car is None:
                self.findCarConnectedToCharger()

    def _check_if_still_disconnected(self, kwargs) -> None:
        if self.ADapi.get_state(self.charger_sensor, namespace = self.namespace) == 'disconnected':
            if self.Car is not None:
                self._CleanUpWhenChargingStopped()
                self.Car.connectedCharger = None
                self.Car = None
            else: ###
                self.ADapi.log(f"{self.charger} still disconnected with no car connected. Should not see this") ###
        elif self.Car is not None: # Check if new car is connected.
            if self.Car.getCarChargerState() == 'Disconnected':
                self._CleanUpWhenChargingStopped()
                self.Car.connectedCharger = None
                self.Car = None
                self.findCarConnectedToCharger()
            else:
                self.ADapi.log(f"{self.charger} was not disconnected in check. Found solution!") ###


    def reasonChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to reasonChange in Easee charger.
            Easee reason can be:
            'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' /
            'limited_by_local_adjustment' / 'limited_by_car' / 'car_not_charging' /  from reason_for_no_current
        """
        if (
            new == 'limited_by_car'
        ):
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps,
                namespace = self.namespace))
            )
            if (
                self.Car.car_limit_max_charging != chargingAmpere
                and chargingAmpere >= 6
            ):
                self.Car.car_limit_max_charging = chargingAmpere
                self.ADapi.log(f"Updated {self.Car.carName} limit max ampere charging to {chargingAmpere} in Easee charger") ###

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors
        """
        try:
            self.maxChargerAmpere = math.ceil(float(self.ADapi.get_state(self.max_charger_limit,
                namespace = self.namespace))
            )
        except (ValueError, TypeError):
            return False
        self.setVolts()
        self.setPhases()

        self.setVoltPhase(
            volts = self.volts,
            phases = self.phases
        )
        return True

    def setVolts(self):
        try:
            self.volts = math.ceil(float(self.ADapi.get_state(self.voltage,
                namespace = self.namespace))
            )
        except (ValueError, TypeError):
            return

    def setPhases(self):
        try:
            self.phases = int(self.ADapi.get_state(self.charger_sensor,
            namespace = self.namespace,
            attribute = 'config_phaseMode')
        )
        except (ValueError, TypeError):
            self.phases = 1

    def setChargingAmps(self, charging_amp_set:int = 16) -> None:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        if (
            self.ampereCharging != charging_amp_set
            and self.ampereCharging != charging_amp_set -1
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
                carName = getattr(self.Car, 'carName', 'car') ###
                self.ADapi.log(f"Stops charging {carName} from {self.charger}") ###
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
        heater,
        consumptionSensor,
        validConsumptionSensor:bool,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        pricedifference_increase:float,
        namespace,
        away,
        automate,
        recipient,
        json_path
    ):
        self.ADapi = api
        self.namespace = namespace
        self.json_path = json_path

        self.heater = heater # on_off_switch boiler or heater switch

            # Vacation setup
        if away is not None and self.ADapi.entity_exists(away, namespace = self.namespace):
            self.away_state = self.ADapi.get_state(away, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._awayStateListen_Heater, away,
                namespace = self.namespace
            )
        else:
            self.away_state = False

            # Automate setup
        if automate is None:
            self.automate = True
        else:
            self.automate = self.ADapi.get_state(automate, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self.automateStateListen, automate,
                namespace = self.namespace
            )

            # Notification setup
        if recipient:
            self.recipients = recipient
        else:
            self.recipients = RECIPIENTS

            # Consumption sensors and setups
        self.consumptionSensor = consumptionSensor
        self.validConsumptionSensor:bool = validConsumptionSensor
        self.kWhconsumptionSensor = kWhconsumptionSensor
        self.prev_consumption:int = 0
        self.max_continuous_hours:int = max_continuous_hours
        self.reset_continuous_hours:bool = False
        self.on_for_minimum:int = on_for_minimum
        self.pricedrop:float = pricedrop
        self.pricedifference_increase:float = pricedifference_increase

            # Consumption data
        self.time_to_save:list = []
        self.time_to_spend:list = []
        self.kWh_consumption_when_turned_on:float = 0.0
        self.isOverconsumption:bool = False
        self.increase_now:bool = False
        self.normal_power:int = 0
        self.registerConsumption_handler = None
        self.checkConsumption_handler = None

        self.HeatAt = None
        self.EndAt = None
        self.price:float = 0

            # Persistent storage for consumption logging
        with open(self.json_path, 'r') as json_read:
            ElectricityData = json.load(json_read, object_hook=json_deserialize)
        if not self.heater in ElectricityData['consumption']:
            ElectricityData['consumption'].update(
                {self.heater : {"ConsumptionData" : {}}}
            )
            with open(self.json_path, 'w') as json_write:
                json.dump(ElectricityData, json_write, default=json_serial, indent = 4)
        else:
            consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData']
            try:
                self.normal_power = float(self.ADapi.get_state(self.consumptionSensor, namespace = self.namespace))
            except Exception:
                self.normal_power = 0

            if self.normal_power > 100:
                if not "power" in ElectricityData['consumption'][self.heater]:
                    ElectricityData['consumption'][self.heater].update(
                        {"power" : self.normal_power}
                    )
                    with open(self.json_path, 'w') as json_write:
                        json.dump(ElectricityData, json_write, default=json_serial, indent = 4)
            elif "power" in ElectricityData['consumption'][self.heater]:
                self.normal_power = ElectricityData['consumption'][self.heater]['power']
            if 'peak_hours' in ElectricityData['consumption'][self.heater]:
                for item in ElectricityData['consumption'][self.heater]['peak_hours']:
                    if isinstance(item, dict): # Check if database is converted to new version.
                        self.time_to_save = ElectricityData['consumption'][self.heater]['peak_hours']

            # Get prices to set up automation times
        self.ADapi.run_in(self.heater_getNewPrices, 60)

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
        self.time_to_save = ELECTRICITYPRICE.find_times_to_save(
            pricedrop = self.pricedrop,
            max_continuous_hours = self.max_continuous_hours,
            on_for_minimum = self.on_for_minimum,
            pricedifference_increase = self.pricedifference_increase,
            reset_continuous_hours = self.reset_continuous_hours,
            previous_save_hours = self.time_to_save
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
                if self.HeatAt > self.ADapi.datetime(aware=True):
                    self.ADapi.run_at(self.heater_setNewValues, self.HeatAt)
            if self.EndAt is not None:
                if self.EndAt > self.ADapi.datetime(aware=True):
                    self.ADapi.run_at(self.heater_setNewValues, self.EndAt)

        elif not self.away_state:
            self.HeatAt = None
            self.EndAt = None
        
        # TODO: Set up runs for on and off
        if self.time_to_save:
            for item in self.time_to_save:
                if item['end'] > self.ADapi.datetime(aware=True):
                    self.ADapi.run_at(self.heater_setNewValues, item['end'])
                    if item['start'] > self.ADapi.datetime(aware=True):
                        self.ADapi.run_at(self.heater_setNewValues, item['start'])

        self.ADapi.run_in(self.heater_setNewValues, 5)

        """Logging purposes to check what hours heater turns off/down to check if behaving as expected"""
        #if self.time_to_save:
        #    self.ADapi.log(f"{self.heater} save hours:{ELECTRICITYPRICE.print_peaks(self.time_to_save)}")

    def heater_setNewValues(self, kwargs) -> None:
        """ Turns heater on or off based on this hours electricity price.
        """
        isOn:bool = self.ADapi.get_state(self.heater, namespace = self.namespace) == 'on'

        if (
            self.isOverconsumption
            and isOn
        ):
            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )
            return
        if self.increase_now:
            if not isOn:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
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
            return
        elif not isOn:
            if (
                self.HeatAt is not None
                and self.away_state
            ):
                current_time = self.ADapi.datetime(aware=True)
                if (
                    (start := self.HeatAt) <= current_time < (end := self.EndAt)
                    or ELECTRICITYPRICE.electricity_price_now() <= self.price + (self.pricedrop/2)
                ):
                    self.ADapi.call_service('switch/turn_on',
                        entity_id = self.heater,
                        namespace = self.namespace
                    )
                return
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
                return
        elif(
            isOn
            and self.HeatAt is not None
            and self.away_state
        ):
            current_time = self.ADapi.datetime(aware=True)
            if (
                (start := self.HeatAt) <= current_time < (end := self.EndAt)
                or ELECTRICITYPRICE.electricity_price_now() <= self.price + (self.pricedrop/2)
            ):
                return
            if float(self.ADapi.get_state(self.consumptionSensor, namespace = self.namespace)) > 20:
                self.ADapi.listen_state(self.turnOffHeaterAfterConsumption, self.consumptionSensor,
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

    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        self.ADapi.run_in(self.heater_setNewValues, 5)

    def setIncreaseState(self) -> None:
        """ Set heater to increase temperature when electricity production is higher that consumption.
        """
        self.increase_now = True
        self.ADapi.run_in(self.heater_setNewValues, 1)

        # Functions to calculate and log consumption to persistent storage
    def findConsumptionAfterTurnedOn(self, **kwargs) -> None:
        """ Starts to listen for how much heater consumes after it has been in save mode.
        """
        hoursOffInt = kwargs['hoursOffInt']
        try:
            self.kWh_consumption_when_turned_on = float(self.ADapi.get_state(self.kWhconsumptionSensor, namespace = self.namespace))
        except ValueError:
            self.ADapi.log(
                f"{self.kWhconsumptionSensor} unavailable in finding consumption to register after heater is turned back on",
                level = 'DEBUG'
            )
        else:
            if (
                self.ADapi.get_state(self.heater, namespace = self.namespace) != 'off'
                and not self.away_state
                and self.automate
            ):
                self.registerConsumption_handler = self.ADapi.listen_state(self.registerConsumption, self.consumptionSensor,
                    namespace = self.namespace,
                    constrain_state=lambda x: float(x) < 20,
                    hoursOffInt = hoursOffInt
                )
                self._cancel_timer_handler(self.checkConsumption_handler)
                self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 1200)

    def checkIfConsumption(self, kwargs) -> None:
        """ Checks if there is consumption after 'findConsumptionAfterTurnedOn' starts listening.
            If there is no consumption it will cancel the timer.
        """
        self.ADapi.log(f"Check if Consumption for {self.heater}. Overconsumption? {self.isOverconsumption}") ###
        if self.isOverconsumption:
            self._cancel_timer_handler(self.checkConsumption_handler)
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600)
            return
        wattconsumption:float = 0
        try:
            wattconsumption = float(self.ADapi.get_state(self.consumptionSensor, namespace = self.namespace))
        except ValueError:
            self.ADapi.log(
                f"{self.consumptionSensor} unavailable in finding consumption.",
                level = 'DEBUG'
            )
        if wattconsumption < 20:
            self._cancel_listen_handler(self.registerConsumption_handler)
            self.registerConsumption_handler = None

    def registerConsumption(self, entity, attribute, old, new, **kwargs) -> None:
        """ Registers consumption to persistent storage after heater has been off.
        """
        self.ADapi.log(f"Started to register Consumption for {self.heater}. Overconsumption? {self.isOverconsumption}") ###
        offForHours = str(kwargs['hoursOffInt'])
        if self.isOverconsumption:
            self._cancel_timer_handler(self.checkConsumption_handler)
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600)
            return

        consumption:float = 0
        try:
            consumption = float(self.ADapi.get_state(self.kWhconsumptionSensor, namespace = self.namespace)) - self.kWh_consumption_when_turned_on
        except (TypeError, AttributeError) as ve:
            self.ADapi.log(
                f"Could not get consumption for {self.heater} to register data. {consumption} Error: {ve}",
                level = 'DEBUG'
            )
            return
        if consumption == 0:
            consumption = 0.01 # Avoid multiplications by 0.
        if consumption > 0:
            self._cancel_timer_handler(self.checkConsumption_handler)
            self._cancel_listen_handler(self.registerConsumption_handler)
            self.registerConsumption_handler = None
            if self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off':
                return

            try:
                with open(self.json_path, 'r') as json_read:
                    ElectricityData = json.load(json_read)

                consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData']
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)

                if not "power" in ElectricityData['consumption'][self.heater]:
                    ElectricityData['consumption'][self.heater].update(
                        {"power" : float(old)}
                    )

                if not offForHours in ElectricityData['consumption'][self.heater]['ConsumptionData']:
                    newData = {"Consumption" : consumption, "Counter" : 1}
                    ElectricityData['consumption'][self.heater]['ConsumptionData'].update(
                        {offForHours : {out_temp_str : newData}}
                    )

                elif not out_temp_str in ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours]:
                    newData = {"Consumption" : round(consumption,2), "Counter" : 1}
                    ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours].update(
                        {out_temp_str : newData}
                    )

                else:
                    consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours][out_temp_str]
                    counter = consumptionData['Counter'] + 1

                    avgConsumption = round(((consumptionData['Consumption'] * consumptionData['Counter']) + consumption) / counter,2)
                    if counter > 100:
                        counter = 10
                    newData = {"Consumption" : avgConsumption, "Counter" : counter}
                    ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours].update(
                        {out_temp_str : newData}
                    )

                self.ADapi.log(f"Updating {self.heater}. With new data. Consumption: {consumption}") ###
                with open(self.json_path, 'w') as json_write:
                    json.dump(ElectricityData, json_write, default=json_serial, indent = 4)

            except Exception as e:
                self.ADapi.log(
                    f"Not able to register consumption for {self.heater}. Exception: {e}",
                    level = 'DEBUG'
                )

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
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

    def _is_time_within_any_save_range(self):
        current_time = self.ADapi.datetime(aware=True)
        for range_item in self.time_to_save:
            if (start := range_item['start']) <= current_time < (end := range_item['end']):
                return True
        return False

    def _is_time_within_any_spend_range(self):
        current_time = self.ADapi.datetime(aware=True)
        for range_item in self.time_to_spend:
            if (start := range_item['start']) <= current_time < (end := range_item['end']):
                return True
        return False

    def _cancel_timer_handler(self, handler) -> None:
        if handler is not None:
            if self.ADapi.timer_running(handler):
                try:
                    self.ADapi.cancel_timer(handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to stop timer handler for {self.heater}. Exception: {e}",
                        level = 'DEBUG'
                    )

    def _cancel_listen_handler(self, handler) -> None:
        if handler is not None:
            try:
                self.ADapi.cancel_listen_state(handler)
            except Exception as e:
                self.ADapi.log(
                    f"Not able to stop listen handler for {self.heater}. Exception: {e}",
                    level = 'DEBUG'
                )

class Climate(Heater):
    """ Child class of Heater
        For controlling electrical heaters to heat off peak hours.
    """
    def __init__(self,
        api,
        heater,
        consumptionSensor,
        validConsumptionSensor:bool,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        pricedifference_increase:float,
        namespace:str,
        away,
        automate,
        recipient,
        indoor_sensor_temp,
        window_temp,
        window_offset:float,
        target_indoor_input,
        target_indoor_temp:float,
        save_temp_offset:float,
        save_temp:float,
        away_temp:float,
        rain_level:float,
        anemometer_speed:int,
        low_price_max_continuous_hours:int,
        priceincrease:float,
        windowsensors:list,
        getting_cold:int,
        daytime_savings:list,
        temperatures:list,
        json_path
    ):
            # Sensors
        self.indoor_sensor_temp = indoor_sensor_temp
        if target_indoor_input is not None:
            api.listen_state(self.updateTarget, target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = target_indoor_temp
        self.save_temp_offset = save_temp_offset
        self.save_temp = save_temp
        self.away_temp = away_temp
        self.window_temp = window_temp
        self.window_offset:float = window_offset
        self.rain_level:float = rain_level
        self.anemometer_speed:int = anemometer_speed
        self.low_price_max_continuous_hours:int = low_price_max_continuous_hours
        self.priceincrease:float = priceincrease
        self.windowsensors:list = windowsensors
        self.daytime_savings:list = daytime_savings
        self.temperatures:list = temperatures

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            validConsumptionSensor = validConsumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            pricedrop = pricedrop,
            pricedifference_increase = pricedifference_increase,
            namespace = namespace,
            away = away,
            automate = automate,
            recipient = recipient,
            json_path = json_path
        )
        self.reset_continuous_hours = True
        self.getting_cold:float = getting_cold

        self.windows_is_open:bool = False
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                self.windows_is_open = True

        for windows in self.windowsensors:
            self.ADapi.listen_state(self.windowOpened, windows,
                new = 'on',
                duration = 120,
                namespace = self.namespace
            )
            self.ADapi.listen_state(self.windowClosed, windows,
                new = 'off',
                namespace = self.namespace
            )
        self.notify_on_window_open:bool = True
        self.notify_on_window_closed:bool = False

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

        runtime = get_next_runtime(offset_seconds=10, delta_in_seconds=60*15)
        self.ADapi.run_every(self.heater_setNewValues, runtime, 60*15)

        # Get new prices to save and in addition to turn up heat for heaters before expensive hours
    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on ELECTRICITYPRICE.find_times_to_spend()
        """
        super().heater_getNewPrices(0)
        self.time_to_spend = ELECTRICITYPRICE.find_times_to_spend(
            priceincrease = self.priceincrease
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
        for target_num, target_temp in enumerate(self.temperatures):
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
            target_temp = self.temperatures[target_num]
            if self.save_temp is not None:
                save_temp = self.save_temp + target_temp['offset']
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

        target_num = self.find_target_temperatures()
        target_temp = self.temperatures[target_num]

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
        if self.indoor_sensor_temp is not None:
            try:
                in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(
                    f"Not able to get new inside temperature from {self.indoor_sensor_temp}. Error: {e}",
                    level = 'DEBUG'
                )
        if in_temp == -50:
            try:
                in_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='current_temperature'))
                self.ADapi.log(
                    f"{self.heater} Not able to get new inside temperature from {self.indoor_sensor_temp}. "
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

        if self.away_temp is not None:
            away_temp = self.away_temp + target_temp['offset']
        elif 'away' in target_temp:
            away_temp = target_temp['away']
        else:
            away_temp = 5

        # Adjust temperature based on weather
        if RAIN_AMOUNT >= self.rain_level:
            new_temperature += 1
        elif WIND_AMOUNT >= self.anemometer_speed:
            new_temperature += 1
        
        adjust = 0
        if self.window_temp is not None:
            try:
                window_temp = float(self.ADapi.get_state(self.window_temp, namespace = self.namespace))
            except (TypeError, AttributeError):
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"{self.window_temp} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"Not able to get temperature from {self.window_temp}. {e}", level = 'DEBUG')
            if window_temp > self.target_indoor_temp + self.window_offset:
                adjust = math.floor(float(window_temp - (self.target_indoor_temp + self.window_offset)))

        if in_temp > self.target_indoor_temp:
            adjust += math.floor(float(in_temp - self.target_indoor_temp))
        
        new_temperature -= adjust

        if new_temperature < away_temp:
            new_temperature = away_temp

        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and in_temp >= self.target_indoor_temp + 10
            and OUT_TEMP > self.getting_cold
        ):
            NOTIFY_APP.send_notification(
                message = f"No Window near {self.heater} is open and it is getting hot inside! {in_temp}°",
                message_title = f"Window closed",
                message_recipient = RECIPIENTS,
                also_if_not_home = False
            )
            self.notify_on_window_closed = False
        
        if self.windows_is_open:
            new_temperature = away_temp
            if (
                self.notify_on_window_open
                and OUT_TEMP < self.getting_cold
                and in_temp < self.getting_cold
            ):
                NOTIFY_APP.send_notification(
                    message = f"Window near {self.heater} is open and inside temperature is {in_temp}°",
                    message_title = "Window open",
                    message_recipient = RECIPIENTS,
                    also_if_not_home = False
                )
                self.notify_on_window_open = False

        # Holliday temperature
        elif self.away_state:
            new_temperature = away_temp

        # Peak and savings temperature
        if (
            self._is_time_within_any_save_range()
            and self.automate
        ):
            new_temperature = self.getSaveTemp(new_temperature, target_temp)
        
        # Daytime Savings
        else:
            doDaytimeSaving = False
            for daytime in self.daytime_savings:
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
        if self.save_temp_offset is not None:
            new_temperature += self.save_temp_offset
        elif self.save_temp is not None:
            if new_temperature > self.save_temp + target_temp['offset']:
                new_temperature = self.save_temp + target_temp['offset']
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
        consumptionSensor,
        validConsumptionSensor:bool,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        pricedifference_increase:float,
        namespace,
        away,
        automate,
        recipient,
        json_path
    ):
        self.daytime_savings:list = []

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            validConsumptionSensor = validConsumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            pricedrop = pricedrop,
            pricedifference_increase = pricedifference_increase,
            namespace = namespace,
            away = away,
            automate = automate,
            recipient = recipient,
            json_path = json_path
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

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, datetime.timedelta):
        return str(obj.total_seconds())
    raise TypeError(f"Type {type(obj)} not serializable")


def json_deserialize(dct):
    """JSON deserializer for objects serialized with json_serial"""
    try:
        for key, value in dct.items():
            if isinstance(value, str):
                try:
                    dct[key] = datetime.datetime.fromisoformat(value)
                except ValueError:
                    if key == 'duration':
                        try:
                            seconds = float(value)
                            seconds_int = int(seconds)
                            dct[key] = datetime.timedelta(seconds=seconds)
                        except (ValueError, OverflowError) as e:
                            pass
            elif isinstance(value, dict):
                dct[key] = json_deserialize(value)
        return dct
    except Exception as e:
        return dct

def get_next_runtime(offset_seconds=10, delta_in_seconds=60*15):
    now = datetime.datetime.now()
    next_minute_mark = ((now.minute * 60 + now.second) // delta_in_seconds + 1) * delta_in_seconds
    next_runtime = now.replace(minute=0, second=offset_seconds % 60, microsecond=0)
    next_runtime += datetime.timedelta(seconds=next_minute_mark)

    return next_runtime

def find_closest_time_in_data(off_for_int:int = 0, data = []) -> int:
    time_diff:int = 24
    closest_time:int = None
    for time in data:
        time_int = int(time)
        if off_for_int > time_int: 
            if time_diff < off_for_int - time_int:
                continue
            time_diff = off_for_int - time_int
            closest_time = time
        else:
            if time_diff < time_int - off_for_int:
                continue
            time_diff = time_int - off_for_int
            closest_time = time
    return closest_time

def find_closest_temp_in_data(data = []) -> int:
    temp_diff:int = 100
    closest_temp:int = None
    for temps in data:
        if OUT_TEMP > float(temps):
            if temp_diff < OUT_TEMP - float(temps):
                continue
            temp_diff = OUT_TEMP - float(temps)
            closest_temp = temps
        else:
            if temp_diff < float(temps) - OUT_TEMP:
                continue
            temp_diff = float(temps) - OUT_TEMP
            closest_temp = temps
    return closest_temp